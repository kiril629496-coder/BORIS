"""
Борис-Советник по ставкам (CPX). Читает ставки + статистику + целевой CPL,
считает по формуле и складывает РЕКОМЕНДАЦИИ в Storage (ключ cpx_advice).
НИЧЕГО НЕ МЕНЯЕТ — только советует. Реальные действия — отдельный слой позже.
"""
from fastapi import APIRouter
import json, httpx
from datetime import date, timedelta

router = APIRouter(prefix="/api/cpx_advisor", tags=["cpx_advisor"])

# Порог значимости: не судим объявление, пока показов меньше этого числа
MIN_VIEWS_FOR_JUDGEMENT = 30      # суточный порог, оставлен для истории
# Недельное окно: суточные пороги писались под аккаунт с сотнями показов
# на объявление, а у живых клиентов это единицы показов В НЕДЕЛЮ.
MIN_VIEWS_FOR_JUDGEMENT_7D = 3   # меньше — судить не о чем
ARCHIVE_MIN_VIEWS_7D = 10        # столько показов без контактов — повод снизить
WINDOW_DAYS = 7                  # семь ПОЛНЫХ дней, сегодняшний неполный не берём
# Шаг изменения ставки (для будущих действий; сейчас только в тексте совета)
BID_STEP_PCT = 15


def _is_archived(db, account_id, item_id):
    """True если продвижение с объявления уже снято ботом (флаг идемпотентности)."""
    try:
        from app.models.storage import Storage as _St
        return db.query(_St).filter(_St.account_id == account_id, _St.key == f"cpx_archived:{item_id}").first() is not None
    except Exception:
        return False


def _load_json(db, account_id, key):
    from app.models.storage import Storage
    row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == key).first()
    return json.loads(row.value) if row else None


def _save_json(db, account_id, key, value):
    from app.models.storage import Storage
    row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == key).first()
    if row:
        row.value = json.dumps(value, ensure_ascii=False)
    else:
        db.add(Storage(account_id=account_id, key=key, value=json.dumps(value, ensure_ascii=False)))
    db.commit()


@router.get("/run")
def _aggregate_window(db, account_id: str, days: int = WINDOW_DAYS):
    """Суммы просмотров и контактов по объявлению за N ПОЛНЫХ прошлых дней.

    Сегодняшний день не берём: он неполный и занижает картину.
    Возвращает {item_id: {"views": n, "contacts": n, "days": сколько дней были данные}}.
    """
    out = {}
    for back in range(1, days + 1):
        day = (date.today() - timedelta(days=back)).isoformat()
        snap = _load_json(db, account_id, f"daily_stats:{day}")
        if not snap:
            continue
        for it in (snap.get("items") or []):
            iid = it.get("id")
            if iid is None:
                continue
            cur = out.setdefault(iid, {"views": 0, "contacts": 0, "days": 0})
            cur["views"] += int(it.get("views") or 0)
            cur["contacts"] += int(it.get("contacts") or 0)
            cur["days"] += 1
    return out


def run_advisor(account_id: str = "otdushi"):
    """Один прогон Советника: собрать данные, посчитать, сложить рекомендации.
    Вызывается по расписанию (раз в час) или вручную."""
    from app.db.session import SessionLocal
    from app.api.avito import get_avito_token
    db = SessionLocal()
    try:
        # 1) целевой CPL
        kpi = _load_json(db, account_id, "kpi_settings")
        if not kpi or not kpi.get("max_cost_per_lead_rub"):
            return {"status": "no_kpi",
                    "message": "Не задана красная цена лида (max_cost_per_lead_rub). Задайте цель — и Борис начнёт советовать."}
        max_cpl = kpi.get("max_cost_per_lead_rub")
        target_leads = kpi.get("target_leads_per_day", 0)
        daily_limit = kpi.get("daily_budget_limit_rub", 0)

        # 2) свежая статистика по объявлениям (daily_stats за сегодня)
        today = date.today().isoformat()
        stats = _load_json(db, account_id, f"daily_stats:{today}")
        items_stats = stats.get("items", []) if stats else []
        if not items_stats:
            return {"status": "no_stats",
                    "message": "Нет статистики за сегодня. Сначала отработает сбор статистики (collect_stats)."}

        # 3) текущие ставки по объявлениям (наш cpxpromo)
        tok_data = get_avito_token(account_id)
        tok = tok_data.get("access_token") if isinstance(tok_data, dict) else tok_data
        item_ids = [it["id"] for it in items_stats if it.get("status") == "active"][:200]
        bids_map = {}
        if tok and item_ids:
            try:
                r = httpx.post("https://api.avito.ru/cpxpromo/1/getPromotionsByItemIds",
                               headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                               json={"itemIDs": item_ids}, timeout=30)
                if r.status_code == 200:
                    for it in r.json().get("items", []):
                        mp = it.get("manualPromotion") or {}
                        bids_map[it.get("itemID")] = mp.get("bidPenny")
            except Exception as e:
                bids_map = {"_error": str(e)[:100]}

        # 4) оценка расхода за день (по балансу, если есть вчерашний срез)
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        y_stats = _load_json(db, account_id, f"daily_stats:{yesterday}")
        total_contacts = sum(it.get("contacts", 0) for it in items_stats)
        spent = None
        acct_cpl = None
        if stats and y_stats:
            spent = max(y_stats.get("balance", {}).get("real", 0) - stats.get("balance", {}).get("real", 0), 0)
            if total_contacts > 0 and spent > 0:
                acct_cpl = round(spent / total_contacts, 2)

        limit_pct = None
        block_raises = False
        limit_note = None
        if daily_limit and spent is not None:
            limit_pct = round(spent / daily_limit * 100)
            if limit_pct >= 100:
                block_raises = True
                limit_note = f"Суточный лимит исчерпан ({spent:.0f}/{daily_limit:.0f}р) — поднятия заблокированы."
            elif limit_pct >= 80:
                block_raises = True
                limit_note = f"Близко к суточному лимиту ({spent:.0f}/{daily_limit:.0f}р, {limit_pct}%) — поднятия заблокированы."
            else:
                limit_note = f"Расход {spent:.0f}/{daily_limit:.0f}р ({limit_pct}%) — в пределах лимита."

        # 5) формула: разбор по объявлениям
        window = _aggregate_window(db, account_id)
        raise_bids, lower_bids, archive, watching = [], [], [], []
        for it in items_stats:
            if it.get("status") != "active":
                continue
            iid = it["id"]
            views = it.get("views", 0)
            contacts = it.get("contacts", 0)
            bid = bids_map.get(iid)
            bid_rub = round(bid/100, 2) if isinstance(bid, int) else None
            w = window.get(iid) or {"views": 0, "contacts": 0, "days": 0}
            v7, c7, d7 = w["views"], w["contacts"], w["days"]
            conv7 = round(c7 / v7 * 100, 1) if v7 else None
            entry = {"id": iid, "title": it.get("title", "")[:50],
                     # суточные поля — legacy, на новом экране НЕ используются:
                     # они почти всегда нули и вводят в заблуждение
                     "views": views, "contacts": contacts,
                     "bid_rub": bid_rub,
                     "views_7d": v7, "contacts_7d": c7,
                     "conversion_7d": conv7, "days_with_data": d7}

            if c7 > 0:
                entry["reason_code"] = "has_contacts"
                entry["why"] = f"{c7} контакт(ов) на {v7} просмотров за {WINDOW_DAYS} дней — работает"
                if block_raises:
                    entry["reason_code"] = "limit"
                    entry["suggest"] = "поднятие заблокировано суточным лимитом"
                    watching.append(entry)
                else:
                    entry["suggest"] = f"можно поднять ставку на ~{BID_STEP_PCT}% для большего трафика"
                    raise_bids.append(entry)
            elif v7 < MIN_VIEWS_FOR_JUDGEMENT_7D:
                entry["reason_code"] = "no_data"
                entry["why"] = f"{v7} просмотров за {WINDOW_DAYS} дней — объявление почти не показывается"
                watching.append(entry)
            elif v7 < ARCHIVE_MIN_VIEWS_7D:
                entry["reason_code"] = "few_views"
                entry["why"] = f"{v7} просмотров, 0 обращений — пока рано делать вывод"
                watching.append(entry)
            elif _is_archived(db, account_id, iid):
                entry["reason_code"] = "already_off"
                entry["why"] = f"{v7} просмотров, 0 обращений — продвижение уже отключено"
                watching.append(entry)
            else:
                # Архив НЕ применяем автоматически — только рекомендация.
                entry["reason_code"] = "no_contacts"
                entry["why"] = f"{v7} просмотров за {WINDOW_DAYS} дней, 0 обращений — деньги уходят впустую"
                entry["suggest"] = "снизить ставку или переработать объявление"
                archive.append(entry)

        advice = {
            "generated_at": __import__("datetime").datetime.now().isoformat(),
            "account_id": account_id,
            "target": {"max_cpl_rub": max_cpl, "target_leads_per_day": target_leads},
            "fact": {"contacts_today": total_contacts, "spent_today_rub": spent,
                     "daily_limit_rub": daily_limit, "limit_pct": limit_pct, "limit_note": limit_note,
                     "account_cpl_rub": acct_cpl,
                     "data_note": None if acct_cpl else "CPL пока не посчитать — нужна история балансов за 2+ дня"},
            "recommendations": {
                "raise": raise_bids, "lower_or_archive": archive, "watching": watching,
            },
            "summary": f"Активных: {len([i for i in items_stats if i.get('status')=='active'])}. "
                       f"Работают (есть лиды): {len(raise_bids)}. "
                       f"Кандидаты на снижение/архив: {len(archive)}. "
                       f"Наблюдаю (мало данных): {len(watching)}.",
            "mode": "advisor_only_no_actions",
        }
        _save_json(db, account_id, "cpx_advice", advice)
        return {"status": "ok", "advice": advice}
    finally:
        db.close()


@router.get("/last")
def last_advice(account_id: str = "otdushi"):
    """Последние рекомендации Советника (для показа в интерфейсе)."""
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        a = _load_json(db, account_id, "cpx_advice")
        return {"status": "ok", "advice": a} if a else {"status": "empty", "message": "Советник ещё не отрабатывал"}
    finally:
        db.close()


# ============ ПРИМЕНЕНИЕ РЕКОМЕНДАЦИЙ (реальные действия с деньгами) ============
from pydantic import BaseModel

# Предохранители автобиддера
MAX_BID_STEP_PCT = 20        # не менять ставку больше чем на 20% за раз
DEFAULT_MIN_BID_RUB = 3      # минимальная ставка при снижении, ₽
ARCHIVE_MIN_VIEWS = 50       # архивировать только при >= стольких показов без контактов


class ApplyOneBody(BaseModel):
    account_id: str = "otdushi"
    item_id: int
    action: str              # "raise" | "lower" | "archive"


def _applied_key(item_id, action, generated_at):
    """Ключ применения. Включает версию рекомендации: после нового расчёта
    советника то же действие снова становится доступным, иначе однажда
    изменённое объявление заблокировалось бы навсегда."""
    return "%s:%s:%s" % (item_id, action, generated_at or "")


def _mark_applied(db, account_id, key):
    log = _load_json(db, account_id, "cpx_applied") or {}
    log[key] = __import__("datetime").datetime.now().isoformat()
    if len(log) > 500:                      # держим журнал компактным
        for old in sorted(log, key=lambda k: log[k])[:200]:
            log.pop(old, None)
    _save_json(db, account_id, "cpx_applied", log)


@router.post("/apply_one")
def apply_one(body: ApplyOneBody):
    """Применить ОДНУ рекомендацию вручную (по кнопке). Меняет реальную ставку/статус.
    Проходит через автопилот-проверку и логируется."""
    import httpx as _httpx
    from app.db.session import SessionLocal
    from app.api.avito import get_avito_token, _audit_log, _autopilot_allows

    db = SessionLocal()
    try:
        # KPI — берём цель и суточный лимит (предохранители)
        kpi = _load_json(db, body.account_id, "kpi_settings") or {}
        max_cpl = kpi.get("max_cost_per_lead_rub", 0)

        # Одно действие на объявление в рамках ОДНОЙ версии рекомендаций.
        advice_now = _load_json(db, body.account_id, "cpx_advice") or {}
        gen_at = advice_now.get("generated_at") or ""
        applied_log = _load_json(db, body.account_id, "cpx_applied") or {}
        akey = _applied_key(body.item_id, body.action, gen_at)
        if gen_at and akey in applied_log:
            return {"status": "blocked", "item_id": body.item_id,
                    "action": body.action,
                    "applied_at": applied_log[akey],
                    "message": "Это действие уже применено к текущей рекомендации. "
                               "Повтор станет доступен после следующего расчёта советника."}

        tok_data = get_avito_token(body.account_id)
        tok = tok_data.get("access_token") if isinstance(tok_data, dict) else tok_data
        if not tok:
            return {"status": "error", "message": "Нет токена Avito"}

        # текущая ставка по объявлению
        r = _httpx.get(f"https://api.avito.ru/cpxpromo/1/getBids/{body.item_id}",
                       headers={"Authorization": f"Bearer {tok}"}, timeout=20)
        if r.status_code != 200:
            return {"status": "error", "code": r.status_code, "message": r.text[:200]}
        try:
            info = r.json()
        except Exception:
            info = {}
        action_type = info.get("actionTypeID", 5)
        cur_bid = (info.get("manual") or {}).get("bidPenny") or 0
        rec_bid = (info.get("manual") or {}).get("recBidPenny") or 0
        max_bid = (info.get("manual") or {}).get("maxBidPenny") or 0
        min_bid = (info.get("manual") or {}).get("minBidPenny") or DEFAULT_MIN_BID_RUB * 100

        if body.action == "archive":
            # снять продвижение (в архив продвижения — вернуть на прайс-лист)
            rr = _httpx.post("https://api.avito.ru/cpxpromo/1/remove",
                             headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                             json={"itemID": body.item_id}, timeout=20)
            if rr.status_code != 200:
                return {"status": "error", "code": rr.status_code, "message": rr.text[:200]}
            _audit_log(body.account_id, "cpx_apply_archive",
                       f"объявление {body.item_id}: снято продвижение (0 контактов)", "boris")
            # флаг идемпотентности: помечаем снятое, чтобы не снимать по кругу
            try:
                from app.db.session import SessionLocal as _SL
                from app.models.storage import Storage as _St
                import json as _j2, time as _t2
                _db2 = _SL()
                _k = f"cpx_archived:{body.item_id}"
                _row = _db2.query(_St).filter(_St.account_id == body.account_id, _St.key == _k).first()
                _val = _j2.dumps({"at": _t2.strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False)
                if _row:
                    _row.value = _val
                else:
                    _db2.add(_St(account_id=body.account_id, key=_k, value=_val))
                _db2.commit(); _db2.close()
            except Exception as _e2:
                print("[cpx] archive flag:", _e2)
            _mark_applied(db, body.account_id, akey)
            return {"status": "ok", "action": "archive", "item_id": body.item_id}

        # raise / lower — считаем новую ставку с шагом и потолками
        step = int(cur_bid * MAX_BID_STEP_PCT / 100) or 100  # минимум 1₽ шаг
        if body.action == "raise":
            new_bid = cur_bid + step
            if rec_bid and new_bid > rec_bid:
                new_bid = rec_bid          # не выше рекомендованной
            if max_bid and new_bid > max_bid:
                new_bid = max_bid          # жёсткий потолок
        elif body.action == "lower":
            new_bid = cur_bid - step
            if new_bid < min_bid:
                new_bid = min_bid
        else:
            return {"status": "error", "message": f"неизвестное действие {body.action}"}

        # Avito принимает только суммы, кратные рублю: 154,4 ₽ отвергается

        # с ошибкой «Сумма в строке bidPenny должна быть кратна рублю».

        new_bid = (int(new_bid) // 100) * 100

        if min_bid and new_bid < min_bid:

            new_bid = (int(min_bid) // 100) * 100 or 100


        payload = {"actionTypeID": action_type, "bidPenny": int(new_bid), "itemID": body.item_id}
        wr = _httpx.post("https://api.avito.ru/cpxpromo/1/setManual",
                         headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                         json=payload, timeout=20)
        if wr.status_code != 200:
            return {"status": "error", "code": wr.status_code, "message": wr.text[:200]}
        _audit_log(body.account_id, "cpx_apply_bid",
                   f"объявление {body.item_id}: ставка {cur_bid/100:.0f}→{new_bid/100:.0f}₽ ({body.action})", "user")
        _mark_applied(db, body.account_id, akey)
        return {"status": "ok", "action": body.action, "item_id": body.item_id,
                "old_bid_rub": cur_bid/100, "new_bid_rub": new_bid/100}
    finally:
        db.close()
