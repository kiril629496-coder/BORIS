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
from app.services.exception_observability import observe_suppressed


_META_LINE = re.compile(
    r"(?im)^\s*(?:тема|тон|структура|формат(?:\s+подачи)?|cta|"
    r"хэштеги?|теги?|для\s+изображения|визуальн\w*\s+стиль)\s*[:—-].*$"
)
_HASHTAG_LINE = re.compile(r"(?m)^\s*(?:#\w+[\s,]*){1,}\s*$")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.I)


def _project_name(project):
    return str((project or {}).get("name") or "").strip().lower()


def is_ocean(project):
    return "океан клиент" in _project_name(project)


def is_dushi(project):
    return "от души" in _project_name(project)


def is_manicure(project):
    name = _project_name(project)
    return "маникюр евпатория" in name or str((project or {}).get("id") or "") == "270dbf472f7f"


def is_guarded(project):
    return (
        bool((project or {}).get("public_quality_guard"))
        or is_ocean(project)
        or is_dushi(project)
        or is_manicure(project)
    )


def _sanitize(text):
    value = str(text or "")
    value = re.sub(r"```.*?```", "", value, flags=re.S)
    value = value.replace("**", "").replace("__", "").replace(chr(96), "")
    value = _META_LINE.sub("", value)
    value = _HASHTAG_LINE.sub("", value)
    value = re.sub(r"(?m)^\s*#{1,6}\s+", "", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _strip_forced_occasion_lines(text):
    """Remove calendar hooks that were injected only to manufacture a reason to post."""
    value = str(text or "")
    markers = (
        "к дню ", "ко дню ", "на день ",
        "день шахт", "день нефт", "день знаний",
        "день солидар", "день российского флага",
    )
    kept = []
    for line in value.splitlines():
        low = line.lower()
        if any(marker in low for marker in markers):
            continue
        kept.append(line)
    value = "\n".join(kept)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _model_hashtag_block(text):
    """Keep the model's own 5–10 topical hashtags when it produced a valid set."""
    found = re.findall(
        r"(?<![\w#])#[A-Za-zА-Яа-яЁё0-9_]+",
        str(text or ""),
        flags=re.U,
    )
    out = []
    seen = set()
    for tag in found:
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(tag)
        if len(out) >= 10:
            break
    if 5 <= len(out) <= 10:
        return " ".join(out)
    return ""


def _ocean_hashtag_block(text):
    """5-10 thematic hashtags for Ocean, always present in public delivery."""
    hay = str(text or "").lower()
    if any(w in hay for w in ("авито", "автозагруз", "объявлен", "фид", "авитолог")):
        return "#BORIS #авито #авитолог #объявления #автозагрузка #продажи #автоматизация"
    if any(w in hay for w in ("продаж", "лид", "crm", "менеджер", "роп", "клиент", "звонок")):
        return "#BORIS #продажи #лиды #клиенты #отделпродаж #CRM #автоматизация"
    if any(w in hay for w in ("реклам", "маркет", "лидоген", "контент", "соцсет", "vk", "telegram")):
        return "#BORIS #маркетинг #реклама #лидогенерация #контент #соцсети #автоматизация"
    return "#BORIS #бизнес #маркетинг #продажи #клиенты #ИИ #автоматизация"


def _dushi_hashtag_block(text):
    """5-10 thematic hashtags for «От Души», always present."""
    hay = str(text or "").lower()
    if any(w in hay for w in ("свад", "тост", "жених", "невест")):
        return "#отдуши #тост #свадьба #поздравление #речь #подарок #трогательно"
    if any(w in hay for w in ("юбиле", "день рождения", "рождени")):
        return "#отдуши #юбилей #деньрождения #поздравление #стихиназаказ #песняназаказ #подарок"
    if any(w in hay for w in ("учител", "воспитател", "врач", "медик", "професс", "коллег")):
        return "#отдуши #поздравление #профессиональныйпраздник #стихиназаказ #тост #песняназаказ #подарок"
    if any(w in hay for w in ("прощ", "провод", "памят", "речь")):
        return "#отдуши #трогательно #речь #прощальныеслова #стихиназаказ #песняназаказ #важныеслова"
    return "#отдуши #поздравление #стихиназаказ #тост #песняназаказ #подарок #трогательно"


def _manicure_hashtag_block(text):
    """Safe 5–10 fallback tags; model-selected tags remain preferred."""
    hay = str(text or "").lower()
    if any(w in hay for w in ("педикюр", "стоп", "пятк")):
        return "#педикюр #педикюревпатория #уходзаногами #евпатория #крым #красота"
    if any(w in hay for w in ("френч", "дизайн", "металлик", "цвет", "градиент", "омбре")):
        return "#маникюр #маникюревпатория #дизайнногтей #гельлак #идеиманикюра #евпатория"
    if any(w in hay for w in ("море", "отпуск", "лет", "пляж")):
        return "#маникюр #маникюревпатория #маникюрнаморе #евпатория #крым #ногти"
    return "#маникюр #маникюревпатория #ногти #гельлак #евпатория #крым"


def _manicure_clean(text, project=None, platform="vk", fallback=None):
    """Manicure last mile: no contacts/URLs, 5–10 topical tags, no forced alien holiday."""
    candidate = str(text or "")
    if callable(fallback):
        try:
            candidate = fallback(text, project, platform)
        except Exception:
            candidate = str(text or "")
    model_tags = _model_hashtag_block(candidate) or _model_hashtag_block(text)
    candidate = re.sub(r"(?i)(?:\u043a|\u043a\u043e)\s+\u0434\u043d\u044e\s+\u043d\u0435\u0444\u0442\u044f\u043d\u0438\u043a\u0430[^.!?\n]*(?:[.!?]|$)", "", candidate)
    candidate = re.sub(r"(?i)(?:\u043a|\u043a\u043e)\s+\u0434\u043d\u044e\s+\u0448\u0430\u0445\u0442[\u0435\u0451]\u0440\u0430[^.!?\n]*(?:[.!?]|$)", "", candidate)
    value = _sanitize(candidate)
    value = _URL.sub("", value)
    value = re.sub(r"(?<!\w)@[A-Za-z0-9_.-]{2,}", "", value)
    value = re.sub(
        r"(?im)(?:whatsapp|ватсап|telegram|телеграм|написать\s+мне|записаться\s+по|телефон)[:\s].*$",
        "",
        value,
    )
    value = re.sub(
        r"(?<!\d)(?:\+?7|8)[\s()\-]*\d{3}[\s()\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)",
        "",
        value,
    )
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    tags = model_tags or _manicure_hashtag_block(value)
    return (value + "\n\n" + tags).strip() if value else tags


def _ocean_tracking_links(platform="tg"):
    """Trusted Ocean links with short UTM tags visible in Yandex Metrica."""
    source = "vk" if str(platform or "").strip().lower() == "vk" else "tg"
    suffix_boris = "?utm_source=%s&utm_medium=ocean&utm_campaign=boris" % source
    suffix_dev = "?utm_source=%s&utm_medium=ocean&utm_campaign=dev" % source
    return (
        "🤖 BORIS: https://boris-ai.pro/" + suffix_boris + "\n"
        "💻 Разработка ПО: https://boris-ai.pro/software-dev/" + suffix_dev
    )


def _ocean_clean(text, project=None, platform="tg"):
    """Final Ocean delivery: useful post + BORIS/dev tracked links + project signature, no video."""
    model_tags = _model_hashtag_block(text)
    value = _strip_forced_occasion_lines(_sanitize(text))
    # Remove arbitrary/model URLs first. Only trusted tracked BORIS links are appended below.
    value = _URL.sub("", value)
    value = re.sub(
        r"(?im)^\s*(?:"
        r"попробовать\s+BORIS\s+бесплатно.*|"
        r"если\s+хотите\s+проверить\s+BORIS\s+бесплатно.*|"
        r"(?:<b>)?бесплатный\s+тест\s+BORIS(?:</b>)?.*|"
        r"забрать\s+бесплатный\s+тест.*|"
        r"регистрация\s*:.*|YouTube\s*:.*|RuTube\s*:.*|"
        r"🎥.*|Видео\s+BORIS.*|"
        r"🎁.*|🤖\s*BORIS:.*|💻\s*Разработка\s+ПО:.*|"
        r"MAX\s*/\s*WhatsApp\s*:.*|Напишите\s+в\s+MAX\s+или\s+WhatsApp\s*:.*"
        r")\s*$",
        "",
        value,
    )
    value = _HASHTAG_LINE.sub("", value)
    value = re.sub(r"\n{3,}", "\n\n", value).strip()

    signature = str((project or {}).get("footer_signature") or "").strip()
    if signature:
        value = re.sub(re.escape(signature), "", value, flags=re.I)
    value = re.sub(
        r"(?im)^\s*Если хотите проверить BORIS на своей задаче.*?MAX\s*/\s*WhatsApp\s*:\s*8\s*981\s*967[-\s]*37[-\s]*87\.?\s*$",
        "",
        value,
    )
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    tags = model_tags or _ocean_hashtag_block(value)
    links = _ocean_tracking_links(platform)
    footer = "\n\n".join(x for x in (signature, links, tags) if x).strip()

    reserve = len(footer) + 2
    allowed = max(120, 1000 - reserve)
    if len(value) > allowed:
        shortened = value[:allowed].rstrip()
        boundary = max(
            shortened.rfind(". "), shortened.rfind("! "),
            shortened.rfind("? "), shortened.rfind("\n"),
        )
        if boundary >= int(allowed * 0.55):
            shortened = shortened[:boundary + 1].rstrip()
        value = shortened.rstrip(" ,;:-") + "…"

    final = (value + "\n\n" + footer).strip()
    # Video links remain forbidden. Trusted BORIS/dev links must survive.
    final = re.sub(r"(?i)\b(?:youtu\.be|youtube\.com|rutube\.ru)/\S*", "", final)
    final = re.sub(
        r"(?im)^.*(?:🎥\s*BORIS в работе|YouTube\s*:|RuTube\s*:|Видео BORIS регулярно).*$",
        "",
        final,
    )
    final = re.sub(r"\n{3,}", "\n\n", final).strip()
    return final[:1000].rstrip()

def _dushi_clean(text, contact=""):
    """«От Души»: contact + 5-10 thematic hashtags are mandatory."""
    model_tags = _model_hashtag_block(text)
    value = _strip_forced_occasion_lines(_sanitize(text))
    value = _HASHTAG_LINE.sub("", value)
    value = re.sub(
        r"(?im)^\s*Если нужны слова именно под вашу историю\s*[—-]\s*напишите\s*:\s*.*$",
        "",
        value,
    )
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    footer = []
    contact = str(contact or "").strip()
    if value and contact:
        footer.append("Если нужны слова именно под вашу историю — напишите: " + contact)
    footer.append(model_tags or _dushi_hashtag_block(value))
    if value:
        value = value.rstrip() + "\n\n" + "\n\n".join(x for x in footer if x)
    return value.strip()


def render_public_text(text, project, platform="tg", fallback=None):
    """Return the final text that is allowed to leave BORIS."""
    if is_ocean(project):
        return _ocean_clean(text, project, platform)
    if is_dushi(project):
        key = "contact_tg" if platform == "tg" else "contact_vk"
        return _dushi_clean(text, (project or {}).get(key) or "")
    if is_manicure(project):
        return _manicure_clean(text, project, platform, fallback=fallback)
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
    ocean_probe_signature = "Проверочная клиентская подпись BORIS."
    ocean = render_public_text(
        ocean_probe,
        {
            "name": "Океан Клиентов и Борис",
            "footer_signature": ocean_probe_signature,
        },
        "tg",
    )
    dushi = render_public_text(
        dushi_probe, {"name": "От Души", "contact_tg": "@test"}, "tg"
    )
    manicure_probe = (
        "<b>Френч-педикюр</b>\n\nСовет мастера про уход.\n"
        "Ко Дню нефтяника можно выбрать голубой металлик.\n"
        "Записаться по телефону: +7 999 111-22-33\n"
        "https://example.com @master\n"
        "#педикюр #евпатория #френч #голубой #уход #крым"
    )
    manicure = render_public_text(
        manicure_probe, {"name": "Маникюр Евпатория", "id": "270dbf472f7f"}, "vk"
    )

    ocean_ok = (
        len(ocean) <= 1000
        and "Полезная мысль про Авито" in ocean
        and ocean_probe_signature in ocean
        and "Напишите в MAX или WhatsApp:" not in ocean
        and ocean.count("🤖 BORIS:") == 1
        and "#BORIS" in ocean
        and "https://boris-ai.pro/?utm_source=tg&utm_medium=ocean&utm_campaign=boris" in ocean
        and "https://boris-ai.pro/software-dev/?utm_source=tg&utm_medium=ocean&utm_campaign=dev" in ocean
        and "utm_source=vk&utm_medium=ocean&utm_campaign=boris" in _ocean_tracking_links("vk")
        and "utm_source=vk&utm_medium=ocean&utm_campaign=dev" in _ocean_tracking_links("vk")
        and "youtu.be" not in ocean.lower()
        and "youtube.com" not in ocean.lower()
        and "rutube.ru" not in ocean.lower()
        and "🎥" not in ocean
        and "ТОН:" not in ocean
        and "ДЛЯ ИЗОБРАЖЕНИЯ:" not in ocean
    )
    dushi_ok = (
        "@test" in dushi
        and "#отдуши" in dushi
        and "ТОН:" not in dushi
        and "ДЛЯ ИЗОБРАЖЕНИЯ:" not in dushi
    )
    manicure_tags = re.findall(r"(?<![\w#])#[A-Za-zА-Яа-яЁё0-9_]+", manicure, flags=re.U)
    manicure_ok = (
        5 <= len(manicure_tags) <= 10
        and "день нефт" not in manicure.lower()
        and "999 111" not in manicure
        and "example.com" not in manicure
        and "@master" not in manicure
    )
    return {
        "ok": bool(ocean_ok and dushi_ok and manicure_ok),
        "ocean_ok": bool(ocean_ok),
        "dushi_ok": bool(dushi_ok),
        "manicure_ok": bool(manicure_ok),
        "ocean_preview": ocean[:520],
        "dushi_preview": dushi[:360],
        "manicure_preview": manicure[:360],
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

    def guarded_delivery(text, project, platform="tg", **kwargs):
        # SOCIAL_DELIVERY_GUARD_KWARGS_COMPAT_V1:
        # posting_runner may pass delivery metadata such as tracking_id.
        # The owner policy controls public text only; metadata must never make
        # the guard crash or change rate-limit/delivery behavior.
        return render_public_text(text, project, platform, fallback=original_delivery)

    guarded_delivery._boris_social_public_guard = True
    runner._project_delivery_text = guarded_delivery

    if hasattr(runner, "_project_occasion"):
        if not hasattr(runner, "_BORIS_GUARD_ORIGINAL_PROJECT_OCCASION"):
            runner._BORIS_GUARD_ORIGINAL_PROJECT_OCCASION = runner._project_occasion
        original_occasion = runner._BORIS_GUARD_ORIGINAL_PROJECT_OCCASION

        def guarded_occasion(project):
            if is_ocean(project) or is_dushi(project) or is_manicure(project):
                return ""
            return original_occasion(project)

        guarded_occasion._boris_social_public_guard = True
        runner._project_occasion = guarded_occasion

    if hasattr(runner, "_scheduled_generation_plan"):
        if not hasattr(runner, "_BORIS_GUARD_ORIGINAL_SCHEDULED_PLAN"):
            runner._BORIS_GUARD_ORIGINAL_SCHEDULED_PLAN = runner._scheduled_generation_plan
        original_plan = runner._BORIS_GUARD_ORIGINAL_SCHEDULED_PLAN

        def guarded_plan(account_id, project, slot_hour=None, **kwargs):
            # SOCIAL_PLAN_GUARD_KWARGS_COMPAT_V1: preserve recovery metadata such as service_day.
            generation_key, plan = original_plan(account_id, project, slot_hour=slot_hour, **kwargs)
            if not (is_ocean(project) or is_dushi(project) or is_manicure(project)):
                return generation_key, plan
            try:
                if runner._posting_generation_exists(account_id, generation_key):
                    return generation_key, plan
            except Exception as _suppressed_exc:
                observe_suppressed(__name__, _suppressed_exc, line=448)
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
            if (
                not is_manicure(project)
                and configured
                and (theme not in configured or (theme in recent and len(configured) > len(recent)))
            ):
                safe["theme"] = _safe_theme(runner, account_id, project, generation_key)
                changed = True
            try:
                expected_snapshot = runner._build_text_prompt(
                    str(safe.get("theme") or ""),
                    str((project or {}).get("text_prompt") or ""),
                    "",
                    examples=None,
                    allow_hashtags=bool((project or {}).get("allow_hashtags")),
                )
            except TypeError:
                expected_snapshot = runner._build_text_prompt(
                    str(safe.get("theme") or ""),
                    str((project or {}).get("text_prompt") or ""),
                    "",
                    examples=None,
                )
            if str(safe.get("text_prompt_snapshot") or "") != str(expected_snapshot or ""):
                safe["text_prompt_snapshot"] = expected_snapshot
                safe["prompt_snapshot_refreshed"] = True
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


def ensure_owner_project_policy(runner, account_id="u2"):
    """Self-heal durable owner intent without AI/provider/publication calls."""
    db = runner.SessionLocal()
    changed = []
    try:
        row = (
            db.query(Storage)
            .filter(
                Storage.account_id == account_id,
                Storage.key == "posting_projects",
            )
            .with_for_update()
            .first()
        )
        if not row:
            return {"ok": False, "changed": [], "reason": "posting_projects_missing"}
        try:
            projects = json.loads(row.value or "[]")
        except Exception as exc:
            return {
                "ok": False,
                "changed": [],
                "reason": "posting_projects_invalid_json:%s" % type(exc).__name__,
            }
        if not isinstance(projects, list):
            return {"ok": False, "changed": [], "reason": "posting_projects_not_list"}

        required = {
            "public_quality_guard": True,
            "public_copy_policy": "human_editorial_v2",
            "allow_hashtags": True,
            "forced_ecosystem_slot": False,
            "forced_occasion": False,
            "vk_cleanup_order": "newest_first",
            "expected_auto_generation": True,
        }
        for project in projects:
            if not (is_ocean(project) or is_dushi(project) or is_manicure(project)):
                continue
            touched = {}
            for key, expected in required.items():
                if project.get(key) != expected:
                    touched[key] = {"before": project.get(key), "after": expected}
                    project[key] = expected

            # Owner rule: «Океан» reuses the proven old banner library.
            # Do not allow a later UI/deploy edit to silently turn image
            # generation back on.
            if is_ocean(project) and project.get("image_source") != "reuse":
                touched["image_source"] = {
                    "before": project.get("image_source"),
                    "after": "reuse",
                }
                project["image_source"] = "reuse"

            if is_ocean(project):
                for key, expected in (
                    ("video_enabled", False),
                    ("attach_video", False),
                    ("video_url", ""),
                    ("video_source", "none"),
                ):
                    if project.get(key) != expected:
                        touched[key] = {"before": project.get(key), "after": expected}
                        project[key] = expected

            if is_manicure(project):
                for key, expected in (
                    ("contact_tg", ""),
                    ("contact_vk", ""),
                    ("hashtags_policy", "topic_5_10"),
                    ("vk_action_button", "community_message"),
                    ("vk_action_button_label", "Написать сообществу"),
                    ("paid_auto_generation", True),
                ):
                    if project.get(key) != expected:
                        touched[key] = {"before": project.get(key), "after": expected}
                        project[key] = expected

            if touched:
                changed.append(
                    {
                        "project_id": str(project.get("id") or ""),
                        "project": str(project.get("name") or ""),
                        "fields": touched,
                    }
                )

        if changed:
            row.value = json.dumps(projects, ensure_ascii=False)
            db.commit()
        else:
            db.rollback()
        return {"ok": True, "changed": changed, "changed_count": len(changed)}
    except Exception as exc:
        db.rollback()
        return {
            "ok": False,
            "changed": changed,
            "reason": "%s: %s" % (type(exc).__name__, str(exc)[:180]),
        }
    finally:
        db.close()


def stable_social_vk_external_unavailable_health(runner):
    """Use the canonical runner health/self-heal truth."""
    return runner.social_vk_external_unavailable_health()

def _vk_pending_record_retryable(rec):
    if not isinstance(rec, dict) or rec.get("status") != "published":
        return False
    if "vk" not in (rec.get("external_edit_pending") or []):
        return False
    unavailable = rec.get("external_edit_unavailable") or {}
    return not (isinstance(unavailable, dict) and unavailable.get("vk"))


def _vk_project_priority(project):
    return 0 if is_ocean(project) else 1


def _vk_pending_sort_key(item):
    """Newest pending VK publication first across all Social projects."""
    return (str(item[0] or ""), int(item[1] or 0))


def _vk_terminal_storage_key(owner_id, post_id):
    return "social_vk_terminal:%s:%s" % (int(owner_id), int(post_id))


def _load_vk_terminal_tombstones(db):
    result = {}
    for row in db.query(Storage).filter(Storage.key.like("social_vk_terminal:%")).all():
        try:
            payload = json.loads(row.value or "{}")
        except Exception:
            continue
        try:
            owner_id = int(payload.get("owner_id"))
            post_id = int(payload.get("post_id"))
        except Exception:
            parts = str(row.key or "").split(":")
            if len(parts) < 3:
                continue
            try:
                owner_id = int(parts[-2])
                post_id = int(parts[-1])
            except Exception:
                continue
        payload = dict(payload)
        payload["owner_id"] = owner_id
        payload["post_id"] = post_id
        payload["_storage_key"] = str(row.key)
        result[(row.account_id, owner_id, post_id)] = payload
    return result


def _persist_vk_terminal_tombstone(
    runner, account_id, owner_id, post_id, record_id="", project_id="",
    reason="VK_EDIT_WINDOW_EXPIRED", code=15,
):
    db = runner.SessionLocal()
    try:
        key = _vk_terminal_storage_key(owner_id, post_id)
        row = db.query(Storage).filter(
            Storage.account_id == account_id, Storage.key == key
        ).first()
        previous = {}
        if row and row.value:
            try:
                previous = json.loads(row.value)
            except Exception:
                previous = {}
        payload = {
            "owner_id": int(owner_id),
            "post_id": int(post_id),
            "record_id": record_id or previous.get("record_id"),
            "project_id": project_id or previous.get("project_id"),
            "reason": reason,
            "code": int(code),
            "first_verified_at": previous.get("first_verified_at")
                or datetime.now().strftime("%Y-%m-%d %H:%M"),
            "evidence": previous.get("evidence") or "vk_api_terminal_edit_error",
            "owner_action_required": False,
        }
        if row:
            row.value = json.dumps(payload, ensure_ascii=False)
        else:
            db.add(Storage(
                account_id=account_id,
                key=key,
                value=json.dumps(payload, ensure_ascii=False),
            ))
        db.commit()
        payload["_storage_key"] = key
        return payload
    finally:
        db.close()


def _vk_pending_delivery_text(text, project):
    """Re-render historical VK backlog through the current owner policy."""
    value = str(text or "")
    if is_guarded(project):
        value = render_public_text(value, project, "vk")
        if not str(value or "").strip():
            raise ValueError("PUBLIC_COPY_EMPTY_AFTER_SANITIZE")
    return value


def stable_social_vk_pending_cleanup_once(runner):
    """Delegate VK cleanup to the single canonical runner engine."""
    return runner.social_vk_pending_cleanup_once()

