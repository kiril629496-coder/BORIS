# -*- coding: utf-8 -*-
"""Роутер нового рабочего стола БОРИСа (/dashboard/home и сценарии).

ТОЛЬКО ЧТЕНИЕ существующих данных + CRUD одной таблицы business_scenarios.
Ничего не публикует, денег не тратит, чужие модули не меняет.
Авторизация и изоляция аккаунтов навешены централизованно в main.py.
account_id обязателен везде - дефолтов быть не должно (см. этап 0).
"""

import json
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any

from app.db.session import SessionLocal
from app.models.business_scenario import BusinessScenario
from app import scenarios as S

router = APIRouter(prefix="/api/home", tags=["home"])


def _scen_public(row, facts):
    if row.scenario_type == "revive" and "_revive" not in facts:
        try:
            facts = dict(facts)
            facts["_revive"] = S.revive_facts(row.account_id)
        except Exception:
            pass
    if row.scenario_type == "avito_start" and "_start" not in facts:
        try:
            facts = dict(facts)
            facts["_start"] = S.start_facts(row.account_id)
        except Exception:
            pass
    data = S.scenario_progress(row.scenario_type, facts, _j(row.steps_state))
    meta = S.CATALOG.get(row.scenario_type, {})
    return {
        "id": row.id,
        "scenario_type": row.scenario_type,
        "title": meta.get("title") or row.scenario_type,
        "lead": meta.get("lead") or "",
        "status": row.status,
        "input_data": _j(row.input_data),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "progress": data,
        "metrics": _j(getattr(row, "metrics", None)) or {},
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
@router.get("/overview")
def overview(account_id: str):
    """Всё для рабочего стола одним запросом."""
    db = SessionLocal()
    try:
        f = S.collect_facts(account_id, db=db)
        row = _active(db, account_id)
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
            "journal": S.journal(account_id, db=db, limit=10),
            "recommendations": S.recommendations(f, db=db),
            "active_scenario": _scen_public(row, f) if row else None,
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


# --------------------------------------------------------------- каталог

@router.get("/scenarios")
def list_scenarios(account_id: str):
    db = SessionLocal()
    try:
        f = S.collect_facts(account_id, db=db)
        rows = db.query(BusinessScenario).filter(
            BusinessScenario.account_id == account_id).order_by(
            BusinessScenario.updated_at.desc()).all()
        by_type = {}
        for r in rows:
            by_type.setdefault(r.scenario_type, r)
        available = []
        for stype, meta in S.CATALOG.items():
            existing = by_type.get(stype)
            available.append({
                "scenario_type": stype,
                "title": meta["title"],
                "lead": meta["lead"],
                "modules": meta["modules"],
                "plan": meta.get("plan") or [],
                "fields": meta["fields"],
                "available": True,
                "existing_id": existing.id if existing and existing.status == "active" else None,
            })
        soon = [{"scenario_type": k, "title": t, "available": False} for k, t in S.SOON]
        return {"status": "ok", "available": available, "soon": soon,
                "mine": [_scen_public(r, f) for r in rows if r.status == "active"]}
    finally:
        db.close()


class StartRequest(BaseModel):
    account_id: str
    scenario_type: str
    input_data: Optional[Dict[str, Any]] = None


@router.post("/scenario/start")
def start(req: StartRequest):
    if req.scenario_type not in S.CATALOG:
        raise HTTPException(400, "Неизвестный сценарий")
    db = SessionLocal()
    try:
        exist = db.query(BusinessScenario).filter(
            BusinessScenario.account_id == req.account_id,
            BusinessScenario.scenario_type == req.scenario_type,
            BusinessScenario.status == "active").first()
        if exist:
            exist.input_data = json.dumps(req.input_data or {}, ensure_ascii=False)
            exist.updated_at = datetime.now()
            db.commit()
            f = S.collect_facts(req.account_id, db=db)
            return {"status": "ok", "reused": True, "scenario": _scen_public(exist, f)}
        row = BusinessScenario(
            account_id=req.account_id,
            scenario_type=req.scenario_type,
            input_data=json.dumps(req.input_data or {}, ensure_ascii=False),
            steps_state="{}", status="active")
        db.add(row)
        db.commit()
        db.refresh(row)
        _bump_metrics(row, scenario_type=req.scenario_type)
        db.commit()
        _say(req.account_id, "scenario_start",
             "начал работу по задаче «%s»" % (S.CATALOG.get(req.scenario_type, {}).get("title") or req.scenario_type))
        f = S.collect_facts(req.account_id, db=db)
        return {"status": "ok", "reused": False, "scenario": _scen_public(row, f)}
    finally:
        db.close()


@router.get("/scenario/{scenario_id}")
def get_scenario(scenario_id: int, account_id: str):
    db = SessionLocal()
    try:
        row = db.query(BusinessScenario).filter(
            BusinessScenario.id == scenario_id,
            BusinessScenario.account_id == account_id).first()
        if not row:
            raise HTTPException(404, "Сценарий не найден")
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
def mark_step(req: StepRequest):
    if req.action not in ("done", "skip", "later", "reset"):
        raise HTTPException(400, "Неизвестное действие")
    db = SessionLocal()
    try:
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
def cancel(req: ScenarioIdRequest):
    return _set_status(req, "cancelled")


@router.post("/scenario/complete")
def complete(req: ScenarioIdRequest):
    return _set_status(req, "completed")


def _set_status(req, status):
    db = SessionLocal()
    try:
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
    except Exception:
        pass


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
            "seller_defaults", "card_fields")

MAX_REWRITE_BATCH = 10   # уникализация — платные вызовы модели, лимит обязателен
DEFAULT_PARSE_LIMIT = 20  # 0 в парсере означает «весь каталог» — для сценария это слишком


@router.post("/scenario/action")
def scenario_action(req: ActionRequest):
    if req.action not in _ALLOWED:
        raise HTTPException(400, "Действие не разрешено")
    db = SessionLocal()
    try:
        row = db.query(BusinessScenario).filter(
            BusinessScenario.id == req.scenario_id,
            BusinessScenario.account_id == req.account_id).first()
        if not row:
            raise HTTPException(404, "Сценарий не найден")

        p = req.payload or {}
        acc = req.account_id
        result = {}
        try:
            if req.action == "republish_check":
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
                    result = {"ok": False, "summary": "Не указана ссылка на сайт"}
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
                drafts = _load_drafts(acc) or []
                todo = [d for d in drafts
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
                elif not req.confirm:
                    # Условие 3 стандарта: если автопилот разрешает автоматическое
                    # выполнение, клиент обязан знать — подтверждение сработает сразу.
                    _ap = (S._load(db, acc, "autopilot_settings", {}) or {}).get("mode", "always_ask")
                    result = {"ok": False, "need_confirm": True, "item_ids": ids,
                              "autopilot_mode": _ap,
                              "autopilot_warning": (
                                  "У вас включён режим «Доверить работу BORIS» — "
                                  "объявления будут сняты сразу после подтверждения, без второго вопроса."
                                  if _ap == "always_auto" else ""),
                              "summary": "Подтвердите снятие %d %s" % (
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
                    dry_run=not req.confirm)
                r = auto_duplicate_run(rq) or {}
                n = r.get("created") or r.get("planned") or 0
                result = {"ok": True, "raw": r, "dry_run": not req.confirm,
                          "summary": ("План: создам %s" % n) if not req.confirm
                                     else ("Создано дублей: %s" % n),
                          "reason": r.get("reason") or ""}
        except Exception as e:
            result = {"ok": False, "summary": "Не удалось выполнить: %s" % str(e)[:180]}

        # результат сохраняем — он должен пережить перезагрузку страницы
        state = _j(row.steps_state)
        prev = state.get(req.step_key) or {}
        prev["result"] = result
        prev["result_at"] = datetime.now().isoformat(timespec="seconds")
        if result.get("ok") and req.action in ("set_republish_settings", "republish_apply") \
                or (result.get("ok") and req.action == "auto_duplicate_run" and req.confirm):
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
