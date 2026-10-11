import base64
from unittest.mock import patch

import proxy_pool
from app.services import openai_image_transport as transport


class _Resp:
    status_code = 200
    headers = {"x-request-id": "req-test"}
    def json(self):
        return {
            "data": [{"b64_json": base64.b64encode(b"image-bytes").decode()}],
            "usage": {},
            "size": "1024x1024",
            "quality": "medium",
        }


class _Session:
    def __init__(self):
        self.trust_env = True
        self.proxies = {}
        self.post = object()


def test_openai_image_transport_uses_explicit_intl_proxy():
    sess = _Session()
    captured = {}

    def _dep(name, fn, url, **kwargs):
        captured["name"] = name
        captured["fn"] = fn
        captured["url"] = url
        captured["session"] = sess
        return _Resp()

    with patch.object(transport.requests, "Session", return_value=sess), \
         patch.object(proxy_pool, "get_intl_requests_proxies", return_value={
             "http": "http://proxy.test:10000",
             "https": "http://proxy.test:10000",
         }), \
         patch.object(transport, "dependency_call", side_effect=_dep), \
         patch.object(transport.os, "getenv", return_value="sk-test"):
        out = transport.generate_image_http(
            "preview prompt",
            quality="medium",
            account_id="acct",
            idempotency_key="idem-1",
        )

    assert out["ok"] is True
    assert sess.trust_env is False
    assert sess.proxies["https"] == "http://proxy.test:10000"
    assert captured["name"] == "openai.images"
    assert captured["url"] == transport.OPENAI_IMAGES_URL
    assert captured["fn"] is sess.post
