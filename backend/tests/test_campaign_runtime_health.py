from app.services.campaign_runtime_health import classify_campaign_runtime_health


def test_v7_benchmark_is_healthy():
    out=classify_campaign_runtime_health(
        timing={
            "duration_total_seconds":351,
            "duration_active_seconds":104.8,
            "duration_waiting_seconds":27.7,
            "duration_provider_seconds":218.5,
        },
        status="running",
        item_total=100,
        item_progress=True,
        last_progress_age_seconds=20,
    )
    assert out["health_state"]=="OK"
    assert out["sla_state"]=="OK"
    assert out["waiting_ratio"]<0.1


def test_waiting_ratio_warns_and_then_becomes_critical():
    warn=classify_campaign_runtime_health(
        timing={"duration_total_seconds":300,"duration_active_seconds":40,"duration_waiting_seconds":180,"duration_provider_seconds":80},
        status="running",item_total=30,item_progress=True,last_progress_age_seconds=10,
    )
    assert warn["health_state"]=="WARNING"
    critical=classify_campaign_runtime_health(
        timing={"duration_total_seconds":800,"duration_active_seconds":50,"duration_waiting_seconds":650,"duration_provider_seconds":100},
        status="running",item_total=30,item_progress=True,last_progress_age_seconds=10,
    )
    assert critical["health_state"]=="CRITICAL"


def test_no_progress_is_warning_then_critical():
    warn=classify_campaign_runtime_health(
        timing={"duration_total_seconds":700,"duration_active_seconds":100,"duration_waiting_seconds":50,"duration_provider_seconds":550},
        status="running",item_total=30,item_progress=True,last_progress_age_seconds=700,
    )
    assert warn["health_state"]=="WARNING"
    critical=classify_campaign_runtime_health(
        timing={"duration_total_seconds":1900,"duration_active_seconds":1000,"duration_waiting_seconds":100,"duration_provider_seconds":800},
        status="running",item_total=30,item_progress=True,last_progress_age_seconds=1900,
    )
    assert critical["health_state"]=="CRITICAL"


def test_provider_inflight_does_not_create_false_no_progress_incident():
    out=classify_campaign_runtime_health(
        timing={"duration_total_seconds":1900,"duration_active_seconds":100,"duration_waiting_seconds":10,"duration_provider_seconds":1790},
        status="running",item_total=30,item_progress=True,last_progress_age_seconds=1900,current_mode="provider",
    )
    assert out["health_state"]=="OK"


def test_large_campaign_sla_thresholds():
    warn=classify_campaign_runtime_health(
        timing={"duration_total_seconds":1201,"duration_active_seconds":900,"duration_waiting_seconds":100,"duration_provider_seconds":201},
        status="running",item_total=100,item_progress=True,last_progress_age_seconds=20,
    )
    assert warn["sla_state"]=="WARNING"
    critical=classify_campaign_runtime_health(
        timing={"duration_total_seconds":2701,"duration_active_seconds":2300,"duration_waiting_seconds":100,"duration_provider_seconds":301},
        status="running",item_total=100,item_progress=True,last_progress_age_seconds=20,
    )
    assert critical["sla_state"]=="CRITICAL"
    assert critical["health_state"]=="CRITICAL"


def test_owner_confirmation_is_not_system_incident():
    out=classify_campaign_runtime_health(
        timing={"duration_total_seconds":5000,"duration_active_seconds":50,"duration_waiting_seconds":4950,"duration_provider_seconds":0},
        status="awaiting_confirmation",item_total=100,item_progress=True,last_progress_age_seconds=5000,requires_owner_action=True,
    )
    assert out["health_state"]=="WAITING_OWNER"
    assert out["sla_state"]=="PAUSED_FOR_OWNER"
