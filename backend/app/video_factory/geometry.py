# -*- coding: utf-8 -*-
"""BORIS Video Factory — геометрия сцены.

Producer сообщает только факты о том, где что находилось на записи.
Ни одной рамки в кадр этот модуль не рисует: highlight, zoom и подписи —
работа будущего renderer'а.

Ключевое правило: отсутствующий элемент НЕ превращается в нулевой
прямоугольник. Он получает явный статус.
"""

STATUS_OK = "ok"
STATUS_NOT_VISIBLE = "focus_not_visible"
STATUS_OUTSIDE = "focus_outside_viewport"
STATUS_NO_SELECTOR = "focus_no_selector"

# сцены сравниваются в долях кадра, поэтому допуск тоже в долях
DEFAULT_TOLERANCE = 0.02


def _num(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_rect(box):
    """Playwright отдаёт {x, y, width, height}. Приводим к {x, y, w, h} float."""
    if not isinstance(box, dict):
        return None
    x = _num(box.get("x"))
    y = _num(box.get("y"))
    w = _num(box.get("w", box.get("width")))
    h = _num(box.get("h", box.get("height")))
    if None in (x, y, w, h):
        return None
    return {"x": x, "y": y, "w": w, "h": h}


def clamp_rect(rect, vw, vh):
    """Обрезаем прямоугольник по границам кадра. Возвращаем None, если не пересекается."""
    if not rect:
        return None
    left = max(0.0, rect["x"])
    top = max(0.0, rect["y"])
    right = min(float(vw), rect["x"] + rect["w"])
    bottom = min(float(vh), rect["y"] + rect["h"])
    if right <= left or bottom <= top:
        return None
    return {"x": left, "y": top, "w": right - left, "h": bottom - top}


def expand_rect(rect, padding, vw, vh):
    """safe_rect — focus_rect с полями, прижатый к границам кадра."""
    if not rect:
        return None
    pad = max(0.0, float(padding or 0))
    grown = {
        "x": rect["x"] - pad,
        "y": rect["y"] - pad,
        "w": rect["w"] + pad * 2,
        "h": rect["h"] + pad * 2,
    }
    return clamp_rect(grown, vw, vh)


def norm_rect(rect, vw, vh, digits=6):
    """Доли кадра 0..1 — чтобы один semantic scenario сравнивался между разрешениями."""
    if not rect or not vw or not vh:
        return None
    return {
        "x": round(rect["x"] / float(vw), digits),
        "y": round(rect["y"] / float(vh), digits),
        "w": round(rect["w"] / float(vw), digits),
        "h": round(rect["h"] / float(vh), digits),
    }


def describe_focus(box, viewport, padding=0, selector=None):
    """Полное геометрическое описание сцены для scenes.json.

    box — результат bounding_box() или None.
    Возвращает словарь с focus_rect_px/norm, safe_rect_px/norm, is_visible,
    is_in_viewport и статусом. Нулевых координат не бывает: если элемента нет,
    прямоугольники равны None.
    """
    vw = int(viewport.get("width") or 0)
    vh = int(viewport.get("height") or 0)
    out = {
        "selector": selector,
        "focus_rect_px": None,
        "focus_rect_norm": None,
        "safe_rect_px": None,
        "safe_rect_norm": None,
        "focus_padding": int(padding or 0),
        "is_visible": False,
        "is_in_viewport": False,
        "status": STATUS_OK,
        "viewport": {"width": vw, "height": vh},
    }
    if not selector:
        out["status"] = STATUS_NO_SELECTOR
        return out

    rect = as_rect(box)
    if rect is None or rect["w"] <= 0 or rect["h"] <= 0:
        out["status"] = STATUS_NOT_VISIBLE
        return out

    out["is_visible"] = True
    out["focus_rect_px"] = rect
    out["focus_rect_norm"] = norm_rect(rect, vw, vh)

    visible_part = clamp_rect(rect, vw, vh)
    if visible_part is None:
        out["status"] = STATUS_OUTSIDE
        return out

    out["is_in_viewport"] = True
    safe = expand_rect(rect, padding, vw, vh)
    out["safe_rect_px"] = safe
    out["safe_rect_norm"] = norm_rect(safe, vw, vh)
    return out


def rects_match(a, b, tolerance=DEFAULT_TOLERANCE):
    """Совпадают ли два нормализованных прямоугольника в пределах допуска."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    for key in ("x", "y", "w", "h"):
        if abs(float(a[key]) - float(b[key])) > tolerance:
            return False
    return True


def compare_scenes(scenes_a, scenes_b, tolerance=DEFAULT_TOLERANCE):
    """Сравнение двух проходов одного semantic scenario.

    Смотрим порядок сцен, статусы фокуса и совпадение геометрии в долях кадра.
    Абсолютные пиксели и миллисекунды намеренно НЕ сравниваем: они законно
    плавают от прогона к прогону.
    """
    ids_a = [s.get("scene_id") for s in scenes_a]
    ids_b = [s.get("scene_id") for s in scenes_b]
    result = {
        "same_scene_order": ids_a == ids_b,
        "scenes_a": len(scenes_a),
        "scenes_b": len(scenes_b),
        "tolerance": tolerance,
        "differences": [],
        "match": False,
    }
    if ids_a != ids_b:
        result["differences"].append({
            "kind": "scene_order",
            "a": ids_a,
            "b": ids_b,
        })
        return result

    by_id_b = {s.get("scene_id"): s for s in scenes_b}
    for scene_a in scenes_a:
        sid = scene_a.get("scene_id")
        scene_b = by_id_b.get(sid, {})
        if scene_a.get("status") != scene_b.get("status"):
            result["differences"].append({
                "kind": "focus_status",
                "scene_id": sid,
                "a": scene_a.get("status"),
                "b": scene_b.get("status"),
            })
            continue
        if not rects_match(scene_a.get("focus_rect_norm"),
                           scene_b.get("focus_rect_norm"), tolerance):
            result["differences"].append({
                "kind": "focus_rect_norm",
                "scene_id": sid,
                "a": scene_a.get("focus_rect_norm"),
                "b": scene_b.get("focus_rect_norm"),
            })

    result["match"] = not result["differences"]
    return result
