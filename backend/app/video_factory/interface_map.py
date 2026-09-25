# -*- coding: utf-8 -*-
"""BORIS Video Factory — карта интерфейса и выбор первого сценария.

probe заполняет карту фактами, а этот модуль превращает её в решение:
какой экран годится под демонстрацию и почему. Сценарий выбирает машина
по замеренной поверхности, а не человек по памяти.
"""

import re

SCREEN_FIELDS = (
    "route_or_tab",
    "reachable",
    "url_before",
    "url_after",
    "restorable_new_context",
    "active_marker",
    "data_testids",
    "visible_blocks",
    "safe_read_actions",
    "unsafe_or_write_actions",
    "candidate_score",
    "candidate_reason",
)

# слова, по которым элемент считается опасным и экран теряет очки
DANGER_WORDS = (
    "удал", "оплат", "плат", "куп", "спис", "отправ", "опубл", "выгруз",
    "запуст", "продвиж", "ставк", "измен", "сохран", "созда", "добав",
    "delete", "pay", "send", "publish", "submit", "buy", "start",
)

WEIGHTS = {
    "testid": 2,
    "testid_cap": 20,
    "block": 3,
    "block_cap": 21,
    "safe_action": 2,
    "safe_action_cap": 10,
    "restorable": 15,
    "active_marker": 5,
    "unsafe_penalty": 12,
}

MIN_BLOCKS_FOR_SCENARIO = 3


def new_screen(route_or_tab):
    """Пустая запись карты со всеми обязательными полями."""
    return {
        "route_or_tab": route_or_tab,
        "reachable": False,
        "url_before": None,
        "url_after": None,
        "restorable_new_context": None,
        "active_marker": None,
        "data_testids": [],
        "visible_blocks": [],
        "safe_read_actions": [],
        "unsafe_or_write_actions": [],
        "candidate_score": 0,
        "candidate_reason": "не исследован",
    }


def looks_dangerous(label):
    low = (label or "").strip().lower()
    return any(word in low for word in DANGER_WORDS)


def score_screen(screen):
    """Оценка пригодности экрана под первый ролик. Возвращает (score, reason)."""
    if not screen.get("reachable"):
        return 0, "экран недоступен demo-клиенту"

    blocks = screen.get("visible_blocks") or []
    testids = screen.get("data_testids") or []
    safe = screen.get("safe_read_actions") or []
    unsafe = screen.get("unsafe_or_write_actions") or []

    if len(blocks) < MIN_BLOCKS_FOR_SCENARIO:
        return 0, ("видимых блоков %d — меньше минимума %d, показывать нечего"
                   % (len(blocks), MIN_BLOCKS_FOR_SCENARIO))

    score = 0
    parts = []

    add = min(len(testids) * WEIGHTS["testid"], WEIGHTS["testid_cap"])
    score += add
    parts.append("стабильных data-testid: %d (+%d)" % (len(testids), add))

    add = min(len(blocks) * WEIGHTS["block"], WEIGHTS["block_cap"])
    score += add
    parts.append("видимых блоков: %d (+%d)" % (len(blocks), add))

    add = min(len(safe) * WEIGHTS["safe_action"], WEIGHTS["safe_action_cap"])
    score += add
    parts.append("безопасных действий: %d (+%d)" % (len(safe), add))

    if screen.get("restorable_new_context"):
        score += WEIGHTS["restorable"]
        parts.append("состояние восстанавливается в новом контексте (+%d)" % WEIGHTS["restorable"])
    else:
        parts.append("состояние не восстанавливается по URL (+0)")

    if screen.get("active_marker"):
        score += WEIGHTS["active_marker"]
        parts.append("есть якорь активного состояния (+%d)" % WEIGHTS["active_marker"])

    if unsafe:
        penalty = min(len(unsafe) * WEIGHTS["unsafe_penalty"], 40)
        score -= penalty
        parts.append("действий, меняющих данные: %d (-%d)" % (len(unsafe), penalty))

    score = max(0, min(100, score))
    return score, "; ".join(parts)


def score_map(screens):
    """Проставляет candidate_score и candidate_reason во всех записях карты."""
    for screen in screens:
        score, reason = score_screen(screen)
        screen["candidate_score"] = score
        screen["candidate_reason"] = reason
    return screens


def rank_screens(screens):
    """Кандидаты по убыванию оценки. Нулевые не участвуют."""
    scored = score_map(list(screens))
    good = [s for s in scored if s["candidate_score"] > 0]
    good.sort(key=lambda s: (-s["candidate_score"], s["route_or_tab"]))
    return good


def choose_scenario_screens(screens, limit=3):
    """Лучшие экраны под первый ролик и объяснение выбора."""
    ranked = rank_screens(screens)
    if not ranked:
        return [], "ни один экран не набрал очков: показывать нечего"
    chosen = ranked[:limit]
    reason = "выбрано %d из %d пригодных: %s" % (
        len(chosen), len(ranked),
        ", ".join("%s (%d)" % (s["route_or_tab"], s["candidate_score"]) for s in chosen),
    )
    return chosen, reason


def _scene_id(route_or_tab, used):
    base = re.sub(r"[^a-z0-9]+", "_", (route_or_tab or "screen").lower()).strip("_")
    base = re.sub(r"^(dashboard_|api_)", "", base) or "screen"
    if not re.match(r"^[a-z]", base):
        base = "s_" + base
    base = base[:48]
    candidate, n = base, 2
    while candidate in used:
        candidate = "%s_%d" % (base, n)
        n += 1
    used.add(candidate)
    return candidate


def build_scenario_draft(screens, scenario_id, title, viewport=None,
                         fixture_version="none", hold_ms=3500):
    """Черновик сценария прямо из карты.

    Только безопасные действия: переход, ожидание якоря, фокус на блоках,
    статичная выдержка под озвучку. Ни одного клика по элементам, меняющим
    данные. Тексты озвучки помечены как черновые — их пишет человек.
    """
    viewport = viewport or {"width": 2560, "height": 1440}
    steps, voiceover, used = [], {}, set()

    for screen in screens:
        scene = _scene_id(screen.get("route_or_tab"), used)
        route = screen.get("url_after") or screen.get("route_or_tab")
        steps.append({"action": "goto", "scene_id": scene, "target": route})

        anchor = screen.get("active_marker") or (
            "[data-testid='%s']" % screen["data_testids"][0]
            if screen.get("data_testids") else None
        )
        if anchor:
            steps.append({"action": "wait_for", "scene_id": scene, "selector": anchor})

        for block in (screen.get("visible_blocks") or [])[:3]:
            selector = block.get("selector") if isinstance(block, dict) else block
            if not selector:
                continue
            steps.append({"action": "highlight", "scene_id": scene,
                          "selector": selector, "duration_ms": 2500})

        steps.append({"action": "pause_for_voiceover", "scene_id": scene, "ms": hold_ms})

        names = ", ".join(
            str(b.get("label") if isinstance(b, dict) else b)
            for b in (screen.get("visible_blocks") or [])[:3]
        )
        voiceover[scene] = ("ЧЕРНОВИК. Экран «%s». Показываем: %s"
                            % (screen.get("route_or_tab"), names or "основные блоки"))

    return {
        "id": scenario_id,
        "title": title,
        "version": "1",
        "demo_fixture_version": fixture_version,
        "viewport": dict(viewport),
        "voiceover_source": "auto_draft_from_interface_map",
        "voiceover": voiceover,
        "steps": steps,
    }
