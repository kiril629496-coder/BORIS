from pathlib import Path

from PIL import Image, ImageDraw

from app.services.avito_premium_compositor import compose_premium_avito_banner


def _noisy_zone(path: Path, noisy: str):
    im = Image.new("RGB", (1024, 1024), (220, 224, 230))
    draw = ImageDraw.Draw(im)
    if noisy == "bottom":
        y0, y1 = 700, 982
    else:
        y0, y1 = 42, 324
    for y in range(y0, y1, 12):
        for x in range(38, 986, 12):
            if ((x // 12) + (y // 12)) % 2:
                draw.rectangle((x, y, min(x + 8, 985), min(y + 8, y1 - 1)), fill=(25, 35, 45))
    im.save(path)


def test_v6_keeps_exact_verified_title(tmp_path):
    src = tmp_path / "src.png"
    out = tmp_path / "out.png"
    Image.new("RGB", (1024, 1024), (205, 215, 225)).save(src)

    title = "Остекление балконов и лоджий"
    result = compose_premium_avito_banner(str(src), str(out), title)

    assert result["ok"] is True
    assert result["display_title"] == title
    assert result["source_title"] == title
    assert result["visible_word_count"] == len(title.split())
    assert result["line_count"] in (1, 2)
    assert result["font_size"] >= 48
    assert result["text_policy"] == "exact_verified_title_two_lines_min48_adaptive_zone"
    assert out.is_file()


def test_v6_does_not_drop_title_tail(tmp_path):
    src = tmp_path / "src.png"
    out = tmp_path / "out.png"
    Image.new("RGB", (1024, 1024), (210, 210, 210)).save(src)

    title = "Нестандартные окна и конструкции"
    result = compose_premium_avito_banner(str(src), str(out), title)

    assert result["ok"] is True
    assert result["display_title"] == title
    assert result["display_title"].endswith("конструкции")


def test_v6_chooses_top_when_bottom_is_busier(tmp_path):
    src = tmp_path / "bottom_busy.png"
    out = tmp_path / "out.png"
    _noisy_zone(src, "bottom")

    result = compose_premium_avito_banner(str(src), str(out), "Панорамные окна")

    assert result["ok"] is True
    assert result["caption_zone"] == "top"
    assert result["caption_zone_top"] == 42
    assert result["top_edge_density"] < result["bottom_edge_density"]


def test_v6_chooses_bottom_when_top_is_busier(tmp_path):
    src = tmp_path / "top_busy.png"
    out = tmp_path / "out.png"
    _noisy_zone(src, "top")

    result = compose_premium_avito_banner(str(src), str(out), "Панорамные окна")

    assert result["ok"] is True
    assert result["caption_zone"] == "bottom"
    assert result["caption_zone_top"] == 700
    assert result["bottom_edge_density"] < result["top_edge_density"]


def test_v6_output_is_mobile_square(tmp_path):
    src = tmp_path / "src.jpg"
    out = tmp_path / "out.png"
    Image.new("RGB", (1400, 900), (190, 200, 210)).save(src)

    result = compose_premium_avito_banner(str(src), str(out), "Окна в пол")

    assert result["ok"] is True
    assert result["layout"] == "premium_photo_adaptive_glass_caption_v6"
    assert result["caption_style"] == "single_light_frosted_card_adaptive"
    with Image.open(out) as im:
        assert im.size == (1024, 1024)
