from types import SimpleNamespace

from app.api import viral


def _payload(**overrides):
    data = dict(
        leads_per_month=100,
        average_check_rub=30000,
        conversion_percent=10,
        managers_count=2,
        avg_response_minutes=30,
        unanswered_leads=10,
    )
    data.update(overrides)
    return viral.CalculatorInput(**data)


def test_score_is_deterministic_and_bounded():
    p = _payload()
    a = viral._score(p)
    b = viral._score(p)
    assert a == b
    assert 0 <= a["total"] <= 100
    assert sum(x["max"] for x in a["breakdown"].values()) == 100


def test_loss_is_range_and_not_exact_claim():
    loss = viral._loss(_payload())
    assert loss["estimated"]["lost_revenue_low"] <= loss["estimated"]["lost_revenue_high"]
    assert loss["confidence"] == "estimate"
    assert "не подтверждённая" in loss["disclaimer"]


def test_faster_response_improves_score_and_reduces_loss():
    slow = _payload(avg_response_minutes=180)
    fast = _payload(avg_response_minutes=5)
    assert viral._score(fast)["total"] > viral._score(slow)["total"]
    assert viral._loss(fast)["estimated"]["lost_revenue_high"] < viral._loss(slow)["estimated"]["lost_revenue_high"]


def test_public_report_whitelists_sensitive_assumptions(monkeypatch):
    token = "x" * 32
    now = viral._now()
    stored = {
        "created_at": now.isoformat(),
        "expires_at": (now + viral.timedelta(days=1)).isoformat(),
        "revoked_at": None,
        "score": {"total": 73, "label": "Хороший"},
        "loss": {
            "estimated": {"recoverable_leads": 7, "lost_revenue_low": 1000, "lost_revenue_high": 2000},
            "assumptions": {"average_check_rub": 999999, "conversion_percent": 87},
            "confirmed": {"leads_per_month": 12345},
            "disclaimer": "Это расчётный диапазон, а не подтверждённая потеря выручки.",
        },
        "reasons": [],
        "cta": {"label": "Проверить свой бизнес", "href": "/diagnostic"},
    }
    monkeypatch.setattr(viral, "_storage_get", lambda account_id, key: stored)
    monkeypatch.setattr(viral, "_rate_limit", lambda *args, **kwargs: None)
    req = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    from fastapi import Response
    out = viral.public_report(token, req, Response())
    assert "loss" not in out
    assert "score" not in out
    assert "market" not in out
    assert "reasons" not in out
    assert out["money"]["lost_revenue_high"] == 2000
    assert "assumptions" not in str(out)
    assert "confirmed" not in str(out)


def test_manual_nurture_respects_opt_out(monkeypatch):
    monkeypatch.setattr(
        viral,
        "_storage_get",
        lambda account_id, key: {"marketing_opt_in": False} if key == "viral_gateway_state" else None,
    )
    called = []
    monkeypatch.setattr(viral, "_queue_nurture_email", lambda **kwargs: called.append(kwargs) or {"id": 1})
    user = SimpleNamespace(email="owner@example.com")
    audit = {"audit_id": "va_test12345", "score": {"total": 61}}
    out = viral._schedule_manual_audit_nurture(user, "acc_test", audit)
    assert out == []
    assert called == []


def test_manual_nurture_schedules_two_idempotent_steps(monkeypatch):
    monkeypatch.setattr(
        viral,
        "_storage_get",
        lambda account_id, key: {"marketing_opt_in": True} if key == "viral_gateway_state" else None,
    )
    called = []
    monkeypatch.setattr(
        viral,
        "_queue_nurture_email",
        lambda **kwargs: called.append(kwargs) or {"id": len(called), "duplicate": False},
    )
    user = SimpleNamespace(email="owner@example.com")
    audit = {"audit_id": "va_test12345", "score": {"total": 61}}
    out = viral._schedule_manual_audit_nurture(user, "acc_test", audit)
    assert len(out) == 2
    assert [x["email_type"] for x in called] == ["connect_avito", "recommendations"]
    assert [x["delay_hours"] for x in called] == [24, 72]
    assert all(x["account_id"] == "acc_test" for x in called)


def test_audit_explainer_uses_existing_score_without_recalculation():
    from app.services.audit_explainer import build_answer
    audit = {
        "score": {
            "total": 57,
            "mode": "connected",
            "breakdown": {
                "response_speed": {"score": 8, "max": 20, "label": "Скорость ответа"},
                "unanswered": {"score": 9, "max": 20, "label": "Клиенты без ответа"},
                "conversion": {"score": 13, "max": 15, "label": "Конверсия"},
            },
        },
        "loss": {"confirmed": {}, "estimated": {}},
        "reasons": [],
    }
    out = build_answer(audit, "Почему такой Score?", [])
    assert "57/100" in out["answer"]
    assert "Скорость ответа: 8/20" in out["answer"]
    assert out["mode"] == "deterministic_audit_answer"


def test_audit_explainer_money_answer_uses_saved_range_and_assumptions():
    from app.services.audit_explainer import build_answer
    audit = {
        "score": {"total": 61, "mode": "connected", "breakdown": {}},
        "loss": {
            "confirmed": {"currently_or_never_unanswered": 8},
            "estimated": {"lost_revenue_low": 70000, "lost_revenue_high": 130000},
            "assumptions": {
                "sales_conversion_percent_declared": 12,
                "average_check_rub_declared": 45000,
            },
        },
        "reasons": [],
    }
    out = build_answer(audit, "Откуда взялась сумма?", [])
    assert "70 000–130 000 ₽" in out["answer"]
    assert "8 проблемных диалогов" in out["answer"]
    assert "12%" in out["answer"]
    assert "45 000 ₽" in out["answer"]


def test_command_parser_routes_audit_questions_without_external_llm():
    from app.services import command_parser
    parsed = command_parser._fast_path("Что BORIS нашёл по моему аудиту?")
    assert parsed is not None
    assert parsed["intent"] == "chit_chat"
    assert parsed["entities"]["topic"] == "audit_help"


def test_main_boris_smalltalk_reads_saved_audit_context():
    from app.services.command_ops import op_smalltalk

    class FakeRow:
        value = '{"score":57,"connected":true,"main_problems":["unanswered","slow_response"]}'

    class FakeQuery:
        def filter(self, *args, **kwargs):
            return self
        def first(self):
            return FakeRow()

    class FakeDb:
        def query(self, *args, **kwargs):
            return FakeQuery()

    user = SimpleNamespace(account_id="acc_test")
    out = op_smalltalk(
        FakeDb(),
        user,
        {"entities": {"topic": "audit_help"}, "account_id": "acc_test"},
    )
    assert "57/100" in out["answer"]
    assert "реальных данных BORIS" in out["answer"]
    assert "клиенты без ответа" in out["answer"]
    assert out["route"] == "/audit"


def test_market_score_is_deterministic_and_bounded():
    from app.services.audit_market.market_score import build_market_score
    wordstat = {
        "status": "ok", "total_count": 4800,
        "top_requests": [{"phrase": "кухни на заказ", "count": 100} for _ in range(8)],
        "confidence": "high",
    }
    avito = {
        "status": "ok", "competing_offers_sample": 12,
        "prices": {"median": 85000},
        "generic_positioning": ["качество"],
        "sample_ads": [{"photos_count": 8}],
    }
    a = build_market_score(wordstat, avito, "Казань")
    b = build_market_score(wordstat, avito, "Казань")
    assert a == b
    assert 0 <= a["total"] <= 100
    assert sum(x["max"] for x in a["breakdown"].values()) == 100
    assert a["confidence"] == "high"


def test_recommended_ads_is_bounded_and_explainable():
    from app.services.audit_market.market_score import recommended_ads
    out = recommended_ads(4800, 27, [{"phrase": str(i), "count": 100} for i in range(8)], "Казань")
    assert 6 <= out["low"] <= out["high"] <= 60
    assert out["reason"]["semantic_clusters"] == 5
    assert out["reason"]["direct_competitors"] == 27


def test_market_engine_partial_when_demand_unavailable(monkeypatch):
    from app.services.audit_market import engine
    monkeypatch.setattr(engine, "collect_wordstat", lambda *a, **k: {
        "status": "unavailable", "confidence": "low"
    })
    monkeypatch.setattr(engine, "collect_avito_market", lambda *a, **k: {
        "status": "ok", "confidence": "medium", "direct_competitors_sample": 4,
        "relevant_ads_sample": 7, "prices": {"median": 10000},
        "generic_positioning": [], "sample_ads": []
    })
    out = engine.run_market_audit("маникюр", "Евпатория", account_id="acc_test")
    assert out["status"] == "partial"
    assert out["market_score"]["total"] >= 0
    assert out["recommended_ads"]["low"] <= out["recommended_ads"]["high"]


def test_audit_run_finish_calculates_duration_and_variable_cost(monkeypatch):
    saved = []
    monkeypatch.setattr(viral, "_storage_put", lambda account_id, key, value: saved.append((account_id, key, value)))
    start = viral._now() - viral.timedelta(milliseconds=250)
    run = {
        "run_id": "ar_test",
        "account_id": "acc_test",
        "kind": "market",
        "status": "running",
        "started_at": start.isoformat(),
        "ai_cost_rub": 0.4,
        "proxy_cost_rub": 0.2,
        "wordstat_cost_rub": 0.1,
        "email_cost_rub": 0.05,
    }
    out = viral._audit_run_finish(run, status="ok", cache_hit=False)
    assert out["duration_ms"] >= 200
    assert out["total_variable_cost_rub"] == 0.75
    assert saved[-1][0] == "viral_audit_run"
    assert saved[-1][1] == "run:ar_test"


def test_owner_costs_aggregates_p50_p95_and_alerts(monkeypatch):
    rows = [
        {"kind":"connected","status":"ok","duration_ms":80,"total_variable_cost_rub":0},
        {"kind":"connected","status":"ok","duration_ms":120,"total_variable_cost_rub":0},
        {"kind":"market","status":"ok","duration_ms":5000,"total_variable_cost_rub":0.5,"cache_hit":False,"browser_success":False,"wordstat_success":False},
        {"kind":"market","status":"ok","duration_ms":150,"total_variable_cost_rub":0,"cache_hit":True,"browser_success":False,"wordstat_success":False},
    ]
    monkeypatch.setattr(viral, "_audit_run_rows", lambda limit=500: rows)
    out = viral.owner_costs(SimpleNamespace(role="owner"))
    assert out["by_kind"]["connected"]["runs"] == 2
    assert out["by_kind"]["connected"]["p50_ms"] == 100.0
    assert out["summary"]["market_cache_hit_rate"] == 50.0
    assert out["summary"]["browser_success_rate"] == 0.0
    assert any(x["code"] == "browser_success_low" for x in out["alerts"])


def test_connected_response_scoring_prefers_p90_over_average():
    # Real-world outliers must not make the whole team look slow.
    assert viral._response_score(14.5) == 17
    assert viral._response_score(119.5) == 4
