import json,time
from datetime import datetime,timezone
from app.api.avito import _kpi_fetch_live_item,kpi_goal_tick

ACC='planetazayavki_65985'
ITEM='8200446429'
EXPECTED='Гардеробная по индивидуальным размерам Ижевск'
LOG='/root/BORIS/backend/.boris_logs/planeta_recovery_watch_v2_20260903.log'

def emit(kind,**data):
    rec={'ts':datetime.now(timezone.utc).isoformat(),'kind':kind,**data}
    line=json.dumps(rec,ensure_ascii=False,default=str)
    print(line,flush=True)
    with open(LOG,'a',encoding='utf-8') as f:
        f.write(line+'\n')

emit('watch_start',account_id=ACC,item_id=ITEM,expected_title=EXPECTED,cadence_sec=180,max_minutes=45)
for n in range(1,17):
    try:
        live=_kpi_fetch_live_item(ACC,ITEM) or {}
        item=live.get('item') or {}
        title=str(item.get('title') or item.get('Title') or '')
        emit('live_check',iteration=n,status=live.get('status'),title=title)
        if live.get('status')=='ok' and title.strip()==EXPECTED:
            time.sleep(8)
            tick=kpi_goal_tick(ACC,allow_stale_non_money=True)
            emit('confirm_tick',iteration=n,tick=tick)
            time.sleep(5)
            after=kpi_goal_tick(ACC,allow_stale_non_money=True)
            emit('post_confirm_tick',iteration=n,tick=after)
            emit('watch_pass',iteration=n,title=title)
            raise SystemExit(0)
    except SystemExit:
        raise
    except Exception as exc:
        emit('watch_iteration_error',iteration=n,error=f'{type(exc).__name__}: {str(exc)[:500]}')
    if n<16:
        time.sleep(180)
emit('watch_pending',reason='Avito scheduled URL pull not externally confirmed within 45m; hourly self-heal remains active')
