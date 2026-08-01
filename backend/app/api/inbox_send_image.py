"""Отправка изображений в Avito.

⚠️ ЗАКРЫТО feature-флагом AVITO_IMAGE_SEND_ENABLED (по умолчанию false).
Сквозная доставка НЕ подтверждена: нет безопасного тестового диалога (MED-001).
Статус функции: PARTIAL / BLOCKED_BY_TEST_ACCOUNT.

Логика: валидация → загрузка в Avito → image_id → отправка → аудит → синк.
При ошибке загрузки отправка НЕ выполняется.
"""
import datetime
import hashlib
import os

import httpx
from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import text

from app.db.session import SessionLocal
from app.api.inbox_slots import _connected_account_ids, _effective_status
from app.models.account_slot import AccountSlot
try:
    from app.api.auth import get_current_user
except ImportError:
    from app.auth import get_current_user

router = APIRouter(prefix="/api/inbox", tags=["inbox-image"])

ALLOWED_MIME = {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif"}
MAX_BYTES = 10 * 1024 * 1024          # 10 МБ
DEDUP_WINDOW_SEC = 120                 # повтор того же файла в тот же чат — не отправляем
UPLOAD_URL = "https://api.avito.ru/messenger/v1/accounts/{uid}/uploadImages"
SEND_URL = "https://api.avito.ru/messenger/v1/accounts/{uid}/chats/{cid}/messages/image"


def image_send_enabled() -> bool:
    return os.environ.get("AVITO_IMAGE_SEND_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")


def _audit(db, **kw):
    try:
        db.execute(text(
            "INSERT INTO inbox_image_sends (created_at, user_id, account_id, avito_chat_id, "
            " file_name, mime, size_bytes, sha256, image_id, stage, status, http_code, error) "
            "VALUES (now(), :uid, :acc, :cid, :fn, :mime, :size, :sha, :img, :stage, :st, :code, :err)"),
            {"uid": kw.get("user_id"), "acc": kw.get("account_id"), "cid": kw.get("chat_id"),
             "fn": kw.get("file_name"), "mime": kw.get("mime"), "size": kw.get("size"),
             "sha": kw.get("sha256"), "img": kw.get("image_id"), "stage": kw.get("stage"),
             "st": kw.get("status"), "code": kw.get("http_code"), "err": (kw.get("error") or "")[:500]})
        db.commit()
    except Exception as e:
        print("[inbox_image] audit failed:", repr(e)[:150], flush=True)


def _upload(uid, token, fname, data, mime):
    """(ok, image_id, http_code, body)"""
    r = httpx.post(UPLOAD_URL.format(uid=uid),
                   headers={"Authorization": f"Bearer {token}"},
                   files={"uploadfile[]": (fname, data, mime)}, timeout=60)
    if r.status_code != 200:
        return False, None, r.status_code, r.text[:400]
    try:
        j = r.json()
        image_id = next(iter(j.keys()))
    except Exception:
        return False, None, r.status_code, "Avito вернул неожиданный ответ: " + r.text[:200]
    if not image_id:
        return False, None, r.status_code, "Avito не вернул image_id"
    return True, image_id, r.status_code, ""


def _send(uid, token, chat_id, image_id):
    """(ok, http_code, body)"""
    r = httpx.post(SEND_URL.format(uid=uid, cid=chat_id),
                   headers={"Authorization": f"Bearer {token}"},
                   json={"image_id": image_id}, timeout=30)
    return r.status_code in (200, 201), r.status_code, r.text[:400]


def service_send_image(user, account_id, chat_id, file_name, data, mime, idempotency_key=None):
    """Сервисный метод. Возвращает dict со status/message + деталями.
    Отдельно от HTTP-слоя — чтобы можно было тестировать без запроса."""
    uid_user = getattr(user, "id", None)

    if not image_send_enabled():
        return {"status": "error", "code": "feature_disabled",
                "message": "Отправка изображений отключена (AVITO_IMAGE_SEND_ENABLED=false)"}

    if not data:
        return {"status": "error", "code": "empty_file", "message": "Файл не передан"}
    if (mime or "").lower() not in ALLOWED_MIME:
        return {"status": "error", "code": "bad_mime",
                "message": f"Недопустимый тип файла: {mime}. Avito принимает только изображения (JPEG, PNG, WebP, GIF)"}
    if len(data) > MAX_BYTES:
        return {"status": "error", "code": "too_large",
                "message": f"Файл больше {MAX_BYTES // 1024 // 1024} МБ"}

    sha = hashlib.sha256(data).hexdigest()
    db = SessionLocal()
    try:
        conn = dict(_connected_account_ids(db, user))
        if account_id not in conn:
            _audit(db, user_id=uid_user, account_id=account_id, chat_id=chat_id, file_name=file_name,
                   mime=mime, size=len(data), sha256=sha, stage="access", status="denied",
                   error="account not owned")
            return {"status": "error", "code": "forbidden_account",
                    "message": "Нельзя отправить в диалог другого аккаунта"}

        slot = db.query(AccountSlot).filter(AccountSlot.account_id == account_id).first()
        if slot and _effective_status(slot) == "readonly":
            return {"status": "error", "code": "readonly",
                    "message": "Подписка на аккаунт неактивна — отправка недоступна"}

        exists = db.execute(text(
            "SELECT 1 FROM messenger_messages WHERE account_id=:a AND avito_chat_id=:c LIMIT 1"),
            {"a": account_id, "c": chat_id}).first()
        if not exists:
            return {"status": "error", "code": "chat_not_found",
                    "message": "Диалог не найден у этого аккаунта"}

        dup = db.execute(text(
            "SELECT image_id, status FROM inbox_image_sends "
            " WHERE account_id=:a AND avito_chat_id=:c AND sha256=:s AND status='ok' "
            "   AND created_at > now() - interval ':w seconds' ".replace(":w", str(DEDUP_WINDOW_SEC)) +
            " ORDER BY id DESC LIMIT 1"), {"a": account_id, "c": chat_id, "s": sha}).first()
        if dup:
            return {"status": "duplicate", "code": "duplicate",
                    "message": "Это изображение уже отправлено в этот диалог только что",
                    "image_id": dup[0]}

        from app.api.messenger import _get_user_id_and_token
        avito_uid, token = _get_user_id_and_token(account_id)
        if not avito_uid:
            return {"status": "error", "code": "auth", "message": "Не удалось авторизоваться в Avito"}

        ok, image_id, code, body = _upload(avito_uid, token, file_name, data, mime)
        if not ok:
            _audit(db, user_id=uid_user, account_id=account_id, chat_id=chat_id, file_name=file_name,
                   mime=mime, size=len(data), sha256=sha, stage="upload", status="error",
                   http_code=code, error=body)
            return {"status": "error", "code": "upload_failed", "http": code,
                    "message": "Avito не принял изображение", "detail": body}

        ok2, code2, body2 = _send(avito_uid, token, chat_id, image_id)
        if not ok2:
            _audit(db, user_id=uid_user, account_id=account_id, chat_id=chat_id, file_name=file_name,
                   mime=mime, size=len(data), sha256=sha, image_id=image_id, stage="send",
                   status="error", http_code=code2, error=body2)
            return {"status": "error", "code": "send_failed", "http": code2,
                    "message": "Изображение загружено, но не отправлено", "image_id": image_id,
                    "detail": body2}

        _audit(db, user_id=uid_user, account_id=account_id, chat_id=chat_id, file_name=file_name,
               mime=mime, size=len(data), sha256=sha, image_id=image_id, stage="send",
               status="ok", http_code=code2)
    finally:
        db.close()

    try:
        from app.api.messenger import sync_chats
        sync_chats(account_id, limit=50)
    except Exception as e:
        print("[inbox_image] sync after send failed:", repr(e)[:150], flush=True)

    return {"status": "ok", "image_id": image_id, "http": code2}


@router.get("/capabilities")
def capabilities(user=Depends(get_current_user)):
    """Что фронту можно показывать. image_send=false → кнопку скрыть."""
    return {"status": "ok", "capabilities": {
        "text_send": True,
        "image_send": image_send_enabled(),
        "image_send_state": "enabled" if image_send_enabled() else "PARTIAL / BLOCKED_BY_TEST_ACCOUNT",
        "pdf_send": False, "video_send": False, "voice_send": False,
        "image_receive": True, "voice_receive": True,
        "max_image_bytes": MAX_BYTES,
        "allowed_image_mime": sorted(ALLOWED_MIME),
    }}


@router.post("/send_image")
async def send_image(account_id: str = Form(...), avito_chat_id: str = Form(...),
                     file: UploadFile = File(...), idempotency_key: str = Form(""),
                     user=Depends(get_current_user)):
    data = await file.read()
    return service_send_image(user, account_id, avito_chat_id,
                              file.filename or "image.jpg", data,
                              file.content_type or "", idempotency_key or None)
