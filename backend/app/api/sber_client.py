"""Клиент Sber API (набор «Компаниям») — ТОЛЬКО чтение выписки."""
import os, json, datetime, requests, uuid

_BASE = os.environ.get("SBER_BASE_URL", "https://fintech-test.sberbank.ru:9443").rstrip("/")
_CID = os.environ.get("SBER_CLIENT_ID", "")
_CSECRET = os.environ.get("SBER_CLIENT_SECRET", "")
_CERT = os.environ.get("SBER_CERT", "")
_KEY = os.environ.get("SBER_KEY", "")
_CA = os.environ.get("SBER_CA", "")
_TOK_ACC, _TOK_KEY = "__sber", "sber_tokens"


def _rquid():
    return uuid.uuid4().hex


def _cert():
    return (_CERT, _KEY) if (_CERT and _KEY) else None


def _verify():
    return _CA if _CA else False


def _load_tokens():
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == _TOK_ACC, Storage.key == _TOK_KEY).first()
        if row:
            try:
                return json.loads(row.value)
            except Exception:
                pass
    finally:
        db.close()
    return {"access_token": os.environ.get("SBER_ACCESS_TOKEN", ""),
            "refresh_token": os.environ.get("SBER_REFRESH_TOKEN", "")}


def _save_tokens(tok):
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == _TOK_ACC, Storage.key == _TOK_KEY).first()
        val = json.dumps(tok, ensure_ascii=False)
        if row:
            row.value = val
        else:
            db.add(Storage(account_id=_TOK_ACC, key=_TOK_KEY, value=val))
        db.commit()
    finally:
        db.close()


def _refresh_access():
    tok = _load_tokens()
    rt = tok.get("refresh_token")
    if not rt:
        return None
    import base64 as _b64
    url = _BASE + "/ic/sso/api/v2/oauth"
    data = {"grant_type": "refresh_token", "refresh_token": rt, "client_id": _CID, "client_secret": _CSECRET}
    _basic = _b64.b64encode((_CID + ":" + _CSECRET).encode()).decode()
    headers = {"Authorization": "Basic " + _basic,
               "Content-Type": "application/x-www-form-urlencoded",
               "RqUID": _rquid()}
    try:
        r = requests.post(url, data=data, headers=headers, cert=_cert(), verify=_verify(), timeout=30)
        if r.status_code == 200:
            j = r.json()
            tok["access_token"] = j.get("access_token", tok.get("access_token"))
            if j.get("refresh_token"):
                tok["refresh_token"] = j["refresh_token"]
            _save_tokens(tok)
            return tok["access_token"]
        print("[sber] refresh failed:", r.status_code, r.text[:300])
    except Exception as e:
        print("[sber] refresh error:", e)
    return None


def _get(path, params, _retry=True):
    tok = _load_tokens()
    headers = {"Authorization": "Bearer " + str(tok.get("access_token")), "Accept": "application/json"}
    try:
        r = requests.get(_BASE + path, params=params, headers=headers, cert=_cert(), verify=_verify(), timeout=60)
    except Exception as e:
        return {"ok": False, "error": "request_failed", "detail": str(e)}
    if r.status_code == 401 and _retry:
        if _refresh_access():
            return _get(path, params, _retry=False)
        return {"ok": False, "error": "unauthorized", "detail": r.text[:300]}
    if r.status_code != 200:
        return {"ok": False, "error": "http_%d" % r.status_code, "detail": r.text[:500]}
    try:
        return {"ok": True, "data": r.json()}
    except Exception:
        return {"ok": True, "data": {"raw": r.text}}


def get_client_info():
    # инфо об организации + её счета (для песочницы и прома единый путь)
    return _get("/fintech/api/v1/client-info", {})

def get_accounts():
    # счета извлекаются из client-info; структуру ответа выверить по реальному ответу песочницы
    info = get_client_info() or {}
    return info.get("accounts", info)


def get_transactions(account_number, date_from, date_to, page=1):
    return _get("/fintech/api/v1/statement/transactions",
                {"accountNumber": account_number, "statementDate": date_from,
                 "dateFrom": date_from, "dateTo": date_to, "page": page})


def find_incoming(account_number, days=3):
    today = datetime.date.today()
    frm = (today - datetime.timedelta(days=int(days))).isoformat()
    res = get_transactions(account_number, frm, today.isoformat())
    if not res.get("ok"):
        return res
    out = []
    data = res["data"]
    for t in (data.get("transactions") or data.get("operations") or []):
        direction = str(t.get("direction") or t.get("operationType") or "").upper()
        if "CREDIT" not in direction and t.get("credit") is None:
            continue
        payer = t.get("payer") or {}
        out.append({
            "amount": t.get("amountRub") or t.get("amount") or (t.get("credit") or {}).get("amount"),
            "purpose": t.get("purpose") or t.get("paymentPurpose") or "",
            "payer_name": (payer.get("name") if isinstance(payer, dict) else payer) or t.get("payerName", ""),
            "payer_inn": (payer.get("inn") if isinstance(payer, dict) else "") or t.get("payerInn", ""),
            "date": t.get("operationDate") or t.get("date") or "",
        })
    return {"ok": True, "incoming": out, "count": len(out)}


def sber_config_status():
    tok = _load_tokens()
    return {"base_url": _BASE, "client_id_set": bool(_CID), "client_secret_set": bool(_CSECRET),
            "access_token_set": bool(tok.get("access_token")), "refresh_token_set": bool(tok.get("refresh_token")),
            "cert_exists": bool(_CERT) and os.path.exists(_CERT), "key_exists": bool(_KEY) and os.path.exists(_KEY),
            "ca_set": bool(_CA)}
