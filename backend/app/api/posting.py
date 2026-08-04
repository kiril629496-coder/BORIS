from fastapi import Depends as _Depends
from app.api.auth import get_current_user as _get_cur
from fastapi import APIRouter
from pydantic import BaseModel
from typing import List, Optional
import json
from datetime import date

from app.db.session import SessionLocal
from app.models.storage import Storage

router = APIRouter(prefix="/api/posting", tags=["posting"])


def _get(db, account_id, key):
    return db.query(Storage).filter(Storage.account_id == account_id, Storage.key == key).first()

def _load(db, account_id, key, default=None):
    row = _get(db, account_id, key)
    if not row:
        return default
    try:
        return json.loads(row.value)
    except Exception:
        return default

def _save(db, account_id, key, value):
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
    except Exception:
        pass
    body.account_id = _ubucket(_cur)
    db = SessionLocal()
    try:
        sub = {"plan": "trial", "platforms": body.platforms,
               "paid_at": date.today().isoformat(), "period_days": 2,
               "posts_per_day": 3, "trial_total": 6, "trial_used": True, "amount_rub": 0}
        if body.project_id:
            projs = _load(db, body.account_id, "posting_projects", []) or []
            for p in projs:
                if p.get("id") == body.project_id:
                    if (p.get("subscription") or {}).get("trial_used"):
                        return {"status": "used", "message": "Бесплатный тест по этому проекту уже был использован"}
                    p["subscription"] = sub
                    _save(db, body.account_id, "posting_projects", projs)
                    return {"status": "ok", "message": "Бесплатный тест на 2 дня / 6 постов активирован для проекта"}
            return {"status": "not_found", "message": "Проект не найден"}
        existing = _load(db, body.account_id, "posting_subscription", None)
        if existing and existing.get("trial_used"):
            return {"status": "used", "message": "Бесплатный тест уже был использован"}
        _save(db, body.account_id, "posting_subscription", sub)
        return {"status": "ok", "message": "Бесплатный тест на 2 дня / 6 постов активирован"}
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
        today = date.today().isoformat()
        out = []
        for p in projs:
            cnt = _get(db, account_id, "posting_count:" + p.get("id", "") + ":" + today)
            used = 0
            if cnt:
                try: used = int(cnt.value)
                except Exception: used = 0
            sub = p.get("subscription") or {}
            per_day = int(sub.get("posts_per_day", 3)) if sub else 3
            out.append({**p, "active": True if _is_owner else _proj_active(p),
                        "used_today": used,
                        "left_today": None if _is_owner else (max(0, per_day - used) if sub else 0)})
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
        r = _rq.get("https://api.vk.com/method/groups.getById",
                    params={"group_id": name, "access_token": tok, "v": "5.199"}, timeout=20)
        gs = (r.json().get("response") or {}).get("groups") or []
        return -int(gs[0]["id"]) if gs else None
    except Exception as e:
        print("[vk_norm] ошибка:", str(e)[:150], flush=True)
        return None


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
                "auto_publish_hours": body.auto_publish_hours}
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
        posts = _load(db, body.account_id, "posting_posts", []) or []
        posts = [p for p in posts if p.get("id") != body.post_id]
        _save(db, body.account_id, "posting_posts", posts)
        return {"status": "ok"}
    finally:
        db.close()

@router.post("/posts/publish")
def publish_post(body: PostActionBody, _cur=_Depends(_get_cur)):
    body.account_id = _ubucket(_cur)
    db = SessionLocal()
    try:
        posts = _load(db, body.account_id, "posting_posts", []) or []
        post = next((p for p in posts if p.get("id") == body.post_id), None)
        projects = _load(db, body.account_id, "posting_projects", []) or []
    finally:
        db.close()
    if not post:
        return {"status": "not_found"}
    proj = next((p for p in projects if p.get("id") == post.get("project_id")), None) or {}
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    import posting_runner as pr
    banner = os.path.join(pr.BANNER_DIR, post["banner"]) if post.get("banner") else None
    platforms = proj.get("platforms", "both")
    text = post.get("text", "")
    results = {}
    if proj.get("channel_tg") and platforms in ("tg", "both"):
        results["tg"] = pr._send_telegram(proj["channel_tg"], pr._with_tags(pr._with_contact(text, proj.get("contact_tg", "")), proj.get("hashtags")), banner)
    if proj.get("vk_owner_id") and platforms in ("vk", "both"):
        results["vk"] = pr._send_vk(int(proj["vk_owner_id"]), pr._with_tags(pr._with_contact(text, proj.get("contact_vk", "")), proj.get("hashtags")), banner)
    ok = bool(results) and all(r.get("ok") for r in results.values())  # черновик снимаем только если ушло ВЕЗДЕ
    if ok:
        db = SessionLocal()
        try:
            posts = _load(db, body.account_id, "posting_posts", []) or []
            posts = [p for p in posts if p.get("id") != body.post_id]
            _save(db, body.account_id, "posting_posts", posts)
        finally:
            db.close()
    return {"status": "ok" if ok else "fail",
            "results": {k: bool(v.get("ok")) for k, v in results.items()},
            "errors": {k: str(v.get("error"))[:200] for k, v in results.items() if not v.get("ok")}}

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


class RunBody(BaseModel):
    account_id: str = ""
    project_id: str = ""


_RUN_JOBS = {}


@router.post("/run")
def run_project(body: RunBody, _cur=_Depends(_get_cur)):
    import io, contextlib, os, sys, threading, uuid
    bucket = _ubucket(_cur)
    is_owner = getattr(_cur, "role", None) == "owner"
    job_id = uuid.uuid4().hex[:12]
    _RUN_JOBS[job_id] = {"done": False, "log": "Запускаю генерацию…", "project_id": body.project_id}
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
        except Exception as e:
            _RUN_JOBS[job_id] = {"done": True,
                                 "log": (buf.getvalue()[-2500:] + "\nОШИБКА: " + str(e)[:300]),
                                 "project_id": body.project_id}

    threading.Thread(target=_work, daemon=True).start()
    return {"status": "started", "job_id": job_id}


@router.get("/run_status")
def run_status(job_id: str, _cur=_Depends(_get_cur)):
    j = _RUN_JOBS.get(job_id)
    if not j:
        return {"status": "unknown", "done": True, "log": "Задача не найдена — сервер мог перезапуститься."}
    return {"status": "ok", "done": j.get("done"), "log": j.get("log", ""), "project_id": j.get("project_id", "")}


@router.get("/run_active")
def run_active(project_id: str = "", _cur=_Depends(_get_cur)):
    best = None
    for jid, j in _RUN_JOBS.items():
        if j.get("project_id") == project_id and not j.get("done"):
            best = {"job_id": jid, "log": j.get("log", ""), "done": False}
    return {"status": "ok", "job": best}


class PostEditBody(BaseModel):
    account_id: str = ""
    post_id: str = ""
    text: str = ""


@router.post("/posts/update")
def update_post(body: PostEditBody, _cur=_Depends(_get_cur)):
    acc = _ubucket(_cur)
    db = SessionLocal()
    try:
        posts = _load(db, acc, "posting_posts", []) or []
        found = False
        for p in posts:
            if p.get("id") == body.post_id:
                p["text"] = body.text
                p["preview"] = body.text
                found = True
                break
        if not found:
            return {"status": "not_found"}
        _save(db, acc, "posting_posts", posts)
    finally:
        db.close()
    return {"status": "ok"}


class PostScheduleBody(BaseModel):
    account_id: str = ""
    post_id: str = ""
    publish_at: str = ""


@router.post("/posts/schedule")
def schedule_post(body: PostScheduleBody, _cur=_Depends(_get_cur)):
    acc = _ubucket(_cur)
    when = (body.publish_at or "").replace("T", " ")[:16]
    db = SessionLocal()
    try:
        posts = _load(db, acc, "posting_posts", []) or []
        found = False
        for p in posts:
            if p.get("id") == body.post_id:
                p["publish_at"] = when
                found = True
                break
        if not found:
            return {"status": "not_found"}
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
            r = _rq.get("https://api.vk.com/method/groups.getById",
                        params={"group_id": abs(gid), "access_token": tok, "v": "5.199"}, timeout=20)
            gs = (r.json().get("response") or {}).get("groups") or []
            name = gs[0].get("name", "") if gs else ""
        except Exception:
            pass
    return {"status": "ok", "owner_id": gid, "name": name}
