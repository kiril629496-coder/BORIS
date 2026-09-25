"""Universal Consistency Engine ЕЦО.

Ядро НЕ знает ни про духовки, ни про фасады: любая семантика приходит данными
через CategoryConsistencyProfile. Мебель — только первый QA-кейс, за ней идут
авто, одежда, недвижимость, техника, стройматериалы и результат услуги.

Формула генерации: PROJECT DNA + MASTER REFERENCE + SHOT DELTA -> NEW FRAME.
Каждая операция обязана объявить, что locked и что mutable, иначе модель
перерисовывает объект заново вместо смены камеры.
"""

# --- разделы Project DNA (универсальные, без товарной семантики) ---
DNA_SECTIONS = ("subject", "scene", "identity", "spatial", "state")

# Статусы признака на кадре. OCCLUDED и OUT_OF_FRAME — НЕ ошибка: объект может
# быть не виден из-за ракурса, это нормальная физика съёмки.
FEATURE_STATES = ("present", "occluded", "out_of_frame", "changed", "missing",
                  "uncertain")
NON_VIOLATIONS = ("present", "occluded", "out_of_frame")

VERDICTS = ("pass", "pass_with_warning", "reject")

UNIVERSAL_SHOTS = {
    "HERO_FRONT": {"delta": "camera at eye level, straight in front of the subject",
                   "kind": "hero"},
    "HERO_45_LEFT": {"delta": "camera moved 45 degrees to the left of the subject",
                     "kind": "angle"},
    "HERO_45_RIGHT": {"delta": "camera moved 45 degrees to the right of the subject",
                      "kind": "angle"},
    "WIDE": {"delta": "camera stepped back, wider framing of the whole scene",
             "kind": "wide"},
    "DETAIL_SURFACE": {"delta": "close-up of the main surface and its texture",
                       "kind": "detail"},
    "DETAIL_JOINT": {"delta": "close-up of joints, edges or fittings",
                     "kind": "detail"},
    "STATE_OPEN": {"delta": "the same subject shown in its open or working state",
                   "kind": "state"},
    "LIFESTYLE": {"delta": "warm lifestyle framing of the same scene, no people",
                  "kind": "lifestyle"},
}

# Универсальный профиль: работает для НЕИЗВЕСТНОЙ категории. Незнакомая ниша
# не должна ломать фотостудию — она просто получает более мягкие правила.
UNIVERSAL_PROFILE = {
    "code": "universal",
    "identity_features": ["overall_shape", "main_color", "main_material",
                          "proportions", "visible_components"],
    "hard_invariants": ["overall_shape", "main_color", "main_material",
                        "component_count", "component_layout"],
    "soft_invariants": ["exposure", "light_temperature", "reflections",
                        "depth_of_field", "crop", "minor_decor"],
    "scene_invariants": ["surface_material", "surface_orientation",
                         "openings_layout", "light_direction"],
    "allowed_changes": ["camera_position", "camera_angle", "focal_length",
                        "camera_height", "crop", "composition_accent"],
    "forbidden_changes": ["subject_geometry", "component_count",
                          "component_layout", "material", "color"],
    "shot_types": ["HERO_FRONT", "HERO_45_LEFT", "HERO_45_RIGHT", "WIDE",
                   "DETAIL_SURFACE", "LIFESTYLE"],
    "thresholds": {"palette_warn": 35, "palette_crit": 60,
                   "palette_warn_detail": 55, "palette_crit_detail": 90},
}

# Профили категорий — ДАННЫЕ. Добавление ниши не требует правки движка.
CATEGORY_PROFILES = {
    "furniture": {
        "code": "furniture",
        "identity_features": ["geometry", "section_count", "facade_material",
                              "facade_color", "handles", "worktop", "appliances",
                              "layout"],
        "hard_invariants": ["geometry", "section_count", "facade_material",
                            "facade_color", "handles", "worktop", "appliances",
                            "appliance_position", "layout"],
        "soft_invariants": ["exposure", "light_temperature", "reflections",
                            "depth_of_field", "crop", "decor"],
        "scene_invariants": ["floor_material", "floor_direction", "wall_color",
                             "windows", "doors", "ceiling_lights"],
        "allowed_changes": ["camera_position", "camera_angle", "focal_length",
                            "camera_height", "crop"],
        "forbidden_changes": ["section_count", "appliance_set", "handles",
                              "facade_material", "facade_color", "floor_direction"],
        "shot_types": ["HERO_FRONT", "HERO_45_LEFT", "HERO_45_RIGHT", "WIDE",
                       "DETAIL_SURFACE", "DETAIL_JOINT", "STATE_OPEN", "LIFESTYLE"],
        "thresholds": UNIVERSAL_PROFILE["thresholds"],
    },
    "vehicle": {
        "code": "vehicle",
        "identity_features": ["model", "body_type", "body_color", "wheels",
                              "headlights", "grille", "door_count", "trim"],
        "hard_invariants": ["model", "body_type", "body_color", "wheels",
                            "headlights", "grille", "door_count"],
        "soft_invariants": ["exposure", "reflections", "background_blur", "crop"],
        "scene_invariants": ["ground_surface", "light_direction", "background_type"],
        "allowed_changes": ["camera_position", "camera_angle", "focal_length", "crop"],
        "forbidden_changes": ["model", "wheels", "headlights", "door_count",
                              "body_color"],
        "shot_types": ["HERO_45_LEFT", "HERO_45_RIGHT", "HERO_FRONT", "WIDE",
                       "DETAIL_SURFACE", "DETAIL_JOINT"],
        "thresholds": UNIVERSAL_PROFILE["thresholds"],
    },
    "apparel": {
        "code": "apparel",
        "identity_features": ["cut", "print", "seams", "pockets", "buttons",
                              "fabric", "color"],
        "hard_invariants": ["cut", "print", "pockets", "buttons", "fabric", "color"],
        "soft_invariants": ["exposure", "fabric_fold", "crop", "background"],
        "scene_invariants": ["background_type", "light_direction"],
        "allowed_changes": ["camera_position", "camera_angle", "crop"],
        "forbidden_changes": ["cut", "print", "pockets", "buttons", "color"],
        "shot_types": ["HERO_FRONT", "HERO_45_LEFT", "DETAIL_SURFACE", "DETAIL_JOINT"],
        "thresholds": UNIVERSAL_PROFILE["thresholds"],
    },
    "realty": {
        "code": "realty",
        "identity_features": ["layout", "windows", "doors", "walls", "floor",
                              "built_ins"],
        "hard_invariants": ["layout", "windows", "doors", "floor_material",
                            "floor_direction", "built_ins"],
        "soft_invariants": ["exposure", "light_temperature", "decor", "crop"],
        "scene_invariants": ["room_geometry", "window_positions", "light_direction"],
        "allowed_changes": ["camera_position", "camera_angle", "focal_length",
                            "camera_height", "crop"],
        "forbidden_changes": ["layout", "window_count", "door_positions",
                              "floor_direction"],
        "shot_types": ["WIDE", "HERO_FRONT", "HERO_45_LEFT", "HERO_45_RIGHT",
                       "DETAIL_SURFACE"],
        "thresholds": UNIVERSAL_PROFILE["thresholds"],
    },
    "device": {
        "code": "device",
        "identity_features": ["body", "buttons", "screen", "ports", "markings",
                              "controls", "color"],
        "hard_invariants": ["body", "buttons", "screen", "ports", "markings",
                            "controls", "color"],
        "soft_invariants": ["exposure", "reflections", "crop", "background"],
        "scene_invariants": ["surface_material", "light_direction"],
        "allowed_changes": ["camera_position", "camera_angle", "crop"],
        "forbidden_changes": ["ports", "buttons", "markings", "color"],
        "shot_types": ["HERO_FRONT", "HERO_45_LEFT", "DETAIL_SURFACE", "DETAIL_JOINT"],
        "thresholds": UNIVERSAL_PROFILE["thresholds"],
    },
    "material": {
        "code": "material",
        "identity_features": ["shape", "size", "texture", "pattern", "color",
                              "profile"],
        "hard_invariants": ["shape", "size", "texture", "pattern", "color", "profile"],
        "soft_invariants": ["exposure", "crop", "background"],
        "scene_invariants": ["surface_orientation", "light_direction"],
        "allowed_changes": ["camera_position", "camera_angle", "crop"],
        "forbidden_changes": ["pattern", "profile", "color", "texture"],
        "shot_types": ["HERO_FRONT", "DETAIL_SURFACE", "WIDE"],
        "thresholds": UNIVERSAL_PROFILE["thresholds"],
    },
    "service_result": {
        "code": "service_result",
        "identity_features": ["object_geometry", "surface_state", "boundaries",
                              "surroundings"],
        "hard_invariants": ["object_geometry", "boundaries", "surroundings",
                            "openings_layout"],
        "soft_invariants": ["exposure", "light_temperature", "crop"],
        "scene_invariants": ["room_geometry", "openings_layout", "light_direction"],
        # Для «до/после» состояние поверхности меняется намеренно, а сам объект нет.
        "allowed_changes": ["camera_position", "camera_angle", "crop",
                            "surface_state"],
        "forbidden_changes": ["object_geometry", "boundaries", "openings_layout"],
        "shot_types": ["WIDE", "HERO_FRONT", "DETAIL_SURFACE", "STATE_OPEN"],
        "thresholds": UNIVERSAL_PROFILE["thresholds"],
    },
}

# Как ниша клиента ложится на профиль. Незнакомое слово -> universal.
NICHE_HINTS = (
    (("мебел", "кухн", "шкаф", "диван", "кроват", "гардероб", "стол", "кресл"), "furniture"),
    (("авто", "машин", "эвакуат", "прицеп", "спецтехник", "самокат"), "vehicle"),
    (("одежд", "обув", "текстил", "форм"), "apparel"),
    (("квартир", "дом", "недвиж", "бытовк", "модульн", "контейнер", "офис"), "realty"),
    (("техник", "оборудован", "станок", "инструмент", "прибор"), "device"),
    (("плитк", "бетон", "щебен", "песок", "перегородк", "стекл", "материал"), "material"),
    (("штукатур", "ремонт", "монтаж", "услуг", "уборк", "клининг", "натяжн"), "service_result"),
)


def profile_for(category=None, niche=None):
    """Профиль по категории или нише клиента. Неизвестное -> универсальный.

    Совпадение ищем по НАЧАЛУ слова, а не по подстроке: иначе «неведомое»
    цепляется за «дом» и незнакомая ниша получает чужие жёсткие инварианты.
    """
    import re
    key = str(category or "").strip().lower()
    if key in CATEGORY_PROFILES:
        return CATEGORY_PROFILES[key]
    text = ("%s %s" % (category or "", niche or "")).lower()
    words_in_text = re.findall(r"[а-яёa-z]+", text)
    for words, code in NICHE_HINTS:
        for w in words:
            for token in words_in_text:
                if token.startswith(w):
                    return CATEGORY_PROFILES[code]
    return UNIVERSAL_PROFILE


def shot_delta(shot_code):
    """Единственное, что разрешено менять кадру."""
    s = UNIVERSAL_SHOTS.get(shot_code)
    if s:
        return s["delta"], s["kind"]
    return shot_code.lower().replace("_", " "), "angle"


def locked_mutable(profile, operation="new_angle"):
    """Что заморожено и что разрешено менять в конкретной операции.

    Именно эта пара, а не общий промпт, отличает «сними с другого ракурса» от
    «покажи в другом интерьере».
    """
    subject = list(profile["hard_invariants"])
    scene = list(profile["scene_invariants"])
    if operation == "new_scene":          # объект заморожен, окружение меняем
        return subject, ["scene"] + list(profile["allowed_changes"])
    if operation == "new_state":          # до/после: состояние поверхности мутабельно
        locked = [f for f in subject if f not in ("surface_state",)]
        return locked + scene, ["surface_state"] + list(profile["allowed_changes"])
    if operation == "cleanup":            # чистим кадр, товар не трогаем
        return subject, ["scene_clutter", "exposure", "crop"]
    return subject + scene, list(profile["allowed_changes"])


def invariant_lock_text(dna, profile, shot_code, operation="new_angle"):
    """INVARIANT LOCK собирается ИЗ СТРУКТУРЫ ПРОЕКТА, а не из общего текста."""
    locked, mutable = locked_mutable(profile, operation)
    delta, kind = shot_delta(shot_code)
    lines = [
        "This is the same exact physical object in the same exact scene during "
        "the same photo session. Do not redesign, replace, add, remove or "
        "relocate anything.",
        "Only this may change: %s." % delta,
        "If an element becomes invisible because of the camera angle it may be "
        "occluded, but it must NOT be removed or replaced by another element.",
    ]
    facts = []
    for section in ("subject", "identity", "spatial", "state", "scene"):
        block = (dna or {}).get(section) or {}
        for k, v in block.items():
            if v in (None, "", [], {}):
                continue
            facts.append("%s: %s" % (str(k).upper(), v))
    if facts:
        lines.append("INVARIANTS OF THIS EXACT PROJECT:")
        lines.extend(facts[:40])
    lines.append("LOCKED: %s." % ", ".join(locked[:20]))
    lines.append("MAY CHANGE: %s." % ", ".join(mutable[:12]))
    return "\n".join(lines)


def corrective_text(violations):
    """Адресная поправка из конкретных нарушений, а не «попробуй ещё раз»."""
    if not violations:
        return ""
    parts = ["Previous attempt was rejected."]
    for v in violations[:6]:
        f, state = v.get("feature"), v.get("state")
        if state == "missing":
            parts.append("The %s disappeared although its location is visible. "
                         "Restore it exactly as in the reference." % f)
        elif state == "changed":
            parts.append("The %s was changed. Restore it exactly as in the "
                         "reference." % f)
        else:
            parts.append("Check %s against the reference." % f)
    parts.append("Change the camera only.")
    return " ".join(parts)


def verdict(features, palette_delta, thresholds, shot_kind="angle"):
    """PASS / PASS_WITH_WARNING / REJECT + violations[].

    Одно нарушение hard invariant перевешивает любой средний балл: красивый
    кадр с исчезнувшей духовкой в объявление не годится.
    """
    violations = [f for f in features
                  if f.get("hard") and f.get("state") not in NON_VIOLATIONS]
    warnings = [f for f in features
                if not f.get("hard") and f.get("state") in ("changed", "uncertain")]
    warn_at = thresholds.get("palette_warn_detail" if shot_kind == "detail"
                             else "palette_warn", 35)
    crit_at = thresholds.get("palette_crit_detail" if shot_kind == "detail"
                             else "palette_crit", 60)
    score = 100
    if palette_delta is not None:
        score = max(0, int(100 - palette_delta * (1.4 if crit_at <= 60 else 0.9)))
        if palette_delta > crit_at:
            violations.append({"feature": "overall_appearance", "state": "changed",
                               "hard": True,
                               "note": "палитра разошлась с мастером (%d)" % int(palette_delta)})
        elif palette_delta > warn_at:
            warnings.append({"feature": "overall_appearance", "state": "uncertain",
                             "hard": False})
    if violations:
        return "reject", min(score, 40), violations, warnings
    if warnings:
        return "pass_with_warning", score, violations, warnings
    return "pass", score, violations, warnings
