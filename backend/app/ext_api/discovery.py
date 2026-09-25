# -*- coding: utf-8 -*-
"""Поиск ШТАТНЫХ исполнителей BORIS.

Шлюз ничего не исполняет сам сверх того, что уже есть в проекте. Для каждой
операции задан список кандидатов вида "module:attr". Если ни один не
резолвится — операция объявляется недоступной (NOT_IMPLEMENTED) и НИКОГДА не
отдаёт фиктивный PASS.
"""
import importlib, time

_CACHE = {}
_TTL = 300


def _probe(spec):
    mod, _, attr = spec.partition(":")
    try:
        m = importlib.import_module(mod)
    except Exception as e:
        return None, f"модуль не импортируется: {type(e).__name__}"
    if not attr:
        return m, None
    fn = getattr(m, attr, None)
    if fn is None:
        return None, "в модуле нет такого атрибута"
    return fn, None


def resolve(candidates, force=False):
    """Возвращает (callable|None, spec|None, отчёт по кандидатам)."""
    key = tuple(candidates)
    now = time.time()
    hit = _CACHE.get(key)
    if hit and not force and now - hit[0] < _TTL:
        return hit[1], hit[2], hit[3]
    report = []
    found_fn = found_spec = None
    for spec in candidates:
        fn, err = _probe(spec)
        report.append({"target": spec, "ok": fn is not None, "why": err})
        if fn is not None and found_fn is None:
            found_fn, found_spec = fn, spec
    _CACHE[key] = (now, found_fn, found_spec, report)
    return found_fn, found_spec, report


def invalidate():
    _CACHE.clear()
