from pathlib import Path
import json
from app.api.mop_combat_training import RealDialogLearning, real_dialog_learning

root=Path('/root/BORIS/backend/data/mop_combat_training')
updated=0
facts=[]
for p in root.glob('*.json'):
    try:
        rows=json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        continue
    changed=False
    for row in rows:
        if row.get('kind')!='real_dialog_feedback' or row.get('proposal_status')=='remembered':
            continue
        proposal=str(row.get('proposal') or '').strip()
        comment=str(row.get('comment') or '').strip()
        chat=str(row.get('avito_chat_id') or '').strip()
        account=str(row.get('account_id') or p.stem).strip()
        if not proposal or len(comment)<8 or not chat:
            continue
        res=real_dialog_learning(RealDialogLearning(account_id=account,avito_chat_id=chat,score=int(row.get('score') or 3),comment=comment,rule_text=proposal))
        row['proposal_status']='remembered'
        row['fact_id']=res.get('fact_id')
        row['memory_store']='client_facts'
        changed=True; updated+=1; facts.append((account,chat,res.get('fact_id')))
    if changed:
        p.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
print('BACKFILLED',updated)
for x in facts: print(*x)
