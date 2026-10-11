import os, shutil, sys, time, py_compile
sys.path.insert(0, os.getcwd())
P = "app/mop_core.py"
s = open(P, encoding="utf-8").read()
if "locked_by" in s.split("def apply_manual_text")[1][:900]:
    print("УЖЕ ПРИМЕНЁН"); sys.exit(0)

A = ('''def apply_manual_text(db, draft_id, body, channel, user_key):
    """Текст человека НИКОГДА не уходит в Avito сразу — только в waiting_confirm."""
    db.execute(text(
        "UPDATE mop_drafts SET reply_text = :t, reply_author = 'manager', updated_at = now()"
        " WHERE id = :i"), {"t": body, "i": draft_id})
    db.commit()
    return set_status(db, draft_id, "waiting_confirm", ("editing", "custom_waiting"),
                      "edited", channel=channel, actor_type="telegram_user",
                      actor_id=str(user_key), payload=body[:500])
''')
if s.count(A) != 1:
    print("СТОП: якорь не найден"); sys.exit(1)

NEW = ('''def apply_manual_text(db, draft_id, body, channel, user_key):
    """Текст человека НИКОГДА не уходит в Avito сразу — только в waiting_confirm.

    Из in_progress текст принимается только от того, кто взял карточку:
    иначе двое ответят одновременно и клиент получит два разных ответа.
    """
    cur = db.execute(text(
        "SELECT status, COALESCE(locked_by, '') FROM mop_drafts WHERE id = :i"
    ), {"i": draft_id}).fetchone()
    if cur is not None and cur[0] == "in_progress":
        if cur[1] and str(cur[1]) != str(user_key):
            return None, "занята сотрудником %s" % cur[1]
    db.execute(text(
        "UPDATE mop_drafts SET reply_text = :t, reply_author = 'manager', updated_at = now()"
        " WHERE id = :i"), {"t": body, "i": draft_id})
    db.commit()
    return set_status(db, draft_id, "waiting_confirm",
                      ("editing", "custom_waiting", "in_progress"),
                      "edited", channel=channel, actor_type="telegram_user",
                      actor_id=str(user_key), payload=body[:500])
''')
s = s.replace(A, NEW, 1)

# waiting_confirm должен быть достижим из in_progress
T = ('    "in_progress": ("editing", "custom_waiting", "sending", "analyzing",\n'
     '                    "deleted", "human_required", "no_reply_required", "sent"),\n')
if s.count(T) != 1:
    print("СТОП: якорь TRANSITIONS не найден"); sys.exit(1)
s = s.replace(T,
    '    "in_progress": ("editing", "custom_waiting", "waiting_confirm", "sending",\n'
    '                    "analyzing", "deleted", "human_required",\n'
    '                    "no_reply_required", "sent"),\n', 1)

try:
    compile(s, P, "exec")
except SyntaxError as e:
    print("СТОП: синтаксис —", e); sys.exit(1)
bak = "%s.bak_reply_%d" % (P, int(time.time()))
shutil.copy2(P, bak)
open(P, "w", encoding="utf-8").write(s)
open("/tmp/reply_bak.txt", "w").write(bak)
try:
    py_compile.compile(P, doraise=True)
    import importlib
    importlib.import_module("app.main")
    print("ПРИМЕНЕНО, py_compile OK, import app.main OK")
    print("бэкап:", bak)
except Exception as e:
    shutil.copy2(bak, P)
    print("ОТКАТ:", str(e)[:200]); sys.exit(1)
