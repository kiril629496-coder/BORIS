"""FACTORY_D_V1 — safe onboarding → Factory bridge.

READ sources:
- accounts
- onboarding_progress via app.onboarding.read_stage()
- niche_resolver
- storage detected_category

WRITE target:
- factory_launch_state.packet ONLY

Never calls onboarding.track().
Never sends nudges.
Never enables products.
Never spends money.
"""

import json

from sqlalchemy import text

import app.onboarding as ONB
from app.services import niche_resolver as NR


def _stage_number(stage_result):
    if not isinstance(stage_result, dict):
        return None

    for key in (
        "стадия",
        "stage",
        "stage_number",
    ):
        if key in stage_result:
            try:
                return int(stage_result[key])
            except Exception:
                return stage_result[key]

    return None


def onboarding_snapshot(db, account_id):
    """Side-effect-free onboarding state."""

    try:
        st = ONB.read_stage(account_id, db)
    except TypeError:
        # Compatibility only if existing signature does not accept db.
        st = ONB.read_stage(account_id)

    if not isinstance(st, dict):
        st = {
            "status": "unknown",
            "raw": str(st),
        }

    try:
        niche = NR.resolve_niche(account_id)
    except Exception as exc:
        niche = {
            "niche": None,
            "source": None,
            "confidence": None,
            "error": type(exc).__name__,
        }

    acc = db.execute(text("""
        SELECT
            owner_user_id,
            billing_mode,
            company_niche,
            company_website,
            avito_client_id,
            avito_client_secret,
            avito_user_id
        FROM accounts
        WHERE account_id=:a
    """), {"a":account_id}).mappings().first()

    if not acc:
        return {
            "status": "missing_account",
            "stage": None,
            "ready_for_factory": False,
        }

    cat = db.execute(text("""
        SELECT 1
        FROM storage
        WHERE account_id=:a
          AND key='detected_category'
        LIMIT 1
    """), {"a":account_id}).first()

    has_keys = bool(
        acc["avito_client_id"]
        and acc["avito_client_secret"]
    )

    stage = _stage_number(st)

    return {
        "status": st.get("status"),
        "stage": stage,

        "hours_on_stage": (
            st.get("hours")
            if "hours" in st
            else st.get("hours_on_stage")
        ),

        "nudge_count": st.get("nudge_count"),
        "nudged_at": st.get("nudged_at"),

        "billing_mode": acc["billing_mode"],

        "avito": {
            "has_keys": has_keys,
            "has_user_id": bool(acc["avito_user_id"]),
        },

        "business": {
            "niche": niche.get("niche"),
            "niche_source": niche.get("source"),
            "niche_confidence": niche.get("confidence"),
            "profile_niche": acc["company_niche"],
            "website": acc["company_website"],
        },

        "category": {
            "detected": bool(cat),
        },

        "ready_for_factory": bool(
            acc["owner_user_id"] is not None
            and has_keys
            and str(
                niche.get("niche")
                or acc["company_niche"]
                or ""
            ).strip()
        ),
    }


def merge_packet(old_packet, snapshot):
    packet = dict(old_packet or {})
    packet["onboarding"] = snapshot
    return packet


def refresh_account(db, account_id):
    row = db.execute(text("""
        SELECT packet
        FROM factory_launch_state
        WHERE account_id=:a
    """), {"a":account_id}).mappings().first()

    if not row:
        return {
            "account_id": account_id,
            "result": "missing_factory_state",
        }

    old = row["packet"] or {}

    if isinstance(old, str):
        old = json.loads(old)

    snap = onboarding_snapshot(
        db,
        account_id,
    )

    merged = merge_packet(old, snap)

    db.execute(text("""
        UPDATE factory_launch_state
        SET
            packet=CAST(:packet AS jsonb),
            updated_at=now()
        WHERE account_id=:a
    """), {
        "a": account_id,
        "packet": json.dumps(
            merged,
            ensure_ascii=False,
            default=str,
        ),
    })

    return {
        "account_id": account_id,
        "result": "updated",
        "stage": snap.get("stage"),
        "ready_for_factory": snap.get(
            "ready_for_factory"
        ),
    }


def refresh_all(db):
    accounts = [
        r[0]
        for r in db.execute(text("""
            SELECT f.account_id
            FROM factory_launch_state f
            WHERE NOT EXISTS (
                SELECT 1 FROM reliability_kill_switches r
                 WHERE r.account_id=f.account_id AND r.blocked=true
                   AND r.module IN ('actions','background')
            )
            ORDER BY f.account_id
        """)).fetchall()
    ]

    return [
        refresh_account(db, a)
        for a in accounts
    ]
