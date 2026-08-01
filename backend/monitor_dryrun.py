#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Холостой прогон ядра мониторинга BORIS.

Не ходит в сеть, не трогает БД, не требует telethon и api_id/api_hash.
Читает фикстуры сырых ответов Telegram, прогоняет их через normalize(),
ядро правил и скоринг, и печатает то, что ушло бы в группу оповещений.

Запуск:
    cd /root/BORIS/backend && venv/bin/python3 monitor_dryrun.py
    venv/bin/python3 monitor_dryrun.py --all      # показать и отсеянные
    venv/bin/python3 monitor_dryrun.py --json     # машинный вывод
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.monitoring.base import SourceRef                     # noqa: E402
from app.monitoring.rules import NORMALIZER, SearchRule, evaluate, rules_hash         # noqa: E402
from app.monitoring.telegram import TelegramScanner           # noqa: E402

FIX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "app", "monitoring", "fixtures")

# Опорное «сейчас» — чтобы свежесть считалась одинаково при каждом прогоне
NOW = datetime(2026, 7, 29, 13, 0, 0, tzinfo=timezone.utc)


def load(name: str):
    with open(os.path.join(FIX_DIR, name), "r", encoding="utf-8") as f:
        return json.load(f)


def card(msg, res, source: SourceRef) -> str:
    icon = {"hot": "🔥", "warm": "🙂", "topic": "💬", "noise": "·"}[res.bucket_key]
    parts = [
        f"{icon} {res.bucket_label.upper()}  ({res.score_total}/100)",
        f"Категория: {res.rule.category or res.rule.name}",
        f"Совпадение: {', '.join(res.terms) or '—'}",
        f"Источник: {source.title}" + (f" / тема «{source.topic_title}»" if source.topic_title else ""),
        f"Автор: " + (f"@{msg.author_username}" if msg.author_username else (msg.author_name or "—")),
        f"Дата: {msg.posted_at.strftime('%d.%m.%Y, %H:%M') if msg.posted_at else '—'}",
        f"Сообщение: {msg.text[:220]}",
        f"Ссылка: {msg.url or '—'}",
        f"Баллы: {json.dumps(res.score_breakdown, ensure_ascii=False)}",
    ]
    return "\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="показать и отсеянные сообщения")
    ap.add_argument("--json", action="store_true", help="машинный вывод")
    args = ap.parse_args()

    fixtures = load("telegram_raw.json")
    rules = [SearchRule.from_row(r) for r in load("rules_seed.json")]
    rhash = rules_hash(rules)
    scanner = TelegramScanner()

    stats = {"scanned_sources": 0, "scanned_messages": 0, "skipped_no_text": 0,
             "skipped_old": 0, "matched": 0, "duplicates": 0, "would_send": 0}
    seen: set[tuple[str, str, str]] = set()      # (platform, source_id, message_id)
    contacts: dict[tuple[str, str], dict] = {}   # (platform, author_id) -> контакт
    rows: list[dict] = []
    cards: list[str] = []

    for srow in fixtures["sources"]:
        source = SourceRef.from_row(srow)
        stats["scanned_sources"] += 1
        raw_items = fixtures["items"].get(source.id, [])

        for raw in raw_items:
            stats["scanned_messages"] += 1
            msg = scanner.normalize(raw, source)
            if msg is None:
                stats["skipped_no_text"] += 1
                continue

            # инкрементальность: всё, что не новее последнего виденного, пропускаем
            if source.last_seen_external_id and int(msg.external_message_id) <= int(source.last_seen_external_id):
                stats["skipped_old"] += 1
                continue

            key = (msg.platform, source.id, msg.external_message_id)
            if key in seen:
                stats["duplicates"] += 1
                continue
            seen.add(key)

            res = evaluate(msg.text, rules, platform=msg.platform,
                           source_id=source.id, source_kind=source.source_kind,
                           posted_at=msg.posted_at, now=NOW)

            if res.matched and msg.external_author_id:
                ck = (msg.platform, msg.external_author_id)
                c = contacts.setdefault(ck, {
                    "platform": msg.platform, "external_author_id": msg.external_author_id,
                    "username": msg.author_username, "display_name": msg.author_name,
                    "messages": 0,
                })
                c["messages"] += 1

            rows.append({
                "source": source.title, "id": msg.external_message_id,
                "author": msg.author_username or msg.author_name,
                "matched": res.matched, "rule": res.rule.name if res.rule else None,
                "terms": res.terms, "score": res.score_total,
                "matched_rules": res.matched_rules, "rules_hash": rhash,
                "bucket": res.bucket_key, "priority": res.priority,
                "language": res.language, "breakdown": res.score_breakdown,
                "url": msg.url, "text": msg.text[:120],
            })

            if res.matched:
                stats["matched"] += 1
                stats["would_send"] += 1
                cards.append(card(msg, res, source))

    if args.json:
        print(json.dumps({"stats": stats, "rows": rows,
                          "contacts": list(contacts.values())},
                         ensure_ascii=False, indent=2))
        return 0

    print("=" * 78)
    print("DRY-RUN ЯДРА МОНИТОРИНГА — сеть и БД не используются")
    print(f"нормализатор: {NORMALIZER.name} · правил: {len(rules)} · rules_hash: {rhash}")
    print("=" * 78)
    for c in cards:
        print()
        print(c)
        print("-" * 78)

    if args.all:
        print("\nОТСЕЯНО:")
        for r in rows:
            if not r["matched"]:
                print(f"  [{r['id']}] {r['source']}: {r['text']}")

    print("\nКОНТАКТЫ (кандидаты в contacts):")
    for c in contacts.values():
        name = f"@{c['username']}" if c["username"] else c["display_name"]
        print(f"  {name} (id {c['external_author_id']}) — сообщений: {c['messages']}")

    print("\nЖУРНАЛ ПРОГОНА (лёг бы в monitor_runs):")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
