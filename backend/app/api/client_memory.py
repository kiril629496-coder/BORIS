"""База знаний клиента: карточки фактов, подтверждение, конфликты.

Правило: МОП имеет право пользоваться только тем, что подтвердил клиент
или что пришло из надёжного источника. Всё остальное — черновик.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, File, UploadFile
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
    "company": "Профиль компании", "kontakty": "Контакты", "grafik": "График",
    "geografiya": "Адрес / география", "usloviya": "Условия", "akciya": "Акция",
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
MOP_CATS = ("cena", "faq", "garantiya", "usluga", "tovar", "kontakty", "grafik", "geografiya", "usloviya", "akciya")


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
        "requires_confirmation": str(r.status or "") != "confirmed",
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



# BORIS_MEM_ACCOUNT_GUARD: account_id у этих ручек приходит В ТЕЛЕ запроса, а
# check_account_access читает только путь, query и форму - поэтому проверки
# доступа не было вовсе. Владение аккаунтом либо связь в user_account_access.
def _mem_allowed(account_id, user):
    from sqlalchemy import text as _t
    if not account_id:
        return False
    uid = getattr(user, "id", 0)
    if getattr(user, "role", "") == "owner":
        return True
    db = SessionLocal()
    try:
        if db.execute(_t("SELECT 1 FROM accounts WHERE account_id=:a AND owner_user_id=:u"),
                      {"a": account_id, "u": uid}).fetchone():
            return True
        return bool(db.execute(_t(
            "SELECT 1 FROM user_account_access WHERE user_id=:u AND account_id=:a"
            " AND can_view = TRUE"), {"u": uid, "a": account_id}).fetchone())
    finally:
        db.close()


KB_FILE_MAX_MB = 15


@router.post("/upload_file")
async def upload_knowledge_file(account_id: str, file: UploadFile = File(...),
                                preview: bool = False, keep: str = "",
                                user=Depends(get_current_user)):
    """Принимает PDF / Word / Excel / текст и сам кладёт содержимое в знания.

    account_id ИМЕННО в адресе запроса: общая проверка доступа читает его из
    query и иначе отказывает ещё до входа в функцию."""
    from app.services.file_knowledge import extract_text, to_facts
    if not _mem_allowed(account_id, user):
        return {"status": "error", "message": "Аккаунт недоступен"}
    data = await file.read()
    if not data:
        return {"status": "error", "message": "Файл пустой"}
    if len(data) > KB_FILE_MAX_MB * 1024 * 1024:
        return {"status": "error",
                "message": "Файл больше %s МБ" % KB_FILE_MAX_MB}
    try:
        raw, kind = extract_text(file.filename or "", data)
    except Exception as e:
        return {"status": "error",
                "message": "Не удалось прочитать файл: %s" % str(e)[:90]}
    if kind == "unsupported":
        return {"status": "error",
                "message": "Поддерживаются PDF, Word, Excel и текстовые файлы"}
    if not (raw or "").strip():
        return {"status": "error",
                "message": "В файле не нашлось текста. Если это скан или фотография, "
                           "текст из неё пока не распознаётся"}
    facts = to_facts(raw, file.filename or "файл")
    if preview and facts:
        return {"status": "ok", "preview": True, "file": file.filename,
                "facts": [{"i": _i, "category": _f["category"], "name": _f["name"],
                           "value": _f["value"][:600]} for _i, _f in enumerate(facts)],
                "message": "Проверьте, что запомнить"}
    if keep:
        _idx = set(int(_x) for _x in keep.split(",") if _x.strip().isdigit())
        facts = [_f for _i, _f in enumerate(facts) if _i in _idx]
    if not facts:
        return {"status": "error", "message": "Текста слишком мало для знаний"}
    who = getattr(user, "email", None) or "клиент"
    db = SessionLocal()
    saved = 0
    try:
        for f in facts:
            try:
                _save_manual_fact(db, account_id, who, f["category"], f["name"],
                                  f["value"], snippet=f["snippet"])
                saved += 1
            except Exception as fe:
                print("[kb_file] факт не сохранён: %s" % str(fe)[:120], flush=True)
        db.commit()
    finally:
        db.close()
    try:
        from app.services.action_log import log_action, ACTOR_USER
        log_action(account_id=account_id, action="Изучил файл клиента",
                   object_kind="знания", object_name=(file.filename or "файл")[:200],
                   after_val="добавлено фактов: %s" % saved,
                   reason="файл загружен в информацию о компании",
                   actor=ACTOR_USER, user_id=getattr(user, "id", None),
                   source="memory.upload_file")
    except Exception:
        pass
    return {"status": "ok", "facts": saved, "file": file.filename,
            "message": "Борис прочитал файл и запомнил %s фрагментов" % saved}


@router.post("/confirm")
def confirm(body: FactBody, user=Depends(get_current_user)):
    if not _mem_allowed(body.account_id, user):
        return {"status": "error", "message": "Аккаунт недоступен"}
    db = SessionLocal()
    try:
        _confirm(db, body.account_id, body.fact_id, _who(user))
        db.commit()
        return {"status": "ok", "message": "Факт подтверждён"}
    finally:
        db.close()


@router.post("/edit")
def edit(body: EditBody, user=Depends(get_current_user)):
    if not _mem_allowed(body.account_id, user):
        return {"status": "error", "message": "Аккаунт недоступен"}
    db = SessionLocal()
    try:
        _confirm(db, body.account_id, body.fact_id, _who(user), value=body.value)
        db.commit()
        return {"status": "ok", "message": "Факт исправлен и подтверждён"}
    finally:
        db.close()


@router.post("/reject")
def reject(body: FactBody, user=Depends(get_current_user)):
    if not _mem_allowed(body.account_id, user):
        return {"status": "error", "message": "Аккаунт недоступен"}
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
    "usluga": "услуга услуги делаете оказываете сдаете аренда арендуете",
    "tovar": "товар товары продаете продажа купить покупка",
    "preimushchestvo": "преимущество",
    "kontakty": "контакт контакты телефон номер почта email связаться связь",
    "grafik": "график режим часы работает открыто время работы",
    "geografiya": (
        "адрес база базы где находитесь местоположение самовывоз "
        "офис офисе офисы салон салоны шоурум шоурумы приехать подъехать "
        "приехать посетить посещение встреча встретиться"
    ),
    "usloviya": "условия оформление оплата договор",
    "akciya": "акция акции акционный акционные спецпредложение специальные условия",
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
        "       coalesce(string_agg(al.alias, ' '), ''),"
        "       max(f.source_date), max(f.extracted_at)"
        "  FROM client_facts f LEFT JOIN client_aliases al ON al.fact_id = f.id"
        " WHERE f.account_id IN :accs"
        "   AND (f.status = 'confirmed' OR (f.status = 'draft' AND f.confidence >= 80))"
        "   AND f.category NOT IN ('rule', 'keys', 'rule_marketing')"
        "   AND (COALESCE(f.scope, 'business') = 'business'"
        "        OR f.account_id = :self)"
        " GROUP BY f.id"
    ).bindparams(bindparam("accs", expanding=True)),
        {"accs": accs, "self": account_id}).all()

    # маркеры намерения вопроса: цена / покупка / изготовление / залог
    _price_q = bool(qw & stems(CAT_WORDS.get("cena", "")))
    _direct_memory_categories = [cat for cat in ("kontakty", "grafik", "geografiya", "akciya")
                                 if qw & stems(CAT_WORDS.get(cat, ""))]
    _direct_memory_category = _direct_memory_categories[0] if len(_direct_memory_categories) == 1 else None
    _q_low = str(question or "").lower().replace("ё", "е")
    _sale_q = bool(qw & {"прода", "купит", "покуп"})
    _sale_product_price_q = bool(_price_q and any(x in _q_low for x in (
        "под ключ", "каркас", "стандартн", "усиленн", "оргалит", "двпо", "osb",
        "вагонк", "новая бытов", "новую бытов", "новой бытов", "производств"
    )))
    _used_sale_price_q = bool(_price_q and _sale_q and any(x in _q_low for x in ("б/у", "бу бытов", "бывш")))
    _explicit_rent_q = any(x in _q_low for x in ("аренд", "снять", "сда"))
    _rent_price_q = bool(_price_q and _explicit_rent_q)
    import re as _intent_re
    _temporary_term_q = bool(_intent_re.search(
        r"\bна\s+(?:(?:\d+)|один|одну|два|две|три|четыре|несколько)\s*"
        r"(?:месяц(?:а|ев)?|недел(?:ю|и|ь)?|дн(?:я|ей|ь)?|сут(?:ки|ок)?)\b",
        _q_low,
    ) or _intent_re.search(r"\bна\s+месяц\b", _q_low))
    _mixed_rent_sale_account = False
    if _price_q or _temporary_term_q or _explicit_rent_q or _sale_q:
        _has_rent = bool(db.execute(text("""
            SELECT 1 FROM client_facts
             WHERE account_id=:a AND status='confirmed' AND category='tovar'
               AND (lower(name) LIKE '%аренд%' OR lower(value) LIKE '%аренд%' OR lower(name) LIKE '%сда%')
             LIMIT 1
        """), {"a": account_id}).first())
        _has_sale = bool(db.execute(text("""
            SELECT 1 FROM client_facts
             WHERE account_id=:a AND status='confirmed' AND category='tovar'
               AND (lower(name) LIKE '%прода%' OR lower(name) LIKE '%продаж%' OR lower(value) LIKE '%продаж%' OR lower(value) LIKE '%покуп%')
               AND lower(value) NOT LIKE 'нет.%'
               AND lower(value) NOT LIKE 'нет %'
               AND lower(value) NOT LIKE '%только с аренд%'
             LIMIT 1
        """), {"a": account_id}).first())
        _mixed_rent_sale_account = bool(_has_rent and _has_sale)
    _price_context_required = bool(
        _price_q and _mixed_rent_sale_account
        and not _rent_price_q and not _sale_q and not _sale_product_price_q and not _used_sale_price_q
    )
    _rent_or_buy_context_required = bool(
        _mixed_rent_sale_account and _temporary_term_q
        and not _explicit_rent_q and not _sale_q
    )
    _capability_q = (
        any(x in _q_low for x in ("сдаете", "продаете", "есть аренда", "есть продажа", "занимаетесь аренд", "занимаетесь продаж"))
        or ("какие бытов" in _q_low and any(x in _q_low for x in ("сда", "аренд", "прода", "продаж")))
    )
    _rent_capability_q = bool(_capability_q and any(x in _q_low for x in ("сда", "аренд")))
    _sale_capability_q = bool(_capability_q and any(x in _q_low for x in ("прода", "продаж", "куп")))
    _make_q = bool(qw & {"изгот", "произв"})
    _dep_q = bool(qw & {"залог", "депоз", "обесп"})
    _delivery_q = bool(qw & stems("доставка доставить привезти перевозка вывоз манипулятор разгрузка"))
    _avail_q = bool(qw & {"налич", "досту", "свобо", "остал", "имеет"}) or (
        bool(qw & {"сейча", "сегод"}) and not _price_q and not _direct_memory_category)
    _price_context_required = bool(_price_context_required and not _delivery_q and not _avail_q)
    if _price_context_required:
        return {
            "found": False,
            "accounts": accs,
            "reason_code": "price_context_required",
            "reply": "Уточните, пожалуйста: вас интересует аренда или покупка?",
            "task": {"account_id": account_id,
                     "title": "Уточнить формат: аренда или покупка",
                     "reason": "в аккаунте есть и аренда, и продажа; без уточнения цена двусмысленна"},
        }
    if _rent_or_buy_context_required:
        return {
            "found": False,
            "accounts": accs,
            "reason_code": "rent_or_buy_context_required",
            "reply": "Уточните, пожалуйста: речь об аренде на указанный срок или о покупке?",
            "task": {"account_id": account_id,
                     "title": "Уточнить формат: аренда или покупка",
                     "reason": "клиент указал временный срок, а аккаунт работает и с арендой, и с продажей"},
        }

    # If this price subject previously had an authoritative repeated outgoing
    # consensus, expiration must fail closed. Never fall back to an older ad or
    # historic dialog price after the current commercial offer has gone stale.
    _consensus_relevant = []
    if _price_q:
        for _cr in rows:
            if _cr[2] != "cena" or _cr[5] != "dialog_promo_consensus":
                continue
            _hits = len(qw & stems("%s %s %s" % (_cr[3], _cr[4], _cr[9])))
            if _hits:
                _consensus_relevant.append(_cr)
    _consensus_blocks_fallback = bool(
        _consensus_relevant and
        not any(not runtime_fact_expired(_r[2], _r[3], _r[4], _r[10], _r[11])
                for _r in _consensus_relevant)
    )
    cands = []
    best, best_score, best_raw = None, 0, 0
    for r in rows:
        if _consensus_blocks_fallback and r[2] == "cena" and r[5] != "dialog_promo_consensus":
            continue
        if runtime_fact_expired(r[2], r[3], r[4], r[10], r[11]):
            continue
        # Для цены предмет определяется только названием факта и алиасами.
        # Текст значения не участвует: иначе «линолеум бытовой» может ошибочно
        # совпасть с запросом «цена б/у бытовки».
        _name_hits_raw = len(qw & stems(r[3]))
        _value_hits_raw = len(qw & stems(r[4]))
        _alias_hits = len(qw & stems(r[9]))
        _nv_hits = _name_hits_raw if r[2] == "cena" else (_name_hits_raw + _value_hits_raw)
        subj = _nv_hits + min(_alias_hits, 1)
        cw = len(qw & stems(CAT_WORDS.get(r[2], "")))
        _fw = stems("%s %s" % (r[3], r[4]))
        _name_fw = stems(r[3])
        _name_alias_fw = stems("%s %s" % (r[3], r[9]))
        _name_alias_low = (str(r[3] or "") + " " + str(r[9] or "")).lower().replace("ё", "е")
        # Rental intent must not be hijacked by a sale-only Avito listing that
        # happens to share the same product/size words.
        if _explicit_rent_q and r[5] == "avito_item":
            _sale_item = any(x in _name_alias_low for x in ("продам", "прода", "продаж", "купить", "покуп"))
            _rent_item = any(x in _name_alias_low for x in ("аренд", "сдам", "сдаю"))
            if _sale_item and not _rent_item:
                continue
        if (r[5] == "dialog_promo_consensus" and "минималь" in str(r[3] or "").lower()
                and _nv_hits == 0):
            continue
        # Прямой вопрос про контакты/адрес/график должен брать только факт своей категории:
        # иначе исторический FAQ с похожими словами может создать ничью с отдельным фактом.
        if _direct_memory_category and r[2] != _direct_memory_category:
            continue
        # Вопрос «сдаёте/продаёте?» — это каталог/возможность, а не любой
        # факт, где случайно встретилось слово «аренда» или «бытовка».
        if _capability_q and r[2] != "tovar":
            continue
        if _rent_capability_q and not ({"аренд", "сда"} & _name_alias_fw):
            continue
        if _sale_capability_q and not ({"прода", "покуп", "куп"} & _name_alias_fw):
            continue
        # наличие подтверждает только менеджер: база на такой вопрос не отвечает
        if _avail_q:
            continue
        # факт про залог отвечает только на явный вопрос о залоге
        if "залог" in _fw and not _dep_q:
            continue
        # вопрос о покупке: чисто арендный факт не отвечает. Но явный
        # отрицательный/режимный факт с «Продажа ...» в имени остаётся допустим.
        if _sale_q and ({"аренд", "залог"} & _fw) and not ({"прода", "покуп"} & _name_fw):
            continue
        if _sale_product_price_q and r[5] == "dialog_promo_consensus":
            continue
        if _used_sale_price_q and r[2] == "cena" and not any(
                x in _name_alias_low for x in ("б/у", "бу бытов", "бывш")):
            continue
        # доставка/вывоз для бытовок рассчитываются индивидуально: общий прайс
        # товара никогда не должен отвечать на вопрос о стоимости логистики.
        if _delivery_q and r[2] == "cena":
            continue
        # вопрос о сроке изготовления: цена отвечает только своя, предметная
        if _make_q and r[2] == "cena" and not ({"изгот", "произв"} & _fw):
            continue
        # Цена отвечает только когда клиент действительно спрашивает цену.
        # Иначе «вы продаёте/сдаёте?» не имеет права цеплять прайс.
        if r[2] == "cena" and not _price_q:
            continue
        # ценовой вопрос требует ценового факта
        if _price_q and r[2] != "cena":
            continue
        # маркер категории лишь уточняет намерение, но не заменяет
        # совпадения по сути: иначе «сколько стоит X» цепляет любую цену
        if not (subj >= min_overlap or (subj >= 1 and cw >= 1)
                or (_direct_memory_category == r[2] and cw >= 1)):
            continue
        _source_bonus = 4 if r[5] == "dialog_promo_consensus" else 0
        score = (_nv_hits * 3 + min(_alias_hits, 1) + cw
                 + (1 if r[2] == "faq" else 0)
                 + (1 if r[8] == "confirmed" else 0)
                 + _source_bonus)
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
            # Дубликаты из разных подтверждённых источников с одинаковым
            # значением — не конфликт. Разные значения по-прежнему оставляют
            # ничью и заставляют BORIS уточнить.
            if len(_top) > 1:
                _cats = {str(c[2][2] or "") for c in _top}
                _vals = {" ".join(str(c[2][4] or "").lower().split()) for c in _top}
                if len(_cats) == 1 and len(_vals) == 1:
                    _top = [max(_top, key=lambda c: int(c[2][0] or 0))]
        if len(_top) > 1 and _direct_memory_category in {"kontakty", "grafik", "geografiya", "akciya"}:
            _seen = set(); _parts = []; _ids = []
            for _c in _top:
                _r = _c[2]
                _val = str(_r[4] or "").strip()
                if not _val or _val in _seen:
                    continue
                _seen.add(_val); _ids.append(_r[0])
                _label = str(_r[3] or "").strip()
                _parts.append(((_label + ": ") if _label and _label.lower() != _val.lower() else "") + _val)
            if _parts:
                return {
                    "found": True, "fact_id": _ids[0], "fact_ids": _ids,
                    "account_id": account_id, "category": _direct_memory_category,
                    "name": TITLES.get(_direct_memory_category, _direct_memory_category),
                    "answer": "\n".join(_parts), "source": "Подтверждённая память BORIS",
                    "source_ref": "combined_confirmed_facts", "confidence": 100,
                    "confirmed": True, "score": _mx, "matched_words": _name_hits,
                    "accounts": accs,
                }
        if len(_top) == 1:
            best, best_score, best_raw = _top[0][2], _top[0][0], _top[0][1]

    if not best:
        # COMPANY_VISIT_FAST_MEMORY_V1:
        # Operational questions about visiting an office/salon/showroom must not
        # fall through to generic qualification when the company profile already
        # proves a customer-facing location exists. We derive only from explicit
        # confirmed wording; a generic word like "офисная мебель" is not enough.
        _visit_q = any(x in _q_low for x in (
            "офис", "салон", "шоурум", "приех", "подъех", "посет", "встрет",
        ))
        if _visit_q:
            _facility_rows = []
            for _r in rows:
                if str(_r[2] or "") not in ("company", "geografiya"):
                    continue
                _evidence = " ".join((str(_r[3] or ""), str(_r[4] or ""))).lower().replace("ё", "е")
                if any(x in _evidence for x in (
                    "сеть салон", "салон", "шоурум", "наш офис",
                    "офис по адресу", "адрес офиса", "офисы",
                )):
                    _facility_rows.append(_r)
            if _facility_rows:
                _all_facility = " ".join(
                    " ".join((str(_r[3] or ""), str(_r[4] or "")))
                    for _r in _facility_rows
                ).lower().replace("ё", "е")
                if "салон" in _all_facility:
                    _facility_phrase = "У компании есть салоны."
                    _address_subject = "салона"
                elif "шоурум" in _all_facility:
                    _facility_phrase = "У компании есть шоурум."
                    _address_subject = "шоурума"
                else:
                    _facility_phrase = "У компании есть офис."
                    _address_subject = "офиса"

                _city_values = []
                _city_ids = []
                for _r in rows:
                    _name_low = str(_r[3] or "").strip().lower().replace("ё", "е")
                    if (_name_low in {"cities", "city", "города", "город", "города работы", "география"}
                            or (str(_r[2] or "") == "geografiya" and "город" in _name_low)):
                        _value = str(_r[4] or "").strip()
                        if _value and _value not in _city_values:
                            _city_values.append(_value)
                            _city_ids.append(_r[0])

                _visit_answer = _facility_phrase
                if _city_values:
                    _visit_answer += " Города: " + "; ".join(_city_values) + "."
                _visit_answer += (
                    " Точный адрес %s в нужном городе уточнит менеджер."
                    % _address_subject
                )
                _ids = [int(_r[0]) for _r in _facility_rows] + [
                    int(x) for x in _city_ids
                ]
                return {
                    "found": True,
                    "fact_id": _ids[0],
                    "fact_ids": list(dict.fromkeys(_ids)),
                    "account_id": account_id,
                    "category": "geografiya",
                    "name": "",
                    "answer": _visit_answer,
                    "source_type": "confirmed_company_context",
                    "source": "Подтверждённые данные компании",
                    "source_ref": "combined_confirmed_company_profile",
                    "confidence": 100,
                    "confirmed": True,
                    "score": 100,
                    "matched_words": 1,
                    "accounts": accs,
                    "policy_version": "COMPANY_VISIT_FAST_MEMORY_V1",
                }

        _reason_code = "knowledge_missing"
        _reply = "Уточню детали у менеджера и передам ваш вопрос."
        _reason = "в базе знаний нет подтверждённого факта"
        if _avail_q:
            _reason_code = "availability_requires_human"
            _reply = "Актуальное наличие проверяет менеджер. Передам ваш запрос."
            _reason = "актуальное наличие требует проверки менеджером"
        elif _delivery_q and _price_q:
            _reason_code = "individual_delivery_price"
            _reply = "Стоимость доставки и вывоза рассчитывается индивидуально. Передам менеджеру для расчёта."
            _reason = "стоимость доставки/вывоза требует индивидуального расчёта"
        elif _price_q:
            _reason_code = "price_missing"
            _reply = "Актуальную стоимость уточнит менеджер. Передам ваш запрос."
            _reason = "нет свежего однозначного подтверждённого ценового факта"
        elif _capability_q:
            _reason_code = "capability_missing"
            _reply = "Уточню этот вариант у менеджера и передам ваш вопрос."
            _reason = "нет однозначного подтверждённого ответа по возможности услуги"
        return {
            "found": False,
            "accounts": accs,
            "reason_code": _reason_code,
            "reply": _reply,
            "task": {"account_id": account_id,
                     "title": "Нужно уточнить: %s" % str(question)[:120],
                     "reason": _reason},
        }
    return {
        "found": True, "fact_id": best[0], "account_id": best[1],
        "category": best[2], "name": best[3], "answer": best[4],
        "source_type": best[5], "source": SOURCES.get(best[5], best[5]), "source_ref": best[6],
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


MODES = ("off", "strict", "hybrid")          # hybrid добавим позже, когда решим по тарифам


def memory_mode(db, account_id):
    """Режим работы памяти для аккаунта. По умолчанию off — старая логика."""
    try:
        r = db.execute(text("SELECT mode FROM memory_modes WHERE account_id=:a"),
                       {"a": account_id}).first()
        return (r[0] if r else "off") or "off"
    except Exception:
        db.rollback()
        return "off"


def refresh_repeated_outgoing_offer_consensus(db, account_id: str, days: int = 7, min_chats: int = 2):
    """Promote only repeated manager-sent promo prices into short-lived live facts.

    One-off calculations are deliberately ignored. A price must appear in a
    promo/special-offer message in at least min_chats distinct outgoing chats.
    The generated fact names are stable, so a later repeated offer updates the
    current value and stales the previous value instead of accumulating prices.
    """
    import re as _re, hashlib as _hashlib
    from datetime import datetime as _dt, timezone as _tz
    _latest_outgoing = db.execute(text("""
        SELECT max(avito_created_at) FROM messenger_messages
        WHERE account_id=:a AND lower(direction) LIKE 'out%' AND text IS NOT NULL
          AND avito_created_at >= extract(epoch from (now() - (:d || ' days')::interval))::bigint
          AND text ~* '(акци|спецпредлож|специальн.{0,15}цен)' AND text ~* '(руб|₽)'
    """), {"a": account_id, "d": str(max(1, min(int(days), 30)))}).scalar()
    _current_consensus = db.execute(text("""
        SELECT max(extract(epoch from source_date)::bigint) FROM client_facts
        WHERE account_id=:a AND source_type='dialog_promo_consensus' AND status='confirmed'
    """), {"a": account_id}).scalar()
    if _latest_outgoing and _current_consensus and int(_current_consensus) >= int(_latest_outgoing):
        return {"updated": 0, "accepted": "cached"}

    rows = db.execute(text("""
        SELECT avito_chat_id,text,avito_created_at
        FROM messenger_messages
        WHERE account_id=:a
          AND lower(direction) LIKE 'out%'
          AND text IS NOT NULL
          AND avito_created_at >= extract(epoch from (now() - (:d || ' days')::interval))::bigint
          AND text ~* '(акци|спецпредлож|специальн.{0,15}цен)'
          AND text ~* '(руб|₽)'
        ORDER BY avito_created_at DESC
        LIMIT 300
    """), {"a": account_id, "d": str(max(1, min(int(days), 30)))}).all()
    line_re = _re.compile(r"^\s*[-•]?\s*(.{3,150}?)\s*[-—–:]\s*(\d[\d\s]{2,8})\s*(?:руб(?:\.|лей)?|₽)\b", _re.I)
    groups = {}
    for chat_id, body, ts in rows:
        for raw_line in str(body or "").splitlines():
            m = line_re.search(raw_line.strip())
            if not m:
                continue
            label = _re.sub(r"\s+", " ", m.group(1)).strip(" -—–:.;")[:150]
            if not label or any(x in label.lower() for x in ("доставка", "вывоз", "продлить", "штраф", "залог")):
                continue
            try:
                price = int(_re.sub(r"\s+", "", m.group(2)))
            except Exception:
                continue
            if price <= 0:
                continue
            norm = _re.sub(r"[^a-zа-яё0-9]+", " ", label.lower()).strip()
            key = (norm, price)
            item = groups.setdefault(key, {"label": label, "price": price, "chats": set(), "ts": 0})
            item["chats"].add(str(chat_id)); item["ts"] = max(int(item["ts"] or 0), int(ts or 0))
    accepted = [x for x in groups.values() if len(x["chats"]) >= int(min_chats)]
    if not accepted:
        return {"updated": 0, "accepted": 0}
    updated = 0
    for item in accepted:
        name = "Текущая акционная цена аренды: " + item["label"]
        value = str(item["price"])
        source_date = _dt.fromtimestamp(item["ts"], _tz.utc) if item["ts"] else _dt.now(_tz.utc)
        db.execute(text("""
            UPDATE client_facts SET status='stale',updated_at=now()
            WHERE account_id=:a AND category='cena' AND name=:n
              AND status='confirmed' AND value<>:v
        """), {"a": account_id, "n": name, "v": value})
        row = db.execute(text("""
            SELECT id FROM client_facts WHERE account_id=:a AND category='cena' AND name=:n
            ORDER BY id DESC LIMIT 1
        """), {"a": account_id, "n": name}).first()
        fh = _hashlib.sha256((account_id + '|cena|' + name + '|' + value).encode('utf-8')).hexdigest()
        if row:
            db.execute(text("""
                UPDATE client_facts SET value=:v,status='confirmed',confidence=100,scope='account',
                  source_type='dialog_promo_consensus',source_ref='repeated_outgoing_offer',source_date=:sd,
                  confirmed_by='system_repeated_outgoing_consensus',confirmed_at=now(),updated_at=now(),fact_hash=:h
                WHERE id=:i
            """), {"v": value, "sd": source_date, "h": fh, "i": row[0]})
        else:
            db.execute(text("""
                INSERT INTO client_facts(account_id,category,name,value,scope,source_type,source_ref,source_date,
                  confidence,status,fact_hash,snippet,confirmed_by,confirmed_at,extracted_at,updated_at)
                VALUES(:a,'cena',:n,:v,'account','dialog_promo_consensus','repeated_outgoing_offer',:sd,
                  100,'confirmed',:h,:s,'system_repeated_outgoing_consensus',now(),:sd,now())
            """), {"a": account_id, "n": name, "v": value, "sd": source_date,
                    "h": fh, "s": (name + ': ' + value)[:500]})
        updated += 1
    accepted.sort(key=lambda x: (x["price"], x["label"].lower()))
    summary = "; ".join("%s — %s руб." % (x["label"], x["price"]) for x in accepted[:12])
    latest_ts = max(x["ts"] for x in accepted)
    summary_date = _dt.fromtimestamp(latest_ts, _tz.utc) if latest_ts else _dt.now(_tz.utc)
    promo_name = "Текущая акция аренды из повторяющихся исходящих менеджера"
    promo_hash = _hashlib.sha256((account_id + '|akciya|' + promo_name + '|' + summary).encode('utf-8')).hexdigest()
    prow = db.execute(text("SELECT id FROM client_facts WHERE account_id=:a AND category='akciya' AND name=:n ORDER BY id DESC LIMIT 1"), {"a": account_id, "n": promo_name}).first()
    if prow:
        db.execute(text("""UPDATE client_facts SET value=:v,status='confirmed',confidence=100,scope='account',source_type='dialog_promo_consensus',source_ref='repeated_outgoing_offer',source_date=:sd,confirmed_by='system_repeated_outgoing_consensus',confirmed_at=now(),updated_at=now(),fact_hash=:h WHERE id=:i"""), {"v": summary, "sd": summary_date, "h": promo_hash, "i": prow[0]})
    else:
        db.execute(text("""INSERT INTO client_facts(account_id,category,name,value,scope,source_type,source_ref,source_date,confidence,status,fact_hash,snippet,confirmed_by,confirmed_at,extracted_at,updated_at) VALUES(:a,'akciya',:n,:v,'account','dialog_promo_consensus','repeated_outgoing_offer',:sd,100,'confirmed',:h,:s,'system_repeated_outgoing_consensus',now(),:sd,now())"""), {"a": account_id, "n": promo_name, "v": summary, "sd": summary_date, "h": promo_hash, "s": summary[:500]})
    # Generic "from" price is exposed only when all repeated offer lines share
    # a real subject token (e.g. «бытовка»). This prevents a generic minimum
    # from answering unrelated questions such as an individual delivery cost.
    _token_sets = [set(_re.findall(r"[a-zа-яё0-9]{4,}", x["label"].lower())) for x in accepted]
    _common = set.intersection(*_token_sets) if _token_sets else set()
    _common -= {"цена", "рублей", "месяц", "обычная", "комнаты"}
    min_price = min(x["price"] for x in accepted)
    generic_subject = " ".join(sorted(_common)[:3]).strip()
    if not generic_subject:
        db.commit()
        return {"updated": updated + 1, "accepted": len(accepted), "min_price": min_price}
    generic_name = "Текущая акционная минимальная цена аренды: " + generic_subject
    generic_value = "от %s" % min_price
    grow = db.execute(text("SELECT id FROM client_facts WHERE account_id=:a AND category='cena' AND source_type='dialog_promo_consensus' AND name LIKE 'Текущая акционная минимальная цена%' ORDER BY id DESC LIMIT 1"), {"a": account_id}).first()
    generic_hash = _hashlib.sha256((account_id + '|cena|' + generic_name + '|' + generic_value).encode('utf-8')).hexdigest()
    if grow:
        db.execute(text("""UPDATE client_facts SET name=:n,value=:v,status='confirmed',confidence=100,scope='account',source_type='dialog_promo_consensus',source_ref='repeated_outgoing_offer',source_date=:sd,confirmed_by='system_repeated_outgoing_consensus',confirmed_at=now(),updated_at=now(),fact_hash=:h WHERE id=:i"""), {"n": generic_name, "v": generic_value, "sd": summary_date, "h": generic_hash, "i": grow[0]})
        gid = int(grow[0])
    else:
        gid = int(db.execute(text("""INSERT INTO client_facts(account_id,category,name,value,scope,source_type,source_ref,source_date,confidence,status,fact_hash,snippet,confirmed_by,confirmed_at,extracted_at,updated_at) VALUES(:a,'cena',:n,:v,'account','dialog_promo_consensus','repeated_outgoing_offer',:sd,100,'confirmed',:h,:s,'system_repeated_outgoing_consensus',now(),:sd,now()) RETURNING id"""), {"a": account_id, "n": generic_name, "v": generic_value, "sd": summary_date, "h": generic_hash, "s": generic_value}).scalar())
    for alias in ("стоимость аренды", "сколько стоит аренда", "цена аренды", "аренда в месяц"):
        if not db.execute(text("SELECT 1 FROM client_aliases WHERE account_id=:a AND fact_id=:f AND lower(alias)=lower(:x) LIMIT 1"), {"a": account_id, "f": gid, "x": alias}).first():
            db.execute(text("INSERT INTO client_aliases(account_id,fact_id,alias,source_type,created_at) VALUES(:a,:f,:x,'outgoing_consensus',now())"), {"a": account_id, "f": gid, "x": alias})
    db.commit()
    return {"updated": updated + 2, "accepted": len(accepted), "min_price": min_price}


def answer_for_ai(db, account_id, question, shared=True):
    """То, что зовёт МОП. Возвращает готовый ответ либо честное «уточню».

    off    — память не участвует, вызывающий работает как раньше
    strict — отвечаем только подтверждёнными знаниями
    """
    mode = memory_mode(db, account_id)
    if mode == "off":
        return {"mode": "off", "use_memory": False}
    try:
        _qst = stems(question)
        if _qst & (stems(CAT_WORDS.get("cena", "")) | stems(CAT_WORDS.get("akciya", ""))):
            refresh_repeated_outgoing_offer_consensus(db, account_id)
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        print("[memory] repeated outgoing offer refresh skipped: %s" % str(e)[:160])
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
    _qlow = str(question or "").lower().replace("ё", "е")
    _catalog_fast = bool(
        r.get("found") and r.get("category") == "tovar"
        and (any(x in _qlow for x in ("сдаете", "продаете", "есть аренда", "есть продажа"))
             or ("какие бытов" in _qlow and any(x in _qlow for x in ("сда", "аренд", "прода", "продаж"))))
        and not any(x in _qlow for x in ("подойдет", "лучше", "посовет", "выбрать", "какой размер", "достав", "расчет", "расчёт"))
    )
    r["fast_memory_safe"] = bool(
        r.get("found") and (
            r.get("category") in {"akciya", "kontakty", "geografiya", "grafik"}
            or (r.get("category") == "cena" and r.get("source_type") == "dialog_promo_consensus")
            or _catalog_fast
        )
    )
    return r


def confirmed_context(db, account_id: str, limit: int = 80, max_chars: int = 12000,
                      include_rules: bool = False, shared: bool = True) -> str:
    """Confirmed memory for generation, with explicit business-scope sharing only.

    Own confirmed facts are always visible. Facts from another profile are visible
    only when both profiles belong to the same explicit memory_scopes group and
    the source fact is scope='business'. owner_user_id alone is never a sharing
    boundary, so unrelated clients cannot leak facts into each other.
    """
    accs = scope_accounts(db, account_id, shared=shared)
    _rule_clause = "" if include_rules else " AND category <> 'rule'"
    rows = db.execute(text("""
        SELECT account_id,category,name,value,unit,source_date,extracted_at
        FROM client_facts
        WHERE account_id IN :accs
          AND status='confirmed'
          AND (account_id=:self OR COALESCE(scope,'business')='business')""" + _rule_clause + """
        ORDER BY
          CASE WHEN account_id=:self THEN 0 ELSE 1 END,
          CASE category
            WHEN 'rule' THEN 0 WHEN 'garantiya' THEN 1 WHEN 'cena' THEN 2
            WHEN 'usluga' THEN 3 WHEN 'tovar' THEN 4 WHEN 'faq' THEN 5 ELSE 6
          END,
          updated_at DESC NULLS LAST,id DESC
        LIMIT :l
    """).bindparams(bindparam("accs", expanding=True)), {
        "accs": accs,
        "self": account_id,
        "l": max(1, min(int(limit or 80), 200)),
    }).mappings().all()
    parts = []
    total = 0
    seen = set()
    for row in rows:
        name = str(row.get("name") or "").strip()
        value = str(row.get("value") or "").strip()
        if runtime_fact_expired(row.get("category"), name, value,
                                row.get("source_date"), row.get("extracted_at")):
            continue
        if not value:
            continue
        unit = str(row.get("unit") or "").strip()
        line = f"- {name + ': ' if name and name.lower() != value.lower() else ''}{value}{(' ' + unit) if unit else ''}"
        norm = " ".join(line.lower().split())
        if norm in seen:
            continue
        seen.add(norm)
        if total + len(line) + 1 > max_chars:
            break
        parts.append(line)
        total += len(line) + 1
    return "\n".join(parts)


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
        raise HTTPException(status_code=400, detail="Режим должен быть off, strict или hybrid")
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
    """Rules available to one MOP without cross-client leakage.

    scope='account'  — only this account;
    scope='business' — profiles in the same explicit memory_scopes group;
    scope='platform' — generic owner-confirmed MOP behavior for every profile.
    """
    accs = scope_accounts(db, account_id, shared)
    query_accs = list(dict.fromkeys(list(accs) + ["__platform__"]))
    sql = ("SELECT id, account_id, value, scope, confidence, status, source_ref,"
           " confirmed_by FROM client_facts WHERE category='rule'"
           "  AND status <> 'rejected' AND account_id IN :accs")
    if only_confirmed:
        sql += " AND status='confirmed'"
    rows = db.execute(text(sql).bindparams(bindparam("accs", expanding=True)),
                      {"accs": query_accs}).all()
    out = []
    for r in rows:
        sc = r[3] or "account"
        if r[1] == "__platform__":
            if sc != "platform":
                continue
        elif r[1] != account_id and sc != "business":
            # A sibling account may share only explicitly business-wide rules.
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
    "dialog": 60, "dialog_price": 55, "dialog_promo": 40, "dialog_promo_consensus": 100,
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
    "cena": 60, "akciya": 7, "kontakty": 180, "grafik": 180,
    "garantiya": 365, "usloviya": 365, "rekvizity": 730,
    "usluga": 730, "tovar": 730, "faq": 365, "rule": 365,
    "preimushchestvo": 730, "keys": 730, "stil": 730,
}


def runtime_fact_expired(category, name, value, source_date=None, extracted_at=None, now=None):
    """Fail closed for time-sensitive knowledge used in client replies.

    A fact can remain visible/auditable as confirmed in Knowledge while being
    ineligible for live answers after its TTL or an explicit "до DD.MM.YYYY"
    deadline. Promo-like FAQ text gets the shorter promotion TTL even when a
    historic extractor classified it as FAQ.
    """
    import re as _re
    from datetime import datetime as _dt, timezone as _tz
    current = now or _dt.now(_tz.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_tz.utc)
    text_value = (str(name or "") + "\n" + str(value or "")).lower().replace("ё", "е")
    cat = str(category or "").lower()
    ttl_cat = "akciya" if any(x in text_value for x in ("акция", "акционн", "спецпредлож")) else cat
    ttl = FACT_TTL_DAYS.get(ttl_cat)
    if any(x in text_value for x in ("в наличии", "сейчас в аренде", "свободная бытовка", "свободные бытовки", "есть свободн")):
        ttl = min(int(ttl or 7), 7)
    stamp = source_date or extracted_at
    if ttl and stamp:
        if getattr(stamp, "tzinfo", None) is None:
            stamp = stamp.replace(tzinfo=_tz.utc)
        if stamp < current - __import__("datetime").timedelta(days=int(ttl)):
            return True
    for d, m, y in _re.findall(r"\bдо\s+(\d{1,2})[.\-/](\d{1,2})[.\-/](20\d{2})\b", text_value):
        try:
            if _dt(int(y), int(m), int(d), tzinfo=_tz.utc).date() < current.date():
                return True
        except ValueError:
            continue
    return False


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


class AnswerBody(BaseModel):
    account_id: str
    question: str
    answer: str


@router.post("/answer")
def answer_question(body: AnswerBody, user=Depends(get_current_user)):
    """Клиент отвечает на частый вопрос покупателей прямо в Базе знаний.

    Ответ сразу подтверждён: это слова владельца о собственном бизнесе,
    проверять их не у кого. Повторный ответ на тот же вопрос обновляет
    прежний, а не плодит дубли.
    """
    import hashlib
    q = (body.question or "").strip()[:255]
    a = (body.answer or "").strip()
    if not q or not a:
        raise HTTPException(status_code=400, detail="Нужен вопрос и ответ")

    h = hashlib.md5(("faq|" + q.lower()).encode("utf-8")).hexdigest()
    db = SessionLocal()
    try:
        who = _who(user)
        db.execute(text(
            "INSERT INTO client_facts (account_id, category, name, value,"
            " source_type, source_ref, source_date, confidence, status,"
            " confirmed_by, confirmed_at, fact_hash, extracted_at, updated_at)"
            " VALUES (:a, 'faq', :n, :v, 'client', 'answer_ui', :t, 100,"
            " 'confirmed', :w, :t, :h, :t, :t)"
            " ON CONFLICT (account_id, fact_hash) DO UPDATE SET"
            " value = EXCLUDED.value, status = 'confirmed', confidence = 100,"
            " confirmed_by = EXCLUDED.confirmed_by, confirmed_at = EXCLUDED.confirmed_at,"
            " updated_at = EXCLUDED.updated_at"),
            {"a": body.account_id, "n": q, "v": a, "w": who, "t": _now(), "h": h})
        db.commit()
        return {"status": "ok", "message": "Ответ сохранён — BORIS будет отвечать так"}
    finally:
        db.close()
