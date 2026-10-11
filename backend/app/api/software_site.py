from __future__ import annotations

import json
import os
import smtplib
import ssl
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import requests
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from app.services.exception_observability import observe_suppressed
from app.db.session import SessionLocal
from sqlalchemy import text


def _require_loopback_admin(request: Request) -> None:
    """Defense in depth: private admin API is only reachable through local Nginx."""
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1"}:
        raise HTTPException(status_code=404, detail="Not Found")


router = APIRouter(prefix="/api/public/software-site", tags=["software-site-public"])
admin_router = APIRouter(
    prefix="/api/private/software-site",
    tags=["software-site-admin"],
    dependencies=[Depends(_require_loopback_admin)],
)

_RECIPIENTS = ("eliseev-ko@mail.ru", "ostapenko-kirill-86@yandex.ru")
_LEAD_LOG = Path("/root/BORIS/backend/data/software_site_leads.ndjson")
_RECIPIENT_ARCHIVE_BASE = Path("/root/BORIS/backend/data/software_site_recipient_archive")
_ADMIN_LEADS_JSON = Path("/root/BORIS/backend/data/software_site_admin_leads.json")
_CONFIG_PATH = Path("/root/BORIS/backend/data/software_site_config.json")
_RATE_LOCK = threading.Lock()
_DATA_LOCK = threading.Lock()
_RATE: dict[str, list[float]] = {}
_ALLOWED_STATUSES = {
    "Новая", "Связались", "Квалифицирована", "Оценка",
    "КП", "Переговоры", "Оплачено", "Проиграно",
}

_DEFAULT_CONFIG: dict[str, Any] = {
    "contacts": {
        "primary_email": "eliseev-ko@mail.ru",
        "secondary_email": "ostapenko-kirill-86@yandex.ru",
        "phone": "",
        "telegram": "",
    },
    "services": [],
    "prices": [],
}


def _atomic_json_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_json(path: Path, fallback: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=71)
    return fallback


def _load_config() -> dict[str, Any]:
    raw = _load_json(_CONFIG_PATH, {})
    cfg = {
        "contacts": dict(_DEFAULT_CONFIG["contacts"]),
        "services": [],
        "prices": [],
    }
    if isinstance(raw, dict):
        if isinstance(raw.get("contacts"), dict):
            cfg["contacts"].update({
                k: str(v or "").strip()
                for k, v in raw["contacts"].items()
                if k in cfg["contacts"]
            })
        if isinstance(raw.get("services"), list):
            cfg["services"] = raw["services"]
        if isinstance(raw.get("prices"), list):
            cfg["prices"] = raw["prices"]
    return cfg


def _lead_snapshot() -> list[dict[str, Any]]:
    """Merge append-only lead intake with owner-managed statuses."""
    statuses: dict[str, dict[str, Any]] = {}
    admin_rows = _load_json(_ADMIN_LEADS_JSON, [])
    if isinstance(admin_rows, list):
        for row in admin_rows:
            if isinstance(row, dict) and row.get("id"):
                statuses[str(row["id"])] = row

    rows: list[dict[str, Any]] = []
    if _LEAD_LOG.exists():
        try:
            for line in _LEAD_LOG.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if not isinstance(row, dict) or not row.get("id"):
                    continue
                saved = statuses.get(str(row["id"]), {})
                rows.append({
                    **row,
                    "status": saved.get("status") or row.get("status") or "Новая",
                    "note": saved.get("note") or "",
                    "updated_at": saved.get("updated_at") or row.get("created_at"),
                })
        except Exception:
            rows = []

    # Backward compatibility: retain admin-only rows not present in NDJSON.
    known = {str(x.get("id")) for x in rows}
    for lead_id, saved in statuses.items():
        if lead_id not in known:
            rows.append(saved)

    rows.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return rows[:500]


def _write_admin_snapshot(record: dict[str, Any]) -> None:
    with _DATA_LOCK:
        rows = _lead_snapshot()
        lead_id = str(record.get("id") or "")
        current = next((x for x in rows if str(x.get("id")) == lead_id), None)
        merged = {**(current or {}), **record}
        if not merged.get("status"):
            merged["status"] = "Новая"
        merged["updated_at"] = datetime.now(timezone.utc).isoformat()
        rows = [merged] + [x for x in rows if str(x.get("id")) != lead_id]
        _atomic_json_write(_ADMIN_LEADS_JSON, rows[:500])


def _software_metrika_snapshot(days: int = 7) -> dict[str, Any]:
    """Yandex Metrika totals for the /software-dev section plus local lead count."""
    days = max(1, min(int(days or 7), 90))
    end_day = datetime.now(timezone.utc).date()
    start_day = end_day - timedelta(days=days - 1)
    counter_id = str(os.getenv("YANDEX_METRIKA_COUNTER_ID") or "111297252").strip()
    token = str(os.getenv("YANDEX_METRIKA_TOKEN") or "").strip()

    leads_count = 0
    for row in _lead_snapshot():
        try:
            created = datetime.fromisoformat(str(row.get("created_at") or "").replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if start_day <= created.astimezone(timezone.utc).date() <= end_day:
                leads_count += 1
        except Exception:
            continue

    result: dict[str, Any] = {
        "status": "token_required" if not token else "loading",
        "counter_id": counter_id,
        "scope": "/software-dev",
        "date1": start_day.isoformat(),
        "date2": end_day.isoformat(),
        "days": days,
        "leads": leads_count,
        "goal_name": "software_dev_lead",
        "webvisor": True,
        "clickmap": True,
        "track_links": True,
    }
    if not token:
        return result

    headers = {"Authorization": "OAuth " + token}
    common = {
        "ids": counter_id,
        "date1": start_day.isoformat(),
        "date2": end_day.isoformat(),
        "accuracy": "full",
        "filters": "ym:pv:URL=@'boris-ai.pro/software-dev'",
    }
    try:
        response = requests.get(
            "https://api-metrika.yandex.net/stat/v1/data",
            params={**common, "metrics": "ym:s:visits,ym:s:users,ym:s:pageviews,ym:s:bounceRate,ym:s:avgVisitDurationSeconds"},
            headers=headers,
            timeout=15,
        )
        if response.status_code != 200:
            result.update(status="api_error", http_status=int(response.status_code))
            return result
        totals = list((response.json() or {}).get("totals") or [])
        while len(totals) < 5:
            totals.append(0)
        visits = int(round(float(totals[0] or 0)))
        result.update(
            status="ok",
            visits=visits,
            users=int(round(float(totals[1] or 0))),
            pageviews=int(round(float(totals[2] or 0))),
            bounce_rate=round(float(totals[3] or 0), 1),
            avg_seconds=int(round(float(totals[4] or 0))),
            conversion=round((leads_count / visits * 100), 2) if visits else 0.0,
        )
        try:
            source_response = requests.get(
                "https://api-metrika.yandex.net/stat/v1/data",
                params={**common, "metrics": "ym:s:visits", "dimensions": "ym:s:lastTrafficSource", "sort": "-ym:s:visits", "limit": 7},
                headers=headers,
                timeout=15,
            )
            sources: list[dict[str, Any]] = []
            if source_response.status_code == 200:
                for row in (source_response.json() or {}).get("data") or []:
                    dims = row.get("dimensions") or []
                    metrics = row.get("metrics") or []
                    sources.append({
                        "name": str((dims[0] or {}).get("name") or "—")[:100] if dims else "—",
                        "visits": int(round(float(metrics[0] or 0))) if metrics else 0,
                    })
            result["sources"] = sources
        except Exception:
            result["sources"] = []
        return result
    except Exception as exc:
        result.update(status="unavailable", error_type=type(exc).__name__)
        return result


class SoftwareLead(BaseModel):
    name: str = Field(default="Гость", max_length=120)
    contact: str = Field(min_length=3, max_length=240)
    task: str = Field(default="", max_length=5000)
    service: str = Field(default="Заявка с сайта", max_length=240)
    budget: str = Field(default="Не указан", max_length=120)
    source: str = Field(default="software-dev", max_length=120)
    page: str = Field(default="", max_length=500)
    session_id: Optional[str] = Field(default=None, max_length=120)
    utm_source: Optional[str] = Field(default=None, max_length=180)
    utm_medium: Optional[str] = Field(default=None, max_length=180)
    utm_campaign: Optional[str] = Field(default=None, max_length=180)
    utm_content: Optional[str] = Field(default=None, max_length=180)
    utm_term: Optional[str] = Field(default=None, max_length=180)
    privacy_consent: bool = False
    consent_version: str = Field(default="2026-09-09", max_length=32)
    honeypot: Optional[str] = Field(default="", max_length=120)


class SoftwareEvent(BaseModel):
    event: str = Field(min_length=2, max_length=80)
    channel: Optional[str] = Field(default=None, max_length=40)
    page: str = Field(default="", max_length=500)
    session_id: Optional[str] = Field(default=None, max_length=120)
    utm_source: Optional[str] = Field(default=None, max_length=180)
    utm_medium: Optional[str] = Field(default=None, max_length=180)
    utm_campaign: Optional[str] = Field(default=None, max_length=180)
    utm_content: Optional[str] = Field(default=None, max_length=180)
    utm_term: Optional[str] = Field(default=None, max_length=180)


class LeadUpdate(BaseModel):
    status: str = Field(max_length=80)
    note: str = Field(default="", max_length=4000)


class SiteConfigUpdate(BaseModel):
    contacts: dict[str, str] | None = None
    services: list[dict[str, Any]] | None = None
    prices: list[dict[str, Any]] | None = None


def _rate_limit(ip: str) -> None:
    now = time.time()
    with _RATE_LOCK:
        recent = [t for t in _RATE.get(ip, []) if now - t < 600]
        if len(recent) >= 8:
            raise HTTPException(status_code=429, detail="Слишком много заявок. Попробуйте немного позже.")
        recent.append(now)
        _RATE[ip] = recent


def _traffic_from_page(page: str) -> dict[str, str]:
    try:
        query = parse_qs(urlparse(str(page or "")).query)
    except Exception:
        query = {}

    def first(name: str) -> str:
        values = query.get(name) or []
        return str(values[0] if values else "").strip()[:240]

    return {
        "utm_source": first("utm_source"),
        "utm_medium": first("utm_medium"),
        "utm_campaign": first("utm_campaign"),
        "utm_content": first("utm_content"),
        "utm_term": first("utm_term"),
    }



_SOFTWARE_EVENT_ALLOWED = {
    "development_landing_view",
    "development_contact_click",
    "development_form_started",
    "development_form_submit",
    "development_lead_created",
}


def _traffic_from_payload(payload: Any) -> dict[str, str]:
    page_traffic = _traffic_from_page(str(getattr(payload, "page", "") or ""))
    out = {}
    for key in ("utm_source","utm_medium","utm_campaign","utm_content","utm_term"):
        explicit = str(getattr(payload, key, None) or "").strip()[:240]
        out[key] = explicit or page_traffic.get(key, "")
    return out


def _record_software_event(payload: SoftwareEvent | SoftwareLead, *, event: str | None = None,
                           channel: str | None = None, lead_id: str | None = None,
                           crm_lead_id: int | None = None) -> str:
    event_name = str(event or getattr(payload, "event", "") or "").strip()
    if event_name not in _SOFTWARE_EVENT_ALLOWED:
        raise ValueError("software_event_not_allowed")
    traffic = _traffic_from_payload(payload)
    event_id = "sse_" + uuid.uuid4().hex
    body = {
        "event_id": event_id,
        "event": event_name,
        "channel": str(channel or getattr(payload, "channel", None) or "").strip()[:40] or None,
        "page": str(getattr(payload, "page", "") or "")[:500],
        "session_id": str(getattr(payload, "session_id", None) or "").strip()[:120] or None,
        **traffic,
        "lead_id": lead_id,
        "crm_lead_id": int(crm_lead_id) if crm_lead_id else None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    db = SessionLocal()
    try:
        db.execute(text("""
          INSERT INTO storage(account_id,key,value)
          VALUES('software_site_event',:k,:v)
        """), {
            "k": "event:" + event_id,
            "v": json.dumps(body, ensure_ascii=False, separators=(",", ":")),
        })
        db.commit()
    finally:
        db.close()
    return event_id


def _create_software_crm_lead(lead: SoftwareLead, lead_id: str) -> int | None:
    traffic = _traffic_from_payload(lead)
    db = SessionLocal()
    try:
        owner_email = db.execute(text("""
          SELECT email FROM users
          WHERE role='owner'
          ORDER BY CASE WHEN id=2 THEN 0 ELSE 1 END,id
          LIMIT 1
        """)).scalar()
        owner_email = str(owner_email or "").strip()
        if not owner_email:
            return None
        source = "software_site_email" if traffic.get("utm_source") == "email" else "software_site"
        comment = (
            f"Заявка с сайта разработки. ID: {lead_id}\n"
            f"Контакт: {lead.contact}\n"
            f"Бюджет: {lead.budget}\n"
            f"Задача: {lead.task or 'Не указана'}\n"
            f"UTM: {traffic.get('utm_source') or '—'} / {traffic.get('utm_medium') or '—'} / "
            f"{traffic.get('utm_campaign') or '—'} / {traffic.get('utm_content') or '—'}"
        )
        crm_id = db.execute(text("""
          INSERT INTO manager_leads(
            manager_email,name,site,niche,comment,status,lead_source,created_at,updated_at
          )
          VALUES(:me,:name,:site,:niche,:comment,'новый',:source,NOW(),NOW())
          RETURNING id
        """), {
            "me": owner_email,
            "name": lead.name or "Гость",
            "site": (lead.page or "")[:500] or None,
            "niche": (lead.service or "Разработка ПО")[:240],
            "comment": comment,
            "source": source,
        }).scalar()
        if crm_id:
            db.execute(text("""
              INSERT INTO manager_notes(manager_email,lead_id,kind,text,remind_at)
              VALUES(:me,:lead,'reminder','Новая заявка с сайта разработки — связаться сегодня',NOW())
            """), {"me": owner_email, "lead": int(crm_id)})
        db.commit()
        return int(crm_id) if crm_id else None
    finally:
        db.close()


def _send_email(lead: SoftwareLead, recipient: str, lead_id: str) -> None:
    host = os.getenv("SMTP_HOST", "").strip()
    port = int(os.getenv("SMTP_PORT", "465") or 465)
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASS", "")
    sender = os.getenv("EMAIL_FROM_ADDRESS", user).strip() or user
    if not all((host, user, password, sender)):
        raise RuntimeError("SMTP is not configured")

    subject = f"[ЗАЯВКА С САЙТА] {lead.service} — {lead.name}"
    traffic = _traffic_from_page(lead.page)
    body = (
        "Новая заявка с сайта разработки\n\n"
        f"ID: {lead_id}\n"
        f"Имя: {lead.name}\n"
        f"Контакт: {lead.contact}\n"
        f"Услуга: {lead.service}\n"
        f"Бюджет: {lead.budget}\n"
        f"Задача: {lead.task or 'Не указана'}\n"
        f"Страница: {lead.page or 'Не указана'}\n"
        f"Источник формы: {lead.source}\n"
        f"UTM источник: {traffic.get('utm_source') or '—'}\n"
        f"UTM канал: {traffic.get('utm_medium') or '—'}\n"
        f"UTM кампания: {traffic.get('utm_campaign') or '—'}\n"
        f"UTM оффер: {traffic.get('utm_content') or '—'}\n"
        f"UTM ключ: {traffic.get('utm_term') or '—'}\n"
        f"Дата UTC: {datetime.now(timezone.utc).isoformat()}\n"
    )
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Reply-To"] = sender
    msg["Subject"] = subject
    msg.set_content(body)

    from app.services.email_service import _smtp_transport_send
    _smtp_transport_send(
        msg,
        tenant_scope=f"software_site:{lead_id}",
        host=host,
        port=port,
        username=user,
        password=password,
        use_ssl=True,
        starttls=False,
        timeout=20,
    )


@router.get("/config")
def public_config():
    return _load_config()


@router.get("/health")
def public_health():
    cfg = _load_config()
    smtp_configured = all(
        bool((os.getenv(key, "") or "").strip())
        for key in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS")
    )
    storage_ok = True
    try:
        _LEAD_LOG.parent.mkdir(parents=True, exist_ok=True)
        probe = _LEAD_LOG.parent / ".software_site_health_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except Exception:
        storage_ok = False

    last_lead_at = None
    rows = _lead_snapshot()
    if rows:
        last_lead_at = rows[0].get("created_at")

    return {
        "ok": bool(storage_ok and smtp_configured),
        "storage_ok": storage_ok,
        "smtp_configured": smtp_configured,
        "recipient_count": len(_RECIPIENTS),
        "services_count": len(cfg.get("services") or []),
        "prices_count": len(cfg.get("prices") or []),
        "last_lead_at": last_lead_at,
    }


@router.post("/event")
def software_event(payload: SoftwareEvent):
    if payload.event not in _SOFTWARE_EVENT_ALLOWED:
        raise HTTPException(status_code=422, detail="Неизвестное событие")
    try:
        event_id=_record_software_event(payload)
        return {"ok":True,"event_id":event_id}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"event_store_failed:{type(exc).__name__}")


@router.post("/lead")
def create_software_lead(lead: SoftwareLead, request: Request):
    if (lead.honeypot or "").strip():
        return {"ok": True}
    ip = request.client.host if request.client else "unknown"
    _rate_limit(ip)
    if not lead.contact.strip():
        raise HTTPException(status_code=422, detail="Укажите контакт")
    if not lead.privacy_consent:
        raise HTTPException(status_code=422, detail="Необходимо согласие на обработку персональных данных")

    now = datetime.now(timezone.utc)
    lead_id = f"web-{int(now.timestamp() * 1000)}"
    record = {
        "id": lead_id,
        "created_at": now.isoformat(),
        "ip": ip,
        **lead.model_dump(exclude={"honeypot"}),
        "utm": _traffic_from_payload(lead),
        "consent_at": now.isoformat(),
        "status": "Новая",
        "note": "",
    }
    _LEAD_LOG.parent.mkdir(parents=True, exist_ok=True)
    with _LEAD_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    _write_admin_snapshot(record)

    crm_lead_id = None
    try:
        crm_lead_id = _create_software_crm_lead(lead, lead_id)
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=548)
    try:
        _record_software_event(
            lead,
            event="development_lead_created",
            channel="form",
            lead_id=lead_id,
            crm_lead_id=crm_lead_id,
        )
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=559)

    # Near-real-time owner notification. External MAX I/O is never done in
    # the form request itself: enqueue only, background worker delivers it.
    try:
        from app.services.lead_notifications import queue_event, OWNER_MAX_TRANSPORT_ACCOUNT_ID
        traffic = record.get("utm") or {}
        max_body = (
            f"Имя: {lead.name}\n"
            f"Контакт: {lead.contact}\n"
            f"Услуга: {lead.service}\n"
            f"Бюджет: {lead.budget}\n"
            f"Задача: {(lead.task or 'Не указана')[:1200]}\n"
            f"UTM: {traffic.get('utm_source') or '—'} / {traffic.get('utm_medium') or '—'} / {traffic.get('utm_campaign') or '—'}\n"
            f"ID: {lead_id}"
        )
        queue_event(
            OWNER_MAX_TRANSPORT_ACCOUNT_ID,
            f"software-site:new-lead:{lead_id}",
            "software_site_new_lead",
            f"🆕 Новая заявка с сайта · {lead.service[:100]}",
            max_body,
            severity="critical",
            url="/software-dev/",
            payload={
                "max_channel": "owner",
                "delivery_scope": "max_only",
                "lead_id": lead_id,
                "source": lead.source,
            },
        )
    except Exception as _suppressed_exc:
        # Lead intake and email delivery must not fail because MAX is unavailable.
        observe_suppressed(__name__, _suppressed_exc, line=430)

    errors = []
    delivered = []
    for recipient in _RECIPIENTS:
        try:
            _send_email(lead, recipient, lead_id)
            delivered.append(recipient)
            bucket = "mailru" if recipient.lower().endswith("@mail.ru") else "yandex"
            target_dir = _RECIPIENT_ARCHIVE_BASE / bucket
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / f"{lead_id}.json").write_text(
                json.dumps(
                    {**record, "recipient": recipient, "smtp_accepted": True},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as exc:
            errors.append({"recipient": recipient, "error": type(exc).__name__})

    if errors:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Заявка сохранена, но почтовая доставка выполнена не полностью",
                "lead_id": lead_id,
                "crm_lead_id": crm_lead_id,
                "delivered": delivered,
                "errors": errors,
            },
        )

    return {"ok": True, "lead_id": lead_id, "crm_lead_id": crm_lead_id, "delivered_to": len(delivered)}


@admin_router.get("/leads")
def admin_leads():
    return {"ok": True, "items": _lead_snapshot()}


@admin_router.get("/analytics")
def admin_analytics(days: int = 7):
    return {"ok": True, "analytics": _software_metrika_snapshot(days)}


@admin_router.patch("/leads/{lead_id}")
def admin_update_lead(lead_id: str, body: LeadUpdate):
    status = body.status.strip()
    if status not in _ALLOWED_STATUSES:
        raise HTTPException(status_code=422, detail="Неизвестный статус")
    rows = _lead_snapshot()
    row = next((x for x in rows if str(x.get("id")) == lead_id), None)
    if not row:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    updated = {
        **row,
        "status": status,
        "note": body.note.strip(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_admin_snapshot(updated)

    # Keep the owner CRM card in sync with the public-site lead pipeline.
    # The canonical relationship was persisted when development_lead_created
    # was recorded, so no fuzzy matching by email/phone is required here.
    crm_status_map = {
        "Новая": "новый",
        "Связались": "в диалоге",
        "Квалифицирована": "квалифицирован",
        "Оценка": "квалифицирован",
        "КП": "целевое действие",
        "Переговоры": "целевое действие",
        "Оплачено": "сделка",
        "Проиграно": "отказ",
    }
    db = SessionLocal()
    try:
        crm_raw = db.execute(text("""
          SELECT value::jsonb->>'crm_lead_id'
          FROM storage
          WHERE account_id='software_site_event'
            AND value::jsonb->>'event'='development_lead_created'
            AND value::jsonb->>'lead_id'=:lead_id
          ORDER BY id DESC
          LIMIT 1
        """), {"lead_id": str(lead_id)}).scalar()
        crm_id = int(crm_raw) if str(crm_raw or "").isdigit() else None
        if crm_id:
            crm_status = crm_status_map[status]
            db.execute(text("""
              UPDATE manager_leads
              SET status=:status, updated_at=NOW()
              WHERE id=:id AND lead_source IN ('software_site','software_site_email')
            """), {"id": crm_id, "status": crm_status})
            note = body.note.strip()
            if note:
                db.execute(text("""
                  INSERT INTO manager_notes(manager_email,lead_id,kind,text)
                  SELECT manager_email,id,'note',:note
                  FROM manager_leads ml
                  WHERE ml.id=:id
                    AND NOT EXISTS (
                      SELECT 1 FROM manager_notes n
                      WHERE n.lead_id=ml.id AND n.kind='note' AND n.text=:note
                    )
                """), {"id": crm_id, "note": note[:4000]})
            db.commit()
    finally:
        db.close()

    return {"ok": True, "item": updated}


@admin_router.get("/config")
def admin_get_config():
    return {"ok": True, "config": _load_config()}


@admin_router.put("/config")
def admin_put_config(body: SiteConfigUpdate):
    current = _load_config()
    if body.contacts is not None:
        allowed = set(_DEFAULT_CONFIG["contacts"])
        contacts = dict(current["contacts"])
        for key, value in body.contacts.items():
            if key in allowed:
                contacts[key] = str(value or "").strip()[:240]
        current["contacts"] = contacts
    if body.services is not None:
        current["services"] = body.services[:200]
    if body.prices is not None:
        current["prices"] = body.prices[:300]
    with _DATA_LOCK:
        _atomic_json_write(_CONFIG_PATH, current)
    return {"ok": True, "config": current}
