from datetime import datetime, timezone
import pytest
from sqlalchemy import create_engine

from app.services.lead_radar.repository import SQLLeadRepository
from app.services.lead_radar.db import metadata
from app.services.lead_radar.engine import LeadRadar
from app.services.lead_radar.models import RawLead
from app.services.lead_radar.classifier import _keyword_match, normalize


def _repo():
    e=create_engine('sqlite:///:memory:')
    # TEST_SCHEMA_FIXTURE_V1: production schema is migration-owned. Unit tests
    # create their isolated in-memory schema explicitly, then exercise the same
    # repository verification path without restoring runtime DDL bootstrap.
    metadata.create_all(e)
    r=SQLLeadRepository(e); r.install()
    return r


def _raw(owner, key, text):
    return RawLead(source='test',external_id=key,url='',title=text,text='',
                   published_at=datetime.now(timezone.utc),source_family='social',
                   owner_user_id=owner)


def test_missing_profile_fails_closed():
    r=_repo()
    with pytest.raises(RuntimeError,match='PROFILE_OR_OFFERINGS_REQUIRED'):
        LeadRadar(r,enforce_profile=True).process(_raw(99,'x','Куплю щебень'))


def test_tenant_catalog_isolation_and_products_services():
    r=_repo()
    r.upsert_profile(1,name='Строй',business_type='mixed',mode='generic',
                     intent_keywords=['куплю','ищу','нужна','аренда'],
                     negative_keywords=['продаю'],min_score=45)
    r.replace_offerings(1,[
        {'offering_code':1,'external_key':'gravel','kind':'product','name':'Щебень гранитный',
         'keywords':['щебень','гранитный щебень'],'negative_keywords':[]},
        {'offering_code':2,'external_key':'rent','kind':'service','name':'Аренда экскаватора',
         'keywords':['аренда экскаватора','экскаватор в аренду'],'negative_keywords':[]},
    ])
    r.upsert_profile(2,name='Мебель',business_type='products',mode='generic',
                     intent_keywords=['куплю','нужна'],min_score=45)
    r.replace_offerings(2,[
        {'offering_code':1,'external_key':'kitchen','kind':'product','name':'Кухня на заказ',
         'keywords':['кухня на заказ','кухонный гарнитур'],'negative_keywords':[]},
    ])

    a=LeadRadar(r,enforce_profile=True).process(_raw(1,'a','Куплю 100 тонн гранитного щебня'))
    b=LeadRadar(r,enforce_profile=True).process(_raw(2,'b','Куплю 100 тонн гранитного щебня'))
    c=LeadRadar(r,enforce_profile=True).process(_raw(1,'c','Ищу экскаватор в аренду на неделю'))

    assert a.status=='draft_ready' and a.service_ids==[1] and 'условия поставки' in a.reply
    assert b.status=='rejected' and b.service_ids==[]
    assert c.status=='draft_ready' and c.service_ids==[2] and 'по услуге' in c.reply


def test_seller_and_market_article_are_not_buyers():
    r=_repo()
    r.upsert_profile(7,name='Щебень',business_type='products',mode='generic',
                     intent_keywords=['куплю','нужен'],negative_keywords=['продаю'],min_score=45)
    r.replace_offerings(7,[{'offering_code':1,'external_key':'g','kind':'product','name':'Щебень',
                            'keywords':['щебень'],'negative_keywords':[]}])
    radar=LeadRadar(r,enforce_profile=True)
    seller=radar.process(_raw(7,'s','Продаю щебень с доставкой, звоните'))
    article=radar.process(_raw(7,'m','Щебень: обзор рынка и динамика цен'))
    assert seller.status=='rejected'
    assert article.status=='rejected'


def test_russian_inflection_and_short_token_guards():
    assert _keyword_match(normalize('Куплю гранитного щебня'),'гранитный щебень')
    assert _keyword_match(normalize('Нужна доставка песка карьерного'),'карьерный песок')
    assert not _keyword_match(normalize('Песня про лето'),'песок')
    assert not _keyword_match(normalize('случайный abc1cdef'),'1c')
    assert _keyword_match(normalize('нужен API'),'api')

def test_generic_tenant_does_not_inherit_global_sources(monkeypatch):
    from app.services.lead_radar.registry import build_connectors
    monkeypatch.setenv('LEAD_RADAR_HN_ENABLED','1')
    monkeypatch.setenv('LEAD_RADAR_OPEN_WEB_ENABLED','1')
    profile={
        'owner_user_id':77,'mode':'generic','enabled':True,
        'settings_json':{
            'inherit_global_sources':False,
            'rss_sources':[{'key':'client_feed','url':'https://example.org/feed.xml','family':'brief','group':'freelance'}],
            'open_web_enabled':False,
        }
    }
    cs=build_connectors(77,profile=profile,catalog=[{'offering_code':1,'name':'Щебень','keywords':['щебень']}])
    assert [(x.key,x.group_key) for x in cs]==[('client_feed','freelance')]

def test_development_quality_rejects_sales_mentor_and_wikipedia_side_jobs():
    from app.services.lead_radar.scoring import score
    from app.services.lead_radar.models import SignalFamily, RawLead
    now=datetime.now(timezone.utc)
    cases=[
        ('Setter + Closer B2B | Cold Calling | IA para Clínicas','CRM SaaS Automation Business Development Cold Calling'),
        ('AI & Web Dev Sales Partner','Prospecting qualifying closing finance leads for a web development studio'),
        ('SAP EWM Mentor Needed','Certification preparation and practical WMS scenarios; consultant / mentor'),
        ('Publish Scientific 3D Molecular Visualizations on Wikipedia','Wikimedia Commons publication; not paying for code'),
    ]
    for i,(title,body) in enumerate(cases):
        raw=RawLead(source='freelancer',external_id=f'nondev-{i}',url='',title=title,text=body,
                    published_at=now,source_family='brief',owner_user_id=2)
        sc,reasons=score(raw,[5,15,17],SignalFamily.READY_BRIEF,now=now,profile={'mode':'development'})
        assert sc<45, (title,sc,reasons)


def test_development_quality_keeps_real_software_build():
    from app.services.lead_radar.scoring import score
    from app.services.lead_radar.models import SignalFamily, RawLead
    now=datetime.now(timezone.utc)
    raw=RawLead(source='freelancer',external_id='dev-good',url='',
                title='Full-Stack Developer for AI Marketing Platform',
                text='Looking for an experienced developer to build and improve a SaaS platform.',
                published_at=now,source_family='brief',owner_user_id=2)
    sc,reasons=score(raw,[5],SignalFamily.HIRING,now=now,profile={'mode':'development'})
    assert sc>=45,(sc,reasons)


def test_api_owner_scope_is_authenticated_tenant_not_background_env(monkeypatch):
    from app.services.lead_radar.owner_scope import assert_owner_scope
    monkeypatch.setenv('LEAD_RADAR_OWNER_USER_ID','2')
    assert assert_owner_scope(77)==77
    with pytest.raises(PermissionError,match='OWNER_SCOPE_MISMATCH'):
        assert_owner_scope(77,configured_value=2)
    with pytest.raises(PermissionError,match='AUTHENTICATED_OWNER_REQUIRED'):
        assert_owner_scope(0)

def test_html_encoded_ruble_budget_is_real_marketplace_budget():
    from app.services.lead_radar.signals import parse_budget
    assert parse_budget('Ищу Tilda специалиста (Бюджет: 10 000 &#8381;)')==(10000,10000)
    assert parse_budget('Правки лендинга (Бюджет: 900 &#8381;)')==(900,900)


def test_explicit_budget_below_catalog_floor_is_rejected_and_preserved():
    r=_repo()
    r.upsert_profile(81,name='Разработка',business_type='services',mode='development',min_score=45)
    r.replace_offerings(81,[{
        'offering_code':2,'external_key':'website','kind':'service','name':'Сайт / Tilda',
        'keywords':['tilda','сайт'],'negative_keywords':[],
        'price_min_rub':70000,'price_max_rub':180000,
    }])
    raw=RawLead(
        source='freelance',external_id='cheap-tilda',url='',
        title='Ищу специалиста по Tilda для доработки сайта (Бюджет: 10 000 &#8381;)',
        text='Нужно доработать и пересобрать существующий сайт.',
        published_at=datetime.now(timezone.utc),source_family='brief',owner_user_id=81,
    )
    lead=LeadRadar(r,enforce_profile=True).process(raw)
    assert lead.status=='rejected'
    assert lead.score<45
    assert (lead.budget_min_rub,lead.budget_max_rub)==(10000,10000)
    assert any('бюджет ниже' in x for x in lead.reasons)


def test_russian_copywriter_title_is_not_development_lead():
    r=_repo()
    r.upsert_profile(82,name='Разработка',business_type='services',mode='development',min_score=45)
    r.replace_offerings(82,[{
        'offering_code':2,'external_key':'website','kind':'service','name':'Сайты',
        'keywords':['сайт','1с','битрикс24','интеграция'],'negative_keywords':[],
        'price_min_rub':70000,'price_max_rub':180000,
    }])
    raw=RawLead(
        source='freelance',external_id='copywriter',url='',
        title='Ищем копирайтера для написания продающих текстов на сайт IT-компании',
        text='Нужно написать тексты для страниц услуг: 1С, Битрикс24, интеграция 1С.',
        published_at=datetime.now(timezone.utc),source_family='brief',owner_user_id=82,
    )
    lead=LeadRadar(r,enforce_profile=True).process(raw)
    assert lead.status=='rejected'
    assert lead.score<45
    assert any('другую профессию' in x for x in lead.reasons)

def test_requalify_updates_legacy_budget_and_rejects_underpriced_active_lead():
    from sqlalchemy import text
    from app.services.lead_radar.scoring import QUALITY_VERSION
    r=_repo()
    r.upsert_profile(83,name='Разработка',business_type='services',mode='development',min_score=45)
    r.replace_offerings(83,[{
        'offering_code':2,'external_key':'website','kind':'service','name':'Сайт / Tilda',
        'keywords':['tilda','сайт'],'negative_keywords':[],
        'price_min_rub':70000,'price_max_rub':180000,
    }])
    r.ensure_source('freelance','brief',owner_user_id=83)
    raw=RawLead(
        source='freelance',external_id='legacy-cheap',url='',
        title='Ищу специалиста по Tilda (Бюджет: 10 000 &#8381;)',
        text='Нужно доработать существующий сайт.',
        published_at=datetime.now(timezone.utc),source_family='brief',owner_user_id=83,
    )
    LeadRadar(r,enforce_profile=True).process(raw)
    lead_id=r.list_leads(limit=10,owner_user_id=83)[0]['id']
    with r.engine.begin() as c:
        c.execute(text("""
            update lead_radar_leads
            set status='draft_ready', quality_version='demand_gate_v5',
                budget_min_rub=70000, budget_max_rub=180000
            where id=:id
        """),{'id':lead_id})
    out=LeadRadar(r,enforce_profile=True).requalify_active_unsent(83)
    row=r.list_leads(limit=10,owner_user_id=83)[0]
    assert out['checked']==1 and out['rejected']==1
    assert row['status']=='rejected'
    assert row['quality_version']==QUALITY_VERSION
    assert (row['budget_min_rub'],row['budget_max_rub'])==(10000,10000)

def test_design_only_titles_are_not_development_orders_even_when_product_mentions_code():
    from app.services.lead_radar.scoring import score
    from app.services.lead_radar.models import SignalFamily, RawLead
    now=datetime.now(timezone.utc)
    cases=[
        ('Brand & Product Designer for a Live Campus Social App',
         'The product works. We need a real brand and coherent design language. Strong in Figma. No coding is required.',[5]),
        ('Senior Product Designer / Art Director — Full Stan Redesign',
         'Redesign product UI, website and design system. Deliver Figma files and launch creatives.',[9]),
        ('Web Designer Needed for GigResource Refinement',
         'Responsive design for an existing website. Deliver component design system and Figma mockups.',[1]),
    ]
    for i,(title,body,ids) in enumerate(cases):
        raw=RawLead(source='freelancer',external_id=f'design-only-{i}',url='',title=title,text=body,
                    published_at=now,source_family='brief',owner_user_id=2)
        sc,reasons=score(raw,ids,SignalFamily.READY_BRIEF,now=now,profile={'mode':'development'})
        assert sc<45,(title,sc,reasons)
        assert any('другую профессию' in x for x in reasons),(title,reasons)

def test_requalify_covers_registered_tender_family_not_only_brief_sources():
    from sqlalchemy import text
    from app.services.lead_radar.scoring import QUALITY_VERSION
    r=_repo()
    r.upsert_profile(84,name='Разработка',business_type='services',mode='development',min_score=45)
    r.replace_offerings(84,[{
        'offering_code':5,'external_key':'saas','kind':'service','name':'SaaS / Web platform',
        'keywords':['saas','web platform','software development'],'negative_keywords':[],
        'price_min_rub':180000,'price_max_rub':600000,
    }])
    r.ensure_source('eu_ted','tender',owner_user_id=84)
    raw=RawLead(
        source='eu_ted',external_id='legacy-tender',url='',
        title='Software development services for SaaS web platform',
        text='Tender requires development and maintenance of a cloud web platform.',
        published_at=datetime.now(timezone.utc),source_family='tender',owner_user_id=84,
    )
    LeadRadar(r,enforce_profile=True).process(raw)
    lead_id=r.list_leads(limit=10,owner_user_id=84)[0]['id']
    with r.engine.begin() as c:
        c.execute(text("""
            update lead_radar_leads
            set status='qualified', quality_version=''
            where id=:id
        """),{'id':lead_id})
    out=LeadRadar(r,enforce_profile=True).requalify_active_unsent(84)
    row=r.list_leads(limit=10,owner_user_id=84)[0]
    assert out['checked']==1
    assert row['quality_version']==QUALITY_VERSION

def test_russian_design_only_title_rejected_but_development_plus_design_kept():
    from app.services.lead_radar.scoring import score
    from app.services.lead_radar.models import SignalFamily, RawLead
    now=datetime.now(timezone.utc)
    design=RawLead(source='freelance',external_id='design-ru',url='',
        title='Дизайн сайта платежного сервиса',
        text='Нужно сделать дизайн сайта финтех-продукта. Есть готовое ТЗ и прототипы.',
        published_at=now,source_family='brief',owner_user_id=2)
    s1,r1=score(design,[2,18],SignalFamily.READY_BRIEF,now=now,profile={'mode':'development'})
    assert s1<45 and any('только на дизайн' in x for x in r1)
    build=RawLead(source='freelance',external_id='design-build-ru',url='',
        title='Разработка и дизайн сайта платежного сервиса',
        text='Нужно разработать сайт и сверстать готовый дизайн.',
        published_at=now,source_family='brief',owner_user_id=2)
    s2,r2=score(build,[2,18],SignalFamily.READY_BRIEF,now=now,profile={'mode':'development'})
    assert s2>=45 and not any('только на дизайн' in x for x in r2)


def test_social_agency_ad_is_not_customer_demand():
    from app.services.lead_radar.scoring import score
    from app.services.lead_radar.models import SignalFamily, RawLead
    now=datetime.now(timezone.utc)
    raw=RawLead(source='boris_telegram',external_id='agency-ad',url='',
        title='QWORK - Биржа Специалистов',
        text='Всем привет! Представляю команду Digital Dev. Мы помогаем предпринимателям. Что мы разрабатываем: сайты, боты и автоматизацию. Напишите мне в личные сообщения.',
        published_at=now,source_family='social',owner_user_id=2)
    sc,reasons=score(raw,[1,2,11,26],SignalFamily.READY_BRIEF,now=now,profile={'mode':'development'})
    assert sc<45
    assert any('самореклама' in x for x in reasons)


def test_betting_and_sportsbook_projects_are_not_target_leads():
    from app.services.lead_radar.scoring import score
    from app.services.lead_radar.models import SignalFamily, RawLead
    now=datetime.now(timezone.utc)
    for i,(title,body) in enumerate([
        ('Cricket Betting Market Analysis Web App','Need a developer to integrate bookmaker odds and detect value bet opportunities.'),
        ('Fixed odds sportsbook regime detection','Need sportsbook and bookmaker market intelligence software.'),
    ]):
        raw=RawLead(source='freelancer',external_id=f'bet-{i}',url='',title=title,text=body,
                    published_at=now,source_family='brief',owner_user_id=2)
        sc,reasons=score(raw,[5,17,26],SignalFamily.READY_BRIEF,now=now,profile={'mode':'development'})
        assert sc<45,(title,sc,reasons)
        assert any('рисковая/регулируемая' in x for x in reasons)

def test_health_detects_quality_backlog_and_clears_after_self_heal():
    from sqlalchemy import text
    from app.services.lead_radar.health import health
    from app.services.lead_radar.scoring import QUALITY_VERSION
    r=_repo()
    r.upsert_profile(85,name='Разработка',business_type='services',mode='development',min_score=45)
    r.replace_offerings(85,[{
        'offering_code':5,'external_key':'saas','kind':'service','name':'SaaS',
        'keywords':['saas','web platform'],'negative_keywords':[],
        'price_min_rub':180000,'price_max_rub':600000,
    }])
    r.ensure_source('eu_ted','tender',owner_user_id=85)
    raw=RawLead(source='eu_ted',external_id='health-old',url='',
        title='SaaS web platform development tender',
        text='Need development of a SaaS web platform.',
        published_at=datetime.now(timezone.utc),source_family='tender',owner_user_id=85)
    LeadRadar(r,enforce_profile=True).process(raw)
    lead_id=r.list_leads(limit=10,owner_user_id=85)[0]['id']
    with r.engine.begin() as c:
        c.execute(text("update lead_radar_leads set status='qualified', quality_version='old' where id=:id"),{'id':lead_id})
    before=health(r.engine,owner_user_id=85)
    assert before['status']=='DEGRADED'
    assert before['quality_recheck_pending']==1
    assert before['quality_version_current']==QUALITY_VERSION
    assert before['self_heal_active'] is True
    assert before['owner_action_required'] is False
    LeadRadar(r,enforce_profile=True).requalify_active_unsent(85)
    after=health(r.engine,owner_user_id=85)
    assert after['quality_recheck_pending']==0
    assert after['status']=='PASS'

def test_sales_leadgen_role_is_not_software_project():
    from app.services.lead_radar.scoring import score
    from app.services.lead_radar.models import SignalFamily, RawLead
    now=datetime.now(timezone.utc)
    raw=RawLead(
        source='freelancer',external_id='sales-role',url='',
        title='B2B Lead Generation Specialist Needed',
        text='Looking for an experienced B2B Lead Generation Specialist / Sales Representative to acquire client projects for our software development company.',
        published_at=now,source_family='brief',owner_user_id=2,
    )
    sc,reasons=score(raw,[5],SignalFamily.READY_BRIEF,now=now,profile={'mode':'development'})
    assert sc<45,(sc,reasons)
    assert any('другую профессию' in x for x in reasons)


def test_testing_only_project_is_not_development_order_but_testing_plus_build_is_kept():
    from app.services.lead_radar.scoring import score
    from app.services.lead_radar.models import SignalFamily, RawLead
    now=datetime.now(timezone.utc)
    testing=RawLead(
        source='freelancer',external_id='test-only',url='',
        title='Android App Testing – 15 Testers',
        text='Need everyday users to test the app and send four weekly feedback submissions. No professional QA background needed.',
        published_at=now,source_family='brief',owner_user_id=2,
    )
    s1,r1=score(testing,[7,9],SignalFamily.READY_BRIEF,now=now,profile={'mode':'development'})
    assert s1<45,(s1,r1)
    assert any('только на тестирование' in x for x in r1)
    build=RawLead(
        source='freelancer',external_id='test-and-build',url='',
        title='Develop Android App Testing Automation',
        text='Need a developer to build automated Android app testing infrastructure.',
        published_at=now,source_family='brief',owner_user_id=2,
    )
    s2,r2=score(build,[7,9],SignalFamily.READY_BRIEF,now=now,profile={'mode':'development'})
    assert s2>=45,(s2,r2)


def test_contract_staff_role_is_not_project_procurement():
    from app.services.lead_radar.scoring import score
    from app.services.lead_radar.models import SignalFamily, RawLead
    now=datetime.now(timezone.utc)
    raw=RawLead(
        source='freelancer',external_id='staff-role',url='',
        title='Lead EPM Architect Contract Role',
        text='Onboarding a senior Oracle EPM Solution Architect for an on-site contract role. Qualification is non-negotiable. If you are available to mobilise quickly, let us talk.',
        published_at=now,source_family='brief',owner_user_id=2,
    )
    sc,reasons=score(raw,[17],SignalFamily.READY_BRIEF,now=now,profile={'mode':'development'})
    assert sc<45,(sc,reasons)
    assert any('контрактная роль' in x for x in reasons)

def test_content_maker_for_site_is_not_development_order():
    from app.services.lead_radar.scoring import score
    from app.services.lead_radar.models import SignalFamily, RawLead
    now=datetime.now(timezone.utc)
    raw=RawLead(source='freelance',external_id='content-maker',url='',
        title='Нужен контент-мейкер для сайта (заполнение контентом)',
        text='Ищем человека для постинга, заполнения контентом и создания статей.',
        published_at=now,source_family='brief',owner_user_id=2)
    sc,reasons=score(raw,[2,7],SignalFamily.READY_BRIEF,now=now,profile={'mode':'development'})
    assert sc<45 and any('другую профессию' in x for x in reasons)


def test_store_screenshots_and_ai_trainer_are_not_development_orders():
    from app.services.lead_radar.scoring import score
    from app.services.lead_radar.models import SignalFamily, RawLead
    now=datetime.now(timezone.utc)
    cases=[
        ('Minimalist App Store Screenshots','Need 5-7 screenshots for iPhone, iPad and Android. Deliver Figma and PNG files.',[7,9]),
        ('AI Trainer','Train our graphics team on AI design and creative tools through video calls.',[26]),
    ]
    for i,(title,body,ids) in enumerate(cases):
        raw=RawLead(source='freelancer',external_id=f'adjacent-{i}',url='',title=title,text=body,
                    published_at=now,source_family='brief',owner_user_id=2)
        sc,reasons=score(raw,ids,SignalFamily.READY_BRIEF,now=now,profile={'mode':'development'})
        assert sc<45,(title,sc,reasons)
        assert any('другую профессию' in x for x in reasons),(title,reasons)


def test_app_promotion_title_is_not_mobile_development_order():
    from app.services.lead_radar.scoring import score
    from app.services.lead_radar.models import SignalFamily, RawLead
    now=datetime.now(timezone.utc)
    raw=RawLead(source='freelancer',external_id='app-promo-cn',url='',
        title='APP推广拉新方案执行',
        text='需要制定推广流程、渠道策略、广告投放和下载转化优化。 Skills: Mobile App Development, Android, Advertising.',
        published_at=now,source_family='brief',owner_user_id=2)
    sc,reasons=score(raw,[7,9],SignalFamily.READY_BRIEF,now=now,profile={'mode':'development'})
    assert sc<45,(sc,reasons)
    assert any('другую профессию' in x for x in reasons)


def test_game_testing_only_is_rejected():
    from app.services.lead_radar.scoring import score
    from app.services.lead_radar.models import SignalFamily, RawLead
    now=datetime.now(timezone.utc)
    raw=RawLead(source='freelancer',external_id='game-test',url='',
        title='Android Game Testing - 12 Testers for 14 Days',
        text='Need 12 genuine Android testers to install the app, remain opted in and provide feedback.',
        published_at=now,source_family='brief',owner_user_id=2)
    sc,reasons=score(raw,[7,9],SignalFamily.READY_BRIEF,now=now,profile={'mode':'development'})
    assert sc<45 and any('только на тестирование' in x for x in reasons)


def test_founder_partnership_is_not_client_project():
    from app.services.lead_radar.scoring import score
    from app.services.lead_radar.models import SignalFamily, RawLead
    now=datetime.now(timezone.utc)
    raw=RawLead(source='freelancer',external_id='founder-partner',url='',
        title='Intermediate Backend Developer Partners Wanted',
        text="I'm forming a small founding team and want a business partner. This is a partnership, not a one-off contract.",
        published_at=now,source_family='brief',owner_user_id=2)
    sc,reasons=score(raw,[5,17],SignalFamily.READY_BRIEF,now=now,profile={'mode':'development'})
    assert sc<45 and any('некоммерческий' in x for x in reasons)


def test_ux_revamp_with_frontend_implementation_stays_valid():
    from app.services.lead_radar.scoring import score
    from app.services.lead_radar.models import SignalFamily, RawLead
    now=datetime.now(timezone.utc)
    raw=RawLead(source='freelancer',external_id='ux-plus-code',url='',
        title='Web App UX Design Revamp',
        text='Redesign the web interface and implement the approved design as production-ready frontend code in the existing codebase.',
        published_at=now,source_family='brief',owner_user_id=2)
    sc,reasons=score(raw,[5],SignalFamily.READY_BRIEF,now=now,profile={'mode':'development'})
    assert sc>=45,(sc,reasons)

def test_short_russian_fuzzy_does_not_turn_building_class_into_cash_register():
    from app.services.lead_radar.classifier import normalize,_keyword_match,classify
    building=normalize('Разработка проекта ВПВ. Производственный корпус. Класс ФПО здания ф5.1')
    assert _keyword_match(building,'касса',building.split()) is False
    assert 18 not in classify('Разработка проекта ВПВ. Производственный корпус. Класс ФПО здания ф5.1')
    assert 18 in classify('Нужно подключить кассу и биллинг к существующей системе')


def test_ai_operator_is_explicit_development_demand():
    r=_repo()
    r.upsert_profile(86,name='Разработка',business_type='services',mode='development',min_score=45)
    r.replace_offerings(86,[{
        'offering_code':13,'external_key':'ai_operator','kind':'service','name':'AI оператор',
        'keywords':['ии оператор','llm','битрикс24'],'negative_keywords':[],
        'price_min_rub':60000,'price_max_rub':250000,
    }])
    r.ensure_source('freelance','brief',owner_user_id=86)
    raw=RawLead(source='freelance',external_id='ai-clinic',url='',
        title='Нужен ИИ-оператор для клиники на Bitrix24',
        text='LLM консультирует пациента, предлагает свободные слоты и записывает на визит.',
        published_at=datetime.now(timezone.utc),source_family='brief',owner_user_id=86)
    lead=LeadRadar(r,enforce_profile=True).process(raw)
    assert lead.status!='rejected'
    assert 13 in lead.service_ids
    assert lead.score>=45


def test_excel_file_import_java_is_classified_as_data_migration():
    r=_repo()
    r.upsert_profile(87,name='Разработка',business_type='services',mode='development',min_score=45)
    r.replace_offerings(87,[{
        'offering_code':24,'external_key':'excel_import','kind':'service','name':'Excel import',
        'keywords':['excel file import','csv file import','apache poi'],'negative_keywords':[],
        'price_min_rub':40000,'price_max_rub':180000,
    }])
    r.ensure_source('freelancer','brief',owner_user_id=87)
    raw=RawLead(source='freelancer',external_id='excel-java',url='',
        title='Implement Excel File Import and Data Analysis Features',
        text='I need a skilled developer to implement Excel File Import using Apache POI, then CSV import and charts.',
        published_at=datetime.now(timezone.utc),source_family='brief',owner_user_id=87)
    lead=LeadRadar(r,enforce_profile=True).process(raw)
    assert lead.status!='rejected'
    assert 24 in lead.service_ids


def test_building_vpv_and_qa_lead_are_not_software_orders():
    from app.services.lead_radar.scoring import score
    from app.services.lead_radar.models import SignalFamily
    now=datetime.now(timezone.utc)
    building=RawLead(source='freelance',external_id='vpv',url='',
        title='Разработка проекта ВПВ',
        text='Вспомогательный производственный корпус площадью 1500 квм. Класс ФПО здания ф5.1.',
        published_at=now,source_family='brief',owner_user_id=2)
    s1,r1=score(building,[18],SignalFamily.READY_BRIEF,now=now,profile={'mode':'development'})
    assert s1<45 and any(('строительное проектирование' in x) or ('инженерное/промышленное проектирование' in x) for x in r1)
    qa=RawLead(source='freelancer',external_id='qa-cv',url='',
        title='QA Lead for Educational Platforms',
        text='Led day-to-day QA activities, analyzed user stories and executed release regression.',
        published_at=now,source_family='brief',owner_user_id=2)
    s2,r2=score(qa,[7,35],SignalFamily.READY_BRIEF,now=now,profile={'mode':'development'})
    assert s2<45 and any('контрактная роль' in x for x in r2)


def test_recent_false_negative_is_revived_by_new_quality_version():
    from app.services.lead_radar.db import leads
    from app.services.lead_radar.health import health
    from app.services.lead_radar.scoring import QUALITY_VERSION
    r=_repo()
    r.upsert_profile(88,name='Разработка',business_type='services',mode='development',min_score=45)
    r.replace_offerings(88,[{
        'offering_code':24,'external_key':'excel_import','kind':'service','name':'Excel import',
        'keywords':['excel file import','apache poi'],'negative_keywords':[],
        'price_min_rub':40000,'price_max_rub':180000,
    }])
    r.ensure_source('freelancer','brief',owner_user_id=88)
    raw=RawLead(source='freelancer',external_id='revive-excel',url='',
        title='Implement Excel File Import',
        text='Need a developer to implement Excel File Import using Apache POI.',
        published_at=datetime.now(timezone.utc),source_family='brief',owner_user_id=88)
    LeadRadar(r,enforce_profile=True).process(raw)
    row=r.list_leads(limit=10,owner_user_id=88)[0]
    with r.engine.begin() as c:
        c.execute(leads.update().where(leads.c.id==row['id']).values(
            status='rejected',quality_version='old',service_ids=[],score=0,
            contact_mode='none',contact_reason='old_false_negative',reply_text=None
        ))
    before=health(r.engine,owner_user_id=88)
    assert before['quality_recheck_pending']==1
    out=LeadRadar(r,enforce_profile=True).requalify_active_unsent(88)
    after=r.list_leads(limit=10,owner_user_id=88)[0]
    assert out['revived']==1
    assert after['status']!='rejected'
    assert after['quality_version']==QUALITY_VERSION
    assert 24 in after['service_ids']
    assert health(r.engine,owner_user_id=88)['quality_recheck_pending']==0

def test_crm_restore_deal_reopens_same_lost_deal_idempotently():
    from sqlalchemy import create_engine,text
    from app.services.lead_radar.crm_adapter import BorisCRMAdapter
    e=create_engine('sqlite://')
    with e.begin() as c:
        c.execute(text("""create table boris_crm_stages(
            id integer primary key,pipeline_id integer,position integer,semantic_type text
        )"""))
        c.execute(text("""create table boris_crm_deals(
            id integer primary key,owner_user_id integer,pipeline_id integer,contact_id integer,
            status text,source text,stage_id integer,lost_reason text,last_activity_at text,updated_at text
        )"""))
        c.execute(text("""create table boris_crm_activities(
            id integer primary key autoincrement,owner_user_id integer,deal_id integer,contact_id integer,
            activity_type text,channel text,direction text,title text,body text,source text,source_ref text,
            actor_type text,metadata_json text,created_at text
        )"""))
        c.execute(text("insert into boris_crm_stages(id,pipeline_id,position,semantic_type) values(10,1,1,'new'),(20,1,9,'lost')"))
        c.execute(text("insert into boris_crm_deals(id,owner_user_id,pipeline_id,contact_id,status,source,stage_id,lost_reason) values(7,1,1,null,'lost','lead_radar',20,'old quality')"))
    crm=BorisCRMAdapter(e)
    assert crm.restore_deal(7,1,55,'demand_gate_test') is True
    assert crm.restore_deal(7,1,55,'demand_gate_test') is True
    with e.begin() as c:
        deal=dict(c.execute(text("select id,status,stage_id,lost_reason from boris_crm_deals where id=7")).mappings().one())
        events=c.execute(text("select count(*) from boris_crm_activities where deal_id=7 and source_ref='quality_restore:55:demand_gate_test'")).scalar_one()
    assert deal=={'id':7,'status':'open','stage_id':10,'lost_reason':None}
    assert events==1


def test_false_negative_dev_keywords_are_recognized_without_reopening_adjacent_noise():
    from app.services.lead_radar.classifier import classify
    from app.services.lead_radar.catalog import SERVICE_CATALOG
    good={
        'qr': 'Need a web-based tool with modern JavaScript, backend development and frontend development for QR-code sticker printing.',
        'wp': 'Need WordPress speed and website optimization with PageSpeed, Nginx and performance tuning.',
        'fullstack': 'Нужен фуллстек на React + NestJS, реализовывать задачи в существующих проектах.',
        'chain': 'Need a senior engineer to design and code Ethereum smart contract logic in Solidity.',
    }
    for name,text_value in good.items():
        ids=classify(text_value,catalog=SERVICE_CATALOG)
        assert ids, (name,ids)
    assert 5 in classify(good['qr'],catalog=SERVICE_CATALOG)
    assert 34 in classify(good['wp'],catalog=SERVICE_CATALOG)
    assert 5 in classify(good['fullstack'],catalog=SERVICE_CATALOG)
    assert 5 in classify(good['chain'],catalog=SERVICE_CATALOG)


def test_rejected_false_negative_can_revive_after_catalog_expansion():
    from sqlalchemy import text
    from app.services.lead_radar.scoring import QUALITY_VERSION
    r=_repo()
    r.upsert_profile(86,name='Разработка',business_type='services',mode='development',min_score=45)
    r.replace_offerings(86,[{
        'offering_code':5,'external_key':'web_service','kind':'service','name':'Web-сервис',
        'keywords':['react','nestjs','fullstack'],'negative_keywords':[],
        'price_min_rub':180000,'price_max_rub':600000,
    }])
    r.ensure_source('freelance','brief',owner_user_id=86)
    raw=RawLead(source='freelance',external_id='revive-dev',url='',
        title='нужен фуллстек на react + nestjs на поток мелких задач',
        text='есть несколько проектов, нужно реализовывать задачи и пушить в git',
        published_at=datetime.now(timezone.utc),source_family='brief',owner_user_id=86)
    LeadRadar(r,enforce_profile=True).process(raw)
    row=r.list_leads(limit=10,owner_user_id=86)[0]
    with r.engine.begin() as c:
        c.execute(text("update lead_radar_leads set status='rejected', quality_version='old', service_ids='[]', score=10 where id=:id"),{'id':row['id']})
    out=LeadRadar(r,enforce_profile=True).requalify_active_unsent(86)
    row=r.list_leads(limit=10,owner_user_id=86)[0]
    assert out['revived']==1,out
    assert row['status'] in ('qualified','draft_ready','ready_to_contact')
    assert row['quality_version']==QUALITY_VERSION
    assert 5 in row['service_ids']


def test_general_web_and_multilingual_dev_recall_without_design_regression():
    from app.services.lead_radar.classifier import classify
    from app.services.lead_radar.catalog import SERVICE_CATALOG
    from app.services.lead_radar.scoring import score
    from app.services.lead_radar.models import SignalFamily
    now=datetime.now(timezone.utc)
    cases=[
        ('Simple Website Development Project Needed','I need a developer to build a functioning website on WordPress with responsive front-end coding.',2),
        ('App Linux con Python y C++','Necesito crear una aplicación de escritorio para Linux cuya lógica principal esté escrita en Python, con módulos en C++.',5),
        ('Sviluppo applicazione gestionale','Cerco sviluppo di una applicazione software con backend e API.',5),
    ]
    for i,(title,body,expected) in enumerate(cases):
        ids=classify(title+' '+body,catalog=SERVICE_CATALOG)
        assert expected in ids,(title,ids)
        raw=RawLead(source='freelancer',external_id=f'general-dev-{i}',url='',title=title,text=body,
                    published_at=now,source_family='brief',owner_user_id=2)
        sc,reasons=score(raw,ids,SignalFamily.READY_BRIEF,now=now,budget=(200000,300000),profile={'mode':'development','min_score':45})
        assert sc>=45,(title,sc,reasons)
    raw=RawLead(source='freelancer',external_id='clinic-coded',url='',title='Clinic Website Design',
        text='I need this site built, fully designed and coded, with a WordPress CMS back-end and deployment to the live domain.',
        published_at=now,source_family='brief',owner_user_id=2)
    ids=classify(raw.title+' '+raw.text,catalog=SERVICE_CATALOG)
    sc,reasons=score(raw,ids,SignalFamily.READY_BRIEF,now=now,budget=(80000,120000),profile={'mode':'development','min_score':45})
    assert 2 in ids and sc>=45,(ids,sc,reasons)
    assert not any('только на дизайн' in x for x in reasons)


def test_hardware_sales_and_articles_stay_rejected_even_with_software_keywords():
    from app.services.lead_radar.scoring import score
    from app.services.lead_radar.models import SignalFamily
    now=datetime.now(timezone.utc)
    cases=[
        ('Unique All-in-One AI-optimized PC Build','I need a hardware builder. I will handle all AI software myself — this job is hardware only.',[5],'hardware'),
        ('Freelance Telesales Agent for Digital Agency','Need a telesales agent to sell our website development services and cold call prospects.',[2],'другую профессию'),
        ('Software Development Technical Articles','Need 1500-word technical articles explaining software development concepts with code snippets.',[5],'другую профессию'),
    ]
    for i,(title,body,ids,needle) in enumerate(cases):
        raw=RawLead(source='freelancer',external_id=f'guard-{i}',url='',title=title,text=body,
                    published_at=now,source_family='brief',owner_user_id=2)
        sc,reasons=score(raw,ids,SignalFamily.READY_BRIEF,now=now,budget=(250000,400000),profile={'mode':'development','min_score':45})
        assert sc<45,(title,sc,reasons)
        assert any(needle in x for x in reasons),(title,reasons)
