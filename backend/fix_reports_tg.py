import os, shutil, sys, time, py_compile
sys.path.insert(0, os.getcwd())
P = "app/api/messenger.py"
s = open(P, encoding="utf-8").read()
if "_reports_to_telegram" in s:
    print("УЖЕ ПРИМЕНЁН"); sys.exit(0)

A = ('                                tgr = db.execute(_sql("SELECT telegram_chat_id FROM accounts WHERE account_id=:a"), {"a": a_id}).fetchone()\n'
     '                                if tgr and tgr[0]:\n'
     '                                    send_telegram_message(str(tgr[0]),\n'
     '                                        "📊 <b>Готов новый разбор диалогов</b>\\nБорис проанализировал переписки за неделю — загляните во вкладку «Продажи», раздел «Анализ диалогов», чтобы увидеть где сливаются лиды и что улучшить.")\n')
n = s.count(A)
if n != 1:
    print("СТОП: якорь найден %d раз (нужно 1)" % n); sys.exit(1)

NEW = ('                                _txt = ("📊 <b>Готов новый разбор диалогов</b>\\n"\n'
       '                                        "Борис проанализировал переписки за неделю — загляните "\n'
       '                                        "во вкладку «Продажи», раздел «Анализ диалогов», чтобы "\n'
       '                                        "увидеть где сливаются лиды и что улучшить.")\n'
       '                                if _reports_to_telegram(a_id):\n'
       '                                    from app import mop_core as _mc_rep\n'
       '                                    _tgt = _mc_rep.target_for(db, a_id, "reports")\n'
       '                                    if _tgt:\n'
       '                                        send_telegram_message(_tgt[0], _txt, thread_id=_tgt[1])\n'
       '                                    else:\n'
       '                                        tgr = db.execute(_sql("SELECT telegram_chat_id FROM accounts WHERE account_id=:a"), {"a": a_id}).fetchone()\n'
       '                                        if tgr and tgr[0]:\n'
       '                                            send_telegram_message(str(tgr[0]), _txt)\n')

HELPER = '''def _reports_to_telegram(account_id) -> bool:
    """Слать ли недельный разбор в Telegram. По умолчанию да; клиент может отключить."""
    import json as _j
    from app.db.session import SessionLocal as _SL
    from app.models.storage import Storage as _St
    db = _SL()
    try:
        row = db.query(_St).filter(_St.account_id == account_id,
                                   _St.key == "reports_to_telegram").first()
        if not row:
            return True
        return bool(_j.loads(row.value).get("enabled", True))
    except Exception:
        return True
    finally:
        db.close()


'''
ANCHOR2 = "def _mop_incoming(account_id, chat_id, avito_message_id, incoming_text, chat,\n"
if s.count(ANCHOR2) != 1:
    print("СТОП: якорь helper не найден"); sys.exit(1)

s = s.replace(ANCHOR2, HELPER + ANCHOR2, 1)
s = s.replace(A, NEW, 1)

try:
    compile(s, P, "exec")
except SyntaxError as e:
    print("СТОП: синтаксис ДО записи —", e); sys.exit(1)
bak = "%s.bak_reptg_%d" % (P, int(time.time()))
shutil.copy2(P, bak)
open(P, "w", encoding="utf-8").write(s)
open("/tmp/reptg_bak.txt", "w").write(bak)
try:
    py_compile.compile(P, doraise=True)
    import importlib
    importlib.import_module("app.main")
    print("ПРИМЕНЕНО, py_compile OK, import app.main OK")
    print("бэкап:", bak)
except Exception as e:
    shutil.copy2(bak, P)
    print("ОТКАТ:", str(e)[:200]); sys.exit(1)
