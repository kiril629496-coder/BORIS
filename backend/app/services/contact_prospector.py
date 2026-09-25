"""BORIS contact prospector: public business contact extraction from company websites.
No AI calls, no paid provider calls, no login bypass. Reads public pages only.
"""
from __future__ import annotations
import re, time
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

UA={"User-Agent":"Mozilla/5.0 (compatible; BORIS-BusinessResearch/1.0; +https://boris-ai.pro)"}
EMAIL_RE=re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}",re.I)
PHONE_RE=re.compile(r"(?:\+7|8)[\s\-(]*(?:\d[\s\-()]*){10}")
CONTACT_HINTS=("contact","kont","контакт","about","o-kompanii","о-компании")

def _clean_phone(v:str)->str:
    d=re.sub(r"\D","",v)
    if len(d)==11 and d[0]=='8': d='7'+d[1:]
    return '+'+d if len(d)==11 and d[0]=='7' else v.strip()

def _same_host(a,b):
    return (urlparse(a).hostname or '').lower().removeprefix('www.')==(urlparse(b).hostname or '').lower().removeprefix('www.')

def extract_company_contacts(url:str, max_pages:int=4, timeout:int=12)->dict:
    started=time.perf_counter(); queue=[url]; seen=set(); emails=set(); phones=set(); pages=[]; bytes_read=0; title=''
    session=requests.Session(); session.headers.update(UA)
    while queue and len(pages)<max_pages:
        u=queue.pop(0)
        key=u.split('#')[0].rstrip('/')
        if key in seen: continue
        seen.add(key)
        try:
            r=session.get(u,timeout=timeout,allow_redirects=True)
            if r.status_code!=200: continue
            bytes_read+=len(r.content); pages.append(r.url)
            soup=BeautifulSoup(r.text,'lxml')
            if not title:
                title=(soup.title.get_text(' ',strip=True) if soup.title else '')[:180]
            text=soup.get_text(' ',strip=True)
            for e in EMAIL_RE.findall(text): emails.add(e.lower())
            for a in soup.select('a[href^="mailto:"]'):
                e=a.get('href','')[7:].split('?')[0].strip()
                if EMAIL_RE.fullmatch(e): emails.add(e.lower())
            for p in PHONE_RE.findall(text): phones.add(_clean_phone(p))
            for a in soup.select('a[href^="tel:"]'):
                p=a.get('href','')[4:].strip(); cp=_clean_phone(p)
                if cp.startswith('+7') and len(re.sub(r'\D','',cp))==11: phones.add(cp)
            if len(pages)==1:
                for a in soup.find_all('a',href=True):
                    href=a.get('href',''); label=a.get_text(' ',strip=True).lower(); low=href.lower()
                    if any(h in low or h in label for h in CONTACT_HINTS):
                        full=urljoin(r.url,href)
                        if _same_host(r.url,full) and full not in queue: queue.append(full)
        except Exception:
            continue
    return {'url':url,'title':title,'emails':sorted(emails),'phones':sorted(phones),'pages':pages,'pages_count':len(pages),'bytes_read':bytes_read,'elapsed_sec':round(time.perf_counter()-started,3),'paid_api_cost_rub':0.0}
