from __future__ import annotations

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageStat


def _font(size: int):
    for p in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ):
        if Path(p).is_file():
            return ImageFont.truetype(p, size=size)
    return ImageFont.load_default()


def _crop_square(im: Image.Image, size: int = 1024) -> Image.Image:
    im = im.convert("RGB")
    w, h = im.size
    side = min(w, h)
    left = max(0, (w - side) // 2)
    top = max(0, (h - side) // 2)
    return im.crop((left, top, left + side, top + side)).resize((size, size), Image.Resampling.LANCZOS)


def _edge_density(im: Image.Image, box) -> float:
    crop = im.crop(box).convert("L").resize((256, 256), Image.Resampling.BILINEAR)
    edges = crop.filter(ImageFilter.FIND_EDGES)
    return float(ImageStat.Stat(edges).mean[0])


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int, max_lines: int = 3):
    words = [w for w in str(text or "").split() if w]
    lines: list[str] = []
    cur = ""
    for word in words:
        test = (cur + " " + word).strip()
        width = draw.textbbox((0, 0), test, font=font)[2]
        if width <= max_width or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = word
            if len(lines) >= max_lines - 1:
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    consumed = " ".join(lines)
    if consumed != " ".join(words):
        # Keep content honest but compact; never add marketing copy.
        tail_words = words[len(consumed.split()):]
        if tail_words and lines:
            candidate = (lines[-1] + " " + " ".join(tail_words)).strip()
            while draw.textbbox((0, 0), candidate + "…", font=font)[2] > max_width and " " in candidate:
                candidate = candidate.rsplit(" ", 1)[0]
            lines[-1] = candidate + "…"
    return lines


def compose_premium_avito_banner(source_path: str, output_path: str, title: str) -> dict:
    """Create a mobile-first premium Avito banner from a verified real photo.

    V3 keeps the product/photo dominant and reserves only a bottom caption zone
    for one bold verified headline. This avoids covering the main object and gives
    the mobile card an obvious visual hierarchy: photo first, message second.
    """
    src = Image.open(source_path).convert("RGB")
    canvas = _crop_square(src, 1024)

    # Keep the source photograph full-bleed. Only a very light cinematic grade is
    # applied globally; no blur, collage, fake object or decorative card is added.
    rgba = canvas.convert("RGBA")
    grade = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(grade)
    gd.rectangle((0, 0, 1024, 1024), fill=(4, 9, 18, 16))

    # Bottom-only readability field. The upper ~65% stays unobstructed so the
    # shower/product remains the dominant subject. The fade avoids a hard flyer box.
    band_top = 635
    for y in range(band_top, 1024):
        t = (y - band_top) / max(1, 1024 - band_top - 1)
        alpha = int(228 * (t ** 0.62))
        gd.line((0, y, 1024, y), fill=(5, 12, 26, alpha), width=1)
    rgba = Image.alpha_composite(rgba, grade)
    draw = ImageDraw.Draw(rgba)

    source_title = " ".join(str(title or "").split())
    words = source_title.split()
    if not words:
        return {"ok": False, "error": "EMPTY_TITLE", "font_size": 0, "line_count": 0}

    # Visible copy is a complete semantic prefix of the verified listing title.
    # Never end a cropped headline on a dangling preposition/conjunction.
    dangling_tail = {"в", "во", "на", "для", "с", "со", "без", "под", "по", "из", "от", "до", "к", "за", "при", "и", "или"}
    max_width = 900
    display_title = ""
    lines = []
    font_size = 0
    font = _font(72)
    for word_count in range(min(4, len(words)), 0, -1):
        candidate_words = words[:word_count]
        if candidate_words and candidate_words[-1].casefold().strip(".,:;—–-") in dangling_tail:
            continue
        candidate = " ".join(candidate_words).strip()
        for size in range(72, 57, -2):
            candidate_font = _font(size)
            candidate_lines = _wrap(draw, candidate, candidate_font, max_width, 2)
            exact = " ".join(candidate_lines).replace("…", "") == candidate
            if exact and len(candidate_lines) <= 2 and not any("…" in ln for ln in candidate_lines):
                display_title = candidate
                lines = candidate_lines
                font_size = size
                font = candidate_font
                break
        if display_title:
            break
    if not display_title:
        return {"ok": False, "error": "MOBILE_TEXT_GEOMETRY_FAILED", "font_size": 0, "line_count": 0}

    boxes = [draw.textbbox((0, 0), ln, font=font) for ln in lines]
    heights = [max(1, b[3] - b[1]) for b in boxes]
    gap = 14
    total_h = sum(heights) + gap * max(0, len(lines) - 1)
    x = 60
    # Place the block entirely inside the caption zone with generous margins.
    y = max(720, 1024 - 82 - total_h)
    accent_y = max(674, y - 42)
    draw.rounded_rectangle((x, accent_y, x + 108, accent_y + 14), radius=7, fill=(96, 140, 255, 255))
    for idx, ln in enumerate(lines):
        draw.text((x + 3, y + 4), ln, font=font, fill=(0, 0, 0, 150))
        draw.text((x, y), ln, font=font, fill=(255, 255, 255, 255))
        y += heights[idx] + gap

    # Mobile contract: never silently emit tiny or overlong copy.
    if font_size < 58 or len(lines) > 2 or not lines:
        return {"ok": False, "error": "MOBILE_TEXT_GEOMETRY_FAILED", "font_size": font_size, "line_count": len(lines)}

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    rgba.convert("RGB").save(output_path, "PNG", optimize=True)
    return {
        "ok": True,
        "layout": "premium_photo_bottom_caption_v3",
        "source_path": source_path,
        "output_path": output_path,
        "font_size": font_size,
        "line_count": len(lines),
        "display_title": display_title,
        "source_title": source_title,
        "visible_word_count": len(display_title.split()),
        "text_policy": "complete_title_prefix_max4_two_lines_min58",
        "text_renderer": "local_pillow",
        "caption_zone_top": band_top,
    }
