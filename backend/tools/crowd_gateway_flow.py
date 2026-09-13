#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse, urlunparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / '.env')

from sqlalchemy import text

from app.api import browser_gateway as bg
from app.db.session import SessionLocal
from app.services import platform_rules
from app.services import service_marketplace as marketplace
from app.services.outreach_branding import WHATSAPP_URL, MAX_URL, CONSULT_PHONE_DISPLAY

ACCOUNT = bg.CROWD_GATEWAY_ACCOUNT
ACTIVE = {"waiting_gateway", "queued_gateway", "claimed_gateway", "running_gateway", "resume_gateway", "waiting_human"}
CAPTCHA_CHECKPOINTS = {"captcha_required", "captcha_age_and_terms_required"}
EMAIL_CHECKPOINTS = {"email_verification_required"}

REGISTER_OVERRIDES = {
    "forum_seo_net": "https://forum-seo.net/register/",
    "namepros_promotional": "https://www.namepros.com/register/",
    "forum_promotion_employment": "https://forumpromotion.net/register/",
    "htmlforums_programming": "https://htmlforums.net/register/",
    "partnersearch": "https://www.partnersearch.ru/business/ucp.php?mode=register",
    "zismo_programming_services": "https://zismo.biz/index.php?app=core&module=global&section=register",
    "mmgp": "https://mmgp.com/register/",
    "skripters": "https://top.skripters.biz/register/",
    "wjunction_services": "https://www.wjunction.com/register/",
    "disc_affiliate_forum": "https://affiliate.forum/register/",
    "cyberforum_freelancers": "https://www.cyberforum.ru/register.php",
    "nulled_services": "https://nulled.cc/register/",
    "disc_optiboard_com": "https://www.optiboard.com/forums/register",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_jobs() -> list[dict]:
    db = SessionLocal()
    try:
        rows = db.execute(
            text("select key,value from storage where account_id=:a and key like 'browser_job:crowd_%' order by id asc"),
            {"a": ACCOUNT},
        ).fetchall()
    finally:
        db.close()
    out = []
    for key, raw in rows:
        try:
            job = json.loads(raw)
        except Exception:
            continue
        job["_key"] = key
        out.append(job)
    return out


def save_job(job: dict) -> None:
    key = str(job.get("_key") or f"browser_job:{job['job_id']}")
    clean = {k: v for k, v in job.items() if k != "_key"}
    bg._db_put(ACCOUNT, key, clean)


def active_job() -> dict | None:
    jobs = load_jobs()
    for job in reversed(jobs):
        if str(job.get("status") or "") in ACTIVE:
            return job
    return None


def registration_url(row: dict) -> str:
    platform = str(row.get("platform") or "")
    if platform in REGISTER_OVERRIDES:
        return REGISTER_OVERRIDES[platform]
    raw = str(row.get("account_url") or row.get("url") or "").strip()
    if not raw:
        raise RuntimeError(f"registration_url_missing:{platform}")
    parsed = urlparse(raw)
    path = parsed.path or "/"
    if path in {"", "/"} and parsed.scheme in {"http", "https"}:
        return urlunparse((parsed.scheme, parsed.netloc, "/register/", "", "", ""))
    return raw


def queue_registration(platform: str | None = None) -> dict:
    current = active_job()
    if current:
        return {"status": "busy", "job_id": current.get("job_id"), "platform": (current.get("metadata") or {}).get("platform"), "job_status": current.get("status")}

    rows = marketplace.registration_plan()
    candidates = []
    for row in rows:
        if platform and str(row.get("platform") or "") != platform:
            continue
        if str(row.get("status") or "") == "ready":
            continue
        checkpoint = str(row.get("checkpoint") or "")
        if checkpoint not in CAPTCHA_CHECKPOINTS:
            continue
        candidates.append(row)
    candidates.sort(key=lambda r: (0 if r.get("checkpoint") == "captcha_required" else 1, str(r.get("updated_at") or ""), str(r.get("platform") or "")))
    if not candidates:
        return {"status": "empty"}

    row = candidates[0]
    pkey = str(row["platform"])
    url = registration_url(row)
    domain = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if not bg._crowd_domain_allowed(domain):
        raise RuntimeError(f"crowd_domain_not_audited:{domain}")
    job_id = "crowd_reg_" + secrets.token_hex(7)
    job = {
        "job_id": job_id,
        "status": "waiting_gateway",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "account_id": ACCOUNT,
        "domain": domain,
        "capability": "crowd_registration",
        "risk_level": "WRITE",
        "mode": "register",
        "steps": [
            {"action": "open_url", "url": url, "timeout_ms": 60000},
            {"action": "wait_for", "selector": "form input[type=\"email\"], form input[autocomplete=\"username\"], form input[name*=\"email\" i], form input[id*=\"email\" i], form input[name*=\"user\" i], form input[id*=\"user\" i]", "timeout_ms": 30000},
            {"action": "crowd_prefill_registration", "timeout_ms": 15000},
            {"action": "crowd_human_checkpoint", "timeout_ms": 15000},
            {"action": "crowd_submit_registration", "timeout_ms": 30000},
            {"action": "read_page", "timeout_ms": 15000},
        ],
        "gateway_id": None,
        "confirmed": True,
        "metadata": {
            "purpose": "crowd_registration",
            "platform": pkey,
            "email": str(row.get("email") or ""),
            "checkpoint_before": str(row.get("checkpoint") or ""),
            "registration_url": url,
        },
        "results": [],
        "error": None,
        "privacy": {"guard": True, "credentials_local_only": True},
    }
    bg._db_put(ACCOUNT, f"browser_job:{job_id}", job)
    return {"status": "queued", "job_id": job_id, "platform": pkey, "url": url}


def resume_waiting(platform: str | None = None) -> dict:
    jobs = load_jobs()
    for job in reversed(jobs):
        meta = job.get("metadata") or {}
        if job.get("status") != "waiting_human":
            continue
        if platform and str(meta.get("platform") or "") != platform:
            continue
        job["status"] = "resume_gateway"
        job["updated_at"] = now_iso()
        job["human_resumed_at"] = now_iso()
        save_job(job)
        return {"status": "resumed", "job_id": job.get("job_id"), "platform": meta.get("platform"), "current_step": job.get("current_step")}
    return {"status": "nothing_waiting"}


def block_waiting(platform: str, checkpoint: str, reason: str) -> dict:
    jobs = load_jobs()
    for job in reversed(jobs):
        meta = job.get("metadata") or {}
        if str(meta.get("platform") or "") != platform:
            continue
        if str(job.get("status") or "") not in ACTIVE:
            continue
        job["status"] = "completed_with_errors"
        job["error"] = reason
        job["updated_at"] = now_iso()
        job["completed_at"] = job["updated_at"]
        meta["blocked_checkpoint"] = checkpoint
        meta["reconciled_at"] = now_iso()
        job["metadata"] = meta
        save_job(job)
        marketplace.upsert_registration(platform, "blocked", checkpoint=checkpoint, last_error=reason)
        return {"status": "blocked", "job_id": job.get("job_id"), "platform": platform, "checkpoint": checkpoint}
    marketplace.upsert_registration(platform, "blocked", checkpoint=checkpoint, last_error=reason)
    return {"status": "blocked_without_active_job", "platform": platform, "checkpoint": checkpoint}


def _result_for_action(job: dict, action: str) -> dict:
    steps = job.get("steps") or []
    idx = next((i for i, step in enumerate(steps) if step.get("action") == action), None)
    if idx is None:
        return {}
    for row in job.get("results") or []:
        if int(row.get("step_index", -1)) == idx:
            return row.get("result") or {}
    return {}


def queue_email_confirmation(platform: str | None = None) -> dict:
    """Confirm a freshly created forum account through Gmail in the isolated Crowd window.

    No owner work tab is reused: Browser Gateway keeps one dedicated Crowd window
    and navigates that same tab Gmail -> confirmation URL -> forum.
    """
    current = active_job()
    if current:
        return {"status": "busy", "job_id": current.get("job_id"), "platform": (current.get("metadata") or {}).get("platform")}
    rows = marketplace.registration_plan()
    candidates = []
    for row in rows:
        if platform and str(row.get("platform") or "") != platform:
            continue
        if str(row.get("status") or "") == "ready":
            continue
        if str(row.get("checkpoint") or "") not in EMAIL_CHECKPOINTS:
            continue
        candidates.append(row)
    candidates.sort(key=lambda r: (str(r.get("updated_at") or ""), str(r.get("platform") or "")))
    if not candidates:
        return {"status": "empty"}
    row = candidates[0]
    pkey = str(row.get("platform") or "")
    source_url = str(row.get("account_url") or row.get("url") or "")
    source_domain = (urlparse(source_url).hostname or "").lower().removeprefix("www.")
    if not source_domain:
        return {"status": "blocked", "reason": "verification_source_domain_missing", "platform": pkey}
    query = quote(f'newer_than:1d "{source_domain}"', safe="")
    gmail_url = f"https://mail.google.com/mail/u/0/#search/{query}"
    job_id = "crowd_email_" + secrets.token_hex(7)
    job = {
        "job_id": job_id,
        "status": "waiting_gateway",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "account_id": ACCOUNT,
        "domain": "mail.google.com",
        "capability": "crowd_email_confirm",
        "risk_level": "WRITE",
        "mode": "email_confirm",
        "steps": [
            {"action": "open_url", "url": gmail_url, "timeout_ms": 60000},
            {"action": "wait_for", "selector": "div[role=\"main\"], table", "timeout_ms": 30000},
            {"action": "crowd_gmail_open_verification_email", "timeout_ms": 15000},
            {"action": "wait_for", "selector": "a[href]", "timeout_ms": 20000},
            {"action": "crowd_gmail_follow_verification_link", "timeout_ms": 60000},
            {"action": "crowd_verify_account_ready", "timeout_ms": 15000},
            {"action": "read_page", "timeout_ms": 15000},
        ],
        "gateway_id": None,
        "confirmed": True,
        "metadata": {
            "purpose": "crowd_email_confirmation",
            "platform": pkey,
            "email": str(row.get("email") or ""),
            "source_domain": source_domain,
            "forum_url": str(row.get("url") or source_url),
            "checkpoint_before": str(row.get("checkpoint") or ""),
        },
        "results": [],
        "error": None,
        "privacy": {"guard": True, "mail_scope": "current_forum_verification_only"},
    }
    bg._db_put(ACCOUNT, f"browser_job:{job_id}", job)
    return {"status": "queued", "job_id": job_id, "platform": pkey, "gmail_url": gmail_url}


def _post_url_for(platform: str, draft: dict) -> str | None:
    """Choose a publication surface that actually matches the draft semantics.

    A historical draft may carry a stale/mismatched surface id.  We never trust
    that blindly: surfaces with required_terms must match the current draft.
    Neutral commercial surfaces (no required_terms) are the safe fallback.
    """
    blob = " ".join([
        str(draft.get("title") or ""),
        str(draft.get("text") or ""),
        str(draft.get("target_url") or ""),
    ]).casefold()
    surfaces = marketplace.VERIFIED_PUBLICATION_SURFACES.get(platform, []) or []

    def matches(surface: dict) -> bool:
        required = [str(x).casefold().strip() for x in (surface.get("required_terms") or []) if str(x).strip()]
        return not required or any(term in blob for term in required)

    direct = str(draft.get("publication_surface_post_url") or "").strip()
    direct_id = str(draft.get("publication_surface_id") or "").strip()
    if direct:
        surface = next((s for s in surfaces if str(s.get("id") or "") == direct_id or str(s.get("post_url") or "") == direct), None)
        if surface is None or matches(surface):
            return direct

    specific = [s for s in surfaces if (s.get("required_terms") or []) and matches(s)]
    neutral = [s for s in surfaces if not (s.get("required_terms") or []) and matches(s)]
    for surface in specific + neutral:
        value = str(surface.get("post_url") or "").strip()
        if value:
            return value
    return None


DUAL_CAMPAIGN_KINDS = ("boris", "development")
COMBINED_CAMPAIGN_KIND = "combined"
SINGLE_TOPIC_REQUIREMENTS = {
    "single_relevant_commercial_topic",
    "single_relevant_provider_topic",
    "single_relevant_topic",
    "single_relevant_vendor_topic",
    "one_commercial_thread_per_vendor",
    "one_personal_service_topic",
    "one_vendor_topic_only",
    "no_duplicate_service_topic",
    "no_duplicate_topics",
    "separate_advertising_topic_prohibited",
}


def _single_topic_required(platform: str) -> bool:
    row = platform_rules.latest(platform) or {}
    reqs = {str(x).strip() for x in (row.get("requirements") or []) if str(x).strip()}
    if reqs & SINGLE_TOPIC_REQUIREMENTS:
        return True
    frequency = []
    for inspection in row.get("inspections") or []:
        evidence = inspection.get("evidence") or {}
        frequency.extend(str(x) for x in (evidence.get("frequency") or []))
    blob = " ".join(frequency).casefold()
    single_markers = (
        "one promotional post", "one promo", "one commercial thread", "one vendor topic",
        "1 promotional post", "1 promo", "1 post per", "once per",
        "один рекламный пост", "одна рекламная тема", "одна коммерческая тема",
        "не более одного поста", "не более 1 поста", "один пост в",
    )
    return any(marker in blob for marker in single_markers)


def _owner_contact_block() -> str:
    return (
        f"WhatsApp: {WHATSAPP_URL}\n"
        f"MAX: {MAX_URL}\n"
        f"Телефон: {CONSULT_PHONE_DISPLAY}"
    )


def _owner_boris_text() -> str:
    return (
        "BORIS AI — виртуальный отдел продаж и рекламы под ключ\n\n"
        "BORIS объединяет входящие обращения, CRM, задачи менеджеров, переписки, звонки, реактивацию и контроль KPI. "
        "Подходит бизнесу, где собственнику приходится вручную следить за лидами, рекламой и работой отдела продаж.\n\n"
        "Сайт: https://boris-ai.pro\n"
        f"{_owner_contact_block()}\n\n"
        "#BORISAI #автоматизацияпродаж #CRM #ИИменеджер #автоматизациябизнеса"
    )


def _owner_development_text() -> str:
    return (
        "Разработка программного обеспечения для бизнеса\n\n"
        "Разрабатываем веб-приложения, CRM, SaaS, API-интеграции, мобильные приложения, Telegram-боты и AI-автоматизацию. "
        "Можно начать с одного процесса или MVP и довести решение до production.\n\n"
        "Разработка ПО: https://boris-ai.pro/software-dev\n"
        f"{_owner_contact_block()}\n\n"
        "#разработкаПО #вебразработка #CRMразработка #SaaS #API #MVP #AIавтоматизация"
    )


def _ensure_combined_campaign_draft(platform: str) -> dict:
    rows = marketplace.list_drafts(status=None)
    existing = next((d for d in rows if d.get("platform") == platform and d.get("crowd_campaign_kind") == COMBINED_CAMPAIGN_KIND), None)
    if existing and existing.get("status") == "posted":
        return existing
    p = marketplace.get_platform(platform)
    platform_name = getattr(p, "name", None) or platform
    draft = {
        "id": f"crowd_owner_combined_{platform}",
        "platform": platform,
        "platform_name": platform_name,
        "topic": "owner_combined_promo",
        "crowd_campaign_kind": COMBINED_CAMPAIGN_KIND,
        "title": "BORIS AI и разработка ПО для бизнеса",
        "text": (
            f"{_owner_boris_text()}\n\n"
            "------------------------------\n\n"
            f"{_owner_development_text()}\n\n"
            "Ключевые фразы: автоматизация продаж; CRM автоматизация; ИИ-менеджер; BORIS AI; автоматизация рекламы; разработка ПО под заказ; разработка веб-приложений; разработка CRM; разработка SaaS; API интеграция; мобильная разработка; разработка MVP; AI автоматизация; автоматизация бизнеса"
        ),
        "site_url": "https://boris-ai.pro/",
        "target_url": "https://boris-ai.pro/",
        "status": "approved",
        "generated_without_openai": True,
        "owner_standing_approval": True,
        "variant": 1,
    }
    # Replace only our deterministic, not-yet-posted owner draft.
    raw = marketplace._load(marketplace.DRAFTS_FILE, [])
    out = []
    replaced = False
    for row in raw:
        if row.get("id") == draft["id"] and row.get("status") != "posted":
            out.append(draft); replaced = True
        else:
            out.append(row)
    if not replaced:
        out.append(draft)
    marketplace._save(marketplace.DRAFTS_FILE, out)
    return draft


def _ensure_dual_campaign_drafts(platform: str) -> list[dict]:
    """Materialize the owner's standing two-post campaign for one forum.

    These drafts are deterministic and owner-approved by the explicit standing
    instruction: every eligible forum gets one BORIS product post and one
    software-development post.  Publication is still gated by fresh forum rules.
    """
    rows = marketplace.list_drafts(status=None)
    existing = {
        str(d.get("crowd_campaign_kind") or ""): d
        for d in rows
        if d.get("platform") == platform and d.get("crowd_campaign_kind") in DUAL_CAMPAIGN_KINDS
    }
    p = marketplace.get_platform(platform)
    platform_name = getattr(p, "name", None) or platform
    created: list[dict] = []

    if "boris" not in existing:
        created.append({
            "id": f"crowd_owner_dual_{platform}_boris",
            "platform": platform,
            "platform_name": platform_name,
            "topic": "owner_dual_boris",
            "crowd_campaign_kind": "boris",
            "title": "BORIS AI — автоматизация продаж, CRM и рекламы",
            "text": (
                f"{_owner_boris_text()}\n\n"
                "Ключевые фразы: автоматизация продаж; CRM автоматизация; ИИ-менеджер; ИИ РОП; контроль лидов; автоматизация Avito; аналитика продаж; реактивация клиентов; контроль менеджеров; автоматизация рекламы; воронка продаж; BORIS AI"
            ),
            "site_url": "https://boris-ai.pro/",
            "target_url": "https://boris-ai.pro/",
            "status": "approved",
            "generated_without_openai": True,
            "owner_standing_approval": True,
            "variant": 1,
            **({
                "publication_surface_id": "seo_services_traffic",
                "publication_surface_post_url": "https://forum-seo.net/forums/seo-uslugi-i-trafik.6/post-thread",
            } if platform == "forum_seo_net" else {}),
        })

    if "development" not in existing:
        created.append({
            "id": f"crowd_owner_dual_{platform}_development",
            "platform": platform,
            "platform_name": platform_name,
            "topic": "owner_dual_development",
            "crowd_campaign_kind": "development",
            "title": "Разработка ПО для бизнеса: веб-сервисы, CRM, SaaS и интеграции",
            "text": (
                f"{_owner_development_text()}\n\n"
                "Ключевые фразы: разработка ПО под заказ; разработка веб-приложений; разработка CRM; разработка SaaS; API интеграция; мобильная разработка; разработка MVP; AI автоматизация; разработка личного кабинета; backend разработка; frontend разработка; автоматизация бизнеса"
            ),
            "site_url": "https://boris-ai.pro/software-dev",
            "target_url": "https://boris-ai.pro/software-dev",
            "status": "approved",
            "generated_without_openai": True,
            "owner_standing_approval": True,
            "variant": 1,
            **({
                "publication_surface_id": "design_technical_services",
                "publication_surface_post_url": "https://forum-seo.net/forums/dizain-i-tekhnicheskaya-chast.50/post-thread",
            } if platform == "forum_seo_net" else {}),
        })

    if created:
        marketplace.save_drafts(created)
    return [
        d for d in marketplace.list_drafts(status=None)
        if d.get("platform") == platform and d.get("crowd_campaign_kind") in DUAL_CAMPAIGN_KINDS
    ]


def queue_publication(platform: str, campaign_kind: str | None = None) -> dict:
    if active_job():
        return {"status": "busy"}
    gate = platform_rules.publish_gate(platform, action="proactive")
    if not gate.get("allowed"):
        return {"status": "blocked", "reason": gate.get("reason")}
    if marketplace.PLATFORM_MATURITY_REQUIREMENTS.get(platform):
        return {"status": "waiting_maturity"}

    single_topic = _single_topic_required(platform)
    if single_topic:
        _ensure_combined_campaign_draft(platform)
        if campaign_kind and campaign_kind != COMBINED_CAMPAIGN_KIND:
            campaign_kind = COMBINED_CAMPAIGN_KIND
    else:
        _ensure_dual_campaign_drafts(platform)
    all_drafts = [d for d in marketplace.list_drafts(status=None) if d.get("platform") == platform]
    if campaign_kind:
        posted = [d for d in all_drafts if d.get("crowd_campaign_kind") == campaign_kind and d.get("status") == "posted"]
        if posted:
            return {"status": "already_posted", "platform": platform, "campaign_kind": campaign_kind, "draft_id": posted[0].get("id")}

    drafts = [d for d in all_drafts if d.get("status") == "approved"]
    if single_topic:
        drafts = [d for d in drafts if d.get("crowd_campaign_kind") == COMBINED_CAMPAIGN_KIND]
    elif campaign_kind:
        drafts = [d for d in drafts if d.get("crowd_campaign_kind") == campaign_kind]
    else:
        drafts.sort(key=lambda d: (0 if d.get("crowd_campaign_kind") in DUAL_CAMPAIGN_KINDS else 1, str(d.get("id") or "")))

    for draft in drafts:
        content = marketplace.validate_publication_content(draft)
        if not content.get("ok"):
            continue
        post_url = _post_url_for(platform, draft)
        if not post_url:
            continue
        domain = (urlparse(post_url).hostname or "").lower().removeprefix("www.")
        if not bg._crowd_domain_allowed(domain):
            continue
        kind = str(draft.get("crowd_campaign_kind") or campaign_kind or "legacy")
        job_id = "crowd_post_" + secrets.token_hex(7)
        target = str(draft.get("target_url") or draft.get("site_url") or "")
        job = {
            "job_id": job_id,
            "status": "waiting_gateway",
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "account_id": ACCOUNT,
            "domain": domain,
            "capability": "crowd_publish",
            "risk_level": "PUBLISH",
            "mode": "publish",
            "steps": [
                {"action": "open_url", "url": post_url, "timeout_ms": 30000},
                {"action": "crowd_prefill_post", "value": str(draft.get("title") or ""), "text": str(draft.get("text") or ""), "timeout_ms": 15000},
                {"action": "crowd_submit_post", "timeout_ms": 30000},
                {"action": "crowd_verify_post", "timeout_ms": 15000},
            ],
            "gateway_id": None,
            "confirmed": True,
            "metadata": {
                "purpose": "crowd_publication",
                "platform": platform,
                "campaign_kind": kind,
                "draft_id": draft.get("id"),
                "post_url": post_url,
                "post_title": str(draft.get("title") or ""),
                "post_text": str(draft.get("text") or ""),
                "target_url": target,
            },
            "results": [],
            "error": None,
            "privacy": {"guard": True},
        }
        bg._db_put(ACCOUNT, f"browser_job:{job_id}", job)
        return {"status": "queued", "job_id": job_id, "platform": platform, "campaign_kind": kind, "draft_id": draft.get("id"), "url": post_url}
    return {"status": "no_approved_publishable_draft", "platform": platform, "campaign_kind": campaign_kind}


def _registration_requires_email_confirmation(result: dict) -> bool:
    blob = " ".join([
        str(result.get("page_text") or ""),
        str(result.get("text") or ""),
        str(result.get("title") or ""),
    ]).casefold()
    markers = (
        "ожидает подтверждения",
        "письмо с подтверждением",
        "подтвердить адрес электронной почты",
        "подтвердите адрес электронной почты",
        "email confirmation",
        "confirmation email",
        "confirm your email",
        "verify your email",
        "account is awaiting confirmation",
    )
    return any(marker in blob for marker in markers)


def reconcile() -> dict:
    changed = []
    jobs = load_jobs()

    # Repair old false-ready classifications from already reconciled jobs.
    current_regs = {r.get("platform"): r for r in marketplace.registration_plan()}
    for job in jobs:
        meta = job.get("metadata") or {}
        if meta.get("purpose") != "crowd_registration" or job.get("status") != "completed":
            continue
        result = _result_for_action(job, "crowd_submit_registration")
        platform = str(meta.get("platform") or "")
        if platform and _registration_requires_email_confirmation(result):
            reg = current_regs.get(platform) or {}
            if reg.get("status") == "ready" or reg.get("checkpoint") != "email_verification_required":
                marketplace.upsert_registration(
                    platform,
                    "verification_required",
                    email=str(meta.get("email") or ""),
                    account_url=str(result.get("url") or meta.get("registration_url") or ""),
                    checkpoint="email_verification_required",
                    last_error="Аккаунт создан; форум ожидает подтверждение email.",
                )

    for job in jobs:
        if job.get("status") not in {"completed", "completed_with_errors"}:
            continue
        meta = job.get("metadata") or {}
        if meta.get("reconciled_at"):
            continue
        purpose = str(meta.get("purpose") or "")
        platform = str(meta.get("platform") or "")
        if purpose == "crowd_registration":
            if job.get("status") == "completed_with_errors":
                marketplace.upsert_registration(platform, "in_progress", checkpoint="registration_gateway_error", last_error=str(job.get("error") or "Browser Gateway registration failed"))
            else:
                result = _result_for_action(job, "crowd_submit_registration")
                state = str(result.get("registration_state") or "unknown")
                if _registration_requires_email_confirmation(result):
                    state = "email_verification_required"
                final_url = str(result.get("url") or meta.get("registration_url") or "")
                if state == "registered":
                    marketplace.upsert_registration(platform, "ready", email=str(meta.get("email") or ""), account_url=final_url, checkpoint="", last_error=None)
                elif state == "email_verification_required":
                    marketplace.upsert_registration(platform, "verification_required", email=str(meta.get("email") or ""), account_url=final_url, checkpoint="email_verification_required", last_error="Аккаунт создан; форум ожидает подтверждение email.")
                else:
                    marketplace.upsert_registration(platform, "in_progress", email=str(meta.get("email") or ""), account_url=final_url, checkpoint="post_submit_review", last_error="Регистрация отправлена; BORIS перепроверит состояние аккаунта.")
            meta["reconciled_at"] = now_iso(); job["metadata"] = meta; save_job(job); changed.append(job.get("job_id"))
        elif purpose == "crowd_email_confirmation":
            verify = _result_for_action(job, "crowd_verify_account_ready")
            if job.get("status") == "completed" and verify.get("ready"):
                marketplace.upsert_registration(
                    platform,
                    "ready",
                    email=str(meta.get("email") or ""),
                    account_url=str(verify.get("url") or meta.get("forum_url") or ""),
                    checkpoint="",
                    last_error=None,
                )
            else:
                marketplace.upsert_registration(
                    platform,
                    "verification_required",
                    email=str(meta.get("email") or ""),
                    account_url=str(meta.get("forum_url") or ""),
                    checkpoint="email_verification_required",
                    last_error=str(job.get("error") or "Письмо подтверждения пока не найдено или аккаунт ещё ожидает активацию."),
                )
            meta["reconciled_at"] = now_iso(); job["metadata"] = meta; save_job(job); changed.append(job.get("job_id"))
        elif purpose == "crowd_publication":
            verify = _result_for_action(job, "crowd_verify_post")
            draft_id = str(meta.get("draft_id") or "")
            if job.get("status") == "completed" and verify.get("verified") and draft_id:
                marketplace.mark_posted(draft_id, str(verify.get("url") or ""))
            else:
                marketplace.record_attempt(platform=platform, action="publish", status_value="failed", draft_id=draft_id or None, url=str(verify.get("url") or "") or None, error_code="crowd_gateway_publication_unverified", error_detail=str(job.get("error") or "Browser Gateway did not verify the published post."))
            meta["reconciled_at"] = now_iso(); job["metadata"] = meta; save_job(job); changed.append(job.get("job_id"))

    if not active_job():
        jobs = load_jobs()
        reg_rows = marketplace.registration_plan()
        regs = {r.get("platform"): r for r in reg_rows}

        # Email confirmation is part of registration, not an owner task. Give a
        # freshly created account one immediate Gmail attempt, then leave delayed
        # mail in the retry pool so it cannot block today's CAPTCHA conveyor.
        email_rows = [r for r in reg_rows if str(r.get("checkpoint") or "") in EMAIL_CHECKPOINTS and r.get("status") != "ready"]
        email_rows.sort(key=lambda r: str(r.get("updated_at") or ""), reverse=True)
        for row in email_rows:
            platform = str(row.get("platform") or "")
            attempts = [
                j for j in jobs
                if (j.get("metadata") or {}).get("purpose") == "crowd_email_confirmation"
                and (j.get("metadata") or {}).get("platform") == platform
            ]
            if not attempts:
                out = queue_email_confirmation(platform)
                if out.get("status") == "queued":
                    return {"changed": changed, "next": out}
            elif len(attempts) < 3:
                last = attempts[-1]
                raw_at = str(last.get("completed_at") or last.get("updated_at") or "")
                try:
                    age = (datetime.now(timezone.utc) - datetime.fromisoformat(raw_at.replace("Z", "+00:00"))).total_seconds()
                except Exception:
                    age = 9999
                if age >= 90:
                    out = queue_email_confirmation(platform)
                    if out.get("status") == "queued":
                        return {"changed": changed, "next": out}

        for platform, row in regs.items():
            if row.get("status") != "ready":
                continue
            if _single_topic_required(str(platform)):
                combined = _ensure_combined_campaign_draft(str(platform))
                kinds = (COMBINED_CAMPAIGN_KIND,)
                campaign_rows = [combined]
            else:
                campaign_rows = _ensure_dual_campaign_drafts(str(platform))
                kinds = DUAL_CAMPAIGN_KINDS
            for kind in kinds:
                posted = any(d.get("crowd_campaign_kind") == kind and d.get("status") == "posted" for d in campaign_rows)
                if posted:
                    continue
                active_same = any(
                    (j.get("metadata") or {}).get("purpose") == "crowd_publication"
                    and (j.get("metadata") or {}).get("platform") == platform
                    and (j.get("metadata") or {}).get("campaign_kind") == kind
                    and j.get("status") in ACTIVE
                    for j in jobs
                )
                if active_same:
                    continue
                attempts = sum(
                    1 for j in jobs
                    if (j.get("metadata") or {}).get("purpose") == "crowd_publication"
                    and (j.get("metadata") or {}).get("platform") == platform
                    and (j.get("metadata") or {}).get("campaign_kind") == kind
                )
                if attempts >= 3:
                    continue
                out = queue_publication(str(platform), kind)
                if out.get("status") == "queued":
                    return {"changed": changed, "next": out}
        return {"changed": changed, "next": queue_registration()}
    return {"changed": changed, "next": {"status": "busy"}}


def status() -> dict:
    current = active_job()
    gateway = bg._account_gateway_state(ACCOUNT)
    regs = marketplace.registration_plan()
    pending = [r for r in regs if str(r.get("checkpoint") or "") in CAPTCHA_CHECKPOINTS and r.get("status") != "ready"]
    email_pending = [r for r in regs if str(r.get("checkpoint") or "") in EMAIL_CHECKPOINTS and r.get("status") != "ready"]
    return {
        "gateway_online": bool(gateway.get("online")),
        "gateway_state": gateway.get("state"),
        "active_job": current,
        "captcha_pending": len(pending),
        "email_pending": len(email_pending),
        "next_platform": (current.get("metadata") or {}).get("platform") if current else (pending[0].get("platform") if pending else None),
        "next_url": ((current.get("human_checkpoint") or {}).get("url") if current else None),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pair-code")
    q = sub.add_parser("queue-next"); q.add_argument("--platform")
    r = sub.add_parser("resume"); r.add_argument("--platform")
    b = sub.add_parser("block"); b.add_argument("--platform", required=True); b.add_argument("--checkpoint", required=True); b.add_argument("--reason", required=True)
    p = sub.add_parser("queue-publication"); p.add_argument("--platform", required=True); p.add_argument("--kind", choices=DUAL_CAMPAIGN_KINDS + (COMBINED_CAMPAIGN_KIND,))
    e = sub.add_parser("queue-email"); e.add_argument("--platform")
    sub.add_parser("reconcile")
    sub.add_parser("status")
    args = parser.parse_args()

    if args.cmd == "pair-code":
        out = bg.create_pair_code(ACCOUNT)
    elif args.cmd == "queue-next":
        out = queue_registration(args.platform)
    elif args.cmd == "resume":
        out = resume_waiting(args.platform)
    elif args.cmd == "block":
        out = block_waiting(args.platform, args.checkpoint, args.reason)
    elif args.cmd == "queue-publication":
        out = queue_publication(args.platform, args.kind)
    elif args.cmd == "queue-email":
        out = queue_email_confirmation(args.platform)
    elif args.cmd == "reconcile":
        out = reconcile()
    else:
        out = status()
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
