# -*- coding: utf-8 -*-
"""Пост-полётная проверка (Auto QA).

Проверяет ФАКТИЧЕСКОЕ состояние, а не то, что вернул исполнитель.
completed выставляется только при qa.status == PASS.
Если проверить нечем — qa.status = UNVERIFIED, и задача НЕ становится completed.
"""
from . import db, registry


def _pass(note, facts=None):
    return {"status": "PASS", "note": note, "facts": facts or {}}


def _fail(note, facts=None):
    return {"status": "FAIL", "note": note, "facts": facts or {}}


def _unverified(note, facts=None):
    return {"status": "UNVERIFIED", "note": note, "facts": facts or {}}


def _check_campaign(result):
    cid = (result or {}).get("campaign_id")
    if not cid:
        return _fail("исполнитель не вернул campaign_id")
    row = db.one("SELECT id, name, default_account_id FROM campaigns "
                 "WHERE CAST(id AS TEXT)=:i", i=str(cid))
    if not row:
        return _fail("кампании нет в базе", {"campaign_id": cid})
    return _pass("кампания существует", {"campaign_id": cid, "name": row["name"]})


def _check_items(result):
    cid = (result or {}).get("campaign_id")
    refs = (result or {}).get("expected_refs") or []
    source = (result or {}).get("source")
    requested = (result or {}).get("requested", len(refs))
    if not cid or not refs:
        return _fail("исполнитель не вернул campaign_id/expected_refs")
    got = db.one("""SELECT COUNT(*) AS n FROM campaign_items
                    WHERE CAST(campaign_id AS TEXT)=:c AND source=:s
                      AND source_ref = ANY(:refs)""",
                 c=str(cid), s=source, refs=refs)["n"]
    facts = {"campaign_id": cid, "requested": requested, "actual_in_db": got,
             "created": (result or {}).get("created"), "updated": (result or {}).get("updated"),
             "skipped": (result or {}).get("skipped")}
    if got != requested:
        return _fail(f"в базе {got} элементов из {requested} ожидаемых", facts)
    return _pass("все элементы подтверждены в базе", facts)


def _check_launch(result):
    camp = _check_campaign(result)
    if camp["status"] != "PASS":
        return camp
    if not (result or {}).get("expected_refs"):
        return _pass("кампания подготовлена, объявления не передавались",
                     {"campaign_id": result.get("campaign_id")})
    items = _check_items(result)
    items["facts"]["campaign_verified"] = True
    return items


VERIFIERS = {
    "campaign_create": _check_campaign,
    "ads_create": _check_items,
    "launch_client": _check_launch,
}


def run(operation, result):
    spec = registry.get(operation) or {}
    fn = VERIFIERS.get(operation)
    if fn:
        try:
            return fn(result)
        except Exception as e:
            return _fail(f"проверка не отработала: {type(e).__name__}: {e}"[:300])
    if spec.get("cls") == registry.READ_ONLY:
        return _pass("операция только читает, менять было нечего")
    return _unverified("для этой операции ещё нет автопроверки фактического состояния — "
                       "задача не может быть отмечена completed")
