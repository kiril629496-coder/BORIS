from app.services.development_lead_radar import (
    _brief_outreach_ready,
    _company_brief,
    _existing_company_fit_score,
    _extract_site_evidence,
    _is_dev_vendor,
    _pain_hypothesis,
    _score_pain_evidence,
)
from app.services.prospect_campaigns import _development_brief_ready, _development_outreach_contract


def test_vendor_gate_rejects_software_agency_discovery():
    row = {
        'source': 'niche_discovery',
        'name': 'Разработка ПО на заказ, CRM и мобильных приложений',
        'domain': 'example-dev.ru',
        'website': 'https://example-dev.ru/',
        'search_query': 'Заказная разработка SaaS, мобильных приложений и автоматизации | официальный сайт',
    }
    assert _is_dev_vendor(row) is True
    assert _existing_company_fit_score(row)[0] == 0


def test_vendor_gate_allows_explicit_curated_buyer():
    row = {
        'source': 'development_curated',
        'name': 'Хеликс',
        'domain': 'helix.ru',
        'website': 'https://helix.ru/',
        'search_query': 'development-fit: медицинская сеть, франшиза, мобильное приложение и собственное программное обеспечение',
    }
    assert _is_dev_vendor(row) is False
    score, _vertical, _pain = _existing_company_fit_score(row)
    assert score >= 72


def test_verified_reserve_rejects_old_materials_noise():
    row = {
        'source': 'niche_discovery',
        'name': 'Продажа щебня и песка',
        'domain': 'nerud-example.ru',
        'website': 'https://nerud-example.ru/',
        'search_query': 'Сыпучие материалы Москва щебень песок',
    }
    assert _existing_company_fit_score(row)[0] == 0


def test_verified_reserve_accepts_logistics_buyer():
    row = {
        'source': 'development_curated',
        'name': 'Транспортная сеть',
        'domain': 'logistics-example.ru',
        'website': 'https://logistics-example.ru/',
        'search_query': 'development-fit: логистическая сеть, личный кабинет, маршруты и документы',
    }
    score, _vertical, pain = _existing_company_fit_score(row)
    assert score >= 72
    assert 'маршрут' in pain or 'заявк' in pain


def test_development_contract_rejects_boris_product_copy():
    subject='По цифровой системе для бизнеса'
    body='Я создал BORIS. Подробнее: https://boris-ai.pro/software-dev/\nЕсли предложение не актуально, ответьте «не интересно». Разработка ПО.'
    html='<html>https://boris-ai.pro/software-dev/</html>'
    errors=_development_outreach_contract(subject,body,html,[])
    assert any('development_forbidden_boris_copy' in x for x in errors)


def test_development_contract_requires_software_landing():
    subject='По автоматизации процессов'
    body='Разработка ПО для бизнеса. Если предложение не актуально, ответьте «не интересно».'
    html='<html></html>'
    errors=_development_outreach_contract(subject,body,html,[])
    assert 'development_software_link_required' in errors


def test_generic_replenisher_excludes_specialized_development_campaign():
    from pathlib import Path
    src=Path("app/services/prospect_replenisher.py").read_text(encoding="utf-8")
    assert "c.name <> 'Кирилл · разработка SaaS/App'" in src


def test_site_evidence_requires_real_process_signals():
    html = '''<html><body><h1>Сеть клиник</h1><p>У нас 12 филиалов.</p>
    <a href="/booking">Онлайн-запись</a><a href="/cabinet">Личный кабинет</a></body></html>'''
    evidence = _extract_site_evidence('https://clinic.example/', html)
    kinds = {x['kind'] for x in evidence}
    assert {'multi_location', 'online_request', 'client_cabinet'} <= kinds
    assert all(x['source_url'] == 'https://clinic.example/' for x in evidence)
    assert _score_pain_evidence(evidence) >= 55


def test_evidence_backed_company_brief_is_honest_and_sendable():
    evidence = [
        {'kind':'multi_location','fact':'На сайте описана сеть, филиалы или работа в нескольких точках.','source_url':'https://example.ru/','source_type':'company_site','strength':28},
        {'kind':'online_request','fact':'На сайте есть онлайн-запись, заявка или заказ.','source_url':'https://example.ru/','source_type':'company_site','strength':22},
        {'kind':'client_cabinet','fact':'На сайте есть личный кабинет или отдельный вход для клиента/партнёра.','source_url':'https://example.ru/cabinet','source_type':'company_site','strength':28},
    ]
    hypothesis, offer = _pain_hypothesis(evidence, 'медицина')
    analysis = {
        'analysis_status':'pass','evidence':evidence,'evidence_count':3,'site_evidence_count':3,
        'pain_score':86,'pain_confidence':'high','pain_hypothesis':hypothesis,'offer_angle':offer,
        'pages_analyzed':2,'analyzed_at':'2026-09-14T00:00:00+00:00',
    }
    brief = _company_brief(
        {'name':'Example Clinic','domain':'example.ru','website':'https://example.ru/'},
        'медицина','типовая боль не должна попасть в письмо',88,'info@example.ru',analysis=analysis,
    )
    assert brief['outreach_gate'] == 'pass'
    assert _brief_outreach_ready(brief) is True
    assert _development_brief_ready(brief) is True
    assert 'Посмотрел' not in brief['personal_first_message']
    assert 'два конкретных сигнала' in brief['personal_first_message']
    assert 'Не утверждаю' in brief['personal_first_message']


def test_company_brief_blocks_generic_vertical_guess_without_evidence():
    brief = _company_brief(
        {'name':'Example Logistics','domain':'example.ru','website':'https://example.ru/'},
        'логистика','заявки и маршруты',80,'info@example.ru',
        analysis={'analysis_status':'insufficient_evidence','evidence':[],'evidence_count':0,'site_evidence_count':0,'pain_score':0},
    )
    assert brief['outreach_gate'] == 'blocked'
    assert brief['personal_first_message'] == ''
    assert _brief_outreach_ready(brief) is False
    assert _development_brief_ready(brief) is False
