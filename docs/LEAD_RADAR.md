# BORIS Lead Radar

## Назначение

Lead Radar — мультиклиентский модуль BORIS для постоянного поиска реального спроса на товары и услуги.

Модуль не ограничен только разработкой. У каждого клиента есть собственный каталог предложений:
- товары;
- услуги;
- смешанный каталог товаров и услуг.

Текущий внутренний профиль BORIS для разработки сохраняет 35 IT-направлений. Для других клиентов количество и состав предложений задаются их собственным каталогом.

## Главное правило мультиклиентности

Каждый клиент работает только в своём owner scope.

Для каждого owner отдельно хранятся:
- профиль бизнеса;
- каталог товаров/услуг;
- ключевые слова и минус-слова;
- цены;
- география;
- минимальный score;
- настройки источников;
- состояние источников и backoff;
- лиды и fingerprint-дедупликация;
- CRM-сделки;
- outreach/follow-up.

Клиент не наследует каталог, лиды, source state или CRM другого клиента.

Без активного профиля или без хотя бы одного активного предложения Lead Radar работает fail-closed и не начинает поиск от имени клиента.

## 20 целевых групп источников

1. freelance
2. tenders
3. jobs
4. startups
5. telegram
6. linkedin
7. communities
8. apps
9. maps
10. company_sites
11. crm_companies
12. seo_agencies
13. design_studios
14. developers
15. new_companies
16. franchises
17. news
18. social
19. reviews
20. cold_b2b

Один физический connector может покрывать одну группу, а в одной группе может быть несколько connector-ов.

Business PASS для активного клиента требует покрытия 20/20 групп. Недостающие группы видны в coverage/Brain и не маскируются.

## Поток обработки

SOURCE
→ normalize
→ classify against tenant catalog
→ buyer/signal detection
→ quality score
→ reject noise/non-buyer
→ owner-scoped dedupe
→ BORIS CRM deal/activity
→ personalized reply draft
→ send only if explicit source policy permits
→ follow-up only if policy permits
→ health/self-heal
→ owner escalation only when safe self-heal exhausted.

## Сигналы

Lead Radar поддерживает:
- готовое ТЗ / явный запрос;
- hiring как B2B-сигнал;
- деньги / funding;
- запуск нового продукта/проекта;
- проблема или ручной процесс;
- business gap / аудит;
- партнёрский спрос.

Вакансия никогда не превращается в автоматический отклик соискателя.

## Товары и услуги

Для каждого offering задаются:
- offering_code;
- external_key;
- kind = product или service;
- name;
- keywords;
- negative_keywords;
- price_min_rub;
- price_max_rub;
- currency;
- enabled;
- metadata.

Русская морфология учитывается при сопоставлении товарных фраз. Короткие технические токены вроде API/1C сопоставляются как отдельные токены, а не как случайная подстрока.

## Источники клиента

Generic-клиент по умолчанию не наследует глобальные IT-источники BORIS.

Источник может быть задан через owner-scoped profile settings, включая разрешённые RSS/public-web конфигурации.

Фоновый runner сам читает все enabled Lead Radar profiles и запускает каждого owner отдельно. Ошибка одного клиента не останавливает остальных.

Legacy LEAD_RADAR_OWNER_USER_ID используется только как fallback для фонового runner, если в БД ещё нет профилей. Он не является API ACL для мультиклиентского продукта.

## API

Основные owner-authenticated endpoints:

- GET /api/lead-radar/profile
- PUT /api/lead-radar/profile
- POST /api/lead-radar/ingest
- POST /api/lead-radar/scan
- GET /api/lead-radar/leads
- GET /api/lead-radar/sources
- GET /api/lead-radar/stats
- GET /api/lead-radar/coverage
- GET /api/lead-radar/health

API scope определяется authenticated owner user id.

## CRM

Lead Radar использует только существующую BORIS CRM.

Один бизнес-запрос внутри одного owner scope создаёт одну CRM-сделку. Повторный или найденный в другом источнике сигнал добавляется как activity/evidence, а не создаёт вторую сделку.

## Outreach

По умолчанию система fail-closed.

AUTO разрешён только при явной SourcePolicy, которая разрешает автоматизированный ответ.

DRAFT/CRM_ONLY никогда не повышаются до AUTO скрытым follow-up.

Неидемпотентный канал не получает слепой повтор после неоднозначной ошибки отправки.

Внутреннего дневного лимита BORIS на количество найденных лидов нет. Реальные ограничения и квоты внешних площадок/API соблюдаются.

## Self-heal

Источник хранит:
- last_scan_at;
- last_success_at;
- last_failure_at;
- last_error;
- consecutive_failures;
- next_scan_at;
- recovery_strategy;
- blocked_external.

Сетевые timeout/429/временная недоступность идут в provider backoff и не превращают владельца в оператора.

Для Freelancer дополнительно:
- timeout запроса имеет безопасный повтор;
- пагинация прекращается, когда вся очередная страница по time_updated уже старше freshness boundary;
- это не позволяет широкому поиску обходить всю историческую выдачу и создавать лишние timeout.

Схема:
DETECT → DIAGNOSE → SELF-HEAL → VERIFY → RETRY → ESCALATE только если восстановление невозможно.

## Scheduler

Production timer:
- boris-lead-radar.timer;
- enabled;
- Persistent=true;
- периодический запуск каждую минуту;
- один системный timer обслуживает все enabled tenant profiles;
- per-owner single-flight не допускает одновременные scan одного клиента из timer и API.

## System Brain

Каноническое требование:
prospecting.lead_radar.autonomous_dev_demand

Текущая модель требования — multi-tenant products/services.

Brain проверяет:
- owner-scoped catalogs;
- owner-scoped sources;
- наличие каталога у каждого enabled tenant;
- 20/20 source groups;
- runtime health;
- CRM linkage;
- dedupe;
- self-heal;
- отсутствие необходимости владельца быть оператором.

NO EVIDENCE = NO PASS.

Финальный PASS требует runtime/business evidence, а не только успешную компиляцию.

## Production acceptance

Минимальный набор:
1. Python compile/tests PASS.
2. Owner isolation PASS.
3. Generic product/service profile PASS.
4. Multi-tenant runner PASS.
5. Live authenticated API PASS.
6. 20/20 source coverage для активного tenant.
7. Real source cycle без ошибок.
8. CRM deal/activity readback.
9. Health PASS.
10. System Brain reconcile PASS.
11. L4/L5/L6 evidence.
12. QA data cleanup = 0.
