import pytest

from app.services.audit_market.source_hint import _validate_url


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000/admin",
    "http://10.0.0.1/internal",
    "http://192.168.1.1/",
    "http://169.254.169.254/latest/meta-data/",
    "file:///etc/passwd",
    "ftp://example.com/file",
    "http://user:pass@example.com/",
])
def test_source_hint_rejects_unsafe_urls(url):
    with pytest.raises(ValueError):
        _validate_url(url)


def test_source_hint_accepts_public_https_literal():
    # Cloudflare public resolver IP is used only as a syntactically public literal;
    # the test performs no HTTP request.
    assert _validate_url("https://1.1.1.1/") == "https://1.1.1.1/"
