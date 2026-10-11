"""Pure Social date and delivery-QA contract; no database/provider side effects.

The caller supplies a budget-guarded *real* QA callback.  Rechecking an existing
asset never creates new copy, a new banner, or a new external publication.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone


TEMPORAL_POLICY = "SOCIAL_EDITORIAL_DATE_V1"


def project_offset(project: dict) -> int:
    raw = (project or {}).get("tz_offset")
    raw = 3 if raw in (None, "") else raw
    if isinstance(raw, bool):
        raise ValueError("SOCIAL_PROJECT_TZ_OFFSET_INVALID")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("SOCIAL_PROJECT_TZ_OFFSET_INVALID") from exc
    if str(raw).strip() != str(value) or not -23 <= value <= 23:
        raise ValueError("SOCIAL_PROJECT_TZ_OFFSET_INVALID")
    return value


def project_local_now(project: dict, now: datetime | None = None) -> datetime:
    instant = now if now is not None else datetime.now(timezone.utc)
    if instant.tzinfo is None:
        # Legacy callers inject naive UTC; the host TZ must never affect a post.
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone(timedelta(hours=project_offset(project))))


def parse_reference_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def planned_content_date(project: dict, *, now: datetime | None = None,
                         generation_key: str = "", publish_at: str = "") -> date:
    """Date for a NEW plan; generation_key keeps its original service slot.

    A 23:00 slot + 3 h review has tomorrow's editorial context.  A newly planned
    previous-day catch-up uses today's context without changing its slot key.
    Never use this helper to rewrite an already frozen paid prompt snapshot.
    """
    today = project_local_now(project, now).date()
    target = None
    if publish_at:
        target = datetime.strptime(str(publish_at).replace("T", " ")[:16], "%Y-%m-%d %H:%M")
    elif generation_key:
        match = re.fullmatch(r"social-slot:[^:]+:[^:]+:(\d{8})_(\d{2})", str(generation_key))
        if match:
            target = datetime.strptime("".join(match.groups()), "%Y%m%d%H")
            if str((project or {}).get("mode") or "auto") == "moderate":
                target += timedelta(hours=int((project or {}).get("auto_publish_hours") or 0))
    return max(today, target.date()) if target is not None else today


def occasion_blockers(occasion: dict | None, reference_date) -> list[str]:
    """A stored dated occasion is never silently rolled into the next year."""
    occasion = occasion or {"kind": "evergreen"}
    if not isinstance(occasion, dict):
        return ["occasion_date_unverified"]
    kind = occasion.get("kind")
    if kind == "evergreen":
        return []
    if kind != "dated" or not str(occasion.get("source") or "").strip():
        return ["occasion_date_unverified"]
    try:
        event_date = parse_reference_date(occasion.get("date"))
        delta = (event_date - parse_reference_date(reference_date)).days
    except (TypeError, ValueError):
        return ["occasion_date_invalid"]
    if delta < 0:
        return ["occasion_expired"]
    if delta > 7:
        return ["occasion_outside_preparation_window"]
    return []


def public_text_for_qa(text: str) -> str:
    """Remove markup/URLs while retaining the actual hashtag words."""
    value = re.sub(r"https?://\S+|www\.\S+", " ", str(text or ""), flags=re.I)
    return re.sub(r"<[^>]*>", " ", value).replace("ё", "е").lower()


def seasonal_hits(text: str, forbidden: list[str]) -> list[str]:
    value = public_text_for_qa(text)
    hashtags = re.findall(r"(?<!\w)#[\w]+", value)
    patterns = {
        "море": r"\bмор(?:е|я|ю|ем|ск[а-я]*)\b",
        "купание": r"\b(?:купан[а-я]*|купа(?:ться|ть|юсь|емся|етесь|й[а-я]*|ешь|ют))\b",
        "пляж": r"\bпляж[а-я]*\b",
        "жара": r"\bжар(?:а|у|ы|ой|е|к[а-я]*)\b",
    }
    tag_patterns = {
        "море": r"море|моря|морю|морск",
        "купание": r"купан|купаться",
        "пляж": r"пляж",
        "жара": r"жара|жаре|жару|жарк",
    }
    hits = []
    for term in forbidden or []:
        normal = str(term).strip().replace("ё", "е").lower()
        if not normal:
            continue
        matched = bool(re.search(patterns[normal], value)) if normal in patterns else normal in value
        if normal in tag_patterns:
            matched = matched or any(re.search(tag_patterns[normal], tag) for tag in hashtags)
        if matched:
            hits.append(str(term))
    return list(dict.fromkeys(hits))


def strict_qa_flags(verdict, required_flags) -> tuple[bool, list[str]]:
    """JSON booleans only: strings, ints, missing values, lists all fail closed."""
    if not isinstance(verdict, dict):
        return False, ["vision_qa_invalid_envelope"]
    issues = []
    for key in ("pass", *required_flags):
        value = verdict.get(key)
        if type(value) is not bool:
            issues.append("vision_qa_invalid_boolean:" + key)
        elif value is False:
            issues.append("vision_qa_failed:" + key)
    return not issues, issues


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def pending_platforms(record: dict, project: dict) -> list[str]:
    selected = str(project.get("platforms") or "both")
    ids = record.get("ids") if isinstance(record.get("ids"), dict) else {}
    missing = []
    for platform, target, id_field in (("tg", "channel_tg", "message_id"), ("vk", "vk_owner_id", "post_id")):
        if selected not in (platform, "both") or not project.get(target):
            continue
        sent = ids.get(platform) if isinstance(ids.get(platform), dict) else {}
        if not sent.get(id_field):
            missing.append(platform)
    return missing


def ensure_delivery_qa(*, record: dict, project: dict, brief: dict, visual_brief: dict,
                       banner_sha256: str, public_texts: dict, required_flags: tuple,
                       qa_callback, now: datetime | None = None) -> dict:
    """Check only unsent outputs; callback must inspect the same existing image.

    Caller runs this after the post advisory lock and fresh DB read, persists
    returned delivery_quality BEFORE sending, then sends only pending_platforms.
    It must supply the current brief (same idea/CTA, refreshed date and seasonal
    constraints), exact output strings and actual image bytes' SHA-256.  The
    callback enforces the remaining accepted-banner budget and returns its
    actual QA result plus server-produced cost_guard_ok (never a model field).
    """
    pending = pending_platforms(record, project)
    evidence = copy.deepcopy(record.get("delivery_quality")) if isinstance(record.get("delivery_quality"), dict) else {}
    base = {"pending_platforms": pending, "delivery_quality": evidence,
            "rechecked_platforms": [], "blockers": []}
    if not pending:
        return {**base, "pass": True}
    local_date = project_local_now(project, now).date().isoformat()
    blockers = []
    if brief.get("current_date") != local_date:
        blockers.append("delivery_brief_date_mismatch")
    if not re.fullmatch(r"[0-9a-f]{64}", str(banner_sha256)):
        blockers.append("delivery_banner_digest_missing")
    blockers += occasion_blockers(brief.get("occasion"), local_date)
    for platform in pending:
        if not str(public_texts.get(platform) or "").strip():
            blockers.append("delivery_copy_missing:" + platform)
        hits = seasonal_hits(public_texts.get(platform, ""), brief.get("forbidden_mentions") or [])
        if hits:
            blockers.append("season_forbidden:" + platform + ":" + ",".join(hits[:4]))
    if blockers:
        return {**base, "pass": False, "blockers": blockers}

    context_sha = _digest({
        "policy": TEMPORAL_POLICY, "project_id": str(project.get("id") or ""),
        "tz_offset": project_offset(project), "brief": brief,
        "visual_brief": visual_brief, "banner_sha256": banner_sha256,
        "qa_flags": list(required_flags),
    })
    needed = []
    for platform in pending:
        stored = evidence.get(platform) if isinstance(evidence.get(platform), dict) else {}
        passed, _ = strict_qa_flags(stored.get("verdict"), required_flags)
        if not (passed and stored.get("policy_version") == TEMPORAL_POLICY
                and stored.get("context_sha256") == context_sha
                and stored.get("public_text_sha256") == _digest(public_texts[platform])
                and stored.get("cost_guard_ok") is True):
            needed.append(platform)
    if not needed:
        return {**base, "pass": True}
    try:
        # One real QA request can cover all currently unsent platform variants.
        verdict = qa_callback(brief=copy.deepcopy(brief), visual_brief=copy.deepcopy(visual_brief),
                              public_texts={p: public_texts[p] for p in needed},
                              banner_sha256=banner_sha256, context_sha256=context_sha)
    except Exception as exc:
        return {**base, "pass": False, "blockers": ["delivery_qa_unavailable:" + type(exc).__name__]}
    passed, issues = strict_qa_flags(verdict, required_flags)
    if not isinstance(verdict, dict) or verdict.get("cost_guard_ok") is not True:
        passed = False
        issues.append("delivery_qa_cost_guard_not_passed")
    if not passed:
        return {**base, "pass": False, "blockers": issues,
                "rechecked_platforms": needed, "qa_result": verdict}
    for platform in needed:
        evidence[platform] = {
            "policy_version": TEMPORAL_POLICY,
            "context_sha256": context_sha,
            "public_text_sha256": _digest(public_texts[platform]),
            "local_date": local_date, "banner_sha256": banner_sha256,
            "verdict": copy.deepcopy(verdict), "cost_guard_ok": True,
        }
    return {**base, "pass": True, "delivery_quality": evidence,
            "rechecked_platforms": needed, "qa_result": verdict}
