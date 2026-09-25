from fastapi import Depends as _Depends
from app.api.auth import get_current_user as _get_cur
from fastapi import APIRouter
from pydantic import BaseModel
from typing import List, Optional
import json
from datetime import date, datetime, timedelta, timezone

from app.db.session import SessionLocal
from app.models.storage import Storage
from app.models.reliability import ReliabilityHeartbeat
from app.services.posting_store_lock import (
    load_posting_posts_for_update,
    posting_posts_lock_held,
    save_posting_posts_locked,
)

from app.services.reliability import dependency_call as _dep_call
from app.services.exception_observability import observe_suppressed

router = APIRouter(prefix="/api/posting", tags=["posting"])


def _owner_public_copy_output_health(value, project):
    """Independent last-mile rejector for owner-approved human editorial policy.

    This deliberately does not trust the renderer's own health report. If a
    concurrent code writer regresses the renderer, manual API publish still
    fails closed before any Telegram/VK request.
    """
    name = str((project or {}).get("name") or "").strip().lower()
    guarded = (
        str((project or {}).get("public_copy_policy") or "") == "human_editorial_v2"
        or "океан клиент" in name
        or "от души" in name
    )
    if not guarded:
        return {"ok": True, "guarded": False, "reasons": []}

    text_value = str(value or "")
    low = text_value.lower()
    reasons = []
    forbidden = (
        "http://", "https://", "youtu.be", "youtube.com", "rutube.ru",
        "🤖 boris:", "ai-роп — контроль продаж",
        "настройка boris и сопровождение первого месяца — бесплатно",
    )
    for marker in forbidden:
        if marker in low:
            reasons.append("forbidden:" + marker)

    if "#" in text_value:
        reasons.append("hashtag_or_hash")

    meta_prefixes = (
        "тема:", "тон:", "структура:", "формат:", "cta:",
        "хэштеги:", "хэштег:", "теги:", "тег:",
        "для изображения:", "визуальный стиль:",
    )
    for line in text_value.splitlines():
        norm = line.strip().lower()
        if any(norm.startswith(prefix) for prefix in meta_prefixes):
            reasons.append("prompt_meta:" + norm[:48])

    return {
        "ok": not bool(reasons),
        "guarded": True,
        "reasons": reasons[:12],
        "length": len(text_value),
    }


def _get(db, account_id, key):
    return db.query(Storage).filter(Storage.account_id == account_id, Storage.key == key).first()

def _load(db, account_id, key, default=None, for_update=False):
    if key == "posting_posts" and for_update:
        _row, posts = load_posting_posts_for_update(db, account_id)
        return posts
    row = _get(db, account_id, key)
    if not row:
        return default
    try:
        return json.loads(row.value)
    except Exception:
        return default

def _save(db, account_id, key, value):
    if key == "posting_posts":
        if not posting_posts_lock_held(db, account_id):
            raise RuntimeError("POSTING_POSTS_WRITE_WITHOUT_LOCK")
        row = _get(db, account_id, key)
        save_posting_posts_locked(db, account_id, row, value)
        return
    row = _get(db, account_id, key)
    s = json.dumps(value, ensure_ascii=False)
    if row:
        row.value = s
    else:
        db.add(Storage(account_id=account_id, key=key, value=s))
    db.commit()


@router.get("/config")
def get_config(account_id: str):
    db = SessionLocal()
    try:
        settings = _load(db, account_id, "posting_settings", {})
        sub = _load(db, account_id, "posting_subscription", None)
        today = date.today().isoformat()
        cnt = _get(db, account_id, "posting_count:" + account_id + ":" + today)
        used_today = 0
        if cnt:
            try: used_today = int(cnt.value)
            except Exception: used_today = 0
        plan = platforms = left = None
        active = False
        if sub:
            plan = sub.get("plan")
            platforms = sub.get("platforms")
            try:
                paid = date.fromisoformat(sub.get("paid_at", ""))
                active = (date.today() - paid).days < int(sub.get("period_days", 30))
            except Exception:
                active = False
            left = max(0, int(sub.get("posts_per_day", 3)) - used_today)
        return {"settings": settings,
                "subscription": {"plan": plan, "platforms": platforms, "active": active,
                                 "used_today": used_today, "left_today": left}}
    finally:
        db.close()


class SettingsBody(BaseModel):
    account_id: str
    channel_tg: str = ""
    vk_owner_id: Optional[str] = None
    style_source: str = ""
    themes: List[str] = []
    times: List[str] = []
    text_prompt: str = ""
    banner_prompt: str = ""

@router.post("/settings")
def save_settings(body: SettingsBody):
    db = SessionLocal()
    try:
        cfg = {"channel_tg": body.channel_tg, "vk_owner_id": _norm_vk(body.vk_owner_id),
               "style_source": body.style_source, "themes": body.themes, "times": body.times,
               "text_prompt": body.text_prompt, "banner_prompt": body.banner_prompt}
        _save(db, body.account_id, "posting_settings", cfg)
        return {"status": "ok"}
    finally:
        db.close()


class TrialBody(BaseModel):
    account_id: str
    platforms: str = "both"
    project_id: Optional[str] = None

@router.post("/trial")
def start_trial(body: TrialBody, _cur=_Depends(_get_cur)):
    try:
        from app.api.nps import ensure_trial as _et
        _et(getattr(body, "account_id", None), "social")
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=166)
    body.account_id = _ubucket(_cur)
    db = SessionLocal()
    try:
        sub = {"plan": "trial", "platforms": body.platforms,
               "paid_at": date.today().isoformat(), "period_days": 3,
               "posts_per_day": 3, "trial_total": 6, "trial_used": True, "amount_rub": 0}
        if body.project_id:
            projs = _load(db, body.account_id, "posting_projects", []) or []
            for p in projs:
                if p.get("id") == body.project_id:
                    if (p.get("subscription") or {}).get("trial_used"):
                        return {"status": "used", "message": "Бесплатный тест по этому проекту уже был использован"}
                    p["subscription"] = sub
                    _save(db, body.account_id, "posting_projects", projs)
                    return {"status": "ok", "message": "Бесплатный тест на 3 дня / до 6 постов активирован для проекта"}
            return {"status": "not_found", "message": "Проект не найден"}
        existing = _load(db, body.account_id, "posting_subscription", None)
        if existing and existing.get("trial_used"):
            return {"status": "used", "message": "Бесплатный тест уже был использован"}
        _save(db, body.account_id, "posting_subscription", sub)
        return {"status": "ok", "message": "Бесплатный тест на 3 дня / до 6 постов активирован"}
    finally:
        db.close()


# ================= ПРОЕКТЫ (мультипроектность) =================
import uuid as _uuid

def _load_projects(db, account_id):
    return _load(db, account_id, "posting_projects", []) or []

def _proj_active(proj):
    sub = proj.get("subscription")
    if not sub:
        return False
    try:
        paid = date.fromisoformat(sub.get("paid_at", ""))
        return (date.today() - paid).days < int(sub.get("period_days", 30))
    except Exception:
        return False


def _ubucket(cur):
    return "u" + str(getattr(cur, "id", "0"))


@router.get("/projects")
def get_projects(account_id: str = "", _cur=_Depends(_get_cur)):
    _is_owner = getattr(_cur, "role", None) == "owner"
    account_id = _ubucket(_cur)
    db = SessionLocal()
    try:
        projs = _load_projects(db, account_id)
        _hb = db.query(ReliabilityHeartbeat).filter(
            ReliabilityHeartbeat.module == "runtime",
            ReliabilityHeartbeat.worker_id == "social_posting_scheduler",
            ReliabilityHeartbeat.account_id == "*",
        ).first()
        _scheduler_age = None
        _scheduler_ok = False
        if _hb and _hb.last_seen_at:
            _seen = _hb.last_seen_at
            if _seen.tzinfo is None:
                _seen = _seen.replace(tzinfo=timezone.utc)
            _scheduler_age = max(0, int((datetime.now(timezone.utc) - _seen).total_seconds()))
            _scheduler_ok = str(_hb.state or "") == "ok" and _scheduler_age <= 180
        out = []
        for p in projs:
            _project_today = (datetime.now() + timedelta(hours=int(p.get("tz_offset", 3) or 3))).strftime("%Y%m%d")
            cnt = _get(db, account_id, "posting_count:" + p.get("id", "") + ":" + _project_today)
            used = 0
            if cnt:
                try: used = int(cnt.value)
                except Exception: used = 0
            sub = p.get("subscription") or {}
            per_day = int(sub.get("posts_per_day", 3)) if sub else 3
            out.append({**p, "active": True if _is_owner else _proj_active(p),
                        "used_today": used,
                        "left_today": None if _is_owner else (max(0, per_day - used) if sub else 0),
                        "scheduler_ok": _scheduler_ok,
                        "scheduler_age_seconds": _scheduler_age})
        return {"projects": out}
    finally:
        db.close()

class ProjectBody(BaseModel):
    account_id: str = ""
    id: Optional[str] = None
    name: str = "Новый проект"
    channel_tg: str = ""
    vk_owner_id: Optional[int] = None
    style_source: str = ""
    themes: List[str] = []
    times: List[str] = []
    text_prompt: str = ""
    banner_prompt: str = ""
    image_source: str = "ai"
    platforms: str = "both"
    contact_tg: str = ""
    contact_vk: str = ""
    mode: str = "auto"
    auto_publish_hours: int = 0
    tz_offset: int = 3
    paid_auto_generation: Optional[bool] = None

def _norm_vk(value):
    import re as _re, os as _os, requests as _rq
    if value is None or not str(value).strip():
        return None
    v = str(value).strip()
    m = _re.search(r"wall(-?[0-9]+)_", v) or _re.search(r"(?:club|public)([0-9]+)", v)
    if m:
        return -abs(int(m.group(1)))
    if _re.fullmatch(r"-?[0-9]+", v):
        return -abs(int(v))
    name = v.rstrip("/").split("/")[-1].split("?")[0]
    tok = _os.environ.get("VK_TOKEN", "")
    if not tok:
        return None
    try:
        r = _dep_call("vk.api", _rq.get, "https://api.vk.com/method/groups.getById",
                    params={"group_id": name, "access_token": tok, "v": "5.199"}, timeout=20)
        gs = (r.json().get("response") or {}).get("groups") or []
        return -int(gs[0]["id"]) if gs else None
    except Exception as e:
        print("[vk_norm] ошибка:", str(e)[:150], flush=True)
        return None


def _enforce_project_content_contract(data: dict) -> dict:
    """Server-owned rules that must survive ordinary UI edits."""
    out = dict(data or {})
    if "океан клиент" not in str(out.get("name") or "").lower():
        return out
    text_prompt = str(out.get("text_prompt") or "")
    legacy = (
        "\n\nЖЕЛЕЗНОЕ ПРАВИЛО ДЛЯ КАЖДОГО НОВОГО ПОСТА «ОКЕАН КЛИЕНТОВ»: "
        "короткий CTA-блок с бесплатной настройкой BORIS, сопровождением первого месяца и "
        "MAX/WhatsApp 8 981 967-37-87 должен присутствовать в КАЖДОМ посте. "
        "Короткий блок основных модулей BORIS тоже обязателен. "
        "НЕ добавляй YouTube, RuTube, видео-ссылки, сайт, регистрационные URL или любые другие ссылки. "
        "Фактический футер и лимит Telegram дополнительно обеспечивает сервер после генерации."
    )
    text_prompt = text_prompt.replace(legacy, "")
    quality = (
        "\n\nПУБЛИЧНЫЙ КОНТЕНТ «ОКЕАН КЛИЕНТОВ»: пиши как живой редактор, а не как рекламный шаблон. "
        "Одна конкретная мысль на пост. Не перечисляй все модули BORIS без прямой необходимости. "
        "Не добавляй хэштеги, ссылки, видео, служебные пометки, названия полей промта или технические инструкции. "
        "Не привязывай пост к календарному событию только ради повода. "
        "CTA допустим одной естественной фразой и не обязан быть в каждом посте; контакт при необходимости добавит сервер."
    )
    if "ПУБЛИЧНЫЙ КОНТЕНТ «ОКЕАН КЛИЕНТОВ»" not in text_prompt:
        text_prompt += quality
    out["text_prompt"] = text_prompt
    banner_prompt = str(out.get("banner_prompt") or "")
    mobile = (
        "\n\nМОБИЛЬНАЯ ЧИТАЕМОСТЬ ОБЯЗАТЕЛЬНА: на баннере максимум 1 короткий заголовок и "
        "1 короткий подзаголовок, суммарно желательно 3–9 слов. Никаких списков модулей, телефонов, "
        "длинных абзацев, таблиц и мелкого текста. Главная мысль должна читаться с телефона за 1–2 секунды."
    )
    if "МОБИЛЬНАЯ ЧИТАЕМОСТЬ ОБЯЗАТЕЛЬНА" not in banner_prompt:
        banner_prompt += mobile
    out["banner_prompt"] = banner_prompt
    return out


@router.post("/projects/save")
def save_project(body: ProjectBody, _cur=_Depends(_get_cur)):
    body.account_id = _ubucket(_cur)
    db = SessionLocal()
    try:
        projs = _load_projects(db, body.account_id)
        data = {"name": body.name, "channel_tg": body.channel_tg, "vk_owner_id": body.vk_owner_id,
                "style_source": body.style_source, "themes": body.themes, "times": body.times,
                "text_prompt": body.text_prompt, "banner_prompt": body.banner_prompt,
                "image_source": body.image_source, "platforms": body.platforms,
                "contact_tg": body.contact_tg, "contact_vk": body.contact_vk, "mode": body.mode,
                "auto_publish_hours": body.auto_publish_hours, "tz_offset": body.tz_offset}
        if body.paid_auto_generation is not None:
            data["paid_auto_generation"] = bool(body.paid_auto_generation)
        data = _enforce_project_content_contract(data)
        if body.id:
            found = False
            for p in projs:
                if p.get("id") == body.id:
                    p.update(data); found = True; break
            if not found:
                return {"status": "not_found"}
        else:
            data["id"] = _uuid.uuid4().hex[:12]
            data["subscription"] = None
            projs.append(data)
        _save(db, body.account_id, "posting_projects", projs)
        return {"status": "ok", "id": body.id or data.get("id")}
    finally:
        db.close()

class DeleteProjectBody(BaseModel):
    account_id: str
    id: str

@router.post("/projects/delete")
def delete_project(body: DeleteProjectBody, _cur=_Depends(_get_cur)):
    body.account_id = _ubucket(_cur)
    db = SessionLocal()
    try:
        projs = _load_projects(db, body.account_id)
        projs = [p for p in projs if p.get("id") != body.id]
        _save(db, body.account_id, "posting_projects", projs)
        return {"status": "ok"}
    finally:
        db.close()


# ================= ВИТРИНА ПОСТОВ (модерация + история) =================
@router.get("/posts")
def get_posts(account_id: str = "", project_id: str = "", _cur=_Depends(_get_cur)):
    account_id = _ubucket(_cur)
    db = SessionLocal()
    try:
        posts = _load(db, account_id, "posting_posts", []) or []
    finally:
        db.close()
    if project_id:
        posts = [p for p in posts if p.get("project_id") == project_id]
    return {"posts": posts}

class PostActionBody(BaseModel):
    account_id: str
    post_id: str

@router.post("/posts/delete")
def delete_post(body: PostActionBody, _cur=_Depends(_get_cur)):
    body.account_id = _ubucket(_cur)
    db = SessionLocal()
    try:
        posts = _load(db, body.account_id, "posting_posts", [], for_update=True) or []
        target = next((p for p in posts if p.get("id") == body.post_id), None)
        if not target:
            return {"status": "not_found"}
        if target.get("ids"):
            return {"status": "external_publication_exists",
                    "message": "Сначала удалите публикацию из Telegram/ВКонтакте"}
        posts = [p for p in posts if p.get("id") != body.post_id]
        _save(db, body.account_id, "posting_posts", posts)
        return {"status": "ok"}
    finally:
        db.close()

@router.post("/posts/publish")
def publish_post(body: PostActionBody, _cur=_Depends(_get_cur)):
    body.account_id = _ubucket(_cur)
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    import posting_runner as pr
    from app.services.social_owner_policy_v3 import (
        is_guarded as _social_is_guarded,
        is_ocean as _social_is_ocean,
        public_copy_runtime_boundary_health as _social_boundary_health,
        render_public_text as _social_render_public_text,
    )

    delivery_lock = pr._try_post_delivery_lock(body.account_id, body.post_id)
    if delivery_lock is None:
        return {"status": "busy", "results": {},
                "message": "Этот пост уже публикуется другим процессом"}
    try:
        # Re-read only after the distributed lock. A stale browser request must
        # never resend a post that the scheduler has just completed.
        post = pr._load_post_record(body.account_id, body.post_id)
        db = SessionLocal()
        try:
            projects = _load(db, body.account_id, "posting_projects", []) or []
        finally:
            db.close()
        if not post:
            return {"status": "not_found"}
        if post.get("status") == "published" and post.get("ids"):
            return {"status": "already_published", "results": {}, "ids": post.get("ids") or {},
                    "message": "Пост уже опубликован; повторная отправка не выполнена"}
        if post.get("status") not in ("draft", "partial"):
            return {"status": "invalid_state", "results": {},
                    "message": "Публиковать можно только черновик или частично доставленный пост"}

        proj = next((p for p in projects if p.get("id") == post.get("project_id")), None) or {}
        if _social_is_guarded(proj):
            boundary = _social_boundary_health()
            if not bool(boundary.get("ok")):
                return {
                    "status": "fail",
                    "results": {},
                    "errors": {"copy": "PUBLIC_COPY_BOUNDARY_REGRESSION"},
                    "quality_boundary": boundary,
                    "message": "Публикация остановлена: финальная проверка публичного текста не пройдена",
                }
        banner = os.path.join(pr.BANNER_DIR, post["banner"]) if post.get("banner") else None
        _media_ok, _media_err = pr._banner_delivery_ready({"banner": banner}, proj.get("image_source", "ai"))
        if not _media_ok:
            return {"status": "fail", "results": {},
                    "errors": {"banner": "BANNER_REQUIRED: " + str(_media_err)}}

        platforms = proj.get("platforms", "both")
        text = post.get("text", "")
        tracking = pr._ocean_tracking_record(text, proj, post.get("id"))
        tracking_id = str(tracking.get("utm_content") or "")
        if tracking:
            post.update(tracking)
        results = {}
        existing_ids = dict(post.get("ids") or {})
        rendered = {}

        # Independent per-output fail-closed boundary. The renderer may change,
        # but no guarded text reaches a provider until the actual outgoing bytes
        # satisfy the owner-approved human_editorial_v2 policy.
        if (
            proj.get("channel_tg")
            and platforms in ("tg", "both")
            and not (
                isinstance(existing_ids.get("tg"), dict)
                and existing_ids["tg"].get("message_id")
            )
        ):
            if _social_is_ocean(proj):
                rendered["tg"] = pr._project_delivery_text_tracked(
                    text, proj, "tg", tracking_id=tracking_id
                )
                output_health = pr._ocean_publication_gate(
                    rendered["tg"], text, "tg", tracking_id
                )
                if not output_health.get("ok"):
                    return {
                        "status": "fail",
                        "results": {},
                        "errors": {"copy": "OCEAN_PUBLICATION_GATE_REJECTED"},
                        "output_health": output_health,
                        "message": "Публикация остановлена: ссылка/UTM не прошли финальную проверку",
                    }
            else:
                rendered["tg"] = _social_render_public_text(
                    text, proj, "tg", fallback=pr._project_delivery_text
                )
                output_health = _owner_public_copy_output_health(rendered["tg"], proj)
                if not output_health.get("ok"):
                    return {
                        "status": "fail",
                        "results": {},
                        "errors": {"copy": "PUBLIC_COPY_OUTPUT_REJECTED"},
                        "output_health": output_health,
                        "message": "Публикация остановлена: итоговый текст нарушает публичный контракт",
                    }

        if (
            proj.get("vk_owner_id")
            and platforms in ("vk", "both")
            and not (
                isinstance(existing_ids.get("vk"), dict)
                and existing_ids["vk"].get("post_id")
            )
        ):
            if _social_is_ocean(proj):
                rendered["vk"] = pr._project_delivery_text_tracked(
                    text, proj, "vk", tracking_id=tracking_id
                )
                output_health = pr._ocean_publication_gate(
                    rendered["vk"], text, "vk", tracking_id
                )
                if not output_health.get("ok"):
                    return {
                        "status": "fail",
                        "results": {},
                        "errors": {"copy": "OCEAN_PUBLICATION_GATE_REJECTED"},
                        "output_health": output_health,
                        "message": "Публикация остановлена: ссылка/UTM не прошли финальную проверку",
                    }
            else:
                rendered["vk"] = _social_render_public_text(
                    text, proj, "vk", fallback=pr._project_delivery_text
                )
                output_health = _owner_public_copy_output_health(rendered["vk"], proj)
                if not output_health.get("ok"):
                    return {
                        "status": "fail",
                        "results": {},
                        "errors": {"copy": "PUBLIC_COPY_OUTPUT_REJECTED"},
                        "output_health": output_health,
                        "message": "Публикация остановлена: итоговый текст нарушает публичный контракт",
                    }

        if proj.get("channel_tg") and platforms in ("tg", "both"):
            if isinstance(existing_ids.get("tg"), dict) and existing_ids["tg"].get("message_id"):
                results["tg"] = {
                    "ok": True,
                    "skipped_existing": True,
                    "message_id": existing_ids["tg"].get("message_id"),
                }
            else:
                results["tg"] = pr._send_telegram(
                    proj["channel_tg"], rendered["tg"], banner, tenant_scope=body.account_id
                )

        if proj.get("vk_owner_id") and platforms in ("vk", "both"):
            if isinstance(existing_ids.get("vk"), dict) and existing_ids["vk"].get("post_id"):
                results["vk"] = {
                    "ok": True,
                    "skipped_existing": True,
                    "post_id": existing_ids["vk"].get("post_id"),
                }
            else:
                results["vk"] = pr._send_vk(
                    int(proj["vk_owner_id"]), rendered["vk"], banner, tenant_scope=body.account_id
                )

        merged_ids = pr._merge_delivery_ids(existing_ids, proj, results)
        ok = pr._all_required_delivery_ok(proj, results)
        db = SessionLocal()
        try:
            posts = _load(db, body.account_id, "posting_posts", [], for_update=True) or []
            for p in posts:
                if p.get("id") != body.post_id:
                    continue
                p["ids"] = merged_ids
                p["external_post_id"] = dict(merged_ids or {})
                if tracking:
                    p.update(tracking)
                if ok:
                    _preview_platform = "tg" if proj.get("channel_tg") else "vk"
                    if _social_is_ocean(proj):
                        _preview_text = pr._project_delivery_text_tracked(
                            text, proj, _preview_platform, tracking_id=tracking_id
                        )
                    else:
                        _preview_text = _social_render_public_text(
                            text, proj, _preview_platform, fallback=pr._project_delivery_text
                        )
                    pr._mark_social_delivery_published(
                        p,
                        proj,
                        preview_text=_preview_text,
                    )
                else:
                    p["status"] = "partial"
                    p["publication_status"] = "partial"
                    errs = []
                    for k in pr._required_delivery_platforms(proj):
                        rv = results.get(k) if isinstance(results.get(k), dict) else {}
                        if not rv.get("ok"):
                            errs.append(k + ": " + str(rv.get("error") or "delivery_failed")[:120])
                    p["error"] = "; ".join(errs)[:300]
                break
            _save(db, body.account_id, "posting_posts", posts)
        finally:
            db.close()
        return {"status": "ok" if ok else "fail",
                "results": {k: bool(v.get("ok")) for k, v in results.items()},
                "errors": {k: str(v.get("error") or "delivery_failed")[:200]
                           for k, v in results.items() if not v.get("ok")},
                "partial_saved": (not ok), "ids": merged_ids}
    finally:
        pr._release_post_delivery_lock(delivery_lock, body.account_id, body.post_id)


@router.post("/posts/unpublish")
def unpublish_post(body: PostActionBody, _cur=_Depends(_get_cur)):
    """Delete a persisted publication from every platform that still has an ID."""
    body.account_id = _ubucket(_cur)
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    import posting_runner as pr

    delivery_lock = pr._try_post_delivery_lock(body.account_id, body.post_id)
    if delivery_lock is None:
        return {"status": "busy", "had_ids": True, "results": {},
                "message": "С этим постом уже выполняется внешнее действие"}
    try:
        post = pr._load_post_record(body.account_id, body.post_id)
        if not post:
            return {"status": "not_found", "had_ids": False, "results": {}}
        existing_ids = dict(post.get("ids") or {})
        if not existing_ids:
            return {"status": "ok", "had_ids": False, "results": {}}

        results = {}
        tg = existing_ids.get("tg") if isinstance(existing_ids.get("tg"), dict) else {}
        if tg.get("chat_id") and tg.get("message_id"):
            results["tg"] = pr.delete_telegram_post(tg.get("chat_id"), tg.get("message_id"))
        vk = existing_ids.get("vk") if isinstance(existing_ids.get("vk"), dict) else {}
        if vk.get("owner_id") and vk.get("post_id"):
            results["vk"] = pr.delete_vk_post(vk.get("owner_id"), vk.get("post_id"))

        remaining_ids = dict(existing_ids)
        for platform, result in results.items():
            if isinstance(result, dict) and result.get("ok"):
                remaining_ids.pop(platform, None)
        ok = bool(results) and not remaining_ids and all(
            isinstance(v, dict) and v.get("ok") for v in results.values()
        )

        db = SessionLocal()
        try:
            posts = _load(db, body.account_id, "posting_posts", [], for_update=True) or []
            for p in posts:
                if p.get("id") != body.post_id:
                    continue
                p["ids"] = remaining_ids
                if ok:
                    p["status"] = "unpublished"
                    p["error"] = ""
                    p["unpublished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                else:
                    p["status"] = "published"
                    errs = []
                    for platform, result in results.items():
                        if not (isinstance(result, dict) and result.get("ok")):
                            errs.append(platform + ": " + str((result or {}).get("error") or "delete_failed")[:120])
                    p["error"] = "UNPUBLISH_PARTIAL: " + "; ".join(errs)
                break
            _save(db, body.account_id, "posting_posts", posts)
        finally:
            db.close()

        return {
            "status": "ok" if ok else "partial",
            "had_ids": True,
            "results": {k: bool(v.get("ok")) for k, v in results.items() if isinstance(v, dict)},
            "errors": {k: str(v.get("error") or "delete_failed")[:200]
                       for k, v in results.items() if isinstance(v, dict) and not v.get("ok")},
            "remaining_ids": remaining_ids,
        }
    finally:
        pr._release_post_delivery_lock(delivery_lock, body.account_id, body.post_id)


@router.get("/banner")
def get_banner(f: str):
    import os, sys
    from fastapi.responses import FileResponse
    from fastapi import HTTPException
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    import posting_runner as pr
    fname = os.path.basename(f)
    path = os.path.join(pr.BANNER_DIR, fname)
    if os.path.exists(path):
        return FileResponse(path)
    raise HTTPException(status_code=404, detail="not found")


@router.get("/social-assets")
def social_assets(account_id: str = "", _cur=_Depends(_get_cur)):
    """Read-only связь persisted social banners с post lifecycle для кабинета."""
    import os, sys
    account_id = _ubucket(_cur)
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    import posting_runner as pr
    db = SessionLocal()
    try:
        posts = _load(db, account_id, "posting_posts", []) or []
    finally:
        db.close()
    out = []
    for p in posts:
        name = os.path.basename(str(p.get("banner") or ""))
        if not name:
            continue
        path = os.path.join(pr.BANNER_DIR, name)
        if not (os.path.exists(path) and os.path.getsize(path) > 0):
            continue
        out.append({
            "post_id": p.get("id"), "project_id": p.get("project_id"),
            "project_name": p.get("project_name"), "status": p.get("status"),
            "created_at": p.get("created_at"), "publish_at": p.get("publish_at"),
            "banner": name, "bytes": os.path.getsize(path),
            "url": "/api/posting/banner?f=" + name,
        })
    return {"assets": out[:50]}


class RunBody(BaseModel):
    account_id: str = ""
    project_id: str = ""


_RUN_JOBS = {}  # local hot cache; PostgreSQL below is the HA authority
_RUN_JOB_PREFIX = "posting_run_job:"


def _save_run_job(account_id: str, job_id: str, payload: dict) -> None:
    data = dict(payload or {})
    data["job_id"] = str(job_id)
    data["account_id"] = str(account_id)
    data["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db = SessionLocal()
    try:
        _save(db, account_id, _RUN_JOB_PREFIX + str(job_id), data)
        # Bounded history per account. Old rows are operational telemetry, not
        # business records; keeping 60 is enough for recovery/debugging.
        rows = db.query(Storage).filter(
            Storage.account_id == account_id,
            Storage.key.like(_RUN_JOB_PREFIX + "%"),
        ).order_by(Storage.id.desc()).all()
        for row in rows[60:]:
            db.delete(row)
        if len(rows) > 60:
            db.commit()
    finally:
        db.close()


def _load_run_job(account_id: str, job_id: str):
    db = SessionLocal()
    try:
        return _load(db, account_id, _RUN_JOB_PREFIX + str(job_id), None)
    finally:
        db.close()


def _claim_run_job(account_id: str, project_id: str, job_id: str):
    """Atomically claim one manual Social run per account/project across API replicas."""
    from sqlalchemy import text as _sql_text
    db = SessionLocal()
    try:
        lock_key = "social-manual-run:%s:%s" % (str(account_id), str(project_id))
        db.execute(_sql_text("select pg_advisory_xact_lock(hashtextextended(:k, 88319))"), {"k": lock_key})
        rows = db.query(Storage).filter(
            Storage.account_id == account_id,
            Storage.key.like(_RUN_JOB_PREFIX + "%"),
        ).order_by(Storage.id.desc()).limit(60).all()
        now = datetime.now(timezone.utc)
        for row in rows:
            try:
                item = json.loads(row.value or "{}")
            except Exception:
                continue
            if str(item.get("project_id") or "") != str(project_id or "") or item.get("done"):
                continue
            try:
                updated = datetime.fromisoformat(str(item.get("updated_at") or ""))
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=timezone.utc)
                if (now - updated).total_seconds() > 1800:
                    item["done"] = True
                    item["log"] = (str(item.get("log") or "") + "\nЗадача была прервана рестартом и снята с активных.")[-3000:]
                    item["updated_at"] = now.isoformat(timespec="seconds")
                    row.value = json.dumps(item, ensure_ascii=False)
                    continue
            except Exception as _suppressed_exc:
                observe_suppressed(__name__, _suppressed_exc, line=765)
            db.commit()
            return item, False

        payload = {
            "job_id": str(job_id),
            "account_id": str(account_id),
            "project_id": str(project_id or ""),
            "done": False,
            "log": "Запускаю генерацию…",
            "updated_at": now.isoformat(timespec="seconds"),
        }
        db.add(Storage(account_id=account_id, key=_RUN_JOB_PREFIX + str(job_id),
                       value=json.dumps(payload, ensure_ascii=False)))
        db.commit()
        return payload, True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _active_run_job(account_id: str, project_id: str):
    db = SessionLocal()
    try:
        rows = db.query(Storage).filter(
            Storage.account_id == account_id,
            Storage.key.like(_RUN_JOB_PREFIX + "%"),
        ).order_by(Storage.id.desc()).limit(60).all()
        for row in rows:
            try:
                item = json.loads(row.value or "{}")
            except Exception:
                continue
            if str(item.get("project_id") or "") == str(project_id or "") and not item.get("done"):
                try:
                    updated = datetime.fromisoformat(str(item.get("updated_at") or ""))
                    if updated.tzinfo is None:
                        updated = updated.replace(tzinfo=timezone.utc)
                    if (datetime.now(timezone.utc) - updated).total_seconds() > 1800:
                        item["done"] = True
                        item["log"] = (str(item.get("log") or "") + "\nЗадача была прервана рестартом и снята с активных.")[-3000:]
                        item["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                        row.value = json.dumps(item, ensure_ascii=False)
                        db.commit()
                        continue
                except Exception as _suppressed_exc:
                    observe_suppressed(__name__, _suppressed_exc, line=813)
                return item
        return None
    finally:
        db.close()


@router.post("/run")
def run_project(body: RunBody, _cur=_Depends(_get_cur)):
    import io, contextlib, os, sys, threading, uuid
    bucket = _ubucket(_cur)
    is_owner = getattr(_cur, "role", None) == "owner"
    job_id = uuid.uuid4().hex[:12]
    claimed, created = _claim_run_job(bucket, body.project_id, job_id)
    if not created:
        existing_id = str(claimed.get("job_id") or "")
        if existing_id:
            _RUN_JOBS[existing_id] = claimed
        return {"status": "already_running", "job_id": existing_id,
                "message": "По этому проекту уже идёт генерация"}
    _RUN_JOBS[job_id] = claimed
    if len(_RUN_JOBS) > 40:
        for k in list(_RUN_JOBS.keys())[:-20]:
            _RUN_JOBS.pop(k, None)

    def _work():
        buf = io.StringIO()
        try:
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            import posting_runner as pr
            with contextlib.redirect_stdout(buf):
                pr.autopost_projects(bucket, owner=is_owner, respect_time=False,
                                     only_id=body.project_id or None)
            _RUN_JOBS[job_id] = {"done": True, "log": buf.getvalue()[-3000:] or "готово",
                                 "project_id": body.project_id}
            _save_run_job(bucket, job_id, _RUN_JOBS[job_id])
        except Exception as e:
            _RUN_JOBS[job_id] = {"done": True,
                                 "log": (buf.getvalue()[-2500:] + "\nОШИБКА: " + str(e)[:300]),
                                 "project_id": body.project_id}
            _save_run_job(bucket, job_id, _RUN_JOBS[job_id])

    threading.Thread(target=_work, daemon=True).start()
    return {"status": "started", "job_id": job_id}


@router.get("/run_status")
def run_status(job_id: str, _cur=_Depends(_get_cur)):
    bucket = _ubucket(_cur)
    j = _load_run_job(bucket, job_id)
    if not j:
        cached = _RUN_JOBS.get(job_id)
        if isinstance(cached, dict) and str(cached.get("account_id") or "") == bucket:
            j = cached
    if not j:
        return {"status": "unknown", "done": True, "log": "Задача не найдена."}
    return {"status": "ok", "done": bool(j.get("done")), "log": j.get("log", ""),
            "project_id": j.get("project_id", ""), "job_id": job_id}


@router.get("/run_active")
def run_active(project_id: str = "", _cur=_Depends(_get_cur)):
    bucket = _ubucket(_cur)
    best = _active_run_job(bucket, project_id)
    if best:
        return {"status": "ok", "job": {"job_id": best.get("job_id"), "log": best.get("log", ""), "done": False}}
    return {"status": "ok", "job": None}


class PostEditBody(BaseModel):
    account_id: str = ""
    post_id: str = ""
    text: str = ""


@router.post("/posts/update")
def update_post(body: PostEditBody, _cur=_Depends(_get_cur)):
    acc = _ubucket(_cur)
    db = SessionLocal()
    try:
        posts = _load(db, acc, "posting_posts", [], for_update=True) or []
        projects = _load(db, acc, "posting_projects", []) or []
        found = None
        for p in posts:
            if p.get("id") == body.post_id:
                found = p
                break
        if not found:
            return {"status": "not_found"}
        if found.get("status") != "draft" or found.get("ids"):
            return {"status": "locked_after_delivery",
                    "message": "После первой внешней доставки текст нельзя менять; сначала удалите публикацию"}
        found["text"] = body.text
        proj = next((p for p in projects if p.get("id") == found.get("project_id")), None) or {}
        try:
            import os, sys
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            import posting_runner as pr
            from app.services.social_owner_policy_v3 import render_public_text
            found["preview"] = render_public_text(
                body.text,
                proj,
                "tg" if proj.get("channel_tg") else "vk",
                fallback=pr._project_delivery_text,
            )
        except Exception:
            found["preview"] = body.text
        _save(db, acc, "posting_posts", posts)
    finally:
        db.close()
    return {"status": "ok", "preview": found.get("preview", "") if found else ""}


class PostScheduleBody(BaseModel):
    account_id: str = ""
    post_id: str = ""
    publish_at: str = ""


@router.post("/posts/schedule")
def schedule_post(body: PostScheduleBody, _cur=_Depends(_get_cur)):
    acc = _ubucket(_cur)
    when = (body.publish_at or "").replace("T", " ")[:16]
    try:
        datetime.strptime(when, "%Y-%m-%d %H:%M")
    except Exception:
        return {"status": "invalid_datetime", "message": "Укажите корректные дату и время публикации"}
    db = SessionLocal()
    try:
        posts = _load(db, acc, "posting_posts", [], for_update=True) or []
        found = None
        for p in posts:
            if p.get("id") == body.post_id:
                found = p
                break
        if not found:
            return {"status": "not_found"}
        if found.get("status") != "draft" or found.get("ids"):
            return {"status": "locked_after_delivery",
                    "message": "После первой внешней доставки расписание менять нельзя; сначала удалите публикацию"}
        found["publish_at"] = when
        _save(db, acc, "posting_posts", posts)
    finally:
        db.close()
    return {"status": "ok", "publish_at": when}


class VkResolveBody(BaseModel):
    value: str = ""


@router.post("/vk_resolve")
def vk_resolve(body: VkResolveBody, _cur=_Depends(_get_cur)):
    import os as _os, requests as _rq
    gid = _norm_vk(body.value)
    if not gid:
        return {"status": "error", "message": "группа не найдена — проверьте ссылку"}
    name = ""
    tok = _os.environ.get("VK_TOKEN", "")
    if tok:
        try:
            r = _dep_call("vk.api", _rq.get, "https://api.vk.com/method/groups.getById",
                        params={"group_id": abs(gid), "access_token": tok, "v": "5.199"}, timeout=20)
            gs = (r.json().get("response") or {}).get("groups") or []
            name = gs[0].get("name", "") if gs else ""
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=979)
    return {"status": "ok", "owner_id": gid, "name": name}
