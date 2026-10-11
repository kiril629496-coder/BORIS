from pathlib import Path

SRC = (Path(__file__).resolve().parents[1] / "app" / "api" / "avito.py").read_text(encoding="utf-8")


def _block():
    start = SRC.index("KPI_NON_MONEY_PARALLEL_REJECTED_REPAIR_V1")
    end = SRC.index("KPI_NON_MONEY_PARALLEL_AI_FENCE_V1", start)
    return SRC[start:end]


def test_rejected_prepare_is_admitted_to_parallel_lifecycle():
    assert '{"ready_for_ai", "proposal_rejected", "proposal_ready", "apply_in_progress"}' in SRC
    assert '"proposal_rejected": 25' in SRC
    assert 'if _ps == "proposal_rejected":' in SRC


def test_parallel_repair_is_single_internal_transition_only():
    block = _block()
    assert '_repair_attempts >= 1' in block
    assert '_repair_data["repair_attempts"] = 1' in block
    assert '_repair_data["status"] = "ready_for_ai"' in block
    assert '"stage": "proposal_repair_scheduled"' in block
    assert '"ai_calls_made": 0' in block
    assert '"changed_avito": False' in block
    assert "kpi_ai_prepare_execute(" not in block
    assert "kpi_publish_changes(" not in block
    assert "apply_one" not in block
