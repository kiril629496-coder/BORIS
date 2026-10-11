"""The approved July premium engine with Social's actual editorial QA."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

from app.services.social_media_assets import persist_banner_asset, file_sha256
from app.services.social_editorial_contract import strict_qa_flags


def _cost(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    return amount if math.isfinite(amount) and amount >= 0 else None


NATIVE_PUBLIC_QA_VERSION = "premium_vision_identity_bool_v2_20261008"


def _native_v2_social_cost(account_id, result):
    """Read this post's existing Social ledger without relabeling native QA."""
    receipts = {}
    for attempt in (result.get("delivery_qa_attempts") or {}).values():
        if not isinstance(attempt, dict):
            raise ValueError("SOCIAL_QA_RECEIPT_INVALID")
        evidence = attempt.get("cost_evidence") or {}
        amount = _cost(attempt.get("cost_rub"))
        accounted = _cost(attempt.get("accounted_cost_rub"))
        rid = evidence.get("request_id")
        if (evidence.get("account_id") != str(account_id)
                or evidence.get("operation") != "social_banner_vision_qa_v1"
                or not isinstance(rid, str) or not rid or amount is None or amount <= 0
                or _cost(evidence.get("cost_rub")) != amount or accounted not in (0.0, amount)):
            raise ValueError("SOCIAL_QA_RECEIPT_INVALID")
        entry = receipts.setdefault(rid, {"cost_rub": amount, "accounted": 0, "evidence": copy.deepcopy(evidence)})
        if entry["cost_rub"] != amount:
            raise ValueError("SOCIAL_QA_RECEIPT_CONFLICT")
        entry["accounted"] += int(accounted == amount)
    if any(row["accounted"] != 1 for row in receipts.values()):
        raise ValueError("SOCIAL_QA_RECEIPT_CONFLICT")
    return round(sum(row["cost_rub"] for row in receipts.values()), 6), receipts


def _native_v2_account(account_id, result, preview, state):
    """A native snapshot is cumulative; append only previously unseen receipts.

    The public native reader proves its own image/native-QA journal. Social's
    separate exact receipts remain in this post and share the outer 4 RUB cap.
    Unknown or conflicting proof never turns into a zero-cost cache hit.
    """
    budget = preview.get("budget") or {}
    total = _cost(preview.get("total_cost_rub"))
    native_qa = _cost(preview.get("qa_cost_rub"))
    actual = _cost(preview.get("actual_cost_rub"))
    scope = hashlib.sha256(state["generation_key"].encode()).hexdigest()[:32]
    if (preview.get("account_id") != str(account_id)
            or (preview.get("provenance") or {}).get("budget_intent_key") != state["generation_key"]
            or budget.get("budget_intent_hash") != scope or budget.get("known") is not True
            or preview.get("cost_reconciled") is not True
            or budget.get("duplicate_paid_calls") != 0 or total is None
            or _cost(budget.get("total_cost_rub")) != total or actual is None or actual <= 0
            or native_qa is None):
        raise ValueError("NATIVE_COST_RECONCILIATION_REQUIRED")
    observed = copy.deepcopy(state.get("native_receipts") or {})
    image_id = preview.get("request_id") or preview.get("provider_request_id")
    fresh = [{"request_id": image_id, "operation": "banner_premium_ai", "cost_rub": actual}]
    qa_ids = set()
    qa_sum = 0.0
    for item in preview.get("qa_cost_attempts") or []:
        rid, operation, amount = item.get("request_id"), item.get("operation"), _cost(item.get("cost_rub"))
        if (operation not in {"banner_premium_vision_qa", "banner_premium_vision_qa_batch_v1"}
                or not isinstance(rid, str) or not rid or item.get("cost_known") is not True
                or amount is None or amount <= 0 or rid in qa_ids):
            raise ValueError("NATIVE_QA_RECEIPT_INVALID")
        qa_ids.add(rid)
        qa_sum += amount
        fresh.append({"request_id": rid, "operation": operation, "cost_rub": amount})
    if round(qa_sum, 4) != native_qa:
        raise ValueError("NATIVE_QA_RECEIPT_INCOMPLETE")
    for receipt in fresh:
        rid = receipt["request_id"]
        if not isinstance(rid, str) or not rid or (rid in observed and observed[rid] != receipt):
            raise ValueError("NATIVE_RECEIPT_CONFLICT")
        observed[rid] = receipt
    request_ids = budget.get("request_ids")
    if (not isinstance(request_ids, list) or len(request_ids) != len(set(request_ids))
            or set(request_ids) != set(observed)
            or round(sum(x["cost_rub"] for x in observed.values()), 4) != total
            or total < float(state.get("native_total_rub") or 0)):
        raise ValueError("NATIVE_BUDGET_JOURNAL_INCOMPLETE")
    social, social_receipts = _native_v2_social_cost(account_id, result)
    prior = _cost(result.get("banner_cost_rub"))
    expected_prior = round(float(state.get("native_total_rub") or 0) + social, 6)
    if prior is None or result.get("banner_cost_known") is not True or prior != expected_prior:
        raise ValueError("SOCIAL_NATIVE_AGGREGATE_CONFLICT")
    state.update(native_total_rub=total, native_receipts=observed,
                 native_budget=copy.deepcopy(budget), social_total_rub=social,
                 social_receipts=copy.deepcopy(social_receipts))
    result.update(banner_cost_rub=round(total + social, 6), banner_cost_known=True,
                  banner_attempt_costs=[x["cost_rub"] for x in observed.values() if x["operation"] == "banner_premium_ai"],
                  banner_vision_qa_costs=[x["cost_rub"] for x in observed.values() if x["operation"] != "banner_premium_ai"]
                    + [x["cost_rub"] for x in social_receipts.values()])


def _native_v2_identity(account_id, preview):
    identity = (preview.get("vision_qa") or {}).get("qa_identity")
    contract = json.dumps(preview.get("qa_contract") or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    expected = {"account_id": str(account_id), "banner_id": str(preview.get("banner_id") or ""),
                "version": int(preview.get("version") or 0), "asset_sha256": str(preview.get("artifact_sha256") or ""),
                "qa_version": NATIVE_PUBLIC_QA_VERSION, "contract_sha256": hashlib.sha256(contract.encode()).hexdigest()}
    if (identity != expected or len(expected["asset_sha256"]) != 64
            or preview.get("artifact_current") is not True or preview.get("receipt_current") is not True
            or preview.get("qa_version") != NATIVE_PUBLIC_QA_VERSION):
        raise ValueError("NATIVE_CURRENT_RECEIPT_REQUIRED")
    return copy.deepcopy(identity)


def _generate_premium_native_v2(*, account_id, result, project, content_brief,
                                visual_brief, sq, banner_dir, native, create_kwargs,
                                reservation, cap, final_qa_callback, checkpoint):
    """Approved native API and Social final QA have independent real receipts.

    This new lifecycle uses one image intent. A native quality/schema/provider
    failure is retained without buying another image. Its durable generation
    claim also prevents a lost process from blindly starting a new paid intent.
    """
    context = result.get("final_payload_qa") or {}
    metadata = result.setdefault("july_premium", {})
    state = metadata.get("social_native_v2")

    def blocked(code, *, unknown=False):
        if isinstance(state, dict):
            state["blocked_reason"] = code
            state["known_cost_subtotal_rub"] = result.get("banner_cost_rub")
        result.update(banner=None, banner_engine="full_ai_premium", banner_model="gpt-image-2",
                      quality_status="banner_blocked", banner_error=code)
        if unknown:
            result.update(banner_cost_rub=None, banner_cost_known=False, final_payload_reconciliation_required=True)
        if not isinstance(result.get("vision_qa"), dict):
            result["vision_qa"] = {"pass": False, "issues": [code], "image_repair_allowed": False, "repair_scope": "qa"}
        if isinstance(state, dict) and callable(checkpoint):
            checkpoint()
        return result

    if (getattr(native, "VISION_QA_VERSION", None) != NATIVE_PUBLIC_QA_VERSION
            or not all(callable(getattr(native, name, None)) for name in ("create_preview", "read_current_preview", "approve_preview"))):
        return blocked("PREMIUM_NATIVE_API_VERSION_UNSUPPORTED")
    if (context.get("version") != "SOCIAL_FINAL_PAYLOAD_QA_V1" or not result.get("delivery_texts")
            or not context.get("record_id") or not result.get("generation_key")
            or context.get("generation_key") != result.get("generation_key")
            or not callable(final_qa_callback) or not callable(checkpoint)):
        return blocked("PREMIUM_NATIVE_FINAL_LIFECYCLE_REQUIRED")
    if state is not None and (not isinstance(state, dict) or state.get("context") != context
                              or state.get("version") != NATIVE_PUBLIC_QA_VERSION):
        return blocked("PREMIUM_NATIVE_CONTEXT_MISMATCH")
    if state is None:
        if (result.get("banner_cost_rub", 0.0) != 0.0 or result.get("banner_attempts")
                or result.get("premium_attempts") or result.get("delivery_qa_attempts") or result.get("banner")):
            return blocked("PREMIUM_NATIVE_HISTORICAL_UPGRADE_FORBIDDEN")
        estimate = _cost(native.estimate_cost_rub())
        # Admit the complete native stage using its unchanged image estimate
        # and native-QA reserve. Social is a separate required stage: only the
        # reconciled ACTUAL native debit may fund its full reserve below.
        # A costly native result can remain unpaid-for-Social and unpublished;
        # it cannot cause another image intent or bypass either cost guard.
        if estimate is None or max(estimate, 1.5) + reservation > cap:
            return blocked("BANNER_COST_GUARD_BLOCKED")
        reference = sq.vision_reference_context(project, content_brief, visual_brief)
        if reference.get("required") and reference.get("ok") is not True:
            return blocked("BANNER_OWNER_REFERENCE_UNAVAILABLE")
        state = {"version": NATIVE_PUBLIC_QA_VERSION, "context": copy.deepcopy(context),
                 "generation_key": result["generation_key"], "image_intent": create_kwargs["idempotency_key"],
                 "native_total_rub": 0.0, "native_receipts": {}, "stage": "image_request",
                 "paid_admission": {"policy": "native_then_social_actual_remaining_v1",
                    "image_estimate_rub": estimate, "native_qa_reserve_rub": reservation,
                    "native_stage_reserve_rub": max(estimate, 1.5) + reservation,
                    "social_qa_reserve_rub": reservation},
                 "social_qa_status": "pending_actual_native_cost", "remaining_step": "social_final_payload_qa"}
        metadata["social_native_v2"] = state
        result.update(banner_cost_rub=0.0, banner_cost_known=True, final_payload_stage="image_request")
        checkpoint()
        try:
            preview = native.create_preview(**create_kwargs, defer_vision_qa=False,
                provenance={"source": "social", "project_id": str(project.get("id") or ""),
                            "generation_key": state["generation_key"], "budget_intent_key": state["generation_key"]})
        except Exception as exc:
            state["provider_error"] = type(exc).__name__
            return blocked("PREMIUM_NATIVE_IMAGE_OUTCOME_RECONCILIATION_REQUIRED", unknown=True)
        state["native_create_result"] = {k: copy.deepcopy(preview.get(k)) for k in (
            "status", "code", "banner_id", "version", "request_id", "provider_request_id",
            "image_outcome", "ambiguous_provider_outcome", "actual_cost_rub", "qa_cost_rub", "total_cost_rub")}
        if preview.get("status") != "ok":
            # Both exact codes are returned by the pinned native preset guard
            # before _generate_image_core. The same code on an error response
            # after a provider request is not evidence of a free attempt.
            no_paid_call = preview.get("image_outcome") == "not_started" or (
                preview.get("status") == "cost_cap_exceeded"
                and preview.get("code") in {"COST_CIRCUIT_OPEN", "COST_CAP_EXCEEDED"}
                and not any(preview.get(k) for k in ("request_id", "provider_request_id", "absolute_path", "actual_cost_rub")))
            return blocked(str(preview.get("code") or "PREMIUM_NATIVE_CREATE_BLOCKED"),
                           unknown=not no_paid_call)
        if preview.get("idempotency_replay") is True:
            return blocked("PREMIUM_NATIVE_UNTRACKED_REPLAY_RECONCILIATION_REQUIRED", unknown=True)
        state.update(banner_id=preview.get("banner_id"), banner_version=preview.get("version"), stage="native_received",
                     native_verdict=copy.deepcopy(preview.get("vision_qa") or {}))
        result["banner_attempts"] = 1
        result["premium_attempts"] = [copy.deepcopy(state["native_create_result"])]
        try:
            _native_v2_account(account_id, result, preview, state)
        except (ValueError, TypeError, KeyError) as exc:
            return blocked(str(exc), unknown=True)
        result["final_payload_stage"] = "native_accounted"
        checkpoint()
    if not state.get("banner_id") or not state.get("banner_version"):
        return blocked("PREMIUM_NATIVE_IMAGE_OUTCOME_RECONCILIATION_REQUIRED", unknown=True)
    try:
        preview = native.read_current_preview(str(account_id), str(state["banner_id"]), int(state["banner_version"]))
    except Exception as exc:
        return blocked("PREMIUM_NATIVE_READBACK_BLOCKED:" + type(exc).__name__)
    try:
        _native_v2_account(account_id, result, preview, state)
    except (ValueError, TypeError, KeyError) as exc:
        return blocked(str(exc), unknown=True)
    try:
        identity = _native_v2_identity(account_id, preview)
    except Exception as exc:
        return blocked("PREMIUM_NATIVE_READBACK_BLOCKED:" + type(exc).__name__)
    state["native_verdict"] = copy.deepcopy(preview.get("vision_qa") or {})
    metadata.update({k: preview.get(k) for k in ("engine", "engine_source", "banner_id", "version",
        "owner_reference_version", "owner_style_niche", "preview_status", "qa_passed", "cost_sla_pass")})
    native_pass = (preview.get("qa_passed") is True and preview.get("cost_sla_pass") is True
                   and preview.get("qa_terminal") is True and preview.get("vision_qa", {}).get("qa_status") == "PASS")
    if not native_pass:
        return blocked("PREMIUM_NATIVE_QA_BLOCKED:" + str(preview.get("vision_qa", {}).get("qa_status") or "UNKNOWN"))
    if state.get("identity") is not None and state["identity"] != identity:
        return blocked("PREMIUM_NATIVE_IDENTITY_CHANGED")
    state["identity"] = identity
    path = str(preview.get("absolute_path") or "")
    if not Path(path).is_file() or file_sha256(path) != identity["asset_sha256"]:
        return blocked("PREMIUM_NATIVE_ASSET_CHANGED")
    checked = copy.deepcopy(visual_brief)
    checked["premium_contract"] = copy.deepcopy(preview.get("qa_contract") or {})
    checked["allowed_secondary_text"] = [create_kwargs["exact_subtitle"], *create_kwargs["advantages"]]
    required = sq.vision_required_flags(project, content_brief, checked)
    result["visual_brief"] = checked
    # The existing callback handles exact proof reuse before reserving a new
    # Social request. Its durable checkpoint includes the native debit above.
    try:
        verdict = final_qa_callback(path=path, visual_brief=checked)
    except Exception as exc:
        received = result.get("final_payload_stage") == "qa_accounted"
        if received:
            try:
                _native_v2_account(account_id, result, preview, state)
            except (ValueError, TypeError, KeyError):
                received = False
        return blocked("PREMIUM_SOCIAL_QA_OUTCOME_RECONCILIATION_REQUIRED:" + type(exc).__name__, unknown=not received)
    result["vision_qa"] = copy.deepcopy(verdict)
    try:
        _native_v2_account(account_id, result, preview, state)
    except (ValueError, TypeError, KeyError) as exc:
        return blocked(str(exc), unknown=True)
    valid, issues = strict_qa_flags(verdict, required)
    if "DELIVERY_QA_REMAINING_BUDGET_BLOCKED" in (verdict.get("issues") or []):
        state["social_qa_status"] = "not_performed_remaining_budget_blocked"
        return blocked("BANNER_SOCIAL_QA_REMAINING_BUDGET_BLOCKED")
    state["social_qa_status"] = "passed" if valid and verdict.get("pass") is True else "failed"
    if not valid or verdict.get("pass") is not True or result["banner_cost_rub"] > cap:
        return blocked("BANNER_COST_GUARD_BLOCKED" if result["banner_cost_rub"] > cap else "BANNER_QUALITY_BLOCKED")
    state["remaining_step"] = "native_approval_and_readback"
    checkpoint()
    try:
        approved = native.approve_preview(str(account_id), str(state["banner_id"]), int(state["banner_version"]))
        state["approval_result"] = copy.deepcopy(approved)
        current = native.read_current_preview(str(account_id), str(state["banner_id"]), int(state["banner_version"]))
    except Exception as exc:
        return blocked("PREMIUM_NATIVE_ACCEPTANCE_BLOCKED:" + type(exc).__name__)
    try:
        _native_v2_account(account_id, result, current, state)
    except (ValueError, TypeError, KeyError) as exc:
        return blocked(str(exc), unknown=True)
    try:
        if (approved.get("status") != "ok" or _native_v2_identity(account_id, current) != identity
                or current.get("approved") is not True or current.get("preview_status") != "AVAILABLE_FOR_USE"
                or current.get("qa_passed") is not True or current.get("cost_sla_pass") is not True
                or result["banner_cost_rub"] > cap or file_sha256(path) != identity["asset_sha256"]):
            raise ValueError("PREMIUM_NATIVE_APPROVAL_OR_IDENTITY_INVALID")
        accepted = persist_banner_asset(account_id, path, banner_dir=banner_dir)
        if accepted.get("sha256") != identity["asset_sha256"]:
            raise ValueError("PREMIUM_CANONICAL_ASSET_CHANGED")
    except Exception as exc:
        return blocked("PREMIUM_NATIVE_ACCEPTANCE_BLOCKED:" + type(exc).__name__)
    metadata.update({k: current.get(k) for k in ("preview_status", "qa_passed", "cost_sla_pass")})
    state.update(stage="accepted", native_verdict=copy.deepcopy(current.get("vision_qa") or {}))
    state.pop("remaining_step", None)
    state.pop("blocked_reason", None)
    result.update(banner=accepted["path"], banner_asset=accepted, quality_status="pass",
                  banner_engine="full_ai_premium", banner_model="gpt-image-2", final_payload_stage="accepted")
    result.pop("banner_error", None)
    checkpoint()
    return result


def _final_payload_premium_qa(*, account_id, result, preview, path, checked_visual,
                              total, image_costs, qa_costs, required, cap,
                              final_qa_callback, apply_verdict):
    """Preserve native acceptance while sharing the real final QA cost ledger."""
    native_qa_cost = _cost(preview.get("qa_cost_rub"))
    result.update(banner_cost_rub=total, banner_cost_known=True,
                  banner_attempt_costs=list(image_costs), banner_vision_qa_costs=list(qa_costs),
                  visual_brief=checked_visual)
    saved = (preview.get("vision_qa") or {}).get("social_editorial") or {}
    saved_state = saved.get("final_payload_state") if isinstance(saved, dict) else None
    if native_qa_cost:
        # Native total already contains its original QA. Only an actual proof
        # from this same NEW lifecycle can seed reuse; raw-copy proof cannot.
        if (not isinstance(saved_state, dict)
                or saved_state.get("final_payload_qa") != result.get("final_payload_qa")
                or not isinstance(saved_state.get("delivery_qa_attempts"), dict)
                or not saved_state["delivery_qa_attempts"]):
            return {"pass": False, "image_repair_allowed": False, "repair_scope": "qa",
                    "issues": ["NATIVE_FINAL_PAYLOAD_PROOF_UNAVAILABLE"]}, preview, total, qa_costs
        for key in ("delivery_qa_attempts", "delivery_quality"):
            result[key] = {**copy.deepcopy(saved_state.get(key) or {}),
                           **copy.deepcopy(result.get(key) or {})}
    before = total
    verdict = final_qa_callback(path=path, visual_brief=checked_visual)
    total = _cost(result.get("banner_cost_rub"))
    qa_costs = list(result.get("banner_vision_qa_costs") or [])
    if total is None or result.get("banner_cost_known") is not True:
        return {**verdict, "pass": False, "image_repair_allowed": False, "repair_scope": "qa",
                "issues": list(verdict.get("issues") or []) + ["BANNER_QA_COST_RECONCILIATION_REQUIRED"]}, preview, total, qa_costs
    delta = round(total - before, 6)
    valid, issues = strict_qa_flags(verdict, required)
    native = copy.deepcopy(verdict.get("premium_verdict")) if isinstance(verdict.get("premium_verdict"), dict) else None
    if delta < 0 or not native:
        return {**verdict, "pass": False, "image_repair_allowed": False, "repair_scope": "qa",
                "issues": list(verdict.get("issues") or []) + ["premium_actual_qa_missing" if not native else "premium_cost_regressed"]}, preview, total, qa_costs
    native["passed"] = valid and native.get("passed") is True and total <= cap
    native["qa_cost_rub"] = round(float(native_qa_cost or 0) + delta, 6)
    native["social_editorial"] = {
        "verdict": copy.deepcopy(verdict),
        "final_payload_state": {k: copy.deepcopy(result.get(k)) for k in (
            "final_payload_qa", "delivery_quality", "delivery_qa_attempts",
        )},
    }
    try:
        finalized = apply_verdict(account_id=str(account_id), banner_id=str(preview["banner_id"]),
            version=int(preview["version"]), vision_qa=native,
            qa_mode="social_editorial_with_owner_reference")
        if finalized is None:
            raise RuntimeError("PREMIUM_NATIVE_VERSION_UNAVAILABLE")
        preview = finalized
    except Exception as exc:
        verdict = {**verdict, "pass": False, "image_repair_allowed": False, "repair_scope": "qa",
                   "issues": list(verdict.get("issues") or []) + ["PREMIUM_NATIVE_SAVE_FAILED:" + type(exc).__name__]}
    # The shared result already contains the received QA, even if native save
    # or the later canonical asset copy fails.
    result["vision_qa"] = copy.deepcopy(verdict)
    return verdict, preview, total, qa_costs


def generate_premium_social_banner(
    *, account_id, project_id, theme, result, project, content_brief,
    visual_brief, quality_module, banner_dir, final_qa_callback=None, final_payload_checkpoint=None,
):
    """Generate/reconcile bounded intents; never translate missing QA into PASS.

    The premium engine stores the image before one actual Social + premium QA.
    It inspects the same bytes against text, date and the approved reference.
    Image and QA costs, including rejected attempts, share one 4 RUB cap.
    """
    from app.services.banner_full_ai_premium import create_preview, estimate_cost_rub, _apply_vision_verdict
    from app.services import banner_full_ai_premium as native_api

    from app.services.media_semantic_validator import _vision_estimate_kopeks

    sq = quality_module
    reservation = float(_vision_estimate_kopeks()) / 100.0
    if not math.isfinite(reservation) or reservation <= 0:
        raise ValueError("SOCIAL_QA_RESERVATION_INVALID")
    title = str((visual_brief or {}).get("headline") or "").strip()
    if not title or len(title) > 80:
        result.update(banner=None, quality_status="banner_blocked", banner_error="BANNER_COMPLETE_HEADLINE_REQUIRED")
        return result
    cap = min(4.0, float(sq.BANNER_ACCEPTED_TOTAL_COST_CAP_RUB))
    prior = result.get("banner_cost_rub", 0.0)
    total = _cost(prior)
    image_costs = list(result.get("banner_attempt_costs") or [])
    qa_costs = list(result.get("banner_vision_qa_costs") or [])
    prior_attempts = max(int(result.get("banner_attempts") or 0), len(image_costs))
    max_attempts = min(3, max(1, int(sq.BANNER_REGEN_MAX)))
    if total is None or total > cap:
        result.update(banner=None, quality_status="banner_blocked", banner_error="BANNER_COST_RECONCILIATION_REQUIRED", banner_cost_known=False)
        return result

    theme_text = str((content_brief or {}).get("selected_theme") or theme or "")
    main_object = str((visual_brief or {}).get("main_object") or "")
    pedicure = any(word in (theme_text + " " + main_object).lower() for word in ("педикюр", "стоп", "feet"))
    subtitle = "Уход и комфорт круглый год" if pedicure else "Красиво в вашем ритме"
    advantages = ["Аккуратная форма", "Ухоженный вид", "Комфорт каждый день"]
    seed_data = {
        "account": str(account_id), "project": str(project_id), "theme": theme_text,
        "text": str(result.get("text") or ""), "headline": title,
        "reference_date": (content_brief or {}).get("current_date"),
        "visual_variant": (visual_brief or {}).get("composition_variant") or (content_brief or {}).get("visual_variant"),
        "version": "social_premium_editorial_20261008",
    }
    if final_qa_callback is not None:
        if (result.get("final_payload_qa") or {}).get("version") != "SOCIAL_FINAL_PAYLOAD_QA_V1" or not result.get("delivery_texts"):
            raise ValueError("PREMIUM_FINAL_PAYLOAD_CONTEXT_REQUIRED")
        if not str((visual_brief or {}).get("post_visual_source_text") or "").strip():
            raise ValueError("PREMIUM_FINAL_VISUAL_SOURCE_REQUIRED")
        # The new lifecycle owns this image intent. A later caption-only edit
        # needs QA of the same image, never a different paid image seed.
        seed_data["final_payload"] = {"identity": result["final_payload_qa"]}
    seed = hashlib.sha256(json.dumps(seed_data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    families = {"macro_beauty_closeup": "left", "editorial_salon_scene": "editorial", "premium_product_beauty": "split", "fashion_shadow_composition": "diagonal"}
    family = families.get(str(seed_data["visual_variant"] or ""), ("left", "right", "editorial", "diagonal")[int(seed[:8], 16) % 4])
    description = (
        "Премиальный beauty-баннер для проекта «" + str(project.get("name") or "") + "». "
        "Тема: " + theme_text + ". Главный объект: " + main_object + ". "
        "Дата публикации: " + str((content_brief or {}).get("current_date") or "") + ". "
        "Сезон: " + str((content_brief or {}).get("season") or "") + ". "
        "Полноценная современная рекламная композиция с сильной читаемой типографикой. "
        "Сезон и смысл определяет текущий бриф; старый референс задаёт только уровень исполнения. "
        + ("Исключить: " + ", ".join(str(x) for x in ((visual_brief or {}).get("forbidden_objects") or [])) + ". "
           if final_qa_callback is None else
           "Цвет покрытия на ногтях определяется постом; палитра фона и типографики — референсом и текущим брифом. ")
        + sq.visual_brief_prompt(visual_brief or {})
    )
    if final_qa_callback is not None and len(description) > 3000:
        # The unchanged native engine caps description at 3000. Never send a
        # silently clipped semantic/seasonal contract into a paid image call.
        result.update(banner=None, quality_status="banner_blocked", banner_error="PREMIUM_FINAL_VISUAL_BRIEF_TOO_LONG")
        return result
    if getattr(native_api, "VISION_QA_VERSION", None) is not None or hasattr(native_api, "read_current_preview"):
        return _generate_premium_native_v2(
            account_id=account_id, result=result, project=project, content_brief=content_brief,
            visual_brief=visual_brief, sq=sq, banner_dir=banner_dir, native=native_api,
            create_kwargs={"account_id": str(account_id), "description": description,
                "exact_title": title, "exact_subtitle": subtitle, "exact_cta": "", "exact_price": "",
                "advantages": advantages, "accent_color": "#167A63" if pedicure else "#5B3DF5",
                "fmt": "1:1", "hypothesis": "product_hero", "composition_family": family,
                "reference_style": "", "idempotency_key": "social-premium:" + seed + ":1",
                "edit_instruction": "Сохрани законченную мысль заголовка целиком. Полноценный премиальный beauty-креатив."},
            reservation=reservation, cap=cap, final_qa_callback=final_qa_callback,
            checkpoint=final_payload_checkpoint,
        )
    verdict = dict(result.get("vision_qa") or {})
    error = "BANNER_QUALITY_BLOCKED"
    accepted = None
    known = True
    metadata = None
    attempt_rows = []
    attempts = prior_attempts
    for number in range(prior_attempts + 1, max_attempts + 1):
        estimate = _cost(estimate_cost_rub())
        # Reserve actual Vision cost before the next paid image call.
        if estimate is None or total + max(estimate, 1.5) + reservation > cap:
            error = "BANNER_COST_GUARD_BLOCKED"
            break
        repair = "; ".join(str(x) for x in (verdict.get("issues") or [])[:8])
        if final_qa_callback is not None:
            result.update(banner_cost_rub=total, banner_cost_known=known,
                          banner_attempt_costs=list(image_costs), banner_vision_qa_costs=list(qa_costs),
                          premium_attempts=copy.deepcopy(attempt_rows), final_payload_stage="image_request")
        preview = create_preview(
            account_id=str(account_id), description=description,
            exact_title=title, exact_subtitle=subtitle, exact_cta="", exact_price="",
            advantages=advantages, accent_color="#167A63" if pedicure else "#5B3DF5",
            fmt="1:1", hypothesis="product_hero", composition_family=family,
            reference_style="", idempotency_key="social-premium:" + seed + ":" + str(number),
            edit_instruction=("Сохрани законченную мысль заголовка целиком. Полноценный премиальный beauty-креатив. " + ("Исправь замечания: " + repair if repair else "")),
            defer_vision_qa=True,
            provenance={"source": "social", "project_id": str(project_id), "generation_key": str(result.get("generation_key") or "")},
        )
        attempts = number
        amount = _cost(preview.get("total_cost_rub"))
        image_cost = _cost(preview.get("actual_cost_rub"))
        native_qa_cost = _cost(preview.get("qa_cost_rub"))
        attempt_rows.append({k: preview.get(k) for k in ("banner_id", "version", "provider_request_id", "idempotency_replay", "status", "code", "ambiguous_provider_outcome")})
        # A lost/unknown paid outcome must be reconciled, never repurchased.
        if str(preview.get("status") or "") != "ok" or amount is None or image_cost is None or image_cost <= 0 or native_qa_cost is None:
            if amount is not None:
                total = round(total + amount, 4)
            else:
                known = False
            if final_qa_callback is not None:
                known = False
                result.update(banner_cost_rub=total, banner_cost_known=False,
                              premium_attempts=copy.deepcopy(attempt_rows),
                              final_payload_reconciliation_required=True)
            error = str(preview.get("code") or "BANNER_COST_RECONCILIATION_REQUIRED")
            break
        image_costs.append(image_cost)
        qa_costs.append(native_qa_cost)
        total = round(total + amount, 4)
        if final_qa_callback is not None:
            result.update(banner_cost_rub=total, banner_cost_known=True,
                          banner_attempt_costs=list(image_costs), banner_vision_qa_costs=list(qa_costs),
                          premium_attempts=copy.deepcopy(attempt_rows), banner_attempts=number,
                          final_payload_stage="image_accounted")
        path = str(preview.get("absolute_path") or "")
        if not Path(path).is_file():
            error = "BANNER_ASSET_MISSING"
            break
        checked_visual = dict(visual_brief or {})
        checked_visual["premium_contract"] = dict(preview.get("qa_contract") or {})
        checked_visual["allowed_secondary_text"] = [subtitle, *advantages]
        reference = sq.vision_reference_context(project, content_brief, checked_visual)
        if reference.get("required") and reference.get("ok") is not True:
            error = "BANNER_OWNER_REFERENCE_UNAVAILABLE"
            break
        result["visual_brief"] = checked_visual
        context = {
            "policy": sq.POLICY_VERSION, "account_id": str(account_id),
            "project_id": str(project_id), "brief": content_brief,
            "visual": checked_visual, "text": str(result.get("text") or ""),
            "banner_sha256": file_sha256(path),
            "reference": {k: reference.get(k) for k in ("required", "niche", "version", "sha256")},
        }
        context_sha = hashlib.sha256(json.dumps(context, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        saved = (preview.get("vision_qa") or {}).get("social_editorial") or {}
        required = sq.vision_required_flags(project, content_brief, checked_visual)
        saved_verdict = saved.get("verdict") if isinstance(saved, dict) else None
        reuse = (
            isinstance(saved_verdict, dict) and saved.get("context_sha256") == context_sha
            and _cost(saved_verdict.get("qa_cost_rub")) is not None
            and _cost(saved_verdict.get("qa_cost_rub")) > 0
            and native_qa_cost > 0
        )
        if final_qa_callback is not None:
            verdict, preview, total, qa_costs = _final_payload_premium_qa(
                account_id=account_id, result=result, preview=preview, path=path,
                checked_visual=checked_visual, total=total, image_costs=image_costs,
                qa_costs=qa_costs, required=required, cap=cap,
                final_qa_callback=final_qa_callback, apply_verdict=_apply_vision_verdict,
            )
            if total is None or result.get("banner_cost_known") is not True:
                known = False
                error = "BANNER_QA_COST_RECONCILIATION_REQUIRED"
                break
        elif reuse:
            # The durable native preview already includes this exact paid QA.
            # Failed verdicts are reused too; they must not trigger a rebuy.
            verdict = copy.deepcopy(saved_verdict)
        else:
            if total + reservation > cap:
                error = "BANNER_COST_GUARD_BLOCKED"
                break
            verdict = sq.validate_banner_vision(account_id, path, content_brief, checked_visual, result.get("text", ""), project)
            qa_cost = _cost(verdict.get("qa_cost_rub"))
            if qa_cost is None or qa_cost <= 0:
                known = False
                error = "BANNER_QA_COST_RECONCILIATION_REQUIRED"
                break
            qa_costs.append(qa_cost)
            total = round(total + qa_cost, 4)
            if file_sha256(path) != context["banner_sha256"]:
                verdict = dict(verdict, **{"pass": False})
                verdict["issues"] = list(verdict.get("issues") or []) + ["banner_changed_during_qa"]
            if reference.get("required") and (verdict.get("reference") or {}).get("sha256") != reference.get("sha256"):
                verdict = dict(verdict, **{"pass": False})
                verdict["issues"] = list(verdict.get("issues") or []) + ["reference_changed_during_qa"]
            valid, schema_issues = strict_qa_flags(verdict, required)
            premium_verdict = copy.deepcopy(verdict.get("premium_verdict")) if isinstance(verdict.get("premium_verdict"), dict) else {}
            if not premium_verdict:
                verdict = dict(verdict, **{"pass": False})
                verdict["issues"] = list(verdict.get("issues") or []) + ["premium_actual_qa_missing"]
            else:
                premium_verdict["passed"] = valid and premium_verdict.get("passed") is True and total <= cap
                premium_verdict["qa_cost_rub"] = round(native_qa_cost + qa_cost, 4)
                premium_verdict["social_editorial"] = {
                    "context_sha256": context_sha, "verdict": copy.deepcopy(verdict),
                }
                finalized = _apply_vision_verdict(
                    account_id=str(account_id), banner_id=str(preview["banner_id"]), version=int(preview["version"]),
                    vision_qa=premium_verdict, qa_mode="social_editorial_with_owner_reference",
                )
                if finalized is not None:
                    preview = finalized
        valid, schema_issues = strict_qa_flags(verdict, required)
        if not valid:
            verdict = dict(verdict, **{"pass": False})
            verdict["issues"] = list(dict.fromkeys(list(verdict.get("issues") or []) + schema_issues))
        metadata = {k: preview.get(k) for k in (
            "engine", "engine_source", "banner_id", "version", "composition_family", "composition_profile",
            "icon_profile", "owner_reference_version", "owner_style_niche", "preview_status", "qa_passed", "cost_sla_pass",
        )}
        native_ok = preview.get("qa_passed") is True and preview.get("cost_sla_pass") is True and preview.get("preview_status") == "DRAFT_PREVIEW"
        if verdict.get("pass") is True and native_ok and total <= cap:
            if final_qa_callback is not None:
                try:
                    accepted = persist_banner_asset(account_id, path, banner_dir=banner_dir)
                    if accepted.get("sha256") != context["banner_sha256"]:
                        raise ValueError("PREMIUM_CANONICAL_ASSET_CHANGED")
                except Exception as exc:
                    accepted = None
                    verdict = {**verdict, "pass": False, "image_repair_allowed": False, "repair_scope": "qa",
                               "issues": list(verdict.get("issues") or []) + ["PREMIUM_ASSET_SAVE_FAILED:" + type(exc).__name__]}
                    error = "BANNER_ASSET_PERSIST_FAILED"
            else:
                accepted = persist_banner_asset(account_id, path, banner_dir=banner_dir)
            break
        error = "BANNER_COST_GUARD_BLOCKED" if total > cap else "BANNER_QUALITY_BLOCKED"
        if not native_ok:
            verdict = dict(verdict)
            verdict["pass"] = False
            verdict["issues"] = list(verdict.get("issues") or []) + list((preview.get("vision_qa") or {}).get("issues") or ["premium_qa_failed"])
        if verdict.get("copy_season_ok") is False or verdict.get("occasion_ok") is False:
            error = "BANNER_COPY_REPAIR_REQUIRED"
            break
        if verdict.get("image_repair_allowed") is False or verdict.get("qa_schema_repair_required") is True or verdict.get("repair_scope") == "qa":
            error = "BANNER_QA_REPAIR_REQUIRED"
            break
        if any(str(x).startswith("vision_qa_unavailable:") for x in (verdict.get("issues") or [])):
            error = "BANNER_VISION_QA_UNAVAILABLE"
            break

    result.update(
        banner=accepted["path"] if accepted else None,
        banner_engine="full_ai_premium", banner_model="gpt-image-2",
        banner_attempts=attempts, banner_attempt_costs=image_costs, banner_vision_qa_costs=qa_costs,
        banner_cost_rub=total if known else None, banner_cost_known=known,
        vision_qa=verdict, july_premium=metadata or {}, premium_attempts=attempt_rows,
        quality_status="pass" if accepted else "banner_blocked",
    )
    if accepted:
        result["banner_asset"] = accepted
        result.pop("banner_error", None)
    else:
        result["banner_error"] = error
    return result
