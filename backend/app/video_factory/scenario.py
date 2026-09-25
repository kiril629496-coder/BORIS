# -*- coding: utf-8 -*-
"""BORIS Video Factory — разбор и валидация смыслового сценария.

Модуль ничего не знает ни о браузере, ни о площадках, ни о рендере.
Он превращает yaml/json в проверенный resolved-контракт, который producer
исполняет, а artifacts сохраняют для будущего монтажа.
"""

import json
import os
import re

VERSION = "1.0"

DEFAULT_VIEWPORT = {"width": 2560, "height": 1440}
DEFAULT_DSF = 1
DEFAULT_FOCUS_PADDING = 40
DEFAULT_CAPTURE_KIND = "playwright"

# границы, за которыми сценарий считается ошибочным, а не смелым
LIMITS = {
    "width": (320, 7680),
    "height": (240, 4320),
    "device_scale_factor": (1, 4),
    "ms": (0, 600000),
    "duration_ms": (0, 600000),
    "focus_padding": (0, 1000),
}

SCENE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
SCENARIO_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

# действие -> обязательные поля шага
ACTIONS = {
    "goto": ("target",),
    "click": ("selector",),
    "fill": ("selector", "value"),
    "wait": ("ms",),
    "wait_for": ("selector",),
    "scroll_to": ("selector",),
    "highlight": ("selector", "duration_ms"),
    "pause_for_voiceover": ("ms",),
    "screenshot": (),
}

BOOL_FIELDS = ("optional", "separate_pass_allowed", "separate_pass_required")
INT_FIELDS = ("ms", "duration_ms", "focus_padding")

TRUE_WORDS = ("true", "yes", "1", "да")
FALSE_WORDS = ("false", "no", "0", "нет")


class ScenarioError(Exception):
    """Ошибка сценария. Всегда человеческим языком и всегда до запуска браузера."""


def _where(step_no, field):
    if step_no is None:
        return "поле «%s»" % field
    return "шаг %d, поле «%s»" % (step_no, field)


def _parse_bool(value, field, step_no=None, default=None):
    """Строгий bool. bool('false') is True — поэтому строки разбираем руками."""
    if value is None:
        if default is None:
            raise ScenarioError("%s: значение обязательно" % _where(step_no, field))
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in TRUE_WORDS:
            return True
        if low in FALSE_WORDS:
            return False
    raise ScenarioError(
        "%s: ожидается true или false, получено %r" % (_where(step_no, field), value)
    )


def _parse_int(value, field, step_no=None, default=None):
    """Целое число в границах LIMITS. Сырой ValueError наружу не выходит."""
    if value is None:
        if default is None:
            raise ScenarioError("%s: значение обязательно" % _where(step_no, field))
        return default
    if isinstance(value, bool):
        raise ScenarioError(
            "%s: ожидается число, получено логическое значение" % _where(step_no, field)
        )
    if isinstance(value, int):
        num = value
    elif isinstance(value, float):
        if value != int(value):
            raise ScenarioError(
                "%s: ожидается целое число, получено %r" % (_where(step_no, field), value)
            )
        num = int(value)
    elif isinstance(value, str):
        try:
            num = int(value.strip())
        except ValueError:
            raise ScenarioError(
                "%s: ожидается число, получено %r" % (_where(step_no, field), value)
            )
    else:
        raise ScenarioError(
            "%s: ожидается число, получено %r" % (_where(step_no, field), value)
        )
    low, high = LIMITS.get(field, (0, 10 ** 9))
    if num < low or num > high:
        raise ScenarioError(
            "%s: значение %d вне допустимых границ (%d..%d)"
            % (_where(step_no, field), num, low, high)
        )
    return num


def _parse_str(value, field, step_no=None, default=None):
    if value is None:
        if default is None:
            raise ScenarioError("%s: значение обязательно" % _where(step_no, field))
        return default
    if not isinstance(value, str) or not value.strip():
        raise ScenarioError(
            "%s: ожидается непустая строка, получено %r" % (_where(step_no, field), value)
        )
    return value


def group_scenes(steps):
    """Подряд идущие шаги с одним scene_id — одна сцена."""
    out = []
    for st in steps:
        if out and out[-1]["scene_id"] == st.scene_id:
            out[-1]["last_step"] = st.index
        else:
            out.append({
                "scene_id": st.scene_id,
                "first_step": st.index,
                "last_step": st.index,
                "voiceover_key": st.voiceover_key,
            })
    return out


class Step(object):
    __slots__ = (
        "index", "action", "scene_id", "target", "selector", "value",
        "ms", "duration_ms", "focus_padding", "voiceover_key", "optional",
        "separate_pass_allowed", "separate_pass_required", "name",
    )

    def to_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}


class Scenario(object):
    def __init__(self, scenario_id, title, version, fixture_version, viewport,
                 device_scale_factor, focus_padding, capture_kind, voiceover,
                 steps, source_raw, source_path):
        self.id = scenario_id
        self.title = title
        self.version = version
        self.fixture_version = fixture_version
        self.viewport = viewport
        self.device_scale_factor = device_scale_factor
        self.focus_padding = focus_padding
        self.capture_kind = capture_kind
        self.voiceover = voiceover
        self.steps = steps
        self.source_raw = source_raw
        self.source_path = source_path

    def scenes(self):
        """Сцены в порядке появления: [{scene_id, first_step, last_step, voiceover_key}]."""
        return group_scenes(self.steps)

    def to_source_dict(self):
        """Ровно то, что лежало в файле. Без единого умолчания."""
        return self.source_raw

    def to_resolved_dict(self):
        """Контракт, который реально исполнит runner. Все умолчания раскрыты."""
        return {
            "parser_version": VERSION,
            "id": self.id,
            "title": self.title,
            "version": self.version,
            "fixture_version": self.fixture_version,
            "viewport": dict(self.viewport),
            "device_scale_factor": self.device_scale_factor,
            "focus_padding": self.focus_padding,
            "capture_kind": self.capture_kind,
            "source_path": self.source_path,
            "voiceover": dict(self.voiceover),
            "scenes": self.scenes(),
            "steps": [st.to_dict() for st in self.steps],
        }


def parse_scenario(data, source_path=None):
    if not isinstance(data, dict):
        raise ScenarioError("Сценарий должен быть словарём верхнего уровня")

    scenario_id = _parse_str(data.get("id"), "id")
    if not SCENARIO_ID_RE.match(scenario_id):
        raise ScenarioError(
            "Недопустимый id сценария %r: строчные латинские буквы, цифры и подчёркивание"
            % scenario_id
        )
    title = _parse_str(data.get("title"), "title", default=scenario_id)
    version = _parse_str(data.get("version"), "version", default="1")
    fixture_version = _parse_str(
        data.get("demo_fixture_version") or data.get("fixture_version"),
        "demo_fixture_version", default="none",
    )
    capture_kind = _parse_str(data.get("capture_kind"), "capture_kind",
                              default=DEFAULT_CAPTURE_KIND)

    vp_raw = data.get("viewport") or {}
    if not isinstance(vp_raw, dict):
        raise ScenarioError("Поле «viewport» должно быть словарём с width и height")
    viewport = {
        "width": _parse_int(vp_raw.get("width"), "width", default=DEFAULT_VIEWPORT["width"]),
        "height": _parse_int(vp_raw.get("height"), "height", default=DEFAULT_VIEWPORT["height"]),
    }
    dsf = _parse_int(data.get("device_scale_factor"), "device_scale_factor", default=DEFAULT_DSF)
    base_padding = _parse_int(data.get("focus_padding"), "focus_padding",
                              default=DEFAULT_FOCUS_PADDING)

    voiceover = data.get("voiceover") or {}
    if not isinstance(voiceover, dict):
        raise ScenarioError("Поле «voiceover» должно быть словарём ключ → текст")
    for key, text in voiceover.items():
        if not isinstance(key, str) or not SCENE_ID_RE.match(key):
            raise ScenarioError("Недопустимый ключ озвучки %r" % (key,))
        if not isinstance(text, str) or not text.strip():
            raise ScenarioError("Пустой текст озвучки для ключа «%s»" % key)

    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ScenarioError("Поле «steps» должно быть непустым списком шагов")

    steps = []
    seen_scenes = []
    for i, raw in enumerate(raw_steps, start=1):
        if not isinstance(raw, dict):
            raise ScenarioError("Шаг %d должен быть словарём" % i)

        action = _parse_str(raw.get("action"), "action", i)
        if action not in ACTIONS:
            raise ScenarioError(
                "Шаг %d: неизвестное действие «%s». Допустимы: %s"
                % (i, action, ", ".join(sorted(ACTIONS)))
            )

        scene_id = _parse_str(raw.get("scene_id"), "scene_id", i)
        if not SCENE_ID_RE.match(scene_id):
            raise ScenarioError(
                "Шаг %d: недопустимый scene_id %r — строчные латинские буквы, цифры "
                "и подчёркивание, от 2 до 64 символов" % (i, scene_id)
            )
        # правило повторения: сцена собирается из подряд идущих шагов
        if not seen_scenes or seen_scenes[-1] != scene_id:
            if scene_id in seen_scenes:
                raise ScenarioError(
                    "Шаг %d: сцена «%s» уже закончилась раньше и открывается снова. "
                    "Шаги одной сцены должны идти подряд" % (i, scene_id)
                )
            seen_scenes.append(scene_id)

        st = Step()
        st.index = i
        st.action = action
        st.scene_id = scene_id
        st.target = raw.get("target")
        st.selector = raw.get("selector")
        st.value = raw.get("value")
        st.name = raw.get("name")

        for field in ACTIONS[action]:
            if raw.get(field) in (None, ""):
                raise ScenarioError(
                    "Шаг %d, действие «%s»: обязательное поле «%s» не заполнено"
                    % (i, action, field)
                )

        if st.target is not None:
            st.target = _parse_str(st.target, "target", i)
        if st.selector is not None:
            st.selector = _parse_str(st.selector, "selector", i)
        if st.value is not None:
            st.value = _parse_str(st.value, "value", i)
        if st.name is not None:
            st.name = _parse_str(st.name, "name", i)

        st.ms = _parse_int(raw.get("ms"), "ms", i, default=0)
        st.duration_ms = _parse_int(raw.get("duration_ms"), "duration_ms", i, default=0)
        st.focus_padding = _parse_int(raw.get("focus_padding"), "focus_padding", i,
                                      default=base_padding)

        st.optional = _parse_bool(raw.get("optional"), "optional", i, default=False)
        st.separate_pass_allowed = _parse_bool(
            raw.get("separate_pass_allowed"), "separate_pass_allowed", i, default=True)
        st.separate_pass_required = _parse_bool(
            raw.get("separate_pass_required"), "separate_pass_required", i, default=False)
        if st.separate_pass_required and not st.separate_pass_allowed:
            raise ScenarioError(
                "Шаг %d: separate_pass_required=true при separate_pass_allowed=false — "
                "противоречивый сценарий" % i
            )

        st.voiceover_key = raw.get("voiceover_key") or scene_id
        st.voiceover_key = _parse_str(st.voiceover_key, "voiceover_key", i)
        if st.voiceover_key not in voiceover:
            raise ScenarioError(
                "Шаг %d: ключ озвучки «%s» отсутствует в блоке voiceover"
                % (i, st.voiceover_key)
            )

        steps.append(st)

    # у сцены один ключ озвучки на все её шаги
    for scene in group_scenes(steps):
        keys = set(st.voiceover_key for st in steps if st.scene_id == scene["scene_id"])
        if len(keys) > 1:
            raise ScenarioError(
                "Сцена «%s»: разные ключи озвучки внутри одной сцены (%s)"
                % (scene["scene_id"], ", ".join(sorted(keys)))
            )

    return Scenario(scenario_id, title, version, fixture_version, viewport, dsf,
                    base_padding, capture_kind, voiceover, steps, data, source_path)


def load_scenario(path):
    if not os.path.exists(path):
        raise ScenarioError("Файл сценария не найден: %s" % path)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        raise ScenarioError("Не удалось прочитать сценарий %s: %s" % (path, exc))

    low = path.lower()
    if low.endswith(".json"):
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ScenarioError("Не удалось разобрать сценарий %s: %s" % (path, exc))
    elif low.endswith(".yaml") or low.endswith(".yml"):
        try:
            import yaml
        except ImportError:
            raise ScenarioError(
                "Не удалось разобрать сценарий %s: не установлен PyYAML. "
                "Используйте .json или установите пакет" % path
            )
        try:
            data = yaml.safe_load(text)
        except Exception as exc:
            raise ScenarioError("Не удалось разобрать сценарий %s: %s" % (path, exc))
    else:
        raise ScenarioError(
            "Не удалось разобрать сценарий %s: поддерживаются .yaml, .yml и .json" % path
        )

    return parse_scenario(data, source_path=path)
