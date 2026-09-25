"""Initial Avito portfolio bootstrap for BORIS.

Rule:
- works 24/7;
- if at least one active Avito item exists, do nothing;
- if active=0, inspect only naturally completed items (status=old);
- rank by proven demand: contacts, then conversion, then views/favorites;
- choose at most 100 items with contacts > 0;
- reactivate only items that BORIS can prove are managed by its autoload feed.

The public Avito Item API has no generic 'reactivate old item' write method.
Therefore external/native old items are never falsely marked as activated.
"""
from __future__ import annotations

import json
from datetime import date, timedelta, datetime, timezone

import httpx
from sqlalchemy import text

from app.db.session import SessionLocal
from app.models.storage import Storage
from app.models.campaign_item import CampaignItem
from app.services.avito_account_throttle import account_throttle_remaining, record_account_throttle
from app.services.exception_observability import observe_suppressed

MAX_REACTIVATE = 100
STATE_KEY = "initial_portfolio_bootstrap"


def _save_state(db, account_id: str, payload: dict):
    raw = json.dumps(payload, ensure_ascii=False)
    row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == STATE_KEY).first()
    if row:
        row.value = raw
    else:
        db.add(Storage(account_id=account_id, key=STATE_KEY, value=raw))
    db.commit()


def _retry_after_seconds(resp) -> float:
    raw = str((resp.headers or {}).get("Retry-After") or "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except Exception:
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
            except Exception as _suppressed_exc:
                observe_suppressed(__name__, _suppressed_exc, line=53)
    return 30.0


def _shared_throttle_guard(account_id: str) -> None:
    # INITIAL_PORTFOLIO_SHARED_ACCOUNT_THROTTLE_V1: every provider read honors
    # tenant-wide Retry-After before creating new Avito pressure.
    remaining = account_throttle_remaining(account_id)
    if remaining > 0:
        raise RuntimeError(f"avito_account_throttled:{remaining}")


def _fetch_items(account_id: str, token: str, status: str, max_pages: int = 20) -> list[dict]:
    out = []
    headers = {"Authorization": f"Bearer {token}"}
    for page in range(1, max_pages + 1):
        _shared_throttle_guard(account_id)
        r = httpx.get(
            "https://api.avito.ru/core/v1/items",
            headers=headers,
            params={"per_page": 100, "page": page, "status": status},
            timeout=25,
        )
        if r.status_code == 429:
            record_account_throttle(account_id, _retry_after_seconds(r), source="initial_portfolio_items_429")
            raise RuntimeError("avito_account_throttled")
        if r.status_code != 200:
            raise RuntimeError(f"items_{status}_http_{r.status_code}")
        chunk = r.json().get("resources") or []
        if not chunk:
            break
        out.extend(chunk)
        if len(chunk) < 100:
            break
    return out


def _fetch_stats(account_id: str, token: str, user_id: str, item_ids: list[int], days: int = 90) -> dict[int, dict]:
    result: dict[int, dict] = {}
    if not item_ids or not user_id:
        return result
    date_to = date.today().isoformat()
    date_from = (date.today() - timedelta(days=days)).isoformat()
    headers = {"Authorization": f"Bearer {token}"}
    for start in range(0, len(item_ids), 200):
        _shared_throttle_guard(account_id)
        chunk = item_ids[start:start + 200]
        r = httpx.post(
            f"https://api.avito.ru/stats/v1/accounts/{user_id}/items",
            headers=headers,
            json={
                "dateFrom": date_from,
                "dateTo": date_to,
                "fields": ["uniqViews", "uniqContacts", "uniqFavorites"],
                "itemIds": chunk,
            },
            timeout=40,
        )
        if r.status_code == 429:
            record_account_throttle(account_id, _retry_after_seconds(r), source="initial_portfolio_stats_429")
            raise RuntimeError("avito_account_throttled")
        if r.status_code != 200:
            continue
        for item in (r.json().get("result") or {}).get("items", []):
            views = contacts = favorites = 0
            for s in item.get("stats") or []:
                views += int(s.get("uniqViews") or 0)
                contacts += int(s.get("uniqContacts") or 0)
                favorites += int(s.get("uniqFavorites") or 0)
            iid = int(item.get("itemId"))
            result[iid] = {"views": views, "contacts": contacts, "favorites": favorites}
    return result


def _rank_old(items: list[dict], stats: dict[int, dict], limit: int = MAX_REACTIVATE) -> list[dict]:
    ranked = []
    for item in items:
        try:
            iid = int(item.get("id"))
        except Exception:
            continue
        s = stats.get(iid) or {"views": 0, "contacts": 0, "favorites": 0}
        views = int(s.get("views") or 0)
        contacts = int(s.get("contacts") or 0)
        favorites = int(s.get("favorites") or 0)
        conversion = (contacts / views * 100.0) if views > 0 else (100.0 if contacts > 0 else 0.0)
        ranked.append({
            "item_id": iid,
            "title": item.get("title") or "",
            "url": item.get("url") or "",
            "views": views,
            "contacts": contacts,
            "favorites": favorites,
            "conversion": round(conversion, 2),
        })
    # Proven demand only. No random resurrection of zero-contact listings.
    ranked = [x for x in ranked if x["contacts"] > 0]
    ranked.sort(key=lambda x: (x["contacts"], x["conversion"], x["views"], x["favorites"]), reverse=True)
    return ranked[:max(1, min(MAX_REACTIVATE, int(limit or MAX_REACTIVATE)))]


def _queue_managed_reactivation(account_id: str, selected: list[dict]) -> tuple[list[dict], list[dict]]:
    """Clear DateEnd for exact CampaignItem -> feed identity mappings only."""
    db = SessionLocal()
    try:
        feed_row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "feed_items").first()
        if not feed_row or not feed_row.value:
            return [], [{**x, "reason": "external_reactivation_unavailable"} for x in selected]
        try:
            feed = json.loads(feed_row.value)
        except Exception:
            feed = []
        if not isinstance(feed, list):
            feed = []
        by_feed_id = {str(x.get("id") or x.get("Id") or ""): x for x in feed if isinstance(x, dict)}
        ids = [str(x["item_id"]) for x in selected]
        rows = db.query(CampaignItem).filter(
            CampaignItem.account_id == account_id,
            CampaignItem.avito_item_id.in_(ids),
        ).all() if ids else []
        mapping = {}
        for row in rows:
            feed_id = str(row.feed_identity or row.external_item_id or "").strip()
            if feed_id and feed_id in by_feed_id:
                mapping[str(row.avito_item_id)] = feed_id

        queued, external = [], []
        for item in selected:
            avito_id = str(item["item_id"])
            feed_id = mapping.get(avito_id)
            if not feed_id:
                external.append({**item, "reason": "external_reactivation_unavailable"})
                continue
            feed_item = by_feed_id[feed_id]
            # Same canonical feed item, not a duplicate. Clearing the end date
            # makes the existing autoload item eligible to become active again.
            feed_item["date_end"] = ""
            if "DateEnd" in feed_item:
                feed_item["DateEnd"] = ""
            queued.append({**item, "feed_identity": feed_id, "status": "queued_for_autoload"})

        if queued:
            # Reactivation changes only DateEnd, but still run the global media gate
            # before persisting the whole canonical feed. This prevents an old bad
            # image already present in storage from being silently reactivated.
            from app.api.avito import _assert_no_legacy_visual_urls
            for _feed_it in feed:
                _assert_no_legacy_visual_urls(db, account_id, (_feed_it or {}).get("images") or [])
            feed_row.value = json.dumps(feed, ensure_ascii=False)
            db.commit()
        return queued, external
    finally:
        db.close()


def prepare_canonical_replacement(account_id: str, limit: int = MAX_REACTIVATE) -> dict:
    """Prepare a fail-closed replacement when old external Avito ids cannot be reactivated.

    CANONICAL_REPLACEMENT_PREPARE_V1
    This is intentionally PREPARE-only: it never sends a feed and never spends money.
    It selects only existing canonical feed rows that already carry Availability and
    excludes furniture set/bed subtypes that require FurnitureSet unless that exact
    field is already present. The exact subset is then checked by BORIS' model/media
    gates and Avito's official XML checker. A later publisher may act only on status
    ``safe_feed_ready`` and the immutable prepared ids/hash.
    """
    import hashlib, os, time, asyncio
    from app.api.avito import _kpi_feed_items_load, _kpi_feed_items_to_models, _build_xml, _check_feed_images
    from app.api.parser import validate_feed_xmlcheck
    db = SessionLocal()
    try:
        _row, feed = _kpi_feed_items_load(db, account_id)
        if not feed:
            return {"status":"blocked","reason":"canonical_feed_missing","changed_avito":False}
        def _eligible(x):
            p = dict((x or {}).get("params") or {})
            subtype = str((x or {}).get("goods_subtype") or p.get("GoodsSubType") or "")
            if not str(p.get("Availability") or "").strip():
                return False
            if subtype in {"Гарнитуры и комплекты", "Спальные гарнитуры"} and not str(p.get("FurnitureSet") or "").strip():
                return False
            return True
        selected = [dict(x) for x in feed if isinstance(x, dict) and _eligible(x)]
        selected = selected[:max(1, min(MAX_REACTIVATE, int(limit or MAX_REACTIVATE)))]
        if not selected:
            return {"status":"blocked","reason":"no_safe_canonical_replacement_candidates","changed_avito":False}
        converted = _kpi_feed_items_to_models(selected)
        if converted.get("status") != "ok":
            return {"status":"blocked","reason":"replacement_model_validation_failed","validation":converted,"changed_avito":False}
        media = _check_feed_images(account_id)
        if not media.get("ok"):
            return {"status":"blocked","reason":"account_feed_media_gate_failed","media":media,"changed_avito":False}
        xml = _build_xml(converted["items"])
        digest = hashlib.sha256(xml.encode("utf-8")).hexdigest()
        rel = f"{account_id}/feed_factory_validation/canonical_replacement_{digest[:16]}.xml"
        path = os.path.join("/root/BORIS/backend/images", rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(xml)
        url = f"https://boris-ai.pro/images/{rel}?v={time.time_ns()}"
        try:
            official = asyncio.run(validate_feed_xmlcheck(url)) or {}
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try: official = loop.run_until_complete(validate_feed_xmlcheck(url)) or {}
            finally: loop.close()
        issues = list(official.get("issues") or [])
        hard = [x for x in issues if str(x.get("severity") or "error").lower() != "warning"]
        status = "safe_feed_ready" if official.get("status") == "ok" and not hard else ("checking" if official.get("status") == "pending" else "blocked")
        result = {"status":status,"account_id":account_id,"candidate_count":len(selected),
                  "candidate_ids":[str(x.get("id") or x.get("Id") or "") for x in selected],
                  "sha256":digest,"validation_url":url.split("?",1)[0],"official_status":official.get("status"),
                  "official_issue_count":len(issues),"official_hard_issue_count":len(hard),
                  "changed_feed":False,"changed_avito":False,"prepared_at":datetime.now(timezone.utc).isoformat(),
                  "publication_authorized":False,"safety":"prepare_only_no_external_write"}
        _save_state(db, account_id, {"canonical_replacement":result})
        return result
    finally:
        db.close()


def bootstrap_zero_active_portfolio(account_id: str, limit: int = MAX_REACTIVATE) -> dict:
    from app.api.avito import get_avito_token, _extract_token, _get_avito_credentials, _audit_log

    now = datetime.now(timezone.utc).isoformat()
    # INITIAL_PORTFOLIO_THROTTLE_BEFORE_TOKEN_V1: shared Retry-After wins even
    # before token refresh / accounts-self work, so another trusted worker's
    # 429 cannot be ignored by the first hourly stage.
    remaining = account_throttle_remaining(account_id)
    if remaining > 0:
        result = {"status": "blocked", "reason": "avito_account_throttled", "retry_after_seconds": remaining,
                  "account_id": account_id, "checked_at": now, "changed_feed": False}
    else:
        token_data = get_avito_token(account_id)
        if not isinstance(token_data, dict) or not token_data.get("access_token"):
            result = {"status": "blocked", "reason": "avito_not_connected", "account_id": account_id,
                      "checked_at": now, "changed_feed": False}
        else:
            token = _extract_token(token_data)
            try:
                active = _fetch_items(account_id, token, "active")
                if active:
                    result = {"status": "active_portfolio_present", "account_id": account_id,
                              "active_count": len(active), "checked_at": now, "changed_feed": False}
                else:
                    old = _fetch_items(account_id, token, "old")
                    _, _, user_id = _get_avito_credentials(account_id)
                    if not user_id:
                        _shared_throttle_guard(account_id)
                        r = httpx.get("https://api.avito.ru/core/v1/accounts/self",
                                      headers={"Authorization": f"Bearer {token}"}, timeout=20)
                        if r.status_code == 429:
                            record_account_throttle(account_id, _retry_after_seconds(r), source="initial_portfolio_self_429")
                            raise RuntimeError("avito_account_throttled")
                        user_id = str(r.json().get("id") or "") if r.status_code == 200 else ""
                    old_ids = [int(x["id"]) for x in old if str(x.get("id") or "").isdigit()]
                    stats = _fetch_stats(account_id, token, str(user_id or ""), old_ids, days=90)
                    selected = _rank_old(old, stats, limit=limit)
                    queued, external = _queue_managed_reactivation(account_id, selected)
                    result = {
                        "status": "reactivation_queued" if queued else ("no_effective_old_items" if not selected else "external_write_unavailable"),
                        "account_id": account_id,
                        "active_count": 0,
                        "old_count": len(old),
                        "effective_old_count": len([1 for x in old_ids if int((stats.get(x) or {}).get("contacts") or 0) > 0]),
                        "selected_count": len(selected),
                        "queued_count": len(queued),
                        "external_unavailable_count": len(external),
                        "selected": selected,
                        "queued": queued,
                        "external_unavailable": external,
                        "selection_rule": "contacts desc -> conversion desc -> views desc; contacts>0; max 100",
                        "checked_at": now,
                        "changed_feed": bool(queued),
                    }
                    if queued:
                        _audit_log(account_id, "initial_portfolio_reactivation",
                                   f"Ноль активных: отобрано {len(selected)}, в автозагрузку на реактивацию поставлено {len(queued)}",
                                   actor="boris_auto")
            except Exception as exc:
                reason = str(exc)[:300]
                result = {"status": "blocked" if reason.startswith("avito_account_throttled") else "error",
                          "reason": reason, "account_id": account_id,
                          "checked_at": now, "changed_feed": False}

    db = SessionLocal()
    try:
        _save_state(db, account_id, result)
    finally:
        db.close()
    return result


def post_connect_sync(account_id: str) -> dict:
    """Immediate post-connect lifecycle used by both account and inbox connectors."""
    bootstrap = bootstrap_zero_active_portfolio(account_id, MAX_REACTIVATE)
    stats = None
    try:
        from app.api.avito import collect_stats
        stats = collect_stats(account_id)
    except Exception as exc:
        stats = {"status": "error", "reason": str(exc)[:300]}
    return {"status": "ok", "account_id": account_id, "bootstrap": bootstrap,
            "stats_status": (stats or {}).get("status") if isinstance(stats, dict) else None}
