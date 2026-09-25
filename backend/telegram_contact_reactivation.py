#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
from datetime import datetime, timezone

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv('/root/BORIS/backend/.env')

from app.crypto_utils import decrypt_secret
from app.monitoring.telegram import build_client

DB=os.getenv('DATABASE_URL') or os.getenv('SQLALCHEMY_DATABASE_URL')
engine=create_engine(DB,pool_pre_ping=True)
LOCK_KEY=884422990

MIN_AGE_DAYS=int(os.getenv('TG_CONTACT_REACT_MIN_AGE_DAYS','30'))
LABELLED_AVITO_MIN_AGE_DAYS=int(os.getenv('TG_LABELLED_AVITO_MIN_AGE_DAYS','14'))
MAX_AGE_DAYS=int(os.getenv('TG_CONTACT_REACT_MAX_AGE_DAYS','365'))
DIALOG_LIMIT=int(os.getenv('TG_CONTACT_REACT_DIALOG_LIMIT','2500'))
HISTORY_LIMIT=int(os.getenv('TG_CONTACT_REACT_HISTORY_LIMIT','12'))
MIN_SCORE=int(os.getenv('TG_CONTACT_REACT_MIN_SCORE','55'))

SIGNALS=(
    ('авито',45),('avito',45),('авитолог',45),
    ('бизнес',30),('предприним',35),('собственник',30),('директор',20),
    ('маркет',30),('реклам',30),('продаж',32),('лид',32),('заявк',28),
    ('клиент',24),('crm',35),('срм',35),('телефон',18),('аналитик',18),
    ('сайт',16),('агентств',18),('подрядчик',20),('услуг',12),
    ('мебел',14),('стро',14),('производ',14),('магазин',14),('товар',12),
)
NEGATIVE=(
    'не пишите','больше не пишите','не интересно','неинтересно','не актуально',
    'неактуально','не нужно','нет спасибо','нет, спасибо','стоп','удалите мой контакт',
    'резюме','собеседование','ищу работу','поиск работы','по поводу вакансии',
    'данная вакансия','есть еще вакансии','устроиться на работу','трудоустройство',
    'кандидат','кандидатура','соискатель','ищем сотрудника','нужен сотрудник',
    'менеджер по продажам','квалификатор',
    'предлагаю услуги','предлагаем услуги','оказываю услуги','оказываем услуги',
    'можем предложить','продаем лиды','продаём лиды','лиды по вашей нише',
    'маркетинг под ключ','я авитолог','мы авитологи','работаю авитологом',
    'наши услуги','наша команда занимается','готовы взять на ведение',
)
JOB_NAME_MARKERS=(
    'кандидат','по вакансии','вакансия','резюме','соискатель',
)
STRONG_NAME_INTENT=(
    'нужен авитолог','нужна авитолог','ищу авитолога','авитолог нужен',
    'нужен специалист по авито','нужна помощь с авито','нужно продвижение',
    'ищет авитолога','сам написал','сама написала',
)
CITY_MARKERS=(
    'москва','мск','мо ','московская','спб','санкт-петербург','питер','ленобласть','ленинградская',
    'сочи','краснодар','ростов','уфа','казань','екатеринбург','екб','новосибирск','нск',
    'нижний новгород','нн ','самара','пермь','воронеж','волгоград','омск','челябинск',
    'тюмень','саратов','тольятти','барнаул','ижевск','иркутск','хабаровск','владивосток',
    'ярославль','махачкала','томск','оренбург','кемерово','новокузнецк','рязань','астрахань',
    'пенза','липецк','киров','чебоксары','тула','калининград','курск','ставрополь','белгород',
    'симферополь','севастополь','владимир','сургут','мытищи','химки','балашиха','подольск',
)
NICHE_MARKERS=(
    'мебел','кухн','шкаф','гардероб','ремонт','строй','строит','ижс','дом','бан','бытовк',
    'окн','двер','ворот','авто','машин','запчаст','разбор','фар','детейлинг','шиномонтаж',
    'плитк','кровл','забор','фасад','бетон','газобетон','арматур','металл','профлист','щебен','песок',
    'недвиж','риелтор','ипотек','юрист','бухгалтер','достав','аренд','производ','магазин',
    'товар','услуг','салон','косметолог','эпиляц','стоматолог','медицин','клининг','эвакуатор',
    'спецтех','груз','логист','маркетплейс','образован','курс','фитнес','цвет','туризм','отель',
)

def labelled_avito_contact(name: str) -> bool:
    n=' '.join(str(name or '').lower().split())
    return ('авито' in n or 'avito' in n) and any(x in n for x in NICHE_MARKERS) and any(x in n for x in CITY_MARKERS)

SELF_PROMO_STRONG=(
    'пишу с предложением','предлагаю сотрудничество','предлагаю обсудить сотрудничество',
    'предлагаю услуги','наш сервис','наша команда занимается продвижением',
    'занимаемся продвижением youtube','предлагаем 10 000 проверок','предлагаем 10000 проверок',
    'являюсь представителем сервиса','мы продаём спикерские места','мы продаем спикерские места',
    'помогаем бизнесу увеличивать количество клиентов','я авитолог, нужны клиенты',
)
HARD_DECLINE=(
    'не запускать сотрудничество','решение пока не запускать','остановимся на вашей кандидатуре',
    'буду готов - обращусь','буду готов — обращусь','больше не пишите',
    'уже нашли авитолога','уже нашел авитолога','уже нашёл авитолога','выбрал подрядчиков',
    'если не сработаемся','позже вернусь к этому вопросу','здравствуйте, нет',
    'авито вообще теперь не интересует','авито больше не интересует','не интересует авито',
    'авито не интересно','авито не актуально','авито неактуально',
    'уже работаю с авитологом','уже работаем с авитологом','уже есть авитолог','есть свой авитолог',
    'есть действующий авитолог','авитолог уже есть','с авитологом уже работаем',
)
BUYER_MARKERS=(
    'нужен','нужно','ищу','интересует','стоимость','сколько стоит','цена',
    'покажите','давайте','заявк','лид','авито','avito','crm','срм',
    'телефон','коллтрекинг','сквозн','аналитик','результат неудовлетвор',
)
BUYER_NAME_MARKERS=(
    'нужен авитолог','нужна авитолог','ищу авитолога','авитолог нужен',
    'нужен специалист по авито','нужна помощь с авито','нужно продвижение',
)
STRONG_BUYER_TEXT=(
    'ищу авитолога','нужен авитолог','нужна авитолог','выбираем авитолога','ищу подрядчика',
    'нужен подрядчик','нужен специалист','нужна помощь','нужно продвижение','интересует',
    'сколько стоит','стоимость','какая цена','покажите','давайте','хочу посмотреть',
    'мало заявок','нет заявок','мало лидов','нет лидов','результат неудовлетвор',
    'как работает','как ведется работа','как ведётся работа','как понять','какой формат',
)
SELLER_DIRECTION=(
    'пишу с предложением','предлагаю сотрудничество','предлагаю обсудить сотрудничество',
    'предлагаю услуги','наша команда занимается','мы занимаемся','я занимаюсь',
    'я разрабатываю','мы разрабатываем','я представляю','являюсь представителем',
    'наш сервис','наша студия','наше агентство','маркетинговое агентство',
    'мы помогаем','помогаем бизнесу','мы даём поток лидов','мы даем поток лидов',
    'лиды от ','стоимость лида 500','первые обращения — уже','первые обращения - уже',
    'работаем легально','с официальным договором','если хотите увеличить kpi',
    'получать клиентов с помощью','привлечении целевых клиентов',
    'делаем стоимость лида','на лиды и заявки не смотрите','работайте только бюджет',
    'готовый арендный бизнес','обмениваться лидами','от нас 10% от чека',
    'сами продаем на авито','сделали свой','консалтинговая компания','мы консалтинго',
    'профессионально занимаемся avito','работаем с авито профессионально','специализируемся на авито',
    'работаем с производителями','если подрядчика еще не нашли','агентские','условия передачи клиентов',
    'список услуг, которые мы предлагаем','вы развиваете продажи через авито и ищете способ',
    'мы ему своими методами лиды привлекаем','мини продукт и менеджеры продают наставничество',
    'готовы предложить 15% с выручки','партнерство','партнёрство','можем придумать партнерство',
    'можем придумать партнёрство','помогу привлечь клиентов','актуально ли для вас увеличить поток лидов',
    'ежедневные целевые лиды','если интересно — пиши','если интересно - пиши',
)


def reactivation_quality_ok(name: str, inbound_context: str) -> bool:
    n=' '.join(str(name or '').lower().split())
    t=' '.join(str(inbound_context or '').lower().split())
    if any(x in n for x in JOB_NAME_MARKERS):
        return False
    if any(x in n for x in ('поддержк','support','служба заботы')):
        return False
    if any(x in t for x in HARD_DECLINE):
        return False
    # Referral/intermediary dialogs are not direct buyers. Do not reactivate the
    # intermediary when they explicitly handed the conversation to another person.
    if any(x in t for x in (
        'передам ваш контакт','передал ваш контакт','передала ваш контакт',
        'дал ваш контакт','дала ваш контакт','маркетологу дал ваш контакт','маркетологу дала ваш контакт',
        'он с вами свяжется','она с вами свяжется','свяжется с вами','он напишет','она напишет',
        'напишите маркетологу','напиши маркетологу','напишите моему маркетологу','напиши моему маркетологу',
        'свяжитесь с ним напрямую','свяжитесь с ней напрямую','мой друг ищет авитолога','моя знакомая ищет авитолога',
    )):
        return False
    if any(x in t for x in (
        'вакансия','ищем сотрудника','ищем в штат','ищем в команду','помощник авитолога',
        'менеджер авитолог','свободный график','опыт не нужен','обучение с нуля',
    )):
        return False
    if not t.strip() and 'клиент' in n:
        return False
    strong_buyer=any(x in t for x in STRONG_BUYER_TEXT)
    named_buyer=any(x in n for x in BUYER_NAME_MARKERS) or labelled_avito_contact(n)
    seller=any(x in t for x in SELF_PROMO_STRONG) or any(x in t for x in SELLER_DIRECTION)
    # Explicit professional Avito vendors/lead sellers are not reactivation buyers
    # even if their sales pitch contains generic buyer-like words such as
    # "актуально" or "обсудим".
    hard_seller=any(x in t for x in (
        'профессионально занимаемся avito','работаем с авито профессионально','специализируемся на авито',
        'я авитолог','мы авитологи','продаем лиды','продаём лиды','делаем стоимость лида',
        'готовый арендный бизнес','обмениваться лидами','агентские','условия передачи клиентов',
        'если подрядчика еще не нашли','работаем с производителями',
        'продаем аккаунты','продаём аккаунты','цена аккаунта','доступ в складчину','складчина —',
        'поток лидов через вертикальные ролики','бесплатный тест контент-воронки','использую свои площадки',
        'предлагаю бесплатный тест','список услуг, которые мы предлагаем',
        'вы развиваете продажи через авито и ищете способ','мы ему своими методами лиды привлекаем',
        'мини продукт и менеджеры продают наставничество','готовы предложить 15% с выручки',
        'партнерство','партнёрство','можем придумать партнерство','можем придумать партнёрство','предлагаю партнерство','предлагаю партнёрство',
        'актуально ли для вас увеличить поток лидов','ежедневные целевые лиды','если интересно — пиши','если интересно - пиши',
        'не, по авито нет к сожалению','вы обратились в bright4auto','аферист ты',
        'спасибо, подумаем','если что буду иметь ввиду','если что буду иметь в виду',
        'эти доки должны быть в личном кабинете на авито','2000+ экспертов и предпринимателей сейчас в клубе',
        'передал ваш контакт коллеге','свяжется по поводу заявок конкурентов с авито',
        'количество лидов за 10 000','количество лидов за 10000','стоимость лида для таких услуг',
        'мы можем настроить эффективную кампанию','предлагаю начать с минимального бюджета',
        'запустить тестовый проект','подобрать оптимальный план для вашего бизнеса',
        'мы обеспечиваем стабильный и прогнозируемый поток заявок','мы обеспечиваем поток заявок',
        'я развиваю сервис','развиваю сервис для интеграции','мы сейчас в поиске партнеров','мы сейчас в поиске партнёров',
        'готовы давать лучшие условия для партнеров','готовы давать лучшие условия для партнёров',
        'могу предложить добавить ваш аккаунт','могу предложить добавить ваш аккаунт в текущие запуски',
        'с вами изначально связывалась нейросеть','на зуме сделать демонстрацию crm',
        'сайты делаем на тильде','цена в среднем 120, срок разработки','пришлю работы цена в среднем',
        'как и обещал – высылаю коммерческое предложение','как и обещал - высылаю коммерческое предложение',
        'примеры наших сайтов','напишите @sibresult',
    ))
    if hard_seller or (seller and not strong_buyer):
        return False
    if not t.strip():
        return named_buyer
    return bool(strong_buyer or named_buyer or (not seller and any(x in t for x in BUYER_MARKERS)))


def score_text(value: str) -> tuple[int,list[str]]:
    t=' '.join(str(value or '').lower().split())
    score=0; hits=[]
    for marker,weight in SIGNALS:
        if marker in t:
            score += weight; hits.append(marker)
    return min(score,100),hits


def reactivation_priority(name: str, context: str, username: str | None = None) -> tuple[int,str]:
    """Deterministic commercial-readiness score. Safety eligibility is checked separately."""
    t=' '.join(str(context or '').lower().split())
    n=' '.join(str(name or '').lower().split())
    score=0; reasons=[]
    def hit(points:int, pattern:str, reason:str):
        nonlocal score
        if re.search(pattern,t): score+=points; reasons.append(reason)
    hit(50,r'(назнач.{0,20}(созвон|звон|встреч)|давайте.{0,20}(созвон|видео|встреч)|когда.{0,20}(созвон|связ|показ)|номер телефона|видеосозвон)','meeting')
    hit(60,r'(хочу.{0,35}(запуст|реклам|продвиж)|планирую.{0,35}(запуст|выбрать)|выстроить.{0,35}источник|сможем.{0,20}(взаимодействовать|поработать)|есть ли смысл.{0,30}авито)','launch_intent')
    hit(38,r'(ищу.{0,40}авитолог|нужен.{0,40}авитолог|выбираем.{0,30}авитолог|подрядчик по авито)','explicit_avito_need')
    hit(34,r'(пришел сезон|пришёл сезон|на нашем аккаунте|готовых объявлен|условия.{0,30}не изменил|остались объявления)','active_existing_account')
    hit(28,r'(стоимост|сколько стоит|цена|отобь[её]тся|окупаемост|стоит ли делать|бюджет)','economics')
    hit(22,r'((покаж|пришл|есть|какие).{0,25}(кейс|результат)|сколько.{0,15}(лид|заяв)|цена.{0,10}(лид|заяв)|cpl)','proof_request')
    hit(18,r'(мало заяв|нет заяв|мало лид|нет лид|никто не пишет|по нулям|не окуп)','pain')
    hit(12,r'(ниша\s*:|гео\s*[:\-]|москва|мск|спб|санкт-петербург|екатеринбург|екб|воронеж|уфа|автосервис|строительств|мебел|грузоперевоз)','concrete_context')
    if username: score+=5; reasons.append('direct_username')
    if any(x in t for x in ('без кейсов маркетолог не пропуст','без кейсов не пропуст')):
        score-=12; reasons.append('conditional_case_gate')
    if any(x in t for x in ('за процент с продаж','процент с продаж')):
        score-=8; reasons.append('nonstandard_commercial_model')
    if not t.strip(): score-=15; reasons.append('no_context')
    if labelled_avito_contact(n): score+=6; reasons.append('labelled_avito_contact')
    return max(0,min(100,score)),','.join(reasons) or 'generic_history'


def _context_hint(name: str, context: str) -> str:
    nlow=' '.join(str(name or '').lower().split())
    low=' '.join(str(context or '').lower().split())
    structured=''
    m=re.search(r'(?:ниша|бизнес)\s*:\s*([^\n\.]{1,180})',low)
    if m:
        structured=m.group(1)
    # Niche is trusted only from the owner's contact label. Historical dialog
    # text can contain old/forwarded/unrelated projects and must never invent a niche.
    niche_source=nlow
    niche=''
    for markers,label in (
        (('мебел','кухн','шкаф'),'мебели'),
        (('строительств','каркасн','ижс','дом','бан'),'строительства'),
        (('автосервис','автосалон','автозапчаст'),'автониши'),
        (('плитк','бордюр'),'тротуарной плитки'),
        (('штукатур',),'штукатурных работ'),
        (('клининг',),'клининга'),
        (('грузоперевоз','логист'),'грузоперевозок'),
        (('одежд',),'одежды'),
    ):
        if any(x in niche_source for x in markers):
            niche=label; break
    geo_source=' '.join(x for x in (nlow,structured) if x)
    # Full history is used for geography only when it contains an explicit
    # geo-like phrase, avoiding stale unrelated business mentions.
    for city_token in ('москва','мск','санкт-петербург','питер','спб','екатеринбург','екб','новосибирск','нск','краснодар','ставропол','волгоград','белгород','воронеж','уфа'):
        if re.search(rf'(?:\bпо\s+|\bв\s+|\bгео\s*[:\-]?\s*){re.escape(city_token)}',low):
            geo_source += ' '+city_token
    city=''
    for markers,label in (
        (('санкт-петербург','питер','спб'),'Санкт-Петербурге'),
        (('москва','мск'),'Москве'),
        (('екатеринбург','екб'),'Екатеринбурге'),
        (('новосибирск','нск'),'Новосибирске'),
        (('краснодар',),'Краснодаре'),
        (('ставропол',),'Ставрополе'),
        (('волгоград',),'Волгограде'),
        (('белгород',),'Белгороде'),
        (('воронеж',),'Воронеже'),
        (('уфа',),'Уфе'),
    ):
        if any(x in geo_source for x in markers):
            city=label; break
    if niche and city:
        return f' для {niche} в {city}'
    if niche:
        return f' для {niche}'
    if city:
        return f' в {city}'
    return ''


def draft(name: str, context: str, signals: list[str]) -> str:
    # Contact names often contain CRM notes/niche/city, so a neutral greeting is
    # safer and sounds more human than addressing a person by a noisy label.
    hello='Привет!'
    s=set(signals)
    low=str(context or '').lower()
    hint=_context_hint(name,context)
    if not low.strip() and ({'авито','avito','авитолог'} & s):
        return (f'{hello} Мы раньше пересекались по Авито. За это время я сильно развил BORIS — сейчас он помогает вести объявления, контент и CPL, '
                'а обращения дальше не теряются благодаря AI-менеджеру и CRM. Если тема Авито ещё актуальна, могу коротко показать, что изменилось.')
    if {'авито','avito','авитолог'} & s:
        # Reactivation should remember the old buyer context instead of sending
        # one identical generic pitch to every historical Avito contact.
        if any(x in low for x in ('не занимались авито','с нуля','проект только запускаем','только запускаем','запустить рекламу на авито','хочу запустить рекламу','сейчас только выбираем авитолога','выбираем авитолога','планирую выбрать подрядчика','хочу выстроить этот источник','нужны лиды в авито')):
            return (f'{hello} Мы раньше обсуждали запуск Avito{hint}: вы хотели сначала проверить канал, а при результате выстроить его в систему. '
                    'Сейчас BORIS закрывает этот путь целиком — анализ ниши и конкурентов, структура объявлений, тексты/фото/фиды, контроль CPL, а дальше AI-менеджер и CRM. '
                    'Если задача ещё актуальна, могу начать не с общей презентации, а с короткого плана: что тестируем первым и по каким цифрам принимаем решение.')
        if any(x in low for x in ('перечень гипотез','сгенерировать перечень гипотез','оценить перспективы','разбивкой на этапы','поэтапной оплат','поэтапно же оплат','поэтапно оплат')):
            return (f'{hello} Мы раньше обсуждали Avito{hint}, и задача была не просто разместить объявления, а поэтапно проверить гипотезу и экономику канала. '
                    'Предлагаю возвращаться так же предметно: сначала спрос и конкуренты + выбор гипотезы, затем ориентиры по CPL/бюджету на фактических данных, после этого короткий тест и решение — масштабировать или остановить. '
                    'Цифры до анализа выдумывать не буду. Если задача ещё актуальна, могу начать именно с первого этапа.')
        if any(x in low for x in ('пришел сезон','пришёл сезон','на нашем аккаунте','готовых объявлен','остались объявления','объявления которые вы составляли','условия и услуги у нас почти ничего не изменилось')):
            return (f'{hello} Мы раньше уже обсуждали Avito{hint}, и у вас остались готовые объявления с прошлого запуска. '
                    'Если задача снова актуальна, логичнее не собирать всё заново: BORIS может сначала проверить текущие объявления, конкурентов и продвижение, '
                    'выделить что оставить/обновить и дальше контролировать CPL и обращения через AI-менеджера и CRM. Могу начать с такого короткого аудита.')
        if (any(x in low for x in ('покажите цифры','покажи цифры','какие результаты','покажите результат','покажи результат','пришлите кейсы','пришлите кейс','есть кейсы','есть ли кейсы','cpl','цена заявки','стоимость лида')) or re.search(r'(покаж|пришл|есть ли).{0,25}(кейс|опыт|портфолио)',low)):
            return (f'{hello} Мы раньше пересекались по Авито, и вы как раз спрашивали про цифры/кейсы. За это время я сильно развил BORIS: '
                    'он анализирует аккаунт и конкурентов, готовит объявления и контент, следит за CPL и дальше связывает обращения с AI-менеджером и CRM. '
                    'Если задача ещё жива, могу не присылать общую презентацию, а показать конкретный разбор и результаты на похожем сценарии.')
        if any(x in low for x in ('сколько стоит','стоимость услуг','стоимость вашей','какова стоимость','цена услуги','по стоимости','стоимость этой','дорого','дорогой','ценничек','ценник','не окупится','не окупается','отобьётся','отобьется')):
            return (f'{hello} Мы раньше общались по Авито, и у вас был вопрос по стоимости и экономике. Сейчас BORIS уже закрывает не только ведение объявлений: '
                    'он помогает с текстами, фото и фидами, контролирует CPL и ведёт обращения дальше через AI-менеджера и CRM. '
                    'Если Авито актуально, могу коротко пересчитать формат под вашу текущую задачу и показать, из чего складывается результат.')
        if any(x in low for x in ('источник лидов','источник?','какой источник','откуда лиды','как ведется работа','как ведётся работа','это база','этичност','перепродаете','перепродаёте','холодные звонки','холодные контакты')):
            return (f'{hello} Мы раньше как раз обсуждали, откуда берутся обращения и чем это отличается от холодной базы. Сейчас BORIS строит контур прозрачнее: '
                    'работает с вашим Avito/рекламным потоком, входящими обращениями и публичным спросом, фиксирует источник в CRM и не выдаёт чужую клиентскую базу за ваши лиды. '
                    'Если тема ещё актуальна, могу показать на схеме, откуда появляется контакт и что с ним происходит дальше.')
        if any(x in low for x in ('созвон','видеосозвон','по видео','встреч','номер телефона','позвоню','можем связаться','модем связаться','в четверг','в понедельник','во вторник','сегодня на','все в силе','всё в силе')):
            return (f'{hello} Мы раньше обсуждали Авито{hint} и хотели связаться. За это время BORIS сильно вырос: сейчас он анализирует объявления и конкурентов, '
                    'готовит контент, контролирует CPL и связывает обращения с AI-менеджером и CRM. Если задача ещё актуальна, можем вернуться к ней — покажу на конкретном сценарии без длинной презентации.')
        if any(x in low for x in ('нет заявок','мало заявок','никто не пишет','по нулям','не звонит','не окупится','не окупается')):
            return (f'{hello} Мы раньше пересекались по Авито, и тогда основной вопрос был в заявках/окупаемости. Сейчас BORIS как раз строит работу от этой точки: '
                    'проверяет объявления и конкурентов, помогает с контентом, контролирует CPL и дальше не теряет обращения благодаря AI-менеджеру и CRM. '
                    'Если проблема ещё актуальна, могу показать, что бы сейчас проверили в первую очередь.')
        if any(x in low for x in ('не занимались авито','с нуля','проект только запускаем','только запускаем','запустить рекламу на авито','хочу запустить рекламу','сейчас только выбираем авитолога','выбираем авитолога','планирую выбрать подрядчика','хочу выстроить этот источник','нужны лиды в авито')):
            return (f'{hello} Мы раньше обсуждали запуск/рост Avito{hint}. За это время я сильно развил BORIS — сейчас он помогает пройти весь путь: '
                    'анализ ниши и конкурентов, объявления, тексты, фото, фиды, контроль CPL, а затем AI-менеджер и CRM для обработки обращений. '
                    'Если задача всё ещё актуальна, могу коротко показать, как бы собрали её сейчас без лишних ручных действий.')
        return (f'{hello} Мы раньше пересекались по Авито{hint}. Я за это время сильно развил BORIS — сейчас он работает как AI-авитолог: '
                'анализирует объявления и конкурентов, помогает с текстами, фото и фидами, следит за CPL, а обращения ведёт дальше через AI-менеджера и CRM. '
                'Если тема Авито сейчас актуальна, могу коротко показать, что получилось.')
    if any(x in low for x in ('сквозн','аналитик','romi','окупаемост')):
        return (f'{hello} Мы раньше общались по аналитике и продажам. Сейчас в BORIS я связал рекламу, обращения, звонки, CRM, договоры и оплаты в один контур, '
                'чтобы было видно не просто лиды, а какие источники реально приносят деньги. Если эта задача ещё актуальна, могу коротко показать схему.')
    if any(x in low for x in ('телефон','коллтрекинг','звонк','атс')):
        return (f'{hello} Мы раньше пересекались по звонкам и продажам. Сейчас BORIS связывает телефонию с CRM: фиксирует входящие и пропущенные, создаёт карточку клиента, '
                'сохраняет историю и показывает следующий шаг менеджера. Если тема актуальна, могу показать, как это работает в одном окне.')
    if {'crm','срм'} & s:
        return (f'{hello} Мы раньше общались по CRM. Сейчас BORIS сам собирает обращения, квалифицирует клиентов, ведёт сделки, показывает зависшие лиды и подсказывает следующий шаг. '
                'Если вопрос CRM ещё актуален, могу показать коротко без длинной презентации.')
    if {'лид','заявк','клиент'} & s:
        return (f'{hello} Мы раньше общались по заявкам и клиентам. Сейчас BORIS объединяет привлечение лидов, их квалификацию, AI-менеджера и CRM, чтобы обращения не терялись после рекламы. '
                'Если задача с потоком клиентов ещё актуальна, могу коротко показать, как это сейчас устроено.')
    if {'продаж'} & s:
        return (f'{hello} Мы раньше общались по рабочим вопросам. Я сейчас развил BORIS в полноценную AI-систему продаж: '
                'он собирает обращения, ведёт CRM, квалифицирует лиды, контролирует зависшие сделки и показывает, откуда приходят деньги. '
                'Если у вас сейчас актуальны продажи или автоматизация отдела, могу скинуть коротко, как это работает.')
    return (f'{hello} Мы раньше общались по бизнесу. Я сейчас развиваю BORIS — AI-систему, которая объединяет привлечение клиентов, '
            'обработку обращений, CRM и аналитику продаж. Если сейчас актуальны заявки или автоматизация, могу коротко показать без длинной презентации.')


def ensure_schema(db):
    db.execute(text('ALTER TABLE boris_sales_hot_leads ADD COLUMN IF NOT EXISTS contact_peer_id BIGINT'))
    db.execute(text('ALTER TABLE boris_sales_hot_leads ADD COLUMN IF NOT EXISTS reactivation_priority INTEGER'))
    db.execute(text('ALTER TABLE boris_sales_hot_leads ADD COLUMN IF NOT EXISTS reactivation_priority_reason TEXT'))


def run_scan(apply: bool=False) -> int:
    now=datetime.now(timezone.utc)
    with engine.begin() as db:
        ensure_schema(db)
        db.execute(text("CREATE TABLE IF NOT EXISTS monitor_runtime_state (key TEXT PRIMARY KEY,value_text TEXT,updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"))
        raw=db.execute(text("SELECT value_text FROM monitor_runtime_state WHERE key='telegram_scan_cooldown_until'" )).scalar()
        if raw:
            try:
                until=datetime.fromisoformat(str(raw).replace('Z','+00:00'))
                if until.tzinfo is None: until=until.replace(tzinfo=timezone.utc)
                if until > now:
                    print(f'TG_CONTACT_REACT=SKIP telegram_cooldown until={until.isoformat()}')
                    return 0
            except Exception:
                pass
        already=set(int(x[0]) for x in db.execute(text("SELECT contact_peer_id FROM boris_sales_hot_leads WHERE contact_peer_id IS NOT NULL")).all())
        contacted_names=set(str(x[0]).lower().lstrip('@') for x in db.execute(text("SELECT contact_username FROM boris_sales_hot_leads WHERE contact_username IS NOT NULL AND contacted_at IS NOT NULL")).all())

    client=build_client(
        decrypt_secret(os.environ['MONITOR_TG_SESSION_ENC']),
        int(os.environ['MONITOR_TG_API_ID']),
        os.environ['MONITOR_TG_API_HASH'],
    )
    checked=eligible=queued=0; previews=[]
    try:
        for d in client.iter_dialogs(limit=DIALOG_LIMIT):
            if not getattr(d,'is_user',False):
                continue
            e=d.entity
            if getattr(e,'bot',False) or getattr(e,'is_self',False):
                continue
            peer_id=int(getattr(e,'id',0) or 0)
            if not peer_id or peer_id in already:
                continue
            username=(getattr(e,'username',None) or '').strip()
            if username and username.lower() in contacted_names:
                continue
            dt=getattr(d,'date',None)
            if not dt:
                continue
            if dt.tzinfo is None:
                dt=dt.replace(tzinfo=timezone.utc)
            age=(now-dt).days
            first=(getattr(e,'first_name',None) or '').strip()
            last=(getattr(e,'last_name',None) or '').strip()
            name=' '.join(x for x in (first,last) if x).strip()
            low_name=name.lower()
            min_age=LABELLED_AVITO_MIN_AGE_DAYS if labelled_avito_contact(low_name) else MIN_AGE_DAYS
            if age < min_age or age > MAX_AGE_DAYS:
                continue
            checked+=1
            if any(x in low_name for x in JOB_NAME_MARKERS):
                continue
            dialog_msg=getattr(d,'message',None)
            latest=str(getattr(dialog_msg,'message',None) or '')
            latest_inbound=latest if dialog_msg is not None and not bool(getattr(dialog_msg,'out',False)) else ''
            seed=' '.join((name,username,latest_inbound))
            seed_score,seed_hits=score_text(seed)
            history=[]; inbound=[]
            try:
                for m in client.iter_messages(e,limit=HISTORY_LIMIT):
                    body=str(getattr(m,'message',None) or '').strip()
                    if not body:
                        continue
                    history.append(body)
                    if not bool(getattr(m,'out',False)):
                        inbound.append(body)
            except Exception:
                continue
            context=' '.join(history)
            inbound_context=' '.join(inbound)
            low=inbound_context.lower()
            if any(x in low for x in NEGATIVE):
                continue
            # A CRM-like contact label containing "клиент" can represent an active
            # customer/partner. With no inbound evidence, never cold-reactivate it.
            if not inbound_context.strip() and 'клиент' in low_name:
                continue
            if not inbound_context.strip() and not (any(x in low_name for x in STRONG_NAME_INTENT) or labelled_avito_contact(low_name)):
                continue
            if not reactivation_quality_ok(name,inbound_context):
                continue
            hist_score,hits=score_text(inbound_context)
            total=min(100,max(seed_score,hist_score)+10)
            if total < MIN_SCORE:
                continue
            eligible+=1
            all_hits=list(dict.fromkeys(seed_hits+hits))
            msg=draft(name,inbound_context,all_hits)
            priority,priority_reason=reactivation_priority(name,inbound_context,username)
            previews.append((total,peer_id,username,name,all_hits[:8],msg[:180]))
            if apply:
                with engine.begin() as db:
                    ensure_schema(db)
                    res=db.execute(text("""
                      INSERT INTO boris_sales_hot_leads(
                        monitor_message_id,platform,contact_name,contact_username,contact_peer_id,
                        source_name,original_text,monitor_intent,monitor_score,boris_fit,boris_bucket,
                        first_message_draft,status,approval_required,source_posted_at,approval_note,reactivation_priority,reactivation_priority_reason
                      ) VALUES(
                        :mid,'telegram',:name,:username,:peer,'Telegram contacts',:ctx,'existing_contact_reactivation',
                        :score,:score,'HOT',:draft,'new',false,:last,'existing_telegram_contact',:priority,:priority_reason
                      ) ON CONFLICT(monitor_message_id) DO UPDATE SET
                        reactivation_priority=EXCLUDED.reactivation_priority,
                        reactivation_priority_reason=EXCLUDED.reactivation_priority_reason,
                        first_message_draft=CASE WHEN boris_sales_hot_leads.status='new' THEN EXCLUDED.first_message_draft ELSE boris_sales_hot_leads.first_message_draft END,
                        updated_at=NOW()
                      RETURNING id
                    """),{
                      'mid':f'tg_contact:{peer_id}','name':name or None,'username':username or None,
                      'peer':peer_id,'ctx':inbound_context[:3000],'score':total,'draft':msg,'last':dt,'priority':priority,'priority_reason':priority_reason,
                    }).scalar()
                    if res: queued+=1
    finally:
        client.disconnect()
    previews.sort(reverse=True,key=lambda x:x[0])
    print(f'TG_CONTACT_REACT checked={checked} eligible={eligible} queued={queued} apply={apply}')
    for p in previews[:20]:
        print('candidate',p)
    print('TG_CONTACT_REACT=PASS')
    return 0

def main(apply: bool=False) -> int:
    with engine.connect() as db:
        try:
            raw=db.execute(text("SELECT value_text FROM monitor_runtime_state WHERE key='telegram_scan_cooldown_until'")).scalar()
            if raw:
                until=datetime.fromisoformat(str(raw).replace('Z','+00:00'))
                if until.tzinfo is None:
                    until=until.replace(tzinfo=timezone.utc)
                now=datetime.now(timezone.utc)
                if until>now:
                    print(f'TG_CONTACT_REACT=SKIP telegram_cooldown remaining={int((until-now).total_seconds())}s')
                    return 0
        except Exception:
            pass
        got=bool(db.execute(text('SELECT pg_try_advisory_lock(:k)'),{'k':LOCK_KEY}).scalar())
        db.commit()
        if not got:
            print('TG_CONTACT_REACT=SKIP telegram_session_busy')
            return 0
        try:
            return run_scan(apply)
        finally:
            db.execute(text('SELECT pg_advisory_unlock(:k)'),{'k':LOCK_KEY})
            db.commit()


if __name__=='__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('--apply',action='store_true'); args=ap.parse_args()
    raise SystemExit(main(args.apply))
