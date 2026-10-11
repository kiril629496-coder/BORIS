from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
import os

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services import service_marketplace as marketplace
from app.services.outreach_branding import (
    BORIS_SITE,
    CONSULT_PHONE_DISPLAY,
    MAX_PHONE_DISPLAY,
    MAX_URL,
    WHATSAPP_URL,
)

TZ = "Europe/Moscow"

def _scalar(db, sql: str, params: dict | None = None, default=0):
    try:
        v = db.execute(text(sql), params or {}).scalar()
        return default if v is None else v
    except Exception:
        return default

def status() -> dict:
    db = SessionLocal()
    try:
        now = datetime.now(ZoneInfo(TZ))
        params = {"tz": TZ, "d": now.date()}

        tg_daily_cap = max(1, int(os.getenv("HOT_LEAD_AUTO_DAILY_CAP", "10") or 10))
        tg_initial_sent = int(_scalar(db, """SELECT count(*) FROM boris_sales_hot_leads
            WHERE outbound_transport='telegram_personal'
              AND timezone(:tz,contacted_at)::date=:d""", params))
        tg_followup_sent = int(_scalar(db, """SELECT count(*) FROM boris_sales_hot_leads
            WHERE followup_sent_at IS NOT NULL
              AND timezone(:tz,followup_sent_at)::date=:d""", params))
        tg_sent_total = tg_initial_sent + tg_followup_sent
        tg_ready = int(_scalar(db, """SELECT count(*) FROM boris_sales_hot_leads l
            WHERE l.status='new'
              AND l.monitor_intent='buy_service'
              AND COALESCE(l.boris_fit,0)>=75
              AND COALESCE(l.approval_required,false)=false
              AND COALESCE(l.source_posted_at,l.discovered_at)>=NOW()-interval '72 hours'
              AND (COALESCE(l.contact_username,'')<>'' OR
                   (l.contact_peer_id IS NOT NULL AND l.contact_access_hash IS NOT NULL))"""))
        tg_stuck = int(_scalar(db, """SELECT count(*) FROM boris_sales_hot_leads
            WHERE status='approved' AND contacted_at IS NULL AND outbound_message_id IS NULL
              AND approved_at<NOW()-interval '20 minutes'"""))
        tg_failed_today = int(_scalar(db, """SELECT count(*) FROM boris_sales_hot_leads
            WHERE COALESCE(outbound_error,'')<>''
              AND contacted_at IS NULL
              AND status NOT IN ('skipped','declined','lost','ignored')
              AND timezone(:tz,updated_at)::date=:d""", params))
        tg_cooldown = str(_scalar(db, """SELECT value_text FROM monitor_runtime_state
            WHERE key='tg_personal_peer_flood_cooldown_until'""", default="") or "")
        tg_cooldown_active = bool(_scalar(db, """SELECT CASE
            WHEN value_text ~ '^[0-9]{4}-' THEN value_text::timestamptz>NOW()
            ELSE false END
            FROM monitor_runtime_state WHERE key='tg_personal_peer_flood_cooldown_until'""", default=False))
        tg_reply_degraded = bool(_scalar(db, """SELECT COALESCE((details->>'reply_pipeline_degraded')::int,0)
            FROM telegram_sales_cycle_runs ORDER BY started_at DESC LIMIT 1""", default=0))
        tg_latest_cycle = str(_scalar(db, """SELECT status FROM telegram_sales_cycle_runs
            ORDER BY started_at DESC LIMIT 1""", default="") or "")

        telegram = {
            "active_sources": int(_scalar(db, "SELECT count(*) FROM monitor_sources WHERE platform='telegram' AND is_active=TRUE")),
            "waiting": int(_scalar(db, "SELECT count(*) FROM boris_sales_hot_leads WHERE status='new' AND COALESCE(approval_required,false)=false")),
            "ready_to_send": tg_ready,
            "daily_cap": tg_daily_cap,
            "initial_sent_today": tg_initial_sent,
            "followup_sent_today": tg_followup_sent,
            "sent_today": tg_sent_total,
            "remaining_today": max(0, tg_daily_cap - tg_sent_total),
            "approved_stuck": tg_stuck,
            "failed_today": tg_failed_today,
            "cooldown_until": tg_cooldown or None,
            "external_rate_limit": tg_cooldown_active,
            "reply_pipeline_degraded": tg_reply_degraded,
            "latest_cycle_status": tg_latest_cycle,
            "replies_today": int(_scalar(db, "SELECT count(DISTINCT hot_lead_id) FROM boris_sales_tg_replies WHERE timezone(:tz,received_at)::date=:d", params)),
            "qualified_total": int(_scalar(db, "SELECT count(*) FROM boris_sales_hot_leads WHERE status='qualified'")),
        }

        email = {
            "active_campaigns": int(_scalar(db, "SELECT count(*) FROM prospect_campaigns WHERE status='active'")),
            "ready_contacts": int(_scalar(db, """SELECT count(*) FROM prospect_campaign_members m
                JOIN prospect_campaigns c ON c.id=m.campaign_id
                WHERE c.status='active' AND m.status='ready'""")),
            "sent_today": int(_scalar(db, """SELECT count(*) FROM prospect_campaign_members m
                JOIN prospect_campaigns c ON c.id=m.campaign_id
                WHERE timezone(:tz,m.sent_at)::date=:d""", params)),
            "replies_today": int(_scalar(db, """SELECT count(*) FROM prospect_inbound_replies
                WHERE COALESCE(message_kind,'human')='human'
                  AND timezone(:tz,received_at)::date=:d""", params)),
            "qualified_total": int(_scalar(db, "SELECT count(*) FROM manager_leads WHERE lead_source='email_outreach' AND status='квалифицирован'")),
        }

        ms = marketplace.status()
        config = marketplace.free_outreach_config()
        registrations = marketplace.registration_plan()
        placements = {
            "enabled_free_platforms": len(config["enabled_platforms"]),
            "blocked_nonfree_or_unverified": len(config["blocked_platforms"]),
            "drafts": int(ms.get("drafts") or 0),
            "needs_owner_approval": int((ms.get("by_status") or {}).get("needs_owner_approval") or 0),
            "approved": int((ms.get("by_status") or {}).get("approved") or 0),
            "posted": int((ms.get("by_status") or {}).get("posted") or 0),
            "ready_accounts": sum(1 for x in registrations if x.get("status") == "ready"),
            "publications": int(ms.get("publications") or 0),
        }

        return {
            "module": "client_acquisition",
            "title": "Привлечение клиентов",
            "free_only": True,
            "paid_budget_rub": 0,
            "contacts": {
                "site": BORIS_SITE,
                "whatsapp": WHATSAPP_URL,
                "max": MAX_URL,
                "phone": CONSULT_PHONE_DISPLAY,
            },
            "channels": {
                "telegram": telegram,
                "email": email,
                "free_placements": placements,
            },
            "totals": {
                "outbound_today": telegram["sent_today"] + email["sent_today"],
                "replies_today": telegram["replies_today"] + email["replies_today"],
                "qualified_total": telegram["qualified_total"] + email["qualified_total"],
            },
        }
    finally:
        db.close()

def config() -> dict:
    return marketplace.free_outreach_config()

def guardian_status() -> dict:
    from pathlib import Path
    import json
    path = Path("/root/BORIS/backend/data/service_marketplaces/guardian_status.json")
    if not path.exists():
        return {"status": "not_run", "last_run": None, "remaining_audits": None, "failed": []}
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
        return {
            "status": "ok" if not row.get("failed") else "partial",
            "last_run": row.get("at"),
            "remaining_audits": row.get("remaining_audits"),
            "failed": row.get("failed") or [],
            "audited": row.get("audited") or [],
            "free_platforms": row.get("free_platforms") or [],
            "discovery": row.get("discovery") or {},
            "auto_discovered_candidates": int(row.get("auto_discovered_candidates") or 0),
            "registration_preflights": row.get("registration_preflights") or [],
            "registration_submissions": row.get("registration_submissions") or [],
            "registration_verifications": row.get("registration_verifications") or [],
            "platform_bootstrap": marketplace.platform_bootstrap_queue(),
            "crowd_capacity": {
                **(row.get("crowd_capacity") or {}),
                "after": __import__("app.services.crowd_seo", fromlist=["capacity_snapshot"]).capacity_snapshot(),
                "live": True,
            },
            "crowd_seo": row.get("crowd_seo") or {},
        }
    except Exception as exc:
        return {"status": "invalid", "last_run": None, "remaining_audits": None, "failed": [{"error": str(exc)}]}
