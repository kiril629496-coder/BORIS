"""
BORIS AI — ASK -> ANSWER -> FACT BRIDGE

V25:
    DRY RUN ONLY.

Задача:
    определить, может ли входящий ответ клиента быть
    однозначно привязан к APPROVED ask.client confirmation.

В ЭТОЙ ВЕРСИИ:
    - НЕТ DB WRITE
    - НЕТ fact_intake.submit()
    - НЕТ confirmation update
    - НЕТ executor
    - НЕТ client message
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy import text


APPROVED_STATUSES = {
    "approved",
    "APPROVE",
    "APPROVED",
}


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _lower(value: Any) -> str:
    return _norm(value).lower()


def _confirmation_is_ask_client(row: Dict[str, Any]) -> bool:
    """
    Строго проверяем, что confirmation относится именно
    к запросу клиенту.

    Никаких догадок по обычному тексту.
    """

    candidates = []

    for key in (
        "kind",
        "type",
        "action",
        "operation",
        "operation_kind",
        "executor",
        "command",
    ):
        if key in row and row[key] is not None:
            candidates.append(_lower(row[key]))

    blob = " ".join(candidates)

    return (
        "ask.client" in blob
        or "ask_client" in blob
        or "ask client" in blob
    )


def _extract_field(row: Dict[str, Any]) -> Optional[str]:
    """
    Ищем только явно сохранённое поле.

    Ничего не угадываем.
    """

    for key in (
        "target_field",
        "field",
        "fact_kind",
        "fact_field",
        "requested_field",
    ):
        value = _norm(row.get(key))
        if value:
            return value

    return None


def _extract_question(row: Dict[str, Any]) -> str:
    for key in (
        "question",
        "human_question",
        "client_question",
        "prompt",
        "message",
        "description",
        "title",
    ):
        value = _norm(row.get(key))
        if value:
            return value

    return ""


def find_active_ask(
    db,
    account_id: str,
) -> Dict[str, Any]:
    """
    READ ONLY.

    Возвращает только однозначно найденный ASK.

    Если найдено:
        один подходящий ASK -> MATCH

    Если:
        0 -> NO_MATCH
        >1 -> AMBIGUOUS
    """

    rows = db.execute(
        text(
            """
            SELECT *
            FROM factory_confirmations
            WHERE account_id=:account_id
            ORDER BY id DESC
            LIMIT 100
            """
        ),
        {"account_id": account_id},
    ).mappings().all()

    candidates = []

    for row in rows:
        data = dict(row)

        status = _lower(data.get("status"))

        if status not in {x.lower() for x in APPROVED_STATUSES}:
            continue

        if not _confirmation_is_ask_client(data):
            continue

        candidates.append(data)

    if not candidates:
        return {
            "status": "NO_MATCH",
            "account_id": account_id,
            "candidate_count": 0,
        }

    if len(candidates) > 1:
        return {
            "status": "AMBIGUOUS",
            "account_id": account_id,
            "candidate_count": len(candidates),
            "candidates": [
                {
                    "id": c.get("id"),
                    "field": _extract_field(c),
                    "question": _extract_question(c),
                }
                for c in candidates
            ],
        }

    row = candidates[0]

    return {
        "status": "MATCH",
        "account_id": account_id,
        "confirmation_id": row.get("id"),
        "field": _extract_field(row),
        "question": _extract_question(row),
        "raw_keys": sorted(row.keys()),
    }


def submit_answer(
    db,
    *,
    account_id: str,
    message_id: str,
    incoming_text: str,
):
    """
    REAL V32 FACT INTAKE.

    Gate:
      1. non-empty client answer
      2. exactly one approved ask.client confirmation
      3. explicit fact field
      4. fact_intake.validate()
      5. fact_intake.submit()

    Никакой confirmation mutation здесь нет.
    Никакого executor здесь нет.
    Никакой отправки сообщения здесь нет.

    ВАЖНО:
      DB transaction принадлежит вызывающему production path.
      Этот bridge не делает commit самостоятельно.
    """

    answer = _norm(incoming_text)

    if not answer:
        return {
            "status": "EMPTY_ANSWER",
            "account_id": account_id,
            "message_id": message_id,
        }

    ask = find_active_ask(db, account_id)

    if ask.get("status") != "MATCH":
        return {
            "status": ask.get("status"),
            "account_id": account_id,
            "message_id": message_id,
            "ask": ask,
        }

    field = ask.get("field")

    if not field:
        return {
            "status": "ASK_FIELD_MISSING",
            "account_id": account_id,
            "message_id": message_id,
            "confirmation_id": ask.get("confirmation_id"),
        }

    from app.factory import fact_intake as J

    source_ref = f"avito_message:{message_id}"

    try:
        validated = J.validate(
            account_id=account_id,
            fact_kind=field,
            value=answer,
            source_type="client",
            source_ref=source_ref,
            actor="client",
            confidence=1.0,
        )
    except ValueError as exc:
        # fact_intake intentionally owns the supported fact_kind contract.
        # Bridge must fail closed instead of crashing the incoming path.
        message = str(exc)

        if "unsupported fact_kind" in message.lower():
            return {
                "status": "FACT_KIND_UNSUPPORTED",
                "account_id": account_id,
                "message_id": message_id,
                "confirmation_id": ask.get("confirmation_id"),
                "fact_kind": field,
                "answer_preview": answer[:200],
            }

        raise

    if validated is False:
        return {
            "status": "FACT_VALIDATION_FAILED",
            "account_id": account_id,
            "message_id": message_id,
            "confirmation_id": ask.get("confirmation_id"),
            "fact_kind": field,
        }

    result = J.submit(
        db,
        account_id=account_id,
        fact_kind=field,
        value=answer,
        source_type="client",
        source_ref=source_ref,
        actor="client",
        confidence=1.0,
    )

    return {
        "status": "FACT_INTAKE_SUBMITTED",
        "account_id": account_id,
        "message_id": message_id,
        "confirmation_id": ask.get("confirmation_id"),
        "fact_kind": field,
        "value": answer,
        "source_type": "client",
        "source_ref": source_ref,
        "actor": "client",
        "confidence": 1.0,
        "result": result,
    }



def dry_run_answer(
    db,
    *,
    account_id: str,
    message_id: str,
    incoming_text: str,
) -> Dict[str, Any]:
    """
    Полностью READ ONLY.

    Никакого фактического intake.
    """

    answer = _norm(incoming_text)

    if not answer:
        return {
            "status": "EMPTY_ANSWER",
            "account_id": account_id,
            "message_id": message_id,
        }

    ask = find_active_ask(db, account_id)

    if ask["status"] != "MATCH":
        return {
            "status": ask["status"],
            "account_id": account_id,
            "message_id": message_id,
            "answer_preview": answer[:200],
            "ask": ask,
        }

    field = ask.get("field")

    if not field:
        return {
            "status": "ASK_FIELD_MISSING",
            "account_id": account_id,
            "message_id": message_id,
            "confirmation_id": ask["confirmation_id"],
            "answer_preview": answer[:200],
        }

    return {
        "status": "READY_FOR_FACT_INTAKE",
        "account_id": account_id,
        "message_id": message_id,
        "confirmation_id": ask["confirmation_id"],
        "fact_kind": field,
        "value": answer,
        "source_type": "client",
        "source_ref": f"avito_message:{message_id}",
        "actor": "client",
        "confidence": 1.0,
        "question": ask.get("question", ""),
    }
