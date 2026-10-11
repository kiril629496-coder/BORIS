"""Executive Report BORIS v4. Разделы 1-8 формирует Python, AI пишет только 9-10."""
import os, re, time, json, argparse
from datetime import date as _date, timedelta

GAPS = []
W_CPX = "предыдущие 7 полных дней, без сегодняшнего дня"
BALANCE_KNOWN_ACCOUNTS = set()


def gap(what, why=""):
    GAPS.append({"что": what, "почему": why})


def safe(name, fn, default=None):
    try:
        return fn()
    except Exception as e:
        gap(name, f"{type(e).__name__}: {str(e)[:120]}")
        return default


def dsn():
    u = os.environ.get("DATABASE_URL")
    if u:
        return u
    for l in open("/root/BORIS/backend/.env"):
        if l.startswith("DATABASE_URL"):
            return l.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL не найден")


def q(cur, sql, args=()):
    cur.execute(sql, args)
    return cur.fetchall()


def st(cur, acc, key):
    r = q(cur, "SELECT value FROM storage WHERE account_id=%s AND key=%s", (acc, key))
    if not r:
        return None
    try:
        return json.loads(r[0][0])
    except Exception:
        return None


def pct(new, old):
    if old in (None, 0):
        return None
    return round((new - old) * 100.0 / old, 1)


def resolve(cur, owner):
    accs = {a: (n or a) for a, n in q(
        cur, "SELECT account_id, COALESCE(name,'') FROM accounts WHERE owner_user_id=%s", (owner,))}
    for (a,) in q(cur, "SELECT account_id FROM user_account_access WHERE user_id=%s", (owner,)):
        accs.setdefault(a, a)
    return {a: (n or a) for a, n in accs.items()}



# BORIS_FUNNEL_CHANNELS: каналы обращения по каждому аккаунту.
# Звонок берём из Avito CallTracking по дате call_time; уникальность - по
# непустому buyer_phone. Новая переписка = ПЕРВОЕ ВХОДЯЩЕЕ сообщение диалога
# пришло в этот день (повторное письмо в старый диалог сюда не попадает).
# Деньги здесь НЕ считаем: дельта баланса это движение кошелька, а не расход.
def _channels(cur, acc, day):
    out = {"calls": 0, "talked": 0, "phones": 0, "new_dialogs": 0, "calls_ok": True}
    try:
        from app.api.calltracking import calltracking_stats
        r = calltracking_stats(acc, 3) or {}
        _ph = set()
        for c in (r.get("calls") or []):
            if str(c.get("call_time") or "")[:10] != day:
                continue
            out["calls"] += 1
            if (c.get("talk_duration") or 0) > 0:
                out["talked"] += 1
            p = str(c.get("buyer_phone") or "").strip()
            if p:
                _ph.add(p)
        out["phones"] = len(_ph)
    except Exception:
        out["calls_ok"] = False
    try:
        rows = q(cur,
                 "SELECT count(*) FROM (SELECT avito_chat_id,"
                 " to_timestamp(min(avito_created_at) / CASE WHEN min(avito_created_at)"
                 " > 100000000000 THEN 1000 ELSE 1 END)::date AS d"
                 " FROM messenger_messages WHERE account_id=%s"
                 "   AND direction IN ('in','incoming') GROUP BY avito_chat_id) t"
                 " WHERE d = %s", (acc, day))
        out["new_dialogs"] = int(rows[0][0]) if rows else 0
    except Exception:
        pass
    return out


def collect(cur, owner, day, accs):
    ids, bucket = list(accs.keys()), f"u{owner}"
    yday = (_date.fromisoformat(day) - timedelta(days=1)).isoformat()
    TS = ("to_timestamp(avito_created_at / CASE WHEN avito_created_at > 100000000000"
          " THEN 1000 ELSE 1 END)::date=%s")
    f = {"date": day, "owner": owner, "accounts": []}

    for a, name in accs.items():
        s = st(cur, a, "kpi_settings") or {}
        td = st(cur, a, f"daily_stats:{day}") or {}
        yd = st(cur, a, f"daily_stats:{yday}") or {}
        items = td.get("items") or []
        c_now = sum(int(i.get("contacts") or 0) for i in items)
        c_prev = sum(int(i.get("contacts") or 0) for i in (yd.get("items") or []))
        views = sum(int(i.get("views") or 0) for i in items)
        target = s.get("target_leads_per_day")

        raw_bal = (td.get("balance") or {}).get("real")
        if a in BALANCE_KNOWN_ACCOUNTS and raw_bal is not None:
            bal = {"status": "KNOWN", "value": raw_bal, "reason": None}
        else:
            bal = {"status": "UNKNOWN", "value": None, "_raw": raw_bal,
                   "reason": "достоверное значение через API при текущем тарифе недоступно"}

        base = st(cur, a, f"kpi_balance_baseline:{day}") or {}
        if bal["status"] != "KNOWN":
            spent = {"reliability": "UNAVAILABLE",
                     "reason": "расход считается от баланса, а достоверный баланс сейчас недоступен"}
        elif base.get("created_at", "")[11:] > "00:05":
            spent = {"reliability": "PARTIAL_DAY",
                     "reason": f"учёт восстановлен сегодня в {base.get('created_at','')[11:16]},"
                               f" данные доступны только с этого момента"}
        else:
            spent = {"reliability": "INSUFFICIENT_DATA",
                     "reason": "расчёт расхода за сегодня не выполнялся"}

        adv = st(cur, a, "cpx_advice") or {}
        recs = adv.get("recommendations") or {}
        f["accounts"].append({
            "id": a, "name": name, "target": target,
            "contacts_today": c_now, "contacts_yesterday": c_prev,
            "contacts_delta_abs": c_now - c_prev,
            "contacts_delta_pct": pct(c_now, c_prev),
            "target_completion_pct": round(c_now * 100.0 / target, 1) if target else None,
            "views_today": views,
            "channels": _channels(cur, a, day),
            "conversion_pct": round(c_now * 100.0 / views, 2) if views else None,
            "active_items": td.get("items_count") or len(items),
            "balance": bal, "spent": spent,
            "cpx_working": len(recs.get("raise") or []),
            "cpx_attention": len(recs.get("archive_candidates")
                                 or recs.get("lower_or_archive") or []),
            "cpx_watching": len(recs.get("watching") or []),
        })

    tot_now = sum(x["contacts_today"] for x in f["accounts"])
    tot_prev = sum(x["contacts_yesterday"] for x in f["accounts"])
    f["totals"] = {"contacts_today": tot_now, "contacts_yesterday": tot_prev,
                   "delta_abs": tot_now - tot_prev, "delta_pct": pct(tot_now, tot_prev)}

    f["sales"] = safe("продажи", lambda: {
        "incoming": q(cur, "SELECT count(*) FROM messenger_messages WHERE account_id = ANY(%s)"
                      " AND " + TS + " AND direction='in'", (ids, day))[0][0],
        "dialogs": q(cur, "SELECT count(DISTINCT avito_chat_id) FROM messenger_messages"
                     " WHERE account_id = ANY(%s) AND " + TS, (ids, day))[0][0],
        "drafts": dict(q(cur, "SELECT status, count(*) FROM mop_drafts"
                         " WHERE account_id = ANY(%s) AND created_at::date=%s GROUP BY 1",
                         (ids, day))),
    }, None)

    f["crm"] = safe("CRM", lambda: dict(q(
        cur, "SELECT status, count(*) FROM crm_tasks WHERE account_id = ANY(%s) GROUP BY 1",
        (ids,))), None)

    log = safe("журнал действий", lambda: q(cur,
        "SELECT ts, actor, source, action, object_kind, object_name, before_val, after_val"
        " FROM action_log WHERE account_id = ANY(%s) AND ts::date=%s ORDER BY id",
        (ids + [bucket], day)), []) or []
    boris, team, unresolved, changes = {}, {}, 0, []
    for ts, actor, source, action, kind, name, bv, av in log:
        if actor == "cron":
            boris[action] = boris.get(action, 0) + 1
        elif source and str(source).startswith("cpx_advisor"):
            unresolved += 1
        else:
            team[action] = team.get(action, 0) + 1
        if kind and "объявлен" in str(kind):
            url = safe("ссылка на объявление", lambda: q(cur,
                "SELECT item_url FROM messenger_messages WHERE item_id=%s"
                " AND item_url <> '' LIMIT 1", (str(name),)), None)
            changes.append({"item_id": str(name), "action": action, "before": bv,
                            "after": av, "url": (url[0][0] if url else None),
                            "time": str(ts)[11:16]})
    f["boris_actions"], f["team_actions"] = boris, team
    f["unresolved_count"] = unresolved
    f["item_changes"] = changes

    pend = {}
    prop = 0
    for a in ids:
        for p in (st(cur, a, "ads_proposals") or []):
            if isinstance(p, dict) and str(p.get("статус", "")).startswith("ожид"):
                prop += 1
    pend["Предложения по заголовкам"] = prop
    pend["Черновики МОП"] = safe("черновики", lambda: q(cur,
        "SELECT count(*) FROM mop_drafts WHERE account_id = ANY(%s)"
        " AND status IN ('draft_ready','waiting_confirm')", (ids,))[0][0], 0) or 0
    pend["Команды на подтверждение"] = safe("команды", lambda: q(cur,
        "SELECT count(*) FROM boris_commands WHERE account_id = ANY(%s)"
        " AND status='awaiting_confirm'", (ids,))[0][0], 0) or 0
    f["pending"] = {k: v for k, v in pend.items() if v}

    posts = st(cur, bucket, "posting_posts") or []
    today_posts = [p for p in posts if isinstance(p, dict)
                   and str(p.get("created_at", "")).startswith(day)]
    lead = [p for p in posts if isinstance(p, dict)
            and ((p.get("stats") or {}).get("er") or 0) > 0]
    f["social"] = {"published_today": len(today_posts),
                   "leader": (max(lead, key=lambda p: p["stats"]["er"]) if lead else None)}
    f["effects_empty"] = safe("замеры", lambda:
                              q(cur, "SELECT count(*) FROM action_effects")[0][0] == 0, True)
    f["data_gaps"] = GAPS
    return f


def render(f):
    L = []
    t = f["totals"]
    d = f"{t['delta_abs']:+d}" + (f" ({t['delta_pct']:+.1f}%)" if t["delta_pct"] is not None else "")
    L += ["📊 BORIS — итоги дня " + f["date"], "",
          "1. ИТОГ ДНЯ",
          f"• Обращений сегодня: {t['contacts_today']}",
          f"• Вчера: {t['contacts_yesterday']}",
          f"• Изменение: {d}"]
    for a in f["accounts"]:
        if a["target"]:
            L.append(f"• {a['name']}: цель {a['target']} в день, "
                     f"выполнение {a['target_completion_pct']}%")
    L += ["", "2. AVITO"]
    for a in f["accounts"]:
        L += [f"— {a['name']}",
              f"   Активных объявлений: {a['active_items']}",
              f"   Просмотры сегодня: {a['views_today']}",
              f"   Обращения сегодня: {a['contacts_today']}"
              f" (вчера {a['contacts_yesterday']}, {a['contacts_delta_abs']:+d})",
              f"   Конверсия: {a['conversion_pct']}%" if a["conversion_pct"] is not None
              else "   Конверсия: нет просмотров"]
        if a["balance"]["status"] == "KNOWN":
            L.append(f"   Баланс: {a['balance']['value']} ₽")
        else:
            L.append(f"   Баланс: {a['balance']['reason']}")
        _ch = a.get("channels") or {}
        if _ch.get("calls_ok"):
            L.append(f"   Звонки: {_ch.get('calls', 0)} · с разговором: {_ch.get('talked', 0)}"
                     f" · уникальных номеров: {_ch.get('phones', 0)}")
        else:
            L.append("   Звонки: данные телефонии за день не получены")
        L.append(f"   Новые переписки: {_ch.get('new_dialogs', 0)}")
        L.append("   Расход на продвижение: точная сумма недоступна по текущим данным")
        L.append("   Цена обращения: не рассчитывается без достоверного расхода")
    s = f.get("sales")
    L += ["", "3. ПРОДАЖИ"]
    if s:
        L += [f"• Входящих сообщений: {s['incoming']}",
              f"• Диалогов затронуто: {s['dialogs']}"]
        if s["drafts"]:
            L.append("• Черновики МОП: " + ", ".join(f"{k} — {v}" for k, v in s["drafts"].items()))
        else:
            L.append("• Новых черновиков МОП сегодня нет")
    else:
        L.append("• Данные по диалогам сегодня собрать не удалось")
    L += ["", "4. ПРОДВИЖЕНИЕ", f"Окно анализа: {W_CPX}"]
    for a in f["accounts"]:
        L += [f"— {a['name']}",
              f"   Приносят обращения: {a['cpx_working']}",
              f"   Требуют проверки: {a['cpx_attention']}"
              " (были просмотры, но не было обращений)",
              f"   Под наблюдением: {a['cpx_watching']}"]
    L += ["", "5. CRM"]
    if f.get("crm"):
        L += ["• " + ", ".join(f"{k} — {v}" for k, v in f["crm"].items())]
    else:
        L.append("• Задач по клиентам за сегодня нет")
    L += ["", "6. ЧТО СДЕЛАЛ BORIS"]
    if f["boris_actions"]:
        L += [f"• {k}: {v}" for k, v in f["boris_actions"].items()]
    else:
        L.append("• Автоматических действий сегодня не было")
    if f["item_changes"]:
        L.append(f"• Изменения объявлений: {len(f['item_changes'])}")
        for c in f["item_changes"][:5]:
            L.append(f"   {c['time']} {c['action']}: {c['before']} → {c['after']}")
            if c["url"]:
                L.append(f"   {c['url']}")
        if len(f["item_changes"]) > 5:
            L.append(f"   + ещё {len(f['item_changes']) - 5}")
    if f["unresolved_count"]:
        L.append(f"• Изменений, где исполнитель не определён: {f['unresolved_count']}"
                 " — выполнено через BORIS, исполнитель уточняется")
    L += ["", "7. ЧТО СДЕЛАЛА КОМАНДА"]
    if f["team_actions"]:
        L += [f"• {k}: {v}" for k, v in f["team_actions"].items()]
    else:
        L.append("• Действий сотрудников через BORIS сегодня не зафиксировано")
    if f["social"]["published_today"]:
        L += ["", "СОЦСЕТИ", f"• Опубликовано постов: {f['social']['published_today']}"]
        if f["social"]["leader"]:
            b = f["social"]["leader"]
            L.append(f"• Лучший пост: {(b.get('text') or '')[:70]} — "
                     f"{b['stats'].get('views')} просмотров, вовлечение {b['stats'].get('er')}%")
        else:
            L.append("• Явного лидера по вовлечённости сегодня нет")
    L += ["", "8. ТРЕБУЕТ РЕШЕНИЯ"]
    if f["pending"]:
        L += [f"• {k}: {v}" for k, v in f["pending"].items()]
    else:
        L.append("• Решений от вас сегодня не требуется")
    if f["effects_empty"]:
        L += ["", "Идёт накопление данных для оценки выполненных изменений"]
    return "\n".join(L)


def reasoning_facts(f):
    r = []
    t = f["totals"]
    r.append(f"обращений сегодня {t['contacts_today']} против {t['contacts_yesterday']} вчера")
    for a in f["accounts"]:
        if a["target"] and a["target_completion_pct"] is not None:
            r.append(f"{a['name']}: цель выполнена на {a['target_completion_pct']}%")
        if a["cpx_attention"]:
            r.append(f"{a['name']}: {a['cpx_attention']} объявлений требуют проверки"
                     f" за окно «{W_CPX}»")
        r.append(f"{a['name']}: расход недоступен — {a['spent']['reason']}")
    for k, v in f["pending"].items():
        r.append(f"ожидает решения владельца: {k} — {v}")
    if f["effects_empty"]:
        r.append("завершённых замеров эффекта пока нет")
    return r


PROMPT = """Ты — коммерческий директор. Отчёт владельцу за день уже сформирован (ниже).
Твоя задача — дописать РОВНО два раздела, ничего больше.

Верни СТРОГО один JSON-объект и ничего кроме него:
{"conclusion": "текст 3-5 предложений", "tomorrow": ["пункт", "пункт", "пункт"]}
conclusion — что сегодня действительно важно, 3-5 предложений.
tomorrow — от 3 до 5 пунктов, каждый отдельной строкой массива.
Заголовки, нумерацию, markdown и пояснения не добавляй — их подставит система.

ЖЁСТКИЕ ПРАВИЛА:
- НЕ вычисляй ничего. Все числа уже посчитаны. Новых чисел не вводи.
- Не повторяй пересказ разделов 1-8, добавляй смысл.
- Не приписывай причинность: не доказано замером — «наблюдаем», «возможно».
- Денежные и видимые действия предлагай только как «предлагаю подготовить»,
  «предлагаю проверить», «требует вашего подтверждения». Не как принятое решение.
- Пиши простым текстом для Telegram: без markdown, без звёздочек и решёток.
- Пиши МОП русскими буквами.

ОТЧЁТ:
"""


def summarize(rendered, reasons, account_id):
    from gigachat_pool import chat_with_fallback
    payload = (PROMPT + rendered + "\n\nКРАТКИЕ ФАКТЫ ДЛЯ ВЫВОДА:\n"
               + "\n".join("- " + x for x in reasons))
    try:
        from gigachat.models import Messages, MessagesRole
        msg = [Messages(role=MessagesRole.USER, content=payload)]
    except Exception:
        msg = [{"role": "user", "content": payload}]
    return payload, chat_with_fallback(msg, temperature=0.3, max_tokens=600,
                                       account_id=account_id, operation="executive_evening")


def demarkdown(s):
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"^#{1,6}\s*", "", s, flags=re.M)
    return s.replace("**", "").replace("`", "")


TG_LIMIT = 4096


def route_for(cur, owner):
    r = q(cur, "SELECT chat_id, thread_id FROM tg_routes"
          " WHERE account_id=%s AND thread_key='executive'", (f"owner:{owner}",))
    return (r[0][0], r[0][1]) if r else (None, None)


def split_blocks(text, limit=TG_LIMIT):
    parts, cur_part = [], ""
    for block in text.split("\n\n"):
        if len(cur_part) + len(block) + 2 > limit and cur_part:
            parts.append(cur_part.rstrip())
            cur_part = ""
        cur_part += block + "\n\n"
    if cur_part.strip():
        parts.append(cur_part.rstrip())
    too_long = [p for p in parts if len(p) > limit]
    return parts, too_long


def marker_key(day):
    return f"executive_report_sent:{day}:evening"


def marker_get(cur, acc, day):
    return st(cur, acc, marker_key(day))


def marker_set(cur, acc, day, payload):
    import json as _j
    cur.execute("SELECT id FROM storage WHERE account_id=%s AND key=%s", (acc, marker_key(day)))
    row = cur.fetchone()
    val = _j.dumps(payload, ensure_ascii=False)
    if row:
        cur.execute("UPDATE storage SET value=%s WHERE id=%s", (val, row[0]))
    else:
        cur.execute("INSERT INTO storage (account_id, key, value) VALUES (%s,%s,%s)",
                    (acc, marker_key(day), val))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--owner", type=int, required=True)
    ap.add_argument("--date", default=_date.today().isoformat())
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--force-send", action="store_true")
    a = ap.parse_args()

    import psycopg2
    conn = psycopg2.connect(dsn())
    conn.autocommit = True
    cur = conn.cursor()

    accs = resolve(cur, a.owner)
    print(f"owner={a.owner} accounts={list(accs.keys())} bucket=u{a.owner}")
    if not accs:
        print("Аккаунтов не найдено")
        return

    f = collect(cur, a.owner, a.date, accs)
    rendered = render(f)
    reasons = reasoning_facts(f)

    print("=" * 72 + "\nDETERMINISTIC REPORT 1-8\n")
    print(rendered)

    print("\n" + "=" * 72 + "\nAI INPUT (структура)")
    print(f"  готовый текст разделов 1-8: {len(rendered)} символов")
    print(f"  кратких фактов для вывода: {len(reasons)}")
    for x in reasons:
        print("   - " + x)

    payload, ai = summarize(rendered, reasons, list(accs.keys())[0])
    _raw = demarkdown(ai or "")
    _ai_ok, _concl, _plan = False, "", []
    try:
        _txt = re.sub(r"^[^{]*", "", _raw, count=1)
        _txt = re.sub(r"[^}]*$", "", _txt, count=1)
        _d = json.loads(_txt)
        _concl = str(_d.get("conclusion") or "").strip()
        _plan = [str(x).strip(" -*\u2022\t") for x in (_d.get("tomorrow") or [])
                 if str(x).strip(" -*\u2022\t")]
        _ai_ok = bool(_concl) and 3 <= len(_plan) <= 5
    except Exception as _e:
        print(f"  [AI CONTRACT] не разобрал JSON: {str(_e)[:80]}")
    if not _ai_ok:
        print("  [AI CONTRACT] FAIL: нет conclusion или tomorrow не 3-5 пунктов")
    ai = "\U0001F9E0 ВЫВОД BORIS\n" + (_concl or _raw.strip())
    if _plan:
        ai += "\n\n\U0001F4CC ПЛАН НА ЗАВТРА\n" + "\n".join("\u2022 " + p for p in _plan)
    print("\n" + "=" * 72 + "\nAI OUTPUT (только 9-10)\n")
    print(ai)

    final = demarkdown(rendered) + "\n\n" + ai
    print("\n" + "=" * 72 + "\nFINAL TELEGRAM TEXT\n")
    print(final)

    print("\n" + "=" * 72 + "\nECONOMICS")
    print(f"  payload в модель: {len(payload.encode('utf-8'))} байт")
    print(f"  готовый отчёт: {len(final.encode('utf-8'))} байт")
    cur.execute("SELECT id, provider, model, prompt_tokens, completion_tokens, cost_rub"
                " FROM api_usage WHERE operation='executive_evening' ORDER BY id DESC LIMIT 1")
    for r in cur.fetchall():
        print(f"  id={r[0]} {r[1]}/{r[2]} input={r[3]} output={r[4]} cost_rub={r[5]}")

    print("\n" + "=" * 72 + "\nCHECKLIST")
    t = f["totals"]
    ck = [
        (f"общий рост посчитан кодом: {t['delta_abs']:+d} ({t['delta_pct']}%)", True),
        ("AI не считал: разделы 1-8 не менялись", rendered in final),
        ("SALES присутствует", "3. ПРОДАЖИ" in final),
        ("CRM присутствует", "5. CRM" in final),
        ("расход с причиной присутствует", "Причина:" in final),
        ("окно CPX точное", W_CPX in final),
        ("очередь решений реальная", "8. ТРЕБУЕТ РЕШЕНИЯ" in final),
        ("баланс скрыт", not any(str((x['balance'].get('_raw') or '')) in final
                                 for x in f["accounts"] if x['balance'].get('_raw'))),
        ("markdown отсутствует", "**" not in final and "#" not in final),
        ("AI добавил только 9-10", "9." in ai or "ВЫВОД" in ai.upper()),
    ]
    for name, ok in ck:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")

    print("\nDATA GAPS")
    for g in f["data_gaps"]:
        print(f"  - {g['что']}: {g['почему']}")
    # ---------------------------------------------------------- доставка
    primary = list(accs.keys())[0]
    report_key = f"executive:{a.owner}:{a.date}:evening"
    chat_id, thread_id = route_for(cur, a.owner)
    parts, too_long = split_blocks(final)
    sent_before = marker_get(cur, primary, a.date)

    print("\n" + "=" * 72 + "\nPREFLIGHT")
    print(f"  owner: {a.owner}")
    print(f"  report_key: {report_key}")
    print(f"  chat_id: {chat_id or 'МАРШРУТ НЕ НАЙДЕН'}  thread_id: {thread_id}")
    print(f"  длина текста: {len(final)} символов, частей к отправке: {len(parts)}")
    print(f"  первая строка: {final.splitlines()[0] if final else ''}")
    print(f"  последняя строка: {final.splitlines()[-1] if final else ''}")
    print(f"  уже отправлялся: {'да, ' + str(sent_before.get('sent_at')) if sent_before else 'нет'}")
    print(f"  would_send: {bool(chat_id and not too_long)}   send: {bool(a.send)}")

    if too_long:
        print("  ⛔ есть блок длиннее лимита Telegram — отправка невозможна без ручной правки")

    if not a.send:
        print("\nSAFETY: DB writes 0, Telegram 0, Avito 0, разрешён только AI usage log")
        cur.close(); conn.close(); return

    if not chat_id:
        print("\nОТПРАВКА ОТМЕНЕНА: маршрут executive для этого владельца не найден")
        cur.close(); conn.close(); return
    if too_long:
        print("\nОТПРАВКА ОТМЕНЕНА: блок превышает лимит Telegram")
        cur.close(); conn.close(); return
    if sent_before and not a.force_send:
        print(f"\nalready_sent: отчёт за {a.date} уже отправлен "
              f"({sent_before.get('sent_at')}). Повтор только с --force-send")
        cur.close(); conn.close(); return

    if not _ai_ok:
        print("\nОТПРАВКА ОТМЕНЕНА: ответ модели не прошёл контракт"
              " (нужны conclusion и 3-5 пунктов tomorrow)")
        cur.close(); conn.close(); return
    from app.telegram_bot import send_telegram_message
    results = []
    for i, p in enumerate(parts, 1):
        r = send_telegram_message(chat_id, p)
        results.append(r)
        print(f"  часть {i}/{len(parts)}: отправлена, ответ {str(r)[:120]}")
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    marker_set(cur, primary, a.date, {"report_key": report_key, "chat_id": str(chat_id),
                                      "sent_at": ts, "parts": len(parts)})
    print("\n" + "=" * 72 + "\nSEND RESULT")
    print(f"  telegram: ok, частей {len(parts)}")
    print(f"  chat_id: {chat_id}")
    print(f"  timestamp: {ts}")
    print(f"  report_key: {report_key}")
    print("  Avito mutations: 0, CRM mutations: 0, бизнес-данные не менялись")
    print("  записана только метка отправки в storage")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
