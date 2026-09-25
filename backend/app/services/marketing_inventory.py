"""Account-wide Avito marketing memory.

Analyses ALL live inventory returned by Avito, including ads BORIS did not create.
Read-only with respect to Avito. Exact CampaignItem identity is used only to label
managed vs native/external items; unresolved items are never mutated automatically.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from app.db.session import SessionLocal
from app.models.storage import Storage
from app.models.campaign_item import CampaignItem
from app.services.campaign_identity import is_canonical_writable
from app.services.initial_portfolio import _fetch_items, _fetch_stats

KEY = "marketing_inventory_audit"


def refresh_marketing_inventory(account_id: str, token: str, avito_user_id: str, days: int = 90) -> dict:
    # MARKETING_INVENTORY_ACCOUNT_THROTTLE_IDENTITY_V1:
    # initial_portfolio helpers are account-scoped so they can honor the shared
    # Avito Retry-After ledger. Keep the account identity on every provider read.
    active = _fetch_items(account_id, token, "active")
    old = _fetch_items(account_id, token, "old")
    all_rows = active + old
    ids = [int(x.get("id")) for x in all_rows if str(x.get("id") or "").isdigit()]
    stats = _fetch_stats(account_id, token, str(avito_user_id or ""), ids, days=days)

    # Materialize only the identity scalars we need, then close the read
    # transaction before CPU-heavy inventory assembly / JSON serialization.
    # Large Avito accounts can have thousands of rows and previously left an
    # idle PostgreSQL transaction open for the whole transformation.
    db = SessionLocal()
    try:
        managed_rows = db.query(CampaignItem).filter(CampaignItem.account_id == account_id).all()
        managed = {
            str(row.avito_item_id): {
                "campaign_item_id": row.id,
                "feed_identity": str(row.feed_identity or ""),
                "source": str(row.source or ""),
                "identity_status": str(row.identity_status or ""),
                "canonical_writable": bool(is_canonical_writable(row)),
            }
            for row in managed_rows
            if str(row.avito_item_id or "").isdigit()
        }
        db.rollback()
    finally:
        db.close()

    items = []
    for row in all_rows:
        iid = str(row.get("id") or "")
        st = stats.get(int(iid)) if iid.isdigit() else None
        st = st or {"views": 0, "contacts": 0, "favorites": 0}
        views = int(st.get("views") or 0); contacts = int(st.get("contacts") or 0)
        identity = managed.get(iid)
        items.append({
            "id": int(iid) if iid.isdigit() else iid,
            "title": row.get("title") or "",
            "url": row.get("url") or "",
            "status": row.get("status") or "",
            "category": ((row.get("category") or {}).get("name") if isinstance(row.get("category"), dict) else row.get("category")) or "",
            "views_90d": views,
            "contacts_90d": contacts,
            "favorites_90d": int(st.get("favorites") or 0),
            "conversion_90d": round(contacts / views * 100, 2) if views else 0.0,
            "origin": "boris_managed" if identity else "avito_native_external",
            "write_capability": "canonical" if identity and identity.get("canonical_writable") else "read_only_until_mapped",
            "identity": identity,
        })
    proven = [x for x in items if x["contacts_90d"] > 0]
    proven.sort(key=lambda x: (x["contacts_90d"], x["conversion_90d"], x["views_90d"]), reverse=True)
    native_proven = [x for x in proven if x["origin"] == "avito_native_external"]
    managed_proven = [x for x in proven if x["origin"] == "boris_managed"]
    payload = {
        "status": "ok", "account_id": account_id,
        "generated_at": datetime.now(timezone.utc).isoformat(), "window_days": days,
        "counts": {
            "active": sum(1 for x in items if x["status"] == "active"),
            "old": sum(1 for x in items if x["status"] == "old"),
            "total": len(items),
            "boris_managed": sum(1 for x in items if x["origin"] == "boris_managed"),
            "avito_native_external": sum(1 for x in items if x["origin"] == "avito_native_external"),
            "native_with_contacts": len(native_proven),
        },
        "top_all": proven[:30],
        "top_native_external": native_proven[:30],
        "top_boris_managed": managed_proven[:30],
        "items": items,
        "policy": "all_inventory_is_analyzed; external_native_is_read_only_until_exact_canonical_mapping",
    }
    raw = json.dumps(payload, ensure_ascii=False)
    write_db = SessionLocal()
    try:
        row = write_db.query(Storage).filter(Storage.account_id == account_id, Storage.key == KEY).first()
        if row: row.value = raw
        else: write_db.add(Storage(account_id=account_id, key=KEY, value=raw))
        write_db.commit()
    finally:
        write_db.close()
    return payload
