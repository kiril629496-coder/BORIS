# -*- coding: utf-8 -*-
"""Low-noise observability for legacy best-effort exception boundaries.

This module is a migration aid, not permission to add broad catches. It makes
existing suppressed failures observable without logging exception messages,
request payloads, PII, provider responses or secrets.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time


_LOCK = threading.Lock()
_LAST: dict[tuple[str, int, str, str], float] = {}
_DEFAULT_INTERVAL = 300.0


def observe_suppressed(module: str, exc: BaseException, *, line: int = 0,
                       min_interval_seconds: float = _DEFAULT_INTERVAL) -> None:
    """Emit a rate-limited, privacy-safe signal for a legacy suppressed error.

    The raw exception text is never written. A short irreversible digest lets
    operators group repeated failures without exposing message contents.
    """
    exc_type = type(exc).__name__
    try:
        raw = f"{exc_type}:{exc}".encode("utf-8", "replace")
        fingerprint = hashlib.sha256(raw).hexdigest()[:12]
    except Exception:
        fingerprint = "fingerprint_error"
    key = (str(module or "unknown"), int(line or 0), exc_type, fingerprint)
    now = time.monotonic()
    with _LOCK:
        previous = float(_LAST.get(key, 0.0) or 0.0)
        if previous and now - previous < max(1.0, float(min_interval_seconds)):
            return
        _LAST[key] = now
        # Bound memory in long-lived workers without another cleanup service.
        if len(_LAST) > 4096:
            cutoff = now - max(_DEFAULT_INTERVAL, float(min_interval_seconds)) * 4.0
            for old_key, seen_at in list(_LAST.items()):
                if seen_at < cutoff:
                    _LAST.pop(old_key, None)
    logging.getLogger("boris.suppressed").warning(
        "legacy_suppressed_exception module=%s line=%s type=%s fingerprint=%s",
        module or "unknown", int(line or 0), exc_type, fingerprint,
    )
