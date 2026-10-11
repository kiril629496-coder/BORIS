from pathlib import Path


def test_nps_db_prefers_runtime_database_url():
    src = Path("app/api/nps.py").read_text(encoding="utf-8")
    start = src.index("def _db():")
    end = src.index("\ndef _state_get", start)
    block = src[start:end]
    assert 'os.environ.get("DATABASE_URL")' in block
    assert block.index('os.environ.get("DATABASE_URL")') < block.index("with open(")
    assert 'FileNotFoundError' in block
