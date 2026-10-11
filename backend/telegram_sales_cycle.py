#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
import json
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ROOT = Path('/root/BORIS/backend')
PY = str(ROOT / 'venv/bin/python')
load_dotenv(ROOT / '.env')
DB=os.getenv('DATABASE_URL') or os.getenv('SQLALCHEMY_DATABASE_URL')
LOCK_KEY=884422990
lock_engine=create_engine(DB,pool_pre_ping=True)

def start_run() -> str:
    run_id=str(uuid.uuid4())
    with lock_engine.begin() as db:
        db.execute(text("""CREATE TABLE IF NOT EXISTS telegram_sales_cycle_runs (
          id UUID PRIMARY KEY, started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), ended_at TIMESTAMPTZ,
          status TEXT NOT NULL DEFAULT 'running', duration_seconds INTEGER, details JSONB NOT NULL DEFAULT '{}'::jsonb
        )"""))
        # A run left in 'running' for longer than the systemd watchdog is stale,
        # not healthy. Mark it explicitly so it cannot masquerade as an active cycle.
        db.execute(text("""UPDATE telegram_sales_cycle_runs SET status='timeout',ended_at=COALESCE(ended_at,NOW()),
          duration_seconds=COALESCE(duration_seconds,EXTRACT(EPOCH FROM (NOW()-started_at))::int)
          WHERE status='running' AND started_at < NOW()-interval '20 minutes'"""))
        db.execute(text("INSERT INTO telegram_sales_cycle_runs(id) VALUES(:i)"),{'i':run_id})
        # monitor_runner has its own run history. Anything still running beyond
        # the scanner's 12 minute timeout plus grace is stale and should never
        # remain indistinguishable from a live scan forever.
        db.execute(text("""UPDATE monitor_runs SET status='timeout',finished_at=COALESCE(finished_at,NOW()),
          errors=CASE WHEN COALESCE(jsonb_array_length(errors),0)=0
            THEN '[{"error":"stale_run_self_healed"}]'::jsonb ELSE errors END
          WHERE status='running' AND started_at < NOW()-interval '20 minutes'"""))
    return run_id

def finish_run(run_id: str, status: str, started_monotonic: float, details: dict) -> None:
    try:
        with lock_engine.begin() as db:
            db.execute(text("""UPDATE telegram_sales_cycle_runs SET ended_at=NOW(),status=:s,duration_seconds=:d,
              details=CAST(:j AS jsonb) WHERE id=:i"""),{
              'i':run_id,'s':status,'d':max(0,int(time.monotonic()-started_monotonic)),
              'j':json.dumps(details,ensure_ascii=False)[:20000]})
    except Exception as e:
        print(f'TG_RUN_HISTORY=DEGRADED type={type(e).__name__}')


def alert_cycle_failure(stages: dict) -> None:
    bad={k:int(v or 0) for k,v in stages.items() if not k.endswith('_sec') and k not in ('scan_delegated',) and int(v or 0)!=0}
    if not bad:
        return
    signature=json.dumps(bad,sort_keys=True,ensure_ascii=False)
    try:
        should_send=False
        with lock_engine.begin() as db:
            row=db.execute(text("SELECT value_text,updated_at FROM monitor_runtime_state WHERE key='tg_sales_cycle_last_failure'" )).first()
            if not row or str(row[0] or '')!=signature or not row[1] or row[1] < datetime.now(timezone.utc)-timedelta(hours=6):
                db.execute(text("""INSERT INTO monitor_runtime_state(key,value_text,updated_at) VALUES('tg_sales_cycle_last_failure',:v,NOW())
                  ON CONFLICT(key) DO UPDATE SET value_text=EXCLUDED.value_text,updated_at=NOW()"""),{'v':signature})
                should_send=True
        if not should_send:
            print('TG_CYCLE_ALERT=SKIP duplicate')
            return
        from app.telegram_bot import send_telegram_message
        chat=os.getenv('HOT_LEAD_REPLY_CHAT_ID','-1003952038222')
        thread=int(os.getenv('HOT_LEAD_REPLY_THREAD_ID','126') or 126)
        labels={
            'scan':'Поиск новых заявок завершился ошибкой',
            'replies':'Обработка ответов завершилась ошибкой',
            'outreach':'Отправка новых сообщений завершилась ошибкой',
            'reactivation':'Реактивация старых контактов завершилась ошибкой',
            'health':'Контроль безопасности Telegram-продаж завершился ошибкой',
        }
        body='\n'.join('• '+labels.get(k,'Этап Telegram-продаж «%s» завершился ошибкой'%k) for k in bad)
        body += '\n\nBORIS уже повторит цикл автоматически. Небезопасные отправки заблокированы. От вас действий не требуется.'
        res=send_telegram_message(chat,'🚨 <b>BORIS — Telegram-продажи требуют автопочинки</b>\n\n'+body,thread)
        print('TG_CYCLE_ALERT=' + ('PASS' if isinstance(res,dict) and res.get('ok') else 'DEGRADED'))
    except Exception as e:
        print(f'TG_CYCLE_ALERT=DEGRADED type={type(e).__name__}')


def run(label: str, args: list[str], timeout: int) -> tuple[int, str, int]:
    t0=time.monotonic()
    child_env=os.environ.copy()
    # TG_HEALTH_CURRENT_CYCLE_WIRE_V1: health invoked from the orchestrated
    # cycle may use the current scanner/safety evidence and must not deadlock on
    # requiring a previous PASS before this recovered cycle can become PASS.
    if label == 'health':
        child_env['TG_SALES_HEALTH_IN_CYCLE']='1'
    p = subprocess.run(
        [PY, *args],
        cwd=str(ROOT),
        env=child_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    out = (p.stdout or '').strip()
    print(f'[{label}] rc={p.returncode}')
    if out:
        print(out[-12000:])
    return p.returncode, out, max(0,int(time.monotonic()-t0))


def _scan_lock_busy(output: str) -> bool:
    text_out=(output or '').lower()
    return ('другой прогон уже идёт' in text_out or 'другой прогон уже идет' in text_out or
            'scan_lock_busy' in text_out or 'scanner_lock_busy' in text_out)


def wait_for_concurrent_scan(cycle_started_at: datetime, timeout_seconds: int = 420) -> tuple[int, int, str]:
    """Resolve monitor_runner advisory-lock delegation into a real stage result.

    monitor_runner intentionally exits without touching Telegram when another scanner
    owns its advisory lock. rc=0 alone is therefore not proof that this cycle scanned.
    We wait for the overlapping monitor_runs row and accept the delegated scan only
    when it actually reaches status=ok. Otherwise the sales cycle is degraded.
    """
    t0=time.monotonic()
    deadline=t0+max(5,int(timeout_seconds))
    since=cycle_started_at-timedelta(minutes=2)
    last='no_overlapping_scan'
    while time.monotonic() < deadline:
        try:
            with lock_engine.connect() as db:
                row=db.execute(text("""SELECT id,status,started_at,finished_at
                  FROM monitor_runs
                  WHERE platform='telegram' AND started_at>=:since
                  ORDER BY started_at DESC LIMIT 1"""),{'since':since}).mappings().first()
            if row:
                status=str(row.get('status') or '').lower()
                last=f"id={row.get('id')} status={status} started={row.get('started_at')} finished={row.get('finished_at')}"
                if status=='ok' and row.get('finished_at') and row.get('finished_at') >= cycle_started_at:
                    sec=max(0,int(time.monotonic()-t0))
                    print(f'TG_SCAN_DELEGATION=PASS {last} wait_sec={sec}')
                    return 0,sec,last
                if status in ('failed','timeout','degraded'):
                    sec=max(0,int(time.monotonic()-t0))
                    print(f'TG_SCAN_DELEGATION=FAIL {last} wait_sec={sec}')
                    return 75,sec,last
        except Exception as e:
            last=f'db_error={type(e).__name__}'
        time.sleep(5)
    sec=max(0,int(time.monotonic()-t0))
    print(f'TG_SCAN_DELEGATION=TIMEOUT {last} wait_sec={sec}')
    return 124,sec,last


def scheduler_conflict_guard() -> tuple[int, str]:
    """Fail closed when legacy cron duplicates core Telegram orchestration.

    The systemd sales-cycle is the single owner of discovery -> scan -> queue.
    A reintroduced root cron for any of these core stages creates split-brain
    execution and can cause duplicated Telegram reads or misleading cycle PASS.
    """
    try:
        p=subprocess.run(['crontab','-l'],text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=10)
        body=p.stdout or ''
    except Exception as e:
        print(f'TG_SCHEDULER_GUARD=DEGRADED type={type(e).__name__}')
        return 2, type(e).__name__
    signatures=(
        'monitor_runner.py --scan',
        'monitor_source_discovery.py --apply',
        'hot_lead_queue.py',
        'telegram_sales_cycle.py',
    )
    conflicts=[]
    for raw in body.splitlines():
        line=raw.strip()
        if not line or line.startswith('#'):
            continue
        if any(sig in line for sig in signatures):
            conflicts.append(line[:300])
    try:
        dp=subprocess.run(['systemctl','is-active','boris-tg-sales-discovery.timer'],text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=10)
        if (dp.stdout or '').strip()=='active':
            conflicts.append('systemd:boris-tg-sales-discovery.timer active')
    except Exception:
        conflicts.append('systemd:boris-tg-sales-discovery.timer state_unknown')
    if conflicts:
        print('TG_SCHEDULER_GUARD=FAIL conflicts='+str(len(conflicts)))
        for line in conflicts[:8]: print('TG_SCHEDULER_CONFLICT '+line)
        return 2, '; '.join(conflicts[:8])
    print('TG_SCHEDULER_GUARD=PASS')
    return 0, ''


def maybe_hydrate_source_hashes() -> None:
    """Hydrate Telegram access_hash after a global cooldown, before scan.

    StringSession does not persist a durable entity cache. Without access_hash,
    hundreds of public groups can force ResolveUsername repeatedly. This preflight
    runs outside the sales advisory lock because discovery owns the same lock.
    """
    now=datetime.now(timezone.utc)
    with lock_engine.begin() as db:
        db.execute(text("CREATE TABLE IF NOT EXISTS monitor_runtime_state (key TEXT PRIMARY KEY,value_text TEXT,updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"))
        raw=db.execute(text("SELECT value_text FROM monitor_runtime_state WHERE key='telegram_scan_cooldown_until'" )).scalar()
        if raw:
            try:
                until=datetime.fromisoformat(str(raw).replace('Z','+00:00'))
                if until.tzinfo is None: until=until.replace(tzinfo=timezone.utc)
                if until>now:
                    print(f'TG_HASH_HYDRATE=SKIP cooldown until={until.isoformat()}')
                    return
            except Exception:
                pass
        active=int(db.execute(text("SELECT count(*) FROM monitor_sources WHERE platform='telegram' AND is_active=true")).scalar() or 0)
        hashed=int(db.execute(text("SELECT count(*) FROM monitor_sources WHERE platform='telegram' AND is_active=true AND COALESCE(payload->>'telegram_access_hash','')<>''")).scalar() or 0)
        if not active or hashed*100 >= active*80:
            print(f'TG_HASH_HYDRATE=OK coverage={hashed}/{active}')
            return
        last_raw=db.execute(text("SELECT value_text FROM monitor_runtime_state WHERE key='telegram_access_hash_hydration_at'" )).scalar()
        if last_raw:
            try:
                last=datetime.fromisoformat(str(last_raw).replace('Z','+00:00'))
                if last.tzinfo is None: last=last.replace(tzinfo=timezone.utc)
                if last>now-timedelta(hours=2):
                    print(f'TG_HASH_HYDRATE=SKIP recent coverage={hashed}/{active}')
                    return
            except Exception:
                pass
    try:
        rc,out,_sec=run('hydrate',['monitor_source_discovery.py','--apply','--dialogs','400','--messages','20'],600)
    except subprocess.TimeoutExpired:
        print('[hydrate] timeout')
        return
    if rc!=0 or 'COOLDOWN' in out or 'telegram_cooldown' in out:
        return
    with lock_engine.begin() as db:
        db.execute(text("""INSERT INTO monitor_runtime_state(key,value_text,updated_at)
          VALUES('telegram_access_hash_hydration_at',:v,NOW())
          ON CONFLICT(key) DO UPDATE SET value_text=EXCLUDED.value_text,updated_at=NOW()"""),{'v':now.isoformat()})
        active=int(db.execute(text("SELECT count(*) FROM monitor_sources WHERE platform='telegram' AND is_active=true")).scalar() or 0)
        hashed=int(db.execute(text("SELECT count(*) FROM monitor_sources WHERE platform='telegram' AND is_active=true AND COALESCE(payload->>'telegram_access_hash','')<>''")).scalar() or 0)
    print(f'TG_HASH_HYDRATE=PASS coverage={hashed}/{active}')


def run_cycle(run_id: str, started_monotonic: float, cycle_started_at: datetime, initial_stages: dict | None = None) -> int:
    results = []
    stages = dict(initial_stages or {})

    try:
        rank_rc, rank_out, sec = run('rank_sources', ['telegram_source_ranker.py'], 60)
        stages['rank_sources']=rank_rc; stages['rank_sources_sec']=sec
    except subprocess.TimeoutExpired:
        print('[rank_sources] timeout')
        stages['rank_sources']=124; stages['rank_sources_sec']=60

    # Fresh-demand discovery is part of the production sales cycle, not a
    # separate 3-hour side job. Run it before scanning so newly found public
    # buyer posts/sources can enter qualification in the same cycle. The
    # discovery script shares the Telegram advisory lock internally and is
    # invoked here before this process owns that lock (see main()).
    try:
        _r=run('scan', ['monitor_runner.py', '--scan', '--platform', 'telegram', '--limit', '80'], 720); results.append(_r)
        stages['scan']=_r[0]; stages['scan_sec']=_r[2]
        if _r[0]==0 and _scan_lock_busy(_r[1]):
            delegated_rc,wait_sec,delegated_info=wait_for_concurrent_scan(cycle_started_at)
            stages['scan']=delegated_rc
            stages['scan_sec']=_r[2]+wait_sec
            stages['scan_delegated']=1
            stages['scan_delegated_wait_sec']=wait_sec
            print(f'TG_SCAN_STAGE=DELEGATED rc={delegated_rc} info={delegated_info}')
    except subprocess.TimeoutExpired:
        print('[scan] timeout')
        results.append((124, 'timeout'))
        stages['scan']=124; stages['scan_sec']=720

    try:
        _r=run('queue', ['hot_lead_queue.py'], 120); results.append(_r); stages['queue']=_r[0]; stages['queue_sec']=_r[2]
    except subprocess.TimeoutExpired:
        print('[queue] timeout')
        results.append((124, 'timeout'))
        stages['queue']=124; stages['queue_sec']=120

    try:
        _r=run('replies', ['telegram_sales_replies.py'], 90); results.append(_r); stages['replies']=_r[0]; stages['replies_sec']=_r[2]
    except subprocess.TimeoutExpired:
        print('[replies] timeout')
        results.append((124, 'timeout'))
        stages['replies']=124; stages['replies_sec']=90
    stages['reply_pipeline_degraded']=1 if int(stages.get('replies',0) or 0)!=0 else 0

    # The current cycle itself is the candidate PASS, so its health stage must
    # not require an older completed cycle PASS. Standalone health/watchdog runs
    # still enforce historical freshness.
    _prev_health_in_cycle=os.environ.get('TG_SALES_HEALTH_IN_CYCLE')
    os.environ['TG_SALES_HEALTH_IN_CYCLE']='1'
    try:
        health_rc, health_out, sec = run('health', ['telegram_sales_health.py'], 60); stages['health']=health_rc; stages['health_sec']=sec
    except subprocess.TimeoutExpired:
        print('[health] timeout')
        health_rc, health_out = 124, 'timeout'
        stages['health']=124; stages['health_sec']=60
    finally:
        if _prev_health_in_cycle is None: os.environ.pop('TG_SALES_HEALTH_IN_CYCLE',None)
        else: os.environ['TG_SALES_HEALTH_IN_CYCLE']=_prev_health_in_cycle

    # A production PASS means the whole core chain completed. Telegram-side
    # transient failures are retried by the timer, but must still be visible as
    # DEGRADED instead of being hidden behind a successful sender step.
    # Reply ingestion is isolated from the fresh outbound contract. A slow
    # historical reply scan remains visible in details but cannot mark the
    # revenue-critical send cycle failed after scan/queue/send/health succeeded.
    critical_order=('scheduler_guard','discovery','identity_repair','rank_sources','scan','queue','sender','health')
    final_rc=0
    for stage in critical_order:
        rc=int(stages.get(stage,0) or 0)
        if rc!=0:
            final_rc=rc
            break
    status='pass' if final_rc == 0 else 'degraded'
    finish_run(run_id,status,started_monotonic,stages)
    if final_rc != 0:
        alert_cycle_failure(stages)
    print('TG_SALES_CYCLE=' + ('PASS' if final_rc == 0 else 'DEGRADED'))
    return 0 if final_rc == 0 else final_rc


def recent_pass_age_seconds() -> float | None:
    """Return age of the latest completed PASS, or None when none exists."""
    try:
        with lock_engine.connect() as db:
            ended=db.execute(text("select ended_at from telegram_sales_cycle_runs where status='pass' and ended_at is not null order by ended_at desc limit 1")).scalar()
        if not ended:
            return None
        if ended.tzinfo is None:
            ended=ended.replace(tzinfo=timezone.utc)
        return max(0.0,(datetime.now(timezone.utc)-ended).total_seconds())
    except Exception as e:
        print(f'TG_RECENT_PASS_GUARD=DEGRADED type={type(e).__name__}')
        return None


def should_skip_recent_pass() -> tuple[bool,float | None,int]:
    threshold=max(0,int(os.getenv('TG_SALES_CYCLE_RECENT_PASS_SKIP_SEC','300') or 300))
    if os.getenv('TG_SALES_CYCLE_FORCE','0')=='1' or threshold<=0:
        return False,None,threshold
    age=recent_pass_age_seconds()
    return bool(age is not None and age < threshold),age,threshold

def main() -> int:
    skip_recent,pass_age,skip_threshold=should_skip_recent_pass()
    if skip_recent:
        print(f'TG_SALES_CYCLE=SKIP recent_pass age_sec={int(pass_age or 0)} threshold_sec={skip_threshold}')
        return 0
    started_monotonic=time.monotonic()
    cycle_started_at=datetime.now(timezone.utc)
    run_id=start_run()
    initial_stages={}
    scheduler_rc, _scheduler_detail = scheduler_conflict_guard()
    initial_stages['scheduler_guard']=scheduler_rc

    # Revenue-critical fast lane: consume an already-qualified ready lead before
    # discovery/scan. Slow discovery refreshes the NEXT cycle but cannot delay
    # today's send quota.
    try:
        rc, out, sec = run('sender', ['hot_lead_auto_sender.py'], 120)
        initial_stages['sender']=rc; initial_stages['sender_sec']=sec
    except subprocess.TimeoutExpired:
        initial_stages['sender']=124; initial_stages['sender_sec']=120
        print('[sender] timeout')

    try:
        rc, out, sec = run('discovery', ['monitor_source_discovery.py', '--apply', '--dialogs', '0', '--messages', '0'], 600)
        initial_stages['discovery']=rc; initial_stages['discovery_sec']=sec
        if rc != 0:
            print('TG_FRESH_DISCOVERY=DEGRADED')
        else:
            print('TG_FRESH_DISCOVERY=PASS')
    except subprocess.TimeoutExpired:
        initial_stages['discovery']=124; initial_stages['discovery_sec']=600
        print('TG_FRESH_DISCOVERY=TIMEOUT')
    maybe_hydrate_source_hashes()
    try:
        rc, out, sec = run('identity_repair', ['telegram_author_identity_repair.py'], 180)
        initial_stages['identity_repair']=rc; initial_stages['identity_repair_sec']=sec
    except subprocess.TimeoutExpired:
        initial_stages['identity_repair']=124; initial_stages['identity_repair_sec']=180
        print('TG_AUTHOR_IDENTITY_REPAIR=TIMEOUT')
    with lock_engine.connect() as db:
        got=bool(db.execute(text('SELECT pg_try_advisory_lock(:k)'),{'k':LOCK_KEY}).scalar())
        db.commit()
        if not got:
            print('TG_SALES_CYCLE=SKIP telegram_session_busy')
            finish_run(run_id,'skipped_busy',started_monotonic,{'lock_busy':1})
            return 0
        try:
            return run_cycle(run_id,started_monotonic,cycle_started_at,initial_stages)
        finally:
            db.execute(text('SELECT pg_advisory_unlock(:k)'),{'k':LOCK_KEY})
            db.commit()


if __name__ == '__main__':
    raise SystemExit(main())
