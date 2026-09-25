from app.services.client_execution_contract import (
    _contract_reconciliation_decision,
    _deterministic_contract_texts,
    _text_quality_report,
    _upsert_artifact,
    build_contract,
    verify_contract,
)


def test_owner_text_banner_command_builds_strict_requirements():
    c = build_contract(
        thread_id="t1",
        account_id="a1",
        command_id="c1",
        command_text="сделай 15 текстов и покажи красивые баннеры из профиля, не публикуй",
        history_texts=[],
    )
    req = {r["kind"]: r for r in c["requirements"]}
    assert req["text_ads"]["required_count"] == 15
    assert req["text_ads"]["publish_allowed"] is False
    assert req["banner_selection"]["required_count"] == 6
    assert c["criteria_total"] == 2


def test_text_quantity_without_quality_never_completes():
    c = build_contract(
        thread_id="t1", account_id="a1", command_id="c1",
        command_text="сделай 2 текста", history_texts=[]
    )
    c["artifacts"] = [{
        "artifact_id": "texts:1", "kind": "text_ads", "command_id": "c1",
        "count": 2, "quality_passed": False,
    }]
    verify_contract(c)
    assert c["status"] != "completed"
    assert c["criteria_done"] == 0
    assert c["requirements"][0]["completed_count"] == 0


def test_other_command_artifact_never_counts():
    c = build_contract(
        thread_id="t1", account_id="a1", command_id="c1",
        command_text="сделай 2 текста", history_texts=[]
    )
    c["artifacts"] = [{
        "artifact_id": "texts:old", "kind": "text_ads", "command_id": "old-command",
        "count": 50, "quality_passed": True,
    }]
    verify_contract(c)
    assert c["criteria_done"] == 0
    assert c["status"] != "completed"


def test_deterministic_fallback_passes_quality_gate():
    ads = _deterministic_contract_texts(
        topics=["одобрение ипотеки", "рефинансирование кредитов", "новостройки"],
        required=15,
        company="одобрение ипотеки\nрефинансирование и консолидация кредитов\nновостройки",
        clients="семьи с детьми\nинвесторы\nвозраст 25-55",
    )
    report = _text_quality_report(ads, 15)
    assert report["passed"] is True
    assert len(ads) == 15
    assert len({x["title"] for x in ads}) == 15
    assert min(len(x["description"]) for x in ads) >= 550


def test_guardian_requeues_false_completed_job():
    c = {"status": "running", "criteria_done": 1, "criteria_total": 2}
    assert _contract_reconciliation_decision(c, "completed", attempts=3) == "requeue_job"


def test_guardian_closes_queue_when_artifacts_are_fully_proven():
    c = {"status": "completed", "criteria_done": 2, "criteria_total": 2}
    assert _contract_reconciliation_decision(c, "failed", attempts=5) == "complete_job"


def test_guardian_fails_closed_when_contract_has_no_requirements():
    c = {"status": "completed", "criteria_done": 0, "criteria_total": 0}
    assert _contract_reconciliation_decision(c, "completed", attempts=1) == "fail_closed_zero_requirements"


def test_text_acceptance_cannot_mix_count_and_quality_from_different_retries():
    c = build_contract(
        thread_id="t1", account_id="a1", command_id="c1",
        command_text="сделай 15 текстов", history_texts=[]
    )
    c["artifacts"] = [
        {"artifact_id": "texts:old", "kind": "text_ads", "command_id": "c1", "count": 15, "quality_passed": False},
        {"artifact_id": "texts:new", "kind": "text_ads", "command_id": "c1", "count": 1, "quality_passed": True},
    ]
    verify_contract(c)
    assert c["status"] != "completed"
    assert c["criteria_done"] == 0
    assert c["requirements"][0]["completed_count"] == 0


def test_upsert_replaces_old_command_scoped_text_proof():
    c = {"artifacts": [
        {"artifact_id": "texts:old", "kind": "text_ads", "command_id": "c1", "count": 15, "quality_passed": False},
        {"artifact_id": "other", "kind": "banner_selection", "command_id": "c1", "count": 6},
    ]}
    _upsert_artifact(c, {"artifact_id": "texts:new", "kind": "text_ads", "command_id": "c1", "count": 15, "quality_passed": True})
    text_rows = [a for a in c["artifacts"] if a.get("kind") == "text_ads" and a.get("command_id") == "c1"]
    assert len(text_rows) == 1
    assert text_rows[0]["artifact_id"] == "texts:new"
    assert any(a.get("artifact_id") == "other" for a in c["artifacts"])
