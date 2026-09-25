"""
BORIS Banner Design Engine V4.

Цель:
не "текст поверх фотографии", а управляемый рекламный макет.

AI:
- создаёт только исходную рекламную сцену;
- не рисует текст;
- не рисует иконки;
- не рисует кнопки;
- не рисует логотипы.

BORIS:
- нормализует копирайт;
- выбирает внутренний референсный стиль;
- собирает композицию локально;
- рисует иконки;
- контролирует safe zones;
- контролирует количество строк;
- не придумывает факты и цены.
"""

import hashlib
import math
import os
import re
import time
from pathlib import Path
from difflib import SequenceMatcher

from PIL import Image, ImageDraw, ImageFont, ImageFilter

from banner_generator import (
    generate_ai_image,
    FONT_BOLD,
    FONT_EXTRABOLD,
)
from app.services.exception_observability import observe_suppressed


FONT_BLACK = "/root/BORIS/backend/fonts/Montserrat-Black.ttf"

W = 1024
H = 1024
BANNER_V4_MAX_AI_COST_RUB = 2.0


###############################################################################
# REFERENCE LIBRARY
###############################################################################

REFERENCE_LIBRARY = {
    "expert_split": {
        "family": "expert",
        "panel": "split",
        "title_width": 430,
        "subject_side": "right",
        "max_advantages": 3,
        "prompt": (
            "premium editorial commercial photography of a real professional "
            "service being performed, believable worker and professional equipment, "
            "strong process detail, tactile surfaces, architectural realism, "
            "subject concentrated in the RIGHT 40 percent, "
            "LEFT 55 percent calm and relatively dark for graphic overlay, "
            "natural realistic light, professional advertising art direction, "
            "not a generic stock photo"
        ),
    },

    "expert_cards": {
        "family": "expert",
        "panel": "card",
        "title_width": 455,
        "subject_side": "right",
        "max_advantages": 3,
        "prompt": (
            "real professional work process photographed for a premium advertising "
            "campaign, clear equipment and workmanship, realistic materials, "
            "main subject on the right side, left half clean and uncluttered, "
            "strong visual depth, sophisticated commercial lighting"
        ),
    },

    "premium_editorial": {
        "family": "premium",
        "panel": "premium",
        "title_width": 420,
        "subject_side": "right",
        "max_advantages": 2,
        "prompt": (
            "luxury editorial commercial photography, refined materials, "
            "subtle high-end interior lighting, restrained sophisticated scene, "
            "main subject on the right third, dark calm negative space on the left, "
            "premium magazine advertising art direction, no cheap stock-photo look"
        ),
    },

    "commercial_card": {
        "family": "commercial",
        "panel": "card",
        "title_width": 455,
        "subject_side": "right",
        "max_advantages": 3,
        "prompt": (
            "modern high-conversion commercial advertising photography, "
            "clear product or service context, realistic professional lighting, "
            "main visual subject on the right, clean left side, "
            "confident simple visual hierarchy, premium but accessible"
        ),
    },

    "commercial_split": {
        "family": "commercial",
        "panel": "split",
        "title_width": 440,
        "subject_side": "right",
        "max_advantages": 3,
        "prompt": (
            "modern professional advertising photograph, strong visual subject "
            "on the right side, realistic environment, clean negative space on the left, "
            "clear commercial story, sophisticated lighting and realistic details"
        ),
    },

    "offer_focus": {
        "family": "offer",
        "panel": "offer",
        "title_width": 455,
        "subject_side": "right",
        "max_advantages": 2,
        "prompt": (
            "bold high-conversion commercial photography, strong hero subject "
            "on the right, minimal uncluttered scene, clean left and upper-left region, "
            "strong realistic lighting, premium advertising campaign style"
        ),
    },
}


FORBIDDEN_VISUAL_TEXT = (
    "ABSOLUTELY NO TEXT OR TYPOGRAPHY. "
    "NO LETTERS. NO WORDS. NO NUMBERS. "
    "NO LOGOS. NO WATERMARKS. "
    "NO LABELS. NO WRITTEN SIGNS. "
    "NO PRICE TAGS. NO BUTTONS. NO BADGES. "
    "NO POSTER OR BANNER GRAPHICS INSIDE THE IMAGE. "
    "Generate ONLY the photographic scene. "
)


CONSTRUCTION = (
    "штукатур",
    "ремонт",
    "отделк",
    "монтаж",
    "строит",
    "плитк",
    "покраск",
    "сантех",
    "электр",
    "фасад",
    "кровл",
)

PREMIUM = (
    "дизайнер",
    "премиум",
    "эксклюзив",
    "интерьер",
    "индивидуаль",
    "бутик",
)

PRICE = (
    "цена",
    "скид",
    "акци",
    "₽",
    "руб",
)


###############################################################################
# TEXT / COPY
###############################################################################

def _clean(value, limit=700):
    value = re.sub(
        r"\s+",
        " ",
        (value or "").strip(),
    )

    return value[:limit].strip()


def _words(value):
    return [
        x
        for x in re.split(
            r"\s+",
            _clean(value),
        )
        if x
    ]


def _short(value, count):
    return " ".join(
        _words(value)[:count]
    ).strip(" ,.;:-")


def _similar(a, b):
    a = _clean(a).lower()
    b = _clean(b).lower()

    if not a or not b:
        return 0.0

    return SequenceMatcher(
        None,
        a,
        b,
    ).ratio()


def _sentences(text):
    return [
        _clean(x, 180).strip(" .;:-")
        for x in re.split(
            r"(?<=[.!?])\s+|\n+",
            text or "",
        )
        if _clean(x)
    ]


def _first_sentence_title(description):
    items = _sentences(description)

    if not items:
        return _short(
            description,
            6,
        )[:60]

    return _short(
        items[0],
        6,
    )[:60]


def _subtitle_from_source(
    description,
    title,
):
    for sentence in _sentences(description)[1:]:
        if _similar(
            sentence,
            title,
        ) > 0.68:
            continue

        return _short(
            sentence,
            8,
        )[:76]

    return ""


def _source_fragments(description):
    """
    Извлекаем только фактические фрагменты,
    содержащиеся в пользовательском описании.
    """
    result = []

    for sentence in _sentences(description)[1:]:
        pieces = [
            _clean(x, 80).strip(" .;:-")
            for x in re.split(
                r"[,;•]",
                sentence,
            )
            if _clean(x)
        ]

        if not pieces:
            pieces = [sentence]

        for item in pieces:
            n = len(
                _words(item)
            )

            if not 1 <= n <= 7:
                continue

            result.append(
                item[:50]
            )

    return result


def normalize_copy_v4(
    *,
    description,
    goal,
    generated=None,
):
    description = _clean(
        description,
        700,
    )

    generated = (
        generated
        if isinstance(
            generated,
            dict,
        )
        else {}
    )

    raw_title = _clean(
        generated.get("title"),
        120,
    )

    banned = {
        "показать предложение",
        "показать преимущество",
        "показать преимущества",
        "сделать акцент на цене",
        "коммерческое предложение",
        "главное коммерческое предложение",
        "ключевое преимущество",
    }

    title_bad = (
        not raw_title
        or raw_title.lower() in banned
        or len(_words(raw_title)) > 6
        or len(raw_title) > 60
        or _similar(
            raw_title,
            description,
        ) > 0.72
    )

    title = (
        _first_sentence_title(
            description
        )
        if title_bad
        else _short(
            raw_title,
            6,
        )[:60]
    )

    raw_subtitle = _clean(
        generated.get("subtitle"),
        120,
    )

    subtitle_bad = (
        len(_words(raw_subtitle)) > 8
        or len(raw_subtitle) > 76
        or _similar(
            raw_subtitle,
            title,
        ) > 0.70
        or _similar(
            raw_subtitle,
            description,
        ) > 0.84
    )

    subtitle = (
        _subtitle_from_source(
            description,
            title,
        )
        if (
            not raw_subtitle
            or subtitle_bad
        )
        else _short(
            raw_subtitle,
            8,
        )[:76]
    )

    candidate_advantages = []

    # AI advantages принимаем только если
    # они действительно встречаются в исходном описании.
    for item in (
        generated.get(
            "advantages"
        ) or []
    ):
        item = _clean(
            str(item),
            60,
        )

        if not item:
            continue

        if item.lower() not in description.lower():
            continue

        candidate_advantages.append(
            item
        )

    candidate_advantages.extend(
        _source_fragments(
            description
        )
    )

    advantages = []

    for item in candidate_advantages:
        item = _short(
            item,
            6,
        )[:48]

        if not item:
            continue

        low = item.lower()

        # Не дублируем заголовок и подзаголовок.
        if low in title.lower():
            continue

        if low in subtitle.lower():
            continue

        if (
            title.lower()
            in low
        ):
            continue

        if (
            subtitle
            and low
            in subtitle.lower()
        ):
            continue

        if _similar(
            item,
            title,
        ) > 0.66:
            continue

        if _similar(
            item,
            subtitle,
        ) > 0.72:
            continue

        if any(
            _similar(
                item,
                existing,
            ) > 0.76
            for existing
            in advantages
        ):
            continue

        advantages.append(
            item
        )

        if len(advantages) == 3:
            break

    # Цена только если цифры есть в пользовательском источнике.
    raw_price = _clean(
        generated.get("price_text"),
        35,
    )

    source_digits = set(
        re.findall(
            r"\d+",
            description,
        )
    )

    price_digits = set(
        re.findall(
            r"\d+",
            raw_price,
        )
    )

    price_text = ""

    if (
        raw_price
        and price_digits
        and price_digits.issubset(
            source_digits
        )
    ):
        price_text = raw_price

    return {
        "title": title,
        "subtitle": subtitle,
        "advantages": advantages,
        "price_text": price_text,
        "goal": goal or "offer",
    }


###############################################################################
# ART DIRECTION
###############################################################################

def choose_reference(
    *,
    account_id,
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
        pool = [
            "offer_focus",
        ]

    elif any(
        word in text
        for word in CONSTRUCTION
    ):
        pool = [
            "expert_split",
            "expert_cards",
        ]

    elif any(
        word in text
        for word in PREMIUM
    ):
        pool = [
            "premium_editorial",
        ]

    else:
        pool = [
            "commercial_card",
            "commercial_split",
        ]

    # Детерминированный выбор:
    # один и тот же brief не прыгает случайно.
    seed = (
        str(account_id)
        + "|"
        + description
        + "|"
        + goal
    )

    digest = hashlib.sha256(
        seed.encode(
            "utf-8",
        )
    ).digest()

    idx = digest[0] % len(
        pool
    )

    return pool[idx]


def _scene_prompt(
    *,
    description,
    reference_key,
):
    ref = REFERENCE_LIBRARY[
        reference_key
    ]

    return (
        FORBIDDEN_VISUAL_TEXT
        + "\nCreate a photographic advertising background for this real business/service:\n"
        + _clean(
            description,
            600,
        )
        + "\n\nART DIRECTION:\n"
        + ref["prompt"]
        + "\nDo not invent written claims, prices, brands, logos or facts. "
        + "Do not insert any graphic design elements. "
        + "The final layout will be created separately by software."
    )


###############################################################################
# TYPOGRAPHY
###############################################################################

def _font(
    path,
    size,
):
    return ImageFont.truetype(
        path,
        int(size),
    )


def _text_width(
    draw,
    text,
    font,
):
    box = draw.textbbox(
        (0, 0),
        text,
        font=font,
    )

    return (
        box[2]
        - box[0]
    )


def _wrap(
    draw,
    text,
    font,
    max_width,
):
    words = _words(text)

    if not words:
        return []

    result = []
    current = words[0]

    for word in words[1:]:
        test = (
            current
            + " "
            + word
        )

        if (
            _text_width(
                draw,
                test,
                font,
            )
            <= max_width
        ):
            current = test
        else:
            result.append(
                current
            )
            current = word

    result.append(
        current
    )

    return result


def _fit_text(
    draw,
    text,
    *,
    width,
    max_lines,
    max_size,
    min_size,
    font_path,
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
    )

    return (
        font,
        lines[:max_lines],
    )


def _draw_lines(
    draw,
    *,
    x,
    y,
    lines,
    font,
    fill,
    gap,
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
# COLOR
###############################################################################

def _hex(
    value,
    fallback=(47, 111, 237),
):
    try:
        value = (
            value or ""
        ).strip().lstrip("#")

        if len(value) != 6:
            return fallback

        return tuple(
            int(
                value[i:i+2],
                16,
            )
            for i
            in (0, 2, 4)
        )

    except Exception:
        return fallback


###############################################################################
# VECTOR ICON SYSTEM
###############################################################################

def _icon_check(
    d,
    cx,
    cy,
    r,
    c,
):
    d.ellipse(
        (
            cx-r,
            cy-r,
            cx+r,
            cy+r,
        ),
        outline=c,
        width=3,
    )

    d.line(
        (
            cx-r*0.42,
            cy,
            cx-r*0.08,
            cy+r*0.34,
            cx+r*0.52,
            cy-r*0.42,
        ),
        fill=c,
        width=4,
        joint="curve",
    )


def _icon_home(
    d,
    cx,
    cy,
    r,
    c,
):
    d.line(
        (
            cx-r*0.65,
            cy-r*0.02,
            cx,
            cy-r*0.62,
            cx+r*0.65,
            cy-r*0.02,
        ),
        fill=c,
        width=4,
        joint="curve",
    )

    d.rectangle(
        (
            cx-r*0.44,
            cy-r*0.02,
            cx+r*0.44,
            cy+r*0.55,
        ),
        outline=c,
        width=4,
    )


def _icon_tool(
    d,
    cx,
    cy,
    r,
    c,
):
    d.line(
        (
            cx-r*0.58,
            cy+r*0.55,
            cx+r*0.42,
            cy-r*0.45,
        ),
        fill=c,
        width=5,
    )

    d.ellipse(
        (
            cx+r*0.16,
            cy-r*0.66,
            cx+r*0.64,
            cy-r*0.18,
        ),
        outline=c,
        width=4,
    )


def _icon_star(
    d,
    cx,
    cy,
    r,
    c,
):
    pts = []

    for i in range(10):
        angle = (
            -math.pi / 2
            + i * math.pi / 5
        )

        rr = (
            r * 0.62
            if i % 2 == 0
            else r * 0.27
        )

        pts.append(
            (
                cx
                + rr
                * math.cos(angle),

                cy
                + rr
                * math.sin(angle),
            )
        )

    d.line(
        pts + [pts[0]],
        fill=c,
        width=3,
        joint="curve",
    )


def _icon_clock(
    d,
    cx,
    cy,
    r,
    c,
):
    d.ellipse(
        (
            cx-r*0.61,
            cy-r*0.61,
            cx+r*0.61,
            cy+r*0.61,
        ),
        outline=c,
        width=4,
    )

    d.line(
        (
            cx,
            cy,
            cx,
            cy-r*0.34,
        ),
        fill=c,
        width=4,
    )

    d.line(
        (
            cx,
            cy,
            cx+r*0.29,
            cy+r*0.16,
        ),
        fill=c,
        width=4,
    )


def _icon_kind(
    text,
):
    t = _clean(
        text
    ).lower()

    if any(
        x in t
        for x in (
            "квартир",
            "дом",
            "новостро",
            "коттедж",
        )
    ):
        return "home"

    if any(
        x in t
        for x in (
            "механиз",
            "работ",
            "ремонт",
            "монтаж",
            "штукатур",
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
            "ровн",
            "качест",
            "премиум",
        )
    ):
        return "star"

    return "check"


def _draw_icon(
    d,
    kind,
    cx,
    cy,
    r,
    color,
):
    funcs = {
        "check": _icon_check,
        "home": _icon_home,
        "tool": _icon_tool,
        "star": _icon_star,
        "clock": _icon_clock,
    }

    funcs.get(
        kind,
        _icon_check,
    )(
        d,
        cx,
        cy,
        r,
        color,
    )


###############################################################################
# PANELS
###############################################################################

def _split_panel(
    img,
    accent,
):
    layer = Image.new(
        "RGBA",
        img.size,
        (0, 0, 0, 0),
    )

    d = ImageDraw.Draw(
        layer
    )

    d.polygon(
        [
            (0, 0),
            (605, 0),
            (515, H),
            (0, H),
        ],
        fill=(
            8,
            17,
            32,
            242,
        ),
    )

    d.rectangle(
        (
            0,
            0,
            15,
            H,
        ),
        fill=
            accent
            + (255,),
    )

    return Image.alpha_composite(
        img.convert("RGBA"),
        layer,
    )


def _card_panel(
    img,
    accent,
):
    # Очень лёгкий общий затемняющий слой.
    base = Image.new(
        "RGBA",
        img.size,
        (0, 0, 0, 0),
    )

    d = ImageDraw.Draw(
        base
    )

    d.rounded_rectangle(
        (
            42,
            48,
            620,
            957,
        ),
        radius=34,
        fill=(
            7,
            17,
            32,
            226,
        ),
    )

    d.rounded_rectangle(
        (
            76,
            78,
            150,
            88,
        ),
        radius=5,
        fill=
            accent
            + (255,),
    )

    return Image.alpha_composite(
        img.convert("RGBA"),
        base,
    )


def _premium_panel(
    img,
    accent,
):
    layer = Image.new(
        "RGBA",
        img.size,
        (0, 0, 0, 0),
    )

    d = ImageDraw.Draw(
        layer
    )

    d.rectangle(
        (
            0,
            0,
            545,
            H,
        ),
        fill=(
            16,
            18,
            21,
            238,
        ),
    )

    d.rectangle(
        (
            66,
            92,
            135,
            98,
        ),
        fill=
            accent
            + (255,),
    )

    return Image.alpha_composite(
        img.convert("RGBA"),
        layer,
    )


def _offer_panel(
    img,
    accent,
):
    layer = Image.new(
        "RGBA",
        img.size,
        (0, 0, 0, 0),
    )

    d = ImageDraw.Draw(
        layer
    )

    d.rounded_rectangle(
        (
            45,
            50,
            625,
            950,
        ),
        radius=32,
        fill=(
            7,
            16,
            31,
            232,
        ),
    )

    d.rounded_rectangle(
        (
            77,
            80,
            190,
            90,
        ),
        radius=5,
        fill=
            accent
            + (255,),
    )

    return Image.alpha_composite(
        img.convert("RGBA"),
        layer,
    )


###############################################################################
# ADVANTAGE CARDS
###############################################################################

def _draw_advantages(
    d,
    *,
    advantages,
    x,
    y,
    width,
    accent,
    max_items,
):
    items = (
        advantages or []
    )[:max_items]

    if not items:
        return y

    font = _font(
        FONT_BOLD,
        21,
    )

    card_h = 72

    for item in items:
        d.rounded_rectangle(
            (
                x,
                y,
                x + width,
                y + card_h,
            ),
            radius=17,
            fill=(
                255,
                255,
                255,
                24,
            ),
            outline=(
                255,
                255,
                255,
                43,
            ),
            width=1,
        )

        icon_x = (
            x + 36
        )

        icon_y = (
            y + card_h // 2
        )

        _draw_icon(
            d,
            _icon_kind(item),
            icon_x,
            icon_y,
            21,
            accent + (255,),
        )

        label = _short(
            item,
            6,
        )

        # Поджимаем слишком длинное преимущество.
        local_font = font

        if len(label) > 32:
            local_font = _font(
                FONT_BOLD,
                18,
            )

        d.text(
            (
                x + 76,
                y + 23,
            ),
            label,
            font=local_font,
            fill=(
                244,
                248,
                255,
                255,
            ),
        )

        y += (
            card_h
            + 9
        )

    return y


###############################################################################
# PRICE
###############################################################################

def _draw_price(
    d,
    *,
    price,
    x,
    y,
    accent,
):
    price = _clean(
        price,
        35,
    )

    if not price:
        return

    font = _font(
        FONT_EXTRABOLD,
        38,
    )

    box = d.textbbox(
        (0, 0),
        price,
        font=font,
    )

    tw = (
        box[2]
        - box[0]
    )

    th = (
        box[3]
        - box[1]
    )

    d.rounded_rectangle(
        (
            x,
            y,
            x + tw + 46,
            y + th + 32,
        ),
        radius=18,
        fill=
            accent
            + (255,),
    )

    d.text(
        (
            x + 23,
            y + 14 - box[1],
        ),
        price,
        font=font,
        fill=(
            255,
            255,
            255,
            255,
        ),
    )


###############################################################################
# RENDER CORE
###############################################################################

def _assemble(
    *,
    image,
    reference_key,
    title,
    subtitle,
    advantages,
    price_text,
    accent_color,
):
    ref = REFERENCE_LIBRARY[
        reference_key
    ]

    accent = _hex(
        accent_color
    )

    panel = ref[
        "panel"
    ]

    if panel == "split":
        canvas = _split_panel(
            image,
            accent,
        )

    elif panel == "premium":
        canvas = _premium_panel(
            image,
            accent,
        )

    elif panel == "offer":
        canvas = _offer_panel(
            image,
            accent,
        )

    else:
        canvas = _card_panel(
            image,
            accent,
        )

    d = ImageDraw.Draw(
        canvas,
        "RGBA",
    )

    if panel == "premium":
        x = 67
        y = 125
        title_width = 405
        max_title = 61

    elif panel == "split":
        x = 68
        y = 115
        title_width = 430
        max_title = 65

    else:
        x = 80
        y = 120
        title_width = 450
        max_title = 64

    # Micro-accent — визуальная подпись,
    # но без текста и служебных слов.
    d.rounded_rectangle(
        (
            x,
            y,
            x + 76,
            y + 9,
        ),
        radius=5,
        fill=
            accent
            + (255,),
    )

    y += 43

    title_font, title_lines = _fit_text(
        d,
        title,
        width=title_width,
        max_lines=4,
        max_size=max_title,
        min_size=42,
        font_path=FONT_BLACK,
    )

    y = _draw_lines(
        d,
        x=x,
        y=y,
        lines=title_lines,
        font=title_font,
        fill=(
            255,
            255,
            255,
            255,
        ),
        gap=7,
    )

    if subtitle:
        y += 22

        sub_font, sub_lines = _fit_text(
            d,
            subtitle,
            width=title_width,
            max_lines=3,
            max_size=28,
            min_size=20,
            font_path=FONT_BOLD,
        )

        y = _draw_lines(
            d,
            x=x,
            y=y,
            lines=sub_lines,
            font=sub_font,
            fill=(
                222,
                231,
                244,
                255,
            ),
            gap=6,
        )

    adv = (
        advantages or []
    )[:ref["max_advantages"]]

    if adv:
        y += 27

        required = (
            len(adv)
            * 81
        )

        # Нижняя safe-zone.
        y = min(
            y,
            918 - required,
        )

        _draw_advantages(
            d,
            advantages=adv,
            x=x,
            y=y,
            width=title_width,
            accent=accent,
            max_items=ref[
                "max_advantages"
            ],
        )

    if price_text:
        _draw_price(
            d,
            price=price_text,
            x=x,
            y=862,
            accent=accent,
        )

    return canvas


###############################################################################
# PUBLIC COMPOSER
###############################################################################

def compose_banner_v4(
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

    title = _short(
        title,
        6,
    )[:60]

    subtitle = _short(
        subtitle,
        8,
    )[:76]

    advantages = [
        _short(
            _clean(x, 50),
            6,
        )
        for x
        in (
            advantages
            or []
        )
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

    reference_key = choose_reference(
        account_id=account_id,
        description=description,
        goal=goal,
        price_text=price_text,
    )

    prompt = _scene_prompt(
        description=description,
        reference_key=reference_key,
    )

    # Hard invariant:
    # пользовательский заголовок не добавляем
    # в prompt как видимый текст.
    if (
        title
        and title.lower()
        in prompt.lower()
    ):
        # Такое возможно только если title совпадает
        # с частью business context.
        # Сам prompt всё равно содержит NO TEXT,
        # но не разрешаем никакую инструкцию "write title".
        pass

    # Stable paid-artifact identity. V4 already hashes design state elsewhere;
    # include the full AI prompt and economic parameters so an identical retry
    # becomes a cache hit in ImageGenerationService rather than another charge.
    _bg_key = hashlib.sha256((str(account_id) + "|v4|gpt-image-1-mini|medium|1024x1024|" + prompt).encode("utf-8", "ignore")).hexdigest()
    tmp = f"/root/BORIS/backend/images/_generated_cache/banner_v4/{_bg_key}.png"

    # Cost SLA: the paid layer is background-only and must fit the <=2 RUB target.
    from app.services.image_service import estimate_kopeks as _estimate_image_kopeks
    _estimated_rub=float(_estimate_image_kopeks(1,model="gpt-image-1-mini",quality="medium",with_reference=False))/100.0
    if _estimated_rub > BANNER_V4_MAX_AI_COST_RUB:
        raise RuntimeError("BANNER_V4_COST_SLA_BLOCKED")
    _usage_started_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    result = generate_ai_image(
        prompt,
        tmp,
        size="1024x1024",
        quality="medium",
        model="gpt-image-1-mini",
        account_id=account_id,
        operation="banner_v4_background",
    )
    _actual_cost_rub=0.0
    try:
        from app.db.session import SessionLocal as _BannerDb
        from sqlalchemy import text as _bt
        _bdb=_BannerDb()
        _row=_bdb.execute(_bt("SELECT cost_rub FROM api_usage WHERE account_id=:a AND operation='banner_v4_background' AND created_at>=:t ORDER BY id DESC LIMIT 1"),{"a":account_id,"t":_usage_started_at}).fetchone()
        _actual_cost_rub=float((_row[0] if _row else 0) or 0); _bdb.close()
    except Exception:
        _actual_cost_rub=0.0
    if _actual_cost_rub > BANNER_V4_MAX_AI_COST_RUB:
        try:
            if os.path.isfile(tmp): os.remove(tmp)
        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=1747)
        raise RuntimeError("BANNER_V4_ACTUAL_COST_SLA_EXCEEDED")

    if (
        result
        and os.path.isfile(tmp)
        and os.path.getsize(tmp) > 0
    ):
        image = Image.open(
            tmp
        ).convert(
            "RGB"
        )
        background_source = "ai"
    else:
        # Production-safe fallback: внешний image provider не должен
        # уничтожать весь баннер. BORIS собирает нейтральную визуальную
        # основу локально и дальше применяет тот же V4 composition engine.
        # Здесь нет выдуманных предметов/фактов клиента — только абстрактный
        # рекламный фон и safe-zone под реальный текст поста.
        image = Image.new("RGB", (W, H), (235, 240, 248))
        d = ImageDraw.Draw(image)
        for y in range(H):
            t = y / max(H - 1, 1)
            r = int(244 - 24 * t)
            g = int(247 - 20 * t)
            b = int(252 - 10 * t)
            d.line([(0, y), (W, y)], fill=(r, g, b))
        d.ellipse((650, 80, 1090, 520), fill=(206, 220, 246))
        d.rounded_rectangle((720, 500, 1035, 930), radius=70, fill=(190, 209, 240))
        d.ellipse((820, 660, 1120, 960), fill=(219, 229, 247))
        background_source = "local_fallback"
        print(
            "[banner_v4] AI background unavailable; local fallback used",
            flush=True,
        )


    if image.size != (
        W,
        H,
    ):
        image = image.resize(
            (
                W,
                H,
            ),
            Image.LANCZOS,
        )

    canvas = _assemble(
        image=image,
        reference_key=reference_key,
        title=title,
        subtitle=subtitle,
        advantages=advantages,
        price_text=price_text,
        accent_color=accent_color,
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
        "reference": reference_key,
        "family": REFERENCE_LIBRARY[
            reference_key
        ]["family"],
        "title": title,
        "subtitle": subtitle,
        "advantages": advantages,
        "price_text": price_text,
        "background_source": background_source,
        "ai_cost_rub": round(float(_actual_cost_rub or 0),4),
        "cost_sla_rub": BANNER_V4_MAX_AI_COST_RUB,
        "cost_sla_pass": bool(float(_actual_cost_rub or 0) <= BANNER_V4_MAX_AI_COST_RUB),
    }


###############################################################################
# FREE LOCAL LAYOUT TEST
###############################################################################

def render_local_test(
    output_path,
):
    """
    Только локальный тест верстки.
    Никакого AI.
    """

    img = Image.new(
        "RGB",
        (
            W,
            H,
        ),
        (
            178,
            185,
            192,
        ),
    )

    d = ImageDraw.Draw(
        img
    )

    # Псевдообъект справа:
    # доказываем safe-zone без AI.
    d.rounded_rectangle(
        (
            650,
            120,
            950,
            900,
        ),
        radius=35,
        fill=(
            138,
            145,
            151,
        ),
    )

    canvas = _assemble(
        image=img,
        reference_key="expert_split",
        title="Механизированная штукатурка в Сочи",
        subtitle="Квартиры, новостройки и частные дома",
        advantages=[
            "Ровные стены под обои",
            "Подготовка под покраску",
        ],
        price_text="",
        accent_color="#2F6FED",
    )

    canvas.convert(
        "RGB"
    ).save(
        output_path,
        "PNG",
    )

    return output_path
