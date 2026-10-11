#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import fcntl
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel
from playwright.async_api import BrowserContext, Page, async_playwright

from app.services import service_marketplace as marketplace

ROOT = Path("/root/BORIS/backend")
DATA = ROOT / "data" / "service_marketplaces"
SESSIONS_DIR = ROOT / "browser_sessions" / "service_marketplaces"
ACCOUNT_SECRETS = DATA / "account_secrets.json"
MAILBOXES = DATA / "mailboxes.json"
OPERATOR_PROFILE = DATA / "operator_profile.json"
SESSION_TTL = timedelta(minutes=30)
VIEWPORT = {"width": 1440, "height": 1000}
GUARD_LOCK_FILE = ROOT / "run" / "crowd_handoff_gateway.lock"

app = FastAPI(title="BORIS Crowd Human Handoff", docs_url=None, redoc_url=None, openapi_url=None)
_pw = None
_recovery_task: asyncio.Task | None = None


@dataclass
class Handoff:
    token: str
    platform: str
    start_url: str
    email: str
    context: BrowserContext | None
    page: Page | None
    created_at: datetime
    expires_at: datetime
    state: str = "waiting_human"
    message: str = ""
    last_frame: bytes | None = None
    filled: list[str] = field(default_factory=list)
    guard_lock: Any | None = None


SESSIONS: dict[str, Handoff] = {}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_secret_rows(rows: list[dict[str, Any]]) -> None:
    ACCOUNT_SECRETS.parent.mkdir(parents=True, exist_ok=True)
    tmp = ACCOUNT_SECRETS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, ACCOUNT_SECRETS)
    os.chmod(ACCOUNT_SECRETS, 0o600)


def default_email() -> str:
    for row in load_json(MAILBOXES, []):
        address = str(row.get("address") or "").strip()
        if address:
            return address
    raise RuntimeError("registration mailbox is not configured")


def account_secret(platform: str, email: str) -> dict[str, str]:
    rows = load_json(ACCOUNT_SECRETS, [])
    if not isinstance(rows, list):
        rows = []
    row = next((x for x in rows if str(x.get("platform") or "") == platform), None)
    if isinstance(row, dict) and row.get("password"):
        if not row.get("email"):
            row["email"] = email
            save_secret_rows(rows)
        return {"username": str(row.get("username") or "BORIS_AI"), "email": str(row.get("email") or email), "password": str(row.get("password") or "")}
    username = {
        "towerbuild": "BorisAI2026",
    }.get(platform, "BORIS_AI")
    row = {
        "platform": platform,
        "email": email,
        "username": username,
        "password": "B!" + secrets.token_urlsafe(20) + "9a",
        "created_at": utcnow().isoformat(),
    }
    rows.append(row)
    save_secret_rows(rows)
    return {"username": username, "email": email, "password": str(row["password"])}


def registration_row(platform: str) -> dict[str, Any]:
    row = next((x for x in marketplace.registration_plan() if str(x.get("platform") or "") == platform), None)
    if not row:
        raise RuntimeError("platform is not present in registration plan")
    return row


def platform_start_url(platform: str) -> str:
    row = registration_row(platform)
    account_url = str(row.get("account_url") or "").strip()
    if account_url:
        return account_url
    obj = marketplace.get_platform(platform)
    url = str(getattr(obj, "url", "") or "").strip() if obj else ""
    if not url:
        raise RuntimeError("platform has no registration/start URL")
    return url


def acquire_guard_lock():
    GUARD_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    fh = GUARD_LOCK_FILE.open("a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        return None
    return fh


def release_guard_lock(session: Handoff) -> None:
    if session.guard_lock is None:
        return
    try:
        fcntl.flock(session.guard_lock.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        session.guard_lock.close()
    except Exception:
        pass
    session.guard_lock = None


def session_or_404(token: str) -> Handoff:
    session = SESSIONS.get(token)
    if not session:
        raise HTTPException(status_code=404, detail="Handoff session not found")
    if session.expires_at < utcnow():
        raise HTTPException(status_code=410, detail="Handoff session expired")
    return session


async def visible(locator) -> bool:
    try:
        return bool(await locator.count()) and bool(await locator.first.is_visible())
    except Exception:
        return False


async def fill_first(page: Page, selectors: list[str], value: str) -> str | None:
    if not value:
        return None
    for selector in selectors:
        try:
            loc = page.locator(selector).first
            if await visible(loc):
                await loc.fill(value, timeout=2500)
                return selector
        except Exception:
            continue
    return None


async def prefill(session: Handoff) -> list[str]:
    if not session.page:
        return session.filled
    creds = account_secret(session.platform, session.email)
    page = session.page
    filled: list[str] = []
    if await fill_first(page, ['input[name*="user" i]', 'input[name*="login" i]', 'input[autocomplete="username"]'], creds["username"]):
        filled.append("username")
    if await fill_first(page, ['input[type="email"]', 'input[name*="email" i]', 'input[autocomplete="email"]'], creds["email"]):
        filled.append("email")
    try:
        password_fields = page.locator('input[type="password"],input[autocomplete="new-password"]')
        count = min(await password_fields.count(), 2)
        for idx in range(count):
            loc = password_fields.nth(idx)
            try:
                if await loc.is_visible():
                    await loc.fill(creds["password"], timeout=2500)
            except Exception:
                pass
        if count:
            filled.append(f"password:{count}")
    except Exception:
        pass
    profile = load_json(OPERATOR_PROFILE, {})
    phone = str(profile.get("phone") or "").strip() if isinstance(profile, dict) else ""
    if phone and await fill_first(page, ['input[type="tel"]', 'input[name*="phone" i]', 'input[name*="mobile" i]', 'input[name*="tel" i]'], phone):
        filled.append("phone")
    session.filled = filled
    return filled


async def frame(session: Handoff) -> bytes:
    if session.page:
        try:
            session.last_frame = await session.page.screenshot(type="png", full_page=False, timeout=7000)
        except Exception:
            pass
    if not session.last_frame:
        raise HTTPException(status_code=503, detail="Browser frame is not available yet")
    return session.last_frame


async def page_text(page: Page) -> str:
    try:
        return " ".join((await page.locator("body").inner_text(timeout=5000)).split())[:50000]
    except Exception:
        return ""


async def captcha_present(page: Page) -> bool:
    selectors = [
        'iframe[src*="recaptcha" i]', 'iframe[src*="hcaptcha" i]', 'iframe[src*="turnstile" i]',
        '[class*="captcha" i]', '[id*="captcha" i]', '[class*="turnstile" i]',
        'input[name="g-recaptcha-response"]', 'input[name="h-captcha-response"]',
        'input[name="cf-turnstile-response"]', 'input[name="captcha_question_answer"]',
    ]
    for selector in selectors:
        try:
            if await page.locator(selector).count():
                return True
        except Exception:
            pass
    text = (await page_text(page)).lower()
    return any(marker in text for marker in [
        'captcha', 'капча', 'я не робот', "i'm not a robot", 'verify you are human',
        'подтвердите, что вы не робот',
    ])


async def classify_after_submit(session: Handoff) -> dict[str, Any]:
    if not session.page:
        return {"status": session.state, "checkpoint": None, "url": session.start_url}
    page = session.page
    text = (await page_text(page)).lower()
    url = str(page.url or session.start_url)
    if await captcha_present(page):
        return {"status": "verification_required", "checkpoint": "captcha_required", "url": url, "message": "CAPTCHA всё ещё присутствует; BORIS не отправляет форму до завершения проверки человеком."}
    email_markers = ["подтвердите почту", "проверьте почту", "verification email", "confirm your email", "activation email", "активац"]
    sms_markers = ["код из смс", "sms code", "verification code", "одноразовый код", "one-time code"]
    success_markers = ["регистрация завершена", "успешно зарегистр", "registration complete", "registration successful", "welcome"]
    logout_markers = ["выйти", "logout", "log out", "мой профиль", "my profile", "личный кабинет"]
    if any(x in text for x in email_markers):
        return {"status": "verification_required", "checkpoint": "email_verification_required", "url": url, "message": "Площадка ждёт подтверждение email."}
    if any(x in text for x in sms_markers):
        return {"status": "verification_required", "checkpoint": "sms_or_verification_code_required", "url": url, "message": "Площадка ждёт одноразовый код."}
    password_visible = False
    try:
        password_fields = page.locator('input[type="password"]')
        for idx in range(min(await password_fields.count(), 20)):
            if await password_fields.nth(idx).is_visible():
                password_visible = True
                break
    except Exception:
        password_visible = False
    if any(x in text for x in logout_markers) and not password_visible:
        return {"status": "ready", "checkpoint": "registered", "url": url, "message": "Аккаунт выглядит авторизованным."}
    if any(x in text for x in success_markers):
        return {"status": "in_progress", "checkpoint": "account_creation_unverified", "url": url, "message": "Регистрация принята; BORIS перепроверит вход."}
    return {"status": "in_progress", "checkpoint": "post_submit_review", "url": url, "message": "После отправки нужна повторная проверка состояния."}


async def find_submit(page: Page):
    forms = page.locator("form")
    form_count = await forms.count()
    chosen = None
    for idx in range(min(form_count, 30)):
        form = forms.nth(idx)
        try:
            if await form.locator('input[type="password"]').count() or await form.locator('input[type="email"],input[name*="email" i]').count():
                chosen = form
                break
        except Exception:
            continue
    if chosen is None and form_count:
        chosen = forms.first
    if chosen is None:
        return None
    candidates = chosen.locator('button[type="submit"],input[type="submit"],button:not([type])')
    for idx in range(min(await candidates.count(), 20)):
        button = candidates.nth(idx)
        try:
            if await button.is_visible() and await button.is_enabled():
                return button
        except Exception:
            continue
    return None


async def close_session_browser(session: Handoff) -> None:
    if session.page:
        try:
            session.last_frame = await session.page.screenshot(type="png", full_page=False, timeout=5000)
        except Exception:
            pass
    if session.context:
        try:
            await session.context.close()
        except Exception:
            pass
    session.page = None
    session.context = None
    release_guard_lock(session)


async def trigger_guard() -> None:
    try:
        proc = await asyncio.create_subprocess_exec("systemctl", "start", "boris-forum-acquisition-guard.service", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(proc.wait(), timeout=180)
    except Exception:
        pass


@app.on_event("startup")
async def startup() -> None:
    global _pw
    DATA.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    _pw = await async_playwright().start()


@app.on_event("shutdown")
async def shutdown() -> None:
    global _pw
    for session in list(SESSIONS.values()):
        await close_session_browser(session)
    if _pw:
        await _pw.stop()
        _pw = None


@app.get("/health")
async def health():
    return {"status": "ok", "sessions": len(SESSIONS), "time": utcnow().isoformat()}


class CreateRequest(BaseModel):
    platform: str


@app.post("/internal/create")
async def internal_create(req: CreateRequest, request: Request):
    if request.client and request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(status_code=403, detail="local only")
    platform = str(req.platform or "").strip()
    row = registration_row(platform)
    checkpoint = str(row.get("checkpoint") or "")
    if not checkpoint:
        raise HTTPException(status_code=409, detail="platform has no pending onboarding checkpoint")
    now = utcnow()
    for old_token, existing in list(SESSIONS.items()):
        if existing.expires_at <= now and existing.state not in {"completed", "closed"}:
            await close_session_browser(existing)
            existing.state = "expired"
            SESSIONS.pop(old_token, None)
            continue
        if existing.platform == platform and existing.expires_at > now and existing.state not in {"completed", "closed"}:
            return {"status": "existing", "platform": platform, "token": existing.token, "url": f"https://boris-ai.pro/crowd-handoff/s/{existing.token}", "checkpoint": checkpoint}
    if _pw is None:
        raise HTTPException(status_code=503, detail="Playwright is not ready")
    guard_lock = acquire_guard_lock()
    if guard_lock is None:
        raise HTTPException(status_code=409, detail="Crowd Guardian is busy; retry this handoff after the current guard tick")
    email = default_email()
    account_secret(platform, email)
    start_url = platform_start_url(platform)
    token = secrets.token_urlsafe(32)
    user_data = SESSIONS_DIR / platform
    try:
        context = await _pw.chromium.launch_persistent_context(
            str(user_data),
            headless=True,
            viewport=VIEWPORT,
            locale="ru-RU",
            timezone_id="Europe/Moscow",
        )
    except Exception as exc:
        try:
            fcntl.flock(guard_lock.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        guard_lock.close()
        raise HTTPException(status_code=503, detail=f"Browser profile launch failed: {type(exc).__name__}")
    page = context.pages[0] if context.pages else await context.new_page()
    session = Handoff(
        token=token,
        platform=platform,
        start_url=start_url,
        email=email,
        context=context,
        page=page,
        created_at=utcnow(),
        expires_at=utcnow() + SESSION_TTL,
        message="BORIS заполняет форму. Решите только внешний human-checkpoint на странице.",
        guard_lock=guard_lock,
    )
    SESSIONS[token] = session
    try:
        await page.goto(start_url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(1200)
    except Exception as exc:
        session.message = f"Страница открылась с ошибкой навигации: {type(exc).__name__}. Можно попробовать обновить через интерфейс."
    await prefill(session)
    await frame(session)
    return {"status": "ok", "platform": platform, "token": token, "url": f"https://boris-ai.pro/crowd-handoff/s/{token}", "checkpoint": checkpoint, "filled": session.filled, "expires_at": session.expires_at.isoformat()}


@app.get("/s/{token}", response_class=HTMLResponse)
async def handoff_ui(token: str):
    session_or_404(token)
    return HTMLResponse(f"""<!doctype html>
<html lang=\"ru\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>BORIS Crowd Handoff</title><style>
body{{font-family:Arial,sans-serif;margin:0;background:#111;color:#eee}}header{{padding:12px 16px;background:#1d1d1d;position:sticky;top:0;z-index:2}}button{{font-size:16px;padding:10px 14px;margin:4px;border-radius:8px;border:0;cursor:pointer}}#done{{background:#44c767}}#msg{{padding:8px 0;color:#ddd}}#frameWrap{{display:flex;justify-content:center;padding:10px}}#frame{{max-width:100%;height:auto;border:1px solid #555;cursor:crosshair;background:#fff}}.small{{font-size:13px;color:#aaa}}</style></head>
<body><header><b>BORIS Crowd Handoff</b><div id=\"msg\">Решите CAPTCHA/обязательный пункт площадки на изображении ниже. Остальные поля BORIS заполняет сам.</div>
<button onclick=\"scrollByRemote(-700)\">↑ выше</button><button onclick=\"scrollByRemote(700)\">↓ ниже</button><button onclick=\"reloadRemote()\">↻ перезагрузить страницу</button><button onclick=\"refreshFrame()\">Обновить картинку</button><br>
<input id=\"humanText\" placeholder=\"Если CAPTCHA/код нужно ввести текстом\" style=\"font-size:16px;padding:9px;width:min(520px,70vw);border-radius:7px;border:1px solid #666\"><button onclick=\"typeRemote()\">Ввести в выбранное поле</button><button onclick=\"keyRemote('Enter')\">Enter</button>
<button id=\"done\" onclick=\"continueBoris()\">Готово — продолжить BORIS</button><div class=\"small\">Сначала кликните нужное поле/картинку. Ссылка одноразовая и ограничена по времени. Все действия идут только в эту серверную browser-сессию.</div></header>
<div id=\"frameWrap\"><img id=\"frame\" draggable=\"false\"></div><script>
const token={json.dumps(token)}; const img=document.getElementById('frame'); const msg=document.getElementById('msg');
async function refreshFrame(){{ img.src='/crowd-handoff/api/'+token+'/frame?t='+Date.now(); }}
img.addEventListener('click',async e=>{{const r=img.getBoundingClientRect(); const x=(e.clientX-r.left)*1440/r.width; const y=(e.clientY-r.top)*1000/r.height; msg.textContent='Клик…'; const q=await fetch('/crowd-handoff/api/'+token+'/click',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{x,y}})}}); const d=await q.json(); msg.textContent=d.message||d.status||'OK'; setTimeout(refreshFrame,250);}});
async function scrollByRemote(delta){{await fetch('/crowd-handoff/api/'+token+'/scroll',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{delta}})}}); setTimeout(refreshFrame,250);}}
async function typeRemote(){{const text=document.getElementById('humanText').value; const q=await fetch('/crowd-handoff/api/'+token+'/type',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{text}})}}); const d=await q.json(); msg.textContent=d.message||d.detail||d.status||'Введено'; setTimeout(refreshFrame,250);}}
async function keyRemote(key){{await fetch('/crowd-handoff/api/'+token+'/key',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{key}})}}); setTimeout(refreshFrame,250);}}
async function reloadRemote(){{msg.textContent='Перезагрузка…'; const q=await fetch('/crowd-handoff/api/'+token+'/reload',{{method:'POST'}}); const d=await q.json(); msg.textContent=d.message||d.status||'Перезагружено'; setTimeout(refreshFrame,500);}}
async function continueBoris(){{msg.textContent='BORIS продолжает регистрацию…'; const q=await fetch('/crowd-handoff/api/'+token+'/continue',{{method:'POST'}}); const d=await q.json(); msg.textContent=d.message||d.detail||d.status||'Готово'; setTimeout(refreshFrame,500);}}
refreshFrame(); setInterval(refreshFrame,1800);
</script></body></html>""")


@app.get("/api/{token}/frame")
async def get_frame(token: str):
    session = session_or_404(token)
    return Response(await frame(session), media_type="image/png", headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"})


class ClickRequest(BaseModel):
    x: float
    y: float


@app.post("/api/{token}/click")
async def click(token: str, req: ClickRequest):
    session = session_or_404(token)
    if not session.page:
        raise HTTPException(status_code=409, detail="browser session is already closed")
    x = max(0.0, min(float(req.x), float(VIEWPORT["width"] - 1)))
    y = max(0.0, min(float(req.y), float(VIEWPORT["height"] - 1)))
    await session.page.mouse.click(x, y)
    await session.page.wait_for_timeout(350)
    await prefill(session)
    session.message = "Клик передан. Если проверка завершена, нажмите «Готово — продолжить BORIS»."
    return {"status": "ok", "message": session.message}


class ScrollRequest(BaseModel):
    delta: float


@app.post("/api/{token}/scroll")
async def scroll(token: str, req: ScrollRequest):
    session = session_or_404(token)
    if not session.page:
        raise HTTPException(status_code=409, detail="browser session is already closed")
    delta = max(-2000.0, min(float(req.delta), 2000.0))
    await session.page.mouse.wheel(0, delta)
    await session.page.wait_for_timeout(250)
    return {"status": "ok"}


class TypeRequest(BaseModel):
    text: str


@app.post("/api/{token}/type")
async def type_text(token: str, req: TypeRequest):
    session = session_or_404(token)
    if not session.page:
        raise HTTPException(status_code=409, detail="browser session is already closed")
    text_value = str(req.text or "")[:256]
    if not text_value:
        raise HTTPException(status_code=400, detail="text is empty")
    await session.page.keyboard.type(text_value, delay=35)
    await session.page.wait_for_timeout(200)
    return {"status": "ok", "message": "Текст введён в выбранное поле."}


class KeyRequest(BaseModel):
    key: str


@app.post("/api/{token}/key")
async def key_press(token: str, req: KeyRequest):
    session = session_or_404(token)
    if not session.page:
        raise HTTPException(status_code=409, detail="browser session is already closed")
    key = str(req.key or "")
    if key not in {"Enter", "Tab", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Space"}:
        raise HTTPException(status_code=400, detail="key is not allowed")
    await session.page.keyboard.press(key)
    await session.page.wait_for_timeout(200)
    return {"status": "ok", "message": f"Клавиша {key} передана."}


@app.post("/api/{token}/reload")
async def reload_page(token: str):
    session = session_or_404(token)
    if not session.page:
        raise HTTPException(status_code=409, detail="browser session is already closed")
    try:
        await session.page.reload(wait_until="domcontentloaded", timeout=30000)
    except Exception:
        pass
    await session.page.wait_for_timeout(700)
    await prefill(session)
    await frame(session)
    return {"status": "ok", "message": "Страница перезагружена, регистрационные поля BORIS заполнил повторно."}


@app.post("/api/{token}/continue")
async def continue_boris(token: str):
    session = session_or_404(token)
    if not session.page:
        return {"status": session.state, "message": session.message or "Browser session already closed"}
    await prefill(session)
    if await captcha_present(session.page):
        session.state = "waiting_human"
        session.message = "CAPTCHA ещё видна. Завершите её на странице, затем снова нажмите «Готово — продолжить BORIS»."
        await frame(session)
        return {"status": session.state, "message": session.message, "checkpoint": "captcha_required"}
    button = await find_submit(session.page)
    if button is None:
        session.state = "waiting_human"
        session.message = "Кнопка регистрации пока недоступна. Проверьте CAPTCHA и обязательные пункты площадки, затем нажмите «Готово» ещё раз."
        await frame(session)
        return {"status": session.state, "message": session.message, "filled": session.filled}
    try:
        await button.scroll_into_view_if_needed(timeout=2500)
        await button.click(timeout=5000)
    except Exception as exc:
        session.state = "waiting_human"
        session.message = f"Форма ещё не готова к отправке: {type(exc).__name__}. Завершите обязательный шаг площадки и повторите."
        return {"status": session.state, "message": session.message}
    try:
        await session.page.wait_for_load_state("domcontentloaded", timeout=8000)
    except Exception:
        pass
    await session.page.wait_for_timeout(1800)
    result = await classify_after_submit(session)
    session.state = str(result.get("status") or "in_progress")
    session.message = str(result.get("message") or "BORIS отправил форму и проверяет результат.")
    marketplace.upsert_registration(
        session.platform,
        session.state,
        email=session.email,
        account_url=str(result.get("url") or session.page.url or session.start_url),
        checkpoint=str(result.get("checkpoint") or "post_submit_review"),
        last_error=None if session.state == "ready" else session.message,
    )
    await frame(session)
    if session.state == "ready":
        await close_session_browser(session)
        session.state = "completed"
        session.message = "Аккаунт подтверждён. BORIS сохранил browser-сессию и передал площадку обратно в автономный Crowd SEO для публикации."
        asyncio.create_task(trigger_guard())
    return {"status": session.state, "message": session.message, "checkpoint": result.get("checkpoint"), "url": result.get("url")}


@app.get("/api/{token}/status")
async def status(token: str):
    session = session_or_404(token)
    return {"status": session.state, "platform": session.platform, "message": session.message, "filled": session.filled, "expires_at": session.expires_at.isoformat(), "current_url": str(session.page.url) if session.page else None}


@app.post("/api/{token}/close")
async def close(token: str):
    session = session_or_404(token)
    await close_session_browser(session)
    session.state = "closed"
    session.message = "Handoff session closed"
    return {"status": "closed"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("crowd_handoff_gateway:app", host="127.0.0.1", port=8765, workers=1)
