from __future__ import annotations
import datetime as dt, json, secrets
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from app.db.session import get_db, SessionLocal
from app.director.models import DirectConnection, DirectKpiConfig
from app.director.intelligence import diagnose, business_diagnose, negative_keyword_plan, strategy_intelligence, scale_recovery_plan, creative_intelligence, bid_modifier_optimizer, budget_allocator, performance_forecast, creative_fatigue, business_setup_plan, result_influence_map, KNOWLEDGE
from app.director.core import DirectError, app_configured, authorization_url, dashboard, ensure_connection, exchange_code, make_state, parse_state, redirect_uri, encrypt_token, sync_entities, sync_report, sync_search_queries, sync_marketing_controls, sync_dimension_report, advertising_capabilities, audit_and_plan, plan_advanced_actions, execute_action, rollback_action, experiment_candidates, landing_health, Gateway
from app.services.exception_observability import observe_suppressed

router=APIRouter(prefix="/api/direct", tags=["ai-director"])
public_router=APIRouter(prefix="/api/direct", tags=["ai-director-oauth"])

def fail(e: Exception):
    if isinstance(e, DirectError): raise HTTPException(status_code=409 if e.code in {"NOT_CONNECTED","APP_NOT_CONFIGURED","WRITES_DISABLED","CONFIRMATION_REQUIRED"} else 400, detail={"code":e.code,"message":e.message})
    raise e

@router.get("/readiness")
def readiness(account_id: str, db=Depends(get_db)):
    c=ensure_connection(db,account_id); db.commit()
    return {"status":"ok","app_configured":app_configured(),"redirect_uri":redirect_uri(),"connection_status":c.status,"connected":c.status=="CONNECTED","external_blockers":([] if app_configured() else ["YANDEX_DIRECT_OAUTH_APP_NOT_CONFIGURED"])}

@router.post("/oauth/start")
def oauth_start(payload: dict=Body(...), db=Depends(get_db)):
    account_id=str(payload.get("account_id") or "").strip()
    if not account_id: raise HTTPException(422,"account_id required")
    if not app_configured(): raise HTTPException(409,detail={"code":"APP_NOT_CONFIGURED","message":"Сначала добавьте OAuth Client ID/Secret приложения Яндекс Директа"})
    c=ensure_connection(db,account_id); nonce=secrets.token_urlsafe(24); c.oauth_nonce=nonce; c.oauth_nonce_expires_at=dt.datetime.utcnow()+dt.timedelta(minutes=15); c.status="OAUTH_PENDING"; db.commit()
    return {"status":"ok","authorization_url":authorization_url(make_state(account_id,nonce))}

@public_router.get("/oauth/callback")
def oauth_callback(code: str=Query(default=""), state: str=Query(default=""), error: str=Query(default="")):
    base="https://boris-ai.pro/dashboard/director"
    if error: return RedirectResponse(base+"?oauth=denied")
    db=SessionLocal()
    try:
        data=parse_state(state); account_id=str(data["a"]); c=ensure_connection(db,account_id)
        if not c.oauth_nonce or c.oauth_nonce!=data.get("n") or not c.oauth_nonce_expires_at or c.oauth_nonce_expires_at<dt.datetime.utcnow(): raise DirectError("OAUTH_NONCE_INVALID","OAuth nonce недействителен")
        token=exchange_code(code); c.token_encrypted=encrypt_token(token); c.oauth_nonce=None; c.oauth_nonce_expires_at=None; c.status="CONNECTED"; c.connected_at=dt.datetime.utcnow(); db.commit()
        try:
            obj=Gateway(c).call("clients","get",{"FieldNames":["Login","ClientInfo","Currency","TimeZone","Type"]})
            clients=(obj.get("result") or {}).get("Clients") or []
            if clients:
                c.yandex_login=str(clients[0].get("Login") or "") or None
                c.currency=str(clients[0].get("Currency") or "") or None
                c.timezone=str(clients[0].get("TimeZone") or "") or None
                db.commit()
        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=48)
        return RedirectResponse(base+"?oauth=connected")
    except Exception as e:
        try:
            if 'c' in locals(): c.status="OAUTH_ERROR"; c.last_error_code=getattr(e,"code",type(e).__name__); c.last_error_message=str(e)[:500]; db.commit()
        except Exception: db.rollback()
        return RedirectResponse(base+"?oauth=error")
    finally: db.close()

@router.post("/disconnect")
def disconnect(payload: dict=Body(...), db=Depends(get_db)):
    c=ensure_connection(db,str(payload.get("account_id") or "")); c.token_encrypted=None; c.status="DISCONNECTED"; c.oauth_nonce=None; db.commit(); return {"status":"ok"}

@router.get("/dashboard")
def get_dashboard(account_id: str, db=Depends(get_db)): return dashboard(db,account_id)

@router.post("/kpi")
def set_kpi(payload: dict=Body(...), db=Depends(get_db)):
    account_id=str(payload.get("account_id") or "").strip(); row=db.query(DirectKpiConfig).filter_by(account_id=account_id).first()
    if row is None: row=DirectKpiConfig(account_id=account_id); db.add(row)
    for key in ("target_leads_daily","target_cpl","max_cpl","target_sales_daily","target_cac","monthly_budget","daily_hard_limit","max_single_change_percent"):
        if key in payload: setattr(row,key, float(payload[key]) if payload[key] not in (None,"") else None)
    if "autonomy_mode" in payload:
        mode=str(payload["autonomy_mode"]).upper()
        if mode not in {"ADVISOR","CONFIRM","AUTOPILOT"}: raise HTTPException(422,"invalid autonomy_mode")
        row.autonomy_mode=mode
    if "primary_goal" in payload: row.primary_goal=str(payload["primary_goal"]).upper()
    if "ai_writes_enabled" in payload: row.ai_writes_enabled=bool(payload["ai_writes_enabled"])
    db.commit(); return {"status":"ok"}

@router.post("/sync")
def sync(payload: dict=Body(...), db=Depends(get_db)):
    account_id=str(payload.get("account_id") or "")
    try:
        entities=sync_entities(db,account_id)
        controls=sync_marketing_controls(db,account_id)
        dimensions=None
        try: dimensions=sync_dimension_report(db,account_id,min(30,int(payload.get("days") or 30)))
        except DirectError as e:
            if e.code=="REPORT_PENDING": dimensions={"status":"PROCESSING","message":e.message}
            else: raise
        query_report=None
        try: query_report=sync_search_queries(db,account_id,min(14,int(payload.get("days") or 30)))
        except DirectError as e:
            if e.code=="REPORT_PENDING": query_report={"status":"PROCESSING","message":e.message}
            else: raise
        try: report=sync_report(db,account_id,int(payload.get("days") or 30))
        except DirectError as e:
            if e.code=="REPORT_PENDING": report={"status":"PROCESSING","message":e.message}
            else: raise
        return {"status":"ok","entities":entities,"controls":controls,"dimensions":dimensions,"report":report,"query_report":query_report}
    except Exception as e:
        c=ensure_connection(db,account_id); c.last_error_at=dt.datetime.utcnow(); c.last_error_code=getattr(e,"code",type(e).__name__); c.last_error_message=str(e)[:500]; db.commit(); fail(e)

@router.post("/audit")
def audit(payload: dict=Body(...), db=Depends(get_db)):
    try: return audit_and_plan(db,str(payload.get("account_id") or ""))
    except Exception as e: fail(e)

@router.post("/actions/{action_id}/execute")
def action_execute(action_id: int, payload: dict=Body(...), db=Depends(get_db)):
    try: return execute_action(db,str(payload.get("account_id") or ""),action_id,bool(payload.get("confirmed")))
    except Exception as e: fail(e)

@router.get("/health")
def health(account_id: str, db=Depends(get_db)):
    d=dashboard(db,account_id); return {"status":"ok","module":"AI_DIRECTOR","connection":d["connection"]["state"],"data_state":d["today"]["data_state"],"health":d["health"],"app_configured":d["connection"]["app_configured"]}


@router.get("/diagnostics")
def diagnostics(account_id: str, db=Depends(get_db)):
    return business_diagnose(db, account_id)

@router.get("/knowledge")
def knowledge():
    return {"status":"ok","version":"2026-08-30","rules":KNOWLEDGE}


@router.get("/capabilities")
def capabilities(account_id: str, db=Depends(get_db)):
    return advertising_capabilities(db, account_id)


@router.post("/actions/{action_id}/rollback")
def action_rollback(action_id: int, payload: dict=Body(...), db=Depends(get_db)):
    try: return rollback_action(db,str(payload.get("account_id") or ""),action_id,bool(payload.get("confirmed")))
    except Exception as e: fail(e)

@router.get("/experiments")
def experiments(account_id: str, db=Depends(get_db)):
    return experiment_candidates(db, account_id)


@router.get("/negative-keywords")
def negative_keywords(account_id: str, db=Depends(get_db)):
    return negative_keyword_plan(db, account_id)

@router.get("/strategies/intelligence")
def strategies_intelligence(account_id: str, db=Depends(get_db)):
    return strategy_intelligence(db, account_id)

@router.get("/recovery-plan")
def recovery_plan(account_id: str, db=Depends(get_db)):
    return scale_recovery_plan(db, account_id)

@router.get("/landing-health")
def get_landing_health(account_id: str, db=Depends(get_db)):
    return landing_health(db, account_id)


@router.get("/creatives/intelligence")
def creatives_intelligence(account_id: str, db=Depends(get_db)):
    return creative_intelligence(db, account_id)


@router.get("/bid-modifier-plan")
def bid_modifier_plan(account_id: str, db=Depends(get_db)):
    return bid_modifier_optimizer(db, account_id)

@router.get("/budget-allocation")
def budget_allocation(account_id: str, db=Depends(get_db)):
    return budget_allocator(db, account_id)

@router.get("/forecast")
def forecast(account_id: str, db=Depends(get_db)):
    return performance_forecast(db, account_id)

@router.get("/creatives/fatigue")
def creatives_fatigue(account_id: str, db=Depends(get_db)):
    return creative_fatigue(db, account_id)


@router.post("/action-plan")
def action_plan(payload: dict=Body(...), db=Depends(get_db)):
    try: return plan_advanced_actions(db,str(payload.get("account_id") or ""))
    except Exception as e: fail(e)


@router.post("/setup/plan")
def setup_plan(payload: dict=Body(...), db=Depends(get_db)):
    account_id=str(payload.get("account_id") or "").strip(); brief=payload.get("brief") or {}
    if not account_id: raise HTTPException(422,"account_id required")
    plan=business_setup_plan(db,account_id,brief)
    row=db.query(DirectKpiConfig).filter_by(account_id=account_id).first()
    if row is None: row=DirectKpiConfig(account_id=account_id); db.add(row)
    row.business_brief_json=json.dumps(brief,ensure_ascii=False); row.setup_status=plan["status"]
    if brief.get("monthly_budget"): row.monthly_budget=float(brief["monthly_budget"])
    if brief.get("target_cpl"): row.target_cpl=float(brief["target_cpl"]); row.max_cpl=float(brief.get("max_cpl") or brief["target_cpl"])
    if brief.get("goal"): row.primary_goal=str(brief["goal"]).upper()
    db.commit(); return plan

@router.get("/result-loop")
def result_loop(account_id: str, db=Depends(get_db)):
    return result_influence_map(db,account_id)


@router.post("/oauth/complete")
def oauth_complete(payload: dict=Body(...), db=Depends(get_db)):
    account_id=str(payload.get("account_id") or "").strip(); code=str(payload.get("code") or "").strip()
    if not account_id or not code: raise HTTPException(422,"account_id and code required")
    c=ensure_connection(db,account_id)
    if c.status not in {"OAUTH_PENDING","OAUTH_ERROR","NOT_CONNECTED","DISCONNECTED"}: pass
    try:
        token=exchange_code(code); c.token_encrypted=encrypt_token(token); c.oauth_nonce=None; c.oauth_nonce_expires_at=None; c.status="CONNECTED"; c.connected_at=dt.datetime.utcnow(); c.last_error_code=None; c.last_error_message=None; db.commit()
        try:
            obj=Gateway(c).call("clients","get",{"FieldNames":["Login","ClientInfo","Currency","TimeZone","Type"]})
            clients=(obj.get("result") or {}).get("Clients") or []
            if clients:
                c.yandex_login=str(clients[0].get("Login") or "") or None
                c.currency=str(clients[0].get("Currency") or "") or None
                c.timezone=str(clients[0].get("TimeZone") or "") or None
                db.commit()
        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=220)
        return {"status":"ok","connected":True}
    except Exception as e:
        c.status="OAUTH_ERROR"; c.last_error_code=getattr(e,"code",type(e).__name__); c.last_error_message=str(e)[:500]; db.commit(); fail(e)
