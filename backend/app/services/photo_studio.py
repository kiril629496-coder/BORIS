"""AI-фотостудия ЕЦО: DNA, промпты, master -> derived, валидатор, деньги.

Порядок из ТЗ соблюдён буквально: REFERENCE -> Furniture DNA -> Scene DNA ->
MASTER -> подтверждение человеком -> derived shots. Неудачная сцена стоит один
кадр, а не десять.

Согласованность серии держится не на удаче: каждый derived-кадр уходит в
/v1/images/edits С МАСТЕРОМ как reference, повторяет неизменяемые признаки
изделия и меняет ровно один параметр — положение камеры или деталь.

⛔ Генеративная модель не CAD-рендер: математическую идентичность мы не
обещаем, а максимизируем consistency и честно показываем оценку.
"""
import json
import os
import time
import uuid

from app.models.dna_profile import DnaProfile
from app.models.photo_session import PhotoSession
from app.models.photo_shot import PhotoShot
from app.models.prompt_template import PromptTemplate
from app.services import image_service as IMG
from app.services import media_service as M
from app.services import media_storage as st
from app.services.action_log import ACTOR_BORIS, ACTOR_USER, log_action

SHOT_PRESETS = [
    # Universal marketplace / vehicle angles. Furniture presets remain below.
    ("VEHICLE_FRONT_3Q", "furniture_angle", "front three-quarter exterior view of the exact same vehicle"),
    ("VEHICLE_REAR_3Q", "furniture_angle", "rear three-quarter exterior view of the exact same vehicle"),
    ("VEHICLE_SIDE", "furniture_angle", "clean full side profile of the exact same vehicle"),
    ("VEHICLE_FRONT", "furniture_angle", "straight front exterior view of the exact same vehicle"),
    ("VEHICLE_REAR", "furniture_angle", "straight rear exterior view of the exact same vehicle"),
    ("VEHICLE_INTERIOR", "furniture_detail", "driver cabin and dashboard of the exact same vehicle"),
    ("VEHICLE_REAR_SEATS", "furniture_detail", "rear passenger seats of the exact same vehicle"),
    ("VEHICLE_WHEEL", "furniture_detail", "close detail of the exact same vehicle wheel and body paint"),
    ("VEHICLE_HEADLIGHT", "furniture_detail", "close detail of the exact same vehicle headlight and front body"),
    ("VEHICLE_TRUNK", "furniture_detail", "open trunk view of the exact same vehicle without inventing cargo"),
    ("HERO_FRONT", "furniture_master", "главный продающий кадр от входа"),
    ("HERO_45_LEFT", "furniture_angle", "camera moved 45 degrees to the left"),
    ("HERO_45_RIGHT", "furniture_angle", "camera moved 45 degrees to the right"),
    ("FRONT", "furniture_angle", "strict frontal camera position"),
    ("WIDE", "furniture_angle", "wider shot showing the whole room"),
    ("DETAIL_FACADE", "furniture_detail", "facade surface and its texture"),
    ("DETAIL_HARDWARE", "furniture_detail", "handles and hardware"),
    ("DETAIL_COUNTERTOP", "furniture_detail", "countertop edge and material"),
    ("OPEN_STORAGE", "furniture_angle", "one section open, interior filling visible"),
    ("LIFESTYLE", "furniture_lifestyle", "warm living scene, no people"),
]
SCENE_PRESETS = {
    "client_auto": "automatically choose a commercially appropriate real-world environment for the selected client's business, assortment and positioning; use the CLIENT COMMERCIAL DNA below as the authoritative guide",
    "sovremennyy": "modern light interior, white walls, light oak floor, daylight from a large window on the left",
    "minimalizm": "minimalist interior, plain white walls, grey micro-cement floor, diffused even daylight",
    "premium": "premium interior, deep warm tones, marble accents, brass details, soft directional light",
    "neoklassika": "neoclassical interior, panelled walls, herringbone parquet, tall window with light curtains",
    "novostroyka": "newly built flat, plain white walls, grey laminate floor, bright even daylight",
    "loft": "loft interior, exposed brick wall, concrete ceiling, black metal window frames",
    "semeynyy": "warm family interior, beige walls, oak floor, soft evening light",
}


def _shot_by_code(code):
    for c, tpl, definition in SHOT_PRESETS:
        if c == code:
            return c, tpl, definition
    return code, "furniture_angle", code.lower().replace("_", " ")


def build_furniture_dna(db, session, product):
    """Паспорт изделия из ФАКТОВ каталога. Ничего не выдумываем.

    Чего в карточке нет, того в DNA не появляется: пустая характеристика хуже,
    чем её отсутствие, потому что уедет в изображение как фантазия модели.
    """
    attrs = dict(product.attrs or {})
    payload = {"name": product.name, "category": product.category or "",
               "type": product.kind, "attrs": attrs}
    immutable = []
    for key, val in attrs.items():
        if val in (None, "", []):
            continue
        immutable.append("%s: %s" % (key, val))
    if product.description:
        payload["description"] = product.description[:1200]
    row = db.query(DnaProfile).filter(
        DnaProfile.session_id == session.id, DnaProfile.kind == "furniture").first()
    if row:
        row.payload, row.immutable_features = payload, immutable
        row.version = (row.version or 1) + 1
        return row
    row = DnaProfile(owner_user_id=session.owner_user_id, kind="furniture",
                     product_id=product.id, session_id=session.id,
                     name=product.name[:255], payload=payload,
                     immutable_features=immutable, source="catalog")
    db.add(row)
    db.flush()
    return row


def build_scene_dna(db, session, preset, custom_prompt=""):
    custom_prompt = (custom_prompt or "").strip()
    body = custom_prompt or SCENE_PRESETS.get(preset or "sovremennyy", SCENE_PRESETS["sovremennyy"])
    payload = {"preset": "custom" if custom_prompt else (preset or "sovremennyy"), "scene": body}
    immutable = [body,
                 "same exact floor material, color, pattern, seams and direction",
                 "same exact walls, doors, windows, ceiling and lighting fixtures",
                 "same exact furniture geometry, colors, materials, appliances and decor",
                 "same exact light direction and time of day"]
    row = db.query(DnaProfile).filter(
        DnaProfile.session_id == session.id, DnaProfile.kind == "scene").first()
    if row:
        row.payload, row.immutable_features = payload, immutable
        row.version = (row.version or 1) + 1
        return row
    row = DnaProfile(owner_user_id=session.owner_user_id, kind="scene",
                     session_id=session.id, name=preset or "sovremennyy",
                     payload=payload, immutable_features=immutable, source="preset")
    db.add(row)
    db.flush()
    return row


def _account_commercial_context(db, account_id):
    """Account-scoped commercial DNA from canonical BORIS data only.

    The photo studio must behave like the selected client's own studio, not a
    generic image generator.  We reuse accounts + confirmed client_facts; no
    second knowledge base is created and no facts from linked accounts leak in.
    """
    from sqlalchemy import text
    account = db.execute(text("""
        SELECT name, company_niche, company_tone, company_description,
               company_advantages, company_client_description, client_goal, client_goal_text, company_website
          FROM accounts WHERE account_id=:a
    """), {"a": account_id}).mappings().first() or {}
    facts = db.execute(text("""
        SELECT category, name, value, source_type, source_ref
          FROM client_facts
         WHERE account_id=:a AND status='confirmed'
           AND category IN ('company','service','campaign','rule')
         ORDER BY id DESC LIMIT 80
    """), {"a": account_id}).mappings().all()
    return {
        "account_id": account_id,
        "company": {k: account.get(k) or "" for k in (
            "name", "company_niche", "company_tone", "company_description",
            "company_advantages", "company_client_description", "client_goal", "client_goal_text", "company_website")},
        "confirmed_facts": [dict(r) for r in facts],
    }


def _commercial_context_text(context):
    company = context.get("company") or {}
    facts = context.get("confirmed_facts") or []
    lines = [
        "CLIENT COMMERCIAL DNA — authoritative account-scoped context:",
        "Brand/company: %s" % (company.get("name") or "not specified"),
        "Business/niche: %s" % (company.get("company_niche") or "not specified"),
        "Company description: %s" % (company.get("company_description") or "not specified"),
        "Advantages: %s" % (company.get("company_advantages") or "not specified"),
        "Target customer: %s" % (company.get("company_client_description") or "not specified"),
        "Commercial goal: %s" % (company.get("client_goal_text") or company.get("client_goal") or "not specified"),
        "Tone/positioning: %s" % (company.get("company_tone") or "not specified"),
        "Confirmed business rules/capabilities:",
    ]
    for fact in facts[:40]:
        value = str(fact.get("value") or "").strip()
        if value:
            lines.append("- [%s/%s] %s" % (fact.get("category") or "fact", fact.get("name") or "", value[:700]))
    lines.extend([
        "ADAPTATION RULE: interpret the user's visual request through this client's real business, assortment, positioning and proven capabilities.",
        "Never invent a product, service, material, city, price, availability, certification or capability that is absent from the user prompt/product facts/confirmed client facts.",
        "Do not render commercial claims, prices, logos or text into the image unless the user explicitly asks for them.",
    ])
    return "\n".join(lines)[:9000]


def build_concept_dna(db, session, prompt, commercial_context=None):
    """Паспорт сцены по промту, автоматически адаптированный под бизнес аккаунта."""
    prompt = (prompt or "").strip()
    commercial_context = commercial_context or _account_commercial_context(db, session.account_id)
    payload = {"name": "Сцена по промту", "description": prompt,
               "commercial_context": commercial_context}
    immutable = [prompt,
                 "preserve every major object, its geometry, material and color across all shots"]
    row = db.query(DnaProfile).filter(
        DnaProfile.session_id == session.id, DnaProfile.kind == "furniture").first()
    if row:
        row.payload, row.immutable_features = payload, immutable
        row.version = (row.version or 1) + 1
        return row
    row = DnaProfile(owner_user_id=session.owner_user_id, kind="furniture",
                     session_id=session.id, name="Сцена по промту",
                     payload=payload, immutable_features=immutable,
                     source="user", created_by=session.created_by)
    db.add(row); db.flush()
    return row


def _dna_text(dna):
    if dna is None:
        return ""
    feats = dna.immutable_features or []
    body = json.dumps(dna.payload or {}, ensure_ascii=False)
    return "%s\nIMMUTABLE: %s" % (body, "; ".join(str(f) for f in feats)[:1200])


def load_prompt(db, name):
    row = db.query(PromptTemplate).filter(
        PromptTemplate.name == name, PromptTemplate.is_active.is_(True)
    ).order_by(PromptTemplate.version.desc()).first()
    if row is None:
        raise RuntimeError("нет шаблона промпта %s — реестр не засеян" % name)
    return row


def build_prompt(db, tpl_name, furniture_dna, scene_dna, shot_definition,
                 purpose="Commercial listing photo for a marketplace."):
    tpl = load_prompt(db, tpl_name)
    body = (tpl.body
            .replace("{FURNITURE_DNA}", _dna_text(furniture_dna))
            .replace("{SCENE_DNA}", _dna_text(scene_dna))
            .replace("{SHOT_DEFINITION}", shot_definition or "")
            .replace("{PURPOSE}", purpose))
    return body, tpl


# Крупный план ОБЯЗАН отличаться палитрой от общего вида: это не подмена
# изделия, а другой масштаб. Порог зависит от типа кадра, иначе валидатор
# бракует правильные детали (найдено на живом прогоне 15.08).
PALETTE_LIMITS = {"detail": (55, 90), "lifestyle": (45, 75), "default": (35, 60)}


def _limits_for(shot_code):
    code = (shot_code or "").upper()
    if code.startswith("DETAIL") or code.startswith("OPEN_"):
        return PALETTE_LIMITS["detail"]
    if code.startswith("LIFESTYLE"):
        return PALETTE_LIMITS["lifestyle"]
    return PALETTE_LIMITS["default"]


def validate_shot(path, master_path=None, dna=None, shot_code=None):
    """Бесплатный слой проверки на PIL: палитра, яркость, размер, вес.

    Ловит главное — «нарисована другая мебель» через расхождение палитры с
    мастером. Тонкие подмены (форма ручек при том же цвете) этим слоем не
    ловятся, для них нужен vision — он подключается отдельным шагом.
    """
    from PIL import Image
    out = {"consistency_score": None, "photo_score": None,
           "critical_errors": [], "warnings": []}
    try:
        size_bytes = os.path.getsize(path)
    except OSError:
        out["critical_errors"].append("файл не создан")
        out["consistency_score"] = 0
        return out
    if size_bytes < 120 * 1024:
        out["critical_errors"].append("подозрительно маленький файл")
    with Image.open(path) as im:
        w, h = im.size
        if min(w, h) < 900:
            out["warnings"].append("низкое разрешение %dx%d" % (w, h))
        small = im.convert("RGB").resize((64, 64))
        px = list(small.getdata())
    avg = [sum(c[i] for c in px) / len(px) for i in range(3)]
    bright = sum(avg) / 3
    if bright < 35:
        out["critical_errors"].append("кадр провален в тень")
    elif bright > 225:
        out["critical_errors"].append("кадр пересвечен")
    score = 100
    if master_path and os.path.exists(master_path):
        with Image.open(master_path) as mi:
            msmall = mi.convert("RGB").resize((64, 64))
            mpx = list(msmall.getdata())
        mavg = [sum(c[i] for c in mpx) / len(mpx) for i in range(3)]
        delta = sum(abs(a - b) for a, b in zip(avg, mavg)) / 3
        warn_at, crit_at = _limits_for(shot_code)
        score = max(0, int(100 - delta * (1.4 if crit_at <= 60 else 0.9)))
        if delta > crit_at:
            out["critical_errors"].append(
                "палитра сильно расходится с мастером (%d при пороге %d) — возможно, изделие другое"
                % (int(delta), crit_at))
        elif delta > warn_at:
            out["warnings"].append("заметное расхождение палитры (%d)" % int(delta))
    out["consistency_score"] = score if not out["critical_errors"] else min(score, 40)
    out["photo_score"] = max(0, min(100, int(100 - abs(bright - 128) / 2)))
    return out


def session_dir(session):
    return "%s/photo_sessions/session_%s" % (session.account_id or "common",
                                             session.id)


def _register_media(db, session, path, shot_code):
    """Результат сразу становится ассетом медиатеки — второго хранилища нет."""
    key = os.path.relpath(path, st.IMAGES_DIR)
    a, _ = M.register_asset(
        db, session.owner_user_id, session.account_id, key,
        os.path.basename(path), media_type="ai_generated", source_type="ai",
        source_provider="openai", source_ref="photo_session:%s" % session.id,
        created_by=session.created_by, status="review")
    if session.product_id:
        M.link(db, a, "product", str(session.product_id), "gallery",
               created_by=session.created_by)
    return a


def run_master(db, session, product):
    """Мастер-кадр: один платный вызов, дальше ждём человека."""
    prompt_brief = ((session.settings or {}).get("prompt") or "").strip()
    commercial_context = _account_commercial_context(db, session.account_id)
    commercial_text = _commercial_context_text(commercial_context)
    reference_path = None
    if session.mode == "free_concept":
        fdna = build_concept_dna(db, session, prompt_brief, commercial_context)
        sdna = build_scene_dna(db, session, session.preset, custom_prompt=prompt_brief)
    elif session.mode == "preserve_reference":
        from app.models.media_asset import MediaAsset
        ref_id = int((session.settings or {}).get("reference_media_id") or 0)
        ref = db.query(MediaAsset).get(ref_id)
        if ref is None or ref.account_id != session.account_id or not ref.storage_key or not st.exists(ref.storage_key):
            session.status = "failed"
            db.commit()
            return {"ok": False, "message": "Исходная фотография недоступна"}
        reference_path = st.abs_path(ref.storage_key)
        fdna = build_concept_dna(db, session, prompt_brief, commercial_context)
        sdna = build_scene_dna(db, session, session.preset)
    else:
        fdna = build_furniture_dna(db, session, product)
        sdna = build_scene_dna(db, session, session.preset)
    session.furniture_dna_id, session.scene_dna_id = fdna.id, sdna.id
    code, tpl_name, definition = _shot_by_code("HERO_FRONT")
    prompt, tpl = build_prompt(db, tpl_name, fdna, sdna, definition)
    prompt += "\n\n" + commercial_text

    run_id = session.run_id or ("photo-%s" % uuid.uuid4().hex[:12])
    session.run_id = run_id
    est = IMG.estimate_kopeks(1)
    res = IMG.budget_reserve(session.account_id, run_id, est,
                             "photo_master", session.shots_planned or 1)
    if isinstance(res, dict) and res.get("status") not in (None, "reserved", "exists", "observe"):
        session.status = "paused_limit"
        db.commit()
        return {"ok": False, "reason": res.get("status"),
                "message": "Лимит операции достигнут"}
    allow = IMG.budget_allow(run_id)
    if isinstance(allow, dict) and not allow.get("allowed", True):
        session.status = "paused_limit"
        db.commit()
        return {"ok": False, "reason": allow.get("reason"),
                "message": "Лимит операции достигнут"}

    model = (session.settings or {}).get("model") or IMG.MODEL
    quality = (session.settings or {}).get("quality") or "medium"
    if session.mode == "preserve_reference":
        prompt += ("\n\nMARKETPLACE PHOTO RULE: produce a clean photorealistic listing photograph, not an advertising banner. "
                   "NO typography, captions, badges, prices, frames, logos or invented text. The exact reference object is the product. ")
    prompt += ("\n\nSERIES CONSISTENCY LOCK: Create one physically coherent scene. "
               "The floor material, color, grain/pattern, seams and direction; walls; windows; "
               "doors; ceiling; lighting fixtures; cabinetry; appliances; handles; worktop; "
               "decor and daylight direction are immutable for the entire series.")
    if reference_path:
        prompt += ("\n\nREFERENCE OBJECT LOCK: the supplied source photo is authoritative. Preserve the exact same physical object, "
                   "its make/model or design, geometry, body/facade color, materials, wheels/hardware and visible wear/damage. "
                   "PRIVACY: do not reproduce real license plates, serial numbers, phone numbers, addresses, personal names, faces or other identifying text from the source. "
                   "Render such identifiers neutral, unreadable or absent while keeping the non-identifying product geometry unchanged. "
                   "Change only the requested camera position/environment while keeping product identity otherwise unchanged.")
    shot = PhotoShot(session_id=session.id, shot_code=code, is_master=1,
                     status="generating", operation="generations", model=model,
                     quality=quality, size=IMG.DEFAULT_SIZE,
                     prompt_name=tpl.name, prompt_version=str(tpl.version),
                     prompt_text=prompt[:4000],
                     furniture_dna_version=str(fdna.version),
                     scene_dna_version=str(sdna.version))
    db.add(shot)
    db.flush()
    shot_id = int(shot.id)
    account_id = str(session.account_id)
    _product_name = product.name if product is not None else "Сцена по промту"
    path = st.abs_path("%s/master_%s.png" % (session_dir(session), int(time.time())))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Persist the durable `generating` state, then release PostgreSQL before the
    # paid provider call. SQLAlchemy expires ORM rows on commit, so everything
    # needed by the network phase is copied to scalars first.
    _burst_override = bool(session.batch_confirmed_kopeks)
    db.commit()
    out = IMG.generate(db, account_id, prompt, path, operation="photo_master",
                       model=model, quality=quality, reference_path=reference_path,
                       burst_override=_burst_override)
    shot = db.get(PhotoShot, shot_id)
    if shot is None:
        IMG.budget_release(run_id)
        raise RuntimeError("photo shot disappeared after generation")
    if not out["ok"]:
        shot.status, shot.error_text = "failed", (out.get("error") or "")[:2000]
        session.status = "failed"
        IMG.budget_release(run_id)
        db.commit()
        return {"ok": False, "message": "Кадр не удалось создать"}
    shot.request_id = out.get("request_id")
    if out.get("request_id"):
        IMG.budget_commit(run_id, out["request_id"])
    shot.cost_rub = out.get("cost_rub")
    shot.cost_kopeks = int(round(float(out.get("cost_rub") or 0) * 100))
    val = validate_shot(path)
    shot.consistency_score = val["consistency_score"]
    shot.photo_score = val["photo_score"]
    shot.critical_errors, shot.warnings = val["critical_errors"], val["warnings"]
    shot.status = "rejected" if val["critical_errors"] else "approved"
    asset = _register_media(db, session, path, code)
    shot.media_id = asset.id
    session.master_media_id = asset.id
    session.shots_done = 1
    session.actual_kopeks = (session.actual_kopeks or 0) + (shot.cost_kopeks or 0)
    session.status = "master_review"
    log_action(account_id=session.account_id or "",
               action="Создал мастер-кадр фотосессии",
               object_kind="photo_session", object_name=_product_name,
               after_val="кадр %s, согласованность %s" % (code, shot.consistency_score),
               actor=ACTOR_BORIS, source="photo.master")
    db.commit()
    return {"ok": True, "approved": shot.status == "approved", "shot_id": shot.id, "media_id": asset.id,
            "consistency_score": shot.consistency_score,
            "cost_rub": float(shot.cost_rub or 0)}


def _shot_cost(out):
    return int(round(float(out.get("cost_rub") or 0) * 100))


def run_derived(db, session, product, codes, max_retry=IMG.MAX_AUTO_RETRY):
    """Ракурсы из принятого мастера. Мастер уходит reference'ом в каждый вызов.

    Кадр с критической ошибкой валидатора переснимается с corrective-подсказкой,
    но не более max_retry раз: бесконтрольный retry — это деньги клиента.
    Достижение дневного лимита НЕ переносит расход на завтра само: сессия
    встаёт в paused_limit и спрашивает человека, если весь batch заранее не
    подтверждён.
    """
    from app.models.media_asset import MediaAsset
    master = db.query(MediaAsset).get(int(session.master_media_id or 0))
    if master is None:
        return {"ok": False, "message": "Сначала нужен принятый мастер-кадр"}
    master_path = st.abs_path(master.storage_key)
    fdna = db.query(DnaProfile).get(int(session.furniture_dna_id or 0))
    sdna = db.query(DnaProfile).get(int(session.scene_dna_id or 0))
    commercial_text = _commercial_context_text(
        _account_commercial_context(db, session.account_id))

    # Отдельный резерв на этап: прогон мастера уже закрыт, переиспользовать
    # его run_id нельзя — allow_next по завершённому прогону откажет.
    run_id = "photo-%s" % uuid.uuid4().hex[:12]
    session.run_id = run_id
    model = (session.settings or {}).get("model") or IMG.MODEL
    quality = (session.settings or {}).get("quality") or "medium"
    est = IMG.estimate_kopeks(len(codes) + 1, model=model, quality=quality)
    IMG.budget_reserve(session.account_id, run_id, est, "photo_derived",
                       len(codes) * (1 + max_retry))
    session.status = "deriving"
    db.commit()

    made, rejected, spent = [], [], 0
    try:
        for code in codes:
            c, tpl_name, definition = _shot_by_code(code)
            attempt, accepted, prev_shot = 1, False, None
            while attempt <= (1 + max_retry) and not accepted:
                allow = IMG.budget_allow(run_id)
                if isinstance(allow, dict) and not allow.get("allowed", True):
                    session.status = "paused_limit"
                    db.commit()
                    return {"ok": False, "message": "Лимит операции достигнут",
                            "made": made, "rejected": rejected,
                            "spent_rub": round(spent / 100, 2),
                            "left": [x for x in codes if x not in made]}
                extra = ""
                if attempt > 1 and prev_shot is not None:
                    extra = ("\nCORRECTION: previous attempt changed the product. "
                             "Reproduce the reference product EXACTLY, change only "
                             "the camera position. Problems: %s"
                             % "; ".join(prev_shot.critical_errors or []))
                prompt, tpl = build_prompt(db, tpl_name, fdna, sdna, definition)
                prompt += "\n\n" + commercial_text
                if session.mode == "preserve_reference":
                    prompt += ("\n\nMARKETPLACE PHOTO RULE: output a photorealistic product/listing photo only. NO banner design, typography, captions, badges, prices, frames, logos or invented text. ")
                prompt += ("\n\nSTRICT SAME-SCENE LOCK: use the master image as the authoritative reference. "
                           "Change ONLY the requested camera/view delta. Keep the exact same floor "
                           "material, color, grain/pattern, seams and direction; same walls, windows, "
                           "doors, ceiling, lights, cabinets, appliances, handles, countertop, decor, "
                           "object positions and daylight direction. Never redesign or substitute them.")
                prompt += extra
                shot = PhotoShot(
                    session_id=session.id, shot_code=c, is_master=0,
                    status="generating", operation="edits",
                    model=((session.settings or {}).get("model") or IMG.MODEL),
                    quality=((session.settings or {}).get("quality") or "medium"), size=IMG.DEFAULT_SIZE,
                    prompt_name=tpl.name, prompt_version=str(tpl.version),
                    prompt_text=prompt[:4000],
                    furniture_dna_version=str(getattr(fdna, "version", 1)),
                    scene_dna_version=str(getattr(sdna, "version", 1)),
                    reference_media_id=master.id, attempt=attempt,
                    retry_of=prev_shot.id if prev_shot is not None else None)
                db.add(shot)
                db.flush()
                shot_id = int(shot.id)
                account_id = str(session.account_id)
                _model = ((session.settings or {}).get("model") or IMG.MODEL)
                _quality = ((session.settings or {}).get("quality") or "medium")
                path = st.abs_path("%s/%s_%d_%d.png"
                                   % (session_dir(session), c.lower(), attempt,
                                      int(time.time())))
                # Each derived image can spend minutes at the provider. Persist the
                # attempt and close the transaction before external I/O; resume by id.
                _burst_override = bool(session.batch_confirmed_kopeks)
                db.commit()
                out = IMG.generate(db, account_id, prompt, path,
                                   model=_model, quality=_quality,
                                   operation="photo_derived",
                                   reference_path=master_path,
                                   burst_override=_burst_override)
                shot = db.get(PhotoShot, shot_id)
                if shot is None:
                    raise RuntimeError("photo derived shot disappeared after generation")
                if not out["ok"]:
                    shot.status = "failed"
                    shot.error_text = (out.get("error") or "")[:2000]
                    db.commit()
                    break
                shot.request_id = out.get("request_id")
                if out.get("request_id"):
                    IMG.budget_commit(run_id, out["request_id"])
                shot.cost_rub = out.get("cost_rub")
                shot.cost_kopeks = _shot_cost(out)
                spent += shot.cost_kopeks or 0
                val = validate_shot(path, master_path, fdna, shot_code=c)
                shot.consistency_score = val["consistency_score"]
                shot.photo_score = val["photo_score"]
                shot.critical_errors = val["critical_errors"]
                shot.warnings = val["warnings"]
                if val["critical_errors"]:
                    shot.status = "rejected"
                    rejected.append({"code": c, "attempt": attempt,
                                     "why": val["critical_errors"]})
                    prev_shot = shot
                    attempt += 1
                    db.commit()
                    continue
                shot.status = "approved"
                asset = _register_media(db, session, path, c)
                shot.media_id = asset.id
                made.append(c)
                accepted = True
                db.commit()
        session.shots_done = (session.shots_done or 0) + len(made)
        session.shots_rejected = (session.shots_rejected or 0) + len(rejected)
        session.actual_kopeks = (session.actual_kopeks or 0) + spent
        session.status = "completed" if len(made) == len(codes) else "partial"
        from datetime import datetime, timezone as _tz
        session.finished_at = datetime.now(_tz.utc)
        log_action(account_id=session.account_id or "",
                   action="Собрал серию кадров фотосессии",
                   object_kind="photo_session", object_name=(product.name if product is not None else "Сцена по промту"),
                   after_val="принято %d из %d, переснято %d"
                             % (len(made), len(codes), len(rejected)),
                   actor=ACTOR_BORIS, source="photo.derived")
        db.commit()
    finally:
        IMG.budget_release(run_id)
    return {"ok": True, "made": made, "rejected": rejected,
            "spent_rub": round(spent / 100, 2), "status": session.status}


def session_public(db, session):
    """Галерея результата: оценки, стоимость и происхождение каждого кадра."""
    shots = db.query(PhotoShot).filter(
        PhotoShot.session_id == session.id).order_by(PhotoShot.id).all()
    from app.models.media_asset import MediaAsset
    master_url = None
    if session.master_media_id:
        ma = db.query(MediaAsset).get(int(session.master_media_id))
        if ma is not None:
            master_url = st.public_url(ma.storage_key)
    return {
        "id": session.id, "status": session.status, "mode": session.mode,
        "preset": session.preset, "product_id": session.product_id,
        "shots_planned": session.shots_planned, "shots_done": session.shots_done,
        "shots_rejected": session.shots_rejected,
        "master_media_id": session.master_media_id, "master_url": master_url,
        "cost_rub": round((session.actual_kopeks or 0) / 100, 2),
        "est_rub": round((session.est_kopeks or 0) / 100, 2),
        "shots": [{"id": s.id, "code": s.shot_code, "master": bool(s.is_master),
                   "status": s.status, "media_id": s.media_id,
                   "consistency_score": s.consistency_score,
                   "photo_score": s.photo_score,
                   "critical": s.critical_errors or [],
                   "warnings": s.warnings or [],
                   "attempt": s.attempt, "operation": s.operation,
                   "prompt": "%s v%s" % (s.prompt_name, s.prompt_version)}
                  for s in shots],
    }


def metrics(db, owner_user_id=None):
    """Метрики фотостудии: сколько принимается с первого раза и почём."""
    from sqlalchemy import func as _f
    q = db.query(PhotoShot).join(PhotoSession,
                                 PhotoSession.id == PhotoShot.session_id)
    if owner_user_id:
        q = q.filter(PhotoSession.owner_user_id == owner_user_id)
    shots = q.all()
    if not shots:
        return {"shots": 0}
    approved = [s for s in shots if s.status == "approved"]
    first_try = [s for s in approved if (s.attempt or 1) == 1]
    cost = sum(int(s.cost_kopeks or 0) for s in shots)
    cons = [s.consistency_score for s in approved if s.consistency_score is not None]
    return {
        "shots": len(shots), "approved": len(approved),
        "accepted_first_try_rate": round(len(first_try) / max(1, len(approved)), 2),
        "average_retry_count": round(sum(s.attempt or 1 for s in shots) / len(shots), 2),
        "average_consistency_score": round(sum(cons) / len(cons), 1) if cons else None,
        "cost_per_generated_image_rub": round(cost / 100 / len(shots), 2),
        "cost_per_accepted_image_rub": round(cost / 100 / max(1, len(approved)), 2),
        "rejected": len([s for s in shots if s.status == "rejected"]),
    }
