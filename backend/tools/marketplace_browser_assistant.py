#!/usr/bin/env python3
"""Assisted browser runner for BORIS service marketplaces.

Safety/operational rules:
- one platform per run;
- persistent browser profile per platform;
- no CAPTCHA/SMS/2FA bypass;
- registration may fill fields but does not fabricate identity data;
- posting requires a draft with status=approved;
- publish button is clicked only with --publish;
- direct post URL can be recorded after successful publish.

This is intentionally selector-tolerant: it tries common form fields and logs
what it could/could not fill. Site-specific adapters can be added after the
first verified run on each platform.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import platform_rules
from app.services import service_marketplace as marketplace_svc

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "service_marketplaces"
DRAFTS = DATA / "drafts.json"
REGISTRATIONS = DATA / "registrations.json"
MAILBOXES = DATA / "mailboxes.json"
RUNS = DATA / "browser_runs"
SESSIONS = ROOT / "browser_sessions" / "service_marketplaces"
RUNS.mkdir(parents=True, exist_ok=True)
SESSIONS.mkdir(parents=True, exist_ok=True)

REGISTRATION_URLS = {
    "stroy_forum": "https://stroy-forum.ru/register/",
    "ssa": "https://shop.ssa.ru/index.php?do=register",
    "towerbuild": "https://forum.towerbuild.ru/register",
    "forum_baza_1c": "https://forum-baza.ru/index.php?action=signup",
    "foodmarkets": "https://foodmarkets.ru/registration",
    "partnersearch": "https://www.partnersearch.ru/business/ucp.php?mode=register",
    "finforum_services": "https://finforum.pro/register/",
    "tcfs": "https://tcfs.ru/register/",
    "kolsar_auto": "https://kolsar.info/forum/ucp.php?mode=register",
    "disc_affiliate_forum": "https://affiliate.forum/register/",
    "wmboard_services": "https://qa.wmboard.net/auth",
    "gidtalk": "https://gidtalk.ru/register.php",
    "disc_orvin_online": "https://orvin.online/index.php?do=register",
    "sashakustov_build_services": "https://sashakustov.ru/?do=register",
    "skripters": "https://top.skripters.biz/register/",
    "nulled_services": "https://nulled.cc/register/",
    "cyberforum_freelancers": "https://www.cyberforum.ru/register.php",
    "supplier_forum": "https://forum.tvoipostavshik.ru/register/",
    "wjunction_services": "https://www.wjunction.com/register/",
    "n8n_jobs": "https://community.n8n.io/signup",
    "airtable_jobs": "https://community.airtable.com/member/register",
    "bubble_jobs": "https://forum.bubble.io/signup",
    "weweb_jobs": "https://community.weweb.io/signup",
    "talkingcity_services": "https://www.talkingcity.com/register",
    "digitalpoint_services": "https://www.digitalpoint.com/register/",
    "searchengines_services": "https://searchengines.guru/ru/register",
    "zismo_programming_services": "https://zismo.biz/index.php?app=core&module=global&section=register",
    "freehostforum_webdev": "https://www.freehostforum.com/register",
    "print_forum_goods": "https://forum.print-forum.ru/register.php",
    "cnc_club_goods": "https://www.cnc-club.ru/forum/ucp.php?mode=register",
}

PLATFORM_URLS = {
    "fl": "https://www.fl.ru/",
    "workspace": "https://workspace.ru/",
    "profi": "https://profi.ru/registration/it_freelance/programmer/",
    "kwork": "https://kwork.ru/",
    "tenchat": "https://tenchat.ru/",
    "vk": "https://vk.com/",
    "stroy_forum": "https://stroy-forum.ru/forums/uslugi-organizacii-i-ispolnoteley/",
    "ssa": "https://forum.ssa.ru/",
    "towerbuild": "https://forum.towerbuild.ru/categories",
    "forum_baza_1c": "https://forum-baza.ru/",
    "bitrix_dev": "https://dev.1c-bitrix.ru/community/forums/forum14/",
    "homeidea": "https://homeidea.ru/",
    "forumrieltorov": "https://forumrieltorov.ru/viewforum.php?f=26",
    "promebelclub": "https://promebelclub.ru/forum/forumdisplay.php?f=189",
    "moigruz": "https://www.moigruz.ru/forum/",
    "rekforum_logistics": "https://rekforum.ru/viewforum.php?f=144",
    "metaprom": "https://metaprom.ru/page-production-services/",
    "foodmarkets": "https://foodmarkets.ru/forums",
    "kolsar_auto": "https://kolsar.info/forum/viewforum.php?f=88",
    "partnersearch": "https://www.partnersearch.ru/business/viewforum.php?f=22",
    "cnc_club_goods": "https://www.cnc-club.ru/forum/viewforum.php?f=163",
}

REGISTER_HINTS = {
    "fl": ["Регистрация", "Зарегистрироваться"],
    "workspace": ["Регистрация", "Создать аккаунт", "Войти"],
    "profi": [],
    "kwork": ["Регистрация", "Зарегистрироваться"],
    "tenchat": ["Регистрация", "Войти"],
    "vk": ["Зарегистрироваться", "Регистрация"],
}

def _load(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))

def _save(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

def _looks_like_existing_thread_url(value: str | None) -> bool:
    """True when URL points to an existing discussion/post, not a forum listing/create form."""
    raw = str(value or "").strip()
    if not raw:
        return False
    parsed = urlparse(raw)
    path = parsed.path.lower()
    query = {str(k).lower(): v for k, v in parse_qs(parsed.query).items()}

    if any(token in path for token in (
        "/viewtopic.php",
        "/showthread",
        "/threads/",
        "/thread/",
        "/forum-posts/",
    )):
        return True
    if "/topic/" in path and "/forum-topics/" not in path:
        return True
    if any(k in query for k in ("topic", "thread", "msg")):
        return True
    if ("viewtopic" in path or "showthread" in path) and "t" in query:
        return True
    # Legacy WR-Forum pattern: forum listing has only fid=..., a concrete topic has fid=...&id=...
    if "fid" in query and "id" in query:
        return True
    return False


def _log(platform: str, action: str, payload: dict) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    p = RUNS / f"{stamp}_{platform}_{action}.json"
    payload = {"platform": platform, "action": action, "at": datetime.now(timezone.utc).isoformat(), **payload}
    _save(p, payload)
    return p

def _wmboard_register_http(email: str, *, submit: bool) -> dict:
    """Safe email-only registration adapter for WMBoard public /auth form."""
    platform = "wmboard_services"
    start_url = REGISTRATION_URLS[platform]
    headers = {"User-Agent": "Mozilla/5.0"}
    filled: dict[str, str] = {}
    submitted = False
    checkpoint: str | None = None
    detail: str | None = None
    current_url = start_url

    try:
        session = requests.Session()
        response = session.get(start_url, headers=headers, timeout=20, allow_redirects=True)
        response.raise_for_status()
        current_url = response.url
        soup = BeautifulSoup(response.text, "html.parser")
        form = None
        for candidate in soup.find_all("form"):
            reg_marker = candidate.find("input", attrs={"name": "act", "value": "reg"})
            if reg_marker is not None:
                form = candidate
                break

        if form is None:
            checkpoint = "registration_form_not_found"
            detail = "WMBoard: не найдена публичная форма act=reg."
        else:
            form_text = " ".join(form.stripped_strings).lower()
            form_html = str(form).lower()
            if any(x in form_text or x in form_html for x in ["captcha", "капча", "turnstile", "я не робот", "killbot"]):
                checkpoint = "captcha_required"
                detail = "WMBoard registration требует антибот-проверку."
            elif form.find("input", attrs={"type": "checkbox"}) is not None or any(
                x in form_text for x in ["согласен с условиями", "соглашаюсь с условиями", "принимаю условия"]
            ):
                checkpoint = "terms_acceptance_required"
                detail = "WMBoard registration требует отдельное принятие условий."
            else:
                email_input = form.find("input", attrs={"type": "email"}) or form.find("input", attrs={"name": "email"})
                if email_input is None:
                    checkpoint = "registration_form_not_found"
                    detail = "WMBoard: в форме act=reg нет email."
                else:
                    filled = {
                        "email": "wmboard_registration_email",
                        "registration_mode": "email_only",
                    }

            if submit and checkpoint is None and filled:
                data: dict[str, str] = {}
                for field in form.find_all("input"):
                    name = str(field.get("name") or "").strip()
                    if not name:
                        continue
                    field_type = str(field.get("type") or "text").lower()
                    if field_type in {"submit", "button", "checkbox", "radio", "file"}:
                        continue
                    data[name] = str(field.get("value") or "")
                data["act"] = "reg"
                data["email"] = email
                action = urljoin(response.url, str(form.get("action") or "/auth"))
                posted = session.post(action, data=data, headers=headers, timeout=20, allow_redirects=True)
                posted.raise_for_status()
                submitted = True
                current_url = posted.url
                result_text = " ".join(BeautifulSoup(posted.text, "html.parser").stripped_strings)
                low = result_text.lower()
                if any(x in low for x in [
                    "проверьте почту",
                    "проверьте свою почту",
                    "письмо отправлено",
                    "ссылка для активации",
                    "подтвердите email",
                    "подтвердите e-mail",
                    "для завершения регистрации",
                ]):
                    checkpoint = "email_verification_required"
                    detail = result_text[:1000]
                elif any(x in low for x in [
                    "уже зарегистрирован",
                    "уже используется",
                    "такой email уже",
                    "такой e-mail уже",
                    "ошибка регистрации",
                    "регистрация по указанным данным невозможна",
                    "регистрация по этим данным невозможна",
                ]):
                    checkpoint = "registration_form_error"
                    detail = result_text[:1000]
                else:
                    checkpoint = "post_submit_review"
                    detail = result_text[:1000] or "WMBoard registration отправлена; требуется проверка результата."
    except Exception as exc:
        checkpoint = "registration_adapter_error"
        detail = f"{type(exc).__name__}: {exc}"

    if checkpoint in {"captcha_required", "email_verification_required", "terms_acceptance_required"}:
        status = "verification_required"
    elif checkpoint in {"registration_form_not_found", "registration_form_error", "registration_adapter_error"}:
        status = "in_progress"
    else:
        status = "in_progress"

    regs = _load(REGISTRATIONS, [])
    row = next((x for x in regs if x.get("platform") == platform), None)
    if row is None:
        row = {"platform": platform}
        regs.append(row)
    row.update({
        "status": status,
        "email": email,
        "checkpoint": checkpoint or "form_prepared",
        "account_url": current_url,
        "last_error": detail,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    _save(REGISTRATIONS, regs)

    log = _log(platform, "register", {
        "url": current_url,
        "filled": filled,
        "submitted": submitted,
        "checkpoint": checkpoint,
        "transport": "http",
    })
    attempt_status = "blocked" if checkpoint in {
        "captcha_required", "email_verification_required", "terms_acceptance_required", "registration_form_error",
    } else "prepared"
    marketplace_svc.record_attempt(
        platform=platform,
        action="register",
        status_value=attempt_status,
        url=current_url,
        error_code=checkpoint,
        error_detail=detail,
        meta={"submitted": submitted, "filled": filled, "transport": "http"},
    )
    return {
        "ok": True,
        "platform": platform,
        "url": current_url,
        "filled": filled,
        "submitted": submitted,
        "checkpoint": checkpoint,
        "log": str(log),
        "transport": "http",
    }

def _find_draft(draft_id: str) -> dict:
    draft = next((x for x in _load(DRAFTS, []) if x.get("id") == draft_id), None)
    if not draft:
        raise SystemExit(f"draft not found: {draft_id}")
    return draft

def _click_text(page, labels: list[str]) -> bool:
    for label in labels:
        for locator in [
            page.get_by_role("link", name=re.compile(re.escape(label), re.I)),
            page.get_by_role("button", name=re.compile(re.escape(label), re.I)),
            page.get_by_text(re.compile(re.escape(label), re.I), exact=False),
        ]:
            try:
                if locator.first.is_visible(timeout=1000):
                    locator.first.click(timeout=3000)
                    return True
            except Exception:
                pass
    return False


def _same_site_target(reference_url: str, candidate_url: str | None) -> bool:
    """Allow registration navigation only inside the same site/domain family."""
    if not candidate_url:
        return True
    try:
        reference_host=(urlparse(reference_url).hostname or "").lower().removeprefix("www.")
        resolved=urljoin(reference_url, candidate_url)
        target_host=(urlparse(resolved).hostname or "").lower().removeprefix("www.")
    except Exception:
        return False
    if not reference_host or not target_host:
        return False
    return (
        reference_host == target_host
        or reference_host.endswith("." + target_host)
        or target_host.endswith("." + reference_host)
    )


def _safe_registration_click(page, reference_url: str, labels: list[str]) -> bool:
    """Find a registration route without ever following an unrelated domain."""
    selector_candidates=[
        'a[href*="/register" i]',
        'a[href*="mode=register" i]',
        'a[href*="action=register" i]',
        'a[href*="signup" i]',
    ]
    for sel in selector_candidates:
        try:
            locator=page.locator(sel)
            count=min(locator.count(), 30)
        except Exception:
            count=0
        for idx in range(count):
            item=locator.nth(idx)
            try:
                if not item.is_visible(timeout=700):
                    continue
                href=item.get_attribute("href")
                if href and not _same_site_target(reference_url, href):
                    continue
                item.click(timeout=3000)
                page.wait_for_timeout(500)
                if _same_site_target(reference_url, page.url):
                    return True
                page.goto(reference_url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(300)
            except Exception:
                continue

    for label in labels:
        try:
            links=page.get_by_role("link", name=re.compile(re.escape(label), re.I))
            count=min(links.count(), 30)
        except Exception:
            count=0
        for idx in range(count):
            item=links.nth(idx)
            try:
                if not item.is_visible(timeout=700):
                    continue
                href=item.get_attribute("href")
                if href and not _same_site_target(reference_url, href):
                    continue
                item.click(timeout=3000)
                page.wait_for_timeout(500)
                if _same_site_target(reference_url, page.url):
                    return True
                page.goto(reference_url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(300)
            except Exception:
                continue

        try:
            buttons=page.get_by_role("button", name=re.compile(re.escape(label), re.I))
            count=min(buttons.count(), 10)
        except Exception:
            count=0
        for idx in range(count):
            item=buttons.nth(idx)
            try:
                if not item.is_visible(timeout=700):
                    continue
                item.click(timeout=3000)
                page.wait_for_timeout(500)
                if _same_site_target(reference_url, page.url):
                    return True
                page.goto(reference_url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(300)
            except Exception:
                continue
    return False


def _fill_first(page, selectors: list[str], value: str) -> str | None:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=700):
                loc.fill(value, timeout=2500)
                return sel
        except Exception:
            pass
    return None

def _checkpoint(page) -> str | None:
    text = ""
    try:
        text = page.locator("body").inner_text(timeout=3000).lower()
    except Exception:
        pass
    try:
        title = str(page.title() or "").lower()
        if title:
            text = f"{title} {text}"
    except Exception:
        pass
    captcha_dom = False
    try:
        captcha_dom = page.locator('iframe[src*="turnstile" i], [class*="turnstile" i], input[name="cf-turnstile-response"], iframe[src*="captcha" i], [class*="captcha" i], [id*="captcha" i], input[name="smart-token"], input[name="sortables_confirm_id"]').count() > 0
    except Exception:
        captcha_dom = False
    if captcha_dom or any(x in text for x in [
        "captcha", "капча", "я не робот", "i'm not a robot",
        "подтвердите, что вы не робот", "killbot user verification",
        "user verification",
    ]):
        return "captcha_required"
    if any(x in text for x in ["код из смс", "код из sms", "sms-код", "код подтверждения", "введите код"]):
        return "sms_or_verification_code_required"
    if any(x in text for x in ["подтвердите почту", "проверьте почту", "verification email", "confirm your email"]):
        return "email_verification_required"
    return None

def _context(playwright, platform: str, headless: bool):
    session_dir = SESSIONS / platform
    session_dir.mkdir(parents=True, exist_ok=True)
    return playwright.chromium.launch_persistent_context(
        str(session_dir),
        headless=headless,
        viewport={"width": 1440, "height": 1000},
        locale="ru-RU",
        timezone_id="Europe/Moscow",
    )

def _default_email() -> str:
    rows = _load(MAILBOXES, [])
    for row in rows:
        address = str(row.get("address") or "").strip()
        if address:
            return address
    raise SystemExit("registration mailbox is not configured")


import secrets

ACCOUNT_SECRETS = DATA / "account_secrets.json"

def _account_secret(platform: str, email: str) -> dict:
    """Get/create per-platform credentials without printing the password."""
    rows = _load(ACCOUNT_SECRETS, [])
    row = next((x for x in rows if x.get("platform") == platform), None)
    if row:
        return row
    base_username = {
        "forum_baza_1c": "BORIS_AI",
        "stroy_forum": "BORIS_AI",
        "foodmarkets": "BORIS_AI",
        "ssa": "BORIS_AI",
        "towerbuild": "BorisAI2026",
        "partnersearch": "BORIS_AI",
    }.get(platform, "BORIS_AI")
    # 24+ chars, mixed classes, URL/form-safe.
    password = "B!" + secrets.token_urlsafe(20) + "9a"
    row = {
        "platform": platform,
        "email": email,
        "username": base_username,
        "password": password,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    rows.append(row)
    _save(ACCOUNT_SECRETS, rows)
    try:
        os.chmod(ACCOUNT_SECRETS, 0o600)
    except Exception:
        pass
    return row

def _detect_hard_checkpoint(page) -> str | None:
    """Detect CAPTCHA/verification widgets by DOM as well as visible text."""
    selectors = [
        'input[name="cf-turnstile-response"]',
        'iframe[src*="turnstile" i]',
        '[class*="turnstile" i]',
        'input[name="smart-token"]',
        'input[name="sortables_confirm_id"]',
        'input[name="captcha_question_answer"]',
        # vBulletin 6 image verification uses humanverify[input]/[hash].
        'input[name^="humanverify" i]',
        'iframe[src*="captcha" i]',
        '[class*="captcha" i]',
        '[id*="captcha" i]',
        '[class*="smart-captcha" i]',
    ]
    for sel in selectors:
        try:
            if page.locator(sel).count():
                return "captcha_required"
        except Exception:
            pass
    return _checkpoint(page)

FREE_EMAIL_DOMAINS = {
    "gmail.com","googlemail.com","yandex.ru","yandex.com","ya.ru","mail.ru","bk.ru",
    "inbox.ru","list.ru","rambler.ru","outlook.com","hotmail.com","icloud.com",
}

def _registration_policy_checkpoint(page, email: str, account: dict) -> tuple[str | None, str | None]:
    """Block registrations that require data BORIS must not fabricate."""
    try:
        body = page.locator("body").inner_text(timeout=4000)
    except Exception:
        return None, None
    low = body.lower()
    if any(x in low for x in [
        "регистрация отключена администрацией",
        "регистрация на форуме отключена",
        "регистрация на сайте отключена",
        "администратором сайта была отключена поддержка регистрации",
        "registration has been disabled by the administrator",
        "registrations are currently disabled",
    ]):
        return (
            "registration_disabled_by_site",
            "Регистрация отключена самой площадкой. BORIS не должен ретраить такую площадку как техническую ошибку.",
        )
    # Some classic forum engines show a login form above a registration
    # agreement. Never mistake that login form for the registration form.
    try:
        age_terms = page.locator('form input[name="accept_agreement"]').count() > 0 and (
            "старше 18" in low or "регистрационное соглашение" in low
        )
    except Exception:
        age_terms = False
    if age_terms:
        return (
            "age_and_terms_declaration_required",
            "Площадка требует отдельно принять регистрационное соглашение и подтвердить возраст 18+. BORIS не делает возрастные/юридические заявления за владельца.",
        )
    # Registration can itself be the legal acceptance action even when the
    # site does not render a dedicated checkbox. Fail closed on explicit text
    # such as "continuing registration you agree..." before touching the form.
    implied_terms = any(x in low for x in [
        "продолжая регистрацию вы соглашаетесь",
        "продолжая регистрацию, вы соглашаетесь",
        "регистрируясь, вы соглашаетесь",
        "регистрируясь вы соглашаетесь",
        "by registering you agree",
        "by registering, you agree",
        "by continuing registration you agree",
    ])
    if implied_terms:
        return (
            "terms_acceptance_required",
            "Площадка считает сам факт регистрации согласием с условиями/политикой. BORIS не принимает такое юридическое согласие автоматически.",
        )

    # If the form simultaneously asks for date of birth and legal acceptance,
    # expose that whole human declaration in one checkpoint instead of first
    # reporting only the terms checkbox.
    try:
        has_birthdate_controls = (
            page.locator('input[name*="dob" i],select[name*="birth" i],select#regMonth,select#regDay,select#regYear').count() >= 2
        )
        legal_box = page.locator(
            'input[type="checkbox"][id*="agree" i],input[type="checkbox"][name*="agree" i],'
            'input[type="checkbox"][id*="terms" i],input[type="checkbox"][name*="terms" i]'
        ).first
        if has_birthdate_controls and legal_box.count():
            return (
                "age_and_terms_declaration_required",
                "Площадка требует дату рождения и отдельное принятие условий. BORIS не заявляет возраст и не принимает юридические условия автоматически.",
            )
    except Exception:
        pass

    # Generic legal/terms checkbox: do not auto-accept agreements on an
    # unfamiliar platform. Known adapters can have explicit, reviewed handling.
    try:
        checkboxes = page.locator('input[type="checkbox"]')
        for i in range(min(checkboxes.count(), 12)):
            cb = checkboxes.nth(i)
            if not cb.is_visible() or cb.is_checked():
                continue
            ident = " ".join(
                str(cb.get_attribute(a) or "") for a in ("name", "id", "value")
            ).lower()
            label_text = ""
            cb_id = str(cb.get_attribute("id") or "")
            if cb_id:
                label = page.locator(f'label[for="{cb_id}"]').first
                if label.count():
                    label_text = label.inner_text(timeout=1000)
            context = f"{ident} {label_text}".lower()
            if any(x in context for x in ["terms", "agree", "consent", "правил", "соглаш", "услов"]):
                return (
                    "terms_acceptance_required",
                    "Площадка требует отдельно принять правила/соглашение. BORIS не принимает юридические условия автоматически.",
                )
    except Exception:
        pass
    try:
        agree_submit = page.locator('input[type="submit"][name="agreed"], button[name="agreed"]').first
        if agree_submit.count() and agree_submit.is_visible():
            agree_text = " ".join(
                [
                    str(agree_submit.get_attribute("value") or ""),
                    str(agree_submit.inner_text(timeout=1000) or ""),
                    low[:1200],
                ]
            ).lower()
            if any(x in agree_text for x in ["соглашаюсь", "согласен", "услови", "правил"]):
                return (
                    "terms_acceptance_required",
                    "Площадка требует отдельным действием принять правила/условия. BORIS не делает такое согласие автоматически.",
                )
    except Exception:
        pass
    email_domain = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
    free_email_block = (
        "запрещено использование бесплатных почтовых серверов" in low
        or "бесплатных почтовых сервисов" in low
        or "корпоративн" in low and "почт" in low
    )
    identity_block = (
        "используйте пожалуйста настоящие имя и фамилию" in low
        or "настоящие имя и фамилию" in low
        or "реальные имя и фамилию" in low
        or "real name" in low
        or "this is my real name" in low
    )
    english_age_terms = (
        ("date of birth" in low or "birthday" in low)
        and ("agree to the terms" in low or "terms and privacy policy" in low)
    )
    if identity_block and english_age_terms:
        return (
            "age_and_terms_declaration_required",
            "Площадка требует реальные персональные данные, дату рождения и принятие условий. BORIS не выдумывает личность/возраст и не принимает юридические условия автоматически.",
        )
    if free_email_block and email_domain in FREE_EMAIL_DOMAINS and identity_block:
        return (
            "business_email_and_real_identity_required",
            "Площадка требует корпоративную почту и настоящие имя/фамилию. BORIS не выдумывает персональные данные и не использует бесплатную почту в обход правил.",
        )
    if free_email_block and email_domain in FREE_EMAIL_DOMAINS:
        return (
            "business_email_required",
            "Площадка запрещает бесплатные почтовые сервисы; доступная почта не подходит по правилам регистрации.",
        )
    if identity_block:
        return (
            "real_identity_required",
            "Площадка требует настоящие имя и фамилию. BORIS не выдумывает персональные данные.",
        )
    return None, None

def _fill_registration_adapter(page, platform: str, email: str, account: dict) -> dict:
    """Fill known public registration forms. Never solves CAPTCHA."""
    filled: dict[str, str] = {}
    username = str(account["username"])
    password = str(account["password"])

    if platform == "forum_baza_1c":
        page.locator('input[name="user"]').fill(username)
        page.locator('input[name="email"]').fill(email)
        page.locator('input[name="passwrd1"]').fill(password)
        page.locator('input[name="passwrd2"]').fill(password)
        for cb in page.locator('input[name="legal_soglasie"]').all():
            try:
                cb.check()
            except Exception:
                pass
        try:
            page.locator('input[name="notify_announcements"]').uncheck()
        except Exception:
            pass
        try:
            page.locator('select[name="customfield[cust_681]"]').select_option("6")
        except Exception:
            pass
        filled.update({"username": "user", "email": "email", "password": "passwrd1/passwrd2", "consent": "legal_soglasie", "profession": "Разработчик 1С"})
        return filled


    if platform == "wmboard_services":
        form = page.locator('form:has(input[name="act"][value="reg"])').first
        if form.count():
            email_input = form.locator('input[type="email"], input[name="email"]').first
            if email_input.count():
                email_input.fill(email)
                filled["email"] = "wmboard_registration_email"
                filled["registration_mode"] = "email_only"
        return filled

    if platform == "towerbuild":
        if "/register/complete" in page.url:
            email_input = page.locator('input[name="email"]').first
            if email_input.count():
                email_input.fill(email)
                filled["email"] = "email"
            data_consent = page.locator('input[name="gdpr_agree_data"]').first
            if data_consent.count() and not data_consent.is_checked():
                data_consent.check()
            if data_consent.count():
                filled["consent"] = "gdpr_agree_data"
            try:
                digest = page.locator('input[name="gdpr_agree_email"]').first
                if digest.count() and not digest.is_checked():
                    digest.check()
                if digest.count():
                    filled["email_notifications_consent"] = "gdpr_agree_email"
            except Exception:
                pass
        else:
            page.locator('input[name="username"]').fill(username)
            page.locator('input[name="password"]').fill(password)
            page.locator('input[name="password-confirm"]').fill(password)
            filled.update({"username": "username", "password": "password/password-confirm"})
        return filled

    if platform == "foodmarkets":
        page.locator('input[name="login"]').fill(username)
        page.locator('input[name="email1"]').fill(email)
        page.locator('input[name="email2"]').fill(email)
        try:
            page.locator('input[name="sex"][value="1"]').check()
        except Exception:
            pass
        try:
            page.locator('input[name="iagree"]').check()
        except Exception:
            pass
        filled.update({"username": "login", "email": "email1/email2", "consent": "iagree"})
        return filled

    if platform == "sashakustov_build_services":
        page.locator('input[name="name"]').fill(username)
        page.locator('input[name="password1"]').fill(password)
        page.locator('input[name="password2"]').fill(password)
        page.locator('input[name="email"]').fill(email)
        filled.update({
            "username": "name",
            "email": "email",
            "password": "password1/password2",
        })
        return filled

    if platform == "stroy_forum":
        # XenForo exposes decoy fields; use semantics, not generated names.
        try:
            page.locator('input[name="username"]').fill(username)
            filled["username"] = "username"
        except Exception:
            pass
        email_sel = _fill_first(page, ['input[autocomplete="email"]','input[type="email"]'], email)
        if email_sel:
            filled["email"] = email_sel
        pass_sel = _fill_first(page, ['input[autocomplete="new-password"]','input[type="password"]'], password)
        if pass_sel:
            filled["password"] = pass_sel
        try:
            page.locator('input[name="accept"]').check()
            filled["consent"] = "accept"
        except Exception:
            pass
        return filled

    # Generic fallback.
    user_sel = _fill_first(page, ['input[name*="user" i]','input[name*="login" i]','input[autocomplete="username"]'], username)
    if user_sel:
        filled["username"] = user_sel
    email_sel = _fill_first(page, ['input[type="email"]','input[name*="email" i]','input[autocomplete="email"]','input[placeholder*="mail" i]','input[placeholder*="почт" i]'], email)
    if email_sel:
        filled["email"] = email_sel
    pass_fields = page.locator('input[type="password"]')
    if pass_fields.count():
        for i in range(min(pass_fields.count(), 2)):
            try:
                pass_fields.nth(i).fill(password)
            except Exception:
                pass
        filled["password"] = f"{min(pass_fields.count(),2)} fields"
    return filled


def _platform_start_url(platform: str) -> str:
    if platform in PLATFORM_URLS:
        return PLATFORM_URLS[platform]
    # Dynamic forum discovery often captures the exact registration URL.
    # Prefer it over the commercial section URL; otherwise preflight can
    # falsely conclude that no registration form exists.
    if str(platform).startswith("disc_"):
        try:
            from app.services import forum_discovery
            candidate = next(
                (x for x in forum_discovery.list_candidates(min_score=0)
                 if str(x.get("key") or "") == str(platform)),
                None,
            )
            register_url = str((candidate or {}).get("register_url") or "").strip()
            if register_url:
                return register_url
        except Exception:
            pass
    p = marketplace_svc.get_platform(platform)
    if p and p.url:
        return p.url
    raise SystemExit(f"browser adapter URL is not configured for {platform}")


def _registration_result(page) -> tuple[str | None, str | None]:
    """Classify post-submit registration result without guessing from generic page links."""
    hard = _detect_hard_checkpoint(page)
    if hard:
        return hard, "Площадка требует CAPTCHA/код подтверждения."
    try:
        body = page.locator("body").inner_text(timeout=5000)
    except Exception:
        return None, None
    low = body.lower()
    if "sashakustov.ru" in page.url and "ошибка регистрации" in low:
        return (
            "registration_rejected_by_site",
            "Площадка отклонила регистрацию после отправки корректно заполненной формы.",
        )
    if page.url.startswith("https://forum.towerbuild.ru/register"):
        messages=[]
        for sel in ["#register-error-notify p","#username-notify","#password-notify","#password-confirm-notify"]:
            try:
                loc=page.locator(sel)
                if loc.count():
                    msg=" ".join(loc.first.inner_text().split()).strip()
                    if msg:
                        messages.append(msg)
            except Exception:
                pass
        if messages:
            return "registration_form_error", " | ".join(messages)[:1000]
    error_phrases = [
        "следующие ошибки были обнаружены",
        "ошибки были обнаружены",
        "исправьте следующие ошибки",
        "имя пользователя уже",
        "такой пользователь уже",
        "недопустимое имя пользователя",
        "неверно заполнено",
    ]
    activation_phrases = [
        "письмо для активации отправлено",
        "ссылка для активации отправлена",
        "проверьте вашу почту",
        "проверьте свою почту",
        "завершите регистрацию по ссылке",
        "для завершения регистрации",
    ]
    success_phrases = [
        "регистрация завершена",
        "вы успешно зарегистрированы",
        "учётная запись создана",
        "учетная запись создана",
    ]
    diag = [
        x.strip() for x in body.splitlines()
        if any(w in x.lower() for w in ["ошиб", "пользоват", "регистрац", "активац", "почт"])
    ][:8]
    diagnostic = " | ".join(diag)[:1000]
    authenticated = False
    try:
        authenticated = (
            page.locator('a[href*="/logout"],a[href*="action=logout"]').count() > 0
            or '"loggedIn":true' in page.content()
            or '"loggedIn": true' in page.content()
        )
    except Exception:
        authenticated = False
    if authenticated:
        return "registered", diagnostic or "После регистрации подтверждена авторизованная сессия."
    if any(x in low for x in error_phrases):
        return "registration_form_error", diagnostic or "Форма регистрации вернула ошибку."
    if any(x in low for x in activation_phrases):
        return "email_verification_required", diagnostic or "Нужно подтвердить регистрацию по почте."
    if any(x in low for x in success_phrases):
        return "registered", diagnostic or "Регистрация завершена."
    return None, diagnostic or None

# CAPTCHA_HUMAN_CHECKPOINT_FAIL_CLOSED_V1
# CAPTCHA is detected automatically but never bypassed. It becomes an explicit
# one-time platform onboarding checkpoint; after completion BORIS re-verifies
# the account and resumes the normal READY/WARMING flow.
def _registration_http_fallback(platform: str, start_url: str) -> dict | None:
    """Read-only fallback when a registration page is too slow for Playwright.

    It only classifies the page. It never submits registration, accepts terms,
    or attempts to solve/bypass anti-bot challenges.
    """
    try:
        headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "ru-RU,ru;q=0.9"}
        if platform == "searchengines_services":
            # Searchengines serves /ru/register only after its public forum
            # endpoint has issued a first-party session cookie. Bootstrap that
            # cookie read-only; never submit the registration form here.
            session = requests.Session()
            session.headers.update(headers)
            session.get("https://searchengines.guru/ru/forum", timeout=20, allow_redirects=True).raise_for_status()
            response = session.get(
                start_url,
                headers={"Referer": "https://searchengines.guru/ru/forum"},
                timeout=20,
                allow_redirects=True,
            )
        else:
            response = requests.get(
                start_url,
                headers=headers,
                timeout=20,
                allow_redirects=True,
            )
        response.raise_for_status()
    except Exception:
        return None

    soup = BeautifulSoup(response.text, "html.parser")
    text = " ".join(soup.stripped_strings).lower()
    html = response.text.lower()
    captcha_markers = [
        "g-recaptcha", "recaptcha", "hcaptcha", "turnstile",
        "humanverify", "captcha", "я не робот", "антибот",
    ]
    checkpoint = None
    detail = None
    if any(marker in html or marker in text for marker in captcha_markers):
        checkpoint = "captcha_required"
        detail = "Регистрационная страница доступна по HTTP и содержит антибот-проверку."
    else:
        checkboxes = soup.find_all("input", attrs={"type": "checkbox"})
        terms_markers = [
            "согласен с правилами", "соглашаюсь с правилами",
            "принимаю правила", "согласен с условиями",
            "terms and rules", "terms of service",
        ]
        implied_terms_markers = [
            "регистрируясь, вы соглашаетесь",
            "регистрируясь вы соглашаетесь",
            "by registering, you agree",
            "by registering you agree",
        ]
        if (checkboxes and any(marker in text for marker in terms_markers)) or any(marker in text for marker in implied_terms_markers):
            checkpoint = "terms_acceptance_required"
            detail = "Регистрация сама означает принятие правил/условий; BORIS не делает юридическое согласие автоматически."
        elif soup.find("form") is None:
            checkpoint = "registration_form_not_found"
            detail = "HTTP fallback не нашёл регистрационную форму."
        else:
            checkpoint = "form_prepared"
            detail = "Регистрационная форма доступна; браузерный preflight можно повторить позже."

    return {
        "platform": platform,
        "url": response.url,
        "checkpoint": checkpoint,
        "detail": detail,
    }


def _registration_status_for_checkpoint(checkpoint: str | None) -> str:
    if checkpoint in {
        "business_email_required", "real_identity_required",
        "business_email_and_real_identity_required",
        "registration_disabled_by_site", "registration_rejected_by_site",
        "registration_route_unavailable",
    }:
        return "blocked"
    if checkpoint in {
        "captcha_required", "captcha_age_and_terms_required", "sms_or_verification_code_required",
        "email_verification_required", "age_and_terms_declaration_required",
        "terms_acceptance_required", "registration_agreement_required",
        "external_account_sso_required",
    }:
        return "verification_required"
    if checkpoint in marketplace_svc.WARMING_REGISTRATION_CHECKPOINTS:
        return "warming"
    return "in_progress"


def register(platform: str, email: str | None, *, headless: bool, submit: bool) -> dict:
    email = (email or "").strip() or _default_email()
    policy = marketplace_svc.platform_policy(platform)
    if not policy.get("free"):
        reason = str(policy.get("reason") or "Площадка не разрешена бесплатной политикой.")
        try:
            marketplace_svc.upsert_registration(platform, "blocked", email=email, checkpoint="free_only_policy", last_error=reason)
        except Exception:
            pass
        marketplace_svc.record_attempt(platform=platform, action="register", status_value="blocked", error_code="free_only_policy", error_detail=reason)
        raise SystemExit(f"registration blocked by free-only policy: {reason}")
    if platform == "wmboard_services":
        return _wmboard_register_http(email, submit=submit)
    start_url = REGISTRATION_URLS.get(platform) or _platform_start_url(platform)
    if platform == "searchengines_services":
        fallback = _registration_http_fallback(platform, start_url)
        if fallback is not None:
            checkpoint = str(fallback.get("checkpoint") or "registration_form_not_found")
            status = _registration_status_for_checkpoint(checkpoint)
            detail = str(fallback.get("detail") or "")
            current_url = str(fallback.get("url") or start_url)
            marketplace_svc.upsert_registration(
                platform, status, email=email, account_url=current_url,
                checkpoint=checkpoint, last_error=detail,
            )
            marketplace_svc.record_attempt(
                platform=platform, action="register",
                status_value="blocked" if status in {"blocked", "verification_required"} else "prepared",
                url=current_url, error_code=checkpoint, error_detail=detail,
                meta={"submitted": False, "filled": {}, "http_fallback": True},
            )
            return {
                "ok": True, "platform": platform, "url": current_url,
                "filled": {}, "submitted": False, "checkpoint": checkpoint,
                "http_fallback": True,
            }
    account = _account_secret(platform, email)
    with sync_playwright() as p:
        ctx = _context(p, platform, headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(start_url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(1500)
        except PlaywrightTimeoutError:
            fallback = _registration_http_fallback(platform, start_url)
            ctx.close()
            if fallback is None:
                raise
            checkpoint = str(fallback.get("checkpoint") or "registration_form_not_found")
            status = _registration_status_for_checkpoint(checkpoint)
            detail = str(fallback.get("detail") or "")
            current_url = str(fallback.get("url") or start_url)
            marketplace_svc.upsert_registration(
                platform,
                status,
                email=email,
                account_url=current_url,
                checkpoint=checkpoint,
                last_error=detail,
            )
            marketplace_svc.record_attempt(
                platform=platform,
                action="register",
                status_value="blocked" if status in {"blocked", "verification_required"} else "prepared",
                url=current_url,
                error_code=checkpoint,
                error_detail=detail,
                meta={"submitted": False, "filled": {}, "http_fallback": True},
            )
            return {
                "ok": True,
                "platform": platform,
                "url": current_url,
                "filled": {},
                "submitted": False,
                "checkpoint": checkpoint,
                "http_fallback": True,
            }
        register_labels = REGISTER_HINTS.get(platform) or ["Регистрация","Зарегистрироваться","Регистрация пользователя","Создать аккаунт","Войти"]
        if platform == "partnersearch":
            try:
                agree = page.locator('input[name="agreed"]').first
                if agree.count():
                    agree.click(timeout=4000)
                    page.wait_for_timeout(900)
            except Exception:
                pass
        elif platform not in REGISTRATION_URLS:
            # Prefer an explicit registration URL over visible text. Forum
            # templates often hide/duplicate the same "Регистрация" label.
            clicked = False
            for sel in [
                'a[href*="/register" i]',
                'a[href*="mode=register" i]',
                'a[href*="action=register" i]',
                'a[href*="signup" i]',
            ]:
                try:
                    loc = page.locator(sel).first
                    if loc.count() and loc.is_visible():
                        loc.click(timeout=4000)
                        clicked = True
                        break
                except Exception:
                    pass
            if not clicked:
                _click_text(page, register_labels)
            page.wait_for_timeout(1200)

        hard_checkpoint = _detect_hard_checkpoint(page)
        policy_checkpoint, policy_detail = _registration_policy_checkpoint(page, email, account)

        # Some communities delegate account creation to the vendor identity
        # system instead of exposing a local registration form. Treat that as
        # a one-time external account/SSO checkpoint rather than an adapter
        # failure, so it remains visible in the reusable onboarding queue.
        if not hard_checkpoint and platform == "airtable_jobs":
            body_low = ""
            try:
                body_low = page.locator("body").inner_text(timeout=3000).lower()
            except Exception:
                pass
            if "log in with airtable sso" in body_low or "create an account" in body_low:
                policy_checkpoint = "external_account_sso_required"
                policy_detail = (
                    "Airtable Community requires an Airtable/community account or SSO. "
                    "BORIS does not create or authorize an external identity automatically."
                )
        if not hard_checkpoint and platform == "bubble_jobs":
            host = urlparse(page.url).netloc.lower()
            if host.endswith("bubble.io") and not host.startswith("forum."):
                policy_checkpoint = "external_account_sso_required"
                policy_detail = (
                    "Bubble Forum registration is delegated to the main Bubble account. "
                    "BORIS does not create or authorize that external account automatically."
                )
        if hard_checkpoint or policy_checkpoint:
            filled = {}
        else:
            try:
                filled = _fill_registration_adapter(page, platform, email, account)
            except Exception as exc:
                filled = {}
                marketplace_svc.record_attempt(
                    platform=platform,
                    action="register",
                    status_value="failed",
                    error_code="registration_adapter_error",
                    error_detail=f"{type(exc).__name__}: {exc}",
                )

        checkpoint = hard_checkpoint or _detect_hard_checkpoint(page) or policy_checkpoint
        if checkpoint == "captcha_required" and policy_checkpoint == "age_and_terms_declaration_required":
            checkpoint = "captcha_age_and_terms_required"
            policy_detail = (
                "Площадка требует одним регистрационным шагом CAPTCHA, дату рождения "
                "и отдельное согласие с условиями. BORIS не решает CAPTCHA, "
                "не заявляет возраст и не принимает юридические условия автоматически."
            )
        email_filled = bool(filled.get("email"))
        credentials_ready = bool(filled.get("username")) and bool(filled.get("password"))
        email_fields_present = page.locator('input[type="email"],input[name*="email" i]').count() > 0
        form_ready = (
            email_filled
            or (credentials_ready and not email_fields_present)
            or (platform == "towerbuild" and (bool(filled.get("consent")) or credentials_ready))
        )
        if not form_ready and not checkpoint:
            checkpoint = "registration_form_not_found"
        screenshot = RUNS / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{platform}_register.png"
        screenshot_error = None
        try:
            page.screenshot(path=str(screenshot), full_page=True, timeout=7000)
        except Exception as exc:
            screenshot_error = f"{type(exc).__name__}: {exc}"
            # Evidence collection must never block the registration workflow.

        submitted = False
        result_detail = policy_detail
        if submit and not checkpoint and form_ready:
            if platform == "forum_baza_1c":
                try:
                    # Forum has a legitimate anti-bot dwell-time requirement.
                    # Respect it instead of trying to bypass it.
                    page.wait_for_timeout(65000)
                    page.locator('form#registration input[name="regSubmit"]').click(timeout=4000)
                    submitted = True
                except Exception:
                    submitted = False
            elif platform == "wmboard_services":
                try:
                    form = page.locator('form:has(input[name="act"][value="reg"])').first
                    submit_button = form.locator('button[type="submit"], input[type="submit"]').last
                    if form.count() and submit_button.count():
                        submit_button.click(timeout=4000)
                        submitted = True
                except Exception:
                    submitted = False
            elif platform == "sashakustov_build_services":
                try:
                    form = page.locator('form#registration').first
                    submit_button = form.locator('button[type="submit"][name="submit"]').first
                    if form.count() and submit_button.count():
                        submit_button.click(timeout=4000)
                        submitted = True
                except Exception:
                    submitted = False
            elif platform == "towerbuild":
                try:
                    if "/register/complete" in page.url:
                        page.locator('form[action="/register/complete"] button.btn-primary').first.click(timeout=4000)
                        submitted = True
                        page.wait_for_timeout(1800)
                    else:
                        page.locator('form[action="/register"] button#register').click(timeout=4000)
                        submitted = True
                        page.wait_for_timeout(1200)
                        if "/register/complete" in page.url:
                            email_input = page.locator('form[action="/register/complete"] input[name="email"]').first
                            data_consent = page.locator('form[action="/register/complete"] input[name="gdpr_agree_data"]').first
                            if email_input.count() and data_consent.count():
                                email_input.fill(email)
                                if not data_consent.is_checked():
                                    data_consent.check()
                                try:
                                    digest = page.locator('form[action="/register/complete"] input[name="gdpr_agree_email"]').first
                                    if digest.count() and not digest.is_checked():
                                        digest.check()
                                except Exception:
                                    pass
                                page.locator('form[action="/register/complete"] button.btn-primary').first.click(timeout=4000)
                                page.wait_for_timeout(1800)
                except Exception:
                    submitted = False
            else:
                submitted = _click_text(page, ["Зарегистрироваться","Регистрация","Создать аккаунт","Продолжить","Далее"])
            if submitted:
                page.wait_for_timeout(2200)
                checkpoint, result_detail = _registration_result(page)

        current_url = page.url
        ctx.close()

    # Technical/adaptor failures and one-time human checkpoints are
    # classified in one place so browser and HTTP-fallback paths cannot drift.
    status = _registration_status_for_checkpoint(checkpoint)
    regs = _load(REGISTRATIONS, [])
    row = next((x for x in regs if x.get("platform") == platform), None)
    if row is None:
        row = {"platform": platform}
        regs.append(row)
    row.update({
        "status": status,
        "email": email,
        "checkpoint": ("login_verification_required" if checkpoint == "registered" else checkpoint) or ("form_prepared" if not submit else "post_submit_review"),
        "account_url": current_url,
        "last_error": result_detail or ("Не найдена регистрационная форма; нужен адаптер площадки." if checkpoint == "registration_form_not_found" else None),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    _save(REGISTRATIONS, regs)
    log = _log(platform, "register", {"url": current_url, "filled": filled, "submitted": submitted, "checkpoint": checkpoint, "screenshot": str(screenshot)})
    attempt_status = "blocked" if checkpoint in {
        "registration_form_not_found","registration_form_error","captcha_required",
        "sms_or_verification_code_required","email_verification_required",
        "age_and_terms_declaration_required","terms_acceptance_required",
        "external_account_sso_required","registration_disabled_by_site",
    } else "prepared"
    marketplace_svc.record_attempt(platform=platform, action="register", status_value=attempt_status, url=current_url, error_code=checkpoint, error_detail=result_detail or ("Требуется ручное подтверждение регистрации." if checkpoint in {"captcha_required","sms_or_verification_code_required","email_verification_required"} else None), screenshot=str(screenshot), meta={"submitted": submitted, "filled": filled})
    return {"ok": True, "platform": platform, "url": current_url, "filled": filled, "submitted": submitted, "checkpoint": checkpoint, "log": str(log), "screenshot": str(screenshot)}


LOGIN_URLS = {
    "forum_baza_1c": "https://forum-baza.ru/index.php?action=login",
    "towerbuild": "https://forum.towerbuild.ru/login",
    "nulled_services": "https://nulled.cc/login/",
    "cyberforum_freelancers": "https://www.cyberforum.ru/",
    "supplier_forum": "https://forum.tvoipostavshik.ru/login/",
    "wjunction_services": "https://www.wjunction.com/login/",
    "digitalpoint_services": "https://forums.digitalpoint.com/login/",
    "namepros_promotional": "https://www.namepros.com/login/",
    "n8n_jobs": "https://community.n8n.io/login",
    "airtable_jobs": "https://community.airtable.com/member/login",
    "bubble_jobs": "https://forum.bubble.io/login",
    "weweb_jobs": "https://community.weweb.io/login",
    "talkingcity_services": "https://www.talkingcity.com/forum/the-marketplace/marketplace-buy-sell-or-barter/services",
    "freehostforum_webdev": "https://www.freehostforum.com/",
    "print_forum_goods": "https://forum.print-forum.ru/login.php?do=login",
}

POST_URLS = {
    "forum_baza_1c": "https://forum-baza.ru/index.php?action=post;board=62.0",
    "disc_pspx_ru": "https://www.pspx.ru/forum/newthread.php?do=newthread&f=74",
    "searchengines_services": "https://searchengines.guru/ru/forum/webmasters-jobs/programming?do=add",
    "zismo_programming_services": "https://zismo.biz/index.php?app=forums&module=post&section=post&do=new_post&f=92",
}

POST_LOGIN_READINESS_URLS = {
    "nulled_services": "https://nulled.cc/forums/kommerciya-i-reklama-uslug.198/post-thread",
    "cyberforum_freelancers": "https://www.cyberforum.ru/newthread.php?do=newthread&f=258",
    "wjunction_services": "https://www.wjunction.com/forums/services.112/post-thread",
    "digitalpoint_services": "https://forums.digitalpoint.com/forums/services.60/create-thread",
    "namepros_promotional": "https://www.namepros.com/forums/promotional.15/post-thread",
    "n8n_jobs": "https://community.n8n.io/new-topic?category=jobs/13",
    "airtable_jobs": "https://community.airtable.com/topic/new",
    "bubble_jobs": "https://forum.bubble.io/new-topic?category=jobs-freelance/13",
    "weweb_jobs": "https://community.weweb.io/new-topic?category=jobs/22",
    "talkingcity_services": "https://www.talkingcity.com/forum/the-marketplace/marketplace-buy-sell-or-barter/services",
    "freehostforum_webdev": "https://www.freehostforum.com/forum/advertising-forums/webmaster-marketplace/web-development-offers-and-requests",
    "print_forum_goods": "https://forum.print-forum.ru/newthread.php?do=newthread&f=22",
    "cnc_club_goods": "https://www.cnc-club.ru/forum/posting.php?mode=post&f=163",
}


def _post_login_readiness_url(platform: str) -> str | None:
    direct = POST_LOGIN_READINESS_URLS.get(platform)
    if direct:
        return direct
    try:
        for row in marketplace_svc.list_platforms():
            if str(row.get("key") or "") != platform:
                continue
            for surface in row.get("publication_surfaces") or []:
                candidate = str(surface.get("post_url") or surface.get("url") or "").strip()
                if candidate:
                    return candidate
            candidate = str(row.get("url") or "").strip()
            return candidate or None
    except Exception:
        return None
    return None


def _classify_post_login_readiness(platform: str, http_status: int, body: str) -> tuple[str, str | None, str | None]:
    low = str(body or "").lower()
    if platform == "nulled_services":
        blocked_hints = [
            "недостаточно прав",
            "у вас нет прав",
            "нет прав для",
            "нужно сначала войти",
            "нужно сначала войти на форум",
            "уровень доступа",
            "level 0",
            "level 1",
            "level 2",
        ]
        if http_status in {401, 403} or any(x in low for x in blocked_hints):
            return (
                "warming",
                "participation_level_required",
                "Nulled: аккаунт существует, но бесплатная новая рекламная тема ещё недоступна. Требуется Level 3; BORIS перепроверит позже.",
            )
    if platform == "cyberforum_freelancers":
        blocked_hints = [
            "недостаточно прав",
            "не имеете доступа",
            "у вас нет прав",
            "доступ в данный раздел ограничен",
            "членство в группах",
            "группы 4",
        ]
        if http_status in {401, 403} or any(x in low for x in blocked_hints):
            return (
                "warming",
                "freelance_group_required",
                "CyberForum: аккаунт существует, но раздел предложений фрилансеров ещё недоступен. Нужна группа 4+ и заявка в группу Фриланса; BORIS перепроверит позже.",
            )
    if platform == "print_forum_goods":
        blocked_hints = [
            "у вас недостаточно прав",
            "не имеете доступа к этой странице",
            "для включения в эту группу",
            "статус \"полиграфист\"",
            "статус «полиграфист»",
        ]
        if http_status in {401, 403} or any(x in low for x in blocked_hints):
            return (
                "warming",
                "membership_group_required",
                "Принт-Форум: аккаунт существует, но торговая площадка ещё не открыта. Нужно один раз заполнить профиль и подать бесплатную заявку в группу «Полиграфист»; BORIS перепроверит позже.",
            )
        post_form_hints = [
            "создать новую тему",
            "заголовок темы",
            "название темы",
            "опубликовать новую тему",
        ]
        if any(x in low for x in post_form_hints):
            return ("ready", None, None)
        return (
            "in_progress",
            "posting_permission_unverified",
            "Принт-Форум: вход подтверждён, но BORIS пока не увидел форму создания товарной темы.",
        )
    if platform == "wjunction_services":
        denied_hints = [
            "you do not have permission",
            "you must be logged in",
            "you must log in",
            "insufficient privileges",
            "no permission to post",
        ]
        if http_status in {401, 403} or any(x in low for x in denied_hints):
            return (
                "in_progress",
                "posting_permission_required",
                "WJunction: вход подтверждён, но форма публикации Services пока недоступна. BORIS не публикует до явного подтверждения права создания темы.",
            )
        post_form_hints = [
            "post thread",
            "thread title",
            "create thread",
        ]
        if any(x in low for x in post_form_hints):
            return ("ready", None, None)
        return (
            "in_progress",
            "posting_permission_unverified",
            "WJunction: не удалось однозначно подтвердить форму создания темы Services после входа.",
        )
    if platform == "digitalpoint_services":
        established_hints = [
            "established member",
            "established members",
            "you have insufficient privileges to post threads here",
            "insufficient privileges to post",
        ]
        if any(x in low for x in established_hints):
            return (
                "warming",
                "established_member_required",
                "DigitalPoint: аккаунт существует, но бесплатный Services marketplace ещё не открыт. Нужны 48 часов после регистрации и 3 естественных лайка от разных Established Members; BORIS перепроверит позже.",
            )
        denied_hints = [
            "you do not have permission",
            "you must be logged in",
            "you must log in",
            "no permission to post",
        ]
        if http_status in {401, 403} or any(x in low for x in denied_hints):
            return (
                "in_progress",
                "posting_permission_required",
                "DigitalPoint: право создания темы Services после входа пока не подтверждено.",
            )
        if any(x in low for x in ["post thread", "thread title", "create thread"]):
            return ("ready", None, None)
        return (
            "in_progress",
            "posting_permission_unverified",
            "DigitalPoint: BORIS не смог однозначно подтвердить форму создания темы Services.",
        )
    if platform == "namepros_promotional":
        denied_hints = [
            "you have insufficient privileges to post threads here",
            "insufficient privileges",
            "you do not have permission",
            "you must be logged in",
            "you must log in",
        ]
        if http_status in {401, 403} or any(x in low for x in denied_hints):
            return (
                "in_progress",
                "posting_permission_required",
                "NamePros: бесплатный Promotional-раздел подтверждён правилами, но право создания темы для этого аккаунта пока не подтверждено.",
            )
        if any(x in low for x in ["post thread", "thread title", "create thread"]):
            return ("ready", None, None)
        return (
            "in_progress",
            "posting_permission_unverified",
            "NamePros: BORIS не смог однозначно подтвердить форму создания темы Promotional.",
        )
    if platform == "talkingcity_services":
        denied_hints = [
            "you must be logged in",
            "please log in",
            "permission to post",
            "not authorized",
            "not authorised",
        ]
        if http_status in {401, 403} or any(x in low for x in denied_hints):
            return (
                "in_progress",
                "posting_permission_required",
                "TalkingCity: раздел Services доступен, но право создания темы для этого аккаунта пока не подтверждено.",
            )
        # Do not match generic filter text such as "New Topics On/Off" on the
        # public page. Only explicit authoring calls count as posting evidence.
        authoring_hints = [
            "post new topic",
            "start new topic",
            "start a new topic",
            "create new topic",
            "create a new topic",
        ]
        if any(x in low for x in authoring_hints):
            return ("ready", None, None)
        return (
            "in_progress",
            "posting_permission_unverified",
            "TalkingCity: вход подтверждён, но BORIS не увидел явное действие создания темы в Services; публикация остаётся fail-closed.",
        )
    if platform == "freehostforum_webdev":
        denied_hints = [
            "please log in to your account",
            "you must be logged in",
            "login or sign up",
            "permission to post",
            "not authorized",
            "not authorised",
        ]
        if http_status in {401, 403} or any(x in low for x in denied_hints):
            return (
                "in_progress",
                "posting_permission_required",
                "FreeHostForum: раздел Web Development доступен, но право создания темы для аккаунта пока не подтверждено.",
            )
        authoring_hints = [
            "post new topic",
            "start new topic",
            "start a new topic",
            "create new topic",
            "create a new topic",
        ]
        if any(x in low for x in authoring_hints):
            return ("ready", None, None)
        return (
            "in_progress",
            "posting_permission_unverified",
            "FreeHostForum: вход подтверждён, но BORIS не увидел явное действие создания темы в Web Development; публикация остаётся fail-closed.",
        )

    # GENERIC_POST_PERMISSION_FAIL_CLOSED_V1
    # Successful login is not publication permission. For every other forum,
    # require explicit authoring evidence on the verified commercial surface.
    denied_hints = [
        "недостаточно прав",
        "нет прав",
        "не имеете доступа",
        "войдите или зарегистрируйтесь",
        "необходимо войти",
        "you must be logged in",
        "please log in",
        "insufficient privileges",
        "you do not have permission",
        "no permission to post",
        "not authorized",
        "not authorised",
    ]
    if http_status in {401, 403} or any(x in low for x in denied_hints):
        return (
            "in_progress",
            "posting_permission_required",
            "Вход подтверждён, но площадка не подтвердила право создания темы в разрешённом разделе.",
        )
    authoring_hints = [
        "создать новую тему",
        "создать тему",
        "новая тема",
        "начать новую тему",
        "заголовок темы",
        "название темы",
        "отправить тему",
        "post new topic",
        "post thread",
        "start new topic",
        "start a new topic",
        "create new topic",
        "create a new topic",
        "create thread",
        "thread title",
    ]
    if any(x in low for x in authoring_hints):
        return ("ready", None, None)
    return (
        "in_progress",
        "posting_permission_unverified",
        "Вход подтверждён, но BORIS не увидел явную форму/действие создания темы; публикация остаётся fail-closed.",
    )

def _registration_was_submitted(platform: str | None) -> bool:
    if not platform:
        return False
    try:
        attempts = marketplace_svc.list_attempts(limit=5000)
    except Exception:
        return False
    for row in reversed(attempts):
        if row.get("platform") != platform or row.get("action") != "register":
            continue
        meta = row.get("meta") or {}
        if bool(meta.get("submitted")):
            return True
    return False


def _preserve_pending_bootstrap_checkpoint(
    previous_checkpoint: str,
    previous_error: str | None,
    status: str,
    checkpoint: str | None,
    error: str | None,
    *,
    platform: str | None = None,
) -> tuple[str, str | None, str | None]:
    """A login probe must preserve a real bootstrap step, but self-heal false email states.

    Historically a generic phpBB link named "Повторно выслать письмо для активации"
    was enough to misclassify an account that had never been submitted as
    email_verification_required. If there is no successful registration-submit
    evidence, do not keep that false checkpoint forever.
    """
    if (
        previous_checkpoint == "email_verification_required"
        and checkpoint in {"login_failed", "login_result_unclear"}
        and platform
        and not _registration_was_submitted(platform)
    ):
        return (
            "not_registered",
            "registration_not_submitted",
            error or "Регистрация не была отправлена; BORIS вернёт площадку в безопасный preflight.",
        )
    if (
        previous_checkpoint in marketplace_svc.HUMAN_PLATFORM_BOOTSTRAP_CHECKPOINTS
        and status != "ready"
        and checkpoint in {"login_failed", "login_result_unclear"}
    ):
        return (
            "verification_required",
            previous_checkpoint,
            previous_error or error or "Одноразовая подготовка площадки ещё не подтверждена.",
        )
    return status, checkpoint, error

# VERIFY_REGISTRATION_TIMEOUT_FAILCLOSED_V1:
# A slow forum page must not turn a known CAPTCHA/terms checkpoint into a
# traceback. Preserve the external checkpoint and retry later; never infer
# that an account is ready from a failed browser navigation.
def _verify_timeout_state(previous_checkpoint: str, previous_error: str | None) -> tuple[str, str, str]:
    if previous_checkpoint in marketplace_svc.HUMAN_PLATFORM_BOOTSTRAP_CHECKPOINTS:
        return (
            "verification_required",
            previous_checkpoint,
            previous_error or "Площадка отвечает медленно; одноразовая проверка ещё не завершена.",
        )
    return (
        "in_progress",
        "login_probe_timeout",
        "Площадка отвечает медленно; BORIS повторит безопасную проверку входа позже.",
    )

def verify_registration(platform: str, *, headless: bool = True) -> dict:
    """Verify account login using server-side secret storage; never prints password."""
    rows = _load(ACCOUNT_SECRETS, [])
    account = next((x for x in rows if x.get("platform") == platform), None)
    if not account:
        raise SystemExit("account secret is not prepared")
    reg_rows = _load(REGISTRATIONS, [])
    existing_reg = next((x for x in reg_rows if x.get("platform") == platform), None)
    previous_checkpoint = str((existing_reg or {}).get("checkpoint") or "")
    previous_error = (existing_reg or {}).get("last_error")
    previous_account_url = (existing_reg or {}).get("account_url")
    login_url = LOGIN_URLS.get(platform) or _platform_start_url(platform)

    with sync_playwright() as pw:
        ctx = _context(pw, platform, headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(login_url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(700)
        except PlaywrightTimeoutError:
            status, checkpoint, error = _verify_timeout_state(previous_checkpoint, previous_error)
            now_iso = datetime.now(timezone.utc).isoformat()
            row = existing_reg
            if row is None:
                row = {"platform": platform}
                reg_rows.append(row)
            row.update({
                "status": status,
                "checkpoint": checkpoint,
                "account_url": previous_account_url or login_url,
                "last_error": error,
                "updated_at": now_iso,
            })
            _save(REGISTRATIONS, reg_rows)
            marketplace_svc.record_attempt(
                platform=platform,
                action="verify_registration",
                status_value="blocked" if status == "verification_required" else "prepared",
                url=None,
                error_code=checkpoint,
                error_detail=error,
                meta={"submitted": False, "timeout_failclosed": True},
            )
            ctx.close()
            return {
                "ok": False,
                "platform": platform,
                "status": status,
                "checkpoint": checkpoint,
                "url": previous_account_url or login_url,
                "error": error,
                "timeout_failclosed": True,
            }

        submitted = False
        if platform == "forum_baza_1c":
            user = page.locator('input[name="user"],input[name="username"]').first
            pwd = page.locator('input[name="passwrd"],input[type="password"]').first
            if user.count() and pwd.count():
                user.fill(str(account["username"]))
                pwd.fill(str(account["password"]))
                submit = page.locator('input[type="submit"],button[type="submit"]').filter(has_text=re.compile("вход|войти", re.I)).first
                if not submit.count():
                    submit = page.locator('form input[type="submit"],form button[type="submit"]').first
                submit.click(timeout=4000)
                submitted = True
                page.wait_for_timeout(1800)
        elif platform == "towerbuild":
            def _tower_login(identity: str) -> bool:
                form = page.locator('form#login-form').first
                user = form.locator('input[name="username"]').first
                pwd = form.locator('input[name="password"]').first
                if not (user.count() and pwd.count()):
                    return False
                user.fill(identity)
                pwd.fill(str(account["password"]))
                form.locator('button[type="submit"]').first.click(timeout=4000)
                page.wait_for_timeout(1400)
                return True
            submitted = _tower_login(str(account["username"]))
            # The form explicitly accepts "Имя пользователя / Email". If username
            # login leaves us on the login page, retry once with the registration mailbox.
            if submitted and "/login" in page.url:
                page.goto(login_url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(400)
                submitted = _tower_login(str(account.get("email") or ""))
        else:
            def _generic_login(identity: str) -> bool:
                pwd = page.locator('input[type="password"]').first
                if not pwd.count():
                    _click_text(page, ["Войти","Вход","Авторизация","Login","Sign in"])
                    page.wait_for_timeout(700)
                    pwd = page.locator('input[type="password"]').first
                if not pwd.count():
                    return False
                form = pwd.locator("xpath=ancestor::form[1]")
                if not form.count():
                    return False
                user = None
                for sel in [
                    'input[name*="user" i]',
                    'input[name*="login" i]',
                    'input[autocomplete="username"]',
                    'input[type="email"]',
                    'input[name*="email" i]',
                    'input[type="text"]',
                ]:
                    loc = form.locator(sel).first
                    if loc.count():
                        try:
                            if loc.is_visible():
                                user = loc
                                break
                        except Exception:
                            user = loc
                            break
                if user is None:
                    return False
                user.fill(identity)
                pwd.fill(str(account["password"]))
                submit = form.locator('button[type="submit"],input[type="submit"]').first
                if not submit.count():
                    return False
                submit.click(timeout=4000)
                page.wait_for_timeout(1500)
                return True

            submitted = _generic_login(str(account["username"]))
            if submitted:
                try:
                    first_body = page.locator("body").inner_text(timeout=4000).lower()
                except Exception:
                    first_body = ""
                obvious_failure = any(x in first_body for x in [
                    "неверный пароль","неправильный пароль","пароль неверен",
                    "пользователь не существует","такого пользователя не существует",
                    "не ввели e-mail или ввели его неверно",
                    "не ввели email или ввели его неверно",
                    "ошибка входа","ошибка авторизации",
                ])
                if obvious_failure and account.get("email"):
                    page.goto(login_url, wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(500)
                    submitted = _generic_login(str(account.get("email") or ""))

        body = page.locator("body").inner_text(timeout=5000)
        low = body.lower()
        current_url = page.url
        try:
            html = page.content()
            logged_in = (
                page.locator('a[href*="/logout"],a[href*="action=logout"]').count() > 0
                or '"loggedIn":true' in html
                or '"loggedIn": true' in html
            )
        except Exception:
            logged_in = False
        activation_phrases = [
            "учётная запись не активирована", "учетная запись не активирована",
            "аккаунт не активирован", "активировать учётную запись", "активировать учетную запись",
            "активация аккаунта", "активация учётной записи", "активация учетной записи",
            "подтвердите e-mail", "подтвердите email", "код активации",
        ]
        login_error_phrases = [
            "неверный пароль", "неправильный пароль", "пароль неверен",
            "неправильный логин или пароль",
            "неправильно указано имя пользователя или электронная почта",
            "неправильно указано имя пользователя",
            "неверное имя пользователя",
            "не ввели e-mail или ввели его неверно",
            "не ввели email или ввели его неверно",
            "пользователь не существует", "такого пользователя не существует",
            "ошибка входа", "ошибка авторизации",
        ]
        diag_lines = [
            x.strip() for x in body.splitlines()
            if any(w in x.lower() for w in ["актив", "невер", "неправ", "не существует", "ошибка", "пароль"])
        ][:6]
        diagnostic = " | ".join(diag_lines)[:900]
        if logged_in:
            status = "ready"
            checkpoint = None
            error = None
            readiness_url = _post_login_readiness_url(platform)
            if readiness_url:
                try:
                    readiness_response = page.goto(
                        readiness_url,
                        wait_until="domcontentloaded",
                        timeout=45000,
                    )
                    page.wait_for_timeout(500)
                    readiness_body = page.locator("body").inner_text(timeout=5000)
                    status, checkpoint, error = _classify_post_login_readiness(
                        platform,
                        readiness_response.status if readiness_response else 0,
                        readiness_body,
                    )
                    current_url = page.url
                except Exception as exc:
                    status = "in_progress"
                    checkpoint = "login_result_unclear"
                    error = f"Не удалось проверить право публикации после входа: {type(exc).__name__}: {exc}"
            else:
                status = "in_progress"
                checkpoint = "posting_permission_unverified"
                error = "Вход подтверждён, но для площадки не найден проверяемый коммерческий раздел/маршрут создания темы."
        elif any(x in low for x in activation_phrases):
            status = "verification_required"
            checkpoint = "email_verification_required"
            error = diagnostic or "Учётная запись требует подтверждения по почте."
        elif any(x in low for x in login_error_phrases):
            status = "blocked"
            checkpoint = "login_failed"
            error = diagnostic or "Вход не выполнен."
        else:
            status = "in_progress"
            checkpoint = "login_result_unclear"
            error = diagnostic or "Форма входа отправлена, но авторизация не подтверждена."

        status, checkpoint, error = _preserve_pending_bootstrap_checkpoint(
            previous_checkpoint,
            previous_error,
            status,
            checkpoint,
            error,
            platform=platform,
        )

        regs = reg_rows
        row = existing_reg
        if row is None:
            row = {"platform": platform}
            regs.append(row)
        preserved_bootstrap = bool(
            previous_checkpoint in marketplace_svc.HUMAN_PLATFORM_BOOTSTRAP_CHECKPOINTS
            and status != "ready"
            and checkpoint == previous_checkpoint
        )
        now_iso = datetime.now(timezone.utc).isoformat()
        warming_started_at = row.get("warming_started_at")
        if status == "warming" and not warming_started_at:
            warming_started_at = now_iso
        row.update({
            "status": status,
            "checkpoint": checkpoint,
            "account_url": previous_account_url if preserved_bootstrap and previous_account_url else current_url,
            "last_error": error,
            "updated_at": now_iso,
            "warming_started_at": warming_started_at,
        })
        _save(REGISTRATIONS, regs)
        marketplace_svc.record_attempt(
            platform=platform,
            action="verify_registration",
            status_value="success" if status == "ready" else ("blocked" if status in {"blocked","verification_required"} else "prepared"),
            url=current_url if status == "ready" else None,
            error_code=checkpoint,
            error_detail=error,
            meta={"submitted": submitted},
        )
        ctx.close()
    return {"ok": status == "ready", "platform": platform, "status": status, "checkpoint": checkpoint, "url": current_url, "error": error}

def prepare_post(platform: str, draft_id: str, *, headless: bool, publish: bool) -> dict:
    # Fresh rule audit is mandatory before every prepared publication. If the
    # rules are ambiguous, paid-only, prohibit links/contacts, or the audit
    # failed, the browser operator stops before touching the publish flow.
    audit = platform_rules.inspect_platform(platform)
    gate = platform_rules.publish_gate(platform, action="proactive")
    if not gate.get("allowed"):
        marketplace_svc.record_attempt(platform=platform, action="prepare_post", status_value="blocked", draft_id=draft_id, error_code=str(gate.get("reason") or "rules_block"), error_detail="Площадка не прошла свежую проверку правил для бесплатной публикации.", rules_decision=str((audit or {}).get("decision") or ""))
        raise SystemExit(f"publication blocked by platform rules: {gate.get('reason')}")
    # Every forum channel must have a verified READY account before BORIS
    # even opens a posting form. Do not special-case only platforms that already
    # have hand-written adapters: generic/discovered forums need the same gate.
    pobj = marketplace_svc.get_platform(platform)
    requires_ready_account = bool(
        platform in REGISTRATION_URLS
        or platform in POST_URLS
        or (pobj is not None and marketplace_svc._channel_type(pobj) == "forum")
    )
    if requires_ready_account:
        reg = next((x for x in marketplace_svc.registration_plan() if x.get("platform") == platform), None)
        if not reg or reg.get("status") != "ready":
            reason = str((reg or {}).get("checkpoint") or "account_not_ready")
            detail = str((reg or {}).get("last_error") or "Аккаунт площадки ещё не подтверждён.")
            marketplace_svc.record_attempt(
                platform=platform, action="prepare_post", status_value="blocked",
                draft_id=draft_id, error_code=reason, error_detail=detail,
                rules_decision=str((audit or {}).get("decision") or ""),
            )
            raise SystemExit(f"publication blocked: account not ready ({reason})")

    draft = _find_draft(draft_id)
    if draft.get("status") != "approved":
        marketplace_svc.record_attempt(platform=platform, action="prepare_post", status_value="blocked", draft_id=draft_id, error_code="owner_approval_required", error_detail="Материал ещё не согласован владельцем.", rules_decision=str((audit or {}).get("decision") or ""))
        raise SystemExit("publication blocked: owner approval required")
    if draft.get("platform") != platform:
        marketplace_svc.record_attempt(platform=platform, action="prepare_post", status_value="failed", draft_id=draft_id, error_code="wrong_platform", error_detail=f"Материал относится к {draft.get('platform')}, а запущена {platform}.", rules_decision=str((audit or {}).get("decision") or ""))
        raise SystemExit(f"draft belongs to {draft.get('platform')}, not {platform}")
    start_url = (
        str(draft.get("publication_surface_post_url") or "").strip()
        or POST_URLS.get(platform)
        or _platform_start_url(platform)
    )

    with sync_playwright() as p:
        ctx = _context(p, platform, headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(start_url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(1500)

        # OWN_TOPIC_ONLY_V1
        # Never place BORIS links as a reply/comment inside somebody else's
        # existing discussion. If a verified surface resolves to a concrete
        # thread, ignore any reply editor there and look only for a control that
        # creates a brand-new topic/listing. If such a control is unavailable,
        # fail closed.
        existing_thread_page = _looks_like_existing_thread_url(page.url)
        try:
            has_editor = (
                not existing_thread_page
                and page.locator('textarea, input[name*="title" i], [contenteditable="true"]').count() > 0
            )
        except Exception:
            has_editor = False
        if not has_editor:
            opened_composer = False
            for sel in [
                'a[href*="newthread" i]',
                'a[href*="new-topic" i]',
                'a[href*="newtopic" i]',
                'a[href*="create-thread" i]',
                'a[href*="post-thread" i]',
                'a[href*="module=post" i]:not([href*="topic=" i]):not([href*="thread=" i])',
                'a[href*="action=post" i]:not([href*="topic=" i]):not([href*="thread=" i])',
                'a[href*="posting.php" i]:not([href*="mode=reply" i])',
            ]:
                try:
                    loc = page.locator(sel).first
                    if loc.count() and loc.is_visible():
                        loc.click(timeout=4000)
                        opened_composer = True
                        break
                except Exception:
                    pass
            if not opened_composer:
                opened_composer = _click_text(page, [
                    "Создать тему", "Новая тема", "Создать новую тему",
                    "Начать новую тему", "Добавить новую тему",
                    "Post Thread", "New Topic", "Create Topic", "Start Topic", "Start New Topic",
                ])
            if opened_composer:
                page.wait_for_timeout(1200)
            elif existing_thread_page:
                marketplace_svc.record_attempt(
                    platform=platform,
                    action="prepare_post",
                    status_value="blocked",
                    draft_id=draft_id,
                    error_code="foreign_thread_reply_forbidden",
                    error_detail="BORIS размещает ссылки только в собственных новых темах/объявлениях; ответы в чужих обсуждениях запрещены.",
                    rules_decision=str((audit or {}).get("decision") or ""),
                )
                ctx.close()
                raise SystemExit("publication blocked: foreign thread replies are forbidden")

        if _looks_like_existing_thread_url(page.url):
            marketplace_svc.record_attempt(
                platform=platform,
                action="prepare_post",
                status_value="blocked",
                draft_id=draft_id,
                error_code="foreign_thread_reply_forbidden",
                error_detail="После навигации BORIS всё ещё находится в существующей теме, а не в форме создания собственной темы.",
                rules_decision=str((audit or {}).get("decision") or ""),
            )
            ctx.close()
            raise SystemExit("publication blocked: new own topic form was not reached")

        # Use verified platform-specific fields when known; generic fallback
        # is only for inspected platforms and never changes the publish gate.
        if platform == "forum_baza_1c":
            title_sel = None
            body_sel = None
            try:
                page.locator('form#postmodify input#subject[name="subject"]').fill(draft.get("title",""))
                title_sel = 'form#postmodify input#subject[name="subject"]'
            except Exception:
                pass
            try:
                # SCEditor hides the source textarea. Set both the editor instance
                # and the backing textarea so submit receives exactly our text.
                message_text = draft.get("text","")
                page.evaluate("""(value) => {
                    const ta = document.querySelector('form#postmodify textarea#message[name="message"]');
                    if (!ta) throw new Error('message textarea not found');
                    try {
                        const inst = window.sceditor && window.sceditor.instance ? window.sceditor.instance(ta) : null;
                        if (inst && typeof inst.val === 'function') inst.val(value);
                    } catch (e) {}
                    ta.value = value;
                    ta.dispatchEvent(new Event('input', {bubbles:true}));
                    ta.dispatchEvent(new Event('change', {bubbles:true}));
                }""", message_text)
                body_sel = 'form#postmodify textarea#message[name="message"]'
            except Exception:
                pass
            try:
                tags = draft.get("tags") or "1С, CRM, API, автоматизация"
                page.locator('form#postmodify input#tags[name="tags"]').fill(tags[:80])
            except Exception:
                pass
        else:
            if platform in {"talkingcity_services", "freehostforum_webdev"}:
                # vBulletin 6 exposes the commercial listing before the authoring
                # form. Open the explicit topic action only after READY-account
                # and rule gates have already passed. If the control is absent,
                # the generic field lookup below fails closed with
                # post_form_not_found instead of guessing a route.
                opened = _click_text(
                    page,
                    ["Post New Topic", "Start New Topic", "Start a New Topic", "Create New Topic"],
                )
                if opened:
                    page.wait_for_timeout(1000)
            title_sel = _fill_first(page, [
                'input[name*="title" i]','input[placeholder*="заголов" i]','input[placeholder*="назван" i]'
            ], draft.get("title",""))
            body_sel = _fill_first(page, [
                'textarea','textarea[name*="description" i]','textarea[name*="text" i]',
                '[contenteditable="true"]'
            ], draft.get("text",""))

        checkpoint = _checkpoint(page)
        screenshot = RUNS / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{platform}_{draft_id}_post.png"
        screenshot_error = None
        try:
            page.screenshot(path=str(screenshot), full_page=True, timeout=7000)
        except Exception as exc:
            screenshot_error = f"{type(exc).__name__}: {exc}"

        submitted = False
        publication_verified = False
        if publish and not checkpoint and body_sel:
            if platform == "forum_baza_1c":
                try:
                    page.locator('form#postmodify input[name="post"]').click(timeout=4000)
                    submitted = True
                except Exception:
                    submitted = False
            else:
                submitted = _click_text(page, ["Опубликовать","Разместить","Отправить","Предложить новость"])
            if submitted:
                page.wait_for_timeout(2000)
                checkpoint = _checkpoint(page)
                current_url = page.url
                if not checkpoint and current_url.rstrip("/") != start_url.rstrip("/"):
                    try:
                        body_after = page.locator("body").inner_text(timeout=5000)
                    except Exception:
                        body_after = ""
                    try:
                        html_after = page.content()
                    except Exception:
                        html_after = ""
                    title_probe = str(draft.get("title") or "").strip()[:80]
                    target_probe = str(draft.get("target_url") or draft.get("site_url") or "").strip()
                    publication_verified = bool(
                        (title_probe and title_probe.lower() in body_after.lower())
                        or (target_probe and target_probe in html_after)
                    )
                    if publication_verified:
                        try:
                            marketplace_svc.mark_posted(draft_id, current_url)
                        except Exception:
                            publication_verified = False

        current_url = page.url
        ctx.close()

    log = _log(platform, "post", {
        "draft_id": draft_id, "url": current_url, "title_field": title_sel, "body_field": body_sel,
        "publish_clicked": submitted, "checkpoint": checkpoint, "screenshot": str(screenshot)
    })
    if checkpoint:
        attempt_status = "blocked"
        error_code = checkpoint
        error_detail = "Площадка потребовала ручное подтверждение; автоматический обход запрещён."
    elif not body_sel:
        attempt_status = "failed"
        error_code = "post_form_not_found"
        error_detail = "Не найдено поле публикации; нужен адаптер конкретного раздела."
    elif publish and submitted and publication_verified:
        attempt_status = "success"
        error_code = None
        error_detail = None
    elif publish and submitted:
        attempt_status = "submitted"
        error_code = "publication_url_verification_required"
        error_detail = "Кнопка публикации нажата; прямой URL пока не удалось подтвердить автоматически."
    else:
        attempt_status = "prepared"
        error_code = None
        error_detail = None
    marketplace_svc.record_attempt(platform=platform, action="publish" if publish else "prepare_post", status_value=attempt_status, draft_id=draft_id, url=current_url if submitted else None, error_code=error_code, error_detail=error_detail, rules_decision=str((audit or {}).get("decision") or ""), screenshot=str(screenshot), meta={"publish_clicked": submitted, "publication_verified": publication_verified, "title_field": title_sel, "body_field": body_sel, "screenshot_error": screenshot_error})
    return {
        "ok": True, "platform": platform, "draft_id": draft_id, "url": current_url,
        "filled": bool(body_sel), "publish_clicked": submitted, "publication_verified": publication_verified,
        "checkpoint": checkpoint, "log": str(log), "screenshot": str(screenshot),
        "screenshot_error": screenshot_error,
        "next_action": "done" if publication_verified else ("verify_publication_url_and_mark_posted" if submitted and not checkpoint else "manual_checkpoint_or_adapter_needed"),
    }

def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    rg = sub.add_parser("register")
    rg.add_argument("--platform", required=True)
    rg.add_argument("--email", required=False)
    rg.add_argument("--submit", action="store_true")
    rg.add_argument("--headed", action="store_true")

    vr = sub.add_parser("verify-registration")
    vr.add_argument("--platform", required=True)
    vr.add_argument("--headed", action="store_true")

    pp = sub.add_parser("prepare-post")
    pp.add_argument("--platform", required=True)
    pp.add_argument("--draft-id", required=True)
    pp.add_argument("--publish", action="store_true")
    pp.add_argument("--headed", action="store_true")

    args = ap.parse_args()
    if args.cmd == "register":
        out = register(args.platform, args.email, headless=not args.headed, submit=args.submit)
    elif args.cmd == "verify-registration":
        out = verify_registration(args.platform, headless=not args.headed)
    else:
        out = prepare_post(args.platform, args.draft_id, headless=not args.headed, publish=args.publish)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
