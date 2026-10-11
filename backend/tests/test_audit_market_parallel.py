import threading

from app.services.audit_market import engine


def test_market_collectors_start_in_parallel(monkeypatch):
    wordstat_started = threading.Event()
    avito_started = threading.Event()
    overlap = []

    def fake_wordstat(query, region):
        wordstat_started.set()
        overlap.append(avito_started.wait(0.4))
        return {
            "status": "ok",
            "total_count": 552,
            "top_requests": [{"phrase": query, "count": 552}],
            "region_scope": region,
            "requested_region": region,
            "regional_filter_applied": True,
            "confidence": "medium",
            "demand_level": "Средний",
            "checked_at": "2026-10-06T00:00:00+00:00",
            "cost_rub": 0.0,
        }

    def fake_avito(query, region):
        avito_started.set()
        overlap.append(wordstat_started.wait(0.4))
        return {
            "status": "partial",
            "competing_offers_sample": 0,
            "relevant_ads_sample": 0,
            "search_total": None,
            "prices": {},
            "sample_ads": [],
            "confidence": "low",
            "proxy_cost_known": True,
        }

    monkeypatch.setattr(engine, "collect_wordstat", fake_wordstat)
    monkeypatch.setattr(engine, "collect_avito_market", fake_avito)

    result = engine.run_market_audit("кухни на заказ", "Уфа")

    assert result["status"] in {"ok", "partial"}
    assert overlap == [True, True]
