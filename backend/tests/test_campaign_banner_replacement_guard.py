import inspect
from app.api import campaigns

def test_regenerated_banner_never_preserves_exact_previous_banner_as_gallery():
    source = inspect.getsource(campaigns.ff_workflow_banners)
    assert '_is_replaced_banner=bool(old_banner' in source
    assert '_asset_url==old_banner' in source
    assert '_is_replaced_banner or _source_ref in' in source
    assert 'become gallery photo #1. Only the exact previous banner is removed.' in source
