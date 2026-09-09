# -*- coding: utf-8 -*-
"""MASTER TASK — фабрика автономной разработки BORIS.

Здесь не выполняется работа. Здесь backlog владельца превращается в граф
задач, который дальше ведёт уже существующий контур: лид ставит наряды,
исполнители работают, лид принимает. Второго оркестратора не появляется.

Правила, зашитые в модуль:
  · дубли не создаются — задача ищется по ключу в заголовке;
  · зависимости настоящие: параллельно идёт только то, что не пересекается;
  · блокировки по путям и подсистемам, чтобы два исполнителя не правили одно;
  · приоритет P0 у боевых инцидентов, остальное ниже.

    venv/bin/python3 -m app.ext_api.master inspect     # что уже есть
    venv/bin/python3 -m app.ext_api.master plan        # что будет создано
    venv/bin/python3 -m app.ext_api.master apply       # создать недостающее
    venv/bin/python3 -m app.ext_api.master dashboard   # сводка владельцу
"""
import json, re

from . import db, dev, a2a, sched
from .errors import ApiError

LEAD_KEY = {"scope_set": {"read", "write", "tasks"}, "actor_type": "ai_agent",
            "actor_name": "claude-lead", "name": "claude-lead", "id": None,
            "max_items_per_call": 200, "allowed_accounts": None}
EXECUTOR = "gpt-executor"

FORBIDDEN = [
    "публикация во внешние площадки без явного разрешения владельца",
    "любые платные операции: генерация изображений, баннеров, платные API",
    "массовая рассылка писем и сообщений",
    "разрушительные изменения схемы БД: только добавление, совместимое назад",
    "правки nginx, системной конфигурации, чужих служб и секретов",
    "автоматические действия с продвижением без dry-run и стратегии отката",
]

EVIDENCE = ["вывод db_query или repo_read", "вывод команды сборки, тестов или обхода"]

# Общий стандарт доказательств: он одинаков для всех задач, и повторять его
# в каждой спецификации незачем.
COMMON_ACCEPTANCE = [
    "Причина названа доказанной: вывод запроса, журнала или обхода приложен, "
    "а не пересказан",
    "Правки предложены через proposePatch, сборка или тесты чистые",
    "Отдельно перечислено, чего не хватает от владельца, и это не смешано "
    "с техническими блокерами",
]

# key · title · goal · locks · depends · priority
BACKLOG = [
    ("P0_VK_POSTING", "P0: публикация во ВКонтакте не работает",
     "Боевой инцидент. Найти, когда была последняя успешная публикация во ВК и что "
     "происходит с попытками сейчас: очередь, воркер, cron или systemd, состояние "
     "токенов и авторизации, ответы API ВК, сеть и зависимость от прокси, дедупликация, "
     "валидация payload. Назвать первопричину и починить. Доказать управляемым E2E: "
     "одна публикация от постановки до подтверждённого результата.",
     ["backend/posting_runner.py", "backend/app/api/posting.py",
      "backend/posting_stats.py", "backend/tests/test_social_posting_ha_contract.py",
      "backend/tests/test_social_banner_delivery.py", "backend/tests/test_ocean_autopost_contract.py",
      "vk"], [], 98),

    ("P0_TG_POSTING", "P0: публикация в Telegram не работает",
     "То же по Telegram: последняя успешная отправка, последние отказы, очередь, "
     "транспорт и маршруты, воркер и расписание, ответы Telegram API, дедупликация. "
     "Первопричину доказать и устранить, результат подтвердить управляемым E2E. "
     "Инцидент ВК и инцидент Telegram не смешивать: причины могут быть разные.",
     ["backend/posting_runner.py", "backend/app/api/posting.py",
      "backend/boris_background_worker.py", "backend/tests/test_social_posting_ha_contract.py",
      "backend/tests/test_social_banner_delivery.py", "backend/tests/test_ocean_autopost_contract.py",
      "telegram"], [], 98),

    ("P0_PROXY_INFRA", "P0: разобрать состояние прокси-инфраструктуры",
     "Пришло оповещение «RU proxy networkpw dead». Не принимать это за факт из-за "
     "десяти неответивших портов. Найти все настроенные пулы, определить, какие задачи "
     "какой пул используют, проверить точки входа и разметить LIVE / DEAD / DEGRADED, "
     "посмотреть ротацию, таймауты, авторизацию, гео и кэш здоровья. Главный вопрос: "
     "какая конкретная функция BORIS зависит от российского прокси. Отдельно доказать "
     "или опровергнуть связь прокси с публикацией во ВК, публикацией в Telegram, "
     "разбором Avito и анализом конкурентов.",
     ["proxy"], [], 97),

    ("TG_INBOX_WIRE", "Задания из Telegram через существующего бота",
     "Владелец должен ставить задачи прямо в теме Telegram, и для этого НЕ нужен второй "
     "бот: конфликт 409 возникает только при чтении очереди обновлений, отправка ничем "
     "не занята. Найти в BORIS обработчик, который уже получает обновления боевого бота "
     "(вебхук или опрос), и отдать каждое обновление в готовую функцию "
     "app.ext_api.inbox.handle_update(update) — она сама отфильтрует чужие чаты и темы "
     "и ответит владельцу. Проверить `python3 -m app.ext_api.inbox webhook` — он "
     "покажет, вебхук у бота или опрос. Ничего не переписывать, добавить вызов.",
     ["backend/app/telegram_bot.py"], [], 90),

    ("AVITO_CATEGORY_REGISTRY", "Avito: единый реестр категорий и полей",
     "У каждой категории Avito свой набор обязательных и дополнительных полей — "
     "десять-двадцать и больше. Нужен один источник истины: категория, подкатегория, "
     "тип товара или услуги, обязательные и необязательные поля, допустимые значения "
     "перечислений, типы, ограничения, значения по умолчанию, источник, версия и дата "
     "обновления. Сначала проверить, что из этого в BORIS уже есть, и достроить, а не "
     "заводить второй источник истины.",
     ["backend/app/services/category_resolver.py", "backend/app/services/feed_param_normalizer.py"], [], 88),

    ("AVITO_ITEM_RESOLUTION", "Avito: категория определяется у каждого объявления",
     "Категория и параметры определяются НА УРОВНЕ ОБЪЯВЛЕНИЯ, а не клиента и не фида. "
     "Кухня, шкаф-купе, диван и матрас у одного клиента получают разные категории и "
     "разные наборы полей. Собрать разрешение: текст товара плюс настройки клиента плюс "
     "определитель ниши плюс реестр категорий дают кандидата с уверенностью, набор "
     "обязательных полей и выведенные значения. У каждого значения хранить источник: "
     "сказал клиент, взято из данных товара, правило категории, выведено, значение по "
     "умолчанию, предложено моделью. При низкой уверенности не публиковать, а ставить "
     "объявлению состояние «нужны данные» или «нужна проверка».",
     ["backend/app/services/category_resolver.py", "backend/app/services/feed_param_normalizer.py"], ["AVITO_CATEGORY_REGISTRY"], 87),

    ("AVITO_FEED_VALIDATOR", "Avito: проверка фида до публикации",
     "Перед выгрузкой проверять: категория существует, обязательные поля заполнены, "
     "значения перечислений допустимы, типы верные, цена, адрес и медиа на месте, "
     "несовместимых полей нет. Один негодный товар не должен ронять весь фид: у каждого "
     "объявления своё состояние. Показать на реальном фиде клиента, что негодные "
     "объявления изолируются, а годные выгружаются.",
     ["backend/app/services/category_resolver.py", "backend/app/services/feed_param_normalizer.py"], ["AVITO_ITEM_RESOLUTION"], 86),

    ("AVITO_CATEGORY_MEMORY", "Avito: запоминание подтверждённых соответствий",
     "Если оператор один раз подтвердил соответствие «кухни на заказ → такие поля», "
     "BORIS обязан сохранить это и применять дальше сам. Нужна версионность: изменение "
     "схемы Avito не должно оставлять старые правила навсегда.",
     ["backend/app/services/category_resolver.py", "backend/app/services/feed_param_normalizer.py"], ["AVITO_ITEM_RESOLUTION"], 80),

    ("ZERO_TOUCH_LAUNCH", "Запуск клиента без ручной сборки",
     "Нового клиента нужно заводить без того, чтобы владелец собирал процесс руками: "
     "регистрация, данные компании, товары и направления, определение ниши, определение "
     "категорий, подключение аккаунтов, контент, кампания, фид, проверка, готовность к "
     "запуску, управляемый запуск, наблюдение, отчёты. Готовность обязана быть машинно "
     "читаемой: ГОТОВ или НЕ ГОТОВ с конкретным списком блокеров. Продолжать "
     "существующую фабрику, не создавая новую.",
     ["backend/app/api/client_launch_control.py", "backend/app/services/onboarding_factory_sync.py"], ["AVITO_FEED_VALIDATOR"], 82),

    ("CLIENT_CONFIG_AUDIT", "Настройки клиента: одна иерархия, без второй подсистемы",
     "Проверить существующий определитель настроек и происхождение значений: иерархия "
     "владелец → аккаунт → значение по умолчанию, права и подключённые пакеты, МОП, РОП, "
     "тариф, ниша, направления, цели, стоимость заявки, бюджет, гео, настройки контента "
     "и публикации. Новую подсистему настроек не заводить.",
     ["backend/app/config.py", "backend/app/api/admin_clients.py"], [], 74),

    ("KPI_AUTOPILOT", "KPI-автопилот по каждому активному клиенту",
     "По каждому активному клиенту ежедневно: цель по заявкам в день, целевая стоимость "
     "заявки, бюджет, фактические заявки, фактическая стоимость, разрыв, рекомендованные "
     "действия, что BORIS вправе сделать сам и что требует подтверждения. Система обязана "
     "каждый день отвечать: выполняем план или нет, почему, что сделал BORIS и что должен "
     "сделать человек.",
     ["backend/app/services/autonomy.py", "backend/app/services/intraday.py", "backend/app/services/marketing_rules.py", "backend/app/services/optimization_state.py"], ["CLIENT_CONFIG_AUDIT"], 76),

    ("DAILY_OWNER_REPORT", "Единый ежедневный отчёт владельцу",
     "Один отчёт вместо россыпи: Avito, ВК, Telegram, звонки, МОП, РОП, действия людей, "
     "заявки, стоимость заявки, бюджет, проблемы, что сделано, что дальше. Ключевое "
     "требование: отчёт должен реально доставляться, а не только формироваться — "
     "доставку доказать.",
     ["backend/app/api/inbox_daily.py", "backend/app/ext_api/owner_daily_job.py", "backend/daily_inbox_report.py"], ["DEV_CALLS_REPORT_FIX"], 78),

    ("MESSAGES_HUB", "Единый центр сообщений: довести до боевого состояния",
     "Проверить полноту по Avito, ВК, Telegram и другим подключённым каналам: единая "
     "личность диалога, дедупликация, канал, клиент и аккаунт, стадия лида, черновик "
     "ответа, кто ведёт диалог — человек или BORIS, история действий. Второй мессенджер "
     "не создавать, доводить существующий.",
     ["backend/app/api/messenger.py", "backend/app/models/messenger_message.py"], [], 72),

    ("MOP_CYCLE", "МОП: полный цикл AI-менеджера и его тренировки",
     "Проверить весь цикл: входящий лид, классификация, квалификация, ответ, дожим, "
     "работа с возражением, следующий шаг, состояние в CRM, передача человеку, правила "
     "остановки. Отдельно — программу тренировок: как оценивается качество, какие ошибки, "
     "где слабые места, как идёт обучение, как проверяется повторно и видна ли динамика.",
     ["backend/app/mop_core.py", "backend/app/api/messenger.py", "backend/app/api/mop_training.py", "backend/app/api/mop_combat_training.py"], ["MESSAGES_HUB"], 70),

    ("ROP_CYCLE", "РОП: контроль качества и эскалация",
     "Проверить контроль над МОПом и над людьми-менеджерами: KPI, разборы диалогов, "
     "упущенные лиды, время ответа, качество, рекомендации, задачи, эскалация владельцу.",
     ["backend/rop_auto.py", "backend/rop_digest.py", "backend/rop_batch.py", "backend/app/api/inbox_daily.py", "backend/app/api/messenger.py"], ["MOP_CYCLE"], 68),

    ("CALL_ANALYSIS", "Звонки: приём, расшифровка, разбор, стоимость",
     "Проверить весь путь: приём звонков, аудио, расшифровка, разбор, учёт стоимости, "
     "дедупликация, уведомления, связь с МОП и РОП, попадание в отчёт владельцу. Старые "
     "показатели не считать текущим состоянием без проверки.",
     ["backend/app/services/telephony_core.py", "backend/app/api/calltracking.py"], ["DEV_CALLS_REPORT_FIX"], 75),

    ("MEDIA_STUDIO", "Медиа, фотостудия и баннеры",
     "Проверить хранилище медиа, папки, ресурсы, ссылки, мост до кампании, генерацию фото, "
     "правку по образцу, генерацию баннеров, учёт стоимости и использование галереи. "
     "Платные генерации в этой задаче не запускать — только разбор состояния и правки кода.",
     ["backend/app/services/media_service.py", "backend/app/services/media_storage.py", "backend/app/services/photo_studio.py", "backend/app/services/image_service.py", "backend/app/services/banner_design_v4.py"], [], 66),

    ("CONTENT_ENGINE", "Контент: заголовки, описания, уникализация",
     "Проверить заголовки, описания, уникализацию, баннеры, изображения, массовую "
     "генерацию, контент на уровне товара, городские варианты и защиту от дублей.",
     ["backend/app/services/jobs.py", "backend/app/services/campaign_service.py", "backend/app/services/media_service.py"], [], 65),

    ("SOCIAL_PRODUCTION", "Соцсети: довести ВК и Telegram до стабильной работы",
     "После устранения инцидентов довести до боевого состояния: планирование, контент, "
     "медиа, очередь, публикация, повторы, дедупликация, статусы, аналитика, отчёт.",
     ["backend/posting_runner.py", "backend/app/api/posting.py", "backend/boris_background_worker.py"], ["P0_VK_POSTING", "P0_TG_POSTING"], 71),

    ("PROSPECTING", "Поиск клиентов: продолжить существующий конвейер",
     "Названия компаний → кандидаты в поиске → оценка домена → сайт → публичные контакты → "
     "проверка качества → запись о потенциальном клиенте. Учитывать существующий пул "
     "прокси. Платные API и почтовую отправку без отдельного разрешения не включать.",
     ["backend/app/services/contact_prospector.py", "backend/app/services/prospect_discovery.py", "backend/app/services/prospecting.py", "backend/app/services/prospect_replenisher.py"], ["P0_PROXY_INFRA"], 60),

    ("EMAIL_OUTREACH", "Почтовые касания: разделить поиск и отправку",
     "Проверить текущее состояние и развести две вещи: поиск лидов и доставку писем. "
     "Массовую рассылку автоматически не запускать — только при безопасных лимитах и "
     "явно разрешённой политике.",
     ["backend/app/services/contact_outreach.py", "backend/app/services/email_queue.py", "backend/app/services/email_service.py", "backend/app/services/client_mailboxes.py", "backend/app/services/prospect_campaigns.py"], ["PROSPECTING"], 55),

    ("CRM_INTEGRATIONS", "CRM: своя и внешние",
     "Проверить внутреннюю CRM, интеграции с Битрикс и amoCRM, сопоставление сущностей, "
     "синхронизацию лидов, владение записью, дедупликацию и безопасность записи.",
     ["backend/app/services/crm_service.py", "backend/app/api/crm.py"], ["MESSAGES_HUB"], 64),

    ("COMMAND_CENTER", "Командный центр: от команды до аудита",
     "Проверить существующие этапы. Текстовая или голосовая команда должна проходить путь: "
     "распознан замысел → операция → право на неё → подтверждение → выполнение → запись в "
     "историю → стоимость.",
     ["backend/app/services/command_flow.py", "backend/app/services/command_executor.py", "backend/app/services/command_policy.py", "backend/app/services/command_audit.py", "backend/app/services/command_store.py"], [], 62),

    ("CPX_SAFETY", "Продвижение: защита от повторения старой аварии",
     "Учесть прошлый боевой инцидент с отключением продвижения. Никаких автоматических "
     "разрушительных действий без корректной точки отсчёта, лимитов, холостого прогона, "
     "доказательств и стратегии отката. Доказать, что тот же класс ошибки повториться "
     "не может.",
     ["backend/app/services/marketing_money_policy.py", "backend/app/services/autonomy.py", "backend/app/services/intraday.py"], [], 79),

    ("AI_COST_ACCOUNTING", "Экономика: честный учёт стоимости AI",
     "У каждого действия модели должен быть корректный учёт: текст OpenAI, изображения "
     "OpenAI, расшифровка, Claude, прочие провайдеры. Отделять исполнение по подписке от "
     "платного вызова API. Разрезы: по дням, клиентам, функциям, провайдерам и моделям.",
     ["backend/app/services/ai_budget.py", "backend/app/services/ai_cost_alerts.py", "backend/app/services/ai_guard_audit.py"], [], 73),
    # --- добавлено 21.08 по требованиям владельца: ни одно направление не
    # должно жить только в переписке. Каждый пункт — отдельная задача с
    # приёмкой, иначе он теряется при первой же уборке очереди.

    ("ADS_WORKSPACE_VIEW", "Объявления: просмотр результата как на Avito",
     "Достроить существующий Центр объявлений, ничего не удаляя: карточка "
     "объявления с галереей, главным фото, заголовком, ценой, описанием, "
     "характеристиками, категорией, статусом и состоянием фида.",
     ["frontend/app/dashboard", "backend/app/api"], [], 87),

    ("ADS_EDIT_FIELDS", "Объявления: правка заголовка, описания, цены",
     "Правка прямо в карточке с сохранением в реальные данные кампании и "
     "черновиков, а не в состояние интерфейса. История версий и возврат.",
     ["frontend/app/dashboard", "backend/app/api"], ["ADS_WORKSPACE_VIEW"], 87),

    ("ADS_MEDIA_FOLDERS", "Медиатека клиента по папкам",
     "Фото клиента, сток, баннеры, использованные, отклонённые — раздельно и "
     "строго в пределах клиента. Перестановка порядка, главное фото, замена, "
     "удаление из объявления без удаления файла.",
     ["frontend/app/dashboard", "backend/app/services"], ["ADS_WORKSPACE_VIEW"], 87),

    ("ADS_BULK_EDIT", "Массовое редактирование объявлений",
     "Выбрать 10/50/100 и поменять призыв, фразу, заголовки, изображения, "
     "пересобрать баннеры. Перед применением — предпросмотр и оценка "
     "радиуса поражения.",
     ["frontend/app/dashboard", "backend/app/api"], ["ADS_EDIT_FIELDS"], 84),

    ("ADS_LEARN_FROM_EDIT", "Обучение BORIS на правках владельца",
     "Сохранять было, стало, инструкцию словами, клиента, кампанию, поле и "
     "область действия. Подтверждённое правило обязано применяться в "
     "следующих генерациях — это проверяется двумя результатами.",
     ["backend/app/services", "backend/app/api"], ["ADS_EDIT_FIELDS"], 86),

    ("STOCK_COLLECTOR_WIRE", "Подключить существующий сборщик изображений",
     "Найти рабочий модуль сбора изображений BORIS и подключить его как "
     "источник для баннеров. Новый сборщик не писать. Ранжировать "
     "кандидатов и хранить источник.",
     ["backend/app/services"], [], 86),

    ("BANNER_FACTORY_SERVER", "Баннерная фабрика как постоянный модуль",
     "Производство баннеров живёт на сервере и продолжается при закрытом "
     "чате: задание, фото, анализ, спецификация, рендер, проверка, "
     "починка, хранение, показ в редакторе.",
     ["backend/app/ext_api", "backend/app/services"],
     ["STOCK_COLLECTOR_WIRE"], 85),

    ("BANNER_VISUAL_QA", "Двое ворот качества баннера",
     "Технические ворота и коммерческие. Готово только когда оба пройдены. "
     "Заглушка вместо героя, отсутствие героя, чужой водяной знак и "
     "нерелевантное фото — жёсткий отказ независимо от оценки.",
     ["backend/app/ext_api"], ["BANNER_FACTORY_SERVER"], 85),

    ("ECS_PRESERVE_EXTEND", "ЕЦС: сохранить существующее и достроить",
     "Ничего не удалять. Добавить связанные зоны: сообщения, CRM, МОП и "
     "тренировка МОПа — как части одного экрана, а не отдельные страницы.",
     ["frontend/app", "backend/app/api"], [], 86),

    ("CRM_PARITY", "CRM видна всем клиентам одинаково",
     "У части новых клиентов CRM не появляется. Найти причину в выдаче прав, "
     "привязке данных или интерфейсе. Вторую CRM не создавать — распространить "
     "работающую.",
     ["backend/app/api", "frontend/app"], ["ECS_PRESERVE_EXTEND"], 88),

    ("MOP_REAL_ANSWERS", "Фактические ответы МОПа в ЕЦС и CRM",
     "Показывать переписку целиком: сообщение клиента, намерение, стадия, "
     "уверенность, черновик ответа, фактически отправленный ответ, время и "
     "следующее действие. Не только внутренний разбор.",
     ["backend/app/api", "frontend/app"], ["ECS_PRESERVE_EXTEND"], 87),

    ("MOP_TRAINING_CENTER", "Тренировка МОПа: рабочий центр настроек",
     "Роль, цель, тон, стадии сделки, правила ответа, запрещённые фразы, "
     "вопросы и возражения, скрипты, хорошие и плохие примеры, правила "
     "следующего шага, квалификация лида, передача человеку.",
     ["backend/app/services", "frontend/app"], ["MOP_REAL_ANSWERS"], 86),

    ("MOP_SIMULATOR", "Симулятор диалога для обучения МОПа",
     "Клиент пишет, МОП отвечает, владелец оценивает: хорошо, плохо, "
     "исправить, так отвечать всегда, так не отвечать. Исправление "
     "превращается в правило конкретного клиента.",
     ["backend/app/services", "frontend/app"], ["MOP_TRAINING_CENTER"], 85),

    ("MOP_ZERO_OPENAI", "МОП и тренировка без OpenAI",
     "Симуляция клиента, ответ, намерение, стадия, уверенность, оценка, "
     "исправление и правила — через серверный транспорт Claude по подписке. "
     "Запасного пути в OpenAI нет.",
     ["backend/app/services", "backend/app/ext_api"], [], 90),

    ("CALL_STT_LOCAL", "Расшифровка звонков без платного API",
     "Найти существующий контур распознавания речи. Если он ходит в платный "
     "Whisper — временно перевести на локальный faster-whisper, если железо "
     "позволяет. Новый платный сервис не подключать. Плохую расшифровку "
     "помечать низкой уверенностью, а не выдавать за успех.",
     ["backend/app/services"], [], 89),

    ("CALL_AUDIT_CLAUDE", "Разбор звонка через Claude",
     "По расшифровке: краткое содержание, оценка качества, возражения, "
     "упущенные возможности, ошибки менеджера, следующее действие, стадия "
     "сделки, соответствие скрипту, рекомендации для обучения.",
     ["backend/app/services"], ["CALL_STT_LOCAL"], 88),

    ("NEW_ACCOUNT_PARITY", "Новый аккаунт получает всё сразу",
     "После регистрации клиент автоматически получает редактор объявлений, "
     "ЕЦС, CRM, МОП, тренировку, историю диалогов, ответы, привязки кампаний "
     "и фида, папки медиа и конфигурацию. Ситуации «у старого работает, у "
     "нового нет» быть не должно.",
     ["backend/app/services", "backend/app/api"], ["CRM_PARITY"], 87),

    ("BYTOVKI_REFERENCE", "Бытовки как образец, который нельзя сломать",
     "У бытовок отображение сообщений и ответов менеджера уже работает. "
     "Найти его договорённости, API, хранилище и привязки, распространить на "
     "остальных и доказать, что у самих бытовок ничего не сломалось.",
     ["backend/app/api", "frontend/app"], ["MOP_REAL_ANSWERS"], 88),

    ("CLIENT_ISOLATION_GATE", "Ворота против утечки между клиентами",
     "CRM, диалоги, фото, сток, баннеры, правила МОПа, обучение, объявления и "
     "фид — данные одного клиента не могут попасть другому. Нужен отдельный "
     "регрессионный тест именно на утечку.",
     ["backend/app/api", "backend/app/services"], [], 89),

    ("NON_DESTRUCTIVE_GATE", "Ворота против поломки работающего",
     "Перед изменением зоны зафиксировать существующие экраны, действия, "
     "маршруты, API, данные и сценарии. Новая функция ценой сломанной старой "
     "— это отказ и откат, а не компромисс.",
     ["backend/app", "frontend/app"], [], 90),

    ("BLAST_RADIUS_GUARD", "Предохранитель массовых правок",
     "Любая массовая правка обязана оценить число затрагиваемых строк до "
     "записи и остановиться, если оно превышает ожидаемое. Снимок и откат "
     "обязательны. Ожидали двадцать, набралось восемнадцать тысяч — стоп.",
     ["backend/app/ext_api"], [], 92),

    ("OWNER_WATCHDOG_VERIFY", "Сторож владельца на боевом контуре",
     "Каждые полчаса отчёт по конечному результату, обнаружение ложной "
     "зелени, обнаружение остановок, автоматическая починка и отдельная "
     "мёртвая рука на случай молчания самого сторожа.",
     ["backend/app/ext_api"], [], 91),

    ("MASTER_COVERAGE_GUARD", "Покрытие требований не теряется",
     "Каждое подтверждённое требование владельца обязано иметь пункт "
     "MASTER, задачу и наряд. Требование, живущее только в переписке, "
     "считается потерянным. Уборка очереди не имеет права снести продуктовую "
     "задачу — она снимает старый наряд, а не требование.",
     ["backend/app/ext_api"], [], 92),

    ("CONCURRENCY_LADDER", "Безопасная лестница потоков",
     "Поднимать одновременность 4 → 5 → 6 → 7 → 8 только при устойчивых "
     "показателях процессора, памяти, очереди приёмки, ошибок и прибавки у "
     "клиентов. Оптимизировать результаты в час, а не число занятых воркеров.",
     ["backend/app/ext_api"], [], 83),

    # --- добавлено 21.08: владелец перестаёт быть посредником между Claude и
    # терминалом. Наблюдение вместо переноса команд.

    ("SERVER_COMMAND_BRIDGE", "Серверный мост исполнения команд",
     "Постоянный серверный мост между фабрикой и исполнением. Перед любым "
     "действием — вопрос «сервер может сам?». Может — выполняет, владельцу "
     "команду не отдаёт. Мост ограничен списком разрешённых операций, "
     "внешнему агенту root-оболочка не даётся. Опасные массовые правки "
     "проходят снимок, подсчёт строк и предохранитель. Секреты в вывод не "
     "попадают.",
     ["backend/app/ext_api", "backend/app/services"], [], 93),

    ("LIVE_OPERATIONS_CENTER", "Экран «Работа BORIS» в реальном времени",
     "Отдельная вкладка: наверху готовность клиентов и реальные результаты, "
     "карточки четырёх клиентов с готовностью по составляющим, живая лента "
     "действий, панель MASTER, фильтры и история за час, три часа, сутки. "
     "Существующую фабрику не переписывать — инструментировать её точки.",
     ["frontend/app", "backend/app/api"], ["SERVER_COMMAND_BRIDGE"], 91),

    ("LIVE_TERMINAL_STREAM", "Живой терминал только на просмотр",
     "Поток фактического серверного исполнения: команда, каталог, агент, "
     "задача, клиент, время, длительность, код возврата, вывод. Длинный "
     "вывод сворачивается, ошибки выделяются, секреты вычищаются до записи, "
     "а не в браузере.",
     ["backend/app/ext_api", "frontend/app"], ["SERVER_COMMAND_BRIDGE"], 90),

    ("OPS_EVENT_STREAM", "Событийный поток и его хранение",
     "События разных видов: план, наряд, чтение и правка файла, запрос к базе, "
     "команда, сборка, тесты, приёмка, выкатка, откат, починка, бизнес-"
     "результат, блокировка, решение владельца. Скрытые рассуждения модели не "
     "показывать. История переживает перезапуск, прерванная операция "
     "восстанавливается.",
     ["backend/app/ext_api"], ["LIVE_OPERATIONS_CENTER"], 90),

    ("OPS_ACTIVITY_VS_RESULT", "Две независимые шкалы: активность и результат",
     "На экране раздельно: активность системы и производство результата. "
     "Высокая активность при нулевом результате показывается как ложная "
     "зелень, а не как успех. Telegram и экран считают показатели из одного "
     "источника, а не каждый по-своему.",
     ["backend/app/ext_api", "frontend/app"], ["OPS_EVENT_STREAM"], 90),

    ("CLIENT_READINESS_VIEW", "Готовность клиента по составляющим",
     "Для каждого из четырёх: тексты, изображения и сток, баннеры, объявления "
     "и черновики, экран объявлений, фид, ЕЦС, CRM, МОП, ответы МОПа, "
     "тренировка, аудит звонков. Неприменимое помечается и не блокирует. "
     "Показатель дня — сколько клиентов готовы полностью.",
     ["backend/app/ext_api", "frontend/app"], [], 94),

]


def _find(key):
    r = db.one("""SELECT id FROM ext_dev_jobs WHERE title LIKE :p
                  ORDER BY id DESC LIMIT 1""", p=key + "%")
    return dev.get(r["id"]) if r else None


def _acceptance(key, title, goal):
    return [
        "Проверено фактическое состояние по теме «%s»: что уже работает, что нет — "
        "выводом запросов и чтением кода, а не предположением" % title,
        "Первопричина каждой найденной поломки названа с файлом и строкой либо с "
        "выводом журнала",
        "Сделанное подтверждено доказательством нужного вида: сборка, тесты, обход "
        "экранов или управляемый сквозной прогон",
    ] + COMMON_ACCEPTANCE


# MASTER_WORKSTREAM_SCOPE_V1: system-wide MASTER tasks must not enter the
# scheduler with a broad ambiguous directory claim. These four tasks are
# infrastructure/control tasks with deterministic canonical writers.
MASTER_WORKSTREAM_SCOPES = {
    "BLAST_RADIUS_GUARD": {
        "workstream_id": "workstream_coordination",
        "paths": [
            "backend/app/ext_api/dev.py",
            "backend/app/ext_api/repo.py",
            "backend/run/workstream_scope_guard.py",
        ],
    },
    "OWNER_WATCHDOG_VERIFY": {
        "workstream_id": "control_plane_core",
        "paths": ["backend/app/ext_api/watchdog.py"],
    },
    "MASTER_COVERAGE_GUARD": {
        "workstream_id": "workstream_coordination",
        "paths": ["backend/app/ext_api/master.py"],
    },
    "CONCURRENCY_LADDER": {
        "workstream_id": "workstream_coordination",
        "paths": [
            "backend/app/ext_api/dev.py",
            "backend/app/ext_api/sched.py",
        ],
    },
}


def _master_scope_contract(key, locks, base_scope=None):
    """Return canonical scope + write locks for one MASTER task.

    Explicit overrides are intentionally tiny and deterministic. All other
    backlog items keep their existing path contract unchanged.
    """
    scope = dict(base_scope or {})
    override = MASTER_WORKSTREAM_SCOPES.get(str(key or ""))
    if override:
        paths = list(override["paths"])
        scope.pop("inspection_paths", None)
        scope.pop("read_only", None)
        scope.pop("scope_mode", None)
        scope["paths"] = paths
        scope["workstream_id"] = override["workstream_id"]
        scope["scope_guard"] = "MASTER_CANONICAL_WORKSTREAM_V1"
        return scope, paths
    paths = [l for l in (locks or []) if "/" in str(l)]
    scope["paths"] = paths
    return scope, list(locks or [])


def inspect():
    """Что уже есть на доске — до того, как что-то создавать."""
    out = []
    for key, title, goal, locks, deps, prio in BACKLOG:
        job = _find(key)
        state = "нет"
        if job:
            done = [c for c in job["acceptance_criteria"] if (c or {}).get("done")]
            state = "%s, приёмка %d/%d" % (job["status"], len(done),
                                           len(job["acceptance_criteria"]))
        out.append({"key": key, "title": title,
                    "dev_job_id": (job or {}).get("dev_job_id"), "state": state})
    return out


def plan():
    """Что будет создано и почему — без единой записи в базу."""
    existing = {k["key"]: k for k in inspect()}
    create, skip = [], []
    for key, title, goal, locks, deps, prio in BACKLOG:
        if existing[key]["dev_job_id"]:
            skip.append({"key": key, "why": "уже заведена: " +
                         existing[key]["dev_job_id"]})
            continue
        create.append({"key": key, "title": title, "priority": prio,
                       "locks": locks, "depends_on": deps})
    return {"create": create, "skip": skip, "total": len(BACKLOG)}


def _sync_existing_contract(job, key, locks, prio):
    """Keep persisted MASTER jobs/orders aligned with the canonical backlog.

    Existing acceptance/evidence is preserved. Canonical system-task ownership
    is reapplied on every sync so a planning/review pass cannot erase
    workstream_id and recreate UNSCOPED_DEV_JOB.
    """
    jid = int(job["id"])
    scope, canonical_locks = _master_scope_contract(key, locks, job.get("scope") or {})
    db.q("""UPDATE ext_dev_jobs
               SET scope_json=CAST(:scope AS jsonb), locks_json=CAST(:locks AS jsonb),
                   priority=:prio, updated_at=NOW()
             WHERE id=:jid""",
         scope=json.dumps(scope, ensure_ascii=False),
         locks=json.dumps(canonical_locks, ensure_ascii=False),
         prio=int(prio), jid=jid)
    db.q("""UPDATE ext_a2a_orders
               SET scope_json=CAST(:scope AS jsonb), priority=:prio, updated_at=NOW()
             WHERE dev_job_id=:jid
               AND status IN ('queued','running','review_requested','returned',
                              'blocked_human','blocked_infra')""",
         scope=json.dumps(scope, ensure_ascii=False), prio=int(prio), jid=jid)

    # MASTER_SCOPE_QUARANTINE_RECOVERY_V1: after deterministic canonical scope
    # replacement, clear only stale scope quarantine. Provider/human/dependency
    # waits are deliberately preserved.
    override = MASTER_WORKSTREAM_SCOPES.get(str(key or ""))
    if override:
        dev._requeue_scope_recovered_orders(
            jid, scope, canonical_locks, override["workstream_id"]
        )

    reason = str(job.get("blocked_reason") or job.get("wait_reason") or "")
    if job.get("status") == "blocked" and reason.startswith("WORKSTREAM_SCOPE_AMBIGUOUS:"):
        db.q("""UPDATE ext_dev_jobs
                  SET status='waiting', assigned_agent=NULL, blocked_reason=NULL,
                      wait_reason='WORKSTREAM_SCOPE_RECOVERED: canonical MASTER scope applied',
                      updated_at=NOW()
                WHERE id=:jid""", jid=jid)
    elif str(job.get("wait_reason") or "").startswith("UNSCOPED_DEV_JOB:"):
        db.q("""UPDATE ext_dev_jobs SET wait_reason=NULL, updated_at=NOW()
                WHERE id=:jid""", jid=jid)
    return dev.get(jid)


def apply(with_orders=True):
    """Создать недостающее, связать зависимости, поставить первые наряды."""
    made = {}
    result = []
    for key, title, goal, locks, deps, prio in BACKLOG:
        job = _find(key)
        created = False
        if not job:
            dep_ids = []
            for d in deps:
                dj = made.get(d) or _find(d)
                if dj:
                    dep_ids.append(dj["dev_job_id"])
            full_goal = (goal + "\n\nРаботать штатными средствами BORIS. Второй "
                         "оркестратор, второй исполнитель и вторая подсистема "
                         "запрещены: сначала разобраться в существующем и достроить. "
                         "Разрешений на обычные технические шаги не спрашивать. "
                         "Блокер владельца — только настоящий: секрет, оплата, внешняя "
                         "авторизация человеком, юридическое или бизнес-решение.")
            scope, canonical_locks = _master_scope_contract(key, locks)
            job = dev.create(LEAD_KEY, "master_" + key.lower(),
                             key + " — " + title, full_goal,
                             _acceptance(key, title, goal),
                             scope=scope,
                             priority=prio, depends_on=dep_ids, locks=canonical_locks,
                             owner=EXECUTOR, batch_id="master")
            created = True
        else:
            job = _sync_existing_contract(job, key, locks, prio)
        made[key] = job
        order_id = None
        if with_orders and created:
            o = a2a.create_order(
                LEAD_KEY, job["dev_job_id"], "PLANNING",
                "Разведка по задаче «%s». Сначала факты: что в базе, что в коде, что в "
                "журналах. Ничего не переписывай на этом шаге, правки только через "
                "proposePatch. Ответ подкрепляй выводом команд." % title,
                scope=job.get("scope") or {}, acceptance=job["acceptance_criteria"]
                if isinstance(job["acceptance_criteria"], list) else [],
                forbidden=FORBIDDEN, required_qa=["db_query", "repo_read"],
                required_evidence=EVIDENCE, assigned_to=EXECUTOR,
                max_iterations=6, priority=prio)
            order_id = o["order_id"]
        result.append({"key": key, "dev_job_id": job["dev_job_id"],
                       "created": created, "order_id": order_id})
    try:
        dev.schedule(200)
    except Exception:
        pass
    return result


def _counts():
    rows = db.rows("SELECT status, COUNT(*) AS n FROM ext_dev_jobs GROUP BY status")
    return {r["status"]: r["n"] for r in rows}


def dashboard():
    """Сводка в том виде, в каком её просил владелец."""
    caps = sched.limits()
    c = _counts()
    orders = db.rows("""SELECT status, COUNT(*) AS n FROM ext_a2a_orders
                        GROUP BY status""")
    o = {r["status"]: r["n"] for r in orders}
    running = c.get("running", 0)
    state = "READY"
    if c.get("blocked"):
        state = "DEGRADED"
    if not running and (c.get("queued") or c.get("waiting")):
        state = "DEGRADED"
    lines = ["AUTONOMOUS_DEV: " + state,
             "",
             "WORKERS:",
             "  active %d" % running,
             "  max_safe %d" % caps["MAX_EXECUTOR_WORKERS"],
             "  heavy slots %d" % caps["MAX_HEAVY_BUILD_SLOTS"],
             "",
             "JOBS:",
             "  DONE %d" % c.get("completed", 0),
             "  RUNNING %d" % running,
             "  QUEUED %d" % (c.get("queued", 0) + c.get("waiting", 0)),
             "  RETURNED %d" % o.get("returned", 0),
             "  BLOCKED %d" % (c.get("blocked", 0) + o.get("blocked_human", 0)),
             ""]
    lines.append("P0:")
    for key in ("P0_VK_POSTING", "P0_TG_POSTING", "P0_PROXY_INFRA",
                "DEV_CALLS_REPORT_FIX"):
        j = _find(key)
        lines.append("  %-22s %s" % (key.replace("P0_", ""),
                                     (j or {}).get("status", "не заведена")))
    lines.append("")
    lines.append("CLIENTS:")
    for key in ("CLIENT_ANTON", "CLIENT_VASILY", "CLIENT_PLANETA_MEBELI",
                "CLIENT_ANDREY_PENZA", "CLIENT_BYTOVKI_REPORTS"):
        j = _find(key)
        if not j:
            continue
        done = [x for x in j["acceptance_criteria"] if (x or {}).get("done")]
        lines.append("  %-24s %s, %d/%d" % (key.replace("CLIENT_", ""), j["status"],
                                            len(done), len(j["acceptance_criteria"])))
    lines.append("")
    lines.append("COST:")
    lines.append("  Claude Max: подписка")
    try:
        u = sched.usage(1)
        for m in u["by_model"][:4]:
            lines.append("  %s/%s: вызовов %s, токенов на выход %s"
                         % (m["provider"], m["model"], m["calls"], m["output_tokens"]))
        if u["runaway_candidates"]:
            lines.append("  внимание: задач у предела итераций — %d"
                         % len(u["runaway_candidates"]))
    except Exception:
        lines.append("  расход за сутки посчитать не удалось")
    return "\n".join(lines)


def main():
    import argparse
    ap = argparse.ArgumentParser(prog="master")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("inspect")
    sub.add_parser("coverage")
    sub.add_parser("plan")
    ap_apply = sub.add_parser("apply")
    ap_apply.add_argument("--no-orders", action="store_true")
    d = sub.add_parser("dashboard")
    d.add_argument("--send", action="store_true")
    a = ap.parse_args()
    if a.cmd == "coverage":
        print(coverage_text())
        return 0
    if a.cmd == "dashboard":
        text = dashboard()
        print(text)
        if a.send:
            from . import notify
            print("\n— отправлено" if notify.send(text) else "\n— Telegram не настроен")
        return 0
    if a.cmd == "apply":
        out = apply(not a.no_orders)
    elif a.cmd == "plan":
        out = plan()
    else:
        out = inspect()
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main() or 0)


def _coverage_job_row(job_id):
    return db.one("""SELECT id,status,blocked_reason,wait_reason,scope_json
                     FROM ext_dev_jobs WHERE id=:i""", i=int(job_id))


def _coverage_live_order(job_id, run_states):
    return db.one("""SELECT id,status,claimed_by FROM ext_a2a_orders
                     WHERE dev_job_id=:i AND status=ANY(:st)
                     ORDER BY id DESC LIMIT 1""",
                  i=int(job_id), st=list(run_states))


def _coverage_status_for_job(job, run_states):
    """Return the same coverage state used for ordinary backlog jobs."""
    if not job:
        return {"job": None, "order": None, "status": "NOT_CREATED",
                "worker": None}
    order = _coverage_live_order(job["id"], run_states)
    if job.get("status") == "completed":
        state = "DONE"
    elif not order:
        state = "NOT_CREATED"
    elif str(order.get("status") or "").startswith("blocked"):
        state = "BLOCKED"
    elif order.get("status") in ("running", "review_requested"):
        state = "RUNNING"
    else:
        state = "QUEUED"
    return {
        "job": "dev_%s" % job["id"],
        "order": (order or {}).get("id"),
        "status": state,
        "worker": (order or {}).get("claimed_by"),
    }


def _coverage_scope_workstream(job):
    raw = (job or {}).get("scope_json")
    try:
        scope = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except Exception:
        scope = {}
    value = str((scope or {}).get("workstream_id") or "").strip()
    return value or None


def _coverage_canonical_workstream(job):
    """Resolve a canonical-merge marker without hardcoded job ids.

    Exactly one registry workstream must contain the marker token as a full
    underscore segment. Ambiguity fails closed.
    """
    marker = "%s %s" % (
        str((job or {}).get("blocked_reason") or ""),
        str((job or {}).get("wait_reason") or ""),
    )
    match = re.search(
        r"merged_into_canonical_([a-z0-9_]+?)_scope\b",
        marker,
        re.I,
    )
    if not match:
        return None
    token = match.group(1).lower().strip("_")
    if not token:
        return None
    try:
        from app.services.workstream_scope import load_registry
        workstreams = (load_registry().get("workstreams") or {})
    except Exception:
        return None
    needle = "_" + token + "_"
    matches = [
        str(workstream_id)
        for workstream_id in workstreams
        if (
            str(workstream_id).lower() == token
            or needle in ("_" + str(workstream_id).lower() + "_")
        )
    ]
    return matches[0] if len(matches) == 1 else None


def _coverage_workstream(workstream_id, run_states):
    """Map a merged requirement to real live work owned by one workstream."""
    if not workstream_id:
        return None
    jobs = db.rows("""SELECT id,status,blocked_reason,wait_reason,scope_json,priority
                      FROM ext_dev_jobs
                      WHERE status NOT IN ('cancelled','cancelled_superseded')
                      ORDER BY priority DESC,id ASC""")
    ranked = []
    rank = {"RUNNING": 4, "QUEUED": 3, "BLOCKED": 2, "DONE": 1}
    for job in jobs:
        if _coverage_scope_workstream(job) != workstream_id:
            continue
        row = _coverage_status_for_job(job, run_states)
        if row["status"] == "NOT_CREATED":
            continue
        ranked.append((rank.get(row["status"], 0), row))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    row = dict(ranked[0][1])
    row["resolution"] = {
        "kind": "canonical_workstream",
        "workstream_id": workstream_id,
    }
    return row


def _coverage_replacement(job, run_states):
    """Follow explicit successor or canonical merge for an intentionally closed job."""
    marker = "%s %s" % (
        str((job or {}).get("blocked_reason") or ""),
        str((job or {}).get("wait_reason") or ""),
    )
    dedup_match = re.search(
        r"dedup_resolved_by_canonical_workstream[_: ]*(?:dev_)?(\d+)",
        marker,
        re.I,
    )
    if dedup_match:
        canonical_id = int(dedup_match.group(1))
        canonical = _coverage_job_row(canonical_id)
        direct = _coverage_status_for_job(canonical, run_states)
        if direct["status"] != "NOT_CREATED":
            direct["resolution"] = {
                "kind": "canonical_job",
                "from": "dev_%s" % job["id"],
                "to": "dev_%s" % canonical_id,
            }
            return direct
        workstream_id = _coverage_scope_workstream(canonical)
        via_workstream = _coverage_workstream(workstream_id, run_states)
        if via_workstream:
            via_workstream["resolution"]["canonical_job"] = "dev_%s" % canonical_id
            via_workstream["resolution"]["from"] = "dev_%s" % job["id"]
            return via_workstream

    successor_match = re.search(
        r"superseded(?:_by| by)(?:_dev)?[_: ]*(?:dev_)?(\d+)",
        marker,
        re.I,
    )
    if successor_match:
        successor_id = int(successor_match.group(1))
        successor = _coverage_job_row(successor_id)
        direct = _coverage_status_for_job(successor, run_states)
        if direct["status"] != "NOT_CREATED":
            direct["resolution"] = {
                "kind": "successor",
                "from": "dev_%s" % job["id"],
                "to": "dev_%s" % successor_id,
            }
            return direct
        workstream_id = _coverage_scope_workstream(successor)
        via_workstream = _coverage_workstream(workstream_id, run_states)
        if via_workstream:
            via_workstream["resolution"]["successor"] = "dev_%s" % successor_id
            via_workstream["resolution"]["from"] = "dev_%s" % job["id"]
            return via_workstream

    workstream_id = _coverage_canonical_workstream(job)
    via_workstream = _coverage_workstream(workstream_id, run_states)
    if via_workstream:
        via_workstream["resolution"]["from"] = "dev_%s" % job["id"]
        return via_workstream
    return None


def coverage():
    """Покрытие требований: у каждого пункта должна быть задача и наряд.

    Требование, живущее только в переписке, считается потерянным — владелец
    сформулировал это прямо, и он прав: 25 заведённых пунктов из 49 это не
    «в работе», это половина забытого задания.
    """
    from . import a2a
    run = list(a2a.OPEN_STATES) + ["queued", "returned", "blocked_human",
                                   "blocked_infra"]
    rows, counts = [], {"DONE": 0, "RUNNING": 0, "QUEUED": 0, "BLOCKED": 0,
                        "NOT_CREATED": 0}
    for it in BACKLOG:
        key = it[0]
        job = db.one("""SELECT id,status,blocked_reason,wait_reason,scope_json
                        FROM ext_dev_jobs WHERE title LIKE :p
                        ORDER BY id DESC LIMIT 1""", p=key + "%")
        if not job:
            row = {"req": key, "job": None, "order": None,
                   "status": "NOT_CREATED", "worker": None}
        else:
            row = _coverage_status_for_job(job, run)
            # A cancelled job may be intentionally absorbed by one canonical
            # writer. Count the requirement only when that replacement has
            # real live/completed coverage; otherwise fail closed.
            if row["status"] == "NOT_CREATED" and str(job.get("status") or "").startswith("cancelled"):
                replacement = _coverage_replacement(job, run)
                if replacement:
                    row = replacement
                    row["original_job"] = "dev_%s" % job["id"]
            row["req"] = key
        counts[row["status"]] += 1
        rows.append(row)
    total = len(BACKLOG)
    counts["total"] = total
    counts["mapped"] = total - counts["NOT_CREATED"]
    counts["coverage_percent"] = round(100.0 * counts["mapped"] / max(1, total), 1)
    counts["pass"] = counts["NOT_CREATED"] == 0
    return {"counts": counts, "rows": rows}


def ensure_coverage():
    """Восстановить недостающие задачи/наряды без дублей. Идемпотентно.

    Раньше функция вызывала apply(), хотя apply() возвращает list, а затем
    обращалась к нему как к dict. Ещё хуже: существующая waiting-задача без
    активного наряда так и оставалась без работы. Здесь отдельно восстанавливаем
    оба случая и всегда повторно проверяем наличие активного наряда прямо перед
    созданием.
    """
    cov = coverage()
    missing = [r["req"] for r in cov["rows"] if r["status"] == "NOT_CREATED"]
    if not missing:
        return {"created": [], "orders_created": [], "coverage": cov["counts"]}
    if len(missing) > 100:
        raise RuntimeError("MASTER_COVERAGE_HEAL_LIMIT: %d" % len(missing))

    created_jobs = []
    orders_created = []
    # First create truly missing jobs. apply() is idempotent and returns a list.
    if any(not r.get("job") for r in cov["rows"] if r["status"] == "NOT_CREATED"):
        res = apply(with_orders=True)
        created_jobs = [r for r in (res or []) if r.get("created")]

    # Then recover existing non-completed jobs that have no active order.
    active_states = list(a2a.OPEN_STATES) + ["blocked_human", "blocked_infra"]
    backlog_by_key = {it[0]: it for it in BACKLOG}
    for key in missing:
        job = _find(key)
        if not job or str(job.get("status") or "") == "completed":
            continue
        _job_num = int(str(job["dev_job_id"]).replace("dev_", ""))
        # BUSINESS_GOAL_MASTER_TERMINAL_FENCE_V1: master coverage is a generic
        # technical reconciler. A durable client business-result proof is owned
        # by clients.ensure_business_orders(); master must not resurrect the
        # same dev job merely because technical coverage still calls it missing.
        if _job_num in dev.business_goal_terminal_job_ids():
            continue
        live = db.one("""SELECT id FROM ext_a2a_orders WHERE dev_job_id=:i
                         AND status=ANY(:st) ORDER BY id DESC LIMIT 1""",
                      i=_job_num, st=active_states)
        if live:
            continue
        meta = backlog_by_key.get(key)
        if not meta:
            continue
        _, title, _goal, _locks, _deps, prio = meta
        o = a2a.create_order(
            LEAD_KEY, job["dev_job_id"], "PLANNING",
            "Разведка по задаче «%s». Сначала факты: что в базе, что в коде, что в "
            "журналах. Ничего не переписывай на этом шаге, правки только через "
            "proposePatch. Ответ подкрепляй выводом команд." % title,
            scope=job.get("scope") or {},
            acceptance=job["acceptance_criteria"] if isinstance(job.get("acceptance_criteria"), list) else [],
            forbidden=FORBIDDEN, required_qa=["db_query", "repo_read"],
            required_evidence=EVIDENCE, assigned_to=EXECUTOR,
            max_iterations=6, priority=prio)
        orders_created.append({"key": key, "order_id": o["order_id"]})
    try:
        dev.schedule(200)
    except Exception:
        pass
    return {"created": created_jobs, "orders_created": orders_created,
            "was_missing": missing, "coverage": coverage()["counts"]}


def coverage_text():
    c = coverage()["counts"]
    return "\n".join([
        "MASTER COVERAGE = %s" % ("PASS" if c["pass"] else "FAIL"),
        "REQUIREMENTS = %d" % c["total"],
        "MAPPED = %d/%d" % (c["mapped"], c["total"]),
        "DONE = %d · RUNNING = %d · QUEUED = %d · BLOCKED = %d"
        % (c["DONE"], c["RUNNING"], c["QUEUED"], c["BLOCKED"]),
        "NOT_CREATED = %d" % c["NOT_CREATED"],
    ])
