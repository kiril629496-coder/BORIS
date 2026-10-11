import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUTH_SOURCE = ROOT / "app/api/auth.py"


def _access_source():
    return AUTH_SOURCE.read_text(encoding="utf-8")


def test_expired_campaign_reads_are_exempt_only_after_tenant_grant():
    source = _access_source()
    tree = ast.parse(source)
    funcs = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "check_account_access"]
    assert len(funcs) == 1
    fn = funcs[0]
    body = ast.get_source_segment(source, fn)
    assert body is not None

    # The exemption is narrowly bound to account-scoped Campaign GET paths.
    assert 'request.method == "GET"' in body
    assert 'request.url.path.startswith("/api/campaign/")' in body
    assert "_read_only_campaign" in body

    # Tenant ownership/access must still be proven before the exemption is reached.
    assert body.index("_granted") < body.index("_read_only_campaign")
    assert 'if not _read_only_campaign and not bool(_subscription.get("allowed"))' in body


def test_expired_campaign_write_paths_are_not_exempted():
    source = _access_source()
    assert 'if not _read_only_campaign and not bool(_subscription.get("allowed"))' in source
    assert 'request.method == "GET"' in source
    assert 'request.url.path.startswith("/api/campaign/")' in source
