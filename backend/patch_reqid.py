# -*- coding: utf-8 -*-
"""Шаг 1 пилота бюджета: прокладка request_id.

log_usage УЖЕ принимает request_id снаружи (app/usage.py:199,218) и генерирует
свой только если не передан. Но обёртка его не пробрасывает. Добавляем
необязательный параметр — при None поведение прежнее, остальные вызывающие
места не заметят.

analyze начинает возвращать paid: по одному ok отличить «не платили» от
«заплатили, но разбор упал» нельзя, а guard обязан фиксировать факт расхода."""
import sys, os, ast, time, hashlib

APPLY = "--apply" in sys.argv
B = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "/root/BORIS/backend"

JOBS = [
    (os.path.join(B, "gigachat_pool.py"),
     "def _openai_fallback_completion(messages, temperature=None, max_completion_tokens=16000, attempts=3, timeout=150, account_id=None, operation=None):",
     "def _openai_fallback_completion(messages, temperature=None, max_completion_tokens=16000, attempts=3, timeout=150, account_id=None, operation=None, request_id=None):",
     "сигнатура обёртки", "operation=None, request_id=None)"),
    (os.path.join(B, "gigachat_pool.py"),
     '                _log(account_id, "openai", "gpt-5.4", operation,\n                     _u.get("prompt_tokens", 0), _u.get("completion_tokens", 0))',
     '                _log(account_id, "openai", "gpt-5.4", operation,\n                     _u.get("prompt_tokens", 0), _u.get("completion_tokens", 0),\n                     request_id=request_id)',
     "проброс в log_usage", "request_id=request_id)"),
    (os.path.join(B, "app/reactivation_ai.py"),
     "def analyze(db, account_id, avito_chat_id, item_title=None, model=None):",
     "def analyze(db, account_id, avito_chat_id, item_title=None, model=None, request_id=None):",
     "сигнатура analyze", "model=None, request_id=None)"),
    (os.path.join(B, "app/reactivation_ai.py"),
     '        account_id=account_id, operation=OPERATION)\n    data, err = parse_verdict(resp, set(ids))\n    if err:\n        log.warning("реактивация: разбор ответа не удался (%s), чат %s", err, avito_chat_id)\n        return {"ok": False, "error": err, "raw": _extract_text(resp)[:400]}\n    verdict, why = decide(data)\n    return {"ok": True, "verdict": verdict, "why": why, "data": data, "context_ids": ids}',
     '        account_id=account_id, operation=OPERATION, request_id=request_id)\n'
     '    # Ниже этой строки провайдер УЖЕ ответил и деньги потрачены. Признак paid\n'
     '    # нужен вызывающему: по одному ok отличить «не платили» от «заплатили,\n'
     '    # но разбор упал» невозможно, а бюджет обязан зафиксировать факт.\n'
     '    data, err = parse_verdict(resp, set(ids))\n'
     '    if err:\n'
     '        log.warning("реактивация: разбор ответа не удался (%s), чат %s", err, avito_chat_id)\n'
     '        return {"ok": False, "error": err, "raw": _extract_text(resp)[:400],\n'
     '                "paid": True, "request_id": request_id}\n'
     '    verdict, why = decide(data)\n'
     '    return {"ok": True, "verdict": verdict, "why": why, "data": data,\n'
     '            "context_ids": ids, "paid": True, "request_id": request_id}',
     "признак paid", '"paid": True, "request_id": request_id}'),
]

print("РЕЖИМ:", "ЗАПИСЬ" if APPLY else "ПРОСМОТР")
cache, stop = {}, False
for path, old, new, name, marker in JOBS:
    src = cache.get(path) or open(path, encoding="utf-8").read()
    if marker in src:
        print("  %-20s уже применено" % name); cache[path] = src; continue
    n = src.count(old)
    print("  %-20s якорь: %s" % (name, n))
    if n != 1:
        print("  СТОП: якорь должен быть один"); stop = True; break
    cache[path] = src.replace(old, new, 1)
if stop:
    sys.exit(1)
for p, out in cache.items():
    try:
        ast.parse(out)
    except SyntaxError as e:
        print("  СТОП: синтаксис сломан в %s: %s" % (os.path.basename(p), e)); sys.exit(1)
    print("  %s -> %s" % (os.path.basename(p), hashlib.sha256(out.encode()).hexdigest()[:12]))
if not APPLY:
    print("ничего не записано, повторите с --apply"); sys.exit(0)
for p, out in cache.items():
    bak = p + ".bak_reqid_" + str(int(time.time()))
    open(bak, "w", encoding="utf-8").write(open(p, encoding="utf-8").read())
    open(p, "w", encoding="utf-8").write(out)
    print("  записан", os.path.basename(p))
