from app.services.development_lead_radar import _existing_company_fit_score
from app.services.prospect_campaigns import (
    _campaign_outreach_family,
    _development_outreach_contract,
    _html_email,
)


def test_development_curated_company_is_eligible():
    score, vertical, pain = _existing_company_fit_score({
        'name': 'Example Logistics',
        'domain': 'example-logistics.ru',
        'website': 'https://example-logistics.ru/',
        'source': 'development_curated',
        'search_query': 'development-fit: логистика, сеть пунктов, маршруты и личный кабинет',
    })
    assert score >= 72
    assert pain


def test_old_bulk_material_pool_is_never_reused_for_development():
    score, _, _ = _existing_company_fit_score({
        'name': 'Песок и щебень с доставкой',
        'domain': 'example-nerud.ru',
        'website': 'https://example-nerud.ru/',
        'source': 'web_seed',
        'search_query': 'сыпучие материалы песок щебень Москва',
    })
    assert score == 0


def test_offer_families_are_separate():
    assert _campaign_outreach_family({
        'id': 9, 'name': 'Кирилл · BORIS AI', 'niche': 'Сыпучие материалы',
        'account_id': '__owner_outreach__',
    }) == 'boris'
    assert _campaign_outreach_family({
        'id': 22, 'name': 'Кирилл · разработка SaaS/App',
        'niche': 'Заказная разработка SaaS, мобильных приложений и автоматизации',
        'account_id': None,
    }) == 'development'


def test_development_contract_accepts_clean_personalized_mail():
    subject = 'По цифровой системе Example Logistics'
    body = '''Добрый день!\n\nПосмотрел Example Logistics.\n\nГипотеза по типу бизнеса: заявки, маршруты, статусы и кабинет клиента. Требует подтверждения на первом контакте.\n\nМы занимаемся заказной разработкой SaaS-платформ и мобильных приложений.\n\nПодробнее: https://boris-ai.pro/go/software\n\nЕсли предложение не актуально, ответьте «не интересно» — адрес будет исключён из следующих обращений.\n\nКирилл'''
    html = _html_email(body, '', '', brand_subtitle='Разработка программных продуктов', brand_note='')
    assert _development_outreach_contract(subject, body, html, []) == []
    assert '<img' not in html.lower()


def test_development_contract_rejects_boris_product_copy_and_artwork():
    subject = 'По цифровой системе'
    body = '''Добрый день!\n\nЯ создал BORIS — виртуальную команду маркетинга.\nhttps://boris-ai.pro/go/boris\nhttps://boris-ai.pro/go/software\n\nЕсли предложение не актуально, ответьте «не интересно».'''
    html = _html_email(body, '', '')
    errors = _development_outreach_contract(subject, body, html, [{'path': '/tmp/a.png'}])
    assert any('development_forbidden_boris_copy' in e for e in errors)
    assert 'development_attachments_forbidden' in errors


def test_development_contract_requires_software_link_and_optout():
    errors = _development_outreach_contract(
        'По автоматизации процессов',
        'Добрый день! Занимаемся разработкой SaaS и приложений.',
        '<html>разработка SaaS</html>',
        [],
    )
    assert 'development_software_link_required' in errors
    assert 'development_optout_required' in errors
