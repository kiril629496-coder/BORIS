"""
Генератор баннеров для Бориса.
Слои: фон (цвет/градиент) -> геометрическая плашка -> фото (опц.) ->
иконка -> текст (Montserrat) -> сборка в PNG.
"""

import os
from dotenv import load_dotenv
load_dotenv()
from PIL import Image, ImageDraw, ImageFont
import cairosvg
import io

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONTS_DIR = os.path.join(BASE_DIR, "fonts")
ICONS_DIR = os.path.join(BASE_DIR, "icons", "selected")

FONT_BLACK = os.path.join(FONTS_DIR, "Montserrat-Black.ttf")
FONT_EXTRABOLD = os.path.join(FONTS_DIR, "Montserrat-ExtraBold.ttf")
FONT_BOLD = os.path.join(FONTS_DIR, "Montserrat-Bold.ttf")

# Безопасный отступ от края — 6% от меньшей стороны холста
SAFE_MARGIN_RATIO = 0.06

# Запрещённые Avito символы, которые не должны попасть в текст баннера
FORBIDDEN_SEQUENCES = ["->", ">>", "—>", "→"]


def sanitize_text(text: str) -> str:
    """Убирает запрещённые модерацией Avito конструкции и капс."""
    for seq in FORBIDDEN_SEQUENCES:
        text = text.replace(seq, "")
    # Не убиваем аббревиатуры из 2-3 букв, но полностью капсовые
    # длинные слова переводим в нормальный регистр (Title Case)
    words = text.split()
    fixed = []
    for w in words:
        if len(w) > 3 and w.isupper():
            fixed.append(w.capitalize())
        else:
            fixed.append(w)
    return " ".join(fixed)


def load_svg_icon(icon_name: str, size: int, color: str = "#FFFFFF") -> Image.Image:
    """Рендерит SVG-иконку из icons/selected в PNG нужного размера и цвета."""
    svg_path = os.path.join(ICONS_DIR, icon_name)
    with open(svg_path, "r", encoding="utf-8") as f:
        svg_data = f.read()
    # tabler-иконки используют stroke="currentColor" — подменяем на нужный цвет
    svg_data = svg_data.replace("currentColor", color)
    png_bytes = cairosvg.svg2png(
        bytestring=svg_data.encode("utf-8"),
        output_width=size,
        output_height=size,
    )
    return Image.open(io.BytesIO(png_bytes)).convert("RGBA")


def fit_text_font(draw, text, font_path, max_width, max_size, min_size=24):
    """Подбирает максимальный размер шрифта, чтобы текст влез в max_width."""
    size = max_size
    while size > min_size:
        font = ImageFont.truetype(font_path, size)
        bbox = draw.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
        if w <= max_width:
            return font
        size -= 2
    return ImageFont.truetype(font_path, min_size)


def draw_text_with_shadow(draw, position, text, font, fill="#FFFFFF",
                           shadow_color="#00000080", shadow_offset=(3, 3)):
    x, y = position
    ox, oy = shadow_offset
    draw.text((x + ox, y + oy), text, font=font, fill=shadow_color)
    draw.text((x, y), text, font=font, fill=fill)


def generate_infographic_banner(
    title: str,
    subtitle: str = "",
    price_text: str = "",
    icon_name: str = "star.svg",
    bg_color_top: str = "#FF6B35",
    bg_color_bottom: str = "#F7931E",
    accent_color: str = "#FFFFFF",
    output_path: str = "/tmp/banner_output.png",
    size: int = 1080,
    photo_query: str = "",
    subtitle_color: str = "#F0F0F0",
    photo_source: str = "ai",
    ai_quality: str = "medium",
    ai_model: str = "gpt-image-2",
    own_photo_path: str = None, account_id: str = None, operation: str = "генерация изображения",
) -> str:
    """
    Инфографика для объявления, 1080x1080.
    Полноэкранное фото-фон (собственное фото клиента, если own_photo_path задан;
    иначе AI/Pexels по photo_query; иначе — градиентная заливка как fallback) +
    затемняющий оверлей снизу под заголовком/подзаголовком/ценой + иконка-акцент.
    """
    title = sanitize_text(title)
    subtitle = sanitize_text(subtitle)

    margin = int(size * SAFE_MARGIN_RATIO)

    def hex_to_rgb(h):
        h = h.lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

    photo_path = None
    if own_photo_path and os.path.isfile(own_photo_path):
        photo_path = own_photo_path
    elif photo_query:
        if photo_source == "ai":
            ai_prompt = f"{photo_query}, photorealistic, professional advertising photography, vivid colors, high quality, natural lighting, square composition"
            photo_path = generate_ai_image(
                ai_prompt, "/tmp/_banner_bg_photo_ai.png",
                size="1024x1024", quality=ai_quality, model=ai_model, account_id=account_id, operation=operation,
            )
        else:
            photo_path = get_photo_stock(photo_query, "/tmp/_banner_bg_photo.jpg", orientation="square")

    if photo_path:
        # Фото-фон: масштабируем и обрезаем по центру под квадрат size x size
        photo = Image.open(photo_path).convert("RGB")
        pw, ph = photo.size
        scale = max(size / pw, size / ph)
        new_w, new_h = int(pw * scale), int(ph * scale)
        photo = photo.resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - size) // 2
        top_crop = (new_h - size) // 2
        photo = photo.crop((left, top_crop, left + size, top_crop + size))
        img = photo.convert("RGBA")
    else:
        # Fallback — вертикальный градиент, если фото не нашлось
        img = Image.new("RGB", (size, size), bg_color_top)
        draw_grad = ImageDraw.Draw(img)
        top = hex_to_rgb(bg_color_top)
        bottom = hex_to_rgb(bg_color_bottom)
        for y in range(size):
            t = y / size
            r = int(top[0] + (bottom[0] - top[0]) * t)
            g = int(top[1] + (bottom[1] - top[1]) * t)
            b = int(top[2] + (bottom[2] - top[2]) * t)
            draw_grad.line([(0, y), (size, y)], fill=(r, g, b))
        img = img.convert("RGBA")

    draw = ImageDraw.Draw(img)

    # Тёмный оверлей-плашка в нижней ПОЛОВИНЕ (а не только трети) для контраста текста
    plate_top = int(size * 0.42)
    overlay = Image.new("RGBA", (size, size - plate_top), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    for y in range(size - plate_top):
        t = y / (size - plate_top)
        alpha = int(40 + t * 160)  # от лёгкого затемнения к плотному внизу
        overlay_draw.line([(0, y), (size, y)], fill=(0, 0, 0, alpha))
    img.paste(overlay, (0, plate_top), overlay)

    # Иконка-акцент в верхней части
    icon_size = int(size * 0.22)
    icon_img = load_svg_icon(icon_name, icon_size, color=accent_color)
    icon_x = size - margin - icon_size
    icon_y = margin
    img.paste(icon_img, (icon_x, icon_y), icon_img)

    # Заголовок
    max_text_width = size - 2 * margin
    title_font = fit_text_font(draw, title, FONT_BLACK, max_text_width, int(size * 0.09))
    draw_text_with_shadow(draw, (margin, plate_top + int(size * 0.06)), title, title_font)

    # Подзаголовок
    if subtitle:
        sub_font = fit_text_font(draw, subtitle, FONT_BOLD, max_text_width, int(size * 0.045))
        bbox = draw.textbbox((0, 0), title, font=title_font)
        title_h = bbox[3] - bbox[1]
        sub_y = plate_top + int(size * 0.06) + title_h + int(size * 0.03)
        draw_text_with_shadow(draw, (margin, sub_y), subtitle, sub_font, fill=subtitle_color)

    # Цена (если задана) — плашка-кружок в углу
    if price_text:
        price_text = sanitize_text(price_text)
        price_font = ImageFont.truetype(FONT_EXTRABOLD, int(size * 0.06))
        bbox = draw.textbbox((0, 0), price_text, font=price_font)
        pw, ph = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad = int(size * 0.025)
        badge_w, badge_h = pw + pad * 2, ph + pad * 2
        badge_x, badge_y = margin, margin
        draw.rounded_rectangle(
            [(badge_x, badge_y), (badge_x + badge_w, badge_y + badge_h)],
            radius=badge_h // 2,
            fill=(255, 255, 255, 235),
        )
        draw.text(
            (badge_x + pad, badge_y + pad - bbox[1]),
            price_text, font=price_font, fill=bg_color_top,
        )

    img.convert("RGB").save(output_path, "PNG", quality=95)
    return output_path


if __name__ == "__main__":
    # Быстрый тест
    out = generate_infographic_banner(
        title="Шкафы на заказ",
        subtitle="Замер и монтаж бесплатно",
        price_text="от 15 000₽",
        icon_name="star.svg",
    )
    print("Сохранено:", out)


def get_photo_stock(query: str, save_path: str, orientation: str = "square") -> str | None:
    """
    Ищет фото на Pexels по запросу, скачивает первое подходящее.
    orientation: 'square' | 'portrait' | 'landscape'
    Возвращает путь к сохранённому файлу или None, если не нашлось / нет ключа.
    """
    import requests
    from proxy_pool import get_intl_requests_proxies, calc_intl_proxy_cost_rub

    api_key = os.getenv("PEXELS_API_KEY")
    if not api_key:
        return None

    max_attempts = 3
    last_error = None
    for attempt in range(1, max_attempts + 1):
        proxies = get_intl_requests_proxies()
        try:
            resp = requests.get(
                "https://api.pexels.com/v1/search",
                headers={"Authorization": api_key},
                params={"query": query, "per_page": 5, "orientation": orientation},
                proxies=proxies,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            photos = data.get("photos", [])
            if not photos:
                return None

            # Выбираем САМОЕ ШИРОКОЕ по соотношению сторон фото из выдачи —
            # для панорамы это даёт больше "чёткой" площади вместо узкой полосы по центру
            best_photo = max(photos, key=lambda p: p.get("width", 1) / max(p.get("height", 1), 1))
            photo_url = best_photo["src"].get("original") or best_photo["src"]["large"]
            img_resp = requests.get(photo_url, proxies=proxies, timeout=20)
            img_resp.raise_for_status()

            total_bytes = len(resp.content) + len(img_resp.content)
            cost_rub = calc_intl_proxy_cost_rub(total_bytes)
            print(f"[get_photo_stock] Скачано {total_bytes} байт через intl-прокси, "
                  f"стоимость ≈{cost_rub}₽ (запрос: «{query}», попытка {attempt})", flush=True)

            with open(save_path, "wb") as f:
                f.write(img_resp.content)
            return save_path
        except Exception as e:
            last_error = e
            print(f"[get_photo_stock] Попытка {attempt} не удалась: {e}", flush=True)
            continue

    print(f"[get_photo_stock] Все {max_attempts} попыток исчерпаны, последняя ошибка: {last_error}")
    return None


def generate_carousel_banner(
    title: str,
    subtitle: str = "",
    price_text: str = "",
    icon_name: str = "star.svg",
    photo_query: str = "",
    bg_color_top: str = "#FF6B35",
    bg_color_bottom: str = "#F7931E",
    variant: str = "pc",  # "pc" | "mobile"
    output_dir: str = "/tmp",
    slide2_text: str = "",
    slide3_text: str = "",
) -> list[str]:
    """
    Карусель-панорама для Максимального тарифа Avito.
    Рисует ОДНУ широкую композицию (панораму) и режет на 3 равные части
    нужного размера — при пролистывании создаётся эффект непрерывной картины.

    variant="pc": каждый слайд 1304x480 (панорама 3912x480)
    variant="mobile": каждый слайд 1420x960 (панорама 4260x960)

    Возвращает список путей к 3 сохранённым файлам (slide_1, slide_2, slide_3).
    """
    title = sanitize_text(title)
    subtitle = sanitize_text(subtitle)

    if variant == "mobile":
        slide_w, slide_h = 1420, 960
    else:
        slide_w, slide_h = 1304, 480

    panorama_w = slide_w * 3
    panorama_h = slide_h

    def hex_to_rgb(h):
        h = h.lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

    photo_path = None
    if photo_query:
        photo_path = get_photo_stock(
            photo_query, "/tmp/_carousel_bg_photo.jpg", orientation="landscape"
        )

    if photo_path:
        from PIL import ImageFilter
        photo = Image.open(photo_path).convert("RGB")
        pw, ph = photo.size

        # Слой 1: размытый фон, растянутый на всю панораму (cover), для заполнения краёв
        cover_scale = max(panorama_w / pw, panorama_h / ph)
        cover_w, cover_h = int(pw * cover_scale), int(ph * cover_scale)
        bg_photo = photo.resize((cover_w, cover_h), Image.LANCZOS)
        left = (cover_w - panorama_w) // 2
        top_crop = (cover_h - panorama_h) // 2
        bg_photo = bg_photo.crop((left, top_crop, left + panorama_w, top_crop + panorama_h))
        bg_photo = bg_photo.filter(ImageFilter.GaussianBlur(radius=30))

        # Слой 2: чёткое фото БЕЗ агрессивного кропа (contain), по центру, естественный масштаб
        contain_scale = min(panorama_w / pw, panorama_h / ph)
        sharp_w, sharp_h = int(pw * contain_scale), int(ph * contain_scale)
        sharp_photo = photo.resize((sharp_w, sharp_h), Image.LANCZOS)

        img = bg_photo.convert("RGBA")
        paste_x = (panorama_w - sharp_w) // 2
        paste_y = (panorama_h - sharp_h) // 2
        img.paste(sharp_photo, (paste_x, paste_y))
    else:
        img = Image.new("RGB", (panorama_w, panorama_h), bg_color_top)
        draw_grad = ImageDraw.Draw(img)
        top = hex_to_rgb(bg_color_top)
        bottom = hex_to_rgb(bg_color_bottom)
        for x in range(panorama_w):
            t = x / panorama_w
            r = int(top[0] + (bottom[0] - top[0]) * t)
            g = int(top[1] + (bottom[1] - top[1]) * t)
            b = int(top[2] + (bottom[2] - top[2]) * t)
            draw_grad.line([(x, 0), (x, panorama_h)], fill=(r, g, b))
        img = img.convert("RGBA")

    draw = ImageDraw.Draw(img)

    # Затемняющий оверлей слева направо для читаемости текста в первой трети
    overlay = Image.new("RGBA", (slide_w, panorama_h), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    for x in range(slide_w):
        t = x / slide_w
        alpha = int(180 - t * 120)  # плотнее слева, легче к концу первого слайда
        overlay_draw.line([(x, 0), (x, panorama_h)], fill=(0, 0, 0, max(alpha, 0)))
    img.paste(overlay, (0, 0), overlay)

    margin = int(slide_h * SAFE_MARGIN_RATIO)

    # Иконка-акцент в верхнем правом углу первого слайда
    icon_size = int(panorama_h * 0.22)
    icon_img = load_svg_icon(icon_name, icon_size, color="#FFFFFF")
    icon_x = slide_w - margin - icon_size
    icon_y = margin
    img.paste(icon_img, (icon_x, icon_y), icon_img)

    # Заголовок и подзаголовок в первой трети (первый слайд)
    max_text_width = slide_w - 2 * margin - icon_size - margin
    title_font = fit_text_font(draw, title, FONT_BLACK, max_text_width, int(panorama_h * 0.16))
    title_y = int(panorama_h * 0.35)
    draw_text_with_shadow(draw, (margin, title_y), title, title_font)

    if subtitle:
        sub_font = fit_text_font(draw, subtitle, FONT_BOLD, max_text_width, int(panorama_h * 0.08))
        bbox = draw.textbbox((0, 0), title, font=title_font)
        title_h = bbox[3] - bbox[1]
        sub_y = title_y + title_h + int(panorama_h * 0.05)
        draw_text_with_shadow(draw, (margin, sub_y), subtitle, sub_font, fill="#F0F0F0")

    if price_text:
        price_text = sanitize_text(price_text)
        price_font = ImageFont.truetype(FONT_EXTRABOLD, int(panorama_h * 0.1))
        bbox = draw.textbbox((0, 0), price_text, font=price_font)
        pw2, ph2 = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad = int(panorama_h * 0.04)
        badge_w, badge_h = pw2 + pad * 2, ph2 + pad * 2
        badge_x, badge_y = margin, margin
        draw.rounded_rectangle(
            [(badge_x, badge_y), (badge_x + badge_w, badge_y + badge_h)],
            radius=badge_h // 2,
            fill=(255, 255, 255, 235),
        )
        draw.text(
            (badge_x + pad, badge_y + pad - bbox[1]),
            price_text, font=price_font, fill=bg_color_top,
        )

    # Текст-преимущество на 2-м слайде (плашка снизу)
    if slide2_text:
        slide2_text_clean = sanitize_text(slide2_text)
        s2_start = slide_w
        s2_font = fit_text_font(draw, slide2_text_clean, FONT_BOLD, slide_w - 2 * margin, int(panorama_h * 0.11))
        bbox = draw.textbbox((0, 0), slide2_text_clean, font=s2_font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad_x, pad_y = int(panorama_h * 0.05), int(panorama_h * 0.04)
        plate_w, plate_h = tw + pad_x * 2, th + pad_y * 2
        plate_x = s2_start + (slide_w - plate_w) // 2
        plate_y = panorama_h - plate_h - margin
        draw.rounded_rectangle(
            [(plate_x, plate_y), (plate_x + plate_w, plate_y + plate_h)],
            radius=plate_h // 2,
            fill=(0, 0, 0, 160),
        )
        draw.text((plate_x + pad_x, plate_y + pad_y - bbox[1]), slide2_text_clean, font=s2_font, fill="#FFFFFF")

    # Текст-преимущество на 3-м слайде (плашка снизу)
    if slide3_text:
        slide3_text_clean = sanitize_text(slide3_text)
        s3_start = slide_w * 2
        s3_font = fit_text_font(draw, slide3_text_clean, FONT_BOLD, slide_w - 2 * margin, int(panorama_h * 0.11))
        bbox = draw.textbbox((0, 0), slide3_text_clean, font=s3_font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad_x, pad_y = int(panorama_h * 0.05), int(panorama_h * 0.04)
        plate_w, plate_h = tw + pad_x * 2, th + pad_y * 2
        plate_x = s3_start + (slide_w - plate_w) // 2
        plate_y = panorama_h - plate_h - margin
        draw.rounded_rectangle(
            [(plate_x, plate_y), (plate_x + plate_w, plate_y + plate_h)],
            radius=plate_h // 2,
            fill=(0, 0, 0, 160),
        )
        draw.text((plate_x + pad_x, plate_y + pad_y - bbox[1]), slide3_text_clean, font=s3_font, fill="#FFFFFF")

    img_rgb = img.convert("RGB")

    saved_paths = []
    for i in range(3):
        slide = img_rgb.crop((i * slide_w, 0, (i + 1) * slide_w, slide_h))
        path = f"{output_dir}/carousel_{variant}_slide_{i+1}.png"
        slide.save(path, "PNG", quality=95)
        saved_paths.append(path)

    return saved_paths


def _generate_single_slide(
    photo_query: str,
    main_text: str,
    slide_w: int,
    slide_h: int,
    icon_name: str = "star.svg",
    price_text: str = "",
    accent_color: str = "#FF6B35",
    bg_color_top: str = "#FF6B35",
    bg_color_bottom: str = "#F7931E",
    photo_source: str = "pexels",  # "pexels" | "ai"
    ai_quality: str = "medium",
    ai_model: str = "gpt-image-1-mini", account_id: str = None, operation: str = "генерация изображения",
) -> Image.Image:
    """Строит ОДИН самостоятельный слайд размера slide_w x slide_h:
    своё фото-фон (contain+blur, без искажений) + свой текст снизу + иконка."""
    from PIL import ImageFilter

    def hex_to_rgb(h):
        h = h.lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

    photo_path = None
    if photo_query:
        if photo_source == "ai":
            ai_prompt = f"{photo_query}, photorealistic, professional advertising photography, high quality, natural lighting"
            photo_path = generate_ai_image(
                ai_prompt,
                f"/tmp/_slide_ai_{abs(hash(photo_query))}.png",
                size="1024x1024",
                quality=ai_quality,
                model=ai_model, account_id=account_id, operation=operation,
            )
        else:
            photo_path = get_photo_stock(photo_query, f"/tmp/_slide_bg_{abs(hash(photo_query))}.jpg", orientation="landscape")

    if photo_path:
        photo = Image.open(photo_path).convert("RGB")
        pw, ph = photo.size

        cover_scale = max(slide_w / pw, slide_h / ph)
        cover_w, cover_h = int(pw * cover_scale), int(ph * cover_scale)
        bg_photo = photo.resize((cover_w, cover_h), Image.LANCZOS)
        left = (cover_w - slide_w) // 2
        top_crop = (cover_h - slide_h) // 2
        bg_photo = bg_photo.crop((left, top_crop, left + slide_w, top_crop + slide_h))
        bg_photo = bg_photo.filter(ImageFilter.GaussianBlur(radius=18))

        contain_scale = min(slide_w / pw, slide_h / ph)
        sharp_w, sharp_h = int(pw * contain_scale), int(ph * contain_scale)
        sharp_photo = photo.resize((sharp_w, sharp_h), Image.LANCZOS)

        slide = bg_photo.convert("RGBA")
        paste_x = (slide_w - sharp_w) // 2
        paste_y = (slide_h - sharp_h) // 2
        slide.paste(sharp_photo, (paste_x, paste_y))
    else:
        slide = Image.new("RGB", (slide_w, slide_h), bg_color_top)
        draw_grad = ImageDraw.Draw(slide)
        top = hex_to_rgb(bg_color_top)
        bottom = hex_to_rgb(bg_color_bottom)
        for x in range(slide_w):
            t = x / slide_w
            r = int(top[0] + (bottom[0] - top[0]) * t)
            g = int(top[1] + (bottom[1] - top[1]) * t)
            b = int(top[2] + (bottom[2] - top[2]) * t)
            draw_grad.line([(x, 0), (x, slide_h)], fill=(r, g, b))
        slide = slide.convert("RGBA")

    draw = ImageDraw.Draw(slide)
    margin = int(slide_h * SAFE_MARGIN_RATIO)

    # Затемняющая плашка снизу под текст
    overlay_h = int(slide_h * 0.45)
    overlay = Image.new("RGBA", (slide_w, overlay_h), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    for y in range(overlay_h):
        t = y / overlay_h
        alpha = int(t * 190)
        overlay_draw.line([(0, y), (slide_w, y)], fill=(0, 0, 0, alpha))
    slide.paste(overlay, (0, slide_h - overlay_h), overlay)

    # Иконка в углу
    icon_size = int(slide_h * 0.22)
    icon_img = load_svg_icon(icon_name, icon_size, color="#FFFFFF")
    slide.paste(icon_img, (slide_w - margin - icon_size, margin), icon_img)

    # Цена (если задана — только на первом слайде обычно)
    text_start_y = margin
    if price_text:
        price_clean = sanitize_text(price_text)
        price_font = ImageFont.truetype(FONT_EXTRABOLD, int(slide_h * 0.11))
        bbox = draw.textbbox((0, 0), price_clean, font=price_font)
        pw2, ph2 = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad = int(slide_h * 0.04)
        badge_w, badge_h = pw2 + pad * 2, ph2 + pad * 2
        draw.rounded_rectangle(
            [(margin, margin), (margin + badge_w, margin + badge_h)],
            radius=badge_h // 2, fill=(255, 255, 255, 235),
        )
        draw.text((margin + pad, margin + pad - bbox[1]), price_clean, font=price_font, fill=accent_color)

    # Основной текст слайда, снизу
    main_text_clean = sanitize_text(main_text)
    max_w = slide_w - 2 * margin - icon_size - margin
    font = fit_text_font(draw, main_text_clean, FONT_BLACK, max_w, int(slide_h * 0.16))
    bbox = draw.textbbox((0, 0), main_text_clean, font=font)
    text_h = bbox[3] - bbox[1]
    text_y = slide_h - margin - text_h
    draw_text_with_shadow(draw, (margin, text_y), main_text_clean, font)

    return slide


def generate_carousel_banner_v2(
    slide1_text: str,
    slide1_photo_query: str,
    slide2_text: str,
    slide2_photo_query: str,
    slide3_text: str,
    slide3_photo_query: str,
    price_text: str = "",
    icon_name: str = "star.svg",
    accent_color: str = "#FF6B35",
    bg_color_top: str = "#FF6B35",
    bg_color_bottom: str = "#F7931E",
    variant: str = "pc",
    output_dir: str = "/tmp",
    photo_source: str = "ai",
    ai_quality: str = "medium", account_id: str = None, operation: str = "генерация изображения",
) -> list[str]:
    """Карусель для Максимального тарифа — КАЖДЫЙ слайд самостоятельный
    (своё фото + свой текст), единый стиль (цвета/шрифт/иконка)."""
    if variant == "mobile":
        slide_w, slide_h = 1420, 960
    else:
        slide_w, slide_h = 1304, 480

    slides_config = [
        (slide1_photo_query, slide1_text, price_text),
        (slide2_photo_query, slide2_text, ""),
        (slide3_photo_query, slide3_text, ""),
    ]

    saved_paths = []
    for i, (query, text, price) in enumerate(slides_config):
        slide_img = _generate_single_slide(
            photo_query=query,
            main_text=text,
            slide_w=slide_w,
            slide_h=slide_h,
            icon_name=icon_name,
            price_text=price,
            accent_color=accent_color,
            bg_color_top=bg_color_top,
            bg_color_bottom=bg_color_bottom,
            photo_source=photo_source,
            ai_quality=ai_quality, account_id=account_id, operation=operation,
        )
        path = f"{output_dir}/carousel_v2_{variant}_slide_{i+1}.png"
        slide_img.convert("RGB").save(path, "PNG", quality=95)
        saved_paths.append(path)

    return saved_paths


def generate_ai_image(prompt: str, save_path: str, size: str = "1024x1024", quality: str = "low", model: str = "gpt-image-1-mini", account_id: str = None, operation: str = "генерация изображения") -> str | None:
    """
    Генерирует изображение через OpenAI Images API (GPT Image).
    quality: 'low' | 'medium' | 'high'. model: 'gpt-image-1-mini' (дёшево) | 'gpt-image-2' (флагман).
    Возвращает путь к сохранённому файлу или None при ошибке.
    """
    import requests
    import base64
    from proxy_pool import get_intl_requests_proxies

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None

    max_attempts = 3
    logged = set()
    for attempt in range(1, max_attempts + 1):
        try:
            proxies = get_intl_requests_proxies()  # СВЕЖИЙ случайный порт на КАЖДУЮ попытку -
            # раньше порт брался один раз до цикла, и retry был бесполезен против мёртвого порта
            resp = requests.post(
                "https://api.openai.com/v1/images/generations",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": model, "prompt": prompt, "size": size, "quality": quality, "n": 1},
                proxies=proxies,
                timeout=240,
            )
            if resp.status_code != 200:
                print(f"[generate_ai_image] Попытка {attempt}: статус {resp.status_code}, {resp.text[:200]}", flush=True)
                continue
            rid = resp.headers.get("x-request-id")
            try:
                data = resp.json()
                img_b64 = (data.get("data") or [{}])[0].get("b64_json")
            except Exception as _pe:
                img_b64 = None
                print(f"[generate_ai_image] Попытка {attempt}: ответ 200, но разбор не удался: {str(_pe)[:120]}", flush=True)
            if not img_b64:
                print(f"[generate_ai_image] Попытка {attempt}: ответ 200 без b64_json (request_id={rid}) - расход не записан", flush=True)
                continue
            # провайдер вернул ОПЛАЧИВАЕМЫЙ результат - учитываем ДО локальной обработки
            if rid not in logged:
                try:
                    from app.usage import log_usage as _log_img
                    _log_img(account_id, "openai", model, operation,
                             images=1, request_id=rid,
                             size=size, quality=quality)
                    if rid:
                        logged.add(rid)
                except Exception as _ue:
                    print("[usage]", str(_ue)[:100], flush=True)
            try:
                decoded = base64.b64decode(img_b64)
                with open(save_path, "wb") as f:
                    f.write(decoded)
            except Exception as _fe:
                print(f"[generate_ai_image] Попытка {attempt}: ответ учтён, но файл не сохранён: {str(_fe)[:120]}", flush=True)
                continue
            print(f"[generate_ai_image] Сгенерировано через {model}/{quality} (попытка {attempt}): «{prompt[:60]}...»", flush=True)
            return save_path
        except Exception as e:
            print(f"[generate_ai_image] Попытка {attempt} не удалась: {e}", flush=True)
            continue

    return None


def _generate_diagonal_slide(
    photo_query: str,
    main_text: str,
    slide_w: int,
    slide_h: int,
    icon_name: str = "star.svg",
    price_text: str = "",
    accent_color: str = "#1A56DB",
    bg_color: str = "#0B0F1A",
    photo_source: str = "ai",
    ai_quality: str = "medium",
    diagonal_split: float = 0.42,  # доля ширины, где проходит наклонная граница (0..1)
    diagonal_skew: int = 140,      # насколько наклонена граница, в пикселях
    advantages: list[str] | None = None,  # список пунктов преимуществ с чекбоксами
    company_name: str = "",  # название компании — маленький блок над заголовком
    phone: str = "",  # телефон — нижняя контактная полоса
    address: str = "",  # адрес/зона доставки — нижняя контактная полоса
    ribbon_text: str = "",  # угловая лента-бейдж, напр. "ОПЫТ 10+ ЛЕТ"
    ribbon_color: str = "#DC2626", account_id: str = None, operation: str = "генерация изображения",  # цвет ленты (обычно красный, контрастный к accent_color)
) -> Image.Image:
    """
    Слайд с ДИАГОНАЛЬНОЙ нарезкой: фото занимает параллелограмм слева,
    остальная часть — сплошной тёмный/брендовый фон с текстом справа.
    Ближе к референсам конкурентов (диагональные секторы, а не полноэкранное фото).
    """
    from PIL import ImageFilter

    def hex_to_rgb(h):
        h = h.lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

    # 1. Фон — сплошной тёмный/брендовый цвет на весь слайд
    slide = Image.new("RGB", (slide_w, slide_h), bg_color).convert("RGBA")
    draw = ImageDraw.Draw(slide)

    # 2. Получаем фото (AI или Pexels)
    photo_path = None
    if photo_query:
        if photo_source == "ai":
            ai_prompt = f"{photo_query}, photorealistic, professional advertising photography, high quality, natural lighting"
            photo_path = generate_ai_image(
                ai_prompt, f"/tmp/_diag_ai_{abs(hash(photo_query))}.png",
                size="1024x1024", quality=ai_quality, model="gpt-image-1-mini", account_id=account_id, operation=operation,
            )
        else:
            photo_path = get_photo_stock(photo_query, f"/tmp/_diag_bg_{abs(hash(photo_query))}.jpg", orientation="landscape")

    if photo_path:
        photo = Image.open(photo_path).convert("RGB")
        pw, ph = photo.size
        # cover-заполнение области фото (левая часть слайда, шире чем сам параллелограмм — на diagonal_skew)
        photo_area_w = int(slide_w * diagonal_split) + diagonal_skew
        scale = max(photo_area_w / pw, slide_h / ph)
        new_w, new_h = int(pw * scale), int(ph * scale)
        photo = photo.resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - photo_area_w) // 2
        top_crop = (new_h - slide_h) // 2
        photo = photo.crop((left, top_crop, left + photo_area_w, top_crop + slide_h))

        # Параллелограмм-маска: левая грань вертикальная, правая — наклонная
        split_x = int(slide_w * diagonal_split)
        mask = Image.new("L", (photo_area_w, slide_h), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.polygon(
            [(0, 0), (split_x + diagonal_skew, 0), (split_x, slide_h), (0, slide_h)],
            fill=255,
        )
        photo_rgba = photo.convert("RGBA")
        slide.paste(photo_rgba, (0, 0), mask)
    else:
        split_x = int(slide_w * diagonal_split)

    # 3. Тонкая цветная окантовка по диагональному стыку (акцентная линия)
    draw.line(
        [(split_x + diagonal_skew, 0), (split_x, slide_h)],
        fill=hex_to_rgb(accent_color) + (255,), width=max(4, slide_h // 80),
    )

    margin = int(slide_h * SAFE_MARGIN_RATIO)
    text_zone_x = split_x + diagonal_skew + margin
    text_zone_w = slide_w - text_zone_x - margin

    # 4. Иконка-акцент в правом верхнем углу
    icon_size = int(slide_h * 0.2)
    icon_img = load_svg_icon(icon_name, icon_size, color=accent_color)
    slide.paste(icon_img, (slide_w - margin - icon_size, margin), icon_img)

    # 5. Цена — плашка у левого края текстовой зоны
    text_top = margin
    if price_text:
        price_clean = sanitize_text(price_text)
        price_font = ImageFont.truetype(FONT_EXTRABOLD, int(slide_h * 0.13))
        bbox = draw.textbbox((0, 0), price_clean, font=price_font)
        pw2, ph2 = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad = int(slide_h * 0.04)
        badge_w, badge_h = pw2 + pad * 2, ph2 + pad * 2
        draw.rounded_rectangle(
            [(text_zone_x, margin), (text_zone_x + badge_w, margin + badge_h)],
            radius=badge_h // 2, fill=hex_to_rgb(accent_color) + (255,),
        )
        draw.text((text_zone_x + pad, margin + pad - bbox[1]), price_clean, font=price_font, fill="#FFFFFF")
        text_top = margin + badge_h + int(slide_h * 0.06)

    # 5.5. Название компании — маленький текст над заголовком (с проверкой ширины)
    if company_name:
        company_clean = sanitize_text(company_name)
        company_font = fit_text_font(draw, company_clean, FONT_BOLD, max(text_zone_w, 50), int(slide_h * 0.06))
        draw.text((text_zone_x, text_top), company_clean, font=company_font, fill=accent_color)
        cbbox = draw.textbbox((0, 0), company_clean, font=company_font)
        text_top += (cbbox[3] - cbbox[1]) + int(slide_h * 0.03)

    # 6. Заголовок — крупный, в текстовой зоне справа от диагонали
    main_text_clean = sanitize_text(main_text)
    font = fit_text_font(draw, main_text_clean, FONT_BLACK, max(text_zone_w, 50), int(slide_h * 0.17))
    draw_text_with_shadow(draw, (text_zone_x, text_top), main_text_clean, font, fill="#FFFFFF")

    # 7. Список преимуществ с чекбоксами (под заголовком)
    if advantages:
        title_bbox = draw.textbbox((0, 0), main_text_clean, font=font)
        title_h = title_bbox[3] - title_bbox[1]
        adv_y = text_top + title_h + int(slide_h * 0.08)
        base_adv_font_size = int(slide_h * 0.075)
        check_size = int(base_adv_font_size * 1.1)
        line_gap = int(slide_h * 0.035)
        adv_max_w = text_zone_w - check_size - int(slide_h * 0.025)

        for adv_text in advantages:
            adv_clean = sanitize_text(adv_text)
            adv_font = fit_text_font(draw, adv_clean, FONT_BOLD, max(adv_max_w, 50), base_adv_font_size)
            check_icon = load_svg_icon("circle-check.svg", check_size, color=accent_color)
            slide.paste(check_icon, (text_zone_x, adv_y), check_icon)
            text_x = text_zone_x + check_size + int(slide_h * 0.025)
            adv_bbox = draw.textbbox((0, 0), adv_clean, font=adv_font)
            text_y_offset = (check_size - (adv_bbox[3] - adv_bbox[1])) // 2 - adv_bbox[1]
            draw.text((text_x, adv_y + text_y_offset), adv_clean, font=adv_font, fill="#E8ECF5")
            adv_y += check_size + line_gap

    # 8. Нижняя контактная полоса на всю ширину
    if phone or address:
        bar_h = int(slide_h * 0.14)
        bar_y = slide_h - bar_h
        draw.rectangle([(0, bar_y), (slide_w, slide_h)], fill=hex_to_rgb(accent_color) + (255,))

        contact_font = ImageFont.truetype(FONT_BOLD, int(bar_h * 0.42))
        contact_parts = [p for p in [phone, address] if p]
        contact_text = "   |   ".join(sanitize_text(p) for p in contact_parts)
        cbbox = draw.textbbox((0, 0), contact_text, font=contact_font)
        cth = cbbox[3] - cbbox[1]
        cty = bar_y + (bar_h - cth) // 2 - cbbox[1]

        phone_icon_size = int(bar_h * 0.55)
        phone_icon = load_svg_icon("phone-call.svg", phone_icon_size, color="#FFFFFF")
        icon_x = margin
        slide.paste(phone_icon, (icon_x, bar_y + (bar_h - phone_icon_size) // 2), phone_icon)

        draw.text((icon_x + phone_icon_size + int(slide_h * 0.03), cty), contact_text, font=contact_font, fill="#FFFFFF")

    # 9. Угловая лента-бейдж в верхнем левом углу (поверх фото-зоны)
    if ribbon_text:
        ribbon_clean = sanitize_text(ribbon_text)
        ribbon_font = ImageFont.truetype(FONT_EXTRABOLD, int(slide_h * 0.07))
        ribbon_w = int(slide_h * 1.0)
        ribbon_h = int(slide_h * 0.16)
        ribbon_layer = Image.new("RGBA", (ribbon_w, ribbon_h), (0, 0, 0, 0))
        ribbon_draw = ImageDraw.Draw(ribbon_layer)
        ribbon_draw.rectangle([(0, 0), (ribbon_w, ribbon_h)], fill=hex_to_rgb(ribbon_color) + (255,))
        rbbox = ribbon_draw.textbbox((0, 0), ribbon_clean, font=ribbon_font)
        rtw, rth = rbbox[2] - rbbox[0], rbbox[3] - rbbox[1]
        ribbon_draw.text(((ribbon_w - rtw) // 2, (ribbon_h - rth) // 2 - rbbox[1]), ribbon_clean, font=ribbon_font, fill="#FFFFFF")
        ribbon_rotated = ribbon_layer.rotate(-45, expand=True, resample=Image.BICUBIC)
        rx = -ribbon_rotated.width // 4
        ry = -ribbon_rotated.height // 4
        slide.paste(ribbon_rotated, (rx, ry), ribbon_rotated)

    return slide
