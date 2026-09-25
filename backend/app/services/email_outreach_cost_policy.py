# -*- coding: utf-8 -*-
"""Read-only unit economics and cost safety for BORIS Email Outreach.

This module deliberately owns no SMTP/IMAP, queue, copy, tracking or shared
AI-budget writes.  It reads the canonical production ledgers and converts them
into a bounded economics/capacity snapshot for the 5-10 client service model.

No provider/API calls are made here.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.email_queue import BATCH_SIZE
from app.services.prospect_banner import banner_pool_stats

BASE = Path("/root/BORIS/backend")
ENV_FILE = BASE / ".env"

# Product/economics contract for the current service.
INCLUDED_BANNERS_MIN = 10
INCLUDED_BANNERS_MAX = 20
DEFAULT_SENDS_PER_CLIENT_DAY = 20
SCALE_CLIENTS = (5, 10)

# The actual recent premium unit price is currently much lower, but the
# economics guard must leave room for model/input price drift and small retries.
VARIABLE_TECH_BUDGET_PER_CLIENT_RUB = 200.0
FALLBACK_PREMIUM_BANNER_UNIT_RUB = 10.0

# Shared server warning thresholds mirror BORIS reliability semantics.
DISK_DEGRADED_FREE_PCT = 12.0
DISK_CRITICAL_FREE_PCT = 5.0


def _configured_bool(name: str, default: bool = False) -> bool:
    """Read one boolean without exposing or loading unrelated .env secrets."""
    raw = os.getenv(name)
    if raw is None:
        try:
            prefix = name + "="
            for line in ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith(prefix):
                    raw = line[len(prefix):].strip().strip('"').strip("'")
                    break
        except OSError:
            raw = None
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def project_variable_cost(
    premium_banner_unit_rub: float,
    *,
    clients: int,
    banners_per_client: int,
) -> dict[str, Any]:
    clients = max(0, int(clients))
    banners = max(0, int(banners_per_client))
    unit = max(0.0, float(premium_banner_unit_rub or 0.0))
    total_banners = clients * banners
    total = round(total_banners * unit, 2)
    per_client = round(banners * unit, 2)
    return {
        "clients": clients,
        "banners_per_client": banners,
        "total_banners": total_banners,
        "premium_banner_unit_rub": round(unit, 4),
        "projected_image_cost_rub": total,
        "projected_variable_cost_per_client_rub": per_client,
    }


def _memory_available_mb() -> int | None:
    try:
        rows = {}
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, value = line.split(":", 1)
            rows[key] = value.strip()
        kb = int(rows["MemAvailable"].split()[0])
        return kb // 1024
    except Exception:
        return None


def server_capacity_snapshot() -> dict[str, Any]:
    usage = shutil.disk_usage("/")
    free_pct = round((usage.free / usage.total) * 100, 1) if usage.total else 0.0
    try:
        load1, load5, load15 = os.getloadavg()
    except (AttributeError, OSError):
        load1 = load5 = load15 = 0.0
    cpu_count = int(os.cpu_count() or 1)
    return {
        "cpu_count": cpu_count,
        "load_1m": round(float(load1), 2),
        "load_5m": round(float(load5), 2),
        "load_15m": round(float(load15), 2),
        "load_5m_per_cpu": round(float(load5) / cpu_count, 3),
        "memory_available_mb": _memory_available_mb(),
        "disk_free_gb": round(usage.free / (1024 ** 3), 1),
        "disk_free_pct": free_pct,
        "disk_state": (
            "critical"
            if free_pct < DISK_CRITICAL_FREE_PCT
            else ("degraded" if free_pct < DISK_DEGRADED_FREE_PCT else "ok")
        ),
    }


def _usage_snapshot(days: int = 30) -> dict[str, Any]:
    days = max(1, min(int(days), 90))
    db = SessionLocal()
    try:
        premium = db.execute(
            text(
                """
                SELECT count(*)::int AS calls,
                       COALESCE(sum(images),0)::int AS images,
                       round(COALESCE(sum(cost_rub),0)::numeric,2) AS cost_rub
                FROM api_usage
                WHERE created_at >= NOW() - make_interval(days => :days)
                  AND provider='openai'
                  AND model='gpt-image-2'
                  AND COALESCE(images,0)>0
                  AND lower(COALESCE(operation,'')) LIKE '%banner%'
                  AND lower(COALESCE(account_id,'')) NOT LIKE 'qa%'
                  AND lower(COALESCE(account_id,'')) NOT LIKE '__qa_%'
                """
            ),
            {"days": days},
        ).mappings().one()

        owner = db.execute(
            text(
                """
                SELECT count(*)::int AS calls,
                       COALESCE(sum(images),0)::int AS images,
                       COALESCE(sum(prompt_tokens),0)::bigint AS prompt_tokens,
                       COALESCE(sum(completion_tokens),0)::bigint AS completion_tokens,
                       round(COALESCE(sum(cost_rub),0)::numeric,2) AS cost_rub
                FROM api_usage
                WHERE account_id='__owner_outreach__'
                  AND created_at >= NOW() - make_interval(days => :days)
                """
            ),
            {"days": days},
        ).mappings().one()

        recent = db.execute(
            text(
                """
                SELECT
                  count(*) FILTER (
                    WHERE COALESCE(prompt_tokens,0)+COALESCE(completion_tokens,0)>0
                      AND COALESCE(images,0)=0
                  )::int AS paid_text_calls_24h,
                  round(COALESCE(sum(cost_rub) FILTER (
                    WHERE COALESCE(prompt_tokens,0)+COALESCE(completion_tokens,0)>0
                      AND COALESCE(images,0)=0
                  ),0)::numeric,2) AS paid_text_cost_rub_24h,
                  count(*) FILTER (
                    WHERE operation='prospect_outreach_openai_banner'
                      AND COALESCE(images,0)>0
                  )::int AS paid_outreach_banner_calls_24h,
                  round(COALESCE(sum(cost_rub) FILTER (
                    WHERE operation='prospect_outreach_openai_banner'
                      AND COALESCE(images,0)>0
                  ),0)::numeric,2) AS paid_outreach_banner_cost_rub_24h
                FROM api_usage
                WHERE account_id='__owner_outreach__'
                  AND created_at >= NOW() - interval '24 hours'
                """
            )
        ).mappings().one()

        guard_recent = {}
        try:
            guard_recent = dict(
                db.execute(
                    text(
                        """
                        SELECT
                          count(*) FILTER (
                            WHERE event_code IN (
                              'AI_BUDGET_RESERVE_FAILED',
                              'AI_BUDGET_NOT_ALLOWED',
                              'PAID_IMAGE_BURST_CAP',
                              'PAID_IMAGE_DAILY_CAP',
                              'PAID_IMAGE_OUTCOME_UNCERTAIN'
                            )
                          )::int AS cost_guard_events_6h,
                          max(created_at) AS last_cost_guard_event_at
                        FROM ai_guard_event_log
                        WHERE account_id='__owner_outreach__'
                          AND created_at >= NOW() - interval '6 hours'
                        """
                    )
                ).mappings().one()
            )
        except Exception:
            db.rollback()
            guard_recent = {
                "cost_guard_events_6h": None,
                "last_cost_guard_event_at": None,
                "telemetry": "unavailable",
            }

        premium_d = dict(premium)
        owner_d = dict(owner)
        recent_d = dict(recent)
        images = int(premium_d.get("images") or 0)
        premium_cost = float(premium_d.get("cost_rub") or 0.0)
        if images:
            premium_unit = premium_cost / images
            unit_source = "api_usage_30d"
        else:
            premium_unit = FALLBACK_PREMIUM_BANNER_UNIT_RUB
            unit_source = "safe_fallback_no_recent_data"

        return {
            "days": days,
            "premium_banner": {
                **premium_d,
                "cost_rub": premium_cost,
                "unit_cost_rub": round(premium_unit, 4),
                "unit_cost_source": unit_source,
            },
            "owner_outreach": {
                **owner_d,
                "cost_rub": float(owner_d.get("cost_rub") or 0.0),
            },
            "recent_24h": {
                **recent_d,
                "paid_text_cost_rub_24h": float(recent_d.get("paid_text_cost_rub_24h") or 0.0),
                "paid_outreach_banner_cost_rub_24h": float(recent_d.get("paid_outreach_banner_cost_rub_24h") or 0.0),
            },
            "guard_recent_6h": guard_recent,
        }
    finally:
        db.close()


def economics_snapshot(days: int = 30) -> dict[str, Any]:
    usage = _usage_snapshot(days)
    pool = banner_pool_stats()
    capacity = server_capacity_snapshot()
    unit = float(usage["premium_banner"]["unit_cost_rub"])

    projections = []
    for clients in SCALE_CLIENTS:
        for banners in (INCLUDED_BANNERS_MIN, INCLUDED_BANNERS_MAX):
            projections.append(
                project_variable_cost(unit, clients=clients, banners_per_client=banners)
            )

    configured_claim_capacity = int(BATCH_SIZE) * 60 * 24
    planned_5 = 5 * DEFAULT_SENDS_PER_CLIENT_DAY
    planned_10 = 10 * DEFAULT_SENDS_PER_CLIENT_DAY

    return {
        "policy": {
            "included_banners_min": INCLUDED_BANNERS_MIN,
            "included_banners_max": INCLUDED_BANNERS_MAX,
            "default_sends_per_client_day": DEFAULT_SENDS_PER_CLIENT_DAY,
            "variable_tech_budget_per_client_rub": VARIABLE_TECH_BUDGET_PER_CLIENT_RUB,
            "paid_copy_enabled": _configured_bool("PROSPECT_PAID_COPY_ENABLED", False),
            "email_send_variable_cost_assumption_rub": 0.0,
            "client_mailbox_cost_to_boris_rub": 0.0,
        },
        "usage": usage,
        "banner_pool": pool,
        "capacity": {
            **capacity,
            "email_worker_batch_size": int(BATCH_SIZE),
            "email_worker_timer_seconds": 60,
            "configured_claim_capacity_per_day": configured_claim_capacity,
            "planned_sends_5_clients_per_day": planned_5,
            "planned_sends_10_clients_per_day": planned_10,
            "planned_10_clients_share_of_claim_capacity_pct": round(
                planned_10 * 100 / configured_claim_capacity, 3
            )
            if configured_claim_capacity
            else None,
        },
        "projections": projections,
    }


def health(days: int = 30) -> dict[str, Any]:
    snap = economics_snapshot(days)
    critical: list[str] = []
    warnings: list[str] = []

    policy = snap["policy"]
    recent = snap["usage"]["recent_24h"]
    pool = snap["banner_pool"]
    capacity = snap["capacity"]

    if policy["paid_copy_enabled"]:
        critical.append("PROSPECT_PAID_COPY_ENABLED: платная генерация текста включена")
    if bool(pool.get("paid_generation_enabled")):
        critical.append("prospect_banner: платная генерация на send-path включена")
    if int(recent.get("paid_text_calls_24h") or 0) > 0:
        critical.append(
            f"за 24ч найдено платных текстовых вызовов outreach: {recent.get('paid_text_calls_24h')}"
        )
    if int(recent.get("paid_outreach_banner_calls_24h") or 0) > 0:
        critical.append(
            "за 24ч send-path снова покупал outreach-баннеры вместо reuse-only"
        )

    unit = float(snap["usage"]["premium_banner"]["unit_cost_rub"])
    per_client_20 = project_variable_cost(
        unit, clients=1, banners_per_client=INCLUDED_BANNERS_MAX
    )["projected_variable_cost_per_client_rub"]
    if per_client_20 > VARIABLE_TECH_BUDGET_PER_CLIENT_RUB:
        warnings.append(
            f"20 premium-баннеров стоят {per_client_20:.2f} ₽/клиент — выше внутреннего бюджета "
            f"{VARIABLE_TECH_BUDGET_PER_CLIENT_RUB:.0f} ₽"
        )

    if capacity["disk_state"] == "critical":
        critical.append(
            f"общий сервер: свободно только {capacity['disk_free_pct']}% диска"
        )
    elif capacity["disk_state"] == "degraded":
        warnings.append(
            f"общий сервер: свободно {capacity['disk_free_pct']}% диска"
        )

    state = "critical" if critical else ("degraded" if warnings else "ok")
    return {
        "state": state,
        "critical": critical,
        "warnings": warnings,
        "owner_action_required": bool(critical),
        "snapshot": snap,
    }
