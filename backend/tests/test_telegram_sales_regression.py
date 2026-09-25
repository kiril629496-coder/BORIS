from telegram_contact_reactivation import reactivation_quality_ok
from telegram_sales_replies import safe_sales_auto_reply, later_delay_days


def test_vendor_reply_is_not_buyer_continuation():
    policy,text=safe_sales_auto_reply('Я авитолог, у меня агентство и мы занимаемся продвижением')
    assert policy=='vendor_handoff'
    assert text is None


def test_explicit_decline_never_gets_auto_reply():
    policy,text=safe_sales_auto_reply('Нет, спасибо, не актуально')
    assert policy is None and text is None


def test_later_reply_is_handoff_not_positive_autoreply():
    policy,text=safe_sales_auto_reply('Давайте позже, через месяц вернемся')
    assert policy=='later_handoff'
    assert text is None
    assert later_delay_days('через месяц')==30


def test_meeting_request_is_target_action_branch():
    policy,text=safe_sales_auto_reply('Давайте созвонимся завтра')
    assert policy=='meeting_request'
    assert text and 'день и время' in text


def test_indirect_referral_is_not_reactivated():
    assert not reactivation_quality_ok('Виктор', 'Мой друг ищет авитолога. Я ему передам ваш контакт, он с вами свяжется')
    assert not reactivation_quality_ok('Наталия', 'Авито ведет Иван. Свяжитесь с ним напрямую, пожалуйста')


def test_direct_buyer_context_stays_eligible():
    assert reactivation_quality_ok('Максим нужен авитолог', 'Напишите свой номер телефона, доберусь до рабочего места и позвоню')


def test_marketing_contractor_demand_is_eligible():
    from app.monitoring.quality import classify
    r=classify('Ищу маркетолога на проект, нужен подрядчик по рекламе',None,75)
    assert r['qualified'] and not r['reject']


def test_marketing_job_is_rejected():
    from app.monitoring.quality import classify
    r=classify('Вакансия: нужен маркетолог в штат, зарплата 120000',None,75)
    assert r['reject'] and not r['qualified']


def test_advertising_pain_is_buyer_demand():
    from app.monitoring.quality import classify
    r=classify('Реклама не окупается, лиды дорогие',None,75)
    assert r['qualified'] and not r['reject']


def test_plain_marketing_discussion_is_not_a_lead():
    from app.monitoring.quality import classify
    r=classify('Обсуждаем маркетинг и продажи, интересно мнение коллег',None,75)
    assert not r['qualified']


def _fit(body,intent='buy_service',monitor_score=95):
    import ast,re
    from pathlib import Path
    src=Path('monitor_lead_digest.py').read_text(); tree=ast.parse(src)
    wanted_globals={'BORIS_FIT_POSITIVE','BORIS_FIT_NEGATIVE'}; selected=[]
    for node in tree.body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id in wanted_globals for t in node.targets): selected.append(node)
        elif isinstance(node,ast.FunctionDef) and node.name=='calculate_boris_fit': selected.append(node)
    mod=ast.Module(body=selected,type_ignores=[]); ast.fix_missing_locations(mod); ns={'re':re}; exec(compile(mod,'<fit>','exec'),ns)
    return ns['calculate_boris_fit'](body,intent,monitor_score)


def test_marketing_buyer_reaches_hot_queue_threshold():
    assert _fit('Ищу маркетолога на проект, нужен подрядчик по рекламе')['score'] >= 75


def test_marketing_self_promo_stays_below_send_threshold():
    assert _fit('Я маркетолог, предлагаю услуги по рекламе','offer_service')['score'] < 75


def test_directologist_self_promo_is_rejected_centrally():
    from app.monitoring.quality import classify
    r=classify('Я директолог, работаю с рекламой и беру проекты',None,75)
    assert r['reject'] and not r['qualified']


def test_military_recruitment_is_never_a_sales_lead():
    from app.monitoring.quality import classify
    r=classify('Ищу авитолога для объявлений о наборе на службу по контракту',None,95)
    assert r['reject'] and not r['qualified']


def test_marketing_rule_matches_buyer_and_blocks_job():
    from app.monitoring import store
    from app.monitoring.rules import SearchRule,evaluate
    con=store.connect()
    try:
        with con.cursor() as cur:
            rules=[SearchRule.from_row(r) for r in store.load_rules(cur)]
        good=evaluate('Ищу директолога, нужен подрядчик по рекламе',rules,platform='telegram',source_id=None,source_kind='group',source_weight=20,posted_at=None)
        bad=evaluate('Вакансия: нужен маркетолог в штат, зарплата 120000',rules,platform='telegram',source_id=None,source_kind='group',source_weight=20,posted_at=None)
        assert good.matched and not bad.matched
    finally:
        con.close()


def test_trusted_author_identity_requires_access_hash():
    from app.services.telegram_sales_identity import trusted_author_identity
    assert trusted_author_identity('123456',{})==(None,None)

def test_trusted_author_identity_rejects_publisher_bot():
    from app.services.telegram_sales_identity import trusted_author_identity
    assert trusted_author_identity('123456',{'author_access_hash':'987654','author_is_bot':True})==(None,None)

def test_trusted_author_identity_accepts_real_user():
    from app.services.telegram_sales_identity import trusted_author_identity
    assert trusted_author_identity('123456',{'author_access_hash':'987654','author_is_bot':False})==(123456,987654)


def test_targetologist_only_request_stays_outside_auto_send_scope():
    text='Требуется таргетолог для настройки рекламы в ВК для онлайн-магазина. Пишите портфолио и стоимость в лс.'
    assert _fit(text,'buy_service',95)['score'] < 75


def test_trusted_author_identity_rejects_min_user():
    from app.services.telegram_sales_identity import trusted_author_identity
    assert trusted_author_identity('6877140007', {'author_access_hash':'5671647203221898219','author_is_bot':False,'author_is_min':True}) == (None,None)

def test_publisher_bot_redirect_reply_is_vendor_handoff():
    from telegram_sales_replies import safe_sales_auto_reply
    policy,text=safe_sales_auto_reply('Добрый день! Дополнительные сведения о данной вакансии можно получить через бота @GetClient_TG_bot.')
    assert policy == 'vendor_handoff' and text is None


def test_explicit_username_wins_over_publisher_peer():
    from hot_lead_auto_sender import select_outbound_route
    assert select_outbound_route('Shrek_Shrek_ShrekShrek',8751345427)==('username','Shrek_Shrek_ShrekShrek')
    assert select_outbound_route('',6877140007)==('peer',6877140007)


def test_source_transparency_question_gets_safe_truthful_reply():
    from telegram_sales_replies import safe_sales_auto_reply
    policy,text=safe_sales_auto_reply('Подскажите, как вы сейчас клиентов ищете? Сами, по чатам?')
    assert policy=='source_transparency'
    assert text and 'публичных Telegram-чатах' in text
    assert 'Ваше сообщение' in text


def test_referral_to_marketer_is_not_reactivated():
    from telegram_contact_reactivation import reactivation_quality_ok
    assert not reactivation_quality_ok('Сергей нужен авитолог','Маркетологу дал ваш контакт, он напишет')


def test_manager_dialog_stage_is_preserved_until_qualified():
    from pathlib import Path
    src=Path('telegram_sales_replies.py').read_text()
    assert "ELSE 'в диалоге'" in src
    assert "VALUES(:m,:n,:c,'в диалоге'" in src



def test_source_transparency_dialog_has_bounded_followup():
    from pathlib import Path
    src=Path('telegram_sales_replies.py').read_text()
    assert "IN ('source_transparency','backlog_source_transparency')" in src
    assert "l.status='replied' AND l.monitor_intent='buy_service'" in src
    assert "l.followup_sent_at IS NULL" in src



def test_all_reply_outbound_uses_serialized_sender():
    from pathlib import Path
    src=Path('telegram_sales_replies.py').read_text()
    assert 'def send_personal_serialized' in src
    # Direct personal send calls are allowed only inside the serializer and the
    # separately advisory-locked follow-up worker. Backlog/live continuation
    # must use the shared serializer.
    assert "send_personal_serialized(client,entity,auto_text,'backlog')" in src
    assert "send_personal_serialized(client,entity,auto_text,'live_reply')" in src

def test_buyer_auto_reply_is_serialized_but_not_cold_paced():
    from pathlib import Path
    src=Path('telegram_sales_replies.py').read_text()
    block=src[src.index('def send_personal_serialized'):src.index('HARD_SEND_START_HOUR') ]
    assert 'try_outbound_lock' in block
    assert 'client.send_message' in block
    assert 'recent_outbound' not in block


def test_health_guards_auto_reply_db_consistency():
    from pathlib import Path
    src=Path('telegram_sales_health.py').read_text()
    for key in ('auto_reply_without_message','auto_reply_without_timestamp','duplicate_auto_reply_message'):
        assert key in src


def test_daily_report_has_retry_verifier_and_deadline_guard():
    from pathlib import Path
    audit=Path('telegram_sales_production_audit.py').read_text()
    verify=Path('telegram_sales_daily_report_verify.py').read_text()
    assert 'daily_report_verify_timer_active' in audit
    assert 'daily_report_confirmed_after_deadline' in audit
    assert 'telegram_sales_daily_report.py' in verify and '--send' in verify


def test_production_audit_guards_auto_reply_db_consistency():
    from pathlib import Path
    src=Path('telegram_sales_production_audit.py').read_text()
    for key in ('auto_reply_without_message','auto_reply_without_timestamp','duplicate_auto_reply_message'):
        assert key in src


def test_delivery_readback_is_required_for_new_personal_outbound():
    from pathlib import Path
    outbound=Path('app/monitoring/telegram_outbound.py').read_text()
    sender=Path('hot_lead_auto_sender.py').read_text()
    replies=Path('telegram_sales_replies.py').read_text()
    assert 'def verify_personal_message' in outbound
    assert 'outbound_verified_at' in sender and "'verified':verified" in outbound
    assert 'auto_reply_verified_at' in replies and 'followup_verified_at' in replies

def test_health_and_audit_guard_unverified_delivery_after_baseline():
    from pathlib import Path
    for f in ('telegram_sales_health.py','telegram_sales_production_audit.py'):
        src=Path(f).read_text()
        for key in ('unverified_initial_delivery','unverified_followup_delivery','unverified_auto_reply_delivery'):
            assert key in src
        assert 'tg_delivery_verification_baseline' in src


def test_stale_qualified_telegram_dialogs_have_bounded_recovery():
    from pathlib import Path
    src=Path('telegram_sales_replies.py').read_text()
    assert "l.status='qualified'" in src
    assert "nq.remind_at < NOW()-interval '6 hours'" in src
    assert 'manual_outbound_after_reply' in src
    assert 'followup_verified_at' in src

def test_overdue_telegram_crm_reminder_is_observable():
    from pathlib import Path
    assert 'overdue_tg_crm_reminder_24h' in Path('telegram_sales_health.py').read_text()
    assert 'overdue_tg_crm_reminder_24h' in Path('telegram_sales_production_audit.py').read_text()


def test_watchdog_reconciles_unverified_delivery_without_resend():
    from pathlib import Path
    w=Path('telegram_sales_watchdog.py').read_text()
    r=Path('telegram_delivery_reconcile.py').read_text()
    assert 'telegram_delivery_reconcile.py' in w
    assert 'verify_personal_message' in r
    assert 'send_message(' not in r


def test_existing_avitologist_is_not_reactivated_or_followed_up():
    from telegram_contact_reactivation import reactivation_quality_ok
    assert not reactivation_quality_ok('Павел','Уже работаю с авитологом')
    assert not reactivation_quality_ok('Павел','Уже есть свой авитолог')
    assert reactivation_quality_ok('Максим нужен авитолог','Интересует стоимость и сколько заявок можно получить')


def test_explicit_contact_identity_does_not_inherit_publisher_peer():
    from pathlib import Path
    src=Path('hot_lead_queue.py').read_text()
    assert "explicit_u and author_u and explicit_u != author_u" in src
    assert "peer_id,access_hash=None,None" in src


def test_watchdog_retries_daily_report_after_deadline():
    from pathlib import Path
    src=Path('telegram_sales_watchdog.py').read_text()
    assert "retry_daily_report_verifier" in src
    assert "daily_report_receipt_message_id" in src
    assert "boris-tg-sales-daily-report-verify.service" in src


def test_digest_aggregator_is_not_buyer_demand():
    from app.monitoring.quality import classify
    text='📋 Дайджест чата · 3 сентября ❓ Вопросы ▪️ Где черпают клиентов для Avito объявлений 🤖 Бесплатный AI-дайджест'
    r=classify(text,'buy_service',90)
    assert r['reject'] and not r['qualified'] and 'digest_aggregator' in r['reasons']


def test_reactivation_no_context_uses_short_cautious_message():
    from telegram_contact_reactivation import draft
    msg=draft('Алексей нужен авитолог', '', ['авитолог'])
    assert 'Мы раньше пересекались по Авито' in msg
    assert len(msg) < 430
    assert 'Если тема Авито ещё актуальна' in msg


def test_daily_report_exposes_delivery_and_safety_budgets():
    from pathlib import Path
    src=Path('telegram_sales_daily_report.py').read_text()
    for marker in ('verified_initial','verified_followups','auto_replies_verified','invalidated_followups','outbound_total','REACT_DAILY_CAP','FOLLOWUP_DAILY_CAP'):
        assert marker in src
    assert 'Лимит инициативных исходящих' in src
    assert 'доставка подтверждена' in src


def test_active_high_stage_telegram_crm_repairs_missing_next_action():
    from pathlib import Path
    replies=Path('telegram_sales_replies.py').read_text()
    health=Path('telegram_sales_health.py').read_text()
    audit=Path('telegram_sales_production_audit.py').read_text()
    assert 'ensure_active_crm_next_actions' in replies
    assert 'missing_tg_crm_next_action' in health
    assert 'active_crm_next_actions_present' in audit


def test_source_scan_telemetry_records_raw_match_and_errors_without_extra_fetch():
    from pathlib import Path
    store=Path('app/monitoring/store.py').read_text()
    runner=Path('monitor_runner.py').read_text()
    assert 'telegram_source_scan_stats' in store
    assert 'bump_source_scan_stats' in store
    assert 'raw_messages=len(raw_items)' in runner
    assert "matched_messages=int(stats.get('matched_messages'" in runner
    assert 'scanner.fetch_new(source, limit=limit)' in runner


def test_source_ranker_counts_only_valid_non_skipped_buyers():
    from pathlib import Path
    src=Path('telegram_source_ranker.py').read_text()
    assert "l.boris_fit>=75" in src
    assert "l.status NOT IN ('skipped','declined','lost','ignored')" in src
    assert "elif buyers==1: target=min(target,18)" in src


def test_daily_selftest_must_be_fresh_after_1920():
    from pathlib import Path
    audit=Path('telegram_sales_production_audit.py').read_text()
    watchdog=Path('telegram_sales_watchdog.py').read_text()
    for src in (audit,watchdog):
        assert "replace(hour=19,minute=20" in src
        assert "ran_at" in src
    assert 'daily_selftest_fresh_after_1920' in audit
    assert 'retry_daily_selftest' in watchdog

def test_audit_hard_guards_actual_outside_window_outbound():
    from pathlib import Path
    src=Path('telegram_sales_production_audit.py').read_text()
    for key in ('outside_window_initial','outside_window_followup','outside_window_auto_reply','no_outside_window_personal_outbound'):
        assert key in src


def test_reactivation_priority_prefers_real_launch_over_generic_proof_request():
    from telegram_contact_reactivation import reactivation_priority
    launch,_=reactivation_priority('Александр авито','Хочу запустить рекламу на Авито и выстроить этот источник в систему','buyer_user')
    proof,_=reactivation_priority('Камиль авито','Пришлите кейсы. Какой бюджет, результат, сколько лид стоил и окупаемость?','proof_user')
    assert launch > proof

def test_reactivation_priority_never_overrides_safety_decline():
    from telegram_contact_reactivation import reactivation_priority,reactivation_quality_ok
    text='Хочу запустить рекламу, но уже работаю с авитологом и больше не пишите'
    score,_=reactivation_priority('Павел авито',text,'pavel_user')
    assert score > 0
    assert not reactivation_quality_ok('Павел авито',text)


def test_outside_window_positive_reply_schedules_next_allowed_action():
    from pathlib import Path
    src=Path('telegram_sales_replies.py').read_text()
    assert 'def next_allowed_send_time' in src
    assert "minute: int = 5" in src
    assert "policy in QUALIFY_POLICIES" in src
    assert "UPDATE manager_notes SET remind_at=:due" in src
    assert "Ответить Telegram-лиду в ближайшее разрешённое окно" in src


def test_daily_report_runs_after_final_1930_scan_window():
    from pathlib import Path
    report=Path('telegram_sales_daily_report.py').read_text()
    verify=Path('telegram_sales_daily_report_verify.py').read_text()
    watchdog=Path('telegram_sales_watchdog.py').read_text()
    audit=Path('telegram_sales_production_audit.py').read_text()
    assert "TG_SALES_DAILY_REPORT_MINUTE','38'" in report
    assert 'earliest=19:43' in verify
    assert 'minute>=48' in watchdog
    assert 'minute>=48' in audit


def test_morning_preflight_builds_safe_top4_reactivation_plan():
    from pathlib import Path
    src=Path('telegram_sales_morning_preflight.py').read_text()
    assert 'def build_reactivation_plan' in src
    assert 'reactivation_quality_ok' in src
    assert 'reactivation_priority' in src
    assert "tg_sales_reactivation_plan:" in src
    assert "build_reactivation_plan(4)" in src


def test_sender_orders_reactivation_by_saved_priority_before_legacy_tiebreak():
    from pathlib import Path
    src=Path('hot_lead_auto_sender.py').read_text()
    pri="CASE WHEN l.monitor_intent='existing_contact_reactivation' THEN COALESCE(l.reactivation_priority,0) ELSE 0 END DESC"
    assert pri in src
    assert src.index(pri) < src.index("WHEN l.monitor_intent='existing_contact_reactivation' AND lower(coalesce(l.original_text,''))")

def test_reactivation_plan_and_sender_share_priority_field():
    from pathlib import Path
    sender=Path('hot_lead_auto_sender.py').read_text()
    preflight=Path('telegram_sales_morning_preflight.py').read_text()
    assert 'reactivation_priority' in sender
    assert 'reactivation_priority' in preflight
    assert 'reactivation_quality_ok' in preflight


def test_target_action_deferred_reply_does_not_requalify_buyer():
    from pathlib import Path
    src=Path('telegram_sales_replies.py').read_text()
    assert "r.get('manager_status')=='целевое действие'" in src
    assert "policy='meeting_request'" in src
    assert 'не возвращаться к общей квалификации' in src
    assert 'удобное время по Москве' in src


def test_reactivation_case_word_in_customer_situation_is_not_proof_request():
    from telegram_contact_reactivation import reactivation_priority
    score,reason=reactivation_priority('Роман строительство авито','Ниша: строительство домов. Кейс: ниша перегрета. Есть идея построить воронку.','roman')
    assert 'proof_request' not in reason

def test_reactivation_explicit_case_request_is_proof_request():
    from telegram_contact_reactivation import reactivation_priority
    score,reason=reactivation_priority('Алексей авито','Хочу запустить рекламу на Авито. Есть кейсы по строительству?','alex')
    assert 'proof_request' in reason

def test_launch_and_existing_account_get_specific_reactivation_copy():
    from telegram_contact_reactivation import draft
    launch=draft('Александр строительство авито СПБ','Хочу запустить рекламу на Авито и потом выстроить этот источник в систему',['авито'])
    existing=draft('Екатерина авито Воронеж','Пришел сезон беседок. На нашем аккаунте остались готовые объявления, какая стоимость продвижения?',['авито'])
    assert 'что тестируем первым' in launch
    assert 'остались готовые объявления' in existing
    assert 'что оставить/обновить' in existing


def test_morning_preflight_sanitizes_reactivation_before_send_window():
    from pathlib import Path
    src=Path('telegram_sales_morning_preflight.py').read_text()
    assert 'morning_preflight_reactivation_quality_guard' in src
    assert "status='skipped'" in src
    assert 'first_message_draft=:d' in src
    assert 'score_text' in src and 'draft(' in src


def test_daily_report_uses_manager_funnel_stages_and_target_action():
    from pathlib import Path
    src=Path('telegram_sales_daily_report.py').read_text()
    assert "ml.status='квалифицирован'" in src
    assert "ml.status='целевое действие'" in src
    assert "ml.status='сделка'" in src
    assert 'Дошло до целевого действия / демонстрации' in src
    assert 'reply_to_advanced' in src


def test_daily_report_separates_initial_and_followup_reply_attribution():
    from pathlib import Path
    src=Path('telegram_sales_daily_report.py').read_text()
    for marker in ('buyer_reply_messages','initial_reply_leads','followup_reply_leads','initial_reply_rate','followup_reply_rate'):
        assert marker in src
    assert 'count(distinct hot_lead_id)' in src
    assert 'Initial → ответ' in src and 'Follow-up → ответ' in src


def test_production_audit_requires_complete_reactivation_queue():
    from pathlib import Path
    audit=Path('telegram_sales_production_audit.py').read_text()
    watchdog=Path('telegram_sales_watchdog.py').read_text()
    assert 'reactivation_queue_incomplete' in audit
    assert 'reactivation_queue_ready' in audit
    assert 'reactivation_priority is null' in audit
    assert 'reactivation_queue_ready' in watchdog


def test_personal_outbound_pacing_includes_auto_replies_but_daily_cap_does_not():
    from pathlib import Path
    src=Path('app/services/telegram_sales_safety.py').read_text()
    recent=src[src.index('def recent_outbound'):src.index('def remaining_outbound') if 'def remaining_outbound' in src else len(src)]
    counts=src[src.index('def outbound_counts'):src.index('def recent_outbound')]
    assert 'boris_sales_tg_replies' in recent and 'auto_replied_at' in recent
    assert 'boris_sales_tg_replies' not in counts


def test_late_buyer_replies_are_carried_into_next_morning_preflight():
    from pathlib import Path
    src=Path('telegram_sales_morning_preflight.py').read_text()
    assert 'previous_report_late_events' in src
    assert "steps['late_events']" in src
    assert "coalesce(r.auto_reply_policy,'')<>'vendor_handoff'" in src
    assert 'has_next_action' in src


def test_unalerted_buyer_reply_is_a_hard_guard():
    from pathlib import Path
    h=Path('telegram_sales_health.py').read_text()
    a=Path('telegram_sales_production_audit.py').read_text()
    assert 'buyer_reply_unalerted' in h and 'buyer_reply_unalerted' in a
    assert "received_at<now()-interval '5 minutes'" in h
    assert "received_at<now()-interval '5 minutes'" in a


def test_avito_low_view_and_setup_intent_is_qualified():
    from app.monitoring.quality import classify
    samples=(
      'На Авито мало просмотров, объявления есть, заявок почти нет. Кто поможет с Авито?',
      'Нужна настройка Авито под магазин, сейчас не идут заявки с Авито',
      'Нужно ведение Авито, объявления на Авито есть заявок нет',
      'Кто настроит Авито? Хотим больше заявок',
    )
    for text in samples:
        r=classify(text,None,80)
        assert r['qualified'] and not r['reject'], (text,r)


def test_avito_setup_phrase_does_not_override_self_promo_reject():
    from app.monitoring.quality import classify
    r=classify('Я авитолог, предлагаю услуги. Настраиваю Авито, беру проекты, нужно ведение Авито — пишите мне',None,90)
    assert r['reject'] and not r['qualified']


def test_health_does_not_self_latch_on_previous_degraded_cycle():
    from pathlib import Path
    h=Path('telegram_sales_health.py').read_text()
    a=Path('telegram_sales_production_audit.py').read_text()
    guard_line=[x for x in h.splitlines() if x.strip().startswith('guards=')][0]
    assert "recent_cycle_degraded']==0" not in guard_line
    assert "select status from telegram_sales_cycle_runs where status<>'running' order by started_at desc limit 1" in a


def test_declined_reply_has_owner_alert_retry_backlog():
    from pathlib import Path
    r=Path('telegram_sales_replies.py').read_text()
    h=Path('telegram_sales_health.py').read_text()
    a=Path('telegram_sales_production_audit.py').read_text()
    assert 'def alert_unalerted_declines' in r
    assert "l.status='declined' and r.alerted_at is null" in r
    assert 'TG_DECLINE_ALERT=PASS' in r
    assert "timezone(:tz,r.received_at)::date=timezone(:tz,now())::date" in h
    assert "timezone(:tz,r.received_at)::date=timezone(:tz,now())::date" in a


def test_recent_pass_guard_prevents_duplicate_full_cycle():
    from pathlib import Path
    src=Path('telegram_sales_cycle.py').read_text()
    assert 'def recent_pass_age_seconds' in src
    assert "TG_SALES_CYCLE_RECENT_PASS_SKIP_SEC" in src
    assert "TG_SALES_CYCLE_FORCE" in src
    main=src[src.index('def main() -> int:'):]
    assert main.index('should_skip_recent_pass()') < main.index('run_id=start_run()')
    assert 'TG_SALES_CYCLE=SKIP recent_pass' in main


def test_historical_vendor_and_referral_dialogs_are_not_reactivated():
    from telegram_contact_reactivation import reactivation_quality_ok
    bad=(
      'Хотели бы вы увеличить число продаж? Мы обеспечиваем стабильный и прогнозируемый поток заявок, чтобы вы могли совершать успешные сделки.',
      'Я развиваю сервис для интеграции WhatsApp, Telegram, MAX в amoCRM. Мы сейчас в поиске партнеров и готовы давать лучшие условия для партнеров.',
      'Могу предложить добавить Ваш аккаунт в текущие запуски. С Вами изначально связывалась нейросеть, могу на зуме сделать демонстрацию CRM.',
      'Цена в среднем 120, срок разработки - 1 месяц. Да сайты делаем на тильде.',
      'Как и обещал – высылаю коммерческое предложение. Примеры наших сайтов: iv2a.ru',
      'Видел похожую тему. Напишите @sibresult, думаю он ответит.',
    )
    for text in bad:
        assert not reactivation_quality_ok('',text), text


def test_decline_reason_classifier_and_report_are_owner_visible():
    from telegram_sales_replies import classify_decline_reason
    from pathlib import Path
    assert classify_decline_reason('Посмотрел видео, такое неинтересно. Недостаточно проработано')=='качество демонстрации/материалов'
    assert classify_decline_reason('Для нас слишком дорого и не окупается')=='цена/экономика'
    assert classify_decline_reason('Уже работаем с другим авитологом')=='уже есть подрядчик'
    assert classify_decline_reason('Пока не актуально')=='неактуально/не вовремя'
    assert 'Причина отказа:' in Path('telegram_sales_replies.py').read_text()
    assert 'Причины отказов:' in Path('telegram_sales_daily_report.py').read_text()


def test_daily_report_audit_requires_send_exec():
    from pathlib import Path
    src=Path('/root/BORIS/backend/telegram_sales_production_audit.py').read_text()
    assert "daily_report_service_sends" in src
    assert "telegram_sales_daily_report.py' in report_exec" in src
    assert "'--send' in report_exec" in src


def test_low_activity_source_cadence_is_fail_open():
    from pathlib import Path
    src=Path('/root/BORIS/backend/app/monitoring/store.py').read_text()
    assert 'TG_SOURCE_LOW_ACTIVITY_CADENCE_MIN' in src
    assert 'COALESCE(t.scans,0)<5' in src
    assert 'COALESCE(ms.weight,10)>=17' in src
    assert 'return load_sources(cur, platform)' in src


def test_source_cadence_has_starvation_audit():
    from pathlib import Path
    src=Path('/root/BORIS/backend/telegram_sales_production_audit.py').read_text()
    assert "source_cadence_not_starved" in src
    assert "source_fast_stale" in src and "25 minutes" in src
    assert "source_slow_stale" in src and "60 minutes" in src


def test_morning_late_events_deduplicate_by_lead():
    from pathlib import Path
    src=Path('/root/BORIS/backend/telegram_sales_morning_preflight.py').read_text()
    assert 'row_number() over(partition by r.hot_lead_id' in src
    assert 'count(*) over(partition by r.hot_lead_id) as reply_count' in src
    assert "where r.rn=1" in src


def test_reactivation_hypothesis_draft_is_stage_based():
    from telegram_contact_reactivation import draft
    text=draft('Роман екб строительство авито','Ниша: строительство домов. Гео - Екатеринбург. Задача: сгенерировать перечень гипотез, оценить перспективы продвижения на авито: цена лида, рекламный бюджет. Интересно предложение с разбивкой на этапы с поэтапной оплатой',['авито'])
    assert 'поэтапно проверить гипотезу' in text
    assert 'Цифры до анализа выдумывать не буду' in text
    assert 'CPL/бюджету' in text


def test_source_cadence_snapshot_is_persisted():
    from pathlib import Path
    runner=Path('/root/BORIS/backend/monitor_runner.py').read_text()
    audit=Path('/root/BORIS/backend/telegram_sales_production_audit.py').read_text()
    assert "telegram_source_cadence_last" in runner
    assert "deferred_sources" in runner and "selected_sources" in runner
    assert "source_cadence_last" in audit


def test_source_telemetry_hard_check_uses_current_errors():
    from pathlib import Path
    src=Path('/root/BORIS/backend/telegram_sales_production_audit.py').read_text()
    assert "source_telemetry_errors_historical" in src
    assert "where last_error is not null" in src
    assert "source_telemetry_errors']==0" in src


def test_telegram_sales_auto_templates_do_not_push_video_links():
    from pathlib import Path
    for name in ('telegram_contact_reactivation.py','telegram_sales_replies.py','hot_lead_auto_sender.py'):
        src=Path('/root/BORIS/backend/'+name).read_text().lower()
        assert 'youtu.be/' not in src
        assert 'youtube.com/' not in src
        assert 'rutube.ru/video/' not in src


def test_dead_username_valueerror_is_permanent_and_does_not_degrade_cycle():
    from hot_lead_auto_sender import permanent_tg_error
    assert permanent_tg_error('ValueError: No user has "vamdomkazan" as username')
    assert permanent_tg_error('UsernameNotOccupiedError: nobody')
    assert not permanent_tg_error('RpcCallFailError: Telegram is having internal issues')


def test_mikhail_proof_request_does_not_restart_avito_qualification():
    from telegram_sales_replies import safe_sales_auto_reply
    policy,reply=safe_sales_auto_reply('Пришлите ссылку на похожий аккаунт в авито, который вы ведёте.')
    assert policy=='proof_account_request'
    assert reply and '4 дня' in reply
    assert 'какая ниша' not in reply.lower()


def test_mikhail_qualification_answer_is_consumed_not_asked_again():
    from telegram_sales_replies import safe_sales_auto_reply
    policy,reply=safe_sales_auto_reply('Откатные ворота, мск и мо, 0')
    assert policy=='qualification_answer'
    assert reply and 'повторно спрашивать не буду' in reply.lower()
    assert '4 дня' in reply


def test_mikhail_avito_price_objection_is_not_misclassified_as_avito_context():
    from telegram_sales_replies import safe_sales_auto_reply
    policy,reply=safe_sales_auto_reply('Для авито дорого. Неродходит')
    assert policy=='avito_price_objection'
    assert reply and 'авито не навязываю' in reply.lower()
    assert '4 дня' in reply
    assert 'какая ниша' not in reply.lower()


def test_context_repeat_complaint_repairs_dialogue_instead_of_qualifying_again():
    from telegram_sales_replies import safe_sales_auto_reply
    policy,reply=safe_sales_auto_reply('Тут же написано')
    assert policy=='context_repair'
    assert reply and 'повторно спрашивать' in reply.lower()
    assert '4 дня' in reply


def test_duplicate_dialogue_guard_blocks_semantic_basic_qualification_loop():
    from telegram_sales_replies import guard_duplicate_dialogue
    class Result:
        def mappings(self): return self
        def all(self):
            return [{'auto_reply_policy':'backlog_avito_context','auto_reply_text':'старый вопрос про нишу'}]
    class DB:
        def execute(self,*args,**kwargs): return Result()
    policy,reply=guard_duplicate_dialogue(DB(),16240,99999,'avito_context','новая формулировка того же вопроса')
    assert policy=='duplicate_qualification_guard'
    assert reply is None


def test_duplicate_dialogue_guard_blocks_literal_repeat():
    from telegram_sales_replies import guard_duplicate_dialogue
    text='Понял. По Авито тогда начнём с самого важного.'
    class Result:
        def mappings(self): return self
        def all(self):
            return [{'auto_reply_policy':'backlog_avito_context','auto_reply_text':text}]
    class DB:
        def execute(self,*args,**kwargs): return Result()
    policy,reply=guard_duplicate_dialogue(DB(),16240,99999,'avito_context',text)
    assert policy=='duplicate_dialogue_guard'
    assert reply is None


def test_duplicate_guard_is_wired_to_both_backlog_and_realtime_paths():
    from pathlib import Path
    src=Path('telegram_sales_replies.py').read_text()
    assert src.count('policy,auto_text=guard_duplicate_dialogue(') >= 2
    assert "send_personal_serialized(client,entity,auto_text,'backlog')" in src
    assert "send_personal_serialized(client,entity,auto_text,'live_reply')" in src


def test_initial_leadgen_respects_explicit_avito_and_yandex_exclusions_and_offers_4d_trial():
    import ast,re
    from pathlib import Path
    src=Path('hot_lead_queue.py').read_text(); tree=ast.parse(src)
    selected=[]
    for node in tree.body:
        if isinstance(node,ast.FunctionDef) and node.name in {'compact','_human_first_name','first_message_draft'}:
            selected.append(node)
        elif isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='BORIS_AVITO_OFFER' for t in node.targets):
            selected.append(node)
    mod=ast.Module(body=selected,type_ignores=[]); ast.fix_missing_locations(mod); ns={'re':re}; exec(compile(mod,'<draft>','exec'),ns)
    body=('Нажны лиды/клиенты/заявки. На откатные ворота в Москве и Московской области. '
          'Только не через сайты и не через Яндекс Директ и не через Авито. Любые другие источники трафика нужны.')
    draft=ns['first_message_draft']('Михаил Туркин',body,100)
    assert 'ваш запрос по Авито' not in draft
    assert 'Авито и Яндекс Директ не предлагаем' in draft
    assert 'Для Авито отдельно' not in draft
    assert 'AI-менеджер' in draft and 'CRM' in draft and 'AI-РОП' in draft and 'реактивац' in draft
    assert '4 дня' in draft


def test_explicit_telegram_source_addition_is_read_only_and_persists_stable_peer():
    from pathlib import Path
    src=Path('monitor_source_discovery.py').read_text()
    assert '"--source"' in src
    assert 'client.get_entity(explicit)' in src
    assert "'telegram_access_hash':str(access_hash)" in src
    assert "owner-added lead source: https://t.me/" in src
    assert 'JoinChannelRequest' not in src and 'ImportChatInviteRequest' not in src


def test_freelance_source_self_promo_developer_is_not_treated_as_buyer():
    from app.monitoring.quality import classify
    text=('Привет, меня зовут Дмитрий! Я — разработчик чат-ботов, которые помогают бизнесу. '
          'С чем могу помочь: разработка чат-ботов любой сложности под ключ, автоматизация процессов и интеграции.')
    r=classify(text,'buy_service',95)
    assert r['reject'] and not r['qualified']
    assert 'self_promo' in r['reasons']


def test_phrase_under_key_alone_is_not_buyer_demand():
    from app.monitoring.quality import classify
    r=classify('Разработка чат-ботов и сайтов под ключ. Пишите в личные сообщения.',None,80)
    assert not r['qualified']


def test_portfolio_seller_overrides_stale_buy_service_intent():
    from app.monitoring.quality import classify
    text=(
        'Привет! Ищете разработчика на запуск веб-продукта или автоматизацию процессов? '
        'Я — Паша, full-stack разработчик. Прямо сейчас работаю на компанию. '
        'Портфолио и код: https://example.test/ Нужен веб-сервис или автоматизация? Пишите.'
    )
    r=classify(text,'buy_service',95)
    assert r['reject'] and not r['qualified']
    assert 'self_promo' in r['reasons']


def test_turnkey_site_bot_seller_overrides_stale_buy_service_intent():
    from app.monitoring.quality import classify
    text='Сайты и Telegram-боты под ключ. Лендинги, магазины, интеграции с CRM. Цены ниже рынка.'
    r=classify(text,'buy_service',75)
    assert r['reject'] and not r['qualified']
    assert 'self_promo' in r['reasons']


def test_dialogue_context_honors_original_no_avito_constraint_in_full_offer():
    from telegram_sales_replies import guard_duplicate_dialogue,BORIS_FULL_VALUE_4D
    class Rows:
        def mappings(self): return self
        def all(self): return []
    class Scalar:
        def scalar(self):
            return 'Нужны лиды. Только не через Яндекс Директ и не через Авито. Любые другие источники.'
    class DB:
        def __init__(self): self.n=0
        def execute(self,*args,**kwargs):
            self.n+=1
            return Rows() if self.n==1 else Scalar()
    policy,reply=guard_duplicate_dialogue(DB(),16240,24037,'context_repair','Да, вижу. '+BORIS_FULL_VALUE_4D)
    assert policy=='context_repair'
    assert reply and 'много ручной работы на Авито' not in reply
    assert 'поиск публичного спроса' in reply
    assert '4 дня' in reply


def test_human_dialogue_guard_blocks_same_business_question_when_reworded():
    from telegram_sales_replies import _near_duplicate_text
    a='Понял. Какая ниша, город и сколько заявок сейчас получаете за неделю?'
    b='Подскажите нишу, город и примерно сколько лидов у вас за неделю?'
    assert _near_duplicate_text(a,b) is True


def test_human_dialogue_context_honors_channel_exclusions_and_does_not_requalify():
    from unittest.mock import patch
    import telegram_sales_replies as m
    snap={
        'qualification_line':'Откатные ворота, мск и мо, 0',
        'avito_excluded':True,'yandex_excluded':True,'site_excluded':True,
        'geo_known':True,'volume_known':True,'asked_slots':{'niche','geo','lead_volume'},
        'trial_offered':False,'original':'Нужны лиды. Только не через сайты, Директ и Авито.',
        'inbound':[],'outbound':[],'all_text':'',
    }
    with patch.object(m,'_dialogue_snapshot',return_value=snap):
        policy,text=m.contextual_sales_auto_reply(None,1,2,'Для авито дорого. Не подходит')
    assert policy=='channel_constraint_ack'
    assert 'Авито здесь вообще убираем' in text
    assert 'какая ниша' not in text.lower()
    assert 'город' not in text.lower()
    assert '4 дня' in text


def test_human_dialogue_context_repair_reuses_known_facts_instead_of_asking_again():
    from unittest.mock import patch
    import telegram_sales_replies as m
    snap={
        'qualification_line':'Откатные ворота, мск и мо, 0',
        'avito_excluded':True,'yandex_excluded':True,'site_excluded':True,
        'geo_known':True,'volume_known':True,'asked_slots':{'niche','geo','lead_volume'},
        'trial_offered':True,'original':'Нужны лиды.',
        'inbound':[],'outbound':['бесплатный тест BORIS на 4 дня'],'all_text':'',
    }
    with patch.object(m,'_dialogue_snapshot',return_value=snap):
        policy,text=m.contextual_sales_auto_reply(None,1,2,'Тут же написано')
    assert policy=='context_repair'
    assert 'Откатные ворота' in text and 'Москва и МО' in text and 'заявок — 0' in text
    assert 'Повторно анкету гонять не буду' in text
    assert text.count('4 дня')==0


def test_live_telegram_history_guard_blocks_literal_repeat_without_sending():
    from datetime import datetime, timezone
    from telegram_sales_replies import live_outbound_duplicate
    class Msg:
        out=True
        date=datetime.now(timezone.utc)
        message='Понял. По Авито тогда начнём с самого важного: какая ниша, город и примерно сколько заявок сейчас получаете за неделю?'
    class Client:
        def iter_messages(self,entity,limit=30):
            return [Msg()]
    candidate='Понял. По Авито тогда начнём с самого важного: какая ниша, город и примерно сколько заявок сейчас получаете за неделю?'
    assert live_outbound_duplicate(Client(),object(),candidate)=='same_outbound_or_question'
