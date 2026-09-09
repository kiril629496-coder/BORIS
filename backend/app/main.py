from fastapi import FastAPI, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.db.base import Base
from app.db.session import engine

from app.api.campaigns import router as campaigns_router
from app.api import imports as import_router
from app.api import chat
from app.api import support as support_router
from app.api.avito import router as avito_router
from app.api.calltracking import router as calltracking_router
from app.api.calltracking_own import router as own_calltracking_router, public_router as own_calltracking_public_router
from app.api.mcn_phone import router as mcn_phone_router
from app.api.telephony import router as telephony_router, public_router as telephony_public_router
from app.api.revenue_analytics import router as revenue_analytics_router, public_router as revenue_tracker_public_router
from app.api.nps import router as nps_router
from app.api.prompt_check import router as prompt_check_router
from app.api.avito import public_router as avito_public_router
from app.api.browser_gateway import router as browser_gateway_router, control_router as browser_gateway_control_router, transport_router as browser_gateway_transport_router
from app.api.mass_editor import router as mass_editor_router
from app.api.manager import router as manager_router
from app.api.director_stats import router as director_stats_router
from app.api.parser import router as parser_router
from app.api.storage import router as storage_router
from app.api.tasks import router as tasks_router
from app.api.accounts import router as accounts_router
from app.api.sitebuild import router as sitebuild_router
from app.api.plan_items import router as plan_items_router
from app.api.avito_categories import router as avito_categories_router
from app.api.banners import router as banners_router
from app.api.crm import router as crm_router
from app.api.lead_notifications import router as lead_notifications_router, public_router as lead_notifications_public_router
from app.crm.router import router as boris_crm_router
from app.crm.sync_core_router import router as sync_core_router
from app.crm.connection_control_router import router as connection_control_router
from app.api.prompts import router as prompts_router
from app.api.new_client_launch import router as new_client_launch_router
from app.models.saved_prompt import SavedPrompt  # чтобы create_all() увидел таблицу
from app.models.banner import Banner  # чтобы create_all() увидел таблицу banners
from app.models.campaign import Campaign  # create_all: campaigns
from app.models.campaign_item import CampaignItem  # create_all: campaign_items
from app.models.campaign_op import CampaignOp  # create_all: campaign_ops
from app.models.media_folder import MediaFolder  # create_all: media_folders
from app.models.media_asset import MediaAsset  # create_all: media_assets
from app.models.media_link import MediaLink  # create_all: media_links
from app.models.background_job import BackgroundJob  # create_all: background_jobs
from app.api.landing import router as landing_router
from app.api.billing import router as billing_router
from app.api.software_site import router as software_site_router, admin_router as software_site_admin_router
from app.telegram_bot import _telegram_poll_loop
from app.api.messenger import _messenger_poll_loop
from app.api.messenger import _reminder_loop
from app.api.messenger import router as messenger_router
from app.api.messenger_prompts import router as messenger_prompts_router
from app.api.auth import router as auth_router, get_current_user_or_internal, check_account_access
from app.api.onboarding import router as onboarding_router
from app.models.user import User  # импорт на уровне модуля — чтобы create_all() увидел таблицу users при старте
from app.models.revenue_event import RevenueEvent  # create_all: revenue_events
from app.models.reliability import (ReliabilityCircuit, ReliabilityEvent, ReliabilityFlag,
                                    ReliabilityKillSwitch, ReliabilityHeartbeat)  # create_all: reliability control plane
from app.api.reliability import router as reliability_router
from app.models.control_plane import (ControlRequirement, ControlRequirementVersion, ControlExecution, ControlEvidence, ControlIncident, ControlModuleContract, ControlOwnerInstruction, ControlExecutionStep, ControlExecutionAttempt, ControlRuleConflict, ControlWorkspaceThread, ControlWorkspaceEvent)  # create_all: owner control plane + workspace registry
from app.api.control_plane import router as control_plane_router
import threading as _threading

from fastapi.staticfiles import StaticFiles
import os as _os

app = FastAPI()


@app.get("/health", include_in_schema=False)
def public_health():
    """Small unauthenticated liveness/readiness probe for external monitoring.

    It intentionally exposes no client/provider details. A 200 means the API
    process and PostgreSQL are both usable; a 503 means traffic should be kept
    away from this node until it recovers.
    """
    from datetime import datetime, timezone
    from sqlalchemy import text as _sql_text
    try:
        with engine.connect() as conn:
            conn.execute(_sql_text("select 1"))
    except Exception:
        return JSONResponse(status_code=503, content={
            "status": "degraded", "service": "boris-backend", "database": "unavailable",
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }, headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache", "Expires": "0",
        })
    return JSONResponse(content={
        "status": "ok", "service": "boris-backend", "database": "ok",
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }, headers={
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache", "Expires": "0",
    })


from app.services.reliability import ProviderDeferred, CircuitOpen

@app.exception_handler(ProviderDeferred)
async def provider_deferred_handler(request: Request, exc: ProviderDeferred):
    retry_after = max(5, int(exc.retry_after_seconds or 30))
    return JSONResponse(status_code=429, content={"status":"deferred","message":"Operation deferred; BORIS will retry automatically.","retry_after_seconds":retry_after}, headers={"Retry-After":str(retry_after)})

@app.exception_handler(CircuitOpen)
async def provider_circuit_handler(request: Request, exc: CircuitOpen):
    return JSONResponse(status_code=503, content={"status":"deferred","message":"External service temporarily unavailable; BORIS will retry automatically.","retry_after_seconds":30}, headers={"Retry-After":"30"})


@app.middleware("http")
async def boris_trace_middleware(request, call_next):
    """Attach one trace id to the complete request path and persist unhandled failures."""
    import time as _time
    from app.services.reliability import set_trace_id, get_trace_id, record_event
    trace_id = set_trace_id(request.headers.get("x-boris-trace-id"))
    started = _time.monotonic()
    try:
        response = await call_next(request)
        response.headers["x-boris-trace-id"] = trace_id
        response.headers["x-boris-duration-ms"] = str(int((_time.monotonic() - started) * 1000))
        return response
    except Exception as exc:
        # Best effort only: observability must never turn one request failure into a wider outage.
        try:
            db = __import__("app.db.session", fromlist=["SessionLocal"]).SessionLocal()
            record_event(db, "api", "unhandled_exception", "Необработанная ошибка API",
                         severity="critical", details={"path": request.url.path, "method": request.method,
                                                       "error_type": type(exc).__name__}, trace_id=get_trace_id())
            db.commit(); db.close()
        except Exception:
            pass
        raise


def _background_jobs_loop(slot: int):
    """Persistent DB-backed worker. Multiple slots are safe via SKIP LOCKED in jobs.run_once."""
    import time as _time
    from app.services.jobs import run_once
    from app.services.reliability import heartbeat
    _next_browser_maintenance = 0.0
    while True:
        try:
            processed = run_once(verbose=False)
            heartbeat("jobs", f"background_jobs_{slot}",
                      details={"processed_last_tick": int(processed or 0)})
            # Slot 0 also maintains the authoritative Browser Gateway session vault
            # and releases stale leases. This reuses the existing worker engine rather
            # than creating a second scheduler/daemon.
            if slot == 0 and _time.monotonic() >= _next_browser_maintenance:
                from app.api.browser_gateway import maintain_browser_sessions
                browser_health = maintain_browser_sessions()
                heartbeat("browser_gateway", "session_watchdog", details=browser_health)
                _next_browser_maintenance = _time.monotonic() + 30.0
            _time.sleep(0.8 if processed else 3.0)
        except Exception as exc:
            heartbeat("jobs", f"background_jobs_{slot}", state="degraded",
                      details={"error_type": type(exc).__name__})
            print(f"[background_jobs:{slot}] loop error: {repr(exc)[:240]}", flush=True)
            _time.sleep(5.0)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)




@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)
    _runtime_role = str(_os.environ.get("BORIS_RUNTIME_ROLE", "all") or "all").strip().lower()
    _run_workers = _runtime_role in ("all", "worker")
    _run_api = _runtime_role in ("all", "api")
    from app.services.jobs import recover_interrupted_campaign_jobs
    _recovered_jobs = recover_interrupted_campaign_jobs() if _run_workers else 0
    print(f"STARTUP: runtime_role={_runtime_role} campaign jobs recovered={_recovered_jobs}", flush=True)

    from gigachat_pool import reset_giga_breaker
    reset_giga_breaker()
    print("STARTUP: предохранитель GigaChat (402) сброшен", flush=True)

    # API replicas never execute isolated Chromium jobs. Browser execution has
    # one authoritative owner in the dedicated worker runtime, so two API
    # replicas cannot open the same persistent client profile concurrently.
    if _run_api:
        print("STARTUP: isolated Avito executor owned by dedicated worker runtime", flush=True)

    if _run_workers:
        print("STARTUP: запускаю поток Telegram-поллинга...", flush=True)
        _telegram_thread = _threading.Thread(target=_telegram_poll_loop, daemon=True, name="telegram_poll")
        _telegram_thread.start()
        print(f"STARTUP: поток Telegram запущен, alive={_telegram_thread.is_alive()}", flush=True)

        print("STARTUP: запускаю поток Messenger-поллинга...", flush=True)
        _messenger_thread = _threading.Thread(target=_messenger_poll_loop, daemon=True, name="messenger_poll")
        _messenger_thread.start()
        _reminder_thread = _threading.Thread(target=_reminder_loop, daemon=True, name="reminder_poll")
        _reminder_thread.start()
        print("STARTUP: поток напоминаний запущен", flush=True)
        print(f"STARTUP: поток Messenger запущен, alive={_messenger_thread.is_alive()}", flush=True)

        # One authoritative persistent background_jobs engine. Concurrency is configurable,
        # but bounded on this host: external AI/API work is mostly I/O-bound while local image
        # validation can consume CPU/RAM. Start conservatively at 8 slots on the current 8-vCPU
        # production host; benchmark data can raise/lower it without changing code.
        try:
            _job_workers = max(1, min(int(_os.environ.get("BORIS_JOB_WORKERS", "8")), 16))
        except Exception:
            _job_workers = 8
        for _job_slot in range(_job_workers):
            _job_thread = _threading.Thread(target=_background_jobs_loop, args=(_job_slot,), daemon=True,
                                            name=f"background_jobs_{_job_slot}")
            _job_thread.start()
        print(f"STARTUP: persistent background_jobs workers={_job_workers}", flush=True)
    else:
        print("STARTUP: API-only role; embedded pollers/background workers disabled", flush=True)

_os.makedirs("/root/BORIS/backend/images", exist_ok=True)
app.mount("/images", StaticFiles(directory="/root/BORIS/backend/images"), name="images")

# Все роутеры ниже защищены Depends(get_current_user_or_internal): нужен либо валидный JWT,
# либо запрос с localhost (self-вызовы backend'а и cron-скрипты - см. auth.py). Публичные
# исключения: auth_router (login/register), landing_router (сам решает поэндпоинтно,
# GET /api/landing/content открыт для маркетинговой страницы) и avito_public_router
# (feed.xml/feed_preview.xml - их дёргает сам Avito и его валидатор, у них не может быть JWT).
_protected = [Depends(get_current_user_or_internal), Depends(check_account_access)]

# BORIS_CRM_STAGE_A_V3
from app.api.photo_factory_v3 import router as photo_factory_v3_router

app.include_router(photo_factory_v3_router)
app.include_router(boris_crm_router)
from app.api import mop_combat_training
app.include_router(mop_combat_training.router, dependencies=_protected)
app.include_router(connection_control_router)
app.include_router(sync_core_router)

app.include_router(new_client_launch_router, dependencies=_protected)
app.include_router(campaigns_router, dependencies=_protected)
from app.models.catalog_product import CatalogProduct  # create_all: catalog_products
from app.models.product_variant import ProductVariant  # create_all: product_variants
from app.models.catalog_fact import CatalogFact  # create_all: catalog_facts
from app.models.photo_session import PhotoSession  # create_all: photo_sessions
from app.models.photo_shot import PhotoShot  # create_all: photo_shots
from app.models.dna_profile import DnaProfile  # create_all: dna_profiles
from app.models.prompt_template import PromptTemplate  # create_all: prompt_templates
from app.api.media import router as media_router  # ЕЦО: Медиатека
# без _protected: check_account_access требует одиночный account_id,
# а медиатека owner-level — права проверяет каждая ручка сама
app.include_router(media_router)
from app.api.catalog import router as catalog_router  # ЕЦО: Каталог
app.include_router(catalog_router)
from app.api.photo import router as photo_router  # ЕЦО: AI-фотостудия
app.include_router(photo_router)
app.include_router(import_router.router, dependencies=_protected)
app.include_router(chat.router, dependencies=_protected)
app.include_router(avito_router, dependencies=_protected)
app.include_router(avito_public_router)
app.include_router(browser_gateway_router)
app.include_router(browser_gateway_transport_router)
app.include_router(browser_gateway_control_router, dependencies=_protected)
app.include_router(mass_editor_router, dependencies=_protected)
app.include_router(director_stats_router, dependencies=_protected)
app.include_router(support_router.router)
app.include_router(parser_router, dependencies=_protected)
app.include_router(storage_router, dependencies=_protected)
app.include_router(tasks_router, dependencies=_protected)
app.include_router(accounts_router, dependencies=_protected)
app.include_router(plan_items_router, dependencies=_protected)
app.include_router(sitebuild_router, dependencies=_protected)
app.include_router(messenger_router, dependencies=_protected)
app.include_router(messenger_prompts_router, dependencies=_protected)
app.include_router(auth_router)
app.include_router(manager_router)  # свои Depends(get_current_user) внутри; account_id у менеджера = None
from app.api.team import router as team_router
# без _protected: check_account_access требует одиночный account_id,
# а здесь работа со списком аккаунтов; права проверяются внутри ручек
app.include_router(team_router)
from app.api.action_log_api import router as action_log_router
# без _protected: check_account_access требует одиночный account_id,
# а доступ здесь проверяется внутри ручек по связям
app.include_router(action_log_router)
app.include_router(avito_categories_router, dependencies=_protected)
app.include_router(banners_router, dependencies=_protected)
app.include_router(crm_router, dependencies=_protected)
app.include_router(lead_notifications_router, dependencies=_protected)
app.include_router(lead_notifications_public_router)
app.include_router(prompts_router, dependencies=_protected)
app.include_router(landing_router)
app.include_router(software_site_router)
app.include_router(software_site_admin_router)
app.include_router(billing_router, dependencies=_protected)
from app.api.category_requests import router as category_requests_router  # noqa: E402
app.include_router(category_requests_router, dependencies=_protected)
from app.api.cpxpromo import router as cpxpromo_router
app.include_router(cpxpromo_router, dependencies=_protected)
from app.api.cpx_advisor import router as cpx_advisor_router
app.include_router(cpx_advisor_router, dependencies=_protected)
from app.api.payments import router as payments_router
app.include_router(payments_router, dependencies=_protected)
from app.api.legal import router as legal_router
app.include_router(legal_router)
from app.api.wallet import router as wallet_router
app.include_router(wallet_router)
app.include_router(calltracking_router, dependencies=_protected)
app.include_router(own_calltracking_router, dependencies=_protected)
app.include_router(own_calltracking_public_router)
app.include_router(mcn_phone_router, dependencies=_protected)
app.include_router(telephony_router, dependencies=_protected)
app.include_router(telephony_public_router)
app.include_router(revenue_analytics_router, dependencies=_protected)
app.include_router(revenue_tracker_public_router)
app.include_router(reliability_router)
app.include_router(control_plane_router)
app.include_router(nps_router, dependencies=_protected)
app.include_router(prompt_check_router, dependencies=_protected)
from app.api.economics import router as economics_router
from app.api.posting import router as posting_router
app.include_router(posting_router, dependencies=_protected)
app.include_router(economics_router, dependencies=_protected)

from app.api.qa import router as qa_router
app.include_router(qa_router, dependencies=_protected)

from app.api.home import router as home_router  # noqa: E402
from app.api import inbox_slots
from app.api import client_memory
from app.api import ai_bindings
from app.api.factory_new_client_preflight import router as factory_new_client_preflight_router
app.include_router(home_router, dependencies=_protected)
app.include_router(inbox_slots.router, dependencies=_protected)
from app.api import inbox_templates
app.include_router(inbox_templates.router, dependencies=_protected)
from app.api import inbox_export
app.include_router(inbox_export.router, dependencies=_protected)
from app.api import inbox_send_image
app.include_router(inbox_send_image.router, dependencies=_protected)
from app.api import inbox_daily
from app.api import admin_clients
app.include_router(inbox_daily.router, dependencies=_protected)
app.include_router(client_memory.router, dependencies=_protected)
from app.api import campaigns as campaigns_api
app.include_router(ai_bindings.router, dependencies=_protected)
app.include_router(factory_new_client_preflight_router)
app.include_router(admin_clients.router, dependencies=_protected)
# Экран «Возврат клиентов». Импорт рядом с подключением, чтобы не трогать
# общий блок импортов: он большой и правится параллельно другими задачами.
from app.api import reactivation as reactivation_api  # noqa: E402
app.include_router(reactivation_api.router, dependencies=_protected)
app.include_router(onboarding_router, dependencies=[Depends(get_current_user_or_internal)])

@app.get("/")
def root():
    return {"status": "ok"}


# --- BORIS Command Center (этап 1) ---
# Без dependencies=_protected: часть ручек работает без account_id,
# права проверяются внутри через command_policy.
from app.api.monitoring import router as monitoring_router  # noqa: E402
from app.api.command import router as command_router  # noqa: E402
app.include_router(command_router)
app.include_router(monitoring_router, dependencies=[Depends(get_current_user_or_internal)])


# ZERO_TOUCH_ONBOARDING_FACTORY_V1
from app.api.onboarding_factory import router as onboarding_factory_router
from app.api.factory_confirmations import router as factory_confirmations_router  # FACTORY_N_ROUTER_V1
from app.api.factory_launches import router as factory_launches_router  # ZERO_TOUCH_LAUNCH_ROUTER_V1
app.include_router(onboarding_factory_router)
app.include_router(factory_confirmations_router)  # FACTORY_N_ROUTER_V1
app.include_router(factory_launches_router)  # ZERO_TOUCH_LAUNCH_ROUTER_V1

# FACTORY_N12_OWNER_API_V1
from app.api.factory_owner import router as factory_owner_router
from app.api import ai_costs
from app.api import prospecting as prospecting_router
from app.api import prospecting_discovery as prospecting_discovery_router
from app.api import prospecting_scale as prospecting_scale_router
from app.api import service_marketplace as service_marketplace_router
from app.api import yandex_webmaster as yandex_webmaster_router
from app.api import prospecting_unified as prospecting_unified_router
from app.api import crowd_seo as crowd_seo_router
from app.api.category_ui import router as category_ui_router
from app.api.client_launch_control import router as client_launch_control_router
from app.api import factory_client_completion
from app.api import account_capabilities
from app.api import mop_training
from app.api.photo_factory import router as photo_factory_router
from app.api.photo_factory_v31 import router as photo_factory_v31_router
from app.api.photo_factory_v32 import router as photo_factory_v32_router
from app.api.photo_factory_v33 import router as photo_factory_v33_router

app.include_router(factory_owner_router)
app.include_router(ai_costs.router)
app.include_router(prospecting_router.router, dependencies=_protected)
app.include_router(prospecting_discovery_router.router, dependencies=_protected)
app.include_router(prospecting_scale_router.public_router)
app.include_router(prospecting_scale_router.router, dependencies=_protected)
app.include_router(service_marketplace_router.router, dependencies=_protected)
app.include_router(yandex_webmaster_router.router, dependencies=_protected)
app.include_router(prospecting_unified_router.router, dependencies=_protected)
app.include_router(crowd_seo_router.router, dependencies=_protected)
app.include_router(category_ui_router)
app.include_router(client_launch_control_router)
app.include_router(factory_client_completion.router, dependencies=_protected)

app.include_router(account_capabilities.router)
app.include_router(mop_training.router)
app.include_router(photo_factory_router)
app.include_router(photo_factory_v31_router)
app.include_router(photo_factory_v32_router)
app.include_router(photo_factory_v33_router)
# ---- BORIS AGENT GATEWAY ---------------------------------------------------
try:
    from app.ext_api.router import router as _gateway_router
    app.include_router(_gateway_router)
    from app.ext_api.orchestrator import start_inline_runner as _gw_runner
    _gw_runner()
except Exception as _gateway_err:  # прод не должен падать из-за шлюза агентов
    import logging as _lg
    _lg.getLogger("uvicorn.error").error("agent gateway not loaded: %s", _gateway_err)
# ---- /BORIS AGENT GATEWAY --------------------------------------------------







# AI_DIRECTOR_YANDEX_V1
from app.director.models import DirectConnection, DirectEntity, DirectStat, DirectKpiConfig, DirectAction  # create_all
from app.director.api import router as ai_director_router, public_router as ai_director_public_router
app.include_router(ai_director_router, dependencies=_protected)
app.include_router(ai_director_public_router)

# BORIS_VIDEO_FACTORY_V1 — one canonical video contour
from app.api import video_factory as video_factory_api
app.include_router(video_factory_api.router, dependencies=_protected)

# LEAD_RADAR_ROUTER_V1
from app.api.lead_radar import router as lead_radar_router
app.include_router(lead_radar_router)
