#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Воркер системы мониторинга источников BORIS.

Собран по образцу posting_runner.py: отдельный процесс, режимы в argv, крон.
Бэкенд не импортирует и не рестартует — работает рядом.

РЕЖИМЫ
    --seed              загрузить источники и правила из фикстур в БД (идемпотентно)
    --fixtures          прогнать ФИКСТУРЫ через весь конвейер С ЗАПИСЬЮ в БД
                        (проверка слоя записи без Telegram и без ключей)
    --scan              боевой сбор: Telegram -> правила -> БД
    --dry               то же, что --scan, но без единой записи
    --platform telegram ограничить платформой (по умолчанию telegram)
    --limit N           сколько сообщений тянуть из источника за раз (по умолчанию 100)

ПРИМЕРЫ
    venv/bin/python3 monitor_runner.py --seed
    venv/bin/python3 monitor_runner.py --fixtures
    venv/bin/python3 monitor_runner.py --scan

Уведомления НЕ рассылаются этим процессом: он только собирает и раскладывает.
Отправку карточек делает monitor_bot.py — так сбор не зависит от доступности бота.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# КРИТИЧНО: .env должен попасть в окружение ДО первого импорта app.* —
# иначе app/db/session.py возьмёт дефолт postgresql://postgres:postgres@...
# и учёт расхода (log_usage через SessionLocal) упадёт на аутентификации.
# Веб-процесс грузит .env через systemd EnvironmentFile; ручной запуск — здесь.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except Exception:  # noqa: BLE001
    pass

from app.monitoring import alerts, budget, dedupe, llm, store                   # noqa: E402
from app.monitoring.stage2 import prepare_raw_items, enrich_normalized_message
from app.monitoring.quality import classify as quality_classify
from app.monitoring.base import ScannerError, SourceRef                 # noqa: E402
from app.monitoring.rules import (                                      # noqa: E402
    NORMALIZER, SearchRule, evaluate, rules_hash,
)
from app.monitoring.telegram import TelegramScanner                     # noqa: E402

LOG_PATH = os.getenv("MONITOR_LOG", "/root/BORIS/backend/logs/monitor.log")
# LLM зовём только для того, что реально пойдёт менеджеру, и с потолком за прогон —
# кривое правило не должно сжечь бюджет
LLM_MIN_SCORE = int(os.getenv("MONITOR_LLM_MIN_SCORE", "40"))
LLM_MAX_PER_RUN = int(os.getenv("MONITOR_LLM_MAX_PER_RUN", "40"))
MAX_MESSAGE_AGE_DAYS = int(os.getenv("MONITOR_MAX_MESSAGE_AGE_DAYS", "14"))
PRE_LLM_REJECT_MARKERS = (
    "предлагаю услуги", "оказываю услуги", "я авитолог", "работаю авитологом",
    "я маркетолог", "я директолог", "мы маркетологи", "мы директологи", "мы авитологи",
    "ищем сотрудника", "ищем в команду", "требуется сотрудник", "отклик на вакансию",
    "присылайте резюме", "отправляйте резюме", "вакансия:",
    "служба по контракту", "службу по контракту", "набор на службу", "военная служба",
)
FIX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "app", "monitoring", "fixtures")

log = logging.getLogger("monitor")


def setup_logging() -> None:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)],
    )


def load_fixture(name: str):
    with open(os.path.join(FIX_DIR, name), "r", encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------- seed

def cmd_seed() -> int:
    """Заливает стартовые источники и правила. Повторный запуск дублей не создаёт."""
    fx = load_fixture("telegram_raw.json")
    rules = load_fixture("rules_seed.json")

    con = store.connect()
    try:
        with con, con.cursor() as cur:
            n_src = 0
            for s in fx["sources"]:
                store.upsert_source(cur, {
                    "id": s["id"], "platform": s["platform"], "source_kind": s["source_kind"],
                    "external_id": s.get("external_id"), "username": s.get("username"),
                    "title": s.get("title"), "topic_id": s.get("topic_id"),
                    "topic_title": s.get("topic_title"), "weight": s.get("weight", 10),
                    "last_seen_external_id": s.get("last_seen_external_id"),
                })
                n_src += 1
            n_rul = 0
            for r in rules:
                store.save_rule(cur, r, changed_by="seed", comment="стартовый набор")
                n_rul += 1
        print(f"источников: {n_src}, правил: {n_rul}")
    finally:
        con.close()
    return 0


# ----------------------------------------------------------------- конвейер

def process_source(cur, scanner, source: SourceRef, raw_items: list[dict],
                   rules: list[SearchRule], rhash: str, run_id: str | None,
                   source_weight: int, stats: dict, write: bool) -> int | None:
    """
    Общая часть для боевого и фикстурного режимов: нормализация -> правила ->
    скоринг -> контакт -> запись. Возвращает максимальный обработанный id.
    """
    max_id = source.last_seen_external_id or 0

    for raw in prepare_raw_items(raw_items):
        stats["scanned_messages"] += 1
        msg = scanner.normalize(raw, source)
        msg = enrich_normalized_message(msg, raw)
        if msg is None:
            continue

        try:
            ext_num = int(msg.external_message_id)
        except (TypeError, ValueError):
            ext_num = 0
        if source.last_seen_external_id and ext_num and ext_num <= int(source.last_seen_external_id):
            continue
        max_id = max(max_id, ext_num)

        # First scan of a newly discovered source can contain hundreds of old posts.
        # Advance the source checkpoint, but do not spend rules/LLM budget on stale leads.
        if msg.posted_at and MAX_MESSAGE_AGE_DAYS > 0:
            age_sec=(datetime.now(timezone.utc)-msg.posted_at).total_seconds()
            if age_sec > MAX_MESSAGE_AGE_DAYS*86400:
                stats["stale_skipped"] = stats.get("stale_skipped",0) + 1
                continue

        res = evaluate(
            msg.text, rules, platform=msg.platform, source_id=source.id,
            source_kind=source.source_kind, source_weight=source_weight,
            posted_at=msg.posted_at,
        )
        if not res.matched:
            continue

        stats["matched_messages"] += 1

        low_text=' '.join(str(msg.text or '').lower().split())
        if any(marker in low_text for marker in PRE_LLM_REJECT_MARKERS):
            stats["pre_llm_rejected"] = stats.get("pre_llm_rejected",0) + 1
            stats["quality_rejected"] = stats.get("quality_rejected",0) + 1
            log.info("[pre-llm-drop] %s/%s marker_noise", source.title, msg.external_message_id)
            continue

        # смысловая группа: тот же текст под другим id объединяется, но НЕ теряется
        dgroup = dedupe.duplicates_group(msg.platform, msg.text, msg.posted_at)

        analysis = {}
        budget_block = None  # None | monthly_budget | daily_analysis_limit | monthly_analysis_limit

        # Deterministic quality pre-gate. Obvious demand and obvious noise do not
        # need an LLM call: this makes the sales monitor faster and avoids paid
        # classification on phrases that rules can resolve safely.
        deterministic = quality_classify(msg.text, None, res.score_total)
        if deterministic["reject"]:
            stats["quality_rejected"] = stats.get("quality_rejected", 0) + 1
            stats["llm_avoided"] = stats.get("llm_avoided", 0) + 1
            log.info(
                "[quality-drop-pre] %s/%s score=%s reasons=%s",
                source.title, msg.external_message_id, res.score_total,
                deterministic.get("reasons"),
            )
            continue
        if deterministic["qualified"]:
            analysis = {"intent": "buy_service", "source": "deterministic_quality_gate"}
            stats["llm_avoided"] = stats.get("llm_avoided", 0) + 1
            wants_llm = False
        else:
            wants_llm = res.score_total >= LLM_MIN_SCORE and stats["llm_calls"] < LLM_MAX_PER_RUN

        if wants_llm and write:
            # финансовый + технические предохранители ДО вызова модели
            verdict = budget.check_budget(cur)
            if not verdict["allowed"]:
                budget_block = verdict["reason"]
                stats["budget_skipped"] = stats.get("budget_skipped", 0) + 1
                # владельцу — одно уведомление за расчётный период
                key = budget.notify_key()
                if not budget.already_notified(cur, key):
                    alerts.send_text(_budget_alert_text(verdict))
                    budget.mark_notified(cur, key)
        if wants_llm and budget_block is None:
            paid_intent_key=hashlib.sha256(json.dumps({
                "policy":"monitor-llm-intent-v1","platform":msg.platform,
                "source_id":str(source.id),"external_message_id":str(msg.external_message_id),
                "text":msg.text or "","rules_hash":rhash,
            },ensure_ascii=False,sort_keys=True,separators=(",",":")).encode("utf-8")).hexdigest()
            analysis = llm.analyze(
                msg.text, res.terms, res.score_breakdown, res.bucket_label,
                source_title=source.title or "", paid_intent_key=paid_intent_key,
            )
            stats["llm_calls"] += 1
            if analysis.get("source") == "llm":
                stats["llm_ok"] += 1

        # QUALITY_GATE_V2
        quality_result = quality_classify(
            msg.text,
            analysis.get("intent"),
            res.score_total,
        )

        if not quality_result["qualified"]:
            stats["quality_rejected"] = (
                stats.get("quality_rejected", 0) + 1
            )

            log.info(
                "[quality-drop] %s/%s score=%s intent=%s reasons=%s",
                source.title,
                msg.external_message_id,
                res.score_total,
                analysis.get("intent"),
                quality_result.get("reasons"),
            )

            continue

        stats["quality_qualified"] = (
            stats.get("quality_qualified", 0) + 1
        )

        if not write:
            log.info("[dry] %s/%s %s баллов: %s | %s",
                     source.title, msg.external_message_id, res.score_total,
                     res.terms, (analysis.get("summary") or "")[:80])
            continue

        contact_id = store.upsert_contact(
            cur, msg.platform, msg.external_author_id,
            msg.author_username, msg.author_name,
        )
        _id, is_new = store.save_message(cur, {
            "platform": msg.platform, "source_id": source.id, "run_id": run_id,
            "external_message_id": msg.external_message_id,
            "external_author_id": msg.external_author_id, "contact_id": contact_id,
            "author_name": msg.author_name, "author_username": msg.author_username,
            "topic_id": msg.topic_id, "topic_title": msg.topic_title,
            "text": msg.text, "posted_at": msg.posted_at, "url": msg.url,
            "matched_rule_id": res.rule.id if res.rule else None,
            "matched_terms": res.terms, "matched_rules": res.matched_rules,
            "rules_hash": rhash, "normalizer": NORMALIZER.name,
            "score_total": res.score_total, "score_breakdown": res.score_breakdown,
            "language": res.language, "priority": res.priority,
            "duplicates_group": dgroup,
            "intent": analysis.get("intent"),
            "summary": analysis.get("summary"),
            "suggested_reply": analysis.get("reply"),
            "ai": {k: v for k, v in analysis.items() if k != "reply"},
            "payload": msg.payload,
            "status": "awaiting_budget" if budget_block else "new",
            "budget_block": budget_block,
        })
        if is_new:
            stats["new_messages"] += 1
            store.bump_contact_messages(cur, contact_id)
        else:
            stats["duplicate_messages"] += 1

    return max_id or None


def _budget_alert_text(verdict: dict) -> str:
    """Текст разового уведомления владельцу об исчерпании лимита."""
    reason = verdict.get("reason")
    if reason == "monthly_budget":
        head = ("💰 <b>Лимит бюджета мониторинга исчерпан</b>\n\n"
                f"Потрачено за месяц: {verdict['spent']:.2f} ₽ из "
                f"{verdict['budget']:.2f} ₽ (остаток {verdict['remaining']:.2f} ₽).\n"
                "Остатка не хватает на один анализ — новые AI-разборы приостановлены.")
    elif reason == "daily_analysis_limit":
        head = ("📊 <b>Дневной лимит анализов исчерпан</b>\n\n"
                f"Сегодня выполнено {verdict['analyses_day']} анализов — "
                "достигнут дневной предел. Новые разборы продолжатся завтра.")
    elif reason == "monthly_analysis_limit":
        head = ("📊 <b>Месячный лимит анализов исчерпан</b>\n\n"
                f"За месяц выполнено {verdict['analyses_month']} анализов — "
                "достигнут месячный предел.")
    else:
        head = "⏸ <b>AI-анализ мониторинга приостановлен</b>"
    return (head + "\n\nНайденные сообщения сохраняются и ждут разбора — "
            "ничего не потеряно. Поднимите лимит в настройках, чтобы продолжить.")


# --------------------------------------------------------- фикстурный прогон

def cmd_fixtures(write: bool = True) -> int:
    """
    Полный конвейер на фикстурах С ЗАПИСЬЮ в боевую БД.
    Доказывает слой записи, дедуп, контакты и журнал до появления api_id/api_hash.
    Повторный запуск обязан дать new_messages=0 и duplicate_messages>0.
    """
    fx = load_fixture("telegram_raw.json")
    scanner = TelegramScanner()
    con = store.connect()
    stats = {"scanned_sources": 0, "scanned_messages": 0, "matched_messages": 0,
             "new_messages": 0, "duplicate_messages": 0, "sent_alerts": 0,
             "flood_wait_seconds": 0, "llm_calls": 0, "llm_ok": 0}
    errors: list[dict] = []

    try:
        with con, con.cursor() as cur:
            rules = [SearchRule.from_row(r) for r in store.load_rules(cur)]
            if not rules:
                print("В БД нет активных правил — сначала: monitor_runner.py --seed")
                return 2
            rhash = rules_hash(rules)
            run_id = store.start_run(cur, "telegram", "manual") if write else None

            for srow in store.load_sources(cur, "telegram"):
                source = SourceRef.from_row(srow)
                raw_items = fx["items"].get(source.id, [])
                if not raw_items:
                    continue
                stats["scanned_sources"] += 1
                try:
                    max_id = process_source(
                        cur, scanner, source, raw_items, rules, rhash, run_id,
                        int(srow.get("weight") or 10), stats, write,
                    )
                    if write:
                        store.bump_source_cursor(cur, source.id, max_id)
                        con.commit()
                except ScannerError as e:
                    errors.append({"source": source.title, "error": str(e)})
                    if write:
                        store.bump_source_cursor(cur, source.id, None, error=str(e))
                        con.commit()

            if write and platform=='telegram':
                store.set_runtime_state(cur,'telegram_source_cadence_last',json.dumps({
                    'selected_sources':int(stats.get('scanned_sources',0) or 0),
                    'deferred_sources':int(stats.get('cadence_deferred_sources',0) or 0),
                    'active_sources':int((stats.get('scanned_sources',0) or 0)+(stats.get('cadence_deferred_sources',0) or 0)),
                    'cadence_min':max(15,int(os.getenv('TG_SOURCE_LOW_ACTIVITY_CADENCE_MIN','30') or 30)),
                    'scanned_messages':int(stats.get('scanned_messages',0) or 0),
                    'matched_messages':int(stats.get('matched_messages',0) or 0),
                    'new_messages':int(stats.get('new_messages',0) or 0),
                    'finished_at':datetime.now(timezone.utc).isoformat(),
                },ensure_ascii=False,sort_keys=True))
            if write and run_id:
                store.finish_run(cur, run_id, stats, errors)
    finally:
        con.close()

    print(json.dumps(stats, ensure_ascii=False, indent=2))
    if errors:
        print("ошибки:", json.dumps(errors, ensure_ascii=False))
    return 0


# ------------------------------------------------------------- боевой сбор

def cmd_scan(platform: str, limit: int, write: bool) -> int:
    """
    Боевой сбор. Требует api_id/api_hash и авторизованной сессии служебного
    аккаунта. Секреты берутся из окружения и НИКОГДА не логируются.
    """
    api_id = os.getenv("MONITOR_TG_API_ID")
    api_hash = os.getenv("MONITOR_TG_API_HASH")
    session_enc = os.getenv("MONITOR_TG_SESSION_ENC")
    session = None
    if session_enc:
        try:
            from app.crypto_utils import decrypt_secret
            session = decrypt_secret(session_enc)
        except Exception as e:  # noqa: BLE001
            log.error("не удалось расшифровать сессию: %s", type(e).__name__)
    if not (api_id and api_hash and session):
        print("Нет MONITOR_TG_API_ID / MONITOR_TG_API_HASH / MONITOR_TG_SESSION_ENC.")
        print("Авторизация служебного аккаунта: venv/bin/python3 monitor_auth.py")
        print("Пока ключей нет — проверять конвейер режимом --fixtures.")
        return 2

    if platform == 'telegram':
        state_con=store.connect()
        try:
            with state_con, state_con.cursor() as state_cur:
                raw=store.get_runtime_state(state_cur,'telegram_scan_cooldown_until')
                if raw:
                    try:
                        until=datetime.fromisoformat(str(raw).replace('Z','+00:00'))
                        if until.tzinfo is None:
                            until=until.replace(tzinfo=timezone.utc)
                        now=datetime.now(timezone.utc)
                        if until > now:
                            remaining=max(1,int((until-now).total_seconds()))
                            print(f'TELEGRAM_SCAN=COOLDOWN remaining={remaining}s until={until.isoformat()}')
                            return 0
                    except Exception:
                        pass
                    store.set_runtime_state(state_cur,'telegram_scan_cooldown_until',None)
        finally:
            state_con.close()

    from app.monitoring.telegram import build_client
    client = build_client(session, int(api_id), api_hash)
    scanner = TelegramScanner(client)

    con = store.connect()
    stats = {"scanned_sources": 0, "scanned_messages": 0, "matched_messages": 0,
             "new_messages": 0, "duplicate_messages": 0, "sent_alerts": 0,
             "flood_wait_seconds": 0, "llm_calls": 0, "llm_ok": 0}
    errors: list[dict] = []

    try:
        with con, con.cursor() as cur:
            if not store.try_lock_run(cur):
                print("TELEGRAM_SCAN=SKIP scanner_lock_busy")
                print("Другой прогон уже идёт — выхожу, ничего не трогая.")
                return 0
            rules = [SearchRule.from_row(r) for r in store.load_rules(cur)]
            if not rules:
                print("Нет активных правил")
                return 2
            if platform=='telegram' and write:
                store.ensure_source_scan_stats_schema(cur)
                con.commit()
            rhash = rules_hash(rules)
            run_id = store.start_run(cur, platform, "scheduled") if write else None
            # Do not hold an idle PostgreSQL transaction while Telegram/network/LLM
            # work runs for minutes. The advisory lock is session-scoped and survives
            # commits; committing here and after each source prevents SSL/proxy
            # idle-in-transaction disconnects from killing the sales scan.
            if write:
                con.commit()

            # Bootstrap stable Telegram access_hash values from dialogs already
            # available to the authorized user session. This is intentionally not
            # Global Search and never joins anything. It only runs while at least
            # one active source still lacks a cached hash, and prevents StringSession
            # from falling back to ResolveUsername after a cooldown expires.
            if platform == 'telegram':
                cur.execute("""
                    SELECT count(*) FROM monitor_sources
                    WHERE platform='telegram' AND is_active=true
                      AND external_id LIKE '-100%'
                      AND COALESCE(payload->>'telegram_access_hash','')=''
                """)
                missing_hashes=int(cur.fetchone()[0] or 0)
                if missing_hashes:
                    try:
                        from telethon.errors import FloodWaitError
                        from telethon.utils import get_peer_id
                        client.connect()
                        hydrated=0
                        for dialog in client.iter_dialogs(limit=500):
                            entity=dialog.entity
                            access_hash=getattr(entity,'access_hash',None)
                            if not access_hash:
                                continue
                            external_id=str(get_peer_id(entity))
                            cur.execute("""UPDATE monitor_sources
                                SET payload=COALESCE(payload,'{}'::jsonb) || jsonb_build_object('telegram_access_hash',%s),updated_at=NOW()
                                WHERE platform='telegram' AND is_active=true AND external_id=%s
                                  AND COALESCE(payload->>'telegram_access_hash','')=''""",
                                (str(access_hash),external_id))
                            hydrated += int(cur.rowcount or 0)
                        if write:
                            con.commit()
                        print(f'TELEGRAM_HASH_BOOTSTRAP=PASS missing={missing_hashes} hydrated={hydrated}')
                    except FloodWaitError as e:
                        retry_after=max(300,int(e.seconds or 0))
                        until=datetime.now(timezone.utc)+timedelta(seconds=retry_after)
                        if write:
                            store.set_runtime_state(cur,'telegram_scan_cooldown_until',until.isoformat()); con.commit()
                        print(f'TELEGRAM_HASH_BOOTSTRAP=COOLDOWN retry_after={retry_after}s')
                        return 0
                    except Exception as e:
                        # Fail closed: scanner itself will skip numeric channels without
                        # hashes instead of resolving usernames and extending throttling.
                        log.warning('telegram access-hash bootstrap degraded: %s',type(e).__name__)

            claimed_sources=store.claim_sources(cur, platform)
            if platform=='telegram':
                cur.execute("SELECT count(*) FROM monitor_sources WHERE platform='telegram' AND is_active=true")
                active_source_count=int(cur.fetchone()[0] or 0)
                stats['cadence_deferred_sources']=max(0,active_source_count-len(claimed_sources))
                print(f"TG_SOURCE_CADENCE selected={len(claimed_sources)} deferred={stats['cadence_deferred_sources']} active={active_source_count} min={max(15,int(os.getenv('TG_SOURCE_LOW_ACTIVITY_CADENCE_MIN','30') or 30))}")
            for srow in claimed_sources:
                source = SourceRef.from_row(srow)
                # Numeric Telegram channels without a cached access_hash are
                # deliberately fail-closed in TelegramScanner to avoid
                # ResolveUsername/FloodWait. Do not count or mark those as
                # successfully scanned until discovery/bootstrap hydrates them.
                if platform == 'telegram' and source.external_id and str(source.external_id).startswith('-100') and not (source.payload or {}).get('telegram_access_hash'):
                    stats["unresolved_sources"] = stats.get("unresolved_sources", 0) + 1
                    continue
                stats["scanned_sources"] += 1
                source_scanned_before=int(stats.get('scanned_messages',0) or 0)
                source_matched_before=int(stats.get('matched_messages',0) or 0)
                source_new_before=int(stats.get('new_messages',0) or 0)
                raw_items=[]
                try:
                    raw_items = scanner.fetch_new(source, limit=limit)
                    max_id = process_source(
                        cur, scanner, source, raw_items, rules, rhash, run_id,
                        int(srow.get("weight") or 10), stats, write,
                    )
                    if write:
                        store.bump_source_cursor(cur, source.id, max_id)
                        if platform=='telegram':
                            store.bump_source_scan_stats(
                                cur,source.id,raw_messages=len(raw_items),
                                processed_messages=int(stats.get('scanned_messages',0) or 0)-source_scanned_before,
                                matched_messages=int(stats.get('matched_messages',0) or 0)-source_matched_before,
                                new_messages=int(stats.get('new_messages',0) or 0)-source_new_before,
                            )
                        con.commit()
                except ScannerError as e:
                    # Small source errors do not stop the run. A long Telegram FloodWait
                    # is account/global throttling, so persist cooldown and stop hammering.
                    log.warning("источник %s: %s", source.title, e)
                    errors.append({"source": source.title, "error": str(e),
                                   "retry_after": e.retry_after})
                    retry_after=int(e.retry_after or 0)
                    stats["flood_wait_seconds"] += retry_after
                    if write:
                        store.bump_source_cursor(cur, source.id, None, error=str(e))
                        if platform=='telegram':
                            store.bump_source_scan_stats(cur,source.id,raw_messages=len(raw_items),error=str(e))
                        if platform=='telegram' and retry_after >= 300:
                            until=datetime.now(timezone.utc)+timedelta(seconds=retry_after)
                            store.set_runtime_state(cur,'telegram_scan_cooldown_until',until.isoformat())
                        con.commit()
                    if platform=='telegram' and retry_after >= 300:
                        log.warning("Telegram global cooldown until %s", until.isoformat() if write else f'+{retry_after}s')
                        break

            if write and run_id:
                store.finish_run(cur, run_id, stats, errors)
    finally:
        con.close()
        try:
            client.disconnect()
        except Exception:  # noqa: BLE001
            pass

    log.info("прогон завершён: %s", json.dumps(stats, ensure_ascii=False))
    if errors:
        log.warning("ошибок: %d", len(errors))
        wait = max((e.get("retry_after") or 0) for e in errors)
        alerts.notify_failure(
            "; ".join(f"{e['source']}: {e['error']}" for e in errors[:5]),
            next_try=(f"через {wait} сек" if wait else "по расписанию"),
        )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--fixtures", action="store_true")
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--dry", action="store_true", help="без единой записи в БД")
    ap.add_argument("--probe-llm", action="store_true",
                    help="показать, как модуль видит пул моделей проекта")
    ap.add_argument("--recount", action="store_true",
                    help="пересчитать messages_count у контактов")
    ap.add_argument("--platform", default="telegram")
    ap.add_argument("--limit", type=int, default=100)
    args = ap.parse_args()

    setup_logging()
    if args.probe_llm:
        info = llm.describe_pool()
        print(json.dumps(info, ensure_ascii=False, indent=2))
        if not info.get("available"):
            print("\nПул не найден. Пришлите этот вывод — подгоню адаптер.")
            return 0

        # Замер учёта расхода: сколько строк в api_usage было до и после вызова.
        before = after = None
        last = None
        try:
            con = store.connect()
            with con, con.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM api_usage")
                before = cur.fetchone()[0]
            con.close()
        except Exception as e:  # noqa: BLE001
            print(f"\n[учёт] не удалось прочитать api_usage: {e}")

        print("\nПробный вызов модели...")
        try:
            answer = llm.call_llm("Ответь одним словом: работает")
            print("ответ:", (answer or "")[:200])
        except Exception as e:  # noqa: BLE001
            print(f"вызов не удался: {e}")

        pool_self = info.get("pool_logs_usage_itself")
        if before is None:
            # не смогли прочитать api_usage (напр. нет доступа к БД из этого процесса)
            if pool_self:
                print("\n[учёт] api_usage не прочитан из этого процесса, НО пул принимает")
                print("[учёт] account_id/operation и логирует расход сам (стр. 167/212).")
                print("[учёт] ВЫВОД: учёт идёт штатно через пул. Флаг НЕ включать.")
            else:
                print("\n[учёт] api_usage не прочитан и пул сам не логирует — проверьте вручную.")
        if before is not None:
            try:
                con = store.connect()
                with con, con.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM api_usage")
                    after = cur.fetchone()[0]
                    cur.execute("SELECT * FROM api_usage ORDER BY 1 DESC LIMIT 1")
                    last = cur.fetchone()
                con.close()
            except Exception as e:  # noqa: BLE001
                print(f"[учёт] повторное чтение не удалось: {e}")

            print(f"\n[учёт] строк в api_usage: было {before}, стало {after}")
            if after is not None and after > before:
                print("[учёт] ВЫВОД: пул сам пишет расход — MONITOR_LLM_LOG_USAGE не включать.")
                print("[учёт] последняя строка:", last)
            elif after is not None and info.get("pool_logs_usage_itself"):
                print("[учёт] число строк не изменилось в ЭТОЙ транзакции, но пул принимает")
                print("[учёт] account_id/operation и логирует сам — расход учитывается штатно.")
                print("[учёт] (запись пула могла уйти в отдельной транзакции/соединении.)")
            elif after is not None:
                print("[учёт] ВЫВОД: вызов В УЧЁТ НЕ ПОПАЛ.")
                print("[учёт] мониторинг будет тратить мимо тарификации.")
                print("[учёт] лечение: MONITOR_LLM_LOG_USAGE=1 в .env, затем повторить проверку.")
        return 0

    if args.recount:
        con = store.connect()
        try:
            with con, con.cursor() as cur:
                n = store.recount_contacts(cur)
            print(f"пересчитано контактов: {n}")
        finally:
            con.close()
        return 0
    if args.seed:
        return cmd_seed()
    if args.fixtures:
        return cmd_fixtures(write=not args.dry)
    if args.scan:
        return cmd_scan(args.platform, args.limit, write=not args.dry)
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
