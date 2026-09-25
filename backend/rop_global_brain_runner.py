#!/usr/bin/env python3
"""Build privacy-safe shared ROP learning from call/chat analyses."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.rop_global_brain import (
    GLOBAL_BRAIN_TABLE,
    ACCOUNT_BRAIN_TABLE,
    GLOBAL_BRAIN_VERSION,
    PUBLIC_PLAYBOOK,
    ensure_table,
)

DIMENSIONS = [
    ("greeting_intro", "Приветствие и представление", ("поздоров", "попривет", "представ")),
    ("need_discovery", "Выявление потребности", ("потребност", "задач", "событ", "критер")),
    ("budget_timing", "Бюджет и сроки", ("бюджет", "срок", "дата")),
    ("product_qualification", "Уточнение подходящего решения", ("тип", "размер", "комплектац", "вариант")),
    ("location_logistics", "География и условия выполнения", ("город", "адрес", "достав", "площадк", "манипулятор")),
    ("price_terms", "Цена и условия", ("цен", "стоим", "залог", "оплат")),
    ("solution_presentation", "Презентация решения под запрос", ("презент", "решени", "под запрос", "преимуществ")),
    ("objection_handling", "Работа с возражениями", ("возраж", "сомнен", "дорого", "подума")),
    ("contact_commitment", "Контакт или фиксация договорённости", ("контакт", "зафикс", "договорённост")),
    ("next_step", "Конкретный следующий шаг", ("следующ", "замер", "созвон", "счёт", "встреч", "визит")),
]


def _json(raw):
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _canon(item: str):
    s = re.sub(r"^\s*\d+[.)]?\s*", "", str(item or "").strip().lower())
    if not s:
        return None
    for key, label, needles in DIMENSIONS:
        if any(x in s for x in needles):
            return key, label
    return None


def _analysis_dims(data: dict):
    grouped = defaultdict(list)
    for row in data.get("checklist") or []:
        if not isinstance(row, dict):
            continue
        c = _canon(row.get("item"))
        if not c:
            continue
        key, label = c
        grouped[(key, label)].append(bool(row.get("done")))
    out = {}
    for (key, label), values in grouped.items():
        ratio = sum(1 for x in values if x) / max(1, len(values))
        out[key] = {"label": label, "done": ratio >= 0.5, "ratio": round(ratio, 3)}
    return out


def _score(data: dict):
    try:
        return max(0.0, min(100.0, float(data.get("score") or 0)))
    except Exception:
        return 0.0


def build():
    db = SessionLocal()
    try:
        ensure_table(db)

        call_rows = db.execute(text(
            "SELECT account_id, analysis FROM call_analysis WHERE analysis IS NOT NULL"
        )).all()
        chat_rows = db.execute(text(
            "SELECT account_id, analysis FROM chat_analysis WHERE analysis IS NOT NULL"
        )).all()

        stats = {}
        all_accounts = set()
        agreements = 0

        def slot(key, label):
            if key not in stats:
                stats[key] = {
                    "dimension": key,
                    "label": label,
                    "accounts": set(),
                    "call_accounts": set(),
                    "chat_accounts": set(),
                    "calls_seen": 0,
                    "chats_seen": 0,
                    "done_calls": 0,
                    "done_chats": 0,
                    "score_done_sum": 0.0,
                    "score_done_n": 0,
                    "score_miss_sum": 0.0,
                    "score_miss_n": 0,
                    "agreement_done": 0,
                    "agreement_done_n": 0,
                    "agreement_miss": 0,
                    "agreement_miss_n": 0,
                }
            return stats[key]

        for account_id, raw in call_rows:
            account_id = str(account_id or "").strip()
            if not account_id:
                continue
            all_accounts.add(account_id)
            data = _json(raw)
            agreement = bool((data.get("agreement") or {}).get("exists"))
            if agreement:
                agreements += 1
            score = _score(data)
            for key, d in _analysis_dims(data).items():
                s = slot(key, d["label"])
                s["accounts"].add(account_id)
                s["call_accounts"].add(account_id)
                s["calls_seen"] += 1
                if d["done"]:
                    s["done_calls"] += 1
                    s["score_done_sum"] += score
                    s["score_done_n"] += 1
                    s["agreement_done_n"] += 1
                    s["agreement_done"] += int(agreement)
                else:
                    s["score_miss_sum"] += score
                    s["score_miss_n"] += 1
                    s["agreement_miss_n"] += 1
                    s["agreement_miss"] += int(agreement)

        for account_id, raw in chat_rows:
            account_id = str(account_id or "").strip()
            if not account_id:
                continue
            all_accounts.add(account_id)
            data = _json(raw)
            score = _score(data)
            for key, d in _analysis_dims(data).items():
                s = slot(key, d["label"])
                s["accounts"].add(account_id)
                s["chat_accounts"].add(account_id)
                s["chats_seen"] += 1
                if d["done"]:
                    s["done_chats"] += 1
                    s["score_done_sum"] += score
                    s["score_done_n"] += 1
                else:
                    s["score_miss_sum"] += score
                    s["score_miss_n"] += 1

        dimensions = []
        for key, s in stats.items():
            total = s["calls_seen"] + s["chats_seen"]
            done = s["done_calls"] + s["done_chats"]
            row = {
                "dimension": key,
                "label": s["label"],
                "accounts_seen": len(s["accounts"]),
                "call_accounts": len(s["call_accounts"]),
                "chat_accounts": len(s["chat_accounts"]),
                "calls_seen": s["calls_seen"],
                "chats_seen": s["chats_seen"],
                "samples_seen": total,
                "done_rate": round(100.0 * done / total, 1) if total else 0.0,
                "avg_score_when_done": round(s["score_done_sum"] / s["score_done_n"], 1)
                    if s["score_done_n"] else None,
                "avg_score_when_missed": round(s["score_miss_sum"] / s["score_miss_n"], 1)
                    if s["score_miss_n"] else None,
            }
            if s["agreement_done_n"]:
                row["agreement_rate_when_done"] = round(
                    100.0 * s["agreement_done"] / s["agreement_done_n"], 1
                )
            if s["agreement_miss_n"]:
                row["agreement_rate_when_missed"] = round(
                    100.0 * s["agreement_miss"] / s["agreement_miss_n"], 1
                )
            # Privacy/evidence gate: a cross-account insight is publishable only
            # when at least two different accounts contributed.
            row["publishable"] = bool(len(s["accounts"]) >= 2 and total >= 5)
            dimensions.append(row)

        dimensions.sort(
            key=lambda x: (
                not x["publishable"],
                x["done_rate"],
                -x["accounts_seen"],
                -x["samples_seen"],
            )
        )
        publishable = [x for x in dimensions if x["publishable"]]

        # Per-account coaching memory: only aggregate percentages, never raw
        # transcript/client facts. This is private to the account_id.
        per_account = defaultdict(lambda: {
            "calls": 0,
            "chats": 0,
            "dims": defaultdict(lambda: {"seen": 0, "done": 0, "label": ""}),
        })
        for account_id, raw in call_rows:
            account_id = str(account_id or "").strip()
            if not account_id:
                continue
            pa = per_account[account_id]
            pa["calls"] += 1
            for key, d in _analysis_dims(_json(raw)).items():
                slot = pa["dims"][key]
                slot["label"] = d["label"]
                slot["seen"] += 1
                slot["done"] += int(bool(d["done"]))
        for account_id, raw in chat_rows:
            account_id = str(account_id or "").strip()
            if not account_id:
                continue
            pa = per_account[account_id]
            pa["chats"] += 1
            for key, d in _analysis_dims(_json(raw)).items():
                slot = pa["dims"][key]
                slot["label"] = d["label"]
                slot["seen"] += 1
                slot["done"] += int(bool(d["done"]))

        for account_id, pa in per_account.items():
            local_dims = []
            for key, d in pa["dims"].items():
                if int(d["seen"] or 0) < 2:
                    continue
                local_dims.append({
                    "dimension": key,
                    "label": d["label"],
                    "samples_seen": int(d["seen"]),
                    "done_rate": round(100.0 * int(d["done"]) / int(d["seen"]), 1),
                })
            local_dims.sort(key=lambda x: (x["done_rate"], -x["samples_seen"]))
            account_payload = {
                "version": "rop_account_brain_v1",
                "account_id": account_id,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "summary": {
                    "calls": int(pa["calls"]),
                    "chats": int(pa["chats"]),
                    "analyses": int(pa["calls"] + pa["chats"]),
                },
                "top_gaps": local_dims[:5],
                "top_strengths": sorted(
                    local_dims, key=lambda x: (-x["done_rate"], -x["samples_seen"])
                )[:3],
            }
            db.execute(text(f"""
                INSERT INTO {ACCOUNT_BRAIN_TABLE}(account_id, generated_at, payload)
                VALUES (:a, now(), CAST(:p AS jsonb))
                ON CONFLICT (account_id) DO UPDATE
                SET generated_at=EXCLUDED.generated_at, payload=EXCLUDED.payload
            """), {
                "a": account_id,
                "p": json.dumps(account_payload, ensure_ascii=False),
            })

        public_consensus = []
        try:
            exists = db.execute(text(
                "SELECT to_regclass('public.rop_public_consensus')"
            )).scalar()
            if exists:
                row = db.execute(text(
                    "SELECT payload FROM rop_public_consensus "
                    "ORDER BY generated_at DESC,id DESC LIMIT 1"
                )).first()
                if row and row[0]:
                    _pr = row[0] if isinstance(row[0], dict) else json.loads(row[0])
                    public_consensus = [
                        x for x in (_pr.get("rules") or [])
                        if isinstance(x, dict)
                        and bool(x.get("consensus"))
                        and int(x.get("source_count") or 0) >= 2
                    ][:12]
        except Exception:
            db.rollback()
            public_consensus = []

        payload = {
            "version": GLOBAL_BRAIN_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "privacy": {
                "raw_transcripts_shared": False,
                "client_facts_shared": False,
                "prices_shared": False,
                "names_phones_addresses_shared": False,
                "minimum_accounts_for_shared_pattern": 2,
            },
            "public_playbook": PUBLIC_PLAYBOOK,
            "public_consensus": public_consensus,
            "internal": {
                "summary": {
                    "calls": len(call_rows),
                    "chats": len(chat_rows),
                    "analyses": len(call_rows) + len(chat_rows),
                    "accounts": len(all_accounts),
                    "agreements": agreements,
                },
                "dimensions": publishable,
                "top_gaps": publishable[:5],
            },
        }

        db.execute(text(f"""
            INSERT INTO {GLOBAL_BRAIN_TABLE}(version, call_count, account_count, payload)
            VALUES (:v, :c, :a, CAST(:p AS jsonb))
        """), {
            "v": GLOBAL_BRAIN_VERSION,
            "c": len(call_rows),
            "a": len(all_accounts),
            "p": json.dumps(payload, ensure_ascii=False),
        })
        db.execute(text(f"""
            DELETE FROM {GLOBAL_BRAIN_TABLE}
            WHERE id NOT IN (
                SELECT id FROM {GLOBAL_BRAIN_TABLE}
                ORDER BY generated_at DESC, id DESC
                LIMIT 90
            )
        """))
        db.commit()

        print("ROP_GLOBAL_BRAIN=PASS")
        print("CALLS=", len(call_rows))
        print("CHATS=", len(chat_rows))
        print("ANALYSES=", len(call_rows) + len(chat_rows))
        print("ACCOUNTS=", len(all_accounts))
        print("AGREEMENTS=", agreements)
        print("PUBLISHABLE_DIMENSIONS=", len(publishable))
        for x in publishable:
            print(
                "DIM",
                x["dimension"],
                "accounts=", x["accounts_seen"],
                "samples=", x["samples_seen"],
                "done_rate=", x["done_rate"],
            )
    finally:
        db.close()


if __name__ == "__main__":
    build()
