# -*- coding: utf-8 -*-
"""МОП не видел вложений: в истории для модели стояло `if not text: continue`,
и фото, голосовые, документы, ссылки и звонки просто выбрасывались. Клиент
присылал фотографию объекта — модель отвечала так, будто ничего не было.

Заодно убраны два мёртвых дублирующих блока: _mtype и _cref вычислялись дважды
подряд и нигде дальше не использовались (настоящий разбор — в
_store_messages_locally)."""
import sys, os, ast, time, hashlib

APPLY = "--apply" in sys.argv
BASE = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "/root/BORIS/backend"
path = os.path.join(BASE, "app/api/messenger.py")

OLD = '''            direction = m.get("direction", "")
            text = (m.get("content") or {}).get("text", "")
            _content = m.get("content") or {}
            _mtype = m.get("type") or ("system" if (text or "").startswith("[Системное сообщение]") else "text")
            _cref = None
            if _mtype == "voice":
                _cref = (_content.get("voice") or {}).get("voice_id")
            elif _mtype == "image":
                _img = _content.get("image") or {}
                _cref = _img.get("image_id") or (next(iter((_img.get("sizes") or {}).values()), None))
            elif _mtype in ("video", "file"):
                _blk = _content.get(_mtype) or {}
                _cref = _blk.get("id") or _blk.get(_mtype + "_id")
            _content = m.get("content") or {}
            _mtype = m.get("type") or "text"
            _cref = None
            if _mtype == "voice":
                _cref = ((_content.get("voice") or {}).get("voice_id"))
            elif _mtype == "image":
                _img = _content.get("image") or {}
                # image_id или первый URL — сохраняем что есть
                _cref = _img.get("image_id") or (next(iter(_img.get("sizes",{}).values()), None) if isinstance(_img.get("sizes"),dict) else None)
            if not text:
                continue'''

NEW = '''            direction = m.get("direction", "")
            _content = m.get("content") or {}
            text = _content.get("text", "")
            _mtype = m.get("type") or "text"
            # Раньше здесь стояло `if not text: continue` — модель не узнавала,
            # что клиент прислал фото, документ, ссылку или звонил. Теперь
            # нетекстовое попадает в историю описанием. Заодно убраны два
            # мёртвых блока разбора: их результат нигде не использовался.
            if not text:
                if _mtype == "image":
                    text = "[фотография]"
                elif _mtype == "voice":
                    text = "[голосовое сообщение]"
                elif _mtype == "file":
                    text = "[документ]"
                elif _mtype == "link":
                    _lnk = (_content.get("link") or {}).get("url") or ""
                    text = ("[ссылка: %s]" % _lnk) if _lnk else "[ссылка]"
                elif _mtype == "item":
                    text = "[карточка объявления]"
                elif _mtype == "appCall":
                    text = "[звонок через Avito]"
                elif _mtype == "video":
                    text = "[видео]"
                else:
                    continue'''

src = open(path, encoding="utf-8").read()
print("РЕЖИМ:", "ЗАПИСЬ" if APPLY else "ПРОСМОТР")
if "[звонок через Avito]" in src:
    print("уже пропатчено"); sys.exit(0)
n = src.count(OLD)
print("  якорь встречается:", n)
if n != 1:
    print("  СТОП: якорь должен быть один"); sys.exit(1)
out = src.replace(OLD, NEW, 1)
try:
    ast.parse(out)
except SyntaxError as e:
    print("  СТОП: синтаксис сломан:", e); sys.exit(1)
h = lambda s: hashlib.sha256(s.encode()).hexdigest()[:12]
print("SHA %s -> %s (строк %s -> %s)" % (h(src), h(out), len(src.splitlines()), len(out.splitlines())))
if not APPLY:
    print("ничего не записано, повторите с --apply"); sys.exit(0)
bak = path + ".bak_mopctx_" + str(int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(path, "w", encoding="utf-8").write(out)
print("записан, бэкап", os.path.basename(bak))
