from __future__ import annotations
import base64, csv, datetime as dt, hashlib, hmac, io, json, os, secrets, urllib.error, urllib.parse, urllib.request
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session
from app.director.models import DirectConnection, DirectEntity, DirectStat, DirectKpiConfig, DirectAction, DirectQueryStat, DirectSliceStat
from app.services.exception_observability import observe_suppressed

DIRECT_API = "https://api.direct.yandex.com/json/v501"
OAUTH_AUTHORIZE = "https://oauth.yandex.ru/authorize"
OAUTH_TOKEN = "https://oauth.yandex.ru/token"

class DirectError(RuntimeError):
    def __init__(self, code: str, message: str, retryable: bool = False, http_status: int | None = None):
        super().__init__(message); self.code=code; self.message=message; self.retryable=retryable; self.http_status=http_status

def _env(name: str, default: str="") -> str: return (os.getenv(name) or default).strip()
def app_configured() -> bool: return bool(_env("YANDEX_DIRECT_CLIENT_ID") and _env("YANDEX_DIRECT_CLIENT_SECRET"))
def redirect_uri() -> str: return _env("YANDEX_DIRECT_REDIRECT_URI", _env("PUBLIC_BASE_URL", "https://boris-ai.pro").rstrip("/") + "/api/direct/oauth/callback")
def _signing_key() -> bytes: return (_env("JWT_SECRET") or _env("SECRET_KEY") or "boris-direct-state").encode()

def _fernet() -> Fernet:
    raw = _env("FERNET_KEY")
    if raw:
        try: return Fernet(raw.encode())
        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=24)
    digest = hashlib.sha256(_signing_key() + b":direct-token").digest()
    return Fernet(base64.urlsafe_b64encode(digest))

def encrypt_token(token: str) -> str: return _fernet().encrypt(token.encode()).decode()
def decrypt_token(token: str) -> str:
    try: return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as e: raise DirectError("TOKEN_DECRYPT_FAILED", "Не удалось расшифровать OAuth-токен") from e

def make_state(account_id: str, nonce: str) -> str:
    payload = json.dumps({"a":account_id,"n":nonce,"t":int(dt.datetime.utcnow().timestamp())}, separators=(",",":")).encode()
    body = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    sig = hmac.new(_signing_key(), body.encode(), hashlib.sha256).hexdigest()
    return body + "." + sig

def parse_state(state: str) -> dict:
    try:
        body, sig = state.rsplit(".",1)
        expected = hmac.new(_signing_key(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected): raise ValueError("signature")
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body)%4))
        data = json.loads(raw)
        if int(dt.datetime.utcnow().timestamp()) - int(data["t"]) > 900: raise ValueError("expired")
        return data
    except Exception as e: raise DirectError("OAUTH_STATE_INVALID", "OAuth state недействителен или устарел") from e

def _http_json(url: str, *, data: dict | None=None, headers: dict | None=None, form: dict | None=None, timeout: int=35) -> tuple[int, dict, dict]:
    body = None
    hdr = dict(headers or {})
    if form is not None:
        body = urllib.parse.urlencode(form).encode(); hdr.setdefault("Content-Type","application/x-www-form-urlencoded")
    elif data is not None:
        body = json.dumps(data, ensure_ascii=False).encode(); hdr.setdefault("Content-Type","application/json; charset=utf-8")
    req = urllib.request.Request(url, data=body, headers=hdr, method="POST" if body is not None else "GET")
    try:
        from app.services.reliability import dependency_call
        def _perform():
            with urllib.request.urlopen(req, timeout=timeout) as r:
                text = r.read().decode("utf-8", "replace")
                return r.status, json.loads(text) if text else {}, dict(r.headers.items())
        return dependency_call("yandex.direct", _perform, threshold=5, cooldown_seconds=120)
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")
        try: obj=json.loads(text)
        except Exception: obj={"message":text[:500]}
        raise DirectError(str(obj.get("error") or obj.get("error_code") or "HTTP_ERROR"), str(obj.get("error_description") or obj.get("message") or text[:500]), e.code in (429,500,502,503,504), e.code)
    except urllib.error.URLError as e: raise DirectError("NETWORK_ERROR", str(e.reason), True) from e

def exchange_code(code: str) -> str:
    if not app_configured(): raise DirectError("APP_NOT_CONFIGURED", "Не настроено OAuth-приложение Яндекс Директа")
    _, obj, _ = _http_json(OAUTH_TOKEN, form={"grant_type":"authorization_code","code":code,"client_id":_env("YANDEX_DIRECT_CLIENT_ID"),"client_secret":_env("YANDEX_DIRECT_CLIENT_SECRET")})
    token = obj.get("access_token")
    if not token: raise DirectError("TOKEN_MISSING", "Яндекс не вернул access_token")
    return str(token)

def authorization_url(state: str) -> str:
    # For Yandex OAuth apps of type "API access / debugging" redirect_uri is fixed by Yandex.
    # The user receives a verification code and pastes it back into BORIS.
    q={"response_type":"code","client_id":_env("YANDEX_DIRECT_CLIENT_ID"),"redirect_uri":"https://oauth.yandex.ru/verification_code","state":state,"force_confirm":"yes"}
    return OAUTH_AUTHORIZE + "?" + urllib.parse.urlencode(q)

class Gateway:
    def __init__(self, conn: DirectConnection):
        if not conn.token_encrypted: raise DirectError("NOT_CONNECTED", "Яндекс Директ не подключён")
        self.token=decrypt_token(conn.token_encrypted); self.client_login=(conn.client_login or "").strip()
    def headers(self) -> dict:
        h={"Authorization":"Bearer "+self.token,"Accept-Language":"ru","Content-Type":"application/json; charset=utf-8"}
        if self.client_login: h["Client-Login"]=self.client_login
        return h
    def call(self, service: str, method: str, params: dict) -> dict:
        _, obj, headers = _http_json(f"{DIRECT_API}/{service}", data={"method":method,"params":params}, headers=self.headers())
        if obj.get("error"):
            err=obj["error"]; raise DirectError(str(err.get("error_code") or "DIRECT_ERROR"), str(err.get("error_detail") or err.get("error_string") or "Ошибка API Директа"))
        obj["_request_id"] = headers.get("RequestId") or headers.get("Request-ID")
        return obj
    def report(self, definition: dict) -> tuple[str, dict]:
        headers=self.headers(); headers.update({"processingMode":"auto","returnMoneyInMicros":"false","skipReportHeader":"true","skipReportSummary":"true"})
        req=urllib.request.Request(f"{DIRECT_API}/reports", data=json.dumps({"params":definition}, ensure_ascii=False).encode(), headers=headers, method="POST")
        try:
            from app.services.reliability import dependency_call
            def _perform_report():
                with urllib.request.urlopen(req, timeout=60) as r:
                    return r.read().decode("utf-8","replace"), dict(r.headers.items())
            return dependency_call("yandex.direct", _perform_report, threshold=5, cooldown_seconds=120)
        except urllib.error.HTTPError as e:
            text=e.read().decode("utf-8","replace")
            if e.code in (201,202): raise DirectError("REPORT_PENDING", "Отчёт Яндекс Директа ещё формируется", True, e.code)
            try: obj=json.loads(text); msg=obj.get("error",{}).get("error_detail") or obj.get("message") or text[:500]
            except Exception: msg=text[:500]
            raise DirectError("REPORT_HTTP_ERROR", str(msg), e.code in (429,500,502,503,504), e.code)

def get_connection(db: Session, account_id: str) -> DirectConnection | None:
    return db.query(DirectConnection).filter(DirectConnection.account_id==account_id).first()
def ensure_connection(db: Session, account_id: str) -> DirectConnection:
    row=get_connection(db, account_id)
    if row is None: row=DirectConnection(account_id=account_id); db.add(row); db.flush()
    return row

def _dump(x): return json.dumps(x, ensure_ascii=False, separators=(",",":"), default=str)
def _loads(x, default=None):
    try: return json.loads(x or "")
    except Exception: return {} if default is None else default

def sync_entities(db: Session, account_id: str) -> dict:
    conn=get_connection(db,account_id)
    if not conn or conn.status!="CONNECTED": raise DirectError("NOT_CONNECTED","Яндекс Директ не подключён")
    gw=Gateway(conn); summary={}
    specs=[
      ("campaign","campaigns", {"SelectionCriteria":{},"FieldNames":["Id","Name","State","Status","Type","StartDate","DailyBudget","NegativeKeywords","TimeTargeting"],"TextCampaignFieldNames":["BiddingStrategy"],"UnifiedCampaignFieldNames":["BiddingStrategy"]}),
      ("adgroup","adgroups", {"SelectionCriteria":{},"FieldNames":["Id","Name","CampaignId","Status","RegionIds","NegativeKeywords"]}),
      ("ad","ads", {"SelectionCriteria":{},"FieldNames":["Id","AdGroupId","CampaignId","Status","State"]}),
      ("keyword","keywords", {"SelectionCriteria":{},"FieldNames":["Id","Keyword","AdGroupId","CampaignId","State","Status","ServingStatus","Bid","ContextBid","StrategyPriority","AutotargetingSettings"]}),
    ]
    now=dt.datetime.utcnow()
    for etype, service, params in specs:
        obj=gw.call(service,"get",params); key={"campaign":"Campaigns","adgroup":"AdGroups","ad":"Ads","keyword":"Keywords"}[etype]
        items=(obj.get("result") or {}).get(key) or []; summary[etype]=len(items)
        for item in items:
            eid=str(item.get("Id")); row=db.query(DirectEntity).filter_by(account_id=account_id,entity_type=etype,external_id=eid).first()
            if row is None: row=DirectEntity(account_id=account_id,entity_type=etype,external_id=eid); db.add(row)
            row.name=str(item.get("Name") or item.get("Keyword") or eid); row.status=str(item.get("Status") or item.get("State") or "")
            row.parent_external_id=str(item.get("CampaignId") or item.get("AdGroupId") or "") or None; row.state_json=_dump(item); row.synced_at=now
    # Rich creative/moderation snapshot. Kept isolated so unsupported ad subtypes never break base sync.
    try:
        obj=gw.call("ads","get",{
            "SelectionCriteria":{},
            "FieldNames":["Id","AdGroupId","CampaignId","Status","State","StatusClarification","Type","Subtype","AgeLabel"],
            "TextAdFieldNames":["Title","Title2","Text","Href","DisplayUrlPath","VCardId","AdImageHash","SitelinkSetId","DisplayUrlPathModeration","VCardModeration","SitelinksModeration","AdImageModeration","AdExtensions"],
            "ResponsiveAdFieldNames":["Titles","Texts","Href","DisplayUrlPath","AdImages","SitelinkSetId","DisplayUrlPathModeration","SitelinksModeration","AdExtensions"]
        })
        rich=(obj.get("result") or {}).get("Ads") or []; summary["creative_details"]=len(rich)
        for item in rich:
            eid=str(item.get("Id")); row=db.query(DirectEntity).filter_by(account_id=account_id,entity_type="ad",external_id=eid).first()
            if row:
                row.status=str(item.get("Status") or item.get("State") or ""); row.state_json=_dump(item); row.synced_at=now
    except DirectError as e:
        summary["creative_details"]={"status":"UNAVAILABLE","code":e.code}
    conn.last_success_at=now; conn.last_error_code=None; conn.last_error_message=None; db.commit(); return summary

def sync_report(db: Session, account_id: str, days: int=30) -> dict:
    conn=get_connection(db,account_id)
    if not conn or conn.status!="CONNECTED": raise DirectError("NOT_CONNECTED","Яндекс Директ не подключён")
    gw=Gateway(conn); end=dt.date.today(); start=end-dt.timedelta(days=max(1,min(days,90))-1)
    definition={"SelectionCriteria":{},"FieldNames":["Date","CampaignId","Impressions","Clicks","Cost","Conversions","Revenue"],"OrderBy":[{"Field":"Date"}],"ReportName":f"BORIS_{account_id}_{start}_{end}","ReportType":"CAMPAIGN_PERFORMANCE_REPORT","DateRangeType":"CUSTOM_DATE","DateFrom":str(start),"DateTo":str(end),"Format":"TSV","IncludeVAT":"YES","IncludeDiscount":"YES"}
    text,_=gw.report(definition); reader=csv.DictReader(io.StringIO(text), delimiter="\t"); n=0
    for r in reader:
        day=r.get("Date"); cid=str(r.get("CampaignId") or "")
        if not day: continue
        row=db.query(DirectStat).filter_by(account_id=account_id,day=day,campaign_id=cid).first()
        if row is None: row=DirectStat(account_id=account_id,day=day,campaign_id=cid); db.add(row)
        def num(k):
            v=(r.get(k) or "").strip(); return None if v in ("","--") else float(v.replace(",","."))
        row.impressions=int(num("Impressions") or 0); row.clicks=int(num("Clicks") or 0); row.cost=num("Cost"); row.conversions=num("Conversions"); row.revenue=num("Revenue"); row.raw_json=_dump(r); n+=1
    conn.last_success_at=dt.datetime.utcnow(); db.commit(); return {"rows":n,"date_from":str(start),"date_to":str(end)}


def sync_search_queries(db: Session, account_id: str, days: int=14) -> dict:
    conn=get_connection(db,account_id)
    if not conn or conn.status!="CONNECTED": raise DirectError("NOT_CONNECTED","Яндекс Директ не подключён")
    gw=Gateway(conn); end=dt.date.today(); start=end-dt.timedelta(days=max(1,min(days,30))-1)
    definition={"SelectionCriteria":{},"FieldNames":["Date","CampaignId","AdGroupId","Query","CriterionId","CriterionType","Impressions","Clicks","Cost","Conversions"],"OrderBy":[{"Field":"Date"}],"ReportName":f"BORIS_QUERY_{account_id}_{start}_{end}","ReportType":"SEARCH_QUERY_PERFORMANCE_REPORT","DateRangeType":"CUSTOM_DATE","DateFrom":str(start),"DateTo":str(end),"Format":"TSV","IncludeVAT":"YES","IncludeDiscount":"YES","AttributionModels":["AUTO"]}
    text,_=gw.report(definition); reader=csv.DictReader(io.StringIO(text), delimiter="\t"); n=0
    for r in reader:
        day=r.get("Date"); query=(r.get("Query") or "").strip(); cid=str(r.get("CampaignId") or ""); gid=str(r.get("AdGroupId") or ""); crid=str(r.get("CriterionId") or "")
        if not day or not query: continue
        row=db.query(DirectQueryStat).filter_by(account_id=account_id,day=day,campaign_id=cid,ad_group_id=gid,query=query,criterion_id=crid).first()
        if row is None: row=DirectQueryStat(account_id=account_id,day=day,campaign_id=cid,ad_group_id=gid,query=query,criterion_id=crid); db.add(row)
        def num(k):
            v=(r.get(k) or "").strip(); return None if v in ("","--") else float(v.replace(",","."))
        row.criterion_type=r.get("CriterionType") or None; row.impressions=int(num("Impressions") or 0); row.clicks=int(num("Clicks") or 0); row.cost=num("Cost"); row.conversions=num("Conversions"); row.raw_json=_dump(r); n+=1
    db.commit(); return {"rows":n,"date_from":str(start),"date_to":str(end)}


def sync_marketing_controls(db: Session, account_id: str) -> dict:
    """Read high-value control surfaces: bid adjustments and retargeting/audiences."""
    conn=get_connection(db,account_id)
    if not conn or conn.status!="CONNECTED": raise DirectError("NOT_CONNECTED","Яндекс Директ не подключён")
    gw=Gateway(conn); now=dt.datetime.utcnow(); summary={"bid_modifier":0,"retargeting_list":0}

    # BidModifiers.get requires an entity scope and Levels. CampaignIds are limited
    # to 10 per request, so walk the already-synced authoritative campaign IDs.
    campaign_ids=[]
    for e in db.query(DirectEntity).filter_by(account_id=account_id,entity_type="campaign").all():
        try: campaign_ids.append(int(e.external_id))
        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=208)
    modifier_items=[]
    for i in range(0,len(campaign_ids),10):
        ids=campaign_ids[i:i+10]
        if not ids: continue
        params={
          "SelectionCriteria":{"CampaignIds":ids,"Levels":["CAMPAIGN","AD_GROUP"]},
          "FieldNames":["Id","CampaignId","AdGroupId","Level","Type"],
          "MobileAdjustmentFieldNames":["BidModifier","OperatingSystemType"],
          "TabletAdjustmentFieldNames":["BidModifier","OperatingSystemType"],
          "DesktopAdjustmentFieldNames":["BidModifier"],
          "DesktopOnlyAdjustmentFieldNames":["BidModifier"],
          "DemographicsAdjustmentFieldNames":["Gender","Age","BidModifier","Enabled"],
          "RetargetingAdjustmentFieldNames":["RetargetingConditionId","BidModifier","Accessible","Enabled"],
          "RegionalAdjustmentFieldNames":["RegionId","BidModifier","Enabled"],
          "VideoAdjustmentFieldNames":["BidModifier"],
          "SmartAdAdjustmentFieldNames":["BidModifier"],
          "SerpLayoutAdjustmentFieldNames":["SerpLayout","BidModifier","Enabled"],
          "IncomeGradeAdjustmentFieldNames":["Grade","BidModifier","Enabled"],
          "AdGroupAdjustmentFieldNames":["BidModifier"],
        }
        obj=gw.call("bidmodifiers","get",params); modifier_items.extend((obj.get("result") or {}).get("BidModifiers") or [])
    summary["bid_modifier"]=len(modifier_items)
    for item in modifier_items:
        eid=str(item.get("Id")); row=db.query(DirectEntity).filter_by(account_id=account_id,entity_type="bid_modifier",external_id=eid).first()
        if row is None: row=DirectEntity(account_id=account_id,entity_type="bid_modifier",external_id=eid); db.add(row)
        row.name=str(item.get("Type") or eid); row.status="ACTIVE"
        row.parent_external_id=str(item.get("CampaignId") or item.get("AdGroupId") or "") or None; row.state_json=_dump(item); row.synced_at=now

    # Portfolio strategies: read the complete optimization contract, including goals,
    # weekly spend limits, bid ceilings and attribution model.
    try:
        sp={"SelectionCriteria":{},"FieldNames":["Id","AttributionModel","CounterIds","PriorityGoals","Type","Name","StatusArchived"],
            "StrategyMaximumClicksFieldNames":["WeeklySpendLimit","WeeklyBudgetRollover","CustomPeriodBudget","BudgetType","BidCeiling"],
            "StrategyMaximumConversionRateFieldNames":["WeeklySpendLimit","WeeklyBudgetRollover","CustomPeriodBudget","BudgetType","BidCeiling","GoalId"],
            "StrategyAverageCpcFieldNames":["AverageCpc","WeeklySpendLimit","WeeklyBudgetRollover","CustomPeriodBudget","BudgetType"],
            "StrategyAverageCpaFieldNames":["AverageCpa","GoalId","WeeklySpendLimit","WeeklyBudgetRollover","CustomPeriodBudget","BudgetType","BidCeiling","ExplorationBudget"],
            "StrategyAverageCpaMultipleGoalsFieldNames":["WeeklySpendLimit","WeeklyBudgetRollover","CustomPeriodBudget","BudgetType","ExplorationBudget","BidCeiling"],
            "StrategyMaxProfitFieldNames":["WeeklySpendLimit","WeeklyBudgetRollover","CustomPeriodBudget","BudgetType","ExplorationBudget"],
            "StrategyPayForConversionFieldNames":["Cpa","GoalId","WeeklySpendLimit","CustomPeriodBudget","BudgetType"],
            "StrategyPayForConversionMultipleGoalsFieldNames":["GoalId","WeeklySpendLimit","CustomPeriodBudget"],
            "StrategyAverageCrrFieldNames":["Crr","GoalId","WeeklySpendLimit","WeeklyBudgetRollover","CustomPeriodBudget","BudgetType","ExplorationBudget"],
            "StrategyPayForConversionCrrFieldNames":["Crr","GoalId","WeeklySpendLimit","CustomPeriodBudget","BudgetType"]}
        obj=gw.call("strategies","get",sp); items=(obj.get("result") or {}).get("Strategies") or []; summary["strategy"]=len(items)
        for item in items:
            eid=str(item.get("Id")); row=db.query(DirectEntity).filter_by(account_id=account_id,entity_type="strategy",external_id=eid).first()
            if row is None: row=DirectEntity(account_id=account_id,entity_type="strategy",external_id=eid); db.add(row)
            row.name=str(item.get("Name") or item.get("Type") or eid); row.status=str(item.get("StatusArchived") or ""); row.parent_external_id=None; row.state_json=_dump(item); row.synced_at=now
    except DirectError as e: summary["strategy"]={"status":"UNAVAILABLE","code":e.code}

    # Shared negative keyword library: a senior audit must see global exclusions too.
    try:
        obj=gw.call("negativekeywordsharedsets","get",{"SelectionCriteria":{},"FieldNames":["Id","Name","NegativeKeywords","Associated"]})
        items=(obj.get("result") or {}).get("NegativeKeywordSharedSets") or []; summary["negative_shared_set"]=len(items)
        for item in items:
            eid=str(item.get("Id")); row=db.query(DirectEntity).filter_by(account_id=account_id,entity_type="negative_shared_set",external_id=eid).first()
            if row is None: row=DirectEntity(account_id=account_id,entity_type="negative_shared_set",external_id=eid); db.add(row)
            row.name=str(item.get("Name") or eid); row.status=str(item.get("Associated") or ""); row.parent_external_id=None; row.state_json=_dump(item); row.synced_at=now
    except DirectError as e: summary["negative_shared_set"]={"status":"UNAVAILABLE","code":e.code}

    # Audience targets are scoped by campaigns; up to 100 campaign IDs per call.
    audience=[]
    for i in range(0,len(campaign_ids),100):
        ids=campaign_ids[i:i+100]
        if not ids: continue
        try:
            obj=gw.call("audiencetargets","get",{"SelectionCriteria":{"CampaignIds":ids},"FieldNames":["Id","AdGroupId","CampaignId","RetargetingListId","InterestId","ContextBid","StrategyPriority","State"]})
            audience.extend((obj.get("result") or {}).get("AudienceTargets") or [])
        except DirectError as e:
            summary["audience_target"]={"status":"UNAVAILABLE","code":e.code}; audience=[]; break
    if "audience_target" not in summary: summary["audience_target"]=len(audience)
    for item in audience:
        eid=str(item.get("Id")); row=db.query(DirectEntity).filter_by(account_id=account_id,entity_type="audience_target",external_id=eid).first()
        if row is None: row=DirectEntity(account_id=account_id,entity_type="audience_target",external_id=eid); db.add(row)
        row.name=f"Audience {eid}"; row.status=str(item.get("State") or ""); row.parent_external_id=str(item.get("CampaignId") or item.get("AdGroupId") or "") or None; row.state_json=_dump(item); row.synced_at=now

    try:
        obj=gw.call("retargetinglists","get",{"SelectionCriteria":{},"FieldNames":["Type","Id","Name","Description","Rules","IsAvailable","Scope","AvailableForTargetsInAdGroupTypes"]})
        retargeting=(obj.get("result") or {}).get("RetargetingLists") or []; summary["retargeting_list"]=len(retargeting)
        for item in retargeting:
            eid=str(item.get("Id")); row=db.query(DirectEntity).filter_by(account_id=account_id,entity_type="retargeting_list",external_id=eid).first()
            if row is None: row=DirectEntity(account_id=account_id,entity_type="retargeting_list",external_id=eid); db.add(row)
            row.name=str(item.get("Name") or eid); row.status=str(item.get("IsAvailable") or "")
            row.parent_external_id=None; row.state_json=_dump(item); row.synced_at=now
    except DirectError as e:
        summary["retargeting_list"]={"status":"UNAVAILABLE","code":e.code}
    db.commit(); return summary

def sync_dimension_report(db: Session, account_id: str, days: int=30) -> dict:
    """Economics by independent Yandex report dimensions without cross-segment overwrite."""
    conn=get_connection(db,account_id)
    if not conn or conn.status!="CONNECTED": raise DirectError("NOT_CONNECTED","Яндекс Директ не подключён")
    gw=Gateway(conn); end=dt.date.today(); start=end-dt.timedelta(days=max(1,min(days,60))-1)
    dimensions={
        "network":["AdNetworkType"],
        "device":["Device"],
        "geo":["LocationOfPresenceId","LocationOfPresenceName"],
        "age":["Age"],
        "gender":["Gender"],
        "ad_format":["AdFormat"],
        "mobile_platform":["MobilePlatform"],
        "ad":["AdId"],
    }
    total_rows=0; result={}
    for dtype,df in dimensions.items():
        fields=["Date","CampaignId",*df,"Impressions","Clicks","Cost","Conversions","Revenue"]
        definition={"SelectionCriteria":{},"FieldNames":fields,"OrderBy":[{"Field":"Date"}],"ReportName":f"BORIS_{dtype.upper()}_{account_id}_{start}_{end}","ReportType":"CUSTOM_REPORT","DateRangeType":"CUSTOM_DATE","DateFrom":str(start),"DateTo":str(end),"Format":"TSV","IncludeVAT":"YES","IncludeDiscount":"YES","AttributionModels":["AUTO"]}
        try:
            text,_=gw.report(definition)
        except DirectError as e:
            if e.code=="REPORT_PENDING": result[dtype]={"status":"PROCESSING"}; continue
            result[dtype]={"status":"UNAVAILABLE","code":e.code}; continue
        reader=csv.DictReader(io.StringIO(text), delimiter="\t"); n=0
        for r in reader:
            day=r.get("Date"); cid=str(r.get("CampaignId") or "")
            if not day: continue
            if dtype=="geo": dvalue=f"{r.get('LocationOfPresenceId') or ''}:{r.get('LocationOfPresenceName') or 'UNKNOWN'}"
            else: dvalue=str(r.get(df[0]) or "UNKNOWN")
            row=db.query(DirectSliceStat).filter_by(account_id=account_id,day=day,campaign_id=cid,dimension_type=dtype,dimension_value=dvalue).first()
            if row is None: row=DirectSliceStat(account_id=account_id,day=day,campaign_id=cid,dimension_type=dtype,dimension_value=dvalue); db.add(row)
            def num(k):
                v=(r.get(k) or "").strip(); return None if v in ("","--") else float(v.replace(",","."))
            row.impressions=int(num("Impressions") or 0); row.clicks=int(num("Clicks") or 0); row.cost=num("Cost"); row.conversions=num("Conversions"); row.revenue=num("Revenue"); row.raw_json=_dump(r); n+=1; total_rows+=1
        result[dtype]={"status":"OK","rows":n}
    db.commit(); return {"rows":total_rows,"date_from":str(start),"date_to":str(end),"dimensions":result}

def advertising_capabilities(db: Session, account_id: str) -> dict:
    counts={}
    for et in ("campaign","adgroup","ad","keyword","bid_modifier","retargeting_list","strategy","negative_shared_set","audience_target"):
        counts[et]=db.query(DirectEntity).filter_by(account_id=account_id,entity_type=et).count()
    slices={k:db.query(DirectSliceStat).filter_by(account_id=account_id,dimension_type=k).count() for k in ("network","device","geo","age","gender","ad_format","mobile_platform","ad")}
    return {"status":"ok","coverage":{
        "campaigns":{"read":True,"safe_write":True,"objects":counts["campaign"]},
        "adgroups":{"read":True,"safe_write":False,"objects":counts["adgroup"]},
        "ads":{"read":True,"moderation_surface":True,"creative_write":"PLANNED","objects":counts["ad"]},
        "keywords":{"read":True,"safe_write":True,"objects":counts["keyword"]},
        "search_queries":{"read":True},
        "bid_modifiers":{"read":True,"write":"GUARDED_PLANNED","objects":counts["bid_modifier"]},
        "retargeting":{"read":True,"write":"GUARDED_PLANNED","objects":counts["retargeting_list"]},
        "audience_targets":{"read":True,"write":"GUARDED_PLANNED","objects":counts["audience_target"]},
        "shared_negative_sets":{"read":True,"write":"GUARDED_PLANNED","objects":counts["negative_shared_set"]},
        "portfolio_strategies":{"read":True,"write":"HIGH_RISK_CONFIRM_ONLY","objects":counts["strategy"]},
        "search_vs_network":{"read":True,"rows":slices["network"]},
        "devices":{"read":True,"rows":slices["device"]},
        "geo":{"read":True,"rows":slices["geo"]},
        "age":{"read":True,"rows":slices["age"]},
        "gender":{"read":True,"rows":slices["gender"]},
        "ad_format":{"read":True,"rows":slices["ad_format"]},
        "mobile_platform":{"read":True,"rows":slices["mobile_platform"]},
        "crm_profit_feedback":{"read":True},
        "negative_keyword_intelligence":{"read":True,"auto_write":False,"confidence_guard":True},
        "strategy_intelligence":{"read":True,"write":"HIGH_RISK_CONFIRM_ONLY"},
        "creative_intelligence":{"read":True,"moderation_reasons":True,"write":"PLANNED"},
        "creative_fatigue":{"read":True,"ad_level_rows":slices["ad"],"write":False},
        "bid_modifier_optimizer":{"planner":True,"write":False,"max_step_percent":15},
        "budget_allocator":{"planner":True,"increase_total_budget":False},
        "forecast":{"month":True,"intraday_requires_yandex_timezone":True},
        "landing_health":{"read":True,"auto_suspend":False},
        "experiments":{"planner":True,"one_variable_only":True},
        "rollback":{"campaign_suspend_resume":True,"keyword_suspend_resume":True,"verify_after_write":True},
        "concurrency":{"account_lock":True},
        "autopilot":{"low_risk_only":True},
    }}

def dashboard(db: Session, account_id: str) -> dict:
    conn=get_connection(db,account_id); cfg=db.query(DirectKpiConfig).filter_by(account_id=account_id).first()
    today=str(dt.date.today()); stats=db.query(DirectStat).filter(DirectStat.account_id==account_id, DirectStat.day==today).all()
    total=lambda attr: sum(float(getattr(x,attr) or 0) for x in stats)
    cost=total("cost"); conv=total("conversions"); rev=total("revenue"); clicks=total("clicks"); impr=total("impressions")
    cpl=(cost/conv) if conv>0 else None; ctr=(clicks/impr*100) if impr>0 else None
    target=float(cfg.target_leads_daily or 0) if cfg else 0; max_cpl=float(cfg.max_cpl or cfg.target_cpl or 0) if cfg else 0
    state="NO_DATA"
    if stats:
        state="GOOD"
        if target>0 and conv < target*0.5: state="ATTENTION"
        if max_cpl>0 and cpl is not None and cpl>max_cpl: state="CRITICAL" if cpl>max_cpl*1.5 else "ATTENTION"
    actions=db.query(DirectAction).filter_by(account_id=account_id).order_by(DirectAction.id.desc()).limit(20).all()
    has_data=bool(stats)
    today={"impressions":int(impr),"clicks":int(clicks),"cost":round(cost,2),"conversions":round(conv,2),"revenue":round(rev,2),"ctr":round(ctr,2) if ctr is not None else None,"cpl":round(cpl,2) if cpl is not None else None,"data_state":"OK"} if has_data else {"impressions":None,"clicks":None,"cost":None,"conversions":None,"revenue":None,"ctr":None,"cpl":None,"data_state":"NO_DATA"}
    return {"status":"ok","connection":{"state":conn.status if conn else "NOT_CONNECTED","app_configured":app_configured(),"client_login":conn.client_login if conn else None,"last_success_at":conn.last_success_at.isoformat() if conn and conn.last_success_at else None,"last_error_code":conn.last_error_code if conn else None,"last_error_message":conn.last_error_message if conn else None},"kpi":{"target_leads_daily":cfg.target_leads_daily if cfg else None,"target_cpl":cfg.target_cpl if cfg else None,"max_cpl":cfg.max_cpl if cfg else None,"monthly_budget":cfg.monthly_budget if cfg else None,"daily_hard_limit":cfg.daily_hard_limit if cfg else None,"autonomy_mode":cfg.autonomy_mode if cfg else "ADVISOR","ai_writes_enabled":bool(cfg.ai_writes_enabled) if cfg else False},"today":today,"health":state,"actions":[{"id":a.id,"type":a.action_type,"status":a.status,"risk":a.risk_level,"reason":a.reason,"entity_id":a.entity_id,"created_at":a.created_at.isoformat() if a.created_at else None} for a in actions]}

def audit_and_plan(db: Session, account_id: str) -> dict:
    view=dashboard(db,account_id); t=view["today"]; cfg=view["kpi"]; proposals=[]
    if t["data_state"]!="OK": return {"status":"waiting_data","proposals":[],"message":"Недостаточно статистики для безопасных действий"}
    target=float(cfg.get("target_leads_daily") or 0); max_cpl=float(cfg.get("max_cpl") or cfg.get("target_cpl") or 0); conv=float(t.get("conversions") or 0); cpl=t.get("cpl")
    if target>0 and conv < target*0.5:
        proposals.append({"action":"INVESTIGATE_UNDERDELIVERY","risk":"LOW","confidence":0.92,"reason":f"Факт {conv:g} при дневной цели {target:g}; требуется анализ структуры трафика до изменения бюджета"})
    if max_cpl>0 and cpl is not None and cpl>max_cpl:
        proposals.append({"action":"INVESTIGATE_HIGH_CPL","risk":"LOW","confidence":0.95,"reason":f"CPL {cpl:.0f} ₽ выше допустимых {max_cpl:.0f} ₽; сначала локализовать слабые кампании/запросы"})
    # Marketing/business intelligence can veto premature Direct changes. This reuses
    # BORIS CRM feedback and Yandex learning rules instead of reacting to today's CPL alone.
    try:
        from app.director.intelligence import business_diagnose
        intel=business_diagnose(db, account_id)
        blocking={s.get("code") for s in (intel.get("signals") or [])}
        if "TRAFFIC_WITHOUT_CONVERSIONS" in blocking:
            proposals.append({"action":"CHECK_TRACKING_AND_LANDING","risk":"LOW","confidence":0.97,"reason":"Есть клики без конверсий: до изменения трафика проверить Метрику, формы, телефонию и посадочную."})
        if "SALES_CONVERSION_LOW" in blocking:
            proposals.append({"action":"HANDOFF_TO_MOP_ROP","risk":"LOW","confidence":0.9,"reason":"Квалифицированные лиды есть, но продажи проседают: не душить Директ, передать проблему в контур продаж."})
        if "CRM_ATTRIBUTION_MISSING" in blocking:
            proposals.append({"action":"FIX_CRM_ATTRIBUTION","risk":"LOW","confidence":0.99,"reason":"Нет доказанной связи Direct → CRM → продажа; оптимизация по прибыли пока заблокирована."})
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=409)
    if not proposals: proposals.append({"action":"KEEP_OBSERVING","risk":"LOW","confidence":0.9,"reason":"Критических отклонений по доступным данным не обнаружено"})
    for p in proposals:
        key=hashlib.sha256(f"{account_id}:{dt.date.today()}:{p['action']}".encode()).hexdigest()
        if not db.query(DirectAction).filter_by(idempotency_key=key).first():
            db.add(DirectAction(account_id=account_id,idempotency_key=key,entity_type="account",action_type=p["action"],risk_level=p["risk"],confidence=p["confidence"],reason=p["reason"],evidence_json=_dump(t),status="PLANNED"))
    db.commit(); return {"status":"ok","proposals":proposals}

def execute_action(db: Session, account_id: str, action_id: int, confirmed: bool=False) -> dict:
    action=db.query(DirectAction).filter_by(id=action_id,account_id=account_id).first()
    if not action: raise DirectError("ACTION_NOT_FOUND","Действие не найдено")
    cfg=db.query(DirectKpiConfig).filter_by(account_id=account_id).first(); mode=(cfg.autonomy_mode if cfg else "ADVISOR"); writes=bool(cfg.ai_writes_enabled) if cfg else False
    if action.action_type in ("INVESTIGATE_UNDERDELIVERY","INVESTIGATE_HIGH_CPL","KEEP_OBSERVING","CHECK_TRACKING_AND_LANDING","HANDOFF_TO_MOP_ROP","FIX_CRM_ATTRIBUTION","BID_MODIFIER_RECOMMENDATION","BUDGET_REALLOCATION_RECOMMENDATION","CREATIVE_FATIGUE_TEST","NEGATIVE_KEYWORD_CANDIDATE","RECOVERY_SCALE_RECOMMENDATION"):
        action.status="VERIFIED"; action.verified_at=dt.datetime.utcnow(); db.commit(); return {"status":"verified","message":"Аналитическое действие не требует изменения Яндекс Директа"}
    if not writes: raise DirectError("WRITES_DISABLED","Автоматические изменения выключены")
    if mode not in {"ADVISOR","CONFIRM","AUTOPILOT"}: raise DirectError("INVALID_AUTONOMY_MODE","Неизвестный режим автономности; изменение заблокировано")
    if mode in {"ADVISOR","CONFIRM"} and not confirmed: raise DirectError("CONFIRMATION_REQUIRED","Этот режим требует подтверждения перед изменением Яндекс Директа")
    if mode=="AUTOPILOT" and action.risk_level!="LOW" and not confirmed: raise DirectError("CONFIRMATION_REQUIRED","Автопилот выполняет без подтверждения только действия низкого риска")
    allowed={"SUSPEND_CAMPAIGN":("campaigns","suspend","SUSPENDED"),"RESUME_CAMPAIGN":("campaigns","resume","ON"),"SUSPEND_KEYWORD":("keywords","suspend","SUSPENDED"),"RESUME_KEYWORD":("keywords","resume","ON")}
    if action.action_type not in allowed: raise DirectError("ACTION_NOT_ALLOWED","Тип действия не разрешён policy engine")
    if action.risk_level not in ("LOW","MEDIUM") and not confirmed: raise DirectError("CONFIRMATION_REQUIRED","Для рискованного действия требуется подтверждение")
    conn=get_connection(db,account_id); gw=Gateway(conn); service,method,expected_state=allowed[action.action_type]; entity_id=int(action.entity_id)
    before=gw.call(service,"get",{"SelectionCriteria":{"Ids":[entity_id]},"FieldNames":["Id","State","Status"]})
    action.before_json=_dump(before); action.status="EXECUTING"; db.commit()
    obj=gw.call(service,method,{"SelectionCriteria":{"Ids":[entity_id]}}); action.api_response_json=_dump(obj); action.status="API_ACCEPTED"; action.executed_at=dt.datetime.utcnow(); db.commit()
    verify=gw.call(service,"get",{"SelectionCriteria":{"Ids":[entity_id]},"FieldNames":["Id","State","Status"]})
    key="Campaigns" if service=="campaigns" else "Keywords"; items=(verify.get("result") or {}).get(key) or []
    actual=str(items[0].get("State") if items else "UNKNOWN")
    action.after_json=_dump(verify)
    if actual!=expected_state:
        action.status="FAILED"; action.error_message=f"WRITE_VERIFY_FAILED expected={expected_state} actual={actual}"; db.commit()
        raise DirectError("WRITE_VERIFY_FAILED",f"Яндекс принял запрос, но фактическое состояние {actual}, ожидалось {expected_state}")
    action.status="VERIFIED"; action.verified_at=dt.datetime.utcnow(); action.rollback_available=True; action.error_message=None; db.commit()
    return {"status":"VERIFIED","action_id":action.id,"state":actual}


def rollback_action(db: Session, account_id: str, action_id: int, confirmed: bool=False) -> dict:
    """Rollback only actions for which BORIS has a deterministic inverse and verified history."""
    action=db.query(DirectAction).filter_by(id=action_id,account_id=account_id).first()
    if not action: raise DirectError("ACTION_NOT_FOUND","Действие не найдено")
    if not action.rollback_available or action.status!="VERIFIED": raise DirectError("ROLLBACK_NOT_AVAILABLE","Для этого действия нет безопасного rollback")
    inverse={"SUSPEND_CAMPAIGN":("campaigns","resume","ON"),"RESUME_CAMPAIGN":("campaigns","suspend","SUSPENDED"),"SUSPEND_KEYWORD":("keywords","resume","ON"),"RESUME_KEYWORD":("keywords","suspend","SUSPENDED")}
    if action.action_type not in inverse: raise DirectError("ROLLBACK_NOT_AVAILABLE","Для этого типа действия автоматический rollback не разрешён")
    if not confirmed: raise DirectError("CONFIRMATION_REQUIRED","Rollback изменяет рекламу и требует подтверждения")
    conn=get_connection(db,account_id); gw=Gateway(conn); service,method,expected_state=inverse[action.action_type]; entity_id=int(action.entity_id)
    obj=gw.call(service,method,{"SelectionCriteria":{"Ids":[entity_id]}})
    verify=gw.call(service,"get",{"SelectionCriteria":{"Ids":[entity_id]},"FieldNames":["Id","State","Status"]})
    key="Campaigns" if service=="campaigns" else "Keywords"; items=(verify.get("result") or {}).get(key) or []
    actual=str(items[0].get("State") if items else "UNKNOWN")
    action.api_response_json=_dump({"rollback":obj,"verify":verify})
    if actual!=expected_state:
        action.status="FAILED"; action.error_message=f"ROLLBACK_VERIFY_FAILED expected={expected_state} actual={actual}"; db.commit()
        raise DirectError("ROLLBACK_VERIFY_FAILED",f"Rollback принят API, но фактическое состояние {actual}, ожидалось {expected_state}")
    action.status="ROLLED_BACK"; action.rolled_back_at=dt.datetime.utcnow(); action.error_message=None; db.commit()
    return {"status":"ROLLED_BACK","action_id":action.id,"state":actual}


def experiment_candidates(db: Session, account_id: str) -> dict:
    """Safe experiment planner. It never writes to Direct; it ranks where one-variable tests are justified."""
    since=str(dt.date.today()-dt.timedelta(days=13)); rows=db.query(DirectStat).filter(DirectStat.account_id==account_id,DirectStat.day>=since).all()
    grouped={}
    for r in rows: grouped.setdefault(r.campaign_id,[]).append(r)
    entities={e.external_id:e.name for e in db.query(DirectEntity).filter_by(account_id=account_id,entity_type="campaign").all()}
    out=[]
    for cid,rs in grouped.items():
        cost=sum(float(x.cost or 0) for x in rs); conv=sum(float(x.conversions or 0) for x in rs); clicks=sum(int(x.clicks or 0) for x in rs); cpl=cost/conv if conv else None
        if clicks<30: continue
        hypothesis="Проверить новый оффер/креатив при неизменных ставках и аудитории" if conv>=3 else "Сначала проверить запросы и посадочную; креативный тест пока вторичен"
        out.append({"campaign_id":cid,"campaign":entities.get(cid,cid),"clicks":clicks,"cost":round(cost,2),"conversions":round(conv,2),"cpl":round(cpl,2) if cpl else None,"eligible":conv>=3,"hypothesis":hypothesis,"rule":"ONE_VARIABLE_ONLY","min_observation_days":7})
    out.sort(key=lambda x:(not x["eligible"],-x["cost"]))
    return {"status":"ok","candidates":out[:20],"policy":{"one_variable_only":True,"minimum_observation_days":7,"premature_winner_forbidden":True}}


def landing_health(db: Session, account_id: str) -> dict:
    """Read-only landing availability check. No campaign write is performed here."""
    from sqlalchemy import text
    import re, time
    row=db.execute(text("SELECT company_website FROM accounts WHERE account_id=:a LIMIT 1"),{"a":account_id}).mappings().first()
    raw=str((row or {}).get("company_website") or "").strip()
    m=re.search(r"https?://[^\s,]+",raw,re.I)
    if not m: return {"status":"NO_URL","url":None,"reachable":None,"latency_ms":None,"policy":"NO_AD_CHANGE"}
    url=m.group(0).rstrip(".,;)")
    started=time.monotonic(); code=None; final=url; error=None; attempts=[]
    for attempt in range(3):
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 (compatible; BORIS-AI-Director/1.0)"},method="GET")
            with urllib.request.urlopen(req,timeout=8) as r:
                code=int(r.status); final=r.geturl(); r.read(2048)
            attempts.append({"attempt":attempt+1,"http_status":code,"ok":bool(200<=code<400)})
            if 200<=code<400: break
        except urllib.error.HTTPError as e:
            code=int(e.code); error=f"HTTP_{e.code}"; attempts.append({"attempt":attempt+1,"http_status":code,"ok":False,"error":error})
        except Exception as e:
            error=type(e).__name__; attempts.append({"attempt":attempt+1,"http_status":None,"ok":False,"error":error})
        if attempt<2: time.sleep(0.35)
    latency=round((time.monotonic()-started)*1000)
    reachable=bool(code and 200<=code<400)
    failures=sum(1 for x in attempts if not x.get("ok"))
    severity="OK" if reachable else "UNCONFIRMED_OUTAGE"
    return {"status":"ok","url":url,"final_url":final,"http_status":code,"reachable":reachable,"latency_ms":latency,"severity":severity,"error":error,"attempts":attempts,"consecutive_failures":failures,"policy":"CHECK_YANDEX_SITE_MONITORING_BEFORE_MANUAL_SUSPEND","auto_suspend":False,"note":"Даже три технических ошибки одной проверки не равны подтверждённой 15-минутной недоступности. BORIS не останавливает рекламу до повторного временного подтверждения и проверки штатного мониторинга Яндекса."}


def plan_advanced_actions(db: Session, account_id: str) -> dict:
    """Materialize advanced optimizer recommendations into the canonical DirectAction ledger."""
    from app.director.intelligence import bid_modifier_optimizer, budget_allocator, creative_fatigue, negative_keyword_plan, scale_recovery_plan
    plans=[]
    sources=[
        ("BID_MODIFIER_RECOMMENDATION", bid_modifier_optimizer(db,account_id).get("recommendations") or [], lambda x:x.get("action")!="OBSERVE"),
        ("BUDGET_REALLOCATION_RECOMMENDATION", budget_allocator(db,account_id).get("moves") or [], lambda x:True),
        ("CREATIVE_FATIGUE_TEST", creative_fatigue(db,account_id).get("fatigue_candidates") or [], lambda x:True),
        ("NEGATIVE_KEYWORD_CANDIDATE", negative_keyword_plan(db,account_id).get("candidates") or [], lambda x:x.get("status")=="CANDIDATE" and not x.get("already_covered")),
        ("RECOVERY_SCALE_RECOMMENDATION", scale_recovery_plan(db,account_id).get("items") or [], lambda x:True),
    ]
    for action_type,items,predicate in sources:
        for item in items:
            if not predicate(item): continue
            entity_id=str(item.get("campaign_id") or item.get("ad_id") or item.get("candidate") or item.get("value") or "account")
            risk=str(item.get("risk") or ("MEDIUM" if action_type!="CREATIVE_FATIGUE_TEST" else "LOW"))
            confidence=float(item.get("confidence") or 0.8)
            fingerprint=_dump(item)
            key=hashlib.sha256(f"advanced:{account_id}:{dt.date.today()}:{action_type}:{entity_id}:{fingerprint}".encode()).hexdigest()
            existing=db.query(DirectAction).filter_by(idempotency_key=key).first()
            if existing:
                plans.append({"id":existing.id,"type":action_type,"status":existing.status,"deduplicated":True}); continue
            reason=str(item.get("reason") or item.get("hypothesis") or action_type)
            row=DirectAction(account_id=account_id,idempotency_key=key,entity_type="recommendation",entity_id=entity_id,action_type=action_type,risk_level=risk,confidence=confidence,reason=reason,evidence_json=fingerprint,status="PLANNED",rollback_available=False)
            db.add(row); db.flush(); plans.append({"id":row.id,"type":action_type,"status":"PLANNED","deduplicated":False})
    db.commit()
    return {"status":"ok","planned":plans,"count":len(plans)}
