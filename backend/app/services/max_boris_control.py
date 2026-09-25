# -*- coding: utf-8 -*-
"""Restricted MAX -> BORIS control plane.

Only CRM stage changes and reminders are supported here. Chat scope is a hard
boundary: owner chat may resolve across owner's accounts, client chat is bound
to one BORIS account. Ambiguous entities fail closed.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any

from sqlalchemy import text
from app.db.session import SessionLocal

OWNER_USER_ID = 2
TZ = ZoneInfo("Europe/Moscow")

_STAGE_ALIASES = {
    "new": ("новый", "новая", "новое"),
    "contacted": ("связались", "связался", "контакт", "связь"),
    "qualified": ("квалифицирован", "квалификация", "целевой"),
    "work": ("в работе", "работа", "работаем"),
    "proposal": ("кп", "предложение", "коммерческое предложение"),
    "contract": ("договор", "контракт"),
    "won": ("оплачено", "оплата", "успешно", "сделка", "купил", "оплатил"),
    "lost": ("не реализовано", "проиграно", "отказ", "потерян", "не купил"),
}

_MONTHS = {
    "января": 1, "январь": 1,
    "февраля": 2, "февраль": 2,
    "марта": 3, "март": 3,
    "апреля": 4, "апрель": 4,
    "мая": 5, "май": 5,
    "июня": 6, "июнь": 6,
    "июля": 7, "июль": 7,
    "августа": 8, "август": 8,
    "сентября": 9, "сентябрь": 9,
    "октября": 10, "октябрь": 10,
    "ноября": 11, "ноябрь": 11,
    "декабря": 12, "декабрь": 12,
}


def _norm(v: Any) -> str:
    return re.sub(r"[^a-zа-я0-9]+", " ", str(v or "").lower().replace("ё", "е")).strip()


def ensure_schema() -> None:
    db = SessionLocal()
    try:
        db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
        db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
        db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
        db.commit()
    finally:
        db.close()


def register_chat(chat_id: str, transport_account_id: str, route_type: str,
                  *, boris_account_id: str | None = None, chat_title: str = "",
                  max_user_id: str = "") -> None:
    ensure_schema()
    if route_type not in {"owner", "client", "unbound"}:
        raise ValueError("bad_route_type")
    db = SessionLocal()
    try:
        db.execute(text("""
          INSERT INTO max_boris_chat_routes(
            chat_id,transport_account_id,route_type,boris_account_id,chat_title,enabled,created_by_max_user_id,updated_at
          ) VALUES(:c,:t,:r,:a,:title,true,:u,now())
          ON CONFLICT(chat_id) DO UPDATE SET
            transport_account_id=excluded.transport_account_id,
            route_type=excluded.route_type,
            boris_account_id=excluded.boris_account_id,
            chat_title=excluded.chat_title,
            enabled=true,
            updated_at=now()
        """), {"c": str(chat_id), "t": str(transport_account_id), "r": route_type,
               "a": boris_account_id, "title": str(chat_title or "")[:240],
               "u": str(max_user_id or "")[:120] or None})
        db.commit()
    finally:
        db.close()


def bind_client_chat(chat_id: str, boris_account_id: str) -> dict:
    ensure_schema()
    db = SessionLocal()
    try:
        exists = db.execute(text("SELECT name FROM accounts WHERE account_id=:a"), {"a": boris_account_id}).scalar()
        if not exists:
            return {"status": "account_not_found"}
        changed = db.execute(text("""
          UPDATE max_boris_chat_routes
             SET route_type='client',boris_account_id=:a,enabled=true,updated_at=now()
           WHERE chat_id=:c
           RETURNING chat_title
        """), {"a": boris_account_id, "c": str(chat_id)}).mappings().first()
        db.commit()
        return {"status": "ok" if changed else "chat_not_found", "account_id": boris_account_id,
                "account_name": str(exists)}
    finally:
        db.close()


def unregister_chat(chat_id: str) -> None:
    ensure_schema()
    db = SessionLocal()
    try:
        db.execute(text("UPDATE max_boris_chat_routes SET enabled=false,updated_at=now() WHERE chat_id=:c"), {"c": str(chat_id)})
        db.commit()
    finally:
        db.close()


def _route(db, chat_id: str) -> dict | None:
    row = db.execute(text("""
      SELECT chat_id,transport_account_id,route_type,boris_account_id,chat_title,created_by_max_user_id
      FROM max_boris_chat_routes WHERE chat_id=:c AND enabled=true
    """), {"c": str(chat_id)}).mappings().first()
    return dict(row) if row else None


def _account_candidates(db, command: str, route: dict) -> list[dict]:
    if route.get("route_type") == "client":
        row = db.execute(text("SELECT account_id,name FROM accounts WHERE account_id=:a"),
                         {"a": route.get("boris_account_id")}).mappings().first()
        return [dict(row)] if row else []
    rows = [dict(x) for x in db.execute(text("""
      SELECT account_id,name FROM accounts WHERE owner_user_id=:o ORDER BY length(coalesce(name,'')) DESC,id
    """), {"o": OWNER_USER_ID}).mappings().all()]
    low = _norm(command)
    explicit = [r for r in rows if _norm(r.get("name")) and _norm(r.get("name")) in low]
    return explicit or rows


def _deal_candidates(db, client: str, command: str, route: dict) -> list[dict]:
    client_n = _norm(client)
    if not client_n:
        return []
    accounts = _account_candidates(db, command, route)
    ids = [str(r["account_id"]) for r in accounts]
    if not ids:
        return []
    rows = [dict(x) for x in db.execute(text("""
      SELECT d.id,d.avito_account_id,d.pipeline_id,d.stage_id,d.status,d.title,
             c.display_name,c.company_name,s.name current_stage,s.position current_position
      FROM boris_crm_deals d
      LEFT JOIN boris_crm_contacts c ON c.id=d.contact_id
      LEFT JOIN boris_crm_stages s ON s.id=d.stage_id
      WHERE d.owner_user_id=:o
        AND d.avito_account_id = ANY(:accounts)
        AND d.status='open'
        AND (
          lower(coalesce(c.display_name,'')) LIKE :lk
          OR lower(coalesce(c.company_name,'')) LIKE :lk
          OR lower(coalesce(d.title,'')) LIKE :lk
        )
      ORDER BY COALESCE(d.last_activity_at,d.updated_at,d.created_at) DESC,d.id DESC
      LIMIT 30
    """), {"o": OWNER_USER_ID, "accounts": ids, "lk": "%" + str(client).lower() + "%"}).mappings().all()]
    for r in rows:
        vals = [_norm(r.get("display_name")), _norm(r.get("company_name")), _norm(r.get("title"))]
        score = 0
        for v in vals:
            if not v:
                continue
            if v == client_n:
                score = max(score, 100)
            elif client_n in v:
                score = max(score, 80)
            elif all(tok in v for tok in client_n.split()):
                score = max(score, 70)
        r["_score"] = score
    rows.sort(key=lambda x: (-int(x.get("_score") or 0), -int(x["id"])))
    if not rows:
        return []
    best = int(rows[0].get("_score") or 0)
    return [r for r in rows if int(r.get("_score") or 0) == best][:8]


def _extract_stage_command(command: str) -> tuple[str, str] | None:
    raw = re.sub(r"\s+", " ", command.strip())
    patterns = [
        r"(?:по\s+)?клиент(?:у|а)?\s+[«\"]?(.+?)[»\"]?\s+(?:уш[её]л|переш[её]л|перевед[иите]*|перевести|теперь).*?(?:в|на)\s+(?:этап\s+)?(.+)$",
        r"перевед[иите]*\s+(?:клиента\s+)?[«\"]?(.+?)[»\"]?\s+(?:в|на)\s+(?:этап\s+)?(.+)$",
        r"(?:этап|стадия)\s+(?:клиента\s+)?[«\"]?(.+?)[»\"]?\s*[-—:]\s*(.+)$",
    ]
    for p in patterns:
        m = re.search(p, raw, flags=re.I)
        if m:
            return m.group(1).strip(" «»\""), m.group(2).strip(" .,!«»\"")
    return None


def _resolve_stage(db, deal: dict, target: str) -> dict | None:
    target_n = _norm(target)
    stages = [dict(x) for x in db.execute(text("""
      SELECT id,name,code,position,semantic_type
      FROM boris_crm_stages WHERE pipeline_id=:p ORDER BY position,id
    """), {"p": deal["pipeline_id"]}).mappings().all()]
    if "следующ" in target_n:
        later = [x for x in stages if int(x.get("position") or 0) > int(deal.get("current_position") or 0)]
        return later[0] if later else None
    wanted_code = None
    for code, aliases in _STAGE_ALIASES.items():
        if any(_norm(a) == target_n or _norm(a) in target_n for a in aliases):
            wanted_code = code
            break
    if wanted_code:
        hit = next((x for x in stages if str(x.get("code") or "") == wanted_code), None)
        if hit:
            return hit
    exact = [x for x in stages if _norm(x.get("name")) == target_n]
    if len(exact) == 1:
        return exact[0]
    contains = [x for x in stages if target_n and (target_n in _norm(x.get("name")) or _norm(x.get("name")) in target_n)]
    return contains[0] if len(contains) == 1 else None


def _stage_help(db, pipeline_id: int) -> str:
    names = [str(x[0]) for x in db.execute(text(
        "SELECT name FROM boris_crm_stages WHERE pipeline_id=:p ORDER BY position,id"
    ), {"p": pipeline_id}).all()]
    return ", ".join(names)


def _handle_stage(db, command: str, route: dict, max_user_id: str) -> dict | None:
    parsed = _extract_stage_command(command)
    if not parsed:
        return None
    client, target = parsed
    deals = _deal_candidates(db, client, command, route)
    if not deals:
        return {"handled": True, "reply": f"Не нашёл активную сделку по клиенту «{client}». Ничего не изменил."}
    if len(deals) > 1 and int(deals[0].get("_score") or 0) == int(deals[1].get("_score") or 0):
        options = "\n".join(
            f"• {d.get('display_name') or d.get('company_name') or d.get('title')} · {d.get('current_stage') or '—'} · {d.get('avito_account_id')}"
            for d in deals[:5]
        )
        return {"handled": True, "reply": "Нашёл несколько подходящих клиентов. Ничего не меняю, уточните:\n" + options}
    deal = deals[0]
    stage = _resolve_stage(db, deal, target)
    if not stage:
        return {"handled": True, "reply": "Не понял этап «%s». Доступные этапы: %s" %
                (target, _stage_help(db, int(deal["pipeline_id"])))}
    before = str(deal.get("current_stage") or "—")
    semantic = str(stage.get("semantic_type") or "open")
    status = "won" if semantic == "won" else ("lost" if semantic == "lost" else "open")
    db.execute(text("""
      UPDATE boris_crm_deals
         SET stage_id=:s,status=:st,last_activity_at=NOW(),updated_at=NOW()
       WHERE id=:d AND owner_user_id=:o
    """), {"s": int(stage["id"]), "st": status, "d": int(deal["id"]), "o": OWNER_USER_ID})
    db.execute(text("""
      INSERT INTO boris_crm_activities(
        owner_user_id,deal_id,activity_type,channel,title,body,source,source_ref,actor_type,created_at
      ) VALUES(
        :o,:d,'stage_change','max','Этап изменён из MAX',:b,'max_control',:r,'human',NOW()
      )
    """), {"o": OWNER_USER_ID, "d": int(deal["id"]),
           "b": f"{before} → {stage['name']} · MAX user {max_user_id}",
           "r": f"max_stage:{deal['id']}:{stage['id']}:{int(datetime.now(timezone.utc).timestamp())}"})
    db.commit()
    who = deal.get("display_name") or deal.get("company_name") or deal.get("title") or client
    return {"handled": True, "reply": f"✅ {who}: этап изменён «{before}» → «{stage['name']}»."}


def _parse_date(command: str, now_local: datetime) -> date | None:
    low = _norm(command)
    if "послезавтра" in low:
        return now_local.date() + timedelta(days=2)
    if "завтра" in low:
        return now_local.date() + timedelta(days=1)
    if "сегодня" in low:
        return now_local.date()
    m = re.search(r"\b(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?\b", command)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        y = int(m.group(3)) if m.group(3) else now_local.year
        if y < 100:
            y += 2000
        try:
            out = date(y, mo, d)
            if not m.group(3) and out < now_local.date():
                out = date(y + 1, mo, d)
            return out
        except ValueError:
            return None
    m = re.search(r"\b(\d{1,2})\s+(" + "|".join(map(re.escape, _MONTHS.keys())) + r")(?:\s+(\d{4}))?\b",
                  command.lower().replace("ё", "е"))
    if m:
        d, mo = int(m.group(1)), _MONTHS[m.group(2)]
        y = int(m.group(3)) if m.group(3) else now_local.year
        try:
            out = date(y, mo, d)
            if not m.group(3) and out < now_local.date():
                out = date(y + 1, mo, d)
            return out
        except ValueError:
            return None
    return None


def _extract_times(command: str) -> list[str]:
    raw = []
    for h, m in re.findall(r"(?<!\d)([01]?\d|2[0-3])[:.]([0-5]\d)(?!\d)", command):
        raw.append(f"{int(h):02d}:{int(m):02d}")
    out = []
    for x in raw:
        if x not in out:
            out.append(x)
    return out[:8]


def _default_repeat_times(times: list[str]) -> list[str]:
    if len(times) >= 2:
        return sorted(times)
    if not times:
        return ["10:00", "14:00", "18:00"]
    h, m = map(int, times[0].split(":"))
    base = datetime.combine(date.today(), dtime(h, m))
    result = [times[0]]
    for hours in (3, 6):
        x = base + timedelta(hours=hours)
        if x.date() == base.date() and x.time() <= dtime(21, 30):
            val = x.strftime("%H:%M")
            if val not in result:
                result.append(val)
    return result


def _extract_reminder_client(command: str) -> str:
    m = re.search(
        r"(?:по\s+)?клиент(?:у|а)?\s+[«\"]?(.+?)[»\"]?(?=\s+(?:сегодня|завтра|послезавтра|\d{1,2}[.\-/]|\d{1,2}\s+(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)|в\s+\d{1,2}[:.]\d{2})|$)",
        command, flags=re.I,
    )
    return m.group(1).strip(" «»\",.") if m else ""


def _clean_reminder_text(command: str) -> str:
    s = re.sub(r"\b(?:поставь|создай|сделай)?\s*напоминани[ея]?\b|\bнапомни\b", "", command, flags=re.I)
    s = re.sub(r"\b(?:сегодня|завтра|послезавтра)\b", "", s, flags=re.I)
    s = re.sub(r"\b\d{1,2}[.\-/]\d{1,2}(?:[.\-/]\d{2,4})?\b", "", s)
    s = re.sub(r"\b\d{1,2}[:.]\d{2}\b", "", s)
    s = re.sub(r"^\s*(?:в|на)\s+", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" ,.-")
    return s[:1000] or "Проверить клиента"


def _handle_reminder(db, command: str, route: dict, max_user_id: str) -> dict | None:
    low = _norm(command)
    if "напомни" not in low and "напоминан" not in low:
        return None
    now_local = datetime.now(timezone.utc).astimezone(TZ)
    due = _parse_date(command, now_local)
    if not due:
        return {"handled": True, "reply": "Не понял дату напоминания. Напишите, например: «15 сентября в 11:00»."}
    times = _default_repeat_times(_extract_times(command))
    client = _extract_reminder_client(command)
    deal = None
    if client:
        deals = _deal_candidates(db, client, command, route)
        if not deals:
            return {"handled": True, "reply": f"Не нашёл клиента «{client}». Напоминание не создаю, чтобы не привязать его не туда."}
        if len(deals) > 1 and int(deals[0].get("_score") or 0) == int(deals[1].get("_score") or 0):
            return {"handled": True, "reply": f"По «{client}» найдено несколько сделок. Уточните клиента или аккаунт."}
        deal = deals[0]
    account_id = route.get("boris_account_id")
    if deal:
        account_id = deal.get("avito_account_id")
    reminder_text = _clean_reminder_text(command)
    rid = db.execute(text("""
      INSERT INTO max_boris_reminders(
        chat_id,transport_account_id,boris_account_id,deal_id,client_label,reminder_text,
        due_date,repeat_times,sent_times,status,created_by_max_user_id
      ) VALUES(:chat,:transport,:account,:deal,:client,:body,:due,CAST(:times AS JSONB),'[]'::jsonb,'active',:u)
      RETURNING id
    """), {
        "chat": route["chat_id"], "transport": route["transport_account_id"],
        "account": account_id, "deal": int(deal["id"]) if deal else None,
        "client": client or None, "body": reminder_text, "due": due,
        "times": json.dumps(times), "u": max_user_id or None,
    }).scalar_one()
    if deal:
        first_dt = datetime.combine(due, dtime.fromisoformat(times[0]), tzinfo=TZ).astimezone(timezone.utc)
        db.execute(text("""
          INSERT INTO boris_crm_tasks(
            owner_user_id,deal_id,title,description,due_at,assigned_user_id,status,source
          ) VALUES(:o,:d,:t,:descr,:due,:o,'open','max_control')
        """), {"o": OWNER_USER_ID, "d": int(deal["id"]),
               "t": reminder_text[:500], "descr": f"MAX reminder #{rid}",
               "due": first_dt})
        db.execute(text("UPDATE boris_crm_deals SET next_action_at=LEAST(COALESCE(next_action_at,:due),:due),updated_at=NOW() WHERE id=:d"),
                   {"due": first_dt, "d": int(deal["id"])})
    db.commit()
    who = f" по клиенту «{client}»" if client else ""
    return {"handled": True, "reply": f"✅ Напоминание{who} поставлено на {due.strftime('%d.%m.%Y')}: {', '.join(times)}. Тема: {reminder_text}"}


def _handle_bind_client_chat(db, command: str, route: dict) -> dict | None:
    if route.get("route_type") != "owner":
        return None
    low = _norm(command)
    if "привяжи" not in low or not any(x in low for x in ("групп", "чат")):
        return None

    m = re.search(
        r"привяжи\s+(?:последн(?:юю|ий)\s+)?(?:группу|чат)\s*(.*?)\s+к\s+(.+)$",
        command,
        flags=re.I,
    )
    if not m:
        return {
            "handled": True,
            "reply": "Напишите: «привяжи последнюю группу к Гарик плитка МО» или «привяжи группу Название к Название аккаунта».",
        }

    group_hint = m.group(1).strip(" «»\"")
    account_hint = m.group(2).strip(" «»\".,")
    ah = _norm(account_hint)

    accounts = [dict(x) for x in db.execute(text(
        "SELECT account_id,name FROM accounts WHERE owner_user_id=:o ORDER BY length(coalesce(name,'')) DESC,id"
    ), {"o": OWNER_USER_ID}).mappings().all()]

    matches = [
        a for a in accounts
        if ah and (
            ah == _norm(a.get("name"))
            or ah in _norm(a.get("name"))
            or _norm(a.get("name")) in ah
        )
    ]
    if len(matches) != 1:
        token_matches = [
            a for a in accounts
            if ah and all(tok in _norm(a.get("name")) for tok in ah.split())
        ]
        if len(token_matches) == 1:
            matches = token_matches

    if len(matches) != 1:
        opts = ", ".join(str(a.get("name")) for a in matches[:6]) if matches else "совпадений нет"
        return {
            "handled": True,
            "reply": f"Не смог однозначно определить аккаунт «{account_hint}»: {opts}. Ничего не привязал.",
        }

    if group_hint:
        gh = _norm(group_hint)
        groups = [
            dict(x)
            for x in db.execute(text("""
              SELECT chat_id,chat_title,updated_at
              FROM max_boris_chat_routes
              WHERE route_type='unbound' AND enabled=true
              ORDER BY updated_at DESC
              LIMIT 30
            """)).mappings().all()
            if gh and gh in _norm(x.get("chat_title"))
        ]
    else:
        groups = [dict(x) for x in db.execute(text("""
          SELECT chat_id,chat_title,updated_at
          FROM max_boris_chat_routes
          WHERE route_type='unbound' AND enabled=true
          ORDER BY updated_at DESC
          LIMIT 2
        """)).mappings().all()]
        groups = groups[:1]

    if len(groups) != 1:
        names = ", ".join(str(g.get("chat_title") or g.get("chat_id")) for g in groups[:6]) if groups else "непривязанных групп нет"
        return {
            "handled": True,
            "reply": f"Не смог однозначно определить группу: {names}. Ничего не привязал.",
        }

    g = groups[0]
    a = matches[0]
    changed = db.execute(text("""
      UPDATE max_boris_chat_routes
         SET route_type='client',boris_account_id=:a,updated_at=now()
       WHERE chat_id=:c AND route_type='unbound'
    """), {"a": a["account_id"], "c": g["chat_id"]}).rowcount or 0
    if not changed:
        db.rollback()
        return {"handled": True, "reply": "Группа уже изменилась или была привязана. Ничего не сделал."}

    db.commit()
    return {
        "handled": True,
        "reply": f"✅ Группа «{g.get('chat_title') or g['chat_id']}» привязана к аккаунту «{a['name']}». Команды из неё теперь работают только в этом аккаунте.",
    }


def help_text(route_type: str) -> str:
    scope = "по всем вашим аккаунтам" if route_type == "owner" else "только по этому клиентскому аккаунту"
    return (
        "🤖 Команды BORIS в MAX (" + scope + "):\n"
        "• «переведи клиента Иван в Договор»\n"
        "• «клиент Иван перешёл в Оплачено»\n"
        "• «напомни по клиенту Иван 15 сентября в 10:00 отправить КП»\n"
        "• можно указать несколько времён: «10:00, 14:00, 18:00»\n"
        + ("• «привяжи последнюю группу к Гарик плитка МО»\n" if route_type == "owner" else "") +
        "Если время одно, BORIS добавит повторные напоминания в тот же день."
    )


def handle_message(transport_account_id: str, chat_id: str, max_user_id: str, command: str) -> dict:
    ensure_schema()
    command = str(command or "").strip()
    if not command:
        return {"handled": False}
    db = SessionLocal()
    try:
        route = _route(db, chat_id)
        if not route:
            return {"handled": False}
        if route.get("transport_account_id") != transport_account_id or route.get("route_type") == "unbound":
            return {"handled": False}
        low = _norm(command)
        if route.get("route_type") == "owner":
            owner_uid = str(route.get("created_by_max_user_id") or "").strip()
            if owner_uid and str(max_user_id or "").strip() != owner_uid:
                return {"handled": True, "reply": "⛔ Эта группа — пульт владельца BORIS. У вас нет права выполнять управляющие команды."}
        if low in {"помощь", "команды", "что умеешь", "boris", "борис"}:
            return {"handled": True, "reply": help_text(str(route.get("route_type")))}
        out = _handle_bind_client_chat(db, command, route)
        if out:
            return out
        out = _handle_stage(db, command, route, max_user_id)
        if out:
            return out
        out = _handle_reminder(db, command, route, max_user_id)
        if out:
            return out
        return {"handled": True, "reply": help_text(str(route.get("route_type")))}
    except Exception as exc:
        db.rollback()
        return {"handled": True, "reply": "Не выполнил команду: внутренняя проверка не прошла. Ничего в CRM не изменено.",
                "error_type": type(exc).__name__}
    finally:
        db.close()


def queue_due_reminders(now_utc: datetime | None = None) -> dict:
    ensure_schema()
    now_utc = now_utc or datetime.now(timezone.utc)
    local = now_utc.astimezone(TZ)
    db = SessionLocal()
    queued = 0
    try:
        rows = [dict(x) for x in db.execute(text("""
          SELECT r.*,cr.route_type
          FROM max_boris_reminders r
          LEFT JOIN max_boris_chat_routes cr ON cr.chat_id=r.chat_id
          WHERE r.status='active' AND r.due_date<=:day
          ORDER BY r.due_date,r.id
          LIMIT 300
        """), {"day": local.date()}).mappings().all()]
        for r in rows:
            due_date = r["due_date"]
            times = list(r.get("repeat_times") or [])
            sent = list(r.get("sent_times") or [])
            if due_date < local.date():
                due_times = [x for x in times if x not in sent]
            else:
                due_times = [x for x in times if x not in sent and x <= local.strftime("%H:%M")]
            for tm in due_times:
                from app.services.lead_notifications import queue_event
                who = f" · {r.get('client_label')}" if r.get("client_label") else ""
                result = queue_event(
                    str(r["transport_account_id"]),
                    f"max-control-reminder:{r['id']}:{due_date.isoformat()}:{tm}",
                    "max_control_reminder",
                    f"⏰ Напоминание{who}",
                    str(r["reminder_text"]),
                    severity="critical",
                    url=(f"/dashboard/crm?account_id={r.get('boris_account_id')}" if r.get("boris_account_id") else "/dashboard/crm"),
                    payload={
                        "max_channel": "direct_chat",
                        "max_chat_id": str(r["chat_id"]),
                        "delivery_scope": "max_only",
                        "reminder_id": int(r["id"]),
                        "deal_id": int(r["deal_id"]) if r.get("deal_id") else None,
                        "scheduled_time": tm,
                    },
                )
                if result.get("status") in {"queued", "exists"}:
                    sent.append(tm)
                    queued += int(result.get("status") == "queued")
            if times and all(x in sent for x in times):
                status = "completed"
            else:
                status = "active"
            db.execute(text("""
              UPDATE max_boris_reminders
                 SET sent_times=CAST(:sent AS JSONB),status=:st,updated_at=NOW()
               WHERE id=:i
            """), {"sent": json.dumps(sorted(set(sent))), "st": status, "i": int(r["id"])})
        db.commit()
        return {"status": "ok", "queued": queued}
    finally:
        db.close()
