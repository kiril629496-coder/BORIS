import re
from functools import lru_cache
from html import unescape
from .catalog import SERVICE_CATALOG

def normalize(s: str) -> str:
    raw=unescape(str(s or ''))
    # Search terms are connector metadata, not customer demand. Older TED rows
    # stored this marker in the body, so strip it during requalification too.
    raw=re.sub(r'(?i)поисковый сигнал:\s*[^\n]+$', ' ', raw)
    # RSS/Atom content can contain HTML attributes and tracking URLs. They are not
    # semantic demand evidence and short tokens inside hashes caused false matches.
    raw=re.sub(r'<[^>]+>',' ',raw,flags=re.S)
    raw=re.sub(r'https?://\S+',' ',raw,flags=re.I)
    return re.sub(r'\s+',' ',re.sub(r'[^a-zа-яё0-9+.# -]+',' ',raw.lower())).strip()

_RU_SUFFIXES=tuple(sorted({
    'иями','ями','ами','ого','его','ому','ему','ыми','ими','ией','иям','ием','иях',
    'ую','юю','ая','яя','ое','ее','ые','ие','ых','их','ый','ий','ой',
    'ев','ов','ей','ам','ям','ах','ях','ом','ем','ою','ею','ию','ью','ия','ья',
    'а','я','у','ю','ы','и','е','о','ь'
},key=len,reverse=True))

@lru_cache(maxsize=8192)
def _ru_stem(word: str) -> str:
    w=str(word or '').lower().replace('ё','е')
    if not re.fullmatch(r'[а-я]+',w) or len(w)<4:
        return w
    for suf in _RU_SUFFIXES:
        if w.endswith(suf) and len(w)-len(suf)>=3:
            return w[:-len(suf)]
    return w

def _edit_distance_le1(a: str,b: str) -> bool:
    if a==b: return True
    if abs(len(a)-len(b))>1: return False
    if len(a)==len(b):
        return sum(x!=y for x,y in zip(a,b))<=1
    if len(a)>len(b): a,b=b,a
    i=j=miss=0
    while i<len(a) and j<len(b):
        if a[i]==b[j]:
            i+=1; j+=1
        else:
            miss+=1; j+=1
            if miss>1: return False
    return True

@lru_cache(maxsize=65536)
def _token_equiv(a: str,b: str) -> bool:
    if a==b: return True
    if re.fullmatch(r'[а-яё]+',a,re.I) and re.fullmatch(r'[а-яё]+',b,re.I):
        sa,sb=_ru_stem(a),_ru_stem(b)
        if sa==sb: return True
        # Fuzzy typo tolerance is useful only for longer Russian words. On short
        # stems it created dangerous semantic collisions such as "класс" ~= "касса",
        # which misclassified fire-safety building projects as payment/cash-register work.
        if min(len(sa),len(sb))>=4 and sa[:2]==sb[:2] and _edit_distance_le1(sa,sb): return True
    # Small English plural variation only; technical tokens remain exact.
    if re.fullmatch(r'[a-z]{5,}',a,re.I) and re.fullmatch(r'[a-z]{5,}',b,re.I):
        return a.rstrip('s')==b.rstrip('s')
    return False

@lru_cache(maxsize=8192)
def _normalized_keyword(keyword: str) -> str:
    return normalize(keyword)

def _keyword_match(text: str, keyword: str, tt=None) -> bool:
    k=_normalized_keyword(str(keyword or ''))
    if not k:
        return False
    kt=k.split(); tt=tt if tt is not None else text.split()
    # Short technical tokens (1c, 1с, BI, API, iOS, amo...) must be exact whole tokens.
    if len(kt)==1 and len(k)<=4:
        return k in tt
    if k in text:
        return True
    if len(kt)>len(tt):
        return False
    # First try phrase order.
    for i in range(0,len(tt)-len(kt)+1):
        if all(_token_equiv(kw,tw) for kw,tw in zip(kt,tt[i:i+len(kt)])):
            return True
    # Natural language can reverse adjective/noun order: "карьерного песка" /
    # "песок карьерный". For short catalog phrases allow a compact unordered window.
    if 2 <= len(kt) <= 4:
        width=min(len(tt),len(kt)+2)
        for i in range(0,len(tt)-width+1):
            window=tt[i:i+width]; used=set(); ok=True
            for kw in kt:
                hit=None
                for j,tw in enumerate(window):
                    if j not in used and _token_equiv(kw,tw):
                        hit=j; break
                if hit is None:
                    ok=False; break
                used.add(hit)
            if ok:
                return True
    return False

EXTRA = {
    2:['сайт нужен','разработка сайта','сделать сайт'],
    5:['mvp','веб приложение','web app','веб платформа','web platform','saas платформа'],
    7:['android developer','android разработчик'],
    8:['ios developer','ios разработчик'],
    9:['flutter developer','flutter разработчик','react native developer','мобильное приложение','мобильного приложения','mobile app','need an app','need app'],
    10:['нужен бот','бот для телеграм','telegram developer','telegram bot','telegram chatbot'],
    14:['срм с нуля','crm система'],
    15:['bitrix','битрикс','amo','amocrm','retailcrm','hubspot','salesforce'],
    16:['1с программист','1c developer','1с разработчик'],
    17:['интеграция','webhooks'],
    20:['нужен парсер','сбор данных','web scraping'],
    26:['ml engineer','machine learning','нейросеть','llm','ai automation','process automation','workflow automation','automation script','python automation','business process automation'],
    35:['devops engineer','sre','kubernetes','docker','ci/cd'],
}

# Неявная бизнес-потребность: клиент ещё не написал ТЗ, но проблема уже видна.
# Это один из главных источников Lead Radar — продаём решение процесса, а не слово 'программирование'.
BUSINESS_GAP = {
    33:['нет личного кабинета','личного кабинета нет','нет админки','нет админ панели'],
    26:['заказы вручную','заявки вручную','обрабатываем вручную','ручной процесс','вручную переносим'],
    24:['переносим в excel','excel вручную','из excel','в эксель'],
    15:['нет интеграции crm','crm не связана','сайт не связан с crm','нет crm'],
    17:['нет интеграции','системы не связаны','нужно связать системы'],
    34:['устаревший сайт','медленный сайт','битые страницы','плохой сайт','давно не обновлялся'],
    7:['нет android приложения'],
    8:['нет ios приложения'],
    9:['нет приложения','мобильного приложения нет'],
}


def _catalog_rows(catalog=None):
    if catalog is None:
        rows=[]
        for svc in SERVICE_CATALOG:
            row=dict(svc)
            code=int(row['id'])
            row['offering_code']=code
            row['keywords']=list(row.get('keywords') or [])+EXTRA.get(code,[])+BUSINESS_GAP.get(code,[])
            row.setdefault('negative_keywords',[])
            row.setdefault('kind','service')
            rows.append(row)
        return rows
    return [dict(x) for x in catalog]

def classify(text: str,catalog=None) -> list[int]:
    t=normalize(text); tt=t.split(); out=[]
    for svc in _catalog_rows(catalog):
        code=int(svc.get('offering_code') or svc.get('id') or 0)
        if code<=0: continue
        negatives=list(svc.get('negative_keywords') or [])
        if any(_keyword_match(t,k,tt) for k in negatives): continue
        keys=list(svc.get('keywords') or [])
        if any(_keyword_match(t,k,tt) for k in keys): out.append(code)
    return sorted(set(out))

def service_budget(service_ids: list[int],catalog=None):
    wanted=set(int(x) for x in service_ids or [])
    rows=[x for x in _catalog_rows(catalog) if int(x.get('offering_code') or x.get('id') or 0) in wanted]
    los=[x.get('price_min_rub') for x in rows if x.get('price_min_rub') is not None]
    his=[x.get('price_max_rub') for x in rows if x.get('price_max_rub') is not None]
    return (min(los) if los else None, max(his) if his else None)
