#!/usr/bin/env python3
"""Fail-closed source contract for the minute telephony guardian.

Read-only: imports local Python only, performs no DB/provider/network calls.
"""
from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import telephony_guardian_runner as guardian
from app.services import telephony_core as phone_core
from app.services.mcn_core import current_mcn_accounts


def _function_ast(fn):
    src = inspect.getsource(fn)
    return src, ast.parse(src)


def _sql_text_literals(fn) -> str:
    """Return only literal SQL passed to sqlalchemy.text(...) inside fn."""
    _src, tree = _function_ast(fn)
    values: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        is_text_call = (
            isinstance(func, ast.Name) and func.id == "text"
        ) or (
            isinstance(func, ast.Attribute) and func.attr == "text"
        )
        if not is_text_call:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            values.append(first.value.lower())
    return "\n".join(values)


def main() -> int:
    failures: list[str] = []

    _run_src, run_tree = _function_ast(guardian.run_guardian_once)
    calls = {
        node.func.id
        for node in ast.walk(run_tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    if "real_mcn_acceptance_watch_once" not in calls:
        failures.append("mcn_acceptance_watcher_not_wired")
    if "mcn_mailbox_autoonboard_once" not in calls:
        failures.append("mcn_mailbox_autoonboard_not_wired")
    # The mailbox watcher can discover an external owner blocker (contract,
    # ambiguous credentials, provider conflict) even before a real phone
    # account exists. The minute guardian must promote that signal to its
    # top-level health; otherwise monitoring can look green while BORIS is
    # actually waiting on the owner.
    for required in (
        'mailbox_result.get("owner_action_required")',
        'result["owner_action_codes"]',
        'result["owner_action_required"] = True',
    ):
        if required not in _run_src:
            failures.append(f"mcn_mailbox_owner_action_propagation_missing:{required}")

    mailbox_auto_src = inspect.getsource(guardian.mcn_mailbox_autoonboard_once)
    for required in (
        "current_mcn_accounts",
        "_single_active_phone_account",
        "_single_active_imap_mailbox",
        "_mailbox_autoonboard_recent",
        "phone-mcn-mailbox-onboard.py",
        "--apply",
        "timeout=45",
    ):
        if required not in mailbox_auto_src:
            failures.append(f"mcn_mailbox_autoonboard_contract_missing:{required}")

    draft_prepare_src = inspect.getsource(guardian._prepare_mcn_company_card_draft)
    if "--save-draft" not in draft_prepare_src:
        failures.append("mcn_company_card_guardian_not_draft_only")
    if '"--apply"' in draft_prepare_src or "'--apply'" in draft_prepare_src:
        failures.append("mcn_company_card_guardian_must_not_send")

    company_card_src = (
        ROOT / "scripts" / "phone-mcn-company-card-send.py"
    ).read_text(encoding="utf-8")
    for required in (
        "recipient_not_mcn_domain",
        "banking_share_confirmation_required",
        "validate_mcn_recipient",
        "company_card_draft_exists",
        "persist_draft_state",
        "DRAFT_STATE_KEY",
        "SEND_STATE_KEY",
        "claim_send_once",
        "finish_send_state",
        "delivery_ambiguous",
        "retry_blocked",
        "requisites_fingerprint",
        "X-BORIS-Card-Fingerprint",
        "prepared_card_changed_or_stale",
        "card_fingerprint",
        "--confirm-share-banking",
    ):
        if required not in company_card_src:
            failures.append(f"mcn_company_card_safety_contract_missing:{required}")
    claim_pos = company_card_src.find("claim = claim_send_once(")
    smtp_pos = company_card_src.find("ok, reason, message_id = send_outbound(", claim_pos)
    if claim_pos < 0 or smtp_pos < 0 or claim_pos > smtp_pos:
        failures.append("mcn_company_card_exactly_once_claim_not_before_smtp")

    company_card_discovery_src = (
        ROOT / "scripts" / "phone-company-card-mailbox-discovery.py"
    ).read_text(encoding="utf-8")
    for required in (
        "X-BORIS-Action-ID",
        "In-Reply-To",
        "boris_action_signal",
        "company_card_signal",
    ):
        if required not in company_card_discovery_src:
            failures.append(f"mcn_company_card_sent_discovery_contract_missing:{required}")
    guardian_card_evidence_src = inspect.getsource(guardian._mcn_company_card_sent_evidence)
    guardian_card_progress_src = inspect.getsource(guardian._mcn_company_card_progress)
    for required in (
        "boris_action_signal",
        "unverified_manual_attachment_after_request",
        "delivery_ambiguous",
    ):
        if required not in guardian_card_evidence_src:
            failures.append(f"mcn_company_card_sent_evidence_contract_missing:{required}")
    if "mcn_company_card_delivery_verify" not in guardian_card_progress_src:
        failures.append("mcn_company_card_manual_delivery_verify_contract_missing")
    mailbox_auto_src = inspect.getsource(guardian.mcn_mailbox_autoonboard_once)
    if "MCN_WAITING_REPLY_THROTTLE_NO_SLIDING_V1" not in mailbox_auto_src:
        failures.append("mcn_waiting_reply_poll_throttle_can_slide_forever")
    if "if state_changed:" not in mailbox_auto_src:
        failures.append("mcn_waiting_reply_state_change_persist_guard_missing")

    telephony_api_src = (
        ROOT / "app" / "api" / "telephony.py"
    ).read_text(encoding="utf-8")
    for required in (
        "@router.get('/mcn/onboarding')",
        "@router.post('/mcn/company-card/send')",
        "_require_private_platform_owner",
        "is_private_platform_owner",
        "confirm_share_banking",
        "expected_action_code",
        "expected_provider_reply_date",
        "expected_card_fingerprint",
        "_run_mcn_company_card_send",
        "mcn.company_card.owner_approved",
        "mcn.company_card.send",
    ):
        if required not in telephony_api_src:
            failures.append(f"mcn_company_card_owner_api_contract_missing:{required}")

    # BORIS Phone commercial entitlement is a separate paid product.
    # Guard this architecture every minute: Inbox/account_slots must never grant
    # Phone, and all automatic consumers must use the canonical entitlement.
    active_entitlement_src = inspect.getsource(phone_core.active_phone_entitlements)
    if "telephony_entitlements" not in active_entitlement_src:
        failures.append("phone_entitlement_canonical_table_missing")
    active_entitlement_sql = _sql_text_literals(phone_core.active_phone_entitlements)
    if "from account_slots" in active_entitlement_sql or "join account_slots" in active_entitlement_sql:
        failures.append("phone_entitlement_legacy_account_slots_fallback_forbidden")

    entitlement_status_src = inspect.getsource(phone_core.phone_entitlement_status)
    if "telephony_entitlements" not in entitlement_status_src:
        failures.append("phone_entitlement_status_canonical_table_missing")
    entitlement_status_sql = _sql_text_literals(phone_core.phone_entitlement_status)
    if "from account_slots" in entitlement_status_sql or "join account_slots" in entitlement_status_sql:
        failures.append("phone_entitlement_status_account_slots_forbidden")

    entitlement_provision_src = inspect.getsource(phone_core.provision_paid_phone_entitlement)
    for required in (
        "commercial_reference_required",
        "price_required",
        "zero_price_confirmation_required",
        "would_shorten_paid_period",
        "pg_advisory_xact_lock",
        "idempotent_replay",
    ):
        if required not in entitlement_provision_src:
            failures.append(f"phone_entitlement_commercial_guard_missing:{required}")

    guardian_phone_target_src = inspect.getsource(guardian._single_active_phone_account)
    if "active_phone_entitlements" not in guardian_phone_target_src:
        failures.append("guardian_phone_target_not_using_entitlement")
    if "account_slots" in guardian_phone_target_src:
        failures.append("guardian_phone_target_account_slots_forbidden")

    mailbox_onboard_src = (
        ROOT / "scripts" / "phone-mcn-mailbox-onboard.py"
    ).read_text(encoding="utf-8")
    for required in (
        "active_phone_entitlements",
        "phone_entitlement_inactive",
        "single_active_phone_entitlement",
    ):
        if required not in mailbox_onboard_src:
            failures.append(f"mcn_mailbox_phone_entitlement_contract_missing:{required}")
    if "account_slots" in mailbox_onboard_src:
        failures.append("mcn_mailbox_account_slots_phone_fallback_forbidden")

    for required in (
        "@router.get('/platform/phone-entitlement')",
        "@router.post('/platform/phone-entitlement/activate')",
        "@router.post('/platform/phone-entitlement/revoke')",
        "confirm_paid_phone",
        "confirm_zero_price_phone",
        "commercial_ref",
        "price_rub",
        "_require_private_platform_owner",
    ):
        if required not in telephony_api_src:
            failures.append(f"phone_entitlement_owner_api_contract_missing:{required}")

    payments_src = (
        ROOT / "app" / "api" / "payments.py"
    ).read_text(encoding="utf-8")
    for required in (
        "def _grant_phone_subscription_from_payment_ledger",
        "provision_paid_phone_entitlement(",
        "p.get(\"unit\") == \"phone_subscription\"",
        "phone_payment_ledger_missing",
        "BORIS_PHONE_MONTHLY_PRICE_RUB",
        "_configured_phone_monthly_package",
    ):
        if required not in payments_src:
            failures.append(f"phone_payment_entitlement_bridge_missing:{required}")
    if 'PACKAGES["phone_monthly"]' in payments_src and "if _PHONE_MONTHLY_PACKAGE:" not in payments_src:
        failures.append("phone_monthly_price_must_not_be_invented")

    sig = inspect.signature(guardian.real_mcn_acceptance_watch_once)
    param = sig.parameters.get("include_synthetic")
    if param is None or param.default is not False:
        failures.append("synthetic_default_not_fail_closed")

    watcher_src, watcher_tree = _function_ast(guardian.real_mcn_acceptance_watch_once)
    watcher_calls = {
        node.func.id
        for node in ast.walk(watcher_tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    if "_real_mcn_accounts" not in watcher_calls:
        failures.append("mcn_discovery_missing")
    if "_retire_stale_mcn_acceptance" not in watcher_calls:
        failures.append("stale_mcn_runtime_retirement_missing")
    if "phone_client_readiness" not in watcher_calls:
        failures.append("real_readiness_projection_missing")
    if "reconcile_phone_manager_action" not in watcher_calls:
        failures.append("manager_action_reconcile_missing")
    if "_retire_stale_mcn_manager_actions" not in watcher_calls:
        failures.append("stale_manager_action_retirement_missing")

    manager_reconcile_src = inspect.getsource(guardian.reconcile_phone_manager_action)
    for required in (
        "telephony_manager_actions",
        "pg_advisory_xact_lock",
        "assigned_user_id",
        "owner_action_required",
    ):
        if required not in manager_reconcile_src:
            failures.append(f"manager_action_contract_missing:{required}")

    manager_retire_src = inspect.getsource(guardian._retire_stale_mcn_manager_actions)
    if "retire_phone_manager_action" not in manager_retire_src:
        failures.append("manager_action_retire_helper_not_wired")

    autonomy_src = inspect.getsource(guardian.telephony_autonomy_guardian)
    for required in (
        "overdue_manager_actions",
        "phone_manager_action_guardian",
        "manager_actions",
    ):
        if required not in autonomy_src:
            failures.append(f"manager_action_autonomy_missing:{required}")

    overdue_src = inspect.getsource(phone_core.phone_manager_action_guardian)
    for required in (
        "phone_client_readiness",
        "reconcile_phone_manager_action",
        "append_notification",
        "telephony_manager_action_overdue",
        "owner_action_code",
    ):
        if required not in overdue_src:
            failures.append(f"manager_action_overdue_contract_missing:{required}")

    discovery_wrapper_src = inspect.getsource(guardian._real_mcn_accounts)
    if "current_mcn_accounts" not in discovery_wrapper_src:
        failures.append("guardian_not_using_canonical_mcn_discovery")

    canonical_sig = inspect.signature(current_mcn_accounts)
    canonical_param = canonical_sig.parameters.get("include_synthetic")
    if canonical_param is None or canonical_param.default is not False:
        failures.append("canonical_mcn_discovery_synthetic_default_not_fail_closed")

    canonical_discovery_src = inspect.getsource(current_mcn_accounts)
    for required in (
        "telephony_provider_configs",
        "telephony_trunks",
        "telephony_dids",
        "p.account_id IS NULL",
        "include_synthetic",
    ):
        if required not in canonical_discovery_src:
            failures.append(f"canonical_discovery_contract_missing:{required}")

    e2e_src = (ROOT / "scripts" / "phone-real-e2e-check.py").read_text(encoding="utf-8")
    if "from app.services.mcn_core import current_mcn_accounts" not in e2e_src:
        failures.append("e2e_checker_missing_canonical_mcn_import")
    if "return current_mcn_accounts(include_synthetic=False)" not in e2e_src:
        failures.append("e2e_checker_not_using_canonical_mcn_discovery")
    if "FROM telephony_trunks" in e2e_src or "FROM telephony_provider_configs" in e2e_src:
        failures.append("e2e_checker_contains_duplicate_mcn_discovery_sql")

    retire_src = inspect.getsource(guardian._retire_stale_mcn_acceptance)
    for required in ("status", "retired", "_MCN_ACCEPTANCE_STORAGE_KEY"):
        if required not in retire_src:
            failures.append(f"retirement_contract_missing:{required}")

    forbidden_tokens = (
        "make_call(",
        "voice_agent_start(",
        "start_outbound",
        "create_test_call",
        "originate(",
        "_queue_device_push(",
        "send_telegram_message(",
    )
    combined = (
        watcher_src
        + "\n" + discovery_wrapper_src
        + "\n" + canonical_discovery_src
        + "\n" + e2e_src
        + "\n" + retire_src
        + "\n" + manager_reconcile_src
        + "\n" + manager_retire_src
        + "\n" + autonomy_src
        + "\n" + overdue_src
    )
    for token in forbidden_tokens:
        if token in combined:
            failures.append(f"forbidden_call_boundary:{token}")

    if failures:
        print("TELEPHONY_GUARDIAN_CONTRACT=FAIL " + ",".join(sorted(set(failures))))
        return 3
    print("TELEPHONY_GUARDIAN_CONTRACT=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
