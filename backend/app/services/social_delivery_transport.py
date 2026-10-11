"""Single-attempt Social transports behind durable publication receipts."""
from __future__ import annotations

import hashlib
import io
import os

from app.services.social_delivery_receipts import (
    DeliveryIntent, PgReceiptJournal, guarded_delivery,
)
from app.services.social_delivery_reconcile import (
    normalized_text, reconcile_telegram, reconcile_vk,
)


def _wire_image(path):
    if not path:
        return None
    with open(path, "rb") as handle:
        source = handle.read()
    try:
        from PIL import Image
        image = Image.open(io.BytesIO(source)).convert("RGB")
        image.thumbnail((1080, 1080))
        output = io.BytesIO()
        image.save(output, "JPEG", quality=85)
        return output.getvalue()
    except Exception:
        return source


def _telegram_outcome(payload):
    if not isinstance(payload, dict):
        return "uncertain", {"ok": False, "reason": "telegram_response_invalid"}
    result = payload.get("result")
    if payload.get("ok") is True and isinstance(result, dict):
        mid = result.get("message_id")
        if isinstance(mid, int) and not isinstance(mid, bool) and mid > 0:
            return "success", {
                "ok": True,
                "result": {"message_id": mid, "date": result.get("date"),
                           "chat": {"id": (result.get("chat") or {}).get("id")}},
            }
        return "uncertain", {"ok": False, "reason": "telegram_success_without_id"}
    if payload.get("ok") is False and isinstance(payload.get("error_code"), int):
        if payload["error_code"] >= 500:
            return "uncertain", {"ok": False, "reason": "telegram_server_outcome_uncertain"}
        seconds = (payload.get("parameters") or {}).get("retry_after")
        result = {
            "ok": False, "code": payload["error_code"],
            "error": "Telegram API rejected request (%s)" % payload["error_code"],
            "stage": "telegram_send", "owner_action_required": False,
        }
        if payload["error_code"] == 429:
            result.update(
                reason="tg_rate_limited", dependency_state="waiting_external",
                retryable_external=True, retry_after_seconds=max(1, int(seconds or 60)),
            )
        return "rejected", result
    return "uncertain", {"ok": False, "reason": "telegram_response_unclassified"}


def send_telegram(chat_id, text, banner_path, *, tenant_scope, delivery_context=None,
                  api_base=None, session=None, journal_factory=None, reconcile=None):
    # Deliberately do not use telegram_bot._telegram_post: it retries an
    # ambiguous POST three times. The existing direct/IPv6 session is retained.
    if session is None or api_base is None:
        from app import telegram_bot
        if not telegram_bot.BOT_TOKEN:
            return {"ok": False, "error": "нет токена бота"}
        api_base = api_base or telegram_bot.API_BASE
        session = session or telegram_bot._TELEGRAM_SESSION
    if not chat_id:
        return {"ok": False, "error": "telegram_destination_missing"}
    try:
        image = _wire_image(banner_path)
    except (OSError, ValueError):
        return {"ok": False, "error": "BANNER_REQUIRED: BANNER_ASSET_MISSING"}
    target = str(chat_id).strip()
    factory = journal_factory or PgReceiptJournal
    reconcile_fn = reconcile or reconcile_telegram
    context = dict(delivery_context or {})
    asset_hash = hashlib.sha256(image).hexdigest() if image is not None else ""
    # Long captions use plain text, so truncation cannot cut an HTML tag.
    long = image is not None and len(text) > 1024
    caption = normalized_text(text)[:1000] + "…" if long else text

    def deliver_text(wire_text, step):
        intent = DeliveryIntent(str(tenant_scope), "tg", target, step,
                                wire_text, "", context)
        def send(_prepared):
            return _telegram_outcome(session.post(
                api_base + "/sendMessage",
                json={"chat_id": chat_id, "text": wire_text, "parse_mode": "HTML"},
                timeout=(5, 60),
            ).json())
        return guarded_delivery(intent, send, reconcile=reconcile_fn, journal_factory=factory)

    if image is None:
        return deliver_text(text, "text")
    intent = DeliveryIntent(str(tenant_scope), "tg", target, "photo",
                            caption, asset_hash, context)
    def send_photo(_prepared):
        data = {"chat_id": chat_id, "caption": caption}
        if not long:
            data["parse_mode"] = "HTML"
        return _telegram_outcome(session.post(
            api_base + "/sendPhoto", data=data,
            files={"photo": ("social.jpg", io.BytesIO(image), "image/jpeg")},
            timeout=(5, 120),
        ).json())
    first = guarded_delivery(intent, send_photo, reconcile=reconcile_fn, journal_factory=factory)
    if not first.get("ok") or not long:
        return first
    extra = deliver_text(text, "overflow_text")
    if not extra.get("ok"):
        return {**extra, "photo_receipt_key": first.get("receipt_key"),
                "photo_message_id": (first.get("result") or {}).get("message_id")}
    return {**first,
            "overflow_message_id": (extra.get("result") or {}).get("message_id"),
            "overflow_receipt_key": extra.get("receipt_key")}


def send_vk(owner_id, text, banner_path, *, tenant_scope, delivery_context=None,
            api_failure, cooldown_remaining, clear_cooldown, publish_wall, token=None,
            session=None, journal_factory=None, reconcile=None):
    token = token or os.environ.get("VK_TOKEN")
    if not token:
        return {"ok": False, "error": "нет VK_TOKEN"}
    if session is None:
        import requests
        session = requests.Session()
        session.trust_env = False
    try:
        image = _wire_image(banner_path)
    except (OSError, ValueError):
        return {"ok": False, "error": "BANNER_REQUIRED: BANNER_ASSET_MISSING"}
    target = str(int(owner_id))
    intent = DeliveryIntent(
        str(tenant_scope), "vk", target, "wall_post", text,
        hashlib.sha256(image).hexdigest() if image is not None else "",
        dict(delivery_context or {}),
    )
    group_id = target.lstrip("-")
    version = "5.199"

    def prepare(existing):
        remaining = int(cooldown_remaining() or 0)
        if remaining > 0:
            return False, {
                "ok": False, "error": "VK cooldown после rate limit: %s сек" % remaining,
                "stage": "rate_backoff", "reason": "vk_rate_limited",
                "dependency_state": "waiting_external", "retryable_external": True,
                "retry_after_seconds": remaining, "owner_action_required": False,
            }
        if image is None:
            return True, {}
        if existing.get("attachment"):
            return True, existing
        # Image preparation has no public-post side effect. There is no local
        # retry loop; API Flood control stops this pass immediately.
        base = {"access_token": token, "v": version, "group_id": group_id}
        upload = session.get("https://api.vk.com/method/photos.getWallUploadServer",
                             params=base, timeout=(5, 30)).json()
        if upload.get("error"):
            return False, api_failure(upload, "photos.getWallUploadServer", "загрузка фото ВК: ")
        upload_url = (upload.get("response") or {}).get("upload_url")
        if not upload_url:
            return False, {"ok": False, "error": "VK upload server response invalid",
                           "stage": "photos.getWallUploadServer"}
        media = session.post(
            upload_url, files={"photo": ("social.jpg", io.BytesIO(image), "image/jpeg")},
            timeout=(5, 120),
        ).json()
        if not all(k in media for k in ("photo", "server", "hash")):
            return False, {"ok": False, "error": "VK image upload response invalid",
                           "stage": "photo_upload"}
        saved = session.get(
            "https://api.vk.com/method/photos.saveWallPhoto",
            params={**base, "photo": media["photo"], "server": media["server"], "hash": media["hash"]},
            timeout=(5, 30),
        ).json()
        if saved.get("error"):
            return False, api_failure(saved, "photos.saveWallPhoto", "загрузка фото ВК: ")
        photos = saved.get("response")
        if not isinstance(photos, list) or not photos or not isinstance(photos[0], dict):
            return False, {"ok": False, "error": "VK saved photo response invalid",
                           "stage": "photos.saveWallPhoto"}
        photo = photos[0]
        if photo.get("owner_id") is None or not photo.get("id"):
            return False, {"ok": False, "error": "VK saved photo identity missing",
                           "stage": "photos.saveWallPhoto"}
        return True, {"attachment": "photo%s_%s" % (photo["owner_id"], photo["id"])}

    def send(prepared):
        params = {"access_token": token, "v": version, "owner_id": target,
                  "from_group": 1, "message": text}
        if prepared.get("attachment"):
            params["attachments"] = prepared["attachment"]
        # Exactly one publication request. Exceptions and malformed replies are
        # quarantined by guarded_delivery; no second wall.post is attempted.
        payload = publish_wall(session, params)
        if isinstance(payload, dict) and isinstance(payload.get("response"), dict):
            pid = payload["response"].get("post_id")
            if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
                try:
                    clear_cooldown()
                except Exception as exc:
                    from app.services.exception_observability import observe_suppressed
                    observe_suppressed(__name__, exc, line=209)
                return "success", {"ok": True, "post_id": pid,
                                   "attachment": prepared.get("attachment")}
            return "uncertain", {"ok": False, "reason": "vk_success_without_post_id"}
        if isinstance(payload, dict) and isinstance(payload.get("error"), dict) and payload["error"].get("error_code"):
            # Server/unknown errors cannot prove the public write was rejected.
            if payload["error"].get("error_code") in {1, 10}:
                return "uncertain", {"ok": False, "reason": "vk_server_outcome_uncertain"}
            return "rejected", api_failure(payload, "wall.post")
        return "uncertain", {"ok": False, "reason": "vk_response_unclassified"}

    recon = reconcile or (
        lambda i, r: reconcile_vk(i, r, token=token,
                                 cooldown_remaining=cooldown_remaining, session=session,
                                 on_api_failure=api_failure)
    )
    return guarded_delivery(intent, send, prepare=prepare, reconcile=recon,
                            journal_factory=journal_factory or PgReceiptJournal)

