import hashlib
import inspect
import os
import unittest

from app.services import prospect_campaigns as prospect_service
from app.services.prospect_campaigns import (
    OWNER_OUTREACH_APPROVED_BODY,
    _sender_signature,
    _copy_version,
    _brand_icon_attachment,
    _html_email,
    _owner_outreach_contract,
    validate_owner_outreach_copy_set,
)
from app.services.email_queue import is_permanent, retry_delay


class ProspectOwnerOutreachContractTest(unittest.TestCase):
    def setUp(self):
        self.prev_banner = os.environ.get("PROSPECT_EMAIL_BANNER_MODE")
        self.prev_phone = os.environ.get("PROSPECT_SENDER_PHONE")
        self.prev_icon = os.environ.get("PROSPECT_EMAIL_BRAND_ICON_PATH")
        self.prev_sha = os.environ.get("PROSPECT_EMAIL_BRAND_ICON_SHA256")
        os.environ["PROSPECT_EMAIL_BANNER_MODE"] = "off"
        os.environ["PROSPECT_SENDER_PHONE"] = "8 981 967-37-87"

    def tearDown(self):
        for key, previous in (
            ("PROSPECT_EMAIL_BANNER_MODE", self.prev_banner),
            ("PROSPECT_SENDER_PHONE", self.prev_phone),
            ("PROSPECT_EMAIL_BRAND_ICON_PATH", self.prev_icon),
            ("PROSPECT_EMAIL_BRAND_ICON_SHA256", self.prev_sha),
        ):
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous

    def _good_body(self):
        return OWNER_OUTREACH_APPROVED_BODY

    def _good_shape(self, label="A"):
        variant=next(x for x in prospect_service.OWNER_OUTREACH_COPY_VARIANTS if x["label"]==label)
        slot=prospect_service._owner_banner_slot_for_label(label)
        attachment,cid,_=prospect_service._owner_outreach_banner(slot)
        self.assertIsNotNone(attachment)
        html=_html_email(
            variant["body"], "", "",
            brand_subtitle="BORIS AI", brand_note="",
            marketing_banner_cid=cid,
        )
        return variant,html,[attachment]

    def test_owner_approved_body_is_exact_agreed_revision(self):
        variants=prospect_service.OWNER_OUTREACH_COPY_VARIANTS
        self.assertEqual([x["label"] for x in variants], ["A","B","C","D","E","F"])
        self.assertEqual(len(variants),6)
        self.assertEqual(OWNER_OUTREACH_APPROVED_BODY, variants[0]["body"])
        for v in variants:
            lines=[x.strip() for x in v["body"].splitlines() if x.strip()]
            self.assertEqual(lines[-1], "https://boris-ai.pro/go/boris")
            self.assertEqual(v["body"].count("https://boris-ai.pro/go/boris"),1)
            self.assertNotIn("https://boris-ai.pro/software-dev/",v["body"])
            self.assertLessEqual(len(v["subject"]),60)
        self.assertNotIn("Я создал BORIS — виртуальную команду маркетинга и продаж.", OWNER_OUTREACH_APPROVED_BODY)

    def test_signature_has_no_url(self):
        sig = _sender_signature(campaign_id=9, member_id=1)
        low = sig.lower()
        self.assertNotIn("http://", low)
        self.assertNotIn("https://", low)
        self.assertNotIn("boris-ai.pro", low)
        self.assertIn("MAX / WhatsApp: 8 981 967-37-87", sig)
        self.assertIn("сопровождение первого месяца бесплатно", low)

    def test_html_without_banner_has_no_img_or_domain(self):
        html = _html_email(self._good_body(), "")
        low = html.lower()
        self.assertNotIn("<img ", low)
        self.assertIn('<a href="https://boris-ai.pro/go/boris"', html)

    def test_owner_contract_accepts_approved_shape(self):
        variant,html,attachments=self._good_shape("A")
        self.assertEqual(_owner_outreach_contract(variant["subject"],variant["body"],html,attachments), [])

    def test_salesy_mechanic_is_rejected_in_subject(self):
        body = self._good_body()
        html = _html_email(body, "")
        errors = _owner_outreach_contract(
            "Сбор базы контактов + email-рассылка силами BORIS",
            body,
            html,
            [],
        )
        self.assertTrue(any(x.startswith("subject_forbidden:") for x in errors))
        self.assertIn("subject_forbidden_symbol:+", errors)

    def test_owner_subject_company_variant_falls_back_when_company_missing(self):
        subject = prospect_service._render_subject(
            "Идея для {company}||Вопрос по привлечению клиентов",
            {"company": "", "city": "", "website": "", "niche": "B2B"},
            1,
        )
        self.assertEqual(subject, "Вопрос по привлечению клиентов")

    def test_subject_choice_is_stable_but_varied_across_recipients(self):
        template = (
            "Сколько часов команда теряет на рутине продаж?"
            "||Что дешевле: ещё 2 сотрудника или автоматизация?"
            "||Сколько стоит пропущенный клиент?"
            "||Сэкономь время и деньги получи больше продаж!"
        )
        row = {"company": "Тест", "city": "Москва", "website": "", "niche": "B2B"}
        first = prospect_service._render_subject(template, row, 17)
        self.assertEqual(first, prospect_service._render_subject(template, row, 17))
        choices = {prospect_service._render_subject(template, row, i) for i in range(1, 33)}
        self.assertGreaterEqual(len(choices), 3)

    def test_any_body_drift_is_rejected(self):
        body = self._good_body() + "\n\nЛишняя строка, которой нет в согласованном письме."
        html = _html_email(body, "")
        errors = _owner_outreach_contract(
            "Вопрос по привлечению клиентов",
            body,
            html,
            [],
        )
        self.assertIn("body_not_owner_approved", errors)

    def test_copy_version_is_stable_and_changes_with_copy(self):
        v1 = _copy_version("Тема", "Текст", "base")
        v2 = _copy_version("Тема", "Текст", "base")
        v3 = _copy_version("Тема 2", "Текст", "base")
        self.assertEqual(v1, v2)
        self.assertNotEqual(v1, v3)
        self.assertTrue(v1.startswith("cp"))
        self.assertLessEqual(len(v1), 16)

    def test_owner_contract_rejects_old_copy_url_and_attachment(self):
        body = self._good_body() + "\n\nМы посмотрели вашу нишу. https://boris-ai.pro/"
        html = _html_email(body, "")
        errors = _owner_outreach_contract("BORIS", body, html, [{"path": "/tmp/wrong.png"}])
        self.assertIn("url_forbidden", errors)
        self.assertIn("body_not_owner_approved", errors)
        self.assertIn("owner_banner_attachment_invalid", errors)

    def test_owner_approved_copy_rejects_visible_brand_attachment(self):
        os.environ["PROSPECT_EMAIL_BANNER_MODE"] = "brand_icon"
        os.environ["PROSPECT_EMAIL_BRAND_ICON_PATH"] = "/root/BORIS/frontend/public/boris-icon-512.png"
        os.environ["PROSPECT_EMAIL_BRAND_ICON_SHA256"] = "041cd2ed46448f1cb99e84531cbcc2ede59299564b9aad565c1fcb64a47a4739"
        approved = _brand_icon_attachment()
        self.assertIsNotNone(approved)
        body = self._good_body()
        html = _html_email(body, approved["cid"], "BORIS AI")
        errors = _owner_outreach_contract("Вопрос по привлечению клиентов", body, html, [approved])
        self.assertIn("owner_banner_attachment_invalid", errors)

    def test_bad_brand_icon_config_still_allows_locked_text_only_copy(self):
        os.environ["PROSPECT_EMAIL_BANNER_MODE"] = "brand_icon"
        os.environ["PROSPECT_EMAIL_BRAND_ICON_PATH"] = "/root/BORIS/frontend/public/boris-icon-512.png"
        os.environ["PROSPECT_EMAIL_BRAND_ICON_SHA256"] = "0" * 64
        self.assertIsNone(_brand_icon_attachment())
        body = self._good_body()
        html = _html_email(body, "")
        errors = _owner_outreach_contract("Вопрос по привлечению клиентов", body, html, [])
        self.assertIn("owner_banner_attachment_invalid", errors)

    def test_copy_set_rejects_invalid_ab_variant(self):
        errors = validate_owner_outreach_copy_set(
            "Вопрос по привлечению клиентов",
            self._good_body(),
            [{"label":"A","subject":"Песок с доставкой","body":"Короткое письмо только про песок и цену, без описания BORIS и его модулей."}],
            campaign_id=9,
        )
        self.assertIn("ab_A:body_not_owner_approved", errors)

    def test_copy_set_accepts_base_without_ab(self):
        errors = validate_owner_outreach_copy_set(
            "Вопрос по привлечению клиентов",
            self._good_body(),
            [],
            campaign_id=9,
        )
        self.assertEqual(errors, [])

    def test_copy_set_checks_every_subject_alternative(self):
        errors = validate_owner_outreach_copy_set(
            "Вопрос по привлечению клиентов||https://boris-ai.pro",
            self._good_body(),
            [],
            campaign_id=9,
        )
        self.assertIn("base:url_forbidden", errors)

    def test_owner_six_copy_banner_rotation_is_exact(self):
        variants=prospect_service.OWNER_OUTREACH_COPY_VARIANTS
        self.assertEqual([x["label"] for x in variants],["A","B","C","D","E","F"])
        self.assertEqual(
            [prospect_service._owner_banner_slot_for_label(x["label"]) for x in variants],
            [1,2,3,4,1,2],
        )
        self.assertEqual(
            [prospect_service._owner_outreach_banner(i)[2] for i in (1,2,3,4)],
            ["competitors","routine","scale","complex"],
        )
        for v in variants:
            slot=prospect_service._owner_banner_slot_for_label(v["label"])
            att,cid,_=prospect_service._owner_outreach_banner(slot)
            html=_html_email(v["body"],"","",brand_subtitle="BORIS AI",brand_note="",marketing_banner_cid=cid)
            self.assertEqual(_owner_outreach_contract(v["subject"],v["body"],html,[att]),[])
            self.assertGreaterEqual(html.count('<a href="https://boris-ai.pro/go/boris"'),2)

    def test_owner_outreach_never_uses_paid_ai_copy(self):
        src = inspect.getsource(prospect_service.tick_campaign)
        self.assertIn("and not ab and not is_owner", src)

    def test_owner_contract_mismatch_fails_closed_without_persistent_pause(self):
        src = inspect.getsource(prospect_service.tick_campaign)
        start = src.index("if str(c.get('account_id') or '') == '__owner_outreach__':")
        end = src.index("if is_development:", start)
        owner_guard = src[start:end]
        self.assertIn("content_contract_blocked_retrying", owner_guard)
        self.assertIn("changed_delivery", owner_guard)
        self.assertIn("next scheduler tick retries", owner_guard)
        self.assertNotIn("SET status='paused'", owner_guard)

    def test_owner_ramp_is_10_then_15_then_20(self):
        from app.services import owner_outreach_policy as policy
        self.assertEqual(prospect_service.OWNER_OUTREACH_MAX_DAILY, 20)
        self.assertEqual(
            [policy.canonical_daily_cap(x) for x in range(8)],
            [10, 10, 10, 10, 10, 10, 15, 20],
        )
        src = inspect.getsource(prospect_service._owner_daily_state)
        self.assertIn("canonical_policy_state(age)", src)
        self.assertIn("computed_cap=min(configured_cap,canonical_cap)", src)
        self.assertIn('policy_state["next_cap"]', src)
        self.assertNotIn("next_cap=min(configured_cap,30)", src)

    def test_owner_campaign_limit_self_heals_and_night_gate_is_before_enqueue(self):
        floor = inspect.getsource(prospect_service._enforce_owner_campaign_limit)
        self.assertIn("global_cap=OWNER_OUTREACH_MAX_DAILY", floor)
        self.assertIn("safe=min(current,global_cap)", floor)
        self.assertIn("UPDATE prospect_campaigns SET daily_limit=:safe", floor)
        self.assertNotIn("PROSPECT_OWNER_DAILY_CAP", floor)
        create = inspect.getsource(prospect_service.create_campaign)
        self.assertIn("owner_cap=OWNER_OUTREACH_MAX_DAILY", create)
        self.assertNotIn("PROSPECT_OWNER_DAILY_CAP", create)
        tick = inspect.getsource(prospect_service.tick_campaign)
        self.assertIn("_enforce_owner_campaign_limit(db,c)", tick)
        allowance = inspect.getsource(prospect_service._owner_allowance)
        self.assertIn("return 0,'outside_window'", allowance)

    def test_owner_cap_schema_guard_is_detected_and_self_healed(self):
        src = inspect.getsource(prospect_service.ensure_schema)
        self.assertIn("prospect_campaigns_owner_outreach_daily_limit_max20", src)
        self.assertIn("convalidated", src)
        self.assertIn("pg_get_constraintdef(oid)", src)
        self.assertIn("DROP CONSTRAINT IF EXISTS prospect_campaigns_owner_outreach_daily_limit_max30", src)
        self.assertIn("SET daily_limit=LEAST(daily_limit,:cap),updated_at=NOW()", src)
        self.assertIn("daily_limit>:cap", src)
        self.assertIn("ADD CONSTRAINT prospect_campaigns_owner_outreach_daily_limit_max20", src)
        self.assertIn("DROP CONSTRAINT IF EXISTS prospect_campaigns_owner_outreach_daily_limit_min30", src)
        self.assertIn("VALIDATE CONSTRAINT prospect_campaigns_owner_outreach_daily_limit_max20", src)
        self.assertIn("prospect_campaigns_owner_body_exact_v1", src)
        self.assertIn("trg_owner_outreach_queue_copy_guard_v1", src)
        self.assertIn("approved_variants", src)
        self.assertIn("_ensure_owner_copy_db_locks(db)", src)
        db_lock_src = inspect.getsource(prospect_service._ensure_owner_copy_db_locks)
        self.assertIn("OWNER_OUTREACH_BODY_LOCK_V2", db_lock_src)
        self.assertIn("OWNER_OUTREACH_LINK_LOCK_V2", db_lock_src)
        self.assertIn("OWNER_OUTREACH_SUBJECT_LOCK_V1", db_lock_src)
        self.assertIn("OWNER_OUTREACH_ATTACHMENTS_LOCK_V1", db_lock_src)
        self.assertIn("UPDATE OF subject,text_body,html_body,attachments,ref_type,ref_id", db_lock_src)
        self.assertIn("ab_variants=CAST(:variants AS jsonb)", db_lock_src)
        self.assertNotIn("ADD CONSTRAINT prospect_campaigns_owner_outreach_daily_limit_max30", src)
        self.assertNotIn("ADD CONSTRAINT prospect_campaigns_owner_outreach_daily_limit_min30", src)

    def test_smtp_auth_error_is_retryable_with_slow_backoff(self):
        self.assertFalse(is_permanent("SMTPAuthenticationError:525"))
        self.assertGreaterEqual(retry_delay("SMTPAuthenticationError:525", 1), 900)

    def test_daily_quota_counts_only_queued_or_sent_members(self):
        src = inspect.getsource(prospect_service._owner_daily_state)
        self.assertIn("m.status IN ('queued','sent')", src)
        self.assertIn("attempted=int", src)
        self.assertIn("attempt_cap", src)
        self.assertIn("attempt_remaining", src)

    def test_campaign_and_domain_caps_release_permanent_failures(self):
        src = inspect.getsource(prospect_service.tick_campaign)
        self.assertIn("status IN ('queued','sent')", src)
        self.assertIn("x.status IN ('queued','sent')", src)
        self.assertNotIn("WHERE campaign_id=:c AND timezone(:tz,queued_at)::date=:d\"),", src)

    def test_attempt_cap_prevents_bad_base_runaway(self):
        src = inspect.getsource(prospect_service._owner_allowance)
        self.assertIn("owner_attempt_limit", src)
        self.assertIn("attempt_remaining", src)

    def test_owner_daily_health_detects_stored_ceiling_drift(self):
        src = inspect.getsource(prospect_service.owner_daily_delivery_health)
        self.assertIn("configured_cap_above_hard_ceiling", src)
        self.assertIn("stored_min_daily_limit", src)
        self.assertIn("stored_max_daily_limit", src)
        self.assertIn("ramp_policy_max=OWNER_OUTREACH_MAX_DAILY", src)
        self.assertIn("legacy_env_requested_cap", src)
        self.assertIn("legacy_env_ignored", src)
        self.assertIn("configured_max_cap=min(OWNER_OUTREACH_MAX_DAILY", src)
        self.assertIn("stored_max_daily_limit > OWNER_OUTREACH_MAX_DAILY", src)

    def test_owner_daily_health_tracks_real_sent_plan_fact_and_quality(self):
        src = inspect.getsource(prospect_service.owner_daily_delivery_health)
        self.assertIn("expected_by_now", src)
        self.assertIn("'successful':successful", src)
        self.assertIn("'quota_used':used", src)
        self.assertIn("'queued_now':queued_now", src)
        self.assertIn("quality_versioned_sent", src)
        self.assertIn("quality_untracked_sent", src)
        self.assertIn("quality_unversioned_after_enforcement", src)
        self.assertIn("content_version_untracked", src)
        self.assertIn("PROSPECT_COPY_VERSION_ENFORCED_AT", src)
        self.assertIn("PROSPECT_DAILY_HEALTH_GRACE_MINUTES", src)
        self.assertIn("'can_self_heal':can_self_heal", src)
        self.assertIn("'owner_action_required':bool(state=='critical' and status not in {'daily_cap_exceeded','window_closed_shortfall'})", src)

    def test_owner_daily_health_respects_shared_mailbox_cap(self):
        src = inspect.getsource(prospect_service.owner_daily_delivery_health)
        self.assertIn("shared_cap_complete", src)
        self.assertIn("shared_cap_reserved", src)
        self.assertIn("shared_successful", src)
        self.assertIn("shared_queued_now", src)
        self.assertIn("remaining>0 and used<cap", src)

    def test_owner_queue_identity_includes_copy_version_and_member_link(self):
        src = inspect.getsource(prospect_service.tick_campaign)
        self.assertIn(":{copy_version}", src)
        self.assertIn("ref_type='prospect_campaign_member'", src)
        self.assertIn("ref_id=str(r['id'])", src)
        self.assertIn("_requeue_stale_owner_copy", src)

    def test_owner_stale_copy_requeues_before_send(self):
        src = inspect.getsource(prospect_service._requeue_stale_owner_copy)
        self.assertIn("COPY_VERSION_SUPERSEDED", src)
        self.assertIn("status='cancelled'", src)
        self.assertIn("status='ready'", src)
        self.assertIn("copy_version=NULL", src)

    def test_owner_copy_integrity_health_has_detect_heal_verify_contour(self):
        src = inspect.getsource(prospect_service.owner_copy_integrity_health)
        self.assertIn("ensure_schema(db)", src)
        self.assertIn("status IN ('queued','retrying')", src)
        self.assertIn("OWNER_COPY_POLICY_DRIFT", src)
        self.assertIn("pending_copy_drift", src)
        self.assertIn("pending_tracking_drift", src)
        self.assertIn("wrong_sent_after_lock", src)
        self.assertIn("historical_prelock_wrong_sent", src)
        self.assertIn("campaign_lock_ok", src)
        self.assertIn("queue_lock_ok", src)
        self.assertIn("final pre-SMTP guard", src)

    def test_email_worker_has_final_owner_content_guard_before_smtp(self):
        from app.services import email_queue as email_queue_service
        src = inspect.getsource(email_queue_service._deliver)
        guard_pos = src.index("owner_queue_pre_send_guard")
        smtp_pos = src.index("send_outbound")
        self.assertLess(guard_pos, smtp_pos)
        self.assertIn("content_guard_blocked", src)
        self.assertIn("return \"cancelled\"", src)

    def test_email_health_detects_owner_ready_reserve_before_zero(self):
        from app.services import reliability
        src = inspect.getsource(reliability.email_delivery_health)
        self.assertIn('EMAIL_READY_RESERVE_HEALTH_V1', src)
        self.assertIn("'owner_ready_reserve':owner_ready_reserve", src)
        self.assertIn("mb.account_id='__owner_outreach__'", src)
        self.assertIn("mb.id=c.mailbox_id", src)
        self.assertNotIn("c.account_id='__owner_outreach__'", src)
        self.assertIn("'critical' in reserve_states", src)
        self.assertIn("'degraded' in reserve_states", src)

    def test_email_health_includes_daily_outcome_not_only_worker_liveness(self):
        from app.services import reliability
        src = inspect.getsource(reliability.email_delivery_health)
        self.assertIn("owner_daily_delivery_health", src)
        self.assertIn("owner_copy_integrity_health", src)
        self.assertIn("'daily_outreach':daily_outreach", src)
        self.assertIn("'owner_copy_integrity':owner_copy_integrity", src)
        self.assertIn("'critical' in daily_states", src)
        self.assertIn("'critical' in copy_integrity_states", src)
        self.assertIn("'degraded' in daily_states", src)


if __name__ == "__main__":
    unittest.main()
