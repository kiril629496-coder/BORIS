"""
BORIS Banner Design System V3.

AI:
    создаёт ТОЛЬКО рекламную сцену без текста.

BORIS:
    выбирает композицию;
    сокращает копирайт;
    рисует русский текст;
    рисует векторные иконки;
    собирает преимущества;
    контролирует safe-zones и переполнение.

Stage V2 не изменяется и остаётся fallback.
"""

import hashlib
import os
import re
import time
from difflib import SequenceMatcher
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from banner_generator import (
    generate_ai_image,
    FONT_BOLD,
    FONT_EXTRABOLD,
)

FONT_BLACK = "/root/BORIS/backend/fonts/Montserrat-Black.ttf"

W = 1024
H = 1024


FORBIDDEN_IMAGE_TEXT = (
    "ABSOLUTELY NO TEXT. "
    "NO LETTERS. NO WORDS. NO NUMBERS. "
    "NO LOGOS. NO WATERMARKS. "
    "NO WRITTEN SIGNS. NO LABELS. "
    "NO POSTER DESIGN. NO TYPOGRAPHY. "
    "Generate only the photographic advertising scene. "
)


CONSTRUCTION_WORDS = (
    "штукатур",
    "ремонт",
    "строит",
    "монтаж",
    "отделк",
    "плитк",
    "фасад",
    "электр",
    "сантех",
    "покраск",
)

PREMIUM_WORDS = (
    "премиум",
    "дизайн",
    "дизайнер",
    "эксклюзив",
    "индивидуаль",
    "интерьер",
)

PRICE_WORDS = (
    "цена",
    "скид",
    "акци",
    "руб",
    "₽",
    "от ",
)


def _clean(value, limit=500):
    value = re.sub(
        r"\s+",
        " ",
        (value or "").strip(),
    )
    return value[:limit].strip()


def _words(text):
    return [
        x
        for x in re.split(r"\s+", _clean(text))
        if x
    ]


def _shorten_words(text, maximum):
    words = _words(text)

    if len(words) <= maximum:
        return " ".join(words)

    return " ".join(words[:maximum]).rstrip(" ,.-")


def _similar(a, b):
    a = _clean(a).lower()
    b = _clean(b).lower()

    if not a or not b:
        return 0.0

    return SequenceMatcher(None, a, b).ratio()


def _sentences(description):
    return [
        _clean(x, 160)
        for x in re.split(
            r"(?<=[.!?])\s+|\n+",
            description or "",
        )
        if _clean(x)
    ]


def _fallback_title(description):
    parts = _sentences(description)

    raw = parts[0] if parts else description

    raw = raw.rstrip(".!?")

    return _shorten_words(raw, 7)[:62]


def _fallback_subtitle(description, title):
    parts = _sentences(description)

    for part in parts[1:]:
        candidate = part.rstrip(".!?")

        if _similar(candidate, title) < 0.65:
            return _shorten_words(
                candidate,
                8,
            )[:78]

    return ""


def _extract_fact_chips(description, title="", subtitle=""):
    """
    Только фразы из пользовательского описания.
    Никаких придуманных преимуществ.
    """
    source = _clean(description, 700)

    sentences = _sentences(source)

    candidates = []

    for sentence in sentences[1:]:
        sentence = sentence.rstrip(".!?")

        pieces = [
            _clean(x, 80)
            for x in sentence.split(",")
            if _clean(x)
        ]

        if len(pieces) <= 1:
            pieces = [sentence]

        for piece in pieces:
            piece = piece.strip(" .;:-")

            wc = len(_words(piece))

            if wc < 1 or wc > 8:
                continue

            if _similar(piece, title) > 0.72:
                continue

            if _similar(piece, subtitle) > 0.78:
                continue

            candidates.append(piece[:52])

    result = []

    for item in candidates:
        key = item.lower()

        if key in {
            x.lower()
            for x in result
        }:
            continue

        result.append(item)

        if len(result) == 3:
            break

    return result


def normalize_copy(
    *,
    description,
    goal,
    generated,
):
    description = _clean(description, 700)

    generated = (
        generated
        if isinstance(generated, dict)
        else {}
    )

    title = _clean(
        generated.get("title"),
        100,
    )

    subtitle = _clean(
        generated.get("subtitle"),
        140,
    )

    banned = {
        "показать предложение",
        "сделать акцент на цене",
        "показать преимущество",
        "показать преимущества",
        "главное коммерческое предложение",
        "ключевое преимущество",
    }

    title_bad = (
        not title
        or title.lower() in banned
        or len(_words(title)) > 7
        or len(title) > 62
        or _similar(title, description) > 0.76
    )

    if title_bad:
        title = _fallback_title(
            description
        )

    title = _shorten_words(
        title,
        7,
    )[:62]

    subtitle_bad = (
        not subtitle
        or len(_words(subtitle)) > 9
        or len(subtitle) > 78
        or _similar(
            subtitle,
            description,
        ) > 0.82
        or _similar(
            subtitle,
            title,
        ) > 0.74
    )

    if subtitle_bad:
        subtitle = _fallback_subtitle(
            description,
            title,
        )

    subtitle = _shorten_words(
        subtitle,
        9,
    )[:78]

    # Преимущества — только из исходного фактического текста.
    advantages = _extract_fact_chips(
        description,
        title,
        subtitle,
    )

    price = _clean(
        generated.get("price_text"),
        35,
    )

    # Цена разрешена только если пользователь реально
    # указал цифры/ценовое условие в исходном описании.
    desc_digits = set(
        re.findall(r"\d+", description)
    )

    price_digits = set(
        re.findall(r"\d+", price)
    )

    if not (
        price
        and price_digits
        and price_digits.issubset(
            desc_digits
        )
    ):
        price = ""

    return {
        "title": title,
        "subtitle": subtitle,
        "advantages": advantages,
        "price_text": price,
        "goal": goal or "offer",
    }


def choose_layout(
    *,
    description,
    goal,
    price_text="",
):
    text = _clean(
        description
    ).lower()

    if (
        goal == "price"
        and price_text
    ):
        return "offer"

    if any(
        word in text
        for word in CONSTRUCTION_WORDS
    ):
        return "expert"

    if any(
        word in text
        for word in PREMIUM_WORDS
    ):
        return "premium"

    return "commercial"


def _hex(value, fallback=(47, 111, 237)):
    try:
        value = (
            value or ""
        ).strip().lstrip("#")

        if len(value) != 6:
            return fallback

        return tuple(
            int(
                value[i:i + 2],
                16,
            )
            for i in (0, 2, 4)
        )

    except Exception:
        return fallback


def _font(path, size):
    return ImageFont.truetype(
        path,
        int(size),
    )


def _width(draw, text, font):
    box = draw.textbbox(
        (0, 0),
        text,
        font=font,
    )

    return box[2] - box[0]


def _wrap(
    draw,
    text,
    font,
    max_width,
):
    words = _words(text)

    if not words:
        return []

    lines = []
    current = words[0]

    for word in words[1:]:
        trial = current + " " + word

        if (
            _width(
                draw,
                trial,
                font,
            )
            <= max_width
        ):
            current = trial

        else:
            lines.append(current)
            current = word

    lines.append(current)

    return lines


def _fit(
    draw,
    text,
    *,
    font_path,
    width,
    max_lines,
    max_size,
    min_size,
):
    for size in range(
        max_size,
        min_size - 1,
        -2,
    ):
        font = _font(
            font_path,
            size,
        )

        lines = _wrap(
            draw,
            text,
            font,
            width,
        )

        if len(lines) <= max_lines:
            return font, lines

    font = _font(
        font_path,
        min_size,
    )

    lines = _wrap(
        draw,
        text,
        font,
        width,
    )[:max_lines]

    if lines:
        last = lines[-1]

        while (
            last
            and _width(
                draw,
                last + "…",
                font,
            )
            > width
        ):
            last = last[:-1]

        lines[-1] = (
            last.rstrip() + "…"
        )

    return font, lines


def _draw_lines(
    draw,
    x,
    y,
    lines,
    font,
    fill,
    gap=6,
):
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

        y += (
            box[3]
            - box[1]
            + gap
        )

    return y


###############################################################################
# VECTOR ICONS
###############################################################################

def _icon_check(draw, cx, cy, r, color):
    draw.ellipse(
        (
            cx-r,
            cy-r,
            cx+r,
            cy+r,
        ),
        outline=color,
        width=3,
    )

    draw.line(
        (
            cx-r*0.45,
            cy,
            cx-r*0.08,
            cy+r*0.38,
            cx+r*0.52,
            cy-r*0.42,
        ),
        fill=color,
        width=4,
        joint="curve",
    )


def _icon_home(draw, cx, cy, r, color):
    draw.line(
        (
            cx-r*0.65,
            cy-r*0.05,
            cx,
            cy-r*0.65,
            cx+r*0.65,
            cy-r*0.05,
        ),
        fill=color,
        width=4,
        joint="curve",
    )

    draw.rectangle(
        (
            cx-r*0.47,
            cy-r*0.05,
            cx+r*0.47,
            cy+r*0.58,
        ),
        outline=color,
        width=4,
    )


def _icon_tool(draw, cx, cy, r, color):
    draw.line(
        (
            cx-r*0.55,
            cy+r*0.52,
            cx+r*0.45,
            cy-r*0.48,
        ),
        fill=color,
        width=5,
    )

    draw.ellipse(
        (
            cx+r*0.2,
            cy-r*0.68,
            cx+r*0.67,
            cy-r*0.21,
        ),
        outline=color,
        width=4,
    )


def _icon_star(draw, cx, cy, r, color):
    import math

    points = []

    for i in range(10):
        a = (
            -math.pi / 2
            + i * math.pi / 5
        )

        rr = (
            r * 0.65
            if i % 2 == 0
            else r * 0.28
        )

        points.append(
            (
                cx + rr * math.cos(a),
                cy + rr * math.sin(a),
            )
        )

    draw.polygon(
        points,
        outline=color,
    )


def _icon_clock(draw, cx, cy, r, color):
    draw.ellipse(
        (
            cx-r*0.62,
            cy-r*0.62,
            cx+r*0.62,
            cy+r*0.62,
        ),
        outline=color,
        width=4,
    )

    draw.line(
        (
            cx,
            cy,
            cx,
            cy-r*0.35,
        ),
        fill=color,
        width=4,
    )

    draw.line(
        (
            cx,
            cy,
            cx+r*0.3,
            cy+r*0.18,
        ),
        fill=color,
        width=4,
    )


def _icon_kind(text):
    t = _clean(text).lower()

    if any(
        x in t
        for x in (
            "квартир",
            "дом",
            "новостро",
        )
    ):
        return "home"

    if any(
        x in t
        for x in (
            "механиз",
            "монтаж",
            "ремонт",
            "штукатур",
            "работ",
        )
    ):
        return "tool"

    if any(
        x in t
        for x in (
            "срок",
            "день",
            "час",
            "быстро",
        )
    ):
        return "clock"

    if any(
        x in t
        for x in (
            "качест",
            "ровн",
            "премиум",
        )
    ):
        return "star"

    return "check"


def _draw_icon(
    draw,
    kind,
    cx,
    cy,
    r,
    color,
):
    fn = {
        "home": _icon_home,
        "tool": _icon_tool,
        "clock": _icon_clock,
        "star": _icon_star,
        "check": _icon_check,
    }.get(
        kind,
        _icon_check,
    )

    fn(
        draw,
        cx,
        cy,
        r,
        color,
    )


###############################################################################
# BACKGROUND PROMPT
###############################################################################

def _scene_prompt(
    *,
    description,
    layout,
):
    context = _clean(
        description,
        600,
    )

    base = (
        FORBIDDEN_IMAGE_TEXT
        + "Create a premium realistic commercial advertising photograph. "
        + "The image must clearly represent this business/service: "
        + context
        + ". "
        + "Do not invent prices, brands, logos, promises or written content. "
    )

    if layout == "expert":
        return (
            base
            + "Show the real professional work process and appropriate equipment. "
            + "A believable worker or specialist may be visible. "
            + "Place the main human/product/process subject on the RIGHT 40 percent. "
            + "Keep the LEFT 55 percent visually calm and suitable for graphic layout. "
            + "Modern architectural photography, tactile materials, realistic lighting. "
            + "No cheesy stock-photo posing."
        )

    if layout == "premium":
        return (
            base
            + "Premium editorial art direction. "
            + "Sophisticated material textures and natural light. "
            + "Main subject on the lower-right or right third. "
            + "Clean darker negative space on the left. "
            + "Minimal, expensive, restrained visual language."
        )

    if layout == "offer":
        return (
            base
            + "High-conversion advertising composition. "
            + "Strong visual subject on the right. "
            + "Clean neutral area on the left and upper-left. "
            + "Clear depth, professional lighting, uncluttered environment."
        )

    return (
        base
        + "Modern commercial advertising art direction. "
        + "Strong subject on the right half. "
        + "Clean negative space on the left. "
        + "Professional realistic lighting. "
        + "Simple, confident composition."
    )


###############################################################################
# LAYOUT HELPERS
###############################################################################

def _panel_expert(canvas, accent):
    overlay = Image.new(
        "RGBA",
        (W, H),
        (0, 0, 0, 0),
    )

    d = ImageDraw.Draw(overlay)

    d.polygon(
        [
            (0, 0),
            (600, 0),
            (510, H),
            (0, H),
        ],
        fill=(9, 20, 38, 238),
    )

    d.polygon(
        [
            (0, 0),
            (18, 0),
            (18, H),
            (0, H),
        ],
        fill=accent + (255,),
    )

    return Image.alpha_composite(
        canvas.convert("RGBA"),
        overlay,
    )


def _panel_commercial(canvas, accent):
    overlay = Image.new(
        "RGBA",
        (W, H),
        (0, 0, 0, 0),
    )

    d = ImageDraw.Draw(overlay)

    d.rounded_rectangle(
        (48, 54, 625, 950),
        radius=30,
        fill=(7, 18, 34, 222),
    )

    d.rounded_rectangle(
        (78, 78, 148, 88),
        radius=5,
        fill=accent + (255,),
    )

    return Image.alpha_composite(
        canvas.convert("RGBA"),
        overlay,
    )


def _panel_premium(canvas, accent):
    overlay = Image.new(
        "RGBA",
        (W, H),
        (0, 0, 0, 0),
    )

    d = ImageDraw.Draw(overlay)

    d.rectangle(
        (0, 0, 540, H),
        fill=(18, 20, 22, 232),
    )

    d.rectangle(
        (62, 91, 132, 97),
        fill=accent + (255,),
    )

    return Image.alpha_composite(
        canvas.convert("RGBA"),
        overlay,
    )


def _panel_offer(canvas, accent):
    return _panel_commercial(
        canvas,
        accent,
    )


def _render_advantage_cards(
    draw,
    *,
    advantages,
    accent,
    x,
    y,
    width,
    dark=True,
):
    if not advantages:
        return y

    available = advantages[:3]

    font = _font(
        FONT_BOLD,
        23,
    )

    text_color = (
        (246, 249, 255, 255)
        if dark
        else (18, 28, 44, 255)
    )

    card_fill = (
        (255, 255, 255, 24)
        if dark
        else (255, 255, 255, 230)
    )

    card_outline = (
        (255, 255, 255, 42)
        if dark
        else (20, 35, 55, 28)
    )

    card_h = 76
    gap = 10

    for item in available:
        draw.rounded_rectangle(
            (
                x,
                y,
                x + width,
                y + card_h,
            ),
            radius=18,
            fill=card_fill,
            outline=card_outline,
            width=1,
        )

        icon_x = x + 38
        icon_y = y + card_h // 2

        _draw_icon(
            draw,
            _icon_kind(item),
            icon_x,
            icon_y,
            22,
            accent + (255,),
        )

        text = _shorten_words(
            item,
            6,
        )

        draw.text(
            (
                x + 78,
                y + 24,
            ),
            text,
            font=font,
            fill=text_color,
        )

        y += card_h + gap

    return y


def _render_price(
    draw,
    *,
    price_text,
    accent,
    x,
    y,
):
    if not price_text:
        return

    price_text = _clean(
        price_text,
        35,
    )

    font = _font(
        FONT_EXTRABOLD,
        39,
    )

    box = draw.textbbox(
        (0, 0),
        price_text,
        font=font,
    )

    tw = box[2] - box[0]
    th = box[3] - box[1]

    draw.rounded_rectangle(
        (
            x,
            y,
            x + tw + 42,
            y + th + 32,
        ),
        radius=18,
        fill=accent + (255,),
    )

    draw.text(
        (
            x + 21,
            y + 14 - box[1],
        ),
        price_text,
        font=font,
        fill=(255, 255, 255, 255),
    )


###############################################################################
# MAIN COMPOSER
###############################################################################

def compose_banner_v3(
    *,
    account_id,
    description,
    goal,
    title,
    subtitle="",
    advantages=None,
    price_text="",
    accent_color="#2F6FED",
    output_path,
):
    description = _clean(
        description,
        700,
    )

    title = _shorten_words(
        _clean(title, 100),
        7,
    )[:62]

    subtitle = _shorten_words(
        _clean(subtitle, 120),
        9,
    )[:78]

    advantages = [
        _clean(x, 52)
        for x in (advantages or [])
        if _clean(x)
    ][:3]

    price_text = _clean(
        price_text,
        35,
    )

    if not description:
        raise ValueError(
            "description required"
        )

    if not title:
        raise ValueError(
            "title required"
        )

    layout = choose_layout(
        description=description,
        goal=goal,
        price_text=price_text,
    )

    prompt = _scene_prompt(
        description=description,
        layout=layout,
    )

    # Stable paid-artifact identity: the same account/prompt/model/quality
    # is one economic intent even when HTTP/job execution is retried.
    _bg_key = hashlib.sha256((str(account_id) + "|v3|gpt-image-1-mini|medium|1024x1024|" + prompt).encode("utf-8", "ignore")).hexdigest()
    tmp = f"/root/BORIS/backend/images/_generated_cache/banner_v3/{_bg_key}.png"

    generated = generate_ai_image(
        prompt,
        tmp,
        size="1024x1024",
        quality="medium",
        model="gpt-image-1-mini",
        account_id=account_id,
        operation="banner_v3_background",
    )

    if (
        not generated
        or not os.path.isfile(tmp)
    ):
        raise RuntimeError(
            "Не удалось создать фон"
        )

    image = Image.open(
        tmp
    ).convert("RGB")

    if image.size != (W, H):
        image = image.resize(
            (W, H),
            Image.LANCZOS,
        )

    accent = _hex(
        accent_color
    )

    if layout == "expert":
        canvas = _panel_expert(
            image,
            accent,
        )
    elif layout == "premium":
        canvas = _panel_premium(
            image,
            accent,
        )
    elif layout == "offer":
        canvas = _panel_offer(
            image,
            accent,
        )
    else:
        canvas = _panel_commercial(
            image,
            accent,
        )

    draw = ImageDraw.Draw(
        canvas,
        "RGBA",
    )

    if layout == "expert":
        x = 68
        y = 82
        text_w = 455
        title_max = 67

    elif layout == "premium":
        x = 66
        y = 120
        text_w = 410
        title_max = 62

    else:
        x = 84
        y = 110
        text_w = 470
        title_max = 65

    # Маленький системный акцент — не служебная надпись.
    draw.rounded_rectangle(
        (
            x,
            y,
            x + 72,
            y + 9,
        ),
        radius=5,
        fill=accent + (255,),
    )

    y += 44

    title_font, title_lines = _fit(
        draw,
        title,
        font_path=FONT_BLACK,
        width=text_w,
        max_lines=4,
        max_size=title_max,
        min_size=43,
    )

    y = _draw_lines(
        draw,
        x,
        y,
        title_lines,
        title_font,
        (255, 255, 255, 255),
        gap=7,
    )

    if subtitle:
        y += 23

        sub_font, sub_lines = _fit(
            draw,
            subtitle,
            font_path=FONT_BOLD,
            width=text_w,
            max_lines=3,
            max_size=29,
            min_size=21,
        )

        y = _draw_lines(
            draw,
            x,
            y,
            sub_lines,
            sub_font,
            (220, 231, 245, 255),
            gap=6,
        )

    if advantages:
        y += 27

        # Если текста много, преимущества уходят чуть ниже,
        # но никогда за safe-zone.
        max_adv_y = 925 - (
            min(
                3,
                len(advantages),
            )
            * 86
        )

        y = min(
            y,
            max_adv_y,
        )

        _render_advantage_cards(
            draw,
            advantages=advantages,
            accent=accent,
            x=x,
            y=y,
            width=text_w,
            dark=True,
        )

    if price_text:
        _render_price(
            draw,
            price_text=price_text,
            accent=accent,
            x=x,
            y=865,
        )

    Path(
        output_path
    ).parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    canvas.convert(
        "RGB"
    ).save(
        output_path,
        "PNG",
        optimize=True,
    )

    # Keep the paid AI background cache. Its deterministic path is the
    # artifact-level idempotency key used by ImageGenerationService, so deleting
    # it here would turn an otherwise identical retry into another paid call.

    return {
        "path": output_path,
        "layout": layout,
        "title": title,
        "subtitle": subtitle,
        "advantages": advantages,
        "price_text": price_text,
    }
