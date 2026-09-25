"""FACTORY_H_V1 — full safe BORIS Factory cycle.

Pipeline:
A readiness
→ B planner
→ C persistent launch state
→ D onboarding enrichment
→ E executor decisions
→ F safe internal derivation
→ H stale reconciliation

G is runtime read-through and needs no periodic write.

The cycle does NOT:
- spend money
- alter bids
- send messages
- publish ads
- modify Client Config
"""

import argparse
import json
import subprocess
import sys

from app.db.session import SessionLocal
from app.factory import reconcile as R


PYTHON="venv/bin/python3"


def run(cmd):
    print("$"," ".join(cmd))

    p=subprocess.run(
        cmd,
        text=True,
        capture_output=True,
    )

    if p.stdout:
        print(p.stdout.rstrip())

    if p.returncode != 0:
        if p.stderr:
            print(
                p.stderr.rstrip(),
                file=sys.stderr,
            )

        raise RuntimeError(
            "command failed rc=%s: %s"
            % (
                p.returncode,
                " ".join(cmd),
            )
        )


def load_e():
    with open(
        "/root/BORIS/backend/run/factory_e_packets.json",
        encoding="utf-8",
    ) as fh:
        return json.load(fh)


def cycle(dry_run=False):
    # A→D is already encapsulated by the proven D runner.
    if dry_run:
        run([
            PYTHON,
            "factory_d_runner.py",
        ])
    else:
        run([
            PYTHON,
            "factory_d_runner.py",
            "--apply",
        ])

    # E always read-only; regenerates decisions from CURRENT Factory state.
    run([
        PYTHON,
        "factory_e.py",
    ])

    packets=load_e()

    # FACTORY_J_SYNC_V1
    # Keep human confirmation queue synchronized with current E decisions.
    from app.factory import confirmations as _confirmations

    _cdb=SessionLocal()

    try:
        _cres=_confirmations.sync(
            _cdb,
            packets,
            dry_run=dry_run,
        )

        if not dry_run:
            _cdb.commit()

        print(
            "confirmation sync:",
            json.dumps(
                _cres,
                ensure_ascii=False,
                default=str,
            )
        )

    except Exception:
        _cdb.rollback()
        raise

    finally:
        _cdb.close()

    db=SessionLocal()

    try:
        before=R.reconcile(
            db,
            packets,
            dry_run=True,
        )
    finally:
        db.close()

    print(
        "reconcile preview:",
        json.dumps(
            before,
            ensure_ascii=False,
            default=str,
        )
    )

    if dry_run:
        # F dry-run only.
        run([
            PYTHON,
            "factory_f.py",
        ])

        print("FACTORY H DRY RUN COMPLETE")
        return

    # F writes only proven safe Factory-derived state.
    run([
        PYTHON,
        "factory_f.py",
        "--apply",
    ])

    # Re-load decisions is unnecessary: F consumed the same current E packet.
    db=SessionLocal()

    try:
        result=R.reconcile(
            db,
            packets,
            dry_run=False,
        )

        db.commit()

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()

    print(
        "reconcile apply:",
        json.dumps(
            result,
            ensure_ascii=False,
            default=str,
        )
    )

    with open(
        "/root/BORIS/backend/run/factory_h_result.json",
        "w",
        encoding="utf-8",
    ) as fh:
        json.dump(
            result,
            fh,
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    # ACCOUNT_DISABLE_CONTRACT_V1
    # A disabled tenant must remain terminally blocked even when stale Factory
    # state from an older cycle still exists. This is idempotent and internal-only.
    _ddb = SessionLocal()
    try:
        _ddb.execute(__import__('sqlalchemy').text("""
            UPDATE factory_launch_state f
               SET launch_status='BLOCKED_CORE', core_blocked=true,
                   eligible_zero_touch=false, updated_at=now()
             WHERE EXISTS (
                 SELECT 1 FROM reliability_kill_switches r
                  WHERE r.account_id=f.account_id AND r.blocked=true
                    AND r.module IN ('actions','background')
             )
        """))
        _ddb.execute(__import__('sqlalchemy').text("""
            UPDATE factory_confirmations c
               SET status='stale', updated_at=now()
             WHERE c.status NOT IN ('stale','rejected')
               AND EXISTS (
                   SELECT 1 FROM reliability_kill_switches r
                    WHERE r.account_id=c.account_id AND r.blocked=true
                      AND r.module IN ('actions','background')
               )
        """))
        if not dry_run:
            _ddb.commit()
        else:
            _ddb.rollback()
    except Exception:
        _ddb.rollback()
        raise
    finally:
        _ddb.close()

    # FACTORY_K_EXECUTOR_V1
    # Apply only already-approved schedule decisions.
    # K never invents cron and never edits crontab.
    run([
        PYTHON,
        "factory_k.py",
        "--apply",
    ])

    # ZERO_TOUCH_LAUNCH_HOOK_V1
    # Final product-level health/state snapshot AFTER:
    # A→B→C→D→E→F→reconcile→J→K.
    run([
        PYTHON,
        "factory_zero_touch.py",
        "--apply",
    ])

    # BUSINESS_SCENARIO_AUTO_CONTINUATION_V1
    # Reuse this existing periodic Factory lifecycle; do not create a second scheduler.
    # The bridge is fail-closed and only advances scenario actions already classified
    # as autonomous-safe by app.api.home.
    from app.api.home import run_business_scenario_continuations
    scenario_stats = run_business_scenario_continuations()
    print("scenario continuations:", json.dumps(scenario_stats, ensure_ascii=False, default=str))

    # FACTORY_POST_PREPARED_AUTO_RESUME_V1
    # Reuse the existing boris-factory.timer; no second scheduler. Only account-scoped
    # lifecycle rows in post-PREPARED states are considered. External effects remain blocked.
    from app.api.new_client_launch import auto_resume_post_prepared
    handoff_stats = auto_resume_post_prepared()
    print("post-prepared handoff:", json.dumps(handoff_stats, ensure_ascii=False, default=str))

    # ACCOUNT_DISABLE_FINAL_V2
    # Final terminal fence AFTER every Factory/zero-touch/scenario/handoff stage.
    # Nothing later in this cycle is allowed to resurrect a disabled tenant.
    _fdb = SessionLocal()
    try:
        _sql = __import__('sqlalchemy').text
        _fdb.execute(_sql("""
            UPDATE factory_launch_state f
               SET launch_status='BLOCKED_CORE', core_blocked=true,
                   eligible_zero_touch=false, updated_at=now()
             WHERE EXISTS (
                 SELECT 1 FROM reliability_kill_switches r
                  WHERE r.account_id=f.account_id AND r.blocked=true
                    AND r.module IN ('actions','background')
             )
        """))
        _fdb.execute(_sql("""
            UPDATE factory_client_launch f
               SET launch_status='BLOCKED_CORE', launch_ready=false, updated_at=now()
             WHERE EXISTS (
                 SELECT 1 FROM reliability_kill_switches r
                  WHERE r.account_id=f.account_id AND r.blocked=true
                    AND r.module IN ('actions','background')
             )
        """))
        _fdb.execute(_sql("""
            UPDATE factory_confirmations c
               SET status='stale', updated_at=now()
             WHERE c.status NOT IN ('stale','rejected')
               AND EXISTS (
                   SELECT 1 FROM reliability_kill_switches r
                    WHERE r.account_id=c.account_id AND r.blocked=true
                      AND r.module IN ('actions','background')
               )
        """))
        if not dry_run:
            _fdb.commit()
        else:
            _fdb.rollback()
    except Exception:
        _fdb.rollback()
        raise
    finally:
        _fdb.close()

    # RULE_RUNTIME_V1
    # Reuse the existing Factory timer. Structured recurring rules carry their
    # own daily target; Rule Runtime materializes exactly-once slots, executes
    # only allowlisted internal executors and writes plan/fact evidence.
    from app.factory import rule_runtime as _rule_runtime
    _rdb = SessionLocal()
    try:
        _rule_stats = _rule_runtime.run_due(_rdb)
        _rdb.commit()
    except Exception:
        _rdb.rollback()
        raise
    finally:
        _rdb.close()
    print("rule runtime:", json.dumps(_rule_stats, ensure_ascii=False, default=str))

    print("FACTORY H APPLY COMPLETE")


def main():
    p=argparse.ArgumentParser()

    p.add_argument(
        "--dry-run",
        action="store_true",
    )

    args=p.parse_args()

    cycle(
        dry_run=args.dry_run
    )


if __name__=="__main__":
    main()
