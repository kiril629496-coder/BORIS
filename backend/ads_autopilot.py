# -*- coding: utf-8 -*-
"""Автопилот объявлений. Конвейер: найти → сгенерировать → проверить правила →
from app.services.mass_ai_guard import guarded_post as _mass_guarded_post
самооценка → предложить человеку. Публикации нет: белый список — только заголовок.

Запуск:
    venv/bin/python3 ads_autopilot.py <account_id>          # просмотр
    venv/bin/python3 ads_autopilot.py <account_id> --apply  # создать предложения
"""
from client_aliases import client_id as _client_id, client_text as _client_text
import json
import os
import sys
import time
from datetime import date, timedelta

RUNTIME_BACKEND = os.environ.get("BORIS_RUNTIME_BACKEND_ROOT", "/root/BORIS/backend")
sys.path.insert(0, RUNTIME_BACKEND)

from sqlalchemy import text as sql
from app.db.session import SessionLocal
import ads_rules as R

MAX_PER_RUN = 5           # лимит: одна ошибка не должна испортить тысячи
MAX_SHARE = 0.03          # и не более 3% объявлений аккаунта
MIN_DAYS_SILENT = 7
TRIES = 1                 # one paid intent per item; quality failure stays fail-closed
SCORE_MIN = 7.0
STAMP = time.strftime("%Y-%m-%d %H:%M")


def log(m):
    print("[ads %s] %s" % (STAMP, m), flush=True)


def _load(db, acc, key, default=None):
    row = db.execute(sql("SELECT value FROM storage WHERE account_id=:a AND key=:k"),
                     {"a": acc, "k": key}).fetchone()
    if not row or not row[0]:
        return default
    try:
        return json.loads(row[0])
    except Exception:
        return default


def _save(db, acc, key, value):
    p = json.dumps(value, ensure_ascii=False)
    if db.execute(sql("SELECT 1 FROM storage WHERE account_id=:a AND key=:k"),
                  {"a": acc, "k": key}).fetchone():
        db.execute(sql("UPDATE storage SET value=:v WHERE account_id=:a AND key=:k"),
                   {"v": p, "a": acc, "k": key})
    else:
        db.execute(sql("INSERT INTO storage (account_id, key, value) VALUES (:a,:k,:v)"),
                   {"a": acc, "k": key, "v": p})
    db.commit()


def _city_of(db, acc):
    row = db.execute(sql("SELECT company_niche, name FROM accounts WHERE account_id=:a"),
                     {"a": acc}).fetchone()
    blob = " ".join(str(x or "") for x in (row or ()))
    for city in ("Санкт-Петербург", "Москва", "Екатеринбург", "Нижний Новгород"):
        if city.lower()[:6] in blob.lower():
            return city
    # Город чаще всего лежит в Базе знаний: «Регион работы», «География доставки».
    rows = db.execute(sql("SELECT name, value FROM client_facts WHERE account_id=:a"
                          " AND status <> 'rejected' LIMIT 200"), {"a": acc}).fetchall()
    blob2 = " ".join(str(r[0] or "") + " " + str(r[1] or "") for r in rows).lower()
    for city, marks in (("Санкт-Петербург", ("петербург", "спб", "ленинградск")),
                        ("Москва", ("москва", "московск")),
                        ("Екатеринбург", ("екатеринбург",)),
                        ("Нижний Новгород", ("нижний новгород",))):
        if any(m in blob2 for m in marks):
            return city
    return ""


def find_weak(db, acc, days=MIN_DAYS_SILENT):
    window, info = {}, {}
    for back in range(1, days + 1):
        snap = _load(db, acc, "daily_stats:" + (date.today() - timedelta(days=back)).isoformat())
        if not snap:
            continue
        for it in (snap.get("items") or []):
            iid = it.get("id")
            if iid is None:
                continue
            cur = window.setdefault(iid, {"v": 0, "c": 0})
            cur["v"] += int(it.get("views") or 0)
            cur["c"] += int(it.get("contacts") or 0)
            info[iid] = it
    weak, total_active = [], 0
    for iid, w in window.items():
        it = info.get(iid) or {}
        if it.get("status") != "active":
            continue
        total_active += 1
        if w["v"] > 0 or w["c"] > 0:
            continue
        weak.append({"id": iid, "title": it.get("title") or "", "views": w["v"],
                     "category": it.get("category") or ""})
    return weak, total_active


def _kind(title, category):
    t = (title + " " + category).lower()
    return "rent" if ("аренд" in t or "прокат" in t) else "buy"


def _generate(account_id, item_id, old_title, city, kind, category, bad_before=None, terms=None,
              facts=None, avoid=None, generics=None):
    """One deterministic paid title intent; never retries silently."""
    import hashlib
    need = "аренду" if kind == "rent" else "купить"
    fix = ("\nПрошлая попытка отклонена: " + ", ".join(bad_before)) if bad_before else ""
    # Занятые заголовки: модель должна отличаться от них, а не угадывать вслепую
    busy = ""
    if avoid:
        busy = ("\nЭТИ ЗАГОЛОВКИ УЖЕ ЗАНЯТЫ, твой должен заметно отличаться "
                "не предлогом, а СМЫСЛОВЫМ словом:\n- " + "\n- ".join(list(avoid)[:8]) + "\n")
    # Подтверждённые данные объявления — единственный источник цифр
    known = ""
    if facts:
        known = "\nПОДТВЕРЖДЁННЫЕ ДАННЫЕ ОБЪЯВЛЕНИЯ (только отсюда можно брать цифры):\n" \
                + str(facts)[:600] + "\n"
    prompt = (
        "Ты пишешь заголовок объявления на Avito. Верни ТОЛЬКО заголовок,"
        " одной строкой, без кавычек и пояснений.\n\n"
        "ЖЁСТКИЕ ПРАВИЛА:\n"
        "- не более 50 символов, стремись к 45\n"
        "- без эмодзи, без HTML, без КАПСА, без фигурных скобок\n"
        "- обязательно город: " + (city or "город клиента") + "\n"
        "- обязательно слово про " + need + "\n"
        "- естественный русский язык, без кликбейта и восклицаний\n"
        "- НИ ОДНОЙ цифры, которой нет в подтверждённых данных ниже.\n"
        "  Нет данных о размере или количестве — пиши заголовок БЕЗ чисел\n"
        "- СОХРАНИ отличительное слово прежнего заголовка (жилая, прорабская,\n"
        "  распашонка, с доставкой, утеплённая, вагончик и подобные): именно оно\n"
        "  отличает это объявление от соседних. Потерять его нельзя\n"
        "- НЕ повторяй прежний заголовок\n\n"
        "- НЕ заменяй предметные слова: тип товара, материал и назначение\n"
          "  переноси ДОСЛОВНО. Меняй только структуру, добавляй город и намерение\n"
        + (("- отраслевые слова, которые нельзя искажать: "
            + ", ".join(terms[:12]) + "\n") if terms else "")
        + (("- ОБЯЗАТЕЛЬНО родовое слово товара: " + " или ".join(generics[:4])
            + ". Нишевый термин (распашонка, прорабская) его НЕ заменяет:\n"
              "  по такому слову не ищут, ставь его уточнением\n") if generics else "")
        + known
        + busy
        + "\nКатегория Avito: " + (category or "не указана") + "\n"
        "Прежний заголовок: " + old_title + fix
    )
    try:
        from app.api.campaigns import _ff_guarded_openai_response
        basis=json.dumps({'account_id':str(account_id),'item_id':str(item_id),'prompt':prompt},ensure_ascii=False,sort_keys=True,separators=(',',':'))
        intent='ads-title:'+hashlib.sha256(basis.encode('utf-8','ignore')).hexdigest()
        paid=_ff_guarded_openai_response(str(account_id),'заголовок объявления','gpt-5.4',prompt,120,intent,timeout=60)
        txt=str((paid or {}).get('text') or '').strip().strip('"').split("\n")[0][:80]
        return txt,(paid or {}).get('usage') or {}
    except Exception:
        return "", {}



def rework(acc, dry=True, limit=MAX_PER_RUN):
    db = SessionLocal()
    try:
        weak, total = find_weak(db, acc)
        # Три процента защищают крупные аккаунты, но у малых съедают лимит:
        # берём максимум из доли и разрешённого минимума, но не выше limit.
        cap = min(limit, max(limit, int(total * MAX_SHARE)))
        log("%s — активных %d, слабых %d, к переработке не более %d" % (acc, total, len(weak), cap))
        if not weak:
            return

        done = _load(db, acc, "ads_reworked", {}) or {}
        queue = [w for w in weak if str(w["id"]) not in done][:cap]
        if not queue:
            log("все слабые уже обработаны ранее")
            return

        city = _city_of(db, acc)
        log("город клиента: %s" % (city or "не определён"))
        if dry:
            for w in queue:
                k = _kind(w["title"], w["category"])
                log("  [просмотр] %s | %s" % (w["title"][:44], "; ".join(R.explain(w["title"], w["views"], city, k))))
            return

        from app.api.avito import _audit_log
        props = _load(db, acc, "ads_proposals", []) or []
        run_id = int(time.time())
        made = 0

        # Отчёт приёмки: сколько принято сразу и почему отклонялось остальное
        stat = {"обработано": 0, "с_первой_попытки": 0, "числа": 0,
                "дубли": 0, "правила": 0, "родовое": 0, "пропущено": 0}
        cases = {"числа": [], "дубли": [], "правила": [], "родовое": []}

        feed_all = _load(db, acc, "feed_items", []) or []
        # Родовые слова ниши: по нишевому термину не ищут. Список задаётся на аккаунт.
        generics = _load(db, acc, "ads_generics", []) or []
        # Вид источника храним ВМЕСТЕ с заголовком — иначе в журнале не разобрать,
        # с чем именно совпал кандидат.
        taken = [("ранее сохранённое предложение", p.get("стало") or "") for p in props]
        taken += [("действующее объявление", i.get("title") or "") for i in _load(db, acc,
                  "daily_stats:" + date.today().isoformat(), {}).get("items", [])]

        for w in queue:
            stat["обработано"] += 1
            kind = _kind(w["title"], w["category"])
            src_row = next((f for f in feed_all
                            if str(f.get("Id") or f.get("id") or "") == str(w["id"])), {})
            # Старый заголовок — тоже подтверждённый источник: размер из него терять нельзя
            blob = json.dumps(src_row, ensure_ascii=False) + " " + (w.get("title") or "")
            best, bad, sc, used = "", None, None, 0
            for attempt in range(TRIES):
                used = attempt + 1
                cand, _ = _generate(acc, w["id"], w["title"], city, kind, w["category"], bad,
                                   facts=blob[:600],
                                   avoid=[t for _, t in taken][-8:],
                                   generics=generics)
                if not cand:
                    continue
                bad = R.check(cand, city, kind, w["title"])
                if bad:
                    stat["правила"] += 1
                    if len(cases["правила"]) < 3:
                        cases["правила"].append("«%s» — %s" % (cand, "; ".join(bad)))
                ok_num, fake = R.numbers_ok(cand, blob)
                if not ok_num:
                    why_num = "число %s не подтверждено данными объявления" % fake
                    bad = bad + [why_num]
                    stat["числа"] += 1
                    if len(cases["числа"]) < 3:
                        cases["числа"].append("«%s» — %s" % (cand, why_num))
                ok_gen, need_gen = R.generic_ok(cand, generics)
                if not ok_gen:
                    why_gen = ("нет родового слова (%s): по нишевому термину не ищут"
                               % need_gen)
                    bad = bad + [why_gen]
                    stat["родовое"] += 1
                    if len(cases["родовое"]) < 3:
                        cases["родовое"].append("«%s» — %s" % (cand, why_gen))
                # Своё же объявление и его клоны не блокируют переработку:
                # похожесть на собственный старый заголовок неизбежна,
                # а буквальный повтор ловит R.check(..., w["title"]).
                mine = (w.get("title") or "").strip().lower()
                others = [(k, t) for k, t in taken
                          if (t or "").strip().lower() != mine]
                dup, like, coef, src_kind = R.too_similar(cand, others, city=city)
                if dup:
                    why_dup = ("дубль: «%s» совпадает с %s «%s», коэффициент %.2f при пороге 0.70"
                               % (cand, src_kind or "ранее известным заголовком", like, coef))
                    bad = bad + [why_dup]
                    stat["дубли"] += 1
                    if len(cases["дубли"]) < 3:
                        cases["дубли"].append(why_dup)
                sc = R.score(cand, city, kind)
                if not bad and sc["итого"] >= SCORE_MIN:
                    best = cand
                    break
            if not best:
                reason = "; ".join(bad or ["генератор не вернул текст"])
                stat["пропущено"] += 1
                log("  не удалось за %d попытки: %s (%s)" % (TRIES, w["title"][:34], reason))
                _audit_log(acc, "ads_title_rejected",
                           "Объявление «%s»: новый заголовок не принят за %d попытки. Причина: %s"
                           % (w["title"][:50], TRIES, reason), "boris")
                continue
            if used == 1:
                stat["с_первой_попытки"] += 1

            why = R.explain(w["title"], w["views"], city, kind)
            props = [p for p in props if p.get("id") != w["id"]]
            props.append({"id": w["id"], "запуск": run_id, "создано": STAMP,
                          "было": w["title"], "стало": best, "почему": why,
                          "оценка": sc, "статус": "ожидает"})
            taken.append(("предложение текущего прогона", best))
            done[str(w["id"])] = STAMP
            made += 1
            _audit_log(acc, "ads_title_proposed",
                       "«%s» → «%s». Причина: %s" % (w["title"][:50], best[:50], "; ".join(why)),
                       "boris")
            log("  %s -> %s (оценка %s)" % (w["title"][:30], best[:40], sc["итого"]))

        if made:
            _save(db, acc, "ads_proposals", props)
            _save(db, acc, "ads_reworked", done)
            _audit_log(acc, "ads_run_summary",
                       "Проверено объявлений: %d. Слабых: %d. Предложено новых заголовков: %d. "
                       "Отклонено вариантов: выдуманные числа %d, дубли %d, нарушения правил %d. "
                       "Объявлений пропущено совсем: %d. "
                       "Причина отбора: ни одного просмотра за %d дней."
                       % (total, len(weak), made, stat["числа"], stat["дубли"],
                          stat["правила"], stat["пропущено"], MIN_DAYS_SILENT), "boris")

        log("")
        log("ОТЧЁТ ПРИЁМКИ (запуск №%d)" % run_id)
        log("  объявлений обработано:        %d" % stat["обработано"])
        log("  предложений создано:          %d (с первой попытки %d)" % (made, stat["с_первой_попытки"]))
        log("  вариантов отклонено — числа:  %d" % stat["числа"])
        log("  вариантов отклонено — дубли:  %d" % stat["дубли"])
        log("  вариантов отклонено — правила:%d" % stat["правила"])
        log("  вариантов отклонено — нет родового слова: %d" % stat["родовое"])
        log("  объявлений пропущено совсем:  %d" % stat["пропущено"])
        for key in ("числа", "дубли", "правила", "родовое"):
            for c in cases[key]:
                log("    [%s] %s" % (key, c))
    finally:
        db.close()


def apply_run(acc, run_id, dry=True):
    """Публикует предложения запуска в фид. Сохраняет прежний заголовок,
    чтобы откат вернул всё дословно."""
    from app.api.avito import _audit_log, update_feed_item, FeedItemUpdateRequest
    db = SessionLocal()
    try:
        props = _load(db, acc, "ads_proposals", []) or []
        mine = [p for p in props if str(p.get("запуск")) == str(run_id)
                and p.get("статус") == "ожидает"]
        if not mine:
            log("запуск №%s: нечего применять" % run_id)
            return
        log("запуск №%s: к публикации %d заголовков%s" % (run_id, len(mine), " (просмотр)" if dry else ""))
        if dry:
            for p in mine:
                log("  %s -> %s" % (p["было"][:34], p["стало"][:40]))
            return
        ok = 0
        for p in mine:
            try:
                res = update_feed_item(FeedItemUpdateRequest(
                    account_id=acc, item_id=p["id"], title=p["стало"]))
            except Exception as e:
                log("  ошибка публикации %s: %s" % (p["id"], str(e)[:80]))
                continue
            if (res or {}).get("status") != "ok":
                log("  отказ по %s: %s" % (p["id"], str(res)[:80]))
                continue
            p["статус"] = "применено"
            p["применено"] = time.strftime("%Y-%m-%d %H:%M")
            ok += 1
            _audit_log(acc, "ads_title_applied",
                       "Заголовок изменён: «%s» → «%s». Причина: %s"
                       % (p["было"][:44], p["стало"][:44], "; ".join(p.get("почему") or [])),
                       "boris")
        _save(db, acc, "ads_proposals", props)
        _audit_log(acc, "ads_run_applied",
                   "Запуск №%s: изменено заголовков %d из %d. Откатить: ads_autopilot.py %s --rollback %s"
                   % (run_id, ok, len(mine), acc, run_id), "boris")
        log("применено: %d из %d" % (ok, len(mine)))
    finally:
        db.close()


def rollback_run(acc, run_id, dry=True):
    """Возвращает прежние заголовки запуска. Одна команда — всё назад."""
    from app.api.avito import _audit_log, update_feed_item, FeedItemUpdateRequest
    db = SessionLocal()
    try:
        props = _load(db, acc, "ads_proposals", []) or []
        mine = [p for p in props if str(p.get("запуск")) == str(run_id)
                and p.get("статус") == "применено"]
        if not mine:
            log("запуск №%s: нечего откатывать" % run_id)
            return
        log("запуск №%s: к откату %d заголовков%s" % (run_id, len(mine), " (просмотр)" if dry else ""))
        if dry:
            for p in mine:
                log("  вернуть: %s <- %s" % (p["было"][:34], p["стало"][:40]))
            return
        ok = 0
        for p in mine:
            try:
                res = update_feed_item(FeedItemUpdateRequest(
                    account_id=acc, item_id=p["id"], title=p["было"]))
            except Exception as e:
                log("  ошибка отката %s: %s" % (p["id"], str(e)[:80]))
                continue
            if (res or {}).get("status") != "ok":
                continue
            p["статус"] = "откачено"
            p["откачено"] = time.strftime("%Y-%m-%d %H:%M")
            ok += 1
            _audit_log(acc, "ads_title_rolled_back",
                       "Заголовок возвращён: «%s» ← «%s»" % (p["было"][:44], p["стало"][:44]),
                       "boris")
        # Снимаем отметку об обработке, иначе объявление больше никогда не попадёт в отбор
        done = _load(db, acc, "ads_reworked", {}) or {}
        for p in mine:
            done.pop(str(p["id"]), None)
        _save(db, acc, "ads_reworked", done)
        _save(db, acc, "ads_proposals", props)
        _audit_log(acc, "ads_run_rolled_back",
                   "Запуск №%s откачен: возвращено заголовков %d из %d" % (run_id, ok, len(mine)), "boris")
        log("откачено: %d из %d" % (ok, len(mine)))
    finally:
        db.close()




def _views_around(db, acc, item_id, since, days=7):
    """Просмотры и обращения по объявлению за N дней от даты since."""
    v = c = 0
    for back in range(0, days):
        d = (since + timedelta(days=back)).isoformat()
        snap = _load(db, acc, "daily_stats:" + d)
        if not snap:
            continue
        for it in (snap.get("items") or []):
            if str(it.get("id")) == str(item_id):
                v += int(it.get("views") or 0)
                c += int(it.get("contacts") or 0)
    return v, c


def measure_runs(acc, days=7, dry=True):
    """Замер результата: что дало изменение заголовка через неделю.

    Без этого шага автопилот — генератор изменений, а не автоматизация:
    неизвестно, помогло ли. Если стало хуже, предлагаем вернуть.
    """
    from app.api.avito import _audit_log
    db = SessionLocal()
    try:
        props = _load(db, acc, "ads_proposals", []) or []
        ready = []
        for p in props:
            if p.get("статус") != "применено" or p.get("замер"):
                continue
            try:
                applied = date.fromisoformat(str(p.get("применено"))[:10])
            except Exception:
                continue
            if (date.today() - applied).days < days:
                continue
            ready.append((p, applied))

        if not ready:
            log("нечего мерить: нет изменений старше %d дней" % days)
            return

        log("к замеру: %d изменений" % len(ready))
        better = worse = same = 0
        for p, applied in ready:
            was_v, was_c = _views_around(db, acc, p["id"], applied - timedelta(days=days), days)
            now_v, now_c = _views_around(db, acc, p["id"], applied, days)
            if now_v > was_v or now_c > was_c:
                verdict, better = "стало лучше", better + 1
            elif now_v < was_v or now_c < was_c:
                verdict, worse = "стало хуже", worse + 1
            else:
                verdict, same = "без изменений", same + 1

            p["замер"] = {"дата": time.strftime("%Y-%m-%d"), "было_просмотров": was_v,
                          "стало_просмотров": now_v, "было_обращений": was_c,
                          "стало_обращений": now_c, "вывод": verdict}
            log("  %s: %d -> %d просм., %d -> %d обращ. — %s"
                % (p["стало"][:32], was_v, now_v, was_c, now_c, verdict))
            if not dry:
                _audit_log(acc, "ads_effect_measured",
                           "«%s»: просмотры %d → %d, обращения %d → %d за %d дней. Вывод: %s%s"
                           % (p["стало"][:44], was_v, now_v, was_c, now_c, days, verdict,
                              ". Рекомендую вернуть прежний заголовок." if verdict == "стало хуже" else ""),
                           "boris")

        if not dry:
            _save(db, acc, "ads_proposals", props)
            _audit_log(acc, "ads_effect_summary",
                       "Замер через %d дней: лучше %d, хуже %d, без изменений %d."
                       % (days, better, worse, same), "boris")
        log("итог: лучше %d, хуже %d, без изменений %d" % (better, worse, same))
    finally:
        db.close()


def _main():
    a = sys.argv[1] if len(sys.argv) > 1 else _client_id("client_06")
    args = sys.argv[2:]
    dry = "--apply" not in args and "--yes" not in args
    if "--measure" in args:
        measure_runs(a, dry=dry)
    elif "--rollback" in args:
        rollback_run(a, args[args.index("--rollback") + 1], dry=dry)
    elif "--publish" in args:
        apply_run(a, args[args.index("--publish") + 1], dry=dry)
    else:
        rework(a, dry=dry)


if __name__ == "__main__":
    _main()
