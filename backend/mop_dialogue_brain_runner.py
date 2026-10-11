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
    _sanitize_question, _behavioral_question_tail, _norm_template, classify_question,
    classify_client_intent, _sanitize_response, _norm_response_template,
    response_style_features, response_pattern_safe,
)

NICHE_TITLE_HINTS = {
    "bytovki": ("бытов", "блок-контейнер", "вагончик"),
    "ipoteka_nedvizhimost": ("ипотек", "квартир", "недвиж", "новострой", "рефинанс", "кредит"),
    "plitka": ("плитк", "брусчат", "бордюр", "благоустр"),
    "mebel": ("мебел", "кухн", "шкаф", "гардероб", "прихож"),
    "steklo": (
        "стекл", "перегород", "зеркал", "окн", "пвх", "алюмини",
        "витраж", "лоджи", "балкон", "остеклен", "оконн", "рамы",
    ),
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
    """Rebuild account-private v3 dialogue profiles and semantic niche fallback.

    Human response/style learning excludes every outgoing text already recorded
    as a MOP send. Historical conversations are evidence, never business truth.
    """
    from app.mop_core import text_hash

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

        mop_hashes=defaultdict(set)
        try:
            for a,c,h in db.execute(text("""
                SELECT account_id,avito_chat_id,outgoing_text_hash
                  FROM mop_drafts
                 WHERE outgoing_text_hash IS NOT NULL
                   AND outgoing_text_hash<>''
            """)).all():
                mop_hashes[(str(a),str(c))].add(str(h))
        except Exception:
            db.rollback()
            mop_hashes=defaultdict(set)

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
            aid,cid=str(a),str(c)
            direction=str(d or "").lower()
            body=str(t or "")
            generated=False
            if direction.startswith("out") and body.strip():
                try:
                    generated=text_hash(body) in mop_hashes.get((aid,cid),set())
                except Exception:
                    generated=False
            by_account[aid][cid].append({
                "direction":direction,
                "text":body,
                "ts":int(ts or 0),
                "id":int(i),
                "item_title":str(item_title or ""),
                "mop_generated":bool(generated),
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
            response_stats=defaultdict(lambda:{
                "support":0,"continued":0,"outcome_sum":0.0,"outcome_n":0,
                "intent":"","label":"","examples":Counter(),
                "question_count":0,"cta_count":0,"handoff_count":0,
            })
            handoff_stats=defaultdict(lambda:{
                "support":0,"outcome_sum":0.0,"outcome_n":0,"label":""
            })
            intent_stats=defaultdict(lambda:{
                "support":0,"chars":[],"questions":0,"cta":0,"handoff":0,
                "outcome_sum":0.0,"outcome_n":0,"label":"",
            })
            style_samples=[]
            tag_positions=defaultdict(list)
            tag_support=Counter()
            transitions=Counter()
            total_msgs=0
            human_replies=0
            mop_replies_excluded=0

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
                human_out_index=0

                for idx,m in enumerate(seq):
                    if not m["direction"].startswith("out"):
                        continue
                    if m.get("mop_generated"):
                        mop_replies_excluded += 1
                        continue

                    # Human-only learning source.
                    human_replies += 1
                    human_out_index += 1
                    raw_reply=str(m.get("text") or "")
                    safe_reply=_sanitize_response(raw_reply)
                    features=response_style_features(raw_reply)
                    if safe_reply:
                        style_samples.append({
                            **features,
                            "first_turn": human_out_index == 1,
                        })

                    prev_client=""
                    for previous in reversed(seq[:idx]):
                        if previous["direction"].startswith("in") and str(previous.get("text") or "").strip():
                            prev_client=str(previous.get("text") or "").strip()
                            break
                    if safe_reply and prev_client:
                        intent,label=classify_client_intent(prev_client)
                        oscore=outcome.get((account_id,chat_id))

                        ips=intent_stats[intent]
                        ips["support"] += 1
                        ips["label"] = label
                        ips["chars"].append(int(features.get("chars") or 0))
                        ips["questions"] += int(bool(features.get("questions")))
                        ips["cta"] += int(bool(features.get("cta")))
                        ips["handoff"] += int(bool(features.get("handoff")))
                        if oscore is not None:
                            ips["outcome_sum"] += oscore
                            ips["outcome_n"] += 1

                        # Handoff learning may use the semantic event even when
                        # the literal reply contains dynamic facts and is unsafe
                        # to persist as a response template.
                        if features.get("handoff"):
                            hs=handoff_stats[intent]
                            hs["support"] += 1
                            hs["label"] = label
                            if oscore is not None:
                                hs["outcome_sum"] += oscore
                                hs["outcome_n"] += 1

                        # Literal examples must be behavior-only. Dynamic prices,
                        # contacts, availability, schedules and offers stay out.
                        if response_pattern_safe(safe_reply):
                            rkey=intent+"|"+_norm_response_template(safe_reply)
                            if rkey.strip("|"):
                                continued=False
                                for later in seq[idx+1:]:
                                    if later["direction"].startswith("out"):
                                        break
                                    if later["direction"].startswith("in") and str(later.get("text") or "").strip():
                                        continued=True
                                        break
                                rs=response_stats[rkey]
                                rs["support"] += 1
                                rs["continued"] += int(continued)
                                rs["intent"],rs["label"]=intent,label
                                rs["examples"][safe_reply] += 1
                                rs["question_count"] += int(features.get("questions") or 0)
                                rs["cta_count"] += int(bool(features.get("cta")))
                                rs["handoff_count"] += int(bool(features.get("handoff")))
                                if oscore is not None:
                                    rs["outcome_sum"] += oscore
                                    rs["outcome_n"] += 1

                    # Human question/qualification learning.
                    q=_behavioral_question_tail(_sanitize_question(raw_reply))
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
                    for prev,nxt in zip(qturns,qturns[1:]):
                        if prev[1] != nxt[1]:
                            transitions[(prev[1],nxt[1])] += 1

            # Explicit account settings remain a trusted behavioral baseline.
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
                -(x["avg_crm_outcome"] if x["avg_crm_outcome"] is not None else 35),
                -(x["response_rate"] or 0),
                -(x["support"] or 0),
            ))

            response_patterns=[]
            for _,st in response_stats.items():
                example=st["examples"].most_common(1)[0][0] if st["examples"] else ""
                support=int(st["support"] or 0)
                if not example or support<=0:
                    continue
                continue_rate=100.0*st["continued"]/support
                avg_outcome=st["outcome_sum"]/st["outcome_n"] if st["outcome_n"] else None
                # Prefer patterns that actually kept/moved the dialogue forward.
                if avg_outcome is not None and avg_outcome < 25:
                    continue
                if avg_outcome is None and continue_rate < 50:
                    continue
                # Singletons are useful only with a strong downstream outcome;
                # repeated human behavior remains the preferred learning signal.
                if support<2 and not (
                    avg_outcome is not None and avg_outcome>=70 and continue_rate>=50
                ):
                    continue
                quality=(
                    (avg_outcome if avg_outcome is not None else 35.0)
                    + min(18.0, support*3.0)
                    + min(20.0, continue_rate*0.20)
                )
                response_patterns.append({
                    "intent":st["intent"],
                    "label":st["label"],
                    "response":example,
                    "support":support,
                    "client_continue_rate":round(continue_rate,1),
                    "avg_crm_outcome":round(avg_outcome,1) if avg_outcome is not None else None,
                    "question_rate":round(100.0*st["question_count"]/support,1),
                    "cta_rate":round(100.0*st["cta_count"]/support,1),
                    "handoff_rate":round(100.0*st["handoff_count"]/support,1),
                    "quality_score":round(quality,1),
                })
            response_patterns.sort(key=lambda x:(
                -float(x.get("quality_score") or 0),
                -int(x.get("support") or 0),
                str(x.get("intent") or ""),
            ))

            intent_profiles=[]
            for intent,ips in intent_stats.items():
                support=int(ips["support"] or 0)
                if support<2:
                    continue
                avg_outcome=ips["outcome_sum"]/ips["outcome_n"] if ips["outcome_n"] else None
                if avg_outcome is not None and avg_outcome < 20:
                    continue
                med_chars=float(median([x for x in ips["chars"] if x>0])) if any(x>0 for x in ips["chars"]) else 0.0
                intent_profiles.append({
                    "intent":intent,
                    "label":ips["label"] or intent,
                    "support":support,
                    "preferred_length":"short" if med_chars<=120 else ("normal" if med_chars<=260 else "detailed"),
                    "median_chars":round(med_chars),
                    "question_rate_pct":round(100.0*ips["questions"]/support),
                    "cta_rate_pct":round(100.0*ips["cta"]/support),
                    "handoff_rate_pct":round(100.0*ips["handoff"]/support),
                    "avg_crm_outcome":round(avg_outcome,1) if avg_outcome is not None else None,
                })
            intent_profiles.sort(key=lambda x:(
                -(x["avg_crm_outcome"] if x["avg_crm_outcome"] is not None else 35),
                -int(x.get("support") or 0),
            ))

            handoff_patterns=[]
            for intent,hs in handoff_stats.items():
                support=int(hs["support"] or 0)
                if support<=0:
                    continue
                avg_outcome=hs["outcome_sum"]/hs["outcome_n"] if hs["outcome_n"] else None
                if avg_outcome is not None and avg_outcome < 25:
                    continue
                handoff_patterns.append({
                    "intent":intent,
                    "label":hs["label"] or intent,
                    "support":support,
                    "avg_crm_outcome":round(avg_outcome,1) if avg_outcome is not None else None,
                })
            handoff_patterns.sort(key=lambda x:(
                -(x["avg_crm_outcome"] if x["avg_crm_outcome"] is not None else 35),
                -int(x.get("support") or 0),
            ))

            style_profile={}
            if style_samples:
                chars=[int(x.get("chars") or 0) for x in style_samples if int(x.get("chars") or 0)>0]
                sentences=[int(x.get("sentences") or 0) for x in style_samples if int(x.get("sentences") or 0)>0]
                med_chars=float(median(chars)) if chars else 0.0
                def pct(key, subset=None):
                    vals=subset if subset is not None else style_samples
                    if not vals:
                        return 0
                    return round(100.0*sum(1 for x in vals if bool(x.get(key)))/len(vals))
                first=[x for x in style_samples if x.get("first_turn")]
                follow=[x for x in style_samples if not x.get("first_turn")]
                style_profile={
                    "preferred_length":"short" if med_chars<=120 else ("normal" if med_chars<=260 else "detailed"),
                    "median_chars":round(med_chars),
                    "typical_sentences":max(1,round(float(median(sentences)))) if sentences else 1,
                    "question_rate_pct":pct("questions"),
                    "cta_rate_pct":pct("cta"),
                    "handoff_rate_pct":pct("handoff"),
                    "first_turn_greeting_rate_pct":pct("greeting",first),
                    "followup_greeting_rate_pct":pct("greeting",follow),
                    "sample_size":len(style_samples),
                }

            tags=set(tag_support)
            labels={classify_question(t["question"])[0]:classify_question(t["question"])[1] for t in templates}
            seq_scores=[]
            for tag in tags:
                pos=tag_positions.get(tag) or []
                cfg_pos=next((i+1 for i,x in enumerate(configured) if x["tag"]==tag),None)
                med=float(median(pos)) if pos else float(cfg_pos or 99)
                support=int(tag_support.get(tag,0))
                seq_scores.append((
                    0 if cfg_pos else 1,
                    med,
                    -support,
                    tag,
                    labels.get(tag) or next((x["label"] for x in configured if x["tag"]==tag),"Этап"),
                ))
            seq_scores.sort()
            recommended=[
                {"tag":tag,"label":label,"support":int(tag_support.get(tag,0))}
                for _,_,_,tag,label in seq_scores[:12]
            ]

            payload={
                "version":"mop_account_dialogue_brain_v3",
                "account_id":account_id,
                "niche_key":nk,
                "summary":{
                    "messages":total_msgs,
                    "chats":eligible_chats,
                    "all_chats_seen":len(chats),
                    "irrelevant_chats_skipped":skipped_irrelevant_chats,
                    "questions_seen":sum(x["support"] for x in templates),
                    "human_replies":human_replies,
                    "mop_replies_excluded":mop_replies_excluded,
                },
                "recommended_sequence":recommended,
                "question_templates":templates[:30],
                "configured_questions":configured,
                "response_patterns":response_patterns[:40],
                "intent_profiles":intent_profiles[:24],
                "handoff_patterns":handoff_patterns[:20],
                "style_profile":style_profile,
                "transitions":[
                    {"from":a,"to":b,"support":n}
                    for (a,b),n in transitions.most_common(30)
                    if n>=2
                ],
                "privacy":"account_private_human_only_sanitized_patterns",
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
            """),{
                "a":account_id,
                "m":total_msgs,
                "c":len(chats),
                "p":json.dumps(payload,ensure_ascii=False),
            })
            if nk:
                niche_accounts[nk].append(payload)

        # Cross-account niche fallback contains semantic stages only.
        db.execute(text(f"DELETE FROM {NICHE_TABLE}"))
        niche_count=0
        for nk,payloads in niche_accounts.items():
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
                label=next((
                    x.get("label")
                    for p in payloads
                    for x in (p.get("recommended_sequence") or [])
                    if x.get("tag")==tag
                ),tag)
                rows.append({
                    "tag":tag,
                    "label":label,
                    "accounts":n,
                    "median_position":round(float(median(stage_position[tag])),1),
                })
            rows.sort(key=lambda x:(x["median_position"],-x["accounts"],x["tag"]))
            np={
                "version":"mop_niche_dialogue_brain_v1",
                "niche_key":nk,
                "accounts":len(payloads),
                "recommended_sequence":rows[:12],
                "privacy":"semantic_aggregates_only_min_2_accounts_no_literal_responses",
            }
            db.execute(text(f"""
                INSERT INTO {NICHE_TABLE}(niche_key,generated_at,account_count,payload)
                VALUES(:k,now(),:n,CAST(:p AS jsonb))
            """),{"k":nk,"n":len(payloads),"p":json.dumps(np,ensure_ascii=False)})
            niche_count+=1

        db.commit()
        summary={
            "status":"pass",
            "accounts":len(account_payloads),
            "accounts_with_history":sum(
                1 for p in account_payloads.values()
                if (p.get("summary") or {}).get("messages",0)>0
            ),
            "total_messages":sum(
                (p.get("summary") or {}).get("messages",0)
                for p in account_payloads.values()
            ),
            "human_replies":sum(
                (p.get("summary") or {}).get("human_replies",0)
                for p in account_payloads.values()
            ),
            "mop_replies_excluded":sum(
                (p.get("summary") or {}).get("mop_replies_excluded",0)
                for p in account_payloads.values()
            ),
            "niche_brains":niche_count,
        }
        print("MOP_DIALOGUE_BRAIN=PASS")
        print("ACCOUNTS=",summary["accounts"])
        print("ACCOUNTS_WITH_HISTORY=",summary["accounts_with_history"])
        print("TOTAL_MESSAGES=",summary["total_messages"])
        print("HUMAN_REPLIES=",summary["human_replies"])
        print("MOP_REPLIES_EXCLUDED=",summary["mop_replies_excluded"])
        print("NICHE_BRAINS=",niche_count)
        for aid,payload in sorted(
            account_payloads.items(),
            key=lambda kv:-(kv[1].get("summary") or {}).get("messages",0),
        )[:12]:
            ss=payload["summary"]
            print(
                "ACCOUNT",aid,
                "messages=",ss["messages"],
                "chats=",ss["chats"],
                "human=",ss["human_replies"],
                "mop_excluded=",ss["mop_replies_excluded"],
                "responses=",len(payload["response_patterns"]),
                "templates=",len(payload["question_templates"]),
                "niche=",payload["niche_key"],
            )
        return summary
    finally:
        db.close()

if __name__=="__main__":
    build()
