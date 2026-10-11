"""Isolated PostgreSQL acceptance. Never runs a production worker or provider."""
import json, uuid
from types import SimpleNamespace
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema
from app.db.session import engine as production_engine
from app.models.campaign import Campaign
from app.models.campaign_item import CampaignItem
from app.models.media_link import MediaLink
from app.models.media_asset import MediaAsset
from app.models.storage import Storage
from app.models.background_job import BackgroundJob
from app.api import campaigns
from app.services.campaign_history import page,public_text,csv_chunk,OLD_DETAILS

@pytest.fixture
def qa(monkeypatch):
    schema="qa_campaign_run4_"+uuid.uuid4().hex[:12]
    admin=create_engine(production_engine.url)
    with admin.begin() as c:c.execute(CreateSchema(schema))
    engine=create_engine(production_engine.url,connect_args={"options":"-csearch_path="+schema})
    try:
        for model in (Campaign,CampaignItem,MediaLink,MediaAsset,Storage,BackgroundJob):model.__table__.create(engine)
        with engine.begin() as c:
            c.execute(text("CREATE TABLE accounts (account_id text primary key,owner_user_id integer)"))
            c.execute(text("CREATE TABLE user_account_access (user_id integer,account_id text,can_view boolean,can_manage_ads boolean)"))
            c.execute(text("CREATE TABLE users (id integer primary key,email text)"))
            c.execute(text("CREATE TABLE action_log (id bigserial primary key,ts timestamptz default now(),account_id text,actor text,user_id integer,source text,action text,object_kind text,object_name text,before_val text,after_val text,reason text)"))
            c.execute(text("INSERT INTO accounts VALUES ('qa-a',1),('qa-b',9)"))
            c.execute(text("INSERT INTO users VALUES (1,'owner@example.test'),(2,'Анна'),(3,'viewer@example.test')"))
            c.execute(text("INSERT INTO user_account_access VALUES (2,'qa-a',true,true),(3,'qa-a',true,false)"))
        with Session(engine) as db:
            db.add(Campaign(id=1,name="Проект столов",default_account_id="qa-a",owner_user_id=1,settings_json='{}'))
            db.add(Campaign(id=2,name="Чужой проект",default_account_id="qa-b",owner_user_id=9,settings_json='{}'))
            db.flush()
            for n,status in [(101,"published"),(102,"draft"),(103,"superseded")]:
                db.add(CampaignItem(id=n,campaign_id=1,account_id="qa-a",status=status,source="custom-client-source",avito_item_id=str(n),payload_json=json.dumps({"title":"Стол","description":"Из массива","price":100,"category":"Мебель","params":{"Material":"Дерево"},"images":[]})))
            db.add(CampaignItem(id=201,campaign_id=2,account_id="qa-b",status="published",payload_json='{"title":"Чужой товар"}'))
            db.add(Storage(account_id="qa-a",key="daily_stats:2026-10-03",value=json.dumps({"date":"2026-10-03","items":[{"id":str(n),"views":2,"contacts":1,"status":"active","campaign_identity":{"campaign_item_id":n}} for n in [101,102,103]]})))
            db.commit()
        with engine.begin() as c:
            c.execute(text("SELECT setval(pg_get_serial_sequence('campaigns','id'),(SELECT max(id) FROM campaigns))"))
            for n in range(8):
                c.execute(text("INSERT INTO action_log(account_id,actor,user_id,source,action,object_kind,object_name,ts) VALUES ('qa-a',:actor,2,:source,:action,'объявление','Стол','2026-10-02T12:00:00Z')"),{"actor":"user" if n%2 else "boris","source":["campaign.item_patch:1:101","campaign.publish:1:101","campaign.ab:1:101","campaign.failed:1:101"][n%4],"action":["Изменил объявление","Публикация","A/B тест","Ошибка"][n%4]})
            c.execute(text("INSERT INTO action_log(account_id,actor,source,action,object_name) VALUES ('qa-b','user','campaign.item_patch','Чужая тайна','Чужой товар')"))
        app=FastAPI();app.include_router(campaigns.router)
        def session():
            with Session(engine,autoflush=False) as db:yield db
        def user(request:Request):
            n=int(request.headers.get("x-qa-user","2"))
            return SimpleNamespace(id=n,role="owner" if n==1 else "employee",email="qa-"+str(n))
        app.dependency_overrides[campaigns._db]=session
        app.dependency_overrides[campaigns.get_current_user]=user
        # Legacy logger uses an independent global connection: isolate it.
        monkeypatch.setattr(campaigns,"log_action",lambda **kwargs:None)
        from app.db import session as db_session
        monkeypatch.setattr(db_session,"SessionLocal",lambda:Session(engine))
        yield engine,TestClient(app)
    finally:
        engine.dispose()
        with admin.begin() as c:c.execute(DropSchema(schema,cascade=True))
        admin.dispose()

def test_history_cursor_search_filters_and_tenant_scope(qa):
    engine,client=qa
    def get(**kw):
        r=client.get("/api/campaign/timeline/search",params={"account_id":"qa-a",**kw});assert r.status_code==200,r.text;return r.json()
    first=get(limit=3)
    with engine.begin() as c:c.execute(text("INSERT INTO action_log(account_id,source,action) VALUES ('qa-a','campaign.ab','Новое событие')"))
    second=get(limit=3,cursor=first["next_cursor"])
    third=get(limit=3,cursor=second["next_cursor"])
    ids=[x["id"] for p in (first,second,third) for x in p["items"]]
    assert ids==list(range(8,0,-1))
    assert all(x["account"]=="qa-a" for p in (first,second,third) for x in p["items"])
    assert get(q="Анна")["items"]
    assert get(q="Проект столов")["items"]
    assert get(q="Чужая тайна")["items"]==[]
    assert get(q="%%")["items"]==[]
    for cat in ["boris","staff","changes","publications","ab","errors"]:assert get(category=cat)["items"]
    assert len(get(period="custom",start="2026-10-02",end="2026-10-02")["items"])==8
    assert client.get("/api/campaign/timeline/search",params={"account_id":"qa-b"}).status_code==403
    assert client.get("/api/campaign/timeline/search",params={"account_id":"qa-b","cursor":first["next_cursor"]}).status_code==403
    assert client.get("/api/campaign/timeline/search",params={"account_id":"qa-a","q":"changed","cursor":first["next_cursor"]}).status_code==422
    assert all(x["details_notice"]==OLD_DETAILS for x in first["items"])

@pytest.mark.parametrize("secret",['access_token=abc123','{"cookie":"private"}',"Traceback internal stack","a"*64,"provider template_id depends_on"])
def test_history_safe_public_fields(secret):
    s=public_text(secret)
    assert secret not in s
    assert "template_id" not in s and "provider" not in s

def test_export_allowlist_and_formula_injection(qa):
    engine,client=qa
    with engine.begin() as c:
        c.execute(text("INSERT INTO action_log(account_id,actor,source,action,object_name,reason) VALUES ('qa-a','user','campaign.item_patch','=HYPERLINK(1)','access_token=SECRET','{raw:private}')"))
    r=client.get("/api/campaign/timeline/export",params={"account_id":"qa-a"})
    assert r.status_code==200
    assert "Дата;Время;Аккаунт;Проект;Объявление;Действие;Кто;Статус;Длительность" in r.text
    assert "'=HYPERLINK(1)" in r.text
    assert "SECRET" not in r.text and "raw:private" not in r.text and "Чужая" not in r.text
    assert client.get("/api/campaign/timeline/export",params={"account_id":"qa-b"}).status_code==403

def test_ab_pause_resume_cas_journal_and_permissions(qa):
    engine,client=qa
    def version():
        with Session(engine) as db:return db.get(CampaignItem,101).updated_at.isoformat()
    def act(action,v=None,user=2,**kw):
        return client.post("/api/campaign/ab-tests/action",headers={"x-qa-user":str(user)},json={"account_id":"qa-a","item_id":101,"action":action,"expected_updated_at":v or version(),**kw})
    r=client.get("/api/campaign/ab-tests",params={"account_id":"qa-a"});assert r.status_code==200,r.text
    assert [x["item_id"] for x in r.json()["items"]]==[101]
    assert r.json()["items"][0]["experiment_state"]=="collecting"
    assert r.json()["leaders"]==[]
    old=version()
    r=act("pause",old);assert r.status_code==200,r.text
    assert act("resume",old).status_code==409
    assert act("resume").status_code==200
    assert act("pause",user=3).status_code==403
    assert act("finish",user=2,confirmed=True).status_code==403
    assert act("finish",user=1,confirmed=False).status_code==409
    assert act("scale",user=2,count=2).status_code==403
    journal=client.get("/api/campaign/ab-tests/101/journal",params={"account_id":"qa-a","limit":1}).json()
    assert len(journal["items"])==1 and journal["next_cursor"]
    assert journal["observations"]==[{"date":"2026-10-03","views":2,"contacts":1,"conversion":50.0}]
    nextpage=client.get("/api/campaign/ab-tests/101/journal",params={"account_id":"qa-a","cursor":journal["next_cursor"]}).json()
    assert nextpage["items"][0]["id"]!=journal["items"][0]["id"]
    assert client.get("/api/campaign/ab-tests/201/journal",params={"account_id":"qa-a"}).status_code==404
    with Session(engine) as db:
        row=db.get(CampaignItem,101)
        assert row.status=="published" and row.avito_item_id=="101"
        assert "ab_finish_requested" not in json.loads(row.payload_json)
        assert not db.query(Storage).filter(Storage.key=="feed_items").count()
        assert db.query(CampaignItem).count()==4

def test_boris_edit_uses_existing_durable_job_without_running_it(qa):
    engine,client=qa
    with Session(engine) as db:v=db.get(CampaignItem,101).updated_at.isoformat()
    r=client.post("/api/campaign/ab-tests/action",json={"account_id":"qa-a","item_id":101,"action":"edit","prompt":"Сократить описание без новых фактов","expected_updated_at":v})
    assert r.status_code==200,r.text
    assert r.json().get("job"),r.text
    with Session(engine) as db:
        jobs=db.query(BackgroundJob).all()
        assert len(jobs)==1 and jobs[0].kind=="campaign_enhance" and jobs[0].status=="queued"
        assert jobs[0].payload_json["expected_updated_at"]["101"]==v
        assert jobs[0].started_at is None
    r=client.get("/api/campaign/tasks",params={"account_id":"qa-a"})
    assert r.status_code==200,r.text
    assert any(x["title"]=="Улучшение текстов" for x in r.json()["items"])

def test_full_snapshot_restore_including_media_order_and_hero(qa,monkeypatch):
    engine,client=qa
    with Session(engine) as db:
        for i in [11,12]:
            db.add(MediaAsset(id=i,owner_user_id=1,account_id="qa-a",filename=f"qa-{i}.jpg",storage_key=f"qa/{i}.jpg",source_provider="user_upload"))
        db.flush()
        row=db.get(CampaignItem,101)
        p=json.loads(row.payload_json);p.update(images=["/images/qa/11.jpg","/images/qa/12.jpg"],banner_url="/images/qa/11.jpg")
        row.payload_json=json.dumps(p);row.hero_media_id=11
        db.add(MediaLink(media_id=11,entity_type="campaign_item",entity_id="101",role="hero",position=0))
        db.add(MediaLink(media_id=12,entity_type="campaign_item",entity_id="101",role="gallery",position=1))
        db.commit()
        before=version=row.updated_at.isoformat()
        p.update(title="Другой заголовок",description="Другой текст",price=200,category="Новая категория",params={"Material":"Металл"},images=["/images/qa/12.jpg","/images/qa/11.jpg"],banner_url="/images/qa/12.jpg")
        row.payload_json=json.dumps(p);row.hero_media_id=12
        links=db.query(MediaLink).all()
        for link in links:link.position=1-link.position;link.role="hero" if link.media_id==12 else "gallery"
        db.commit()
        revision=db.query(Storage).filter(Storage.key.like("campaign_card_revision:101:%")).order_by(Storage.id.desc()).first()
        rid=json.loads(revision.value)["revision"]
    url="/api/campaign/1/item/101/revisions/"+rid+"/restore"
    r=client.post(url,json={"preview_only":True,"side":"before"})
    assert r.status_code==200,r.text
    assert {"title","description","price","category","params","images","banner_url","media","hero_media_id"}.issubset(r.json()["changed_fields"])
    r=client.post(url,json={"preview_only":False,"confirmed":True,"side":"before","expected_updated_at":r.json()["expected_updated_at"]})
    assert r.status_code==200,r.text
    if r.json().get("persistent"):
        from app.services import campaign_editor_jobs as editor,jobs
        monkeypatch.setattr(jobs,"_campaign_job_context",lambda db,job,payload:(campaigns,db.get(Campaign,1),SimpleNamespace(id=2,email="Анна",role="employee"),["qa-a"],"qa-a"))
        validated=[]
        monkeypatch.setattr(campaigns,"_ff_validate_row",lambda row,db:validated.append(row.id) or {"valid":False,"errors":[{"message":"QA category"}]})
        with Session(engine) as db:
            job=db.get(BackgroundJob,r.json()["job_id"])
            result,state=editor.run(db,job,dict(job.payload_json))
            assert state=="completed",result
        assert validated==[101]
    with Session(engine) as db:
        row=db.get(CampaignItem,101);p=json.loads(row.payload_json)
        assert (p["title"],p["price"],p["category"],p["params"])==("Стол",100,"Мебель",{"Material":"Дерево"})
        assert p["images"]==["/images/qa/11.jpg","/images/qa/12.jpg"]
        assert row.hero_media_id==11 and row.status=="draft" and not p["verified_price"]
        assert [(l.media_id,l.position,l.role) for l in db.query(MediaLink).order_by(MediaLink.position).all()]==[(11,0,"hero"),(12,1,"gallery")]
        assert json.loads(db.get(Campaign,1).settings_json)["feed_factory"]["step"]=="validation"
        queued=db.query(BackgroundJob).all()
        if r.json().get("persistent"):
            assert len(queued)==1 and queued[0].kind=="campaign_editor"
            assert queued[0].payload_json["operation"]=="restore"
            assert queued[0].progress_done==1 and queued[0].payload_json["remaining"]==[]
        else:
            assert queued==[]

def test_owner_publication_and_automation_routes_fail_closed_for_manager(qa):
    engine,client=qa
    for path,body,method in [
        ("/api/campaign/inventory-policy?account_id=qa-a",{"enabled":True},"put"),
        ("/api/campaign/inventory-policy/run?account_id=qa-a",{"confirmed":True},"post"),
        ("/api/campaign/1/guardian/deliver",{"account_id":"qa-a"},"post"),
        ("/api/campaign/1/owner_activation",{"account_id":"qa-a","confirmed":True},"post"),
        ("/api/campaign/1/workflow/bulk-transform",{"item_ids":[101],"preview_only":False,"confirmed":True},"post"),
    ]:
        r=getattr(client,method)(path,json=body)
        assert r.status_code==403,(path,r.status_code,r.text)

def test_owner_scale_preserves_source_truth_without_executing_job(qa):
    engine,client=qa
    with Session(engine) as db:
        row=db.get(CampaignItem,101);p=json.loads(row.payload_json)
        p.update(copy_formula_id="PAS",creative_angle="Массив дерева",hypothesis="Долговечность")
        row.payload_json=json.dumps(p)
        stats=db.query(Storage).filter(
            Storage.account_id=="qa-a",
            Storage.key=="daily_stats:2026-10-03",
        ).one()
        stats.value=json.dumps({
            "date":"2026-10-03",
            "items":[
                {"id":"101","views":30,"contacts":3,"status":"active","campaign_identity":{"campaign_item_id":101}},
                {"id":"102","views":2,"contacts":1,"status":"active","campaign_identity":{"campaign_item_id":102}},
                {"id":"103","views":2,"contacts":1,"status":"active","campaign_identity":{"campaign_item_id":103}},
            ],
        })
        db.commit();v=row.updated_at.isoformat()
    r=client.post("/api/campaign/ab-tests/action",headers={"x-qa-user":"1"},json={"account_id":"qa-a","item_id":101,"action":"scale","count":50,"expected_updated_at":v})
    assert r.status_code==200,r.text
    with Session(engine) as db:
        campaign=db.get(Campaign,r.json()["campaign_id"]);ff=json.loads(campaign.settings_json)["feed_factory"]
        assert ff["defaults"]["price"]==100
        assert ff["defaults"]["params"]=={"Material":"Дерево"}
        assert ff["ab_scale"]["formula_id"]=="PAS" and ff["ab_scale"]["hypothesis"]=="Долговечность"
        assert ff["ab_scale"]["source_item_id"]==101
        assert ff["variants"]==50 and ff["references"]["text"]=="Из массива"
        assert db.query(BackgroundJob).one().status=="queued"
        assert db.query(CampaignItem).count()==4

def test_task_cursor_interleaves_real_jobs_without_duplicates(qa):
    from datetime import datetime,timezone
    engine,client=qa
    stamp=datetime(2026,10,3,12,tzinfo=timezone.utc)
    with Session(engine) as db:
        for n in range(1,111):
            db.add(BackgroundJob(id=n,kind="campaign_enhance",account_id="qa-a",status="completed",payload_json={"campaign_id":1},created_at=stamp,started_at=stamp,finished_at=stamp))
            db.add(Storage(account_id="qa-a",key=f"mass_editor_job:qa-{n}",value=json.dumps({"job_id":f"qa-{n}","created_at":stamp.isoformat(),"status":"completed","total":1,"done":1})))
        db.add(BackgroundJob(id=111,kind="campaign_enhance",account_id="qa-b",status="queued",payload_json={},created_at=stamp))
        db.commit()
    cursor="";ids=[]
    for _ in range(20):
        r=client.get("/api/campaign/tasks",params={"account_id":"qa-a","limit":17,"cursor":cursor})
        assert r.status_code==200,r.text
        data=r.json();ids.extend(x["id"] for x in data["items"])
        cursor=data["next_cursor"]
        if not cursor:break
    assert len(ids)==220 and len(set(ids))==220
    assert 111 not in ids
    first=client.get("/api/campaign/tasks",params={"account_id":"qa-a","limit":17}).json()
    assert client.get("/api/campaign/tasks",params={"account_id":"qa-a","campaign_id":1,"cursor":first["next_cursor"]}).status_code==422
    project=client.get("/api/campaign/tasks",params={"account_id":"qa-a","campaign_id":1,"limit":100}).json()
    assert len(project["items"])==100 and project["next_cursor"]
