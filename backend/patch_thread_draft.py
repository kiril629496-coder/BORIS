# -*- coding: utf-8 -*-
"""П.1 клиентских замечаний: Unified Inbox не знал о черновиках МОПа.

/thread читал только messenger_messages, а готовый AI-ответ уходил лишь
карточкой в Telegram — в кабинете менеджер видел пустоту. Добавляем блок
draft: read-only, отправку и confirmation gate не трогаем.
"""
import sys, os, ast, time, hashlib

APPLY = "--apply" in sys.argv
BASE = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "/root/BORIS/backend"
path = os.path.join(BASE, "app/api/inbox_slots.py")

OLD = '        return {"status": "ok", "dialog": dialog, "messages": messages}'

NEW = '''        # П.1: состояние AI-МОПа для кабинета. Инвариант — у входящего не может
        # быть состояния «BORIS молчит и непонятно почему». Только чтение.
        draft = None
        try:
            _d = db.execute(text(
                "SELECT d.status, COALESCE(d.reply_text, ''),"
                "       COALESCE(d.updated_at, d.created_at),"
                "       (SELECT string_agg(e.event, ',' ORDER BY e.at)"
                "          FROM mop_draft_events e WHERE e.draft_id = d.id)"
                "  FROM mop_drafts d"
                " WHERE d.account_id = :acc AND d.avito_chat_id = :cid"
                "   AND d.status NOT IN ('sent', 'deleted', 'no_reply_required')"
                " ORDER BY COALESCE(d.updated_at, d.created_at) DESC LIMIT 1"),
                {"acc": account_id, "cid": avito_chat_id}).fetchone()
            if _d:
                _st, _txt, _upd, _ev = _d[0], _d[1], _d[2], (_d[3] or "")
                _events = [x for x in _ev.split(",") if x]
                _last_ok = max([i for i, e in enumerate(_events)
                                if e == "ai_generated"], default=-1)
                _last_err = max([i for i, e in enumerate(_events)
                                 if e in ("generation_failed", "ai_failed")], default=-1)
                if _txt.strip():
                    draft = {"status": "draft_ready", "text": _txt, "reason": "",
                             "can_retry": False}
                elif _last_err > _last_ok:
                    draft = {"status": "generation_failed", "text": "",
                             "reason": "BORIS не смог подготовить ответ",
                             "can_retry": True}
                elif _st == "analyzing":
                    draft = {"status": "analyzing", "text": "",
                             "reason": "BORIS готовит ответ", "can_retry": False}
                elif "card_no_ai" in _events:
                    draft = {"status": "human_required", "text": "",
                             "reason": "AI-менеджер не был активен в момент обращения",
                             "can_retry": True}
                else:
                    draft = {"status": "human_required", "text": "",
                             "reason": "BORIS не подготовил ответ — нужен сотрудник",
                             "can_retry": True}
                draft["updated_at"] = _fmt_ts(_upd) if _upd else ""
            else:
                # Черновика нет: честно говорим, подключён ли вообще новый контур
                _c = db.execute(text(
                    "SELECT contour FROM mop_modes WHERE account_id = :a"),
                    {"a": account_id}).scalar()
                if (_c or "legacy") != "new":
                    draft = {"status": "not_connected", "text": "",
                             "reason": "AI-менеджер не подключён к этому аккаунту",
                             "can_retry": False, "updated_at": ""}
        except Exception as _de:
            print("[inbox_thread] draft lookup failed: " + repr(_de)[:150], flush=True)
        return {"status": "ok", "dialog": dialog, "messages": messages, "draft": draft}'''

src = open(path, encoding="utf-8").read()
print("РЕЖИМ:", "ЗАПИСЬ" if APPLY else "ПРОСМОТР")
if '"draft": draft' in src:
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
bak = path + ".bak_draft_" + str(int(time.time()))
open(bak, "w", encoding="utf-8").write(src)
open(path, "w", encoding="utf-8").write(out)
print("записан, бэкап", os.path.basename(bak))
