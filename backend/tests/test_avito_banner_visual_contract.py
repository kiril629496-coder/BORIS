import inspect
from pathlib import Path

from PIL import Image

from app.api import campaigns
from app.services.avito_premium_compositor import compose_premium_avito_banner
from app.services import media_service


def test_campaign_banner_never_asks_ai_to_render_visible_copy():
    source = inspect.getsource(campaigns.ff_workflow_banners)
    assert "campaign_openai_visual_only" in source
    assert "campaign_openai_reference_visual" in source
    assert "compose_premium_avito_banner" in source
    assert 'banner_text_renderer"]="local_pillow"' in source
    assert "campaign_openai_visual_local_text_v1" in source
    assert "CRITICAL: NO TEXT" in source
    assert "Headline: "+"title" not in source
    assert "campaign_openai_social_banner" not in source


def test_old_campaign_text_in_image_source_is_permanently_blocked_from_media_links():
    assert "campaign_openai_gpt_image_2" in media_service.LEGACY_AVITO_VISUAL_SOURCE_REFS


def test_local_compositor_produces_mobile_square_without_ai_text(tmp_path):
    src = tmp_path / "source.jpg"
    out = tmp_path / "out.jpg"
    Image.new("RGB", (1200, 900), (190, 200, 210)).save(src)
    result = compose_premium_avito_banner(str(src), str(out), "Шкафы купе на заказ")
    assert result["ok"] is True
    assert result["layout"] == "premium_photo_bottom_caption_v4"
    assert result["caption_style"] == "single_translucent_plane_no_gradient"
    assert result["text_renderer"] == "local_pillow"
    assert out.is_file() and out.stat().st_size > 0
    with Image.open(out) as im:
        assert im.size == (1024, 1024)


def test_campaign_banner_tariff_preflight_counts_only_new_paid_rows_and_settles_pending():
    source = inspect.getsource(campaigns.ff_workflow_banners)
    assert 'billing_pending_banners' in source
    assert 'if _paid_count > _remaining' in source
    assert 'requested":_paid_count' in source
    assert 'len(_rows) > _remaining' not in source
    assert '_settle=_billing_consume(_account_for_billing,"banners",_pending_banner_charge,' in source
    assert 'idempotency_key=_pending_banner_billing_key' in source


def test_campaign_materialize_never_advances_with_unsettled_listing_charge():
    source = inspect.getsource(campaigns.ff_workflow_materialize)
    assert 'billing_pending_listings' in source
    assert '_pending_listing_billing_key' in source
    assert 'pending_charge' in source
    assert '_settle=_billing_consume(account_id,"listings",_pending_listing_charge,' in source
    assert 'idempotency_key=_pending_listing_billing_key' in source
    # New rows are stronger than the legacy "step=billing" contract: they are
    # rolled back and never become durable unless the exact tariff intent settles.
    assert 'CAMPAIGN_LISTING_TARIFF_BEFORE_COMMIT_V1' in source
    assert 'db.rollback()' in source
    assert '"created":0,"updated":0,"item_ids":[],"items_total":0' in source


def test_campaign_paid_banner_selection_is_single_account_and_complete():
    source = inspect.getsource(campaigns.ff_workflow_banners)
    assert 'campaign_item_selection_invalid' in source
    assert 'campaign_account_scope_mismatch' in source
    assert '_row_accounts=sorted' in source
    assert '_missing_ids=sorted(wanted-_found_ids)' in source


def test_local_compositor_has_no_long_faded_gradient_overlay():
    source = inspect.getsource(compose_premium_avito_banner)
    assert "for y in range(band_top, 1024)" not in source
    assert "t ** 0.62" not in source
    assert 'gd.rectangle((0, band_top, 1024, 1024)' in source


def test_campaign_banner_uses_dedicated_qa_and_gallery_only_generic_semantics():
    source = inspect.getsource(campaigns.ff_workflow_banners)
    assert "CAMPAIGN_BANNER_DEDICATED_QA_AUTHORITY_V1" in source
    assert "_gallery_planned=list(_planned[1:])" in source
    assert '"checker":"campaign_banner_qa"' in source
    assert "classify_item_media_vision, p,_planned" not in source
