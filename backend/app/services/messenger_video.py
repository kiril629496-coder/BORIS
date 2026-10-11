"""Secure BORIS Messenger video/video-note lifecycle."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from jose import JWTError, jwt
from sqlalchemy import text

from app.models.user import User
from app.services.messenger_voice_media import parse_range, VoiceError
from app.services.messenger_video_media import MAX_BYTES, MIMES
from app.services import messenger_telemetry as telemetry

VIDEO_STREAM_COOKIE = "boris_video_stream"
VIDEO_RUNTIME_CONTRACT_VERSION = "MESSENGER_VIDEO_RUNTIME_20261005_V2"
VIDEO_STREAM_MAX_TTL_SECONDS = max(180, min(int(os.getenv("MESSENGER_VIDEO_STREAM_MAX_TTL_SECONDS", "900")), 900))

def enabled(name="MESSENGER_VIDEO_ENABLED"):
    return os.getenv(name, "false").lower() in {"1","true","yes"}

def require_enabled():
    if not enabled():
        raise HTTPException(503, "Видеосообщения временно недоступны")

def core():
    from app.api import internal_messenger
    return internal_messenger

def _secret():
    value = os.getenv("JWT_SECRET") or os.getenv("FERNET_KEY")
    if not value:
        raise RuntimeError("MESSENGER_VIDEO_STREAM_SECRET_MISSING")
    return value

def stream_path(asset_id) -> str:
    return f"/api/messenger-workspace/video/{asset_id}/stream"

def thumbnail_path(asset_id) -> str:
    return f"/api/messenger-workspace/video/{asset_id}/thumbnail"

def public(asset):
    result = {
        "asset_id": str(asset["id"]),
        "kind": asset["kind"],
        "processing_status": asset["processing_status"],
        "processing_error_code": asset["processing_error_code"],
        "duration_ms": asset["duration_ms"],
        "width": asset["width"],
        "height": asset["height"],
        "mime": asset["playback_mime"],
        "size_bytes": asset["playback_size_bytes"],
    }
    if asset["processing_status"] == "ready":
        result["playback_ticket_url"] = f"/api/internal-messenger/video/{asset['id']}/playback-ticket"
    return result

def authorize(db, user, *, asset_id=None, message_id=None, pending=False, lock=False):
    clause = "v.id=:id" if asset_id is not None else "v.message_id=:id"
    value = str(asset_id) if asset_id is not None else message_id
    row = db.execute(text(
        "SELECT v.* FROM internal_video_assets v LEFT JOIN internal_messages m ON m.id=v.message_id "
        f"WHERE {clause} AND v.deleted_at IS NULL "
        "AND (v.message_id IS NULL OR (m.deleted_at IS NULL AND m.conversation_id=v.conversation_id)) "
        + ("FOR UPDATE OF v" if lock else "")
    ), {"id": value}).mappings().first()
    if not row:
        raise HTTPException(404, "Видео не найдено")
    member = core()._require_member(db, row["conversation_id"], user)
    if member["account_id"] != row["account_id"]:
        raise HTTPException(404, "Видео не найдено")
    if row["message_id"] is None and (not pending or row["uploader_user_id"] != user.id):
        raise HTTPException(404, "Видео не найдено")
    return row

async def upload(conversation_id, file, user, kind="video"):
    require_enabled()
    if kind not in {"video","video_note"}:
        raise HTTPException(422, "Недопустимый тип видеосообщения")
    api = core()
    db = api.SessionLocal()
    try:
        member = api._require_member(db, conversation_id, user)
        api._require_reply(db, user, member["account_id"])
        account_id = member["account_id"]
    finally:
        db.close()
    name = file.filename or "video"
    mime = (file.content_type or "").split(";")[0].strip().lower()
    if any(c in name for c in ("/","\\","\x00","\r","\n")) or name in {".",".."}:
        raise HTTPException(400, "Недопустимое имя файла")
    if Path(name).suffix.lower() in api.ATTACHMENT_DENIED_EXTENSIONS or mime not in MIMES:
        raise HTTPException(415, "Этот формат видео не поддерживается")
    # Messenger storage refs have one strict canonical shape:
    # internal:<conversation_id>:<32-hex-token>. Each derived artifact gets
    # its own token instead of adding a fourth colon-separated suffix.
    original_ref = f"internal:{conversation_id}:{uuid4().hex}"
    playback_ref = f"internal:{conversation_id}:{uuid4().hex}"
    thumbnail_ref = f"internal:{conversation_id}:{uuid4().hex}"
    original = api._attachment_storage_path(original_ref, conversation_id)
    original.parent.mkdir(parents=True, exist_ok=True)
    committed = False
    try:
        size = 0
        with original.open("xb") as out:
            while chunk := await file.read(1024*1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, "Видео больше 150 МБ")
                out.write(chunk)
        if size <= 0:
            raise HTTPException(415, "Видео пустое")
        db = api.SessionLocal()
        try:
            member = api._require_member(db, conversation_id, user)
            api._require_reply(db, user, account_id)
            if member["account_id"] != account_id:
                raise HTTPException(404, "Чат не найден")
            asset_id = str(uuid4())
            db.execute(text(
                "INSERT INTO internal_video_assets(id,account_id,conversation_id,uploader_user_id,kind,"
                "original_storage_ref,playback_storage_ref,thumbnail_storage_ref,original_mime,original_size_bytes) "
                "VALUES (:id,:a,:c,:u,:k,:o,:p,:t,:mime,:size)"
            ), {"id":asset_id,"a":account_id,"c":conversation_id,"u":user.id,"k":kind,
                "o":original_ref,"p":playback_ref,"t":thumbnail_ref,"mime":mime,"size":size})
            db.execute(text(
                "INSERT INTO background_jobs(kind,status,owner_user_id,account_id,payload_json,fingerprint,est_kopeks,actual_kopeks) "
                "VALUES ('messenger_video_processing','queued',:u,:a,CAST(:p AS JSONB),:f,0,0) "
                "ON CONFLICT(fingerprint) WHERE fingerprint IS NOT NULL DO NOTHING"
            ), {"u":user.id,"a":account_id,"p":json.dumps({"asset_id":asset_id}),
                "f":"video:"+hashlib.sha256(asset_id.encode()).hexdigest()})
            api.append_security_audit(db, action="video_uploaded", user_id=user.id, account_id=account_id,
                entity_type="conversation", entity_id=conversation_id, details={"asset_id":asset_id,"kind":kind})
            telemetry.record(
                db,
                "video_uploaded",
                user_id=user.id,
                account_id=account_id,
                entity_type="conversation",
                entity_id=conversation_id,
                details={"media_kind": kind, "size_bytes": size},
            )
            db.commit()
            committed=True
            return {"status":"ok","upload":{"asset_id":asset_id,"kind":kind,"processing_status":"queued","size_bytes":size}}
        finally:
            db.close()
    finally:
        await file.close()
        if not committed:
            original.unlink(missing_ok=True)

def commit(db, conversation_id, body, user):
    require_enabled()
    api=core()
    row=authorize(db,user,asset_id=body.video_asset_id,pending=True,lock=True)
    if row["conversation_id"] != conversation_id or row["uploader_user_id"] != user.id:
        raise HTTPException(404,"Видео не найдено")
    if row["processing_status"] == "failed":
        raise HTTPException(422,"Видео не удалось обработать")
    if body.message_type != row["kind"]:
        raise HTTPException(400,"Тип видеосообщения не совпадает")
    api._require_reply(db,user,row["account_id"])
    if row["message_id"]:
        return int(row["message_id"])
    if body.reply_to_message_id and not db.execute(text(
        "SELECT 1 FROM internal_messages WHERE id=:m AND conversation_id=:c AND deleted_at IS NULL"
    ),{"m":body.reply_to_message_id,"c":conversation_id}).first():
        raise HTTPException(400,"Ответ ссылается на другое сообщение")
    if not api._attachment_storage_path(row["original_storage_ref"],conversation_id).is_file():
        raise HTTPException(404,"Видеофайл отсутствует")
    mid=db.execute(text(
        "INSERT INTO internal_messages(conversation_id,sender_user_id,body,reply_to_message_id,message_type) "
        "VALUES (:c,:u,'',:r,:t) RETURNING id"
    ),{"c":conversation_id,"u":user.id,"r":body.reply_to_message_id,"t":row["kind"]}).scalar_one()
    aid=db.execute(text(
        "INSERT INTO internal_message_attachments(message_id,file_name,content_type,storage_ref,size_bytes) "
        "VALUES (:m,:n,'video/mp4',:p,:s) RETURNING id"
    ),{"m":mid,"n":"video-note.mp4" if row["kind"]=="video_note" else "video.mp4",
       "p":row["playback_storage_ref"],"s":row["playback_size_bytes"]}).scalar_one()
    db.execute(text("UPDATE internal_video_assets SET message_id=:m,attachment_id=:a WHERE id=:id"),
               {"m":mid,"a":aid,"id":str(row["id"])})
    db.execute(text("UPDATE internal_conversations SET updated_at=now() WHERE id=:c"),{"c":conversation_id})
    db.execute(text(
        "UPDATE internal_conversation_members SET last_read_message_id=GREATEST(COALESCE(last_read_message_id,0),:m) "
        "WHERE conversation_id=:c AND user_id=:u"
    ),{"m":mid,"c":conversation_id,"u":user.id})
    api.emit_messenger_event(db,row["account_id"],"internal.message.created","message",mid,
                             {"conversation_id":conversation_id,"message_type":row["kind"]})
    return int(mid)

def enrich(db,messages):
    ids=[int(m["id"]) for m in messages if not m.get("deleted_at")]
    rows=db.execute(text("SELECT * FROM internal_video_assets WHERE message_id=ANY(:ids) AND deleted_at IS NULL"),
                    {"ids":ids}).mappings().all() if ids else []
    videos={int(r["message_id"]):public(r) for r in rows}
    for message in messages:
        if message.get("deleted_at"):
            continue
        asset=videos.get(int(message["id"]))
        if asset:
            message.update(message_type=asset["kind"],video=asset,attachments=[])
    return messages

def issue_playback_ticket(db,asset_id,user):
    require_enabled()
    row=authorize(db,user,asset_id=asset_id)
    if row["processing_status"] != "ready":
        raise HTTPException(409,"Видео ещё обрабатывается")
    sid=str(getattr(user,"_auth_session_id","") or "").strip()
    if not sid:
        raise HTTPException(401,"Обновите сеанс для воспроизведения видео")
    epoch=int(getattr(user,"_auth_security_epoch",getattr(user,"security_epoch",0)) or 0)
    duration=max(1,int((int(row["duration_ms"] or 0)+999)/1000))
    ttl=min(VIDEO_STREAM_MAX_TTL_SECONDS,max(180,duration+180))
    exp=datetime.now(timezone.utc)+timedelta(seconds=ttl)
    token=jwt.encode({"typ":"messenger_video_stream","aud":"messenger_video_stream","sub":str(int(user.id)),
                      "sid":sid,"sev":epoch,"asset":str(row["id"]),"exp":exp},_secret(),algorithm="HS256")
    return {"ticket":token,"stream_url":stream_path(row["id"]),"thumbnail_url":thumbnail_path(row["id"]),"expires_in":ttl}

def user_from_ticket(db,asset_id,ticket):
    try:
        payload=jwt.decode(ticket,_secret(),algorithms=["HS256"],audience="messenger_video_stream")
        if payload.get("typ")!="messenger_video_stream" or str(payload.get("asset") or "")!=str(asset_id):
            raise ValueError("bad_ticket")
        uid=int(payload.get("sub")); sid=str(payload.get("sid") or ""); epoch=int(payload.get("sev") or 0)
        if not sid: raise ValueError("missing_session")
    except (JWTError,ValueError,TypeError):
        raise HTTPException(401,"Ссылка воспроизведения истекла")
    user=db.query(User).filter(User.id==uid,User.is_active==True).first()
    if not user or int(getattr(user,"security_epoch",0) or 0)!=epoch:
        raise HTTPException(401,"Сеанс завершён")
    from app.services.auth_sessions import validate_session
    if not validate_session(db,user_id=uid,session_id=sid,token_epoch=epoch,current_epoch=epoch):
        raise HTTPException(401,"Сеанс завершён")
    user._auth_session_id=sid; user._auth_security_epoch=epoch
    authorize(db,user,asset_id=asset_id)
    return user

def _range_response(row,range_header):
    path=core()._attachment_storage_path(row["playback_storage_ref"],row["conversation_id"])
    if not path.is_file(): raise HTTPException(404,"Видеофайл отсутствует")
    size=path.stat().st_size
    try:
        start,end,partial=parse_range(range_header,size)
    except VoiceError:
        raise HTTPException(416,"Недопустимый диапазон",headers={"Content-Range":f"bytes */{size}"})
    def chunks():
        with path.open("rb") as f:
            f.seek(start); left=end-start+1
            while left:
                chunk=f.read(min(256*1024,left))
                if not chunk: break
                left-=len(chunk); yield chunk
    headers={"Accept-Ranges":"bytes","Content-Length":str(end-start+1),"Cache-Control":"private, no-store",
             "Pragma":"no-cache","X-Content-Type-Options":"nosniff","Content-Disposition":'inline; filename="video.mp4"'}
    if partial: headers["Content-Range"]=f"bytes {start}-{end}/{size}"
    return StreamingResponse(chunks(),status_code=206 if partial else 200,media_type="video/mp4",headers=headers)

def playback_ticket_response(db,asset_id,ticket,range_header):
    require_enabled()
    user=user_from_ticket(db,asset_id,ticket)
    row=authorize(db,user,asset_id=asset_id)
    if row["processing_status"]!="ready": raise HTTPException(409,"Видео ещё обрабатывается")
    return _range_response(row,range_header)

def thumbnail_ticket_response(db,asset_id,ticket):
    require_enabled()
    user=user_from_ticket(db,asset_id,ticket)
    row=authorize(db,user,asset_id=asset_id)
    path=core()._attachment_storage_path(row["thumbnail_storage_ref"],row["conversation_id"])
    if not path.is_file(): raise HTTPException(404,"Превью отсутствует")
    return FileResponse(path,media_type="image/jpeg",headers={"Cache-Control":"private, no-store","X-Content-Type-Options":"nosniff"})

def mark_deleted(db,message_id):
    db.execute(text("UPDATE internal_video_assets SET deleted_at=now() WHERE message_id=:m"),{"m":message_id})
