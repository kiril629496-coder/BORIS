"""Canonical buyer pools and public-company gates shared by existing discovery."""
import re
from urllib.parse import urlparse

OWNER_BORIS_DISCOVERY_LABEL = 'Коммерческие Avito-ниши'
OWNER_BORIS_DISCOVERY_NICHES = tuple(x.strip() for x in '''пластиковые окна; алюминиевые окна; остекление балконов; натяжные потолки; кухни на заказ; корпусная мебель; шкафы купе; мягкая мебель; матрасы; двери; входные двери; межкомнатные двери; ворота; роллеты; заборы; навесы; металлоконструкции; лестницы; ремонт квартир; строительство домов; каркасные дома; бытовки; модульные здания; бани; кровельные работы; фасады; фундамент; электромонтаж; сантехника; отопление; кондиционеры; вентиляция; бурение скважин; септики; бетон; песок; щебень; грунт; тротуарная плитка; стройматериалы; автосервис; кузовной ремонт; детейлинг; шиномонтаж; автостекла; автозапчасти; контрактные двигатели; авторазбор; грузовой автосервис; спецтехника; аренда спецтехники; грузоперевозки; переезды; эвакуаторы; клининг; химчистка мебели; ремонт бытовой техники; ремонт телефонов; ремонт компьютеров; видеонаблюдение; охранные системы; вывоз мусора; демонтаж; благоустройство; ландшафтные работы; лазерная эпиляция; косметология; салоны красоты; массаж; стоматология; медицинские услуги; обучение; курсы; репетиторы; оборудование; торговое оборудование; холодильное оборудование; производственное оборудование; мебель для бизнеса; упаковка; полиграфия; рекламные конструкции; спецодежда; инструменты; промышленное оборудование'''.split(';'))

NON_BUYER_DOMAINS = frozenset({
    'pikabu.ru', 'poki.com', 'playhop.com', 'crazygames.com', 'lumpics.ru',
    'medportal.ru', 'dom.mail.ru', 'minzdrav.gov.ru', 'wikipedia.org',
    'dzen.ru', 'rutube.ru', 'youtube.com', 'hh.ru', 'avito.ru', 'ozon.ru',
    'wildberries.ru', '2gis.ru', 'zoon.ru', 'yell.ru', 'orgpage.ru',
    'franshiza.ru', 'topfranchise.ru', 'businessmens.ru', 'sravni.ru', 'rfranch.ru', 'beboss.ru',
})


def company_noise_reason(item):
    """Reject publisher pages even when their snippet contains buyer keywords."""
    url = str(item.get('website') or item.get('url') or '')
    domain = str(item.get('domain') or item.get('company_domain') or urlparse(url).hostname or '').lower().strip('.').removeprefix('www.')
    domains = {domain, str(urlparse(url).hostname or '').lower().rstrip('.')}
    if any(any(host == d or host.endswith('.' + d) for d in NON_BUYER_DOMAINS) or host.endswith('.gov.ru') for host in domains):
        return 'non_buyer_domain'
    path = urlparse(url).path.lower()
    if re.search(r'/(?:articles?|blog|news|wiki|games?|vacanc\w*|resumes?|forum)(?:/|$)', path):
        return 'content_page'
    title = ' '.join(str(item.get(k) or '') for k in ('name', 'title')).lower().replace('ё', 'е')
    if any(x in title for x in ('википед', 'энциклопед', 'играть онлайн', 'бесплатные игры', 'справочник', 'каталог компаний', 'каталог франшиз', 'подбор франшиз', 'рейтинг компаний', 'вакансии', 'резюме', 'как выбрать', 'как открыть', 'что такое', 'новости', 'форум', 'своими руками')):
        return 'content_or_directory'
    return None


def niche_company_relevant(niche, item):
    if company_noise_reason(item):
        return False
    hay = ' '.join(str(item.get(k) or '') for k in ('title', 'name', 'snippet')).lower().replace('ё', 'е')
    words = [x for x in re.findall(r'[а-яa-z]+', niche.lower().replace('ё', 'е')) if len(x) > 3 and x not in {'заказ', 'компания', 'услуги', 'бизнеса'}]
    # Conservative stems match inflected company descriptions, not search-query tags.
    matches = sum(1 for w in words if (w[:-2] if len(w) > 6 else w[:-1]) in hay)
    commercial = any(x in hay for x in ('компан', 'контакт', 'заказ', 'продаж', 'производ', 'услуг', 'сеть', 'клиник', 'салон', 'запис', 'достав', 'ремонт', 'магазин', 'центр', 'завод', 'купить', 'цены'))
    return bool(words and matches >= min(2, len(words)) and commercial)


def boris_discovery_slot(cursor):
    cursor = max(0, int(cursor or 0))
    return OWNER_BORIS_DISCOVERY_NICHES[cursor % len(OWNER_BORIS_DISCOVERY_NICHES)], cursor // len(OWNER_BORIS_DISCOVERY_NICHES), cursor + 1


DEVELOPMENT_QUERY_SUFFIXES = ('официальный сайт', 'сеть филиалов официальный сайт', 'личный кабинет контакты')
