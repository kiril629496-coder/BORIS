from pathlib import Path

from app.api.avito import _kpi_prepare_mapping_wait


ROOT = Path("/root/BORIS/backend")


def test_mapping_wait_normalizes_only_exact_mapping_preflight_block():
    content = {
        "status": "ok",
        "prepared": [],
        "blocked": [
            {
                "action": "prepare_weak_titles",
                "blocked_by": "publishable_mapping_preflight",
                "item_ids": ["20", "10"],
            },
            {
                "action": "prepare_weak_descriptions",
                "blocked_by": "publishable_mapping_preflight",
                "item_ids": ["20"],
            },
        ],
    }
    out = _kpi_prepare_mapping_wait(content)
    assert out == {
        "status": "waiting",
        "reason": "canonical_mapping_unavailable_after_exact_self_heal",
        "mapping_recovery_attempted": True,
        "item_ids": ["10", "20"],
        "blocked_actions": 2,
    }


def test_mapping_wait_does_not_hide_real_prepare_or_unrelated_block():
    assert _kpi_prepare_mapping_wait({"prepared": [{"operation_id": "x"}], "blocked": []}) is None
    assert _kpi_prepare_mapping_wait({"prepared": [], "blocked": [{"blocked_by": "prepare_duplicate"}]}) is None


def test_non_money_cpl_paths_preserve_mapping_wait_truth_and_endpoint_decorator():
    source = (ROOT / "app/api/avito.py").read_text(encoding="utf-8")
    assert source.count("_nm_mapping_wait = _kpi_prepare_mapping_wait(_nm_content)") >= 2
    assert source.count('"stage":"mapping_wait"') >= 2
    assert '@router.post("/kpi_prepare_cycle")\ndef kpi_prepare_cycle(' in source
