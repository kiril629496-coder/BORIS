"""P0-B — денежная безопасность CPX-контура. Dry-run по умолчанию, применение: --apply"""
import sys, os, time, shutil, py_compile

ROOT = "/root/BORIS/backend"
ADV = os.path.join(ROOT, "app/api/cpx_advisor.py")
RUN = os.path.join(ROOT, "cpx_advisor_runner.py")

E = []

E.append((ADV, "выдача advice", '''                "raise": raise_bids, "lower_or_archive": archive, "watching": watching,
''', '''                "raise": raise_bids,
                "lower": [],
                "archive_candidates": archive,
                "lower_or_archive": archive,
                "watching": watching,
'''))

E.append((ADV, "summary", '''f"Кандидаты на снижение/архив: {len(archive)}. "''',
          '''f"Требуют внимания: {len(archive)}. "'''))

E.append((ADV, "execution context", '''    action: str              # "raise" | "lower" | "archive"
''', '''    action: str              # "raise" | "lower" | "archive"
    actor_type: str = "user"
    actor_user_id: str = ""
    source: str = ""
    request_id: str = ""
'''))

E.append((ADV, "актор archive", '''            _audit_log(body.account_id, "cpx_apply_archive",
                       f"объявление {body.item_id}: снято продвижение (0 контактов)", "boris")''',
          '''            _audit_log(body.account_id, "cpx_apply_archive",
                       f"объявление {body.item_id}: снято продвижение (0 контактов)",
                       body.actor_type or "user")'''))

E.append((ADV, "log_action archive", '''                           reason="рекомендация советника: объявление не приносит обращений",
                           actor=ACTOR_USER, source="cpx_advisor.apply_one")''',
          '''                           reason="рекомендация советника: объявление не приносит обращений",
                           actor=(body.actor_type or "user"),
                           source=(body.source or "cpx_advisor.apply_one"))'''))

E.append((ADV, "ранний no-op", '''        payload = {"actionTypeID": action_type, "bidPenny": int(new_bid), "itemID": body.item_id}''',
          '''        # P0-B: холостое изменение не является мутацией.
        if int(new_bid) == int(cur_bid):
            return {"status": "skipped",
                    "reason_code": "skipped_bid_already_at_minimum",
                    "action": body.action, "item_id": body.item_id,
                    "bid_rub": cur_bid / 100, "changed_avito": False}

        payload = {"actionTypeID": action_type, "bidPenny": int(new_bid), "itemID": body.item_id}'''))

E.append((ADV, "актор ставок", '''                   f"объявление {body.item_id}: ставка {cur_bid/100:.0f}\u2192{new_bid/100:.0f}\u20bd ({body.action})", "user")''',
          '''                   f"объявление {body.item_id}: ставка {cur_bid/100:.0f}\u2192{new_bid/100:.0f}\u20bd ({body.action})",
                   body.actor_type or "user")'''))

E.append((ADV, "log_action ставок", '''                       reason="рекомендация советника по статистике за 7 дней",
                       actor=ACTOR_USER, source="cpx_advisor.apply_one")''',
          '''                       reason="рекомендация советника по статистике за 7 дней",
                       actor=(body.actor_type or "user"),
                       source=(body.source or "cpx_advisor.apply_one"))'''))

E.append((ADV, "единый шаг ставки", '''        step = int(cur_bid * MAX_BID_STEP_PCT / 100) or 100  # минимум 1\u20bd шаг''',
          '''        # P0-B: шаг берётся из той же константы, что и текст рекомендации,
        # MAX_BID_STEP_PCT остаётся жёстким потолком.
        step = int(cur_bid * BID_STEP_PCT / 100) or 100
        _cap = int(cur_bid * MAX_BID_STEP_PCT / 100) or 100
        if step > _cap:
            step = _cap'''))

E.append((RUN, "исполнительный блок", '''                # АВТОПИЛОТ: если включён — применяем рекомендации (с предохранителями внутри apply_one)
                if acc in auto_accounts and advice:
                    recs = advice.get("recommendations", {})
                    applied = 0
                    for action_key, act in [("raise", "raise"), ("lower_or_archive", "archive")]:
                        for it in recs.get(action_key, []):
                            try:
                                ar = httpx.post("http://127.0.0.1:8000/api/cpx_advisor/apply_one",
                                    json={"account_id": acc, "item_id": it["id"], "action": act}, timeout=30)
                                if ar.json().get("status") == "ok":
                                    applied += 1
                            except Exception as e:
                                print(f"    apply {it.get('id')}: ошибка {str(e)[:80]}")
                    if applied:
                        print(f"    🤖 АВТОПИЛОТ: применено действий = {applied}")''',
          '''                # P0-B: автоматически исполняется ТОЛЬКО raise.
                if acc in auto_accounts and advice:
                    recs = advice.get("recommendations", {})
                    considered = would_apply = applied = skipped = blocked = failed = 0
                    budget = MAX_ITEMS_PER_CYCLE
                    for action_key, act in AUTO_EXECUTABLE:
                        for it in recs.get(action_key, []):
                            considered += 1
                            if budget <= 0:
                                blocked += 1
                                continue
                            if not APPLY:
                                would_apply += 1
                                budget -= 1
                                continue
                            try:
                                ar = httpx.post("http://127.0.0.1:8000/api/cpx_advisor/apply_one",
                                    json={"account_id": acc, "item_id": it["id"], "action": act,
                                          "actor_type": "boris", "source": "cpx_advisor_runner",
                                          "request_id": RUN_ID}, timeout=30)
                                st = ar.json().get("status")
                                if st == "ok":
                                    applied += 1
                                    budget -= 1
                                elif st == "skipped":
                                    skipped += 1
                                else:
                                    blocked += 1
                            except Exception as e:
                                failed += 1
                                print(f"    apply {it.get('id')}: ошибка {str(e)[:80]}")
                    archive_n = len(recs.get("archive_candidates")
                                    or recs.get("lower_or_archive") or [])
                    print(f"    considered={considered} would_apply={would_apply} "
                          f"applied={applied} skipped={skipped} blocked={blocked} failed={failed} "
                          f"archive_candidates={archive_n} (не исполняются)")'''))

E.append((RUN, "шапка раннера", '''import json, httpx, psycopg2
from datetime import datetime''',
          '''import json, sys, uuid, httpx, psycopg2
from datetime import datetime

APPLY = "--apply" in sys.argv
MAX_ITEMS_PER_CYCLE = 5
AUTO_EXECUTABLE = [("raise", "raise")]
RUN_ID = uuid.uuid4().hex[:12]'''))


def main():
    apply = "--apply" in sys.argv
    files = {}
    for path in {p for p, *_ in E}:
        files[path] = open(path, encoding="utf-8").read()

    for path, label, old, new in E:
        n = files[path].count(old)
        if n != 1:
            print(f"СТОП: якорь «{label}» найден {n} раз в {os.path.basename(path)}")
            sys.exit(1)
        if new in files[path]:
            print(f"СТОП: «{label}» уже применён")
            sys.exit(1)
        files[path] = files[path].replace(old, new, 1)
        print(f"ok  {os.path.basename(path):22s} {label}")

    if not apply:
        print("\nDRY-RUN. Все {} якорей найдены ровно по одному разу".format(len(E)) + ". Файлы не тронуты.")
        return

    ts = int(time.time())
    for path, content in files.items():
        bak = f"{path}.bak_p0b_{ts}"
        shutil.copy2(path, bak)
        open(path, "w", encoding="utf-8").write(content)
        py_compile.compile(path, doraise=True)
        print(f"ПРИМЕНЕНО {path}  бэкап {bak}")
    print("\nГотово. Крон НЕ включается этим патчем.")


if __name__ == "__main__":
    main()
