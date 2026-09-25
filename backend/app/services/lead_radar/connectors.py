from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json, urllib.request, urllib.parse, urllib.error, xml.etree.ElementTree as ET, html, re, hashlib, time, subprocess, shutil
import requests
from sqlalchemy import create_engine, text as sa_text
from .models import RawLead
from app.services.exception_observability import observe_suppressed

def _owner(value):
    try: v=int(value or 0)
    except Exception: v=0
    if v<=0: raise ValueError('LEAD_RADAR_OWNER_REQUIRED_FAIL_CLOSED')
    return v

class SourceConnector:
    key='base'; family='unknown'; group_key='unmapped'; supports_outreach=False; outreach_idempotent=False
    def fetch(self,since): raise NotImplementedError
    def send_reply(self,raw,text): raise RuntimeError('SOURCE_OUTREACH_NOT_IMPLEMENTED')

class ConnectorSenderRouter:
    """Routes AUTO only to a connector that explicitly implements send_reply."""
    def __init__(self,connectors): self.by_key={x.key:x for x in connectors if getattr(x,'supports_outreach',False) and getattr(x,'outreach_idempotent',False)}
    def retry_safe(self,source): return source in self.by_key
    def send(self,raw,text):
        c=self.by_key.get(raw.source)
        if not c: raise RuntimeError('NO_PERMITTED_SOURCE_SENDER')
        return c.send_reply(raw,text)

def _parse_dt(value):
    if not value: return None
    v=value.strip()
    try:
        dt=parsedate_to_datetime(v)
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=34)
    try:
        dt=datetime.fromisoformat(v.replace('Z','+00:00'))
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception: return None

class RSSConnector(SourceConnector):
    """Generic RSS/Atom connector for explicitly configured public/allowed feeds."""
    def __init__(self,key,url,family='brief',owner_user_id=None,timeout=15): self.key=key; self.url=url; self.family=family; self.owner=_owner(owner_user_id); self.timeout=timeout

    def _fetch_bytes(self):
        """RSS_TRANSPORT_SELF_HEAL_V1: bounded retry + independent TLS fallback."""
        headers={'User-Agent':'BORIS-Lead-Radar/1.0','Accept':'application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.1'}
        errors=[]
        host=(urllib.parse.urlparse(self.url).hostname or '').lower()
        for attempt in range(3):
            try:
                # GOOGLE_NEWS_FAST_FAIL_V1: Google News TLS is intermittently
                # unstable on this host. Give it one short curl/HTTP1.1 attempt;
                # GoogleNewsSearchConnector immediately falls back to Bing on failure.
                if attempt == 0 and host == 'news.google.com':
                    curl=shutil.which('curl')
                    if not curl: raise RuntimeError('RSS_CURL_FALLBACK_UNAVAILABLE')
                    cp=subprocess.run(
                        [curl,'-4','--http1.1','-L','-sS','--connect-timeout','4',
                         '--max-time','7','-A',headers['User-Agent'],self.url],
                        stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                        timeout=10,check=False,
                    )
                    if cp.returncode!=0 or not cp.stdout:
                        raise RuntimeError('RSS_GOOGLE_FAST_FAIL:'+cp.stderr.decode('utf-8','replace')[:220])
                    return cp.stdout
                if attempt == 0:
                    req=urllib.request.Request(self.url,headers=headers)
                    with urllib.request.urlopen(req,timeout=self.timeout) as r:
                        status=int(getattr(r,'status',200) or 200)
                        if status >= 400: raise RuntimeError('RSS_HTTP_'+str(status))
                        return r.read()
                if attempt == 1:
                    r=requests.get(self.url,headers=headers,timeout=(5,self.timeout),allow_redirects=True)
                    r.raise_for_status()
                    return r.content
                curl=shutil.which('curl')
                if not curl:
                    raise RuntimeError('RSS_CURL_FALLBACK_UNAVAILABLE')
                cp=subprocess.run(
                    [curl,'-4','--http1.1','-L','-sS','--connect-timeout','8',
                     '--max-time',str(max(12,int(self.timeout)+10)),
                     '-A',headers['User-Agent'],self.url],
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                    timeout=max(20,int(self.timeout)+15),check=False,
                )
                if cp.returncode!=0 or not cp.stdout:
                    raise RuntimeError('RSS_CURL_FAILED:'+cp.stderr.decode('utf-8','replace')[:220])
                return cp.stdout
            except Exception as exc:
                errors.append(type(exc).__name__+':'+str(exc)[:220])
                if host == 'news.google.com':
                    break
                if attempt < 2:
                    time.sleep(0.8*(attempt+1))
        raise RuntimeError('RSS_FETCH_FAILED_AFTER_RETRY|'+'|'.join(errors))

    def fetch(self,since):
        data=self._fetch_bytes()
        root=ET.fromstring(data); out=[]
        items=root.findall('.//item') or root.findall('.//{http://www.w3.org/2005/Atom}entry')
        for i,x in enumerate(items):
            def tx(name):
                e=x.find(name)
                if e is None: e=x.find('{http://www.w3.org/2005/Atom}'+name)
                return (e.text or '').strip() if e is not None and e.text else ''
            title=tx('title'); desc=tx('description') or tx('summary') or tx('content'); link=tx('link')
            if not link:
                e=x.find('{http://www.w3.org/2005/Atom}link'); link=e.attrib.get('href','') if e is not None else ''
            published=_parse_dt(tx('pubDate') or tx('published') or tx('updated'))
            # Fresh means timestamp evidence exists. Unknown-age feed items are not promoted as fresh leads.
            if published is None: continue
            if since and published<since: continue
            out.append(RawLead(source=self.key,external_id=tx('guid') or tx('id') or str(i),url=link,title=title,text=desc,published_at=published,source_family=self.family,owner_user_id=self.owner,metadata={'connector':'rss','timestamp_in_feed':bool(tx('pubDate') or tx('published') or tx('updated'))}))
        return out


class GoogleNewsSearchConnector(SourceConnector):
    """Read-only timestamped Google News RSS search with code-managed query sets.

    Uses RSSConnector transport, so production TLS recovery (curl fallback) is shared.
    Each item gets a bounded stable SHA-256 external ID instead of provider GUID length.
    """
    supports_outreach=False
    outreach_idempotent=False
    BASE='https://news.google.com/rss/search'

    def __init__(self,key,group_key,family,queries,owner_user_id=None,country='US',lang='en-US',timeout=15):
        self.key=str(key); self.group_key=str(group_key); self.family=str(family)
        self.owner=_owner(owner_user_id); self.country=str(country or 'US').upper()
        self.lang=str(lang or 'en-US'); self.timeout=max(5,int(timeout))
        self.queries=[str(x).strip() for x in (queries or []) if str(x).strip()]
        if not self.queries:
            raise ValueError('GOOGLE_NEWS_QUERIES_REQUIRED')

    @staticmethod
    def _clean(value):
        return re.sub(r'\s+',' ',re.sub(r'<[^>]+>',' ',html.unescape(str(value or '')))).strip()

    @staticmethod
    def _infer_company(title):
        base=str(title or '').rsplit(' - ',1)[0].strip()
        m=re.match(
            r'^(.{2,100}?)\s+(?:raises?|secures?|launches?|unveils?|debuts?|introduces?|expands?|'
            r'rolls?\s+out|streamlines?|adopts?|tests?|acquires?|partners?)\b',
            base,re.I,
        )
        if not m:
            return ''
        name=m.group(1).strip(' :-')
        name=re.sub(r'^(?:exclusive:\s*)','',name,flags=re.I)
        name=re.sub(
            r'^(?:(?:uk|us|german|chicago|malaysian|israeli|singapore|african|healthtech|sports-tech|hr)\s+)?'
            r'(?:(?:healthcare|software|ai|deep-tech|tech)\s+)?startup\s+',
            '',name,flags=re.I,
        ).strip()
        return name[:180] if 1 < len(name) <= 180 else ''

    def _evidence_and_services(self,text):
        t=str(text or '').lower()
        tech_terms=('software','saas','ai ',' ai','mobile app','application','platform','crm','automation',
                    'digital','technology','tech ','integration','api','agent')
        if not any(x in t for x in tech_terms):
            return False,[]
        services=set()
        if any(x in t for x in ('saas','software platform','digital platform','software startup')):
            services.add(5)
        if any(x in t for x in ('mobile app','mobile application','app launch','launches app')):
            services.add(9)
        if re.search(r'\bcrm\b',t):
            services.add(15)
        if any(x in t for x in ('ai platform','ai agent','ai automation','automation','artificial intelligence')):
            services.add(26)
        if any(x in t for x in ('integration','integrat','api ')):
            services.add(17)

        if self.group_key=='news':
            if any(x in t for x in ('funding roundup','funding report','offers funding','offer funding','funding to startups')):
                return False,[]
            event=bool(
                re.search(r'\b(?:raises?|raised|secures?|secured|closes?|closed|lands?|receives?|received)\b.{0,50}\b(?:funding|round|seed|series|investment|\$|million|m)\b',t)
                or any(x in t for x in ('seed round','series a','series b','funding round','funded by','investment round'))
            )
            return bool(event and services),sorted(services)
        if self.group_key=='new_companies':
            event=any(x in t for x in (
                ' launch','launches','launched','unveil','debuts','introduces','rolls out',
            ))
            return bool(event and services),sorted(services)
        if self.group_key=='franchises':
            franchise=any(x in t for x in ('franchise','franchisee','restaurant chain','retail chain'))
            action=any(x in t for x in (
                ' launch','rollout','rolls out','expand','expands','automation','adopt','streamline',
                'test','deploy','technology','digital','crm','mobile app','ai ',
            ))
            return bool(franchise and action and services),sorted(services)
        return bool(services),sorted(services)

    def fetch(self,since):
        out=[]; seen=set()
        ceid=f'{self.country}:en'
        for query in self.queries:
            google_url=self.BASE+'?'+urllib.parse.urlencode({
                'q':query,'hl':self.lang,'gl':self.country,'ceid':ceid,
            })
            provider='google_news'
            primary_error=None
            try:
                rows=RSSConnector(self.key,google_url,self.family,self.owner,timeout=self.timeout).fetch(since)
            except Exception as exc:
                # GOOGLE_NEWS_BING_FALLBACK_V1: provider/TLS failure is healed
                # inside this connector; no owner action and no fake fresh data.
                primary_error=(type(exc).__name__+':'+str(exc))[:500]
                provider='bing_rss_fallback'
                bing_url='https://www.bing.com/news/search?'+urllib.parse.urlencode({
                    'q':query,'format':'rss','setlang':'en-us','count':20,
                })
                rows=RSSConnector(self.key,bing_url,self.family,self.owner,timeout=min(self.timeout,10)).fetch(since)
            for row in rows:
                clean_title=self._clean(row.title)
                clean_text=self._clean(row.text)
                stable_title=re.sub(r'\s+',' ',clean_title.lower()).strip()
                stable_day=row.published_at.astimezone(timezone.utc).date().isoformat()
                stable=hashlib.sha256(
                    (self.key+'|'+stable_day+'|'+stable_title).encode('utf-8')
                ).hexdigest()
                if stable in seen:
                    continue
                seen.add(stable)
                evidence_ok,suggested_services=self._evidence_and_services(clean_title+' '+clean_text)
                if not evidence_ok:
                    continue
                row.external_id=stable
                row.title=clean_title
                row.text=clean_text
                row.source=self.key
                row.source_family=self.family
                row.owner_user_id=self.owner
                row.company_name=self._infer_company(clean_title)
                meta=dict(row.metadata or {})
                meta.update({
                    'connector':'google_news_search_rss',
                    'source_group':self.group_key,
                    'search_query':query,
                    'provider':provider,
                    'primary_error':primary_error,
                    'suggested_service_ids':suggested_services,
                    'evidence_gate':'technology_plus_event_v1',
                    'read_only':True,
                })
                row.metadata=meta
                out.append(row)
        return out


class HTTPJSONConnector(SourceConnector):
    """Standardized allowed API gateway.
    Fetch endpoint returns either a JSON list or {"items":[...]}; reply endpoint is optional.
    This adapter is intentionally generic: provider-specific auth/ToS remains explicit deployment config.
    """
    def __init__(self,key,fetch_url,family='brief',reply_url=None,headers=None,owner_user_id=None,timeout=15,reply_idempotent=False):
        if not key or not fetch_url: raise ValueError('HTTP_SOURCE_KEY_AND_FETCH_URL_REQUIRED')
        self.key=key; self.fetch_url=fetch_url; self.family=family; self.reply_url=reply_url; self.headers=dict(headers or {}); self.owner=_owner(owner_user_id); self.timeout=timeout
        self.outreach_idempotent=bool(reply_url and reply_idempotent); self.supports_outreach=self.outreach_idempotent
    def _request(self,url,method='GET',payload=None):
        headers={'User-Agent':'BORIS-Lead-Radar/1.0',**self.headers}; data=None
        if payload is not None:
            data=json.dumps(payload,ensure_ascii=False).encode(); headers.setdefault('Content-Type','application/json')
        req=urllib.request.Request(url,data=data,headers=headers,method=method)
        with urllib.request.urlopen(req,timeout=self.timeout) as r: return json.loads(r.read())
    def fetch(self,since):
        data=self._request(self.fetch_url); rows=data.get('items',[]) if isinstance(data,dict) else data
        if not isinstance(rows,list): raise RuntimeError('HTTP_SOURCE_RESPONSE_MUST_BE_LIST')
        out=[]
        for i,x in enumerate(rows):
            if not isinstance(x,dict): continue
            published=_parse_dt(x.get('published_at'))
            if published is None: continue
            if since and published<since: continue
            out.append(RawLead(source=self.key,external_id=str(x.get('id') or x.get('external_id') or i),url=str(x.get('url') or ''),
                title=str(x.get('title') or ''),text=str(x.get('text') or x.get('body') or ''),published_at=published,source_family=str(x.get('source_family') or self.family),
                author=str(x.get('author') or ''),company_name=str(x.get('company_name') or ''),company_domain=str(x.get('company_domain') or ''),
                location=str(x.get('location') or ''),contact_hint=str(x.get('contact_hint') or ''),owner_user_id=self.owner,metadata=x.get('metadata') or {}))
        return out
    def send_reply(self,raw,text):
        if not self.reply_url: raise RuntimeError('HTTP_SOURCE_REPLY_NOT_CONFIGURED')
        if not self.outreach_idempotent: raise RuntimeError('HTTP_SOURCE_IDEMPOTENCY_REQUIRED_FAIL_CLOSED')
        stable=hashlib.sha256(f'{self.owner}|{self.key}|{raw.external_id}|{raw.url}|{text}'.encode('utf-8')).hexdigest()
        data=self._request(self.reply_url,'POST',{'external_id':raw.external_id,'url':raw.url,'message':text,'idempotency_key':stable})
        if isinstance(data,dict) and data.get('ok') is False: raise RuntimeError('HTTP_SOURCE_REPLY_REJECTED:'+str(data.get('error') or 'unknown'))
        return (data or {}).get('message_id') if isinstance(data,dict) else None

class HHVacancyConnector(SourceConnector):
    """HH API read-only. Vacancy = B2B signal; applicant auto-reply is forbidden by policy."""
    key='hh'; family='jobs'; group_key='jobs'
    def __init__(self,queries,area=None,owner_user_id=None,timeout=15,user_agent=None):
        if not user_agent: raise ValueError('HH_USER_AGENT_REQUIRED_FAIL_CLOSED')
        self.queries=queries; self.area=area; self.owner=_owner(owner_user_id); self.timeout=timeout; self.user_agent=user_agent
    def fetch(self,since):
        out=[]
        for q in self.queries:
            page=0
            while True:
                params={'text':q,'per_page':100,'page':page,'order_by':'publication_time'}
                if self.area: params['area']=self.area
                url='https://api.hh.ru/vacancies?'+urllib.parse.urlencode(params)
                req=urllib.request.Request(url,headers={'User-Agent':self.user_agent,'HH-User-Agent':self.user_agent})
                with urllib.request.urlopen(req,timeout=self.timeout) as r: data=json.loads(r.read())
                items=data.get('items',[]) or []
                for x in items:
                    emp=x.get('employer') or {}; salary=x.get('salary') or {}
                    published=_parse_dt(x.get('published_at'))
                    if published is None: continue
                    if since and published<since: continue
                    body=f"{x.get('name','')} {x.get('snippet',{}).get('requirement','')} {x.get('snippet',{}).get('responsibility','')} salary {salary}"
                    out.append(RawLead(source='hh',external_id=str(x.get('id','')),url=x.get('alternate_url',''),title=x.get('name',''),text=body,published_at=published,source_family='jobs',company_name=emp.get('name',''),owner_user_id=self.owner,metadata={'employer_url':emp.get('alternate_url'),'salary':salary,'hh_page':page}))
                pages=max(1,int(data.get('pages') or 1))
                if not items or page+1>=pages: break
                # Ordered newest first. Once an entire page is older than the freshness window, later pages are older too.
                dated=[_parse_dt(x.get('published_at')) for x in items if x.get('published_at')]
                if since and dated and all(dt is not None and dt<since for dt in dated): break
                page+=1
        return out


class HackerNewsConnector(SourceConnector):
    """Official Hacker News Firebase API. Read-only signal source; no auto outreach."""
    key='hacker_news'; family='social'; group_key='communities'; supports_outreach=False
    BASE='https://hacker-news.firebaseio.com/v0'
    DEFAULT_TERMS=(
        'looking for developer','need a developer','need developer','looking for agency','need an app','need app',
        'need mvp','need automation','technical cofounder','looking for technical cofounder','need a website','need website',
        'need a bot','need bot','need crm','need integration','looking for flutter','looking for react','looking for python'
    )
    def __init__(self,owner_user_id=None,max_items=60,timeout=12,terms=None):
        self.owner=_owner(owner_user_id); self.max_items=max(1,min(200,int(max_items))); self.timeout=timeout
        self.terms=tuple((terms or self.DEFAULT_TERMS))
    def _json(self,url):
        req=urllib.request.Request(url,headers={'User-Agent':'BORIS-Lead-Radar/1.0'})
        with urllib.request.urlopen(req,timeout=self.timeout) as r: return json.loads(r.read())
    @staticmethod
    def _clean(v):
        return re.sub(r'\s+',' ',re.sub(r'<[^>]+>',' ',html.unescape(v or ''))).strip()
    def fetch(self,since):
        ids=self._json(self.BASE+'/askstories.json') or []
        out=[]
        for item_id in ids[:self.max_items]:
            x=self._json(f'{self.BASE}/item/{int(item_id)}.json') or {}
            if x.get('deleted') or x.get('dead') or x.get('type')!='story': continue
            published=datetime.fromtimestamp(int(x.get('time') or 0),tz=timezone.utc)
            if since and published<since: continue
            title=self._clean(x.get('title')); body=self._clean(x.get('text')); hay=(title+' '+body).lower()
            if not any(t.lower() in hay for t in self.terms): continue
            out.append(RawLead(source=self.key,external_id=str(x.get('id') or item_id),
                url=f"https://news.ycombinator.com/item?id={x.get('id') or item_id}",title=title,text=body,
                published_at=published,source_family=self.family,author=str(x.get('by') or ''),owner_user_id=self.owner,
                metadata={'connector':'hacker_news_official','score':x.get('score'),'descendants':x.get('descendants')}))
        return out




class HackerNewsShowConnector(SourceConnector):
    """Official Hacker News showstories API as startup/launch signal.

    Read-only. New product launches are prospecting signals only; no automatic
    outreach is available on this connector.
    """
    key='hacker_news_show'; family='startup'; group_key='startups'; supports_outreach=False
    BASE='https://hacker-news.firebaseio.com/v0'
    def __init__(self,owner_user_id=None,max_items=80,timeout=12):
        self.owner=_owner(owner_user_id); self.max_items=max(1,min(120,int(max_items))); self.timeout=int(timeout)
    def _json(self,url):
        req=urllib.request.Request(url,headers={'User-Agent':'BORIS-Lead-Radar/1.0'})
        with urllib.request.urlopen(req,timeout=self.timeout) as r: return json.loads(r.read())
    def fetch(self,since):
        ids=self._json(self.BASE+'/showstories.json') or []
        out=[]
        for item_id in ids[:self.max_items]:
            x=self._json(f'{self.BASE}/item/{int(item_id)}.json') or {}
            if x.get('deleted') or x.get('dead') or x.get('type')!='story':
                continue
            published=datetime.fromtimestamp(int(x.get('time') or 0),tz=timezone.utc)
            if since and published<since:
                continue
            title=re.sub(r'\s+',' ',html.unescape(str(x.get('title') or ''))).strip()
            body=re.sub(r'\s+',' ',re.sub(r'<[^>]+>',' ',html.unescape(str(x.get('text') or '')))).strip()
            # Keep only genuine Show HN launches. Engine will still require a match
            # to one of the 35 development services before CRM creation.
            if not title.lower().startswith('show hn:'):
                continue
            target_url=str(x.get('url') or '')
            host=(urllib.parse.urlparse(target_url).hostname or '').lower()
            if host.startswith('www.'): host=host[4:]
            # Repo-only launches are usually developer projects, not commercial prospects.
            if host in ('github.com','gitlab.com','bitbucket.org'):
                continue
            if not target_url:
                continue
            out.append(RawLead(
                source=self.key, external_id=str(x.get('id') or item_id),
                url=target_url,
                title=title, text=body, published_at=published, source_family=self.family,
                author=str(x.get('by') or ''), company_name=host[:180], company_domain=host[:180],
                owner_user_id=self.owner,
                metadata={'connector':'hacker_news_official_show','hn_item_url':f"https://news.ycombinator.com/item?id={x.get('id') or item_id}",
                          'score':x.get('score'),'descendants':x.get('descendants')}
            ))
        return out


class HackerNewsJobsConnector(SourceConnector):
    """Official Hacker News jobstories API, read-only hiring signal.

    A job listing is treated as evidence that a company needs engineering capacity.
    It is never used for applicant impersonation or automatic job application.
    """
    key='hacker_news_jobs'; family='jobs'; group_key='jobs'; supports_outreach=False
    BASE='https://hacker-news.firebaseio.com/v0'
    ROLE_TERMS=(
        'engineer','developer','software','backend','frontend','full stack','full-stack',
        'mobile','ios','android','flutter','react','python','java','javascript','typescript',
        'devops','sre','machine learning','ml ','ai ','data engineer','1c','1с','bitrix','crm'
    )
    def __init__(self,owner_user_id=None,max_items=200,timeout=12):
        self.owner=_owner(owner_user_id); self.max_items=max(1,min(200,int(max_items))); self.timeout=int(timeout)
    def _json(self,url):
        req=urllib.request.Request(url,headers={'User-Agent':'BORIS-Lead-Radar/1.0'})
        with urllib.request.urlopen(req,timeout=self.timeout) as r: return json.loads(r.read())
    def fetch(self,since):
        ids=self._json(self.BASE+'/jobstories.json') or []
        out=[]
        for item_id in ids[:self.max_items]:
            x=self._json(f'{self.BASE}/item/{int(item_id)}.json') or {}
            if x.get('deleted') or x.get('dead') or x.get('type')!='job':
                continue
            published=datetime.fromtimestamp(int(x.get('time') or 0),tz=timezone.utc)
            if since and published<since:
                continue
            title=re.sub(r'\s+',' ',html.unescape(str(x.get('title') or ''))).strip()
            body=re.sub(r'\s+',' ',re.sub(r'<[^>]+>',' ',html.unescape(str(x.get('text') or '')))).strip()
            hay=(title+' '+body).lower()
            if not any(t in hay for t in self.ROLE_TERMS):
                continue
            low=title.lower()
            company=title[:180]
            if ' is hiring' in low:
                company=title[:low.index(' is hiring')][:180]
            out.append(RawLead(
                source=self.key, external_id=str(x.get('id') or item_id),
                url=str(x.get('url') or f"https://news.ycombinator.com/item?id={x.get('id') or item_id}"),
                title=title, text=body, published_at=published, source_family=self.family,
                author=str(x.get('by') or ''), company_name=company,
                owner_user_id=self.owner,
                metadata={'connector':'hacker_news_official_jobs','hn_item_url':f"https://news.ycombinator.com/item?id={x.get('id') or item_id}"}
            ))
        return out


class AppleAppStoreConnector(SourceConnector):
    group_key='apps'
    """Public Apple iTunes Search API audit signal.
    It searches only explicitly configured terms and turns low-rating / long-stale apps into DRAFT business-gap leads.
    """
    key='apple_app_store'; family='app_review'; supports_outreach=False
    ENDPOINT='https://itunes.apple.com/search'
    def __init__(self,terms,country='us',owner_user_id=None,limit_per_term=200,rating_max=3.5,stale_days=365,timeout=12,min_request_interval_seconds=3.2):
        terms=[str(x).strip() for x in (terms or []) if str(x).strip()]
        if not terms: raise ValueError('APPLE_APP_STORE_TERMS_REQUIRED')
        # No BORIS term-count cap. Apple Search API documents 1..200 results/request and
        # approximately 20 requests/minute; pacing below is the external-provider guard.
        self.terms=terms; self.country=str(country or 'us').lower(); self.owner=_owner(owner_user_id)
        self.limit=max(1,min(200,int(limit_per_term))); self.rating_max=float(rating_max); self.stale_days=max(90,int(stale_days)); self.timeout=timeout
        self.min_request_interval=max(0.0,float(min_request_interval_seconds)); self._last_request_at=None
    def _search(self,term):
        if self._last_request_at is not None and self.min_request_interval>0:
            wait=self.min_request_interval-(time.monotonic()-self._last_request_at)
            if wait>0: time.sleep(wait)
        qs=urllib.parse.urlencode({'term':term,'country':self.country,'entity':'software','limit':self.limit})
        req=urllib.request.Request(self.ENDPOINT+'?'+qs,headers={'User-Agent':'BORIS-Lead-Radar/1.0'})
        try:
            with urllib.request.urlopen(req,timeout=self.timeout) as r: return json.loads(r.read())
        finally:
            self._last_request_at=time.monotonic()
    def fetch(self,since):
        now=datetime.now(timezone.utc); out=[]; seen=set()
        for term in self.terms:
            data=self._search(term)
            for x in data.get('results',[]):
                app_id=str(x.get('trackId') or '')
                if not app_id or app_id in seen: continue
                seen.add(app_id)
                rating=float(x.get('averageUserRating') or 0.0); ratings=int(x.get('userRatingCount') or 0)
                version_dt=_parse_dt(str(x.get('currentVersionReleaseDate') or x.get('releaseDate') or ''))
                stale_days=((now-version_dt).days if version_dt else None)
                low=ratings>=10 and rating>0 and rating<=self.rating_max
                stale=stale_days is not None and stale_days>=self.stale_days
                if not (low or stale): continue
                problems=[]
                if low: problems.append(f'рейтинг {rating:.1f}/5 при {ratings} оценках')
                if stale: problems.append(f'приложение не обновлялось около {stale_days} дней')
                name=str(x.get('trackName') or 'мобильное приложение'); seller=str(x.get('sellerName') or x.get('artistName') or '')
                title=f'Проблемы мобильного приложения {name}: '+', '.join(problems)
                body=(f'Компания: {seller}. Приложение: {name}. '+'. '.join(problems)+'. '
                      f'Версия: {x.get("version") or ""}. Категория: {x.get("primaryGenreName") or ""}. '
                      'Сигнал для аудита мобильного приложения: исправление ошибок, UX, развитие функций или новая версия.')
                out.append(RawLead(source=self.key,external_id=app_id,url=str(x.get('trackViewUrl') or ''),title=title,text=body,
                    published_at=now,source_family=self.family,company_name=seller,owner_user_id=self.owner,
                    metadata={'connector':'apple_itunes_search','search_term':term,'rating':rating,'rating_count':ratings,
                              'version':x.get('version'),'current_version_release_date':version_dt.isoformat() if version_dt else None,
                              'evidence_stale_days':stale_days,'bundle_id':x.get('bundleId')}))
        return out


class AppleCustomerReviewsConnector(SourceConnector):
    """Fresh low-rating App Store reviews for apps already discovered by BORIS.

    Read-only. Reuses app IDs already stored by apple_app_store, rotates a bounded
    batch every hour, and never treats discovery time as review time.
    """
    key='reviews'; family='reviews'; group_key='reviews'; supports_outreach=False; outreach_idempotent=False
    def __init__(self,database_url,country='us',owner_user_id=None,max_apps_per_cycle=6,
                 rating_max=2,min_request_interval_seconds=1.0,timeout=15):
        if not database_url: raise ValueError('BORIS_DATABASE_URL_REQUIRED')
        self.owner=_owner(owner_user_id); self.country=str(country or 'us').lower()
        self.max_apps=max(1,min(25,int(max_apps_per_cycle))); self.rating_max=max(1,min(3,int(rating_max)))
        self.min_request_interval=max(0.0,float(min_request_interval_seconds)); self.timeout=max(5,int(timeout))
        self.db=create_engine(database_url,pool_pre_ping=True); self._last_request_at=None
    def _get(self,url):
        if self._last_request_at is not None and self.min_request_interval>0:
            wait=self.min_request_interval-(time.monotonic()-self._last_request_at)
            if wait>0: time.sleep(wait)
        req=urllib.request.Request(url,headers={'User-Agent':'BORIS-Lead-Radar/1.0','Accept':'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=self.timeout) as r: return json.loads(r.read())
        finally:
            self._last_request_at=time.monotonic()
    def _apps(self):
        sql=sa_text("""
          SELECT external_id,title,company_name,url,metadata_json
          FROM lead_radar_leads
          WHERE owner_user_id=:owner AND source_key='apple_app_store'
            AND COALESCE(external_id,'')<>''
            AND status NOT IN ('rejected','stale')
          ORDER BY id
        """)
        with self.db.connect() as c:
            rows=[dict(x) for x in c.execute(sql,{'owner':self.owner}).mappings()]
        if not rows: return []
        def evidence(row):
            meta=row.get('metadata_json') or {}
            try: rating=float(meta.get('rating') or 99)
            except Exception: rating=99
            try: count=int(meta.get('rating_count') or 0)
            except Exception: count=0
            return rating,count
        # Prioritize apps whose aggregate rating already proves a product problem.
        # High review volume makes the review lane both fresher and more commercially meaningful.
        low=[r for r in rows if evidence(r)[0] <= 3.5 and evidence(r)[1] >= 25]
        pool=low or rows
        pool=sorted(pool,key=lambda r:(-evidence(r)[1],evidence(r)[0],str(r.get('external_id') or '')))
        return pool[:self.max_apps]
    def fetch(self,since):
        out=[]; seen=set()
        for app in self._apps():
            app_id=str(app.get('external_id') or '').strip()
            if not app_id: continue
            url=(f'https://itunes.apple.com/{urllib.parse.quote(self.country)}/rss/customerreviews/'
                 f'id={urllib.parse.quote(app_id)}/sortBy=mostRecent/json')
            data=self._get(url)
            entries=((data.get('feed') or {}).get('entry') or [])
            if isinstance(entries,dict): entries=[entries]
            for entry in entries:
                if not isinstance(entry,dict): continue
                rating_raw=((entry.get('im:rating') or {}).get('label') or '')
                try: rating=int(rating_raw)
                except Exception: continue
                if rating<1 or rating>self.rating_max: continue
                updated=_parse_dt((entry.get('updated') or {}).get('label'))
                if updated is None or (since and updated<since): continue
                review_id=str((entry.get('id') or {}).get('label') or '').strip()
                title=str((entry.get('title') or {}).get('label') or 'Негативный отзыв').strip()
                body=str((entry.get('content') or {}).get('label') or '').strip()
                if len(body)<8: continue
                stable=hashlib.sha256(f'{app_id}|{review_id}|{updated.isoformat()}|{title}|{body}'.encode('utf-8')).hexdigest()
                if stable in seen: continue
                seen.add(stable)
                meta=app.get('metadata_json') or {}
                app_name=str(meta.get('track_name') or '').strip()
                if not app_name:
                    # Production apple source title starts with the app issue; keep a bounded readable fallback.
                    app_name=str(app.get('title') or f'App {app_id}')[:180]
                company=str(app.get('company_name') or '')
                out.append(RawLead(
                    source=self.key,external_id=stable,url=str(app.get('url') or ''),
                    title=f'{app_name}: отзыв {rating}/5 — {title}'[:500],
                    text=(f'Мобильное приложение. Реальный отзыв пользователя, оценка {rating}/5. {body}')[:5000],
                    published_at=updated,source_family=self.family,company_name=company,
                    owner_user_id=self.owner,
                    metadata={'connector':'apple_customer_reviews','app_id':app_id,'rating':rating,
                              'review_id':review_id,'review_updated_at':updated.isoformat(),'read_only':True}
                ))
        return out



def _pick_text(value):
    """TED and similar public APIs may return multilingual values as strings/lists/dicts."""
    if value is None: return ''
    if isinstance(value,str): return value.strip()
    if isinstance(value,list):
        for x in value:
            t=_pick_text(x)
            if t: return t
        return ''
    if isinstance(value,dict):
        for k in ('eng','en','rus','ru'):
            if k in value:
                t=_pick_text(value[k])
                if t: return t
        for x in value.values():
            t=_pick_text(x)
            if t: return t
    return str(value).strip()


class TEDTenderConnector(SourceConnector):
    """Official EU TED Search API v3, anonymous published-notice search.

    Read-only. No business contact is performed through TED itself.
    Uses ITERATION mode so BORIS does not impose its own result-count cap.
    """
    key='eu_ted'; family='tender'; group_key='tenders'; supports_outreach=False
    ENDPOINT='https://api.ted.europa.eu/v3/notices/search'
    DEFAULT_TERMS=('software development','web application','mobile application','information technology','CRM','artificial intelligence','automation')
    FIELDS=('publication-number','publication-date','notice-title','buyer-name','buyer-country','classification-cpv','deadline')
    def __init__(self,owner_user_id=None,terms=None,scope='ACTIVE',timeout=20,page_size=250):
        self.owner=_owner(owner_user_id); self.timeout=int(timeout); self.scope=str(scope or 'ACTIVE').upper()
        self.page_size=max(1,min(250,int(page_size)))
        terms=[str(x).strip() for x in (terms or self.DEFAULT_TERMS) if str(x).strip()]
        if not terms: raise ValueError('TED_TERMS_REQUIRED')
        self.terms=terms
    def _post(self,payload):
        data=json.dumps(payload,ensure_ascii=False).encode('utf-8')
        req=urllib.request.Request(self.ENDPOINT,data=data,headers={'User-Agent':'BORIS-Lead-Radar/1.0','Content-Type':'application/json'},method='POST')
        with urllib.request.urlopen(req,timeout=self.timeout) as r: return json.loads(r.read())
    @staticmethod
    def _query(term,since):
        safe=str(term).replace('"',' ').strip()
        parts=[f'FT~"{safe}"']
        if since:
            if getattr(since,'tzinfo',None) is None: since=since.replace(tzinfo=timezone.utc)
            parts.append('PD>='+since.astimezone(timezone.utc).strftime('%Y%m%d'))
        return ' AND '.join(parts)+' SORT BY publication-date DESC'
    def fetch(self,since):
        out=[]; seen=set()
        for term in self.terms:
            token=None
            while True:
                body={'query':self._query(term,since),'fields':list(self.FIELDS),'limit':self.page_size,'scope':self.scope,
                      'checkQuerySyntax':False,'paginationMode':'ITERATION'}
                if token: body['iterationNextToken']=token
                data=self._post(body)
                if data.get('timedOut'): raise RuntimeError('TED_SEARCH_TIMED_OUT')
                notices=data.get('notices') or data.get('results') or []
                if not isinstance(notices,list): raise RuntimeError('TED_SEARCH_RESPONSE_INVALID')
                for x in notices:
                    if not isinstance(x,dict): continue
                    pub=str(x.get('publication-number') or x.get('publicationNumber') or x.get('notice-id') or '')
                    if not pub or pub in seen: continue
                    published=_parse_dt(_pick_text(x.get('publication-date') or x.get('publicationDate')))
                    if published is None: continue
                    if since and published<since: continue
                    seen.add(pub)
                    title=_pick_text(x.get('notice-title') or x.get('title'))
                    buyer=_pick_text(x.get('buyer-name') or x.get('buyerName'))
                    country=_pick_text(x.get('buyer-country') or x.get('buyerCountry'))
                    cpv=_pick_text(x.get('classification-cpv') or x.get('cpv'))
                    deadline=_pick_text(x.get('deadline'))
                    body_text=' '.join(z for z in [title, f'Заказчик: {buyer}' if buyer else '', f'Страна: {country}' if country else '', f'CPV: {cpv}' if cpv else '', f'Дедлайн: {deadline}' if deadline else ''] if z)
                    out.append(RawLead(source=self.key,external_id=pub,
                        url=f'https://ted.europa.eu/en/notice/-/detail/{urllib.parse.quote(pub)}',title=title or f'TED procurement {pub}',text=body_text,
                        published_at=published,source_family=self.family,company_name=buyer,location=country,owner_user_id=self.owner,
                        metadata={'connector':'ted_search_api_v3','search_term':term,'cpv':cpv,'deadline':deadline,'scope':self.scope}))
                token=data.get('iterationNextToken')
                if not token or not notices: break
        return out


class GitHubIssuesConnector(SourceConnector):
    """GitHub public issue search, read-only lead signal.

    AUTO outreach through GitHub is intentionally unsupported. Queries are explicit deployment config.
    GitHub's own search-result/rate limits are respected; BORIS adds no daily lead cap.
    To avoid polluting CRM with ordinary bug reports, a result must contain an explicit
    commercial/hiring intent phrase before it becomes a RawLead.
    """
    key='github_issues'; family='partner'; group_key='developers'; supports_outreach=False
    ENDPOINT='https://api.github.com/search/issues'
    INTENT_PHRASES=(
        'looking for a developer','looking for developer','need a developer','need developer',
        'hire a developer','hiring a developer','hiring developer','developer wanted',
        'seeking developer','contract developer','paid bounty','bounty available',
        'freelancer wanted','seeking freelancer','looking for freelancer','need freelancer',
        'paid task','paid work','contract work',
    )
    def __init__(self,queries,owner_user_id=None,token=None,timeout=15):
        queries=[str(x).strip() for x in (queries or []) if str(x).strip()]
        if not queries: raise ValueError('GITHUB_ISSUE_QUERIES_REQUIRED')
        self.queries=queries; self.owner=_owner(owner_user_id); self.token=str(token or '').strip(); self.timeout=int(timeout)
    def _get(self,params):
        url=self.ENDPOINT+'?'+urllib.parse.urlencode(params)
        headers={'User-Agent':'BORIS-Lead-Radar/1.0','Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'}
        if self.token: headers['Authorization']='Bearer '+self.token
        req=urllib.request.Request(url,headers=headers)
        last=None
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req,timeout=self.timeout) as r:
                    return json.loads(r.read())
            except Exception as exc:
                last=exc
                code=getattr(exc,'code',None)
                msg=str(exc).lower()
                transient=(code in (502,503,504) or 'timed out' in msg or 'timeout' in msg or 'connection reset' in msg)
                if attempt>=1 or not transient:
                    raise
                time.sleep(0.5)
        raise last
    def fetch(self,since):
        out=[]; seen=set()
        since_date=(since.astimezone(timezone.utc).strftime('%Y-%m-%d') if since else None)
        for base_query in self.queries:
            q=base_query
            if 'is:issue' not in q: q+=' is:issue'
            if since_date and 'created:' not in q and 'updated:' not in q: q+=f' created:>={since_date}'
            page=1
            while True:
                data=self._get({'q':q,'sort':'created','order':'desc','per_page':100,'page':page})
                items=data.get('items') or []
                if not isinstance(items,list): raise RuntimeError('GITHUB_SEARCH_RESPONSE_INVALID')
                for x in items:
                    if x.get('pull_request'): continue
                    iid=str(x.get('id') or '')
                    if not iid or iid in seen: continue
                    published=_parse_dt(str(x.get('created_at') or ''))
                    if published is None: continue
                    if since and published<since: continue
                    seen.add(iid)
                    repo_url=str(x.get('repository_url') or '')
                    repo_name=repo_url.rsplit('/',1)[-1] if repo_url else ''
                    user=x.get('user') or {}
                    title=str(x.get('title') or '')
                    body=str(x.get('body') or '')
                    title_hay=(' '+title+' ').lower()
                    body_hay=(' '+body+' ').lower()
                    title_intent=any(p in title_hay for p in self.INTENT_PHRASES)
                    # Body-only matches are accepted only when a commercial marker is close
                    # to the explicit hiring/contract phrase. This avoids technical words such
                    # as rate/payment appearing elsewhere in long issue templates.
                    commercial_markers=('budget','compensation','paid work','paid task','paid project','fixed price','hourly rate','bounty')
                    body_intent_commercial=False
                    for phrase in self.INTENT_PHRASES:
                        start=0
                        while True:
                            pos=body_hay.find(phrase,start)
                            if pos<0: break
                            window=body_hay[max(0,pos-260):pos+len(phrase)+260]
                            money_near=bool(re.search(r'(?:[$€£]\s?\d|\d[\d,. ]*\s?(?:usd|eur|rub|₽))',window))
                            if money_near or any(m in window for m in commercial_markers):
                                body_intent_commercial=True
                                break
                            start=pos+len(phrase)
                        if body_intent_commercial:
                            break
                    if not title_intent and not body_intent_commercial:
                        continue
                    out.append(RawLead(source=self.key,external_id=iid,url=str(x.get('html_url') or ''),title=title,text=body,
                        published_at=published,source_family=self.family,author=str(user.get('login') or ''),company_name=repo_name,
                        owner_user_id=self.owner,metadata={'connector':'github_search_issues','repository_url':repo_url,'comments':x.get('comments'),'search_query':base_query}))
                # GitHub Search API exposes at most the provider-defined searchable result window.
                if not items or len(items)<100 or page>=10: break
                page+=1
        return out


class _BorisDatabaseConnector(SourceConnector):
    """Read-only connector over existing BORIS production data.

    It never creates a second CRM/brain and never writes to the source tables.
    """
    supports_outreach=False
    outreach_idempotent=False
    def __init__(self,database_url,owner_user_id=None):
        if not database_url: raise ValueError('BORIS_DATABASE_URL_REQUIRED')
        self.owner=_owner(owner_user_id)
        self.db=create_engine(database_url,pool_pre_ping=True)

class BorisTelegramHotLeadConnector(_BorisDatabaseConnector):
    key='boris_telegram'; family='social'; group_key='telegram'
    DEV_RULE_ID='aaaaaaaa-0000-0000-0000-000000000007'
    def fetch(self,since):
        sql=sa_text("""
          SELECT m.id,m.external_message_id,m.author_name,m.author_username,m.text,m.intent,
                 m.score_total,m.posted_at,m.collected_at,m.url,m.matched_rule_id,m.matched_rules,
                 s.username AS source_username,s.title AS source_title
          FROM monitor_messages m
          JOIN monitor_sources s ON s.id=m.source_id
          WHERE m.platform='telegram'
            AND COALESCE(m.posted_at,m.collected_at) >= :since
            AND COALESCE(trim(m.text),'')<>''
            AND (
              m.matched_rule_id::text=:rule_id
              OR COALESCE(m.matched_rules::text,'') LIKE :rule_like
            )
          ORDER BY COALESCE(m.posted_at,m.collected_at) DESC
          LIMIT 1000
        """)
        out=[]
        with self.db.connect() as c:
            for x in c.execute(sql,{'since':since,'rule_id':self.DEV_RULE_ID,'rule_like':'%'+self.DEV_RULE_ID+'%'}).mappings():
                published=x.get('posted_at') or x.get('collected_at')
                if not published: continue
                username=str(x.get('source_username') or '').strip().lstrip('@')
                mid=str(x.get('external_message_id') or '')
                url=str(x.get('url') or '')
                if not url and username:
                    url='https://t.me/'+username+('/'+mid if mid.isdigit() else '')
                out.append(RawLead(
                    source=self.key,external_id=str(x.get('id')),url=url,
                    title=str(x.get('source_title') or 'Telegram: запрос на разработку'),
                    text=str(x.get('text') or ''),published_at=published,source_family=self.family,
                    author=str(x.get('author_username') or x.get('author_name') or ''),
                    contact_hint=str(x.get('author_username') or ''),owner_user_id=self.owner,
                    metadata={'monitor_message_id':str(x.get('id')),'monitor_intent':x.get('intent'),
                              'monitor_score':x.get('score_total'),'dev_rule_id':self.DEV_RULE_ID}
                ))
        return out

class BorisCRMCompaniesConnector(_BorisDatabaseConnector):
    key='boris_crm_companies'; family='audit'; group_key='crm_companies'
    def fetch(self,since):
        sql=sa_text("""
          SELECT id,name,website,source,source_ref,created_at,updated_at
          FROM boris_crm_companies
          WHERE owner_user_id=:owner
            AND COALESCE(source,'')<>'lead_radar'
            AND COALESCE(updated_at,created_at) >= :since
          ORDER BY COALESCE(updated_at,created_at) DESC
          LIMIT 500
        """)
        out=[]
        with self.db.connect() as c:
            for x in c.execute(sql,{'owner':self.owner,'since':since}).mappings():
                published=x.get('updated_at') or x.get('created_at')
                if not published: continue
                url=str(x.get('website') or '')
                out.append(RawLead(
                    source=self.key,external_id=str(x.get('id')),url=url,
                    title=str(x.get('name') or 'Компания из BORIS CRM'),
                    text=' '.join(str(v or '') for v in (x.get('name'),x.get('website'),x.get('source_ref'))),
                    published_at=published,source_family=self.family,company_name=str(x.get('name') or ''),
                    company_domain=(urllib.parse.urlparse(url if '://' in url else 'https://'+url).hostname or '') if url else '',
                    owner_user_id=self.owner,metadata={'crm_company_id':x.get('id'),'crm_source':x.get('source')}
                ))
        return out

class BorisCompanySitesConnector(_BorisDatabaseConnector):
    """Official company-site discoveries already proven by BORIS web discovery.

    Read-only: consumes only owner-scoped prospect_companies rows whose discovery
    query explicitly targeted an official/company site. This is not a second
    crawler and never contacts the company automatically.
    """
    key='boris_company_sites'; family='audit'; group_key='company_sites'
    supports_outreach=False; outreach_idempotent=False

    def fetch(self,since):
        sql=sa_text("""
          SELECT id,name,city,website,domain,status,source,source_url,discovery_provider,
                 discovered_at,search_query,discovery_score
          FROM prospect_companies
          WHERE owner_id=:owner
            AND discovered_at IS NOT NULL AND discovered_at >= :since
            AND COALESCE(trim(website),'')<>''
            AND (
              lower(COALESCE(search_query,'')) LIKE '%официальный сайт%'
              OR lower(COALESCE(search_query,'')) LIKE '%company site%'
              OR COALESCE(source,'') IN ('web_seed','open_web_curated','web_discovery')
            )
          ORDER BY discovered_at DESC
          LIMIT 1000
        """)
        out=[]
        with self.db.connect() as c:
            for x in c.execute(sql,{'owner':self.owner,'since':since}).mappings():
                url=str(x.get('website') or '').strip()
                published=x.get('discovered_at')
                if not url or not published:
                    continue
                host=(urllib.parse.urlparse(url if '://' in url else 'https://'+url).hostname or '').lower()
                if not host:
                    continue
                out.append(RawLead(
                    source=self.key,external_id=str(x.get('id')),url=url,
                    title=str(x.get('name') or host),text=str(x.get('search_query') or ''),
                    published_at=published,source_family=self.family,
                    company_name=str(x.get('name') or ''),company_domain=host,
                    location=str(x.get('city') or ''),owner_user_id=self.owner,
                    metadata={
                        'prospect_company_id':x.get('id'),
                        'discovery_provider':x.get('discovery_provider'),
                        'discovery_score':float(x.get('discovery_score') or 0),
                        'source_url':x.get('source_url'),
                        'evidence':'official_company_site_discovery',
                    }
                ))
        return out


class BorisColdB2BConnector(_BorisDatabaseConnector):
    key='boris_cold_b2b'; family='audit'; group_key='cold_b2b'
    TECH_TERMS=(
        'crm','срм','автоматизац','1с','1c',' api ','api ','интеграц','telegram бот','телеграм бот',
        'чатбот','чат-бот','приложен','android','ios','flutter','react native','excel','парсер','парсинг',
        'мониторинг цен','xml','yml','дашборд','bi ','devops','коллтрекинг','телефони','seo автоматизац'
    )
    def fetch(self,since):
        sql=sa_text("""
          SELECT id,name,city,website,domain,status,source,source_url,discovery_provider,
                 discovered_at,search_query,discovery_score
          FROM prospect_companies
          WHERE owner_id=:owner
            AND discovered_at IS NOT NULL AND discovered_at >= :since
            AND COALESCE(trim(website),'')<>''
          ORDER BY discovered_at DESC
          LIMIT 500
        """)
        out=[]
        with self.db.connect() as c:
            for x in c.execute(sql,{'owner':self.owner,'since':since}).mappings():
                query=str(x.get('search_query') or '')
                # "официальный сайт" is discovery plumbing, never evidence that a company needs a website.
                evidence=query.lower().replace('официальный сайт',' ')
                if not any(t in (' '+evidence+' ') for t in self.TECH_TERMS):
                    continue
                out.append(RawLead(
                    source=self.key,external_id=str(x.get('id')),url=str(x.get('website') or ''),
                    title=str(x.get('name') or 'Cold B2B компания'),text=evidence,
                    published_at=x.get('discovered_at'),source_family=self.family,
                    company_name=str(x.get('name') or ''),company_domain=str(x.get('domain') or ''),
                    location=str(x.get('city') or ''),owner_user_id=self.owner,
                    metadata={'prospect_company_id':x.get('id'),'discovery_provider':x.get('discovery_provider'),
                              'discovery_score':float(x.get('discovery_score') or 0),'source_url':x.get('source_url')}
                ))
        return out


# FREELANCER_ACTIVE_PROJECTS_V1
class FreelancerProjectsConnector(SourceConnector):
    """Freelancer.com official active-project API, read-only.

    The active-project endpoint is used only for discovery. Production live
    preflight on 2026-09-06 returned HTTP 200 without OAuth; an optional OAuth
    token is still supported if configured. This connector never places bids
    and never sends Freelancer messages.

    BORIS does not impose a daily lead cap. Provider cadence is controlled by
    source policy/backoff and pagination follows the provider result set.
    """
    key='freelancer'; family='brief'; group_key='freelance'
    supports_outreach=False
    # First successful bootstrap should see a useful recent pool without
    # crawling thousands of stale active rows. Normal scans overlap heavily
    # so short provider/network outages do not lose newly posted projects.
    bootstrap_lookback_seconds=24*3600
    incremental_overlap_seconds=6*3600
    ENDPOINT='https://www.freelancer.com/api/projects/0.1/projects/active/'
    DEFAULT_QUERIES=(
        'software development','web development','mobile app','python','automation',
        'api integration','crm','telegram bot','scraper','artificial intelligence',
    )

    def __init__(self,queries=None,owner_user_id=None,oauth_token=None,timeout=15,page_size=100):
        queries=[str(x).strip() for x in (queries or self.DEFAULT_QUERIES) if str(x).strip()]
        if not queries:
            raise ValueError('FREELANCER_QUERIES_REQUIRED')
        self.queries=queries
        self.owner=_owner(owner_user_id)
        self.oauth_token=str(oauth_token or '').strip()
        self.timeout=int(timeout)
        self.page_size=max(1,min(100,int(page_size)))

    def _get(self,params):
        url=self.ENDPOINT+'?'+urllib.parse.urlencode(params)
        headers={'User-Agent':'BORIS-Lead-Radar/1.0','Accept':'application/json'}
        if self.oauth_token:
            headers['Freelancer-OAuth-V1']=self.oauth_token
        req=urllib.request.Request(url,headers=headers)
        last=None
        # A single slow Freelancer response must not poison all nine search
        # queries and force the entire source into a long provider backoff.
        # Retry only transport timeouts; auth/provider/schema errors remain
        # fail-fast and visible to source health.
        for attempt,timeout in enumerate((self.timeout,max(30,self.timeout*2)),1):
            try:
                with urllib.request.urlopen(req,timeout=timeout) as r:
                    return json.loads(r.read())
            except Exception as exc:
                last=exc
                if attempt>=2 or 'timed out' not in str(exc).lower():
                    raise
        raise last

    @staticmethod
    def _published(value):
        try:
            ts=int(float(value or 0))
        except Exception:
            return None
        if ts<=0:
            return None
        try:
            return datetime.fromtimestamp(ts,tz=timezone.utc)
        except Exception:
            return None

    @staticmethod
    def _project_url(project):
        seo=str(project.get('seo_url') or '').strip().lstrip('/')
        if seo:
            if seo.startswith('http://') or seo.startswith('https://'):
                return seo
            return 'https://www.freelancer.com/projects/'+seo
        pid=str(project.get('id') or '').strip()
        return ('https://www.freelancer.com/projects/'+pid) if pid else ''

    @staticmethod
    def _skill_names(project):
        out=[]
        for row in project.get('jobs') or []:
            if isinstance(row,dict):
                name=str(row.get('name') or row.get('seo_url') or '').strip()
            else:
                name=str(row or '').strip()
            if name:
                out.append(name)
        return out

    def fetch(self,since):
        out=[]; seen=set()
        for query in self.queries:
            offset=0
            while True:
                params={
                    'query':query,
                    'sort_field':'time_updated',
                    'limit':self.page_size,
                    'offset':offset,
                    'full_description':'true',
                    'job_details':'true',
                }
                if since:
                    params['from_time']=int(since.timestamp())
                data=self._get(params)
                if not isinstance(data,dict) or data.get('status') not in (None,'success'):
                    raise RuntimeError('FREELANCER_API_RESPONSE_INVALID')
                result=data.get('result') or {}
                projects=result.get('projects') or []
                if not isinstance(projects,list):
                    raise RuntimeError('FREELANCER_PROJECTS_RESPONSE_INVALID')
                # Endpoint is requested with sort_field=time_updated descending.
                # Once every row on a page has a parseable time_updated older than
                # our freshness boundary, all following pages are older too.
                # Stop there instead of crawling the provider's full historical
                # total_count for every broad query.
                if since and projects:
                    updated=[self._published(x.get('time_updated')) for x in projects if isinstance(x,dict)]
                    if len(updated)==len(projects) and all(t is not None and t<since for t in updated):
                        break
                for x in projects:
                    if not isinstance(x,dict):
                        continue
                    pid=str(x.get('id') or '').strip()
                    if not pid or pid in seen:
                        continue
                    published=self._published(x.get('submitdate'))
                    if published is None:
                        continue
                    if since and published<since:
                        continue
                    seen.add(pid)
                    title=str(x.get('title') or '').strip()
                    description=str(x.get('description') or x.get('preview_description') or '').strip()
                    currency=x.get('currency') or {}
                    budget=x.get('budget') or {}
                    bid_stats=x.get('bid_stats') or {}
                    skills=self._skill_names(x)
                    budget_text=''
                    if isinstance(budget,dict):
                        bmin=budget.get('minimum'); bmax=budget.get('maximum')
                        code=(currency.get('code') if isinstance(currency,dict) else None) or ''
                        if bmin is not None or bmax is not None:
                            budget_text=f'Budget: {bmin if bmin is not None else "?"}-{bmax if bmax is not None else "?"} {code}.'
                    body=(' '.join(z for z in [
                        description,
                        ('Skills: '+', '.join(skills)+'.') if skills else '',
                        budget_text,
                    ] if z)).strip()
                    out.append(RawLead(
                        source=self.key,external_id=pid,url=self._project_url(x),title=title,text=body,
                        published_at=published,source_family=self.family,owner_user_id=self.owner,
                        metadata={
                            'connector':'freelancer_active_projects',
                            'search_query':query,
                            'project_type':x.get('type'),
                            'budget':budget if isinstance(budget,dict) else {},
                            'currency':currency if isinstance(currency,dict) else {},
                            'bid_stats':bid_stats if isinstance(bid_stats,dict) else {},
                            'skills':skills,
                            'time_updated':x.get('time_updated'),
                        }
                    ))
                total=result.get('total_count')
                offset+=len(projects)
                if not projects or len(projects)<self.page_size:
                    break
                try:
                    if total is not None and offset>=int(total):
                        break
                except Exception as _suppressed_exc:
                    observe_suppressed(__name__, _suppressed_exc, line=1130)
        return out
