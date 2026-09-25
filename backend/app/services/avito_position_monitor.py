"""BORIS Avito position monitor.

Read-only production contour for the search-position signal shown in Avito Pro.
Official core/v1/items does not expose the cabinet position field, therefore the
monitor consumes only an already authenticated, identity-verified Browser Gateway
READ session. It never scrapes public search, never bypasses Avito protections and
never performs promotion/money actions itself.
"""
from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from app.db.session import SessionLocal
from app.models.storage import Storage
from app.services.exception_observability import observe_suppressed

LATEST_KEY = "avito_position_latest"
HEALTH_KEY = "avito_position_health"
SIGNAL_KEY = "avito_position_marketer_signal"
JOB_PURPOSE = "auto_position_monitor"
DEFAULT_INTERVAL_SEC = 15 * 60
MAX_ITEMS_PER_PAGE = 50


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _now()).isoformat()


def _json_load(raw: str | None, default):
    try:
        value = json.loads(raw or "")
        return value
    except Exception:
        return default


def _storage_get(db, account_id: str, key: str) -> dict:
    row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == key).order_by(Storage.id.desc()).first()
    return _json_load(row.value, {}) if row else {}


def _storage_put(db, account_id: str, key: str, payload: dict) -> None:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == key).order_by(Storage.id.desc()).first()
    if row:
        row.value = raw
    else:
        db.add(Storage(account_id=account_id, key=key, value=raw))
    db.commit()


def ensure_schema(db) -> None:
    db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
    db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
    db.execute(text('SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'))
    db.commit()


def active_ai_accounts() -> list[str]:
    """All real accounts whose marketer autopilot is enabled."""
    db = SessionLocal()
    try:
        rows = db.query(Storage).filter(Storage.key == "autopilot_settings").all()
        result: list[str] = []
        for row in rows:
            cfg = _json_load(row.value, {})
            if str(cfg.get("mode") or "") not in {"goal_auto", "always_auto"}:
                continue
            aid = str(row.account_id or "").strip()
            if not aid or aid.startswith("qa") or aid.startswith("user:"):
                continue
            result.append(aid)
        return sorted(set(result))
    finally:
        db.close()


def _latest_items(db, account_id: str) -> list[dict]:
    today = _now().date().isoformat()
    row = db.query(Storage).filter(
        Storage.account_id == account_id,
        Storage.key == f"daily_stats:{today}",
    ).order_by(Storage.id.desc()).first()
    if not row:
        row = db.query(Storage).filter(
            Storage.account_id == account_id,
            Storage.key.like("daily_stats:%"),
        ).order_by(Storage.id.desc()).first()
    snap = _json_load(row.value, {}) if row else {}
    items = snap.get("items") if isinstance(snap.get("items"), list) else []
    return [x for x in items if isinstance(x, dict) and str(x.get("status") or "active") == "active"]


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _position_from_excerpt(excerpt: str) -> tuple[int | None, int | None, str, str]:
    compact = str(excerpt or "")
    hidden = re.search(r"Скрыто\s*из\s*поисковой\s*выдачи", compact, re.I)
    # Prefer the cabinet's explicit "N место ↑/↓K" presentation.
    m = re.search(r"(?<!\d)(\d{1,4})\s*место(?:\s*([↑↓])\s*(\d{1,4}))?", compact, re.I)
    if m:
        position = int(m.group(1))
        arrow = m.group(2) or ""
        magnitude = int(m.group(3) or 0)
        delta = magnitude if arrow == "↑" else (-magnitude if arrow == "↓" else 0)
        trend = "up" if delta > 0 else ("down" if delta < 0 else "flat")
        return position, delta, trend, "visible"
    if hidden:
        return None, None, "hidden", "hidden"
    return None, None, "unknown", "unknown"


def parse_pro_items_page(account_id: str, page_text: str, items: list[dict]) -> list[dict]:
    """Map Avito Pro card text to canonical item IDs using exact active titles.

    Avito's read_page result is flattened text. We therefore only accept a title
    match when that normalized title is unique in the active inventory. Ambiguous
    duplicate titles stay unmeasured instead of being assigned to the wrong item.
    """
    raw = str(page_text or "")
    lowered = raw.casefold()
    title_counts: dict[str, int] = {}
    for item in items:
        key = _norm(item.get("title") or "")
        if key:
            title_counts[key] = title_counts.get(key, 0) + 1

    found: list[dict] = []
    for item in items[:2000]:
        title = str(item.get("title") or "").strip()
        key = _norm(title)
        if not title or title_counts.get(key) != 1:
            continue
        idx = lowered.find(title.casefold())
        if idx < 0:
            continue
        # The position chip is rendered on the same card. Bound the window before
        # the next known title whenever possible to avoid stealing another card's
        # position value.
        end = min(len(raw), idx + max(500, len(title) + 700))
        next_indices = []
        for other in items:
            other_title = str(other.get("title") or "").strip()
            if not other_title or other_title == title:
                continue
            oi = lowered.find(other_title.casefold(), idx + len(title))
            if oi >= 0:
                next_indices.append(oi)
        if next_indices:
            end = min(end, min(next_indices))
        excerpt = raw[idx:end]
        position, delta, trend, visibility = _position_from_excerpt(excerpt)
        # Keep explicit hidden rows too; unknown means the card was found but the
        # cabinet did not expose a position in this rendering/window.
        found.append({
            "item_id": str(item.get("id") or "") or None,
            "title": title,
            "position": position,
            "delta": delta,
            "trend": trend,
            "visibility": visibility,
            "views": int(item.get("views") or 0),
            "contacts": int(item.get("contacts") or 0),
            "conversion": float(item.get("conversion") or 0),
            "raw_excerpt": excerpt[:700],
        })
    return found


def _derive_signal(rows: list[dict]) -> dict:
    measured = [r for r in rows if r.get("position") is not None]
    hidden = [r for r in rows if r.get("visibility") == "hidden"]
    low = [r for r in measured if int(r.get("position") or 0) > 30]
    top = [r for r in measured if int(r.get("position") or 9999) <= 15]
    falling = [r for r in measured if int(r.get("delta") or 0) < 0]
    improving = [r for r in measured if int(r.get("delta") or 0) > 0]
    positions = [int(r["position"]) for r in measured]
    median = None
    if positions:
        ordered = sorted(positions)
        n = len(ordered)
        median = float(ordered[n // 2]) if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2.0
    # Signal is advisory only. Existing money mandates remain the only authority
    # for bids/promotion. This prevents rank chasing from bypassing CPL/budget caps.
    return {
        "status": "ok" if measured or hidden else "no_position_data",
        "measured": len(measured),
        "hidden": len(hidden),
        "low_below_30": len(low),
        "top_15": len(top),
        "falling": len(falling),
        "improving": len(improving),
        "median_position": median,
        "recommended_lane": (
            "diagnose_hidden" if hidden else
            "conversion_before_spend" if low else
            "hold_and_measure" if measured else
            "no_position_data"
        ),
        "money_authority": "existing_kpi_budget_and_cpl_guards_only",
    }


def persist_browser_page(account_id: str, gateway_job_id: str, page_text: str, structured_cards: list[dict] | None = None, scan_id: str | None = None, page_no: int = 1, scan_meta: dict | None = None) -> dict:
    db = SessionLocal()
    try:
        ensure_schema(db)
        items = _latest_items(db, account_id)
        if structured_cards:
            by_id = {str(x.get("id") or ""): x for x in items if str(x.get("id") or "")}
            rows = []
            seen_ids = set()
            for card in structured_cards:
                if not isinstance(card, dict):
                    continue
                iid = str(card.get("item_id") or "")
                item = by_id.get(iid)
                if not iid or iid in seen_ids or not item:
                    continue
                seen_ids.add(iid)
                visibility = str(card.get("visibility") or "unknown")
                position = card.get("position")
                try: position = int(position) if position is not None else None
                except Exception: position = None
                delta = card.get("delta")
                try: delta = int(delta) if delta is not None else None
                except Exception: delta = None
                trend = "up" if (delta or 0) > 0 else ("down" if (delta or 0) < 0 else ("hidden" if visibility == "hidden" else "flat" if position is not None else "unknown"))
                rows.append({
                    "item_id": iid,
                    "title": str(item.get("title") or card.get("title") or "Без названия"),
                    "position": position,
                    "delta": delta,
                    "trend": trend,
                    "visibility": visibility,
                    "views": int(item.get("views") or 0),
                    "contacts": int(item.get("contacts") or 0),
                    "conversion": float(item.get("conversion") or 0),
                    "raw_excerpt": str(card.get("text") or "")[:700],
                })
        else:
            rows = parse_pro_items_page(account_id, page_text, items)
        measured_at = _now()
        inserted = 0
        deduplicated = 0
        # History is stateful, not a heartbeat log. The identity watchdog may read
        # the same cabinet page every few minutes; unchanged position evidence must
        # not create thousands of duplicate rows. Keep a new history row only when
        # the visible state changed or the previous sample is at least 15 minutes old.
        for row in rows:
            prev = db.execute(text("""
                SELECT position,delta,trend,visibility,measured_at
                FROM avito_position_snapshots
                WHERE account_id=:account_id AND item_id IS NOT DISTINCT FROM :item_id
                ORDER BY measured_at DESC,id DESC LIMIT 1
            """), {"account_id": account_id, "item_id": row.get("item_id")}).first()
            unchanged = False
            if prev:
                age = (measured_at - prev[4]).total_seconds() if prev[4] else 10**9
                unchanged = (
                    prev[0] == row.get("position") and
                    prev[1] == row.get("delta") and
                    str(prev[2] or "") == str(row.get("trend") or "") and
                    str(prev[3] or "") == str(row.get("visibility") or "") and
                    age < DEFAULT_INTERVAL_SEC
                )
            if unchanged:
                deduplicated += 1
                continue
            db.execute(text("""
                INSERT INTO avito_position_snapshots(
                    account_id,item_id,title,position,delta,trend,visibility,
                    views,contacts,conversion,source,gateway_job_id,measured_at,raw_excerpt
                ) VALUES (
                    :account_id,:item_id,:title,:position,:delta,:trend,:visibility,
                    :views,:contacts,:conversion,'browser_gateway_pro_items',:job_id,:measured_at,:raw_excerpt
                )
            """), {**row, "account_id": account_id, "job_id": gateway_job_id, "measured_at": measured_at})
            inserted += 1
        db.commit()
        public_rows = [{k: v for k, v in r.items() if k != "raw_excerpt"} for r in rows]
        previous_latest = _storage_get(db, account_id, LATEST_KEY) if scan_id else {}
        if scan_id and str(previous_latest.get("scan_id") or "") == str(scan_id) and int(page_no or 1) > 1:
            merged = {}
            for previous_row in previous_latest.get("rows") or []:
                if isinstance(previous_row, dict) and previous_row.get("item_id"):
                    merged[str(previous_row.get("item_id"))] = previous_row
            for current_row in public_rows:
                if current_row.get("item_id"):
                    merged[str(current_row.get("item_id"))] = current_row
            analysis_rows = list(merged.values())
            pages_completed = sorted(set([int(x) for x in (previous_latest.get("pages_completed") or []) if str(x).isdigit()] + [int(page_no or 1)]))
        else:
            analysis_rows = public_rows
            pages_completed = [int(page_no or 1)] if scan_id else []
        signal = _derive_signal(analysis_rows)
        inventory_count = len(items)
        matched_count = len(analysis_rows)
        coverage_base = int((scan_meta or {}).get("total") or 0) or inventory_count
        coverage_pct = round((matched_count / coverage_base) * 100.0, 2) if coverage_base else 0.0
        # read_page is the currently rendered Avito Pro inventory page, not proof
        # of the whole account. We explicitly mark partial coverage instead of
        # pretending that an account with hundreds of listings was fully measured.
        reported_total = int((scan_meta or {}).get("total") or 0)
        expected_count = reported_total or inventory_count
        coverage_complete = bool(expected_count and matched_count >= expected_count)
        latest = {
            "account_id": account_id,
            "measured_at": _iso(measured_at),
            "source": "browser_gateway_pro_items",
            "gateway_job_id": gateway_job_id,
            "inventory_items": inventory_count,
            "avito_reported_total": reported_total or None,
            "page_cards_matched": len(public_rows),
            "scan_id": scan_id,
            "page_no": int(page_no or 1),
            "pages_completed": pages_completed,
            "reader": "avito_pro_items_v1" if structured_cards else "legacy_page_text",
            "scan_meta": scan_meta or {},
            "coverage": {
                "complete": coverage_complete,
                "matched": matched_count,
                "inventory": expected_count,
                "coverage_pct": coverage_pct,
                "scope": "current_rendered_avito_pro_page" if not coverage_complete else "full_active_inventory",
            },
            "history_write": {"inserted": inserted, "deduplicated": deduplicated},
            "rows": [{k: v for k, v in r.items() if k != "raw_excerpt"} for r in rows],
            "signal": signal,
        }
        _storage_put(db, account_id, LATEST_KEY, latest)
        _storage_put(db, account_id, SIGNAL_KEY, {**signal, "measured_at": latest["measured_at"], "source": latest["source"]})
        _health_state = "ok" if coverage_complete and rows else ("partial" if rows else "deferred")
        _health_reason = (
            "full active inventory position snapshot persisted" if coverage_complete and rows else
            f"partial Avito Pro page coverage: {matched_count}/{expected_count} active items matched" if rows else
            "Avito Pro page read but no active card titles matched"
        )
        _storage_put(db, account_id, HEALTH_KEY, {
            "state": _health_state,
            "reason": _health_reason,
            "checked_at": _iso(),
            "gateway_job_id": gateway_job_id,
            "matched": matched_count,
            "inventory": expected_count,
            "coverage_pct": coverage_pct,
            "coverage_complete": coverage_complete,
            "history_inserted": inserted,
            "history_deduplicated": deduplicated,
            "scan_id": scan_id,
            "page_no": int(page_no or 1),
            "reader": "avito_pro_items_v1" if structured_cards else "legacy_page_text",
            "measured": int(signal.get("measured") or 0),
            "hidden": int(signal.get("hidden") or 0),
        })
        return latest
    finally:
        db.close()


def _gateway_for_account(account_id: str) -> dict:
    # Lazy import avoids a module cycle because Browser Gateway calls back into
    # persist_browser_page after a read job completes. Carry authoritative online
    # state with the gateway record: a historically verified but stale Chrome
    # session is not executable capacity.
    from app.api import browser_gateway as bg
    state = bg._account_gateway_state(account_id) or {}
    gateway = dict(state.get("gateway") or {}) if isinstance(state.get("gateway"), dict) else {}
    gateway["_connected"] = bool(state.get("connected"))
    gateway["_online"] = bool(state.get("online"))
    gateway["_runtime_state"] = str(state.get("state") or "unknown")
    return gateway


def queue_position_probe(account_id: str, min_interval_seconds: int = DEFAULT_INTERVAL_SEC) -> dict:
    """Queue one READ-only Avito Pro position probe when a verified gateway exists."""
    from app.api import browser_gateway as bg

    db = SessionLocal()
    try:
        last = _storage_get(db, account_id, LATEST_KEY)
        try:
            ts = datetime.fromisoformat(str(last.get("measured_at") or ""))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age = (_now() - ts).total_seconds()
            if age < max(60, int(min_interval_seconds)):
                return {"status": "fresh", "age_sec": round(age, 1), "account_id": account_id}
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=424)
    finally:
        db.close()

    # Idempotency before provider work: one account may have at most one pending
    # Position Monitor browser job. Repeated hourly/owner refreshes reuse it.
    db = SessionLocal()
    try:
        pending_rows = db.query(Storage).filter(
            Storage.account_id == account_id,
            Storage.key.like("browser_job:position_%"),
        ).order_by(Storage.id.desc()).limit(20).all()
        for pending_row in pending_rows:
            pending = _json_load(pending_row.value, {})
            if str((pending.get("metadata") or {}).get("purpose") or "") != JOB_PURPOSE:
                continue
            if str(pending.get("status") or "") in {"waiting_gateway", "running_gateway"}:
                return {
                    "status": "queued",
                    "reason": "position_probe_already_pending",
                    "account_id": account_id,
                    "job_id": pending.get("job_id"),
                    "reused": True,
                }
    finally:
        db.close()

    gateway = _gateway_for_account(account_id)
    gateway_id = str(gateway.get("gateway_id") or "")
    if not gateway_id or not gateway.get("identity_verified"):
        db = SessionLocal()
        try:
            _storage_put(db, account_id, HEALTH_KEY, {
                "state": "deferred",
                "reason": "identity-verified Browser Gateway session is not connected; official Avito API has no position field",
                "checked_at": _iso(),
                "source": "awaiting_gateway",
            })
        finally:
            db.close()
        return {"status": "deferred", "reason": "no_verified_gateway", "account_id": account_id}

    if not gateway.get("_connected") or not gateway.get("_online"):
        db = SessionLocal()
        try:
            _storage_put(db, account_id, HEALTH_KEY, {
                "state": "deferred",
                "reason": "Browser Gateway session is verified but currently offline; position probe was not queued",
                "checked_at": _iso(),
                "source": "awaiting_online_gateway",
                "gateway_id": gateway_id,
                "runtime_state": gateway.get("_runtime_state"),
            })
        finally:
            db.close()
        return {"status": "deferred", "reason": "gateway_offline", "account_id": account_id, "gateway_id": gateway_id}

    # Do not steal the browser from an active write/read job. Position monitoring
    # is lower priority and waits for the next scheduler pass.
    if str(gateway.get("state") or "idle") not in {"idle", "paired"} or gateway.get("current_job_id"):
        return {"status": "busy", "reason": "gateway_busy", "account_id": account_id}

    job_id = "position_" + secrets.token_hex(7)
    try:
        _ver = tuple(int(x) for x in re.findall(r"\d+", str(gateway.get("extension_version") or "0"))[:3])
    except Exception:
        _ver = (0,)
    _supports_pro_reader = _ver >= (2, 1, 14)
    _steps = [
        {"action": "open_url", "url": "https://www.avito.ru/profile/pro/items", "selector": None, "text": None, "value": None, "timeout_ms": 25000, "redact_selectors": []},
    ]
    if _supports_pro_reader:
        _steps.append({"action": "read_avito_pro_items", "url": None, "selector": None, "text": None, "value": None, "timeout_ms": 15000, "redact_selectors": []})
    else:
        _steps.append({"action": "read_page", "url": None, "selector": None, "text": None, "value": None, "timeout_ms": 15000, "redact_selectors": []})
    job = {
        "job_id": job_id,
        "kind": "browser",
        "status": "waiting_gateway",
        "created_at": bg._iso(),
        "updated_at": bg._iso(),
        "account_id": account_id,
        "domain": "avito.ru",
        "capability": "read_avito_pro_items" if _supports_pro_reader else "read_page",
        "risk_level": "READ",
        "mode": "read",
        "steps": _steps,
        "gateway_id": gateway_id,
        "confirmed": False,
        "metadata": {"purpose": JOB_PURPOSE, "read_only": True, "scan_id": "scan_" + secrets.token_hex(6), "page_no": 1, "reader": "avito_pro_items" if _supports_pro_reader else "legacy_page_text"},
        "results": [],
        "error": None,
        "privacy": {"guard": True, "never_read_passwords": True},
    }
    bg._db_put(account_id, f"browser_job:{job_id}", job)
    db = SessionLocal()
    try:
        _storage_put(db, account_id, HEALTH_KEY, {
            "state": "queued",
            "reason": "read-only Avito Pro position probe queued",
            "checked_at": _iso(),
            "gateway_job_id": job_id,
        })
    finally:
        db.close()
    return {"status": "queued", "account_id": account_id, "job_id": job_id, "gateway_id": gateway_id}


def process_completed_job(job: dict) -> dict | None:
    purpose = str((job.get("metadata") or {}).get("purpose") or "")
    # Reuse the existing identity-watchdog read of /profile/pro/items. This is
    # both faster and gentler on Avito than opening the same page a second time.
    if purpose not in {JOB_PURPOSE, "auto_identity_watchdog"}:
        return None
    page_text = ""
    structured_card_texts: list[str] = []
    structured_pro_cards: list[dict] = []
    pro_scan_meta: dict = {}
    for row in job.get("results") or []:
        result = row.get("result") if isinstance(row, dict) else None
        if not isinstance(result, dict):
            continue
        # Generic read_page fallback.
        if str(result.get("text") or ""):
            page_text = str(result.get("text") or "")
        # Structured card reader returns individual card text/URLs. Never use its
        # ordinal `position` field (that is DOM order); only feed the card text
        # back through the explicit "N место ↑/↓K" parser below.
        if result.get("reader") == "avito_pro_items_v1" and isinstance(result.get("items"), list):
            pro_scan_meta = {"pages": result.get("pages") or [], "visible_to": result.get("visible_to"), "total": result.get("total"), "url": result.get("url")}
            for card in result.get("items") or []:
                if isinstance(card, dict) and card.get("item_id") and "visibility" in card:
                    structured_pro_cards.append(card)
        if isinstance(result.get("items"), list):
            for card in result.get("items") or []:
                if not isinstance(card, dict):
                    continue
                text_value = str(card.get("text") or "").strip()
                title_value = str(card.get("title") or "").strip()
                if text_value:
                    structured_card_texts.append(text_value)
                elif title_value:
                    structured_card_texts.append(title_value)
    if structured_card_texts:
        # Per-card boundaries are much safer than one flattened 30k body snapshot.
        page_text = "\n".join(structured_card_texts)
    if not page_text and not structured_pro_cards:
        db = SessionLocal()
        try:
            _storage_put(db, str(job.get("account_id") or ""), HEALTH_KEY, {
                "state": "deferred",
                "reason": "Browser Gateway completed without readable Avito Pro text",
                "checked_at": _iso(),
                "gateway_job_id": job.get("job_id"),
            })
        finally:
            db.close()
        return {"status": "deferred", "reason": "empty_page_text"}
    metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
    persisted = persist_browser_page(
        str(job.get("account_id") or ""),
        str(job.get("job_id") or ""),
        page_text,
        structured_cards=structured_pro_cards or None,
        scan_id=str(metadata.get("scan_id") or "") or None,
        page_no=int(metadata.get("page_no") or 1),
        scan_meta=pro_scan_meta,
    )
    # Continue the same scan one page at a time. The gateway is single-flight,
    # so never fan out all pages at once. The next scheduler tick harvests this
    # page and queues exactly one unseen page.
    if structured_pro_cards and not (persisted.get("coverage") or {}).get("complete"):
        completed = set(int(x) for x in (persisted.get("pages_completed") or []) if str(x).isdigit())
        for candidate in sorted(pro_scan_meta.get("pages") or [], key=lambda x: int(x.get("page") or 9999)):
            pno = int(candidate.get("page") or 0)
            if pno > 0 and pno not in completed and candidate.get("url"):
                persisted["next_page"] = {"page": pno, "url": candidate.get("url")}
                break
    return persisted


def harvest_latest_position_job(account_id: str) -> dict:
    """Consume the newest completed position result exactly once.

    Browser Gateway persists the already parsed result on completion. Reading that
    payload avoids re-processing the same page and therefore avoids duplicate
    position snapshots. Legacy completed jobs without the full payload remain
    observable but are not parsed a second time.
    """
    db = SessionLocal()
    try:
        rows = (
            db.query(Storage)
            .filter(
                Storage.account_id == account_id,
                Storage.key.like("browser_job:%"),
            )
            .order_by(Storage.id.desc())
            .limit(40)
            .all()
        )
        for row in rows:
            job = _json_load(row.value, {})
            if not isinstance(job, dict):
                continue
            purpose = str((job.get("metadata") or {}).get("purpose") or "")
            if purpose not in {JOB_PURPOSE, "auto_identity_watchdog"}:
                continue
            if str(job.get("status") or "") != "completed":
                continue
            if job.get("position_monitor_harvested_at"):
                continue

            result = job.get("position_monitor_result")
            if not isinstance(result, dict):
                summary = job.get("position_monitor") if isinstance(job.get("position_monitor"), dict) else {}
                # Legacy callback versions stored only a summary. Do not call the
                # parser again because it may already have persisted snapshots.
                result = {
                    "status": str(summary.get("status") or "legacy_completed"),
                    "measured_at": summary.get("measured_at"),
                    "page_cards_matched": summary.get("matched"),
                    "legacy_summary_only": True,
                }

            job["position_monitor_harvested_at"] = _iso()
            row.value = json.dumps(job, ensure_ascii=False, separators=(",", ":"))
            db.commit()
            return result
        return {"status": "empty"}
    finally:
        db.close()


def monitor_tick(account_id: str) -> dict:
    """Harvest a completed scan page, continue pagination, or start a due scan."""
    harvested = harvest_latest_position_job(account_id)
    if isinstance(harvested, dict) and harvested.get("next_page") and harvested.get("scan_id"):
        nxt = harvested.get("next_page") or {}
        queued = queue_position_scan_page(account_id, str(nxt.get("url") or ""), str(harvested.get("scan_id") or ""), int(nxt.get("page") or 0))
    else:
        queued = queue_position_probe(account_id)
    harvested_status = str((harvested or {}).get("status") or "") if isinstance(harvested, dict) else ""
    queued_status = str((queued or {}).get("status") or "") if isinstance(queued, dict) else ""
    if harvested_status not in {"", "empty", "legacy_completed"}:
        status = "fresh"
    elif queued_status == "queued":
        status = "queued"
    elif queued_status == "deferred":
        status = "deferred"
    else:
        status = queued_status or harvested_status or "idle"
    return {
        "status": status,
        "reason": (queued or {}).get("reason") if isinstance(queued, dict) else None,
        "account_id": account_id,
        "harvested": harvested,
        "queued": queued,
    }


def get_position_status(account_id: str, history_limit: int = 100) -> dict:
    db = SessionLocal()
    try:
        ensure_schema(db)
        latest = _storage_get(db, account_id, LATEST_KEY)
        health = _storage_get(db, account_id, HEALTH_KEY)
        signal = _storage_get(db, account_id, SIGNAL_KEY)
        rows = db.execute(text("""
            SELECT item_id,title,position,delta,trend,visibility,views,contacts,conversion,source,gateway_job_id,measured_at
            FROM avito_position_snapshots
            WHERE account_id=:a
            ORDER BY measured_at DESC,id DESC
            LIMIT :lim
        """), {"a": account_id, "lim": max(1, min(int(history_limit or 100), 500))}).fetchall()
        history = [{
            "item_id": r[0], "title": r[1], "position": r[2], "delta": r[3],
            "trend": r[4], "visibility": r[5], "views": r[6], "contacts": r[7],
            "conversion": r[8], "source": r[9], "gateway_job_id": r[10],
            "measured_at": r[11].isoformat() if r[11] else None,
        } for r in rows]
        return {
            "status": "ok",
            "account_id": account_id,
            "latest": latest,
            "health": health,
            "signal": signal,
            "history": history,
            "contract": {
                "provider_write": False,
                "paid_ai": False,
                "position_source": "identity_verified_avito_pro_browser_read",
                "official_api_position_field": False,
                "money_actions": "existing KPI/CPL/budget mandates only",
            },
        }
    finally:
        db.close()


def queue_position_scan_page(account_id: str, url: str, scan_id: str, page_no: int) -> dict:
    """Queue one additional READ-only Avito Pro page for the same verified account."""
    if not account_id or not url or "avito.ru/profile/pro/items" not in str(url):
        return {"status": "deferred", "reason": "invalid_scan_page"}
    # Reuse the same authoritative gateway resolver as the first-page probe.
    # The former helper names here were stale and undefined, so pagination would
    # crash only after a successful first page. Keep identity + live connectivity
    # fail-closed before queuing any continuation page.
    gateway = _gateway_for_account(account_id)
    if not gateway or not gateway.get("identity_verified"):
        return {"status": "deferred", "reason": "no_verified_gateway"}
    if not gateway.get("_connected") or not gateway.get("_online"):
        return {"status": "deferred", "reason": "gateway_offline"}
    try:
        ver = tuple(int(x) for x in re.findall(r"\d+", str(gateway.get("extension_version") or "0"))[:3])
    except Exception:
        ver = (0,)
    if ver < (2, 1, 14):
        return {"status": "deferred", "reason": "gateway_upgrade_required"}
    from app.api import browser_gateway as bg
    gateway_id = str(gateway.get("gateway_id") or "")
    # A verified gateway is single-flight. This prevents duplicate page jobs
    # without scanning the whole job store.
    if str(gateway.get("state") or "idle") not in {"idle", "online"}:
        return {"status": "busy", "reason": "gateway_busy"}
    job_id = "position_" + secrets.token_hex(7)
    job = {"job_id": job_id, "kind": "browser", "status": "waiting_gateway", "created_at": bg._iso(), "updated_at": bg._iso(), "account_id": account_id, "domain": "avito.ru", "capability": "read_avito_pro_items", "risk_level": "READ", "mode": "read", "steps": [{"action": "open_url", "url": str(url), "selector": None, "text": None, "value": None, "timeout_ms": 25000, "redact_selectors": []}, {"action": "read_avito_pro_items", "url": None, "selector": None, "text": None, "value": None, "timeout_ms": 15000, "redact_selectors": []}], "gateway_id": gateway_id, "confirmed": False, "metadata": {"purpose": JOB_PURPOSE, "read_only": True, "scan_id": scan_id, "page_no": int(page_no), "reader": "avito_pro_items"}, "results": [], "error": None, "privacy": {"guard": True, "never_read_passwords": True}}
    # POSITION_PAGINATION_JOB_SCOPE_V1: Browser Gateway claims and reports
    # browser jobs from the tenant-scoped `browser_job:<id>` key. Continuation
    # pages must use exactly the same storage contract as the first page; writing
    # them to SYSTEM_ACCOUNT/job:<id> makes /browser-progress return 404 and
    # silently strands otherwise healthy multi-page position scans.
    bg._db_put(account_id, f"browser_job:{job_id}", job)
    return {"status": "queued", "job_id": job_id, "scan_id": scan_id, "page_no": int(page_no)}
