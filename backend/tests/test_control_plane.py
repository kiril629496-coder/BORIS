from pathlib import Path
ROOT = Path('/root/BORIS/backend')


def _src(name): return (ROOT / name).read_text(encoding='utf-8')


def test_no_evidence_no_pass_is_hard_gate():
    s = _src('app/services/control_plane.py')
    assert 'NO_EVIDENCE_NO_PASS' in s
    assert 'ControlEvidence.level.in_(("L4", "L5", "L6"))' in s


def test_execution_flow_contains_runtime_gate():
    s = _src('app/services/control_plane.py')
    assert '"technical_tested": {"runtime_verified"' in s
    assert '"runtime_verified": {"business_verified", "pass"' in s


def test_desired_actual_reconciliation_creates_incident():
    s = _src('app/services/control_plane.py')
    assert 'CONFIG_DRIFT' in s
    assert 'desired state differs' in s.lower()


def test_owner_instruction_compiler_exists_without_paid_ai():
    s = _src('app/services/control_plane.py')
    assert 'def classify_owner_instruction' in s
    assert 'permanent_rule' in s
    assert 'cancellation' in s


def test_api_has_required_control_plane_surfaces():
    s = _src('app/api/control_plane.py')
    for token in ('/requirements', '/effective-config/{module}', '/executions/{execution_id}/evidence', '/reconcile', '/incidents', '/health'):
        assert token in s


def test_owner_nonstop_without_operator_is_persistent_rule():
    from app.services.control_plane import classify_owner_instruction
    r=classify_owner_instruction('работай нонстоп без меня чтобы я не был снова оператором и не останавливайся')
    assert r['intent']=='permanent_rule'


def test_control_profiles_cover_every_mandatory_module_without_unsafe_generic_write():
    from app.services.control_plane_adapters import CONTROL_PROFILES, get, adapter_capabilities
    mandatory={'marketing','mop','crm','telephony','messages','rop','feed_factory','social','email','telegram','analytics','direct','billing','sitebuild','reactivation'}
    assert set(CONTROL_PROFILES)==mandatory
    for module in mandatory:
        c=adapter_capabilities(get(module))
        assert c['get_state'] and c['verify'] and c['diagnose']
        assert c['external_actions_gated'] is True
        assert c['rollback_strategy']
        if c['apply']:
            assert c['rollback']
        if c['control_mode'] in {'read_verify','external_gated'}:
            assert c['apply'] is False


def test_mop_empty_reply_is_recoverable_with_same_exactly_once_draft():
    s=_src('app/mop_core.py')
    assert 'SELF_HEAL_EMPTY_REPLY_V1' in s
    assert 'status in ("draft_ready", "send_failed") and ai_enabled' in s
    assert '"send_failed": ("sending", "editing", "custom_waiting", "human_required", "analyzing", "waiting_external", "no_reply_required")' in s
    assert 'send_error = NULL' in s


def test_mop_human_handoff_has_ownerless_manager_task_recovery():
    r=_src('app/services/brain_recovery.py')
    a=_src('app/services/control_plane_adapters.py')
    p=_src('app/services/brain_action_planner.py')
    assert 'MOP_HUMAN_HANDOFF_MANAGER_TASK_V1' in r
    assert "source='mop_handoff_recovery'" in r
    assert 'MOP_HUMAN_ESCALATION_NEEDS_MANAGER_TASK' in a
    assert 'MOP_HUMAN_ESCALATION_MANAGED' in a
    assert '"ensure_manager_handoff_task": {"level":"L1"' in p


def test_durable_queue_keeps_authoritative_unread_discovery():
    s=_src('app/api/messenger.py')
    assert 'DURABLE_QUEUE_UNREAD_DISCOVERY_V2' in s
    assert '_unread = fetch_chats(account_id, unread_only=True)' in s


def test_messages_health_respects_explicit_item_whitelist_scope():
    s=_src('app/services/control_plane_adapters.py')
    assert 'scope_excluded_unanswered' in s
    assert 'actionable_unanswered_dialogs' in s
    assert 'UNANSWERED_OUTSIDE_CONFIGURED_SCOPE' in s


def test_messages_health_excludes_buyer_side_chats_from_mop_incidents():
    s=_src('app/services/control_plane_adapters.py')
    assert 'MESSAGE_SELLER_SIDE_SCOPE_PARITY_V1' in s
    assert 'buyer_side_unanswered' in s
    assert "item_owner_id" in s and "avito_user_id" in s
    assert "_our_avito_user != _item_owner" in s


def test_e2e_acceptance_is_ledger_backed_not_hardcoded_pass():
    s=_src('app/services/brain_e2e_acceptance.py')
    for token in ('OWNER_INTAKE_LINKS_REQUIREMENT','NO_EVIDENCE_NO_PASS_RUNTIME','RETRY_CHANGES_STRATEGY','MOP_EXACTLY_ONCE_IDENTITY','NO_UNSAFE_GENERIC_WRITE_SURFACE'):
        assert token in s
    a=_src('app/services/brain_acceptance.py')
    assert 'build_e2e_report' in a
    assert "'E2E_ACCEPTANCE':_status(str(e2e.get('status') or '')=='pass')" in a


def test_social_stall_has_bounded_safe_recovery():
    s = _src('app/services/brain_recovery.py')
    assert 'def _safe_social_recovery' in s
    assert "systemctl','restart','boris-background-worker.service" in s
    assert "'passed':not after['stale']" in s
    assert 'recover_social_scheduler' in s


def test_global_world_state_does_not_project_account_rules_into_every_module():
    s = _src('app/services/brain_world_state.py')
    assert 'GLOBAL_PROJECTION_SCOPE_V1' in s
    assert 'r.scope_type != "account"' in s
    assert 'SYSTEM_SCOPE_ISOLATION_V1' in s


def test_owner_money_policy_is_waiting_dependency_not_fake_internal_failure():
    a = _src('app/services/brain_acceptance.py')
    p = _src('app/services/brain_action_planner.py')
    g = _src('control_plane_guardian_runner.py')
    assert 'OWNER_POLICY_WAIT_V1' in a
    assert 'EXTERNAL_DEPENDENCY_WAIT_V1' in a
    assert 'waiting_dependencies' in a
    assert "'EXTERNAL_P1':p1_waiting_external" in a
    assert "dependency_state') or '')=='waiting_external'" in a
    assert 'MONEY_POLICY_BLOCKERS_V1' in p
    assert 'waiting_owner_policy' in g


def test_marketing_portfolio_dependency_wait_is_not_runtime_crash():
    s = _src('app/services/control_plane_adapters.py')
    assert 'PORTFOLIO_DEPENDENCY_WAIT_V1' in s
    assert 'PORTFOLIO_KPI_WAITING_DEPENDENCY' in s


def test_qa_suite_tracks_current_command_center_and_scenario_catalog():
    s = _src('qa_suite.py')
    assert 'command-center-root' in s
    assert '13 бизнес-сценариев в каталоге' in s
    assert 'scenarios-soon' not in s


def test_runtime_convergence_delegates_rolling_to_canonical_deploy_service():
    s = _src('scripts/runtime_convergence_60m_v1.py')
    assert 'DEPLOY_COORDINATOR_OWNS_ROLL_V1' in s
    assert "ROLL_DELEGATED=boris-deploy.service" in s
    assert "runtime_selfheal_v1" not in s


def test_dev_scheduler_cannot_mark_jobs_running_without_free_provider():
    s = _src('app/ext_api/dev.py')
    assert 'SCHEDULER_PROVIDER_GATE_V1' in s
    assert 'WAITING_FREE_CODING_PROVIDER' in s
    assert 'provider_gate' in s


def test_dispatcher_persists_provider_wait_state_instead_of_fake_running():
    s = _src('app/ext_api/recover.py')
    assert 'PROVIDER_WAIT_JOB_STATE_V1' in s
    assert 'provider_wait_jobs' in s


def test_dispatcher_can_self_heal_executor_systemd_units_without_interactive_auth():
    s = _src('app/ext_api/recover.py')
    assert '["sudo", "-n", "systemctl", "start", unit]' in s
    assert '["sudo", "-n", "systemctl", "stop", unit]' in s


def test_owner_disabled_mop_is_waiting_scope_not_internal_failure():
    s=_src('app/services/control_plane_adapters.py')
    assert 'MESSAGE_OWNER_SCOPE_SEMANTICS_V1' in s
    assert 'UNANSWERED_OWNER_DISABLED_MOP' in s
    assert 'MOP_BINDING_MISSING_FOR_ENABLED_ACCOUNT' in s
    assert 'CRM_OWNER_DISABLED_SOURCE_WAIT_V1' in s
    assert 'CRM_NEXT_ACTION_WAITING_OWNER_DISABLED_MOP' in s
