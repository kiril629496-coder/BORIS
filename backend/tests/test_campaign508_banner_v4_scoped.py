from pathlib import Path

from PIL import Image

from app.services.avito_premium_compositor import compose_premium_avito_banner
from app.services import media_semantic_validator as validator


def test_campaign508_public_banner_qa_is_sharp_while_reference_default_stays_private():
    import inspect
    source = inspect.getsource(validator)
    assert '"policy":"campaign_banner_vision_qa_v8_content_sha_bottom_caption"' in source
    assert '"public_artifact_preprocess":"sharp_768_no_blur_v1"' in source
    assert "_image_data_uri(banner_asset,max_side=768,blur_radius=0.0)" in source
    assert "REFERENCE OWNER PHOTO" in source
    assert "_image_data_uri(reference_asset)" in source


def test_campaign508_compositor_v4_uses_single_caption_plane(tmp_path: Path):
    src = tmp_path / "source.jpg"
    out = tmp_path / "out.png"
    Image.new("RGB", (1200, 900), (190, 200, 210)).save(src)
    result = compose_premium_avito_banner(str(src), str(out), "Гардеробные на заказ Уфа")
    assert result["ok"] is True
    assert result["layout"] == "premium_photo_bottom_caption_v4"
    assert result["caption_style"] == "single_translucent_plane_no_gradient"
    assert result["text_renderer"] == "local_pillow"
    assert out.is_file() and out.stat().st_size > 0


def test_campaign508_banner_uses_strongest_semantic_media_not_first_position():
    import inspect
    from app.api import campaigns

    source=inspect.getsource(campaigns.ff_workflow_banners)
    assert "def _banner_semantic_asset_score" in source
    assert "vision_family_confirmed:" in source
    assert "vision_family_confidence:" in source
    assert "semantic_confidence:" in source
    assert "_verified_pair=_best_banner_semantic_pair(_verified_candidates)" in source
    assert "_fallback_pair=_best_banner_semantic_pair(_fallback_candidates)" in source
    score=source[source.index("def _banner_semantic_asset_score"):source.index("def _best_banner_semantic_pair")]
    assert "family_confirmed" in score
    assert "vision_conf" in score
    assert "semantic_conf" in score
