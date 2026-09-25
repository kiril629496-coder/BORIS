"""Fresh stats collector for the production virtual marketer.

One collector serves both KPI planning and money safety. Besides paid/KPI
accounts it includes every connected Avito account that already has an active
daily money limit, so budget guards never depend on stale snapshots.
"""
import os
import sys
import time
import json
import httpx
import psycopg2

def _load_dsn():
    try:
        with open(".env", encoding="utf-8") as fh:
            for _l in fh:
                if _l.startswith("DATABASE_URL"):
                    return _l.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        return None
    return None

DB_DSN = _load_dsn()
API_BASES = tuple(
    x.strip().rstrip("/") for x in os.environ.get(
        "BORIS_INTERNAL_API_BASES",
        "http://127.0.0.1:8000,http://127.0.0.1:8001",
    ).split(",") if x.strip()
) or ("http://127.0.0.1:8000", "http://127.0.0.1:8001")


def _provider_alias_score(account_id, kpi_raw, mandate_daily, canonical_account_id=None):
    """Rank internal aliases for one real Avito tenant without provider I/O."""
    try:
        cfg = json.loads(kpi_raw or "{}") if kpi_raw else {}
        if not isinstance(cfg, dict):
            cfg = {}
    except Exception:
        cfg = {}
    try:
        target = float(cfg.get("target_leads_per_day") or 0)
    except Exception:
        target = 0.0
    try:
        configured_daily = float(cfg.get("daily_budget_limit_rub") or 0)
    except Exception:
        configured_daily = 0.0
    try:
        mandate_daily = float(mandate_daily or 0)
    except Exception:
        mandate_daily = 0.0
    return (
        1 if canonical_account_id and str(account_id) == str(canonical_account_id) else 0,
        1 if target > 0 else 0,
        1 if mandate_daily > 0 else 0,
        1 if configured_daily > 0 else 0,
    )


def _dedupe_provider_alias_rows(rows, canonical_by_uid=None):
    """Return one internal account per real Avito user_id.

    AVITO_STATS_PROVIDER_ALIAS_DEDUPE_V1: historical BORIS aliases can share one
    Avito user_id (for example an old inbox scope and the current business
    account). Calling the provider once per alias creates self-inflicted 429s.
    Prefer the active account_slots binding; otherwise prefer the alias with
    current KPI/money configuration. Accounts without avito_user_id remain
    independent.
    """
    canonical_by_uid = {str(k): str(v) for k, v in (canonical_by_uid or {}).items() if k and v}
    groups = {}
    for account_id, avito_user_id, kpi_raw, mandate_daily in rows or []:
        aid = str(account_id or "").strip()
        if not aid:
            continue
        uid = str(avito_user_id or "").strip()
        provider_key = "uid:" + uid if uid else "account:" + aid
        groups.setdefault(provider_key, []).append((aid, uid, kpi_raw, mandate_daily))

    selected = []
    for candidates in groups.values():
        uid = candidates[0][1] if candidates else ""
        canonical = canonical_by_uid.get(uid)
        candidates = sorted(
            candidates,
            key=lambda row: tuple(-x for x in _provider_alias_score(row[0], row[2], row[3], canonical)) + (row[0],),
        )
        selected.append(candidates[0][0])
    return sorted(selected)

def get_all_account_ids():
    # ALL_CONNECTED_STATS_SCOPE_V1: statistics/health observation is fleet-wide,
    # but provider observation is one external read per real Avito tenant, not
    # one read per historical BORIS alias.
    # Money-safety coverage is wider than money authorization: all connected
    # provider tenants are observed read-only, while every money write remains
    # independently gated by paid period, explicit mandate, budget and hard caps.
    conn = psycopg2.connect(DB_DSN)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT ON (avito_user_id)
                   avito_user_id, account_id
              FROM account_slots
             WHERE avito_user_id IS NOT NULL
               AND btrim(avito_user_id) <> ''
               AND account_id IS NOT NULL
               AND status IN ('connected','readonly')
             ORDER BY avito_user_id, updated_at DESC NULLS LAST, id DESC
        """)
        canonical_by_uid = {
            str(uid): str(account_id)
            for uid, account_id in cur.fetchall()
            if uid and account_id
        }

        cur.execute("""
            SELECT a.account_id,
                   a.avito_user_id,
                   (SELECT s.value FROM storage s
                     WHERE s.account_id=a.account_id AND s.key='kpi_settings'
                     ORDER BY s.id DESC LIMIT 1) AS kpi_raw,
                   (SELECT m.daily_budget_rub FROM money_mandates m
                     WHERE a.account_id=ANY(m.account_scope)
                       AND m.status='active' AND m.revoked_at IS NULL
                       AND (m.valid_from IS NULL OR m.valid_from<=now())
                       AND (m.valid_until IS NULL OR m.valid_until>now())
                       AND COALESCE(m.daily_budget_rub,0)>0
                     ORDER BY m.id DESC LIMIT 1) AS mandate_daily
              FROM accounts a
             WHERE a.avito_client_id IS NOT NULL
               AND a.avito_client_secret IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM reliability_kill_switches r
                    WHERE r.account_id=a.account_id AND r.blocked=true
                      AND r.module IN ('actions','marketing','background')
               )
             ORDER BY a.account_id
        """)
        rows = cur.fetchall()
        ids = _dedupe_provider_alias_rows(rows, canonical_by_uid)
        if len(ids) < len(rows):
            print(
                f"AVITO_STATS_PROVIDER_ALIAS_DEDUPE selected={len(ids)} raw_aliases={len(rows)} "
                f"deduped={len(rows)-len(ids)}",
                flush=True,
            )
        return ids
    finally:
        conn.close()


def _collect_one(account_id: str, attempts: int = 20):
    """Collect one snapshot through the local API replica pair.

    GET is read-only, so connect failover and bounded retry are safe. The
    retry window intentionally spans a normal HA rolling restart, preventing
    a transient deploy overlap from turning the whole marketer cycle red.
    Any actual HTTP response is authoritative and is not hidden by another node.
    """
    last_connect_error = None
    for attempt in range(max(1, int(attempts))):
        for base in API_BASES:
            try:
                r = httpx.get(
                    f"{base}/api/avito/collect_stats",
                    params={"account_id": account_id},
                    timeout=60,
                )
                r.raise_for_status()
                if base != API_BASES[0]:
                    print(f"[{account_id}] internal HA failover succeeded via {base}", flush=True)
                return r.json()
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
                    httpx.RemoteProtocolError) as exc:
                # GET is read-only, so transport resets/timeouts are safe to retry
                # through the HA peer. Keep the failure tenant-scoped.
                last_connect_error = exc
                print(f"[{account_id}] internal API {base} transient {type(exc).__name__} before GET", flush=True)
                continue
        if attempt < attempts - 1:
            time.sleep(1.0)
    if last_connect_error is not None:
        raise last_connect_error
    raise RuntimeError("BORIS internal API replicas are unavailable")


def main():
    account_ids = get_all_account_ids()
    print(f"Найдено аккаунтов: {len(account_ids)}")
    failed = []
    succeeded = []
    provider_deferred = []
    hard_failed = []
    for account_id in account_ids:
        try:
            data = _collect_one(account_id)
            # STATS_TENANT_DEFERRED_SELFHEAL_V1 / STATS_TENANT_THROTTLE_HANDOFF_V2: a provider 429 is already
            # persisted in the shared cross-process tenant Retry-After ledger by
            # the canonical Avito API. Do NOT sleep/re-probe inside this fleet
            # sweep: that stalls unrelated clients and can recreate provider
            # pressure as soon as a short Retry-After expires. Preserve the last
            # known reporting snapshot, mark only this tenant deferred, and let
            # the next scheduled collector cycle self-heal after the ledger TTL.
            # Money-increasing actions remain fail-closed meanwhile because the
            # dedicated spending row carries fallback_status/fallback_at.
            if data.get("status") == "degraded" and data.get("reason") == "avito_account_throttled":
                try: wait=max(1,int(data.get("retry_after_seconds") or 0))
                except Exception: wait=30
                provider_deferred.append(account_id)
                print(f"[{account_id}] SELF_HEAL_DEFER provider throttle retry_after={wait}s next_scheduled_cycle=1", flush=True)
            if data.get("status") == "ok":
                snap = data.get("snapshot", {})
                spending = snap.get("spending") if isinstance(snap.get("spending"), dict) else {}
                if str(spending.get("status") or "") != "ok":
                    failed.append(account_id)
                    hard_failed.append(account_id)
                    print(f"[{account_id}] НЕПОЛНЫЕ ДАННЫЕ: расходы не подтверждены ({spending.get('status')})")
                else:
                    succeeded.append(account_id)
                    print(f"[{account_id}] OK — объявлений: {snap.get('items_count')}, баланс: {snap.get('balance')}")
            else:
                failed.append(account_id)
                if not (data.get("status") == "degraded" and data.get("reason") == "avito_account_throttled"):
                    hard_failed.append(account_id)
                print(f"[{account_id}] НЕСВЕЖИЕ ДАННЫЕ: status={data.get('status')} reason={data.get('reason')} message={data.get('message')}")
        except Exception as e:
            failed.append(account_id)
            hard_failed.append(account_id)
            print(f"[{account_id}] ИСКЛЮЧЕНИЕ: {type(e).__name__}: {e}")
    if failed and not succeeded and account_ids:
        # DAILY_STATS_PROVIDER_DEFERRED_EXIT_V1: a fleet-wide provider 429 is an
        # external wait, not an internal BORIS crash. Return a dedicated rc=75 so
        # the hourly owner can keep money fail-closed without painting the service
        # red. Any transport/storage/non-throttle failure remains rc=1.
        if len(provider_deferred) == len(account_ids) and not hard_failed:
            print(f"DAILY_STATS_SYSTEMIC_DEFERRED reason=avito_account_throttled accounts={provider_deferred}", flush=True)
            return 75
        print(f"DAILY_STATS_SYSTEMIC_FAIL accounts={failed} hard_failed={hard_failed}", flush=True)
        return 1
    if failed:
        # Tenant isolation: healthy accounts may continue. Every downstream KPI
        # account independently checks _fresh_stats_ready(), so failed tenants
        # remain money-safe instead of blocking the entire fleet.
        print(f"DAILY_STATS_PARTIAL ok={len(succeeded)} failed={len(failed)} failed_accounts={failed}", flush=True)
        return 0
    print(f"DAILY_STATS_PASS ok={len(succeeded)}", flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
