from __future__ import annotations

from pathlib import Path
import hashlib
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


def _headline_lines(draw: ImageDraw.ImageDraw, text: str, font, max_width: int):
    """Prefer one line; otherwise choose the cleanest 2-line split.

    Brand pair "Grand Line" must stay together. The old generic wrapper could
    emit "Grand" / "Line" on separate lines, which failed mobile QA.
    """
    words=[w for w in str(text or "").split() if w]
    if not words:
        return []
    full=" ".join(words)
    if draw.textbbox((0,0),full,font=font)[2] <= max_width:
        return [full]
    candidates=[]
    for i in range(1,len(words)):
        # Never split the Grand Line brand pair.
        if words[i-1].casefold()=="grand" and words[i].casefold()=="line":
            continue
        a=" ".join(words[:i]); b=" ".join(words[i:])
        wa=draw.textbbox((0,0),a,font=font)[2]
        wb=draw.textbbox((0,0),b,font=font)[2]
        if wa<=max_width and wb<=max_width:
            # Prefer balanced, visually stable lines with the second not tiny.
            score=max(wa,wb)+abs(wa-wb)*0.22
            candidates.append((score,[a,b]))
    return min(candidates,key=lambda x:x[0])[1] if candidates else []


def compose_premium_avito_banner(source_path: str, output_path: str, title: str) -> dict:
    """Mobile-first Avito banner: dominant photo + one quiet glass caption.

    V5 removes the full-width dark bar that Vision QA correctly flagged as a
    cheap fragmented template. The photograph remains dominant; a single light
    translucent card carries only the verified title.
    """
    src=Image.open(source_path).convert("RGB")
    canvas=_crop_square(src,1024)
    rgba=canvas.convert("RGBA")

    # Very light global grade only; keep source truthful.
    grade=Image.new("RGBA",rgba.size,(0,0,0,0))
    ImageDraw.Draw(grade).rectangle((0,0,1024,1024),fill=(8,15,28,10))
    rgba=Image.alpha_composite(rgba,grade)

    source_title=" ".join(str(title or "").split())
    words=source_title.split()
    if not words:
        return {"ok":False,"error":"EMPTY_TITLE","font_size":0,"line_count":0}

    # V6 identity contract: the verified Campaign title is atomic. Never drop
    # trailing words to make a banner fit; shortening a title changes the offer
    # and correctly fails dedicated banner QA. Fit the COMPLETE title in one or
    # two lines, reducing font size only within the mobile-safe range.
    display_title=source_title
    lines=[]
    font_size=0
    font=_font(64)
    probe=ImageDraw.Draw(rgba)
    for size in range(68,47,-2):
        f=_font(size)
        ls=_headline_lines(probe,display_title,f,850)
        if ls and len(ls)<=2:
            lines=ls
            font_size=size
            font=f
            break
    if not lines:
        return {
            "ok":False,
            "error":"EXACT_TITLE_GEOMETRY_FAILED",
            "font_size":0,
            "line_count":0,
            "source_title":source_title,
        }

    boxes=[probe.textbbox((0,0),ln,font=font) for ln in lines]
    heights=[max(1,b[3]-b[1]) for b in boxes]
    gap=10
    text_h=sum(heights)+gap*max(0,len(lines)-1)

    # V6 adaptive caption placement. The old fixed bottom panel could cover the
    # sold object (Campaign 512 proved this on panoramic glazing). Compare the
    # exact candidate zones and put the single frosted card over the calmer one.
    top_panel=(38,42,986,324)
    bottom_panel=(38,700,986,982)
    top_density=_edge_density(canvas,top_panel)
    bottom_density=_edge_density(canvas,bottom_panel)
    if top_density < bottom_density:
        caption_zone="top"
        panel=top_panel
        panel_top=42
        shadow_box=(44,52,992,332)
    else:
        caption_zone="bottom"
        panel=bottom_panel
        panel_top=700
        shadow_box=(44,710,992,990)

    # V7 creative-family contract. Layout choice is deterministic from the
    # canonical output identity + verified title, so retries never randomly
    # change an already approved creative. Families vary only presentation;
    # source photo and exact verified title remain authoritative.
    family_seed=(Path(output_path).name+"|"+source_title).encode("utf-8")
    family_idx=int.from_bytes(hashlib.blake2b(family_seed,digest_size=2).digest(),"big") % 4
    family_names=(
        "glass_light",
        "glass_dark",
        "warm_accent",
        "charcoal_center",
    )
    creative_family=family_names[family_idx]

    shadow=Image.new("RGBA",rgba.size,(0,0,0,0))
    sd=ImageDraw.Draw(shadow)
    sd.rounded_rectangle(shadow_box,radius=34,fill=(0,0,0,72 if family_idx in (0,2) else 96))
    shadow=shadow.filter(ImageFilter.GaussianBlur(16))
    rgba=Image.alpha_composite(rgba,shadow)

    glass=Image.new("RGBA",rgba.size,(0,0,0,0))
    gd=ImageDraw.Draw(glass)
    if family_idx == 0:
        panel_fill=(250,252,255,232); panel_outline=(255,255,255,245)
        text_fill=(18,30,52,255); align="left"
    elif family_idx == 1:
        panel_fill=(18,28,45,222); panel_outline=(255,255,255,95)
        text_fill=(250,252,255,255); align="left"
    elif family_idx == 2:
        panel_fill=(252,248,239,235); panel_outline=(255,255,255,245)
        text_fill=(34,34,32,255); align="left"
    else:
        panel_fill=(22,25,31,218); panel_outline=(255,255,255,125)
        text_fill=(255,255,255,255); align="center"
    gd.rounded_rectangle(panel,radius=30,fill=panel_fill,outline=panel_outline,width=2)
    if family_idx == 2:
        # A restrained local accent gives a distinct premium family without
        # adding claims, prices, logos, badges or any unverified copy.
        gd.rounded_rectangle((58,panel_top+38,70,panel_top+244),radius=6,fill=(164,117,62,230))
    rgba=Image.alpha_composite(rgba,glass)
    draw=ImageDraw.Draw(rgba)

    y=panel_top+(282-text_h)//2
    for idx,ln in enumerate(lines):
        if align == "center":
            bb=draw.textbbox((0,0),ln,font=font)
            line_w=max(1,bb[2]-bb[0])
            x=(1024-line_w)//2
        else:
            x=92 if family_idx == 2 else 82
        draw.text((x,y),ln,font=font,fill=text_fill)
        y += heights[idx]+gap

    if font_size<48 or len(lines)>2 or not lines:
        return {"ok":False,"error":"MOBILE_TEXT_GEOMETRY_FAILED","font_size":font_size,"line_count":len(lines)}

    Path(output_path).parent.mkdir(parents=True,exist_ok=True)
    rgba.convert("RGB").save(output_path,"PNG",optimize=True)
    return {
        "ok":True,
        "layout":f"premium_photo_{creative_family}_adaptive_v7",
        "creative_family":creative_family,
        "caption_style":creative_family+"_adaptive",
        "source_path":source_path,
        "output_path":output_path,
        "font_size":font_size,
        "line_count":len(lines),
        "display_title":display_title,
        "source_title":source_title,
        "visible_word_count":len(display_title.split()),
        "text_policy":"exact_verified_title_two_lines_min48_adaptive_zone",
        "text_renderer":"local_pillow",
        "caption_zone":caption_zone,
        "caption_zone_top":panel_top,
        "top_edge_density":round(top_density,4),
        "bottom_edge_density":round(bottom_density,4),
    }
