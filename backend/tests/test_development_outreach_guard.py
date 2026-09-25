from app.services.development_lead_radar import DEVELOPMENT_EMAIL_VARIANTS, _existing_company_fit_score
from app.services.prospect_campaigns import (
    _campaign_outreach_family,
    _development_outreach_banner,
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
    body = '''Добрый день!\n\nПосмотрел Example Logistics.\n\nГипотеза по типу бизнеса: заявки, маршруты, статусы и кабинет клиента. Требует подтверждения на первом контакте.\n\nМы занимаемся заказной разработкой SaaS-платформ и мобильных приложений.\n\nПодробнее: https://boris-ai.pro/software-dev/\n\nЕсли предложение не актуально, ответьте «не интересно» — адрес будет исключён из следующих обращений.\n\nКирилл'''
    banner, cid, _ = _development_outreach_banner(1)
    assert banner and cid
    html = _html_email(
        body, '', '', brand_subtitle='Разработка программных продуктов', brand_note='',
        marketing_banner_cid=cid,
        marketing_banner_href='https://boris-ai.pro/software-dev/',
        marketing_banner_alt='Разработка программных продуктов под бизнес',
    )
    assert _development_outreach_contract(subject, body, html, [banner]) == []
    assert f'cid:{cid}' in html


def test_development_contract_rejects_boris_product_copy_and_artwork():
    subject = 'По цифровой системе'
    body = '''Добрый день!\n\nЯ создал BORIS — виртуальную команду маркетинга.\nhttps://boris-ai.pro/go/boris\nhttps://boris-ai.pro/software-dev/\n\nЕсли предложение не актуально, ответьте «не интересно».'''
    html = _html_email(body, '', '')
    errors = _development_outreach_contract(subject, body, html, [{'path': '/tmp/a.png'}])
    assert any('development_forbidden_boris_copy' in e for e in errors)
    assert 'development_banner_invalid' in errors


def test_development_contract_requires_software_link_and_optout():
    errors = _development_outreach_contract(
        'По автоматизации процессов',
        'Добрый день! Занимаемся разработкой SaaS и приложений.',
        '<html>разработка SaaS</html>',
        [],
    )
    assert 'development_software_link_required' in errors
    assert 'development_optout_required' in errors



def test_development_six_copy_rotation_uses_new_landing_and_four_banners():
    assert [x['label'] for x in DEVELOPMENT_EMAIL_VARIANTS] == list('ABCDEF')
    assert len(DEVELOPMENT_EMAIL_VARIANTS) == 6
    landing = 'https://boris-ai.pro/software-dev/'
    filenames = []
    for i, variant in enumerate(DEVELOPMENT_EMAIL_VARIANTS, start=1):
        assert landing in variant['body']
        assert '/go/software' not in variant['body']
        banner, cid, _ = _development_outreach_banner(i)
        assert banner and cid
        filenames.append(banner['filename'])
        html = _html_email(
            variant['body'], '', '', brand_subtitle='Разработка программных продуктов', brand_note='',
            marketing_banner_cid=cid,
            marketing_banner_href=landing,
            marketing_banner_alt='Разработка программных продуктов под бизнес',
        )
        assert _development_outreach_contract(variant['subject'], variant['body'], html, [banner]) == []
        assert f'href="{landing}"' in html
    assert len(set(filenames[:4])) == 4
    cycle2 = [_development_outreach_banner(i)[0]['filename'] for i in range(5, 9)]
    cycle1 = [_development_outreach_banner(i)[0]['filename'] for i in range(1, 5)]
    assert cycle2 == cycle1


def test_development_campaign_bootstrap_persists_six_variants_not_empty_list():
    import inspect
    from app.services import development_lead_radar as radar
    src = inspect.getsource(radar._ensure_campaign)
    assert 'DEVELOPMENT_EMAIL_VARIANTS' in src
    assert "ab_variants='[]'::jsonb" not in src
    assert 'ab_variants=CAST(:ab AS JSONB)' in src
