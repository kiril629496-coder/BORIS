from __future__ import annotations
import json, shutil, sys
sys.path.insert(0, "/root/BORIS/backend")
from pathlib import Path
from datetime import datetime, timezone
from app.services import crowd_seo

STATE = Path("/root/BORIS/backend/data/crowd_seo/projects.json")
TARGET = 60
NOW = lambda: datetime.now(timezone.utc).isoformat()

def active_for(rows, domain):
    items = [p for p in rows if p.get("domain") == domain and crowd_seo.project_is_active(p)]
    return max(items, key=lambda p: str(p.get("created_at") or "")) if items else None

def set_contract(p):
    p["plan_mode"] = "contract"
    p["contract_count_source"] = "owner_explicit_target_2026_10_04"
    p["target_count"] = TARGET
    p["bonus_count"] = 0
    p["publish_all_eligible"] = False
    p["owner_action_required"] = False
    p["updated_at"] = NOW()

stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
backup = STATE.with_name(f"projects.json.bak.owner-seo-60.{stamp}")
shutil.copy2(STATE, backup)

rows = crowd_seo._load()
ids = {}

# 1) systems-ai-oko.ru: keep the current live project, convert inventory -> explicit 60-link contract.
systems = active_for(rows, "systems-ai-oko.ru")
if not systems:
    raise RuntimeError("systems_active_project_missing")
set_contract(systems)
ids["systems-ai-oko.ru"] = systems["id"]

# 2) boris-ai.pro: revive the canonical historical project if no newer active project exists.
boris = active_for(rows, "boris-ai.pro")
if not boris:
    candidates = [p for p in rows if p.get("domain") == "boris-ai.pro"]
    if not candidates:
        raise RuntimeError("boris_project_missing")
    boris = max(candidates, key=lambda p: str(p.get("created_at") or ""))
    boris["status"] = "analysis_ready"
    boris["content_status"] = "approved"
    boris.pop("superseded_by", None)
    boris.pop("superseded_at", None)
    boris["offer_type"] = boris.get("offer_type") or "services"
    boris["offer_type_label"] = boris.get("offer_type_label") or "услуги"
set_contract(boris)
ids["boris-ai.pro"] = boris["id"]

# 3) avito-specialist.ru: create a deterministic service project without depending on a slow external site-analysis call.
avito = active_for(rows, "avito-specialist.ru")
if not avito:
    key = "crowd_owner_avito_specialist_20261004"
    avito = {
        "id": key,
        "site": "https://avito-specialist.ru/",
        "domain": "avito-specialist.ru",
        "status": "analysis_ready",
        "client_input": {"site": "https://avito-specialist.ru/", "only_required_field": "site"},
        "niche": "marketing",
        "niche_label": "маркетинг и реклама",
        "offer_type": "services",
        "offer_type_label": "услуги",
        "plan_mode": "contract",
        "contract_count_source": "owner_explicit_target_2026_10_04",
        "target_count": TARGET,
        "bonus_count": 0,
        "target_pages": [
            {"url": "https://avito-specialist.ru/", "label": "Авитолог — продвижение бизнеса на Авито"}
        ],
        "keywords": [
            "авитолог", "продвижение на авито", "реклама на авито",
            "ведение авито", "аудит авито", "авито под ключ",
            "объявления авито", "увеличение заявок с авито"
        ],
        "anchor_plan": [
            {"url": "https://avito-specialist.ru/", "keyword": "авитолог", "mode": "безанкорная", "anchor": "https://avito-specialist.ru/"}
        ],
        "text_variants": [
            {"variant": 1, "text": "Если нужен практический разбор продвижения на Авито, можно посмотреть https://avito-specialist.ru/. На сайте собраны услуги авитолога и подход к работе с объявлениями, трафиком и заявками."},
            {"variant": 2, "text": "Для бизнеса, который хочет системно получать обращения с Авито, есть описание услуг на https://avito-specialist.ru/. Полезно сравнить стратегию, кейсы и формат ведения до запуска рекламы."},
            {"variant": 3, "text": "По теме продвижения на Авито оставлю https://avito-specialist.ru/ — услуги авитолога, аудит аккаунта, объявления и работа с эффективностью размещений."}
        ],
        "forum_candidates": [],
        "placements": [],
        "created_at": NOW(),
        "updated_at": NOW(),
        "guarantee_days": 30,
        "free_only": True,
        "generated_without_openai": True,
        "delivery_rule": "owner SEO: only rule-allowed public placements; no captcha bypass; count only live verified links",
        "owner_action_required": False,
        "publish_all_eligible": False,
    }
    crowd_seo._apply_auto_content_approval(avito)
    rows.append(avito)
else:
    set_contract(avito)
ids["avito-specialist.ru"] = avito["id"]

crowd_seo._save(rows)

results = {}
for domain, pid in ids.items():
    result = {"project_id": pid}
    try:
        result["refresh"] = crowd_seo.refresh_project_forums(pid)
    except Exception as exc:
        result["refresh_error"] = f"{type(exc).__name__}: {exc}"
    try:
        result["prepare"] = crowd_seo.prepare_placement_plan(pid)
    except Exception as exc:
        result["prepare_error"] = f"{type(exc).__name__}: {exc}"
    try:
        result["drafts"] = crowd_seo.sync_project_drafts(pid)
    except Exception as exc:
        result["drafts_error"] = f"{type(exc).__name__}: {exc}"
    try:
        result["replacements"] = crowd_seo.schedule_replacements(pid)
    except Exception as exc:
        result["replacements_error"] = f"{type(exc).__name__}: {exc}"
    p = crowd_seo.get_project(pid)
    result["state"] = {
        "domain": p.get("domain"),
        "status": p.get("status"),
        "content_status": p.get("content_status"),
        "plan_mode": p.get("plan_mode"),
        "target_count": p.get("target_count"),
        "candidates": len(p.get("forum_candidates") or []),
        "placements": len(p.get("placements") or []),
        "publication_urls": sum(bool(x.get("publication_url")) for x in p.get("placements") or []),
        "verified_links": sum(bool(x.get("publication_url")) and x.get("link_present") is True for x in p.get("placements") or []),
    }
    results[domain] = result

print(json.dumps({"ok": True, "backup": str(backup), "results": results, "capacity": crowd_seo.capacity_snapshot()}, ensure_ascii=False, indent=2))
