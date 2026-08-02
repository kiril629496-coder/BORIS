"""База знаний клиента: карточки фактов, подтверждение, конфликты.

Правило: МОП имеет право пользоваться только тем, что подтвердил клиент
или что пришло из надёжного источника. Всё остальное — черновик.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text, bindparam

from app.db.session import SessionLocal

try:
    from app.api.auth import get_current_user
except ImportError:
    from app.auth import get_current_user

router = APIRouter(prefix="/api/memory", tags=["memory"])

TITLES = {
    "cena": "Цена", "usluga": "Услуга", "tovar": "Товар",
    "preimushchestvo": "Преимущество", "garantiya": "Гарантия",
    "faq": "Ответ клиенту", "vozrazhenie": "Возражение", "stil": "Стиль общения",
}
SOURCES = {
    "avito_item": "Объявление Авито", "dialog": "Переписка с клиентом",
    "dialog_price": "Переписка с клиентом", "client": "Указано клиентом",
}
USABLE = "(status = 'confirmed' OR (status = 'draft' AND confidence >= 80))"


def _now():
    return datetime.now(timezone.utc)


def _who(user):
    return str(getattr(user, "email", None) or getattr(user, "login", None)
               or getattr(user, "id", "") or "client")[:60]


STAGE = {
    1: "Найдено",
    2: "Требует подтверждения",
    3: "Подтверждено клиентом",
    4: "Используется BORIS",
}
USAGE = {
    "cena": "Ответы клиентам в переписке",
    "faq": "Ответы клиентам в переписке",
    "garantiya": "Ответы клиентам в переписке",
    "usluga": "Ответы клиентам в переписке",
    "tovar": "Ответы клиентам в переписке",
    "preimushchestvo": "Тексты объявлений и постов",
    "stil": "Тон и стиль ответов",
}
MOP_CATS = ("cena", "faq", "garantiya", "usluga", "tovar")


def _stage(r):
    if r.status == "confirmed":
        return 4 if r.category in MOP_CATS else 3
    if r.status == "conflict":
        return 2
    return 2 if (r.confidence or 0) >= 60 else 1


def _row(r, aliases=None):
    ref = str(r.source_ref or "")
    human = SOURCES.get(r.source_type, r.source_type or "")
    if ref.startswith("avito_item:"):
        human += " №" + ref.split(":", 1)[1]
    st = _stage(r)
    when = r.source_date or getattr(r, "extracted_at", None)
    return {
        "id": r.id,
        "stage": st,
        "stage_label": STAGE[st],
        "usage": USAGE.get(r.category, "Справочно"),
        "date": when.strftime("%d.%m.%Y") if when else "",
        "aliases": (aliases or {}).get(r.id, []),
        "type": TITLES.get(r.category, r.category),
        "category": r.category,
        "name": r.name,
        "value": r.value,
        "unit": r.unit or "",
        "source": human,
        "source_ref": ref,
        "source_date": r.source_date.isoformat() if r.source_date else "",
        "confidence": r.confidence,
        "status": r.status,
        "confirmed_by": r.confirmed_by or "",
        "snippet": r.snippet or "",
    }


class AccBody(BaseModel):
    account_id: str


class FactBody(BaseModel):
    account_id: str
    fact_id: int


class EditBody(BaseModel):
    account_id: str
    fact_id: int
    value: str


class ResolveBody(BaseModel):
    account_id: str
    fact_id: int = 0
    mode: str = "pick"
    value: str = ""


@router.get("/facts")
def facts(account_id: str, status: str = "", category: str = "",
          limit: int = 200, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        sql = ("SELECT * FROM client_facts WHERE account_id = :a"
               " AND status <> 'rejected'")
        p = {"a": account_id, "l": max(min(limit, 500), 1)}
        if status:
            sql += " AND status = :s"; p["s"] = status
        if category:
            sql += " AND category = :c"; p["c"] = category
        sql += (" ORDER BY (status='conflict') DESC, (status='draft') DESC,"
                " category, confidence DESC LIMIT :l")
        rows = db.execute(text(sql), p).all()
        al = {}
        for a in db.execute(text(
                "SELECT fact_id, alias FROM client_aliases WHERE account_id=:a"),
                {"a": account_id}).all():
            al.setdefault(a[0], []).append(a[1])
        return {"status": "ok", "facts": [_row(r, al) for r in rows]}
    finally:
        db.close()


@router.get("/summary")
def summary(account_id: str, user=Depends(get_current_user)):
    """Цифры для экрана «БОРИС изучил ваш бизнес»."""
    db = SessionLocal()
    try:
        def one(sql, **kw):
            kw["a"] = account_id
            return db.execute(text(sql), kw).scalar() or 0

        items = 0
        r = db.execute(text("SELECT value FROM storage WHERE account_id=:a"
                            " AND key='feed_items'"), {"a": account_id}).first()
        if r:
            try:
                import json
                d = json.loads(str(r[0]))
                if isinstance(d, dict):
                    d = d.get("items") or list(d.values())
                items = len(d)
            except Exception:
                items = 0
        dialogs = one("SELECT count(DISTINCT avito_chat_id) FROM messenger_messages"
                      " WHERE account_id=:a")
        total = one("SELECT count(*) FROM client_facts WHERE account_id=:a"
                    " AND status <> 'rejected'")
        confirmed = one("SELECT count(*) FROM client_facts WHERE account_id=:a"
                        " AND status='confirmed'")
        usable = one("SELECT count(*) FROM client_facts WHERE account_id=:a AND " + USABLE)
        conflicts = one("SELECT count(*) FROM client_facts WHERE account_id=:a"
                        " AND status='conflict'")
        answers = one("SELECT count(*) FROM client_facts WHERE account_id=:a"
                      " AND category='faq' AND status <> 'rejected'")
        stale = one("SELECT count(*) FROM client_facts WHERE account_id=:a"
                    " AND status='stale'")
        try:
            from client_memory_runner import coverage
            asked, strong, allf = coverage(db, account_id)
        except Exception:
            asked = strong = allf = 0
        return {"status": "ok", "items": items, "dialogs": dialogs,
                "facts": total, "confirmed": confirmed, "usable": usable,
                "conflicts": conflicts, "answers": answers, "stale": stale,
                "questions": asked, "covered_strong": strong, "covered_all": allf}
    finally:
        db.close()


@router.get("/conflicts")
def conflicts(account_id: str, user=Depends(get_current_user)):
    """Спорные цены: что сказал менеджер против того, что стоит в объявлениях."""
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT * FROM client_facts WHERE account_id=:a AND status='conflict'"
            " ORDER BY id"), {"a": account_id}).all()
        listed = [r[0] for r in db.execute(text(
            "SELECT DISTINCT value FROM client_facts WHERE account_id=:a"
            " AND category='cena' AND source_type='avito_item'"
            " AND status <> 'rejected' ORDER BY value"), {"a": account_id}).all()]
        return {"status": "ok", "listed_prices": listed,
                "conflicts": [_row(r) for r in rows]}
    finally:
        db.close()


def _confirm(db, account_id, fact_id, who, value=None):
    f = db.execute(text("SELECT * FROM client_facts WHERE id=:i AND account_id=:a"),
                   {"i": fact_id, "a": account_id}).first()
    if not f:
        raise HTTPException(status_code=404, detail="Факт не найден")
    note = f.snippet or ""
    if value is not None and str(value).strip() and str(value) != str(f.value):
        note = ("было: %s | %s" % (f.value, note))[:500]
    db.execute(text(
        "UPDATE client_facts SET status='confirmed', confidence=100,"
        " confirmed_by=:w, confirmed_at=:t, updated_at=:t,"
        " value = COALESCE(:v, value), snippet=:n WHERE id=:i"),
        {"i": fact_id, "w": who, "t": _now(),
         "v": (str(value) if value is not None and str(value).strip() else None),
         "n": note})
    return f


@router.post("/confirm")
def confirm(body: FactBody, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        _confirm(db, body.account_id, body.fact_id, _who(user))
        db.commit()
        return {"status": "ok", "message": "Факт подтверждён"}
    finally:
        db.close()


@router.post("/edit")
def edit(body: EditBody, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        _confirm(db, body.account_id, body.fact_id, _who(user), value=body.value)
        db.commit()
        return {"status": "ok", "message": "Факт исправлен и подтверждён"}
    finally:
        db.close()


@router.post("/reject")
def reject(body: FactBody, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        db.execute(text("UPDATE client_facts SET status='rejected', updated_at=:t,"
                        " confirmed_by=:w WHERE id=:i AND account_id=:a"),
                   {"i": body.fact_id, "a": body.account_id, "t": _now(),
                    "w": _who(user)})
        db.commit()
        return {"status": "ok", "message": "Факт удалён из базы знаний"}
    finally:
        db.close()


@router.post("/resolve")
def resolve(body: ResolveBody, user=Depends(get_current_user)):
    """Разбор конфликта цен: выбрать одну, указать свою или оставить диапазон."""
    db = SessionLocal()
    try:
        who = _who(user)
        if body.mode == "pick":
            f = _confirm(db, body.account_id, body.fact_id, who)
            db.execute(text(
                "UPDATE client_facts SET status='rejected', updated_at=:t WHERE account_id=:a"
                " AND status='conflict' AND id <> :i"),
                {"a": body.account_id, "i": body.fact_id, "t": _now()})
            msg = "Актуальной назначена цена %s" % f.value
        elif body.mode in ("new", "range"):
            val = str(body.value or "").strip()
            if not val:
                raise HTTPException(status_code=400, detail="Не указано значение")
            name = "Цена (уточнена клиентом)" if body.mode == "new" else "Диапазон цен"
            import hashlib
            h = hashlib.md5(("cena|%s|%s" % (name.lower(), val.lower())).encode()).hexdigest()
            db.execute(text(
                "INSERT INTO client_facts (account_id, category, name, value, source_type,"
                " source_ref, confidence, status, confirmed_by, confirmed_at, fact_hash)"
                " VALUES (:a,'cena',:n,:v,'client','client',100,'confirmed',:w,:t,:h)"
                " ON CONFLICT (account_id, fact_hash) DO UPDATE SET value=:v,"
                " status='confirmed', confidence=100, confirmed_by=:w, confirmed_at=:t"),
                {"a": body.account_id, "n": name, "v": val, "w": who, "t": _now(), "h": h})
            db.execute(text(
                "UPDATE client_facts SET status='rejected', updated_at=:t"
                " WHERE account_id=:a AND status='conflict'"),
                {"a": body.account_id, "t": _now()})
            msg = "Записано клиентом: %s" % val
        else:
            raise HTTPException(status_code=400, detail="Неизвестный режим")
        db.commit()
        return {"status": "ok", "message": msg}
    finally:
        db.close()


@router.post("/extract")
def extract(body: AccBody, user=Depends(get_current_user)):
    """Перечитать источники. Подтверждённые факты не трогаются."""
    db = SessionLocal()
    try:
        from client_memory_runner import extract_listings, extract_dialogs, mark_conflicts
        ni, nf = extract_listings(db, body.account_id)
        nc, npairs, nd = extract_dialogs(db, body.account_id)
        cf = mark_conflicts(db, body.account_id)
        return {"status": "ok", "items": ni, "facts_from_items": nf,
                "dialogs": nc, "pairs": npairs, "facts_from_dialogs": nd,
                "conflicts": cf,
                "message": "Изучено объявлений %d, диалогов %d" % (ni, nc)}
    finally:
        db.close()


@router.get("/top_questions")
def top_questions(account_id: str, limit: int = 10, user=Depends(get_current_user)):
    """Топ повторяющихся вопросов клиентов и есть ли на них готовый ответ."""
    import re as _re
    W = _re.compile(r"[а-яёa-z0-9]{4,}", _re.I)
    db = SessionLocal()
    try:
        qs = [str(r[0]) for r in db.execute(text(
            "SELECT text FROM messenger_messages m WHERE account_id=:a"
            " AND lower(direction) LIKE 'in%' AND length(text) > 15"
            " AND coalesce(msg_type,'') <> 'system'"
            " AND (item_owner_id IS NULL OR item_owner_id = (SELECT avito_user_id"
            "     FROM account_slots WHERE account_id = m.account_id LIMIT 1))"), {"a": account_id}).all()]
        groups = []
        _QW = ("?", "скольк", "как ", "какой", "какая", "какие", "почему", "когда",
               "где", "можно", "есть ли", "сделаете", "делаете", "цена", "стоит")
        for q in qs:
            ql = q.lower()
            if not any(k in ql for k in _QW):
                continue                      # не вопрос — в витрину не берём
            w = set(x.lower() for x in W.findall(q))
            if len(w) < 2:
                continue
            for g in groups:
                inter = len(w & g["words"])
                # словарь группы НЕ растёт: сравниваем с исходным вопросом-образцом,
                # и требуем существенное пересечение, а не два случайных слова
                if inter >= 2 and inter >= min(len(w), len(g["words"])) * 0.5:
                    g["count"] += 1
                    if len(q) < len(g["sample"]):
                        g["sample"] = q
                    break
            else:
                groups.append({"words": w, "count": 1, "sample": q})
        groups.sort(key=lambda g: -g["count"])
        facts = db.execute(text(
            "SELECT id, name, value, status FROM client_facts WHERE account_id=:a"
            " AND category='faq' AND status <> 'rejected'"), {"a": account_id}).all()
        out = []
        for g in groups[:max(min(limit, 30), 1)]:
            best, score = None, 0
            for f in facts:
                fw = set(x.lower() for x in W.findall("%s %s" % (f[1], f[2])))
                inter = len(g["words"] & fw)
                if inter > score:
                    best, score = f, inter
            out.append({
                "question": g["sample"][:180],
                "count": g["count"],
                "fact_id": best[0] if best and score >= 2 else 0,
                "answer": (best[2][:300] if best and score >= 2 else ""),
                "confirmed": bool(best and score >= 2 and best[3] == "confirmed"),
            })
        return {"status": "ok", "questions": out, "total_incoming": len(qs)}
    finally:
        db.close()


@router.get("/progress")
def progress(account_id: str, user=Depends(get_current_user)):
    """Было → стало: снимок до подтверждения против текущего состояния."""
    db = SessionLocal()
    try:
        base = db.execute(text(
            "SELECT facts, confirmed, usable, questions, covered_strong, covered_all,"
            " label, created_at FROM client_memory_snapshots WHERE account_id=:a"
            " ORDER BY created_at ASC LIMIT 1"), {"a": account_id}).first()
        try:
            from client_memory_runner import snapshot
            now = snapshot(db, account_id, "текущий")
        except Exception:
            now = {}
        was = None
        if base:
            was = {"facts": base[0], "confirmed": base[1], "usable": base[2],
                   "questions": base[3], "covered_strong": base[4],
                   "covered_all": base[5], "label": base[6],
                   "date": base[7].strftime("%d.%m.%Y") if base[7] else ""}
        return {"status": "ok", "was": was, "now": now}
    finally:
        db.close()


SCOPE_SQL = """
SELECT account_id FROM memory_scopes WHERE scope_key = (
    SELECT scope_key FROM memory_scopes WHERE account_id = :a)
"""


def scope_accounts(db, account_id, shared=True):
    """Аккаунты, чья память общая. Без группы — только сам аккаунт."""
    if not shared:
        return [account_id]
    try:
        rows = db.execute(text(SCOPE_SQL), {"a": account_id}).all()
        out = [r[0] for r in rows]
        return out or [account_id]
    except Exception:
        db.rollback()
        return [account_id]


CAT_WORDS = {
    "cena": "цена стоимость сколько стоит прайс",
    "garantiya": "гарантия гарантии",
    "usluga": "услуга услуги делаете",
    "tovar": "товар продаете",
    "preimushchestvo": "преимущество",
    "faq": "",
    "stil": "",
}


# Слова-пустышки: приветствия и вежливость есть почти в каждом сообщении.
# Если их считать, «Добрый день» совпадает со всем подряд и топит очередь.
STOPWORDS = {
    "добры", "доброе", "доброй", "здрав", "привет", "спаси", "пожал",
    "подск", "скажи", "хотел", "хотела", "нужно", "нужна", "нужен",
    "можно", "может", "будет", "буду", "есть", "меня", "зовут", "вопро",
    "утро", "вечер", "ночи", "день", "прив", "ответ", "напиш", "прошу",
}


def stems(s):
    """Огрубление до основы — падежи и числа перестают мешать совпадению."""
    import re as _re
    out = set()
    _short_ok = {"итр"}
    for w3 in _re.findall(r"\b[а-яёa-z0-9]{3}\b", str(s or ""), _re.I):
        if w3.lower() in _short_ok:
            out.add(w3.lower())
    for w in _re.findall(r"[а-яёa-z0-9]{4,}", str(s or ""), _re.I):
        w = w.lower()
        st = w[:5] if len(w) > 5 else w
        if st not in STOPWORDS:
            out.add(st)
    return out


def _words(s):
    return stems(s)


def find_answer(db, account_id, question, shared=True, min_overlap=2):
    """Единая точка: любой ИИ спрашивает знания только отсюда.

    Ничего не выдумывает: если подтверждённого факта нет —
    предлагает уточнить у владельца.
    """
    accs = scope_accounts(db, account_id, shared)
    qw = _words(question)
    if not qw:
        return {"found": False, "reason": "пустой вопрос", "accounts": accs}

    rows = db.execute(text(
        "SELECT f.id, f.account_id, f.category, f.name, f.value, f.source_type,"
        "       f.source_ref, f.confidence, f.status,"
        "       coalesce(string_agg(al.alias, ' '), '')"
        "  FROM client_facts f LEFT JOIN client_aliases al ON al.fact_id = f.id"
        " WHERE f.account_id IN :accs"
        "   AND (f.status = 'confirmed' OR (f.status = 'draft' AND f.confidence >= 80))"
        "   AND f.category <> 'rule'"
        " GROUP BY f.id"
    ).bindparams(bindparam("accs", expanding=True)), {"accs": accs}).all()

    # маркеры намерения вопроса: цена / покупка / изготовление / залог
    _price_q = bool(qw & stems(CAT_WORDS.get("cena", "")))
    _sale_q = bool(qw & {"прода", "купит", "покуп"})
    _make_q = bool(qw & {"изгот", "произв"})
    _dep_q = bool(qw & {"залог", "депоз", "обесп"})
    _avail_q = bool(qw & {"налич", "досту", "свобо", "остал", "имеет"}) or (
        bool(qw & {"сейча", "сегод"}) and not _price_q)
    cands = []
    best, best_score, best_raw = None, 0, 0
    for r in rows:
        # слова самого факта и отдельно слова-маркеры категории
        subj = len(qw & stems("%s %s %s" % (r[3], r[4], r[9])))
        cw = len(qw & stems(CAT_WORDS.get(r[2], "")))
        _fw = stems("%s %s" % (r[3], r[4]))
        # наличие подтверждает только менеджер: база на такой вопрос не отвечает
        if _avail_q:
            continue
        # факт про залог отвечает только на явный вопрос о залоге
        if "залог" in _fw and not _dep_q:
            continue
        # вопрос о покупке: арендные и залоговые факты не отвечают
        if _sale_q and ({"аренд", "залог"} & _fw):
            continue
        # вопрос о сроке изготовления: цена отвечает только своя, предметная
        if _make_q and r[2] == "cena" and not ({"изгот", "произв"} & _fw):
            continue
        # ценовой вопрос требует ценового факта
        if _price_q and r[2] != "cena":
            continue
        # маркер категории лишь уточняет намерение, но не заменяет
        # совпадения по сути: иначе «сколько стоит X» цепляет любую цену
        if not (subj >= min_overlap or (subj >= 1 and cw >= 1)):
            continue
        score = subj * 2 + cw + (1 if r[2] == "faq" else 0) + (1 if r[8] == "confirmed" else 0)
        cands.append((score, subj, r, len(qw & stems(r[3]))))

    if cands:
        _mx = max(c[0] for c in cands)
        _top = [c for c in cands if c[0] == _mx]
        # ничью не разрешаем порядком строк из БД: честнее переспросить.
        # шаг 1: предметное преимущество — больше совпадений с ИМЕНЕМ факта
        _name_hits = max(c[3] for c in _top)
        _top = [c for c in _top if c[3] == _name_hits]
        # шаг 2: одинаковое имя, но цена и услуга — это один предмет, не двусмысленность
        # шаг 2 применяется ТОЛЬКО к ценовому вопросу: без маркеров цены
        # отдавать число вместо описания услуги нельзя
        if len(_top) > 1 and _price_q:
            _nm_norm = {" ".join(str(c[2][3]).lower().split()) for c in _top}
            if len(_nm_norm) == 1:
                _cena = [c for c in _top if c[2][2] == "cena"]
                if len(_cena) == 1:
                    _top = _cena
        if len(_top) == 1:
            best, best_score, best_raw = _top[0][2], _top[0][0], _top[0][1]

    if not best:
        return {
            "found": False,
            "accounts": accs,
            "reply": "Уточню стоимость и условия у коллег и вернусь к вам.",
            "task": {"account_id": account_id,
                     "title": "Нужно уточнить: %s" % str(question)[:120],
                     "reason": "в базе знаний нет подтверждённого факта"},
        }
    return {
        "found": True, "fact_id": best[0], "account_id": best[1],
        "category": best[2], "name": best[3], "answer": best[4],
        "source": SOURCES.get(best[5], best[5]), "source_ref": best[6],
        "confidence": best[7], "confirmed": best[8] == "confirmed",
        "score": best_score, "matched_words": best_raw, "accounts": accs,
    }


class AskBody(BaseModel):
    account_id: str
    question: str
    shared: bool = True


@router.post("/ask")
def ask(body: AskBody, user=Depends(get_current_user)):
    """Спросить память. Этой ручкой ходят МОП, РОП и любой другой ИИ."""
    db = SessionLocal()
    try:
        return {"status": "ok", **find_answer(db, body.account_id, body.question, body.shared)}
    finally:
        db.close()


class ScopeBody(BaseModel):
    account_id: str
    scope_key: str
    accounts: list = []


@router.post("/scope")
def set_scope(body: ScopeBody, user=Depends(get_current_user)):
    """Объединить аккаунты клиента в один бизнес — тогда память общая."""
    if getattr(user, "role", "") != "owner":
        raise HTTPException(status_code=403, detail="Только владелец")
    db = SessionLocal()
    try:
        accs = [a for a in (body.accounts or [body.account_id]) if a]
        for a in accs:
            db.execute(text(
                "INSERT INTO memory_scopes (scope_key, account_id) VALUES (:k,:a)"
                " ON CONFLICT (account_id) DO UPDATE SET scope_key = :k"),
                {"k": body.scope_key, "a": a})
        db.commit()
        return {"status": "ok", "scope_key": body.scope_key, "accounts": accs,
                "message": "Память объединена для %d аккаунтов" % len(accs)}
    finally:
        db.close()


@router.get("/by_source")
def by_source(account_id: str, shared: bool = True, user=Depends(get_current_user)):
    """Разрез по источникам: сколько фактов, подтверждено, конфликтов."""
    db = SessionLocal()
    try:
        accs = scope_accounts(db, account_id, shared)
        rows = db.execute(text(
            "SELECT source_type, count(*),"
            " count(*) FILTER (WHERE status='confirmed'),"
            " count(*) FILTER (WHERE status='conflict')"
            "  FROM client_facts WHERE account_id IN :accs AND status <> 'rejected'"
            " GROUP BY 1 ORDER BY 2 DESC"
        ).bindparams(bindparam("accs", expanding=True)), {"accs": accs}).all()
        return {"status": "ok", "accounts": accs, "sources": [
            {"source": SOURCES.get(r[0], r[0]), "facts": r[1],
             "confirmed": r[2], "conflicts": r[3]} for r in rows]}
    finally:
        db.close()


MODES = ("off", "strict")          # hybrid добавим позже, когда решим по тарифам


def memory_mode(db, account_id):
    """Режим работы памяти для аккаунта. По умолчанию off — старая логика."""
    try:
        r = db.execute(text("SELECT mode FROM memory_modes WHERE account_id=:a"),
                       {"a": account_id}).first()
        return (r[0] if r else "off") or "off"
    except Exception:
        db.rollback()
        return "off"


def answer_for_ai(db, account_id, question, shared=True):
    """То, что зовёт МОП. Возвращает готовый ответ либо честное «уточню».

    off    — память не участвует, вызывающий работает как раньше
    strict — отвечаем только подтверждёнными знаниями
    """
    mode = memory_mode(db, account_id)
    if mode == "off":
        return {"mode": "off", "use_memory": False}
    r = find_answer(db, account_id, question, shared)
    r["mode"] = mode
    r["use_memory"] = True

    # Правила РОПа — только ПОДТВЕРЖДЁННЫЕ. Неподтверждённое правило
    # не управляет МОПом. По цене и характеристикам приоритет у факта:
    # правило добавляет оговорку, но не подменяет значение.
    try:
        rules = rules_for(db, account_id, shared, only_confirmed=True)
    except Exception as e:
        print("[memory] правила недоступны: %s" % str(e)[:120])
        rules = []
    qw = stems(question)
    applicable = [x for x in rules if len(qw & stems(x["rule"])) >= 1] or rules
    r["rules"] = applicable
    r["rule_ids"] = [x["id"] for x in applicable]
    if r.get("found") and r.get("category") in ("cena", "garantiya", "tovar", "usluga"):
        r["fact_wins"] = True
    return r


class ModeBody(BaseModel):
    account_id: str
    mode: str


@router.get("/mode")
def get_mode(account_id: str, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        return {"status": "ok", "account_id": account_id,
                "mode": memory_mode(db, account_id), "available": list(MODES)}
    finally:
        db.close()


@router.post("/mode")
def set_mode(body: ModeBody, user=Depends(get_current_user)):
    if getattr(user, "role", "") != "owner":
        raise HTTPException(status_code=403, detail="Только владелец")
    if body.mode not in MODES:
        raise HTTPException(status_code=400, detail="Режим должен быть off или strict")
    db = SessionLocal()
    try:
        db.execute(text(
            "INSERT INTO memory_modes (account_id, mode) VALUES (:a,:m)"
            " ON CONFLICT (account_id) DO UPDATE SET mode=:m, updated_at=now()"),
            {"a": body.account_id, "m": body.mode})
        db.commit()
        return {"status": "ok", "account_id": body.account_id, "mode": body.mode,
                "message": "Режим памяти: %s" % body.mode}
    finally:
        db.close()


# ======================= ПРАВИЛА РОПа КАК ФАКТЫ =======================
RULE_SCOPES = ("account", "business")


def add_rule(db, account_id, text_rule, source_ref="", scope="account",
             confidence=60, evidence=""):
    """Правило РОПа как факт: категория rule, источник rop.

    Неподтверждённое правило МОПу НЕДОСТУПНО — попадает в работу только
    после подтверждения владельцем (status='confirmed').
    """
    import hashlib
    rule = str(text_rule or "").strip()
    if not rule:
        return {"status": "error", "message": "пустая формулировка"}
    if scope not in RULE_SCOPES:
        scope = "account"
    h = hashlib.md5(("rule|%s|%s" % (scope, rule.lower())).encode()).hexdigest()
    try:
        r = db.execute(text(
            "INSERT INTO client_facts (account_id, category, name, value, scope,"
            " source_type, source_ref, source_date, confidence, status, fact_hash, snippet)"
            " VALUES (:a,'rule',:n,:v,:sc,'rop',:sr,now(),:cf,'draft',:h,:sn)"
            " ON CONFLICT (account_id, fact_hash) DO NOTHING"),
            {"a": account_id, "n": rule[:250], "v": rule[:2000], "sc": scope,
             "sr": str(source_ref)[:250], "cf": int(confidence),
             "h": h, "sn": str(evidence)[:500]})
        db.commit()
        added = int(getattr(r, "rowcount", 0) or 0)
        return {"status": "ok", "added": added, "scope": scope,
                "message": "Правило записано, ждёт подтверждения" if added
                           else "Такое правило уже есть"}
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)[:150]}


def rules_for(db, account_id, shared=True, only_confirmed=True):
    """Правила, доступные аккаунту.

    scope='account'  — только собственные правила этого аккаунта;
    scope='business' — правила всей группы, применяются ко всем её аккаунтам.
    """
    accs = scope_accounts(db, account_id, shared)
    sql = ("SELECT id, account_id, value, scope, confidence, status, source_ref,"
           " confirmed_by FROM client_facts WHERE category='rule'"
           "  AND status <> 'rejected' AND account_id IN :accs")
    if only_confirmed:
        sql += " AND status='confirmed'"
    rows = db.execute(text(sql).bindparams(bindparam("accs", expanding=True)),
                      {"accs": accs}).all()
    out = []
    for r in rows:
        sc = r[3] or "account"
        # правило чужого аккаунта применимо, только если оно business
        if r[1] != account_id and sc != "business":
            continue
        out.append({"id": r[0], "account_id": r[1], "rule": r[2], "scope": sc,
                    "confidence": r[4], "status": r[5], "source_ref": r[6] or "",
                    "confirmed_by": r[7] or ""})
    return out


def rule_conflicts(db, account_id, shared=True):
    """Правило против подтверждённого факта. Ничего не решаем автоматически."""
    accs = scope_accounts(db, account_id, shared)
    rules = rules_for(db, account_id, shared, only_confirmed=False)
    facts = db.execute(text(
        "SELECT id, category, name, value FROM client_facts"
        "  WHERE account_id IN :accs AND category IN ('cena','garantiya')"
        "    AND status='confirmed'"
    ).bindparams(bindparam("accs", expanding=True)), {"accs": accs}).all()
    out = []
    for rl in rules:
        rw = stems(rl["rule"])
        for f in facts:
            if len(rw & stems("%s %s" % (f[2], f[3]))) >= 2:
                out.append({"rule_id": rl["id"], "rule": rl["rule"],
                            "fact_id": f[0], "fact": "%s: %s" % (f[2], f[3]),
                            "category": f[1],
                            "note": "По цене и характеристикам приоритет у факта. "
                                    "Разрешите вручную."})
    return out


class RuleBody(BaseModel):
    account_id: str
    rule: str
    scope: str = "account"
    source_ref: str = ""
    evidence: str = ""


@router.post("/rules")
def create_rule(body: RuleBody, user=Depends(get_current_user)):
    """Создать правило. Зовётся РОПом после разбора переписки."""
    db = SessionLocal()
    try:
        return add_rule(db, body.account_id, body.rule, body.source_ref,
                        body.scope, evidence=body.evidence)
    finally:
        db.close()


@router.get("/rules")
def list_rules(account_id: str, shared: bool = True,
               only_confirmed: bool = False, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        return {"status": "ok",
                "rules": rules_for(db, account_id, shared, only_confirmed),
                "conflicts": rule_conflicts(db, account_id, shared)}
    finally:
        db.close()


# ==================== РЕЕСТР ИСТОЧНИКОВ ЗНАНИЙ ====================
SOURCE_STATUSES = ("connected", "queued", "processing", "ready",
                   "needs_attention", "failed", "disabled")

# Базовое доверие по типу источника. Подтверждение владельцем всегда 100.
SOURCE_TRUST = {
    "avito_item": 85, "site": 85, "price_list": 85, "contract": 85, "invoice": 85,
    "commercial_offer": 80, "estimate": 80,
    "dialog": 60, "dialog_price": 55, "dialog_promo": 40,
    "vk": 55, "telegram": 55,
    "2gis": 50, "yandex_maps": 50,
    "certificate_verified": 85,   # распознан номер и срок действия
    "certificate_image": 35,      # просто изображение без проверки
    "image": 30,                  # подтверждает вид, но не цену и не гарантию
    "model": 30,                  # вывод модели — всегда черновик
    "rop": 60, "client": 100,
}

# Что источник ВПРАВЕ утверждать. Фото не задаёт цену и гарантию.
SOURCE_ALLOWED = {
    "image": ("preimushchestvo",),
    "certificate_image": ("preimushchestvo",),
    "certificate_verified": ("garantiya", "preimushchestvo"),
    "2gis": ("kontakty", "geografiya", "usluga"),
    "yandex_maps": ("kontakty", "geografiya", "usluga"),
}

# Срок годности факта в днях. Просроченный НЕ удаляется — требует подтверждения.
FACT_TTL_DAYS = {
    "cena": 60, "akciya": 14, "kontakty": 180, "grafik": 180,
    "garantiya": 365, "usloviya": 365, "rekvizity": 730,
    "usluga": 730, "tovar": 730, "faq": 365, "rule": 365,
    "preimushchestvo": 730, "keys": 730, "stil": 730,
}


def source_trust(source_type, default=50):
    return SOURCE_TRUST.get(source_type, default)


def source_may_claim(source_type, category):
    """Может ли источник такого типа утверждать факт этой категории."""
    allowed = SOURCE_ALLOWED.get(source_type)
    return True if allowed is None else category in allowed


def register_source(db, account_id, source_type, title, url="",
                    external_id="", scope="account", owner_user_id=None,
                    added_by="system", status="connected"):
    """Создать или обновить источник. Секреты и токены сюда НЕ пишем."""
    try:
        r = db.execute(text(
            "INSERT INTO client_sources (owner_user_id, account_id, scope, source_type,"
            " title, url, external_id, status, added_by)"
            " VALUES (:o,:a,:sc,:st,:t,:u,:e,:s,:b)"
            " ON CONFLICT (account_id, source_type, external_id)"
            " DO UPDATE SET title=:t, url=:u, updated_at=now() RETURNING id"),
            {"o": owner_user_id, "a": account_id, "sc": scope, "st": source_type,
             "t": str(title)[:250], "u": str(url)[:500],
             "e": str(external_id)[:128], "s": status, "b": str(added_by)[:64]})
        sid = r.scalar()
        db.commit()
        return sid
    except Exception as e:
        db.rollback()
        print("[sources] не записан %s/%s: %s" % (account_id, source_type, str(e)[:120]))
        return None


def update_source_stats(db, source_id, facts=0, new_facts=0, conflicts=0,
                        status=None, error=""):
    """Итоги разбора. Частичный сбой не стирает уже полученное."""
    sets = ["facts_total = :f", "facts_new = :n", "conflicts = :c",
            "last_parsed_at = now()", "updated_at = now()", "last_error = :e"]
    p = {"i": source_id, "f": int(facts), "n": int(new_facts),
         "c": int(conflicts), "e": str(error)[:500]}
    if status:
        sets.append("status = :s"); p["s"] = status
    try:
        db.execute(text("UPDATE client_sources SET " + ", ".join(sets) + " WHERE id = :i"), p)
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        print("[sources] статистика не обновлена: %s" % str(e)[:120])
        return False


def sync_existing_sources(db, account_id):
    """Зарегистрировать уже существующие источники БЕЗ повторного извлечения.

    Факты не трогаем — только описываем, откуда они взялись, и подтягиваем
    накопленную статистику из client_facts.
    """
    made = []
    # объявления Авито
    r = db.execute(text("SELECT value FROM storage WHERE account_id=:a AND key='feed_items'"),
                   {"a": account_id}).first()
    if r:
        n = 0
        try:
            import json as _j
            d = _j.loads(str(r[0]))
            if isinstance(d, dict):
                d = d.get("items") or list(d.values())
            n = len(d)
        except Exception:
            n = 0
        sid = register_source(db, account_id, "avito_item",
                              "Объявления Авито (%d шт.)" % n,
                              external_id="feed_items", status="ready")
        if sid:
            made.append(("avito_item", sid))
    # переписки Авито
    cnt = db.execute(text("SELECT count(DISTINCT avito_chat_id) FROM messenger_messages"
                          " WHERE account_id=:a"), {"a": account_id}).scalar() or 0
    if cnt:
        sid = register_source(db, account_id, "dialog",
                              "Переписки Авито (%d диалогов)" % cnt,
                              external_id="messenger", status="ready")
        if sid:
            made.append(("dialog", sid))

    # подтянуть статистику по уже извлечённым фактам
    for stype, sid in made:
        types = ("avito_item",) if stype == "avito_item" else ("dialog", "dialog_price", "dialog_promo")
        row = db.execute(text(
            "SELECT count(*), count(*) FILTER (WHERE status='confirmed'),"
            "       count(*) FILTER (WHERE status='conflict')"
            "  FROM client_facts WHERE account_id=:a AND source_type IN :t"
            "   AND status <> 'rejected'"
        ).bindparams(bindparam("t", expanding=True)),
            {"a": account_id, "t": list(types)}).first()
        update_source_stats(db, sid, facts=row[0] or 0, new_facts=row[1] or 0,
                            conflicts=row[2] or 0, status="ready")
    return made


def mark_stale_facts(db, account_id, shared=True):
    """Просроченные факты не удаляем — переводим в stale, из strict они выпадают."""
    accs = scope_accounts(db, account_id, shared)
    total = 0
    for cat, days in FACT_TTL_DAYS.items():
        r = db.execute(text(
            "UPDATE client_facts SET status='stale', updated_at=now()"
            "  WHERE account_id IN :accs AND category=:c AND status='draft'"
            "    AND coalesce(source_date, extracted_at) < now() - (:d || ' days')::interval"
        ).bindparams(bindparam("accs", expanding=True)),
            {"accs": accs, "c": cat, "d": str(days)})
        total += int(getattr(r, "rowcount", 0) or 0)
    db.commit()
    return total


class SyncBody(BaseModel):
    account_id: str


@router.post("/sources/sync")
def sources_sync(body: SyncBody, user=Depends(get_current_user)):
    """Описать уже имеющиеся источники. Извлечение не запускается."""
    db = SessionLocal()
    try:
        made = sync_existing_sources(db, body.account_id)
        stale = mark_stale_facts(db, body.account_id)
        return {"status": "ok", "registered": [m[0] for m in made],
                "stale_marked": stale,
                "message": "Зарегистрировано источников: %d" % len(made)}
    finally:
        db.close()


@router.get("/sources")
def sources_list(account_id: str, shared: bool = True, user=Depends(get_current_user)):
    """Центр обучения: источники, статусы, очередь, ошибки, рекомендации."""
    db = SessionLocal()
    try:
        accs = scope_accounts(db, account_id, shared)
        rows = db.execute(text(
            "SELECT id, account_id, source_type, title, url, status, last_parsed_at,"
            "       facts_total, facts_new, conflicts, last_error, scope"
            "  FROM client_sources WHERE account_id IN :accs ORDER BY source_type"
        ).bindparams(bindparam("accs", expanding=True)), {"accs": accs}).all()
        srcs = [{
            "id": r[0], "account_id": r[1], "source_type": r[2],
            "title": r[3] or r[2], "url": r[4] or "", "status": r[5],
            "last_parsed_at": r[6].strftime("%d.%m.%Y %H:%M") if r[6] else "",
            "facts": r[7], "confirmed": r[8], "conflicts": r[9],
            "error": r[10] or "", "scope": r[11] or "account",
            "trust": source_trust(r[2]),
        } for r in rows]

        q = db.execute(text(
            "SELECT count(*) FILTER (WHERE status IN ('queued','processing'))"
            "  FROM client_sources WHERE account_id IN :accs"
        ).bindparams(bindparam("accs", expanding=True)), {"accs": accs}).scalar() or 0

        have = {r[2] for r in rows}
        advice = [
            {"type": t, "title": n, "why": w} for t, n, w in (
                ("site", "Сайт компании", "самый богатый источник услуг, цен и преимуществ"),
                ("price_list", "Прайс-лист", "самые достоверные цены, разбирается без ИИ"),
                ("commercial_offer", "Коммерческие предложения", "готовые формулировки для ответов"),
                ("vk", "ВКонтакте", "акции и отзывы"),
                ("telegram", "Telegram", "оперативные объявления"),
                ("2gis", "2ГИС", "контакты и география"),
            ) if t not in have
        ]
        return {"status": "ok", "sources": srcs, "in_queue": q,
                "recommend": advice[:4]}
    finally:
        db.close()


# Вес категории в очереди подтверждения: что сильнее влияет на ответы МОПа
CAT_WEIGHT = {"cena": 40, "garantiya": 30, "usloviya": 30, "faq": 25,
              "rule": 25, "usluga": 20, "tovar": 20, "akciya": 20,
              "kontakty": 15, "preimushchestvo": 10, "stil": 5}


def confirm_queue(db, account_id, shared=True, limit=12):
    """Что подтверждать первым: не по алфавиту, а по влиянию на ответы.

    Учитываем частоту похожих вопросов клиентов, категорию, конфликт,
    свежесть и текущую достоверность.
    """
    accs = scope_accounts(db, account_id, shared)
    questions = [str(r[0]) for r in db.execute(text(
        "SELECT text FROM messenger_messages WHERE account_id IN :accs"
        "  AND lower(direction) LIKE 'in%' AND length(text) > 15"
    ).bindparams(bindparam("accs", expanding=True)), {"accs": accs}).all()]
    qwords = [stems(q) for q in questions]

    rows = db.execute(text(
        "SELECT id, account_id, category, name, value, source_type, confidence,"
        "       status, coalesce(source_date, extracted_at)"
        "  FROM client_facts WHERE account_id IN :accs"
        "   AND status IN ('draft','conflict','stale')"
    ).bindparams(bindparam("accs", expanding=True)), {"accs": accs}).all()

    scored = []
    for r in rows:
        fw = stems("%s %s" % (r[3], r[4]))
        # три общих значимых слова, иначе длинные тексты цепляют всё подряд
        need = 3 if len(fw) > 12 else 2
        asked = sum(1 for q in qwords if len(q & fw) >= need)
        score = CAT_WEIGHT.get(r[2], 10)
        score += asked * 12                       # чаще спрашивают — важнее
        if r[7] == "conflict":
            score += 35                           # спорное чинить в первую очередь
        if r[7] == "stale":
            score += 15                           # устаревшее подтвердить заново
        score += max(0, (r[6] or 0) - 50) // 10   # ближе к порогу — дешевле поднять
        scored.append((score, asked, r))
    scored.sort(key=lambda x: -x[0])

    top = scored[:max(min(limit, 50), 1)]
    # сколько вопросов закроется, если подтвердить эти факты
    strong_now = 0
    pool_now = [stems("%s %s" % (r[3], r[4])) for _, _, r in []]
    covered_after = set()
    for _, _, r in top:
        fw = stems("%s %s" % (r[3], r[4]))
        for i, q in enumerate(qwords):
            if len(q & fw) >= 2:
                covered_after.add(i)
    gain = round(100.0 * len(covered_after) / len(qwords)) if qwords else 0

    return {
        "items": [{
            "id": r[0], "account_id": r[1], "category": r[2],
            "type": TITLES.get(r[2], r[2]), "name": r[3], "value": r[4],
            "source": SOURCES.get(r[5], r[5]), "confidence": r[6],
            "status": r[7], "asked": asked, "score": sc,
            "date": r[8].strftime("%d.%m.%Y") if r[8] else "",
            "why": ("спорное значение" if r[7] == "conflict" else
                    "устарело" if r[7] == "stale" else
                    ("клиенты спрашивали %d раз" % asked) if asked else
                    "важная категория"),
        } for sc, asked, r in top],
        "questions_total": len(questions),
        "gain_pct": gain,
        "hint": ("Подтвердите эти %d фактов — BORIS сможет отвечать ещё "
                 "на %d%% типовых вопросов." % (len(top), gain)) if top else
                "Всё подтверждено.",
    }


@router.get("/confirm_queue")
def confirm_queue_api(account_id: str, shared: bool = True, limit: int = 12,
                      user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        return {"status": "ok", **confirm_queue(db, account_id, shared, limit)}
    finally:
        db.close()


# ==================== РУЧНОЙ ВВОД ЗНАНИЙ ВЛАДЕЛЬЦЕМ ====================
# Факт, внесённый владельцем: confidence=100, status=confirmed, source_type='client'.
# fact_hash считается ТОЙ ЖЕ формулой, что в client_memory_runner.fhash —
# поэтому повторный ввод обновляет факт, а не создаёт дубль.
import hashlib as _hl_m
import re as _re_m

MANUAL_CATEGORIES = ("usluga", "tovar", "cena", "preimushchestvo",
                     "garantiya", "faq", "stil", "keys", "rule")
_WS_M = _re_m.compile(r"[ \t\u00a0]+")
_PRICE_M = _re_m.compile(r"(\d[\d\s\u00a0]{1,})\s*(?:₽|руб|р\.|рублей)?", _re_m.I)


def _norm_m(s):
    return _WS_M.sub(" ", str(s or "").strip().lower())


def _fhash_m(cat, name, value):
    return _hl_m.md5(("%s|%s|%s" % (cat, _norm_m(name), _norm_m(value))).encode()).hexdigest()


def _conflicts_for(db, account_id, category, name, value):
    """Существующие факты той же категории с тем же названием, но другим значением."""
    rows = db.execute(text(
        "SELECT id, value, status, confidence, source_type FROM client_facts"
        " WHERE account_id=:a AND category=:c AND lower(btrim(name))=:n"
        "   AND status <> 'rejected' AND lower(btrim(value)) <> :v"),
        {"a": account_id, "c": category, "n": _norm_m(name), "v": _norm_m(value)}).all()
    return [{"id": r[0], "value": r[1], "status": r[2],
             "confidence": r[3], "source": r[4]} for r in rows]


OCCASION_WORDS = ("юбилей", "юбиле", "свадьб", "день рожден", "днюх", "новый год",
                  "новогодн", "рождеств", "8 март", "23 фев", "годовщин", "выпускн",
                  "корпоратив", "8 марта", "днём рожден", "днем рожден")


def _word_forms(name):
    """Падежные формы для КОРОТКИХ названий. Огрубление режет слово до 5 букв,
    поэтому «тост» и «тоста» не сводятся — добавляем формы явно."""
    n = _norm_m(name)
    if " " in n or len(n) > 6 or len(n) < 3:
        return []
    if n.endswith("я"):        # песня -> песни, песне, песню, песней
        b = n[:-1]; out = {b + "и", b + "е", b + "ю", b + "ей"}
    elif n.endswith("а"):      # книга -> книги, книге, книгу, книгой
        b = n[:-1]; out = {b + "и", b + "е", b + "у", b + "ой"}
    elif n.endswith("ь"):
        b = n[:-1]; out = {b + "я", b + "ю", b + "ем", b + "е"}
    else:                      # тост -> тоста, тосту, тостом, тосте, тосты
        out = {n + "а", n + "у", n + "ом", n + "е", n + "ы"}
    return sorted(out - {n})


def _other_service_names(db, account_id, own_name):
    """Названия ДРУГИХ услуг/товаров этого аккаунта — алиас не должен их содержать."""
    rows = db.execute(text(
        "SELECT DISTINCT lower(btrim(name)) FROM client_facts"
        " WHERE account_id=:a AND status <> 'rejected' AND length(btrim(name)) >= 4"
        "   AND lower(btrim(name)) <> :n"), {"a": account_id, "n": _norm_m(own_name)}).all()
    return [r[0] for r in rows if r[0]]


def _prep_aliases(name, aliases, others=None):
    """Правила алиасов (закреплены 01.08):
    1) повод (юбилей/свадьба/др) — не алиас услуги;
    2) коротким названиям добавляются падежные формы;
    3) нормализация: нижний регистр, схлопывание пробелов, дедуп,
       алиас, равный названию, не сохраняется.
    Возвращает (готовые, отсеянные_поводы)."""
    base = _norm_m(name)
    good, dropped, seen = [], [], {base}
    for al in (aliases or []):
        a = _norm_m(al)
        if not a or a in seen:
            continue
        if any(w in a for w in OCCASION_WORDS):
            dropped.append(str(al).strip())
            continue
        if any(o in a for o in (others or [])):
            dropped.append(str(al).strip())
            continue
        seen.add(a); good.append(a)
    for f in _word_forms(name):
        if f not in seen:
            seen.add(f); good.append(f)
    return good, dropped


def _save_manual_fact(db, account_id, who, category, name, value, unit="",
                      valid_until=None, scope="account", aliases=None, snippet=""):
    h = _fhash_m(category, name, value)
    db.execute(text(
        "INSERT INTO client_facts (account_id, category, name, value, unit, source_type,"
        " source_ref, source_date, confidence, status, confirmed_by, confirmed_at,"
        " fact_hash, snippet, scope, valid_until, extracted_at, updated_at)"
        " VALUES (:a,:c,:n,:v,:u,'client','manual',:t,100,'confirmed',:w,:t,:h,:s,:sc,:vu,:t,:t)"
        " ON CONFLICT (account_id, fact_hash) DO UPDATE SET value=:v, unit=:u,"
        " status='confirmed', confidence=100, confirmed_by=:w, confirmed_at=:t,"
        " source_type='client', scope=:sc, valid_until=:vu, updated_at=:t"),
        {"a": account_id, "c": category, "n": name.strip(), "v": str(value).strip(),
         "u": unit or "", "w": who, "t": _now(), "h": h, "s": snippet or "",
         "sc": scope if scope in ("account", "business") else "account",
         "vu": valid_until})
    fid = db.execute(text(
        "SELECT id FROM client_facts WHERE account_id=:a AND fact_hash=:h"),
        {"a": account_id, "h": h}).scalar()
    _good, _ = _prep_aliases(name, aliases, _other_service_names(db, account_id, name))
    for al in _good:
        try:
            db.execute(text(
                "INSERT INTO client_aliases (account_id, fact_id, alias, source_type)"
                " VALUES (:a,:f,:al,'client') ON CONFLICT DO NOTHING"),
                {"a": account_id, "f": fid, "al": al})
        except Exception:
            pass
    return fid


class ManualFact(BaseModel):
    account_id: str
    category: str = "usluga"
    name: str
    value: str = ""
    price: str = ""
    unit: str = ""
    valid_until: str = ""       # YYYY-MM-DD, пусто = бессрочно
    scope: str = "account"      # account | business
    aliases: list = []
    force: bool = False         # True = записать несмотря на конфликт


@router.post("/fact_add")
def fact_add(body: ManualFact, user=Depends(get_current_user)):
    """Добавить знание вручную. Владелец = источник правды: confidence 100, confirmed."""
    if body.category not in MANUAL_CATEGORIES:
        return {"status": "error", "message": "Неизвестная категория: %s" % body.category}
    name = (body.name or "").strip()
    if not name:
        return {"status": "error", "message": "Не указано название или вопрос"}
    value = (body.value or "").strip()
    price = str(body.price or "").strip()
    if not value and not price:
        return {"status": "error", "message": "Не указано значение или цена"}

    who = getattr(user, "email", None) or str(getattr(user, "id", "owner"))
    vu = None
    if (body.valid_until or "").strip():
        try:
            import datetime as _dt_m
            vu = _dt_m.datetime.strptime(body.valid_until[:10], "%Y-%m-%d")
        except Exception:
            return {"status": "error", "message": "Срок действия: ожидается ГГГГ-ММ-ДД"}

    db = SessionLocal()
    try:
        planned = []
        if value:
            planned.append((body.category, name, value, body.unit))
        if price:
            planned.append(("cena", name, price, body.unit or "₽"))

        conflicts = []
        for cat, nm, val, _u in planned:
            conflicts += [dict(c, category=cat, name=nm, new_value=val)
                          for c in _conflicts_for(db, body.account_id, cat, nm, val)]
        if conflicts and not body.force:
            return {"status": "conflict",
                    "message": "Уже есть другое значение — подтвердите замену",
                    "conflicts": conflicts}

        ids = []
        for cat, nm, val, u in planned:
            ids.append(_save_manual_fact(db, body.account_id, who, cat, nm, val, u,
                                         vu, body.scope, body.aliases))
        # при force старые расходящиеся значения уводим в rejected
        if conflicts and body.force:
            old = [c["id"] for c in conflicts if c["id"] not in ids]
            if old:
                db.execute(text(
                    "UPDATE client_facts SET status='rejected', updated_at=:t"
                    " WHERE id = ANY(:ids)"), {"ids": old, "t": _now()})
        db.commit()
        _, _dropped = _prep_aliases(name, body.aliases)
        res = {"status": "ok", "fact_ids": ids,
               "replaced": len(conflicts) if body.force else 0}
        if _dropped:
            res["warning"] = ("Не сохранены как алиасы (это поводы, а не названия услуг): "
                              + ", ".join(_dropped))
        return res
    finally:
        db.close()


class BulkFacts(BaseModel):
    account_id: str
    text: str = ""              # построчно: «Стих на юбилей — 3500 ₽»
    category: str = "usluga"
    scope: str = "account"
    preview: bool = True        # сначала показать разбор, писать только по подтверждению
    force: bool = False


def _parse_bulk_line(line):
    """«Название — 3500 ₽ | алиас1, алиас2» → (name, price, aliases).
    Разделители: — – - ; = таб. Без модели, чистая регулярка."""
    raw = str(line or "").strip()
    if not raw or raw.startswith("#"):
        return None
    aliases = []
    if "|" in raw:
        raw, al = raw.split("|", 1)
        aliases = [a.strip() for a in al.replace(";", ",").split(",") if a.strip()]
        raw = raw.strip()
    parts = _re_m.split(r"\s+[—–-]\s+|\t+|\s*;\s*", raw, maxsplit=1)
    name = parts[0].strip()
    rest = parts[1].strip() if len(parts) > 1 else ""
    price = ""
    m = _PRICE_M.search(rest or name)
    if m:
        digits = _re_m.sub(r"\D", "", m.group(1))
        if digits and 10 <= int(digits) <= 100000000:
            price = digits
    if price and not rest:
        name = _PRICE_M.sub("", name).strip(" -—–;:")
    value = rest if (rest and not price) else ""
    if not name:
        return None
    return {"name": name, "price": price, "value": value, "aliases": aliases}


@router.post("/fact_bulk")
def fact_bulk(body: BulkFacts, user=Depends(get_current_user)):
    """Массовый ввод: вставка текста построчно. preview=true — только разбор, без записи."""
    if body.category not in MANUAL_CATEGORIES:
        return {"status": "error", "message": "Неизвестная категория"}
    parsed, bad = [], []
    for line in (body.text or "").splitlines():
        p = _parse_bulk_line(line)
        if p:
            parsed.append(p)
        elif line.strip():
            bad.append(line.strip()[:80])
    if not parsed:
        return {"status": "error", "message": "Не удалось разобрать ни одной строки",
                "skipped": bad}

    who = getattr(user, "email", None) or str(getattr(user, "id", "owner"))
    db = SessionLocal()
    try:
        for p in parsed:
            p["conflicts"] = _conflicts_for(db, body.account_id, "cena", p["name"], p["price"]) \
                if p["price"] else []
        if body.preview:
            return {"status": "preview", "parsed": parsed, "skipped": bad,
                    "total": len(parsed),
                    "with_conflicts": sum(1 for p in parsed if p["conflicts"])}
        written = 0
        for p in parsed:
            if p["conflicts"] and not body.force:
                continue
            if p["value"]:
                _save_manual_fact(db, body.account_id, who, body.category, p["name"],
                                  p["value"], "", None, body.scope, p["aliases"])
            if p["price"]:
                _save_manual_fact(db, body.account_id, who, "cena", p["name"],
                                  p["price"], "₽", None, body.scope, p["aliases"])
            written += 1
        db.commit()
        return {"status": "ok", "written": written, "skipped": bad,
                "blocked_by_conflict": sum(1 for p in parsed if p["conflicts"]) if not body.force else 0}
    finally:
        db.close()
