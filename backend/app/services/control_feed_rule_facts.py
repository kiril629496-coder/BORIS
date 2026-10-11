"""Pure stage evidence for the fourteen registered feed rules.

Inputs come from existing canonical writers; this module never reads a provider,
mutates a campaign, authorizes publication, or upgrades stage evidence to rule PASS.
"""
from datetime import datetime, timezone
import hashlib
import json
import re

PREFIX = "rule.feed_factory."
FEED_FACT_CONTRACT = "feed_stage_facts_v1"
STAGE_RULES = {
    "delivery_guard": (PREFIX + "retry", PREFIX + "superseded"),
    "materialize": (PREFIX + "draft",),
    "local_validation": (PREFIX + "validation", PREFIX + "media_quality"),
    "xmlcheck": (PREFIX + "validation", PREFIX + "xml_generation"),
    "activation_preflight": tuple(PREFIX + x for x in
        ("validation", "media_quality", "duplicate", "replacement", "superseded")),
    "publish_submit": tuple(PREFIX + x for x in ("publish", "schedule", "retry")),
    "delivery_review": tuple(PREFIX + x for x in
        ("review", "rejection", "partial_publish", "terminal_states", "schedule", "retry", "superseded")),
}
MANDATORY_STAGE_RULES = {
    "delivery_guard": (),
    "materialize": (PREFIX + "draft",),
    "local_validation": (PREFIX + "validation", PREFIX + "media_quality"),
    "xmlcheck": (PREFIX + "validation", PREFIX + "xml_generation"),
    "activation_preflight": tuple(PREFIX + x for x in ("validation", "media_quality", "duplicate")),
    "publish_submit": (PREFIX + "publish",),
    "delivery_review": (PREFIX + "review",),
}


def feed_event_identity(kind, value):
    """Stable identity over persisted/canonical inputs, never a random attempt id."""
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return str(kind) + ":" + hashlib.sha256(raw.encode()).hexdigest()


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _ids(value):
    return isinstance(value, list) and bool(value) and all(_integer(x, 1) for x in value) and len(set(value)) == len(value)


def _strings(value):
    return isinstance(value, list) and all(isinstance(x, str) and x.strip() == x and x for x in value) and len(set(value)) == len(value)


def _time(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo and dt.utcoffset() is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def _decision(f, result, reason, evidence=None, *, due=True, waiting=False):
    return {"evaluation_status": "EVALUATED" if type(result) is bool else ("WAITING_EVENT" if waiting else "NOT_PROVEN"),
            "predicate_result": result if type(result) is bool else None,
            "reason": reason, "facts": {"contract": FEED_FACT_CONTRACT,
                **{k: f.get(k) for k in ("account_id", "campaign_id", "stage", "event_id", "artifact_sha256", "artifact_kind", "observed_at")},
                **(evidence or {})}, "source_observed_at": f.get("observed_at"),
            "evaluation_due_applicable": bool(due), "whole_rule_postcondition_proven": False,
            "rule_pass": False}


def evaluate_feed_rule_facts(facts):
    f = facts if isinstance(facts, dict) else {}
    stage = f.get("stage")
    if stage not in STAGE_RULES:
        return {}
    out = {}
    def put(key, result, reason, evidence=None, **kw):
        full = PREFIX + key
        if full in STAGE_RULES[stage]:
            out[full] = _decision(f, result, reason, evidence, **kw)
    observed = _time(f.get("observed_at"))
    identity_ok = (isinstance(f.get("account_id"), str) and bool(f["account_id"].strip())
        and isinstance(f.get("event_id"), str) and bool(f["event_id"].strip())
        and (f.get("campaign_id") is None or _integer(f.get("campaign_id"), 1))
        and observed is not None and observed <= datetime.now(timezone.utc)
        and (f.get("artifact_sha256") is None or _sha(f.get("artifact_sha256"))))
    if not identity_ok:
        return {key: _decision(f, None, "FEED_SOURCE_IDENTITY_NOT_PROVEN", due=False)
                for key in MANDATORY_STAGE_RULES[stage]}

    if stage == "materialize":
        ids = f.get("materialized_ids")
        complete = (_ids(ids) and f.get("row_scope_verified") is True
            and isinstance(f.get("order_id"), str) and bool(f["order_id"])
            and _integer(f.get("items_total")) and f["items_total"] == len(ids)
            and _integer(f.get("requested_total"), 1)
            and _integer(f.get("created_count")) and _integer(f.get("updated_count")))
        put("draft", (len(ids) == f["requested_total"]) if complete else None,
            "DRAFT_REQUESTED_QUANTITY_MATERIALIZED" if complete and len(ids) == f["requested_total"] else
            "DRAFT_QUANTITY_INCOMPLETE" if complete else "DRAFT_ROW_SCOPE_NOT_PROVEN",
            {k: f.get(k) for k in ("order_id", "requested_total", "items_total", "created_count", "updated_count", "materialized_ids")})

    elif stage == "local_validation":
        ids, reports = f.get("expected_item_ids"), f.get("reports")
        complete = (_ids(ids) and isinstance(reports, list) and len(reports) == len(ids)
            and all(isinstance(r, dict) and _integer(r.get("id"), 1)
                    and r.get("account_id") == f["account_id"] and type(r.get("valid")) is bool
                    and isinstance(r.get("errors"), list) for r in reports)
            and {r["id"] for r in reports} == set(ids)
            and f.get("source") == "campaigns._ff_validate_row")
        evidence = {"expected_item_ids": ids, "reports_complete": bool(complete)}
        if complete:
            evidence["invalid_item_ids"] = [r["id"] for r in reports if not r["valid"]]
            media_bad = [r["id"] for r in reports if any(isinstance(e, dict) and
                (str(e.get("code", "")).startswith(("semantic_", "photo_stack_", "image_", "images_"))
                 or e.get("field") in {"images", "image", "media", "photo_stack"}) for e in r["errors"])]
            evidence["media_invalid_item_ids"] = media_bad
            put("validation", not evidence["invalid_item_ids"], "CANONICAL_LOCAL_VALIDATION_OBSERVED", evidence)
            put("media_quality", not media_bad, "CURRENT_LINKED_MEDIA_VALIDATION_OBSERVED", {**evidence,
                "full_order_gallery_count_proven": False})
        else:
            for key in ("validation", "media_quality"):
                put(key, None, "CANONICAL_LOCAL_VALIDATION_INCOMPLETE", evidence)

    elif stage == "xmlcheck":
        artifact = _sha(f.get("artifact_sha256")) and f.get("xml_written") is True and _integer(f.get("xml_items"), 1)
        put("xml_generation", True if artifact else None,
            "EXACT_XML_ARTIFACT_WRITTEN" if artifact else "XML_ARTIFACT_NOT_PROVEN", {"xml_items": f.get("xml_items")})
        state = f.get("xmlcheck_status")
        result = (state == "ok") if artifact and state in {"ok", "errors"} else None
        put("validation", result, "OFFICIAL_XMLCHECK_RESULT" if result is not None else
            "OFFICIAL_XMLCHECK_PENDING" if state == "pending" else "OFFICIAL_XMLCHECK_NOT_PROVEN",
            {"xmlcheck_status": state, "check_id": f.get("check_id")}, waiting=state == "pending", due=state != "pending")

    elif stage == "activation_preflight":
        d = f.get("delivery") if isinstance(f.get("delivery"), dict) else {}
        bound = d.get("account_id") == f["account_id"] and d.get("campaign_id") == f["campaign_id"]
        current, validated = d.get("current_sha"), d.get("validated_sha")
        proof = bound and _sha(current) and _sha(validated)
        put("validation", (d.get("validation_state") == "READY" and current == validated) if proof else None,
            "ACTIVATION_CURRENT_XML_VALIDATION", {"current_sha": current, "validated_sha": validated, "validation_state": d.get("validation_state")})
        put("media_quality", d.get("semantic_ready") if bound and type(d.get("semantic_ready")) is bool else None,
            "ACTIVATION_CURRENT_LINKED_MEDIA", {"semantic_blocked": d.get("semantic_blocked"), "full_order_gallery_count_proven": False})
        known = bound and type(d.get("image_unique")) is bool and type(d.get("content_unique")) is bool
        put("duplicate", d["image_unique"] and d["content_unique"] if known else None,
            "ACTIVATION_DUPLICATE_GUARDS", {"image_unique": d.get("image_unique"), "content_unique": d.get("content_unique")})
        if f.get("replacement_requested") is True:
            e = f.get("replacement_evidence") if isinstance(f.get("replacement_evidence"), dict) else {}
            known = (e.get("verified") is True and isinstance(e.get("old_feed_identity"), str)
                and bool(e.get("old_feed_identity")) and isinstance(e.get("new_feed_identity"), str)
                and bool(e.get("new_feed_identity")) and _integer(e.get("provider_rejection_upload_id"), 1)
                and _time(e.get("verified_at")) is not None)
            put("replacement", e["old_feed_identity"] != e["new_feed_identity"] if known else None,
                "TARGETED_REPLACEMENT_SCOPE_OBSERVED" if known else "REPLACEMENT_SCOPE_NOT_PROVEN", e)

    elif stage == "publish_submit":
        result = f.get("result") if isinstance(f.get("result"), dict) else {}
        state = result.get("delivery_state")
        exact = _sha(f.get("artifact_sha256")) and f.get("canonical_publisher") == "app.api.avito.feed_send_to_avito"
        if f.get("artifact_kind") == "canonical_feed_items_json":
            exact = exact and f.get("source_input_unchanged") is True and f.get("source_items_present") is True
        accepted = (result.get("status") == "ok" and state in {"submitted", "submitted_reconciled"}
                    and _integer(result.get("upload_id"), 1))
        put("publish", True if exact and accepted else None,
            "SUBMISSION_RECEIPT_OBSERVED_NOT_PUBLICATION" if exact and accepted else "PUBLICATION_DELIVERY_NOT_PROVEN",
            {"status": result.get("status"), "delivery_state": state, "upload_id": result.get("upload_id"), "published_proven": False})
        if state == "scheduled_url":
            known = exact and result.get("status") == "ok" and result.get("autoload_enabled") is True and result.get("feed_url_matches") is True
            put("schedule", True if known else None, "AUTOLOAD_SCHEDULE_CONFIGURATION_OBSERVED" if known else "SCHEDULE_CONFIGURATION_NOT_PROVEN",
                {"baseline_upload_id": result.get("baseline_upload_id"), "new_upload_observed": False})

    elif stage == "delivery_review":
        s = f.get("snapshot") if isinstance(f.get("snapshot"), dict) else {}
        expected = f.get("expected_ad_ids")
        scope = s.get("scope") if isinstance(s.get("scope"), dict) else {}
        source = (f.get("snapshot_source") == "app.api.avito._autoload_upload_snapshot"
            and f.get("snapshot_account_id") == f["account_id"]
            and _integer(s.get("upload_id"), 1) and _strings(expected) and bool(expected)
            and f.get("snapshot_expected_ad_ids") == expected)
        actual_upload = s.get("upload_id")
        requested_upload = f.get("requested_upload_id")
        if requested_upload is not None:
            source = source and _integer(requested_upload, 1) and actual_upload == requested_upload
        if f.get("scheduled_watch") is True:
            baseline = f.get("baseline_upload_id")
            source = source and _integer(baseline) and actual_upload > baseline
            put("schedule", True if source else None, "SCHEDULE_NEW_UPLOAD_OBSERVED" if source else "SCHEDULE_AWAITING_NEW_UPLOAD",
                {"baseline_upload_id": baseline, "upload_id": actual_upload}, due=False, waiting=not source)
        pending = s.get("status") in {"pending", "error"} or s.get("terminal") is not True
        counts = all(_integer(s.get(k)) for k in ("total", "successful", "error", "blocked"))
        total = s.get("total")
        scoped = source and counts and total == len(expected) and s["successful"] + s["error"] + s["blocked"] <= total
        evidence = {"upload_id": actual_upload, "expected_ad_ids": expected, "terminal": s.get("terminal"),
            **{k: s.get(k) for k in ("total", "successful", "error", "blocked")}, "provider_scope_proven": bool(scoped)}
        put("review", True if scoped and not pending else None,
            "TERMINAL_SCOPED_PROVIDER_REPORT_OBSERVED" if scoped and not pending else "PROVIDER_REVIEW_WAITING" if pending else "PROVIDER_REPORT_SCOPE_NOT_PROVEN",
            evidence, due=not pending, waiting=pending)
        errors = s.get("item_errors")
        rejected = isinstance(errors, list) and bool(errors)
        if rejected:
            exact_errors = scoped and all(isinstance(e, dict) and str(e.get("ad_id") or "") in expected for e in errors)
            put("rejection", True if exact_errors else None, "EXACT_PROVIDER_REJECTIONS_OBSERVED" if exact_errors else "REJECTION_SCOPE_NOT_PROVEN",
                {**evidence, "rejected_ad_ids": [str(e.get("ad_id") or "") for e in errors if isinstance(e, dict)]})
        if s.get("partial") is True:
            observed = scoped and not pending and 0 < s["successful"] < total
            put("partial_publish", True if observed else None, "PARTIAL_PROVIDER_RESULT_OBSERVED" if observed else "PARTIAL_PROVIDER_RESULT_NOT_PROVEN", evidence)
        if s.get("terminal") is True:
            local = f.get("persisted_terminal_status")
            expected_status = "completed" if s.get("full_success") is True else "partial" if s.get("partial") is True else "failed"
            complete_counts = scoped and s["successful"] + s["error"] + s["blocked"] == total
            truthful = complete_counts and ((expected_status == "completed" and s["successful"] == total) or
                (expected_status == "partial" and 0 < s["successful"] < total) or
                (expected_status == "failed" and s["successful"] == 0))
            put("terminal_states", local == expected_status if truthful and local in {"completed", "partial", "failed"} else None,
                "LOCAL_TERMINAL_MATCHES_SCOPED_PROVIDER" if truthful else "TERMINAL_RESULT_NOT_PROVEN", {**evidence, "persisted_terminal_status": local})

    # Only actual guarded branches make retry/superseded applicable; a missing
    # event is not a skipped decision and never a fabricated successful no-op.
    if f.get("retry_guard_triggered") is True:
        policy = f.get("retry_policy")
        no_write = f.get("external_action") is False
        allowed = f.get("retry_authorized")
        known = type(allowed) is bool and policy == "explicit_retry_authorization"
        put("retry", (allowed or no_write) if known else None, "RETRY_AUTHORIZATION_GUARD_OBSERVED",
            {"retry_authorized": allowed, "external_action": f.get("external_action")})
    if f.get("superseded_guard_triggered") is True:
        known = f.get("campaign_status") == "superseded" and type(f.get("external_action")) is bool
        put("superseded", f["external_action"] is False if known else None, "SUPERSEDED_PUBLICATION_GUARD_OBSERVED",
            {"campaign_status": f.get("campaign_status"), "external_action": f.get("external_action")})
    return out
