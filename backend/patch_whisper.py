# -*- coding: utf-8 -*-
"""Retired migration helper.

Raw call audio must stay inside BORIS. Speech-to-text is local; this historical
patch is intentionally non-applicable so it cannot reintroduce an external
Whisper upload in the future.
"""
import sys

MARKER = "LEGACY_EXTERNAL_RAW_AUDIO_PATCH_DISABLED_V1"

if __name__ == "__main__":
    if "--apply" in sys.argv:
        print("BLOCKED: raw client audio must use local STT; external Whisper patch is retired")
        raise SystemExit(2)
    print(MARKER)
    print("dry-run only: no files changed")
