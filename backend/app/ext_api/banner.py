# -*- coding: utf-8 -*-
"""Фабрика рекламных баннеров: макет от Claude, пиксели от сервера.

Claude Design серверной автоматизации не имеет — проверено по реестру
коннекторов, MCP-сервера у него нет. Поэтому роль разделена так, как она и
должна быть разделена:

    Claude (подписка Max, уже работает на сервере) — бриф, копирайт,
    иерархия, выбор фото, концепции A/B/C;
    этот модуль — детерминированная отрисовка в нужных размерах.

Ни одного платного вызова генерации изображений здесь нет. Фотографию берём
у клиента; выдумывать её нельзя, как нельзя выдумывать цену, сроки и
гарантии — баннер уходит клиенту под именем владельца.

    venv/bin/python3 -m app.ext_api.banner demo --out /tmp/b
"""
import hashlib, io, json, os

try:
    from PIL import (Image, ImageChops, ImageDraw, ImageFont,
                     ImageFilter, ImageStat)
except Exception:                                   # рендер не обязателен для импорта
    Image = None

FONT_DIR = "/usr/share/fonts/truetype/dejavu"
FONTS = {"bold": "DejaVuSans-Bold.ttf", "regular": "DejaVuSans.ttf",
         "cond": "DejaVuSansCondensed-Bold.ttf"}

# Каналы и размеры. Владелец задал минимум; остальное подбирается по каналу.
FORMATS = {
    "4:5":   (1080, 1350),
    "1:1":   (1080, 1080),
    "16:9":  (1200, 628),
}
CHANNEL_FORMAT = {"avito": "4:5", "vk": "1:1", "telegram": "1:1", "web": "16:9"}

VARIANTS = ("A", "B", "C")

# Семейства композиций. Один шаблон на пятьсот баннеров — это не фабрика, а
# копир: клиент видит одинаковые картинки и перестаёт их замечать. Здесь
# меняется само устройство макета, а не только цвет.
LAYOUTS = ("stack",        # заголовок сверху, призыв снизу
           "hero_bottom",  # фото во весь кадр, текст в нижней трети
           "split",        # цветная плашка сверху, текст под ней
           "badge_left",   # вертикальная лента с акцентом слева
           "price_first")  # оффер крупно, заголовок под ним

# Насколько сильно два баннера должны отличаться, чтобы считаться разными.
AHASH_MIN_DISTANCE = int(os.environ.get("BORIS_BANNER_AHASH_MIN", "6"))
MAX_REPAIRS = int(os.environ.get("BORIS_BANNER_REPAIRS", "3"))

# Слова, которых не должно быть в готовом баннере: это следы шаблона.
PLACEHOLDER_SIGNS = ("lorem", "placeholder", "образец", "текст сюда", "xxx",
                     "todo", "заголовок", "ваш текст")


# ------------------------------------------------------------------ контракт

def brief(client_id, headline, cta, city=None, subheadline=None, price=None,
          usp=None, source_image=None, brand=None, logo=None, channel="avito",
          fmt=None, style=None, campaign_id=None):
    """Бриф на баннер. Пустые поля остаются пустыми — не выдумываются."""
    return {
        "client_id": client_id, "campaign_id": campaign_id,
        "source_image": source_image, "headline": headline,
        "subheadline": subheadline, "price": price, "usp": list(usp or []),
        "cta": cta, "brand_colors": (brand or {}), "logo": logo,
        "format": fmt or CHANNEL_FORMAT.get(channel, "4:5"),
        "channel": channel, "style": style or "auto",
    }


def palette(brand, variant):
    """Цвета клиента, если он их дал. Иначе — сдержанная схема под вариант."""
    b = brand or {}
    base = {
        "A": {"bg": (16, 22, 34), "bg2": (33, 46, 70), "ink": (255, 255, 255),
              "muted": (186, 198, 216), "accent": (255, 199, 44),
              "on_accent": (20, 24, 32)},
        "B": {"bg": (246, 247, 250), "bg2": (226, 232, 242), "ink": (15, 22, 38),
              "muted": (96, 108, 132), "accent": (0, 106, 255),
              "on_accent": (255, 255, 255)},
        "C": {"bg": (28, 30, 34), "bg2": (12, 13, 16), "ink": (255, 255, 255),
              "muted": (190, 198, 210), "accent": (255, 92, 46),
              "on_accent": (255, 255, 255)},
    }[variant]
    if b.get("accent"):
        base["accent"] = tuple(b["accent"])
        base["on_accent"] = _readable_on(base["accent"])
    return base


def _readable_on(rgb):
    lum = (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255.0
    return (20, 24, 32) if lum > 0.55 else (255, 255, 255)


def _contrast(a, b):
    def L(c):
        s = [x / 255.0 for x in c]
        s = [(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4) for v in s]
        return 0.2126 * s[0] + 0.7152 * s[1] + 0.0722 * s[2]
    l1, l2 = sorted([L(a), L(b)], reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


# -------------------------------------------------------------------- рисунок

def _font(kind, size):
    return ImageFont.truetype(os.path.join(FONT_DIR, FONTS[kind]), size)


def _fit(draw, text, kind, maxw, start, minimum=22):
    """Кегль подбирается под ширину. Обрезанный текст — это брак, а не мелочь."""
    size = start
    while size > minimum and draw.textlength(text, font=_font(kind, size)) > maxw:
        size -= 2
    return _font(kind, size)


def _wrap(draw, text, fnt, maxw):
    out, cur = [], ""
    for w in (text or "").split():
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=fnt) <= maxw or not cur:
            cur = t
        else:
            out.append(cur)
            cur = w
    if cur:
        out.append(cur)
    return out


def _chip(d, x, y, text, fnt, fill, tcolor, padx=34, pady=20, radius=None,
          boxes=None):
    tw = d.textlength(text, font=fnt)
    bb = fnt.getbbox("Ay")
    h = (bb[3] - bb[1]) + pady * 2
    w = tw + padx * 2
    d.rounded_rectangle((x, y, x + w, y + h),
                        radius=(int(h / 2) if radius is None else radius), fill=fill)
    d.text((x + padx, y + pady - bb[1]), text, font=fnt, fill=tcolor)
    if boxes is not None:
        boxes.append((x, y, x + w, y + h))
    return x + w, y + h


def _backdrop(W, H, pal, photo=None):
    """Фон: фото клиента, если оно есть. Иначе — честная подложка без стока."""
    if photo and os.path.isfile(photo):
        im = Image.open(photo).convert("RGB")
        # Кадрируем по центру без искажения пропорций: растянутое фото
        # клиента — это брак, который видно сразу.
        k = max(W / im.width, H / im.height)
        im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))),
                       Image.LANCZOS)
        im = im.crop(((im.width - W) // 2, (im.height - H) // 2,
                      (im.width - W) // 2 + W, (im.height - H) // 2 + H))
        return im, True
    im = Image.new("RGB", (W, H), pal["bg"])
    d = ImageDraw.Draw(im)
    for y in range(H):
        t = y / float(H - 1)
        d.line([(0, y), (W, y)], fill=tuple(
            int(pal["bg"][i] + (pal["bg2"][i] - pal["bg"][i]) * t) for i in range(3)))
    return im, False


def _veil(im, pal, strength=200, from_y=0.42):
    """Тёмная вуаль под текст: без неё текст на фото не читается с телефона."""
    W, H = im.size
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    y0 = int(H * from_y)
    for y in range(y0, H):
        t = (y - y0) / float(max(1, H - y0))
        d.line([(0, y), (W, y)], fill=(8, 10, 14, int(strength * (t ** 0.7))))
    return Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")


def ahash(path, size=16):
    """Отпечаток картинки для поиска почти-дублей. Точное совпадение байтов
    ловит только копии; глазу же одинаковыми кажутся и слегка разные макеты."""
    im = Image.open(path).convert("L").resize((size, size), Image.LANCZOS)
    px = list(im.getdata())
    avg = sum(px) / float(len(px))
    return "".join("1" if p > avg else "0" for p in px)


def ahash_distance(a, b):
    return sum(1 for x, y in zip(a, b) if x != y)


# ------------------------------------------------------------------ варианты

def _layout_for(b, variant, seed=0):
    """Какой макет взять. Выбор детерминированный: одинаковый бриф даёт
    одинаковый баннер, а соседние товары — разные."""
    forced = (b.get("style") or "auto")
    if forced in LAYOUTS:
        return forced
    key = "%s|%s|%s" % (b.get("client_id"), b.get("headline"), variant)
    h = int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16) + int(seed)
    return LAYOUTS[h % len(LAYOUTS)]


def render(b, variant="A", fmt=None, out=None, seed=0, shrink=0.0):
    """Один баннер. Возвращает контракт результата, а не только файл.

    shrink — запас, который добавляет авто-починка: если ОТК нашёл тесноту,
    следующий проход рисует то же самое чуть спокойнее, а не заново с нуля.
    """
    if Image is None:
        raise RuntimeError("PIL недоступен — рендер баннеров не соберётся")
    fmt = fmt or b.get("format") or "4:5"
    W, H = FORMATS[fmt]
    pal = palette(b.get("brand_colors"), variant)
    layout = _layout_for(b, variant, seed)
    photo = b.get("source_image")
    im, has_photo = _backdrop(W, H, pal, photo)
    k = max(0.72, 1.0 - shrink)                 # общий коэффициент кегля

    if has_photo or layout == "hero_bottom":
        im = _veil(im, pal, strength=215,
                   from_y=0.30 if fmt != "16:9" else 0.18)
        pal = dict(pal, ink=(255, 255, 255), muted=(206, 214, 226))
    d = ImageDraw.Draw(im, "RGBA")
    m = int(W * 0.075)
    used = {}
    boxes = []                       # где реально лежит текст, а не «на глаз»

    def put(x, y, text, fnt, fill):
        d.text((x, y), text, font=fnt, fill=fill)
        bb = d.textbbox((x, y), text, font=fnt)
        boxes.append(bb)
        return bb

    if layout == "badge_left":
        d.rectangle((0, 0, int(W * 0.018), H), fill=pal["accent"])
        m = int(W * 0.095)
    if layout == "split":
        d.rectangle((0, 0, W, int(H * 0.30)), fill=pal["accent"])

    # --- верхняя метка
    top = m if layout != "hero_bottom" else int(H * 0.055)
    if b.get("city"):
        chip_bg = pal["accent"] if layout != "split" else (255, 255, 255)
        chip_fg = pal["on_accent"] if layout != "split" else pal["accent"]
        if has_photo or layout == "hero_bottom":
            chip_bg, chip_fg = (255, 255, 255, 46), (255, 255, 255)
        _, hh = _chip(d, m, top, b["city"].upper(),
                      _font("bold", max(20, int(W * 0.030 * k))), chip_bg, chip_fg,
                      boxes=boxes)
        top += hh + int(H * 0.030)
        used["city"] = b["city"]

    # --- блок цены раньше заголовка в этом семействе
    def draw_price(y):
        if not b.get("price"):
            return y
        pf = _font("bold", max(28, int(W * 0.068 * k)))
        put(m, y, str(b["price"]), pf, pal["accent"])
        used["price"] = b["price"]
        return y + int(pf.size * 1.25)

    if layout == "price_first":
        top = draw_price(top)

    # --- заголовок
    head = (b.get("headline") or "").strip()
    base_size = 0.085 if fmt == "16:9" else (0.088 if layout == "hero_bottom" else 0.098)
    hf = _font("bold", max(30, int(W * base_size * k)))
    maxlines = 2 if fmt == "16:9" else 3
    lines = _wrap(d, head, hf, W - 2 * m)
    while len(lines) > maxlines and hf.size > 28:
        hf = _font("bold", hf.size - 4)
        lines = _wrap(d, head, hf, W - 2 * m)

    if layout == "hero_bottom":
        # Текст живёт в нижней трети: считаем высоту блока и ставим от низа.
        block = len(lines) * int(hf.size * 1.16) + int(H * 0.16)
        top = max(int(H * 0.42), H - m - block - int(H * 0.10))
    head_color = pal["ink"] if layout != "split" or top > int(H * 0.30) \
        else pal["on_accent"]
    for ln in lines:
        put(m, top, ln, hf, head_color)
        top += int(hf.size * 1.16)
    used["headline"] = head
    top += int(H * 0.020)

    if b.get("subheadline"):
        sf = _font("regular", max(20, int(W * 0.038 * k)))
        for ln in _wrap(d, b["subheadline"], sf, W - 2 * m)[:2]:
            put(m, top, ln, sf, pal["muted"])
            top += int(sf.size * 1.35)
        used["subheadline"] = b["subheadline"]
        top += int(H * 0.010)

    if b.get("usp") and layout != "hero_bottom":
        uf = _font("regular", max(19, int(W * 0.034 * k)))
        bar_top = top
        for ln in b["usp"][:3]:
            put(m + int(W * 0.030), top, ln, uf, pal["muted"])
            top += int(uf.size * 1.5)
        d.line([(m + 4, bar_top + 6), (m + 4, top - 10)], fill=pal["accent"], width=6)
        used["usp"] = b["usp"][:3]
        top += int(H * 0.012)

    if layout != "price_first":
        top = draw_price(top)

    # --- призыв: всегда внизу, всегда внутри безопасной зоны
    cf = _font("bold", max(20, int(W * (0.038 if fmt != "16:9" else 0.034) * k)))
    ch = (cf.getbbox("Ay")[3] - cf.getbbox("Ay")[1]) + int(W * 0.052)
    cy = min(max(top + int(H * 0.02), H - m - int(ch)), H - m - int(ch))
    _chip(d, m, cy, (b.get("cta") or "").upper(), cf, pal["accent"],
          pal["on_accent"], padx=int(W * 0.040), pady=int(W * 0.026),
          radius=int(W * 0.020), boxes=boxes)
    used["cta"] = b.get("cta")
    overlap = top > cy                      # текст наехал на кнопку

    if b.get("logo") and os.path.isfile(b["logo"]):
        lg = Image.open(b["logo"]).convert("RGBA")
        kk = min((W * 0.16) / lg.width, (H * 0.075) / lg.height)
        lg = lg.resize((max(1, int(lg.width * kk)), max(1, int(lg.height * kk))),
                       Image.LANCZOS)
        im.paste(lg, (W - m - lg.width, m), lg)
        used["logo"] = b["logo"]

    path = out or "banner_%s_%s.png" % (variant, fmt.replace(":", "x"))
    im.save(path, quality=95)
    return {"banner_file": path, "preview": path, "dimensions": [W, H],
            "variant": variant, "format": fmt, "layout": layout,
            "source_assets": [x for x in (photo, b.get("logo")) if x],
            "text_used": used, "where_saved": os.path.abspath(path),
            "openai_image_calls": 0, "_overlap": overlap, "_shrink": shrink,
            "text_boxes": boxes,
            "content_bottom": top, "cta_top": cy}


# --------------------------------------------------------------------- ОТК

def qa(result, brief_data):
    """Проверка до сохранения. Человек в ней не участвует."""
    problems = []
    W, H = result["dimensions"]
    try:
        im = Image.open(result["banner_file"])
        im.load()
        im = im.convert("RGB")
    except Exception as e:
        return {"pass": False, "problems": ["файл не открывается: %s" % e]}
    if im.size != (W, H):
        problems.append("размер не совпадает с заявленным")

    text = " ".join(str(v) for v in result["text_used"].values()).lower()
    if any(s in text for s in PLACEHOLDER_SIGNS):
        problems.append("остался placeholder-текст")
    if not result["text_used"].get("cta"):
        problems.append("нет призыва к действию")
    if not result["text_used"].get("headline"):
        problems.append("нет заголовка")
    if result.get("_overlap"):
        problems.append("текст наехал на кнопку")

    # Безопасная зона: проверяем рамки текста и плашек, а не пёстрость краёв.
    # Полоса слева и цветной блок сверху — это элементы макета «в край», и
    # раньше ОТК браковал ровно их, а настоящую тесноту не видел.
    safe = int(W * 0.045)
    for (x0, y0, x1, y1) in result.get("text_boxes") or []:
        if x0 < safe or y0 < safe or x1 > W - safe or y1 > H - safe:
            problems.append("текст выходит в безопасное поле")
            break

    # Перекрытия блоков между собой.
    bxs = sorted(result.get("text_boxes") or [], key=lambda b: b[1])
    for a, bb in zip(bxs, bxs[1:]):
        if a[3] > bb[1] + 2 and not (a[2] < bb[0] or bb[2] < a[0]):
            problems.append("блоки текста накладываются")
            break

    # Читаемость с телефона: уменьшаем до ширины экрана и смотрим, остался ли
    # заголовок различимым пятном, а не серой кашей.
    small = im.resize((360, int(360 * H / W)), Image.LANCZOS)
    if max(ImageStat.Stat(small).stddev) < 18:
        problems.append("на мобильном макет неразличим")

    if len(result["text_used"].get("headline", "")) > 90:
        problems.append("заголовок длиннее, чем читается с телефона")

    # Факты в баннере обязаны приходить из брифа.
    for field in ("price", "usp", "subheadline"):
        if result["text_used"].get(field) and not brief_data.get(field):
            problems.append("в баннере есть «%s», которого нет в брифе" % field)

    return {"pass": not problems, "problems": problems}


def make(b, variant="A", fmt=None, out=None, seed=0):
    """Отрисовать и довести до годного без человека.

    Провал ОТК — не повод звать владельца: макет пересобирается с большим
    воздухом, и так до предела попыток. Владельцу уходит только то, что
    фабрика починить не смогла.
    """
    shrink, repairs = 0.0, 0
    while True:
        r = render(b, variant=variant, fmt=fmt, out=out, seed=seed + repairs,
                   shrink=shrink)
        r["qa"] = qa(r, b)
        r["repairs"] = repairs
        if r["qa"]["pass"] or repairs >= MAX_REPAIRS:
            return r
        repairs += 1
        shrink += 0.09


def batch(briefs, out_dir, formats=("4:5",), variants_per_brief=1, seen=None):
    """Массовое производство. Возвращает измеримые показатели, а не бодрый
    отчёт: сколько сделано, сколько прошло ОТК с первого раза, сколько
    починено автоматически, сколько дублей, сколько вмешательств человека."""
    import time as _t
    os.makedirs(out_dir, exist_ok=True)
    seen = seen if seen is not None else []
    started = _t.time()
    ok = first_pass = repaired = failed = dup = 0
    times = []
    items = []
    for n, b in enumerate(briefs):
        for vi in range(variants_per_brief):
            v = VARIANTS[vi % len(VARIANTS)]
            for fmt in formats:
                path = os.path.join(out_dir, "%s_%04d_%s_%s.png" % (
                    b.get("client_id", "c"), n, v, fmt.replace(":", "x")))
                t0 = _t.time()
                try:
                    r = make(b, variant=v, fmt=fmt, out=path, seed=n)
                except Exception as e:
                    failed += 1
                    items.append({"brief": n, "error": "%s: %s" % (type(e).__name__, e)})
                    continue
                times.append(_t.time() - t0)
                if not r["qa"]["pass"]:
                    failed += 1
                    items.append({"brief": n, "file": path,
                                  "problems": r["qa"]["problems"]})
                    continue
                h = ahash(path)
                near = any(ahash_distance(h, s) < AHASH_MIN_DISTANCE for s in seen)
                if near:
                    dup += 1
                seen.append(h)
                ok += 1
                if r["repairs"] == 0:
                    first_pass += 1
                else:
                    repaired += 1
                items.append({"brief": n, "file": path, "layout": r["layout"],
                              "repairs": r["repairs"], "duplicate": near})
    spent = max(0.001, _t.time() - started)
    total = ok + failed
    times.sort()
    return {
        "requested": total, "rendered_ok": ok, "failed": failed,
        "valid_percent": round(100.0 * ok / max(1, total), 1),
        "qa_first_pass_percent": round(100.0 * first_pass / max(1, ok), 1),
        "auto_repair_percent": round(100.0 * repaired / max(1, ok), 1),
        "duplicate_percent": round(100.0 * dup / max(1, ok), 1),
        "median_seconds": round(times[len(times) // 2], 3) if times else None,
        "banners_per_hour": int(ok / spent * 3600),
        "seconds_total": round(spent, 1),
        "openai_calls": 0, "human_interventions": 0,
        "cost_per_banner": 0.0,
        "items": items[:5],
    }


def main():
    import argparse
    ap = argparse.ArgumentParser(prog="banner")
    ap.add_argument("cmd", choices=["demo"])
    ap.add_argument("--out", default="/tmp/banners")
    a = ap.parse_args()
    b = brief("demo", headline="Механизированная штукатурка",
              cta="Рассчитать стоимость", city="Большой Сочи")
    print(json.dumps([make(b, variant=v, out=os.path.join(a.out, "demo_%s.png" % v))
                      for v in VARIANTS], ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main() or 0)


# ------------------------------------------- коммерческая грамматика
# Разобрано по референсам владельца (детские комнаты, эвакуаторы, электро-
# байки). У всех семи одна и та же грамматика, и она воспроизводима:
# фотография-герой почти во весь кадр, тёмная полоса с двухцветным
# заголовком, ряд круглых иконок с подписями, плашка-обещание снизу,
# косая геометрия и одна акцентная краска на весь баннер.

FAMILIES = {
    "DARK_PREMIUM":   {"accent": (168, 85, 247), "ink": (255, 255, 255),
                       "band": (18, 10, 32), "band_alpha": 232, "glow": True,
                       "band_pos": "top", "skew": 0},
    "NEON_PRODUCT":   {"accent": (236, 72, 153), "ink": (255, 255, 255),
                       "band": (10, 8, 14), "band_alpha": 226, "glow": True,
                       "band_pos": "top", "skew": 0},
    "INDUSTRIAL":     {"accent": (214, 138, 60), "ink": (255, 255, 255),
                       "band": (26, 22, 20), "band_alpha": 224, "glow": False,
                       "band_pos": "middle", "skew": 26},
    "HIGH_CONTRAST":  {"accent": (255, 214, 0), "ink": (255, 255, 255),
                       "band": (14, 14, 16), "band_alpha": 235, "glow": False,
                       "band_pos": "middle", "skew": 18},
    "CORPORATE":      {"accent": (37, 99, 235), "ink": (255, 255, 255),
                       "band": (12, 28, 64), "band_alpha": 232, "glow": False,
                       "band_pos": "middle", "skew": 12},
    "SOFT_LIFESTYLE": {"accent": (139, 92, 246), "ink": (255, 255, 255),
                       "band": (46, 24, 82), "band_alpha": 236, "glow": False,
                       "band_pos": "top", "skew": 0},
    "LOCAL_SERVICE":  {"accent": (16, 185, 129), "ink": (255, 255, 255),
                       "band": (8, 32, 26), "band_alpha": 230, "glow": False,
                       "band_pos": "top", "skew": 10},
    "EDITORIAL":      {"accent": (244, 63, 94), "ink": (255, 255, 255),
                       "band": (18, 18, 20), "band_alpha": 222, "glow": False,
                       "band_pos": "middle", "skew": 0},
}


def _icon(d, cx, cy, r, kind, accent, ink, width):
    """Простые векторные знаки. Ряд круглых иконок — фирменный элемент
    референсов: без него баннер сразу выглядит самоделкой."""
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=accent, width=width)
    u = r * 0.46
    if kind == "shield":
        d.polygon([(cx, cy - u), (cx + u * .8, cy - u * .4),
                   (cx, cy + u), (cx - u * .8, cy - u * .4)], outline=ink, width=width)
        d.line([(cx - u * .35, cy), (cx - u * .05, cy + u * .32),
                (cx + u * .45, cy - u * .3)], fill=ink, width=width)
    elif kind == "truck":
        d.rectangle((cx - u, cy - u * .5, cx + u * .1, cy + u * .3), outline=ink, width=width)
        d.polygon([(cx + u * .12, cy - u * .1), (cx + u * .7, cy - u * .1),
                   (cx + u, cy + u * .3), (cx + u * .12, cy + u * .3)],
                  outline=ink, width=width)
        for x in (cx - u * .6, cx + u * .62):
            d.ellipse((x - u * .22, cy + u * .18, x + u * .22, cy + u * .62),
                      outline=ink, width=width)
    elif kind == "bolt":
        d.polygon([(cx - u * .2, cy - u), (cx + u * .5, cy - u * .1),
                   (cx + u * .05, cy - u * .1), (cx + u * .35, cy + u),
                   (cx - u * .5, cy + u * .05), (cx - u * .05, cy + u * .05)],
                  fill=ink)
    elif kind == "box":
        d.rectangle((cx - u * .8, cy - u * .5, cx + u * .8, cy + u * .6),
                    outline=ink, width=width)
        d.line([(cx - u * .8, cy - u * .05), (cx + u * .8, cy - u * .05)],
               fill=ink, width=width)
        d.line([(cx, cy - u * .5), (cx, cy + u * .6)], fill=ink, width=width)
    elif kind == "pin":
        d.ellipse((cx - u * .55, cy - u * .8, cx + u * .55, cy + u * .3),
                  outline=ink, width=width)
        d.line([(cx, cy + u * .28), (cx, cy + u)], fill=ink, width=width)
    elif kind in ("factory", "production"):
        d.polygon([(cx - u, cy + u * .6), (cx - u, cy - u * .1),
                   (cx - u * .3, cy + u * .25), (cx - u * .3, cy - u * .1),
                   (cx + u * .4, cy + u * .25), (cx + u * .4, cy - u * .55),
                   (cx + u, cy - u * .55), (cx + u, cy + u * .6)],
                  outline=ink, width=width)
    elif kind in ("delivery", "truck2"):
        d.rectangle((cx - u, cy - u * .45, cx + u * .15, cy + u * .25),
                    outline=ink, width=width)
        d.polygon([(cx + u * .18, cy - u * .05), (cx + u * .68, cy - u * .05),
                   (cx + u, cy + u * .25), (cx + u * .18, cy + u * .25)],
                  outline=ink, width=width)
        for x in (cx - u * .55, cx + u * .6):
            d.ellipse((x - u * .2, cy + u * .12, x + u * .2, cy + u * .52),
                      outline=ink, width=width)
    elif kind in ("warranty", "safety"):
        d.polygon([(cx, cy - u), (cx + u * .82, cy - u * .45),
                   (cx, cy + u), (cx - u * .82, cy - u * .45)],
                  outline=ink, width=width)
        d.line([(cx - u * .34, cy - u * .05), (cx - u * .05, cy + u * .3),
                (cx + u * .46, cy - u * .38)], fill=ink, width=width)
    elif kind in ("install", "service"):
        d.line([(cx - u * .6, cy + u * .6), (cx + u * .35, cy - u * .35)],
               fill=ink, width=width + 1)
        d.ellipse((cx + u * .25, cy - u * .85, cx + u * .95, cy - u * .15),
                  outline=ink, width=width)
    elif kind == "price":
        d.ellipse((cx - u * .85, cy - u * .85, cx + u * .85, cy + u * .85),
                  outline=ink, width=width)
        d.line([(cx, cy - u * .5), (cx, cy + u * .5)], fill=ink, width=width)
        d.arc((cx - u * .42, cy - u * .5, cx + u * .42, cy), 180, 360,
              fill=ink, width=width)
        d.arc((cx - u * .42, cy, cx + u * .42, cy + u * .5), 0, 180,
              fill=ink, width=width)
    elif kind in ("speed", "fast"):
        d.arc((cx - u * .85, cy - u * .85, cx + u * .85, cy + u * .85),
              200, 340, fill=ink, width=width)
        d.line([(cx, cy), (cx + u * .5, cy - u * .45)], fill=ink, width=width)
    elif kind == "quality":
        d.polygon([(cx, cy - u), (cx + u * .28, cy - u * .28),
                   (cx + u, cy - u * .22), (cx + u * .42, cy + u * .22),
                   (cx + u * .6, cy + u), (cx, cy + u * .55),
                   (cx - u * .6, cy + u), (cx - u * .42, cy + u * .22),
                   (cx - u, cy - u * .22), (cx - u * .28, cy - u * .28)],
                  outline=ink, width=width)
    elif kind in ("geo", "location"):
        d.ellipse((cx - u * .5, cy - u * .8, cx + u * .5, cy + u * .2),
                  outline=ink, width=width)
        d.line([(cx, cy + u * .18), (cx, cy + u)], fill=ink, width=width)
    elif kind == "gift":
        d.rectangle((cx - u * .8, cy - u * .3, cx + u * .8, cy + u * .7),
                    outline=ink, width=width)
        d.line([(cx, cy - u * .3), (cx, cy + u * .7)], fill=ink, width=width)
        d.arc((cx - u * .5, cy - u * .8, cx, cy - u * .2), 0, 180, fill=ink, width=width)
        d.arc((cx, cy - u * .8, cx + u * .5, cy - u * .2), 0, 180, fill=ink, width=width)
    elif kind in ("consult", "support"):
        d.rounded_rectangle((cx - u * .85, cy - u * .7, cx + u * .85, cy + u * .35),
                            radius=int(u * .3), outline=ink, width=width)
        d.line([(cx - u * .3, cy + u * .35), (cx - u * .1, cy + u * .85)],
               fill=ink, width=width)
    elif kind in ("term", "time"):
        d.ellipse((cx - u * .85, cy - u * .85, cx + u * .85, cy + u * .85),
                  outline=ink, width=width)
        d.line([(cx, cy - u * .45), (cx, cy)], fill=ink, width=width)
        d.line([(cx, cy), (cx + u * .4, cy + u * .2)], fill=ink, width=width)
    elif kind == "materials":
        d.polygon([(cx, cy - u * .8), (cx + u * .85, cy - u * .3),
                   (cx, cy + u * .2), (cx - u * .85, cy - u * .3)],
                  outline=ink, width=width)
        d.line([(cx - u * .85, cy + u * .2), (cx, cy + u * .7)], fill=ink, width=width)
        d.line([(cx + u * .85, cy + u * .2), (cx, cy + u * .7)], fill=ink, width=width)
    elif kind == "leaf":
        d.arc((cx - u, cy - u, cx + u, cy + u), 200, 20, fill=ink, width=width)
        d.line([(cx - u * .7, cy + u * .6), (cx + u * .6, cy - u * .6)],
               fill=ink, width=width)
    else:                                   # галочка по умолчанию
        d.line([(cx - u * .55, cy), (cx - u * .1, cy + u * .45),
                (cx + u * .6, cy - u * .5)], fill=ink, width=width)


def _skew_band(im, y0, y1, color, alpha, skew=0):
    """Полоса под заголовок. Скос — та самая «косая геометрия» референсов."""
    W, H = im.size
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    if skew:
        d.polygon([(0, y0), (W, y0 - skew), (W, y1 - skew), (0, y1)],
                  fill=color + (alpha,))
    else:
        d.rectangle((0, y0, W, y1), fill=color + (alpha,))
    return Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")


def _two_tone(d, x, y, parts, fnt, ink, accent, boxes=None):
    """Заголовок в два цвета: главное слово акцентом. Это то, что делает
    заголовок рекламным, а не подписью к картинке."""
    for text, hot in parts:
        d.text((x, y), text, font=fnt, fill=(accent if hot else ink))
        bb = d.textbbox((x, y), text, font=fnt)
        if boxes is not None:
            boxes.append(bb)
        x = bb[2] + d.textlength(" ", font=fnt)
    return x


def commercial(spec, out="banner.png"):
    """Баннер по грамматике референсов.

    spec: family, headline (список пар «текст, акцент?»), subtitle, icons
    (до трёх: kind + две строки подписи), price, cta, photo, canvas.
    """
    fam = FAMILIES.get(spec.get("family") or "DARK_PREMIUM",
                       FAMILIES["DARK_PREMIUM"])
    W, H = FORMATS.get(spec.get("canvas") or "1:1", FORMATS["1:1"])
    pal_accent, ink = fam["accent"], fam["ink"]
    im, has_photo = _backdrop(W, H, {"bg": (16, 16, 20), "bg2": (8, 8, 10)},
                              spec.get("photo"))
    if fam["glow"]:
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        gd = ImageDraw.Draw(ov)
        gd.ellipse((W * 0.12, H * 0.32, W * 0.88, H * 0.92),
                   fill=pal_accent + (46,))
        im = Image.alpha_composite(im.convert("RGBA"),
                                   ov.filter(ImageFilter.GaussianBlur(W * 0.06))
                                   ).convert("RGB")
    im = _veil(im, {}, strength=150, from_y=0.55)

    m = int(W * 0.072)
    boxes = []
    band_top = int(H * (0.055 if fam["band_pos"] == "top" else 0.26))
    lines = spec.get("headline") or []
    # Кегль подбирается под самую длинную строку: двухцветный заголовок
    # состоит из кусков, и складывать их ширину надо до отрисовки, а не
    # обнаруживать обрезку постфактум.
    _probe = ImageDraw.Draw(im)

    def _line_w(parts, f):
        sp = _probe.textlength(" ", font=f)
        return sum(_probe.textlength(t, font=f) for t, _ in parts) + sp * (len(parts) - 1)

    hf = _font("bold", int(W * 0.088))
    while lines and hf.size > 26 and max(_line_w(p, hf) for p in lines) > W - 2 * int(W * 0.072):
        hf = _font("bold", hf.size - 3)
    sub = spec.get("subtitle")
    band_h = len(lines) * int(hf.size * 1.14) + (int(W * 0.062) if sub else 0) \
        + int(H * 0.055)
    im = _skew_band(im, band_top, band_top + band_h, fam["band"],
                    fam["band_alpha"], fam["skew"])
    d = ImageDraw.Draw(im, "RGBA")

    y = band_top + int(H * 0.028)
    for parts in lines:
        _two_tone(d, m, y, parts, hf, ink, pal_accent, boxes)
        y += int(hf.size * 1.14)
    if sub:
        sf = _font("regular", int(W * 0.034))
        d.line([(m, y + int(W * 0.012)), (m + int(W * 0.055), y + int(W * 0.012))],
               fill=pal_accent, width=max(2, int(W * 0.004)))
        d.text((m + int(W * 0.070), y), sub, font=sf, fill=(226, 230, 238))
        boxes.append(d.textbbox((m + int(W * 0.070), y), sub, font=sf))
        y += int(sf.size * 1.6)

    icons = (spec.get("icons") or [])[:3]
    if icons:
        r = int(W * 0.052)
        step = W / (len(icons) + 1.0)
        iy = y + int(H * 0.045)
        cf = _font("regular", int(W * 0.024))
        for n, ic in enumerate(icons):
            cx = int(step * (n + 1))
            _icon(d, cx, iy, r, ic.get("kind", "check"), pal_accent, ink,
                  max(2, int(W * 0.0055)))
            ty = iy + r + int(H * 0.022)
            for cap in (ic.get("caption") or "").split("\n")[:2]:
                tw = d.textlength(cap, font=cf)
                d.text((cx - tw / 2, ty), cap, font=cf, fill=(224, 228, 236))
                boxes.append((cx - tw / 2, ty, cx + tw / 2, ty + cf.size))
                ty += int(cf.size * 1.35)

    if spec.get("price"):
        pf = _font("bold", int(W * 0.055))
        _chip(d, m, int(H * 0.70), str(spec["price"]), pf, pal_accent,
              _readable_on(pal_accent), padx=int(W * 0.035),
              pady=int(W * 0.022), radius=int(W * 0.018), boxes=boxes)

    if spec.get("cta"):
        bf = _font("bold", int(W * 0.032))
        bh = int(W * 0.088)
        by = H - m - bh
        d.rounded_rectangle((m, by, W - m, by + bh), radius=int(bh / 2),
                            fill=pal_accent)
        t = spec["cta"]
        tw = d.textlength(t, font=bf)
        bb = bf.getbbox("Ay")
        d.text(((W - tw) / 2, by + (bh - (bb[3] - bb[1])) / 2 - bb[1]), t,
               font=bf, fill=_readable_on(pal_accent))
        boxes.append((m, by, W - m, by + bh))

    im.save(out, quality=95)
    return {"banner_file": out, "dimensions": [W, H],
            "family": spec.get("family"), "text_boxes": boxes,
            "text_used": {"headline": " ".join(t for ln in lines for t, _ in ln),
                          "cta": spec.get("cta"), "price": spec.get("price"),
                          "subheadline": sub},
            "openai_image_calls": 0, "_overlap": False,
            "where_saved": os.path.abspath(out)}


# ------------------------------------------------- расширенный движок
# Владелец: «выглядит слишком шаблонно». Разница между макетом и рекламой —
# не в количестве элементов, а в слоях: свет, глубина, стекло, геометрия и
# типографика, которая работает как композиция, а не как подпись.

ICONS = ("delivery", "warranty", "factory", "install", "price", "speed",
         "quality", "service", "geo", "gift", "consult", "term",
         "materials", "safety")

FAMILIES_PLUS = {
    "INDUSTRIAL_DARK": {"accent": (255, 176, 32), "band": (20, 18, 16),
                        "panel": "glass", "decor": "diagonal", "light": "warm",
                        "hero": "right", "vignette": 0.38},
    "TECH_NEON":       {"accent": (168, 85, 247), "band": (14, 10, 26),
                        "panel": "glass", "decor": "rings", "light": "cool",
                        "hero": "center", "vignette": 0.42},
    "LUXURY":          {"accent": (198, 166, 100), "band": (16, 15, 14),
                        "panel": "solid", "decor": "frame", "light": "warm",
                        "hero": "right", "vignette": 0.5},
    "LIGHT_LIFESTYLE": {"accent": (139, 92, 246), "band": (250, 249, 255),
                        "panel": "cards", "decor": "wave", "light": "soft",
                        "hero": "bottom", "vignette": 0.12},
    "CORPORATE_BAND":  {"accent": (37, 99, 235), "band": (10, 26, 60),
                        "panel": "solid", "decor": "chevron", "light": "cool",
                        "hero": "bottom", "vignette": 0.3},
    "PRICE_FIRST":     {"accent": (239, 68, 68), "band": (18, 16, 18),
                        "panel": "glass", "decor": "ribbon", "light": "warm",
                        "hero": "right", "vignette": 0.35},
    "EDITORIAL":       {"accent": (244, 63, 94), "band": (18, 18, 20),
                        "panel": "none", "decor": "rule", "light": "soft",
                        "hero": "full", "vignette": 0.28},
    "LOCAL_SERVICE":   {"accent": (16, 185, 129), "band": (8, 30, 24),
                        "panel": "glass", "decor": "corner", "light": "soft",
                        "hero": "right", "vignette": 0.3},
}


def _grad_v(size, c1, c2, alpha1=255, alpha2=0):
    """Вертикальный градиент с прозрачностью — основа мягких переходов."""
    W, H = size
    g = Image.new("RGBA", (W, H))
    d = ImageDraw.Draw(g)
    for y in range(H):
        t = y / float(max(1, H - 1))
        d.line([(0, y), (W, y)],
               fill=(int(c1[0] + (c2[0] - c1[0]) * t),
                     int(c1[1] + (c2[1] - c1[1]) * t),
                     int(c1[2] + (c2[2] - c1[2]) * t),
                     int(alpha1 + (alpha2 - alpha1) * t)))
    return g


def _vignette(im, strength=0.35):
    """Затемнение к краям: взгляд собирается в центр, как в референсах."""
    W, H = im.size
    mask = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(mask)
    d.ellipse((-W * 0.15, -H * 0.15, W * 1.15, H * 1.15), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(W * 0.10))
    dark = Image.new("RGB", (W, H), (0, 0, 0))
    return Image.composite(im, Image.blend(im, dark, min(0.85, strength)), mask)


def _glass(im, box, radius=None, tint=(255, 255, 255), alpha=26, blur=18):
    """Стеклянная панель: размытая подложка под текстом. Без неё текст на
    фотографии либо не читается, либо требует глухой плашки."""
    x0, y0, x1, y1 = [int(v) for v in box]
    radius = int((y1 - y0) * 0.18) if radius is None else radius
    region = im.crop((x0, y0, x1, y1)).filter(ImageFilter.GaussianBlur(blur))
    im.paste(region, (x0, y0))
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    d.rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=tint + (alpha,),
                        outline=(255, 255, 255, 46), width=2)
    return Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")


def _light_streak(im, accent, angle="diagonal", power=64):
    """Световая полоса — дешёвый и честный способ добавить глубины."""
    W, H = im.size
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    if angle == "diagonal":
        d.polygon([(W * 0.55, 0), (W * 0.78, 0), (W * 0.30, H), (W * 0.07, H)],
                  fill=accent + (power,))
    else:
        d.rectangle((0, H * 0.42, W, H * 0.52), fill=accent + (power,))
    ov = ov.filter(ImageFilter.GaussianBlur(W * 0.05))
    return Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")


def _rim_light(im, accent, side="right", warm=True):
    """Контровой свет по краю героя: он отделяет товар от фона."""
    W, H = im.size
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    c = (255, 214, 170) if warm else accent
    x = W * (0.86 if side == "right" else 0.14)
    d.ellipse((x - W * 0.30, H * 0.20, x + W * 0.30, H * 0.95), fill=c + (52,))
    ov = ov.filter(ImageFilter.GaussianBlur(W * 0.09))
    return Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")


def _decor(d, W, H, kind, accent, side="left"):
    """Геометрия. Не больше одного-двух акцентов, иначе получается ёлка.

    side говорит, с какой стороны кадра рисовать: акцент обязан жить там,
    где нет текста.
    """
    a = accent
    base = (W * 0.02) if side == "left" else (W * 0.86)
    if kind == "diagonal":
        for i, off in enumerate((0, W * 0.045, W * 0.09)):
            d.polygon([(base + off, H * 0.30), (base + W * 0.04 + off, H * 0.30),
                       (base + off, H * 0.42), (base - W * 0.04 + off, H * 0.42)],
                      fill=a + (255 - i * 60,))
    elif kind == "rings":
        for r in (W * 0.30, W * 0.38, W * 0.46):
            d.ellipse((W * 0.5 - r, H * 0.52 - r, W * 0.5 + r, H * 0.52 + r),
                      outline=a + (40,), width=max(2, int(W * 0.003)))
    elif kind == "chevron":
        d.polygon([(0, H * 0.30), (W, H * 0.26), (W, H * 0.30), (0, H * 0.34)],
                  fill=a + (210,))
    elif kind == "ribbon":
        d.polygon([(W * 0.62, 0), (W, 0), (W, H * 0.14), (W * 0.70, H * 0.14)],
                  fill=a + (235,))
    elif kind == "corner":
        w = max(3, int(W * 0.006))
        d.line([(W * 0.045, H * 0.045), (W * 0.20, H * 0.045)], fill=a, width=w)
        d.line([(W * 0.045, H * 0.045), (W * 0.045, H * 0.16)], fill=a, width=w)
    elif kind == "frame":
        w = max(2, int(W * 0.004))
        d.rounded_rectangle((W * 0.035, H * 0.035, W * 0.965, H * 0.965),
                            radius=int(W * 0.02), outline=a + (150,), width=w)
    elif kind == "wave":
        pts = [(x, H * 0.62 + (H * 0.02) * ((x / (W * 0.08)) % 2)) for x in
               range(0, W + 1, int(W * 0.04))]
        d.line(pts + [(W, H), (0, H)], fill=a + (26,), width=2)
    elif kind == "rule":
        d.line([(W * 0.072, H * 0.20), (W * 0.35, H * 0.20)], fill=a,
               width=max(3, int(W * 0.006)))


def design_score(result, spec):
    """Оценка коммерческой пригодности 0–100.

    Считается по картинке, а не по намерению: контраст, воздух, доля текста,
    заметность призыва, наличие иерархии. Ниже порога баннер не «готов».
    """
    im = Image.open(result["banner_file"]).convert("RGB")
    W, H = im.size
    parts = {}

    st = ImageStat.Stat(im)
    parts["contrast"] = min(100, int(max(st.stddev) * 1.6))

    boxes = result.get("text_boxes") or []
    area = sum(max(0, (b[2] - b[0])) * max(0, (b[3] - b[1])) for b in boxes)
    ratio = area / float(W * H)
    # Слишком мало текста — непонятно; слишком много — простыня.
    parts["text_balance"] = int(100 - min(100, abs(ratio - 0.16) * 420))

    small = im.resize((360, int(360 * H / W)), Image.LANCZOS)
    parts["mobile"] = min(100, int(max(ImageStat.Stat(small).stddev) * 2.2))

    sizes = sorted({int(b[3] - b[1]) for b in boxes if b[3] > b[1]})
    parts["hierarchy"] = 100 if len(sizes) >= 3 else (70 if len(sizes) == 2 else 35)

    parts["cta"] = 100 if result["text_used"].get("cta") else 0
    # Баннер без фотографии героя коммерчески неполноценен — сколько бы
    # хорошей типографики на нём ни было. Это не штраф, это факт: в
    # референсах владельца герой есть на каждом.
    parts["hero"] = 100 if result.get("has_photo") else 0
    parts["uniqueness"] = 100 if result.get("family") else 60

    weights = {"contrast": .18, "text_balance": .16, "mobile": .16,
               "hierarchy": .16, "cta": .12, "hero": .16, "uniqueness": .06}
    score = int(sum(parts[k] * weights[k] for k in weights))
    if not result.get("has_photo"):
        score = min(score, COMMERCIAL_SCORE_MIN - 1)   # «готово» без героя не бывает
    return {"score": score, "parts": parts,
            "verdict": "READY" if score >= COMMERCIAL_SCORE_MIN else "BELOW_TARGET"}


COMMERCIAL_SCORE_MIN = int(os.environ.get("BORIS_DESIGN_SCORE_MIN", "70"))


def advanced(spec, out="banner.png"):
    """Баннер по спецификации арт-директора: слои, свет, стекло, геометрия.

    spec понимает: family, canvas, photo, hero (right/center/bottom/full),
    hero_scale, headline (строки из пар «текст, акцент?»), subtitle, icons,
    price, cta, decor, light, vignette, panel.
    """
    fam_name = spec.get("family") or "INDUSTRIAL_DARK"
    fam = dict(FAMILIES_PLUS.get(fam_name, FAMILIES_PLUS["INDUSTRIAL_DARK"]))
    for k in ("decor", "light", "vignette", "panel", "hero"):
        if spec.get(k) is not None:
            fam[k] = spec[k]
    accent = tuple(spec.get("accent") or fam["accent"])
    W, H = FORMATS.get(spec.get("canvas") or "1:1", FORMATS["1:1"])

    # --- слой фона и героя
    im, has_photo = _backdrop(W, H, {"bg": tuple(fam["band"]), "bg2": (6, 6, 8)},
                              spec.get("photo"))
    if has_photo and float(spec.get("hero_scale") or 1.0) != 1.0:
        k = float(spec["hero_scale"])
        big = im.resize((int(W * k), int(H * k)), Image.LANCZOS)
        ox, oy = (big.width - W) // 2, (big.height - H) // 2
        im = big.crop((ox, oy, ox + W, oy + H))
    if spec.get("background_blur"):
        im = im.filter(ImageFilter.GaussianBlur(float(spec["background_blur"])))

    # --- свет и глубина
    if fam.get("light") in ("warm", "cool"):
        im = _rim_light(im, accent, side=("right" if fam["hero"] != "left" else "left"),
                        warm=(fam["light"] == "warm"))
    if fam.get("decor") in ("diagonal", "rings"):
        im = _light_streak(im, accent, "diagonal", 52)
    im = _vignette(im, float(fam.get("vignette") or 0.32))

    # --- затемнение под текст, снизу вверх
    ov = _grad_v((W, int(H * 0.62)), (6, 7, 10), (6, 7, 10), 226, 0)
    base = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    base.paste(ov, (0, H - int(H * 0.62)))
    im = Image.alpha_composite(im.convert("RGBA"), base).convert("RGB")

    d = ImageDraw.Draw(im, "RGBA")
    _decor(d, W, H, fam.get("decor"), accent)

    m = int(W * 0.072)
    boxes = []
    lines = spec.get("headline") or []
    sub = spec.get("subtitle")
    icons = (spec.get("icons") or [])[:3]

    # --- типографика: кегль под ширину, две краски, отслеживание
    probe = ImageDraw.Draw(im)

    def line_w(parts, f):
        sp = probe.textlength(" ", font=f)
        return sum(probe.textlength(t, font=f) for t, _ in parts) + sp * (len(parts) - 1)

    hf = _font("bold", int(W * 0.092))
    while lines and hf.size > 26 and max(line_w(p, hf) for p in lines) > W - 2 * m:
        hf = _font("bold", hf.size - 3)

    block_h = len(lines) * int(hf.size * 1.12) + (int(W * 0.055) if sub else 0)
    icons_h = int(W * 0.20) if icons else 0
    cta_h = int(W * 0.088) if spec.get("cta") else 0
    total = block_h + icons_h + cta_h + int(H * 0.10)
    y = H - m - total

    if fam.get("panel") == "glass":
        im = _glass(im, (m * 0.55, y - int(H * 0.035), W - m * 0.55,
                         H - m + int(H * 0.012)), radius=int(W * 0.035))
        d = ImageDraw.Draw(im, "RGBA")

    for parts in lines:
        _two_tone(d, m, y, parts, hf, (255, 255, 255), accent, boxes)
        y += int(hf.size * 1.12)

    if sub:
        sf = _font("regular", int(W * 0.032))
        d.line([(m, y + int(W * 0.014)), (m + int(W * 0.048), y + int(W * 0.014))],
               fill=accent, width=max(2, int(W * 0.0045)))
        d.text((m + int(W * 0.064), y), sub, font=sf, fill=(228, 232, 240))
        boxes.append(d.textbbox((m + int(W * 0.064), y), sub, font=sf))
        y += int(sf.size * 1.7)

    if icons:
        r = int(W * 0.048)
        step = (W - 2 * m) / float(len(icons))
        iy = y + r + int(H * 0.012)
        cf = _font("regular", int(W * 0.023))
        for n, ic in enumerate(icons):
            cx = int(m + step * (n + 0.5))
            _icon(d, cx, iy, r, ic.get("kind", "check"), accent,
                  (255, 255, 255), max(2, int(W * 0.005)))
            ty = iy + r + int(H * 0.020)
            for cap in (ic.get("caption") or "").split("\n")[:2]:
                tw = d.textlength(cap, font=cf)
                d.text((cx - tw / 2, ty), cap, font=cf, fill=(226, 230, 238))
                boxes.append((cx - tw / 2, ty, cx + tw / 2, ty + cf.size))
                ty += int(cf.size * 1.32)
        y = iy + r + int(H * 0.075)

    if spec.get("price"):
        pf = _font("bold", int(W * 0.050))
        _chip(d, m, y, str(spec["price"]), pf, accent, _readable_on(accent),
              padx=int(W * 0.032), pady=int(W * 0.020),
              radius=int(W * 0.016), boxes=boxes)
        y += int(W * 0.10)

    if spec.get("cta"):
        bf = _font("bold", int(W * 0.031))
        bh = int(W * 0.088)
        by = H - m - bh
        d.rounded_rectangle((m, by, W - m, by + bh), radius=int(bh / 2), fill=accent)
        t = spec["cta"]
        tw = d.textlength(t, font=bf)
        bb = bf.getbbox("Ay")
        d.text(((W - tw) / 2, by + (bh - (bb[3] - bb[1])) / 2 - bb[1]), t,
               font=bf, fill=_readable_on(accent))
        boxes.append((m, by, W - m, by + bh))

    im.save(out, quality=95)
    res = {"banner_file": out, "dimensions": [W, H], "family": fam_name,
           "text_boxes": boxes, "has_photo": has_photo,
           "text_used": {"headline": " ".join(t for ln in lines for t, _ in ln),
                         "cta": spec.get("cta"), "price": spec.get("price"),
                         "subheadline": sub},
           "openai_image_calls": 0, "_overlap": False,
           "where_saved": os.path.abspath(out)}
    res["design"] = design_score(res, spec)
    return res


# ------------------------------------------------ анализ фотографии
# Владелец сформулировал главное: фотография — не заливка прямоугольника.
# Сначала смотрим, что на снимке и где там пусто, и только потом строим
# композицию. Иначе текст ложится на товар, а «свободное место» оказывается
# капотом.

def analyze_photo(path, grid=12):
    """Что на фотографии и куда можно класть текст.

    Считается по сетке яркости и детализации: где мало деталей — там пусто,
    где много — там объект. Это не распознавание предметов, но для решения
    «слева пусто, герой справа» этого достаточно, и работает оно на сервере
    без единой платной модели.
    """
    if Image is None or not path or not os.path.isfile(path):
        return {"ok": False, "why": "фотографии нет"}
    im = Image.open(path).convert("RGB")
    W, H = im.size
    small = im.resize((grid * 8, grid * 8), Image.LANCZOS)
    gray = small.convert("L")

    cells = []
    cw, ch = small.width // grid, small.height // grid
    for gy in range(grid):
        for gx in range(grid):
            box = (gx * cw, gy * ch, (gx + 1) * cw, (gy + 1) * ch)
            st = ImageStat.Stat(gray.crop(box))
            cells.append({"x": gx, "y": gy, "mean": st.mean[0],
                          "detail": st.stddev[0]})
    detail = [c["detail"] for c in cells]
    avg_detail = sum(detail) / float(len(detail))
    busy = [c for c in cells if c["detail"] > avg_detail * 1.25]

    if busy:
        xs = [c["x"] for c in busy]
        ys = [c["y"] for c in busy]
        bbox = (min(xs) / grid, min(ys) / grid,
                (max(xs) + 1) / grid, (max(ys) + 1) / grid)
        cx = sum(xs) / float(len(xs)) / grid
        cy = sum(ys) / float(len(ys)) / grid
    else:
        bbox, cx, cy = (0.2, 0.2, 0.8, 0.8), 0.5, 0.5

    # Свободное место — четверть кадра с наименьшей детализацией.
    zones = {}
    for name, (x0, y0, x1, y1) in (("left", (0, 0, .5, 1)), ("right", (.5, 0, 1, 1)),
                                   ("top", (0, 0, 1, .45)), ("bottom", (0, .55, 1, 1))):
        sel = [c for c in cells if x0 <= c["x"] / grid < x1 and y0 <= c["y"] / grid < y1]
        zones[name] = {
            "detail": round(sum(c["detail"] for c in sel) / max(1, len(sel)), 1),
            "brightness": round(sum(c["mean"] for c in sel) / max(1, len(sel)), 1)}
    free = min(zones, key=lambda k: zones[k]["detail"])

    colors = small.resize((1, 1)).getpixel((0, 0))
    q = small.quantize(colors=5, method=Image.MEDIANCUT).convert("RGB")
    dominant = sorted(q.getcolors(64) or [], reverse=True)[:3]

    st_full = ImageStat.Stat(gray)
    return {
        "ok": True, "size": [W, H],
        "orientation": ("square" if 0.9 < W / float(H) < 1.1
                        else ("landscape" if W > H else "portrait")),
        "subject_bbox": [round(v, 3) for v in bbox],
        "focal_point": [round(cx, 3), round(cy, 3)],
        "subject_share": round((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]), 3),
        "free_zone": free, "zones": zones,
        "brightness": round(st_full.mean[0], 1),
        "contrast": round(st_full.stddev[0], 1),
        "background_complexity": round(avg_detail, 1),
        "avg_color": list(colors),
        "dominant_colors": [list(c[1]) for c in dominant],
        "dark": st_full.mean[0] < 90,
    }


# Композиции, которые движок умеет строить. Выбор — по фотографии, а не по
# счётчику: если герой справа, текст уходит влево, и никак иначе.
COMPOSITIONS = ("TEXT_LEFT_HERO_RIGHT", "HERO_LEFT_TEXT_RIGHT",
                "FULL_BLEED_OVERLAY", "CENTER_HERO_TOP_COPY",
                "CENTER_HERO_BOTTOM_PANEL", "DIAGONAL_SPLIT",
                "ASYMMETRIC_EDITORIAL", "PRODUCT_STAGE",
                "LIFESTYLE_OVERLAY", "PRICE_FIRST")


def plan_composition(photo_info, offer=None):
    """План композиции по фотографии: где текст, где герой, чем их разделять.

    Возвращает решение и причину — чтобы потом было видно, почему движок
    поступил именно так, а не «шаблон номер три».
    """
    if not photo_info or not photo_info.get("ok"):
        return {"composition": "FULL_BLEED_OVERLAY", "text_zone": "bottom",
                "why": "фотографии нет — текст внизу, герой отсутствует",
                "needs_scrim": True, "hero": False}

    fx, fy = photo_info["focal_point"]
    free = photo_info["free_zone"]
    busy_bg = photo_info["background_complexity"] > 24
    share = photo_info["subject_share"]

    if fx > 0.58 and free in ("left",):
        comp, zone, why = ("TEXT_LEFT_HERO_RIGHT", "left",
                           "герой справа, слева свободнее — текст уходит влево")
    elif fx < 0.42 and free in ("right",):
        comp, zone, why = ("HERO_LEFT_TEXT_RIGHT", "right",
                           "герой слева — текст справа")
    elif 0.42 <= fx <= 0.58 and free == "top":
        comp, zone, why = ("CENTER_HERO_TOP_COPY", "top",
                           "герой в центре, сверху воздух — заголовок сверху")
    elif 0.42 <= fx <= 0.58:
        comp, zone, why = ("CENTER_HERO_BOTTOM_PANEL", "bottom",
                           "герой в центре — информация уходит в нижнюю панель")
    elif share > 0.55:
        comp, zone, why = ("PRODUCT_STAGE", "bottom",
                           "герой занимает почти весь кадр — панель снизу")
    else:
        comp, zone, why = ("ASYMMETRIC_EDITORIAL", free,
                           "выраженной стороны нет — асимметричная композиция")

    if offer and str(offer).strip():
        pass                      # оффер сам по себе композицию не меняет

    return {"composition": comp, "text_zone": zone, "why": why,
            "needs_scrim": busy_bg or photo_info["contrast"] > 60,
            "hero": True, "focal_point": [fx, fy],
            "hero_dark": photo_info.get("dark"),
            "separate_hero": photo_info.get("dark") and photo_info["brightness"] < 70,
            "accent_from_photo": photo_info["dominant_colors"][:1]}


def _scrim(im, side, accent=None, power=214):
    """Локальное затемнение только там, где ляжет текст.

    Глухая плашка на весь кадр убивает фотографию; градиент строго со
    стороны текста оставляет героя нетронутым.
    """
    W, H = im.size
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    if side in ("left", "right"):
        steps = 120
        for i in range(steps):
            t = i / float(steps - 1)
            a = int(power * (1 - t) ** 1.5)
            x = int(W * 0.62 * t) if side == "left" else int(W - W * 0.62 * t)
            if side == "left":
                d.rectangle((x, 0, x + W // steps + 2, H), fill=(6, 8, 12, a))
            else:
                d.rectangle((x - W // steps - 2, 0, x, H), fill=(6, 8, 12, a))
    else:
        y0 = 0 if side == "top" else int(H * 0.42)
        y1 = int(H * 0.52) if side == "top" else H
        for y in range(y0, y1):
            t = (y - y0) / float(max(1, y1 - y0))
            a = int(power * ((1 - t) if side == "top" else t) ** 1.3)
            d.line([(0, y), (W, y)], fill=(6, 8, 12, a))
    return Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")


def _smart_crop(im, W, H, focal=(0.5, 0.5)):
    """Кадрирование вокруг фокуса, а не по центру: герой не срезается."""
    k = max(W / im.width, H / im.height)
    im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))),
                   Image.LANCZOS)
    fx, fy = focal
    ox = int(im.width * fx - W / 2)
    oy = int(im.height * fy - H / 2)
    ox = max(0, min(ox, im.width - W))
    oy = max(0, min(oy, im.height - H))
    return im.crop((ox, oy, ox + W, oy + H))


def photo_aware(spec, out="banner.png"):
    """Композиция строится по фотографии, а не фотография вставляется в макет.

    Порядок ровно такой, как у живого дизайнера: посмотреть снимок → решить,
    где герой и где воздух → положить текст в воздух → затемнить только под
    текстом → связать фото и типографику одним акцентом.
    """
    fam_name = spec.get("family") or "INDUSTRIAL_DARK"
    fam = dict(FAMILIES_PLUS.get(fam_name, FAMILIES_PLUS["INDUSTRIAL_DARK"]))
    W, H = FORMATS.get(spec.get("canvas") or "1:1", FORMATS["1:1"])
    photo = spec.get("photo")

    info = analyze_photo(photo) if photo else {"ok": False}
    plan = plan_composition(info, spec.get("price"))
    accent = tuple(spec.get("accent") or fam["accent"])

    if info.get("ok"):
        src = Image.open(photo).convert("RGB")
        im = _smart_crop(src, W, H, plan.get("focal_point") or (0.5, 0.5))
    else:
        im, _ = _backdrop(W, H, {"bg": tuple(fam["band"]), "bg2": (6, 6, 8)}, None)

    zone = plan["text_zone"]
    if plan.get("separate_hero"):
        im = _rim_light(im, accent, side=("right" if zone == "left" else "left"),
                        warm=(fam.get("light") == "warm"))
    im = _vignette(im, float(fam.get("vignette") or 0.30))
    if plan.get("needs_scrim") or not info.get("ok"):
        im = _scrim(im, zone, accent)

    d = ImageDraw.Draw(im, "RGBA")
    # Декор рисуется со стороны героя: украшение поверх заголовка — это не
    # акцент, а помеха чтению.
    _decor_side = "right" if zone == "left" else "left"
    _decor(d, W, H, fam.get("decor"), accent, side=_decor_side)

    m = int(W * 0.070)
    col_w = int(W * (0.52 if zone in ("left", "right") else 0.86))
    x0 = m if zone in ("left", "top", "bottom") else W - m - col_w
    boxes = []

    lines = spec.get("headline") or []
    sub = spec.get("subtitle")
    icons = (spec.get("icons") or [])[:3]

    probe = ImageDraw.Draw(im)

    def line_w(parts, f):
        sp = probe.textlength(" ", font=f)
        return sum(probe.textlength(t, font=f) for t, _ in parts) + sp * (len(parts) - 1)

    hf = _font("bold", int(W * (0.074 if zone in ("left", "right") else 0.092)))
    while lines and hf.size > 24 and max(line_w(p, hf) for p in lines) > col_w:
        hf = _font("bold", hf.size - 3)

    block = len(lines) * int(hf.size * 1.12)
    if sub:
        block += int(W * 0.055)
    if icons:
        block += int(W * (0.30 if zone in ("left", "right") else 0.19))
    if spec.get("price"):
        block += int(W * 0.10)
    cta_h = int(W * 0.082) if spec.get("cta") else 0

    if zone == "top":
        y = m
    elif zone == "bottom":
        y = H - m - block - cta_h - int(H * 0.03)
    else:
        y = int((H - block - cta_h) / 2)
    y = max(m, y)

    for parts in lines:
        _two_tone(d, x0, y, parts, hf, (255, 255, 255), accent, boxes)
        y += int(hf.size * 1.12)

    if sub:
        sf = _font("regular", int(W * 0.030))
        d.line([(x0, y + int(W * 0.013)), (x0 + int(W * 0.042), y + int(W * 0.013))],
               fill=accent, width=max(2, int(W * 0.004)))
        for ln in _wrap(d, sub, sf, col_w - int(W * 0.060))[:2]:
            d.text((x0 + int(W * 0.058), y), ln, font=sf, fill=(228, 232, 240))
            boxes.append(d.textbbox((x0 + int(W * 0.058), y), ln, font=sf))
            y += int(sf.size * 1.35)
        y += int(H * 0.018)

    if icons:
        cf = _font("regular", int(W * 0.022))
        if zone in ("left", "right"):
            # Узкая колонка: выгоды строкой в столбик, знак слева от подписи.
            r = int(W * 0.030)
            for ic in icons:
                _icon(d, x0 + r, y + r, r, ic.get("kind", "check"), accent,
                      (255, 255, 255), max(2, int(W * 0.0045)))
                ty = y + int(r * 0.25)
                for cap in (ic.get("caption") or "").replace("\n", " ").split("|")[:1]:
                    d.text((x0 + r * 2 + int(W * 0.018), ty), cap, font=cf,
                           fill=(226, 230, 238))
                    boxes.append(d.textbbox((x0 + r * 2 + int(W * 0.018), ty),
                                            cap, font=cf))
                y += int(r * 2.6)
        else:
            r = int(W * 0.046)
            step = col_w / float(len(icons))
            iy = y + r
            for n, ic in enumerate(icons):
                cx = int(x0 + step * (n + 0.5))
                _icon(d, cx, iy, r, ic.get("kind", "check"), accent,
                      (255, 255, 255), max(2, int(W * 0.005)))
                ty = iy + r + int(H * 0.018)
                for cap in (ic.get("caption") or "").split("\n")[:2]:
                    tw = d.textlength(cap, font=cf)
                    d.text((cx - tw / 2, ty), cap, font=cf, fill=(226, 230, 238))
                    boxes.append((cx - tw / 2, ty, cx + tw / 2, ty + cf.size))
                    ty += int(cf.size * 1.3)
            y = iy + r + int(H * 0.070)

    if spec.get("price"):
        pf = _font("bold", int(W * 0.048))
        _chip(d, x0, y, str(spec["price"]), pf, accent, _readable_on(accent),
              padx=int(W * 0.030), pady=int(W * 0.018),
              radius=int(W * 0.015), boxes=boxes)
        y += int(W * 0.095)

    if spec.get("cta"):
        bf = _font("bold", int(W * 0.029))
        bh = int(W * 0.082)
        bw = col_w if zone in ("left", "right") else (W - 2 * m)
        # Кнопка не имеет права наезжать на товар: обрезаем её по границе
        # области героя, а не по ширине колонки.
        bb = (info.get("subject_bbox") if info.get("ok") else None)
        if bb and zone == "left":
            bw = min(bw, max(int(W * 0.30), int(W * bb[0]) - x0 - int(W * 0.02)))
        elif bb and zone == "right":
            shift = max(x0, int(W * bb[2]) + int(W * 0.02))
            bw = min(bw, W - m - shift)
            x0 = shift
        by = min(y + int(H * 0.01), H - m - bh)
        d.rounded_rectangle((x0, by, x0 + bw, by + bh), radius=int(bh / 2),
                            fill=accent)
        t = spec["cta"]
        # Кнопку урезали по границе героя — значит подпись обязана ужаться
        # под неё. Текст, вылезший из кнопки, — брак, который видно первым.
        pad = int(W * 0.030)
        while bf.size > 14 and d.textlength(t, font=bf) > bw - pad * 2:
            bf = _font("bold", bf.size - 1)
        tw = d.textlength(t, font=bf)
        bb = bf.getbbox("Ay")
        d.text((x0 + (bw - tw) / 2, by + (bh - (bb[3] - bb[1])) / 2 - bb[1]),
               t, font=bf, fill=_readable_on(accent))
        boxes.append((x0, by, x0 + bw, by + bh))

    im.save(out, quality=95)
    res = {"banner_file": out, "dimensions": [W, H], "family": fam_name,
           "composition": plan["composition"], "why": plan["why"],
           "text_zone": zone, "photo": photo, "photo_info": info,
           "text_boxes": boxes, "has_photo": bool(info.get("ok")),
           "text_used": {"headline": " ".join(t for ln in lines for t, _ in ln),
                         "cta": spec.get("cta"), "price": spec.get("price"),
                         "subheadline": sub},
           "openai_image_calls": 0, "_overlap": False,
           "where_saved": os.path.abspath(out)}
    res["design"] = design_score(res, spec)
    return res


# =========================================================== ДВОЕ ВОРОТ
# Владелец: «renderer success ≠ banner success». Файл создан, текст влез,
# картинка открывается — это ещё ничего не значит. Технические ворота
# проверяют, что баннер собран правильно; коммерческие — что его не стыдно
# показать клиенту. READY = оба PASS и ни одного жёсткого отказа.

HARD_FAILS = (
    "MISSING_HERO", "PLACEHOLDER_HERO", "IRRELEVANT_IMAGE", "WATERMARK",
    "BROKEN_CROP", "SUBJECT_TOO_SMALL", "TEXT_OVERLAP", "CTA_OVERLAP",
    "TEXT_OUTSIDE_SAFE_ZONE", "UNREADABLE_CONTRAST", "EXCESSIVE_DEAD_SPACE",
    "BROKEN_HIERARCHY", "DUPLICATE", "MISSING_PRICE", "UNFINISHED_COMPOSITION",
    "NO_SEMANTIC_PROOF",
)

# Разрешено только в разработке. В production подстановка «чего-нибудь»
# вместо фотографии товара — это визуальный брак у клиента.
ALLOW_PLACEHOLDER = os.environ.get("BORIS_BANNER_ALLOW_PLACEHOLDER") == "1"


def is_placeholder(path):
    """Настоящая ли это фотография.

    Отличить снимок от нарисованной заглушки можно без нейросети: у фото
    есть шум, полутона и тысячи оттенков, у векторной болванки — десяток
    плоских заливок и стерильные края. Именно на этом попался мой жёлтый
    прямоугольник, который выглядел как «эвакуатор».
    """
    if Image is None or not path or not os.path.isfile(path):
        return True, "файла нет"
    im = Image.open(path).convert("RGB")
    small = im.resize((256, 256), Image.LANCZOS)
    colors = small.getcolors(maxcolors=256 * 256) or []
    if not colors:
        return False, ""
    total = float(sum(c for c, _ in colors))
    top = sorted(colors, reverse=True)[:6]
    flat_share = sum(c for c, _ in top) / total
    distinct = len(colors)

    # Шум: у фотографии разница с размытой копией заметна повсюду.
    blur = small.filter(ImageFilter.GaussianBlur(1.2))
    noise = ImageStat.Stat(ImageChops.difference(small, blur)).mean[0]

    if distinct < 900:
        return True, "слишком мало оттенков (%d) — это рисунок, а не снимок" % distinct
    if flat_share > 0.62:
        return True, "%.0f%% кадра — плоские заливки" % (flat_share * 100)
    if noise < 0.9:
        return True, "нет зернистости — похоже на векторную заглушку"
    return False, ""


def _has_watermark(path):
    """Грубый признак водяного знака: повторяющийся полупрозрачный узор в
    центре. Ловит не всё, но пропускать заведомо чужое нельзя."""
    if Image is None or not os.path.isfile(path):
        return False
    im = Image.open(path).convert("L")
    W, H = im.size
    mid = im.crop((int(W * .3), int(H * .4), int(W * .7), int(H * .6)))
    edges = mid.filter(ImageFilter.FIND_EDGES)
    st = ImageStat.Stat(edges)
    return st.mean[0] > 34 and st.stddev[0] > 46


def technical_qa(result, spec):
    """Ворота сборки: файл, размеры, поля, наложения, читаемость."""
    base = qa(result, spec)
    problems = list(base["problems"])
    codes = []
    for pr in problems:
        if "безопасн" in pr:
            codes.append("TEXT_OUTSIDE_SAFE_ZONE")
        elif "накладыва" in pr or "наехал" in pr:
            codes.append("TEXT_OVERLAP")
        elif "мобильн" in pr:
            codes.append("UNREADABLE_CONTRAST")
    return {"pass": not problems, "problems": problems, "codes": codes}


def commercial_qa(result, spec):
    """Ворота коммерческой пригодности. Здесь решается, покажем ли клиенту."""
    fails, notes = [], []
    photo = result.get("photo") or spec.get("photo")
    info = result.get("photo_info") or {}

    if not result.get("has_photo"):
        fails.append("MISSING_HERO")
    else:
        ph, why = is_placeholder(photo)
        if ph and not ALLOW_PLACEHOLDER:
            fails.append("PLACEHOLDER_HERO")
            notes.append(why)
        if _has_watermark(photo):
            fails.append("WATERMARK")
        share = info.get("subject_share") or 0
        if share and share < 0.18:
            fails.append("SUBJECT_TOO_SMALL")
        bb = info.get("subject_bbox")
        if bb and (bb[0] <= 0.001 and bb[2] >= 0.999):
            notes.append("объект занимает кадр целиком — проверить кадрирование")

    # Семантика: BORIS обязан знать, что на снимке именно то, что продаём.
    # Своего распознавания у движка нет, поэтому доказательством считается
    # подтверждение от источника изображения. Нет подтверждения — нет READY.
    expected = (spec.get("expected_subject") or "").strip()
    proof = (spec.get("subject_proof") or {})
    if expected:
        conf = float(proof.get("confidence") or 0)
        matched = (proof.get("subject") or "").strip().lower()
        if not matched or conf < float(os.environ.get("BORIS_SEMANTIC_MIN", "0.7")):
            fails.append("NO_SEMANTIC_PROOF")
        elif expected.lower() not in matched and matched not in expected.lower():
            fails.append("IRRELEVANT_IMAGE")

    # Мёртвое пространство: часть кадра, где нет ни героя, ни текста.
    boxes = result.get("text_boxes") or []
    W, H = result["dimensions"]
    text_area = sum(max(0, b[2] - b[0]) * max(0, b[3] - b[1]) for b in boxes)
    hero_area = ((info.get("subject_bbox") or [0, 0, 0, 0]) and
                 (info["subject_bbox"][2] - info["subject_bbox"][0]) *
                 (info["subject_bbox"][3] - info["subject_bbox"][1])
                 if info.get("ok") else 0)
    used = text_area / float(W * H) + (hero_area or 0)
    if used < 0.30:
        fails.append("EXCESSIVE_DEAD_SPACE")

    sizes = sorted({int(b[3] - b[1]) for b in boxes if b[3] > b[1]})
    if len(sizes) < 2:
        fails.append("BROKEN_HIERARCHY")

    if spec.get("price_required") and not result["text_used"].get("price"):
        fails.append("MISSING_PRICE")

    if result.get("duplicate"):
        fails.append("DUPLICATE")

    return {"pass": not fails, "hard_fails": fails, "notes": notes}


def verdict(result, spec):
    """Итог. Жёсткий отказ перевешивает любую оценку — красивая типографика
    не делает баннер без товара пригодным."""
    tech = technical_qa(result, spec)
    comm = commercial_qa(result, spec)
    hard = list(comm["hard_fails"]) + [c for c in tech["codes"] if c in HARD_FAILS]
    score = (result.get("design") or {}).get("score", 0)
    ok = tech["pass"] and comm["pass"] and not hard
    return {"verdict": "READY" if ok else "REJECT",
            "hard_fails": hard, "technical": tech, "commercial": comm,
            "score": score if ok else None,
            "reason": (hard[0] if hard else (tech["problems"][0] if tech["problems"]
                                             else ""))}


REPAIR_PLAN = {
    "MISSING_HERO": "запросить другое изображение у источника",
    "PLACEHOLDER_HERO": "запросить настоящую фотографию, заглушку не публиковать",
    "IRRELEVANT_IMAGE": "взять следующего кандидата из выдачи источника",
    "WATERMARK": "отбраковать кандидата и взять следующего",
    "SUBJECT_TOO_SMALL": "увеличить героя и пересчитать кадр",
    "BROKEN_CROP": "сменить фокусную точку",
    "TEXT_OVERLAP": "перестроить типографику: меньше кегль, больше воздуха",
    "CTA_OVERLAP": "сдвинуть призыв за границу героя",
    "TEXT_OUTSIDE_SAFE_ZONE": "уменьшить блок и вернуть в поля",
    "UNREADABLE_CONTRAST": "усилить локальное затемнение под текстом",
    "EXCESSIVE_DEAD_SPACE": "сменить композицию и масштаб героя",
    "BROKEN_HIERARCHY": "развести кегли: заголовок, подзаголовок, подписи",
    "DUPLICATE": "сменить семейство макета",
    "MISSING_PRICE": "добавить цену из брифа или снять требование",
    "NO_SEMANTIC_PROOF": "получить подтверждение предмета от источника изображения",
}


def repair(spec, reason, attempt):
    """Что менять на следующем круге. Не наугад, а по причине отказа."""
    nxt = dict(spec)
    if reason in ("MISSING_HERO", "PLACEHOLDER_HERO", "IRRELEVANT_IMAGE",
                  "WATERMARK", "NO_SEMANTIC_PROOF"):
        nxt["_needs_new_asset"] = True
    elif reason == "SUBJECT_TOO_SMALL":
        nxt["hero_scale"] = float(spec.get("hero_scale") or 1.0) + 0.18
    elif reason == "BROKEN_CROP":
        nxt["focal_shift"] = attempt * 0.12
    elif reason in ("TEXT_OVERLAP", "TEXT_OUTSIDE_SAFE_ZONE", "BROKEN_HIERARCHY"):
        nxt["density"] = "airy"
    elif reason == "UNREADABLE_CONTRAST":
        nxt["scrim_power"] = min(245, int(spec.get("scrim_power") or 214) + 20)
    elif reason in ("EXCESSIVE_DEAD_SPACE", "DUPLICATE"):
        fams = list(FAMILIES_PLUS)
        cur = spec.get("family") or fams[0]
        nxt["family"] = fams[(fams.index(cur) + 1 + attempt) % len(fams)] \
            if cur in fams else fams[0]
    return nxt


def produce(spec, out="banner.png", max_attempts=4):
    """Один баннер до годного или до честного отказа.

    Здесь и живёт принцип «лучше не выпустить, чем выпустить брак»: если
    после разумного числа попыток ворота не пройдены, результат называется
    FAILED_VISUAL_QA и клиенту не уходит.
    """
    attempt, history = 0, []
    cur = dict(spec)
    while True:
        res = photo_aware(cur, out=out)
        v = verdict(res, cur)
        history.append({"attempt": attempt, "verdict": v["verdict"],
                        "reason": v["reason"],
                        "plan": REPAIR_PLAN.get(v["reason"])})
        if v["verdict"] == "READY":
            res["qa"] = v
            res["attempts"] = history
            return res
        attempt += 1
        if attempt >= max_attempts or v["reason"] in (
                "MISSING_HERO", "PLACEHOLDER_HERO", "NO_SEMANTIC_PROOF") \
                and not cur.get("asset_pool"):
            res["qa"] = v
            res["attempts"] = history
            res["status"] = "NO_ASSET" if v["reason"] in (
                "MISSING_HERO", "PLACEHOLDER_HERO") else "FAILED_VISUAL_QA"
            return res
        cur = repair(cur, v["reason"], attempt)
        pool = cur.get("asset_pool") or []
        if cur.pop("_needs_new_asset", False) and pool:
            cur["photo"] = pool.pop(0)
            cur["asset_pool"] = pool
