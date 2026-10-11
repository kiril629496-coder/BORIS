#!/usr/bin/env python3
"""Import existing live marketing/KPI policy into BORIS Control Plane.

This is a one-way bootstrap of declared production settings, not a mutation of
Avito or money state. Zero/missing money limits are never normalized as desired
state: they remain visible safety gaps for Guardian.
"""
from __future__ import annotations
import json, sys
sys.path.insert(0,'/root/BORIS/backend')
from app.db.session import SessionLocal
from app.models.control_plane import ControlRequirement
from app.models.storage import Storage
from app.services.control_plane import create_requirement, update_requirement
from kpi_goal_runner import get_goal_auto_accounts


def load_json(db,a,k):
    r=db.query(Storage).filter(Storage.account_id==a,Storage.key==k).order_by(Storage.id.desc()).first()
    if not r:return {}
    try:return json.loads(r.value or '{}')
    except Exception:return {}


def main():
    accounts=get_goal_auto_accounts(); db=SessionLocal(); created=updated=0
    try:
        for item in accounts:
            a=item['account_id']; kpi=load_json(db,a,'kpi_settings'); ap=load_json(db,a,'autopilot_settings')
            desired={
                'autopilot_mode':str(ap.get('mode') or item.get('autopilot_mode') or ''),
                'target_leads_per_day':float(kpi.get('target_leads_per_day') or item.get('target_leads_per_day') or 0),
                'max_cost_per_lead_rub':float(kpi.get('max_cost_per_lead_rub') or item.get('max_cost_per_lead_rub') or 0),
            }
            budget=float(kpi.get('daily_budget_limit_rub') or item.get('daily_budget_limit_rub') or 0)
            hard=float(kpi.get('hard_max_bid_rub') or 0)
            if budget>0: desired['daily_budget_limit_rub']=budget
            if hard>0: desired['hard_max_bid_rub']=hard
            key=f'production.marketing.autopilot.{a}'[:160]
            source={'type':'legacy_production_import','authoritative_after_import':True,'allow_override':True,'bootstrap':'control_plane_v1'}
            verification=[{'level':'L5','type':'actual_settings_readback','required':True},{'level':'L6','type':'kpi_effect','required':True}]
            actions=[{'type':'diagnose_on_kpi_fail'},{'type':'execute_with_money_guard'}]
            constraints=[{'type':'max_cpl','value':desired['max_cost_per_lead_rub']},{'type':'daily_budget_required','value':budget if budget>0 else None}]
            row=db.query(ControlRequirement).filter(ControlRequirement.key==key).first()
            if row is None:
                create_requirement(db,key=key,title=f'ИИ-маркетолог: {a}',normalized_rule='Автопилот обязан соблюдать KPI и финансовые ограничения, диагностировать отклонения и подтверждать фактическое действие.',requirement_type='kpi_rule',scope_type='account',scope_id=a,module='marketing',priority='high',desired_state=desired,actions=actions,constraints=constraints,verification=verification,source=source)
                created+=1
            else:
                patch={
                    'desired_state_json':desired,'actions_json':actions,'constraints_json':constraints,
                    'verification_json':verification,'source_json':source,'status':'active','priority':'high'
                }
                # Avoid version churn on repeated bootstrap when effective values did not change.
                changed=any(getattr(row,k)!=v for k,v in patch.items())
                if changed:
                    update_requirement(db,row,patch,reason='production settings bootstrap refresh'); updated+=1
        db.commit()
        print(json.dumps({'status':'pass','accounts':len(accounts),'created':created,'updated':updated},ensure_ascii=False))
        return 0
    except Exception:
        db.rollback(); raise
    finally:db.close()

if __name__=='__main__':raise SystemExit(main())
