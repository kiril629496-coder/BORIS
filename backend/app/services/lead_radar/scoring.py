from datetime import datetime, timezone
import re
from html import unescape
from .models import SignalFamily

QUALITY_VERSION='demand_gate_v17'

BUY=('нужен','ищу','кто может','заказать','сделать','разработать','доработать','интегрировать','бюджет','срок','mvp')
NOISE=('резюме','ищу работу','курс','обучение','стажировка для студентов','продам курс')
SELF_PROMO=('предлагаю услуги','оказываю услуги','разрабатываю:','разрабатываю сайты','разрабатываю ботов',
            'разрабатываю приложения','пишу чистый код','беру проекты на разработку','готов разработать',
            'сделаю сайт','создам сайт','жду в личных сообщениях',
            'available for freelance','available for remote work','[for hire]','for hire]',
            "i’m a full-stack","i'm a full-stack",'i am a full-stack',"i’m a developer","i'm a developer",
            'my main stack','portfolio:','looking for a remote opportunity','looking for freelance work',
            'bring in clients and close deals','bring a genuine client','commission-based',
            'я — разработчик','я - разработчик','я разработчик','я являюсь разработчиком',
            'с чем могу помочь','почему меня выбирают','мои услуги','обращайтесь в личные сообщения',
            'представляю команду','мы помогаем предпринимателям','что мы разрабатываем',
            'напишите мне в личные сообщения','наши проекты:',
            'i can help with','services i offer','my services include','hire me','available to take on projects')
NON_COMMERCIAL=('looking for testers','beta test','beta testers','help shape the product','join our community',
                'new developer community','passion project','unpaid','no upfront payment','revenue share',
                'partnership, not a one-off contract','business partner','founding team')
RISKY_TASK=('sportsbook','bookmaker','betting market','betting app','value bet','casino','gambling',
            'букмекер','букмекерск','казино','ставки на спорт','ставок на спорт')
HARDWARE_ONLY=('hardware only','this job is hardware only','hardware builder only','no software development required')
DESIGN_ONLY_TITLE=('дизайн сайта','website design','site design')
TEST_ONLY_TITLE=('app testing','mobile app testing','android app testing','ios app testing','game testing','closed testing','qa testing','software testing','testers needed','tester needed','testers for',
                 'vapt','penetration testing','pen testing','pentest','security testing','security audit','vulnerability assessment','vulnerability testing','api testing')
STAFF_ROLE_TITLE=('contract role','full-time role','part-time role','job opening','employment opportunity','on-site role','onsite role','qa lead','quality assurance lead')
NON_DEV_ROLE_TITLE=('marketing specialist','digital marketing specialist','marketing manager','event marketing manager','email marketing manager','sales specialist','demo & sales specialist','content publishing','content marketing specialist',
                    'content writer','technical writer','copywriter','lead generation specialist','sales representative','business development specialist')
NON_SOFTWARE_ENGINEERING=('проект впв','внутренний противопожарный водопровод','класс фпо','производственный корпус',
                          'plc programmer','plc programming','mitsubishi fx5','gx works3','paint shop automation')
LICENSE_PROCUREMENT=('suministro de licencias','supply of software licenses','supply of software licences',
                     'software license procurement','software licence procurement','procurement of software licenses',
                     'procurement of software licences')
NON_DEV_TASK=('нужен юрист','нужна юридическая','роскомнадзор','152-фз','брендинг','логотип','упаковк',
              'этикетк','инфографик','нейминг','найти покупателя','поиск клиентов','нужен продавец',
              'ищем продавца','менеджер по продажам','подписан упд','эдо документ','фото для карточки',
              '3d modeler','3d modeller','voice actor','map builder',
              'social media ads specialist','social media manager','drive traffic via social media',
              'presentation designer','ppt designer','ppt design','proposal designer',
              'product/ui designer','product designer','web designer','brand designer','art director',
              'ui/ux designer','ui design','graphic designer',
              'веб-дизайнер','продуктовый дизайнер','дизайнер продукта','арт-директор',
              'logo designer','branding designer','copywriter','content writer','content maker','content creator','video editor',
              'app store screenshots','store screenshots','ai trainer',
              'копирайтер','контент-мейкер','контент мейкер','написание продающих текстов','написание текстов','редактор текстов',
              '推广','拉新',
              'virtual assistant','data entry',
              'sales executive','sales partner','sales lead','field sales',
              'lead generation specialist','sales representative','client acquisition specialist',
              'telesales agent','telecaller','telemarketer','appointment setter','sdr freelance','bdr freelance',
              'setter + closer','setter/closer','cold calling',
              'business development specialist','business development rep',
              'technical articles','software development technical articles','article writer','blog writer',
              'mentor needed','consultant / mentor','wikipedia','wikimedia')
DEV_ACTION_PATTERNS=(
    r'(?:нуж(?:ен|на|ны|но)|ищу|необходим(?:о|а|ы)?|требуется|сделать|написать|доработать|настроить|внедрить|реализовать|реализовывать|создать|исправить|починить|скопировать|редизайн|верстк)[^.!?\n]{0,100}(?:сайт|лендинг|интернет-магазин|приложен|бот|ии[- ]?оператор|ai[- ]?operator|llm[- ]?operator|crm|срм|битрикс|amo(?:crm)?|1с|1c|api|webhook|вебхук|парсер|скрипт|mvp|saas|автоматизац|интеграц|баз[ау] данных|админк|дашборд|devops|seo|xml|yml|backend|frontend|web[- ]?app|web[- ]?based tool|full[- ]?stack|fullstack|фуллстек|react|nestjs|node\.js|smart contract|solidity|blockchain)',
    r'(?:сайт|лендинг|приложен|бот|crm|срм|api|парсер|mvp|автоматизац|интеграц|backend|frontend)[^.!?\n]{0,80}(?:нужно|нужен|ищу|необходимо|требуется|сделать|разработать|доработать|настроить|внедрить|реализовать|исправить)',
    r'(?:need|looking for|hire|build|built|develop|implement|integrate|fix|create|code|coded|coding|migrate|redesign|optimi[sz]e|boost)[^.!?\n]{0,130}(?:developer|programmer|software engineer|smart contract engineer|website|site|wordpress|web app|web[- ]?based tool|desktop app(?:lication)?|application|mobile app|bot|crm|api|backend|frontend|software|saas|mvp|automation|integration|database|dashboard|devops|smart contract|solidity|blockchain|react|nestjs|full[- ]?stack|fullstack|python|c\+\+)',
    r'(?:developer|programmer|software engineer|smart contract engineer|website|wordpress|web app|web[- ]?based tool|application|mobile app|bot|crm|api|backend|frontend|software|saas|mvp|automation|integration|database|dashboard|devops|smart contract|solidity|blockchain|react|nestjs|full[- ]?stack|fullstack)[^.!?\n]{0,100}(?:needed|need|looking for|hire|build|develop|implement|integrate|fix|create|optimi[sz]e|boost)',
    r'(?:necesito|busco|crear|desarrollar|desarrollo|implementar|programar)[^.!?\n]{0,120}(?:sitio web|página web|aplicaci[oó]n|software|plataforma|backend|frontend|python|c\+\+|api|crm|automatizaci[oó]n)',
    r'(?:sviluppo|sviluppare|creare|realizzare|implementare)[^.!?\n]{0,120}(?:sito web|applicazione|software|piattaforma|backend|frontend|api|crm|automazione)',
    r'(?:desenvolver|desenvolvimento|criar|implementar)[^.!?\n]{0,120}(?:site|aplicativo|aplica[cç][aã]o|software|plataforma|backend|frontend|api|crm|automa[cç][aã]o)',
    r'(?:entwickeln|entwicklung|erstellen|implementieren)[^.!?\n]{0,120}(?:webseite|website|anwendung|software|plattform|backend|frontend|api|crm|automatisierung)',
    r'(?:необходимо|нужно|требуется)\s+разработать\s+(?:систему|сервис|платформу|модуль|функционал)',
)

SIGNAL_WEIGHT={
 SignalFamily.READY_BRIEF:25, SignalFamily.HIRING:14, SignalFamily.MONEY:16,
 SignalFamily.LAUNCH:12, SignalFamily.PROBLEM:18, SignalFamily.BUSINESS_GAP:14,
 SignalFamily.PARTNER:16, SignalFamily.UNKNOWN:0}

def _semantic_text(value):
    s=unescape(str(value or ''))
    # Connector search terms are discovery metadata, not evidence of buyer intent.
    # Strip the legacy TED marker so historical rows self-heal on requalification.
    s=re.sub(r'(?i)поисковый сигнал:\s*[^\n]+$', ' ', s)
    s=re.sub(r'<[^>]+>',' ',s,flags=re.S)
    s=re.sub(r'https?://\S+',' ',s,flags=re.I)
    return re.sub(r'\s+',' ',s).lower().strip()

def _explicit_dev_demand(text):
    return any(re.search(p,text,re.I) for p in DEV_ACTION_PATTERNS)

def score(raw, service_ids, signal, now=None, budget=None, profile=None):
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now=now.replace(tzinfo=timezone.utc)
    published=raw.published_at
    if published.tzinfo is None:
        published=published.replace(tzinfo=timezone.utc)
    title_text=_semantic_text(raw.title); text=_semantic_text(raw.title+' '+raw.text); s=0; reasons=[]
    mode=str((profile or {}).get('mode') or 'development').lower()
    if mode!='development':
        if service_ids: s+=24; reasons.append('совпало предложение клиента')
        sw=SIGNAL_WEIGHT[signal]
        if sw: s+=sw; reasons.append(f'сигнал: {signal.value}')
        intent=list((profile or {}).get('intent_keywords') or [])
        generic=('нужен','нужна','нужны','нужно','ищу','требуется','куплю','купить','закупка','закупаем','поставка','поставщик','заказать','цена','стоимость','сколько стоит','кп','need','looking for','buy','purchase','supplier','quote','price')
        buyer=any(str(w).lower() in text for w in intent+list(generic) if str(w).strip())
        if buyer: s+=24; reasons.append('есть покупательский сигнал')
        age=max(0,(now-published).total_seconds()/3600)
        if age<=6: s+=22; reasons.append('очень свежий')
        elif age<=24: s+=16; reasons.append('свежий')
        elif age<=72: s+=8; reasons.append('до 3 суток')
        elif age<=168: s+=2; reasons.append('до 7 суток')
        else: s-=18; reasons.append('устарел')
        if budget and budget[0]: s+=7; reasons.append('есть бюджет')
        if raw.contact_hint: s+=5; reasons.append('есть контакт')
        if raw.company_name or raw.company_domain: s+=5; reasons.append('компания идентифицирована')
        negatives=list((profile or {}).get('negative_keywords') or [])
        if any(str(w).lower() in text for w in negatives if str(w).strip()):
            s-=90; reasons.append('минус-слово профиля клиента')
        if not buyer and signal not in (SignalFamily.MONEY,SignalFamily.LAUNCH,SignalFamily.PROBLEM,SignalFamily.BUSINESS_GAP,SignalFamily.PARTNER):
            s-=35; reasons.append('нет подтверждённого покупательского сигнала')
        return max(0,min(100,s)),reasons
    if service_ids: s+=24; reasons.append('совпало направление разработки')
    sw=SIGNAL_WEIGHT[signal]
    if sw: s+=sw; reasons.append(f'сигнал: {signal.value}')
    if signal==SignalFamily.READY_BRIEF and any(w in text for w in BUY): s+=18; reasons.append('явный покупательский запрос')
    age=max(0,(now-published).total_seconds()/3600)
    if age<=6: s+=22; reasons.append('очень свежий')
    elif age<=24: s+=16; reasons.append('свежий')
    elif age<=72: s+=8; reasons.append('до 3 суток')
    elif age<=168: s+=2; reasons.append('до 7 суток')
    else: s-=18; reasons.append('устарел')
    if budget and budget[0]: s+=7; reasons.append('есть бюджет')
    if raw.contact_hint: s+=5; reasons.append('есть контакт')
    if raw.company_name or raw.company_domain: s+=5; reasons.append('компания идентифицирована')
    # Audit sources often contain words like "сайт", "каталог", "CRM" merely because
    # a company already has them. Without an objective gap/pain signal this is inventory,
    # not purchase intent, and must not create a CRM deal.
    if str(getattr(raw,'source_family','') or '').lower() in ('audit','business_gap') and signal==SignalFamily.UNKNOWN:
        s-=35; reasons.append('аудит без доказанной боли/разрыва — не считать спросом')
    if any(w in text for w in NOISE): s-=55; reasons.append('похоже на соискателя/обучение, не B2B спрос')
    if any(w in text for w in SELF_PROMO): s-=90; reasons.append('самореклама исполнителя, не запрос покупателя')
    if any(w in text for w in NON_COMMERCIAL): s-=80; reasons.append('некоммерческий тест/сообщество/бесплатный проект')
    if any(w in text for w in RISKY_TASK): s-=100; reasons.append('рисковая/регулируемая ниша не входит в целевой поток разработки')
    if any(w in text for w in HARDWARE_ONLY): s-=100; reasons.append('заказ явно только на hardware, не программная разработка')
    if any(w in text for w in NON_SOFTWARE_ENGINEERING): s-=100; reasons.append('инженерное/промышленное проектирование, не программная разработка текущего каталога')
    if any(w in text for w in LICENSE_PROCUREMENT): s-=100; reasons.append('закупка лицензий/готового ПО, не заказ на разработку')
    multi_account_bypass=('bypass' in text and (
        'multiple accounts' in text or 'multiple isolated browser' in text or
        'secure multiple bookings' in text or 'across different accounts' in text
    ))
    if multi_account_bypass:
        s-=100; reasons.append('мультиаккаунтная автоматизация обхода ограничений не входит в целевой поток')
    # Public freelance/social feeds contain many adjacent professions. For these
    # sources a service keyword alone is insufficient: require an explicit IT action,
    # except a genuine commercial hiring signal.
    sf=str(getattr(raw,'source_family','') or '').lower()
    explicit_dev=_explicit_dev_demand(text)
    dominant_nondev=any(w in text for w in NON_DEV_TASK)
    title_nondev=any(w in title_text for w in NON_DEV_TASK)
    nondev_role_title=any(w in title_text for w in NON_DEV_ROLE_TITLE)
    design_only_title=any(w in title_text for w in DESIGN_ONLY_TITLE)
    test_only_title=any(w in title_text for w in TEST_ONLY_TITLE)
    staff_role_title=any(w in title_text for w in STAFF_ROLE_TITLE)
    title_dev_action=bool(re.search(r'(разработ|develop|build|верстк|код|code)',title_text,re.I))
    implementation_signal=bool(re.search(r'(coded|coding|codebase|frontend code|backend implementation|production[- ]ready code|deploy(?:ment)?|верстк|написать код|реализовать функционал)',text,re.I))
    if sf in ('brief','freelance','social'):
        # If the title itself asks for another profession/task, that is the primary
        # job even when the description mentions software the customer already has.
        if title_nondev or nondev_role_title:
            s-=90; reasons.append('заголовок явно про другую профессию/задачу')
        elif staff_role_title:
            s-=90; reasons.append('вакансия/контрактная роль специалиста, не заказ на разработку')
        elif test_only_title and not title_dev_action:
            s-=90; reasons.append('заказ только на тестирование, без разработки')
        elif design_only_title and not (title_dev_action or implementation_signal):
            s-=90; reasons.append('заказ только на дизайн, без разработки')
        elif dominant_nondev and not explicit_dev:
            s-=80; reasons.append('явная задача другой профессии, не разработка')
        elif signal!=SignalFamily.HIRING and not explicit_dev:
            s-=55; reasons.append('нет явного действия по разработке')
    return max(0,min(100,s)),reasons
