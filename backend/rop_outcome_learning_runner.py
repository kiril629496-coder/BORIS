#!/usr/bin/env python3
"""Outcome-linked ROP learning.

Associates call/chat analyses with canonical CRM deals and learns only from
anonymized checklist dimensions. It does NOT claim causality: results are
stored as outcome associations and are re-evaluated as CRM stages mature.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import text
from app.db.session import SessionLocal

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
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {}


def _canon(item):
    s = re.sub(r"^\s*\d+[.)]?\s*", "", str(item or "").strip().lower())
    for key, label, needles in DIMENSIONS:
        if any(n in s for n in needles):
            return key, label
    return None


def analysis_dims(raw):
    data = _json(raw)
    grouped = defaultdict(list)
    for row in data.get("checklist") or []:
        if not isinstance(row, dict):
            continue
        c = _canon(row.get("item"))
        if c:
            grouped[c].append(bool(row.get("done")))
    out = {}
    for (key, label), vals in grouped.items():
        out[key] = {
            "label": label,
            "done": (sum(1 for v in vals if v) / max(1, len(vals))) >= 0.5,
        }
    return out


def outcome_score(code, semantic, status):
    c = str(code or "").strip().lower()
    sem = str(semantic or "").strip().lower()
    st = str(status or "").strip().lower()
    if st == "lost" or sem == "lost" or c in {"lost", "closed_lost"}:
        return 0.0
    if st in {"won", "success", "successful", "paid"} or sem == "won" or c in {"won", "paid", "payment"}:
        return 100.0
    if c in {"contract", "agreement"}:
        return 85.0
    if c in {"proposal", "offer", "quote"}:
        return 70.0
    if c in {"qualified", "qualification"}:
        return 55.0
    if c in {"work", "in_work"}:
        return 45.0
    if c in {"contacted", "contact"}:
        return 35.0
    if c in {"new", "lead"}:
        return 15.0
    return 10.0


def niche_key(name, niche):
    s = (str(name or "") + " " + str(niche or "")).lower().replace("ё", "е")
    rules = [
        ("bytovki", ("бытов",)),
        ("plitka", ("тротуар", "брусчат", "плитк")),
        ("mebel", ("мебел", "кухн", "гардероб")),
        ("steklo", ("стекл", "перегород")),
        ("shtukaturka", ("штукатур",)),
        ("beton_stroitelstvo", ("бетон", "фундамент", "монолит", "жби")),
        ("almaznoe", ("алмазн",)),
        ("evakuatory", ("эвакуатор",)),
        ("marketing", ("маркетинг", "продвижение на авито")),
    ]
    for key, needles in rules:
        if any(n in s for n in needles):
            return key
    return ""


def ensure_tables(db):
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS rop_outcome_links (
            analysis_kind varchar(16) NOT NULL,
            analysis_id bigint NOT NULL,
            account_id text NOT NULL,
            deal_id bigint NOT NULL,
            stage_code text,
            stage_name text,
            semantic_type text,
            deal_status text,
            outcome_score numeric NOT NULL DEFAULT 0,
            amount_kopeks bigint NOT NULL DEFAULT 0,
            deal_updated_at timestamptz,
            first_linked_at timestamptz NOT NULL DEFAULT now(),
            last_seen_at timestamptz NOT NULL DEFAULT now(),
            score_changed_at timestamptz,
            PRIMARY KEY (analysis_kind,analysis_id)
        )
    """))
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS rop_outcome_brain (
            id bigserial PRIMARY KEY,
            generated_at timestamptz NOT NULL DEFAULT now(),
            payload jsonb NOT NULL
        )
    """))
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS rop_niche_brain (
            niche_key varchar(80) PRIMARY KEY,
            generated_at timestamptz NOT NULL DEFAULT now(),
            payload jsonb NOT NULL
        )
    """))
    db.commit()


def upsert_links(db):
    call_rows = db.execute(text("""
        SELECT ca.id,ca.account_id,a.deal_id,
               coalesce(s.code,''),coalesce(s.name,''),coalesce(s.semantic_type,''),
               coalesce(d.status,''),coalesce(d.amount_kopeks,0),d.updated_at
        FROM call_analysis ca
        JOIN LATERAL (
            SELECT x.deal_id
            FROM boris_crm_activities x
            WHERE x.deal_id IS NOT NULL
              AND coalesce(x.metadata_json->>'account_id','')=ca.account_id
              AND coalesce(x.metadata_json->>'call_id','')=ca.call_id::text
            ORDER BY x.created_at DESC,x.id DESC
            LIMIT 1
        ) a ON true
        JOIN boris_crm_deals d ON d.id=a.deal_id
        LEFT JOIN boris_crm_stages s ON s.id=d.stage_id
    """)).all()
    chat_rows = db.execute(text("""
        SELECT ca.id,ca.account_id,d.id,
               coalesce(s.code,''),coalesce(s.name,''),coalesce(s.semantic_type,''),
               coalesce(d.status,''),coalesce(d.amount_kopeks,0),d.updated_at
        FROM chat_analysis ca
        JOIN LATERAL (
            SELECT x.*
            FROM boris_crm_deals x
            WHERE x.avito_account_id=ca.account_id
              AND x.avito_chat_id=ca.chat_id
            ORDER BY x.updated_at DESC NULLS LAST,x.id DESC
            LIMIT 1
        ) d ON true
        LEFT JOIN boris_crm_stages s ON s.id=d.stage_id
    """)).all()

    changed = 0
    for kind, rows in (("call", call_rows), ("chat", chat_rows)):
        for aid, account, deal_id, code, name, sem, status, amount, updated in rows:
            score = outcome_score(code, sem, status)
            old = db.execute(text("""
                SELECT outcome_score FROM rop_outcome_links
                WHERE analysis_kind=:k AND analysis_id=:i
            """), {"k": kind, "i": aid}).scalar()
            score_changed = old is not None and float(old) != float(score)
            if score_changed:
                changed += 1
            db.execute(text("""
                INSERT INTO rop_outcome_links(
                    analysis_kind,analysis_id,account_id,deal_id,stage_code,stage_name,
                    semantic_type,deal_status,outcome_score,amount_kopeks,deal_updated_at,
                    first_linked_at,last_seen_at,score_changed_at
                )
                VALUES(:k,:i,:a,:d,:c,:n,:sem,:st,:score,:amt,:upd,now(),now(),
                       CASE WHEN :chg THEN now() ELSE NULL END)
                ON CONFLICT (analysis_kind,analysis_id) DO UPDATE SET
                    account_id=EXCLUDED.account_id,
                    deal_id=EXCLUDED.deal_id,
                    stage_code=EXCLUDED.stage_code,
                    stage_name=EXCLUDED.stage_name,
                    semantic_type=EXCLUDED.semantic_type,
                    deal_status=EXCLUDED.deal_status,
                    outcome_score=EXCLUDED.outcome_score,
                    amount_kopeks=EXCLUDED.amount_kopeks,
                    deal_updated_at=EXCLUDED.deal_updated_at,
                    last_seen_at=now(),
                    score_changed_at=CASE
                      WHEN rop_outcome_links.outcome_score<>EXCLUDED.outcome_score THEN now()
                      ELSE rop_outcome_links.score_changed_at END
            """), {
                "k": kind, "i": aid, "a": account, "d": deal_id, "c": code, "n": name,
                "sem": sem, "st": status, "score": score, "amt": int(amount or 0),
                "upd": updated, "chg": score_changed,
            })
    db.commit()
    return len(call_rows), len(chat_rows), changed


def load_samples(db):
    rows = []
    for kind, table in (("call", "call_analysis"), ("chat", "chat_analysis")):
        q = text(f"""
            SELECT a.account_id,a.analysis,l.outcome_score,l.stage_code,
                   coalesce(acc.name,''),coalesce(acc.company_niche,'')
            FROM {table} a
            JOIN rop_outcome_links l
              ON l.analysis_kind=:k AND l.analysis_id=a.id
            LEFT JOIN accounts acc ON acc.account_id=a.account_id
        """)
        for account, analysis, score, stage, name, niche in db.execute(q, {"k": kind}).all():
            rows.append({
                "kind": kind,
                "account_id": str(account),
                "analysis": analysis,
                "score": float(score or 0),
                "stage": str(stage or ""),
                "niche": niche_key(name, niche),
            })
    return rows


def aggregate(samples):
    # Per dimension -> per account, so one large client cannot dominate everyone.
    raw = defaultdict(lambda: defaultdict(lambda: {
        "done_scores": [], "miss_scores": [], "samples": 0,
    }))
    for s in samples:
        for key, d in analysis_dims(s["analysis"]).items():
            slot = raw[key][s["account_id"]]
            slot["samples"] += 1
            slot["label"] = d["label"]
            (slot["done_scores"] if d["done"] else slot["miss_scores"]).append(s["score"])

    out = []
    for key, accounts in raw.items():
        account_deltas = []
        samples_n = 0
        done_n = 0
        miss_n = 0
        label = key
        account_rows = 0
        for account, d in accounts.items():
            samples_n += int(d["samples"])
            done_n += len(d["done_scores"])
            miss_n += len(d["miss_scores"])
            label = d.get("label") or label
            if d["done_scores"] and d["miss_scores"]:
                done_avg = sum(d["done_scores"]) / len(d["done_scores"])
                miss_avg = sum(d["miss_scores"]) / len(d["miss_scores"])
                account_deltas.append(done_avg - miss_avg)
                account_rows += 1
        delta = sum(account_deltas) / len(account_deltas) if account_deltas else 0.0
        done_rate = (100.0 * done_n / samples_n) if samples_n else 0.0
        priority = max(0.0, delta) * max(0.0, 100.0 - done_rate) / 100.0
        out.append({
            "dimension": key,
            "label": label,
            "samples": samples_n,
            "done_samples": done_n,
            "miss_samples": miss_n,
            "done_rate": round(done_rate, 1),
            "accounts_seen": len(accounts),
            "accounts_with_comparison": account_rows,
            "outcome_lift_points": round(delta, 1),
            "training_priority": round(priority, 2),
            "reliable": bool(account_rows >= 2 and samples_n >= 20),
            "evidence_type": "association_not_causation",
        })
    out.sort(key=lambda x: (not x["reliable"], -x["training_priority"], -abs(x["outcome_lift_points"]), -x["samples"]))
    return out


def niche_aggregate(samples):
    grouped = defaultdict(list)
    for s in samples:
        if s["niche"]:
            grouped[s["niche"]].append(s)

    brains = {}
    for niche, rows in grouped.items():
        accounts = {x["account_id"] for x in rows}
        if len(accounts) < 2:
            continue
        dims = aggregate(rows)
        brains[niche] = {
            "version": "rop_niche_brain_v1",
            "niche_key": niche,
            "accounts": len(accounts),
            "samples": len(rows),
            "outcome_patterns": [x for x in dims if x["reliable"]][:8],
            "privacy": "aggregates_only_min_2_accounts",
        }
    return brains


def main():
    db = SessionLocal()
    try:
        ensure_tables(db)
        call_links, chat_links, changed = upsert_links(db)
        samples = load_samples(db)
        dimensions = aggregate(samples)
        reliable = [x for x in dimensions if x["reliable"]]
        stages = db.execute(text("""
            SELECT stage_code,count(*)
            FROM rop_outcome_links GROUP BY stage_code ORDER BY count(*) DESC
        """)).all()
        payload = {
            "version": "rop_outcome_brain_v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "linked_call_analyses": call_links,
                "linked_chat_analyses": chat_links,
                "linked_total": len(samples),
                "score_changes_this_run": changed,
                "stages": [{"stage": str(s or ""), "count": int(n)} for s,n in stages],
            },
            "patterns": reliable[:10],
            "all_dimensions": dimensions,
            "warning": "Associations only; do not claim causal effect from observational CRM data.",
        }
        db.execute(text(
            "INSERT INTO rop_outcome_brain(payload) VALUES(CAST(:p AS jsonb))"
        ), {"p": json.dumps(payload, ensure_ascii=False)})
        db.execute(text("""
            DELETE FROM rop_outcome_brain
            WHERE id NOT IN (
              SELECT id FROM rop_outcome_brain ORDER BY generated_at DESC,id DESC LIMIT 90
            )
        """))

        niches = niche_aggregate(samples)
        for key, np in niches.items():
            db.execute(text("""
                INSERT INTO rop_niche_brain(niche_key,generated_at,payload)
                VALUES(:k,now(),CAST(:p AS jsonb))
                ON CONFLICT (niche_key) DO UPDATE
                SET generated_at=EXCLUDED.generated_at,payload=EXCLUDED.payload
            """), {"k": key, "p": json.dumps(np, ensure_ascii=False)})
        db.commit()

        print("ROP_OUTCOME_BRAIN=PASS")
        print("CALL_LINKS=", call_links)
        print("CHAT_LINKS=", chat_links)
        print("TOTAL_LINKS=", len(samples))
        print("SCORE_CHANGES=", changed)
        print("RELIABLE_GLOBAL_PATTERNS=", len(reliable))
        print("NICHE_BRAINS=", len(niches))
        for x in reliable[:10]:
            print("PATTERN", x["dimension"], "lift=", x["outcome_lift_points"],
                  "accounts=", x["accounts_with_comparison"], "samples=", x["samples"])
        for key,np in niches.items():
            print("NICHE", key, "accounts=", np["accounts"], "samples=", np["samples"],
                  "patterns=", len(np["outcome_patterns"]))
    finally:
        db.close()


if __name__ == "__main__":
    main()
