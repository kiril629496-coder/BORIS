import sys
sys.path.insert(0, "/root/BORIS/backend")

from datetime import datetime, timezone
from sqlalchemy import text

from app.db.session import SessionLocal
from app.services import mcn_core
from app.services import calltracking_own
from app.services.telephony_core import create_call, apply_event, ensure_schema as ensure_telephony_schema

ACCOUNT = "__qa_mcn_calltracking_own_v1"
OTHER = "__qa_mcn_calltracking_own_v1_other"
TARIFF = "__QA_MCN_LOCAL_V1"


def _cleanup():
    db = SessionLocal()
    try:
        # Child tables first. All predicates are QA-scoped.
        db.execute(text("DELETE FROM calltracking_attributions WHERE account_id IN (:a,:b)"), {"a": ACCOUNT, "b": OTHER})
        db.execute(text("DELETE FROM calltracking_touchpoints WHERE account_id IN (:a,:b)"), {"a": ACCOUNT, "b": OTHER})
        db.execute(text("DELETE FROM calltracking_sessions WHERE account_id IN (:a,:b)"), {"a": ACCOUNT, "b": OTHER})
        db.execute(text("DELETE FROM calltracking_pool_numbers WHERE pool_id IN (SELECT id FROM calltracking_pools WHERE account_id IN (:a,:b))"), {"a": ACCOUNT, "b": OTHER})
        db.execute(text("DELETE FROM calltracking_sites WHERE account_id IN (:a,:b)"), {"a": ACCOUNT, "b": OTHER})
        db.execute(text("DELETE FROM calltracking_static_bindings WHERE account_id IN (:a,:b)"), {"a": ACCOUNT, "b": OTHER})
        db.execute(text("DELETE FROM calltracking_pools WHERE account_id IN (:a,:b)"), {"a": ACCOUNT, "b": OTHER})
        db.execute(text("DELETE FROM telephony_cdr_reconciliation WHERE account_id IN (:a,:b)"), {"a": ACCOUNT, "b": OTHER})
        db.execute(text("DELETE FROM telephony_carrier_cost_ledger WHERE account_id IN (:a,:b)"), {"a": ACCOUNT, "b": OTHER})
        db.execute(text("DELETE FROM telephony_carrier_cdr WHERE account_id IN (:a,:b)"), {"a": ACCOUNT, "b": OTHER})
        db.execute(text("DELETE FROM telephony_call_carrier_links WHERE account_id IN (:a,:b)"), {"a": ACCOUNT, "b": OTHER})
        db.execute(text("DELETE FROM telephony_events WHERE account_id IN (:a,:b)"), {"a": ACCOUNT, "b": OTHER})
        db.execute(text("DELETE FROM telephony_recordings WHERE account_id IN (:a,:b)"), {"a": ACCOUNT, "b": OTHER})
        db.execute(text("DELETE FROM telephony_calls WHERE account_id IN (:a,:b)"), {"a": ACCOUNT, "b": OTHER})
        db.execute(text("DELETE FROM telephony_dids WHERE account_id IN (:a,:b) OR metadata_json->>'qa'='mcn-own-v1'"), {"a": ACCOUNT, "b": OTHER})
        db.execute(text("DELETE FROM telephony_trunks WHERE account_id IN (:a,:b)"), {"a": ACCOUNT, "b": OTHER})
        db.execute(text("DELETE FROM telephony_provider_configs WHERE account_id IN (:a,:b)"), {"a": ACCOUNT, "b": OTHER})
        db.execute(text("DELETE FROM telephony_tariff_versions WHERE operator='mcn' AND name=:n"), {"n": TARIFF})
        db.commit()
    finally:
        db.close()


def test_mcn_core_and_own_calltracking_end_to_end_without_external_calls():
    ensure_telephony_schema()
    mcn_core.ensure_schema()
    calltracking_own.ensure_schema()
    _cleanup()
    try:
        trunk = mcn_core.trunk_upsert(ACCOUNT, {
            "name": "MCN QA IP",
            "auth_mode": "ip",
            "registrar": "sip.qa.invalid",
            "source_ips": ["192.0.2.10"],
            "codecs": ["alaw", "ulaw"],
            "max_channels": 5,
            "allowed_cli": ["+74950009001", "+74950009002", "+74950009003"],
            "metadata": {"qa": "mcn-own-v1"},
        })
        assert trunk["status"] == "ok"
        trunk_id = int(trunk["trunk"]["id"])
        assert trunk["trunk"]["status"] == "configured_unverified"
        health = mcn_core.trunk_mark_health(ACCOUNT, trunk_id, True, "ok")
        assert health["trunk"]["status"] == "connected"

        did_main = mcn_core.did_upsert(ACCOUNT, {
            "number": "+74950009001", "trunk_id": trunk_id, "purpose": "main",
            "status": "active", "outbound_cli_enabled": True, "metadata": {"qa": "mcn-own-v1"},
        })
        did_static = mcn_core.did_upsert(ACCOUNT, {
            "number": "+74950009002", "trunk_id": trunk_id, "purpose": "calltracking_static",
            "status": "active", "metadata": {"qa": "mcn-own-v1"},
        })
        did_dynamic = mcn_core.did_upsert(ACCOUNT, {
            "number": "+74950009003", "trunk_id": trunk_id, "purpose": "calltracking_dynamic",
            "status": "active", "metadata": {"qa": "mcn-own-v1"},
        })
        assert all(x["status"] == "ok" for x in [did_main, did_static, did_dynamic])
        assert mcn_core.readiness(ACCOUNT)["ready"] is True

        conflict = mcn_core.did_upsert(OTHER, {
            "number": "+74950009003", "purpose": "main", "status": "active",
            "metadata": {"qa": "mcn-own-v1"},
        })
        assert conflict["status"] == "number_already_assigned"

        static = calltracking_own.static_binding_upsert(ACCOUNT, {
            "did_id": did_static["did"]["id"], "source": "avito", "source_ref": "qa-account",
            "campaign_id": "qa-campaign",
        })
        assert static["status"] == "ok"
        static_resolved = calltracking_own.resolve_inbound(ACCOUNT, "+74950009002")
        assert static_resolved["status"] == "attributed"
        assert static_resolved["mode"] == "static"
        assert static_resolved["source"] == "avito"

        auto = apply_event(
            ACCOUNT, "call.started", provider="mcn", provider_call_id="qa-mcn-auto-call-1",
            provider_event_id="qa-mcn-auto-event-1",
            payload={"direction": "inbound", "from": "+79990000009", "to": "+74950009002"},
        )
        assert auto["status"] == "ok"
        assert auto["attribution"] is not None
        assert auto["attribution"]["status"] in {"ok", "existing"}
        assert auto["call"]["source"] == "avito"
        assert auto["call"]["source_ref"] == "qa-account"

        pool = calltracking_own.pool_upsert(ACCOUNT, {
            "name": "QA dynamic", "default_did_id": did_main["did"]["id"],
            "lease_ttl_sec": 300, "cooloff_sec": 120,
        })
        assert pool["status"] == "ok"
        pool_id = int(pool["pool"]["id"])
        add = calltracking_own.pool_add_number(ACCOUNT, pool_id, int(did_dynamic["did"]["id"]))
        assert add["status"] == "ok"

        site = calltracking_own.site_upsert(ACCOUNT, {
            "name": "QA site", "allowed_domains": ["example.com"], "default_pool_id": pool_id,
        })
        assert site["status"] == "ok"
        site_key = site["site"]["public_key"]

        sess = calltracking_own.session_allocate(site_key, {
            "visitor_id": "visitor_qa_0001",
            "session_id": "browser_qa_0001",
            "landing_url": "https://shop.example.com/kitchens?utm_source=yandex&utm_campaign=qa",
            "referrer": "https://yandex.ru/",
            "utm_source": "yandex",
            "utm_medium": "cpc",
            "utm_campaign": "qa_campaign",
            "utm_term": "кухня на заказ",
            "yclid": "qa-yclid",
        }, "https://shop.example.com")
        assert sess["status"] == "ok"
        assert sess["allocation_mode"] == "dynamic"
        assert sess["number"] == "+74950009003"

        sticky = calltracking_own.session_allocate(site_key, {
            "visitor_id": "visitor_qa_0001",
            "session_id": "browser_qa_0001",
            "landing_url": "https://shop.example.com/kitchens",
            "utm_source": "yandex",
            "utm_campaign": "qa_campaign",
        }, "https://shop.example.com")
        assert sticky["status"] == "ok"
        assert sticky["reused"] is True
        assert sticky["session_id"] == sess["session_id"]
        assert sticky["number"] == sess["number"]

        denied = calltracking_own.session_allocate(site_key, {
            "visitor_id": "visitor_qa_denied",
            "session_id": "browser_qa_denied",
        }, "https://evil.example.net")
        assert denied["status"] == "site_not_found_or_origin_denied"

        call = create_call(
            ACCOUNT, "inbound", "+79990000001", sess["number"],
            provider="mcn", provider_call_id="qa-mcn-provider-call-1",
        )
        attr = calltracking_own.attach_call(ACCOUNT, call["id"])
        assert attr["status"] == "ok"
        assert attr["attribution"]["attribution_mode"] == "dynamic"
        assert attr["attribution"]["source"] == "yandex"

        db = SessionLocal()
        try:
            stored = db.execute(text("SELECT source,source_ref,metadata_json FROM telephony_calls WHERE id=:id"), {"id": call["id"]}).mappings().one()
            assert stored["source"] == "yandex"
            assert (stored["metadata_json"] or {}).get("calltracking", {}).get("session_id") == sess["session_id"]
        finally:
            db.close()

        release = calltracking_own.session_release(site_key, sess["session_id"], "visitor_qa_0001", "https://shop.example.com")
        assert release["status"] == "ok"

        fallback = calltracking_own.session_allocate(site_key, {
            "visitor_id": "visitor_qa_0002",
            "session_id": "browser_qa_0002",
            "landing_url": "https://shop.example.com/",
            "utm_source": "vk",
            "utm_campaign": "qa_vk",
        }, "https://shop.example.com")
        assert fallback["status"] == "ok"
        assert fallback["allocation_mode"] == "fallback"
        assert fallback["number"] == "+74950009001"
        assert fallback["confidence"] < 0.5

        version = mcn_core.tariff_version_upsert({
            "name": TARIFF, "effective_from": "2026-09-01", "status": "active",
            "vat_mode": "without_vat", "source_sha256": "0" * 64,
            "source_name": "qa.xlsx", "metadata": {"qa": "mcn-own-v1"},
        })
        assert version["status"] == "ok"
        rates = mcn_core.tariff_rate_bulk_replace(int(version["version"]["id"]), [
            {"direction_type": "local_fixed", "label": "Москва QA", "prefix": "7495", "price": "0.55"},
            {"direction_type": "mobile_ru", "label": "Мобильные QA", "prefix": "79", "price": "1.95"},
        ])
        assert rates == {"status": "ok", "version_id": int(version["version"]["id"]), "rates": 2}
        resolved_rate = mcn_core.tariff_rate_resolve("+74951234567", direction_type="local_fixed")
        assert resolved_rate is not None
        assert str(resolved_rate["price"]).startswith("0.55")

        # CDR reconciliation uses explicit carrier identity; no phone/time heuristic is allowed.
        link = mcn_core.carrier_link_upsert(ACCOUNT, call["id"], {
            "trunk_id": trunk_id, "did_id": did_dynamic["did"]["id"],
            "carrier_call_id": "qa-carrier-cdr-1",
            "asterisk_uniqueid": "qa-unique-1",
            "asterisk_linkedid": "qa-linked-1",
        })
        assert link["status"] == "ok"
        db = SessionLocal()
        try:
            db.execute(text("UPDATE telephony_calls SET talk_duration_sec=60,answered_at=now()-interval '60 seconds',ended_at=now(),state='ended' WHERE id=:id"), {"id": call["id"]})
            db.commit()
        finally:
            db.close()
        cdr = mcn_core.carrier_cdr_upsert({
            "account_id": ACCOUNT, "carrier_call_id": "qa-carrier-cdr-1",
            "direction": "inbound", "from_number": "+79990000001", "to_number": "+74950009003",
            "started_at": datetime.now(timezone.utc), "actual_duration_sec": 60, "billed_duration_sec": 60,
            "carrier_amount": "0", "raw": {"qa": True},
        })
        assert cdr["status"] == "ok"
        rec = mcn_core.reconcile_carrier_cdr(ACCOUNT)
        assert rec["reconciled"] == 1
        assert rec["matched"] == 1
        assert rec["mismatched"] == 0

        js = calltracking_own.script_source(site_key)
        assert "document.currentScript" in js
        assert "new URL(S.src,location.href).origin" in js
        assert "B=O+'/api/calltracking/v1'" in js

        st = calltracking_own.account_status(ACCOUNT)
        assert st["sites"] == 1
        assert st["pools"] == 1

    finally:
        _cleanup()


if __name__ == "__main__":
    test_mcn_core_and_own_calltracking_end_to_end_without_external_calls()
    print("MCN_CALLTRACKING_RUNTIME_OK")
