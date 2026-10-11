#!/usr/bin/env python3
"""Fleet-wide CPX hard-cap reconciler.

Safety invariant: a configured account-scoped hard_max_bid_rub is enforced
against the REAL Avito promotion state before KPI/marketing logic runs.
This process NEVER raises a bid. It may only lower a manual bid or remove an
auto/manual promotion when Avito's minimum cannot fit under the owner's cap.

Read path is bulk-first: getPromotionsByItemIds handles up to 200 items/request,
so large portfolios do not hit the 20 RPM getBids limit. getBids is used only
for actual violators to learn Avito minBid before the write.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import math
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy import text

from app.db.session import SessionLocal
from app.api.avito import get_avito_token, _audit_log
from app.models.storage import Storage
from app.services.reliability import ProviderDeferred
from app.services.avito_account_throttle import account_throttle_remaining, record_account_throttle
from app.services.marketing_money_policy import (
    GLOBAL_HARD_MAX_BID_RUB,
    effective_hard_bid_cap,
)

CORE_ITEMS = "https://api.avito.ru/core/v1/items"
CPX_BASE = "https://api.avito.ru/cpxpromo/1"
PAGE_SIZE = 100
BULK_SIZE = 200
TIMEOUT = 20


def _token(account_id: str) -> str:
    raw = get_avito_token(account_id)
    if isinstance(raw, dict):
        return str(raw.get("access_token") or raw.get("token") or "")
    return str(raw or "")


def _request(client: httpx.Client, method: str, url: str, *, attempts: int = 2, **kwargs):
    # CPX_CAP_SHARED_THROTTLE_EVERY_PROVIDER_CALL_V1: another trusted worker may
    # publish Retry-After after this reconciler's account-level preflight. Check
    # the shared tenant ledger before every provider call, including inventory,
    # promotion detail, lower/remove writes, and verification reads.
    _account_id = str(getattr(client, "_boris_account_id", "") or "").strip()
    if _account_id:
        _shared_retry = int(account_throttle_remaining(_account_id) or 0)
        if _shared_retry > 0:
            raise ProviderDeferred("shared_avito_account_throttle", _shared_retry)
    # CPX_CAP_TRANSPORT_RETRY_BOUNDED_V1: hard-cap reconciliation is safety
    # critical but must not spend minutes hammering a systemically unavailable
    # provider. One bounded retry is enough for read transport failures; writes
    # already force attempts=1 to avoid ambiguous replay.
    last = None
    for attempt in range(attempts):
        try:
            r = client.request(method, url, timeout=TIMEOUT, **kwargs)
            if r.status_code != 429:
                return r
            # CPX_CAP_RETRY_AFTER_FAIL_CLOSED_V1: a confirmed Avito 429 is an
            # account throttle signal, not permission to keep enumerating the
            # tenant. Preserve the provider's Retry-After (seconds or HTTP-date)
            # and defer the entire account instead of retrying before that time.
            retry_after = 30.0
            try:
                raw_retry = str(r.headers.get("Retry-After") or "").strip()
                if raw_retry:
                    try:
                        retry_after = max(0.0, float(raw_retry))
                    except Exception:
                        from email.utils import parsedate_to_datetime
                        when = parsedate_to_datetime(raw_retry)
                        if when.tzinfo is None:
                            when = when.replace(tzinfo=timezone.utc)
                        retry_after = max(0.0, (when.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds())
            except Exception:
                retry_after = 30.0
            raise ProviderDeferred("avito_cpx_rate_limit", max(5, int(math.ceil(retry_after))))
        except ProviderDeferred:
            raise
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(min(15.0, 2.0 * (attempt + 1)))
                continue
        break
    if last:
        raise last
    raise RuntimeError("request failed")


def _active_item_ids(client: httpx.Client, headers: dict) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    page = 1
    while page <= 100:
        r = _request(client, "GET", CORE_ITEMS, headers=headers,
                     params={"per_page": PAGE_SIZE, "page": page, "status": "active"})
        if r.status_code != 200:
            raise RuntimeError(f"core_items_http_{r.status_code}:{r.text[:120]}")
        resources = (r.json() or {}).get("resources") or []
        added = 0
        for item in resources:
            try:
                iid = int(item.get("id"))
            except Exception:
                continue
            if iid not in seen:
                seen.add(iid)
                out.append(iid)
                added += 1
        if len(resources) < PAGE_SIZE or added == 0:
            break
        page += 1
    return out


def _stored_inventory_ids(db, account_id: str, limit_days: int = 14) -> tuple[list[int], int]:
    """Fallback inventory from recent persisted Avito snapshots.

    This path is used only when Avito throttles live item enumeration. We union
    recent snapshots because a single intraday snapshot can be page-truncated.
    `expected` is the largest reported items_count and lets the caller refuse a
    false PASS when persisted coverage is materially incomplete.
    """
    fresh = SessionLocal()
    try:
        rows = fresh.execute(text("""
            SELECT key,value
              FROM storage
             WHERE account_id=:a AND key LIKE 'daily_stats:%'
             ORDER BY key DESC
             LIMIT :n
        """), {"a": account_id, "n": int(limit_days)}).fetchall()
    finally:
        fresh.close()
    seen: set[int] = set()
    expected = 0
    for _key, raw in rows:
        try:
            snap = json.loads(raw or "{}")
        except Exception:
            continue
        if not isinstance(snap, dict):
            continue
        try:
            expected = max(expected, int(snap.get("items_count") or 0))
        except Exception:
            pass
        for item in snap.get("items") or []:
            try:
                iid = int(item.get("id") or item.get("itemId"))
            except Exception:
                continue
            seen.add(iid)
    return sorted(seen), expected


def _bulk_promotions(client: httpx.Client, headers: dict, ids: list[int]) -> list[dict]:
    out: list[dict] = []
    for pos in range(0, len(ids), BULK_SIZE):
        chunk = ids[pos:pos + BULK_SIZE]
        r = _request(client, "POST", f"{CPX_BASE}/getPromotionsByItemIds",
                     headers={**headers, "Content-Type": "application/json"},
                     json={"itemIDs": chunk})
        if r.status_code != 200:
            raise RuntimeError(f"promotions_http_{r.status_code}:{r.text[:120]}")
        out.extend((r.json() or {}).get("items") or [])
    return out


def _get_bid_detail(client: httpx.Client, headers: dict, item_id: int) -> dict:
    r = _request(client, "GET", f"{CPX_BASE}/getBids/{item_id}", headers=headers)
    if r.status_code != 200:
        raise RuntimeError(f"get_bids_http_{r.status_code}:{r.text[:120]}")
    return r.json() or {}


def _remove(client: httpx.Client, headers: dict, item_id: int):
    return _request(client, "POST", f"{CPX_BASE}/remove",
                    headers={**headers, "Content-Type": "application/json"},
                    json={"itemID": item_id}, attempts=1)


def _set_manual(client: httpx.Client, headers: dict, item_id: int,
                action_type_id: int, bid_rub: int):
    return _request(client, "POST", f"{CPX_BASE}/setManual",
                    headers={**headers, "Content-Type": "application/json"},
                    json={"actionTypeID": int(action_type_id or 5),
                          "bidPenny": int(bid_rub) * 100,
                          "itemID": int(item_id)}, attempts=1)


def _save_runtime(db, account_id: str, payload: dict):
    """CPX_CAP_RUNTIME_DB_SESSION_ISOLATION_V2.

    Provider enumeration may take tens of seconds. Never trust a DB connection
    that was checked out before those network calls; persist runtime through a
    fresh short session and retry once on invalidation.
    """
    value = json.dumps(payload, ensure_ascii=False)
    for attempt in range(2):
        fresh = SessionLocal()
        try:
            row = fresh.query(Storage).filter(
                Storage.account_id == account_id,
                Storage.key == "cpx_cap_reconcile_runtime",
            ).first()
            if row:
                row.value = value
            else:
                fresh.add(Storage(account_id=account_id, key="cpx_cap_reconcile_runtime", value=value))
            fresh.commit()
            return
        except Exception:
            try: fresh.rollback()
            except Exception: pass
            if attempt:
                raise
        finally:
            fresh.close()


def _owner_manual_bid_overrides(db, account_id: str) -> dict[int, dict]:
    """Explicit owner-set bids are observed but never auto-reconciled downward."""
    fresh = SessionLocal()
    try:
        row = fresh.query(Storage).filter(
            Storage.account_id == account_id,
            Storage.key == "owner_manual_bid_overrides",
        ).first()
        if not row or not row.value:
            return {}
        try:
            data = json.loads(row.value) or {}
        except Exception:
            return {}
        items = data.get("items") if isinstance(data, dict) else {}
        # OWNER_OVERRIDE_UNTIL_BUDGET_AUTH_V1: incident-rescue manual bids stay
        # protected from autonomous cap reconciliation until the owner explicitly
        # authorizes a new positive daily budget through authenticated KPI settings.
        kpi = {}
        try:
            krow = fresh.query(Storage).filter(
                Storage.account_id == account_id,
                Storage.key == "kpi_settings",
            ).order_by(Storage.id.desc()).first()
            kpi = json.loads(krow.value or "{}") if krow else {}
        except Exception:
            kpi = {}
        try:
            daily = float(kpi.get("daily_budget_limit_rub") or 0)
        except Exception:
            daily = 0.0
        auth = kpi.get("daily_budget_authorization") if isinstance(kpi.get("daily_budget_authorization"), dict) else {}
        budget_proven = bool(
            daily > 0
            and str(auth.get("policy_version") or "") == "MONEY_BUDGET_OWNER_PROVENANCE_V1"
            and int(auth.get("authorized_by_user_id") or 0) > 0
            and float(auth.get("daily_budget_limit_rub") or -1) == daily
            and str(auth.get("source") or "") == "authenticated_set_kpi_settings"
        )
        out = {}
        if isinstance(items, dict):
            for raw_id, entry in items.items():
                if not isinstance(entry, dict) or not entry.get("active", True):
                    continue
                if str(entry.get("scope") or "") == "until_positive_daily_budget" and budget_proven:
                    continue
                try: out[int(raw_id)] = entry
                except Exception: continue
        return out
    finally:
        fresh.close()


def _accounts(db, account_filter: str | None):
    rows = db.execute(text("""
      SELECT a.account_id
        FROM accounts a
       WHERE a.avito_client_id IS NOT NULL AND a.avito_client_secret IS NOT NULL
         AND NOT EXISTS (
             SELECT 1 FROM reliability_kill_switches r
              WHERE r.account_id=a.account_id AND r.blocked=true
                AND r.module IN ('actions','marketing','background')
         )
       ORDER BY a.account_id
    """)).fetchall()
    result = []
    for (account_id,) in rows:
        if account_filter and account_id != account_filter:
            continue
        # One authoritative money policy: account settings may tighten the red
        # line, but can never expand the global ceiling.
        cap = effective_hard_bid_cap(db, str(account_id))
        result.append((str(account_id), float(cap)))
    return result


def reconcile_account(db, account_id: str, cap_rub: float, apply: bool) -> dict:
    started = datetime.now(timezone.utc).isoformat()
    # CPX_CAP_SAFETY_LOWER_LANE_V1:
    # This reconciler never raises spend. It only removes auto-promotion or
    # lowers a manual bid that is already above the hard cap. Safety reductions
    # remain allowed even when the paid marketing period is expired/unknown so
    # BORIS never leaves avoidable client spend running after entitlement loss.
    apply_requested = bool(apply)
    apply = apply_requested
    result = {
        "account_id": account_id,
        "hard_max_bid_rub": cap_rub,
        "apply_requested": apply_requested,
        "apply": apply,
        "safety_lane": "lower_only",
        "can_increase_spend": False,
        "started_at": started,
        "status": "running",
        "active_items": 0,
        "promotions": 0,
        "manual_over_cap": 0,
        "auto_promotions": 0,
        "lowered": 0,
        "removed": 0,
        "failed": 0,
        "remaining_over_cap": 0,
        "errors": [],
    }
    # AVITO_SHARED_ACCOUNT_THROTTLE_PRECHECK_V1: respect Retry-After recorded by
    # any trusted BORIS stage before touching Avito again for this tenant.
    _shared_retry = account_throttle_remaining(account_id)
    if _shared_retry > 0:
        result.update(status="deferred", retry_after_seconds=int(_shared_retry),
                      errors=["provider_deferred:shared_avito_account_throttle"])
        _save_runtime(db, account_id, result)
        return result
    try:
        tok = _token(account_id)
    except ProviderDeferred as exc:
        result.update(status="deferred", errors=[f"provider_deferred:{str(exc)[:160]}"])
        _save_runtime(db, account_id, result)
        return result
    if not tok:
        result.update(status="blocked", errors=["no_avito_token"])
        _save_runtime(db, account_id, result)
        return result
    headers = {"Authorization": f"Bearer {tok}"}
    with httpx.Client() as client:
        # Bind the internal account identity to this one-account client so the
        # centralized _request() guard can stop every subsequent provider call
        # when any sibling worker publishes tenant Retry-After mid-cycle.
        client._boris_account_id = account_id
        try:
            inventory_complete = True
            try:
                ids = _active_item_ids(client, headers)
                result["inventory_source"] = "live_core"
                result["inventory_expected"] = len(ids)
                result["inventory_ids"] = len(ids)
                result["inventory_coverage_pct"] = 100.0
            except Exception as inventory_exc:
                if "429" not in str(inventory_exc):
                    raise
                ids, expected = _stored_inventory_ids(db, account_id)
                if not ids:
                    raise
                inventory_complete = (expected <= 0 or len(ids) >= max(1, int(math.ceil(expected * 0.95))))
                result["inventory_source"] = "stored_snapshot_fallback"
                result["inventory_live_error"] = "HTTP 429"
                result["inventory_expected"] = int(expected or len(ids))
                result["inventory_ids"] = len(ids)
                result["inventory_coverage_pct"] = round(
                    min(100.0, (len(ids) / float(expected or len(ids))) * 100.0), 2
                )
            result["inventory_complete"] = bool(inventory_complete)
            result["active_items"] = int(result.get("inventory_expected") or len(ids))
            promos = _bulk_promotions(client, headers, ids) if ids else []
            result["promotions"] = len(promos)
            violations = []
            autos = []
            for p in promos:
                try:
                    iid = int(p.get("itemID"))
                except Exception:
                    continue
                manual = p.get("manualPromotion") or {}
                auto = p.get("autoPromotion") or {}
                bid = float(manual.get("bidPenny") or 0) / 100.0
                if bid > cap_rub + 1e-9:
                    violations.append((iid, bid, int(p.get("actionTypeID") or 5)))
                if auto:
                    autos.append((iid, auto))
            owner_overrides = _owner_manual_bid_overrides(db, account_id)
            if owner_overrides:
                protected = []
                remaining_violations = []
                for iid, bid, action_type in violations:
                    if iid in owner_overrides:
                        protected.append((iid, bid, action_type))
                    else:
                        remaining_violations.append((iid, bid, action_type))
                violations = remaining_violations
                result["owner_manual_overrides"] = len(owner_overrides)
                result["owner_manual_over_cap"] = len(protected)
                result["owner_manual_items"] = [iid for iid, _bid, _action in protected]
            else:
                result["owner_manual_overrides"] = 0
                result["owner_manual_over_cap"] = 0
                result["owner_manual_items"] = []
            result["manual_over_cap"] = len(violations)
            result["auto_promotions"] = len(autos)

            if apply:
                # CPX_CAP_VERIFY_BEFORE_AUDIT_V1: provider HTTP success is only
                # acceptance. Defer counters/audit until the authoritative bulk
                # read-back proves the requested lower/remove state.
                pending_verify = {}

                # Automatic CPX has no deterministic per-action bid ceiling, so it
                # is incompatible with an explicit hard bid cap. Remove it; the
                # normal marketer may later enable MANUAL promotion under the cap.
                for iid, _auto in autos:
                    rr = _remove(client, headers, iid)
                    if rr.status_code == 200:
                        pending_verify[int(iid)] = {"kind": "remove_auto"}
                    else:
                        result["failed"] += 1
                        result["errors"].append(f"auto_remove:{iid}:http_{rr.status_code}")

                for iid, old_bid, action_type in violations:
                    try:
                        detail = _get_bid_detail(client, headers, iid)
                        manual = detail.get("manual") or {}
                        min_bid_rub = float(manual.get("minBidPenny") or 0) / 100.0
                        if min_bid_rub > cap_rub + 1e-9:
                            wr = _remove(client, headers, iid)
                            if wr.status_code == 200:
                                pending_verify[int(iid)] = {
                                    "kind": "remove_manual",
                                    "old_bid": float(old_bid),
                                    "min_bid_rub": float(min_bid_rub),
                                }
                            else:
                                raise RuntimeError(f"remove_http_{wr.status_code}")
                        else:
                            target = max(1, int(math.floor(cap_rub)))
                            wr = _set_manual(client, headers, iid, action_type, target)
                            if wr.status_code == 200:
                                pending_verify[int(iid)] = {
                                    "kind": "lower_manual",
                                    "old_bid": float(old_bid),
                                    "target": int(target),
                                }
                            else:
                                raise RuntimeError(f"set_http_{wr.status_code}:{wr.text[:80]}")
                    except ProviderDeferred:
                        # CPX_CAP_MANUAL_THROTTLE_PROPAGATE_V1: do not swallow an
                        # account throttle as an item failure and continue with
                        # the next listing. Bubble it to the account-level defer.
                        raise
                    except Exception as exc:
                        result["failed"] += 1
                        result["errors"].append(f"manual:{iid}:{type(exc).__name__}:{str(exc)[:100]}")

                # Verify using the same high-throughput authoritative endpoint.
                verify = _bulk_promotions(client, headers, ids) if ids else []
                verify_map = {}
                remaining = 0
                auto_remaining = 0
                for p in verify:
                    try:
                        verify_iid = int(p.get("itemID"))
                    except Exception:
                        verify_iid = 0
                    if verify_iid:
                        verify_map[verify_iid] = p
                    bid = float(((p.get("manualPromotion") or {}).get("bidPenny") or 0)) / 100.0
                    if bid > cap_rub + 1e-9 and verify_iid not in owner_overrides:
                        remaining += 1
                    if p.get("autoPromotion"):
                        auto_remaining += 1

                for iid, expected in pending_verify.items():
                    p = verify_map.get(int(iid))
                    if not isinstance(p, dict):
                        result["failed"] += 1
                        result["errors"].append(f"verify_missing:{iid}")
                        continue
                    kind = str(expected.get("kind") or "")
                    manual_promo = p.get("manualPromotion") if isinstance(p.get("manualPromotion"), dict) else None
                    auto_promo = p.get("autoPromotion") if isinstance(p.get("autoPromotion"), dict) else None
                    if kind == "remove_auto":
                        if auto_promo:
                            result["failed"] += 1
                            result["errors"].append(f"verify_auto_remove_not_applied:{iid}")
                            continue
                        result["removed"] += 1
                        _audit_log(account_id, "cpx_cap_remove_auto",
                                   f"объявление {iid}: авто-продвижение снято из-за hard cap {cap_rub:.0f} ₽; подтверждено Avito",
                                   "boris_bid_cap_guard")
                    elif kind == "remove_manual":
                        if manual_promo:
                            result["failed"] += 1
                            result["errors"].append(f"verify_manual_remove_not_applied:{iid}")
                            continue
                        result["removed"] += 1
                        _audit_log(account_id, "cpx_cap_remove",
                                   f"объявление {iid}: ставка {expected.get('old_bid',0):.0f} ₽ снята, Avito minBid {expected.get('min_bid_rub',0):.2f} ₽ выше hard cap {cap_rub:.0f} ₽; подтверждено Avito",
                                   "boris_bid_cap_guard")
                    elif kind == "lower_manual":
                        try:
                            observed_penny = int((manual_promo or {}).get("bidPenny"))
                        except Exception:
                            observed_penny = -1
                        intended_penny = int(expected.get("target") or 0) * 100
                        if observed_penny != intended_penny:
                            result["failed"] += 1
                            result["errors"].append(
                                f"verify_manual_lower_mismatch:{iid}:intended={intended_penny}:observed={observed_penny}"
                            )
                            continue
                        result["lowered"] += 1
                        _audit_log(account_id, "cpx_cap_lower",
                                   f"объявление {iid}: ставка {expected.get('old_bid',0):.0f}→{expected.get('target',0)} ₽, hard cap {cap_rub:.0f} ₽; подтверждено Avito",
                                   "boris_bid_cap_guard")

                result["remaining_over_cap"] = remaining
                result["auto_promotions_remaining"] = auto_remaining
            else:
                result["remaining_over_cap"] = len(violations)
                result["auto_promotions_remaining"] = len(autos)

            if not result.get("inventory_complete", True):
                result["status"] = "needs_retry"
            else:
                result["status"] = "pass" if (
                    result["remaining_over_cap"] == 0
                    and result.get("auto_promotions_remaining", 0) == 0
                    and result["failed"] == 0
                ) else ("would_fix" if not apply else "fail")
        except ProviderDeferred as exc:
            # CPX_CAP_ACCOUNT_THROTTLE_STOP_V1: one confirmed 429 ends all
            # remaining inventory/promotion pressure for this tenant in the
            # current cycle. Publish that Retry-After to the shared tenant ledger
            # so later trusted stages do not immediately pressure the same account.
            _shared_retry = record_account_throttle(
                account_id, int(exc.retry_after_seconds), source=f"cpx_cap:{exc.dependency}"
            )
            result["status"] = "deferred"
            result["retry_after_seconds"] = int(_shared_retry or exc.retry_after_seconds)
            result["errors"].append(f"provider_deferred:{exc.dependency}")
        except Exception as exc:
            # CPX_CAP_TRANSPORT_DEFERRED_V2: provider/network transport loss is
            # not evidence that the hard-cap policy itself failed. Keep the
            # account fail-closed, publish a deferred result and let the fleet
            # transport breaker stop further provider pressure. The next trusted
            # cycle retries naturally; no raise is possible in this lane.
            _exc_name = type(exc).__name__
            if _exc_name in {"ConnectTimeout", "ReadTimeout", "ConnectError", "PoolTimeout"}:
                result["status"] = "deferred"
                result["errors"].append(f"provider_deferred:{_exc_name}:{str(exc)[:150]}")
            else:
                result["status"] = "error"
                result["failed"] += 1
                result["errors"].append(f"{_exc_name}:{str(exc)[:180]}")

    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    _save_runtime(db, account_id, result)
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--account", default="")
    args = ap.parse_args()
    lock_file = open("/root/BORIS/backend/run/cpx_cap_reconciler.lock", "w")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("CPX_CAP_RECONCILE_SKIP overlap", flush=True)
        return 0
    discovery_db = SessionLocal()
    failures = []
    try:
        accounts = _accounts(discovery_db, args.account.strip() or None)
    finally:
        discovery_db.close()
    try:
        print(f"CPX_CAP_RECONCILE accounts={len(accounts)} apply={args.apply}", flush=True)
        consecutive_transport_failures = 0
        for account_id, cap in accounts:
            # CPX_CAP_ACCOUNT_DB_SESSION_ISOLATION_V2: each tenant gets its own
            # short-lived ORM session. Runtime/fallback reads themselves reopen
            # fresh sessions after provider I/O, so one stale SSL connection can
            # never abort the remaining fleet.
            account_db = SessionLocal()
            try:
                out = reconcile_account(account_db, account_id, cap, args.apply)
            finally:
                # CPX_CAP_ACCOUNT_DB_CLOSE_FAILSAFE_V1: provider enumeration can
                # outlive an idle PostgreSQL SSL session. Runtime persistence is
                # already done through a fresh isolated session, so a stale
                # rollback/close must never abort reconciliation of the rest of
                # the fleet after an account result was safely determined.
                try:
                    account_db.close()
                except Exception as close_exc:
                    print(f"CPX_CAP_DB_CLOSE_WARN account={account_id} {type(close_exc).__name__}:{str(close_exc)[:160]}", flush=True)
            print(json.dumps(out, ensure_ascii=False), flush=True)
            if out.get("status") not in {"pass", "would_fix", "deferred"} and not (
                out.get("status") == "blocked" and (out.get("errors") or []) == ["no_avito_token"]
            ):
                failures.append(account_id)
            # CPX_CAP_FLEET_TRANSPORT_BREAKER_V1: repeated connect/read timeouts
            # across different tenants are systemic transport evidence, not a
            # reason to spend the rest of the hourly cycle retrying every account.
            # Stop this non-raising reconciliation stage after two consecutive
            # transport failures; the next trusted hourly run retries naturally.
            _transport_error = any(
                any(tag in str(err) for tag in ("ConnectTimeout", "ReadTimeout", "ConnectError"))
                for err in (out.get("errors") or [])
            )
            if _transport_error:
                consecutive_transport_failures += 1
            else:
                consecutive_transport_failures = 0
            if consecutive_transport_failures >= 2:
                print(
                    f"CPX_CAP_TRANSPORT_BREAKER_OPEN account={account_id} "
                    f"consecutive={consecutive_transport_failures}; remaining fleet deferred to next hourly",
                    flush=True,
                )
                break
    finally:
        try:
            lock_file.close()
        except Exception:
            pass
    print(f"CPX_CAP_RECONCILE_DONE failures={failures}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
