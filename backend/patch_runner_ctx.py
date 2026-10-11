"""Раннер передаёт полный execution_context. Dry-run по умолчанию."""
import sys, os, time, shutil, tempfile, py_compile

F = "/root/BORIS/backend/cpx_advisor_runner.py"

OLD = '''                                          "actor_type": "boris", "source": "cpx_advisor_runner",'''
NEW = '''                                          "actor_type": "boris_auto", "trigger": "cron",
                                          "source": "cpx_advisor_runner",'''


def main():
    apply = "--apply" in sys.argv
    src = open(F, encoding="utf-8").read()
    n = src.count(OLD)
    if n != 1:
        print(f"СТОП: якорь найден {n} раз")
        sys.exit(1)
    if NEW in src:
        print("СТОП: уже применён")
        sys.exit(1)
    src = src.replace(OLD, NEW, 1)
    fd, t = tempfile.mkstemp(suffix=".py"); os.close(fd)
    open(t, "w", encoding="utf-8").write(src)
    py_compile.compile(t, doraise=True)
    print("COMPILE PASS")
    if not apply:
        os.unlink(t); print("DRY-RUN PASS. Файл не тронут."); return
    bak = f"{F}.bak_ctx_{int(time.time())}"
    shutil.copy2(F, bak); shutil.copy2(t, F); os.unlink(t)
    py_compile.compile(F, doraise=True)
    print(f"ПРИМЕНЕНО {F}  бэкап {bak}")


if __name__ == "__main__":
    main()
