from __future__ import annotations

import json
import os
import smtplib
import ssl
import threading
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field


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
    except Exception:
        pass
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


class SoftwareLead(BaseModel):
    name: str = Field(default="Гость", max_length=120)
    contact: str = Field(min_length=3, max_length=240)
    task: str = Field(default="", max_length=5000)
    service: str = Field(default="Заявка с сайта", max_length=240)
    budget: str = Field(default="Не указан", max_length=120)
    source: str = Field(default="software-dev", max_length=120)
    page: str = Field(default="", max_length=500)
    honeypot: Optional[str] = Field(default="", max_length=120)


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


def _send_email(lead: SoftwareLead, recipient: str, lead_id: str) -> None:
    host = os.getenv("SMTP_HOST", "").strip()
    port = int(os.getenv("SMTP_PORT", "465") or 465)
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASS", "")
    sender = os.getenv("EMAIL_FROM_ADDRESS", user).strip() or user
    if not all((host, user, password, sender)):
        raise RuntimeError("SMTP is not configured")

    subject = f"[ЗАЯВКА С САЙТА] {lead.service} — {lead.name}"
    body = (
        "Новая заявка с сайта разработки\n\n"
        f"ID: {lead_id}\n"
        f"Имя: {lead.name}\n"
        f"Контакт: {lead.contact}\n"
        f"Услуга: {lead.service}\n"
        f"Бюджет: {lead.budget}\n"
        f"Задача: {lead.task or 'Не указана'}\n"
        f"Страница: {lead.page or 'Не указана'}\n"
        f"Источник: {lead.source}\n"
        f"Дата UTC: {datetime.now(timezone.utc).isoformat()}\n"
    )
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Reply-To"] = sender
    msg["Subject"] = subject
    msg.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(host, port, context=context, timeout=20) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)


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


@router.post("/lead")
def create_software_lead(lead: SoftwareLead, request: Request):
    if (lead.honeypot or "").strip():
        return {"ok": True}
    ip = request.client.host if request.client else "unknown"
    _rate_limit(ip)
    if not lead.contact.strip():
        raise HTTPException(status_code=422, detail="Укажите контакт")

    now = datetime.now(timezone.utc)
    lead_id = f"web-{int(now.timestamp() * 1000)}"
    record = {
        "id": lead_id,
        "created_at": now.isoformat(),
        "ip": ip,
        **lead.model_dump(exclude={"honeypot"}),
        "status": "Новая",
        "note": "",
    }
    _LEAD_LOG.parent.mkdir(parents=True, exist_ok=True)
    with _LEAD_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    _write_admin_snapshot(record)

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
                "delivered": delivered,
                "errors": errors,
            },
        )

    return {"ok": True, "lead_id": lead_id, "delivered_to": len(delivered)}


@admin_router.get("/leads")
def admin_leads():
    return {"ok": True, "items": _lead_snapshot()}


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
