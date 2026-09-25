#!/usr/bin/env python3
from types import SimpleNamespace
import sys

from app.db.session import SessionLocal
from app.api.campaigns import campaign_account_context, _ff_extract_plan, FFWorkflowBriefBody

ACCOUNTS = [
    "planetazayavki_65985",
    "mbroker_27859",
    "pbi_artashes_plitka_mo_83993",
    "prodazha_bytovok_25677",
    "andrey_mebel_launzh_moskva_69737",
]
BAD_FRAGMENTS = (
    "реквизит", "генеральный директор", "название компании", "сокращенное название",
    "компетентность", "профессионализм", "нашими приоритетами", "техническое автоматизированное",
)

def main():
    db = SessionLocal()
    user = SimpleNamespace(id=0, role="owner")
    failures = []
    report = {}
    try:
        for account_id in ACCOUNTS:
            ctx = campaign_account_context(account_id=account_id, db=db, user=user)
            dirs = [str(x or "").strip() for x in (ctx.get("launcher_directions") or []) if str(x or "").strip()]
            report[account_id] = dirs
            if not dirs:
                failures.append(f"{account_id}:empty")
                continue
            for direction in dirs:
                low = direction.casefold()
                if len(direction) > 100 or len(direction.split()) > 14:
                    failures.append(f"{account_id}:prose:{direction[:80]}")
                if direction.startswith("{") or "|" in direction:
                    failures.append(f"{account_id}:spintax:{direction[:80]}")
                if any(fragment in low for fragment in BAD_FRAGMENTS):
                    failures.append(f"{account_id}:boilerplate:{direction[:80]}")
        # Client truth anchors: these accounts must remain recognizably their own niche.
        joined = {k:" ".join(v).casefold() for k,v in report.items()}
        if "кух" not in joined["planetazayavki_65985"]: failures.append("planeta:not_furniture")
        if not any(x in joined["mbroker_27859"] for x in ("ипотек", "квартир", "кредит")): failures.append("maria:not_broker")
        if not any(x in joined["pbi_artashes_plitka_mo_83993"] for x in ("плит", "брусчат", "бордюр")): failures.append("pbi:not_paving")
        if "бытов" not in joined["prodazha_bytovok_25677"]: failures.append("bytovki:not_cabins")
        if not any(x in joined["andrey_mebel_launzh_moskva_69737"] for x in ("мебел", "кровать", "гарнитур", "прихож")): failures.append("andrey:not_furniture")

        # Campaign launcher parser safety: quantities are execution parameters, never
        # fake directions; compact city forms are recognized without poisoning the
        # product/service phrase; product phrases such as «в душ» must not become geo.
        parser_campaign = SimpleNamespace(default_account_id="testref_47784")
        parser_cases = [
            (
                "Тестовая уборка в Москве, 1 объявление, цена от 1000 ₽.",
                ["Тестовая уборка"], ["Москва"], 1, 1,
            ),
            (
                "Механизированная штукатурка в Сочи, 20 объявлений, цена от 3500 ₽.",
                ["Механизированная штукатурка"], ["Сочи"], 20, 20,
            ),
            (
                "Перегородки в душ, 1 объявление.",
                ["Перегородки в душ"], [], 1, 1,
            ),
        ]
        for brief, expected_dirs, expected_cities, expected_qty, expected_rows in parser_cases:
            parsed=_ff_extract_plan(db, parser_campaign, FFWorkflowBriefBody(brief=brief, variants=1))
            if parsed.get("directions") != expected_dirs:
                failures.append(f"parser:directions:{brief[:36]}:{parsed.get('directions')}")
            if parsed.get("cities") != expected_cities:
                failures.append(f"parser:cities:{brief[:36]}:{parsed.get('cities')}")
            if int(parsed.get("quantity") or 0) != expected_qty:
                failures.append(f"parser:quantity:{brief[:36]}:{parsed.get('quantity')}")
            if len(parsed.get("matrix") or []) != expected_rows:
                failures.append(f"parser:matrix:{brief[:36]}:{len(parsed.get('matrix') or [])}")
    finally:
        db.close()
    print({"status":"PASS" if not failures else "FAIL", "accounts":report, "failures":failures})
    return 0 if not failures else 2

if __name__ == "__main__":
    sys.exit(main())
