
## ACCEPTED ordinary V3 — 2026-10-09T06:49:49.446110+00:00
Релиз `c3b55ea2be818ac802689ec9d4b1cc6ecd08507a1a6877d17c752108c406cee5` accepted `2026-10-09T06:37:16.847646+00:00`; canonical331passed, отдельныйPG110passed, runtimeAPI1/API2/worker совпали. Текущий итог: `/root/BORIS/outputs/clients510-execution-20261008/resume-20261009-domain-v3-f57/HANDOFF_20261009.md`. Старые нижние разделы NOT_INSTALLED/restore31 исторические и отменены этим receipt. Source foreignWIP сохранён, повторное восстановление31 не требуется. Protectedостаток и full_TZ/72h остаются открыты.

# БОРИС 510 — итог исполнения и продолжение, v5

Статус: **HANDOFF_WITH_EXPLICIT_OPEN_ACCEPTANCE_ITEMS**. Root завершил записи production/source; `root_work_active=false` выставляется в сопутствующем машинном checkpoint после проверки фактического состояния. Полное ТЗ и §7 пока не приняты. Это не запрос повторно разрешить уже порученные обычные исправления.

## Текущий принятый результат

Backend `6804e955f2e230f09d38c5ee8fbf0c5407952fa1224e69f2d259ec1e66ba617a`, принят 09.10.2026 00:23:49.509574 UTC. Повторная проверка 02:08 UTC: 13/13 пилотных файлов совпали, пять штатных проверок поколений процессов пройдены; CRM/deploy не имеют build environment, их поколение подтверждено отдельно. Current неизменен при чтении 02:33:45 UTC.

Ранее приняты ordinary4 `ac087198…` (299 тестов), Garik app `f2c61fd5…` и штатная SQL071 (42 canonical и 28 PG/Python), UI /admin/clients (33 сценария), pilot13 (160 canonical +163 preflight). Для семи клиентов сохранены 12 оплаченных полей и legacy пилот Марии. Durable notices и точная конверсия в оплату установлены; новый pilot_start требует ещё защищённый auth adapter, выбранная реактивация — двух её адаптеров. Наличие UI не доказывает реальную выдачу нового пилота.

Общая сертификация допускала 181 прежнюю несвязанную ошибку: PASS_WITH_PREEXISTING_FAILURES не является полной приёмкой системы. Исторический timeout обёртки pilot job не отменяет accepted receipt; принятые релизы и SQL071 не повторять.

## B510-R00 — обычный V3 подготовлен, установка отложена

Scope: 13 runtime-файлов; MSG16, обычные наблюдения CRM12, Feed14 predicates/ordinary producer subset, два правила подтверждённых бюджетных настроек. Это не все полностью подключённые защищённые ветви.

- Frozen `outputs/boris510-controlplane-e2e-v3-20261009/integrated-manifest.json` SHA `7149857302571c7e79ac47240e8a689559e324ab02685cc7b9086ea8b3aea5ec`.
- Canonical request `backend/run/scoped-release-requests/clients510-domain-rules13-v3-testcompat1-20261009.json` SHA `b4b631dfcf96118b85ceb9733063a93faf62c81e408beed3b7bd54100ef108ba`.
- Единственная testcompat поправка `test_clients510_message_rule_producers.py` SHA `7052b9ce7c6ec51007ef7065130138569e66c330ada38645e8fc992535bf4754`; исходные 13 runtime не менялись.
- Attempt01 `job_710b5bde9dc3`: ошибка collection до активации; точное окружение импорта воспроизведено, исправление — 65 PASS.
- Attempt02 `job_bfbdb1507953` завершился 01:41:28.845532 UTC, attempt03 `job_90a0d02f755c` — 01:58:48.875548 UTC. Оба terminal deferred/no_safe_capacity_window, exit75, по10 минут, до переключения runtime.
- Дополнительное чтение 02:08:29 UTC: 259,7% при штатном допуске менее90%. Порог не ослаблялся. Нет своей активной release job.
- Тесты разных этапов:101PG до финальной дельты,330 staged,13 finalPGdelta,3 report,65testcompat. Полный финальный canonicalPG110 **не выполнялся**.

**Исходники уже возвращены.** В 02:23:57 UTC root выполнил exact pre-stage restore31; итоговый readback31PASS, 23 изменённые записи и восемь совпадавших оставлены, 14 собственных новых файлов сохранены в карантине. Runtime не менялся. Журнал:
`outputs/clients510-execution-20261008/domain-source-housekeeping/operations/20261009T022356-fd32f30bd52f4b3ba91375e8a8df94b6/journal.json`,
SHA `f227dc3da6c7c4b19ec25430f03fb7f78a7425a6798f7f1fa18d4c0418c800b3`.

Foreign WIP сохранён точно: Avito `ed57a77d…` с PriceType в _build_xml; Campaign `fda57585…` с **_ff_ai_marketer_projection**; billing `725a9b33…`; banners `42b07aa7…`. Подготовленные совместимые Avito25e379…/Campaignfaf1ed… предназначены только для после actual V3 acceptance и сейчас НЕ применены. Старый billingV1 не восстанавливать.

Следующий разрешённый выпуск: прочитать действующие AGENTS/CHATGPT_OPERATING_RULES/ownership и фактические current/jobs/31SHA. После появления safe window и при неизменном baseline выполнить reviewed `stage_reviewed` из `domain-source-housekeeping/source_housekeeping31.py`, SHA `4fd1dd9b333f7c4f7c771bedd9a99c340d63daaeac5561ee46e5d01b27dfbaaa`, plan `bd9be2582a83cdf435cb4c4516327cac00c16558dd44c1a258769460e50254ee`. Пока этот режим пройден **только чтением**. Staging должен предшествовать созданию нового attempt-directory, поскольку frozen helper фиксирует историю02/03. Новая попытка требует собственного claim, проверки и соответствующего cleanup plan. Изменившийся baseline требует переноса минимальных правок. Не выпускать восстановленное дерево как будто V3 остаётся staged; не повторять terminal или unknown job. Использовать существующий `boris-deploy.service` и `/usr/local/sbin/boris-backend-scoped-deploy`; после принятия 13SHA/5runtime, новый естественный цикл, матрица и собственные72h.

## B510-R01 — готовые защищённые пакеты, NOT_INSTALLED

1. `protected-cpx`: CPX333 candidate6c6219…/11+11 PASS. **Реальный AUTO_REJECTED_PERMANENT**, forbidden `backend/cpx_cap_reconciler.py`, rollback подтверждён.
2. `outputs/clients510-content-feed-combined-20261009`: jobs `a490e1dcdf0cc2c8b6774f180d894e03bef2badabcd26f49184f753c6e1af1c7`, helperb17ff6…, 66PASS (58content+8реальных границ). Прежние раздельные jobs не копировать последовательно друг поверх друга.
3. `supervisor-provider-canonical-v7/rebased-frozen-domain-v3`: manifest `ca06c502666a3311c25a4eef6a68a5c12303cee0103eeea0e3f943763208e7f8`, inboxe062b940…, supervisorac3656…, 38PASS02:02UTC и69остальных функций отчёта сохранены. Требуется будущий ordinary inbox479708…. Supervisor immutable; в auto-review не подавался.
4. Защищённые auth и два adapter реактивации: продуктовый пилот, SOURCE_REVIEWED_OPERATOR_REQUIRED.
5. `outputs/boris510-protected-rule-producers-20261009`: manifest `5b56316ec2e8c40ba06439880e97bc5958c12448ac24defea419cc2d4246f8c0`, ZIP `53fb2586ccfa97df8ad7fe94eae5c7c2942397f92b7a5a0143d29b1adb92300c`,7Python+SQL8. CPX/Brain/autonomy export существующих фактов; точный receipt/account/request/item/action/old/intended, UUID/version до действия, provider readback без повторного business action.
6. SQL8 `public.boris_cpx_receipt_atomic_capacity_guard()`: candidate `827d9d54089c192d832ef27dbe98153c522c942c47dde82dde8f2e80ad841d67`,7PG/source-parity PASS. Только подготовлен экспорт уже вычисленного счётчика под прежней блокировкой; SQL НЕ применён, не queued, номер072 не занят. Whitelist не включает daily_budget_pacing_first_bid; это не общий часовой лимит.
7. Brain после совместимой установки: отдельная exact10UUID registration через существующий update_requirement, all-before CAS, v1→v2; rollback новойv3 без переписи истории. Plan `129aad2144ee8571810edbfa9e0c4d1c203a3f94bbdd460ad1c76a724510c6e6`. Делегация только evidence для **boris_crm_tasks**, не legacycrm_tasks и не расширение business writer.

Protected overlay требует ещё не установленный ordinary control_rule_runtime `c7944d7a20d91e4f95c1d4f76fa94ec7347d9dc327bb68f2ae212a597b4bdfcb`. Тесты17Brain/12CPX PostgreSQL относятся к своим прежнимSHA; final65local и поздние чистыеdelta имеют отдельные peer. Не называть их новым PG-прогоном на финальных версиях.

20CPXhooks допускают17условных локальных оценок при нужныхфактах; это не17whole-rulePASS. Ранняя marketing22карта15/5 superseded только MKT001/MKT011 финальными atomic/lowviews deltas; MKT001 требует SQL8/whitelist, MKT011 точныйselectorcontext. Оставшиеся три определения открыты.

Immutable/owner/money границы не обходить, humanactor не подменять. Пользователь уже поручил полное выполнение обычной работы; готовый manifest не создаёт отсутствующий реальный допуск защищённого writer.

## B510-R02…R06 — внешние входы и итоговая приёмка

- QA537:100/100semantic,73/100полных галерей,62missing настоящих фото для27карточек. Все62донорыnegative, поиск500не дал подтверждённыхальтернатив. Сохранены73галереи/154подходящихфото/27баннеров. Job10859failed done73/total100/scheduledNULL; не возобновлять без установленного jobs, подходящихassets и подтверждённого конечногоQAбюджета. РабочаяTimCampaign513100/100.
- Альбертbudget0; поздний publishwait требует подтверждения тарифаAvito. Тимофейbudget/KPI/CPLunknown. ГарикCRMtask8593/manager467 ждёт подтверждённой цены. Евгенийexpiredwatch; самостоятельного продлениянет.
- MKT002 UUID885855f1-a9bd-4bd7-85bc-26e38244e030: численный общийhourcap, окно/timezone/actions/statuses/sources/exceptions.
- MKT014 UUID5925f130-da73-4bb6-bb2f-c0a7de8e249b: весьportfolio denominator, минимум/boost,округление/existingboosted для90/10.
- MKT022 UUIDf156bf2d-ebd9-477e-aed0-a874d4ac16eb: единыйatomicowner/lifecycle гипотезы междуcontent/feed/всемиCPX.
- Новые цепочки МОП/РОП/ЕЦС, provider402 и реальные72h подтверждать фактически. Не отправлять клиентам и не тратить деньги ради QA. Coverage по уникальным наступившим UUID/version/account/window; negativeevaluated, missed, unknown/noevent и отсутствиеэффекта различаются.

## Последние доказательства и наблюдение

Матрица44строки×10колонок снята02:01:45.808927→02:01:47.911845UTC на6804; index `bbf202f47fc86ab37bb9f5779d28f9bae5d3b3f459ee416b522ba19a15252e7a`. 37expected/connected внешних +1PBIavailableCRM +3internal +1alias +2watch. ПБИ CRM AVAILABLE_UNCONFIGURED,142историческихdeal; это не активнаяCRMавтоматизация. В boris_crm_tasks естьcreated_at/completed_at, нетupdated_at; не приписывать другиеизменения новомуокну.

MOP storedDB00:23:49→02:02:31UTC:0newdrafts/events/историческихcontinuations,WAITING_NATURAL_EVENT; externalintakecomplete=false. Старые6событий — отдельныйbaseline.

Поздний естественный hourly02:05→02:10:36UTC завершёнfail=0 по orchestration. Garikno_action/waiting1, Maria/Planetaeffect_check; Albertpublishtariffwait, PBIprovider_stats_lagging/waiting2. Полныйlogсодержит прежниеrolloutотказы; DONE неglobal/businessPASS. Чтение02:24:41UTC отдельно отматрицы02:01.

Обычныеaccepted72hокна earliestUTC: ordinary4Oct11 22:04:43.825566;GarikOct11 23:19:25.571530;UIOct11 23:49:25.333255;pilotOct12 00:23:49.509574. V3/protectedNOT_STARTED. Будущее принятие запускает собственное окно.

## Отчёт, мониторинг и контекст

Полный private1186отчёт10461/10462 доставлен ранее2/2. Финальное дополнение10467 доставлено02:32:56UTC, UUID `2e4a8574-7b78-5f4e-8270-3f5a0af3d982`, body/readback SHA `dda52da573467dcfa2961ef4769e5080cf7f0fe25bd3b825df6ceb09db1311e0`, receipt `c306e4e36b113f4bc242a43a7d288e2572fb05a16892bcd8b8083b57bacf4713`. Не повторять. Исходный sender helper имел ошибочный owner_user_id в readonlypreflight; она случилась до send/receiptwrite. Новый schema_v2 SHA876e6e… использовал реально прочитанный created_by_user_id, exacttitle/account/GROUP/members[2]; корректная единственная отправка подтверждена.

Обновлены толькоprompts двух существующих задач, exactreadback:
AI KPI `6ac4cc26b80481919486c7cdcc49d018` — отчёт в тот же чат и проверенныйprivate1186; RecoveryWatch `6ac7358484908191baef190f8d682938` — уведомляет только новое существенное изменение. Расписания/получатели/enabled сохранены. AI-onlyexpiryГарика `6ac4cc2e1fc881919d8c7ebe2436d0eb` до10.10 остался неизменён; МОП вместе сAI не отключать. Серверныетаймеры не менялись.

SentinelX context `sentinelx_context_sxc_CBWCVS36` **revision7**. Сохранённое знание не заменяет live state/ownership/действительный допуск. Machinecheckpoint с более новыми receipts имеет приоритет. Предыдущие checkpoint и handoff сохранены в `outputs/clients510-execution-20261008/final-handoff/`; чужие workstreams не редактировались.
