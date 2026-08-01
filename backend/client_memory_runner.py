"""Память клиента v1 — извлечение фактов из объявлений и переписок Avito.

Без вызовов модели: всё детерминированно. Запуск:
  venv/bin/python3 client_memory_runner.py extract <account_id>
  venv/bin/python3 client_memory_runner.py stats   <account_id>
"""
import sys, re, json, hashlib
from datetime import datetime, timezone

from app.db.session import SessionLocal
from sqlalchemy import text

TAG = re.compile(r"<[^>]+>")
WS = re.compile(r"[ \t\u00a0]+")
PRICE = re.compile(r"(\d[\d\s\u00a0]{2,})\s*(?:₽|руб|р\.|рублей)", re.I)
PRICE_TYS = re.compile(r"(\d+(?:[.,]\d+)?)\s*тыс", re.I)
WORD = re.compile(r"[а-яёa-z0-9]{4,}", re.I)

SRC_CONF = {"avito_item": 85, "dialog": 60, "dialog_price": 55}


def clean(s):
    s = TAG.sub(" ", str(s or ""))
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
    s = WS.sub(" ", s)
    return "\n".join(l.strip() for l in s.split("\n") if l.strip())


def norm(s):
    return WS.sub(" ", str(s or "").strip().lower())


def fhash(cat, name, value):
    return hashlib.md5(("%s|%s|%s" % (cat, norm(name), norm(value))).encode()).hexdigest()


def prices_in(t):
    out = []
    for m in PRICE.finditer(t or ""):
        d = re.sub(r"\D", "", m.group(1))
        if d and 100 <= int(d) <= 100000000:
            out.append(int(d))
    for m in PRICE_TYS.finditer(t or ""):
        try:
            out.append(int(float(m.group(1).replace(",", ".")) * 1000))
        except Exception:
            pass
    return out


def bullets(desc):
    res = []
    for line in clean(desc).split("\n"):
        if len(line) < 12 or len(line) > 160 or line.rstrip().endswith("?"):
            continue
        if re.match(r"^[•▪✅✔☑—\-–*]|^[\U0001F300-\U0001FAFF]", line):
            res.append(re.sub(r"^[•▪✅✔☑—\-–*\s]+", "", line).strip())
    return res[:8]


def titles_all(v):
    """Все варианты заголовка Avito из набора {а|б|в}."""
    t = str(v or "").strip()
    if t.startswith("{") and t.endswith("}"):
        t = t[1:-1]
    parts = [x.strip().strip('"').strip() for x in t.split("|")] if "|" in t else [t]
    return [x for x in parts if x]


def add_alias(db, acc, fact_id, alias, src="avito_item"):
    """Алиас — только словарь понимания. Достоверность факта не меняет."""
    if not str(alias or "").strip():
        return 0
    try:
        r = db.execute(text(
            "INSERT INTO client_aliases (account_id, fact_id, alias, source_type)"
            " VALUES (:a,:f,:al,:s) ON CONFLICT DO NOTHING"),
            {"a": acc, "f": fact_id, "al": str(alias)[:250], "s": src})
        return int(getattr(r, "rowcount", 0) or 0)
    except Exception:
        db.rollback()
        return 0


def fact_id_of(db, acc, cat, name, value):
    r = db.execute(text("SELECT id FROM client_facts WHERE account_id=:a AND fact_hash=:h"),
                   {"a": acc, "h": fhash(cat, name, value)}).first()
    return r[0] if r else None


def first_title(v):
    """Заголовок Avito приходит набором вариантов {а|б|в} — берём первый."""
    t = str(v or "").strip()
    if t.startswith("{") and t.endswith("}"):
        t = t[1:-1]
    if "|" in t:
        t = t.split("|")[0]
    return t.strip().strip('"').strip()


PROMO = ("🔥", "только до", "акция", "скидка", "успей", "предложение действует")


def is_promo(t):
    """Рассылка с прайсом — это не цена конкретному клиенту."""
    low = str(t or "").lower()
    return any(m in low for m in PROMO)


def add(db, acc, cat, name, value, src_type, src_ref, conf, snippet="", src_date=None):
    if not str(value or "").strip() or not str(name or "").strip():
        return 0
    h = fhash(cat, name, value)
    try:
        _r = db.execute(text(
            "INSERT INTO client_facts (account_id, category, name, value, source_type,"
            " source_ref, source_date, confidence, status, fact_hash, snippet)"
            " VALUES (:a,:c,:n,:v,:st,:sr,:sd,:cf,'draft',:h,:sn)"
            " ON CONFLICT (account_id, fact_hash) DO NOTHING"),
            {"a": acc, "c": cat, "n": str(name)[:250], "v": str(value)[:2000],
             "st": src_type, "sr": str(src_ref)[:250], "sd": src_date,
             "cf": conf, "h": h, "sn": str(snippet)[:500]})
        return int(getattr(_r, "rowcount", 0) or 0)
    except Exception as e:
        db.rollback()
        print("   пропуск факта:", str(e)[:70])
        return 0


def load_items(db, acc):
    r = db.execute(text("SELECT value FROM storage WHERE account_id=:a AND key='feed_items'"),
                   {"a": acc}).first()
    if not r:
        return []
    try:
        data = json.loads(str(r[0]))
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("items") or list(data.values())
    return [i for i in data if isinstance(i, dict)]


def extract_listings(db, acc):
    items = load_items(db, acc)
    n = 0
    for it in items:
        iid = it.get("id") or it.get("Id") or ""
        ref = "avito_item:%s" % iid
        title = clean(first_title(it.get("title")))
        if not title:
            continue
        cat = "usluga" if (it.get("service_type") or it.get("service_subtype")) else "tovar"
        cat_val = clean(it.get("category")) or "-"
        n += add(db, acc, cat, title, cat_val, "avito_item", ref, SRC_CONF["avito_item"])
        fid = fact_id_of(db, acc, cat, title, cat_val)
        for alt in titles_all(it.get("title"))[1:]:
            alt = clean(alt)
            if alt and alt.lower() != title.lower():
                add_alias(db, acc, fid, alt)
        price = it.get("price")
        if str(price or "").strip():
            n += add(db, acc, "cena", title, str(price), "avito_item", ref,
                     SRC_CONF["avito_item"], snippet="price_type=%s" % it.get("price_type"))
        if str(it.get("guarantee") or "").strip():
            n += add(db, acc, "garantiya", title, clean(it.get("guarantee")),
                     "avito_item", ref, SRC_CONF["avito_item"])
        if str(it.get("work_experience") or "").strip():
            n += add(db, acc, "preimushchestvo", "Опыт работы",
                     clean(it.get("work_experience")), "avito_item", ref,
                     SRC_CONF["avito_item"])
        for b in bullets(it.get("description")):
            n += add(db, acc, "preimushchestvo", b[:120], b, "avito_item", ref, 70)
    db.commit()
    return len(items), n


_ANS_HINT = ("руб", "₽", "цена", "стоим", "дней", "дня", "недел", "час",
             "срок", "готов", "сделаю", "можем", "входит", "включ", "стоит")


def _is_real_answer(t):
    """Встречный вопрос менеджера — не ответ и не знание.
    Ответ = есть конкретика (цифра/цена/срок) ИЛИ это утверждение, а не вопрос."""
    tl = (t or "").lower()
    if any(ch.isdigit() for ch in t):
        return True
    if any(k in tl for k in _ANS_HINT):
        return True
    return not tl.rstrip().endswith("?")


def extract_dialogs(db, acc):
    rows = db.execute(text(
        "SELECT avito_chat_id, direction, text, avito_created_at, item_title"
        "  FROM messenger_messages WHERE account_id=:a AND text IS NOT NULL"
        " ORDER BY avito_chat_id, avito_created_at"), {"a": acc}).all()
    by_chat = {}
    for chat, d, t, ts, title in rows:
        by_chat.setdefault(chat, []).append((d, t, ts, title))
    pairs, n = [], 0
    for chat, seq in by_chat.items():
        pending, skipped = None, 0
        for d, t, ts, title in seq:
            inc = str(d or "").lower().startswith("in")
            t = clean(t)
            if "Системное сообщение" in t[:60] or t.startswith("["):
                continue
            if inc:
                if len(t) >= 12:
                    pending = (t, ts, title)
            elif pending and len(t) >= 20:
                pairs.append({"chat": chat, "q": pending[0], "a": t,
                              "ts": pending[1], "title": pending[2]})
                pending = None
    for p in pairs:
        ref = "chat:%s" % p["chat"]
        n += add(db, acc, "faq", p["q"][:200], p["a"], "dialog", ref,
                 SRC_CONF["dialog"], snippet=p["title"] or "")
        found = prices_in(p["a"])
        if is_promo(p["a"]) or len(found) > 2:
            if found:
                n += add(db, acc, "cena", "Прайс из рассылки",
                         ", ".join(str(x) for x in found[:6]), "dialog_promo", ref,
                         40, snippet=p["a"][:300])
            continue
        anchor = clean(first_title(p.get("title"))) or "Цена, названная в переписке"
        for pr in found:
            n += add(db, acc, "cena", anchor[:150], str(pr), "dialog_price", ref,
                     SRC_CONF["dialog_price"], snippet=p["a"][:300])
    for p in sorted(pairs, key=lambda x: -len(x["a"]))[:3]:
        n += add(db, acc, "stil", "Пример ответа менеджера", p["a"], "dialog",
                 "chat:%s" % p["chat"], 60)
    db.commit()
    return len(by_chat), len(pairs), n


def mark_conflicts(db, acc):
    """Конфликт = цена из переписки вне диапазона цен объявлений.
    Разные цены внутри объявлений — это вилка по комплектации, не конфликт."""
    def nums(src):
        out = []
        for r in db.execute(text(
                "SELECT id, value FROM client_facts WHERE account_id=:a"
                " AND category='cena' AND source_type=:s"), {"a": acc, "s": src}).all():
            try:
                out.append((r[0], int(float(str(r[1]).replace(" ", "")))))
            except Exception:
                pass
        return out

    db.execute(text("UPDATE client_facts SET status='draft' WHERE account_id=:a"
                    " AND category='cena' AND status='conflict'"), {"a": acc})
    listed = [v for _, v in nums("avito_item")]
    n = 0
    if listed:
        lo, hi = min(listed), max(listed)
        for fid, v in nums("dialog_price"):
            if v < lo or v > hi:
                db.execute(text("UPDATE client_facts SET status='conflict',"
                                " snippet = coalesce(snippet,'') || :note WHERE id=:i"),
                           {"i": fid, "note": " | в объявлениях %d-%d" % (lo, hi)})
                n += 1
    db.commit()
    return n


def coverage(db, acc):
    """Грубая оценка: на сколько входящих вопросов есть факт со свежими словами."""
    qs = [r[0] for r in db.execute(text(
        "SELECT text FROM messenger_messages WHERE account_id=:a"
        " AND lower(direction) LIKE 'in%' AND length(text) > 15"), {"a": acc}).all()]
    def pool_for(cond):
        rows = db.execute(text(
            "SELECT f.name, f.value, coalesce(string_agg(al.alias, ' '), '')"
            "  FROM client_facts f"
            "  LEFT JOIN client_aliases al ON al.fact_id = f.id"
            " WHERE f.account_id=:a AND " + cond.replace("status", "f.status")
            .replace("confidence", "f.confidence") + " GROUP BY f.id, f.name, f.value"),
            {"a": acc}).all()
        return [set(w.lower() for w in WORD.findall("%s %s %s" % (r[0], r[1], r[2])))
                for r in rows]

    strong = pool_for("(status='confirmed' OR confidence >= 80)")
    allf = pool_for("status <> 'rejected'")
    hit_s = hit_a = 0
    for q in qs:
        qw = set(w.lower() for w in WORD.findall(clean(q)))
        if not qw:
            continue
        if any(len(qw & p) >= 2 for p in strong):
            hit_s += 1
        if any(len(qw & p) >= 2 for p in allf):
            hit_a += 1
    return len(qs), hit_s, hit_a


def snapshot(db, acc, label="auto"):
    """Замер для сравнения до и после подтверждения."""
    def one(sql):
        return db.execute(text(sql), {"a": acc}).scalar() or 0
    facts = one("SELECT count(*) FROM client_facts WHERE account_id=:a AND status<>'rejected'")
    confirmed = one("SELECT count(*) FROM client_facts WHERE account_id=:a AND status='confirmed'")
    usable = one("SELECT count(*) FROM client_facts WHERE account_id=:a"
                 " AND (status='confirmed' OR (status='draft' AND confidence>=80))")
    qs, strong, allf = coverage(db, acc)
    db.execute(text(
        "INSERT INTO client_memory_snapshots (account_id, label, facts, confirmed,"
        " usable, questions, covered_strong, covered_all)"
        " VALUES (:a,:l,:f,:c,:u,:q,:s,:al)"),
        {"a": acc, "l": label, "f": facts, "c": confirmed, "u": usable,
         "q": qs, "s": strong, "al": allf})
    db.commit()
    return {"facts": facts, "confirmed": confirmed, "usable": usable,
            "questions": qs, "covered_strong": strong, "covered_all": allf}


def stats(db, acc):
    print("\n=== ПАМЯТЬ КЛИЕНТА: %s ===" % acc)
    rows = db.execute(text(
        "SELECT category, status, count(*) FROM client_facts WHERE account_id=:a"
        " GROUP BY 1,2 ORDER BY 1,2"), {"a": acc}).all()
    for c, st, n in rows:
        print("   %-16s %-10s %4d" % (c, st, n))
    tot = db.execute(text("SELECT count(*) FROM client_facts WHERE account_id=:a"),
                     {"a": acc}).scalar()
    good = db.execute(text(
        "SELECT count(*) FROM client_facts WHERE account_id=:a"
        " AND (status='confirmed' OR confidence >= 80)"), {"a": acc}).scalar()
    conf = db.execute(text("SELECT count(*) FROM client_facts WHERE account_id=:a"
                           " AND status='conflict'"), {"a": acc}).scalar()
    qs, hit_s, hit_a = coverage(db, acc)
    print("\n   всего фактов          %d" % tot)
    print("   пригодно для МОПа     %d" % good)
    print("   конфликтов            %d" % conf)
    print("   входящих вопросов     %d" % qs)
    print("   закрывают надёжные    %d (%.0f%%)" % (hit_s, 100.0 * hit_s / qs if qs else 0))
    print("   похожий вопрос был    %d (%.0f%%)" % (hit_a, 100.0 * hit_a / qs if qs else 0))


def main():
    if len(sys.argv) < 3:
        print("usage: client_memory_runner.py extract|stats <account_id>")
        return
    cmd, acc = sys.argv[1], sys.argv[2]
    db = SessionLocal()
    if cmd == "extract":
        ni, nf = extract_listings(db, acc)
        nc, np_, nd = extract_dialogs(db, acc)
        cf = mark_conflicts(db, acc)
        print("объявлений обработано %d, фактов из них %d" % (ni, nf))
        print("диалогов %d, пар вопрос-ответ %d, фактов из них %d" % (nc, np_, nd))
        print("конфликтов помечено   %d" % cf)
        stats(db, acc)
    elif cmd == "stats":
        stats(db, acc)
    elif cmd == "snapshot":
        lab = sys.argv[3] if len(sys.argv) > 3 else "auto"
        print("снимок «%s»:" % lab, snapshot(db, acc, lab))
    else:
        print("неизвестная команда")
    db.close()


if __name__ == "__main__":
    main()
