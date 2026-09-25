"""Миниатюры и превью. Только PIL, никаких платных вызовов.

Грид работает на thumb (~320px), просмотр — на preview (~1280px), оригинал
запрашивается лишь когда действительно нужен. Это то, что делает медиатеку
быстрой на 13 000 файлов.

Пайплайн НЕ блокирует появление медиатеки: asset регистрируется сразу,
миниатюра дорисовывается фоновой задачей, до этого UI показывает skeleton.
"""
from app.services import media_storage as st

THUMB_PX = 320
PREVIEW_PX = 1280
JPEG_Q = 82


def build(key: str):
    """Считает thumb+preview для существующего файла.

    Возвращает (thumb_key, preview_key, width, height, phash) — любое поле
    может быть None, если формат не читается. Оригинал не трогается.
    """
    from PIL import Image, ImageOps

    src = st.abs_path(key)
    thumb_key = st.derived_key(key, st.THUMB_ROOT)
    prev_key = st.derived_key(key, st.PREV_ROOT)
    width = height = None
    phash = None

    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        width, height = im.size
        try:
            import imagehash
            phash = str(imagehash.phash(im))
        except Exception:
            phash = None
        rgb = im.convert("RGB")

        prev = rgb.copy()
        prev.thumbnail((PREVIEW_PX, PREVIEW_PX), Image.LANCZOS)
        st.ensure_parent(prev_key)
        prev.save(st.abs_path(prev_key), "JPEG", quality=JPEG_Q, optimize=True)

        thumb = rgb.copy()
        thumb.thumbnail((THUMB_PX, THUMB_PX), Image.LANCZOS)
        st.ensure_parent(thumb_key)
        thumb.save(st.abs_path(thumb_key), "JPEG", quality=JPEG_Q, optimize=True)

    return thumb_key, prev_key, width, height, phash
