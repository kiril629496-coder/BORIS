# -*- coding: utf-8 -*-
"""
База знаний BORIS — единственная точка доступа.

Читает knowledge/_schema.json, knowledge/_common.json и knowledge/niches/*.json,
проверяет контракт и держит словари в памяти. Прямое чтение этих файлов
из других модулей запрещено: всё через resolve / get / for_module / common.

Модуль не зависит от базы данных. Проверка ссылок на шаблоны Avito вынесена
в отдельную функцию validate_avito_links(db) и при импорте не выполняется.
"""
import hashlib
import json
import logging
import os
import re
import unicodedata

log = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
KNOWLEDGE_DIR = os.path.join(_HERE, "knowledge")
NICHES_DIR = os.path.join(KNOWLEDGE_DIR, "niches")

SCHEMA = {}
COMMON = {}
BY_SLUG = {}
ALIAS_INDEX = {}
_HEALTH = {"ok": [], "failed": [], "schema_version": None, "loaded": False}


class NicheResolution(object):
    """Результат разбора свободного текста: matched / ambiguous / not_found."""

    def __init__(self, status, slug=None, candidates=None, matched_alias=None):
        self.status = status
        self.slug = slug
        self.candidates = candidates or []
        self.matched_alias = matched_alias

    def as_dict(self):
        return {"status": self.status, "slug": self.slug,
                "candidates": self.candidates, "matched_alias": self.matched_alias}

    def __repr__(self):
        return "NicheResolution(status=%r, slug=%r, candidates=%r)" % (
            self.status, self.slug, self.candidates)


class ContractError(Exception):
    """Нарушение контракта. Несёт точный путь до места ошибки."""

    def __init__(self, path, message):
        self.path = path
        self.message = message
        super(ContractError, self).__init__("%s: %s" % (path, message))


# --- нормализация и разбор текста ---------------------------------------

def normalize(text):
    """Нижний регистр, ё→е, без пунктуации, одинарные пробелы."""
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", str(text)).lower().replace(chr(0x451), chr(0x435))
    s = re.sub(r"[^0-9a-zа-я]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _stems(text):
    """Значимые слова, огрублённые до основы. Общие слова отброшены."""
    conf = SCHEMA.get("resolve", {})
    n = int(conf.get("stem_length", 5))
    stop = set(conf.get("stop_words", []))
    out = []
    for word in normalize(text).split():
        if word in stop or len(word) < 3:
            continue
        out.append(word[:n])
    return out


# --- проверки контракта --------------------------------------------------

def _need(cond, path, message):
    if not cond:
        raise ContractError(path, message)


def _check_confidence(value, path):
    _need(not isinstance(value, bool), path, "confidence не может быть булевым")
    _need(not isinstance(value, (int, float)), path,
          "числовой confidence запрещён — это перечисление, а не шкала client_facts")
    _need(value in SCHEMA["confidence_values"], path,
          "недопустимое значение confidence: %r" % (value,))


def _check_common(common):
    for section in SCHEMA["common_required_sections"]:
        _need(section in common, "_common.json", "нет раздела %s" % section)

    ops = set(common["_operators"])
    modes = set(SCHEMA["detect_modes"])
    pers = set(SCHEMA["detect_per"])
    metrics = set(common["_metrics"])
    scen = set(common["scenarios"])
    results = set(common["business_results"])
    kpis = set(common["kpi"])
    thr_keys = set(common["_niche_thresholds"])

    for key, prob in common["problems"].items():
        path = "_common.json#problems.%s" % key
        for s in prob.get("scenarios", []):
            _need(s in scen, path, "неизвестный сценарий %s" % s)
        if prob.get("result"):
            _need(prob["result"] in results, path, "неизвестный результат %s" % prob["result"])
        fw = prob.get("forbidden_wording") or []
        mt = prob.get("message_template") or ""
        for bad in fw:
            _need(normalize(bad) != normalize(mt), path,
                  "forbidden_wording совпадает с message_template")
        det = prob.get("detect")
        if not det:
            continue
        _need(det["metric"] in metrics, path, "неизвестная метрика %s" % det["metric"])
        _need(common["_metrics"][det["metric"]].get("available"), path,
              "метрика %s помечена как недоступная" % det["metric"])
        _need(det["operator"] in ops, path, "неизвестный оператор %s" % det["operator"])
        _need(det.get("mode", "state") in modes, path, "недопустимый mode")
        _need(det.get("per", "account") in pers, path, "недопустимый per")
        t = det["threshold"]
        _need(t["source"] in SCHEMA["threshold_sources"], path, "неизвестный source порога")
        if t["source"] == "niche":
            _need(t["key"] in thr_keys, path, "порог %s не описан в _niche_thresholds" % t["key"])

    for key, goal in common["goals"].items():
        path = "_common.json#goals.%s" % key
        for s in goal.get("scenarios", []):
            _need(s in scen, path, "неизвестный сценарий %s" % s)
        if goal.get("result"):
            _need(goal["result"] in results, path, "неизвестный результат")

    for key, res in common["business_results"].items():
        path = "_common.json#business_results.%s" % key
        for s in res.get("scenarios", []):
            _need(s in scen, path, "неизвестный сценарий %s" % s)
        for k in res.get("kpi", []):
            _need(k in kpis, path, "неизвестный kpi %s" % k)


def _check_niche(niche, filename):
    path = "niches/%s" % filename
    for field in SCHEMA["niche_required_fields"]:
        _need(field in niche, path, "нет обязательного поля %s" % field)

    _need(niche["schema_version"] == SCHEMA["schema_version"], path,
          "schema_version %s не совпадает со схемой %s" % (
              niche["schema_version"], SCHEMA["schema_version"]))

    slug_from_name = os.path.splitext(filename)[0]
    _need(niche["slug"] == slug_from_name, path,
          "slug %r не совпадает с именем файла" % niche["slug"])

    for name, group in (("goals", "goals"), ("problems", "problems"),
                        ("capabilities", "capabilities"),
                        ("competitor_types", "competitor_types")):
        for key in niche[name]:
            _need(key in COMMON[group], path + "#" + name, "неизвестный ключ %s" % key)
    for key in niche["materials_needed"]:
        _need(key in COMMON["materials"], path + "#materials_needed", "неизвестный ключ %s" % key)
    for item in niche["connect"]:
        _need(item["key"] in COMMON["channels"], path + "#connect", "неизвестный канал %s" % item["key"])
    for group in ("primary", "secondary"):
        for key in niche["kpi"].get(group, []):
            _need(key in COMMON["kpi"], path + "#kpi", "неизвестный kpi %s" % key)

    # пороги: формат и покрытие выбранных проблем
    for key, thr in niche["thresholds"].items():
        tpath = path + "#thresholds." + key
        _need(key in COMMON["_niche_thresholds"], tpath, "порог не описан в _niche_thresholds")
        for field in SCHEMA["threshold_required_fields"]:
            _need(field in thr, tpath, "нет поля %s" % field)
        _check_confidence(thr["confidence"], tpath)
        _need(thr["source"] in COMMON["_source_priority"], tpath,
              "неизвестный источник %s" % thr["source"])
        _need(thr["scope"] in SCHEMA["threshold_scopes"], tpath, "неизвестный scope")

    for key in niche["problems"]:
        det = COMMON["problems"][key].get("detect")
        if det and det["threshold"].get("source") == "niche":
            need_key = det["threshold"]["key"]
            _need(need_key in niche["thresholds"], path + "#thresholds",
                  "проблема %s требует порог %s, его нет" % (key, need_key))

    # уровни достоверности в живых блоках
    for block in ("buyer_questions", "limits"):
        for i, item in enumerate(niche.get(block, [])):
            ipath = "%s#%s[%d]" % (path, block, i)
            _need("confidence" in item, ipath, "нет confidence")
            _check_confidence(item["confidence"], ipath)
            if item.get("source_priority"):
                _need(item["source_priority"] in COMMON["_source_priority"], ipath,
                      "неизвестный источник %s" % item["source_priority"])
    if "avito" in niche and "confidence" in niche["avito"]:
        _check_confidence(niche["avito"]["confidence"], path + "#avito")


def _content_hash(obj):
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# --- загрузка ------------------------------------------------------------

def load(knowledge_dir=None):
    """Прочитать и проверить всю базу знаний. Возвращает health()."""
    global SCHEMA, COMMON, BY_SLUG, ALIAS_INDEX, _HEALTH, KNOWLEDGE_DIR, NICHES_DIR

    if knowledge_dir:
        KNOWLEDGE_DIR = knowledge_dir
        NICHES_DIR = os.path.join(knowledge_dir, "niches")

    SCHEMA = {}
    COMMON = {}
    BY_SLUG = {}
    ALIAS_INDEX = {}
    _HEALTH = {"ok": [], "failed": [], "schema_version": None, "loaded": False}

    with open(os.path.join(KNOWLEDGE_DIR, "_schema.json"), encoding="utf-8") as fh:
        SCHEMA = json.load(fh)
    _HEALTH["schema_version"] = SCHEMA["schema_version"]

    with open(os.path.join(KNOWLEDGE_DIR, "_common.json"), encoding="utf-8") as fh:
        common = json.load(fh)
    _need(common.get("schema_version") == SCHEMA["schema_version"], "_common.json",
          "schema_version не совпадает со схемой")
    _check_common(common)
    COMMON = common

    for filename in sorted(os.listdir(NICHES_DIR)):
        if not filename.endswith(".json"):
            continue
        full = os.path.join(NICHES_DIR, filename)
        try:
            with open(full, encoding="utf-8") as fh:
                niche = json.load(fh)
            _check_niche(niche, filename)

            slug = niche["slug"]
            if slug in BY_SLUG:
                raise ContractError("niches/" + filename, "дублируется slug %s" % slug)

            aliases = [niche["name"]] + list(niche.get("aliases", []))
            normalized = {}
            for alias in aliases:
                na = normalize(alias)
                if not na:
                    continue
                if na in ALIAS_INDEX and ALIAS_INDEX[na] != slug:
                    raise ContractError(
                        "niches/" + filename,
                        "алиас %r после нормализации совпал с нишей %s" % (alias, ALIAS_INDEX[na]))
                normalized[na] = slug

            niche["content_hash"] = _content_hash(niche)
            BY_SLUG[slug] = niche
            ALIAS_INDEX.update(normalized)
            _HEALTH["ok"].append({"file": filename, "slug": slug,
                                  "content_hash": niche["content_hash"],
                                  "aliases": len(normalized)})
        except ContractError as err:
            log.error("KNOWLEDGE: ниша %s исключена — %s", filename, err)
            _HEALTH["failed"].append({"file": filename, "path": err.path, "error": err.message})
        except Exception as err:  # noqa: BLE001 — битый JSON тоже не должен класть модуль
            log.error("KNOWLEDGE: ниша %s исключена — %s", filename, err)
            _HEALTH["failed"].append({"file": filename, "path": "niches/" + filename,
                                      "error": str(err)})

    _HEALTH["loaded"] = True
    return health()


# --- публичное API -------------------------------------------------------

def get(slug):
    return BY_SLUG.get(slug)


def common(section, key=None):
    data = COMMON.get(section)
    if data is None or key is None:
        return data
    return data.get(key)


def health():
    return {
        "loaded": _HEALTH["loaded"],
        "schema_version": _HEALTH["schema_version"],
        "niches_ok": len(_HEALTH["ok"]),
        "niches_failed": len(_HEALTH["failed"]),
        "ok": list(_HEALTH["ok"]),
        "failed": list(_HEALTH["failed"]),
    }


def resolve(text):
    """Свободный текст → ниша. Без модели, без автодобавления алиасов."""
    norm = normalize(text)
    if not norm:
        return NicheResolution("not_found")

    if norm in ALIAS_INDEX:
        return NicheResolution("matched", slug=ALIAS_INDEX[norm], matched_alias=norm)

    query = set(_stems(text))
    if not query:
        return NicheResolution("not_found")

    hits = set()
    for alias, slug in ALIAS_INDEX.items():
        if query & set(_stems(alias)):
            hits.add(slug)

    if len(hits) == 1:
        return NicheResolution("matched", slug=hits.pop())
    if len(hits) > 1:
        return NicheResolution("ambiguous", candidates=sorted(hits))
    return NicheResolution("not_found")


def for_module(slug, module):
    """Срез ниши под конкретный модуль. Лишние блоки не отдаём."""
    niche = BY_SLUG.get(slug)
    if not niche:
        return None
    allowed = SCHEMA["module_slices"].get(module)
    if allowed is None:
        raise KeyError("неизвестный модуль %r" % module)
    out = {"slug": slug, "name": niche["name"], "content_hash": niche["content_hash"]}
    for field in allowed:
        if field in niche:
            out[field] = niche[field]
    return out


def validate_avito_links(db):
    """Отдельная проверка: template_id ниш существуют в category_templates.

    При обычном импорте не вызывается — загрузчик не зависит от базы.
    """
    from sqlalchemy import text as _sql

    problems = []
    for slug, niche in BY_SLUG.items():
        for cat in niche.get("avito", {}).get("categories", []):
            tid = cat.get("template_id")
            if not tid or tid == "TO_CONFIRM":
                problems.append({"slug": slug, "category": cat.get("name"),
                                 "error": "template_id не заполнен"})
                continue
            row = db.execute(_sql(
                "SELECT 1 FROM category_templates WHERE template_id = :t LIMIT 1"),
                {"t": str(tid)}).fetchone()
            if not row:
                problems.append({"slug": slug, "category": cat.get("name"),
                                 "error": "template_id %s не найден в category_templates" % tid})
    return {"checked": len(BY_SLUG), "problems": problems}


try:
    load()
except Exception as _err:  # noqa: BLE001 — база знаний не должна валить приложение
    log.error("KNOWLEDGE: база знаний не загружена — %s", _err)
    _HEALTH["failed"].append({"file": "_common.json или _schema.json",
                              "path": getattr(_err, "path", "?"),
                              "error": str(_err)})
