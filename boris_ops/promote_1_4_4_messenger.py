#!/usr/bin/env python3
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path('/root/BORIS/boris_ops')
CANDIDATE = ROOT / 'releases/agent-1.4.4-messenger-p0-candidate.py'
EXPECTED_SHA256 = '59fd7830931b054f0eafa20561b6112b9e79905e430bf47f385fc34ab819b3b2'
GATE = Path('/etc/boris-ops/allow-self-update.json')
REPORT = ROOT / 'PROMOTION_1_4_4_MESSENGER.json'
OPS_BASE = 'https://boris-ai.pro/_ops'
LOCAL_HEALTH = 'http://127.0.0.1:8741/health'

state = {
    'contract': 'BORIS_OPS_PROMOTE_1_4_4_MESSENGER',
    'started_at': time.time(),
    'candidate': str(CANDIDATE),
    'expected_sha256': EXPECTED_SHA256,
    'steps': [],
}


def save():
    tmp = REPORT.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(tmp, REPORT)


def fail(error):
    state['ok'] = False
    state['error'] = error
    state['finished_at'] = time.time()
    save()
    print(json.dumps({'ok': False, 'error': error}))
    raise SystemExit(1)


def load_env():
    values = {}
    for line in Path('/etc/boris-ops/agent.env').read_text(encoding='utf-8').splitlines():
        if line.strip() and '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            values[key] = value.strip().strip('"').strip("'")
    return values


def request(path, method='GET', payload=None, token='', secret=''):
    body = b'' if payload is None else json.dumps(payload, separators=(',', ':')).encode()
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(12)
    signature = hmac.new(secret.encode(), (timestamp + '\n' + nonce + '\n').encode() + body, hashlib.sha256).hexdigest()
    request = urllib.request.Request(
        OPS_BASE + path,
        data=body if method == 'POST' else None,
        method=method,
        headers={
            'Authorization': 'Bearer ' + token,
            'X-Boris-Timestamp': timestamp,
            'X-Boris-Nonce': nonce,
            'X-Boris-Signature': signature,
            'Content-Type': 'application/json',
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.loads(error.read())
        except Exception:
            return error.code, {}


def local_health():
    try:
        with urllib.request.urlopen(LOCAL_HEALTH, timeout=5) as response:
            return response.status, json.loads(response.read())
    except Exception as error:
        return 0, {'error': str(error)[:200]}


def active_identity():
    active = ROOT / 'agent.py'
    return hashlib.sha256(active.read_bytes()).hexdigest() if active.is_file() else ''


def main():
    if not CANDIDATE.is_file():
        fail('candidate_missing')
    actual = hashlib.sha256(CANDIDATE.read_bytes()).hexdigest()
    state['steps'].append({'candidate_sha256': actual})
    if actual != EXPECTED_SHA256:
        fail('candidate_sha_mismatch')
    env = load_env()
    token = env.get('BORIS_OPS_TOKEN', '')
    secret = env.get('BORIS_OPS_HMAC_SECRET', '')
    if not token or not secret:
        fail('ops_credentials_missing')
    status, health = local_health()
    state['steps'].append({'preflight_health': {'status': status, 'ok': health.get('ok'), 'version': health.get('version')}})
    if status != 200 or health.get('ok') is not True:
        fail('ops_unhealthy_before_promotion')
    gate = {'sha256': EXPECTED_SHA256, 'expires_at': time.time() + 120, 'purpose': 'messenger_p0_selftest_action'}
    try:
        GATE.write_text(json.dumps(gate), encoding='utf-8')
        borisops_gid = int(subprocess.check_output(['id', '-g', 'borisops'], text=True).strip())
        os.chown(GATE, 0, borisops_gid)
        os.chmod(GATE, 0o640)
        state['steps'].append({'gate': {'sha256': EXPECTED_SHA256, 'ttl_seconds': 120, 'mode': '0640', 'owner': 'root:borisops', 'purpose': gate['purpose']}})
        code, response = request('/self/update', 'POST', {'candidate': str(CANDIDATE), 'sha256': EXPECTED_SHA256}, token, secret)
        state['steps'].append({'self_update_request': {'status': code, 'ok': response.get('ok'), 'status_value': response.get('status'), 'error': response.get('error')}})
        if code != 202 or response.get('ok') is not True:
            fail('self_update_request_failed')
        recovered = False
        for index in range(120):
            if index:
                time.sleep(0.5)
            health_status, health_value = local_health()
            identity = active_identity()
            if health_status == 200 and health_value.get('ok') is True and identity == EXPECTED_SHA256 and health_value.get('version') == '1.4.4':
                recovered = True
                state['steps'].append({'recovered_after_seconds': round(index * 0.5, 1), 'active_sha256': identity, 'version': health_value.get('version')})
                break
        if not recovered:
            fail('candidate_not_recovered')
        status_code, update_status = request('/self/update/status', 'GET', None, token, secret)
        state['steps'].append({'update_status': {'status': status_code, 'ok': update_status.get('ok'), 'status_value': update_status.get('status'), 'installed_sha256': update_status.get('installed_sha256'), 'installed_version': update_status.get('installed_version')}})
        if status_code != 200 or update_status.get('ok') is not True or update_status.get('installed_sha256') != EXPECTED_SHA256:
            fail('update_status_failed')
        rpc_status, rpc = request('/mcp', 'POST', {'jsonrpc': '2.0', 'id': 'promotion-tools-list', 'method': 'tools/list', 'params': {}}, token, secret)
        tools = (((rpc.get('result') or {}).get('tools')) or []) if isinstance(rpc, dict) else []
        action_tool = next((tool for tool in tools if tool.get('name') == 'boris_action'), {})
        enum = (((action_tool.get('inputSchema') or {}).get('properties') or {}).get('action') or {}).get('enum') or []
        state['steps'].append({'tools_list': {'status': rpc_status, 'tool_count': len(tools), 'has_boris_action': bool(action_tool), 'has_messenger_p0_selftest': 'messenger_p0_selftest' in enum, 'existing_actions_present': all(name in enum for name in ['restart_backend', 'restart_frontend', 'reload_nginx', 'frontend_build', 'frontend_deploy', 'backend_scoped_deploy'])}})
        if rpc_status != 200 or 'messenger_p0_selftest' not in enum:
            fail('live_action_schema_missing')
        state['ok'] = True
        state['finished_at'] = time.time()
        save()
        print(json.dumps({'ok': True, 'version': '1.4.4', 'sha256': EXPECTED_SHA256, 'messenger_p0_selftest': True}))
    finally:
        try:
            GATE.unlink()
        except FileNotFoundError:
            pass


if __name__ == '__main__':
    main()
