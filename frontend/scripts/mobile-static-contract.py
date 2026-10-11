#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fast source-level guard for BORIS mobile web invariants.

This runs before Next.js build. It does not contact APIs and cannot mutate
production. Browser audits remain the runtime proof; this guard prevents known
regressions from reaching an artifact in the first place.
"""
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
FILES={
    "globals": ROOT/"app/globals.css",
    "shell": ROOT/"app/components/shell/shell.module.css",
    "dock": ROOT/"app/components/shell/MobileDock.tsx",
    "campaign": ROOT/"app/dashboard/campaign/[id]/page.tsx",
    "campaign_home": ROOT/"app/dashboard/campaign/page.tsx",
    "messages": ROOT/"app/messages/page.tsx",
    "command": ROOT/"app/dashboard/command-center/page.tsx",
    "command_css": ROOT/"app/dashboard/command-center/page.module.css",
    "scenario_detail": ROOT/"app/dashboard/scenarios/[id]/page.tsx",
    "scenario_catalog": ROOT/"app/dashboard/scenarios/page.tsx",
    "scenario_guide": ROOT/"app/components/ScenarioGuide.tsx",
    "clients_control": ROOT/"app/dashboard/clients-control/page.tsx",
    "development": ROOT/"app/dashboard/development/page.tsx",
    "account_context": ROOT/"app/lib/AccountContext.tsx",
    "acquisition": ROOT/"app/dashboard/prospecting/acquisition/page.tsx",
    "prospecting_services": ROOT/"app/dashboard/prospecting/services/page.tsx",
    "crm_api": ROOT/"app/lib/crmApi.ts",
    "crm_contact_detail": ROOT/"app/dashboard/crm/contact/[id]/page.tsx",
    "crm_deal_detail": ROOT/"app/dashboard/crm/deal/[id]/page.tsx",
}
text={k:p.read_text(encoding="utf-8") for k,p in FILES.items()}
checks=[]

def ck(name, ok):
    checks.append((name,bool(ok)))

ck("mobile_interaction_contract", "BORIS_MOBILE_INTERACTION_CONTRACT_V1_BEGIN" in text["globals"])
ck("company_mobile_grid", "BORIS_MOBILE_READABILITY_AND_COMPANY_GRID_V1_BEGIN" in text["globals"])
ck("billing_mobile_touch", "BORIS_BILLING_MOBILE_TOUCH_FINAL_V1_BEGIN" in text["globals"])
ck("contextual_assistant_dock_safe", "BORIS_CONTEXTUAL_ASSISTANT_MOBILE_DOCK_SAFE_V1_BEGIN" in text["globals"])
ck("campaign_mobile_readability", "BORIS_CAMPAIGN_BILLING_COMPANY_MOBILE_READABILITY_V2_BEGIN" in text["globals"])
ck("topbar_touch_targets", "MOBILE_TOUCH_TARGETS_V2" in text["shell"] and "min-width: 44px" in text["shell"])
ck("mobile_dock_exists", 'aria-label="Быстрая навигация"' in text["dock"] and 'label: "Объявления"' in text["dock"])
ck("campaign_scope", 'data-boris-area="campaign"' in (ROOT/"app/dashboard/campaign/layout.tsx").read_text(encoding="utf-8"))
ck("campaign_category_picker", 'data-testid="campaign-category-picker"' in text["campaign"])
ck("campaign_intake_fullscreen", "boris-intake-dialog" in text["campaign"] and "height:100dvh!important" in text["globals"])
ck("campaign_quantity_mobile", 'data-testid="campaign-quantity-row"' in text["campaign"])
ck("campaign_busy_release", 'BORIS_CAMPAIGN_START_BUSY_CLEAR_V1' in text["campaign"])
ck("messages_mobile_workspace", 'data-testid="messages-workspace"' in text["messages"] and 'data-mobile-view' in text["messages"])
ck("messages_thread_back", 'data-testid="message-thread-back"' in text["messages"])
ck("messages_refresh_accessible", 'aria-label="Обновить аккаунты"' in text["messages"])
for tid in ("command-center-root","command-center-status","command-center-attention","command-center-today","command-center-journal","command-center-quick"):
    ck("command_"+tid, f'data-testid="{tid}"' in text["command"])
ck("command_mobile_readability", "BORIS_COMMAND_CENTER_MOBILE_READABILITY_V2_BEGIN" in text["command_css"])
ck("command_action_touch", "BORIS_COMMAND_CENTER_MOBILE_PRIMARY_ACTION_V1_BEGIN" in text["command_css"])
ck("campaign_prompt_mobile_font", '[data-boris-area="campaign"] .boris-campaign-prompt-rules small' in text["globals"])
ck("billing_invoice_mobile_44", '[data-boris-area="billing"] .boris-billing-invoice-box .boris-ui-button' in text["globals"])
ck("command_mobile_touch_final", "BORIS_COMMAND_MOBILE_TOUCH_FINAL_V1_BEGIN" in text["globals"])
ck("journal_has_shell", "AppShell" in (ROOT/"app/dashboard/development/page.tsx").read_text(encoding="utf-8"))
ck("reliability_has_shell", "AppShell" in (ROOT/"app/dashboard/reliability/page.tsx").read_text(encoding="utf-8"))
ck("prospecting_manage_has_shell", "AppShell" in (ROOT/"app/dashboard/prospecting/manage/page.tsx").read_text(encoding="utf-8"))
ck("prospecting_menu_goes_to_manage", 'href: "/dashboard/prospecting/manage"' in (ROOT/"app/ui/menu.ts").read_text(encoding="utf-8"))
ck("client_account_state_preserved", 'role === "client"' in text["account_context"] and "const rawList: AccountSummary[]" in text["account_context"] and "role === \"client\"\n          ? rawList" in text["account_context"])
ck("acquisition_live_refresh", "window.setInterval(refresh,15000)" in text["acquisition"] and 'document.addEventListener("visibilitychange",onVisible)' in text["acquisition"])
ck("prospecting_services_live_refresh", "window.setInterval(refresh,15000)" in text["prospecting_services"] and 'document.addEventListener("visibilitychange",onVisible)' in text["prospecting_services"])
ck("prospecting_services_warmup_visible_in_reserve", "reserveBootstrapItems.map" in text["prospecting_services"] and "Минимальный возраст:" in text["prospecting_services"] and "BORIS не накручивает активность" in text["prospecting_services"])
ck("campaign_intake_close_touch", "BORIS_CAMPAIGN_INTAKE_CLOSE_TOUCH_V1_BEGIN" in text["globals"])
ck("owner_menu_fast_path", "FRONTEND_OWNER_MENU_FAST_PATH_V1" in (ROOT/"app/components/shell/Sidebar.tsx").read_text(encoding="utf-8"))
ck(
    "owner_api_waits_for_server_confirmation",
    all("OWNER_AUTH_SERVER_CONFIRM_V1" in text[k] and 'const allowed=authState==="allowed";' in text[k] for k in ("clients_control","development"))
    and 'if(savedEmail===PRIVATE_OWNER_EMAIL)setAuthState("allowed")' not in text["clients_control"]
    and 'if(savedEmail===PRIVATE_OWNER_EMAIL)setAuthState("allowed")' not in text["development"]
    and "OWNER_AUTH_SELF_HEAL_V1" not in text["development"],
)
ck("deploy_mobile_runtime_gate", "FRONTEND_MOBILE_RUNTIME_GATE_V1" in (ROOT/"scripts/boris-frontend-deploy.sh").read_text(encoding="utf-8") and "qa-mobile-campaign-deploy-gate.py" in (ROOT/"scripts/boris-frontend-deploy.sh").read_text(encoding="utf-8"))
ck("campaign_business_status_logic", "CAMPAIGN_BUSINESS_STATUS_V1" in text["campaign"] and 'data-testid="campaign-business-status"' in text["campaign"])
ck("campaign_business_status_mobile", "BORIS_CAMPAIGN_BUSINESS_STATUS_V1_BEGIN" in text["globals"])
ck("deploy_campaign_launch_runtime_gate", "FRONTEND_CAMPAIGN_LAUNCH_RUNTIME_GATE_V1" in (ROOT/"scripts/boris-frontend-deploy.sh").read_text(encoding="utf-8") and (ROOT/"scripts/qa-campaign-launch-contract.py").is_file())
ck("continuous_mobile_campaign_watch", "_maybe_runtime_watch" in (ROOT/"scripts/frontend-artifact-retention.py").read_text(encoding="utf-8") and (ROOT/"scripts/boris-mobile-campaign-runtime-watch.sh").is_file())
retention_text=(ROOT/"scripts/frontend-artifact-retention.py").read_text(encoding="utf-8")
ck("retention_watch_independent", "def _run_retention" in retention_text and "retention_rc, store = _run_retention(args)" in retention_text and "watch_rc = _maybe_runtime_watch(store" in retention_text and "return retention_rc or watch_rc" in retention_text)
ck("runtime_watch_supervisor_timeout_safe", "watch_timeout_seconds = 420" in retention_text and "watch_supervisor_timeout" in retention_text and "tmp.replace(status_file)" in retention_text)
ck("runtime_watch_deploy_race_guard", "deploy_started_before_self_heal" in (ROOT/"scripts/boris-mobile-campaign-runtime-watch.sh").read_text(encoding="utf-8") and "release_changed_reverified" in (ROOT/"scripts/boris-mobile-campaign-runtime-watch.sh").read_text(encoding="utf-8"))
ck("runtime_watch_release_aware", "recent_pass_same_release" in (ROOT/"scripts/frontend-artifact-retention.py").read_text(encoding="utf-8") and 'fields.get("release") == current_release' in (ROOT/"scripts/frontend-artifact-retention.py").read_text(encoding="utf-8"))
ck("runtime_watch_real_deploy_detector", "pgrep -af '^bash (scripts/boris-frontend-deploy" in (ROOT/"scripts/boris-mobile-campaign-runtime-watch.sh").read_text(encoding="utf-8"))
ck("runtime_watch_public_api_dependency_guard", "dependency_public_api_unavailable" in (ROOT/"scripts/boris-mobile-campaign-runtime-watch.sh").read_text(encoding="utf-8") and "public_api_alive" in (ROOT/"scripts/boris-mobile-campaign-runtime-watch.sh").read_text(encoding="utf-8"))
ck("runtime_qa_uses_public_api", 'API=WEB' in (ROOT/"scripts/qa-campaign-launch-contract.py").read_text(encoding="utf-8") and 'WEB+"/api/auth/login"' in (ROOT/"scripts/qa-mobile-campaign-deploy-gate.py").read_text(encoding="utf-8"))
mobile_gate_text=(ROOT/"scripts/qa-mobile-campaign-deploy-gate.py").read_text(encoding="utf-8")
ck("runtime_qa_dynamic_campaign_fixture", "def choose_campaign_route()" in mobile_gate_text and '"/dashboard/campaign/287"' not in mobile_gate_text and '"fixture":fixture' in mobile_gate_text)
ck("runtime_qa_messages_setup_390_1440", '"/messages/setup"' in mobile_gate_text and '"phone-390"' in mobile_gate_text and '"desktop-1440"' in mobile_gate_text and '"messages_setup"' in mobile_gate_text)
launch_gate_text=(ROOT/"scripts/qa-campaign-launch-contract.py").read_text(encoding="utf-8")
def visible_wait_at_least(source, variable, minimum_ms=4000):
    match=re.search(rf'{re.escape(variable)}\.wait_for\(state="visible",\s*timeout=(\d+)\)', source)
    return bool(match and int(match.group(1)) >= int(minimum_ms))
ck("runtime_qa_hydration_wait", visible_wait_at_least(launch_gate_text,"card") and visible_wait_at_least(launch_gate_text,"btn"))
ck("runtime_watch_owner_escalation", "notify_owner_failure_once" in (ROOT/"scripts/boris-mobile-campaign-runtime-watch.sh").read_text(encoding="utf-8") and "needs_owner_or_code_fix" in (ROOT/"scripts/boris-mobile-campaign-runtime-watch.sh").read_text(encoding="utf-8"))
ck("runtime_watch_alert_dedup", "duplicate_incident" in (ROOT/"scripts/boris-mobile-campaign-runtime-watch.sh").read_text(encoding="utf-8") and "ALERT_FILE" in (ROOT/"scripts/boris-mobile-campaign-runtime-watch.sh").read_text(encoding="utf-8"))
ck("runtime_watch_restart_loop_suppressed", "previous_terminal_failure_same_release" in (ROOT/"scripts/boris-mobile-campaign-runtime-watch.sh").read_text(encoding="utf-8") and "restart_suppressed=1" in (ROOT/"scripts/boris-mobile-campaign-runtime-watch.sh").read_text(encoding="utf-8"))
ck(
    "crm_detail_human_error_state",
    "BORIS_CRM_HUMAN_ERROR_V1" in text["crm_api"]
    and 'data-testid="crm-detail-error"' in text["crm_contact_detail"]
    and 'data-testid="crm-detail-error"' in text["crm_deal_detail"]
    and "Повторить" in text["crm_contact_detail"]
    and "Вернуться в CRM" in text["crm_contact_detail"]
    and "Повторить" in text["crm_deal_detail"]
    and "Вернуться в CRM" in text["crm_deal_detail"],
)
ck(
    "crm_detail_keeps_technical_evidence",
    "Техническая причина" in text["crm_contact_detail"]
    and "view.technical" in text["crm_contact_detail"]
    and "Техническая причина" in text["crm_deal_detail"]
    and "view.technical" in text["crm_deal_detail"],
)

# GUIDED_SCENARIO_STATIC_GUARD_V1: the browser regression gate is the runtime
# proof; these source checks fail the build earlier if the guided path is
# accidentally reduced back to a catalog or if «Сделать сейчас» becomes a
# no-op again.
ck(
    "scenario_guide_component",
    "BORIS Guide" in text["scenario_guide"]
    and "boris_scenario_guide_seen:" in text["scenario_guide"]
    and "BORIS делает сам" in text["scenario_guide"]
    and "Нужно ваше действие" in text["scenario_guide"],
)
ck(
    "scenario_guide_inline_action_target",
    "GUIDE_ACTION_TARGET_V1" in text["scenario_detail"]
    and "guideDoNow(currentStep)" in text["scenario_detail"]
    and "data-scenario-step-key" in text["scenario_detail"]
    and "data-guide-step-action" in text["scenario_detail"],
)
ck(
    "scenario_catalog_recommendation",
    'data-testid="scenario-recommended"' in text["scenario_catalog"]
    and "BORIS рекомендует начать отсюда" in text["scenario_catalog"],
)

failed=[name for name,ok in checks if not ok]
for name,ok in checks:
    print(f"{name}={'PASS' if ok else 'FAIL'}")
if failed:
    print("MOBILE_STATIC_CONTRACT=FAIL",",".join(failed))
    raise SystemExit(2)
print(f"MOBILE_STATIC_CONTRACT=PASS checks={len(checks)}")
