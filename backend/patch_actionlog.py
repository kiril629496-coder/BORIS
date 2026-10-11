"""Аддитивное расширение log_action шестью полями доказательности. Dry-run по умолчанию."""
import sys, os, time, shutil, tempfile, py_compile

F = "/root/BORIS/backend/app/services/action_log.py"

E = [
 ("1. константа boris_auto",
  '''ACTOR_USER = "user"        # человек нажал кнопку''',
  '''ACTOR_USER = "user"        # человек нажал кнопку
ACTOR_BORIS_AUTO = "boris_auto"   # автономное действие BORIS в рамках мандата'''),

 ("2. новые параметры в сигнатуре",
  '''               reason: str = "", actor: str = ACTOR_BORIS, user_id=None,
               source: str = ""):''',
  '''               reason: str = "", actor: str = ACTOR_BORIS, user_id=None,
               source: str = "", trigger=None, mandate_id=None,
               mandate_version=None, request_id=None, balance_status=None,
               guard_reason=None):'''),

 ("3. колонки в INSERT",
  '''                "INSERT INTO action_log (account_id, actor, user_id, source, action,"
                " object_kind, object_name, before_val, after_val, reason)"
                " VALUES (:acc, :act, :uid, :src, :a, :ok, :on, :bv, :av, :r)"),''',
  '''                "INSERT INTO action_log (account_id, actor, user_id, source, action,"
                " object_kind, object_name, before_val, after_val, reason,"
                " trigger, mandate_id, mandate_version, request_id,"
                " balance_status, guard_reason)"
                " VALUES (:acc, :act, :uid, :src, :a, :ok, :on, :bv, :av, :r,"
                " :trg, :mid, :mver, :rid, :bst, :grr)"),'''),

 ("4. значения новых параметров",
  '''                 "r": (reason or "")[:1000]})''',
  '''                 "r": (reason or "")[:1000],
                 "trg": (trigger or None), "mid": mandate_id,
                 "mver": mandate_version, "rid": (request_id or None),
                 "bst": (balance_status or None),
                 "grr": (guard_reason or None)})'''),
]


def main():
    apply = "--apply" in sys.argv
    src = open(F, encoding="utf-8").read()
    for label, old, new in E:
        n = src.count(old)
        if n != 1:
            print(f"СТОП: якорь «{label}» найден {n} раз")
            sys.exit(1)
        if new in src:
            print(f"СТОП: «{label}» уже применён")
            sys.exit(1)
        src = src.replace(old, new, 1)
        print(f"ok  {label}")
    fd, t = tempfile.mkstemp(suffix=".py"); os.close(fd)
    open(t, "w", encoding="utf-8").write(src)
    py_compile.compile(t, doraise=True)
    print("COMPILE PASS")
    if not apply:
        os.unlink(t); print("DRY-RUN PASS. Файл не тронут."); return
    bak = f"{F}.bak_alog_{int(time.time())}"
    shutil.copy2(F, bak); shutil.copy2(t, F); os.unlink(t)
    py_compile.compile(F, doraise=True)
    print(f"ПРИМЕНЕНО {F}  бэкап {bak}")


if __name__ == "__main__":
    main()
