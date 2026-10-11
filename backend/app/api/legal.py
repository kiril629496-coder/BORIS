"""Правила использования продуктов + фиксация согласия в журнале."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from datetime import datetime
import json
from app.api.auth import get_current_user

router = APIRouter(prefix="/api/legal", tags=["legal"])

TERMS_VERSION = "2026-07-24"

TERMS = {
    "rop": {
        "version": TERMS_VERSION,
        "title": "Правила использования «ИИ Руководитель отдела продаж»",
        "html": """
<h3>Что делает сервис</h3>
<p>РОП анализирует работу отдела продаж: расшифровывает и разбирает записи звонков через коллтрекинг Avito, разбирает переписки с покупателями, формирует PDF-отчёты с рекомендациями. РОП — аналитический инструмент, он не звонит, не пишет покупателям и не принимает решений за вас.</p>
<h3>Базовый пакет — 20 000 ₽, 30 дней</h3>
<p>1 500 минут разбора звонков · 450 разборов переписок · 30 отчётов по звонкам · 30 отчётов по перепискам.</p>
<h3>Как расходуется</h3>
<p>Минуты — по фактической длительности звонка. Переписки и отчёты — по одной единице за штуку. Повторный разбор того же звонка или диалога бесплатен.</p>
<h3>Перерасход</h3>
<p>Если переписки или отчёты закончились раньше минут, они списываются из остатка минут: одна переписка или один отчёт = одна минута. При исчерпании минут разбор звонков останавливается.</p>
<h3>Ограничения</h3>
<p>Не более 15 новых диалогов в сутки на аккаунт. Разбираются диалоги от 4 до 40 сообщений.</p>
<h3>Срок и сгорание</h3>
<p>Пакет действует 30 дней. Неиспользованный остаток сгорает, кроме случая автопродления с оплаченного кошелька — тогда остаток (до объёма одного пакета) переносится в новый период.</p>
<h3>Докупки</h3>
<p>Пакеты дополнительного объёма доступны при действующем базовом пакете, не продлевают срок и расходуются до конца текущего периода.</p>
<h3>Требования к аккаунту</h3>
<p>Нужны ключи Avito API, тариф «Расширенный» или «Максимальный» и включённый коллтрекинг. Без них сервис не работает; это не является основанием для возврата.</p>
<h3>Отчёты</h3>
<p>Хранятся в кабинете 30 дней, затем удаляются безвозвратно. Повторное скачивание бесплатно.</p>
<h3>Персональные данные и результат</h3>
<p>Разбираются записи и переписки с вашими покупателями. Вы выступаете оператором их данных, обеспечиваете законное основание обработки и уведомление покупателей. Платформа не гарантирует роста продаж — оценки носят рекомендательный характер.</p>
"""
    },
    "mop": {
        "version": TERMS_VERSION,
        "title": "Правила использования «ИИ Менеджер по продажам»",
        "html": """
<h3>Что делает сервис</h3>
<p>Менеджер отвечает покупателям в чатах Avito от вашего имени по отмеченным объявлениям, ведёт диалог к цели, показывает воронку, уведомляет в Telegram о новых телефонах, напоминает о молчащих диалогах.</p>
<h3>Базовый пакет — 10 000 ₽ (агентствам 9 000 ₽), 30 дней</h3>
<p>1 700 сообщений покупателям.</p>
<h3>Как расходуется</h3>
<p>Одна единица — одно сообщение, отправленное Менеджером. Входящие сообщения и просмотр диалогов не расходуют пакет.</p>
<h3>Докупки</h3>
<p>+2 000 сообщений за 8 000 ₽ (агентствам 7 000 ₽), +3 000 за 11 000 ₽ (10 000 ₽). Доступны при действующем базовом пакете, срок не продлевают.</p>
<h3>Срок и сгорание</h3>
<p>Пакет действует 30 дней. Остаток сгорает, кроме автопродления с оплаченного кошелька — тогда переносится (до объёма одного пакета).</p>
<h3>Ответственность за ответы</h3>
<p>Менеджер отвечает на основе предоставленных вами сведений: описания, прайса, скриптов. Ответственность за содержание отправленных сообщений несёт клиент. Поддерживайте цены и условия в актуальном состоянии.</p>
<h3>Требования к аккаунту</h3>
<p>Нужны ключи Avito API и тариф, открывающий API мессенджера. Без них Менеджер не работает; это не основание для возврата.</p>
<h3>Персональные данные и результат</h3>
<p>Обрабатываются переписки с вашими покупателями; вы выступаете оператором их данных. Платформа не гарантирует роста продаж или числа обращений.</p>
"""
    },
}


@router.get("/terms")
def get_terms(product: str = "rop"):
    t = TERMS.get(product)
    if not t:
        return {"status": "error", "message": "unknown product"}
    return {"status": "ok", **t}


class AcceptBody(BaseModel):
    account_id: str
    product: str
    pack: str = ""


@router.post("/accept")
def accept_terms(body: AcceptBody, user=Depends(get_current_user)):
    """Пишет согласие в тот же журнал действий (audit_log), что видит клиент."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    t = TERMS.get(body.product, {})
    ver = t.get("version", TERMS_VERSION)
    title = t.get("title", body.product)
    entry = {
        "ts": datetime.utcnow().isoformat(),
        "actor": "user",
        "action": "accept_terms",
        "text": "Вы приняли «%s», редакция от %s%s" % (
            title, ver, (" — при покупке пакета «%s»" % body.pack if body.pack else "")),
        "product": body.product,
        "version": ver,
        "pack": body.pack,
    }
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == body.account_id, Storage.key == "audit_log").first()
        if row:
            try:
                log = json.loads(row.value)
            except Exception:
                log = []
            if not isinstance(log, list):
                log = []
        else:
            log = []
        log.insert(0, entry)
        val = json.dumps(log[:500], ensure_ascii=False)
        if row:
            row.value = val
        else:
            db.add(Storage(account_id=body.account_id, key="audit_log", value=val))
        db.commit()
    finally:
        db.close()
    return {"status": "ok", "version": ver}


# ==================== BORIS Messenger: versioned clickwrap ====================

class MessengerLegalAcceptBody(BaseModel):
    accepted: bool
    documents: list[dict] = []


@router.get("/messenger/documents")
def messenger_documents():
    """Public immutable current legal documents for the Messenger clickwrap."""
    from app.services.messenger_legal import current_documents
    return {"status": "ok", "documents": current_documents(include_text=True)}


def _require_messenger_server_session(user) -> str:
    """Messenger security v6 requires a revocable server-side device session."""
    sid = str(getattr(user, "_auth_session_id", "") or "").strip()
    if not sid:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "messenger_session_upgrade_required",
                "message": "Сессия безопасности обновлена. Войдите снова.",
            },
        )
    return sid


@router.get("/messenger/status")
def messenger_legal_status(user=Depends(get_current_user)):
    _require_messenger_server_session(user)
    from app.db.session import SessionLocal
    from app.services.messenger_legal import acceptance_status
    db = SessionLocal()
    try:
        state = acceptance_status(db, int(user.id))
        return {"status": "ok", **state}
    finally:
        db.close()


@router.post("/messenger/accept")
def messenger_legal_accept(body: MessengerLegalAcceptBody, request: Request,
                           user=Depends(get_current_user)):
    """Explicit per-user acceptance of the exact currently-required versions/hashes."""
    _require_messenger_server_session(user)
    from app.db.session import SessionLocal
    from app.services.messenger_legal import accept_current, current_documents

    if body.accepted is not True:
        raise HTTPException(status_code=400, detail="Для продолжения необходимо явно принять документы")

    expected = {
        (item["document_type"], item["version"], item["hash"])
        for item in current_documents(include_text=False)
    }
    submitted = {
        (
            str(item.get("document_type") or ""),
            str(item.get("version") or ""),
            str(item.get("hash") or ""),
        )
        for item in (body.documents or [])
    }
    if submitted != expected:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "legal_documents_changed",
                "message": "Документы обновились. Откройте актуальную редакцию и подтвердите её.",
            },
        )

    db = SessionLocal()
    try:
        state = accept_current(
            db,
            int(user.id),
            ip=(request.client.host if request.client else ""),
            user_agent=request.headers.get("user-agent", ""),
            session_id=str(getattr(user, "_auth_session_id", "") or "") or None,
        )
        from app.services.messenger_security_audit import append_security_audit
        append_security_audit(
            db,
            action="legal_acceptance",
            user_id=int(user.id),
            entity_type="legal_documents",
            session_id=str(getattr(user, "_auth_session_id", "") or "") or None,
            request_id=request.headers.get("x-boris-trace-id"),
            details={"documents": [
                {"type": item["document_type"], "version": item["version"], "hash": item["hash"]}
                for item in current_documents(include_text=False)
            ]},
        )
        db.commit()
        from app.services.messenger_security import audit_security_event
        audit_security_event(
            user_id=int(user.id), account_id=None,
            action="Принял документы BORIS Messenger", object_kind="legal",
            object_name=", ".join(f"{item['document_type']}:{item['version']}" for item in state.get("documents", [])),
            after="accepted", reason="explicit_clickwrap",
        )
        return {"status": "ok", **state}
    finally:
        db.close()


def require_messenger_legal_user(user=Depends(get_current_user)):
    """Authenticated user with a revocable session and current legal acceptance."""
    _require_messenger_server_session(user)
    from app.db.session import SessionLocal
    from app.services.messenger_legal import require_acceptance
    db = SessionLocal()
    try:
        require_acceptance(db, int(user.id))
    finally:
        db.close()
    return user
