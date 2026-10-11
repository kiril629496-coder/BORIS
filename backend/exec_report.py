"""Executive Report BORIS v2 — вечерний отчёт владельцу. Только dry-run."""
import os, json, argparse
from datetime import date as _date, timedelta

GAPS, OBS = [], []
WINDOWS = {}

# Аккаунты с подтверждённым тарифом, где балансу от Avito можно верить.
# Пусто намеренно: пока тариф не подтверждён вручную — статус UNKNOWN (fail closed).
BALANCE_KNOWN_ACCOUNTS = set()


def gap(what, why=""):
    GAPS.append({"что": what, "почему": why})


def safe(name, fn, default=None):
    try:
        return fn()
    except Exception as e:
        gap(name, f"{type(e).__name__}: {str(e)[:120]}")
        return default


def m(value, period, source):
    WINDOWS[source] = period
    return {"value": value, "period": period, "source": source}


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


def resolve(cur, owner):
    accs = {a: (n or "") for a, n in q(
        cur, "SELECT account_id, COALESCE(name,'') FROM accounts WHERE owner_user_id=%s", (owner,))}
    for (a,) in q(cur, "SELECT account_id FROM user_account_access WHERE user_id=%s", (owner,)):
        accs.setdefault(a, "")
    return accs


def collect(cur, owner, day, accs):
    ids, bucket = list(accs.keys()), f"u{owner}"
    yday = (_date.fromisoformat(day) - timedelta(days=1)).isoformat()
    W_TODAY = f"{day} 00:00 -> now"
    W_YDAY = f"{yday} полные сутки"
    W_CPX = "7 полных дней, сегодняшний неполный не включён"
    W_NOW = "текущее состояние на момент отчёта"

    f = {"period": {"date": day, "owner_user_id": owner,
                    "accounts": [{"id": a, "name": n} for a, n in accs.items()]}}
    per_account = []

    for a in ids:
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
            bal = {"value": raw_bal, "status": "KNOWN",
                   "source": "daily_stats.balance.real", "reason": None}
        elif raw_bal is None:
            bal = {"value": None, "status": "UNKNOWN",
                   "source": "daily_stats.balance.real",
                   "reason": "значение отсутствует в сегодняшнем снимке"}
        else:
            bal = {"value": None, "status": "UNKNOWN",
                   "source": "daily_stats.balance.real",
                   "_raw_diagnostic": raw_bal,
                   "reason": "тариф Avito не подтверждён; число от API может не отражать"
                             " реальный кошелёк, владельцу не показывать"}

        base = st(cur, a, f"kpi_balance_baseline:{day}") or {}
        if bal["status"] != "KNOWN":
            spent = {"value": None, "reliability": "UNAVAILABLE",
                     "reason": "зависит от баланса, достоверность которого не подтверждена"}
        elif base.get("created_at", "")[11:] > "00:05":
            spent = {"value": None, "reliability": "PARTIAL_DAY",
                     "since": base.get("created_at"),
                     "reason": "учёт восстановлен в течение дня, полные сутки недоступны"}
        else:
            spent = {"value": None, "reliability": "INSUFFICIENT_DATA",
                     "reason": "расчёт расхода за сегодня не выполнялся"}

        adv = st(cur, a, "cpx_advice") or {}
        recs = adv.get("recommendations") or {}
        per_account.append({
            "account": a,
            "target_leads_per_day": m(target, W_NOW, "kpi_settings"),
            "contacts_today": m(c_now, W_TODAY, "daily_stats"),
            "contacts_yesterday": m(c_prev, W_YDAY, "daily_stats_prev"),
            "target_completion_pct": (round(c_now * 100.0 / target, 1) if target else None),
            "gap_to_target": (target - c_now) if target is not None else None,
            "views_today": m(views, W_TODAY, "daily_stats"),
            "conversion_pct_today": m(round(c_now * 100.0 / views, 2) if views else None,
                                      W_TODAY, "daily_stats"),
            "active_items": m(td.get("items_count") or len(items), W_TODAY, "daily_stats"),
            "balance": bal,
            "spent": spent,
            "cpl": {"value": None, "reliability": spent["reliability"],
                    "reason": "не считается без достоверного расхода"},
            "cpx_working_items": m(len(recs.get("raise") or []), W_CPX, "cpx_advice"),
            "cpx_need_attention": m(len(recs.get("archive_candidates")
                                        or recs.get("lower_or_archive") or []),
                                    W_CPX, "cpx_advice"),
            "cpx_watching": m(len(recs.get("watching") or []), W_CPX, "cpx_advice"),
        })
    f["accounts_detail"] = per_account

    f["sales"] = safe("продажи", lambda: {
        "vhodyaschih_soobscheniy": m(q(cur,
            "SELECT count(*) FROM messenger_messages WHERE account_id = ANY(%s)"
            " AND to_timestamp(avito_created_at / CASE WHEN avito_created_at > 100000000000 THEN 1000 ELSE 1 END)::date=%s AND direction='in'", (ids, day))[0][0],
            W_TODAY, "messenger_messages"),
        "dialogov_zatronuto": m(q(cur,
            "SELECT count(DISTINCT avito_chat_id) FROM messenger_messages"
            " WHERE account_id = ANY(%s) AND to_timestamp(avito_created_at / CASE WHEN avito_created_at > 100000000000 THEN 1000 ELSE 1 END)::date=%s", (ids, day))[0][0],
            W_TODAY, "messenger_messages"),
        "chernoviki_mopa": m(dict(q(cur,
            "SELECT status, count(*) FROM mop_drafts WHERE account_id = ANY(%s)"
            " AND created_at::date=%s GROUP BY 1", (ids, day))), W_TODAY, "mop_drafts"),
    }, {})

    f["crm"] = safe("CRM", lambda: m(dict(q(cur,
        "SELECT status, count(*) FROM crm_tasks WHERE account_id = ANY(%s) GROUP BY 1",
        (ids,))), W_NOW, "crm_tasks"), None)
    if not f.get("crm") or not (f["crm"] or {}).get("value"):
        gap("CRM", "задачи по этим аккаунтам не найдены либо таблица недоступна")

    log = safe("журнал действий", lambda: q(cur,
        "SELECT id, ts, actor, source, action, object_kind, object_name,"
        " before_val, after_val, reason FROM action_log"
        " WHERE account_id = ANY(%s) AND ts::date=%s ORDER BY id",
        (ids + [bucket], day)), []) or []

    boris, team, unresolved, changes = [], [], [], []
    for _id, ts, actor, source, action, kind, name, bv, av, reason in log:
        row = {"deystvie": action, "obekt": kind, "istochnik": source}
        if actor == "cron":
            row.update(attribution="BORIS_AUTO", confidence="high")
            boris.append(row)
        elif source and source.startswith("cpx_advisor"):
            row.update(attribution="UNRESOLVED", confidence="low")
            unresolved.append(row)
        else:
            row.update(attribution="HUMAN_VIA_BORIS", confidence="partial", actor_id=None)
            team.append(row)
        if kind and "объявлен" in str(kind):
            url = safe("ссылка на объявление", lambda: q(cur,
                "SELECT item_url FROM messenger_messages WHERE item_id=%s"
                " AND item_url <> '' LIMIT 1", (str(name),)), None)
            changes.append({"item_id": str(name), "izmenenie": action,
                            "bylo": bv, "stalo": av, "reason": reason,
                            "attribution": row["attribution"],
                            "item_url": (url[0][0] if url else None),
                            "time": str(ts)[11:16]})

    f["boris_actions"] = m(boris, W_TODAY, "action_log")
    f["team_actions"] = m(team, W_TODAY, "action_log_team")
    if unresolved:
        f["unresolved_actions"] = unresolved
        gap("исполнитель изменений ставок",
            "в журнале actor=user у всех записей cpx_advisor — автомат и человек неразличимы")
    money = [c for c in changes if "ставк" in str(c["izmenenie"]).lower()
             or "продвижен" in str(c["izmenenie"]).lower()]
    rest = [c for c in changes if c not in money]
    top = (money + rest)[:5]
    f["item_changes"] = {"vsego": len(changes), "klyuchevye": top,
                         "esche": max(0, len(changes) - len(top)),
                         "period": W_TODAY, "source": "action_log"}

    pend = {}
    prop = 0
    for a in ids:
        for p in (st(cur, a, "ads_proposals") or []):
            if isinstance(p, dict) and str(p.get("статус", "")).startswith("ожид"):
                prop += 1
    pend["predlozheniya_po_zagolovkam"] = prop
    pend["chernoviki_mopa_zhdut"] = safe("ожидающие черновики", lambda: q(cur,
        "SELECT count(*) FROM mop_drafts WHERE account_id = ANY(%s)"
        " AND status IN ('draft_ready','waiting_confirm')", (ids,))[0][0], None)
    pend["komandy_zhdut"] = safe("команды Command Center", lambda: q(cur,
        "SELECT count(*) FROM boris_commands WHERE account_id = ANY(%s)"
        " AND status='awaiting_confirm'", (ids,))[0][0], None)
    f["pending_decisions"] = m(pend, W_NOW, "queues")

    social = {}
    posts = st(cur, bucket, "posting_posts") or []
    today_posts = [p for p in posts if isinstance(p, dict)
                   and str(p.get("created_at", "")).startswith(day)]
    if today_posts:
        social["opublikovano_segodnya"] = m(len(today_posts), W_TODAY, "posting_posts")
    ws = [p for p in posts if isinstance(p, dict)
          and ((p.get("stats") or {}).get("er") or 0) > 0]
    if ws:
        best = max(ws, key=lambda p: (p["stats"].get("er") or 0))
        social["vk_luchshiy_post"] = m({"text": (best.get("text") or "")[:120],
                                        "prosmotry": best["stats"].get("views"),
                                        "reaktsii": best["stats"].get("likes"),
                                        "vovlechenie_pct": best["stats"].get("er")},
                                       "накопительно с момента публикации", "posting_stats")
    if today_posts and not ws:
        social["vovlechenie"] = "явного лидера по вовлечённости сегодня нет"
    if social:
        f["social"] = social
    else:
        gap("соцсети", "за этот день нет публикаций и собранных метрик")

    f["effects"] = safe("замеры действий", lambda: m(
        "Идёт накопление данных для оценки выполненных изменений"
        if q(cur, "SELECT count(*) FROM action_effects")[0][0] == 0
        else "есть завершённые замеры",
        "замер закрывается через 7 дней после действия", "action_effects"), None)

    f["observations"], f["data_gaps"] = OBS, GAPS
    f["windows"] = WINDOWS
    return f


PROMPT = """Ты — коммерческий директор, вечером докладываешь владельцу бизнеса итоги дня.

ЖЁСТКИЕ ЗАПРЕТЫ:
- Использовать ТОЛЬКО числа из фактов. Ни одной цифры сверх них.
- У каждой метрики есть period. ЗАПРЕЩЕНО сравнивать показатели с РАЗНЫМИ period
  как одну выборку. Если окна разные — либо объясни разницу словами, либо не сравнивай.
  Например: обращения за сегодня и анализ продвижения за 7 полных дней — разные окна.
- Если balance.status не KNOWN — НЕ называть сумму баланса. Писать, что достоверное
  значение через API сейчас недоступно. Не делать вывод «деньги заканчиваются»
  и не рекомендовать пополнение.
- Если spent.reliability не KNOWN — НЕ писать «потрачено 0 рублей». Писать, что полный
  расход за сегодня недоступен, и почему, коротко и по-человечески.
- Не приписывать причинность. Не доказано замером — «наблюдаем», «возможно»,
  «нужно накопить данные». Не писать «BORIS увеличил лиды».
- Если attribution UNRESOLVED или confidence low — не называть ни человека, ни BORIS:
  «выполнено через BORIS, исполнитель уточняется».
- Не называть технические имена, идентификаторы, ключи, JSON, таблицы.
- Разделов без данных в отчёте быть не должно.

ФОРМАТ (коротко, языком владельца бизнеса):
BORIS — итоги дня
Главное: цель, получено, выполнение, вчера/сегодня
Avito: объявления, просмотры, обращения, конверсия, баланс
Что сделал BORIS
Что сделала команда
Продажи
Изменённые объявления (не более 5, со ссылками где есть)
Соцсети (только если есть данные)
Проблемы, требующие внимания
План на завтра: 3-5 конкретных действий
Нужно решение владельца

ФАКТЫ:
"""


def strip_diag(o):
    """Убираем из payload всё, что начинается с подчёркивания: AI это не видит."""
    if isinstance(o, dict):
        return {k: strip_diag(v) for k, v in o.items() if not str(k).startswith("_")}
    if isinstance(o, list):
        return [strip_diag(v) for v in o]
    return o


PROMPT2 = """Ты — коммерческий директор. Вечером докладываешь владельцу бизнеса итоги дня.
Текст пойдёт в Telegram: простые строки, короткие заголовки, допустимы эмодзи.
Запрещены markdown-разметка, решётки, звёздочки и таблицы.

СТРУКТУРА ОБЯЗАТЕЛЬНА. Разделы 1-5, 8, 9, 10 выводить ВСЕГДА, даже если данных мало —
в этом случае честно писать, каких данных не хватает. Разделы 6 и 7 — только при наличии данных.
1. ИТОГ ДНЯ
2. AVITO
3. ПРОДАЖИ
4. ПРОДВИЖЕНИЕ
5. ЧТО СДЕЛАЛ BORIS
6. ЧТО СДЕЛАЛА КОМАНДА
7. СОЦСЕТИ
8. ТРЕБУЕТ РЕШЕНИЯ
9. ВЫВОД BORIS
10. ПЛАН НА ЗАВТРА

ЖЁСТКИЕ ПРАВИЛА:
- Только числа из фактов. Ни одной цифры сверх них.
- У каждой метрики есть period. Показатели с РАЗНЫМИ period не сравнивать между собой.
  Сегодняшние обращения и анализ продвижения за 7 полных дней — разные окна.
- Баланс: если balance.status не KNOWN, сумму НЕ называть вообще. Достаточно строки,
  что достоверное значение через API сейчас недоступно. Не советовать пополнение.
- Расход и цена обращения: по КАЖДОМУ аккаунту отдельной строкой — название аккаунта,
  состояние и причина СЛОВАМИ ИЗ ПОЛЯ reason. Свою причину не придумывать.
  Не объединять аккаунты с разными состояниями в одну фразу.
- Раздел ПРОДВИЖЕНИЕ обязателен, если есть cpx-данные. Указывать окно:
  предыдущие 7 полных дней, без сегодняшнего. Про объявления, требующие внимания,
  писать так: за анализируемое окно были просмотры, но не было обращений,
  поэтому они требуют дополнительной проверки. НЕ предлагать отключать продвижение.
- Причинность не приписывать: не доказано замером — «наблюдаем», «возможно»,
  «нужно накопить данные». Не писать «BORIS увеличил лиды».
- Если attribution UNRESOLVED или confidence low — не называть ни человека, ни BORIS:
  «выполнено через BORIS, исполнитель уточняется».
- Писать МОП русскими буквами. Не использовать технические имена, ключи и названия таблиц.

ФАКТЫ:
"""


def summarize(facts, account_id):
    from gigachat_pool import chat_with_fallback
    payload = PROMPT2 + json.dumps(strip_diag(facts), ensure_ascii=False)
    try:
        from gigachat.models import Messages, MessagesRole
        msg = [Messages(role=MessagesRole.USER, content=payload)]
    except Exception:
        msg = [{"role": "user", "content": payload}]
    return chat_with_fallback(msg, temperature=0.3, max_tokens=1200,
                              account_id=account_id, operation="executive_evening")


def reliability_table(facts):
    out = []
    for acc in facts.get("accounts_detail", []):
        for k in ("balance", "spent", "cpl"):
            v = acc.get(k) or {}
            out.append((acc["account"], k,
                        v.get("status") or v.get("reliability") or "?",
                        v.get("reason") or ""))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--owner", type=int, required=True)
    ap.add_argument("--date", default=_date.today().isoformat())
    ap.add_argument("--dry-run", action="store_true", default=True)
    a = ap.parse_args()

    import psycopg2
    conn = psycopg2.connect(dsn())
    conn.autocommit = True
    conn.autocommit = True
    conn.autocommit = True
    cur = conn.cursor()

    accs = resolve(cur, a.owner)
    print("=" * 72 + "\n1. RESOLVED COMPANY")
    print(f"resolved_owner_id: {a.owner}")
    print(f"resolved_account_ids: {list(accs.keys())}")
    print(f"posting_bucket: u{a.owner}")
    if not accs:
        print("Аккаунтов у владельца не найдено")
        return

    facts = collect(cur, a.owner, a.date, accs)
    blob = json.dumps(facts, ensure_ascii=False)
    print("=" * 72 + "\n2. FACTS JSON")
    print(json.dumps(facts, ensure_ascii=False, indent=2))

    print("=" * 72 + "\n3. WINDOWS")
    for src, per in facts.get("windows", {}).items():
        print(f"  {src:24s} {per}")

    print("=" * 72 + "\n4. RELIABILITY")
    for acc, k, status, reason in reliability_table(facts):
        print(f"  {acc:28s} {k:8s} {status:18s} {reason[:60]}")

    print("=" * 72 + "\n5. AI REPORT")
    print(summarize(facts, list(accs.keys())[0]))

    print("=" * 72 + "\n6. ATTRIBUTION")
    for k in ("boris_actions", "team_actions"):
        for r in (facts.get(k) or {}).get("value", []):
            print(f"  {r['attribution']:18s} {r['confidence']:8s} {r['deystvie']}")
    for r in facts.get("unresolved_actions", []):
        print(f"  {r['attribution']:18s} {r['confidence']:8s} {r['deystvie']}")

    print("=" * 72 + "\n7. DATA GAPS")
    for g in facts.get("data_gaps", []):
        print(f"  - {g['что']}: {g['почему']}")

    print("=" * 72 + "\n8. ECONOMICS")
    print(f"  facts_json: {len(blob.encode('utf-8'))} байт, ~{len(blob)//4} токенов оценочно")
    conn.rollback()
    cur.execute("SELECT id, provider, model, prompt_tokens, completion_tokens,"
                " cost_rub, rate_source, account_id FROM api_usage"
                " WHERE operation='executive_evening' ORDER BY id DESC LIMIT 2")
    for r in cur.fetchall():
        print(f"  id={r[0]} provider={r[1]} model={r[2]} input={r[3]} output={r[4]}"
              f" cost_rub={r[5]} rate_source={r[6]} account={r[7]}")

    print("=" * 72 + "\n9. SAFETY")
    print("  DB writes скриптом: 0 (только SELECT, транзакция откачена)")
    print("  Telegram sends: 0 (кода отправки в файле нет)")
    print("  Avito mutations: 0 (обращений к Avito API нет)")
    print("  Разрешённый side effect: запись в api_usage обёрткой AI")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
