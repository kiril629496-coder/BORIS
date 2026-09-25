from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _function_source(path: Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(
        n for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
    )
    lines = source.splitlines()
    return "\n".join(lines[node.lineno - 1: node.end_lineno])


def test_social_due_uses_current_account_tenant_scope():
    body = _function_source(ROOT / "posting_runner.py", "due_posts_all")
    assert "tenant_scope=account_id" not in body
    assert body.count("tenant_scope=acc") == 2


def test_cpx_getbids_transport_is_wait_external_without_exception_text():
    body = _function_source(ROOT / "app/api/cpx_advisor.py", "apply_one")
    marker = "CPX_PROVIDER_TRANSPORT_WAIT_V1"
    assert marker in body
    section = body[body.index(marker): body.index("if r.status_code == 429")]
    assert "except _httpx.RequestError as exc" in body
    assert '"dependency_state": "waiting_external"' in section
    assert '"changed_avito": False' in section
    assert "str(exc)" not in section


def test_social_transport_error_redacts_credentials():
    source = (ROOT / "run/social_transport_readiness_v3.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "_safe_transport_error"
    )
    ns = {"re": re}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "<safe-error-test>", "exec"), ns)

    class FakeError(Exception):
        pass

    out = ns["_safe_transport_error"](
        FakeError(
            "url=/bot123456:FAKE_SECRET/getMe?access_token=FAKE_TOKEN "
            "Authorization Bearer FAKE_BEARER"
        ),
        500,
    )
    assert "FAKE_SECRET" not in out
    assert "FAKE_TOKEN" not in out
    assert "FAKE_BEARER" not in out
    assert "<redacted>" in out


def test_calltracking_never_inherits_ambient_proxy():
    source = (ROOT / "app/api/calltracking.py").read_text(encoding="utf-8")
    assert "CALLTRACKING_DIRECT_TRANSPORT_V1" in source
    assert "_CALLTRACKING_HTTP.trust_env = False" in source
    assert '"avito.calltracking", requests.post,' not in source
    assert '"avito.calltracking", requests.get,' not in source
    assert '"avito.calltracking", _CALLTRACKING_HTTP.post,' in source
    assert '"avito.calltracking", _CALLTRACKING_HTTP.get,' in source


def test_sales_ai_never_inherits_ambient_proxy():
    source = (ROOT / "app/services/sales_ai_router.py").read_text(encoding="utf-8")
    assert "SALES_AI_AMBIENT_PROXY_BYPASS_V1" in source
    assert source.count("_session.trust_env = False") >= 2


def test_browser_gateway_contract_matches_six_file_builder():
    contract = (ROOT / "production_contract_runner.py").read_text(encoding="utf-8")
    builder = (ROOT.parent / "frontend/scripts/build-browser-gateway.sh").read_text(encoding="utf-8")
    required = ["manifest.json", "background.js", "popup.html", "popup.js", "offscreen.html", "offscreen.js"]
    for name in required:
        assert name in contract
        assert name in builder
    assert 'gw_files=["manifest.json","background.js","popup.html","popup.js","offscreen.html","offscreen.js"]' in contract


def test_backend_release_includes_social_owner_policy_authority_file():
    source = (ROOT / "scripts/build_backend_release.py").read_text(encoding="utf-8")
    assert "def _release_support_files" in source
    assert 'SOURCE / "run" / "social_owner_policy_v3.canonical"' in source
    assert 'SOURCE / "run" / "social_owner_preflight.canonical.py"' in source
    assert "support_files = list(_release_support_files())" in source
    assert "app_files + root_files + support_files" in source


def test_social_generation_continues_on_healthy_sibling_transport():
    body = _function_source(ROOT / "boris_background_worker.py", "_social_generation_transport_state")
    ns = {}
    exec(body, ns)
    decide = ns["_social_generation_transport_state"]
    transport = {
        "telegram": {"ready": True},
        "vk": {"ready": False, "dependency_state": "waiting_external", "retryable_external": True},
    }
    both = decide({"platforms": "both"}, transport, True)
    assert both["ready"] is True
    assert both["partial"] is True
    assert both["available"] == ["tg"]
    assert both["deferred"] == ["vk"]
    assert both["reason"] is None

    vk_only = decide({"platforms": "vk"}, transport, True)
    assert vk_only["ready"] is False
    assert vk_only["reason"] == "transport_permission_not_ready"

    stale = decide({"platforms": "both"}, transport, False)
    assert stale["ready"] is False
    assert stale["reason"] == "transport_readiness_stale_or_missing"


def test_published_media_banner_expansion_reconcile_is_narrow_and_fail_closed():
    source = (ROOT / "app/services/media_service.py").read_text(encoding="utf-8")
    marker = "PUBLISHED_BANNER_PLACEMENT_REVIEW_RECONCILE_V1"
    assert marker in source
    section = source[source.index(marker): source.index('if not codes or not codes.issubset(locally_repairable_codes)')]
    assert 'codes == {"media_semantic_placement_count_drift"}' in section
    assert "image_count == expected_placements + 1" in section
    assert "len(payload_keys) == image_count" in section
    assert "len(linked_for_layout) == image_count" in section
    assert "current_keys == wanted_keys" in section
    assert 'banner_qa.get("passed") is True' in section
    assert "wanted_keys[0][1] == banner_key" in section
    assert 'review["placements"] = image_count' in section


def test_published_media_reuses_exact_family_asset_proof_before_paid_vision():
    source = (ROOT / "app/services/media_service.py").read_text(encoding="utf-8")
    marker = "PUBLISHED_MEDIA_ASSET_FAMILY_CACHE_REUSE_V1"
    assert marker in source
    section = source[source.index(marker): source.index('if chosen is not None:', source.index(marker))]
    assert "_asset_family_cache(asset)" in section
    assert "cached_family == family" in section
    assert "float(cached_conf or 0) >= 0.80" in section
    assert '"checker": "openai_vision_family_cache"' in section


def test_vision_fallback_is_safe_counted_and_never_fans_out_from_uncertain_transport():
    source = (ROOT / "app/services/media_semantic_validator.py").read_text(encoding="utf-8")
    marker = "VISION_SAFE_PROVIDER_FALLBACK_V1"
    assert marker in source
    assert 'safe_primary_reject = primary_status in {401, 402, 403, 404, 429}' in source
    assert 'use_fallback = bool(fallback_key and (not api_key or safe_primary_reject))' in source
    assert 'provider_name = "plusvibe"' in source
    assert 'provider_model = fallback_model' in source
    assert 'fallback_session.trust_env = False' in source
    assert 'provider_scope="((provider=\'openai\' and model=\'gpt-4o-mini\') or (provider=\'plusvibe\' and model=:fm))"' in source
    # Transport uncertainty is quarantined before the safe fallback decision.
    uncertain = source.index('_uncertain_write("vision_transport_outcome_unknown")')
    fallback = source.index(marker)
    assert uncertain < fallback


def test_published_media_403_is_durable_external_wait_not_retry_burst():
    source = (ROOT / "app/services/media_service.py").read_text(encoding="utf-8")
    marker = "PUBLISHED_MEDIA_PROVIDER_ACCESS_BACKOFF_V1"
    assert marker in source
    block = source[source.index(marker):source.index(marker) + 2600]
    assert "provider_http_status in {401, 403}" in block
    assert "cooldown_seconds = 900 if is_429 else 21600" in block
    assert '"provider_access_wait"' in block
    assert '"waiting_external"' in block
    assert "if is_429 or is_provider_access_block:" in block


def test_lead_radar_provider_backoff_is_wait_external_not_false_self_heal():
    health = (ROOT / "app/services/lead_radar/health.py").read_text(encoding="utf-8")
    adapters = (ROOT / "app/services/control_plane_adapters.py").read_text(encoding="utf-8")
    assert "LEAD_RADAR_EXTERNAL_BACKOFF_TRUTH_V1" in health
    assert "sources_waiting_external" in health
    assert "status='WAIT_EXTERNAL'" in health
    assert 'state=="WAIT_EXTERNAL"' in adapters
    assert '"PROSPECTING_PROVIDER_BACKOFF"' in adapters
    assert '"health":"waiting_external"' in adapters


def test_plastering_family_wins_over_generic_paint_token():
    import app.services.media_semantic_validator as V
    payload = {
        "title": "Штукатурка стен под покраску в Сочи качественно и быстро",
        "direction": "Механизированная штукатурка стен",
        "category": "Предложение услуг",
    }
    assert V._semantic_family(payload) == "plastering_service"
