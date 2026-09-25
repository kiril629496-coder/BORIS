# -*- coding: utf-8 -*-
import inspect

from app import reactivation_runtime as rr
from app import reactivation_scan as rs
from app import reactivation_draft as rd


class _One:
    def __init__(self, value=True):
        self.value = value
    def fetchone(self):
        return (1,) if self.value else None


class _DupDB:
    def __init__(self, value=True):
        self.value = value
    def execute(self, *args, **kwargs):
        return _One(self.value)


def test_exact_duplicate_guard_detects_previous_outgoing():
    assert rd.is_duplicate_outgoing(_DupDB(True), "a", "c", "Повтор текста") is True
    assert rd.is_duplicate_outgoing(_DupDB(False), "a", "c", "Новый текст") is False


def test_current_mop_qualification_can_forbid_auto_reactivation():
    assert rr._qualification_block_from_dict({"reactivation_candidate": False}) == "qualification_reactivation_disabled"
    assert rr._qualification_block_from_dict({"human_handoff": True}) == "human_handoff_pending"
    assert rr._qualification_block_from_dict({
        "phone_received": True,
        "qualification_stage": "TARGET_ACTION",
        "target_action": "передать специалисту на звонок",
    }) == "target_action_pending"
    assert rr._qualification_block_from_dict({"reactivation_candidate": True}) is None


def test_duplicate_is_checked_when_generating_canonical_and_fallback_drafts():
    src = inspect.getsource(rd.generate)
    assert src.count("is_duplicate_outgoing") >= 2
    assert "точный дубль предыдущего исходящего" in src


def test_preflight_and_scheduler_both_apply_last_mile_guards():
    pre = inspect.getsource(rr.preflight_send)
    proc = inspect.getsource(rr.process)
    assert "qualification_block = _auto_qualification_block_reason" in pre
    assert '"duplicate_outgoing_text"' in pre
    assert "send_qualification_blocked" in proc
    assert "send_duplicate_blocked" in proc
    assert "_duplicate_outgoing_blocked(db, aid, chat_id, final_text)" in proc


def test_future_scan_reuses_mop_qualification_instead_of_blind_no_reply():
    src = inspect.getsource(rs.scan_account)
    assert "qualification = _qualification_policy" in src
    assert 'qualification.get("skip")' in src
    assert 'qualification.get("needs_review")' in src


def test_owner_truth_includes_dynamic_auto_safety_holds():
    src = inspect.getsource(rr.ready_queue_truth)
    assert '"hold_total"' in src
    assert '"qualification_blocked"' in src
    assert '"duplicate_blocked"' in src
    assert "dynamic_holds = qualification_blocked + customer_recontact_blocked + factual_safety_blocked + duplicate_blocked" in src
    assert "auto_sendable = max(0, raw_auto - dynamic_holds)" in src


def test_autopilot_pacing_enforces_min_interval_in_preflight_and_scheduler():
    import inspect
    pre = inspect.getsource(rr.preflight_send)
    proc = inspect.getsource(rr.process)
    helper = inspect.getsource(rr._auto_min_interval_blocked)
    assert 'min_interval_minutes' in helper
    assert 'sent_at > now()' in helper
    assert 'return False, "min_interval", m' in pre
    assert 'send_waiting_interval' in proc
    assert '_auto_min_interval_blocked(db, aid, st)' in proc


def test_customer_promised_recontact_language_is_fail_closed_for_auto():
    assert rr._CLIENT_RECONTACT_RE.search('Сейчас сестра в отъезде, приедет, напишу!')
    assert rr._CLIENT_RECONTACT_RE.search('Я уточню и напишу позже')
    assert not rr._CLIENT_RECONTACT_RE.search('Напишите, пожалуйста, номер телефона')
    pre = inspect.getsource(rr.preflight_send)
    assert 'customer_promised_to_recontact' in pre
    proc = inspect.getsource(rr.process)
    assert 'send_customer_recontact_blocked' in proc


def test_factual_guard_rejects_unsupported_business_policy_claims():
    assert 'неподтверждённое условие самовывоза' in rd.verify('Самовывоза нет.', 'Можно приехать посмотреть кухню.')
    assert 'неподтверждённое условие зависимости' in rd.verify('Возможность зависит от размеров.', 'Нужны два верхних модуля.')
    assert not rd.verify('Самовывоза нет.', 'Самовывоза нет, работаем с доставкой.')
    pre = inspect.getsource(rr.preflight_send)
    assert 'draft_factual_safety' in pre
    assert 'send_factual_safety_blocked' in inspect.getsource(rr.process)


def test_owner_truth_counts_recontact_and_fact_holds():
    src = inspect.getsource(rr.ready_queue_truth)
    assert 'customer_recontact_blocked' in src
    assert 'factual_safety_blocked' in src
    assert 'dynamic_holds' in src


def test_future_scan_marks_customer_promised_return_for_review():
    src = inspect.getsource(rs.prefilter)
    assert 'CLIENT_RECONTACT_RE' in src
    assert 'customer_promised_to_recontact' in src


def test_generation_wait_states_are_not_reported_as_internal_blocks():
    src = inspect.getsource(rr.process)
    assert 'generation_waiting_mop' in src
    assert 'generation_waiting_transport' in src
    assert 'generation_safety_blocked' in src
    assert 'generation_reason == "mop_inactive"' in src
    assert 'generation_reason in ("transport_unavailable", "transport_preflight_failed")' in src


def test_generation_blocked_reasons_are_observable():
    src = inspect.getsource(rr.process)
    assert 'generation_blocked_reasons' in src
    assert 'generation_reason or "unknown"' in src
