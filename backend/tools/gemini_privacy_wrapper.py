#!/root/BORIS/backend/venv/bin/python
"""Privacy boundary for all BORIS headless Gemini CLI prompts."""
from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, "/root/BORIS/backend")
from app.services.ai_privacy import safe_external_ai_payload, restore_external_ai_output


def main() -> int:
    real = os.environ.get("BORIS_TRUE_GEMINI", "/usr/bin/gemini")
    args = list(sys.argv[1:])

    # Interactive Gemini is not used for customer automation. Preserve it as-is.
    prompt_index = None
    prompt_value = None
    for i, arg in enumerate(args):
        if arg in {"-p", "--prompt"} and i + 1 < len(args):
            prompt_index = i + 1
            prompt_value = args[prompt_index]
            break
        if arg.startswith("--prompt="):
            prompt_index = i
            prompt_value = arg.split("=", 1)[1]
            break

    if prompt_index is None:
        os.execvpe(real, [real, *args], os.environ)
        return 127

    clean, vault = safe_external_ai_payload(prompt_value)
    if args[prompt_index].startswith("--prompt="):
        args[prompt_index] = "--prompt=" + clean
    else:
        args[prompt_index] = clean

    proc = subprocess.run(
        [real, *args],
        input=sys.stdin.read() if not sys.stdin.isatty() else None,
        text=True,
        capture_output=True,
        env=os.environ,
    )
    out = restore_external_ai_output(proc.stdout or "", vault)
    err = restore_external_ai_output(proc.stderr or "", vault)
    if out:
        sys.stdout.write(out)
    if err:
        sys.stderr.write(err)
    return int(proc.returncode or 0)


if __name__ == "__main__":
    raise SystemExit(main())
