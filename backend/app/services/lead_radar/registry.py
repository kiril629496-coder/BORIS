"""Explicit source registration.
No implicit crawler: each live source must be explicitly configured or implemented with an allowed API/public feed.
"""
import json,os,urllib.parse
from .connectors import RSSConnector,HHVacancyConnector,HTTPJSONConnector,HackerNewsConnector,HackerNewsShowConnector,HackerNewsJobsConnector,AppleAppStoreConnector,TEDTenderConnector,GitHubIssuesConnector,FreelancerProjectsConnector,BorisTelegramHotLeadConnector,BorisCRMCompaniesConnector,BorisCompanySitesConnector,BorisColdB2BConnector,AppleCustomerReviewsConnector,GoogleNewsSearchConnector
from .coverage import validate_group,SOURCE_GROUP_SET
from .open_web import BingRSSSearchConnector,DEFAULT_OPEN_WEB_GROUPS

DEFAULT_HH_QUERIES=['Flutter','1С программист','Python developer','React developer','DevOps','ML engineer','Bitrix разработчик']

def _json_env(name,default):
    raw=os.getenv(name,'').strip()
    if not raw: return default
    try: return json.loads(raw)
    except Exception as e: raise RuntimeError(f'{name}_INVALID_JSON:{e}')

def _tenant_context(owner_user_id,profile=None,catalog=None):
    if profile is not None:
        return dict(profile),list(catalog or [])
    url=os.getenv('DATABASE_URL') or os.getenv('DB_URL') or os.getenv('POSTGRES_URL')
    if not url:
        return None,[]
    try:
        from sqlalchemy import create_engine
        from .repository import SQLLeadRepository
        repo=SQLLeadRepository(create_engine(url,pool_pre_ping=True))
        p=repo.get_profile(owner_user_id)
        return (dict(p) if p else None),(repo.get_offerings(owner_user_id) if p else [])
    except Exception:
        return None,[]

def _flag(env_name,key,inherit,settings):
    if inherit:
        return os.getenv(env_name,'0').lower() in ('1','true','yes','on')
    return bool((settings.get('connectors') or {}).get(key,False))

def _merge_rows(base,custom):
    out={}
    for row in list(base or [])+list(custom or []):
        if not isinstance(row,dict): continue
        key=str(row.get('key') or '').strip()
        if key: out[key]=row
    return list(out.values())

def build_connectors(owner_user_id=None,profile=None,catalog=None):
    try: owner_user_id=int(owner_user_id or 0)
    except Exception: owner_user_id=0
    if owner_user_id<=0: raise RuntimeError('LEAD_RADAR_OWNER_REQUIRED_FAIL_CLOSED')
    profile,catalog=_tenant_context(owner_user_id,profile,catalog)
    settings=dict((profile or {}).get('settings_json') or {})
    inherit=bool(settings.get('inherit_global_sources',profile is None or str((profile or {}).get('mode') or '').lower()=='development'))
    out=[]
    database_url=os.getenv('DATABASE_URL') or os.getenv('DB_URL') or os.getenv('POSTGRES_URL')
    if _flag('LEAD_RADAR_BORIS_TELEGRAM_ENABLED','boris_telegram',inherit,settings):
        out.append(BorisTelegramHotLeadConnector(database_url,owner_user_id=owner_user_id))
    if _flag('LEAD_RADAR_BORIS_CRM_COMPANIES_ENABLED','boris_crm_companies',inherit,settings):
        out.append(BorisCRMCompaniesConnector(database_url,owner_user_id=owner_user_id))
    if _flag('LEAD_RADAR_BORIS_COMPANY_SITES_ENABLED','boris_company_sites',inherit,settings):
        out.append(BorisCompanySitesConnector(database_url,owner_user_id=owner_user_id))
    if _flag('LEAD_RADAR_BORIS_COLD_B2B_ENABLED','boris_cold_b2b',inherit,settings):
        out.append(BorisColdB2BConnector(database_url,owner_user_id=owner_user_id))
    if _flag('LEAD_RADAR_APPLE_REVIEWS_ENABLED','apple_reviews',inherit,settings):
        out.append(AppleCustomerReviewsConnector(
            database_url,country=os.getenv('LEAD_RADAR_APPLE_REVIEWS_COUNTRY','us'),
            owner_user_id=owner_user_id,
            max_apps_per_cycle=int(os.getenv('LEAD_RADAR_APPLE_REVIEWS_MAX_APPS','6')),
            rating_max=int(os.getenv('LEAD_RADAR_APPLE_REVIEWS_RATING_MAX','2')),
            min_request_interval_seconds=float(os.getenv('LEAD_RADAR_APPLE_REVIEWS_MIN_REQUEST_INTERVAL_SECONDS','1.0')),
            timeout=int(os.getenv('LEAD_RADAR_APPLE_REVIEWS_TIMEOUT','15')),
        ))
    # [{"key":"workspace_feed","url":"https://.../feed","family":"brief"}]
    rss_rows=_merge_rows(_json_env('LEAD_RADAR_RSS_SOURCES_JSON',[]) if inherit else [],settings.get('rss_sources') or [])
    for row in rss_rows:
        if not isinstance(row,dict) or not row.get('key') or not row.get('url'):
            raise RuntimeError('LEAD_RADAR_RSS_SOURCE_INVALID_FAIL_CLOSED')
        c=RSSConnector(row['key'],row['url'],row.get('family','brief'),owner_user_id,timeout=int(row.get('timeout',15)))
        c.group_key=validate_group(row.get('group') or (row['key'] if row['key'] in SOURCE_GROUP_SET else ''))
        out.append(c)
    # Standardized provider/partner gateways. reply_url is optional; AUTO still requires separate source policy.
    http_rows=_merge_rows(_json_env('LEAD_RADAR_HTTP_SOURCES_JSON',[]) if inherit else [],settings.get('http_sources') or [])
    for row in http_rows:
        if not isinstance(row,dict) or not row.get('key') or not row.get('fetch_url'):
            raise RuntimeError('LEAD_RADAR_HTTP_SOURCE_INVALID_FAIL_CLOSED')
        host=(urllib.parse.urlparse(str(row['fetch_url'])).hostname or '').lower()
        if host.endswith('producthunt.com') and os.getenv('LEAD_RADAR_PRODUCTHUNT_COMMERCIAL_PERMISSION','0').lower() not in ('1','true','yes','on'):
            raise RuntimeError('PRODUCT_HUNT_COMMERCIAL_PERMISSION_REQUIRED_FAIL_CLOSED')
        c=HTTPJSONConnector(row['key'],row['fetch_url'],row.get('family','brief'),row.get('reply_url'),row.get('headers') or {},owner_user_id,int(row.get('timeout',15)),reply_idempotent=bool(row.get('reply_idempotent',False)))
        c.group_key=validate_group(row.get('group') or (row['key'] if row['key'] in SOURCE_GROUP_SET else ''))
        out.append(c)
    if _flag('LEAD_RADAR_HH_ENABLED','hh',inherit,settings):
        queries=_json_env('LEAD_RADAR_HH_QUERIES_JSON',DEFAULT_HH_QUERIES)
        if not isinstance(queries,list) or not queries: raise RuntimeError('LEAD_RADAR_HH_QUERIES_INVALID')
        ua=os.getenv('LEAD_RADAR_HH_USER_AGENT','').strip()
        if not ua: raise RuntimeError('LEAD_RADAR_HH_USER_AGENT_REQUIRED')
        out.append(HHVacancyConnector(queries,area=os.getenv('LEAD_RADAR_HH_AREA') or None,owner_user_id=owner_user_id,user_agent=ua))

    if _flag('LEAD_RADAR_HN_ENABLED','hacker_news',inherit,settings):
        terms=_json_env('LEAD_RADAR_HN_TERMS_JSON',list(HackerNewsConnector.DEFAULT_TERMS))
        if not isinstance(terms,list) or not terms: raise RuntimeError('LEAD_RADAR_HN_TERMS_INVALID')
        out.append(HackerNewsConnector(owner_user_id=owner_user_id,max_items=int(os.getenv('LEAD_RADAR_HN_MAX_ITEMS','200')),terms=terms))

    if _flag('LEAD_RADAR_HN_SHOW_ENABLED','hacker_news_show',inherit,settings):
        out.append(HackerNewsShowConnector(owner_user_id=owner_user_id,max_items=int(os.getenv('LEAD_RADAR_HN_SHOW_MAX_ITEMS','80'))))

    if _flag('LEAD_RADAR_HN_JOBS_ENABLED','hacker_news_jobs',inherit,settings):
        out.append(HackerNewsJobsConnector(owner_user_id=owner_user_id,max_items=int(os.getenv('LEAD_RADAR_HN_JOBS_MAX_ITEMS','200'))))

    if _flag('LEAD_RADAR_TED_ENABLED','ted',inherit,settings):
        terms=_json_env('LEAD_RADAR_TED_TERMS_JSON',list(TEDTenderConnector.DEFAULT_TERMS))
        if not isinstance(terms,list) or not terms: raise RuntimeError('LEAD_RADAR_TED_TERMS_INVALID')
        out.append(TEDTenderConnector(owner_user_id=owner_user_id,terms=terms,scope=os.getenv('LEAD_RADAR_TED_SCOPE','ACTIVE'),
            timeout=int(os.getenv('LEAD_RADAR_TED_TIMEOUT','20')),page_size=int(os.getenv('LEAD_RADAR_TED_PAGE_SIZE','250'))))
    if _flag('LEAD_RADAR_GITHUB_ENABLED','github',inherit,settings):
        queries=_json_env('LEAD_RADAR_GITHUB_QUERIES_JSON',[])
        if not isinstance(queries,list) or not queries: raise RuntimeError('LEAD_RADAR_GITHUB_QUERIES_REQUIRED')
        out.append(GitHubIssuesConnector(queries,owner_user_id=owner_user_id,token=os.getenv('LEAD_RADAR_GITHUB_TOKEN',''),
            timeout=int(os.getenv('LEAD_RADAR_GITHUB_TIMEOUT','15'))))
    if _flag('LEAD_RADAR_FREELANCER_ENABLED','freelancer',inherit,settings):
        queries=_json_env('LEAD_RADAR_FREELANCER_QUERIES_JSON',list(FreelancerProjectsConnector.DEFAULT_QUERIES))
        if not isinstance(queries,list) or not queries: raise RuntimeError('LEAD_RADAR_FREELANCER_QUERIES_REQUIRED')
        out.append(FreelancerProjectsConnector(
            queries,owner_user_id=owner_user_id,
            oauth_token=os.getenv('LEAD_RADAR_FREELANCER_OAUTH_TOKEN',''),
            timeout=int(os.getenv('LEAD_RADAR_FREELANCER_TIMEOUT','15')),
            page_size=int(os.getenv('LEAD_RADAR_FREELANCER_PAGE_SIZE','100')),
        ))
    if _flag('LEAD_RADAR_APPLE_ENABLED','apple',inherit,settings):
        terms=_json_env('LEAD_RADAR_APPLE_TERMS_JSON',[])
        if not isinstance(terms,list) or not terms: raise RuntimeError('LEAD_RADAR_APPLE_TERMS_REQUIRED')
        out.append(AppleAppStoreConnector(terms,country=os.getenv('LEAD_RADAR_APPLE_COUNTRY','us'),owner_user_id=owner_user_id,
            limit_per_term=int(os.getenv('LEAD_RADAR_APPLE_LIMIT','200')),rating_max=float(os.getenv('LEAD_RADAR_APPLE_RATING_MAX','3.5')),
            stale_days=int(os.getenv('LEAD_RADAR_APPLE_STALE_DAYS','365')),min_request_interval_seconds=float(os.getenv('LEAD_RADAR_APPLE_MIN_REQUEST_INTERVAL_SECONDS','3.2'))))

    # Stable code-managed public news signals. Explicit user-configured sources
    # always win; this only fills still-missing groups while public web is enabled.
    if (_flag('LEAD_RADAR_OPEN_WEB_ENABLED','open_web',inherit,settings) or bool(settings.get('open_web_enabled',False))):
        configured={getattr(x,'group_key','unmapped') for x in out}
        google_news_defaults={
            'news':('money',[
                'startup raised funding software SaaS',
                'AI startup funding platform',
                'software startup raises funding',
            ]),
            'new_companies':('startup',[
                'startup launches software platform',
                'startup launches AI platform',
                'startup launches mobile app',
            ]),
            'franchises':('launch',[
                'franchise mobile app launch',
                'franchise CRM technology rollout',
                'franchise AI automation expansion',
            ]),
        }
        for group,(family,queries) in google_news_defaults.items():
            if group in configured:
                continue
            out.append(GoogleNewsSearchConnector(
                key=group,group_key=group,family=family,queries=queries,
                owner_user_id=owner_user_id,
                country=os.getenv('LEAD_RADAR_NEWS_COUNTRY','US'),
                lang=os.getenv('LEAD_RADAR_NEWS_LANG','en-US'),
                timeout=int(os.getenv('LEAD_RADAR_NEWS_TIMEOUT','15')),
            ))
            configured.add(group)

    # OPEN_WEB_MISSING_GROUPS_REGISTRY_V1: activate only still-missing source
    # groups. Direct/API connectors always win if a stronger source is already
    # configured for the same group. All public-search lanes are read-only.
    if (_flag('LEAD_RADAR_OPEN_WEB_ENABLED','open_web',inherit,settings) or bool(settings.get('open_web_enabled',False))):
        cfg=_json_env('LEAD_RADAR_OPEN_WEB_GROUPS_JSON',DEFAULT_OPEN_WEB_GROUPS) if inherit else (settings.get('open_web_groups') or {})
        if not isinstance(cfg,dict): raise RuntimeError('LEAD_RADAR_OPEN_WEB_GROUPS_INVALID')
        configured={getattr(x,'group_key','unmapped') for x in out}
        for group,row in cfg.items():
            group=validate_group(group)
            if group in configured: continue
            if not isinstance(row,dict): raise RuntimeError('LEAD_RADAR_OPEN_WEB_GROUP_INVALID:'+group)
            queries=row.get('queries') or []
            if not isinstance(queries,list) or not queries: raise RuntimeError('LEAD_RADAR_OPEN_WEB_QUERIES_INVALID:'+group)
            out.append(BingRSSSearchConnector(
                key=group,group_key=group,family=str(row.get('family') or group),queries=queries,
                owner_user_id=owner_user_id,timeout=int(os.getenv('LEAD_RADAR_OPEN_WEB_TIMEOUT','15')),
                max_results_per_query=int(os.getenv('LEAD_RADAR_OPEN_WEB_MAX_RESULTS','20')),
                min_request_interval_seconds=float(os.getenv('LEAD_RADAR_OPEN_WEB_MIN_REQUEST_INTERVAL_SECONDS','1.2')),
            ))
            configured.add(group)
    return out
