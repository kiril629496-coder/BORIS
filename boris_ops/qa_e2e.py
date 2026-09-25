#!/usr/bin/env python3
"""Zero-cost, non-destructive BORIS Ops production contract QA."""
import hashlib
import hmac
import json
import secrets
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ENV = {}
for line in Path('/etc/boris-ops/agent.env').read_text(encoding='utf-8').splitlines():
    if line.strip() and '=' in line and not line.lstrip().startswith('#'):
        key, value = line.split('=', 1)
        ENV[key] = value.strip().strip('"').strip("'")
TOKEN = ENV['BORIS_OPS_TOKEN']
SECRET = ENV['BORIS_OPS_HMAC_SECRET']
BASE = 'https://boris-ai.pro/_ops'
OUT_DIR = Path('/var/lib/boris-ops/qa')
OUT_DIR.mkdir(parents=True, exist_ok=True)


def request(path, method='GET', obj=None, fixed=None):
    body = b'' if obj is None else json.dumps(obj, separators=(',', ':')).encode()
    # QA_RATE_AWARE_V1: production 1.4.3 has a token bucket. QA may run beside
    # independent witnesses, so explicit 429 is retried with bounded backoff.
    last = (0, {'ok': False, 'error': 'no_attempt'})
    for attempt in range(1, 7):
        if fixed:
            ts, nonce = fixed
        else:
            ts, nonce = str(int(time.time())), secrets.token_hex(16)
        signature = hmac.new(SECRET.encode(), (ts + '\n' + nonce + '\n').encode() + body, hashlib.sha256).hexdigest()
        req = urllib.request.Request(
            BASE + path,
            data=body if method == 'POST' else None,
            method=method,
            headers={
                'Authorization': 'Bearer ' + TOKEN,
                'X-Boris-Timestamp': ts,
                'X-Boris-Nonce': nonce,
                'X-Boris-Signature': signature,
                'Content-Type': 'application/json',
                'Accept': 'application/json, text/event-stream',
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            try:
                last = (exc.code, json.loads(exc.read()))
            except Exception:
                last = (exc.code, {'ok': False, 'error': 'http_error'})
            if exc.code == 429 and attempt < 6:
                time.sleep(min(2.5, 0.55 * attempt))
                continue
            return last
    return last


def mcp(rpc_id, method, params=None):
    return request('/mcp', 'POST', {'jsonrpc': '2.0', 'id': rpc_id, 'method': method, 'params': params or {}})


def main():
    checks = {}
    started = time.time()
    # Public health needs no credential.
    try:
        with urllib.request.urlopen(BASE + '/health', timeout=8) as response:
            checks['public_health'] = response.status == 200
    except Exception:
        checks['public_health'] = False

    code, version = request('/version')
    checks['authenticated_version'] = code == 200 and version.get('ok') is True
    checks['least_privilege_capability'] = 'least_privilege_runtime' in version.get('capabilities', [])
    checks['dangerous_scripts_default_off'] = version.get('dangerous_scripts_enabled') is False

    # Missing auth must be denied.
    try:
        urllib.request.urlopen(BASE + '/version', timeout=8)
        checks['unauthorized_denied'] = False
    except urllib.error.HTTPError as exc:
        checks['unauthorized_denied'] = exc.code == 401
    except Exception:
        checks['unauthorized_denied'] = False

    # Replay: same signed request succeeds once and is denied the second time.
    fixed = (str(int(time.time())), secrets.token_hex(16))
    first = request('/version', fixed=fixed)[0]
    second = request('/version', fixed=fixed)[0]
    checks['replay_denied'] = first == 200 and second == 401

    # Generic execution must remain read-only / allowlisted.
    checks['safe_exec'] = request('/exec', 'POST', {'command': 'uptime'})[0] == 200
    bad = request('/exec', 'POST', {'command': 'python3 -c print(1)'})
    checks['arbitrary_exec_denied'] = bad[0] == 400 and bad[1].get('error') == 'command_not_allowed'

    # Project file mutation with optimistic hash precondition.
    temp = '/root/BORIS/boris_ops/.qa-contract.tmp'
    code, wr = request('/file/write', 'POST', {'path': temp, 'content': 'alpha beta\n'})
    checks['project_write'] = code == 200 and wr.get('ok') is True
    stat_code, stat = request('/file/stat?path=' + urllib.parse.quote(temp, safe=''))
    checks['file_stat'] = stat_code == 200 and len(stat.get('sha256', '')) == 64
    patch_code, patch = request('/file/patch', 'POST', {
        'path': temp, 'old': 'beta', 'new': 'BETA', 'expected_sha256': stat.get('sha256', ''), 'count': 1
    })
    checks['optimistic_patch'] = patch_code == 200 and patch.get('replacements') == 1

    # Protected/config paths must not be writable via generic file API.
    checks['nginx_generic_write_denied'] = request('/file/write', 'POST', {
        'path': '/etc/nginx/sites-enabled/boris-ops-qa-forbidden', 'content': 'x'
    })[0] == 403
    checks['agent_generic_write_denied'] = request('/file/write', 'POST', {
        'path': '/root/BORIS/boris_ops/agent.py', 'content': 'x'
    })[0] == 403

    # MCP transport contract.
    c, init = mcp(101, 'initialize', {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'boris-qa', 'version': '1'}})
    checks['mcp_initialize'] = c == 200 and init.get('result', {}).get('serverInfo', {}).get('name') == 'boris-ops'
    c, tools = mcp(102, 'tools/list')
    tool_names = {x.get('name') for x in tools.get('result', {}).get('tools', [])}
    checks['mcp_tools'] = c == 200 and {'health', 'exec', 'file_read', 'file_patch', 'boris_health'}.issubset(tool_names)
    c, call = mcp(103, 'tools/call', {'name': 'health', 'arguments': {}})
    checks['mcp_call'] = c == 200 and call.get('result', {}).get('isError') is False

    c, health = request('/boris/health')
    checks['boris_health'] = c == 200 and health.get('ok') is True
    checks['disk_guard_sane'] = float(health.get('disk_percent', 100)) < 92 or health.get('write_enabled') is False

    # Runtime must not be root.
    cp = subprocess.run(['systemctl', 'show', 'boris-ops.service', '-p', 'User', '--value'], capture_output=True, text=True, timeout=5)
    checks['runtime_user_borisops'] = cp.stdout.strip() == 'borisops'

    # Best-effort cleanup of QA temp; it is deliberately inside write root and owned by borisops.
    try:
        Path(temp).unlink(missing_ok=True)
    except Exception:
        pass

    ok = all(checks.values())
    report = {
        'ok': ok,
        'contract': 'BORIS_OPS_PRODUCTION_QA',
        'started_at': started,
        'finished_at': time.time(),
        'duration_s': round(time.time() - started, 3),
        'checks': checks,
        'passed': sum(1 for value in checks.values() if value),
        'total': len(checks),
        'failed': [name for name, value in checks.items() if not value],
    }
    (OUT_DIR / 'latest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(0 if ok else 1)


if __name__ == '__main__':
    main()
