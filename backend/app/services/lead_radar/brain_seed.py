"""Durable Lead Radar rule capture into the existing BORIS Control Plane.

No second Brain. Uses BORIS native ControlOwnerInstruction + create_requirement,
so UUID ids, requirement version ledger and Control Plane events remain canonical.
Default is read-only dry-run; --apply writes idempotently.
"""
import argparse, hashlib, os, uuid
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

OWNER_TEXT = 'Lead Radar — продаваемый мультиклиентский модуль: для каждого клиента использовать только его собственный каталог товаров и/или услуг, цены, географию, ключевые и минус-слова и его собственные источники; не смешивать клиентов и не наследовать чужие каталоги/источники; без профиля или каталога работать fail-closed; круглосуточно искать весь доступный свежий спрос по 20 группам источников, квалифицировать, дедуплицировать, создавать сделки в существующей BORIS CRM, готовить или отправлять персональные ответы только через разрешённые каналы, соблюдать ограничения площадок/API, делать follow-up и self-heal без превращения владельца в оператора. Наш текущий профиль сохраняет 35 IT-услуг.'
KEY = 'prospecting.lead_radar.autonomous_dev_demand'
TITLE = 'Lead Radar: автономный поиск спроса по товарам и услугам'
NORMALIZED_RULE = 'Lead Radar is a multi-tenant demand engine. Every enabled tenant has an owner-scoped profile, its own product/service catalog, prices, geography, keywords/negative keywords and owner-scoped source configuration. Tenants never inherit another tenant catalog or source state; missing profile/catalog fails closed. For each tenant, BORIS continuously discovers all available fresh demand across the 20 target source groups, merges multi-source signals into one lead, persists evidence, creates/updates existing BORIS CRM, contacts only when source policy permits, obeys provider/API quotas and idempotency, and self-heals before owner escalation. The BORIS owner development tenant currently retains its 35 IT offerings.'
DESIRED_STATE = {
    'enabled': True,
    'catalog_mode': 'owner_scoped_products_services',
    'owner_scoped_catalogs': True,
    'owner_scoped_sources': True,
    'all_enabled_tenants_have_catalog': True,
    'source_groups_count': 20,
    'max_age_hours': 168,
    'persistent_dedupe': True,
    'cross_source_merge': True,
    'crm': 'boris_crm',
    'autocontact': 'policy_gated',
    'internal_daily_volume_cap': None,
    'process_all_available_fresh_leads': True,
    'provider_quotas_obeyed': True,
    'coverage_ready_for_business_pass': True,
    'followup': 'policy_gated',
    'self_heal': True,
    'fail_closed_without_catalog': True,
    'owner_is_operator': False,
}

CONDITIONS = [{'run': 'continuous', 'signals': ['ready_brief','hiring','money','launch','problem','business_gap','partner']}]
ACTIONS = ['scan','normalize','classify','score','dedupe','persist','crm_upsert','prepare_outreach','send_if_permitted','followup','verify','self_heal']
CONSTRAINTS = ['no CAPTCHA bypass','no auth bypass','no prohibited scraping','no bulk unsolicited auto-send','job listing is signal not applicant channel','no BORIS internal daily volume cap','provider/API quotas only','idempotent outreach','per-lead duplicate protection']
VERIFICATION = [
    {'type':'tenant_catalog','rule':'every enabled tenant has its own owner-scoped profile and at least one offering'},
    {'type':'tenant_isolation','rule':'same offering_code may exist in different tenants without catalog/source leakage'},
    {'type':'catalog','rule':'BORIS owner development profile retains 35 IT offerings'},
    {'type':'coverage','expected_source_groups_per_enabled_tenant':20},
    {'type':'runtime_required','required':True},
    {'type':'dedupe','rule':'same lead from multiple sources -> one CRM deal inside the same owner scope'},
    {'type':'restart_idempotency','required':True},
    {'type':'hiring_safety','rule':'hiring signal never auto-applies'},
    {'type':'auto_policy','rule':'auto-send only with explicit source policy'},
    {'type':'followup_policy','rule':'follow-up never upgrades draft to auto'},
    {'type':'self_heal','rule':'source failures change retry strategy and quality gate requalifies unsent leads'},
    {'type':'owner_escalation','rule':'owner escalated only after self-heal exhausted'},
]

DEPENDENCIES = [
    {'type':'runtime_preflight','name':'BORIS database','verified_by':'lead_radar adapter readback'},
    {'type':'runtime_preflight','name':'existing boris_crm schema','verified_by':'lead_radar CRM linkage readback'},
    {'type':'runtime_preflight','name':'source credentials/APIs where required','verified_by':'lead_radar source health; external blocks stay visible in diagnosis'},
]

REQUIRED_INSTRUCTION = {'id','idempotency_key','raw_text','intent','scope_type','scope_id','modules_json','linked_requirement_ids_json','status','compiler_json'}
REQUIRED_REQUIREMENT = {'id','key','title','normalized_rule','requirement_type','scope_type','scope_id','module','priority','status','desired_state_json','conditions_json','actions_json','constraints_json','verification_json','dependencies_json','source_json','version'}

def _owner_id():
    raw=(os.getenv('LEAD_RADAR_OWNER_USER_ID') or '').strip()
    return int(raw) if raw.isdigit() and int(raw)>0 else None

def _idem():
    return hashlib.sha256(('lead-radar-owner-instruction-v5-multitenant:'+KEY).encode()).hexdigest()

def _schema_check(engine):
    ins=inspect(engine); tables=set(ins.get_table_names())
    need={'control_owner_instructions','control_requirements','control_requirement_versions'}
    missing=sorted(need-tables)
    if missing: raise RuntimeError('BRAIN_TABLES_MISSING_FAIL_CLOSED:'+','.join(missing))
    ic={c['name'] for c in ins.get_columns('control_owner_instructions')}
    rc={c['name'] for c in ins.get_columns('control_requirements')}
    mi=sorted(REQUIRED_INSTRUCTION-ic); mr=sorted(REQUIRED_REQUIREMENT-rc)
    if mi: raise RuntimeError('OWNER_INSTRUCTION_SCHEMA_MISMATCH:'+','.join(mi))
    if mr: raise RuntimeError('REQUIREMENT_SCHEMA_MISMATCH:'+','.join(mr))
    return True

def capture(engine, apply=False, printer=print):
    _schema_check(engine)
    printer('TARGET=existing BORIS Control Plane')
    printer('KEY='+KEY)
    if not apply:
        printer('BRAIN_DRY_RUN=PASS')
        return {'dry_run':True,'instruction_id':None,'requirement_id':None}

    # Import the canonical production models/services only after schema preflight.
    from app.models.control_plane import ControlOwnerInstruction, ControlRequirement
    from app.services.control_plane import create_requirement
    Session=sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db=Session()
    created_instruction=False; created_requirement=False
    try:
        iid_key=_idem()
        insrow=db.query(ControlOwnerInstruction).filter(ControlOwnerInstruction.idempotency_key==iid_key).first()
        if insrow is None:
            insrow=ControlOwnerInstruction(
                id=str(uuid.uuid4()), idempotency_key=iid_key, user_id=_owner_id(),
                source='owner_chat', source_ref='lead_radar_v5', account_id=None,
                raw_text=OWNER_TEXT, intent='permanent_rule', requirement_type='automation_rule',
                scope_type='global', scope_id='*', modules_json=['prospecting'],
                linked_requirement_ids_json=[], status='captured',
                compiler_json={
                    'compiler':'lead_radar_native_seed_v5_multitenant','intent':'permanent_rule',
                    'requirement_type':'automation_rule','scope_type':'global','scope_id':'*',
                    'modules':['prospecting'],'desired_state':DESIRED_STATE,
                    'actions':ACTIONS,'constraints':CONSTRAINTS,
                },
            )
            db.add(insrow); db.flush(); created_instruction=True
            printer('CAPTURED_OWNER_INSTRUCTION_ID='+str(insrow.id))
        else:
            printer('OWNER_INSTRUCTION_ALREADY_CAPTURED='+str(insrow.id))

        req=db.query(ControlRequirement).filter(ControlRequirement.key==KEY).first()
        if req is None:
            req=create_requirement(
                db, key=KEY, title=TITLE, normalized_rule=NORMALIZED_RULE,
                requirement_type='automation_rule', scope_type='global', scope_id='*',
                module='prospecting', priority='high', raw_owner_text=OWNER_TEXT,
                desired_state=DESIRED_STATE, conditions=CONDITIONS, actions=ACTIONS,
                constraints=CONSTRAINTS, verification=VERIFICATION,
                dependencies=DEPENDENCIES,
                source={'type':'owner_instruction','instruction_id':str(insrow.id),'source':'owner_chat','source_ref':'lead_radar_v5','allow_control_write':True},
                created_by=_owner_id(),
            )
            created_requirement=True
            printer('CAPTURED_REQUIREMENT_ID='+str(req.id))
            printer('CAPTURED_REQUIREMENT_VERSION='+str(req.version or 1))
        elif str(req.status or '') == 'superseded':
            src=dict(req.source_json or {})
            if src.get('source_ref') not in ('lead_radar_v4','lead_radar_v5'):
                raise RuntimeError('EXISTING_SUPERSEDED_REQUIREMENT_NOT_OURS_FAIL_CLOSED')
            from app.services.control_plane import update_requirement
            update_requirement(db, req, {
                'title': TITLE,
                'normalized_rule': NORMALIZED_RULE,
                'requirement_type': 'automation_rule',
                'scope_type': 'global', 'scope_id': '*', 'module': 'prospecting',
                'priority': 'high', 'status': 'active',
                'desired_state_json': DESIRED_STATE,
                'conditions_json': CONDITIONS, 'actions_json': ACTIONS,
                'constraints_json': CONSTRAINTS, 'verification_json': VERIFICATION,
                'dependencies_json': DEPENDENCIES,
                'source_json': {'type':'owner_instruction','instruction_id':str(insrow.id),'source':'owner_chat','source_ref':'lead_radar_v5','allow_control_write':True},
            }, changed_by=_owner_id(), reason='lead_radar_reapply_after_rollback')
            # Emit the same marker used by rollback: this apply changed the rule and may safely supersede it again on failure.
            printer('CAPTURED_REQUIREMENT_ID='+str(req.id))
            printer('REACTIVATED_REQUIREMENT_VERSION='+str(req.version or 1))
        else:
            src=dict(req.source_json or {})
            if src.get('source_ref') not in ('lead_radar_v4','lead_radar_v5'):
                raise RuntimeError('EXISTING_ACTIVE_REQUIREMENT_NOT_OURS_FAIL_CLOSED')
            needs_update=(
                str(req.title or '')!=TITLE or
                str(req.normalized_rule or '')!=NORMALIZED_RULE or
                dict(req.desired_state_json or {})!=DESIRED_STATE or
                list(req.verification_json or [])!=VERIFICATION or
                src.get('source_ref')!='lead_radar_v5'
            )
            if needs_update:
                from app.services.control_plane import update_requirement
                update_requirement(db, req, {
                    'title': TITLE,
                    'normalized_rule': NORMALIZED_RULE,
                    'requirement_type': 'automation_rule',
                    'scope_type': 'global', 'scope_id': '*', 'module': 'prospecting',
                    'priority': 'high', 'status': 'active',
                    'desired_state_json': DESIRED_STATE,
                    'conditions_json': CONDITIONS, 'actions_json': ACTIONS,
                    'constraints_json': CONSTRAINTS, 'verification_json': VERIFICATION,
                    'dependencies_json': DEPENDENCIES,
                    'source_json': {'type':'owner_instruction','instruction_id':str(insrow.id),
                                    'source':'owner_chat','source_ref':'lead_radar_v5',
                                    'allow_control_write':True},
                }, changed_by=_owner_id(), reason='lead_radar_multitenant_products_services_v5')
                printer('UPDATED_REQUIREMENT_ID='+str(req.id))
                printer('UPDATED_REQUIREMENT_VERSION='+str(req.version or 1))
            else:
                printer('REQUIREMENT_ALREADY_CURRENT='+str(req.id))

        linked=list(insrow.linked_requirement_ids_json or [])
        if str(req.id) not in [str(x) for x in linked]: linked.append(str(req.id))
        insrow.linked_requirement_ids_json=linked
        insrow.status='materialized'
        db.commit()
        printer('BRAIN_CAPTURE=PASS')
        return {'dry_run':False,'instruction_id':str(insrow.id),'requirement_id':str(req.id),'created_instruction':created_instruction,'created_requirement':created_requirement}
    except Exception:
        db.rollback(); raise
    finally:
        db.close()

def rollback_created_requirement(engine, requirement_id, printer=print):
    _schema_check(engine)
    from app.models.control_plane import ControlRequirement
    from app.services.control_plane import update_requirement
    Session=sessionmaker(bind=engine, autoflush=False, expire_on_commit=False); db=Session()
    try:
        row=db.query(ControlRequirement).filter(ControlRequirement.id==str(requirement_id)).first()
        if not row: printer('BRAIN_ROLLBACK=NO_ROW'); return False
        if row.key!=KEY: raise RuntimeError('BRAIN_ROLLBACK_KEY_MISMATCH_FAIL_CLOSED')
        if row.status=='superseded': printer('BRAIN_ROLLBACK=ALREADY_SUPERSEDED'); return True
        update_requirement(db,row,{'status':'superseded'},changed_by=_owner_id(),reason='lead_radar_deploy_rollback')
        db.commit(); printer('BRAIN_ROLLBACK=PASS requirement_id='+str(requirement_id)); return True
    except Exception:
        db.rollback(); raise
    finally:
        db.close()

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--apply',action='store_true'); ap.add_argument('--rollback-created',type=str,default=''); args=ap.parse_args()
    url=os.getenv('DATABASE_URL') or os.getenv('DB_URL') or os.getenv('POSTGRES_URL')
    if not url: raise SystemExit('DATABASE_URL_MISSING')
    try:
        engine=create_engine(url,pool_pre_ping=True)
        if args.rollback_created: rollback_created_requirement(engine,args.rollback_created)
        else: capture(engine,apply=args.apply)
    except Exception as exc:
        raise SystemExit(f'{type(exc).__name__}:{exc}')

if __name__=='__main__': main()
