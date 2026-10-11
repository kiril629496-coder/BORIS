"""P0-B2: нейтральный reason_code no-op + HTTP-ошибка считается failed. Атомарно."""
import sys, os, time, shutil, tempfile, py_compile

ROOT = "/root/BORIS/backend"
ADV = os.path.join(ROOT, "app/api/cpx_advisor.py")
RUN = os.path.join(ROOT, "cpx_advisor_runner.py")

E = [
    (ADV, "нейтральный reason_code",
     '''                    "reason_code": "skipped_bid_already_at_minimum",''',
     '''                    "reason_code": "skipped_bid_unchanged",'''),
    (RUN, "HTTP-ошибка -> failed",
     '''                                st = ar.json().get("status")''',
     '''                                if ar.status_code >= 400:
                                    failed += 1
                                    continue
                                st = ar.json().get("status")'''),
]


def main():
    apply = "--apply" in sys.argv
    files = {}
    for path in {p for p, *_ in E}:
        files[path] = open(path, encoding="utf-8").read()
    for path, label, old, new in E:
        n = files[path].count(old)
        if n != 1:
            print(f"СТОП: якорь «{label}» найден {n} раз"); sys.exit(1)
        files[path] = files[path].replace(old, new, 1)
        print(f"ok  {os.path.basename(path):22s} {label}")

    tmp = {}
    for path, content in files.items():
        fd, t = tempfile.mkstemp(suffix=".py"); os.close(fd)
        open(t, "w", encoding="utf-8").write(content)
        py_compile.compile(t, doraise=True)
        tmp[path] = t
    print("COMPILE PASS для обеих будущих версий")

    if not apply:
        print("DRY-RUN PASS. Файлы не тронуты.")
        for t in tmp.values():
            os.unlink(t)
        return

    ts = int(time.time())
    for path, t in tmp.items():
        shutil.copy2(path, f"{path}.bak_p0b2_{ts}")
    for path, t in tmp.items():
        shutil.copy2(t, path)
        os.unlink(t)
        print(f"ПРИМЕНЕНО {path}")
    print("Готово. Крон не включается.")


if __name__ == "__main__":
    main()
