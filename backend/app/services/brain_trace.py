"""BORIS Brain cross-module business trace projection.

Builds a deterministic correlation id for an Avito conversation and reconstructs
the factual chain Messages -> MOP -> CRM -> Telephony -> ROP from canonical DB
records. No synthetic PASS is emitted for missing stages.
"""
from __future__ import annotations

import hashlib
from typing import Any
from sqlalchemy import text


def trace_id(account_id: str, chat_id: str) -> str:
    raw=f"avito:{account_id}:{chat_id}".encode("utf-8")
    return "tr_"+hashlib.sha256(raw).hexdigest()[:24]


def _rows(db, sql: str, params: dict) -> list[dict]:
    return [dict(r._mapping) for r in db.execute(text(sql),params).fetchall()]


def build_trace(db, account_id: str, chat_id: str) -> dict[str, Any]:
    p={"a":str(account_id),"c":str(chat_id)}
    messages=_rows(db,"""SELECT id,avito_message_id,direction,msg_type,content_type,text,avito_created_at,stored_at,is_read,read_at
      FROM messenger_messages WHERE account_id=:a AND avito_chat_id=:c ORDER BY COALESCE(avito_created_at,0),id""",p)
    drafts=_rows(db,"""SELECT id,avito_message_id,status,reply_author,sent_at,send_error,lead_id,created_at,updated_at
      FROM mop_drafts WHERE account_id=:a AND avito_chat_id=:c ORDER BY id""",p)
    deals=_rows(db,"""SELECT id,contact_id,status,stage_id,next_action_at,created_at,updated_at,avito_item_id,avito_chat_id
      FROM boris_crm_deals WHERE avito_account_id=:a AND avito_chat_id=:c ORDER BY created_at,id""",p)
    deal_ids=[int(x["id"]) for x in deals if x.get("id") is not None]
    calls=[];quality=[];tasks=[]
    if deal_ids:
        q={"ids":tuple(deal_ids)}
        calls=_rows(db,"""SELECT id,account_id,provider_call_id,direction,state,started_at,answered_at,ended_at,crm_contact_id,crm_deal_id,
          recording_status,transcription_status,ai_analysis_status,qualification,next_action,callback_due_at,callback_status
          FROM telephony_calls WHERE crm_deal_id IN :ids ORDER BY started_at,id""",q)
        tasks=_rows(db,"""SELECT id,deal_id,contact_id,title,due_at,status,source,created_at,completed_at
          FROM boris_crm_tasks WHERE deal_id IN :ids ORDER BY created_at,id""",q)
        call_ids=[str(x["id"]) for x in calls if x.get("id")]
        if call_ids:
            quality=_rows(db,"""SELECT id,call_id,total_score,status,evaluator,strengths_json,gaps_json,created_at
              FROM telephony_call_quality WHERE call_id IN :ids ORDER BY created_at,id""",{"ids":tuple(call_ids)})
    inbound=sum(1 for x in messages if str(x.get("direction") or "").lower().startswith("in"))
    outbound=sum(1 for x in messages if str(x.get("direction") or "").lower().startswith("out"))
    mop_sent=sum(1 for x in drafts if x.get("status")=="sent")
    stages={
        "messages":{"present":bool(messages),"inbound":inbound,"outbound":outbound,"count":len(messages)},
        "mop":{"present":bool(drafts),"sent":mop_sent,"unresolved":sum(1 for x in drafts if x.get("status") not in ("sent","deleted","no_reply_required")),"count":len(drafts)},
        "crm":{"present":bool(deals),"deals":len(deals),"tasks":len(tasks),"open_without_next_action":sum(1 for x in deals if x.get("status")=="open" and x.get("next_action_at") is None)},
        "telephony":{"present":bool(calls),"calls":len(calls),"without_crm_link":sum(1 for x in calls if x.get("crm_deal_id") is None)},
        "rop":{"present":bool(quality),"quality_audits":len(quality),"calls_without_quality":max(0,len(calls)-len({str(x.get('call_id')) for x in quality}))},
    }
    breaks=[]
    if inbound and not drafts and outbound==0: breaks.append("MESSAGES_TO_MOP")
    if drafts and not deals: breaks.append("MOP_TO_CRM")
    if calls and not quality: breaks.append("TELEPHONY_TO_ROP")
    if deals and any(x.get("status")=="open" and x.get("next_action_at") is None for x in deals): breaks.append("CRM_NEXT_ACTION")
    return {
        "trace_id":trace_id(account_id,chat_id),
        "account_id":str(account_id),
        "chat_id":str(chat_id),
        "status":"degraded" if breaks else ("pass" if messages else "not_found"),
        "breaks":breaks,
        "stages":stages,
        "events":{"messages":messages,"mop_drafts":drafts,"crm_deals":deals,"crm_tasks":tasks,"telephony_calls":calls,"rop_quality":quality},
    }


def recent_traces(db, account_id: str | None=None, hours: int=24, limit: int=100) -> list[dict[str,Any]]:
    h=max(1,min(int(hours),720));lim=max(1,min(int(limit),500))
    p={"a":str(account_id or ""),"h":h,"lim":lim}
    rows=_rows(db,"""SELECT account_id,avito_chat_id,max(stored_at) AS last_at
      FROM messenger_messages WHERE (:a='' OR account_id=:a) AND stored_at>=now()-(:h||' hours')::interval
      GROUP BY account_id,avito_chat_id ORDER BY last_at DESC LIMIT :lim""",p)
    out=[]
    for r in rows:
        try:
            t=build_trace(db,str(r["account_id"]),str(r["avito_chat_id"]))
            out.append({"trace_id":t["trace_id"],"account_id":t["account_id"],"chat_id":t["chat_id"],"status":t["status"],"breaks":t["breaks"],"stages":t["stages"],"last_at":r.get("last_at")})
        except Exception as exc:
            out.append({"trace_id":trace_id(str(r["account_id"]),str(r["avito_chat_id"])),"account_id":str(r["account_id"]),"chat_id":str(r["avito_chat_id"]),"status":"error","breaks":["TRACE_BUILD_FAILED"],"error_type":type(exc).__name__,"last_at":r.get("last_at")})
    return out
