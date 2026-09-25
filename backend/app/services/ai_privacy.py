"""PII minimization for external AI providers.

The module creates reversible placeholders *inside one process/request only*.
Original values are never included in the outbound payload. Provider output is
restored locally before BORIS uses it.

This is a technical minimization layer, not a substitute for the legal basis
for processing data that BORIS itself legitimately stores.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import re
from typing import Any

# Ordered from highly specific identifiers to broader person/address patterns.
_EMAIL_RE = re.compile(r"(?<![\w.+-])([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})(?![\w.-])", re.I)
_INTL_PHONE_RE = re.compile(r"(?<!\d)\+\d(?:[\s().-]*\d){7,14}(?!\d)")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?7|8)?[\s(.-]*(?:\d[\s().-]*){9}\d(?!\d)")
_IPV4_RE = re.compile(r"(?<!\d)(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?!\d)")
_HANDLE_RE = re.compile(r"(?<![\w@])@[A-Za-z0-9_]{5,32}\b")
_SNILS_RE = re.compile(r"(?<!\d)\d{3}[- ]?\d{3}[- ]?\d{3}[ -]?\d{2}(?!\d)")
_PASSPORT_RE = re.compile(r"(?<!\d)\d{2}[ -]?\d{2}[ -]?\d{6}(?!\d)")
_LONG_ID_RE = re.compile(r"(?<!\d)\d{12,19}(?!\d)")
_CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
# Address line fragments. Bound length to avoid swallowing whole prompts.
_ADDRESS_RE = re.compile(
    r"(?i)(?<!\w)(?:адрес\s*[:\-]?\s*)?"
    r"(?:г\.?\s*[А-ЯЁA-Z][^\n,;]{1,50}[,;]\s*)?"
    r"(?:ул\.?|улица|просп(?:ект|\.)?|пр-т|пер\.?|переулок|шоссе|наб(?:ережная|\.)?)"
    r"\s+[^\n;]{2,100}?"
    r"(?:[, ]+(?:д\.?|дом)\s*\d+[А-Яа-яA-Za-z0-9\-/]*"
    r"(?:[, ]+(?:кв\.?|квартира|офис)\s*\d+[А-Яа-яA-Za-z0-9\-/]*)?)?"
)
# Conservative FIO: surname + name (+ patronymic). Avoid all-caps technical tokens.
_FIO_RE = re.compile(
    r"(?<![\w-])"
    r"([А-ЯЁ][а-яё]{2,}(?:-[А-ЯЁ][а-яё]{2,})?\s+"
    r"[А-ЯЁ][а-яё]{2,}(?:-[А-ЯЁ][а-яё]{2,})?"
    r"(?:\s+[А-ЯЁ][а-яё]{3,}(?:ич|на|лы|зы|оглы|кызы))?)"
    r"(?![\w-])"
)

# Explicit labeled fields catch one-word names that the FIO regex intentionally
# leaves alone.
_LABELLED_NAME_RE = re.compile(
    r"(?im)\b(имя|фио|клиент|контактное лицо|менеджер|покупатель|заказчик|меня зовут|мо[её] имя)"
    r"(\s*[:=\-]?\s+)([А-ЯЁA-Z][А-ЯЁA-Za-zА-Яа-яё-]{1,40}(?:\s+[А-ЯЁA-Z][А-ЯЁA-Za-zА-Яа-яё-]{1,40}){0,2})"
)

_LABELLED_ORG_RE = re.compile(
    r"(?im)\b(компания|организация|юр\.?\s*лицо|company|organization|business)"
    r"(\s*[:=\-]?\s+)([^\n,;]{2,100})"
)
_LABELLED_ID_RE = re.compile(
    r"(?im)\b(account_id|client_id|customer_id|lead_id|deal_id|chat_id|"
    r"order_id|campaign_id|item_id|project_id|call_id|message_id)"
    r"(\s*[:=\-]?\s*)([A-Za-zА-Яа-яЁё0-9_.:@/-]{2,128})"
)
_SECRET_ASSIGN_RE = re.compile(
    r"(?im)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|"
    r"password|passwd|authorization|cookie)"
    r"(\s*[:=]\s*)([^\s,;]{4,})"
)
_URL_SECRET_PARAM_RE = re.compile(
    r"(?i)([?&](?:token|access_token|refresh_token|api_key|apikey|key|secret|"
    r"signature|sig|auth|authorization|session|session_id|code)=)([^&#\s]+)"
)


@dataclass
class PrivacyVault:
    mapping: dict[str, str] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)

    def token(self, kind: str, value: str) -> str:
        value = str(value)

        def norm(v: str) -> str:
            if kind in {"PHONE", "SNILS", "PASSPORT", "ID", "NUMBER"}:
                return re.sub(r"\D+", "", v)
            if kind in {"EMAIL", "HANDLE"}:
                return v.strip().lower()
            return re.sub(r"\s+", " ", v).strip()

        # Re-use the same placeholder inside one request even when phone/
        # identifier formatting differs between history messages.
        wanted = norm(value)
        for token, original in self.mapping.items():
            if token.startswith(f"[[BORIS_{kind}_") and norm(original) == wanted:
                return token
        n = self.counters.get(kind, 0) + 1
        self.counters[kind] = n
        token = f"[[BORIS_{kind}_{n}]]"
        self.mapping[token] = value
        return token

    def restore_text(self, text: str) -> str:
        out = str(text or "")
        # Provider may occasionally add spaces around a placeholder. Restore the
        # exact canonical token first; do not fuzzy-match arbitrary output.
        for token, original in sorted(self.mapping.items(), key=lambda kv: -len(kv[0])):
            out = out.replace(token, original)
        return out


def _replace_regex(text: str, rx: re.Pattern, kind: str, vault: PrivacyVault) -> str:
    return rx.sub(lambda m: vault.token(kind, m.group(0)), text)


def anonymize_text(text: str, vault: PrivacyVault | None = None) -> tuple[str, PrivacyVault]:
    vault = vault or PrivacyVault()
    out = str(text or "")

    # Secrets are not reversible business context. Strip them permanently before
    # any provider call; unlike client placeholders they must never be restored
    # from model output.
    out = _SECRET_ASSIGN_RE.sub(lambda m: m.group(1) + m.group(2) + "[[BORIS_SECRET_REDACTED]]", out)
    out = _URL_SECRET_PARAM_RE.sub(lambda m: m.group(1) + "[[BORIS_SECRET_REDACTED]]", out)

    # Binary image payloads are already re-encoded by media transport. Never
    # regex-substitute inside base64 because that would corrupt the image.
    if out.startswith("data:image/") and ";base64," in out[:100]:
        return out, vault

    # Labeled names first so labels remain useful context for the model.
    def repl_label(m: re.Match) -> str:
        return f"{m.group(1)}{m.group(2)}{vault.token('PERSON', m.group(3))}"
    out = _LABELLED_NAME_RE.sub(repl_label, out)

    def repl_org(m: re.Match) -> str:
        return f"{m.group(1)}{m.group(2)}{vault.token('ORG', m.group(3))}"
    out = _LABELLED_ORG_RE.sub(repl_org, out)

    def repl_id(m: re.Match) -> str:
        return f"{m.group(1)}{m.group(2)}{vault.token('ID', m.group(3))}"
    out = _LABELLED_ID_RE.sub(repl_id, out)

    for rx, kind in (
        (_EMAIL_RE, "EMAIL"),
        (_INTL_PHONE_RE, "PHONE"),
        (_PHONE_RE, "PHONE"),
        (_SNILS_RE, "SNILS"),
        (_PASSPORT_RE, "PASSPORT"),
        (_IPV4_RE, "IP"),
        (_HANDLE_RE, "HANDLE"),
        (_ADDRESS_RE, "ADDRESS"),
        (_FIO_RE, "PERSON"),
        (_LONG_ID_RE, "ID"),
        (_CARD_RE, "NUMBER"),
    ):
        out = _replace_regex(out, rx, kind, vault)
    return out, vault


def anonymize_value(value: Any, vault: PrivacyVault | None = None) -> tuple[Any, PrivacyVault]:
    vault = vault or PrivacyVault()
    if isinstance(value, str):
        return anonymize_text(value, vault)
    if isinstance(value, list):
        return [anonymize_value(v, vault)[0] for v in value], vault
    if isinstance(value, tuple):
        return tuple(anonymize_value(v, vault)[0] for v in value), vault
    if isinstance(value, dict):
        out = {}
        key_kinds = {
            "fio": "PERSON", "full_name": "PERSON", "person_name": "PERSON",
            "first_name": "PERSON", "last_name": "PERSON", "middle_name": "PERSON",
            "client_name": "PERSON", "customer_name": "PERSON", "contact_name": "PERSON",
            "company_name": "ORG", "organization_name": "ORG", "business_name": "ORG",
            "company": "ORG", "organization": "ORG",
            "phone": "PHONE", "telephone": "PHONE", "mobile": "PHONE",
            "email": "EMAIL", "e_mail": "EMAIL",
            "telegram": "HANDLE", "username": "HANDLE",
            "address": "ADDRESS", "legal_address": "ADDRESS", "actual_address": "ADDRESS",
            "passport": "PASSPORT", "snils": "SNILS", "inn": "ID", "ogrn": "ID", "ogrnip": "ID",
            "user_id": "ID", "account_id": "ID", "client_id": "ID", "customer_id": "ID",
            "lead_id": "ID", "deal_id": "ID", "chat_id": "ID", "contact_id": "ID",
            "order_id": "ID", "campaign_id": "ID", "item_id": "ID", "project_id": "ID",
            "call_id": "ID", "message_id": "ID",
        }
        secret_keys = {
            "api_key", "apikey", "access_token", "refresh_token", "token", "secret",
            "password", "passwd", "authorization", "cookie", "session", "session_id",
        }
        for k, v in value.items():
            normalized_key = str(k).strip().lower()
            if normalized_key in secret_keys and v is not None:
                out[k] = "[[BORIS_SECRET_REDACTED]]"
                continue
            kind = key_kinds.get(normalized_key)
            if kind and v is not None and not isinstance(v, (dict, list, tuple)):
                out[k] = vault.token(kind, str(v))
            else:
                out[k], _ = anonymize_value(v, vault)
        return out, vault
    return value, vault


def restore_value(value: Any, vault: PrivacyVault) -> Any:
    if isinstance(value, str):
        return vault.restore_text(value)
    if isinstance(value, list):
        return [restore_value(v, vault) for v in value]
    if isinstance(value, tuple):
        return tuple(restore_value(v, vault) for v in value)
    if isinstance(value, dict):
        return {k: restore_value(v, vault) for k, v in value.items()}
    return value


def contains_obvious_pii(text: str) -> bool:
    """Fail-closed helper for QA/audit; not used to identify people in product UI."""
    value = str(text or "")
    checks = (
        _EMAIL_RE, _INTL_PHONE_RE, _PHONE_RE, _SNILS_RE, _PASSPORT_RE, _IPV4_RE,
        _HANDLE_RE, _ADDRESS_RE, _FIO_RE, _LONG_ID_RE,
    )
    return any(rx.search(value) for rx in checks)


class ExternalAIPrivacyBlocked(RuntimeError):
    pass


def _residual_pii_in_value(value: Any) -> bool:
    if isinstance(value, str):
        if value.startswith("data:image/") and ";base64," in value[:100]:
            return False
        return contains_obvious_pii(value)
    if isinstance(value, (list, tuple)):
        return any(_residual_pii_in_value(v) for v in value)
    if isinstance(value, dict):
        return any(_residual_pii_in_value(v) for v in value.values())
    return False


def safe_external_ai_payload(
    value: Any,
    vault: PrivacyVault | None = None,
) -> tuple[Any, PrivacyVault]:
    """Canonical entrypoint for all external AI transports.

    Default policy is fail-closed: if direct PII survives the redaction pass,
    the foreign/cloud provider call is blocked rather than leaking the value.
    """
    clean, vault = anonymize_value(value, vault)
    fail_closed = str(os.getenv("BORIS_EXTERNAL_AI_PII_FAIL_CLOSED", "1") or "1").strip().lower()
    if fail_closed not in {"0", "false", "off", "no"} and _residual_pii_in_value(clean):
        raise ExternalAIPrivacyBlocked("EXTERNAL_AI_PII_REDACTION_INCOMPLETE")
    return clean, vault


def restore_external_ai_output(value: Any, vault: PrivacyVault) -> Any:
    return restore_value(value, vault)


def scrub_image_metadata_bytes(data: bytes, mime: str = "image/png") -> tuple[bytes, str]:
    """Re-encode image bytes before external AI upload, stripping EXIF/GPS/text metadata."""
    from io import BytesIO
    from PIL import Image

    raw = bytes(data or b"")
    if not raw:
        raise ValueError("empty image")
    with Image.open(BytesIO(raw)) as img:
        img.load()
        fmt = (img.format or "").upper()
        out = BytesIO()
        if fmt in {"JPG", "JPEG"}:
            if img.mode not in {"RGB", "L"}:
                img = img.convert("RGB")
            img.save(out, format="JPEG", quality=95, optimize=True)
            return out.getvalue(), "image/jpeg"
        if fmt == "WEBP":
            img.save(out, format="WEBP", quality=95, method=4)
            return out.getvalue(), "image/webp"
        if img.mode not in {"RGB", "RGBA", "L", "LA", "P"}:
            img = img.convert("RGBA")
        img.save(out, format="PNG", optimize=True)
        return out.getvalue(), "image/png"


def sanitize_external_reference_image_bytes(
    data: bytes,
    *,
    max_side: int = 384,
    blur_radius: float = 3.5,
) -> tuple[bytes, str]:
    """Create a privacy-minimized reference image for a foreign/cloud AI.

    We intentionally trade fine detail for privacy: metadata is removed, the
    image is downscaled, and a mild whole-frame blur makes small text, number
    plates, document fields and fine facial detail materially harder to read
    while preserving coarse product/scene geometry needed for generation/QA.
    """
    from io import BytesIO
    from PIL import Image, ImageFilter

    raw = bytes(data or b"")
    if not raw:
        raise ValueError("empty image")
    max_side = max(128, min(int(max_side or 384), 768))
    blur_radius = max(0.0, min(float(blur_radius or 0.0), 12.0))

    with Image.open(BytesIO(raw)) as img:
        img.load()
        img = img.convert("RGB")
        img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        if blur_radius > 0:
            img = img.filter(ImageFilter.GaussianBlur(radius=blur_radius))
        out = BytesIO()
        img.save(out, format="JPEG", quality=88, optimize=True)
        return out.getvalue(), "image/jpeg"
