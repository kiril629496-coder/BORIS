"""ИИ РОП — калтрекинг Avito: статистика звонков, запись→whisper→разбор по чек-листу."""
import os, json, datetime, requests
from fastapi import APIRouter, Body
from app.api.avito import get_avito_token
from proxy_pool import get_intl_requests_proxies

router = APIRouter(prefix="/api/calltracking", tags=["calltracking"])

# тариф whisper: $0.006/мин; GPT-5.4 см. messenger. Курс ~95.
_WHISPER_PER_MIN_USD = 0.006
_USD_TO_RUB = 95


def _ct_token(account_id: str):
    """Токен под калтрекинг. Возвращает (token, error). error!=None → нет доступа."""
    td = get_avito_token(account_id)
    if not isinstance(td, dict) or "access_token" not in td:
        return None, (td.get("error_description") or td.get("error") or "нет доступа к калтрекингу") if isinstance(td, dict) else "Avito не подключён"
    return td["access_token"], None


def _get_calls(token: str, date_from: str, date_to: str):
    """POST /calltracking/v1/getCalls/ — список звонков за период."""
    r = requests.post(
        "https://api.avito.ru/calltracking/v1/getCalls/",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"dateTimeFrom": date_from, "dateTimeTo": date_to, "limit": 100, "offset": 0},
        timeout=60,
    )
    return r.status_code, r.json() if r.headers.get("content-type", "").startswith("application/json") else {}


@router.get("/stats")
def calltracking_stats(account_id: str, days: int = 30):
    """Статистика звонков: всего/отвечено/пропущено/воронка + минуты + себестоимость."""
    token, err = _ct_token(account_id)
    if err:
        return {"status": "no_access", "message": err}
    now = datetime.datetime.utcnow()
    date_from = (now - datetime.timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z")
    date_to = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        code, data = _get_calls(token, date_from, date_to)
    except Exception as e:
        return {"status": "error", "message": str(e)[:150]}
    if code != 200:
        return {"status": "error", "code": code, "message": (data or {}).get("error", {}).get("message", "ошибка Avito")}
    calls = data.get("calls", []) if isinstance(data, dict) else []
    total = len(calls)
    answered = sum(1 for c in calls if (c.get("call") or c).get("talkDuration", 0) > 0)
    missed = total - answered
    total_sec = sum((c.get("call") or c).get("talkDuration", 0) for c in calls)
    total_min = round(total_sec / 60, 1)
    cost_rub = round(total_min * _WHISPER_PER_MIN_USD * _USD_TO_RUB, 2)
    return {
        "status": "ok",
        "total": total,
        "answered": answered,
        "missed": missed,
        "answered_pct": round(answered / total * 100) if total else 0,
        "missed_pct": round(missed / total * 100) if total else 0,
        "total_minutes": total_min,
        "cost_rub": cost_rub,
        "calls": [
            {
                "call_id": (c.get("call") or c).get("callId"),
                "buyer_phone": (c.get("call") or c).get("buyerPhone"),
                "call_time": (c.get("call") or c).get("callTime"),
                "talk_duration": (c.get("call") or c).get("talkDuration", 0),
                "item_id": (c.get("call") or c).get("itemId"),
            }
            for c in calls
        ],
    }


CHECKLIST = [
    "Поприветствовал и представился (имя, компания)",
    "Выявил потребность: событие, дата, бюджет",
    "Презентовал решение под запрос клиента",
    "Отработал возражения (цена, сроки)",
    "Назвал цену или вилку цен",
    "Закрыл на следующий шаг (замер/оплата/созвон)",
    "Взял контакт или зафиксировал договорённость",
]


def _talk_minutes(token: str, call_id, days: int = 90) -> float:
    """Минуты разговора по call_id — из getCalls, того же источника, что и
    статистика в кабинете. Так остаток пакета сходится с цифрами Avito."""
    import datetime as _dt
    now = _dt.datetime.utcnow()
    d_from = (now - _dt.timedelta(days=int(days))).strftime("%Y-%m-%dT00:00:00Z")
    d_to = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    code, data = _get_calls(token, d_from, d_to)
    if code != 200 or not isinstance(data, dict):
        return 0.0
    for c in data.get("calls", []):
        _c = c.get("call") or c
        if str(_c.get("callId")) == str(call_id):
            return round(float(_c.get("talkDuration", 0) or 0) / 60, 1)
    return 0.0


def _get_record(token: str, call_id):
    """GET /calltracking/v1/getRecordByCallId/?callId=N → байты аудио."""
    r = requests.get(
        "https://api.avito.ru/calltracking/v1/getRecordByCallId/",
        headers={"Authorization": f"Bearer {token}"},
        params={"callId": call_id}, timeout=120,
    )
    return r.status_code, r.content, r.headers.get("content-type", "")


def _asr_hint(account_id: str) -> str:
    """Подсказка whisper: ниша клиента + названия фактов из его базы знаний.
    Так отраслевые слова распознаются правильно у КАЖДОГО клиента, а не только
    у того, под кого зашили список."""
    words = []
    try:
        import psycopg2, json as _jj
        u = _db_url()
        if u:
            c = psycopg2.connect(u); cur = c.cursor()
            # Ручной словарь важнее автоматического: названия фактов не содержат
            # само отраслевое слово («бытовка» не встречается в «Пол и покрытия»),
            # а именно оно и слышится неверно.
            cur.execute("SELECT value FROM storage WHERE account_id=%s"
                        " AND key='rop_asr_hint'", (account_id,))
            _mrow = cur.fetchone()
            if _mrow and _mrow[0]:
                _mv = _mrow[0] if isinstance(_mrow[0], list) else _jj.loads(_mrow[0])
                words.extend([str(x) for x in _mv if str(x).strip()])
            cur.execute("SELECT company_niche FROM accounts WHERE account_id=%s", (account_id,))
            row = cur.fetchone()
            if row and row[0]:
                words.append(str(row[0]))
            cur.execute("""SELECT DISTINCT name FROM client_facts
                           WHERE account_id=%s AND status <> 'rejected'
                           ORDER BY name LIMIT 60""", (account_id,))
            for r in cur.fetchall():
                if r[0]:
                    words.append(str(r[0]))
            c.close()
    except Exception:
        pass
    txt = ", ".join(w.strip() for w in words if w and len(str(w).strip()) > 2)
    return txt[:900]


def _transcribe(audio_bytes: bytes, filename: str = "call.mp3", hint: str = "") -> str:
    """OpenAI whisper: аудио → текст. Тот же ключ/прокси что GPT."""
    api_key = os.environ.get("OPENAI_API_KEY")
    proxies = get_intl_requests_proxies()
    r = requests.post(
        "https://api.openai.com/v1/audio/transcriptions",
        headers={"Authorization": "Bearer " + api_key},
        files={"file": (filename, audio_bytes)},
        data={"model": "whisper-1", "language": "ru",
              **({"prompt": hint} if hint else {})},
        proxies=proxies, timeout=300,
    )
    if r.status_code != 200:
        raise RuntimeError(f"whisper {r.status_code}: {r.text[:200]}")
    return r.json().get("text", "")


def _checklist_for(account_id: str):
    """Чек-лист звонка под нишу клиента. Хранится в storage под ключом
    rop_checklist — так его можно править по каждому клиенту без правки кода.
    Нет своего — берётся общий (он написан под поздравления и подходит не всем)."""
    try:
        import psycopg2, json as _jj
        u = _db_url()
        if u:
            c = psycopg2.connect(u); cur = c.cursor()
            cur.execute("SELECT value FROM storage WHERE account_id=%s"
                        " AND key='rop_checklist'", (account_id,))
            row = cur.fetchone(); c.close()
            if row and row[0]:
                v = row[0] if isinstance(row[0], list) else _jj.loads(row[0])
                items = [str(x).strip() for x in v if str(x).strip()]
                if items:
                    return items
    except Exception:
        pass
    return CHECKLIST


def _asr_fix(account_id: str, text_in: str) -> str:
    """Правка расшифровки по списку замен клиента (storage rop_asr_fix).
    Нужна потому, что подсказка whisper-1 на отраслевые слова почти не влияет:
    на бытовках модель устойчиво слышит «бутылки». Список свой у каждого
    аккаунта, поэтому чужие ниши не задеваются."""
    if not text_in:
        return text_in
    try:
        import psycopg2, json as _jj, re as _re
        u = _db_url()
        if not u:
            return text_in
        c = psycopg2.connect(u); cur = c.cursor()
        cur.execute("SELECT value FROM storage WHERE account_id=%s"
                    " AND key='rop_asr_fix'", (account_id,))
        row = cur.fetchone(); c.close()
        if not row or not row[0]:
            return text_in
        pairs = row[0] if isinstance(row[0], list) else _jj.loads(row[0])
        out = text_in
        for pair in pairs:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                continue
            out = _re.sub(pair[0], pair[1], out, flags=_re.IGNORECASE)
        return out
    except Exception:
        return text_in


def _account_context(account_id: str) -> str:
    """Ниша и описание бизнеса клиента — чтобы разбор шёл по его теме, а не абстрактно."""
    try:
        import psycopg2
        u = _db_url()
        if not u: return ""
        c = psycopg2.connect(u); cur = c.cursor()
        cur.execute("""SELECT company_niche, company_description, company_advantages, company_tone
                       FROM accounts WHERE account_id=%s""", (account_id,))
        row = cur.fetchone(); c.close()
        if not row: return ""
        niche, descr, adv, tone = [(x or "").strip() for x in row]
        parts = []
        if niche: parts.append("Ниша: " + niche)
        if descr: parts.append("Чем занимается: " + descr[:600])
        if adv: parts.append("Преимущества: " + adv[:400])
        if tone: parts.append("Тон общения: " + tone[:200])
        if not parts: return ""
        return ("\n\nБИЗНЕС КЛИЕНТА (учитывай при разборе — оценивай применительно к этой нише, "
                "не предлагай советы из других сфер):\n" + "\n".join(parts) + "\n")
    except Exception:
        return ""


def _analyze_call(transcript: str, account_id: str) -> dict:
    """GPT-5.4 разбор звонка по чек-листу: имя, ЛПР/ЛВР, галочки, балл, где провалился."""
    _cl = _checklist_for(account_id)
    checklist_txt = "\n".join(f"{i+1}. {item}" for i, item in enumerate(_cl))
    prompt = (
        "Ты — опытный, доброжелательный наставник по продажам. Разбери разговор менеджера с клиентом РАЗВИВАЮЩЕ и МЯГКО: "
        "хвали за конкретику, а зоны роста подавай как возможности ('стоит попробовать…', 'можно усилить…'), НЕ ругай, "
        "не пиши 'провалил' или 'плохо'. Тон — поддерживающий, как хороший руководитель на разборе.\n\n"
        f"ЧЕК-ЛИСТ (по каждому пункту true/false — выполнил ли менеджер):\n{checklist_txt}\n\n"
        "Определи: имя клиента, его статус — ЛПР / ЛВР / не тот.\n\n"
        "Верни ТОЛЬКО валидный JSON без markdown:\n"
        '{"client_name": "имя или пусто", "role": "ЛПР|ЛВР|не тот", '
        '"checklist": [{"item": "текст пункта", "done": true/false}], '
        '"score": число 0-100, '
        '"recommendation": "главный развивающий совет одной тёплой фразой", '
        '"strong_moments": ["что менеджер сделал хорошо — конкретно, до 4"], '
        '"growth_points": ["зоны роста мягко, как возможности усилить — до 4"], '
        '"stop_words": [{"phrase": "слово/фраза которую лучше не употреблять", "why": "чем мешает в продаже одной фразой", "better": "как сказать вместо — пример"}], '
        '"speech_tips": ["рекомендации по речи менеджера: темп, уверенность, структура фразы — конкретно и полезно, до 4"], '
        '"client_hooks": ["что клиента зацепило/заинтересовало — до 4"]}\n\n'
        + _account_context(account_id) +
        f"РАСШИФРОВКА РАЗГОВОРА:\n{transcript[:6000]}"
    )
    api_key = os.environ.get("OPENAI_API_KEY")
    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
        json={"model": "gpt-5.4", "messages": [{"role": "user", "content": prompt}], "max_completion_tokens": 1400},
        proxies=get_intl_requests_proxies(), timeout=120,
    )
    if r.status_code != 200:
        return {"error": f"GPT {r.status_code}"}
    data = r.json()
    usage = data.get("usage", {})
    try:
        from app.usage import log_usage as _lc
        _lc(account_id, "openai", "gpt-5.4", "разбор звонка РОП", usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
    except Exception:
        pass
    raw = data["choices"][0]["message"]["content"].strip().replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(raw)
    except Exception:
        return {"error": "не удалось разобрать ответ GPT", "raw": raw[:300]}


@router.post("/analyze_call")
def analyze_call(account_id: str, body: dict = Body(...)):
    """Полный разбор одного звонка: запись → whisper → чек-лист. call_id из getCalls."""
    call_id = body.get("call_id")
    if not call_id:
        return {"status": "error", "message": "нужен call_id"}
    # СТОПОР: если звонок уже разобран — берём готовое (whisper+GPT не гоняем, 0₽)
    try:
        import psycopg2, json as _json, os as _os
        _url = [l.split("=",1)[1].strip().strip('"').strip("'") for l in open(_os.path.join(_os.path.dirname(__file__), "..", "..", ".env")) if l.strip().startswith("DATABASE_URL")]
        _url = _url[0] if _url else None
        if _url:
            _cc = psycopg2.connect(_url); _ccur = _cc.cursor()
            _ccur.execute("SELECT transcript, analysis FROM call_analysis WHERE account_id=%s AND call_id=%s", (account_id, int(call_id)))
            _row = _ccur.fetchone(); _cc.close()
            if _row:
                _an = _row[1] if isinstance(_row[1], dict) else _json.loads(_row[1])
                return {"status": "ok", "call_id": call_id, "transcript": _row[0], "analysis": _an, "cached": True}
    except Exception:
        pass
    token, err = _ct_token(account_id)
    if err:
        return {"status": "no_access", "message": err}
    try:
        from app.api.nps import ensure_trial as _et
        _et(account_id, "rop")
    except Exception:
        pass
    _mins = float(body.get("minutes") or 0)
    _bal = get_rop_minutes(account_id)
    if _bal["left"] <= 0:
        return {"status": "no_minutes", "message": "Минуты пакета РОП закончились. Докупите пакет, чтобы продолжить разбор.", "balance": _bal}
    try:
        code, audio, ctype = _get_record(token, call_id)
    except Exception as e:
        return {"status": "error", "message": f"запись: {str(e)[:150]}"}
    if code == 425:
        return {"status": "not_ready", "message": "Запись ещё не готова (появляется до 30 мин после звонка)"}
    if code != 200 or not audio:
        return {"status": "error", "code": code, "message": "запись недоступна"}
    ext = "mp3" if "mpeg" in ctype or "mp3" in ctype else "wav"
    try:
        transcript = _asr_fix(account_id, _transcribe(audio, f"call_{call_id}.{ext}", _asr_hint(account_id)))
    except Exception as e:
        return {"status": "error", "message": str(e)[:150]}
    if not transcript.strip():
        return {"status": "error", "message": "пустая расшифровка"}
    analysis = _analyze_call(transcript, account_id)
    # Минуты для списания из пакета: из тела запроса, иначе спрашиваем у Avito
    # длительность разговора. Источник тот же, что в статистике клиента.
    _mins_final = float(_mins or 0)
    if _mins_final <= 0:
        try:
            _mins_final = _talk_minutes(token, call_id)
        except Exception:
            _mins_final = 0.0
    # СОХРАНЯЕМ в кэш — чтобы повторный разбор/отчёт брал готовое
    try:
        import psycopg2, json as _json, os as _os
        _url = [l.split("=",1)[1].strip().strip('"').strip("'") for l in open(_os.path.join(_os.path.dirname(__file__), "..", "..", ".env")) if l.strip().startswith("DATABASE_URL")]
        _url = _url[0] if _url else None
        if _url:
            _cc = psycopg2.connect(_url); _ccur = _cc.cursor()
            _ccur.execute("""INSERT INTO call_analysis (account_id, call_id, transcript, analysis, minutes)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (account_id, call_id) DO UPDATE SET transcript=EXCLUDED.transcript, analysis=EXCLUDED.analysis, minutes=EXCLUDED.minutes""",
                (account_id, int(call_id), transcript, _json.dumps(analysis), float(_mins_final)))
            _cc.commit(); _cc.close()
    except Exception:
        pass
    try:
        _nm = analysis.get("client_name") or "Клиент"
        _sc = analysis.get("score", "-")
        _rc = analysis.get("recommendation") or ""
        _tg_notify(account_id, f"\U0001F4DE Разобран звонок\n\n{_nm} \u00b7 балл {_sc}/100\n\n\U0001F4A1 {_rc}", topic="sales")
    except Exception:
        pass
    return {"status": "ok", "call_id": call_id, "transcript": transcript, "analysis": analysis, "cached": False}


# ============ PDF-ОТЧЁТ ПО ЗВОНКАМ ============
_WHISPER_MIN_USD = 0.006      # whisper за минуту аудио
_USD_RUB = 95
_GPT_CALL_RUB = 1.28         # ср. стоимость GPT-разбора одного звонка.
                             # Замер api_usage 07.08: 1,276 руб (946 промпт / 737 ответ).
                             # Прежние 0.74 занижали цену отчёта клиенту почти вдвое.
_GPT_REPORT_RUB = 0.26       # ср. стоимость GPT-рекомендаций отчёта (из логов)


def _db_url():
    import os as _os
    try:
        _p = _os.path.join(_os.path.dirname(__file__), "..", "..", ".env")
        for l in open(_p):
            if l.strip().startswith("DATABASE_URL"):
                return l.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        return None
    return None


def _cache_get_analysis(account_id: str, call_id):
    try:
        import psycopg2, json as _j
        u = _db_url()
        if not u: return None
        c = psycopg2.connect(u); cur = c.cursor()
        cur.execute("SELECT analysis FROM call_analysis WHERE account_id=%s AND call_id=%s", (account_id, int(call_id)))
        row = cur.fetchone(); c.close()
        if not row: return None
        return row[0] if isinstance(row[0], dict) else _j.loads(row[0])
    except Exception:
        return None


def _cache_put_analysis(account_id: str, call_id, transcript: str, analysis: dict, minutes: float = 0):
    try:
        import psycopg2, json as _j
        u = _db_url()
        if not u: return
        c = psycopg2.connect(u); cur = c.cursor()
        cur.execute("""INSERT INTO call_analysis (account_id, call_id, transcript, analysis, minutes)
            VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT (account_id, call_id) DO UPDATE SET transcript=EXCLUDED.transcript, analysis=EXCLUDED.analysis""",
            (account_id, int(call_id), transcript, _j.dumps(analysis), float(minutes or 0)))
        c.commit(); c.close()
    except Exception:
        pass


def _report_recommendations(account_id: str, managers: list, totals: dict) -> dict:
    """GPT-рекомендации по отделу и по каждому менеджеру на основе цифр."""
    import os, json as _j, requests
    from proxy_pool import get_intl_requests_proxies
    mgr_txt = "\n".join(
        f"- Менеджер {m['phone']}: звонков {m['total']}, отвечено {m['answered']}, пропущено {m['missed']}, минут {m['minutes']}"
        for m in managers
    )
    prompt = (
        "Ты — руководитель отдела продаж. По статистике звонков ниже дай короткие практичные рекомендации.\n"
        f"ВСЕГО: звонков {totals['total']}, отвечено {totals['answered']}, пропущено {totals['missed']}, минут {totals['minutes']}.\n"
        f"ПО МЕНЕДЖЕРАМ:\n{mgr_txt}\n\n"
        "Верни ТОЛЬКО JSON без markdown:\n"
        '{"overall": ["2-4 рекомендации по отделу"], '
        '"by_manager": [{"phone": "номер", "note": "1 фраза — что улучшить этому менеджеру"}]}'
    )
    try:
        api_key = os.environ.get("OPENAI_API_KEY")
        r = requests.post("https://api.openai.com/v1/chat/completions",
            headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
            json={"model": "gpt-5.4", "messages": [{"role": "user", "content": prompt}], "max_completion_tokens": 800},
            proxies=get_intl_requests_proxies(), timeout=90)
        if r.status_code != 200:
            return {"overall": [], "by_manager": []}
        data = r.json()
        try:
            from app.usage import log_usage as _lc
            u = data.get("usage", {})
            _lc(account_id, "openai", "gpt-5.4", "отчёт РОП рекомендации", u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
        except Exception:
            pass
        raw = data["choices"][0]["message"]["content"].strip().replace("```json", "").replace("```", "").strip()
        return _j.loads(raw)
    except Exception:
        return {"overall": [], "by_manager": []}


_CALLS_CACHE = {}


def _get_calls_retry(token, date_from, date_to, tries: int = 2, cache_key=None):
    """Avito иногда отвечает 200 с пустым списком. Пробуем пару раз,
    а если всё равно пусто — отдаём последний удачный ответ за этот же период (до 15 мин)."""
    import time as _t
    code, data = 0, {}
    key = (str(cache_key or ""), date_from, date_to)
    for i in range(max(1, tries)):
        code, data = _get_calls(token, date_from, date_to)
        calls = data.get("calls", []) if isinstance(data, dict) else []
        if calls:
            _CALLS_CACHE[key] = (_t.time(), data)
            return code, data
        if i < tries - 1:
            _t.sleep(2.0)
    cached = _CALLS_CACHE.get(key)
    if cached and (_t.time() - cached[0]) < 900:
        return 200, cached[1]
    return code, data


import re as _re_mask


def _mask_phones_html(html: str) -> str:
    """Прячет номера телефонов в готовом HTML — для показа отчёта как примера."""
    def _rep(m):
        raw = m.group(0)
        d = _re_mask.sub(r"\D", "", raw)
        if len(d) < 10:
            return raw
        return d[:2] + "*" * (len(d) - 6) + d[-4:]
    return _re_mask.sub(r"(?<!\d)(?:\+7|8|7)[\s\-\(]?\d{3}[\s\-\)]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}(?!\d)", _rep, html)


@router.get("/report")
def calltracking_report(account_id: str, date_from: str = None, date_to: str = None, owner: bool = False, max_calls: int = 15, mask_phones: bool = False):
    """PDF-отчёт по звонкам за период: воронка, разбивка по менеджерам (sellerPhone), рекомендации, стоимость."""
    import io, datetime
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image as RLImage, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from fastapi.responses import StreamingResponse

    token, err = _ct_token(account_id)
    if err:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail=err)
    _rb = get_rop_reports(account_id, "calls")
    if _rb["left"] <= 0:
        from fastapi.responses import JSONResponse
        msg = ("Отчёты по звонкам в пакете закончились (%d из %d использовано)." % (_rb["used"], _rb["purchased"])) if _rb["purchased"] else "Тариф ИИ РОП не подключён. Оплатите пакет, чтобы формировать отчёты."
        return JSONResponse(status_code=402, content={"status": "no_reports", "message": msg, "balance": _rb})

    now = datetime.datetime.utcnow()
    if not date_from:
        date_from = (now - datetime.timedelta(days=30)).strftime("%Y-%m-%dT00:00:00Z")
    if not date_to:
        date_to = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    code, data = _get_calls_retry(token, date_from, date_to, cache_key=account_id)
    calls = data.get("calls", []) if isinstance(data, dict) else []

    def g(c, k, d=0):
        return (c.get("call") or c).get(k, d)

    if not calls:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"status": "no_calls",
            "message": "За выбранный период звонков не найдено. Проверьте даты или попробуйте ещё раз — Avito иногда отвечает не сразу."})

    total = len(calls)
    answered = sum(1 for c in calls if g(c, "talkDuration", 0) > 0)
    missed = total - answered
    total_sec = sum(g(c, "talkDuration", 0) for c in calls)
    total_min = round(total_sec / 60, 1)
    _whisper_per_min = _WHISPER_MIN_USD * _USD_RUB  # ~0.57₽/мин расшифровка
    # полная стоимость минуты ГЛУБОКОГО разбора: whisper/мин + GPT-разбор размазан по минутам звонка
    if total_min > 0 and answered > 0:
        cost_min_rub = round(_whisper_per_min + (answered * _GPT_CALL_RUB) / total_min, 2)
    else:
        cost_min_rub = round(_whisper_per_min, 2)
    # себестоимость этого отчёта: (whisper+разбор) по отвеченным + рекомендации
    report_cost = round(total_min * _whisper_per_min + answered * _GPT_CALL_RUB + _GPT_REPORT_RUB, 2)
    cost_total = report_cost

    # группировка по менеджерам (sellerPhone)
    mgr = {}
    for c in calls:
        ph = g(c, "sellerPhone", "") or "не указан"
        m = mgr.setdefault(ph, {"phone": ph, "total": 0, "answered": 0, "missed": 0, "sec": 0})
        m["total"] += 1
        if g(c, "talkDuration", 0) > 0:
            m["answered"] += 1; m["sec"] += g(c, "talkDuration", 0)
        else:
            m["missed"] += 1
    managers = []
    for m in mgr.values():
        m["minutes"] = round(m["sec"] / 60, 1)
        managers.append(m)
    managers.sort(key=lambda x: -x["total"])

    totals = {"total": total, "answered": answered, "missed": missed, "minutes": total_min}
    recs = _report_recommendations(account_id, managers, totals)

    # подробный разбор отвеченных звонков (лимит, чтобы не гонять сотни)
    _MAX_ANALYZE = max(1, min(int(max_calls or 15), 200))
    call_reviews = []
    answered_calls = [c for c in calls if g(c, "talkDuration", 0) > 0][:_MAX_ANALYZE]
    for c in answered_calls:
        cid = g(c, "callId")
        if not cid:
            continue
        try:
            _c = _cache_get_analysis(account_id, cid)
            if _c:
                _c["_phone"] = g(c, "buyerPhone", "")
                _c["_time"] = g(c, "callTime", "")[:16].replace("T", " ")
                _c["_min"] = round(g(c, "talkDuration", 0) / 60, 1)
                call_reviews.append(_c)
                continue
            if get_rop_minutes(account_id)["left"] <= 0:
                break
            rc, audio, ctype = _get_record(token, cid)
            if rc != 200 or not audio:
                continue
            ext = "mp3" if ("mpeg" in ctype or "mp3" in ctype) else "wav"
            tr = _asr_fix(account_id, _transcribe(audio, f"call_{cid}.{ext}", _asr_hint(account_id)))
            if not tr.strip():
                continue
            an = _analyze_call(tr, account_id)
            if an.get("error"):
                continue
            _cache_put_analysis(account_id, cid, tr, an, round(g(c, "talkDuration", 0) / 60, 1))
            an["_phone"] = g(c, "buyerPhone", "")
            an["_time"] = g(c, "callTime", "")[:16].replace("T", " ")
            an["_min"] = round(g(c, "talkDuration", 0) / 60, 1)
            call_reviews.append(an)
        except Exception:
            continue
    rec_by = {r.get("phone"): r.get("note") for r in recs.get("by_manager", [])}

    # --- диаграммы (чистый минимал) ---
    import matplotlib.font_manager as fm
    plt.rcParams["font.family"] = "DejaVu Sans"
    C_BLUE, C_GREEN, C_RED = "#2F6FED", "#12B76A", "#F04438"

    # 1) DONUT — тонкое кольцо, цифра в центре, БЕЗ легенды (легенда будет в HTML)
    fig1, ax1 = plt.subplots(figsize=(3.6, 3.6))
    if total:
        ax1.pie([answered, missed], colors=[C_GREEN, C_RED], startangle=90,
                wedgeprops=dict(width=0.32, edgecolor="white", linewidth=4))
        ax1.text(0, 0.08, str(total), ha="center", va="center", fontsize=30, fontweight="bold", color="#101828")
        ax1.text(0, -0.24, "звонков", ha="center", va="center", fontsize=11, color="#98A2B3")
    else:
        ax1.pie([1], colors=["#EAECF0"]); ax1.text(0,0,"нет данных",ha="center",va="center",color="#98A2B3")
    ax1.set_aspect("equal")
    buf1 = io.BytesIO(); fig1.savefig(buf1, format="png", dpi=160, bbox_inches="tight", transparent=True); plt.close(fig1); buf1.seek(0)

    # 2) БАРЫ по менеджерам — аккуратные, подписи слева, значения справа, без наложений
    buf2 = None
    if managers:
        nm = len(managers)
        fig2, ax2 = plt.subplots(figsize=(7.2, max(1.4, 0.6*nm+0.8)))
        names = []
        for m in managers:
            ph = m["phone"]
            names.append(ph if len(ph)<=12 else "…"+ph[-7:])
        yy = list(range(nm))
        ans = [m["answered"] for m in managers]
        mis = [m["missed"] for m in managers]
        ax2.barh(yy, ans, color=C_GREEN, height=0.5, zorder=3)
        ax2.barh(yy, mis, left=ans, color=C_RED, height=0.5, zorder=3)
        for i,(a,mm) in enumerate(zip(ans,mis)):
            if a>0: ax2.text(a/2, i, str(a), ha="center", va="center", color="white", fontsize=10, fontweight="bold")
            if mm>0: ax2.text(a+mm/2, i, str(mm), ha="center", va="center", color="white", fontsize=10, fontweight="bold")
            ax2.text(a+mm+max(ans+mis)*0.03, i, f"{a+mm}", va="center", fontsize=10, color="#667085")
        ax2.set_yticks(yy); ax2.set_yticklabels(names, fontsize=11, color="#344054")
        ax2.invert_yaxis()
        for sp in ["top","right","left","bottom"]: ax2.spines[sp].set_visible(False)
        ax2.tick_params(length=0); ax2.set_xticks([])
        ax2.margins(x=0.14, y=0.15)
        buf2 = io.BytesIO(); fig2.savefig(buf2, format="png", dpi=160, bbox_inches="tight", transparent=True); plt.close(fig2); buf2.seek(0)

    # --- сборка PDF ---
    # ==== HTML → PDF (эталонный стиль Бориса, weasyprint) ====
    import base64 as _b64
    from weasyprint import HTML as _WHTML

    def _img_b64(buf):
        return "data:image/png;base64," + _b64.b64encode(buf.getvalue()).decode()

    donut_img = _img_b64(buf1)
    bars_img = _img_b64(buf2) if buf2 else ""

    ans_pct = round(answered/total*100) if total else 0
    mis_pct = round(missed/total*100) if total else 0

    def _metric_card(icon, grad, num, label, badge, badge_color, blob):
        return f"""<div class="card">
          <div class="blob" style="background:{blob}"></div>
          <div class="ic" style="background:{grad}">{icon}</div>
          <div class="num">{num}</div>
          <div class="lbl">{label}</div>
          <div class="badge" style="background:{badge_color}1A;color:{badge_color}">{badge}</div>
        </div>"""

    cards_html = (
        _metric_card('<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z"/></svg>', "linear-gradient(135deg,#5A8DFF,#2F6FED)", total, "Всего звонков", "за период", "#2F6FED", "#EAF1FF") +
        _metric_card('<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>', "linear-gradient(135deg,#37D68A,#12B76A)", f"{answered} · {ans_pct}%", "Отвечено", "в работе", "#027A48", "#E7F8EF") +
        _metric_card('<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>', "linear-gradient(135deg,#FF7A6E,#F04438)", f"{missed} · {mis_pct}%", "Пропущено", "упущено", "#B42318", "#FDECEC") +
        _metric_card('<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/></svg>', "linear-gradient(135deg,#98A2B3,#667085)", f"{total_min}", "Минут разговора", "всего", "#667085", "#F0F1F3")
    )

    # разбивка по менеджерам — строки таблицы
    mgr_rows = ""
    for m in managers:
        note = (rec_by.get(m["phone"]) or "—")
        mgr_rows += f"""<tr>
          <td class="mgr-ph">{m['phone']}</td>
          <td class="ctr">{m['total']}</td>
          <td class="ctr" style="color:#027A48">{m['answered']}</td>
          <td class="ctr" style="color:#B42318">{m['missed']}</td>
          <td class="ctr">{m['minutes']}</td>
          <td class="mgr-note">{note}</td>
        </tr>"""

    # подробный разбор звонков — карточки
    reviews_html = ""
    for rv in call_reviews:
        def _ul(items, color):
            if not items: return ""
            if isinstance(items, str): items=[items]
            lis = "".join(f"<li>{x}</li>" for x in items)
            return f'<div class="rv-block" style="--c:{color}"><ul>{lis}</ul></div>'
        good = _ul(rv.get("strong_moments"), "#027A48")
        grow = _ul(rv.get("growth_points"), "#1570EF")
        tips = _ul(rv.get("speech_tips"), "#5925DC")
        sw_html = ""
        sw = rv.get("stop_words") or []
        if sw:
            items=[]
            for w in sw:
                if isinstance(w, dict):
                    items.append(f'<b>«{w.get("phrase","")}»</b> — {w.get("why","")}. <span style="color:#027A48">Лучше: {w.get("better","")}</span>')
                else:
                    items.append(f'«{w}»')
            sw_html = '<div class="rv-block" style="--c:#B54708"><div class="rv-h">⚠ Слова, которых лучше избегать</div><ul>'+"".join(f"<li>{x}</li>" for x in items)+'</ul></div>'
        rec = rv.get("recommendation") or ""
        rec_html = f'<div class="rv-rec">💡 <b>Главный совет:</b> {rec}</div>' if rec else ""
        blocks = ""
        if good: blocks += '<div class="rv-h" style="color:#027A48">✓ Что хорошо</div>'+good
        if grow: blocks += '<div class="rv-h" style="color:#1570EF">↑ Точки роста</div>'+grow
        if tips: blocks += '<div class="rv-h" style="color:#5925DC">🎙 По речи</div>'+tips
        blocks += sw_html + rec_html
        reviews_html += f"""<div class="rv-card">
          <div class="rv-head"><b>{rv.get('client_name') or 'Клиент'}</b> · {rv.get('role','')} · {rv.get('_phone','')} · {rv.get('_time','')} · <span class="rv-score">{rv.get('score','-')}/100</span></div>
          {blocks}
        </div>"""

    recs_html = ""
    for r in recs.get("overall", []):
        recs_html += f'<div class="rec-item">{r}</div>'

    cost_html = ""

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
    @page {{ size: A4; margin: 1.2cm; }}
    * {{ font-family: 'DejaVu Sans', sans-serif; box-sizing: border-box; }}
    body {{ color:#1D2939; font-size:12px; }}
    .hero {{ background:linear-gradient(135deg,#2F6FED,#1E4FD8); border-radius:20px; padding:28px 32px; color:#fff; margin-bottom:20px; }}
    .hero h1 {{ margin:0 0 6px; font-size:24px; font-weight:800; }}
    .hero p {{ margin:0; font-size:13px; opacity:.9; }}
    .cards {{ display:flex; gap:14px; margin-bottom:22px; }}
    .card {{ flex:1; background:#fff; border:1px solid #E3E7F0; border-radius:16px; padding:22px 16px; text-align:center; position:relative; overflow:hidden; box-shadow:0 4px 10px rgba(16,24,40,.06), 0 12px 24px rgba(16,24,40,.05); }}
    .blob {{ position:absolute; top:-25px; right:-25px; width:80px; height:80px; border-radius:50%; opacity:.35; z-index:0; }}
    .ic {{ width:48px; height:48px; border-radius:50%; margin:0 auto 12px; display:flex; align-items:center; justify-content:center; box-shadow:0 8px 18px rgba(16,24,40,.18); position:relative; z-index:1; }}
    .num {{ font-size:22px; font-weight:800; letter-spacing:-.02em; color:#1D2939; }}
    .lbl {{ font-size:11px; color:#667085; margin:2px 0 8px; }}
    .badge {{ display:inline-block; font-size:10px; font-weight:700; border-radius:20px; padding:3px 12px; }}
    .cost {{ background:#F8FAFF; border:1px solid #D0DEFF; border-radius:12px; padding:12px 16px; font-size:12px; color:#344054; margin-bottom:18px; }}
    .sec-h {{ font-size:16px; font-weight:700; color:#1D2939; margin:18px 0 12px; }}
    .charts {{ text-align:center; margin:10px 0; }}
    .charts img {{ max-width:42%; }}
    .legend {{ margin-top:6px; display:flex; justify-content:center; gap:18px; }}
    .lg {{ font-size:11px; color:#475467; display:inline-flex; align-items:center; gap:6px; }}
    .lg i {{ width:10px; height:10px; border-radius:3px; display:inline-block; }}
    .bars img {{ max-width:90%; }}
    table {{ width:100%; border-collapse:collapse; font-size:11px; }}
    th {{ background:#2F6FED; color:#fff; padding:9px 8px; text-align:left; font-weight:700; }}
    th:first-child {{ border-radius:10px 0 0 0; }} th:last-child {{ border-radius:0 10px 0 0; }}
    td {{ padding:8px; border-bottom:1px solid #EAECF0; vertical-align:top; }}
    tr:nth-child(even) td {{ background:#FAFAFB; }}
    .ctr {{ text-align:center; }} .mgr-ph {{ font-weight:700; }} .mgr-note {{ color:#475467; }}
    .rv-card {{ background:#fff; border:1px solid #E3E7F0; border-radius:14px; padding:16px 18px; margin-bottom:12px; box-shadow:0 1px 3px rgba(16,24,40,.05); }}
    .rv-head {{ font-size:13px; color:#344054; margin-bottom:8px; }}
    .rv-score {{ color:#2F6FED; font-weight:800; }}
    .rv-h {{ font-size:12px; font-weight:700; margin:8px 0 3px; }}
    .rv-block ul {{ margin:0 0 4px; padding-left:18px; }}
    .rv-block li {{ font-size:11px; color:#475467; margin:2px 0; }}
    .rv-block {{ border-left:3px solid var(--c); padding-left:10px; }}
    .rv-rec {{ background:#EEF4FF; border-radius:10px; padding:8px 12px; margin-top:8px; font-size:11px; color:#344054; }}
    .rec-item {{ background:#EEF4FF; border-left:4px solid #2F6FED; border-radius:8px; padding:10px 14px; margin-bottom:8px; font-size:12px; color:#344054; }}
    .foot {{ text-align:center; color:#98A2B3; font-size:10px; margin-top:20px; }}
    </style></head><body>
    <div class="hero"><h1>📞 Отчёт по звонкам</h1><p>ИИ Руководитель отдела продаж · {date_from[:10]} — {date_to[:10]}</p></div>
    <div class="cards">{cards_html}</div>
    {cost_html}
    <div class="charts"><img src="{donut_img}">
      <div class="legend"><span class="lg"><i style="background:#12B76A"></i>Отвечено — {answered}</span><span class="lg"><i style="background:#F04438"></i>Пропущено — {missed}</span></div>
    </div>
    {'<div class="sec-h">Звонки по менеджерам</div><div class="charts bars"><img src="'+bars_img+'"></div>' if bars_img else ''}
    <div class="sec-h">Разбивка по менеджерам</div>
    <table><tr><th>Менеджер</th><th>Звонков</th><th>Отвечено</th><th>Пропущено</th><th>Минут</th><th>Рекомендация</th></tr>{mgr_rows}</table>
    {'<div class="sec-h">Подробный разбор звонков</div>'+reviews_html if reviews_html else ''}
    {'<div class="sec-h">Рекомендации по отделу</div>'+recs_html if recs_html else ''}
    <div class="foot">Сформировано в БОРИС · boris-ai.pro · {datetime.datetime.now().strftime("%d.%m.%Y")}</div>
    </body></html>"""

    if mask_phones:
        html = _mask_phones_html(html)
    out = io.BytesIO()
    _WHTML(string=html).write_pdf(out)
    out.seek(0)
    fname = f"otchet_zvonki_{date_from[:10]}_{date_to[:10]}.pdf"
    consume_rop_report(account_id, "calls")
    try:
        _tg_notify(account_id, f"\U0001F4C4 Готов отчёт по звонкам\n\nПериод: {date_from[:10]} \u2014 {date_to[:10]}\nЗвонков: {total} \u00b7 разобрано: {len(call_reviews)}\n\nСкачать можно в кабинете: Продажи \u2192 РОП \u2192 Отчёты", topic="sales")
    except Exception:
        pass
    # сохраняем отчёт в архив (хранится 30 дней)
    try:
        import os as _os, psycopg2
        _pdf = out.getvalue()
        _dir = _os.path.join(_os.path.dirname(__file__), "..", "..", "reports", account_id)
        _os.makedirs(_dir, exist_ok=True)
        _u = _db_url()
        if _u:
            _cc = psycopg2.connect(_u); _cur = _cc.cursor()
            _cur.execute("""INSERT INTO call_reports (account_id, date_from, date_to, calls_total, calls_analyzed, file_path, size_bytes)
                VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (account_id, date_from[:10], date_to[:10], total, len(call_reviews), "", len(_pdf)))
            _rid = _cur.fetchone()[0]
            _fp = _os.path.join(_dir, f"report_{_rid}.pdf")
            open(_fp, "wb").write(_pdf)
            _cur.execute("UPDATE call_reports SET file_path=%s WHERE id=%s", (_fp, _rid))
            _cc.commit(); _cc.close()
        out.seek(0)
    except Exception:
        out.seek(0)
    try:
        _save_report(account_id, out.getvalue(), date_from, date_to, fname)
    except Exception as _se:
        print("[report] не сохранён:", str(_se)[:200], flush=True)
    out.seek(0)
    return StreamingResponse(out, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'})



# ================= ХРАНИЛИЩЕ ОТЧЁТОВ РОП =================
def _save_report(account_id, pdf_bytes, date_from, date_to, fname):
    import os as _os, json as _json, uuid as _uuid, datetime as _dt
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    base = _os.path.join("/root/BORIS/backend/rop_reports", str(account_id))
    _os.makedirs(base, exist_ok=True)
    rid = _uuid.uuid4().hex[:12]
    disk = _os.path.join(base, rid + ".pdf")
    with open(disk, "wb") as fh:
        fh.write(pdf_bytes)
    rec = {"id": rid, "file": disk, "name": fname,
           "date_from": str(date_from)[:10], "date_to": str(date_to)[:10],
           "created_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
           "size_kb": round(len(pdf_bytes) / 1024)}
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "rop_reports").first()
        items = _json.loads(row.value) if row else []
        items.insert(0, rec)
        for old in items[50:]:
            try: _os.remove(old.get("file", ""))
            except Exception: pass
        items = items[:50]
        if row:
            row.value = _json.dumps(items, ensure_ascii=False)
        else:
            db.add(Storage(account_id=account_id, key="rop_reports", value=_json.dumps(items, ensure_ascii=False)))
        db.commit()
    finally:
        db.close()
    return rid


@router.get("/reports")
def list_reports(account_id: str):
    import json as _json
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "rop_reports").first()
        items = _json.loads(row.value) if row else []
    finally:
        db.close()
    return {"reports": [{k: v for k, v in it.items() if k != "file"} for it in items]}


@router.get("/report_file")
def report_file(account_id: str, id: str):
    import json as _json, os as _os
    from fastapi.responses import FileResponse
    from fastapi import HTTPException
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "rop_reports").first()
        items = _json.loads(row.value) if row else []
    finally:
        db.close()
    rec = next((x for x in items if x.get("id") == id), None)
    if not rec or not _os.path.exists(rec.get("file", "")):
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(rec["file"], media_type="application/pdf", filename=rec.get("name", "report.pdf"))



# ============ АРХИВ ОТЧЁТОВ (хранение 30 дней) ============
_REPORT_KEEP_DAYS = 30


def _purge_old_reports():
    """Удаляет отчёты старше 30 дней — файлы и записи."""
    try:
        import os as _os, psycopg2
        u = _db_url()
        if not u: return
        c = psycopg2.connect(u); cur = c.cursor()
        cur.execute("SELECT id, file_path FROM call_reports WHERE created_at < NOW() - INTERVAL '%s days'" % _REPORT_KEEP_DAYS)
        rows = cur.fetchall()
        for rid, fp in rows:
            try:
                if fp and _os.path.exists(fp): _os.remove(fp)
            except Exception:
                pass
        if rows:
            cur.execute("DELETE FROM call_reports WHERE created_at < NOW() - INTERVAL '%s days'" % _REPORT_KEEP_DAYS)
            c.commit()
        c.close()
    except Exception:
        pass


@router.get("/reports/list")
def reports_list(account_id: str):
    """Список сохранённых отчётов за последние 30 дней."""
    _purge_old_reports()
    try:
        import psycopg2
        u = _db_url()
        if not u: return {"status": "error", "items": []}
        c = psycopg2.connect(u); cur = c.cursor()
        cur.execute("""SELECT id, date_from, date_to, calls_total, calls_analyzed, size_bytes, created_at, COALESCE(kind,'calls')
            FROM call_reports WHERE account_id=%s ORDER BY created_at DESC""", (account_id,))
        items = []
        for r in cur.fetchall():
            created = r[6]
            left = _REPORT_KEEP_DAYS - (__import__("datetime").datetime.utcnow() - created).days
            items.append({
                "id": r[0], "date_from": r[1], "date_to": r[2],
                "calls_total": r[3], "calls_analyzed": r[4],
                "size_kb": round((r[5] or 0) / 1024),
                "created_at": created.strftime("%d.%m.%Y %H:%M"),
                "days_left": max(0, left),
                "kind": r[7],
            })
        c.close()
        return {"status": "ok", "items": items, "keep_days": _REPORT_KEEP_DAYS}
    except Exception as e:
        return {"status": "error", "message": str(e)[:150], "items": []}


@router.get("/reports/download")
def reports_download(account_id: str, id: int):
    """Скачать сохранённый отчёт — повторная генерация не нужна, платить не надо."""
    from fastapi.responses import FileResponse
    from fastapi import HTTPException
    import os as _os, psycopg2
    u = _db_url()
    if not u: raise HTTPException(status_code=500, detail="db")
    c = psycopg2.connect(u); cur = c.cursor()
    cur.execute("SELECT file_path, date_from, date_to FROM call_reports WHERE id=%s AND account_id=%s", (id, account_id))
    row = cur.fetchone(); c.close()
    if not row or not row[0] or not _os.path.exists(row[0]):
        raise HTTPException(status_code=404, detail="Отчёт не найден или уже удалён")
    return FileResponse(row[0], media_type="application/pdf",
        filename=f"otchet_zvonki_{row[1]}_{row[2]}.pdf")


@router.post("/reports/delete")
def reports_delete(account_id: str, body: dict = Body(...)):
    """Удалить выбранные отчёты (или все)."""
    ids = body.get("ids") or []
    delete_all = bool(body.get("all"))
    try:
        import os as _os, psycopg2
        u = _db_url()
        if not u: return {"status": "error"}
        c = psycopg2.connect(u); cur = c.cursor()
        if delete_all:
            cur.execute("SELECT id, file_path FROM call_reports WHERE account_id=%s", (account_id,))
        else:
            if not ids: return {"status": "error", "message": "не выбраны отчёты"}
            cur.execute("SELECT id, file_path FROM call_reports WHERE account_id=%s AND id = ANY(%s)", (account_id, [int(x) for x in ids]))
        rows = cur.fetchall()
        for rid, fp in rows:
            try:
                if fp and _os.path.exists(fp): _os.remove(fp)
            except Exception:
                pass
        if rows:
            cur.execute("DELETE FROM call_reports WHERE account_id=%s AND id = ANY(%s)", (account_id, [r[0] for r in rows]))
            c.commit()
        c.close()
        return {"status": "ok", "deleted": len(rows)}
    except Exception as e:
        return {"status": "error", "message": str(e)[:150]}



@router.get("/report/estimate")
def report_estimate(account_id: str, date_from: str = None, date_to: str = None, max_calls: int = 15):
    """Оценка ДО генерации: сколько звонков новых (платно) и сколько уже разобрано (0₽). OpenAI не дёргается."""
    import datetime, psycopg2
    token, err = _ct_token(account_id)
    if err:
        return {"status": "no_access", "message": err}
    now = datetime.datetime.utcnow()
    if not date_from:
        date_from = (now - datetime.timedelta(days=30)).strftime("%Y-%m-%dT00:00:00Z")
    if not date_to:
        date_to = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    code, data = _get_calls_retry(token, date_from, date_to, cache_key=account_id)
    calls = data.get("calls", []) if isinstance(data, dict) else []
    def g(c, k, d=0):
        return (c.get("call") or c).get(k, d)
    answered = [c for c in calls if g(c, "talkDuration", 0) > 0]
    lim = max(1, min(int(max_calls or 15), 200))
    take = answered[:lim]
    cached_ids = set()
    try:
        u = _db_url()
        if u and take:
            cc = psycopg2.connect(u); cur = cc.cursor()
            cur.execute("SELECT call_id FROM call_analysis WHERE account_id=%s AND call_id = ANY(%s)",
                        (account_id, [int(g(c, "callId")) for c in take if g(c, "callId")]))
            cached_ids = {int(r[0]) for r in cur.fetchall()}
            cc.close()
    except Exception:
        pass
    new_calls = [c for c in take if int(g(c, "callId") or 0) not in cached_ids]
    new_min = round(sum(g(c, "talkDuration", 0) for c in new_calls) / 60, 1)
    cost = round(new_min * _WHISPER_MIN_USD * _USD_RUB + len(new_calls) * _GPT_CALL_RUB + _GPT_REPORT_RUB, 2)
    return {
        "status": "ok",
        "total": len(calls),
        "answered": len(answered),
        "to_analyze": len(take),
        "already_done": len(take) - len(new_calls),
        "new_calls": len(new_calls),
        "new_minutes": new_min,
        "cost_rub": cost,
        "eta_sec": int(len(new_calls) * 15),
    }



# ============ ПАКЕТ МИНУТ РОП ============
def _is_owner_account(account_id: str) -> bool:
    """Аккаунты владельца сервиса — без лимитов и списаний."""
    try:
        import psycopg2
        u = _db_url()
        if not u: return False
        c = psycopg2.connect(u); cur = c.cursor()
        cur.execute("""SELECT u.role FROM accounts a JOIN users u ON u.id = a.owner_user_id
                       WHERE a.account_id=%s""", (account_id,))
        row = cur.fetchone(); c.close()
        return bool(row and str(row[0]).lower() == "owner")
    except Exception:
        return False


def get_rop_minutes(account_id: str) -> dict:
    """Остаток пакета минут РОП. У владельца — безлимит."""
    if _is_own_account(account_id):
        return {"purchased": 0, "used": 0, "left": 999999, "active": True, "unlimited": True}
    import json as _j
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    db = SessionLocal()
    purchased = 0
    _pstart = _pend = None
    _overflow = 0.0
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "billing").first()
        if row:
            try:
                _d = _j.loads(row.value)
                purchased = float(_d.get("rop_minutes_purchased", 0) or 0)
                _pstart = _d.get("rop_period_start")
                _pend = _d.get("rop_paid_until")
                _overflow = float(_d.get("rop_overflow_minutes", 0) or 0)
            except Exception: purchased = 0
    finally:
        db.close()
    used = 0.0
    try:
        import psycopg2
        u = _db_url()
        if u:
            c = psycopg2.connect(u); cur = c.cursor()
            if _pstart:
                cur.execute("SELECT COALESCE(SUM(minutes),0) FROM call_analysis WHERE account_id=%s AND created_at >= %s", (account_id, _pstart))
            else:
                cur.execute("SELECT COALESCE(SUM(minutes),0) FROM call_analysis WHERE account_id=%s", (account_id,))
            used = float(cur.fetchone()[0] or 0); c.close()
    except Exception:
        pass
    used = used + _overflow
    left = round(purchased - used, 1)
    _alive = True
    if _pend:
        from datetime import datetime as _dt
        try: _alive = _dt.utcnow() < _dt.fromisoformat(_pend)
        except Exception: _alive = True
    return {"purchased": round(purchased, 1), "used": round(used, 1), "left": left, "active": left > 0 and _alive, "expired": not _alive}


def add_rop_minutes(account_id: str, amount: int) -> dict:
    """Начисляет пакет минут РОП — вызывается при подтверждении оплаты."""
    import json as _j
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "billing").first()
        data = {}
        if row:
            try: data = _j.loads(row.value)
            except Exception: data = {}
        data["rop_minutes_purchased"] = float(data.get("rop_minutes_purchased", 0) or 0) + float(amount)
        if row: row.value = _j.dumps(data, ensure_ascii=False)
        else: db.add(Storage(account_id=account_id, key="billing", value=_j.dumps(data, ensure_ascii=False)))
        db.commit()
        return {"status": "ok", "rop_minutes_purchased": data["rop_minutes_purchased"]}
    finally:
        db.close()


@router.get("/minutes/balance")
def minutes_balance(account_id: str):
    b = get_rop_minutes(account_id)
    b["status"] = "ok"
    return b



# ============ ЛИМИТ ОТЧЁТОВ РОП (30 по звонкам + 30 по перепискам) ============
def _billing_get(account_id: str) -> dict:
    import json as _j
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "billing").first()
        if not row: return {}
        try: return _j.loads(row.value)
        except Exception: return {}
    finally:
        db.close()


def _billing_set(account_id: str, data: dict):
    import json as _j
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "billing").first()
        if row: row.value = _j.dumps(data, ensure_ascii=False)
        else: db.add(Storage(account_id=account_id, key="billing", value=_j.dumps(data, ensure_ascii=False)))
        db.commit()
    finally:
        db.close()


def add_rop_reports(account_id: str, amount: int) -> dict:
    """Начисляет пакет отчётов РОП при оплате."""
    d = _billing_get(account_id)
    d["rop_reports_purchased"] = int(d.get("rop_reports_purchased", 0) or 0) + int(amount)
    _billing_set(account_id, d)
    return {"status": "ok", "rop_reports_purchased": d["rop_reports_purchased"]}


def get_rop_reports(account_id: str, kind: str = "calls") -> dict:
    """Остаток отчётов: kind='calls' (звонки) или 'chats' (переписки). У владельца — безлимит."""
    if _is_own_account(account_id):
        return {"purchased": 0, "used": 0, "left": 999999, "active": True, "unlimited": True}
    d = _billing_get(account_id)
    purchased_total = int(d.get(f"rop_reports_purchased_{kind}", 0) or 0)
    half = purchased_total // 2
    used = int(d.get(f"rop_reports_used_{kind}", 0) or 0)
    return {"purchased": half, "used": used, "left": max(0, half - used), "active": (half - used) > 0}


def consume_rop_report(account_id: str, kind: str = "calls") -> bool:
    """Списывает один отчёт. False — если лимит исчерпан."""
    if _is_own_account(account_id):
        return True
    b = get_rop_reports(account_id, kind)
    if b["left"] <= 0:
        if get_rop_minutes(account_id)["left"] <= 0:
            return False
        _add_overflow_minutes(account_id, 1)
        return True
    d = _billing_get(account_id)
    d[f"rop_reports_used_{kind}"] = int(d.get(f"rop_reports_used_{kind}", 0) or 0) + 1
    _billing_set(account_id, d)
    return True


_ROP_PERIOD_DAYS = 30


def _is_own_account(account_id: str) -> bool:
    """Собственный аккаунт Кирилла (флаг accounts.is_own) — только он безлимитный в РОП."""
    import psycopg2
    u = _db_url()
    if not u:
        return False
    try:
        c = psycopg2.connect(u); cur = c.cursor()
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='accounts' AND column_name IN ('account_id','id')")
        cols = [r[0] for r in cur.fetchall()]
        key = "account_id" if "account_id" in cols else "id"
        cur.execute("SELECT COALESCE(is_own,false) FROM accounts WHERE " + key + "=%s", (account_id,))
        r = cur.fetchone(); c.close()
        return bool(r and r[0])
    except Exception as e:
        print("[rop] _is_own_account:", e)
        return False


def _rop_period(account_id: str) -> dict:
    """Текущий период пакета РОП."""
    from datetime import datetime
    d = _billing_get(account_id)
    st, un = d.get("rop_period_start"), d.get("rop_paid_until")
    if not st or not un:
        return {"active": False, "start": None, "until": None, "days_left": 0}
    try:
        u = datetime.fromisoformat(un)
    except Exception:
        return {"active": False, "start": None, "until": None, "days_left": 0}
    now = datetime.utcnow()
    delta = u - now
    left = delta.days + (1 if delta.seconds > 0 else 0)
    return {"active": now < u, "start": st, "until": un, "days_left": max(0, left)}


def _add_overflow_minutes(account_id: str, n: float = 1.0):
    """Списывает n минут за то, что израсходовано сверх своего лимита (переписка или отчёт)."""
    try:
        d = _billing_get(account_id)
        d["rop_overflow_minutes"] = float(d.get("rop_overflow_minutes", 0) or 0) + float(n)
        _billing_set(account_id, d)
    except Exception as e:
        print("[rop] _add_overflow_minutes:", e)


def get_rop_chats(account_id: str) -> dict:
    """Пакет разборов переписок: куплено / израсходовано за период / остаток."""
    if _is_own_account(account_id):
        return {"purchased": 999999, "used": 0, "left": 999999, "unlimited": True}
    d = _billing_get(account_id)
    purchased = int(d.get("rop_chats_purchased", 0) or 0)
    used, st = 0, d.get("rop_period_start")
    if st:
        import psycopg2
        u = _db_url()
        if u:
            try:
                c = psycopg2.connect(u); cur = c.cursor()
                cur.execute("SELECT count(*) FROM chat_analysis WHERE account_id=%s AND created_at >= %s", (account_id, st))
                used = int(cur.fetchone()[0] or 0)
                c.close()
            except Exception as e:
                print("[rop] get_rop_chats:", e)
    return {"purchased": purchased, "used": used, "left": max(0, purchased - used), "unlimited": False}


def add_rop_package(account_id: str, minutes: int = 0, chats: int = 0, rep_calls: int = 0, rep_chats: int = 0, days: int = 0):
    """days>0 — базовый пакет: новый период, счётчики с нуля. days=0 — докупка внутрь текущего периода."""
    from datetime import datetime, timedelta
    d = _billing_get(account_id)
    if days > 0:
        now = datetime.utcnow()
        d["rop_period_start"] = now.isoformat()
        d["rop_paid_until"] = (now + timedelta(days=int(days))).isoformat()
        d["rop_minutes_purchased"] = int(minutes)
        d["rop_chats_purchased"] = int(chats)
        d["rop_reports_purchased_calls"] = int(rep_calls)
        d["rop_reports_purchased_chats"] = int(rep_chats)
        d["rop_overflow_minutes"] = 0
        d["rop_reports_used_calls"] = 0
        d["rop_reports_used_chats"] = 0
    else:
        for k, v in (("rop_minutes_purchased", minutes), ("rop_chats_purchased", chats),
                     ("rop_reports_purchased_calls", rep_calls), ("rop_reports_purchased_chats", rep_chats)):
            d[k] = int(d.get(k, 0) or 0) + int(v)
    _billing_set(account_id, d)
    return d


@router.get("/limits")
def rop_limits(account_id: str):
    """Остатки пакета РОП: минуты + отчёты по звонкам + отчёты по перепискам."""
    return {
        "status": "ok",
        "minutes": get_rop_minutes(account_id),
        "reports_calls": get_rop_reports(account_id, "calls"),
        "reports_chats": get_rop_reports(account_id, "chats"),
        "chats": get_rop_chats(account_id),
        "period": _rop_period(account_id),
    }



# ============ РАЗБОР ПЕРЕПИСОК + ОТЧЁТ ПО ЧАТАМ ============
CHAT_CHECKLIST = [
    "Ответил быстро, не заставил клиента ждать",
    "Поздоровался и представился",
    "Выявил потребность: что нужно, когда, бюджет",
    "Ответил по сути вопроса клиента",
    "Назвал цену или вилку цен",
    "Отработал возражения",
    "Закрыл на следующий шаг (телефон, замер, встреча)",
]


_CHAT_MIN_MSGS = 4
_CHAT_MAX_MSGS = 40
_CHAT_DAY_LIMIT = 15


def _chat_day_used(account_id: str) -> int:
    """Сколько диалогов уже разобрано сегодня по этому аккаунту."""
    import psycopg2
    u = _db_url()
    if not u:
        return 0
    try:
        c = psycopg2.connect(u); cur = c.cursor()
        cur.execute("SELECT count(*) FROM chat_analysis WHERE account_id=%s AND created_at::date = CURRENT_DATE", (account_id,))
        n = cur.fetchone()[0]
        c.close()
        return int(n or 0)
    except Exception as e:
        print("[rop] _chat_day_used:", e)
        return 0


def _get_chats(account_id: str, days: int = 30, limit: int = 15):
    """Диалоги за период: [{chat_id, title, stage, convo}]"""
    import psycopg2
    u = _db_url()
    if not u: return []
    c = psycopg2.connect(u); cur = c.cursor()
    cur.execute("""SELECT l.avito_chat_id, l.item_title, l.stage
        FROM messenger_leads l WHERE l.account_id=%s ORDER BY l.last_msg_at DESC NULLS LAST LIMIT %s""",
        (account_id, limit * 5))
    leads = cur.fetchall()
    out = []
    for chat_id, title, stage in leads:
        cur.execute("""SELECT direction, text FROM messenger_messages
            WHERE avito_chat_id=%s ORDER BY avito_created_at""", (chat_id,))
        msgs = cur.fetchall()
        if len(msgs) < _CHAT_MIN_MSGS:
            continue
        msgs = msgs[-_CHAT_MAX_MSGS:]
        convo = "\n".join([("Клиент: " if d in ("in", "incoming") else "Менеджер: ") + (t or "") for d, t in msgs])
        out.append({"chat_id": str(chat_id), "title": title or "", "stage": stage or "", "convo": convo[:4000]})
        if len(out) >= limit:
            break
    c.close()
    return out


def _analyze_chat(convo: str, account_id: str) -> dict:
    """Мягкий разбор одной переписки по чек-листу — как разбор звонка."""
    import os, json as _j, requests
    from proxy_pool import get_intl_requests_proxies
    checklist_txt = "\n".join(f"{i+1}. {item}" for i, item in enumerate(CHAT_CHECKLIST))
    prompt = (
        "Ты — опытный, доброжелательный наставник по продажам. Разбери переписку менеджера с клиентом в чате Avito "
        "РАЗВИВАЮЩЕ и МЯГКО: хвали за конкретику, зоны роста подавай как возможности ('стоит попробовать…', 'можно усилить…'), "
        "НЕ ругай, не пиши 'провалил' или 'плохо'.\n\n"
        f"ЧЕК-ЛИСТ (по каждому пункту true/false):\n{checklist_txt}\n\n"
        "Верни ТОЛЬКО валидный JSON без markdown:\n"
        '{"client_name": "имя или пусто", "outcome": "чем закончился диалог одной фразой", '
        '"checklist": [{"item": "текст пункта", "done": true/false}], "score": число 0-100, '
        '"recommendation": "главный развивающий совет одной тёплой фразой", '
        '"strong_moments": ["что сделал хорошо — до 4"], '
        '"growth_points": ["зоны роста мягко — до 4"], '
        '"stop_words": [{"phrase": "фраза которую лучше не писать", "why": "чем мешает", "better": "как написать вместо"}], '
        '"speech_tips": ["советы по стилю переписки — до 3"]}\n\n'
        + _account_context(account_id) +
        f"ПЕРЕПИСКА:\n{convo[:6000]}"
    )
    try:
        api_key = os.environ.get("OPENAI_API_KEY")
        r = requests.post("https://api.openai.com/v1/chat/completions",
            headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
            json={"model": "gpt-5.4", "messages": [{"role": "user", "content": prompt}], "max_completion_tokens": 1400},
            proxies=get_intl_requests_proxies(), timeout=120)
        if r.status_code != 200:
            return {"error": f"gpt {r.status_code}"}
        data = r.json()
        try:
            from app.usage import log_usage as _lc
            u = data.get("usage", {})
            _lc(account_id, "openai", "gpt-5.4", "разбор переписки РОП", u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
        except Exception:
            pass
        raw = data["choices"][0]["message"]["content"].strip().replace("```json", "").replace("```", "").strip()
        return _j.loads(raw)
    except Exception as e:
        return {"error": str(e)[:150]}


def _chat_cache_get(account_id: str, chat_id: str):
    try:
        import psycopg2, json as _j
        u = _db_url()
        if not u: return None
        c = psycopg2.connect(u); cur = c.cursor()
        cur.execute("SELECT analysis FROM chat_analysis WHERE account_id=%s AND chat_id=%s", (account_id, str(chat_id)))
        row = cur.fetchone(); c.close()
        if not row: return None
        return row[0] if isinstance(row[0], dict) else _j.loads(row[0])
    except Exception:
        return None


def _chat_cache_put(account_id: str, chat_id: str, analysis: dict):
    try:
        import psycopg2, json as _j
        u = _db_url()
        if not u: return
        c = psycopg2.connect(u); cur = c.cursor()
        cur.execute("""INSERT INTO chat_analysis (account_id, chat_id, analysis) VALUES (%s,%s,%s)
            ON CONFLICT (account_id, chat_id) DO UPDATE SET analysis=EXCLUDED.analysis""",
            (account_id, str(chat_id), _j.dumps(analysis)))
        c.commit(); c.close()
    except Exception:
        pass


@router.get("/chats_report/estimate")
def chats_report_estimate(account_id: str, days: int = 30, max_chats: int = 15):
    chats = _get_chats(account_id, days, max(1, min(int(max_chats or 15), 100)))
    new = [c for c in chats if not _chat_cache_get(account_id, c["chat_id"])]
    _used = _chat_day_used(account_id)
    _left = max(0, _CHAT_DAY_LIMIT - _used)
    _pack = get_rop_chats(account_id)["left"]
    _will = min(len(new), _left, _pack)
    return {"status": "ok", "total": len(chats), "already_done": len(chats) - len(new),
            "new_chats": len(new), "will_analyze": _will, "skipped_by_day_limit": len(new) - _will,
            "day_used": _used, "day_limit": _CHAT_DAY_LIMIT, "day_left": _left, "pack_left": _pack,
            "cost_rub": round(_will * 1.12 + 0.26, 2), "eta_sec": int(_will * 8)}



@router.get("/chats_report")
def chats_report(account_id: str, days: int = 30, max_chats: int = 15, owner: bool = False, mask_phones: bool = False):
    """PDF-отчёт по перепискам: разбор каждого диалога + сводка. Списывает отчёт из пакета chats."""
    import io, datetime, base64 as _b64, os as _os, psycopg2
    from weasyprint import HTML as _WHTML
    from fastapi.responses import StreamingResponse, JSONResponse

    _rb = get_rop_reports(account_id, "chats")
    if _rb["left"] <= 0:
        msg = ("Отчёты по перепискам в пакете закончились (%d из %d использовано)." % (_rb["used"], _rb["purchased"])) if _rb["purchased"] else "Тариф ИИ РОП не подключён. Оплатите пакет, чтобы формировать отчёты."
        return JSONResponse(status_code=402, content={"status": "no_reports", "message": msg, "balance": _rb})

    lim = max(1, min(int(max_chats or 15), 100))
    chats = _get_chats(account_id, days, lim)
    if not chats:
        return JSONResponse(status_code=404, content={"status": "empty", "message": "Нет диалогов для разбора"})

    reviews = []
    _day_used = _chat_day_used(account_id)
    _day_stop = False
    _pack_stop = False
    _chats_left = get_rop_chats(account_id)["left"]
    for ch in chats:
        an = _chat_cache_get(account_id, ch["chat_id"])
        if not an:
            if _chats_left <= 0:
                if get_rop_minutes(account_id)["left"] > 0:
                    _add_overflow_minutes(account_id, 1)
                else:
                    _pack_stop = True
                    continue
            if _day_used >= _CHAT_DAY_LIMIT:
                _day_stop = True
                continue
            an = _analyze_chat(ch["convo"], account_id)
            if an.get("error"):
                continue
            _chat_cache_put(account_id, ch["chat_id"], an)
            _day_used += 1
            _chats_left -= 1
        an = dict(an)
        an["_title"] = ch["title"]
        an["_stage"] = ch["stage"]
        reviews.append(an)

    scores = [r.get("score", 0) for r in reviews if isinstance(r.get("score"), (int, float))]
    avg = round(sum(scores) / len(scores)) if scores else 0
    stages = {}
    for r in reviews:
        st = r.get("_stage") or "не указан"
        stages[st] = stages.get(st, 0) + 1

    recs = _report_recommendations(account_id, [], {"total": len(reviews), "answered": len(reviews), "missed": 0, "minutes": 0})

    def card(icon, grad, num, label, blob):
        return f'''<div class="card"><div class="blob" style="background:{blob}"></div><div class="ic" style="background:{grad}">{icon}</div><div class="num">{num}</div><div class="lbl">{label}</div></div>'''

    svg_chat = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round"><path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 8.5 8.5 0 0 1-3.8-.9L3 21l1.9-5.2A8.4 8.4 0 0 1 12 3a8.4 8.4 0 0 1 9 8.5z"/></svg>'
    svg_star = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linejoin="round"><polygon points="12 2 15 9 22 9.3 16.5 13.8 18.4 21 12 17 5.6 21 7.5 13.8 2 9.3 9 9"/></svg>'
    svg_flag = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round"><path d="M4 22V4h13l-2 4 2 4H4"/></svg>'

    cards_html = (card(svg_chat, "linear-gradient(135deg,#5A8DFF,#2F6FED)", len(reviews), "Диалогов разобрано", "#EAF1FF")
                  + card(svg_star, "linear-gradient(135deg,#37D68A,#12B76A)", f"{avg}/100", "Средний балл", "#E7F8EF")
                  + card(svg_flag, "linear-gradient(135deg,#98A2B3,#667085)", len(stages), "Стадий в воронке", "#F0F1F3"))

    rv_html = ""
    for rv in reviews:
        def ul(items, color):
            if not items: return ""
            if isinstance(items, str): items = [items]
            return f'<div class="rv-block" style="--c:{color}"><ul>' + "".join(f"<li>{x}</li>" for x in items) + "</ul></div>"
        blocks = ""
        if rv.get("strong_moments"): blocks += '<div class="rv-h" style="color:#027A48">✓ Что хорошо</div>' + ul(rv["strong_moments"], "#027A48")
        if rv.get("growth_points"): blocks += '<div class="rv-h" style="color:#1570EF">↑ Точки роста</div>' + ul(rv["growth_points"], "#1570EF")
        if rv.get("speech_tips"): blocks += '<div class="rv-h" style="color:#2F6FED">✍ По стилю переписки</div>' + ul(rv["speech_tips"], "#2F6FED")
        sw = rv.get("stop_words") or []
        if sw:
            parts = []
            for w in sw:
                if isinstance(w, dict):
                    parts.append(f'<b>«{w.get("phrase","")}»</b> — {w.get("why","")}. <span style="color:#027A48">Лучше: {w.get("better","")}</span>')
                else:
                    parts.append(f"«{w}»")
            blocks += '<div class="rv-block" style="--c:#B54708"><div class="rv-h" style="color:#B54708">⚠ Формулировки, которых лучше избегать</div><ul>' + "".join(f"<li>{x}</li>" for x in parts) + "</ul></div>"
        if rv.get("recommendation"):
            blocks += f'<div class="rv-rec">💡 <b>Главный совет:</b> {rv["recommendation"]}</div>'
        rv_html += f'''<div class="rv-card">
          <div class="rv-head"><b>{rv.get("client_name") or "Клиент"}</b> · {rv.get("_title","")[:60]} · стадия: {rv.get("_stage","")} · <span class="rv-score">{rv.get("score","-")}/100</span></div>
          <div class="rv-out">{rv.get("outcome","")}</div>
          {blocks}
        </div>'''

    recs_html = "".join(f'<div class="rec-item">{r}</div>' for r in recs.get("overall", []))
    cost_html = ""

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
    @page {{ size: A4; margin: 1.2cm; }}
    * {{ font-family: 'DejaVu Sans', sans-serif; box-sizing: border-box; }}
    body {{ color:#1D2939; font-size:12px; }}
    .hero {{ background:linear-gradient(135deg,#2F6FED,#1E4FD8); border-radius:20px; padding:28px 32px; color:#fff; margin-bottom:20px; }}
    .hero h1 {{ margin:0 0 6px; font-size:24px; font-weight:800; }}
    .hero p {{ margin:0; font-size:13px; opacity:.9; }}
    .cards {{ display:flex; gap:14px; margin-bottom:22px; }}
    .card {{ flex:1; background:#fff; border:1px solid #E3E7F0; border-radius:16px; padding:22px 16px; text-align:center; position:relative; overflow:hidden; box-shadow:0 4px 10px rgba(16,24,40,.06); }}
    .blob {{ position:absolute; top:-25px; right:-25px; width:80px; height:80px; border-radius:50%; opacity:.35; }}
    .ic {{ width:48px; height:48px; border-radius:50%; margin:0 auto 12px; display:flex; align-items:center; justify-content:center; box-shadow:0 8px 18px rgba(16,24,40,.18); position:relative; z-index:1; }}
    .num {{ font-size:22px; font-weight:800; color:#1D2939; }}
    .lbl {{ font-size:11px; color:#667085; margin-top:2px; }}
    .cost {{ background:#F8FAFF; border:1px solid #D0DEFF; border-radius:12px; padding:12px 16px; font-size:12px; color:#344054; margin-bottom:18px; }}
    .sec-h {{ font-size:16px; font-weight:700; color:#1D2939; margin:18px 0 12px; }}
    .rv-card {{ background:#fff; border:1px solid #E3E7F0; border-radius:14px; padding:16px 18px; margin-bottom:12px; box-shadow:0 1px 3px rgba(16,24,40,.05); }}
    .rv-head {{ font-size:13px; color:#344054; margin-bottom:4px; }}
    .rv-out {{ font-size:11px; color:#667085; margin-bottom:8px; font-style:italic; }}
    .rv-score {{ color:#2F6FED; font-weight:800; }}
    .rv-h {{ font-size:12px; font-weight:700; margin:8px 0 3px; }}
    .rv-block {{ border-left:3px solid var(--c); padding-left:10px; }}
    .rv-block ul {{ margin:0 0 4px; padding-left:18px; }}
    .rv-block li {{ font-size:11px; color:#475467; margin:2px 0; }}
    .rv-rec {{ background:#EEF4FF; border-radius:10px; padding:8px 12px; margin-top:8px; font-size:11px; color:#344054; }}
    .rec-item {{ background:#EEF4FF; border-left:4px solid #2F6FED; border-radius:8px; padding:10px 14px; margin-bottom:8px; font-size:12px; color:#344054; }}
    .foot {{ text-align:center; color:#98A2B3; font-size:10px; margin-top:20px; }}
    </style></head><body>
    <div class="hero"><h1>💬 Отчёт по перепискам</h1><p>ИИ Руководитель отдела продаж · последние {days} дней</p></div>
    <div class="cards">{cards_html}</div>
    {cost_html}
    <div class="sec-h">Разбор диалогов</div>
    {rv_html}
    {'<div class="sec-h">Рекомендации по отделу</div>' + recs_html if recs_html else ''}
    <div class="foot">Сформировано в БОРИС · boris-ai.pro · {datetime.datetime.now().strftime("%d.%m.%Y")}</div>
    </body></html>"""

    if mask_phones:
        html = _mask_phones_html(html)
    out = io.BytesIO()
    _WHTML(string=html).write_pdf(out)
    out.seek(0)
    consume_rop_report(account_id, "chats")
    try:
        _tg_notify(account_id, f"\U0001F4AC Готов отчёт по перепискам\n\nДиалогов разобрано: {len(reviews)} \u00b7 средний балл: {avg}/100\n\nСкачать можно в кабинете: Продажи \u2192 РОП \u2192 Отчёты", topic="sales")
    except Exception:
        pass
    try:
        _pdf = out.getvalue()
        _dir = _os.path.join(_os.path.dirname(__file__), "..", "..", "reports", account_id)
        _os.makedirs(_dir, exist_ok=True)
        _u = _db_url()
        if _u:
            _cc = psycopg2.connect(_u); _cur = _cc.cursor()
            _today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
            _cur.execute("""INSERT INTO call_reports (account_id, date_from, date_to, calls_total, calls_analyzed, file_path, size_bytes, kind)
                VALUES (%s,%s,%s,%s,%s,%s,%s,'chats') RETURNING id""",
                (account_id, _today, _today, len(chats), len(reviews), "", len(_pdf)))
            _rid = _cur.fetchone()[0]
            _fp = _os.path.join(_dir, f"chats_{_rid}.pdf")
            open(_fp, "wb").write(_pdf)
            _cur.execute("UPDATE call_reports SET file_path=%s WHERE id=%s", (_fp, _rid))
            _cc.commit(); _cc.close()
        out.seek(0)
    except Exception:
        out.seek(0)
    return StreamingResponse(out, media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="otchet_perepiski.pdf"'})



# Привязка услуга → тема (message_thread_id) по аккаунту.
# Пока задано для аккаунта Кирилла (группа -1003952038222). Для клиентов расширяется по тому же принципу.
_TG_TOPICS = {
    "otdushi": {  # ключ = account_id владельца (Кирилл)
        "chat_id": -1003952038222,
        "topics": {"posting": 2, "support": 3, "tech": 4, "avito": 5, "ai_manager": 6, "sales": 14},
    },
}

def _tg_notify(account_id: str, text: str, topic: str = None):
    """Уведомление владельцу аккаунта в Telegram. topic — ключ услуги (posting/support/tech/avito/ai_manager) → пишет в свою тему группы. Тихо молчит, если чат не привязан."""
    try:
        import psycopg2
        from app.telegram_bot import send_telegram_message
        cfg = _TG_TOPICS.get(account_id)
        if cfg:
            thr = cfg["topics"].get(topic) if topic else None
            send_telegram_message(str(cfg["chat_id"]), text, thread_id=thr)
            return
        u = _db_url()
        if not u: return
        c = psycopg2.connect(u); cur = c.cursor()
        cur.execute("SELECT telegram_chat_id FROM accounts WHERE account_id=%s", (account_id,))
        row = cur.fetchone(); c.close()
        if row and row[0]:
            send_telegram_message(str(row[0]), text)
    except Exception:
        pass



@router.get("/setup_check")
def setup_check(account_id: str):
    """Что мешает работать: API-ключи Avito, тариф, коллтрекинг. Ничего не тратит."""
    import psycopg2
    steps = []
    has_keys = False
    try:
        u = _db_url()
        c = psycopg2.connect(u); cur = c.cursor()
        cur.execute("SELECT avito_client_id, avito_client_secret FROM accounts WHERE account_id=%s", (account_id,))
        row = cur.fetchone(); c.close()
        has_keys = bool(row and row[0] and row[1])
    except Exception:
        pass
    steps.append({
        "key": "api",
        "ok": has_keys,
        "title": "Добавьте мне API-ключи Avito",
        "hint": "Без ключей я не вижу ни звонков, ни переписок. Возьмите Client ID и Client Secret в кабинете Avito (Настройки → Профиль → API) и внесите их в «Мои аккаунты».",
    })

    token_ok, ct_ok, ct_msg = False, False, ""
    if has_keys:
        token, err = _ct_token(account_id)
        token_ok = not err
        ct_msg = err or ""
        if token_ok:
            try:
                import datetime
                now = datetime.datetime.utcnow()
                code, data = _get_calls(token, (now - datetime.timedelta(days=7)).strftime("%Y-%m-%dT00:00:00Z"),
                                        now.strftime("%Y-%m-%dT%H:%M:%SZ"))
                ct_ok = code == 200
                if not ct_ok:
                    ct_msg = f"CallTracking API вернул {code}"
            except Exception as e:
                ct_msg = str(e)[:120]
    steps.append({
        "key": "token",
        "ok": token_ok,
        "title": "Ключи рабочие — я подключился",
        "hint": ct_msg or "Проверьте, что ключи внесены полностью, без лишних пробелов.",
    })
    steps.append({
        "key": "calltracking",
        "ok": ct_ok,
        "title": "Тариф Расширенный или Максимальный + Коллтрекинг",
        "hint": "Слушать звонки я могу только при тарифе Расширенный или Максимальный с включённым Коллтрекингом. Включите его в кабинете Avito → Профиль → Звонки — и я начну разбирать разговоры.",
    })

    chats = 0
    try:
        u = _db_url()
        c = psycopg2.connect(u); cur = c.cursor()
        cur.execute("SELECT COUNT(*) FROM messenger_leads WHERE account_id=%s", (account_id,))
        chats = int(cur.fetchone()[0] or 0); c.close()
    except Exception:
        pass
    steps.append({
        "key": "chats",
        "ok": chats > 0,
        "title": "Переписки подтянуты",
        "hint": "Нажмите «Подтянуть переписки» — я заберу диалоги из Avito и разберу их так же, как звонки.",
    })

    ready = all(s["ok"] for s in steps[:3])
    return {"status": "ok", "ready": ready, "steps": steps, "chats": chats}



@router.get("/all_limits")
def all_limits(account_id: str):
    """Остатки по всем пакетам: РОП (минуты + отчёты), МОП (сообщения), соцсети (посты)."""
    import json as _j, datetime as _dt
    from app.db.session import SessionLocal
    from app.models.storage import Storage

    unlimited = _is_own_account(account_id)

    # МОП — сообщения
    try:
        from app.api.messenger import get_manager_balance
        mop = get_manager_balance(account_id)
    except Exception:
        mop = {"purchased": 0, "used": 0, "left": 0, "active": False}

    # соцсети — подписка и посты на сегодня
    social = {"projects": 0, "active": 0, "posts_today": 0, "per_day": 0, "left_today": 0}
    try:
        db = SessionLocal()
        try:
            row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "posting_projects").first()
            projects = _j.loads(row.value) if row else []
            today = _dt.date.today().isoformat()
            act = 0; per_day = 0; used = 0
            for pr in projects:
                sub = pr.get("subscription") or {}
                if sub:
                    try:
                        paid = _dt.date.fromisoformat(sub.get("paid_at", ""))
                        if (_dt.date.today() - paid).days < int(sub.get("period_days", 30)):
                            act += 1
                    except Exception:
                        pass
                    per_day += int(sub.get("posts_per_day", 3) or 3)
                c = db.query(Storage).filter(Storage.account_id == account_id,
                                             Storage.key == "posting_count:" + str(pr.get("id", "")) + ":" + today).first()
                if c:
                    try: used += int(c.value)
                    except Exception: pass
            social = {"projects": len(projects), "active": act, "posts_today": used,
                      "per_day": per_day, "left_today": max(0, per_day - used)}
        finally:
            db.close()
    except Exception:
        pass

    return {
        "status": "ok",
        "unlimited": unlimited,
        "rop": {
            "minutes": get_rop_minutes(account_id),
            "reports_calls": get_rop_reports(account_id, "calls"),
            "reports_chats": get_rop_reports(account_id, "chats"),
        "chats": get_rop_chats(account_id),
        "period": _rop_period(account_id),
        },
        "mop": mop,
        "social": social,
    }



_TRAIN_MARK = "=== Опыт от ИИ Руководителя отдела продаж ==="


@router.post("/train_manager")
def train_manager(account_id: str, body: dict = Body(default=None)):
    """РОП обучает МОПа: собирает выводы из готовых разборов и дописывает правила в скрипт менеджера.
    apply=false — только показать текст, apply=true — записать в настройки менеджера."""
    import json as _j, os, requests, psycopg2
    from proxy_pool import get_intl_requests_proxies

    body = body or {}
    apply = bool(body.get("apply"))

    u = _db_url()
    if not u:
        return {"status": "error", "message": "нет доступа к базе"}
    c = psycopg2.connect(u); cur = c.cursor()
    cur.execute("SELECT analysis FROM call_analysis WHERE account_id=%s ORDER BY created_at DESC LIMIT 40", (account_id,))
    calls = [r[0] if isinstance(r[0], dict) else _j.loads(r[0]) for r in cur.fetchall()]
    cur.execute("SELECT analysis FROM chat_analysis WHERE account_id=%s ORDER BY created_at DESC LIMIT 40", (account_id,))
    chats = [r[0] if isinstance(r[0], dict) else _j.loads(r[0]) for r in cur.fetchall()]
    c.close()

    if not calls and not chats:
        return {"status": "empty", "message": "Пока нечего передать: сначала разберите звонки или переписки — РОП учит менеджера на их основе."}

    def collect(items, key):
        out = []
        for it in items:
            v = it.get(key)
            if isinstance(v, list):
                for x in v:
                    if isinstance(x, dict):
                        out.append(f'«{x.get("phrase","")}» — {x.get("why","")} (лучше: {x.get("better","")})')
                    elif x:
                        out.append(str(x))
            elif v:
                out.append(str(v))
        return out[:40]

    material = {
        "точки роста": collect(calls + chats, "growth_points"),
        "неудачные формулировки": collect(calls + chats, "stop_words"),
        "что работает хорошо": collect(calls + chats, "strong_moments"),
        "советы по речи": collect(calls + chats, "speech_tips"),
        "главные советы": collect(calls + chats, "recommendation"),
    }
    scores = [x.get("score") for x in (calls + chats) if isinstance(x.get("score"), int)]
    avg = round(sum(scores) / len(scores)) if scores else None

    prompt = (
        "Ты — руководитель отдела продаж. Ниже выводы из разбора реальных звонков и переписок менеджеров. "
        "Преврати их в КОРОТКУЮ инструкцию для ИИ-менеджера, который общается с покупателями в чатах Avito.\n\n"
        "Правила: пиши по-русски, конкретными указаниями в повелительном наклонении, без вступлений и пояснений. "
        "8-14 пунктов. Отдельно перечисли фразы, которые запрещено писать, и чем их заменить. "
        "Не выдумывай ничего, чего нет в материале.\n\n"
        + _account_context(account_id) +
        "МАТЕРИАЛ РАЗБОРА:\n" + _j.dumps(material, ensure_ascii=False)[:6000]
    )
    try:
        api_key = os.environ.get("OPENAI_API_KEY")
        r = requests.post("https://api.openai.com/v1/chat/completions",
            headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
            json={"model": "gpt-5.4", "messages": [{"role": "user", "content": prompt}], "max_completion_tokens": 1200},
            proxies=get_intl_requests_proxies(), timeout=120)
        if r.status_code != 200:
            return {"status": "error", "message": f"gpt {r.status_code}"}
        data = r.json()
        try:
            from app.usage import log_usage as _lc
            us = data.get("usage", {})
            _lc(account_id, "openai", "gpt-5.4", "обучение менеджера от РОП",
                us.get("prompt_tokens", 0), us.get("completion_tokens", 0))
        except Exception:
            pass
        rules = data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return {"status": "error", "message": str(e)[:150]}

    block = _TRAIN_MARK + "\n" + rules

    if not apply:
        return {"status": "ok", "applied": False, "rules": rules,
                "based_on": {"calls": len(calls), "chats": len(chats), "avg_score": avg}}

    # записываем в активный скрипт менеджера, не затирая то, что там уже есть
    try:
        from app.db.session import SessionLocal
        from app.models.messenger_prompt import MessengerPrompt
        db = SessionLocal()
        try:
            active = db.query(MessengerPrompt).filter(MessengerPrompt.account_id == account_id,
                                                      MessengerPrompt.is_active == True).first()
            if active:
                cur_text = active.custom_instructions or ""
                if _TRAIN_MARK in cur_text:
                    head = cur_text.split(_TRAIN_MARK)[0].rstrip()
                    active.custom_instructions = (head + "\n\n" + block).strip()
                else:
                    active.custom_instructions = (cur_text.rstrip() + "\n\n" + block).strip()
            else:
                db.add(MessengerPrompt(account_id=account_id, label="Скрипт от Бориса",
                                       item_ids="", custom_instructions=block, is_active=True))
            db.commit()
        finally:
            db.close()
    except Exception as e:
        return {"status": "error", "message": "не удалось записать в настройки менеджера: " + str(e)[:120]}

    try:
        _tg_notify(account_id, "🧠 РОП обучил ИИ-менеджера\n\nВ скрипт добавлены правила по итогам разбора "
                   f"({len(calls)} звонков, {len(chats)} переписок).", topic="sales")
    except Exception:
        pass
    return {"status": "ok", "applied": True, "rules": rules,
            "based_on": {"calls": len(calls), "chats": len(chats), "avg_score": avg}}


@router.get("/analyzed")
def analyzed_calls(account_id: str, days: int = 7, limit: int = 50):
    """Разобранные звонки с чек-листом — чтобы руководитель видел работу
    менеджеров в кабинете, а не только внутри ответа модели."""
    import json as _j
    import psycopg2
    u = _db_url()
    if not u:
        return {"status": "error", "message": "Нет доступа к базе"}
    c = psycopg2.connect(u)
    cur = c.cursor()
    try:
        cur.execute(
            "SELECT call_id, created_at, minutes, analysis FROM call_analysis"
            " WHERE account_id=%s AND created_at > now() - (%s || ' days')::interval"
            " ORDER BY created_at DESC LIMIT %s",
            (account_id, str(int(days)), int(limit)))
        rows = cur.fetchall()
    finally:
        c.close()

    out = []
    for call_id, created, minutes, analysis in rows:
        a = analysis if isinstance(analysis, dict) else (_j.loads(analysis or "{}") or {})
        checklist = a.get("checklist") or []
        done = sum(1 for x in checklist if x.get("done"))
        out.append({
            "call_id": call_id,
            "когда": created.isoformat() if created else None,
            "минут": round(float(minutes or 0), 1),
            "клиент": a.get("client_name") or "",
            "роль": a.get("role") or "",
            "балл": a.get("score"),
            "чеклист": checklist,
            "выполнено": done,
            "всего_пунктов": len(checklist),
            "совет": a.get("recommendation") or "",
            "сильные": a.get("strong_moments") or [],
            "зоны_роста": a.get("growth_points") or [],
        })
    return {"status": "ok", "звонки": out, "всего": len(out)}
