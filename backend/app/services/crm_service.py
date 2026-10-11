# -*- coding: utf-8 -*-
"""Сервисный слой мини-CRM.

Извлечён из app/api/crm.py БЕЗ изменения поведения: тот же SQL, те же
правила. Нужен, чтобы Command Center и ручки сходились в одну точку —
executor обязан звать функцию, а не слать HTTP-запрос сам себе.

Ошибки валидации — ValueError с кодом; в HTTP их переводит слой ручек.
"""
import datetime

from sqlalchemy import text

TASK_TYPES = ("позвонить", "написать", "замер", "отправить фото", "отправить КП",
              "запросить оплату", "уточнить доставку", "другое")


def own(db, uid, account_id):
    """Владение имеет приоритет, затем связь user_account_access."""
    row = db.execute(text("select 1 from accounts where account_id=:a and owner_user_id=:u"),
                     {"a": account_id, "u": uid}).fetchone()
    if not row:
        row = db.execute(text(
            "select 1 from user_account_access"
            " where user_id=:u and account_id=:a and can_view = TRUE limit 1"),
            {"a": account_id, "u": uid}).fetchone()
    return bool(row)


def shape(r, today):
    status = r.status
    if status != "done":
        if r.due_date < today:
            status = "overdue"
        elif r.due_date == today:
            status = "today"
        else:
            status = "planned"
    return {"id": r.id, "chat_id": r.avito_chat_id,
            "due_date": r.due_date.isoformat() if r.due_date else None,
            "due_time": r.due_time or "", "type": r.task_type, "title": r.title,
            "comment": r.comment or "", "status": status,
            "done_at": r.done_at.isoformat() if r.done_at else None,
            "done_comment": r.done_comment or ""}


def list_tasks(db, account_id, chat_id=""):
    today = datetime.date.today()
    q = "select * from crm_tasks where account_id=:a"
    p = {"a": account_id}
    if chat_id:
        q += " and avito_chat_id=:c"
        p["c"] = chat_id
    q += " order by (status='done'), due_date, due_time nulls last, id"
    rows = db.execute(text(q), p).fetchall()
    items = [shape(r, today) for r in rows]
    return {"tasks": items,
            "counts": {k: sum(1 for i in items if i["status"] == k)
                       for k in ("today", "overdue", "planned", "done")}}


def badges(db, account_id):
    today = datetime.date.today()
    rows = db.execute(text(
        "select avito_chat_id, count(*) filter (where due_date < :t) as overdue, "
        "count(*) filter (where due_date = :t) as today, count(*) as total "
        "from crm_tasks where account_id=:a and status<>'done' and avito_chat_id is not null "
        "group by avito_chat_id"), {"a": account_id, "t": today}).fetchall()
    return {r[0]: {"overdue": r[1], "today": r[2], "total": r[3]} for r in rows}


def create_task(db, uid, account_id, title, due_date, chat_id="", due_time="",
                task_type="позвонить", comment=""):
    if not (title or "").strip():
        raise ValueError("no_title")
    try:
        d = datetime.date.fromisoformat((due_date or "")[:10])
    except Exception:
        raise ValueError("bad_date")
    t = (task_type or "").strip() or "другое"
    if t not in TASK_TYPES:
        t = "другое"
    _rule_context = _prepare_task_rule(db, account_id, "created")
    r = db.execute(text(
        "insert into crm_tasks (account_id, avito_chat_id, due_date, due_time,"
        " task_type, title, comment, created_by)"
        " values (:a,:c,:d,:tm,:tp,:ti,:cm,:u) returning id"),
        {"a": account_id, "c": chat_id or None, "d": d, "tm": (due_time or "")[:5],
         "tp": t, "ti": title.strip()[:300], "cm": (comment or "").strip(),
         "u": str(uid)}).fetchone()
    _observe_task_rule(db, account_id, int(r[0]), "created", _rule_context)
    db.commit()
    return r[0]


def done_task(db, account_id, task_id, comment=""):
    _rule_context = _prepare_task_rule(db, account_id, "completed")
    db.execute(text(
        "update crm_tasks set status='done', done_at=now(), done_comment=:c,"
        " updated_at=now() where id=:i and account_id=:a"),
        {"c": (comment or "").strip(), "i": task_id, "a": account_id})
    _observe_task_rule(db, account_id, int(task_id), "completed", _rule_context)
    db.commit()
    return True


def update_task(db, account_id, task_id, due_date="", due_time="", title="",
                task_type="", comment=""):
    sets, p = [], {"i": task_id, "a": account_id}
    if due_date:
        try:
            p["d"] = datetime.date.fromisoformat(due_date[:10])
        except Exception:
            raise ValueError("bad_date")
        sets.append("due_date=:d")
    if due_time:
        sets.append("due_time=:tm"); p["tm"] = due_time[:5]
    if title:
        sets.append("title=:ti"); p["ti"] = title.strip()[:300]
    if task_type and task_type in TASK_TYPES:
        sets.append("task_type=:tp"); p["tp"] = task_type
    if comment:
        sets.append("comment=:cm"); p["cm"] = comment.strip()
    if not sets:
        return 0
    sets.append("status='planned'")
    sets.append("updated_at=now()")
    db.execute(text("update crm_tasks set " + ", ".join(sets) +
                    " where id=:i and account_id=:a"), p)
    db.commit()
    return len(sets)


def delete_task(db, account_id, task_id):
    db.execute(text("delete from crm_tasks where id=:i and account_id=:a"),
               {"i": task_id, "a": account_id})
    db.commit()
    return True


def today_tasks(db, uid, limit=50):
    """Сводка для колокольчика. ⚠ Фильтр по owner_user_id сохранён как есть —
    извлечение без изменения поведения. То, что сотрудник видит пустой
    список, — отдельный дефект, чинится не здесь."""
    rows = db.execute(text("""
        select t.id, t.account_id, t.avito_chat_id, t.due_date, t.due_time,
               t.task_type, t.title, a.name
          from crm_tasks t
          join accounts a on a.account_id = t.account_id
         where (a.owner_user_id = :u
                or exists (select 1 from user_account_access uaa
                            where uaa.user_id = :u
                              and uaa.account_id = t.account_id
                              and uaa.can_view = TRUE))
           and t.status = 'planned'
           and t.due_date <= CURRENT_DATE
         order by t.due_date asc, coalesce(t.due_time, '99:99') asc
         limit :l
    """), {"u": uid, "l": limit}).fetchall()
    today = datetime.date.today()
    items, n_today, n_late = [], 0, 0
    for r in rows:
        late = r.due_date < today
        if late:
            n_late += 1
        else:
            n_today += 1
        items.append({
            "id": r.id, "account_id": r.account_id, "avito_chat_id": r.avito_chat_id,
            "дата": r.due_date.isoformat(), "время": r.due_time or "",
            "тип": r.task_type, "заголовок": r.title,
            "аккаунт": r.name or r.account_id, "просрочено": late})
    return {"всего": len(items), "сегодня": n_today, "просрочено": n_late,
            "задачи": items}


def _observe_task_rule(db, account_id, task_id, event, context):
    from app.services.control_rule_runtime import _observe_rule_failure
    try:
        from app.services.control_rule_runtime import observe_legacy_crm_task
        with db.begin_nested():
            observe_legacy_crm_task(db, account_id, task_id, event, context=context)
    except Exception as exc:
        _observe_rule_failure(exc)


def _prepare_task_rule(db, account_id, event):
    from app.services.control_rule_runtime import _observe_rule_failure
    try:
        from app.services.control_rule_runtime import prepare_legacy_crm_task_context
        with db.begin_nested():
            return prepare_legacy_crm_task_context(db, account_id, event)
    except Exception as exc:
        _observe_rule_failure(exc)
        return None
