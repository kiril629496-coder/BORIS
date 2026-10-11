import threading

from app.services.audit_market import engine
from app.services.audit_market.query_normalizer import normalize_query, relevance
from app.services.audit_market.market_score import recommended_ads, build_market_score


def test_query_normalization_and_relevance():
    q = normalize_query("  Кухни на заказ  ", "Казань")
    assert q["normalized_query"] == "кухни на заказ"
    assert q["region"] == "Казань"
    assert relevance("Кухни на заказ по размерам", "кухни на заказ") == "direct"
    assert relevance("Фурнитура для шкафов", "кухни на заказ") == "irrelevant"


def test_recommended_ads_is_bounded_and_deterministic():
    top = [{"phrase": f"запрос {i}", "count": 100 + i} for i in range(10)]
    a = recommended_ads(8000, 18, top, "Казань")
    b = recommended_ads(8000, 18, top, "Казань")
    assert a == b
    assert 6 <= a["low"] <= 60
    assert a["low"] < a["high"] <= 60
    assert 1 <= a["clusters"] <= 8


def test_market_score_is_bounded_without_llm():
    wordstat = {
        "status": "ok",
        "total_count": 5000,
        "top_requests": [{"phrase": "кухни", "count": 5000}, {"phrase": "кухни на заказ", "count": 2000}],
    }
    avito = {
        "status": "ok",
        "competing_offers_sample": 10,
        "unique_sellers_sample": 5,
        "prices": {"median": 120000},
        "generic_positioning": ["качество"],
        "sample_ads": [{"photos_count": 8}],
    }
    score = build_market_score(wordstat, avito, "Казань")
    assert 0 <= score["total"] <= 100
    assert score["score_version"] == "market_v1"
    assert sum(x["max"] for x in score["breakdown"].values()) == 100
    assert score["recommended_ads"]["low"] <= score["recommended_ads"]["high"]


def test_market_score_handles_missing_sources_neutrally():
    score = build_market_score(
        {"status": "unavailable"},
        {"status": "partial", "competing_offers_sample": 0, "prices": {}, "sample_ads": []},
        "Евпатория",
    )
    assert 0 < score["total"] < 100
    assert score["confidence"] in {"low", "medium"}
    assert score["recommended_ads"]["low"] >= 6
    assert score["recommended_ads"]["basis"] == "insufficient"
    assert score["recommended_ads"]["competition_grounded"] is False


def test_market_score_demand_only_is_explicitly_preliminary():
    score = build_market_score(
        {
            "status": "ok",
            "total_count": 552,
            "top_requests": [
                {"phrase": "кухни на заказ", "count": 552},
                {"phrase": "кухни на заказ уфа", "count": 350},
                {"phrase": "кухни на заказ недорого", "count": 85},
                {"phrase": "кухни каталог", "count": 40},
            ],
        },
        {
            "status": "partial",
            "competing_offers_sample": 0,
            "relevant_ads_sample": 0,
            "search_total": None,
            "prices": {},
            "sample_ads": [],
        },
        "Уфа",
    )
    assert score["label"] == "Предварительная оценка рынка"
    assert score["recommended_ads"]["basis"] == "demand_only"
    assert score["recommended_ads"]["demand_grounded"] is True
    assert score["recommended_ads"]["competition_grounded"] is False
    assert score["recommended_ads"]["low"] < score["recommended_ads"]["high"] <= 60


def test_market_collectors_start_in_parallel(monkeypatch):
    wordstat_started = threading.Event()
    avito_started = threading.Event()
    both_started = {"wordstat": False, "avito": False}

    def fake_wordstat(query, region):
        wordstat_started.set()
        both_started["wordstat"] = avito_started.wait(timeout=1.0)
        return {
            "status": "ok",
            "total_count": 100,
            "top_requests": [{"phrase": query, "count": 100}],
            "region_scope": region,
            "confidence": "medium",
            "cost_rub": 0,
        }

    def fake_avito(query, region):
        avito_started.set()
        both_started["avito"] = wordstat_started.wait(timeout=1.0)
        return {
            "status": "partial",
            "competing_offers_sample": 0,
            "relevant_ads_sample": 0,
            "prices": {},
            "sample_ads": [],
            "confidence": "low",
        }

    monkeypatch.setattr(engine, "collect_wordstat", fake_wordstat)
    monkeypatch.setattr(engine, "collect_avito_market", fake_avito)
    stages = []
    result = engine.run_market_audit("кухни на заказ", "Уфа", progress=stages.append)

    assert both_started == {"wordstat": True, "avito": True}
    assert stages[0:2] == ["DEMAND_RUNNING", "COMPETITORS_RUNNING"]
    assert "DEMAND_READY" in stages
    assert "COMPETITORS_READY" in stages
    assert stages[-1] == "ANALYSIS_READY"
    assert result["demand"]["total_count"] == 100


def test_market_collector_failure_is_partial_not_global(monkeypatch):
    def broken_wordstat(query, region):
        raise RuntimeError("wordstat unavailable")

    def healthy_avito(query, region):
        return {
            "status": "ok",
            "competing_offers_sample": 3,
            "relevant_ads_sample": 3,
            "unique_sellers_sample": 2,
            "prices": {},
            "sample_ads": [],
            "confidence": "medium",
        }

    monkeypatch.setattr(engine, "collect_wordstat", broken_wordstat)
    monkeypatch.setattr(engine, "collect_avito_market", healthy_avito)
    result = engine.run_market_audit("кухни", "Уфа")

    assert result["status"] == "partial"
    assert result["partial"] is True
    assert result["competitors"]["competing_offers_sample"] == 3
    assert result["demand"]["total_count"] is None


def test_avito_failure_is_partial_not_global(monkeypatch):
    def healthy_wordstat(query, region):
        return {
            "status": "ok",
            "total_count": 240,
            "top_requests": [{"phrase": query, "count": 240}],
            "region_scope": region,
            "confidence": "medium",
            "cost_rub": 0,
        }

    def broken_avito(query, region):
        raise RuntimeError("avito unavailable")

    monkeypatch.setattr(engine, "collect_wordstat", healthy_wordstat)
    monkeypatch.setattr(engine, "collect_avito_market", broken_avito)
    result = engine.run_market_audit("пластиковые окна", "Казань")

    assert result["status"] == "partial"
    assert result["partial"] is True
    assert result["demand"]["total_count"] == 240
    assert result["competitors"]["competing_offers_sample"] == 0
