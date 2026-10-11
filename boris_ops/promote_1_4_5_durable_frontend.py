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
CANDIDATE = ROOT / 'releases/agent-1.4.5-durable-frontend-candidate.py'
EXPECTED_SHA256 = '29c6b1d4f877ee5170a84c365a4ff431cf0e6599bdb493524f9a8c3c75820cd4'
GATE = Path('/etc/boris-ops/allow-self-update.json')
REPORT = ROOT / 'PROMOTION_1_4_5_DURABLE_FRONTEND.json'
OPS_BASE = 'http://127.0.0.1:8741'
LOCAL_HEALTH = 'http://127.0.0.1:8741/health'

state = {
    'contract': 'guarded_promotion_1_4_5_durable_frontend',
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
    req = urllib.request.Request(
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
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.loads(error.read())
        except Exception:
            return error.code, {}


def health():
    try:
        with urllib.request.urlopen(LOCAL_HEALTH, timeout=5) as response:
            return response.status, json.loads(response.read())
    except Exception as error:
        return 0, {'error': str(error)[:200]}


def main():
    try:
        if not CANDIDATE.is_file():
            fail('candidate_missing')
        actual = hashlib.sha256(CANDIDATE.read_bytes()).hexdigest()
        state['steps'].append({'candidate_sha256': actual})
        if actual != EXPECTED_SHA256:
            fail('candidate_sha_mismatch')
        compile(CANDIDATE.read_text(encoding='utf-8'), str(CANDIDATE), 'exec')
        state['steps'].append({'python_validation': 'PASS'})
        env = load_env()
        token = env.get('BORIS_OPS_TOKEN', '')
        secret = env.get('BORIS_OPS_HMAC_SECRET', '')
        if not token or not secret:
            fail('ops_credentials_missing')
        status, value = health()
        state['steps'].append({'preflight_health': {'status': status, 'ok': value.get('ok'), 'version': value.get('version')}})
        if status != 200 or value.get('ok') is not True:
            fail('ops_unhealthy_before_promotion')
        gate = {'sha256': EXPECTED_SHA256, 'expires_at': time.time() + 120, 'purpose': 'guarded_promotion_1_4_5_durable_frontend'}
        GATE.write_text(json.dumps(gate), encoding='utf-8')
        os.chmod(GATE, 0o640)
        try:
            gid = int(subprocess.check_output(['id', '-g', 'borisops'], text=True).strip())
            os.chown(GATE, 0, gid)
        except Exception:
            pass
        state['steps'].append({'gate': {'sha256': EXPECTED_SHA256, 'ttl_seconds': 120, 'purpose': gate['purpose']}})
        code, response = request('/self/update', 'POST', {'candidate': str(CANDIDATE), 'sha256': EXPECTED_SHA256}, token, secret)
        state['steps'].append({'self_update_request': {'status': code, 'ok': response.get('ok'), 'status_value': response.get('status'), 'error': response.get('error')}})
        if code != 202 or response.get('ok') is not True:
            fail('self_update_request_failed')
        state['ok'] = True
        state['finished_at'] = time.time()
        save()
    finally:
        try:
            GATE.unlink()
        except FileNotFoundError:
            pass


if __name__ == '__main__':
    main()
