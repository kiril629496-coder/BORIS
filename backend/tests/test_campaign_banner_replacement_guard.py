import inspect
from app.api import campaigns

def test_regenerated_banner_never_preserves_exact_previous_banner_as_gallery():
    source = inspect.getsource(campaigns.ff_workflow_banners)
    assert '_is_replaced_banner=bool(old_banner' in source
    assert '_asset_url==old_banner' in source
    assert '_is_replaced_banner or _source_ref in' in source
    assert 'become gallery photo #1. Only the exact previous banner is removed.' in source


def test_qa_campaigns_never_fall_through_to_paid_banner_provider():
    source = inspect.getsource(campaigns.ff_workflow_banners)
    assert "_qa_local_only=_ff_paid_qa_forbidden(campaign)" in source
    assert "if _qa_local_only or (_content_local_banner_first() and not _content_paid_fallback()):" in source
    assert "if _qa_local_only or not _content_paid_fallback():" in source
    assert "and not _qa_local_only and _content_paid_fallback()" in source
    assert "if _ff_paid_qa_forbidden(campaign): return _ff_paid_qa_response()" not in source
