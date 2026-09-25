"""Explicit per-source contact policy loader.
AUTO is never inferred from a raw lead. It must be enabled here by deployment config.
"""
import json,os
from .models import SourcePolicy
from .source_policy import DEFAULT_SOURCE_POLICIES

def load_source_policies():
    out=dict(DEFAULT_SOURCE_POLICIES); raw=os.getenv('LEAD_RADAR_SOURCE_POLICY_JSON','').strip()
    if not raw: return out
    try: cfg=json.loads(raw)
    except Exception as e: raise RuntimeError(f'LEAD_RADAR_SOURCE_POLICY_INVALID_JSON:{e}')
    if not isinstance(cfg,dict): raise RuntimeError('LEAD_RADAR_SOURCE_POLICY_MUST_BE_OBJECT')
    for key,row in cfg.items():
        if not isinstance(row,dict): raise RuntimeError(f'LEAD_RADAR_SOURCE_POLICY_INVALID:{key}')
        family=str(row.get('family') or key)
        applicant=bool(row.get('applicant_channel',False)); auto=bool(row.get('allows_automation',False))
        if applicant and auto: raise RuntimeError(f'UNSAFE_APPLICANT_AUTOMATION_FORBIDDEN:{key}')
        # Legacy field is parsed only for backward compatibility and is NOT enforced.
        # Lead Radar has no internal daily volume cap; provider/API quotas live in connector scheduling.
        # Legacy max_auto_per_day is accepted in old deployment JSON but intentionally ignored.
        row.get('max_auto_per_day',None)
        if auto and not row.get('allows_business_contact'):
            raise RuntimeError(f'AUTO_REQUIRES_CONTACT_PERMISSION:{key}')
        out[key]=SourcePolicy(key=key,family=family,allows_business_contact=bool(row.get('allows_business_contact',False)),
            allows_automation=auto,applicant_channel=applicant,
            min_interval_seconds=max(60,int(row.get('min_interval_seconds',300) or 300)),
            followup_max=max(0,min(3,int(row.get('followup_max',0) or 0))),enabled=bool(row.get('enabled',True)))
    return out
