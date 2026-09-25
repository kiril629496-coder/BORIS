from datetime import datetime,timezone,timedelta
from .repository import SQLLeadRepository
from .engine import LeadRadar
from .crm_adapter import BorisCRMAdapter
from .health import health
from .policy_loader import load_source_policies
from .connectors import ConnectorSenderRouter
from .coverage import source_coverage

EXTERNAL_MARKERS=('captcha','forbidden','401','invalid token','access denied','account blocked')
def _is_external_block(ex): return any(x in str(ex).lower() for x in EXTERNAL_MARKERS)

def _connector_since(connector,since,last_success,now):
    overlap=max(0,int(getattr(connector,'incremental_overlap_seconds',0) or 0))
    bootstrap=max(0,int(getattr(connector,'bootstrap_lookback_seconds',0) or 0))
    if last_success is not None and overlap:
        return max(since,last_success-timedelta(seconds=overlap))
    if last_success is None and bootstrap:
        return max(since,now-timedelta(seconds=bootstrap))
    return since

def run_cycle(engine,connectors,sender=None,now=None):
    owners={int(getattr(x,'owner',0) or 0) for x in connectors}
    if len(owners)!=1 or 0 in owners: raise RuntimeError('LEAD_RADAR_CYCLE_REQUIRES_EXACTLY_ONE_OWNER')
    owner_user_id=next(iter(owners))
    repo=SQLLeadRepository(engine); repo.install(); repo.require_catalog(owner_user_id); now=now or datetime.now(timezone.utc)
    with repo.cycle_guard(owner_user_id) as acquired:
        if not acquired:
            return {'processed':0,'duplicates':0,'errors':[],'skipped_backoff':[], 'outreach_retries':[], 'followups':[],
                    'skipped_single_flight':True,'health':health(engine,now,owner_user_id=owner_user_id),'coverage':source_coverage(connectors)}
        policies=load_source_policies(); sender=sender or ConnectorSenderRouter(connectors); radar=LeadRadar(repo,BorisCRMAdapter(engine),sender,source_policies=policies,enforce_profile=True)
        quality_recheck=radar.requalify_active_unsent(owner_user_id=owner_user_id,now=now)
        processed=0; duplicates=0; errors=[]; skipped_backoff=[]; since=now-timedelta(days=7)
        for connector in connectors:
            policy=policies.get(connector.key) or policies.get(getattr(connector,'family',''))
            repo.ensure_source(connector.key,getattr(connector,'family','unknown'),policy,owner_user_id=owner_user_id)
            if not repo.source_due(connector.key,now,owner_user_id=owner_user_id): skipped_backoff.append(connector.key); continue
            last_success=repo.source_last_success(connector.key,owner_user_id=owner_user_id)
            source_since=_connector_since(connector,since,last_success,now)
            repo.source_scan_start(connector.key,now,owner_user_id=owner_user_id)
            try:
                for raw in connector.fetch(source_since):
                    result=radar.process(raw,now=now)
                    if isinstance(result,dict) and result.get('duplicate_of'): duplicates+=1
                    elif result is not None: processed+=1
                repo.source_success(connector.key,now,owner_user_id=owner_user_id)
            except Exception as ex:
                state=repo.source_failure(connector.key,ex,now,blocked_external=_is_external_block(ex),owner_user_id=owner_user_id)
                errors.append({'source':connector.key,'error':str(ex)[:500],**{k:(v.isoformat() if hasattr(v,'isoformat') else v) for k,v in state.items()}})
        outreach_retries=radar.process_outreach_retries(now=now,owner_user_id=owner_user_id)
        followups=radar.process_due_followups(now=now,owner_user_id=owner_user_id); state=health(engine,now,owner_user_id=owner_user_id)
        return {'quality_recheck':quality_recheck,'processed':processed,'duplicates':duplicates,'errors':errors,'skipped_backoff':skipped_backoff,'outreach_retries':outreach_retries,'followups':followups,'skipped_single_flight':False,'health':state,'coverage':source_coverage(connectors)}
