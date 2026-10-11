"""Tenant-safe WebRTC signaling state machine for BORIS Messenger calls."""
from __future__ import annotations
from datetime import datetime, timezone
import base64, hashlib, hmac, json, os, time
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import text

from app.services import messenger_telemetry as telemetry

ACTIVE={"created","ringing","accepted","connecting","connected","reconnecting"}
TERMINAL={"ended","missed","declined","busy","failed"}
CALL_RUNTIME_CONTRACT_VERSION="MESSENGER_CALLS_RUNTIME_20261005_V4"
CALL_RING_TIMEOUT_SECONDS=max(30,min(int(os.getenv("MESSENGER_CALL_RING_TIMEOUT_SECONDS","60")),120))
CALL_HEARTBEAT_TIMEOUT_SECONDS=max(30,min(int(os.getenv("MESSENGER_CALL_HEARTBEAT_TIMEOUT_SECONDS","45")),120))

def enabled(name="MESSENGER_CALLS_ENABLED"):
    return os.getenv(name,"false").lower() in {"1","true","yes"}

def require_enabled():
    if not enabled(): raise HTTPException(503,"Звонки временно недоступны")

def core():
    from app.api import internal_messenger
    return internal_messenger

def _public(row):
    return {
        "call_id":str(row["id"]),"account_id":row["account_id"],"conversation_id":int(row["conversation_id"]),
        "caller_user_id":int(row["caller_user_id"]),"peer_user_id":int(row["peer_user_id"]),
        "call_type":row["call_type"],"state":row["state"],"created_at":row["created_at"],
        "ringing_at":row["ringing_at"],"answered_at":row["answered_at"],"connected_at":row["connected_at"],
        "ended_at":row["ended_at"],"ended_reason":row["ended_reason"],
    }

def _authorized_call(db,call_id,user,lock=False):
    row=db.execute(text(
        "SELECT c.* FROM internal_calls c WHERE c.id=:id "+("FOR UPDATE OF c" if lock else "")
    ),{"id":str(call_id)}).mappings().first()
    if not row:
        raise HTTPException(404,"Звонок не найден")
    if int(user.id) not in {int(row["caller_user_id"]),int(row["peer_user_id"])}:
        raise HTTPException(404,"Звонок не найден")
    member=core()._require_member(db,int(row["conversation_id"]),user)
    if member["account_id"]!=row["account_id"]:
        raise HTTPException(404,"Звонок не найден")
    return row

def _emit(db,row,event_type,extra=None):
    payload={"conversation_id":int(row["conversation_id"]),"call_id":str(row["id"]),"call_type":row["call_type"],"state":row["state"]}
    if extra: payload.update(extra)
    # Entity is the conversation so the existing realtime membership filter remains authoritative.
    core().emit_messenger_event(db,row["account_id"],event_type,"conversation",row["conversation_id"],payload)

def _history_message(db,row,label):
    if row["history_message_id"]:
        return int(row["history_message_id"])
    body = "Системное событие: " + label
    mid=db.execute(text(
        "INSERT INTO internal_messages(conversation_id,sender_user_id,body,message_type) VALUES (:c,:u,:b,'system') RETURNING id"
    ),{"c":row["conversation_id"],"u":row["caller_user_id"],"b":body}).scalar_one()
    db.execute(text("UPDATE internal_calls SET history_message_id=:m WHERE id=:id"),{"m":mid,"id":str(row["id"])})
    core().emit_messenger_event(
        db,row["account_id"],"internal.message.created","message",mid,
        {"conversation_id":int(row["conversation_id"]),"message_type":"system"},
    )
    return int(mid)

def expire_stale(db,account_id=None):
    """Expire stale call ownership without letting one group participant kill everyone.

    Direct calls remain fail-closed: either participant missing heartbeats ends
    the 1:1 call. Group calls are participant-scoped: declined/missed/ringing
    members never participate in the heartbeat gate, and one stale connected
    member is removed while the remaining group continues.
    """
    params={"ring_timeout":CALL_RING_TIMEOUT_SECONDS,"heartbeat_timeout":CALL_HEARTBEAT_TIMEOUT_SECONDS}
    scope=""
    if account_id:
        scope=" AND c.account_id=:a"; params["a"]=account_id
    changed=0

    # Whole-call ringing timeout. For a group this only applies before anyone
    # accepts; after the first answer, outstanding invitees are handled below.
    ringing=db.execute(text(
        "SELECT c.* FROM internal_calls c WHERE c.state IN ('created','ringing') "
        "AND c.created_at<now()-(:ring_timeout * interval '1 second')"+scope+" FOR UPDATE OF c"
    ),params).mappings().all()
    for row in ringing:
        db.execute(text(
            "UPDATE internal_calls SET state='missed',ended_at=now(),ended_reason='no_answer' WHERE id=:id"
        ),{"id":str(row["id"])})
        if str(row.get("call_mode") or "direct")=="group":
            db.execute(text(
                "UPDATE internal_call_participants SET state='missed',left_at=COALESCE(left_at,now()),updated_at=now() "
                "WHERE call_id=:id AND state IN ('ringing','invited')"
            ),{"id":str(row["id"])})
        live=dict(row); live["state"]="missed"; live["ended_reason"]="no_answer"
        _history_message(db,live,"📞 Пропущенный звонок")
        _emit(db,live,"internal.call.missed")
        telemetry.record(
            db,"call_missed",user_id=int(row["caller_user_id"]),account_id=row["account_id"],
            entity_type="call",entity_id=str(row["id"]),
            details={"call_type":row["call_type"],"call_mode":str(row.get("call_mode") or "direct"),"reason":"no_answer"},
        )
        db.execute(text("UPDATE internal_call_events SET payload_json='{}'::jsonb WHERE call_id=:id"),{"id":str(row["id"])})
        changed+=1

    # Direct 1:1 heartbeat timeout keeps its strict original semantics.
    direct=db.execute(text(
        "SELECT c.* FROM internal_calls c WHERE c.call_mode='direct' "
        "AND c.state IN ('accepted','connecting','connected','reconnecting') "
        "AND EXISTS (SELECT 1 FROM internal_call_participants p WHERE p.call_id=c.id "
        "AND p.state IN ('accepted','connected') "
        "AND p.updated_at<now()-(:heartbeat_timeout * interval '1 second'))"+scope+" FOR UPDATE OF c"
    ),params).mappings().all()
    for row in direct:
        db.execute(text(
            "UPDATE internal_calls SET state='failed',ended_at=now(),ended_reason='heartbeat_timeout' WHERE id=:id"
        ),{"id":str(row["id"])})
        db.execute(text(
            "UPDATE internal_call_participants SET state='left',left_at=COALESCE(left_at,now()),updated_at=now() "
            "WHERE call_id=:id AND state NOT IN ('left','declined','missed','failed')"
        ),{"id":str(row["id"])})
        live=dict(row); live["state"]="failed"; live["ended_reason"]="heartbeat_timeout"
        _history_message(db,live,"📞 Связь прервана")
        _emit(db,live,"internal.call.failed",{"reason":"heartbeat_timeout"})
        telemetry.record(
            db,"call_failed",user_id=int(row["caller_user_id"]),account_id=row["account_id"],
            entity_type="call",entity_id=str(row["id"]),
            details={"call_type":row["call_type"],"call_mode":"direct","reason":"heartbeat_timeout"},
        )
        db.execute(text("UPDATE internal_call_events SET payload_json='{}'::jsonb WHERE call_id=:id"),{"id":str(row["id"])})
        changed+=1

    # Outstanding group invitees time out individually after the ring window.
    group_scope=""
    group_params={"ring_timeout":CALL_RING_TIMEOUT_SECONDS}
    if account_id:
        group_scope=" AND c.account_id=:a"; group_params["a"]=account_id
    expired_invites=db.execute(text(
        "SELECT c.id call_id,c.account_id,c.conversation_id,c.caller_user_id,c.call_type,c.call_mode,p.user_id "
        "FROM internal_calls c JOIN internal_call_participants p ON p.call_id=c.id "
        "WHERE c.call_mode='group' AND c.state IN ('accepted','connecting','connected','reconnecting') "
        "AND p.state IN ('ringing','invited') "
        "AND COALESCE(c.ringing_at,c.created_at)<now()-(:ring_timeout * interval '1 second')"
        +group_scope+" FOR UPDATE OF p"
    ),group_params).mappings().all()
    affected_group_ids=set()
    for item in expired_invites:
        db.execute(text(
            "UPDATE internal_call_participants SET state='missed',left_at=COALESCE(left_at,now()),updated_at=now() "
            "WHERE call_id=:id AND user_id=:u AND state IN ('ringing','invited')"
        ),{"id":str(item["call_id"]),"u":int(item["user_id"])})
        live={
            "id":item["call_id"],"account_id":item["account_id"],"conversation_id":item["conversation_id"],
            "caller_user_id":item["caller_user_id"],"call_type":item["call_type"],"call_mode":"group",
            "state":"connected","peer_user_id":None,
        }
        _emit(db,live,"internal.call.participant_missed",{"actor_user_id":int(item["user_id"]),"call_mode":"group"})
        affected_group_ids.add(str(item["call_id"]))

    # Stale active group participants fail individually, never the whole group.
    stale_group_params={"heartbeat_timeout":CALL_HEARTBEAT_TIMEOUT_SECONDS}
    stale_group_scope=""
    if account_id:
        stale_group_scope=" AND c.account_id=:a"; stale_group_params["a"]=account_id
    stale_participants=db.execute(text(
        "SELECT c.id call_id,c.account_id,c.conversation_id,c.caller_user_id,c.call_type,c.call_mode,p.user_id "
        "FROM internal_calls c JOIN internal_call_participants p ON p.call_id=c.id "
        "WHERE c.call_mode='group' AND c.state IN ('accepted','connecting','connected','reconnecting') "
        "AND p.state IN ('accepted','connected') "
        "AND p.updated_at<now()-(:heartbeat_timeout * interval '1 second')"
        +stale_group_scope+" FOR UPDATE OF p"
    ),stale_group_params).mappings().all()
    for item in stale_participants:
        db.execute(text(
            "UPDATE internal_call_participants SET state='failed',left_at=COALESCE(left_at,now()),updated_at=now() "
            "WHERE call_id=:id AND user_id=:u AND state IN ('accepted','connected')"
        ),{"id":str(item["call_id"]),"u":int(item["user_id"])})
        live={
            "id":item["call_id"],"account_id":item["account_id"],"conversation_id":item["conversation_id"],
            "caller_user_id":item["caller_user_id"],"call_type":item["call_type"],"call_mode":"group",
            "state":"connected","peer_user_id":None,
        }
        _emit(db,live,"internal.call.participant_failed",
              {"actor_user_id":int(item["user_id"]),"call_mode":"group","reason":"heartbeat_timeout"})
        telemetry.record(
            db,"call_failed",user_id=int(item["user_id"]),account_id=item["account_id"],
            entity_type="call",entity_id=str(item["call_id"]),
            details={"call_type":item["call_type"],"call_mode":"group",
                     "reason":"participant_heartbeat_timeout"},
        )
        affected_group_ids.add(str(item["call_id"]))

    # Reconcile only groups touched by participant expiry.
    for call_id in affected_group_ids:
        live=db.execute(text("SELECT * FROM internal_calls WHERE id=:id FOR UPDATE"),{"id":call_id}).mappings().first()
        if not live or str(live["state"]) in TERMINAL:
            continue
        active_count=int(db.execute(text(
            "SELECT count(*) FROM internal_call_participants WHERE call_id=:id AND state IN ('accepted','connected')"
        ),{"id":call_id}).scalar_one())
        ringing_count=int(db.execute(text(
            "SELECT count(*) FROM internal_call_participants WHERE call_id=:id AND state IN ('ringing','invited')"
        ),{"id":call_id}).scalar_one())
        if active_count<=1 and ringing_count==0:
            db.execute(text(
                "UPDATE internal_calls SET state='ended',ended_at=now(),ended_reason='participants_left' WHERE id=:id"
            ),{"id":call_id})
            db.execute(text(
                "UPDATE internal_call_participants SET state='left',left_at=COALESCE(left_at,now()),updated_at=now() "
                "WHERE call_id=:id AND state IN ('accepted','connected')"
            ),{"id":call_id})
            closed=dict(live); closed["state"]="ended"; closed["ended_reason"]="participants_left"; closed["ended_at"]=datetime.now(timezone.utc)
            _history_message(db,closed,
                "📹 Групповой видеозвонок" if live["call_type"]=="video" else "📞 Групповой аудиозвонок")
            _emit(db,closed,"internal.call.ended",{"reason":"participants_left","call_mode":"group"})
        else:
            db.execute(text(
                "UPDATE internal_calls SET state=CASE WHEN connected_at IS NOT NULL THEN 'connected' ELSE 'accepted' END WHERE id=:id"
            ),{"id":call_id})
        changed+=1

    return changed

def expire_all(db):
    accounts=[str(r[0]) for r in db.execute(text(
        "SELECT DISTINCT account_id FROM internal_calls "
        "WHERE state IN ('created','ringing','accepted','connecting','connected','reconnecting')"
    )).all()]
    expired=0
    for account_id in accounts:
        expired += expire_stale(db,account_id)
    return expired

def start(db,conversation_id,user,call_type,client_call_id):
    require_enabled()
    if call_type not in {"audio","video"}: raise HTTPException(422,"Недопустимый тип звонка")
    if call_type=="video" and not enabled("MESSENGER_VIDEO_CALLS_ENABLED"):
        raise HTTPException(503,"Видеозвонки временно недоступны")
    member=core()._require_member(db,conversation_id,user)
    core()._require_reply(db,user,member["account_id"])
    if member["kind"]!="DM": raise HTTPException(422,"Сейчас звонки доступны в личных чатах")
    peers=[int(r[0]) for r in db.execute(text(
        "SELECT user_id FROM internal_conversation_members WHERE conversation_id=:c AND user_id<>:u ORDER BY user_id"
    ),{"c":conversation_id,"u":user.id}).all()]
    if len(peers)!=1: raise HTTPException(422,"Для звонка нужен один собеседник")
    peer=peers[0]
    # Serialize all call intents for the same tenant/user pair. This closes
    # both concurrent duplicate-retry races and two-different-client-id races
    # that could otherwise pass the busy probe before either transaction commits.
    lock_key=f"messenger-call:{member['account_id']}:{min(int(user.id),peer)}:{max(int(user.id),peer)}"
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k,0))"),{"k":lock_key})
    existing=db.execute(text(
        "SELECT * FROM internal_calls WHERE conversation_id=:c AND caller_user_id=:u AND client_call_id=:cid"
    ),{"c":conversation_id,"u":user.id,"cid":str(client_call_id)}).mappings().first()
    if existing: return _public(existing),False
    expire_stale(db,member["account_id"])
    busy=db.execute(text(
        "SELECT 1 FROM internal_calls WHERE account_id=:a "
        "AND state IN ('created','ringing','accepted','connecting','connected','reconnecting') "
        "AND (caller_user_id IN (:u,:p) OR peer_user_id IN (:u,:p)) LIMIT 1"
    ),{"a":member["account_id"],"u":int(user.id),"p":peer}).first()
    if busy: raise HTTPException(409,"Собеседник сейчас занят")
    cid=str(uuid4())
    row=db.execute(text(
        "INSERT INTO internal_calls(id,account_id,conversation_id,caller_user_id,peer_user_id,client_call_id,call_type,state,ringing_at) "
        "VALUES (:id,:a,:c,:u,:p,:client,:t,'ringing',now()) RETURNING *"
    ),{"id":cid,"a":member["account_id"],"c":conversation_id,"u":user.id,"p":peer,"client":str(client_call_id),"t":call_type}).mappings().first()
    db.execute(text(
        "INSERT INTO internal_call_participants(call_id,user_id,state) VALUES (:id,:u,'accepted'),(:id,:p,'ringing')"
    ),{"id":cid,"u":user.id,"p":peer})
    _emit(db,row,"internal.call.invite",{"target_user_ids":[peer]})
    telemetry.record(
        db, "call_started",
        user_id=int(user.id), account_id=row["account_id"],
        entity_type="call", entity_id=str(row["id"]),
        details={"call_type": call_type},
    )
    return _public(row),True

def transition(db,call_id,user,action,session_id=None):
    require_enabled()
    row=_authorized_call(db,call_id,user,lock=True)
    state=row["state"]
    if state in TERMINAL: return _public(row)
    call_mode=str(row.get("call_mode") or "direct")
    is_caller=int(row["caller_user_id"])==int(user.id)

    if call_mode=="direct":
        if action=="accept":
            if is_caller or state not in {"created","ringing"}: raise HTTPException(409,"Звонок уже изменился")
            db.execute(text("UPDATE internal_calls SET state='accepted',answered_at=COALESCE(answered_at,now()) WHERE id=:id"),{"id":str(call_id)})
            db.execute(text("UPDATE internal_call_participants SET state='accepted',accepted_session_id=:s,joined_at=COALESCE(joined_at,now()),updated_at=now() WHERE call_id=:id AND user_id=:u"),
                       {"s":session_id,"id":str(call_id),"u":user.id})
            event="internal.call.accepted"
        elif action=="decline":
            if is_caller or state not in {"created","ringing"}: raise HTTPException(409,"Звонок уже изменился")
            db.execute(text("UPDATE internal_calls SET state='declined',ended_at=now(),ended_reason='declined' WHERE id=:id"),{"id":str(call_id)})
            db.execute(text("UPDATE internal_call_participants SET state='declined',left_at=now(),updated_at=now() WHERE call_id=:id AND user_id=:u"),{"id":str(call_id),"u":user.id})
            event="internal.call.declined"
        elif action in {"connecting","connected","reconnecting"}:
            if state not in {"accepted","connecting","connected","reconnecting"}: raise HTTPException(409,"Звонок ещё не принят")
            db.execute(text(
                "UPDATE internal_calls SET state=:s,connected_at=CASE WHEN :s='connected' THEN COALESCE(connected_at,now()) ELSE connected_at END WHERE id=:id"
            ),{"s":action,"id":str(call_id)})
            db.execute(text(
                "UPDATE internal_call_participants SET state=CASE WHEN :s='connected' THEN 'connected' ELSE state END,"
                "updated_at=now(),joined_at=COALESCE(joined_at,now()) WHERE call_id=:id AND user_id=:u"
            ),{"s":action,"id":str(call_id),"u":user.id})
            event=f"internal.call.{action}"
        elif action=="end":
            db.execute(text("UPDATE internal_calls SET state='ended',ended_at=now(),ended_reason=:r WHERE id=:id"),
                       {"r":"caller_hangup" if is_caller else "peer_hangup","id":str(call_id)})
            db.execute(text("UPDATE internal_call_participants SET state='left',left_at=now(),updated_at=now() WHERE call_id=:id AND user_id=:u"),{"id":str(call_id),"u":user.id})
            event="internal.call.ended"
        else:
            raise HTTPException(422,"Неизвестное действие")
    else:
        participant=db.execute(text(
            "SELECT state FROM internal_call_participants WHERE call_id=:id AND user_id=:u FOR UPDATE"
        ),{"id":str(call_id),"u":int(user.id)}).mappings().first()
        if not participant:
            raise HTTPException(404,"Звонок не найден")
        pstate=str(participant["state"])

        if action=="accept":
            if is_caller or pstate not in {"ringing","invited"}:
                raise HTTPException(409,"Приглашение уже изменилось")
            db.execute(text(
                "UPDATE internal_call_participants SET state='accepted',accepted_session_id=:s,"
                "joined_at=COALESCE(joined_at,now()),updated_at=now() WHERE call_id=:id AND user_id=:u"
            ),{"s":session_id,"id":str(call_id),"u":user.id})
            db.execute(text(
                "UPDATE internal_calls SET state=CASE WHEN state='ringing' THEN 'accepted' ELSE state END,"
                "answered_at=COALESCE(answered_at,now()) WHERE id=:id"
            ),{"id":str(call_id)})
            event="internal.call.participant_joined"
        elif action=="decline":
            if is_caller or pstate not in {"ringing","invited"}:
                raise HTTPException(409,"Приглашение уже изменилось")
            db.execute(text(
                "UPDATE internal_call_participants SET state='declined',left_at=now(),updated_at=now() "
                "WHERE call_id=:id AND user_id=:u"
            ),{"id":str(call_id),"u":user.id})
            remaining=int(db.execute(text(
                "SELECT count(*) FROM internal_call_participants WHERE call_id=:id AND user_id<>:caller "
                "AND state IN ('ringing','accepted','connected')"
            ),{"id":str(call_id),"caller":int(row["caller_user_id"])}).scalar_one())
            if remaining==0 and row["connected_at"] is None:
                db.execute(text(
                    "UPDATE internal_calls SET state='declined',ended_at=now(),ended_reason='all_declined' WHERE id=:id"
                ),{"id":str(call_id)})
                event="internal.call.declined"
            else:
                event="internal.call.participant_declined"
        elif action in {"connecting","connected","reconnecting"}:
            if pstate not in {"accepted","connected"}:
                raise HTTPException(409,"Сначала примите групповой звонок")
            db.execute(text(
                "UPDATE internal_call_participants SET state=CASE WHEN :s='connected' THEN 'connected' ELSE state END,"
                "joined_at=COALESCE(joined_at,now()),updated_at=now() WHERE call_id=:id AND user_id=:u"
            ),{"s":action,"id":str(call_id),"u":user.id})
            if action=="connected":
                db.execute(text(
                    "UPDATE internal_calls SET state='connected',connected_at=COALESCE(connected_at,now()) WHERE id=:id"
                ),{"id":str(call_id)})
            else:
                # GROUP_STATE_RECONCILE_V3: one mesh edge reconnecting must not
                # downgrade an already-live group for every other participant.
                db.execute(text(
                    "UPDATE internal_calls SET state=CASE WHEN connected_at IS NOT NULL THEN 'connected' ELSE 'accepted' END "
                    "WHERE id=:id"
                ),{"id":str(call_id)})
            event=f"internal.call.{action}"
        elif action=="end":
            if is_caller:
                db.execute(text(
                    "UPDATE internal_calls SET state='ended',ended_at=now(),ended_reason='caller_hangup' WHERE id=:id"
                ),{"id":str(call_id)})
                db.execute(text(
                    "UPDATE internal_call_participants SET state='left',left_at=COALESCE(left_at,now()),updated_at=now() "
                    "WHERE call_id=:id AND state NOT IN ('left','declined')"
                ),{"id":str(call_id)})
                event="internal.call.ended"
            else:
                db.execute(text(
                    "UPDATE internal_call_participants SET state='left',left_at=now(),updated_at=now() WHERE call_id=:id AND user_id=:u"
                ),{"id":str(call_id),"u":user.id})
                remaining=int(db.execute(text(
                    "SELECT count(*) FROM internal_call_participants WHERE call_id=:id AND state IN ('ringing','accepted','connected')"
                ),{"id":str(call_id)}).scalar_one())
                if remaining<=1:
                    db.execute(text(
                        "UPDATE internal_calls SET state='ended',ended_at=now(),ended_reason='participants_left' WHERE id=:id"
                    ),{"id":str(call_id)})
                    event="internal.call.ended"
                else:
                    event="internal.call.participant_left"
        else:
            raise HTTPException(422,"Неизвестное действие")

    live=db.execute(text("SELECT * FROM internal_calls WHERE id=:id"),{"id":str(call_id)}).mappings().first()
    if live["state"] in TERMINAL:
        if live["state"]=="declined": label="📞 Звонок отклонён"
        else:
            seconds=0
            if live["connected_at"] and live["ended_at"]:
                seconds=max(0,int((live["ended_at"]-live["connected_at"]).total_seconds()))
            if str(live.get("call_mode") or "direct")=="group":
                label=("📹 Групповой видеозвонок" if live["call_type"]=="video" else "📞 Групповой аудиозвонок")
            else:
                label=("📹 Видеозвонок" if live["call_type"]=="video" else "📞 Аудиозвонок")
            if seconds: label+=f" · {seconds//60}:{seconds%60:02d}"
        _history_message(db,live,label)
        db.execute(text("UPDATE internal_call_events SET payload_json='{}'::jsonb WHERE call_id=:id"),{"id":str(call_id)})

    telemetry_event=None
    telemetry_details={"call_type":live["call_type"],"call_mode":str(live.get("call_mode") or "direct")}
    if action=="accept": telemetry_event="call_answered"
    elif action=="decline": telemetry_event="call_rejected"
    elif action=="reconnecting": telemetry_event="reconnect_attempt"
    elif action=="connected" and state=="reconnecting": telemetry_event="reconnect_success"
    if telemetry_event:
        telemetry.record(db,telemetry_event,user_id=int(user.id),account_id=live["account_id"],
                         entity_type="call",entity_id=str(call_id),details=telemetry_details)
    _emit(db,live,event,{"actor_user_id":int(user.id),"call_mode":call_mode})
    return _public(live)

def signal(db,call_id,user,event_type,payload):
    require_enabled()
    if event_type not in {"offer","answer","ice","renegotiate","ice_restart","mute","camera","heartbeat"}:
        raise HTTPException(422,"Недопустимый сигнал")
    row=_authorized_call(db,call_id,user,lock=True)
    if row["state"] in TERMINAL: raise HTTPException(409,"Звонок завершён")
    is_caller=int(row["caller_user_id"])==int(user.id)
    if event_type in {"offer","renegotiate","ice_restart"} and not is_caller:
        raise HTTPException(403,"Этот сигнал может отправить только инициатор звонка")
    if event_type=="answer" and is_caller:
        raise HTTPException(403,"Ответ на звонок должен отправить собеседник")
    if event_type=="heartbeat":
        # Heartbeats only refresh the participant lease. Persisting one signal
        # row every 10 seconds per participant would create needless DB churn
        # and retain connection metadata that no peer needs to replay.
        db.execute(text(
            "UPDATE internal_call_participants SET updated_at=now() WHERE call_id=:id AND user_id=:u"
        ),{"id":str(call_id),"u":user.id})
        return {"status":"ok","heartbeat":True}
    raw=json.dumps(payload or {},ensure_ascii=False,separators=(",",":"))
    if len(raw.encode("utf-8"))>65536: raise HTTPException(413,"Сигнал слишком большой")
    # _authorized_call(..., lock=True) holds the call row until commit, so
    # concurrent signal writers for this call serialize without an invalid
    # FOR UPDATE aggregate query.
    seq=int(db.execute(text(
        "SELECT COALESCE(max(seq),0)+1 FROM internal_call_events WHERE call_id=:id"
    ),{"id":str(call_id)}).scalar_one())
    db.execute(text(
        "INSERT INTO internal_call_events(call_id,account_id,seq,actor_user_id,event_type,payload_json) "
        "VALUES (:id,:a,:seq,:u,:t,CAST(:p AS JSONB))"
    ),{"id":str(call_id),"a":row["account_id"],"seq":seq,"u":user.id,"t":event_type,"p":raw})
    _emit(db,row,"internal.call.signal",{"signal_seq":seq})
    return {"status":"ok","seq":seq}

def events(db,call_id,user,after_seq=0,limit=100):
    row=_authorized_call(db,call_id,user)
    expire_stale(db,row["account_id"])
    row=_authorized_call(db,call_id,user)
    items=db.execute(text(
        "SELECT seq,actor_user_id,event_type,payload_json,created_at FROM internal_call_events "
        "WHERE call_id=:id AND seq>:after ORDER BY seq ASC LIMIT :limit"
    ),{"id":str(call_id),"after":max(0,int(after_seq)),"limit":max(1,min(int(limit),200))}).mappings().all()
    return {"status":"ok","call":_public(row),"items":[{"seq":int(x["seq"]),"actor_user_id":int(x["actor_user_id"]),
        "event_type":x["event_type"],"payload":x["payload_json"] or {},"created_at":x["created_at"]} for x in items],
        "next_after_seq":int(items[-1]["seq"]) if items else int(after_seq)}

def incoming(db,account_id,user):
    if not core()._access(db,user,account_id): raise HTTPException(403,"Аккаунт недоступен")
    expire_stale(db,account_id)
    rows=db.execute(text(
        "SELECT c.* FROM internal_calls c JOIN internal_conversation_members m ON m.conversation_id=c.conversation_id "
        "WHERE c.account_id=:a AND m.user_id=:u AND c.peer_user_id=:u AND c.state='ringing' "
        "ORDER BY c.created_at DESC LIMIT 5"
    ),{"a":account_id,"u":user.id}).mappings().all()
    return [_public(r) for r in rows]

def history(db,account_id,user,limit=100):
    if not core()._access(db,user,account_id): raise HTTPException(403,"Аккаунт недоступен")
    expire_stale(db,account_id)
    rows=db.execute(text(
        "SELECT c.* FROM internal_calls c JOIN internal_conversation_members m ON m.conversation_id=c.conversation_id "
        "WHERE c.account_id=:a AND m.user_id=:u AND (c.caller_user_id=:u OR c.peer_user_id=:u) "
        "ORDER BY c.created_at DESC LIMIT :limit"
    ),{"a":account_id,"u":user.id,"limit":max(1,min(int(limit),200))}).mappings().all()
    return [_public(r) for r in rows]

def ice_config(user):
    require_enabled()
    if not enabled("MESSENGER_TURN_ENABLED"):
        raise HTTPException(503,"TURN сервер ещё подключается")
    urls=[v.strip() for v in os.getenv("MESSENGER_TURN_URLS","").split(",") if v.strip()]
    if not urls: raise HTTPException(503,"Сервер звонков ещё подключается")
    ttl=max(300,min(int(os.getenv("MESSENGER_TURN_TTL_SECONDS","3600")),3600))
    secret=os.getenv("MESSENGER_TURN_SECRET","")
    expires=int(time.time())+ttl
    username=f"{expires}:{int(user.id)}"
    servers=[]
    stuns=[u for u in urls if u.startswith("stun:")]
    turns=[u for u in urls if u.startswith("turn:") or u.startswith("turns:")]
    if stuns: servers.append({"urls":stuns})
    if turns:
        if not secret: raise HTTPException(503,"TURN credentials недоступны")
        credential=base64.b64encode(hmac.new(secret.encode(),username.encode(),hashlib.sha1).digest()).decode()
        servers.append({"urls":turns,"username":username,"credential":credential,"credentialType":"password"})
    return {"status":"ok","iceServers":servers,"ttl_seconds":ttl}
