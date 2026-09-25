from __future__ import annotations

from urllib.parse import quote

BORIS_SITE = "https://boris-ai.pro"
CONSULT_PHONE_DISPLAY = "+7 981 967-37-87"
CONSULT_PHONE_DIGITS = "79819673787"
WHATSAPP_URL = f"https://wa.me/{CONSULT_PHONE_DIGITS}"
MAX_PHONE_DISPLAY = CONSULT_PHONE_DISPLAY
MAX_URL = "https://max.ru/u/f9LHodD0cOLLNHY2Rea9pC_FXcSh-1LgXiDxZZTAfCr-XQM3rBeNpfMKPb0"

def contact_block(*, include_site: bool = True) -> str:
    parts = []
    if include_site:
        parts.append(f"BORIS: {BORIS_SITE}")
    parts.append(f"WhatsApp: {WHATSAPP_URL}")
    parts.append(f"MAX: {MAX_URL}")
    parts.append(f"Консультация: {CONSULT_PHONE_DISPLAY}")
    return "\n".join(parts)

def ensure_contact_block(text: str, *, include_site: bool = True) -> str:
    """Append missing contact lines and collapse duplicate canonical contact lines."""
    raw = str(text or "").rstrip()
    canonical_prefixes = ("BORIS:", "WhatsApp:", "MAX:", "Консультация:")
    seen = set()
    lines = []
    for line in raw.splitlines():
        stripped = line.strip()
        key = None
        for prefix in canonical_prefixes:
            if stripped.startswith(prefix):
                key = prefix
                break
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        lines.append(line)
    value = "\n".join(lines).rstrip()
    missing = []
    if include_site and BORIS_SITE not in value:
        missing.append(f"BORIS: {BORIS_SITE}")
    if WHATSAPP_URL not in value:
        missing.append(f"WhatsApp: {WHATSAPP_URL}")
    if MAX_URL not in value:
        missing.append(f"MAX: {MAX_URL}")
    if CONSULT_PHONE_DIGITS not in value and CONSULT_PHONE_DISPLAY not in value:
        missing.append(f"Консультация: {CONSULT_PHONE_DISPLAY}")
    if not missing:
        return value
    return value + ("\n\n" if value else "") + "\n".join(missing)

def whatsapp_prefill(message: str = "Здравствуйте! Интересует автоматизация бизнеса BORIS.") -> str:
    return f"{WHATSAPP_URL}?text={quote(message)}"
