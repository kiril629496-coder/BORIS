#!/usr/bin/env python3
"""Build per-account and privacy-safe niche MOP dialogue playbooks."""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from statistics import median

from sqlalchemy import text
from app.db.session import SessionLocal
from app.services.mop_dialogue_brain import (
    ACCOUNT_TABLE, NICHE_TABLE, ensure_tables, niche_key,
    _sanitize_question, _norm_template, classify_question,
)

NICHE_TITLE_HINTS = {
    "bytovki": ("бытов", "блок-контейнер", "вагончик"),
    "ipoteka_nedvizhimost": ("ипотек", "квартир", "недвиж", "новострой", "рефинанс", "кредит"),
    "plitka": ("плитк", "брусчат", "бордюр", "благоустр"),
    "mebel": ("мебел", "кухн", "шкаф", "гардероб", "прихож"),
    "steklo": ("стекл", "перегород", "зеркал"),
    "shtukaturka": ("штукатур", "отделк"),
    "beton_stroitelstvo": ("бетон", "фундамент", "монолит", "жби"),
    "almaznoe": ("алмазн", "бурен", "резк"),
    "evakuatory": ("эвакуатор", "эвакуац"),
    "avto": ("camry", "авто", "автомобил", "машин", "выкуп"),
    "dveri": ("двер",),
    "angary": ("ангар", "ферм", "металлоконструк"),
    "nerudnye": ("песок", "щебен", "грунт"),
    "water_gear": ("гидрокост", "вейкборд", "аквашуз", "спасательн", "экипировк", "водн"),
    "electric_bikes": ("электровелосип", "электросамокат", "венбокс", "электротранспорт"),
    "building_materials": ("строительн", "стройматериал", "материал"),
    "marketing": ("маркетинг", "авито", "продвижен"),
}

def _chat_matches_niche(nk: str, titles: list[str], crm_linked: bool) -> bool:
    if crm_linked:
        return True
    if not nk:
        return True
    clean=[str(x or "").lower().replace("ё","е") for x in titles if str(x or "").strip()]
    if not clean:
        return True
    hints=NICHE_TITLE_HINTS.get(nk) or ()
    if not hints:
        return True
    return any(any(h in title for h in hints) for title in clean)

def _cfg(raw):
    try:
        x=json.loads(raw or "{}")
        return x if isinstance(x,dict) else {}
    except Exception:
        return {}

def _humanize_configured(value: str) -> str:
    q=" ".join(str(value or "").split()).strip()
    if not q:
        return ""
    low=q.lower().replace("ё","е")
    mapping={
        "район или метро":"Какой район или метро рассматриваете?",
        "район/метро":"Какой район или метро рассматриваете?",
        "новостройка или вторичный рынок":"Рассматриваете новостройку или вторичный рынок?",
        "новостройка/вторичный рынок":"Рассматриваете новостройку или вторичный рынок?",
        "комнатность":"Сколько комнат рассматриваете?",
        "сколько комнат":"Сколько комнат рассматриваете?",
        "бюджет":"Какой бюджет рассматриваете?",
        "наличные или ипотека":"Покупка планируется за наличные или с ипотекой?",
        "способ оплаты: наличные или ипотека":"Покупка планируется за наличные или с ипотекой?",
        "аренда или покупка":"Нужна аренда или покупка?",
        "адрес объекта/доставки":"Подскажите адрес объекта или доставки?",
        "срок, к которому нужна бытовка":"К какому сроку нужна бытовка?",
        "на какой срок нужна бытовка":"На какой срок нужна бытовка?",
        "нужный размер бытовки":"Какой размер бытовки нужен?",
    }
    if low in mapping:
        return mapping[low]
    if "мягко выяснить задачу" in low:
        return "Что именно хотите решить?"
    if "предложить" in low or "получить номер" in low:
        return ""
    if q.endswith("?"):
        return q
    if re.match(r"^(какой|какая|какие|какое|где|когда|что|сколько|есть ли|нужен ли|нужна ли)\b",low):
        return q.rstrip(".")+"?"
    return ""

def _chat_outcome_map(db):
    result={}
    try:
        rows=db.execute(text("""
            SELECT ca.account_id,ca.chat_id,max(l.outcome_score)
            FROM chat_analysis ca
            JOIN rop_outcome_links l
              ON l.analysis_kind='chat' AND l.analysis_id=ca.id
            GROUP BY ca.account_id,ca.chat_id
        """)).all()
        for a,c,s in rows:
            result[(str(a),str(c))]=float(s or 0)
    except Exception:
        db.rollback()
    return result

def build():
    db=SessionLocal()
    try:
        ensure_tables(db)
        accounts=db.execute(text("""
            SELECT a.account_id,coalesce(a.name,''),coalesce(a.company_niche,''),
                   coalesce(a.company_description,''),
                   (SELECT value FROM storage s
                    WHERE s.account_id=a.account_id AND s.key='mop_crm_sales_settings'
                    ORDER BY s.id DESC LIMIT 1) settings
            FROM accounts a
            ORDER BY a.account_id
        """)).all()
        outcome=_chat_outcome_map(db)

        messages=db.execute(text("""
            SELECT account_id,avito_chat_id,direction,coalesce(text,''),avito_created_at,id,
                   coalesce(item_title,'')
            FROM messenger_messages
            WHERE coalesce(msg_type,'text')<>'system'
              AND coalesce(text,'')<>''
            ORDER BY account_id,avito_chat_id,avito_created_at,id
        """)).all()
        by_account=defaultdict(lambda: defaultdict(list))
        for a,c,d,t,ts,i,item_title in messages:
            by_account[str(a)][str(c)].append({
                "direction":str(d or "").lower(),
                "text":str(t or ""),
                "ts":int(ts or 0),
                "id":int(i),
                "item_title":str(item_title or ""),
            })

        account_payloads={}
        niche_accounts=defaultdict(list)

        for account_id,name,niche,desc,settings_raw in accounts:
            account_id=str(account_id)
            chats=by_account.get(account_id,{})
            cfg=_cfg(settings_raw)
            nk=niche_key(name,niche,desc)
            template_stats=defaultdict(lambda:{
                "support":0,"responses":0,"outcome_sum":0.0,"outcome_n":0,
                "positions":[],"tag":"","label":"","examples":Counter()
            })
            tag_positions=defaultdict(list)
            tag_support=Counter()
            transitions=Counter()
            first_tags=Counter()
            total_msgs=0

            eligible_chats=0
            skipped_irrelevant_chats=0
            for chat_id,seq in chats.items():
                titles=[m.get("item_title") or "" for m in seq]
                if not _chat_matches_niche(
                    nk,
                    titles,
                    (account_id,chat_id) in outcome,
                ):
                    skipped_irrelevant_chats += 1
                    continue
                eligible_chats += 1
                total_msgs += len(seq)
                qturns=[]
                for idx,m in enumerate(seq):
                    if not m["direction"].startswith("out"):
                        continue
                    q=_sanitize_question(m["text"])
                    if not q:
                        continue
                    key=_norm_template(q)
                    if not key:
                        continue
                    tag,label=classify_question(q)
                    replied=False
                    for later in seq[idx+1:]:
                        if later["direction"].startswith("out"):
                            break
                        if later["direction"].startswith("in") and later["text"].strip():
                            replied=True
                            break
                    st=template_stats[key]
                    st["support"] += 1
                    st["responses"] += int(replied)
                    st["tag"],st["label"]=tag,label
                    st["examples"][q] += 1
                    st["positions"].append(len(qturns)+1)
                    oscore=outcome.get((account_id,chat_id))
                    if oscore is not None:
                        st["outcome_sum"] += oscore
                        st["outcome_n"] += 1
                    qturns.append((key,tag,label))
                    tag_positions[tag].append(len(qturns))
                    tag_support[tag]+=1
                if qturns:
                    first_tags[qturns[0][1]] += 1
                    for prev,nxt in zip(qturns,qturns[1:]):
                        if prev[1] != nxt[1]:
                            transitions[(prev[1],nxt[1])] += 1

            # Seed with explicit account settings so accounts without history
            # still get an account-specific playbook.
            configured=[]
            for rawq in cfg.get("required_questions") or []:
                q=_humanize_configured(rawq)
                if not q:
                    continue
                key=_norm_template(q)
                tag,label=classify_question(q)
                configured.append({"question":q,"tag":tag,"label":label})
                if key not in template_stats:
                    st=template_stats[key]
                    st["tag"],st["label"]=tag,label
                    st["examples"][q]+=1
                    st["positions"].append(len(configured))
                tag_support[tag]+=0

            templates=[]
            for key,st in template_stats.items():
                q=st["examples"].most_common(1)[0][0] if st["examples"] else ""
                if not q:
                    continue
                support=int(st["support"])
                configured_match=any(_norm_template(x["question"])==key for x in configured)
                if support<2 and not configured_match:
                    continue
                response_rate=100.0*st["responses"]/support if support else None
                avg_outcome=st["outcome_sum"]/st["outcome_n"] if st["outcome_n"] else None
                templates.append({
                    "question":q,
                    "tag":st["tag"],
                    "label":st["label"],
                    "support":support,
                    "configured":configured_match,
                    "response_rate":round(response_rate,1) if response_rate is not None else None,
                    "avg_crm_outcome":round(avg_outcome,1) if avg_outcome is not None else None,
                    "median_position":round(float(median(st["positions"])),1) if st["positions"] else None,
                })
            templates.sort(key=lambda x:(
                not x["configured"],
                -(x["support"] or 0),
                -(x["response_rate"] or 0),
                -(x["avg_crm_outcome"] or 0),
            ))

            tags=set(tag_support)
            labels={classify_question(t["question"])[0]:classify_question(t["question"])[1] for t in templates}
            seq_scores=[]
            for tag in tags:
                pos=tag_positions.get(tag) or []
                cfg_pos=next((i+1 for i,x in enumerate(configured) if x["tag"]==tag),None)
                med=float(median(pos)) if pos else float(cfg_pos or 99)
                support=int(tag_support.get(tag,0))
                seq_scores.append((0 if cfg_pos else 1, med, -support, tag, labels.get(tag) or next((x["label"] for x in configured if x["tag"]==tag),"Этап")))
            seq_scores.sort()
            recommended=[{"tag":tag,"label":label,"support":int(tag_support.get(tag,0))} for _,_,_,tag,label in seq_scores[:12]]

            payload={
                "version":"mop_account_dialogue_brain_v2",
                "account_id":account_id,
                "niche_key":nk,
                "summary":{
                    "messages":total_msgs,
                    "chats":eligible_chats,
                    "all_chats_seen":len(chats),
                    "irrelevant_chats_skipped":skipped_irrelevant_chats,
                    "questions_seen":sum(x["support"] for x in templates),
                },
                "recommended_sequence":recommended,
                "question_templates":templates[:30],
                "configured_questions":configured,
                "transitions":[
                    {"from":a,"to":b,"support":n}
                    for (a,b),n in transitions.most_common(30)
                    if n>=2
                ],
                "privacy":"account_private_sanitized_templates",
            }
            account_payloads[account_id]=payload
            db.execute(text(f"""
                INSERT INTO {ACCOUNT_TABLE}(account_id,generated_at,message_count,chat_count,payload)
                VALUES(:a,now(),:m,:c,CAST(:p AS jsonb))
                ON CONFLICT (account_id) DO UPDATE SET
                  generated_at=EXCLUDED.generated_at,
                  message_count=EXCLUDED.message_count,
                  chat_count=EXCLUDED.chat_count,
                  payload=EXCLUDED.payload
            """),{"a":account_id,"m":total_msgs,"c":len(chats),"p":json.dumps(payload,ensure_ascii=False)})
            if nk:
                niche_accounts[nk].append(payload)

        db.execute(text(f"DELETE FROM {NICHE_TABLE}"))
        niche_count=0
        for nk,payloads in niche_accounts.items():
            # Require >=2 independent accounts. Aggregate only semantic stages.
            if len(payloads)<2:
                continue
            stage_account_support=Counter()
            stage_position=defaultdict(list)
            for p in payloads:
                seen=set()
                for idx,x in enumerate(p.get("recommended_sequence") or [],1):
                    tag=str(x.get("tag") or "")
                    if not tag:
                        continue
                    stage_position[tag].append(idx)
                    seen.add(tag)
                for tag in seen:
                    stage_account_support[tag]+=1
            rows=[]
            for tag,n in stage_account_support.items():
                if n<2:
                    continue
                label=next((x.get("label") for p in payloads for x in (p.get("recommended_sequence") or []) if x.get("tag")==tag),tag)
                rows.append({
                    "tag":tag,"label":label,
                    "accounts":n,
                    "median_position":round(float(median(stage_position[tag])),1),
                })
            rows.sort(key=lambda x:(x["median_position"],-x["accounts"],x["tag"]))
            np={
                "version":"mop_niche_dialogue_brain_v1",
                "niche_key":nk,
                "accounts":len(payloads),
                "recommended_sequence":rows[:12],
                "privacy":"semantic_aggregates_only_min_2_accounts",
            }
            db.execute(text(f"""
                INSERT INTO {NICHE_TABLE}(niche_key,generated_at,account_count,payload)
                VALUES(:k,now(),:n,CAST(:p AS jsonb))
            """),{"k":nk,"n":len(payloads),"p":json.dumps(np,ensure_ascii=False)})
            niche_count+=1

        db.commit()
        print("MOP_DIALOGUE_BRAIN=PASS")
        print("ACCOUNTS=",len(account_payloads))
        print("ACCOUNTS_WITH_HISTORY=",sum(1 for p in account_payloads.values() if (p.get("summary") or {}).get("messages",0)>0))
        print("TOTAL_MESSAGES=",sum((p.get("summary") or {}).get("messages",0) for p in account_payloads.values()))
        print("NICHE_BRAINS=",niche_count)
        for aid,p in sorted(account_payloads.items(),key=lambda kv:-(kv[1].get("summary") or {}).get("messages",0))[:12]:
            s=p["summary"]
            print("ACCOUNT",aid,"messages=",s["messages"],"chats=",s["chats"],"templates=",len(p["question_templates"]),"niche=",p["niche_key"])
    finally:
        db.close()

if __name__=="__main__":
    build()
