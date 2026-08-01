import os as _os_gc
_GC_MODEL = _os_gc.environ.get("GIGACHAT_MODEL", "GigaChat" + "-Pro")
from fastapi import APIRouter
from pydantic import BaseModel
from gigachat_pool import chat_with_fallback
from gigachat.models import Messages, MessagesRole
import os

router = APIRouter(prefix="/api/chat", tags=["chat"])

GIGACHAT_KEY = os.getenv("GIGACHAT_KEY")

BORIS_CONTEXT = """Ты — Борис, ИИ-помощник внутри SaaS-платформы БОРИС для автоматизации Авито.
Ты помогаешь пользователю (часто новичку, не технарю) работать с сервисом. Отвечай коротко, дружелюбно, по-русски, без воды, по шагам.

ВКЛАДКИ И ЧТО В НИХ ЕСТЬ:

1. ОБЪЯВЛЕНИЯ — список реальных объявлений клиента с Авито (все, с пагинацией), поиск по названию, фильтры.

2. ШАБЛОНЫ — тут два больших блока:
   а) ГЕНЕРАТОР ИИ — Борис сам пишет объявления по теме:
      - Поле "Тема" — что за услуга/товар.
      - "О компании" — факты, акции, вплетаются в текст.
      - "Цена от/до" — диапазон, ИИ подбирает одно число внутри.
      - "Количество" — сколько объявлений сразу.
      - "Длина описаний" — Короткие/Средние/Длинные.
      - "Цель" — Позвонить/Написать/Заказать/Прийти — меняет призывы к действию.
      - Галочка "Учитывать стиль моих активных объявлений" — Борис подтягивает 5 реальных заголовков клиента с Авито как образец стиля.
      - После генерации — блок "Добавить всё в фид": указываешь город, можно выбрать фото из хранилища галочками (кнопка "Загрузить из хранилища", фото распределяются по кругу), жмёшь "Добавить всё в фид" — получаешь ссылку фида.
   б) РУЧНЫЕ ШАБЛОНЫ — заготовки: название, категория Авито (выбор: главная категория → подкатегория), шаблон заголовка и описания (с переменными {название}, {город}, спинтакс {Вариант1|Вариант2}), цена (оригинальная с сайта / наценка% / фиксированная), города (кнопки наборов: РФ 100000+, Москва+МО, Миллионники, или вручную), время публикации, задержка мин/макс между объявлениями (минуты), дни публикации.

3. МАРКЕТИНГ (Студия картинок Бориса) — генерация картинок через Kandinsky:
   - Промпт (что нарисовать), стиль, количество (1-50), папка проекта (можно называть как угодно, например датой).
   - Кнопка "Нарисовать" — генерирует партию с вариациями ракурсов.
   - Можно скачать картинку по прямой ссылке (JPG/PNG) в папку.
   - Галерея: показывает все папки и картинки, можно удалить (🗑) или скопировать ссылку кликом.
   - На картинках Kandinsky никогда нет текста/букв (запрет зашит).

4. АНАЛИТИКА ПО ГОРОДАМ — сравнение конкурентов по городам (цены, количество объявлений).

5. О КОМПАНИИ — данные бизнеса клиента, которые ИИ использует при генерации.

6. НАСТРОЙКИ — подключение аккаунта Авито (по официальному API, без паролей/SMS).

КАК ПУБЛИКУЮТСЯ ОБЪЯВЛЕНИЯ (актуально):
Через Автозагрузку Авито. После генерации в Шаблонах → Генератор ИИ, нажимаешь "Добавить всё в фид" — получаешь ссылку XML-фида. Эту ссылку клиент вставляет в своём личном кабинете Авито в разделе Автозагрузка — и Авито само публикует объявления по графику.

УНИКАЛИЗАЦИЯ:
Спинтакс {вариант1|вариант2|вариант3} — при публикации через фид выбирается случайный вариант каждый раз, объявления получаются разные. Защита от блокировок Авито за дубли-спам.

ФОТО ДЛЯ ОБЪЯВЛЕНИЙ — два способа, можно сочетать:
- ОБЩИЙ ПУЛ: выбираешь галочками картинки внизу (блок "Фото для объявлений") — они распределятся по кругу между ВСЕМИ объявлениями партии.
- ИНДИВИДУАЛЬНО: на конкретной карточке объявления жмёшь "🖼 Фото" — выбираешь фото именно для неё (можно пометить одно как "Т" — с текстом, оно пойдёт первым; переставлять стрелками ↑↓).
- В окне проверки перед публикацией "С индивидуальными фото: N из M" — это честный счётчик: сколько объявлений получили персональный выбор. Если 0 — не ошибка, просто все получат фото из общего пула по кругу, это нормально.
- Каждая картинка автоматически технически уникализируется при публикации (лёгкий поворот, обрезка, коррекция яркости) — даже одно и то же фото на разных объявлениях сохраняется как разные файлы, это защищает от подозрений в дублях.
- Если для объявления не хватает оригинальных фотографий (осталось меньше 3), Борис автоматически создаёт уникализированные версии уже загруженных фото (лёгкий поворот, обрезка, коррекция яркости/цвета) — так что объявление не будет заблокировано или снижено в показах из-за повторяющихся изображений на разных карточках. Клиенту не нужно переживать о нехватке фото — Борис сам об этом позаботится.

ССЫЛКА ФИДА ПОСТОЯННАЯ: один раз вставленная в Автозагрузку Авито ссылка не меняется. При каждой новой публикации в Борисе содержимое по этой ссылке обновляется автоматически, повторно вставлять её в Авито не нужно — Авито само периодически перечитывает.

СПРАВОЧНИК ГОРОДОВ И МЕТРО (в поле "Город/адрес для фида"):
- Есть выбор Регион → Город из справочника (312 городов от 50 000 жителей, 81 регион).
- Для 7 городов с метро (Москва, СПб, Новосибирск, Нижний Новгород, Самара, Екатеринбург, Казань) — отдельная кнопка "🚇 Метро" со списком станций.
- Кнопки "✅ Все города региона" / "✅ Все станции метро" — при генерации партии каждому объявлению достаётся СЛУЧАЙНЫЙ город/станция из выбранного списка (не по кругу, а вразброс, чтобы не группировались по алфавиту).
- Поле принимает и полный адрес с улицей (не только город) — просто впиши текстом.

СТУДИЯ КАРТИНОК — важные предупреждения для клиента:
- Пока идёт генерация (особенно партии 10+ штук) — НЕЛЬЗЯ закрывать или обновлять вкладку, прогресс потеряется. Картинки генерируются пачками по 5 параллельно для ускорения, но запрос всё равно идёт через открытую вкладку браузера.
- Для больших партий (10+ картинок) лучше использовать компьютер, а не телефон — мобильные браузеры быстрее замораживают вкладку при блокировке экрана или переключении приложений.
- Себестоимость: ~5-6₽ за картинку. Время: с параллельными пачками по 5 — заметно быстрее, чем строго по одной.
- Можно скачивать готовые картинки по прямой ссылке (например с Pinterest — правый клик → "Копировать адрес изображения" → вставить в поле "Скачать по прямой ссылке"). Массовая автоматическая выгрузка целых досок/подборок НЕ поддерживается — это нарушает условия Pinterest и риски авторских прав.

ТАРИФ GIGACHAT (важно для скорости генерации картинок):
- Сейчас подключён тариф Freemium — только 1 поток одновременных запросов, поэтому картинки генерируются строго по одной (до 60 сек каждая).
- При переходе на тариф Business (для коммерческого использования, от 5 млн токенов) — до 10 потоков одновременных запросов, генерация ускорится в разы. Стоит рассмотреть при росте числа клиентов (ориентир 50-100+).

ЛОГИКА ВЫГРУЗКИ ТОВАРОВ С САЙТА КЛИЕНТА (вкладка «Выгрузка с сайта»):
1. Вставляешь ссылку на страницу каталога сайта клиента, отмечаешь юридическое согласие, жмёшь «Запустить» — Борис парсит товары (название, цена, фото).
2. Дальше два способа сделать текст объявления:
   — ЧЕРЕЗ ШАБЛОН: выбрать шаблон в выпадающем списке — применяет твой спинтакс-шаблон к сырому названию/описанию с сайта.
   — ЧЕРЕЗ ИИ: кнопка «🤖 Сгенерировать текст ИИ» на карточке товара — заходит на страницу товара, берёт характеристики и описание, генерирует продающий текст, открывает окно превью с редактором (можно поправить текст, выделить жирным кнопкой «Ж»). После нажатия «Применить к карточке» — карточка помечается как «обработана ИИ» и в массовой публикации через шаблон её больше НЕ трогают (шаблон не перезапишет качественный ИИ-текст).
3. Публикация — два варианта:
   — ПО ОДНОЙ: кнопка «📤 Добавить в Авито» на конкретной карточке — публикует именно эту карточку СРАЗУ, ДОБАВЛЯЯ к уже существующим объявлениям в фиде (не заменяет остальные).
   — МАССОВО: отметить галочками нужные карточки («✅ Выбрать все» или вручную) → блок «Планировщик запуска» (дата/время/интервал) → «Применить к выбранным».
4. Кнопка «🏢 Информация о компании» — одно текстовое поле, что впишешь туда (доставка, гарантия, контакты) автоматически добавится в конец описания КАЖДОГО товара при генерации ИИ.
5. «📦 Скачать фото (ZIP)» — скачивает архив фото всех найденных товаров.

ПОЛЕЗНЫЕ ЦИФРЫ:
- Себестоимость одного ИИ-объявления ≈ 0.5-1 ₽ (токены GigaChat).
- Картинка Kandinsky ≈ 5-6 ₽.
- Задержка мин/макс — сколько минут паузы между публикацией соседних объявлений (защита от резкого всплеска активности, который может привлечь модерацию Авито).

Если пользователь спрашивает что делать, куда нажать, как работает какая-то функция — объясни просто и по шагам, ссылаясь на конкретную вкладку и кнопку. Если не уверен в деталях — честно скажи, что лучше уточнить у разработчика, не выдумывай несуществующие кнопки.

КАК ПРАВИЛЬНО ОПРЕДЕЛИТЬ КАТЕГОРИЮ И ОБЯЗАТЕЛЬНЫЕ ПОЛЯ ТОВАРА (важно перед публикацией новой ниши):
1. НИКОГДА не гадать категорию/поля по названию раздела или по аналогии с другим товаром — Avito отклоняет объявление если категория не листовая или поле называется неверно.
2. Точный источник истины — официальная документация: https://www.avito.ru/autoload/documentation/templates/{id}
   - Открыть категорию через боковое меню (не через "Поиск по категориям" — он не фильтрует).
   - Переключить "Язык параметров" на Английский — там точные технические имена тегов (например Condition, не Sostoyanie; VehicleType, не ВидТранспорта).
   - Включить тумблер "Только обязательные" — сразу видно ВСЕ обязательные поля этого шаблона, ничего не потеряется.
   - Обратить внимание на условные поля вида "Обязательно, если Availability = 'В наличии'" — если у клиента "Под заказ", такие поля указывать НЕ нужно, это освобождает от кучи характеристик (размер/материал/цвет и т.п.).
3. Если товар "под заказ" (Availability="Под заказ" или KitchenType="На заказ" и т.п.) — обычно достаточно 5-7 базовых полей (GoodsType/AdType/Condition/Availability/GoodsSubType + 1-2 специфичных типа-товара). Если "в наличии" — нужны все размерные/цветовые поля.
4. НЕ придумывать спинтакс для полей, описывающих факт о товаре (цвет, марка, размер, бренд) — спинтакс допустим только для синонимов/стилистики, никогда для характеристик которые реально различаются у товаров (иначе получится вранье в объявлении).

КАК ПРОВЕРИТЬ ФИД ПЕРЕД РЕАЛЬНОЙ ПУБЛИКАЦИЕЙ (обязательный шаг для новой ниши/клиента):
1. Открыть https://www.avito.ru/autoload/documentation/templates/ → в правом меню "Проверить файл" → вкладка "По ссылке".
2. Вставить ссылку фида клиента: https://boris-ai.pro/api/avito/feed/{account_id}.xml (или через IP-адрес сервера если домен недоступен).
3. Нажать "Проверить". Статусы:
   - "XML соответствует формату" (зелёный) — фид готов, можно подключать/обновлять в реальной Автозагрузке.
   - "XML не полностью соответствует формату" (жёлтый) — есть предупреждения (Avito сам подставил значения по умолчанию), объявления скорее всего пройдут, но лучше проверить.
   - "XML не соответствует формату" (красный) — есть настоящие ошибки, объявления НЕ будут опубликованы, нужно смотреть "Подробнее" по каждой ошибке и исправлять params.
4. Только после зелёного статуса просить владельца зайти в Личный кабинет Avito → Автозагрузка → вставить/обновить ссылку фида ("Обновить сейчас" или выключить-включить, если нет отдельной кнопки).
5. Если фид подключается ВПЕРВЫЕ для нового клиента — точный путь в ЛК Avito: Настройки → Автозагрузка (или "Профиль" → "Автозагрузка объявлений") → вставить ссылку фида → сохранить. Обработка первого фида может занять несколько часов.

КАК ЧИТАТЬ И ИСПРАВЛЯТЬ ЭСКАЛАЦИИ ОШИБОК В ПАНЕЛИ ДИРЕКТОРА:
Эскалации — это записи о сбоях автоматического исполнителя (plan_item_execute), которые сохраняются в Storage(_global, "director_escalations"). У них есть кнопка "🗑 Очистить всё" (стирает весь список) и "✓ Исправить" на каждой отдельной записи (убирает только её).

Частые типы ошибок и что делать:
1. "Не удалось разобрать ответ от GPT: {...}" — GPT вернул обрезанный/невалидный JSON при генерации плана шагов. Обычно причина — слишком большая задача (много направлений сразу) упирается в лимит max_completion_tokens. Если ошибка повторяется на новой большой задаче — либо ещё раз поднять max_completion_tokens в plan_items.py (сейчас 8000), либо (надёжнее) попросить владельца сформулировать задачу как ОТДЕЛЬНЫЕ более мелкие подзадачи по одному направлению вместо одной гигантской.
2. "publish_listings пропущен: в папке '{folder}' нет фото" — у клиента нет загруженных фото в нужной папке. Нужно попросить владельца загрузить фото или запустить "🖼️ Собрать реальные фото по теме" (Pexels) перед повторным запуском.
3. "publish_listings пропущен для '{topic}': не удалось определить категорию Avito" — detect_category не смог найти категорию (обычно из-за отсутствия реальных объявлений у клиента через API и проблем с анонимным поиском/прокси). Нужно определить категорию вручную через документацию https://www.avito.ru/autoload/documentation/templates/{id} (см. раздел выше "как правильно определить категорию") и добавить её в category_knowledge_base, либо попросить владельца подтвердить нишу точнее.
4. Ошибки вида "ProxyError"/"Timeout" внутри detect_category или city_analysis — резидентный прокси pool.networkpw.com либо закончился баланс (проверить в личном кабинете провайдера), либо попал на мёртвый порт (сам код уже делает retry на нескольких портах, если ошибка не проходит — вероятно баланс).

Если ошибка НЕ входит в эти категории или непонятна — не пытаться угадывать причину, а честно сказать владельцу что нужно разобраться вручную, показав точный текст ошибки."""

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []
    account_id: str = None

_OWNER_MARK = "КАК ЧИТАТЬ И ИСПРАВЛЯТЬ ЭСКАЛАЦИИ"
_i = BORIS_CONTEXT.find(_OWNER_MARK)
_owner_part = BORIS_CONTEXT[_i:] if _i > 0 else ""
_body = BORIS_CONTEXT[:_i].rstrip() if _i > 0 else BORIS_CONTEXT

# Тяжёлые тематические блоки подгружаем только когда вопрос по теме —
# в остальных ответах они зря раздувают промпт (~4700 символов на каждый запрос).
_iu = _body.find("УНИКАЛИЗАЦИЯ")
_if = _body.find("ССЫЛКА ФИДА ПОСТОЯННАЯ")
_ip = _body.find("ПОЛЕЗНЫЕ ЦИФРЫ")
BORIS_BLOCK_UNIQ = _body[_iu:_if].rstrip() if _iu > 0 and _if > _iu else ""
BORIS_BLOCK_FEED = _body[_if:_ip].rstrip() if _if > 0 and _ip > _if else ""
# из базового промпта вырезаем оба блока, оставляя короткую подсказку
BORIS_CONTEXT_BASE = (
    _body[:_iu].rstrip()
    + "\n\n(Если спрашивают про уникализацию текстов, ссылку фида или автозагрузку — отвечай подробно, детали у тебя есть.)\n\n"
    + _body[_ip:]
) if _iu > 0 and _ip > _if else _body
BORIS_CONTEXT_OWNER = _owner_part
_FEED_WORDS = ("фид", "ссылк", "автозагруз", "выгруз", "url", "постоянн")
_UNIQ_WORDS = ("уникализ", "шаблон", "спинтакс", "рерайт", "переписа")


def _topic_extra(text):
    t = (text or "").lower()
    add = ""
    if any(w in t for w in _FEED_WORDS) and BORIS_BLOCK_FEED:
        add += "\n\n" + BORIS_BLOCK_FEED
    if any(w in t for w in _UNIQ_WORDS) and BORIS_BLOCK_UNIQ:
        add += "\n\n" + BORIS_BLOCK_UNIQ
    return add


def _is_owner_account(account_id):
    """Разбор эскалаций нужен только владельцу — клиенту эта часть промпта не отправляется."""
    if not account_id:
        return False
    from app.db.session import SessionLocal
    from sqlalchemy import text as _t
    db = SessionLocal()
    try:
        # если у аккаунта есть свой пользователь-клиент — это клиент,
        # даже когда аккаунт числится за владельцем платформы
        own = db.execute(_t("SELECT role FROM users WHERE account_id=:a LIMIT 1"),
                         {"a": account_id}).fetchone()
        if own:
            return own[0] == "owner"
        r = db.execute(_t("""SELECT role FROM users
                             WHERE id=(SELECT owner_user_id FROM accounts WHERE account_id=:a)
                             LIMIT 1"""), {"a": account_id}).fetchone()
        return bool(r and r[0] == "owner")
    except Exception:
        return False
    finally:
        db.close()


def _load_extra_knowledge(account_id=None):
    """Читает дополнительные знания Бориса из Storage (добавляются владельцем без правки кода)."""
    try:
        from app.db.session import SessionLocal
        from app.models.storage import Storage
        db = SessionLocal()
        try:
            parts = []
            g = db.query(Storage).filter(Storage.account_id == "global", Storage.key == "boris_knowledge").first()
            if g and g.value:
                parts.append("=== ОБЩИЕ ЗНАНИЯ О СЕРВИСЕ ===\n" + g.value)
            if account_id and account_id != "global":
                a = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "boris_knowledge").first()
                if a and a.value:
                    parts.append("=== ЗНАНИЯ ОБ ЭТОМ КЛИЕНТЕ ===\n" + a.value)
            return "\n\n".join(parts)
        finally:
            db.close()
    except Exception:
        return ""

@router.post("")
def chat(req: ChatRequest):
    _acc = getattr(req, "account_id", None)
    _extra = _load_extra_knowledge(_acc)
    _base = BORIS_CONTEXT if _is_owner_account(_acc) else BORIS_CONTEXT_BASE
    _base = _base + _topic_extra(req.message)
    _ctx = _base + ("\n\n=== ДОПОЛНИТЕЛЬНЫЕ ЗНАНИЯ (добавлены владельцем) ===\n" + _extra if _extra else "")
    messages = [Messages(role=MessagesRole.SYSTEM, content=_ctx)]
    for m in req.history[-10:]:
        role = MessagesRole.USER if m.role == "user" else MessagesRole.ASSISTANT
        messages.append(Messages(role=role, content=m.content))
    messages.append(Messages(role=MessagesRole.USER, content=req.message))

    answer = chat_with_fallback(messages, model=_GC_MODEL, credentials=GIGACHAT_KEY,
                                 account_id=getattr(req, "account_id", None), operation="чат с Борисом")

    return {"answer": answer}


from fastapi import UploadFile, File

def _extract_text_from_file(filename: str, content: bytes) -> str:
    """Извлекает текст из PDF / Word / txt. Возвращает текст или пустую строку."""
    import io
    name = (filename or "").lower()
    try:
        if name.endswith(".pdf"):
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(content))
            return "\n".join((p.extract_text() or "") for p in reader.pages).strip()
        elif name.endswith(".docx"):
            import docx
            doc = docx.Document(io.BytesIO(content))
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip()).strip()
        elif name.endswith((".txt", ".md", ".csv")):
            return content.decode("utf-8", errors="ignore").strip()
    except Exception as e:
        return f"[не удалось прочитать файл: {str(e)[:80]}]"
    return ""

@router.post("/knowledge/upload")
async def upload_knowledge_file(file: UploadFile = File(...)):
    """Принимает файл (PDF/Word/txt), извлекает текст, добавляет в знания Бориса."""
    content = await file.read()
    text = _extract_text_from_file(file.filename, content)
    if not text or text.startswith("[не удалось"):
        return {"status": "error", "message": text or "Не удалось извлечь текст из файла", "filename": file.filename}
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "boris_knowledge").first()
        old = row.value if row and row.value else ""
        header = f"\n\n=== Из файла: {file.filename} ===\n"
        new_val = (old + header + text).strip()
        if row:
            row.value = new_val
        else:
            row = Storage(account_id=account_id, key="boris_knowledge", value=new_val)
            db.add(row)
        db.commit()
        return {"status": "ok", "filename": file.filename, "extracted_chars": len(text), "total_chars": len(new_val), "account_id": account_id}
    finally:
        db.close()


class KnowledgeRequest(BaseModel):
    text: str
    account_id: str = "global"

@router.get("/knowledge")
def get_knowledge(account_id: str = "global"):
    """Возвращает знания: если account_id=global — общие; иначе личные аккаунта."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "boris_knowledge").first()
        return {"knowledge": row.value if row and row.value else "", "account_id": account_id}
    finally:
        db.close()

@router.post("/knowledge/add")
def add_knowledge(req: KnowledgeRequest):
    """Добавляет знание. account_id=global — общие; иначе личные аккаунта."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    _aid = req.account_id or "global"
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == _aid, Storage.key == "boris_knowledge").first()
        old = row.value if row and row.value else ""
        new_val = (old + "\n\n" + req.text.strip()).strip()
        if row:
            row.value = new_val
        else:
            row = Storage(account_id=_aid, key="boris_knowledge", value=new_val)
            db.add(row)
        db.commit()
        return {"status": "ok", "total_chars": len(new_val), "account_id": _aid}
    finally:
        db.close()

@router.post("/knowledge/replace")
def replace_knowledge(req: KnowledgeRequest):
    """Полностью заменяет знания в нужном уровне (global или аккаунт)."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    _aid = req.account_id or "global"
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == _aid, Storage.key == "boris_knowledge").first()
        if row:
            row.value = req.text.strip()
        else:
            row = Storage(account_id=_aid, key="boris_knowledge", value=req.text.strip())
            db.add(row)
        db.commit()
        return {"status": "ok", "total_chars": len(req.text.strip())}
    finally:
        db.close()


TASK_PARSER_PROMPT = """Ты — модуль планирования внутри Бориса, ИИ-ассистента для автоматизации Авито.
Тебе дают команду владельца бизнеса на естественном языке. Твоя задача — разобрать её
в СТРОГИЙ JSON-план подзадач. Ничего кроме JSON не выводи, без markdown-разметки, без пояснений.

Формат ответа (строго):
{
  "understood": true/false,
  "summary": "краткое описание того, что понял, 1-2 предложения на русском",
  "steps": [
    {"type": "generate_banners", "count": число, "topic": "тема"},
    {"type": "publish_listings", "count": число, "cities": ["город1","город2"], "templates_count": число},
    {"type": "ab_test", "duration_days": число, "compare": "что сравниваем"},
    {"type": "report_back", "after_days": число}
  ],
  "clarifying_question": "вопрос, если что-то неясно, иначе null"
}

Доступные типы шагов: generate_banners, publish_listings, ab_test, report_back, analyze_competitors, collect_stats.
Если команда не про задачу (а просто вопрос/приветствие) — understood: false, steps: [].
Если не хватает важных деталей (например не указана тема баннеров) — заполни clarifying_question,
но всё равно верни steps с разумными предположениями, которые пользователь сможет поправить."""


class ParseTaskRequest(BaseModel):
    message: str


@router.post("/parse_task")
def parse_task(req: ParseTaskRequest):
    """Разбирает комплексную команду владельца в структурированный план подзадач.
    Первый кирпич оркестратора: понять команду -> показать план -> (следующий этап) выполнить."""
    import json as _json
    import re as _re

    messages = [
        Messages(role=MessagesRole.SYSTEM, content=TASK_PARSER_PROMPT),
        Messages(role=MessagesRole.USER, content=req.message),
    ]
    try:
        raw = chat_with_fallback(messages, model=_GC_MODEL, credentials=GIGACHAT_KEY)

        cleaned = _re.sub(r"^```json\s*|\s*```$", "", raw.strip(), flags=_re.MULTILINE).strip()
        plan = _json.loads(cleaned)
        return {"status": "ok", "plan": plan}
    except _json.JSONDecodeError:
        return {"status": "error", "message": "Не удалось разобрать план (ИИ вернул не-JSON)", "raw": raw[:500]}
    except Exception as e:
        return {"status": "error", "message": str(e)[:300]}


@router.get("/notifications")
def get_notifications(account_id: str, unread_only: bool = True):
    """Возвращает уведомления/отчёты Бориса (например report_back после А/Б-теста).
    unread_only=true — только непрочитанные (для бейджика в чате)."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json

    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "notifications").first()
        if not row:
            return {"status": "ok", "notifications": []}
        notifications = _json.loads(row.value)
        if unread_only:
            notifications = [n for n in notifications if not n.get("read")]
        return {"status": "ok", "notifications": list(reversed(notifications))}
    finally:
        db.close()


class MarkReadRequest(BaseModel):
    account_id: str = "otdushi"
    ts: str


@router.post("/notifications/mark_read")
def mark_notification_read(req: MarkReadRequest):
    """Помечает уведомление прочитанным по его timestamp."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json

    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == req.account_id, Storage.key == "notifications").first()
        if not row:
            return {"status": "ok"}
        notifications = _json.loads(row.value)
        for n in notifications:
            if n.get("ts") == req.ts:
                n["read"] = True
        row.value = _json.dumps(notifications, ensure_ascii=False)
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()

