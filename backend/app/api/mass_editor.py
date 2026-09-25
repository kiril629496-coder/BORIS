from __future__ import annotations

import asyncio
import json
import os
import random
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.db.session import SessionLocal, engine
from app.models.account import Account
from app.services.exception_observability import observe_suppressed

router = APIRouter(prefix="/api/avito/mass-editor", tags=["avito-mass-editor"])

# MASS_EDITOR_CHARACTERISTICS_MEMORY_KEY_V1: persistent account-scoped schema memory.
CHAR_MEMORY_KEY = "mass_editor_characteristics_memory"

DEFAULT_FIELDS = {
    "brand": {"mode": "fixed", "value": "HAG", "label": "Бренд"},
    "product_subtype": {
        "mode": "random",
        "values": ["Ограждение", "Уголок"],
        "label": "Подтип товара",
    },
    "glass_type": {
        "mode": "random",
        "values": ["Прозрачное стекло", "Матовое стекло", "Рифлёное стекло"],
        "label": "Тип стекла",
    },
    "door_type": {
        "mode": "random",
        "values": ["Раздвижные", "Распашные"],
        "label": "Тип дверей",
    },
}

FIELD_LABEL_ALIASES = {
    "brand": ["Бренд"],
    "product_subtype": ["Подтип товара"],
    "glass_type": ["Тип стекла"],
    "door_type": ["Тип дверей"],
}

# Feed/XML names are not necessarily the labels used by the live Avito editor.
# All mutation jobs are normalized to the labels actually observed in Chrome.
LIVE_FIELD_ALIASES = {
    "ShowerEnclosureBrand": "Бренд",
    "ShowerEnclosureDoorType": "Тип дверей",
    "ShowerEnclosureGlassType": "Тип стекла",
    "ShowerEnclosureType": "Тип душевого ограждения",
    "ShowerEnclosureWidth": "Ширина",
    "ShowerEnclosureHeight": "Высота",
}


def _canonical_live_label(label: str) -> str:
    raw = str(label or "").strip()
    return LIVE_FIELD_ALIASES.get(raw, raw)


def _field_is_client_safe(field: dict[str, Any]) -> bool:
    label = str(field.get("label") or "").strip()
    if not label:
        return False
    low = label.lower()
    if low.startswith("params[") or low.startswith("iac") or label.endswith("[]"):
        return False
    # Single radio options are stale artifacts of the old scanner, not fields.
    if str(field.get("type") or "").lower() == "radio" and len(field.get("options") or []) < 2:
        return False
    return True


class FieldRule(BaseModel):
    enabled: bool = True
    mode: str = "fixed"  # fixed | random
    value: str | None = None
    values: list[str] = Field(default_factory=list)


class CreateJobRequest(BaseModel):
    account_id: str
    limit: int = Field(default=1, ge=1, le=100000)
    newest: bool = True
    segment_key: str | None = None
    fields: dict[str, FieldRule] = Field(default_factory=dict)
    confirmed: bool = False


class ScanJobRequest(BaseModel):
    account_id: str
    limit: int = Field(default=100, ge=1, le=100000)
    newest: bool = True
    segment_key: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_get(account_id: str, key: str) -> Any:
    db = SessionLocal()
    try:
        row = db.execute(text("select value from storage where account_id=:a and key=:k"), {"a": account_id, "k": key}).fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except Exception:
            return row[0]
    finally:
        db.close()


def _db_put(account_id: str, key: str, value: Any) -> None:
    # STORAGE_SINGLETON_ATOMIC_PUT_V1: serialize one logical Storage singleton
    # by account/key and collapse any historical duplicate rows before writing.
    db = SessionLocal()
    try:
        import hashlib as _hashlib
        raw = json.dumps(value, ensure_ascii=False)
        digest = _hashlib.blake2b((str(account_id)+"|"+str(key)).encode("utf-8"), digest_size=8).digest()
        lk = int.from_bytes(digest, "big", signed=False)
        if lk >= (1 << 63):
            lk -= (1 << 64)
        db.execute(text("select pg_advisory_xact_lock(:k)"), {"k": lk})
        rows = db.execute(text("select id from storage where account_id=:a and key=:k order by id desc for update"), {"a": account_id, "k": key}).fetchall()
        if rows:
            keep = int(rows[0][0])
            db.execute(text("update storage set value=:v where id=:i"), {"v": raw, "i": keep})
            if len(rows) > 1:
                db.execute(text("delete from storage where account_id=:a and key=:k and id<>:i"), {"a": account_id, "k": key, "i": keep})
        else:
            db.execute(text("insert into storage (account_id,key,value) values (:a,:k,:v)"), {"a": account_id, "k": key, "v": raw})
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _merge_characteristics_memory(account_id: str, item: dict[str, Any], fields: list[dict[str, Any]], source: str = "active_editor") -> dict[str, Any]:
    memory = _db_get(account_id, CHAR_MEMORY_KEY) or {"version": 1, "updated_at": None, "fields": {}, "items": {}}
    memory.setdefault("fields", {})
    memory.setdefault("items", {})
    now = _now()
    item_id = str(item.get("id") or "")
    compact = []
    for f in fields:
        label = str(f.get("label") or "").strip()
        if not label or len(label) > 120:
            continue
        options = []
        for v in (f.get("options") or []):
            v = str(v).strip()
            if v and v not in options and len(v) <= 160:
                options.append(v)
        current = str(f.get("current") or "").strip()
        entry = memory["fields"].setdefault(label, {"label": label, "options": [], "seen_count": 0, "last_seen": now})
        entry["seen_count"] = int(entry.get("seen_count") or 0) + 1
        entry["last_seen"] = now
        entry["source"] = source
        for v in options + ([current] if current else []):
            if v and v not in entry["options"]:
                entry["options"].append(v)
        compact.append({"label": label, "type": f.get("type") or "unknown", "options": options, "current": current})
    memory["items"][item_id] = {
        "id": item.get("id"), "title": item.get("title") or "", "category": item.get("category") or "",
        "seen_at": now, "source": source, "fields": compact,
    }
    # bounded memory: keep most recently scanned 500 listings
    if len(memory["items"]) > 500:
        ordered = sorted(memory["items"].items(), key=lambda kv: kv[1].get("seen_at") or "", reverse=True)[:500]
        memory["items"] = dict(ordered)
    memory["updated_at"] = now
    memory["active_items_scanned"] = len(memory["items"])
    _db_put(account_id, CHAR_MEMORY_KEY, memory)
    return memory


def _seed_characteristics_memory(account_id: str) -> dict[str, Any]:
    memory = _db_get(account_id, CHAR_MEMORY_KEY)
    if memory and memory.get("segments"):
        return memory

    # Seed only as a hint from BORIS-owned drafts/feed. Live Browser Gateway
    # scans later replace/confirm the exact Avito editor structure.
    field_values: dict[str, set[str]] = {}
    segments: dict[str, dict[str, Any]] = {}
    item_segments: dict[str, str] = {}
    for key in ("drafts", "feed_items"):
        data = _db_get(account_id, key) or []
        if not isinstance(data, list):
            continue
        for it in data[:500]:
            params = it.get("params") or {}
            if not isinstance(params, dict):
                params = {}
            category = str(it.get("category") or it.get("категория") or params.get("Category") or "Другие объявления").strip()
            discr = []
            for name in ("ProductType", "GoodsType", "GoodsSubType", "ServiceType", "ServiceSubtype", "ServiceSubspecies"):
                val = str(params.get(name) or "").strip()
                if val and val not in discr:
                    discr.append(val)
            segment_key = " / ".join(([category] if category else []) + discr[:3]) or "Другие объявления"
            seg = segments.setdefault(segment_key, {"key": segment_key, "title": segment_key, "item_ids": [], "fields": {}, "seen_count": 0, "source": "local_seed"})
            iid = str(it.get("id") or it.get("avito_id") or "").strip()
            if iid and iid not in seg["item_ids"]:
                seg["item_ids"].append(iid)
                item_segments[iid] = segment_key
            for k, v in params.items():
                if isinstance(v, (str, int, float)) and str(v).strip():
                    field_values.setdefault(str(k), set()).add(str(v).strip())
                f = seg["fields"].setdefault(str(k), {"label": str(k), "options": [], "seen_count": 0, "last_seen": None, "source": "local_seed"})
                f["seen_count"] = int(f.get("seen_count") or 0) + 1
                if isinstance(v, (str, int, float)) and str(v).strip() and str(v).strip() not in f["options"]:
                    f["options"].append(str(v).strip())
            seg["seen_count"] = max(len(seg["item_ids"]), int(seg.get("seen_count") or 0) + 1)

    if not segments:
        segments["Все объявления"] = {"key": "Все объявления", "title": "Все объявления", "item_ids": [], "fields": {}, "seen_count": 0, "source": "local_seed"}

    base = memory if isinstance(memory, dict) else {}
    base.update({
        "version": 2,
        "updated_at": _now(),
        "active_items_scanned": int(base.get("active_items_scanned") or 0),
        "source": base.get("source") or "local_seed",
        "fields": base.get("fields") or {k: {"label": k, "options": sorted(vals)[:50], "seen_count": 0, "last_seen": None, "source": "local_seed"} for k, vals in field_values.items()},
        "segments": segments,
        "item_segments": base.get("item_segments") or item_segments,
    })
    _db_put(account_id, CHAR_MEMORY_KEY, base)
    return base


def _latest_items(account_id: str) -> list[dict[str, Any]]:
    """Authoritative active-item source for the mass editor.

    Prefer the official Avito API whenever credentials are connected. Daily stats may
    legitimately contain zero embedded item rows even though the account has hundreds
    of live ads, so it is only a fallback cache, never the primary inventory source.
    """
    try:
        import httpx
        from app.api.avito import get_avito_token, _extract_token
        from app.services.avito_account_throttle import account_throttle_remaining as _mass_inventory_throttle, record_account_throttle as _mass_inventory_record
        # MASS_EDITOR_INVENTORY_SHARED_THROTTLE_V1: never start live inventory
        # discovery while another trusted stage is inside provider Retry-After.
        if _mass_inventory_throttle(account_id) > 0:
            raise RuntimeError("avito_account_throttled")
        token_data = get_avito_token(account_id)
        token = _extract_token(token_data) if isinstance(token_data, dict) else None
        if token:
            out: list[dict[str, Any]] = []
            live_complete = True
            for page in range(1, 21):
                if _mass_inventory_throttle(account_id) > 0:
                    live_complete = False
                    break
                r = httpx.get(
                    "https://api.avito.ru/core/v1/items",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"per_page": 100, "page": page},
                    timeout=15,
                )
                if r.status_code == 429:
                    # MASS_EDITOR_INVENTORY_RETRY_AFTER_HTTPDATE_V1: Retry-After may
                    # be delta-seconds or an RFC HTTP-date. Never shorten a provider
                    # cooldown merely because the header is not numeric.
                    try:
                        raw = str(r.headers.get("Retry-After") or "").strip()
                        if raw:
                            try:
                                delay = max(0, int(float(raw)))
                            except Exception:
                                from email.utils import parsedate_to_datetime as _mass_parse_ra
                                from datetime import datetime as _mass_dt, timezone as _mass_tz
                                when = _mass_parse_ra(raw)
                                if when.tzinfo is None:
                                    when = when.replace(tzinfo=_mass_tz.utc)
                                delay = max(0, int((when.astimezone(_mass_tz.utc) - _mass_dt.now(_mass_tz.utc)).total_seconds()))
                        else:
                            delay = 30
                    except Exception:
                        delay = 30
                    _mass_inventory_record(account_id, delay or 30, source="mass_editor_inventory_429")
                    # MASS_EDITOR_NO_PARTIAL_INVENTORY_ON_THROTTLE_V1: partial
                    # live pages are not authoritative. Fall back to persisted
                    # reporting inventory instead of returning a truncated account.
                    live_complete = False
                    break
                if r.status_code != 200:
                    live_complete = False
                    break
                rows = (r.json() or {}).get("resources") or []
                for x in rows:
                    category = x.get("category")
                    out.append({
                        "id": x.get("id"),
                        "title": x.get("title") or "",
                        "status": x.get("status") or "",
                        "category": (category.get("name") if isinstance(category, dict) else category) or "",
                        "price": x.get("price"),
                        "url": x.get("url") or "",
                        "source": "avito_api",
                    })
                if len(rows) < 100:
                    break
            if out and live_complete:
                return sorted(out, key=lambda x: int(x.get("id") or 0), reverse=True)
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=320)

    db = SessionLocal()
    try:
        row = db.execute(text("select key,value from storage where account_id=:a and key like 'daily_stats:%' order by key desc limit 1"), {"a": account_id}).fetchone()
        if not row:
            return []
        data = json.loads(row[1])
        items = data.get("items") or []
        return sorted(items, key=lambda x: int(x.get("id") or 0), reverse=True)
    finally:
        db.close()


def _segment_items(account_id: str, segment_key: str | None, strict: bool = True) -> list[dict[str, Any]]:
    items = [x for x in _latest_items(account_id) if str(x.get("status") or "").lower() == "active"]
    if not segment_key:
        return items
    memory = _db_get(account_id, CHAR_MEMORY_KEY) or {}
    seg = (memory.get("segments") or {}).get(segment_key) or {}
    ids = {str(x) for x in (seg.get("item_ids") or [])}
    matched = [x for x in items if str(x.get("id")) in ids] if ids else []
    if matched:
        return matched
    if strict:
        # Editing a mixed account without live item-to-segment mapping is unsafe.
        # Return nothing until Browser Gateway has classified the active Avito IDs.
        return []
    # Scanning is read-only, so it is safe to scan the whole top-level category
    # and let Browser Gateway discover the real subcategories and item mapping.
    top = str(segment_key).split(" / ", 1)[0].strip()
    return [x for x in items if str(x.get("category") or "").strip() == top]


def _rules(req_fields: dict[str, FieldRule]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for key, default in DEFAULT_FIELDS.items():
        rule = req_fields.get(key)
        if rule is None:
            # The HAG recipe is only the no-fields pilot default for Evgeniy.
            # As soon as the UI sends explicit fields, never add hidden mutations.
            if not req_fields:
                out[key] = dict(default)
                out[key]["enabled"] = True
            continue
        out[key] = {
            "enabled": rule.enabled,
            "mode": rule.mode,
            "value": rule.value,
            "values": rule.values,
            "label": default["label"],
        }
    # Generic contour: UI/API may send custom:<field> rules. XML/feed names are
    # converted to the actual label seen in the live Avito editor. The canonical
    # key is also used for verification so old technical names cannot break E2E.
    for key, rule in req_fields.items():
        if key in out:
            continue
        raw_label = key.split("custom:", 1)[1] if key.startswith("custom:") else key
        label = _canonical_live_label(raw_label)
        canonical_key = f"custom:{label}" if key.startswith("custom:") else label
        out[canonical_key] = {
            "enabled": rule.enabled,
            "mode": rule.mode,
            "value": rule.value,
            "values": rule.values,
            "label": label,
        }
    return out


def _assignment(item_id: int | str, rules: dict[str, dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for field, rule in rules.items():
        if not rule.get("enabled", True):
            continue
        if rule.get("mode") == "fixed":
            if rule.get("value"):
                result[field] = str(rule["value"])
        else:
            vals = list(rule.get("values") or [])
            if vals:
                rnd = random.Random(f"boris-mass-editor:{item_id}:{field}")
                result[field] = rnd.choice(vals)
    return result


def _validate_requested_fields(account_id: str, fields: dict[str, FieldRule], segment_key: str | None = None) -> str | None:
    if not fields:
        return None
    memory = _seed_characteristics_memory(account_id)
    segments = memory.get("segments") or {}
    selected = (segments.get(segment_key) if segment_key else None) or {}
    raw_fields = selected.get("fields") if selected else memory.get("fields") or {}
    catalog = raw_fields if isinstance(raw_fields, dict) else {str(f.get("label") or ""): f for f in raw_fields if isinstance(f, dict)}
    standard = {"Цена", "Описание", "Фотографии"}
    for key, rule in fields.items():
        raw_label = str(key[7:] if key.startswith("custom:") else key).strip()
        label = _canonical_live_label(raw_label)
        if label in standard or key in DEFAULT_FIELDS:
            continue
        meta = catalog.get(label) or catalog.get(raw_label)
        if not meta:
            return f"Параметр «{label}» ещё не подтверждён сканированием Avito. Сначала нажмите «Обновить параметры из Avito»."
        field_type = str(meta.get("type") or "").lower()
        options = [str(x).strip() for x in (meta.get("options") or []) if str(x).strip()]
        if field_type == "radio" and len(options) < 2:
            return f"«{label}» оказался вариантом выбора, а не отдельным параметром. Обновите параметры из Avito перед запуском."
        values = [str(rule.value).strip()] if rule.mode == "fixed" and rule.value is not None else [str(x).strip() for x in (rule.values or [])]
        # A live Avito text input is not a closed dictionary even if an older
        # local seed once contained suggested brands. Official XML validation is
        # authoritative for free-text values such as HAG.
        closed_reference = label in {"Бренд"} and field_type in {"radio", "select", "combobox"}
        if (field_type in {"radio", "select", "combobox"} or closed_reference) and options:
            invalid = [v for v in values if v and v not in options]
            if invalid:
                return f"Для «{label}» Avito принимает только значения из справочника. Недопустимо: {', '.join(invalid[:5])}."
    return None


def _plan(account_id: str, limit: int, fields: dict[str, FieldRule], segment_key: str | None = None) -> dict[str, Any]:
    items = _segment_items(account_id, segment_key)[:limit]
    # HAG + shower-enclosure defaults are only the explicit pilot recipe for Evgeniy.
    # Every other client starts from an empty mutation plan until fields are chosen from
    # that client's own learned characteristics memory.
    # Never hide a mutation recipe behind an empty UI selection. A mass edit is
    # prepared only from fields explicitly selected by the client.
    rules = _rules(fields) if fields else {}
    planned = []
    for x in items:
        planned.append({
            "id": int(x.get("id")),
            "title": x.get("title") or "",
            "status": x.get("status") or "",
            "changes": _assignment(x.get("id"), rules),
        })
    return {"account_id": account_id, "count": len(planned), "rules": rules, "items": planned}


# BORIS_MASS_EDITOR_AUTOLOAD_EXECUTOR_V1
# Feed-managed ads should be changed through Avito Autoload, not through the
# owner's currently opened Chrome session. Browser remains a fallback only for
# ads/fields that cannot be mapped safely to the feed contract.
def _try_autoload_feed_mass_edit(account_id: str, plan: dict[str, Any]) -> dict[str, Any] | None:
    planned = list(plan.get("items") or [])
    if not planned or not any((x.get("changes") or {}) for x in planned):
        return None
    try:
        import copy
        import httpx
        from app.api.avito import get_avito_token, _extract_token, feed_send_to_avito
        from app.api.parser import validate_feed_xmlcheck
    except Exception:
        return None

    # Only mappings that are unambiguous in the canonical feed are allowed.
    param_map = {
        "custom:Бренд": "ShowerEnclosureBrand",
        "Бренд": "ShowerEnclosureBrand",
    }
    requested_keys = {str(k) for row in planned for k in (row.get("changes") or {}).keys()}
    if not requested_keys or any(k not in param_map for k in requested_keys):
        return None

    from app.services.avito_account_throttle import account_throttle_remaining as _mass_edit_throttle, record_account_throttle as _mass_edit_record_throttle
    # MASS_EDITOR_AUTOLOAD_SHARED_THROTTLE_V1: resolving exact autoload identities
    # is provider I/O. Do not fall through to another executor while the tenant is
    # inside Retry-After; surface a durable blocked result instead.
    _mass_retry = _mass_edit_throttle(account_id)
    if _mass_retry > 0:
        return {"status":"blocked","reason_code":"avito_account_throttled",
                "retry_after_seconds":int(_mass_retry),"changed":0,"verified":0}
    token = _extract_token(get_avito_token(account_id))
    if not token:
        return None
    headers = {"Authorization": f"Bearer {token}"}

    def _mass_edit_record_429(resp, source: str) -> int:
        retry = 30
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
            retry = 30
        return _mass_edit_record_throttle(account_id, retry or 30, source=source)

    # Resolve Avito IDs to their exact autoload source IDs. One non-autoload item
    # makes the whole batch fall back; never partially mutate a requested batch.
    db = SessionLocal()
    try:
        acc = db.query(Account).filter(Account.account_id == account_id).first()
        avito_user_id = str(getattr(acc, "avito_user_id", "") or "") if acc else ""
    finally:
        db.close()
    if not avito_user_id:
        try:
            _mass_retry = _mass_edit_throttle(account_id)
            if _mass_retry > 0:
                return {"status":"blocked","reason_code":"avito_account_throttled",
                        "retry_after_seconds":int(_mass_retry),"changed":0,"verified":0}
            me = httpx.get("https://api.avito.ru/core/v1/accounts/self", headers=headers, timeout=15)
            if me.status_code == 429:
                _mass_retry = _mass_edit_record_429(me, "mass_editor_autoload_self_429")
                return {"status":"blocked","reason_code":"avito_account_throttled",
                        "retry_after_seconds":int(_mass_retry),"changed":0,"verified":0}
            if me.status_code == 200:
                avito_user_id = str((me.json() or {}).get("id") or "")
        except Exception:
            return None
    if not avito_user_id:
        return None

    source_ids: dict[str, str] = {}
    for row in planned:
        avito_id = str(row.get("id") or "")
        try:
            _mass_retry = _mass_edit_throttle(account_id)
            if _mass_retry > 0:
                return {"status":"blocked","reason_code":"avito_account_throttled",
                        "retry_after_seconds":int(_mass_retry),"changed":0,"verified":0}
            rr = httpx.get(
                f"https://api.avito.ru/core/v1/accounts/{avito_user_id}/items/{avito_id}/",
                headers=headers, timeout=15,
            )
            if rr.status_code == 429:
                _mass_retry = _mass_edit_record_429(rr, "mass_editor_autoload_item_429")
                return {"status":"blocked","reason_code":"avito_account_throttled",
                        "retry_after_seconds":int(_mass_retry),"changed":0,"verified":0}
            payload = rr.json() if rr.status_code == 200 else {}
            source_id = str((payload or {}).get("autoload_item_id") or "").strip()
        except Exception:
            source_id = ""
        if not source_id:
            return None
        source_ids[avito_id] = source_id

    # Lock authoritative feed_items, take a durable backup, and apply only the
    # mapped params for the requested source IDs.
    db = SessionLocal()
    backup_key = ""
    original_raw = ""
    try:
        rec = db.execute(text("select id,value from storage where account_id=:a and key='feed_items' for update"), {"a": account_id}).fetchone()
        if not rec:
            return None
        original_raw = str(rec[1] or "")
        items = json.loads(original_raw)
        by_id = {str(x.get("id") or ""): x for x in items if isinstance(x, dict)}
        if any(source_id not in by_id for source_id in source_ids.values()):
            db.rollback()
            return None
        future = copy.deepcopy(items)
        future_by_id = {str(x.get("id") or ""): x for x in future if isinstance(x, dict)}
        actual = []
        for row in planned:
            avito_id = str(row.get("id") or "")
            target = future_by_id[source_ids[avito_id]]
            params = target.setdefault("params", {})
            for field, value in (row.get("changes") or {}).items():
                feed_key = param_map[str(field)]
                old = params.get(feed_key)
                if str(old or "") == str(value or ""):
                    continue
                params[feed_key] = value
                actual.append({"avito_id": avito_id, "autoload_item_id": source_ids[avito_id], "field": field, "feed_key": feed_key, "old": old, "new": value})
        if not actual:
            db.rollback()
            return {
                "status":"waiting_external_verification","executor":"autoload_feed","changed":0,"verified":0,
                "source_verified":len(planned),"external_verified":0,"upload_id":None,"changes":[],
                "verification_required":True,
                "message":"Запрошенные значения уже находятся в authoritative feed. BORIS подтвердит их в реальном редакторе Avito перед статусом «Готово».",
            }
        # Mass editor is an authoritative feed writer too. Apply the same
        # fail-closed media gate before taking/sending the mutated snapshot.
        from app.api.avito import _assert_no_legacy_visual_urls
        for _feed_it in future:
            _assert_no_legacy_visual_urls(db, account_id, (_feed_it or {}).get("images") or [])
        backup_key = "feed_items_bak_massedit_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:6]
        db.execute(text("insert into storage(account_id,key,value) values(:a,:k,:v)"), {"a":account_id,"k":backup_key,"v":original_raw})
        db.execute(text("update storage set value=:v where id=:i"), {"v":json.dumps(future,ensure_ascii=False),"i":rec[0]})
        db.commit()
    finally:
        db.close()

    # Official Avito XML validator is mandatory before upload. On any failure,
    # restore the exact snapshot before returning.
    feed_url = f"https://boris-ai.pro/api/avito/feed/{account_id}.xml"
    try:
        validation = asyncio.run(validate_feed_xmlcheck(feed_url)) or {}
    except Exception as exc:
        validation = {"status":"error","error":str(exc)[:200]}
    if str(validation.get("status") or "").lower() != "ok":
        db = SessionLocal()
        try:
            # Recovery snapshots are historical data and must obey the CURRENT
            # visual policy. Never restore a snapshot that would resurrect a
            # forbidden legacy provider just because the mutation failed later.
            from app.api.avito import _assert_no_legacy_visual_urls
            _restore_items=json.loads(original_raw or "[]")
            for _feed_it in _restore_items if isinstance(_restore_items,list) else []:
                _assert_no_legacy_visual_urls(db, account_id, (_feed_it or {}).get("images") or [])
            db.execute(text("update storage set value=:v where account_id=:a and key='feed_items'"), {"v":original_raw,"a":account_id})
            db.commit()
        finally:
            db.close()
        return {"status":"error","executor":"autoload_feed","code":"feed_validation_failed","validation":validation,"backup_key":backup_key}

    upload = feed_send_to_avito(account_id)
    _upload_status=str(upload.get("status") or "")
    if _upload_status == "delivery_unknown":
        # Critical crash-consistency rule: Avito may already have accepted the
        # new feed. Restoring the old local feed here would create split-brain
        # between BORIS and Avito and a later retry could duplicate the launch.
        # Keep the exact submitted snapshot and queue read-only verification.
        return {"status":"waiting_external_verification","executor":"autoload_feed",
                "code":"autoload_submit_delivery_unknown","upload":upload,"upload_id":None,
                "changed":len(actual),"verified":0,"changes":actual,"backup_key":backup_key,
                "verification_required":True,"delivery_unknown":True,
                "validation":{"status":"ok","issues":validation.get("issues") or []}}
    if _upload_status != "ok":
        db = SessionLocal()
        try:
            from app.api.avito import _assert_no_legacy_visual_urls
            _restore_items=json.loads(original_raw or "[]")
            for _feed_it in _restore_items if isinstance(_restore_items,list) else []:
                _assert_no_legacy_visual_urls(db, account_id, (_feed_it or {}).get("images") or [])
            db.execute(text("update storage set value=:v where account_id=:a and key='feed_items'"), {"v":original_raw,"a":account_id})
            db.commit()
        finally:
            db.close()
        return {"status":"error","executor":"autoload_feed","code":"autoload_submit_failed","upload":upload,"backup_key":backup_key}
    return {
        "status":"submitted_autoload","executor":"autoload_feed","upload_id":upload.get("upload_id"),
        "changed":len(actual),"verified":0,"changes":actual,"backup_key":backup_key,
        "validation":{"status":"ok","issues":validation.get("issues") or []},
    }


def _queue_external_verification_job(account_id: str, parent_job_id: str, plan: dict[str, Any], delay_seconds: int = 0) -> dict[str, Any]:
    """Queue a READ-only isolated-browser verification for an autoload mutation.

    The parent autoload job is not considered complete until this verifier reads
    the concrete Avito editor field. No user confirmation is needed because this
    mode never writes; it only verifies the already-requested state.
    """
    job_id = "massverify_" + uuid.uuid4().hex[:12]
    items = []
    for row in list(plan.get("items") or []):
        changes = dict(row.get("changes") or {})
        if not changes:
            continue
        items.append({
            "id": int(row.get("id")), "title": row.get("title") or "", "status": row.get("status") or "",
            "changes": changes, "run_status": "waiting_verification", "error": None,
        })
    now_dt = datetime.now(timezone.utc)
    not_before_at = (now_dt + timedelta(seconds=max(0, int(delay_seconds or 0)))).isoformat() if delay_seconds else None
    job = {
        "job_id": job_id, "mode": "verify", "status": "queued", "created_at": now_dt.isoformat(), "updated_at": now_dt.isoformat(),
        "account_id": account_id, "parent_job_id": parent_job_id, "total": len(items), "processed": 0,
        "changed": 0, "verified": 0, "skipped": 0, "failed": 0, "items": items, "rules": dict(plan.get("rules") or {}),
        "executor_selected": "isolated_browser", "executor": "isolated_browser", "read_only": True,
        "confirmed": False, "not_before_at": not_before_at, "error": None,
        "cost": {"openai_rub": 0.0, "browser_rub": 0.0, "proxy_estimate_rub": 0.0},
    }
    _db_put(account_id, f"mass_editor_job:{job_id}", job)
    return job


def _verify_autoload_parent_via_official_report(account_id: str, parent: dict[str, Any]) -> dict[str, Any]:
    """Read-only external verification for exact feed-managed mutations.

    Avito v4's current upload item report does not echo arbitrary field values,
    so BORIS never fabricates them. Verification is accepted only when all of
    these independent facts line up for every target item:
      * the current Avito upload started after this mass-edit job was created;
      * the exact Avito id is present in that upload with active status;
      * Avito classified it as successful / no changes;
      * the exact report ad_id exists in BORIS' authoritative feed; and
      * that feed contains every requested mapped value.

    "success_skipped" is especially useful: Avito explicitly says the submitted
    source has no changes against the live item. This is stronger and safer than
    scraping the editor through a blocked browser route.
    """
    try:
        import httpx
        from app.api.avito import get_avito_token, _extract_token, _autoload_upload_snapshot
        from app.services.avito_account_throttle import account_throttle_remaining as _mass_throttle_remaining, record_account_throttle as _mass_record_throttle
        # MASS_EDITOR_SHARED_ACCOUNT_THROTTLE_V1: verification is read-only but must
        # not bypass a Retry-After already observed by marketer/stats workers.
        _shared_retry = _mass_throttle_remaining(account_id)
        if _shared_retry > 0:
            return {"status":"unavailable","reason":"avito_account_throttled","http_status":429,"retry_after_seconds":int(_shared_retry)}
        token = _extract_token(get_avito_token(account_id))
        if not token:
            return {"status":"unavailable","reason":"token_unavailable"}
        snap = {}
        # MASS_EDITOR_CONFIRMED_429_NO_RETRY_V1: a confirmed Avito throttle is
        # terminal for this report cycle. Never turn Retry-After into an immediate
        # polling loop; the next trusted cycle owns the retry.
        snap = _autoload_upload_snapshot(account_id) or {}
        if str(snap.get("status") or "") != "ok":
            return {"status":"unavailable","reason":"upload_report_unavailable","report":snap}
        try:
            job_created = datetime.fromisoformat(str(parent.get("created_at") or "").replace("Z","+00:00"))
            upload_started = datetime.fromisoformat(str(snap.get("started_at") or "").replace("Z","+00:00"))
            if job_created.tzinfo is None: job_created = job_created.replace(tzinfo=timezone.utc)
            if upload_started.tzinfo is None: upload_started = upload_started.replace(tzinfo=timezone.utc)
            if upload_started < job_created:
                return {"status":"pending","reason":"upload_predates_job","upload_id":snap.get("upload_id")}
        except Exception:
            return {"status":"pending","reason":"upload_time_unproven","upload_id":snap.get("upload_id")}

        headers={"Authorization":f"Bearer {token}"}
        def _report_page(page_no: int):
            # MASS_EDITOR_REPORT_429_NO_RETRY_V1: report pagination is read-only
            # but still consumes provider quota. A confirmed 429 performs zero
            # same-cycle retries; preserve fail-closed report semantics.
            retry = _mass_throttle_remaining(account_id)
            if retry > 0:
                return None
            resp = httpx.get("https://api.avito.ru/autoload/v4/uploads/current/items",headers=headers,params={"page":page_no},timeout=20)
            if resp.status_code == 429:
                # MASS_EDITOR_REPORT_RETRY_AFTER_HTTPDATE_V1: honor both numeric
                # Retry-After and RFC HTTP-date before publishing tenant cooldown.
                try:
                    raw = str(resp.headers.get("Retry-After") or "").strip()
                    if raw:
                        try:
                            delay = max(0, int(float(raw)))
                        except Exception:
                            from email.utils import parsedate_to_datetime as _mass_report_parse_ra
                            from datetime import datetime as _mass_report_dt, timezone as _mass_report_tz
                            when = _mass_report_parse_ra(raw)
                            if when.tzinfo is None:
                                when = when.replace(tzinfo=_mass_report_tz.utc)
                            delay = max(0, int((when.astimezone(_mass_report_tz.utc) - _mass_report_dt.now(_mass_report_tz.utc)).total_seconds()))
                    else:
                        delay = 30
                except Exception:
                    delay = 30
                _mass_record_throttle(account_id, delay or 30, source="mass_editor_report_429")
            return resp
        first=_report_page(1)
        if first is None or first.status_code != 200:
            return {"status":"unavailable","reason":"items_report_http","http_status":getattr(first,"status_code",None)}
        body=first.json() or {}; report_items=list(body.get("items") or [])
        meta=body.get("meta") or {}; pages=max(1,int(meta.get("pages") or 1))
        for page_no in range(2,min(pages,50)+1):
            rr=_report_page(page_no)
            if rr is None or rr.status_code != 200:
                return {"status":"unavailable","reason":"items_report_page_http","page":page_no,"http_status":getattr(rr,"status_code",None)}
            report_items.extend((rr.json() or {}).get("items") or [])
        report_by_avito={str(x.get("avito_id") or ""):x for x in report_items if x.get("avito_id")}

        feed_raw=_db_get(account_id,"feed_items")
        # _db_get may deserialize storage already; retain compatibility with direct JSON.
        feed_items=feed_raw if isinstance(feed_raw,list) else []
        if not feed_items:
            db=SessionLocal()
            try:
                row=db.execute(text("select value from storage where account_id=:a and key='feed_items' limit 1"),{"a":account_id}).fetchone()
                feed_items=json.loads(row[0] or "[]") if row else []
            finally:
                db.close()
        feed_by_id={str(x.get("id") or ""):x for x in feed_items if isinstance(x,dict)}
        param_map={"custom:Бренд":"ShowerEnclosureBrand","Бренд":"ShowerEnclosureBrand"}
        verified=[]; pending=[]
        for row in list(parent.get("items") or []):
            changes=dict(row.get("changes") or {})
            if not changes: continue
            avito_id=str(row.get("id") or "")
            rep=report_by_avito.get(avito_id) or {}
            ad_id=str(rep.get("ad_id") or "")
            feed=feed_by_id.get(ad_id) or {}
            params=feed.get("params") or {}
            section=str((rep.get("section") or {}).get("slug") or "")
            external_ok=(str(rep.get("avito_status") or "").lower()=="active" and section in {"success","success_skipped","success_updated"})
            values_ok=True
            for field,value in changes.items():
                feed_key=param_map.get(str(field))
                if not feed_key or str(params.get(feed_key) or "") != str(value or ""):
                    values_ok=False; break
            if external_ok and values_ok and ad_id:
                verified.append({"avito_id":avito_id,"ad_id":ad_id,"section":section,"avito_status":rep.get("avito_status")})
            else:
                pending.append({"avito_id":avito_id,"ad_id":ad_id or None,"section":section or None,"avito_status":rep.get("avito_status"),"external_ok":external_ok,"values_ok":values_ok})
        total=sum(1 for x in list(parent.get("items") or []) if (x.get("changes") or {}))
        if total>0 and len(verified)==total and not pending:
            return {"status":"verified","verified":len(verified),"total":total,"failed":0,"upload_id":snap.get("upload_id"),"avito_status":snap.get("avito_status"),"evidence":"avito_v4_current_items_plus_authoritative_feed","items":verified}
        return {"status":"pending","verified":len(verified),"total":total,"failed":0,"upload_id":snap.get("upload_id"),"pending":pending[:50]}
    except Exception as exc:
        return {"status":"unavailable","reason":type(exc).__name__,"detail":str(exc)[:240]}


def _sync_external_verification_parent(account_id: str, verification_job: dict[str, Any]) -> None:
    """Propagate verifier truth to its autoload parent without inventing success."""
    if str(verification_job.get("mode") or "") != "verify":
        return
    parent_job_id = str(verification_job.get("parent_job_id") or "")
    if not parent_job_id:
        return
    parent = _db_get(account_id, f"mass_editor_job:{parent_job_id}") or {}
    if not parent:
        return
    child_status = str(verification_job.get("status") or "")
    # Browser verification is preferred when an account-scoped route exists.
    # If that route is blocked, use Avito's own autoload report as a strictly
    # read-only verifier for exact feed-managed items instead of stalling forever.
    if child_status == "blocked_network_route" and str(parent.get("executor") or "") == "autoload_feed":
        official = _verify_autoload_parent_via_official_report(account_id, parent)
        if str(official.get("status") or "") == "verified":
            verification_job["status"] = "completed"
            verification_job["processed"] = int(official.get("total") or 0)
            verification_job["verified"] = int(official.get("verified") or 0)
            verification_job["failed"] = 0
            verification_job["error"] = None
            verification_job["official_autoload_verification"] = official
            verification_job["updated_at"] = _now()
            _db_put(account_id, f"mass_editor_job:{verification_job.get('job_id')}", verification_job)
            child_status = "completed"
    external = dict(parent.get("external_verification") or {})
    external.update({
        "job_id": verification_job.get("job_id"), "status": child_status,
        "verified": int(verification_job.get("verified") or 0), "total": int(verification_job.get("total") or 0),
        "failed": int(verification_job.get("failed") or 0), "checked_at": _now(),
        "error": verification_job.get("error"),
    })
    parent["external_verification"] = external
    autoload = dict(parent.get("autoload") or {})
    autoload["external_verified"] = int(verification_job.get("verified") or 0)
    parent["autoload"] = autoload
    if child_status == "completed" and int(verification_job.get("total") or 0) > 0 and int(verification_job.get("verified") or 0) == int(verification_job.get("total") or 0):
        parent["status"] = "completed"
        parent["verified"] = int(verification_job.get("verified") or 0)
        parent["completed_at"] = _now()
        parent["error"] = None
    elif child_status == "completed_with_errors":
        parent["status"] = "completed_with_errors"
        parent["error"] = "Внешняя проверка Avito не подтвердила все запрошенные значения"
    else:
        parent["status"] = "waiting_external_verification"
    parent["updated_at"] = _now()
    _db_put(account_id, f"mass_editor_job:{parent_job_id}", parent)


@router.get("/characteristics")
def characteristics(account_id: str, segment_key: str | None = None):
    memory = _seed_characteristics_memory(account_id)
    segments_raw = memory.get("segments") or {}
    segments = []
    for key, seg in segments_raw.items():
        segments.append({"key": key, "title": seg.get("title") or key, "seen_count": int(seg.get("seen_count") or 0), "source": seg.get("source") or memory.get("source") or "memory"})
    segments.sort(key=lambda x: (-x["seen_count"], x["title"]))
    selected = (segments_raw.get(segment_key) if segment_key else None) or None
    raw_fields = (selected or {}).get("fields") if selected else memory.get("fields")
    raw_fields = raw_fields or {}
    source_fields = list(raw_fields.values()) if isinstance(raw_fields, dict) else list(raw_fields)

    # Build one client-facing catalog. Live Avito labels win over XML/feed aliases,
    # and stale single-option radio artifacts/internal form names are hidden.
    merged: dict[str, dict[str, Any]] = {}
    for raw in source_fields:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        original_label = str(item.get("label") or "").strip()
        canonical = _canonical_live_label(original_label)
        item["label"] = canonical
        if not _field_is_client_safe(item):
            continue
        existing = merged.get(canonical)
        score = (2 if item.get("source") == "browser_gateway" else 1, int(item.get("seen_count") or 0))
        old_score = ((2 if existing and existing.get("source") == "browser_gateway" else 1), int((existing or {}).get("seen_count") or 0)) if existing else (0, 0)
        if existing is None or score > old_score:
            merged[canonical] = item
        elif existing:
            opts = list(existing.get("options") or [])
            for option in item.get("options") or []:
                if option not in opts:
                    opts.append(option)
            existing["options"] = opts[:250]
    fields = list(merged.values())
    fields.sort(key=lambda x: (-int(x.get("seen_count") or 0), str(x.get("label") or "")))
    return {
        "status": "ok", "account_id": account_id, "updated_at": memory.get("updated_at"),
        "active_items_scanned": memory.get("active_items_scanned", 0), "source": memory.get("source") or "memory",
        "segment_key": segment_key, "segments": segments, "fields": fields,
    }


@router.post("/scan-jobs")
def create_scan_job(req: ScanJobRequest):
    from app.api.browser_gateway import _execution_route
    route = _execution_route(req.account_id, "scan_characteristics", "READ")
    isolated_fallback = route.get("executor") == "blocked"
    if isolated_fallback:
        route = {"executor":"isolated_browser","reason":"account_browser_session_fallback","browser_required":False}
    items = _segment_items(req.account_id, req.segment_key, strict=False)[:req.limit]
    job_id = "massscan_" + uuid.uuid4().hex[:12]
    job = {
        "job_id": job_id, "mode": "scan", "status": "queued", "created_at": _now(), "updated_at": _now(),
        "account_id": req.account_id, "segment_key": req.segment_key, "total": len(items), "processed": 0, "changed": 0, "verified": 0,
        "skipped": 0, "failed": 0,
        "items": [{"id": int(x.get("id")), "title": x.get("title") or "", "category": x.get("category") or "", "status": x.get("status") or "", "changes": {}} for x in items],
        "rules": {}, "route": route, "executor_selected": route.get("executor"), "cost": {"openai_rub": 0.0, "browser_rub": 0.0, "proxy_estimate_rub": 0.0}, "error": None,
    }
    _db_put(req.account_id, f"mass_editor_job:{job_id}", job)
    if isolated_fallback:
        job["status"] = "queued"
        job["executor"] = "isolated_browser"
        job["updated_at"] = _now()
        _db_put(req.account_id, f"mass_editor_job:{job_id}", job)
        return {"status":"queued","job_id":job_id,"total":job["total"],"items":job["items"],"executor":"isolated_browser"}
    job["status"] = "waiting_gateway"
    job["executor"] = "browser_gateway"
    job["updated_at"] = _now()
    _db_put(req.account_id, f"mass_editor_job:{job_id}", job)
    return {"status": "queued", "job_id": job_id, "total": job["total"], "items": job["items"], "executor": "browser_gateway"}


@router.get("/preview")
def preview(account_id: str, limit: int = 10):
    return {"status": "ok", **_plan(account_id, max(1, min(limit, 100)), {})}




def _identity_guard_key(account_id: str) -> str:
    return "browser_identity_guard"

def _identity_guard(account_id: str) -> dict:
    return _db_get(account_id, _identity_guard_key(account_id)) or {}

def _identity_guard_fresh_ok(account_id: str, max_age_seconds: int = 300) -> tuple[bool, dict]:
    guard = _identity_guard(account_id)
    if not guard.get("verified"):
        return False, guard
    try:
        checked = datetime.fromisoformat(str(guard.get("checked_at") or ""))
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - checked).total_seconds()
        return age <= max_age_seconds, guard
    except Exception:
        return False, guard

@router.post("/jobs")
def create_job(req: CreateJobRequest):
    validation_error = _validate_requested_fields(req.account_id, req.fields, req.segment_key)
    if validation_error:
        return {"status": "error", "message": validation_error}
    plan = _plan(req.account_id, req.limit, req.fields, req.segment_key)
    if not req.confirmed:
        return {
            "status": "needs_confirmation",
            "message": "Сначала проверьте план массовой правки. Остальные поля объявления BORIS не меняет.",
            **plan,
        }
    # Preferred executor for feed-managed ads: official Avito Autoload. This path
    # is independent from the owner's Chrome identity and therefore avoids cross-
    # account cookie conflicts entirely.
    autoload_result = _try_autoload_feed_mass_edit(req.account_id, plan)
    if autoload_result is not None:
        job_id = "massedit_" + uuid.uuid4().hex[:12]
        autoload_status = str(autoload_result.get("status") or "")
        needs_external_verification = autoload_status in {"waiting_external_verification", "submitted_autoload"}
        parent_status = "waiting_external_verification" if needs_external_verification else autoload_status
        job = {
            "job_id": job_id,
            "status": parent_status,
            "created_at": _now(), "updated_at": _now(), "account_id": req.account_id,
            "segment_key": req.segment_key, "total": len(plan["items"]),
            "processed": len(plan["items"]), "changed": int(autoload_result.get("changed") or 0),
            "verified": 0 if needs_external_verification else int(autoload_result.get("verified") or 0), "skipped": 0,
            "failed": 0 if autoload_status != "error" else len(plan["items"]),
            "items": plan["items"], "rules": plan["rules"],
            "route": {"executor":"autoload_feed","reason":"autoload_managed_item"},
            "executor_selected": "autoload_feed", "executor": "autoload_feed",
            "upload_id": autoload_result.get("upload_id"), "autoload": autoload_result,
            "external_verification": {"status":"pending"} if needs_external_verification else None,
            "error": None if autoload_status != "error" else str(autoload_result.get("code") or "autoload_error"),
        }
        _db_put(req.account_id, f"mass_editor_job:{job_id}", job)
        verification_job = None
        if needs_external_verification:
            # A just-submitted feed may take time to propagate through Avito. If
            # the source already contained the value, verification can start now.
            delay_seconds = 180 if autoload_status == "submitted_autoload" else 0
            verification_job = _queue_external_verification_job(req.account_id, job_id, plan, delay_seconds=delay_seconds)
            job["verification_job_id"] = verification_job.get("job_id")
            job["external_verification"] = {
                "status":"queued", "job_id":verification_job.get("job_id"),
                "not_before_at":verification_job.get("not_before_at"),
            }
            job["updated_at"] = _now()
            _db_put(req.account_id, f"mass_editor_job:{job_id}", job)
        return {
            "status": parent_status, "job_id": job_id, "total": job["total"], "items": job["items"],
            "verification_job_id": (verification_job or {}).get("job_id"), **autoload_result,
            "status": parent_status,
        }

    identity_ok, identity = _identity_guard_fresh_ok(req.account_id)
    if not identity_ok:
        # A different account may be open in the owner's ordinary Chrome. Do not
        # block the account here: route it to its isolated BORIS browser session.
        # The isolated runner performs its own login/identity verification before WRITE.
        identity = identity or {}
    job_id = "massedit_" + uuid.uuid4().hex[:12]
    from app.api.browser_gateway import _execution_route
    route = _execution_route(req.account_id, "mass_edit_characteristics", "WRITE")
    isolated_fallback = route.get("executor") == "blocked"
    if isolated_fallback:
        # Account-scoped server browser is independent from whichever Avito account
        # happens to be open in the owner's Chrome. Credentials are never exposed.
        route = {"executor":"isolated_browser","reason":"account_browser_session_fallback","browser_required":False}
    job = {
        "job_id": job_id,
        "status": "queued",
        "created_at": _now(),
        "updated_at": _now(),
        "account_id": req.account_id,
        "segment_key": req.segment_key,
        "total": len(plan["items"]),
        "processed": 0,
        "changed": 0,
        "verified": 0,
        "skipped": 0,
        "failed": 0,
        "items": plan["items"],
        "rules": plan["rules"],
        "confirmed": True,
        "confirmed_at": _now(),
        "route": route,
        "executor_selected": route.get("executor"),
        "cost": {"openai_rub": 0.0, "browser_rub": 0.0, "proxy_estimate_rub": 0.0},
        "error": None,
    }
    _db_put(req.account_id, f"mass_editor_job:{job_id}", job)
    if isolated_fallback:
        # No account-bound Chrome session: execute in a dedicated isolated browser
        # instead of borrowing another client's cookies. The worker persists state
        # per account and fails closed on Avito verification challenges.
        job["status"] = "queued"
        job["executor"] = "isolated_browser"
        job["updated_at"] = _now()
        _db_put(req.account_id, f"mass_editor_job:{job_id}", job)
        return {"status":"queued","job_id":job_id,"total":job["total"],"items":job["items"],"executor":"isolated_browser"}
    # Preferred path when the exact account already has its own live Gateway.
    job["status"] = "waiting_gateway"
    job["executor"] = "browser_gateway"
    job["updated_at"] = _now()
    _db_put(req.account_id, f"mass_editor_job:{job_id}", job)
    return {"status": "queued", "job_id": job_id, "total": job["total"], "items": job["items"], "executor": "browser_gateway"}


@router.get("/jobs/current")
def get_current_job(account_id: str):
    """Return the newest durable mass-editor job for this account.

    The UI is disposable: navigation, refresh, another tab or another device must
    never be the source of truth for progress. Job state lives in storage and the
    page reattaches to it whenever it mounts again.
    """
    db = SessionLocal()
    try:
        rows = db.execute(text("select key,value from storage where account_id=:a and key like 'mass_editor_job:%' order by id desc limit 20"), {"a": account_id}).fetchall()
        # UI navigation/refresh must reattach to every non-terminal or
        # owner-action-required state of both Browser Gateway and the server-side
        # isolated browser. Otherwise a real durable job appears to disappear.
        active_statuses = {
            "waiting_gateway", "queued_gateway", "claimed_gateway", "running_gateway", "resume_gateway",
            "queued", "claimed_isolated", "starting", "running", "resume_isolated", "waiting_avito_code", "waiting_external_verification",
            "blocked_network_route", "blocked_captcha", "blocked_identity", "blocked_credentials",
            "blocked_auth_or_ui", "blocked_transport",
        }
        for _key, raw in rows:
            try:
                job = json.loads(raw)
            except Exception:
                continue
            if isinstance(job, dict) and str(job.get("status") or "") in active_statuses:
                return {"status": "ok", "job": job}
    finally:
        db.close()
    return {"status": "ok", "job": None}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, account_id: str):
    job = _db_get(account_id, f"mass_editor_job:{job_id}")
    if not job:
        return {"status": "error", "message": "Задание не найдено"}
    return {"status": "ok", "job": job}


ISOLATED_SESSION_KEY = "isolated_browser_session"
ISOLATED_ROUTE_KEY = "isolated_browser_route"
ISOLATED_CODE_KEY = "isolated_browser_verification_code"


def _isolated_state(account_id: str, **updates) -> dict[str, Any]:
    state = _db_get(account_id, ISOLATED_SESSION_KEY) or {"account_id": account_id, "state": "not_started"}
    state.update(updates)
    state["updated_at"] = _now()
    _db_put(account_id, ISOLATED_SESSION_KEY, state)
    return state


def _occupied_isolated_route_ports(account_id: str) -> set[int]:
    """Ports currently pinned to other ready client sessions."""
    db = SessionLocal()
    used: set[int] = set()
    try:
        rows = db.execute(text("select account_id,value from storage where key=:k and account_id<>:a"), {"k":ISOLATED_ROUTE_KEY,"a":account_id}).fetchall()
        for other_account_id, raw in rows:
            try:
                route = json.loads(raw)
            except Exception:
                continue
            if str(route.get("state") or "") != "ready":
                continue
            try:
                port = int(route.get("port") or 0)
            except Exception:
                port = 0
            if port > 0:
                used.add(port)
    finally:
        db.close()
    return used


async def _account_isolated_proxy(account_id: str) -> dict[str, str] | None:
    """Return a verified, account-pinned Avito proxy. Never fall back to datacenter IP."""
    from proxy_pool import PROXY_HOST, PROXY_USER, PROXY_PASS, find_clean_port, get_avito_proxy_health, avito_proxy_port_reachable
    if not all([PROXY_HOST, PROXY_USER, PROXY_PASS]):
        _db_put(account_id, ISOLATED_ROUTE_KEY, {"state":"network_route_required","reason":"proxy_pool_not_configured","updated_at":_now()})
        return None
    route = _db_get(account_id, ISOLATED_ROUTE_KEY) or {}
    # Negative cache: when the provider currently has no Avito-clean exit, fail
    # in milliseconds instead of burning ~1 minute on every user click.
    if route.get("state") == "network_route_required":
        try:
            checked = datetime.fromisoformat(str(route.get("checked_at") or ""))
            if checked.tzinfo is None:
                checked = checked.replace(tzinfo=timezone.utc)
            reason = str(route.get("reason") or "")
            # Provider transport/auth outages can recover quickly after billing or
            # provider maintenance. Avito-level route blocks change more slowly.
            ttl = 60 if reason in {"proxy_transport_down","proxy_transport_degraded","proxy_auth_failed","no_clean_avito_proxy"} else 300
            if (datetime.now(timezone.utc) - checked).total_seconds() < ttl:
                return None
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=1186)
    port = route.get("port")
    try:
        if port and int(port) > 0:
            pinned_port = int(port)
            if await avito_proxy_port_reachable(pinned_port):
                route.update({"state":"ready","checked_at":_now(),"updated_at":_now(),"sticky":True,"account_isolated":True})
                _db_put(account_id, ISOLATED_ROUTE_KEY, route)
                return {"server": f"http://{PROXY_HOST}:{pinned_port}", "username": PROXY_USER, "password": PROXY_PASS}
            _db_put(account_id, ISOLATED_ROUTE_KEY, {
                "state":"network_route_required","reason":"proxy_transport_down","provider_state":"transport_down",
                "last_port":pinned_port,"checked_at":_now(),"updated_at":_now(),"account_isolated":True,
            })
            route = {}
    except Exception:
        route = {}
    occupied_ports = _occupied_isolated_route_ports(account_id)
    port = await find_clean_port(max_tries=120, exclude_ports=occupied_ports, cache_key=account_id)
    if not port:
        health = get_avito_proxy_health() or {}
        health_state = str(health.get("state") or "unknown")
        reason_map = {
            "not_configured": "proxy_pool_not_configured",
            "transport_down": "proxy_transport_down",
            "transport_degraded": "proxy_transport_degraded",
            "auth_failed": "proxy_auth_failed",
            "avito_blocked": "no_avito_usable_exit",
        }
        reason = reason_map.get(health_state, str(health.get("detail") or "no_avito_usable_exit"))
        _db_put(account_id, ISOLATED_ROUTE_KEY, {
            "state":"network_route_required", "reason":reason,
            "provider_state":health_state,
            "tested_ports":int(health.get("tested_ports") or 0),
            "open_ports":int(health.get("open_ports") or 0),
            "checked_at":_now(), "updated_at":_now(),
        })
        return None
    route = {"state":"ready","port":int(port),"checked_at":_now(),"updated_at":_now(),"assigned_at":_now(),"sticky":True,"account_isolated":True,"occupied_ports_avoided":len(occupied_ports)}
    _db_put(account_id, ISOLATED_ROUTE_KEY, route)
    return {"server": f"http://{PROXY_HOST}:{int(port)}", "username": PROXY_USER, "password": PROXY_PASS}


class IsolatedVerificationCodeRequest(BaseModel):
    account_id: str
    code: str


@router.post("/isolated/verification-code")
def isolated_verification_code(req: IsolatedVerificationCodeRequest):
    code = "".join(ch for ch in str(req.code or "") if ch.isdigit())[:12]
    if len(code) < 4:
        return {"status":"error","message":"Некорректный код Avito"}
    _db_put(req.account_id, ISOLATED_CODE_KEY, {"code":code,"created_at":_now(),"used":False})
    # If the first 5-minute wait already elapsed, requeue the durable job. The
    # persistent Chromium profile keeps this account isolated across retries.
    db = SessionLocal()
    resumed = None
    try:
        rows = db.execute(text("select id,value from storage where account_id=:a and key like 'mass_editor_job:%' order by id desc limit 20"), {"a":req.account_id}).fetchall()
        for row_id, raw in rows:
            try: job = json.loads(raw)
            except Exception: continue
            if str(job.get("executor") or job.get("executor_selected") or "") == "isolated_browser" and str(job.get("status") or "") == "waiting_avito_code":
                job["status"] = "resume_isolated"; job["updated_at"] = _now(); job["error"] = None
                db.execute(text("update storage set value=:v where id=:i"), {"v":json.dumps(job,ensure_ascii=False),"i":row_id})
                db.commit(); resumed = job.get("job_id"); break
    finally:
        db.close()
    return {"status":"ok","message":"Код принят и будет передан в изолированную сессию Avito","resumed_job_id":resumed}


@router.get("/isolated/status")
def isolated_status(account_id: str):
    return {
        "status":"ok","account_id":account_id,
        "session":_db_get(account_id, ISOLATED_SESSION_KEY) or {},
        "route":_db_get(account_id, ISOLATED_ROUTE_KEY) or {},
        "provider":_db_get("__browser_gateway__", "isolated_route_provider_health") or {},
    }


async def _wait_and_submit_avito_code(page, account_id: str, timeout_seconds: int = 300) -> bool:
    """Wait for a one-time Avito verification code without keeping it after use."""
    deadline = time.time() + max(30, timeout_seconds)
    _isolated_state(account_id, state="waiting_avito_code", code_required=True)
    while time.time() < deadline:
        rec = _db_get(account_id, ISOLATED_CODE_KEY) or {}
        code = str(rec.get("code") or "").strip()
        if code and not rec.get("used"):
            inputs = page.locator('input[inputmode="numeric"],input[name*="code" i],input[autocomplete="one-time-code"],input[type="tel"]')
            if await inputs.count():
                await inputs.first.fill(code)
                btn = page.get_by_role("button", name="Продолжить", exact=False)
                if await btn.count() == 0:
                    btn = page.get_by_role("button", name="Подтвердить", exact=False)
                if await btn.count():
                    await btn.first.click()
                rec["used"] = True; rec["used_at"] = _now(); rec["code"] = ""
                _db_put(account_id, ISOLATED_CODE_KEY, rec)
                await page.wait_for_timeout(2500)
                return True
        await asyncio.sleep(2)
    return False


async def _run_job(account_id: str, job_id: str) -> None:
    key = f"mass_editor_job:{job_id}"
    job = _db_get(account_id, key)
    if not job:
        return
    job["status"] = "starting"
    job["updated_at"] = _now()
    _db_put(account_id, key, job)

    # Exact autoload verification does not require browser credentials or a
    # residential route when Avito's own v4 report already proves the submitted
    # source is synchronized with the live item. Try that first; fall back to the
    # isolated editor only when official evidence is incomplete/unavailable.
    if str(job.get("mode") or "") == "verify":
        parent_id=str(job.get("parent_job_id") or "")
        parent=_db_get(account_id, f"mass_editor_job:{parent_id}") if parent_id else None
        if isinstance(parent,dict) and str(parent.get("executor") or "") == "autoload_feed":
            official=_verify_autoload_parent_via_official_report(account_id,parent)
            if str(official.get("status") or "") == "verified":
                job["status"]="completed"
                job["processed"]=int(official.get("total") or 0)
                job["verified"]=int(official.get("verified") or 0)
                job["failed"]=0
                job["error"]=None
                job["official_autoload_verification"]=official
                job["updated_at"]=_now()
                _db_put(account_id,key,job)
                _sync_external_verification_parent(account_id,job)
                return

    db = SessionLocal()
    account = db.query(Account).filter(Account.account_id == account_id).first()
    db.close()
    if not account or not account.avito_login or not account.avito_password:
        job["status"] = "blocked_credentials"
        job["error"] = "Для браузерной правки не сохранены логин/пароль Avito"
        job["updated_at"] = _now()
        _db_put(account_id, key, job)
        _sync_external_verification_parent(account_id, job)
        return

    try:
        from playwright.async_api import async_playwright
        proxy = await _account_isolated_proxy(account_id)
        if not proxy:
            route_state = _db_get(account_id, ISOLATED_ROUTE_KEY) or {}
            route_reason = str(route_state.get("reason") or "no_avito_usable_exit")
            message_map = {
                "proxy_transport_down": "Прокси-провайдер Avito сейчас недоступен: соединение с его портами отклоняется. BORIS не использует обычный Chrome или чужие cookies и повторит проверку маршрута позже.",
                "proxy_transport_degraded": "Прокси-маршрут Avito нестабилен. BORIS остановил браузерную задачу до восстановления безопасного маршрута.",
                "proxy_auth_failed": "Прокси-провайдер Avito отклонил авторизацию. Браузерная задача остановлена до восстановления доступа к маршруту.",
                "proxy_pool_not_configured": "Для изолированной Avito-сессии не настроен отдельный сетевой маршрут.",
                "no_avito_usable_exit": "Сейчас нет проверенного сетевого маршрута до Avito для изолированной сессии.",
            }
            job["status"] = "blocked_network_route"
            job["error"] = message_map.get(route_reason, message_map["no_avito_usable_exit"])
            job["route_reason"] = route_reason
            job["updated_at"] = _now()
            _db_put(account_id, key, job)
            _isolated_state(account_id, state="network_route_required", ready=False, code_required=False, error=job["error"], route_reason=route_reason)
            _sync_external_verification_parent(account_id, job)
            return

        async with async_playwright() as p:
            from pathlib import Path
            profile_dir = Path("/root/BORIS/backend/data/avito-browser-sessions") / account_id
            profile_dir.mkdir(parents=True, exist_ok=True)
            _isolated_state(account_id, state="starting", ready=False, code_required=False, profile_dir=str(profile_dir), route="residential_pinned")
            # One account = one Chromium profile/cookie jar. This is deliberately
            # isolated from the owner's normal Chrome and from every other client.
            context = await p.chromium.launch_persistent_context(
                str(profile_dir), headless=True, args=["--no-sandbox"], proxy=proxy,
                viewport={"width": 1440, "height": 1000}, locale="ru-RU",
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            )
            browser = context.browser
            page = context.pages[0] if context.pages else await context.new_page()
            page.set_default_timeout(12000)

            # Login once into THIS account's persistent profile. Normal Chrome cookies are never imported.
            try:
                await page.goto("https://www.avito.ru/profile", wait_until="domcontentloaded", timeout=35000)
            except Exception as e:
                await context.close()
                raise RuntimeError(f"Avito недоступен из isolated browser: {type(e).__name__}")

            await page.wait_for_timeout(1200)
            try:
                gate_text = (await page.locator("body").inner_text(timeout=8000)).lower()
            except Exception:
                raise RuntimeError("Avito не отдал страницу isolated browser")
            if "доступ ограничен" in gate_text or "проблема с ip" in gate_text:
                _db_put(account_id, ISOLATED_ROUTE_KEY, {"state":"network_route_required","reason":"avito_ip_blocked","checked_at":_now(),"updated_at":_now()})
                _isolated_state(account_id, state="network_route_required", ready=False, code_required=False, error="Avito ограничил текущий IP")
                await context.close()
                raise RuntimeError("Avito ограничил IP isolated browser")

            if "/login" in page.url or "auth" in page.url or "войти" in gate_text[:2500]:
                _isolated_state(account_id, state="logging_in", ready=False, code_required=False)
                login_inputs = page.locator('input[type="text"],input[type="email"],input[type="tel"]')
                if await login_inputs.count() == 0:
                    raise RuntimeError("Форма входа Avito не найдена")
                await login_inputs.first.fill(account.avito_login)
                for txt in ["Продолжить", "Войти"]:
                    btn = page.get_by_role("button", name=txt, exact=False)
                    if await btn.count():
                        await btn.first.click(); await page.wait_for_timeout(1200); break
                body = (await page.locator("body").inner_text()).lower()
                if "captcha" in body or "капча" in body:
                    _isolated_state(account_id, state="captcha_required", ready=False, code_required=False)
                    raise RuntimeError("Avito запросил captcha в isolated browser")
                pw = page.locator('input[type="password"]')
                if await pw.count() == 0:
                    # Some Avito login variants ask for a one-time code before password.
                    if "введите код" in body or "код из смс" in body or "код отправ" in body:
                        ok = await _wait_and_submit_avito_code(page, account_id)
                        if not ok:
                            raise RuntimeError("Avito verification code timeout")
                    else:
                        raise RuntimeError("Поле пароля Avito не найдено")
                else:
                    await pw.first.fill(account.avito_password)
                    btn = page.get_by_role("button", name="Войти", exact=False)
                    if await btn.count(): await btn.first.click()
                    await page.wait_for_timeout(2200)
                    body = (await page.locator("body").inner_text()).lower()
                    if "captcha" in body or "капча" in body:
                        _isolated_state(account_id, state="captcha_required", ready=False, code_required=False)
                        raise RuntimeError("Avito запросил captcha в isolated browser")
                    if "код из смс" in body or "введите код" in body or "код отправ" in body:
                        ok = await _wait_and_submit_avito_code(page, account_id)
                        if not ok:
                            raise RuntimeError("Avito verification code timeout")

            # Identity verification is mandatory before any WRITE. Official API is the reference.
            await page.goto("https://www.avito.ru/profile/pro/items", wait_until="domcontentloaded", timeout=35000)
            await page.wait_for_timeout(900)
            profile_text = (await page.locator("body").inner_text(timeout=8000))
            readiness = execution_readiness(account_id)
            expected_name = str(((readiness.get("api") or {}).get("name") or "")).strip()
            expected_id = str(((readiness.get("api") or {}).get("avito_user_id") or "")).strip()
            norm = lambda s: " ".join(str(s or "").lower().replace("ё", "е").split())
            tokens = [t for t in norm(expected_name).split() if len(t) >= 3]
            profile_norm = norm(profile_text)
            identity_verified = bool(tokens and all(t in profile_norm for t in tokens))
            guard = {"verified":identity_verified,"checked_at":_now(),"expected_name":expected_name,"expected_avito_user_id":expected_id,"page_url":page.url,"source":"isolated_browser"}
            _db_put(account_id, _identity_guard_key(account_id), guard)
            if not identity_verified:
                _isolated_state(account_id, state="identity_mismatch", ready=False, code_required=False, expected_name=expected_name, expected_avito_user_id=expected_id)
                raise RuntimeError("Isolated browser Avito identity mismatch")
            _isolated_state(account_id, state="ready", ready=True, code_required=False, expected_name=expected_name, expected_avito_user_id=expected_id, identity_verified_at=_now())

            job["status"] = "running"
            _db_put(account_id, key, job)

            for idx, item in enumerate(job["items"]):
                _prior_run_status = str(item.get("run_status") or "")
                # Restart-safe item checkpoint: already proven terminal items are
                # never submitted again when a worker process resumes the job.
                if _prior_run_status in {"verified", "scanned", "skipped_no_fields"}:
                    job["processed"] = max(int(job.get("processed") or 0), idx + 1)
                    continue
                _ambiguous_save = _prior_run_status in {"save_submitting", "save_submitted"}
                item["run_status"] = "verifying_ambiguous_save" if _ambiguous_save else "opening"
                item["error"] = None
                _db_put(account_id, key, job)
                try:
                    await page.goto(f"https://www.avito.ru/items/edit/{item['id']}", wait_until="commit", timeout=20000)
                    await page.wait_for_timeout(1200)
                    if str(item["id"]) not in page.url and "/items/edit/" not in page.url:
                        raise RuntimeError("Редактор объявления не открылся")

                    discovered = await _discover_characteristics(page)
                    _merge_characteristics_memory(account_id, item, discovered, source="active_editor")
                    item["characteristics_found"] = len(discovered)
                    if _ambiguous_save and job.get("mode") == "edit":
                        # A crash may happen after the Save click but before BORIS
                        # persisted the response. Never click Save twice. Reopen
                        # read-only and prove the exact intended values instead.
                        expected_changes = {f:item.get("changes",{}).get(f) for f in (item.get("changed_fields") or item.get("changes",{}).keys())}
                        checks=[]
                        for field,value in expected_changes.items():
                            checks.append(await _verify_field(page,field,value))
                        if expected_changes and checks and all(checks):
                            item["run_status"]="verified"
                            item["reconciled_after_ambiguous_save"]=True
                            job["verified"] += 1
                        else:
                            item["run_status"]="verification_failed"
                            item["error"]="Неоднозначный Save после сбоя: BORIS не повторяет запись без нового подтверждения"
                            item["requires_reconfirmation"]=True
                            job["failed"] += 1
                        job["processed"] = idx + 1
                        job["updated_at"] = _now()
                        _db_put(account_id,key,job)
                        continue
                    if job.get("mode") == "scan":
                        item["run_status"] = "scanned"
                        job["verified"] += 1
                        job["processed"] = idx + 1
                        job["updated_at"] = _now()
                        _db_put(account_id, key, job)
                        await page.wait_for_timeout(900 + (idx % 3) * 300)
                        continue

                    if job.get("mode") == "verify":
                        # READ-only external proof. Never mutate the editor in
                        # verification mode; only inspect the requested fields.
                        expected_changes = dict(item.get("changes") or {})
                        checks = []
                        for field, value in expected_changes.items():
                            checks.append(await _verify_field(page, field, value))
                        if expected_changes and checks and all(checks):
                            item["run_status"] = "verified"
                            item["verified_fields"] = list(expected_changes.keys())
                            job["verified"] += 1
                        else:
                            item["run_status"] = "verification_failed"
                            item["error"] = "Avito не подтвердил сохранённое значение поля"
                            item["verification"] = {field: bool(ok) for field, ok in zip(expected_changes.keys(), checks)}
                            job["failed"] += 1
                        job["processed"] = idx + 1
                        job["updated_at"] = _now()
                        _db_put(account_id, key, job)
                        await page.wait_for_timeout(900 + (idx % 3) * 300)
                        continue

                    changed_fields = []
                    for field, value in item.get("changes", {}).items():
                        ok = await _set_field(page, field, value)
                        if ok:
                            changed_fields.append(field)
                        else:
                            item.setdefault("skipped_fields", []).append(field)

                    if not changed_fields:
                        item["run_status"] = "skipped_no_fields"
                        job["skipped"] += 1
                    else:
                        save = page.get_by_role("button", name="Сохранить", exact=False)
                        if await save.count() == 0:
                            save = page.get_by_role("button", name="Продолжить", exact=False)
                        if await save.count() == 0:
                            raise RuntimeError("Кнопка сохранения не найдена")
                        # Durable write-ahead checkpoint BEFORE the browser side
                        # effect. If the process dies after click, recovery enters
                        # verification-only mode and never blindly clicks twice.
                        item["run_status"] = "save_submitting"
                        item["changed_fields"] = list(changed_fields)
                        item["save_intent_at"] = _now()
                        job["updated_at"] = _now()
                        _db_put(account_id,key,job)
                        await save.first.click()
                        item["run_status"] = "save_submitted"
                        item["save_submitted_at"] = _now()
                        job["updated_at"] = _now()
                        _db_put(account_id,key,job)
                        await page.wait_for_timeout(1800)
                        job["changed"] += 1
                        # Verification pass: reopen and check only values we changed.
                        await page.goto(f"https://www.avito.ru/items/edit/{item['id']}", wait_until="commit", timeout=20000)
                        await page.wait_for_timeout(900)
                        checks = []
                        for field in changed_fields:
                            checks.append(await _verify_field(page, field, item["changes"][field]))
                        if checks and all(checks):
                            item["run_status"] = "verified"
                            job["verified"] += 1
                except Exception as e:
                    item["run_status"] = "failed"
                    item["error"] = str(e)[:300]
                    job["failed"] += 1

                job["processed"] = idx + 1
                job["updated_at"] = _now()
                _db_put(account_id, key, job)
                # Human-like safe pace; no parallel hammering of Avito.
                await page.wait_for_timeout(1500 + (idx % 3) * 500)

            await context.close()
            job["status"] = "completed" if job["failed"] == 0 else "completed_with_errors"
    except Exception as e:
        msg = str(e)[:500]
        low = msg.lower()
        if "ip isolated browser" in low or "network_route" in low or "residential" in low:
            job["status"] = "blocked_network_route"
            _isolated_state(account_id, state="network_route_required", ready=False, code_required=False, error=msg)
        elif "verification code timeout" in low:
            job["status"] = "waiting_avito_code"
            _isolated_state(account_id, state="waiting_avito_code", ready=False, code_required=True, error=None)
        elif "captcha" in low:
            job["status"] = "blocked_captcha"
            _isolated_state(account_id, state="captcha_required", ready=False, code_required=False, error=msg)
        elif "identity mismatch" in low:
            job["status"] = "blocked_identity"
            _isolated_state(account_id, state="identity_mismatch", ready=False, code_required=False, error=msg)
        elif "недоступен" in low or "не отдал страницу" in low:
            job["status"] = "blocked_transport"
            _isolated_state(account_id, state="transport_error", ready=False, code_required=False, error=msg)
        else:
            job["status"] = "blocked_auth_or_ui"
            _isolated_state(account_id, state="auth_or_ui_error", ready=False, code_required=False, error=msg)
        job["error"] = msg

    job["updated_at"] = _now()
    _db_put(account_id, key, job)
    _sync_external_verification_parent(account_id, job)


def _claim_next_isolated_job(exclude_accounts: set[str] | None = None) -> tuple[str, str] | None:
    """Atomically claim one durable isolated-browser job for a free account."""
    exclude_accounts = {str(x) for x in (exclude_accounts or set())}
    db = SessionLocal()
    try:
        rows = db.execute(text("select id,account_id,key,value from storage where key like 'mass_editor_job:%' order by id asc for update skip locked")).fetchall()
        for row_id, account_id, key, raw in rows:
            if str(account_id) in exclude_accounts:
                continue
            try:
                job = json.loads(raw)
            except Exception:
                continue
            if str(job.get("executor") or job.get("executor_selected") or "") != "isolated_browser":
                continue
            if str(job.get("status") or "") not in {"queued", "resume_isolated"}:
                continue
            not_before = str(job.get("not_before_at") or "").strip()
            if not_before:
                try:
                    ready_at = datetime.fromisoformat(not_before)
                    if ready_at.tzinfo is None:
                        ready_at = ready_at.replace(tzinfo=timezone.utc)
                    if datetime.now(timezone.utc) < ready_at:
                        continue
                except Exception as _suppressed_exc:
                    # Invalid delay metadata must not strand a durable READ job.
                    observe_suppressed(__name__, _suppressed_exc, line=1626)
            job["status"] = "claimed_isolated"
            job["worker_claimed_at"] = _now()
            job["updated_at"] = _now()
            db.execute(text("update storage set value=:v where id=:i"), {"v":json.dumps(job,ensure_ascii=False),"i":row_id})
            db.commit()
            return str(account_id), str(job.get("job_id") or key.split(":",1)[-1])
        db.commit()
        return None
    except Exception:
        db.rollback()
        return None
    finally:
        db.close()


def _isolated_network_recovery_policy(job: dict[str, Any], now: datetime | None = None) -> str:
    """Pure safety policy: resume_read | resume_write | reconfirm | skip."""
    now = now or datetime.now(timezone.utc)
    mode = str(job.get("mode") or "edit").lower()
    attempts = int(job.get("route_recovery_attempts") or 0)
    try:
        created = datetime.fromisoformat(str(job.get("created_at") or ""))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        created_age = (now - created).total_seconds()
    except Exception:
        created_age = 10**12
    # External verification is READ-only and is the proof required before a
    # mutation can become green. Keep retrying it longer than ordinary scans;
    # temporary proxy outages must not silently strand a false-unverified result.
    if mode == "verify":
        return "resume_read" if created_age <= 7 * 24 * 3600 and attempts < 50 else "skip"
    if attempts >= 5:
        return "skip"
    if mode == "scan":
        return "resume_read" if created_age <= 24 * 3600 else "skip"
    try:
        confirmed_at = datetime.fromisoformat(str(job.get("confirmed_at") or ""))
        if confirmed_at.tzinfo is None:
            confirmed_at = confirmed_at.replace(tzinfo=timezone.utc)
        confirmation_age = (now - confirmed_at).total_seconds()
    except Exception:
        confirmation_age = 10**12
    if job.get("confirmed") is True and confirmation_age <= 2 * 3600:
        return "resume_write"
    return "reconfirm"


def _expire_stale_network_scans(now: datetime | None = None) -> int:
    """Terminalize abandoned READ-only scans that can no longer auto-resume.

    A plain scan older than 24h is already `skip` by the canonical recovery
    policy. Leaving it in `blocked_network_route` forever makes the UI and
    Guardian report a live outage for work that is intentionally unrecoverable.
    Only unconfirmed, parentless scan jobs are touched; verify/write jobs keep
    their stronger proof/reconfirmation semantics.
    """
    now = now or datetime.now(timezone.utc)
    db = SessionLocal(); expired = 0
    try:
        rows = db.execute(text("select id,value from storage where key like 'mass_editor_job:%' order by id asc for update skip locked")).fetchall()
        for row_id, raw in rows:
            try:
                job = json.loads(raw)
            except Exception:
                continue
            if str(job.get("executor") or job.get("executor_selected") or "") != "isolated_browser":
                continue
            if str(job.get("status") or "") != "blocked_network_route":
                continue
            if str(job.get("mode") or "").lower() != "scan":
                continue
            if job.get("confirmed") is True or job.get("parent_job_id"):
                continue
            if _isolated_network_recovery_policy(job, now=now) != "skip":
                continue
            job["status"] = "expired_network_scan"
            job["expired_reason"] = "read_only_scan_older_than_24h"
            job["expired_at"] = now.isoformat()
            job["updated_at"] = now.isoformat()
            db.execute(text("update storage set value=:v where id=:i"), {"v":json.dumps(job, ensure_ascii=False), "i":row_id})
            expired += 1
        db.commit()
        return expired
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _recover_network_blocked_isolated_jobs(provider_state: str) -> dict[str, int]:
    """Resume only safe recent jobs after the proxy transport actually returns.

    READ scans may resume for 24h. WRITE jobs resume only when the original owner
    confirmation is explicitly persisted and younger than 2h. Older writes stay
    blocked and require a new confirmation, which prevents surprise mutations
    after a long provider outage.
    """
    if str(provider_state or "") not in {"transport_ok", "ready"}:
        return {"resumed_read": 0, "resumed_write": 0, "needs_reconfirmation": 0}
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    resumed_read = resumed_write = needs_reconfirmation = 0
    try:
        rows = db.execute(text("select id,account_id,value from storage where key like 'mass_editor_job:%' order by id asc for update skip locked")).fetchall()
        for row_id, account_id, raw in rows:
            try:
                job = json.loads(raw)
            except Exception:
                continue
            if str(job.get("executor") or job.get("executor_selected") or "") != "isolated_browser":
                continue
            if str(job.get("status") or "") != "blocked_network_route":
                continue
            reason = str(job.get("route_reason") or "")
            if not reason:
                route_rec = _db_get(str(account_id), ISOLATED_ROUTE_KEY) or {}
                reason = str(route_rec.get("reason") or "")
            if reason not in {"proxy_transport_down", "proxy_transport_degraded", "proxy_auth_failed", "no_avito_usable_exit", "avito_ip_blocked"}:
                continue
            attempts = int(job.get("route_recovery_attempts") or 0)
            policy = _isolated_network_recovery_policy(job, now=now)
            if policy == "skip":
                continue
            if policy == "reconfirm":
                job["requires_reconfirmation"] = True
                job["reconfirmation_reason"] = "network_outage_confirmation_expired"
                job["updated_at"] = _now()
                db.execute(text("update storage set value=:v where id=:i"), {"v":json.dumps(job, ensure_ascii=False), "i":row_id})
                needs_reconfirmation += 1
                continue
            if policy == "resume_read":
                resumed_read += 1
            elif policy == "resume_write":
                resumed_write += 1
            else:
                continue
            job["status"] = "resume_isolated"
            job["route_recovery_attempts"] = attempts + 1
            job["auto_resumed_at"] = _now()
            job["error"] = None
            job["route_reason"] = None
            job["updated_at"] = _now()
            db.execute(text("update storage set value=:v where id=:i"), {"v":json.dumps(job, ensure_ascii=False), "i":row_id})
            # Remove the negative route cache for this account. The resumed job
            # must perform a fresh Avito-exit validation before opening Chromium.
            route_row = db.execute(text("select id,value from storage where account_id=:a and key=:k limit 1"), {"a":account_id,"k":ISOLATED_ROUTE_KEY}).fetchone()
            route = {"state":"probe_required","reason":None,"provider_state":provider_state,"checked_at":_now(),"updated_at":_now()}
            if route_row:
                db.execute(text("update storage set value=:v where id=:i"), {"v":json.dumps(route, ensure_ascii=False),"i":route_row[0]})
            else:
                db.execute(text("insert into storage(account_id,key,value) values(:a,:k,:v)"), {"a":account_id,"k":ISOLATED_ROUTE_KEY,"v":json.dumps(route, ensure_ascii=False)})
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return {"resumed_read":resumed_read,"resumed_write":resumed_write,"needs_reconfirmation":needs_reconfirmation}


def _current_network_blocked_isolated_job_count() -> int:
    """Return the current blocked isolated-browser jobs, never historical revisions.

    Storage may contain multiple rows/revisions for the same logical job. Provider
    probing is meaningful only while the latest state of at least one job is
    blocked_network_route. This keeps an old incident from hammering Avito forever.
    """
    db = SessionLocal()
    try:
        rows = db.execute(text("""
            select distinct on (account_id,key) value
              from storage
             where key like 'mass_editor_job:%'
             order by account_id,key,id desc
        """)).fetchall()
        blocked = 0
        for (raw,) in rows:
            try:
                job = json.loads(raw)
            except Exception:
                continue
            if (str(job.get("executor") or job.get("executor_selected") or "") == "isolated_browser"
                    and str(job.get("status") or "") == "blocked_network_route"):
                blocked += 1
        return blocked
    finally:
        db.close()


def _probe_and_recover_network_blocked_jobs() -> dict[str, Any]:
    """Probe only while real current jobs are blocked, then recover durably.

    Avito-level IP blocks are retried at most every five minutes. Transport/auth
    outages keep the outer 60-second retry cadence. With no blocked job there is
    nothing to heal, so no provider/Avito request is made at all.
    """
    try:
        blocked_jobs = _current_network_blocked_isolated_job_count()
        previous = _db_get("__browser_gateway__", "isolated_route_provider_health") or {}
        if blocked_jobs <= 0:
            return {
                "provider": previous, "blocked_jobs": 0, "skipped_no_blocked_jobs": True,
                "resumed_read": 0, "resumed_write": 0, "needs_reconfirmation": 0,
            }
        previous_state = str(previous.get("state") or "")
        try:
            previous_age = max(0.0, time.time() - float(previous.get("checked_at") or 0))
        except Exception:
            previous_age = 10**9
        if previous_state == "avito_blocked" and previous_age < 300:
            return {
                "provider": previous, "blocked_jobs": blocked_jobs,
                "skipped_avito_block_backoff": True,
                "retry_after_seconds": max(1, int(300 - previous_age)),
                "resumed_read": 0, "resumed_write": 0, "needs_reconfirmation": 0,
            }
        from proxy_pool import find_clean_port, get_avito_proxy_health
        # TCP-open is not enough: a proxy may accept connections while Avito
        # returns 429 / "problem with IP". Recovery is allowed only after a real
        # browser-level Avito validation marks the provider ready.
        clean_port = asyncio.run(find_clean_port(max_tries=3, cache_key="provider_watchdog"))
        health = get_avito_proxy_health()
        if clean_port and str(health.get("state") or "") not in {"ready"}:
            health["state"] = "ready"
        safe_health = {k: health.get(k) for k in ["state","checked_at","detail","open_ports","tested_ports","blocked_attempts","transport_errors","auth_errors"] if k in health}
        safe_health["clean_route_available"] = bool(clean_port)
        safe_health["updated_at"] = _now()
        _db_put("__browser_gateway__", "isolated_route_provider_health", safe_health)
        # Recovery is permitted only when this exact probe returned a usable
        # Avito exit. Never trust a stale/global health state by itself: the
        # provider cache can still say `ready` while the current browser probe
        # returned no port. This prevents one blocked route from being resumed
        # every minute forever.
        recovery_state = "ready" if clean_port else str(health.get("state") or "")
        if not clean_port and recovery_state in {"ready", "transport_ok"}:
            recovery_state = "avito_blocked"
        recovered = _recover_network_blocked_isolated_jobs(recovery_state)
        return {"provider":safe_health, **recovered}
    except Exception as exc:
        return {"provider":{"state":"probe_error","error_type":type(exc).__name__},"resumed_read":0,"resumed_write":0,"needs_reconfirmation":0}


def _recover_isolated_jobs() -> int:
    db = SessionLocal(); recovered = 0
    try:
        rows = db.execute(text("select id,value from storage where key like 'mass_editor_job:%' order by id asc")).fetchall()
        for row_id, raw in rows:
            try: job = json.loads(raw)
            except Exception: continue
            if str(job.get("executor") or job.get("executor_selected") or "") != "isolated_browser": continue
            if str(job.get("status") or "") not in {"claimed_isolated","starting","running"}: continue
            try:
                updated = datetime.fromisoformat(str(job.get("updated_at") or job.get("worker_claimed_at") or ""))
                if updated.tzinfo is None: updated = updated.replace(tzinfo=timezone.utc)
                if (datetime.now(timezone.utc)-updated).total_seconds() < 90: continue
            except Exception as _suppressed_exc:
                observe_suppressed(__name__, _suppressed_exc, line=1833)
            job["status"] = "resume_isolated"; job["updated_at"] = _now(); job["recovered_after_restart"] = True
            db.execute(text("update storage set value=:v where id=:i"), {"v":json.dumps(job,ensure_ascii=False),"i":row_id}); recovered += 1
        db.commit(); return recovered
    except Exception:
        db.rollback(); return recovered
    finally:
        db.close()


def _try_distributed_account_browser_lock(account_id: str):
    """Hold one PostgreSQL session-level lock for this Avito account.

    The returned SQLAlchemy Connection owns the advisory lock until explicitly
    released/closed. This makes the one-account/one-browser invariant survive a
    future second worker node; the lock is not merely process-local.
    """
    conn = engine.connect()
    try:
        acquired = bool(conn.execute(text(
            "select pg_try_advisory_lock(hashtextextended(:key, 744615))"
        ), {"key": "boris:avito-browser:" + str(account_id)}).scalar())
        # End the SELECT transaction while keeping the same physical connection
        # checked out; session-level advisory locks survive COMMIT. This avoids
        # idle-in-transaction while Playwright may work for minutes.
        conn.commit()
        if acquired:
            return conn
    except Exception:
        try: conn.rollback()
        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=1864)
    conn.close()
    return None


def _release_distributed_account_browser_lock(conn, account_id: str) -> None:
    if conn is None:
        return
    try:
        conn.execute(text(
            "select pg_advisory_unlock(hashtextextended(:key, 744615))"
        ), {"key": "boris:avito-browser:" + str(account_id)})
        conn.commit()
    except Exception:
        try: conn.rollback()
        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=1879)
    finally:
        try: conn.close()
        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=1882)


def isolated_worker_loop() -> None:
    """Authoritative bounded isolated-browser dispatcher.

    Different accounts may run concurrently, but the same account is never
    executed twice at once because one persistent Chromium profile/cookie jar
    belongs to exactly one account_id. Durable job state survives process restarts.
    """
    _recover_isolated_jobs()
    try:
        max_concurrency = max(1, min(int(os.environ.get("BORIS_ISOLATED_BROWSER_WORKERS", "3")), 6))
    except Exception:
        max_concurrency = 3
    active: dict[str, threading.Thread] = {}
    lock = threading.Lock()
    next_route_recovery_at = 0.0
    route_recovery_interval = max(30, min(int(os.environ.get("BORIS_AVITO_ROUTE_RECOVERY_SECONDS", "60")), 300))

    def execute_claim(account_id: str, job_id: str) -> None:
        distributed_lock = None
        try:
            distributed_lock = _try_distributed_account_browser_lock(account_id)
            if distributed_lock is None:
                # Another BORIS worker node currently owns this account. Requeue
                # without treating it as an error; PostgreSQL is the distributed
                # source of truth for account/browser exclusivity.
                job = _db_get(account_id, f"mass_editor_job:{job_id}") or {}
                if job:
                    job["status"] = "resume_isolated"
                    job["updated_at"] = _now()
                    job["distributed_lock_wait"] = True
                    _db_put(account_id, f"mass_editor_job:{job_id}", job)
                return
            asyncio.run(_run_job(account_id, job_id))
        except Exception as exc:
            job = _db_get(account_id, f"mass_editor_job:{job_id}") or {}
            job["status"] = "completed_with_errors"
            job["error"] = f"isolated worker: {type(exc).__name__}: {str(exc)[:300]}"
            job["updated_at"] = _now()
            _db_put(account_id, f"mass_editor_job:{job_id}", job)
        finally:
            _release_distributed_account_browser_lock(distributed_lock, account_id)
            with lock:
                active.pop(account_id, None)

    while True:
        now_mono = time.monotonic()
        if now_mono >= next_route_recovery_at:
            expired_scans = _expire_stale_network_scans()
            recovery = _probe_and_recover_network_blocked_jobs()
            next_route_recovery_at = now_mono + route_recovery_interval
            resumed = int(recovery.get("resumed_read") or 0) + int(recovery.get("resumed_write") or 0)
            if expired_scans:
                print(f"ISOLATED_ROUTE_EXPIRED_STALE_SCANS: {expired_scans}", flush=True)
            if resumed or int(recovery.get("needs_reconfirmation") or 0):
                print(f"ISOLATED_ROUTE_RECOVERY: {json.dumps(recovery, ensure_ascii=False)[:1200]}", flush=True)
        with lock:
            # Defensive cleanup in case a thread exited before its finally block
            # could remove itself (e.g. interpreter-level cancellation).
            dead = [account_id for account_id, thread in active.items() if not thread.is_alive()]
            for account_id in dead:
                active.pop(account_id, None)
            busy_accounts = set(active)
            capacity = max_concurrency - len(active)
        if capacity <= 0:
            time.sleep(0.5)
            continue
        claimed = _claim_next_isolated_job(busy_accounts)
        if not claimed:
            time.sleep(1.0)
            continue
        account_id, job_id = claimed
        with lock:
            # The dispatcher is single-threaded, but keep the invariant explicit
            # in case this implementation is reused from another runtime later.
            if account_id in active:
                job = _db_get(account_id, f"mass_editor_job:{job_id}") or {}
                if job:
                    job["status"] = "resume_isolated"
                    job["updated_at"] = _now()
                    _db_put(account_id, f"mass_editor_job:{job_id}", job)
                continue
            thread = threading.Thread(
                target=execute_claim, args=(account_id, job_id), daemon=True,
                name=f"avito_isolated_{account_id[:28]}",
            )
            active[account_id] = thread
            thread.start()
        time.sleep(0.1)


async def _discover_characteristics(page) -> list[dict[str, Any]]:
    """Read visible editable fields from the real Avito editor without changing anything."""
    try:
        return await page.evaluate(r"""() => {
          const clean = (s) => (s || '').replace(/\s+/g,' ').trim();
          const result = []; const seen = new Set();
          const push = (label, type, options, current) => {
            label = clean(label); if (!label || label.length > 120) return;
            options = (options || []).map(clean).filter(Boolean).filter((v,i,a)=>a.indexOf(v)===i).slice(0,80);
            current = clean(current);
            const sig = label + '|' + type; if (seen.has(sig)) return; seen.add(sig);
            result.push({label, type, options, current});
          };
          document.querySelectorAll('select').forEach(el => {
            const label = clean(el.getAttribute('aria-label')) || clean(el.labels && el.labels[0] && el.labels[0].innerText) || clean(el.closest('label') && el.closest('label').innerText);
            push(label, 'select', [...el.options].map(o=>o.textContent), el.options[el.selectedIndex] && el.options[el.selectedIndex].textContent);
          });
          const radios = {};
          document.querySelectorAll('input[type=radio]').forEach(el => {
            const name = el.name || el.getAttribute('data-marker') || 'radio';
            const lab = clean(el.labels && el.labels[0] && el.labels[0].innerText) || clean(el.closest('label') && el.closest('label').innerText) || clean(el.value);
            if (!radios[name]) radios[name] = {els:[], options:[]}; radios[name].els.push(el); if(lab) radios[name].options.push(lab);
          });
          Object.values(radios).forEach(g => {
            const el = g.els[0];
            let root = el.closest('fieldset') || el.parentElement?.parentElement || el.parentElement;
            const legend = root && root.querySelector && root.querySelector('legend');
            let label = clean(legend && legend.innerText) || clean(root && root.querySelector && root.querySelector('h1,h2,h3,h4,[data-marker*=title],[class*=title]')?.innerText);
            if (!label) { const aria = el.getAttribute('aria-label'); label = clean(aria && aria.split(':')[0]); }
            const checked = g.els.find(x=>x.checked);
            const cur = checked ? (clean(checked.labels && checked.labels[0] && checked.labels[0].innerText) || clean(checked.value)) : '';
            push(label, 'radio', g.options, cur);
          });
          document.querySelectorAll('input:not([type=radio]):not([type=hidden]):not([type=submit]), textarea, [role=combobox]').forEach(el => {
            const label = clean(el.getAttribute('aria-label')) || clean(el.labels && el.labels[0] && el.labels[0].innerText) || clean(el.closest('label') && el.closest('label').innerText);
            const value = clean(el.value || el.getAttribute('value') || el.textContent);
            push(label, el.getAttribute('role') === 'combobox' ? 'combobox' : (el.tagName.toLowerCase()==='textarea'?'textarea':'input'), [], value);
          });
          return result;
        }""")
    except Exception:
        return []


async def _set_field(page, field: str, value: str) -> bool:
    aliases = FIELD_LABEL_ALIASES.get(field, [field.split("custom:", 1)[1] if field.startswith("custom:") else field])
    for label in aliases:
        # First try a normal labelled input/combobox.
        try:
            loc = page.get_by_label(label, exact=False)
            if await loc.count():
                el = loc.first
                tag = await el.evaluate("e => e.tagName.toLowerCase()")
                if tag in ("input", "textarea"):
                    await el.fill(value)
                    await page.wait_for_timeout(250)
                    opt = page.get_by_text(value, exact=True)
                    if await opt.count():
                        await opt.first.click()
                    return True
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=2035)

        # Some Avito characteristic inputs are wrapped by a visual <label> but
        # are not exposed through the accessibility tree, so get_by_label()
        # misses the very same control that _discover_characteristics() sees.
        # Resolve only inside the exact label container; never search arbitrary
        # page inputs, because a mass edit must not touch unrelated fields.
        try:
            label_nodes = page.locator("label").filter(has_text=label)
            for _idx in range(min(await label_nodes.count(), 5)):
                box = label_nodes.nth(_idx)
                controls = box.locator('input:not([type="hidden"]), textarea, [role="combobox"]')
                if await controls.count():
                    el = controls.first
                    await el.fill(value)
                    await page.wait_for_timeout(250)
                    opt = page.get_by_text(value, exact=True)
                    if await opt.count():
                        await opt.first.click()
                    return True
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=2056)

        # Avito characteristics often use text heading + radio options.
        try:
            heading = page.get_by_text(label, exact=True)
            if await heading.count():
                option = page.get_by_text(value, exact=True)
                if await option.count():
                    await option.first.click()
                    return True
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=2067)
    return False


async def _verify_field(page, field: str, value: str) -> bool:
    """Verify one concrete Avito editor field without global body-text guesses.

    A value appearing in the title/description/company name is not proof that the
    characteristic itself was saved. Prefer the labelled control value and only
    fall back to the nearest field container around the exact field label.
    """
    expected = " ".join(str(value or "").lower().replace("ё", "е").split())
    if not expected:
        return False
    aliases = FIELD_LABEL_ALIASES.get(field, [field.split("custom:", 1)[1] if field.startswith("custom:") else field])
    for label in aliases:
        try:
            controls = page.get_by_label(label, exact=False)
            count = min(await controls.count(), 5)
            for idx in range(count):
                el = controls.nth(idx)
                state = await el.evaluate("""e => ({
                    tag:(e.tagName||'').toLowerCase(),
                    type:(e.getAttribute('type')||'').toLowerCase(),
                    value:('value' in e ? String(e.value||'') : ''),
                    checked:Boolean(e.checked),
                    text:(e.innerText||e.textContent||''),
                    aria:e.getAttribute('aria-valuetext')||e.getAttribute('aria-label')||'',
                    selected:e.tagName==='SELECT' && e.selectedIndex>=0 ? (e.options[e.selectedIndex]?.text||'') : ''
                })""")
                candidates = [state.get("value"), state.get("selected"), state.get("aria"), state.get("text")]
                if state.get("type") in {"radio", "checkbox"} and not state.get("checked"):
                    continue
                for candidate in candidates:
                    normalized = " ".join(str(candidate or "").lower().replace("ё", "е").split())
                    if normalized == expected or (expected and expected in normalized and len(normalized) <= max(120, len(expected) * 4)):
                        return True
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=2105)
        try:
            label_nodes = page.locator("label").filter(has_text=label)
            for _idx in range(min(await label_nodes.count(), 5)):
                controls = label_nodes.nth(_idx).locator('input:not([type="hidden"]), textarea, [role="combobox"]')
                for _j in range(min(await controls.count(), 3)):
                    el = controls.nth(_j)
                    current = await el.evaluate("e => String(('value' in e ? e.value : '') || e.getAttribute('aria-valuetext') || e.textContent || '')")
                    normalized = " ".join(str(current or "").lower().replace("ё", "е").split())
                    if normalized == expected:
                        return True
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=2117)
        try:
            labels = page.get_by_text(label, exact=True)
            for idx in range(min(await labels.count(), 3)):
                heading = labels.nth(idx)
                # Use the nearest semantic/form container, not the whole page.
                box = heading.locator("xpath=ancestor::*[self::fieldset or @role='group' or @data-marker][1]")
                if await box.count() == 0:
                    box = heading.locator("xpath=parent::*")
                text_value = await box.first.inner_text(timeout=3000)
                normalized = " ".join(str(text_value or "").lower().replace("ё", "е").split())
                if expected in normalized and len(normalized) <= 1200:
                    return True
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=2131)
    return False


@router.post("/gateway/pair-code")
def gateway_pair_code(account_id: str | None = None):
    """Creates a one-time code. With account_id, the Chrome profile is permanently scoped to that BORIS/Avito account."""
    from app.api.browser_gateway import create_pair_code
    rec = create_pair_code(account_id=account_id)
    return {
        "status": "ok",
        "code": rec["code"],
        "expires_at": rec["expires_at"],
        "extension_url": "/boris-browser-gateway.zip",
    }


@router.get("/gateway/status")
def gateway_status(account_id: str | None = None):
    """Owner-facing status. If account_id is supplied, return that account's dedicated browser session."""
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "select value from storage where account_id='__browser_gateway__' and key like 'gateway:gw_%' order by id desc limit 200"
        )).fetchall()
        gw = None
        for row in rows:
            try:
                candidate = json.loads(row[0])
            except Exception:
                continue
            if account_id and str(candidate.get("account_id") or "") != str(account_id):
                continue
            if candidate.get("state") == "superseded" or candidate.get("superseded_at"):
                continue
            gw = candidate
            break
        if not gw:
            return {"status": "ok", "connected": False, "gateway": None, "requires_account_session": bool(account_id)}
        last_seen = gw.get("last_seen_at")
        connected = False
        if last_seen:
            try:
                connected = (datetime.now(timezone.utc) - datetime.fromisoformat(last_seen)).total_seconds() <= 45
            except Exception:
                connected = False
        safe = {k: gw.get(k) for k in ["gateway_id", "device_name", "extension_version", "last_seen_at", "state", "current_job_id", "current_capability", "avito_logged_in", "services", "account_id", "session_scope"]}
        return {"status": "ok", "connected": connected, "gateway": safe}
    finally:
        db.close()


@router.post("/gateway/identity-check")
def gateway_identity_check(account_id: str):
    """Queue a READ-only browser preflight that proves the Chrome Avito profile matches this BORIS account."""
    import secrets as _secrets
    from app.api.browser_gateway import _account_gateway_state, _db_put as _gw_put, _iso as _gw_iso
    session = _account_gateway_state(account_id)
    if not session.get("online"):
        return {"status":"error","code":"browser_session_required","message":"Browser Gateway этого аккаунта не подключён."}
    readiness = execution_readiness(account_id)
    expected_name = str(((readiness.get("api") or {}).get("name") or "")).strip()
    expected_id = str(((readiness.get("api") or {}).get("avito_user_id") or "")).strip()
    if not expected_name and not expected_id:
        return {"status":"error","code":"avito_api_identity_unavailable","message":"Не удалось получить эталонный профиль из Avito API."}
    job_id = "browser_" + _secrets.token_hex(7)
    job = {
        "job_id":job_id,"kind":"browser","status":"waiting_gateway","created_at":_gw_iso(),"updated_at":_gw_iso(),
        "account_id":account_id,"domain":"avito.ru","capability":"read_page","risk_level":"READ","mode":"read",
        "steps":[
            {"action":"open_url","url":"https://www.avito.ru/profile/pro/items","selector":None,"text":None,"value":None,"timeout_ms":35000,"redact_selectors":[]},
            {"action":"read_page","url":None,"selector":None,"text":None,"value":None,"timeout_ms":15000,"redact_selectors":[]},
        ],
        "gateway_id":None,"confirmed":False,
        "metadata":{"purpose":"avito_identity_preflight","expected_name":expected_name,"expected_avito_user_id":expected_id,"account_isolation":True,"auto_rebind":False},
        "results":[],"error":None,"privacy":{"guard":True,"never_read_passwords":True},
    }
    _gw_put(account_id, "browser_job:"+job_id, job)
    return {"status":"queued","job_id":job_id,"expected_name":expected_name}


@router.get("/gateway/identity-check/status")
def gateway_identity_check_status(account_id: str, job_id: str):
    from app.api.browser_gateway import _db_get as _gw_get
    job = _gw_get(account_id, "browser_job:"+job_id)
    if not job:
        return {"status":"error","message":"Проверка профиля не найдена"}
    status = str(job.get("status") or "")
    if status not in {"completed","completed_with_errors"}:
        return {"status":status,"job_id":job_id}
    if status == "completed_with_errors":
        return {"status":"error","job_id":job_id,"message":job.get("error") or "Не удалось прочитать профиль Avito"}
    page = {}
    for row in job.get("results") or []:
        result = row.get("result") or {}
        if isinstance(result, dict) and result.get("text"):
            page = result
    text_value = str(page.get("text") or "")
    expected_name = str((job.get("metadata") or {}).get("expected_name") or "").strip()
    norm = lambda v: " ".join(str(v or "").lower().replace("ё","е").split())
    expected_norm = norm(expected_name)
    text_norm = norm(text_value)
    tokens = [x for x in expected_norm.split() if len(x) >= 3]
    # Require the official Avito API profile name to be represented on the account page.
    # Matching all substantial tokens is intentionally conservative: a false block is safer than editing another client's account.
    verified = bool(tokens and all(t in text_norm for t in tokens))
    # Reuse the authoritative extractor so manual preflight and the automatic
    # watchdog interpret Avito's profile header identically (including cases
    # where rating/review text is glued to the profile name).
    from app.api.browser_gateway import _extract_avito_profile_name
    actual_name = _extract_avito_profile_name(text_value)
    guard = {
        "verified": verified, "checked_at": _now(), "expected_name": expected_name,
        "expected_avito_user_id": str((job.get("metadata") or {}).get("expected_avito_user_id") or ""),
        "actual_name": actual_name,
        "page_url": str(page.get("url") or "")[:500],
        "reason": None if verified else "AVITO_PROFILE_MISMATCH",
    }
    _db_put(account_id, _identity_guard_key(account_id), guard)
    # Strict account isolation: validate the active browser identity against the
    # account chosen at pairing. The compatibility helper NEVER rebinds the
    # gateway to another account; mismatch becomes a sticky safety block.
    if actual_name:
        try:
            from app.api.browser_gateway import _rebind_gateway_by_profile_name
            isolation = _rebind_gateway_by_profile_name(account_id, actual_name, job.get("gateway_id"))
            guard["session_isolation"] = isolation
            if isolation.get("status") == "verified":
                verified = True
                guard["verified"] = True
                guard["reason"] = None
            elif isolation.get("status") == "mismatch":
                verified = False
                guard["verified"] = False
                guard["reason"] = "AVITO_PROFILE_MISMATCH"
        except Exception as _route_exc:
            guard["session_isolation"] = {"status":"error","message":str(_route_exc)[:180]}
    return {"status":"ok" if verified else "mismatch","job_id":job_id,**guard}


@router.get("/execution-readiness")
def execution_readiness(account_id: str):
    """Single authoritative readiness view for API-first + account-scoped browser fallback."""
    api = {"connected": False, "identity_verified": False, "name": None, "avito_user_id": None, "error": None}
    try:
        from app.crypto_utils import decrypt_secret
        import httpx as _httpx
        from app.services.avito_account_throttle import account_throttle_remaining as _readiness_throttle, record_account_throttle as _readiness_record_throttle
        # MASS_EDITOR_READINESS_SHARED_THROTTLE_V1: readiness is diagnostic only;
        # it must never bypass a tenant Retry-After just to refresh identity state.
        _readiness_retry = _readiness_throttle(account_id)
        if _readiness_retry > 0:
            api["error"] = "avito_account_throttled"
            api["retry_after_seconds"] = int(_readiness_retry)
            raise RuntimeError("avito_account_throttled")
        db = SessionLocal()
        try:
            acc = db.query(Account).filter(Account.account_id == account_id).first()
            if not acc or not acc.avito_client_id or not acc.avito_client_secret:
                raise RuntimeError("Avito API-ключи не подключены")
            client_id = decrypt_secret(acc.avito_client_id)
            client_secret = decrypt_secret(acc.avito_client_secret)
        finally:
            db.close()
        def _readiness_record_429(resp, source: str) -> int:
            retry = 30
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
                retry = 30
            return _readiness_record_throttle(account_id, retry or 30, source=source)
        tr = _httpx.post("https://api.avito.ru/token/", data={"grant_type":"client_credentials","client_id":client_id,"client_secret":client_secret}, timeout=12)
        if tr.status_code == 429:
            _readiness_retry = _readiness_record_429(tr, "mass_editor_readiness_token_429")
            api["retry_after_seconds"] = int(_readiness_retry)
            raise RuntimeError("avito_account_throttled")
        token = (tr.json() or {}).get("access_token") if tr.status_code == 200 else None
        if not token:
            raise RuntimeError(f"Avito token HTTP {tr.status_code}")
        _readiness_retry = _readiness_throttle(account_id)
        if _readiness_retry > 0:
            api["retry_after_seconds"] = int(_readiness_retry)
            raise RuntimeError("avito_account_throttled")
        rr = _httpx.get("https://api.avito.ru/core/v1/accounts/self", headers={"Authorization": f"Bearer {token}"}, timeout=12)
        if rr.status_code == 429:
            _readiness_retry = _readiness_record_429(rr, "mass_editor_readiness_self_429")
            api["retry_after_seconds"] = int(_readiness_retry)
            raise RuntimeError("avito_account_throttled")
        if rr.status_code == 200:
            me = rr.json() or {}
            api.update({"connected": True, "identity_verified": True, "name": me.get("name"), "avito_user_id": me.get("id")})
        else:
            api["error"] = f"Avito API HTTP {rr.status_code}"
    except Exception as exc:
        api["error"] = str(exc)[:240]

    gw = gateway_status(account_id)
    browser = {
        "connected": bool(gw.get("connected")),
        "requires_login": False,
        "gateway_id": (gw.get("gateway") or {}).get("gateway_id"),
        "extension_version": (gw.get("gateway") or {}).get("extension_version"),
        "avito_logged_in": (gw.get("gateway") or {}).get("avito_logged_in"),
        "session_scope": (gw.get("gateway") or {}).get("session_scope"),
    }
    browser["requires_login"] = bool(browser["connected"] and browser["avito_logged_in"] is False)
    return {
        "status": "ok", "account_id": account_id,
        "api": api, "browser": browser,
        "router": {
            "mode": "api_first",
            "read_executor": "avito_api" if api["connected"] else ("browser_gateway" if browser["connected"] else None),
            "browser_write_ready": bool(browser["connected"] and not browser["requires_login"]),
            "fallback": "account_browser_session",
            "verify_required": True,
        },
    }


# BORIS_ISOLATED_AVITO_SESSION_V1
# Server-side persistent browser profile per Avito account. This executor is
# intentionally independent from the owner's ordinary Chrome/Gateway cookies.
_ISOLATED_SESSION_ROOT = "/root/BORIS/backend/runtime/avito_sessions"

def _isolated_session_dir(account_id: str) -> str:
    import re as _re, os as _os
    safe = _re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(account_id or ""))[:120]
    path = _os.path.join(_ISOLATED_SESSION_ROOT, safe)
    _os.makedirs(path, exist_ok=True)
    return path

async def _isolated_avito_login_probe(account_id: str) -> dict[str, Any]:
    """Open/reuse a dedicated persistent Chromium context for one Avito account.
    Never touches the user's normal Chrome profile. If Avito asks for a one-time
    challenge we report it explicitly; otherwise cookies persist for future runs.
    """
    from app.models.account import Account
    from playwright.async_api import async_playwright
    import os
    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.account_id == account_id).first()
        if not account:
            return {"status":"error","code":"account_not_found"}
        login = str(account.avito_login or "").strip()
        password = str(account.avito_password or "")
    finally:
        db.close()
    if not login or not password:
        return {"status":"error","code":"credentials_missing"}
    profile_dir = _isolated_session_dir(account_id)
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=profile_dir, headless=True,
            args=["--no-sandbox","--disable-dev-shm-usage"],
            viewport={"width":1440,"height":1000}, locale="ru-RU",
        )
        page = context.pages[0] if context.pages else await context.new_page()
        page.set_default_timeout(12000)
        try:
            await page.goto("https://www.avito.ru/profile/pro/items", wait_until="commit", timeout=25000)
            await page.wait_for_timeout(1800)
            body = (await page.locator("body").inner_text()).strip()
            low = body.lower()
            if "доступ ограничен" in low or "проблема с ip" in low:
                return {"status":"blocked","code":"avito_ip_restricted","url":page.url}
            if "/login" in page.url or "auth" in page.url or "войти" in low[:2500]:
                inputs = page.locator('input[type="text"],input[type="email"],input[type="tel"]')
                if await inputs.count() == 0:
                    return {"status":"challenge","code":"login_form_unavailable","url":page.url}
                await inputs.first.fill(login)
                for txt in ["Продолжить","Войти"]:
                    btn=page.get_by_role("button",name=txt,exact=False)
                    if await btn.count():
                        await btn.first.click(); await page.wait_for_timeout(1000); break
                pw=page.locator('input[type="password"]')
                if await pw.count() == 0:
                    body=(await page.locator("body").inner_text()).lower()
                    challenge = "sms" if ("смс" in body or "код" in body) else "captcha" if ("captcha" in body or "провер" in body) else "password_step_missing"
                    return {"status":"challenge","code":challenge,"url":page.url}
                await pw.first.fill(password)
                btn=page.get_by_role("button",name="Войти",exact=False)
                if await btn.count(): await btn.first.click()
                await page.wait_for_timeout(2200)
                body=(await page.locator("body").inner_text()).strip(); low=body.lower()
                if "код из смс" in low or "введите код" in low or "код подтверждения" in low:
                    return {"status":"challenge","code":"sms_code_required","url":page.url}
                if "captcha" in low or "подтвердите, что вы не робот" in low:
                    return {"status":"challenge","code":"captcha_required","url":page.url}
            return {"status":"ok","url":page.url,"title":await page.title(),"text":body[:4000],"profile_dir":profile_dir}
        finally:
            await context.close()

@router.post("/isolated-session/probe")
async def isolated_session_probe(account_id: str):
    return {"status":"disabled","code":"use_authoritative_isolated_queue","message":"Проверка выполняется только через единую очередь BORIS."}
