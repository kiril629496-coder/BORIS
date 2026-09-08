#!/usr/bin/env python3
"""Deterministic zero-paid guard for feed membership changes between watchdog ticks.

Stores only account/feed identity sets in a local state file. It never mutates DB,
never publishes, and never calls external providers. Removed identities are
classified from CampaignItem lifecycle so normal supersede/archive/delete does not
page the owner; removal of a still-published bound identity fails closed.
"""
import json
from pathlib import Path

from app.db.session import SessionLocal
from app.models.campaign_item import CampaignItem
from app.models.storage import Storage

STATE_PATH = Path("/root/BORIS/backend/.boris_ops/avito_feed_lifecycle_snapshot.json")
INACTIVE = {"superseded", "archived", "deleted"}


def _removal_cause(rows):
    """Infer a deterministic local cause from campaign lifecycle evidence only."""
    for row in rows:
        if row.get("replacement_completed") or row.get("superseded_by_feed_identity"):
            return "replacement_confirmed"
    for row in rows:
        if row.get("identity_resolution") == "superseded_by_autoload_report" or str(row.get("identity_status") or "") == "superseded_by_authoritative_feed":
            return "authoritative_feed_cutover"
    for row in rows:
        if str(row.get("status") or "").lower() in {"archived", "deleted"}:
            return "explicit_archive_or_delete"
    if rows:
        return "inactive_without_provenance"
    return "unowned_feed_removal"


def classify_removed(previous, current, lifecycle):
    """Return (expected, unexpected, informational) removal records.

    lifecycle maps (account_id, feed_identity) to a list of campaign row summaries.
    Pure function to make the production classification regression-testable.
    """
    expected, unexpected, informational = [], [], []
    for account_id, prev_ids in previous.items():
        removed = sorted(set(prev_ids) - set(current.get(account_id, [])))
        for fid in removed:
            rows = lifecycle.get((account_id, fid), [])
            active_published = [r for r in rows if str(r.get("status") or "").lower() == "published" and str(r.get("identity_status") or "") == "published_identity_bound"]
            inactive = [r for r in rows if str(r.get("status") or "").lower() in INACTIVE or str(r.get("identity_status") or "").lower().startswith("superseded")]
            rec = {"account_id": account_id, "feed_identity": fid, "campaign_rows": rows[:5]}
            if active_published:
                rec["reason"] = "removed_while_published_identity_bound"
                rec["cause"] = "unexplained_live_removal"
                unexpected.append(rec)
            elif inactive:
                rec["reason"] = "expected_inactive_lifecycle"
                rec["cause"] = _removal_cause(rows)
                expected.append(rec)
            else:
                rec["reason"] = "no_active_campaign_owner"
                rec["cause"] = _removal_cause(rows)
                informational.append(rec)
    return expected, unexpected, informational


def load_state_file(path):
    """Load a snapshot fail-closed when an existing state file is malformed."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"snapshot_unreadable:{type(exc).__name__}") from exc
    if not isinstance(raw, dict) or raw.get("version") != 1 or not isinstance(raw.get("feeds"), dict):
        raise RuntimeError("snapshot_schema_invalid")
    feeds = raw["feeds"]
    for account_id, ids in feeds.items():
        if not isinstance(account_id, str) or not isinstance(ids, list) or any(not isinstance(x, str) for x in ids):
            raise RuntimeError("snapshot_feed_shape_invalid")
    return feeds


def _load_state():
    return load_state_file(STATE_PATH)


def _atomic_save(feeds):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": 1, "feeds": feeds}, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def main():
    db = SessionLocal()
    try:
        current = {}
        for row in db.query(Storage).filter(Storage.key == "feed_items").all():
            try:
                data = json.loads(row.value or "[]")
            except Exception:
                data = []
            ids = sorted({str(x.get("id") or "").strip() for x in data if isinstance(x, dict) and str(x.get("id") or "").strip()}) if isinstance(data, list) else []
            current[str(row.account_id)] = ids

        try:
            previous = _load_state()
        except RuntimeError as exc:
            report = {
                "status": "FAIL",
                "reason": "lifecycle_snapshot_invalid",
                "detail": str(exc),
                "snapshot_path": str(STATE_PATH),
                "paid_calls": 0,
                "external_publish_calls": 0,
            }
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 2
        lifecycle = {}
        if previous:
            removed_pairs = {(acc, fid) for acc, ids in previous.items() for fid in (set(ids) - set(current.get(acc, [])))}
            if removed_pairs:
                for row in db.query(CampaignItem).filter(CampaignItem.feed_identity.isnot(None)).all():
                    key = (str(row.account_id), str(row.feed_identity or "").strip())
                    if key not in removed_pairs:
                        continue
                    try:
                        payload = json.loads(row.payload_json or "{}")
                    except Exception:
                        payload = {}
                    identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
                    replacement = payload.get("replacement_result") if isinstance(payload.get("replacement_result"), dict) else {}
                    lifecycle.setdefault(key, []).append({
                        "id": int(row.id),
                        "status": str(row.status or ""),
                        "identity_status": str(row.identity_status or ""),
                        "campaign_id": int(row.campaign_id) if row.campaign_id is not None else None,
                        "identity_resolution": str(identity.get("resolution") or ""),
                        "superseded_by_feed_identity": str(identity.get("superseded_by_feed_identity") or ""),
                        "replacement_completed": str(replacement.get("state") or "") == "completed",
                    })

        expected, unexpected, informational = classify_removed(previous, current, lifecycle)
        # Save the observed truth even on failure. The media audit still independently
        # fails every tick while an active published identity remains absent, so this
        # snapshot cannot hide a persistent incident; it only avoids replaying the same
        # transition event forever.
        _atomic_save(current)
        report = {
            "status": "PASS" if not unexpected else "FAIL",
            "baseline": not bool(previous),
            "feed_accounts": len(current),
            "feed_items": sum(len(v) for v in current.values()),
            "removed_expected": len(expected),
            "removed_unexpected": len(unexpected),
            "removed_informational": len(informational),
            "expected_examples": expected[:20],
            "unexpected_examples": unexpected[:20],
            "informational_examples": informational[:20],
            "paid_calls": 0,
            "external_publish_calls": 0,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "PASS" else 2
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
