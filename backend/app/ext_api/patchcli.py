# -*- coding: utf-8 -*-
"""Применение правок, предложенных агентами — с сервера, руками владельца.

    venv/bin/python3 -m app.ext_api.patchcli list
    venv/bin/python3 -m app.ext_api.patchcli show 3
    venv/bin/python3 -m app.ext_api.patchcli apply 3 --yes

Агент правку только ПРЕДЛАГАЕТ. Запись на диск идёт отсюда и всегда через
BACKUP -> WRITE -> VERIFY -> ROLLBACK-при-провале. Ключ здесь не нужен: тот,
кто уже сидит в терминале сервера, и есть человек, которого требует шлюз.
"""
import argparse, difflib, io, os, sys, json
from . import repo
from .errors import ApiError

HUMAN = {"scope_set": {"admin", "read", "write"}, "actor_type": "human",
         "actor_name": "owner", "name": "owner-cli"}


def _preview(edit):
    full = os.path.join(repo.ROOT, edit["path"])
    cur = io.open(full, encoding="utf-8", errors="replace").read() if os.path.isfile(full) else ""
    if edit["mode"] == "full":
        new = edit.get("content") or ""
    else:
        old = edit.get("old") or ""
        n = cur.count(old)
        if n != 1:
            return [f"  !! якорь встречается {n} раз — правка не применится, "
                    f"агенту надо перечитать файл"]
        new = cur.replace(old, edit.get("new") or "", 1)
    diff = difflib.unified_diff(cur.split("\n"), new.split("\n"),
                                fromfile=edit["path"] + " (сейчас)",
                                tofile=edit["path"] + " (станет)", lineterm="", n=2)
    out = list(diff)
    return out[:120] + ([f"  … ещё {len(out) - 120} строк diff"] if len(out) > 120 else [])


def cmd_list(a):
    ps = repo.list_patches(a.job, a.status)
    if not ps:
        print("  предложенных правок нет")
        return
    for p in ps:
        print(f"  #{p['patch_id']:>4}  {p['status']:<12} {p['dev_job_id']:<10} "
              f"{p['author']:<10} {', '.join(p['files'])[:70]}")
        if p.get("note"):
            print(f"        {p['note'][:100]}")


def cmd_show(a):
    p = repo.get_patch(a.id)
    print(f"\n  правка #{p['patch_id']}  ({p['status']})  от {p['author']}  "
          f"по задаче {p['dev_job_id']}")
    if p.get("note"):
        print(f"  «{p['note']}»")
    for e in p["edits"]:
        print(f"\n  --- {e['path']}  ({e['mode']})")
        for line in _preview(e):
            print("  " + line)
    if p.get("verify_output"):
        print("\n  вывод прошлой проверки (хвост):")
        print("  " + p["verify_output"][-1500:].replace("\n", "\n  "))
    print()


def cmd_apply(a):
    p = repo.get_patch(a.id)
    if not a.yes:
        cmd_show(a)
        print(f"  это только предпросмотр. Применить: "
              f"patchcli apply {a.id} --yes\n")
        return 0
    print(f"\n  применяю правку #{p['patch_id']}: {', '.join(p['files'])}")
    try:
        manifest = json.load(open(a.manifest)) if a.manifest else None
        r = repo.apply(HUMAN, a.id, confirm=True, skip_verify=a.skip_verify, manifest=manifest)
    except ApiError as e:
        print(f"\n  ОТКАЗ: {e.message}")
        d = e.details or {}
        if d.get("restored"):
            print(f"  файлы возвращены как были: {', '.join(d['restored'])}")
        if d.get("verify_tail"):
            print("\n  хвост проверки:")
            print("  " + str(d["verify_tail"]).replace("\n", "\n  "))
        return 1
    print(f"  статус: {r['status']}")
    print(f"  бэкап:  {r['backup_dir']}")
    print("\n  вывод проверки:")
    print("  " + (r.get("verify_output") or "").replace("\n", "\n  "))
    print("\n  этот вывод и есть evidence для закрытия фазы.\n")
    return 0


def cmd_apply_all(a):
    """Применить все предложенные правки подряд.

    Каждая правка применяется отдельно и со своей проверкой: упавшая
    откатывается сама и не мешает остальным. В конце — итог человеческим
    языком, чтобы не читать простыню вывода сборок.
    """
    ps = [p for p in repo.list_patches(a.job, "proposed")]
    if not ps:
        print("  предложенных правок нет — применять нечего")
        return 0
    print(f"\n  правок к применению: {len(ps)}\n")
    ok, failed = [], []
    for p in ps:
        pid = p["patch_id"]
        files = ", ".join(p["files"])[:80]
        print(f"  --- #{pid}  {files}")
        try:
            r = repo.apply(HUMAN, pid, confirm=True, skip_verify=a.skip_verify)
            ok.append((pid, files, r.get("backup_dir")))
            print(f"      применено, бэкап {r.get('backup_dir')}")
        except ApiError as e:
            failed.append((pid, files, e.message))
            print(f"      ОТКАЗ: {e.message}")
            d = e.details or {}
            if d.get("restored"):
                print(f"      файлы возвращены как были: {', '.join(d['restored'])}")
            if not a.keep_going:
                print("\n  остановился на первой неудаче. Дальше — patchcli show "
                      f"{pid}, либо повторите с --keep-going\n")
                break
    print(f"\n  ИТОГ: применено {len(ok)}, не применено {len(failed)}")
    for pid, files, why in failed:
        print(f"    #{pid} {files}: {why}")
    if ok:
        print("\n  бэкапы каждой правки лежат рядом, откат — patchcli со списком "
              "или вручную из каталога бэкапа")
    print()
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(prog="patchcli")
    sub = ap.add_subparsers(dest="cmd", required=True)

    l = sub.add_parser("list", help="показать предложенные правки")
    l.add_argument("--job", default=None, help="dev_12")
    l.add_argument("--status", default=None, help="proposed / applied / rolled_back")

    s = sub.add_parser("show", help="показать diff правки")
    s.add_argument("id", type=int)

    p = sub.add_parser("apply", help="применить правку")
    p.add_argument("id", type=int)
    p.add_argument("--manifest", help="verified before/after SHA-256 manifest")
    p.add_argument("--yes", action="store_true", help="без него — только предпросмотр")
    p.add_argument("--skip-verify", action="store_true",
                   help="не запускать сборку (только если знаете, зачем)")

    aa = sub.add_parser("apply-all", help="применить все предложенные правки")
    aa.add_argument("--yes", action="store_true", required=True,
                    help="обязателен: это запись на диск")
    aa.add_argument("--job", default=None, help="только по одной задаче: dev_12")
    aa.add_argument("--keep-going", action="store_true",
                    help="не останавливаться на первой неудачной правке")
    aa.add_argument("--skip-verify", action="store_true")

    a = ap.parse_args()
    if a.cmd == "list":
        return cmd_list(a) or 0
    if a.cmd == "show":
        return cmd_show(a) or 0
    if a.cmd == "apply-all":
        return cmd_apply_all(a)
    a.skip_verify = getattr(a, "skip_verify", False)
    return cmd_apply(a)


if __name__ == "__main__":
    sys.exit(main() or 0)
