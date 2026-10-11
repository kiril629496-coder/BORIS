#!/usr/bin/env python3
import asyncio
import base64
import hashlib
import hmac
import difflib
import json
import os
import re
import secrets
import shlex
import signal
import shutil
import subprocess
import threading
import time
import stat as statmod
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

VERSION = "1.4.5"
DISABLE_FILE = Path(os.getenv("BORIS_OPS_DISABLE_FILE", "/etc/boris-ops/disabled"))
READ_ONLY_FILE = Path(os.getenv("BORIS_OPS_READ_ONLY_FILE", "/etc/boris-ops/read-only"))
FORCE_READ_ONLY = os.getenv("BORIS_OPS_READ_ONLY", "0") == "1"
DISK_WRITE_BLOCK_PERCENT = int(os.getenv("BORIS_OPS_DISK_WRITE_BLOCK_PERCENT", "92"))
BIND = os.getenv("BORIS_OPS_BIND", "127.0.0.1")
PORT = int(os.getenv("BORIS_OPS_PORT", "8741"))
TOKEN = os.getenv("BORIS_OPS_TOKEN", "")
HMAC_SECRET = os.getenv("BORIS_OPS_HMAC_SECRET", "")
AUDIT = Path(os.getenv("BORIS_OPS_AUDIT", "/var/log/boris-ops/audit.jsonl"))
READ_ROOTS = [Path(p).resolve() for p in os.getenv("BORIS_OPS_READ_ROOTS", "/root/BORIS,/var/www,/etc/nginx,/etc/systemd/system,/var/log").split(",")]
WRITE_ROOTS = [Path(p).resolve() for p in os.getenv("BORIS_OPS_WRITE_ROOTS", "/root/BORIS").split(",")]
ROOTS = READ_ROOTS
PROTECTED_WRITE_PATHS = {Path("/root/BORIS/boris_ops/agent.py").resolve()}
NONCES = {}
NONCE_FILE = Path(os.getenv("BORIS_OPS_NONCE_FILE", "/var/lib/boris-ops/nonces.json"))
NONCE_LOCK = threading.Lock()
JOBS = {}
JOB_PROCS = {}
JOB_DIR = Path(os.getenv("BORIS_OPS_JOB_DIR", "/var/lib/boris-ops/jobs"))
FRONTEND_RESULT_DIR = Path(os.getenv("BORIS_OPS_FRONTEND_RESULT_DIR", "/var/lib/boris-ops/frontend-build-results"))
SCRIPT_DIR = Path(os.getenv("BORIS_OPS_SCRIPT_DIR", "/var/lib/boris-ops/scripts"))
TRASH_DIR = Path(os.getenv("BORIS_OPS_TRASH_DIR", "/var/lib/boris-ops/trash"))
RELEASE_DIR = Path(os.getenv("BORIS_OPS_RELEASE_DIR", "/root/BORIS/boris_ops/releases")).resolve()
UPDATE_REQUEST = Path("/var/lib/boris-ops/update-request.json")
UPDATE_STATUS = Path("/var/lib/boris-ops/update-status.json")
SELF_UPDATE_GATE = Path(os.getenv("BORIS_OPS_SELF_UPDATE_GATE", "/etc/boris-ops/allow-self-update.json"))
MAX_SCRIPT = 64 * 1024
ALLOW_DANGEROUS_SCRIPTS = os.getenv("BORIS_OPS_ALLOW_DANGEROUS_SCRIPTS", "0") == "1"
MAX_BODY = 1024 * 1024
MAX_OUTPUT = 512 * 1024
MAX_CONCURRENT_REQUESTS = int(os.getenv("BORIS_OPS_MAX_CONCURRENT_REQUESTS", "12"))
REQUEST_SEMAPHORE = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)
RATE_LIMIT_PER_MIN = min(max(float(os.getenv("BORIS_OPS_RATE_LIMIT_PER_MIN", "120")),1.0),6000.0)
RATE_LIMIT_BURST = min(max(float(os.getenv("BORIS_OPS_RATE_LIMIT_BURST", "30")),1.0),1000.0)
_RATE_LOCK = threading.Lock()
_RATE_TOKENS = RATE_LIMIT_BURST
_RATE_LAST = time.monotonic()
MAX_CONCURRENT_JOBS = min(max(int(os.getenv("BORIS_OPS_MAX_CONCURRENT_JOBS", "3")),1),8)
JOB_SEMAPHORE = threading.BoundedSemaphore(MAX_CONCURRENT_JOBS)
SEARCH_MAX_FILES = min(max(int(os.getenv("BORIS_OPS_SEARCH_MAX_FILES", "4000")),100),20000)
SEARCH_MAX_SECONDS = min(max(float(os.getenv("BORIS_OPS_SEARCH_MAX_SECONDS", "8")),1.0),30.0)
SEARCH_SKIP_DIRS = {'.git','node_modules','.next','.venv','venv','__pycache__','.pytest_cache','dist','build','.boris-builds','backups'}

SAFE_COMMANDS = {"uptime", "df", "free", "ps", "ss", "sleep"}

SERVICE_ALLOWLIST = {
    "boris-backend.service", "boris-backend-replica.service", "boris-frontend.service",
    "boris-background-worker.service", "boris-crm-sync.service", "boris-deploy.service",
    "boris-dispatcher.service", "boris-inbox.service", "boris-ops.service",
    "boris-telephony-runtime.service", "boris-watchdog.service", "nginx.service",
    "postgresql@16-main.service"
}

SERVICE_ACTIONS_ALLOWED = {
    "boris-backend.service": {"status","restart","start","stop"},
    "boris-backend-replica.service": {"status","restart"},
    "boris-frontend.service": {"status","restart","start","stop"},
    "boris-background-worker.service": {"status","restart"},
    "boris-crm-sync.service": {"status","restart"},
    "boris-telephony-runtime.service": {"status","restart"},
    "boris-watchdog.service": {"status","restart"},
    "nginx.service": {"status","reload"},
    "postgresql@16-main.service": {"status"},
    "boris-ops.service": {"status"},
    "boris-deploy.service": {"status"},
    "boris-dispatcher.service": {"status"},
    "boris-inbox.service": {"status"},
}
TOOL_RISK = {
    "health":"READ", "exec":"READ", "file_list":"READ", "file_stat":"READ", "file_read":"READ", "file_download":"READ", "file_trash":"READ", "file_search":"READ",
    "logs":"READ", "job_get":"READ", "job_list":"READ", "self_update_status":"READ", "boris_health":"READ", "host_diagnostics":"READ", "audit_recent":"READ", "runtime_capabilities":"READ", "nginx_validate":"READ", "process_list":"READ", "http_check":"READ", "postgres_check":"READ",
    "file_diff":"READ", "file_validate":"READ", "file_write":"SAFE_WRITE", "file_patch":"SAFE_WRITE", "file_copy":"SAFE_WRITE", "file_move":"SAFE_WRITE",
    "file_delete":"SAFE_WRITE", "file_restore":"SAFE_WRITE", "file_upload":"SAFE_WRITE", "job_cancel":"SAFE_WRITE",
    "service":"DEPLOY", "boris_action":"DEPLOY", "self_update":"DANGEROUS", "script_run":"DANGEROUS"
}

BORIS_ACTIONS = {
    "restart_backend": (["python3", "/root/BORIS/boris_ops/rolling_backend_restart.py"], "/root/BORIS/boris_ops", 180),
    "restart_frontend": (["sudo", "-n", "systemctl", "restart", "boris-frontend.service"], None, 90),
    "reload_nginx": (["python3", "/root/BORIS/boris_ops/reload_nginx_safe.py"], "/root/BORIS/boris_ops", 60),
    "frontend_build": (["bash", "scripts/boris-frontend-build.sh"], "/root/BORIS/frontend", 1800),
    "frontend_deploy": ([], "/root/BORIS/frontend", 1800),
    "backend_scoped_deploy": ([], "/root/BORIS/backend", 1800),
    "messenger_p0_selftest": ([], "/root/BORIS/releases/backend/current/backend", 1800),
    "messenger_migration_status": (["/root/BORIS/backend/venv/bin/python", "/root/BORIS/backend/migrations/run_all.py", "status"], "/root/BORIS/backend", 1800),
    "messenger_migration_verify": (["/root/BORIS/backend/venv/bin/python", "/root/BORIS/backend/migrations/run_all.py", "verify"], "/root/BORIS/backend", 1800),
    "messenger_migration_060": (["/root/BORIS/backend/venv/bin/python", "/root/BORIS/backend/migrations/run_all.py", "apply", "--from", "060", "--to", "060"], "/root/BORIS/backend", 1800),
    "messenger_backend_tests": (["/root/BORIS/backend/venv/bin/python", "-m", "pytest", "/root/BORIS/backend/tests/test_internal_messenger_contract.py", "-q"], "/root/BORIS/backend", 1800),
}


def run_messenger_p0_selftest():
    cwd = Path("/root/BORIS/releases/backend/current/backend").resolve()
    if not cwd.is_dir():
        return {"ok": False, "error": "active_backend_release_missing", "action": "messenger_p0_selftest"}
    env = os.environ.copy()
    env["EXT_API_ALLOW_TEST_HOOKS"] = "1"
    env["BORIS_MESSENGER_TEST_TRANSPORT"] = "stub"
    argv = ["/root/BORIS/backend/venv/bin/python", "-m", "app.ext_api.selftest", "--selector", "messenger_p0", "--account", "synthetic"]
    started = time.time()
    try:
        cp = subprocess.run(argv, shell=False, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=1800)
        return {"ok": cp.returncode == 0, "action": "messenger_p0_selftest", "cwd": str(cwd), "fixed_selector": "messenger_p0", "fixed_account": "synthetic", "fixed_transport": "stub", "test_hooks": True, "returncode": cp.returncode, "stdout": cp.stdout[-MAX_OUTPUT:], "stderr": cp.stderr[-MAX_OUTPUT:], "duration": round(time.time() - started, 3)}
    except subprocess.TimeoutExpired as e:
        return {"ok": False, "action": "messenger_p0_selftest", "cwd": str(cwd), "fixed_selector": "messenger_p0", "fixed_account": "synthetic", "fixed_transport": "stub", "test_hooks": True, "error": "timeout", "returncode": 124, "stdout": (e.stdout or "")[-MAX_OUTPUT:] if isinstance(e.stdout, str) else "", "stderr": (e.stderr or "")[-MAX_OUTPUT:] if isinstance(e.stderr, str) else "", "duration": round(time.time() - started, 3)}

DENY_PATTERNS = [
    re.compile(r"(^|\s)rm\s+-[^\n]*r[^\n]*f", re.I),
    re.compile(r"mkfs|fdisk|parted|dd\s+if=|shutdown|reboot|poweroff|iptables\s+-F|nft\s+flush", re.I),
    re.compile(r"curl[^\n]*\|\s*(bash|sh)|wget[^\n]*\|\s*(bash|sh)", re.I),
]


def json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()


def redact_text(value):
    if not isinstance(value,str): return value
    value=re.sub(r"(?i)(authorization:\s*bearer\s+)[^\s]+", r"\1[REDACTED]", value)
    value=re.sub(r"(?i)\b(token|secret|password|passwd|api[_-]?key|hmac[_-]?secret)\s*[=:]\s*[^\s,;]+", lambda m:m.group(1)+"=[REDACTED]", value)
    return value[:8000]

def sanitize_audit(obj):
    if isinstance(obj,dict): return {k:("[REDACTED]" if k.lower() in {"token","secret","password","passwd","api_key","hmac_secret","authorization"} else sanitize_audit(v)) for k,v in obj.items()}
    if isinstance(obj,list): return [sanitize_audit(x) for x in obj[:100]]
    if isinstance(obj,str): return redact_text(obj)
    return obj


def audit(event):
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    event = sanitize_audit({"ts": time.time(), **event})
    with AUDIT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def path_allowed(raw):
    p = Path(raw).resolve()
    return p, any(p == r or r in p.parents for r in READ_ROOTS)

def path_write_allowed(raw):
    p = Path(raw).resolve()
    readable = any(p == r or r in p.parents for r in READ_ROOTS)
    writable = any(p == r or r in p.parents for r in WRITE_ROOTS)
    protected = p in PROTECTED_WRITE_PATHS
    return p, bool(readable and writable and not protected)


def _persist_nonces_locked(now=None):
    now = int(time.time()) if now is None else int(now)
    for n, exp in list(NONCES.items()):
        if exp < now:
            NONCES.pop(n, None)
    NONCE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = NONCE_FILE.with_name(NONCE_FILE.name + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(NONCES, separators=(",", ":")), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, NONCE_FILE)
    os.chmod(NONCE_FILE, 0o600)


def load_nonces():
    now = int(time.time())
    with NONCE_LOCK:
        NONCES.clear()
        try:
            raw = json.loads(NONCE_FILE.read_text(encoding="utf-8")) if NONCE_FILE.exists() else {}
            if isinstance(raw, dict):
                for nonce, exp in raw.items():
                    if isinstance(nonce, str) and nonce and isinstance(exp, (int, float)) and int(exp) >= now:
                        NONCES[nonce] = int(exp)
        except Exception as e:
            audit({"operation":"nonce.load.error","error":str(e)[:200]})
        _persist_nonces_locked(now)


def verify_auth(headers, body):
    if not TOKEN or not HMAC_SECRET:
        return False, "server_not_configured"
    auth = headers.get("Authorization", "")
    if auth != f"Bearer {TOKEN}":
        return False, "bad_token"
    ts = headers.get("X-Boris-Timestamp", "")
    nonce = headers.get("X-Boris-Nonce", "")
    sig = headers.get("X-Boris-Signature", "")
    try:
        ti = int(ts)
    except Exception:
        return False, "bad_timestamp"
    now = int(time.time())
    if abs(now - ti) > 90:
        return False, "expired"
    if not nonce:
        return False, "replay"
    expected = hmac.new(HMAC_SECRET.encode(), (ts + "\n" + nonce + "\n").encode() + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return False, "bad_signature"
    with NONCE_LOCK:
        for n, exp in list(NONCES.items()):
            if exp < now:
                NONCES.pop(n, None)
        if nonce in NONCES:
            return False, "replay"
        NONCES[nonce] = now + 180
        try:
            _persist_nonces_locked(now)
        except Exception as e:
            NONCES.pop(nonce, None)
            audit({"operation":"nonce.persist.error","error":str(e)[:200]})
            return False, "nonce_persist_failed"
    return True, "ok"


def validate_command(command):
    if len(command) > 8000 or "\x00" in command:
        return False, "invalid_command"
    if any(x in command for x in [";", "&&", "||", "|", ">", "<", "`", "$(", "\n", "\r"]):
        return False, "shell_operator_blocked"
    for pat in DENY_PATTERNS:
        if pat.search(command):
            return False, "dangerous_command"
    try:
        argv = shlex.split(command)
    except Exception:
        return False, "parse_error"
    if not argv:
        return False, "empty"
    first = Path(argv[0]).name
    if first == "sudo" and len(argv) > 1:
        first = Path(argv[1]).name
    if first not in SAFE_COMMANDS:
        return False, "command_not_allowed"
    if first == "systemctl":
        if len(argv) != 3:
            return False, "invalid_systemctl_shape"
        action,service=argv[1],argv[2]
        if service not in SERVICE_ALLOWLIST:
            return False, "service_not_allowed"
        if action not in {"status","is-active"}:
            return False, "service_mutation_requires_service_tool"
    if first == "journalctl":
        if "-u" not in argv:
            return False, "journal_service_required"
        try: service=argv[argv.index("-u")+1]
        except Exception: return False, "journal_service_required"
        if service not in SERVICE_ALLOWLIST:
            return False, "service_not_allowed"
        allowed_flags={"journalctl","-u","-n","--no-pager","--since","--until","-p"}
        # Reject output/file altering flags; values are allowed only after known flags.
        for tok in argv[1:]:
            if tok.startswith("--output") or tok.startswith("--file") or tok in {"-f","--follow","--vacuum-size","--vacuum-time","--rotate"}:
                return False, "journal_option_not_allowed"
    if first == "sleep":
        if len(argv) != 2:
            return False, "invalid_sleep"
        try: seconds=float(argv[1])
        except Exception: return False, "invalid_sleep"
        if seconds < 0 or seconds > 300:
            return False, "sleep_out_of_range"
    return True, "ok"


def rate_allowed():
    global _RATE_TOKENS,_RATE_LAST
    now=time.monotonic()
    with _RATE_LOCK:
        elapsed=max(0.0,now-_RATE_LAST); _RATE_LAST=now
        _RATE_TOKENS=min(RATE_LIMIT_BURST,_RATE_TOKENS+elapsed*(RATE_LIMIT_PER_MIN/60.0))
        if _RATE_TOKENS < 1.0: return False
        _RATE_TOKENS-=1.0; return True


def self_update_gate_allows(expected):
    try:
        gate=json.loads(SELF_UPDATE_GATE.read_text(encoding="utf-8"))
        return float(gate.get("expires_at",0)) >= time.time() and hmac.compare_digest(str(gate.get("sha256","")).lower(),str(expected).lower())
    except Exception:
        return False


def write_guard():
    if DISABLE_FILE.exists():
        return False, "write_disabled"
    if FORCE_READ_ONLY or READ_ONLY_FILE.exists():
        return False, "read_only_mode"
    try:
        import shutil
        usage = shutil.disk_usage("/")
        pct = int((usage.used / usage.total) * 100)
        if pct >= DISK_WRITE_BLOCK_PERCENT:
            return False, f"disk_pressure_{pct}pct"
    except Exception:
        pass
    return True, "ok"


def run_command(command, cwd=None, timeout=30):
    ok, reason = validate_command(command)
    if not ok:
        return {"ok": False, "error": reason, "returncode": 126}
    if cwd:
        p, allowed = path_allowed(cwd)
        if not allowed:
            return {"ok": False, "error": "cwd_not_allowed", "returncode": 126}
        cwd = str(p)
    started = time.time()
    try:
        argv = shlex.split(command)
        cp = subprocess.run(argv, shell=False, cwd=cwd, capture_output=True, text=True, timeout=min(max(int(timeout),1),1800))
        out, err = cp.stdout[-MAX_OUTPUT:], cp.stderr[-MAX_OUTPUT:]
        return {"ok": cp.returncode == 0, "returncode": cp.returncode, "stdout": out, "stderr": err, "duration": round(time.time()-started,3)}
    except subprocess.TimeoutExpired as e:
        return {"ok": False, "error": "timeout", "returncode": 124, "stdout": (e.stdout or "")[-MAX_OUTPUT:] if isinstance(e.stdout,str) else "", "stderr": (e.stderr or "")[-MAX_OUTPUT:] if isinstance(e.stderr,str) else "", "duration": round(time.time()-started,3)}


def run_trusted_argv(argv, cwd=None, timeout=60):
    if not isinstance(argv,list) or not argv or any(not isinstance(x,str) or "\x00" in x for x in argv):
        return {"ok":False,"error":"invalid_trusted_argv","returncode":126}
    if cwd:
        cp,allowed=path_allowed(cwd)
        if not allowed: return {"ok":False,"error":"cwd_not_allowed","returncode":126}
        cwd=str(cp)
    started=time.time()
    try:
        cp=subprocess.run(argv,shell=False,cwd=cwd,capture_output=True,text=True,timeout=min(max(int(timeout),1),1800))
        return {"ok":cp.returncode==0,"returncode":cp.returncode,"stdout":cp.stdout[-MAX_OUTPUT:],"stderr":cp.stderr[-MAX_OUTPUT:],"duration":round(time.time()-started,3)}
    except subprocess.TimeoutExpired as e:
        return {"ok":False,"error":"timeout","returncode":124,"stdout":(e.stdout or "")[-MAX_OUTPUT:] if isinstance(e.stdout,str) else "","stderr":(e.stderr or "")[-MAX_OUTPUT:] if isinstance(e.stderr,str) else "","duration":round(time.time()-started,3)}


def _frontend_result_path(jid):
    return FRONTEND_RESULT_DIR / (str(jid) + ".json")


def _read_frontend_result(jid):
    try:
        fp = _frontend_result_path(jid)
        if not fp.is_file():
            return None
        data = json.loads(fp.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and data.get("job_id") == jid else None
    except Exception:
        return None


def _write_frontend_result(jid, result):
    FRONTEND_RESULT_DIR.mkdir(parents=True, exist_ok=True)
    fp = _frontend_result_path(jid)
    tmp = fp.with_name(fp.name + ".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, fp)


def _write_frontend_build_wrapper(jid, unit):
    SCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    wrapper = SCRIPT_DIR / (str(jid) + ".frontend-build.py")
    result_path = _frontend_result_path(jid)
    code = f'''#!/usr/bin/env python3
import json, os, re, subprocess, sys, time
from pathlib import Path
job_id = {jid!r}
unit = {unit!r}
result_path = Path({str(result_path)!r})
started_at = time.time()
cp = subprocess.run(["/bin/bash", "/root/BORIS/frontend/scripts/boris-frontend-build.sh"], cwd="/root/BORIS/frontend", capture_output=True, text=True)
combined = (cp.stdout or "") + "\\n" + (cp.stderr or "")
artifact = ""
source_hash = ""
for line in combined.splitlines():
    if line.startswith("ARTIFACT="):
        artifact = line.split("=", 1)[1].strip()
    elif line.startswith("SNAPSHOT_SOURCE_HASH="):
        source_hash = line.split("=", 1)[1].strip()
build_id = ""
if artifact:
    try:
        build_id = (Path(artifact) / "BUILD_ID").read_text(encoding="utf-8").strip()
    except Exception:
        build_id = ""
status = "success" if cp.returncode == 0 else "failed"
payload = {{"job_id": job_id, "unit": unit, "status": status, "returncode": cp.returncode, "artifact": artifact, "build_id": build_id, "snapshot_source_hash": source_hash, "started_at": started_at, "finished_at": time.time()}}
result_path.parent.mkdir(parents=True, exist_ok=True)
tmp = result_path.with_name(result_path.name + ".tmp")
tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
os.replace(tmp, result_path)
sys.stdout.write((cp.stdout or "")[-524288:])
sys.stderr.write((cp.stderr or "")[-524288:])
sys.exit(cp.returncode)
'''
    wrapper.write_text(code, encoding="utf-8")
    os.chmod(wrapper, 0o700)
    return wrapper


def _durable_frontend_show(unit):
    try:
        cp = subprocess.run(["systemctl", "show", unit, "--no-pager", "--property=ActiveState,SubState,ExecMainStatus,Result"], capture_output=True, text=True, timeout=8)
        values = {}
        for line in cp.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                values[k] = v.strip()
        if cp.returncode != 0:
            return {"ok": False, "error": "unit_query_failed", "stderr": cp.stderr[-1000:]}
        active = values.get("ActiveState", "")
        sub = values.get("SubState", "")
        result = values.get("Result", "")
        try:
            main_status = int(values.get("ExecMainStatus", "0") or 0)
        except Exception:
            main_status = 1
        if active in {"activating", "active", "deactivating"}:
            return {"ok": True, "running": True, "active_state": active, "sub_state": sub, "result": result, "exec_main_status": main_status}
        return {"ok": True, "running": False, "active_state": active, "sub_state": sub, "result": result, "exec_main_status": main_status}
    except Exception as e:
        return {"ok": False, "error": "unit_query_failed", "detail": str(e)[:300]}


def _reconcile_durable_frontend_job(j):
    unit = str(j.get("unit", ""))
    jid = str(j.get("id", ""))
    if not unit or not jid:
        return j
    result = _read_frontend_result(jid)
    if result:
        terminal = result.get("status") in {"success", "failed"}
        if terminal:
            j.update({"ok": result.get("status") == "success", "returncode": int(result.get("returncode", 1)), "status": result.get("status"), "artifact": result.get("artifact", ""), "build_id": result.get("build_id", ""), "snapshot_source_hash": result.get("snapshot_source_hash", ""), "started_at": result.get("started_at", j.get("started_at")), "finished_at": result.get("finished_at", time.time()), "result_file": str(_frontend_result_path(jid))})
            return j
    state = _durable_frontend_show(unit)
    if state.get("ok"):
        j["unit_state"] = state
        if state.get("running"):
            j["status"] = "running"
            return j
        rc = int(state.get("exec_main_status", 1))
        success = state.get("result") in {"", "success"} and rc == 0
        terminal = {"job_id": jid, "unit": unit, "status": "success" if success else "failed", "returncode": rc, "artifact": j.get("artifact", ""), "build_id": j.get("build_id", ""), "snapshot_source_hash": j.get("snapshot_source_hash", ""), "started_at": j.get("started_at", time.time()), "finished_at": time.time()}
        _write_frontend_result(jid, terminal)
        j.update({"ok": success, "returncode": rc, "status": terminal["status"], "finished_at": terminal["finished_at"], "result_file": str(_frontend_result_path(jid))})
        return j
    age = time.time() - float(j.get("started_at", j.get("created_at", time.time())))
    if age >= float(j.get("timeout", 1800)) + 30:
        j.update({"ok": False, "status": "unknown", "error": "durable_result_missing", "finished_at": time.time()})
    return j


def start_durable_frontend_job(timeout=1800):
    existing = [j for j in JOBS.values() if j.get("command") == "frontend_build" and j.get("status") in {"queued", "running"}]
    if existing:
        j = _reconcile_durable_frontend_job(existing[0])
        if j.get("status") in {"queued", "running"}:
            persist_job(j["id"])
            return {"ok": True, "deferred": True, "job_id": j["id"], "status": j["status"], "unit": j.get("unit")}
    jid = "job_" + uuid.uuid4().hex[:16]
    unit = "boris-frontend-build@" + jid[4:] + ".service"
    job = {"id": jid, "status": "queued", "created_at": time.time(), "command": "frontend_build", "cwd": "/root/BORIS/frontend", "timeout": min(max(int(timeout), 1), 1800), "trusted": True, "unit": unit, "log_source": "journald:" + unit}
    JOBS[jid] = job
    persist_job(jid)
    wrapper = _write_frontend_build_wrapper(jid, unit)
    argv = ["sudo", "-n", "systemd-run", "--no-block", "--unit=" + unit, "--property=User=borisops", "--property=Group=borisops", "--property=WorkingDirectory=/root/BORIS/frontend", "--property=TimeoutStartSec=" + str(min(max(int(timeout), 1), 1800)), "--property=KillMode=process", "--property=Nice=15", "/root/BORIS/backend/venv/bin/python", str(wrapper)]
    try:
        cp = subprocess.run(argv, cwd="/root/BORIS/frontend", capture_output=True, text=True, timeout=20)
        if cp.returncode != 0:
            job.update({"ok": False, "status": "failed", "returncode": cp.returncode, "error": "durable_dispatch_failed", "stderr": cp.stderr[-MAX_OUTPUT:], "finished_at": time.time()})
        else:
            job.update({"status": "running", "started_at": time.time(), "dispatch_stdout": cp.stdout[-2000:]})
    except Exception as e:
        job.update({"ok": False, "status": "failed", "returncode": 127, "error": "durable_dispatch_failed", "detail": str(e)[:300], "finished_at": time.time()})
    persist_job(jid)
    return {"ok": job.get("status") in {"queued", "running"}, "job_id": jid, "status": job.get("status"), "unit": unit, "error": job.get("error")}


def start_trusted_job(argv, cwd=None, timeout=1800, label="trusted"):
    if cwd:
        cp,allowed=path_allowed(cwd)
        if not allowed: return {"ok":False,"error":"cwd_not_allowed"}
        cwd=str(cp)
    jid="job_"+uuid.uuid4().hex[:16]
    JOBS[jid]={"id":jid,"status":"queued","created_at":time.time(),"command":label,"cwd":cwd,"timeout":min(max(int(timeout),1),1800),"trusted":True}; persist_job(jid)
    def worker():
        acquired=JOB_SEMAPHORE.acquire(timeout=max(5,JOBS[jid]["timeout"]))
        if not acquired:
            JOBS[jid].update({"ok":False,"status":"timeout","error":"job_queue_timeout","returncode":124,"finished_at":time.time()}); persist_job(jid); return
        if JOBS[jid].get("status")=="cancelled": JOB_SEMAPHORE.release(); return
        JOBS[jid]["status"]="running"; JOBS[jid]["started_at"]=time.time(); persist_job(jid); proc=None
        try:
            proc=subprocess.Popen(argv,cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True); JOB_PROCS[jid]=proc
            try:
                out,err=proc.communicate(timeout=JOBS[jid]["timeout"]); rc=proc.returncode
                if JOBS[jid].get("status")=="cancelled": JOBS[jid].update({"ok":False,"returncode":rc,"stdout":out[-MAX_OUTPUT:],"stderr":err[-MAX_OUTPUT:],"error":"cancelled"})
                else: JOBS[jid].update({"ok":rc==0,"returncode":rc,"stdout":out[-MAX_OUTPUT:],"stderr":err[-MAX_OUTPUT:],"status":"success" if rc==0 else "failed"})
            except subprocess.TimeoutExpired:
                try: os.killpg(proc.pid,signal.SIGTERM)
                except Exception: proc.kill()
                out,err=proc.communicate(timeout=5); JOBS[jid].update({"ok":False,"error":"timeout","returncode":124,"stdout":out[-MAX_OUTPUT:],"stderr":err[-MAX_OUTPUT:],"status":"timeout"})
        except Exception as e: JOBS[jid].update({"ok":False,"error":"spawn_failed","detail":str(e)[:300],"status":"failed","returncode":127})
        finally:
            JOB_PROCS.pop(jid,None); JOBS[jid]["finished_at"]=time.time(); persist_job(jid); audit({"request_id":jid,"operation":"trusted_job","command":label,"result":JOBS[jid]["status"],"returncode":JOBS[jid].get("returncode")}); JOB_SEMAPHORE.release()
    import threading; threading.Thread(target=worker,daemon=True).start(); return JOBS[jid]


def run_script(content, interpreter="bash", cwd=None, timeout=60):
    if not ALLOW_DANGEROUS_SCRIPTS:
        return {"ok":False,"error":"script_run_disabled","returncode":126}
    guard_ok,guard_reason=write_guard()
    if not guard_ok: return {"ok":False,"error":guard_reason,"returncode":126}
    if interpreter not in {"bash","python3"}: return {"ok":False,"error":"interpreter_not_allowed","returncode":126}
    if not isinstance(content,str) or not content.strip() or len(content.encode()) > MAX_SCRIPT: return {"ok":False,"error":"invalid_script","returncode":126}
    for pat in DENY_PATTERNS:
        if pat.search(content): return {"ok":False,"error":"dangerous_script","returncode":126}
    if cwd:
        cp,allowed=path_allowed(cwd)
        if not allowed: return {"ok":False,"error":"cwd_not_allowed","returncode":126}
        cwd=str(cp)
    SCRIPT_DIR.mkdir(parents=True,exist_ok=True)
    fp=SCRIPT_DIR/("script_"+uuid.uuid4().hex+(".py" if interpreter=="python3" else ".sh"))
    fp.write_text(content,encoding="utf-8"); os.chmod(fp,0o700); started=time.time()
    try:
        cp=subprocess.run([interpreter,str(fp)],shell=False,cwd=cwd,capture_output=True,text=True,timeout=min(max(int(timeout),1),600))
        return {"ok":cp.returncode==0,"returncode":cp.returncode,"stdout":cp.stdout[-MAX_OUTPUT:],"stderr":cp.stderr[-MAX_OUTPUT:],"duration":round(time.time()-started,3)}
    except subprocess.TimeoutExpired as e:
        return {"ok":False,"error":"timeout","returncode":124,"stdout":(e.stdout or "")[-MAX_OUTPUT:] if isinstance(e.stdout,str) else "","stderr":(e.stderr or "")[-MAX_OUTPUT:] if isinstance(e.stderr,str) else "","duration":round(time.time()-started,3)}
    finally:
        try: fp.unlink()
        except Exception: pass


def persist_job(jid):
    try:
        JOB_DIR.mkdir(parents=True, exist_ok=True)
        tmp=JOB_DIR/(jid+".json.tmp")
        tmp.write_text(json.dumps(JOBS[jid],ensure_ascii=False),encoding="utf-8")
        os.replace(tmp,JOB_DIR/(jid+".json"))
    except Exception as e:
        audit({"operation":"job.persist.error","job_id":jid,"error":str(e)[:200]})


def load_jobs():
    JOB_DIR.mkdir(parents=True, exist_ok=True)
    for fp in JOB_DIR.glob("job_*.json"):
        try:
            j=json.loads(fp.read_text(encoding="utf-8"))
            was_active = j.get("status") in {"queued","running"}
            durable_frontend = was_active and j.get("command") == "frontend_build" and j.get("unit")
            if durable_frontend:
                _reconcile_durable_frontend_job(j)
                if j.get("status") in {"queued","running"}:
                    j.pop("error", None)
            elif was_active:
                j["status"]="interrupted"
                j["error"]="agent_restarted"
                j["finished_at"]=time.time()
            JOBS[j["id"]]=j
            if was_active:
                persist_job(j["id"])
        except Exception:
            pass


def start_job(command, cwd=None, timeout=1800):
    ok, reason = validate_command(command)
    if not ok:
        return {"ok":False,"error":reason}
    if cwd:
        cp, allowed = path_allowed(cwd)
        if not allowed:
            return {"ok":False,"error":"cwd_not_allowed"}
        cwd=str(cp)
    jid = "job_" + uuid.uuid4().hex[:16]
    JOBS[jid] = {"id": jid, "status": "queued", "created_at": time.time(), "command": command, "cwd":cwd, "timeout":min(max(int(timeout),1),1800)}
    persist_job(jid)
    def worker():
        acquired=JOB_SEMAPHORE.acquire(timeout=max(5,JOBS[jid]["timeout"]))
        if not acquired:
            JOBS[jid].update({"ok":False,"status":"timeout","error":"job_queue_timeout","returncode":124,"finished_at":time.time()}); persist_job(jid); return
        if JOBS[jid].get("status")=="cancelled": JOB_SEMAPHORE.release(); return
        JOBS[jid]["status"] = "running"; JOBS[jid]["started_at"] = time.time(); persist_job(jid)
        argv=shlex.split(command); proc=None
        try:
            proc=subprocess.Popen(argv,cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
            JOB_PROCS[jid]=proc
            try:
                out,err=proc.communicate(timeout=JOBS[jid]["timeout"])
                rc=proc.returncode
                if JOBS[jid].get("status") == "cancelled":
                    JOBS[jid].update({"ok":False,"returncode":rc,"stdout":out[-MAX_OUTPUT:],"stderr":err[-MAX_OUTPUT:],"error":"cancelled"})
                else:
                    JOBS[jid].update({"ok":rc==0,"returncode":rc,"stdout":out[-MAX_OUTPUT:],"stderr":err[-MAX_OUTPUT:]})
                    JOBS[jid]["status"]="success" if rc==0 else "failed"
            except subprocess.TimeoutExpired:
                try: os.killpg(proc.pid, signal.SIGTERM)
                except Exception: proc.kill()
                out,err=proc.communicate(timeout=5)
                JOBS[jid].update({"ok":False,"error":"timeout","returncode":124,"stdout":out[-MAX_OUTPUT:],"stderr":err[-MAX_OUTPUT:],"status":"timeout"})
        except Exception as e:
            JOBS[jid].update({"ok":False,"error":"spawn_failed","detail":str(e)[:300],"status":"failed","returncode":127})
        finally:
            JOB_PROCS.pop(jid,None); JOBS[jid]["finished_at"]=time.time(); persist_job(jid)
            audit({"request_id": jid, "operation": "job", "command": command, "result": JOBS[jid]["status"], "returncode": JOBS[jid].get("returncode")}); JOB_SEMAPHORE.release()
    import threading
    threading.Thread(target=worker, daemon=True).start()
    return JOBS[jid]


def cancel_job(jid):
    j=JOBS.get(jid)
    if not j:
        return {"ok":False,"error":"not_found"}
    if j.get("status") not in {"queued","running"}:
        return {"ok":False,"error":"not_running","status":j.get("status")}
    proc=JOB_PROCS.get(jid)
    if proc:
        try: os.killpg(proc.pid, signal.SIGTERM)
        except Exception:
            try: proc.terminate()
            except Exception: pass
    j.update({"ok":False,"status":"cancelled","error":"cancelled","finished_at":time.time()}); persist_job(jid)
    audit({"operation":"job.cancel","job_id":jid})
    return {"ok":True,"job_id":jid,"status":"cancelled"}


def unique_backup_path(p):
    return p.with_name(p.name + ".bak." + time.strftime("%Y%m%d-%H%M%S") + "." + secrets.token_hex(4))


def preserve_mode(source, temp):
    try: os.chmod(temp, statmod.S_IMODE(source.stat().st_mode))
    except Exception: pass

def bounded_diff(old_text,new_text,path="file"):
    text="".join(difflib.unified_diff(old_text.splitlines(True),new_text.splitlines(True),fromfile=path,tofile=path+".new",n=3))
    return {"text":text[:65536],"truncated":len(text)>65536}

def validate_candidate(path,preset):
    preset=str(preset or "").lower().strip()
    if not preset:return {"ok":True,"validator":None}
    try:
        if preset=="json": json.loads(Path(path).read_text(encoding="utf-8")); return {"ok":True,"validator":"json"}
        if preset=="python":
            compile(Path(path).read_text(encoding="utf-8"),str(path),"exec"); return {"ok":True,"validator":"python"}
        if preset in {"sh","bash"}:
            cp=subprocess.run(["bash","-n",str(path)],capture_output=True,text=True,timeout=30); return {"ok":cp.returncode==0,"validator":"sh","stderr":cp.stderr[-4000:]}
        return {"ok":False,"error":"validator_not_allowed","validator":preset}
    except Exception as e:return {"ok":False,"error":"validation_failed","validator":preset,"detail":str(e)[:500]}

def bounded_file_search(root, term, hit_limit=100, max_files=None, max_seconds=None):
    term=str(term or "")
    if not term or len(term)>200: return {"ok":False,"error":"invalid_search"}
    hit_limit=min(max(int(hit_limit or 100),1),200)
    max_files=min(max(int(max_files or SEARCH_MAX_FILES),100),SEARCH_MAX_FILES)
    max_seconds=min(max(float(max_seconds or SEARCH_MAX_SECONDS),1.0),SEARCH_MAX_SECONDS)
    started=time.monotonic(); hits=[]; scanned=0; truncated=False; reason=None
    try:
        for dirpath,dirs,files in os.walk(root,topdown=True,followlinks=False):
            dirs[:] = [d for d in dirs if d not in SEARCH_SKIP_DIRS]
            for name in files:
                if scanned>=max_files: truncated=True; reason='max_files'; break
                if time.monotonic()-started>=max_seconds: truncated=True; reason='timeout'; break
                fp=Path(dirpath)/name; scanned+=1
                try:
                    st=fp.stat()
                    if st.st_size>2*1024*1024 or not fp.is_file(): continue
                    with fp.open('r',encoding='utf-8',errors='ignore') as fh:
                        for no,line in enumerate(fh,1):
                            if term.lower() in line.lower():
                                hits.append({"path":str(fp),"line":no,"text":line.rstrip('\n')[:500]})
                                if len(hits)>=hit_limit: truncated=True; reason='hit_limit'; break
                    if len(hits)>=hit_limit: break
                except (OSError,UnicodeError):
                    continue
            if truncated: break
    except OSError as e:
        return {"ok":False,"error":"search_failed","detail":str(e)[:200],"scanned_files":scanned}
    return {"ok":True,"count":len(hits),"hits":hits,"scanned_files":scanned,"truncated":truncated,"stop_reason":reason,"duration_s":round(time.monotonic()-started,3)}


def file_stat_result(raw):
    p,allowed=path_allowed(raw)
    if not allowed or not p.exists(): return {"ok":False,"error":"path_not_allowed_or_missing"}
    st=p.stat(); result={"ok":True,"path":str(p),"type":"dir" if p.is_dir() else "file","size":st.st_size,"mtime":st.st_mtime,"mode":oct(st.st_mode & 0o777)}
    if p.is_file() and st.st_size <= 16*1024*1024:
        result["sha256"]=hashlib.sha256(p.read_bytes()).hexdigest()
    return result

def file_copy_move(src_raw,dst_raw,move=False,overwrite=False):
    guard_ok,reason=write_guard()
    if not guard_ok:return {"ok":False,"error":reason}
    src,sa=path_write_allowed(src_raw) if move else path_allowed(src_raw); dst,da=path_write_allowed(dst_raw)
    if not sa or not da or not src.exists(): return {"ok":False,"error":"path_not_allowed_or_missing"}
    if src in ROOTS: return {"ok":False,"error":"protected_root"}
    if dst.exists() and not overwrite:return {"ok":False,"error":"destination_exists"}
    dst.parent.mkdir(parents=True,exist_ok=True); backup=None
    if dst.exists():
        backup=unique_backup_path(dst); shutil.copy2(dst,backup) if dst.is_file() else shutil.copytree(dst,backup)
    if move: shutil.move(str(src),str(dst))
    elif src.is_dir(): shutil.copytree(src,dst,dirs_exist_ok=overwrite)
    else: shutil.copy2(src,dst)
    return {"ok":True,"operation":"move" if move else "copy","source":str(src),"destination":str(dst),"backup":str(backup) if backup else None}

def file_soft_delete(raw):
    guard_ok,reason=write_guard()
    if not guard_ok:return {"ok":False,"error":reason}
    p,allowed=path_write_allowed(raw)
    if not allowed or not p.exists():return {"ok":False,"error":"path_not_allowed_or_missing"}
    if p in WRITE_ROOTS:return {"ok":False,"error":"protected_root"}
    TRASH_DIR.mkdir(parents=True,exist_ok=True); tid="trash_"+uuid.uuid4().hex[:16]; dest=TRASH_DIR/(tid+"__"+p.name); shutil.move(str(p),str(dest))
    meta={"trash_id":tid,"original":str(p),"stored":str(dest),"deleted_at":time.time()}; (TRASH_DIR/(tid+".json")).write_text(json.dumps(meta,ensure_ascii=False),encoding="utf-8")
    return {"ok":True,**meta}

def file_restore(tid,destination=None):
    guard_ok,reason=write_guard()
    if not guard_ok:return {"ok":False,"error":reason}
    if not re.fullmatch(r"trash_[0-9a-f]{16}",tid or ""):return {"ok":False,"error":"invalid_trash_id"}
    mf=TRASH_DIR/(tid+".json")
    if not mf.is_file():return {"ok":False,"error":"not_found"}
    meta=json.loads(mf.read_text()); src=Path(meta["stored"]); dst,allowed=path_write_allowed(destination or meta["original"])
    if not allowed or not src.exists():return {"ok":False,"error":"restore_path_invalid"}
    if dst.exists():return {"ok":False,"error":"destination_exists"}
    dst.parent.mkdir(parents=True,exist_ok=True); shutil.move(str(src),str(dst)); meta["restored_at"]=time.time(); meta["restored_to"]=str(dst); mf.write_text(json.dumps(meta,ensure_ascii=False),encoding="utf-8")
    return {"ok":True,"trash_id":tid,"restored_to":str(dst)}


def host_diagnostics_result():
    import shutil
    result={"ok":True,"ts":time.time(),"version":VERSION}
    try:
        result["loadavg"]=list(os.getloadavg())
    except Exception as e:
        result["loadavg_error"]=str(e)[:200]
    try:
        mem={}
        for line in Path('/proc/meminfo').read_text().splitlines():
            if ':' in line:
                k,v=line.split(':',1); mem[k]=v.strip()
        result["memory"]={k:mem.get(k) for k in ["MemTotal","MemAvailable","SwapTotal","SwapFree"]}
    except Exception as e:
        result["memory_error"]=str(e)[:200]
    try:
        du=shutil.disk_usage('/')
        result["disk"]={"total":du.total,"used":du.used,"free":du.free,"percent":round(du.used/du.total*100,1),"write_block_percent":DISK_WRITE_BLOCK_PERCENT}
    except Exception as e:
        result["disk_error"]=str(e)[:200]
    services=["boris-backend.service","boris-backend-replica.service","boris-frontend.service","boris-background-worker.service","nginx.service","postgresql@16-main.service","boris-ops.service"]
    states={}
    for svc in services:
        try:
            cp=subprocess.run(["systemctl","is-active",svc],capture_output=True,text=True,timeout=3)
            states[svc]=cp.stdout.strip() or "unknown"
        except Exception as e: states[svc]="error:"+str(e)[:80]
    result["services"]=states
    try:
        cp=subprocess.run(["pg_isready","-q"],capture_output=True,text=True,timeout=3)
        result["postgresql_ready"]=(cp.returncode==0)
    except FileNotFoundError:
        result["postgresql_ready"]=states.get("postgresql@16-main.service")=="active"
    except Exception as e:
        result["postgresql_ready"]=False; result["postgresql_error"]=str(e)[:200]
    result["write_guard"]={"enabled":write_guard()[0],"reason":write_guard()[1]}
    result["jobs"]={"running":sum(1 for j in JOBS.values() if j.get("status")=="running"),"total":len(JOBS)}
    result["runtime"]={"pid":os.getpid(),"uid":os.getuid(),"gid":os.getgid(),"max_concurrent_requests":MAX_CONCURRENT_REQUESTS,"max_concurrent_jobs":MAX_CONCURRENT_JOBS,"search_max_files":SEARCH_MAX_FILES,"search_max_seconds":SEARCH_MAX_SECONDS,"rate_limit_per_min":RATE_LIMIT_PER_MIN,"rate_limit_burst":RATE_LIMIT_BURST,"read_only":bool(FORCE_READ_ONLY or READ_ONLY_FILE.exists()),"write_guard":write_guard()[1]}
    return result


def audit_recent_result(limit=100):
    limit=min(max(int(limit),1),500)
    if not AUDIT.is_file(): return {"ok":True,"count":0,"events":[]}
    try:
        lines=AUDIT.read_text(encoding="utf-8",errors="replace").splitlines()[-limit:]
        events=[]
        for line in lines:
            try: events.append(sanitize_audit(json.loads(line)))
            except Exception: events.append({"raw":redact_text(line)})
        return {"ok":True,"count":len(events),"events":events}
    except Exception as e:
        return {"ok":False,"error":"audit_read_failed","detail":str(e)[:200]}



def runtime_capabilities_result():
    import shutil
    return {"ok":True,"version":VERSION,"runtime_dependency_on_github":False,"runtime_dependency_on_sentinelx":False,"optional":{"docker":{"installed":bool(shutil.which("docker"))},"git":{"installed":bool(shutil.which("git")),"github_required":False},"nginx":{"installed":bool(shutil.which("nginx") or Path("/usr/sbin/nginx").is_file())},"systemctl":{"installed":bool(shutil.which("systemctl"))}}}

def nginx_validate_result():
    if not Path('/usr/sbin/nginx').is_file():
        return {"ok":True,"available":False,"reason":"nginx_not_installed"}
    result=run_trusted_argv(["sudo","-n","/usr/sbin/nginx","-t"],None,30)
    result["available"]=True
    return result



def process_list_result(limit=50):
    try: limit=min(max(int(limit),1),200)
    except Exception: limit=50
    try:
        cp=subprocess.run(["ps","-eo","pid,ppid,user,stat,%cpu,%mem,etime,comm,args","--sort=-%cpu"],capture_output=True,text=True,timeout=10)
        lines=cp.stdout.splitlines()
        header=lines[0] if lines else ""
        rows=[redact_text(x) for x in lines[1:limit+1]]
        return {"ok":cp.returncode==0,"header":header,"rows":rows,"count":len(rows),"returncode":cp.returncode,"stderr":redact_text(cp.stderr[-1000:])}
    except Exception as e:
        return {"ok":False,"error":"process_list_failed","detail":str(e)[:200]}

HTTP_TARGETS={
    "public":"https://boris-ai.pro/health",
    "ops":"http://127.0.0.1:8741/health",
    "backend_primary":"http://127.0.0.1:8000/health",
    "backend_replica":"http://127.0.0.1:8001/health",
}
def http_check_result(target="public",timeout=5):
    target=str(target or "public")
    if target not in HTTP_TARGETS:return {"ok":False,"error":"target_not_allowed","allowed":sorted(HTTP_TARGETS)}
    try: timeout=min(max(float(timeout),0.5),10.0)
    except Exception: timeout=5.0
    url=HTTP_TARGETS[target]; started=time.time()
    try:
        import urllib.request
        with urllib.request.urlopen(url,timeout=timeout) as r:
            raw=r.read(4096)
            return {"ok":200<=r.status<300,"target":target,"status":r.status,"duration_ms":round((time.time()-started)*1000,1),"content_type":r.headers.get("Content-Type",""),"body_preview":redact_text(raw.decode("utf-8","replace")[:1000])}
    except Exception as e:
        return {"ok":False,"target":target,"duration_ms":round((time.time()-started)*1000,1),"error":str(e)[:300]}

def postgres_check_result():
    exe=shutil.which("pg_isready")
    if not exe:return {"ok":False,"error":"pg_isready_unavailable"}
    started=time.time()
    try:
        cp=subprocess.run([exe,"-q"],capture_output=True,text=True,timeout=8)
        return {"ok":cp.returncode==0,"ready":cp.returncode==0,"returncode":cp.returncode,"duration_ms":round((time.time()-started)*1000,1)}
    except Exception as e:return {"ok":False,"error":"postgres_check_failed","detail":str(e)[:200]}

class Handler(BaseHTTPRequestHandler):
    server_version = "BORISOps/" + VERSION
    def handle_one_request(self):
        acquired = REQUEST_SEMAPHORE.acquire(timeout=15)
        if not acquired:
            self.close_connection = True
            return
        try:
            return super().handle_one_request()
        finally:
            REQUEST_SEMAPHORE.release()
    def log_message(self, fmt, *args):
        pass
    def send_json(self, code, obj):
        b = json_bytes(obj)
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)
    def body(self):
        n = int(self.headers.get("Content-Length", "0") or 0)
        if n > MAX_BODY:
            raise ValueError("body_too_large")
        return self.rfile.read(n)
    def authenticated(self, body=b""):
        ok, reason = verify_auth(self.headers, body)
        if not ok:
            self.send_json(401, {"ok": False, "error": reason})
        return ok
    def do_GET(self):
        u = urlparse(self.path)
        if u.path != "/health" and not rate_allowed(): return self.send_json(429,{"ok":False,"error":"rate_limited"})
        if u.path == "/health":
            return self.send_json(200, {"ok": True, "service": "boris-ops", "version": VERSION, "uptime": int(time.time()-self.server.started_at), "jobs": {"running": sum(1 for j in JOBS.values() if j["status"]=="running"), "total": len(JOBS)}})
        if not self.authenticated(b""):
            return
        if u.path == "/self/update/status":
            if not UPDATE_STATUS.is_file(): return self.send_json(200,{"ok":True,"status":"never_run"})
            try: data=json.loads(UPDATE_STATUS.read_text(encoding="utf-8"))
            except Exception as e: data={"ok":False,"status":"corrupt","error":str(e)[:200]}
            return self.send_json(200,data)
        if u.path == "/version":
            return self.send_json(200, {"ok": True, "version": VERSION, "capabilities": ["health","exec","script.run","jobs","file.read","file.write","file.patch","file.search","file.list","file.stat","file.diff","file.validate","file.copy","file.move","file.delete","file.restore","file.upload","file.download","service","logs","write_guard","boris.actions","dangerous_scripts_gated","self_update_with_rollback","self_update_gate_required","fail_safe_read_only","agent_rate_limit","least_privilege_runtime","separate_read_write_roots","bounded_concurrency","host_diagnostics","audit_recent","runtime_capabilities","nginx_validate","process_list","http_check","postgres_check"], "write_enabled": write_guard()[0], "dangerous_scripts_enabled": ALLOW_DANGEROUS_SCRIPTS, "risk_levels":["READ","SAFE_WRITE","DEPLOY","DANGEROUS"]})
        if u.path == "/host/diagnostics":
            result=host_diagnostics_result(); return self.send_json(200 if result.get("ok") else 503,result)
        if u.path == "/runtime/capabilities":
            return self.send_json(200,runtime_capabilities_result())
        if u.path == "/nginx/validate":
            result=nginx_validate_result(); return self.send_json(200 if result.get("ok") else 400,result)
        if u.path == "/process/list":
            q=parse_qs(u.query); result=process_list_result(q.get("limit",[50])[0]); return self.send_json(200 if result.get("ok") else 500,result)
        if u.path == "/http/check":
            q=parse_qs(u.query); result=http_check_result(q.get("target",["public"])[0],q.get("timeout",[5])[0]); return self.send_json(200 if result.get("ok") else 503,result)
        if u.path == "/postgres/check":
            result=postgres_check_result(); return self.send_json(200 if result.get("ok") else 503,result)
        if u.path == "/audit/recent":
            q=parse_qs(u.query); result=audit_recent_result(q.get("limit",[100])[0]); return self.send_json(200 if result.get("ok") else 500,result)
        if u.path == "/boris/health":
            services=["boris-backend.service","boris-backend-replica.service","boris-frontend.service","boris-background-worker.service","nginx.service","postgresql@16-main.service","boris-ops.service"]
            states={}
            for svc in services:
                cp=subprocess.run(["systemctl","is-active",svc],capture_output=True,text=True,timeout=5)
                states[svc]=cp.stdout.strip() or "unknown"
            import shutil
            du=shutil.disk_usage("/"); disk_pct=round((du.used/du.total)*100,1)
            http_checks={}
            for name,url in {"public":"https://boris-ai.pro/health","ops":"http://127.0.0.1:8741/health"}.items():
                try:
                    import urllib.request
                    with urllib.request.urlopen(url,timeout=5) as r: http_checks[name]={"ok":200<=r.status<300,"status":r.status}
                except Exception as e: http_checks[name]={"ok":False,"error":str(e)[:200]}
            backend_pair_ok = any(states.get(svc)=="active" for svc in ["boris-backend.service","boris-backend-replica.service"])
            required_single = ["boris-frontend.service","boris-background-worker.service","nginx.service","postgresql@16-main.service","boris-ops.service"]
            required_ok = all(states.get(svc)=="active" for svc in required_single)
            http_ok = all(v.get("ok") for v in http_checks.values())
            redundancy_degraded = not all(states.get(svc)=="active" for svc in ["boris-backend.service","boris-backend-replica.service"])
            ok = backend_pair_ok and required_ok and http_ok and disk_pct < DISK_WRITE_BLOCK_PERCENT
            return self.send_json(200 if ok else 503,{"ok":ok,"services":states,"http":http_checks,"disk_percent":disk_pct,"write_enabled":write_guard()[0],"backend_pair_ok":backend_pair_ok,"redundancy_degraded":redundancy_degraded})
        if u.path == "/jobs":
            items=sorted(JOBS.values(),key=lambda j:j.get("created_at",0),reverse=True)[:100]
            return self.send_json(200,{"ok":True,"count":len(items),"jobs":items})
        if u.path.startswith("/jobs/"):
            jid = u.path.rsplit("/",1)[-1]
            return self.send_json(200 if jid in JOBS else 404, JOBS.get(jid, {"ok":False,"error":"not_found"}))
        if u.path == "/file/stat":
            q=parse_qs(u.query); result=file_stat_result(q.get("path",[""])[0]); return self.send_json(200 if result.get("ok") else 403,result)
        if u.path == "/file/download":
            q=parse_qs(u.query); fp,allowed=path_allowed(q.get("path",[""])[0])
            if not allowed or not fp.is_file(): return self.send_json(403,{"ok":False,"error":"path_not_allowed_or_missing"})
            raw=fp.read_bytes()
            if len(raw)>MAX_BODY:return self.send_json(413,{"ok":False,"error":"file_too_large","max_bytes":MAX_BODY})
            return self.send_json(200,{"ok":True,"path":str(fp),"size":len(raw),"sha256":hashlib.sha256(raw).hexdigest(),"content_base64":base64.b64encode(raw).decode()})
        if u.path == "/file/trash":
            items=[]; TRASH_DIR.mkdir(parents=True,exist_ok=True)
            for mf in sorted(TRASH_DIR.glob("trash_*.json"),key=lambda x:x.stat().st_mtime,reverse=True)[:100]:
                try: items.append(json.loads(mf.read_text()))
                except Exception: pass
            return self.send_json(200,{"ok":True,"count":len(items),"items":items})
        if u.path == "/file/list":
            q=parse_qs(u.query); root,allowed=path_allowed(q.get("path",["/root/BORIS"])[0]); limit=min(max(int(q.get("limit",[200])[0]),1),1000)
            if not allowed or not root.is_dir(): return self.send_json(403,{"ok":False,"error":"path_not_allowed_or_missing"})
            items=[]
            try:
                for x in sorted(root.iterdir(),key=lambda y:(not y.is_dir(),y.name.lower()))[:limit]:
                    try: st=x.stat(); items.append({"name":x.name,"path":str(x),"type":"dir" if x.is_dir() else "file","size":st.st_size,"mtime":st.st_mtime})
                    except Exception: pass
            except Exception as e: return self.send_json(400,{"ok":False,"error":"list_failed","detail":str(e)[:200]})
            return self.send_json(200,{"ok":True,"path":str(root),"count":len(items),"items":items})
        if u.path == "/file/read":
            q = parse_qs(u.query)
            p, allowed = path_allowed(q.get("path",[""])[0])
            if not allowed or not p.is_file():
                return self.send_json(403, {"ok":False,"error":"path_not_allowed_or_missing"})
            data = p.read_text(encoding="utf-8", errors="replace")
            return self.send_json(200, {"ok":True,"path":str(p),"content":data[:MAX_OUTPUT],"truncated":len(data)>MAX_OUTPUT})
        if u.path == "/file/search":
            q=parse_qs(u.query); root,allowed=path_allowed(q.get("root",["/root/BORIS"])[0]); term=q.get("q",[""])[0]
            if not allowed or not root.exists(): return self.send_json(400,{"ok":False,"error":"invalid_search"})
            result=bounded_file_search(root,term,q.get("limit",[100])[0],q.get("max_files",[SEARCH_MAX_FILES])[0],q.get("max_seconds",[SEARCH_MAX_SECONDS])[0])
            return self.send_json(200 if result.get("ok") else 400,result)
        return self.send_json(404, {"ok":False,"error":"not_found"})
    def do_POST(self):
        if not rate_allowed(): return self.send_json(429,{"ok":False,"error":"rate_limited"})
        try:
            body = self.body()
        except ValueError as e:
            return self.send_json(413,{"ok":False,"error":str(e)})
        if not self.authenticated(body):
            return
        try:
            data = json.loads(body or b"{}")
        except Exception:
            return self.send_json(400,{"ok":False,"error":"invalid_json"})
        rid = self.headers.get("X-Request-Id", uuid.uuid4().hex)
        if self.path == "/mcp":
            rpc_id = data.get("id")
            method = data.get("method", "")
            params = data.get("params") or {}
            if method == "initialize":
                return self.send_json(200,{"jsonrpc":"2.0","id":rpc_id,"result":{"protocolVersion":"2025-06-18","capabilities":{"tools":{}},"serverInfo":{"name":"boris-ops","version":VERSION}}})
            if method == "notifications/initialized":
                return self.send_json(202,{"jsonrpc":"2.0","result":{}})
            if method == "ping":
                return self.send_json(200,{"jsonrpc":"2.0","id":rpc_id,"result":{}})
            if method == "tools/list":
                tools=[
                  {"name":"health","description":"BORIS Ops health","inputSchema":{"type":"object","properties":{}}},
                  {"name":"exec","description":"Execute an allowlisted server command","inputSchema":{"type":"object","properties":{"command":{"type":"string"},"cwd":{"type":"string"},"timeout":{"type":"integer"},"background":{"type":"boolean"}},"required":["command"]}},
                  {"name":"script_run","description":"Run a bounded bash or python3 maintenance script with safety guards","inputSchema":{"type":"object","properties":{"content":{"type":"string"},"interpreter":{"type":"string","enum":["bash","python3"]},"cwd":{"type":"string"},"timeout":{"type":"integer"}},"required":["content"]}},
                  {"name":"file_list","description":"List an allowed directory","inputSchema":{"type":"object","properties":{"path":{"type":"string"},"limit":{"type":"integer"}}}},
                  {"name":"file_stat","description":"Stat and checksum an allowed path","inputSchema":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}},
                  {"name":"file_diff","description":"Compare allowed text files or a file against proposed text","inputSchema":{"type":"object","properties":{"path":{"type":"string"},"other_path":{"type":"string"},"proposed_content":{"type":"string"}},"required":["path"]}},
                  {"name":"file_validate","description":"Validate an allowed file with json, python or sh validator","inputSchema":{"type":"object","properties":{"path":{"type":"string"},"validator":{"type":"string","enum":["json","python","sh"]}},"required":["path","validator"]}},
                  {"name":"file_copy","description":"Copy an allowed file or directory","inputSchema":{"type":"object","properties":{"source":{"type":"string"},"destination":{"type":"string"},"overwrite":{"type":"boolean"}},"required":["source","destination"]}},
                  {"name":"file_move","description":"Move an allowed file or directory","inputSchema":{"type":"object","properties":{"source":{"type":"string"},"destination":{"type":"string"},"overwrite":{"type":"boolean"}},"required":["source","destination"]}},
                  {"name":"file_delete","description":"Soft-delete an allowed path to recoverable BORIS Ops trash","inputSchema":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}},
                  {"name":"file_restore","description":"Restore a soft-deleted path","inputSchema":{"type":"object","properties":{"trash_id":{"type":"string"},"destination":{"type":"string"}},"required":["trash_id"]}},
                  {"name":"file_upload","description":"Upload base64 content atomically with backup","inputSchema":{"type":"object","properties":{"path":{"type":"string"},"content_base64":{"type":"string"},"expected_sha256":{"type":"string"},"validator":{"type":"string","enum":["json","python","sh"]}},"required":["path","content_base64"]}},
                  {"name":"file_download","description":"Download an allowed file as bounded base64 content with SHA256","inputSchema":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}},
                  {"name":"file_trash","description":"List recent recoverable soft-deleted paths","inputSchema":{"type":"object","properties":{"limit":{"type":"integer","minimum":1,"maximum":100}}}},
                  {"name":"file_read","description":"Read an allowed server file","inputSchema":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}},
                  {"name":"file_search","description":"Bounded text search under an allowed root with traversal/time/file limits","inputSchema":{"type":"object","properties":{"root":{"type":"string"},"q":{"type":"string"},"limit":{"type":"integer","minimum":1,"maximum":200},"max_files":{"type":"integer","minimum":100,"maximum":4000},"max_seconds":{"type":"number","minimum":1,"maximum":8}},"required":["q"]}},
                  {"name":"file_write","description":"Atomically write an allowed file with backup, validation and diff","inputSchema":{"type":"object","properties":{"path":{"type":"string"},"content":{"type":"string"},"validator":{"type":"string","enum":["json","python","sh"]},"return_diff":{"type":"boolean"}},"required":["path","content"]}},
                  {"name":"file_patch","description":"Safely replace exact text with SHA256 precondition, validation, backup and optional diff","inputSchema":{"type":"object","properties":{"path":{"type":"string"},"old":{"type":"string"},"new":{"type":"string"},"expected_sha256":{"type":"string"},"count":{"type":"integer"},"validator":{"type":"string","enum":["json","python","sh"]},"return_diff":{"type":"boolean"}},"required":["path","old","new"]}},
                  {"name":"service","description":"Status or control a systemd service","inputSchema":{"type":"object","properties":{"service":{"type":"string"},"action":{"type":"string","enum":["status","restart","start","stop","reload"]}},"required":["service","action"]}},
                  {"name":"logs","description":"Read journal lines for a service","inputSchema":{"type":"object","properties":{"service":{"type":"string"},"lines":{"type":"integer"}},"required":["service"]}},
                  {"name":"job_get","description":"Get a background job","inputSchema":{"type":"object","properties":{"job_id":{"type":"string"}},"required":["job_id"]}},
                  {"name":"job_list","description":"List recent persistent jobs","inputSchema":{"type":"object","properties":{}}},
                  {"name":"job_cancel","description":"Cancel a running background job","inputSchema":{"type":"object","properties":{"job_id":{"type":"string"}},"required":["job_id"]}},
                  {"name":"self_update","description":"Install a checksum-verified BORIS Ops agent candidate from the protected releases directory with automatic rollback","inputSchema":{"type":"object","properties":{"candidate":{"type":"string"},"sha256":{"type":"string"}},"required":["candidate","sha256"]}},
                  {"name":"self_update_status","description":"Read the latest BORIS Ops self-update result","inputSchema":{"type":"object","properties":{}}},
                  {"name":"host_diagnostics","description":"Read bounded host, resource, PostgreSQL and BORIS service diagnostics","inputSchema":{"type":"object","properties":{}}},
                  {"name":"audit_recent","description":"Read recent sanitized BORIS Ops audit events","inputSchema":{"type":"object","properties":{"limit":{"type":"integer","minimum":1,"maximum":500}}}},
                  {"name":"runtime_capabilities","description":"Report optional local runtime capabilities without requiring GitHub","inputSchema":{"type":"object","properties":{}}},
                  {"name":"nginx_validate","description":"Validate nginx configuration without reloading it","inputSchema":{"type":"object","properties":{}}},
                  {"name":"process_list","description":"List bounded redacted host processes","inputSchema":{"type":"object","properties":{"limit":{"type":"integer","minimum":1,"maximum":200}}}},
                  {"name":"http_check","description":"Check an allowlisted BORIS HTTP health target","inputSchema":{"type":"object","properties":{"target":{"type":"string","enum":["public","ops","backend_primary","backend_replica"]},"timeout":{"type":"number","minimum":0.5,"maximum":10}}}},
                  {"name":"postgres_check","description":"Check local PostgreSQL readiness without reading credentials","inputSchema":{"type":"object","properties":{}}},
                  {"name":"boris_health","description":"Check BORIS production services and public health","inputSchema":{"type":"object","properties":{}}},
                  {"name":"boris_action","description":"Run a predefined safe BORIS production action","inputSchema":{"type":"object","properties":{"action":{"type":"string","enum":["restart_backend","restart_frontend","reload_nginx","frontend_build","frontend_deploy","backend_scoped_deploy","messenger_migration_status","messenger_migration_verify","messenger_migration_060","messenger_backend_tests"]},"background":{"type":"boolean"},"artifact":{"type":"string"}},"required":["action"]}}
                ]
                for tool in tools:
                    risk=TOOL_RISK.get(tool.get("name"),"READ")
                    tool["annotations"]={"title":risk,"readOnlyHint":risk=="READ","destructiveHint":risk in {"DEPLOY","DANGEROUS"},"idempotentHint":tool.get("name") in {"health","file_list","file_stat","file_read","file_download","file_trash","file_search","logs","job_get","job_list","self_update_status","boris_health","runtime_capabilities","nginx_validate","process_list","http_check","postgres_check"},"openWorldHint":tool.get("name") in {"boris_action","self_update"}}
                return self.send_json(200,{"jsonrpc":"2.0","id":rpc_id,"result":{"tools":tools}})
            if method == "tools/call":
                name=params.get("name",""); a=params.get("arguments") or {}; result=None
                if name == "health": result={"ok":True,"service":"boris-ops","version":VERSION,"write_enabled":not DISABLE_FILE.exists()}
                elif name == "exec":
                    if a.get("background"):
                        j=start_job(str(a.get("command","")),a.get("cwd"),a.get("timeout",1800))
                        result={"ok":bool(j.get("id")),"job_id":j.get("id"),"status":j.get("status"),"error":j.get("error")}
                    else: result=run_command(str(a.get("command","")),a.get("cwd"),a.get("timeout",30))
                elif name == "script_run": result=run_script(str(a.get("content","")),str(a.get("interpreter","bash")),a.get("cwd"),a.get("timeout",60))
                elif name == "file_list":
                    root,allowed=path_allowed(str(a.get("path","/root/BORIS"))); items=[]; limit=min(max(int(a.get("limit",200)),1),1000)
                    if not allowed or not root.is_dir(): result={"ok":False,"error":"path_not_allowed_or_missing"}
                    else:
                        for x in sorted(root.iterdir(),key=lambda y:(not y.is_dir(),y.name.lower()))[:limit]:
                            try: st=x.stat(); items.append({"name":x.name,"path":str(x),"type":"dir" if x.is_dir() else "file","size":st.st_size,"mtime":st.st_mtime})
                            except Exception: pass
                        result={"ok":True,"path":str(root),"count":len(items),"items":items}
                elif name == "file_stat": result=file_stat_result(str(a.get("path","")))
                elif name == "file_diff":
                    fp,allowed=path_allowed(str(a.get("path","")))
                    if not allowed or not fp.is_file(): result={"ok":False,"error":"path_not_allowed_or_missing"}
                    else:
                        left=fp.read_text(encoding="utf-8",errors="replace"); other=a.get("other_path"); proposed=a.get("proposed_content")
                        if other:
                            op,oa=path_allowed(str(other)); result={"ok":False,"error":"other_path_not_allowed_or_missing"} if not oa or not op.is_file() else {"ok":True,"diff":bounded_diff(left,op.read_text(encoding="utf-8",errors="replace"),str(fp))}
                        elif proposed is not None: result={"ok":True,"diff":bounded_diff(left,str(proposed),str(fp))}
                        else: result={"ok":False,"error":"other_path_or_proposed_content_required"}
                elif name == "file_validate":
                    fp,allowed=path_allowed(str(a.get("path",""))); result={"ok":False,"error":"path_not_allowed_or_missing"} if not allowed or not fp.is_file() else validate_candidate(fp,a.get("validator"))
                elif name == "file_copy": result=file_copy_move(str(a.get("source","")),str(a.get("destination","")),False,bool(a.get("overwrite",False)))
                elif name == "file_move": result=file_copy_move(str(a.get("source","")),str(a.get("destination","")),True,bool(a.get("overwrite",False)))
                elif name == "file_delete": result=file_soft_delete(str(a.get("path","")))
                elif name == "file_restore": result=file_restore(str(a.get("trash_id","")),a.get("destination"))
                elif name == "file_upload":
                    guard_ok,reason=write_guard(); fp,allowed=path_write_allowed(str(a.get("path",""))); result=None
                    if not guard_ok: result={"ok":False,"error":reason}
                    elif not allowed: result={"ok":False,"error":"path_not_allowed"}
                    else:
                        try: raw=base64.b64decode(str(a.get("content_base64","")),validate=True)
                        except Exception: raw=None
                        if raw is None or len(raw)>MAX_BODY: result={"ok":False,"error":"invalid_or_too_large_base64"}
                        else:
                            expected=str(a.get("expected_sha256","") or ""); incoming=hashlib.sha256(raw).hexdigest()
                            if expected and expected!=incoming: result={"ok":False,"error":"payload_sha256_mismatch","actual_sha256":incoming}
                            else:
                                fp.parent.mkdir(parents=True,exist_ok=True); backup=None; existed=fp.exists()
                                if existed: backup=unique_backup_path(fp); shutil.copy2(fp,backup)
                                tmp=fp.with_name(fp.name+".tmp."+secrets.token_hex(4)); tmp.write_bytes(raw)
                                if existed: preserve_mode(fp,tmp)
                                validation=validate_candidate(tmp,a.get("validator"))
                                if not validation.get("ok"):
                                    tmp.unlink(missing_ok=True); result={"ok":False,"error":"validation_failed","validation":validation}
                                else:
                                    os.replace(tmp,fp); result={"ok":True,"path":str(fp),"size":len(raw),"sha256":incoming,"backup":str(backup) if backup else None,"validation":validation}
                elif name == "file_download":
                    fp,allowed=path_allowed(str(a.get("path","")))
                    if not allowed or not fp.is_file(): result={"ok":False,"error":"path_not_allowed_or_missing"}
                    else:
                        raw=fp.read_bytes()
                        if len(raw)>MAX_BODY: result={"ok":False,"error":"file_too_large","max_bytes":MAX_BODY,"size":len(raw)}
                        else: result={"ok":True,"path":str(fp),"size":len(raw),"sha256":hashlib.sha256(raw).hexdigest(),"content_base64":base64.b64encode(raw).decode()}
                elif name == "file_trash":
                    limit=min(max(int(a.get("limit",100)),1),100); items=[]; TRASH_DIR.mkdir(parents=True,exist_ok=True)
                    for mf in sorted(TRASH_DIR.glob("trash_*.json"),key=lambda x:x.stat().st_mtime,reverse=True)[:limit]:
                        try: items.append(json.loads(mf.read_text(encoding="utf-8")))
                        except Exception: pass
                    result={"ok":True,"count":len(items),"items":items}
                elif name == "file_read":
                    fp,allowed=path_allowed(str(a.get("path","")))
                    if not allowed or not fp.is_file(): result={"ok":False,"error":"path_not_allowed_or_missing"}
                    else:
                        text=fp.read_text(encoding="utf-8",errors="replace"); result={"ok":True,"path":str(fp),"content":text[:MAX_OUTPUT],"truncated":len(text)>MAX_OUTPUT}
                elif name == "file_search":
                    root,allowed=path_allowed(str(a.get("root","/root/BORIS")))
                    if not allowed or not root.exists(): result={"ok":False,"error":"invalid_search"}
                    else: result=bounded_file_search(root,a.get("q",""),a.get("limit",100),a.get("max_files",SEARCH_MAX_FILES),a.get("max_seconds",SEARCH_MAX_SECONDS))
                elif name == "file_write":
                    guard_ok,guard_reason=write_guard(); fp,allowed=path_write_allowed(str(a.get("path","")))
                    if not guard_ok: result={"ok":False,"error":guard_reason}
                    elif not allowed: result={"ok":False,"error":"path_not_allowed"}
                    else:
                        content=str(a.get("content","")); fp.parent.mkdir(parents=True,exist_ok=True); backup=None; existed=fp.exists(); old_text=fp.read_text(encoding="utf-8",errors="replace") if existed else ""
                        if existed: backup=unique_backup_path(fp); backup.write_bytes(fp.read_bytes())
                        tmp=fp.with_name(fp.name+".tmp."+secrets.token_hex(4)); tmp.write_text(content,encoding="utf-8")
                        if existed: preserve_mode(fp,tmp)
                        validation=validate_candidate(tmp,a.get("validator"))
                        if not validation.get("ok"):
                            tmp.unlink(missing_ok=True); result={"ok":False,"error":"validation_failed","validation":validation}
                        else:
                            os.replace(tmp,fp); result={"ok":True,"path":str(fp),"backup":str(backup) if backup else None,"validation":validation}
                            if a.get("return_diff"): result["diff"]=bounded_diff(old_text,content,str(fp))
                elif name == "file_patch":
                    guard_ok,guard_reason=write_guard(); fp,allowed=path_write_allowed(str(a.get("path",""))); result=None
                    if not guard_ok: result={"ok":False,"error":guard_reason}
                    elif not allowed or not fp.is_file(): result={"ok":False,"error":"path_not_allowed_or_missing"}
                    else:
                        old=str(a.get("old","")); new=str(a.get("new","")); count=max(int(a.get("count",1)),1); raw=fp.read_bytes(); cur=hashlib.sha256(raw).hexdigest(); expected=str(a.get("expected_sha256","") or "")
                        if expected and expected != cur: result={"ok":False,"error":"sha256_mismatch","current_sha256":cur}
                        else:
                            text=raw.decode("utf-8"); matches=text.count(old)
                            if not old or matches < count: result={"ok":False,"error":"pattern_not_found_or_insufficient","matches":matches}
                            else:
                                backup=unique_backup_path(fp); backup.write_bytes(raw); updated=text.replace(old,new,count); tmp=fp.with_name(fp.name+".tmp."+secrets.token_hex(4)); tmp.write_text(updated,encoding="utf-8"); preserve_mode(fp,tmp); validation=validate_candidate(tmp,a.get("validator"))
                                if not validation.get("ok"):
                                    tmp.unlink(missing_ok=True); result={"ok":False,"error":"validation_failed","validation":validation}
                                else:
                                    os.replace(tmp,fp); after=hashlib.sha256(fp.read_bytes()).hexdigest(); result={"ok":True,"path":str(fp),"backup":str(backup),"before_sha256":cur,"after_sha256":after,"replacements":count,"validation":validation}
                                    if a.get("return_diff"): result["diff"]=bounded_diff(text,updated,str(fp))
                elif name == "service":
                    service=str(a.get("service","")); action=str(a.get("action","status"))
                    if not re.fullmatch(r"[A-Za-z0-9_.@-]+",service) or action not in {"status","restart","start","stop","reload"}: result={"ok":False,"error":"invalid_service_action"}
                    elif service not in SERVICE_ALLOWLIST: result={"ok":False,"error":"service_not_allowed"}
                    elif action not in SERVICE_ACTIONS_ALLOWED.get(service,{"status"}): result={"ok":False,"error":"service_action_not_allowed"}
                    elif action != "status" and not write_guard()[0]: result={"ok":False,"error":write_guard()[1]}
                    else:
                        argv=["systemctl",action,service] if action=="status" else ["sudo","-n","systemctl",action,service]
                        result=run_trusted_argv(argv,None,60)
                elif name == "logs": result=run_command(f"journalctl -u {shlex.quote(str(a.get('service','')))} -n {min(max(int(a.get('lines',100)),1),1000)} --no-pager",None,30)
                elif name == "self_update_status":
                    if UPDATE_STATUS.is_file():
                        try: result=json.loads(UPDATE_STATUS.read_text(encoding="utf-8"))
                        except Exception as e: result={"ok":False,"error":"status_corrupt","detail":str(e)[:200]}
                    else: result={"ok":True,"status":"never_run"}
                elif name == "self_update":
                    guard_ok,reason=write_guard(); candidate=Path(str(a.get("candidate",""))).resolve(); expected=str(a.get("sha256","")).lower(); result=None
                    if not guard_ok: result={"ok":False,"error":reason}
                    elif RELEASE_DIR not in candidate.parents or not candidate.is_file(): result={"ok":False,"error":"candidate_outside_release_dir"}
                    elif not re.fullmatch(r"[0-9a-f]{64}",expected): result={"ok":False,"error":"invalid_sha256"}
                    elif not self_update_gate_allows(expected): result={"ok":False,"error":"self_update_gate_closed"}
                    else:
                        actual=hashlib.sha256(candidate.read_bytes()).hexdigest()
                        if actual!=expected: result={"ok":False,"error":"candidate_sha256_mismatch","actual_sha256":actual}
                        else:
                            UPDATE_REQUEST.parent.mkdir(parents=True,exist_ok=True); tmp=UPDATE_REQUEST.with_suffix('.tmp'); tmp.write_text(json.dumps({"candidate":str(candidate),"sha256":expected,"requested_at":time.time()}),encoding="utf-8"); os.replace(tmp,UPDATE_REQUEST)
                            cp=subprocess.run(["sudo","-n","systemctl","start","--no-block","boris-ops-updater.service"],capture_output=True,text=True,timeout=10); result={"ok":cp.returncode==0,"status":"started" if cp.returncode==0 else "failed_to_start","candidate":str(candidate),"sha256":expected,"stderr":cp.stderr[-500:]}
                elif name == "host_diagnostics": result=host_diagnostics_result()
                elif name == "audit_recent": result=audit_recent_result(a.get("limit",100))
                elif name == "runtime_capabilities": result=runtime_capabilities_result()
                elif name == "nginx_validate": result=nginx_validate_result()
                elif name == "process_list": result=process_list_result(a.get("limit",50))
                elif name == "http_check": result=http_check_result(a.get("target","public"),a.get("timeout",5))
                elif name == "postgres_check": result=postgres_check_result()
                elif name == "boris_health":
                    services=["boris-backend.service","boris-backend-replica.service","boris-frontend.service","boris-background-worker.service","nginx.service","postgresql@16-main.service","boris-ops.service"]
                    states={}
                    for svc in services:
                        cp=subprocess.run(["systemctl","is-active",svc],capture_output=True,text=True,timeout=5); states[svc]=cp.stdout.strip() or "unknown"
                    result={"ok":all(v=="active" for v in states.values()),"services":states,"write_enabled":write_guard()[0]}
                elif name == "boris_action":
                    action=str(a.get("action",""))
                    if action not in BORIS_ACTIONS: result={"ok":False,"error":"unknown_action"}
                    elif not write_guard()[0]: result={"ok":False,"error":write_guard()[1]}
                    else:
                        if action == "messenger_p0_selftest":
                            result=run_messenger_p0_selftest()
                        elif action == "frontend_deploy":
                            artifact=Path(str(a.get("artifact",""))).resolve(); root=Path("/root/BORIS/frontend/.boris-builds/artifacts").resolve()
                            if not (artifact.is_dir() and root in artifact.parents and (artifact/"BORIS_ARTIFACT.json").is_file()): result={"ok":False,"error":"invalid_artifact"}
                            else:
                                argv=["bash","scripts/boris-frontend-deploy.sh",str(artifact)]; j=start_trusted_job(argv,"/root/BORIS/frontend",1800,"frontend_deploy"); result={"ok":bool(j.get("id")),"job_id":j.get("id"),"status":j.get("status"),"action":action,"error":j.get("error")}
                        elif action == "backend_scoped_deploy":
                            artifact=Path(str(a.get("artifact",""))).resolve(); root=Path("/root/BORIS/backend/run/scoped-release-requests").resolve()
                            if not (artifact.is_file() and root in artifact.parents and artifact.suffix.lower()==".json"): result={"ok":False,"error":"invalid_scope_manifest"}
                            else:
                                argv=["sudo","-n","/usr/local/sbin/boris-backend-scoped-deploy",str(artifact)]
                                j=start_trusted_job(argv,"/root/BORIS/backend",1800,"backend_scoped_deploy")
                                result={"ok":bool(j.get("id")),"job_id":j.get("id"),"status":j.get("status"),"action":action,"error":j.get("error")}
                        else:
                            argv,cwd,to=BORIS_ACTIONS[action]
                            if action == "frontend_build" and a.get("background", True):
                                result = start_durable_frontend_job(to)
                                result["action"] = action
                            elif a.get("background", action=="frontend_build"):
                                j=start_trusted_job(argv,cwd,to,action); result={"ok":bool(j.get("id")),"job_id":j.get("id"),"status":j.get("status"),"action":action,"error":j.get("error")}
                            else: result=run_trusted_argv(argv,cwd,to)
                elif name == "job_get":
                    jid = str(a.get("job_id","")); job = JOBS.get(jid)
                    if job and job.get("command") == "frontend_build" and job.get("unit"):
                        _reconcile_durable_frontend_job(job); persist_job(jid)
                    result = job or {"ok":False,"error":"not_found"}
                elif name == "job_list": result={"ok":True,"jobs":sorted(JOBS.values(),key=lambda j:j.get("created_at",0),reverse=True)[:100]}
                elif name == "job_cancel": result=cancel_job(str(a.get("job_id","")))
                else: result={"ok":False,"error":"unknown_tool"}
                audit({"request_id":rid,"operation":"mcp.tools.call","tool":name,"result":"ok" if result.get("ok") else "failed"})
                return self.send_json(200,{"jsonrpc":"2.0","id":rpc_id,"result":{"content":[{"type":"text","text":json.dumps(result,ensure_ascii=False)}],"isError":not bool(result.get("ok"))}})
            return self.send_json(200,{"jsonrpc":"2.0","id":rpc_id,"error":{"code":-32601,"message":"Method not found"}})
        if self.path.startswith("/jobs/") and self.path.endswith("/cancel"):
            guard_ok,guard_reason=write_guard()
            if not guard_ok: return self.send_json(503,{"ok":False,"error":guard_reason})
            jid=self.path.split("/")[2]; result=cancel_job(jid)
            return self.send_json(200 if result.get("ok") else 400,result)
        if self.path == "/self/update":
            guard_ok,reason=write_guard()
            if not guard_ok:return self.send_json(503,{"ok":False,"error":reason})
            candidate=Path(str(data.get("candidate",""))).resolve(); expected=str(data.get("sha256","")).lower()
            if RELEASE_DIR not in candidate.parents or not candidate.is_file():return self.send_json(400,{"ok":False,"error":"candidate_outside_release_dir"})
            if not re.fullmatch(r"[0-9a-f]{64}",expected):return self.send_json(400,{"ok":False,"error":"invalid_sha256"})
            if not self_update_gate_allows(expected):return self.send_json(403,{"ok":False,"error":"self_update_gate_closed"})
            actual=hashlib.sha256(candidate.read_bytes()).hexdigest()
            if actual!=expected:return self.send_json(409,{"ok":False,"error":"candidate_sha256_mismatch","actual_sha256":actual})
            UPDATE_REQUEST.parent.mkdir(parents=True,exist_ok=True); tmp=UPDATE_REQUEST.with_suffix('.tmp'); tmp.write_text(json.dumps({"candidate":str(candidate),"sha256":expected,"requested_at":time.time()}),encoding="utf-8"); os.replace(tmp,UPDATE_REQUEST); cp=subprocess.run(["sudo","-n","systemctl","start","--no-block","boris-ops-updater.service"],capture_output=True,text=True,timeout=10); audit({"request_id":rid,"operation":"self.update","candidate":str(candidate),"sha256":expected,"start_rc":cp.returncode}); return self.send_json(202 if cp.returncode==0 else 500,{"ok":cp.returncode==0,"status":"started" if cp.returncode==0 else "failed_to_start","candidate":str(candidate),"sha256":expected,"stderr":cp.stderr[-500:]})
        if self.path == "/script/run":
            result=run_script(str(data.get("content","")),str(data.get("interpreter","bash")),data.get("cwd"),data.get("timeout",60)); audit({"request_id":rid,"operation":"script.run","interpreter":data.get("interpreter","bash"),"result":"ok" if result.get("ok") else "failed","returncode":result.get("returncode")}); return self.send_json(200 if result.get("ok") else 400,result)
        if self.path == "/exec":
            cmd = str(data.get("command", "")); cwd = data.get("cwd"); timeout = data.get("timeout",30)
            if data.get("background"):
                job = start_job(cmd,cwd,timeout)
                if not job.get("id"):
                    audit({"request_id":rid,"operation":"exec.background","command":cmd,"result":"rejected","error":job.get("error")})
                    return self.send_json(400,job)
                audit({"request_id":rid,"operation":"exec.background","command":cmd,"job_id":job.get("id")})
                return self.send_json(202,{"ok":True,"job_id":job.get("id"),"status":job.get("status")})
            result = run_command(cmd,cwd,timeout)
            audit({"request_id":rid,"operation":"exec","command":cmd,"result":"ok" if result.get("ok") else "failed","returncode":result.get("returncode")})
            return self.send_json(200 if result.get("ok") else 400,result)
        if self.path == "/file/copy" or self.path == "/file/move":
            result=file_copy_move(str(data.get("source","")),str(data.get("destination","")),self.path.endswith("/move"),bool(data.get("overwrite",False))); audit({"request_id":rid,"operation":self.path.rsplit("/",1)[-1],"source":data.get("source"),"destination":data.get("destination"),"result":"ok" if result.get("ok") else "failed"}); return self.send_json(200 if result.get("ok") else 400,result)
        if self.path == "/file/delete":
            result=file_soft_delete(str(data.get("path",""))); audit({"request_id":rid,"operation":"file.delete","path":data.get("path"),"result":"ok" if result.get("ok") else "failed","trash_id":result.get("trash_id")}); return self.send_json(200 if result.get("ok") else 400,result)
        if self.path == "/file/restore":
            result=file_restore(str(data.get("trash_id","")),data.get("destination")); audit({"request_id":rid,"operation":"file.restore","trash_id":data.get("trash_id"),"result":"ok" if result.get("ok") else "failed"}); return self.send_json(200 if result.get("ok") else 400,result)
        if self.path == "/file/upload":
            guard_ok,reason=write_guard()
            if not guard_ok:return self.send_json(503,{"ok":False,"error":reason})
            fp,allowed=path_write_allowed(str(data.get("path","")))
            if not allowed:return self.send_json(403,{"ok":False,"error":"path_not_allowed"})
            try: raw=base64.b64decode(str(data.get("content_base64","")),validate=True)
            except Exception:return self.send_json(400,{"ok":False,"error":"invalid_base64"})
            if len(raw)>MAX_BODY:return self.send_json(413,{"ok":False,"error":"file_too_large"})
            incoming=hashlib.sha256(raw).hexdigest(); expected=str(data.get("expected_sha256","") or "")
            if expected and expected!=incoming:return self.send_json(409,{"ok":False,"error":"payload_sha256_mismatch","actual_sha256":incoming})
            fp.parent.mkdir(parents=True,exist_ok=True); backup=None; existed=fp.exists()
            if existed: backup=unique_backup_path(fp); shutil.copy2(fp,backup)
            tmp=fp.with_name(fp.name+".tmp."+secrets.token_hex(4)); tmp.write_bytes(raw)
            if existed: preserve_mode(fp,tmp)
            validation=validate_candidate(tmp,data.get("validator"))
            if not validation.get("ok"): tmp.unlink(missing_ok=True); return self.send_json(400,{"ok":False,"error":"validation_failed","validation":validation})
            os.replace(tmp,fp); audit({"request_id":rid,"operation":"file.upload","path":str(fp),"bytes":len(raw),"sha256":incoming}); return self.send_json(200,{"ok":True,"path":str(fp),"size":len(raw),"sha256":incoming,"backup":str(backup) if backup else None,"validation":validation})
        if self.path == "/file/patch":
            guard_ok,guard_reason=write_guard()
            if not guard_ok: return self.send_json(503,{"ok":False,"error":guard_reason})
            fp,allowed=path_write_allowed(str(data.get("path","")))
            if not allowed or not fp.is_file(): return self.send_json(403,{"ok":False,"error":"path_not_allowed_or_missing"})
            raw=fp.read_bytes(); cur=hashlib.sha256(raw).hexdigest(); expected=str(data.get("expected_sha256","") or "")
            if expected and expected != cur: return self.send_json(409,{"ok":False,"error":"sha256_mismatch","current_sha256":cur})
            old=str(data.get("old","")); new=str(data.get("new","")); count=max(int(data.get("count",1)),1); text=raw.decode("utf-8"); matches=text.count(old)
            if not old or matches < count: return self.send_json(409,{"ok":False,"error":"pattern_not_found_or_insufficient","matches":matches})
            backup=unique_backup_path(fp); backup.write_bytes(raw); updated=text.replace(old,new,count); tmp=fp.with_name(fp.name+".tmp."+secrets.token_hex(4)); tmp.write_text(updated,encoding="utf-8"); preserve_mode(fp,tmp); validation=validate_candidate(tmp,data.get("validator"))
            if not validation.get("ok"): tmp.unlink(missing_ok=True); return self.send_json(400,{"ok":False,"error":"validation_failed","validation":validation})
            os.replace(tmp,fp); after=hashlib.sha256(fp.read_bytes()).hexdigest(); audit({"request_id":rid,"operation":"file.patch","path":str(fp),"backup":str(backup),"before_sha256":cur,"after_sha256":after}); result={"ok":True,"path":str(fp),"backup":str(backup),"before_sha256":cur,"after_sha256":after,"replacements":count,"validation":validation}
            if data.get("return_diff"): result["diff"]=bounded_diff(text,updated,str(fp))
            return self.send_json(200,result)
        if self.path == "/file/write":
            guard_ok, guard_reason = write_guard()
            if not guard_ok:
                return self.send_json(503,{"ok":False,"error":guard_reason})
            p, allowed = path_write_allowed(str(data.get("path", "")))
            if not allowed:
                return self.send_json(403,{"ok":False,"error":"path_not_allowed"})
            content = str(data.get("content", ""))
            if len(content.encode()) > MAX_BODY:
                return self.send_json(413,{"ok":False,"error":"content_too_large"})
            p.parent.mkdir(parents=True, exist_ok=True)
            backup = None; existed=p.exists(); old_text=p.read_text(encoding="utf-8",errors="replace") if existed else ""
            if existed:
                backup = unique_backup_path(p)
                backup.write_bytes(p.read_bytes())
            tmp = p.with_name(p.name + ".tmp." + secrets.token_hex(4))
            tmp.write_text(content, encoding="utf-8")
            if existed: preserve_mode(p,tmp)
            validation=validate_candidate(tmp,data.get("validator"))
            if not validation.get("ok"):
                tmp.unlink(missing_ok=True); return self.send_json(400,{"ok":False,"error":"validation_failed","validation":validation})
            os.replace(tmp,p)
            audit({"request_id":rid,"operation":"file.write","path":str(p),"backup":str(backup) if backup else None,"bytes":len(content.encode())})
            result={"ok":True,"path":str(p),"backup":str(backup) if backup else None,"validation":validation}
            if data.get("return_diff"): result["diff"]=bounded_diff(old_text,content,str(p))
            return self.send_json(200,result)
        if self.path == "/service":
            service = str(data.get("service", "")); action = str(data.get("action", "status"))
            if action != "status":
                guard_ok, guard_reason = write_guard()
                if not guard_ok:
                    return self.send_json(503,{"ok":False,"error":guard_reason})
            if not re.fullmatch(r"[A-Za-z0-9_.@-]+", service) or action not in {"status","restart","start","stop","reload"}:
                return self.send_json(400,{"ok":False,"error":"invalid_service_action"})
            if service not in SERVICE_ALLOWLIST:
                return self.send_json(403,{"ok":False,"error":"service_not_allowed"})
            if action not in SERVICE_ACTIONS_ALLOWED.get(service,{"status"}):
                return self.send_json(403,{"ok":False,"error":"service_action_not_allowed"})
            argv=["systemctl",action,service] if action=="status" else ["sudo","-n","systemctl",action,service]
            result = run_trusted_argv(argv,None,60)
            audit({"request_id":rid,"operation":"service."+action,"service":service,"returncode":result.get("returncode")})
            return self.send_json(200 if result.get("ok") else 400,result)
        if self.path == "/logs":
            service = str(data.get("service", "")); lines = min(max(int(data.get("lines",100)),1),1000)
            if not re.fullmatch(r"[A-Za-z0-9_.@-]+", service):
                return self.send_json(400,{"ok":False,"error":"invalid_service"})
            result = run_command(f"journalctl -u {shlex.quote(service)} -n {lines} --no-pager",None,30)
            return self.send_json(200 if result.get("ok") else 400,result)
        return self.send_json(404,{"ok":False,"error":"not_found"})


def main():
    load_nonces()
    load_jobs()
    server = ThreadingHTTPServer((BIND, PORT), Handler)
    server.started_at = time.time()
    server.daemon_threads = True
    print(f"BORIS Ops {VERSION} listening on {BIND}:{PORT}", flush=True)
    server.serve_forever()

if __name__ == "__main__":
    main()
