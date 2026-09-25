#!/root/BORIS/backend/venv/bin/python
"""Kill only exact known retired autonomous script argv targets."""
import os, signal
from pathlib import Path
from datetime import datetime, timezone
LOG=Path("/root/BORIS/backend/logs/legacy_autonomy_sweeper.log")
TARGETS={
 "ownerless_marketer_75m_v3.py",
 "ownerless_marketer_60m_v2.py",
 "max150_autopilot_soak.sh",
 "production_watch_90m_v1.py",
 "rollout_runtime_integrity_20260903.py",
 "kpi_formula_v13_ownerless.sh",
}
self_pid=os.getpid(); now=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
for p in Path("/proc").iterdir():
 if not p.name.isdigit(): continue
 pid=int(p.name)
 if pid in {self_pid, os.getppid()}: continue
 try:
  raw=(p/"cmdline").read_bytes()
  argv=[x.decode(errors="ignore") for x in raw.split(b"\0") if x]
 except Exception:
  continue
 # LEGACY_SWEEPER_EXACT_ARGV1_V2: kill only a real script target passed as
 # argv[1] to its interpreter. Never inspect a bash -lc command string.
 if len(argv) < 2:
  continue
 candidate=argv[1]
 if candidate.startswith("-") or any(ch in candidate for ch in (" ",";","\n","\t")):
  continue
 base=os.path.basename(candidate)
 if base not in TARGETS:
  continue
 try:
  os.kill(pid, signal.SIGTERM)
  with LOG.open("a") as f: f.write(f"{now} TERMINATE_EXACT_ARGV1 pid={pid} target={base} argv={argv[:4]!r}\n")
 except (ProcessLookupError, PermissionError):
  pass
