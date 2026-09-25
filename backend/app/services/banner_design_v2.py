"""
BORIS Banner Design Engine V2.

Принцип:
1. AI создаёт только рекламный визуал БЕЗ текста.
2. Русская типографика рисуется Pillow.
3. Служебные названия задач никогда не попадают на баннер.
4. Один вызов = один контрольный баннер.
"""

import hashlib
import os
import re
import time
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter

from banner_generator import (
    generate_ai_image,
    FONT_BOLD,
    FONT_EXTRABOLD,
)


W = 1024
H = 1024


ART = {
    "offer": {
        "name": "Коммерческий",
        "prompt": (
            "premium commercial advertising photography, "
            "clear service context, professional real-world environment, "
            "one strong visual subject, believable materials, "
            "clean composition, visual hierarchy, "
            "large uncluttered negative space on the LEFT for later typography, "
            "subject concentrated mostly on the RIGHT and lower-right area, "
            "realistic light, sophisticated advertising art direction"
        ),
    },
    "price": {
        "name": "Акцент на предложении",
        "prompt": (
            "high-conversion commercial advertising photography, "
            "bold but premium composition, strong visual subject on the RIGHT, "
            "large clean negative space on the LEFT and upper-left, "
            "simple background, premium lighting, "
            "clear service or product context, realistic professional photography"
        ),
    },
    "benefit": {
        "name": "Преимущества",
        "prompt": (
            "editorial commercial advertising photography, "
            "professional service process clearly visible, "
            "real workers or equipment when relevant, believable scene, "
            "subject mostly on the RIGHT half, "
            "clean negative space on the LEFT for typography, "
            "modern premium art direction, realistic lighting"
        ),
    },
}


FORBIDDEN_VISUAL_TEXT = (
    "ABSOLUTELY NO TEXT, NO LETTERS, NO WORDS, NO NUMBERS, "
    "NO TYPOGRAPHY, NO LOGOS, NO WATERMARKS, NO SIGNS WITH WRITING. "
    "Any signs or labels must be blank and unreadable. "
)


def _clean(s, limit):
    s = re.sub(r"\s+", " ", (s or "").strip())
    return s[:limit].strip()


def _hex_rgb(value, fallback=(47, 111, 237)):
    try:
        v = (value or "").strip().lstrip("#")
        if len(v) != 6:
            return fallback
        return tuple(int(v[i:i+2], 16) for i in (0, 2, 4))
    except Exception:
        return fallback


def _font(path, size):
    return ImageFont.truetype(path, max(16, int(size)))


def _text_width(draw, text, font):
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def _wrap_by_width(draw, text, font, max_width):
    words = (text or "").split()
    if not words:
        return []

    lines = []
    current = words[0]

    for word in words[1:]:
        test = current + " " + word
        if _text_width(draw, test, font) <= max_width:
            current = test
        else:
            lines.append(current)
            current = word

    lines.append(current)
    return lines


def _fit_multiline(
    draw,
    text,
    font_path,
    max_width,
    max_lines,
    max_size,
    min_size,
):
    for size in range(max_size, min_size - 1, -2):
        font = _font(font_path, size)
        lines = _wrap_by_width(draw, text, font, max_width)

        if len(lines) <= max_lines:
            return font, lines

    font = _font(font_path, min_size)
    lines = _wrap_by_width(draw, text, font, max_width)

    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and _text_width(
            draw,
            last + "…",
            font,
        ) > max_width:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"

    return font, lines


def _draw_lines(
    draw,
    xy,
    lines,
    font,
    fill,
    gap,
):
    x, y = xy

    for line in lines:
        draw.text(
            (x, y),
            line,
            font=font,
            fill=fill,
        )

        box = draw.textbbox(
            (x, y),
            line,
            font=font,
        )

        y += (box[3] - box[1]) + gap

    return y


def _make_readability_overlay(img):
    """
    Тёмная зона только слева.
    Правая часть с рекламным визуалом остаётся живой.
    """
    overlay = Image.new(
        "RGBA",
        img.size,
        (0, 0, 0, 0),
    )

    px = overlay.load()

    for x in range(W):
        ratio = x / W

        if ratio < 0.50:
            alpha = int(
                205 - ratio / 0.50 * 70
            )
        elif ratio < 0.72:
            alpha = int(
                135 *
                (1 - (ratio - 0.50) / 0.22)
            )
        else:
            alpha = 0

        alpha = max(0, min(220, alpha))

        for y in range(H):
            px[x, y] = (4, 12, 26, alpha)

    return Image.alpha_composite(
        img.convert("RGBA"),
        overlay,
    )


def _draw_accent(draw, accent):
    # Небольшой фирменный элемент,
    # вместо огромных декоративных плашек.
    draw.rounded_rectangle(
        (62, 62, 132, 74),
        radius=6,
        fill=accent + (255,),
    )


def _draw_price(draw, price, accent):
    if not price:
        return

    text = _clean(price, 35)
    font = _font(FONT_EXTRABOLD, 48)

    box = draw.textbbox(
        (0, 0),
        text,
        font=font,
    )

    tw = box[2] - box[0]
    th = box[3] - box[1]

    x = 62
    y = H - 150

    pad_x = 22
    pad_y = 15

    draw.rounded_rectangle(
        (
            x,
            y,
            x + tw + pad_x * 2,
            y + th + pad_y * 2,
        ),
        radius=18,
        fill=accent + (255,),
    )

    draw.text(
        (
            x + pad_x,
            y + pad_y - box[1],
        ),
        text,
        font=font,
        fill=(255, 255, 255, 255),
    )


def compose_banner(
    *,
    account_id,
    description,
    goal,
    title,
    subtitle,
    price_text,
    accent_color,
    output_path,
):
    description = _clean(description, 600)
    title = _clean(title, 85)
    subtitle = _clean(subtitle, 130)
    price_text = _clean(price_text, 35)

    if not description:
        raise ValueError("description required")

    if not title:
        raise ValueError("title required")

    direction = ART.get(
        goal,
        ART["offer"],
    )

    prompt = (
        FORBIDDEN_VISUAL_TEXT
        + "\nCreate ONLY the photographic/visual background for an advertisement. "
        + direction["prompt"]
        + "\nBUSINESS / SERVICE CONTEXT: "
        + description
        + "\nDo not create a poster layout with generated text. "
        + "Do not invent offers, prices, claims or brands. "
        + "The final typography will be added separately by software."
    )

    # Stable paid-artifact identity: retries of the same semantic background
    # must reuse bytes instead of creating a new timestamped paid request.
    _bg_key = hashlib.sha256((str(account_id) + "|v2|gpt-image-1-mini|medium|1024x1024|" + prompt).encode("utf-8", "ignore")).hexdigest()
    tmp = f"/root/BORIS/backend/images/_generated_cache/banner_v2/{_bg_key}.png"

    generated = generate_ai_image(
        prompt,
        tmp,
        size="1024x1024",
        quality="medium",
        model="gpt-image-1-mini",
        account_id=account_id,
        operation="banner_v2_visual_background",
    )

    if not generated or not os.path.isfile(tmp):
        raise RuntimeError(
            "Не удалось создать визуальную основу"
        )

    img = Image.open(tmp).convert("RGB")

    if img.size != (W, H):
        img = img.resize(
            (W, H),
            Image.LANCZOS,
        )

    canvas = _make_readability_overlay(img)

    draw = ImageDraw.Draw(canvas)
    accent = _hex_rgb(accent_color)

    _draw_accent(
        draw,
        accent,
    )

    x = 62
    y = 115
    text_w = 505

    title_font, title_lines = _fit_multiline(
        draw,
        title,
        FONT_EXTRABOLD,
        text_w,
        max_lines=4,
        max_size=72,
        min_size=44,
    )

    y = _draw_lines(
        draw,
        (x, y),
        title_lines,
        title_font,
        (255, 255, 255, 255),
        gap=8,
    )

    if subtitle:
        y += 24

        subtitle_font, subtitle_lines = _fit_multiline(
            draw,
            subtitle,
            FONT_BOLD,
            text_w,
            max_lines=4,
            max_size=31,
            min_size=22,
        )

        _draw_lines(
            draw,
            (x, y),
            subtitle_lines,
            subtitle_font,
            (230, 237, 248, 255),
            gap=8,
        )

    _draw_price(
        draw,
        price_text,
        accent,
    )

    Path(output_path).parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    canvas.convert("RGB").save(
        output_path,
        "PNG",
        optimize=True,
    )

    return {
        "path": output_path,
        "art_direction": direction["name"],
        "title": title,
        "subtitle": subtitle,
        "price_text": price_text,
    }
