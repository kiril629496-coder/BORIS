# -*- coding: utf-8 -*-
"""BORIS Social quality-first pipeline.

This module is deliberately independent from transport. It creates the shared
content/visual brief, rejects weak or repetitive copy before publication, runs
fail-closed vision QA for the exact generated banner, stores owner feedback and
computes the 10-slot quality acceptance snapshot.

Policy: SOCIAL_QUALITY_V1 / SOCIAL_QUALITY_BEATS_SCHEDULE.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import re
from datetime import datetime, timedelta, timezone
from app.services.social_editorial_contract import (
    project_local_now, parse_reference_date, seasonal_hits,
    public_text_for_qa, strict_qa_flags, occasion_blockers,
)
from difflib import SequenceMatcher
from typing import Any

from app.db.session import SessionLocal
from app.models.storage import Storage
from app.services.exception_observability import observe_suppressed

POLICY_VERSION = "SOCIAL_QUALITY_V1"
VISION_CONTRACT_VERSION = "SOCIAL_VISION_PROJECT_SCOPE_V2"
FINAL_PAYLOAD_QA_VERSION = "SOCIAL_FINAL_PAYLOAD_QA_V1"
COPY_DELETION_VERSION = "SOCIAL_OPTIONAL_COPY_DELETION_V1"
QUALITY_BEATS_SCHEDULE = True
MAX_RECENT_POSTS = 60
COPY_REGEN_MAX = 2
BANNER_REGEN_MAX = 2
BANNER_ACCEPTED_TOTAL_COST_CAP_RUB = 4.0

FORMATS = [
    "полезный совет",
    "разбор ошибки",
    "сравнение двух вариантов",
    "миф и факт",
    "разбор конкретного дизайна",
    "мини-гайд",
    "вопрос клиента",
    "практический уход",
    "экспертное наблюдение",
    "лёгкий юмор",
    "образовательный пост",
    "продающий пост",
]

_WEAK_PHRASES = (
    "индивидуальный подход",
    "качество и надёжность",
    "лучшие решения",
    "идеальный вариант",
    "красиво и стильно",
    "высокое качество",
    "широкий спектр",
    "наша команда профессионалов",
)

_META_RE = re.compile(
    r"(?im)^\s*(?:тема|тон|структура|формат(?:\s+подачи)?|услуга|"
    r"форма\s+ногтей|длина|дизайн|основн\w*\s+цвет|призыв(?:\s+в\s+конце)?|"
    r"cta|хэштеги?|теги?|для\s+изображения|визуальн\w*\s+стиль)\s*[:—-]"
)
_HASH_RE = re.compile(r"(?<!\w)#[A-Za-zА-Яа-яЁё0-9_]+", re.U)
_HTML_RE = re.compile(r"<[^>]+>")
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+", re.U)


def _plain(value: Any) -> str:
    text = str(value or "").replace("\r", "")
    text = _HTML_RE.sub(" ", text)
    text = _HASH_RE.sub(" ", text)
    text = re.sub(r"https?://\S+|@[A-Za-zА-Яа-яЁё0-9_.-]+", " ", text, flags=re.U)
    text = re.sub(r"\s+", " ", text).strip().lower().replace("ё", "е")
    return text


def _words(value: Any) -> list[str]:
    return [x.lower().replace("ё", "е") for x in _WORD_RE.findall(_plain(value)) if len(x) > 1]


def _title(value: Any) -> str:
    raw = str(value or "").strip()
    first = next((x.strip() for x in raw.splitlines() if x.strip()), "")
    first = _HTML_RE.sub("", first)
    first = first.replace("**", "").replace("__", "").strip(" .:-")
    return first[:160]


def _last_cta(value: Any) -> str:
    lines = []
    for line in str(value or "").splitlines():
        clean = _HTML_RE.sub("", line).strip()
        if not clean or clean.startswith("#"):
            continue
        lines.append(clean)
    return lines[-1][:220] if lines else ""


def _created_at(post: dict) -> datetime | None:
    raw = str(post.get("published_at") or post.get("created_at") or "").strip()
    if not raw:
        return None
    for candidate in (raw, raw.replace("Z", "+00:00")):
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is not None:
                return dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except ValueError:
            continue
    try:
        return datetime.strptime(raw[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def recent_project_posts(account_id: str, project_id: str, limit: int = MAX_RECENT_POSTS) -> list[dict]:
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(
            Storage.account_id == str(account_id),
            Storage.key == "posting_posts",
        ).first()
        try:
            posts = json.loads(row.value or "[]") if row else []
        except Exception:
            posts = []
    finally:
        db.close()
    out = [
        dict(p) for p in posts
        if isinstance(p, dict) and str(p.get("project_id") or "") == str(project_id or "")
    ]
    out.sort(key=lambda p: str(p.get("published_at") or p.get("created_at") or ""), reverse=True)
    return out[: max(1, min(100, int(limit or MAX_RECENT_POSTS)))]


def _season(month: int) -> tuple[str, list[str], list[str]]:
    month = int(month)
    if month in (10, 11, 12, 1, 2):
        return (
            "осень-зима",
            [
                "сезонный уход", "практичность", "закрытая обувь", "сухость кожи",
                "деловой и повседневный образ", "тёплые и глубокие оттенки",
            ],
            [
                "море", "пляж", "купание", "солёная вода", "соленая вода", "жара",
                "летний отпуск", "шезлонг", "ракушк", "пальм", "летняя туристическая эстетика",
            ],
        )
    if month in (3, 4, 5):
        return ("весна", ["обновление образа", "свежие оттенки", "лёгкий дизайн"], [])
    return (
        "лето",
        ["летний уход", "открытая обувь", "стойкость покрытия", "отпускной образ"],
        [],
    )


def _base_theme(theme: Any) -> str:
    value = str(theme or "")
    value = value.split(". Услуга:", 1)[0]
    value = value.split(". Для ИЗОБРАЖЕНИЯ", 1)[0]
    return value.strip(" .:-")


def _audience(project: dict) -> str:
    prompt = str((project or {}).get("text_prompt") or "")
    m = re.search(r"(?im)^\s*АУДИТОРИЯ\s*\n([^\n]+)", prompt)
    if not m:
        m = re.search(r"(?im)^\s*Аудитория\s*:\s*([^\n]+)", prompt)
    return (m.group(1).strip() if m else "клиенты этого бизнеса")[:500]


def _content_formats(project: dict) -> list[str]:
    """Keep beauty formats in their niche; classify the project, never its topic."""
    from app.services.social_owner_policy_v3 import is_ocean, is_dushi, is_manicure

    if is_manicure(project):
        return list(FORMATS)
    if is_ocean(project):
        replacements = {
            "разбор конкретного дизайна": "разбор бизнес-задачи",
            "практический уход": "разбор рабочего процесса",
        }
    elif is_dushi(project):
        replacements = {
            "разбор конкретного дизайна": "разбор поздравления",
            "практический уход": "подбор слов по личной истории",
        }
    else:
        return [fmt for fmt in FORMATS if fmt not in {
            "разбор конкретного дизайна", "практический уход",
        }]
    return [replacements.get(fmt, fmt) for fmt in FORMATS]


def _least_recent_format(recent: list[dict], seed: str, *, project: dict | None = None) -> str:
    formats = _content_formats(project or {})
    used = []
    for p in recent[:12]:
        brief = p.get("content_brief") if isinstance(p.get("content_brief"), dict) else {}
        fmt = str(brief.get("format") or "").strip()
        if fmt:
            used.append(fmt)
    candidates = [x for x in formats if x not in used[:2]]
    if not candidates:
        candidates = formats
    digest = hashlib.sha256(str(seed).encode("utf-8", "ignore")).hexdigest()
    return candidates[int(digest[:8], 16) % len(candidates)]


def _uses_editorial_banner_headline(project: dict) -> bool:
    """Only the two editorial projects; keep the approved nail art direction."""
    from app.services.social_owner_policy_v3 import is_ocean, is_dushi, is_manicure

    return not is_manicure(project) and (is_ocean(project) or is_dushi(project))


def _vision_project_contract(project: dict) -> dict:
    """A semantic contract revision, never a release/time based retry epoch."""
    from app.services.social_owner_policy_v3 import is_ocean, is_dushi, is_manicure

    project = project or {}
    name = str(project.get("name") or "").lower()
    if is_manicure(project) or "маникюр" in name or "педикюр" in name:
        profile = "nail_premium"
    elif project.get("owner_reference_niche"):
        profile = "reference_premium"
    elif is_ocean(project):
        profile = "ocean_business"
    elif is_dushi(project):
        profile = "dushi_personal"
    else:
        profile = "project_editorial"
    return {"version": VISION_CONTRACT_VERSION, "profile": profile}


def _recent_visual_headlines(recent: list[dict], project: dict, reference_date) -> list[str]:
    """Bounded project-local memory for NEW briefs, without another DB read.

    Include queued and partially delivered work as well as published work.
    A missing historical date is not permission to reuse its visible heading.
    """
    day = parse_reference_date(reference_date)
    project_id = str(project.get("id") or "")
    headlines = []
    for post in recent[:MAX_RECENT_POSTS]:
        if not isinstance(post, dict):
            continue
        if post.get("project_id") and str(post["project_id"]) != project_id:
            continue
        created = _created_at(post)
        if created is not None:
            local_day = project_local_now(project, created.replace(tzinfo=timezone.utc)).date()
            if (day - local_day).days > 14:
                continue
        visual = post.get("visual_brief") if isinstance(post.get("visual_brief"), dict) else {}
        headline = str(visual.get("headline") or "").strip()[:160]
        if headline and headline not in headlines:
            headlines.append(headline)
        if len(headlines) >= 30:
            break
    return headlines


def _feedback_preferences(account_id: str, project_id: str, limit: int = 30) -> dict:
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(
            Storage.account_id == str(account_id),
            Storage.key == "social_feedback:" + str(project_id),
        ).first()
        try:
            items = json.loads(row.value or "[]") if row else []
        except Exception:
            items = []
    finally:
        db.close()
    items = [x for x in items if isinstance(x, dict)][:limit]
    dislikes: dict[str, int] = {}
    likes = 0
    for item in items:
        if item.get("rating") == "good":
            likes += 1
        elif item.get("rating") == "weak":
            for reason in item.get("reasons") or []:
                key = str(reason or "").strip()
                if key:
                    dislikes[key] = dislikes.get(key, 0) + 1
    return {"likes": likes, "dislikes": dislikes, "recent": items[:10]}


def build_content_brief(
    account_id: str,
    project: dict,
    theme: str,
    *,
    recent_posts: list[dict] | None = None,
    now: datetime | None = None,
    reference_date=None,
    occasion: dict | None = None,
) -> dict:
    project = dict(project or {})
    project_id = str(project.get("id") or "")
    recent = list(recent_posts) if isinstance(recent_posts, list) else recent_project_posts(account_id, project_id)
    local = project_local_now(project, now)
    editorial_day = parse_reference_date(reference_date) if reference_date is not None else local.date()
    season, seasonal_priorities, seasonal_forbidden = _season(editorial_day.month)
    _business_name = str(project.get("name") or "").lower().replace("ё", "е")
    _is_nail_business = (
        "маникюр" in _business_name
        or "педикюр" in _business_name
        or str(project_id) == "270dbf472f7f"
    )
    if not _is_nail_business:
        # Season/date is universal context. Closed-shoe/sea/nail priorities are
        # not universal business rules: a future hotel or travel account must
        # not inherit the manicure exclusions.
        seasonal_priorities = []
        seasonal_forbidden = []
    configured_forbidden = configured_seasonal_mentions(project, editorial_day.month)
    base = _base_theme(theme)
    fmt = _least_recent_format(
        recent, project_id + "|" + base + "|" + editorial_day.isoformat(), project=project,
    )

    tone = "живой, уверенный, конкретный"
    low_prompt = str(project.get("text_prompt") or "").lower()
    if "тёпл" in low_prompt or "тепл" in low_prompt:
        tone = "тёплый, человечный, конкретный"
    if "эксперт" in low_prompt:
        tone = "экспертный, понятный, без канцелярита"

    cta_options = [
        "спокойный вывод без призыва",
        "сохранить идею",
        "выбрать один из двух вариантов",
        "короткий вопрос по теме",
    ]
    previous_ctas = [
        str((p.get("content_brief") or {}).get("cta") or "")
        for p in recent[:2] if isinstance(p.get("content_brief"), dict)
    ]
    cta_candidates = [x for x in cta_options if x not in previous_ctas] or cta_options
    cta = cta_candidates[int(hashlib.sha256((base + project_id).encode()).hexdigest()[:8], 16) % len(cta_candidates)]

    if "океан клиент" in str(project.get("name") or "").lower():
        cta = "Утверждённый коммерческий CTA подставляет система при доставке; не дублировать его в основном тексте"
    feedback = _feedback_preferences(account_id, project_id)
    forbidden = list(dict.fromkeys(seasonal_forbidden + configured_forbidden))

    brief = {
        "policy_version": POLICY_VERSION,
        "vision_qa_contract": _vision_project_contract(project),
        "business": str(project.get("name") or project_id or "проект")[:240],
        "project_id": project_id,
        "audience": _audience(project),
        "platform": str(project.get("platforms") or "both"),
        "current_date": editorial_day.isoformat(),
        "occasion": dict(occasion or {"kind": "evergreen"}),
        "season": season,
        "selected_theme": base,
        "client_problem": base,
        "main_thesis": base,
        "evidence_rule": "использовать только подтверждённые факты из проекта; не додумывать цены, отзывы, сроки и гарантии",
        "format": fmt,
        "emotional_tone": tone,
        "cta": cta,
        "visual_idea": base,
        "seasonal_priorities": seasonal_priorities,
        "forbidden_mentions": forbidden,
        "feedback_preferences": {
            "avoid": [k for k, v in sorted(feedback["dislikes"].items(), key=lambda kv: kv[1], reverse=True)[:5]],
            "positive_count": feedback["likes"],
        },
    }
    if _uses_editorial_banner_headline(project):
        brief["recent_visual_headlines"] = _recent_visual_headlines(recent, project, editorial_day)
    return brief


def content_brief_prompt(brief: dict) -> str:
    b = dict(brief or {})
    visual_headlines = b.get("recent_visual_headlines")
    headline_rule = ""
    if isinstance(visual_headlines, list):
        headline_rule = (
            "Первая строка — законченный конкретный заголовок по смыслу поста, не длиннее 80 знаков. "
            "Он может стать заголовком баннера; не обрывай фразу и не заменяй её общим слоганом.\n"
            "Недавние заголовки баннеров не повторять: "
            + json.dumps(visual_headlines[:10], ensure_ascii=False) + "\n"
        )
    return (
        "\n\nSOCIAL_CONTENT_BRIEF — единый контракт текста и изображения. "
        "Не выводи названия полей в публичный пост.\n"
        f"Бизнес: {b.get('business','')}\n"
        f"Аудитория: {b.get('audience','')}\n"
        f"Дата публикации в часовом поясе проекта: {b.get('current_date','')}; сезон: {b.get('season','')}\n"
        f"Подтверждённый повод: {json.dumps(b.get('occasion') or {'kind': 'evergreen'}, ensure_ascii=False)}\n"
        "Для будущего повода допустима подготовка к указанной дате; нельзя поздравлять как с уже наступившим. "
        "Без подтверждённой даты допустим пример поздравления с личным событием, но нельзя утверждать, что праздник сегодня.\n"
        f"Одна тема: {b.get('selected_theme','')}\n"
        f"Проблема/выбор клиента: {b.get('client_problem','')}\n"
        f"Главный тезис: {b.get('main_thesis','')}\n"
        f"Формат: {b.get('format','')}\n"
        f"Тон: {b.get('emotional_tone','')}\n"
        f"Финал/CTA: {b.get('cta','')}\n"
        f"Сезонные приоритеты: {', '.join(b.get('seasonal_priorities') or [])}\n"
        f"Запрещено упоминать без прямой команды владельца: {', '.join(b.get('forbidden_mentions') or []) or 'нет дополнительных запретов'}\n"
        f"Из обратной связи владельца избегать: {', '.join((b.get('feedback_preferences') or {}).get('avoid') or []) or 'нет накопленных запретов'}\n"
        + headline_rule
        + "Текст обязан раскрыть одну мысль: контекст/проблема → одно конкретное объяснение → естественный вывод. "
        "Если факта нет в проекте, не выдумывай его."
    )


def _jaccard(a: str, b: str) -> float:
    aw, bw = set(_words(a)), set(_words(b))
    if not aw or not bw:
        return 0.0
    return len(aw & bw) / max(1, len(aw | bw))


def semantic_similarity(a: str, b: str) -> float:
    pa, pb = _plain(a), _plain(b)
    if not pa or not pb:
        return 0.0
    seq = SequenceMatcher(None, pa[:2400], pb[:2400]).ratio()
    jac = _jaccard(pa, pb)
    # Weighted lexical-semantic approximation. It is deterministic, free and
    # conservative enough to stop obvious near-duplicate Social copy.
    return round(max(seq, (0.55 * jac + 0.45 * seq)), 4)


def evaluate_copy(
    text: str,
    brief: dict,
    project: dict,
    recent_posts: list[dict] | None = None,
    now: datetime | None = None,
) -> dict:
    raw = str(text or "").strip()
    plain = _plain(raw)
    issues: list[str] = []
    blockers: list[str] = []
    score = 100

    if len(plain) < 120:
        blockers.append("copy_too_short")
        score -= 35
    if len(plain) > 2200:
        issues.append("copy_too_long")
        score -= 15
    if _META_RE.search(raw):
        blockers.append("prompt_metadata_leak")
        score -= 50

    low = plain
    forbidden_hits = seasonal_hits(raw, brief.get("forbidden_mentions") or [])
    temporal_issues = occasion_blockers(brief.get("occasion"), brief.get("current_date"))
    if temporal_issues:
        blockers.extend(temporal_issues)
        score -= 50
    if forbidden_hits:
        blockers.append("season_forbidden:" + ",".join(forbidden_hits[:4]))
        score -= 50

    # New briefs carry their own frozen visual-heading history. Checking here
    # lets the existing bounded writer/repair loop fix a heading BEFORE image
    # generation. Historical briefs without this marker keep their contract.
    if isinstance(brief.get("recent_visual_headlines"), list) and _uses_editorial_banner_headline(project):
        try:
            _editorial_banner_headline(brief, raw)
        except RuntimeError as exc:
            if not str(exc).startswith("SOCIAL_COPY_QUALITY_BLOCKED:headline_requires_unique_complete_short_rewrite"):
                raise
            blockers.append("banner_headline_requires_unique_complete_short_rewrite")
            score -= 40

    weak_hits = [x for x in _WEAK_PHRASES if x in low]
    if len(weak_hits) >= 2:
        blockers.append("generic_advertising_copy")
        score -= 30
    elif weak_hits:
        issues.append("generic_phrase:" + weak_hits[0])
        score -= 8

    paragraphs = [x.strip() for x in re.split(r"\n\s*\n", raw) if x.strip()]
    if len(paragraphs) < 2:
        issues.append("weak_structure")
        score -= 12

    words = _words(raw)
    if len(set(words)) < 18:
        issues.append("low_information_density")
        score -= 12

    recent = list(recent_posts or [])
    now = now or datetime.now(timezone.utc)
    now = (now.astimezone(timezone.utc) if now.tzinfo else now).replace(tzinfo=None)
    max_similarity = 0.0
    max_title_similarity = 0.0
    duplicate_post_id = None
    candidate_title = _title(raw)
    for post in recent[:MAX_RECENT_POSTS]:
        prev = str(post.get("text") or post.get("preview") or "")
        if not prev:
            continue
        created = _created_at(post)
        age_days = (now - created).total_seconds() / 86400.0 if created else 0
        sim = semantic_similarity(raw, prev)
        title_sim = semantic_similarity(candidate_title, _title(prev)) if candidate_title else 0.0
        if 0 <= age_days <= 7 and sim > max_similarity:
            max_similarity = sim
            duplicate_post_id = post.get("id")
        if 0 <= age_days <= 14:
            max_title_similarity = max(max_title_similarity, title_sim)
        if 0 <= age_days <= 7 and sim >= 0.82:
            blockers.append("semantic_repeat_7d")
            score -= 45
            break
    if max_title_similarity >= 0.90:
        blockers.append("headline_repeat_14d")
        score -= 30

    current_cta = _last_cta(raw)
    previous_ctas = [_last_cta(p.get("text") or "") for p in recent[:2]]
    if ("океан клиент" not in str((project or {}).get("name") or "").lower()
            and current_cta and current_cta in previous_ctas):
        issues.append("cta_repeated")
        score -= 10

    # For the nail niche, the public text must match service semantics and must
    # never expose summer sea language during the cold-season brief.
    name = str((project or {}).get("name") or "").lower()
    theme = str(brief.get("selected_theme") or "").lower()
    if "маникюр" in name and "педикюр" in theme and "маникюр" in low and "педикюр" not in low:
        blockers.append("service_semantic_mismatch")
        score -= 40

    score = max(0, min(100, int(score)))
    passed = not blockers and score >= 70
    return {
        "policy_version": POLICY_VERSION,
        "pass": bool(passed),
        "score": score,
        "issues": list(dict.fromkeys(issues))[:12],
        "blockers": list(dict.fromkeys(blockers))[:12],
        "max_similarity": round(max_similarity, 4),
        "max_title_similarity": round(max_title_similarity, 4),
        "duplicate_post_id": duplicate_post_id,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def copy_repair_prompt(base_prompt: str, draft: str, verdict: dict, brief: dict, attempt: int) -> str:
    problems = list(verdict.get("blockers") or []) + list(verdict.get("issues") or [])
    return (
        str(base_prompt or "")
        + content_brief_prompt(brief)
        + "\n\nEDITOR QUALITY REPAIR. Перепиши черновик полностью, а не косметически. "
          "Сохрани только подтверждённые факты. Устрани перечисленные проблемы. "
          "Новый текст должен отличаться по первой фразе, структуре и финалу от старого. "
          "Верни только готовый публичный пост.\n"
        + f"Попытка редактора: {int(attempt)} из {COPY_REGEN_MAX}.\n"
        + "Проблемы: " + ", ".join(problems[:12]) + "\n"
        + "Черновик, который нельзя копировать дословно:\n" + str(draft or "")[:3500]
    )


def _editorial_banner_headline(brief: dict, text: str) -> str:
    """Use a complete current heading, never a generic niche slogan.

    Both candidates already belong to the accepted post intent. Do not invent
    a quote, occasion/date, benefit or shortened fragment to fill the banner.
    """
    history = brief.get("recent_visual_headlines")
    history = history if isinstance(history, list) else []
    # Unlike _title(), preserve the original suffix until the completeness
    # check: _title strips ASCII '...' and could conceal a clipped sentence.
    first_line = next((line.strip() for line in str(text or "").splitlines() if line.strip()), "")
    first_line = _HTML_RE.sub("", first_line).replace("**", "").replace("__", "")
    candidates = (first_line, _base_theme(brief.get("selected_theme")))
    for value in candidates:
        edged = re.sub(r"^[^\w]+|[^\w?!.…]+$", "", str(value or "").strip(), flags=re.U)
        if re.search(r"(?:\.{2,}|…)\s*$", edged):
            continue
        # Decorative edge emoji/quotes are not part of the banner's wording.
        candidate = re.sub(r"^[^\w]+|[^\w?!]+$", "", edged, flags=re.U)
        if not candidate or len(candidate) > 80 or len(_words(candidate)) < 2:
            continue
        if re.search(r"(?i)\b(?:и|или|но|что|чтобы|не|в|на|с|к|для|без|от|при|между|обязаны|должны|быть|когда|если|где|который|которые|которых|чем|пока)\s*[?!]?$", candidate):
            continue
        if any(semantic_similarity(candidate, previous) >= 0.90 for previous in history if previous):
            continue
        return candidate
    raise RuntimeError("SOCIAL_COPY_QUALITY_BLOCKED:headline_requires_unique_complete_short_rewrite")


def _headline_from_brief(brief: dict, text: str) -> str:
    project = {"name": brief.get("business"), "id": brief.get("project_id")}
    if _uses_editorial_banner_headline(project):
        return _editorial_banner_headline(brief, text)
    theme = str(brief.get("selected_theme") or "").strip()
    low = theme.lower().replace("ё", "е")
    if "педикюр" in low and "осен" in low:
        return "Педикюр осенью"
    if "кутикул" in low:
        return "Уход за кутикулой"
    if "молоч" in low:
        return "Молочный маникюр"
    if "коротк" in low and "ног" in low:
        return "Короткие ногти"
    if any(x in low for x in ("бордо", "шоколад", "оттен")):
        month = parse_reference_date(brief["current_date"]).month if brief.get("current_date") else None
        return "Осенние оттенки" if month in (9, 10, 11) else "Глубокие оттенки"
    if "френч" in low:
        return "Френч без скуки"
    if "crm" in low:
        return "Лиды без потерь"
    if any(x in low for x in ("авито", "объявлен")):
        return "Объявления, которые работают"
    if any(x in low for x in ("звон", "телефон")):
        return "Не теряйте звонки"
    if any(x in low for x in ("поздрав", "тост", "речь", "слова")):
        return "Слова по вашей истории"
    title = _title(text)
    words = title.split()
    if title and len(title) <= 80:
        return title
    business = str(brief.get("business") or "").lower()
    if "маникюр" in business or "педикюр" in business:
        if "педикюр" in low or "стоп" in low:
            return "Педикюр для реальной жизни"
        return "Маникюр для реальной жизни"
    raise RuntimeError("SOCIAL_COPY_QUALITY_BLOCKED:headline_requires_complete_short_rewrite")


def build_visual_brief(brief: dict, text: str, project: dict) -> dict:
    theme = str(brief.get("selected_theme") or "").lower().replace("ё", "е")
    name = str((project or {}).get("name") or "").lower()
    main_object = "один конкретный визуальный объект по теме"
    action = "спокойная профессиональная сцена"
    composition = "один главный объект, крупный план, много воздуха"
    primary = "глубокий акцентный цвет по теме"
    secondary = "нейтральный поддерживающий фон"
    mood = "премиально, современно, естественно"
    person_needed = False
    object_needed = True
    shot_type = "editorial advertising"

    if "маникюр" in name:
        if "педикюр" in theme or "стоп" in theme:
            main_object = "ухоженные женские стопы и пальцы ног с аккуратным педикюром"
            composition = "крупный beauty-кадр стоп, анатомически корректные пальцы"
        else:
            main_object = "ухоженные женские руки и ногти с маникюром по теме"
            composition = "крупный beauty-кадр рук, анатомически корректные пальцы"
        if "бордо" in theme:
            primary = "глубокий бордовый"
        elif "шоколад" in theme:
            primary = "шоколадный"
        elif "молоч" in theme:
            primary = "молочный"
        elif "нюд" in theme:
            primary = "нюд"
        secondary = "ткань, камень, стекло или чистый салонный фон"
        mood = "премиальный beauty editorial, мягкий объёмный свет"
    elif "океан клиент" in name:
        if any(x in theme for x in ("авито", "объявлен", "фид")):
            main_object = "понятная сцена с карточками объявлений и одним сильным рекламным креативом"
        elif any(x in theme for x in ("crm", "лид", "менеджер", "продаж")):
            main_object = "чистая визуальная метафора воронки лидов и следующего действия менеджера"
        elif any(x in theme for x in ("звон", "телефон")):
            main_object = "смартфон или гарнитура с понятным входящим звонком и аналитикой"
        elif any(x in theme for x in ("контент", "соцсет", "vk", "telegram")):
            main_object = "одна выразительная карточка публикации в современном интерфейсном контексте"
        else:
            main_object = "одна конкретная бизнес-сцена, прямо раскрывающая тему поста"
        mood = "премиальный технологичный B2B, без случайных роботов и рукопожатий"
    elif "от души" in name:
        main_object = "эмоциональная персональная сцена поздравления, письма, подарка или речи по теме"
        person_needed = True
        mood = "тёплая премиальная эмоциональная фотография, без дешёвой открытки"

    forbidden = list(dict.fromkeys(
        [str(x) for x in (brief.get("forbidden_mentions") or [])]
        + ["случайный робот", "универсальное рукопожатие", "лишний мелкий текст", "водяные знаки"]
    ))
    if "маникюр" in name or "педикюр" in name:
        # Describe the already-required premium QA failures before generation;
        # do not revive the rejected local-art palette/composition templates.
        forbidden += [
            "однотонный бежевый фон без акцента и коммерческой композиции",
            "дешёвый салонный шаблон и пустая композиция",
            "грязная коричнево-бордовая spa-палитра фона и реквизита",
            "свечи и янтарное стекло",
            "виноград и бокалы",
            "баночка лака как главный герой",
            "огромная старомодная классическая serif-типографика",
        ]
    if "маникюр" in name and ("педикюр" in theme or "стоп" in theme):
        forbidden += ["руки вместо стоп", "лишние пальцы", "деформированные стопы",
                      "только стопы на пустом фоне без коммерческой композиции"]

    return {
        "policy_version": POLICY_VERSION,
        "main_object": main_object,
        "action": action,
        "composition": composition,
        "primary_color": primary,
        "secondary_color": secondary,
        "mood": mood,
        "shot_type": shot_type,
        "person_needed": bool(person_needed),
        "object_needed": bool(object_needed),
        "headline": _headline_from_brief(brief, text),
        "forbidden_objects": list(dict.fromkeys(forbidden)),
        "forbidden_text": ["цена", "скидка", "телефон", "адрес", "хэштеги", "CTA", "служебные поля"],
    }


def visual_brief_prompt(visual: dict) -> str:
    v = dict(visual or {})
    value = (
        "\n\nSEMANTIC_VISUAL_BRIEF — обязательный контракт готового баннера:\n"
        f"Главный объект: {v.get('main_object','')}\n"
        f"Действие/сцена: {v.get('action','')}\n"
        f"Композиция: {v.get('composition','')}\n"
        f"Основной цвет: {v.get('primary_color','')}\n"
        f"Вторичный цвет: {v.get('secondary_color','')}\n"
        f"Настроение: {v.get('mood','')}\n"
        f"Тип кадра: {v.get('shot_type','')}\n"
        f"Короткий заголовок: {v.get('headline','')}\n"
        f"Запрещённые объекты/сюжеты: {', '.join(v.get('forbidden_objects') or [])}\n"
        f"Запрещённый текст: {', '.join(v.get('forbidden_text') or [])}\n"
        "Покажи именно эту одну мысль. Не добавляй вторую услугу или случайный сюжет."
    )
    source = str(v.get("post_visual_source_text") or "").strip()
    if source:
        value = (
            "\n\nСМЫСЛОВОЙ ИСТОЧНИК ИЗОБРАЖЕНИЯ — согласованный основной текст поста:\n"
            + source
            + "\nИзобрази именно описанные здесь услугу, предмет, цвет, форму, длину, материал и действие, если они указаны. "
              "Эти конкретные детали важнее общего названия темы. Не выдумывай отсутствующие характеристики. "
              "Этот блок задаёт содержание сцены: не печатай полный пост, хэштеги и контакты на баннере. "
              "Видимый текст задаётся отдельными точными заголовками и подписями.\n"
            + value
        )
    return value


def _image_data_uri(path: str, max_side: int = 768) -> str:
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((int(max_side), int(max_side)))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def final_payload_qa_contract(project: dict, copy_deletion_context=None) -> dict:
    """Fingerprint only the explicitly opted-in final-publication QA path.

    Existing proofs/failed paid intents keep their original contract. A new
    proof also binds the project-specific inspector rules and input encoding.
    """
    contract = {
        "format_version": FINAL_PAYLOAD_QA_VERSION,
        "vision_contract": _vision_project_contract(project),
        "quality_policy": POLICY_VERSION,
        "operation": "social_banner_vision_qa_v1",
        "max_tokens": 700,
        "image_encoding": "rgb-jpeg-max768-quality88-detail-high",
        "caption_encoding": "per-channel-public-text-json-v1",
        "required_flags_schema": {"social": list(SOCIAL_VISION_REQUIRED_FLAGS),
                                  "native_premium": list(NATIVE_PREMIUM_REQUIRED_FLAGS),
                                  "reference": "reference_style_match"},
    }
    if copy_deletion_context is not None:
        contract["copy_deletion"] = {
            "version": COPY_DELETION_VERSION,
            "context_sha256": _copy_deletion_digest(copy_deletion_context),
        }
    return contract


def _copy_deletion_digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def _copy_deletion_paragraphs(value: str) -> list:
    return [{"start": m.start(), "end": m.end(), "text": m.group()}
            for m in re.finditer(r"[^\n]+(?:\n(?!\n)[^\n]+)*", value)]


def copy_deletion_manifest(text: str, public_texts: dict, brief: dict, visual: dict):
    """Offer only standalone weather/calendar asides, never service copy.

    This deliberately small grammar excludes names, numbers, contacts, URLs,
    tags, offers and multi-sentence paragraphs. No model chooses eligibility.
    Exact raw spans are retained separately from the normalized Vision input.
    """
    raw = str(text or "")
    paragraphs = _copy_deletion_paragraphs(raw)
    body_parts = []
    for index, paragraph in enumerate(paragraphs):
        value = paragraph["text"]
        if index == 0:
            # A generated headline may share its paragraph with useful copy.
            # Exclude the header, not the entire factual business paragraph.
            if re.match(r"(?i)^<b>.*?</b>", value):
                value = re.sub(r"(?i)^<b>.*?</b>", "", value).strip()
            else:
                value = value.split("\n", 1)[1] if "\n" in value else ""
        if not re.search(r"[#@]|https?://|www\.", value, re.I):
            body_parts.append(re.sub(r"<[^>]*>", "", value))
    body_size = sum(len(value) for value in body_parts)
    if not public_texts or body_size < 160:
        return None
    season = (
        r"(?:(?:За окном|На улице)(?: сегодня| сейчас)? (?:уже |ещ[её] |снова |по-прежнему )?"
        r"(?:лето|осень|зима|весна|жарко|тепло|холодно|прохладно|солнечно|ид[её]т снег|лежит снег)"
        r"|(?:Сегодня|Сейчас) (?:уже |ещ[её] |снова )?(?:лето|осень|зима|весна|бархатный сезон)"
        r"|(?:Наступила|Началась) (?:осень|зима|весна)|(?:Наступило|Началось) лето)[.!]"
    )
    relative_date = r"Сегодня (?:праздник|праздничный день|выходной день)[.!]"
    units = []
    for paragraph in paragraphs[1:]:
        value = paragraph["text"]
        if value != value.strip() or len(value) > 100:
            continue
        kind = "season" if re.fullmatch(season, value, re.I) else None
        if kind is None and (brief.get("occasion") or {}).get("kind") == "evergreen" and re.fullmatch(relative_date, value, re.I):
            kind = "date"
        if kind is None or len(value) > body_size * .25:
            continue
        # A theme/headline/date supplied by the project is not an aside.
        if any(value.rstrip(".!").casefold() in str(fact or "").casefold()
               for fact in (brief.get("selected_theme"), brief.get("main_thesis"), visual.get("headline"),
                            (brief.get("occasion") or {}).get("name"))):
            continue
        spans = {}
        for platform, caption in public_texts.items():
            matches = [p for p in _copy_deletion_paragraphs(caption) if p["text"] == value]
            if len(matches) != 1 or matches[0]["start"] == 0:
                break
            spans[platform] = {k: matches[0][k] for k in ("start", "end")}
        if set(spans) != set(public_texts) or sum(p["text"] == value for p in paragraphs) != 1:
            continue
        units.append({**paragraph, "kind": kind, "caption_spans": spans,
                      "unit_sha256": hashlib.sha256(value.encode("utf-8")).hexdigest()})
    if not units:
        return None
    return {"mode": "proposal", "body_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            "public_texts_sha256": _copy_deletion_digest(public_texts), "body_size": body_size,
            "units": units[:8]}


def copy_deletion_qa_context(record: dict, banner_sha256: str):
    state = record.get("copy_deletion") or {}
    manifest = state.get("manifest") or {}
    context = record.get("final_payload_qa") or {}
    if (state.get("version") != COPY_DELETION_VERSION or state.get("image_sha256") != banner_sha256
            or manifest.get("mode") not in {"proposal", "verify_only"}
            or manifest.get("body_sha256") != hashlib.sha256(str(record.get("text") or "").encode("utf-8")).hexdigest()
            or manifest.get("public_texts_sha256") != _copy_deletion_digest(record.get("delivery_texts") or {})):
        return None
    return {"version": COPY_DELETION_VERSION, "mode": manifest["mode"],
            "record_id": context.get("record_id"), "local_date": context.get("local_date"),
            "body_sha256": manifest["body_sha256"], "public_texts_sha256": manifest["public_texts_sha256"],
            "units": [{k: unit[k] for k in ("unit_sha256", "text", "kind")}
                      for unit in manifest.get("units") or []]}


def normalize_copy_deletion_proposal(proposal, context):
    """A proposal is optional evidence, never a replacement verdict or text."""
    if proposal is None:
        return None, None
    if (not isinstance(context, dict) or context.get("mode") != "proposal"
            or not isinstance(proposal, dict)
            or set(proposal) != {"version", "body_sha256", "units", "image_occasion_ok", "business_facts_ok"}
            or proposal.get("version") != COPY_DELETION_VERSION
            or proposal.get("body_sha256") != context.get("body_sha256")
            or proposal.get("image_occasion_ok") is not True or proposal.get("business_facts_ok") is not True):
        return None, "COPY_DELETION_PROPOSAL_INVALID"
    units = proposal.get("units")
    if not isinstance(units, list) or not 1 <= len(units) <= 2:
        return None, "COPY_DELETION_PROPOSAL_INVALID"
    eligible = {unit["unit_sha256"]: unit["kind"] for unit in context.get("units") or []}
    seen = set()
    for unit in units:
        if (not isinstance(unit, dict) or set(unit) != {"unit_sha256", "kind"}
                or not isinstance(unit.get("unit_sha256"), str)
                or unit["unit_sha256"] in seen or unit["unit_sha256"] not in eligible
                or eligible[unit["unit_sha256"]] != unit.get("kind")):
            return None, "COPY_DELETION_PROPOSAL_INVALID"
        seen.add(unit["unit_sha256"])
    return {"version": COPY_DELETION_VERSION, "body_sha256": proposal["body_sha256"],
            "units": [{"unit_sha256": u["unit_sha256"], "kind": u["kind"]} for u in units],
            "image_occasion_ok": True, "business_facts_ok": True}, None


def _final_payload_qa_text(public_texts: dict) -> str:
    """Normalize each caption BEFORE JSON encoding; never regex the JSON."""
    if (not isinstance(public_texts, dict) or not public_texts
            or any(p not in {"tg", "vk"} or not isinstance(t, str) or not t.strip()
                   for p, t in public_texts.items())):
        raise ValueError("FINAL_PAYLOAD_TEXTS_INVALID")
    value = json.dumps({p: public_text_for_qa(t) for p, t in public_texts.items()},
                       ensure_ascii=False, sort_keys=True)
    # Never make a truncated proof for an uninspected tail or broken envelope.
    if len(value) > 5000:
        raise ValueError("FINAL_PAYLOAD_TEXTS_TOO_LONG")
    return value


def validate_banner_vision(
    account_id: str,
    banner_path: str,
    brief: dict,
    visual_brief: dict,
    final_text: str | dict,
    project: dict,
) -> dict:
    from app.services.media_semantic_validator import _vision_chat_json
    contract = _vision_project_contract(project)
    final_payload = isinstance(final_text, dict)
    deletion_context = (visual_brief or {}).get("copy_deletion_context") if final_payload else None
    qa_text = _final_payload_qa_text(final_text) if final_payload else public_text_for_qa(final_text)[:5000]
    reference = vision_reference_context(project, brief, visual_brief)
    if reference.get("required") and not reference.get("ok"):
        return {"policy_version": POLICY_VERSION, "pass": False,
                "vision_qa_contract": contract,
                "issues": [reference.get("error") or "owner_reference_unavailable"],
                "reference": reference}

    if not banner_path:
        return {
            "policy_version": POLICY_VERSION,
            "vision_qa_contract": contract,
            "pass": False,
            "issues": ["banner_missing"],
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    nail_rules = ""
    if (contract["profile"] in {"nail_premium", "reference_premium"}
            or reference.get("required")
            or isinstance((visual_brief or {}).get("premium_contract"), dict)):
        nail_rules = (
            "NAIL/REFERENCE PREMIUM STYLE RULES: reject generic beige beauty-template look, "
            "empty composition with only hands/feet on a plain background, dated oversized classical serif typography, "
            "muddy brown/burgundy/beige salon palette, candles, amber glass, grapes, wine-like props, "
            "and generic nail-polish still-life cliches. "
            "For nail/pedicure content be especially strict about hands vs feet and realistic anatomy. "
            "For a manicure beauty close-up, hands-only framing is valid and a face/full body is NOT required; "
            "never reject a correct manicure close-up merely because no face is visible. "
            "For pedicure, overlapping toes and partially hidden toes are acceptable if the feet otherwise look anatomically plausible. "
        )

    prompt = (
        "You are the final fail-closed QA inspector for a Russian social-media advertising banner. "
        "Return strict JSON object only with keys: pass, semantic_match, service_ok, object_ok, anatomy_ok, "
        "headline_ok, headline_complete_ok, spelling_ok, forbidden_ok, season_ok, professional_ok, "
        "mobile_ok, creative_distinctiveness_ok, premium_palette_ok, premium_typography_ok, copy_season_ok, occasion_ok, reference_style_match, issues. "
        "Every required boolean must be a JSON boolean true for pass=true. "
        "If NATIVE PREMIUM CONTRACT is present, also return actual JSON booleans exact_text_ok, "
        "forbidden_claims_ok, composition_ok, crop_safe_ok, selling_typography_ok, palette_ok, "
        "reference_style_match, editorial_richness_ok, secondary_copy_density_ok and check that contract in full. "
        "copy_season_ok checks the actual final post INCLUDING HASHTAGS against the dated brief. "
        "occasion_ok checks dates and wording in BOTH text and image: never present a future event as today, "
        "never publish an expired dated occasion, never invent a calendar date for an evergreen birthday/wedding example. "
        "An undated business/service advice post with occasion.kind=evergreen is valid without any holiday or calendar event. "
        "Evergreen does not assert that an event happens today; judge only explicit date/event claims in the actual text and image. "
        "Use only the project-local current_date and explicit occasion date in CONTENT BRIEF. "
        "If a second image is supplied, it is the owner-approved style reference; reference_style_match must compare "
        "the FIRST image with that SECOND image for premium art direction, selling typography and palette. "
        "Do not copy the reference wording, people, dates or services into the candidate. "
        "Reject if the image is generic or does not visually express the exact post idea. "
        "Judge semantic_match, service_ok and object_ok against this project's selected_theme/main_thesis and VISUAL BRIEF main_object. "
        "A service list or contact line in a marketing footer does not require illustrating every listed service; the main scene must still match the core post idea. "
        "Judge premium_palette_ok and premium_typography_ok using this project's own mood/style in VISUAL BRIEF, not an unrelated nail-salon style. "
        "Reject wrong service/object, unrelated scene, cheap stock/template look, collage noise, random robot, "
        "garbled or misspelled Russian text IN THE IMAGE, image text outside the exact headline plus explicitly allowed_secondary_text in VISUAL BRIEF, "
        "unfinished or grammatically incomplete headline, unreadable mobile typography, clipped headline, deformed anatomy, extra fingers/toes, "
        "or any forbidden seasonal object. "
        "IMAGE TEXT SCOPE: VISUAL BRIEF forbidden_text applies only to text rendered inside the image. "
        "Reject a displayed phone number, price offer, address or other prohibited commercial disclosure in the image. "
        "The words 'телефон'/'цена' in that list mean a contact number/pricing disclosure, not a ban on a topic-relevant headline about calls or pricing. "
        "Legacy forbidden_objects entries 'телефоны'/'цены' have the same written-contact/pricing meaning; "
        "a physical smartphone/handset explicitly required by the exact topic and main_object is allowed, while an unrelated device still fails semantic/object checks. "
        "This does not override explicit CONTENT BRIEF forbidden_mentions or a topic-specific object exclusion; those remain binding. "
        "FINAL POST TEXT is supplied for semantic/date/season checks and explicit CONTENT BRIEF forbidden_mentions; "
        "do not extend image-only text prohibitions to a phone number/contact in the post caption. "
        "Caption contact/price permission remains governed by the existing project public-copy policy; this image QA does not grant a new permission or replace that policy. "
        + nail_rules
        + "Natural human skin tones, including tan/beige/brown skin, are NEVER a palette violation; palette checks apply to backgrounds, props and graphic art direction. "
        "Headline matching is case-insensitive: normal capitalization of the first word is acceptable and must not fail headline_complete_ok or semantic_match. "
        "For anatomy, fail only on clearly visible impossible anatomy. Do not infer extra/missing fingers or toes merely from overlap, perspective, cropping or natural occlusion. "
        "If the brief forbids sea/beach, reject sea, beach, sand, shells, palms, swimming or vacation imagery.\n"
        "VISION QA CONTRACT:\n" + json.dumps(contract, ensure_ascii=False, sort_keys=True)
        + "\nCONTENT BRIEF:\n" + json.dumps(brief, ensure_ascii=False, sort_keys=True)[:5000]
        + "\nVISUAL BRIEF:\n" + json.dumps(visual_brief, ensure_ascii=False, sort_keys=True)[:5000]
        + "\nNATIVE PREMIUM CONTRACT:\n" + json.dumps((visual_brief or {}).get("premium_contract"), ensure_ascii=False, sort_keys=True)[:9000]
        + "\nFINAL POST TEXT (hashtags included):\n" + qa_text
    )
    if final_payload:
        prompt = (
            "EXACT OUTPUT SCHEMA FOR THIS REQUEST: return every one of these keys as an actual JSON boolean, "
            "based on the observed evidence (never strings, numbers or omitted fields): "
            + json.dumps(list(dict.fromkeys(("pass", *vision_required_flags(project, brief, visual_brief)))))
            + ". Also return issues as an array of strings. A missing required key is an invalid QA response.\n"
            + prompt
        )
    if deletion_context is not None:
        prompt += (
            "\nOPTIONAL COPY DELETION: keep every required QA boolean truthful and keep pass=false on a copy defect. "
            "Only when mode=proposal, ALL image/native/service/object/semantic checks pass, the image has no invalid date, "
            "and all retained business facts match the unchanged brief, you MAY additionally return copy_deletion_proposal. "
            "It may identify at most TWO of the backend-enumerated standalone optional units that make only an unsupported "
            "season or relative-date assertion. Do not propose wording, replacements, insertions, another image or other deletions. "
            "Do not use deletion to repair an image defect, service mismatch, business fact, headline, contact, number, CTA, URL or hashtag. "
            "The exact proposal schema is {version, body_sha256, units:[{unit_sha256,kind}], image_occasion_ok:true, business_facts_ok:true}; "
            "echo version/body_sha256 and exact eligible unit identifiers/kinds from the context. If in doubt omit it. "
            "For mode=verify_only return no proposal: inspect the same image against the entire corrected FINAL POST TEXT. "
            "The original generation brief/reference/native contract remains binding; a deletion grants no image or fact exception. "
            "\nCOPY DELETION CONTEXT:\n" + json.dumps(deletion_context, ensure_ascii=False, sort_keys=True)
        )
    content = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": _image_data_uri(banner_path), "detail": "high"}},
    ]
    if reference.get("required"):
        content.append({"type": "image_url", "image_url": {"url": _image_data_uri(reference["path"]), "detail": "high"}})
    input_evidence = ({
        "final_payload_qa_version": FINAL_PAYLOAD_QA_VERSION,
        "qa_input_contract": final_payload_qa_contract(project, deletion_context),
        "qa_input_sha256": hashlib.sha256(json.dumps({
            "operation": "social_banner_vision_qa_v1", "content": content,
            "max_tokens": 700,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
    } if final_payload else {})
    try:
        out, meta = _vision_chat_json(
            str(account_id),
            "social_banner_vision_qa_v1",
            content,
            max_tokens=700,
            timeout=90,
            return_meta=True,
            provenance={"source": "social", "policy": POLICY_VERSION},
        )
    except Exception as exc:
        return {
            "policy_version": POLICY_VERSION,
            "vision_qa_contract": contract,
            "pass": False,
            "issues": ["vision_qa_unavailable:" + type(exc).__name__],
            "image_repair_allowed": False, "repair_scope": "qa",
            "error": str(exc)[:220],
            **input_evidence,
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    if not isinstance(out, dict):
        return {
            "policy_version": POLICY_VERSION,
            "vision_qa_contract": contract,
            "pass": False,
            "issues": ["vision_qa_invalid_envelope"],
            **input_evidence,
            **vision_cost_evidence(account_id, meta),
            **({"qa_schema_repair_required": True} if final_payload else {}),
            "image_repair_allowed": False, "repair_scope": "qa",
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    required = list(vision_required_flags(project, brief, visual_brief))
    passed, schema_issues = strict_qa_flags(out, required)
    _raw_issues = out.get("issues")
    if isinstance(_raw_issues, list):
        _issues = [str(x)[:180] for x in _raw_issues if str(x).strip()]
    elif _raw_issues is None or str(_raw_issues).strip().lower() in {"", "none", "null", "нет"}:
        _issues = []
    else:
        _issues = [str(_raw_issues)[:180]]
    _cost = vision_cost_evidence(account_id, meta)
    _repair = vision_repair_policy(out)
    deletion_proposal, deletion_error = normalize_copy_deletion_proposal(out.get("copy_deletion_proposal"), deletion_context)
    if final_payload:
        invalid_fields = [issue for issue in schema_issues if issue.startswith("vision_qa_invalid_")]
        if invalid_fields:
            # An incomplete inspector response is not evidence that another
            # paid image can fix the banner. Preserve FAIL and the received
            # cost; stop image retries until the QA contract is reconciled.
            _repair.update(image_repair_allowed=False, qa_schema_repair_required=True,
                           repair_scope="qa", qa_schema_issues=invalid_fields,
                           repair_blockers=list(dict.fromkeys(list(_repair.get("repair_blockers") or []) + invalid_fields)))
    return {
        "policy_version": POLICY_VERSION,
        "vision_qa_contract": contract,
        "pass": bool(passed),
        **{k: out.get(k) is True for k in required},
        "issues": list(dict.fromkeys(schema_issues + _issues))[:20],
        "reference": reference,
        "context_date": brief.get("current_date"),
        **({"copy_deletion_proposal": deletion_proposal,
            "copy_deletion_proposal_error": deletion_error} if deletion_context is not None else {}),
        "premium_verdict": ({
            "passed": bool(passed),
            **{k: out.get(k) is True for k in NATIVE_PREMIUM_REQUIRED_FLAGS},
            **_cost,
            **_repair,
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "issues": list(dict.fromkeys(schema_issues + _issues))[:20],
        } if isinstance((visual_brief or {}).get("premium_contract"), dict) else None),
        "request_id": (meta or {}).get("request_id"),
        "provider_request_id": (meta or {}).get("provider_request_id"),
        **input_evidence,
        **_cost,
            **_repair,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def quality_tracking(result: dict) -> dict:
    out = {}
    for key in (
        "content_brief", "copy_quality", "copy_attempts", "visual_brief",
        "vision_qa", "banner_cost_rub", "banner_cost_known", "banner_attempt_costs", "banner_attempts",
        "banner_vision_qa_costs", "banner_asset", "banner_engine", "banner_model", "july_premium", "premium_attempts",
        "delivery_quality", "delivery_qa_attempts", "delivery_copy_quality", "delivery_texts",
        "final_payload_qa", "final_payload_reconciliation_required", "banner_attempt_artifacts", "copy_deletion",
        "quality_policy", "quality_status",
    ):
        if key in (result or {}):
            out[key] = result.get(key)
    return out


def append_feedback(
    account_id: str,
    project_id: str,
    post_id: str,
    rating: str,
    reasons: list[str] | None = None,
    note: str = "",
) -> dict:
    rating = str(rating or "").strip().lower()
    if rating not in {"good", "weak"}:
        raise ValueError("SOCIAL_FEEDBACK_RATING_INVALID")
    item = {
        "post_id": str(post_id or ""),
        "rating": rating,
        "reasons": [str(x).strip()[:80] for x in (reasons or []) if str(x).strip()][:8],
        "note": str(note or "").strip()[:500],
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    key = "social_feedback:" + str(project_id)
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == str(account_id), Storage.key == key).with_for_update().first()
        try:
            items = json.loads(row.value or "[]") if row else []
        except Exception:
            items = []
        items.insert(0, item)
        items = items[:120]
        payload = json.dumps(items, ensure_ascii=False)
        if row:
            row.value = payload
        else:
            db.add(Storage(account_id=str(account_id), key=key, value=payload))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return {"status": "ok", "feedback": item}


def acceptance_snapshot(account_id: str, project_id: str = "", required_slots: int = 10) -> dict:
    from app.services.social_editorial_contract import TEMPORAL_POLICY
    required_slots = max(10, int(required_slots))
    db = SessionLocal()
    try:
        prow = db.query(Storage).filter(Storage.account_id == str(account_id), Storage.key == "posting_projects").first()
        postrow = db.query(Storage).filter(Storage.account_id == str(account_id), Storage.key == "posting_posts").first()
        try:
            projects = json.loads(prow.value or "[]") if prow else []
            posts = json.loads(postrow.value or "[]") if postrow else []
        except (TypeError, ValueError):
            projects, posts = [], []
    finally:
        db.close()
    pmap = {str(x.get("id") or ""): x for x in projects if isinstance(x, dict)}
    rows = [dict(p) for p in posts if isinstance(p, dict)
            and re.fullmatch(r"social-slot:[^:]+:[^:]+:\d{8}_\d{2}", str(p.get("generation_key") or ""))
            and (not project_id or str(p.get("project_id") or "") == str(project_id))]
    rows.sort(key=lambda p: str(p.get("published_at") or p.get("created_at") or ""), reverse=True)
    consecutive, checks = 0, []
    for post in rows[:max(required_slots * 3, 30)]:
        proj = pmap.get(str(post.get("project_id") or ""), {})
        selected = str(proj.get("platforms") or "both")
        required = [p for p, target in (("tg", "channel_tg"), ("vk", "vk_owner_id"))
                    if selected in (p, "both") and proj.get(target)]
        ids = post.get("ids") if isinstance(post.get("ids"), dict) else {}
        id_ok = bool(required) and all(isinstance(ids.get(p), dict)
                    and bool(ids[p].get("message_id" if p == "tg" else "post_id")) for p in required)
        proofs = post.get("delivery_quality") if isinstance(post.get("delivery_quality"), dict) else {}
        copy_checks = post.get("delivery_copy_quality") if isinstance(post.get("delivery_copy_quality"), dict) else {}
        copy_ok = bool(required) and all(isinstance(copy_checks.get(p), dict)
                                        and copy_checks[p].get("pass") is True for p in required)
        flags = vision_required_flags(proj, post.get("content_brief") or {}, post.get("visual_brief") or {})
        vision_ok = bool(required) and all(isinstance(proofs.get(p), dict) and strict_qa_flags(proofs[p].get("verdict"), flags)[0]
                                          for p in required)
        evidence_ok = bool(required) and all(
            isinstance(proofs.get(p), dict)
            and proofs[p].get("policy_version") == TEMPORAL_POLICY
            and proofs[p].get("cost_guard_ok") is True
            and all(re.fullmatch(r"[0-9a-f]{64}", str(proofs[p].get(k) or ""))
                    for k in ("context_sha256", "public_text_sha256", "banner_sha256"))
            and bool(proofs[p].get("local_date"))
            and proofs[p].get("local_date") == (proofs[p].get("verdict") or {}).get("context_date")
            for p in required)
        cost = post.get("banner_cost_rub")
        cost_ok = (post.get("banner_cost_known") is True and type(cost) in (int, float)
                   and math.isfinite(cost) and 0 < cost <= BANNER_ACCEPTED_TOTAL_COST_CAP_RUB)
        duplicate_ok = bool(required)
        for platform in required:
            similarity = copy_checks[platform].get("max_similarity") if isinstance(copy_checks.get(platform), dict) else None
            duplicate_ok = (duplicate_ok and type(similarity) in (int, float)
                            and math.isfinite(similarity) and 0 <= similarity < 0.82)
        verify_ok = str((post.get("post_publish_verification") or {}).get("status") or "") == "pass"
        ok = (post.get("status") == "published" and id_ok and copy_ok and vision_ok
              and evidence_ok and verify_ok and cost_ok and duplicate_ok)
        checks.append({"post_id": post.get("id"), "project_id": post.get("project_id"),
                       "generation_key": post.get("generation_key"), "pass": bool(ok),
                       "id_ok": bool(id_ok), "copy_ok": bool(copy_ok), "vision_ok": bool(vision_ok),
                       "delivery_evidence_ok": bool(evidence_ok), "verify_ok": verify_ok,
                       "cost_ok": bool(cost_ok), "duplicate_ok": bool(duplicate_ok)})
        if not ok:
            break
        consecutive += 1
        if consecutive >= required_slots:
            break
    return {"policy_version": POLICY_VERSION, "delivery_policy": TEMPORAL_POLICY,
            "required_slots": required_slots, "consecutive_passed": consecutive,
            "pass": consecutive >= required_slots, "checks": checks[:required_slots]}


def analytics_snapshot(account_id: str, project_id: str = "", days: int = 30) -> dict:
    posts = recent_project_posts(account_id, project_id, limit=100) if project_id else []
    if not project_id:
        db = SessionLocal()
        try:
            row = db.query(Storage).filter(
                Storage.account_id == str(account_id),
                Storage.key == "posting_posts",
            ).first()
            try:
                posts = json.loads(row.value or "[]") if row else []
            except Exception:
                posts = []
        finally:
            db.close()

    cutoff = datetime.now() - timedelta(days=max(1, int(days)))
    selected = []
    for p in posts:
        if not isinstance(p, dict):
            continue
        dt = _created_at(p)
        if dt and dt < cutoff:
            continue
        if project_id and str(p.get("project_id") or "") != str(project_id):
            continue
        selected.append(p)

    def _bucket_add(table: dict, key: str, score: float, views: float) -> None:
        key = str(key or "не определено")[:180]
        row = table.setdefault(
            key,
            {"posts": 0, "views": 0.0, "engagement_score": 0.0},
        )
        row["posts"] += 1
        row["views"] += views
        row["engagement_score"] += score

    by_format: dict[str, dict] = {}
    by_topic: dict[str, dict] = {}
    by_hook: dict[str, dict] = {}
    by_visual: dict[str, dict] = {}
    measured_posts = 0
    quality_published = 0
    quality_verified = 0
    semantic_failures = 0
    publish_failures = 0

    for p in selected:
        brief = p.get("content_brief") if isinstance(p.get("content_brief"), dict) else {}
        visual = p.get("visual_brief") if isinstance(p.get("visual_brief"), dict) else {}
        metrics = p.get("engagement") if isinstance(p.get("engagement"), dict) else {}
        views = float(metrics.get("views") or 0)
        reactions = float(metrics.get("reactions") or metrics.get("likes") or 0)
        comments = float(metrics.get("comments") or 0)
        reposts = float(metrics.get("reposts") or 0)
        has_measurement = bool(metrics)
        if has_measurement:
            measured_posts += 1
        score = reactions + comments * 2 + reposts * 3

        _bucket_add(by_format, str(brief.get("format") or "не определён"), score, views)
        _bucket_add(by_topic, str(brief.get("selected_theme") or "не определена"), score, views)
        _bucket_add(by_hook, _title(p.get("text") or "") or "без заголовка", score, views)
        visual_key = " · ".join(
            x for x in (
                str(visual.get("composition") or "").strip(),
                str(visual.get("primary_color") or "").strip(),
            ) if x
        ) or "не определён"
        _bucket_add(by_visual, visual_key, score, views)

        if str(p.get("quality_policy") or "") == POLICY_VERSION:
            if p.get("status") == "published":
                quality_published += 1
                if (
                    bool((p.get("copy_quality") or {}).get("pass"))
                    and bool((p.get("vision_qa") or {}).get("pass"))
                    and str((p.get("post_publish_verification") or {}).get("status") or "") == "pass"
                ):
                    quality_verified += 1
            if p.get("status") == "quality_blocked":
                semantic_failures += 1
            if p.get("status") in {"error", "partial"}:
                publish_failures += 1

    def _finish(table: dict, label: str) -> list[dict]:
        for row in table.values():
            row["engagement_per_post"] = round(
                row["engagement_score"] / max(1, row["posts"]), 3
            )
        ranked = sorted(
            table.items(),
            key=lambda kv: (
                kv[1]["engagement_per_post"],
                kv[1]["posts"],
            ),
            reverse=True,
        )
        return [{label: key, **value} for key, value in ranked]

    format_rank = _finish(by_format, "format")
    topic_rank = _finish(by_topic, "topic")
    hook_rank = _finish(by_hook, "hook")
    visual_rank = _finish(by_visual, "visual")

    feedback = (
        _feedback_preferences(account_id, project_id, limit=50)
        if project_id else {"likes": 0, "dislikes": {}, "recent": []}
    )
    weak_feedback = sum(
        1 for x in (feedback.get("recent") or [])
        if x.get("rating") == "weak"
    )
    total_feedback = len(feedback.get("recent") or [])
    weak_ratio = round(
        weak_feedback / max(1, total_feedback), 3
    ) if total_feedback else 0.0

    autopilot_ready = bool(
        quality_verified >= 20
        and semantic_failures == 0
        and publish_failures == 0
        and weak_ratio <= 0.10
    )

    return {
        "policy_version": POLICY_VERSION,
        "days": int(days),
        "posts": len(selected),
        "measured_posts": measured_posts,
        "by_format": by_format,
        "winning_formats": format_rank[:5],
        "winning_topics": topic_rank[:5],
        "losing_topics": list(reversed(topic_rank[-5:])) if topic_rank else [],
        "winning_hooks": hook_rank[:5],
        "winning_visual_patterns": visual_rank[:5],
        "quality_published": quality_published,
        "quality_verified": quality_verified,
        "quality_failures": {
            "semantic": semantic_failures,
            "publish": publish_failures,
        },
        "owner_feedback": {
            "positive": int(feedback.get("likes") or 0),
            "weak_recent": weak_feedback,
            "weak_ratio": weak_ratio,
            "top_dislikes": [
                {"reason": k, "count": v}
                for k, v in sorted(
                    (feedback.get("dislikes") or {}).items(),
                    key=lambda kv: kv[1],
                    reverse=True,
                )[:5]
            ],
        },
        "autopilot_readiness": {
            "ready": autopilot_ready,
            "required_successful_posts": 20,
            "successful_verified_posts": quality_verified,
            "weak_feedback_ratio": weak_ratio,
            "semantic_failures": semantic_failures,
            "publish_failures": publish_failures,
            "message": (
                "Контент можно перевести в полный автопилот"
                if autopilot_ready
                else "Нужно накопить минимум 20 подтверждённых качественных публикаций без сбоев"
            ),
        },
        "measurement_note": (
            "VK metrics are stored only from proven provider readback at "
            "24h/72h/7d checkpoints. Telegram metrics remain absent unless "
            "the provider exposes verifiable statistics; missing values are not invented."
        ),
    }



# These flags are produced by the real multimodal validator, never filled by
# generator success or file existence. Native premium QA remains independent.
SOCIAL_VISION_REQUIRED_FLAGS = (
    "semantic_match", "service_ok", "object_ok", "anatomy_ok", "headline_ok",
    "headline_complete_ok", "spelling_ok", "forbidden_ok", "season_ok",
    "professional_ok", "mobile_ok", "creative_distinctiveness_ok",
    "premium_palette_ok", "premium_typography_ok", "copy_season_ok", "occasion_ok",
)
NATIVE_PREMIUM_REQUIRED_FLAGS = (
    "semantic_match", "headline_ok", "exact_text_ok", "spelling_ok",
    "forbidden_claims_ok", "composition_ok", "crop_safe_ok", "professional_ok",
    "mobile_ok", "selling_typography_ok", "palette_ok", "reference_style_match",
    "editorial_richness_ok", "secondary_copy_density_ok",
)


def vision_required_flags(project: dict, brief: dict, visual_brief: dict) -> tuple:
    flags = list(SOCIAL_VISION_REQUIRED_FLAGS)
    if isinstance((visual_brief or {}).get("premium_contract"), dict):
        flags.extend(NATIVE_PREMIUM_REQUIRED_FLAGS)
    name = str((project or {}).get("name") or (brief or {}).get("business") or "").lower()
    if "маникюр" in name or "педикюр" in name or (project or {}).get("owner_reference_niche"):
        flags.append("reference_style_match")
    return tuple(dict.fromkeys(flags))


def configured_seasonal_mentions(project: dict, month: int) -> list[str]:
    configured = [str(x).strip() for x in project.get("seasonal_exclusions") or [] if str(x).strip()]
    name = str(project.get("name") or "").lower()
    if ("маникюр" in name or "педикюр" in name) and month not in (10, 11, 12, 1, 2):
        # This legacy field held the October cold-season exclusions.  Its known
        # cold-season vocabulary must not become a permanent annual beach ban.
        cold_terms = {str(x).lower().replace("ё", "е") for x in _season(10)[2]}
        configured = [x for x in configured if x.lower().replace("ё", "е") not in cold_terms]
    return configured


def vision_reference_context(project: dict, brief: dict, visual_brief: dict) -> dict:
    """Select only configured approved references, by niche rather than client ID."""
    from pathlib import Path
    business = str((project or {}).get("name") or (brief or {}).get("business") or "").lower()
    configured = str((project or {}).get("owner_reference_niche") or "").strip()
    is_nail = "маникюр" in business or "педикюр" in business
    if not is_nail and not configured:
        return {"required": False, "ok": True}
    main = str((visual_brief or {}).get("main_object") or "").lower()
    theme = str((brief or {}).get("selected_theme") or "").lower()
    if configured:
        niche = configured
    elif any(x in main for x in ("стоп", "feet", "foot", "пальцы ног")):
        niche = "pedicure"
    elif any(x in main for x in ("рук", "hand", "кист")):
        niche = "manicure"
    else:
        niche = "pedicure" if "педикюр" in theme else "manicure"
    try:
        from app.services.banner_full_ai_premium import OWNER_REFERENCE_IMAGES, OWNER_REFERENCE_VERSION
        path = str(OWNER_REFERENCE_IMAGES.get(niche) or "")
        if not path or not Path(path).is_file():
            return {"required": True, "ok": False, "niche": niche,
                    "error": "owner_reference_asset_missing"}
        return {"required": True, "ok": True, "niche": niche, "path": path,
                "version": OWNER_REFERENCE_VERSION,
                "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}
    except Exception as exc:
        return {"required": True, "ok": False, "niche": niche,
                "error": "owner_reference_unavailable:" + type(exc).__name__}


def refresh_temporal_brief(brief: dict, project: dict, *, now=None) -> dict:
    """Keep the accepted idea/CTA; refresh only time and seasonal constraints.

    The original stored brief is not overwritten. A dated occasion retains its
    original year/date, so retrying after expiry cannot turn it into next year.
    """
    import copy
    out = copy.deepcopy(brief or {})
    if not out.get("selected_theme"):
        raise ValueError("SOCIAL_QUALITY_BRIEF_MISSING")
    today = project_local_now(project, now).date()
    old_forbidden = []
    try:
        old_forbidden = _season(parse_reference_date(out.get("current_date")).month)[2]
    except (TypeError, ValueError):
        old_forbidden = []
    season, priorities, forbidden = _season(today.month)
    name = str((project or {}).get("name") or out.get("business") or "").lower()
    if "маникюр" not in name and "педикюр" not in name:
        priorities, forbidden = [], []
    configured = configured_seasonal_mentions(project, today.month)
    retained = [str(x) for x in out.get("forbidden_mentions") or []
                if str(x) not in old_forbidden]
    out.update({
        "current_date": today.isoformat(), "season": season,
        "vision_qa_contract": _vision_project_contract(project),
        "seasonal_priorities": priorities,
        "forbidden_mentions": list(dict.fromkeys(retained + forbidden + configured)),
    })
    if not isinstance(out.get("occasion"), dict):
        out["occasion"] = {"kind": "evergreen"}
    return out


def headline_before_image(visual_brief: dict, final_text: str) -> dict:
    """Reject a mechanically clipped prefix before buying an image."""
    headline = str((visual_brief or {}).get("headline") or "").strip()
    full_title = _title(final_text).strip()
    issues = []
    if not headline or len(headline) > 80:
        issues.append("headline_requires_complete_short_rewrite")
    if (headline and full_title.startswith(headline) and full_title != headline
            and re.search(r"(?i)\b(?:и|или|но|что|чтобы|не|в|на|с|к|для|без|от|при|между|обязаны|должны|быть)\s*$", headline)):
        issues.append("headline_clipped_sentence")
    return {"pass": not issues, "issues": issues}


def _positive_vision_cost(value):
    """A paid proof needs an actual positive amount; absence is never free."""
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return amount if math.isfinite(amount) and amount > 0 else None


def _social_vision_usage_receipt(account_id: str, request_id: str | None):
    """Read the exact tenant/operation/provider request, never a latest charge.

    Both OpenAI and the canonical JSON-object provider fallback keep the same
    operation name. Do not add a retry suffix: it would leave the guard's
    existing object-JSON whitelist and create a different paid intent.
    """
    rid = str(request_id or "").strip()
    account = str(account_id or "").strip()
    if not account or not rid or rid.startswith("ambiguous-vision:"):
        return None
    db = None
    try:
        from sqlalchemy import text
        db = SessionLocal()
        row = db.execute(
            text("SELECT id, cost_rub FROM api_usage "
                 "WHERE account_id=:a AND operation=:o AND request_id=:r "
                 "ORDER BY id DESC LIMIT 1"),
            {"a": account, "o": "social_banner_vision_qa_v1", "r": rid},
        ).fetchone()
        amount = _positive_vision_cost(row[1] if row else None)
        if amount is None:
            return None
        return {"source": "api_usage", "api_usage_id": row[0],
                "account_id": account, "operation": "social_banner_vision_qa_v1",
                "request_id": rid, "cost_rub": amount}
    except Exception as exc:
        # Keep the slot blocked and expose only the failure type, not DB data.
        import logging
        logging.getLogger(__name__).warning("social_vision_usage_receipt failed: %s", type(exc).__name__)
        return None
    finally:
        if db is not None:
            db.close()


def vision_cost_evidence(account_id: str, meta: dict | None) -> dict:
    """Separate the original proof's cost from a new provider charge.

    A cache replay is cost-free now only after the original positive cost is
    known. The caller still accounts that original cost once in the post total;
    a zero new charge is not proof that it was already included there.
    """
    meta = meta if isinstance(meta, dict) else {}
    cached = meta.get("cache_hit") is True
    amount = _positive_vision_cost(meta.get("cost_rub"))
    rid = meta.get("provider_request_id") or meta.get("request_id")
    receipt = None
    if amount is not None:
        receipt = {"source": "provider_meta", "account_id": str(account_id),
                   "operation": "social_banner_vision_qa_v1",
                   "request_id": rid, "cost_rub": amount}
    elif cached:
        receipt = _social_vision_usage_receipt(account_id, rid)
        amount = _positive_vision_cost((receipt or {}).get("cost_rub"))
    return {"qa_cost_rub": amount, "qa_cost_known": amount is not None,
            "qa_new_charge_rub": (0.0 if cached else amount) if amount is not None else None,
            "qa_cost_evidence": receipt, "cache_hit": cached}


def vision_repair_policy(verdict: dict) -> dict:
    """A new image cannot repair a dated copy error or missing QA fields."""
    verdict = verdict if isinstance(verdict, dict) else {}
    copy_fields = ("copy_season_ok", "occasion_ok")
    copy_failed = [key for key in copy_fields if verdict.get(key) is False]
    invalid = [key for key in copy_fields if type(verdict.get(key)) is not bool]
    return {"image_repair_allowed": not copy_failed and not invalid,
            "copy_repair_required": bool(copy_failed),
            "qa_schema_repair_required": bool(invalid),
            "repair_scope": "copy" if copy_failed else "qa" if invalid else "image",
            "repair_blockers": ["copy_repair_required:" + key for key in copy_failed]
                               + ["vision_qa_invalid_boolean:" + key for key in invalid]}
