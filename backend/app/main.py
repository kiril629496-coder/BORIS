from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware

from app.db.base import Base
from app.db.session import engine

from app.api.campaigns import router as campaigns_router
from app.api import imports as import_router
from app.api import chat
from app.api import support as support_router
from app.api.avito import router as avito_router
from app.api.calltracking import router as calltracking_router
from app.api.nps import router as nps_router
from app.api.prompt_check import router as prompt_check_router
from app.api.avito import public_router as avito_public_router
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
from app.api.prompts import router as prompts_router
from app.models.saved_prompt import SavedPrompt  # чтобы create_all() увидел таблицу
from app.models.banner import Banner  # чтобы create_all() увидел таблицу banners
from app.api.landing import router as landing_router
from app.api.billing import router as billing_router
from app.telegram_bot import _telegram_poll_loop
from app.api.messenger import _messenger_poll_loop
from app.api.messenger import _reminder_loop
from app.api.messenger import router as messenger_router
from app.api.messenger_prompts import router as messenger_prompts_router
from app.api.auth import router as auth_router, get_current_user_or_internal, check_account_access
from app.api.onboarding import router as onboarding_router
from app.models.user import User  # импорт на уровне модуля — чтобы create_all() увидел таблицу users при старте
import threading as _threading

from fastapi.staticfiles import StaticFiles
import os as _os

app = FastAPI()

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

    from gigachat_pool import reset_giga_breaker
    reset_giga_breaker()
    print("STARTUP: предохранитель GigaChat (402) сброшен", flush=True)

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

_os.makedirs("/root/BORIS/backend/images", exist_ok=True)
app.mount("/images", StaticFiles(directory="/root/BORIS/backend/images"), name="images")

# Все роутеры ниже защищены Depends(get_current_user_or_internal): нужен либо валидный JWT,
# либо запрос с localhost (self-вызовы backend'а и cron-скрипты - см. auth.py). Публичные
# исключения: auth_router (login/register), landing_router (сам решает поэндпоинтно,
# GET /api/landing/content открыт для маркетинговой страницы) и avito_public_router
# (feed.xml/feed_preview.xml - их дёргает сам Avito и его валидатор, у них не может быть JWT).
_protected = [Depends(get_current_user_or_internal), Depends(check_account_access)]

app.include_router(campaigns_router, dependencies=_protected)
app.include_router(import_router.router, dependencies=_protected)
app.include_router(chat.router, dependencies=_protected)
app.include_router(avito_router, dependencies=_protected)
app.include_router(avito_public_router)
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
app.include_router(avito_categories_router, dependencies=_protected)
app.include_router(banners_router, dependencies=_protected)
app.include_router(prompts_router, dependencies=_protected)
app.include_router(landing_router)
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
app.include_router(ai_bindings.router, dependencies=_protected)
app.include_router(admin_clients.router, dependencies=_protected)
app.include_router(onboarding_router, dependencies=[Depends(get_current_user_or_internal)])

@app.get("/")
def root():
    return {"status": "ok"}
