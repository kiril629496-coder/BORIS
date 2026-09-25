# -*- coding: utf-8 -*-
# NICHE_RESOLVER_V1 / NICHE_JUNK_FILTER_V1 / NICHE_PROFILE_FALLBACK_V1
# Единственное место, где решается, что такое «ниша аккаунта».
# Приоритет: company_niche -> form_data.company_niche -> input_data.direction
#            -> derive_niche_from_profile (name, подтверждённый company_description)
import json
import re
from sqlalchemy import text as _T
from app.db.session import SessionLocal
from app.services.exception_observability import observe_suppressed

SOURCE_PROFILE = "profile"
SOURCE_FORM = "onboarding_form"
SOURCE_SCENARIO = "scenario_input"
SOURCE_ACCOUNT_NAME = "account_name"
ORDER = (SOURCE_PROFILE, SOURCE_FORM, SOURCE_SCENARIO, SOURCE_ACCOUNT_NAME)
# В профиль переливаем только то, что ввёл клиент. Fallback — производная, её не пишем.
WRITABLE_SOURCES = (SOURCE_FORM, SOURCE_SCENARIO)

_JUNK_PREFIX = ("\u0442\u0435\u0441\u0442", "test", "\u0434\u0435\u043c\u043e", "demo",
                "\u043f\u0440\u0438\u043c\u0435\u0440", "sample")
_JUNK_EXACT = set(["qa", "qwe", "asd", "asdf", "xxx", "\u0445\u0445\u0445", "n/a", "na",
                   "null", "none", "-", "\u043d\u0438\u0448\u0430",
                   "\u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0430", "check"])
_STOP = set(["\u0438", "\u0432", "\u043d\u0430", "\u043f\u043e", "\u0434\u043b\u044f", "\u043e\u0442",
             "\u0441", "\u043a", "\u043e\u043e\u043e", "\u0438\u043f", "\u0437\u0430\u043e"])
STEM = 5
MIN_CONF = 0.6


def _norm(v):
    return " ".join(str(v or "").split()).strip()


def _key(v):
    return _norm(v).lower().replace("\u0451", "\u0435")


def _tokens(v):
    return [t for t in re.split(r"[^0-9a-z\u0430-\u044f]+", _key(v)) if t]


def is_junk(v):
    """fail-closed: непригодное значение НЕ является валидным источником ниши."""
    s = _key(v)
    if not s or len(s) < 3:
        return True
    toks = _tokens(s)
    if not toks:
        return True
    for t in toks:
        if t in _JUNK_EXACT or t.isdigit():
            continue
        if any(t.startswith(p) for p in _JUNK_PREFIX):
            continue
        return False
    return True


def _parse(txt):
    # form_data хранится ТЕКСТОМ и бывает python-repr, а не JSON.
    if not txt:
        return {}
    try:
        v = json.loads(txt)
        return v if isinstance(v, dict) else {}
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=66)
    try:
        import ast
        v = ast.literal_eval(txt)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def _stems(v):
    return set(t[:STEM] for t in _tokens(v) if len(t) >= 4)


def derive_niche_from_profile(name, description):
    """Детерминированный fallback. Берём ТОЛЬКО accounts.name и только если он
    выглядит короткой бизнес-формулировкой И подтверждён company_description.
    Описание — контекст для проверки, а не источник значения. Без AI и без срезов."""
    n = _norm(name)
    empty = {"value": "", "source": "", "confidence": 0.0}
    if is_junk(n):
        empty["reason"] = "name пуст или мусорный"
        return empty
    words = [w for w in n.split(" ") if w]
    if len(words) > 6 or len(n) > 60 or len(n) < 6:
        empty["reason"] = "name не похож на короткую формулировку (%d слов, %d симв)" % (len(words), len(n))
        return empty
    toks = [t for t in _tokens(n) if len(t) >= 4 and t not in _STOP]
    if not toks:
        empty["reason"] = "в name нет значимых слов"
        return empty
    dst = _stems(description)
    if not dst:
        empty["reason"] = "company_description пуст — подтвердить name нечем"
        return empty
    ok = [t for t in toks if t[:STEM] in dst]
    conf = round(float(len(ok)) / len(toks), 2)
    if not ok or conf < MIN_CONF:
        empty["confidence"] = conf
        empty["reason"] = "name не подтверждён описанием (%d из %d слов)" % (len(ok), len(toks))
        return empty
    return {"value": n, "source": SOURCE_ACCOUNT_NAME, "confidence": conf,
            "reason": "name подтверждён описанием (%d из %d слов)" % (len(ok), len(toks))}


def pick_source(cands):
    """Чистая функция приоритета — вынесена, чтобы её можно было проверить тестом."""
    for s in ORDER:
        if cands.get(s):
            return cands[s], s
    return "", ""


def resolve_niche(account_id, db=None):
    own = db is None
    db = db or SessionLocal()
    try:
        row = db.execute(_T("""SELECT coalesce(company_niche,''), coalesce(name,''),
                                      coalesce(company_description,'')
                               FROM accounts WHERE account_id=:a"""), {"a": account_id}).fetchone()
        prof = _norm(row[0]) if row else ""
        acc_name = row[1] if row else ""
        acc_desc = row[2] if row else ""
        form = ""
        for (fd,) in db.execute(_T("""SELECT o.form_data::text FROM user_onboarding o
                                      JOIN users u ON u.id = o.user_id
                                      WHERE u.account_id = :a
                                      ORDER BY o.updated_at DESC NULLS LAST"""),
                                {"a": account_id}).fetchall():
            j = _parse(fd)
            v = _norm(j.get("company_niche") or j.get("niche"))
            if v:
                form = v
                break
        scen = ""
        for (idata,) in db.execute(_T("""SELECT input_data::text FROM business_scenarios
                                         WHERE account_id = :a
                                         ORDER BY updated_at DESC NULLS LAST, id DESC"""),
                                   {"a": account_id}).fetchall():
            j = _parse(idata)
            v = _norm(j.get("direction") or j.get("niche"))
            if v:
                scen = v
                break
        cands = {SOURCE_PROFILE: prof, SOURCE_FORM: form, SOURCE_SCENARIO: scen}
        cands = dict((k, ("" if is_junk(v) else v)) for k, v in cands.items())
        derived = derive_niche_from_profile(acc_name, acc_desc)
        cands[SOURCE_ACCOUNT_NAME] = derived["value"]
        conflict = bool(cands[SOURCE_FORM] and cands[SOURCE_SCENARIO]
                        and _key(cands[SOURCE_FORM]) != _key(cands[SOURCE_SCENARIO])
                        and not cands[SOURCE_PROFILE])
        value, source = pick_source(cands)
        conf = derived["confidence"] if source == SOURCE_ACCOUNT_NAME else (1.0 if source else 0.0)
        return {"niche": value, "source": source, "confidence": conf, "conflict": conflict,
                "candidates": cands, "synced": False,
                "status": "ok" if value else "needs_input",
                "reason": derived.get("reason", "") if not value or source == SOURCE_ACCOUNT_NAME else ""}
    finally:
        if own:
            db.close()


def business_niche(account_id, db=None):
    """Только чтение. Для read-путей (stage_of, do_for, collect_facts)."""
    return resolve_niche(account_id, db=db).get("niche") or ""


def ensure_profile_niche(account_id, db=None):
    """Перелив в профиль. Пишет ТОЛЬКО то, что ввёл клиент (form/scenario),
    только при пустом профиле, без конфликта и без мусора. Fallback НЕ пишется."""
    own = db is None
    db = db or SessionLocal()
    r = None
    try:
        r = resolve_niche(account_id, db=db)
        if (r["source"] not in WRITABLE_SOURCES or not r["niche"]
                or r["conflict"] or is_junk(r["niche"])):
            return r
        db.execute(_T("""UPDATE accounts SET company_niche = :n
                         WHERE account_id = :a AND coalesce(company_niche, '') = ''"""),
                   {"n": r["niche"], "a": account_id})
        db.commit()
        r["synced"] = True
        return r
    except Exception:
        try:
            db.rollback()
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=193)
        return r or {"niche": "", "source": "", "confidence": 0.0, "conflict": False,
                     "candidates": {}, "synced": False, "status": "needs_input", "reason": ""}
    finally:
        if own:
            db.close()
