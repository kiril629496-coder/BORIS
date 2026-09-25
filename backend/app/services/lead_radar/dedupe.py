import hashlib,re
from urllib.parse import urlsplit,urlunsplit

def _norm(s): return re.sub(r'\W+',' ',(s or '').lower()).strip()

def canonical_url(url: str):
    try:
        u=urlsplit(url or '')
        return urlunsplit((u.scheme.lower(),u.netloc.lower(),u.path.rstrip('/'),'',''))
    except Exception: return (url or '').split('?')[0]

def fingerprint(source,external_id,url,title,text):
    base=f'{source}|{external_id}' if external_id else f'{canonical_url(url)}|{_norm(title+" "+text)[:800]}'
    return hashlib.sha256(base.encode()).hexdigest()

def _entity(raw):
    domain=(raw.company_domain or '').lower().strip().removeprefix('https://').removeprefix('http://').split('/')[0].removeprefix('www.')
    if domain: return 'domain:'+domain
    name=_norm(raw.company_name)
    if name: return 'company:'+name
    contact=_norm(raw.contact_hint)
    if contact: return 'contact:'+contact
    return ''

def dedupe_key(raw, service_ids):
    entity=_entity(raw)
    # Для идентифицированной компании один и тот же класс потребности = одна сделка + много сигналов.
    # Не привязываем ключ к формулировке поста/вакансии, иначе Telegram+вакансия создадут две сделки.
    if entity and service_ids:
        base=f'{entity}|service:{service_ids[0]}'
    else:
        intent=_norm(raw.title+' '+raw.text)
        base=f'{entity or _norm(raw.title)[:140]}|{",".join(map(str,service_ids))}|{intent[:320]}'
    return hashlib.sha256(base.encode()).hexdigest()
