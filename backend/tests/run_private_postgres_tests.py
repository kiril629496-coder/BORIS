"""Create, test and stop a private PostgreSQL cluster, with no TCP listener."""
import argparse
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import tempfile
import json
import time

parser = argparse.ArgumentParser()
parser.add_argument("--backend", required=True)
parser.add_argument("--python", required=True)
parser.add_argument("--output", required=True)
parser.add_argument("--pytest-filter", default="")
parser.add_argument("pytest_args", nargs="*")
args = parser.parse_args()
assert os.geteuid() != 0, "Run as the ordinary test user, never as postgres/root"
out = Path(args.output)
out.mkdir(parents=True, exist_ok=True)
root = Path(tempfile.mkdtemp(prefix="boris-rule-pg-", dir="/tmp"))
socket = root / "socket"
socket.mkdir(mode=0o700)
data = root / "data"
bin = Path("/usr/lib/postgresql/16/bin")
started = False
result = {"private_cluster": True, "tcp_listener": False, "production_db_access": False, "effective_uid":os.geteuid(), "backend":str(Path(args.backend).resolve())}
t0 = time.monotonic()
try:
    init = subprocess.run([str(bin / "initdb"), "-D", str(data), "--no-locale", "--encoding=UTF8", "--auth=trust"],
                          text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=40)
    (out / "initdb.log").write_text(init.stdout)
    if init.returncode:
        raise RuntimeError("private initdb failed")
    start = subprocess.run([str(bin / "pg_ctl"), "-D", str(data), "-l", str(out / "postgres.log"), "-o",
                            f"-c listen_addresses='' -k {socket} -p 55493 -c max_connections=16 -c shared_buffers=8MB", "-w", "start"],
                           text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
    (out / "start.log").write_text(start.stdout)
    if start.returncode:
        raise RuntimeError("private postgres start failed")
    started = True
    env = dict(os.environ)
    env["BORIS_RULE_TEST_BACKEND"] = str(Path(args.backend).resolve())
    env["BORIS_RULE_TEST_DATABASE_URL"] = f"postgresql+psycopg2://{pwd.getpwuid(os.geteuid()).pw_name}@/postgres?host={socket}&port=55493"
    selection = ["-k", args.pytest_filter] if args.pytest_filter else []
    test = subprocess.run([args.python, "-m", "pytest", "-q", str(Path(__file__).parent / "test_control_rule_runtime_postgres.py"), *selection, *args.pytest_args],
                          env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
    (out / "pytest.log").write_text(test.stdout)
    result.update(returncode=test.returncode, elapsed_seconds=round(time.monotonic()-t0, 3), pytest_tail=test.stdout[-2500:])
finally:
    if started:
        stop = subprocess.run([str(bin / "pg_ctl"), "-D", str(data), "-m", "fast", "-w", "stop"],
                              text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
        (out / "stop.log").write_text(stop.stdout)
        result["cluster_stopped"] = stop.returncode == 0
    if not started or result.get("cluster_stopped"):
        shutil.rmtree(root)
        result["private_data_removed"] = True
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
print(json.dumps(result, ensure_ascii=False))
raise SystemExit(result.get("returncode", 1))
