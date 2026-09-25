# -*- coding: utf-8 -*-
"""Stable runtime safety boundary for BORIS Social.

Critical public-copy and VK reconciliation rules live outside posting_runner.py
because that legacy file is concurrently edited by several autonomous jobs.
"""
import hashlib
import json
import re
from datetime import datetime

from app.models.storage import Storage


_META_LINE = re.compile(
    r"(?im)^\s*(?:тема|тон|структура|формат(?:\s+подачи)?|cta|"
    r"хэштеги?|теги?|для\s+изображения|визуальн\w*\s+стиль)\s*[:—-].*$"
)
_HASHTAG_LINE = re.compile(r"(?m)^\s*(?:#\w+[\s,]*){1,}\s*$")
_URL = re.compile(r"https?://\S+", re.I)


def _project_name(project):
    return str((project or {}).get("name") or "").strip().lower()


def is_ocean(project):
    return "океан клиент" in _project_name(project)


def is_dushi(project):
    return "от души" in _project_name(project)


def is_guarded(project):
    return bool((project or {}).get("public_quality_guard")) or is_ocean(project) or is_dushi(project)


def _sanitize(text):
    value = str(text or "")
    value = re.sub(r"```.*?```", "", value, flags=re.S)
    value = value.replace("**", "").replace("__", "").replace(chr(96), "")
    value = _META_LINE.sub("", value)
    value = _HASHTAG_LINE.sub("", value)
    value = re.sub(r"(?m)^\s*#{1,6}\s+", "", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _ocean_hashtag_block(text):
    hay = str(text or "").lower()
    if any(w in hay for w in ("авито", "автозагруз", "объявлен", "фид", "авитолог")):
        return "#BORIS #авито #авитолог #объявления #автоматизация"
    if any(w in hay for w in ("продаж", "лид", "crm", "менеджер", "роп", "клиент")):
        return "#BORIS #продажи #клиенты #отделпродаж #автоматизация"
    return "#BORIS #бизнес #маркетинг #продажи #автоматизация"


def _ocean_clean(text):
    """Final Ocean delivery: useful thought + mandatory BORIS footer, no links/video."""
    value = _sanitize(text)
    value = _URL.sub("", value)
    value = re.sub(
        r"(?im)^\s*(?:"
        r"попробовать\s+BORIS\s+бесплатно.*|"
        r"если\s+хотите\s+проверить\s+BORIS\s+бесплатно.*|"
        r"(?:<b>)?бесплатный\s+тест\s+BORIS(?:</b>)?.*|"
        r"забрать\s+бесплатный\s+тест.*|"
        r"регистрация\s*:.*|YouTube\s*:.*|RuTube\s*:.*|"
        r"🎥.*|🎁.*|🤖\s*BORIS:.*"
        r")\s*$",
        "",
        value,
    )
    value = _HASHTAG_LINE.sub("", value)
    value = re.sub(r"\n{3,}", "\n\n", value).strip()

    modules = (
        "🤖 BORIS: AI-Авитолог — объявления и Авито; AI-менеджер — ответы и реактивация; "
        "AI-РОП — контроль продаж; CRM — клиенты и сделки; телефония + коллтрекинг — звонки и источник; "
        "сквозная аналитика — реклама → сделка; анализ звонков — качество продаж; "
        "AI-Директолог — кампании; VK/TG — контент и автопостинг; "
        "AI-фотостудия — тексты и баннеры; Command Center — план/факт, лиды и CPL."
    )
    trial = (
        "🎁 Настройка BORIS и сопровождение первого месяца — бесплатно.\n"
        "Напишите в MAX или WhatsApp: 8 981 967-37-87."
    )
    footer = modules + "\n\n" + trial + "\n\n" + _ocean_hashtag_block(value)

    reserve = len(footer) + 2
    allowed = max(120, 1000 - reserve)
    if len(value) > allowed:
        shortened = value[:allowed].rstrip()
        boundary = max(
            shortened.rfind(". "),
            shortened.rfind("! "),
            shortened.rfind("? "),
            shortened.rfind("\n"),
        )
        if boundary >= int(allowed * 0.55):
            shortened = shortened[:boundary + 1].rstrip()
        value = shortened.rstrip(" ,;:-") + "…"

    final = (value + "\n\n" + footer).strip()
    final = _URL.sub("", final)
    final = re.sub(r"(?i)\b(?:youtu\.be|youtube\.com|rutube\.ru|boris-ai\.pro)/\S*", "", final)
    final = re.sub(r"(?im)^.*(?:🎥\s*BORIS в работе|YouTube\s*:|RuTube\s*:).*$", "", final)
    final = re.sub(r"\n{3,}", "\n\n", final).strip()
    return final[:1000].rstrip()


def _dushi_clean(text, contact=""):
    value = _sanitize(text)
    value = _HASHTAG_LINE.sub("", value)
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    if value and contact:
        digest = int(hashlib.sha1(value.encode("utf-8", "ignore")).hexdigest()[:8], 16)
        if digest % 3 == 0:
            value += "\n\nЕсли нужны слова именно под вашу историю — напишите: " + str(contact).strip()
    return value


def render_public_text(text, project, platform="tg", fallback=None):
    """Return the final text that is allowed to leave BORIS."""
    if is_ocean(project):
        return _ocean_clean(text)
    if is_dushi(project):
        key = "contact_tg" if platform == "tg" else "contact_vk"
        return _dushi_clean(text, (project or {}).get(key) or "")
    if callable(fallback):
        return fallback(text, project, platform)
    return str(text or "")


def public_copy_runtime_boundary_health():
    ocean_probe = (
        "<b>Проверка границы</b>\n\nПолезная мысль про Авито.\n"
        "ТОН: разговорный\nДЛЯ ИЗОБРАЖЕНИЯ: синий фон\n"
        "Попробовать BORIS бесплатно\n"
        "🎥 BORIS в работе: https://youtu.be/example\n"
        "#старый #мусор"
    )
    dushi_probe = (
        "<b>Тёплые слова</b>\n\nИстория важнее шаблона.\n"
        "ТОН: тёплый\n#поздравление #тост #отдуши"
    )
    ocean = render_public_text(
        ocean_probe, {"name": "Океан Клиентов и Борис"}, "tg"
    )
    dushi = render_public_text(
        dushi_probe, {"name": "От Души", "contact_tg": "@test"}, "tg"
    )

    forbidden_ocean = (
        "http://", "https://", "youtu.be", "youtube.com", "rutube.ru",
        "🎥 BORIS в работе", "ТОН:", "ДЛЯ ИЗОБРАЖЕНИЯ:",
    )
    required_ocean = (
        "AI-Авитолог", "AI-менеджер", "AI-РОП", "CRM",
        "коллтрекинг", "сквозная аналитика", "анализ звонков",
        "Command Center",
        "Настройка BORIS и сопровождение первого месяца — бесплатно",
        "8 981 967-37-87",
    )
    ocean_ok = (
        len(ocean) <= 1000
        and all(x.lower() not in ocean.lower() for x in forbidden_ocean)
        and all(x.lower() in ocean.lower() for x in required_ocean)
        and len([x for x in ocean.split() if x.startswith("#")]) >= 5
    )
    dushi_ok = (
        "#" not in dushi
        and "ТОН:" not in dushi
        and "ДЛЯ ИЗОБРАЖЕНИЯ:" not in dushi
    )
    return {
        "ok": bool(ocean_ok and dushi_ok),
        "ocean_ok": bool(ocean_ok),
        "dushi_ok": bool(dushi_ok),
        "ocean_preview": ocean[:320],
        "dushi_preview": dushi[:240],
    }


def _recent_plan_themes(runner, account_id, project_id, exclude_generation_key="", limit=8):
    themes = []
    db = runner.SessionLocal()
    try:
        rows = db.query(Storage).filter(
            Storage.account_id == account_id,
            Storage.key.like("posting_generation_plan:" + str(project_id) + ":%"),
        ).order_by(Storage.id.desc()).limit(max(2, int(limit) + 1)).all()
        for row in rows:
            try:
                value = json.loads(row.value or "{}")
            except Exception:
                continue
            if str((value or {}).get("generation_key") or "") == str(exclude_generation_key or ""):
                continue
            theme = str((value or {}).get("theme") or "").strip()
            if theme and theme not in themes:
                themes.append(theme)
            if len(themes) >= int(limit):
                break
    finally:
        db.close()
    return themes


def _safe_theme(runner, account_id, project, generation_key):
    pool = [str(x).strip() for x in ((project or {}).get("themes") or []) if str(x).strip()]
    if not pool:
        return "практический совет без рекламного шаблона"
    recent = _recent_plan_themes(
        runner, account_id, (project or {}).get("id") or "", generation_key, 8
    )
    candidates = [x for x in pool if x not in recent] or pool
    digest = int(hashlib.sha1(str(generation_key).encode("utf-8", "ignore")).hexdigest()[:8], 16)
    return candidates[digest % len(candidates)]


def _persist_generation_plan(runner, account_id, project_id, plan):
    slot = str((plan or {}).get("slot") or "")
    if not slot:
        return False
    key = "posting_generation_plan:" + str(project_id) + ":" + slot
    db = runner.SessionLocal()
    try:
        row = db.query(Storage).filter(
            Storage.account_id == account_id,
            Storage.key == key,
        ).first()
        payload = json.dumps(plan, ensure_ascii=False)
        if row:
            row.value = payload
        else:
            db.add(Storage(account_id=account_id, key=key, value=payload))
        db.commit()
        return True
    finally:
        db.close()


def install_posting_runner_guard(runner):
    """Re-assert in-memory guards after importing mutable posting_runner."""
    if not hasattr(runner, "_BORIS_GUARD_ORIGINAL_PROJECT_DELIVERY"):
        runner._BORIS_GUARD_ORIGINAL_PROJECT_DELIVERY = getattr(runner, "_project_delivery_text", None)
    original_delivery = runner._BORIS_GUARD_ORIGINAL_PROJECT_DELIVERY

    def guarded_delivery(text, project, platform="tg"):
        return render_public_text(text, project, platform, fallback=original_delivery)

    guarded_delivery._boris_social_public_guard = True
    runner._project_delivery_text = guarded_delivery

    if hasattr(runner, "_project_occasion"):
        if not hasattr(runner, "_BORIS_GUARD_ORIGINAL_PROJECT_OCCASION"):
            runner._BORIS_GUARD_ORIGINAL_PROJECT_OCCASION = runner._project_occasion
        original_occasion = runner._BORIS_GUARD_ORIGINAL_PROJECT_OCCASION

        def guarded_occasion(project):
            if is_ocean(project) or is_dushi(project):
                return ""
            return original_occasion(project)

        guarded_occasion._boris_social_public_guard = True
        runner._project_occasion = guarded_occasion

    if hasattr(runner, "_scheduled_generation_plan"):
        if not hasattr(runner, "_BORIS_GUARD_ORIGINAL_SCHEDULED_PLAN"):
            runner._BORIS_GUARD_ORIGINAL_SCHEDULED_PLAN = runner._scheduled_generation_plan
        original_plan = runner._BORIS_GUARD_ORIGINAL_SCHEDULED_PLAN

        def guarded_plan(account_id, project, slot_hour=None):
            generation_key, plan = original_plan(account_id, project, slot_hour=slot_hour)
            if not (is_ocean(project) or is_dushi(project)):
                return generation_key, plan
            try:
                if runner._posting_generation_exists(account_id, generation_key):
                    return generation_key, plan
            except Exception:
                pass
            safe = dict(plan or {})
            changed = False
            if safe.get("occasion"):
                safe["occasion"] = ""
                changed = True
            configured = [
                str(x).strip() for x in ((project or {}).get("themes") or []) if str(x).strip()
            ]
            theme = str(safe.get("theme") or "").strip()
            recent = _recent_plan_themes(
                runner, account_id, (project or {}).get("id") or "", generation_key, 8
            )
            canonical_ocean_ecosystem = bool(
                is_ocean(project)
                and "виртуальная команда маркетинга и продаж" in theme.lower()
            )
            if (
                configured
                and not canonical_ocean_ecosystem
                and (theme not in configured or (theme in recent and len(configured) > len(recent)))
            ):
                safe["theme"] = _safe_theme(runner, account_id, project, generation_key)
                changed = True
            if changed:
                _persist_generation_plan(
                    runner, account_id, (project or {}).get("id") or "", safe
                )
                plan = safe
            return generation_key, plan

        guarded_plan._boris_social_public_guard = True
        runner._scheduled_generation_plan = guarded_plan

    return {
        "ok": True,
        "delivery_guard": bool(
            getattr(runner._project_delivery_text, "_boris_social_public_guard", False)
        ),
        "occasion_guard": bool(
            getattr(getattr(runner, "_project_occasion", None), "_boris_social_public_guard", False)
        ),
        "plan_guard": bool(
            getattr(getattr(runner, "_scheduled_generation_plan", None), "_boris_social_public_guard", False)
        ),
    }


def _vk_pending_record_retryable(rec):
    if not isinstance(rec, dict) or rec.get("status") != "published":
        return False
    if "vk" not in (rec.get("external_edit_pending") or []):
        return False
    unavailable = rec.get("external_edit_unavailable") or {}
    return not (isinstance(unavailable, dict) and unavailable.get("vk"))


def _vk_pending_sort_key(item):
    """Newest created_at first when sorted with reverse=True."""
    return (str(item[0] or ""), int(item[1]))


def stable_social_vk_pending_cleanup_once(runner):
    """Retry one VK quality edit, newest-first, with terminal code-15 handling."""
    db = runner.SessionLocal()
    try:
        project_map = {}
        for prow in db.query(Storage).filter(Storage.key == "posting_projects").all():
            try:
                projects = json.loads(prow.value or "[]")
            except Exception:
                projects = []
            for project in projects:
                project_map[(prow.account_id, str(project.get("id") or ""))] = project

        pending = []
        for row in db.query(Storage).filter(Storage.key == "posting_posts").all():
            try:
                posts = json.loads(row.value or "[]")
            except Exception:
                continue
            for rec in posts:
                if not _vk_pending_record_retryable(rec):
                    continue
                ids = rec.get("ids") or {}
                vk = ids.get("vk") if isinstance(ids, dict) else {}
                if not isinstance(vk, dict) or vk.get("owner_id") is None or vk.get("post_id") is None:
                    continue
                project = project_map.get(
                    (row.account_id, str(rec.get("project_id") or ""))
                ) or {}
                pending.append(
                    (
                        str(rec.get("created_at") or ""),
                        int(vk["post_id"]),
                        row.account_id,
                        rec.get("id"),
                        int(vk["owner_id"]),
                        rec.get("text", ""),
                        str(project.get("name") or ""),
                    )
                )
    finally:
        db.close()

    pending.sort(key=lambda item: (item[0], item[1]), reverse=True)
    if not pending:
        runner._clear_social_vk_backoff()
        return {"status": "idle", "checked": 0}

    created_at, post_id, account_id, record_id, owner_id, text_value, project_name = pending[0]
    remaining = int(runner._social_vk_backoff_remaining() or 0)
    if remaining > 0:
        return {
            "status": "backoff",
            "checked": 0,
            "retry_after_seconds": remaining,
            "next_retry_in_seconds": remaining,
            "pending_count": len(pending),
            "next_project": project_name,
            "next_post_id": post_id,
            "owner_action_required": False,
        }

    result = runner.edit_vk_post(owner_id, post_id, text_value)
    if result.get("ok"):
        runner._clear_social_vk_backoff()
        fresh = runner._load_post_record(account_id, record_id)
        if fresh:
            left = [x for x in (fresh.get("external_edit_pending") or []) if x != "vk"]
            if left:
                fresh["external_edit_pending"] = left
            else:
                fresh.pop("external_edit_pending", None)
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            fresh["vk_quality_edited_at"] = stamp
            fresh["external_quality_edited_at"] = stamp
            runner._persist_post_record(account_id, fresh)
        return {"status": "fixed_pending", "post_id": post_id, "checked": 1}

    code = int(result.get("code") or 0)
    if code == 14:
        retry_after = int(runner._set_social_vk_backoff(21600) or 21600)
        return {
            "status": "captcha",
            "stage": "pending_edit",
            "post_id": post_id,
            "code": 14,
            "error": str(result.get("error") or "")[:180],
            "checked": 1,
            "retry_after_seconds": retry_after,
            "next_retry_in_seconds": retry_after,
            "pending_count": len(pending),
            "owner_action_required": False,
        }

    if code == 15:
        runner._clear_social_vk_backoff()
        fresh = runner._load_post_record(account_id, record_id)
        if fresh:
            left = [x for x in (fresh.get("external_edit_pending") or []) if x != "vk"]
            if left:
                fresh["external_edit_pending"] = left
            else:
                fresh.pop("external_edit_pending", None)
            unavailable = dict(fresh.get("external_edit_unavailable") or {})
            unavailable["vk"] = {
                "reason": "VK_EDIT_WINDOW_EXPIRED",
                "code": 15,
                "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
            fresh["external_edit_unavailable"] = unavailable
            runner._persist_post_record(account_id, fresh)
        return {
            "status": "skipped_uneditable",
            "stage": "pending_edit",
            "post_id": post_id,
            "code": 15,
            "checked": 1,
            "owner_action_required": False,
        }

    return {
        "status": "error",
        "stage": "pending_edit",
        "post_id": post_id,
        "code": code,
        "error": str(result.get("error") or "")[:180],
        "checked": 1,
    }
