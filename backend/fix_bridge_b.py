import os, shutil, sys, time, py_compile
sys.path.insert(0, os.getcwd())
TS = int(time.time())
MC = "app/mop_core.py"
mc = open(MC, encoding="utf-8").read()
if "def take_to_work" in mc:
    print("УЖЕ ПРИМЕНЁН"); sys.exit(0)

A = {}
A["statuses"] = '    "card_posted", "card_no_ai",\n)\n'
A["trans_dr"] = ('    "draft_ready": ("editing", "custom_waiting", "analyzing", "sending",\n'
                 '                    "deleted", "human_required", "no_reply_required"),\n')
A["claim"] = ('        allowed_from=("draft_ready", "waiting_confirm", "send_failed"),\n'
              '        event="send_started", channel=channel,\n')
A["btn"] = ('    if st == "human_required":\n'
            '        return [[b("↩️ Вернуть в работу", "b")]]\n')
A["cb_d"] = ('        elif code == "d":\n'
             '            r, st = set_status(db, draft_id, "deleted",\n'
             '                               ("draft_ready", "editing", "custom_waiting", "waiting_confirm"),\n')
A["cb_e"] = ('            r, st = set_status(db, draft_id, "editing",\n'
             '                               ("draft_ready", "waiting_confirm", "send_failed"),\n')
A["cb_c"] = ('            r, st = set_status(db, draft_id, "custom_waiting",\n'
             '                               ("draft_ready", "waiting_confirm", "send_failed"),\n')
A["cb_h"] = ('                               ("draft_ready", "editing", "custom_waiting",\n'
             '                                "waiting_confirm", "deleted", "send_failed", "new"),\n')
A["cb_x"] = ('            r, st = set_status(db, draft_id, "no_reply_required",\n'
             '                               ("draft_ready", "new", "human_required"),\n')
A["fn"] = 'def upsert_card(db, draft_id, channel, chat_id="", thread_id=0,\n'

for k, frag in A.items():
    n = mc.count(frag)
    if n != 1:
        print("СТОП: якорь «%s» найден %d раз (нужно 1)" % (k, n)); sys.exit(1)

# 1. статус в STATUSES и событие
mc = mc.replace(A["statuses"], '    "card_posted", "card_no_ai", "taken", "answered_externally",\n)\n', 1)
if '"in_progress"' not in mc.split("TRANSITIONS")[0]:
    mc = mc.replace('    "draft_ready",', '    "draft_ready", "in_progress",', 1)

# 2. переходы
mc = mc.replace(A["trans_dr"],
    '    "draft_ready": ("editing", "custom_waiting", "analyzing", "sending",\n'
    '                    "deleted", "human_required", "no_reply_required",\n'
    '                    "in_progress", "sent"),\n'
    '    "in_progress": ("editing", "custom_waiting", "sending", "analyzing",\n'
    '                    "deleted", "human_required", "no_reply_required", "sent"),\n', 1)

# 3. guards
mc = mc.replace(A["claim"],
    '        allowed_from=("draft_ready", "in_progress", "waiting_confirm", "send_failed"),\n'
    '        event="send_started", channel=channel,\n', 1)
mc = mc.replace(A["cb_e"],
    '            r, st = set_status(db, draft_id, "editing",\n'
    '                               ("draft_ready", "in_progress", "waiting_confirm",\n'
    '                                "send_failed"),\n', 1)
mc = mc.replace(A["cb_c"],
    '            r, st = set_status(db, draft_id, "custom_waiting",\n'
    '                               ("draft_ready", "in_progress", "waiting_confirm",\n'
    '                                "send_failed"),\n', 1)
mc = mc.replace(A["cb_d"],
    '        elif code == "d":\n'
    '            r, st = set_status(db, draft_id, "deleted",\n'
    '                               ("draft_ready", "in_progress", "editing",\n'
    '                                "custom_waiting", "waiting_confirm"),\n', 1)
mc = mc.replace(A["cb_h"],
    '                               ("draft_ready", "in_progress", "editing", "custom_waiting",\n'
    '                                "waiting_confirm", "deleted", "send_failed", "new"),\n', 1)
mc = mc.replace(A["cb_x"],
    '            r, st = set_status(db, draft_id, "no_reply_required",\n'
    '                               ("draft_ready", "in_progress", "new", "human_required"),\n', 1)

# 4. кнопки
mc = mc.replace(A["btn"],
    '    if st == "human_required":\n'
    '        return [[b("↩️ Вернуть в работу", "b")]]\n'
    '    if st == "draft_ready":\n'
    '        return [[b("🙋 Взять в работу", "t")],\n'
    '                [b("📌 Создать лид", "l"), b("❌ Закрыть", "x")]]\n'
    '    if st == "in_progress":\n'
    '        if has_reply:\n'
    '            return [[b("✅ Отправить", "a"), b("✏️ Редактировать", "e"),\n'
    '                     b("📝 Свой ответ", "c")],\n'
    '                    [b("🔄 Другой ответ", "r"), b("👤 Менеджеру", "h"),\n'
    '                     b("❌ Закрыть", "x")]]\n'
    '        return [[b("📝 Свой ответ", "c")],\n'
    '                [b("👤 Менеджеру", "h"), b("❌ Закрыть", "x")]]\n', 1)
mc = mc.replace('    st = draft["status"]\n    b = lambda t, c:',
                '    st = draft["status"]\n'
                '    has_reply = bool((draft.get("reply_text") or "").strip())\n'
                '    b = lambda t, c:', 1)

# 5. take_to_work
mc = mc.replace(A["fn"], '''def take_to_work(db, draft_id, tg_user_id, actor_name=None):
    """Взять карточку в работу. Захватывает только свободную."""
    lb = str(tg_user_id)
    row = db.execute(text(
        "UPDATE mop_drafts SET status = 'in_progress', locked_by = :lb,"
        " locked_at = now(), updated_at = now()"
        " WHERE id = :i AND status = 'draft_ready' AND locked_by IS NULL"
        " RETURNING id, status, locked_by, account_id, avito_chat_id"
    ), {"i": draft_id, "lb": lb}).fetchone()
    if row is not None:
        log_event(db, draft_id, "taken", from_status="draft_ready",
                  to_status="in_progress", channel="telegram",
                  actor_type="telegram_user", actor_id=lb,
                  meta={"actor_name": actor_name} if actor_name else None)
        db.commit()
        return "taken", dict(row._mapping)
    cur = db.execute(text(
        "SELECT id, status, locked_by FROM mop_drafts WHERE id = :i"
    ), {"i": draft_id}).fetchone()
    db.commit()
    if cur is None:
        return "bad_status", None
    d = dict(cur._mapping)
    if str(d.get("locked_by") or "") == lb:
        return "already_mine", d
    if d.get("locked_by"):
        return "busy", d
    return "bad_status", d


''' + A["fn"], 1)

# 6. ветка callback
CB = ('        elif code == "d":\n'
      '            r, st = set_status(db, draft_id, "deleted",\n')
mc = mc.replace(CB,
    '        elif code == "t":\n'
    '            _res, _dd = take_to_work(db, draft_id, user,\n'
    '                                     actor_name=(callback_query.get("from") or {}).get("first_name"))\n'
    '            if _res == "taken":\n'
    '                note = "взяли в работу"\n'
    '            elif _res == "already_mine":\n'
    '                note = "карточка уже у вас"\n'
    '            elif _res == "busy":\n'
    '                note = "уже взял: %s" % (_dd or {}).get("locked_by")\n'
    '            else:\n'
    '                note = "нельзя взять в статусе %s" % (_dd or {}).get("status")\n'
    + CB, 1)

try:
    compile(mc, MC, "exec")
except SyntaxError as e:
    print("СТОП: синтаксис ДО записи —", e); sys.exit(1)
bak = "%s.bak_bridgeB_%d" % (MC, TS)
shutil.copy2(MC, bak)
open(MC, "w", encoding="utf-8").write(mc)
open("/tmp/bridgeB_bak.txt", "w").write(bak)
try:
    py_compile.compile(MC, doraise=True)
    import importlib
    importlib.import_module("app.main")
    print("ПОДЭТАП B ПРИМЕНЁН, py_compile OK, import app.main OK")
    print("бэкап:", bak)
except Exception as e:
    shutil.copy2(bak, MC)
    print("ОТКАТ:", str(e)[:200]); sys.exit(1)
