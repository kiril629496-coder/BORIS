# -*- coding: utf-8 -*-
"""Sanctioned capability metadata for Business Scenario orchestration.

This is a deterministic contract/read-model, not an execution engine. Modules keep
owning their business implementation; Scenario uses these descriptors to decide
whether an existing operation may be called and how its structured result is read.
"""
from copy import deepcopy
import hashlib
import json

AUTONOMY_CLASSES = {
    "AUTONOMOUS_SAFE", "OWNER_INPUT_REQUIRED", "OWNER_APPROVAL_REQUIRED",
    "EXTERNAL_WAIT", "MEASUREMENT_WAIT", "MODULE_WAIT", "FORBIDDEN_AUTONOMOUS",
}
OPERATION_CLASSES = {
    "READ_ONLY", "SAFE_INTERNAL_MUTATION", "REVERSIBLE_EXTERNAL_PREPARATION",
    "OWNER_APPROVED_EXTERNAL", "PUBLICATION", "CLIENT_SEND", "PAID_OPERATION",
    "BUSINESS_POLICY_CHANGE",
}
RESULT_STATES = {
    "SUCCESS", "WAITING_EXTERNAL", "WAITING_MODULE", "WAITING_MEASUREMENT",
    "OWNER_REQUIRED", "FAILED_RECOVERABLE", "FAILED_TERMINAL",
}


def _cap(capability_id, module, human_name, human_purpose, operation_class,
         autonomy_class, *, reversible=True, external_effect=False,
         client_communication=False, publication=False, paid_operation=False,
         owner_approval_required=False, approval_scope=None,
         approval_mode="NONE", preview_without_approval=False,
         completion_source="scenario structured result", result_source="scenario structured result",
         supports_resume=False, resume_source=None, measurement_source=None,
         idempotency_key_strategy="read-only/recompute", failure_class="RECOVERABLE",
         retry_policy=None, required_inputs=None, required_capabilities=None,
         forbidden_without_owner=None):
    return {
        "capability_id": capability_id, "module": module,
        "human_name": human_name, "human_purpose": human_purpose,
        "operation_class": operation_class, "account_scoped": True,
        "autonomy_class": autonomy_class, "reversible": bool(reversible),
        "external_effect": bool(external_effect),
        "client_communication": bool(client_communication),
        "publication": bool(publication), "paid_operation": bool(paid_operation),
        "owner_approval_required": bool(owner_approval_required),
        "approval_scope": approval_scope or [],
        "approval_mode": approval_mode,
        "preview_without_approval": bool(preview_without_approval),
        "execution_interface": "scenario_action",
        "completion_source": completion_source, "result_source": result_source,
        "supports_resume": bool(supports_resume), "resume_source": resume_source,
        "pending_states": [], "done_states": [], "failed_states": [],
        "measurement_source": measurement_source,
        "idempotency_key_strategy": idempotency_key_strategy,
        "failure_class": failure_class,
        "retry_policy": retry_policy or {"retry_allowed": True, "backoff_class": "existing_continuation", "max_safe_attempts": 3},
        "required_inputs": required_inputs or [],
        "required_capabilities": required_capabilities or [],
        "forbidden_without_owner": forbidden_without_owner or [],
    }


CAPABILITIES = {
    "marketing_prepare": _cap("marketing_prepare", "AI Marketing", "Подготовить оптимизацию", "Прочитать существующий AI Marketing lifecycle и diagnosis без запуска платного действия.", "READ_ONLY", "AUTONOMOUS_SAFE", measurement_source="daily_stats / authoritative AI Marketing observation window", idempotency_key_strategy="reuse existing kpi_prepare/kpi_apply_log"),
    "reactivation_prepare": _cap("reactivation_prepare", "Reactivation", "Подготовить реактивацию", "Прочитать account-scoped reactivation candidates без отправки клиентам.", "READ_ONLY", "AUTONOMOUS_SAFE", measurement_source="existing CRM/Reactivation funnel"),
    "sales_prepare": _cap("sales_prepare", "CRM/MOP/ROP", "Подготовить sales-контур", "Прочитать сделки и задачи выбранного аккаунта через существующий CRM/MOP/ROP контур.", "READ_ONLY", "AUTONOMOUS_SAFE", measurement_source="existing CRM/MOP/ROP funnel"),
    "sitebuild_prepare": _cap("sitebuild_prepare", "Sitebuild", "Подготовить Sitebuild", "Прочитать существующее состояние проекта сайта без внешней публикации.", "READ_ONLY", "AUTONOMOUS_SAFE", measurement_source="authoritative Sitebuild/lead attribution"),
    "social_prepare": _cap("social_prepare", "Social", "Подготовить Social", "Прочитать существующий Social/content-plan state без реальной публикации.", "READ_ONLY", "AUTONOMOUS_SAFE", measurement_source="authoritative Social analytics"),
    "mop_prepare": _cap("mop_prepare", "MOP", "Подготовить AI-менеджера", "Проверить существующий MOP binding/settings без изменения prompts или клиентских сообщений.", "READ_ONLY", "AUTONOMOUS_SAFE", measurement_source="existing CRM/MOP/ROP funnel"),
    "season_prepare": _cap("season_prepare", "Campaign/Marketing/Social", "Подготовить сезонный план", "Собрать deterministic season plan из существующего account state.", "READ_ONLY", "AUTONOMOUS_SAFE", measurement_source="daily_stats + authoritative module analytics"),
    "market_launch_prepare": _cap("market_launch_prepare", "Campaign/Feed Factory", "Подготовить рыночный тест", "Переиспользовать exact account+geo+segment Campaign либо подготовить deterministic spec без генерации.", "READ_ONLY", "AUTONOMOUS_SAFE", measurement_source="Avito daily_stats after publication", idempotency_key_strategy="exact account_id+geo+segment reuse"),
    "campaign_safe_export": _cap("campaign_safe_export", "Campaign/Feed Factory", "Собрать safe feed", "Собрать локальный проверенный XML из уже согласованных CampaignItem без внешней выгрузки.", "SAFE_INTERNAL_MUTATION", "AUTONOMOUS_SAFE", external_effect=False, completion_source="local feed artifact + sha256", result_source="CampaignItem validation + generated artifact", measurement_source="Avito daily_stats after owner publication", idempotency_key_strategy="scenario_id+campaign_id+approved item payload", required_capabilities=["approved CampaignItem set"]),
    "scenario_measure": _cap("scenario_measure", "Scenario measurement projection", "Измерить outcome", "Сравнить только authoritative source, связанный с типом бизнес-сценария.", "READ_ONLY", "AUTONOMOUS_SAFE", supports_resume=True, resume_source="steps_state.measure.baseline + next authoritative window", measurement_source="scenario-bound authoritative source", idempotency_key_strategy="baseline+authoritative measurement window"),
    "city_research": _cap("city_research", "Market Research parser", "Исследовать город/нишу", "Один owner-approved внешний market research task с безопасным resume по persisted task_id.", "OWNER_APPROVED_EXTERNAL", "OWNER_APPROVAL_REQUIRED", external_effect=True, owner_approval_required=True, approval_scope=["capability_id", "account_id", "city", "product/segment"], supports_resume=True, resume_source="tasks.id persisted in scenario step result", measurement_source="parser city_analysis result", idempotency_key_strategy="persisted task_id; never recreate while present", required_inputs=["city", "product/segment"], forbidden_without_owner=["initial external market research call"]),
    "parse_site": _cap("parse_site", "Parser", "Разобрать сайт или запросить ассортимент", "Если сайт указан — прочитать его и сохранить parsed products; если сайта нет — безопасно остановиться на owner input и направить в Campaign/ассортимент. Ничего не публиковать.", "REVERSIBLE_EXTERNAL_PREPARATION", "AUTONOMOUS_SAFE", external_effect=True, supports_resume=True, resume_source="storage.parsed_products", measurement_source=None, idempotency_key_strategy="account_id+site URL+parse limit"),
    "parsed_products": _cap("parsed_products", "Parser", "Показать найденные товары", "Прочитать сохранённые parsed products перед owner selection.", "READ_ONLY", "OWNER_INPUT_REQUIRED"),
    "seller_defaults": _cap("seller_defaults", "Avito preparation", "Настройки продавца", "Прочитать вопросы; сохранять owner-provided seller defaults только из явного payload.", "BUSINESS_POLICY_CHANGE", "OWNER_INPUT_REQUIRED", approval_scope=["capability_id", "account_id", "seller default values"], idempotency_key_strategy="account_id+normalized values"),
    "to_drafts": _cap("to_drafts", "Parser/Avito drafts", "Создать черновики", "Создать только внутренние drafts из выбранных parsed products.", "SAFE_INTERNAL_MUTATION", "OWNER_APPROVAL_REQUIRED", owner_approval_required=True, approval_scope=["capability_id", "account_id", "selected product indices", "batch label"], idempotency_key_strategy="account_id+selected indices+batch label", required_inputs=["selected product indices"], forbidden_without_owner=["draft creation"]),
    "rewrite_draft": _cap("rewrite_draft", "Avito drafts/AI provider", "Уникализировать тексты", "Переписать bounded batch drafts через платный model call.", "PAID_OPERATION", "OWNER_APPROVAL_REQUIRED", external_effect=True, paid_operation=True, owner_approval_required=True, approval_scope=["capability_id", "account_id", "draft batch fingerprint"], idempotency_key_strategy="draft ids+current title/description fingerprint", forbidden_without_owner=["paid model call"]),
    "card_fields": _cap("card_fields", "Avito category resolver", "Проверить поля карточек", "Определить обязательные поля; owner answers сохранять только когда они явно переданы.", "SAFE_INTERNAL_MUTATION", "AUTONOMOUS_SAFE", idempotency_key_strategy="account drafts+saved card answers"),
    "feed_check": _cap("feed_check", "Avito validator", "Проверить feed", "Отправить preview URL в официальный validator; живой feed не менять.", "REVERSIBLE_EXTERNAL_PREPARATION", "AUTONOMOUS_SAFE", external_effect=True, measurement_source="official Avito XML validator result", idempotency_key_strategy="account_id+ordered draft ids"),
    "republish_check": _cap("republish_check", "Avito", "Найти слабые объявления", "Прочитать кандидатов на замену без снятия объявлений.", "READ_ONLY", "AUTONOMOUS_SAFE", measurement_source="Avito daily_stats"),
    "republish_settings": _cap("republish_settings", "Avito", "Прочитать пороги", "Прочитать business policy порогов слабого объявления.", "READ_ONLY", "AUTONOMOUS_SAFE"),
    "set_republish_settings": _cap("set_republish_settings", "Avito", "Изменить пороги", "Изменить owner business policy слабого объявления.", "BUSINESS_POLICY_CHANGE", "OWNER_APPROVAL_REQUIRED", owner_approval_required=True, approval_scope=["capability_id", "account_id", "min_views_no_contact", "zero_views_days", "enabled"], idempotency_key_strategy="account_id+normalized settings", required_inputs=["republish thresholds"], forbidden_without_owner=["business policy mutation"]),
    "republish_apply": _cap("republish_apply", "Avito", "Снять выбранные объявления", "Выполнить owner-approved внешнее снятие bounded списка объявлений.", "OWNER_APPROVED_EXTERNAL", "OWNER_APPROVAL_REQUIRED", reversible=False, external_effect=True, publication=True, owner_approval_required=True, approval_scope=["capability_id", "account_id", "exact item_ids"], idempotency_key_strategy="account_id+sorted exact item_ids", required_inputs=["exact item_ids"], forbidden_without_owner=["external unpublish"]),
    "auto_duplicate_run": _cap("auto_duplicate_run", "Avito", "Создать дубли", "Без approval только dry-run; mutation требует exact owner confirmation.", "OWNER_APPROVED_EXTERNAL", "OWNER_APPROVAL_REQUIRED", external_effect=True, owner_approval_required=True, approval_scope=["capability_id", "account_id", "max_per_item", "daily_limit", "min_contacts"], idempotency_key_strategy="account_id+run parameters+source state", forbidden_without_owner=["duplicate creation"]),
}


# Approval semantics are normalized once, next to the descriptor registry.
# TWO_PHASE means: safe preview first -> exact fingerprint persisted -> confirm
# can execute only if the authoritative target/parameters are unchanged.
for _cid in ("city_research", "to_drafts", "rewrite_draft", "republish_apply", "auto_duplicate_run"):
    if _cid in CAPABILITIES:
        CAPABILITIES[_cid]["approval_mode"] = "TWO_PHASE"
        CAPABILITIES[_cid]["preview_without_approval"] = True
        CAPABILITIES[_cid]["retry_policy"] = {"retry_allowed": False, "backoff_class": None, "max_safe_attempts": 0}
for _cid in ("set_republish_settings",):
    if _cid in CAPABILITIES:
        CAPABILITIES[_cid]["approval_mode"] = "EXPLICIT_REQUEST"
        CAPABILITIES[_cid]["preview_without_approval"] = False
        CAPABILITIES[_cid]["retry_policy"] = {"retry_allowed": False, "backoff_class": None, "max_safe_attempts": 0}

# Explicit target ownership/precondition strategies consumed by Scenario adapters.
CAPABILITIES["to_drafts"]["approval_scope"] = ["capability_id", "account_id", "selected product indices", "batch label", "address", "niche/topic"]
CAPABILITIES["republish_apply"]["target_scope_strategy"] = "exact item_ids must exist in account-scoped feed_items"
CAPABILITIES["to_drafts"]["target_scope_strategy"] = "selected indices must exist in account-scoped parsed_products"
CAPABILITIES["campaign_safe_export"]["target_scope_strategy"] = "campaign and CampaignItem.account_id must match scenario account"
CAPABILITIES["market_launch_prepare"]["target_scope_strategy"] = "exact account_id + geo/city + segment/product"
CAPABILITIES["city_research"]["target_scope_strategy"] = "scenario account + exact city + exact product/segment"
CAPABILITIES["city_research"]["pending_states"] = ["queued", "running"]
CAPABILITIES["city_research"]["done_states"] = ["done"]
CAPABILITIES["city_research"]["failed_states"] = ["failed", "error"]
CAPABILITIES["scenario_measure"]["pending_states"] = ["WAITING_MEASUREMENT"]
CAPABILITIES["scenario_measure"]["done_states"] = ["SUCCESS"]
CAPABILITIES["scenario_measure"]["failed_states"] = ["FAILED_RECOVERABLE", "FAILED_TERMINAL"]
CAPABILITIES["parse_site"]["done_states"] = ["parsed_products persisted"]
CAPABILITIES["republish_apply"]["authoritative_approval_source"] = "Avito autopilot_settings.mode=always_auto"
CAPABILITIES["campaign_safe_export"]["authoritative_approval_source"] = "CampaignItem owner-ready/approved state"


def get_capability(capability_id):
    item = CAPABILITIES.get(str(capability_id or ""))
    return deepcopy(item) if item else None


def list_capabilities():
    return [get_capability(k) for k in sorted(CAPABILITIES)]


def execution_fingerprint(capability_id, account_id, scenario_id=None, step_key=None,
                          payload=None, scenario_input=None):
    """Deterministic per-scenario execution identity; no persistence of its own."""
    body = {"capability_id": str(capability_id or ""), "account_id": str(account_id or ""),
            "scenario_id": scenario_id, "step_key": str(step_key or ""),
            "payload": payload if isinstance(payload, dict) else {},
            "scenario_input": scenario_input if isinstance(scenario_input, dict) else {}}
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def approval_fingerprint(capability_id, account_id, payload=None, scenario_input=None):
    cap = get_capability(capability_id)
    if not cap:
        return None
    scope = {"capability_id": capability_id, "account_id": str(account_id or "")}
    merged = {}
    if isinstance(scenario_input, dict): merged.update(scenario_input)
    if isinstance(payload, dict): merged.update(payload)
    aliases = {
        "city": ("city", "region"),
        "product/segment": ("product", "direction", "segment"),
        "selected product indices": ("indices",),
        "exact item_ids": ("item_ids",),
        "draft batch fingerprint": ("draft_batch_fingerprint",),
        "seller default values": ("values",),
        "batch label": ("batch_label",),
        "niche/topic": ("niche", "topic"),
        "address": ("address",),
    }
    for key in cap.get("approval_scope") or []:
        if key in ("capability_id", "account_id"): continue
        names = aliases.get(key, (key,))
        scope[key] = next((merged.get(n) for n in names if merged.get(n) not in (None, "")), None)
    raw = json.dumps(scope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def validate_call(capability_id, account_id, payload=None, scenario_input=None, confirm=False,
                  resume_ref=None, previous_approval_fingerprint=None,
                  authoritative_module_approval=False):
    cap = get_capability(capability_id)
    if not cap:
        return {"allowed": False, "result_state": "FAILED_TERMINAL",
                "failure_class": "POLICY_BLOCKED", "reason": "Capability is not sanctioned"}
    merged = {}
    if isinstance(scenario_input, dict): merged.update(scenario_input)
    if isinstance(payload, dict): merged.update(payload)
    missing = []
    for field in cap.get("required_inputs") or []:
        if field == "city" and not (merged.get("city") or merged.get("region")): missing.append(field)
        elif field == "product/segment" and not (merged.get("product") or merged.get("direction") or merged.get("segment")): missing.append(field)
        elif field == "site URL" and not (merged.get("url") or merged.get("site")): missing.append(field)
        elif field == "selected product indices" and not merged.get("indices"): missing.append(field)
        elif field == "exact item_ids" and not merged.get("item_ids"): missing.append(field)
        elif field == "republish thresholds" and not any(k in merged for k in ("min_views_no_contact", "zero_views_days", "enabled")): missing.append(field)
    if missing:
        return {"allowed": False, "result_state": "OWNER_REQUIRED",
                "failure_class": "OWNER_FIXABLE", "owner_input_required": True,
                "missing_inputs": missing, "reason": "Required owner input is missing"}

    fp = approval_fingerprint(capability_id, account_id, payload, scenario_input) if cap.get("owner_approval_required") else None
    if authoritative_module_approval:
        return {"allowed": True, "result_state": "SUCCESS", "module_approval_reused": True,
                "approval_fingerprint": fp}
    if resume_ref and cap.get("supports_resume"):
        return {"allowed": True, "result_state": "SUCCESS", "resume": True,
                "approval_fingerprint": previous_approval_fingerprint or fp}

    if cap.get("owner_approval_required"):
        mode = str(cap.get("approval_mode") or "EXPLICIT_REQUEST")
        if not confirm:
            if cap.get("preview_without_approval"):
                return {"allowed": True, "preview_only": True,
                        "approval_required": True, "result_state": "OWNER_REQUIRED",
                        "approval_fingerprint": fp}
            return {"allowed": False, "result_state": "OWNER_REQUIRED",
                    "failure_class": "POLICY_BLOCKED", "owner_approval_required": True,
                    "approval_fingerprint": fp, "reason": "Exact capability approval is required"}
        if mode == "TWO_PHASE":
            if not previous_approval_fingerprint:
                return {"allowed": False, "result_state": "OWNER_REQUIRED",
                        "failure_class": "POLICY_BLOCKED", "owner_approval_required": True,
                        "approval_fingerprint": None, "next_approval_fingerprint": fp,
                        "stale_approval": False,
                        "reason": "Preview this exact action before confirming it"}
            if previous_approval_fingerprint != fp:
                return {"allowed": False, "result_state": "OWNER_REQUIRED",
                        "failure_class": "POLICY_BLOCKED", "owner_approval_required": True,
                        "approval_fingerprint": previous_approval_fingerprint,
                        "next_approval_fingerprint": fp, "stale_approval": True,
                        "reason": "Action parameters changed; previous approval is stale"}

    return {"allowed": True, "result_state": "SUCCESS", "approval_fingerprint": fp}


def project_result(capability_id, result):
    result = result if isinstance(result, dict) else {}
    if result.get("owner_required") or result.get("need_confirm") or result.get("owner_input_required"):
        state = "OWNER_REQUIRED"
    elif result.get("waiting_measurement"):
        state = "WAITING_MEASUREMENT"
    elif result.get("external_wait"):
        state = "WAITING_EXTERNAL"
    elif result.get("module_wait"):
        state = "WAITING_MODULE"
    elif result.get("ok") is True:
        state = "SUCCESS"
    elif result.get("failure_class") in ("TERMINAL", "POLICY_BLOCKED"):
        state = "FAILED_TERMINAL"
    else:
        state = "FAILED_RECOVERABLE"
    return {"capability_id": capability_id, "state": state,
            "result_ref": result.get("task_id") or result.get("campaign_id") or result.get("file"),
            "result_summary": result.get("summary"),
            "source": (get_capability(capability_id) or {}).get("result_source"),
            "failure_class": result.get("failure_class") or ("RECOVERABLE" if state == "FAILED_RECOVERABLE" else None)}
