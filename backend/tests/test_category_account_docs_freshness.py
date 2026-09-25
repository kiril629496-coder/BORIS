import datetime as dt

from app.services.category_resolver import _account_docs_cache_fresh_enough


def test_fresh_user_docs_cache_can_be_reused():
    now = dt.datetime(2026, 9, 2, 12, 0, 0)
    fetched = now - dt.timedelta(hours=2)
    fields = [{"tag": "Place", "source": "official_avito_user_docs"}]
    assert _account_docs_cache_fresh_enough(fields, fetched, now=now) is True


def test_live_xmlcheck_learning_forces_user_docs_refresh_even_when_cache_is_fresh():
    now = dt.datetime(2026, 9, 2, 12, 0, 0)
    fetched = now - dt.timedelta(hours=2)
    fields = [
        {"tag": "Place", "source": "official_avito_user_docs"},
        {"tag": "Consultations", "source": "official_avito_xmlcheck", "required": True},
    ]
    assert _account_docs_cache_fresh_enough(fields, fetched, now=now) is False


def test_stale_user_docs_cache_is_not_reused():
    now = dt.datetime(2026, 9, 2, 12, 0, 0)
    fetched = now - dt.timedelta(days=8)
    fields = [{"tag": "Place", "source": "official_avito_user_docs"}]
    assert _account_docs_cache_fresh_enough(fields, fetched, now=now) is False
