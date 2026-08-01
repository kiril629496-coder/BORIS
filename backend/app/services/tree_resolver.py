"""Смысловой спуск по дереву категорий Avito.

Единый механизм для ВСЕХ разделов: услуги, товары, недвижимость, вакансии.
Никаких `if category == ...` — разделы отличаются только данными в path.

Дерево не хранится отдельно: дочерние категории вычисляются из уникальных
сегментов path в category_tree_leaves. Второго справочника не появляется.

Выбор на каждом уровне делает модель, а не поиск подстроки. Это принципиально:
клиент пишет «брусчатка», а в дереве Avito она называется «Тротуарная плитка».
"""
import json
import re

SEP = " > "
MAX_DEPTH = 8
CONF_AUTO = 80      # уверенность, при которой идём глубже без вопроса
CONF_GAP = 25       # и отрыв от второго варианта


def _norm(s):
    """Ключ кэша: без регистра, пунктуации и лишних пробелов."""
    return re.sub(r"[^\w\s]", " ", str(s or "").lower()).strip()[:120]


def _children(db, prefix):
    """Уникальные значения следующего сегмента пути под данным префиксом."""
    from sqlalchemy import text as _t
    depth = len(prefix.split(SEP)) if prefix else 0
    if prefix:
        rows = db.execute(_t(
            "SELECT DISTINCT split_part(path, ' > ', :n) AS seg "
            "FROM category_tree_leaves "
            "WHERE path LIKE :p AND split_part(path, ' > ', :n) <> '' "
            "ORDER BY 1"), {"n": depth + 1, "p": prefix + SEP + "%"}).all()
    else:
        rows = db.execute(_t(
            "SELECT DISTINCT top_level FROM category_tree_leaves "
            "WHERE coalesce(top_level,'') <> '' ORDER BY 1")).all()
    return [r[0] for r in rows if r[0]]


def _examples(db, prefix, seg, limit=4):
    """Несколько листьев из поддерева варианта. Без них модель выбирает вслепую:
    «Строительство стен» звучит подходяще для брусчатки, пока не видно, что под
    ним только блоки и кирпич, а плитка лежит в «Железобетонных изделиях»."""
    from sqlalchemy import text as _t
    full = (prefix + SEP + seg) if prefix else seg
    rows = db.execute(_t(
        "SELECT DISTINCT leaf_name FROM category_tree_leaves "
        "WHERE path = :f OR path LIKE :fp ORDER BY 1 LIMIT :l"),
        {"f": full, "fp": full + SEP + "%", "l": limit}).all()
    return [r[0] for r in rows if r[0]]


def _leaf(db, path):
    """Строка листа, если такой путь существует целиком."""
    from sqlalchemy import text as _t
    r = db.execute(_t(
        "SELECT id, template_id, leaf_name FROM category_tree_leaves "
        "WHERE path = :p LIMIT 1"), {"p": path}).first()
    return {"leaf_id": r[0], "template_id": r[1], "leaf_name": r[2]} if r else None


def _direct_match(db, user_text):
    """Точное сопоставление до спуска: если в тексте встречается имя листа
    дерева или category_id шаблона — категория известна без вызовов модели.

    Зачем: «Бордюр садовый» уводил модель в «Для сада и дачи > Дачные
    конструкции» (шаблона нет), тогда как правильный лист «Стройматериалы >
    Железобетонные изделия > Дорожные > Бордюры» лежал рядом. Плюс это
    убирает 4-6 вызовов модели на каждой знакомой нише.
    """
    from sqlalchemy import text as _t
    t = " " + _norm(user_text) + " "
    best = None

    rows = db.execute(_t(
        "SELECT template_id, category_id FROM category_templates "
        "WHERE coalesce(btrim(category_id),'') <> '' "
        "  AND coalesce(required_fields,'') NOT IN ('','[]')")).all()
    for tid, cid in rows:
        n = _norm(cid)
        if len(n) >= 4 and (" " + n + " ") in t and (best is None or len(n) > best[2]):
            best = (str(tid), "category_id:" + cid, len(n))

    rows = db.execute(_t(
        "SELECT template_id, leaf_name, path FROM category_tree_leaves "
        "WHERE coalesce(btrim(leaf_name),'') <> ''")).all()
    for tid, leaf, path in rows:
        n = _norm(leaf)
        if len(n) >= 5 and (" " + n + " ") in t and (best is None or len(n) > best[2]):
            best = (str(tid), "leaf:" + leaf, len(n))

    if not best:
        return None
    r = db.execute(_t(
        "SELECT id, path, leaf_name FROM category_tree_leaves "
        "WHERE btrim(template_id) = :t LIMIT 1"), {"t": best[0]}).first()
    if not r:
        return None
    return {"template_id": best[0], "leaf_id": r[0], "path": r[1],
            "leaf_name": r[2], "matched_by": best[1]}


def _ask_model(user_text, chosen_path, children, account_id, db=None):
    """Один уровень выбора. Возвращает выбор, уверенность, альтернативы, почему."""
    from gigachat.models import Messages, MessagesRole
    from gigachat_pool import chat_with_fallback

    lines = []
    for i, c in enumerate(children):
        ex = _examples(db, chosen_path, c) if db is not None else []
        tail = ("  — внутри: " + ", ".join(e[:26] for e in ex)) if ex else ""
        lines.append("%d. %s%s" % (i + 1, c, tail))
    listing = "\n".join(lines)
    prompt = (
        "Ты подбираешь категорию Avito по описанию от продавца.\n\n"
        "ОПИСАНИЕ ПРОДАВЦА:\n%s\n\n"
        "УЖЕ ВЫБРАННЫЙ ПУТЬ: %s\n\n"
        "ВАРИАНТЫ СЛЕДУЮЩЕГО УРОВНЯ:\n%s\n\n"
        "Выбери ОДИН вариант из списка. Учитывай, что продавец пишет бытовым\n"
        "языком, а в дереве Avito свои названия: «брусчатка» это «Тротуарная\n"
        "плитка», «кухонный гарнитур» это «Кухни», «алмазное сверление» это\n"
        "работы по бетону. Смотри на смысл, а не на совпадение слов.\n\n"
        "ВАЖНО. После каждого варианта показано, что лежит ВНУТРИ него —\n"
        "ориентируйся прежде всего на это, а не на само название.\n"
        "Вариант «Другое» выбирай только если ни один другой не подходит.\n"
        "Различай: продажа вещи это товар, выполнение работ это услуга.\n"
        "«Кухни на заказ» — это товар (мебель), а не услуга по ремонту.\n\n"
        "Ответь ТОЛЬКО JSON без пояснений и без markdown:\n"
        '{\"choice\": \"точное название варианта из списка\", '
        '\"confidence\": число 0-100, '
        '\"alternatives\": [\"ещё один подходящий вариант\", \"и ещё\"], '
        '\"why\": \"одно короткое предложение\"}\n\n'
        "confidence — насколько ты уверен. Если описание не даёт понять, к чему\n"
        "оно относится, ставь ниже 60 и перечисли альтернативы."
        % (str(user_text)[:1200], chosen_path or "(начало дерева)", listing))

    raw = chat_with_fallback(
        [Messages(role=MessagesRole.USER, content=prompt)],
        temperature=0.1, max_tokens=400,
        account_id=account_id, operation="tree_resolve") or ""

    txt = re.sub(r"```(?:json)?|```", "", str(raw)).strip()
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        return {"choice": None, "confidence": 0, "alternatives": [], "why": "модель не ответила"}
    try:
        d = json.loads(m.group(0))
    except Exception:
        return {"choice": None, "confidence": 0, "alternatives": [], "why": "неразбираемый ответ"}

    choice = str(d.get("choice") or "").strip()
    if choice not in children:                      # мягкое сопоставление
        low = {c.lower(): c for c in children}
        choice = low.get(choice.lower(), None)
    alts = [a for a in (d.get("alternatives") or []) if a in children and a != choice]
    try:
        conf = int(float(d.get("confidence") or 0))
    except Exception:
        conf = 0
    return {"choice": choice, "confidence": max(0, min(100, conf)),
            "alternatives": alts[:3], "why": str(d.get("why") or "")[:200]}


def _cache_get(db, account_id, key):
    from app.models.storage import Storage
    row = db.query(Storage).filter(Storage.account_id == account_id,
                                   Storage.key == "tree_cache").first()
    if not row or not row.value:
        return None
    try:
        return (json.loads(row.value) or {}).get(key)
    except Exception:
        return None


def _cache_put(db, account_id, key, value):
    from app.models.storage import Storage
    row = db.query(Storage).filter(Storage.account_id == account_id,
                                   Storage.key == "tree_cache").first()
    try:
        data = json.loads(row.value) if (row and row.value) else {}
    except Exception:
        data = {}
    data[key] = value
    if len(data) > 200:
        data = dict(list(data.items())[-200:])
    blob = json.dumps(data, ensure_ascii=False)
    if row:
        row.value = blob
    else:
        db.add(Storage(account_id=account_id, key="tree_cache", value=blob))
    db.commit()


def _orphan_candidates(db, user_text, limit=40):
    """Шаблоны БЕЗ пути и БЕЗ связи с деревом — 269 из 660. Дерево собиралось
    из документации Avito отдельно от Excel, и множества template_id не совпали.
    Связать по id нельзя — его там нет. Поэтому вторая полка данных: ищем
    прямо среди шаблонов по пересечению слов, затем выбор делает модель."""
    from sqlalchemy import text as _t
    rows = db.execute(_t(
        "SELECT template_id, category_name FROM category_templates t "
        " WHERE coalesce(btrim(category_path),'') = '' "
        "   AND coalesce(required_fields,'') NOT IN ('','[]') "
        "   AND NOT EXISTS (SELECT 1 FROM category_tree_leaves l "
        "                    WHERE btrim(l.template_id) = btrim(t.template_id))")).all()
    words = {w for w in _norm(user_text).split() if len(w) >= 4}
    scored = []
    for tid, name in rows:
        nm = _norm(name)
        if not nm:
            continue
        score = sum(1 for w in words if w[:5] in nm)
        if score:
            scored.append((score, str(tid), str(name)))
    scored.sort(key=lambda x: -x[0])
    return [(t, n) for _, t, n in scored[:limit]]


def _orphan_match(db, user_text, account_id):
    """Выбор среди шаблонов вне дерева. Возвращает template_id либо None."""
    from gigachat.models import Messages, MessagesRole
    from gigachat_pool import chat_with_fallback

    cand = _orphan_candidates(db, user_text)
    if not cand:
        return None
    if len(cand) == 1:
        return {"template_id": cand[0][0], "name": cand[0][1], "why": "единственный кандидат"}

    listing = "\n".join("%d. %s" % (i + 1, n) for i, (_, n) in enumerate(cand))
    prompt = (
        "Подбери категорию Avito по описанию продавца.\n\n"
        "ОПИСАНИЕ:\n%s\n\nВАРИАНТЫ:\n%s\n\n"
        "Названия могли быть обрезаны при выгрузке — ориентируйся на смысл.\n"
        "Если НИ ОДИН вариант не подходит, верни choice пустым.\n\n"
        'Ответь только JSON: {\"choice\": \"название из списка или пусто\", '
        '\"confidence\": 0-100, \"why\": \"кратко\"}'
        % (str(user_text)[:900], listing))

    raw = chat_with_fallback(
        [Messages(role=MessagesRole.USER, content=prompt)],
        temperature=0.1, max_tokens=300,
        account_id=account_id, operation="orphan_resolve") or ""
    m = re.search(r"\{.*\}", re.sub(r"```(?:json)?|```", "", str(raw)), re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except Exception:
        return None
    ch = str(d.get("choice") or "").strip()
    if not ch:
        return None
    try:
        conf = int(float(d.get("confidence") or 0))
    except Exception:
        conf = 0
    if conf < 60:
        return None
    for tid, name in cand:
        if name.strip().lower() == ch.lower() or ch.lower() in name.strip().lower():
            return {"template_id": tid, "name": name, "why": str(d.get("why") or "")[:160]}
    return None


# Версия механизма резолва. Штампуется в заявках на подключение
# категории, чтобы потом воспроизводить проблемные случаи.
RESOLVER_VERSION = "2026-07-31.orphan-path-fix"


def resolve_by_tree(user_text, account_id=None, answers=None, use_cache=True, db=None):
    """Спуск от корня до листа.

    answers — {номер уровня: выбранное значение}, ответы клиента на вопросы.
    Возврат: status ok | need_answer | awaiting_adviz_fields | not_found
    """
    from app.db.session import SessionLocal
    own = db is None
    if own:
        db = SessionLocal()
    try:
        answers = {int(k): v for k, v in (answers or {}).items()}
        ckey = _norm(user_text)
        out = {"source_text": str(user_text)[:400], "levels": [], "path": "",
               "leaf_id": None, "template_id": None, "fields_count": 0,
               "question": None, "status": "not_found", "from_cache": False}

        if use_cache and account_id and not answers:
            hit = _cache_get(db, account_id, ckey)
            if hit:
                hit["from_cache"] = True
                return hit

        direct = _direct_match(db, user_text)
        if direct and not answers:
            out.update({"path": direct["path"], "leaf_id": direct["leaf_id"],
                        "template_id": direct["template_id"],
                        "leaf_name": direct["leaf_name"],
                        "matched_by": direct["matched_by"]})
            out["levels"] = [{"level": 0, "segment": direct["path"],
                              "confidence": 100, "alternatives": [],
                              "why": "точное совпадение: " + direct["matched_by"]}]
            from app.models.category_template import CategoryTemplate as _CT0
            t0 = db.query(_CT0).filter(_CT0.template_id == direct["template_id"]).first()
            if t0 and (t0.required_fields or "").strip() not in ("", "[]"):
                try:
                    out["fields_count"] = len(json.loads(t0.required_fields))
                except Exception:
                    out["fields_count"] = 0
                out["status"] = "ok"
                if use_cache and account_id:
                    try:
                        _cache_put(db, account_id, ckey, dict(out))
                    except Exception:
                        pass
                return out
            # шаблона нет — не сдаёмся, идём обычным спуском
            out["status"] = "not_found"
            out["levels"] = []
            out["path"] = ""
            out["template_id"] = None
            out["leaf_id"] = None

        path = ""
        for depth in range(MAX_DEPTH):
            kids = _children(db, path)
            if not kids:
                break                                   # дошли до листа

            if depth in answers and answers[depth] in kids:
                pick = {"choice": answers[depth], "confidence": 100,
                        "alternatives": [], "why": "выбрал клиент"}
            elif len(kids) == 1:
                pick = {"choice": kids[0], "confidence": 100,
                        "alternatives": [], "why": "единственный вариант"}
            else:
                pick = _ask_model(user_text, path, kids, account_id, db=db)

            if not pick["choice"]:
                out["status"] = "need_answer"
                out["path"] = path
                out["question"] = {
                    "level": depth,
                    "text": "Не понял, к чему это относится. Выберите раздел:",
                    "options": kids[:12]}
                return out

            gap = 100
            if pick["alternatives"]:
                gap = CONF_GAP if pick["confidence"] < CONF_AUTO else 100

            out["levels"].append({
                "level": depth, "segment": pick["choice"],
                "confidence": pick["confidence"],
                "alternatives": pick["alternatives"], "why": pick["why"]})

            if pick["confidence"] < CONF_AUTO or gap < CONF_GAP:
                opts = [pick["choice"]] + pick["alternatives"]
                out["status"] = "need_answer"
                out["path"] = path
                out["question"] = {
                    "level": depth,
                    "text": "Уточните, что именно вы размещаете:",
                    "options": [o for o in opts if o][:4] or kids[:4]}
                return out

            path = (path + SEP + pick["choice"]) if path else pick["choice"]

        out["path"] = path
        leaf = _leaf(db, path)
        if not leaf:
            orph = _orphan_match(db, user_text, account_id)
            if orph:
                # ПРАВКА 30.07: _orphan_match больше НЕ подтверждает категорию.
                # Путь берём только из дерева, имя шаблона — подсказка, не путь.
                out.update({"leaf_id": None, "template_id": None,
                            "hint_template_id": orph["template_id"],
                            "hint_name": orph["name"],
                            "matched_by": "подсказка вне дерева"})
                out["status"] = "awaiting_adviz_fields"
                out["reason"] = "no_leaf"
                out["message"] = (
                    "Категория пока не подключена. Похоже на «%s», но структура "
                    "полей для неё ещё не загружена." % orph["name"])
                return out
            out["status"] = "not_found"
            return out

        out.update({"leaf_id": leaf["leaf_id"], "template_id": leaf["template_id"],
                    "leaf_name": leaf["leaf_name"]})

        from app.models.category_template import CategoryTemplate as CT
        tpl = db.query(CT).filter(CT.template_id == str(leaf["template_id"])).first()
        if not tpl or not (tpl.required_fields or "").strip() or tpl.required_fields == "[]":
            out["status"] = "awaiting_adviz_fields"
            out["reason"] = "no_template"
            out["message"] = ("Категория определена: %s. Структура полей для неё "
                              "пока не загружена." % path)
        else:
            try:
                out["fields_count"] = len(json.loads(tpl.required_fields))
            except Exception:
                out["fields_count"] = 0
            out["status"] = "ok"

        # Кэшируем ТОЛЬКО успешный резолв. Иначе неудачная попытка залипает
        # навсегда и отдаётся мгновенно, хотя после правки механизма тот же
        # текст резолвится верно.
        if use_cache and account_id and out["status"] == "ok":
            try:
                _cache_put(db, account_id, ckey, dict(out))
            except Exception:
                pass
        return out
    finally:
        if own:
            db.close()


# ============ ИЗВЛЕЧЕНИЕ ЗНАЧЕНИЙ ИЗ ТЕКСТА ============
# Модель работает не только классификатором категории, но и извлекателем
# характеристик. «Кухня МДФ, белая, гарантия 24 месяца» должно дать
# DoorsMaterial=МДФ, Color=Белый, Warranty=24 месяца — и эти поля больше
# не спрашиваются. Спрашиваем только то, чего в тексте действительно нет.

SKIP_TAGS = {"Id", "Title", "Description", "Price", "Images", "Address",
             "DateBegin", "DateEnd", "Category", "ImageNames", "ListingFee",
             "AdStatus", "Promo"}

MAX_FIELDS_PER_CALL = 40
MAX_OPTIONS_SHOWN = 30


def _match_option(value, options):
    """Сверка извлечённого значения с allowed_values. Ничего не выдумываем:
    если значения нет в списке — отбрасываем, поле уйдёт в вопросы."""
    v = str(value or "").strip()
    if not v:
        return None
    for o in options:
        if str(o).strip().lower() == v.lower():
            return o
    for o in options:                      # мягкое вхождение: «МДФ» в «МДФ плёнка»
        so = str(o).strip().lower()
        if v.lower() in so or so in v.lower():
            return o
    return None


def extract_fields(user_text, template_id, account_id=None, existing=None, db=None):
    """Тянет из текста клиента значения полей конкретного шаблона.

    Возврат: extracted {tag: value}, missing [{tag,label,options}],
             rejected [{tag,value,reason}] — что модель предложила, но не прошло сверку.
    """
    from gigachat.models import Messages, MessagesRole
    from gigachat_pool import chat_with_fallback
    from app.db.session import SessionLocal
    from app.models.category_template import CategoryTemplate as CT

    own = db is None
    if own:
        db = SessionLocal()
    try:
        out = {"extracted": {}, "missing": [], "rejected": [], "fields_total": 0}
        tpl = db.query(CT).filter(CT.template_id == str(template_id)).first()
        if not tpl or not (tpl.required_fields or "").strip():
            return out
        try:
            fields = json.loads(tpl.required_fields)
        except Exception:
            return out

        have = dict(existing or {})
        cand = []
        for f in fields:
            tag = f.get("tag")
            if not tag or tag in SKIP_TAGS or tag in have:
                continue
            cand.append(f)
        out["fields_total"] = len(cand)
        if not cand:
            return out

        lines = []
        for f in cand[:MAX_FIELDS_PER_CALL]:
            av = f.get("allowed_values") or []
            label = f.get("name_ru") or f.get("tag")
            if av:
                shown = av[:MAX_OPTIONS_SHOWN]
                tail = " …" if len(av) > MAX_OPTIONS_SHOWN else ""
                lines.append("- %s (%s). Допустимо: %s%s"
                             % (f["tag"], label, "; ".join(str(x) for x in shown), tail))
            else:
                ex = f.get("example")
                lines.append("- %s (%s). Свободное значение%s"
                             % (f["tag"], label,
                                (", например: %s" % str(ex)[:40]) if ex else ""))

        prompt = (
            "Ты извлекаешь характеристики товара или услуги из объявления продавца.\n\n"
            "ТЕКСТ ПРОДАВЦА:\n%s\n\n"
            "ПОЛЯ, КОТОРЫЕ НУЖНО ЗАПОЛНИТЬ:\n%s\n\n"
            "ПРАВИЛА:\n"
            "1. Указывай ТОЛЬКО то, что прямо следует из текста продавца.\n"
            "2. Ничего не додумывай и не предполагай. Сомневаешься — пропусти поле.\n"
            "3. Где есть список допустимых — бери значение ТОЧНО из него.\n"
            "4. Пустые и неизвестные поля просто не включай в ответ.\n"
            "5. Синонимы учитывай: «белая» это «Белый», «МДФ фасады» это «МДФ».\n\n"
            "Ответь ТОЛЬКО JSON без markdown:\n"
            '{\"values\": {\"Тег\": \"значение\"}, \"notes\": \"одно предложение\"}'
            % (str(user_text)[:2000], "\n".join(lines)))

        raw = chat_with_fallback(
            [Messages(role=MessagesRole.USER, content=prompt)],
            temperature=0.0, max_tokens=900,
            account_id=account_id, operation="field_extract") or ""

        txt = re.sub(r"```(?:json)?|```", "", str(raw)).strip()
        m = re.search(r"\{.*\}", txt, re.S)
        got = {}
        if m:
            try:
                got = (json.loads(m.group(0)) or {}).get("values") or {}
            except Exception:
                got = {}

        by_tag = {f["tag"]: f for f in cand}
        for tag, val in got.items():
            f = by_tag.get(tag)
            if not f:
                out["rejected"].append({"tag": tag, "value": val,
                                        "reason": "поля нет в шаблоне"})
                continue
            av = f.get("allowed_values") or []
            if av:
                hit = _match_option(val, av)
                if hit is None:
                    out["rejected"].append({"tag": tag, "value": val,
                                            "reason": "нет в списке допустимых"})
                    continue
                out["extracted"][tag] = hit
            else:
                v = str(val or "").strip()
                if v:
                    out["extracted"][tag] = v

        for f in cand:
            if f["tag"] in out["extracted"]:
                continue
            av = f.get("allowed_values") or []
            if len(av) == 1:                       # константа шаблона, не вопрос
                out["extracted"][f["tag"]] = av[0]
                continue
            if av:
                out["missing"].append({"tag": f["tag"],
                                       "label": f.get("name_ru") or f["tag"],
                                       "options": av})
        return out
    finally:
        if own:
            db.close()
