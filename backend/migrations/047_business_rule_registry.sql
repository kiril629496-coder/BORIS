BEGIN;

-- BORIS_BUSINESS_RULE_REGISTRY_V1
-- Full minimum catalog from the architecture audit. Control Plane remains the
-- single Rule Registry; this migration only seeds versioned business rules.

WITH rules(key,rule_id,title,module,operation,money_side_effect,external_side_effect,mandatory_test) AS (
VALUES
  ('rule.marketing.first_bid_hourly_limit','MKT-001','Первое повышение ставки ограничено часовым атомарным лимитом','marketing','AVITO_BID_WRITE',true,true,'marketer_action_outcome_contract'),
  ('rule.marketing.hourly_action_limit','MKT-002','Почасовой лимит денежных действий','marketing','AVITO_BID_WRITE',true,true,'marketer_action_outcome_contract'),
  ('rule.marketing.daily_action_limit','MKT-003','Суточный лимит денежных действий','marketing','AVITO_BID_WRITE',true,true,'marketer_action_outcome_contract'),
  ('rule.marketing.hard_bid_cap','MKT-004','Абсолютный hard cap ставки','marketing','AVITO_BID_WRITE',true,true,'marketer_action_outcome_contract'),
  ('rule.marketing.daily_budget','MKT-005','Только явно подтверждённый суточный бюджет разрешает расход','marketing','AVITO_BUDGET_WRITE',true,true,'marketer_action_outcome_contract'),
  ('rule.marketing.budget_owner_provenance','MKT-006','Бюджет имеет подтверждённое происхождение от владельца','marketing','AVITO_BUDGET_WRITE',true,false,'marketer_action_outcome_contract'),
  ('rule.marketing.budget_brake','MKT-007','Budget brake блокирует повышение до перерасхода','marketing','AVITO_BID_WRITE',true,false,'marketer_action_outcome_contract'),
  ('rule.marketing.spend_freshness','MKT-008','Денежное действие требует свежий расход','marketing','AVITO_BID_WRITE',true,false,'marketer_action_outcome_contract'),
  ('rule.marketing.cpl_redline','MKT-009','CPL redline блокирует невыгодное повышение','marketing','AVITO_BID_WRITE',true,false,'marketer_action_outcome_contract'),
  ('rule.marketing.live_balance_guard','MKT-010','Денежное действие требует достаточный live balance','marketing','AVITO_BID_WRITE',true,false,'marketer_action_outcome_contract'),
  ('rule.marketing.low_views','MKT-011','Low views запускает ограниченное измеряемое действие','marketing','AVITO_BID_WRITE',true,false,'marketer_action_outcome_contract'),
  ('rule.marketing.winner_scaling','MKT-012','Победители масштабируются ограниченным шагом','marketing','AVITO_BID_WRITE',true,false,'marketer_action_outcome_contract'),
  ('rule.marketing.profitable_day_continue','MKT-013','Выгодный день может продолжать масштабирование в рамках лимитов','marketing','AVITO_BID_WRITE',true,false,'marketer_action_outcome_contract'),
  ('rule.marketing.promotion_90_10','MKT-014','Основной пул держится на минимуме, усиление только ограниченной доли','marketing','AVITO_PROMOTION_WRITE',true,false,'marketer_action_outcome_contract'),
  ('rule.marketing.new_ad_activation','MKT-015','Новая карточка активируется только через canonical money contour','marketing','AVITO_PROMOTION_WRITE',true,true,'marketer_action_outcome_contract'),
  ('rule.marketing.first_bid_policy','MKT-016','Первая ставка проходит те же денежные guards','marketing','AVITO_BID_WRITE',true,false,'marketer_action_outcome_contract'),
  ('rule.marketing.feed_ramp','MKT-017','Feed ramp ограничен теми же budget/CPL guards','marketing','AVITO_BID_WRITE',true,false,'marketer_action_outcome_contract'),
  ('rule.marketing.rollback','MKT-018','Rollback подтверждается provider readback','marketing','AVITO_BID_WRITE',true,true,'marketer_action_outcome_contract'),
  ('rule.marketing.negative_feedback','MKT-019','Негативный эффект блокирует дальнейшее усиление','marketing','AVITO_BID_WRITE',true,false,'marketer_action_outcome_contract'),
  ('rule.marketing.money_idempotency','MKT-020','Каждое денежное действие имеет стабильную idempotency identity','marketing','AVITO_BID_WRITE',true,false,'marketer_action_outcome_contract'),
  ('rule.marketing.mutation_per_cycle','MKT-021','Число мутаций за цикл ограничено','marketing','AVITO_BID_WRITE',true,false,'marketer_action_outcome_contract'),
  ('rule.marketing.experiment_isolation','MKT-022','Одновременно действует одна измеряемая гипотеза на item','marketing','AVITO_BID_WRITE',true,false,'marketer_action_outcome_contract'),
  ('rule.messages.first_response','MSG-001','Новое входящее состояние получает один главный ответ','messages','MESSAGE_SEND',false,true,'mop_no_duplicate_reply_guard'),
  ('rule.messages.greeting','MSG-002','Первый ответ содержит корректное приветствие','messages','MESSAGE_SEND',false,false,'mop_no_duplicate_reply_guard'),
  ('rule.messages.reply_dedupe','MSG-003','Повторный ответ на то же состояние не отправляется','messages','MESSAGE_SEND',false,false,'mop_no_duplicate_reply_guard'),
  ('rule.messages.send_ownership','MSG-004','Отправку сообщения выполняет только canonical message contour','messages','MESSAGE_SEND',false,true,'mop_no_duplicate_reply_guard'),
  ('rule.messages.human_takeover','MSG-005','Human takeover переводит automation в явное состояние','messages','MESSAGE_SEND',false,false,'mop_no_duplicate_reply_guard'),
  ('rule.messages.empty_chat','MSG-006','Empty chat обрабатывается одним canonical trigger','messages','MESSAGE_SEND',false,false,'mop_no_duplicate_reply_guard'),
  ('rule.messages.viewed_phone','MSG-007','Viewed phone не создаёт дублирующий ответ','messages','MESSAGE_SEND',false,false,'mop_no_duplicate_reply_guard'),
  ('rule.messages.phone_received','MSG-008','Полученный телефон переводит lead по canonical CRM handoff','messages','MESSAGE_SEND',false,false,'mop_no_duplicate_reply_guard'),
  ('rule.messages.job_seeker','MSG-009','Соискатель не проходит sales automation','messages','MESSAGE_SEND',false,false,'mop_no_duplicate_reply_guard'),
  ('rule.messages.stale_inbound','MSG-010','Устаревшее входящее не вызывает новый autosend','messages','MESSAGE_SEND',false,false,'mop_no_duplicate_reply_guard'),
  ('rule.messages.provider_wait','MSG-011','Provider недоступен становится WAIT, не FAIL и не дубль','messages','MESSAGE_SEND',false,false,'mop_no_duplicate_reply_guard'),
  ('rule.messages.entitlement_402','MSG-012','402/entitlement становится WAIT_EXTERNAL без опасного replay','messages','MESSAGE_SEND',false,false,'mop_no_duplicate_reply_guard'),
  ('rule.messages.technical_handoff','MSG-013','Технический handoff не считается завершённой продажей','messages','MESSAGE_SEND',false,false,'mop_no_duplicate_reply_guard'),
  ('rule.messages.crm_handoff','MSG-014','CRM handoff фиксируется доказательством','messages','CRM_STATE_WRITE',false,true,'mop_no_duplicate_reply_guard'),
  ('rule.messages.first_turn_identity','MSG-015','Первый ответ не раскрывает внутреннюю identity automation','messages','MESSAGE_SEND',false,false,'mop_no_duplicate_reply_guard'),
  ('rule.messages.autosend_eligibility','MSG-016','Autosend разрешён только при полной eligibility','messages','MESSAGE_SEND',false,false,'mop_no_duplicate_reply_guard'),
  ('rule.crm.deal_creation','CRM-001','Сделка создаётся один раз по canonical client identity','crm','CRM_DEAL_CREATE',false,true,'crm_inbound'),
  ('rule.crm.next_action','CRM-002','Next action отражает фактическое необработанное обязательство','crm','CRM_STATE_WRITE',false,false,'crm_inbound'),
  ('rule.crm.owner_assignment','CRM-003','Owner assignment имеет один canonical source','crm','CRM_STATE_WRITE',false,true,'crm_inbound'),
  ('rule.crm.task_creation','CRM-004','Задача CRM создаётся идемпотентно','crm','CRM_TASK_WRITE',false,true,'crm_inbound'),
  ('rule.crm.task_completion','CRM-005','Task completion требует outcome evidence','crm','CRM_TASK_WRITE',false,true,'crm_inbound'),
  ('rule.crm.human_mop_ownership','CRM-006','Human и MOP ownership взаимоисключающи','crm','CRM_STATE_WRITE',false,false,'crm_inbound'),
  ('rule.crm.missed_calls','CRM-007','Missed call создаёт один coalesced follow-up','crm','CRM_TASK_WRITE',false,false,'crm_inbound'),
  ('rule.crm.stale_task_cleanup','CRM-008','Устаревшая задача закрывается только после проверки факта','crm','CRM_TASK_WRITE',false,false,'crm_inbound'),
  ('rule.crm.answered_inquiry_cleanup','CRM-009','Уже отвеченный inquiry не остаётся открытой задачей','crm','CRM_TASK_WRITE',false,false,'crm_inbound'),
  ('rule.crm.mop_handoff','CRM-010','CRM↔MOP handoff имеет одну state transition','crm','CRM_STATE_WRITE',false,true,'crm_inbound'),
  ('rule.crm.phone_handoff','CRM-011','CRM↔Phone связь использует canonical phone identity','crm','CRM_STATE_WRITE',false,true,'crm_inbound'),
  ('rule.crm.calltracking_handoff','CRM-012','CRM↔Calltracking связь использует canonical call identity','crm','CRM_STATE_WRITE',false,true,'crm_inbound'),
  ('rule.telephony.outbound_call','TEL-001','Исходящий звонок создаётся с exactly-once provider identity','telephony','PHONE_PROVIDER_COMMAND',false,true,'phone_provider_call_identity_exactly_once'),
  ('rule.telephony.answer','TEL-002','Ответ на звонок проходит monotonic lifecycle','telephony','PHONE_PROVIDER_COMMAND',false,true,'phone_provider_call_identity_exactly_once'),
  ('rule.telephony.hangup','TEL-003','Завершение звонка monotonic и идемпотентно','telephony','PHONE_PROVIDER_COMMAND',false,true,'phone_provider_call_identity_exactly_once'),
  ('rule.telephony.recording','TEL-004','Запись привязана к точному call identity','telephony','PHONE_RECORDING_FETCH',false,true,'phone_provider_call_identity_exactly_once'),
  ('rule.telephony.stt','TEL-005','STT запускается только для разрешённой записи','telephony','PHONE_RECORDING_FETCH',false,false,'phone_provider_call_identity_exactly_once'),
  ('rule.telephony.retry','TEL-006','Retry bounded и не повторяет ambiguous provider mutation','telephony','PHONE_PROVIDER_COMMAND',false,false,'phone_provider_call_identity_exactly_once'),
  ('rule.telephony.command_lifecycle','TEL-007','Command lifecycle имеет terminal/exhausted states','telephony','PHONE_PROVIDER_COMMAND',false,true,'phone_provider_call_identity_exactly_once'),
  ('rule.telephony.provider_event','TEL-008','Provider event deduped по provider identity','telephony','PHONE_PROVIDER_COMMAND',false,false,'phone_provider_call_identity_exactly_once'),
  ('rule.telephony.push','TEL-009','Push привязан к текущему device/token','telephony','PHONE_PUSH_SEND',false,true,'phone_provider_call_identity_exactly_once'),
  ('rule.telephony.callback_sla','TEL-010','Callback SLA создаёт одно обязательство','telephony','CRM_TASK_WRITE',false,false,'phone_provider_call_identity_exactly_once'),
  ('rule.telephony.provider_readiness','TEL-011','Нет readiness → WAIT_EXTERNAL','telephony','PHONE_PROVIDER_COMMAND',false,false,'phone_provider_call_identity_exactly_once'),
  ('rule.telephony.phone_identity','TEL-012','Номер/peer identity tenant-scoped','telephony','PHONE_PROVIDER_COMMAND',false,false,'phone_provider_call_identity_exactly_once'),
  ('rule.telephony.crm_sync','TEL-013','Call→CRM sync exactly once','telephony','CRM_STATE_WRITE',false,true,'phone_provider_call_identity_exactly_once'),
  ('rule.telephony.billing','TEL-014','Минуты телефонии списываются идемпотентно','telephony','BILLING_USAGE_WRITE',true,true,'phone_provider_call_identity_exactly_once'),
  ('rule.telephony.failed_command_states','TEL-015','Provider reject и internal fail не смешиваются','telephony','PHONE_PROVIDER_COMMAND',false,false,'phone_provider_call_identity_exactly_once'),
  ('rule.email.enqueue','EML-001','Email intent ставится в очередь идемпотентно','email','EMAIL_SEND',false,false,'email_open_tracking'),
  ('rule.email.send','EML-002','Отправка выполняется только canonical email owner','email','EMAIL_SEND',false,true,'email_open_tracking'),
  ('rule.email.retry','EML-003','Email retry bounded','email','EMAIL_SEND',false,false,'email_open_tracking'),
  ('rule.email.delivery_unknown','EML-004','Ambiguous SMTP result не пересылается вслепую','email','EMAIL_SEND',false,false,'email_open_tracking'),
  ('rule.email.sent_copy','EML-005','Sent copy имеет отдельный durable retry','email','EMAIL_SEND',false,false,'email_open_tracking'),
  ('rule.email.open_tracking','EML-006','Open tracking не меняет факт доставки','email','EMAIL_SEND',false,false,'email_open_tracking'),
  ('rule.email.reply_tracking','EML-007','Reply связывается с точным campaign/member','email','EMAIL_SEND',false,false,'email_open_tracking'),
  ('rule.email.provider_wait','EML-008','Provider недоступен становится WAIT_EXTERNAL','email','EMAIL_SEND',false,false,'email_open_tracking'),
  ('rule.email.daily_send_limits','EML-009','Daily send limit блокирует без потери очереди','email','EMAIL_SEND',false,false,'email_open_tracking'),
  ('rule.email.idempotency','EML-010','Provider send имеет стабильную idempotency identity','email','EMAIL_SEND',false,false,'email_open_tracking'),
  ('rule.feed_factory.draft','FED-001','Draft создаётся атомарно и versioned','feed_factory','FEED_PUBLISH',false,false,'feed_quantity_e2e'),
  ('rule.feed_factory.validation','FED-002','Невалидный artifact не публикуется','feed_factory','FEED_PUBLISH',false,false,'feed_quantity_e2e'),
  ('rule.feed_factory.media_quality','FED-003','Media quality fail закрывает publish fail-closed','feed_factory','FEED_PUBLISH',false,false,'feed_quantity_e2e'),
  ('rule.feed_factory.xml_generation','FED-004','XML соответствует canonical category/schema contract','feed_factory','FEED_PUBLISH',false,false,'feed_quantity_e2e'),
  ('rule.feed_factory.publish','FED-005','Publish выполняется одним canonical owner','feed_factory','FEED_PUBLISH',false,true,'feed_quantity_e2e'),
  ('rule.feed_factory.review','FED-006','Review state материализован и доказуем','feed_factory','FEED_PUBLISH',false,false,'feed_quantity_e2e'),
  ('rule.feed_factory.rejection','FED-007','Provider rejection terminal до нового artifact','feed_factory','FEED_PUBLISH',false,false,'feed_quantity_e2e'),
  ('rule.feed_factory.partial_publish','FED-008','Partial publish отражает item-level truth','feed_factory','FEED_PUBLISH',false,false,'feed_quantity_e2e'),
  ('rule.feed_factory.retry','FED-009','Retry не создаёт повторную публикацию','feed_factory','FEED_PUBLISH',false,false,'feed_quantity_e2e'),
  ('rule.feed_factory.duplicate','FED-010','Cross-campaign duplicate блокируется до provider','feed_factory','FEED_PUBLISH',false,false,'feed_quantity_e2e'),
  ('rule.feed_factory.replacement','FED-011','Replacement требует explicit authorization','feed_factory','FEED_PUBLISH',false,true,'feed_quantity_e2e'),
  ('rule.feed_factory.superseded','FED-012','Новый artifact supersedes старый явно','feed_factory','FEED_PUBLISH',false,false,'feed_quantity_e2e'),
  ('rule.feed_factory.schedule','FED-013','Scheduled publish учитывает due/backoff','feed_factory','FEED_PUBLISH',false,false,'feed_quantity_e2e'),
  ('rule.feed_factory.terminal_states','FED-014','Published/rejected/failed имеют durable terminal proof','feed_factory','FEED_PUBLISH',false,false,'feed_quantity_e2e'),
  ('rule.social.project_ownership','SOC-001','Social project имеет одного owner','social','SOCIAL_PUBLISH',false,false,'social_text_stable_idempotency'),
  ('rule.social.publish','SOC-002','Публикацию выполняет canonical Social owner','social','SOCIAL_PUBLISH',false,true,'social_text_stable_idempotency'),
  ('rule.social.schedule','SOC-003','Scheduled slot не считается выполненным без provider proof','social','SOCIAL_PUBLISH',false,false,'social_text_stable_idempotency'),
  ('rule.social.text_generation','SOC-004','Один plan state создаёт один canonical post draft','social','SOCIAL_PUBLISH',false,false,'social_text_stable_idempotency'),
  ('rule.social.allowed_links','SOC-005','Публичные ссылки проходят Social policy','social','SOCIAL_PUBLISH',false,false,'social_text_stable_idempotency'),
  ('rule.social.utm','SOC-006','BORIS/Software links используют canonical короткие UTM','social','SOCIAL_PUBLISH',false,false,'social_text_stable_idempotency'),
  ('rule.social.transport_readiness','SOC-007','Нет TG/VK readiness → WAIT_EXTERNAL','social','SOCIAL_PUBLISH',false,false,'social_text_stable_idempotency'),
  ('rule.social.paid_publication','SOC-008','Paid social action требует entitlement/billing','social','SOCIAL_PUBLISH',true,true,'social_text_stable_idempotency'),
  ('rule.social.wait_external','SOC-009','WAIT_EXTERNAL не превращается в аварийный self-heal','social','SOCIAL_PUBLISH',false,false,'social_text_stable_idempotency'),
  ('rule.social.retry','SOC-010','Social retry bounded и provider-identity safe','social','SOCIAL_PUBLISH',false,false,'social_text_stable_idempotency'),
  ('rule.prospecting.discovery','PRO-001','Discovery сохраняет provenance источника','prospecting','INTERNAL_PROSPECTING',false,false,'prospecting_provider_rate_guard'),
  ('rule.prospecting.source_priority','PRO-002','Источники обходятся по canonical priority','prospecting','INTERNAL_PROSPECTING',false,false,'prospecting_provider_rate_guard'),
  ('rule.prospecting.dedupe','PRO-003','Prospect dedupe обязателен до outreach','prospecting','INTERNAL_PROSPECTING',false,false,'prospecting_provider_rate_guard'),
  ('rule.prospecting.platform_readiness','PRO-004','Недоступная площадка имеет explicit WAIT/BLOCK','prospecting','INTERNAL_PROSPECTING',false,false,'prospecting_provider_rate_guard'),
  ('rule.prospecting.captcha','PRO-005','Captcha fail-closed без ложного success','prospecting','INTERNAL_PROSPECTING',false,false,'prospecting_provider_rate_guard'),
  ('rule.prospecting.warmup','PRO-006','Warmup state не считается placement','prospecting','INTERNAL_PROSPECTING',false,false,'prospecting_provider_rate_guard'),
  ('rule.prospecting.sending','PRO-007','Outreach выполняется через canonical delivery owner','prospecting','EMAIL_SEND',false,true,'prospecting_provider_rate_guard'),
  ('rule.prospecting.retry','PRO-008','Prospecting retry bounded','prospecting','INTERNAL_PROSPECTING',false,false,'prospecting_provider_rate_guard'),
  ('rule.prospecting.provider_limits','PRO-009','Provider rate limit становится WAIT/RETRY','prospecting','INTERNAL_PROSPECTING',false,false,'prospecting_provider_rate_guard'),
  ('rule.prospecting.daily_plan','PRO-010','Daily plan измеряется plan→fact','prospecting','INTERNAL_PROSPECTING',false,false,'prospecting_provider_rate_guard'),
  ('rule.billing.charge','BIL-001','Charge выполняется ровно один раз','billing','BILLING_CHARGE',true,true,'billing_usage_idempotency'),
  ('rule.billing.reservation','BIL-002','Reservation имеет durable identity','billing','BILLING_CHARGE',true,false,'billing_usage_idempotency'),
  ('rule.billing.usage','BIL-003','Usage записывается идемпотентно','billing','BILLING_USAGE_WRITE',true,true,'billing_usage_idempotency'),
  ('rule.billing.idempotency','BIL-004','Каждый charge/usage требует idempotency key','billing','BILLING_USAGE_WRITE',true,false,'billing_usage_idempotency'),
  ('rule.billing.rollback','BIL-005','Billing rollback только по точному receipt','billing','BILLING_CHARGE',true,true,'billing_usage_idempotency'),
  ('rule.billing.payment_required','BIL-006','Недостаток entitlement становится BLOCKED_POLICY/WAIT','billing','BILLING_CHARGE',true,false,'billing_usage_idempotency')
)
INSERT INTO control_requirements(
 id,key,title,normalized_rule,requirement_type,scope_type,scope_id,module,priority,status,
 desired_state_json,conditions_json,actions_json,constraints_json,verification_json,
 dependencies_json,source_json,version
)
SELECT gen_random_uuid(), r.key, r.title,
       r.title || ' Canonical BORIS business rule; a caller may not bypass the registered owner or terminal-state contract.',
       'business_rule','global','*',r.module,
       CASE WHEN r.money_side_effect OR r.external_side_effect THEN 'critical' ELSE 'high' END,
       'active',
       jsonb_build_object(
         'enforced',true,
         'rule_id',r.rule_id,
         'operation',r.operation,
         'allowed_states',jsonb_build_array('OK','BLOCKED_POLICY','WAIT_EXTERNAL','RETRY','DEGRADED','FAILED_INTERNAL'),
         'mutation_type',CASE WHEN r.external_side_effect THEN 'external_side_effect' ELSE 'policy_or_internal_state' END,
         'external_side_effect',r.external_side_effect,
         'money_side_effect',r.money_side_effect,
         'tenant_scope',CASE WHEN r.module IN ('marketing','messages','crm','telephony','email','feed_factory','social','prospecting','billing') THEN 'account_required' ELSE 'global' END
       ),
       '[]'::jsonb,
       CASE WHEN r.external_side_effect THEN jsonb_build_array(jsonb_build_object('operation',r.operation,'must_use_action_owner',true)) ELSE '[]'::jsonb END,
       jsonb_build_array(
          jsonb_build_object('idempotency_required',r.external_side_effect),
          jsonb_build_object('evidence_required',r.external_side_effect),
          jsonb_build_object('silent_failure_forbidden',true)
       ),
       jsonb_build_array(
          jsonb_build_object('type','production_contract','check',r.mandatory_test,'required',true),
          jsonb_build_object('type','architecture_test','path','tests/test_business_rule_registry.py','required',true)
       ),
       '[]'::jsonb,
       jsonb_build_object(
         'source_of_truth','control_plane',
         'catalog','architecture_business_rule_v1',
         'rule_id',r.rule_id,
         'owner',(SELECT c.contract_json->>'writer' FROM control_module_contracts c WHERE c.module=r.module),
         'operation',r.operation,
         'mandatory_tests',jsonb_build_array(r.mandatory_test,'tests/test_business_rule_registry.py'),
         'external_side_effect',r.external_side_effect,
         'money_side_effect',r.money_side_effect,
         'tenant_scope','account_required',
         'reason','architecture audit completion'
       ),
       1
FROM rules r
ON CONFLICT (key) DO NOTHING;

INSERT INTO control_requirement_versions(requirement_id,version,snapshot_json,change_reason)
SELECT q.id,q.version,
       jsonb_build_object(
         'key',q.key,'title',q.title,'normalized_rule',q.normalized_rule,'requirement_type',q.requirement_type,
         'scope_type',q.scope_type,'scope_id',q.scope_id,'module',q.module,'priority',q.priority,'status',q.status,
         'desired_state_json',q.desired_state_json,'conditions_json',q.conditions_json,'actions_json',q.actions_json,
         'constraints_json',q.constraints_json,'verification_json',q.verification_json,
         'dependencies_json',q.dependencies_json,'source_json',q.source_json,'version',q.version
       ),
       'BORIS architecture business rule catalog v1 seed'
FROM control_requirements q
WHERE q.status='active'
  AND q.requirement_type='business_rule'
  AND q.source_json->>'catalog'='architecture_business_rule_v1'
  AND NOT EXISTS (
     SELECT 1 FROM control_requirement_versions v
      WHERE v.requirement_id=q.id AND v.version=q.version
  );

COMMIT;
