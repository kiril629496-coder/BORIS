from app.services.marketing_clock import marketing_today, marketing_today_iso
"""
Борис-Советник по ставкам (CPX). Читает ставки + статистику + целевой CPL,
считает по формуле и складывает РЕКОМЕНДАЦИИ в Storage (ключ cpx_advice).
НИЧЕГО НЕ МЕНЯЕТ — только советует. Реальные действия — отдельный слой позже.
"""
from fastapi import APIRouter
import json, httpx
from datetime import date, timedelta, datetime, timezone

router = APIRouter(prefix="/api/cpx_advisor", tags=["cpx_advisor"])

# Порог значимости: не судим объявление, пока показов меньше этого числа
MIN_VIEWS_FOR_JUDGEMENT = 30      # суточный порог, оставлен для истории
# Недельное окно: суточные пороги писались под аккаунт с сотнями показов
# на объявление, а у живых клиентов это единицы показов В НЕДЕЛЮ.
MIN_VIEWS_FOR_JUDGEMENT_7D = 3   # меньше — судить не о чем
ARCHIVE_MIN_VIEWS_7D = 10        # столько показов без контактов — повод снизить
RED_CPL_CURRENT_DAY_MIN_VIEWS = 10  # при выполненном KPI и красном CPL: 10 просмотров сегодня без контакта -> bounded lower
WINDOW_DAYS = 7                  # семь ПОЛНЫХ дней, сегодняшний неполный не берём
# Шаг изменения ставки (для будущих действий; сейчас только в тексте совета)
BID_STEP_PCT = 10


def _is_archived(db, account_id, item_id):
    """True если продвижение с объявления уже снято ботом (флаг идемпотентности)."""
    try:
        from app.models.storage import Storage as _St
        return db.query(_St).filter(_St.account_id == account_id, _St.key == f"cpx_archived:{item_id}").first() is not None
    except Exception:
        return False


def _load_json(db, account_id, key):
    from app.models.storage import Storage
    row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == key).first()
    return json.loads(row.value) if row else None


def _save_json(db, account_id, key, value):
    from app.models.storage import Storage
    row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == key).first()
    if row:
        row.value = json.dumps(value, ensure_ascii=False)
    else:
        db.add(Storage(account_id=account_id, key=key, value=json.dumps(value, ensure_ascii=False)))
    db.commit()


def _owner_manual_bid_override(db, account_id: str, item_id: int):
    """Return active explicit owner bid override for one Avito item.

    BORIS may observe/measure such a bid, but autonomous CPX must not overwrite
    the exact manual owner decision. This does not expand BORIS auto-bid caps:
    only the owner-set item is isolated from autonomous raise/lower actions.
    """
    data = _load_json(db, account_id, "owner_manual_bid_overrides") or {}
    items = data.get("items") if isinstance(data, dict) else {}
    entry = (items or {}).get(str(int(item_id))) if isinstance(items, dict) else None
    if not isinstance(entry, dict) or not entry.get("active", True):
        return None
    return entry


def _aggregate_window(db, account_id: str, days: int = WINDOW_DAYS):
    """Суммы просмотров и контактов по объявлению за N ПОЛНЫХ прошлых дней.

    Сегодняшний день не берём: он неполный и занижает картину.
    Возвращает {item_id: {"views": n, "contacts": n, "days": сколько дней были данные}}.
    """
    out = {}
    for back in range(1, days + 1):
        day = (marketing_today() - timedelta(days=back)).isoformat()
        snap = _load_json(db, account_id, f"daily_stats:{day}")
        if not snap:
            continue
        for it in (snap.get("items") or []):
            iid = it.get("id")
            if iid is None:
                continue
            cur = out.setdefault(iid, {"views": 0, "contacts": 0, "days": 0})
            cur["views"] += int(it.get("views") or 0)
            cur["contacts"] += int(it.get("contacts") or 0)
            cur["days"] += 1
    return out


@router.get("/run")
def run_advisor(account_id: str = "otdushi"):
    """Один прогон Советника: собрать данные, посчитать, сложить рекомендации.
    Вызывается по расписанию (раз в час) или вручную."""
    from app.db.session import SessionLocal
    from app.api.avito import get_avito_token
    db = SessionLocal()
    try:
        # CPX_ADVISOR_EARLY_SAFE_SNAPSHOT_V1: early diagnostic exits must
        # overwrite any older actionable advice. Otherwise a stale raise/lower
        # can remain visible after KPI removal, entitlement expiry, or missing
        # current stats. This helper is DB-only and never calls Avito money APIs.
        def _current_entitlement_state():
            try:
                from app.services.control_plane_adapters_ext import marketing_service_entitlement as _safe_entitlement
                _e = _safe_entitlement(db, account_id) or {}
                return str(_e.get("state") or "unknown")
            except Exception:
                return "unknown"

        def _persist_nonactionable_advice(reason_code: str, message: str, target=None):
            _ent_state = _current_entitlement_state()
            # CPX_ADVISOR_OWNER_ACTION_REQUIRED_TRUTH_V1: only genuinely
            # missing owner-controlled money inputs are owner actions. Missing
            # stats/provider data remains BORIS-owned automatic retry.
            # CPX_ADVISOR_INACTIVE_SERVICE_NO_OWNER_ACTION_V1:
            # Missing KPI is actionable by the owner only while the paid marketer
            # service is actually active. Expired/unknown service must not create
            # an owner action for a money contour that cannot run.
            _early_kpi = _load_json(db, account_id, "kpi_settings") or {}
            _early_bid_autopilot = bool(_early_kpi.get("bid_autopilot"))
            # CPX_ADVISOR_EARLY_AUTOPILOT_PAUSE_TRUTH_V1:
            # Missing KPI is not an owner task while automatic bidding itself is
            # deliberately disabled. Do not make the owner configure a money
            # contour that BORIS is currently forbidden to execute.
            _owner_required = (
                _ent_state == "active"
                and reason_code in {"no_kpi"}
                and _early_bid_autopilot
            )
            _safe = {
                "generated_at": __import__("datetime").datetime.now().isoformat(),
                "account_id": account_id,
                "target": target or {},
                "fact": {
                    "marketing_service_entitlement_state": _ent_state,
                    "data_note": message,
                },
                "position_monitor": {"state": "deferred", "reason": reason_code, "read_only": True},
                "recommendations": {"raise": [], "lower": [], "archive_candidates": [], "lower_or_archive": [], "watching": []},
                "money_writability": {"canonical_writable_active": 0, "read_only_active": 0},
                "summary": message,
                "diagnostic_reason": reason_code,
                "owner_action_required": bool(_owner_required),
                "owner_action": (
                    message if _owner_required else
                    "Автоматическое управление ставками выключено владельцем; денежные параметры сейчас не требуются."
                    if reason_code == "no_kpi" and not _early_bid_autopilot else
                    "Не требуется — BORIS повторит проверку автоматически."
                ),
            }
            _save_json(db, account_id, "cpx_advice", _safe)
            return _safe

        # 1) целевой CPL
        kpi = _load_json(db, account_id, "kpi_settings")
        if not kpi or not kpi.get("max_cost_per_lead_rub"):
            _msg = "Не задана максимальная цена обращения. Денежные рекомендации очищены; BORIS продолжит только диагностику до появления подтверждённого KPI."
            _persist_nonactionable_advice("no_kpi", _msg)
            return {"status": "no_kpi", "message": _msg}
        max_cpl = kpi.get("max_cost_per_lead_rub")
        target_leads = kpi.get("target_leads_per_day", 0)
        daily_limit = kpi.get("daily_budget_limit_rub", 0)
        # ADVISOR_BUDGET_AUTHORIZATION_TRUTH_V1: a numeric budget is not spend
        # authority. Owner-facing advice must match the executor's provenance
        # guard, otherwise BORIS can say "nothing required" while all raises are
        # actually blocked. Exact authenticated value is the only positive proof.
        _budget_auth = kpi.get("daily_budget_authorization") if isinstance(kpi.get("daily_budget_authorization"), dict) else {}
        try:
            _daily_limit_num = float(daily_limit or 0)
            _budget_authorized = bool(
                _daily_limit_num > 0
                and str(_budget_auth.get("policy_version") or "") == "MONEY_BUDGET_OWNER_PROVENANCE_V1"
                and int(_budget_auth.get("authorized_by_user_id") or 0) > 0
                and float(_budget_auth.get("daily_budget_limit_rub") or -1) == _daily_limit_num
                and str(_budget_auth.get("source") or "") == "authenticated_set_kpi_settings"
            )
        except Exception:
            _daily_limit_num = 0.0
            _budget_authorized = False

        # CPX_ADVISOR_SERVICE_ENTITLEMENT_GATE_V1: recommendation truth must
        # match mutation truth. Expired/inactive marketer service may keep
        # historical diagnostics visible, but must never emit raise/lower
        # candidates or spend provider quota on CPX recommendation reads.
        _money_entitlement_state = _current_entitlement_state()
        _money_entitlement_active = (_money_entitlement_state == "active")

        # 2) свежая статистика по объявлениям (daily_stats за сегодня)
        today = marketing_today_iso()
        stats = _load_json(db, account_id, f"daily_stats:{today}")
        items_stats = stats.get("items", []) if stats else []
        if not items_stats:
            _msg = "Статистика Avito за сегодня ещё не получена. Старые денежные рекомендации очищены; BORIS продолжит автоматически, когда появятся свежие данные."
            _persist_nonactionable_advice(
                "no_stats", _msg,
                {"max_cpl_rub": max_cpl, "target_leads_per_day": target_leads},
            )
            return {"status": "no_stats", "message": _msg}
        # MARKETER_ADVICE_STATS_FRESH_15M_V1: stale/retained stats may still be
        # shown for reporting and diagnostics, but must not generate a raise
        # recommendation. Reuse the same authoritative DB-only freshness guard.
        try:
            from app.services.marketing_signal_guard import money_stats_snapshot_eligible as _money_stats_ok
            _stats_money_fresh, _stats_money_evidence = _money_stats_ok(db, account_id, max_age_seconds=900)
        except Exception:
            _stats_money_fresh, _stats_money_evidence = False, {"reason":"stats_guard_error"}

        # 3) текущие ставки по объявлениям (наш cpxpromo)
        # CPX_MEASURE_PROVIDER_QUERY_PRIORITIZES_WAITING_V2:
        # Avito accepts getPromotionsByItemIds in bounded batches. Waiting money
        # experiments remain first for fast self-heal, but candidate discovery
        # must cover the FULL active inventory: on PBI every live paid promotion
        # was beyond item position 200, so truncating here hid real client spend.
        # Query all active IDs in <=200-item batches; every actual mutation still
        # requires an exact getBids/{itemID} proof immediately before provider write.
        _active_item_ids_all = [
            int(it["id"]) for it in items_stats
            if it.get("status") == "active" and str(it.get("id") or "").isdigit()
        ]
        try:
            _waiting_probe_ids = [
                int(x.get("item_id")) for x in
                (_active_raise_measurement_summary(db, account_id).get("items") or [])
                if str(x.get("item_id") or "").isdigit()
            ]
        except Exception:
            _waiting_probe_ids = []
        _priority_ids = []
        for _iid in _waiting_probe_ids + _active_item_ids_all:
            if _iid not in _priority_ids:
                _priority_ids.append(_iid)
        item_ids = _priority_ids
        bids_map = {}
        # CPX_MEASURE_PROMOTION_OFF_SUPERSEDE_V1: only a successful promotions
        # inventory response may prove that an experiment's paid promotion is
        # now off/unreported. 429/transport/stale-stats must never masquerade as
        # provider truth and prematurely close measurement evidence.
        _bids_truth_complete = False
        _bids_exact_item_ids = set()
        # CPX_MEASURE_PROVIDER_QUERY_SCOPE_V1: a successful provider response is
        # authoritative only for itemIDs that were actually included in that
        # request. Large accounts may have >200 active items, so a global
        # response-success flag must never be used as proof for an unqueried item.
        _bids_queried_item_ids = set()
        tok = None
        # MARKETER_ADVICE_SHARED_ACCOUNT_THROTTLE_V1: recommendation reads consume
        # the same provider quota as money workers. Never pressure a tenant during
        # a Retry-After observed elsewhere; stale stats also do not justify a CPX probe.
        from app.services.avito_account_throttle import account_throttle_remaining as _advice_throttle_remaining, record_account_throttle as _advice_record_throttle
        _advice_retry = _advice_throttle_remaining(account_id)
        if _advice_retry > 0:
            bids_map = {"_error":"avito_account_throttled","retry_after_seconds":int(_advice_retry)}
        elif not _money_entitlement_active:
            bids_map = {"_error":"marketing_service_period_inactive"}
        elif not _stats_money_fresh:
            bids_map = {"_error":"stats_not_fresh"}
        else:
            # MARKETER_ADVICE_THROTTLE_BEFORE_TOKEN_V1: token refresh may itself
            # perform provider I/O, so only resolve it after cooldown/freshness pass.
            tok_data = get_avito_token(account_id)
            tok = tok_data.get("access_token") if isinstance(tok_data, dict) else tok_data
        if _advice_retry <= 0 and _money_entitlement_active and _stats_money_fresh and tok and item_ids:
            try:
                # CPX_ADVISOR_FULL_ACTIVE_PROMOTION_TRUTH_V1:
                # Avito bulk promotion reads are capped per request, not per
                # account. Query the whole active inventory in <=200-item chunks
                # so late-position paid listings remain visible to red-CPL lower
                # recovery. A partial/failed tail never becomes global truth.
                _bulk_all_ok = True
                for _pos in range(0, len(item_ids), 200):
                    _chunk_ids = item_ids[_pos:_pos + 200]
                    r = httpx.post(
                        "https://api.avito.ru/cpxpromo/1/getPromotionsByItemIds",
                        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                        json={"itemIDs": _chunk_ids},
                        timeout=30,
                    )
                    if r.status_code == 429:
                        _retry = None
                        try:
                            _raw = str(r.headers.get("Retry-After") or "").strip()
                            if _raw:
                                try:
                                    _retry = max(0, int(float(_raw)))
                                except Exception:
                                    from email.utils import parsedate_to_datetime as _parse_advice_ra
                                    from datetime import datetime as _advice_dt, timezone as _advice_tz
                                    _when = _parse_advice_ra(_raw)
                                    if _when.tzinfo is None:
                                        _when = _when.replace(tzinfo=_advice_tz.utc)
                                    _retry = max(0, int((_when.astimezone(_advice_tz.utc)-_advice_dt.now(_advice_tz.utc)).total_seconds()))
                        except Exception:
                            _retry = None
                        _retry = _advice_record_throttle(
                            account_id, _retry or 30, source="cpx_advice_promotions_429"
                        )
                        bids_map["_error"] = "avito_account_throttled"
                        bids_map["retry_after_seconds"] = int(_retry)
                        _bulk_all_ok = False
                        break
                    if r.status_code != 200:
                        bids_map["_error"] = f"promotions_http_{int(r.status_code)}"
                        _bulk_all_ok = False
                        break
                    _bids_queried_item_ids.update(
                        int(x) for x in _chunk_ids if str(x).isdigit()
                    )
                    for it in (r.json() or {}).get("items", []):
                        mp = it.get("manualPromotion") or {}
                        iid = it.get("itemID")
                        if iid is None:
                            continue
                        bids_map[int(iid)] = {
                            "bid_penny": mp.get("bidPenny"),
                            "min_bid_penny": mp.get("minBidPenny"),
                            "rec_bid_penny": mp.get("recBidPenny"),
                            "max_bid_penny": mp.get("maxBidPenny"),
                            "promotion_active": bool(it.get("manualPromotion") or it.get("autoPromotion")),
                            "promotion_mode": "manual" if it.get("manualPromotion") else ("auto" if it.get("autoPromotion") else "off"),
                        }
                _bids_truth_complete = bool(
                    _bulk_all_ok
                    and len(_bids_queried_item_ids) == len(set(item_ids))
                )
            except Exception as e:
                _bids_truth_complete = False
                bids_map = {"_error": str(e)[:100]}

        # CPX_MEASURE_EXACT_GETBIDS_TRUTH_V1:
        # Bulk getPromotionsByItemIds is useful for broad portfolio reads, but it
        # has been observed to omit the currently selected manual promotion for an
        # item while exact getBids/{itemID} reports the live manual bid. For at
        # most the two unresolved account measurement slots, exact read-only
        # getBids is authoritative. This keeps causal slots from waiting on a
        # misleading bulk projection and costs at most two extra provider reads
        # only when unresolved experiments exist.
        if (_advice_retry <= 0 and _money_entitlement_active and _stats_money_fresh
                and tok and _waiting_probe_ids and not isinstance(bids_map.get("_error"), str)):
            for _mid in _waiting_probe_ids[:MAX_ACTIVE_RAISE_MEASUREMENTS_PER_ACCOUNT]:
                try:
                    _er = httpx.get(
                        f"https://api.avito.ru/cpxpromo/1/getBids/{int(_mid)}",
                        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                        timeout=20,
                    )
                    if _er.status_code == 429:
                        _retry = None
                        try:
                            _raw = str(_er.headers.get("Retry-After") or "").strip()
                            _retry = max(0, int(float(_raw))) if _raw else 30
                        except Exception:
                            _retry = 30
                        _advice_record_throttle(
                            account_id, _retry or 30,
                            source="cpx_advice_measure_exact_getbids_429",
                        )
                        break
                    if _er.status_code != 200:
                        continue
                    _exact = _er.json() or {}
                    _selected = str(_exact.get("selectedType") or "").strip().lower()
                    _manual = _exact.get("manual") if isinstance(_exact.get("manual"), dict) else {}
                    _auto = _exact.get("auto") if isinstance(_exact.get("auto"), dict) else {}
                    _bid_penny = _manual.get("bidPenny") if _selected == "manual" else None
                    bids_map[int(_mid)] = {
                        "bid_penny": _bid_penny,
                        "min_bid_penny": _manual.get("minBidPenny"),
                        "rec_bid_penny": _manual.get("recBidPenny"),
                        "max_bid_penny": _manual.get("maxBidPenny"),
                        "promotion_active": bool(_selected in {"manual", "auto"}),
                        "promotion_mode": (_selected if _selected in {"manual", "auto"} else "off"),
                        "exact_getbids_truth": True,
                    }
                    _bids_exact_item_ids.add(int(_mid))
                    _bids_queried_item_ids.add(int(_mid))
                except Exception:
                    # Exact measurement truth is an optimization/self-heal read.
                    # Never turn transport trouble into false "promotion off".
                    continue

        # 4) расход за день — только подтверждённый провайдером spending signal.
        # Баланс кошелька не является расходом за сегодня: пополнение, возврат и
        # иные движения могут давать ложный CPL и ошибочно разрешать/запрещать raise.
        from app.services.marketing_money_policy import latest_confirmed_spend
        # CPX_ADVISOR_CURRENT_DAY_CONTACTS_ONLY_V1:
        # Avito can briefly keep prior-day item counters after Moscow midnight
        # while spending has already rolled to the new business day. Historical
        # counters remain useful for the 7-day winner signal, but must not become
        # today's KPI fact or today's CPL denominator.
        _marketing_day = marketing_today_iso()
        _item_stats_day = str((stats or {}).get("stats_date") or "")
        _item_signal_current_day = (_item_stats_day == _marketing_day)
        total_contacts = (
            sum(int(it.get("contacts") or 0) for it in items_stats)
            if _item_signal_current_day else 0
        )
        # MARKETER_GROWTH_SIGNAL_MAX_AGE_15M_V2: recommendations that can
        # lead to a raise use the same strict freshness contract as rollout.
        spend_signal = latest_confirmed_spend(db, account_id, max_age_seconds=900) or {}
        # MARKETER_ADVICE_SPEND_TIMESTAMP_SANITY_V1: recommendation freshness
        # rejects future/non-finite provider facts exactly like money execution.
        try:
            import math as _sp_math, time as _sp_time
            _sp_ts=float(spend_signal.get("timestamp") or 0)
            _sp_val=float(spend_signal.get("spent_today_rub"))
            _sp_ok=(spend_signal.get("status") == "ok" and _sp_math.isfinite(_sp_ts)
                    and _sp_math.isfinite(_sp_val) and _sp_ts>0 and _sp_val>=0
                    and _sp_ts<=_sp_time.time()+60)
        except Exception:
            _sp_ok=False; _sp_val=None
        if _sp_ok:
            try:
                from app.services.marketing_signal_guard import money_spend_signal_eligible as _money_signal_ok
                _sp_ok = bool(_money_signal_ok(db, account_id, spend_signal))
            except Exception:
                _sp_ok = False
        spent = _sp_val if _sp_ok else None
        acct_cpl = round(spent / total_contacts, 2) if (spent is not None and spent > 0 and total_contacts > 0) else None

        limit_pct = None
        # Recommendation layer is fail-closed too. Actual mutation has a second
        # independent money guard, but UI/advice must not suggest a raise while
        # same-day spending is missing or stale.
        block_raises = (spent is None or not _stats_money_fresh or not _money_entitlement_active)
        # CPX_ADVISOR_BID_AUTOPILOT_DISABLED_NO_RAISE_V1:
        # UI/advice truth must match the final provider-write guard. When the
        # owner disabled automatic bid management, do not emit autonomous raise
        # candidates and do not ask for optional money configuration merely to
        # prepare a contour that is intentionally paused.
        _bid_autopilot_enabled = bool(kpi.get("bid_autopilot"))
        if not _bid_autopilot_enabled:
            block_raises = True
            limit_note = (
                "Автоматическое управление ставками выключено владельцем — "
                "BORIS не формирует и не исполняет повышения ставок."
            )
        if not _money_entitlement_active:
            limit_note = "Оплаченный период AI-маркетолога завершён или не подтверждён — денежные рекомендации отключены."
        elif spent is None:
            limit_note = "Расходы Avito за сегодня не подтверждены — повышение ставок временно остановлено."
        elif not _stats_money_fresh:
            limit_note = "Статистика Avito старше 15 минут или неполная — повышение ставок временно остановлено."
        else:
            limit_note = None
        if daily_limit and spent is not None:
            limit_pct = round(spent / daily_limit * 100)
            if limit_pct >= 100:
                block_raises = True
                limit_note = f"Суточный лимит исчерпан ({spent:.0f}/{daily_limit:.0f}р) — поднятия заблокированы."
            elif limit_pct >= 90:
                block_raises = True
                limit_note = f"Близко к суточному лимиту ({spent:.0f}/{daily_limit:.0f}р, {limit_pct}%) — поднятия заблокированы."
            elif _stats_money_fresh:
                limit_note = f"Расход {spent:.0f}/{daily_limit:.0f}р ({limit_pct}%) — в пределах лимита."
        elif not daily_limit:
            block_raises = True
            limit_note = "Суточный рекламный бюджет не задан — повышение ставок остановлено."
        if daily_limit and not _budget_authorized:
            block_raises = True
            limit_note = (
                f"Суточный бюджет {float(daily_limit):.0f} ₽ сохранён, но не подтверждён владельцем через защищённую настройку — "
                "повышение ставок остановлено до подтверждения этого же лимита."
            )

        # CPX_ADVISOR_RED_CPL_RECOVERY_V1: account economics outranks reach
        # growth. If confirmed current CPL is already above the owner's red line,
        # never emit a raise recommendation. The classification loop below may
        # instead expose only proven paid zero-contact waste for bounded lowering.
        try:
            _cpl_redline_block = bool(
                acct_cpl is not None and float(max_cpl or 0) > 0
                and float(acct_cpl) > float(max_cpl)
            )
        except Exception:
            _cpl_redline_block = False
        if _cpl_redline_block:
            block_raises = True
            limit_note = (
                f"Текущий CPL {float(acct_cpl):.0f} ₽ выше лимита {float(max_cpl):.0f} ₽ — "
                "новые повышения ставок запрещены; BORIS снижает только доказанно неэффективное платное продвижение."
            )

        # 4b) Закрываем созревшие периоды измерения CPX.
        # Это read/DB-only lifecycle: никаких запросов на изменение Avito.
        # Без этого waiting_measurement мог оставаться вечным, а повторная
        # оптимизация объявления была бы заблокирована навсегда.
        measurement_results = []
        measure_states_by_item = {}
        measurement_compaction = {"status": "not_run", "before": 0, "after": 0, "closed": 0}
        try:
            # ACCOUNT_RAISE_MEASUREMENT_BACKLOG_SELF_HEAL_V1:
            # Older builds could open one feed20 measurement every hour. Compact
            # that historical overflow locally before normal measurement sweep.
            # No Avito mutation and no winner/loser verdict is created.
            measurement_compaction = _compact_raise_measurement_backlog(
                db, account_id, MAX_ACTIVE_RAISE_MEASUREMENTS_PER_ACCOUNT
            )
            from app.models.storage import Storage as _MeasureStorage
            measure_rows = (
                db.query(_MeasureStorage)
                .filter(
                    _MeasureStorage.account_id == account_id,
                    _MeasureStorage.key.like("cpx_measure:%"),
                )
                .all()
            )
            # CPX_MEASURE_INACTIVE_TERMINAL_V1: a complete current inventory is
            # authoritative that a missing item cannot be scaled. Close its old
            # money experiment locally instead of waiting forever for views that
            # can never arrive (rejected/blocked/removed item). No provider write.
            _complete_now=bool((stats.get("completeness") or {}).get("complete") is True and (stats.get("completeness") or {}).get("inventory_complete") is True)
            _active_now={int((x or {}).get("id") or 0) for x in (items_stats or []) if isinstance(x,dict) and str(x.get("status") or "").lower()=="active" and str((x or {}).get("id") or "").isdigit()}
            for measure_row in measure_rows:
                try:
                    measure_state = json.loads(measure_row.value or "{}")
                except Exception:
                    continue
                try:
                    _mi = int(measure_state.get("item_id") or 0)
                except Exception:
                    _mi = 0
                if _mi:
                    _prev = measure_states_by_item.get(_mi) or {}
                    if str(measure_state.get("started_at") or "") >= str(_prev.get("started_at") or ""):
                        measure_states_by_item[_mi] = dict(measure_state)
                if measure_state.get("status") != "waiting_measurement":
                    continue
                measure_item_id = measure_state.get("item_id")
                measure_action = str(measure_state.get("action") or "")
                try: _measure_iid=int(measure_item_id or 0)
                except Exception: _measure_iid=0
                if _complete_now and _measure_iid and _measure_iid not in _active_now:
                    from datetime import datetime as _measure_dt
                    measure_state["status"]="measured"
                    measure_state["finished_at"]=_measure_dt.now().isoformat()
                    measure_state["effect"]="inactive_or_rejected"
                    measure_state["decision"]="stop_money_inactive_item"
                    measure_state["reason"]="Полный текущий inventory не содержит объявление как active; денежный эксперимент закрыт без масштабирования."
                    measure_state["scale_eligible"]=False
                    measure_row.value=json.dumps(measure_state,ensure_ascii=False)
                    _journal=_load_json(db,account_id,"cpx_learning_journal") or []
                    if not isinstance(_journal,list): _journal=[]
                    _journal.append({"finished_at":measure_state["finished_at"],"account_id":account_id,"item_id":_measure_iid,"action":measure_action,"old_bid_rub":measure_state.get("old_bid_rub"),"new_bid_rub":measure_state.get("new_bid_rub"),"effect":"inactive_or_rejected","decision":"stop_money_inactive_item","learning_contract":"daily_bid_learning_v1","red_price_guard":"item_not_active_no_scale"})
                    _save_json(db,account_id,"cpx_learning_journal",_journal[-500:])
                    db.commit()
                    measurement_results.append({"status":"measured","item_id":_measure_iid,"action":measure_action,"effect":"inactive_or_rejected","decision":"stop_money_inactive_item"})
                    measure_states_by_item[_measure_iid]=dict(measure_state)
                    continue
                # CPX_MEASURE_CURRENT_TRUTH_SUPERSEDE_V1: an old bid experiment
                # is no longer causally measurable when the listing lost canonical
                # write capability or Avito now confirms a different live bid.
                # Close only the local measurement state; never call provider here
                # and never classify the confounded window as winner/loser.
                if measure_action in {"raise", "lower"} and _measure_iid:
                    _cur_item = next((x for x in (items_stats or []) if isinstance(x,dict) and int(x.get("id") or 0)==_measure_iid), {})
                    _wc = str((_cur_item or {}).get("write_capability") or "read_only_until_mapped")
                    _cur_bid_info = bids_map.get(_measure_iid) if isinstance(bids_map.get(_measure_iid), dict) else {}
                    if not _cur_bid_info and isinstance(bids_map.get(str(_measure_iid)), dict):
                        _cur_bid_info = bids_map.get(str(_measure_iid)) or {}
                    _cur_penny = _cur_bid_info.get("bid_penny")
                    _cur_bid_rub = round(float(_cur_penny)/100.0, 2) if isinstance(_cur_penny,(int,float)) else None
                    try: _expected_bid = float(measure_state.get("new_bid_rub")) if measure_state.get("new_bid_rub") is not None else None
                    except Exception: _expected_bid = None
                    _truth_reason = None
                    # CPX_MEASURE_MONEY_IDENTITY_ONLY_V1: content/feed
                    # write_capability is not CPX money identity. Never supersede
                    # a bid measurement merely because CampaignItem mapping is
                    # read-only. Provider truth is authoritative only when THIS
                    # exact itemID was included in a successful current CPX read.
                    _item_cpx_truth_complete = bool(
                        _measure_iid in _bids_exact_item_ids
                        or (
                            _bids_truth_complete
                            and _measure_iid in _bids_queried_item_ids
                        )
                    )
                    if (_item_cpx_truth_complete and _expected_bid is not None and _expected_bid > 0
                          and (not bool(_cur_bid_info.get("promotion_active")) or _cur_bid_rub is None)):
                        # Provider answered successfully and confirms the paid
                        # state from the experiment is no longer active. Keeping
                        # this row waiting would consume backlog capacity until
                        # timeout even though causal continuity is already gone.
                        _truth_reason = "promotion_inactive_or_bid_unreported"
                    elif _cur_bid_rub is not None and _expected_bid is not None and abs(_cur_bid_rub-_expected_bid) >= 0.01:
                        _truth_reason = "live_bid_changed"
                    if _truth_reason:
                        from datetime import datetime as _measure_dt
                        measure_state["status"]="superseded"
                        measure_state["finished_at"]=_measure_dt.now().isoformat()
                        measure_state["effect"]="current_provider_state_changed"
                        measure_state["decision"]="do_not_learn_from_confounded_window"
                        measure_state["reason_code"]=_truth_reason
                        measure_state["reason"]=(
                            "Объявление больше не имеет канонического права денежной записи; старое измерение закрыто без verdict."
                            if _truth_reason=="write_capability_lost" else
                            "Avito подтверждает, что платное продвижение эксперимента сейчас выключено или активная ставка отсутствует; старое измерение закрыто без verdict."
                            if _truth_reason=="promotion_inactive_or_bid_unreported" else
                            f"Текущая ставка Avito {_cur_bid_rub} ₽ не совпадает со ставкой эксперимента {_expected_bid} ₽; старое измерение закрыто без verdict."
                        )
                        measure_state["scale_eligible"]=False
                        measure_state["current_truth"]={"write_capability":_wc,"live_bid_rub":_cur_bid_rub,"expected_bid_rub":_expected_bid}
                        measure_row.value=json.dumps(measure_state,ensure_ascii=False)
                        db.commit()
                        measurement_results.append({"status":"superseded","item_id":_measure_iid,"action":measure_action,"effect":"current_provider_state_changed","reason_code":_truth_reason})
                        measure_states_by_item[_measure_iid]=dict(measure_state)
                        continue
                if measure_item_id is None or measure_action not in {"raise", "lower", "archive"}:
                    continue
                measurement_results.append(
                    _finalize_measure_state(
                        db=db,
                        account_id=account_id,
                        item_id=int(measure_item_id),
                        action=measure_action,
                    )
                )
        except Exception as measure_exc:
            measurement_results = [{
                "status": "error",
                "reason": str(measure_exc)[:200],
            }]

        # CPX_ADVISOR_MEASUREMENT_BACKLOG_NO_VISIBLE_RAISE_V1:
        # Planner/final guard already stop new raises at 2/2 unresolved causal
        # experiments. Advisor/UI must expose the same truth and must not show
        # actionable raise cards that cannot be executed this cycle.
        _raise_measurement_summary = _active_raise_measurement_summary(db, account_id)
        _raise_measurement_waiting = int(_raise_measurement_summary.get("active_count") or 0)
        _raise_measurement_cap = int(_raise_measurement_summary.get("cap") or MAX_ACTIVE_RAISE_MEASUREMENTS_PER_ACCOUNT)
        if _raise_measurement_waiting >= max(1, _raise_measurement_cap):
            block_raises = True
            limit_note = (
                f"Идут {_raise_measurement_waiting}/{_raise_measurement_cap} денежных измерения. "
                "Новые повышения ставок временно не формируются; BORIS сам возобновит их после завершения замера."
            )

        # 5) формула: разбор по объявлениям
        window = _aggregate_window(db, account_id)
        raise_bids, lower_bids, archive, watching = [], [], [], []
        for it in items_stats:
            if it.get("status") != "active":
                continue
            iid = it["id"]
            views = it.get("views", 0)
            contacts = it.get("contacts", 0)
            bid_info = bids_map.get(iid) if isinstance(bids_map.get(iid), dict) else {}
            bid = bid_info.get("bid_penny")
            min_bid = bid_info.get("min_bid_penny")
            rec_bid = bid_info.get("rec_bid_penny")
            provider_max_bid = bid_info.get("max_bid_penny")
            bid_rub = round(bid/100, 2) if isinstance(bid, int) else None
            min_bid_rub = round(min_bid/100, 2) if isinstance(min_bid, int) else None
            rec_bid_rub = round(rec_bid/100, 2) if isinstance(rec_bid, int) else None
            provider_max_bid_rub = round(provider_max_bid/100, 2) if isinstance(provider_max_bid, int) else None
            w = window.get(iid) or {"views": 0, "contacts": 0, "days": 0}
            v7, c7, d7 = w["views"], w["contacts"], w["days"]
            conv7 = round(c7 / v7 * 100, 1) if v7 else None
            measure_state = measure_states_by_item.get(int(iid)) or {}
            measure_started_at = measure_state.get("started_at")
            next_check_at = None
            if measure_state.get("status") == "waiting_measurement" and measure_started_at:
                try:
                    from datetime import datetime as _dt, timedelta as _td
                    next_check_at = (_dt.fromisoformat(str(measure_started_at)) + _td(days=MEASURE_MIN_DAYS)).isoformat()
                except Exception:
                    next_check_at = None
            last_action = measure_state.get("action")
            old_bid_rub = measure_state.get("old_bid_rub")
            new_bid_rub = measure_state.get("new_bid_rub")
            # HISTORICAL_CPX_ACTION_TRUTH_V2: a historical raise/lower is current
            # only when Avito currently reports exactly that live bid. If promotion
            # is now off/unreported, never present the old mutation as still active.
            # NATIVE_RECEIPT_MEASURE_UI_CONTINUITY_V1: a native item can be
            # content-read-only while its exact CPX mutation is already proven by
            # a successful execution receipt. Do not present that still-waiting
            # measurement as superseded merely because this aggregate snapshot
            # has no bid row. A later exact provider truth can still supersede it.
            _receipt_backed_native_wait = bool(
                measure_state.get("status") == "waiting_measurement"
                and str(measure_state.get("money_identity_proof") or "") == "cpx_execution_receipt"
                and str(measure_state.get("source") or "") == "account_low_views_ramp"
                and float(measure_state.get("old_bid_rub") or 0) <= 0
                and float(measure_state.get("new_bid_rub") or 0) > 0
            )
            measure_matches_current = True
            if last_action in {"raise", "lower"}:
                if new_bid_rub is None:
                    measure_matches_current = False
                elif bid_rub is None:
                    measure_matches_current = bool(_receipt_backed_native_wait)
                else:
                    try:
                        measure_matches_current = abs(float(new_bid_rub) - float(bid_rub)) < 0.01
                    except Exception:
                        measure_matches_current = False
            if last_action == "raise":
                boris_action_text = f"Поднял ставку: {old_bid_rub} ₽ → {new_bid_rub} ₽"
            elif last_action == "lower":
                boris_action_text = f"Снизил ставку: {old_bid_rub} ₽ → {new_bid_rub} ₽"
            elif last_action == "archive":
                boris_action_text = "Отключил продвижение"
            else:
                boris_action_text = "Ставку пока не менял"
            if last_action in {"raise", "lower"} and not measure_matches_current:
                current_truth = (
                    f"сейчас ставка {bid_rub} ₽" if bid_rub is not None
                    else "сейчас активная ставка Avito не подтверждена"
                )
                boris_action_text = (
                    f"Ранее менял ставку: {old_bid_rub} ₽ → {new_bid_rub} ₽; {current_truth}"
                )
                next_check_at = None

            # CPX_PROVIDER_EXACT_MONEY_WRITABILITY_V1: content/feed identity and
            # CPX money identity are different concerns. A native Avito item is
            # money-writable only when the authenticated CPX provider response
            # in THIS account returned this exact itemID in the same fresh cycle.
            # apply_one re-confirms getBids/{itemID} immediately before mutation.
            _raw_write_capability = str(it.get("write_capability") or "read_only_until_mapped")
            _provider_exact_money = bool(
                isinstance(bid_info, dict) and iid in bids_map
                and (_bids_truth_complete or int(iid) in _bids_exact_item_ids)
            )
            # CPX_PROVIDER_PROBE_MONEY_WRITABILITY_V1: an item present in the
            # fresh authenticated daily inventory may enter bid planning even if
            # it has no CampaignItem/content mapping. This grants NO provider
            # mutation by itself: apply_one must still receive HTTP 200 from the
            # exact account-scoped getBids/{itemID} immediately before setManual.
            _provider_probe_money = bool(
                _stats_money_fresh and _money_entitlement_active
                and str(iid).isdigit() and it.get("status") == "active"
            )
            _money_write_capability = (
                "canonical" if _raw_write_capability == "canonical" else
                "provider_exact_money" if _provider_exact_money else
                "provider_probe_money" if _provider_probe_money else
                _raw_write_capability
            )
            entry = {"id": iid, "title": it.get("title", "")[:50],
                     # суточные поля — legacy, на новом экране НЕ используются:
                     # они почти всегда нули и вводят в заблуждение
                     "views": views, "contacts": contacts,
                     "bid_rub": bid_rub,
                     "min_bid_rub": min_bid_rub,
                     "recommended_bid_rub": rec_bid_rub,
                     "provider_max_bid_rub": provider_max_bid_rub,
                     "promotion_active": bool(bid_info.get("promotion_active")),
                     "promotion_mode": bid_info.get("promotion_mode") or "off",
                     "write_capability": _money_write_capability,
                     "content_write_capability": _raw_write_capability,
                     "boris_last_action": last_action,
                     "boris_action_text": boris_action_text,
                     "boris_action_started_at": measure_started_at,
                     "boris_next_check_at": next_check_at,
                     "boris_measure_status": ("superseded" if (last_action in {"raise", "lower"} and not measure_matches_current) else measure_state.get("status")),
                     "boris_measure_effect": (None if (last_action in {"raise", "lower"} and not measure_matches_current) else measure_state.get("effect")),
                     "views_7d": v7, "contacts_7d": c7,
                     "conversion_7d": conv7, "days_with_data": d7}

            # CPX_ADVISOR_SERVICE_ENTITLEMENT_GATE_V1: expired/inactive money
            # service is diagnostic-only. No raise/lower recommendation survives
            # this boundary, regardless of historical winner state.
            if not _money_entitlement_active:
                entry["reason_code"] = "service_period_ended"
                entry["why"] = "Оплаченный период AI-маркетолога завершён или не подтверждён; денежные изменения отключены."
                entry["suggest"] = "наблюдать; возобновлять денежные действия только после нового подтверждённого периода услуги"
                watching.append(entry)
                continue

            # CPX_ADVISOR_MONEY_IDENTITY_V2: content mapping is not required for
            # bid-only management. Fresh authenticated inventory can be probed,
            # while apply_one independently re-proves the exact item through Avito
            # getBids/{itemID} before any monetary mutation.
            if str(entry.get("write_capability") or "") not in {"canonical", "provider_exact_money", "provider_probe_money"}:
                entry["reason_code"] = "read_only_until_money_identity_proven"
                entry["why"] = "Объявление видно в статистике, но Avito CPX ещё не подтвердил точный itemID в этом аккаунте для денежной записи."
                entry["suggest"] = "наблюдать; денежное изменение разрешится автоматически после точного CPX-подтверждения itemID"
                watching.append(entry)
                continue

            # RED_CPL_CURRENT_DAY_PAID_WASTE_V1:
            # Historical converters are not immune to a bad current day. Once
            # the account has already met today's lead target, confirmed account
            # CPL is red, and one active paid listing itself collected >=10
            # current-day views with zero contacts, a single 10% lower is a
            # spend-reducing recovery — not an archive and not a raise. This
            # reuses the same 10-view boundary where intraday reach-rescue stops
            # buying more impressions without contact evidence.
            _red_cpl_current_day_paid_waste = bool(
                _cpl_redline_block
                and target_leads > 0
                and float(total_contacts or 0) >= float(target_leads)
                and bool(entry.get("promotion_active"))
                and bid_rub is not None
                and float(bid_rub) > 0
                and int(contacts or 0) == 0
                and int(views or 0) >= RED_CPL_CURRENT_DAY_MIN_VIEWS
            )
            if _red_cpl_current_day_paid_waste:
                entry["reason_code"] = "red_cpl_paid_zero_contact_waste"
                entry["red_cpl_evidence"] = "current_day_10plus_zero_contact_after_kpi_met"
                entry["why"] = (
                    f"Сегодня {int(views or 0)} просмотров и 0 обращений при активной ставке "
                    f"{float(bid_rub):.0f} ₽; KPI дня уже выполнен, CPL аккаунта "
                    f"{float(acct_cpl):.0f} ₽ выше лимита {float(max_cpl):.0f} ₽ — "
                    "безопасно снизить ставку одним шагом"
                )
                entry["suggest"] = "снизить ставку на один безопасный шаг и продолжить наблюдение"
                archive.append(entry)
                continue

            if c7 > 0:
                entry["reason_code"] = "has_contacts"
                entry["why"] = f"{c7} контакт(ов) на {v7} просмотров за {WINDOW_DAYS} дней — работает"
                if block_raises:
                    entry["reason_code"] = "limit"
                    entry["suggest"] = "поднятие заблокировано суточным лимитом"
                    watching.append(entry)
                else:
                    entry["suggest"] = f"можно поднять ставку на ~{BID_STEP_PCT}% для большего трафика"
                    raise_bids.append(entry)
            elif v7 < MIN_VIEWS_FOR_JUDGEMENT_7D:
                entry["reason_code"] = "no_data"
                entry["why"] = f"{v7} просмотров за {WINDOW_DAYS} дней — объявление почти не показывается"
                watching.append(entry)
            elif v7 < ARCHIVE_MIN_VIEWS_7D:
                entry["reason_code"] = "few_views"
                entry["why"] = f"{v7} просмотров, 0 обращений — пока рано делать вывод"
                watching.append(entry)
            elif _is_archived(db, account_id, iid):
                entry["reason_code"] = "already_off"
                entry["why"] = f"{v7} просмотров, 0 обращений — продвижение уже отключено"
                watching.append(entry)
            else:
                # UNDER_KPI_GROWTH_BEFORE_SHRINK_V1:
                # When the account is below the lead target, lack of contacts is
                # not permission to shrink reach. Keep the listing/promotion in
                # the growth lane and improve/expand content first. Cost cutting
                # remains available only after KPI recovery or a proven red-price
                # guard elsewhere in the money policy.
                _red_cpl_paid_waste = bool(
                    _cpl_redline_block
                    and bool(entry.get("promotion_active"))
                    and bid_rub is not None
                    and float(bid_rub) > 0
                )
                if target_leads > 0 and total_contacts < target_leads and not _red_cpl_paid_waste:
                    entry["reason_code"] = "under_kpi_preserve_reach"
                    entry["why"] = (
                        f"{v7} просмотров за {WINDOW_DAYS} дней, 0 обращений; "
                        f"план {target_leads:g}/день, факт {total_contacts:g} — охват сокращать нельзя"
                    )
                    entry["suggest"] = (
                        "сохранить объявление и продвижение; подготовить новую уникальную связку "
                        "заголовок/фото/оффер и расширять охват, ставку менять только в пределах "
                        "денежного лимита и красной цены"
                    )
                    watching.append(entry)
                else:
                    # Архив НЕ применяем автоматически — только рекомендация.
                    # При красном account-CPL сюда допускается только реально
                    # активное платное продвижение с достаточным zero-contact
                    # evidence; executor затем снижает максимум один item за цикл.
                    entry["reason_code"] = (
                        "red_cpl_paid_zero_contact_waste" if _red_cpl_paid_waste else "no_contacts"
                    )
                    entry["why"] = (
                        f"{v7} просмотров за {WINDOW_DAYS} дней, 0 обращений; "
                        f"CPL аккаунта {float(acct_cpl):.0f} ₽ выше лимита {float(max_cpl):.0f} ₽ — платное продвижение нужно удешевить"
                        if _red_cpl_paid_waste else
                        f"{v7} просмотров за {WINDOW_DAYS} дней, 0 обращений — деньги уходят впустую"
                    )
                    entry["suggest"] = ("снизить ставку и понаблюдать неделю;"
                                        " если и на низкой ставке нет обращений —"
                                        " переработать текст или снять продвижение")
                    archive.append(entry)

        try:
            from app.services.avito_position_monitor import get_position_status as _get_position_status
            _position_status = _get_position_status(account_id, history_limit=1) or {}
            _position_health = _position_status.get("health") if isinstance(_position_status.get("health"), dict) else {}
            _position_latest = _position_status.get("latest") if isinstance(_position_status.get("latest"), dict) else {}
            _position_monitor = {
                "state": _position_health.get("state") or "unknown",
                "reason": _position_health.get("reason") or "",
                "measured_at": _position_latest.get("measured_at"),
                "matched": _position_health.get("matched"),
                "inventory": _position_health.get("inventory"),
                "coverage_pct": _position_health.get("coverage_pct"),
                "coverage_complete": bool(_position_health.get("coverage_complete", False)),
                "read_only": True,
            }
        except Exception as _position_exc:
            _position_monitor = {
                "state": "deferred",
                "reason": f"position status unavailable: {type(_position_exc).__name__}",
                "measured_at": None,
                "read_only": True,
            }

        # CPX_ADVISOR_MONEY_WRITABILITY_SUMMARY_V1: expose whether the money
        # workstream can actually mutate the currently active inventory. This keeps
        # read-only mapping gaps visible without pretending they are raise candidates.
        _active_items=[i for i in items_stats if i.get("status")=="active"]
        # CPX_ADVISOR_EFFECTIVE_MONEY_WRITABILITY_V3: report the same effective
        # identity that recommendation planning uses above. Fresh authenticated
        # active inventory is probe-eligible even when its content mapping is
        # intentionally read-only; the final exact getBids/{itemID} proof still
        # gates every provider mutation in apply_one.
        _effective_probe_allowed = bool(_stats_money_fresh and _money_entitlement_active)
        _money_writable=sum(
            1 for i in _active_items
            if (
                str(i.get("write_capability") or "") in {"canonical", "provider_exact_money", "provider_probe_money"}
                or (_effective_probe_allowed and str(i.get("id") or "").isdigit())
            )
        )
        _money_read_only=len(_active_items)-_money_writable

        # CPX_ADVISOR_BUDGET_BRAKE_OWNER_ACTION_V1: money advice must surface
        # the hard budget brake's external blocker instead of saying "nothing
        # required" while the account is already over its owner daily limit.
        _brake_state = _load_json(db, account_id, "cpx_budget_brake_runtime") or {}
        _nested_presence = (
            _brake_state.get("presence_budget_pressure")
            if isinstance(_brake_state.get("presence_budget_pressure"), dict)
            else {}
        )
        # CPX_ADVISOR_PRESENCE_RUNTIME_FRESHEST_V1:
        # cpx_budget_brake_runtime embeds a point-in-time projection of the
        # presence policy. The dedicated presence_budget_pressure_runtime may be
        # refreshed later by the same canonical money policy. Prefer that newer
        # account/budget-matched projection so owner advice cannot lag behind
        # self-heal state; fall back to the nested brake copy if validation fails.
        _presence_runtime = _load_json(db, account_id, "presence_budget_pressure_runtime") or {}
        _brake_pressure = _nested_presence
        try:
            def _presence_checked_at(_x):
                _raw = str((_x or {}).get("checked_at") or "").strip()
                if not _raw:
                    return None
                _dt = datetime.fromisoformat(_raw.replace("Z", "+00:00"))
                if _dt.tzinfo is None:
                    _dt = _dt.replace(tzinfo=timezone.utc)
                return _dt.astimezone(timezone.utc)
            _dedicated_pressure = (
                _presence_runtime.get("pressure")
                if isinstance(_presence_runtime.get("pressure"), dict)
                else {}
            )
            _dedicated_valid = bool(
                str(_presence_runtime.get("account_id") or "") == str(account_id)
                and abs(
                    float(_dedicated_pressure.get("daily_budget_limit_rub") or 0)
                    - float(daily_limit or 0)
                ) < 1e-9
            )
            _dedicated_at = _presence_checked_at(_presence_runtime) if _dedicated_valid else None
            _nested_at = _presence_checked_at(_nested_presence)
            if _dedicated_at is not None and (
                _nested_at is None or _dedicated_at >= _nested_at
            ):
                _brake_pressure = _presence_runtime
        except Exception:
            _brake_pressure = _nested_presence
        # CPX_ADVISOR_PRESENCE_BLOCK_OWNER_VISIBILITY_V1:
        # Structural paid-presence pressure is an external business decision even
        # when today's same-day spend is still "within_budget". Hiding it behind
        # the same-day brake status leaves the account paused with no visible
        # recovery action. Surface canonical presence-policy owner action
        # independently; ordinary same-day over-limit logic remains unchanged.
        _presence_owner_action_required = bool(_brake_pressure.get("owner_action_required"))
        _same_day_over_limit = bool(
            _brake_state.get("status") in {"braked", "deferred"}
            and float(_brake_state.get("spent_today_rub") or 0)
                > float(_brake_state.get("daily_budget_limit_rub") or 0) > 0
        )
        _brake_owner_action_required = bool(
            _presence_owner_action_required or _same_day_over_limit
        )

        # CPX_ADVISOR_BALANCE_OWNER_ACTION_PRIORITY_V1:
        # Guardian is the canonical wallet observer. If its fresh account/KPI-
        # matched evidence says the real Avito wallet is below one red CPL and
        # all KPI mutation is paused, funding is the FIRST owner action. Asking
        # the owner to tune budget/KPI first would not unlock the next safe step.
        _balance_health = _load_json(db, account_id, "guardian_balance_funding_health") or {}
        _balance_owner_action_required = False
        _balance_owner_action = None
        _balance_health_fresh = False
        try:
            _bh_checked_raw = str(_balance_health.get("checked_at") or "").strip()
            _bh_checked = datetime.fromisoformat(_bh_checked_raw.replace("Z", "+00:00")) if _bh_checked_raw else None
            if _bh_checked is not None and _bh_checked.tzinfo is None:
                _bh_checked = _bh_checked.replace(tzinfo=timezone.utc)
            _bh_age = (
                (datetime.now(timezone.utc) - _bh_checked.astimezone(timezone.utc)).total_seconds()
                if _bh_checked is not None else None
            )
            _balance_health_fresh = bool(_bh_age is not None and -300 <= _bh_age <= 900)
            _bh_real = float(_balance_health.get("real_balance_rub"))
            _bh_red = float(_balance_health.get("max_cpl_rub") or 0)
            _bh_target = float(_balance_health.get("target_leads_per_day") or 0)
            _bh_budget = float(_balance_health.get("daily_budget_limit_rub") or 0)
            _balance_owner_action_required = bool(
                _balance_health_fresh
                and _balance_health.get("service_active") is True
                and _balance_health.get("work_pause_required") is True
                and _balance_health.get("owner_action_required") is True
                and str(_balance_health.get("reason") or "") == "avito_balance_below_one_red_cpl"
                and abs(_bh_red - float(max_cpl or 0)) < 1e-9
                and abs(_bh_target - float(target_leads or 0)) < 1e-9
                and abs(_bh_budget - float(daily_limit or 0)) < 1e-9
                and float(total_contacts or 0) < float(target_leads or 0)
                and _bh_real < float(max_cpl or 0)
            )
            if _balance_owner_action_required:
                _balance_owner_action = (
                    f"Реальный баланс Avito {_bh_real:.0f} ₽ ниже максимальной цены обращения "
                    f"{float(max_cpl):.0f} ₽. Пополните баланс Avito так, чтобы реальный баланс "
                    f"был не ниже {float(max_cpl):.0f} ₽. BORIS сам увидит пополнение и "
                    "автоматически продолжит работу."
                )
        except Exception:
            _balance_owner_action_required = False
            _balance_owner_action = None
        _brake_owner_action = str(_brake_pressure.get("owner_action") or "").strip()
        if _brake_owner_action_required and not _brake_owner_action:
            _brake_owner_action = (
                f"Дневной рекламный лимит превышен: подтверждённый расход "
                f"{float(_brake_state.get('spent_today_rub') or 0):.0f} ₽ при лимите "
                f"{float(_brake_state.get('daily_budget_limit_rub') or 0):.0f} ₽. "
                "BORIS уже остановил новые повышения/платное ускорение; требуется внешний шаг только если базовое платное размещение Avito продолжает списания."
            )

        # CPX_ADVISOR_BAD_CPL_NO_BUDGET_ESCALATION_V1:
        # If current confirmed CPL is already above the owner's red line,
        # authorizing more spend cannot unlock a safe raise. Keep this BORIS-owned
        # and do not ask the owner to add/confirm budget merely to spend more.
        try:
            _cpl_redline_block = bool(
                acct_cpl is not None and float(max_cpl or 0) > 0
                and float(acct_cpl) > float(max_cpl)
            )
        except Exception:
            _cpl_redline_block = False

        # CPX_ADVISOR_TARGET_BUDGET_NOT_WORST_CASE_V1:
        # target_leads * max_cpl is a worst-case envelope, not a required daily
        # budget. BORIS must pursue the target inside the owner's explicit hard
        # budget using observed CPL. The final mutation guard independently
        # blocks bad CPL, stale spend, exhausted budget, low balance and unsafe
        # provider state, so this theoretical product must never create a false
        # owner task to increase budget or relax KPI.
        advice = {
            "generated_at": __import__("datetime").datetime.now().isoformat(),
            "account_id": account_id,
            "target": {"max_cpl_rub": max_cpl, "target_leads_per_day": target_leads},
            "fact": {"contacts_today": total_contacts, "spent_today_rub": spent,
                     "daily_limit_rub": daily_limit, "limit_pct": limit_pct, "limit_note": limit_note,
                     "daily_budget_authorized": bool(_budget_authorized),
                     "marketing_service_entitlement_state": _money_entitlement_state,
                     "daily_budget_authorization_source": (_budget_auth.get("source") if _budget_authorized else None),
                     "account_cpl_rub": acct_cpl,
                     "spend_signal": {
                         "status": spend_signal.get("status"),
                         "age_seconds": spend_signal.get("age_seconds"),
                         "reason": spend_signal.get("reason"),
                         "spent_today_rub": spend_signal.get("spent_today_rub"),
                     },
                     "item_signal_date": _item_stats_day or None,
                     "item_signal_current_day": bool(_item_signal_current_day),
                     "data_note": None if acct_cpl is not None else ("Цена обращения пока не рассчитана: нужен свежий подтверждённый расход Avito и хотя бы одно обращение текущего московского дня." if spent is None or total_contacts == 0 else "Цена обращения пока не рассчитана по текущим данным.")},
            "position_monitor": _position_monitor,
            "recommendations": {
                "raise": raise_bids,
                # Слабое объявление сначала УДЕШЕВЛЯЕМ и наблюдаем, и только
                # потом обсуждаем снятие. Кому снижать уже некуда — отсечёт
                # apply_one ранним no-op (skipped_bid_unchanged).
                "lower": archive,
                "archive_candidates": archive,
                "lower_or_archive": archive,
                "watching": watching,
            },
            "money_writability": {"canonical_writable_active": _money_writable, "read_only_active": _money_read_only},
            "summary": f"Активных: {len(_active_items)}. Денежно управляемых: {_money_writable}. "
                       f"Read-only до mapping: {_money_read_only}. Работают (есть лиды и право записи): {len(raise_bids)}. "
                       f"Требуют внимания: {len(archive)}. Наблюдаю: {len(watching)}.",
            "measurement_cycle": {
                "checked": len(measurement_results),
                "measured": len([x for x in measurement_results if x.get("status") == "measured"]),
                "waiting": _raise_measurement_waiting,
                "cap": _raise_measurement_cap,
                "errors": len([x for x in measurement_results if x.get("status") == "error"]),
                "backlog_compaction": measurement_compaction,
            },
            "mode": "advisor_only_no_actions",
            # CPX_ADVISOR_OWNER_ACTION_MINIMAL_V1: never ask the owner for a
            # money input that cannot unlock the next action. If all active
            # inventory is read-only, mapping is an internal BORIS workstream
            # blocker; budget confirmation would only make the owner an operator.
            "owner_action_required": bool(
                _brake_owner_action_required
                or _balance_owner_action_required
                or (
                    _money_entitlement_active
                    and _bid_autopilot_enabled
                    and not (_money_writable == 0 and _money_read_only > 0)
                    and not _cpl_redline_block
                    and (not max_cpl or not daily_limit or not _budget_authorized)
                )
            ),
            "owner_action": (
                _brake_owner_action if _brake_owner_action_required else
                _balance_owner_action if _balance_owner_action_required else
                "Оплаченный период AI-маркетолога не активен; денежные действия закрыты и не считаются сбоем." if not _money_entitlement_active else
                "Автоматическое управление ставками выключено владельцем; денежных действий BORIS не выполняет, дополнительная настройка не требуется." if not _bid_autopilot_enabled else
                "Денежных действий сейчас нет: активные объявления read-only до канонического mapping; это внутренняя задача BORIS, действие владельца не требуется." if _money_writable == 0 and _money_read_only > 0 else
                f"Текущий CPL {float(acct_cpl):.0f} ₽ выше лимита {float(max_cpl):.0f} ₽. Повышение ставок заблокировано; действие владельца не требуется — BORIS продолжит безопасную оптимизацию и измерение." if _cpl_redline_block else
                "Задайте максимальную цену обращения и суточный бюджет." if not max_cpl else
                "Задайте суточный рекламный бюджет, если хотите разрешить BORIS повышать ставки." if not daily_limit else
                f"Подтвердите сохранённый суточный рекламный бюджет {float(daily_limit):.0f} ₽ в настройках аккаунта. BORIS не будет повышать ставки, пока владелец не подтвердит этот лимит." if not _budget_authorized else
                "Не требуется — BORIS продолжит денежный контур автоматически."
            ),
            "next_check_text": "Следующая автоматическая проверка — по часовому циклу BORIS.",
        }
        _save_json(db, account_id, "cpx_advice", advice)
        return {"status": "ok", "advice": advice}
    finally:
        db.close()


@router.get("/last")
def last_advice(account_id: str = "otdushi"):
    """Последние рекомендации Советника (для показа в интерфейсе)."""
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        a = _load_json(db, account_id, "cpx_advice")
        return {"status": "ok", "advice": a} if a else {"status": "empty", "message": "Советник ещё не отрабатывал"}
    finally:
        db.close()


# ============ ПРИМЕНЕНИЕ РЕКОМЕНДАЦИЙ (реальные действия с деньгами) ============
from pydantic import BaseModel

# Предохранители автобиддера
from app.services.marketing_money_policy import GLOBAL_MAX_BID_DELTA_PCT, effective_bid_step_cap_pct
MAX_BID_STEP_PCT = GLOBAL_MAX_BID_DELTA_PCT  # глобальный hard ceiling шага
MEASURE_MIN_DAYS = 1         # DAILY_BID_LEARNING_V1: ежедневный feedback после первого полного дня
MEASURE_NO_SIGNAL_MAX_DAYS = 7  # MEASURE_NO_SIGNAL_TIMEOUT_V1: never lock an item forever
MEASURE_COOLDOWN_DAYS = 1    # DAILY_BID_LEARNING_V1: после суточного verdict следующий шаг не раньше следующего дня
# ACCOUNT_RAISE_MEASUREMENT_BACKLOG_CAP_V1:
# Keep at most two unresolved autonomous raise hypotheses per account. This is
# an evidence/causality ceiling, not a spend quota. Safety lower/reset actions
# remain available even while raise experiments are waiting.
MAX_ACTIVE_RAISE_MEASUREMENTS_PER_ACCOUNT = 2
DEFAULT_MIN_BID_RUB = 3      # минимальная ставка при снижении, ₽
NEW_ITEM_NO_PROMO_MIN_BID_MARKUP_PCT = GLOBAL_MAX_BID_DELTA_PCT  # NEW_FEED_FIRST_BID_GLOBAL_STEP_V1: first activation shares the same <=10% global money step
ARCHIVE_MIN_VIEWS = 50       # архивировать только при >= стольких показов без контактов


class ApplyOneBody(BaseModel):
    max_bid_delta_pct: int | None = None
    # LATE_DAY_SPEND_CATCHUP_V1: ordinary evening acceleration still uses the
    # global <=10% per-write step. The target is cumulative metadata only.
    temporary_boost_target_pct: int | None = None
    # LATE_DAY_SPEND_RESET_V1: only the exact baseline persisted by BORIS before
    # a temporary evening raise may be supplied here, and only for the dedicated
    # lower-only compensation source. It is re-verified in apply_one + autonomy.
    target_bid_rub: float | None = None
    account_id: str = "otdushi"
    item_id: int
    action: str              # "raise" | "lower" | "archive"
    actor_type: str = "user"
    actor_user_id: str = ""
    source: str = ""
    request_id: str = ""
    trigger: str = ""


def _applied_key(item_id, action, generated_at):
    """Ключ применения. Включает версию рекомендации: после нового расчёта
    советника то же действие снова становится доступным, иначе однажда
    изменённое объявление заблокировалось бы навсегда."""
    return "%s:%s:%s" % (item_id, action, generated_at or "")


def _cpx_request_key(body, generated_at=""):
    """Stable account-scoped identity for one logical live CPX mutation."""
    raw = str(getattr(body, "request_id", "") or "").strip()
    if raw:
        return raw[:240]
    # Manual UI callers historically omit request_id. Bind them to the current
    # advice generation so a browser retry cannot repeat the same money action.
    return _applied_key(getattr(body, "item_id", ""), getattr(body, "action", ""), generated_at)[:240]


def _native_provider_probe_first_bid_preflight(db, body):
    """NATIVE_PROVIDER_PROBE_FIRST_BID_V1.

    A native Avito listing may start CPX from zero only inside the trusted
    hourly low-views service and only when the fresh advisor already proved the
    exact item as provider_probe_money + has_contacts. This does not grant
    content/feed write authority. The exact getBids/{itemID} provider proof,
    paid-tariff mandate, money guards, receipt and hard cap still run later in
    apply_one before setManual.
    """
    if not (
        str(getattr(body, "action", "") or "") == "raise"
        and str(getattr(body, "actor_type", "") or "").lower() in {"boris_auto", "autopilot", "system"}
        and str(getattr(body, "source", "") or "") == "account_low_views_ramp"
        and str(getattr(body, "trigger", "") or "") == "daily_views_below_30"
    ):
        return {"eligible": False, "reason_code": "not_native_first_bid_lane"}

    # Source strings are not authority. Require the canonical systemd caller
    # that already owns ACCOUNT_LOW_VIEWS_SERVICE_CALLER_GUARD_V2.
    try:
        from pathlib import Path as _Path_native_fb
        _cgroup = _Path_native_fb("/proc/self/cgroup").read_text(errors="ignore")
    except Exception:
        _cgroup = ""
    if "boris-ai-marketer.service" not in _cgroup:
        return {"eligible": False, "blocked": True,
                "reason_code": "untrusted_native_first_bid_caller"}

    # NATIVE_FIRST_BID_MAX_TWO_EXPERIMENTS_V1:
    # A first activation is a causal experiment, not a quota to consume. Allow
    # at most TWO receipt-backed native first-bid measurements concurrently per
    # account; this is the bounded 1-2 hypothesis policy. A third paid hypothesis
    # is blocked until one of the first two gets a terminal measurement.
    from app.models.storage import Storage as _NativeFirstBidStorage
    _active_native_first_measurements = []
    for _mr in db.query(_NativeFirstBidStorage).filter(
        _NativeFirstBidStorage.account_id == body.account_id,
        _NativeFirstBidStorage.key.like("cpx_measure:%"),
    ).all():
        try:
            _ms = json.loads(_mr.value or "{}")
        except Exception:
            continue
        try:
            _old_native = float(_ms.get("old_bid_rub") or 0)
            _new_native = float(_ms.get("new_bid_rub") or 0)
        except Exception:
            _old_native = _new_native = 0
        if (
            _ms.get("status") == "waiting_measurement"
            and str(_ms.get("source") or "") == "account_low_views_ramp"
            and str(_ms.get("money_identity_proof") or "") == "cpx_execution_receipt"
            and _old_native <= 0 < _new_native
        ):
            _active_native_first_measurements.append({
                "item_id": int(_ms.get("item_id") or 0),
                "started_at": _ms.get("started_at"),
                "new_bid_rub": _new_native,
            })
    _native_first_measurement_cap = 2
    if len(_active_native_first_measurements) >= _native_first_measurement_cap:
        return {
            "eligible": False,
            "blocked": True,
            "reason_code": "native_first_bid_measurement_wait",
            "active_measurements": _active_native_first_measurements[:5],
            "active_measurement_cap": _native_first_measurement_cap,
        }

    advice = _load_json(db, body.account_id, "cpx_advice") or {}
    generated_at = str(advice.get("generated_at") or "").strip()
    advice_fresh = False
    advice_age_sec = None
    if generated_at:
        try:
            from datetime import datetime as _dt_native_fb, timezone as _tz_native_fb
            _gen = _dt_native_fb.fromisoformat(generated_at.replace("Z", "+00:00"))
            if _gen.tzinfo is None:
                _gen = _gen.replace(tzinfo=_tz_native_fb.utc)
            advice_age_sec = max(0.0, (_dt_native_fb.now(_tz_native_fb.utc) - _gen.astimezone(_tz_native_fb.utc)).total_seconds())
            advice_fresh = advice_age_sec <= 7200
        except Exception:
            advice_fresh = False

    candidate = None
    raises = ((advice.get("recommendations") or {}).get("raise") or [])
    for rec in raises if isinstance(raises, list) else []:
        if not isinstance(rec, dict):
            continue
        try:
            rid = int(rec.get("id") or rec.get("item_id") or 0)
        except Exception:
            rid = 0
        if rid == int(body.item_id):
            candidate = rec
            break

    proven = bool(
        advice_fresh
        and isinstance(candidate, dict)
        and str(candidate.get("write_capability") or "") == "provider_probe_money"
        and str(candidate.get("reason_code") or "") == "has_contacts"
        and int(candidate.get("contacts_7d") or 0) > 0
        and not bool(candidate.get("promotion_active"))
        and str(candidate.get("boris_measure_status") or "") != "waiting_measurement"
    )
    if not proven:
        return {"eligible": False, "reason_code": "native_first_bid_advice_not_proven",
                "advice_generated_at": generated_at or None,
                "advice_age_sec": round(advice_age_sec, 1) if advice_age_sec is not None else None}

    # NATIVE_PROVIDER_PROBE_FIRST_BID_DAILY_CAP2_V1:
    # A large under-KPI account may initialize at most TWO proven native
    # converters per Moscow day. This matches the evidence-backlog policy
    # (1-2 concurrent hypotheses) without leaving hundreds of ads idle.
    # Any unresolved/ambiguous provider receipt remains a full stop: never fan
    # out to a second item while the first delivery outcome is unknown.
    from sqlalchemy import text as _native_fb_text
    _native_daily_cap = 2
    _receipt_counts = db.execute(_native_fb_text("""
        SELECT
          count(*) FILTER (WHERE status IN ('prepared','attempting','delivery_unknown')) AS unresolved,
          count(*) FILTER (WHERE status IN ('succeeded','reconciled')) AS completed
          FROM cpx_execution_receipts
         WHERE account_id=:a
           AND action='raise'
           AND source='account_low_views_ramp'
           AND old_bid_penny=0
           AND (created_at AT TIME ZONE 'Europe/Moscow')::date
               = (now() AT TIME ZONE 'Europe/Moscow')::date
    """), {"a": body.account_id}).mappings().one()
    _unresolved_native = int(_receipt_counts.get("unresolved") or 0)
    _completed_native = int(_receipt_counts.get("completed") or 0)
    if _unresolved_native > 0:
        return {"eligible": False, "blocked": True,
                "reason_code": "native_first_bid_unresolved_receipt",
                "unresolved_first_activations": _unresolved_native,
                "completed_first_activations_today": _completed_native,
                "daily_cap": _native_daily_cap,
                "advice_generated_at": generated_at}
    if _completed_native >= _native_daily_cap:
        return {"eligible": False, "blocked": True,
                "reason_code": "native_first_bid_daily_cap",
                "prior_first_activations_today": _completed_native,
                "daily_cap": _native_daily_cap,
                "advice_generated_at": generated_at}

    return {
        "eligible": True,
        "reason_code": "native_provider_probe_first_bid_proven",
        "advice_generated_at": generated_at,
        "advice_age_sec": round(advice_age_sec, 1) if advice_age_sec is not None else None,
        "write_capability": "provider_probe_money",
        "contacts_7d": int(candidate.get("contacts_7d") or 0),
    }


def _provider_alias_recent_receipt(account_id, item_id, action, window_days=7):
    """Fail closed when another BORIS alias of the same real Avito account
    already has an unresolved/recent money mutation for the same listing/action.

    Internal account_id aliases must never multiply provider-side paid actions.
    The seven-day window mirrors the CPX measurement/cooldown contract and does
    not merge UI state or entitlements between aliases.
    """
    from sqlalchemy import text as _t, bindparam as _bindparam
    from app.db.session import SessionLocal as _SL
    from app.models.account import Account as _Account
    d = _SL()
    try:
        acc = d.query(_Account).filter(_Account.account_id == str(account_id)).first()
        uid = str(getattr(acc, "avito_user_id", "") or "").strip() if acc else ""
        if not uid:
            return None
        aliases = [str(x[0]) for x in d.query(_Account.account_id).filter(_Account.avito_user_id == uid).all() if x and x[0]]
        aliases = sorted(set(aliases))
        if len(aliases) <= 1:
            return None
        stmt = _t("""
            SELECT account_id,request_key,item_id,action,status,created_at,updated_at
            FROM cpx_execution_receipts
            WHERE account_id IN :aliases
              AND account_id <> :current_account
              AND item_id = :item_id
              AND action = :action
              AND status IN ('prepared','attempting','delivery_unknown','succeeded','reconciled')
              AND created_at >= now() - (:window_days * interval '1 day')
            ORDER BY created_at DESC,id DESC
            LIMIT 1
        """).bindparams(_bindparam("aliases", expanding=True))
        row = d.execute(stmt, {
            "aliases": aliases,
            "current_account": str(account_id),
            "item_id": int(item_id),
            "action": str(action),
            "window_days": max(1.0/24.0, min(float(window_days or 7), 30.0)),
        }).mappings().first()
        if not row:
            return None
        out = dict(row)
        out["provider_uid"] = uid
        out["aliases"] = aliases
        return out
    finally:
        d.close()


def _cpx_receipt_get(account_id, request_key):
    from sqlalchemy import text as _t
    from app.db.session import SessionLocal as _SL
    d=_SL()
    try:
        row=d.execute(_t("SELECT * FROM cpx_execution_receipts WHERE account_id=:a AND request_key=:k LIMIT 1"),{"a":str(account_id),"k":str(request_key)}).mappings().first()
        return dict(row) if row else None
    finally:d.close()


def _cpx_receipt_prepare(account_id, request_key, item_id, action, source, old_bid_penny=None, intended_bid_penny=None):
    """Atomically reserve a logical mutation. Returns (receipt, created)."""
    from sqlalchemy import text as _t
    from app.db.session import SessionLocal as _SL
    d=_SL()
    try:
        try:
            row=d.execute(_t("""INSERT INTO cpx_execution_receipts(account_id,request_key,item_id,action,source,status,old_bid_penny,intended_bid_penny)
              VALUES(:a,:k,:i,:x,:s,'prepared',:o,:n)
              ON CONFLICT(account_id,request_key) DO NOTHING RETURNING *"""),
              {"a":str(account_id),"k":str(request_key),"i":int(item_id),"x":str(action),"s":str(source or "")[:120],"o":old_bid_penny,"n":intended_bid_penny}).mappings().first()
        except Exception as exc:
            # CPX_ATOMIC_CAPACITY_BLOCK_NORMALIZE_V1:
            # PostgreSQL owns the final race-free blast-radius guard. Hitting that
            # guard is an expected blocked business outcome, not a worker crash.
            # Normalize only the two explicit trigger messages; every unrelated DB
            # error is still raised so real storage failures remain visible.
            _msg = str(exc)
            if "blocked_max_actions_run_atomic" in _msg or "blocked_max_actions_day_atomic" in _msg:
                d.rollback()
                _reason = ("blocked_max_actions_run"
                           if "blocked_max_actions_run_atomic" in _msg
                           else "blocked_max_actions_day")
                return {
                    "status": "capacity_blocked",
                    "reason_code": _reason,
                    "error": _msg[:240],
                }, False
            d.rollback()
            raise
        d.commit()
        if row:return dict(row),True
        row=d.execute(_t("SELECT * FROM cpx_execution_receipts WHERE account_id=:a AND request_key=:k"),{"a":str(account_id),"k":str(request_key)}).mappings().one()
        return dict(row),False
    finally:d.close()


def _cpx_receipt_mark(account_id, request_key, status, *, http_status=None, observed_bid_penny=None, result=None, error=None):
    import json as _j
    from sqlalchemy import text as _t
    from app.db.session import SessionLocal as _SL
    d=_SL()
    try:
        d.execute(_t("""UPDATE cpx_execution_receipts SET status=:st,provider_http_status=:h,observed_bid_penny=:ob,
          result_json=CAST(:r AS jsonb),error_text=:e,attempted_at=COALESCE(attempted_at,now()),
          finished_at=CASE WHEN :st IN ('succeeded','failed','reconciled') THEN now() ELSE finished_at END,updated_at=now()
          WHERE account_id=:a AND request_key=:k"""),
          {"st":str(status),"h":http_status,"ob":observed_bid_penny,"r":_j.dumps(result or {},ensure_ascii=False),"e":str(error or "")[:500] or None,"a":str(account_id),"k":str(request_key)})
        d.commit()
    finally:d.close()


def _cpx_measure_key(item_id, action):
    """
    Ключ состояния измерения эффекта после изменения ставки.
    """
    return f"cpx_measure:{item_id}:{action}"


def _active_raise_measurement_summary(db, account_id):
    """DB-only count of unresolved raise experiments for one account."""
    from app.models.storage import Storage as _MeasureBacklogStorage
    active = {}
    for row in db.query(_MeasureBacklogStorage).filter(
        _MeasureBacklogStorage.account_id == account_id,
        _MeasureBacklogStorage.key.like("cpx_measure:%:raise"),
    ).all():
        try:
            state = json.loads(row.value or "{}")
        except Exception:
            continue
        if str(state.get("status") or "") != "waiting_measurement":
            continue
        try:
            iid = int(state.get("item_id") or str(row.key).split(":")[1])
        except Exception:
            continue
        active[iid] = {
            "item_id": iid,
            "source": state.get("source"),
            "started_at": state.get("started_at"),
            "old_bid_rub": state.get("old_bid_rub"),
            "new_bid_rub": state.get("new_bid_rub"),
        }
    rows = sorted(active.values(), key=lambda x: str(x.get("started_at") or ""))
    return {
        "active_count": len(rows),
        "cap": MAX_ACTIVE_RAISE_MEASUREMENTS_PER_ACCOUNT,
        "items": rows,
    }


def _compact_raise_measurement_backlog(db, account_id, cap=MAX_ACTIVE_RAISE_MEASUREMENTS_PER_ACCOUNT):
    """Self-heal legacy over-parallel raise measurements without inventing verdicts.

    Keep at most cap unresolved experiments. Prefer one true incremental
    paid-bid experiment (old_bid>0), then preserve source diversity where
    possible. Excess rows become explicit inconclusive/superseded evidence;
    Avito is never called and no winner/loser learning is emitted.
    """
    from datetime import datetime as _BacklogDt, timezone as _BacklogTz
    from app.models.storage import Storage as _BacklogStorage

    waiting = []
    for row in db.query(_BacklogStorage).filter(
        _BacklogStorage.account_id == account_id,
        _BacklogStorage.key.like("cpx_measure:%:raise"),
    ).all():
        try:
            state = json.loads(row.value or "{}")
        except Exception:
            continue
        if str(state.get("status") or "") != "waiting_measurement":
            continue
        try:
            iid = int(state.get("item_id") or str(row.key).split(":")[1])
            old_bid = float(state.get("old_bid_rub") or 0)
        except Exception:
            continue
        waiting.append({
            "row": row, "state": state, "item_id": iid,
            "old_bid_rub": old_bid,
            "source": str(state.get("source") or "unknown"),
            "started_at": str(state.get("started_at") or ""),
        })

    cap = max(1, int(cap or 1))
    if len(waiting) <= cap:
        return {
            "status": "within_cap", "before": len(waiting), "after": len(waiting),
            "cap": cap, "closed": 0,
            "kept_item_ids": [x["item_id"] for x in waiting],
        }

    ordered = sorted(
        waiting,
        key=lambda x: (0 if x["old_bid_rub"] > 0 else 1, x["started_at"], x["item_id"]),
    )
    keep = []
    if ordered:
        keep.append(ordered.pop(0))
    while ordered and len(keep) < cap:
        existing_sources = {x["source"] for x in keep}
        idx = next((i for i, x in enumerate(ordered) if x["source"] not in existing_sources), 0)
        keep.append(ordered.pop(idx))
    keep_ids = {x["item_id"] for x in keep}

    finished_at = _BacklogDt.now(_BacklogTz.utc).isoformat()
    closed = []
    for x in waiting:
        if x["item_id"] in keep_ids:
            continue
        state = dict(x["state"])
        state.update({
            "status": "superseded",
            "finished_at": finished_at,
            "effect": "inconclusive_backlog_compaction",
            "decision": "do_not_learn_from_overparallel_window",
            "scale_eligible": False,
            "reason": (
                "Историческая версия BORIS открыла больше двух параллельных "
                "денежных измерений. Этот замер закрыт без verdict и без изменения "
                "Avito; сохранены только два независимых измерения для чистой причинности."
            ),
            "backlog_compaction": {
                "policy_version": "ACCOUNT_RAISE_MEASUREMENT_BACKLOG_SELF_HEAL_V1",
                "cap": cap,
                "before": len(waiting),
                "kept_item_ids": sorted(keep_ids),
            },
        })
        x["row"].value = json.dumps(state, ensure_ascii=False)
        closed.append(x["item_id"])

    db.commit()
    return {
        "status": "compacted",
        "before": len(waiting),
        "after": len(keep_ids),
        "cap": cap,
        "closed": len(closed),
        "closed_item_ids": sorted(closed),
        "kept_item_ids": sorted(keep_ids),
        "policy_version": "ACCOUNT_RAISE_MEASUREMENT_BACKLOG_SELF_HEAL_V1",
    }


def _winner_replay_measurement_guard(measure_data, live_bid_rub=None):
    """Fail-closed causal fence for repeating an autonomous winner raise.

    The first raise has no previous measurement and may proceed through the
    normal money guards. Any later winner replay is allowed only after a
    completed positive measurement; once provider truth is available, the live
    bid must still equal the bid that produced that measurement.
    """
    # WINNER_REPLAY_CURRENT_TRUTH_FENCE_V1
    if not measure_data:
        return {"allowed": True, "reason_code": "first_raise_no_prior_measurement"}
    if not isinstance(measure_data, dict):
        return {"allowed": False, "reason_code": "winner_measurement_invalid"}
    status = str(measure_data.get("status") or "")
    if status == "waiting_measurement":
        return {"allowed": False, "reason_code": "winner_measurement_waiting"}
    if status != "measured":
        return {"allowed": False, "reason_code": "winner_measurement_not_eligible"}
    if str(measure_data.get("effect") or "") != "improved":
        return {"allowed": False, "reason_code": "winner_positive_feedback_required"}
    try:
        expected = float(measure_data.get("new_bid_rub"))
    except Exception:
        return {"allowed": False, "reason_code": "winner_expected_bid_missing"}
    if expected < 0:
        return {"allowed": False, "reason_code": "winner_expected_bid_invalid"}
    if live_bid_rub is not None:
        try:
            live = float(live_bid_rub)
        except Exception:
            return {"allowed": False, "reason_code": "winner_live_bid_invalid"}
        if abs(live - expected) >= 0.01:
            return {"allowed": False, "reason_code": "winner_current_bid_changed",
                    "expected_bid_rub": expected, "live_bid_rub": live}
    return {"allowed": True, "reason_code": "winner_positive_feedback_current"}



def _finalize_measure_state(
    db,
    account_id,
    item_id,
    action,
):
    """
    Проверяет текущий measure_state и, если накопилось
    достаточно полных дней, фиксирует результат измерения.

    Никаких изменений Avito здесь нет.
    """
    import json

    from app.models.storage import Storage

    key = _cpx_measure_key(item_id, action)

    row = (
        db.query(Storage)
        .filter(
            Storage.account_id == account_id,
            Storage.key == key,
        )
        .first()
    )

    if not row:
        return {
            "status": "no_measurement",
            "item_id": int(item_id),
            "action": action,
            "reason": "measure_state отсутствует",
        }

    try:
        state = json.loads(row.value)
    except Exception:
        return {
            "status": "error",
            "item_id": int(item_id),
            "action": action,
            "reason": "measure_state содержит некорректный JSON",
        }

    if state.get("status") != "waiting_measurement":
        return {
            "status": "already_finalized",
            "item_id": int(item_id),
            "action": action,
            "state": state,
        }

    started_at_raw = state.get("started_at")

    if not started_at_raw:
        return {
            "status": "error",
            "item_id": int(item_id),
            "action": action,
            "reason": "В measure_state отсутствует started_at",
        }

    try:
        from datetime import datetime

        started_at = datetime.fromisoformat(started_at_raw)
    except Exception:
        return {
            "status": "error",
            "item_id": int(item_id),
            "action": action,
            "reason": "Некорректный started_at",
        }

    result = _calculate_measure_effect(
        db=db,
        account_id=account_id,
        item_id=item_id,
        started_at=started_at,
        measure_state=state,
        measure_action=action,
    )

    if result.get("status") != "measured":
        return result

    # Сохраняем полный результат измерения.
    state["status"] = "measured"
    state["finished_at"] = datetime.now().isoformat()
    state["effect"] = result.get("effect")
    state["before"] = result.get("before")
    state["after"] = result.get("after")
    state["delta"] = result.get("delta")
    state["required_days"] = result.get("required_days", MEASURE_MIN_DAYS)
    # DAILY_BID_LEARNING_JOURNAL_V1: durable bounded account journal.
    # One record answers: what bid changed, what happened next, and whether to scale.
    journal = _load_json(db, account_id, "cpx_learning_journal") or []
    if not isinstance(journal, list):
        journal = []
    effect = str(state.get("effect") or "")
    decision = (
        "scale_next_bounded_step" if effect == "improved"
        else "hold_collect_leads" if effect == "reach_improved_no_lead_yet"
        else "rollback_or_lower" if effect == "worsened"
        else "stop_money_no_signal" if effect in {"inconclusive_no_signal", "inconclusive_no_baseline", "inconclusive_first_bid_no_signal"}
        else "hold"
    )
    journal.append({
        "finished_at": state.get("finished_at"),
        "account_id": account_id, "item_id": int(item_id), "action": action,
        "old_bid_rub": state.get("old_bid_rub"), "new_bid_rub": state.get("new_bid_rub"),
        "before": state.get("before"), "after": state.get("after"),
        "delta": state.get("delta"), "effect": effect, "decision": decision,
        "red_price_guard": "account_max_cpl_and_daily_budget_enforced_at_next_money_action",
        "learning_contract": "daily_bid_learning_v1",
    })
    _save_json(db, account_id, "cpx_learning_journal", journal[-500:])

    row.value = json.dumps(
        state,
        ensure_ascii=False,
    )

    db.commit()

    # DAILY_BID_RESULT_JOURNAL_V1: measurement is a first-class owner-visible
    # action result. One finalized measure_state produces one immutable Action Log
    # row, so the next daily decision can explain what the previous bid change did.
    try:
        from app.services.action_log import log_action, ACTOR_BORIS_AUTO
        _before = state.get("before") or {}
        _after = state.get("after") or {}
        log_action(
            account_id=account_id,
            action="Оценил результат изменения ставки",
            object_kind="объявление",
            object_name=str(item_id),
            before_val=json.dumps({
                "ставка": state.get("old_bid_rub"),
                "просмотры": _before.get("views"),
                "лиды": _before.get("contacts"),
                "конверсия": _before.get("conversion"),
            }, ensure_ascii=False),
            after_val=json.dumps({
                "ставка": state.get("new_bid_rub"),
                "просмотры": _after.get("views"),
                "лиды": _after.get("contacts"),
                "конверсия": _after.get("conversion"),
                "эффект": state.get("effect"),
            }, ensure_ascii=False),
            reason="Суточный feedback-loop: результат предыдущего изменения ставки измерен по завершённым дням",
            actor=ACTOR_BORIS_AUTO,
            source="cpx_daily_learning",
            trigger="daily_measurement_complete",
            request_id=f"cpx_measure_result:{account_id}:{item_id}:{action}:{started_at_raw}",
        )
    except Exception:
        pass

    return {
        "status": "measured",
        "item_id": int(item_id),
        "action": action,
        "effect": state["effect"],
        "before": state["before"],
        "after": state["after"],
        "delta": state["delta"],
        "measure_state": state,
    }


def negative_raise_rollback_candidates(db, account_id: str):
    """Latest harmful raises that still need a lower-only self-heal."""
    journal = _load_json(db, account_id, "cpx_learning_journal") or []
    if not isinstance(journal, list): return []
    latest = {}
    for event in reversed(journal):
        if not isinstance(event, dict) or str(event.get("action") or "") != "raise": continue
        iid = str(event.get("item_id") or "").strip()
        if iid and iid not in latest: latest[iid] = event
    out = []
    for iid, event in latest.items():
        if str(event.get("effect") or "") != "worsened": continue
        try: old_bid=float(event.get("old_bid_rub")); new_bid=float(event.get("new_bid_rub"))
        except Exception: continue
        if old_bid < 0 or new_bid <= old_bid: continue
        lower = next((x for x in reversed(journal) if isinstance(x, dict)
                      and str(x.get("action") or "") == "lower"
                      and str(x.get("item_id") or "") == iid), None)
        if lower and str(lower.get("finished_at") or "") >= str(event.get("finished_at") or ""): continue
        # Only the most recent harmful raise is actionable. Historical losses
        # from before a later raise are evidence, not pending compensation.
        # The current live provider bid is re-checked by apply_one; this helper
        # intentionally never guesses that old state is still active.
        # Current advisor truth is a pre-provider-I/O fence. Historical learning
        # may nominate only a listing that BORIS currently sees as canonical +
        # actively promoted with a positive bid. apply_one re-checks provider
        # truth again immediately before the write.
        advice = _load_json(db, account_id, "cpx_advice") or {}
        # NEGATIVE_ROLLBACK_ADVICE_TYPE_FAIL_CLOSED_V1: persisted advisor
        # state can be stale/legacy/corrupt. A lower-only self-heal must never
        # crash the hourly runner or act without canonical current truth.
        if not isinstance(advice, dict):
            continue
        recommendations = advice.get("recommendations") or {}
        if not isinstance(recommendations, dict):
            continue
        current = None
        for group in recommendations.values():
            if not isinstance(group, list): continue
            current = next((x for x in group if isinstance(x, dict)
                            and str(x.get("id") or x.get("item_id") or "") == iid), current)
        if not isinstance(current, dict): continue
        if str(current.get("write_capability") or "") not in {"canonical", "provider_exact_money"}: continue
        if current.get("promotion_active") is not True: continue
        try: live_bid = float(current.get("bid_rub") or 0)
        except Exception: live_bid = 0.0
        if live_bid <= 0: continue
        # NEGATIVE_ROLLBACK_ALREADY_COMPENSATED_V1: if another safe lane has
        # already returned the live bid to the pre-loss baseline or lower, the
        # harmful raise is economically compensated. Never lower it again just
        # because the historical measurement still says worsened.
        if live_bid <= old_bid:
            continue
        out.append({"item_id":int(iid),"old_bid_rub":old_bid,"bad_bid_rub":new_bid,
                    "live_bid_rub":live_bid,"finished_at":event.get("finished_at"),"effect":"worsened"})
    out.sort(key=lambda x: str(x.get("finished_at") or ""))
    return out


def _classify_negative_rollback_apply_result(result):
    """Classify one apply_one result without turning proven idempotency into failure."""
    result = result or {}
    status = str(result.get("status") or "error")
    changed = bool(status == "ok" and result.get("changed_avito") is not False)
    idempotent_success = bool(
        status == "ok"
        and result.get("changed_avito") is False
        and result.get("idempotent") is True
        and str(result.get("execution_status") or "") in {"succeeded", "reconciled"}
    )
    if changed:
        return "applied", status, True
    if idempotent_success:
        return "idempotent", status, False
    if status == "blocked":
        return "blocked", status, False
    if status == "skipped":
        return "skipped", status, False
    return "error", status, False


def run_negative_raise_rollback_cycle():
    """Run the loser self-heal from canonical Money-owned code."""
    # NEGATIVE_ROLLBACK_OWNED_CYCLE_V2: production must not depend on the
    # legacy unowned top-level runner. Candidate selection, provider mutation,
    # monotonic receipt verification and durable runtime evidence live here.
    import json as _json_neg
    from datetime import datetime as _dt_neg, timezone as _tz_neg
    from app.db.session import SessionLocal as _SL_neg
    from app.models.storage import Storage as _Storage_neg
    from app.services.control_plane_adapters_ext import marketing_service_entitlement as _ent_neg

    state_key = "negative_raise_rollback_runtime"

    def _save_runtime(account_id, payload):
        d = _SL_neg()
        try:
            raw = _json_neg.dumps(payload, ensure_ascii=False)
            row = (d.query(_Storage_neg)
                   .filter(_Storage_neg.account_id == account_id,
                           _Storage_neg.key == state_key)
                   .order_by(_Storage_neg.id.desc()).first())
            if row:
                row.value = raw
            else:
                d.add(_Storage_neg(account_id=account_id, key=state_key, value=raw))
            d.commit()
        finally:
            d.close()

    d = _SL_neg()
    try:
        accounts = [str(x[0]) for x in d.query(_Storage_neg.account_id)
                    .filter(_Storage_neg.key == "cpx_learning_journal")
                    .distinct().all()]
    finally:
        d.close()

    summary = {"checked_accounts": 0, "candidates": 0, "applied": 0,
               "blocked": 0, "skipped": 0, "errors": 0, "details": []}
    for account_id in accounts:
        d = _SL_neg()
        candidates = []
        candidate = None
        try:
            ent = _ent_neg(d, account_id) or {}
            if str(ent.get("state") or "").lower() != "active":
                continue
            candidates = negative_raise_rollback_candidates(d, account_id)
            summary["checked_accounts"] += 1
            summary["candidates"] += len(candidates)
            if not candidates:
                _save_runtime(account_id, {
                    "status": "ok", "candidate_count": 0,
                    "checked_at": _dt_neg.now(_tz_neg.utc).isoformat(),
                })
                continue
            candidates.sort(key=lambda x: str(x.get("finished_at") or ""), reverse=True)
            candidate = candidates[0]
        except Exception as exc:
            # NEGATIVE_ROLLBACK_TENANT_ISOLATION_V1: one corrupt tenant state
            # must not abort loser self-heal for every other active account.
            summary["errors"] += 1
            rec = {"account_id": account_id, "status": "error",
                   "reason": str(exc)[:220], "phase": "candidate_discovery"}
            summary["details"].append(rec)
            try:
                _save_runtime(account_id, {
                    "status": "error", "candidate_count": 0, "result": rec,
                    "checked_at": _dt_neg.now(_tz_neg.utc).isoformat(),
                })
            except Exception:
                pass
            continue
        finally:
            d.close()

        try:
            result = apply_one(ApplyOneBody(
                account_id=account_id, item_id=int(candidate["item_id"]),
                action="lower", max_bid_delta_pct=10,
                actor_type="boris_auto", source="negative_raise_rollback",
                trigger="worsened_raise_feedback",
                request_id=(f"neg-rollback:{account_id}:{candidate['item_id']}:"
                            f"{candidate.get('finished_at') or 'unknown'}"),
            )) or {}
            outcome, status, changed = _classify_negative_rollback_apply_result(result)
            # NEGATIVE_ROLLBACK_RUNTIME_MONOTONIC_VERIFY_V2: even after the
            # generic lower fence, count success only when the provider receipt
            # proves a strict decrease. Never send a compensating second write.
            if changed:
                try:
                    old_bid = float(result.get("old_bid_rub"))
                    new_bid = float(result.get("new_bid_rub"))
                except Exception:
                    old_bid = new_bid = None
                if old_bid is None or new_bid is None or new_bid >= old_bid:
                    outcome = "error"
                    status = "error"
                    changed = False
                    result = dict(result)
                    result["reason_code"] = "negative_rollback_non_monotonic_receipt"

            if outcome == "applied":
                summary["applied"] += 1
            elif outcome == "idempotent":
                # A succeeded/reconciled receipt is durable proof that this exact
                # rollback already happened. Re-observing it is healthy and must
                # never make the whole hourly marketer unit fail.
                summary["skipped"] += 1
            elif outcome == "blocked":
                summary["blocked"] += 1
            elif outcome == "skipped":
                summary["skipped"] += 1
            else:
                summary["errors"] += 1

            rec = {
                "account_id": account_id, "candidate": candidate,
                "status": status, "changed_avito": changed,
                "reason": (result.get("blocked_by") or result.get("reason_code")
                           or result.get("reason") or result.get("message")),
            }
            summary["details"].append(rec)
            _save_runtime(account_id, {
                "status": status, "candidate_count": len(candidates),
                "candidate": candidate, "result": rec,
                "checked_at": _dt_neg.now(_tz_neg.utc).isoformat(),
            })
        except Exception as exc:
            summary["errors"] += 1
            rec = {"account_id": account_id, "status": "error",
                   "reason": str(exc)[:220]}
            summary["details"].append(rec)
            try:
                _save_runtime(account_id, {
                    "status": "error", "candidate_count": len(candidates),
                    "candidate": candidate, "result": rec,
                    "checked_at": _dt_neg.now(_tz_neg.utc).isoformat(),
                })
            except Exception:
                pass
    return summary


def _calculate_measure_effect(
    db,
    account_id,
    item_id,
    started_at,
    measure_state=None,
    measure_action=None,
):
    """
    Сравнивает статистику до и после изменения ставки.

    Минимум MEASURE_MIN_DAYS полных дней после изменения.
    До изменения берём доступные полные дни непосредственно
    перед started_at.
    """
    from datetime import date, timedelta

    before = {
        "views": 0,
        "contacts": 0,
        "days": 0,
    }

    try:
        started_date = started_at.date()
    except Exception:
        return {
            "status": "error",
            "reason": "Некорректный started_at",
        }

    # -----------------------------
    # BEFORE
    # -----------------------------
    current = started_date - timedelta(days=1)

    for _ in range(7):
        snap = _load_json(
            db,
            account_id,
            f"daily_stats:{current.isoformat()}",
        )

        if snap:
            for item in snap.get("items") or []:
                if int(item.get("id") or 0) != int(item_id):
                    continue

                before["views"] += int(item.get("views") or 0)
                before["contacts"] += int(item.get("contacts") or 0)
                before["days"] += 1
                break

        current -= timedelta(days=1)

    # -----------------------------
    # AFTER
    # -----------------------------
    after = _collect_measure_period(
        db=db,
        account_id=account_id,
        item_id=item_id,
        started_at=started_at,
    )

    # FIRST_BID_NO_SIGNAL_SLOT_RELEASE_V1:
    # A bounded first activation (0 RUB -> positive bid) that receives zero
    # views for one complete post-change day has produced no reach evidence.
    # Keeping it in one of only two account measurement slots for seven days
    # blocks better hypotheses without improving causality. Finalize it as
    # inconclusive only: do not lower/disable the live bid and never call it a
    # winner or loser. Ordinary positive-bid experiments keep the longer
    # no-signal timeout below.
    _ms = measure_state if isinstance(measure_state, dict) else {}
    try:
        _first_bid_no_signal = bool(
            str(measure_action or _ms.get("action") or "") == "raise"
            and str(_ms.get("status") or "") == "waiting_measurement"
            and float(_ms.get("old_bid_rub") or 0) <= 0
            and float(_ms.get("new_bid_rub") or 0) > 0
            and int(after.get("days") or 0) >= MEASURE_MIN_DAYS
            and int(after.get("views") or 0) <= 0
        )
    except Exception:
        _first_bid_no_signal = False
    if _first_bid_no_signal:
        return {
            "status": "measured",
            "effect": "inconclusive_first_bid_no_signal",
            "before": before,
            "after": after,
            "delta": {"views_per_day_pct": None, "contacts_per_day_pct": None},
            "required_days": MEASURE_MIN_DAYS,
            "reason": (
                f"После первой платной ставки завершён {int(after.get('days') or 0)} полный день "
                "без просмотров; замер закрыт без winner/loser, ставка Avito не менялась."
            ),
        }

    # MEASURE_NO_SIGNAL_TIMEOUT_V1: a bid experiment that produced zero views
    # for a full week is not allowed to hold a permanent measurement lock. It is
    # finalized as an inconclusive paid hypothesis (never a winner), so BORIS can
    # stop retrying money forever; any content diagnosis belongs to WS-AVITO-CONTENT.
    # MEASURE_NO_SIGNAL_ELAPSED_TIMEOUT_V2: missing item rows cannot keep a no-signal experiment locked forever.
    try:
        started_date = started_at.date() if hasattr(started_at, 'date') else datetime.fromisoformat(str(started_at).replace('Z','+00:00')).date()
        today = datetime.now(timezone.utc).date()
        elapsed_complete_days = max(0, (today - started_date).days)
    except Exception:
        elapsed_complete_days = int(after.get("days") or 0)
    if elapsed_complete_days >= MEASURE_NO_SIGNAL_MAX_DAYS and int(after.get("views") or 0) <= 0:
        return {
            "status": "measured",
            "effect": "inconclusive_no_signal",
            "before": before,
            "after": after,
            "delta": {"views_per_day_pct": None, "contacts_per_day_pct": None},
            "required_days": MEASURE_MIN_DAYS,
            "reason": f"{elapsed_complete_days} полных календарных дней после изменения без просмотров; денежная гипотеза завершена без сигнала.",
        }

    # Нужны минимум MEASURE_MIN_DAYS полных дней: на слабом трафике за два дня
    # статистики не набирается и вердикт получается случайным.
    # DAILY_BID_LEARNING_BASELINE_GUARD_V1: a daily verdict is only causal
    # when at least one complete pre-change day exists. Never label a winner
    # from an empty historical baseline.
    if before["days"] < 1:
        # MEASURE_NO_BASELINE_TIMEOUT_V1: a pre-change baseline can never appear
        # retroactively after the experiment has already run for a full week.
        # Keep the result non-winning and free the money lock instead of waiting
        # forever on an impossible comparison. No provider mutation here.
        if elapsed_complete_days >= MEASURE_NO_SIGNAL_MAX_DAYS:
            return {
                "status": "measured",
                "effect": "inconclusive_no_baseline",
                "before": before, "after": after,
                "delta": {"views_per_day_pct": None, "contacts_per_day_pct": None},
                "required_days": MEASURE_MIN_DAYS,
                "reason": f"{elapsed_complete_days} полных календарных дней после изменения, но сопоставимый baseline до изменения отсутствует; денежная гипотеза завершена без права масштабирования.",
            }
        return {
            "status": "waiting_measurement",
            "before": before, "after": after,
            "required_days": MEASURE_MIN_DAYS,
            "reason": "Нет полного дня baseline до изменения ставки; BORIS ждёт сопоставимые данные.",
        }

    if after["days"] < MEASURE_MIN_DAYS:
        return {
            "status": "waiting_measurement",
            "before": before,
            "after": after,
            "required_days": MEASURE_MIN_DAYS,
            "reason": (
                f"После изменения завершено только "
                f"{after['days']} полных дн. "
                f"Для оценки нужны минимум {MEASURE_MIN_DAYS}."
            ),
        }

    # DAILY_BID_RESULT_EVIDENCE_GUARD_V1: zero post-change traffic is not
    # evidence of success/failure; keep measuring instead of poisoning learning.
    if int(after.get("views") or 0) <= 0:
        return {"status":"waiting_measurement", "item_id":int(item_id),
                "before":before, "after":after, "required_days":MEASURE_MIN_DAYS,
                "reason":"После изменения ещё нет просмотров; недостаточно доказательств для оценки эффекта."}

    before_views_day = (
        before["views"] / before["days"]
        if before["days"]
        else 0
    )

    before_contacts_day = (
        before["contacts"] / before["days"]
        if before["days"]
        else 0
    )

    after_views_day = (
        after["views"] / after["days"]
        if after["days"]
        else 0
    )

    after_contacts_day = (
        after["contacts"] / after["days"]
        if after["days"]
        else 0
    )

    contacts_delta_pct = (
        ((after_contacts_day - before_contacts_day)
         / before_contacts_day) * 100
        if before_contacts_day > 0
        else None
    )

    views_delta_pct = (
        ((after_views_day - before_views_day)
         / before_views_day) * 100
        if before_views_day > 0
        else None
    )

    before_conversion = (
        before["contacts"] / before["views"] * 100
        if before["views"]
        else None
    )

    after_conversion = (
        after["contacts"] / after["views"] * 100
        if after["views"]
        else None
    )

    # DAILY_BID_LEARNING_V1: verdict is primarily lead/CPL oriented.
    # With a zero-lead baseline, a new lead is a real positive signal instead
    # of the old permanent insufficient_baseline. Views are supporting evidence.
    if before_contacts_day > 0:
        contacts_delta_pct = ((after_contacts_day - before_contacts_day) / before_contacts_day) * 100
    elif after_contacts_day > 0:
        contacts_delta_pct = 100.0
    else:
        contacts_delta_pct = 0.0

    if after_contacts_day > before_contacts_day:
        effect = "improved"
    elif after_contacts_day < before_contacts_day:
        effect = "worsened"
    elif views_delta_pct is not None and views_delta_pct >= 15:
        effect = "reach_improved_no_lead_yet"
    elif views_delta_pct is not None and views_delta_pct <= -15:
        effect = "worsened"
    else:
        effect = "neutral"

    return {
        "status": "measured",
        "effect": effect,
        "before": {
            **before,
            "views_per_day": round(before_views_day, 2),
            "contacts_per_day": round(before_contacts_day, 2),
            "conversion": (
                round(before_conversion, 2)
                if before_conversion is not None
                else None
            ),
        },
        "after": {
            **after,
            "views_per_day": round(after_views_day, 2),
            "contacts_per_day": round(after_contacts_day, 2),
            "conversion": (
                round(after_conversion, 2)
                if after_conversion is not None
                else None
            ),
        },
        "delta": {
            "views_per_day_pct": (
                round(views_delta_pct, 2)
                if views_delta_pct is not None
                else None
            ),
            "contacts_per_day_pct": (
                round(contacts_delta_pct, 2)
                if contacts_delta_pct is not None
                else None
            ),
        },
        "required_days": MEASURE_MIN_DAYS,
    }


def _collect_measure_period(
    db,
    account_id,
    item_id,
    started_at,
):
    """
    Собирает статистику только за полные календарные дни,
    наступившие после начала измерения.

    Сегодня намеренно не включаем: день ещё неполный.
    """
    from datetime import date, timedelta

    out = {
        "views": 0,
        "contacts": 0,
        "days": 0,
    }

    try:
        started_date = started_at.date()
    except Exception:
        return out

    today = marketing_today()

    # День изменения и сегодняшний день не используем.
    # Берём только полностью завершившиеся дни после started_at.
    current = started_date + timedelta(days=1)

    while current < today:
        snap = _load_json(
            db,
            account_id,
            f"daily_stats:{current.isoformat()}",
        )

        if snap:
            for item in snap.get("items") or []:
                if int(item.get("id") or 0) != int(item_id):
                    continue

                out["views"] += int(item.get("views") or 0)
                out["contacts"] += int(item.get("contacts") or 0)
                out["days"] += 1
                break

        current += timedelta(days=1)

    if out["views"] > 0:
        out["conversion"] = round(
            out["contacts"] / out["views"] * 100,
            2,
        )
    else:
        out["conversion"] = None

    return out


def _save_measure_state(
    db,
    account_id,
    item_id,
    action,
    old_bid_rub,
    new_bid_rub,
    *,
    source=None,
    request_key=None,
    provider_receipt_proven=False,
):
    """
    Фиксирует, что по объявлению начался новый период измерения.

    Само изменение Avito здесь НЕ выполняется.
    """
    import json
    from datetime import datetime

    from app.models.storage import Storage

    key = _cpx_measure_key(item_id, action)

    _started_at = datetime.now().isoformat()
    payload = {
        "item_id": int(item_id),
        "action": action,
        "old_bid_rub": old_bid_rub,
        "new_bid_rub": new_bid_rub,
        "started_at": _started_at,
        "status": "waiting_measurement",
        "learning_contract": "daily_bid_learning_v1",
        "next_daily_review_after_complete_days": MEASURE_MIN_DAYS,
        "source": str(source or "") or None,
        "request_key": str(request_key or "") or None,
        "money_identity_proof": (
            "cpx_execution_receipt" if provider_receipt_proven else None
        ),
    }

    # CPX_MEASURE_CROSS_ACTION_SUPERSEDE_V1: only the latest live bid mutation
    # on an item is causally measurable. If a later lower/reset follows a raise
    # (or vice versa), the older waiting state must not remain a second lock.
    # This is DB-only lifecycle cleanup: no provider call and no money mutation.
    _prefix = f"cpx_measure:{int(item_id)}:"
    for _old in db.query(Storage).filter(
        Storage.account_id == account_id,
        Storage.key.like(_prefix + "%"),
    ).all():
        if _old.key == key:
            continue
        try:
            _old_state = json.loads(_old.value or "{}")
        except Exception:
            continue
        if _old_state.get("status") != "waiting_measurement":
            continue
        _old_state["status"] = "superseded"
        _old_state["finished_at"] = _started_at
        _old_state["effect"] = "superseded_by_later_bid_change"
        _old_state["decision"] = "do_not_learn_from_confounded_window"
        _old_state["scale_eligible"] = False
        _old_state["superseded_by"] = {
            "action": action, "old_bid_rub": old_bid_rub,
            "new_bid_rub": new_bid_rub, "started_at": _started_at,
        }
        _old.value = json.dumps(_old_state, ensure_ascii=False)

    row = (
        db.query(Storage)
        .filter(
            Storage.account_id == account_id,
            Storage.key == key,
        )
        .first()
    )

    value = json.dumps(
        payload,
        ensure_ascii=False,
    )

    if row:
        row.value = value
    else:
        db.add(
            Storage(
                account_id=account_id,
                key=key,
                value=value,
            )
        )

    db.commit()

    return payload


def _mark_applied(db, account_id, key):
    log = _load_json(db, account_id, "cpx_applied") or {}
    log[key] = __import__("datetime").datetime.now().isoformat()
    if len(log) > 500:                      # держим журнал компактным
        for old in sorted(log, key=lambda k: log[k])[:200]:
            log.pop(old, None)
    _save_json(db, account_id, "cpx_applied", log)


def reconcile_cpx_execution_receipts(limit: int = 20, min_age_seconds: int = 20) -> dict:
    """Resolve ambiguous live CPX writes from authoritative Avito state.

    This function is strictly read-only against Avito. It never retries a
    mutation. Exact observed state decides whether the prior request succeeded,
    failed without applying, or remains conflicting/unknown.
    """
    import httpx as _httpx
    from sqlalchemy import text as _t
    from app.db.session import SessionLocal as _SL
    from app.api.avito import get_avito_token
    from app.services.avito_account_throttle import account_throttle_remaining as _receipt_throttle_remaining, record_account_throttle as _receipt_record_throttle

    # CPX_RECEIPT_SHARED_ACCOUNT_THROTTLE_V1: reconciliation is read-only, but it
    # still consumes provider quota. Respect a tenant Retry-After before every
    # reconciliation read, and publish any 429 so sibling receipts/stages stop.
    def _receipt_record_429(account_id, resp):
        retry = None
        try:
            raw = str(resp.headers.get("Retry-After") or "").strip()
            if raw:
                try:
                    retry = max(0, int(float(raw)))
                except Exception:
                    from email.utils import parsedate_to_datetime as _parse_receipt_ra
                    from datetime import datetime as _receipt_dt, timezone as _receipt_tz
                    when = _parse_receipt_ra(raw)
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=_receipt_tz.utc)
                    retry = max(0, int((when.astimezone(_receipt_tz.utc) - _receipt_dt.now(_receipt_tz.utc)).total_seconds()))
        except Exception:
            retry = None
        return _receipt_record_throttle(account_id, retry or 30, source="cpx_receipt_429")

    d=_SL()
    try:
        rows=d.execute(_t("""SELECT id,account_id,request_key,item_id,action,old_bid_penny,intended_bid_penny,status,attempted_at
          FROM cpx_execution_receipts
          WHERE status IN ('attempting','delivery_unknown')
            AND COALESCE(attempted_at,created_at) < now() - (:age * interval '1 second')
          ORDER BY id ASC LIMIT :n"""),{"age":max(0,int(min_age_seconds)),"n":max(1,min(int(limit or 20),100))}).mappings().all()
        rows=[dict(x) for x in rows]
    finally:d.close()
    out=[]
    for row in rows:
        account_id=str(row['account_id']); key=str(row['request_key']); item_id=int(row['item_id']); action=str(row['action'])
        try:
            _receipt_retry = _receipt_throttle_remaining(account_id)
            if _receipt_retry > 0:
                out.append({'id':row['id'],'status':'waiting_throttle','retry_after_seconds':int(_receipt_retry)})
                continue
            tok_data=get_avito_token(account_id)
            tok=tok_data.get('access_token') if isinstance(tok_data,dict) else tok_data
            if not tok:
                out.append({'id':row['id'],'status':'waiting_token'}); continue
            headers={'Authorization':f'Bearer {tok}','Content-Type':'application/json'}
            if action in {'raise','lower'}:
                rr=_httpx.get(f'https://api.avito.ru/cpxpromo/1/getBids/{item_id}',headers=headers,timeout=20)
                if rr.status_code == 429:
                    _retry = _receipt_record_429(account_id, rr)
                    out.append({'id':row['id'],'status':'waiting_throttle','http_status':429,'retry_after_seconds':int(_retry)}); continue
                if rr.status_code != 200:
                    out.append({'id':row['id'],'status':'waiting_http','http_status':rr.status_code}); continue
                info=rr.json() or {}; manual=info.get('manual') or {}
                observed=int(manual.get('bidPenny') or 0)
                intended=row.get('intended_bid_penny'); old=row.get('old_bid_penny')
                if intended is not None and observed == int(intended):
                    _cpx_receipt_mark(account_id,key,'reconciled',http_status=200,observed_bid_penny=observed,result={'reconciliation':'intended_bid_observed','observed_bid_penny':observed})
                    out.append({'id':row['id'],'status':'reconciled','result':'applied'})
                elif old is not None and observed == int(old):
                    _cpx_receipt_mark(account_id,key,'failed',http_status=200,observed_bid_penny=observed,result={'reconciliation':'old_bid_still_observed','observed_bid_penny':observed},error='provider mutation not observed')
                    out.append({'id':row['id'],'status':'failed','result':'not_applied'})
                else:
                    _cpx_receipt_mark(account_id,key,'delivery_unknown',http_status=200,observed_bid_penny=observed,result={'reconciliation':'conflicting_bid_observed','observed_bid_penny':observed},error='observed bid matches neither old nor intended')
                    out.append({'id':row['id'],'status':'delivery_unknown','result':'conflict'})
            elif action == 'archive':
                rr=_httpx.post('https://api.avito.ru/cpxpromo/1/getPromotionsByItemIds',headers=headers,json={'itemIDs':[item_id]},timeout=20)
                if rr.status_code == 429:
                    _retry = _receipt_record_429(account_id, rr)
                    out.append({'id':row['id'],'status':'waiting_throttle','http_status':429,'retry_after_seconds':int(_retry)}); continue
                if rr.status_code != 200:
                    out.append({'id':row['id'],'status':'waiting_http','http_status':rr.status_code}); continue
                items=(rr.json() or {}).get('items') or []
                info=next((x for x in items if int(x.get('itemID') or 0)==item_id),{})
                promoted=bool(info.get('manualPromotion') or info.get('autoPromotion'))
                if not promoted:
                    _cpx_receipt_mark(account_id,key,'reconciled',http_status=200,result={'reconciliation':'promotion_absent'})
                    out.append({'id':row['id'],'status':'reconciled','result':'applied'})
                else:
                    _cpx_receipt_mark(account_id,key,'failed',http_status=200,result={'reconciliation':'promotion_still_present'},error='remove promotion not observed')
                    out.append({'id':row['id'],'status':'failed','result':'not_applied'})
            else:
                _cpx_receipt_mark(account_id,key,'failed',error='unsupported action for reconciliation')
                out.append({'id':row['id'],'status':'failed','result':'unsupported_action'})
        except Exception as exc:
            # Reconciliation itself is read-only. Transport trouble leaves the
            # original unknown state intact for a later read-only retry.
            out.append({'id':row['id'],'status':'waiting_transport','error':type(exc).__name__})
    return {'status':'ok','checked':len(rows),'results':out}


def cpx_reconciliation_loop(stop_event=None, interval_seconds: int = 30):
    import time as _time
    while True:
        if stop_event is not None and stop_event.wait(max(5,int(interval_seconds))):
            return
        if stop_event is None:
            _time.sleep(max(5,int(interval_seconds)))
        try:
            result=reconcile_cpx_execution_receipts(limit=20,min_age_seconds=20)
            if result.get('checked'):
                print(f"CPX_RECONCILE checked={result.get('checked')} results={result.get('results')}",flush=True)
        except Exception as exc:
            print(f"CPX_RECONCILE_ERROR {type(exc).__name__}",flush=True)


@router.post("/apply_one")
def apply_one(body: ApplyOneBody):
    from app.models.storage import Storage
    """Применить ОДНУ рекомендацию вручную (по кнопке). Меняет реальную ставку/статус.
    Проходит через автопилот-проверку и логируется."""
    import httpx as _httpx
    from app.db.session import SessionLocal
    from app.api.avito import get_avito_token, _audit_log, _autopilot_allows

    db = SessionLocal()
    try:
        # KPI — берём цель и суточный лимит (предохранители)
        kpi = _load_json(db, body.account_id, "kpi_settings") or {}
        max_cpl = kpi.get("max_cost_per_lead_rub", 0)

        # KPI_GLOBAL_AUTONOMOUS_RAISE_STOP_V1
        # Final business-level stop for autonomous growth lanes. Individual
        # runners may reason about views/position differently, but no autonomous
        # raise is allowed when today's KPI is already met, when CPL is already
        # above the owner's red line, or when zero-lead spend crossed the same
        # economic-emergency threshold used by the main KPI orchestrator.
        _actor_auto = str(body.actor_type or "").strip().lower() in {"boris_auto", "autopilot", "system"}
        _source = str(body.source or "").strip()
        # AUTONOMOUS_RAISE_FINAL_MODE_FENCE_V1: the last provider-write entry
        # point must independently honor BOTH owner switches. A stale mandate or
        # an unusual internal caller must never keep automatic spend alive after
        # bid_autopilot/autonomous mode is disabled. Manual owner actions and
        # lower-only safety actions remain untouched.
        if body.action == "raise" and _actor_auto:
            if not bool(kpi.get("bid_autopilot")):
                return {"status":"blocked","action":body.action,"item_id":body.item_id,
                        "reason_code":"blocked_bid_autopilot_disabled",
                        "reason":"Автоматическое повышение ставок выключено владельцем.",
                        "changed_avito":False}
            if not _autopilot_allows(body.account_id):
                return {"status":"blocked","action":body.action,"item_id":body.item_id,
                        "reason_code":"blocked_autonomous_mode_disabled",
                        "reason":"Автономный режим маркетолога выключен владельцем.",
                        "changed_avito":False}
        # LATE_DAY_SPEND_RESET_V1: reset is not a new optimization hypothesis.
        # It is the exact compensation for a prior BORIS-owned temporary boost.
        _late_day_reset = bool(body.action == "lower" and _actor_auto and _source == "late_day_spend_reset")
        _late_day_reset_baseline = None
        if _late_day_reset:
            try:
                from zoneinfo import ZoneInfo as _ZoneInfo_reset
                _boost_state = _load_json(db, body.account_id, "late_day_spend_boost_state") or {}
                _boost_items = _boost_state.get("items") if isinstance(_boost_state, dict) else {}
                _boost_item = (_boost_items or {}).get(str(body.item_id)) if isinstance(_boost_items, dict) else None
                _state_day = str((_boost_state or {}).get("day_msk") or "")
                _today_msk = datetime.now(_ZoneInfo_reset("Europe/Moscow")).date().isoformat()
                _late_day_reset_baseline = float((_boost_item or {}).get("baseline_bid_rub")) if isinstance(_boost_item, dict) else None
                _requested_reset = float(body.target_bid_rub) if body.target_bid_rub is not None else None
                if (not _state_day or _state_day >= _today_msk or _late_day_reset_baseline is None
                        or _requested_reset is None or abs(_requested_reset - _late_day_reset_baseline) > 0.011):
                    return {"status":"blocked","action":body.action,"item_id":body.item_id,
                            "blocked_by":"late_day_reset_baseline_unverified",
                            "reason":"Ночной откат разрешён только к точной ставке, сохранённой BORIS до вечернего форсажа.",
                            "changed_avito":False}
            except Exception as _reset_exc:
                return {"status":"blocked","action":body.action,"item_id":body.item_id,
                        "blocked_by":"late_day_reset_state_unavailable","reason":str(_reset_exc)[:180],
                        "changed_avito":False}
        _growth_raise = body.action == "raise" and _actor_auto and _source not in {"feed20", "new_feed_hourly_ramp"}
        if _growth_raise:
            _today_stats = _load_json(db, body.account_id, f"daily_stats:{marketing_today_iso()}") or {}
            _items_today = _today_stats.get("items") if isinstance(_today_stats, dict) else []
            _items_today = _items_today if isinstance(_items_today, list) else []
            # CPX_FINAL_RAISE_CURRENT_DAY_KPI_ONLY_V1:
            # Lagged prior-day contacts are historical evidence only.
            _today_item_signal_current = (
                str(_today_stats.get("stats_date") or "") == marketing_today_iso()
            )
            _contacts_today = (
                sum(int((x or {}).get("contacts") or 0) for x in _items_today if isinstance(x, dict))
                if _today_item_signal_current else 0
            )
            try:
                _target_today = float(kpi.get("target_leads_per_day") or 0)
            except Exception:
                _target_today = 0.0
            try:
                _max_cpl_today = float(max_cpl or 0)
            except Exception:
                _max_cpl_today = 0.0
            _sp = _today_stats.get("spending") if isinstance(_today_stats, dict) else {}
            try:
                _spent_today = float((_sp or {}).get("all_spend_rub") or 0) if isinstance(_sp, dict) else 0.0
            except Exception:
                _spent_today = 0.0
            _actual_cpl_today = (_spent_today / _contacts_today) if _contacts_today > 0 else None
            if (_target_today > 0 and _contacts_today >= _target_today
                    and _source != "profitable_day_push"):
                return {"status":"blocked","action":body.action,"item_id":body.item_id,
                        "blocked_by":"daily_kpi_already_met",
                        "reason":f"Дневной KPI уже выполнен: {_contacts_today}/{_target_today:g}. Обычный разгон остановлен; прибыльный дневной дожим работает отдельным guarded-контуром.",
                        "changed_avito":False}
            if _actual_cpl_today is not None and _max_cpl_today > 0 and _actual_cpl_today > _max_cpl_today:
                return {"status":"blocked","action":body.action,"item_id":body.item_id,
                        "blocked_by":"daily_cpl_above_limit",
                        "reason":f"Фактический CPL {_actual_cpl_today:.2f} ₽ выше лимита {_max_cpl_today:.2f} ₽; повышение ставки запрещено.",
                        "changed_avito":False}
            if (_contacts_today <= 0 and _max_cpl_today > 0
                    and _spent_today >= 1.10 * _max_cpl_today):
                return {"status":"blocked","action":body.action,"item_id":body.item_id,
                        "blocked_by":"zero_lead_economic_emergency",
                        "reason":f"0 лидов при расходе {_spent_today:.2f} ₽; аварийный порог по CPL уже достигнут. Новый разгон запрещён.",
                        "changed_avito":False}

        # OWNER_MANUAL_BID_OVERRIDE_V1
        # Явно выставленную владельцем ставку BORIS не перетирает автономно.
        # Ручной пользовательский вызов остаётся разрешённым и может заменить
        # прежнее решение владельца осознанным новым решением.
        _owner_override = _owner_manual_bid_override(db, body.account_id, body.item_id)
        if _owner_override and str(body.actor_type or "").lower() not in {"user", "owner"}:
            return {
                "status": "blocked",
                "action": body.action,
                "item_id": body.item_id,
                "reason": "Ставка вручную зафиксирована владельцем; автономный CPX её не изменяет.",
                "blocked_by": "owner_manual_bid_override",
                "owner_bid_rub": _owner_override.get("bid_rub"),
                "changed_avito": False,
            }

        # AI_MARKETING_OBSERVATION_ISOLATION_V1
        # Пока опубликованная AI Marketing версия проходит causal observation,
        # CPX не должен менять ставку этого же объявления: иначе KPI_AFTER будет
        # измерять одновременно title-change и bid-change. Блокируем только
        # точный account+item с active published/waiting_observation_window.
        _ops = _load_json(db, body.account_id, "kpi_apply_log") or []
        if isinstance(_ops, list):
            for _op_obs in _ops:
                if not isinstance(_op_obs, dict):
                    continue
                _obs_item = str(_op_obs.get("avito_item_id") or _op_obs.get("item_id") or "")
                _obs_effect = _op_obs.get("effect") or {}
                # AI_MARKETING_OBSERVATION_ISOLATION_V2: isolation starts as
                # soon as a content mutation is queued for external publication,
                # not only after Avito confirmation. A bid mutation during
                # waiting_avito_confirmation would already destroy the clean
                # baseline for the upcoming title experiment.
                _obs_status = str(_op_obs.get("status") or "")
                _obs_effect_status = str(_obs_effect.get("status") or "") if isinstance(_obs_effect, dict) else ""
                _obs_active = (
                    (_obs_status == "published" and _obs_effect_status == "waiting_observation_window")
                    or (_obs_status == "publish_requested" and _obs_effect_status == "waiting_avito_confirmation")
                )
                if (_obs_item == str(body.item_id) and _obs_active):
                    return {
                        "status": "blocked",
                        "action": body.action,
                        "item_id": body.item_id,
                        "reason": "AI Marketing observation window активен; CPX изменение ставки отложено до KPI_AFTER.",
                        "blocked_by": "ai_marketing_observation_window",
                        "changed_avito": False,
                    }

        # Одно действие на объявление в рамках ОДНОЙ версии рекомендаций.
        advice_now = _load_json(db, body.account_id, "cpx_advice") or {}
        gen_at = advice_now.get("generated_at") or ""
        applied_log = _load_json(db, body.account_id, "cpx_applied") or {}
        akey = _applied_key(body.item_id, body.action, gen_at)
        request_key = _cpx_request_key(body, gen_at)
        # CPX_RECEIPT_SESSION_ISOLATION_V1
        # _cpx_receipt_get owns a separate short DB session. Release the
        # preflight transaction first; otherwise the deliberately small daemon
        # pool (1+1) can deadlock when receipt lookup needs the second slot while
        # another runtime component briefly owns the overflow connection.
        db.close()
        # REACH_RESCUE_SHORT_CYCLE_V1: the launch/reach-rescue lanes may revisit
        # an item hourly. Persistent account-low-view growth is different: owner policy
        # requires one money hypothesis, then a daily evidence review before another step.
        _source_name = str(body.source or "")
        _late_day_catchup = _source_name == "late_day_spend_catchup"
        _late_day_reset_lane = _source_name == "late_day_spend_reset"
        _hourly_short_cycle_source = _source_name in {"new_feed_hourly_ramp", "feed20", "late_day_spend_catchup", "late_day_spend_reset"}  # REACH_RESCUE_DAILY_MEASURE_V3
        # LATE_DAY_SPEND_SHORT_ALIAS_WINDOW_V1: the temporary catch-up lane may
        # need 3-5 staged <=10% steps before Moscow midnight. Keep a 15-minute
        # provider-alias fence (enough to stop immediate duplicate retries) while
        # ordinary short-cycle lanes retain the 1-hour fence.
        _alias_window_days = (1.0 if _source_name == "account_low_views_ramp"
                              else ((15.0 / 1440.0) if _source_name in {"late_day_spend_catchup", "late_day_spend_reset"}
                                    else ((1.0 / 24.0) if _hourly_short_cycle_source else MEASURE_COOLDOWN_DAYS)))
        alias_receipt = _provider_alias_recent_receipt(body.account_id, body.item_id, body.action, _alias_window_days)
        if alias_receipt:
            return {
                "status": "blocked",
                "action": body.action,
                "item_id": body.item_id,
                "changed_avito": False,
                "blocked_by": "provider_alias_money_guard",
                "reason": "Другой BORIS-alias этого же Avito-аккаунта уже выполнял или ещё подтверждает такое денежное действие. Повтор заблокирован до окончания общего окна измерения.",
                "provider_uid": alias_receipt.get("provider_uid"),
                "conflict_account_id": alias_receipt.get("account_id"),
                "conflict_status": alias_receipt.get("status"),
                "conflict_request_key": alias_receipt.get("request_key"),
            }
        prior_receipt = _cpx_receipt_get(body.account_id, request_key)
        if prior_receipt:
            _pst = str(prior_receipt.get("status") or "")
            if _pst in {"succeeded", "reconciled"}:
                return {"status":"ok","action":body.action,"item_id":body.item_id,"changed_avito":False,
                        "idempotent":True,"execution_status":_pst,"request_key":request_key}
            if _pst in {"prepared", "attempting", "delivery_unknown"}:
                return {"status":"blocked","action":body.action,"item_id":body.item_id,"changed_avito":False,
                        "blocked_by":"cpx_execution_reconciliation","execution_status":_pst,"request_key":request_key,
                        "reason":"Предыдущее денежное действие с этим request_id ещё не доказано. BORIS не повторяет его вслепую."}
        if gen_at and akey in applied_log:
            return {"status": "blocked", "item_id": body.item_id,
                    "action": body.action,
                    "applied_at": applied_log[akey],
                    "message": "Это действие уже применено к текущей рекомендации. "
                               "Повтор станет доступен после следующего расчёта советника."}

        # ---------------------------------------------------------
        # MEASURE LOCK
        #
        # Обычный CPX-контур ждёт 7-дневный measurement. Для новых наших
        # feed_factory объявлений действует отдельный hourly-ramp policy:
        # первые 72 часа реакция измеряется каждый час и при низком трафике
        # допускается следующий +10% шаг. Денежные guards ниже НЕ обходятся.
        # ---------------------------------------------------------
        _hourly_feed_ramp = str(body.source or "") == "new_feed_hourly_ramp"
        _new_feed_first_bid = (str(body.source or "") == "feed20" and str(body.trigger or "") == "feed")
        _reach_rescue = str(body.source or "") == "reach_rescue"
        _profitable_day_push = str(body.source or "") == "profitable_day_push"
        # ACCOUNT_LOW_VIEWS_DAILY_MEASURE_V1: persistent low-view rescue is
        # a daily experiment, not an hourly spend loop. It uses the normal
        # one-full-day measurement lock and all money/balance/provider guards.
        _account_low_views_ramp = str(body.source or "") == "account_low_views_ramp"
        # MONEY_SAFE_PORTFOLIO_V2: KPI pressure no longer bypasses the
        # measurement lock. A cheap account-level CPL is not proof that another
        # +step on the same listing is profitable. Only the explicit new-feed
        # ramp may use the short measurement mode.
        _aggressive_kpi_push = False
        # First launch bid has no previous CPX mutation to measure. The exact
        # feed20 scope is selected only from currently unpromoted canonical items.
        # PROFITABLE_DAY_DAILY_MEASURE_V1: cheap CPL is account-level evidence,
        # not permission to stack hourly raises on one listing. profitable_day_push
        # uses the normal daily measurement lock/cooldown; only launch/reach lanes
        # retain their dedicated short-cycle semantics.
        # PROFITABLE_DAY_WINNER_DAILY_LANE_V2: this lane already has its own
        # one-step-per-item/day durable journal. It may rotate past a different
        # waiting item, but negative measured feedback still remains binding.
        _profitable_day_lane = (_source_name == "profitable_day_push")
        # WINNER_REPLAY_SOURCE_FENCE_V1: both legacy KPI winner scaling and the
        # profitable-day lane converge on this money boundary. The first raise
        # may establish a hypothesis; every replay requires positive measured
        # feedback and later an exact live-bid match to that measured winner.
        _winner_replay_lane = (body.action == "raise" and _source_name in {"boris_kpi_autopilot", "profitable_day_push"})
        # PROFITABLE_DAY_NORMAL_MEASUREMENT_V3: a cheap lead is useful evidence,
        # but it does not authorize bypassing the normal per-item measurement
        # lock. Winner scaling must remain causal: one step, observe, then decide.
        # NEW_FEED_ONE_STEP_THEN_MEASURE_V5: low hourly reach is a signal to
        # test one bounded step, not permission to stack +10% every hour. The
        # first activation has no prior hypothesis; all later feed raises use
        # the normal causal measurement lock before another money mutation.
        _fast_measure_mode = (_new_feed_first_bid or _late_day_reset_lane)
        measure_key = _cpx_measure_key(
            body.item_id,
            body.action,
        )

        measure_row = (
            db.query(Storage)
            .filter(
                Storage.account_id == body.account_id,
                Storage.key == measure_key,
            )
            .first()
        )

        measure_data = {}
        if measure_row:
            try:
                measure_data = json.loads(measure_row.value)
            except Exception:
                measure_data = {"status": "invalid"}

            if _winner_replay_lane:
                _winner_gate = _winner_replay_measurement_guard(measure_data)
                if not _winner_gate.get("allowed"):
                    return {
                        "status": "blocked", "action": body.action,
                        "item_id": body.item_id, "changed_avito": False,
                        "blocked_by": _winner_gate.get("reason_code") or "winner_measurement_not_eligible",
                        "reason_code": _winner_gate.get("reason_code") or "winner_measurement_not_eligible",
                        "measure_state": measure_data,
                    }

            # Замкнутый feedback-loop для автономного режима: если прошлый
            # такой же манёвр доказанно ухудшил результат, BORIS_AUTO не
            # повторяет его по кругу. Ручное решение владельца остаётся
            # возможным после обычного cooldown. Другая action (например lower)
            # имеет отдельный measure key и не блокируется этим verdict.
            # ACCOUNT_LOW_VIEWS_NEGATIVE_FEEDBACK_GUARD_V1: hourly rescue may
            # shorten the waiting window, but it must never repeat a raise that
            # was already measured as harmful on this exact listing.
            if ((not _fast_measure_mode or _reach_rescue or _account_low_views_ramp or _profitable_day_lane or _late_day_catchup)
                    and measure_data.get("status") == "measured"
                    and measure_data.get("effect") == "worsened"
                    and str(body.actor_type or "") in {"boris_auto", "autopilot"}):
                return {
                    "status": "blocked",
                    "action": body.action,
                    "item_id": body.item_id,
                    "reason": "Предыдущий такой манёвр ухудшил результат; BORIS автоматически его не повторяет.",
                    "blocked_by": "negative_measurement_feedback",
                    "measure_state": measure_data,
                    "changed_avito": False,
                }

            # Пауза после завершённого замера: без неё объявление освобождалось
            # сразу и могло получать +20% каждые несколько дней подряд.
            if not _fast_measure_mode and measure_data.get("status") == "measured":
                _fin = measure_data.get("finished_at")
                if _fin:
                    try:
                        from datetime import datetime as _dtc, timedelta as _tdc
                        if _dtc.fromisoformat(_fin) + _tdc(days=MEASURE_COOLDOWN_DAYS) > _dtc.now():
                            return {
                                "status": "blocked",
                                "action": body.action,
                                "item_id": body.item_id,
                                "reason": (
                                    "Замер прошлого изменения завершён недавно. "
                                    "Даём объявлению отработать перед следующей правкой."
                                ),
                                "blocked_by": "measure_cooldown",
                                "measure_state": measure_data,
                                "changed_avito": False,
                            }
                    except Exception:
                        pass
            if not _fast_measure_mode and measure_data.get("status") == "waiting_measurement":
                return {
                    "status": "blocked",
                    "action": body.action,
                    "item_id": body.item_id,
                    "reason": (
                        "По объявлению уже идёт измерение эффекта "
                        "предыдущего изменения ставки. "
                        "Сначала нужно завершить период измерения."
                    ),
                    "blocked_by": "measure_effect",
                    "measure_state": measure_data,
                    "changed_avito": False,
                }

        # ACCOUNT_RAISE_MEASUREMENT_FINAL_GUARD_V1:
        # Every autonomous raise lane shares one causal backlog ceiling. This is
        # rechecked at the final DB boundary to close races between feed20,
        # lowviews, staged rollout and late-day workers. Lower/reset safety lanes
        # are intentionally unaffected.
        if body.action == "raise" and _actor_auto:
            _raise_backlog = _active_raise_measurement_summary(db, body.account_id)
            if int(_raise_backlog.get("active_count") or 0) >= MAX_ACTIVE_RAISE_MEASUREMENTS_PER_ACCOUNT:
                return {
                    "status": "blocked",
                    "action": body.action,
                    "item_id": body.item_id,
                    "reason_code": "account_measurement_backlog_wait",
                    "blocked_by": "account_measurement_backlog_wait",
                    "waiting_measurements": int(_raise_backlog.get("active_count") or 0),
                    "measurement_cap": MAX_ACTIVE_RAISE_MEASUREMENTS_PER_ACCOUNT,
                    "next_action": "measurement_sweep_then_retry_automatically",
                    "changed_avito": False,
                }

        # NATIVE_PROVIDER_PROBE_FIRST_BID_V1: decide the only allowed native
        # zero-bid lane before provider I/O. This is advisory/receipt evidence
        # only; exact getBids and all authoritative money guards still follow.
        _native_first_bid_preflight = _native_provider_probe_first_bid_preflight(db, body)
        if _native_first_bid_preflight.get("blocked"):
            return {"status": "blocked", "action": body.action, "item_id": body.item_id,
                    "reason_code": _native_first_bid_preflight.get("reason_code"),
                    "blocked_by": _native_first_bid_preflight.get("reason_code"),
                    "native_first_bid": _native_first_bid_preflight,
                    "changed_avito": False}

        # Preflight policy/measurement state is fully materialized. Avito token
        # refresh + getBids are external I/O; never keep this read transaction
        # open while waiting on the provider. SQLAlchemy can lazily reconnect
        # below for the short autonomy/mutation phase.
        db.close()
        from app.services.avito_account_throttle import account_throttle_remaining, record_account_throttle
        # CPX_SHARED_ACCOUNT_THROTTLE_PRECHECK_V1: a Retry-After observed by any
        # trusted stage is binding for this tenant before CPX provider I/O.
        _shared_retry = account_throttle_remaining(body.account_id)
        if _shared_retry > 0:
            return {"status": "blocked", "code": 429,
                    "reason_code": "avito_account_throttled",
                    "blocked_by": "shared_avito_account_throttle",
                    "retry_after_seconds": int(_shared_retry),
                    "message": "Avito временно ограничил запросы по аккаунту; повтор после Retry-After",
                    "action": body.action, "item_id": body.item_id,
                    "changed_avito": False}
        tok_data = get_avito_token(body.account_id)
        tok = tok_data.get("access_token") if isinstance(tok_data, dict) else tok_data
        if not tok:
            return {"status": "error", "message": "Нет токена Avito"}

        # CPX_FINAL_PROVIDER_ITEM_PROOF_V1: this exact account-scoped provider
        # read is the final identity proof for bid-only operations, including
        # native Avito items without CampaignItem/content mapping. HTTP 200 is
        # required before setManual can ever be reached.
        r = _httpx.get(f"https://api.avito.ru/cpxpromo/1/getBids/{body.item_id}",
                       headers={"Authorization": f"Bearer {tok}"}, timeout=20)
        if r.status_code == 429:
            # CPX_GETBIDS_RETRY_AFTER_FAIL_CLOSED_V1: a confirmed Avito throttle
            # is an account-level stop signal for this money cycle. Do not sleep
            # and hammer getBids again from caller loops; surface Retry-After as
            # evidence and let the next trusted systemd cycle retry naturally.
            _retry_after_seconds = None
            try:
                _raw_ra = str(r.headers.get("Retry-After") or "").strip()
                if _raw_ra:
                    try:
                        _retry_after_seconds = max(0, int(float(_raw_ra)))
                    except Exception:
                        from email.utils import parsedate_to_datetime as _parse_ra_dt
                        from datetime import datetime as _ra_dt, timezone as _ra_tz
                        _ra_when = _parse_ra_dt(_raw_ra)
                        if _ra_when.tzinfo is None:
                            _ra_when = _ra_when.replace(tzinfo=_ra_tz.utc)
                        _retry_after_seconds = max(0, int((_ra_when.astimezone(_ra_tz.utc) - _ra_dt.now(_ra_tz.utc)).total_seconds()))
            except Exception:
                _retry_after_seconds = None
            _retry_after_seconds = record_account_throttle(
                body.account_id, _retry_after_seconds or 30, source="cpx_getbids_429"
            )
            return {"status": "blocked", "code": 429,
                    "reason_code": "avito_account_throttled",
                    "blocked_by": "avito_retry_after",
                    "retry_after_seconds": _retry_after_seconds,
                    "message": "Avito временно ограничил CPX-запросы; дальнейшее давление в этом цикле остановлено",
                    "action": body.action, "item_id": body.item_id,
                    "changed_avito": False}
        if r.status_code == 403:
            # CPX_ITEM_CAPABILITY_COOLDOWN_V1: item-level 403 is not an account
            # outage. Remember it for 24h so autonomous planning can try another
            # item instead of hammering the same unavailable listing every hour.
            try:
                from datetime import datetime as _dt_cap, timezone as _tz_cap
                _cd = _load_json(db, body.account_id, "cpx_item_capability_cooldown") or {}
                _items = _cd.get("items") if isinstance(_cd, dict) else {}
                _items = _items if isinstance(_items, dict) else {}
                _items[str(int(body.item_id))] = _dt_cap.now(_tz_cap.utc).isoformat()
                _save_json(db, body.account_id, "cpx_item_capability_cooldown", {
                    "items": _items, "ttl_hours": 24,
                    "updated_at": _dt_cap.now(_tz_cap.utc).isoformat(),
                })
            except Exception:
                pass
            return {"status": "blocked", "code": 403,
                    "reason_code": "cpx_promotion_unavailable",
                    "blocked_by": "avito_cpx_item_capability",
                    "message": r.text[:200], "action": body.action,
                    "item_id": body.item_id, "changed_avito": False}
        if r.status_code != 200:
            return {"status": "error", "code": r.status_code, "message": r.text[:200]}
        try:
            info = r.json()
        except Exception:
            info = {}
        action_type = info.get("actionTypeID", 5)
        manual = info.get("manual") or {}
        cur_bid = manual.get("bidPenny") or 0
        rec_bid = manual.get("recBidPenny") or 0
        max_bid = manual.get("maxBidPenny") or 0
        min_bid = manual.get("minBidPenny") or DEFAULT_MIN_BID_RUB * 100

        if _winner_replay_lane and measure_row:
            _winner_live_gate = _winner_replay_measurement_guard(
                measure_data, live_bid_rub=float(cur_bid) / 100.0
            )
            if not _winner_live_gate.get("allowed"):
                return {
                    "status": "blocked", "action": body.action,
                    "item_id": body.item_id, "changed_avito": False,
                    "blocked_by": _winner_live_gate.get("reason_code") or "winner_current_truth_invalid",
                    "reason_code": _winner_live_gate.get("reason_code") or "winner_current_truth_invalid",
                    "expected_bid_rub": _winner_live_gate.get("expected_bid_rub"),
                    "live_bid_rub": _winner_live_gate.get("live_bid_rub"),
                    "measure_state": measure_data,
                }

        # NEW_ITEM_NO_PROMO_MIN_PLUS_20_V1
        # На новом объявлении без активного ручного продвижения Avito обычно
        # отдаёт минимальную цену просмотра. Первый raise должен стартовать
        # сразу с minBid +20%, а не с голого минимума. Это остаётся внутри
        # существующих money/autonomy guards и никогда не обходит maxBid.
        no_manual_promotion = not bool(manual.get("bidPenny"))
        _native_provider_probe_first_bid = bool(
            no_manual_promotion and _native_first_bid_preflight.get("eligible")
        )
        new_item_floor_bid = None
        if body.action == "raise" and no_manual_promotion and min_bid:
            import math as _math_new_item_bid
            # AVITO_EFFECTIVE_MIN_BID_V1: minBidPenny may describe a technical
            # floor that setManual rejects as "Ставка слишком маленькая". Use
            # the first forecast ladder value with positive compare as the
            # minimum bid that can actually buy reach; fall back to minBid.
            effective_min_bid = int(min_bid)
            try:
                ladder = manual.get("bids") or []
                effective = [int(x.get("valuePenny")) for x in ladder
                             if int(x.get("valuePenny") or 0) > 0 and float(x.get("compare") or 0) > 0]
                if effective:
                    effective_min_bid = max(int(min_bid), min(effective))
            except Exception:
                effective_min_bid = int(min_bid)
            if _native_provider_probe_first_bid:
                # Native first activation buys the minimum provider-proven reach
                # only. Round UP to the next whole ruble because setManual rejects
                # fractional-ruble bids; never add an arbitrary launch markup.
                new_item_floor_bid = int(_math_new_item_bid.ceil(
                    float(effective_min_bid) / 100.0
                ) * 100)
            elif str(body.source or "") == "reach_rescue":
                new_item_floor_bid = effective_min_bid
            else:
                new_item_floor_bid = max(effective_min_bid, int(_math_new_item_bid.ceil(
                    float(min_bid) * (100 + NEW_ITEM_NO_PROMO_MIN_BID_MARKUP_PCT) / 100.0
                )))

        if body.action == "archive":
            from app.services.autonomy import can_execute_live_action, log_guard_block
            _ctx = {"actor": body.actor_type or "", "trigger": body.trigger or "",
                    "source": body.source or "cpx_advisor.apply_one",
                    "request_id": body.request_id or ""}
            _g = can_execute_live_action(db, body.account_id, "cpx.remove_promotion",
                                         _ctx, bid_context={"item_id": body.item_id},
                                         balance={"status": "UNKNOWN", "value": None})
            if not _g["allowed"]:
                log_guard_block(db, body.account_id, "cpx.remove_promotion", "blocked",
                                _g["reason_code"], _ctx, object_id=body.item_id,
                                mandate={"id": _g.get("mandate_id"),
                                         "version": _g.get("mandate_version")},
                                balance_status="UNKNOWN",
                                limits=_g.get("limits_applied"), evidence=_g.get("evidence"))
                return {"status": "blocked", "reason_code": _g["reason_code"],
                        "action": body.action, "item_id": body.item_id,
                        "changed_avito": False}
            # Guard audit is durable before the external mutation; do not hold
            # its transaction while Avito processes the remove request.
            db.commit()
            db.close()
            receipt, created = _cpx_receipt_prepare(body.account_id, request_key, body.item_id, body.action, body.source, cur_bid, None)
            if not created:
                if str((receipt or {}).get("status") or "") == "capacity_blocked":
                    return {"status":"blocked","action":body.action,"item_id":body.item_id,
                            "changed_avito":False,
                            "blocked_by":(receipt or {}).get("reason_code") or "blocked_money_capacity",
                            "reason_code":(receipt or {}).get("reason_code") or "blocked_money_capacity",
                            "request_key":request_key}
                return {"status":"blocked","action":body.action,"item_id":body.item_id,"changed_avito":False,"blocked_by":"cpx_execution_reconciliation","execution_status":receipt.get("status"),"request_key":request_key}
            _cpx_receipt_mark(body.account_id, request_key, "attempting")
            # CPX_ARCHIVE_FINAL_SHARED_THROTTLE_RECHECK_V1: archive lowers spend,
            # but still consumes provider quota. A sibling 429 between getBids and
            # remove must stop this I/O before it starts.
            _archive_retry = account_throttle_remaining(body.account_id)
            if _archive_retry > 0:
                _cpx_receipt_mark(body.account_id, request_key, "failed", error="avito_account_throttled")
                return {"status":"blocked","code":429,"reason_code":"avito_account_throttled",
                        "retry_after_seconds":int(_archive_retry),"request_key":request_key,"changed_avito":False}
            # снять продвижение (в архив продвижения — вернуть на прайс-лист)
            try:
                rr = _httpx.post("https://api.avito.ru/cpxpromo/1/remove",
                                 headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                                 json={"itemID": body.item_id}, timeout=20)
            except Exception as exc:
                _cpx_receipt_mark(body.account_id, request_key, "delivery_unknown", error=repr(exc))
                return {"status":"blocked","code":"delivery_unknown","message":"Avito transport outcome unknown; blind retry forbidden","request_key":request_key,"changed_avito":False}
            if rr.status_code == 429:
                _archive_retry = None
                try:
                    _raw = str(rr.headers.get("Retry-After") or "").strip()
                    if _raw:
                        try:
                            _archive_retry = max(0, int(float(_raw)))
                        except Exception:
                            from email.utils import parsedate_to_datetime as _parse_archive_ra
                            from datetime import datetime as _archive_dt, timezone as _archive_tz
                            _when = _parse_archive_ra(_raw)
                            if _when.tzinfo is None:
                                _when = _when.replace(tzinfo=_archive_tz.utc)
                            _archive_retry = max(0, int((_when.astimezone(_archive_tz.utc)-_archive_dt.now(_archive_tz.utc)).total_seconds()))
                except Exception:
                    _archive_retry = None
                _archive_retry = record_account_throttle(body.account_id, _archive_retry or 30, source="cpx_archive_remove_429")
                _cpx_receipt_mark(body.account_id, request_key, "failed", http_status=429, result={"reason":"avito_account_throttled"})
                return {"status":"blocked","code":429,"reason_code":"avito_account_throttled",
                        "retry_after_seconds":int(_archive_retry),"request_key":request_key,"changed_avito":False}
            if rr.status_code != 200:
                _cpx_receipt_mark(body.account_id, request_key, "failed", http_status=rr.status_code, result={"body":rr.text[:200]})
                return {"status": "error", "code": rr.status_code, "message": rr.text[:200]}
            _cpx_receipt_mark(body.account_id, request_key, "succeeded", http_status=rr.status_code, result={"action":"archive"})
            _audit_log(body.account_id, "cpx_apply_archive",
                       f"объявление {body.item_id}: снято продвижение (0 контактов)",
                       body.actor_type or "user")
            # флаг идемпотентности: помечаем снятое, чтобы не снимать по кругу
            try:
                from app.db.session import SessionLocal as _SL
                from app.models.storage import Storage as _St
                import json as _j2, time as _t2
                _db2 = _SL()
                _k = f"cpx_archived:{body.item_id}"
                _row = _db2.query(_St).filter(_St.account_id == body.account_id, _St.key == _k).first()
                _val = _j2.dumps({"at": _t2.strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False)
                if _row:
                    _row.value = _val
                else:
                    _db2.add(_St(account_id=body.account_id, key=_k, value=_val))
                _db2.commit(); _db2.close()
            except Exception as _e2:
                print("[cpx] archive flag:", _e2)
            _mark_applied(db, body.account_id, akey)
            try:
                from app.services.action_log import log_action, ACTOR_USER
                log_action(account_id=body.account_id,
                           action="Отключил продвижение",
                           object_kind="объявление", object_name=str(body.item_id),
                           before_val="продвижение включено", after_val="продвижение снято",
                           reason="рекомендация советника: объявление не приносит обращений",
                           actor=(body.actor_type or "user"),
                           source=(body.source or "cpx_advisor.apply_one"),
                           trigger=(body.trigger or None),
                           mandate_id=_g.get("mandate_id"),
                           mandate_version=_g.get("mandate_version"),
                           request_id=(body.request_id or None),
                           balance_status="UNKNOWN",
                           guard_reason=_g.get("reason_code"))
            except Exception:
                pass
            return {"status": "ok", "action": "archive", "item_id": body.item_id}

        # raise / lower — считаем новую ставку с шагом и потолками.
        # ШАГ БЕРЁТСЯ ИЗ МАНДАТА — он единственный источник истины. Константа
        # в коде приводила к простою: владелец менял лимит, исполнитель жил по
        # своему проценту и молча блокировал каждое действие (12.08).
        from app.services.autonomy import load_mandate as _load_mandate
        _op_pre = "cpx.raise_bid" if body.action == "raise" else "cpx.lower_bid"
        _m = _load_mandate(db, body.account_id, _op_pre, source=body.source)
        try:
            # Prefer explicit step supplied by the native BORIS caller.
            # If absent, preserve the existing DB mandate path.
            _requested_step_pct = getattr(body, "max_bid_delta_pct", None)
            if _requested_step_pct is not None:
                _step_pct = int(_requested_step_pct)
            else:
                _step_pct = int((_m or {}).get("max_bid_delta_pct"))
        except (TypeError, ValueError):
            _step_pct = 0
        # BORIS_NATIVE_CPX_ADVICE_STEP_V1
        # Use the already-generated cpx_advice recommendation for this item.
        # Do not invent a new bid step; fail-closed guard remains below.
        if _step_pct <= 0:
            try:
                from sqlalchemy import text as _cpx_text
                import json as _cpx_json
                import re as _cpx_re
        
                _advice_row = db.execute(
                    _cpx_text("""
                        SELECT value
                        FROM storage
                        WHERE account_id=:account_id
                          AND key='cpx_advice'
                        ORDER BY id DESC
                        LIMIT 1
                    """),
                    {"account_id": account_id},
                ).fetchone()
        
                if _advice_row:
                    _raw = _advice_row[0]
                    _advice = (
                        _cpx_json.loads(_raw)
                        if isinstance(_raw, str)
                        else _raw
                    )
        
                    _raises = (
                        (_advice or {})
                        .get("recommendations", {})
                        .get("raise", [])
                        or []
                    )
        
                    for _rec in _raises:
                        _rid = _rec.get("item_id") or _rec.get("id")
        
                        if _rid is None or int(_rid) != int(item_id):
                            continue
        
                        _suggest = str(_rec.get("suggest") or "")
                        _match = _cpx_re.search(
                            r"(\d+(?:\.\d+)?)\s*%",
                            _suggest,
                        )
        
                        if _match:
                            _step_pct = int(float(_match.group(1)))
        
                        break
        
            except Exception:
                _step_pct = 0
        
        if _late_day_reset and _late_day_reset_baseline is not None:
            _step_pct = MAX_BID_STEP_PCT
        if _step_pct <= 0:
            return {"status": "blocked",
                    "reason_code": "blocked_step_pct_missing",
                    "message": "в мандате не задан допустимый шаг ставки",
                    "action": body.action, "item_id": body.item_id,
                    "changed_avito": False}
        _is_new_feed_launch = bool(
            body.action == "raise" and no_manual_promotion
            and str(body.source or "") == "feed20" and str(body.trigger or "") == "feed"
            and _step_pct == NEW_ITEM_NO_PROMO_MIN_BID_MARKUP_PCT
        )
        _is_bounded_first_activation = bool(
            _is_new_feed_launch or _native_provider_probe_first_bid
        )
        # NON_LAUNCH_ZERO_BID_RAISE_FAIL_CLOSED_V2: ordinary raise lanes must
        # prove a positive current bid so the <=10% step is mathematically real.
        # Zero may be activated only by a bounded feed launch or the exact native
        # provider-probe lane proven from fresh advisor evidence + getBids.
        if body.action == "raise" and int(cur_bid or 0) <= 0 and not _is_bounded_first_activation:
            return {"status": "blocked",
                    "reason_code": "blocked_bid_delta_unprovable",
                    "message": "текущая ставка равна нулю; обычное повышение без bounded first-bid launch запрещено",
                    "action": body.action, "item_id": body.item_id,
                    "changed_avito": False}

        # NEW_FEED_FIRST_BID_NO_STEP_BYPASS_V1: first activation is still a
        # money mutation. No launch/feed/late-day source may exceed the global
        # autonomous per-write step cap. STRICT_STEP10_SOURCE_INVARIANT_V1.
        _source_step_cap = int(effective_bid_step_cap_pct(str(body.source or "")))
        if _step_pct > _source_step_cap and not _late_day_reset:
            return {"status": "blocked",
                    "reason_code": "blocked_step_pct_above_hard_cap",
                    "message": ("шаг %d%% выше предела %d%% для контура %s"
                                % (_step_pct, _source_step_cap, str(body.source or "ordinary"))),
                    "action": body.action, "item_id": body.item_id,
                    "changed_avito": False}
        # STEP_IN_PENNIES_V2: gradual KPI acceleration must respect the configured
        # percent even on low bids. The old 1-ruble minimum step turned 3->4 RUB
        # into +33% and was correctly rejected by the 20% money guard.
        step = max(1, int(round(cur_bid * _step_pct / 100.0)))
        if body.action == "raise":
            new_bid = cur_bid + step
            if new_item_floor_bid is not None and new_bid < new_item_floor_bid:
                new_bid = new_item_floor_bid
            # Для нового объявления без продвижения +20% от Avito minBid —
            # это целевой стартовый floor. recBid ниже этого floor не должен
            # возвращать ставку обратно к минимуму; maxBid остаётся hard cap.
            # A raise must never turn into a decrease. Avito can return recBid
            # below the already-active bid; in that case it is not a valid cap
            # for a raise operation and must be ignored.
            # NEW_FEED_RAMP_RECBID_ESCAPE_V1: Avito often reports recBidPenny equal
            # to the currently active bid. Treating that recommendation as a hard
            # ceiling made every bounded +10% launch-ramp step a no-op. The exact
            # new_feed_hourly_ramp lane may cross recBid by its already-guarded
            # <=10% step; provider maxBid, account hard cap, daily budget, fresh
            # money signals and measurement guards remain authoritative.
            _can_cross_rec_bid = str(body.source or "") == "new_feed_hourly_ramp"
            if (not _fast_measure_mode and not _can_cross_rec_bid
                    and rec_bid and rec_bid >= cur_bid and new_bid > rec_bid
                    and not (new_item_floor_bid is not None and rec_bid < new_item_floor_bid)):
                new_bid = rec_bid
            if max_bid and new_bid > max_bid:
                new_bid = max_bid          # жёсткий потолок
        elif body.action == "lower":
            # LOWER_ZERO_BID_NO_ACTIVATION_V1: a lower-only safety action must
            # never turn promotion ON. Avito may expose minBid even when current
            # manual bid is zero; clamping 0-step to minBid previously converted
            # a rollback into a new paid promotion. Zero current bid means there
            # is nothing to lower, so stop before serialization/provider write.
            if int(cur_bid or 0) <= 0:
                return {"status":"skipped","reason_code":"lower_no_active_bid",
                        "action":body.action,"item_id":body.item_id,"bid_rub":0.0,
                        "changed_avito":False}
            if _late_day_reset and _late_day_reset_baseline is not None:
                _reset_target_penny = int(round(float(_late_day_reset_baseline) * 100.0))
                if int(cur_bid or 0) <= _reset_target_penny:
                    return {"status":"skipped","reason_code":"late_day_reset_already_at_or_below_baseline",
                            "action":body.action,"item_id":body.item_id,"bid_rub":cur_bid/100.0,
                            "changed_avito":False}
                new_bid = _reset_target_penny
            else:
                new_bid = cur_bid - step
            if new_bid < min_bid:
                new_bid = min_bid
        else:
            return {"status": "error", "message": f"неизвестное действие {body.action}"}

        # Avito принимает только суммы, кратные рублю: 154,4 ₽ отвергается

        # с ошибкой «Сумма в строке bidPenny должна быть кратна рублю».

        # Округляем В СТОРОНУ МЕНЬШЕГО ИЗМЕНЕНИЯ ставки: округление вниз
        # добавляет к шагу и выбивает за лимит мандата (21 ₽ при 15% даёт
        # 17,85 — вниз это 17 ₽ и дельта 19% вместо 15%).
        if body.action == "lower":
            new_bid = ((int(new_bid) + 99) // 100) * 100
        else:
            # MARKETER_STRICT_STEP_ROUNDING_V1: Avito accepts whole-ruble bids.
            # For raises we round DOWN, never up, so serialization itself cannot
            # turn a <=10% target into an 11-25% real money mutation on low bids.
            new_bid = (int(new_bid) // 100) * 100

        # LOWER_MINBID_MONOTONIC_FAIL_CLOSED_V1: a lower-only safety lane
        # must be monotonic. Avito may report minBid above the current active
        # bid; clamping a requested lower to that floor would silently become
        # a paid RAISE while the audit log still said "Снизил ставку".
        # Never serialize such a contradiction. Raise lanes may still honor
        # provider minBid, but lower lanes stop without provider mutation.
        if min_bid and new_bid < min_bid:
            if body.action == "lower":
                return {"status":"skipped",
                        "reason_code":"lower_provider_min_above_safe_target",
                        "action":body.action,"item_id":body.item_id,
                        "bid_rub":cur_bid/100.0,"provider_min_bid_rub":min_bid/100.0,
                        "changed_avito":False}
            new_bid = (int(min_bid) // 100) * 100 or 100

        # LOWER_MONOTONIC_FINAL_FENCE_V1: defend at the final serialized-money
        # boundary too, after whole-ruble rounding and every provider clamp.
        if body.action == "lower" and int(new_bid) >= int(cur_bid):
            return {"status":"skipped",
                    "reason_code":"lower_not_strictly_monotonic",
                    "action":body.action,"item_id":body.item_id,
                    "bid_rub":cur_bid/100.0,"candidate_bid_rub":new_bid/100.0,
                    "changed_avito":False}

        # NEW_FEED_FIRST_BID_HARD_CAP_CLAMP_V3: first activation targets
        # minBid+20%, but the owner's hard cap is absolute. If the hard cap is
        # still >= Avito minBid, clamp the launch to it (39 min / 40 cap => 40).
        # If the cap is below Avito minBid, leave the candidate unchanged so the
        # authoritative autonomy guard blocks it rather than sending an invalid bid.
        if _is_bounded_first_activation:
            try:
                from app.services.marketing_money_policy import effective_hard_bid_cap as _effective_launch_cap
                _launch_cap_rub=float(_effective_launch_cap(db, body.account_id) or 0)
                _avito_min_rub=float(min_bid or 0)/100.0
                if _launch_cap_rub>0 and _launch_cap_rub>=_avito_min_rub:
                    new_bid=min(int(new_bid), int(_launch_cap_rub*100))
            except Exception:
                pass

        # NATIVE_PROVIDER_EFFECTIVE_MIN_FINAL_FENCE_V1: provider max/hard-cap
        # clamps must never turn a native first activation into a bid below the
        # exact effective minimum that getBids proved can actually buy reach.
        if (_native_provider_probe_first_bid and new_item_floor_bid is not None
                and int(new_bid) < int(new_item_floor_bid)):
            return {"status": "blocked",
                    "reason_code": "native_first_bid_effective_min_unreachable",
                    "blocked_by": "native_first_bid_effective_min_unreachable",
                    "action": body.action, "item_id": body.item_id,
                    "provider_effective_min_bid_rub": round(float(new_item_floor_bid) / 100.0, 2),
                    "provider_max_bid_rub": round(float(max_bid) / 100.0, 2) if max_bid else None,
                    "changed_avito": False}

        # P0-B: холостое изменение не является мутацией.
        if int(new_bid) == int(cur_bid):
            return {"status": "skipped",
                    "reason_code": "skipped_bid_unchanged",
                    "action": body.action, "item_id": body.item_id,
                    "bid_rub": cur_bid / 100, "changed_avito": False}

        from app.services.autonomy import can_execute_live_action, log_guard_block
        _ctx = {"actor": body.actor_type or "", "trigger": body.trigger or "",
                "source": body.source or "cpx_advisor.apply_one",
                "request_id": body.request_id or ""}
        _op = "cpx.raise_bid" if body.action == "raise" else "cpx.lower_bid"
        _g = can_execute_live_action(
            db, body.account_id, _op, _ctx,
            bid_context={"item_id": body.item_id,
                         "old_bid_rub": cur_bid / 100.0,
                         "new_bid_rub": new_bid / 100.0,
                         "applied_step_pct": _step_pct,
                         "mandate_step_source": "money_mandates",
                         "avito_min_bid_rub": (min_bid / 100.0) if min_bid else None,
                         "new_item_no_promo": no_manual_promotion,
                         "new_item_min_markup_pct": (NEW_ITEM_NO_PROMO_MIN_BID_MARKUP_PCT if new_item_floor_bid is not None else None),
                         "new_item_floor_bid_rub": (new_item_floor_bid / 100.0) if new_item_floor_bid is not None else None,
                         "native_provider_probe_first_bid": bool(_native_provider_probe_first_bid),
                         "native_advice_generated_at": _native_first_bid_preflight.get("advice_generated_at"),
                         # AUTONOMY_ROUNDING_BYPASS_DISABLED_AT_CALLER_V1:
                         # short measurement windows may change cadence only.
                         # They are never evidence for exceeding the realized
                         # <=10% bid delta at the final autonomy guard.
                         "hourly_feed_ramp": False,
                         "hourly_feed_ramp_target_pct": None,
                         "fast_raise_rounding": False,
                         "fast_raise_target_pct": None,
                         "late_day_reset": bool(_late_day_reset),
                         "late_day_reset_baseline_rub": _late_day_reset_baseline},
            balance={"status": "UNKNOWN", "value": None})
        if not _g["allowed"]:
            log_guard_block(db, body.account_id, _op, "blocked", _g["reason_code"], _ctx,
                            object_id=body.item_id,
                            mandate={"id": _g.get("mandate_id"),
                                     "version": _g.get("mandate_version")},
                            balance_status="UNKNOWN",
                            limits=_g.get("limits_applied"), evidence=_g.get("evidence"))
            return {"status": "blocked", "reason_code": _g["reason_code"],
                    "action": body.action, "item_id": body.item_id,
                    "changed_avito": False}

        payload = {"actionTypeID": action_type, "bidPenny": int(new_bid), "itemID": body.item_id}
        # Autonomy guard may read/write audit state. Flush that short DB phase
        # before the real money-changing Avito call so provider latency cannot
        # pin locks/connections. Post-success bookkeeping opens a fresh phase.
        db.commit()
        db.close()
        # CPX_FINAL_RAISE_SIGNAL_RECHECK_V1: provider getBids + guard/audit work may
        # consume part of the 15-minute freshness budget. Re-open a short local
        # DB session immediately before creating the money receipt/provider write.
        # No reporting fallback or stale snapshot can authorize a raise here.
        if body.action == "raise":
            from app.db.session import SessionLocal as _SL_final_money
            from app.services.marketing_signal_guard import money_raise_signals_eligible as _raise_signals_ok
            _db_final_money = _SL_final_money()
            try:
                _signals_fresh, _signals_evidence = _raise_signals_ok(
                    _db_final_money, body.account_id, max_age_seconds=900
                )
            finally:
                _db_final_money.close()
            if not _signals_fresh:
                return {
                    "status": "blocked",
                    "action": body.action,
                    "item_id": body.item_id,
                    "blocked_by": "money_signal_stale_or_degraded",
                    "reason": "Spend/stats proof is no longer fresh enough for a bid raise.",
                    "money_signal": _signals_evidence,
                    "changed_avito": False,
                }
        # LATE_DAY_SPEND_BASELINE_DURABLE_V1: persist the exact live pre-boost
        # bid before the provider write. Even if transport becomes ambiguous,
        # BORIS retains the compensation target.
        if body.action == "raise" and _source == "late_day_spend_catchup":
            from zoneinfo import ZoneInfo as _ZoneInfo_boost
            from app.db.session import SessionLocal as _SL_boost
            _db_boost = _SL_boost()
            try:
                _bst = _load_json(_db_boost, body.account_id, "late_day_spend_boost_state") or {}
                _day_msk = datetime.now(_ZoneInfo_boost("Europe/Moscow")).date().isoformat()
                if str(_bst.get("day_msk") or "") != _day_msk:
                    _bst = {"day_msk": _day_msk, "status":"active", "items":{}}
                _items_bst = _bst.setdefault("items", {})
                _it_bst = _items_bst.setdefault(str(body.item_id), {})
                if _it_bst.get("baseline_bid_rub") is None:
                    _it_bst["baseline_bid_rub"] = round(cur_bid / 100.0, 2)
                try:
                    _target_boost = max(30, min(50, int(body.temporary_boost_target_pct or 30)))
                except Exception:
                    _target_boost = 30
                _it_bst["target_uplift_pct"] = max(int(_it_bst.get("target_uplift_pct") or 0), _target_boost)
                _it_bst["status"] = "boosting"
                _it_bst["last_prepare_at"] = datetime.now(timezone.utc).isoformat()
                _bst["status"] = "active"
                _bst["updated_at"] = datetime.now(timezone.utc).isoformat()
                _save_json(_db_boost, body.account_id, "late_day_spend_boost_state", _bst)
            finally:
                _db_boost.close()
        receipt, created = _cpx_receipt_prepare(body.account_id, request_key, body.item_id, body.action, body.source, cur_bid, int(new_bid))
        if not created:
            if str((receipt or {}).get("status") or "") == "capacity_blocked":
                return {"status":"blocked","action":body.action,"item_id":body.item_id,
                        "changed_avito":False,
                        "blocked_by":(receipt or {}).get("reason_code") or "blocked_money_capacity",
                        "reason_code":(receipt or {}).get("reason_code") or "blocked_money_capacity",
                        "request_key":request_key}
            return {"status":"blocked","action":body.action,"item_id":body.item_id,"changed_avito":False,"blocked_by":"cpx_execution_reconciliation","execution_status":receipt.get("status"),"request_key":request_key}
        # CPX_SETMANUAL_FINAL_SHARED_THROTTLE_RECHECK_V1: another BORIS worker
        # can receive provider 429 after our earlier getBids/preflight and before
        # this final money mutation. Re-check the shared tenant ledger at the last
        # possible point; never create provider traffic while Retry-After is live.
        _final_setmanual_retry = account_throttle_remaining(body.account_id)
        if _final_setmanual_retry > 0:
            _cpx_receipt_mark(body.account_id, request_key, "failed", error="avito_account_throttled")
            return {"status":"blocked","code":429,"reason_code":"avito_account_throttled",
                    "blocked_by":"shared_avito_account_throttle",
                    "retry_after_seconds":int(_final_setmanual_retry),
                    "request_key":request_key,"changed_avito":False}
        _cpx_receipt_mark(body.account_id, request_key, "attempting")
        try:
            wr = _httpx.post("https://api.avito.ru/cpxpromo/1/setManual",
                             headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                             json=payload, timeout=20)
        except Exception as exc:
            _cpx_receipt_mark(body.account_id, request_key, "delivery_unknown", error=repr(exc))
            return {"status":"blocked","code":"delivery_unknown","message":"Avito transport outcome unknown; blind retry forbidden","request_key":request_key,"changed_avito":False}
        if wr.status_code == 429:
            # CPX_SETMANUAL_429_PROPAGATION_V1: publish setManual Retry-After to
            # every internal alias of this real Avito tenant. Sibling workers then
            # stop provider I/O instead of amplifying the throttle storm.
            _retry_after_seconds = 30
            try:
                _retry_header = wr.headers.get("Retry-After")
                if _retry_header:
                    _retry_after_seconds = max(5, min(3600, int(float(_retry_header))))
            except Exception:
                _retry_after_seconds = 30
            _retry_after_seconds = record_account_throttle(
                body.account_id, _retry_after_seconds, source="cpx_setmanual_429"
            )
            _cpx_receipt_mark(body.account_id, request_key, "failed", http_status=429,
                              result={"reason":"avito_account_throttled"})
            return {"status":"blocked","code":429,"reason_code":"avito_account_throttled",
                    "blocked_by":"provider_setmanual_429",
                    "retry_after_seconds":int(_retry_after_seconds),
                    "request_key":request_key,"changed_avito":False}
        if wr.status_code != 200:
            _cpx_receipt_mark(body.account_id, request_key, "failed", http_status=wr.status_code, result={"body":wr.text[:200]})
            return {"status": "error", "code": wr.status_code, "message": wr.text[:200]}
        _cpx_receipt_mark(body.account_id, request_key, "succeeded", http_status=wr.status_code, observed_bid_penny=int(new_bid), result={"old_bid_penny":int(cur_bid),"new_bid_penny":int(new_bid)})
        _audit_log(body.account_id, "cpx_apply_bid",
                   f"объявление {body.item_id}: ставка {cur_bid/100:.0f}→{new_bid/100:.0f}₽ ({body.action})",
                   body.actor_type or "user")
        _mark_applied(db, body.account_id, akey)

        # ---------------------------------------------------------
        # MEASURE EFFECT
        #
        # После успешного изменения ставки начинаем новый период
        # измерения. Повторное изменение до завершения измерения
        # должно быть запрещено.
        # ---------------------------------------------------------
        measure_state = _save_measure_state(
            db=db,
            account_id=body.account_id,
            item_id=body.item_id,
            action=body.action,
            old_bid_rub=cur_bid / 100,
            new_bid_rub=new_bid / 100,
            source=body.source,
            request_key=request_key,
            provider_receipt_proven=bool(_native_provider_probe_first_bid),
        )

        try:
            from app.services.action_log import log_action, ACTOR_USER
            log_action(account_id=body.account_id,
                       action=("Поднял ставку" if body.action == "raise" else "Снизил ставку"),
                       object_kind="объявление", object_name=str(body.item_id),
                       before_val="%.0f руб" % (cur_bid / 100),
                       after_val="%.0f руб" % (new_bid / 100),
                       reason="рекомендация советника по статистике за 7 дней",
                       actor=(body.actor_type or "user"),
                       source=(body.source or "cpx_advisor.apply_one"),
                       trigger=(body.trigger or None),
                       mandate_id=_g.get("mandate_id"),
                       mandate_version=_g.get("mandate_version"),
                       request_id=(body.request_id or None),
                       balance_status="UNKNOWN",
                       guard_reason=_g.get("reason_code"))
        except Exception:
            pass
        return {"status": "ok", "action": body.action, "item_id": body.item_id,
                "old_bid_rub": cur_bid/100, "new_bid_rub": new_bid/100,
                "native_first_activation": bool(_native_provider_probe_first_bid),
                "native_first_bid_evidence": (_native_first_bid_preflight if _native_provider_probe_first_bid else None)}
    finally:
        db.close()


# ======================= P0-C: денежный предохранитель авто-raise =============
BALANCE_KNOWN = "KNOWN"
BALANCE_UNKNOWN = "UNKNOWN"
BALANCE_CTX_MAX_AGE_SEC = 180

GUARD_REASONS = {
    "blocked_balance_no_uid":
        "у аккаунта не заполнен avito_user_id — запрос баланса невозможен",
    "blocked_balance_auth_error":
        "достоверное значение через API недоступно: Avito отклонил авторизацию",
    "blocked_balance_rate_limited":
        "достоверное значение через API недоступно: Avito ограничил частоту запросов",
    "blocked_balance_unavailable":
        "достоверное значение через API недоступно: сеть или таймаут",
    "blocked_balance_contract_invalid":
        "достоверное значение через API недоступно: в ответе нет числового поля real",
    "blocked_balance_http_error":
        "достоверное значение через API недоступно: неожиданный код ответа",
    "blocked_guard_config_missing":
        "не задан дневной бюджет — порог безопасности неизвестен",
    "blocked_insufficient_balance":
        "на счёте меньше одной максимальной цены обращения, автоматическое повышение ставки запрещено",
    "blocked_cpl_above_redline":
        "фактическая цена бизнес-лида выше красной цены, автоматическое повышение ставки запрещено",
}
CLIENT_SAFE_MESSAGE = {
    "blocked_insufficient_balance": "Недостаточно средств на счёте Авито",
    "blocked_cpl_above_redline": "Цена лида выше допустимого предела — повышение ставок остановлено",
    "blocked_guard_config_missing": "Не задан дневной бюджет продвижения",
}
CLIENT_SAFE_DEFAULT = "Проверка баланса временно недоступна"


def _balance_unknown(code, raw=None, now=None):
    import datetime as _dt
    return {"status": BALANCE_UNKNOWN, "value": None, "_raw": raw,
            "reason": GUARD_REASONS[code], "error_code": code, "bonus": None,
            "fetched_at": now or _dt.datetime.utcnow().isoformat(timespec="seconds"),
            "source": "avito_live"}


def _fetch_live_balance(account_id):
    """Живой баланс Avito. ЕДИНСТВЕННЫЙ источник guard.
    daily_stats и baseline не используются даже как fallback."""
    import datetime as _dt
    now = _dt.datetime.utcnow().isoformat(timespec="seconds")
    # CPX_BALANCE_SHARED_ACCOUNT_THROTTLE_V1: live balance is part of the money
    # guard, but it still consumes Avito quota. Respect cross-process Retry-After
    # before token/self/balance I/O and publish any 429 for sibling stages.
    from app.services.avito_account_throttle import account_throttle_remaining as _balance_throttle_remaining, record_account_throttle as _balance_record_throttle
    _balance_retry = _balance_throttle_remaining(account_id)
    if _balance_retry > 0:
        out = _balance_unknown("blocked_balance_rate_limited", 429, now)
        out["retry_after_seconds"] = int(_balance_retry)
        return out

    def _balance_record_429(resp, source):
        retry = None
        try:
            raw = str(resp.headers.get("Retry-After") or "").strip()
            if raw:
                try:
                    retry = max(0, int(float(raw)))
                except Exception:
                    from email.utils import parsedate_to_datetime
                    from datetime import datetime, timezone
                    when = parsedate_to_datetime(raw)
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=timezone.utc)
                    retry = max(0, int((when.astimezone(timezone.utc)-datetime.now(timezone.utc)).total_seconds()))
        except Exception:
            retry = None
        return _balance_record_throttle(account_id, retry or 30, source=source)

    try:
        from app.api.avito import _get_avito_credentials, get_avito_token, _extract_token
        _cid, _cs, uid = _get_avito_credentials(account_id)
        tok = _extract_token(get_avito_token(account_id))
        import httpx as _hx
        if not uid:
            # AVITO_UID_SELF_HEAL_V1: a connected account must not require the
            # owner to populate avito_user_id manually. Resolve from Avito's own
            # authenticated self endpoint, persist account-scoped identity, then
            # continue the same live-balance guard. Any ambiguity fails closed.
            _self = _hx.get("https://api.avito.ru/core/v1/accounts/self",
                            headers={"Authorization": "Bearer %s" % tok}, timeout=10)
            if _self.status_code == 429:
                _retry = _balance_record_429(_self, "cpx_balance_self_429")
                out = _balance_unknown("blocked_balance_rate_limited", 429, now)
                out["retry_after_seconds"] = int(_retry)
                return out
            if _self.status_code != 200:
                return _balance_unknown("blocked_balance_no_uid", _self.status_code, now)
            _uid_raw = (_self.json() or {}).get("id")
            uid = str(_uid_raw or "").strip()
            if not uid or not uid.isdigit():
                return _balance_unknown("blocked_balance_no_uid", "invalid_self_id", now)
            try:
                from app.db.session import SessionLocal as _SL_uid
                from sqlalchemy import text as _text_uid
                _db_uid = _SL_uid()
                try:
                    _changed_uid = _db_uid.execute(
                        _text_uid("UPDATE accounts SET avito_user_id=:uid WHERE account_id=:account_id"),
                        {"uid": uid, "account_id": account_id},
                    )
                    if int(_changed_uid.rowcount or 0) != 1:
                        _db_uid.rollback()
                        return _balance_unknown("blocked_balance_no_uid", "account_missing", now)
                    _db_uid.commit()
                finally:
                    _db_uid.close()
            except Exception as _uid_exc:
                return _balance_unknown("blocked_balance_no_uid", str(_uid_exc)[:120], now)
        r = _hx.get("https://api.avito.ru/core/v1/accounts/%s/balance/" % uid,
                    headers={"Authorization": "Bearer %s" % tok}, timeout=10)
    except Exception as e:
        return _balance_unknown("blocked_balance_unavailable", str(e)[:120], now)
    if r.status_code in (401, 403):
        return _balance_unknown("blocked_balance_auth_error", r.status_code, now)
    if r.status_code == 429:
        _retry = _balance_record_429(r, "cpx_balance_429")
        out = _balance_unknown("blocked_balance_rate_limited", r.status_code, now)
        out["retry_after_seconds"] = int(_retry)
        return out
    if r.status_code != 200:
        return _balance_unknown("blocked_balance_http_error", r.status_code, now)
    try:
        body = r.json()
        real = body.get("real")
        if isinstance(real, bool) or not isinstance(real, (int, float)):
            return _balance_unknown("blocked_balance_contract_invalid", body, now)
    except Exception:
        return _balance_unknown("blocked_balance_contract_invalid", r.text[:120], now)
    return {"status": BALANCE_KNOWN, "value": float(real), "_raw": body, "reason": None,
            "error_code": None, "bonus": body.get("bonus"), "fetched_at": now,
            "source": "avito_live"}


def _ctx_valid(ctx, account_id):
    """Контекст цикла годен только свежий, валидный и по тому же аккаунту."""
    import datetime as _dt
    if not isinstance(ctx, dict):
        return False
    if str(ctx.get("account_id") or "") != str(account_id):
        return False
    if ctx.get("status") != BALANCE_KNOWN or not isinstance(ctx.get("value"), (int, float)):
        return False
    try:
        age = (_dt.datetime.utcnow()
               - _dt.datetime.fromisoformat(str(ctx.get("fetched_at")))).total_seconds()
    except Exception:
        return False
    return 0 <= age <= BALANCE_CTX_MAX_AGE_SEC


def check_raise_allowed(account_id, balance_ctx=None):
    """Денежный предохранитель авто-raise. Только решает, ничего не меняет."""
    bal = balance_ctx if _ctx_valid(balance_ctx, account_id) else _fetch_live_balance(account_id)
    if bal.get("status") != BALANCE_KNOWN:
        code = bal.get("error_code") or "blocked_balance_unavailable"
        return {"allowed": False, "reason_code": code,
                "human_reason": GUARD_REASONS.get(code, CLIENT_SAFE_DEFAULT),
                "client_message": CLIENT_SAFE_DEFAULT, "balance": bal}
    limit = 0.0
    try:
        from app.db.session import SessionLocal as _SL
        _db = _SL()
        try:
            _kpi = _load_json(_db, account_id, "kpi_settings") or {}
        finally:
            _db.close()
        limit = float(_kpi.get("daily_budget_limit_rub") or 0)
        red_cpl = float(_kpi.get("max_cost_per_lead_rub") or 0)
        # FINAL_RAISE_OWNER_PROVENANCE_V1: the final provider-write guard must
        # independently prove the exact authenticated owner budget. Mandate
        # creation already enforces this, but a stale historical mandate must
        # never become a bypass if configuration provenance later disappears.
        _auth = _kpi.get("daily_budget_authorization") if isinstance(_kpi.get("daily_budget_authorization"), dict) else {}
        _budget_proven = bool(
            limit > 0
            and str(_auth.get("policy_version") or "") == "MONEY_BUDGET_OWNER_PROVENANCE_V1"
            and int(_auth.get("authorized_by_user_id") or 0) > 0
            and float(_auth.get("daily_budget_limit_rub") or -1) == limit
            and str(_auth.get("source") or "") == "authenticated_set_kpi_settings"
        )
    except Exception:
        limit = 0.0
        _budget_proven = False
    if limit <= 0:
        c = "blocked_guard_config_missing"
        return {"allowed": False, "reason_code": c, "human_reason": GUARD_REASONS[c],
                "client_message": CLIENT_SAFE_MESSAGE[c], "balance": bal}
    if not _budget_proven:
        return {"allowed": False, "reason_code": "blocked_daily_budget_owner_provenance_missing",
                "human_reason": "суточный бюджет не имеет подтверждённого владельцем источника",
                "client_message": "Автоматическое повышение ставок остановлено: бюджет не подтверждён владельцем.",
                "balance": bal, "daily_budget_limit_rub": limit}
    # FINAL_RAISE_RED_CPL_BALANCE_FLOOR_V1 / V2_EARLY:
    # A known wallet below one owner-defined red CPL is already conclusive proof
    # that an autonomous raise is unsafe. Block here before asking for spend/stats
    # freshness, so external funding shortage is diagnosed correctly and workers
    # do not keep probing a doomed write lane while reporting a weaker stale-spend
    # reason. A sufficient wallet still requires every spend/stat guard below.
    _required_balance_floor = float(red_cpl or 0)
    if bal["value"] <= 0 or (
        _required_balance_floor > 0
        and float(bal["value"]) + 1e-9 < _required_balance_floor
    ):
        c = "blocked_insufficient_balance"
        return {"allowed": False, "reason_code": c,
                "human_reason": "%s: %.2f ₽ при минимуме %.2f ₽" % (
                    GUARD_REASONS[c], bal["value"], _required_balance_floor
                ),
                "client_message": CLIENT_SAFE_MESSAGE[c],
                "balance": bal, "daily_budget_limit_rub": limit,
                "red_cpl_rub": _required_balance_floor,
                "required_balance_floor_rub": _required_balance_floor}
    # Canonical daily-spend guard: wallet balance is not today's ad spend.
    # Once Avito stats/v2 says the configured daily limit is reached, any
    # further automatic bid raise is fail-closed for the rest of the day.
    spent_today = None
    business_contacts_today = None
    spend_signal = {"status": "unknown", "reason": "spending_unavailable"}
    _presence_pressure = {"blocked": False, "reason": "not_checked"}
    try:
        from app.db.session import SessionLocal as _SL_spend
        from app.services.marketing_money_policy import (
            latest_confirmed_spend as _latest_confirmed_spend,
            presence_budget_pressure as _presence_budget_pressure,
        )
        _db_spend = _SL_spend()
        _mut_persisted_ok = False
        _mut_stats_ok = False
        try:
            # MARKETER_MUTATION_SPEND_FRESH_15M_V1: this is the final mutation
            # guard, so stale/degraded reporting snapshots can never authorize
            # more spend even if an earlier rollout preflight saw fresher data.
            spend_signal = _latest_confirmed_spend(_db_spend, account_id, max_age_seconds=900) or {}
            # PRESENCE_BUDGET_FINAL_RAISE_GUARD_V1: completed presence-spend
            # history blocks a new CPX raise before the provider posts another
            # delayed lump into today's spend counter.
            _presence_pressure = _presence_budget_pressure(_db_spend, account_id, float(limit))
            from app.services.marketing_signal_guard import money_spend_signal_eligible as _money_signal_ok
            _mut_persisted_ok = bool(_money_signal_ok(_db_spend, account_id, spend_signal))
            # MARKETER_MUTATION_STATS_FRESH_15M_V1: re-check the complete stats
            # snapshot at the final mutation boundary too. A process may spend
            # minutes between rollout preflight and provider write; stale item
            # observations are never authority to increase money.
            try:
                import datetime as _mut_dt
                _stats = _load_json(_db_spend, account_id, "daily_stats:" + marketing_today_iso()) or {}
                _collected = _mut_dt.datetime.fromisoformat(str(_stats.get("collected_at") or "").replace("Z", "+00:00"))
                if _collected.tzinfo is None:
                    _collected = _collected.replace(tzinfo=_mut_dt.timezone.utc)
                _stats_age = (_mut_dt.datetime.now(_mut_dt.timezone.utc) - _collected.astimezone(_mut_dt.timezone.utc)).total_seconds()
                _mut_stats_ok = bool(((_stats.get("completeness") or {}).get("complete") is True)
                                     and -60 <= _stats_age <= 900)
                if _mut_stats_ok:
                    _raw_contacts_today = sum(
                        int((x or {}).get("contacts") or 0)
                        for x in (_stats.get("items") or []) if isinstance(x, dict)
                    )
                    from app.services.kpi_lead_quality import apply_business_lead_filter as _business_filter
                    _lead_quality = _business_filter(_db_spend, account_id, _raw_contacts_today) or {}
                    business_contacts_today = float(_lead_quality.get("business_contacts_today") or 0)
            except Exception:
                _mut_stats_ok = False
                business_contacts_today = None
        finally:
            _db_spend.close()
        # MARKETER_MUTATION_SPEND_TIMESTAMP_SANITY_V1: a future/non-finite
        # provider timestamp cannot be normalized into a fresh money signal.
        try:
            import math as _mut_math, time as _mut_time
            _mut_ts=float(spend_signal.get("timestamp") or 0)
            _mut_val=float(spend_signal.get("spent_today_rub"))
            _mut_ok=(spend_signal.get("status") == "ok" and _mut_persisted_ok and _mut_stats_ok
                     and _mut_math.isfinite(_mut_ts) and _mut_math.isfinite(_mut_val)
                     and _mut_ts>0 and _mut_val>=0 and _mut_ts<=_mut_time.time()+60)
        except Exception:
            _mut_ok=False; _mut_val=None
        if _mut_ok:
            spent_today = _mut_val
    except Exception:
        spent_today = None
    if spent_today is None:
        c = "blocked_spendings_unavailable"
        return {"allowed": False, "reason_code": c,
                "human_reason": "нет достоверных данных о расходах Avito за сегодня",
                "client_message": CLIENT_SAFE_DEFAULT, "balance": bal,
                "daily_budget_limit_rub": limit}
    # FINAL_RAISE_RED_CPL_ECONOMICS_V1: all autonomous raise lanes share the
    # same account-level economics guard. A confirmed current-day business CPL
    # above the owner's red line forbids further bid increases; lower/repair
    # lanes remain available and are the correct recovery path.
    if red_cpl > 0 and business_contacts_today is not None and business_contacts_today > 0:
        _actual_cpl_today = float(spent_today) / float(business_contacts_today)
        if _actual_cpl_today > float(red_cpl) + 1e-9:
            c = "blocked_cpl_above_redline"
            return {"allowed": False, "reason_code": c,
                    "human_reason": "%s: %.2f ₽ при лимите %.2f ₽" % (
                        GUARD_REASONS[c], _actual_cpl_today, float(red_cpl)
                    ),
                    "client_message": CLIENT_SAFE_MESSAGE[c],
                    "balance": bal, "daily_budget_limit_rub": limit,
                    "spent_today_rub": spent_today,
                    "business_contacts_today": business_contacts_today,
                    "actual_cpl_rub": round(_actual_cpl_today, 2),
                    "red_cpl_rub": float(red_cpl)}
    if bool((_presence_pressure or {}).get("blocked")):
        return {"allowed": False, "reason_code": "blocked_presence_budget_pressure",
                "human_reason": "расход на присутствие/размещение уже системно несовместим с суточным лимитом",
                "client_message": "Новые повышения ставок остановлены: базовый расход размещения уже превышает безопасный дневной лимит.",
                "balance": bal, "daily_budget_limit_rub": limit,
                "spent_today_rub": spent_today,
                "presence_budget_pressure": _presence_pressure}
    if spent_today >= limit:
        return {"allowed": False, "reason_code": "blocked_daily_budget_exhausted",
                "human_reason": "суточный лимит исчерпан: %.2f ₽ из %.0f ₽" % (spent_today, limit),
                "client_message": "Суточный рекламный лимит исчерпан — повышение ставок остановлено.",
                "balance": bal, "daily_budget_limit_rub": limit,
                "spent_today_rub": spent_today}
    # MARKETER_ADAPTIVE_BUDGET_RESERVE_V1: the fixed 10% reserve was unsafe on
    # high-velocity accounts. Use the same adaptive cutoff as the lower-only
    # budget brake so no raise can race ahead of the next control cycle.
    try:
        from app.db.session import SessionLocal as _SL_budget_policy
        from app.services.marketing_money_policy import adaptive_budget_brake_policy as _adaptive_budget_policy
        _db_budget_policy = _SL_budget_policy()
        try:
            _budget_policy = _adaptive_budget_policy(_db_budget_policy, account_id, limit, spent_today)
        finally:
            _db_budget_policy.close()
        _budget_cutoff = float(_budget_policy.get("cutoff_ratio") or 0.90)
    except Exception:
        _budget_policy = {"cutoff_ratio": 0.90, "reason": "adaptive_policy_unavailable_fallback"}
        _budget_cutoff = 0.90
    if spent_today >= limit * _budget_cutoff:
        return {"allowed": False, "reason_code": "blocked_daily_budget_near_limit",
                "human_reason": "расход уже %.2f ₽ из %.0f ₽; защитный порог %.1f%%" % (spent_today, limit, _budget_cutoff*100.0),
                "client_message": "Рекламный бюджет приближается к безопасному пределу — новые повышения ставок остановлены.",
                "balance": bal, "daily_budget_limit_rub": limit,
                "spent_today_rub": spent_today, "adaptive_budget_policy": _budget_policy}
    return {"allowed": True, "reason_code": None,
            "human_reason": "расход %.2f ₽ из дневного лимита %.0f ₽" % (spent_today, limit),
            "client_message": None, "balance": bal, "daily_budget_limit_rub": limit,
            "spent_today_rub": spent_today}


# PAID_TARIFF_RAISE_MANDATE_V1
# Active paid BORIS tariff + KPI is the owner's standing instruction for the
# virtual marketer to pursue KPI. Existing valid raise mandate wins; otherwise
# create one bounded mandate instead of silently blocking the whole lifecycle.
def _marketer_mandate_entitlement(billing: dict, service_entitlement: dict) -> tuple[bool, dict]:
    """Resolve automatic marketer mandate scope from canonical paid-period evidence.

    MARKETER_UNLIMITED_NOT_ENTITLEMENT_V1: ``billing.unlimited`` is legacy
    product metadata and may describe another module or an old commercial
    arrangement. It is not proof of a current Avito-marketing service period.
    Automatic spend therefore requires ``marketing_service_entitlement`` to be
    explicitly active; unknown/expired stays fail-closed.
    """
    service_entitlement = dict(service_entitlement or {})
    return service_entitlement.get("state") == "active", service_entitlement


def ensure_paid_tariff_raise_mandate(account_id: str):
    from app.db.session import SessionLocal
    from app.models.account import Account
    from app.models.storage import Storage
    from app.api.billing import _load_billing
    from sqlalchemy import text as _text_mandate
    from datetime import datetime as _dt_mandate, timezone as _tz_mandate, timedelta as _td_mandate
    import json as _json_mandate

    if not account_id or str(account_id).startswith("qa") or str(account_id).startswith("user:"):
        return {"status": "blocked", "reason": "not_production_account"}
    db = SessionLocal()
    try:
        acc = db.query(Account).filter(Account.account_id == account_id).first()
        if not acc or not (acc.avito_client_id and acc.avito_client_secret):
            return {"status": "blocked", "reason": "no_real_avito_account"}
        billing = _load_billing(account_id, db=db) or {}
        # MARKETER_CANONICAL_ENTITLEMENT_GUARD_V1: tariff labels and legacy
        # unlimited metadata never prove a current marketing service period.
        from app.services.control_plane_adapters_ext import marketing_service_entitlement
        _entitlement = marketing_service_entitlement(db, account_id)
        marketer_entitled, _entitlement = _marketer_mandate_entitlement(
            billing, _entitlement
        )
        if not marketer_entitled:
            # Revoke stale paid-tariff raise authority immediately. Safety lowers
            # may still be performed through separate explicitly authorized lanes;
            # this mandate must never keep spending after service expiry.
            _expired = db.execute(_text_mandate("""
                UPDATE money_mandates SET status='revoked', revoked_at=now(),
                       confirmation_text='BORIS marketer paid period inactive; stale automatic money authority revoked.',
                       confirmation_version='paidperiod_v2', version=version+1
                 WHERE :acc=ANY(account_scope) AND status='active' AND revoked_at IS NULL
                   AND source='paid_tariff_kpi'
            """), {"acc": account_id})
            if int(_expired.rowcount or 0) > 0:
                db.commit()
            return {"status": "blocked", "reason": "no_active_boris_marketer_entitlement",
                    "service_entitlement": _entitlement,
                    "revoked_stale_paid_mandates": int(_expired.rowcount or 0)}
        # PAID_TARIFF_KPI_LATEST_SINGLETON_V1: always reconcile money authority
        # from the newest owner settings. Historical duplicate rows must never
        # resurrect a paused/old budget state.
        krow = (db.query(Storage)
                .filter(Storage.account_id == account_id, Storage.key == "kpi_settings")
                .order_by(Storage.id.desc()).first())
        try:
            kpi = _json_mandate.loads(krow.value or "{}") if krow else {}
        except Exception:
            kpi = {}
        # PAID_KPI_AUTOPILOT_MODE_RECONCILE_V1: the DB money grant must track
        # the owner's current autonomous-mode switches, not merely rely on the
        # final provider-write guard. When autonomous bidding is paused, keep
        # only lower_bid safety authority and discard any stored raise budget.
        arow = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "autopilot_settings").order_by(Storage.id.desc()).first()
        try:
            _ap_cfg = _json_mandate.loads(arow.value or "{}") if arow else {}
        except Exception:
            _ap_cfg = {}
        _ap_mode = str(_ap_cfg.get("mode") or "").strip()
        _bid_auto_enabled = bool(kpi.get("bid_autopilot"))
        if (not _bid_auto_enabled) or _ap_mode not in {"goal_auto", "always_auto"}:
            _changed = db.execute(_text_mandate("""
                UPDATE money_mandates
                   SET allowed_operations=ARRAY['cpx.lower_bid']::text[],
                       daily_budget_rub=NULL,
                       confirmation_text=:txt,
                       confirmation_version='autopause_v1', version=version+1
                 WHERE :acc=ANY(account_scope) AND status='active' AND revoked_at IS NULL
                   AND source='paid_tariff_kpi'
                   AND ('cpx.raise_bid'=ANY(allowed_operations) OR daily_budget_rub IS NOT NULL
                        OR confirmation_version IS DISTINCT FROM 'autopause_v1')
            """), {"acc": account_id, "txt": (
                "Bid autopilot disabled; automatic raise authority downgraded to lower-only."
                if not _bid_auto_enabled else
                "Autonomous marketing mode disabled; automatic raise authority downgraded to lower-only."
            )})
            if int(_changed.rowcount or 0) > 0:
                db.commit()
            return {"status":"blocked",
                    "reason":("bid_autopilot_disabled" if not _bid_auto_enabled else "autonomous_marketing_mode_disabled"),
                    "raise_disabled":True,"lower_safety_available":True,
                    "reconciled_mandates":int(_changed.rowcount or 0),
                    "autopilot_mode":_ap_mode or "not_configured"}
        target = float(kpi.get("target_leads_per_day") or 0)
        max_cpl = float(kpi.get("max_cost_per_lead_rub") or 0)
        explicit_daily = float(kpi.get("daily_budget_limit_rub") or 0)
        # MONEY_BUDGET_OWNER_PROVENANCE_GUARD_V1: a positive number in legacy
        # kpi_settings is not itself proof that the owner authorized spend.
        # New authenticated writes persist exact user/time/value evidence.
        _budget_auth = kpi.get("daily_budget_authorization") if isinstance(kpi.get("daily_budget_authorization"), dict) else {}
        _budget_proven = bool(
            str(_budget_auth.get("policy_version") or "") == "MONEY_BUDGET_OWNER_PROVENANCE_V1"
            and int(_budget_auth.get("authorized_by_user_id") or 0) > 0
            and float(_budget_auth.get("daily_budget_limit_rub") or -1) == explicit_daily
            and str(_budget_auth.get("source") or "") == "authenticated_set_kpi_settings"
        )
        if explicit_daily > 0 and not _budget_proven:
            _changed = db.execute(_text_mandate("""
                UPDATE money_mandates
                   SET allowed_operations=ARRAY['cpx.lower_bid']::text[],
                       daily_budget_rub=NULL,
                       confirmation_text='Daily budget exists without authenticated owner provenance; automatic bid raises are disabled.',
                       confirmation_version='moneyprov_v1', version=version+1
                 WHERE :acc=ANY(account_scope)
                   AND status='active' AND revoked_at IS NULL
                   AND source='paid_tariff_kpi'
                   AND 'cpx.raise_bid'=ANY(allowed_operations)
            """), {"acc": account_id})
            if int(_changed.rowcount or 0) > 0:
                db.commit()
            return {"status":"blocked","reason":"daily_budget_owner_provenance_missing",
                    "target_leads_per_day":target,"max_cost_per_lead_rub":max_cpl,
                    "daily_budget_limit_rub":explicit_daily,
                    "raise_disabled":True,"lower_safety_available":True,
                    "reconciled_mandates":int(_changed.rowcount or 0)}

        # KPI_RED_PRICE_MANDATE_GUARD_V1: a daily budget alone is not enough to
        # authorize automatic bid raises. The owner asked to scale only up to a
        # known red lead price, therefore target + max CPL must be explicit too.
        # Reconcile any historical raise-capable mandate to lower-only rather
        # than letting a stale grant bypass missing economics.
        if target <= 0 or max_cpl <= 0:
            _changed = db.execute(_text_mandate("""
                UPDATE money_mandates
                   SET allowed_operations=ARRAY['cpx.lower_bid']::text[],
                       confirmation_text='KPI target or red CPL is missing; automatic bid raises are disabled until economics are explicit.',
                       confirmation_version='kpired_v1', version=version+1
                 WHERE :acc=ANY(account_scope) AND status='active' AND revoked_at IS NULL
                   AND source='paid_tariff_kpi' AND 'cpx.raise_bid'=ANY(allowed_operations)
            """), {"acc": account_id})
            if int(_changed.rowcount or 0) > 0: db.commit()
            return {"status":"blocked","reason":"kpi_economic_limits_missing",
                    "target_leads_per_day":target,"max_cost_per_lead_rub":max_cpl,
                    "daily_budget_limit_rub":explicit_daily,"raise_disabled":True,
                    "lower_safety_available":True,"reconciled_mandates":int(_changed.rowcount or 0)}

        # EXPLICIT_DAILY_BUDGET_MANDATE_V1: a KPI target and red CPL are not an
        # owner authorization to invent advertising spend. Missing/zero explicit
        # daily budget is fail-closed for raises. Existing auto-created
        # paid_tariff_kpi mandates are downgraded to lower-only so historical
        # inferred budgets cannot silently survive after this policy correction.
        if explicit_daily <= 0:
            _changed = db.execute(_text_mandate("""
                UPDATE money_mandates
                   SET allowed_operations=ARRAY['cpx.lower_bid']::text[],
                       daily_budget_rub=NULL,
                       confirmation_text='No explicit daily advertising budget is configured; automatic bid raises are disabled. Lowering remains available as a safety action.',
                       confirmation_version='expbud_v1',
                       version=version+1
                 WHERE :acc=ANY(account_scope)
                   AND status='active' AND revoked_at IS NULL
                   AND source='paid_tariff_kpi'
                   AND 'cpx.raise_bid'=ANY(allowed_operations)
            """), {"acc": account_id})
            if int(_changed.rowcount or 0) > 0:
                db.commit()
            return {"status":"blocked","reason":"explicit_daily_budget_missing",
                    "target_leads_per_day":target,"max_cost_per_lead_rub":max_cpl,
                    "daily_budget_limit_rub":explicit_daily,
                    "raise_disabled":True,"lower_safety_available":True,
                    "reconciled_mandates":int(_changed.rowcount or 0)}

        # FULL_MARKETER_SCOPE_V3: пакет действий зависит от реального активного
        # портфеля, а не от исторического жёсткого лимита «1 действие за час».
        # Денежные границы (daily_budget/max_bid/step) по-прежнему остаются guards.
        today_stats = _load_json(db, account_id, f"daily_stats:{marketing_today_iso()}") or {}
        active_count = sum(1 for it in (today_stats.get("items") or []) if it.get("status") == "active")
        # DB_GUARDED_MARKETER_CAPACITY_V1: the mandate itself is bounded to the
        # same hard blast radius enforced by PostgreSQL. Portfolio size may
        # influence ranking, never the amount of money authority granted.
        from app.services.marketing_money_policy import (
            effective_hard_bid_cap,
            GLOBAL_MAX_BID_DELTA_PCT,
            GLOBAL_MAX_AUTO_ACTIONS_RUN,
            GLOBAL_MAX_AUTO_ACTIONS_DAY,
        )
        desired_actions_run = int(GLOBAL_MAX_AUTO_ACTIONS_RUN)
        desired_actions_day = int(GLOBAL_MAX_AUTO_ACTIONS_DAY)
        desired_operations = ["cpx.raise_bid", "cpx.lower_bid"]
        account_hard_bid_cap = float(effective_hard_bid_cap(db, account_id))
        account_max_step_pct = int(GLOBAL_MAX_BID_DELTA_PCT)

        now = _dt_mandate.now(_tz_mandate.utc)
        rows = db.execute(_text_mandate(
            "SELECT id,version,status,source,allowed_operations,valid_from,valid_until,revoked_at,"
            " max_actions_run,max_actions_day,max_bid_delta_pct,max_bid_rub,daily_budget_rub"
            " FROM money_mandates WHERE :acc=ANY(account_scope) ORDER BY id DESC"),
            {"acc": account_id}).mappings().all()
        for m in rows:
            if (m.get("status") == "active" and m.get("revoked_at") is None
                    and m.get("source") == "paid_tariff_kpi"
                    and (not m.get("valid_from") or m.get("valid_from") <= now)
                    and (not m.get("valid_until") or m.get("valid_until") > now)
                    and "cpx.raise_bid" in (m.get("allowed_operations") or [])):
                # PAID_TARIFF_MANDATE_SINGLETON_V1: the canonical active mandate
                # must carry the current explicit owner budget. A historical
                # lower-only mandate from a zero-budget state must not survive
                # beside a current raise mandate.
                old_ops = set(m.get("allowed_operations") or [])
                needs_upgrade = (
                    int(m.get("max_actions_run") or 0) < desired_actions_run
                    or int(m.get("max_actions_day") or 0) < desired_actions_day
                    or not set(desired_operations).issubset(old_ops)
                    or float(m.get("daily_budget_rub") or 0) != explicit_daily
                    or float(m.get("max_bid_rub") or 0) != account_hard_bid_cap
                    or int(m.get("max_bid_delta_pct") or 0) != account_max_step_pct
                )
                if needs_upgrade:
                    db.execute(_text_mandate("""
                        UPDATE money_mandates
                           SET allowed_operations=ARRAY['cpx.raise_bid','cpx.lower_bid']::text[],
                               max_actions_run=:run_cap,
                               max_actions_day=:day_cap,
                               max_bid_delta_pct=:step_pct,
                               daily_budget_rub=:daily_budget,
                               max_bid_rub=:account_bid_cap,
                               confirmation_text=:confirmation,
                               confirmation_version='kpi10_v4',
                               version=version+1
                         WHERE id=:id
                    """), {
                        "run_cap": desired_actions_run,
                        "day_cap": desired_actions_day,
                        "step_pct": account_max_step_pct,
                        "account_bid_cap": account_hard_bid_cap,
                        "daily_budget": round(explicit_daily, 2),
                        "confirmation": (
                            "Active paid BORIS tariff: full hourly AI marketer may raise or lower CPX bids "
                            "across the active portfolio to pursue configured KPI. Daily budget, max bid and "
                            "per-step guards remain mandatory; feed_factory bootstrap remains enabled."
                        ),
                        "id": m["id"],
                    })
                    db.execute(_text_mandate("""
                        UPDATE money_mandates SET status='revoked', revoked_at=now(),
                               confirmation_text='Superseded by canonical paid tariff KPI mandate.',
                               confirmation_version='singleton_v1', version=version+1
                         WHERE :acc=ANY(account_scope) AND source='paid_tariff_kpi'
                           AND status='active' AND revoked_at IS NULL AND id<>:id
                    """), {"acc": account_id, "id": m["id"]})
                    db.commit()
                    return {"status": "upgraded", "mandate_id": m["id"],
                            "version": int(m.get("version") or 0) + 1,
                            "active_items": active_count,
                            "max_actions_run": desired_actions_run,
                            "max_actions_day": desired_actions_day,
                            "allowed_operations": desired_operations}
                _dupes = db.execute(_text_mandate("""
                    UPDATE money_mandates SET status='revoked', revoked_at=now(),
                           confirmation_text='Superseded by canonical paid tariff KPI mandate.',
                           confirmation_version='singleton_v1', version=version+1
                     WHERE :acc=ANY(account_scope) AND source='paid_tariff_kpi'
                       AND status='active' AND revoked_at IS NULL AND id<>:id
                """), {"acc": account_id, "id": m["id"]})
                if int(_dupes.rowcount or 0) > 0: db.commit()
                return {"status": "reused", "mandate_id": m["id"], "version": m["version"],
                        "active_items": active_count,
                        "max_actions_run": int(m.get("max_actions_run") or 0),
                        "max_actions_day": int(m.get("max_actions_day") or 0),
                        "allowed_operations": list(m.get("allowed_operations") or [])}

        # Only the explicitly configured daily advertising budget may become
        # a money mandate. KPI target × red CPL is a feasibility calculation,
        # never an authorization to spend.
        plan_daily = explicit_daily
        # Global red line is authoritative: a newly created mandate must never
        # resurrect the old 100–500 ₽ ceiling. Account settings may only tighten it.
        hard_bid_cap = account_hard_bid_cap
        owner_id = int(acc.owner_user_id or 0) or None
        mandate_id = db.execute(_text_mandate("""
            INSERT INTO money_mandates
            (owner_kind,owner_id,account_scope,allowed_operations,daily_budget_rub,
             max_actions_run,max_actions_day,max_bid_delta_pct,min_bid_rub,max_bid_rub,
             reserve_rub,valid_from,valid_until,revoked_at,revoked_by,granted_by_user_id,
             granted_at,confirmation_text,confirmation_version,source,status,version)
            VALUES
            ('user',:owner_id,ARRAY[:acc]::text[],ARRAY['cpx.raise_bid','cpx.lower_bid']::text[],:daily_budget,
             :run_cap,:day_cap,:step_pct,NULL,:max_bid,NULL,now(),:valid_until,NULL,NULL,:owner_id,now(),
             :confirmation,'kpi10_v4','paid_tariff_kpi','active',1)
            RETURNING id
        """), {
            "owner_id": owner_id, "acc": account_id,
            "daily_budget": round(plan_daily, 2), "max_bid": round(hard_bid_cap, 2),
            "step_pct": account_max_step_pct,
            "run_cap": desired_actions_run, "day_cap": desired_actions_day,
            "valid_until": now + _td_mandate(days=365),
            "confirmation": (
                "Active paid BORIS tariff: full hourly AI marketer may raise or lower CPX bids across "
                "the active portfolio to pursue configured KPI. Daily budget, max bid and per-step "
                "guards remain mandatory; feed_factory bootstrap remains enabled."
            )
        }).scalar()
        db.commit()
        return {"status": "created", "mandate_id": int(mandate_id),
                "daily_budget_rub": round(plan_daily, 2), "max_bid_rub": round(hard_bid_cap, 2),
                "max_bid_delta_pct": account_max_step_pct, "max_actions_run": desired_actions_run,
                "max_actions_day": desired_actions_day, "active_items": active_count,
                "allowed_operations": desired_operations}
    finally:
        db.close()


def reconcile_paid_tariff_raise_mandates():
    """DB-only fleet reconcile for standing paid KPI money authority.

    PAID_TARIFF_MANDATE_HOURLY_RECONCILE_V1:
    Rollout preflight requires a raise-capable paid_tariff_kpi mandate, while
    the old code created it only inside downstream money functions that preflight
    could block before reaching. This fleet pass breaks that dependency cycle.
    It never calls Avito and never changes a bid. Each account still must prove
    active paid entitlement, authenticated owner budget, explicit target/red CPL
    and autonomous-mode switches inside ensure_paid_tariff_raise_mandate.
    """
    from app.db.session import SessionLocal
    from sqlalchemy import text as _t_paid_reconcile
    db = SessionLocal()
    try:
        rows = db.execute(_t_paid_reconcile("""
            SELECT DISTINCT account_id
              FROM storage
             WHERE key='kpi_settings'
               AND account_id IS NOT NULL
               AND account_id <> ''
               AND account_id NOT LIKE 'qa%'
               AND account_id NOT LIKE 'user:%'
             ORDER BY account_id
        """)).fetchall()
        accounts = [str(r[0]) for r in rows if r and r[0]]
    finally:
        db.close()

    results=[]; created=0; upgraded=0; reused=0; blocked=0; errors=0
    for account_id in accounts:
        try:
            res=ensure_paid_tariff_raise_mandate(account_id) or {}
            status=str(res.get("status") or "")
            if status=="created": created += 1
            elif status=="upgraded": upgraded += 1
            elif status=="reused": reused += 1
            elif status=="blocked": blocked += 1
            results.append({"account_id":account_id, **res})
        except Exception as exc:
            errors += 1
            results.append({
                "account_id":account_id,
                "status":"error",
                "error":f"{type(exc).__name__}: {str(exc)[:180]}",
            })
    return {
        "status":"ok" if errors==0 else "error",
        "checked":len(accounts),
        "created":created,
        "upgraded":upgraded,
        "reused":reused,
        "blocked":blocked,
        "errors":errors,
        "results":results,
    }


# NEW_ITEM_NO_PROMO_BOOTSTRAP_V2
# Uses the existing KPI tick + apply_one engine. No second scheduler/engine.
def _ensure_new_item_launch_mandate(account_id: str):
    """NEW_FEED_LAUNCH_EXPLICIT_BUDGET_V3.

    First CPX activation is a paid action. It requires an explicit positive
    daily budget, bid_autopilot, autonomous mode and the same hard cap / step
    policy as every later raise. Zero/unknown budget revokes any stale launch
    authority instead of creating a parallel spend lane.
    """
    from app.db.session import SessionLocal
    from app.models.account import Account
    from app.models.storage import Storage
    from app.api.billing import _load_billing
    from app.services.marketing_money_policy import effective_hard_bid_cap, GLOBAL_MAX_BID_DELTA_PCT
    from sqlalchemy import text as _t
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    import json
    db=SessionLocal()
    try:
        acc=db.query(Account).filter(Account.account_id==account_id).first()
        if not acc or not (acc.avito_client_id and acc.avito_client_secret):
            changed=db.execute(_t("""UPDATE money_mandates SET status='revoked',revoked_at=now(),revoked_by=NULL,
                                     confirmation_text='Real Avito credentials unavailable; stale new-feed launch authority revoked.',
                                     confirmation_version='launchcreds_v1',version=version+1
                                     WHERE :a=ANY(account_scope) AND source='new_feed_launch' AND status='active'
                                       AND revoked_at IS NULL AND 'cpx.raise_bid'=ANY(allowed_operations)"""),{'a':account_id})
            db.commit()
            return {"status":"blocked","reason":"no_real_avito_account","changed_avito":False,
                    "reconciled_mandates":int(changed.rowcount or 0)}
        billing=_load_billing(account_id,db=db) or {}
        # NEW_FEED_LAUNCH_PAID_PERIOD_GUARD_V1: first-activation authority is
        # money authority too. A historical tariff label or long-lived launch
        # mandate must never survive the account's actual paid_until clock.
        from app.services.control_plane_adapters_ext import marketing_service_entitlement
        _entitlement = marketing_service_entitlement(db, account_id)
        if _entitlement.get("state") != "active":
            db.execute(_t("""UPDATE money_mandates SET status='revoked',revoked_at=now(),
                             confirmation_text='BORIS marketer paid period inactive; stale new-feed launch authority revoked.',
                             confirmation_version='paidperiod_v1',version=version+1
                          WHERE :a=ANY(account_scope) AND source='new_feed_launch' AND status='active'
                            AND revoked_at IS NULL AND 'cpx.raise_bid'=ANY(allowed_operations)"""), {'a':account_id})
            db.commit()
            return {"status":"blocked","reason":"no_active_boris_marketer_entitlement",
                    "entitlement":_entitlement,"changed_avito":False}
        rowk=db.query(Storage).filter(Storage.account_id==account_id,Storage.key=='kpi_settings').order_by(Storage.id.desc()).first()
        rowa=db.query(Storage).filter(Storage.account_id==account_id,Storage.key=='autopilot_settings').order_by(Storage.id.desc()).first()
        try:kpi=json.loads(rowk.value or '{}') if rowk else {}
        except Exception:kpi={}
        try:ap=json.loads(rowa.value or '{}') if rowa else {}
        except Exception:ap={}
        if not bool(kpi.get('bid_autopilot')):
            changed=db.execute(_t("""UPDATE money_mandates SET status='revoked',revoked_at=now(),revoked_by=NULL,
                                     confirmation_text='Bid autopilot disabled; stale new-feed launch authority revoked.',
                                     confirmation_version='launchmode_v1',version=version+1
                                     WHERE :a=ANY(account_scope) AND source='new_feed_launch' AND status='active'
                                       AND revoked_at IS NULL AND 'cpx.raise_bid'=ANY(allowed_operations)"""),{'a':account_id})
            db.commit()
            return {"status":"blocked","reason":"bid_autopilot_disabled","changed_avito":False,
                    "reconciled_mandates":int(changed.rowcount or 0)}
        if str(ap.get('mode') or '') not in {'goal_auto','always_auto'}:
            changed=db.execute(_t("""UPDATE money_mandates SET status='revoked',revoked_at=now(),revoked_by=NULL,
                                     confirmation_text='Autonomous marketing mode disabled; stale new-feed launch authority revoked.',
                                     confirmation_version='launchmode_v1',version=version+1
                                     WHERE :a=ANY(account_scope) AND source='new_feed_launch' AND status='active'
                                       AND revoked_at IS NULL AND 'cpx.raise_bid'=ANY(allowed_operations)"""),{'a':account_id})
            db.commit()
            return {"status":"blocked","reason":"autonomous_marketing_mode_disabled","changed_avito":False,
                    "reconciled_mandates":int(changed.rowcount or 0)}
        try: budget=float(kpi.get('daily_budget_limit_rub') or 0)
        except Exception: budget=0.0
        if budget <= 0:
            changed=db.execute(_t("""UPDATE money_mandates SET status='revoked',revoked_at=now(),revoked_by=NULL,version=version+1
                                     WHERE :a=ANY(account_scope) AND source='new_feed_launch' AND status='active'
                                       AND revoked_at IS NULL AND 'cpx.raise_bid'=ANY(allowed_operations)"""),{'a':account_id})
            db.commit()
            return {"status":"blocked","reason":"no_explicit_daily_budget","changed_avito":False,
                    "reconciled_mandates":int(changed.rowcount or 0)}
        # MONEY_BUDGET_OWNER_PROVENANCE_GUARD_V1 applies to first CPX activation
        # too: launch authority cannot be created from a legacy/unattributed budget.
        _budget_auth=kpi.get('daily_budget_authorization') if isinstance(kpi.get('daily_budget_authorization'),dict) else {}
        _budget_proven=bool(
            str(_budget_auth.get('policy_version') or '')=='MONEY_BUDGET_OWNER_PROVENANCE_V1'
            and int(_budget_auth.get('authorized_by_user_id') or 0)>0
            and float(_budget_auth.get('daily_budget_limit_rub') or -1)==budget
            and str(_budget_auth.get('source') or '')=='authenticated_set_kpi_settings'
        )
        if not _budget_proven:
            changed=db.execute(_t("""UPDATE money_mandates SET status='revoked',revoked_at=now(),revoked_by=NULL,
                                     confirmation_text='Daily budget lacks authenticated owner provenance; launch authority revoked.',
                                     confirmation_version='moneyprov_v1',version=version+1
                                     WHERE :a=ANY(account_scope) AND source='new_feed_launch' AND status='active'
                                       AND revoked_at IS NULL AND 'cpx.raise_bid'=ANY(allowed_operations)"""),{'a':account_id})
            db.commit()
            return {"status":"blocked","reason":"daily_budget_owner_provenance_missing","changed_avito":False,
                    "daily_budget_limit_rub":budget,"reconciled_mandates":int(changed.rowcount or 0)}
        # NEW_FEED_LAUNCH_VS_RAMP_MANDATE_ISOLATION_V1: first activation is
        # always 1/run. Later low-view +10% ramp uses paid_tariff_kpi via
        # load_mandate(source='new_feed_hourly_ramp') and must never widen launch authority.
        cap=float(effective_hard_bid_cap(db,account_id)); step=int(GLOBAL_MAX_BID_DELTA_PCT); now=_dt.now(_tz.utc)
        txt=('First Feed Factory CPX activation is paid and shares the explicit daily budget, '
             'fresh spend guard, live balance guard, 10% step and absolute hard bid cap.')
        m=db.execute(_t("""SELECT id,version,daily_budget_rub,max_actions_run,max_actions_day,max_bid_delta_pct,max_bid_rub
                              FROM money_mandates WHERE :a=ANY(account_scope) AND source='new_feed_launch'
                               AND status='active' AND revoked_at IS NULL AND 'cpx.raise_bid'=ANY(allowed_operations)
                               AND (valid_until IS NULL OR valid_until>now()) ORDER BY id DESC LIMIT 1"""),{'a':account_id}).mappings().first()
        if m:
            needs=(float(m.get('daily_budget_rub') or 0)!=budget or int(m.get('max_actions_run') or 0)!=1
                   or int(m.get('max_actions_day') or 0)!=24 or int(m.get('max_bid_delta_pct') or 0)!=step
                   or float(m.get('max_bid_rub') or 0)!=cap)
            if needs:
                db.execute(_t("""UPDATE money_mandates SET daily_budget_rub=:b,max_actions_run=1,max_actions_day=24,
                                 max_bid_delta_pct=:s,max_bid_rub=:c,confirmation_text=:t,
                                 confirmation_version='nfbudv3',version=version+1 WHERE id=:id"""),
                           {'b':budget,'s':step,'c':cap,'t':txt,'id':m['id']})
                db.commit(); st='updated'; ver=int(m['version'] or 0)+1
            else: st='reused'; ver=m['version']
            return {"status":st,"mandate_id":m['id'],"version":ver,"launch_only":True,
                    "daily_budget_rub":budget,"max_actions_run":1,"max_actions_day":24,
                    "max_bid_delta_pct":step,"max_bid_rub":cap,"requires_explicit_budget":True}
        owner=int(acc.owner_user_id or 0) or None
        mid=db.execute(_t("""INSERT INTO money_mandates
            (owner_kind,owner_id,account_scope,allowed_operations,daily_budget_rub,max_actions_run,max_actions_day,
             max_bid_delta_pct,min_bid_rub,max_bid_rub,reserve_rub,valid_from,valid_until,revoked_at,revoked_by,
             granted_by_user_id,granted_at,confirmation_text,confirmation_version,source,status,version)
            VALUES('user',:o,ARRAY[:a]::text[],ARRAY['cpx.raise_bid']::text[],:b,1,24,:s,NULL,:c,NULL,
                   now(),:u,NULL,NULL,:o,now(),:t,'nfbudv3','new_feed_launch','active',1) RETURNING id"""),
            {'o':owner,'a':account_id,'b':budget,'s':step,'c':cap,'u':now+_td(days=365),'t':txt}).scalar()
        db.commit()
        return {"status":"created","mandate_id":int(mid),"version":1,"launch_only":True,
                "daily_budget_rub":budget,"max_actions_run":1,"max_actions_day":24,
                "max_bid_delta_pct":step,"max_bid_rub":cap,"requires_explicit_budget":True}
    finally:
        db.close()


def reconcile_existing_new_feed_launch_mandates():
    """Revalidate only already-existing launch money grants; never create one.

    LAUNCH_MANDATE_HOURLY_RECONCILE_V1: a launch grant can become stale after
    tariff, credentials, autopilot mode or owner-budget provenance changes.
    Hourly money control must revoke/update such grants even when no new feed is
    being launched, so a stale DB row cannot wait for a later execution path.
    """
    from app.db.session import SessionLocal
    from sqlalchemy import text as _t_reconcile_launch
    db=SessionLocal()
    try:
        rows=db.execute(_t_reconcile_launch("""
            SELECT DISTINCT unnest(account_scope) AS account_id
              FROM money_mandates
             WHERE source='new_feed_launch' AND status='active' AND revoked_at IS NULL
               AND 'cpx.raise_bid'=ANY(allowed_operations)
               AND (valid_until IS NULL OR valid_until>now())
             ORDER BY account_id
        """)).fetchall()
        accounts=[str(r[0]) for r in rows if r and r[0]]
    finally:
        db.close()
    results=[]; revoked=0; errors=0
    for account_id in accounts:
        try:
            res=_ensure_new_item_launch_mandate(account_id) or {}
            revoked += int(res.get('reconciled_mandates') or 0)
            results.append({'account_id':account_id,**res})
        except Exception as exc:
            errors += 1
            results.append({'account_id':account_id,'status':'error','error':f'{type(exc).__name__}: {str(exc)[:180]}'})
    return {'status':'ok' if errors==0 else 'error','checked':len(accounts),
            'revoked':revoked,'errors':errors,'results':results}


def bootstrap_new_no_promo(account_id: str, max_items: int = 1):
    """Promote every proven active Feed Factory ad that currently has no promotion.

    Scope is exact: CampaignItem.source=feed_factory + persisted numeric avito_item_id
    + current active daily_stats. No fuzzy matching and no unrelated live inventory.
    Every real change goes through apply_one + paid-tariff money mandate/autonomy guard.
    """
    from app.db.session import SessionLocal
    from app.api.avito import get_avito_token, _load_feed_items
    from app.models.campaign_item import CampaignItem
    from app.models.storage import Storage
    import re as _re_feed_boot
    from collections import defaultdict as _defaultdict_feed_boot

    mandate = _ensure_new_item_launch_mandate(account_id)
    if mandate.get("status") == "blocked":
        return {"status": "blocked", "reason": mandate.get("reason"),
                "mandate": mandate, "changed_avito": False}

    db = SessionLocal()
    try:
        today = marketing_today_iso()
        stats = _load_json(db, account_id, f"daily_stats:{today}") or {}
        active_ids = {
            int(it.get("id")) for it in (stats.get("items") or [])
            if it.get("status") == "active" and str(it.get("id") or "").isdigit()
        }
        # CANONICAL_FEED_BID_SCOPE_V1: a current canonical XML identity is
        # authoritative for bid management. Campaign source labels are provenance
        # only and must not exclude valid replacement/pilot/scale publications.
        # Exact canonical feed membership also prevents old provider-tail ads from
        # receiving money after they have been removed from the desired XML.
        current_feed_ids = {
            str(getattr(x, "id", "") or "").strip()
            for x in _load_feed_items(account_id)
            if str(getattr(x, "id", "") or "").strip()
        }
        canonical_rows = db.query(CampaignItem).filter(
            CampaignItem.account_id == account_id,
            CampaignItem.status == "published",
            CampaignItem.avito_item_id.isnot(None),
        ).all()
        feed_ids_set = {
            int(r.avito_item_id) for r in canonical_rows
            if str(r.avito_item_id or "").isdigit()
            and int(r.avito_item_id) in active_ids
            and str(getattr(r, "feed_identity", "") or "").strip() in current_feed_ids
            and str(getattr(r, "identity_status", "") or "") in {
                "published_identity_bound",
                "resolved_legacy_numeric_external_id",
            }
        }
        # FIRST_ACTIVATION_ONLY_V1: bootstrap is only for listings that BORIS has
        # never successfully raised before. A listing whose promotion was later
        # removed by the budget brake must NOT be silently re-enabled as "new".
        prior_applied = _load_json(db, account_id, "cpx_applied") or {}
        ever_raised = set()
        if isinstance(prior_applied, dict):
            for _k in prior_applied.keys():
                try:
                    _iid, _action, *_rest = str(_k).split(":")
                    if _action == "raise" and str(_iid).isdigit():
                        ever_raised.add(int(_iid))
                except Exception:
                    pass
        feed_ids = sorted(i for i in feed_ids_set if i not in ever_raised)
        already_activated = sorted(i for i in feed_ids_set if i in ever_raised)
        if not feed_ids:
            return {"status": "no_first_activation_candidates", "mandate": mandate,
                    "canonical_items": len(feed_ids_set), "already_activated": len(already_activated),
                    "changed_avito": False}

        # FIRST_BID_ACCOUNT_MEASUREMENT_BACKLOG_V1:
        # Starter bids create real daily measurement states. Never open a third
        # concurrent raise experiment and never spend Avito provider quota merely
        # to discover an action that the final money guard must reject.
        _raise_backlog = _active_raise_measurement_summary(db, account_id)
        if int(_raise_backlog.get("active_count") or 0) >= int(_raise_backlog.get("cap") or 2):
            return {
                "status": "measurement_wait",
                "reason": "account_measurement_backlog_wait",
                "waiting_measurements": int(_raise_backlog.get("active_count") or 0),
                "measurement_cap": int(_raise_backlog.get("cap") or 2),
                "next_action": "measurement_sweep_then_retry_automatically",
                "mandate": mandate,
                "changed_avito": False,
            }

        # All DB-derived selection data is materialized above. Token refresh and
        # CPX discovery are external Avito I/O and must never inherit this read
        # transaction. Close it before the first network boundary. The Session
        # object may be reused later: SQLAlchemy will lazily acquire a fresh
        # short connection for cooldown persistence.
        db.close()

        # FIRST_BID_SHARED_ACCOUNT_THROTTLE_PRECHECK_V1: promotion discovery is
        # provider I/O too. A Retry-After observed by any trusted stage is binding
        # before first-bid discovery, not only later inside apply_one().
        from app.services.avito_account_throttle import account_throttle_remaining as _first_bid_throttle_remaining, record_account_throttle as _first_bid_record_throttle
        _first_bid_retry = _first_bid_throttle_remaining(account_id)
        if _first_bid_retry > 0:
            return {"status": "external_wait", "reason": "avito_account_throttled",
                    "retry_after_seconds": int(_first_bid_retry), "mandate": mandate,
                    "changed_avito": False}

        tok_data = get_avito_token(account_id)
        tok = tok_data.get("access_token") if isinstance(tok_data, dict) else tok_data
        if not tok:
            return {"status": "blocked", "reason": "no_avito_token", "mandate": mandate,
                    "changed_avito": False}

        promo_map = {}
        try:
            for start in range(0, len(feed_ids), 200):
                chunk = feed_ids[start:start + 200]
                rr = httpx.post(
                    "https://api.avito.ru/cpxpromo/1/getPromotionsByItemIds",
                    headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                    json={"itemIDs": chunk}, timeout=30)
                if rr.status_code == 429:
                    # FIRST_BID_SHARED_ACCOUNT_THROTTLE_RECORD_V1: publish this
                    # confirmed provider backpressure so later CPX/stats stages
                    # cannot pressure the same tenant during Retry-After.
                    _retry_after_seconds = None
                    try:
                        _raw_ra = str(rr.headers.get("Retry-After") or "").strip()
                        if _raw_ra:
                            try:
                                _retry_after_seconds = max(0, int(float(_raw_ra)))
                            except Exception:
                                from email.utils import parsedate_to_datetime as _parse_fb_ra
                                from datetime import datetime as _fb_dt, timezone as _fb_tz
                                _fb_when = _parse_fb_ra(_raw_ra)
                                if _fb_when.tzinfo is None:
                                    _fb_when = _fb_when.replace(tzinfo=_fb_tz.utc)
                                _retry_after_seconds = max(0, int((_fb_when.astimezone(_fb_tz.utc) - _fb_dt.now(_fb_tz.utc)).total_seconds()))
                    except Exception:
                        _retry_after_seconds = None
                    _retry_after_seconds = _first_bid_record_throttle(
                        account_id, _retry_after_seconds or 30, source="first_bid_promotions_429"
                    )
                    return {"status": "external_wait", "reason": "avito_account_throttled",
                            "retry_after_seconds": int(_retry_after_seconds),
                            "mandate": mandate, "changed_avito": False}
                if rr.status_code != 200:
                    return {"status": "blocked", "reason": f"promotion_http_{rr.status_code}",
                            "mandate": mandate, "changed_avito": False}
                for x in (rr.json().get("items") or []):
                    if x.get("itemID") is not None:
                        promo_map[int(x.get("itemID"))] = x
        except Exception as exc:
            return {"status": "external_wait", "reason": str(exc)[:120],
                    "mandate": mandate, "changed_avito": False}

        unpromoted = []
        for iid in feed_ids:
            p = promo_map.get(iid) or {}
            if not (p.get("manualPromotion") or p.get("autoPromotion")):
                unpromoted.append(iid)
        if not unpromoted:
            return {"status": "all_feed_items_promoted", "feed_items": len(feed_ids),
                    "promoted": len(feed_ids), "mandate": mandate, "changed_avito": False}

        # Do not burn every hourly cycle on ads for which Avito currently
        # exposes no CPX promotion capability. Recheck after 24h so eligibility
        # changes are eventually picked up without starving the rest of KPI work.
        from datetime import datetime as _dt_promo_cool, timezone as _tz_promo_cool, timedelta as _td_promo_cool
        cooldown_key = "cpx_feed_promotion_unavailable"
        cooldown = _load_json(db, account_id, cooldown_key) or {}
        unavailable_map = cooldown.get("items") if isinstance(cooldown, dict) else {}
        unavailable_map = unavailable_map if isinstance(unavailable_map, dict) else {}
        now_cool = _dt_promo_cool.now(_tz_promo_cool.utc)
        eligible_unpromoted, cooling_down = [], []
        for iid in unpromoted:
            raw_ts = unavailable_map.get(str(iid))
            in_cooldown = False
            if raw_ts:
                try:
                    ts = _dt_promo_cool.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
                    in_cooldown = (now_cool - ts) < _td_promo_cool(hours=24)
                except Exception:
                    in_cooldown = False
            if in_cooldown:
                cooling_down.append(iid)
            else:
                eligible_unpromoted.append(iid)

        # Reading cooldown above lazily re-acquired the outer Session connection.
        # Release it again before apply_one(), which owns its own receipt/mandate
        # sessions. Without this second close the 1+1 daemon pool deadlocks on
        # _cpx_receipt_get under normal hourly production load.
        db.close()

        # External discovery is complete. apply_one() owns its own short sessions;
        # the Session object may reconnect only later for cooldown persistence.
        applied, blocked, failed = [], [], []
        # FIRST_BID_BOUNDED_COHORT_V2: the first starter bid is baseline
        # reach initialization, not a content/bid hypothesis. A trusted KPI cycle
        # may initialize a SMALL cohort so newly published ads do not sit at zero
        # reach for many hours. Each item still goes through apply_one(), therefore
        # daily budget, hard bid cap, Avito maxBid, balance, throttle and receipt
        # guards remain authoritative before every provider write.
        # FIRST_BID_EXACTLY_ONE_PER_CALL_V1: preserve causal launch isolation.
        limit = min(len(eligible_unpromoted), 1)
        import time as _time_feed20
        from datetime import datetime as _dt_feed20_run, timezone as _tz_feed20_run
        _run_prefix = "f20v3_" + _dt_feed20_run.now(_tz_feed20_run.utc).strftime("%Y%m%dT%H%M%S%f")
        for iid in eligible_unpromoted[:limit]:
            result = apply_one(ApplyOneBody(
                account_id=account_id,
                item_id=iid,
                action="raise",
                max_bid_delta_pct=NEW_ITEM_NO_PROMO_MIN_BID_MARKUP_PCT,
                actor_type="boris_auto",
                source="feed20",
                trigger="feed",
                request_id=f"{_run_prefix}:{iid}",
            )) or {}
            # CPX_FIRST_BID_THROTTLE_NO_RETRY_V1: provider 429 is authoritative
            # for the account in this cycle. Do not retry the same first-bid
            # activation before Retry-After; the next trusted hourly run owns it.
            if result.get("status") == "ok":
                applied.append({"item_id": iid, "new_bid_rub": result.get("new_bid_rub")})
            elif result.get("status") == "skipped":
                blocked.append({"item_id": iid, "status": "skipped",
                                "reason": result.get("reason_code") or result.get("blocked_by")})
            elif result.get("status") == "blocked":
                blocked.append({"item_id": iid, "status": "blocked",
                                "reason": result.get("reason_code") or result.get("blocked_by") or result.get("message")})
            else:
                reason = result.get("message") or result.get("reason") or result.get("code")
                if "Не получается получить продвижение" in str(reason or ""):
                    unavailable_map[str(iid)] = now_cool.isoformat()
                    blocked.append({"item_id": iid, "status": "promotion_unavailable",
                                    "reason": "avito_promotion_unavailable_24h_recheck"})
                else:
                    failed.append({"item_id": iid, "status": result.get("status") or "error",
                                   "reason": reason})
            # Keep bulk first-activation below Avito's burst limits.
            _time_feed20.sleep(1.0)

        if unavailable_map:
            _save_json(db, account_id, cooldown_key, {
                "items": unavailable_map,
                "updated_at": now_cool.isoformat(),
                "recheck_after_hours": 24,
            })

        # FIRST_BID_ITEM_BLOCK_ISOLATION_V1: an item-level Avito capability,
        # hard-cap, cooldown or measurement block is not an account incident.
        # Continue the rest of the marketer cycle; only real execution failures
        # are account-level errors. Standing-mandate failures still return the
        # earlier top-level `blocked` before item iteration.
        return {
            "status": "applied" if applied else ("error" if failed else "partial" if blocked else "ok"),
            "feed_items": len(feed_ids), "unpromoted_before": len(unpromoted),
            "eligible_unpromoted": len(eligible_unpromoted), "cooling_down": cooling_down,
            "attempted": limit, "applied": applied, "blocked": blocked, "failed": failed,
            "changed_avito": bool(applied), "mandate": mandate,
        }
    finally:
        db.close()


# FIRST_BID_BATCH_DISABLED_CAUSAL_V2: compatibility wrapper cannot widen one-per-call authority.
def bootstrap_new_no_promo_batch(account_id: str, max_items: int = 5):
    """Initialize a bounded cohort of newly published ads with starter CPX bids.

    This wrapper never bypasses apply_one() or money guards. The effective cohort
    is capped at five per trusted KPI cycle; subsequent optimization remains
    causal/gradual and is measured separately.
    """
    requested = max(1, min(int(max_items or 1), 5))
    result = dict(bootstrap_new_no_promo(account_id, max_items=requested) or {})
    result["batch_requested"] = int(max_items or 0)
    result["batch_effective_limit"] = requested
    result["causal_guard"] = "starter_baseline_cohort_then_measure"
    return result


# NEW_FEED_HOURLY_LOW_VIEWS_RAMP_V3
NEW_FEED_RAMP_WINDOW_HOURS = 72
NEW_FEED_LOW_VIEWS_PER_HOUR = 3
NEW_FEED_HOURLY_RAISE_PCT = 10


def _new_feed_ramp_pct(views_per_hour: float) -> int:
    """Launch ramp bounded by the global 10% per-step money ceiling."""
    if views_per_hour < 3:
        return 10
    return 0


def hourly_new_feed_low_views_ramp(account_id: str, max_items: int = 200):
    """Hourly +10% ramp for new proven Feed Factory ads with low view velocity.

    First 72h after CampaignItem creation are the launch window. On first
    observation, current daily views are used as the baseline signal: <2 views
    is low and may receive a +10% step immediately. Later observations require
    at least ~45 minutes since the previous check and use hourly delta views.
    Each target is account-scoped, numeric Avito identity only, and every change
    goes through existing apply_one + money/autonomy guards.
    """
    from app.db.session import SessionLocal
    from app.models.campaign_item import CampaignItem
    from app.api.avito import _load_feed_items
    from datetime import datetime as _dt_ramp, timezone as _tz_ramp, timedelta as _td_ramp
    import time as _time_ramp

    mandate = _ensure_new_item_launch_mandate(account_id)
    if mandate.get("status") == "blocked":
        return {"status": "blocked", "reason": mandate.get("reason"), "changed_avito": False}

    db = SessionLocal()
    try:
        today = marketing_today_iso()
        stats = _load_json(db, account_id, f"daily_stats:{today}") or {}
        # NEW_FEED_RAMP_FRESH_SIGNAL_V4: hourly launch growth is still a money
        # decision. Retained/stale counters are useful for reporting but cannot
        # authorize another +10% step.
        from app.services.marketing_signal_guard import money_stats_snapshot_eligible as _nf_stats_money_ok
        _nf_ok, _nf_evidence = _nf_stats_money_ok(db, account_id, max_age_seconds=900)
        if not _nf_ok:
            return {"status":"blocked","reason":"daily_views_signal_stale_or_incomplete",
                    "changed_avito":False,"money_stats":_nf_evidence}
        stats_by_id = {
            int(it.get("id")): it for it in (stats.get("items") or [])
            if it.get("status") == "active" and str(it.get("id") or "").isdigit()
        }
        now = _dt_ramp.now(_tz_ramp.utc)
        cutoff = now - _td_ramp(hours=NEW_FEED_RAMP_WINDOW_HOURS)
        current_feed_ids = {
            str(getattr(x, "id", "") or "").strip()
            for x in _load_feed_items(account_id)
            if str(getattr(x, "id", "") or "").strip()
        }
        rows = db.query(CampaignItem).filter(
            CampaignItem.account_id == account_id,
            CampaignItem.status == "published",
            CampaignItem.avito_item_id.isnot(None),
            CampaignItem.created_at >= cutoff,
        ).all()
        targets = []
        for row in rows:
            if not str(row.avito_item_id or "").isdigit():
                continue
            if str(getattr(row, "feed_identity", "") or "").strip() not in current_feed_ids:
                continue
            if str(getattr(row, "identity_status", "") or "") not in {
                "published_identity_bound",
                "resolved_legacy_numeric_external_id",
            }:
                continue
            iid = int(row.avito_item_id)
            if iid not in stats_by_id:
                continue
            targets.append((iid, row.created_at, stats_by_id[iid]))
        if not targets:
            return {"status": "no_new_feed_items", "changed_avito": False, "window_hours": NEW_FEED_RAMP_WINDOW_HOURS}

        # NEW_FEED_PENDING_ITEM_SKIP_V7: unfinished measurement blocks only the
        # same item, not the whole account. This preserves causal isolation per
        # ad while allowing the bounded hourly batch to continue through other
        # low-view new ads under the existing run/day/budget money guards.
        from app.models.storage import Storage as _NFMeasureStorage
        _target_ids = {int(x[0]) for x in targets}
        _pending_new_feed = set()
        for _mr in db.query(_NFMeasureStorage).filter(
            _NFMeasureStorage.account_id == account_id,
            _NFMeasureStorage.key.like("cpx_measure:%"),
        ).all():
            try:
                _ms = json.loads(_mr.value or "{}")
                _mi = int(_ms.get("item_id") or 0)
            except Exception:
                continue
            if _mi in _target_ids and str(_ms.get("status") or "") == "waiting_measurement":
                _pending_new_feed.add(_mi)

        state_key = "cpx_new_feed_hourly_ramp"
        state = _load_json(db, account_id, state_key) or {}
        item_state = state.get("items") if isinstance(state, dict) else {}
        item_state = item_state if isinstance(item_state, dict) else {}

        due, healthy, too_soon = [], [], []
        for iid, created_at, it in targets:
            if iid in _pending_new_feed:
                continue
            cur_views = int(it.get("views") or 0)
            prev = item_state.get(str(iid)) or {}
            prev_views = prev.get("views")
            last_checked_raw = prev.get("checked_at")
            elapsed_hours = None
            if last_checked_raw:
                try:
                    last_checked = _dt_ramp.fromisoformat(str(last_checked_raw).replace("Z", "+00:00"))
                    elapsed_hours = max(0.0, (now - last_checked).total_seconds() / 3600.0)
                except Exception:
                    elapsed_hours = None

            if prev_views is None:
                # Existing new ads are actionable immediately when current live
                # signal is already clearly low; this bootstraps the hourly loop.
                delta_views = cur_views
                views_per_hour = float(cur_views)
                ramp_pct = _new_feed_ramp_pct(views_per_hour)
                is_low = ramp_pct > 0
                basis = "initial_current_views"
            else:
                if elapsed_hours is not None and elapsed_hours < 0.75:
                    too_soon.append(iid)
                    continue
                # At midnight daily counters reset; treat reset value as this
                # hour's observed views instead of producing a negative delta.
                delta_views = cur_views - int(prev_views)
                if delta_views < 0:
                    delta_views = cur_views
                observed_hours = max(1.0, elapsed_hours or 1.0)
                views_per_hour = float(delta_views) / observed_hours
                ramp_pct = _new_feed_ramp_pct(views_per_hour)
                is_low = ramp_pct > 0
                basis = "hourly_delta"

            # NEW_FEED_RAMP_ATTEMPTED_STATE_ONLY_V1: low-view candidates are
            # not marked checked until they are actually attempted by this bounded
            # batch. Otherwise the untouched tail is artificially postponed 45m.
            if is_low:
                due.append({"item_id": iid, "views": cur_views, "delta_views": delta_views,
                            "views_per_hour": round(float(views_per_hour), 3),
                            "ramp_pct": int(ramp_pct), "basis": basis,
                            "created_at": created_at.isoformat() if created_at else None})
            else:
                item_state[str(iid)] = {
                    "views": cur_views,
                    "checked_at": now.isoformat(),
                    "created_at": created_at.isoformat() if created_at else None,
                    "last_delta_views": delta_views,
                    "basis": basis,
                }
                healthy.append({"item_id": iid, "views": cur_views, "delta_views": delta_views,
                                "views_per_hour": round(float(views_per_hour), 3)})

        # Selection/state is fully materialized; do not hold the outer session
        # while apply_one() opens its own guard sessions. Session.close() is
        # reusable, so _save_json below can transparently acquire a fresh slot.
        db.close()

        applied, blocked, failed = [], [], []
        for target in due[:max(1, int(max_items or 1))]:
            iid = target["item_id"]
            step_pct = int(target.get("ramp_pct") or NEW_FEED_HOURLY_RAISE_PCT)
            result = apply_one(ApplyOneBody(
                account_id=account_id,
                item_id=iid,
                action="raise",
                max_bid_delta_pct=step_pct,
                actor_type="boris_auto",
                source="new_feed_hourly_ramp",
                trigger="hourly_low_views",
                request_id=f"nf10:{now.strftime('%Y%m%d%H')}:{iid}",
            )) or {}
            # CPX_ACCOUNT_THROTTLE_STOP_V1: confirmed getBids 429 is not an
            # item retry signal. Respect Retry-After by stopping all remaining
            # CPX pressure for this account in the current trusted cycle.
            if int(result.get("code") or 0) == 429:
                blocked.append({**target, "result_status": result.get("status"),
                                "reason": result.get("reason_code") or "avito_account_throttled",
                                "retry_after_seconds": result.get("retry_after_seconds")})
                break
            item_state[str(iid)] = {
                "views": int(target.get("views") or 0),
                "checked_at": now.isoformat(),
                "created_at": target.get("created_at"),
                "last_delta_views": int(target.get("delta_views") or 0),
                "basis": target.get("basis"),
            }
            record = {**target, "result_status": result.get("status")}
            if result.get("status") == "ok":
                record.update({"old_bid_rub": result.get("old_bid_rub"), "new_bid_rub": result.get("new_bid_rub")})
                applied.append(record)
                _time_ramp.sleep(1.0)
            elif result.get("status") in ("blocked", "skipped"):
                record["reason"] = result.get("reason_code") or result.get("blocked_by") or result.get("message") or result.get("reason")
                blocked.append(record)
            else:
                record["reason"] = result.get("message") or result.get("reason") or result.get("code")
                failed.append(record)
                # Transient failures must remain due for the next retry rather
                # than being marked as successfully observed for this hour.
                item_state.setdefault(str(iid), {})["checked_at"] = (now - _td_ramp(hours=1)).isoformat()

        _save_json(db, account_id, state_key, {
            "items": item_state,
            "updated_at": now.isoformat(),
            "window_hours": NEW_FEED_RAMP_WINDOW_HOURS,
            "low_views_per_hour": NEW_FEED_LOW_VIEWS_PER_HOUR,
            "raise_pct": NEW_FEED_HOURLY_RAISE_PCT,
        })
        return {
            "status": "applied" if applied else ("blocked" if blocked and not failed else "error" if failed else "ok"),
            "new_feed_items": len(targets), "due_low_views": len(due), "healthy": len(healthy),
            "too_soon": len(too_soon), "applied": applied, "blocked": blocked, "failed": failed,
            "changed_avito": bool(applied), "raise_pct": NEW_FEED_HOURLY_RAISE_PCT,
            "threshold_views_per_hour": NEW_FEED_LOW_VIEWS_PER_HOUR,
            "window_hours": NEW_FEED_RAMP_WINDOW_HOURS,
            "mandate": mandate,
        }
    finally:
        db.close()


# ACCOUNT_LOW_VIEWS_RAMP_V1
# Owner rule: on active autonomous marketer accounts, do not leave listings at
# 1-9 views/day. Raise CPX gradually until daily views reach the 10/20/30 zone,
# while preserving the daily spend guard, positive-balance guard, Avito maxBid,
# account hard cap and global +10% per-step ceiling.
# ACCOUNT_LOW_VIEWS_BLOCKED_ROTATION_V1
def _low_views_retry_minutes(reason: str | None) -> int:
    r = str(reason or "").strip().lower()
    if r == "cpx_promotion_unavailable":
        # ACCOUNT_LOW_VIEWS_CAPABILITY_ROTATION_V3: apply_one persists a 24h
        # provider capability cooldown; keep local state aligned with it.
        return 1440
    if r in {"measure_effect", "measurement_lock", "measure_locked"}:
        return 60
    if any(x in r for x in ("budget", "spend", "balance", "daily_limit")):
        return 60
    return 30


# ACCOUNT_LOW_VIEWS_PROVIDER_ALIAS_HOURLY_V1 legacy contract marker; DAILY_BID_LEARNING_V1 now safely widens persistent low-view alias to one day.
# Legacy QA token: {"account_low_views_ramp"} ; execution below uses a 24h causal-learning alias window.
# ACCOUNT_LOW_VIEWS_SHORT_MEASURE_V2 legacy contract marker; DAILY_LOW_VIEWS_ACCOUNT_EVIDENCE_V1 uses a complete-day evidence window.
# ACCOUNT_LOW_VIEWS_RUN_ID_SCOPE_V1
# One low-view invocation gets its own account/time run prefix; autonomy run caps
# therefore apply to this invocation instead of all historical lv30 actions.
def hourly_account_low_views_ramp(account_id: str, max_items: int = 3):
    # ACCOUNT_LOW_VIEWS_DIRECT_LANE_GUARD_V3: this legacy broad portfolio
    # money function is fail-closed unless an explicitly controlled maintenance
    # lane opts in. Normal production growth uses staged_rollout instead.
    import os as _os_alv_lane
    _lane = str(_os_alv_lane.environ.get("BORIS_MARKETER_MONEY_LANE") or "").strip()
    # ACCOUNT_LOW_VIEWS_SERVICE_CALLER_GUARD_V2: environment is intent, not authority.
    # Authorize by this process's own systemd cgroup. Legitimate stage wrappers
    # stay inside boris-ai-marketer.service; remote/ownerless processes do not.
    try:
        from pathlib import Path as _Path_alv
        _caller_cgroup=_Path_alv("/proc/self/cgroup").read_text(errors="ignore")
    except Exception:
        _caller_cgroup=""
    if _lane != "controlled_lowviews_v2" or "boris-ai-marketer.service" not in _caller_cgroup:
        return {"status":"blocked","reason":"untrusted_money_lane",
                "required_lane":"controlled_lowviews_v2","required_caller":"boris-ai-marketer.service",
                "changed_avito":False}
    from app.db.session import SessionLocal
    from datetime import datetime as _dt_alv, timezone as _tz_alv, timedelta as _td_alv
    from app.services.marketing_money_policy import effective_hard_bid_cap

    mandate = ensure_paid_tariff_raise_mandate(account_id)
    if mandate.get("status") == "blocked":
        return {"status":"blocked","reason":mandate.get("reason"),"changed_avito":False}

    db = SessionLocal()
    try:
        today = marketing_today_iso()
        stats = _load_json(db, account_id, f"daily_stats:{today}") or {}
        # ACCOUNT_LOW_VIEWS_FRESH_SIGNAL_V2 / ACCOUNT_LOW_VIEWS_FRESH_SIGNAL_V3: candidate selection itself must use
        # the same <=15m complete stats contract as the final money boundary.
        # A fresh spending timestamp is not a substitute for fresh inventory;
        # preserved reporting snapshots remain visible but cannot trigger even
        # a getBids probe for a raise decision.
        from app.services.marketing_signal_guard import money_stats_snapshot_eligible as _stats_money_ok_alv
        _stats_ok, _stats_evidence = _stats_money_ok_alv(db, account_id, max_age_seconds=900)
        if not _stats_ok:
            return {"status":"blocked","reason":"daily_views_signal_stale_or_incomplete",
                    "changed_avito":False,"money_stats":_stats_evidence}
        items = [it for it in (stats.get("items") or [])
                 if it.get("status") == "active" and str(it.get("id") or "").isdigit()]
        if not items:
            return {"status":"no_active_items","changed_avito":False}

        cap = float(effective_hard_bid_cap(db, account_id))
        state_key = "cpx_account_low_views_ramp"
        state = _load_json(db, account_id, state_key) or {}
        item_state = state.get("items") if isinstance(state,dict) else {}
        item_state = item_state if isinstance(item_state,dict) else {}
        now = _dt_alv.now(_tz_alv.utc)

        # ACCOUNT_LOW_VIEWS_ACCOUNT_SIGNAL_COOLDOWN_V1: when the whole account
        # still has near-zero reach after a recent successful low-view mutation,
        # do not rotate money to different listings. Wait for account-level
        # evidence instead of buying a new hypothesis every scheduler tick.
        # ACCOUNT_LOW_VIEWS_CURRENT_DAY_SIGNAL_ONLY_V1:
        # Do not use prior-day Avito counters as current-day reach evidence.
        _account_item_signal_current = (
            str(stats.get("stats_date") or "") == marketing_today_iso()
        )
        _account_views_today=(
            sum(int(_it.get("views") or 0) for _it in items)
            if _account_item_signal_current else 0
        )
        _account_contacts_today=(
            sum(int(_it.get("contacts") or 0) for _it in items)
            if _account_item_signal_current else 0
        )
        _last_account_apply=None
        for _entry in item_state.values():
            if not isinstance(_entry,dict) or _entry.get("status")!="applied":
                continue
            _raw=_entry.get("checked_at")
            if not _raw:
                continue
            try:
                _ts=_dt_alv.fromisoformat(str(_raw).replace("Z","+00:00"))
                if _ts.tzinfo is None:
                    _ts=_ts.replace(tzinfo=_tz_alv.utc)
                if _last_account_apply is None or _ts>_last_account_apply:
                    _last_account_apply=_ts
            except Exception:
                pass
        _account_measure_minutes=1440  # DAILY_LOW_VIEWS_ACCOUNT_EVIDENCE_V1
        if (_account_views_today <= 2 and _account_contacts_today == 0 and
                _last_account_apply is not None):
            _elapsed=(now-_last_account_apply).total_seconds()/60.0
            if _elapsed < _account_measure_minutes:
                return {"status":"blocked","reason":"account_no_signal_measurement_wait",
                        "changed_avito":False,"active_items":len(items),
                        "account_views_today":_account_views_today,
                        "account_contacts_today":_account_contacts_today,
                        "last_account_apply_at":_last_account_apply.isoformat(),
                        "retry_after_minutes":max(1,int(_account_measure_minutes-_elapsed)),
                        "measurement_window_minutes":_account_measure_minutes,
                        "hard_max_bid_rub":cap,"mandate":mandate}

        # ACCOUNT_LOW_VIEWS_CAPABILITY_ROTATION_V2:
        # ACCOUNT_LOW_VIEWS_CAPABILITY_ROTATION_V3: honor provider 24h CPX
        # capability cooldown before candidate selection, so known-403 items do
        # not waste staged slots while other low-view listings remain eligible.
        capability_unavailable=set()
        try:
            _cd=_load_json(db, account_id, "cpx_item_capability_cooldown") or {}
            _cd_items=_cd.get("items") if isinstance(_cd,dict) else {}
            for _iid,_raw in (_cd_items.items() if isinstance(_cd_items,dict) else []):
                try:
                    _ts=_dt_alv.fromisoformat(str(_raw).replace("Z","+00:00"))
                    if _ts.tzinfo is None: _ts=_ts.replace(tzinfo=_tz_alv.utc)
                    if (now-_ts).total_seconds() < 24*3600:
                        capability_unavailable.add(str(_iid))
                except Exception:
                    pass
        except Exception:
            capability_unavailable=set()

        due=[]; healthy=[]; too_soon=[]; capability_skipped=[]
        for it in items:
            iid=int(it.get("id")); views=int(it.get("views") or 0)
            if views >= 30:
                healthy.append({"item_id":iid,"views_today":views})
                continue
            if str(iid) in capability_unavailable:
                capability_skipped.append(iid)
                continue
            prev=item_state.get(str(iid)) or {}
            last_raw=prev.get("checked_at")
            if last_raw:
                try:
                    last=_dt_alv.fromisoformat(str(last_raw).replace('Z','+00:00'))
                    retry_minutes=max(1440,int(prev.get("retry_after_minutes") or 1440))
                    if now-last < _td_alv(minutes=retry_minutes):
                        too_soon.append(iid); continue
                except Exception:
                    pass
            # Every low-view band may receive at most one +10% guarded step
            # per full daily learning cycle. Bands remain in evidence so reporting
            # can explain whether BORIS is rescuing <10, building 10->20 or 20->30.
            band = "below_10" if views < 10 else "below_20" if views < 20 else "below_30"
            due.append({"item_id":iid,"views_today":views,"target_views_day":30,
                        "band":band,"ramp_pct":10})

        # NATIVE_PROVIDER_PROBE_FIRST_BID_PRIORITY_V1: when BORIS already has
        # fresh advisor evidence that a native zero-CPX listing produced contacts,
        # try that exact provider-probe candidate before blind zero-view items.
        # This changes ranking only; apply_one still requires trusted cgroup,
        # fresh advice, exact getBids, one/day cap and all money guards.
        _native_first_priority_ids=set()
        try:
            _adv_now=_load_json(db, account_id, "cpx_advice") or {}
            _adv_raises=((_adv_now.get("recommendations") or {}).get("raise") or [])
            for _rec in _adv_raises if isinstance(_adv_raises,list) else []:
                if not isinstance(_rec,dict):
                    continue
                try: _rid=int(_rec.get("id") or _rec.get("item_id") or 0)
                except Exception: _rid=0
                if (_rid
                        and str(_rec.get("write_capability") or "")=="provider_probe_money"
                        and str(_rec.get("reason_code") or "")=="has_contacts"
                        and int(_rec.get("contacts_7d") or 0)>0
                        and not bool(_rec.get("promotion_active"))
                        and str(_rec.get("boris_measure_status") or "")!="waiting_measurement"):
                    _native_first_priority_ids.add(_rid)
        except Exception:
            _native_first_priority_ids=set()

        # Proven native contact candidates first, then ordinary lowest-view ads.
        due.sort(key=lambda x:(
            0 if int(x["item_id"]) in _native_first_priority_ids else 1,
            x["views_today"], x["item_id"]
        ))
        db.close()

        applied=[]; blocked=[]; failed=[]
        for target in due[:max(1,int(max_items or 1))]:
            iid=target["item_id"]
            result=apply_one(ApplyOneBody(
                account_id=account_id,item_id=iid,action="raise",
                max_bid_delta_pct=10,actor_type="boris_auto",
                source="account_low_views_ramp",trigger="daily_views_below_30",
                request_id=f"lv30_{account_id}_{now.strftime('%Y%m%d')}_daily:{iid}",
            )) or {}
            if int(result.get("code") or 0) == 429:
                blocked.append({**target,"result_status":result.get("status"),
                                "reason":result.get("reason_code") or "avito_account_throttled",
                                "retry_after_seconds":result.get("retry_after_seconds")})
                # CPX_LOWVIEWS_ACCOUNT_THROTTLE_STOP_V1: one confirmed account
                # throttle ends all remaining CPX candidates for this cycle.
                break
            rec={**target,"result_status":result.get("status")}
            if result.get("status") == "ok":
                rec.update({"old_bid_rub":result.get("old_bid_rub"),"new_bid_rub":result.get("new_bid_rub")})
                applied.append(rec)
                item_state[str(iid)]={"checked_at":now.isoformat(),"views_today":target["views_today"],"band":target["band"],
                                     "status":"applied","retry_after_minutes":1440}
            elif result.get("status") in ("blocked","skipped"):
                rec["reason"]=result.get("reason_code") or result.get("blocked_by") or result.get("reason") or result.get("message")
                blocked.append(rec)
                # Blocked candidates also enter a reason-aware retry window. Without
                # this, the same first 3 permanently blocked ads starve the rest of
                # a large portfolio on every low-views cycle. This is a rotation
                # cooldown only; it never converts a blocked money action to success.
                _retry_minutes=_low_views_retry_minutes(rec.get("reason"))
                item_state[str(iid)]={"checked_at":now.isoformat(),"views_today":target["views_today"],"band":target["band"],
                                     "status":"blocked","reason":rec.get("reason"),
                                     "retry_after_minutes":_retry_minutes}
                rec["retry_after_minutes"]=_retry_minutes
            else:
                rec["reason"]=result.get("message") or result.get("reason") or result.get("code")
                failed.append(rec)

        db = SessionLocal()
        _save_json(db,account_id,state_key,{"items":item_state,"updated_at":now.isoformat(),
                   "target_views_day":30,"bands":[10,20,30],"raise_pct":10,
                   "hard_max_bid_rub":cap})
        return {"status":"applied" if applied else ("blocked" if blocked else "error" if failed else "ok"),
                "active_items":len(items),"due_low_views":len(due),"healthy":len(healthy),
                "too_soon":len(too_soon),"capability_skipped":len(capability_skipped),
                "applied":applied,"blocked":blocked,"failed":failed,
                "changed_avito":bool(applied),"target_views_day":30,"bands":[10,20,30],
                "raise_pct":10,"hard_max_bid_rub":cap,"mandate":mandate}
    finally:
        db.close()


# KPI_GAP_BOOST_V1
# One reversible CPX step when the hourly KPI cycle is behind plan but its
# primary content/state-machine action cannot make progress in this tick.
def hourly_kpi_gap_boost(account_id: str):
    import json as _json_gap
    import re as _re_gap
    from datetime import datetime as _dt_gap, timezone as _tz_gap
    from app.db.session import SessionLocal
    from app.models.storage import Storage

    mandate = ensure_paid_tariff_raise_mandate(account_id)
    if mandate.get("status") == "blocked":
        return {"status": "blocked", "reason": mandate.get("reason"), "changed_avito": False}

    # KPI_GAP_TARGET_BUDGET_NOT_WORST_CASE_V1:
    # The KPI target and red CPL define business success/failure, while the
    # explicit daily budget is the hard spend ceiling. target * max_cpl is not
    # a required funding envelope: observed CPL may be far below the red line.
    # apply_one remains authoritative for current CPL, spend, budget, balance,
    # hard bid cap, fresh signals and exact provider item proof.
    _cfg_db = SessionLocal()
    try:
        _kpi_gap_cfg = _load_json(_cfg_db, account_id, "kpi_settings") or {}
    finally:
        _cfg_db.close()
    try:
        _target_gap = float(_kpi_gap_cfg.get("target_leads_per_day") or 0)
        _cpl_gap = float(_kpi_gap_cfg.get("max_cost_per_lead_rub") or 0)
        _budget_gap = float(_kpi_gap_cfg.get("daily_budget_limit_rub") or 0)
    except Exception:
        _target_gap = _cpl_gap = _budget_gap = 0.0
    if _target_gap <= 0 or _cpl_gap <= 0 or _budget_gap <= 0:
        return {"status":"blocked","reason":"kpi_economic_limits_missing",
                "changed_avito":False,"target_leads_per_day":_target_gap,
                "max_cpl_rub":_cpl_gap,"daily_budget_rub":_budget_gap}
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "cpx_advice").first()
        if not row or not row.value:
            return {"status": "blocked", "reason": "no_cpx_advice", "changed_avito": False}
        try:
            advice = _json_gap.loads(row.value) or {}
        except Exception:
            return {"status": "blocked", "reason": "bad_cpx_advice", "changed_avito": False}
        raises = (advice.get("recommendations") or {}).get("raise") or []
        if not raises:
            return {"status": "blocked", "reason": "no_raise_candidate", "changed_avito": False}

        candidate = raises[0]
        iid = candidate.get("item_id") or candidate.get("id")
        if iid is None:
            return {"status": "blocked", "reason": "raise_candidate_without_item_id", "changed_avito": False}
        suggest = str(candidate.get("suggest") or "")
        m = _re_gap.search(r"(\d+(?:\.\d+)?)\s*%", suggest)
        step = int(float(m.group(1))) if m else 15
        step = max(1, min(20, step))
        hour_key = _dt_gap.now(_tz_gap.utc).strftime("%Y%m%d%H")
        # Advice/candidate is detached from DB at this point. Release the outer
        # connection before entering apply_one() and its nested guard lookups.
        db.close()
        result = apply_one(ApplyOneBody(
            account_id=account_id,
            item_id=int(iid),
            action="raise",
            max_bid_delta_pct=step,
            actor_type="boris_auto",
            source="kpi_gap_boost",
            trigger="hourly_kpi_gap",
            request_id=f"gap:{account_id[-6:]}:{hour_key}",
        )) or {}
        return {"status": result.get("status") or "error", "candidate": candidate,
                "step_pct": step, "result": result,
                "changed_avito": result.get("status") == "ok"}
    finally:
        db.close()


# PROFITABLE_DAY_PUSH_V1
def profitable_day_push(account_id: str, max_items: int = 2):
    from datetime import datetime as _dt, timezone as _tz
    # PROFITABLE_DAY_PUSH_SESSION_V1: local import keeps this helper independent
    # from module import order and mirrors the other isolated money helpers.
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    db=SessionLocal()
    try:
        kpi=_load_json(db,account_id,"kpi_settings") or {}; red=float(kpi.get("max_cost_per_lead_rub") or 0); budget=float(kpi.get("daily_budget_limit_rub") or 0)
        if red<=0 or budget<=0: return {"status":"blocked","reason":"economic_limits_missing","changed_avito":False}
        day=marketing_today_iso(); ds=_load_json(db,account_id,"daily_stats:"+day) or {}
        try:
            ca=_dt.fromisoformat(str(ds.get("collected_at") or "").replace("Z","+00:00")); ca=ca if ca.tzinfo else ca.replace(tzinfo=_tz.utc); fresh=bool((ds.get("completeness") or {}).get("complete") is True and -60 <= (_dt.now(_tz.utc)-ca.astimezone(_tz.utc)).total_seconds() <= 900)
        except Exception: fresh=False
        if not fresh: return {"status":"blocked","reason":"stats_not_fresh","changed_avito":False}
        items=[x for x in (ds.get("items") or []) if isinstance(x,dict) and x.get("status")=="active"]
        # PROFITABLE_DAY_CURRENT_DAY_CONTACTS_ONLY_V1:
        # Never scale today's spend from yesterday's lagged contact counters.
        _item_signal_current = str(ds.get("stats_date") or "") == day
        contacts=sum(int(x.get("contacts") or 0) for x in items) if _item_signal_current else 0
        sp=ds.get("spending") or {}; spent=float(sp.get("all_spend_rub") or 0) if sp.get("status")=="ok" else -1
        if not _item_signal_current:
            return {"status":"blocked","reason":"item_stats_day_lagged","stats_date":ds.get("stats_date"),"marketing_day":day,"changed_avito":False}
        if contacts<=0 or spent<0: return {"status":"blocked","reason":"no_profitable_lead_signal","changed_avito":False}
        # PROFITABLE_DAY_CONTINUE_AFTER_TARGET_OWNER_RULE_V1: owner's standing
        # rule is to squeeze a profitable day for MORE leads even after the base
        # daily lead target is reached. The stop conditions are economics and
        # safety (CPL headroom, budget, hard bid cap, feedback), not target count.
        # This deliberately prevents a future refactor from turning target attainment
        # into an early stop while profitable capacity remains.
        cpl=spent/contacts
        if cpl>red*0.80: return {"status":"blocked","reason":"cpl_headroom_insufficient","cpl_rub":round(cpl,2),"red_cpl_rub":red,"changed_avito":False}
        if spent>=budget*0.90: return {"status":"blocked","reason":"budget_near_limit","spent_rub":spent,"budget_rub":budget,"changed_avito":False}
        # MEASUREMENT_BACKLOG_GUARD_V2 compatibility marker.
        # PROFITABLE_DAY_CANONICAL_MEASUREMENT_CAP_V1:
        # All autonomous raise lanes share the same account-level evidence cap.
        # Do not let this helper advertise capacity 20 while final apply_one
        # correctly enforces the canonical cap 2.
        MEASUREMENT_BACKLOG_LIMIT = MAX_ACTIVE_RAISE_MEASUREMENTS_PER_ACCOUNT
        _active_today=len(items)
        _measurement_backlog_limit=MEASUREMENT_BACKLOG_LIMIT
        _waiting_by_item={}
        for _mr in db.query(Storage).filter(Storage.account_id==account_id, Storage.key.like('cpx_measure:%')).all():
            try: _mv=json.loads(_mr.value or '{}')
            except Exception: _mv={}
            if _mv.get('status')!='waiting_measurement': continue
            _iid=str(_mv.get('item_id') or '')
            _prev=_waiting_by_item.get(_iid)
            if _prev is None or str(_mv.get('started_at') or '') > str(_prev.get('started_at') or ''):
                _waiting_by_item[_iid]=_mv
        _waiting_measurement=len(_waiting_by_item)
        if _waiting_measurement >= MEASUREMENT_BACKLOG_LIMIT:
            return {'status':'blocked','reason':'measurement_backlog_limit','waiting_measurement':_waiting_measurement,'measurement_backlog_limit':_measurement_backlog_limit,'active_today':_active_today,'action':'raise','changed_avito':False}
        state=_load_json(db,account_id,"profitable_day_push:"+day) or {}; hist=state.get("items") if isinstance(state,dict) else {}; hist=hist if isinstance(hist,dict) else {}; candidates=[]; now=_dt.now(_tz.utc)
        # PROFITABLE_DAY_ACTIVE_PROMOTION_ONLY_V1: this lane only scales an
        # already-active paid bid. A historical winner whose promotion is now off
        # goes to normal launch/content logic instead of wasting a money attempt.
        advice=_load_json(db,account_id,"cpx_advice") or {}; active_paid_ids=set()
        # PROFITABLE_DAY_ADVICE_TYPE_FAIL_CLOSED_V1: legacy/corrupt advisor state
        # must never crash the staged rollout or be interpreted as permission to
        # spend. No canonical current snapshot means no profitable-day raise.
        if not isinstance(advice,dict):
            return {"status":"blocked","reason":"advisor_state_invalid","changed_avito":False}
        _advice_recommendations=advice.get("recommendations") or {}
        if not isinstance(_advice_recommendations,dict):
            return {"status":"blocked","reason":"advisor_recommendations_invalid","changed_avito":False}
        # PROFITABLE_DAY_CURRENT_PROMOTION_TRUTH_V2: only the current advisor
        # snapshot may nominate an active paid promotion. Historical winner memory
        # ranks candidates but never resurrects a promotion that Avito now reports off.
        for _grp in _advice_recommendations.values():
            if not isinstance(_grp,list): continue
            for _it in _grp:
                if not isinstance(_it,dict): continue
                if (str(_it.get("write_capability") or "") in {"canonical", "provider_exact_money"}
                        and _it.get("promotion_active") and float(_it.get("bid_rub") or 0)>0):
                    active_paid_ids.add(str(_it.get("id")))
        for x in items:
            iid=x.get("id"); leads=int(x.get("contacts") or 0)
            if iid is None or leads<=0 or str(iid) not in active_paid_ids: continue
            h=hist.get(str(iid)) or {}; steps=int(h.get("successful_steps") or 0)
            if steps>=1: continue
            last=str(h.get("last_success_at") or "")
            if last:
                try:
                    ld=_dt.fromisoformat(last.replace("Z","+00:00")); ld=ld if ld.tzinfo else ld.replace(tzinfo=_tz.utc)
                    if (now-ld.astimezone(_tz.utc)).total_seconds()<3300: continue
                except Exception: pass
            candidates.append((iid,leads,int(x.get("views") or 0),steps))
        # PROFITABLE_DAY_WINNER_MEMORY_PRIORITY_V1: within the already-profitable
        # current-lead cohort, prefer items whose last completed raise improved.
        # This changes ordering only; apply_one remains the final money boundary.
        learning=_load_json(db,account_id,"cpx_learning_journal") or []
        latest_raise_effect={}
        for e in reversed(learning if isinstance(learning,list) else []):
            if not isinstance(e,dict) or str(e.get("action") or "")!="raise": continue
            _iid=str(e.get("item_id"))
            if _iid not in latest_raise_effect: latest_raise_effect[_iid]=str(e.get("effect") or "")
        winner_ids={iid for iid,eff in latest_raise_effect.items() if eff=="improved"}
        candidates.sort(key=lambda z:(str(z[0]) not in winner_ids,-z[1],z[3],z[0])); db.close(); applied=[]; blocked=[]
        # PROFITABLE_DAY_PUSH_SKIP_BLOCKED_CANDIDATES_V1: max_items caps successful
        # mutations, not candidates inspected. A no-op/blocked cheap-lead winner
        # must not prevent BORIS from trying the next proven converter this hour.
        success_cap=max(1,int(max_items or 1))
        for iid,leads,views,steps in candidates:
            if len(applied)>=success_cap:
                break
            r=apply_one(ApplyOneBody(account_id=account_id,item_id=int(iid),action="raise",max_bid_delta_pct=10,actor_type="boris_auto",source="profitable_day_push",trigger="cheap_leads_scale_day",request_id=f"pdp:{account_id}:{day}:{iid}:{steps+1}")) or {}; rec={"item_id":iid,"leads_today":leads,"views_today":views,"step_number":steps+1,"status":r.get("status"),"reason":r.get("blocked_by") or r.get("reason_code") or r.get("reason")}
            if r.get("status")=="ok" and r.get("changed_avito") is not False:
                rec.update({"old_bid_rub":r.get("old_bid_rub"),"new_bid_rub":r.get("new_bid_rub")}); applied.append(rec); h=hist.get(str(iid)) or {}; h.update({"successful_steps":steps+1,"last_success_at":now.isoformat(),"old_bid_rub":r.get("old_bid_rub"),"new_bid_rub":r.get("new_bid_rub")}); hist[str(iid)]=h
            else: blocked.append(rec)
            if int(r.get("code") or 0)==429: break
        _save_json(db,account_id,"profitable_day_push:"+day,{"date":day,"account_cpl_rub":round(cpl,2),"red_cpl_rub":red,"max_daily_steps_per_item":1,"step_pct":10,"measurement_policy":"one_step_then_daily_measurement","items":hist,"updated_at":now.isoformat()})
        return {"status":"ok","changed_avito":bool(applied),"cpl_rub":round(cpl,2),"red_cpl_rub":red,"applied":applied,"blocked":blocked,"candidates":len(candidates)}
    finally: db.close()
