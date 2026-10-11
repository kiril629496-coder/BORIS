# -*- coding: utf-8 -*-
"""Воронка в Executive Report: звонки и новые переписки по каждому аккаунту.
Денежный блок НЕ считается из дельты баланса — только статус.
Просмотр по умолчанию, запись --apply."""
import hashlib, os, sys, time

PATH = os.environ.get("BORIS_REPORT", "/root/BORIS/backend/exec_report_v4.py")
MARK = "BORIS_FUNNEL_CHANNELS"

HELPER = '''
# ''' + MARK + ''': каналы обращения по каждому аккаунту.
# Звонок берём из Avito CallTracking по дате call_time; уникальность - по
# непустому buyer_phone. Новая переписка = ПЕРВОЕ ВХОДЯЩЕЕ сообщение диалога
# пришло в этот день (повторное письмо в старый диалог сюда не попадает).
# Деньги здесь НЕ считаем: дельта баланса это движение кошелька, а не расход.
def _channels(cur, acc, day):
    out = {"calls": 0, "talked": 0, "phones": 0, "new_dialogs": 0, "calls_ok": True}
    try:
        from app.api.calltracking import calltracking_stats
        r = calltracking_stats(acc, 3) or {}
        _ph = set()
        for c in (r.get("calls") or []):
            if str(c.get("call_time") or "")[:10] != day:
                continue
            out["calls"] += 1
            if (c.get("talk_duration") or 0) > 0:
                out["talked"] += 1
            p = str(c.get("buyer_phone") or "").strip()
            if p:
                _ph.add(p)
        out["phones"] = len(_ph)
    except Exception:
        out["calls_ok"] = False
    try:
        rows = q(cur,
                 "SELECT count(*) FROM (SELECT avito_chat_id,"
                 " to_timestamp(min(avito_created_at) / CASE WHEN min(avito_created_at)"
                 " > 100000000000 THEN 1000 ELSE 1 END)::date AS d"
                 " FROM messenger_messages WHERE account_id=%s"
                 "   AND direction IN ('in','incoming') GROUP BY avito_chat_id) t"
                 " WHERE d = %s", (acc, day))
        out["new_dialogs"] = int(rows[0][0]) if rows else 0
    except Exception:
        pass
    return out


'''

A1 = "def collect(cur, owner, day, accs):\n"
N1 = HELPER + A1

A2 = "            \"views_today\": views,\n"
N2 = "            \"views_today\": views,\n            \"channels\": _channels(cur, a, day),\n"

A3 = ("        L.append(f\"   Расход за полный день: недоступен. Причина: {a['spent']['reason']}\")\n"
      "        L.append(\"   Цена обращения: не считается без достоверного расхода\")\n")
N3 = ("        _ch = a.get(\"channels\") or {}\n"
      "        if _ch.get(\"calls_ok\"):\n"
      "            L.append(f\"   Звонки: {_ch.get('calls', 0)} · с разговором: {_ch.get('talked', 0)}\"\n"
      "                     f\" · уникальных номеров: {_ch.get('phones', 0)}\")\n"
      "        else:\n"
      "            L.append(\"   Звонки: данные телефонии за день не получены\")\n"
      "        L.append(f\"   Новые переписки: {_ch.get('new_dialogs', 0)}\")\n"
      "        L.append(\"   Расход на продвижение: точная сумма недоступна по текущим данным\")\n"
      "        L.append(\"   Цена обращения: не рассчитывается без достоверного расхода\")\n")

def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]

def main():
    do = "--apply" in sys.argv
    src = open(PATH, encoding="utf-8").read()
    print("file: %s  sha %s  строк %d" % (PATH, sha(src), src.count("\n")))
    if MARK in src:
        print("ALREADY — правка уже применена")
        return 0
    out = src
    for old, new in ((A1, N1), (A2, N2), (A3, N3)):
        n = out.count(old)
        if n != 1:
            print("STOP — якорь найден %d раз(а): %s" % (n, old.strip().splitlines()[0][:60]))
            return 2
        out = out.replace(old, new)
    try:
        compile(out, PATH, "exec")
    except SyntaxError as e:
        print("STOP — не компилируется: %s" % e)
        return 3
    print("new sha %s  строк %d (+%d)" % (sha(out), out.count("\n"), out.count("\n") - src.count("\n")))
    if not do:
        print("DRY RUN. Запись: venv/bin/python patch_funnel.py --apply")
        return 0
    bak = "%s.bak_funnel_%d" % (PATH, int(time.time()))
    open(bak, "w", encoding="utf-8").write(src)
    open(PATH, "w", encoding="utf-8").write(out)
    print("backup %s" % bak)
    print("APPLIED")
    return 0

sys.exit(main())
