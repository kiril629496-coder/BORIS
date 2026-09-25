# ТЗ: BORIS Virtual PBX + собственный коллтрекинг на MCN.ru

Дата: 2026-09-04
Статус: рабочая архитектура и backlog оставшихся задач
Основной оператор связи: MCN Telecom / MCN.ru

## 1. Зафиксированная продуктовая модель

MCN используется только как оператор связи и поставщик транспортного слоя:
- SIP trunk / SIP-доступ;
- телефонные номера (DID);
- входящий PSTN-трафик;
- исходящие минуты;
- CDR/детализация оператора;
- при наличии — API/кабинет для заказа и управления номерами.

Не покупаем у MCN как обязательный слой:
- виртуальную АТС;
- маршрутизацию;
- очереди;
- IVR;
- контроль пропущенных;
- calltracking;
- запись/хранение разговоров;
- CRM-интеграции;
- рейтинг сотрудников;
- скрипты;
- оценку по чек-листам;
- AI-аналитику.

Собственный продукт:
MCN → SIP trunk/DID → Asterisk BORIS → BORIS Phone → CRM → запись → STT → AI → AI-РОП → собственный calltracking → аналитика/экономика.

## 2. Что уже есть в BORIS и не переписывается

В существующем telephony_core уже есть:
- canonical call entity и state machine;
- incoming/outgoing call model;
- provider_call_id и idempotency;
- telephony_events и защита от повторных событий;
- устройства и presence;
- call targets;
- маршруты;
- ring_all / round_robin / least_busy;
- расписания и таймауты;
- fallback;
- команды hold/resume/hangup/transfer на уровне бизнес-контракта;
- provider adapters;
- Telphin/UIS/Mango/Novofon контуры;
- история звонков;
- telephony_recordings;
- загрузка и локальная обработка записей;
- транскрибация;
- AI-анализ;
- telephony_call_quality;
- AI-РОП;
- coaching;
- пропущенные и callback SLA;
- CRM contact/deal/task linkage;
- автоответчик: политика, расписания, no-answer, afterhours;
- voice agent: сессии, turns, qualification, handoff;
- realtime transcript chunks;
- AI suggestions/copilot;
- minute packages и cost ledger;
- owner analytics/economics;
- BORIS Phone UI;
- telephony runtime service.

Не создавать второй telephony_core. Asterisk является media/SIP executor, BORIS остаётся единственным бизнес-источником истины.

## 3. Главные незакрытые блоки

P0:
1. Установить и harden Asterisk.
2. Подключить MCN SIP trunk.
3. Реальный inbound/outbound media.
4. ARI bridge между Asterisk и BORIS.
5. WebRTC WebPhone.
6. Tenant-safe extensions.
7. Реальная запись звонка.
8. End-to-end: call → record → STT → AI → CRM.
9. Собственный статический calltracking.
10. Собственный динамический calltracking для сайтов.
11. Импорт и расчёт себестоимости по тарифным таблицам MCN.
12. Fraud/security.
13. E2E/load/failover QA.

P1:
- supervisor listen/whisper/barge;
- predictive/queued outbound campaigns;
- резервный trunk;
- mobile native production;
- расширенная аналитика динамического calltracking.

## 4. Телефонное ядро

Использовать Asterisk 22 LTS.

Asterisk отвечает только за:
- PJSIP;
- SIP trunk;
- RTP;
- WebRTC;
- DTLS-SRTP;
- channels;
- bridges;
- DTMF;
- queue execution;
- hold;
- transfer;
- recording;
- external media.

BORIS отвечает за:
- tenant;
- маршрутизацию;
- CRM;
- права;
- business state;
- AI;
- отчёты;
- billing;
- attribution;
- настройки.

## 5. Новый Asterisk Gateway

Создать сервис:
- backend/app/services/asterisk_gateway.py
- backend/app/services/asterisk_ari.py
- backend/app/services/asterisk_events.py
- backend/app/services/asterisk_media.py
- backend/app/services/asterisk_recording.py
- backend/app/services/asterisk_config.py

Systemd:
- boris-asterisk-bridge.service

Требования:
- постоянный ARI websocket;
- reconnect с backoff;
- health;
- event dedupe;
- correlation;
- никаких trunk secrets в browser;
- fail closed при неизвестном tenant/DID.

## 6. Asterisk → BORIS event contract

Корреляция:
- BORIS call_id — canonical business ID;
- Asterisk uniqueid;
- Asterisk linkedid;
- MCN CDR/call ID, если предоставляется;
все IDs должны храниться раздельно.

События:
- channel_created;
- inbound_ringing;
- outbound_started;
- ringing;
- answered;
- bridge_entered;
- hold;
- unhold;
- transfer_requested;
- transferred;
- dtmf;
- recording_started;
- recording_finished;
- hangup;
- busy;
- rejected;
- failed.

Каждое событие превращается в idempotent telephony event.

Запрещено:
- регрессировать state;
- создавать вторую сделку при повторном webhook/event;
- повторно списывать минуты;
- повторно запускать платную STT.

## 7. MCN trunk model

Новая таблица telephony_trunks:
- id;
- account_id nullable для shared operator pool;
- provider='mcn';
- name;
- auth_mode: registration/ip;
- registrar;
- outbound_proxy;
- username;
- secret_enc;
- source_ips;
- codecs;
- dtmf_mode;
- max_channels;
- max_cps;
- allowed_cli;
- status;
- health;
- priority;
- created_at/updated_at.

Поддержать:
- primary trunk;
- backup trunk;
- outbound selection;
- per-tenant DID ownership;
- CLI whitelist.

## 8. Что запросить у MCN до production

Обязательная карта интеграции:
1. SIP registrar/proxy.
2. Registration или IP authentication.
3. Исходящие IP MCN.
4. Требуемые IP BORIS.
5. SIP ports.
6. RTP range.
7. Codecs: PCMA/PCMU/Opus/G729 при наличии.
8. DTMF mode.
9. TLS/SRTP support.
10. Количество одновременных каналов.
11. Цена/лимит дополнительных каналов.
12. CPS.
13. CLI policy.
14. Можно ли использовать каждый выданный DID как исходящий CLI.
15. Можно ли несколько DID на одном trunk.
16. Как передаётся called DID.
17. Как передаётся оригинальный Caller ID.
18. CDR API/выгрузка.
19. Уникальный carrier call ID.
20. API заказа/освобождения номеров.
21. Пул свободных номеров.
22. Резервный SIP endpoint.
23. Аварийная переадресация при недоступности BORIS.
24. Биллинг: шаг тарификации/округление/минимальная длительность.
25. Порядок обновления тарифных таблиц.
26. 8-800 и особенности входящего тарифа.
27. Ограничения на массовые исходящие.
28. Антифрод/блокировки.

## 9. DID inventory

Таблица telephony_dids:
- id;
- operator='mcn';
- number_e164;
- region;
- number_type;
- account_id;
- trunk_id;
- purpose:
  - main;
  - calltracking_static;
  - calltracking_dynamic;
  - campaign;
  - reserve;
- inbound_enabled;
- outbound_cli_enabled;
- monthly_cost;
- connection_cost;
- status;
- activated_at;
- released_at.

Номер не может принадлежать двум активным tenant одновременно.

## 10. Extensions

Таблица telephony_extensions:
- id;
- account_id;
- user_id;
- extension;
- display_name;
- enabled;
- voicemail_enabled;
- recording_policy;
- outbound_policy;
- created_at/updated_at.

Credentials:
- отдельный случайный secret;
- хранить encrypted;
- не использовать номер телефона как пароль;
- revoke/rotate.

Extension uniqueness:
UNIQUE(account_id, extension), а не global.

## 11. WebPhone

Существующий BORIS Phone UI подключить к Asterisk через WebRTC.

Функции:
- login устройства;
- входящий звонок;
- ringtone;
- answer;
- reject;
- outgoing call;
- mute;
- unmute;
- hold;
- resume;
- DTMF;
- blind transfer;
- attended transfer;
- hangup;
- network reconnect;
- microphone/speaker selection;
- timer;
- live call status.

Транспорт:
- WSS;
- DTLS-SRTP;
- short-lived device-scoped auth/session;
- trunk credentials никогда не уходят на frontend.

## 12. Inbound flow

MCN DID
→ Asterisk PJSIP
→ определить DID
→ telephony_dids
→ account_id
→ BORIS call.create
→ BORIS resolve_route
→ prepare_call_targets
→ Asterisk звонит устройствам/extension
→ ответ
→ bridge
→ BORIS state active.

Если не ответили:
→ BORIS fallback policy
→ AI/IVR / callback / другая группа / voicemail.

## 13. Outbound flow

Менеджер:
BORIS Phone → call intent → entitlement/limit check → approved CLI → Asterisk originate → MCN trunk → PSTN.

Нельзя:
- принимать arbitrary caller ID с frontend;
- разрешать международные направления по умолчанию;
- делать двойной originate при timeout.

Idempotency key обязателен.

## 14. Маршрутизация

Использовать существующие BORIS routes.

Добавить execution adapter:
- destination user;
- extension;
- device;
- group;
- external phone;
- AI agent.

Стратегии:
- ring_all;
- round_robin;
- least_busy;
- sequential;
- skill/team.

Fallback chain:
primary group → second group → AI → callback.

## 15. Собственный статический calltracking

Назначение:
один DID постоянно связан с источником.

Примеры:
- Avito account;
- отдельная кампания;
- Яндекс Директ;
- VK;
- Telegram;
- сайт;
- наружка.

Таблица calltracking_static_bindings:
- account_id;
- did_id;
- source;
- source_ref;
- campaign_id;
- ad_id;
- location;
- active_from/to.

Inbound call:
DID → binding → source/source_ref → CRM → analytics.

Для Avito на старте использовать статический calltracking, так как сайт-скрипт внутри Avito недоступен.

## 16. Собственный динамический calltracking

MCN предоставляет только пул реальных DID. Вся динамика — BORIS.

Компоненты:
1. JS snippet на сайте клиента.
2. First-party visitor_id.
3. session_id.
4. UTM/referrer/click IDs.
5. pool allocator.
6. number substitution.
7. lease TTL.
8. attribution.
9. call reconciliation.

Таблицы:
calltracking_pools:
- id/account_id/region/default_did;
- lease_ttl_sec;
- cooloff_sec;
- status.

calltracking_pool_numbers:
- pool_id;
- did_id;
- status;
- current_session_id;
- leased_until;
- cooldown_until.

calltracking_sessions:
- id;
- account_id;
- visitor_id;
- session_id;
- first_seen;
- last_seen;
- landing_url;
- referrer;
- utm_source;
- utm_medium;
- utm_campaign;
- utm_content;
- utm_term;
- yclid;
- gclid;
- vk_click_id;
- assigned_did_id;
- expires_at.

calltracking_touchpoints:
- session_id;
- occurred_at;
- channel;
- campaign;
- referrer;
- landing_url;
- metadata_json.

calltracking_attributions:
- call_id;
- did_id;
- session_id;
- attribution_mode: dynamic/static/fallback/direct;
- confidence;
- first_touch_json;
- last_touch_json;
- created_at.

## 17. Dynamic number allocation

Алгоритм:
1. Visitor приходит на сайт.
2. Создать/reuse visitor_id.
3. Создать session.
4. Сохранить UTM/referrer/click ID.
5. Выдать свободный MCN DID из пула.
6. Заменить телефоны на странице.
7. Продлевать lease по heartbeat.
8. После session end держать cool-off.
9. Если в этот период приходит звонок на DID — привязать к session.
10. После cool-off вернуть DID в пул.

Sticky behavior:
- один посетитель в активном окне получает тот же номер;
- несколько вкладок не должны съедать несколько DID.

Pool exhausted:
- показать default static DID;
- attribution_mode='fallback';
- confidence снижена;
- событие pool_exhausted в monitoring;
- не выдумывать источник.

## 18. Calltracking browser API

Публичный минимальный API:
POST /api/calltracking/v1/session
POST /api/calltracking/v1/heartbeat
POST /api/calltracking/v1/release
GET /api/calltracking/v1/config

Требования:
- signed site token;
- allowed domains;
- CORS whitelist;
- rate limits;
- no private BORIS credentials;
- IP/device fingerprint не использовать как единственный visitor key.

JS:
- async;
- <10–15 KB gzip целевой размер;
- не блокирует рендеринг;
- MutationObserver для динамических телефонов;
- поддержка tel: links;
- fallback при недоступности API.

## 19. Attribution hierarchy

Приоритет:
1. Dynamic active/cooloff lease по DID.
2. Static DID binding.
3. CRM previous known source, только как secondary evidence.
4. Direct/unknown.

Нельзя автоматически переносить старый рекламный источник на новый независимый звонок без evidence.

Хранить:
- first touch;
- last touch;
- call source;
- source confidence.

## 20. CRM integration

При неизвестном номере:
- contact create/upsert;
- deal create согласно account policy;
- source from calltracking;
- source_ref;
- DID;
- call_id.

При известном:
- link open deal;
- при отсутствии — policy create.

После звонка:
- recording;
- transcript;
- summary;
- qualification;
- next action;
- manager;
- AI score;
- callback due;
- attribution.

## 21. Recording

Asterisk MixMonitor:
- всегда internal call_id-based filename;
- запрещено имя файла из raw Caller ID;
- начало/конец синхронизировать с call lifecycle.

Local spool:
- временный;
- checksum;
- upload object storage;
- verify;
- удалить local.

Target local spool cap:
- 3–5 GB;
- max age 24h;
- emergency purge только после verified upload.

## 22. STT + AI pipeline

Существующий recording pipeline использовать без второго платного вызова.

Правила:
- exact-once paid STT per recording checksum;
- retry limit;
- cache by checksum;
- provider recording id uniqueness;
- transcript versioning при повторной обработке вручную.

Результат:
- transcript;
- speakers;
- summary;
- qualification;
- agreement;
- next action;
- objections;
- AI-РОП scores.

## 23. Live AI/copilot

Asterisk external media → BORIS streaming STT.

Во время звонка:
- transcript chunks;
- AI suggestions;
- objection detection;
- missing question detection;
- next-step reminder.

AI не должен сам озвучиваться клиенту в human call.

## 24. AI автоответчик

Существующую business logic подключить к real media.

Modes:
- off;
- afterhours;
- no_answer;
- always.

Flow:
inbound → no human → request takeover → Asterisk bridge to AI media → STT/AI/TTS → qualification → CRM → optional human handoff.

Обязательные guards:
- не забирать уже принятый человеком звонок;
- unknown fact → handoff;
- prices только confirmed;
- commitments запрещены по умолчанию;
- max duration;
- budget/minute guard.

## 25. Supervisor monitoring

Сделать собственно вместо платного monitoring:
- live presence;
- active calls;
- duration;
- manager;
- source;
- CRM deal.

Actions:
- listen;
- whisper;
- barge.

RBAC:
только owner/ROP с разрешением.

Все подключения логировать в audit.

## 26. Контроль пропущенных

Использовать уже существующий callback SLA.

Flow:
missed → CRM task → due_at → notify → callback → link callback call → mark completed → calculate within_sla.

Не покупать отдельный модуль MCN.

## 27. Тарифный движок MCN

По загруженным файлам MCN уже есть отдельные таблицы:
- мобильные РФ;
- местная фиксированная связь;
- Россия МГ;
- международная связь.

Создать importer:
mcn_tariff_import.py.

Таблицы:
telephony_tariff_versions:
- id;
- operator;
- name;
- effective_from;
- vat_mode;
- source_sha256;
- imported_at;
- status.

telephony_tariff_rates:
- tariff_version_id;
- direction_type;
- label;
- prefix;
- range_from;
- range_to;
- price;
- currency;
- metadata_json.

Resolver:
- E.164 normalize;
- longest prefix;
- при мобильных диапазонах учитывать prefix + number range;
- effective date;
- no overlapping ambiguous rate without validation.

В загруженных MCN файлах цены обозначены как без НДС — engine должен хранить carrier_net_cost отдельно от tax и retail cost.

Точный billing increment/rounding не предполагать: запросить у MCN и зафиксировать в tariff version.

## 28. Retail billing BORIS

Для клиента отдельно считать:
1. BORIS Phone subscription.
2. Номер/абонплата.
3. Minutes usage.
4. Опциональный пакет.
5. AI usage, если тариф не bundled.

Cost:
- MCN carrier cost;
- infra;
- STT;
- TTS;
- AI.

Revenue:
- retail tariff.

Margin:
revenue - carrier - infra - AI.

Нужны:
- margin by client;
- margin by call;
- margin by direction;
- margin by month.

## 29. CDR reconciliation

Ежедневно:
BORIS CDR vs MCN CDR.

Сверять:
- direction;
- from/to;
- started/answered/ended;
- duration;
- carrier call id;
- billed duration;
- carrier amount.

Statuses:
- matched;
- amount_mismatch;
- duration_mismatch;
- missing_in_boris;
- missing_in_mcn;
- duplicate.

Никогда не корректировать автоматически финансовый ledger без audit trail.

## 30. Security / anti-fraud

P0:
- Asterisk ARI/AMI private only;
- firewall;
- MCN IP ACL при IP auth;
- fail2ban;
- random SIP secrets;
- rate limit;
- international off default;
- premium off default;
- max daily spend;
- max concurrent calls;
- max CPS;
- allowed CLI;
- allowed destinations;
- anomaly detection;
- immediate suspend switch per tenant;
- audit every admin action.

## 31. Network

Asterisk:
- SIP only on required interface/ACL;
- WSS 443 via controlled reverse proxy or dedicated endpoint;
- RTP limited port range;
- ARI localhost/private;
- DB never public.

Monitoring:
- packet loss;
- jitter;
- RTT;
- RTP no-audio;
- one-way audio;
- SIP registration;
- trunk health.

## 32. Self-healing

BORIS должен автоматически:
- reconnect ARI;
- re-register trunk;
- detect stale call;
- reconcile terminated channel;
- retry recording upload;
- retry STT;
- restore device presence;
- detect calltracking pool exhaustion;
- detect missing DID mapping;
- detect CDR mismatch.

Owner escalation только если auto-recovery failed.

## 33. Observability

Metrics:
- active calls;
- calls/min;
- answer rate;
- ASR;
- ACD;
- missed;
- callback SLA;
- SIP 4xx/5xx/6xx;
- trunk registration;
- RTP quality;
- recording pipeline lag;
- STT lag;
- calltracking pool usage;
- attribution rate;
- unattributed calls;
- carrier cost;
- retail revenue;
- margin.

Logs structured with:
account_id, call_id, linkedid, trunk_id, did_id.

## 34. Server resource policy

Текущий BORIS server имеет достаточный RAM/CPU для первого узла, но локальный диск уже сильно заполнен.

Правило:
- Asterisk software/config/logs локально;
- recordings archive НЕ хранить локально;
- local audio spool ограничить 3–5 GB;
- object storage обязателен до масштабирования.

Начальный release limit:
- 50 simultaneous calls per Asterisk node.
После load test — поднять.

## 35. Production QA

Обязательные реальные тесты:

Inbound:
реальный телефон → MCN DID → SIP → Asterisk → WebPhone → answer → 2-way audio → recording → STT → AI → CRM.

Outbound:
WebPhone → Asterisk → MCN → реальный мобильный → answer/end → recording → CRM.

Missed:
manager no answer → callback task.

AI:
manager no answer → AI takeover → voice dialog → confirmed phone/topic → CRM.

Transfer:
manager A → manager B.

Calltracking static:
call on static DID → correct source.

Calltracking dynamic:
visitor UTM → substituted DID → inbound call → exact session → CRM attribution.

Pool exhaustion:
fallback DID, no false attribution.

CDR:
BORIS vs MCN reconciliation.

Tenant isolation:
client A cannot access client B calls/DIDs/recordings/trunk.

Restart:
backend restart does not break active Asterisk call; state reconciles afterward.

Load:
- 50 simultaneous;
- then 100 test.

## 36. Definition of Done

Release PASS только если:
- MCN trunk connected;
- real inbound proven;
- real outbound proven;
- real 2-way media proven;
- WebPhone proven;
- real recording proven;
- STT proven;
- AI analysis proven;
- CRM create/link proven;
- static calltracking proven;
- dynamic calltracking proven;
- attribution proven;
- CDR reconciliation proven;
- anti-fraud limits enabled;
- object storage proven;
- tenant isolation tests PASS;
- recovery tests PASS;
- 50 concurrent load PASS.

## 37. Очерёдность разработки

Stage 1 — MCN foundation
- получить SIP technical sheet/credentials;
- Asterisk install/hardening;
- trunk;
- DID;
- inbound/outbound.

Stage 2 — BORIS media
- ARI bridge;
- WebRTC;
- call control;
- real state events.

Stage 3 — post-call
- recording;
- object storage;
- STT/AI/CRM;
- CDR.

Stage 4 — own calltracking
- static DID mapping;
- dynamic pools;
- website JS;
- attribution;
- analytics.

Stage 5 — AI voice
- external media;
- realtime STT/TTS;
- no-answer agent;
- human handoff.

Stage 6 — production
- security;
- monitoring;
- self-healing;
- load;
- tenant isolation;
- failover.

## 38. Внешние блокеры, которые не надо имитировать

До выдачи MCN production SIP credentials нельзя честно поставить PASS:
- real SIP trunk;
- real RTP;
- inbound/outbound PSTN;
- CLI;
- channel/CPS limits;
- carrier CDR.

При отсутствии данных система должна показывать WAITING_MCN, а не fake success.

## 39. Юридико-операционный чек перед подписанием

По текущему шаблону агентской модели:
- Агент может устанавливать розничные тарифы не ниже условной стоимости;
- Агент производит тарификацию на данных Оператора;
- маршрутизацию вызовов при подключении организует Агент;
- Оператор резервирует номера;
- вознаграждение Агента = начисленный доход минус условная стоимость.

Перед подписанием запросить у MCN актуальный шаблон и актуальные лицензии/реквизиты, поскольку присланный шаблон содержит сроки части лицензий до 02.09.2026.

## 40. Конечная архитектурная формула

MCN = оператор и PSTN.
Asterisk = собственная ВАТС BORIS.
BORIS Telephony Core = управление.
BORIS CRM = сделка и клиент.
BORIS Calltracking = источник и рекламная атрибуция.
BORIS AI = транскрибация, автоответчик, copilot.
BORIS AI-РОП = контроль продаж.
BORIS Billing = розница, себестоимость и маржа.
