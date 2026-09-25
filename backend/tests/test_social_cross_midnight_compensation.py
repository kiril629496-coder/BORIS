from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_posting_runner_preserves_original_previous_day_intent():
    source = (ROOT / "posting_runner.py").read_text(encoding="utf-8")
    assert "SOCIAL_SERVICE_DAY_OVERRIDE_OUTSIDE_WINDOW" in source
    assert "SOCIAL_CROSS_MIDNIGHT_ONLY_PREVIOUS_DAY" in source
    assert "def autopost_previous_day_slot(" in source
    assert "scheduled_service_day_override=day" in source
    assert 'generation_key = "social-slot:%s:%s:%s_%02d"' in source
    assert '"already_materialized"' in source
    assert '"outside_cross_midnight_window"' in source


def test_due_rescue_has_bounded_previous_day_compensation_not_arbitrary_replay():
    source = (ROOT / "run" / "social_due_rescue_all_active_v3.py").read_text(encoding="utf-8")
    assert "SOCIAL_CROSS_MIDNIGHT_COMPENSATION_V1" in source
    assert 'if int(local_now.strftime("%H")) < 6 and _guard_start' in source
    assert '_guard_start=min(_tracked_days) if _tracked_days else None' in source
    assert "autopost_previous_day_slot(" in source
    assert "_delivery_lifecycle_materialized" in source
    assert "_recoverable_editorial_error" in source
    assert '"recovery":"cross_midnight_previous_day"' in source
    assert "Never blindly replay an arbitrary historical generation error" in source
