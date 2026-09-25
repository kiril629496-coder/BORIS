"""API AI-фотостудии ЕЦО.

Деньги показываются ДО запуска: смета в рублях и предупреждение, если партия
дороже порога подтверждения. Себестоимость моделей, копейки резерва и
технические причины guard наружу не уходят — только продуктовые формулировки.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.db.session import get_db
from app.models.catalog_product import CatalogProduct
from app.models.photo_session import PhotoSession
from app.services import image_service as IMG
from app.services import jobs
from app.services import media_service as M
from app.services import photo_studio as PS
from app.services.action_log import ACTOR_USER, log_action

router = APIRouter(prefix="/api/photo", tags=["photo"])

CONFIRM_THRESHOLD_KOPEKS = 30000   # 300 ₽ — дороже требуется подтверждение


def _session(db, user, sid, write=False):
    s = db.query(PhotoSession).get(int(sid))
    if s is None:
        raise HTTPException(404, "Фотосессия не найдена")
    if s.owner_user_id not in M.owner_scopes(db, user):
        raise HTTPException(403, "Нет доступа к фотосессии")
    if write and s.account_id and s.account_id not in M.manage_accounts(db, user):
        raise HTTPException(403, "Нет прав на изменение")
    return s


@router.get("/presets")
def presets():
    return {"status": "ok",
            "shots": [{"code": c, "template": t, "about": d}
                      for c, t, d in PS.SHOT_PRESETS],
            "interiors": [{"code": k, "about": v} for k, v in PS.SCENE_PRESETS.items()]}


@router.get("/client_context")
def client_context(account_id: str, db: Session = Depends(get_db),
                   user=Depends(get_current_user)):
    """Read-only proof of the commercial context used by AI-фотостудия."""
    if account_id not in M.manage_accounts(db, user):
        raise HTTPException(403, "Нет прав на этот аккаунт")
    ctx = PS._account_commercial_context(db, account_id)
    company = ctx.get("company") or {}
    facts = ctx.get("confirmed_facts") or []
    return {"status": "ok", "account_id": account_id,
            "company": company,
            "confirmed_facts_count": len(facts),
            "context_ready": bool(company.get("company_niche") or facts),
            "adaptation": "account_scoped_commercial_dna"}


class SessionBody(BaseModel):
    account_id: str
    product_id: int = 0
    reference_media_id: int = 0
    preset: str = "sovremennyy"
    shots: list[str] = []
    mode: str = "preserve_product"
    prompt: str = ""
    batch_confirmed: bool = False


class BatchSessionBody(BaseModel):
    account_id: str
    prompt: str
    projects: int = 1
    photos_per_project: int = 10
    preset: str = "client_auto"


@router.post("/sessions/batch")
def create_batch_sessions(body: BatchSessionBody, db: Session = Depends(get_db),
                          user=Depends(get_current_user)):
    """Create multiple draft photo projects; no generation or paid job starts here."""
    if body.account_id not in M.manage_accounts(db, user):
        raise HTTPException(403, "Нет прав на этот аккаунт")
    prompt=(body.prompt or "").strip()
    if len(prompt) < 12:
        raise HTTPException(400, "Опишите проекты подробнее")
    projects=max(1,min(int(body.projects or 1),20))
    photos=max(2,min(int(body.photos_per_project or 10),10))
    codes=[x[0] for x in PS.SHOT_PRESETS if x[0] != "HERO_FRONT"][:photos-1]
    created=[]; total_est=0
    for i in range(projects):
        project_prompt=(f"{prompt}\n\nPROJECT {i+1} OF {projects}: create a distinct design concept for this project. "
                        "Within this project keep the exact same object, materials, geometry, colors and identifying details across every camera angle.")
        result=create_session(SessionBody(account_id=body.account_id,preset=body.preset,shots=codes,
                                          mode="free_concept",prompt=project_prompt,batch_confirmed=False),db,user)
        created.append({"project":i+1,"session_id":result["session_id"],"estimate_rub":result["estimate_rub"],"photos":len(codes)+1})
        total_est += float(result["estimate_rub"] or 0)
    return {"status":"ok","projects":created,"project_count":len(created),
            "photos_per_project":len(codes)+1,"total_photos":len(created)*(len(codes)+1),
            "estimate_rub":round(total_est,2),"generation_started":False,
            "message":"Проекты созданы как черновики. Генерация начнётся только после запуска мастер-кадра."}


@router.post("/session")
def create_session(body: SessionBody, db: Session = Depends(get_db),
                   user=Depends(get_current_user)):
    """Создаёт сессию и СРАЗУ показывает смету. Ничего платного не запускает."""
    if body.account_id not in M.manage_accounts(db, user):
        raise HTTPException(403, "Нет прав на этот аккаунт")
    owner = M.owner_of_account(db, body.account_id)
    if owner is None:
        raise HTTPException(400, "У аккаунта не задан владелец")
    free_prompt = body.mode == "free_concept"
    preserve_reference = body.mode == "preserve_reference"
    p = None
    reference = None
    prompt = (body.prompt or "").strip()
    if free_prompt:
        if len(prompt) < 12:
            raise HTTPException(400, "Опишите будущую сцену подробнее")
    elif preserve_reference:
        from app.models.media_asset import MediaAsset
        from app.services import media_storage as _mst
        reference = db.query(MediaAsset).get(int(body.reference_media_id or 0))
        if reference is None or reference.account_id != body.account_id or reference.owner_user_id != owner:
            raise HTTPException(404, "Исходная фотография не найдена в выбранном аккаунте")
        if not reference.storage_key or not _mst.exists(reference.storage_key):
            raise HTTPException(400, "Исходная фотография недоступна в хранилище")
        if len(prompt) < 4:
            prompt = "Сохранить объект с исходной фотографии и показать его с других ракурсов"
    else:
        p = db.query(CatalogProduct).get(int(body.product_id or 0))
        if p is None or p.owner_user_id != owner:
            raise HTTPException(404, "Товар не найден")
    codes = [c for c in (body.shots or []) if c] or ["HERO_45_LEFT", "DETAIL_FACADE"]
    total = len(codes) + 1                       # мастер + ракурсы
    model = IMG.PREMIUM_MODEL if free_prompt else IMG.MODEL
    est = IMG.estimate_kopeks(total, model=model)
    s = PhotoSession(owner_user_id=owner, account_id=body.account_id,
                     product_id=p.id if p is not None else None,
                     preset=body.preset, mode=body.mode,
                     shots_planned=total, est_kopeks=est, status="draft",
                     created_by=M._uid(user),
                     settings={"codes": codes, "prompt": prompt,
                               "reference_media_id": int(body.reference_media_id or 0),
                               "model": model, "quality": "medium"},
                     batch_confirmed_kopeks=est if body.batch_confirmed else None,
                     resume_policy="auto_daily" if body.batch_confirmed else "paused_ask")
    db.add(s)
    db.commit()
    return {"status": "ok", "session_id": s.id, "shots": codes,
            "estimate_rub": round(est / 100, 2),
            "needs_confirmation": est >= CONFIRM_THRESHOLD_KOPEKS,
            "message": "Будет создано %d изображений, ориентировочно %.0f ₽"
                       % (total, est / 100)}


@router.post("/session/{sid}/master")
def start_master(sid: int, db: Session = Depends(get_db),
                 user=Depends(get_current_user)):
    """Запускает ОДИН мастер-кадр. Остальные ракурсы — только после приёмки."""
    s = _session(db, user, sid, write=True)
    if s.status not in ("draft", "failed", "paused_limit"):
        raise HTTPException(400, "Мастер уже создавался, статус: %s" % s.status)
    job, _ = jobs.enqueue(db, "photo_session", {"session_id": s.id, "stage": "master"},
                          owner_user_id=s.owner_user_id, account_id=s.account_id,
                          fingerprint="photo:%s:master" % s.id)
    s.job_id = job.id
    s.status = "running"
    db.commit()
    log_action(account_id=s.account_id or "", action="Запустил AI-фотосессию",
               object_kind="photo_session", object_name="сессия %s" % s.id,
               actor=ACTOR_USER, user_id=M._uid(user), source="photo.start")
    return {"status": "ok", "job_id": job.id,
            "message": "Создаю главный кадр, это займёт около минуты"}


class ApproveBody(BaseModel):
    approved: bool = True
    codes: list[str] | None = None


@router.post("/session/{sid}/approve_master")
def approve_master(sid: int, body: ApproveBody, db: Session = Depends(get_db),
                   user=Depends(get_current_user)):
    """Человек принимает мастер — и только теперь стартуют остальные ракурсы."""
    s = _session(db, user, sid, write=True)
    if s.status != "master_review":
        raise HTTPException(400, "Мастер-кадр ещё не готов")
    if not body.approved:
        s.status = "draft"
        s.master_media_id = None
        db.commit()
        return {"status": "ok", "message": "Мастер отклонён, можно переснять"}
    codes = body.codes or (s.settings or {}).get("codes") or []
    job, _ = jobs.enqueue(db, "photo_session",
                          {"session_id": s.id, "stage": "derived", "codes": codes},
                          owner_user_id=s.owner_user_id, account_id=s.account_id,
                          fingerprint="photo:%s:derived" % s.id)
    s.job_id = job.id
    db.commit()
    log_action(account_id=s.account_id or "", action="Принял мастер-кадр",
               object_kind="photo_session", object_name="сессия %s" % s.id,
               after_val="ракурсов: %d" % len(codes), actor=ACTOR_USER,
               user_id=M._uid(user), source="photo.approve_master")
    return {"status": "ok", "job_id": job.id, "shots": codes,
            "message": "Собираю %d ракурсов" % len(codes)}


@router.get("/session/{sid}")
def get_session(sid: int, db: Session = Depends(get_db),
                user=Depends(get_current_user)):
    s = _session(db, user, sid)
    return {"status": "ok", "session": PS.session_public(db, s)}


@router.get("/sessions")
def list_sessions(account_id: str = "", product_id: int = 0, limit: int = 20,
                  db: Session = Depends(get_db), user=Depends(get_current_user)):
    scopes = M.owner_scopes(db, user)
    if not scopes:
        return {"status": "ok", "items": []}
    q = db.query(PhotoSession).filter(PhotoSession.owner_user_id.in_(list(scopes)))
    if account_id:
        q = q.filter(PhotoSession.account_id == account_id)
    if product_id:
        q = q.filter(PhotoSession.product_id == product_id)
    rows = q.order_by(PhotoSession.id.desc()).limit(min(int(limit or 20), 100)).all()
    return {"status": "ok", "items": [PS.session_public(db, s) for s in rows]}


@router.get("/metrics")
def photo_metrics(db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Метрики качества и экономики. Владельцу платформы — по всем."""
    owner = None if M._role(user) == "owner" else M._uid(user)
    return {"status": "ok", "metrics": PS.metrics(db, owner)}

@router.get('/campaign-series/{campaign_id}')
def campaign_photo_series(campaign_id:int, account_id:str, db:Session=Depends(get_db), user=Depends(get_current_user)):
    """Approved reference-photo generations for a campaign/account, grouped for campaign UI."""
    if account_id not in M.manage_accounts(db,user):
        raise HTTPException(403,'Нет прав на этот аккаунт')
    from app.models.photo_shot import PhotoShot
    from app.models.media_asset import MediaAsset
    rows=(db.query(PhotoSession).filter(PhotoSession.account_id==account_id,PhotoSession.mode=='preserve_reference')
          .order_by(PhotoSession.id.desc()).limit(60).all())
    items=[]; total_cost=0
    for s in rows:
        settings=s.settings or {}
        # Campaign-tagged sessions are preferred; legacy Vasily sessions are accepted
        # only when explicitly reference-based and completed.
        tagged=int(settings.get('campaign_id') or 0)==int(campaign_id)
        legacy_vasily=(int(campaign_id)==223 and account_id=='vasyailchenko_82650' and s.id>=20)
        if not (tagged or legacy_vasily): continue
        total_cost += int(s.actual_kopeks or 0)
        shots=db.query(PhotoShot).filter(PhotoShot.session_id==s.id,PhotoShot.status=='approved').order_by(PhotoShot.id).all()
        for sh in shots:
            a=db.query(MediaAsset).get(int(sh.media_id or 0)) if sh.media_id else None
            if not a: continue
            from app.services import media_storage as _st
            items.append({'session_id':s.id,'shot_id':sh.id,'code':sh.shot_code,'media_id':a.id,'url':_st.public_url(a.storage_key),'score':sh.consistency_score,'reference_media_id':int(settings.get('reference_media_id') or 0)})
    return {'status':'ok','campaign_id':campaign_id,'account_id':account_id,'approved':len(items),'cost_rub':round(total_cost/100,2),'items':items[:60]}
