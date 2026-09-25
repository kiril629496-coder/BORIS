import os
from datetime import datetime
from fastapi import APIRouter,HTTPException,Query,Depends
from pydantic import BaseModel,Field
from sqlalchemy import create_engine,select
from app.api.auth import get_current_user,require_owner
from app.services.lead_radar.repository import SQLLeadRepository
from app.services.lead_radar.engine import LeadRadar
from app.services.lead_radar.crm_adapter import BorisCRMAdapter
from app.services.lead_radar.models import RawLead
from app.services.lead_radar.health import health as radar_health
from app.services.lead_radar.registry import build_connectors
from app.services.lead_radar.cycle import run_cycle
from app.services.lead_radar.db import sources
from app.services.lead_radar.owner_scope import assert_owner_scope
from app.services.lead_radar.coverage import source_coverage

router=APIRouter(prefix='/api/lead-radar',tags=['lead-radar'])

def _engine():
    url=os.getenv('DATABASE_URL') or os.getenv('DB_URL') or os.getenv('POSTGRES_URL')
    if not url: raise HTTPException(503,'DATABASE_URL_MISSING')
    return create_engine(url,pool_pre_ping=True)
def _repo(): return SQLLeadRepository(_engine())
def _uid(user): return int(getattr(user,'id',0) or 0)
def _radar_uid(user):
    try: return assert_owner_scope(_uid(user))
    except PermissionError as e: raise HTTPException(403,str(e))
    except RuntimeError as e: raise HTTPException(503,str(e))

class OfferingConfig(BaseModel):
    offering_code:int=Field(ge=1)
    external_key:str=Field(min_length=1,max_length=120)
    kind:str='service'
    name:str=Field(min_length=1,max_length=500)
    keywords:list[str]=Field(min_length=1)
    negative_keywords:list[str]=Field(default_factory=list)
    price_min_rub:int|None=Field(default=None,ge=0)
    price_max_rub:int|None=Field(default=None,ge=0)
    currency:str='RUB'
    enabled:bool=True
    metadata_json:dict=Field(default_factory=dict)

class ProfileConfig(BaseModel):
    name:str=Field(min_length=1,max_length=250)
    business_type:str='services'
    mode:str='generic'
    geography:list[str]=Field(default_factory=list)
    intent_keywords:list[str]=Field(default_factory=list)
    negative_keywords:list[str]=Field(default_factory=list)
    min_score:int=Field(default=45,ge=1,le=100)
    currency:str='RUB'
    enabled:bool=True
    settings:dict=Field(default_factory=dict)
    offerings:list[OfferingConfig]=Field(min_length=1)

class Ingest(BaseModel):
    source:str; external_id:str=''; url:str=''; title:str; text:str=''; published_at:datetime
    source_family:str=''; author:str=''; company_name:str=''; company_domain:str=''; location:str=''; contact_hint:str=''
    source_allows_outreach:bool=False; source_allows_automation:bool=False; metadata:dict=Field(default_factory=dict)

@router.get('/profile')
def get_profile(user=Depends(require_owner)):
    uid=_radar_uid(user); repo=_repo(); repo.install()
    profile=repo.get_profile(uid)
    if not profile:
        raise HTTPException(404,'LEAD_RADAR_PROFILE_NOT_CONFIGURED')
    return {'ok':True,'profile':profile,'offerings':repo.get_offerings(uid,enabled_only=False)}

@router.put('/profile')
def put_profile(body:ProfileConfig,user=Depends(require_owner)):
    uid=_radar_uid(user); repo=_repo(); repo.install()
    business_type=body.business_type.strip().lower()
    mode=body.mode.strip().lower()
    if business_type not in ('products','services','mixed'):
        raise HTTPException(422,'business_type must be products, services or mixed')
    if mode not in ('generic','development'):
        raise HTTPException(422,'mode must be generic or development')
    rows=[x.model_dump() for x in body.offerings]
    codes=[x['offering_code'] for x in rows]; keys=[x['external_key'] for x in rows]
    if len(codes)!=len(set(codes)) or len(keys)!=len(set(keys)):
        raise HTTPException(422,'offering_code/external_key must be unique inside client catalog')
    for row in rows:
        if row.get('price_min_rub') is not None and row.get('price_max_rub') is not None and row['price_min_rub']>row['price_max_rub']:
            raise HTTPException(422,f"invalid price range: {row['external_key']}")
        if row.get('kind') not in ('product','service'):
            raise HTTPException(422,f"invalid offering kind: {row['external_key']}")
    repo.upsert_profile(
        uid,name=body.name,business_type=business_type,mode=mode,
        geography_json=body.geography,intent_keywords=body.intent_keywords,
        negative_keywords=body.negative_keywords,min_score=body.min_score,
        currency=body.currency,enabled=body.enabled,settings_json=body.settings
    )
    repo.replace_offerings(uid,rows)
    return {'ok':True,'profile':repo.get_profile(uid),'offerings':repo.get_offerings(uid,enabled_only=False)}

@router.post('/ingest')
def ingest(body:Ingest,user=Depends(require_owner)):
    e=_engine(); repo=SQLLeadRepository(e); repo.install(); radar=LeadRadar(repo,BorisCRMAdapter(e),enforce_profile=True)
    q=radar.process(RawLead(**body.model_dump(),owner_user_id=_radar_uid(user)))
    if q is None: return {'ok':True,'duplicate':True}
    if isinstance(q,dict): return {'ok':True,**q}
    return {'ok':True,'lead':q.public_dict()}

@router.post('/scan')
def scan(user=Depends(require_owner)):
    connectors=build_connectors(_radar_uid(user))
    if not connectors: raise HTTPException(409,'NO_EXPLICIT_SOURCES_CONFIGURED')
    return {'ok':True,**run_cycle(_engine(),connectors)}

@router.get('/leads')
def list_leads(status:str|None=None,limit:int=Query(100,ge=1,le=500),user=Depends(require_owner)):
    # lead rows are owner-scoped in DB; owner UI currently exposes the owner radar only.
    return {'ok':True,'items':_repo().list_leads(limit,status,owner_user_id=_radar_uid(user))}

@router.get('/sources')
def list_sources(user=Depends(require_owner)):
    e=_engine();
    with e.begin() as c: rows=[dict(x) for x in c.execute(select(sources).where(sources.c.owner_user_id==_radar_uid(user)).order_by(sources.c.key)).mappings().all()]
    return {'ok':True,'items':rows}

@router.get('/stats')
def stats(user=Depends(require_owner)):
    # TODO on production apply: DB-level owner filter can replace this owner UI aggregate if multi-owner radar is enabled.
    return {'ok':True,**_repo().stats(owner_user_id=_radar_uid(user))}

@router.get('/coverage')
def coverage(user=Depends(require_owner)):
    uid=_radar_uid(user); return {'ok':True,**source_coverage(build_connectors(uid))}

@router.get('/health')
def health(user=Depends(require_owner)):
    uid=_radar_uid(user); state=radar_health(_engine(),owner_user_id=uid); cov=source_coverage(build_connectors(uid))
    state['source_coverage']=cov
    state['business_coverage_status']='PASS' if cov['coverage_ready'] else 'INCOMPLETE'
    return state
