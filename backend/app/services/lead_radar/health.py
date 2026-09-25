from datetime import datetime, timezone, timedelta
from sqlalchemy import select,func,and_,or_
from .db import sources,leads,outreach
from .scoring import QUALITY_VERSION

def health(engine,now=None,owner_user_id=None):
    now=now or datetime.now(timezone.utc)
    with engine.begin() as c:
        lead_scope=[] if owner_user_id is None else [leads.c.owner_user_id==owner_user_id]
        src_scope=[] if owner_user_id is None else [sources.c.owner_user_id==owner_user_id]
        total=c.execute(select(func.count()).select_from(leads).where(*lead_scope)).scalar_one()
        fresh=c.execute(select(func.count()).select_from(leads).where(*lead_scope,leads.c.discovered_at>=now-timedelta(hours=24))).scalar_one()
        oq=select(func.count()).select_from(outreach.join(leads,outreach.c.lead_id==leads.c.id)).where(outreach.c.status=='queued',outreach.c.created_at<now-timedelta(minutes=15),*lead_scope)
        stuck=c.execute(oq).scalar_one()
        retrying=c.execute(select(func.count()).select_from(leads).where(*lead_scope,leads.c.status=='outreach_retrying')).scalar_one()
        outreach_blocked=c.execute(select(func.count()).select_from(leads).where(*lead_scope,leads.c.status=='outreach_blocked')).scalar_one()
        recent_rejected=and_(
            leads.c.status=='rejected',
            leads.c.first_contact_at.is_(None),
            leads.c.published_at>=now-timedelta(hours=168),
        )
        quality_pending=c.execute(select(func.count()).select_from(leads).where(
            *lead_scope,
            or_(leads.c.status.in_(('qualified','draft_ready','ready_to_contact')),recent_rejected),
            func.coalesce(leads.c.quality_version,'')!=QUALITY_VERSION,
        )).scalar_one()
        # LEAD_RADAR_EXTERNAL_BACKOFF_TRUTH_V1: provider rate-limit backoff is
        # an external dependency with an automatic retry time, not an internal
        # BORIS recovery failure. Keep it visible separately so supervision can
        # report WAIT_EXTERNAL without opening a false self-heal incident.
        waiting_external=c.execute(select(func.count()).select_from(sources).where(
            *src_scope,sources.c.enabled==True,sources.c.consecutive_failures>=1,
            sources.c.blocked_external==False,
            sources.c.recovery_strategy.like('provider_backoff_%')
        )).scalar_one()
        recovering=c.execute(select(func.count()).select_from(sources).where(
            *src_scope,sources.c.enabled==True,sources.c.consecutive_failures>=1,
            sources.c.blocked_external==False,
            or_(sources.c.recovery_strategy.is_(None),~sources.c.recovery_strategy.like('provider_backoff_%'))
        )).scalar_one()
        external=c.execute(select(func.count()).select_from(sources).where(*src_scope,sources.c.enabled==True,sources.c.blocked_external==True)).scalar_one()
        grace=now-timedelta(minutes=10)
        overdue=c.execute(select(func.count()).select_from(sources).where(*src_scope,sources.c.enabled==True,or_(
            and_(sources.c.next_scan_at.is_not(None),sources.c.next_scan_at<grace),
            and_(sources.c.next_scan_at.is_(None),sources.c.created_at<grace)
        ))).scalar_one()
    internal_bad=bool(stuck or retrying or outreach_blocked or quality_pending or recovering or overdue)
    if external>0 or outreach_blocked>0:
        status='BLOCKED'
    elif internal_bad:
        status='DEGRADED'
    elif waiting_external>0:
        status='WAIT_EXTERNAL'
    else:
        status='PASS'
    return {'status':status,'total_leads':total,'fresh_24h':fresh,'stuck_outreach':stuck,'outreach_retrying':retrying,'outreach_blocked':outreach_blocked,
            'quality_version_current':QUALITY_VERSION,'quality_recheck_pending':quality_pending,
            'sources_recovering':recovering,'sources_waiting_external':waiting_external,
            'sources_blocked_external':external,'sources_overdue':overdue,
            'self_heal_active':bool(stuck or retrying or recovering or quality_pending),'scheduler_attention_required':overdue>0,
            'owner_action_required':bool(external>0 or outreach_blocked>0)}
