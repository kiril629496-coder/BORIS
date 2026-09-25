# -*- coding: utf-8 -*-
"""Профильная проверка кандидата: относится ли диалог к тому, чем аккаунт
торгует СЕЙЧАС. Не отсев, а сигнал: не прошедшие получают needs_review.

item_id ненадёжен сам по себе — объявления Avito переиздаются с новым id,
поэтому вторым шагом сверяем слова заголовка со словарём активных объявлений."""
import json
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import text
from app.services.exception_observability import observe_suppressed

log = logging.getLogger(__name__)

CACHE_KEY = "react_profile"
CACHE_TTL_HOURS = 24
MIN_WORD_LEN = 4
STEM_LEN = 5
MIN_OVERLAP = 2

# Общеторговые слова, которые есть у всех и ничего не различают.
STOP = {"заказ", "любой", "срок", "новые", "новый", "продажа", "цена", "цены",
        "москва", "спб", "недорого", "качество", "быстро", "работы", "услуги",
        "разные", "выезд", "доставка", "россии"}


def words(s):
    out = set()
    for w in re.findall(r"[а-яёa-z]+", (s or "").lower()):
        if len(w) >= MIN_WORD_LEN and w not in STOP:
            out.add(w[:STEM_LEN])
    return out


def _fetch_active(account_id):
    """Возвращает (ids, vocab) или (None, None), если Avito не ответил."""
    from app.services.avito_account_throttle import account_throttle_remaining, record_account_throttle
    # REACTIVATION_PROFILE_SHARED_ACCOUNT_THROTTLE_V1: this daily inventory reader
    # must not bypass a tenant Retry-After observed by marketer/stats. Check before
    # token/provider I/O and before every page; a confirmed 429 invalidates partial data.
    if account_throttle_remaining(account_id) > 0:
        return None, None
    from app.api.messenger import _get_user_id_and_token
    uid, tok = _get_user_id_and_token(account_id)
    ids, vocab, page = set(), set(), 1
    while page <= 20:
        if account_throttle_remaining(account_id) > 0:
            return None, None
        r = httpx.get("https://api.avito.ru/core/v1/items",
                      params={"per_page": 100, "page": page, "status": "active"},
                      headers={"Authorization": "Bearer %s" % tok}, timeout=40)
        if r.status_code == 429:
            raw = str(r.headers.get("Retry-After") or "").strip()
            retry = 30
            try:
                retry = max(0, int(float(raw))) if raw else 30
            except Exception:
                try:
                    from email.utils import parsedate_to_datetime
                    when = parsedate_to_datetime(raw)
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=timezone.utc)
                    retry = max(0, int((when.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds()))
                except Exception:
                    retry = 30
            record_account_throttle(account_id, retry, source="reactivation_profile_items_429")
            return None, None
        if r.status_code != 200:
            # REACTIVATION_PROFILE_NO_PARTIAL_INVENTORY_V1: role/profile evidence
            # must be complete. A late-page provider failure cannot turn an
            # arbitrary prefix of the account inventory into authoritative truth.
            log.warning("профиль: %s ответил HTTP %s на странице %d",
                        account_id, r.status_code, page)
            return None, None
        batch = (r.json() or {}).get("resources") or []
        if not batch:
            break
        for x in batch:
            if x.get("id"):
                ids.add(str(x["id"]))
            vocab |= words(x.get("title"))
        page += 1
    return ids, vocab


def profile_of(db, account_id, max_age_hours=CACHE_TTL_HOURS, force=False):
    """Читает кэш, при необходимости обновляет. Один запрос на аккаунт в сутки."""
    row = db.execute(text("SELECT value FROM storage WHERE account_id=:a AND key=:k"),
                     {"a": account_id, "k": CACHE_KEY}).fetchone()
    if row and row[0] and not force:
        try:
            data = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            at = datetime.fromisoformat(data["at"])
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - at < timedelta(hours=max_age_hours):
                return set(data["ids"]), set(data["vocab"])
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=100)
    try:
        ids, vocab = _fetch_active(account_id)
    except Exception as e:
        log.warning("профиль: не удалось обновить %s: %s", account_id, e)
        ids, vocab = None, None
    if ids is None:
        if row and row[0]:
            try:
                data = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                log.info("профиль: беру устаревший кэш %s", account_id)
                return set(data["ids"]), set(data["vocab"])
            except Exception as _suppressed_exc:
                observe_suppressed(__name__, _suppressed_exc, line=113)
        return None, None
    payload = json.dumps({"ids": sorted(ids), "vocab": sorted(vocab),
                          "at": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False)
    if row:
        db.execute(text("UPDATE storage SET value=:v WHERE account_id=:a AND key=:k"),
                   {"v": payload, "a": account_id, "k": CACHE_KEY})
    else:
        db.execute(text("INSERT INTO storage (account_id, key, value) VALUES (:a, :k, :v)"),
                   {"a": account_id, "k": CACHE_KEY, "v": payload})
    db.commit()
    return ids, vocab


def apply_profile(db, account_id, rows, force=False):
    """Проставляет needs_review и profile у кандидатов. Список не укорачивает."""
    ids, vocab = profile_of(db, account_id, force=force)
    if ids is None:
        for r in rows:
            r["profile"] = "unknown"
        log.warning("профиль: %s не проверен, кандидаты помечены unknown", account_id)
        return rows
    for r in rows:
        item = str(r.get("item_id") or "")
        if item and item in ids:
            r["profile"] = "active_item"
            continue
        overlap = words(r.get("item_title")) & vocab
        r["profile_overlap"] = sorted(overlap)
        if len(overlap) >= MIN_OVERLAP:
            r["profile"] = "same_niche"
        else:
            r["profile"] = "foreign"
            r["needs_review"] = True
    return rows
