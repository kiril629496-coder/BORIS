"""
Модуль управления ставками CPX-продвижения (оплата за просмотры).
API Avito: раздел "Настройка цены целевого действия" (cpxpromo).
Ставки и бюджеты — ВЕЗДЕ в копейках (bidPenny, valuePenny). В рубли делим на 100.
"""
from fastapi import APIRouter
from pydantic import BaseModel
import httpx
from app.api.avito import get_avito_token

router = APIRouter(prefix="/api/cpxpromo", tags=["cpxpromo"])

BASE = "https://api.avito.ru/cpxpromo/1"


def _token(account_id: str) -> str:
    """Достаём строку access_token из get_avito_token (учитываем оба формата)."""
    t = get_avito_token(account_id)
    if isinstance(t, dict):
        return t.get("access_token") or t.get("token") or ""
    return t or ""


class ItemIDsBody(BaseModel):
    account_id: str = "otdushi"
    item_ids: list[int]


@router.get("/bids/{item_id}")
def get_bids(item_id: int, account_id: str = "otdushi"):
    """Детальная инфо по одному объявлению: действующая ставка, прогнозы бюджет→просмотры.
    GET /cpxpromo/1/getBids/{itemId} (лимит 20 RPM)."""
    tok = _token(account_id)
    if not tok:
        return {"status": "error", "message": "Не удалось авторизоваться в Avito API"}
    r = httpx.get(f"{BASE}/getBids/{item_id}",
                  headers={"Authorization": f"Bearer {tok}"}, timeout=20)
    if r.status_code != 200:
        return {"status": "error", "code": r.status_code, "message": r.text[:300]}
    return {"status": "ok", "data": r.json()}


@router.post("/promotions")
def get_promotions(body: ItemIDsBody):
    """Текущие ставки/бюджеты пачкой (до 200 объявлений) — основной метод для таблицы.
    POST /cpxpromo/1/getPromotionsByItemIds (лимит 400 RPM)."""
    tok = _token(body.account_id)
    if not tok:
        return {"status": "error", "message": "Не удалось авторизоваться в Avito API"}
    r = httpx.post(f"{BASE}/getPromotionsByItemIds",
                   headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                   json={"itemIDs": body.item_ids[:200]}, timeout=30)
    if r.status_code != 200:
        return {"status": "error", "code": r.status_code, "message": r.text[:300]}
    return {"status": "ok", "data": r.json()}


@router.post("/remove")
def remove_promotion(body: ItemIDsBody):
    """Остановить продвижение, переключить на цены из прайс-листа.
    POST /cpxpromo/1/remove (лимит 300 RPM). МЕНЯЕТ реальное состояние — логируем."""
    from app.api.avito import _audit_log
    tok = _token(body.account_id)
    if not tok:
        return {"status": "error", "message": "Не удалось авторизоваться в Avito API"}
    results = []
    for iid in body.item_ids[:200]:
        r = httpx.post(f"{BASE}/remove",
                       headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                       json={"itemID": iid}, timeout=30)
        results.append({"itemID": iid, "code": r.status_code})
    _audit_log(body.account_id, "cpxpromo_remove", f"остановлено продвижение: {body.item_ids}", "user")
    return {"status": "ok", "results": results}



class SetManualBody(BaseModel):
    account_id: str = "otdushi"
    item_id: int
    action_type_id: int = 5          # 1-звонок | 5-пакет кликов | 7-мессенджер
    bid_penny: int                   # цена целевого действия в копейках (700 = 7р)
    limit_penny: int | None = None   # дневной лимит трат в копейках (None = без лимита)


class SetAutoBody(BaseModel):
    account_id: str = "otdushi"
    item_id: int
    action_type_id: int = 5
    budget_penny: int
    budget_type: str = "1d"          # "1d" сутки | "7d" неделя | "30d" месяц


@router.post("/set_manual")
def set_manual(body: SetManualBody):
    """Ручная ставка + дневной лимит. POST /cpxpromo/1/setManual (scope cpxpromo:edit).
    МЕНЯЕТ реальные расходы клиента — обязательный аудит."""
    from app.api.avito import _audit_log
    tok = _token(body.account_id)
    if not tok:
        return {"status": "error", "message": "Не удалось авторизоваться в Avito API"}
    payload = {"actionTypeID": body.action_type_id, "bidPenny": body.bid_penny, "itemID": body.item_id}
    if body.limit_penny is not None:
        payload["limitPenny"] = body.limit_penny
    r = httpx.post(f"{BASE}/setManual",
                   headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                   json=payload, timeout=30)
    if r.status_code != 200:
        return {"status": "error", "code": r.status_code, "message": r.text[:300]}
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text[:200] or "ok (пустой ответ)"}
    lim = f", лимит/день {body.limit_penny/100:.0f}р" if body.limit_penny else ""
    _audit_log(body.account_id, "cpxpromo_set_manual",
               f"объявление {body.item_id}: ставка {body.bid_penny/100:.0f}р{lim}", "user")
    return {"status": "ok", "data": data}


@router.post("/set_auto")
def set_auto(body: SetAutoBody):
    """Авто-настройка (Avito сам крутит ставку под бюджет). POST /cpxpromo/1/setAuto.
    ВНИМАНИЕ: недоступно в категории «Транспорт». МЕНЯЕТ расходы — аудит."""
    from app.api.avito import _audit_log
    tok = _token(body.account_id)
    if not tok:
        return {"status": "error", "message": "Не удалось авторизоваться в Avito API"}
    payload = {"actionTypeID": body.action_type_id, "budgetPenny": body.budget_penny,
               "budgetType": body.budget_type, "itemID": body.item_id}
    r = httpx.post(f"{BASE}/setAuto",
                   headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                   json=payload, timeout=30)
    if r.status_code != 200:
        return {"status": "error", "code": r.status_code, "message": r.text[:300]}
    try:
        data2 = r.json()
    except Exception:
        data2 = {"raw": r.text[:200] or "ok (пустой ответ)"}
    _audit_log(body.account_id, "cpxpromo_set_auto",
               f"объявление {body.item_id}: авто-бюджет {body.budget_penny/100:.0f}р/{body.budget_type}", "user")
    return {"status": "ok", "data": data2}
