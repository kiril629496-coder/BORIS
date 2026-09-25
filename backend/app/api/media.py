"""API Медиатеки ЕЦО.

Роутер подключается БЕЗ общего _protected: check_account_access требует
одиночный account_id, а медиатека owner-level и охватывает несколько
аккаунтов сразу (так же сделаны team_router и action_log_router). Права от
этого не ослабляются — каждая ручка спрашивает media_service.
"""
import json
import os

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.db.session import get_db
from app.models.background_job import BackgroundJob
from app.models.media_asset import MediaAsset
from app.models.media_folder import MediaFolder
from app.services import jobs
from app.services import media_service as M
from app.services import media_storage as st
from app.services.action_log import ACTOR_USER, log_action
from app.services.exception_observability import observe_suppressed

router = APIRouter(prefix="/api/media", tags=["media"])

MAX_UPLOAD_MB = 25
MAX_FILES = 30


def _asset_or_404(db, media_id):
    a = db.query(MediaAsset).get(int(media_id))
    if a is None:
        raise HTTPException(404, "Файл не найден")
    return a


def _read(db, user, media_id):
    a = _asset_or_404(db, media_id)
    if not M.can_read_asset(db, user, a):
        raise HTTPException(403, "Нет доступа к этому файлу")
    return a


def _write(db, user, media_id):
    a = _asset_or_404(db, media_id)
    if not M.can_write_asset(db, user, a):
        raise HTTPException(403, "Нет прав на изменение этого файла")
    return a


@router.get("/folders")
def folders(account_id: str = "", db: Session = Depends(get_db),
            user=Depends(get_current_user)):
    if not account_id:
        raise HTTPException(400, "Для медиатеки нужно выбрать аккаунт")
    if account_id not in M.allowed_accounts(db, user):
        raise HTTPException(403, "Нет доступа к этому аккаунту")
    return {"status": "ok", "folders": M.folder_tree(db, user, account_id)}


class FolderBody(BaseModel):
    name: str
    account_id: str = ""
    parent_id: int | None = None


@router.post("/folder")
def folder_create(body: FolderBody, db: Session = Depends(get_db),
                  user=Depends(get_current_user)):
    owner = M.owner_of_account(db, body.account_id) or M._uid(user)
    if owner is None:
        raise HTTPException(400, "У аккаунта не задан владелец")
    if body.account_id and body.account_id not in M.manage_accounts(db, user):
        raise HTTPException(403, "Нет прав на этот аккаунт")
    f = M.ensure_folder(db, owner, body.account_id or None, body.name,
                        body.parent_id, created_by=M._uid(user))
    db.commit()
    log_action(account_id=body.account_id or "", action="Создал папку в медиатеке",
               object_kind="media_folder", object_name=f.name,
               actor=ACTOR_USER, user_id=M._uid(user), source="media.folder_create")
    return {"status": "ok", "id": f.id, "path": f.path}


class FolderEditBody(BaseModel):
    folder_id: int
    name: str = ""
    parent_id: int | None = None


@router.post("/folder/rename")
def folder_rename(body: FolderEditBody, db: Session = Depends(get_db),
                  user=Depends(get_current_user)):
    f = db.query(MediaFolder).get(int(body.folder_id))
    if f is None:
        raise HTTPException(404, "Папка не найдена")
    if f.owner_user_id not in M.owner_scopes(db, user):
        raise HTTPException(403, "Нет доступа к папке")
    old = f.name
    M.rename_folder(db, f, body.name)
    db.commit()
    log_action(account_id=f.account_id or "", action="Переименовал папку",
               object_kind="media_folder", object_name=f.name,
               before_val=old, after_val=f.name,
               actor=ACTOR_USER, user_id=M._uid(user), source="media.folder_rename")
    return {"status": "ok", "path": f.path}


@router.post("/folder/delete")
def folder_delete(body: FolderEditBody, db: Session = Depends(get_db),
                  user=Depends(get_current_user)):
    from datetime import datetime, timezone as _tz
    f = db.query(MediaFolder).get(int(body.folder_id))
    if f is None:
        raise HTTPException(404, "Папка не найдена")
    if f.owner_user_id not in M.owner_scopes(db, user):
        raise HTTPException(403, "Нет доступа к папке")
    inside = db.query(MediaAsset).filter(MediaAsset.folder_id == f.id,
                                         MediaAsset.deleted_at.is_(None)).count()
    if inside:
        raise HTTPException(400, "В папке %d файлов — сначала перенесите их" % inside)
    f.deleted_at = datetime.now(_tz.utc)
    db.commit()
    return {"status": "ok"}


@router.get("/list")
def list_media(account_id: str = "", folder_id: int = 0, media_type: str = "",
               source: str = "", status: str = "", usage: str = "", q: str = "",
               favorite: int = 0, trashed: int = 0, limit: int = 60,
               offset: int = 0, sort: str = "new",
               db: Session = Depends(get_db), user=Depends(get_current_user)):
    if not account_id:
        raise HTTPException(400, "Для медиатеки нужно выбрать аккаунт")
    if account_id not in M.allowed_accounts(db, user):
        raise HTTPException(403, "Нет доступа к этому аккаунту")
    res = M.list_assets(db, user, account_id=account_id,
                        folder_id=folder_id or None,
                        media_type=media_type or None, source=source or None,
                        status=status or None, usage=usage or None, q=q or None,
                        favorite=bool(favorite), trashed=bool(trashed),
                        limit=limit, offset=offset, sort=sort)
    res["status"] = "ok"
    return res


@router.get("/stats")
def stats(account_id: str = "", db: Session = Depends(get_db),
          user=Depends(get_current_user)):
    """Счётчики только для явно выбранного аккаунта; owner-wide смешивание запрещено."""
    from sqlalchemy import func as _f
    if not account_id:
        raise HTTPException(400, "Для медиатеки нужно выбрать аккаунт")
    if account_id not in M.allowed_accounts(db, user):
        raise HTTPException(403, "Нет доступа к этому аккаунту")
    scopes = M.owner_scopes(db, user)
    if not scopes:
        return {"status": "ok", "by_type": {}, "mine": 0, "stock": 0, "trash": 0,
                "missing": 0, "total": 0, "by_source": {}}
    qq = db.query(MediaAsset.media_type, _f.count(MediaAsset.id)).filter(
        MediaAsset.owner_user_id.in_(list(scopes)), MediaAsset.deleted_at.is_(None))
    if account_id:
        qq = qq.filter(MediaAsset.account_id == account_id)
    by_type = {t: int(c) for t, c in qq.group_by(MediaAsset.media_type).all()}

    base = db.query(MediaAsset).filter(MediaAsset.owner_user_id.in_(list(scopes)))
    if account_id:
        base = base.filter(MediaAsset.account_id == account_id)

    srcq = db.query(MediaAsset.source_type, _f.count(MediaAsset.id)).filter(
        MediaAsset.owner_user_id.in_(list(scopes)), MediaAsset.deleted_at.is_(None))
    if account_id:
        srcq = srcq.filter(MediaAsset.account_id == account_id)
    by_source = {str(t or "unknown"): int(c) for t, c in srcq.group_by(MediaAsset.source_type).all()}
    ai_count = sum(by_source.get(k, 0) for k in ("ai", "ai_generated", "generated", "photo_studio"))
    by_source["ai"] = ai_count
    mine = sum(c for t, c in by_type.items() if t != "stock")
    return {
        "status": "ok", "by_type": by_type,
        "mine": mine, "total": mine, "by_source": by_source,
        "stock": by_type.get("stock", 0),
        "trash": base.filter(MediaAsset.deleted_at.isnot(None)).count(),
        "missing": base.filter(MediaAsset.deleted_at.is_(None),
                               MediaAsset.file_state == "missing").count(),
    }


@router.get("/{media_id}")
def get_one(media_id: int, db: Session = Depends(get_db),
            user=Depends(get_current_user)):
    a = _read(db, user, media_id)
    data = M.asset_public(a)
    data["usages"] = M.usages(db, a.id)
    return {"status": "ok", "media": data}


@router.get("/{media_id}/usages")
def get_usages(media_id: int, db: Session = Depends(get_db),
               user=Depends(get_current_user)):
    a = _read(db, user, media_id)
    return {"status": "ok", "usages": M.usages(db, a.id)}


@router.post("/upload")
async def upload(account_id: str = Form(...), folder_id: int = Form(0),
                 folder_name: str = Form("общая"),
                 files: list[UploadFile] = File(...),
                 db: Session = Depends(get_db), user=Depends(get_current_user)):
    if account_id not in M.manage_accounts(db, user):
        raise HTTPException(403, "Нет прав на этот аккаунт")
    owner = M.owner_of_account(db, account_id)
    if owner is None:
        raise HTTPException(400, "У аккаунта не задан владелец — загрузка недоступна")
    if len(files) > MAX_FILES:
        raise HTTPException(400, "За раз можно загрузить не более %d файлов" % MAX_FILES)

    if folder_id:
        folder = db.query(MediaFolder).get(int(folder_id))
        if folder is None or folder.owner_user_id != owner or folder.account_id != account_id:
            raise HTTPException(404, "Папка не найдена в выбранном аккаунте")
    else:
        folder = M.ensure_folder(db, owner, account_id, folder_name or "общая",
                                 created_by=M._uid(user))
    rel = folder.path.strip("/") or "общая"

    created, dups, errors = [], [], []
    for f in files:
        data = await f.read()
        if not data:
            errors.append({"name": f.filename, "error": "пустой файл"})
            continue
        if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
            errors.append({"name": f.filename, "error": "больше %d МБ" % MAX_UPLOAD_MB})
            continue
        sha = st.sha256_bytes(data)
        twin = M.find_duplicate(db, owner, sha)
        if twin is not None and twin.account_id == account_id:
            dups.append({"name": f.filename or "file.jpg", "duplicate_of": twin.id})
            continue
        key = st.free_key(account_id, rel, f.filename or "file.jpg")
        st.write_bytes(key, data)
        a, _ = M.register_asset(db, owner, account_id, key,
                                os.path.basename(key), media_type="real_photo",
                                source_type="upload", folder_id=folder.id,
                                sha256=sha, file_size=len(data),
                                created_by=M._uid(user), status="approved")
        created.append(M.asset_public(a))
    jobs.enqueue(db, "thumbnail_sweep", {"owner_user_id": owner},
                 owner_user_id=owner, account_id=account_id,
                 fingerprint="thumbs:%s" % owner)
    db.commit()
    log_action(account_id=account_id, action="Загрузил файлы в медиатеку",
               object_kind="media", object_name="%d шт." % len(created),
               actor=ACTOR_USER, user_id=M._uid(user), source="media.upload")
    return {"status": "ok", "created": created, "duplicates": dups, "errors": errors}


class IdsBody(BaseModel):
    ids: list[int]
    folder_id: int | None = None
    tags: list[str] | None = None
    favorite: bool | None = None
    status: str | None = None


def _bulk(db, user, ids, fn):
    ok, denied = 0, 0
    for i in ids[:500]:
        a = db.query(MediaAsset).get(int(i))
        if a is None:
            continue
        if not M.can_write_asset(db, user, a):
            denied += 1
            continue
        fn(a)
        ok += 1
    db.commit()
    return {"status": "ok", "updated": ok, "denied": denied}


@router.post("/move")
def move(body: IdsBody, db: Session = Depends(get_db),
         user=Depends(get_current_user)):
    folder = db.query(MediaFolder).get(int(body.folder_id or 0))
    if folder is None:
        raise HTTPException(404, "Папка не найдена")
    if folder.owner_user_id not in M.owner_scopes(db, user):
        raise HTTPException(403, "Нет доступа к папке")
    ok, denied = 0, 0
    for i in body.ids[:500]:
        a = db.query(MediaAsset).get(int(i))
        if a is None:
            continue
        if not M.can_write_asset(db, user, a) or a.account_id != folder.account_id:
            denied += 1
            continue
        a.folder_id = folder.id
        ok += 1
    db.commit()
    return {"status": "ok", "updated": ok, "denied": denied}


@router.post("/tag")
def tag(body: IdsBody, db: Session = Depends(get_db),
        user=Depends(get_current_user)):
    tags = [str(t).strip() for t in (body.tags or []) if str(t).strip()][:20]

    def apply(a):
        cur = list(a.tags or [])
        a.tags = sorted(set(cur) | set(tags))
    return _bulk(db, user, body.ids, apply)


@router.post("/favorite")
def favorite(body: IdsBody, db: Session = Depends(get_db),
             user=Depends(get_current_user)):
    val = bool(body.favorite)
    return _bulk(db, user, body.ids, lambda a: setattr(a, "is_favorite", val))


@router.post("/status")
def set_status(body: IdsBody, db: Session = Depends(get_db),
               user=Depends(get_current_user)):
    from app.services.media_service import MEDIA_STATUSES
    if body.status not in MEDIA_STATUSES:
        raise HTTPException(400, "Недопустимый статус")
    return _bulk(db, user, body.ids, lambda a: setattr(a, "status", body.status))


@router.post("/delete")
def delete(body: IdsBody, db: Session = Depends(get_db),
           user=Depends(get_current_user)):
    return _bulk(db, user, body.ids, lambda a: M.soft_delete(db, a, user))


@router.post("/restore")
def restore(body: IdsBody, db: Session = Depends(get_db),
            user=Depends(get_current_user)):
    return _bulk(db, user, body.ids, lambda a: M.restore(db, a, user))


@router.post("/purge")
def purge(body: IdsBody, db: Session = Depends(get_db),
          user=Depends(get_current_user)):
    """Окончательное удаление записи. Файл на диске НЕ трогаем: на него могут
    ссылаться уже опубликованные объявления."""
    if M._role(user) != "owner":
        raise HTTPException(403, "Окончательное удаление доступно владельцу")
    removed, blocked = 0, []
    for i in body.ids[:200]:
        a = db.query(MediaAsset).get(int(i))
        if a is None or a.deleted_at is None:
            continue
        used = M.usages(db, a.id)
        if used:
            blocked.append({"id": a.id, "filename": a.filename, "used_in": len(used)})
            continue
        db.delete(a)
        removed += 1
    db.commit()
    return {"status": "ok", "removed": removed, "blocked": blocked}


class LinkBody(BaseModel):
    media_id: int
    entity_type: str
    entity_id: str
    role: str = "gallery"
    position: int = 0


@router.post("/link")
def link(body: LinkBody, db: Session = Depends(get_db),
         user=Depends(get_current_user)):
    from app.services.media_service import LINK_ENTITIES, LINK_ROLES
    if body.entity_type not in LINK_ENTITIES or body.role not in LINK_ROLES:
        raise HTTPException(400, "Недопустимый тип связи")
    a = _write(db, user, body.media_id)
    if a.file_state != "present":
        raise HTTPException(400, "Файл недоступен в хранилище")
    M.link(db, a, body.entity_type, body.entity_id, body.role, body.position,
           created_by=M._uid(user))
    db.commit()
    log_action(account_id=a.account_id or "",
               action="Добавил фото к объявлению" if body.entity_type == "campaign_item"
               else "Связал файл с объектом",
               object_kind=body.entity_type, object_name=a.filename,
               after_val=body.role, actor=ACTOR_USER, user_id=M._uid(user),
               source="media.link")
    return {"status": "ok"}


@router.post("/unlink")
def unlink(body: LinkBody, db: Session = Depends(get_db),
           user=Depends(get_current_user)):
    from app.models.media_link import MediaLink
    a = _write(db, user, body.media_id)
    db.query(MediaLink).filter(
        MediaLink.media_id == a.id, MediaLink.entity_type == body.entity_type,
        MediaLink.entity_id == str(body.entity_id)).delete(synchronize_session=False)
    db.commit()
    return {"status": "ok"}


@router.get("/job/{job_id}")
def job_status(job_id: int, db: Session = Depends(get_db),
               user=Depends(get_current_user)):
    j = db.query(BackgroundJob).get(int(job_id))
    if j is None:
        raise HTTPException(404, "Задача не найдена")
    if M._role(user) != "owner" and j.owner_user_id != M._uid(user):
        raise HTTPException(403, "Нет доступа к задаче")
    return {"status": "ok", "job": jobs.job_public(j)}


# --- Строгий тематический сборщик бесплатных стоков -----------------------
class StockCollectBody(BaseModel):
    account_id: str
    topic: str
    count: int = 12
    # Optional caller context is audit-only. It must never affect the paid
    # semantic intent/cache key; it only makes api_usage attributable.
    provenance: dict = Field(default_factory=dict)


_STOCK_TOPIC_SEARCH = {
    "кухни": "modern fitted kitchen interior cabinetry",
    "кухня": "modern fitted kitchen interior cabinetry",
    "шкафы": "modern built in wardrobe closet cabinetry",
    "шкаф": "modern built in wardrobe closet cabinetry",
    "гардеробные": "walk in wardrobe dressing room closet",
    "гардеробная": "walk in wardrobe dressing room closet",
    "прихожие": "modern entryway hallway storage furniture",
    "прихожая": "modern entryway hallway storage furniture",
    "ванные": "modern bathroom vanity storage furniture",
    "мебель для ванной": "modern bathroom vanity storage furniture",
    "кровати": "modern bedroom bed furniture",
    "кровать": "modern bedroom bed furniture",
    "матрасы": "modern mattress bedroom product",
    "матрас": "modern mattress bedroom product",
    "диваны": "modern sofa living room furniture",
    "диван": "modern sofa living room furniture",
}


def _stock_topic_query(topic: str) -> str:
    key = " ".join((topic or "").strip().lower().replace("ё","е").split())
    # Pexels search works substantially better in English for narrow construction
    # products. Keep this semantic routing generic so every glass-shower direction
    # gets fixed-panel / walk-in references instead of closed shower cabins.
    if "душ" in key and "перегород" in key and ("стекл" in key or "огражден" in key):
        return "frameless walk in shower fixed glass panel partition bathroom"
    if "межкомнат" in key and "перегород" in key and "стекл" in key:
        return "modern interior glass partition wall frameless"
    if "офис" in key and "перегород" in key and "стекл" in key:
        return "modern office glass partition wall"
    return _STOCK_TOPIC_SEARCH.get(key, key)


def _strict_stock_vision(topic: str, candidates: list[dict], account_id: str | None = None, provenance: dict | None = None) -> list[dict]:
    """Fail-closed visual QA: no OpenAI verdict -> no image enters the folder."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY не настроен")
    content = [{"type": "text", "text": (
        "You are a strict commercial stock-photo curator. TARGET FOLDER: " + topic + ". "
        "For EACH image decide whether the dominant visible subject clearly belongs in this exact furniture/product folder. "
        "For glass shower partition targets, prefer a clearly visible fixed/walk-in glass panel or open shower partition. Reject a closed shower cabin or an image dominated by hinged/sliding doors when the target says partition without doors. "
        "Reject related rooms with the wrong product, generic interiors, ambiguous scenes, people-only images, details where the target is not obvious, "
        "and any image that could reasonably be filed under another furniture category. Be conservative. "
        "Return ONLY JSON array in the same order: [{\"index\":1,\"match\":true,\"confidence\":0.0,\"subject\":\"short\",\"reason\":\"short\"}]. "
        "A match must have confidence >=0.90."
    )}]
    for i, c in enumerate(candidates, 1):
        content.append({"type": "text", "text": "IMAGE %d; provider metadata: %s" % (i, str(c.get("alt") or "")[:300])})
        # Pexels candidates already have a public HTTPS image URL. Passing the
        # URL directly keeps low-detail Vision semantic QA pixel-based without
        # embedding megabytes of base64 into the request payload/token accounting.
        image_url = str(c.get("image") or "").strip()
        if not image_url.startswith(("https://", "http://")):
            raise RuntimeError("Стоковое изображение не имеет публичного URL для Vision QA")
        content.append({"type": "image_url", "image_url": {"url": image_url, "detail": "low"}})
    # Canonical paid vision contour: one deterministic intent, durable cache,
    # cross-process advisory lock, ai_budget reserve/allow/commit/release,
    # account burst/daily caps, exact provider request ID and ambiguous-success
    # handling. Stock collection must never bypass this guard with a raw OpenAI POST.
    from app.services.media_semantic_validator import _vision_chat_json
    verdicts = _vision_chat_json(
        str(account_id or ""),
        "stock_semantic_filter",
        content,
        max_tokens=1400,
        timeout=120,
        provenance={"source":"media_library_stock_collect","topic":str(topic or "")[:160],
                    **{str(k):v for k,v in (provenance or {}).items()
                       if str(k) in {"campaign_id","item_id","job_id","source"}}},
    )
    if not isinstance(verdicts, list) or len(verdicts) != len(candidates):
        raise RuntimeError("Некорректный ответ визуальной проверки")
    return verdicts


@router.post("/stock/collect")
def collect_stock(body: StockCollectBody, db: Session = Depends(get_db),
                  user=Depends(get_current_user)):
    """Pexels discovery -> strict Vision QA -> named Media folder -> thumbnails."""
    import hashlib
    import httpx
    from app.services import thumbnails

    account_id = (body.account_id or "").strip()
    topic = " ".join((body.topic or "").strip().split())
    wanted = max(1, min(int(body.count or 12), 30))
    if not account_id or account_id not in M.manage_accounts(db, user):
        raise HTTPException(403, "Нет прав на этот аккаунт")
    if len(topic) < 2:
        raise HTTPException(422, "Укажите товарную группу")
    owner = M.owner_of_account(db, account_id) or M._uid(user)
    if owner is None:
        raise HTTPException(400, "У аккаунта не задан владелец")
    actor_uid = M._uid(user)
    # Permission/owner lookups above start an ORM transaction. End it before
    # Pexels download + OpenAI Vision; those network stages can take minutes
    # and must never count as PostgreSQL idle-in-transaction time.
    db.commit()
    pexels_key = os.environ.get("PEXELS_API_KEY")
    if not pexels_key:
        raise HTTPException(503, "Pexels не настроен")

    search_query = _stock_topic_query(topic)
    candidate_limit = min(80, max(wanted * 4, 24))
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        resp = client.get("https://api.pexels.com/v1/search",
                          headers={"Authorization": pexels_key},
                          params={"query": search_query, "per_page": candidate_limit, "orientation": "landscape"})
        if resp.status_code != 200:
            raise HTTPException(502, "Pexels временно недоступен")
        photos = resp.json().get("photos") or []
        candidates = []
        for p in photos:
            if len(candidates) >= candidate_limit:
                break
            url = (p.get("src") or {}).get("large") or (p.get("src") or {}).get("original")
            if not url:
                continue
            try:
                rr = client.get(url, timeout=30)
                if rr.status_code != 200 or not rr.content or len(rr.content) > 12 * 1024 * 1024:
                    continue
                candidates.append({"id": p.get("id"), "bytes": rr.content,
                                   "alt": p.get("alt") or "", "page": p.get("url") or "", "image": url})
            except Exception:
                continue

    accepted = []
    rejected = 0
    # Batches keep the semantic gate cheap while still checking the pixels, not filenames.
    for start in range(0, len(candidates), 8):
        batch = candidates[start:start + 8]
        if not batch or len(accepted) >= wanted:
            break
        try:
            verdicts = _strict_stock_vision(topic, batch, account_id=account_id, provenance=body.provenance)
        except Exception as e:
            raise HTTPException(502, "Визуальная проверка стоков не прошла: " + str(e)[:140])
        for c, v in zip(batch, verdicts):
            conf = float(v.get("confidence") or 0)
            if bool(v.get("match")) and conf >= 0.90:
                c["verdict"] = v
                accepted.append(c)
                if len(accepted) >= wanted:
                    break
            else:
                rejected += 1

    folder = M.ensure_folder(db, owner, account_id, topic, created_by=actor_uid, kind="stock_topic")
    created = []
    duplicates = 0
    for c in accepted[:wanted]:
        sha = hashlib.sha256(c["bytes"]).hexdigest()
        twin = M.find_duplicate(db, owner, sha)
        if twin:
            duplicates += 1
            continue
        fname = "pexels_%s.jpg" % (c.get("id") or sha[:12])
        key = "%s/stock/%s/%s" % (account_id, st.safe_name(topic), fname)
        try:
            st.write_bytes(key, c["bytes"])
        except FileExistsError:
            continue
        v = c["verdict"]
        tags = ["stock_topic:" + topic, "semantic_reviewed",
                "semantic_confidence:%.2f" % float(v.get("confidence") or 0),
                "semantic_subject:" + str(v.get("subject") or "")[:120]]
        asset, _ = M.register_asset(
            db, owner, account_id, key, fname, media_type="stock", source_type="stock",
            folder_id=folder.id, sha256=sha, file_size=len(c["bytes"]), created_by=actor_uid,
            source_provider="pexels", source_ref=c.get("page"),
            license_json={"provider": "Pexels", "source_url": c.get("page")}, status="approved")
        asset.tags = tags
        try:
            tk, pk, w, h, ph = thumbnails.build(key)
            asset.thumb_key, asset.preview_key, asset.width, asset.height, asset.phash = tk, pk, w, h, ph
            duplicate_keeper = M.merge_phash_duplicate(db, asset)
            if duplicate_keeper is not None:
                duplicates += 1
                continue
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=608)
        created.append(M.asset_public(asset))
    db.commit()
    log_action(account_id=account_id, action="Собрал тематические стоковые фото",
               object_kind="media_folder", object_name=topic,
               after_val="принято %d; отклонено %d; дублей %d" % (len(created), rejected, duplicates),
               actor=ACTOR_USER, user_id=actor_uid, source="media.stock.collect")
    return {"status": "ok", "topic": topic, "folder_id": folder.id,
            "search_query": search_query, "requested": wanted, "accepted": len(created),
            "rejected": rejected, "duplicates": duplicates, "items": created,
            "policy": "strict_vision_fail_closed_0.90"}
