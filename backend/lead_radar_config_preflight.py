"""Validate Lead Radar runtime configuration without network or DB writes.

Runs after BORIS .env is loaded and before backup/apply. Constructors only: no source fetch.
"""
import os
from app.services.lead_radar.registry import build_connectors
from app.services.lead_radar.policy_loader import load_source_policies
from app.services.lead_radar.coverage import source_coverage


def run(owner_user_id=None, printer=print):
    raw = owner_user_id if owner_user_id is not None else os.getenv('LEAD_RADAR_OWNER_USER_ID','')
    try:
        owner = int(raw or 0)
    except Exception:
        owner = 0
    # Policies can be validated even if no live sources are enabled.
    policies = load_source_policies()
    # LEAD_RADAR_PREFLIGHT_ALL_SOURCE_FAMILIES_V1
    configured = any([
        bool(os.getenv('LEAD_RADAR_RSS_SOURCES_JSON','').strip()),
        bool(os.getenv('LEAD_RADAR_HTTP_SOURCES_JSON','').strip()),
        os.getenv('LEAD_RADAR_HH_ENABLED','0').lower() in ('1','true','yes','on'),
        os.getenv('LEAD_RADAR_HN_ENABLED','0').lower() in ('1','true','yes','on'),
        os.getenv('LEAD_RADAR_APPLE_ENABLED','0').lower() in ('1','true','yes','on'),
        os.getenv('LEAD_RADAR_TED_ENABLED','0').lower() in ('1','true','yes','on'),
        os.getenv('LEAD_RADAR_GITHUB_ENABLED','0').lower() in ('1','true','yes','on'),
        os.getenv('LEAD_RADAR_FREELANCER_ENABLED','0').lower() in ('1','true','yes','on'),
        os.getenv('LEAD_RADAR_HN_SHOW_ENABLED','0').lower() in ('1','true','yes','on'),
        os.getenv('LEAD_RADAR_HN_JOBS_ENABLED','0').lower() in ('1','true','yes','on'),
        os.getenv('LEAD_RADAR_BORIS_TELEGRAM_ENABLED','0').lower() in ('1','true','yes','on'),
        os.getenv('LEAD_RADAR_BORIS_CRM_COMPANIES_ENABLED','0').lower() in ('1','true','yes','on'),
        os.getenv('LEAD_RADAR_BORIS_COMPANY_SITES_ENABLED','0').lower() in ('1','true','yes','on'),
        os.getenv('LEAD_RADAR_BORIS_COLD_B2B_ENABLED','0').lower() in ('1','true','yes','on'),
        os.getenv('LEAD_RADAR_OPEN_WEB_ENABLED','0').lower() in ('1','true','yes','on'),
    ])
    connectors=[]
    if configured:
        if owner <= 0:
            raise RuntimeError('LEAD_RADAR_OWNER_REQUIRED_WHEN_SOURCES_CONFIGURED')
        connectors = build_connectors(owner)
        if not connectors:
            raise RuntimeError('LEAD_RADAR_SOURCES_CONFIGURED_BUT_NO_CONNECTORS')
        for connector in connectors:
            policy=policies.get(connector.key) or policies.get(getattr(connector,'family',''))
            if policy and policy.allows_automation:
                if not (getattr(connector,'supports_outreach',False) and getattr(connector,'outreach_idempotent',False)):
                    raise RuntimeError('LEAD_RADAR_AUTO_REQUIRES_IDEMPOTENT_SENDER:'+connector.key)
    cov=source_coverage(connectors)
    printer(f"LEAD_RADAR_RUNTIME_CONFIG=PASS sources={len(connectors)} policies={len(policies)} groups={cov['configured_groups']}/{cov['target_groups']}")
    return {'sources':len(connectors),'policies':len(policies),'coverage':cov}


if __name__=='__main__':
    try:
        run()
    except RuntimeError as e:
        raise SystemExit(str(e))
