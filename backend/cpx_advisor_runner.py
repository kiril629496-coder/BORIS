"""
Часовой раннер Бориса-Советника по ставкам.
Находит аккаунты с заданным KPI и дёргает эндпоинт Советника (вся работа с БД — внутри бэкенда).
БД читаем напрямую через psycopg2 с DSN из .env (НЕ через app.db.session — у него другие креды при прямом запуске).
Без --apply только пересчитывает рекомендации и закрывает созревшие измерения. С --apply выполняет только разрешённые CPX-действия через штатные guards. Запуск: cron раз в час.
"""
import json, os, sys, uuid, httpx, psycopg2, time, fcntl
from datetime import datetime

APPLY = "--apply" in sys.argv
ACCOUNT_FILTER = None
MAX_ACTIONS_OVERRIDE = None
if "--account" in sys.argv:
    try:
        ACCOUNT_FILTER = sys.argv[sys.argv.index("--account") + 1].strip() or None
    except (ValueError, IndexError):
        raise SystemExit("--account требует account_id")
if "--max-actions" in sys.argv:
    try:
        MAX_ACTIONS_OVERRIDE = max(1, min(10, int(sys.argv[sys.argv.index("--max-actions") + 1])))
    except (ValueError, IndexError):
        raise SystemExit("--max-actions требует целое 1..10")
MAX_ITEMS_PER_CYCLE = 5
# MONEY_SAFE_PORTFOLIO_V2: paid promotion is an exception, not the default.
# At most 10% of the portfolio may be in the hourly high-bid cohort, and only
# proven converters can enter it. The remaining 90% stay at minimum/no promo.
HIGH_BID_SHARE = 0.10
MIN_PROVEN_CONTACTS_7D = 1
MIN_PROVEN_VIEWS_7D = 20
# A raise is allowed only when there is enough evidence. One contact on one
# accidental view is not a statistically useful reason to spend more.
MIN_PROVEN_DAYS = 3
# MICRO_CONVERTER_DEEP_HEADROOM_V1:
# Exception is intentionally narrow: one already-active cheap paid converter per
# cycle may be tested before reaching the normal 20-view sample when its observed
# conversion is strong and account CPL has very deep headroom. Final apply_one
# still enforces +10%, fresh spend/stats, budget, balance, hard cap and measurement.
MICRO_PROVEN_MAX_BID_RUB = 10.0
MICRO_PROVEN_MIN_VIEWS_7D = 5
MICRO_PROVEN_MIN_CONVERSION_PCT = 10.0
MICRO_PROVEN_MAX_CPL_SHARE = 0.50
MICRO_PROVEN_MAX_PER_CYCLE = 1
AUTO_EXECUTABLE = [("raise", "raise")]

# MARKETER_WHOLE_RUBLE_STEP_GRANULARITY_V1:
# Avito rejects bidPenny values that are not whole rubles. Before scheduling a
# raise, prove that even the maximum allowed autonomous +10% step can reach a
# strictly larger whole-ruble value. Otherwise apply_one would correctly return
# skipped_bid_unchanged and the planner would waste the hourly slot forever.
def _whole_ruble_raise_possible(current_bid_rub, step_pct=10):
    try:
        cur = int(round(float(current_bid_rub) * 100.0))
        if cur <= 0:
            return False
        step = max(1, int(round(cur * int(step_pct) / 100.0)))
        candidate = ((cur + step) // 100) * 100
        return candidate > cur
    except Exception:
        return False
RUN_ID = uuid.uuid4().hex[:12]
LOCK_PATH = "/root/BORIS/backend/run/cpx_advisor_runner.lock"
INTERNAL_API_BASES = tuple(
    x.strip().rstrip("/") for x in os.environ.get(
        "BORIS_INTERNAL_API_BASES",
        "http://127.0.0.1:8000,http://127.0.0.1:8001",
    ).split(",") if x.strip()
) or ("http://127.0.0.1:8000", "http://127.0.0.1:8001")


def _rid(account_id: str) -> str:
    """request_id уникален для пары прогон+аккаунт.
    Общий RUN_ID приводил к тому, что count_actions_run считал действия
    всех аккаунтов вместе, и max_actions_run мандата работал как одна
    квота на прогон: кто раньше в списке — тот и забирал её."""
    import hashlib
    return RUN_ID[:6] + hashlib.md5(str(account_id).encode("utf-8")).hexdigest()[:6]


def _cycle_permissions_after_capacity(existing_ops, capacity):
    """Return same-cycle money permissions after mandate self-heal.

    CAPACITY_SAME_CYCLE_PERMISSION_REFRESH_V1: the runner snapshots active
    mandates before iterating accounts. Capacity self-heal can create, upgrade,
    downgrade or revoke a mandate after that snapshot. Never keep stale pre-heal
    permissions. A successful capacity result is authoritative for this cycle;
    blocked/error results fail closed to no money operations.
    """
    existing=set(existing_ops or ())
    capacity=capacity or {}
    status=str(capacity.get("status") or "").lower()
    if status in {"created","upgraded","reused"}:
        returned=capacity.get("allowed_operations")
        return set(returned or existing)
    return set()


def _nonmoney_runtime_mode(reason: str, summary: str = "") -> str:
    """Always materialize a diagnostic mode when no money plan can run."""
    reason=str(reason or "diagnostics_only").strip()
    summary=str(summary or "").strip()
    suffix=(f"; {summary[:240]}" if summary else "")
    return (
        "NON_MONEY_DIAGNOSTICS: денежное действие не выполняется; "
        f"причина {reason}{suffix}; BORIS продолжает безопасную диагностику"
    )


def _dsn():
    for l in open("/root/BORIS/backend/.env"):
        if l.startswith("DATABASE_URL"):
            return l.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL не найден в .env")


def _save_runtime(cur, conn, account_id, payload):
    """Account-scoped heartbeat in existing storage; no second scheduler/state engine."""
    try:
        value = json.dumps(payload, ensure_ascii=False)
        cur.execute("SELECT id FROM storage WHERE account_id=%s AND key='virtual_marketer_runtime' ORDER BY id DESC LIMIT 1", (account_id,))
        row = cur.fetchone()
        if row:
            cur.execute("UPDATE storage SET value=%s WHERE id=%s", (value, row[0]))
        else:
            cur.execute("INSERT INTO storage(account_id,key,value) VALUES(%s,'virtual_marketer_runtime',%s)", (account_id, value))
        conn.commit()
    except Exception as exc:
        conn.rollback()
        print(f"    runtime heartbeat error: {str(exc)[:120]}")


def _request_with_retry(method, url, *, attempts=3, **kwargs):
    """Local HA request with bounded retries and money-write uncertainty guard.

    Read-only calls may fail over/retry after transient 5xx or read timeout.
    A money POST may fail over only when the connection was never established.
    After any HTTP response or a read timeout the backend may already have
    executed the write, so repeating that POST would risk a duplicate bid step.
    """
    from urllib.parse import urlsplit
    verb = str(method or "GET").upper()
    read_only = verb in {"GET", "HEAD", "OPTIONS"}
    parsed = urlsplit(url)
    suffix = parsed.path + (("?" + parsed.query) if parsed.query else "")
    bases = INTERNAL_API_BASES if parsed.hostname in {"127.0.0.1", "localhost"} else (f"{parsed.scheme}://{parsed.netloc}",)
    last = None
    for attempt in range(1, attempts + 1):
        for base in bases:
            target = base + suffix
            try:
                response = httpx.request(verb, target, **kwargs)
                if response.status_code < 500 or not read_only:
                    return response
                last = RuntimeError(f"HTTP {response.status_code}")
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                # Connection was not established; no request was accepted.
                last = exc
                continue
            except httpx.ReadTimeout as exc:
                if not read_only:
                    raise
                last = exc
                continue
        if attempt < attempts:
            time.sleep(2 * attempt)
    if last:
        raise last
    raise RuntimeError("request failed")


def _build_plan(acc, recs, advice=None):
    """Что делать сегодня: догонять или экономить.

    Отстаём  -> поднимаем ставки у сильных объявлений (intraday-отбор).
    С запасом-> снижаем там, где переплачиваем (советник).
    Ровно    -> ничего, рано судить.
    Темп задаёт мандат: max_actions_run за прогон, мелкими шагами.
    """
    try:
        sys.path.insert(0, "/root/BORIS/backend")
        from app.db.session import SessionLocal
        from app.services.intraday import select_intraday_raise_candidates
        db = SessionLocal()
        try:
            r = select_intraday_raise_candidates(db, acc)
        finally:
            db.close()
    except Exception as e:
        print(f"    intraday недоступен: {str(e)[:90]}")
        return [], "ошибка", {"decision":"ERROR","reason":"intraday_unavailable","error":str(e)[:160]}
    pace = (r.get("kpi_pace") or {}).get("state")
    status_counts = {}
    for _c in (r.get("top") or []):
        _st = str(_c.get("status") or "unknown")
        status_counts[_st] = status_counts.get(_st, 0) + 1
    diagnostics = {
        "decision": r.get("decision"),
        "pace": pace,
        "selected_for_execution": int(r.get("selected_for_execution") or 0),
        "candidates_total": int(r.get("candidates_total") or 0),
        "status_counts": status_counts,
        "position_signal_required": True,
    }
    # MARKETER_RUNNER_PROVIDER_DAY_LAG_HOLD_V1:
    # Advisor exposes whether Avito item counters already belong to the current
    # Moscow marketing day. A fresh new-day spend snapshot plus yesterday's item
    # counters is diagnostic-only; do not turn it into CPL=0/PUSH MAX.
    _advice_fact = (advice or {}).get("fact") if isinstance(advice, dict) else {}
    _advice_fact = _advice_fact if isinstance(_advice_fact, dict) else {}
    if _advice_fact.get("item_signal_current_day") is False:
        diagnostics.update({
            "planned_after_guard": 0,
            "proven_after_guard": 0,
            "micro_proven_after_guard": 0,
            "reason": "provider_stats_day_lagged",
            "next_action": "wait_for_provider_stats_day_then_retry_automatically",
        })
        return [], (
            "PROVIDER_STATS_WAIT: Avito уже открыл новый денежный день, "
            "но статистика объявлений ещё относится к предыдущему дню; "
            "BORIS ничего не повышает и повторит проверку автоматически"
        ), diagnostics

    # MARKETER_ACCOUNT_MEASUREMENT_BACKLOG_PLAN_HOLD_V1:
    # Final apply_one is the authority, but planner truth should stop earlier:
    # when both causal measurement slots are occupied, do not advertise a PUSH
    # that the final money boundary must reject.
    _measurement_cycle = (advice or {}).get("measurement_cycle") if isinstance(advice, dict) else {}
    _measurement_cycle = _measurement_cycle if isinstance(_measurement_cycle, dict) else {}
    _compaction = _measurement_cycle.get("backlog_compaction") if isinstance(_measurement_cycle.get("backlog_compaction"), dict) else {}
    try:
        _measurement_waiting = int(_measurement_cycle.get("waiting") or 0)
        _measurement_cap = int(_compaction.get("cap") or 2)
    except Exception:
        _measurement_waiting, _measurement_cap = 0, 2
    if _measurement_waiting >= max(1, _measurement_cap):
        diagnostics.update({
            "planned_after_guard": 0,
            "proven_after_guard": 0,
            "micro_proven_after_guard": 0,
            "reason": "account_measurement_backlog_wait",
            "measurement_waiting": _measurement_waiting,
            "measurement_cap": _measurement_cap,
            "next_action": "measurement_sweep_then_retry_automatically",
        })
        return [], (
            f"MEASUREMENT_WAIT: заняты {_measurement_waiting}/{_measurement_cap} "
            "слотов денежных измерений; BORIS ждёт доказательства и возобновит "
            "повышения автоматически"
        ), diagnostics
    cap = int((r.get("mandate") or {}).get("max_actions_run") or MAX_ITEMS_PER_CYCLE)
    if r.get("decision") == "RAISE_CANDIDATES":
        top = [c for c in (r.get("top") or []) if c.get("status") == "eligible"]
        n = int(r.get("selected_for_execution") or 0) or cap
        headroom = (r.get("cpl_headroom") or {})
        pace_data = r.get("kpi_pace") or {}
        target = float(pace_data.get("target") or 0)
        actual = float(pace_data.get("actual") or 0)
        expected = float(pace_data.get("expected_by_now") or 0)
        elapsed_share = float(pace_data.get("elapsed_share") or 0)
        # KPI_EMERGENCY_REANIMATION_V1: when the account is materially behind
        # the time-weighted daily plan, use the full available proven-converter
        # set this hour instead of a conservative partial batch. Execution still
        # goes through the existing 20% bid-step/maxBid/daily money guards.
        emergency = (
            pace == "BEHIND" and target > 0 and actual < target and
            ((expected - actual) >= 1.5 or (elapsed_share >= 0.60 and actual < target * 0.50))
        )
        # Жёсткий денежный guard: повышаем только доказанные объявления.
        # Никакого rescue через объявления без лидов: сначала контент/заголовок/
        # органическая проверка, потом деньги.
        # Preserve the advisor's completed-day evidence: intraday ranking may
        # intentionally carry only compact historical fields.
        _advice_raise_by_id = {
            str(it.get("id")): it for it in (recs.get("raise") or [])
            if isinstance(it, dict) and it.get("id") is not None
        }
        proven = []
        micro_proven_added = 0
        granularity_skipped = 0
        for c in top:
            # MARKETER_PROVEN_ACTIVE_BID_ONLY_V1: ordinary winner/KPI scaling may
            # only operate on a currently confirmed paid promotion. Native/read-only
            # inventory with no confirmed current bid is handled by the separate
            # bounded launch/reach-rescue lane; otherwise one impossible item can
            # consume the hourly growth slot forever.
            # MARKETER_CURRENT_PROMOTION_TRUTH_V2: ordinary scaling needs a currently confirmed active paid promotion.
            if c.get("current_bid_rub") is None or not c.get("promotion_active"):
                continue
            if not _whole_ruble_raise_possible(c.get("current_bid_rub"), 10):
                granularity_skipped += 1
                continue
            h = c.get("historical_signal") or {}
            i = c.get("intraday_signal") or {}
            _advice_signal = _advice_raise_by_id.get(str(c.get("item_id"))) or {}
            _h_contacts = int(h.get("contacts_7d") or _advice_signal.get("contacts_7d") or 0)
            _h_views = int(h.get("views_7d") or _advice_signal.get("views_7d") or 0)
            _h_days = int(h.get("days_with_data") or _advice_signal.get("days_with_data") or 0)
            try:
                _h_conv = float(h.get("conversion_7d") or _advice_signal.get("conversion_7d") or 0)
                _cur_bid = float(c.get("current_bid_rub"))
                _head_actual = float(headroom.get("actual_cpl_rub"))
                _head_red = float(headroom.get("red_cpl_rub"))
            except Exception:
                _h_conv = 0.0
                _cur_bid = 1e18
                _head_actual = 1e18
                _head_red = 0.0
            _standard_proven = (
                _h_contacts >= MIN_PROVEN_CONTACTS_7D
                and _h_views >= MIN_PROVEN_VIEWS_7D
                and _h_days >= MIN_PROVEN_DAYS
            )
            _micro_proven = (
                micro_proven_added < MICRO_PROVEN_MAX_PER_CYCLE
                and bool(headroom.get("active"))
                and target > 0 and actual < target
                and 0 < _cur_bid <= MICRO_PROVEN_MAX_BID_RUB
                and _h_contacts >= MIN_PROVEN_CONTACTS_7D
                and _h_views >= MICRO_PROVEN_MIN_VIEWS_7D
                and _h_days >= MIN_PROVEN_DAYS
                and _h_conv >= MICRO_PROVEN_MIN_CONVERSION_PCT
                and _head_red > 0
                and 0 <= _head_actual <= _head_red * MICRO_PROVEN_MAX_CPL_SHARE
            )
            if _standard_proven:
                c["proof_basis"] = "standard_7d_sample"
                proven.append(c)
            elif _micro_proven:
                c["proof_basis"] = "micro_converter_deep_headroom"
                proven.append(c)
                micro_proven_added += 1
            elif int(i.get("contacts_today") or 0) > 0:
                c["proof_basis"] = "current_day_contact"
                proven.append(c)
        portfolio_total = max(1, int(r.get("candidates_total") or len(top) or 1))
        high_bid_cap = max(1, int(portfolio_total * HIGH_BID_SHARE))
        n = min(n, cap, high_bid_cap, len(proven))
        top = proven
        source = ("cpl_headroom_push" if headroom.get("active") else
                  ("kpi_behind_push" if pace == "BEHIND" else "cpx_advisor_runner"))
        # MARKETER_ZERO_PROVEN_MODE_TRUTH_V1: KPI urgency never overrides proof.
        # If the proven guard removes every candidate, report the real non-money
        # recovery state instead of the misleading phrase "boosting 0 best ads".
        if n <= 0:
            mode = (
                f"CONTENT_RESCUE: факт {actual:g}/{target:g}, ожидалось {expected:.2f}; "
                "нет доказанных конвертирующих объявлений для безопасного повышения ставки; "
                "продолжаем бесплатную диагностику контента и сбор сигнала"
            )
        elif emergency:
            mode = (
                f"EMERGENCY_REANIMATION: факт {actual:g}/{target:g}, ожидалось {expected:.2f}; "
                f"усиливаем {n} лучших конвертирующих объявлений"
            )
        else:
            mode = (
                f"PUSH MAX: CPL {headroom.get('actual_cpl_rub')} < красной {headroom.get('red_cpl_rub')}"
                if headroom.get("active") else f"разгон, отстаём ({pace})"
            )
        diagnostics["proven_after_guard"] = len(proven)
        diagnostics["micro_proven_after_guard"] = micro_proven_added
        diagnostics["whole_ruble_granularity_skipped"] = granularity_skipped
        diagnostics["planned_after_guard"] = n
        diagnostics["reason"] = ("ready" if n > 0 else "no_proven_eligible_converter")
        diagnostics["next_action"] = ("execute_guarded_raise" if n > 0 else "content_rescue_and_collect_more_signal")
        return ([({"id": int(c["item_id"]), "source": source}, "raise") for c in top[:n]], mode, diagnostics)

    # REACH_STARVATION_RESCUE_V1: if the entire portfolio is almost invisible,
    # run a tiny bounded reach experiment instead of waiting forever for proof
    # that cannot appear without impressions. Never use this for ads that already
    # received meaningful reach with zero contacts.
    reach = r.get("reach_signal") or {}
    rescue = r.get("reach_rescue_candidates") or []
    if pace == "BEHIND" and reach.get("starved") and rescue:
        # REACH_RESCUE_ONE_HYPOTHESIS_PER_CYCLE_V2: rescue is exploration, not
        # scaling. One listing per trusted cycle is enough to buy reach evidence.
        n = min(1, cap, len(rescue))
        diagnostics["reason"] = "reach_starvation"
        diagnostics["reach_signal"] = reach
        diagnostics["planned_after_guard"] = n
        diagnostics["next_action"] = "guarded_reach_rescue"
        return ([({"id": int(c["item_id"]), "source": "reach_rescue"}, "raise") for c in rescue[:n]],
                f"REACH_RESCUE: охват {reach.get('views_today')}/{reach.get('threshold_views')} — постепенно усиливаем {n}",
                diagnostics)

    # MONEY_SAFE_PORTFOLIO_V2: если охват уже есть, а лидов нет, деньгами проблему
    # не маскируем — работаем с контентом/оффером/семантикой.
    if pace == "BEHIND":
        diagnostics["reason"] = (
            "measurement_lock_active" if status_counts.get("measure_locked", 0) > 0 else
            "max_bid_or_insufficient_signal" if any(k in status_counts for k in ("max_bid_reached","insufficient_signal")) else
            "no_eligible_raise_candidates"
        )
        # MARKETER_IDLE_MODE_TRUTH_V1: never claim that content is being tested
        # when this CPX runner did not schedule any content mutation. The separate
        # KPI non-money lane may still analyze/prepare a hypothesis, but runtime
        # truth must describe what is actually happening in this cycle.
        if diagnostics["reason"] == "measurement_lock_active":
            diagnostics["next_action"] = "wait_for_measurement_then_reassess"
            mode = "MEASUREMENT_WAIT: идёт окно измерения предыдущего изменения; новые изменения временно запрещены"
        elif diagnostics["reason"] == "max_bid_or_insufficient_signal":
            diagnostics["next_action"] = "collect_signal_and_run_nonmoney_kpi_lane"
            mode = "SIGNAL_COLLECTION: доказательств для изменения ставки пока недостаточно; продолжаем бесплатный анализ"
        else:
            diagnostics["next_action"] = "run_nonmoney_kpi_diagnostics"
            mode = "NON_MONEY_DIAGNOSTICS: безопасного денежного действия нет; продолжаем диагностику без расходов"
        return [], mode, diagnostics

    if pace == "AHEAD":
        # Без ставки продвижение не включено — снижать нечего и денег объявление
        # не тратит. Такие в план не берём, иначе ручка откажет на getBids.
        low = [it for it in (recs.get("lower") or [])
               if it.get("id") and it.get("bid_rub")]
        diagnostics["reason"] = "lower_candidates" if low else "no_active_bid_to_lower"
        diagnostics["next_action"] = "execute_guarded_lower" if low else "observe_without_spend_change"
        return ([({"id": int(it["id"])}, "lower") for it in low[:cap]],
                "экономия, идём с запасом", diagnostics)
    diagnostics["reason"] = "pace_on_track_or_unknown"
    diagnostics["next_action"] = "observe_and_recheck"
    return [], f"пауза ({pace})", diagnostics


def main():
    if APPLY:
        lane = str(os.environ.get("BORIS_MARKETER_MONEY_LANE") or "").strip()
        if lane not in {"evgeniy_recovery_v1", "staged_rollout_v1"}:
            print(f"[{datetime.now().isoformat()}] BLOCKED_UNTRUSTED_MONEY_LANE lane={lane or '-'}", flush=True)
            return 23
    lock_file = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"[{datetime.now().isoformat()}] SKIP: предыдущий cpx_advisor_runner ещё работает")
        return 0

    conn = psycopg2.connect(_dsn())
    cur = conn.cursor()
    try:
        # SAME_PAID_SCOPE_V1: use the exact production entitlement as :00 stats
        # and :10 KPI executor. This avoids spending the five-minute planning
        # window on QA/inactive KPI rows that cannot be acted on anyway.
        sys.path.insert(0, "/root/BORIS/backend")
        from kpi_goal_runner import get_goal_auto_accounts
        eligible = get_goal_auto_accounts()
        accounts = [x["account_id"] for x in eligible if float(x.get("target_leads_per_day") or 0) > 0]
        if ACCOUNT_FILTER:
            accounts = [a for a in accounts if a == ACCOUNT_FILTER]
        print(f"[{datetime.now().isoformat()}] Советник: платных BORIS-аккаунтов с KPI = {len(accounts)}: {accounts}")
        # заодно узнаём, у кого включён автопилот ставок
        # Разрешение на автономные действия даёт МАНДАТ, а не флаг bid_autopilot:
        # у флага нет ни срока, ни лимитов, ни имени того, кто его выдал.
        auto_permissions = {}
        # MANDATE_DISPLAY_ENTITLEMENT_ALIGNMENT_V1: a standing mandate is not
        # enough to call an account "active" in the operator/runner view.
        # Restrict the displayed permission set to the same exact paid/KPI
        # eligibility set used for execution. This prevents expired accounts
        # with stale historical mandates from appearing runnable.
        eligible_ids = set(accounts)
        cur.execute(
            "SELECT account_scope, allowed_operations FROM money_mandates"
            " WHERE status='active' AND revoked_at IS NULL"
            "   AND (valid_until IS NULL OR valid_until > now())"
            "   AND (valid_from IS NULL OR valid_from <= now())"
            " ORDER BY id DESC;")
        for scope, operations in cur.fetchall():
            for account_id in (scope or []):
                if account_id not in eligible_ids:
                    continue
                auto_permissions.setdefault(account_id, set()).update(operations or [])
        auto_accounts = set(auto_permissions)
        print(f"  с активным мандатом: {len(auto_accounts)}: {sorted(auto_accounts)}")

        for acc in accounts:
            runtime = {
                "account_id": acc,
                "last_run_at": datetime.now().isoformat(),
                "apply_enabled": bool(APPLY),
                "mandate_active": acc in auto_accounts,
                "allowed_operations": sorted(auto_permissions.get(acc, set())),
                "status": "running",
                "mode": None,
                "planned": 0,
                "applied": 0,
                "skipped": 0,
                "blocked": 0,
                "failed": 0,
            }
            try:
                # CAPACITY_SELF_HEAL_V1: before every hourly planning cycle make
                # sure the paid-tariff mandate capacity matches the real active
                # portfolio. This prevents an old 100-actions/day mandate from
                # silently stopping the marketer while KPI is still behind.
                #
                # CAPACITY_SAME_CYCLE_PERMISSION_REFRESH_V1: permission truth
                # MUST be refreshed after this mutation. The pre-loop snapshot
                # can be stale in both directions: a mandate may have just been
                # created/upgraded, or just revoked/downgraded.
                cycle_allowed_ops=set(auto_permissions.get(acc, set()))
                capacity={}
                try:
                    from app.api.cpx_advisor import ensure_paid_tariff_raise_mandate
                    capacity = ensure_paid_tariff_raise_mandate(acc) or {}
                    runtime["capacity_status"] = capacity.get("status")
                    runtime["max_actions_run"] = capacity.get("max_actions_run")
                    runtime["max_actions_day"] = capacity.get("max_actions_day")
                    cycle_allowed_ops=_cycle_permissions_after_capacity(
                        cycle_allowed_ops, capacity
                    )
                except Exception as cap_exc:
                    cycle_allowed_ops=set()
                    runtime["capacity_status"] = "error"
                    runtime["capacity_error"] = str(cap_exc)[:160]
                runtime["allowed_operations"]=sorted(cycle_allowed_ops)
                runtime["mandate_active"]=bool(cycle_allowed_ops)
                resp = _request_with_retry("GET", f"http://127.0.0.1:8000/api/cpx_advisor/run?account_id={acc}", timeout=120)
                data = resp.json()
                advice = data.get("advice", {})
                runtime["advisor_status"] = data.get("status")
                runtime["measurement_cycle"] = advice.get("measurement_cycle") or {}
                summary = advice.get("summary", data.get("message", "нет данных"))
                print(f"  {acc}: {data.get('status')} — {summary}")

                # KPI diagnosis is required even with no current money authority.
                # Only the filtered execution plan is mandate-gated; _build_plan
                # itself is read-only and materializes the exact reason BORIS is
                # acting, waiting, or continuing non-money diagnostics.
                if advice:
                    recs = advice.get("recommendations", {})
                    considered = would_apply = applied = skipped = blocked = failed = 0
                    block_reasons = []
                    raw_plan, mode, plan_diagnostics = _build_plan(acc, recs, advice=advice)
                    allowed_ops = set(cycle_allowed_ops)
                    operation_for_action = {"raise": "cpx.raise_bid", "lower": "cpx.lower_bid"}
                    plan = [(it, act) for it, act in raw_plan if operation_for_action.get(act) in allowed_ops]
                    plan_diagnostics["capacity_status"] = runtime.get("capacity_status")
                    plan_diagnostics["allowed_operations"] = sorted(allowed_ops)
                    if raw_plan and not plan:
                        reason = str(capacity.get("reason") or runtime.get("capacity_status") or "money_authority_unavailable")
                        plan_diagnostics["money_plan_filtered"] = len(raw_plan)
                        plan_diagnostics["money_block_reason"] = reason
                        mode = _nonmoney_runtime_mode(reason, summary)
                    if MAX_ACTIONS_OVERRIDE is not None:
                        plan = plan[:MAX_ACTIONS_OVERRIDE]
                        plan_diagnostics["max_actions_override"] = MAX_ACTIONS_OVERRIDE
                    runtime["mode"] = mode
                    runtime["plan_diagnostics"] = plan_diagnostics
                    runtime["planned"] = len(plan)
                    budget = len(plan)
                    print(f"    режим: {mode}, разрешено={sorted(allowed_ops)}, к исполнению {len(plan)}")
                    for it, act in plan:
                            considered += 1
                            if budget <= 0:
                                blocked += 1
                                continue
                            if not APPLY:
                                would_apply += 1
                                budget -= 1
                                continue
                            try:
                                ar = _request_with_retry("POST", "http://127.0.0.1:8000/api/cpx_advisor/apply_one",
                                    json={"account_id": acc, "item_id": it["id"], "action": act,
                                          "actor_type": "boris_auto", "trigger": "cron",
                                          "source": it.get("source") or "cpx_advisor_runner",
                                          "request_id": f"{_rid(acc)}:{int(it['id'])}"}, timeout=30)
                                if ar.status_code >= 400:
                                    failed += 1
                                    continue
                                _body = ar.json()
                                st = _body.get("status")
                                if st == "ok":
                                    applied += 1
                                    budget -= 1
                                elif st == "skipped":
                                    skipped += 1
                                    _reason = str(_body.get('reason_code') or _body.get('blocked_by') or _body.get('message') or 'skipped')[:160]
                                    print(f"    apply {it.get('id')}: skipped {_reason}")
                                else:
                                    blocked += 1
                                    _reason = str(_body.get('reason_code') or _body.get('blocked_by') or _body.get('reason') or _body.get('code') or _body.get('message') or 'blocked')[:160]
                                    block_reasons.append(_reason)
                                    print(f"    apply {it.get('id')}: blocked {_reason}")
                            except Exception as e:
                                failed += 1
                                print(f"    apply {it.get('id')}: ошибка {str(e)[:80]}")
                    runtime.update({"applied": applied, "skipped": skipped, "blocked": blocked, "failed": failed, "would_apply": would_apply, "block_reasons": block_reasons})
                    archive_n = len(recs.get("archive_candidates")
                                    or recs.get("lower_or_archive") or [])
                    print(f"    considered={considered} would_apply={would_apply} "
                          f"applied={applied} skipped={skipped} blocked={blocked} failed={failed} "
                          f"archive_candidates={archive_n} (не исполняются)")
                else:
                    # KPI_RUNTIME_DIAGNOSIS_ALWAYS_MATERIALIZED_V1: no advice is
                    # still a deterministic state. Never persist a successful
                    # off-target cycle with mode=None and force the owner to
                    # discover that BORIS had no visible next step.
                    reason=str(data.get("status") or runtime.get("capacity_status") or "advisor_no_data")
                    runtime["mode"]=_nonmoney_runtime_mode(reason, summary)
                    runtime["plan_diagnostics"]={
                        "decision":"NO_EXECUTABLE_ADVICE",
                        "reason":reason,
                        "next_action":"refresh_inputs_and_continue_nonmoney_diagnostics",
                        "capacity_status":runtime.get("capacity_status"),
                        "allowed_operations":sorted(cycle_allowed_ops),
                    }
                    runtime["planned"]=0
                    print(f"    режим: {runtime['mode']}, к исполнению 0")
                runtime["status"] = "ok"
            except Exception as e:
                runtime["status"] = "error"
                runtime["error"] = str(e)[:200]
                print(f"  {acc}: ОШИБКА {str(e)[:150]}")
            finally:
                runtime["finished_at"] = datetime.now().isoformat()
                _save_runtime(cur, conn, acc, runtime)
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
