# -*- coding: utf-8 -*-
"""Safe normalization of Avito feed category/settings metadata.

Never copies item-specific facts between listings. It only:
- restores an exact template/category path when identity is deterministic;
- projects hierarchy tags from that exact path;
- removes cross-category service/goods garbage;
- keeps only contract-applicable params;
- fills singleton contract constants;
- inherits a tiny whitelist of seller/process settings only by unanimous same-template consensus.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy

SAFE_CONSENSUS_PARAM_FIELDS = {"Place", "ListingFee"}
SAFE_CONSENSUS_TOP_FIELDS = {"ad_type", "condition"}


def _s(v):
    return str(v or "").strip()


def _exact_leaf(db, item: dict):
    from app.models.category_template import CategoryTreeLeaf
    from app.api.feed_contract import resolve_feed_contract

    tid = _s(item.get("template_id"))
    if tid:
        c = resolve_feed_contract(tid, _db=db)
        if c.get("ok"):
            return tid, _s(c.get("category_path"))

    path = _s(item.get("category_path"))
    if path:
        row = db.query(CategoryTreeLeaf).filter(CategoryTreeLeaf.path == path).first()
        if row:
            return _s(row.template_id), _s(row.path)

    params = dict(item.get("params") or {})
    candidates = []
    if _s(item.get("category")) == "Предложение услуг" and _s(item.get("service_type")):
        parts = ["Услуги", "Предложение услуг", _s(item.get("service_type"))]
        if _s(item.get("service_subtype")):
            parts.append(_s(item.get("service_subtype")))
        if _s(params.get("ServiceSubspecies")):
            parts.append(_s(params.get("ServiceSubspecies")))
        candidates.append(" > ".join(parts))
    elif _s(item.get("category")) and _s(item.get("goods_type")):
        # Match by exact path suffix because marketplace super-section varies.
        parts = [_s(item.get("category")), _s(item.get("goods_type"))]
        if _s(item.get("goods_subtype")):
            parts.append(_s(item.get("goods_subtype")))
        for k in ("ProductType", "GoodsSubCategory", "FinishingMaterialsType", "ExteriorFinishingDecorativeStoneSubType"):
            if _s(params.get(k)):
                parts.append(_s(params.get(k)))
        suffix = " > ".join(parts)
        rows = db.query(CategoryTreeLeaf).filter(CategoryTreeLeaf.path.like("% > " + suffix)).all()
        if len(rows) == 1:
            return _s(rows[0].template_id), _s(rows[0].path)
        # Fall back to one level less only when unique.
        base = " > ".join(parts[:3]) if len(parts) >= 3 else suffix
        rows = db.query(CategoryTreeLeaf).filter(CategoryTreeLeaf.path.like("% > " + base)).all()
        if len(rows) == 1:
            return _s(rows[0].template_id), _s(rows[0].path)

    for path in candidates:
        row = db.query(CategoryTreeLeaf).filter(CategoryTreeLeaf.path == path).first()
        if row:
            return _s(row.template_id), _s(row.path)
    return "", ""


def _project_path(item: dict, path: str):
    p = item
    levels = [x.strip() for x in _s(path).split(" > ") if x.strip()]
    params = dict(p.get("params") or {})
    if len(levels) >= 3 and levels[0] == "Услуги" and levels[1] == "Предложение услуг":
        p["category"] = "Предложение услуг"
        p["service_type"] = levels[2]
        p["service_subtype"] = levels[3] if len(levels) >= 4 else ""
        p["goods_type"] = ""; p["goods_subtype"] = ""
        if len(levels) >= 5:
            params.setdefault("ServiceSubspecies", levels[4])
    elif len(levels) >= 3:
        p["category"] = levels[1]
        p["goods_type"] = levels[2]
        p["goods_subtype"] = levels[3] if len(levels) >= 4 else ""
        p["service_type"] = ""; p["service_subtype"] = ""
        if len(levels) >= 5 and levels[2] == "Стройматериалы" and levels[3] == "Отделка":
            params.setdefault("FinishingMaterialsType", levels[4])
            if len(levels) >= 6 and levels[4] == "Наружная отделка и декоративный камень":
                params.setdefault("ExteriorFinishingDecorativeStoneSubType", levels[5])
    p["params"] = params


def normalize_feed_dicts(db, items: list[dict]):
    from app.api.feed_contract import resolve_feed_contract, resolve_applicable_params, _required_now, SYSTEM_FIELDS

    out = [deepcopy(x) for x in items]
    report = {"items": len(out), "template_restored": 0, "path_restored": 0,
              "hierarchy_fixed": 0, "singleton_filled": 0, "consensus_filled": 0,
              "unknown_template": 0, "details": []}

    # First pass: exact category identity + hierarchy projection.
    for item in out:
        before = {k: item.get(k) for k in ("template_id","category_path","category","service_type","service_subtype","goods_type","goods_subtype")}
        tid, path = _exact_leaf(db, item)
        if not tid:
            report["unknown_template"] += 1
            continue
        if _s(item.get("template_id")) != tid:
            item["template_id"] = tid; report["template_restored"] += 1
        if path and _s(item.get("category_path")) != path:
            item["category_path"] = path; report["path_restored"] += 1
        _project_path(item, path)
        after = {k: item.get(k) for k in before}
        if before != after:
            report["hierarchy_fixed"] += 1

        contract = resolve_feed_contract(tid, _db=db)
        if contract.get("ok"):
            params = dict(item.get("params") or {})
            # Legacy rows may already contain HTML entities. _build_xml escapes
            # values itself, so decode once here to prevent double escaping
            # (e.g. Black&amp;White -> Black&White -> valid XML Black&amp;White).
            import html as _html
            params = {k: (_html.unescape(v) if isinstance(v, str) else v) for k, v in params.items()}
            fields = contract.get("fields") or {}
            # Promote core XML tags to dedicated FeedItem fields BEFORE removing
            # them from params. Never lose a fact merely because storage used the
            # legacy params representation.
            promote = {
                "ServiceType":"service_type", "ServiceSubtype":"service_subtype",
                "GoodsType":"goods_type", "GoodsSubType":"goods_subtype",
                "AdType":"ad_type", "Condition":"condition",
                "KitchenType":"kitchen_type", "PriceType":"price_type",
                "Color":"color", "WorkExperience":"work_experience",
                "Guarantee":"guarantee",
            }
            for tag, attr in promote.items():
                if params.get(tag) not in (None, "") and not _s(item.get(attr)):
                    item[attr] = params.get(tag)
            core = {"Category", *promote.keys()}
            for k in core: params.pop(k, None)
            # Fill only template constants where Avito itself allows exactly one value.
            for name, spec in fields.items():
                if name in SYSTEM_FIELDS or name in params:
                    continue
                allowed = list(spec.get("allowed") or [])
                if len(allowed) == 1 and _required_now(name, spec, fields, params):
                    params[name] = allowed[0]; report["singleton_filled"] += 1
            # Legacy exact templates may exist before their field contract has
            # been cached. In that case preserve proven existing params; never
            # erase them just because our local documentation is incomplete.
            item["params"] = (resolve_applicable_params(params, contract).get("params") or {}) if fields else params

    # Second pass: unanimous seller/process settings within exact same template.
    groups = defaultdict(list)
    for item in out:
        if _s(item.get("template_id")):
            groups[_s(item.get("template_id"))].append(item)
    for tid, group in groups.items():
        if len(group) < 2:
            continue
        for field in SAFE_CONSENSUS_TOP_FIELDS:
            vals = {_s(x.get(field)) for x in group if _s(x.get(field))}
            if len(vals) == 1:
                val = next(iter(vals))
                for x in group:
                    if not _s(x.get(field)):
                        x[field] = val; report["consensus_filled"] += 1
        for field in SAFE_CONSENSUS_PARAM_FIELDS:
            vals = []
            for x in group:
                v = (x.get("params") or {}).get(field)
                if v not in (None, "", []): vals.append(v)
            canon = {repr(v) for v in vals}
            if vals and len(canon) == 1:
                val = vals[0]
                for x in group:
                    pp = dict(x.get("params") or {})
                    if pp.get(field) in (None, "", []):
                        pp[field] = val; x["params"] = pp; report["consensus_filled"] += 1
    return out, report
