from fastapi import APIRouter
import random
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

from app.db.session import SessionLocal
from app.models.plan_item import PlanItem
from app.api.avito import spin

router = APIRouter(prefix="/api/plan_items", tags=["plan_items"])


class CreatePlanItemRequest(BaseModel):
    account_id: str
    text: str
    source: str = "user"
    status: str = "planned"
    due_date: Optional[datetime] = None
    remind_at: Optional[datetime] = None
    linked_task_id: Optional[int] = None


class UpdatePlanItemRequest(BaseModel):
    text: Optional[str] = None
    status: Optional[str] = None
    due_date: Optional[datetime] = None
    remind_at: Optional[datetime] = None


@router.post("/create")
def create_plan_item(req: CreatePlanItemRequest):
    db = SessionLocal()
    try:
        item = PlanItem(
            account_id=req.account_id,
            text=req.text,
            source=req.source,
            status=req.status,
            due_date=req.due_date,
            remind_at=req.remind_at,
            linked_task_id=req.linked_task_id,
        )
        db.add(item)
        db.commit()
        db.refresh(item)

        # Сразу ставим задачу в очередь tasks, чтобы фоновый воркер её выполнил.
        # Раньше plan_item просто создавался и висел в статусе planned - его никто не запускал.
        # Только для задач от пользователя (source=user) и в статусе planned.
        if item.status == "planned" and item.source == "user":
            try:
                from app.models.task import Task
                import json as _json
                bg = Task(
                    account_id=item.account_id,
                    task_type="execute_plan_subtask",
                    status="queued",
                    payload=_json.dumps({"plan_item_id": item.id}, ensure_ascii=False)
                )
                db.add(bg)
                item.status = "queued"
                db.commit()
            except Exception as _e:
                print(f"[create_plan_item] не удалось поставить в очередь: {_e}", flush=True)

        return {"status": "ok", "id": item.id}
    finally:
        db.close()


@router.get("/list")
def list_plan_items(account_id: str):
    db = SessionLocal()
    try:
        items = db.query(PlanItem).filter(PlanItem.account_id == account_id).order_by(PlanItem.created_at.desc()).all()
        return {
            "status": "ok",
            "items": [
                {
                    "id": i.id,
                    "text": i.text,
                    "status": i.status,
                    "source": i.source,
                    "linked_task_id": i.linked_task_id,
                    "due_date": i.due_date.isoformat() if i.due_date else None,
                    "remind_at": i.remind_at.isoformat() if i.remind_at else None,
                    "reminder_sent": i.reminder_sent,
                    "created_at": i.created_at.isoformat() if i.created_at else None,
                }
                for i in items
            ],
        }
    finally:
        db.close()


@router.post("/{item_id}/update")
def update_plan_item(item_id: int, req: UpdatePlanItemRequest):
    db = SessionLocal()
    try:
        item = db.query(PlanItem).filter(PlanItem.id == item_id).first()
        if not item:
            return {"status": "error", "message": "not_found"}
        if req.text is not None:
            item.text = req.text
        if req.status is not None:
            item.status = req.status
        if req.due_date is not None:
            item.due_date = req.due_date
        if req.remind_at is not None:
            item.remind_at = req.remind_at
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()


@router.post("/{item_id}/delete")
def delete_plan_item(item_id: int):
    db = SessionLocal()
    try:
        item = db.query(PlanItem).filter(PlanItem.id == item_id).first()
        if not item:
            return {"status": "error", "message": "not_found"}
        db.delete(item)
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()


def _humanize_error(raw_message: str) -> str:
    """Переводит технические сообщения об ошибках в понятный русский текст для владельца."""
    msg = str(raw_message)
    msg_lower = msg.lower()

    rules = [
        (["429", "too many requests", "ratelimiterror"],
         "Превышен лимит запросов к ИИ (GigaChat) — сервис временно перегружен. Борис попробует ещё раз автоматически."),
        (["timed out", "timeout", "timeoutexception", "read timeout"],
         "Действие не успело выполниться за отведённое время — сервер был перегружен или ждал внешний сервис дольше обычного."),
        (["proxyerror", "proxy error", "econnrefused", "connection refused"],
         "Проблема с прокси-соединением — нет связи с внешним сервисом. Проверьте баланс/статус прокси."),
        (["pexels_api_key", "pixabay_api_key", "unsplash_access_key"],
         "Не настроен ключ доступа к сервису поиска фото — обратитесь к администратору."),
        (["502", "503", "bad gateway", "service unavailable"],
         "Внешний сервис временно недоступен (ошибка на его стороне)."),
        (["401", "403", "unauthorized", "forbidden"],
         "Ошибка авторизации — истёк или неверен ключ доступа к сервису."),
        (["ssl", "certificate"],
         "Проблема с защищённым соединением (SSL-сертификат) при обращении к внешнему сервису."),
        (["not_found", "404"],
         "Запрошенные данные не найдены."),
    ]

    for keywords, ru_text in rules:
        if any(k in msg_lower for k in keywords):
            return ru_text

    return f"Техническая ошибка: {msg[:200]}"


def _escalate_plan_error(db, account_id, task_type, error_message):
    from app.models.storage import Storage
    import json as _json_esc
    import datetime as _dt_esc

    row = db.query(Storage).filter(Storage.account_id == "_global", Storage.key == "director_escalations").first()
    escalations = _json_esc.loads(row.value) if row else []
    escalations.insert(0, {
        "ts": _dt_esc.datetime.utcnow().isoformat(),
        "account_id": account_id,
        "task_type": task_type,
        "error_message": _humanize_error(error_message),
        "error_message_raw": str(error_message)[:500],
        "read": False
    })
    escalations = escalations[:200]
    if row:
        row.value = _json_esc.dumps(escalations, ensure_ascii=False)
    else:
        row = Storage(account_id="_global", key="director_escalations", value=_json_esc.dumps(escalations, ensure_ascii=False))
        db.add(row)
    db.commit()


def _get_category_specs(topic: str, db) -> dict:
    """Типовые габариты/материалы/уместные RAL-цвета для темы товара - чтобы черновики
    партии не были похожи один на один (одинаковый заголовок и цена без реальных
    характеристик). Кэшируется в Storage НАВСЕГДА по topic - характеристики категории
    товара не меняются, повторный поход к GPT на каждую партию не нужен."""
    from app.models.storage import Storage
    from app.ral_colors import format_ral_list_for_prompt
    import json as _json_specs

    cache_key = "category_specs:" + topic.strip().lower()
    row = db.query(Storage).filter(Storage.account_id == "_global", Storage.key == cache_key).first()
    if row:
        try:
            return _json_specs.loads(row.value)
        except Exception:
            pass

    from gigachat_pool import chat_with_fallback
    from gigachat.models import Messages, MessagesRole

    prompt = (
        f"Товар/услуга для объявлений на Avito: {topic}\n\n"
        "Дай типовые характеристики, которые реально пишут в объявлениях этого товара:\n"
        "1. widths, heights, depths - если товар имеет физические габариты (мебель, техника и т.п.) - "
        "по 2-3 реалистичных числа (в мм) для ширины/высоты/глубины отдельно. Если у товара нет "
        "габаритов (услуга) - три пустых списка.\n"
        "2. materials - 3-4 типичных материала/исполнения для этого товара (пусто, если неприменимо).\n"
        "3. ral_codes - 3-4 УМЕСТНЫХ для этого товара цвета СТРОГО из следующего закрытого списка, "
        "перепиши код и название ТОЧНО как в списке, не придумывай новые и не бери случайные "
        "(например для мебели/техники не бери сигнальные/транспортные яркие цвета): "
        + format_ral_list_for_prompt() + "\n\n"
        "Ответь СТРОГО чистым JSON без markdown, без пояснений:\n"
        '{"widths": ["1600","1800"], "heights": ["2400"], "depths": ["550","600"], '
        '"materials": ["ЛДСП","МДФ"], "ral_codes": ["RAL 9010 Белый","RAL 7035 Светло-серый"]}'
    )
    try:
        raw = chat_with_fallback([Messages(role=MessagesRole.USER, content=prompt)], temperature=0.4, max_tokens=600,
                                 account_id=account_id, operation="характеристики категории")
        raw = raw.strip().replace("```json", "").replace("```", "").strip()
        specs = _json_specs.loads(raw)
        if not isinstance(specs, dict):
            specs = {}
    except Exception as e:
        print(f"[create_draft_listings] не удалось получить типовые характеристики для '{topic}': {e}", flush=True)
        specs = {}

    if row:
        row.value = _json_specs.dumps(specs, ensure_ascii=False)
    else:
        row = Storage(account_id="_global", key=cache_key, value=_json_specs.dumps(specs, ensure_ascii=False))
        db.add(row)
    db.commit()
    return specs


def _build_specs_spintax_block(specs: dict) -> str:
    """Собирает блок характеристик со спинтаксом из данных _get_category_specs -
    раскрывается функцией spin() при публикации, каждый черновик получает свою комбинацию."""
    lines = []
    ral_codes = [c for c in (specs.get("ral_codes") or []) if c]
    if ral_codes:
        lines.append("Цвет: {" + "|".join(ral_codes) + "}")

    widths = [str(w) for w in (specs.get("widths") or []) if str(w).strip()]
    heights = [str(h) for h in (specs.get("heights") or []) if str(h).strip()]
    depths = [str(d) for d in (specs.get("depths") or []) if str(d).strip()]
    if widths and heights and depths:
        lines.append(f"Размеры (ШхВхГ): {{{'|'.join(widths)}}} × {{{'|'.join(heights)}}} × {{{'|'.join(depths)}}} мм")

    materials = [m for m in (specs.get("materials") or []) if m]
    if materials:
        lines.append("Материал: {" + "|".join(materials) + "}")

    return "\n".join(lines)


BORIS_CAPABILITIES = """
Ты - Борис: ИИ-директор по рекламе, продажам, маркетингу и копирайтингу для клиентов на Avito.
Ты разбираешься в: метриках рекламы (просмотры/обращения/конверсия/CTR), продающем копирайтинге,
структуре сильных объявлений, категориях и обязательных полях Avito, веб-дизайне баннеров,
анализе конкурентов (что делает их объявления/фото/тексты эффективными). Используй эту экспертизу
при формировании параметров любого действия - например, для баннера пиши сильный маркетинговый
текст, а не просто пересказывай задачу.

У тебя ЕСТЬ следующие реальные действия, которые ты можешь выполнить прямо сейчас (только они, не выдумывай другие):

1. search_stock_photos - собрать реальные бесплатные фото по теме (Pexels/Pixabay).
   Параметры: query (тема на английском лучше), count (число, макс 80), folder (название папки).
   ВАЖНОЕ ОБЩЕЕ ПРАВИЛО: если задача касается НЕСКОЛЬКИХ разных направлений/категорий товара
   (например разные виды мебели, разные ниши) - у КАЖДОГО направления должна быть СВОЯ folder
   с понятным названием именно этого направления (транслит, нижний регистр, подчёркивания вместо
   пробелов). НИКОГДА не смешивай фото разных направлений в одну общую папку.
   Пример: "собери 50 фото по теме кухни" -> folder="kuhni"

2. detect_category - определить категорию Avito для товара/услуги по нише.
   Параметры: niche (описание товара/услуги).
   Пример: "определи категорию для шкафов"

3. city_analysis - проанализировать конкурентов в конкретном городе (цены, топ-5 объявлений,
   что делает их эффективными - текст/фото/цена).
   Параметры: city (город), query (что искать).
   Пример: "проанализируй конкурентов по бетону в Калуге"

4. generate_banner - создать рекламный баннер с продающим текстом через ИИ (GPT Image).
   Параметры: raw_description (сырое описание товара/акции от владельца, без выдумывания фактов),
   format (infographic, extended или max).
   Пример: "сделай баннер про скидку на шкафы"

5. ab_test - сравнить статистику нескольких вариантов объявлений и объяснить победителя.
   Параметры: start_date (YYYY-MM-DD), variants (список label и item_ids) - если item_ids
   неизвестны из текста задачи, НЕ придумывай их, используй action null с объяснением.
   Пример: "сравни результаты двух вариантов объявлений с прошлой недели" (только если ID объявлений явно указаны)

6. publish_listings - сгенерировать и реально опубликовать объявления в фид Avito (текст+фото+категория).
   Параметры: topic (тема/ниша объявлений), count (число объявлений), price_from, price_to (диапазон цен,
   если не указан в задаче - используй разумный по нише), folder (название папки с фото для этого товара/услуги -
   можно использовать УЖЕ СУЩЕСТВУЮЩУЮ папку, если пользователь её называет или она логично следует из
   контекста ниши, папку НЕ обязательно собирать заново в этом же вызове).
   ВАЖНО: publish_listings САМ АВТОМАТИЧЕСКИ создаёт рекламный баннер и ставит его первым фото
   каждого объявления - если пользователь просит "с баннером" или "и баннер тоже", НЕ добавляй
   отдельный шаг generate_banner для той же темы, publish_listings уже всё сделает сам.
   Если пользователь ЯВНО просит сначала собрать НОВЫЕ фото (например "собери фото и опубликуй") -
   добавь отдельный шаг search_stock_photos ПЕРЕД publish_listings с той же папкой. Если пользователь
   просто называет папку без просьбы собирать фото заново ("фото возьми из папки X") - используй
   папку X напрямую в publish_listings БЕЗ отдельного шага search_stock_photos.
   Пример: "создай 10 объявлений про шкафы и опубликуй, фото возьми из папки shkafy" -> один шаг
   publish_listings с folder="shkafy", БЕЗ search_stock_photos.

7. generate_templates - создать переиспользуемые ТЕКСТОВЫЕ ШАБЛОНЫ объявлений (без реальной публикации,
   без фото) - сохраняются в разделе Шаблоны для дальнейшего использования.
   Параметры: topic (тема/ниша), count (число шаблонов).
   Используй это действие вместо publish_listings, если пользователь явно просит именно "шаблоны",
   а не "опубликовать"/"создать объявления и отправить".
   Пример: "сделай 10 шаблонов объявлений про кухни с разными названиями"

8. edit_active_listings - ОТРЕДАКТИРОВАТЬ уже АКТИВНЫЕ (реально опубликованные) объявления:
   берёт ГОТОВЫЕ заготовки названий/цен/описаний (уже сгенерированные владельцем в разделе "Генератор
   объявлений через ИИ", хранятся в gen_ads) и случайным образом раздаёт их по объявлениям направления
   (разворачивая спинтакс через spin() для уникальности каждого), ставит рекламный баннер первым фото,
   исправляет адрес на реалистичный (для Москвы - случайная станция метро, не просто "Москва" у всех).
   НЕ генерирует текст заново через ИИ и НЕ создаёт новые объявления - обновляет существующие по их Id,
   используя уже готовый контент.
   Параметры: direction (название направления/ниши для баннера, например "шкафы-купе" или "кухни на заказ"),
   id_prefix (общий префикс Id объявлений этого направления в базе - ОБЯЗАТЕЛЬНО уточни у владельца
   префикс если не очевиден из контекста задачи, не придумывай).
   Пример: "отредактируй активные объявления по шкафам-купе - используй готовые названия/цены/описания, добавь баннер первым фото"

9. create_draft_listings - создать ПОЛНОЦЕННЫЕ объявления (текст + категория + подобранные фото) и
   сохранить их как ЧЕРНОВИКИ, НЕ публикуя в реальный фид Avito и БЕЗ автогенерации баннера.
   Используй это действие вместо publish_listings, если пользователь просит режим предпросмотра/
   черновика перед публикацией ("хочу сначала увидеть", "не публикуй сразу", "черновик", "на проверку").
   Все черновики одной задачи помечаются общим batch_id (используй item_id задачи) и batch_label
   (короткое понятное название партии, например "Книга жизни") - это позволит найти их все разом через
   фильтр "Черновики" в разделе Объявления, вместо того чтобы искать по одному.

   ПОДДЕРЖКА A/B-ТЕСТА ТЕКСТОВ (работает для ЛЮБОЙ ниши - товары, услуги, недвижимость и т.д.):
   Если пользователь хочет сравнить СВОИ готовые тексты с текстами от ИИ - передай оба параметра
   authored_texts (список готовых текстов владельца - каждый будет автоматически уникализирован через
   spin() под свой город, чтобы не было бана за дубли) и ai_count (сколько дополнительных черновиков
   сгенерировать через ИИ). Каждый черновик получает поле variant: "authored" (текст владельца) или
   "ai_generated" (текст от ИИ) - по этому полю потом можно будет сравнить результаты A/B-теста после
   публикации и нескольких дней сбора статистики.
   Параметры: topic (тема/ниша), count (сколько объявлений всего, для обычного режима без A/B),
   folder (папка с фото), photos_per_ad (сколько фото у КАЖДОГО черновика, например "добавь по 7 штук"
   -> photos_per_ad=7; если не указано явно - не передавай, возьмётся значение по умолчанию),
   cities (список городов, по одному объявлению на каждый),
   price_from, price_to, batch_label, authored_texts (для A/B - список готовых текстов владельца),
   ai_count (для A/B - сколько черновиков сделать через ИИ), price_authored (цена для авторских текстов,
   если отличается от диапазона price_from/price_to).
   Пример без A/B: "создай 70 черновиков объявлений про книгу жизни для топ-70 городов РФ по населению,
   цена 6000-9000 рублей, не публикуй, подбери фото, не трогай активные объявления"
   Пример с A/B: "часть черновиков сделай с моими текстами [текст1, текст2...] и уникализацией,
   часть - твоими текстами от ИИ, чтобы провести A/B-тест результатов"

10. apply_banner_to_batch - сгенерировать N рекламных баннеров (через ИИ, на основе текста существующих
    объявлений партии) и расставить фото по ВСЕМ черновикам УЖЕ СУЩЕСТВУЮЩЕЙ партии: баннер первым фото
    у каждого черновика + случайные обычные фото следом (по кругу из указанной папки), не создавая новых
    черновиков и не публикуя ничего. Используй это действие, когда владелец просит "сделай баннеры для
    партии X" или "добавь баннер к уже созданным черновикам" - партия уже должна существовать (создана
    ранее через create_draft_listings).
    Параметры: batch_label (точное название партии черновиков, например "Книга жизни" - должно совпадать
    с уже существующим batch_label, не придумывай новое), banner_count (сколько РАЗНЫХ вариантов баннера
    сгенерировать, они распределятся по кругу между черновиками партии), photos_per_ad (сколько всего фото
    у каждого объявления считая баннер, например 10 = 1 баннер + 9 обычных), folder (папка с обычными
    фото для этой партии - обычно та же, что использовалась при создании черновиков).
    Пример: "сделай 5 баннеров для партии Книга жизни на основе текстов объявлений, у каждого объявления
    баннер первым фото, потом 9 обычных фото из папки kniga_zhizni"

ВАЖНОЕ ПРАВИЛО РАЗРЕШЕНИЯ НЕОДНОЗНАЧНОСТИ "шаблоны" vs "Черновики":
Если в задаче ОДНОВРЕМЕННО упоминаются слова "шаблон(ы)" И явное указание "Отправь в Черновики"/
"в Черновики"/"черновик" - это ВСЕГДА означает create_draft_listings, а НЕ generate_templates.
Слово "шаблон" в такой формулировке относится к СОДЕРЖАНИЮ текста (структуре: цвет, размеры,
материал и т.д.), а не к разделу "Шаблоны". Явное указание раздела назначения (Черновики) важнее
случайного упоминания слова "шаблон". НИКОГДА не выбирай generate_templates, если в тексте задачи
есть слово "черновик" в любой форме.

ОБЯЗАТЕЛЬНАЯ ЦЕПОЧКА для задач с баннерами после создания черновиков:
Если задача просит создать черновики (create_draft_listings) И ТАКЖЕ просит баннеры для этого же
направления (упоминает "баннер(ы)", конкретный размер типа "1080 на 1080", или CTA вроде "вызвать
желание купить/позвонить/написать/пригласить замерщика") - ОБЯЗАТЕЛЬНО добавь ВТОРЫМ шагом
apply_banner_to_batch СРАЗУ ПОСЛЕ create_draft_listings в том же списке steps, используя тот же
batch_label, что указан в payload шага create_draft_listings. НЕ пропускай этот шаг и не добавляй
его в skipped - если create_draft_listings есть в шагах, apply_banner_to_batch для той же партии
почти всегда должен быть следующим шагом, когда задача упоминает баннеры. Количество баннеров
(banner_count) бери из явного числа в задаче (например "5 баннеров" -> banner_count=5).
photos_per_ad считай как 1 (баннер) + число обычных фото, явно указанное в задаче (например
"дальше 7 картинок" -> photos_per_ad=8).

Задача может требовать НЕСКОЛЬКО последовательных действий из списка выше - разбей её на шаги.
Верни ТОЛЬКО чистый JSON вида:
{"steps": [{"action": "search_stock_photos", "payload": {...}}, {"action": "detect_category", "payload": {...}}]}

Каждый шаг - одно из ДЕСЯТИ действий выше с реальными параметрами. Если для какой-то части задачи
не хватает данных или она не входит в список действий - НЕ включай её как шаг, вместо этого добавь
в конец объекта поле "skipped": ["короткое объяснение что именно пропущено и почему"].

Если ВООБЩЕ ни один шаг не удаётся выполнить - верни {"steps": [], "skipped": ["объяснение"]}.

Отвечай ТОЛЬКО чистым JSON, без markdown, без пояснений вокруг.
"""


@router.post("/{item_id}/execute")
def _fill_category_params(niche, existing=None, account_id=None):
    """Умное заполнение params категории из справочника CategoryTemplate.
    Тянет все теги категории, ставит значения из enum (полнота важна для ранжирования Avito).
    Опциональные поля без однозначного значения и без данных — пропускает."""
    import json as _j
    from app.db.session import SessionLocal as _SL
    from app.models.category_template import CategoryTemplate as _CT
    _db = _SL()
    try:
        # ищем шаблон по category_id (заполнен у ~21) ИЛИ по category_name (заполнен у всех 659)
        nm = (niche or "").strip()
        tpl = _db.query(_CT).filter(_CT.category_id.ilike("%" + nm + "%")).first()
        if not tpl:
            tpl = _db.query(_CT).filter(_CT.category_name.ilike("%" + nm + "%")).first()
        if not tpl:
            return existing or {}
        fields = _j.loads(tpl.required_fields or "[]")
        params = dict(existing or {})
        # Настройки продавца, которые клиент указал один раз при подключении
        # аккаунта: ContactMethod, InternetCalls, TargetAudience, Condition,
        # AdType и прочие. Одинаковы для всех его объявлений.
        defaults = {}
        if account_id:
            try:
                from app.models.storage import Storage as _St
                _row = _db.query(_St).filter(_St.account_id == account_id,
                                             _St.key == "seller_defaults").first()
                if _row and _row.value:
                    defaults = _j.loads(_row.value) or {}
            except Exception:
                defaults = {}
        _skip = {"Id","Title","Description","Price","Images","Address","DateBegin","DateEnd","Category","ImageNames","ListingFee","AdStatus","Promo"}
        for f in fields:
            tag = f.get("tag")
            if not tag or tag in _skip or tag in params:
                continue
            allowed = f.get("allowed_values") or []
            # Ответ клиента — высший приоритет.
            if tag in defaults and str(defaults[tag]).strip():
                params[tag] = defaults[tag]
            # Единственный допустимый вариант — это не выбор, а константа шаблона.
            elif len(allowed) == 1:
                params[tag] = allowed[0]
            # Вариантов несколько и ответа нет — НЕ подставляем. Раньше здесь
            # молча брался allowed[0]: у Delivery 10 вариантов, у Color 20,
            # и клиент не знал, что объявлено от его имени. Поле уйдёт в вопросы.
        return params
    finally:
        _db.close()


def execute_plan_item(item_id: int):
    import json as _json
    import os
    import requests as _requests
    from proxy_pool import get_intl_requests_proxies

    db = SessionLocal()
    item = None
    try:
        item = db.query(PlanItem).filter(PlanItem.id == item_id).first()
        if not item:
            return {"status": "error", "message": "not_found"}

        item.status = "running"
        db.commit()

        api_key = os.environ.get("OPENAI_API_KEY")
        proxies = get_intl_requests_proxies()

        def _openai_post(payload_json, attempts=3, tmo=35):
            """Вызов OpenAI с ретраем и НОВЫМ прокси-портом на каждой попытке.
            Порты пула часто дохлые - старый код брал один порт и висел на нём."""
            last_err = None
            for _a in range(attempts):
                px = get_intl_requests_proxies()
                try:
                    r = _requests.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
                        json=payload_json, proxies=px, timeout=tmo,
                    )
                    return r
                except Exception as _e:
                    last_err = _e
                    print(f"[plan_execute] прокси-попытка {_a+1}/{attempts} не удалась: {_e}", flush=True)
                    continue
            raise Exception(f"OpenAI недоступен через прокси за {attempts} попыток: {last_err}")

        # === АВТОРАЗБИВКА: если задача касается НЕСКОЛЬКИХ разных направлений - разбиваем на подзадачи ===
        try:
            decompose_prompt = (
                "Ты помогаешь разбить задачу на подзадачи. Если текст задачи касается ОДНОГО связного "
                "направления/темы - верни {\"parts\": null}. Если задача явно перечисляет НЕСКОЛЬКО "
                "разных направлений/категорий товара (например разные виды мебели через запятую или *) "
                "и просит сделать одинаковый набор действий по КАЖДОМУ из них - разбей на отдельные "
                "самостоятельные тексты задач, ОДИН на каждое направление, сохранив ВСЕ исходные "
                "требования (что именно нужно сделать) для каждого направления. Обязательно укажи в "
                "каждом тексте своё уникальное название папки для фото (транслит, нижний регистр, "
                "подчёркивания). Верни JSON: {\"parts\": [\"текст задачи 1\", \"текст задачи 2\", ...]} "
                "или {\"parts\": null} если разбивать не нужно. ТОЛЬКО чистый JSON, без markdown.\n\n"
                "Текст задачи: " + item.text
            )
            decomp_resp = _openai_post(
                {"model": "gpt-5.4", "messages": [{"role": "user", "content": decompose_prompt}], "max_completion_tokens": 16000}
            )
            if decomp_resp.status_code == 200:
                draw = decomp_resp.json()["choices"][0]["message"]["content"].strip()
                draw = draw.replace("```json", "").replace("```", "").strip()
                dparsed = _json.loads(draw)
                parts = dparsed.get("parts")
                if parts and isinstance(parts, list) and len(parts) > 1:
                    from app.models.task import Task
                    created_ids = []
                    for part_text in parts:
                        new_item = PlanItem(account_id=item.account_id, text=part_text, status="planned", source="boris_decompose")
                        db.add(new_item)
                        db.commit()
                        db.refresh(new_item)
                        created_ids.append(new_item.id)
                        # Ставим подзадачу в фоновую очередь вместо блокирующего вызова -
                        # воркер выполнит их одну за другой сам, не завершая этот HTTP-запрос
                        bg_task = Task(
                            account_id=item.account_id,
                            task_type="execute_plan_subtask",
                            status="queued",
                            payload=_json.dumps({"plan_item_id": new_item.id}, ensure_ascii=False)
                        )
                        db.add(bg_task)
                        db.commit()
                        db.refresh(bg_task)
                        new_item.status = "queued"
                        new_item.linked_task_id = bg_task.id
                        db.commit()

                    item.status = "done"
                    db.commit()
                    return {
                        "status": "ok",
                        "decomposed": True,
                        "subtasks_count": len(created_ids),
                        "message": f"Задача разбита на {len(created_ids)} подзадач, Борис выполняет их одну за другой в фоне. Прогресс можно посмотреть во вкладке Задачи и план."
                    }
        except Exception:
            pass  # если разбивка не удалась - просто продолжаем как единую задачу

        full_prompt = BORIS_CAPABILITIES + "\n\nЗадача: " + item.text + "\nАккаунт: " + item.account_id
        raw = None
        parsed = None
        gpt_cost_rub = 0
        for _routing_attempt in range(1, 3):
            try:
                resp = _openai_post(
                    {"model": "gpt-5.4", "messages": [{"role": "user", "content": full_prompt}], "max_completion_tokens": 16000},
                    tmo=90
                )
                if resp.status_code != 200:
                    if _routing_attempt < 2:
                        continue
                    return {"status": "error", "message": f"Ошибка GPT: {resp.status_code} {resp.text[:200]}"}
                data = resp.json()
                raw = data["choices"][0]["message"]["content"].strip()
                raw = raw.replace("```json", "").replace("```", "").strip()

                usage = data.get("usage", {})
                gpt_prompt_tokens = usage.get("prompt_tokens", 0)
                gpt_completion_tokens = usage.get("completion_tokens", 0)
                gpt_cost_rub = (gpt_prompt_tokens * 2.50 / 1_000_000 + gpt_completion_tokens * 15.00 / 1_000_000) * 95

                parsed = _json.loads(raw)
                break
            except Exception as _routing_e:
                print(f"[plan_item_execute] Роутинг попытка {_routing_attempt}: {_routing_e}", flush=True)
                if _routing_attempt < 2:
                    continue
                if raw is not None:
                    _escalate_plan_error(db, item.account_id, "plan_item_execute", "Не удалось разобрать ответ от GPT: " + raw[:200])
                    return {"status": "error", "message": "Не удалось разобрать ответ от GPT: " + raw[:200]}
                return {"status": "error", "message": f"Ошибка вызова GPT: {str(_routing_e)[:200]}"}

        steps = parsed.get("steps", [])
        skipped = parsed.get("skipped", [])

        if not steps:
            item.status = "needs_clarification"
            db.commit()
            return {"status": "needs_clarification", "reason": "; ".join(skipped) if skipped else "Действие не распознано"}

        import httpx as _httpx
        results = []
        had_error = False

        for step in steps:
            action = step.get("action")
            payload = step.get("payload", {})
            payload["account_id"] = item.account_id
            try:
                if action == "search_stock_photos":
                    payload.setdefault("source", "pexels")
                    payload.setdefault("count", 30)
                    _sp_result = None
                    _sp_last_exc = None
                    for _sp_attempt in range(2):
                        try:
                            r = _httpx.post("http://127.0.0.1:8000/api/avito/search_stock_photos", json=payload, timeout=120)
                            _sp_result = r.json()
                            break
                        except Exception as _sp_e:
                            _sp_last_exc = _sp_e
                            continue
                    if _sp_result is None:
                        _sp_result = {"status": "error", "message": f"search_stock_photos не выполнился за 2 попытки: {str(_sp_last_exc)[:200]}"}
                    results.append({"action": action, "result": _sp_result})
                elif action == "detect_category":
                    if not payload.get("niche"):
                        payload["niche"] = item.text
                    r = _httpx.post("http://127.0.0.1:8000/api/avito/detect_category", json=payload, timeout=60)
                    results.append({"action": action, "result": r.json()})
                elif action == "city_analysis":
                    r = _httpx.post("http://127.0.0.1:8000/api/parser/city_analysis_async", json=payload, timeout=30)
                    results.append({"action": action, "result": r.json()})
                elif action == "generate_banner":
                    payload.setdefault("format", "infographic")
                    r = _httpx.post("http://127.0.0.1:8000/api/banners/full_ai", json=payload, timeout=120)
                    results.append({"action": action, "result": r.json()})
                elif action == "ab_test":
                    r = _httpx.post("http://127.0.0.1:8000/api/tasks/create", json={"account_id": item.account_id, "task_type": "ab_test", "payload": payload}, timeout=30)
                    results.append({"action": action, "result": r.json()})
                elif action == "publish_listings":
                    topic = payload.get("topic", item.text)
                    count = payload.get("count", 10)
                    folder = payload.get("folder", "")

                    imgs_resp = _httpx.get(f"http://127.0.0.1:8000/api/avito/images_list?account_id={item.account_id}", timeout=15)
                    folders = imgs_resp.json().get("folders", {})
                    photo_urls = folders.get(folder, [])

                    if not photo_urls:
                        skipped.append(f"publish_listings пропущен: в папке '{folder}' нет фото")
                    else:
                        cat_data = {}
                        for _cat_attempt in range(1, 4):
                            try:
                                cat_resp = _httpx.post("http://127.0.0.1:8000/api/avito/detect_category", json={"account_id": item.account_id, "niche": topic}, timeout=60)
                                cat_data = cat_resp.json()
                                if cat_data.get("status") == "ok":
                                    break
                            except Exception as _cat_e:
                                print(f"[detect_category] Попытка {_cat_attempt}: {_cat_e}", flush=True)
                                cat_data = {}
                            if _cat_attempt < 3:
                                import time as _time_cat
                                _time_cat.sleep(2)

                        if cat_data.get("status") != "ok":
                            skipped.append(f"publish_listings пропущен для '{topic}': не удалось определить категорию Avito ({cat_data.get('message', 'нет причины')})")
                        else:
                            category = cat_data.get("category")

                            ads_resp = _httpx.post("http://127.0.0.1:8000/api/avito/generate_ads", json={
                                "topic": topic, "count": count,
                                "price_from": payload.get("price_from", 5000),
                                "price_to": payload.get("price_to", 50000),
                                "extra": "", "goal": "message", "length": "medium",
                                "use_my_ads": False, "account_id": item.account_id
                            }, timeout=120)
                            ads_data = ads_resp.json()
                            ads = ads_data.get("ads", [])

                            banner_url = None
                            try:
                                banner_resp = _httpx.post("http://127.0.0.1:8000/api/banners/full_ai", json={
                                    "account_id": item.account_id, "raw_description": topic, "format": "infographic"
                                }, timeout=120)
                                banner_data = banner_resp.json()
                                if banner_data.get("status") == "ok":
                                    banner_url = banner_data.get("url")
                            except Exception:
                                pass

                            feed_items = []
                            for i, ad in enumerate(ads):
                                ad_images = [banner_url] if banner_url else []
                                ad_images.append(photo_urls[i % len(photo_urls)])
                                try:
                                    _spun_title = spin(ad.get("title", ""))
                                except Exception:
                                    _spun_title = ad.get("title", "")
                                try:
                                    _spun_desc = spin(ad.get("description", ""))
                                except Exception:
                                    _spun_desc = ad.get("description", "")
                                feed_items.append({
                                    "id": f"boris-plan-{item.id}-{i}-{int(__import__('time').time())}",
                                    "title": _spun_title,
                                    "description": _spun_desc,
                                    "price": ad.get("price", 0),
                                    "category": category,
                                    "service_type": "",
                                    "address": payload.get("city", "Москва"),
                                    "images": ad_images,
                                    "params": _fill_category_params(topic, account_id=item.account_id)
                                })

                            feed_resp = _httpx.post("http://127.0.0.1:8000/api/avito/generate_feed", json={"account_id": item.account_id, "items": feed_items, "merge": True}, timeout=60)
                            results.append({"action": action, "result": {"status": "ok", "published": len(feed_items), "category": category, "feed": feed_resp.json()}})
                elif action == "create_draft_listings":
                    topic = payload.get("topic", item.text)
                    folder = payload.get("folder", "")
                    cities = payload.get("cities", [])
                    authored_texts = payload.get("authored_texts", [])
                    ai_count = payload.get("ai_count", 0)
                    count = payload.get("count", len(cities) if cities else (len(authored_texts) + ai_count if (authored_texts or ai_count) else 10))
                    photos_per_ad = max(int(payload.get("photos_per_ad", 5)), 1)
                    batch_label = payload.get("batch_label", topic)
                    batch_id = f"batch-{item.id}"
                    price_from = payload.get("price_from", 5000)
                    price_to = payload.get("price_to", 50000)
                    default_price = (price_from + price_to) // 2

                    imgs_resp = _httpx.get(f"http://127.0.0.1:8000/api/avito/images_list?account_id={item.account_id}", timeout=15)
                    folders = imgs_resp.json().get("folders", {})
                    photo_urls = folders.get(folder, [])

                    if not photo_urls:
                        skipped.append(f"create_draft_listings пропущен: в папке '{folder}' нет фото")
                    else:
                        cat_data = {}
                        for _cat_attempt in range(1, 4):
                            try:
                                cat_resp = _httpx.post("http://127.0.0.1:8000/api/avito/detect_category", json={"account_id": item.account_id, "niche": topic}, timeout=60)
                                cat_data = cat_resp.json()
                                if cat_data.get("status") == "ok":
                                    break
                            except Exception as _cat_e:
                                print(f"[detect_category] Попытка {_cat_attempt}: {_cat_e}", flush=True)
                                cat_data = {}
                            if _cat_attempt < 3:
                                import time as _time_cat
                                _time_cat.sleep(2)

                        if cat_data.get("status") != "ok":
                            # FALLBACK: detect_category не справился (частый случай - аккаунт без опубликованных
                            # объявлений). Пробуем resolve_category_for_account - тот же приоритет
                            # (API объявлений -> анонимный поиск по нише), но как самостоятельная функция.
                            try:
                                from app.services.category_resolver import resolve_category_for_account
                                _fallback = resolve_category_for_account(item.account_id, topic)
                                if _fallback.get("category_name"):
                                    cat_data = {"status": "ok", "category": {"name": _fallback["category_name"]}}
                                    print(f"[create_draft_listings] категория для '{topic}' найдена через fallback ({_fallback.get('source')}): {_fallback['category_name']}", flush=True)
                            except Exception as _fb_e:
                                print(f"[create_draft_listings] fallback resolve_category_for_account упал: {_fb_e}", flush=True)

                        if cat_data.get("status") != "ok":
                            skipped.append(f"create_draft_listings пропущен для '{topic}': не удалось определить категорию Avito ({cat_data.get('message', 'нет причины')})")
                        else:
                            category = cat_data.get("category")

                            # БАЗА ЗНАНИЙ ОБЯЗАТЕЛЬНЫХ ПОЛЕЙ КАТЕГОРИИ: раньше ServiceType/ServiceSubtype/
                            # WorkExperience/Guarantee и т.п. просто не заполнялись (код о них не знал) -
                            # чинили постфактум после публикации (инцидент "Книга жизни", 22 черновика).
                            # Теперь достаём шаблон категории и подбираем значения СТРОГО из его enum_values
                            # (или явно спрашиваем клиента, если поле неоднозначно) ДО создания черновиков.
                            from app.services.category_resolver import resolve_required_fields
                            _req_fields = resolve_required_fields(category_id=topic, niche=topic, api_category=category)
                            if _req_fields.get("status") != "ok":
                                skipped.append(f"create_draft_listings пропущен для '{topic}': не удалось получить обязательные поля категории ({_req_fields.get('message', 'нет причины')})")
                                continue
                            if _req_fields.get("needs_clarification"):
                                _clarify_text = "; ".join(q["question"] for q in _req_fields["needs_clarification"])
                                skipped.append(f"create_draft_listings пропущен для '{topic}': нужны уточнения от клиента - {_clarify_text}")
                                continue

                            _TAG_TO_SNAKE = {
                                "ServiceType": "service_type", "ServiceSubtype": "service_subtype",
                                "WorkExperience": "work_experience", "Guarantee": "guarantee",
                                "GoodsType": "goods_type", "GoodsSubType": "goods_subtype",
                                "Condition": "condition", "KitchenType": "kitchen_type",
                                "PriceType": "price_type", "Color": "color", "AdType": "ad_type",
                            }
                            _draft_extra_fields = {}
                            _draft_extra_params = {}
                            for _tag, _val in _req_fields["resolved"].items():
                                _snake = _TAG_TO_SNAKE.get(_tag)
                                if _snake:
                                    _draft_extra_fields[_snake] = _val
                                else:
                                    _draft_extra_params[_tag] = _val

                            from app.api.avito import spin, get_draft_image_set

                            # Уникализация партии: типовые габариты/материалы/цвета под тему товара
                            # (кэшируется по topic), спинтакс-блок дописывается в конец описания КАЖДОГО
                            # черновика ДО spin() - иначе объявления партии выглядят как копии друг друга
                            # (один и тот же заголовок/цена без реальных характеристик).
                            _specs = _get_category_specs(topic, db)
                            _specs_block = _build_specs_spintax_block(_specs)

                            drafts_resp = _httpx.get(f"http://127.0.0.1:8000/api/avito/drafts?account_id={item.account_id}", timeout=15)
                            existing_drafts = drafts_resp.json().get("drafts", [])

                            # Подтягиваем company info аккаунта, чтобы GPT знал РЕАЛЬНУЮ нишу бизнеса
                            # (без этого GPT видел только голое слово topic - например "Детские" -
                            # и мог выдумать случайную нишу вроде услуг няни вместо детской мебели)
                            from app.models.account import Account
                            _acc = db.query(Account).filter(Account.account_id == item.account_id).first()
                            _company_parts = []
                            if _acc:
                                if _acc.company_niche:
                                    _company_parts.append(f"Ниша бизнеса: {_acc.company_niche}")
                                if _acc.company_description:
                                    _company_parts.append(_acc.company_description)
                                if _acc.company_advantages:
                                    _company_parts.append(f"Преимущества: {_acc.company_advantages}")
                            _company_extra = payload.get("extra", "") or ". ".join(_company_parts)

                            new_drafts = []
                            city_idx = 0

                            # Часть A — авторские тексты владельца (A/B-вариант "authored"),
                            # уникализируются через spin() под каждый город, чтобы избежать бана за дубли
                            for text in authored_texts:
                                city = cities[city_idx] if city_idx < len(cities) else payload.get("city", "Москва")
                                text_with_specs = text.rstrip() + "\n\n" + _specs_block if _specs_block else text
                                try:
                                    spun_text = spin(text_with_specs)
                                except Exception:
                                    spun_text = text_with_specs
                                first_line_raw = text.strip().split("\n")[0]
                                try:
                                    first_line = spin(first_line_raw)[:70]
                                except Exception:
                                    first_line = first_line_raw[:70]
                                new_drafts.append({
                                    "id": f"draft-{item.id}-a{city_idx}-{int(__import__('time').time())}",
                                    "batch_id": batch_id,
                                    "batch_label": batch_label,
                                    "variant": "authored",
                                    "title": first_line,
                                    "description": spun_text,
                                    "price": payload.get("price_authored") or random.randint(price_from, price_to),
                                    "category": category,
                                    "category_id": topic,
                                    **_draft_extra_fields,
                                    "address": city,
                                    "images": get_draft_image_set([photo_urls[(city_idx + j) % len(photo_urls)] for j in range(len(photo_urls))], want=photos_per_ad),
                                    "params": dict(_draft_extra_params),
                                    "created_at": __import__('datetime').datetime.utcnow().isoformat()
                                })
                                city_idx += 1

                            # Часть B — тексты от ИИ (A/B-вариант "ai_generated")
                            # ВАЖНО: генерируем ПАЧКАМИ по 12 штук, а не всё разом одним вызовом GigaChat -
                            # при большом count (напр. 70) один ответ модели (лимит 4096 токенов) обрезается
                            # посередине и JSON становится невалидным, из-за чего парсится только малая часть
                            # (баг, из-за которого партия на 70 объявлений реально создавала всего 2-3 штуки).
                            remaining_count = max(count - len(new_drafts), ai_count)
                            ads = []
                            _chunk_size = 12
                            _remaining = remaining_count
                            _stall_count = 0
                            while _remaining > 0:
                                _this_chunk = min(_chunk_size, _remaining)
                                _got_this_round = 0
                                for _chunk_attempt in range(1, 4):
                                    try:
                                        # 180с не хватало: GigaChat на Freemium стабильно 402, каждый
                                        # чанк идёт через OpenAI-фолбэк (gigachat_pool.py), который
                                        # медленнее и может понадобиться дважды (основная генерация +
                                        # повторный запрос при невалидном JSON) - даём запас с потолком.
                                        ads_resp = _httpx.post("http://127.0.0.1:8000/api/avito/generate_ads", json={
                                            "topic": topic, "count": _this_chunk,
                                            "price_from": price_from, "price_to": price_to,
                                            "extra": _company_extra, "goal": "message", "length": "medium",
                                            "use_my_ads": False, "account_id": item.account_id
                                        }, timeout=400)
                                        ads_data = ads_resp.json()
                                        chunk_ads = ads_data.get("ads", [])
                                        if chunk_ads:
                                            ads.extend(chunk_ads)
                                            _got_this_round = len(chunk_ads)
                                            break
                                    except Exception as _chunk_e:
                                        print(f"[create_draft_listings] Пачка попытка {_chunk_attempt}: {_chunk_e}", flush=True)
                                        continue
                                if _got_this_round == 0:
                                    # ВАЖНО: пачка НЕ потеряна молча - если после 3 попыток так и не
                                    # получили ни одного текста, останавливаем цикл здесь
                                    _stall_count += 1
                                    if _stall_count >= 3:
                                        skipped.append(f"create_draft_listings: не удалось сгенерировать оставшиеся тексты (получено {len(ads)} из {remaining_count}, застряло на {_remaining} шт)")
                                        break
                                    continue
                                _stall_count = 0
                                # КРИТИЧНО: уменьшаем остаток на РЕАЛЬНО полученное количество, а не
                                # на запрошенное - GigaChat иногда возвращает меньше из-за обрезки JSON
                                # по лимиту токенов; раньше это молча "теряло" недостающие объявления
                                _remaining -= _got_this_round

                            if remaining_count > 0:
                                for ad in ads:
                                    city = cities[city_idx] if city_idx < len(cities) else payload.get("city", "Москва")
                                    try:
                                        _spun_title_b = spin(ad.get("title", ""))
                                    except Exception:
                                        _spun_title_b = ad.get("title", "")
                                    _desc_b = ad.get("description", "")
                                    _desc_with_specs_b = _desc_b.rstrip() + "\n\n" + _specs_block if _specs_block else _desc_b
                                    try:
                                        _spun_desc_b = spin(_desc_with_specs_b)
                                    except Exception:
                                        _spun_desc_b = _desc_with_specs_b
                                    new_drafts.append({
                                        "id": f"draft-{item.id}-b{city_idx}-{int(__import__('time').time())}",
                                        "batch_id": batch_id,
                                        "batch_label": batch_label,
                                        "variant": "ai_generated",
                                        "title": _spun_title_b,
                                        "description": _spun_desc_b,
                                        "price": ad.get("price") or random.randint(price_from, price_to),
                                        "category": category,
                                        "category_id": topic,
                                        **_draft_extra_fields,
                                        "address": city,
                                        "images": get_draft_image_set([photo_urls[(city_idx + j) % len(photo_urls)] for j in range(len(photo_urls))], want=photos_per_ad),
                                        "params": dict(_draft_extra_params),
                                        "created_at": __import__('datetime').datetime.utcnow().isoformat()
                                    })
                                    city_idx += 1

                            # НЕ генерируем баннер — это черновик, баннер добавит владелец сам

                            # БИЛЛИНГ: проверяем и списываем лимит объявлений перед сохранением.
                            # Если созданных больше, чем осталось в тарифе - режем партию по остатку
                            # и сообщаем клиенту (перейти на след.тариф или подождать обновления лимита).
                            _billing_notice = None
                            try:
                                from app.api.billing import get_status as _bill_status, check_and_consume as _bill_consume
                                _st = _bill_status(item.account_id)
                                if _st.get("unlimited"):
                                    _bill_consume(item.account_id, "listings", len(new_drafts))
                                    raise StopIteration  # пропускаем весь блок урезания партии - лимитов нет
                                _remaining_listings = _st["usage"].get("listings", {}).get("remaining", 0)
                                if len(new_drafts) > _remaining_listings:
                                    _chk = _bill_consume(item.account_id, "listings", _remaining_listings if _remaining_listings > 0 else 1)
                                    if _remaining_listings <= 0:
                                        new_drafts = []
                                        _billing_notice = _chk.get("message")
                                    else:
                                        _billing_notice = f"Лимит объявлений почти исчерпан: сохранено {_remaining_listings} из {len(new_drafts)}, остальные не созданы. " + (_chk.get("message") or "")
                                        new_drafts = new_drafts[:_remaining_listings]
                                else:
                                    _bill_consume(item.account_id, "listings", len(new_drafts))
                            except StopIteration:
                                pass
                            except Exception as _be:
                                print(f"[billing] listings check failed (пропускаем, не блокируем): {_be}", flush=True)

                            save_resp = _httpx.post("http://127.0.0.1:8000/api/avito/drafts", json={"account_id": item.account_id, "drafts": existing_drafts + new_drafts}, timeout=30)
                            authored_n = sum(1 for d in new_drafts if d["variant"] == "authored")
                            ai_n = sum(1 for d in new_drafts if d["variant"] == "ai_generated")
                            _res_obj = {"status": "ok", "drafted": len(new_drafts), "authored": authored_n, "ai_generated": ai_n, "batch_id": batch_id, "batch_label": batch_label, "category": category}
                            if _billing_notice:
                                _res_obj["billing_notice"] = _billing_notice
                                skipped.append(_billing_notice)
                            results.append({"action": action, "result": _res_obj})
                elif action == "apply_banner_to_batch":
                    from app.api.avito import apply_banner_to_batch_impl
                    batch_label = payload.get("batch_label", "")
                    _res = apply_banner_to_batch_impl(
                        item.account_id, batch_label,
                        banner_count=payload.get("banner_count", 5),
                        photos_per_ad=payload.get("photos_per_ad", 10),
                        folder=payload.get("folder", "")
                    )
                    if _res.get("notice"):
                        skipped.append(_res["notice"])
                    if _res.get("status") != "ok":
                        skipped.append(f"apply_banner_to_batch пропущен для партии '{batch_label}': {_res.get('message', 'не удалось')}")
                    else:
                        results.append({"action": action, "result": _res})
                elif action == "edit_active_listings":
                    direction = payload.get("direction", "")
                    id_prefix = payload.get("id_prefix", "")

                    from app.api.avito import spin, get_draft_image_set
                    from app.models.storage import Storage as _StorageModel

                    # Блокируем строку feed_items на время чтения+записи, чтобы параллельные
                    # шаги (например для разных направлений) не перезаписывали изменения друг друга
                    feed_row = db.query(_StorageModel).filter(
                        _StorageModel.account_id == item.account_id, _StorageModel.key == "feed_items"
                    ).with_for_update().first()

                    if not feed_row:
                        skipped.append("edit_active_listings пропущен: у аккаунта нет feed_items")
                    else:
                        feed_data = _json.loads(feed_row.value)

                        hist_row = db.query(_StorageModel).filter(
                            _StorageModel.account_id == item.account_id, _StorageModel.key == "gen_ads_history"
                        ).first()
                        gen_ads_history = _json.loads(hist_row.value) if hist_row else []

                        direction_lower = direction.lower()
                        matching_batches = [b for b in gen_ads_history if direction_lower in b.get("topic", "").lower() or any(w in b.get("topic", "").lower() for w in direction_lower.split() if len(w) > 3)]
                        if not matching_batches:
                            matching_batches = [b for b in gen_ads_history if any(w[:4] in b.get("topic", "").lower() for w in direction_lower.split() if len(w) > 4)]

                        gen_ads = []
                        for b in matching_batches:
                            for ad in b.get("ads", []):
                                gen_ads.append({**ad, "_batch_id": b.get("id")})

                        matching = [it for it in feed_data if it.get("id", "").startswith(id_prefix)]

                        if not matching:
                            skipped.append(f"edit_active_listings пропущен: не найдены объявления с префиксом '{id_prefix}'")
                        elif not gen_ads:
                            skipped.append(f"edit_active_listings пропущен: нет готовых партий шаблонов для направления '{direction}' — сначала сгенерируйте их в разделе 'Генератор объявлений через ИИ' (тема должна содержать слова из направления)")
                        else:
                            banner_url = None
                            try:
                                banner_resp = _httpx.post("http://127.0.0.1:8000/api/banners/full_ai", json={
                                    "account_id": item.account_id, "raw_description": direction, "format": "infographic"
                                }, timeout=120)
                                banner_data = banner_resp.json()
                                if banner_data.get("status") == "ok":
                                    banner_url = banner_data.get("url")
                            except Exception:
                                pass

                            moscow_locations = [
                                "Москва, м. Тверская", "Москва, м. Сокольники", "Москва, м. Юго-Западная",
                                "Москва, м. Строгино", "Москва, м. Марьино", "Москва, м. Бибирево",
                                "Москва, м. Отрадное", "Москва, м. Свиблово", "Москва, м. Тимирязевская",
                                "Москва, м. Царицыно", "Москва, м. Новогиреево", "Москва, м. Перово",
                                "Москва, м. Щукинская", "Москва, м. Бабушкинская", "Москва, м. Медведково",
                                "Москва, м. Митино", "Москва, м. Люблино", "Москва, м. Кузьминки",
                                "Москва, м. Печатники", "Москва, м. Выхино", "Москва, м. Жулебино",
                                "Москва, м. Коньково", "Москва, м. Тёплый Стан", "Москва, м. Ясенево",
                                "Москва, м. Беляево", "Москва, м. Академическая", "Москва, м. Университет",
                                "Москва, м. Профсоюзная", "Москва, м. Орехово", "Москва, м. Домодедовская",
                            ]

                            updated_count = 0
                            for it in matching:
                                source = random.choice(gen_ads)
                                it["title"] = spin(source.get("title", it["title"]))[:100]
                                it["description"] = spin(source.get("description", it["description"]))
                                it["price"] = source.get("price", it["price"])
                                it["address"] = random.choice(moscow_locations)
                                it["source_batch_id"] = source.get("_batch_id")
                                if banner_url:
                                    old_images = [img for img in it.get("images", []) if "/banners/" not in img]
                                    it["images"] = ([banner_url] + old_images)[:10]
                                updated_count += 1

                            # Записываем изменения обратно в feed_data и сохраняем в рамках той же
                            # заблокированной строки - блокировка снимется только после db.commit()
                            feed_row.value = _json.dumps(feed_data, ensure_ascii=False)
                            db.commit()

                            results.append({"action": action, "result": {"status": "ok", "updated": updated_count, "direction": direction, "source_templates_used": len(gen_ads)}})
                elif action == "generate_templates":
                    topic = payload.get("topic", item.text)
                    count = payload.get("count", 10)
                    ads_resp = _httpx.post("http://127.0.0.1:8000/api/avito/generate_ads", json={
                        "topic": topic, "count": count,
                        "price_from": payload.get("price_from", 5000),
                        "price_to": payload.get("price_to", 50000),
                        "extra": "", "goal": "message", "length": "medium",
                        "use_my_ads": False, "account_id": item.account_id
                    }, timeout=120)
                    ads_data = ads_resp.json()
                    ads = ads_data.get("ads", [])

                    load_resp = _httpx.get(f"http://127.0.0.1:8000/api/storage/load?account_id={item.account_id}&key=templates", timeout=15)
                    existing = load_resp.json().get("value") or []

                    new_templates = []
                    for i, ad in enumerate(ads):
                        new_templates.append({
                            "id": int(__import__('time').time() * 1000) + i,
                            "name": ad.get("title", topic)[:60],
                            "titleTemplate": ad.get("title", ""),
                            "description": ad.get("description", ""),
                            "priceType": "original",
                            "priceModifier": 0,
                            "cities": ["Москва"],
                            "category": ""
                        })

                    combined = existing + new_templates
                    _httpx.post("http://127.0.0.1:8000/api/storage/save", json={
                        "account_id": item.account_id, "key": "templates", "value": combined
                    }, timeout=15)
                    results.append({"action": action, "result": {"status": "ok", "created": len(new_templates), "total_templates": len(combined)}})
                else:
                    skipped.append(f"неизвестное действие: {action}")
            except Exception as e:
                had_error = True
                _escalate_plan_error(db, item.account_id, f"plan_item_execute:{action}", str(e)[:300])
                results.append({"action": action, "error": str(e)[:300]})

        _STEP_COST_ESTIMATE_RUB = {
            "search_stock_photos": 0,
            "detect_category": 0,
            "city_analysis": 0,
            "generate_banner": 3.6,
            "ab_test": 0,
            "publish_listings": 3.6,
        }
        steps_cost_rub = sum(_STEP_COST_ESTIMATE_RUB.get(r.get("action"), 0) for r in results)
        total_cost_rub = round(gpt_cost_rub + steps_cost_rub, 2)

        item.status = "done" if not had_error else "needs_clarification"
        db.commit()
        return {
            "status": "ok", "steps_done": len(results), "results": results, "skipped": skipped,
            "cost": {
                "gpt_routing_rub": round(gpt_cost_rub, 2),
                "steps_estimate_rub": round(steps_cost_rub, 2),
                "total_rub": total_cost_rub
            }
        }
    except Exception as e:
        if item is not None:
            _escalate_plan_error(db, item.account_id, "plan_item_execute", str(e)[:300])
        return {"status": "error", "message": f"Непредвиденная ошибка: {str(e)[:300]}"}
    finally:
        # СТРАХОВКА: если ни один из путей выше (return/except) не проставил терминальный
        # статус - пункт плана иначе навсегда зависает в "running" для фронта, который
        # опрашивает статус с сервера. Гарантированно снимаем "running" на любом выходе.
        if item is not None and item.status == "running":
            item.status = "needs_clarification"
            db.commit()
        db.close()
