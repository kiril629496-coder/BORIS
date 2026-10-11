# -*- coding: utf-8 -*-
"""Статистика Avito за ОДИН день вместо суммы трёх суток."""
import hashlib, os, sys, time

PATH = os.environ.get("BORIS_AVITO", "/root/BORIS/backend/app/api/avito.py")
MARK = "BORIS_STATS_ONE_DAY"

A1 = (
    "            for it in stats_resp.json().get(\"result\", {}).get(\"items\", []):\n"
    "                views = 0\n"
    "                contacts = 0\n"
    "                for _s in it.get(\"stats\", []):\n"
    "                    views += _s.get(\"uniqViews\", 0)\n"
    "                    contacts += _s.get(\"uniqContacts\", 0)\n"
    "                stats_by_id[it[\"itemId\"]] = {\"views\": views, \"contacts\": contacts}\n"
)
N1 = (
    "            for it in stats_resp.json().get(\"result\", {}).get(\"items\", []):\n"
    "                # " + MARK + ": раньше три дня складывались в одну цифру и\n"
    "                # отчёт показывал 20 обращений вместо 10. Копим разбивку по\n"
    "                # датам, день выбираем ниже - ОДИН на весь аккаунт.\n"
    "                _by_day = {}\n"
    "                for _s in it.get(\"stats\", []):\n"
    "                    _d = str(_s.get(\"date\") or \"\")[:10]\n"
    "                    _by_day[_d] = {\"views\": int(_s.get(\"uniqViews\") or 0),\n"
    "                                   \"contacts\": int(_s.get(\"uniqContacts\") or 0)}\n"
    "                _raw_by_id[it[\"itemId\"]] = _by_day\n"
)

A2 = "    balance_resp = httpx.get(f\"https://api.avito.ru/core/v1/accounts/{avito_user_id}/balance/\", headers={\"Authorization\": f\"Bearer {token}\"})\n"
N2 = (
    "    # " + MARK + ": выбираем ОДИН день на весь аккаунт - сегодняшний; если он\n"
    "    # ещё пуст из-за задержки Avito, берём последний день с данными.\n"
    "    _totals = {}\n"
    "    for _m in _raw_by_id.values():\n"
    "        for _d, _v in _m.items():\n"
    "            _t = _totals.setdefault(_d, {\"views\": 0, \"contacts\": 0})\n"
    "            _t[\"views\"] += _v[\"views\"]\n"
    "            _t[\"contacts\"] += _v[\"contacts\"]\n"
    "    _pick_day = date_to if item_ids else None\n"
    "    if _pick_day not in _totals or (_totals.get(_pick_day, {}).get(\"views\", 0) == 0\n"
    "                                    and _totals.get(_pick_day, {}).get(\"contacts\", 0) == 0):\n"
    "        for _d in sorted(_totals, reverse=True):\n"
    "            if _totals[_d][\"views\"] or _totals[_d][\"contacts\"]:\n"
    "                _pick_day = _d\n"
    "                break\n"
    "    for _iid, _m in _raw_by_id.items():\n"
    "        _v = _m.get(_pick_day) or {\"views\": 0, \"contacts\": 0}\n"
    "        stats_by_id[_iid] = {\"views\": _v[\"views\"], \"contacts\": _v[\"contacts\"]}\n"
    "    print(\"[collect_stats] %s: статистика за %s (просмотры %s, запросы %s)\" % (\n"
    "        account_id, _pick_day,\n"
    "        sum(x[\"views\"] for x in stats_by_id.values()),\n"
    "        sum(x[\"contacts\"] for x in stats_by_id.values())), flush=True)\n"
    + A2
)

A3 = "    stats_by_id = {}\n    if item_ids:\n"
N3 = "    stats_by_id = {}\n    _raw_by_id = {}\n    if item_ids:\n"

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
    for old, new in ((A3, N3), (A1, N1), (A2, N2)):
        n = out.count(old)
        if n != 1:
            print("STOP — якорь найден %d раз(а): %s" % (n, old.strip()[:60]))
            return 2
        out = out.replace(old, new)
    try:
        compile(out, PATH, "exec")
    except SyntaxError as e:
        print("STOP — не компилируется: %s" % e)
        return 3
    print("new sha %s  строк %d (+%d)" % (sha(out), out.count("\n"), out.count("\n") - src.count("\n")))
    if not do:
        print("DRY RUN. Запись: venv/bin/python patch_stats_day.py --apply")
        return 0
    bak = "%s.bak_statsday_%d" % (PATH, int(time.time()))
    open(bak, "w", encoding="utf-8").write(src)
    open(PATH, "w", encoding="utf-8").write(out)
    print("backup %s" % bak)
    print("APPLIED")
    return 0

sys.exit(main())
