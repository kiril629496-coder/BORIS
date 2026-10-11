"""Local migration for Premium banner QA receipts after machine-metadata policy changes.

No provider calls, no image generation, no Campaign publication. The migration is
idempotent and operates only on an existing artifact with a current historical QA
receipt. Old receipts stay in audit history.
"""
from __future__ import annotations

import copy


VISIBLE_CHECKS = (
    "semantic_match", "headline_ok", "exact_text_ok", "spelling_ok",
    "forbidden_claims_ok", "composition_ok", "crop_safe_ok",
    "professional_ok", "mobile_ok", "selling_typography_ok", "palette_ok",
    "reference_style_match", "editorial_richness_ok", "secondary_copy_density_ok",
)
MACHINE_FIELDS = ("banner_id", "asset_sha256", "contract_sha256", "qa_version")


def _machine_only_issues(verdict: dict) -> bool:
    issues = [str(x or "").strip().lower() for x in (verdict.get("issues") or []) if str(x or "").strip()]
    return bool(issues) and all(any(field in issue for field in MACHINE_FIELDS) for issue in issues)


def _clean_pass(verdict: dict) -> bool:
    return (
        verdict.get("qa_terminal") is True
        and str(verdict.get("qa_status") or "") == "PASS"
        and verdict.get("passed") is True
        and all(verdict.get(key) is True for key in VISIBLE_CHECKS)
        and not [x for x in (verdict.get("issues") or []) if str(x or "").strip()]
    )


def migrate_receipt(account_id: str, banner_id: str, version: int) -> dict:
    """Migrate one existing Premium artifact to the visible-pixels metadata policy.

    PASS receipts with all visible checks true are rebound locally to the new
    contract identity. Metadata-only REJECT receipts are not promoted; they are
    converted to QA-pending on the same immutable PNG so the normal batch QA may
    recheck visible pixels. Every other receipt fails closed unchanged.
    """
    from app.services import banner_full_ai_premium as B

    account_id = str(account_id or "").strip()
    banner_id = str(banner_id or "").strip()
    try:
        version = int(version or 0)
    except (TypeError, ValueError):
        version = 0
    if not account_id or not banner_id or version <= 0:
        return {"status": "invalid_identity", "changed": False}

    versions, chosen = B._version_row(account_id, banner_id, version)
    if chosen is None:
        return {"status": "not_found", "changed": False, "banner_id": banner_id, "version": version}
    artifact_error = B._artifact_error(account_id, chosen)
    if artifact_error:
        return {"status": "artifact_invalid", "changed": False, "code": artifact_error,
                "banner_id": banner_id, "version": version}

    contract = dict(chosen.get("qa_contract") or {})
    if not contract:
        return {"status": "contract_missing", "changed": False, "banner_id": banner_id, "version": version}

    receipt = B._current_qa_receipt(account_id, chosen)
    if contract.get("machine_identity_policy") == B.VISIBLE_QA_METADATA_POLICY:
        return {"status": "already_current", "changed": False, "receipt_current": bool(receipt),
                "banner_id": banner_id, "version": version}
    if not isinstance(receipt, dict):
        return {"status": "receipt_missing", "changed": False, "banner_id": banner_id, "version": version}

    verdict = dict(receipt.get("verdict") or {})
    mode = ""
    if _clean_pass(verdict):
        mode = "pass_rebind"
    elif (
        verdict.get("qa_terminal") is True
        and str(verdict.get("qa_status") or "") == "REJECT"
        and _machine_only_issues(verdict)
    ):
        mode = "metadata_recheck"
    else:
        return {"status": "visual_or_unknown_reject", "changed": False,
                "banner_id": banner_id, "version": version,
                "issues": list(verdict.get("issues") or [])[:8]}

    old_identity = dict(receipt.get("identity") or {})
    old_receipt = copy.deepcopy(receipt)
    contract["machine_identity_policy"] = B.VISIBLE_QA_METADATA_POLICY
    chosen["qa_contract"] = contract
    history = [dict(x) for x in (chosen.get("qa_policy_upgrades") or []) if isinstance(x, dict)]
    history.append({
        "mode": mode,
        "from_contract_sha256": str(old_identity.get("contract_sha256") or ""),
        "reason": "machine_identity_fields_are_not_visible_banner_copy",
        "issues": list(verdict.get("issues") or [])[:8],
        "at": B._now_iso(),
    })
    chosen["qa_policy_upgrades"] = history[-16:]
    chosen.setdefault("qa_history", []).append(old_receipt)

    if mode == "pass_rebind":
        new_identity = B._qa_identity(account_id, chosen)
        new_verdict = copy.deepcopy(verdict)
        new_verdict["qa_identity"] = new_identity
        new_verdict["qa_version"] = B.VISION_QA_VERSION
        receipts = dict(chosen.get("qa_receipts") or {})
        receipts[B._qa_receipt_key(new_identity)] = {
            "schema": 1,
            "identity": new_identity,
            "verdict": new_verdict,
            "persisted_at": B._now_iso(),
            "migration": "machine_metadata_policy_pass_rebind",
        }
        chosen["qa_receipts"] = receipts
        chosen["vision_qa"] = new_verdict
        chosen["qa_passed"] = True
        chosen["vision_qa_deferred"] = False
        chosen["preview_status"] = "AVAILABLE_FOR_USE"
        status = "pass_rebound"
    else:
        # The old REJECT is retained in qa_history. The new contract has no
        # receipt yet, so the existing PNG is explicitly pending re-QA.
        chosen["vision_qa_deferred"] = True
        chosen["vision_qa"] = {
            "passed": False,
            "qa_status": "PENDING",
            "qa_terminal": False,
            "qa_cost_rub": 0.0,
            "qa_cost_known": True,
            "qa_cost_status": "not_requested",
            "issues": [],
            "checked_at": B._now_iso(),
            "migration": "machine_metadata_policy_recheck_same_artifact",
        }
        chosen["qa_passed"] = False
        chosen["preview_status"] = "VISION_QA_PENDING"
        status = "recheck_same_artifact"

    B._save_versions(account_id, banner_id, versions)
    return {
        "status": status,
        "changed": True,
        "recheck_required": mode == "metadata_recheck",
        "banner_id": banner_id,
        "version": version,
        "artifact_sha256": str(chosen.get("artifact_sha256") or ""),
    }
