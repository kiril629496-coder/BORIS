import os, shutil, sys, time, py_compile
sys.path.insert(0, os.getcwd())
P = "app/api/messenger.py"
src = open(P, encoding="utf-8").read()
if "_STORE_MTYPE_FIX" in src:
    print("УЖЕ ПРИМЕНЁН"); sys.exit(0)
A1 = ('            text = (m.get("content") or {}).get("text", "")\n'
      "            _im = item_meta or {}\n")
A2 = ('                "content_type": _mtype,\n'
      '                "media_ref": _cref,\n'
      '                "content_type": _mtype,\n'
      '                "media_ref": _cref,\n')
for f, n in ((A1, "text+item_meta"), (A2, "дубль ключей")):
    c = src.count(f)
    if c != 1:
        print("СТОП: якорь «%s» найден %d раз" % (n, c)); sys.exit(1)
NEW1 = (A1 +
  "            # _STORE_MTYPE_FIX\n"
  '            _content = m.get("content") or {}\n'
  '            _mtype = m.get("type") or ("system" if (text or "").startswith(\n'
  '                "[Системное сообщение]") else "text")\n'
  "            _cref = None\n"
  '            if _mtype == "voice":\n'
  '                _cref = (_content.get("voice") or {}).get("voice_id")\n'
  '            elif _mtype == "image":\n'
  '                _img = _content.get("image") or {}\n'
  '                _cref = _img.get("image_id") or (\n'
  '                    next(iter((_img.get("sizes") or {}).values()), None))\n'
  '            elif _mtype in ("video", "file"):\n'
  '                _blk = _content.get(_mtype) or {}\n'
  '                _cref = _blk.get("id") or _blk.get(_mtype + "_id")\n')
NEW2 = ('                "content_type": _mtype,\n'
        '                "media_ref": _cref,\n')
src = src.replace(A1, NEW1, 1).replace(A2, NEW2, 1)
try:
    compile(src, P, "exec")
except SyntaxError as e:
    print("СТОП: синтаксис ДО записи —", e); sys.exit(1)
bak = "%s.bak_mtype_%d" % (P, int(time.time()))
shutil.copy2(P, bak)
open(P, "w", encoding="utf-8").write(src)
open("/tmp/mtype_bak.txt", "w").write(bak)
try:
    py_compile.compile(P, doraise=True)
    import importlib
    importlib.import_module("app.main")
    print("ФИКС ПРИМЕНЁН, py_compile OK, import app.main OK")
    print("бэкап:", bak)
except Exception as e:
    shutil.copy2(bak, P)
    print("ОТКАТ:", str(e)[:200]); sys.exit(1)
