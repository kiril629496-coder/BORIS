# -*- coding: utf-8 -*-
"""Ключи AI-агентов.

    venv/bin/python3 -m app.ext_api.keygen issue --name chatgpt --actor chatgpt \\
                     --scopes read,write,tasks
    venv/bin/python3 -m app.ext_api.keygen issue --name claude  --actor claude \\
                     --scopes read,write,tasks
    venv/bin/python3 -m app.ext_api.keygen list
    venv/bin/python3 -m app.ext_api.keygen revoke --id 3

Ключ печатается ОДИН раз — в базе лежит только его sha256 и префикс.
"""
import argparse
from . import auth


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("issue")
    i.add_argument("--name", required=True)
    i.add_argument("--actor", help="имя агента: chatgpt / claude / <своё>")
    i.add_argument("--actor-type", default="ai_agent")
    i.add_argument("--scopes", default="read,write,tasks")
    i.add_argument("--accounts", default="", help="через запятую; пусто = все аккаунты")
    i.add_argument("--owner", type=int, default=None)
    i.add_argument("--rate-limit", type=int, default=60)
    i.add_argument("--max-items", type=int, default=200)

    sub.add_parser("list")

    r = sub.add_parser("revoke")
    r.add_argument("--id", type=int, required=True)

    a = ap.parse_args()
    if a.cmd == "issue":
        accounts = [x.strip() for x in a.accounts.split(",") if x.strip()] or None
        raw = auth.issue_key(a.name, a.scopes, accounts, a.owner,
                             actor_name=(a.actor or a.name), actor_type=a.actor_type,
                             rate_limit=a.rate_limit, max_items=a.max_items)
        print("\n  КЛЮЧ (сохраните сейчас, второй раз не покажем):\n")
        print("   " + raw + "\n")
        print(f"  агент: {a.actor or a.name} ({a.actor_type})   scopes: {a.scopes}   "
              f"аккаунты: {accounts or 'все'}   лимит: {a.rate_limit}/мин\n")
    elif a.cmd == "list":
        for k in auth.list_keys():
            print(f"  #{k['id']:>3}  {str(k['actor_name']):<12} {k['name']:<22} "
                  f"{k['scopes']:<22} аккаунты={str(k['account_ids'] or 'все'):<10} "
                  f"активен={'да' if k['active'] else 'нет':<4} "
                  f"вызовов={k['requests_count']}")
    else:
        auth.revoke_key(a.id)
        print(f"  ключ #{a.id} отозван")


if __name__ == "__main__":
    main()
