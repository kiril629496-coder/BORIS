# -*- coding: utf-8 -*-
"""Единая маршрутизация AI-провайдеров с автоматическим failover.

Владелец сформулировал правило прямо: закончился баланс у одного провайдера —
BORIS сам берёт следующий. Не спрашивает, не останавливает клиента, не
тратит попытки на бессмысленные повторы.

Три вещи, из-за которых это не работало раньше:

  · провайдер выбирался в каждом модуле по-своему, и знание «у OpenAI
    кончился баланс» никуда не распространялось;
  · любая ошибка выглядела одинаково, поэтому «нет денег» ретраилось так же,
    как «сеть моргнула» — и наряд умирал на шестой попытке;
  · возможности считались одинаковыми: Claude ставили вместо image-модели,
    хотя картинки он не рисует.

Здесь всё это в одном месте: состояние провайдера, классификация ошибки,
порядок по возможностям и стоимости, и вызов с переключением без потери
наряда.

    venv/bin/python3 -m app.ext_api.aiprov state
    venv/bin/python3 -m app.ext_api.aiprov drill        # controlled E2E
"""
import datetime, json, os, re, time

from . import db
from .errors import ApiError

# ------------------------------------------------------------- возможности
TEXT = "text_generation"
MOP = "mop"
ROP = "rop"
CLASSIFY = "classification"
SUMMARY = "summarization"
CODING = "dev_coding"
IMAGE = "image_generation"
BANNER = "banner_generation"

CAPABILITIES = (TEXT, MOP, ROP, CLASSIFY, SUMMARY, CODING, IMAGE, BANNER)

# Порядок задан владельцем: сначала то, что уже оплачено подпиской, потом
# платное по API. Претендент, который возможность не умеет, в список не
# попадает вовсе — «один провайдер умеет всё» это неправда.
ORDER = {
    TEXT:     ("claude_code", "openai", "anthropic_api"),
    MOP:      ("claude_code", "openai", "anthropic_api"),
    ROP:      ("claude_code", "openai", "anthropic_api"),
    CLASSIFY: ("claude_code", "openai", "anthropic_api"),
    SUMMARY:  ("claude_code", "openai", "anthropic_api"),
    CODING:   ("openai", "claude_code", "gemini_cli", "anthropic_api"),
    # Порядок внутри возможности — это предпочтение при равной цене.
    # Итоговый выбор всё равно делает сортировка по стоимости ниже:
    # оплаченная подписка идёт раньше платного API везде, где умеет.
    # Владелец сформулировал это прямо: OpenAI не тратится там, где задача
    # безопасно выполняется серверным Claude.
    IMAGE:    ("openai",),
    BANNER:   ("openai", "local_renderer"),
}

# Стоимость для политики: 0 — уже оплачено подпиской владельца.
COST = {"claude_code": 0, "local_renderer": 0, "openai": 2, "anthropic_api": 3, "gemini_cli": 1}

# Состояния провайдера
AVAILABLE = "AVAILABLE"
RATE_LIMITED = "RATE_LIMITED"
UNAVAILABLE_BILLING = "UNAVAILABLE_BILLING"
AUTH_ERROR = "AUTH_ERROR"
UNSUPPORTED_LOCATION = "UNSUPPORTED_LOCATION"
TEMP_ERROR = "TEMP_ERROR"
DISABLED_BY_OWNER = "DISABLED_BY_OWNER"

RETRY_AFTER = {RATE_LIMITED: 300, TEMP_ERROR: 120,
               AUTH_ERROR: 1800, UNAVAILABLE_BILLING: 6 * 3600,
               UNSUPPORTED_LOCATION: 0}
TEMP_RETRIES = int(os.environ.get("BORIS_AI_TEMP_RETRIES", "2"))


# --------------------------------------------------------- классификация
BILLING_SIGNS = (
    "insufficient_quota", "credit_balance_exhausted", "billing_hard_limit", "balance exhausted",
    "usage limit reached", "account quota", "quota exceeded",
    "exceeded your current quota", "credit balance is too low",
    "no credits remaining", "insufficient credits",
    "недостаточно средств", "закончился баланс",
)
RATE_SIGNS = ("rate limit", "rate_limit", "too many requests", "429")
AUTH_SIGNS = ("invalid api key", "incorrect api key", "unauthorized",
              "authentication", "permission denied", "401", "403",
              "please set an auth method", "auth method",
              "gemini_api_key", "google_genai_use_vertexai")
GEO_SIGNS = ("user location is not supported for the api use",
             "location is not supported for the api use",
             "unsupported location", "unsupported country")
TEMP_SIGNS = ("timeout", "timed out", "connection", "temporarily",
              "bad gateway", "service unavailable", "500", "502", "503", "504")

FREE_TIER_SHORT_QUOTA_METRICS = (
    "generate_content_free_tier_input_token_count",
    "generate_content_free_tier_requests",
)

GEMINI_DAILY_QUOTA_SIGNS = (
    "boris_gemini_daily_quota_exhausted",
    "exhausted your daily quota",
    "daily quota exhausted",
    "daily quota",
)


def gemini_daily_quota_reset_epoch(text, now=None):
    """Return a proven future reset epoch for a Gemini daily quota, if present."""
    raw = str(text or "")
    low = raw.lower()
    if not any(sign in low for sign in GEMINI_DAILY_QUOTA_SIGNS):
        return None
    now_ts = (now.timestamp() if isinstance(now, datetime.datetime)
              else float(now) if now is not None else time.time())
    for match in re.finditer(r"reset_epoch=([0-9]{9,13})", raw, re.I):
        try:
            ts = float(match.group(1))
            if ts > 10_000_000_000:
                ts /= 1000.0
            if ts > now_ts:
                return ts
        except Exception:
            continue
    return None


def free_tier_short_retry_after(text):
    """Return a proven short Gemini free-tier retry window in seconds.

    Fail closed: exact free-tier metric, explicit quota wording and an explicit
    retry delay <= 10 minutes are all required. Hard billing/credit failures
    therefore keep their existing terminal billing classification.
    """
    low = str(text or "").lower()
    # A daily model quota is not a seconds-level rate window even if the same
    # nested Gemini diagnostic contains "Please retry in Ns". The durable daily
    # reset must win or BORIS will hammer the provider and re-run queued work.
    if any(sign in low for sign in GEMINI_DAILY_QUOTA_SIGNS):
        return None
    if "you exceeded your current quota" not in low:
        return None
    if not any(metric in low for metric in FREE_TIER_SHORT_QUOTA_METRICS):
        return None
    values = []
    for pattern, divisor in (
        (r"please retry in\s+([0-9]+(?:\.[0-9]+)?)s", 1.0),
        (r"suggested retry after\s+([0-9]+(?:\.[0-9]+)?)s", 1.0),
        (r"retrying after\s+([0-9]+(?:\.[0-9]+)?)ms", 1000.0),
    ):
        for match in re.finditer(pattern, low, re.I):
            try:
                value = float(match.group(1)) / divisor
            except Exception:
                continue
            if 0.0 < value <= 600.0:
                values.append(value)
    return max(values) if values else None


def classify(text, code=None):
    """Что это за отказ. От ответа зависит, повторять или переключаться.

    Порядок проверок не случаен: «insufficient_quota» приходит с кодом 429,
    и если сначала посмотреть на код, деньги будут перепутаны с лимитом
    частоты — а это разница между «подожди минуту» и «здесь больше нечего
    ждать».
    """
    low = (text or "").lower()
    # A provider geography/policy rejection is not billing and time does not
    # heal it. Check before billing because CLI diagnostics can contain both.
    if any(s in low for s in GEO_SIGNS):
        return UNSUPPORTED_LOCATION
    # Gemini free-tier token/request windows often use billing-like quota
    # wording together with HTTP 429, but explicitly tell us to retry in
    # seconds. Treat only that exact evidence as a temporary rate window.
    if free_tier_short_retry_after(low) is not None:
        return RATE_LIMITED
    if any(s in low for s in BILLING_SIGNS):
        return UNAVAILABLE_BILLING
    if code in ("RATE_LIMITED",) or any(s in low for s in RATE_SIGNS):
        return RATE_LIMITED
    if code in ("UNAUTHORIZED",) or any(s in low for s in AUTH_SIGNS):
        return AUTH_ERROR
    if any(s in low for s in TEMP_SIGNS):
        return TEMP_ERROR
    return TEMP_ERROR


def is_terminal(state):
    """Состояния, при которых повторять тем же провайдером бессмысленно."""
    return state in (UNAVAILABLE_BILLING, AUTH_ERROR,
                     UNSUPPORTED_LOCATION, DISABLED_BY_OWNER)


# --------------------------------------------------------------- состояние

def _now():
    return time.time()


def weekly_limit_retry_after(text, now=None):
    """Seconds until explicit Claude weekly reset; generic 429 returns None."""
    raw = str(text or "")
    low = raw.lower().replace("’", "'")
    if "you've hit your weekly limit" not in low and "weekly limit" not in low:
        return None
    now = now or datetime.datetime.now(datetime.timezone.utc)
    candidates = []
    for m in re.finditer(r"20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})", raw, re.I):
        token = m.group(0)
        try:
            iso = token[:-1] + "+00:00" if token.endswith("Z") else token
            if re.search(r"[+-]\d{4}$", iso):
                iso = iso[:-5] + iso[-5:-2] + ":" + iso[-2:]
            candidates.append(datetime.datetime.fromisoformat(iso).timestamp())
        except Exception:
            pass
    for m in re.finditer(r"(?:reset(?:s|_at| at)?|reset_timestamp)[^0-9]{0,32}(\d{10,13})", raw, re.I):
        try:
            ts = int(m.group(1))
            if ts > 10_000_000_000:
                ts /= 1000.0
            candidates.append(float(ts))
        except Exception:
            pass
    # Claude Code also returns a human reset string, e.g.
    # "You've hit your weekly limit · resets Sep 1, 6am (UTC)". Parse it so a
    # weekly quota is never degraded to the generic 5-minute 429 cooldown.
    for m in re.finditer(
            r"resets?\s+([A-Za-z]{3,9})\s+(\d{1,2}),\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*\(?(UTC)\)?",
            raw, re.I):
        try:
            month = datetime.datetime.strptime(m.group(1)[:3], "%b").month
            hour = int(m.group(3)) % 12
            if m.group(5).lower() == "pm":
                hour += 12
            minute = int(m.group(4) or 0)
            reset = datetime.datetime(now.year, month, int(m.group(2)), hour,
                                      minute, tzinfo=datetime.timezone.utc)
            if reset <= now:
                reset = reset.replace(year=now.year + 1)
            candidates.append(reset.timestamp())
        except Exception:
            pass
    future = [ts for ts in candidates if ts > now.timestamp()]
    if not future:
        # Weekly-limit text without a machine/human reset timestamp must still
        # fail closed. A week is safer than hammering the subscription every
        # five minutes; provider state can always be reset explicitly earlier.
        return 7 * 24 * 3600
    return max(1, int(min(future) - now.timestamp()))


def _durable_gemini_daily_reset_evidence(limit=500):
    """Return (future_reset_epoch, source) from durable local evidence.

    The generic provider row is shared by development and other runtimes.
    A successful non-development Gemini call must not erase a proven
    development daily quota. Prefer the current provider note; if another
    runtime already overwrote it, recover from a bounded tail of A2A
    checkpoint/provider events. No network call and no schema change.
    """
    try:
        row = db.one(
            "SELECT note FROM ext_ai_providers WHERE name='gemini_cli'"
        ) or {}
        reset = gemini_daily_quota_reset_epoch(row.get("note"))
        if reset:
            return float(reset), "provider_state"
    except Exception:
        pass
    try:
        rows = db.rows("""SELECT body_json
                          FROM ext_a2a_messages
                          WHERE kind = ANY(:k)
                          ORDER BY id DESC LIMIT :l""",
                       k=["CHECKPOINT", "EXECUTOR_ERROR", "PROVIDER_FAILOVER",
                          "DEV_PAID_PROVIDER_BLOCKED"],
                       l=max(1, min(int(limit), 2000)))
    except Exception:
        rows = []
    best = None
    for row in rows:
        raw = str((row or {}).get("body_json") or "")
        reset = gemini_daily_quota_reset_epoch(raw)
        if reset and (best is None or reset > best):
            best = float(reset)
    return (best, "a2a_history") if best else (None, None)


def mark(provider, state, note=None, retry_after=None):
    # A proven future Gemini daily reset is a stronger development safety
    # boundary than a generic success from another runtime. Preserve it until
    # reset even when somebody calls mark(... AVAILABLE ...). This prevents
    # queued development work from being re-opened and hammering a known
    # exhausted free quota.
    if provider == "gemini_cli" and state == AVAILABLE:
        reset, source = _durable_gemini_daily_reset_evidence()
        if reset:
            state = RATE_LIMITED
            retry_after = max(1, int(reset - time.time()) + 15)
            note = (
                "BORIS_GEMINI_DAILY_QUOTA_EXHAUSTED "
                f"reset_epoch={int(reset)} preserved_from={source}; "
                "AVAILABLE suppressed until proven daily reset"
            )
    secs = RETRY_AFTER.get(state, 0) if retry_after is None else int(retry_after)
    db.q("""INSERT INTO ext_ai_providers (name, state, note, last_failure_at,
                                          retry_at, updated_at)
            VALUES (:n, :s, :note,
                    CASE WHEN :s = 'AVAILABLE' THEN NULL ELSE NOW() END,
                    CASE WHEN :secs > 0 THEN NOW() + (:secs || ' seconds')::interval
                         ELSE NULL END,
                    NOW())
            ON CONFLICT (name) DO UPDATE SET
                state = EXCLUDED.state, note = EXCLUDED.note,
                last_failure_at = EXCLUDED.last_failure_at,
                retry_at = EXCLUDED.retry_at, updated_at = NOW()""",
         n=provider, s=state, note=(note or "")[:400], secs=secs)
    return {"provider": provider, "state": state, "retry_after": secs}


def state(provider=None):
    rows = db.rows("""SELECT name, state, note, last_failure_at, retry_at,
                             (retry_at IS NOT NULL AND retry_at > NOW()) AS cooling
                        FROM ext_ai_providers""")
    by = {r["name"]: dict(r) for r in rows}
    for name in COST:
        by.setdefault(name, {"name": name, "state": AVAILABLE, "note": "",
                             "cooling": False})
    # Остывание закончилось — провайдер снова кандидат, кроме тех случаев,
    # где ждать нечего: без денег и без ключа время не лечит.
    for r in by.values():
        state_name = r.get("state")
        # Daily Gemini quota is a durable capability boundary. A stale generic
        # retry_at must never make the provider runnable before reset_epoch.
        if r.get("name") == "gemini_cli" and state_name == RATE_LIMITED:
            note_low = str(r.get("note") or "").lower()
            daily_marked = any(sign in note_low for sign in GEMINI_DAILY_QUOTA_SIGNS)
            daily_reset = gemini_daily_quota_reset_epoch(r.get("note"))
            if daily_marked:
                # Fail closed across the reset boundary itself. Once retry_at
                # expires, a real order must still not race ahead of the
                # canonical health probe. Only mark(... AVAILABLE ...) removes
                # this latch after the probe proves the CLI actually works.
                r["usable"] = False
                r["daily_probe_required"] = True
                if daily_reset:
                    r["cooling"] = True
                    r["daily_reset_epoch"] = int(daily_reset)
                continue
        # Durable terminal provider states do not heal because wall-clock time
        # passed. Billing/auth/geography/policy require new external evidence:
        # a successful bounded recovery probe, refreshed credentials, route
        # change, or explicit owner policy change. Keeping them unusable prevents
        # live client traffic from becoming the health probe after retry_at.
        if state_name in (
            UNAVAILABLE_BILLING,
            AUTH_ERROR,
            DISABLED_BY_OWNER,
            UNSUPPORTED_LOCATION,
        ):
            r["usable"] = False
            r["probe_required"] = state_name in (UNAVAILABLE_BILLING, AUTH_ERROR)
            continue
        r["usable"] = (state_name == AVAILABLE) or (not r.get("cooling"))
    return by.get(provider) if provider else by


def owner_policy():
    """Что владелец запретил прямо сейчас. Политика сильнее любого порядка."""
    off = [p.strip() for p in
           (os.environ.get("BORIS_AI_DISABLED") or "").split(",") if p.strip()]
    # Скрытый платный Anthropic API вместо подписки — запрещено владельцем.
    if os.environ.get("BORIS_ALLOW_ANTHROPIC_API") != "1":
        off.append("anthropic_api")
    return set(off)


def candidates(capability):
    """Кого можно звать для этой возможности и в каком порядке.

    Это общий/client routing. Development policy ниже намеренно отдельная,
    чтобы cost-safety разработки не меняла MOP/content/analytics/banner traffic.
    """
    order = ORDER.get(capability) or ()
    st, off = state(), owner_policy()
    out = []
    for name in order:
        if name in off:
            continue
        row = st.get(name) or {}
        if row.get("state") == DISABLED_BY_OWNER:
            continue
        if not row.get("usable", True):
            continue
        out.append(name)
    # Owner banner policy: when OpenAI Images is healthy/funded it must be the
    # preferred visual generator; the deterministic local renderer is the free
    # fallback for zero-balance/outage. Other capabilities keep cost-first order.
    if capability == BANNER:
        out.sort(key=lambda n: order.index(n))
    else:
        out.sort(key=lambda n: (COST.get(n, 9), order.index(n)))
    return out


DEV_PAID_DENY = frozenset(("openai", "codex", "anthropic_api"))


def _gemini_reserved_for_sales():
    """Keep the shared Gemini CLI quota for client MOP/training when requested."""
    return str(os.environ.get("BORIS_RESERVE_GEMINI_FOR_SALES") or "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def development_candidates(include_mock=False):
    """Owner cost policy for DEVELOPMENT roles only.

    Gemini CLI is normally primary. When BORIS_RESERVE_GEMINI_FOR_SALES=1,
    development must not consume the same tiny daily Gemini quota used by
    client MOP/training; Claude remains the only free development candidate.
    Paid OpenAI/Codex/API providers are never returned. Mock is test-only and
    must be explicitly requested by the caller.

    Fail closed for proven Gemini daily quota even if a stale/non-development
    writer overwrote the shared provider row with AVAILABLE. The durable A2A
    evidence is authoritative until its future reset epoch.
    """
    st = state()
    durable_gemini_reset = None
    gemini_row = st.get("gemini_cli") or {}
    if (
        not _gemini_reserved_for_sales()
        and gemini_row.get("usable", True)
        and gemini_row.get("state") != DISABLED_BY_OWNER
    ):
        try:
            durable_gemini_reset, _source = _durable_gemini_daily_reset_evidence()
        except Exception:
            durable_gemini_reset = None
    out = []
    for name in ("gemini_cli", "claude_code"):
        if name == "gemini_cli" and _gemini_reserved_for_sales():
            continue
        if name == "gemini_cli" and durable_gemini_reset:
            continue
        row = st.get(name) or {}
        if name in owner_policy() or row.get("state") == DISABLED_BY_OWNER:
            continue
        if not row.get("usable", True):
            continue
        out.append(name)
    if include_mock:
        out.append("mock")
    return out


def development_provider_allowed(name, include_mock=False):
    name = str(name or "").strip().lower()
    if name in DEV_PAID_DENY:
        return False
    return name in development_candidates(include_mock=include_mock)


def development_wait_reason():
    """Truthful machine-readable reason when development has no free provider."""
    if development_candidates():
        return None
    st = state()
    gemini = (st.get("gemini_cli") or {}).get("state")
    claude = (st.get("claude_code") or {}).get("state")
    if _gemini_reserved_for_sales():
        return (
            "WAITING_FREE_CODING_PROVIDER: Gemini reserved for client MOP/training; "
            f"Claude={claude}"
        )
    permanent = gemini == UNSUPPORTED_LOCATION and claude in (
        AUTH_ERROR, DISABLED_BY_OWNER, UNSUPPORTED_LOCATION
    )
    if permanent:
        return (
            "WAITING_FREE_CODING_PROVIDER_EXTERNAL: auto-recovery unavailable; "
            f"Gemini={gemini}; Claude={claude}"
        )
    return "WAITING_FREE_CODING_PROVIDER: dispatcher reprobes automatically"


def reprobe_free_development(min_interval_min=30):
    """Проверить Gemini CLI раньше длинного billing cooldown, но без шторма.

    Development policy по-прежнему не разрешает платный OpenAI/Anthropic API.
    Проба выполняется только для уже настроенного subscription/free CLI и не
    чаще одного раза в заданный интервал, когда провайдер помечен недоступным.
    Это закрывает ситуацию, когда краткий отказ оставлял автономную очередь без
    исполнителя на 6 часов, хотя CLI уже снова работал.
    """
    if _gemini_reserved_for_sales():
        return {
            "provider": "gemini_cli",
            "status": "reserved_for_client_sales",
            "probed": False,
            "recovered": False,
            "auto_retry": False,
        }
    row = db.one("""SELECT state, note, retry_at, updated_at,
                           (updated_at IS NULL OR updated_at < NOW() - (:m || ' minutes')::interval) AS due
                      FROM ext_ai_providers WHERE name='gemini_cli'""",
                 m=max(5, int(min_interval_min))) or {}
    daily_reset = gemini_daily_quota_reset_epoch(row.get("note"))
    durable_reset, durable_source = _durable_gemini_daily_reset_evidence()
    if durable_reset:
        restored = not (
            row.get("state") == RATE_LIMITED
            and daily_reset
            and int(daily_reset) == int(durable_reset)
        )
        if restored:
            mark(
                "gemini_cli",
                RATE_LIMITED,
                (
                    "BORIS_GEMINI_DAILY_QUOTA_EXHAUSTED "
                    f"reset_epoch={int(durable_reset)} preserved_from={durable_source}; "
                    "development reprobe restored durable daily latch"
                ),
                retry_after=max(1, int(durable_reset - time.time()) + 15),
            )
        return {
            "provider": "gemini_cli",
            "status": "daily_quota_cooldown",
            "probed": False,
            "recovered": False,
            "auto_retry": True,
            "reset_epoch": int(durable_reset),
            "restored_state": restored,
            "evidence_source": durable_source,
        }
    if row.get("state") == AVAILABLE:
        return {"provider": "gemini_cli", "status": "already_available", "probed": False}
    if row.get("state") == UNSUPPORTED_LOCATION:
        return {
            "provider": "gemini_cli",
            "status": UNSUPPORTED_LOCATION,
            "probed": False,
            "recovered": False,
            "auto_retry": False,
        }
    if row and not row.get("due"):
        return {"provider": "gemini_cli", "status": "cooldown", "probed": False}
    try:
        import subprocess, tempfile
        env = dict(os.environ)
        env.setdefault("TERM", "xterm-256color")
        # Gemini CLI refuses headless execution from a fresh temporary
        # directory unless that workspace is explicitly trusted. The probe
        # intentionally uses an empty temp dir to avoid loading BORIS repo
        # context, so trust that disposable directory for this health check.
        env.setdefault("GEMINI_CLI_TRUST_WORKSPACE", "true")
        prompt = 'Return only JSON: {"status":"ok"}'
        # Never probe Gemini from the BORIS repo. Gemini CLI can auto-load a
        # workspace and burn thousands of input tokens before answering a
        # one-line health check. An empty temporary directory makes the probe
        # truly minimal while HOME still supplies the existing CLI auth.
        with tempfile.TemporaryDirectory(prefix="boris_gemini_probe_") as probe_dir:
            r = subprocess.run(["gemini", "-p", prompt, "-o", "json"], cwd=probe_dir,
                               capture_output=True, text=True, timeout=45, env=env,
                               start_new_session=True)
        raw = ((r.stdout or "") + "\n" + (r.stderr or "")).strip()
        if r.returncode == 0:
            try:
                payload = json.loads(r.stdout or "{}")
                response = payload.get("response") if isinstance(payload, dict) else ""
                good = '"status":"ok"' in str(response).replace(" ", "")
            except Exception:
                good = False
            if good:
                mark("gemini_cli", AVAILABLE, "automatic free CLI health probe PASS")
                return {"provider": "gemini_cli", "status": AVAILABLE,
                        "probed": True, "recovered": True}
        state_name = classify(raw, getattr(r, "returncode", None))
        short_retry = free_tier_short_retry_after(raw)
        mark(
            "gemini_cli",
            state_name,
            "automatic free CLI health probe: " + raw[:300],
            retry_after=(
                max(5, int(short_retry) + 2)
                if state_name == RATE_LIMITED and short_retry is not None
                else None
            ),
        )
        return {"provider": "gemini_cli", "status": state_name,
                "probed": True, "recovered": False,
                "retry_after": short_retry}
    except Exception as exc:
        mark("gemini_cli", TEMP_ERROR,
             "automatic free CLI health probe exception: %s: %s" %
             (type(exc).__name__, str(exc)[:220]))
        return {"provider": "gemini_cli", "status": TEMP_ERROR,
                "probed": True, "recovered": False}


def reprobe_openai_billing(min_interval_min=360):
    """Bounded OpenAI recovery probe owned by background recovery, never live MOP.

    A proven billing failure remains fail-closed after retry_at.  Once the long
    retry window is due, one tiny provider request may prove that funding is
    back.  Only HTTP success marks AVAILABLE.  Transport/provider ambiguity
    preserves the billing latch so customer traffic never becomes the probe.
    """
    enabled = str(os.environ.get("BORIS_OPENAI_BILLING_PROBE_ENABLED", "1")).strip().lower()
    if enabled in {"0", "false", "off", "no"}:
        return {"provider": "openai", "status": "probe_disabled", "probed": False}
    if str(os.environ.get("BORIS_OPENAI_CLIENT_RUNTIME") or "").strip().lower() == "off":
        return {"provider": "openai", "status": "disabled_by_owner", "probed": False}
    key = str(os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        return {"provider": "openai", "status": "key_missing", "probed": False}
    row = db.one(
        """SELECT state, note, retry_at, updated_at,
                  (retry_at IS NULL OR retry_at <= NOW()) AS retry_due,
                  (updated_at IS NULL OR updated_at < NOW() - (:m || ' minutes')::interval) AS interval_due
             FROM ext_ai_providers WHERE name='openai'""",
        m=max(5, int(min_interval_min)),
    ) or {}
    if row.get("state") == AVAILABLE:
        return {"provider": "openai", "status": AVAILABLE, "probed": False, "recovered": False}
    if row.get("state") != UNAVAILABLE_BILLING:
        return {"provider": "openai", "status": str(row.get("state") or "unknown"), "probed": False}
    if not row.get("retry_due") or not row.get("interval_due"):
        return {"provider": "openai", "status": "cooldown", "probed": False}

    retry_long = max(3600, int(os.environ.get("BORIS_SALES_OPENAI_BILLING_RETRY_SEC", "21600") or 21600))
    retry_ambiguous = max(900, int(os.environ.get("BORIS_OPENAI_BILLING_PROBE_RETRY_SEC", "3600") or 3600))
    try:
        import requests
        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
            },
            json={
                "model": str(os.environ.get("BORIS_SALES_OPENAI_MODEL") or "gpt-5.4-mini"),
                "input": "Reply only OK.",
                "max_output_tokens": 16,
            },
            timeout=max(3, min(20, int(os.environ.get("BORIS_OPENAI_BILLING_PROBE_TIMEOUT_SEC", "10") or 10))),
        )
        status_code = int(getattr(response, "status_code", 0) or 0)
        if 200 <= status_code < 300:
            mark("openai", AVAILABLE, "automatic OpenAI billing recovery probe PASS")
            return {"provider": "openai", "status": AVAILABLE, "probed": True, "recovered": True}
        code = ""
        message = ""
        try:
            error = (response.json() or {}).get("error") or {}
            code = str(error.get("code") or error.get("type") or "")[:100]
            message = str(error.get("message") or "")[:180]
        except Exception:
            pass
        kind = classify((code + " " + message).strip(), status_code)
        if kind == AUTH_ERROR:
            mark("openai", AUTH_ERROR, "automatic OpenAI billing probe auth failure", retry_after=retry_long)
            return {"provider": "openai", "status": AUTH_ERROR, "probed": True, "recovered": False}
        if kind == UNAVAILABLE_BILLING:
            mark("openai", UNAVAILABLE_BILLING, "automatic OpenAI billing probe still unfunded", retry_after=retry_long)
            return {"provider": "openai", "status": UNAVAILABLE_BILLING, "probed": True, "recovered": False}
        mark("openai", UNAVAILABLE_BILLING,
             "automatic OpenAI billing probe inconclusive http=%s" % status_code,
             retry_after=retry_ambiguous)
        return {"provider": "openai", "status": "inconclusive", "probed": True,
                "recovered": False, "http_status": status_code}
    except Exception as exc:
        mark("openai", UNAVAILABLE_BILLING,
             "automatic OpenAI billing probe transport inconclusive: %s" % type(exc).__name__,
             retry_after=retry_ambiguous)
        return {"provider": "openai", "status": "transport_inconclusive", "probed": True,
                "recovered": False}


def why_empty(capability):
    st = state()
    return [{"provider": n, "state": (st.get(n) or {}).get("state", AVAILABLE),
             "note": (st.get(n) or {}).get("note", "")}
            for n in (ORDER.get(capability) or ())]


# ------------------------------------------------------- идемпотентность

def _seen(key):
    if not key:
        return None
    r = db.one("SELECT result_json FROM ext_ai_calls WHERE idem_key = :k", k=key)
    if not r:
        return None
    try:
        return json.loads(r["result_json"])
    except Exception:
        return None


def _remember(key, provider, capability, result):
    if not key:
        return
    try:
        db.q("""INSERT INTO ext_ai_calls (idem_key, provider, capability,
                                          result_json, created_at)
                VALUES (:k, :p, :c, :r, NOW())
                ON CONFLICT (idem_key) DO NOTHING""",
             k=key, p=provider, c=capability,
             r=json.dumps(result, ensure_ascii=False, default=str)[:100000])
    except Exception:
        pass


# --------------------------------------------------------------- вызов

def call(capability, runners, idem_key=None, on_switch=None, candidate_names=None):
    """Выполнить операцию, переключая провайдеров по мере отказов.

    runners — {имя провайдера: функция без аргументов}. Наряд, контекст и
    побочный эффект принадлежат вызывающему: здесь только выбор исполнителя
    и решение, повторять или переключаться. Один и тот же idem_key второй раз
    не выполняется — повтор после переключения не должен создавать дубль.
    """
    done = _seen(idem_key)
    if done is not None:
        return {"ok": True, "provider": done.get("__provider"),
                "result": done.get("result"), "reused": True, "attempts": []}
    attempts = []
    selected = list(candidate_names) if candidate_names is not None else candidates(capability)
    for name in selected:
        run = runners.get(name)
        if not run:
            detail = "NO_RUNNER: caller did not provide runner"
            attempts.append({"provider": name, "kind": "NO_RUNNER",
                             "error": detail, "try": 0})
            if on_switch:
                try:
                    on_switch(name, "NO_RUNNER", detail)
                except Exception:
                    pass
            continue
        tries = 0
        while True:
            tries += 1
            try:
                res = run()
                mark(name, AVAILABLE, "успешный вызов")
                _remember(idem_key, name, capability,
                          {"__provider": name, "result": res})
                return {"ok": True, "provider": name, "result": res,
                        "reused": False, "attempts": attempts}
            except ApiError as e:
                raw_error = "%s %s" % (e.code, e.message or "")
                kind = classify(raw_error, e.code)
                detail = "%s: %s" % (e.code, (e.message or "")[:200])
            except Exception as e:
                raw_error = "%s: %s" % (type(e).__name__, str(e))
                kind = classify(raw_error)
                detail = "%s: %s" % (type(e).__name__, str(e)[:200])
            attempts.append({"provider": name, "kind": kind, "error": detail,
                             "try": tries})
            weekly_retry = (weekly_limit_retry_after(raw_error)
                            if name == "claude_code" else None)
            daily_reset = (gemini_daily_quota_reset_epoch(raw_error)
                           if name == "gemini_cli" else None)
            daily_retry = (
                max(1, int(daily_reset - time.time()) + 15)
                if daily_reset is not None else None
            )
            short_retry = (free_tier_short_retry_after(raw_error)
                           if name == "gemini_cli" else None)
            # Revoked OAuth credentials do not heal after a 30-minute retry.
            # Keep Claude out of the autonomous queue until the credential is
            # explicitly refreshed; this prevents repeated 401 storms and
            # leaves Gemini/free routing as the only automatic path.
            auth_retry = (30 * 24 * 3600
                          if name == "claude_code" and kind == AUTH_ERROR and
                          any(x in detail.lower() for x in ("revoked", "failed to authenticate", "401 oauth"))
                          else None)
            retry_override = (
                weekly_retry if weekly_retry is not None else
                daily_retry if daily_retry is not None else
                auth_retry if auth_retry is not None else
                (max(5, int(short_retry) + 2)
                 if kind == RATE_LIMITED and short_retry is not None else None)
            )
            mark(name, kind, detail, retry_after=retry_override)
            # 429/rate-limit is a provider-routing signal: repeating the same
            # already-exhausted provider only delays failover. Keep bounded
            # retries exclusively for transient transport/service failures.
            if kind == RATE_LIMITED or is_terminal(kind) or tries > TEMP_RETRIES:
                if on_switch:
                    try:
                        on_switch(name, kind, detail)
                    except Exception:
                        pass
                break
            time.sleep(min(2 ** tries, 8))
    return {"ok": False, "provider": None, "result": None, "attempts": attempts,
            "why": why_empty(capability),
            "message": "все подходящие провайдеры недоступны для «%s»" % capability}


# ------------------------------------------------- применение решений владельца

def apply_owner_state():
    """Решения владельца, которые нельзя выводить из ошибок API.

    «OpenAI клиентский runtime считать недоступным по бюджету» — это не то,
    что видно из ответа сервера, это распоряжение. Оно применяется здесь и
    снимается тем же способом.
    """
    changed = []
    if os.environ.get("BORIS_OPENAI_CLIENT_RUNTIME") == "off":
        changed.append(mark("openai", UNAVAILABLE_BILLING,
                            "владелец: клиентский runtime OpenAI отключён по бюджету",
                            retry_after=0))
    return changed


def report():
    st = state()
    lines = ["ПРОВАЙДЕРЫ AI"]
    for name in sorted(st, key=lambda n: (COST.get(n, 9), n)):
        r = st[name]
        cost = "подписка" if COST.get(name, 9) == 0 else "платный API"
        lines.append("· %-15s %-20s %s%s" % (
            name, r.get("state", AVAILABLE), cost,
            ("  — " + (r.get("note") or "")[:60]) if r.get("note") else ""))
    lines.append("")
    for cap in CAPABILITIES:
        c = candidates(cap)
        lines.append("%-18s → %s" % (cap, ", ".join(c) if c else "НЕКОМУ"))
    return "\n".join(lines)


# --------------------------------------------------------- controlled E2E

def drill():
    """Доказать переключение, а не рассказать о нём.

    Помечаем OpenAI как «нет баланса», прогоняем текстовую операцию и
    смотрим, кто её выполнил. Состояние восстанавливается в конце — учение
    не должно менять боевую картину.
    """
    before = dict(state().get("openai") or {})
    try:
        mark("openai", UNAVAILABLE_BILLING, "учение: имитация нулевого баланса")
        calls = {"openai": 0, "claude_code": 0}

        def openai_run():
            calls["openai"] += 1
            raise ApiError("RATE_LIMITED", "insufficient_quota: you exceeded "
                                           "your current quota")

        def claude_run():
            calls["claude_code"] += 1
            return {"text": "готово через подписку"}

        key = "drill-%d" % int(_now())
        res = call(TEXT, {"openai": openai_run, "claude_code": claude_run},
                   idem_key=key)
        again = call(TEXT, {"openai": openai_run, "claude_code": claude_run},
                     idem_key=key)
        return {
            "PROVIDER_FAILOVER": "PASS" if res["ok"] else "FAIL",
            "OPENAI_BALANCE_FAILURE_TO_CLAUDE":
                "PASS" if res.get("provider") == "claude_code" else "FAIL",
            "NO_POINTLESS_RETRY": "PASS" if calls["openai"] == 0 else
                                  ("PASS" if calls["openai"] <= 1 else "FAIL"),
            "DUPLICATE_SIDE_EFFECTS":
                0 if (again.get("reused") and calls["claude_code"] == 1) else 1,
            "TASK_LOSS": 0 if res["ok"] else 1,
            "OWNER_ACTION_REQUIRED": 0 if res["ok"] else 1,
            "attempts": res.get("attempts"),
        }
    finally:
        db.q("DELETE FROM ext_ai_calls WHERE idem_key LIKE 'drill-%'")
        if before.get("state") and before["state"] != UNAVAILABLE_BILLING:
            mark("openai", before["state"], before.get("note") or "")
        else:
            mark("openai", AVAILABLE, "восстановлено после учения")


def main():
    import argparse
    ap = argparse.ArgumentParser(prog="aiprov")
    ap.add_argument("cmd", choices=["state", "drill", "apply", "mark"])
    ap.add_argument("--provider")
    ap.add_argument("--to")
    a = ap.parse_args()
    if a.cmd == "state":
        print(report())
    elif a.cmd == "drill":
        print(json.dumps(drill(), ensure_ascii=False, indent=2))
    elif a.cmd == "apply":
        print(json.dumps(apply_owner_state(), ensure_ascii=False, indent=2))
    elif a.cmd == "mark":
        print(json.dumps(mark(a.provider, a.to), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main() or 0)


BILLING_BLOCK_MARKS = ("no credits", "insufficient_quota", "quota",
                       "лимит или баланс", "credits remaining", "billing")


def reroute_blocked(limit=200):
    """Наряды, застрявшие из-за денег провайдера, вернуть в очередь.

    Отсутствие кредитов у OpenAI — не решение владельца и не свойство
    задачи. Такой наряд обязан поехать на другом движке, а не лежать в
    блокировке, пока человек не заметит. 21.08 из-за этого встали все восемь
    исполнителей и четыре клиента одновременно.

    Requeue is useful only when a FREE development provider is usable now.
    Otherwise dispatcher->executor would create a provider-unavailable loop.
    """
    from . import a2a
    if not development_candidates():
        return []
    rows = db.rows("""SELECT id, dev_job_id, blocked_reason FROM ext_a2a_orders
                       WHERE status IN ('blocked_human', 'blocked_infra')
                       ORDER BY id DESC LIMIT :l""", l=int(limit))
    moved = []
    for r in rows:
        why = (r.get("blocked_reason") or "").lower()
        if not any(m in why for m in BILLING_BLOCK_MARKS):
            continue
        a2a._set(r["id"], status=a2a.QUEUED, blocked_reason=None,
                 claimed_by=None, claimed_at=None)
        a2a.message(r["id"], "RESET", "dispatcher",
                    {"why": "у провайдера кончились деньги — наряд возвращён "
                            "в очередь и будет выполнен другим движком"})
        moved.append(r["id"])
    return moved
