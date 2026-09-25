# -*- coding: utf-8 -*-
"""Роутер нового рабочего стола БОРИСа (/dashboard/home и сценарии).

ТОЛЬКО ЧТЕНИЕ существующих данных + CRUD одной таблицы business_scenarios.
Ничего не публикует, денег не тратит, чужие модули не меняет.
Авторизация и изоляция аккаунтов навешены централизованно в main.py.
account_id обязателен везде - дефолтов быть не должно (см. этап 0).
"""

import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, Depends
from app.api.auth import get_current_user
from pydantic import BaseModel
from typing import Optional, Dict, Any

from app.db.session import SessionLocal
from app.models.business_scenario import BusinessScenario
from app import scenarios as S
from app.services.exception_observability import observe_suppressed

router = APIRouter(prefix="/api/home", tags=["home"])


def _scenario_capability_projection(progress):
    from app import scenario_capabilities as SC
    seen, items = set(), []
    for step in (progress or {}).get("steps") or []:
        action = str(step.get("action") or "")
        if not action or action in seen:
            continue
        cap = SC.get_capability(action)
        if cap:
            seen.add(action)
            items.append(cap)
    return items


def _scen_public(row, facts):
    if row.scenario_type == "revive" and "_revive" not in facts:
        try:
            facts = dict(facts)
            facts["_revive"] = S.revive_facts(row.account_id)
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=44)
    if row.scenario_type == "avito_start" and "_start" not in facts:
        try:
            facts = dict(facts)
            facts["_start"] = S.start_facts(row.account_id)
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=50)
    state = _j(row.steps_state)
    data = S.scenario_progress(row.scenario_type, facts, state)
    meta = S.CATALOG.get(row.scenario_type, {})
    inp = _j(row.input_data)
    # Every catalog scenario exposes the same owner-facing execution contract.
    # Legacy Avito scenarios keep their specialised steps, but no longer lose
    # goal/diagnosis/result/next-action semantics in the product UI.
    workflow = S.business_workflow_view(row.scenario_type, facts, inp, data)
    history = []
    for st in data.get("steps") or []:
        if st.get("status") == "completed": history.append({"state":"done","text":st.get("title"),"at":st.get("result_at")})
        elif st.get("status") == "attention": history.append({"state":"attention","text":st.get("title"),"at":st.get("result_at")})
        elif st.get("result"): history.append({"state":"work","text":(st.get("result") or {}).get("summary") or st.get("title"),"at":st.get("result_at")})
    cur = next((x for x in data.get("steps",[]) if x.get("status") in ("current","attention")), None)
    owner_action = None
    if cur:
        title = cur.get("title") or "Нужно ваше решение"
        result = cur.get("result") if isinstance(cur.get("result"),dict) else {}
        owner_words = ("согласовать","подтверд","публикац","выгруз","решить","разреш","подключить","утверд","выбрать")
        needs_owner = bool(cur.get("owner_required") or result.get("need_confirm") or result.get("owner_required") or (not cur.get("auto") and any(x in title.lower() for x in owner_words)))
        if needs_owner and not result.get("external_wait"):
            low=title.lower(); priority = 10 if any(x in low for x in ("публикац","выгруз","согласовать")) else (20 if result.get("need_confirm") else 30)
            done_text = next((h.get("text") for h in reversed(history) if h.get("state")=="done"), "BORIS собрал данные, диагноз и безопасную подготовку")
            reason = result.get("summary") or cur.get("hint")
            owner_action = {"scenario_id":row.id,"scenario_type":row.scenario_type,"title":title,"route":cur.get("route"),"step_key":cur.get("key"),"priority":priority,
                            "what_boris_did":done_text,"why_stopped":reason,"decision_required":title,
                            "after_confirmation":cur.get("hint"),"external_consequence":bool(any(x in low for x in ("публикац","выгруз","внешн"))),
                            "expense_possible":bool(result.get("requires_paid_or_owner_generation")),"primary_cta":title,"reason":reason}
    return {
        "id": row.id,
        "scenario_type": row.scenario_type,
        "title": meta.get("title") or row.scenario_type,
        "lead": meta.get("lead") or "",
        "status": row.status,
        "input_data": inp,
        "workflow": workflow,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "progress": data,
        "metrics": _j(getattr(row, "metrics", None)) or {},
        "history": history[-12:],
        "owner_action": owner_action,
        "continuation": _scenario_wait_projection(row, facts),
        "closed_loop": ((_j(getattr(row, "metrics", None)) or {}).get("closed_loop") or {}),
        "business_timeline": ((_j(getattr(row, "metrics", None)) or {}).get("business_timeline") or [])[-12:],
        "capability_contract": _scenario_capability_projection(data),
        # реальные числа аккаунта — для финального экрана результата
        "numbers": {
            "items_total": facts.get("items_total"),
            "active_items": facts.get("active_items"),
            "feed_items": facts.get("feed_count"),
            "dead_items": facts.get("dead_items"),
            "views": facts.get("views"),
            "contacts": facts.get("contacts"),
            "stats_date": facts.get("stats_date"),
        },
    }


def _j(v, default=None):
    try:
        return json.loads(v) if v else (default if default is not None else {})
    except Exception:
        return default if default is not None else {}


def _require_account_access(db, user, account_id):
    if getattr(user, "role", "") == "owner":
        return
    uid = getattr(user, "id", 0)
    row = db.execute(__import__('sqlalchemy').text(
        "SELECT 1 FROM accounts WHERE account_id=:a AND owner_user_id=:u UNION SELECT 1 FROM user_account_access WHERE account_id=:a AND user_id=:u AND can_view=TRUE LIMIT 1"),
        {"a": account_id, "u": uid}).fetchone()
    if not row:
        raise HTTPException(404, "Аккаунт не найден")


def _active(db, account_id):
    return db.query(BusinessScenario).filter(
        BusinessScenario.account_id == account_id,
        BusinessScenario.status == "active").order_by(
        BusinessScenario.updated_at.desc()).first()


# ------------------------------------------------------------------ обзор

def _parse_validator_report(raw_text):
    """Разбирает сохранённый текст страницы валидатора Avito в список
    {title, reason}. В сеть не ходит. При любом неожиданном формате
    возвращает пустой список и не роняет шаг."""
    import re as _re
    try:
        lines = [l.strip() for l in (raw_text or "").split("\n") if l.strip()]
        start = -1
        for i, l in enumerate(lines):
            if "Сообщения и ошибки" in l:
                start = i + 1
                break
        if start < 0:
            return []
        items, title, parts = [], None, []

        def _flush():
            if title and parts:
                items.append({"title": title,
                              "reason": ". ".join(parts)[:400] + "."})

        for l in lines[start:]:
            m = _re.match(r"^(\d+)\t+(.+)$", l)
            if m:
                _flush()
                title, parts = m.group(2).strip(), []
                continue
            if title is None or l.startswith("Подробнее"):
                continue
            if len(parts) < 12:
                parts.append(l.rstrip("."))
        _flush()
        return items[:50]
    except Exception:
        return []
def _viewer_role(db, user, account_id: str) -> str:
    """Роль смотрящего в этом аккаунте. Нужна журналу: сотруднику не показываем
    записи про оплаты и администрирование."""
    from sqlalchemy import text as _t
    if getattr(user, "role", "") == "owner":
        return "owner"
    own = db.execute(_t(
        "SELECT 1 FROM accounts WHERE account_id = :a AND owner_user_id = :u"),
        {"a": account_id, "u": getattr(user, "id", 0)}).fetchone()
    if own:
        return "owner"
    r = db.execute(_t(
        "SELECT role FROM user_account_access WHERE user_id = :u AND account_id = :a"),
        {"u": getattr(user, "id", 0), "a": account_id}).fetchone()
    return (r[0] if r else "employee") or "employee"


@router.get("/overview")
def overview(account_id: str, user=Depends(get_current_user)):
    """Всё для рабочего стола одним запросом."""
    db = SessionLocal()
    try:
        _require_account_access(db, user, account_id)
        f = S.collect_facts(account_id, db=db)
        row = _active(db, account_id)
        active_rows = db.query(BusinessScenario).filter(BusinessScenario.account_id==account_id, BusinessScenario.status=="active").order_by(BusinessScenario.updated_at.desc()).all()
        public_rows = [_scen_public(x, f) for x in active_rows]
        owner_inbox = sorted([x["owner_action"] for x in public_rows if x.get("owner_action")], key=lambda x:(x.get("priority",99),x.get("scenario_id",0)))
        return {
            "status": "ok",
            "account": {
                "account_id": account_id,
                "name": f.get("account_name"),
                "avito_connected": f.get("avito_connected"),
                "has_active_access": f.get("has_active_access"),
                "unlimited": f.get("unlimited"),
                "payment_until": f.get("payment_until"),
                "tier": f.get("billing_tier"),
                "taught": f.get("taught"),
            },
            "stage": {
                "number": f.get("stage"),
                "total": f.get("stage_total"),
                "name": f.get("stage_name"),
                "hint": f.get("stage_hint"),
            },
            "numbers": {
                "ready_to_publish": f.get("ready_count"),
                "feed_items": f.get("feed_count"),
                "active_items": f.get("active_items"),
                "items_total": f.get("items_total"),
                "views": f.get("views"),
                "contacts": f.get("contacts"),
                "dead_items": f.get("dead_items"),
                "stats_date": f.get("stats_date"),
                "has_stats": f.get("has_stats"),
            },
            "attention": S.attention(f),
            "today": S.today_summary(account_id, db=db),
            "journal": S.journal(account_id, db=db, limit=10,
                                 role=_viewer_role(db, user, account_id)),
            "recommendations": S.recommendations(f, db=db),
            "active_scenario": _scen_public(row, f) if row else None,
            "owner_inbox": owner_inbox[:8],
            "owner_inbox_empty": len(owner_inbox) == 0,
        }
    finally:
        db.close()


@router.get("/stage")
def stage(account_id: str):
    db = SessionLocal()
    try:
        f = S.collect_facts(account_id, db=db)
        return {"status": "ok", "number": f.get("stage"),
                "total": f.get("stage_total"), "name": f.get("stage_name"),
                "hint": f.get("stage_hint")}
    finally:
        db.close()


def _scen_needs_avito(scenario_type: str) -> bool:
    """Требует ли сценарий подключённого Авито. Источник истины — База знаний."""
    try:
        from app import niches as _nz
        sc = _nz.common("scenarios", scenario_type) or {}
        needs = sc.get("needs") or {}
        return "avito" in (needs.get("all") or []) or "avito" in (needs.get("any") or [])
    except Exception:
        # База знаний недоступна — не блокируем ничего, поведение как раньше
        return False


def _scen_available(scenario_type: str, account_id: str) -> bool:
    if account_id:
        return True
    return not _scen_needs_avito(scenario_type)


def _scen_blocked_reason(scenario_type: str, account_id: str):
    if _scen_available(scenario_type, account_id):
        return None
    return "Нужно подключить аккаунт Авито"



# --------------------------------------------------------------- каталог

@router.get("/scenario-capabilities")
def scenario_capabilities(account_id: str, user=Depends(get_current_user)):
    """Runtime read-model of sanctioned Scenario-callable capabilities."""
    db = SessionLocal()
    try:
        _require_account_access(db, user, account_id)
        from app import scenario_capabilities as SC
        return {"status": "ok", "account_id": account_id,
                "capabilities": SC.list_capabilities(),
                "invariants": {"scenario_orchestrates": True,
                               "module_executes_authoritative_logic": True,
                               "second_capability_engine": False,
                               "second_scheduler": False}}
    finally:
        db.close()


@router.get("/scenarios")
def list_scenarios(account_id: str = "", user=Depends(get_current_user)):
    """Каталог виден и без аккаунта: сценарий «Запустить Авито с нуля» как раз
    и ведёт человека к подключению, блокировать его от новичка нельзя.
    Без аккаунта не ходим в базу за запущенными — считать нечего."""
    db = SessionLocal()
    try:
        _require_account_access(db, user, account_id)
        f = None
        rows = []
        if account_id:
            f = S.collect_facts(account_id, db=db)
            rows = db.query(BusinessScenario).filter(
                BusinessScenario.account_id == account_id).order_by(
                BusinessScenario.updated_at.desc()).all()
        by_type = {}
        for r in rows:
            by_type.setdefault(r.scenario_type, r)
        available = []
        active_access = bool(f and f.get("has_active_access")) if account_id else False
        for stype, meta in S.CATALOG.items():
            existing = by_type.get(stype)
            scenario_available = bool(active_access and _scen_available(stype, account_id))
            blocked_reason = None if scenario_available else (
                "Нужен активный тариф или 4-дневный тестовый период"
                if account_id and not active_access else _scen_blocked_reason(stype, account_id))
            available.append({
                "scenario_type": stype,
                "title": meta["title"],
                "lead": meta["lead"],
                "modules": meta["modules"],
                "plan": meta.get("plan") or [],
                "fields": meta["fields"],
                "available": scenario_available,
                "blocked_reason": blocked_reason,
                "existing_id": existing.id if existing and existing.status == "active" else None,
            })
        soon = [{"scenario_type": x[0], "title": x[1], "available": False,
                 "blocked_reason": x[2] if len(x) > 2 else "Пока недоступно"} for x in S.SOON]
        recommendation = S.recommend_scenario(f or {}) if account_id else None
        if recommendation:
            rec_type = recommendation.get("scenario_type")
            rec_card = next((x for x in available if x["scenario_type"] == rec_type), None)
            recommendation = {
                **recommendation,
                "title": (rec_card or {}).get("title"),
                "available": bool((rec_card or {}).get("available")),
                "existing_id": (rec_card or {}).get("existing_id"),
                "blocked_reason": (rec_card or {}).get("blocked_reason"),
            }
        return {"status": "ok", "available": available, "soon": soon,
                "recommended": recommendation,
                "mine": [_scen_public(r, f) for r in rows if r.status == "active"]}
    finally:
        db.close()


class StartRequest(BaseModel):
    account_id: str
    scenario_type: str
    input_data: Optional[Dict[str, Any]] = None


@router.post("/scenario/start")
def start(req: StartRequest, user=Depends(get_current_user)):
    if req.scenario_type not in S.CATALOG:
        raise HTTPException(400, "Неизвестный сценарий")
    db = SessionLocal()
    try:
        _require_account_access(db, user, req.account_id)
        access_facts = S.collect_facts(req.account_id, db=db)
        if not access_facts.get("has_active_access"):
            raise HTTPException(402, "Нужен активный тариф или 4-дневный тестовый период")
        exist = db.query(BusinessScenario).filter(
            BusinessScenario.account_id == req.account_id,
            BusinessScenario.scenario_type == req.scenario_type,
            BusinessScenario.status == "active").first()
        if exist:
            exist.input_data = json.dumps(req.input_data or {}, ensure_ascii=False)
            if req.scenario_type in ("more_leads", "new_niche", "new_city", "find_problem", "season", "social", "automate_sales", "website", "reactivation", "improve_sales"):
                st = _j(exist.steps_state)
                f0 = S.collect_facts(req.account_id, db=db)
                st["diagnosis"] = {"state":"done", "result": {"ok": True,
                    "summary": "Контекст аккаунта собран и план подготовлен",
                    "account_id": req.account_id,
                    "snapshot": {"active_items": f0.get("active_items"), "views": f0.get("views"), "contacts": f0.get("contacts"), "stats_date": f0.get("stats_date")}},
                    "result_at": datetime.now().isoformat(timespec="seconds")}
                exist.steps_state = json.dumps(st, ensure_ascii=False)
            exist.updated_at = datetime.now()
            db.commit()
            _autonomous_continue(exist.id, req.account_id, user, max_steps=5)
            db.expire_all()
            exist = db.query(BusinessScenario).filter(BusinessScenario.id == exist.id,
                                                      BusinessScenario.account_id == req.account_id).first()
            f = S.collect_facts(req.account_id, db=db)
            return {"status": "ok", "reused": True, "scenario": _scen_public(exist, f)}
        initial_state = {}
        if req.scenario_type in ("more_leads", "new_niche", "new_city", "find_problem", "season", "social", "automate_sales", "website", "reactivation", "improve_sales"):
            f0 = S.collect_facts(req.account_id, db=db)
            initial_state["diagnosis"] = {"state":"done", "result": {"ok": True,
                "summary": "Контекст аккаунта собран и план подготовлен",
                "account_id": req.account_id,
                "snapshot": {"active_items": f0.get("active_items"), "views": f0.get("views"), "contacts": f0.get("contacts"), "stats_date": f0.get("stats_date")}},
                "result_at": datetime.now().isoformat(timespec="seconds")}
        row = BusinessScenario(
            account_id=req.account_id,
            scenario_type=req.scenario_type,
            input_data=json.dumps(req.input_data or {}, ensure_ascii=False),
            steps_state=json.dumps(initial_state, ensure_ascii=False), status="active")
        db.add(row)
        db.commit()
        db.refresh(row)
        _bump_metrics(row, scenario_type=req.scenario_type)
        db.commit()
        _say(req.account_id, "scenario_start",
             "начал работу по задаче «%s»" % (S.CATALOG.get(req.scenario_type, {}).get("title") or req.scenario_type))
        _autonomous_continue(row.id, req.account_id, user, max_steps=5)
        db.expire_all()
        row = db.query(BusinessScenario).filter(BusinessScenario.id == row.id,
                                                BusinessScenario.account_id == req.account_id).first()
        f = S.collect_facts(req.account_id, db=db)
        return {"status": "ok", "reused": False, "scenario": _scen_public(row, f)}
    finally:
        db.close()


@router.get("/scenario/{scenario_id}")
def get_scenario(scenario_id: int, account_id: str, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        _require_account_access(db, user, account_id)
        row = db.query(BusinessScenario).filter(
            BusinessScenario.id == scenario_id,
            BusinessScenario.account_id == account_id).first()
        if not row:
            raise HTTPException(404, "Сценарий не найден")
        _autonomous_continue(scenario_id, account_id, user, max_steps=5)
        db.expire_all()
        row = db.query(BusinessScenario).filter(BusinessScenario.id == scenario_id,
                                                BusinessScenario.account_id == account_id).first()
        f = S.collect_facts(account_id, db=db)
        return {"status": "ok", "scenario": _scen_public(row, f)}
    finally:
        db.close()


class StepRequest(BaseModel):
    account_id: str
    scenario_id: int
    step_key: str
    action: str          # done | skip | later | reset


@router.post("/scenario/step")
def mark_step(req: StepRequest, user=Depends(get_current_user)):
    if req.action not in ("done", "skip", "later", "reset"):
        raise HTTPException(400, "Неизвестное действие")
    db = SessionLocal()
    try:
        _require_account_access(db, user, req.account_id)
        row = db.query(BusinessScenario).filter(
            BusinessScenario.id == req.scenario_id,
            BusinessScenario.account_id == req.account_id).first()
        if not row:
            raise HTTPException(404, "Сценарий не найден")
        state = _j(row.steps_state)
        if req.action == "reset":
            state.pop(req.step_key, None)
        else:
            state[req.step_key] = {
                "state": {"done": "done", "skip": "skipped", "later": "later"}[req.action],
                "ts": datetime.now().isoformat(timespec="seconds")}
        row.steps_state = json.dumps(state, ensure_ascii=False)
        row.updated_at = datetime.now()
        db.commit()
        db.refresh(row)
        f = S.collect_facts(req.account_id, db=db)
        return {"status": "ok", "scenario": _scen_public(row, f)}
    finally:
        db.close()


class ScenarioIdRequest(BaseModel):
    account_id: str
    scenario_id: int


@router.post("/scenario/cancel")
def cancel(req: ScenarioIdRequest, user=Depends(get_current_user)):
    return _set_status(req, "cancelled", user)


@router.post("/scenario/complete")
def complete(req: ScenarioIdRequest, user=Depends(get_current_user)):
    return _set_status(req, "completed", user)


def _set_status(req, status, user):
    db = SessionLocal()
    try:
        _require_account_access(db, user, req.account_id)
        row = db.query(BusinessScenario).filter(
            BusinessScenario.id == req.scenario_id,
            BusinessScenario.account_id == req.account_id).first()
        if not row:
            raise HTTPException(404, "Сценарий не найден")
        row.status = status
        _title = S.CATALOG.get(row.scenario_type, {}).get("title") or row.scenario_type
        _next = "more_leads" if row.scenario_type == "revive" else None
        _finish_metrics(row, status, next_scenario=_next)
        row.updated_at = datetime.now()
        db.commit()
        _say(req.account_id, "scenario_finish",
             ("завершил задачу «%s»" if status == "completed" else "остановил задачу «%s»") % _title)
        return {"status": "ok", "new_status": status}
    finally:
        db.close()


class DoForRequest(BaseModel):
    account_id: str


@router.post("/do_for")
def do_for(req: DoForRequest):
    """Существующее «сделаю за тебя» из onboarding.py. Граница не меняется:
    доводит максимум до черновиков, публикация всегда за клиентом."""
    try:
        from app.onboarding import do_for as _do
        return {"status": "ok", "result": _do(req.account_id, force=True)}
    except Exception as e:
        return {"status": "error", "message": str(e)[:300]}



MAX_CONFIRM_BATCH = 10   # больше десяти за одно подтверждение — запрещено стандартом


def _say(account_id, action, phrase, actor="boris"):
    """Запись в общий журнал БОРИСа. Пишем БИЗНЕС-действие человеческим языком,
    а не имя метода: клиент читает «проверил кабинет», а не «republish_check»."""
    try:
        from app.api.avito import _audit_log
        _audit_log(account_id, action, phrase, actor)
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=543)


def _bump_metrics(row, **kw):
    """Копим наблюдаемость в поле metrics. Ничего из steps_state не дублируем —
    только то, что оттуда не вытащить: время, длительность, счётчики."""
    m = _j(row.metrics) if getattr(row, "metrics", None) else {}
    if not m.get("started_at"):
        m["started_at"] = (row.created_at or datetime.now()).isoformat(timespec="seconds")
    for k, v in kw.items():
        if k == "actions_done":
            m[k] = int(m.get(k, 0)) + 1
        elif v is not None:
            m[k] = v
    row.metrics = json.dumps(m, ensure_ascii=False)
    return m


def _finish_metrics(row, status, next_scenario=None):
    from datetime import datetime as _dt
    m = _j(row.metrics) if getattr(row, "metrics", None) else {}
    started = m.get("started_at") or (row.created_at or _dt.now()).isoformat(timespec="seconds")
    fin = _dt.now()
    try:
        dur = int((fin - _dt.fromisoformat(started)).total_seconds())
    except Exception:
        dur = None
    m.update({"started_at": started, "finished_at": fin.isoformat(timespec="seconds"),
              "duration_sec": dur, "result": status})
    if next_scenario:
        m["next_scenario"] = next_scenario
    row.metrics = json.dumps(m, ensure_ascii=False)
    return m

# ------------------------------------------------- действия внутри сценария
class ActionRequest(BaseModel):
    account_id: str
    scenario_id: int
    step_key: str
    action: str
    payload: Optional[Dict[str, Any]] = None
    confirm: bool = False


# Белый список: только существующие эндпоинты. Ничего нового не пишем.
# apply и duplicate помечены как меняющие живые данные — без confirm идут
# в режиме предпросмотра либо возвращают needs_confirmation.
# ТОЛЬКО безопасные действия. republish_apply СОЗНАТЕЛЬНО исключён:
# при autopilot_settings.mode == "always_auto" он снимает объявления МГНОВЕННО,
# без подтверждения (avito.py:3836, флага confirmed в модели нет). 28.07 это
# сняло живое объявление у клиента прямо из теста. Плюс republish_check находит
# сотни кандидатов — массовая кнопка сменила бы половину фида одним нажатием.
# Снятие объявлений делается во вкладке «Объявления», где владелец видит каждое.
# republish_apply ВЕРНУЛСЯ как действие уровня CONFIRM (стандарт, раздел 5):
# не больше 10 за подтверждение, клиент видит точный список, проверяем
# autopilot_settings и предупреждаем, пишем в журнал. Автотестам запрещён.
_ALLOWED = ("republish_check", "republish_settings", "set_republish_settings",
            "auto_duplicate_run", "republish_apply",
            # avito_start: перенос товаров с сайта. generate_feed и publish_*
            # сюда НЕ добавляются — сценарий физически не может опубликовать.
            "parse_site", "parsed_products", "to_drafts", "rewrite_draft", "feed_check",
            "seller_defaults", "card_fields", "city_research",
            "marketing_prepare", "reactivation_prepare", "sales_prepare",
            "sitebuild_prepare", "social_prepare", "mop_prepare", "season_prepare",
            "scenario_measure", "campaign_safe_export", "market_launch_prepare")

MAX_REWRITE_BATCH = 10   # уникализация — платные вызовы модели, лимит обязателен
DEFAULT_PARSE_LIMIT = 20  # 0 в парсере означает «весь каталог» — для сценария это слишком


@router.post("/scenario/action")
def scenario_action(req: ActionRequest, user=Depends(get_current_user)):
    if req.action not in _ALLOWED:
        raise HTTPException(400, "Действие не разрешено")
    db = SessionLocal()
    try:
        _require_account_access(db, user, req.account_id)
        access_facts = S.collect_facts(req.account_id, db=db)
        if not access_facts.get("has_active_access"):
            raise HTTPException(402, "Нужен активный тариф или 4-дневный тестовый период")
        row = db.query(BusinessScenario).filter(
            BusinessScenario.id == req.scenario_id,
            BusinessScenario.account_id == req.account_id).first()
        if not row:
            raise HTTPException(404, "Сценарий не найден")

        p = req.payload or {}
        acc = req.account_id
        result = {}
        # BUSINESS_SCENARIO_SANCTIONED_CAPABILITY_CONTRACT: policy is explicit
        # metadata, not inferred from action names. Existing module branches below
        # remain authoritative executors; this layer only gates and projects them.
        from app import scenario_capabilities as SC
        _scenario_input = _j(row.input_data) or {}
        _state_before = _j(row.steps_state) or {}
        _prev_result = ((_state_before.get(req.step_key) or {}).get("result") or {}) if isinstance(_state_before, dict) else {}
        _resume_ref = _prev_result.get("task_id") if req.action == "city_research" else None
        # Exact approval scope must include server-resolved targets/defaults, not only
        # what the browser happened to send. This prevents stale approval reuse.
        p = dict(p)
        if req.action == "rewrite_draft":
            from app.api.avito import _load_drafts as _approval_load_drafts
            _drafts = _approval_load_drafts(acc) or []
            _todo = [d for d in _drafts if isinstance(d, dict)
                     and not ("{" in str(d.get("title", "")) and "|" in str(d.get("title", "")))]
            _target = [{"id": str(d.get("id") or ""), "title": str(d.get("title") or ""),
                        "description": str(d.get("description") or "")} for d in _todo[:MAX_REWRITE_BATCH]]
            p["draft_batch_fingerprint"] = __import__('hashlib').sha256(
                json.dumps(_target, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        elif req.action == "auto_duplicate_run":
            p.setdefault("max_per_item", 2); p.setdefault("daily_limit", 5); p.setdefault("min_contacts", 1)
        elif req.action == "to_drafts":
            p.setdefault("batch_label", "site_import"); p.setdefault("address", "")

        _precondition_failure = None
        _module_approval_valid = False
        if req.action == "republish_apply":
            _feed = S._load(db, acc, "feed_items", []) or []
            _owned_ids = {str(x.get("id")) for x in _feed if isinstance(x, dict) and x.get("id")}
            _requested_ids = {str(x) for x in (p.get("item_ids") or []) if str(x).strip()}
            _missing_ids = sorted(_requested_ids - _owned_ids)
            _ap = S._load(db, acc, "autopilot_settings", {}) or {}
            _module_approval_valid = str(_ap.get("mode") or "always_ask") == "always_auto"
            if _missing_ids:
                _precondition_failure = {"allowed": False, "result_state": "FAILED_TERMINAL",
                                         "failure_class": "POLICY_BLOCKED",
                                         "reason": "Target does not belong to this account feed",
                                         "cross_account_or_unknown_target": _missing_ids}
            elif req.confirm and not _module_approval_valid:
                _precondition_failure = {"allowed": False, "result_state": "OWNER_REQUIRED",
                                         "failure_class": "POLICY_BLOCKED",
                                         "owner_approval_required": True,
                                         "module_approval_source": "autopilot_settings.mode=always_auto",
                                         "reason": "Для этого действия authoritative Avito-модуль требует включённый режим «Доверить работу BORIS». Разовое подтверждение Scenario не подменяет этот module gate."}
        elif req.action == "to_drafts":
            _parsed = S._load(db, acc, "parsed_products", []) or []
            _parsed_list = _parsed if isinstance(_parsed, list) else ((_parsed.get("products") or []) if isinstance(_parsed, dict) else [])
            _indices = [int(x) for x in (p.get("indices") or []) if str(x).strip().lstrip("-").isdigit()]
            _bad_indices = sorted({x for x in _indices if x < 0 or x >= len(_parsed_list)})
            if _bad_indices:
                _precondition_failure = {"allowed": False, "result_state": "FAILED_TERMINAL",
                                         "failure_class": "POLICY_BLOCKED",
                                         "reason": "Selected product does not belong to account parsed_products",
                                         "cross_account_or_unknown_target": _bad_indices}
        _policy = SC.validate_call(req.action, acc, p, _scenario_input, confirm=req.confirm,
                                   resume_ref=_resume_ref,
                                   previous_approval_fingerprint=_prev_result.get("approval_fingerprint"),
                                   authoritative_module_approval=_module_approval_valid)
        if _precondition_failure:
            _policy = _precondition_failure
        _capability = SC.get_capability(req.action) or {}
        _execution_fp = SC.execution_fingerprint(req.action, acc, row.id, req.step_key, p, _scenario_input)
        if (_policy.get("allowed") and _capability.get("operation_class") != "READ_ONLY"
                and _prev_result.get("execution_committed")
                and _prev_result.get("execution_fingerprint") == _execution_fp
                and not _capability.get("supports_resume")):
            reused = dict(_prev_result)
            reused["idempotent_reuse"] = True
            reused["duplicate_execution_prevented"] = True
            return {"status": "ok", "result": reused,
                    "scenario": _scen_public(row, S.collect_facts(acc, db=db))}
        if not _policy.get("allowed"):
            blocked = {"ok": False,
                       "owner_required": bool(_policy.get("owner_approval_required")),
                       "owner_input_required": bool(_policy.get("owner_input_required")),
                       "failure_class": _policy.get("failure_class"),
                       "approval_fingerprint": _policy.get("approval_fingerprint"),
                       "next_approval_fingerprint": _policy.get("next_approval_fingerprint"),
                       "stale_approval": bool(_policy.get("stale_approval")),
                       "missing_inputs": _policy.get("missing_inputs") or [],
                       "cross_account_or_unknown_target": _policy.get("cross_account_or_unknown_target") or [],
                       "module_approval_source": _policy.get("module_approval_source"),
                       "summary": _policy.get("reason") or "Capability policy blocked execution",
                       "capability_contract": SC.get_capability(req.action),
                       "orchestration_result": {"capability_id": req.action, "state": _policy.get("result_state")}}
            # Policy/owner waits are part of authoritative scenario progress and must
            # survive reload; persisting them does not execute the underlying module.
            _blocked_state = _j(row.steps_state) or {}
            _blocked_prev = _blocked_state.get(req.step_key) or {}
            _blocked_prev["result"] = blocked
            _blocked_prev["result_at"] = datetime.now().isoformat(timespec="seconds")
            _blocked_state[req.step_key] = _blocked_prev
            row.steps_state = json.dumps(_blocked_state, ensure_ascii=False)
            row.updated_at = datetime.now()
            db.commit(); db.refresh(row)
            return {"status": "ok", "result": blocked,
                    "scenario": _scen_public(row, S.collect_facts(acc, db=db))}
        try:
            if req.action in ("marketing_prepare", "reactivation_prepare", "sales_prepare", "sitebuild_prepare", "social_prepare", "mop_prepare", "season_prepare"):
                # Safe orchestration bridge: inspect the authoritative module state and persist
                # the result in this scenario. No external send/publication and no second engine.
                from sqlalchemy import text as _orch_sql
                def _count(sql, params=None):
                    try: return int(db.execute(_orch_sql(sql), params or {"a":acc}).scalar() or 0)
                    except Exception: db.rollback(); return 0
                if req.action == "marketing_prepare":
                    fmk=S.collect_facts(acc,db=db); views=int(fmk.get("views") or 0); contacts=int(fmk.get("contacts") or 0)
                    preps=[]
                    for rr in db.execute(_orch_sql("select key,value from storage where account_id=:a and key like 'kpi_prepare:%%' order by id desc"),{"a":acc}).fetchall():
                        try: op=json.loads(rr[1] or "{}") or {}
                        except Exception: op={}
                        preps.append(op)
                    active=[x for x in preps if str(x.get("status") or "") not in ("cancelled_unsupported","rejected","failed","completed") ]
                    applylog=S._load(db,acc,"kpi_apply_log",[]) or []; last=(applylog[-1] if isinstance(applylog,list) and applylog else {})
                    lifecycle=str(last.get("status") or (active[0].get("status") if active else "analysis_ready")); win=last.get("observation_window") or {}
                    target=(fmk.get("kpi") or {}).get("target_leads_per_day"); cpl=(fmk.get("kpi") or {}).get("max_cost_per_lead_rub")
                    diagnosis=("Есть просмотры, но обращений мало: %s из %s; %s объявлений без просмотров. Цель — %s заявок/день, CPL до %s ₽."%(contacts,views,fmk.get("zero_view_items") or 0,target or "не задана",cpl or "не задан"))
                    result={"ok":True,"module":"AI Marketing","views":views,"contacts":contacts,"diagnosis":diagnosis,"existing_action_reused":bool(active or last),"duplicate_optimization":False,"lifecycle":lifecycle,"observation_window":win,"summary":diagnosis + (" BORIS использует уже существующую оптимизацию; новую не создавал." if (active or last) else " BORIS передал diagnosis существующему AI Marketing без запуска платного действия."),"route":"/dashboard/marketing"}
                elif req.action == "reactivation_prepare":
                    n=_count("select count(*) from reactivation_candidates where account_id=:a and status in ('candidate','needs_review','approved','scheduled','cooldown')")
                    result={"ok":True,"module":"Reactivation","candidates":n,"summary":"Существующая реактивация готова: %s клиентов требуют возврата/наблюдения. Сообщения клиентам не отправлялись."%n,"route":"/reactivation"}
                elif req.action == "sales_prepare":
                    # CRM schema is owner-scoped; deals carry the Avito account id, while tasks
                    # inherit account scope through their deal. Keep the scenario strictly account-scoped.
                    deals=_count("select count(*) from boris_crm_deals where avito_account_id=:a")
                    tasks=_count("select count(*) from boris_crm_tasks t join boris_crm_deals d on d.id=t.deal_id where d.avito_account_id=:a and t.status not in ('done','completed')")
                    result={"ok":True,"module":"CRM/MOP/ROP","deals":deals,"open_tasks":tasks,"summary":"Собрал существующий sales-контур выбранного аккаунта: сделок %s, открытых задач %s. Следующий шаг выполняется CRM/MOP/ROP без нового sales engine."%(deals,tasks),"route":"/rop"}
                elif req.action == "sitebuild_prepare":
                    n=_count("select count(*) from storage where account_id=:a and (key ilike 'site%%' or key ilike '%%website%%')")
                    result={"ok":True,"module":"Sitebuild","existing_state_records":n,"summary":"Передал задачу в существующий контур создания сайта; найдено %s сохранённых записей проекта. Внешняя публикация не выполнялась."%n,"route":"/dashboard/sitebuild"}
                elif req.action == "social_prepare":
                    n=_count("select count(*) from storage where account_id=:a and (key ilike 'social%%' or key ilike '%%content_plan%%')")
                    result={"ok":True,"module":"Social","existing_state_records":n,"summary":"Собрал существующее состояние Social: %s записей/планов. BORIS может продолжить подготовку в штатном Social-контуре; реальная отправка не выполнялась."%n,"route":"/social"}
                elif req.action == "season_prepare":
                    inp=_j(row.input_data) or {}; fseason=S.collect_facts(acc,db=db)
                    campaigns=_count("select count(distinct campaign_id) from campaign_items where account_id=:a")
                    social=_count("select count(*) from storage where account_id=:a and (key ilike 'social%%' or key ilike '%%content_plan%%')")
                    result={"ok":True,"module":"Campaign/Marketing/Social","period":str(inp.get("period") or ""),"priority":str(inp.get("priority") or ""),"campaigns_reused":campaigns,"social_state_records":social,"calendar":["T-30: проверить спрос и слабые карточки","T-14: подготовить объявления и контент","T-7: валидировать готовность без внешней публикации","Старт: измерять показы → обращения → продажи"],"summary":"Сезонный execution-plan подготовлен на данных аккаунта. Существующих кампаний: %s, Social-state: %s. Новая публикация и платная генерация не запускались."%(campaigns,social),"route":"/dashboard/campaign"}
                else:
                    bound=bool(db.execute(_orch_sql("select 1 from ai_bindings where product='mop' and account_id=:a limit 1"),{"a":acc}).first())
                    cfg=S._load(db,acc,"mop_crm_sales_settings",{}) or {}
                    result={"ok":True,"module":"MOP","bound":bound,"enabled":bool(cfg.get("enabled",True)) if isinstance(cfg,dict) else True,"summary":"Проверил существующий AI-менеджер: %s. Настройки диалога и расписание остаются account-scoped."%("подключён" if bound else "не подключён"),"route":"/messages/mop-settings"}

            elif req.action == "city_research":
                inp = _j(row.input_data) or {}
                query = str(inp.get("product") or inp.get("direction") or "").strip()
                city = str(inp.get("region") or inp.get("city") or "").strip()
                state_now = _j(row.steps_state) or {}
                previous = ((state_now.get(req.step_key) or {}).get("result") or {}) if isinstance(state_now, dict) else {}
                task_id = previous.get("task_id")
                if not query or not city:
                    result = {"ok": False, "summary": "Не хватает продукта/направления или региона для анализа"}
                elif task_id:
                    from sqlalchemy import text as _city_sql
                    task = db.execute(_city_sql("select status,result,error_message,updated_at from tasks where id=:i and task_type='city_analysis'"), {"i":int(task_id)}).mappings().first()
                    if not task:
                        result = {"ok":False,"task_id":task_id,"summary":"Ранее запущенная задача анализа рынка не найдена; повторный внешний запуск без нового подтверждения не выполняю."}
                    elif str(task.get("status") or "") in ("queued","running"):
                        result = {"ok":True,"task_id":task_id,"external_wait":True,"task_status":task.get("status"),"summary":"Анализ рынка уже запущен и выполняется. Дополнительного действия владельца не требуется."}
                    elif str(task.get("status") or "") == "done":
                        try: payload = json.loads(task.get("result") or "{}") if task.get("result") else {}
                        except Exception: payload = {}
                        result = {"ok":True,"task_id":task_id,"external_wait":False,"task_status":"done","analysis_result":payload,"summary":"Анализ рынка завершён штатным parser. Повторный внешний запрос не запускался."}
                    else:
                        result = {"ok":False,"task_id":task_id,"task_status":task.get("status"),"summary":"Анализ рынка завершился ошибкой: %s" % str(task.get("error_message") or "неизвестная ошибка")[:180]}
                elif not req.confirm:
                    result = {"ok": False, "need_confirm": True, "owner_required":True,
                              "summary": "Запущу существующий анализ рынка Avito по запросу «%s» в регионе «%s». Это внешний market-research запрос; без вашего подтверждения не запускаю." % (query, city)}
                else:
                    from app.api.parser import city_analysis_async, CityAnalysisRequest
                    r = city_analysis_async(CityAnalysisRequest(query=query, cities=[city])) or {}
                    result = {"ok": r.get("status") == "ok", "task_id": r.get("task_id"), "external_wait": bool(r.get("task_id")),
                              "summary": "Анализ рынка поставлен в работу%s. После запуска BORIS продолжит отслеживать эту же задачу без повторного подтверждения." % ((" (задача #%s)" % r.get("task_id")) if r.get("task_id") else "")}

            elif req.action == "scenario_measure":
                current = S.collect_facts(acc, db=db)
                state_now = _j(row.steps_state) or {}
                previous = ((state_now.get(req.step_key) or {}).get("result") or {}) if isinstance(state_now, dict) else {}
                baseline = previous.get("baseline") if isinstance(previous, dict) else None
                funnel=current.get("sales_funnel") or {}
                now_snapshot = {"captured_date":datetime.now().date().isoformat(),"stats_date":current.get("stats_date"),"views":int(current.get("views") or 0),"contacts":int(current.get("contacts") or 0),"dead_items":int(current.get("dead_items") or 0),"sales_funnel":funnel}
                sales_types={"reactivation","improve_sales","automate_sales"}
                unlinked_types={"social","website"}
                if row.scenario_type in unlinked_types:
                    result={"ok":True,"waiting_measurement":True,"external_wait":True,"baseline":baseline or now_snapshot,"current":now_snapshot,"measurement_source":"authoritative %s analytics only" % ("Social" if row.scenario_type=="social" else "Sitebuild/CRM attribution"),"measurement_window":"after real external publication/traffic exists","success_condition":"attributed business outcome, not technical preparation","summary":"Техническая подготовка завершена, но доказанного business outcome ещё нет. Несвязанную Avito-статистику к этому сценарию не приписываю; действий владельца для самого ожидания не требуется."}
                elif not baseline:
                    source="existing CRM/MOP/ROP funnel" if row.scenario_type in sales_types else "daily_stats + existing CRM/MOP/ROP facts"
                    result={"ok":True,"waiting_measurement":True,"external_wait":True,"baseline":now_snapshot,"measurement_source":source,"measurement_window":"next independent observation window","success_condition":"target business KPI improves, not views alone","summary":"Зафиксировал исходные показатели. Жду независимое окно измерения; действий владельца не требуется."}
                elif row.scenario_type in sales_types:
                    bfun=baseline.get("sales_funnel") or {}; keys=("engaged","qualified","phones","target_actions","deals")
                    changed=any((funnel.get(k) or 0)!=(bfun.get(k) or 0) for k in keys)
                    if not changed or str(now_snapshot.get("captured_date")) == str(baseline.get("captured_date")):
                        result={"ok":True,"waiting_measurement":True,"external_wait":True,"baseline":baseline,"current":now_snapshot,"measurement_source":"existing CRM/MOP/ROP funnel","summary":"Нового независимого sales-окна ещё нет. Продолжаю ждать без участия владельца."}
                    else:
                        delta={k:int(funnel.get(k) or 0)-int(bfun.get(k) or 0) for k in keys}; improved=delta.get("deals",0)>0 or delta.get("target_actions",0)>0 or delta.get("phones",0)>0
                        decision="KEEP" if improved else "TRY_NEXT_ACTION"
                        result={"ok":True,"waiting_measurement":False,"external_wait":False,"baseline":baseline,"current":now_snapshot,"delta":delta,"business_outcome":"sales funnel improved" if improved else "no target funnel improvement","decision":decision,"summary":"Sales measurement завершён. Решение BORIS: %s."%decision}
                elif not current.get("stats_date") or str(current.get("stats_date")) == str(baseline.get("stats_date")):
                    result={"ok":True,"waiting_measurement":True,"external_wait":True,"baseline":baseline,"current":now_snapshot,"measurement_source":"daily_stats + existing CRM/MOP/ROP facts","summary":"Нового статистического окна ещё нет. BORIS продолжит с этим же baseline без ручного перезапуска."}
                else:
                    dc=now_snapshot["contacts"]-int(baseline.get("contacts") or 0); dv=now_snapshot["views"]-int(baseline.get("views") or 0)
                    if dc>0: decision="KEEP"; outcome="business KPI improved"
                    elif dv>0 and dc<=0: decision="TRY_NEXT_ACTION"; outcome="views changed but contacts did not improve"
                    else: decision="CONTINUE"; outcome="insufficient/inconclusive change"
                    result={"ok":True,"waiting_measurement":False,"external_wait":False,"baseline":baseline,"current":now_snapshot,"delta":{"views":dv,"contacts":dc},"business_outcome":outcome,"decision":decision,"summary":"Измерение завершено: просмотры Δ%s, обращения Δ%s. Решение BORIS: %s."%(dv,dc,decision)}

            elif req.action == "campaign_safe_export":
                facts = S.collect_facts(acc, db=db); campaign = facts.get("campaign_ready") or {}
                cid = int(campaign.get("id") or 0)
                if not cid:
                    result={"ok":False,"summary":"Готовая Campaign для безопасного экспорта не найдена; новую кампанию не создаю."}
                elif int(campaign.get("approved") or 0) < int(campaign.get("items") or 0):
                    result={"ok":False,"owner_required":True,"summary":"BORIS уже подготовил и проверил кампанию. Для safe export требуется согласовать оставшиеся объявления; внешней публикации не будет."}
                else:
                    import os, hashlib, datetime as _dt
                    from app.models.campaign_item import CampaignItem
                    from app.api.campaigns import _ff_validate_row, _ff_xml
                    rows=db.query(CampaignItem).filter(CampaignItem.campaign_id==cid,CampaignItem.account_id==acc,CampaignItem.status=="ready").order_by(CampaignItem.id).all()
                    bad=[r.id for r in rows if not _ff_validate_row(r,db).get("valid")]
                    if bad or not rows:
                        result={"ok":False,"summary":"Safe export заблокирован внутренней проверкой: %s" % ("ошибки в карточках " + ",".join(map(str,bad[:10])) if bad else "нет согласованных карточек")}
                    else:
                        xml=_ff_xml(rows); root="/root/BORIS/backend/data/feed_exports"; os.makedirs(root,exist_ok=True)
                        path=os.path.join(root,"scenario_%s_campaign_%s_%s.xml"%(row.id,cid,_dt.datetime.now().strftime("%Y%m%d_%H%M%S")))
                        with open(path,"w",encoding="utf-8") as fh: fh.write(xml)
                        result={"ok":True,"technical_completion":True,"external_upload":False,"campaign_id":cid,"items":len(rows),"file":path,"sha256":hashlib.sha256(xml.encode()).hexdigest(),"summary":"Безопасный feed собран из %s согласованных объявлений. В Avito ничего не отправлено."%len(rows)}

            elif req.action == "market_launch_prepare":
                inp=_j(row.input_data) or {}; city=str(inp.get("region") or inp.get("city") or "").strip(); product=str(inp.get("product") or inp.get("direction") or "").strip()
                from sqlalchemy import text as _market_sql
                existing=db.execute(_market_sql("select ci.id,ci.campaign_id,ci.status,ci.geo,ci.segment from campaign_items ci where ci.account_id=:a and (:c='' or lower(coalesce(ci.geo,''))=lower(:c)) and (:p='' or lower(coalesce(ci.segment,''))=lower(:p)) order by ci.id"),{"a":acc,"c":city,"p":product}).mappings().all()
                ready=sum(1 for x in existing if str(x.get("status") or "") == "ready")
                spec={"product":product,"city":city,"variant_goal":"minimal deterministic test","external_publication":False,"paid_generation_started":False}
                if existing:
                    result={"ok":True,"module":"Campaign/Feed Factory","existing_campaign_reused":True,"campaign_ids":sorted(set(int(x["campaign_id"]) for x in existing if x.get("campaign_id"))),"items":len(existing),"ready":ready,"test_spec":spec,"summary":"Нашёл существующую Campaign/Feed Factory coverage для теста: %s карточек, готово %s. Новую кампанию не создавал."%(len(existing),ready),"route":"/dashboard/campaign"}
                else:
                    result={"ok":True,"owner_required":True,"module":"Campaign/Feed Factory","existing_campaign_reused":False,"test_spec":spec,"requires_paid_or_owner_generation":True,"summary":"Deterministic test spec подготовлен, но существующих карточек для этого города/направления нет. Перед платной генерацией или созданием новых клиентских материалов требуется решение владельца.","route":"/dashboard/campaign"}

            elif req.action == "republish_check":
                from app.api.avito import republish_check
                r = republish_check(acc) or {}
                cands = r.get("candidates") or []
                # Обогащаем ценой и адресом ИЗ ФИДА: у клиента полсписка называется
                # одинаково («Брусчатка»), и без различающих признаков чекбоксы
                # бесполезны — человек не понимает, что именно отмечает.
                feed = S._load(db, acc, "feed_items", []) or []
                fmap = {}
                if isinstance(feed, list):
                    for it in feed:
                        if isinstance(it, dict):
                            fmap[str(it.get("id"))] = it
                items = []
                for c in cands[:20]:
                    cid = str(c.get("id") or "")
                    src = fmap.get(cid) or {}
                    price = src.get("price") or src.get("Price")
                    addr = str(src.get("address") or src.get("Address") or "").strip()
                    items.append({
                        "id": cid,
                        "title": S._short_title(c.get("title")),
                        "reason": c.get("reason"),
                        "price": price,
                        "address": addr[:38],
                        "short_id": cid[-6:] if cid else "",
                        "views": c.get("views"),
                    })
                # Часть объявлений — ВАРИАНТЫ одного предложения: тот же товар,
                # та же цена, тот же город, отличается только текст под уникализацию.
                # Различить их нельзя и не нужно — вместо выдумывания признаков
                # честно нумеруем варианты и помечаем группу.
                groups = {}
                for it in items:
                    key = (it["title"], str(it.get("price")), it.get("address"))
                    groups.setdefault(key, []).append(it)
                for key, grp in groups.items():
                    for n, it in enumerate(grp, 1):
                        it["variant_no"] = n
                        it["group_size"] = len(grp)
                        it["is_variant"] = len(grp) > 1
                        it["first_of_group"] = (n == 1 and len(grp) > 1)
                        it.pop("short_id", None)
                result = {"ok": True, "count": len(cands), "items": items,
                          "has_variants": any(i.get("is_variant") for i in items),
                          "summary": "Нашёл %d %s для замены" % (
                              len(cands), S._plural(len(cands), "объявление",
                                                    "объявления", "объявлений"))}

            elif req.action == "republish_settings":
                from app.api.avito import get_republish_settings
                r = get_republish_settings(acc) or {}
                result = {"ok": True, "settings": r.get("settings")}

            elif req.action == "set_republish_settings":
                from app.api.avito import set_republish_settings, RepublishSettingsRequest
                rq = RepublishSettingsRequest(
                    account_id=acc,
                    min_views_no_contact=int(p.get("min_views_no_contact", 10)),
                    zero_views_days=int(p.get("zero_views_days", 7)),
                    enabled=bool(p.get("enabled", True)))
                set_republish_settings(rq)
                result = {"ok": True, "summary": "Пороги сохранены: %s просмотров без обращений, %s дней без просмотров"
                          % (rq.min_views_no_contact, rq.zero_views_days)}

            elif req.action == "parse_site":
                url = str(p.get("url") or "").strip()
                if not url:
                    url = str((_j(row.input_data) or {}).get("site") or "").strip()
                if not url:
                    result = {"ok": True, "owner_required": True, "owner_input_required": True,
                              "summary": "Сайт не указан — это допустимо. Добавьте ассортимент в Campaign/объявления вручную или файлом; BORIS продолжит сценарий с сохранёнными товарами. Ничего наружу не отправлялось.",
                              "route": "/dashboard/campaign"}
                else:
                    from app.api.parser import parse_site, ParseRequest
                    r = parse_site(ParseRequest(
                        url=url, account_id=acc,
                        limit=int(p.get("limit") or DEFAULT_PARSE_LIMIT))) or {}
                    n = len(r.get("products") or [])
                    _say(acc, "site_parsed", "Разобрал сайт и нашёл %d %s" % (
                        n, S._plural(n, "товар", "товара", "товаров")))
                    if n == 0:
                        result = {"ok": True, "count": 0,
                                  "summary": "На этой странице товаров не нашёл. "
                                             "Чаще всего помогает дать ссылку прямо на страницу "
                                             "каталога, а не на главную. Если каталог "
                                             "подгружается скриптом при прокрутке — разбор "
                                             "не сработает, товары можно загрузить файлом."}
                    else:
                        result = {"ok": True, "count": n,
                                  "summary": "Нашёл %d %s на вашем сайте. "
                                             "Ничего никуда не опубликовано." % (
                                      n, S._plural(n, "товар", "товара", "товаров"))}

            elif req.action == "parsed_products":
                pp = S._load(db, acc, "parsed_products", []) or []
                lst = pp if isinstance(pp, list) else (pp.get("products") or [])
                items = []
                for i, it in enumerate(lst[:40]):
                    if not isinstance(it, dict):
                        continue
                    items.append({"index": i,
                                  "title": str(it.get("title") or "")[:70],
                                  "price": it.get("price"),
                                  "photos": len(it.get("images") or it.get("photos") or [])})
                result = {"ok": True, "count": len(lst), "items": items,
                          "summary": "Показываю %d из %d %s" % (
                              len(items), len(lst),
                              S._plural(len(lst), "товара", "товаров", "товаров"))}

            elif req.action == "to_drafts":
                idx = [int(x) for x in (p.get("indices") or []) if str(x).strip().isdigit()]
                if not idx:
                    result = {"ok": False, "summary": "Не выбрано ни одного товара"}
                elif not req.confirm:
                    result = {"ok": False, "need_confirm": True, "indices": idx,
                              "summary": "Создам черновики по %d %s. "
                                         "Черновики видны только вам, на Avito ничего не уйдёт." % (
                                  len(idx), S._plural(len(idx), "товару", "товарам", "товарам"))}
                else:
                    from app.api.parser import (parsed_products_to_drafts,
                                                ProductsToDraftsRequest)
                    inp = _j(row.input_data) or {}
                    r = parsed_products_to_drafts(ProductsToDraftsRequest(
                        account_id=acc, indices=idx,
                        topic=str(inp.get("niche") or ""),
                        address=str(p.get("address") or ""),
                        batch_label=str(p.get("batch_label") or "site_import"))) or {}
                    made = r.get("created") or r.get("total") or len(idx)
                    _say(acc, "drafts_created", "Создал %d %s объявлений" % (
                        made, S._plural(made, "черновик", "черновика", "черновиков")))
                    result = {"ok": True, "created": made,
                              "summary": "Создал %d %s. На Avito ничего не опубликовано." % (
                                  made, S._plural(made, "черновик", "черновика", "черновиков"))}

            elif req.action == "rewrite_draft":
                from app.api.avito import (_load_drafts, rewrite_text,
                                           RewriteTextRequest, update_draft,
                                           DraftUpdateRequest)
                # Reuse the exact batch resolved for approval fingerprint in this
                # request; do not re-resolve a different paid target after approval.
                drafts = _drafts if '_drafts' in locals() else (_load_drafts(acc) or [])
                todo = _todo if '_todo' in locals() else [d for d in drafts
                        if isinstance(d, dict)
                        and not ("{" in str(d.get("title", "")) and "|" in str(d.get("title", "")))]
                if not todo:
                    result = {"ok": True, "done": 0,
                              "summary": "Все черновики уже уникализированы"}
                elif not req.confirm:
                    result = {"ok": False, "need_confirm": True, "pending": len(todo),
                              "batch": min(len(todo), MAX_REWRITE_BATCH),
                              "summary": "Перепишу тексты у %d из %d черновиков. "
                                         "Это платные обращения к модели." % (
                                  min(len(todo), MAX_REWRITE_BATCH), len(todo))}
                else:
                    done, failed = 0, 0
                    for d in todo[:MAX_REWRITE_BATCH]:
                        try:
                            rw = rewrite_text(RewriteTextRequest(
                                title=str(d.get("title") or ""),
                                description=str(d.get("description") or ""),
                                include_variants=True)) or {}
                            update_draft(str(d.get("id")), DraftUpdateRequest(
                                account_id=acc,
                                title=rw.get("title") or d.get("title"),
                                description=rw.get("description") or d.get("description")))
                            done += 1
                        except Exception:
                            failed += 1
                    _say(acc, "drafts_uniquified", "Сделал уникальными %d %s" % (
                        done, S._plural(done, "объявление", "объявления", "объявлений")))
                    left = max(0, len(todo) - done)
                    result = {"ok": True, "done": done, "failed": failed, "left": left,
                              "summary": "Переписал %d %s%s" % (
                                  done, S._plural(done, "текст", "текста", "текстов"),
                                  (", осталось %d — нажмите ещё раз" % left) if left else "")}

            elif req.action == "feed_check":
                from app.api.avito import _load_drafts
                from app.api.parser import validate_feed_xmlcheck
                import asyncio as _aio
                drafts = _load_drafts(acc) or []
                ids = [str(d.get("id")) for d in drafts if isinstance(d, dict) and d.get("id")]
                if not ids:
                    result = {"ok": False, "summary": "Нет черновиков для проверки"}
                else:
                    url = ("https://boris-ai.pro/api/avito/feed_preview/%s.xml?ids=%s"
                           % (acc, ",".join(ids)))
                    v = _aio.run(validate_feed_xmlcheck(url)) or {}
                    okv = bool(v.get("ok") or v.get("valid")
                               or "принят" in str(v.get("status") or "").lower())
                    _say(acc, "feed_checked", "Проверил файл выгрузки у Avito: %d %s" % (
                        len(ids), S._plural(len(ids), "объявление", "объявления", "объявлений")))
                    result = {"ok": bool(okv), "valid": okv, "count": len(ids), "details": v,
                              "result_type": "feed_validation",
                              "items": _parse_validator_report((v or {}).get("raw_text")),
                              "summary": ("Файл принят Avito, %d %s готовы к выгрузке"
                                          % (len(ids), S._plural(len(ids), "объявление",
                                                                 "объявления", "объявлений")))
                                         if okv else
                                         "Avito вернул замечания — смотрите подробности ниже"}

            elif req.action == "seller_defaults":
                from app.api.avito import (get_seller_defaults, set_seller_defaults,
                                           SellerDefaultsRequest)
                vals = p.get("values") or {}
                if vals:
                    r = set_seller_defaults(SellerDefaultsRequest(
                        account_id=acc, values=vals)) or {}
                    n = r.get("count") or 0
                    _say(acc, "seller_defaults",
                         "Запомнил настройки показа объявлений (%d)" % n)
                    result = {"ok": True, "saved": r.get("saved") or {}, "count": n,
                              "summary": "Запомнил %d %s. Подставлю их во все ваши "
                                         "объявления." % (
                                  n, S._plural(n, "настройку", "настройки", "настроек"))}
                else:
                    d = get_seller_defaults(acc) or {}
                    qs = d.get("questions") or []
                    result = {"ok": True, "questions": qs, "saved": d.get("saved") or {},
                              "summary": "Ответьте на %d %s — они одинаковы для всех "
                                         "ваших объявлений." % (
                                  len(qs), S._plural(len(qs), "вопрос", "вопроса", "вопросов"))}

            elif req.action == "card_fields":
                # Экран уточнений и готовности разом: по каждому черновику
                # определяем категорию через дерево, вытягиваем что можем из
                # текста, остаток собираем в вопросы. Одинаковые поля разных
                # карточек группируем — клиент отвечает один раз на все.
                from app.api.avito import _load_drafts
                from app.api.plan_items import _fill_category_params
                from app.services.tree_resolver import resolve_by_tree, extract_fields

                answers = p.get("answers") or {}
                _answers_saved = True
                if answers:
                    prev = S._load(db, acc, "card_answers", {}) or {}
                    prev.update({k: v for k, v in answers.items() if str(v).strip()})
                    # _save никогда не бросает и возвращает False при сбое записи.
                    # Без этой проверки клиент видел "Запомнил ...", хотя ответы
                    # не сохранились, и на следующем шаге его спрашивали заново.
                    _answers_saved = bool(S._save(db, acc, "card_answers", prev))
                    if _answers_saved:
                        _say(acc, "card_fields", "Запомнил %d %s по товарам" % (
                            len(answers), S._plural(len(answers), "уточнение",
                                                    "уточнения", "уточнений")))

                saved = S._load(db, acc, "card_answers", {}) or {}
                drafts = _load_drafts(acc) or []
                ready, need, groups = 0, 0, {}
                cards = []
                def _niche_of(card):
                    """Ниша для определения категории. Заголовок для этого не
                    годится: он маркетинговый и уникализированный — «Красивый
                    край дорожек» вместо «Бордюр садовый», модель уходит гадать.
                    Настоящая ниша лежит в batch_label вида «Конвейер: Бордюр
                    садовый» либо в category_id."""
                    bl = str(card.get("batch_label") or "")
                    if ":" in bl:
                        bl = bl.split(":", 1)[1]
                    bl = bl.strip()
                    return bl or str(card.get("category_id") or "").strip() \
                        or str(card.get("title") or "")[:60]

                for d in drafts[:30]:
                    if not isinstance(d, dict):
                        continue
                    title = str(d.get("title") or "")[:60]
                    niche = _niche_of(d)
                    # категорию ищем по нише, характеристики — по тексту объявления
                    text = "%s. %s" % (title, str(d.get("description") or "")[:600])
                    r = resolve_by_tree(niche, account_id=acc, db=db)
                    if r["status"] != "ok":
                        _cat_text = {
                            "no_template": "Категория определена, но структура полей ещё не подключена",
                            "no_leaf": "Похоже, нужная категория пока отсутствует в дереве BORIS",
                            "need_answer": "Не удалось однозначно определить категорию",
                            "not_found": "Не удалось определить категорию",
                            "no_confident_option": (
                                "Не удалось надёжно определить категорию. "
                                "Возможно, нужной категории пока нет в дереве BORIS."),
                        }
                        _st = str(r.get("status") or "")
                        _rs = str(r.get("reason") or "")
                        _code = _rs if _st == "awaiting_adviz_fields" else _st
                        _q = r.get("question") or {}
                        _card = {"id": d.get("id"), "title": title,
                                 "path": r.get("path") or "",
                                 "ready": False,
                                 "reason": (_cat_text.get(_code)
                                            or r.get("message")
                                            or "категория не определена"),
                                 "category_state": ("category_not_connected"
                                                    if _st == "awaiting_adviz_fields"
                                                    else (_st or "unknown")),
                                 "category_reason": (_code or "unknown"),
                                 "category_query": str(niche or "")}
                        if _st == "need_answer":
                            _card["category_question"] = str(_q.get("text") or "")
                            _card["category_options"] = list(_q.get("options") or [])
                        cards.append(_card)
                        need += 1
                        continue
                    base = _fill_category_params(niche, account_id=acc)
                    base.update({k: v for k, v in saved.items() if str(v).strip()})
                    e = extract_fields(text, r["template_id"], account_id=acc,
                                       existing=base, db=db)
                    miss = [m for m in e["missing"] if m["tag"] not in saved]
                    if miss:
                        need += 1
                    else:
                        ready += 1
                    for m in miss:
                        g = groups.setdefault(m["tag"], {"tag": m["tag"],
                                                         "label": m["label"],
                                                         "options": m["options"],
                                                         "cards": 0})
                        g["cards"] += 1
                    cards.append({"id": d.get("id"), "title": title,
                                  "path": r["path"], "ready": not miss,
                                  "filled": len(base) + len(e["extracted"]),
                                  "missing": [m["tag"] for m in miss][:10]})

                qs = sorted(groups.values(), key=lambda x: -x["cards"])
                total = ready + need
                result = {"ok": True, "ready": ready, "need": need, "total": total,
                          "cards": cards, "questions": qs[:20],
                          "summary": ("Готово к фиду: %d из %d. %s" % (
                              ready, total,
                              ("Осталось ответить на %d %s — они общие для нескольких "
                               "карточек." % (len(qs), S._plural(len(qs), "вопрос",
                                                                 "вопроса", "вопросов")))
                              if qs else ("Все карточки готовы." if ready == total
                                          else "Требуют внимания: %d." % need)))}
                if not _answers_saved:
                    result["ok"] = False
                    result["summary"] = ("Не удалось сохранить ваши ответы. "
                                         "Попробуйте ещё раз.")

            elif req.action == "republish_apply":
                ids = [str(x) for x in (p.get("item_ids") or []) if str(x).strip()]
                if not ids:
                    result = {"ok": False, "summary": "Не выбрано ни одного объявления"}
                elif len(ids) > MAX_CONFIRM_BATCH:
                    result = {"ok": False,
                              "summary": "За один раз можно снять не больше %d объявлений. "
                                         "Выберите меньше и повторите." % MAX_CONFIRM_BATCH}
                elif not req.confirm and not _module_approval_valid:
                    result = {"ok": False, "need_confirm": True, "item_ids": ids,
                              "autopilot_mode": "always_ask",
                              "module_approval_source": "autopilot_settings.mode=always_auto",
                              "summary": "BORIS подготовил точный список из %d %s. Для выполнения authoritative Avito-модуль требует включить режим «Доверить работу BORIS»; Scenario не просит второе подтверждение поверх этого module approval." % (
                                  len(ids), S._plural(len(ids), "объявления", "объявлений", "объявлений"))}
                else:
                    from app.api.avito import republish_apply, RepublishApplyRequest
                    r = republish_apply(RepublishApplyRequest(account_id=acc, item_ids=ids)) or {}
                    if r.get("status") == "needs_confirmation":
                        result = {"ok": False, "needs_mode": True, "tab": "settings",
                                  "summary": "Чтобы я снимал объявления сам, включите режим "
                                             "«Доверить работу BORIS» в разделе «Режим работы»"}
                    else:
                        n = r.get("removed_count") or len(ids)
                        result = {"ok": True, "raw": r,
                                  "summary": "Снял с публикации %d %s" % (
                                      n, S._plural(n, "объявление", "объявления", "объявлений")),
                                  "note": "Следующая выгрузка фида подготовит новое объявление "
                                          "этого направления. Если передумаете — объявление можно "
                                          "вернуть, для этого напишите нам."}
                        _say(acc, "scenario_republish",
                             "снял %d %s по вашему решению: %s" % (
                                 n, S._plural(n, "объявление", "объявления", "объявлений"),
                                 ", ".join(ids[:5]) + ("…" if len(ids) > 5 else "")),
                             actor="user")

            elif req.action == "auto_duplicate_run":
                from app.api.avito import auto_duplicate_run, AutoDuplicateRunRequest
                rq = AutoDuplicateRunRequest(
                    account_id=acc,
                    max_per_item=int(p.get("max_per_item", 2)),
                    daily_limit=int(p.get("daily_limit", 5)),
                    min_contacts=int(p.get("min_contacts", 1)),
                    dry_run=not req.confirm,
                    # SCENARIO_DUPLICATE_RUN_IDEMPOTENCY_V1: confirmed execution
                    # reuses the exact policy approval fingerprint as business intent.
                    idempotency_key=(str(_policy.get("approval_fingerprint") or _execution_fp) if req.confirm else ""))
                r = auto_duplicate_run(rq) or {}
                n = r.get("created") or r.get("planned") or 0
                result = {"ok": True, "raw": r, "dry_run": not req.confirm,
                          "summary": ("План: создам %s" % n) if not req.confirm
                                     else ("Создано дублей: %s" % n),
                          "reason": r.get("reason") or ""}
        except Exception as e:
            result = {"ok": False, "summary": "Не удалось выполнить: %s" % str(e)[:180]}

        # Canonical orchestration projection is additive: module result remains intact.
        result["capability_contract"] = SC.get_capability(req.action)
        result["orchestration_result"] = SC.project_result(req.action, result)
        if _policy.get("approval_fingerprint"):
            result["approval_fingerprint"] = _policy.get("approval_fingerprint")
        if _policy.get("module_approval_reused"):
            result["module_approval_reused"] = True
            result["module_approval_source"] = _capability.get("authoritative_approval_source")
        if (result.get("ok") and _capability.get("operation_class") != "READ_ONLY"
                and not result.get("owner_required") and not result.get("need_confirm")
                and not result.get("waiting_measurement") and not result.get("dry_run")):
            result["execution_fingerprint"] = _execution_fp
            result["execution_committed"] = True
        if _policy.get("preview_only"):
            _planned = int(((result.get("raw") or {}).get("planned") or 0)) if isinstance(result.get("raw"), dict) else 0
            _preview_requires_owner = bool(result.get("need_confirm") or int(result.get("pending") or 0) > 0
                                           or (result.get("dry_run") and _planned > 0))
            result["approval_required"] = _preview_requires_owner
            result["owner_required"] = _preview_requires_owner
            if _preview_requires_owner:
                result["orchestration_result"]["state"] = "OWNER_REQUIRED"

        # результат сохраняем — он должен пережить перезагрузку страницы
        state = _j(row.steps_state)
        prev = state.get(req.step_key) or {}
        prev["result"] = result
        prev["result_at"] = datetime.now().isoformat(timespec="seconds")
        if (result.get("ok") and req.action in ("set_republish_settings", "republish_apply", "marketing_prepare", "reactivation_prepare", "sales_prepare", "sitebuild_prepare", "social_prepare", "mop_prepare", "season_prepare", "campaign_safe_export") and not result.get("owner_required")) \
                or (result.get("ok") and req.action == "market_launch_prepare" and not result.get("owner_required")) \
                or (result.get("ok") and req.action == "auto_duplicate_run" and req.confirm) \
                or (result.get("ok") and req.action == "city_research" and result.get("task_status") == "done") \
                or (result.get("ok") and req.action == "scenario_measure" and not result.get("waiting_measurement")):
            prev["state"] = "done"
        state[req.step_key] = prev
        row.steps_state = json.dumps(state, ensure_ascii=False)
        # наблюдаемость: считаем действия и запоминаем, сколько объектов нашли
        _found = result.get("count") if isinstance(result.get("count"), int) else None
        _bump_metrics(row, actions_done=1, objects_found=_found)
        if result.get("ok") and result.get("summary"):
            _say(req.account_id, "scenario_step", result["summary"])
        row.updated_at = datetime.now()
        db.commit()
        db.refresh(row)

        f = S.collect_facts(req.account_id, db=db)
        f["_revive"] = S.revive_facts(req.account_id, db=db)
        f["_start"] = S.start_facts(req.account_id, db=db)
        return {"status": "ok", "result": result, "scenario": _scen_public(row, f)}
    finally:
        db.close()


# BUSINESS_SCENARIO_AUTONOMOUS_EXECUTION: autonomy comes from the sanctioned
# capability contract; keep no second action-name safety registry here.
def _autonomous_continue(scenario_id: int, account_id: str, user, max_steps: int = 5):
    """Advance only current AUTONOMOUS_SAFE steps using the existing scenario action contract.
    No new scheduler/engine. For city research we only poll an already-approved task_id.
    Stops on owner approval, external wait, measurement wait, failure, or non-safe action.
    """
    last = None
    for _ in range(max(1, min(int(max_steps or 1), 8))):
        db = SessionLocal()
        try:
            row = db.query(BusinessScenario).filter(BusinessScenario.id == scenario_id,
                                                    BusinessScenario.account_id == account_id).first()
            if not row or row.status != "active":
                break
            facts = S.collect_facts(account_id, db=db)
            if not facts.get("has_active_access"):
                break

            # LEGACY_SCENARIO_RESUME_V1: scenarios created before the unified
            # workflow contract may have an empty steps_state. Their first
            # context/diagnosis/plan steps have no executable action, so the
            # autonomous loop would stop forever at READY_TO_CONTINUE. Backfill
            # the exact same deterministic diagnosis snapshot used by start();
            # no external/module action is executed here.
            if row.scenario_type in ("more_leads", "new_niche", "new_city",
                                     "find_problem", "season", "social",
                                     "automate_sales", "website", "reactivation",
                                     "improve_sales"):
                state0 = _j(row.steps_state) or {}
                if not ((state0.get("diagnosis") or {}).get("result")):
                    state0["diagnosis"] = {"state":"done", "result": {"ok": True,
                        "summary": "Контекст аккаунта собран и план подготовлен",
                        "account_id": account_id,
                        "snapshot": {"active_items": facts.get("active_items"),
                                     "views": facts.get("views"),
                                     "contacts": facts.get("contacts"),
                                     "stats_date": facts.get("stats_date")}},
                        "result_at": datetime.now().isoformat(timespec="seconds")}
                    row.steps_state = json.dumps(state0, ensure_ascii=False)
                    row.updated_at = datetime.now()
                    db.commit(); db.refresh(row)

            progress = S.scenario_progress(row.scenario_type, facts, _j(row.steps_state))
            cur = next((x for x in (progress.get("steps") or []) if x.get("status") in ("current", "attention")), None)
            if not cur or not cur.get("action"):
                break
            action = str(cur.get("action"))
            existing_result = cur.get("result") if isinstance(cur.get("result"), dict) else {}
            from app import scenario_capabilities as SC
            cap = SC.get_capability(action)
            if not cap:
                break
            # Owner-approved long-running capabilities become safe to poll only
            # through their persisted resume reference; initial execution is never autonomous.
            if action == "city_research" and existing_result.get("task_id"):
                pass
            elif cap.get("autonomy_class") != "AUTONOMOUS_SAFE":
                break
            step_key = str(cur.get("key"))
        finally:
            db.close()
        try:
            resp = scenario_action(ActionRequest(account_id=account_id, scenario_id=scenario_id,
                                                step_key=step_key, action=action, payload={}, confirm=False), user=user)
            last = (resp or {}).get("result") or {}
        except Exception:
            break
        if not last.get("ok") or last.get("owner_required") or last.get("need_confirm") \
                or last.get("external_wait") or last.get("waiting_measurement"):
            break
    return last


# BUSINESS_SCENARIO_AUTO_CONTINUATION_V1
# Projection + periodic bridge over the existing BusinessScenario lifecycle.
# No second scheduler/state machine: persisted wait metadata lives in metrics,
# while steps_state remains the authoritative scenario progress store.
def _scenario_wait_projection(row, facts=None):
    db = None
    try:
        if facts is None:
            db = SessionLocal()
            facts = S.collect_facts(row.account_id, db=db)
        progress = S.scenario_progress(row.scenario_type, facts or {}, _j(row.steps_state))
        cur = next((x for x in (progress.get("steps") or []) if x.get("status") in ("current", "attention")), None)
        result = (cur or {}).get("result") if isinstance((cur or {}).get("result"), dict) else {}
        action = str((cur or {}).get("action") or "")
        now = datetime.now()
        if not (facts or {}).get("has_active_access"):
            state, reason, signal, owner = "BLOCKED", "Нужен активный тариф или 4-дневный тестовый период", "active entitlement", True
        elif row.status == "completed" or not cur:
            state, reason, signal, owner = "COMPLETED", "Сценарий завершён", None, False
        elif (cur or {}).get("owner_required") or result.get("owner_required") or result.get("need_confirm"):
            state, reason, signal, owner = "WAITING_OWNER", str(result.get("summary") or "Нужно решение владельца"), "authoritative owner/module approval", True
        elif action == "scenario_measure" and (result.get("waiting_measurement") or not result):
            state, reason, signal, owner = "WAITING_MEASUREMENT", str(result.get("summary") or "Ждём следующее независимое окно данных"), str(result.get("measurement_source") or "authoritative account analytics"), False
        elif not action:
            # A current step without an executable capability cannot truthfully be
            # READY_TO_CONTINUE. It either needs an owner-side route/input or waits
            # for an external/module fact to change. This closes dead states such as
            # Avito connection / photo preparation and owner campaign approval.
            needs_owner = bool((cur or {}).get("owner_required") or not (cur or {}).get("auto")
                               or (cur or {}).get("route") or (cur or {}).get("tab"))
            if needs_owner:
                state, owner = "WAITING_OWNER", True
                signal = "owner input / route completion"
            else:
                state, owner = "WAITING_MODULE", False
                signal = "authoritative module/account fact"
            reason = str((cur or {}).get("hint") or (cur or {}).get("title") or "Ждём условие продолжения")
        elif result.get("external_wait"):
            state, reason, signal, owner = "WAITING_EXTERNAL", str(result.get("summary") or "Ждём внешний результат"), "authoritative external task state", False
        elif action in ("marketing_prepare","reactivation_prepare","sales_prepare","sitebuild_prepare","social_prepare","mop_prepare","season_prepare") and not result:
            state, reason, signal, owner = "WAITING_MODULE", "Ждём состояние существующего модуля BORIS", action.replace("_prepare", ""), False
        elif result.get("ok") is False:
            state, reason, signal, owner = "FAILED_RECOVERABLE", str(result.get("summary") or "Шаг временно не выполнен"), "retry with backoff", False
        else:
            state, reason, signal, owner = "READY_TO_CONTINUE", "Условие продолжения выполнено", action or "current scenario step", False
        m = _j(row.metrics) if getattr(row, "metrics", None) else {}
        old = m.get("continuation") if isinstance(m.get("continuation"), dict) else {}
        since = old.get("waiting_since") if old.get("state") == state else now.isoformat(timespec="seconds")
        retry_count = int(old.get("retry_count") or 0)
        next_check = None
        if state in ("WAITING_OWNER","WAITING_EXTERNAL","WAITING_MEASUREMENT","WAITING_MODULE","FAILED_RECOVERABLE"):
            delay = min(3600, 300 * (2 ** min(retry_count, 3))) if state == "FAILED_RECOVERABLE" else 900
            next_check = (now + __import__('datetime').timedelta(seconds=delay)).isoformat(timespec="seconds")
        return {"state":state,"waiting_reason":reason,"waiting_since":since,"expected_signal":signal,
                "resume_condition":signal,"next_check_at":next_check,"owner_action_required":owner,
                "module_source": action.replace("_prepare","") if action.endswith("_prepare") else None,
                "measurement_window_end": result.get("measurement_window_end") or result.get("measurement_window"),
                "last_error": reason if state == "FAILED_RECOVERABLE" else old.get("last_error"),
                "retry_count": retry_count,
                "human_state":{"WORKING":"BORIS работает","READY_TO_CONTINUE":"BORIS продолжает","WAITING_OWNER":"Ждём от вас","WAITING_EXTERNAL":"Ждём внешний результат","WAITING_MEASUREMENT":"Измеряем","WAITING_MODULE":"Ждём результат модуля","COMPLETED":"Получен результат","FAILED_RECOVERABLE":"Повторим безопасно","BLOCKED":"Нужна помощь"}.get(state,state)}
    finally:
        if db is not None: db.close()


def run_business_scenario_continuations(max_rows: int = 100):
    """Periodic account-scoped continuation invoked by the existing BORIS Factory timer.
    It performs only capabilities whose sanctioned contract is AUTONOMOUS_SAFE,
    plus resume polling of an already owner-approved long-running task.
    A PostgreSQL advisory lock prevents duplicate resume across overlapping ticks.
    """
    from types import SimpleNamespace
    from sqlalchemy import text as _sql
    db = SessionLocal()
    stats = {"checked":0,"resumed":0,"waiting":0,"failed":0,"duplicate_resume":0}
    try:
        rows = db.execute(_sql("""
            SELECT s.id,s.account_id
              FROM business_scenarios s
             WHERE s.status='active'
               AND NOT EXISTS (
                   SELECT 1 FROM reliability_kill_switches r
                    WHERE r.account_id=s.account_id AND r.blocked=true
                      AND r.module IN ('actions','background')
               )
             ORDER BY s.id
             LIMIT :limit
        """), {"limit": max(1,min(int(max_rows or 1),500))}).fetchall()
        ids = [(int(r[0]), str(r[1])) for r in rows]
    finally:
        db.close()
    internal = SimpleNamespace(role="owner", id=0)
    for scenario_id, account_id in ids:
        lockdb = SessionLocal()
        locked = False
        try:
            locked = bool(lockdb.execute(_sql("select pg_try_advisory_lock(:k)"), {"k": 910000000 + scenario_id}).scalar())
            if not locked:
                stats["duplicate_resume"] += 1
                continue
            row = lockdb.query(BusinessScenario).filter(BusinessScenario.id==scenario_id, BusinessScenario.account_id==account_id, BusinessScenario.status=="active").first()
            if not row: continue
            stats["checked"] += 1
            before = _scenario_wait_projection(row, S.collect_facts(account_id, db=lockdb))
            m = _j(row.metrics) if row.metrics else {}
            oldc = m.get("continuation") if isinstance(m.get("continuation"), dict) else {}
            # FAILED_RECOVERABLE respects persisted exponential backoff.
            if before["state"] == "FAILED_RECOVERABLE" and oldc.get("next_check_at"):
                try:
                    if datetime.fromisoformat(str(oldc["next_check_at"])) > datetime.now():
                        stats["waiting"] += 1; continue
                except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=1502)
            if before["state"] in ("WAITING_OWNER", "BLOCKED"):
                m["continuation"] = before; row.metrics=json.dumps(m,ensure_ascii=False); row.updated_at=datetime.now(); lockdb.commit(); stats["waiting"] += 1; continue
            existing_loop = m.get("closed_loop") if isinstance(m.get("closed_loop"), dict) else {}
            if existing_loop.get("state") == "SAFE_OPTIONS_EXHAUSTED":
                before.update({"state":"WAITING_OWNER","human_state":"Ждём от вас","owner_action_required":True,
                               "waiting_reason":((existing_loop.get("owner_decision") or {}).get("decision") or "Безопасные варианты исчерпаны"),
                               "expected_signal":"owner decision","resume_condition":"owner decision"})
                m["continuation"] = before; row.metrics=json.dumps(m,ensure_ascii=False); row.updated_at=datetime.now(); lockdb.commit(); stats["waiting"] += 1; continue
            # Closed-loop transition is foreign-owned. This continuation preserves it,
            # but the selected action must still exist in the sanctioned capability contract.
            if _closed_loop_reconcile(row, S.collect_facts(account_id, db=lockdb)):
                lockdb.commit()
            else:
                lockdb.commit()
            last = _autonomous_continue(scenario_id, account_id, internal, max_steps=5)
            lockdb.expire_all()
            row = lockdb.query(BusinessScenario).filter(BusinessScenario.id==scenario_id, BusinessScenario.account_id==account_id).first()
            after = _scenario_wait_projection(row, S.collect_facts(account_id, db=lockdb)) if row else {"state":"COMPLETED"}
            m = _j(row.metrics) if row and row.metrics else {}
            prevc = m.get("continuation") if isinstance(m.get("continuation"), dict) else {}
            if after["state"] == "FAILED_RECOVERABLE":
                after["retry_count"] = int(prevc.get("retry_count") or 0) + 1
                after["last_attempt_at"] = datetime.now().isoformat(timespec="seconds")
                after["next_retry_at"] = (datetime.now() + __import__('datetime').timedelta(seconds=min(3600,300*(2**min(after["retry_count"],3))))).isoformat(timespec="seconds")
                after["next_check_at"] = after["next_retry_at"]; stats["failed"] += 1
            timeline = m.get("business_timeline") if isinstance(m.get("business_timeline"), list) else []
            if not timeline or timeline[-1].get("state") != after.get("state") or timeline[-1].get("reason") != after.get("waiting_reason"):
                timeline.append({"at":datetime.now().isoformat(timespec="seconds"),"state":after.get("state"),"label":after.get("human_state"),"reason":after.get("waiting_reason")})
                timeline = timeline[-30:]
            m["continuation"] = after; m["business_timeline"] = timeline
            if row:
                row.metrics=json.dumps(m,ensure_ascii=False); row.updated_at=datetime.now(); lockdb.commit()
            if last: stats["resumed"] += 1
            if after.get("state","").startswith("WAITING_"): stats["waiting"] += 1
        except Exception as exc:
            lockdb.rollback(); stats["failed"] += 1
            print("scenario continuation failed id=%s account=%s: %s" % (scenario_id, account_id, str(exc)[:200]))
        finally:
            if locked:
                try: lockdb.execute(_sql("select pg_advisory_unlock(:k)"), {"k":910000000+scenario_id}); lockdb.commit()
                except Exception: lockdb.rollback()
            lockdb.close()
    return stats


# BUSINESS_SCENARIO_CLOSED_LOOP_V1
# Goal/orchestration layer only. It consumes the existing scenario measurement result
# and persists bounded iterations in BusinessScenario.metrics; it does not duplicate
# AI Marketing/MOP/ROP/CRM/Feed Factory measurement or execution engines.
_CLOSED_LOOP_MAX_ITERATIONS = 3
_CLOSED_LOOP_MAX_NO_EFFECT = 2

def _closed_loop_action_catalog(scenario_type):
    # Only existing sanctioned scenario actions. Risky/external actions are owner-gated.
    return {
        "more_leads": [
            {"action":"marketing_prepare","module":"AI Marketing","hypothesis":"Объявления получают трафик, но обращений недостаточно; проверим существующий marketing-контур и conversion layer.","expected_effect":"Рост обращений без необоснованного увеличения трафика","parallel_class":"MEASUREMENT_CONFLICT","owner_required":False},
            {"action":"sales_prepare","module":"CRM/MOP/ROP","hypothesis":"Если трафик не превращается в обращения/целевые действия, следующий резерв может быть в обработке и воронке.","expected_effect":"Найти следующий измеримый резерв в sales funnel","parallel_class":"MEASUREMENT_CONFLICT","owner_required":False},
        ],
        "improve_sales": [
            {"action":"sales_prepare","module":"CRM/MOP/ROP","hypothesis":"Лиды есть, но часть воронки теряет клиентов; используем существующий sales-контур для следующего улучшения.","expected_effect":"Рост телефона, целевого действия или сделки","parallel_class":"MEASUREMENT_CONFLICT","owner_required":False},
            {"action":"reactivation_prepare","module":"Reactivation","hypothesis":"После улучшения текущей обработки резерв может оставаться в зависших старых лидах.","expected_effect":"Вернуть измеримые opportunities в активную воронку","parallel_class":"MEASUREMENT_CONFLICT","owner_required":False},
        ],
        "reactivation": [
            {"action":"reactivation_prepare","module":"Reactivation","hypothesis":"В CRM есть потерянные лиды, которые штатный Reactivation может вернуть в работу.","expected_effect":"Ответы, квалификация или recovered opportunities","parallel_class":"MEASUREMENT_CONFLICT","owner_required":False},
            {"action":"sales_prepare","module":"CRM/MOP/ROP","hypothesis":"Если возврат лидов не дал результата, проверим следующий bottleneck существующей sales-воронки.","expected_effect":"Рост следующего измеримого sales KPI","parallel_class":"MEASUREMENT_CONFLICT","owner_required":False},
        ],
        "automate_sales": [
            {"action":"mop_prepare","module":"MOP","hypothesis":"AI-менеджер может терять клиента на одном из этапов диалога; используем его существующий account-scoped контур.","expected_effect":"Рост квалификации/телефона/целевого действия","parallel_class":"MEASUREMENT_CONFLICT","owner_required":False},
            {"action":"sales_prepare","module":"CRM/MOP/ROP","hypothesis":"Если изменение MOP не доказало business effect, следующий анализ должен перейти в существующий ROP/CRM layer.","expected_effect":"Найти следующий sales bottleneck","parallel_class":"MEASUREMENT_CONFLICT","owner_required":False},
        ],
    }.get(scenario_type, [])


def _closed_loop_goal(row, baseline=None):
    inp=_j(row.input_data) or {}; st=row.scenario_type
    if st == "more_leads":
        target=inp.get("target_leads")
        return {"goal_type":"increase_leads","goal_description":"Получить больше заявок","target_metric":"contacts","target_direction":"increase","target_value":int(target) if str(target or '').isdigit() else None,"target_condition":"contacts increase in comparable authoritative window","measurement_source":"daily_stats","target_window":"next comparable statistics windows"}
    if st in ("improve_sales","automate_sales"):
        return {"goal_type":"improve_sales","goal_description":"Улучшить измеримую конверсию продаж","target_metric":"sales_funnel","target_direction":"increase","target_value":None,"target_condition":"phones/target_actions/deals improve","measurement_source":"existing CRM/MOP/ROP funnel","target_window":"next comparable sales windows"}
    if st == "reactivation":
        return {"goal_type":"reactivation","goal_description":"Вернуть потерянных клиентов в работу","target_metric":"sales_funnel","target_direction":"increase","target_value":None,"target_condition":"recovered opportunity advances in funnel","measurement_source":"existing CRM/Reactivation funnel","target_window":"next comparable sales windows"}
    return {"goal_type":st,"goal_description":"Достичь измеримого business outcome сценария","target_metric":None,"target_direction":None,"target_value":None,"target_condition":"authoritative module outcome","measurement_source":"authoritative module analytics","target_window":"module observation window"}


def _closed_loop_outcome(row, measurement):
    if not isinstance(measurement,dict) or measurement.get("waiting_measurement"):
        return "INSUFFICIENT_DATA", False
    d=measurement.get("delta") or {}; st=row.scenario_type
    if st == "more_leads":
        dc=int(d.get("contacts") or 0); dv=int(d.get("views") or 0)
        if dc > 0: return "GOAL_ACHIEVED", True
        if dc < 0: return "WORSE", False
        if dv > 0: return "NO_EFFECT", False
        return "INSUFFICIENT_DATA", False
    if st in ("improve_sales","automate_sales","reactivation"):
        gains=sum(max(0,int(d.get(k) or 0)) for k in ("phones","target_actions","deals"))
        losses=sum(min(0,int(d.get(k) or 0)) for k in ("phones","target_actions","deals"))
        if gains and not losses: return "PROGRESS_MADE", False
        if losses and not gains: return "WORSE", False
        if gains: return "PROGRESS_MADE", False
        return "NO_EFFECT", False
    return "INSUFFICIENT_DATA", False


def _closed_loop_reconcile(row, facts=None):
    """Consume a completed existing scenario_measure and decide the next bounded iteration.
    Must be called while the scenario advisory lock is held.
    """
    metrics=_j(row.metrics) if row.metrics else {}; loop=metrics.get("closed_loop") if isinstance(metrics.get("closed_loop"),dict) else {}
    state=_j(row.steps_state) or {}; mr=((state.get("measure") or {}).get("result") or {}) if isinstance(state,dict) else {}
    if not mr or mr.get("waiting_measurement"):
        return False
    marker=(state.get("measure") or {}).get("result_at")
    if marker and loop.get("last_measurement_at") == marker:
        return False
    baseline=mr.get("baseline") or {}; current=mr.get("current") or {}
    goal=loop.get("goal") or _closed_loop_goal(row,baseline)
    iterations=loop.get("iterations") if isinstance(loop.get("iterations"),list) else []
    outcome, achieved=_closed_loop_outcome(row,mr)
    # Close the currently measuring iteration, if one exists.
    if iterations and not iterations[-1].get("outcome"):
        iterations[-1]["metric_after"]=current; iterations[-1]["outcome"]=outcome; iterations[-1]["decision"]="STOP_SUCCESS" if achieved else "SELECT_NEXT_SAFE_ACTION"; iterations[-1]["measurement_window_end"]=marker or datetime.now().isoformat(timespec="seconds")
    no_effect=sum(1 for x in iterations if x.get("outcome") in ("NO_EFFECT","WORSE"))
    loop.update({"goal":goal,"baseline":loop.get("baseline") or baseline,"current_value":current,"goal_status":outcome,"goal_progress":outcome,"last_measurement_at":marker,"iterations":iterations,"technical_completion":True,"business_goal_achieved":bool(achieved)})
    if achieved:
        loop.update({"state":"GOAL_ACHIEVED","goal_achieved_at":datetime.now().isoformat(timespec="seconds"),"final_metric":current,"baseline_metric":loop.get("baseline") or baseline,"iterations_used":len(iterations),"winning_action":iterations[-1].get("selected_action") if iterations else None})
        row.status="completed"
    else:
        used={x.get("action_fingerprint") for x in iterations if x.get("outcome") in ("NO_EFFECT","WORSE")}
        choices=[]
        from app import scenario_capabilities as SC
        for c in _closed_loop_action_catalog(row.scenario_type):
            # Foreign optimizer selects only from actions sanctioned by this contract.
            # Selection/ranking remains entirely owned by BUSINESS_SCENARIO_CLOSED_LOOP_OPTIMIZATION.
            if not SC.get_capability(c.get("action")):
                continue
            fp="%s:%s"%(c.get("module"),c.get("action"))
            if fp not in used: choices.append((fp,c))
        exhausted=len(iterations)>=_CLOSED_LOOP_MAX_ITERATIONS or no_effect>=_CLOSED_LOOP_MAX_NO_EFFECT or not choices
        if exhausted:
            loop.update({"state":"SAFE_OPTIONS_EXHAUSTED","owner_decision":{"required":True,"decision":"BORIS исчерпал безопасные автоматические варианты. Выберите следующий бизнес-шаг или разрешите действие с business risk.","reason":"Цель ещё не достигнута после %s измеренных итераций."%len(iterations)}})
        else:
            fp,c=choices[0]; n=len(iterations)+1
            it={"iteration_number":n,"hypothesis":c["hypothesis"],"selected_action":c["action"],"selection_reason":"Следующий sanctioned action после outcome %s; failed-strategy fingerprints не повторяются."%outcome,"why_selected":"Предыдущий outcome: %s"%outcome,"source_evidence":mr.get("delta") or mr.get("business_outcome"),"source_module":c["module"],"previous_outcome":outcome,"expected_effect":c["expected_effect"],"module_source":c["module"],"baseline_before":current,"started_at":datetime.now().isoformat(timespec="seconds"),"measurement_window_start":None,"measurement_window_end":None,"metric_after":None,"outcome":None,"decision":"EXECUTE_SANCTIONED_ACTION","next_action":c["action"],"action_fingerprint":fp,"parallel_class":c["parallel_class"]}
            iterations.append(it); loop.update({"iterations":iterations,"state":"ITERATION_SELECTED","next_action":c["action"],"current_iteration":n})
            # Re-open existing execute+measure steps; no new workflow engine/step type.
            for key in ("execute","measure"):
                if key in state: state.pop(key,None)
            row.steps_state=json.dumps(state,ensure_ascii=False)
    metrics["closed_loop"]=loop
    tl=metrics.get("business_timeline") if isinstance(metrics.get("business_timeline"),list) else []
    tl.append({"at":datetime.now().isoformat(timespec="seconds"),"state":loop.get("state"),"label":"Цель достигнута" if achieved else ("Нужно ваше решение" if loop.get("state")=="SAFE_OPTIONS_EXHAUSTED" else "Следующая гипотеза"),"reason":((iterations[-1].get("hypothesis") if iterations and not achieved and loop.get("state")=="ITERATION_SELECTED" else mr.get("business_outcome")) or outcome)})
    metrics["business_timeline"]=tl[-30:]; row.metrics=json.dumps(metrics,ensure_ascii=False); row.updated_at=datetime.now()
    return True
