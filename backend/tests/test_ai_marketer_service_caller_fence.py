from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_full_marketer_pipeline_has_top_level_systemd_caller_fence():
    src = (ROOT / "ai_marketer_hourly.sh").read_text()
    assert "AI_MARKETER_SERVICE_CALLER_FENCE_V1" in src
    assert "BLOCKED_UNTRUSTED_AI_MARKETER_CALLER" in src
    assert 'boris-ai-marketer.service' in src
    assert src.index("AI_MARKETER_SERVICE_CALLER_FENCE_V1") < src.index("MONEY_WORKSTREAM_SCOPE_PREFLIGHT_V1")
    assert src.index("AI_MARKETER_SERVICE_CALLER_FENCE_V1") < src.index("run_stage late_day_spend_reset")
