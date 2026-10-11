# Known cross-category Avito field semantics that are stable across service templates.
# These values are used only to enrich incomplete cached documentation; exact category
# contracts still come from Avito templates and live xmlcheck feedback.
_AVITO_COMMON_FIELD_OVERLAY = {
    "Place": {"allowed": ["У себя", "У клиента", "Удалённо"], "multiple": True, "name_ru": "Как вы работаете"},
}

# -*- coding: utf-8 -*-
"""Контракт категории Avito для фида: детерминированное разрешение и валидация.

Правила (зафиксированы 04.08):
  * шаблон ищется ТОЛЬКО по точному template_id — никаких ilike, first(), похожих имён;
  * уже заполненные значения ОБЯЗАТЕЛЬНО проверяются, а не пропускаются;
  * значение вне enum — ошибка, подбор похожего запрещён;
  * неизвестное имя категорийного параметра — ошибка;
  * автозаполнение только когда допустимое значение ровно одно либо есть
    настройка продавца, входящая в enum;
  * системные поля объявления валидируются отдельно от категорийных.
"""
import json as _json
import hashlib as _hashlib

# Общие поля объявления. К категорийным параметрам не относятся и здесь не проверяются.
SYSTEM_FIELDS = {
    "Id", "id", "AvitoId", "avitoid", "AuctionPrice", "AuctionPriceLastDate", "Отчет об автозагрузке",
    "Title", "Description", "Price", "Images", "ImageNames", "Address",
    "DateBegin", "DateEnd", "Category", "ListingFee", "AdStatus", "Promo",
    "ImageUrls", "VideoUrls", "VideoURL", "VideoFileURL", "SellerAddressID", "Latitude", "Longitude",
    "Phone", "Manager", "ManagerName", "ContactPhone", "Email", "AllowEmail", "ContactMethod",
    "InternetCalls", "CallsDevices", "TimeZone", "Timezone", "PromoAutoOptions", "PromoManualOptions",
}

CODE_TEMPLATE_ID_MISSING = "template_id_missing"
CODE_TEMPLATE_NOT_FOUND = "template_not_found"
CODE_ENUM_NOT_ALLOWED = "enum_value_not_allowed"
CODE_UNKNOWN_PARAM = "unknown_category_parameter"
CODE_NEEDS_INPUT = "required_field_needs_input"
CODE_CONTRACT_REQUIRED = "contract_required"
CODE_FORMAT = "format_mismatch"

SRC_CLIENT = "client"
SRC_SELLER = "seller_settings"
SRC_CONST = "template_constant"
SRC_DRAFT = "existing_draft"


def _session():
    from app.db.session import SessionLocal
    return SessionLocal()


def resolve_feed_contract(template_id, _db=None):
    """Контракт категории по ТОЧНОМУ template_id. Никакого нечёткого поиска."""
    tid = str(template_id or "").strip()
    if not tid:
        return {"ok": False, "code": CODE_TEMPLATE_ID_MISSING, "template_id": "",
                "category_path": "", "fields": {}, "contract_version": ""}
    db = _db or _session()
    close = _db is None
    try:
        from app.models.category_template import CategoryTemplate, CategoryTreeLeaf
        leaf = db.query(CategoryTreeLeaf).filter(CategoryTreeLeaf.template_id == tid).first()
        tpl = db.query(CategoryTemplate).filter(CategoryTemplate.template_id == tid).first()
        if not leaf and not tpl:
            return {"ok": False, "code": CODE_TEMPLATE_NOT_FOUND, "template_id": tid,
                    "category_path": "", "fields": {}, "contract_version": ""}
        path = (getattr(leaf, "path", "") or getattr(tpl, "category_path", "") or "")
        fields = {}
        raw = getattr(tpl, "required_fields", None) if tpl else None
        if raw:
            parsed = raw if isinstance(raw, list) else _json.loads(raw or "[]")
            for f in parsed:
                tag = f.get("tag")
                if not tag:
                    continue
                fields[tag] = {
                    "required": bool(f.get("required")),
                    "allowed": list(f.get("allowed_values") or []),
                    "format_rule": f.get("format_rule") or "",
                    "name_ru": f.get("name_ru") or "",
                    "depends_on": f.get("depends_on") or "",
                    "multiple": bool(f.get("multiple") or f.get("multi") or f.get("is_multiple")),
                }
        for _tag,_overlay in _AVITO_COMMON_FIELD_OVERLAY.items():
            if _tag in fields:
                if not fields[_tag].get("allowed") and _overlay.get("allowed"):
                    fields[_tag]["allowed"]=list(_overlay.get("allowed") or [])
                if _overlay.get("multiple"):
                    fields[_tag]["multiple"]=True
                if not fields[_tag].get("name_ru") and _overlay.get("name_ru"):
                    fields[_tag]["name_ru"]=_overlay.get("name_ru")
        stamp = "%s|%s|%s" % (tid, getattr(tpl, "fetched_at", "") if tpl else "", len(fields))
        return {"ok": True, "code": "", "template_id": tid, "category_path": path,
                "fields": fields, "contract_version": _hashlib.sha256(stamp.encode()).hexdigest()[:12]}
    finally:
        if close:
            db.close()




def _norm_dep(s):
    return " ".join(str(s or "").strip().lower().replace("ё", "е").split())


def _field_tag_by_label(fields, label):
    want = _norm_dep(label)
    for tag, spec in fields.items():
        if _norm_dep(spec.get("name_ru")) == want or _norm_dep(tag) == want:
            return tag
    return None


def _dep_clause_matches(clause, fields, params):
    import re
    text = str(clause or "").strip()
    low = _norm_dep(text)
    m = re.search(r"если\s+поле\s+(.+?)\s+заполнено", text, re.I)
    if m:
        tag = _field_tag_by_label(fields, m.group(1).strip())
        return bool(tag and not _empty_param(params.get(tag)))
    m = re.search(r"если\s+в\s+поле\s+(.+?)\s+указано\s+значение\s+'([^']+)'", text, re.I)
    if m:
        tag = _field_tag_by_label(fields, m.group(1).strip())
        current=params.get(tag) if tag else None
        if isinstance(current,(list,tuple,set)):
            return m.group(2).strip() in [str(x).strip() for x in current]
        return bool(tag and str("" if current is None else current).strip() == m.group(2).strip())
    m = re.search(r"если\s+в\s+поле\s+(.+?)\s+указаны\s+значения:\s*(.+)$", text, re.I)
    if m:
        tag = _field_tag_by_label(fields, m.group(1).strip())
        raw = m.group(2)
        vals = [x.strip(" '\"") for x in re.split(r"\s+или\s+|,", raw) if x.strip(" '\"")]
        current=params.get(tag) if tag else None
        if isinstance(current,(list,tuple,set)):
            return any(str(x).strip() in vals for x in current)
        return bool(tag and str("" if current is None else current).strip() in vals)
    return False


def _applicable_now(name, spec, fields, params):
    """Conservative applicability: only explicit 'не применимо, если ...' disables a field."""
    dep = str(spec.get("depends_on") or "").strip()
    if not dep:
        return True
    clauses = [x.strip() for x in dep.split("|") if x.strip()]
    for c in clauses:
        if _norm_dep(c).startswith("не применимо") and _dep_clause_matches(c, fields, params):
            return False
    return True


def _required_now(name, spec, fields, params):
    if not spec.get("required"):
        return False
    dep = str(spec.get("depends_on") or "").strip()
    if not dep:
        return True
    clauses = [x.strip() for x in dep.split("|") if x.strip()]
    if not _applicable_now(name, spec, fields, params):
        return False
    conditional = [c for c in clauses if _norm_dep(c).startswith(("обязательно", "применимо"))]
    if conditional:
        return any(_dep_clause_matches(c, fields, params) for c in conditional)
    return True


def _empty_param(value):
    """Missing input is distinct from explicit zero or a false enum value."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return isinstance(value, (list, tuple, set, dict)) and not value


def resolve_applicable_params(params, contract):
    """Keep every valid known category parameter that is currently applicable.
    Unknown/system fields are not silently invented; explicit non-applicable fields are omitted from XML params.
    """
    fields = contract.get("fields") or {}
    src = dict(params or {})
    # A single official enum value is a category constant, not a question.
    # Seed all constants before applicability checks so dependencies are
    # independent of the order of fields in the official contract.
    for name, spec in fields.items():
        allowed = spec.get("allowed") or []
        if name not in SYSTEM_FIELDS and len(allowed) == 1 and _empty_param(src.get(name)):
            src[name] = allowed[0]
    out = {}
    removed = []
    for name, value in src.items():
        if name in SYSTEM_FIELDS:
            continue
        spec = fields.get(name)
        if spec is None:
            continue
        if not _applicable_now(name, spec, fields, src):
            removed.append(name)
            continue
        if not _empty_param(value):
            out[name] = value
    return {"params": out, "removed_non_applicable": removed,
            "available_fields": len([k for k in fields if k not in SYSTEM_FIELDS]),
            "filled_fields": len(out)}

def _fmt_ok(value, rule):
    """Проверка формата по правилу шаблона. Своих правил не выдумываем:
    нет правила — нет проверки формата."""
    if not rule:
        return True
    numeric_rule = rule if isinstance(rule, dict) else None
    if numeric_rule is None:
        try:
            parsed = _json.loads(str(rule))
            numeric_rule = parsed if isinstance(parsed, dict) else None
        except (ValueError, TypeError):
            pass
    if numeric_rule and any(numeric_rule.get(k) is not None for k in ("min", "max")):
        # Official category templates express dimension bounds as JSON. Treat
        # these as inclusive numeric bounds, never as free text or a default.
        from decimal import Decimal, InvalidOperation
        if isinstance(value, bool):
            return False
        try:
            number = Decimal(str(value).strip().replace(" ", "").replace("\u00a0", "").replace(",", "."))
            if not number.is_finite():
                return False
            for key, is_minimum in (("min", True), ("max", False)):
                raw_bound = numeric_rule.get(key)
                if raw_bound is None:
                    continue
                if isinstance(raw_bound, bool):
                    return False
                bound = Decimal(str(raw_bound).strip().replace(",", "."))
                if not bound.is_finite() or (number < bound if is_minimum else number > bound):
                    return False
        except (InvalidOperation, ValueError, TypeError):
            return False
    r = str(rule).lower()
    v = str(value).strip()
    if "целое" in r or "integer" in r:
        return v.replace(" ", "").isdigit()
    if "число" in r or "number" in r:
        return v.replace(" ", "").replace(",", ".").replace(".", "", 1).isdigit()
    return True


def validate_feed_item(item, contract, seller_defaults=None):
    """Проверяет ОДНО объявление против контракта. Ничего не угадывает."""
    seller_defaults = seller_defaults or {}
    out = {"valid": False, "id": str(item.get("id") or ""), "template_id": contract.get("template_id", ""),
           "category_leaf": contract.get("category_path", ""),
           "contract_version": contract.get("contract_version", ""),
           "filled": [], "errors": []}
    if not contract.get("ok"):
        code = contract.get("code") or CODE_TEMPLATE_NOT_FOUND
        out["errors"].append({"field": "template_id", "code": code, "label": "Категория Avito",
                              "value": contract.get("template_id", ""), "allowed_values": [],
                              "message": "Сначала определите категорию Avito"})
        return out
    fields = contract.get("fields") or {}
    params = dict(item.get("params") or {})
    # A present-but-empty key must reach the ordinary required-field check.
    # Unknown keys remain visible so the strict category-name guard still runs.
    params = {name: value for name, value in params.items()
              if name not in fields or name in SYSTEM_FIELDS or not _empty_param(value)}
    constants = resolve_applicable_params(params, contract)["params"]
    for name, value in constants.items():
        if _empty_param(params.get(name)) and len(fields[name].get("allowed") or []) == 1:
            params[name] = value
            out["filled"].append({"field": name, "value": value, "source": SRC_CONST})

    for name, value in params.items():
        if name in SYSTEM_FIELDS:
            continue
        spec = fields.get(name)
        if spec is None:
            out["errors"].append({"field": name, "code": CODE_UNKNOWN_PARAM, "label": name,
                                  "value": value, "allowed_values": [],
                                  "message": "Параметра нет в контракте категории"})
            continue
        allowed = spec.get("allowed") or []
        if allowed:
            allowed_s=[str(a) for a in allowed]
            values=(list(value) if isinstance(value,(list,tuple,set)) else [value])
            if any(str(v) not in allowed_s for v in values):
                out["errors"].append({"field": name, "code": CODE_ENUM_NOT_ALLOWED,
                                      "label": spec.get("name_ru") or name,
                                      "value": value, "allowed_values": allowed,
                                      "message": "Значение вне списка допустимых"})
                continue
        if not _fmt_ok(value, spec.get("format_rule")):
            out["errors"].append({"field": name, "code": CODE_FORMAT, "value": value,
                                  "label": spec.get("name_ru") or name, "allowed_values": [],
                                  "format_rule": spec.get("format_rule"),
                                  "message": "Значение не соответствует формату"})
            continue
        if not any(x["field"] == name for x in out["filled"]):
            out["filled"].append({"field": name, "value": value, "source": SRC_DRAFT})

    for name, spec in fields.items():
        if name in SYSTEM_FIELDS or name in params:
            continue
        allowed = spec.get("allowed") or []
        if name in seller_defaults and not _empty_param(seller_defaults[name]):
            val = seller_defaults[name]
            if allowed and str(val) not in [str(a) for a in allowed]:
                out["errors"].append({"field": name, "code": CODE_ENUM_NOT_ALLOWED,
                                      "label": spec.get("name_ru") or name,
                                      "value": val, "allowed_values": allowed,
                                      "message": "Настройка продавца вне списка допустимых"})
                continue
            if not _fmt_ok(val, spec.get("format_rule")):
                out["errors"].append({"field": name, "code": CODE_FORMAT, "value": val,
                                      "label": spec.get("name_ru") or name, "allowed_values": [],
                                      "format_rule": spec.get("format_rule"),
                                      "message": "Настройка продавца не соответствует формату"})
                continue
            out["filled"].append({"field": name, "value": val, "source": SRC_SELLER})
            params[name] = val
            continue
        required_now = _required_now(name, spec, fields, params)
        if len(allowed) == 1 and required_now:
            out["filled"].append({"field": name, "value": allowed[0], "source": SRC_CONST})
            params[name] = allowed[0]
            continue
        if required_now:
            out["errors"].append({"field": name, "code": CODE_NEEDS_INPUT,
                                  "label": spec.get("name_ru") or name, "value": "",
                                  "allowed_values": allowed,
                                  "message": "Обязательное поле не заполнено: %s" % (spec.get("name_ru") or name)})
    out["resolved_params"] = params
    out["valid"] = not out["errors"]
    return out


def validate_feed_batch(items, seller_defaults=None, _db=None):
    """Пачка объявлений. Сводка по кодам ошибок."""
    db = _db or _session()
    close = _db is None
    try:
        cache, reports = {}, []
        for it in items:
            d = it if isinstance(it, dict) else it.dict()
            tid = str(d.get("template_id") or "").strip()
            if not tid:
                reports.append({"valid": False, "id": str(d.get("id") or ""), "template_id": "",
                                "category_leaf": "", "contract_version": "", "filled": [],
                                "errors": [{"field": "template_id", "code": CODE_CONTRACT_REQUIRED,
                                            "label": "Категория Avito", "value": "", "allowed_values": [],
                                            "message": "Объявление без контракта категории"}]})
                continue
            if tid not in cache:
                cache[tid] = resolve_feed_contract(tid, _db=db)
            reports.append(validate_feed_item(d, cache[tid], seller_defaults))
        by_code = {}
        for r in reports:
            for e in r["errors"]:
                by_code[e["code"]] = by_code.get(e["code"], 0) + 1
        ready = sum(1 for r in reports if r["valid"])
        return {"ok": ready == len(reports), "total": len(reports), "ready": ready,
                "blocked": [r["id"] for r in reports if not r["valid"]],
                "by_code": by_code, "items": reports}
    finally:
        if close:
            db.close()
