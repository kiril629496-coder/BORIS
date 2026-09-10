from app.services import prospect_campaigns as p


def test_orgpage_bulk_supported_is_niche_and_region_bounded():
    assert p._orgpage_bulk_supported('Сыпучие материалы', ['Москва']) is True
    assert p._orgpage_bulk_supported('Щебень', ['Москва']) is True
    assert p._orgpage_bulk_supported('Мобильные приложения', ['Москва']) is False
    assert p._orgpage_bulk_supported('Сыпучие материалы', ['Санкт-Петербург']) is False


def test_parse_orgpage_category_cards_dedupes_company_pages_only():
    html = '''
    <a href="/moskva/ekoscheben-6191194.html">3. Эко-Щебень</a>
    <a href="https://www.orgpage.ru/moskva/ekoscheben-6191194.html#news">новости</a>
    <a href="/otzivy/6191194.html">5 отзывов</a>
    <a href="/moskva/translompererabotka-6136273.html">2. ТрансломМаркет</a>
    '''
    rows = p._parse_orgpage_category_cards(html, 'moskva')
    assert [x['title'] for x in rows] == ['Эко-Щебень', 'ТрансломМаркет']
    assert rows[0]['detail_url'].endswith('/moskva/ekoscheben-6191194.html')


def test_parse_orgpage_detail_prefers_official_company_site_and_never_directory():
    html = '''
    <html><head><title>Эко-Щебень Москва - контакты</title>
      <meta name="description" content="Производство и продажа нерудных материалов" />
    </head><body>
      <h1>Эко-Щебень</h1>
      <div class="company-information__site-text">
        <a class="nofol-link" href="https://эко-щебень.рф/">эко-щебень.рф</a>
      </div>
      <a class="nofol-link" href="https://www.orgpage.ru/landing.html">каталог</a>
    </body></html>
    '''
    row = p._parse_orgpage_company_detail(
        html, 'https://www.orgpage.ru/moskva/ekoscheben-6191194.html'
    )
    assert row is not None
    assert row['domain'] == 'эко-щебень.рф'
    assert row['search_provider'] == 'orgpage_directory'
    assert 'нерудных материалов' in row['snippet']
    assert row['source_url'].startswith('https://www.orgpage.ru/')


def test_parse_orgpage_detail_fails_closed_without_external_site():
    html = '''<h1>Компания без сайта</h1><a href="https://www.orgpage.ru/about.html">OrgPage</a>'''
    assert p._parse_orgpage_company_detail(
        html, 'https://www.orgpage.ru/moskva/x-1.html'
    ) is None

def test_orgpage_bulk_cursor_rotates_verified_finite_pages():
    src = __import__('inspect').getsource(p._orgpage_bulk_directory_results)
    assert "'tsement':6" in src
    assert "'postavschiki-sypuchikh':2" in src
    assert "effective_page=1+(page_round % int(category_pages.get(cat,1)))" in src
    assert "f'https://www.orgpage.ru/moskva/{cat}/{effective_page}/'" in src
    assert "start=(raw_offset // len(categories)) % len(cards)" in src


def test_orgpage_bulk_includes_nerud_combines_category():
    src = __import__('inspect').getsource(p._orgpage_bulk_directory_results)
    assert "'kombinaty-nerudnykh-materialov'" in src
    assert "'kombinaty-nerudnykh-materialov':1" in src
