from app.services.development_lead_radar import _is_dev_vendor, _existing_company_fit_score
from app.services.prospect_campaigns import _development_outreach_contract


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
