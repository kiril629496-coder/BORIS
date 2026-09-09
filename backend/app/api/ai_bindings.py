"""Привязка ИИ-сотрудников к аккаунтам клиента.

МОП и РОП обслуживают один аккаунт, несколько выбранных или все.
Если память общая — аккаунты попадают в одну группу memory_scopes,
и знание, полученное в одном кабинете, доступно во втором.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text, bindparam

from app.db.session import SessionLocal
from app.models.account import Account

try:
    from app.api.auth import get_current_user
except ImportError:
    from app.auth import get_current_user

router = APIRouter(prefix="/api/ai", tags=["ai"])

PRODUCTS = {"mop": "ИИ-менеджер по продажам", "rop": "ИИ-руководитель отдела продаж"}
MODES = ("one", "selected", "all")


def _visible_accounts(db, user):
    q = db.query(Account)
    if getattr(user, "role", "") != "owner":
        # Сотрудники клиента видят аккаунты, выданные им через связь:
        # без этого раздел «AI-сотрудники» у них пустой.
        access_ids = [r[0] for r in db.execute(text(
            "SELECT account_id FROM user_account_access"
            " WHERE user_id = :u AND can_view = TRUE"),
            {"u": getattr(user, "id", 0)}).fetchall()]
        q = q.filter((Account.owner_user_id == user.id) |
                     (Account.account_id == getattr(user, "account_id", None)) |
                     (Account.account_id.in_(access_ids)))
    accounts = q.order_by(Account.created_at.asc()).all()
    visible = []
    for a in accounts:
        # LINKED_AUXILIARY_ACCOUNT_HIDE_V1:
        # Technical inbox_* accounts exist only to preserve legacy transport/history.
        # They must not appear as a second client AI workspace, otherwise an owner can
        # accidentally attach another MOP/ROP to the same real business and create
        # duplicate replies or split reporting.
        auxiliary = db.execute(text("""
            SELECT 1 FROM storage
             WHERE account_id=:a
               AND key='mop_crm_sales_settings'
               AND value ILIKE '%linked auxiliary account%'
             LIMIT 1
        """), {"a": a.account_id}).first()
        if auxiliary:
            continue
        visible.append(a)
    return visible


def _display_account_name(account):
    name = str(getattr(account, "name", "") or "").strip()
    return name if name and name != "\\" else str(getattr(account, "account_id", "") or "")


def _binding(db, product):
    return db.execute(text(
        "SELECT account_id, group_key, memory_shared FROM ai_bindings"
        " WHERE product=:p ORDER BY account_id"), {"p": product}).all()


def _is_owner(user):
    return getattr(user, "role", "") == "owner"


def license_of(db, user_id, product):
    """Сколько ИИ оплачено клиенту и до какого числа."""
    from datetime import datetime, timezone
    r = db.execute(text(
        "SELECT qty, paid_until FROM ai_licenses WHERE user_id=:u AND product=:p"),
        {"u": user_id, "p": product}).first()
    if not r:
        return {"qty": 0, "paid_until": None, "active": False}
    until = r[1]
    active = True
    if until is not None:
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        active = until >= datetime.now(timezone.utc)
    return {"qty": int(r[0] or 0), "paid_until": until,
            "active": active and int(r[0] or 0) > 0}


class LicenseBody(BaseModel):
    user_id: int
    product: str
    qty: int = 1
    days: int = 30
    comment: str = ""


@router.get("/licenses")
def licenses(user=Depends(get_current_user)):
    """Что оплачено этому клиенту. Владелец не ограничен."""
    db = SessionLocal()
    try:
        out = {}
        for pr in PRODUCTS:
            lic = license_of(db, getattr(user, "id", 0), pr)
            out[pr] = {
                "qty": lic["qty"],
                "active": lic["active"],
                "paid_until": lic["paid_until"].strftime("%d.%m.%Y") if lic["paid_until"] else "",
                "unlimited": _is_owner(user),
            }
        return {"status": "ok", "owner": _is_owner(user), "licenses": out}
    finally:
        db.close()


@router.post("/licenses")
def set_license(body: LicenseBody, user=Depends(get_current_user)):
    """Выдать клиенту оплаченные лицензии. Только владелец."""
    if not _is_owner(user):
        raise HTTPException(status_code=403, detail="Только владелец")
    if body.product not in PRODUCTS:
        raise HTTPException(status_code=400, detail="Неизвестный продукт")
    from datetime import datetime, timedelta, timezone
    until = datetime.now(timezone.utc) + timedelta(days=max(body.days, 1))
    db = SessionLocal()
    try:
        db.execute(text(
            "INSERT INTO ai_licenses (user_id, product, qty, paid_until, comment)"
            " VALUES (:u,:p,:q,:t,:c)"
            " ON CONFLICT (user_id, product) DO UPDATE SET qty=:q, paid_until=:t,"
            " comment=:c, updated_at=now()"),
            {"u": body.user_id, "p": body.product, "q": max(body.qty, 0),
             "t": until, "c": body.comment[:250]})
        db.commit()
        return {"status": "ok", "user_id": body.user_id, "product": body.product,
                "qty": body.qty, "paid_until": until.strftime("%d.%m.%Y"),
                "message": "Выдано %d шт. %s до %s" % (
                    body.qty, PRODUCTS[body.product], until.strftime("%d.%m.%Y"))}
    finally:
        db.close()


class BindBody(BaseModel):
    product: str
    mode: str = "one"
    account_ids: list = []
    memory_shared: bool = True
    group_key: str = ""


@router.get("/accounts")
def accounts(product: str = "mop", user=Depends(get_current_user)):
    """Список аккаунтов клиента и текущая привязка ИИ."""
    if product not in PRODUCTS:
        raise HTTPException(status_code=400, detail="Неизвестный продукт")
    db = SessionLocal()
    try:
        accs = _visible_accounts(db, user)
        bound = {r[0]: r for r in _binding(db, product)}
        modes = {r[0]: r[1] for r in db.execute(text(
            "SELECT account_id, mode FROM memory_modes")).all()}
        out = []
        for a in accs:
            b = bound.get(a.account_id)
            out.append({
                "account_id": a.account_id,
                "name": _display_account_name(a),
                "avito_user_id": a.avito_user_id or "",
                "bound": bool(b),
                "memory_shared": bool(b[2]) if b else False,
                "group_key": (b[1] if b else ""),
                "memory_mode": modes.get(a.account_id, "off"),
            })
        return {"status": "ok", "product": product,
                "product_title": PRODUCTS[product], "accounts": out,
                "bound_count": sum(1 for x in out if x["bound"])}
    finally:
        db.close()


@router.post("/bind")
def bind(body: BindBody, user=Depends(get_current_user)):
    """Подключить ИИ к аккаунтам. Режимы: один / выбранные / все."""
    if body.product not in PRODUCTS:
        raise HTTPException(status_code=400, detail="Неизвестный продукт")
    if body.mode not in MODES:
        raise HTTPException(status_code=400, detail="Режим: one, selected или all")
    # Владелец не ограничен: он решает, кому и сколько активировать.
    # Клиент подключает строго в пределах оплаченного (проверка ниже).
    db = SessionLocal()
    try:
        visible = {a.account_id for a in _visible_accounts(db, user)}
        if body.mode == "all":
            chosen = sorted(visible)
        else:
            chosen = [a for a in (body.account_ids or []) if a in visible]
            if body.mode == "one":
                chosen = chosen[:1]
        if not chosen:
            raise HTTPException(status_code=400, detail="Не выбрано ни одного аккаунта")

        if not _is_owner(user):
            lic = license_of(db, getattr(user, "id", 0), body.product)
            if not lic["active"]:
                raise HTTPException(
                    status_code=402,
                    detail="%s не оплачен. Обратитесь к менеджеру BORIS."
                           % PRODUCTS[body.product])
            if len(chosen) > lic["qty"]:
                raise HTTPException(
                    status_code=402,
                    detail="Оплачено %d шт. %s, выбрано %d аккаунтов."
                           % (lic["qty"], PRODUCTS[body.product], len(chosen)))

        group = (body.group_key or ("%s:%s" % (body.product, chosen[0])))[:64]

        db.execute(text("DELETE FROM ai_bindings WHERE product=:p AND account_id IN :accs")
                   .bindparams(bindparam("accs", expanding=True)),
                   {"p": body.product, "accs": sorted(visible)})
        for a in chosen:
            db.execute(text(
                "INSERT INTO ai_bindings (product, account_id, group_key, memory_shared)"
                " VALUES (:p,:a,:g,:s)"),
                {"p": body.product, "a": a, "g": group, "s": bool(body.memory_shared)})

        if body.memory_shared and len(chosen) > 1:
            for a in chosen:
                db.execute(text(
                    "INSERT INTO memory_scopes (scope_key, account_id) VALUES (:k,:a)"
                    " ON CONFLICT (account_id) DO UPDATE SET scope_key=:k"),
                    {"k": group, "a": a})
        else:
            db.execute(text("DELETE FROM memory_scopes WHERE account_id IN :accs")
                       .bindparams(bindparam("accs", expanding=True)),
                       {"accs": chosen})
        db.commit()

        history_learning = []
        if body.product == "mop":
            # MOP activation immediately studies the existing Avito dialog history
            # through the already-authoritative client_memory_runner. No second
            # memory engine/store is created; extraction is idempotent by fact_hash.
            try:
                from client_memory_runner import extract_dialogs, mark_conflicts
                for account_id in chosen:
                    try:
                        dialogs, pairs, facts = extract_dialogs(db, account_id)
                        conflicts = mark_conflicts(db, account_id)
                        history_learning.append({"account_id": account_id, "status": "ok", "dialogs": int(dialogs), "pairs": int(pairs), "facts_from_dialogs": int(facts), "conflicts": int(conflicts)})
                    except Exception as exc:
                        db.rollback()
                        history_learning.append({"account_id": account_id, "status": "error", "error": str(exc)[:180]})
            except Exception as exc:
                history_learning.append({"status": "error", "error": str(exc)[:180]})

        return {"status": "ok", "product": body.product, "accounts": chosen,
                "group_key": group, "memory_shared": bool(body.memory_shared),
                "history_learning": history_learning,
                "message": "%s подключён к %d аккаунт(ам)%s" % (
                    PRODUCTS[body.product], len(chosen),
                    ", память общая" if body.memory_shared and len(chosen) > 1
                    else ", память раздельная")}
    finally:
        db.close()


@router.get("/summary")
def summary(product: str = "rop", user=Depends(get_current_user)):
    """Карточка администратора: с какими аккаунтами работает и что знает."""
    if product not in PRODUCTS:
        raise HTTPException(status_code=400, detail="Неизвестный продукт")
    db = SessionLocal()
    try:
        visible = {a.account_id for a in _visible_accounts(db, user)}
        rows = [r for r in _binding(db, product) if r[0] in visible]
        accs = [r[0] for r in rows]
        if not accs:
            return {"status": "ok", "product": product, "accounts": [],
                    "facts": 0, "confirmed": 0, "conflicts": 0, "usable": 0}
        stat = db.execute(text(
            "SELECT count(*) FILTER (WHERE status <> 'rejected'),"
            "       count(*) FILTER (WHERE status = 'confirmed'),"
            "       count(*) FILTER (WHERE status = 'conflict'),"
            "       count(*) FILTER (WHERE status = 'confirmed'"
            "                         OR (status = 'draft' AND confidence >= 80))"
            "  FROM client_facts WHERE account_id IN :accs")
            .bindparams(bindparam("accs", expanding=True)), {"accs": accs}).first()
        names = {a.account_id: (a.name or a.account_id)
                 for a in db.query(Account).filter(Account.account_id.in_(accs)).all()}
        return {"status": "ok", "product": product,
                "product_title": PRODUCTS[product],
                "accounts": [{"account_id": a, "name": names.get(a, a)} for a in accs],
                "memory_shared": bool(rows[0][2]) if rows else False,
                "facts": stat[0] or 0, "confirmed": stat[1] or 0,
                "conflicts": stat[2] or 0, "usable": stat[3] or 0}
    finally:
        db.close()


@router.get("/account_panel")
def account_panel(product: str, account_id: str, user=Depends(get_current_user)):
    """Strict account-scoped product panel for AI employee setup.
    Returns only the selected account. No owner-wide aggregation is performed here."""
    if product not in PRODUCTS:
        raise HTTPException(status_code=400, detail="Неизвестный продукт")
    db = SessionLocal()
    try:
        visible = {a.account_id: a for a in _visible_accounts(db, user)}
        if account_id not in visible:
            raise HTTPException(status_code=403, detail="Аккаунт недоступен")
        a = visible[account_id]
        bound_row = db.execute(text(
            "SELECT group_key,memory_shared FROM ai_bindings WHERE product=:p AND account_id=:a"),
            {"p": product, "a": account_id}).first()
        bound = bool(bound_row)
        common = {
            "status": "ok", "product": product, "account_id": account_id,
            "name": _display_account_name(a), "enabled": bound,
            "memory_shared": bool(bound_row[1]) if bound_row else False,
        }
        if product == "mop":
            import json as _json
            from datetime import datetime, timezone
            acc = db.execute(text("SELECT client_goal,client_goal_text FROM accounts WHERE account_id=:a"), {"a":account_id}).mappings().first() or {}
            raw = db.execute(text("SELECT value FROM storage WHERE account_id=:a AND key='mop_crm_sales_settings' ORDER BY id DESC LIMIT 1"), {"a":account_id}).scalar()
            try: cfg = _json.loads(raw) if raw else {}
            except Exception: cfg = {}
            ws = cfg.get("work_schedule") if isinstance(cfg,dict) else {}
            try:
                from app.api.messenger import _mop_schedule_active
                active_now, resolved_ws = _mop_schedule_active(db, account_id)
            except Exception:
                active_now, resolved_ws = True, (ws if isinstance(ws,dict) else {})
            try:
                from zoneinfo import ZoneInfo
                tz_name = str((resolved_ws or {}).get("timezone") or "Europe/Moscow")
                local_now = datetime.now(timezone.utc).astimezone(ZoneInfo(tz_name))
                local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
                start_epoch = int(local_start.timestamp())
                start_dt = local_start.astimezone(timezone.utc)
            except Exception:
                start_epoch = int(datetime.now(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0).timestamp())
                start_dt = datetime.fromtimestamp(start_epoch, timezone.utc)
            dialogs_today = int(db.execute(text("SELECT count(*) FROM messenger_leads WHERE account_id=:a AND last_msg_at>=:s"), {"a":account_id,"s":start_epoch}).scalar() or 0)
            continued_today = int(db.execute(text("SELECT count(*) FROM messenger_leads WHERE account_id=:a AND last_msg_at>=:s AND coalesce(msg_count,0)>=2"), {"a":account_id,"s":start_epoch}).scalar() or 0)
            contacts_today = int(db.execute(text("SELECT count(*) FROM messenger_leads WHERE account_id=:a AND has_phone=true AND last_msg_at>=:s"), {"a":account_id,"s":start_epoch}).scalar() or 0)
            qual_rows = db.execute(text("""
                SELECT s.value
                  FROM messenger_leads l
                  JOIN storage s ON s.account_id=l.account_id
                                AND s.key=('mop_qualification:' || l.avito_chat_id)
                 WHERE l.account_id=:a AND l.last_msg_at>=:s
            """), {"a":account_id,"s":start_epoch}).fetchall()
            classified_today = len(qual_rows)
            qualified_today = sum(1 for x in qual_rows if 'QUALIFIED' in str(x[0] or '') or 'TARGET_ACTION' in str(x[0] or '')) if classified_today else None
            target_actions = sum(1 for x in qual_rows if 'TARGET_ACTION' in str(x[0] or '')) if classified_today else None
            human_today = int(db.execute(text("SELECT count(*) FROM mop_drafts WHERE account_id=:a AND status='human_required' AND updated_at>=:s"), {"a":account_id,"s":start_dt}).scalar() or 0)
            deals_today = int(db.execute(text("SELECT count(*) FROM boris_crm_deals WHERE avito_account_id=:a AND created_at>=:s"), {"a":account_id,"s":start_dt}).scalar() or 0)
            attention_now = 0; first_attention_chat = None
            try:
                from app.api.inbox_slots import inbox_attention
                attention_projection = inbox_attention(account_id=account_id, user=user) or {}
                attention_items = attention_projection.get("items") or []
                attention_now = len(attention_items)
                first_attention_chat = attention_items[0].get("avito_chat_id") if attention_items else None
            except Exception:
                attention_now = int(db.execute(text("SELECT count(*) FROM mop_drafts WHERE account_id=:a AND status IN ('human_required','send_failed','draft_ready')"), {"a":account_id}).scalar() or 0)
                first_attention_chat = db.execute(text("SELECT avito_chat_id FROM mop_drafts WHERE account_id=:a AND status IN ('human_required','send_failed','draft_ready') ORDER BY updated_at ASC LIMIT 1"), {"a":account_id}).scalar()
            confirmed_rules = int(db.execute(text("SELECT count(*) FROM client_facts WHERE account_id=:a AND category='rule' AND status='confirmed'"), {"a":account_id}).scalar() or 0)
            learned_rules = [{"id": int(r[0]), "text": str(r[1] or '')[:500], "at": r[2].isoformat() if r[2] else None,
                              "source": str(r[3] or '')[:160]}
                             for r in db.execute(text("SELECT id,value,coalesce(confirmed_at,updated_at,source_date),source_ref FROM client_facts WHERE account_id=:a AND category='rule' AND status='confirmed' ORDER BY coalesce(confirmed_at,updated_at,source_date) DESC NULLS LAST LIMIT 3"), {"a":account_id}).fetchall()]
            ai_cost_today = float(db.execute(text("SELECT coalesce(sum(cost_rub),0) FROM api_usage WHERE account_id=:a AND created_at::date=current_date AND (operation ILIKE '%мессенджер%' OR operation ILIKE '%МОП%' OR operation ILIKE '%спарринг%' OR operation ILIKE '%диалог%')"), {"a":account_id}).scalar() or 0)
            # Existing combat store is account-keyed; read only this account.
            avg_sparring = None; last_training = None
            try:
                from app.api.mop_combat_training import _load
                rows = _load(account_id)
                scores=[]; times=[]
                for row in rows:
                    if isinstance(row.get('score'), (int,float)) and row.get('score',0)>0: scores.append(float(row['score']))
                    for fb in row.get('feedback') or []:
                        if isinstance(fb,dict) and isinstance(fb.get('score'),(int,float)): scores.append(float(fb['score']))
                    for k in ('updated_at','created_at'):
                        if isinstance(row.get(k),(int,float)): times.append(int(row[k]))
                if scores: avg_sparring=round(sum(scores)/len(scores),1)
                if times: last_training=datetime.fromtimestamp(max(times),timezone.utc).isoformat()
            except Exception:
                pass
            # The current MOP training program also persists account-scoped sessions
            # in existing storage keys mop_training:<session>. Use them as an
            # additional read-only source for "last training"; no new training store.
            try:
                training_rows = db.execute(text("SELECT value FROM storage WHERE account_id=:a AND key LIKE 'mop_training:%'"), {"a":account_id}).fetchall()
                training_times=[]
                for tr in training_rows:
                    try:
                        td=_json.loads(tr[0] or "{}")
                        tv=td.get("created_at")
                        if tv: training_times.append(datetime.fromisoformat(str(tv).replace("Z","+00:00")))
                        rv=td.get("review") or {}
                        sc=rv.get("score") if isinstance(rv,dict) else None
                        if isinstance(sc,(int,float)): scores.append(float(sc))
                    except Exception:
                        continue
                if scores: avg_sparring=round(sum(scores)/len(scores),1)
                if training_times:
                    latest_training=max(training_times)
                    if latest_training.tzinfo is None: latest_training=latest_training.replace(tzinfo=timezone.utc)
                    candidate=latest_training.astimezone(timezone.utc).isoformat()
                    if not last_training or candidate > last_training: last_training=candidate
            except Exception:
                pass
            try:
                from app.api.messenger import get_manager_balance
                package_active = bool((get_manager_balance(account_id) or {}).get("active"))
            except Exception:
                package_active = False
            owner_enabled = bool(cfg.get("mop_enabled", cfg.get("enabled", True)))
            # Universal MOP module rule: every account with an active MOP binding
            # and active package gets the training/combat module automatically.
            # The training module is available even when the live MOP is outside
            # its work schedule, because sparring never sends messages to Avito.
            training_available = bool(bound and package_active and owner_enabled)
            common["mop"] = {
                "state": "выключен" if (not package_active or not owner_enabled) else ("работает" if active_now else "вне графика"),
                "package_active": package_active,
                "owner_enabled": owner_enabled,
                "training_available": training_available,
                "goal": acc.get("client_goal_text") or acc.get("client_goal") or "Не задана",
                "schedule": resolved_ws,
                "dialogs_today": dialogs_today,
                "continued_today": continued_today,
                "qualified_today": qualified_today,
                "qualification_coverage": classified_today,
                "contacts_today": contacts_today,
                "target_actions": target_actions,
                "handed_to_human_today": human_today,
                "crm_deals_today": deals_today,
                "attention_now": attention_now,
                "first_attention_chat": str(first_attention_chat) if first_attention_chat else None,
                "avg_sparring_score": avg_sparring,
                "last_training_at": last_training,
                "confirmed_rules": confirmed_rules,
                "learned_rules": learned_rules,
                "ai_cost_today_rub": round(ai_cost_today,2),
                "funnel": {"dialogs_today":dialogs_today,"continued_today":continued_today,
                           "qualified_today":qualified_today,"contacts_today":contacts_today,
                           "target_actions":target_actions,"handed_to_human_today":human_today},
            }
        else:
            from app.api.calltracking import rop_product_summary
            # ROP_MONTH_PANEL_V1: the AI-employee card is a management report,
            # not a same-day pulse. Use the last 30 days so an active ROP does not
            # look empty on a quiet day when historical calls/chats already exist.
            ps = rop_product_summary(account_id, 30)
            sm = ps.get("summary") or {}; ins=ps.get("insights") or {}; att=ps.get("attention") or []
            latest = None
            for row in (ps.get("calls") or []) + (ps.get("chats") or []):
                dt=row.get("created_at")
                if dt and (latest is None or dt > latest): latest=dt
            last_transfer = db.execute(text("SELECT max(updated_at) FROM messenger_prompts WHERE account_id=:a AND is_active=true AND custom_instructions LIKE '%=== Опыт от ИИ Руководителя отдела продаж ===%'"), {"a":account_id}).scalar()
            reactivation = int(db.execute(text("SELECT count(*) FROM reactivation_candidates WHERE account_id=:a AND status IN ('candidate','needs_review')"), {"a":account_id}).scalar() or 0)
            common["rop"] = {
                "state": "работает" if bound else "выключен",
                "period_days": 30,
                "calls": int(sm.get("calls_analyzed") or 0),
                "chats": int(sm.get("chats_analyzed") or 0),
                "avg_score": sm.get("avg_score"),
                "strong": int(sm.get("strong") or 0),
                "weak": int(sm.get("weak") or 0),
                "attention": len(att),
                "last_analysis_at": latest,
                "mop_recommendations": len(ins.get("detailed_actions") or ins.get("recommendations") or []),
                "recommendations": (ins.get("recommendations") or [])[:5],
                "detailed_actions": (ins.get("detailed_actions") or [])[:6],
                "main_problems": (ins.get("main_problems") or [])[:5],
                "growth_points": (ins.get("growth_points") or [])[:5],
                "client_triggers": (ins.get("client_triggers") or [])[:5],
                "reactivation_candidates": reactivation,
                "last_mop_transfer_at": last_transfer.isoformat() if last_transfer else None,
            }
        return common
    finally:
        db.close()



# ---------------------------------------------------------------- режим МОПа
class MopModeBody(BaseModel):
    account_id: str
    contour: str


def _owns_account(db, user, account_id: str) -> bool:
    """Право менять режим. Системная роль owner — это BORIS; для клиента
    владение определяется по accounts.owner_user_id или роли в связи."""
    from sqlalchemy import text as _t
    if _is_owner(user):
        return True
    uid = getattr(user, "id", 0)
    if db.execute(_t("SELECT 1 FROM accounts WHERE account_id = :a"
                     " AND owner_user_id = :u"), {"a": account_id, "u": uid}).fetchone():
        return True
    r = db.execute(_t("SELECT role FROM user_account_access"
                      " WHERE user_id = :u AND account_id = :a"),
                   {"u": uid, "a": account_id}).fetchone()
    return bool(r and r[0] == "owner")


@router.get("/mop_mode")
def get_mop_mode(account_id: str, user=Depends(get_current_user)):
    """Как AI-МОП отвечает по этому аккаунту: сразу или через подтверждение."""
    from app.mop_core import contour_of
    db = SessionLocal()
    try:
        visible = {a.account_id for a in _visible_accounts(db, user)}
        if account_id not in visible:
            raise HTTPException(status_code=403, detail="Аккаунт недоступен")
        c = contour_of(db, account_id)
        return {
            "status": "ok",
            "account_id": account_id,
            "contour": c,
            "title": "Показывать ответ перед отправкой" if c == "new"
                     else "Отвечать покупателю сразу",
            "can_change": _owns_account(db, user, account_id),
        }
    finally:
        db.close()


@router.post("/mop_mode")
def set_mop_mode(body: MopModeBody, user=Depends(get_current_user)):
    """Сменить режим. Решение о том, как работает продажа, принимает владелец."""
    from app.mop_core import set_contour, contour_of, CONTOURS
    if body.contour not in CONTOURS:
        raise HTTPException(status_code=400,
                            detail="Режим: %s" % ", ".join(CONTOURS))
    db = SessionLocal()
    try:
        visible = {a.account_id for a in _visible_accounts(db, user)}
        if body.account_id not in visible:
            raise HTTPException(status_code=403, detail="Аккаунт недоступен")
        if not _owns_account(db, user, body.account_id):
            raise HTTPException(status_code=403,
                                detail="Режим ответов меняет руководитель")
        was = contour_of(db, body.account_id)
        set_contour(db, body.account_id, body.contour)
        return {"status": "ok", "account_id": body.account_id,
                "was": was, "now": body.contour}
    finally:
        db.close()
