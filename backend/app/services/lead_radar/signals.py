import re
from html import unescape
from .models import SignalFamily

MONEY=('raised ','pre-seed','seed round','series a','series b','инвестиц','получил грант','раунд финансирования','финансирован')
LAUNCH=('product launch','launched','запустили продукт','новый сервис','новое приложение','launching platform','beta launch')
PROBLEM=('вручную','не работает','сломал','ошибка','неудобно','переносим в excel','excel вручную','задолбал','теряем заявки','нет интеграции','долго обрабатываем')
HIRING=('вакансия','ищем разработчика','hiring','developer needed','flutter-разработчик','1с программист','devops engineer','ml engineer','react developer','python developer')
READY=('нужен разработчик','ищу разработчика','кто может сделать','нужно сделать','разработать','доработать','заказать разработку','тз','бюджет','срок','looking for developer','need a developer','need developer','looking for agency','need an app','need app','need mvp','need automation','need a website','need website','need a bot','need bot','need crm','need integration')
PARTNER=('white-label','white label','подрядчик','overflow','субподряд','партнёр по разработке','партнер по разработке')
GENERIC_BUY=('нужен','нужна','нужны','нужно','ищу','требуется','куплю','купить','закупка','закупаем','поставка','поставщик','заказать','цена','стоимость','сколько стоит','коммерческое предложение','кп','need','looking for','buy','purchase','supplier','quote','price')
GAP=('нет приложения','нет личного кабинета','нет crm','устаревший сайт','нет онлайн заказа','нет онлайн-заказа','нет https','плохой сайт','давно не обновлялось')

def detect_signal(source_family: str, text: str, profile=None, matched=False) -> SignalFamily:
    t=(text or '').lower(); sf=(source_family or '').lower()
    mode=str((profile or {}).get('mode') or 'development').lower()
    if mode!='development':
        intent=list((profile or {}).get('intent_keywords') or [])+list(GENERIC_BUY)
        if sf in ('funding','money') or any(x in t for x in MONEY): return SignalFamily.MONEY
        if sf in ('launch','startup') or any(x in t for x in LAUNCH): return SignalFamily.LAUNCH
        if sf in ('partner','agency') or any(x in t for x in PARTNER): return SignalFamily.PARTNER
        if any(x in t for x in GAP): return SignalFamily.BUSINESS_GAP
        if any(x in t for x in PROBLEM): return SignalFamily.PROBLEM
        if matched and any(str(x).lower() in t for x in intent if str(x).strip()): return SignalFamily.READY_BRIEF
        return SignalFamily.UNKNOWN
    if sf in ('jobs','hiring') or any(x in t for x in HIRING): return SignalFamily.HIRING
    if sf in ('funding','money') or any(x in t for x in MONEY): return SignalFamily.MONEY
    if sf in ('launch','startup') or any(x in t for x in LAUNCH): return SignalFamily.LAUNCH
    if sf in ('partner','agency') or any(x in t for x in PARTNER): return SignalFamily.PARTNER
    # App-store connectors prefilter objective low-rating/staleness evidence.
    if sf=='app_review': return SignalFamily.BUSINESS_GAP
    if sf=='reviews': return SignalFamily.PROBLEM
    # Generic audit/company-site presence is NOT itself a business gap.
    # Promote it only when the evidence contains an actual missing/broken/manual-process signal.
    if sf in ('audit','business_gap') and (any(x in t for x in GAP) or any(x in t for x in PROBLEM)):
        return SignalFamily.BUSINESS_GAP
    if any(x in t for x in GAP): return SignalFamily.BUSINESS_GAP
    if sf in ('social','problem','reviews') and any(x in t for x in PROBLEM): return SignalFamily.PROBLEM
    if sf in ('brief','tender','freelance') or any(x in t for x in READY): return SignalFamily.READY_BRIEF
    if any(x in t for x in PROBLEM): return SignalFamily.PROBLEM
    return SignalFamily.UNKNOWN

def parse_budget(text: str):
    # Marketplace titles often HTML-encode the ruble sign (for example
    # "10 000 &#8381;"). Decode entities before extracting an explicit budget.
    t=unescape(text or '').lower().replace('\u00a0',' ')
    vals=[]
    for m in re.finditer(r'(\d[\d ]{2,})\s*(?:₽|руб|rub)', t):
        try: vals.append(int(m.group(1).replace(' ','')))
        except ValueError: pass
    for m in re.finditer(r'(\d+(?:[.,]\d+)?)\s*(?:тыс|k)\b', t):
        try: vals.append(int(float(m.group(1).replace(',','.'))*1000))
        except ValueError: pass
    return (min(vals),max(vals)) if vals else (None,None)

def urgency(text: str):
    t=(text or '').lower()
    if any(x in t for x in ('срочно','urgent','сегодня','завтра','asap')): return 'high'
    if any(x in t for x in ('в течение недели','на этой неделе','за неделю')): return 'medium'
    return 'normal'

def decision_maker_hint(signal: SignalFamily):
    if signal==SignalFamily.HIRING: return 'Founder / CEO / CTO / Head of Product / Operations'
    if signal in (SignalFamily.MONEY,SignalFamily.LAUNCH): return 'Founder / CEO / CTO / Head of Product'
    if signal in (SignalFamily.PROBLEM,SignalFamily.BUSINESS_GAP): return 'Собственник / COO / коммерческий директор / руководитель IT'
    if signal==SignalFamily.PARTNER: return 'Владелец агентства / руководитель производства / аккаунт-директор'
    return 'Собственник / заказчик / руководитель проекта'
