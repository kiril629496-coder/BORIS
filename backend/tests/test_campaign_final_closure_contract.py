from types import SimpleNamespace

from app.services import jobs
from app.services import media_semantic_validator as sem


def test_planeta_legacy_banner_density_is_one_in_four():
    ff = {"order_contract": {"banner_required": True, "account_id": "planetazayavki_65985"}}
    ids = list(range(1, 121))
    selected = jobs._campaign_banner_target_ids(ff, ids, "planetazayavki_65985")
    assert len(selected) == 30
    assert selected[:4] == [1, 5, 9, 13]


def test_explicit_banner_every_one_targets_every_card():
    ff = {"order_contract": {"banner_required": True, "banner_every_n": 1}}
    assert jobs._campaign_banner_target_ids(ff, [1, 2, 3], "albertardashov_79016") == [1, 2, 3]


def test_banner_required_false_targets_no_cards():
    ff = {"order_contract": {"banner_required": False, "banner_every_n": 1}}
    assert jobs._campaign_banner_target_ids(ff, [1, 2, 3], "x") == []


def test_semantic_cache_only_reuses_exact_positive_without_provider(monkeypatch):
    payload = {
        "title": "Ипотечная консультация",
        "description": "Помощь с ипотекой",
        "category": "Услуги",
    }
    fp = sem.semantic_content_fingerprint(payload)
    payload["media_semantic_evidence"] = [{
        "media_id": 11,
        "position": 0,
        "content_fingerprint": fp,
        "checker": "openai_vision_text_image",
        "match": True,
        "confidence": 0.97,
        "subject": "mortgage consultation",
        "reason": "matches",
        "policy_version": sem.SEMANTIC_POLICY_VERSION,
    }]
    link = SimpleNamespace(position=0)
    asset = SimpleNamespace(id=11, tags=[], account_id="mbroker_27859")

    def forbidden(*args, **kwargs):
        raise AssertionError("provider must not be called in cache-only mode")

    monkeypatch.setattr(sem, "_vision_chat_json", forbidden)
    out = sem.classify_item_media_vision(
        payload, [(link, asset)], account_id="mbroker_27859", allow_provider=False
    )
    assert out[0]["match"] is True
    assert out[0]["cached"] is True


def test_semantic_cache_only_fails_closed_when_proof_missing(monkeypatch):
    payload = {
        "title": "Окна в Казани",
        "description": "Изготовление окон",
        "category": "Ремонт",
    }
    link = SimpleNamespace(position=0)
    asset = SimpleNamespace(id=12, tags=[], account_id="albertardashov_79016")

    def forbidden(*args, **kwargs):
        raise AssertionError("provider must not be called in cache-only mode")

    monkeypatch.setattr(sem, "_vision_chat_json", forbidden)
    out = sem.classify_item_media_vision(
        payload, [(link, asset)], account_id="albertardashov_79016", allow_provider=False
    )
    assert out[0]["match"] is False
    assert out[0]["checker"] == "cost_anomaly_cache_only"


def test_cost_anomaly_auto_media_never_calls_external_stock():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    src = (root / "app/api/campaigns.py").read_text(encoding="utf-8")
    start = src.index("def ff_workflow_auto_media")
    end = src.index("def _ff_is_existing_banner_asset", start)
    auto_media = src[start:end]
    assert "if len(items) < desired_count and bool(body.allow_paid_qa):" in auto_media
    assert "_campaign_provider_call(collect_stock" in auto_media


def test_non_banner_order_counts_semantically_proven_hero_as_media():
    item = {
        "banner": "/images/banner.webp",
        "gallery": [{
            "url": "/images/banner.webp",
            "source_provider": "openai_full_ai_premium",
            "role": "hero",
        }],
        "media_semantic_evidence": [{
            "match": True,
            "confidence": 0.95,
        }],
    }
    assert jobs._campaign_prepared_photo_count(item) == 0
    assert jobs._campaign_prepared_media_count(item, banner_required=False) == 1
    assert jobs._campaign_prepared_media_count(item, banner_required=True) == 0


def test_non_banner_order_does_not_count_unproven_hero():
    item = {
        "banner": "/images/banner.webp",
        "gallery": [{
            "url": "/images/banner.webp",
            "source_provider": "openai_full_ai_premium",
            "role": "hero",
        }],
        "media_semantic_evidence": [],
    }
    assert jobs._campaign_prepared_media_count(item, banner_required=False) == 0


def test_existing_positive_glazing_proof_can_seed_family_cache():
    payload = {
        "title": "Панорамные окна в частный дом",
        "direction": "Панорамные окна в частный дом",
        "category": "Окна",
    }
    evidence = {
        "match": True,
        "checker": "openai_vision_text_image_cache",
        "confidence": 0.95,
        "subject": "Private house exterior with panoramic windows",
        "media_id": 15568,
    }
    candidate = sem.reusable_family_cache_candidate(payload, evidence)
    assert candidate is not None
    assert candidate["family"] == "glazing_panoramic"
    assert candidate["media_id"] == 15568


def test_banner_qa_proof_cannot_seed_gallery_family_cache():
    payload = {
        "title": "Панорамные окна в частный дом",
        "direction": "Панорамные окна в частный дом",
    }
    evidence = {
        "match": True,
        "checker": "campaign_banner_qa",
        "confidence": 1.0,
        "subject": "QA-approved campaign banner",
        "media_id": 17648,
    }
    assert sem.reusable_family_cache_candidate(payload, evidence) is None


def test_campaign512_generation_provenance_maps_to_glazing_family():
    cases = {
        "campaign512_massmarket_v3:panoramic:2": "glazing_panoramic",
        "campaign512_massmarket_v3:floor:9": "glazing_panoramic",
        "campaign512_massmarket_v3:balcony:4": "glazing_balcony",
        "campaign512_massmarket_v3:facade:3": "glazing_facade_storefront",
        "campaign512_massmarket_v3:commercial:7": "glazing_commercial_storefront",
        "campaign512_massmarket_v3:cold_to_warm:5": "glazing_cold_to_warm",
        "campaign512_massmarket_v3:warm_aluminum:7": "glazing_warm_aluminum",
        "campaign512_massmarket_v3:large_custom:8": "glazing_large_custom",
        "campaign512_massmarket_v3:b2b:6": "glazing_b2b_project",
        "campaign512_massmarket_v3:terrace:3": "glazing_terrace_sliding",
        "campaign512_massmarket_v3:partitions:5": "glazing_aluminum_partition",
        "campaign512_massmarket_v3:entrance:4": "glazing_entrance_group",
        "campaign512_massmarket_v3:aluminum_doors:7": "glazing_aluminum_door",
        "campaign512_partitions_v4:5": "glazing_aluminum_partition",
    }
    for source_ref, expected in cases.items():
        assert sem.semantic_family_from_source_ref(source_ref) == expected
    assert sem.semantic_family_from_source_ref("some_foreign_generator:panoramic:1") == ""


def test_auto_media_uses_semantic_family_pool_before_external_stock():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    src = (root / "app/api/campaigns.py").read_text(encoding="utf-8")
    start = src.index("def ff_workflow_auto_media")
    end = src.index("def _ff_is_existing_banner_asset", start)
    auto_media = src[start:end]
    family_pos = auto_media.index("ACCOUNT_SEMANTIC_FAMILY_POOL_V1")
    stock_pos = auto_media.index("_campaign_provider_call(collect_stock")
    assert family_pos < stock_pos
    assert "vision_family_confirmed:" in auto_media


def test_mortgage_subject_guard_accepts_clear_consultation_document_context():
    payload = {
        "title": "Семейная ипотека — помощь брокера",
        "direction": "Ипотека / консультации по кредитам",
    }
    assert sem._clear_subject_family_match(
        payload, "a person consulting with documents in an office setting"
    ) is True
    assert sem._clear_subject_family_match(
        payload, "consultation for refinancing"
    ) is True
    assert sem._clear_subject_family_match(
        payload, "financial consultation and analysis"
    ) is True


def test_mortgage_subject_guard_keeps_generic_finance_fail_closed():
    payload = {
        "title": "Кредит под залог недвижимости",
        "direction": "Кредит под залог недвижимости",
    }
    assert sem._clear_subject_family_match(payload, "a house or building") is False
    assert sem._clear_subject_family_match(payload, "man contemplating financial data") is False


def test_active_child_does_not_erase_parent_no_progress_debt():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app/services/jobs.py").read_text(encoding="utf-8")
    assert "CHILD_EXISTENCE_IS_NOT_PROGRESS_V1" in src
    assert 'if int((result or {}).get("child_jobs_active") or 0)>0:' not in src


def test_internal_validation_respects_cost_anomaly_cache_only_mode():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app/services/jobs.py").read_text(encoding="utf-8")
    assert '_internal_allow_provider=not bool((payload or {}).get("_cost_anomaly_free_only"))' in src
    assert "allow_provider=_internal_allow_provider" in src
    assert "CACHE_ONLY_UNPROVEN_IS_NOT_REJECTION_V1" in src
    assert '=="cost_anomaly_cache_only"' in src


def test_providerfree_guard_recuration_is_local_and_non_paid():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app/services/jobs.py").read_text(encoding="utf-8")
    assert "PROVIDERFREE_GUARD_RECURATION_V1" in src
    assert '"media_semantic_subject_context_missing"' in src
    assert '"media_semantic_guard_missing"' in src
    assert '"source":"providerfree_guard_recuration"' in src
    assert '"reason":"providerfree_guard_recuration"' in src


def test_explicit_activation_scope_selects_exact_item_ids(monkeypatch):
    from types import SimpleNamespace
    from app.api import campaigns as campaign_api

    rows = [
        SimpleNamespace(id=1, account_id="acc", status="ready", payload_json='{"images":["/images/a.jpg"]}', geo="Moscow"),
        SimpleNamespace(id=2, account_id="acc", status="ready", payload_json='{"images":["/images/b.jpg"]}', geo="Moscow"),
        SimpleNamespace(id=3, account_id="acc", status="draft", payload_json='{"images":["/images/c.jpg"]}', geo="Moscow"),
    ]
    monkeypatch.setattr(campaign_api, "_ff_items", lambda db, campaign_id, allowed: list(rows))
    scoped_rows, scoped, approved, accounts, images, cities = campaign_api._ff_activation_snapshot(
        None, 77, ["acc"], "acc", item_ids=[2]
    )
    assert [r.id for r in scoped_rows] == [2]
    assert [r.id for r in scoped] == [2]
    assert [r.id for r in approved] == [2]
    assert accounts == ["acc"]
    assert images == 1


def test_explicit_activation_scope_rejects_missing_item(monkeypatch):
    import pytest
    from fastapi import HTTPException
    from types import SimpleNamespace
    from app.api import campaigns as campaign_api

    rows = [SimpleNamespace(id=1, account_id="acc", status="ready", payload_json='{}', geo="")]
    monkeypatch.setattr(campaign_api, "_ff_items", lambda db, campaign_id, allowed: list(rows))
    with pytest.raises(HTTPException) as exc:
        campaign_api._ff_activation_snapshot(None, 77, ["acc"], "acc", item_ids=[999])
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "campaign_item_scope_invalid"


def test_subset_publication_receipt_is_bound_to_exact_scope():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app/api/campaigns.py").read_text(encoding="utf-8")
    assert "scope_item_ids" in src
    assert '"scope_mode":"explicit_subset" if body.item_ids else "full_campaign"' in src
    assert "_ready_scope_matches" in src
    assert "_scope_matches" in src


def test_delivery_scope_rows_never_widens_subset():
    from types import SimpleNamespace
    from app.services import jobs

    rows = [
        SimpleNamespace(id=1, account_id="acc"),
        SimpleNamespace(id=2, account_id="acc"),
        SimpleNamespace(id=3, account_id="acc"),
    ]
    C = SimpleNamespace(_ff_items=lambda db, campaign_id, allowed: list(rows))
    campaign = SimpleNamespace(id=77)

    scoped = jobs._campaign_delivery_scope_rows(
        C, None, campaign, ["acc"], "acc", payload={"item_ids":[3,1]}, activation={}
    )
    assert [r.id for r in scoped] == [3,1]

    resumed = jobs._campaign_delivery_scope_rows(
        C, None, campaign, ["acc"], "acc", payload={},
        activation={"submitted_snapshot":{"scope_item_ids":[2]}}
    )
    assert [r.id for r in resumed] == [2]


def test_delivery_scope_rows_fail_closed_on_scope_drift():
    import pytest
    from types import SimpleNamespace
    from app.services import jobs

    rows = [SimpleNamespace(id=1, account_id="acc")]
    C = SimpleNamespace(_ff_items=lambda db, campaign_id, allowed: list(rows))
    campaign = SimpleNamespace(id=77)
    with pytest.raises(RuntimeError, match="DELIVERY_SCOPE_DRIFT"):
        jobs._campaign_delivery_scope_rows(
            C, None, campaign, ["acc"], "acc", payload={"item_ids":[999]}, activation={}
        )


def test_durable_delivery_passes_exact_scope_to_validation_and_activation():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app/services/jobs.py").read_text(encoding="utf-8")
    assert "FFOwnerActivationBody(account_id=account_id, confirmed=False, item_ids=ids)" in src
    assert "FFOwnerActivationBody(account_id=account_id, confirmed=True, item_ids=ids)" in src
    assert "_campaign_delivery_scope_rows" in src


def test_auto_media_filters_banner_assets_before_gallery_stack():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app/api/campaigns.py").read_text(encoding="utf-8")
    assert "AUTO_MEDIA_NEVER_FEEDS_BANNER_AS_GALLERY_V1" in src
    assert "if asset and _ff_is_existing_banner_asset({},None,asset):" in src


def test_providerfree_exhaustion_finalizer_is_terminal_and_subset_scoped():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app/services/jobs.py").read_text(encoding="utf-8")
    assert "def _finalize_providerfree_campaign_repair" in src
    assert '"stage":"providerfree_repair_exhausted"' in src
    assert '"code":"PROVIDERFREE_REPAIR_EXHAUSTED"' in src
    assert '"ready_ids":ready_ids' in src
    assert '"blocked_ids":blocked_ids' in src
    assert '"blocker_by_item":blocker_by_item' in src
    assert 'job.status="partial"' in src
    assert '"_providerfree_ready_subset":True' in src
    assert 'fingerprint=f"campaign_deliver:{campaign_id}:{account_id}:ready_subset:{scope_hash}"' in src


def test_providerfree_terminal_state_is_not_auto_resumed():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app/services/jobs.py").read_text(encoding="utf-8")
    assert "PARTIAL_BUSINESS_TERMINALS_DO_NOT_HOT_RESUME_V1" in src
    assert '"providerfree_repair_exhausted"' in src
    assert 'payload.get("_providerfree_terminalized_at")' in src


def test_startup_recovery_uses_business_finalizer_before_failed_dead_letter():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app/services/jobs.py").read_text(encoding="utf-8")
    assert "if not _finalize_providerfree_campaign_repair(db,job,now):" in src
    assert 'job.error_text="Превышен лимит последовательных восстановлений без прогресса после перезапуска worker"' in src
    assert "_next_recovery_debt(job,p)" in src


def test_providerfree_finalizer_never_delivers_before_required_banner_stage():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app/services/jobs.py").read_text(encoding="utf-8")
    assert "_banner_required=_campaign_banner_required(ff)" in src
    assert 'terminal["delivery_eligible"]=bool(' in src
    assert 'ready_ids and not _banner_required and not _campaign_owner_prepare_only_contract(ff)' in src


def test_providerfree_ready_subset_routes_to_banner_stage_when_required():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app/services/jobs.py").read_text(encoding="utf-8")
    assert 'terminal["banner_stage_eligible"]=bool(ready_ids and _banner_required)' in src
    assert 'db,"campaign_banners",banner_payload' in src
    assert 'fingerprint=f"campaign_banners:{campaign_id}:{account_id}:ready_subset:{scope_hash}"' in src
    assert 'terminal["next_stage"]="banners"' in src


def test_machine_metadata_receipt_migration_classifies_without_auto_passing_reject():
    from app.services.banner_machine_metadata_migration import _machine_only_issues, _clean_pass, VISIBLE_CHECKS
    rejected = {
        "qa_terminal": True, "qa_status": "REJECT", "passed": False,
        "issues": ["Malformed ID in banner_id"],
    }
    assert _machine_only_issues(rejected) is True
    assert _clean_pass(rejected) is False
    visual = {**rejected, "issues": ["visible headline text mismatch"]}
    assert _machine_only_issues(visual) is False

    passed = {
        "qa_terminal": True, "qa_status": "PASS", "passed": True, "issues": [],
        **{key: True for key in VISIBLE_CHECKS},
    }
    assert _clean_pass(passed) is True
    passed["composition_ok"] = False
    assert _clean_pass(passed) is False


def test_premium_banner_job_migrates_old_receipts_before_paid_planning():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app/services/jobs.py").read_text(encoding="utf-8")
    assert "PREMIUM_MACHINE_METADATA_RECEIPT_MIGRATION_V1" in src
    assert "banner_machine_metadata_migration" in src
    assert "_confirmed_artifacts" in src
    assert "_migrate_banner_receipt(account_id,_bid,_ver)" in src
    premium = src[src.index("def _run_campaign_banners_premium"):src.index("def _run_campaign_post_publish_watch")]
    assert premium.index("PREMIUM_MACHINE_METADATA_RECEIPT_MIGRATION_V1") < premium.index("plan = plan_banner_slice(")


def test_avito_error_fee_is_external_wait_not_publication_failure():
    from app.services.jobs import _avito_fee_wait_snapshot
    snap = {
        "item_errors": [
            {"ad_id": "a", "status": "error_fee", "message_code": 2214},
            {"ad_id": "b", "section": "error_fee", "message_code": 2214},
        ]
    }
    out = _avito_fee_wait_snapshot(snap)
    assert out["fee_only"] is True
    assert out["fee_count"] == 2
    assert out["ad_ids"] == ["a", "b"]

    mixed = {"item_errors": [snap["item_errors"][0], {"ad_id": "c", "status": "error_category"}]}
    assert _avito_fee_wait_snapshot(mixed)["fee_only"] is False


def test_avito_fee_wait_preserves_upload_and_never_replays_submit():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app/services/jobs.py").read_text(encoding="utf-8")
    assert "AVITO_FEE_WAIT_IS_EXTERNAL_V1" in src
    assert '"stage":"awaiting_external_funding"' in src
    assert '"code":"AVITO_ADVANCE_FUNDING_REQUIRED"' in src
    assert '"retry_policy":"observe_same_upload_after_funding"' in src
    assert 'payload["stage"]="awaiting_avito_result"' in src
    assert 'payload["upload_id"]=activation["upload_id"]' in src


def test_stale_recovery_respects_fresh_execution_clock_heartbeat():
    import json
    from datetime import datetime, timedelta, timezone

    class Query:
        def __init__(self, value):
            self.row = SimpleNamespace(value=value)
        def filter(self, *args, **kwargs):
            return self
        def first(self):
            return self.row

    class DB:
        def __init__(self, value):
            self.value = value
        def query(self, *args, **kwargs):
            return Query(self.value)

    job = SimpleNamespace(id=11530, account_id="albertardashov_79016")
    now = datetime.now(timezone.utc)

    active = json.dumps({
        "mode": "active",
        "heartbeat_at": (now - timedelta(seconds=1)).isoformat(),
    })
    waiting = json.dumps({
        "mode": "waiting",
        "heartbeat_at": (now - timedelta(seconds=2)).isoformat(),
    })
    stale = json.dumps({
        "mode": "active",
        "heartbeat_at": (now - timedelta(seconds=10)).isoformat(),
    })

    assert jobs._job_execution_heartbeat_is_fresh(DB(active), job, now, 5) is True
    assert jobs._job_execution_heartbeat_is_fresh(DB(waiting), job, now, 5) is True
    assert jobs._job_execution_heartbeat_is_fresh(DB(stale), job, now, 5) is False
    assert jobs._job_execution_heartbeat_is_fresh(DB("not-json"), job, now, 5) is False


def test_stale_recovery_checks_heartbeat_before_execution_lock_probe():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app/services/jobs.py").read_text(encoding="utf-8")
    start = src.index("for j in stale:")
    body = src[start:start + 1600]
    assert body.index("_job_execution_heartbeat_is_fresh") < body.index("_job_execution_lock_is_held")


def test_recovery_budget_is_consecutive_not_lifetime():
    p = {
        "_recovery_count": 10,
        "stage": "internal_validation",
    }
    job = SimpleNamespace(
        kind="campaign_prepare",
        payload_json=p,
        progress_done=5,
        progress_total=8,
    )
    debt = jobs._next_recovery_debt(job, p)
    assert debt["lifetime"] == 10
    assert debt["next_lifetime"] == 11
    assert debt["next_consecutive"] == 1

    p["_last_recovery_progress_signature"] = debt["signature"]
    p["_consecutive_recovery_count"] = 10
    same = jobs._next_recovery_debt(job, p)
    assert same["next_consecutive"] == 11
    assert same["next_consecutive"] > jobs.MAX_RECOVERY_COUNT

    p["stage"] = "ready_for_review"
    progressed = jobs._next_recovery_debt(job, p)
    assert progressed["next_consecutive"] == 1


def test_waiting_heartbeat_grace_is_bound_to_exact_lease():
    import json
    from datetime import datetime, timedelta, timezone

    class Query:
        def __init__(self, value):
            self.row = SimpleNamespace(value=value)
        def filter(self, *args, **kwargs):
            return self
        def first(self):
            return self.row

    class DB:
        def __init__(self, value):
            self.value = value
        def query(self, *args, **kwargs):
            return Query(self.value)

    now = datetime.now(timezone.utc)
    job = SimpleNamespace(
        id=11530,
        account_id="albertardashov_79016",
        payload_json={"_execution_lease_token": "lease-current"},
    )
    waiting = json.dumps({
        "mode": "waiting",
        "heartbeat_at": (now - timedelta(seconds=90)).isoformat(),
        "lease_token": "lease-current",
    })
    assert jobs._job_execution_heartbeat_is_fresh(DB(waiting), job, now, 5, 120) is True

    old_waiting = json.dumps({
        "mode": "waiting",
        "heartbeat_at": (now - timedelta(seconds=121)).isoformat(),
        "lease_token": "lease-current",
    })
    assert jobs._job_execution_heartbeat_is_fresh(DB(old_waiting), job, now, 5, 120) is False

    foreign = json.dumps({
        "mode": "waiting",
        "heartbeat_at": (now - timedelta(seconds=1)).isoformat(),
        "lease_token": "lease-old",
    })
    assert jobs._job_execution_heartbeat_is_fresh(DB(foreign), job, now, 5, 120) is False


def test_terminal_partial_campaign_stages_never_hot_resume():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app/services/jobs.py").read_text(encoding="utf-8")
    assert "PARTIAL_BUSINESS_TERMINALS_DO_NOT_HOT_RESUME_V1" in src
    assert '"ready_subset_for_review"' in src
    assert '"banners_partial"' in src
    assert '"providerfree_repair_exhausted"' in src
