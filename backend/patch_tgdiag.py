"""Временная диагностика: печать chat_id входящих сообщений. Ставится и снимается одним файлом."""
import sys, os, time, shutil, tempfile, py_compile

F = "/root/BORIS/backend/app/telegram_bot.py"

OLD = '''                message = update.get("message")'''
NEW = '''                message = update.get("message")
                try:
                    _c = (message or {}).get("chat") or {}
                    if _c:
                        print(f"[TGDIAG] chat_id={_c.get('id')} type={_c.get('type')}"
                              f" title={_c.get('title')}", flush=True)
                except Exception:
                    pass'''


def main():
    mode = "off" if "--off" in sys.argv else "on"
    apply = "--apply" in sys.argv
    src = open(F, encoding="utf-8").read()
    old, new = (OLD, NEW) if mode == "on" else (NEW, OLD)
    n = src.count(old)
    if n != 1:
        print(f"СТОП: якорь найден {n} раз. Ничего не тронуто.")
        sys.exit(1)
    src = src.replace(old, new, 1)
    fd, t = tempfile.mkstemp(suffix=".py")
    os.close(fd)
    open(t, "w", encoding="utf-8").write(src)
    py_compile.compile(t, doraise=True)
    print(f"COMPILE PASS, режим {mode}")
    if not apply:
        os.unlink(t)
        print("DRY-RUN. Файл не тронут.")
        return
    bak = f"{F}.bak_tgdiag_{int(time.time())}"
    shutil.copy2(F, bak)
    shutil.copy2(t, F)
    os.unlink(t)
    py_compile.compile(F, doraise=True)
    print(f"ПРИМЕНЕНО {F}  бэкап {bak}")


if __name__ == "__main__":
    main()
