import shutil, time, py_compile, sys, hashlib
P = "app/api/banners.py"
SHA = "c710e2691bc1f5b6455b011845538f7ccc648e8a6007dd8cf3725983e963d3e2"
src = open(P, encoding="utf-8").read()
cur = hashlib.sha256(src.encode()).hexdigest()
if cur != SHA:
    print("СТОП: файл изменился.\n ожидался:", SHA, "\n сейчас:  ", cur); sys.exit(1)
if "use_template_library" in src:
    print("патч уже применён"); sys.exit(0)

A = ('    vary_all: bool = False       # варьировать полностью всё (композиция, текст, цвет, сцена)\n',
'''    vary_all: bool = False       # варьировать полностью всё (композиция, текст, цвет, сцена)
    template_id: str = ""               # id шаблона из banner_library аккаунта
    use_template_library: bool = False  # opt-in: текст и стиль из библиотеки, фон рисует OpenAI
''')

HELPERS = '''
def _read_storage_json(account_id: str, key: str):
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == key).first()
        return _json.loads(row.value) if row else None
    except Exception:
        return None
    finally:
        db.close()


def _load_template(account_id: str, template_id: str) -> dict:
    """Шаблон строго из библиотеки ЭТОГО аккаунта. Не найден - 404, повреждён - 422."""
    from fastapi import HTTPException
    lib = _read_storage_json(account_id, "banner_library")
    if not isinstance(lib, list) or not lib:
        raise HTTPException(status_code=404, detail="Библиотека шаблонов не найдена для этого аккаунта")
    found = [t for t in lib if isinstance(t, dict) and t.get("id") == template_id]
    if len(found) != 1:
        raise HTTPException(status_code=404, detail="Шаблон %s не найден" % template_id)
    t = found[0]
    if not isinstance(t.get("title"), str) or not t["title"].strip():
        raise HTTPException(status_code=422, detail="Шаблон повреждён: пустой заголовок")
    if not isinstance(t.get("subtitle"), str):
        raise HTTPException(status_code=422, detail="Шаблон повреждён: подзаголовок не строка")
    if not isinstance(t.get("has_price"), bool):
        raise HTTPException(status_code=422, detail="Шаблон повреждён: has_price не булево")
    price = t.get("price") or ""
    if not isinstance(price, str):
        raise HTTPException(status_code=422, detail="Шаблон повреждён: цена не строка")
    if not t["has_price"] and price:
        raise HTTPException(status_code=422, detail="has_price=false, но цена заполнена")
    return t


def _template_background_query(tpl: dict, style: dict) -> str:
    """Запрос на ФОН для OpenAI: сцена без единой надписи, текст накладываем сами."""
    photo = (tpl.get("photo") or "").strip() or (tpl.get("subtype") or "объект")
    niche = (style or {}).get("niche", "")
    scene = "строительная бытовка, блок-контейнер" if niche == "bytovki" else "мебель на заказ в интерьере"
    return (
        "Photorealistic advertising background photo, square. Scene: " + photo + " (" + scene + "). "
        "Professional commercial photography, natural lighting, clean uncluttered composition, "
        "main object slightly left of center, calm neutral background, plenty of empty space "
        "in the lower third for text overlay. "
        "ABSOLUTELY NO TEXT: no letters, no words, no numbers, no captions, no signs, no logos, "
        "no watermarks, no badges, no stars, no stickers anywhere in the image. "
        "No collage, no frames, no clipart."
    )


'''
B = ('@router.post("/full_ai")\n', HELPERS.lstrip("\n") + '@router.post("/full_ai")\n')

C = ('''    size_map = {
        "infographic": ("1024x1024", "infographic"),''',
'''    if req.use_template_library:
        from fastapi import HTTPException as _HTTPExc
        import time as _t2, json as _j2, hashlib as _h2
        from banner_generator import generate_infographic_banner as _gen_info
        if not req.template_id:
            raise _HTTPExc(status_code=422, detail="use_template_library=true, но template_id не передан")
        _tpl = _load_template(req.account_id, req.template_id)
        _style = _read_storage_json(req.account_id, "brand_style") or {}
        _colors = _style.get("colors", {}) or {}
        _scheme = (_style.get("schemes", {}) or {}).get(_tpl.get("scheme", ""), {}) or {}
        _primary = _scheme.get("primary") or _colors.get("brand") or "#1B4FD8"
        _accent = _colors.get("accent") or "#FF4D2E"
        _price = (_tpl.get("price") or "") if _tpl.get("has_price") else ""
        _q = _template_background_query(_tpl, _style)
        _folder = _account_banner_dir(req.account_id, "infographic")
        _fname = "tpl_" + str(req.template_id) + "_" + str(int(_t2.time())) + ".png"
        _fpath = _folder + "/" + _fname
        _gen_info(
            title=_tpl["title"], subtitle=_tpl.get("subtitle", ""), price_text=_price,
            icon_name="", bg_color_top=_primary, bg_color_bottom=_primary,
            accent_color=_accent, output_path=_fpath, photo_query=_q,
            photo_source="ai", ai_quality=req.quality, ai_model="gpt-image-2",
            own_photo_path=None, account_id=req.account_id, operation="banner_from_template",
        )
        _meta = {"template_id": _tpl.get("id"), "template_type": _tpl.get("type"),
                 "template_subtype": _tpl.get("subtype"), "template_block": _tpl.get("block"),
                 "brand_style_niche": _style.get("niche"), "use_template_library": True,
                 "provider": "openai", "background_prompt_sha256": _h2.sha256(_q.encode()).hexdigest()[:16]}
        _save_banner_record(req.account_id, _fname, "infographic", _tpl["title"],
                            _j2.dumps(_meta, ensure_ascii=False), "template", "infographic")
        print("[banner] шаблон %s: текст дословно, фон от OpenAI, GigaChat не вызывался" % req.template_id)
        return {"status": "ok", "url": BASE_URL + "/" + req.account_id + "/banners/infographic/" + _fname,
                "urls": [BASE_URL + "/" + req.account_id + "/banners/infographic/" + _fname],
                "template_id": _tpl.get("id")}

    size_map = {
        "infographic": ("1024x1024", "infographic"),''')

subs = [A, B, C]
for old, _ in subs:
    if src.count(old) != 1:
        print("СТОП: якорь x%s:" % src.count(old), repr(old[:60])); sys.exit(1)
for old, new in subs:
    src = src.replace(old, new, 1)
bak = P + ".bak_template_library_" + str(int(time.time()))
shutil.copy2(P, bak)
open(P, "w", encoding="utf-8").write(src)
py_compile.compile(P, doraise=True)
print("патч применён, бэкап:", bak)
print("новый SHA:", hashlib.sha256(open(P, encoding="utf-8").read().encode()).hexdigest())
