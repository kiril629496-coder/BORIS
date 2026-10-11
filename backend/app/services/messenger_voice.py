"""Voice lifecycle extension of internal Messenger attachments and events."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from jose import JWTError, jwt
from sqlalchemy import text

from app.models.user import User

from app.services.messenger_voice_media import MAX_BYTES, MIMES, MODEL_VERSION, VoiceError, normalize, parse_range
from app.services import messenger_telemetry as telemetry


def enabled(name="MESSENGER_VOICE_ENABLED"):
    return os.getenv(name, "false").lower() in {"1", "true", "yes"}


def require_enabled():
    if not enabled():
        raise HTTPException(503, "Голосовые сообщения временно недоступны")


VOICE_STREAM_COOKIE = "boris_voice_stream"
VOICE_RUNTIME_CONTRACT_VERSION = "MESSENGER_VOICE_RUNTIME_20261004_R10"
VOICE_STREAM_MAX_TTL_SECONDS = max(180, min(int(os.getenv("MESSENGER_VOICE_STREAM_MAX_TTL_SECONDS", "900")), 900))


def _ticket_secret() -> str:
    secret = os.getenv("JWT_SECRET") or os.getenv("FERNET_KEY")
    if not secret:
        raise RuntimeError("MESSENGER_VOICE_STREAM_SECRET_MISSING")
    return secret


def stream_path(asset_id) -> str:
    return f"/api/messenger-workspace/voice/{asset_id}/stream"


def issue_playback_ticket(db, asset_id, user) -> dict:
    require_enabled()
    row = authorize(db, user, asset_id=asset_id)
    session_id = str(getattr(user, "_auth_session_id", "") or "").strip()
    if not session_id:
        raise HTTPException(401, "Обновите сеанс для воспроизведения голосового сообщения")
    epoch = int(getattr(user, "_auth_security_epoch", getattr(user, "security_epoch", 0)) or 0)
    duration_seconds = max(1, int((int(row["duration_ms"] or 0) + 999) / 1000))
    ttl_seconds = min(VOICE_STREAM_MAX_TTL_SECONDS, max(120, duration_seconds + 120))
    expires = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    token = jwt.encode({
        "typ": "messenger_voice_stream",
        "aud": "messenger_voice_stream",
        "sub": str(int(user.id)),
        "sid": session_id,
        "sev": epoch,
        "asset": str(row["id"]),
        "exp": expires,
    }, _ticket_secret(), algorithm="HS256")
    return {
        "ticket": token,
        "stream_url": stream_path(row["id"]),
        "expires_in": ttl_seconds,
    }


def user_from_playback_ticket(db, asset_id, ticket: str):
    if not ticket:
        raise HTTPException(401, "Ссылка воспроизведения истекла")
    try:
        payload = jwt.decode(
            ticket,
            _ticket_secret(),
            algorithms=["HS256"],
            audience="messenger_voice_stream",
        )
        if payload.get("typ") != "messenger_voice_stream":
            raise ValueError("wrong_ticket_type")
        if str(payload.get("asset") or "") != str(asset_id):
            raise ValueError("wrong_asset")
        user_id = int(payload.get("sub"))
        session_id = str(payload.get("sid") or "").strip()
        token_epoch = int(payload.get("sev") or 0)
        if not session_id:
            raise ValueError("missing_session")
    except (JWTError, ValueError, TypeError):
        raise HTTPException(401, "Ссылка воспроизведения истекла")
    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    if not user:
        raise HTTPException(401, "Сеанс завершён")
    current_epoch = int(getattr(user, "security_epoch", 0) or 0)
    if token_epoch != current_epoch:
        raise HTTPException(401, "Сеанс завершён")
    from app.services.auth_sessions import validate_session
    if not validate_session(
        db,
        user_id=int(user.id),
        session_id=session_id,
        token_epoch=token_epoch,
        current_epoch=current_epoch,
    ):
        raise HTTPException(401, "Сеанс завершён")
    user._auth_session_id = session_id
    user._auth_security_epoch = token_epoch
    authorize(db, user, asset_id=asset_id)
    return user


def core():
    from app.api import internal_messenger
    return internal_messenger


def public(asset):
    keys = ("duration_ms", "codec", "mime", "size_bytes", "waveform_peaks", "transcription_status",
            "transcription_text", "transcription_language", "transcription_engine", "transcription_model",
            "transcription_started_at", "transcription_completed_at", "transcription_error_code",
            "transcription_segments")
    result = {key: asset[key] for key in keys}
    result["asset_id"] = str(asset["id"])
    result["playback_url"] = f"/api/internal-messenger/voice/{asset['id']}"
    return result


def authorize(db, user, *, asset_id=None, message_id=None, pending=False, lock=False):
    clause = "v.id=:id" if asset_id is not None else "v.message_id=:id"
    value = str(asset_id) if asset_id is not None else message_id
    row = db.execute(text("SELECT v.* FROM internal_voice_assets v LEFT JOIN internal_messages m ON m.id=v.message_id "
        f"WHERE {clause} AND v.deleted_at IS NULL "
        "AND (v.message_id IS NULL OR (m.deleted_at IS NULL AND m.conversation_id=v.conversation_id)) "
        + ("FOR UPDATE OF v" if lock else "")), {"id": value}).mappings().first()
    if not row:
        raise HTTPException(404, "Голосовое сообщение не найдено")
    member = core()._require_member(db, row["conversation_id"], user)
    if member["account_id"] != row["account_id"]:
        raise HTTPException(404, "Голосовое сообщение не найдено")
    if row["message_id"] is None and (not pending or row["uploader_user_id"] != user.id):
        raise HTTPException(404, "Голосовое сообщение не найдено")
    return row


async def upload(conversation_id, file, user):
    require_enabled()
    api = core()
    db = api.SessionLocal()
    try:
        member = api._require_member(db, conversation_id, user)
        api._require_reply(db, user, member["account_id"])
        account_id = member["account_id"]
    finally:
        db.close()
    name = file.filename or "voice"
    if any(c in name for c in ("/", "\\", "\x00", "\r", "\n")) or name in {".", ".."}:
        raise HTTPException(400, "Недопустимое имя файла")
    if Path(name).suffix.lower() in api.ATTACHMENT_DENIED_EXTENSIONS or (file.content_type or "").split(";")[0].strip().lower() not in MIMES:
        raise HTTPException(415, "Этот формат голосового не поддерживается")
    original_ref = f"internal:{conversation_id}:{uuid4().hex}"
    playback_ref = f"internal:{conversation_id}:{uuid4().hex}"
    original = api._attachment_storage_path(original_ref, conversation_id)
    playback = api._attachment_storage_path(playback_ref, conversation_id)
    original.parent.mkdir(parents=True, exist_ok=True)
    committed = False
    try:
        size = 0
        with original.open("xb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, "Голосовое больше 25 МБ")
                out.write(chunk)
        metadata = await asyncio.to_thread(normalize, original, playback)
        db = api.SessionLocal()
        try:
            # Recheck permission after decoding, before publishing an asset.
            member = api._require_member(db, conversation_id, user)
            api._require_reply(db, user, account_id)
            if member["account_id"] != account_id:
                raise HTTPException(404, "Чат не найден")
            asset_id = str(uuid4())
            db.execute(text("INSERT INTO internal_voice_assets(id,account_id,conversation_id,uploader_user_id,"
                "original_storage_ref,playback_storage_ref,duration_ms,codec,mime,size_bytes,original_size_bytes,waveform_peaks,transcription_model) "
                "VALUES (:id,:a,:c,:u,:o,:p,:d,:codec,:mime,:size,:original_size,CAST(:peaks AS JSONB),:model)"),
                {"id": asset_id, "a": account_id, "c": conversation_id, "u": user.id, "o": original_ref,
                 "p": playback_ref, "d": metadata["duration_ms"], "codec": metadata["codec"], "mime": metadata["mime"],
                 "size": metadata["size_bytes"], "original_size": size, "peaks": json.dumps(metadata["waveform_peaks"]), "model": MODEL_VERSION})
            api.append_security_audit(db, action="voice_uploaded", user_id=user.id, account_id=account_id,
                entity_type="conversation", entity_id=conversation_id, details={"asset_id": asset_id})
            db.commit()
            committed = True
            return {"status": "ok", "upload": {"asset_id": asset_id, **{k: v for k, v in metadata.items() if k != "original_codec"}}}
        finally:
            db.close()
    except VoiceError as exc:
        messages = {"audio_duration_limit": "Голосовое должно быть не длиннее 10 минут", "audio_size_limit": "Пустой или слишком большой файл"}
        raise HTTPException(415, messages.get(exc.code, "Не удалось прочитать аудиофайл")) from exc
    finally:
        await file.close()
        if not committed:
            original.unlink(missing_ok=True)
            playback.unlink(missing_ok=True)


def enqueue(db, row):
    # Same transaction as message/attachment. ON CONFLICT covers concurrent requests.
    db.execute(text("INSERT INTO background_jobs(kind,status,owner_user_id,account_id,payload_json,fingerprint,est_kopeks,actual_kopeks) "
        "VALUES ('messenger_voice_transcription','queued',:u,:a,CAST(:p AS JSONB),:f,0,0) ON CONFLICT(fingerprint) WHERE fingerprint IS NOT NULL DO NOTHING"),
        {"u": row["uploader_user_id"], "a": row["account_id"], "p": json.dumps({"asset_id": str(row["id"])}),
         "f": fingerprint(row)})


def fingerprint(row):
    import hashlib
    return "voice:" + hashlib.sha256((str(row["id"]) + ":" + row["transcription_model"]).encode()).hexdigest()


def commit(db, conversation_id, body, user):
    require_enabled()
    api = core()
    row = authorize(db, user, asset_id=body.voice_asset_id, pending=True, lock=True)
    if row["conversation_id"] != conversation_id or row["uploader_user_id"] != user.id:
        raise HTTPException(404, "Голосовое сообщение не найдено")
    api._require_reply(db, user, row["account_id"])
    if row["message_id"]:
        return row["message_id"]  # lost HTTP response: no duplicate message/job
    if body.reply_to_message_id and not db.execute(text("SELECT 1 FROM internal_messages WHERE id=:m AND conversation_id=:c AND deleted_at IS NULL"),
            {"m": body.reply_to_message_id, "c": conversation_id}).first():
        raise HTTPException(400, "Ответ ссылается на другое сообщение")
    for ref in (row["original_storage_ref"], row["playback_storage_ref"]):
        if not api._attachment_storage_path(ref, conversation_id).is_file():
            raise HTTPException(404, "Аудиофайл отсутствует")
    message_id = db.execute(text("INSERT INTO internal_messages(conversation_id,sender_user_id,body,reply_to_message_id,message_type) "
        "VALUES (:c,:u,'',:r,'voice') RETURNING id"), {"c": conversation_id, "u": user.id, "r": body.reply_to_message_id}).scalar_one()
    attachment_id = db.execute(text("INSERT INTO internal_message_attachments(message_id,file_name,content_type,storage_ref,size_bytes) "
        "VALUES (:m,'voice.m4a','audio/mp4',:p,:s) RETURNING id"),
        {"m": message_id, "p": row["playback_storage_ref"], "s": row["size_bytes"]}).scalar_one()
    status = "queued" if enabled("MESSENGER_LOCAL_TRANSCRIPTION_ENABLED") else "failed"
    db.execute(text("UPDATE internal_voice_assets SET message_id=:m,attachment_id=:at,transcription_status=:s,transcription_error_code=:err WHERE id=:id"),
        {"m": message_id, "at": attachment_id, "s": status, "err": None if status == "queued" else "local_stt_unavailable", "id": str(row["id"])})
    if status == "queued":
        enqueue(db, row)
    db.execute(text("UPDATE internal_conversations SET updated_at=now() WHERE id=:c"), {"c": conversation_id})
    db.execute(text("UPDATE internal_conversation_members SET last_read_message_id=GREATEST(COALESCE(last_read_message_id,0),:m) WHERE conversation_id=:c AND user_id=:u"),
        {"m": message_id, "c": conversation_id, "u": user.id})
    api.emit_messenger_event(db, row["account_id"], "internal.message.created", "message", message_id, {"conversation_id": conversation_id, "message_type": "voice"})
    telemetry.record(
        db,
        "recordings_sent",
        user_id=user.id,
        account_id=row["account_id"],
        entity_type="message",
        entity_id=message_id,
        details={"media_kind": "voice", "duration_ms": int(row["duration_ms"] or 0)},
    )
    return message_id


def enrich(db, messages):
    ids = [int(m["id"]) for m in messages if not m.get("deleted_at")]
    rows = db.execute(text("SELECT * FROM internal_voice_assets WHERE message_id=ANY(:ids) AND deleted_at IS NULL"), {"ids": ids}).mappings().all() if ids else []
    voices = {row["message_id"]: public(row) for row in rows}
    for message in messages:
        message.setdefault("message_type", "text")
        if message.get("deleted_at"):
            message["attachments"] = []
        elif message["id"] in voices:
            message.update(message_type="voice", voice=voices[message["id"]], attachments=[])
    return messages


def _playback_response_for_row(row, range_header):
    path = core()._attachment_storage_path(row["playback_storage_ref"], row["conversation_id"])
    if not path.is_file():
        raise HTTPException(404, "Аудиофайл отсутствует")
    size = path.stat().st_size
    try:
        start, end, partial = parse_range(range_header, size)
    except VoiceError:
        raise HTTPException(416, "Недопустимый диапазон", headers={"Content-Range": f"bytes */{size}"})
    def chunks():
        with path.open("rb") as stream:
            stream.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = stream.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(end-start+1),
        "Cache-Control": "private, no-store",
        "Pragma": "no-cache",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": 'inline; filename="voice.m4a"',
    }
    if partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(
        chunks(),
        status_code=206 if partial else 200,
        media_type="audio/mp4",
        headers=headers,
    )


def playback_response(db, asset_id, user, range_header):
    row = authorize(db, user, asset_id=asset_id)
    return _playback_response_for_row(row, range_header)


def playback_ticket_response(db, asset_id, ticket: str, range_header):
    require_enabled()
    user = user_from_playback_ticket(db, asset_id, ticket)
    row = authorize(db, user, asset_id=asset_id)
    return _playback_response_for_row(row, range_header)
