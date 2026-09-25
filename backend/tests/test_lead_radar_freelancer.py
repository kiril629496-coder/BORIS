import json
from datetime import datetime, timezone

def test_freelancer_connector_read_only_contract_and_pagination(monkeypatch):
    from app.services.lead_radar.connectors import FreelancerProjectsConnector
    fresh=int(datetime(2026,9,6,10,tzinfo=timezone.utc).timestamp())
    old=int(datetime(2026,8,1,10,tzinfo=timezone.utc).timestamp())
    p1=[{"id":i,"title":"Need Python automation","description":"Build API integration",
         "submitdate":fresh,"seo_url":f"python/project-{i}",
         "budget":{"minimum":100,"maximum":500},"currency":{"code":"USD"},
         "jobs":[{"name":"Python"}]} for i in range(1,101)]
    p2=[{"id":101,"title":"Old task","description":"Old","submitdate":old,"seo_url":"python/old"}]
    calls=[]
    class Resp:
        def __init__(self,rows): self.rows=rows
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def read(self,*a): return json.dumps({"status":"success","result":{"projects":self.rows,"total_count":101}}).encode()
    def open_(req,timeout=0):
        from urllib.parse import urlparse,parse_qs
        qs=parse_qs(urlparse(req.full_url).query); calls.append(qs)
        return Resp(p1 if qs.get("offset",["0"])[0]=="0" else p2)
    monkeypatch.setattr("urllib.request.urlopen",open_)
    c=FreelancerProjectsConnector(["python automation"],owner_user_id=21,page_size=100)
    rows=c.fetch(datetime(2026,9,1,tzinfo=timezone.utc))
    assert len(rows)==100
    assert len(calls)==2
    assert c.supports_outreach is False
    assert all(x.source=="freelancer" and x.owner_user_id==21 for x in rows)
    assert rows[0].published_at==datetime(2026,9,6,10,tzinfo=timezone.utc)
    assert rows[0].metadata["budget"]["maximum"]==500

def test_registry_and_policy_enable_freelancer_read_only_without_oauth(monkeypatch):
    from app.services.lead_radar.registry import build_connectors
    from app.services.lead_radar.source_policy import DEFAULT_SOURCE_POLICIES
    monkeypatch.setenv("LEAD_RADAR_FREELANCER_ENABLED","1")
    monkeypatch.setenv("LEAD_RADAR_FREELANCER_QUERIES_JSON",'["python automation"]')
    monkeypatch.delenv("LEAD_RADAR_FREELANCER_OAUTH_TOKEN",raising=False)
    rows=build_connectors(15)
    c=next(x for x in rows if x.key=="freelancer")
    p=DEFAULT_SOURCE_POLICIES["freelancer"]
    assert c.group_key=="freelance"
    assert c.supports_outreach is False
    assert p.allows_automation is False
    assert p.min_interval_seconds==300

def test_runtime_preflight_recognizes_freelancer(monkeypatch):
    from lead_radar_config_preflight import run
    monkeypatch.setenv("LEAD_RADAR_OWNER_USER_ID","15")
    monkeypatch.setenv("LEAD_RADAR_FREELANCER_ENABLED","1")
    monkeypatch.setenv("LEAD_RADAR_FREELANCER_QUERIES_JSON",'["python automation"]')
    out=run(printer=lambda *a:None)
    assert out["sources"]>=1
    assert "freelance" in out["coverage"]["configured_group_keys"]


def test_freelancer_timeout_retries_once_with_longer_timeout(monkeypatch):
    from app.services.lead_radar.connectors import FreelancerProjectsConnector
    calls=[]
    class Resp:
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def read(self,*a):
            return json.dumps({'status':'success','result':{'projects':[],'total_count':0}}).encode()
    def open_(req,timeout=0):
        calls.append(timeout)
        if len(calls)==1:
            raise TimeoutError('The read operation timed out')
        return Resp()
    monkeypatch.setattr('urllib.request.urlopen',open_)
    c=FreelancerProjectsConnector(['python'],owner_user_id=21,timeout=15)
    data=c._get({'query':'python','limit':5,'offset':0})
    assert data['status']=='success'
    assert calls==[15,30]


def test_freelancer_stops_when_sorted_page_is_older_than_since(monkeypatch):
    from app.services.lead_radar.connectors import FreelancerProjectsConnector
    old=int(datetime(2026,8,1,10,tzinfo=timezone.utc).timestamp())
    rows=[{'id':i,'title':'Old','description':'Old project','submitdate':old,
           'time_updated':old,'seo_url':f'old/{i}'} for i in range(1,101)]
    calls=[]
    class Resp:
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def read(self,*a):
            return json.dumps({'status':'success','result':{'projects':rows,'total_count':50000}}).encode()
    def open_(req,timeout=0):
        calls.append(req.full_url)
        return Resp()
    monkeypatch.setattr('urllib.request.urlopen',open_)
    c=FreelancerProjectsConnector(['python'],owner_user_id=21,page_size=100)
    got=c.fetch(datetime(2026,9,1,tzinfo=timezone.utc))
    assert got==[]
    assert len(calls)==1


def test_freelancer_passes_since_to_provider_from_time(monkeypatch):
    from app.services.lead_radar.connectors import FreelancerProjectsConnector
    from urllib.parse import urlparse, parse_qs
    since=datetime(2026,9,7,12,tzinfo=timezone.utc)
    seen={}
    class Resp:
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def read(self,*a):
            return json.dumps({'status':'success','result':{'projects':[],'total_count':0}}).encode()
    def open_(req,timeout=0):
        seen.update(parse_qs(urlparse(req.full_url).query))
        return Resp()
    monkeypatch.setattr('urllib.request.urlopen',open_)
    c=FreelancerProjectsConnector(['python'],owner_user_id=21,page_size=100)
    assert c.fetch(since)==[]
    assert seen['from_time']==[str(int(since.timestamp()))]


def test_freelancer_incremental_window_uses_last_success_overlap():
    from datetime import timedelta
    from app.services.lead_radar.connectors import FreelancerProjectsConnector
    from app.services.lead_radar.cycle import _connector_since
    c=FreelancerProjectsConnector(['python'],owner_user_id=21)
    now=datetime(2026,9,7,21,tzinfo=timezone.utc)
    global_since=now-timedelta(days=7)
    last_success=now-timedelta(minutes=5)
    assert _connector_since(c,global_since,last_success,now)==last_success-timedelta(hours=6)
    assert _connector_since(c,global_since,None,now)==now-timedelta(hours=24)
