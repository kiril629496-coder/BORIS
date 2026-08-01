# -*- coding: utf-8 -*-
"""
Ядро правил системы мониторинга источников BORIS.

Платформо-независимо: не знает, откуда пришло сообщение (Telegram, VK, форум).
На вход — текст и время публикации, на выход — совпадение, баллы и разбор баллов.

Ничего не импортирует из БД и из сети — модуль тестируется на фикстурах.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Protocol, runtime_checkable

# ------------------------------------------------------------------ константы

RULE_TYPES = ("keyword", "regex", "llm", "semantic")
# semantic и llm зарезервированы: тип принимается схемой, но в v1 не исполняется
IMPLEMENTED_RULE_TYPES = ("keyword", "regex")

# Статусы найденного сообщения (ключ в БД -> подпись для человека)
MESSAGE_STATUSES = {
    "new": "новое",
    "sent": "отправлено",
    "seen": "просмотрено",
    "lead": "полезный лид",
    "rejected": "не подходит",
    "duplicate": "дубль",
    "in_work": "в работе",
    "closed": "закрыто",
}

SCORE_BUCKETS = (
    (90, "hot", "горячий лид"),
    (70, "warm", "потенциальный клиент"),
    (40, "topic", "тематическое обсуждение"),
    (0, "noise", "шум"),
)

# Явные словоформы — стеммер на коротких служебных словах ненадёжен,
# поэтому маркеры намерения перечислены поверхностными формами.
INTENT_MARKERS = (
    "нужен", "нужна", "нужно", "нужны", "нужен ли",
    "ищу", "ищем", "ищет", "в поиске",
    "посоветуйте", "порекомендуйте", "подскажите", "подскажете",
    "требуется", "требуются", "кто может", "кто возьмет", "кто возьмёт",
    "кто занимается", "кто делает", "к кому обратиться", "где найти",
    "хочу заказать", "нужен человек", "нужен специалист",
    "looking for", "need a", "any recommendations",
)

CONTACT_MARKERS = (
    "в лс", "в личку", "в личные", "пишите", "напишите", "жду предложений",
    "жду в лс", "оставляйте контакты", "прайс в лс", "dm me",
)

# Маркер услуги — общий словарь; правило может задать свой через params["service"]
SERVICE_MARKERS = (
    "авито", "avito", "объявлен", "продвижен", "реклам", "маркетинг",
    "таргет", "директ", "seo", "smm", "сайт", "лендинг", "телеграм",
    "telegram", "вконтакте", "соцсет", "контент", "трафик", "лидогенерац",
)

DEFAULT_WEIGHTS = {
    "keyword_phrase": 35,
    "keyword_word": 25,
    "keyword_extra": 5,
    "keyword_cap": 40,
    "intent": 15,
    "service": 10,
    "contact": 5,
    "fresh_1h": 15,
    "fresh_6h": 10,
    "fresh_24h": 5,
    "source_default": 10,   # базовое доверие источнику
    "source_cap": 15,       # потолок вклада источника
    "duplicates": 0,        # РЕЗЕРВ: близкие дубли, в v1 всегда 0
}
# Максимум = 40+15+10+5+15+15 = 100. Порог «горячего лида» (90) достижим.

# Бренды пишут и кириллицей, и латиницей: приводим к одной форме
# и в тексте сообщения, и в терминах правила.
BRAND_ALIASES = {
    "avito": "авито",
    "telegram": "телеграм", "tg": "телеграм", "телега": "телеграм", "тг": "телеграм",
    "vk": "вконтакте", "вк": "вконтакте", "vkontakte": "вконтакте",
    "instagram": "инстаграм", "инст": "инстаграм", "инста": "инстаграм", "ig": "инстаграм",
    "facebook": "фейсбук", "fb": "фейсбук",
    "youtube": "ютуб", "yt": "ютуб", "ютьюб": "ютуб",
    "whatsapp": "вотсап", "wa": "вотсап", "ватсап": "вотсап",
    "olx": "олх", "listam": "листам", "linkedin": "линкедин",
    "reddit": "реддит", "dzen": "дзен", "yandex": "яндекс",
}

# Допуск между словами фразы: «массовая загрузка авито» найдёт
# «массовую загрузку НА авито». Переопределяется через params["gap"].
DEFAULT_PHRASE_GAP = 1

# ------------------------------------------------------- нормализация текста

_WORD_RE = re.compile(r"[а-яa-z0-9ёіїєґ]+", re.IGNORECASE)



def normalize_text(s: str | None) -> str:
    """Нижний регистр, ё -> е, схлопывание пробелов."""
    if not s:
        return ""
    return re.sub(r"\s+", " ", s.replace("Ё", "Е").replace("ё", "е").lower()).strip()


# ---------------------------------------------------------- слой нормализации
#
# Ядро правил НЕ содержит морфологии. Оно обращается к текущему нормализатору
# через NORMALIZER и работает с «ключами сопоставления» — что это за ключи
# (усечённая основа, лемма, вектор), решает сам нормализатор.
# Замена морфологии не требует правок в матчере и скоринге.


class Normalizer(Protocol):
    name: str

    def tokens(self, text: str | None) -> list[str]:
        """Слова текста, приведённые к единой форме написания."""
        ...

    def keys(self, tokens: list[str]) -> list[str]:
        """Ключи сопоставления той же длины, что и tokens."""
        ...


class SimpleNormalizer:
    """
    Действующая реализация: усечение частых окончаний.
    Сознательное упрощение — на редких словоформах промахивается.
    Точный контроль даётся звёздочкой в термине: «авитолог*».
    """

    name = "simple"

    _ENDINGS = (
        "иями", "ями", "ами", "иях", "ией", "ому", "его", "ему", "ыми", "ими",
        "ого", "ая", "ое", "ые", "ый", "ий", "ых", "ов", "ев", "ам", "ах",
        "ям", "ях", "ом", "ем", "ой", "ей", "ию", "ии", "ие", "ия",
        "ую", "ою", "ею", "ья", "ье", "ью",
        "а", "я", "ы", "и", "е", "о", "у", "ю", "ь",
    )
    _MIN_STEM = 4

    def tokens(self, text: str | None) -> list[str]:
        return [BRAND_ALIASES.get(t, t) for t in _WORD_RE.findall(normalize_text(text))]

    def key(self, word: str) -> str:
        for end in self._ENDINGS:
            if len(word) - len(end) >= self._MIN_STEM and word.endswith(end):
                return word[: -len(end)]
        return word

    def keys(self, tokens: list[str]) -> list[str]:
        return [self.key(t) for t in tokens]


class MorphologyNormalizer(SimpleNormalizer):
    """
    Заготовка под настоящую морфологию (pymorphy2/pymorphy3).
    Интерфейс тот же; при отсутствии библиотеки молча работает как SimpleNormalizer,
    поэтому подключение не может уронить прогон.
    """

    name = "morphology"

    def __init__(self) -> None:
        self._morph = None
        try:
            import pymorphy2  # noqa: F401
            self._morph = pymorphy2.MorphAnalyzer()
        except Exception:  # noqa: BLE001
            self._morph = None

    def key(self, word: str) -> str:
        if self._morph is None:
            return super().key(word)
        try:
            return self._morph.parse(word)[0].normal_form
        except Exception:  # noqa: BLE001
            return super().key(word)

    @property
    def is_active(self) -> bool:
        return self._morph is not None


NORMALIZER: Any = SimpleNormalizer()


def set_normalizer(n: Any) -> None:
    """Подмена слоя нормализации целиком. Rule Engine не меняется."""
    global NORMALIZER
    NORMALIZER = n


def tokenize(s: str | None) -> list[str]:
    return NORMALIZER.tokens(s)


def stem(word: str) -> str:
    return NORMALIZER.keys([word])[0]


def stem_all(tokens: Iterable[str]) -> list[str]:
    return NORMALIZER.keys(list(tokens))


# --------------------------------------------------------------- сопоставление

@dataclass
class TermHit:
    term: str
    kind: str          # "phrase" | "word"


def _term_matches(term: str, tokens: list[str], stems: list[str], gap: int = DEFAULT_PHRASE_GAP) -> bool:
    """
    Термин может быть:
      «авитолог»    — совпадение по усечённой основе
      «авитолог*»   — совпадение по префиксу (точный контроль)
      «нужен авитолог» — фраза, подряд идущие слова
    """
    term_n = normalize_text(term)
    if not term_n:
        return False

    parts = [BRAND_ALIASES.get(x, x) for x in term_n.split()]
    if len(parts) == 1:
        p = parts[0]
        if p.endswith("*"):
            pref = p[:-1]
            return any(t.startswith(pref) for t in tokens)
        return stem(p) in stems

    # фраза: слова в исходном порядке, между ними допускается до gap слов
    want, exact_flags = [], []
    for p in parts:
        if p.endswith("*"):
            want.append(p[:-1]); exact_flags.append(True)
        else:
            want.append(stem(p)); exact_flags.append(False)

    def _hit(pos: int, j: int) -> bool:
        if exact_flags[j]:
            return tokens[pos].startswith(want[j])
        return stems[pos] == want[j]

    n = len(want)
    for start in range(len(tokens)):
        if not _hit(start, 0):
            continue
        pos, ok = start, True
        for j in range(1, n):
            found = -1
            for step in range(1, gap + 2):
                cand = pos + step
                if cand >= len(tokens):
                    break
                if _hit(cand, j):
                    found = cand
                    break
            if found < 0:
                ok = False
                break
            pos = found
        if ok:
            return True
    return False


def match_keyword(text: str, params: dict[str, Any]) -> tuple[bool, list[TermHit]]:
    """
    params = {"any": [...], "all": [...], "not": [...]}
    ANY  — достаточно одного (если список пуст, условие считается выполненным)
    ALL  — нужны все
    NOT  — ни одного
    """
    tokens = tokenize(text)
    stems = stem_all(tokens)
    if not tokens:
        return False, []
    gap = int(params.get("gap", DEFAULT_PHRASE_GAP))

    any_terms = [t for t in (params.get("any") or []) if t]
    all_terms = [t for t in (params.get("all") or []) if t]
    not_terms = [t for t in (params.get("not") or []) if t]

    for t in not_terms:
        if _term_matches(t, tokens, stems, gap):
            return False, []

    hits: list[TermHit] = []

    if any_terms:
        for t in any_terms:
            if _term_matches(t, tokens, stems, gap):
                hits.append(TermHit(t, "phrase" if " " in t.strip() else "word"))
        if not hits:
            return False, []

    if all_terms:
        for t in all_terms:
            if not _term_matches(t, tokens, stems, gap):
                return False, []
            hits.append(TermHit(t, "phrase" if " " in t.strip() else "word"))

    if not any_terms and not all_terms:
        return False, []

    return True, hits


def match_regex(text: str, params: dict[str, Any]) -> tuple[bool, list[TermHit]]:
    """params = {"pattern": "...", "flags": "i"} — флаги строкой: i, m, s."""
    pattern = params.get("pattern")
    if not pattern:
        return False, []
    flags = 0
    for ch in (params.get("flags") or ""):
        flags |= {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL}.get(ch, 0)
    try:
        m = re.search(pattern, text or "", flags)
    except re.error:
        return False, []
    if not m:
        return False, []
    return True, [TermHit(m.group(0)[:120], "phrase")]


# ------------------------------------------------------------------- правило

@dataclass
class SearchRule:
    """
    Платформо-независимое правило поиска.
    Соответствует строке monitor_rules; создаётся из неё функцией from_row().
    """
    id: str
    name: str
    category: str = ""
    rule_type: str = "keyword"
    params: dict[str, Any] = field(default_factory=dict)
    platforms: list[str] | None = None       # None = любые
    source_ids: list[str] | None = None      # None = любые
    source_kinds: list[str] | None = None    # None = любые (channel/group/topic...)
    weights: dict[str, int] = field(default_factory=dict)
    min_score: int = 40
    is_active: bool = True

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "SearchRule":
        return cls(
            id=str(row.get("id")),
            name=row.get("name") or "",
            category=row.get("category") or "",
            rule_type=row.get("rule_type") or "keyword",
            params=row.get("params") or {},
            platforms=row.get("platforms"),
            source_ids=row.get("source_ids"),
            source_kinds=row.get("source_kinds"),
            weights=row.get("weights") or {},
            min_score=int(row.get("min_score") if row.get("min_score") is not None else 40),
            is_active=bool(row.get("is_active", True)),
        )

    def applies_to(self, platform: str, source_id: str | None,
                   source_kind: str | None = None) -> bool:
        if not self.is_active:
            return False
        if self.platforms and platform not in self.platforms:
            return False
        if self.source_ids and source_id and source_id not in self.source_ids:
            return False
        if self.source_kinds and source_kind and source_kind not in self.source_kinds:
            return False
        return True

    def match(self, text: str) -> tuple[bool, list[TermHit]]:
        if self.rule_type == "keyword":
            return match_keyword(text, self.params)
        if self.rule_type == "regex":
            return match_regex(text, self.params)
        # llm / semantic зарезервированы — в v1 не срабатывают никогда
        return False, []


# ------------------------------------------------------------------- скоринг

def _hours_since(posted_at: datetime | None, now: datetime | None = None) -> float | None:
    if not posted_at:
        return None
    now = now or datetime.now(timezone.utc)
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=timezone.utc)
    return (now - posted_at).total_seconds() / 3600.0


def score_message(
    text: str,
    hits: list[TermHit],
    posted_at: datetime | None = None,
    weights: dict[str, int] | None = None,
    service_markers: Iterable[str] | None = None,
    source_weight: int | None = None,
    now: datetime | None = None,
) -> tuple[int, dict[str, int]]:
    """
    Возвращает (score_total, score_breakdown).
    breakdown всегда содержит один и тот же набор ключей, включая ai=0,
    зарезервированный под AI-оценку второго этапа.
    """
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        w.update({k: int(v) for k, v in weights.items() if k in DEFAULT_WEIGHTS})

    low = normalize_text(text)
    br = {"keyword": 0, "intent": 0, "service": 0, "contact": 0,
          "freshness": 0, "source": 0, "duplicates": 0, "ai": 0}

    if hits:
        base = w["keyword_phrase"] if any(h.kind == "phrase" for h in hits) else w["keyword_word"]
        extra = w["keyword_extra"] * max(0, len({h.term for h in hits}) - 1)
        br["keyword"] = min(w["keyword_cap"], base + extra)

    if any(m in low for m in INTENT_MARKERS):
        br["intent"] = w["intent"]

    markers = tuple(service_markers) if service_markers else SERVICE_MARKERS
    if any(m in low for m in markers):
        br["service"] = w["service"]

    if any(m in low for m in CONTACT_MARKERS):
        br["contact"] = w["contact"]

    hrs = _hours_since(posted_at, now)
    if hrs is not None:
        if hrs <= 1:
            br["freshness"] = w["fresh_1h"]
        elif hrs <= 6:
            br["freshness"] = w["fresh_6h"]
        elif hrs <= 24:
            br["freshness"] = w["fresh_24h"]

    sw = w["source_default"] if source_weight is None else int(source_weight)
    br["source"] = max(0, min(w["source_cap"], sw))

    br["duplicates"] = w["duplicates"]   # РЕЗЕРВ: близкие дубли, в v1 всегда 0

    total = min(100, max(0, sum(br.values())))
    return total, br


def bucket(score: int) -> tuple[str, str]:
    """Возвращает (ключ, подпись) — hot / warm / topic / noise."""
    for threshold, key, label in SCORE_BUCKETS:
        if score >= threshold:
            return key, label
    return "noise", "шум"


def priority_of(score: int) -> str:
    key, _ = bucket(score)
    return {"hot": "high", "warm": "medium", "topic": "low", "noise": "low"}[key]


def guess_language(text: str) -> str:
    """Грубое определение: доля кириллицы. Только ru/en/unknown."""
    letters = [c for c in (text or "") if c.isalpha()]
    if not letters:
        return "unknown"
    cyr = sum(1 for c in letters if "\u0400" <= c <= "\u04ff")
    share = cyr / len(letters)
    if share >= 0.5:
        return "ru"
    if share <= 0.1:
        return "en"
    return "unknown"


# ------------------------------------------------------- прогон набора правил

def rules_hash(rules: list["SearchRule"]) -> str:
    """
    Отпечаток действующего набора правил. Пишется в monitor_messages.rules_hash,
    чтобы через месяц было видно, по какой версии правил найден лид.
    """
    payload = json.dumps(
        [
            {"id": r.id, "type": r.rule_type, "params": r.params,
             "weights": r.weights, "min_score": r.min_score,
             "platforms": r.platforms, "source_kinds": r.source_kinds,
             "active": r.is_active}
            for r in sorted(rules, key=lambda x: str(x.id))
        ],
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass
class MatchResult:
    matched: bool
    rule: SearchRule | None
    terms: list[str]
    score_total: int
    score_breakdown: dict[str, int]
    language: str
    priority: str
    bucket_key: str
    bucket_label: str
    matched_rules: list[dict[str, Any]] = field(default_factory=list)


def evaluate(
    text: str,
    rules: list[SearchRule],
    platform: str,
    source_id: str | None = None,
    source_kind: str | None = None,
    source_weight: int | None = None,
    posted_at: datetime | None = None,
    now: datetime | None = None,
) -> MatchResult:
    """
    Прогоняет сообщение по ВСЕМ правилам.

    Лучшее совпадение возвращается в rule/score_total — по нему строится карточка.
    Полный список сработавших правил — в matched_rules: он объясняет менеджеру,
    почему сообщение стало лидом, и нужен при отладке правил.
    """
    all_hits: list[dict[str, Any]] = []
    best: MatchResult | None = None

    for rule in rules:
        if not rule.applies_to(platform, source_id, source_kind):
            continue
        ok, hits = rule.match(text)
        if not ok:
            continue
        total, br = score_message(
            text, hits, posted_at=posted_at,
            weights=rule.weights,
            service_markers=(rule.params.get("service") or None),
            source_weight=source_weight,
            now=now,
        )
        if total < rule.min_score:
            # правило сработало, но не добрало собственный порог — фиксируем для отладки
            all_hits.append({
                "rule_id": rule.id, "rule_name": rule.name, "category": rule.category,
                "terms": [h.term for h in hits], "score": total,
                "passed": False, "reason": f"ниже min_score {rule.min_score}",
            })
            continue

        all_hits.append({
            "rule_id": rule.id, "rule_name": rule.name, "category": rule.category,
            "terms": [h.term for h in hits], "score": total, "passed": True,
        })
        key, label = bucket(total)
        res = MatchResult(
            matched=True, rule=rule, terms=[h.term for h in hits],
            score_total=total, score_breakdown=br,
            language=guess_language(text), priority=priority_of(total),
            bucket_key=key, bucket_label=label,
        )
        if best is None or res.score_total > best.score_total:
            best = res

    if best:
        best.matched_rules = all_hits
        return best

    return MatchResult(
        matched=False, rule=None, terms=[], score_total=0,
        score_breakdown={"keyword": 0, "intent": 0, "service": 0, "contact": 0,
                         "freshness": 0, "source": 0, "duplicates": 0, "ai": 0},
        language=guess_language(text), priority="low",
        bucket_key="noise", bucket_label="шум",
        matched_rules=all_hits,
    )
