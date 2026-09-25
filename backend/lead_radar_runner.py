"""One safe multi-tenant Lead Radar cycle for systemd.

The timer is global, but execution is owner-scoped:
- every enabled Lead Radar profile is discovered from DB;
- each tenant gets its own catalog/connectors/single-flight cycle;
- one tenant failure never prevents the remaining tenants from running;
- the legacy LEAD_RADAR_OWNER_USER_ID is fallback-only when no profiles exist.
"""
import os
import sys
from sqlalchemy import create_engine, select

from app.services.lead_radar.db import profiles
from app.services.lead_radar.repository import SQLLeadRepository
from app.services.lead_radar.registry import build_connectors
from app.services.lead_radar.cycle import run_cycle
from app.services.lead_radar.owner_scope import configured_owner


def _enabled_owners(repo):
    with repo.engine.begin() as c:
        return [
            int(x) for x in c.execute(
                select(profiles.c.owner_user_id)
                .where(profiles.c.enabled == True)
                .order_by(profiles.c.owner_user_id)
            ).scalars().all()
        ]


def run_all(engine):
    repo=SQLLeadRepository(engine)
    repo.install()
    owners=_enabled_owners(repo)
    legacy_fallback=False
    if not owners:
        try:
            owners=[configured_owner()]
            legacy_fallback=True
        except RuntimeError as exc:
            return {
                'status':'BLOCKED_CONFIG',
                'reason':str(exc),
                'tenants':[],
                'successful_tenants':0,
                'failed_tenants':0,
                'processed':0,
            }

    tenant_results=[]
    successful=0
    failed=0
    processed=0
    duplicates=0

    for owner in owners:
        item={'owner_user_id':owner}
        try:
            profile,catalog=repo.require_catalog(owner)
            item['profile_name']=profile.get('name')
            item['offerings_count']=len(catalog)
            connectors=build_connectors(owner,profile=profile,catalog=catalog)
            item['connectors_count']=len(connectors)
            if not connectors:
                item.update({
                    'status':'BLOCKED_CONFIG',
                    'reason':'NO_TENANT_SOURCES_CONFIGURED',
                    'processed':0,
                })
                failed+=1
                tenant_results.append(item)
                continue

            result=run_cycle(engine,connectors)
            item['result']=result
            item['status']=str((result.get('health') or {}).get('status') or 'UNKNOWN').upper()
            item['processed']=int(result.get('processed') or 0)
            item['duplicates']=int(result.get('duplicates') or 0)
            processed+=item['processed']
            duplicates+=item['duplicates']
            # A cycle that is waiting only on an external/owner dependency is not
            # a crashed worker. Keep tenant health BLOCKED for visibility, but let
            # systemd stay green so a provider backoff cannot masquerade as an
            # internal runtime failure. Real errors, overdue schedulers and active
            # self-heal states remain fail-closed.
            health=result.get('health') or {}
            blocked_without_progress=item['status']=='BLOCKED' and item['processed']==0
            waiting_dependency=(
                blocked_without_progress
                and not (result.get('errors') or [])
                and not bool(health.get('scheduler_attention_required'))
                and not bool(health.get('self_heal_active'))
                and bool(health.get('owner_action_required') or int(health.get('sources_blocked_external') or 0)>0)
            )
            if waiting_dependency:
                item['dependency_state']='waiting_external_or_owner'
                successful+=1
            elif blocked_without_progress:
                failed+=1
            else:
                successful+=1
        except Exception as exc:
            item.update({
                'status':'ERROR',
                'reason':f'{type(exc).__name__}:{str(exc)[:500]}',
                'processed':0,
            })
            failed+=1
        tenant_results.append(item)

    if successful>0:
        status='PASS' if failed==0 else 'DEGRADED'
    else:
        status='BLOCKED'

    return {
        'status':status,
        'legacy_fallback':legacy_fallback,
        'tenants':tenant_results,
        'successful_tenants':successful,
        'failed_tenants':failed,
        'processed':processed,
        'duplicates':duplicates,
    }


def main():
    url=os.getenv('DATABASE_URL') or os.getenv('DB_URL') or os.getenv('POSTGRES_URL')
    if not url:
        print({'status':'BLOCKED_CONFIG','reason':'DATABASE_URL_MISSING','tenants':[]})
        return 2
    result=run_all(create_engine(url,pool_pre_ping=True))
    print(result)
    # Keep timer alive on partial tenant degradation; return non-zero only when
    # no enabled tenant could complete a cycle at all.
    return 1 if result.get('status')=='BLOCKED' else 0


if __name__=='__main__':
    sys.exit(main())
