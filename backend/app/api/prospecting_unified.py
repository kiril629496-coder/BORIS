from fastapi import APIRouter, HTTPException

from app.services import prospect_unified as svc
from app.services import service_marketplace as marketplace

router = APIRouter(prefix="/api/prospecting/unified", tags=["prospecting-unified"])

@router.get("/status")
def status():
    return svc.status()

@router.get("/config")
def config():
    return svc.config()

@router.post("/purge-nonfree-pending")
def purge_nonfree_pending():
    return marketplace.purge_nonfree_pending_drafts()

@router.get("/rules")
def rules():
    from app.services import platform_rules
    return {"items": platform_rules.all_status()}

@router.post("/rules/{platform}/inspect")
def inspect_rules(platform: str):
    from app.services import platform_rules
    try:
        return platform_rules.inspect_platform(platform)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"rule inspection failed: {type(exc).__name__}: {exc}")

@router.get("/rules/{platform}/gate")
def rule_gate(platform: str, action: str = "proactive"):
    from app.services import platform_rules
    return platform_rules.publish_gate(platform, action=action)

@router.get("/pricing")
def pricing():
    from app.services import service_pricing
    return {"summary": service_pricing.summary(), "items": service_pricing.list_items()}

@router.get("/guardian")
def guardian():
    return svc.guardian_status()

@router.get("/discovery")
def discovery():
    from app.services import forum_discovery
    return {
        "status": forum_discovery.status(),
        "items": forum_discovery.list_candidates(),
    }

@router.post("/discovery/run")
def discovery_run():
    from app.services import forum_discovery
    try:
        return forum_discovery.discover(max_queries=30, per_query=6, sleep_s=0.15)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"forum discovery failed: {type(exc).__name__}: {exc}")
