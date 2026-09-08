"""
BORIS KPI Goal Runner.

Каждый запуск:
- находит реальные Avito-аккаунты с активным платным тарифом BORIS;
- требует существующий kpi_settings с target_leads_per_day > 0;
- делает МАКСИМУМ один kpi_goal_tick на аккаунт;
- не запускается параллельно сам с собой;
- --dry-run ничего не исполняет;
- --apply разрешает реальные переходы state machine.

Production cadence: ровно 1 раз в час после свежего daily_stats и CPX advice.
"""

import argparse
import fcntl
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
from sqlalchemy import text

from dotenv import load_dotenv
from app.services.reliability import ProviderDeferred

load_dotenv("/root/BORIS/backend/.env")

from app.db.session import SessionLocal
from app.models.storage import Storage
from app.models.account import Account
from app.api.billing import _load_billing


API_BASES = tuple(
    x.strip().rstrip("/") for x in os.environ.get(
        "BORIS_INTERNAL_API_BASES",
        "http://127.0.0.1:8000,http://127.0.0.1:8001",
    ).split(",") if x.strip()
) or ("http://127.0.0.1:8000", "http://127.0.0.1:8001")
LOCK_PATH = "/root/BORIS/backend/run/kpi_goal_runner.lock"


def log(msg: str):
    print(
        f"[kpi_goal_runner {datetime.now().isoformat(timespec='seconds')}] "
        f"{msg}",
        flush=True,
    )


def get_goal_auto_accounts():
    """Production virtual-marketer scope.

    Real Account row + Avito credentials + active paid BORIS tariff + KPI target.
    QA/pseudo billing rows are deliberately excluded from money execution.
    """
    db = SessionLocal()
    try:
        result = []
        for acc in db.query(Account).all():
            account_id = str(acc.account_id or "")
            if not account_id or account_id.startswith("qa") or account_id.startswith("user:"):
                continue
            disabled = db.execute(text("""
                SELECT 1 FROM reliability_kill_switches
                 WHERE account_id=:a AND blocked=true AND module IN ('actions','marketing','background')
                 LIMIT 1
            """), {"a": account_id}).first()
            if disabled:
                continue
            if not (acc.avito_client_id and acc.avito_client_secret):
                continue

            billing = _load_billing(account_id, db=db) or {}
            # MARKETER_CANONICAL_SERVICE_ENTITLEMENT_SCOPE_V1 / MARKETER_UNLIMITED_SCOPE_V1 / MARKETER_PAID_PERIOD_EXPIRY_GUARD_V1:
            # compatibility markers now name the stricter rule: unlimited is NOT
            # entitlement; expired/unknown paid periods are excluded fail-closed.
            # production marketer scope must use exactly the same current
            # service-period truth as planner, mandate executor and DB money
            # guard. billing.unlimited is usage/product metadata, not proof of
            # an active Avito-marketing service period.
            from app.services.control_plane_adapters_ext import marketing_service_entitlement
            service_entitlement = marketing_service_entitlement(db, account_id) or {}
            paid_active = service_entitlement.get("state") == "active"

            # LIFECYCLE_SCOPE_V1: unfinished KPI operations are obligations, not
            # fresh marketing consent. A paid account may need to finish a
            # publish confirmation / effect check / rollback even if its KPI
            # target was later removed. Expired/unpaid accounts are never
            # allowed to start or continue write-side execution here.
            ledger_row = db.query(Storage).filter(
                Storage.account_id == account_id,
                Storage.key == "kpi_execution_ledger",
            ).first()
            try:
                ledger = json.loads(ledger_row.value or "{}") if ledger_row else {}
            except Exception:
                ledger = {}
            has_active_obligation = bool(int(ledger.get("active_count") or 0))

            if not paid_active:
                continue

            kpi_row = db.query(Storage).filter(
                Storage.account_id == account_id,
                Storage.key == "kpi_settings",
            ).first()
            try:
                kpi = json.loads(kpi_row.value or "{}") if kpi_row else {}
            except Exception:
                kpi = {}
            target = float(kpi.get("target_leads_per_day") or 0)

            ap_row = db.query(Storage).filter(
                Storage.account_id == account_id,
                Storage.key == "autopilot_settings",
            ).first()
            try:
                ap_mode = (json.loads(ap_row.value).get("mode") if ap_row else None)
            except Exception:
                ap_mode = None

            # Only accounts with explicit autonomous mode belong to the money
            # executor. KPI rows alone are not consent to change bids.
            if ap_mode not in ("goal_auto", "always_auto"):
                continue
            # MARKETER_MONITOR_SCOPE_WITHOUT_KPI_V1: a paid account with explicit
            # autonomous mode still belongs to the read/non-money health loop even
            # when the owner has not configured a KPI target. It must NOT enter the
            # money executor merely because it is monitored.
            monitor_only = bool(target <= 0 and not has_active_obligation)

            result.append({
                "account_id": account_id,
                "tier": billing.get("tier"),
                "autopilot_mode": ap_mode,
                "target_leads_per_day": target,
                "daily_budget_limit_rub": float(kpi.get("daily_budget_limit_rub") or 0),
                "max_cost_per_lead_rub": float(kpi.get("max_cost_per_lead_rub") or 0),
                "lifecycle_only": bool(target <= 0 and has_active_obligation),
                "has_active_obligation": has_active_obligation,
                "monitor_only": monitor_only,
            })

        result.sort(key=lambda x: x["account_id"])
        return result
    finally:
        db.close()


def get_non_money_observation_accounts():
    """Connected production accounts eligible for read/non-money recovery only.

    NON_MONEY_FLEET_SCOPE_V1: billing gates money, not observation/diagnosis.
    This scope can prepare local canonical replacements and persist health, but
    never grants publication or bid authority. Kill switches still win.
    """
    db = SessionLocal()
    try:
        out=[]
        for acc in db.query(Account).all():
            account_id=str(acc.account_id or "")
            if not account_id or account_id.startswith("qa") or account_id.startswith("user:"):
                continue
            if not (acc.avito_client_id and acc.avito_client_secret):
                continue
            disabled=db.execute(text("""SELECT 1 FROM reliability_kill_switches WHERE account_id=:a AND blocked=true AND module IN ('actions','marketing','background') LIMIT 1"""),{"a":account_id}).first()
            if disabled: continue
            ap_row=db.query(Storage).filter(Storage.account_id==account_id,Storage.key=="autopilot_settings").first()
            try: ap_mode=(json.loads(ap_row.value).get("mode") if ap_row else None)
            except Exception: ap_mode=None
            if ap_mode not in ("goal_auto","always_auto"):
                continue
            # NON_MONEY_OBSERVATION_ACTIVE_CLIENT_ONLY_V1: read-only recovery is
            # broader than money execution, but an explicitly expired BORIS
            # marketing period is not a recovery incident. Dormant clients must
            # not generate endless inventory-recovery alarms or provider probes.
            from app.services.control_plane_adapters_ext import marketing_service_entitlement
            ent=marketing_service_entitlement(db, account_id) or {}
            if ent.get("state") == "expired":
                continue
            kpi_row=db.query(Storage).filter(Storage.account_id==account_id,Storage.key=="kpi_settings").first()
            try: kpi=json.loads(kpi_row.value or "{}") if kpi_row else {}
            except Exception: kpi={}
            out.append({"account_id":account_id,"autopilot_mode":ap_mode,
                        "target_leads_per_day":float(kpi.get("target_leads_per_day") or 0),
                        "daily_budget_limit_rub":float(kpi.get("daily_budget_limit_rub") or 0),
                        "max_cost_per_lead_rub":float(kpi.get("max_cost_per_lead_rub") or 0),
                        "non_money_only":True,"has_active_obligation":False})
        return sorted(out,key=lambda x:x["account_id"])
    finally:
        db.close()


def _controller_result_failed(result: dict) -> tuple[bool, str, str]:
    """Classify only explicit business failures; policy blocks/waits are healthy."""
    result = result or {}
    outer = str(result.get("status") or "").lower()
    nested = result.get("result") if isinstance(result.get("result"), dict) else {}
    inner = str((nested or {}).get("status") or "").lower()
    failed = outer in {"error", "failed", "failure"} or inner in {"error", "failed", "failure"}
    return failed, outer, inner


def _expected_policy_block(result: dict) -> tuple[bool, str]:
    """Recognize deliberate fail-closed dependencies that must stay non-red."""
    result=result or {}
    nested=result.get("result") if isinstance(result.get("result"),dict) else {}
    parts=[
        result.get("reason"), result.get("message"),
        nested.get("reason"), nested.get("message"),
        nested.get("tariff_confirmation_state"), nested.get("required_avito_tariff"),
    ]
    text=" | ".join(str(x or "") for x in parts).lower()
    expected=(
        "тариф avito не подтвержд" in text
        or "подтвердите требуемый тариф avito" in text
        or "tariff_not_confirmed" in text
        or "not_confirmed" in text and "тариф" in text
        # NON_MONEY_MONEY_FENCE_IS_POLICY_WAIT_V1: in the ownerless non-money
        # lane, an explicit refusal to mutate bids is the safety contract working,
        # not an incident requiring the owner. Keep it deferred and retry hourly.
        or "non_money режим запретил изменение ставки" in text
        or "non_money_money_mutation_fence" in text
    )
    reason=str(nested.get("reason") or result.get("reason") or nested.get("message") or result.get("message") or "ожидается подтверждение внешней capability")[:300]
    return expected,reason


def call_tick(client, account_id: str, allow_stale_non_money: bool = False):
    """Call KPI through the local HA pair, retrying only pre-response connects.

    A ConnectError proves the selected local socket never accepted the POST, so
    switching 8000 -> 8001 is safe. RemoteProtocolError (peer disconnected before
    sending ANY response) is ambiguous: the server may already have committed a
    money transition. Never replay it. Surface it as account-scoped deferred so a
    single transient disconnect cannot poison the fleet and cannot duplicate money.
    """
    rounds = 2
    last_connect_error = None
    for round_no in range(rounds):
        for base in API_BASES:
            try:
                response = client.post(
                    f"{base}/api/avito/kpi_goal_tick",
                    params={"account_id": account_id, "allow_stale_non_money": str(bool(allow_stale_non_money)).lower()},
                    timeout=90,
                )
                response.raise_for_status()
                if base != API_BASES[0]:
                    log(f"[{account_id}] internal HA failover succeeded via {base}")
                return response.json()
            except httpx.ConnectError as exc:
                last_connect_error = exc
                log(f"[{account_id}] internal API {base} unavailable before request acceptance")
                continue
            except httpx.RemoteProtocolError as exc:
                # Ambiguous completion: request could have reached the controller.
                # Replaying against the replica could duplicate a money transition.
                raise ProviderDeferred(f"internal API disconnected before response; no replay: {exc}") from exc
        if round_no < rounds - 1:
            time.sleep(1.0)
    if last_connect_error is not None:
        raise last_connect_error
    raise RuntimeError("BORIS internal API replicas are unavailable")


def _save_marketer_account_health(account_id: str, state: str, reason: str = "", stage: str = "", next_check_minutes: int | None = None):
    """Persist account-scoped marketer health without failing the whole fleet."""
    db = SessionLocal()
    try:
        payload = {
            "state": str(state or "unknown"),
            "reason": str(reason or "")[:500],
            "stage": str(stage or "")[:120],
            "checked_at": datetime.now().isoformat(),
        }
        # MARKETER_NEXT_CHECK_VISIBILITY_V1: owner-facing health must say when
        # BORIS will re-check an external lifecycle incident. This is metadata
        # only; it never triggers an extra provider mutation.
        if next_check_minutes is not None:
            from datetime import timedelta
            payload["next_check_at"] = (datetime.now() + timedelta(minutes=max(1,int(next_check_minutes)))).isoformat()
        row = db.query(Storage).filter(
            Storage.account_id == account_id,
            Storage.key == "virtual_marketer_account_health",
        ).order_by(Storage.id.desc()).first()
        if row:
            row.value = json.dumps(payload, ensure_ascii=False)
        else:
            db.add(Storage(account_id=account_id, key="virtual_marketer_account_health", value=json.dumps(payload, ensure_ascii=False)))
        db.commit()
    finally:
        db.close()


def _fresh_stats_ready(account_id: str) -> tuple[bool, str]:
    """Money actions require today's complete facts. Missing/partial stats fail closed."""
    db = SessionLocal()
    try:
        # KPI_FRESH_STATS_MOSCOW_DAY_V1:
        # Host clock is UTC; BORIS marketing day is Europe/Moscow.
        from app.services.marketing_clock import marketing_today_iso
        today = marketing_today_iso()
        key = "daily_stats:" + today
        row = db.query(Storage).filter(Storage.account_id==account_id, Storage.key==key).order_by(Storage.id.desc()).first()
        if not row:
            return False, "нет свежей статистики за сегодня"
        try:
            snap = json.loads(row.value or "{}")
        except Exception:
            return False, "статистика за сегодня повреждена"
        # Avito item views/contacts can legitimately lag by a day. `stats_date`
        # therefore must NOT be used as the money freshness clock. Money safety
        # is based on the independently collected same-day spending signal, while
        # the item inventory must be a complete snapshot collected today.
        spending = snap.get("spending") if isinstance(snap.get("spending"), dict) else {}
        if str(spending.get("status") or "") != "ok" or str(spending.get("date") or "") != today:
            return False, "нет подтверждённых данных о расходах за сегодня"
        completeness = snap.get("completeness") if isinstance(snap.get("completeness"), dict) else {}
        if completeness.get("complete") is not True or completeness.get("inventory_complete") is not True:
            return False, "список объявлений за сегодня неполный"
        if snap.get("items_count") is None:
            return False, "не получен список объявлений за сегодня"
        return True, "ok"
    finally:
        db.close()


def _fresh_complete_active_inventory(account_id: str) -> tuple[bool, int, str]:
    """Use today's complete collector snapshot before another Avito inventory read.

    NON_MONEY_FRESH_INVENTORY_PROVIDER_SKIP_V1:
    the canonical hourly collector already fetched the complete active inventory.
    Re-reading /items in the non-money lane can hit a shared Retry-After and
    incorrectly block mapping/content self-heal even though active inventory is
    already proven. Trust only a recent, complete snapshot; zero/unknown/stale
    inventory still falls through to the existing provider bootstrap.
    """
    db = SessionLocal()
    try:
        from app.services.marketing_clock import marketing_today_iso
        day = marketing_today_iso()
        row = db.query(Storage).filter(
            Storage.account_id == account_id,
            Storage.key == "daily_stats:" + day,
        ).order_by(Storage.id.desc()).first()
        if not row:
            return False, 0, "daily_stats_missing"
        try:
            snap = json.loads(row.value or "{}")
        except Exception:
            return False, 0, "daily_stats_invalid"
        completeness = snap.get("completeness") if isinstance(snap.get("completeness"), dict) else {}
        if completeness.get("complete") is not True or completeness.get("inventory_complete") is not True:
            return False, 0, "inventory_incomplete"
        items = snap.get("items")
        if not isinstance(items, list) or snap.get("items_count") is None:
            return False, 0, "inventory_missing"
        try:
            expected = int(snap.get("items_count"))
        except Exception:
            return False, 0, "items_count_invalid"
        if expected != len(items):
            return False, 0, "inventory_count_mismatch"
        collected_raw = str(snap.get("collected_at") or "").strip()
        try:
            collected = datetime.fromisoformat(collected_raw.replace("Z", "+00:00"))
            if collected.tzinfo is None:
                collected = collected.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - collected.astimezone(timezone.utc)).total_seconds()
        except Exception:
            return False, 0, "collected_at_invalid"
        if age < -300 or age > 5400:
            return False, 0, "inventory_snapshot_stale"
        active_count = sum(
            1 for item in items
            if isinstance(item, dict) and str(item.get("status") or "").lower() == "active"
        )
        if active_count <= 0:
            return False, 0, "zero_active_inventory"
        return True, active_count, "fresh_complete_daily_stats"
    finally:
        db.close()


def _campaign_revision_observation(account_id: str) -> dict:
    """Account-wide clean-window guard after a full Feed Factory publication.

    The delivery worker writes this lock only after Avito proves a full campaign
    revision is live. While it is active, the KPI runner keeps collecting stats
    through the normal collector but does not start a NEW bid/content mutation.
    Existing lifecycle obligations are allowed to finish separately.
    """
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(
            Storage.account_id == account_id,
            Storage.key == "campaign_revision_observation",
        ).order_by(Storage.id.desc()).first()
        if not row or not row.value:
            return {"active": False, "status": "missing"}
        try:
            state = json.loads(row.value or "{}")
        except Exception:
            return {"active": False, "status": "invalid"}
        raw = str(state.get("complete_after") or "").strip()
        try:
            complete_at = datetime.fromisoformat(raw.replace("Z", "+00:00")) if raw else None
            if complete_at is not None and complete_at.tzinfo is None:
                complete_at = complete_at.replace(tzinfo=timezone.utc)
        except Exception:
            complete_at = None
        now = datetime.now(timezone.utc)
        active = bool(
            str(state.get("status") or "") == "active"
            and complete_at is not None
            and now < complete_at
        )
        if not active and str(state.get("status") or "") == "active" and complete_at is not None:
            state["status"] = "completed"
            state["completed_at"] = now.isoformat()
            row.value = json.dumps(state, ensure_ascii=False)
            db.commit()
        return {
            **state,
            "active": active,
            "remaining_seconds": max(0, int((complete_at - now).total_seconds())) if active and complete_at else 0,
        }
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser()

    mode = parser.add_mutually_exclusive_group()

    mode.add_argument(
        "--apply",
        action="store_true",
        help="Разрешить реальные KPI state transitions",
    )

    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать подходящие аккаунты",
    )

    mode.add_argument(
        "--non-money-apply",
        action="store_true",
        help="Исполнять только безопасный безденежный KPI/lifecycle путь; денежные действия жёстко запрещены",
    )

    parser.add_argument(
        "--account",
        help="Ограничить запуск одним account_id",
    )

    args = parser.parse_args()

    # Без явного --apply runner безопасен. Direct APPLY is additionally
    # lane-gated so stale soak/parallel sessions cannot create a second money
    # scheduler beside staged_rollout / dedicated recovery.
    apply_mode = bool(args.apply)
    non_money_mode = bool(args.non_money_apply)
    if apply_mode:
        lane = str(os.environ.get("BORIS_MARKETER_MONEY_LANE") or "").strip()
        if lane not in {"controlled_kpi_v1"}:
            log(f"BLOCKED_UNTRUSTED_MONEY_LANE lane={lane or '-'}")
            return 23
        # KPI_APPLY_SERVICE_CALLER_GUARD_V1: an environment variable expresses
        # intent but is not authority. Real money-capable KPI apply is accepted
        # only inside the canonical ai-marketer systemd cgroup. This prevents
        # stale ownerless/soak shells from creating a second money scheduler.
        try:
            from pathlib import Path as _KpiPath
            _cg = _KpiPath("/proc/self/cgroup").read_text(errors="ignore")
        except Exception:
            _cg = ""
        if "boris-ai-marketer.service" not in _cg:
            log("BLOCKED_UNTRUSTED_KPI_APPLY_CALLER required=boris-ai-marketer.service")
            return 23

    # ---------------------------------------------------------
    # Не допускаем наложения двух cron-runner.
    # ---------------------------------------------------------

    lock_file = open(LOCK_PATH, "w")

    # NON_MONEY_LOCK_WAIT_FOR_DEPLOY_V1:
    # rolling_restart_backend.sh intentionally owns this same lock while API
    # generation is being switched. The canonical hourly non-money stage must
    # wait for that safe rollout instead of silently losing the whole KPI hour.
    # Money/dry-run callers keep the historical immediate non-overlap behavior.
    try:
        _non_money_lock_wait_sec = min(
            900.0,
            max(0.0, float(os.environ.get("BORIS_KPI_NONMONEY_LOCK_WAIT_SEC") or 600.0)),
        ) if non_money_mode else 0.0
    except Exception:
        _non_money_lock_wait_sec = 600.0 if non_money_mode else 0.0
    _lock_wait_started = time.monotonic()
    _lock_wait_logged = False
    while True:
        try:
            fcntl.flock(
                lock_file.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
            _lock_waited = time.monotonic() - _lock_wait_started
            if _lock_wait_logged:
                log(f"KPI_NON_MONEY_LOCK_ACQUIRED waited={_lock_waited:.1f}s")
            break
        except BlockingIOError:
            _lock_waited = time.monotonic() - _lock_wait_started
            if not non_money_mode:
                log("SKIP: предыдущий запуск ещё работает")
                return 0
            if _lock_waited >= _non_money_lock_wait_sec:
                # NON_MONEY_LOCK_TIMEOUT_VISIBLE_V1: a >10m lock holder is no
                # longer normal deploy convergence. Surface a real stage failure
                # so Guardian/next timer sees it instead of a false green skip.
                log(
                    f"DEFERRED_KPI_LOCK_BUSY waited={_lock_waited:.1f}s "
                    f"limit={_non_money_lock_wait_sec:.0f}s"
                )
                return 75
            if not _lock_wait_logged:
                log(
                    f"KPI_NON_MONEY_LOCK_WAIT lock_busy=1 "
                    f"limit={_non_money_lock_wait_sec:.0f}s"
                )
                _lock_wait_logged = True
            time.sleep(min(1.0, max(0.05, _non_money_lock_wait_sec - _lock_waited)))

    # Paid entitlement gates money execution only. The non-money lane observes
    # connected autonomous production accounts so expired/unpaid tenants can be
    # diagnosed without BORIS spending or publishing on their behalf.
    accounts = get_non_money_observation_accounts() if non_money_mode else get_goal_auto_accounts()

    if args.account:
        accounts = [
            x for x in accounts
            if x["account_id"] == args.account
        ]

    log(
        f"режим={'APPLY' if apply_mode else ('NON_MONEY_APPLY' if non_money_mode else 'DRY-RUN')}, "
        f"goal_auto аккаунтов={len(accounts)}"
    )

    if not accounts:
        log("Подходящих аккаунтов нет")
        return 0

    def process_account(acc):
        account_id = acc["account_id"]
        log(
            f"[{account_id}] "
            f"цель={acc['target_leads_per_day']:g} лидов/день, "
            f"дневной бюджет={acc['daily_budget_limit_rub']:g} ₽, "
            f"max CPL={acc['max_cost_per_lead_rub']:g} ₽"
        )
        if not apply_mode and not non_money_mode:
            return "ok"
        try:
            # NON_MONEY_MEASUREMENT_LIFECYCLE_SWEEP_V1: every hourly non-money
            # pass first advances old CPX measurement locks using DB-only facts.
            # This never changes Avito or spends money. Without it, an account
            # blocked by a campaign observation window could skip advisor/run and
            # leave an old bid experiment waiting forever.
            if non_money_mode:
                try:
                    # MEASUREMENT_SWEEP_INTERNAL_HA_V2: this GET is DB/read-only,
                    # so a pre-response local ConnectError can safely fail over to
                    # the sibling replica during canonical rolling deploys.
                    _measure_data = None
                    _measure_last_connect = None
                    with httpx.Client() as _measure_client:
                        for _measure_base in API_BASES:
                            try:
                                _measure_resp = _measure_client.get(
                                    f"{_measure_base}/api/cpx_advisor/run",
                                    params={"account_id": account_id}, timeout=45,
                                )
                                _measure_resp.raise_for_status()
                                _measure_data = _measure_resp.json() or {}
                                if _measure_base != API_BASES[0]:
                                    log(f"[{account_id}] MEASUREMENT_SWEEP HA failover succeeded via {_measure_base}")
                                break
                            except httpx.ConnectError as _measure_connect:
                                _measure_last_connect = _measure_connect
                                continue
                    if _measure_data is None:
                        raise _measure_last_connect or RuntimeError("measurement sweep: no local API response")
                    _measure_advice = _measure_data.get("advice") if isinstance(_measure_data.get("advice"), dict) else _measure_data
                    _mc = _measure_advice.get("measurement_cycle") or {}
                    log(f"[{account_id}] MEASUREMENT_SWEEP checked={_mc.get("checked",0)} measured={_mc.get("measured",0)} waiting={_mc.get("waiting",0)} errors={_mc.get("errors",0)}")
                except Exception as _measure_exc:
                    # Read/DB-only lifecycle failure must be visible, but it must
                    # not grant or replay any provider mutation. The ordinary
                    # controller below can still service unrelated lifecycle work.
                    log(f"[{account_id}] MEASUREMENT_SWEEP deferred: {type(_measure_exc).__name__}: {str(_measure_exc)[:180]}")

            _revision_window = _campaign_revision_observation(account_id)
            if _revision_window.get("active") and not bool(acc.get("has_active_obligation")):
                _until = str(_revision_window.get("complete_after") or "")
                _scope_kind = str(_revision_window.get("scope_kind") or ("partial_success_subset" if "partial_success_subset" in str(_revision_window.get("source") or "") else "full_campaign_revision"))
                _reason = (
                    "После частично успешной публикации BORIS собирает чистый полный день по реально опубликованной части до новых ставок/заголовков; существующая статистика продолжает собираться"
                    if _scope_kind == "partial_success_subset" else
                    "После полной публикации кампании BORIS собирает чистый полный день до новых ставок/заголовков; существующая статистика продолжает собираться"
                )
                _save_marketer_account_health(
                    account_id,
                    "deferred",
                    f"{_reason}; complete_after={_until}",
                    "campaign_revision_observation",
                )
                log(
                    f"[{account_id}] CAMPAIGN_REVISION_OBSERVATION: "
                    f"new_mutations=blocked complete_after={_until} "
                    f"campaign_id={_revision_window.get('campaign_id')} "
                    f"upload_id={_revision_window.get('upload_id')}"
                )
                return "deferred"
            if _revision_window.get("active") and bool(acc.get("has_active_obligation")):
                log(
                    f"[{account_id}] CAMPAIGN_REVISION_OBSERVATION: active lifecycle obligation "
                    "allowed to finish; no parallel new mutation will be started"
                )

            if non_money_mode:
                # KPI_ACTIVE_LIFECYCLE_BEFORE_INVENTORY_RECOVERY_V1: once BORIS has
                # a real content/publication obligation, finish it before optional
                # inventory diagnostics. Provider throttle in inventory recovery must
                # never strand a ready publication while the lead goal is missed.
                if bool(acc.get("has_active_obligation")):
                    log(f"[{account_id}] ACTIVE_LIFECYCLE_PRIORITY: skip inventory recovery until current publication/measurement obligation advances")
                else:
                    # OWNERLESS_ZERO_ACTIVE_NONMONEY_RECOVERY_V1: non-money is the
                    # canonical hourly diagnostic lane. It must not stop at the
                    # controller's inventory_recovery label; invoke the existing
                    # exact-identity recovery primitive and persist its real outcome.
                    try:
                        _fresh_active_ok, _fresh_active_count, _fresh_active_reason = _fresh_complete_active_inventory(account_id)
                        if _fresh_active_ok:
                            _nm_inv = {
                                "status": "active_portfolio_present",
                                "account_id": account_id,
                                "active_count": _fresh_active_count,
                                "source": "fresh_complete_daily_stats",
                                "changed_feed": False,
                            }
                            log(
                                f"[{account_id}] NON_MONEY_INVENTORY_FROM_DAILY_STATS "
                                f"active={_fresh_active_count} provider_bootstrap=skipped"
                            )
                        else:
                            from app.services.initial_portfolio import bootstrap_zero_active_portfolio as _nm_bootstrap_zero_active
                            _nm_inv = _nm_bootstrap_zero_active(account_id, 100) or {}
                        _nm_irs = str(_nm_inv.get("status") or "")
                        if _nm_irs != "active_portfolio_present":
                            log(f"[{account_id}] NON_MONEY_INVENTORY_RECOVERY status={_nm_irs} active={_nm_inv.get('active_count')} old={_nm_inv.get('old_count')} selected={_nm_inv.get('selected_count')} queued={_nm_inv.get('queued_count')}")
                        if _nm_irs == "reactivation_queued":
                            _save_marketer_account_health(account_id, "deferred", "BORIS поставил доказанно управляемые объявления на восстановление через canonical feed", "inventory_recovery")
                            return "deferred"
                        if _nm_irs == "external_write_unavailable":
                            # CANONICAL_REPLACEMENT_AUTOPREPARE_V1: if exact reactivation is
                            # impossible, prepare (but NEVER publish) a validated canonical
                            # replacement automatically. Owner is not the diagnostic operator.
                            try:
                                from app.services.initial_portfolio import prepare_canonical_replacement as _prepare_replacement
                                _replacement = _prepare_replacement(account_id, 100) or {}
                                _rs = str(_replacement.get("status") or "")
                                if _rs == "safe_feed_ready":
                                    # CANONICAL_REPLACEMENT_AUTHORIZATION_GAP_V1: preparation
                                    # is automatic, publication authority is not invented. Persist
                                    # the exact external dependency while the hourly controller
                                    # keeps rechecking it ownerlessly.
                                    _save_marketer_account_health(account_id, "deferred", f"Нет активных объявлений; безопасный replacement готов: {_replacement.get('candidate_count')} объявлений, официальный XML-check без ошибок. Публикация не выполнена: отсутствует доказанное разрешение на новую публикацию", "publication_authorization_gap", next_check_minutes=60)
                                    return "deferred"
                                _save_marketer_account_health(account_id, "deferred", f"Нет активных объявлений; replacement автоматически проверен, статус={_rs}, reason={_replacement.get('reason','')}", "canonical_replacement_prepare")
                                return "deferred"
                            except Exception as _replacement_exc:
                                _save_marketer_account_health(account_id, "incident", f"Ошибка автоматической подготовки replacement: {type(_replacement_exc).__name__}: {str(_replacement_exc)[:180]}", "canonical_replacement_prepare")
                                return "incident"
                        if _nm_irs == "no_effective_old_items":
                            _save_marketer_account_health(account_id, "deferred", "Нет активных объявлений и нет старых объявлений с доказанным спросом для безопасной реактивации", "inventory_recovery")
                            return "deferred"
                        if _nm_irs in {"blocked", "error"}:
                            _nm_reason = str(_nm_inv.get("reason") or _nm_irs)[:300]
                            _save_marketer_account_health(account_id, "deferred" if _nm_irs == "blocked" else "incident", _nm_reason, "inventory_recovery")
                            return "deferred" if _nm_irs == "blocked" else "incident"
                    except Exception as _nm_inv_exc:
                        log(f"[{account_id}] NON_MONEY_INVENTORY_RECOVERY deferred: {type(_nm_inv_exc).__name__}: {str(_nm_inv_exc)[:180]}")

                # OWNERLESS_NON_MONEY_EXECUTION_V1: explicit hard barrier around
                # the controller. allow_stale_non_money=True disables every money
                # mutation while allowing deterministic analysis/content/lifecycle
                # work to continue for KPI accounts with zero/missing budget.
                with httpx.Client() as client:
                    result = call_tick(client, account_id, allow_stale_non_money=True)
                stage = str(result.get("stage") or "non_money")
                failed_result, outer_status, nested_status = _controller_result_failed(result)
                nested = result.get("result") if isinstance(result.get("result"), dict) else {}
                nested_status = str(nested.get("status") or nested_status or "").lower()
                reason = str(result.get("reason") or nested.get("reason") or nested.get("message") or outer_status or nested_status or "non-money controller result")[:300]
                # NON_MONEY_TRUTH_V2
                _policy_wait,_policy_reason=_expected_policy_block(result)
                if _policy_wait and (str(outer_status)=="blocked" or nested_status=="blocked"):
                    _save_marketer_account_health(account_id, "deferred", _policy_reason, stage, next_check_minutes=60)
                    log(f"[{account_id}] NON_MONEY_POLICY_WAIT stage={stage} reason={_policy_reason}")
                    return "deferred"
                if failed_result or str(outer_status) in {"blocked", "provider_stalled"} or nested_status in {"blocked", "provider_stalled", "unsupported", "unsupported_action"} or stage == "unsupported_action":
                    _save_marketer_account_health(account_id, "incident", reason, stage)
                    log(f"[{account_id}] NON_MONEY_FAIL status={outer_status or chr(45)} stage={stage} result.status={nested_status or chr(45)} reason={reason}")
                    return "incident"
                if str(outer_status) in {"waiting", "deferred"} or nested_status in {"waiting", "retry_scheduled", "publish_requested", "rollback_publish_requested", "mapping_wait", "waiting_mapping"}:
                    _save_marketer_account_health(account_id, "deferred", reason, stage)
                    log(f"[{account_id}] NON_MONEY_DEFERRED status={outer_status or chr(45)} stage={stage} result.status={nested_status or chr(45)}")
                    return "deferred"
                _save_marketer_account_health(account_id, "ok", str(result.get("reason") or "безденежный цикл выполнен")[:300], f"non_money:{stage}")
                log(f"[{account_id}] NON_MONEY_OK status={result.get(chr(115)+chr(116)+chr(97)+chr(116)+chr(117)+chr(115))} stage={stage}")
                return "ok"
            # MONITOR_ONLY_NO_MONEY_V1: target-less paid accounts are visible and
            # refreshed every hourly cycle, but never receive bid/promotion writes
            # until an explicit KPI target exists. This closes stale owner health
            # without inventing business goals or spending authority.
            if bool(acc.get("monitor_only")):
                with httpx.Client() as client:
                    _mon = call_tick(client, account_id, allow_stale_non_money=True) or {}
                _mon_stage = str(_mon.get("stage") or "monitor_only")
                _mon_status = str(_mon.get("status") or "")
                _mon_reason = str(_mon.get("reason") or "KPI target не задан; аккаунт проверен без денежных действий")[:300]
                _save_marketer_account_health(account_id, "deferred" if _mon_status in {"waiting","deferred"} else "ok", _mon_reason, f"monitor_only:{_mon_stage}")
                log(f"[{account_id}] MONITOR_ONLY status={_mon_status} stage={_mon_stage}; money_actions=forbidden")
                return "deferred" if _mon_status in {"waiting","deferred"} else "ok"

            # POSITION_MONITOR_V1: read-only cabinet position evidence is collected
            # for every active virtual-marketer account when an identity-verified
            # Browser Gateway session is available. It never blocks KPI execution
            # and has no authority to spend money or change bids by itself.
            try:
                if str(acc.get("autopilot_mode") or "") in {"goal_auto", "always_auto"}:
                    from app.services.avito_position_monitor import monitor_tick as _position_monitor_tick
                    _position = _position_monitor_tick(account_id) or {}
                    log(f"[{account_id}] position_monitor={_position.get('status')} reason={_position.get('reason','')}")
            except Exception as _position_exc:
                log(f"[{account_id}] position_monitor deferred: {type(_position_exc).__name__}: {str(_position_exc)[:180]}")

            # OWNERLESS_ZERO_ACTIVE_RECOVERY_V1: before ordinary optimization,
            # use the existing fail-closed portfolio recovery. It can mutate only
            # an exact managed canonical-feed identity; external items are never
            # guessed. Unrecoverable inventory remains deferred and is rechecked
            # hourly, so the owner does not become the polling loop.
            try:
                from app.services.initial_portfolio import bootstrap_zero_active_portfolio as _bootstrap_zero_active_portfolio
                _inventory_recovery = _bootstrap_zero_active_portfolio(account_id, 100) or {}
                _irs = str(_inventory_recovery.get("status") or "")
                if _irs != "active_portfolio_present":
                    log(f"[{account_id}] inventory_recovery={_irs} active={_inventory_recovery.get('active_count')} old={_inventory_recovery.get('old_count')} selected={_inventory_recovery.get('selected_count')} queued={_inventory_recovery.get('queued_count')}")
                if _irs == "reactivation_queued":
                    _save_marketer_account_health(account_id, "deferred", "BORIS поставил доказанно управляемые объявления на автоматическое восстановление через canonical feed", "inventory_recovery")
                    return "deferred"
                if _irs in {"external_write_unavailable", "no_effective_old_items"}:
                    _reason = ("Нет активных объявлений; старые объявления найдены, но безопасная автоматическая реактивация через canonical feed недоступна" if _irs == "external_write_unavailable" else "Нет активных объявлений и нет старых объявлений с доказанным спросом для безопасной реактивации")
                    _save_marketer_account_health(account_id, "deferred", _reason, "inventory_recovery")
                    return "deferred"
                if _irs in {"blocked", "error"}:
                    _reason = str(_inventory_recovery.get("reason") or _irs)[:300]
                    _save_marketer_account_health(account_id, "deferred" if _irs == "blocked" else "incident", _reason, "inventory_recovery")
                    return "deferred" if _irs == "blocked" else "incident"
            except Exception as _inv_exc:
                log(f"[{account_id}] inventory_recovery deferred: {type(_inv_exc).__name__}: {str(_inv_exc)[:180]}")

            # LIFECYCLE_PRIORITY_V1: existing publication/effect/rollback work
            # is serviced before any new feed bootstrap, first-bid or ramp work.
            if bool(acc.get("has_active_obligation")):
                log(f"[{account_id}] LIFECYCLE_PRIORITY: active obligation -> service lifecycle without starving guarded reach")
                # FIRST_BID_CAUSAL_SINGLE_STEP_V2: an unfinished content/publication
                # obligation must not starve reach, but first paid activation is a
                # causal money hypothesis. Start at most ONE new paid item per KPI
                # cycle, then let measurement/journal evidence decide the next step.
                try:
                    from app.api.cpx_advisor import bootstrap_new_no_promo as _lifecycle_bootstrap
                    _lb = _lifecycle_bootstrap(account_id, max_items=1) or {}
                    log(f"[{account_id}] lifecycle_feed_bootstrap={_lb.get('status')} unpromoted={_lb.get('unpromoted_before')} applied={len(_lb.get('applied') or [])}")
                except Exception as _lb_exc:
                    log(f"[{account_id}] lifecycle_feed_bootstrap deferred: {type(_lb_exc).__name__}: {str(_lb_exc)[:180]}")
                with httpx.Client() as client:
                    result = call_tick(client, account_id, allow_stale_non_money=True)
                stage = result.get("stage")
                status = result.get("status")
                nested = result.get("result")
                nested_status = str((nested or {}).get("status") or "").lower() if isinstance(nested, dict) else ""
                log(f"[{account_id}] lifecycle status={status}, stage={stage}, result.status={nested_status or '-'}")
                # LIFECYCLE_PRIORITY_WAIT_TRUTH_V1: priority servicing must keep
                # unresolved canonical mapping non-green just like the normal lane.
                if str(stage or "") == "mapping_recovery" and nested_status in {"mapping_wait", "waiting_mapping", "blocked", "waiting"}:
                    reason = str((nested or {}).get("reason") or "Ожидается точное canonical-сопоставление; BORIS не угадывает identity")[:300]
                    _save_marketer_account_health(account_id, "deferred", reason, "mapping_recovery")
                    return "deferred"
                if str(stage or "") in {"confirm_publish", "confirm_rollback", "publish", "rollback_publish", "effect_check"}:
                    if nested_status in {"waiting", "retry_scheduled", "publish_requested", "rollback_publish_requested"}:
                        reason = str((nested or {}).get("reason") or (nested or {}).get("live_status") or nested_status)[:300]
                        _save_marketer_account_health(account_id, "deferred", reason, str(stage or "lifecycle"))
                        return "deferred"
                    if nested_status in {"blocked", "error", "failed", "provider_stalled"}:
                        reason = str((nested or {}).get("reason") or (nested or {}).get("message") or nested_status)[:300]
                        _policy_wait,_policy_reason=_expected_policy_block(result)
                        if nested_status == "blocked" and _policy_wait:
                            log(f"[{account_id}] LIFECYCLE_POLICY_WAIT: stage={stage} reason={_policy_reason}")
                            _save_marketer_account_health(account_id, "deferred", _policy_reason, str(stage or "lifecycle"), next_check_minutes=60)
                            return "deferred"
                        log(f"[{account_id}] LIFECYCLE_INCIDENT: stage={stage} status={nested_status} reason={reason}")
                        _save_marketer_account_health(account_id, "incident", reason, str(stage or "lifecycle"))
                        return "incident"
                failed_result, outer_status, nested_failed = _controller_result_failed(result)
                if failed_result:
                    if isinstance(nested, dict):
                        reason = str(nested.get("reason") or nested.get("message") or result.get("reason") or "lifecycle controller error")[:300]
                    else:
                        reason = str(result.get("reason") or "lifecycle controller error")[:300]
                    _save_marketer_account_health(account_id, "incident", reason, str(stage or "lifecycle"))
                    return "incident"
                _save_marketer_account_health(account_id, "ok", str(result.get("reason") or "")[:300], str(stage or "lifecycle"))
                return "ok"

            stats_ready, stats_reason = _fresh_stats_ready(account_id)
            if not stats_ready:
                # KPI_EXECUTION_LANES_V1: stale/unknown spend blocks ONLY money.
                # The KPI controller still has to advance deterministic/content
                # lifecycle (mapping recovery, prepare/apply/publish/effect) using
                # preserved item facts. Money functions remain fail-closed inside
                # their own guards. This prevents a provider stats incident from
                # turning BORIS into a completely idle marketer.
                log(f"[{account_id}] MONEY_FAIL_CLOSED: {stats_reason}; ставки/продвижение запрещены, content/diagnostic lane продолжает работу")
                with httpx.Client() as client:
                    result = call_tick(client, account_id, allow_stale_non_money=True)
                stage = result.get("stage")
                status = result.get("status")
                log(f"[{account_id}] non_money status={status}, stage={stage}")
                if result.get("reason"):
                    log(f"[{account_id}] {result.get('reason')}")
                failed_result, outer_status, nested_status = _controller_result_failed(result)
                if failed_result:
                    reason = str(result.get("reason") or "non-money controller error")[:300]
                    _save_marketer_account_health(account_id, "incident", reason, str(stage or "non_money"))
                    return "incident"
                _save_marketer_account_health(account_id, "deferred", stats_reason, f"non_money:{stage or 'tick'}")
                return "deferred"
            # Feed promotion bootstrap applies to every real paid BORIS account,
            # even when KPI target has not been configured yet.
            from app.api.cpx_advisor import (bootstrap_new_no_promo_batch,
                hourly_new_feed_low_views_ramp)
            # FIRST_BID_BOUNDED_BASELINE_COHORT_V3: starter CPX on newly published
            # ads is baseline reach initialization. Process up to five per trusted
            # KPI cycle; every item still passes the normal money/autonomy guards.
            bootstrap = bootstrap_new_no_promo_batch(account_id, max_items=5) or {}
            log(f"[{account_id}] feed_bootstrap={bootstrap.get('status')} feed_items={bootstrap.get('feed_items')} unpromoted={bootstrap.get('unpromoted_before')} applied={len(bootstrap.get('applied') or [])}")
            _bs = str(bootstrap.get("status") or "")
            _bootstrap_reason = str(bootstrap.get("reason") or "; ".join(str(x.get("reason") or "") for x in (bootstrap.get("blocked") or [])[:3] if isinstance(x,dict)) or "")[:500]
            # ZERO_BUDGET_NONMONEY_CONTINUATION_V1: zero advertising budget is a
            # deliberate money-policy block, not an account failure. Paid
            # bootstrap/ramp stays off while content/lifecycle keeps running.
            _force_non_money = (_bs == "blocked" and _bootstrap_reason == "no_explicit_daily_budget")
            _measurement_backlog_wait = (_bs == "measurement_wait")
            if _measurement_backlog_wait:
                log(f"[{account_id}] MONEY_MEASUREMENT_WAIT: waiting={bootstrap.get('waiting_measurements')} cap={bootstrap.get('measurement_cap')}; new raise hypotheses paused until automatic measurement sweep frees capacity")
            if _force_non_money:
                log(f"[{account_id}] MONEY_POLICY_BLOCK: {_bootstrap_reason}; paid bootstrap/ramp skipped, content/lifecycle continues")
            elif _bs in {"error", "blocked"}:
                _br = _bootstrap_reason or "new feed first-bid bootstrap blocked"
                log(f"[{account_id}] FIRST_BID_INCIDENT: {_br}")
                _save_marketer_account_health(account_id, "incident", _br, "new_feed_first_bid")
                return "incident"
            if _bs == "external_wait":
                _br = str(bootstrap.get("reason") or "Avito temporarily deferred first-bid bootstrap")[:500]
                _save_marketer_account_health(account_id, "deferred", _br, "new_feed_first_bid")
                return "deferred"
            if _bs == "partial":
                _blocked_reasons = [str(x.get("reason") or x.get("status") or "blocked")[:120] for x in (bootstrap.get("blocked") or [])[:5] if isinstance(x, dict)]
                log(f"[{account_id}] FIRST_BID_PARTIAL: item-level blocks={len(bootstrap.get('blocked') or [])} reasons={_blocked_reasons}; KPI cycle continues")

            # New Feed Factory launch ramp: after initial minBid+20%, every hourly
            # cycle evaluates EACH new ad. Low velocity (<2 views/hour) gets +10%.
            # If bootstrap changed bids right now, do not stack +10% in the same
            # minute; the first reaction check happens on the next hourly cycle.
            if _force_non_money:
                ramp = {"status": "skipped_money_policy", "changed_avito": False, "reason": _bootstrap_reason}
            elif _measurement_backlog_wait:
                ramp = {"status": "measurement_wait", "changed_avito": False,
                        "reason": "account_measurement_backlog_wait",
                        "waiting_measurements": bootstrap.get("waiting_measurements"),
                        "measurement_cap": bootstrap.get("measurement_cap")}
            elif bootstrap.get("changed_avito"):
                ramp = {"status": "waiting_next_hour_after_bootstrap", "changed_avito": False}
            else:
                # NEW_FEED_ACCOUNT_SINGLE_PENDING_V7: launch/ramp authority is
                # exactly one candidate per trusted KPI run. The helper also
                # refuses a new feed raise while a current feed item is measuring.
                # This keeps launch exactly1/run and lets the existing daily
                # counters remain the authoritative <=24/day ceiling.
                ramp = hourly_new_feed_low_views_ramp(account_id, max_items=1) or {}
            log(f"[{account_id}] new_feed_ramp={ramp.get('status')} new={ramp.get('new_feed_items')} low={ramp.get('due_low_views')} applied={len(ramp.get('applied') or [])} blocked={len(ramp.get('blocked') or [])} failed={len(ramp.get('failed') or [])} too_soon={ramp.get('too_soon')}")
            if ramp.get("status") == "error":
                reasons = [str(x.get("reason") or x.get("result_status") or "unknown")[:160] for x in (ramp.get("failed") or [])[:5] if isinstance(x, dict)]
                reason_text="; ".join(reasons) or str(ramp.get("reason") or "ошибка разгона новых объявлений")[:500]
                log(f"[{account_id}] new_feed_ramp ERROR reasons={reasons}")
                _save_marketer_account_health(account_id, "incident", reason_text, "new_feed_ramp")
                return "incident"

            # ACCOUNT_LOW_VIEWS_MONEY_LANE_REMOVED_V2: portfolio-wide low-view
            # raises are no longer executed from the generic KPI tick. They are
            # handled only by the bounded staged advisor rollout.
            log(f"[{account_id}] account_low_views_ramp=disabled_use_staged_rollout")

            if float(acc.get("target_leads_per_day") or 0) <= 0:
                log(f"[{account_id}] KPI target не задан — feed promotion работает, KPI-разгон пропущен")
                _save_marketer_account_health(account_id, "ok", "KPI target не задан; безопасный feed promotion доступен", "feed_only")
                return "ok"

            with httpx.Client() as client:
                result = call_tick(client, account_id, allow_stale_non_money=True) if _force_non_money else call_tick(client, account_id)
                # KPI_INVENTORY_GAP_NON_MONEY_FALLBACK_V1: an incomplete live
                # inventory may block the normal KPI check even though an already
                # started content/mapping/publish lifecycle still has safe work to
                # finish. Retry only that skipped kpi_check with the explicit
                # non-money lane; bid/promotion actions remain disabled there.
                if (
                    str(result.get("status") or "").lower() == "skipped"
                    and str(result.get("stage") or "") == "kpi_check"
                ):
                    log(f"[{account_id}] INVENTORY_GAP_NON_MONEY_FALLBACK: обычный KPI check недоступен; продолжаю только content/lifecycle без денежных действий")
                    result = call_tick(client, account_id, allow_stale_non_money=True)
            stage = result.get("stage")
            status = result.get("status")
            log(f"[{account_id}] status={status}, stage={stage}")
            if result.get("reason"):
                log(f"[{account_id}] {result.get('reason')}")
            nested = result.get("result")
            nested_status = str((nested or {}).get("status") or "").lower() if isinstance(nested, dict) else ""
            if nested_status:
                log(f"[{account_id}] result.status={nested_status}")
            # LIFECYCLE_WAIT_NOT_GREEN_V1: waiting for an external publication
            # proof is legitimate, but it is not a completed/green business
            # result. Persist it as deferred so owner reports cannot claim
            # success while Avito still has not proved the change.
            if str(stage or "") in {"confirm_publish", "confirm_rollback", "publish", "rollback_publish"}:
                _policy_wait,_policy_reason=_expected_policy_block(result)
                if nested_status == "blocked" and _policy_wait:
                    log(f"[{account_id}] LIFECYCLE_POLICY_WAIT: stage={stage} reason={_policy_reason}")
                    _save_marketer_account_health(account_id, "deferred", _policy_reason, str(stage or "lifecycle"), next_check_minutes=60)
                    return "deferred"
                if nested_status == "provider_stalled":
                    _lr = str((nested or {}).get("reason") or "Avito publication provider stalled after bounded retries")[:300]
                    log(f"[{account_id}] LIFECYCLE_PROVIDER_STALLED: {_lr}")
                    _save_marketer_account_health(account_id, "incident", _lr, str(stage or "lifecycle"), next_check_minutes=60)
                    return "incident"
                if nested_status in {"waiting", "retry_scheduled", "publish_requested", "rollback_publish_requested"}:
                    _retry_count = int((nested or {}).get("retry_count") or 0) if isinstance(nested, dict) else 0
                    if str(stage or "") == "confirm_rollback" and _retry_count >= 3:
                        _lr = f"rollback confirmation exhausted bounded retries ({_retry_count}); owner-independent repair needs incident handling"
                        log(f"[{account_id}] LIFECYCLE_INCIDENT: {_lr}")
                        _save_marketer_account_health(account_id, "incident", _lr, "confirm_rollback")
                        return "incident"
                    _lr = str((nested or {}).get("reason") or (nested or {}).get("live_status") or nested_status)[:300] if isinstance(nested, dict) else nested_status
                    log(f"[{account_id}] LIFECYCLE_DEFERRED: stage={stage} status={nested_status} reason={_lr}")
                    _save_marketer_account_health(account_id, "deferred", _lr, str(stage or "lifecycle"))
                    return "deferred"
            # MARKETER_WAITING_STAGE_TRUTH_V2: a retryable mapping/publication
            # obligation is healthy infrastructure but NOT a completed business
            # outcome. Keep it non-green until authoritative Avito/feed evidence
            # closes the lifecycle. This prevents owner dashboards from showing
            # "ok" while the account is still waiting for canonical identity or
            # rollback publication.
            _stage_s = str(stage or "")
            _nested_reason = str((nested or {}).get("reason") or ((nested or {}).get("effect") or {}).get("reason") or "")[:300] if isinstance(nested, dict) else ""
            if _stage_s == "mapping_recovery" and nested_status in {"mapping_wait", "waiting_mapping", "blocked"}:
                _lr = _nested_reason or "Ожидается точное сопоставление объявления с canonical feed; BORIS не угадывает соответствие."
                log(f"[{account_id}] MAPPING_DEFERRED: {_lr}")
                _save_marketer_account_health(account_id, "deferred", _lr, _stage_s)
                return "deferred"
            if _stage_s == "rollback_publish" and nested_status in {"blocked", "waiting", "retry_scheduled", "rollback_publish_requested"}:
                _lr = _nested_reason or "Откат подготовлен, но Avito ещё не подтвердил безопасную обратную публикацию."
                log(f"[{account_id}] ROLLBACK_PUBLISH_DEFERRED: {_lr}")
                _save_marketer_account_health(account_id, "deferred", _lr, _stage_s)
                return "deferred"

            # A controller can return HTTP 200 while the business transition
            # itself failed. Treat explicit error/failure as an account failure
            # so systemd/Guardian cannot report a green hourly cycle over a
            # hidden publish or apply error. Expected policy blocks/waits remain
            # non-failures because they are deliberate fail-closed outcomes.
            failed_result, outer_status, nested_status = _controller_result_failed(result)
            if failed_result:
                reason = ""
                if isinstance(nested, dict):
                    reason = str(nested.get("message") or nested.get("reason") or nested.get("detail") or "")[:300]
                reason = reason or str(result.get("reason") or "account-level controller error")[:300]
                log(f"[{account_id}] ACCOUNT_INCIDENT controller_status={outer_status or '-'} nested_status={nested_status or '-'} reason={reason}")
                _save_marketer_account_health(account_id, "incident", reason, str(stage or "controller"))
                return "incident"
            _save_marketer_account_health(account_id, "ok", str(result.get("reason") or "")[:300], str(stage or "tick"))
            return "ok"
        except ProviderDeferred as exc:
            reason=f"Внешний Avito временно отложен: {str(exc)[:300]}"
            log(f"[{account_id}] SAFE_DEFERRED: {reason}")
            _save_marketer_account_health(account_id, "deferred", reason, "provider_deferred")
            return "deferred"
        except httpx.ConnectError as exc:
            # INTERNAL_HA_GRACE_V1: rolling deploys can make both local replicas
            # unavailable for a few seconds. This is not an account failure and
            # must not make the hourly marketer red if health returns promptly.
            # No business mutation is replayed here: we only probe /health, then
            # defer the account to the next scheduler tick.
            recovered = False
            last_probe = ""
            for _attempt in range(8):
                time.sleep(1.0)
                for _base in API_BASES:
                    try:
                        _hr = httpx.get(f"{_base}/health", timeout=2.0)
                        if _hr.status_code == 200:
                            recovered = True
                            last_probe = _base
                            break
                    except Exception:
                        pass
                if recovered:
                    break
            if recovered:
                reason=(f"Локальный API кратковременно перезапускался; health восстановлен через {last_probe}. "
                        "Текущий account transition не переигрываем, продолжим на следующем цикле.")
                log(f"[{account_id}] SAFE_DEFERRED_HA_RECOVERED: {reason}")
                _save_marketer_account_health(account_id, "deferred", reason, "internal_api_recovered")
                return "deferred"
            reason=f"Обе локальные API-реплики недоступны и не восстановились за grace window: {str(exc)[:300]}"
            log(f"[{account_id}] SYSTEMIC_ERROR: {reason}")
            _save_marketer_account_health(account_id, "systemic", reason, "internal_api")
            return "systemic"
        except Exception as exc:
            reason=f"{type(exc).__name__}: {str(exc)[:500]}"
            log(f"[{account_id}] SYSTEMIC_ERROR: {reason}")
            _save_marketer_account_health(account_id, "systemic", reason, "runner_exception")
            return "systemic"

    account_incidents = []
    deferred_accounts = []
    systemic_failures = []
    if not apply_mode:
        for acc in accounts:
            process_account(acc)
    else:
        # This scheduler runs with the deliberately small daemon DB pool
        # (1 connection + 1 overflow). Process accounts serially so one tenant
        # cannot starve the process pool. Account incidents are isolated and
        # persisted; only fleet/system failures make systemd red.
        for acc in accounts:
            outcome=process_account(acc)
            if outcome=="incident": account_incidents.append(acc["account_id"])
            elif outcome=="deferred": deferred_accounts.append(acc["account_id"])
            elif outcome=="systemic": systemic_failures.append(acc["account_id"])

    if account_incidents:
        log(f"ACCOUNT_INCIDENTS={len(account_incidents)} accounts={account_incidents}; fleet cycle continues")
    if deferred_accounts:
        log(f"SAFE_DEFERRED={len(deferred_accounts)} accounts={deferred_accounts}")
    if systemic_failures:
        log(f"FAIL: systemic errors={len(systemic_failures)} accounts={systemic_failures}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
