"""BORIS Revenue Analytics — canonical revenue/attribution read model.
Extends existing CRM/calltracking/marketing data; does not create a second CRM.
"""
from fastapi import APIRouter, Depends
from app.api.auth import get_current_user_or_internal
from app.api.calltracking import _db_url, calltracking_stats
import psycopg2
import datetime
from sqlalchemy import text
from app.services.exception_observability import observe_suppressed

router = APIRouter(prefix="/api/revenue-analytics", tags=["revenue-analytics"])


def _actor_user_id(current_user):
    if current_user is None:
        return None
    return getattr(current_user, "id", None) or (current_user.get("id") if isinstance(current_user, dict) else None)


def _assert_revenue_account_access(account_id: str, current_user):
    """Scope Revenue Analytics to the same BORIS account ACL used by the product shell."""
    uid = _actor_user_id(current_user)
    if uid is None:
        return
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        owner = db.execute(text("SELECT owner_user_id FROM accounts WHERE account_id=:a"), {"a": account_id}).scalar()
        if owner is None:
            from fastapi import HTTPException
            raise HTTPException(404, "Аккаунт не найден")
        if int(owner or 0) == int(uid):
            return
        allowed = db.execute(text("SELECT 1 FROM user_account_access WHERE user_id=:u AND account_id=:a AND can_view=TRUE"), {"u": uid, "a": account_id}).first()
        if not allowed:
            from fastapi import HTTPException
            raise HTTPException(403, "Нет доступа к аккаунту")
    finally:
        db.close()


def _revenue_account_guard(account_id: str, current_user=Depends(get_current_user_or_internal)):
    _assert_revenue_account_access(account_id, current_user)
    return current_user


def _conn():
    u = _db_url()
    return psycopg2.connect(u) if u else None


def _live_call_count(account_id: str, days: int):
    """Real Avito call count with last-success fallback for transient provider/token failures."""
    days=max(1,min(int(days),90)); key=f"revenue_call_count_cache:{days}"
    try:
        data=calltracking_stats(account_id=account_id,days=days)
        if isinstance(data,dict) and data.get("status")=="ok":
            total=int(data.get("total",0)); answered=int(data.get("answered",0)); answered_key=key+":answered"
            try:
                from app.db.session import SessionLocal
                from app.models.storage import Storage
                db=SessionLocal(); row=db.query(Storage).filter(Storage.account_id==account_id,Storage.key==key).first(); answered_row=db.query(Storage).filter(Storage.account_id==account_id,Storage.key==answered_key).first()
                cached=int(row.value) if row and str(row.value or '').isdigit() else None
                # Avito can transiently return an empty successful payload around throttling.
                # A zero is impossible when real analyses exist in the same window, so keep the
                # last confirmed positive count rather than corrupting all Revenue widgets.
                if total==0 and cached and cached>0:
                    analyzed=db.execute(text("SELECT count(*) FROM call_analysis WHERE account_id=:a AND created_at>=now()-(:d||' days')::interval"),{'a':account_id,'d':days}).scalar() or 0
                    if analyzed:
                        db.close(); return cached
                if row: row.value=str(total)
                else: db.add(Storage(account_id=account_id,key=key,value=str(total)))
                if answered_row: answered_row.value=str(answered)
                else: db.add(Storage(account_id=account_id,key=answered_key,value=str(answered)))
                db.commit(); db.close()
            except Exception:
                try: db.close()
                except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=78)
            return total
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=80)
    try:
        from app.db.session import SessionLocal
        from app.models.storage import Storage
        db=SessionLocal(); row=db.query(Storage).filter(Storage.account_id==account_id,Storage.key==key).first(); value=row.value if row else None; db.close()
        return int(value) if value is not None else None
    except Exception:
        return None


def _live_answered_call_count(account_id: str, days: int):
    days=max(1,min(int(days),90)); key=f"revenue_call_count_cache:{days}:answered"
    try:
        from app.db.session import SessionLocal
        from app.models.storage import Storage
        db=SessionLocal(); row=db.query(Storage).filter(Storage.account_id==account_id,Storage.key==key).first(); value=row.value if row else None; db.close()
        return int(value) if value is not None else None
    except Exception:
        return None


def _scalar(cur, sql, args, default=0):
    try:
        cur.execute(sql, args); row = cur.fetchone(); return row[0] if row and row[0] is not None else default
    except Exception:
        cur.connection.rollback(); return default


def _canonical_analyzed_call_count(cur, account_id: str, days: int = 30):
    """Count AI-analyzed calls across legacy call_analysis + canonical Telephony, without duplicates."""
    legacy = int(_scalar(cur, "SELECT count(*) FROM call_analysis WHERE account_id=%s AND created_at>=now()-(%s||' days')::interval", (account_id, days), 0) or 0)
    telephony = int(_scalar(cur, """SELECT count(*) FROM telephony_calls tc
      WHERE tc.account_id=%s AND tc.started_at>=now()-(%s||' days')::interval
        AND tc.ai_analysis_status='done'
        AND NOT EXISTS (SELECT 1 FROM call_analysis ca WHERE ca.account_id=tc.account_id
          AND ca.call_id::text IN (coalesce(tc.provider_call_id,''),tc.id))""", (account_id, days), 0) or 0)
    return legacy + telephony


def _real_ad_spend(cur, account_id: str, days: int):
    """Verified ad spend by channel. Never mix planned/reserved budget with actual spend."""
    avito=None; direct=None
    try:
        cur.execute("""SELECT key,value FROM storage WHERE account_id=%s AND key LIKE 'kpi_budget_ledger:%%' AND substring(key from 'kpi_budget_ledger:(.*)$')::date >= current_date-(%s::int-1)""",(account_id,max(1,int(days))))
        total=0.0; found=False
        for _,raw in cur.fetchall():
            try:
                d=json.loads(raw or '{}')
                for op in d.get('operations',[]):
                    if op.get('status')=='committed' and op.get('actual_amount_rub') is not None:
                        total+=float(op.get('actual_amount_rub') or 0); found=True
            except Exception: continue
        avito=round(total,2) if found else None
    except Exception: cur.connection.rollback()
    try:
        direct_value=_scalar(cur,"SELECT sum(cost) FROM direct_stats_daily WHERE account_id=%s AND day::date >= current_date-(%s::int-1)",(account_id,max(1,int(days))),None)
        direct=round(float(direct_value),2) if direct_value is not None else None
    except Exception: cur.connection.rollback()
    known=[x for x in (avito,direct) if x is not None]
    return {"total_rub":round(sum(known),2) if known else None,"channels":{"avito_rub":avito,"yandex_direct_rub":direct},"quality":{"avito":"committed_actual_ledger" if avito is not None else "no_verified_spend","yandex_direct":"direct_stats_daily" if direct is not None else "no_synced_spend"}}


def _payment_predicate(alias="d", stage_alias="s"):
    return f"lower(coalesce({stage_alias}.name,''))=lower(%s)"

@router.get("/overview")
def overview(account_id: str, days: int = 30, _access=Depends(_revenue_account_guard)):
    """One owner-facing funnel. Missing integrations remain explicit, never fabricated."""
    c = _conn()
    if not c:
        return {"status":"error","message":"База данных недоступна"}
    cur = c.cursor()
    # Existing CRM is authoritative for deals; Avito calltracking is authoritative for calls.
    leads = _scalar(cur, "SELECT count(*) FROM messenger_leads WHERE account_id=%s AND created_at >= now()-(%s||' days')::interval", (account_id, days))
    contacts = _scalar(cur, "SELECT count(*) FROM messenger_leads WHERE account_id=%s AND created_at >= now()-(%s||' days')::interval AND has_phone IS TRUE", (account_id, days))
    calls = _live_call_count(account_id, days)
    # Canonical BORIS CRM is authoritative for deal/contract/payment revenue.
    cfg = _setup_get(account_id) if "_setup_get" in globals() else {}
    _, auto_contract, auto_payment = _auto_revenue_stages(cur, account_id, cfg) if "_auto_revenue_stages" in globals() else ([], "", "")
    contract_stage = str(cfg.get("contract_stage") or auto_contract or "Договор")
    payment_stage = str(cfg.get("payment_stage") or auto_payment or "Оплачено")
    deals = _scalar(cur, "SELECT count(*) FROM boris_crm_deals WHERE avito_account_id=%s AND created_at >= now()-(%s||' days')::interval", (account_id, days))
    contracts = _scalar(cur, """SELECT count(*) FROM boris_crm_deals d LEFT JOIN boris_crm_stages s ON s.id=d.stage_id
      WHERE d.avito_account_id=%s AND d.updated_at>=now()-(%s||' days')::interval
      AND (d.status='won' OR lower(coalesce(s.name,'')) IN (lower(%s),lower(%s)))""", (account_id, days, contract_stage, payment_stage))
    payments = _scalar(cur, """SELECT count(*) FROM boris_crm_deals d LEFT JOIN boris_crm_stages s ON s.id=d.stage_id
      WHERE d.avito_account_id=%s AND d.updated_at>=now()-(%s||' days')::interval
      AND lower(coalesce(s.name,''))=lower(%s)""", (account_id, days, payment_stage))
    revenue_kopeks = _scalar(cur, """SELECT coalesce(sum(d.amount_kopeks),0) FROM boris_crm_deals d LEFT JOIN boris_crm_stages s ON s.id=d.stage_id
      WHERE d.avito_account_id=%s AND d.updated_at>=now()-(%s||' days')::interval
      AND lower(coalesce(s.name,''))=lower(%s)""", (account_id, days, payment_stage))
    revenue_rub = round(float(revenue_kopeks or 0)/100.0, 2)
    # api_usage is BORIS operating cost, not advertising spend; keep the two concepts separate.
    boris_cost_rub = float(_scalar(cur, "SELECT coalesce(sum(cost_rub),0) FROM api_usage WHERE account_id=%s AND created_at >= now()-(%s||' days')::interval", (account_id, days), 0) or 0)
    spend = _real_ad_spend(cur, account_id, days); ad_spend_rub = spend.get("total_rub")
    email_activity = _scalar(cur, "SELECT count(*) FROM prospect_campaign_members m JOIN prospect_campaigns pc ON pc.id=m.campaign_id WHERE pc.account_id=%s AND m.created_at >= now()-(%s||' days')::interval", (account_id, days), 0)
    email_campaigns = _scalar(cur, "SELECT count(*) FROM prospect_campaigns WHERE account_id=%s", (account_id,), 0)
    website_not_used = _effective_website_mode(cur, account_id, cfg) == "not_used"
    from app.services.telephony_core import selected_provider as _selected_phone_provider
    phone_provider = _selected_phone_provider(account_id)
    c.close()
    integrations = [
      {"key":"avito","name":"Avito","status":"connected"},
      {"key":"crm","name":"CRM","status":"connected" if (leads or contacts or deals) else "waiting_data"},
      {"key":"calls","name":"Звонки","status":"connected" if calls is not None else "waiting_data"},
      {"key":"yandex_direct","name":"Яндекс Директ","status":"connected" if cfg.get("yandex_direct_enabled") else "connect"},
      {"key":"site","name":"Сайт / SEO","status":"not_used" if website_not_used else ("connected" if cfg.get("website_domain") else "connect")},
      {"key":"social","name":"Соцсети","status":"connected" if (cfg.get("vk_enabled") or cfg.get("telegram_enabled")) else "connect"},
      {"key":"telephony","name":"IP-телефония","status":"connected" if (calls is not None or phone_provider or cfg.get("telephony_provider")) else "connect"},
      {"key":"email","name":"Рассылка · почта","status":"connected" if email_activity else ("waiting_data" if email_campaigns else "not_used")},
      {"key":"telegram_mailing","name":"Рассылка · Telegram","status":"connected" if cfg.get("telegram_enabled") else "connect"},
    ]
    cac_rub = round(ad_spend_rub / contracts, 2) if ad_spend_rub is not None and contracts else None
    romi_pct = round((revenue_rub-ad_spend_rub)/ad_spend_rub*100,1) if ad_spend_rub and contracts else None
    return {"status":"ok","period_days":days,"funnel":{"leads":int(leads),"contacts":int(contacts),"calls":int(calls) if calls is not None else None,"deals":int(deals),"contracts":int(contracts),"payments":int(payments),"revenue_rub":revenue_rub,"boris_operating_cost_rub":round(boris_cost_rub,2),"ad_spend_rub":ad_spend_rub,"cac_rub":cac_rub,"romi_pct":romi_pct},"integrations":integrations,"data_quality":{"contracts":"crm_stage_or_won","payments":"crm_payment_stage","revenue":"crm_payment_stage_amount","boris_operating_cost":"api_usage","ad_spend":spend.get("quality"),"romi":"real_spend_and_revenue" if romi_pct is not None else "waiting_real_spend_or_contract"}}

@router.get("/architecture")
def architecture():
    return {"status":"ok","core":"BORIS Attribution Core","principles":["single_customer_journey","no_second_crm","multi_touch_ready","cost_to_contract","explicit_missing_data"],"sources":["avito","yandex_direct","seo","site","vk","telegram","telephony","crm"],"telephony_gateway":["zadarma","novofon","uis","mango","sip"]}

@router.get("/telephony-gateway")
def telephony_gateway(account_id: str, days: int = 30, _access=Depends(_revenue_account_guard)):
    """Truthful gateway status over existing calltracking. External providers are capabilities, not fake connections."""
    cfg=_setup_get(account_id) if "_setup_get" in globals() else {}
    live_calls=_live_call_count(account_id,days)
    from app.services.telephony_core import selected_provider as _selected_phone_provider
    selected=str(_selected_phone_provider(account_id) or cfg.get("telephony_provider") or "").strip().lower()
    providers=[]
    labels={"avito":"Avito Calltracking","zadarma":"Zadarma","novofon":"Novofon","uis":"UIS / CoMagic","mango":"MANGO","sip":"Другой SIP"}
    for key,label in labels.items():
        if key=="avito":
            status="connected" if live_calls is not None else "unavailable"
            reason=(f"{live_calls} звонков за {days} дней" if live_calls is not None else "Avito Calltracking недоступен для аккаунта")
        else:
            from app.services.telephony_adapters.registry import adapter_status
            adapter=adapter_status(key)
            if not adapter.get("implemented"):
                status="unavailable"
                reason="Production-адаптер провайдера ещё не реализован"
            elif selected==key:
                from app.services.telephony_core import provider_status as _phone_provider_status
                phone_status=_phone_provider_status(account_id)
                phone_state=str(phone_status.get("provider_state") or "")
                if phone_state=="connected":
                    status="connected"
                    reason="Production-доступ подтверждён health-check BORIS Phone"
                elif phone_state=="configured_unverified":
                    status="configured_unverified"
                    reason="Production-доступ сохранён, но health-check ещё не подтверждён"
                else:
                    status="credentials_required"
                    reason="Провайдер выбран, но production-доступ ещё не подтверждён"
            else:
                status="available"
                reason="Можно подключить после предоставления production-доступа"
        providers.append({"key":key,"name":label,"status":status,"selected":selected==key,"reason":reason})
    selected_row=next((x for x in providers if x.get("key")==selected),None) if selected else None
    active=selected if selected_row and selected_row.get("status")=="connected" else ("avito" if live_calls is not None else (selected or None))
    selected_connected=bool(selected_row and selected_row.get("status")=="connected")
    selected_caps=set()
    if selected:
        from app.services.telephony_adapters.registry import adapter_status as _adapter_status
        selected_caps=set(_adapter_status(selected).get("capabilities") or [])
    media_ready=bool(selected_connected and ({"media","webrtc","sip_media"} & selected_caps))
    return {"status":"ok","product_name":"BORIS Телефония","active_provider":active,"calls_30d":live_calls,"providers":providers,"external_credentials_required":bool(selected and selected!="avito" and not selected_connected),"brand_mode":str(cfg.get("telephony_brand_mode") or "boris"),"routing_mode":str(cfg.get("telephony_routing_mode") or "group"),"capabilities":{"virtual_numbers":True,"account_number_mapping":True,"single_or_multi_destination":True,"call_cost_accounting":True,"recordings":bool(cfg.get("telephony_recording_enabled",True) and selected_caps and "recordings" in selected_caps),"ai_analysis":bool(cfg.get("telephony_ai_analysis_enabled",True)),"webrtc_softphone":bool(cfg.get("telephony_webrtc_enabled",True) and media_ready),"mobile_softphone":bool(cfg.get("telephony_mobile_enabled",True) and media_ready),"sip":bool("sip" in selected_caps or "sip_media" in selected_caps),"white_label_ready":True},"routing_model":"virtual_number -> BORIS account/source -> SIP/PBX group -> manager -> CRM -> revenue attribution"}

# BORIS_REVENUE_SETUP_TRACKER_V1
import json, secrets
from fastapi import Body
from app.db.session import SessionLocal
from app.models.storage import Storage


def _setup_get(account_id: str):
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "revenue_analytics_setup_v1").first()
        if not row or not row.value:
            return {}
        try: return json.loads(row.value)
        except Exception: return {}
    finally: db.close()


def _setup_put(account_id: str, data: dict):
    db = SessionLocal()
    try:
        # REVENUE_SETUP_SINGLETON_RACE_GUARD_V1: concurrent first-open requests
        # must not mint two tracker keys. Lock this logical singleton, preserve
        # the oldest existing row (the key clients may already have received),
        # and collapse any historical duplicates deterministically.
        db.execute(text("select pg_advisory_xact_lock(hashtextextended(:k,0))"),
                   {"k": f"storage|{account_id}|revenue_analytics_setup_v1"})
        rows=(db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "revenue_analytics_setup_v1")
              .order_by(Storage.id.asc()).with_for_update().all())
        if rows:
            row=rows[0]
            current={}
            try: current=json.loads(row.value or '{}')
            except Exception: current={}
            merged=dict(data or {})
            if current.get('tracker_key'):
                merged['tracker_key']=current['tracker_key']
            row.value=json.dumps(merged,ensure_ascii=False)
            for dup in rows[1:]: db.delete(dup)
        else:
            db.add(Storage(account_id=account_id,key="revenue_analytics_setup_v1",value=json.dumps(data,ensure_ascii=False)))
        db.commit()
    finally: db.close()

@router.get("/setup")
def get_setup(account_id: str, _access=Depends(_revenue_account_guard)):
    cfg = _setup_get(account_id)
    if not cfg.get("tracker_key"):
        cfg["tracker_key"] = secrets.token_urlsafe(18)
        _setup_put(account_id, cfg)
    public = dict(cfg)
    return {"status":"ok","setup":public,"steps":["business","site","ads","telephony","calltracking","crm","funnel","test"]}

@router.post("/setup")
def save_setup(body: dict = Body(...), current_user=Depends(get_current_user_or_internal)):
    account_id = str(body.get("account_id") or "").strip()
    if not account_id: return {"status":"error","message":"account_id обязателен"}
    _assert_revenue_account_access(account_id, current_user)
    old = _setup_get(account_id)
    allowed = {"website_domain","currency","timezone","telephony_provider","calltracking_mode","funnel_stages","yandex_direct_enabled","metrika_enabled","vk_enabled","telegram_enabled","contract_stage","payment_stage","website_mode","telephony_brand_mode","telephony_routing_mode","telephony_recording_enabled","telephony_ai_analysis_enabled","telephony_webrtc_enabled","telephony_mobile_enabled"}
    for k in allowed:
        if k in body: old[k] = body[k]
    if not old.get("tracker_key"): old["tracker_key"] = secrets.token_urlsafe(18)
    _setup_put(account_id, old)
    return {"status":"ok","setup":old}

def _effective_website_mode(cur, account_id: str, cfg: dict):
    explicit=str(cfg.get("website_mode") or "").strip().lower()
    if explicit in ("used","not_used"): return explicit
    domain=str(cfg.get("website_domain") or "").strip()
    if domain: return "used"
    company_site=_scalar(cur,"SELECT company_website FROM accounts WHERE account_id=%s",(account_id,),"") if cur else ""
    return "used" if str(company_site or "").strip() else "not_used"

def _auto_revenue_stages(cur, account_id: str, cfg: dict):
    contract=str(cfg.get("contract_stage") or "").strip(); payment=str(cfg.get("payment_stage") or "").strip(); funnel=cfg.get("funnel_stages") or []
    if not funnel:
        try:
            cur.execute("SELECT DISTINCT s.name,s.position,s.semantic_type FROM boris_crm_stages s JOIN boris_crm_deals d ON d.pipeline_id=s.pipeline_id WHERE d.avito_account_id=%s ORDER BY s.position",(account_id,))
            rows=cur.fetchall(); funnel=[r[0] for r in rows if r[0]]; won=[r[0] for r in rows if str(r[2] or '').lower()=='won' and r[0]]; names=[str(r[0] or '') for r in rows]
            if not contract: contract=next((n for n in names if 'договор' in n.casefold()),'') or (won[0] if won else '')
            if not payment: payment=next((n for n in names if ('оплат' in n.casefold() or 'успеш' in n.casefold() or n.casefold() in ('реализовано','реализован'))),'') or (won[-1] if won else '')
        except Exception: cur.connection.rollback()
    return funnel,contract,payment

@router.get("/readiness")
def readiness(account_id: str, _access=Depends(_revenue_account_guard)):
    cfg = _setup_get(account_id)
    c = _conn(); cur = c.cursor() if c else None
    analyzed_calls = _canonical_analyzed_call_count(cur, account_id, 3650) if cur else 0
    live_calls = _live_call_count(account_id, 30)
    calls_available = live_calls is not None
    calls = live_calls if live_calls is not None else analyzed_calls
    events = _scalar(cur,"SELECT count(*) FROM revenue_events WHERE account_id=%s",(account_id,)) if cur else 0
    leads = _scalar(cur,"SELECT count(*) FROM messenger_leads WHERE account_id=%s",(account_id,)) if cur else 0
    crm_deals = _scalar(cur,"SELECT count(*) FROM boris_crm_deals WHERE avito_account_id=%s",(account_id,)) if cur else 0
    crm_activity = int(leads or 0) + int(crm_deals or 0)
    funnel_stages, contract_stage, payment_stage = _auto_revenue_stages(cur, account_id, cfg) if cur else (cfg.get("funnel_stages") or [], str(cfg.get("contract_stage") or ""), str(cfg.get("payment_stage") or ""))
    website_not_used = _effective_website_mode(cur, account_id, cfg) == "not_used"
    if c: c.close()
    from app.services.telephony_core import selected_provider as _selected_phone_provider
    telephony_ready = bool(_selected_phone_provider(account_id) or cfg.get("telephony_provider")) or calls_available
    calltracking_ready = bool(cfg.get("calltracking_mode")) or calls_available
    checks = [
      {"key":"crm","name":"CRM получает обращения и сделки","ok":bool(crm_activity)},
      {"key":"site","name":"Сайт учтён","ok":website_not_used or bool(cfg.get("website_domain")),"state":"not_used" if website_not_used else ("ready" if cfg.get("website_domain") else "required")},
      {"key":"tracker","name":"Web-трекинг учтён","ok":website_not_used or bool(events),"state":"not_used" if website_not_used else ("ready" if events else "waiting")},
      {"key":"calls","name":"Звонки поступают","ok":bool(calls)},
      {"key":"telephony","name":"Источник звонков доступен","ok":telephony_ready},
      {"key":"calltracking","name":"Коллтрекинг доступен","ok":calltracking_ready},
      {"key":"funnel","name":"Этапы воронки определены","ok":bool(funnel_stages)},
      {"key":"revenue","name":"Договор и оплата сопоставлены","ok":bool(contract_stage and payment_stage)},
    ]
    passed=sum(1 for x in checks if x["ok"])
    return {"status":"ok","passed":passed,"total":len(checks),"ready":passed==len(checks),"checks":checks,"resolved":{"funnel_stages":funnel_stages,"contract_stage":contract_stage or None,"payment_stage":payment_stage or None}}

@router.get("/tracker-snippet")
def tracker_snippet(account_id: str, _access=Depends(_revenue_account_guard)):
    cfg=_setup_get(account_id)
    key=cfg.get("tracker_key")
    if not key:
        key=secrets.token_urlsafe(18); cfg["tracker_key"]=key; _setup_put(account_id,cfg)
    src=f"https://boris-ai.pro/boris-tracker.js?aid={account_id}&k={key}"
    return {"status":"ok","src":src,"snippet":f'<script async src="{src}"></script>'}

# Public collector used by BORIS Tracker. Project key identifies the account;
# payload is deliberately bounded and contains no form values/PII.
from fastapi import Request, HTTPException
from app.models.revenue_event import RevenueEvent
public_router = APIRouter(prefix="/api/revenue-tracker", tags=["revenue-tracker"])

@public_router.get("/site-config")
def owner_site_tracker_config(request: Request):
    host = str(request.headers.get("x-forwarded-host") or request.headers.get("host") or "")
    host = host.split(",", 1)[0].strip().split(":", 1)[0].lower()
    if host not in {"boris-ai.pro", "www.boris-ai.pro"}:
        raise HTTPException(status_code=404, detail="tracker_config_not_found")
    from app.services.platform_roles import PLATFORM_OWNER_ID
    aid = f"__owner_revenue__:{int(PLATFORM_OWNER_ID)}"
    cfg = _setup_get(aid)
    key = str(cfg.get("tracker_key") or "")
    domain = str(cfg.get("website_domain") or "").strip().lower()
    if not key or domain not in {"boris-ai.pro", "www.boris-ai.pro"}:
        raise HTTPException(status_code=503, detail="tracker_not_configured")
    return {"status": "ok", "account_id": aid, "project_key": key}

@public_router.post("/event")
async def collect_event(request: Request, body: dict = Body(...)):
    aid = str(body.get("account_id") or "")[:255]
    key = str(body.get("project_key") or "")[:128]
    event_type = str(body.get("event_type") or "pageview")[:64]
    if event_type not in {"pageview","click_phone","form_submit","lead","custom"}:
        return {"status":"ignored","reason":"unsupported_event"}
    cfg = _setup_get(aid) if aid else {}
    if not aid or not key or key != str(cfg.get("tracker_key") or ""):
        raise HTTPException(status_code=403, detail="invalid_tracker_key")
    origin = str(request.headers.get("origin") or "")[:500]
    configured_domain = str(cfg.get("website_domain") or "").strip().lower().replace("https://","").replace("http://","").strip("/")
    if configured_domain and origin:
        try:
            from urllib.parse import urlparse
            origin_host = (urlparse(origin).hostname or "").lower()
            base_host = configured_domain.split('/')[0].split(':')[0]
            if origin_host != base_host and not origin_host.endswith('.'+base_host):
                raise HTTPException(status_code=403, detail="origin_mismatch")
        except Exception:
            raise HTTPException(status_code=403, detail="origin_invalid")
    def s(name, n): return str(body.get(name) or "")[:n] or None
    db = SessionLocal()
    try:
        ev = RevenueEvent(
            account_id=aid, visitor_id=s("visitor_id",96) or "unknown", session_id=s("session_id",96) or "unknown",
            event_type=event_type, source=s("source",128), medium=s("medium",128), campaign=s("campaign",255),
            content=s("content",255), term=s("term",255), yclid=s("yclid",255), landing_url=s("landing_url",2000),
            page_url=s("page_url",2000), referrer=s("referrer",2000), payload={"origin":origin} if origin else {},
        )
        db.add(ev); db.commit()
        return {"status":"ok"}
    finally: db.close()

# BORIS_REVENUE_ATTRIBUTION_CORE_V2_20260830
from collections import defaultdict


def _event_source(row):
    source = (row.get("source") or "").strip().lower()
    ref = (row.get("referrer") or "").strip().lower()
    if source: return source
    if "yandex." in ref: return "seo_yandex"
    if "google." in ref: return "seo_google"
    if "vk.com" in ref: return "vk"
    if "t.me" in ref or "telegram" in ref: return "telegram"
    return "direct"

@router.get("/sources")
def revenue_sources(account_id: str, days: int = 30, _access=Depends(_revenue_account_guard)):
    c=_conn()
    if not c: return {"status":"error","message":"База данных недоступна"}
    cur=c.cursor()
    try:
        cur.execute("""SELECT coalesce(nullif(source,''),'') source, coalesce(referrer,''), count(*) events,
          count(distinct visitor_id) visitors, count(distinct session_id) sessions,
          count(*) filter (where event_type in ('form_submit','lead','click_phone')) intents
          FROM revenue_events WHERE account_id=%s AND occurred_at>=now()-(%s||' days')::interval
          GROUP BY 1,2 ORDER BY events DESC LIMIT 200""",(account_id,days))
        agg=defaultdict(lambda:{"events":0,"visitors":0,"sessions":0,"intents":0})
        for source,ref,events,visitors,sessions,intents in cur.fetchall():
            key=_event_source({"source":source,"referrer":ref})
            x=agg[key]; x["events"]+=int(events or 0); x["visitors"]+=int(visitors or 0); x["sessions"]+=int(sessions or 0); x["intents"]+=int(intents or 0)
        items=[]
        for name,x in sorted(agg.items(),key=lambda kv:kv[1]["intents"],reverse=True):
            items.append({"source":name,"kind":"traffic",**x,"intent_rate_pct":round((x["intents"]/x["sessions"]*100),1) if x["sessions"] else 0})
        # Reuse canonical CRM deal sources when no website tracker is present.
        cfg=_setup_get(account_id); _, contract_stage, payment_stage=_auto_revenue_stages(cur,account_id,cfg)
        contract_stage=str(cfg.get("contract_stage") or contract_stage or "Договор"); payment_stage=str(cfg.get("payment_stage") or payment_stage or "Оплачено")
        cur.execute("""SELECT coalesce(nullif(btrim(d.source),''),'unknown') source,count(*) deals,count(distinct d.contact_id) contacts,
          count(*) FILTER (WHERE d.status='won' OR lower(coalesce(s.name,'')) IN (lower(%s),lower(%s))) contracts
          FROM boris_crm_deals d LEFT JOIN boris_crm_stages s ON s.id=d.stage_id
          WHERE d.avito_account_id=%s AND d.updated_at>=now()-(%s||' days')::interval GROUP BY 1""",(contract_stage,payment_stage,account_id,days))
        for src,deals,contacts,contracts in cur.fetchall():
            existing=next((x for x in items if x.get("source")==src),None)
            if existing:
                existing["crm_deals"]=int(deals or 0); existing["contracts"]=int(contracts or 0)
            else:
                d=int(deals or 0); ct=int(contacts or 0); co=int(contracts or 0)
                items.append({"source":src,"kind":"crm","events":d,"visitors":ct,"sessions":d,"intents":co,"intent_rate_pct":round(co/d*100,1) if d else 0,"crm_deals":d,"contracts":co})
        # Reuse the canonical B2B email campaign tables as an attribution source.
        sent=_scalar(cur,"SELECT count(*) FROM prospect_campaign_members m JOIN prospect_campaigns pc ON pc.id=m.campaign_id WHERE pc.account_id=%s AND m.sent_at>=now()-(%s||' days')::interval",(account_id,days),0)
        replies=_scalar(cur,"SELECT count(*) FROM prospect_campaign_members m JOIN prospect_campaigns pc ON pc.id=m.campaign_id WHERE pc.account_id=%s AND m.replied_at>=now()-(%s||' days')::interval",(account_id,days),0)
        if sent or replies:
            items.append({"source":"email_outreach","kind":"outreach","events":int(sent),"visitors":int(sent),"sessions":int(sent),"intents":int(replies),"intent_rate_pct":round((int(replies)/int(sent)*100),1) if sent else 0,"sent":int(sent),"replies":int(replies)})
        items.sort(key=lambda x:x.get("intents",0),reverse=True)
        return {"status":"ok","period_days":days,"items":items}
    finally:c.close()

@router.get("/journeys")
def journeys(account_id: str, days: int = 30, limit: int = 50, _access=Depends(_revenue_account_guard)):
    c=_conn()
    if not c:return {"status":"error","message":"База данных недоступна"}
    cur=c.cursor()
    try:
        cur.execute("""SELECT visitor_id,session_id,event_type,source,medium,campaign,term,landing_url,page_url,referrer,occurred_at
          FROM revenue_events WHERE account_id=%s AND occurred_at>=now()-(%s||' days')::interval
          ORDER BY occurred_at DESC LIMIT %s""",(account_id,days,max(1,min(limit*12,1200))))
        grouped={}
        for r in cur.fetchall():
            vid=r[0]; j=grouped.setdefault(vid,{"visitor_id":vid,"touches":[],"first_touch":None,"last_touch":None,"intent":False})
            touch={"session_id":r[1],"event_type":r[2],"source":_event_source({"source":r[3],"referrer":r[9]}),"medium":r[4],"campaign":r[5],"term":r[6],"url":r[8] or r[7],"occurred_at":r[10].isoformat() if r[10] else None}
            j["touches"].append(touch); j["intent"] = j["intent"] or r[2] in ("form_submit","lead","click_phone")
        # Reuse the canonical Avito chat -> CRM deal link as a customer journey.
        # This closes the real path from an incoming Avito conversation to a CRM deal
        # without creating a second tracking or CRM engine.
        cur.execute("""SELECT d.id,d.avito_chat_id,d.source,d.created_at,d.updated_at,d.status,coalesce(s.name,''),
          l.created_at,l.has_phone
          FROM boris_crm_deals d
          LEFT JOIN boris_crm_stages s ON s.id=d.stage_id
          LEFT JOIN messenger_leads l ON l.account_id=d.avito_account_id AND l.avito_chat_id=d.avito_chat_id
          WHERE d.avito_account_id=%s AND d.updated_at>=now()-(%s||' days')::interval
          ORDER BY d.updated_at DESC LIMIT %s""",(account_id,days,max(1,min(limit,100))))
        cfg=_setup_get(account_id); contract_stage=str(cfg.get("contract_stage") or "Договор").casefold(); payment_stage=str(cfg.get("payment_stage") or "Оплачено").casefold()
        for deal_id,chat_id,source,created,updated,status,stage_name,lead_created,has_phone in cur.fetchall():
            key=f"crm:{deal_id}"; src=(source or "avito").strip().lower() or "avito"
            touches=[]
            if lead_created:
                touches.append({"session_id":chat_id,"event_type":"lead","source":src,"medium":"messenger","campaign":None,"term":None,"url":None,"occurred_at":lead_created.isoformat()})
                if has_phone: touches.append({"session_id":chat_id,"event_type":"contact","source":src,"medium":"messenger","campaign":None,"term":None,"url":None,"occurred_at":lead_created.isoformat()})
            touches.append({"session_id":chat_id,"event_type":"deal","source":src,"medium":"crm","campaign":None,"term":None,"url":None,"occurred_at":created.isoformat() if created else None})
            stage=(stage_name or '').casefold()
            if status=='won' or stage in (contract_stage,payment_stage):
                touches.append({"session_id":chat_id,"event_type":"contract","source":src,"medium":"crm","campaign":None,"term":None,"url":None,"occurred_at":updated.isoformat() if updated else None})
            if stage==payment_stage:
                touches.append({"session_id":chat_id,"event_type":"payment","source":src,"medium":"crm","campaign":None,"term":None,"url":None,"occurred_at":updated.isoformat() if updated else None})
            grouped[key]={"visitor_id":key,"kind":"crm","deal_id":deal_id,"chat_id":chat_id,"touches":touches,"first_touch":touches[0] if touches else None,"last_touch":touches[-1] if touches else None,"intent":True}
        # Canonical calltracking sync already writes each Avito call into boris_crm_activities.
        # Reuse those activities in the same journey instead of inventing a parallel call journey store.
        cur.execute("""SELECT a.id,a.deal_id,a.contact_id,a.created_at,a.metadata_json
          FROM boris_crm_activities a
          WHERE a.source='avito_calltracking'
            AND coalesce(a.metadata_json->>'account_id','')=%s
            AND a.created_at>=now()-(%s||' days')::interval
          ORDER BY a.created_at DESC LIMIT %s""",(account_id,days,max(1,min(limit*4,400))))
        for activity_id,deal_id,contact_id,created,metadata in cur.fetchall():
            meta=metadata if isinstance(metadata,dict) else {}
            key=f"crm:{deal_id}" if deal_id is not None else f"call:{activity_id}"
            j=grouped.setdefault(key,{"visitor_id":key,"kind":"crm" if deal_id is not None else "call","deal_id":deal_id,"contact_id":contact_id,"touches":[],"first_touch":None,"last_touch":None,"intent":True})
            call_id=meta.get("call_id") or activity_id
            j["touches"].append({"session_id":str(call_id),"event_type":"call","source":"avito_calltracking","medium":"phone","campaign":None,"term":None,"url":None,"occurred_at":created.isoformat() if created else None,"missed":bool(meta.get("missed")),"analysis_available":bool(meta.get("analysis_available"))})
        out=[]
        for j in grouped.values():
            j["touches"].sort(key=lambda x:x["occurred_at"] or "")
            j["first_touch"]=j["touches"][0] if j["touches"] else None; j["last_touch"]=j["touches"][-1] if j["touches"] else None
            out.append(j)
        out.sort(key=lambda j:(j["last_touch"] or {}).get("occurred_at") or "",reverse=True)
        return {"status":"ok","items":out[:max(1,min(limit,100))]}
    finally:c.close()

@router.get("/attribution")
def attribution(account_id: str, days: int = 30, _access=Depends(_revenue_account_guard)):
    c=_conn()
    if not c:return {"status":"error","message":"База данных недоступна"}
    cur=c.cursor()
    try:
        cur.execute("""SELECT visitor_id,event_type,source,referrer,occurred_at FROM revenue_events
          WHERE account_id=%s AND occurred_at>=now()-(%s||' days')::interval ORDER BY visitor_id,occurred_at""",(account_id,days))
        visitors=defaultdict(list)
        for vid,etype,source,ref,ts in cur.fetchall(): visitors[vid].append({"event_type":etype,"source":_event_source({"source":source,"referrer":ref}),"ts":ts})
        # Merge existing CRM/calltracking touches into the same attribution model.
        cur.execute("""SELECT d.id,
          CASE WHEN lower(coalesce(a.source,'')) IN ('avito','avito_calltracking','email_outreach','telegram','yandex_direct','vk','seo_yandex','seo_google','site')
               THEN lower(a.source) ELSE lower(coalesce(nullif(btrim(d.source),''),'unknown')) END source,
          a.created_at FROM boris_crm_activities a
          JOIN boris_crm_deals d ON d.id=a.deal_id
          WHERE d.avito_account_id=%s AND a.created_at>=now()-(%s||' days')::interval
          AND nullif(btrim(a.source),'') IS NOT NULL""",(account_id,days))
        for deal_id,source,ts in cur.fetchall():
            visitors[f"crm:{deal_id}"].append({"event_type":"touch","source":str(source).strip().lower(),"ts":ts})
        cur.execute("""SELECT id,source,created_at FROM boris_crm_deals
          WHERE avito_account_id=%s AND updated_at>=now()-(%s||' days')::interval
          AND nullif(btrim(source),'') IS NOT NULL""",(account_id,days))
        for deal_id,source,ts in cur.fetchall():
            visitors[f"crm:{deal_id}"].append({"event_type":"deal","source":str(source).strip().lower(),"ts":ts})
        first=defaultdict(int); last=defaultdict(int); last_non_direct=defaultdict(int); assisted=defaultdict(int); converted=0
        for touches in visitors.values():
            touches.sort(key=lambda t:t.get("ts") or datetime.datetime.min)
            meaningful=[t for t in touches if t["source"]]
            if not meaningful: continue
            intents=[i for i,t in enumerate(touches) if t["event_type"] in ("form_submit","lead","click_phone","deal","contract","payment")]
            if not intents: continue
            converted+=1; end=intents[-1]; path=touches[:end+1]
            first[path[0]["source"]]+=1; last[path[-1]["source"]]+=1
            non_direct=[t for t in path if t["source"] not in ("direct","unknown")]
            primary=(non_direct[-1] if non_direct else path[-1])["source"]
            last_non_direct[primary]+=1
            edge_sources={path[0]["source"],path[-1]["source"]}
            for s in {t["source"] for t in path[1:-1]}:
                if s not in edge_sources: assisted[s]+=1
        sources=sorted(set(first)|set(last)|set(last_non_direct)|set(assisted))
        cfg=_setup_get(account_id); _, auto_contract, auto_payment = _auto_revenue_stages(cur, account_id, cfg); contract_stage=str(cfg.get("contract_stage") or auto_contract or "Договор"); payment_stage=str(cfg.get("payment_stage") or auto_payment or "Оплачено")
        cur.execute("""SELECT coalesce(nullif(btrim(d.source),''),'unknown') source,
          count(*) contracts,
          count(*) FILTER (WHERE lower(coalesce(s.name,''))=lower(%s)) payments,
          coalesce(sum(d.amount_kopeks) FILTER (WHERE lower(coalesce(s.name,''))=lower(%s)),0) paid_kopeks
          FROM boris_crm_deals d LEFT JOIN boris_crm_stages s ON s.id=d.stage_id
          WHERE d.avito_account_id=%s AND d.updated_at>=now()-(%s||' days')::interval
          AND (d.status='won' OR lower(coalesce(s.name,'')) IN (lower(%s),lower(%s)))
          GROUP BY 1 ORDER BY contracts DESC""",(payment_stage,payment_stage,account_id,days,contract_stage,payment_stage))
        revenue_by_source=[{"source":r[0],"contracts":int(r[1] or 0),"payments":int(r[2] or 0),"revenue_rub":round(float(r[3] or 0)/100,2)} for r in cur.fetchall()]
        spend=_real_ad_spend(cur,account_id,days); channel_spend=spend.get("channels") or {}
        spend_map={"avito":channel_spend.get("avito_rub"),"yandex_direct":channel_spend.get("yandex_direct_rub")}
        by_source={str(x["source"]):x for x in revenue_by_source}
        for src,amount in spend_map.items():
            if amount is None: continue
            row=by_source.setdefault(src,{"source":src,"contracts":0,"payments":0,"revenue_rub":0.0})
            row["ad_spend_rub"]=round(float(amount),2)
        for row in by_source.values():
            spend_rub=row.get("ad_spend_rub")
            contracts_n=int(row.get("contracts") or 0); revenue_n=float(row.get("revenue_rub") or 0)
            row["cac_rub"]=round(float(spend_rub)/contracts_n,2) if spend_rub is not None and contracts_n else None
            row["romi_pct"]=round((revenue_n-float(spend_rub))/float(spend_rub)*100,1) if spend_rub else None
        revenue_by_source=sorted(by_source.values(),key=lambda x:(float(x.get("revenue_rub") or 0),int(x.get("contracts") or 0)),reverse=True)
        return {"status":"ok","converted_visitors":converted,"models":{"first_touch":[{"source":s,"conversions":first[s]} for s in sources],"last_touch":[{"source":s,"conversions":last[s]} for s in sources],"last_non_direct":[{"source":s,"conversions":last_non_direct[s]} for s in sources],"assisted":[{"source":s,"conversions":assisted[s]} for s in sources]},"revenue_by_source":revenue_by_source,"spend_quality":spend.get("quality"),"boris_primary":"last_non_direct"}
    finally:c.close()

@router.get("/guardian")
def guardian(account_id: str, _access=Depends(_revenue_account_guard)):
    cfg=_setup_get(account_id); c=_conn(); cur=c.cursor() if c else None
    events=_scalar(cur,"SELECT count(*) FROM revenue_events WHERE account_id=%s AND occurred_at>=now()-interval '24 hours'",(account_id,)) if cur else 0
    analyzed_calls=_canonical_analyzed_call_count(cur, account_id, 30) if cur else 0
    live_calls=_live_call_count(account_id,30); live_answered=_live_answered_call_count(account_id,30)
    calls=live_calls if live_calls is not None else analyzed_calls
    detailed_calls=int(_scalar(cur,"SELECT count(*) FROM telephony_calls WHERE account_id=%s AND started_at>=now()-interval '30 days'",(account_id,),0) or 0) if cur else 0
    analysis_input_mode="detailed" if (detailed_calls or analyzed_calls) else ("aggregate_only" if live_calls else "no_calls")
    analysis_denominator=(live_answered if live_answered is not None else live_calls) if analysis_input_mode=="detailed" else None
    pending_call_analysis=max(0,int(analysis_denominator)-int(analyzed_calls)) if analysis_denominator is not None else None
    call_analysis_coverage=(round(min(100.0,int(analyzed_calls)/int(analysis_denominator)*100),1) if analysis_denominator else (100.0 if analyzed_calls else None)) if analysis_input_mode=="detailed" else None
    call_source="Avito Calltracking" if live_calls is not None else (str(cfg.get("telephony_provider") or "телефония"))
    spend=_real_ad_spend(cur,account_id,30) if cur else {"total_rub":None,"channels":{},"quality":{}}; ad_spend=spend.get("total_rub")
    _, auto_contract, auto_payment = _auto_revenue_stages(cur, account_id, cfg) if cur else ([], "", "")
    contract_stage=str(cfg.get("contract_stage") or auto_contract or "Договор"); payment_stage=str(cfg.get("payment_stage") or auto_payment or "Оплачено")
    won=_scalar(cur,"""SELECT count(*) FROM boris_crm_deals d LEFT JOIN boris_crm_stages s ON s.id=d.stage_id WHERE d.avito_account_id=%s AND d.updated_at>=now()-interval '30 days' AND (d.status='won' OR lower(coalesce(s.name,'')) IN (lower(%s),lower(%s)))""",(account_id,contract_stage,payment_stage),0) if cur else 0
    unattributed=_scalar(cur,"""SELECT count(*) FROM boris_crm_deals d LEFT JOIN boris_crm_stages s ON s.id=d.stage_id WHERE d.avito_account_id=%s AND d.updated_at>=now()-interval '30 days' AND (d.status='won' OR lower(coalesce(s.name,'')) IN (lower(%s),lower(%s))) AND (d.source IS NULL OR btrim(d.source)='')""",(account_id,contract_stage,payment_stage),0) if cur else 0
    email_activity=_scalar(cur,"SELECT count(*) FROM prospect_campaign_members m JOIN prospect_campaigns pc ON pc.id=m.campaign_id WHERE pc.account_id=%s AND m.created_at>=now()-interval '30 days'",(account_id,),0) if cur else 0
    email_campaigns=_scalar(cur,"SELECT count(*) FROM prospect_campaigns WHERE account_id=%s",(account_id,),0) if cur else 0
    stale_leads=_scalar(cur,"SELECT count(*) FROM messenger_leads WHERE account_id=%s AND COALESCE(has_phone,false)=false AND lower(coalesce(stage,'')) IN ('в процессе','новый','новый лид') AND COALESCE(last_msg_at,0)>0 AND COALESCE(last_msg_at,0)<extract(epoch from now()-interval '24 hours')",(account_id,),0) if cur else 0
    website_not_used = _effective_website_mode(cur, account_id, cfg) == "not_used"
    if c:c.close()
    checks=[
      {"key":"tracker_24h","severity":"ok" if website_not_used else ("warning" if not cfg.get("website_domain") else ("critical" if not events else "ok")),"message":"Сайт не используется — web-трекинг не требуется" if website_not_used else ("Сайт ещё не подключён" if not cfg.get("website_domain") else ("Нет событий Tracker за 24 часа" if not events else "Tracker получает данные"))},
      {"key":"calls","severity":"ok" if calls else "warning","message":f"{call_source}: {int(calls)} звонков за 30 дней" if calls else ("Avito Calltracking доступен, но звонков за 30 дней нет" if live_calls is not None else "Источник звонков ещё не подключён")},
      {"key":"call_ai_coverage","severity":"warning" if analysis_input_mode=="aggregate_only" else ("ok" if analysis_input_mode=="no_calls" or pending_call_analysis==0 else "warning"),"message":("Доступна только агрегированная статистика звонков; для AI-разбора нужны записи или детальные события звонков" if analysis_input_mode=="aggregate_only" else ("За 30 дней звонков нет — разбор не требуется" if analysis_input_mode=="no_calls" else (f"AI-разобрано {int(analyzed_calls)} из {int(analysis_denominator)} отвеченных звонков ({call_analysis_coverage}%)" if pending_call_analysis==0 else f"AI-разобрано {int(analyzed_calls)} из {int(analysis_denominator)} отвеченных звонков ({call_analysis_coverage}%), ждут разбора: {int(pending_call_analysis)}")))},
      {"key":"attribution","severity":"ok" if website_not_used or cfg.get("website_domain") else "warning","message":"Web-атрибуция не требуется" if website_not_used else ("Укажите домен для web-атрибуции" if not cfg.get("website_domain") else "Web-атрибуция включена")},
      {"key":"revenue_mapping","severity":"ok" if (contract_stage and payment_stage) else "warning","message":f"Этапы определены: {contract_stage} → {payment_stage}" if (contract_stage and payment_stage) else "Сопоставьте этапы договора и оплаты"},
      {"key":"ad_spend","severity":"warning" if ad_spend is None else "ok","message":"Нет подтверждённых расходов рекламы за 30 дней" if ad_spend is None else f"Расход рекламы подтверждён: {ad_spend:.2f} ₽"},
      {"key":"contract_attribution","severity":"critical" if unattributed else ("ok" if won else "warning"),"message":f"Договоров без источника: {int(unattributed)}" if unattributed else ("Договоры связаны с источниками" if won else "За 30 дней нет договоров для проверки атрибуции")},
      {"key":"lead_sla","severity":"warning" if stale_leads else "ok","message":f"Лидов без результата более 24 часов: {int(stale_leads)}" if stale_leads else "Просроченных необработанных лидов не найдено"},
      {"key":"email_outreach","severity":"ok" if (email_activity or not email_campaigns) else "warning","message":f"Email-рассылка участвует: {int(email_activity)} контактов" if email_activity else ("Email-рассылка не используется — не требуется" if not email_campaigns else "Email-рассылка подключена, но пока не дала данных этому аккаунту")},
    ]
    return {"status":"ok","healthy":all(x["severity"]=="ok" for x in checks),"checks":checks}

# BORIS_REVENUE_FULL_PRODUCT_SLICE_20260830
@router.get("/contracts")
def revenue_contracts(account_id: str, days: int = 30, limit: int = 100, _access=Depends(_revenue_account_guard)):
    c=_conn()
    if not c:return {"status":"error","message":"База данных недоступна"}
    cur=c.cursor()
    try:
        cfg=_setup_get(account_id); _, auto_contract, auto_payment = _auto_revenue_stages(cur, account_id, cfg); contract_stage=str(cfg.get("contract_stage") or auto_contract or "Договор"); payment_stage=str(cfg.get("payment_stage") or auto_payment or "Оплачено")
        cur.execute("""SELECT d.id,d.title,d.status,d.amount_kopeks,d.currency,d.source,d.source_ref,d.created_at,d.updated_at,coalesce(s.name,'')
          FROM boris_crm_deals d LEFT JOIN boris_crm_stages s ON s.id=d.stage_id
          WHERE d.avito_account_id=%s AND d.updated_at>=now()-(%s||' days')::interval
          ORDER BY d.updated_at DESC LIMIT %s""",(account_id,days,max(1,min(limit,300))))
        items=[]
        for r in cur.fetchall():
            stage_name=r[9] or ''; is_payment=(stage_name.casefold()==payment_stage.casefold()); is_contract=(r[2]=='won' or is_payment or stage_name.casefold()==contract_stage.casefold())
            items.append({"id":r[0],"title":r[1],"status":r[2],"stage_name":stage_name,"amount_rub":round(float(r[3] or 0)/100,2),"currency":r[4],"source":r[5],"source_ref":r[6],"created_at":r[7].isoformat() if r[7] else None,"updated_at":r[8].isoformat() if r[8] else None,"is_contract":is_contract,"is_payment":is_payment})
        return {"status":"ok","items":items,"contracts":sum(1 for x in items if x["is_contract"]),"payments":sum(1 for x in items if x["is_payment"]),"revenue_rub":round(sum(x["amount_rub"] for x in items if x["is_payment"]),2)}
    finally:c.close()

@router.get("/calls")
def revenue_calls(account_id: str, days: int = 30, limit: int = 100, _access=Depends(_revenue_account_guard)):
    c=_conn()
    if not c:return {"status":"error","message":"База данных недоступна"}
    cur=c.cursor()
    try:
        row_limit=max(1,min(limit,300))
        cur.execute("""SELECT call_id,created_at,minutes,analysis FROM call_analysis
          WHERE account_id=%s AND created_at>=now()-(%s||' days')::interval ORDER BY created_at DESC LIMIT %s""",(account_id,days,row_limit))
        items=[]
        for call_id,created,minutes,analysis in cur.fetchall():
            a=analysis if isinstance(analysis,dict) else {}
            checklist=a.get("checklist") or []
            items.append({"call_id":call_id,"created_at":created.isoformat() if created else None,"minutes":round(float(minutes or 0),1),"client":a.get("client_name") or "","role":a.get("role") or "","score":a.get("score"),"recommendation":a.get("recommendation") or "","checklist_done":sum(1 for x in checklist if x.get("done")),"checklist_total":len(checklist),"strong_moments":a.get("strong_moments") or [],"growth_points":a.get("growth_points") or [],"analysis_source":"call_analysis"})
        cur.execute("""SELECT tc.id,tc.provider_call_id,tc.started_at,tc.talk_duration_sec,tc.summary,tc.next_action,tc.qualification,
                 coalesce(r.analysis_json,'{}'::jsonb),q.total_score,q.strengths_json,q.gaps_json
          FROM telephony_calls tc
          LEFT JOIN LATERAL (SELECT analysis_json FROM telephony_recordings tr WHERE tr.account_id=tc.account_id AND tr.call_id=tc.id AND tr.analysis_status='done' ORDER BY tr.updated_at DESC LIMIT 1) r ON true
          LEFT JOIN LATERAL (SELECT total_score,strengths_json,gaps_json FROM telephony_call_quality tq WHERE tq.account_id=tc.account_id AND tq.call_id=tc.id ORDER BY tq.updated_at DESC LIMIT 1) q ON true
          WHERE tc.account_id=%s AND tc.started_at>=now()-(%s||' days')::interval AND tc.ai_analysis_status='done'
            AND NOT EXISTS (SELECT 1 FROM call_analysis ca WHERE ca.account_id=tc.account_id AND ca.call_id::text IN (coalesce(tc.provider_call_id,''),tc.id))
          ORDER BY tc.started_at DESC LIMIT %s""",(account_id,days,row_limit))
        for call_id,provider_call_id,created,duration_sec,summary,next_action,qualification,analysis,total_score,strengths,gaps in cur.fetchall():
            a=analysis if isinstance(analysis,dict) else {}
            checklist=a.get("checklist") or []
            items.append({"call_id":provider_call_id or call_id,"telephony_call_id":call_id,"created_at":created.isoformat() if created else None,"minutes":round(float(duration_sec or 0)/60.0,1),"client":a.get("client_name") or "","role":a.get("role") or qualification or "","score":a.get("score") if a.get("score") is not None else total_score,"recommendation":a.get("recommendation") or next_action or summary or "","checklist_done":sum(1 for x in checklist if isinstance(x,dict) and x.get("done")),"checklist_total":len(checklist),"strong_moments":a.get("strong_moments") or strengths or [],"growth_points":a.get("growth_points") or gaps or [],"analysis_source":"telephony"})
        items.sort(key=lambda x:x.get("created_at") or "",reverse=True); items=items[:row_limit]
        live_total=_live_call_count(account_id,days); live_answered=_live_answered_call_count(account_id,days)
        analyzed=_canonical_analyzed_call_count(cur,account_id,days); analysis_denominator=live_answered if live_answered is not None else live_total
        cur.execute("SELECT count(*) FROM telephony_calls WHERE account_id=%s AND started_at>=now()-(%s||' days')::interval",(account_id,days))
        detailed_calls=int((cur.fetchone() or [0])[0] or 0)
        analysis_input_mode="detailed" if (detailed_calls or analyzed) else ("aggregate_only" if live_total else "no_calls")
        coverage=(round(min(100.0,analyzed/analysis_denominator*100),1) if analysis_denominator else (100.0 if analyzed else None)) if analysis_input_mode=="detailed" else None
        pending=max(0,int(analysis_denominator)-analyzed) if analysis_input_mode=="detailed" and analysis_denominator is not None else None
        blocker="Доступна только агрегированная статистика звонков; для AI-разбора нужны записи или детальные события звонков." if analysis_input_mode=="aggregate_only" else None
        return {"status":"ok","items":items,"analyzed":analyzed,"live_calls":live_total,"answered_calls":live_answered,"analysis_coverage_pct":coverage,"pending_analysis":pending,"analysis_source":"canonical_call_analysis_plus_telephony","analysis_input_mode":analysis_input_mode,"analysis_input_calls":detailed_calls,"analysis_blocker":blocker}
    finally:c.close()

@router.get("/economics")
def revenue_economics(account_id: str, days: int = 30, _access=Depends(_revenue_account_guard)):
    c=_conn()
    if not c:return {"status":"error","message":"База данных недоступна"}
    cur=c.cursor()
    try:
        cfg=_setup_get(account_id); _, auto_contract, auto_payment = _auto_revenue_stages(cur, account_id, cfg); contract_stage=str(cfg.get("contract_stage") or auto_contract or "Договор"); payment_stage=str(cfg.get("payment_stage") or auto_payment or "Оплачено")
        won=int(_scalar(cur,"""SELECT count(*) FROM boris_crm_deals d LEFT JOIN boris_crm_stages s ON s.id=d.stage_id WHERE d.avito_account_id=%s AND d.updated_at>=now()-(%s||' days')::interval AND (d.status='won' OR lower(coalesce(s.name,'')) IN (lower(%s),lower(%s)))""",(account_id,days,contract_stage,payment_stage),0) or 0)
        payments=int(_scalar(cur,"""SELECT count(*) FROM boris_crm_deals d LEFT JOIN boris_crm_stages s ON s.id=d.stage_id WHERE d.avito_account_id=%s AND d.updated_at>=now()-(%s||' days')::interval AND lower(coalesce(s.name,''))=lower(%s)""",(account_id,days,payment_stage),0) or 0)
        revenue=round(float(_scalar(cur,"""SELECT coalesce(sum(d.amount_kopeks),0) FROM boris_crm_deals d LEFT JOIN boris_crm_stages s ON s.id=d.stage_id WHERE d.avito_account_id=%s AND d.updated_at>=now()-(%s||' days')::interval AND lower(coalesce(s.name,''))=lower(%s)""",(account_id,days,payment_stage),0) or 0)/100,2)
        boris_cost=round(float(_scalar(cur,"SELECT coalesce(sum(cost_rub),0) FROM api_usage WHERE account_id=%s AND created_at>=now()-(%s||' days')::interval",(account_id,days),0) or 0),2)
        # Advertising spend is intentionally separate. Use only real committed marketing ledger entries in this period.
        spend=_real_ad_spend(cur,account_id,days); ad_spend=spend.get("total_rub")
        cac=round(ad_spend/won,2) if ad_spend is not None and won else None
        boris_cost_per_contract=round(boris_cost/won,2) if won else None
        romi=round((revenue-ad_spend)/ad_spend*100,1) if ad_spend and revenue is not None else None
        return {"status":"ok","contracts":won,"payments":payments,"revenue_rub":revenue,"boris_cost_rub":boris_cost,"boris_cost_per_contract_rub":boris_cost_per_contract,"ad_spend_rub":ad_spend,"cac_rub":cac,"romi_pct":romi,"gross_after_boris_cost_rub":round(revenue-boris_cost,2) if won else None,"spend_by_channel":spend.get("channels"),"quality":{"ad_spend":spend.get("quality"),"revenue":"crm_payment_stage_amount"}}
    finally:c.close()


# BORIS_OWNER_REVENUE_V1_20260914
# Private platform-owner acquisition analytics. This is intentionally separate
# from client Avito-account economics: FL.ru / own website / outreach spend must
# never be attributed to a client account.
import re
from app.api.auth import get_current_user as _owner_get_current_user
from app.services.platform_roles import is_private_platform_owner as _is_private_platform_owner

_OWNER_MANUAL_CHANNEL_KEY = "owner_revenue_manual_channels_v1"
_OWNER_SOURCE_LABELS = {
    "fl_ru": "FL.ru",
    "avito": "Avito",
    "yandex_direct": "Яндекс Директ",
    "site": "Сайт BORIS",
    "seo": "SEO",
    "seo_yandex": "Яндекс / SEO",
    "seo_google": "Google / SEO",
    "vk": "VK",
    "telegram": "Telegram",
    "email_outreach": "Email outreach",
    "crowd_seo": "Форумы / Crowd SEO",
    "referral": "Рекомендации",
    "direct_outreach": "Прямой поиск",
    "lead_radar": "База кандидатов / Lead Radar",
    "other": "Другое",
}
_OWNER_SOURCE_ALIASES = {
    "fl.ru": "fl_ru", "flru": "fl_ru", "fl": "fl_ru", "фл": "fl_ru", "фл.ру": "fl_ru",
    "direct": "yandex_direct", "yandex": "yandex_direct", "telegram_outreach": "telegram",
    "email": "email_outreach", "mail": "email_outreach", "website": "site",
}


def _private_owner_id(user) -> int:
    if not _is_private_platform_owner(user):
        raise HTTPException(status_code=403, detail="Доступ только владельцу платформы")
    return int(getattr(user, "id", 0) or 0)


def _owner_revenue_scope(owner_id: int) -> str:
    return f"__owner_revenue__:{int(owner_id)}"


def _owner_normalize_source(value: str) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    raw = _OWNER_SOURCE_ALIASES.get(raw, raw)
    raw = re.sub(r"[^a-z0-9_]+", "_", raw).strip("_")
    return raw[:64] or "other"


def _owner_manual_entries(owner_id: int) -> list:
    scope = _owner_revenue_scope(owner_id)
    db = SessionLocal()
    try:
        row = (db.query(Storage)
               .filter(Storage.account_id == scope, Storage.key == _OWNER_MANUAL_CHANNEL_KEY)
               .order_by(Storage.id.asc()).first())
        if not row or not row.value:
            return []
        try:
            payload = json.loads(row.value)
        except Exception:
            return []
        entries = payload.get("entries") if isinstance(payload, dict) else []
        return entries if isinstance(entries, list) else []
    finally:
        db.close()


def _owner_manual_save(owner_id: int, entries: list) -> None:
    scope = _owner_revenue_scope(owner_id)
    db = SessionLocal()
    try:
        db.execute(text("select pg_advisory_xact_lock(hashtextextended(:k,0))"),
                   {"k": f"storage|{scope}|{_OWNER_MANUAL_CHANNEL_KEY}"})
        rows = (db.query(Storage)
                .filter(Storage.account_id == scope, Storage.key == _OWNER_MANUAL_CHANNEL_KEY)
                .order_by(Storage.id.asc()).with_for_update().all())
        value = json.dumps({"version": 1, "entries": entries[-1000:]}, ensure_ascii=False)
        if rows:
            rows[0].value = value
            for dup in rows[1:]:
                db.delete(dup)
        else:
            db.add(Storage(account_id=scope, key=_OWNER_MANUAL_CHANNEL_KEY, value=value))
        db.commit()
    finally:
        db.close()


def _owner_manual_rollup(owner_id: int, days: int = 30) -> dict:
    days = max(1, min(int(days or 30), 3660))
    cutoff = datetime.date.today() - datetime.timedelta(days=days - 1)
    out = defaultdict(lambda: {"spend_rub": 0.0, "outreach": 0, "entries": 0})
    for entry in _owner_manual_entries(owner_id):
        try:
            day = datetime.date.fromisoformat(str(entry.get("occurred_on") or "")[:10])
        except Exception:
            continue
        if day < cutoff:
            continue
        source = _owner_normalize_source(entry.get("source") or "other")
        row = out[source]
        row["spend_rub"] += max(0.0, float(entry.get("spend_rub") or 0))
        row["outreach"] += max(0, int(entry.get("outreach") or 0))
        row["entries"] += 1
    return {k: {**v, "spend_rub": round(v["spend_rub"], 2)} for k, v in out.items()}


def _owner_tracker_setup(owner_id: int) -> dict:
    scope = _owner_revenue_scope(owner_id)
    cfg = _setup_get(scope)
    changed = False
    if not cfg.get("tracker_key"):
        cfg["tracker_key"] = secrets.token_urlsafe(18); changed = True
    if not cfg.get("website_domain"):
        cfg["website_domain"] = "boris-ai.pro"; changed = True
    cfg["website_mode"] = "used"
    if changed:
        _setup_put(scope, cfg)
    return cfg


@router.get("/owner/channels")
def owner_channels(days: int = 30, current_user=Depends(_owner_get_current_user)):
    owner_id = _private_owner_id(current_user)
    rollup = _owner_manual_rollup(owner_id, days)
    entries = sorted(_owner_manual_entries(owner_id), key=lambda x: str(x.get("occurred_on") or ""), reverse=True)
    return {
        "status": "ok", "period_days": days, "entries": entries[:200], "rollup": rollup,
        "sources": [{"key": k, "name": v} for k, v in _OWNER_SOURCE_LABELS.items()],
    }


@router.post("/owner/channels")
def owner_channel_save(body: dict = Body(...), current_user=Depends(_owner_get_current_user)):
    owner_id = _private_owner_id(current_user)
    source = _owner_normalize_source(body.get("source") or "other")
    try:
        spend = round(max(0.0, float(body.get("spend_rub") or 0)), 2)
        outreach = max(0, int(body.get("outreach") or 0))
    except Exception:
        raise HTTPException(status_code=400, detail="Некорректный расход или количество касаний")
    occurred_on = str(body.get("occurred_on") or datetime.date.today().isoformat())[:10]
    try:
        datetime.date.fromisoformat(occurred_on)
    except Exception:
        raise HTTPException(status_code=400, detail="Некорректная дата")
    note = str(body.get("note") or "").strip()[:1000]
    entry_id = str(body.get("entry_id") or secrets.token_urlsafe(9))[:80]
    entry = {
        "id": entry_id, "source": source, "occurred_on": occurred_on,
        "spend_rub": spend, "outreach": outreach, "note": note,
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    entries = _owner_manual_entries(owner_id)
    replaced = False
    for idx, old in enumerate(entries):
        if str(old.get("id") or "") == entry_id:
            entries[idx] = entry; replaced = True; break
    if not replaced:
        entries.append(entry)
    _owner_manual_save(owner_id, entries)
    return {"status": "ok", "entry": entry, "rollup": _owner_manual_rollup(owner_id, 30)}


@router.get("/owner/tracker-snippet")
def owner_tracker_snippet(current_user=Depends(_owner_get_current_user)):
    owner_id = _private_owner_id(current_user)
    cfg = _owner_tracker_setup(owner_id)
    scope = _owner_revenue_scope(owner_id)
    src = f"https://boris-ai.pro/boris-tracker.js?aid={scope}&k={cfg['tracker_key']}"
    return {"status": "ok", "src": src, "snippet": f'<script async src="{src}"></script>', "domain": cfg.get("website_domain")}


@router.get("/owner/overview")
def owner_revenue_overview(days: int = 30, current_user=Depends(_owner_get_current_user)):
    owner_id = _private_owner_id(current_user)
    days = max(1, min(int(days or 30), 3660))
    manual = _owner_manual_rollup(owner_id, days)
    scope = _owner_revenue_scope(owner_id)
    cfg = _owner_tracker_setup(owner_id)
    c = _conn()
    if not c:
        return {"status": "error", "message": "База данных недоступна"}
    cur = c.cursor()
    try:
        cur.execute("""
          SELECT coalesce(nullif(btrim(d.source),''),'unknown') source,
                 count(*) prospects,
                 count(*) FILTER (WHERE lower(coalesce(nullif(btrim(d.source),''),'unknown')) <> 'lead_radar' OR coalesce(s.code,'') IN ('contacted','qualified','work','proposal','contract','won')) leads,
                 count(*) FILTER (WHERE coalesce(s.code,'') IN ('qualified','work','proposal','contract','won')) qualified,
                 count(*) FILTER (WHERE coalesce(s.code,'') IN ('proposal','contract','won')) proposals,
                 count(*) FILTER (WHERE coalesce(s.code,'') IN ('contract','won') OR d.status='won') contracts,
                 count(*) FILTER (WHERE coalesce(s.code,'')='won' OR lower(coalesce(s.name,'')) LIKE '%%оплач%%') payments,
                 coalesce(sum(d.amount_kopeks) FILTER (WHERE coalesce(s.code,'')='won' OR lower(coalesce(s.name,'')) LIKE '%%оплач%%'),0) paid_kopeks
            FROM boris_crm_deals d
            LEFT JOIN boris_crm_stages s ON s.id=d.stage_id
           WHERE d.owner_user_id=%s
             AND d.avito_account_id IS NULL
             AND d.created_at>=now()-(%s||' days')::interval
           GROUP BY 1
        """, (owner_id, days))
        crm = {}
        for source, prospects, leads, qualified, proposals, contracts, payments, paid_kopeks in cur.fetchall():
            src = _owner_normalize_source(source)
            crm[src] = {
                "prospects": int(prospects or 0), "leads": int(leads or 0), "qualified": int(qualified or 0),
                "proposals": int(proposals or 0), "contracts": int(contracts or 0),
                "payments": int(payments or 0), "revenue_rub": round(float(paid_kopeks or 0) / 100.0, 2),
            }
        cur.execute("""
          SELECT coalesce(nullif(source,''),'') source, coalesce(referrer,''), count(*) events,
                 count(distinct session_id) sessions,
                 count(*) FILTER (WHERE event_type IN ('form_submit','lead','click_phone')) intents,
                 count(distinct session_id) FILTER (WHERE event_type='lead') web_leads
            FROM revenue_events
           WHERE account_id=%s AND occurred_at>=now()-(%s||' days')::interval
           GROUP BY 1,2
        """, (scope, days))
        web = defaultdict(lambda: {"events": 0, "sessions": 0, "intents": 0, "leads": 0})
        for source, ref, events, sessions, intents, web_leads in cur.fetchall():
            src = _owner_normalize_source(_event_source({"source": source, "referrer": ref}))
            web[src]["events"] += int(events or 0)
            web[src]["sessions"] += int(sessions or 0)
            web[src]["intents"] += int(intents or 0)
            web[src]["leads"] += int(web_leads or 0)
    finally:
        c.close()

    all_sources = sorted(set(manual) | set(crm) | set(web))
    rows = []
    for src in all_sources:
        m = manual.get(src, {}); r = crm.get(src, {}); w = web.get(src, {})
        spend = round(float(m.get("spend_rub") or 0), 2)
        outreach = int(m.get("outreach") or 0)
        crm_leads = int(r.get("leads") or 0)
        web_leads = int(w.get("leads") or 0)
        leads = max(crm_leads, web_leads)
        contracts = int(r.get("contracts") or 0)
        revenue = round(float(r.get("revenue_rub") or 0), 2)
        rows.append({
            "source": src, "name": _OWNER_SOURCE_LABELS.get(src, src),
            "spend_rub": spend, "outreach": outreach,
            "web_sessions": int(w.get("sessions") or 0), "web_intents": int(w.get("intents") or 0),
            "web_leads": web_leads, "crm_leads": crm_leads,
            "prospects": int(r.get("prospects") or 0), "leads": leads, "qualified": int(r.get("qualified") or 0),
            "proposals": int(r.get("proposals") or 0), "contracts": contracts,
            "payments": int(r.get("payments") or 0), "revenue_rub": revenue,
            "cost_per_outreach_rub": round(spend / outreach, 2) if spend and outreach else None,
            "cpl_rub": round(spend / leads, 2) if spend and leads else None,
            "cac_rub": round(spend / contracts, 2) if spend and contracts else None,
            "romi_pct": round((revenue - spend) / spend * 100, 1) if spend and revenue else None,
        })
    rows.sort(key=lambda x: (x["revenue_rub"], x["leads"], x["spend_rub"]), reverse=True)
    totals = {
        "spend_rub": round(sum(x["spend_rub"] for x in rows), 2),
        "outreach": sum(x["outreach"] for x in rows),
        "web_sessions": sum(x["web_sessions"] for x in rows),
        "web_intents": sum(x["web_intents"] for x in rows),
        "prospects": sum(x["prospects"] for x in rows),
        "leads": sum(x["leads"] for x in rows),
        "qualified": sum(x["qualified"] for x in rows),
        "proposals": sum(x["proposals"] for x in rows),
        "contracts": sum(x["contracts"] for x in rows),
        "payments": sum(x["payments"] for x in rows),
        "revenue_rub": round(sum(x["revenue_rub"] for x in rows), 2),
    }
    totals["cost_per_outreach_rub"] = round(totals["spend_rub"] / totals["outreach"], 2) if totals["spend_rub"] and totals["outreach"] else None
    totals["cpl_rub"] = round(totals["spend_rub"] / totals["leads"], 2) if totals["spend_rub"] and totals["leads"] else None
    totals["cac_rub"] = round(totals["spend_rub"] / totals["contracts"], 2) if totals["spend_rub"] and totals["contracts"] else None
    totals["romi_pct"] = round((totals["revenue_rub"] - totals["spend_rub"]) / totals["spend_rub"] * 100, 1) if totals["spend_rub"] and totals["revenue_rub"] else None
    tracker_src = f"https://boris-ai.pro/boris-tracker.js?aid={scope}&k={cfg['tracker_key']}"
    return {
        "status": "ok", "period_days": days, "scope": "platform_owner",
        "totals": totals, "sources": rows,
        "tracker": {"domain": cfg.get("website_domain"), "src": tracker_src, "snippet": f'<script async src="{tracker_src}"></script>'},
        "rules": {"crm_scope": "owner_deals_without_client_account", "spend_scope": "owner_manual_channel_ledger", "client_accounts_excluded": True},
    }
