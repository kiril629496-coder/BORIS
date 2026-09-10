import inspect
from app.services import prospect_replenisher as r


def test_replenisher_backlog_is_strictly_niche_scoped():
    src=inspect.getsource(r.tick)
    assert "OR c.source='niche_discovery'" not in src
    assert src.count("c.search_query ILIKE '%' || :niche || '%'") >= 2


def test_degraded_search_has_attempt_timestamp_backoff():
    src=inspect.getsource(r.tick)
    assert "last_search_attempt_at" in src
    assert "last_search_attempt=c.get('last_search_attempt_at') or c.get('last_discovery_at')" in src
    assert "SET last_search_attempt_at=NOW(),last_error='search_degraded'" in src


def test_schema_contains_search_attempt_timestamp():
    src=inspect.getsource(r.ensure_schema)
    assert "ADD COLUMN IF NOT EXISTS last_search_attempt_at TIMESTAMP" in src
