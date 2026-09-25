from .catalog import SERVICE_CATALOG
from .models import SignalFamily

def _offering(lead,catalog=None):
    rows=catalog if catalog is not None else SERVICE_CATALOG
    wanted=set(int(x) for x in lead.service_ids or [])
    for row in rows:
        code=int(row.get('offering_code') or row.get('id') or 0)
        if code in wanted: return row
    return None

def build_reply(lead,catalog=None,profile=None):
    row=_offering(lead,catalog)
    svc=(row or {}).get('name') or 'нужное предложение'
    kind=str((row or {}).get('kind') or 'service').lower()
    mode=str((profile or {}).get('mode') or 'development').lower()
    company=lead.raw.company_name.strip(); title=lead.raw.title.strip()[:120]
    if mode!='development':
        if kind=='product':
            return (f'Здравствуйте! Увидел ваш запрос «{title}». У нас есть предложение «{svc}». '
                    'Можем быстро уточнить актуальную цену, наличие/объём, сроки и условия поставки. '
                    'Если запрос ещё актуален, напишите нужное количество и город/адрес.')
        return (f'Здравствуйте! Увидел ваш запрос «{title}». Мы можем помочь по услуге «{svc}». '
                'Коротко уточним объём задачи, желаемый результат и сроки, после чего дадим понятные условия и стоимость.')
    if lead.signal_family==SignalFamily.HIRING:
        return (f'Здравствуйте! Видим, что {company or "ваша компания"} усиливает команду под задачу «{title}». '
                f'Если потребность проектная, можем закрыть направление «{svc}» командой целиком без найма нескольких специалистов. '
                'Можем быстро оценить границы MVP, срок и бюджет по текущей задаче.')
    if lead.signal_family in (SignalFamily.PROBLEM,SignalFamily.BUSINESS_GAP):
        return (f'Здравствуйте! Посмотрели открытые данные по {company or "вашему проекту"} и заметили конкретный сигнал: «{title}». '
                f'Это похоже на задачу по направлению «{svc}». Можем сначала прислать короткий разбор: что именно автоматизировать/исправить, '
                'какой минимальный MVP нужен и какой будет диапазон стоимости — без обязательств.')
    if lead.signal_family in (SignalFamily.MONEY,SignalFamily.LAUNCH):
        return (f'Здравствуйте! Увидели свежий сигнал по {company or "вашему продукту"}: «{title}». '
                f'Если в roadmap есть «{svc}», можем быстро оценить MVP и взять реализацию целиком. '
                'Начнём с 3–5 требований и текущего стека/ссылки на продукт.')
    if lead.signal_family==SignalFamily.PARTNER:
        return (f'Здравствуйте! По вашему профилю вижу потенциальный партнёрский fit. Мы закрываем «{svc}» и смежную разработку white-label/overflow. '
                'Можно отдавать нам перегруженные технические задачи под вашим брендом; начнём с одного пилотного проекта.')
    return (f'Здравствуйте! Увидел ваш свежий запрос «{title}». Можем помочь по направлению «{svc}». '
            'Предлагаю коротко зафиксировать, что уже есть, какой результат нужен и ограничения по срокам/интеграциям. '
            'После этого дадим состав MVP, срок и понятный диапазон стоимости.')
