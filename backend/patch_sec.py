import os, shutil, sys, time, py_compile
sys.path.insert(0, os.getcwd())
TS = int(time.time())
IMP_FULL = ("from fastapi import Depends as _DepSec\n"
            "from app.api.auth import require_owner as _ReqOwner, "
            "get_current_user as _CurUser\n")
IMP_QA = ("from fastapi import Depends as _DepSec\n"
          "from app.api.auth import require_owner as _ReqOwner\n")

def load(p):
    return open(p, encoding="utf-8").read()

def add_imp(src, imp):
    i = src.find("\n@router")
    if i < 0:
        print("СТОП: якорь @router не найден"); sys.exit(1)
    return src[:i + 1] + imp + src[i + 1:]

def one(src, frag, name):
    n = src.count(frag)
    if n != 1:
        print("СТОП: якорь «%s» найден %d раз" % (name, n)); sys.exit(1)

A, B, Q = "app/api/avito.py", "app/api/banners.py", "app/api/qa.py"
sa, sb, sq = load(A), load(B), load(Q)

if "_ReqOwner" in sa:
    print("avito.py: уже применён")
else:
    a1 = "def director_overview():\n"
    a2 = "def new_clients_overview():\n"
    one(sa, a1, "director_overview"); one(sa, a2, "new_clients_overview")
    sa = add_imp(sa, IMP_FULL)
    sa = sa.replace(a1, "def director_overview(_=_DepSec(_ReqOwner)):\n", 1)
    sa = sa.replace(a2, "def new_clients_overview(_=_DepSec(_ReqOwner)):\n", 1)

if "_ReqOwner" in sq:
    print("qa.py: уже применён")
else:
    q1 = "def qa_reports():\n"
    one(sq, q1, "qa_reports")
    sq = add_imp(sq, IMP_QA)
    sq = sq.replace(q1, "def qa_reports(_=_DepSec(_ReqOwner)):\n", 1)

if "_ReqOwner" in sb:
    print("banners.py: уже применён")
else:
    b1 = "def browse_all_banners(limit: int = 200):\n"
    b2 = "    import os as _os_ex\n    results = []\n"
    b3 = "        for account_folder in _os_ex.listdir(IMAGES_DIR):\n"
    b4 = "def get_banner_showcase(account_id: str):\n"
    b5 = "        items = _json_sc.loads(row.value) if row else []\n"
    for f, n in ((b1, "browse_all sig"), (b2, "import+results"),
                 (b3, "listdir"), (b4, "showcase sig")):
        one(sb, f, n)
    pos4 = sb.index(b4)
    try:
        pos5 = sb.index(b5, pos4)
    except ValueError:
        print("СТОП: после showcase не найдена строка items"); sys.exit(1)
    if pos5 - pos4 > 600:
        print("СТОП: items далеко от showcase (%d)" % (pos5 - pos4)); sys.exit(1)
    FILT = (b5 +
        '        if getattr(user, "role", "") != "owner":\n'
        "            from app.models.account import Account as _AccSc\n"
        "            _allowed = {a.account_id for a in db.query(_AccSc).filter(\n"
        "                _AccSc.owner_user_id == user.id).all()}\n"
        "\n"
        "            def _is_own(it):\n"
        '                u = (it.get("url") if isinstance(it, dict) else str(it)) or ""\n'
        '                if u.startswith("http://") or u.startswith("https://"):\n'
        "                    return True\n"
        '                if not u.startswith("/images/"):\n'
        "                    return False\n"
        '                seg = u.split("/", 3)\n'
        "                return len(seg) > 2 and seg[2] in _allowed\n"
        "\n"
        "            items = [it for it in items if _is_own(it)]\n")
    sb = sb[:pos5] + FILT + sb[pos5 + len(b5):]
    sb = add_imp(sb, IMP_FULL)
    sb = sb.replace(b1, "def browse_all_banners(limit: int = 200, "
                        "user=_DepSec(_CurUser)):\n", 1)
    sb = sb.replace(b2,
        "    import os as _os_ex\n"
        "    _allowed = None\n"
        '    if getattr(user, "role", "") != "owner":\n'
        "        from app.db.session import SessionLocal as _SLb\n"
        "        from app.models.account import Account as _AccB\n"
        "        _dbb = _SLb()\n"
        "        try:\n"
        "            _allowed = {a.account_id for a in _dbb.query(_AccB).filter(\n"
        "                _AccB.owner_user_id == user.id).all()}\n"
        "        finally:\n"
        "            _dbb.close()\n"
        "    results = []\n", 1)
    sb = sb.replace(b3, b3 +
        "            if _allowed is not None and account_folder not in _allowed:\n"
        "                continue\n", 1)
    sb = sb.replace(b4, "def get_banner_showcase(account_id: str, "
                        "user=_DepSec(_CurUser)):\n", 1)

NEW = {A: sa, B: sb, Q: sq}
for p, s in NEW.items():
    try:
        compile(s, p, "exec")
    except SyntaxError as e:
        print("СТОП: синтаксис ДО записи —", p, e); sys.exit(1)
baks = {}
for p, s in NEW.items():
    bak = "%s.bak_sec_%d" % (p, TS)
    shutil.copy2(p, bak); baks[p] = bak
    open(p, "w", encoding="utf-8").write(s)
try:
    for p in NEW:
        py_compile.compile(p, doraise=True)
    import importlib
    importlib.import_module("app.main")
    print("\nПАТЧ ПРИМЕНЁН, py_compile OK, import app.main OK")
    for p, b in baks.items():
        print("  бэкап:", b)
    open("/tmp/sec_ts.txt", "w").write(str(TS))
except Exception as e:
    for p, b in baks.items():
        shutil.copy2(b, p)
    print("\nОТКАТ всех трёх файлов:", str(e)[:200]); sys.exit(1)
