import json
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session
from tests.test_campaign_run4_closure import qa
from app.models.campaign import Campaign
from app.models.campaign_item import CampaignItem
from app.models.background_job import BackgroundJob
from app.models.storage import Storage
from app.services import campaign_editor_jobs as E,campaign_editor_acceptance as A,jobs
from app.api import campaigns as C

@pytest.fixture
def setup(qa,monkeypatch):
 engine,client=qa
 with Session(engine) as db:
  db.execute(text("UPDATE accounts SET account_id='qa_durable' WHERE account_id='qa-a'"))
  c=db.get(Campaign,1);c.default_account_id='qa_durable';c.name='QA durable fixture'
  r=db.get(CampaignItem,101);r.account_id='qa_durable';r.status='draft';r.avito_item_id=None
  config={'mode':'zero_external','owner_user_id':1,'item_ids':[101],'operations':['texts'],'expires_at':(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat(),'provider_wait_seconds':0}
  db.add(Storage(account_id='qa_durable',key=A.KEY+'1',value=json.dumps(config)));db.commit()
 monkeypatch.setattr(jobs,'_campaign_job_context',lambda db,j,p:(C,db.get(Campaign,1),SimpleNamespace(id=1,role='owner',email='owner@example.test'),['qa_durable'],'qa_durable'))
 return engine,client

def test_real_queue_local_provider_and_schema(setup):
 engine,client=setup
 with Session(engine) as db:v=db.get(CampaignItem,101).updated_at.isoformat()
 r=client.post('/api/campaign/1/workflow/texts',headers={'x-qa-user':'1'},json={'item_ids':[101],'expected_updated_at':{'101':v},'instructions':'QA'})
 assert r.status_code==200,r.text
 data=r.json();job=data['job'];assert job['started_at'] is None and job['recovery_count']==0 and job['result']=={}
 with Session(engine) as db:
  j=db.get(BackgroundJob,data['job_id']);result,state=E.run(db,j,dict(j.payload_json));assert state=='completed' and result['provider_stub']
  assert db.query(Storage).filter(Storage.key.like('campaign_editor_qa_receipt:%')).count()==1
  assert json.loads(db.get(CampaignItem,101).payload_json)['title'].startswith('QA durable text')
  assert E.run(db,j,dict(j.payload_json))[1]=='completed'
  assert db.query(Storage).filter(Storage.key.like('campaign_editor_qa_receipt:%')).count()==1

@pytest.mark.parametrize('violation',['expired','wrong_owner','published','other_account','unlisted_item','wrong_mode'])
def test_internal_lease_fails_closed(setup,violation):
 engine,client=setup
 with Session(engine) as db:
  c=db.get(Campaign,1);r=db.get(CampaignItem,101);lease=db.query(Storage).filter(Storage.key==A.KEY+'1').one();config=json.loads(lease.value)
  if violation=='expired':config['expires_at']=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
  if violation=='wrong_owner':config['owner_user_id']=9
  if violation=='published':r.avito_item_id='real-id'
  if violation=='other_account':r.account_id='qa-b'
  if violation=='unlisted_item':config['item_ids']=[]
  if violation=='wrong_mode':config['mode']='external'
  lease.value=json.dumps(config);db.commit()
  job=SimpleNamespace(id=1,account_id='qa_durable',owner_user_id=1,payload_json={'item_ids':[101]})
  with pytest.raises(HTTPException) as e:A.operation_for(db,c,SimpleNamespace(id=1,role='owner'),job,'texts')
  assert e.value.status_code==403
