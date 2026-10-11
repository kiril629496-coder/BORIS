"""Read-only provider reconciliation for an uncertain Social delivery."""
from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

_MAX_BODY = 8 * 1024 * 1024


def _dt(value):
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, timezone.utc)
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


class _Plain(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag == "br":
            self.parts.append("\n")


def normalized_text(value):
    parser = _Plain()
    parser.feed(str(value or ""))
    return re.sub(r"\s+", " ", html.unescape("".join(parser.parts))).strip()


def _window(receipt):
    attempted = _dt(receipt.get("attempted_at"))
    if attempted:
        return attempted - timedelta(seconds=120), attempted + timedelta(minutes=7)
    created = _dt((receipt.get("context") or {}).get("created_at"))
    if created is None:
        return None
    return created - timedelta(seconds=120), datetime.now(timezone.utc) + timedelta(seconds=120)


def _pre_send_rejection(receipt):
    c = receipt.get("context") or {}
    stage = str(c.get("legacy_stage") or "").lower()
    error = str(c.get("legacy_error") or "").lower()
    return stage in {"rate_backoff", "photos.getwalluploadserver", "photos.savewallphoto"} or any(
        marker in error for marker in (
            "photos.getwalluploadserver:", "photos.savewallphoto:",
            "vk live delivery paced: next rescue pass",
            "vk cooldown после rate limit:", "banner_asset_missing",
        )
    )


def _read_bytes(session, url, **kwargs):
    response = session.get(url, timeout=(5, 25), stream=True, **kwargs)
    response.raise_for_status()
    chunks, size = [], 0
    try:
        for chunk in response.iter_content(64 * 1024):
            size += len(chunk)
            if size > _MAX_BODY:
                raise ValueError("RECONCILIATION_BODY_TOO_LARGE")
            chunks.append(chunk)
    finally:
        response.close()
    return b"".join(chunks)


def match_vk_items(intent, receipt, items, *, scan_complete=False, image_sha=None):
    window = _window(receipt)
    if not window:
        return {"status": "unavailable", "reason": "publication_window_missing"}
    start, end = window
    expected_text = normalized_text(intent.text)
    expected_attachment = str((receipt.get("prepared") or {}).get("attachment") or "")
    confirmed, unresolved = [], []
    for item in items:
        when = _dt(item.get("date"))
        if not when or not start <= when <= end:
            continue
        if str(item.get("owner_id")) != str(intent.target):
            continue
        if normalized_text(item.get("text")) != expected_text:
            continue
        media = item.get("attachments") if isinstance(item.get("attachments"), list) else []
        photos = [x.get("photo") for x in media if x.get("type") == "photo" and isinstance(x.get("photo"), dict)]
        if expected_attachment:
            asset_ok = len(photos) == 1 and (
                "photo%s_%s" % (photos[0].get("owner_id"), photos[0].get("id"))
            ) == expected_attachment
        elif intent.asset_sha256:
            asset_ok = False
            if len(photos) == 1 and image_sha is not None:
                sizes = sorted(photos[0].get("sizes") or [], key=lambda x: int(x.get("width") or 0) * int(x.get("height") or 0), reverse=True)
                if sizes and sizes[0].get("url"):
                    asset_ok = image_sha(sizes[0]["url"]) == intent.asset_sha256
        else:
            # Link previews do not turn a deliberate text-only post into a photo publication.
            asset_ok = not photos
        candidate = {"post_id": item.get("id"), "date": item.get("date"), "asset_verified": bool(asset_ok)}
        if asset_ok and isinstance(item.get("id"), int) and item["id"] > 0:
            confirmed.append(candidate)
        else:
            unresolved.append(candidate)
    if len(confirmed) == 1 and not unresolved:
        c = confirmed[0]
        return {
            "status": "confirmed", "mode": "vk_wall_get_exact",
            "result": {"ok": True, "post_id": c["post_id"], "attachment": expected_attachment or None},
            "provider_post_id": c["post_id"], "provider_published_at": c["date"], "asset_verified": True,
        }
    if confirmed or unresolved:
        return {"status": "ambiguous", "mode": "vk_wall_get_exact",
                "candidates": confirmed + unresolved, "scan_complete": bool(scan_complete)}
    return {"status": "absent", "mode": "vk_wall_get_exact",
            "scan_complete": bool(scan_complete),
            "pre_send_rejection_proven": _pre_send_rejection(receipt)}


class TelegramPublicFeed(HTMLParser):
    """Read public message IDs, UTC dates, caption text and the actual photo URL."""

    _VOID = {"br", "img", "meta", "link", "input", "hr", "source", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.message = None
        self.message_depth = None
        self.text_depth = None
        self.items = []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        classes = set((attrs.get("class") or "").split())
        if tag not in self._VOID:
            self.depth += 1
        if "data-post" in attrs and "tgme_widget_message" in classes:
            identity = attrs["data-post"].rsplit("/", 1)
            if len(identity) == 2 and identity[1].isdigit():
                self.message = {"channel": identity[0], "message_id": int(identity[1]),
                                "parts": [], "photo_url": None, "date": None}
                self.message_depth = self.depth
        if self.message is None:
            return
        if "tgme_widget_message_text" in classes:
            self.text_depth = self.depth
        if tag == "br" and self.text_depth is not None:
            self.message["parts"].append("\n")
        if "tgme_widget_message_photo_wrap" in classes:
            match = re.search(r"background-image\s*:\s*url\(['\"]?(.*?)['\"]?\)", attrs.get("style") or "")
            if match:
                self.message["photo_url"] = html.unescape(match.group(1))
        if tag == "time":
            self.message["date"] = attrs.get("datetime")

    def handle_data(self, data):
        if self.message is not None and self.text_depth is not None:
            self.message["parts"].append(data)

    def handle_endtag(self, tag):
        if tag in self._VOID:
            return
        if self.text_depth == self.depth:
            self.text_depth = None
        if self.message is not None and self.message_depth == self.depth:
            self.message["text"] = "".join(self.message.pop("parts"))
            self.items.append(self.message)
            self.message = None
            self.message_depth = None
            self.text_depth = None
        self.depth = max(0, self.depth - 1)


def match_telegram_items(intent, receipt, items, *, image_sha=None):
    window = _window(receipt)
    if not window:
        return {"status": "unavailable", "reason": "publication_window_missing"}
    start, end = window
    channel = str(intent.target).lstrip("@").lower()
    confirmed, unresolved = [], []
    for item in items:
        if str(item.get("channel") or "").lower() != channel:
            continue
        when = _dt(item.get("date"))
        if not when or not start <= when <= end:
            continue
        if normalized_text(item.get("text")) != normalized_text(intent.text):
            continue
        photo = str(item.get("photo_url") or "")
        asset_ok = not photo if not intent.asset_sha256 else bool(
            photo and image_sha is not None and image_sha(photo) == intent.asset_sha256
        )
        candidate = {"message_id": item.get("message_id"), "date": item.get("date"),
                     "asset_verified": bool(asset_ok)}
        if asset_ok and isinstance(item.get("message_id"), int) and item["message_id"] > 0:
            confirmed.append(candidate)
        else:
            unresolved.append(candidate)
    if len(confirmed) == 1 and not unresolved:
        c = confirmed[0]
        return {
            "status": "confirmed", "mode": "telegram_public_exact",
            "result": {"ok": True, "result": {"message_id": c["message_id"],
                                             "date": c["date"], "chat": {"id": intent.target}}},
            "provider_message_id": c["message_id"], "provider_published_at": c["date"], "asset_verified": True,
        }
    if confirmed or unresolved:
        return {"status": "ambiguous", "mode": "telegram_public_exact",
                "candidates": confirmed + unresolved,
                "reason": "asset_identity_or_multiple_matches"}
    # Telegram public history can be truncated or unavailable. Absence is never
    # proof that a Bot API request was not accepted.
    return {"status": "absent", "mode": "telegram_public_exact",
            "scan_complete": False, "pre_send_rejection_proven": False}


def _vk_readback_api_failure_evidence(value):
    """Keep only bounded typed diagnostics from the existing API normalizer.

    Never persist its free-form error, provider payload, request parameters or
    token. String fields use the normalizer's known vocabulary, not raw text.
    This evidence changes neither retry decisions nor the generic wait reason.
    """
    if not isinstance(value, dict):
        return {}
    evidence = {}
    for key in ("code", "retry_after_seconds"):
        item = value.get(key)
        if type(item) is str and re.fullmatch(r"[0-9]{1,10}", item.strip()):
            item = int(item.strip())
        if type(item) is int and 0 <= item <= 2_147_483_647:
            evidence[key] = item
    for key, allowed in (
        ("stage", {"wall.get"}),
        ("reason", {"vk_rate_limited"}),
        ("dependency_state", {"waiting_external"}),
    ):
        item = value.get(key)
        if type(item) is str and item in allowed:
            evidence[key] = item
    if type(value.get("retryable_external")) is bool:
        evidence["retryable_external"] = value["retryable_external"]
    return evidence


def reconcile_vk(intent, receipt, *, token, cooldown_remaining=lambda: 0, session=None,
                 on_api_failure=None):
    if not token:
        return {"status": "unavailable", "reason": "vk_token_missing"}
    if int(cooldown_remaining() or 0) > 0:
        return {"status": "unavailable", "reason": "vk_cooldown"}
    if session is None:
        import requests
        session = requests.Session()
        session.trust_env = False
    window = _window(receipt)
    if window is None:
        return {"status": "unavailable", "reason": "publication_window_missing"}
    items, complete = [], False
    for offset in (0, 100):
        data = session.get(
            "https://api.vk.com/method/wall.get",
            params={"access_token": token, "v": "5.199", "owner_id": intent.target,
                    "count": 100, "offset": offset, "filter": "owner"},
            timeout=(5, 25),
        ).json()
        response = data.get("response")
        if not isinstance(response, dict) or not isinstance(response.get("items"), list):
            result = {"status": "unavailable", "reason": "vk_readback_rejected"}
            if isinstance(data.get("error"), dict) and on_api_failure is not None:
                evidence = _vk_readback_api_failure_evidence(on_api_failure(data, "wall.get"))
                if evidence:
                    result["api_failure"] = evidence
            return result
        page = response["items"]
        items.extend(page)
        # A pinned old post does not prove that the ordinary history page
        # covers the entire attempted-publication window.
        dates = [_dt(x.get("date")) for x in page if not x.get("is_pinned")]
        dates = [x for x in dates if x is not None]
        total = response.get("count")
        complete = (isinstance(total, int) and len(items) >= total) or (
            bool(dates) and min(dates) <= window[0]
        )
        if complete or not page:
            break
    def photo_sha(url):
        if not str(url).startswith("https://"):
            return ""
        return hashlib.sha256(_read_bytes(session, url)).hexdigest()
    return match_vk_items(intent, receipt, items, scan_complete=complete, image_sha=photo_sha)


def reconcile_telegram(intent, receipt, *, session=None):
    channel = str(intent.target).lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", channel):
        return {"status": "unavailable", "reason": "telegram_private_history_unavailable"}
    if session is None:
        import requests
        session = requests.Session()
        session.trust_env = False
    body = _read_bytes(session, "https://t.me/s/" + channel,
                       headers={"User-Agent": "BORIS-Social-Readback/1"})
    feed = TelegramPublicFeed()
    feed.feed(body.decode("utf-8", errors="replace"))
    def photo_sha(url):
        if not str(url).startswith("https://"):
            return ""
        return hashlib.sha256(_read_bytes(session, url)).hexdigest()
    return match_telegram_items(intent, receipt, feed.items, image_sha=photo_sha)

