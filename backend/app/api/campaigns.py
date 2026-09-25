
# -*- coding: utf-8 -*-
"""API Ad Campaign Workspace.

Единственный новый роутер контура. Публикацию, подтверждение, генерацию и
баннеры он НЕ реализует — для них подключаются существующие механизмы BORIS.

Доступ проверяется по образцу _mem_allowed (client_memory.py): владение
аккаунтом либо связь в user_account_access. Свой механизм прав не заводим.
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import text as _t

from app.db.session import SessionLocal
from app.services import campaign_service as CS
from app.services.action_log import ACTOR_USER, log_action
from app.services.exception_observability import observe_suppressed

try:
    from app.api.auth import get_current_user
except ImportError:  # на случай другой раскладки
    from app.auth import get_current_user

router = APIRouter(prefix="/api/campaign", tags=["campaign"])


def _db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _allowed_accounts(db, user) -> list:
    """Аккаунты, доступные пользователю: владение + user_account_access.
    Владелец платформы видит все — так же, как в остальных модулях."""
    uid = getattr(user, "id", 0)
    if getattr(user, "role", "") == "owner":
        return [r[0] for r in db.execute(_t("SELECT account_id FROM accounts")).fetchall()]
    rows = db.execute(_t(
        "SELECT account_id FROM accounts WHERE owner_user_id=:u"
        " UNION SELECT account_id FROM user_account_access"
        " WHERE user_id=:u AND can_view = TRUE"), {"u": uid}).fetchall()
    return [r[0] for r in rows]


def _campaign_or_404(db, campaign_id: int, allowed: list):
    row = CS.get_campaign(db, campaign_id, allowed_accounts=allowed)
    if row is None:
        raise HTTPException(status_code=404, detail="Кампания не найдена")
    return row


@router.get("/context")
def campaign_account_context(account_id: str = Query(...), db=Depends(_db), user=Depends(get_current_user)):
    """Bounded client context for the campaign launcher cards.

    The launcher must reflect the selected client rather than a demo niche. We
    merge the saved company profile with titles BORIS has actually observed for
    the Avito account and canonical Feed Factory category paths. No full listing
    descriptions, prices, phones or other private payloads are exposed here.
    """
    allowed = _allowed_accounts(db, user)
    aid = str(account_id or "").strip()
    if not aid or aid not in allowed:
        raise HTTPException(status_code=404, detail="Аккаунт не найден")

    account = db.execute(_t(
        "SELECT company_niche, company_description, company_advantages "
        "FROM accounts WHERE account_id=:a LIMIT 1"
    ), {"a": aid}).mappings().first() or {}

    observed = db.execute(_t("""
        SELECT DISTINCT ON (item_id) item_id, title, visibility, measured_at
        FROM avito_position_snapshots
        WHERE account_id=:a AND COALESCE(title,'')<>''
          AND measured_at >= (CURRENT_TIMESTAMP - INTERVAL '72 hours')
        ORDER BY item_id, measured_at DESC
    """), {"a": aid}).mappings().all()
    observed = sorted(observed, key=lambda r: str(r.get("measured_at") or ""), reverse=True)
    # Do not call historical/hidden snapshots "active" in the launcher. That
    # previously made example cards look client-specific while actually using
    # listings Avito had already hidden. Feed titles remain a separate fallback.
    active_visibility = {"active", "visible", "published", "public"}
    active_titles = []
    for row in observed:
        visibility = str(row.get("visibility") or "").strip().lower()
        if visibility not in active_visibility:
            continue
        title = str(row.get("title") or "").strip()
        if title and title not in active_titles:
            active_titles.append(title)
        if len(active_titles) >= 12:
            break

    feed_categories = []
    feed_titles = []
    feed_service_titles = []
    feed_product_titles = []
    feed_service_evidence = 0
    feed_product_evidence = 0
    feed_hint_rows = []
    storage = db.execute(_t(
        "SELECT value FROM storage WHERE account_id=:a AND key='feed_items' LIMIT 1"
    ), {"a": aid}).scalar()
    if storage:
        try:
            rows = json.loads(storage)
            if isinstance(rows, list):
                for item in rows:
                    if not isinstance(item, dict):
                        continue
                    title = str(item.get("title") or "").strip()
                    path = str(item.get("category_path") or "").strip()
                    category = str(item.get("category") or "").strip()
                    service_type = str(item.get("service_type") or "").strip()
                    service_subtype = str(item.get("service_subtype") or "").strip()
                    goods_type = str(item.get("goods_type") or "").strip()
                    goods_subtype = str(item.get("goods_subtype") or "").strip()
                    params = item.get("params") if isinstance(item.get("params"),dict) else {}
                    # Launcher hints cross the browser boundary and are later reused
                    # as confirmed category answers. Expose only user/category fields;
                    # transport/system controls such as ListingFee, Promo, IDs and
                    # contact fields must never become editable campaign facts.
                    try:
                        from app.api.feed_contract import SYSTEM_FIELDS as _LAUNCHER_SYSTEM_FIELDS
                        params={k:v for k,v in params.items() if k not in _LAUNCHER_SYSTEM_FIELDS}
                    except Exception:
                        params={k:v for k,v in params.items() if k not in {"ListingFee","Promo","AdStatus","Id","id","avitoid"}}
                    is_service = bool(path.lower().startswith("услуги >") or category.casefold()=="предложение услуг" or service_type or service_subtype)
                    is_product = bool((path and not path.lower().startswith("услуги >")) or (category and category.casefold()!="предложение услуг" and not is_service) or goods_type or params.get("GoodsType"))
                    if is_service: feed_service_evidence += 1
                    if is_product: feed_product_evidence += 1
                    if title:
                        feed_hint_rows.append({
                            "title": title, "template_id": str(item.get("template_id") or "").strip(),
                            "category_path": path, "params": dict(params),
                            "goods_type": goods_type or str(params.get("GoodsType") or "").strip(),
                            "goods_subtype": goods_subtype or str(params.get("GoodsSubType") or "").strip(),
                            "kind": "services" if is_service and not is_product else ("products" if is_product and not is_service else "")
                        })
                    if title and title not in feed_titles:
                        feed_titles.append(title)
                    if title and is_service and title not in feed_service_titles:
                        feed_service_titles.append(title)
                    if title and is_product and title not in feed_product_titles:
                        feed_product_titles.append(title)
                    if path and path not in feed_categories:
                        feed_categories.append(path)
                    # The launcher needs a bounded sample, but type inference must see
                    # enough rows to understand mixed accounts. 200 is still tiny in
                    # memory and avoids scanning thousand-item feeds on every page open.
                    if len(feed_titles) >= 80 and (feed_service_evidence + feed_product_evidence) >= 200:
                        break
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=158)

    # Server-side launcher directions: profile first, then real Avito category
    # leaves, then observed/feed titles. This keeps the launcher client-specific
    # even when AccountContext is stale and avoids five near-duplicate cards from
    # one product group taking over the examples.
    import re as _re_launcher
    def _clean_launcher_direction(raw):
        value=str(raw or "")
        value=_re_launcher.sub(r"[\u200B-\u200D\u2060\uFEFF]", "", value)
        value=_re_launcher.sub(r"^[\s•·—–-]+", "", value)
        # Resolve spintax before stripping leading punctuation. Otherwise a title
        # starting with "{" loses the opening brace and leaks "A|B|C}" into UI.
        def _first_spin(match):
            inner=str(match.group(1) or "")
            return inner.split("|",1)[0].strip()
        value=_re_launcher.sub(r"\{([^{}]*\|[^{}]*)\}",_first_spin,value)
        value=_re_launcher.sub(r"^[^\wА-Яа-яЁё]+", "", value)
        # Repair a frequent legacy-feed glue typo such as «1200Wдля» so the
        # launcher never copies an unreadable title into a new campaign prompt.
        value=_re_launcher.sub(r"(?<=[A-Za-z0-9])(?=[А-Яа-яЁё])", " ", value)
        value=_re_launcher.sub(r"\bкропусн", "корпусн", value, flags=_re_launcher.IGNORECASE)
        value=_re_launcher.sub(r"\s+", " ", value).strip()
        return value

    def _feed_hint_for_launcher(value, kind):
        key=_clean_launcher_direction(value).casefold()
        if not key: return {}
        matches=[_row for _row in feed_hint_rows
                 if (not _row.get("kind") or _row.get("kind")==kind)
                 and _clean_launcher_direction(_row.get("title")).casefold()==key]
        if not matches: return {}
        # Legacy feeds may know GoodsType/GoodsSubType while lacking exact
        # template_id/category_path. That known business category must outrank
        # lexical guessing from the title: «Тумба под телевизор» is furniture,
        # not a TV. Preserve the proven group and let the later intake resolve an
        # exact leaf instead of inventing one here.
        if not any(str(x.get("template_id") or "").strip() for x in matches):
            known_goods={str(x.get("goods_type") or "").strip() for x in matches if str(x.get("goods_type") or "").strip()}
            known_sub={str(x.get("goods_subtype") or "").strip() for x in matches if str(x.get("goods_subtype") or "").strip()}
            if kind=="products" and len(known_goods)==1:
                _goods_group=next(iter(known_goods))
                params={"GoodsType":_goods_group}
                if len(known_sub)==1:
                    params["GoodsSubType"]=next(iter(known_sub))
                # LAUNCHER_FEED_GROUP_CATEGORY_RESOLUTION_V1: exact client feed
                # GoodsType is authoritative subtree evidence. Resolve a leaf only
                # inside that subtree and only through the normal fail-closed scorer.
                # This prevents lexical collisions such as «тумба под телевизор»
                # selecting the Electronics/TV branch while preserving ambiguity
                # when the group itself does not identify a unique leaf.
                try:
                    _group_cands=_ff_category_candidates(
                        db,_clean_launcher_direction(value),limit=8,path_hint=_goods_group
                    )
                    _group_auto=_ff_auto_category_candidate(
                        _clean_launcher_direction(value),_group_cands
                    )
                except Exception:
                    _group_auto={}
                if _group_auto:
                    return {
                        "template_id":str(_group_auto.get("template_id") or ""),
                        "category_path":str(_group_auto.get("path") or ""),
                        "params":params,"source":"existing_feed_group_resolved",
                        "duplicate_exact_matches":len(matches),"params_consensus":len(matches)>1,
                    }
                return {
                    "template_id":"","category_path":"","params":params,
                    "source":"existing_feed_group_only",
                    "duplicate_exact_matches":len(matches),"params_consensus":len(matches)>1,
                }
            try:
                _cands=_ff_category_candidates(db,_clean_launcher_direction(value),limit=5)
                _auto=_ff_auto_category_candidate(_clean_launcher_direction(value),_cands)
            except Exception:
                _auto=None
            if _auto:
                _tid=str(_auto.get("template_id") or "").strip(); _path=str(_auto.get("path") or "").strip()
                if _tid and _path and not (kind=="services" and not _path.lower().startswith("услуги >")) and not (kind=="products" and _path.lower().startswith("услуги >")):
                    return {"template_id":_tid,"category_path":_path,"params":{},"source":"legacy_feed_taxonomy_exact","duplicate_exact_matches":len(matches),"params_consensus":False}
        # Exact-title duplicates often represent different addresses/sizes/prices.
        # Never let the arbitrary first row become a fact in a new campaign.
        # Reuse category identity only if every non-empty match agrees, and expose
        # only params whose value is identical across all exact-title matches.
        tids={str(x.get("template_id") or "").strip() for x in matches if str(x.get("template_id") or "").strip()}
        paths={str(x.get("category_path") or "").strip() for x in matches if str(x.get("category_path") or "").strip()}
        if len(tids)>1 or len(paths)>1: return {}
        tid=next(iter(tids),""); path=next(iter(paths),"")
        if not tid: return {}
        if not path:
            try:
                from app.models.category_template import CategoryTreeLeaf as _LauncherLeaf
                _leaf=(db.query(_LauncherLeaf).filter(_LauncherLeaf.template_id==tid).order_by(_LauncherLeaf.id.asc()).first())
                path=str(getattr(_leaf,"path","") or "").strip()
            except Exception:
                path=""
        if kind=="services" and path and not path.lower().startswith("услуги >"): return {}
        if kind=="products" and path and path.lower().startswith("услуги >"): return {}
        param_sets=[dict(x.get("params") or {}) for x in matches]
        common=set(param_sets[0]) if param_sets else set()
        for ps in param_sets[1:]: common &= set(ps)
        params={}
        for name in sorted(common):
            encoded=[json.dumps(ps.get(name),ensure_ascii=False,sort_keys=True,default=str) for ps in param_sets]
            if len(set(encoded))==1: params[name]=param_sets[0][name]
        return {"template_id":tid,"category_path":path,"params":params,"source":"existing_feed",
                "duplicate_exact_matches":len(matches),"params_consensus":len(matches)>1}

    def _looks_like_launcher_direction(value):
        """Keep short business directions; reject company prose/boilerplate.

        Launcher cards are task shortcuts, not a place for entire paragraphs from
        company_description. A weak/noisy description must fall back to the saved
        niche and real observed/feed titles rather than masquerading as a service.
        """
        value=_clean_launcher_direction(value)
        if len(value)<4 or len(value)>80: return False
        if len(value.split())>12: return False
        low=value.casefold().strip(" .:;—–-")
        if low in {"москва","санкт-петербург","спб"}: return False
        if value.rstrip().endswith((".", ":")): return False
        if _re_launcher.match(r"^(о компании|компания|наша компания|наши приоритеты|нашими приоритетами|система|все сырье|техническ(?:ое|ая|ий)|реквизиты|сертификаты|знак ростест|знак rohs|знак ce|знак gs|мы работаем для вас)\b", low): return False
        if _re_launcher.search(r"\b(предлагает|позволяет|являются|основан[а-я]*|приобретен[а-я]*|используется|имеет сертификат|можно увидеть|приглашаем)\b", low): return False
        if _re_launcher.match(r"^(?:tm|тм)\s+\S+\s*[-—:]\s*(?:качество|дизайн|функциональность)",low): return False
        # Reject legacy SEO/ALL-CAPS slogans masquerading as launcher directions.
        if len(_re_launcher.findall(r"\b[А-ЯЁ]{4,}\b", value)) >= 3: return False
        letters=[c for c in value if c.isalpha()]
        if len(letters)>=15:
            upper=sum(1 for c in letters if c.isupper())
            if upper/len(letters) > 0.72: return False
        return True

    _profile_description=str(account.get("company_description") or "")
    _profile_niche=str(account.get("company_niche") or "")
    _desc_dirs=[_clean_launcher_direction(x) for x in _re_launcher.split(r"\n|;|•",_profile_description)]
    _desc_dirs=[x for x in _desc_dirs if _looks_like_launcher_direction(x)]
    _niche_dirs=[_clean_launcher_direction(x) for x in _re_launcher.split(r"\n|;|•|,",_profile_niche)]
    _niche_dirs=[x for x in _niche_dirs if _looks_like_launcher_direction(x)]
    # When a real comma-separated assortment ends with «X и Y», split that
    # final list tail too. Do not split ordinary semantic phrases such as
    # «механизированная и ручная штукатурка» where no comma-list exists.
    if "," in _profile_niche:
        _expanded=[]
        for _value in _niche_dirs:
            _parts=[_clean_launcher_direction(x) for x in _re_launcher.split(r"\s+и\s+",_value,flags=_re_launcher.IGNORECASE)] if " и " in _value.lower() else [_value]
            _expanded.extend([x for x in _parts if x])
        _niche_dirs=_expanded
    # Saved niche is curated product truth and always outranks free-form company
    # prose. Description lines are only a last fallback for accounts with neither
    # a niche nor observed/feed/category evidence.
    _profile_dirs=_niche_dirs if _niche_dirs else _desc_dirs
    _category_dirs=[]
    _service_category_dirs=[]
    _product_category_dirs=[]
    for _path in feed_categories:
        _leaf=_clean_launcher_direction(str(_path or "").split(">")[-1])
        if not _leaf: continue
        _category_dirs.append(_leaf)
        if str(_path or "").lower().startswith("услуги >"):
            _service_category_dirs.append(_leaf)
        else:
            _product_category_dirs.append(_leaf)

    def _launcher_kind_from_context():
        # Real feed evidence outranks prose heuristics. Mixed accounts default to
        # the kind with more current cards while still exposing both typed tabs.
        if feed_service_evidence or feed_product_evidence:
            if feed_service_evidence > feed_product_evidence: return "services"
            if feed_product_evidence > feed_service_evidence: return "products"
        has_service=bool(_service_category_dirs or feed_service_titles)
        has_product=bool(_product_category_dirs or feed_product_titles)
        if has_service and not has_product: return "services"
        if has_product and not has_service: return "products"
        low=(str(_profile_niche or "")+" "+str(_profile_description or "")).casefold()
        product_terms=("товар","материал","мебел","кухн","шкаф","гардероб","перегород","огражден","техник","оборудован","запчаст","продукт","магазин","оптов","плитк","брусчат","бордюр","бетон","экипиров","электровел","самокат")
        service_terms=("услуг","ремонт","отделк","аренд","брокер","ипотек","консультац","штукатур","монтаж","установк","маркет","авитолог","копирайт","бурен","сверлен","резк","эвакуат","сопровожд","дизайнер")
        product_score=sum(1 for x in product_terms if x in low)
        service_score=sum(1 for x in service_terms if x in low)
        # Weak words such as «продажи» or «производство» describe both a product
        # company and a service team, so they never decide the kind alone.
        if service_score>product_score: return "services"
        if product_score>service_score: return "products"
        if any(x in low for x in ("маркет","авитолог","копирайт","бурен","сверлен","эвакуат")): return "services"
        if any(x in low for x in ("магазин","товар","материал","мебел","техник","экипиров")): return "products"
        return ""

    _profile_kind=_launcher_kind_from_context()

    # When a real account has no short curated directions and no feed yet, derive
    # a few human launcher directions only from phrases explicitly present in the
    # saved company profile. This is deterministic normalization, not AI invention.
    if not _profile_dirs:
        _profile_text=(str(_profile_niche or "")+" "+str(_profile_description or "")).casefold()
        _semantic=[]
        if _profile_kind=="services":
            _service_candidates=[
                (("продвижение на авито","продвижение avito"), "Продвижение на Avito"),
                (("авитолог",), "Услуги авитолога"),
                (("маркетолог",), "Услуги маркетолога"),
                (("копирайтер",), "Копирайтинг для объявлений"),
                (("менеджер по продажам",), "Менеджер по продажам"),
                (("руководитель отдела продаж",), "Руководитель отдела продаж"),
                (("веб дизайнер","веб-дизайнер"), "Веб-дизайн"),
                (("алмазное бурение",), "Алмазное бурение"),
                (("алмазное сверление",), "Алмазное сверление"),
            ]
            for _needles,_label in _service_candidates:
                if any(_needle in _profile_text for _needle in _needles): _semantic.append(_label)
        elif _profile_kind=="products":
            _product_candidates=[
                (("бытовая техника","бытовой техники"), "Бытовая техника"),
                (("электроника","электроники"), "Электроника"),
                (("посуда",), "Посуда"),
                (("товары для дома и сада",), "Товары для дома и сада"),
                (("тротуарная плитка",), "Тротуарная плитка"),
                (("брусчатка","брусчатки"), "Брусчатка"),
                (("бордюр","бордюров"), "Бордюры"),
            ]
            for _needles,_label in _product_candidates:
                if any(_needle in _profile_text for _needle in _needles): _semantic.append(_label)
        if _semantic:
            _profile_dirs=[]
            for _value in _semantic:
                if _value not in _profile_dirs: _profile_dirs.append(_value)
                if len(_profile_dirs)>=5: break

    # LAUNCHER_HYGIENE_ALL_SOURCES_V1: profile niche, free-form description,
    # active/feed titles and category leaves all pass the same final hygiene
    # predicate before a launcher card can reach the UI.
    def _launcher_direction_is_broad(value):
        low=_clean_launcher_direction(value).casefold()
        broad=(
            ("корпусн" in low and "мебел" in low) or ("мягк" in low and "мебел" in low) or
            low in {"мебель", "мебель на заказ", "бытовая техника", "электроника", "стройматериалы", "строительные товары и материалы"} or
            ("экипиров" in low and "водн" in low and "спорт" in low)
        )
        return bool(broad)

    def _build_launcher(_profile_values, _listing_values, _category_values):
        out=[]
        specific=[x for x in _profile_values if not _launcher_direction_is_broad(x)]
        broad=[x for x in _profile_values if _launcher_direction_is_broad(x)]
        # If real feed titles exist, concrete observed offers outrank broad profile
        # labels. Broad labels remain available only as fallback/context.
        # Concrete observed/feed offers outrank broad company labels. Use up to five
        # exact listing titles before a broad niche such as «экипировка для водного
        # спорта» can occupy a launcher slot; this maximizes zero-touch category
        # resolution without inventing SKUs.
        ranked=[*specific[:3],*_listing_values[:5],*_category_values,*specific[3:],*_listing_values[5:],*broad]
        for _raw in ranked:
            _value=_clean_launcher_direction(_raw)
            if not _looks_like_launcher_direction(_value): continue
            _key=_value.casefold()
            if any(str(x).casefold()==_key for x in out): continue
            out.append(_value)
            if len(out)>=5: break
        return out

    # Overall list powers old clients during rolling deploys. Typed lists prevent
    # the UI from turning a kitchen into a "service" or a plastering service into
    # a "product" when a user switches the launcher tab.
    _listing_examples=active_titles if active_titles else feed_titles
    _launcher_directions=_build_launcher(_profile_dirs,_listing_examples,_category_dirs)
    def _typed_listing_semantic_ok(value, kind):
        low=_clean_launcher_direction(value).casefold()
        # A legacy feed can contain the same business object in both Avito trees.
        # Do not surface rental-intent titles on the Products tab just because an
        # old card was historically placed under a goods category. The Services
        # tab remains authoritative for аренда/прокат semantics.
        if kind=="products" and any(x in low for x in ("аренд", "прокат", "сдам", "напрокат")):
            return False
        # Conversely, a clearly purchase-only title should not become a service
        # shortcut when a product branch exists for the same account.
        if kind=="services" and any(x in low for x in ("купить ", "продажа ", "товар в наличии")):
            return False
        return True

    _service_listing=[x for x in feed_service_titles if _typed_listing_semantic_ok(x,"services")]
    _product_listing=[x for x in feed_product_titles if _typed_listing_semantic_ok(x,"products")]

    # PRODUCT_LAUNCHER_CATEGORY_DIVERSITY_V1: feed order is not business
    # direction priority. A legacy feed can start with dozens of variants from
    # one GoodsType (for example five wardrobes), which used to consume every
    # launcher card. Keep exact real listing titles, but take one representative
    # per proven GoodsType/category family first and only then append siblings.
    def _diversify_product_listing(values):
        representatives=[]; siblings=[]; seen_groups=set()
        for value in list(values or []):
            norm=_clean_launcher_direction(value).casefold()
            match=next((
                row for row in feed_hint_rows
                if row.get("kind") in {"","products"}
                and _clean_launcher_direction(row.get("title")).casefold()==norm
            ),{})
            params=match.get("params") if isinstance(match.get("params"),dict) else {}
            goods_type=str(match.get("goods_type") or params.get("GoodsType") or params.get("goods_type") or "").strip()
            path=str(match.get("category_path") or "").strip()
            parts=[x.strip() for x in path.split(">") if x.strip()]
            parent=parts[-2] if len(parts)>=2 else (parts[-1] if parts else "")
            group=(("goods:"+goods_type.casefold()) if goods_type else
                   ("path:"+parent.casefold()) if parent else
                   ("title:"+norm))
            if group not in seen_groups:
                seen_groups.add(group); representatives.append(value)
            else:
                siblings.append(value)
        return representatives+siblings

    _product_listing=_diversify_product_listing(_product_listing)
    if _profile_kind=="services":
        for _active_title in active_titles:
            if _typed_listing_semantic_ok(_active_title,"services") and _active_title not in _service_listing: _service_listing.append(_active_title)
    elif _profile_kind=="products":
        for _active_title in active_titles:
            if _typed_listing_semantic_ok(_active_title,"products") and _active_title not in _product_listing: _product_listing.append(_active_title)
    _launcher_services=_build_launcher(_profile_dirs if _profile_kind=="services" else [],_service_listing,_service_category_dirs)
    _launcher_products=_build_launcher(_profile_dirs if _profile_kind=="products" else [],_product_listing,_product_category_dirs)

    def _resolved_launcher_hint(value, kind):
        # Existing feed identity is strongest because it is exact account evidence.
        hint=_feed_hint_for_launcher(value,kind)
        if hint.get("template_id") or hint.get("source")=="existing_feed_group_only":
            return hint
        # For profile/active-listing directions with no exact feed row, expose the
        # same conservative category resolver that the workflow will use after the
        # click. This makes the launcher show the real Avito subcategory up front and
        # avoids a pointless second category decision when confidence is sufficient.
        try:
            candidates=_ff_category_candidates(db,value,limit=8)
            auto=_ff_auto_category_candidate(value,candidates)
        except Exception:
            auto={}
        tid=str(auto.get("template_id") or "").strip(); path=str(auto.get("path") or "").strip()
        if not tid or not path:
            return {}
        if kind=="services" and not path.lower().startswith("услуги >"):
            return {}
        if kind=="products" and path.lower().startswith("услуги >"):
            return {}
        return {"template_id":tid,"category_path":path,"params":{},"source":"category_resolver"}

    _launcher_entries_services=[{"title":x,**_resolved_launcher_hint(x,"services")} for x in _launcher_services]
    _launcher_entries_products=[{"title":x,**_resolved_launcher_hint(x,"products")} for x in _launcher_products]

    return {
        "status": "ok",
        "account_id": aid,
        "company_niche": str(account.get("company_niche") or ""),
        "company_description": str(account.get("company_description") or ""),
        "company_advantages": str(account.get("company_advantages") or ""),
        "active_listing_titles": active_titles,
        "feed_listing_titles": feed_titles[:12],
        "feed_category_paths": feed_categories[:12],
        "launcher_directions": _launcher_directions,
        "launcher_directions_services": _launcher_services,
        "launcher_directions_products": _launcher_products,
        "launcher_entries_services": _launcher_entries_services,
        "launcher_entries_products": _launcher_entries_products,
        "launcher_kind": _profile_kind or None,
        "sources": {
            "profile": bool(account),
            "active_listings": len(active_titles),
            "active_snapshot_window_hours": 72,
            "feed_items": len(feed_titles),
        },
    }


def _ff_response_output_text(body: dict) -> str:
    """Extract Responses API text without SDK retry machinery."""
    if not isinstance(body, dict):
        return ""
    if body.get("output_text"):
        return str(body.get("output_text") or "")
    chunks=[]
    for item in (body.get("output") or []):
        for part in ((item or {}).get("content") or []):
            if isinstance(part, dict) and part.get("type") in {"output_text","text"}:
                t=part.get("text")
                if isinstance(t, dict): t=t.get("value")
                if t: chunks.append(str(t))
    return "\n".join(chunks).strip()


def _ff_guarded_openai_response(account_id: str, operation: str, model: str, input_payload,
                                 max_output_tokens: int, idempotency_key: str,
                                 timeout: int = 120, requests_post=None, provenance: dict | None = None) -> dict:
    """Exactly-once paid Campaign/Feed Factory Responses intent.

    One deterministic intent is serialized cross-process, cached durably before
    downstream Campaign state, sent with provider idempotency, and accounted with
    the exact provider request id (or an explicit ambiguous marker). No hidden retry.
    """
    import fcntl, hashlib, json as _json_paid, os, requests, tempfile, time as _time_paid
    from app.services import ai_budget
    from app.services.ai_privacy import safe_external_ai_payload, restore_external_ai_output
    from app.services.ai_guard_audit import log_guard_event as _audit_ai_guard
    from app.usage import log_usage
    account_id=str(account_id or "").strip()
    if not account_id: raise RuntimeError("CAMPAIGN_AI_ACCOUNT_REQUIRED")
    api_key=os.getenv("OPENAI_API_KEY")
    if not api_key: raise RuntimeError("CAMPAIGN_AI_KEY_MISSING")
    idem=str(idempotency_key or "").strip()
    if not idem: raise RuntimeError("CAMPAIGN_AI_IDEMPOTENCY_REQUIRED")
    intent_basis=_json_paid.dumps({
        "policy":"campaign_paid_exactly_once_v2", "account_id":account_id,
        "operation":str(operation), "model":str(model), "input":input_payload,
        "max_output_tokens":int(max_output_tokens), "caller_idempotency_key":idem,
    },ensure_ascii=False,sort_keys=True,separators=(",",":"))
    lock_key=hashlib.sha256(intent_basis.encode("utf-8")).hexdigest()
    provider_idem="campaign-ai:"+lock_key
    lock_path="/tmp/boris_campaign_ai_"+lock_key+".lock"
    cache_root=os.environ.get("BORIS_CAMPAIGN_AI_PAID_CACHE_DIR","/root/BORIS/backend/.campaign_ai_cache/paid")
    cache_path=os.path.join(cache_root,lock_key+".json")
    uncertain_path=cache_path+".uncertain"
    cooldown_path=cache_path+".cooldown"
    run_id="campaign-ai:"+lock_key
    post=requests_post or requests.post

    def _audit(code,status="blocked",details=None):
        _audit_ai_guard(account_id=account_id,source="campaign_ai",operation=operation,
                        event_code=code,status=status,intent_key=idem,
                        details=details if isinstance(details,dict) else None)

    def _uncertain_read():
        try:
            with open(uncertain_path,"r",encoding="utf-8") as fh: obj=_json_paid.load(fh)
            return obj if isinstance(obj,dict) and obj.get("intent_hash")==lock_key else None
        except Exception:
            return None

    def _uncertain_write(error_code,provider_request_id=None,audit=True):
        os.makedirs(cache_root,exist_ok=True)
        fd,tmp=tempfile.mkstemp(prefix=".campaign-uncertain-",suffix=".json",dir=cache_root)
        try:
            with os.fdopen(fd,"w",encoding="utf-8") as fh:
                _json_paid.dump({"schema":1,"intent_hash":lock_key,"status":"provider_outcome_uncertain",
                                 "error_code":str(error_code or "campaign_outcome_unknown"),
                                 "provider_request_id":provider_request_id or None},fh,ensure_ascii=False,sort_keys=True)
                fh.flush(); os.fsync(fh.fileno())
            os.replace(tmp,uncertain_path)
        finally:
            if os.path.exists(tmp):
                try: os.unlink(tmp)
                except OSError: pass
        if audit:
            _audit("CAMPAIGN_AI_OUTCOME_UNCERTAIN","quarantined",{"reason":str(error_code or "campaign_outcome_unknown")})

    def _cooldown_read():
        try:
            with open(cooldown_path,"r",encoding="utf-8") as fh: obj=_json_paid.load(fh)
            until=float((obj or {}).get("until_epoch") or 0)
            left=int(max(0,until-_time_paid.time()))
            if left>0:
                return {"remaining_seconds":left,"status_code":int((obj or {}).get("status_code") or 0)}
            try: os.unlink(cooldown_path)
            except OSError: pass
        except Exception:
            return None
        return None

    def _cooldown_write(status_code,retry_after):
        retry_after=max(60,min(int(retry_after or 300),1800))
        os.makedirs(cache_root,exist_ok=True)
        fd,tmp=tempfile.mkstemp(prefix=".campaign-cooldown-",suffix=".json",dir=cache_root)
        try:
            with os.fdopen(fd,"w",encoding="utf-8") as fh:
                _json_paid.dump({"status_code":int(status_code or 0),"retry_after":retry_after,
                                 "until_epoch":_time_paid.time()+retry_after},fh,ensure_ascii=False,sort_keys=True)
                fh.flush(); os.fsync(fh.fileno())
            os.replace(tmp,cooldown_path)
        finally:
            if os.path.exists(tmp):
                try: os.unlink(tmp)
                except OSError: pass
        return retry_after

    def _cache_read():
        try:
            with open(cache_path,"r",encoding="utf-8") as fh: obj=_json_paid.load(fh)
            if obj.get("intent_hash")!=lock_key: return None
            if obj.get("terminal_error"):
                raise RuntimeError(str(obj.get("terminal_error")))
            text_out=str(obj.get("text") or "").strip()
            if text_out:
                return {"text":text_out,"request_id":obj.get("request_id"),
                        "provider_request_id":obj.get("provider_request_id"),
                        "ambiguous_success":bool(obj.get("ambiguous_success")),
                        "usage":obj.get("usage") or {},"cache_hit":True}
        except RuntimeError:
            raise
        except Exception:
            return None
        return None

    cached=_cache_read()
    if cached is not None: return cached
    uncertain=_uncertain_read()
    if uncertain is not None:
        _audit("CAMPAIGN_AI_OUTCOME_UNCERTAIN","quarantined",{"reason":str(uncertain.get("error_code") or "unknown")})
        raise RuntimeError("CAMPAIGN_AI_OUTCOME_UNCERTAIN:"+str(uncertain.get("error_code") or "unknown"))
    cooldown=_cooldown_read()
    if cooldown is not None:
        _audit("CAMPAIGN_AI_PROVIDER_COOLDOWN","waiting_external",cooldown)
        raise RuntimeError("CAMPAIGN_AI_PROVIDER_COOLDOWN:%s" % cooldown.get("remaining_seconds"))
    with open(lock_path,"a+") as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
        cached=_cache_read()
        if cached is not None: return cached
        uncertain=_uncertain_read()
        if uncertain is not None:
            _audit("CAMPAIGN_AI_OUTCOME_UNCERTAIN","quarantined",{"reason":str(uncertain.get("error_code") or "unknown")})
            raise RuntimeError("CAMPAIGN_AI_OUTCOME_UNCERTAIN:"+str(uncertain.get("error_code") or "unknown"))
        cooldown=_cooldown_read()
        if cooldown is not None:
            _audit("CAMPAIGN_AI_PROVIDER_COOLDOWN","waiting_external",cooldown)
            raise RuntimeError("CAMPAIGN_AI_PROVIDER_COOLDOWN:%s" % cooldown.get("remaining_seconds"))
        # Bounded account caps are independent of ai_budget product limits and
        # protect accidental UI/worker loops. Cache hits never consume the caps.
        db=SessionLocal()
        try:
            ops=tuple(dict.fromkeys(("campaign_reference_vision","campaign_competitor_copy_audit","banner_v4_copy",str(operation))))
            burst=int(db.execute(_t("select count(*) from api_usage where account_id=:a and operation = any(:ops) and created_at>=now()-interval '10 minutes'"),{"a":account_id,"ops":list(ops)}).scalar() or 0)
            daily=int(db.execute(_t("select count(*) from api_usage where account_id=:a and operation = any(:ops) and created_at>=date_trunc('day',now())"),{"a":account_id,"ops":list(ops)}).scalar() or 0)
            retry_seq=int(db.execute(_t("select count(*) from ai_run_budget where run_id like :p"),{"p":run_id+":retry:%"}).scalar() or 0)
            retry_recent=int(db.execute(_t("select count(*) from ai_run_budget where run_id like :p and created_at>=now()-interval '30 minutes'"),{"p":run_id+":retry:%"}).scalar() or 0)
        finally: db.close()
        burst_cap=max(1,int(os.getenv("BORIS_CAMPAIGN_AI_BURST_CALLS","12") or 12)); daily_cap=max(burst_cap,int(os.getenv("BORIS_CAMPAIGN_AI_DAILY_CALLS","60") or 60))
        if daily>=daily_cap:
            _audit("CAMPAIGN_AI_DAILY_CAP","blocked",{"daily_used":daily,"daily_cap":daily_cap})
            raise RuntimeError(f"CAMPAIGN_AI_DAILY_CAP:{daily}/{daily_cap}")
        if burst>=burst_cap:
            _audit("CAMPAIGN_AI_BURST_CAP","blocked",{"used":burst,"cap":burst_cap,"window_min":10})
            raise RuntimeError(f"CAMPAIGN_AI_BURST_CAP:{burst}/{burst_cap}")
        _estimate=max(100,int(os.getenv("BORIS_CAMPAIGN_AI_EST_KOPEKS","2000") or 2000))
        token=ai_budget.reserve(account_id=account_id,module="campaign_ai",operation=operation,run_id=run_id,
                                est_kopeks=_estimate,calls_planned=1)
        st=str((token or {}).get("status") or "") if isinstance(token,dict) else ""
        # A deterministic intent may have a previous RELEASED budget row after a
        # provider non-2xx. There is deliberately no success cache/uncertain marker
        # at this point. Reusing that released row makes the slot fail forever with
        # run_released. Re-arm through a bounded NEW budget run while keeping the
        # same provider Idempotency-Key. This never retries ambiguous transport
        # outcomes because those are quarantined above by uncertain_path.
        if st=="exists" and str((token or {}).get("run_status") or "")=="released" and int((token or {}).get("committed_kopeks") or 0)==0:
            if retry_seq>=3:
                # Hard lifetime cap per deterministic intent. A Social slot has
                # its own intent/idempotency key, so retrying it forever can only
                # burn provider calls; the next slot is unaffected. Three re-arms
                # after the base attempt are enough. Keep this intent fail-closed
                # until an explicit reconciliation/new slot instead of a rolling
                # window that eventually allowed retry:4..retry:N.
                _audit("CAMPAIGN_AI_RELEASED_RETRY_CAP","waiting_external",
                       {"retry_total":retry_seq,"retry_cap":3})
                raise RuntimeError("CAMPAIGN_AI_RELEASED_RETRY_CAP:%s" % retry_seq)
            run_id=("campaign-ai:"+lock_key+":retry:"+str(retry_seq+1))
            token=ai_budget.reserve(account_id=account_id,module="campaign_ai",operation=operation,run_id=run_id,
                                    est_kopeks=_estimate,calls_planned=1)
            st=str((token or {}).get("status") or "") if isinstance(token,dict) else ""
            _audit("CAMPAIGN_AI_RELEASED_RETRY_REARMED","observe",{"retry_no":retry_seq+1,"reserve_status":st})
        if st not in {"reserved","observe","exists"}:
            _audit("AI_BUDGET_RESERVE_FAILED","blocked",{"status":st})
            raise RuntimeError("CAMPAIGN_AI_BUDGET_RESERVE_FAILED:"+st)
        try:
            allowance=ai_budget.allow_next(run_id)
            if not bool((allowance or {}).get("allowed")):
                _audit("AI_BUDGET_NOT_ALLOWED","blocked",{"reason":str((allowance or {}).get("reason") or "")})
                raise RuntimeError("CAMPAIGN_AI_BUDGET_NOT_ALLOWED:"+str((allowance or {}).get("reason") or ""))
            # Durable pre-provider barrier closes the crash-after-send gap. A
            # repeated click/job sees this marker and cannot buy the same intent
            # again until the outcome is explicitly reconciled.
            _uncertain_write("provider_outcome_unknown",audit=False)
            external_input, privacy_vault = safe_external_ai_payload(input_payload)
            try:
                response=post("https://api.openai.com/v1/responses",
                              headers={"Authorization":"Bearer "+api_key,"Content-Type":"application/json","Idempotency-Key":provider_idem},
                              json={"model":model,"input":external_input,"max_output_tokens":int(max_output_tokens)},timeout=int(timeout))
            except Exception as exc:
                _uncertain_write("campaign_transport_outcome_unknown")
                raise RuntimeError("CAMPAIGN_AI_OUTCOME_UNCERTAIN:campaign_transport_outcome_unknown") from exc
            _status=int(getattr(response,"status_code",0) or 0)
            if _status < 200 or _status >= 300:
                try: os.unlink(uncertain_path)
                except OSError: pass
                if _status==429:
                    try: _retry_after=int(str(response.headers.get("Retry-After") or "300").strip())
                    except Exception: _retry_after=300
                    _retry_after=_cooldown_write(_status,_retry_after)
                    _audit("CAMPAIGN_AI_PROVIDER_COOLDOWN","waiting_external",{"status_code":429,"retry_after":_retry_after})
                    raise RuntimeError("CAMPAIGN_AI_PROVIDER_COOLDOWN:%s" % _retry_after)
                response.raise_for_status()
            try: os.unlink(cooldown_path)
            except OSError: pass
            exact_rid=str(response.headers.get("x-request-id") or "").strip()
            rid=exact_rid or ("ambiguous-text:campaign:"+lock_key)
            try:
                body=response.json() or {}
            except Exception as exc:
                _uncertain_write("provider_2xx_unparseable",exact_rid or None)
                raise RuntimeError("CAMPAIGN_AI_OUTCOME_UNCERTAIN:provider_2xx_unparseable") from exc
            usage=body.get("usage") or {}
            text_out=_ff_response_output_text(body)
            if text_out:
                text_out=str(restore_external_ai_output(text_out, privacy_vault))
            terminal_error="" if text_out else "CAMPAIGN_AI_PAID_RESPONSE_UNUSABLE:"+rid

            # Provider success is the economic boundary. Persist a success result
            # or a terminal tombstone before ledger/state work so the same intent
            # can never be purchased again after a process/HTTP failure.
            os.makedirs(cache_root,exist_ok=True)
            fd,tmp=tempfile.mkstemp(prefix=".campaign-paid-",suffix=".json",dir=cache_root)
            try:
                with os.fdopen(fd,"w",encoding="utf-8") as fh:
                    _json_paid.dump({"schema":2,"intent_hash":lock_key,"operation":operation,
                        "request_id":rid,"provider_request_id":exact_rid or None,
                        "ambiguous_success":not bool(exact_rid),"usage":usage,
                        "text":text_out,"terminal_error":terminal_error},fh,ensure_ascii=False,sort_keys=True)
                    fh.flush(); os.fsync(fh.fileno())
                os.replace(tmp,cache_path)
                try: os.unlink(uncertain_path)
                except OSError: pass
            finally:
                if os.path.exists(tmp):
                    try: os.unlink(tmp)
                    except OSError: pass

            log_usage(account_id,"openai",model,operation,
                      int(usage.get("input_tokens") or 0),int(usage.get("output_tokens") or 0),
                      request_id=rid,usage_details={**(usage if isinstance(usage,dict) else {}),
                          "boris_intent_key": provider_idem,
                          "boris_provenance": {str(k)[:64]: v for k,v in (provenance or {}).items() if isinstance(v,(str,int,float,bool)) or v is None}})
            committed=ai_budget.commit(run_id,rid)
            if str((committed or {}).get("status") or "") not in {"committed","already_committed"}:
                raise RuntimeError("CAMPAIGN_AI_COMMIT_FAILED:"+str((committed or {}).get("status") or ""))
            if terminal_error: raise RuntimeError(terminal_error)
            return {"text":text_out,"request_id":rid,"provider_request_id":exact_rid or None,
                    "ambiguous_success":not bool(exact_rid),"usage":usage,"cache_hit":False}
        finally:
            ai_budget.release(run_id)


class CreateCampaignBody(BaseModel):
    name: str
    account_id: str = ""
    scenario_type: str = ""
    source_type: str = ""


class ItemPatchBody(BaseModel):
    patch: dict = {}
    status: str = ""


class NewItemBody(BaseModel):
    account_id: str
    payload: dict = {}
    segment: str = ""
    intent: str = ""
    geo: str = ""
    offer: str = ""
    source: str = "manual"


@router.post("/create")
def create_campaign(body: CreateCampaignBody, db=Depends(_db),
                    user=Depends(get_current_user)):
    allowed = _allowed_accounts(db, user)
    acc = (body.account_id or "").strip()
    if acc and acc not in allowed:
        raise HTTPException(status_code=403, detail="Нет доступа к аккаунту")
    row = CS.new_campaign(db, name=body.name, owner_user_id=getattr(user, "id", None),
                          default_account_id=acc or None,
                          scenario_type=body.scenario_type or None,
                          source_type=body.source_type or None)
    log_action(account_id=acc or "", action="Создал кампанию",
               object_kind="кампания", object_name=row.name,
               actor=ACTOR_USER, user_id=getattr(user, "id", None),
               source="campaign.create")
    return {"status": "ok", "campaign": CS.campaign_public(db, row)}


@router.get("/list")
def list_campaigns(account_id: str = "", db=Depends(_db),
                   user=Depends(get_current_user)):
    from app.models.campaign import Campaign
    from app.models.campaign_item import CampaignItem
    allowed = _allowed_accounts(db, user)
    if account_id:
        if account_id not in allowed:
            raise HTTPException(status_code=403, detail="Нет доступа к аккаунту")
        allowed = [account_id]
    ids = {r[0] for r in db.query(CampaignItem.campaign_id)
           .filter(CampaignItem.account_id.in_(allowed or [""])).distinct().all()}
    empty = {r[0] for r in db.query(Campaign.id)
             .filter(Campaign.default_account_id.in_(allowed or [""])).all()}
    rows = (db.query(Campaign).filter(Campaign.id.in_(list(ids | empty) or [0]),
                                      Campaign.status != "superseded")
              .order_by(Campaign.id.desc()).limit(100).all())
    return {"status": "ok",
            "campaigns": [CS.campaign_public(db, r) for r in rows]}


@router.get("/ab-tests")
def campaign_ab_tests(account_id:str, db=Depends(_db), user=Depends(get_current_user)):
    """Exact creative memory + latest real Avito conversion for A/B learning."""
    if account_id not in _allowed_accounts(db,user):
        raise HTTPException(403,"Нет доступа к аккаунту")
    rows=db.execute(_t("SELECT key,value FROM storage WHERE account_id=:a AND key LIKE 'avito_ab_creative_snapshot:%' ORDER BY id DESC"),{"a":account_id}).fetchall()
    snapshots=[]
    for key,raw in rows:
        try: obj=json.loads(raw or "{}")
        except Exception: continue
        if isinstance(obj,dict): snapshots.append(obj)
    stat_raw=db.execute(_t("SELECT value FROM storage WHERE account_id=:a AND key LIKE 'daily_stats:%' ORDER BY key DESC,id DESC LIMIT 1"),{"a":account_id}).scalar()
    try: stats=json.loads(stat_raw or "{}")
    except Exception: stats={}
    by_item={}
    for it in (stats.get("items") or [] if isinstance(stats,dict) else []):
        ident=it.get("campaign_identity") or {}
        cid=ident.get("campaign_item_id")
        if cid is not None: by_item[str(cid)]=it
    out=[]
    seen=set()
    for snap in snapshots:
        for it in snap.get("items") or []:
            k=str(it.get("item_id") or "")
            if not k or k in seen: continue
            seen.add(k); m=by_item.get(k) or {}
            out.append({**it,"views":int(m.get("views") or 0),"contacts":int(m.get("contacts") or 0),"conversion":float(m.get("conversion") or 0),"avito_status":m.get("status") or "waiting_data"})
    memory={"account_id":account_id,"stats_date":stats.get("stats_date") or stats.get("date"),"items":out}
    val=json.dumps(memory,ensure_ascii=False)
    ex=db.execute(_t("SELECT id FROM storage WHERE account_id=:a AND key='avito_ab_learning_memory' ORDER BY id DESC LIMIT 1"),{"a":account_id}).first()
    if ex: db.execute(_t("UPDATE storage SET value=:v WHERE id=:i"),{"v":val,"i":ex[0]})
    else: db.execute(_t("INSERT INTO storage(account_id,key,value) VALUES(:a,'avito_ab_learning_memory',:v)"),{"a":account_id,"v":val})
    db.commit()
    ranked=sorted(out,key=lambda x:(x.get("conversion",0),x.get("contacts",0),x.get("views",0)),reverse=True)
    return {"status":"ok","account_id":account_id,"stats_date":memory["stats_date"],"items":out,"leaders":ranked[:5],"total":len(out),"with_data":sum(1 for x in out if x.get("views",0)>0)}


@router.get("/{campaign_id}")
def get_campaign(campaign_id: int, db=Depends(_db),
                 user=Depends(get_current_user)):
    allowed = _allowed_accounts(db, user)
    row = _campaign_or_404(db, campaign_id, allowed)
    data = CS.campaign_public(db, row)
    data["counts"] = CS.status_counts(db, campaign_id, allowed_accounts=allowed)
    return {"status": "ok", "campaign": data, "accounts": allowed}


@router.get("/{campaign_id}/items")
def get_items(campaign_id: int, account_id: str = "", status: str = "",
              hypothesis_id: str = "", search: str = "",
              limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
              db=Depends(_db), user=Depends(get_current_user)):
    allowed = _allowed_accounts(db, user)
    _campaign_or_404(db, campaign_id, allowed)
    if account_id and account_id not in allowed:
        raise HTTPException(status_code=403, detail="Нет доступа к аккаунту")
    rows, total = CS.list_items(db, campaign_id, account_id=account_id or None,
                                status=status or None,
                                hypothesis_id=hypothesis_id or None,
                                search=search, allowed_accounts=allowed,
                                limit=limit, offset=offset)
    return {"status": "ok", "total": total, "limit": limit, "offset": offset,
            "items": [CS.item_public(r) for r in rows],
            "counts": CS.status_counts(db, campaign_id, allowed_accounts=allowed)}


@router.post("/{campaign_id}/item")
def add_item(campaign_id: int, body: NewItemBody, db=Depends(_db),
             user=Depends(get_current_user)):
    allowed = _allowed_accounts(db, user)
    _campaign_or_404(db, campaign_id, allowed)
    if body.account_id not in allowed:
        raise HTTPException(status_code=403, detail="Нет доступа к аккаунту")
    row = CS.add_item(db, campaign_id, body.account_id, payload=body.payload,
                      source=(body.source or "manual"), segment=body.segment or None,
                      intent=body.intent or None, geo=body.geo or None,
                      offer=body.offer or None)
    db.commit()
    db.refresh(row)
    return {"status": "ok", "item": CS.item_public(row)}


@router.post("/{campaign_id}/item/{item_id}")
def patch_item(campaign_id: int, item_id: int, body: ItemPatchBody,
               db=Depends(_db), user=Depends(get_current_user)):
    """Правка ЛОКАЛЬНОГО черновика. Наружу ничего не уходит: публикация —
    отдельная операция через существующий confirmation gate."""
    allowed = _allowed_accounts(db, user)
    _campaign_or_404(db, campaign_id, allowed)
    try:
        row = CS.update_item(db, campaign_id, item_id, body.patch or {},
                             status=(body.status or None),
                             allowed_accounts=allowed)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if row is None:
        raise HTTPException(status_code=404, detail="Объявление не найдено")
    log_action(account_id=row.account_id, action="Изменил черновик объявления",
               object_kind="объявление",
               object_name=(json.loads(row.payload_json or "{}") or {}).get("title", "")[:120],
               reason="правка в конструкторе кампании",
               actor=ACTOR_USER, user_id=getattr(user, "id", None),
               source="campaign.item_patch")
    return {"status": "ok", "item": CS.item_public(row)}


class ImportExistingBody(BaseModel):
    account_id: str
    sources: list = []
    dry_run: bool = True


@router.post("/{campaign_id}/import_existing")
def import_existing(campaign_id: int, body: ImportExistingBody, db=Depends(_db),
                    user=Depends(get_current_user)):
    """Перенос существующих объявлений аккаунта (drafts / gen_ads / feed_items)
    в кампанию. Наружу ничего не уходит, storage не меняется. dry_run=True
    показывает, что будет перенесено, ничего не записывая."""
    from app.services import campaign_bridge as BR
    allowed = _allowed_accounts(db, user)
    _campaign_or_404(db, campaign_id, allowed)
    if body.account_id not in allowed:
        raise HTTPException(status_code=403, detail="Нет доступа к аккаунту")
    srcs = tuple(s for s in (body.sources or BR.SOURCES) if s in BR.SOURCES)
    report = BR.import_from_storage(db, campaign_id, body.account_id,
                                    sources=srcs, dry_run=bool(body.dry_run),
                                    user_id=getattr(user, "id", None))
    return {"status": "ok", "report": report,
            "counts": CS.status_counts(db, campaign_id, allowed_accounts=allowed)}

# Feed Factory: safe campaign workflow. These endpoints NEVER publish/upload.
class CampaignBriefBody(BaseModel):
    brief: str = ""; directions: list[str] = []; cities: list[str] = []; variants: int = 1
class CampaignApprovalBody(BaseModel):
    item_ids: list[int] = []; approved: bool = True; comment: str = ""

def _ff_items(db, campaign_id, allowed):
    from app.models.campaign_item import CampaignItem
    # Superseded rows are preserved for audit/history but are not part of the live
    # Feed Factory scope. This prevents an accidental second materialization from
    # doubling a production campaign and blocking validation/publication forever.
    return db.query(CampaignItem).filter(
        CampaignItem.campaign_id==campaign_id,
        CampaignItem.account_id.in_(list(allowed) or [""]),
        CampaignItem.status != "superseded",
    ).order_by(CampaignItem.id).all()
def _ff_payload(row):
    try: p=json.loads(row.payload_json or "{}")
    except Exception: p={}
    return p if isinstance(p,dict) else {}
def _ff_enforce_sales_copy_shape(description: str, topic: str, city: str, max_chars: int = 2400) -> str:
    """Free deterministic presentation repair after the paid copy call.

    It never adds business facts.  It only ensures the first block names the
    sold direction + geography and bounds an overlong answer at paragraph
    boundaries while retaining the closing CTA when present.
    """
    import re as _re
    desc=str(description or "").strip()
    topic=str(topic or "").strip(); city=str(city or "").strip()
    paras=[x.strip() for x in _re.split(r"\n\s*\n",desc) if x.strip()]
    first=(paras[0] if paras else desc)[:260].lower()
    need_topic=bool(topic) and not any(w[:4] in first for w in _re.findall(r"[а-яёa-z0-9]+",topic.lower()) if len(w)>=4)
    city_words=[w for w in _re.findall(r"[а-яёa-z0-9]+",city.lower()) if len(w)>=4]
    need_city=bool(city_words) and not all(w[:4] in first for w in city_words[:3])
    if need_topic or need_city:
        subject=topic or "Предложение"
        lead=(subject + ((" в "+city) if city else "") + " — напишите в сообщения Avito, чтобы обсудить задачу.").strip()
        paras=[lead]+paras
    desc="\n\n".join(paras).strip()
    if len(desc) <= max_chars:
        return desc
    # Keep whole paragraphs. Prefer retaining the last action-oriented paragraph
    # rather than cutting a sentence in the middle.
    final_cta=""
    for par in reversed(paras):
        if any(x in par.lower() for x in ("напиш", "сообщ", "обрат", "звон")):
            final_cta=par; break
    kept=[]; budget=max_chars-(len(final_cta)+2 if final_cta else 0)
    for par in paras:
        if par==final_cta and final_cta:
            continue
        add=len(par)+(2 if kept else 0)
        if kept and sum(len(x) for x in kept)+2*(len(kept)-1)+add>budget:
            break
        kept.append(par)
    if final_cta and final_cta not in kept:
        kept.append(final_cta)
    return "\n\n".join(kept).strip()[:max_chars].rstrip()


def _ff_append_uniqueness_footer(description: str, topic: str, city: str, variant: int = 1, item_id: int = 0, max_chars: int = 2400) -> str:
    """Guarantee a visible, factual per-card example at the bottom of Feed Factory copy.

    The footer uses only the direction and geography already present in the card.
    Its wording rotates deterministically across variants/items, so mass campaigns
    do not fake uniqueness by changing one adjective while preserving the same tail.
    """
    import re as _re
    desc=str(description or "").strip(); topic=str(topic or "").strip(); city=str(city or "").strip()
    if not topic:
        return desc[:max_chars].rstrip()
    # Avoid naive Russian case inflection (e.g. «в Уфа»). A neutral separator
    # keeps arbitrary city names grammatically correct without inventing forms.
    where=(" · "+city) if city else ""
    labels=["Пример задачи","Конкретный сценарий","Пример обращения","Сценарий клиента","Пример запроса"]
    leads=[
        "Опишите исходную ситуацию", "Напишите, что уже известно по задаче",
        "Пришлите исходные данные", "Укажите ключевые вводные",
        "Расскажите о вашей ситуации своими словами", "Опишите текущую задачу",
        "Сообщите, с чего начинаем", "Напишите основные вводные",
        "Опишите, что нужно решить", "Расскажите, какой результат нужен",
    ]
    closes=[
        "и желаемый результат — так ответ будет точнее.",
        "и что важно получить в итоге.",
        "— обсудим подходящий вариант.",
        "— сразу перейдём к предметному обсуждению.",
        "— уточним только действительно важные детали.",
        "и приоритет результата — предложим следующий шаг.",
        "и ожидаемый итог — разберём задачу по существу.",
        "— ответим по конкретной ситуации без общих обещаний.",
        "и цель обращения — так будет проще начать с главного.",
        "— дальше уточним только то, что влияет на решение.",
        "и что для вас важнее всего в результате.",
        "— подготовим ответ именно по вашей ситуации.",
        "и желаемый итог — без лишних общих вопросов.",
        "— разберём вводные и следующий практический шаг.",
        "и главный приоритет — это поможет ответить предметно.",
        "— начнём с фактов, которые уже известны.",
        "и ожидаемый результат — остальное уточним по необходимости.",
        "— так обсуждение сразу будет привязано к вашей задаче.",
        "и что уже пробовали — если это важно для результата.",
        "— достаточно тех фактов, которые вы уже знаете.",
    ]
    # Up to 1000 deterministic formulations per same topic+city. The offset is
    # topic/geo-specific, while variant progression is collision-free for variants
    # 1..1000. Different topics are already semantically distinct by construction.
    import zlib as _zlib
    base=_zlib.crc32((topic+"|"+city).encode("utf-8")) % 1000
    serial=(base+max(0,int(variant or 1)-1)) % 1000
    label=labels[serial % len(labels)]
    lead=leads[(serial // len(labels)) % len(leads)]
    close=closes[(serial // (len(labels)*len(leads))) % len(closes)]
    footer=f"✦ {label}: {topic}{where}. {lead} {close}"
    # Do not duplicate a model-written explicit footer; normalize its location instead.
    tail=desc[-700:].lower()
    if any(x in tail for x in ("✦ пример задачи:","✦ конкретный сценарий:","✦ пример обращения:","✦ сценарий клиента:","✦ пример запроса:")):
        return desc[:max_chars].rstrip()
    budget=max(200,max_chars-len(footer)-2)
    if len(desc)>budget:
        cut=desc[:budget]
        para=cut.rfind("\n\n")
        if para>=max(400,int(budget*0.68)):
            cut=cut[:para]
        else:
            stop=max(cut.rfind(". "),cut.rfind(".\n"),cut.rfind("! "),cut.rfind("? "))
            if stop>=max(400,int(budget*0.68)):
                cut=cut[:stop+1]
        desc=cut.rstrip()
    return (desc+"\n\n"+footer).strip()[:max_chars].rstrip()


def _ff_copy_quality_errors(payload: dict) -> list[dict]:
    """Hard acceptance gate for texts generated by the current Feed Factory copywriter.
    It validates structure only and never invents facts. Legacy/imported copy is not
    retroactively blocked unless it was explicitly generated under this contract.
    """
    import re as _re
    contract=str(payload.get("copy_quality_contract") or "")
    if contract not in {"sales_v2","sales_v3"}:
        return []
    title=str(payload.get("title") or "").strip()
    desc=str(payload.get("description") or "").strip()
    out=[]
    if len(desc) < 650:
        out.append({"field":"description","code":"copy_too_weak","message":"Текст слишком короткий для продающего стандарта BORIS: нужно минимум 650 символов"})
    paras=[x.strip() for x in _re.split(r"\n\s*\n|\n",desc) if x.strip()]
    if len(paras) < 4:
        out.append({"field":"description","code":"copy_structure_weak","message":"Нужно минимум 4 смысловых блока: оффер, ценность, доказательства/снятие возражений, призыв"})
    low=desc.lower()
    cta_hits=sum(1 for x in ("напиш","звон","сообщ","остав","пришл","обрат") if x in low)
    if cta_hits < 1:
        out.append({"field":"description","code":"copy_cta_missing","message":"В продающем тексте нет понятного призыва к действию"})
    first=(paras[0] if paras else desc)[:260].lower()
    city=str(payload.get("city") or payload.get("address") or "").strip().lower()
    direction=str(payload.get("direction") or "").strip().lower()
    if city:
        city_words=[w for w in _re.findall(r"[а-яёa-z0-9]+",city) if len(w)>=3]
        # Russian city names are normally inflected in ad copy (Уфа→Уфе,
        # Москва→Москве). Match a conservative lexical stem instead of demanding
        # the nominative spelling from payload.address/city.
        def _city_stem(w):
            if len(w)<=3: return w[:2]
            return w[:3] if len(w)==4 else w[:4]
        city_ok=bool(city_words) and all(_city_stem(w) in first for w in city_words[:3])
        if not city_ok:
            out.append({"field":"description","code":"copy_lead_city_missing","message":"В первом продающем блоке нет географии объявления"})
    if direction:
        words=[w for w in _re.findall(r"[а-яёa-z0-9]+",direction) if len(w)>=4]
        if words and not any(w[:4] in first for w in words[:5]):
            out.append({"field":"description","code":"copy_lead_service_missing","message":"Первый продающий блок не называет услугу/направление"})
    if contract=="sales_v3":
        footer=dict(payload.get("uniqueness_footer") or {})
        tail=desc[-650:].lower()
        visible_footer=any(x in tail for x in ("✦ пример задачи:","✦ конкретный сценарий:","✦ пример обращения:","✦ сценарий клиента:","✦ пример запроса:"))
        if not (footer.get("present") is True and str(footer.get("position") or "")=="bottom" and visible_footer):
            out.append({"field":"description","code":"copy_uniqueness_footer_missing","message":"Внизу описания нет отдельного уникализированного примера задачи для этой карточки"})
    if not title or len(title)>50:
        out.append({"field":"title","code":"copy_title_invalid","message":"Заголовок должен быть конкретным и не длиннее 50 символов"})
    return out


def _ff_contract_with_payload_overlay(db, payload, base_contract=None, account_id: str=""):
    """Merge campaign/item-scoped live Avito field knowledge without mutating a legacy global template cache.

    If the exact template exists in this account's real saved feed but the global
    CategoryTemplate cache has no parsed fields yet, treat the observed parameter
    names as an account-scoped compatibility schema. We do NOT guess requiredness
    or enum values: official xmlcheck remains authoritative and can promote missing
    required fields later. This only prevents a known-good client feed from being
    erased or rejected as `unknown_category_parameter` on the first new card.
    """
    from app.api.feed_contract import resolve_feed_contract, SYSTEM_FIELDS
    p=payload or {}; tid=str(p.get("template_id") or "").strip()
    contract=dict(base_contract or resolve_feed_contract(tid,_db=db))
    fields=dict(contract.get("fields") or {})
    observed_account=str(account_id or p.get("account_id") or "").strip()
    if contract.get("ok") and tid and observed_account and not fields:
        try:
            from app.models.storage import Storage
            _row=db.query(Storage).filter(Storage.account_id==observed_account,Storage.key=="feed_items").first()
            _items=json.loads(_row.value or "[]") if _row else []
            _observed={}
            for _it in _items if isinstance(_items,list) else []:
                if not isinstance(_it,dict) or str(_it.get("template_id") or "").strip()!=tid: continue
                for _name,_value in dict(_it.get("params") or {}).items():
                    if not _name or _name in SYSTEM_FIELDS or _value in (None,""): continue
                    _observed.setdefault(str(_name),[]).append(_value)
            for _name,_values in _observed.items():
                fields[_name]={"required":False,"allowed":[],"format_rule":"","name_ru":_name,"depends_on":"","multiple":any(isinstance(v,(list,tuple,set)) for v in _values),"source":"existing_account_feed"}
            if _observed:
                import hashlib as _obs_hash
                _basis=json.dumps({"template_id":tid,"account_id":observed_account,"fields":sorted(_observed)},ensure_ascii=False,sort_keys=True)
                contract["contract_version"]="observed-"+_obs_hash.sha256(_basis.encode("utf-8")).hexdigest()[:12]
                contract["observed_feed_fields"]=len(_observed)
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=1208)
    overlay=p.get("live_contract_overlay") or {}
    for name,spec in dict(overlay.get("fields") or {}).items():
        if not name or not isinstance(spec,dict): continue
        old=dict(fields.get(name) or {}); old.update(spec); fields[name]=old
    contract["fields"]=fields
    if overlay.get("contract_version"):
        contract["contract_version"]=str(overlay.get("contract_version"))
    # Schema drift must stay campaign-scoped. The overlay can carry the live
    # official Avito path while the global template keeps serving legacy cards.
    if overlay.get("official_path"):
        contract["category_path"]=str(overlay.get("official_path"))
    if overlay.get("node_slug"):
        contract["node_slug"]=str(overlay.get("node_slug"))
    return contract


def _ff_autosanitize_explicit_negative_claims(description: str, brief: str) -> tuple[str, list[str]]:
    """Remove only claims that an owner brief explicitly forbids.

    FEED_BRIEF_NEGATIVE_FACT_AUTOSANITIZE_V1: local/free repair, no AI call and no
    new facts. It removes the whole textual line containing a forbidden guarantee
    or minimum-order/check claim; losing a nearby benefit is safer than preserving
    a prohibited commercial promise. Validation remains the independent backstop.
    """
    import re as _r
    desc=str(description or ""); b=str(brief or "").casefold(); removed=[]
    no_guarantee=bool(_r.search(r"(?:гарант\w*[^\n.;]{0,55}(?:не\s+обещ|не\s+указыв|не\s+добав|не\s+придум|не\s+выдум)|(?:не\s+обещ|не\s+указыв|не\s+добав|не\s+придум|не\s+выдум)[^\n.;]{0,55}гарант\w*)",b))
    no_minimum=bool(_r.search(r"(?:(?:минимальн\w*\s+(?:заказ|чек))[^\n.;]{0,55}(?:не\s+обещ|не\s+указыв|не\s+добав|не\s+придум|не\s+выдум)|(?:не\s+обещ|не\s+указыв|не\s+добав|не\s+придум|не\s+выдум)[^\n.;]{0,55}(?:минимальн\w*\s+(?:заказ|чек)))",b))
    kept=[]
    for line in desc.splitlines():
        low=line.casefold(); forbidden=False
        if no_guarantee and _r.search(r"\bгарант\w*\b",low): forbidden=True
        if no_minimum and _r.search(r"\bминимальн\w*\s+(?:заказ|чек)\b",low): forbidden=True
        if forbidden: removed.append(line); continue
        kept.append(line)
    cleaned=_r.sub(r"\n{3,}","\n\n","\n".join(kept)).strip()
    return cleaned,removed


def _ff_brief_negative_claim_errors(db, row, payload):
    """Deterministic contradiction guard for explicit owner prohibitions.

    FEED_BRIEF_NEGATIVE_FACT_GUARD_V1: prompts such as «гарантию не обещать» or
    «минимальный заказ не обещать» are policy, not copywriting suggestions. A
    generated description that contradicts them can never become READY/exportable.
    The guard is deliberately narrow and triggers only on explicit negative rules.
    """
    import re as _r
    try:
        from app.models.campaign import Campaign as _NegCampaign
        campaign=db.query(_NegCampaign).filter(_NegCampaign.id==int(row.campaign_id)).first()
        _,ff=_ff_state_load(campaign) if campaign is not None else (None,{})
    except Exception:
        ff={}
    brief=str((ff or {}).get("brief") or "")
    desc=str((payload or {}).get("description") or "")
    if not brief or not desc:
        return []
    b=brief.casefold(); d=desc.casefold(); errors=[]
    # Explicit negative guarantee rule: support common Russian forms and a
    # nearby negation, while not treating generic «не выдумывай факты» as a
    # guarantee-specific prohibition unless guarantee is named.
    no_guarantee=bool(_r.search(r"(?:гарант\w*[^\n.;]{0,55}(?:не\s+обещ|не\s+указыв|не\s+добав|не\s+придум|не\s+выдум)|(?:не\s+обещ|не\s+указыв|не\s+добав|не\s+придум|не\s+выдум)[^\n.;]{0,55}гарант\w*)",b))
    if no_guarantee and _r.search(r"\bгарант\w*\b",d):
        errors.append({"field":"description","code":"brief_forbids_guarantee_claim","message":"В задаче прямо запрещено обещать гарантию, но текст содержит гарантию"})
    # Minimum order/check are one commercial boundary. Trigger only when the
    # brief explicitly says not to promise/name it.
    no_minimum=bool(_r.search(r"(?:(?:минимальн\w*\s+(?:заказ|чек))[^\n.;]{0,55}(?:не\s+обещ|не\s+указыв|не\s+добав|не\s+придум|не\s+выдум)|(?:не\s+обещ|не\s+указыв|не\s+добав|не\s+придум|не\s+выдум)[^\n.;]{0,55}(?:минимальн\w*\s+(?:заказ|чек)))",b))
    if no_minimum and _r.search(r"\bминимальн\w*\s+(?:заказ|чек)\b",d):
        errors.append({"field":"description","code":"brief_forbids_minimum_order_claim","message":"В задаче прямо запрещено обещать минимальный заказ/чек, но текст содержит такое условие"})
    return errors


def _ff_validate_row(row, db):
    from app.api.feed_contract import resolve_feed_contract, validate_feed_item
    p=_ff_payload(row); errors=[]; warnings=[]
    errors.extend(_ff_copy_quality_errors(p))
    errors.extend(_ff_brief_negative_claim_errors(db,row,p))
    for key,label in (("title","Заголовок"),("description","Описание"),("address","Адрес"),("category","Категория")):
        if not str(p.get(key) or "").strip(): errors.append({"field":key,"code":"required","message":f"Не заполнено: {label}"})
    try:
        if int(p.get("price") or 0)<0: errors.append({"field":"price","code":"price_invalid","message":"Цена не может быть отрицательной"})
    except Exception: errors.append({"field":"price","code":"price_invalid","message":"Цена должна быть числом"})
    images=list(p.get("images") or [])
    if not images: errors.append({"field":"images","code":"images_missing","message":"Не добавлено ни одного изображения"})
    if len(set(images))!=len(images): warnings.append({"field":"images","code":"images_duplicate","message":"Есть повторяющиеся изображения"})
    # Preserve the exact gallery contract selected by the workflow. If semantic
    # auto-curation removes a bad photo, one surviving photo is not "good enough"
    # when the task asked for three. Validation must force the worker back to media.
    _stack_ids=[int(x) for x in ((p.get("photo_stack") or {}).get("media_ids") or []) if str(x).isdigit()]
    if _stack_ids:
        try:
            from app.models.media_link import MediaLink as _ValMediaLink
            from app.models.media_asset import MediaAsset as _ValMediaAsset
            _pairs=(db.query(_ValMediaLink,_ValMediaAsset).join(_ValMediaAsset,_ValMediaAsset.id==_ValMediaLink.media_id)
                    .filter(_ValMediaLink.entity_type=="campaign_item",_ValMediaLink.entity_id==str(row.id),_ValMediaAsset.deleted_at.is_(None)).all())
            # photo_stack is the canonical selected gallery. Legacy campaigns can
            # legitimately retain extra linked candidate/generated assets for audit
            # and reuse; counting every historical link makes a complete stack look
            # invalid. Validate that every canonical stack id is still linked and is
            # not a banner, while ignoring extra non-canonical links.
            _linked_photo_ids=set()
            for _lnk,_asset in _pairs:
                _provider=str(_asset.source_provider or "").lower(); _ref=str(_asset.source_ref or "")
                _is_banner=(_provider in {"openai","boris_local"} and str(_lnk.role or "")=="hero") or _ref in {"campaign_openai_gpt_image_2","campaign_openai_visual_local_text_v1","campaign_stock_visual_local_text_v1"}
                if not _is_banner:
                    _linked_photo_ids.add(int(_asset.id))
            _present=sum(1 for _mid in _stack_ids if _mid in _linked_photo_ids)
            if _present!=len(_stack_ids):
                errors.append({"field":"images","code":"photo_stack_incomplete","expected":len(_stack_ids),"actual":_present,"message":f"Нужно {len(_stack_ids)} проверенных фотографий, сейчас осталось {_present}"})
        except Exception:
            errors.append({"field":"images","code":"photo_stack_check_failed","message":"Не удалось подтвердить полный фотостек объявления"})
    import os as _os
    for image in images:
        url=str(image or "")
        if "/images/" in url:
            rel=url.split("/images/",1)[1].split("?",1)[0]
            if rel and not _os.path.isfile(_os.path.join("/root/BORIS/backend/images",rel)):
                errors.append({"field":"images","code":"image_missing","message":"Файл изображения отсутствует: "+rel})
    title=str(p.get("title") or "").strip(); desc=str(p.get("description") or "").strip()
    # Money is never sourced from the copy model. Block approval/export if the
    # visible listing-price claim is absent from canonical data or disagrees with
    # payload.price. Secondary sums such as «монтаж 1500 ₽» are intentionally ignored.
    _copy_price_claims=_ff_explicit_copy_price_claims(desc)
    try: _canonical_price=int(p.get("price") or 0)
    except Exception: _canonical_price=0
    # This contract belongs to the current Feed Factory/sales_v3 pipeline. Do not
    # retroactively reinterpret thousands of legacy AUTO campaign descriptions,
    # where a phrase such as «стоимость от 6000» can intentionally describe a
    # company-wide range while the individual legacy card carries another price.
    _strict_price_contract=bool(p.get("feed_factory")) or str(p.get("copy_quality_contract") or "") in {"sales_v3"}
    if _strict_price_contract and _copy_price_claims:
        if _canonical_price<=0:
            errors.append({"field":"description","code":"copy_price_unconfirmed","claims":sorted(set(_copy_price_claims)),"message":"В тексте указана цена объявления, но подтверждённая цена карточки отсутствует"})
        else:
            _wrong_prices=sorted({int(x) for x in _copy_price_claims if int(x)!=_canonical_price})
            if _wrong_prices:
                errors.append({"field":"description","code":"copy_price_mismatch","expected":_canonical_price,"actual":_wrong_prices,"message":"Цена в тексте не совпадает с подтверждённой ценой карточки"})
    import re as _title_re
    _tm=_title_re.search(r"\{([^{}]+)\}",title); _title_variants=[x.strip() for x in (_tm.group(1).split("|") if _tm else [title]) if x.strip()]
    if any(len(x)>45 for x in _title_variants): warnings.append({"field":"title","code":"title_long","message":"Один из вариантов заголовка длиннее 45 символов"})
    if desc and len(desc)<120: warnings.append({"field":"description","code":"description_short","message":"Описание короче 120 символов"})
    if desc and not any(x in desc.lower() for x in ("звон","пиш","сообщ","остав")): warnings.append({"field":"description","code":"cta_missing","message":"Не найден явный призыв к действию"})
    tid=str(p.get("template_id") or "").strip(); contract=_ff_contract_with_payload_overlay(db,p,resolve_feed_contract(tid,_db=db),account_id=str(getattr(row,"account_id","") or ""))
    # A template id and its official category path are one identity.  Never let
    # a stale/parallel workflow silently pair a valid template with a different
    # human path: that can look plausible in UI while exporting the wrong Avito
    # hierarchy.  Block validation until the payload is canonicalized.
    if tid and contract.get("ok"):
        _contract_path=str(contract.get("category_path") or "").strip()
        _payload_path=str(p.get("category_path") or "").strip()
        if _contract_path and _payload_path and _payload_path != _contract_path:
            errors.append({"field":"category_path","code":"category_contract_mismatch","template_id":tid,"expected":_contract_path,"actual":_payload_path,"message":"Категория объявления не соответствует выбранному шаблону Avito"})
    contract_params=dict(p.get("params") or {})
    # Canonical payload keeps common fields in readable snake_case, while the
    # Avito category contract uses XML names. Normalize without duplicating state.
    if p.get("ad_type") and not contract_params.get("AdType"): contract_params["AdType"]=p.get("ad_type")
    if p.get("condition") and not contract_params.get("Condition"): contract_params["Condition"]=p.get("condition")
    cr=validate_feed_item({"id":str(row.id),"template_id":tid,"params":contract_params},contract)
    errors.extend(cr.get("errors") or [])
    from app.services.media_semantic_validator import validate_linked_media_semantics
    errors.extend(validate_linked_media_semantics(db,row,p))
    return {"id":row.id,"account_id":row.account_id,"status":row.status,"valid":not errors,"errors":errors,"warnings":warnings,"contract_version":cr.get("contract_version","")}

@router.post("/{campaign_id}/brief/plan")
def campaign_brief_plan(campaign_id:int, body:CampaignBriefBody, db=Depends(_db), user=Depends(get_current_user)):
    allowed=_allowed_accounts(db,user); _campaign_or_404(db,campaign_id,allowed); import re
    dirs=[str(x).strip() for x in body.directions if str(x).strip()]; cities=[str(x).strip() for x in body.cities if str(x).strip()]
    if not dirs: dirs=[x.strip(" -•\t") for x in re.split(r"[,;\n]+",body.brief or "") if x.strip(" -•\t")][:50]
    variants=max(1,min(int(body.variants or 1),10)); geo=cities or [""]
    matrix=[{"direction":d,"city":c,"variant":v+1} for d in dirs for c in geo for v in range(variants)]
    return {"status":"ok","directions":dirs,"cities":cities,"variants":variants,"items_planned":len(matrix),"matrix":matrix[:1000],"warnings":(["Укажите города, если тексты или цены должны различаться по гео"] if not cities else [])}

def _ff_uniqueness_required(payload: dict) -> bool:
    """Only current/explicit uniqueness contracts are hard-blocked.

    This avoids retroactively breaking legacy imported cards while making it
    impossible for Feed Factory to mark a new card "unique" by metadata alone.
    """
    p=payload or {}
    return bool(
        str(p.get("copy_quality_contract") or "") in {"sales_v2","sales_v3"}
        or str(p.get("unique_variant") or "").strip()
        or str(p.get("uniqueness_strategy") or "").strip()
    )


def _ff_similarity_text(value: str) -> str:
    """Normalize the selling body and ignore cosmetic uniqueness footers."""
    import re as _re
    s=str(value or "").lower().replace("ё","е")
    s=_re.sub(
        r"(?ims)\n\s*(?:✦\s*)?(?:пример задачи|конкретный сценарий|пример обращения|сценарий клиента|пример запроса|пример услуги)\s*:.*$",
        "",
        s,
    )
    s=_re.sub(r"<[^>]+>"," ",s)
    s=_re.sub(r"https?://\S+"," ",s)
    return " ".join(_re.findall(r"[а-яa-z0-9]+",s))


def _ff_near_duplicate_pairs(rows, threshold: float = 0.86, limit: int = 200) -> list[dict]:
    """Detect copied selling bodies even when city/one phrase/footer differs."""
    import difflib as _difflib
    groups={}
    for row in (rows or []):
        p=_ff_payload(row)
        if not _ff_uniqueness_required(p):
            continue
        desc=_ff_similarity_text(p.get("description") or "")
        if len(desc)<180:
            continue
        family=str(
            p.get("direction") or getattr(row,"segment",None)
            or p.get("category_path") or p.get("category") or ""
        ).strip().lower().replace("ё","е")
        if not family:
            family="__unknown__"
        groups.setdefault((str(getattr(row,"account_id","") or ""),family),[]).append((row,desc))

    found=[]
    for _group, entries in groups.items():
        for i in range(len(entries)):
            ra,a=entries[i]
            for j in range(i+1,len(entries)):
                rb,b=entries[j]
                length_ratio=min(len(a),len(b))/max(len(a),len(b))
                if length_ratio < 0.72:
                    continue
                sm=_difflib.SequenceMatcher(None,a,b,autojunk=False)
                if sm.quick_ratio() < max(0.72,threshold-0.08):
                    continue
                ratio=sm.ratio()
                if ratio >= threshold:
                    found.append({
                        "a":int(ra.id),"b":int(rb.id),
                        "ratio":round(float(ratio),4),
                        "family":_group[1],
                    })
                    if len(found)>=limit:
                        return found
    return found


@router.get("/{campaign_id}/validation")
def campaign_validation(campaign_id:int, db=Depends(_db), user=Depends(get_current_user)):
    allowed=_allowed_accounts(db,user); _campaign_or_404(db,campaign_id,allowed); rows=_ff_items(db,campaign_id,allowed); reports=[_ff_validate_row(r,db) for r in rows]
    seen={}; dup=[]; lead_seen={}; lead_dup=[]; desc_seen={}; desc_dup=[]; near_dup=_ff_near_duplicate_pairs(rows)
    import re as _copy_re
    for r in rows:
        p=_ff_payload(r); key=(str(p.get("title") or "").strip().lower(),str(p.get("description") or "").strip().lower())
        if key!=("",""):
            if key in seen: dup.append([seen[key],r.id])
            else: seen[key]=r.id
        if str(p.get("copy_quality_contract") or "") in {"sales_v2","sales_v3"}:
            _desc_norm=" ".join(_copy_re.findall(r"[а-яёa-z0-9]+",str(p.get("description") or "").lower()))
            if len(_desc_norm)>=80:
                if _desc_norm in desc_seen: desc_dup.append([desc_seen[_desc_norm],r.id])
                else: desc_seen[_desc_norm]=r.id
            first=(str(p.get("description") or "").strip().split("\n",1)[0])
            lead_key=" ".join(_copy_re.findall(r"[а-яёa-z0-9]+",first.lower()))[:220]
            if len(lead_key)>=30:
                if lead_key in lead_seen: lead_dup.append([lead_seen[lead_key],r.id])
                else: lead_seen[lead_key]=r.id
    report_by_id={int(x["id"]):x for x in reports}
    for pair in dup:
        strict_pair=any(str(_ff_payload(r).get("copy_quality_contract") or "") in {"sales_v2","sales_v3"} for r in rows if r.id in pair)
        for rid in pair:
            rr=report_by_id.get(int(rid))
            if not rr: continue
            issue={"field":"content","code":"duplicate_exact","message":"Полностью совпадает с другим объявлением кампании"}
            if strict_pair:
                rr["errors"].append(issue); rr["valid"]=False
            else:
                rr["warnings"].append(issue)
    for pair in desc_dup:
        for rid in pair:
            rr=report_by_id.get(int(rid))
            if rr:
                rr["errors"].append({"field":"description","code":"duplicate_description","message":"Основной текст совпадает с другим объявлением кампании — нужен уникальный вариант"})
                rr["valid"]=False
    for pair in lead_dup:
        for rid in pair:
            rr=report_by_id.get(int(rid))
            if rr:
                rr["errors"].append({"field":"description","code":"duplicate_lead","message":"Первый продающий блок повторяется в другом объявлении — нужна реальная уникализация подачи"})
                rr["valid"]=False
    for pair in near_dup:
        for rid,other in ((pair["a"],pair["b"]),(pair["b"],pair["a"])):
            rr=report_by_id.get(int(rid))
            if rr:
                rr["errors"].append({
                    "field":"description",
                    "code":"near_duplicate_description",
                    "message":"Основная часть текста слишком похожа на другое объявление — косметическая замена города/фразы не считается уникализацией",
                    "other_item_id":int(other),
                    "similarity":pair["ratio"],
                })
                rr["valid"]=False
    ready=sum(1 for r in reports if r["valid"])
    return {"status":"ok","ok":ready==len(reports) and bool(reports),"total":len(reports),"ready":ready,"blocked":len(reports)-ready,"duplicate_pairs":dup[:100],"duplicate_description_pairs":desc_dup[:100],"near_duplicate_description_pairs":near_dup[:100],"items":reports}


def _ff_restore_exact_media_links_from_payload(row, db, created_by=None):
    """Restore lost MediaLink rows from already-registered exact /images URLs.

    Deterministic/local only: no media generation and no Vision call. Recovery is
    allowed only when there are currently no links and every payload image maps
    to exactly one live MediaAsset of the same account.
    """
    from app.models.media_asset import MediaAsset
    from app.models.media_link import MediaLink
    from app.services import media_service as M

    existing=(db.query(MediaLink)
              .filter(MediaLink.entity_type=="campaign_item",
                      MediaLink.entity_id==str(row.id)).count())
    if existing:
        return {"status":"already_linked","restored":0,"expected":existing}

    p=_ff_payload(row)
    urls=[str(x or "").strip() for x in (p.get("images") or []) if str(x or "").strip()]
    if not urls:
        return {"status":"no_payload_images","restored":0,"expected":0}

    resolved=[]; missing=[]; ambiguous=[]
    for pos,url in enumerate(urls):
        if "/images/" not in url:
            missing.append({"position":pos,"url":url[:240],"reason":"not_local_images_url"})
            continue
        rel=url.split("/images/",1)[1].split("?",1)[0].lstrip("/")
        matches=(db.query(MediaAsset)
                 .filter(MediaAsset.account_id==str(row.account_id),
                         MediaAsset.storage_key==rel,
                         MediaAsset.deleted_at.is_(None)).all())
        if len(matches)==1:
            resolved.append((pos,matches[0],url))
        elif not matches:
            missing.append({"position":pos,"storage_key":rel,"reason":"asset_not_found"})
        else:
            ambiguous.append({"position":pos,"storage_key":rel,
                              "media_ids":[int(x.id) for x in matches[:10]]})

    if missing or ambiguous or len(resolved)!=len(urls):
        return {"status":"not_proven","restored":0,"expected":len(urls),
                "resolved":len(resolved),"missing":missing,"ambiguous":ambiguous}

    for pos,asset,_url in resolved:
        role="hero" if (
            (getattr(row,"hero_media_id",None) and int(row.hero_media_id)==int(asset.id))
            or (not getattr(row,"hero_media_id",None) and pos==0)
        ) else "gallery"
        M.link(db,asset,"campaign_item",str(row.id),role,pos,created_by=created_by)
    db.flush()
    return {"status":"restored","restored":len(resolved),"expected":len(urls),
            "media_ids":[int(a.id) for _,a,_ in resolved]}


def _ff_cross_campaign_duplicate_conflicts(db, campaign_id:int, rows:list) -> dict:
    """Detect exact business-content duplicates against other published identities.

    CROSS_CAMPAIGN_ACTIVE_DUPLICATE_PREFLIGHT_V1
    Normal new campaigns must not create a second feed identity with the same
    title+description+price+geo as an already published item in this account.
    Same feed identity is allowed (normal update); explicit replacement uses the
    separate guarded cutover workflow.
    """
    from app.models.campaign_item import CampaignItem
    def _norm(v):
        return " ".join(str(v or "").strip().lower().split())
    def _key(row):
        p=_ff_payload(row)
        return (_norm(p.get("title")),_norm(p.get("description")),
                _norm(p.get("price")),_norm(getattr(row,"geo",None) or p.get("city") or p.get("address")))
    scoped=[r for r in (rows or []) if int(getattr(r,"campaign_id",0) or 0)==int(campaign_id)]
    if not scoped:
        return {}
    accounts=sorted({str(getattr(r,"account_id","") or "") for r in scoped if str(getattr(r,"account_id","") or "")})
    if not accounts:
        return {}
    existing=db.query(CampaignItem).filter(
        CampaignItem.account_id.in_(accounts),
        CampaignItem.campaign_id != int(campaign_id),
        CampaignItem.identity_status == "published_identity_bound",
        CampaignItem.avito_item_id.isnot(None),
        CampaignItem.status != "superseded",
    ).all()
    by_key={}
    for old in existing:
        k=_key(old)
        if not k[0] or not k[1]:
            continue
        by_key.setdefault(k,[]).append(old)
    conflicts={}
    for cur in scoped:
        k=_key(cur)
        if not k[0] or not k[1]:
            continue
        cur_feed=str(getattr(cur,"feed_identity","") or "").strip()
        matches=[]
        for old in by_key.get(k,[]):
            old_feed=str(getattr(old,"feed_identity","") or "").strip()
            if cur_feed and old_feed and cur_feed==old_feed:
                continue
            matches.append({"campaign_id":int(old.campaign_id),"item_id":int(old.id),
                            "feed_identity":old_feed or None,"avito_item_id":str(old.avito_item_id or "") or None})
        if matches:
            conflicts[int(cur.id)]=matches[:10]
    return conflicts


def _ff_cross_campaign_near_duplicate_conflicts(db, campaign_id:int, rows:list, threshold:float=0.86) -> dict:
    import difflib as _difflib
    from app.models.campaign_item import CampaignItem
    scoped=[r for r in (rows or []) if int(getattr(r,"campaign_id",0) or 0)==int(campaign_id) and _ff_uniqueness_required(_ff_payload(r))]
    if not scoped:
        return {}
    accounts=sorted({str(getattr(r,"account_id","") or "") for r in scoped if str(getattr(r,"account_id","") or "")})
    if not accounts:
        return {}
    existing=db.query(CampaignItem).filter(
        CampaignItem.account_id.in_(accounts),
        CampaignItem.campaign_id != int(campaign_id),
        CampaignItem.identity_status == "published_identity_bound",
        CampaignItem.avito_item_id.isnot(None),
        CampaignItem.status != "superseded",
    ).all()
    def _family(row):
        p=_ff_payload(row)
        return str(p.get("direction") or getattr(row,"segment",None) or p.get("category_path") or p.get("category") or "").strip().lower().replace("ё","е")
    old_by_group={}
    for old in existing:
        desc=_ff_similarity_text(_ff_payload(old).get("description") or "")
        fam=_family(old)
        if len(desc)>=180 and fam:
            old_by_group.setdefault((str(old.account_id or ""),fam),[]).append((old,desc))
    conflicts={}
    for cur in scoped:
        a=_ff_similarity_text(_ff_payload(cur).get("description") or "")
        fam=_family(cur)
        if len(a)<180 or not fam:
            continue
        cur_feed=str(getattr(cur,"feed_identity","") or "").strip()
        matches=[]
        for old,b in old_by_group.get((str(cur.account_id or ""),fam),[]):
            old_feed=str(getattr(old,"feed_identity","") or "").strip()
            if cur_feed and old_feed and cur_feed==old_feed:
                continue
            if min(len(a),len(b))/max(len(a),len(b)) < 0.72:
                continue
            sm=_difflib.SequenceMatcher(None,a,b,autojunk=False)
            if sm.quick_ratio() < max(0.72,threshold-0.08):
                continue
            ratio=sm.ratio()
            if ratio >= threshold:
                matches.append({"campaign_id":int(old.campaign_id),"item_id":int(old.id),"feed_identity":old_feed or None,"avito_item_id":str(old.avito_item_id or "") or None,"similarity":round(float(ratio),4)})
        if matches:
            conflicts[int(cur.id)]=matches[:10]
    return conflicts


@router.post("/{campaign_id}/approval")
def campaign_approval(campaign_id:int, body:CampaignApprovalBody, db=Depends(_db), user=Depends(get_current_user)):
    allowed=_allowed_accounts(db,user); campaign=_campaign_or_404(db,campaign_id,allowed); rows=_ff_items(db,campaign_id,allowed); wanted=set(body.item_ids or [r.id for r in rows]); changed=0; blocked=[]; media_links_recovered=[]
    _ff_assert_mutable(campaign)
    cross_campaign_dupes=_ff_cross_campaign_duplicate_conflicts(db,campaign_id,rows) if body.approved else {}
    cross_campaign_near=_ff_cross_campaign_near_duplicate_conflicts(db,campaign_id,rows) if body.approved else {}
    near_pairs=_ff_near_duplicate_pairs(rows) if body.approved else []
    near_by_id={}
    for _pair in near_pairs:
        near_by_id.setdefault(int(_pair["a"]),[]).append(_pair)
        near_by_id.setdefault(int(_pair["b"]),[]).append(_pair)
    for r in rows:
        if r.id not in wanted: continue
        if body.approved:
            if int(r.id) in cross_campaign_dupes:
                blocked.append({"id":r.id,"errors":[{"field":"content","code":"cross_campaign_active_duplicate","message":"Такое же объявление уже опубликовано в этом Avito-аккаунте","matches":cross_campaign_dupes[int(r.id)]}]})
                continue
            if int(r.id) in cross_campaign_near:
                blocked.append({"id":r.id,"errors":[{
                    "field":"description",
                    "code":"cross_campaign_near_duplicate",
                    "message":"Основная часть текста почти повторяет уже опубликованное объявление этого Avito-аккаунта",
                    "matches":cross_campaign_near[int(r.id)],
                }]})
                continue
            if int(r.id) in near_by_id:
                blocked.append({"id":r.id,"errors":[{
                    "field":"description",
                    "code":"near_duplicate_description",
                    "message":"Основная часть текста слишком похожа на другое объявление кампании; согласование заблокировано до реальной уникализации",
                    "matches":near_by_id[int(r.id)][:10],
                }]})
                continue
            vr=_ff_validate_row(r,db)
            _codes={str(e.get("code") or "") for e in (vr.get("errors") or [])}
            if (not vr["valid"]) and _codes=={"media_semantic_no_links"}:
                healed=_ff_restore_exact_media_links_from_payload(r,db,created_by=getattr(user,"id",None))
                if healed.get("status")=="restored":
                    vr=_ff_validate_row(r,db)
                    media_links_recovered.append({"id":r.id,**healed})
            if not vr["valid"]: blocked.append({"id":r.id,"errors":vr["errors"]}); continue
            r.status="ready"
        else:
            r.status="draft"
            if body.comment.strip():
                p=_ff_payload(r); p["owner_review_comment"]=body.comment.strip(); r.payload_json=json.dumps(p,ensure_ascii=False)
        changed+=1
    db.commit()
    try:
        _,ff=_ff_state_load(campaign); ff["step"]="preview" if body.approved and changed else "validation"; _ff_state_save(db,campaign,ff)
    except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=1721)
    return {"status":"ok","changed":changed,"blocked":blocked,"media_links_recovered":media_links_recovered}

@router.get("/{campaign_id}/preview")
def campaign_preview(campaign_id:int, db=Depends(_db), user=Depends(get_current_user)):
    from app.models.media_asset import MediaAsset
    from app.models.media_link import MediaLink
    from app.services.media_service import asset_public
    allowed=_allowed_accounts(db,user); _campaign_or_404(db,campaign_id,allowed); out=[]
    for r in _ff_items(db,campaign_id,allowed):
        p=_ff_payload(r)
        links=(db.query(MediaLink,MediaAsset).join(MediaAsset,MediaAsset.id==MediaLink.media_id)
               .filter(MediaLink.entity_type=="campaign_item",MediaLink.entity_id==str(r.id),MediaAsset.deleted_at.is_(None))
               .order_by(MediaLink.position.asc(),MediaLink.id.asc()).all())
        gallery=[]
        for link,asset in links:
            pub=asset_public(asset); gallery.append({"id":asset.id,"role":link.role,"position":link.position,"url":pub.get("original_url") or pub.get("preview_url"),"preview_url":pub.get("preview_url") or pub.get("original_url"),"source_provider":asset.source_provider})
        images=[x.get("url") for x in gallery if x.get("url")] or (p.get("images") or [])
        out.append({"id":r.id,"account_id":r.account_id,"status":r.status,"external_id":r.external_item_id,"feed_identity":getattr(r,"feed_identity",None),"avito_item_id":getattr(r,"avito_item_id",None),"identity_status":getattr(r,"identity_status",None),"title":p.get("title","") ,"description":p.get("description","") ,"price":p.get("price",0),"price_unit":p.get("price_unit") or p.get("price_type") or "","price_benchmark_logic":p.get("price_benchmark_logic") or "","city":r.geo or p.get("city","") ,"direction":p.get("direction") or r.segment or "","category":p.get("category","") ,"template_id":p.get("template_id","") ,"attributes":p.get("params") or {},"images":images,"gallery":gallery,"banner":p.get("banner") or p.get("banner_url") or ""})
    return {"status":"ok","total":len(out),"items":out}


def _ff_xml(rows):
    import html
    parts=['<?xml version="1.0" encoding="UTF-8"?>','<Ads formatVersion="3" target="Avito.ru">']
    for r in rows:
        from app.services.campaign_identity import ensure_feed_identity
        p=_ff_payload(r); parts += ['  <Ad>',f'    <Id>{html.escape(str(ensure_feed_identity(r)))}</Id>']
        for tag,key in (("Title","title"),("Price","price"),("Category","category"),("Address","address"),("ContactPhone","phone"),("ManagerName","manager")):
            val=p.get(key)
            if tag == "Title" and val not in (None,"",0):
                val=_ff_clean_title_for_avito(str(val), str(p.get("direction") or r.segment or "Объявление"))
            if val not in (None,"",0): parts.append(f'    <{tag}>{html.escape(str(val))}</{tag}>')
        desc=str(p.get("description") or "").replace("]]>","]]]]><![CDATA[>"); parts.append(f'    <Description><![CDATA[{desc}]]></Description>')
        xml_params=dict(p.get("params") or {})
        if p.get("ad_type") and not xml_params.get("AdType"): xml_params["AdType"]=p.get("ad_type")
        if p.get("condition") and not xml_params.get("Condition"): xml_params["Condition"]=p.get("condition")
        # FF_XML_MULTIVALUE_OPTION_V1: Avito multi-select parameters are XML
        # containers with <Option> children. Serializing Python lists as "['x']"
        # passes local type checks but Avito treats the required field as empty.
        for k,v in xml_params.items():
            if not str(k).replace("_","").isalnum():
                continue
            if isinstance(v,(list,tuple,set)):
                vals=[x for x in v if x not in (None,"")]
                if vals:
                    parts.append(f'    <{k}>')
                    parts.extend(f'      <Option>{html.escape(str(x))}</Option>' for x in vals)
                    parts.append(f'    </{k}>')
            elif v not in (None,""):
                parts.append(f'    <{k}>{html.escape(str(v))}</{k}>')
        imgs=p.get("images") or []
        if imgs:
            normalized=[]
            for x in imgs:
                u=str(x or "").strip()
                if u.startswith("/images/"):
                    u="https://boris-ai.pro"+u
                normalized.append(u)
            parts += ['    <Images>']+[f'      <Image url="{html.escape(str(x),quote=True)}"/>' for x in normalized if x]+['    </Images>']
        parts.append('  </Ad>')
    parts.append('</Ads>'); return "\n".join(parts)

class FFOwnerActivationBody(BaseModel):
    account_id: str
    confirmed: bool = False


def _ff_activation_snapshot(db, campaign_id:int, allowed:list, account_id:str):
    rows = _ff_items(db, campaign_id, allowed)
    scoped = [r for r in rows if r.account_id == account_id]
    approved = [r for r in scoped if r.status == "ready"]
    accounts = sorted({r.account_id for r in rows})
    images = sum(len(_ff_payload(r).get("images") or []) for r in scoped)
    cities = sorted({str(r.geo or _ff_payload(r).get("city") or "").strip() for r in scoped if str(r.geo or _ff_payload(r).get("city") or "").strip()})
    return rows, scoped, approved, accounts, images, cities


def _ff_delivery_projection(scoped, db=None):
    """Canonical CampaignItem -> existing Avito FeedItem projection, without saving it.

    MediaLink is the authoritative gallery when present. Some asynchronous media
    workflows intentionally update campaign media links independently from payload_json;
    projecting payload_json alone can therefore silently collapse a 7-photo gallery to
    one image. For delivery we rebuild the ordered image list from canonical links and
    only fall back to payload_json when no live links exist.
    """
    from app.api.avito import _draft_to_feed_item, _build_xml
    from app.services.campaign_identity import ensure_feed_identity
    items=[]
    for r in scoped:
        p=dict(_ff_payload(r)); p["id"]=str(ensure_feed_identity(r))
        if db is not None:
            try:
                from app.models.media_asset import MediaAsset
                from app.models.media_link import MediaLink
                linked=(db.query(MediaLink,MediaAsset)
                    .join(MediaAsset,MediaAsset.id==MediaLink.media_id)
                    .filter(MediaLink.entity_type=="campaign_item",
                            MediaLink.entity_id==str(r.id),
                            MediaAsset.deleted_at.is_(None),
                            MediaAsset.file_state=="present")
                    .order_by(MediaLink.position.asc(),MediaLink.id.asc()).all())
                urls=[]
                seen=set()
                for _link,_asset in linked:
                    key=str(_asset.storage_key or "").strip()
                    if not key: continue
                    url=key if key.startswith("http://") or key.startswith("https://") else "/images/"+key.lstrip("/")
                    if url not in seen:
                        urls.append(url); seen.add(url)
                if urls:
                    p["images"]=urls
            except Exception as _suppressed_exc:
                observe_suppressed(__name__, _suppressed_exc, line=1834)
        p["address"]=p.get("address") or r.geo or p.get("city") or ""
        # CategoryTreeLeaf.path is the authoritative Avito hierarchy. The XML uses
        # dedicated hierarchy tags instead of the human leaf name in ServiceType/GoodsType.
        # Project those deterministic levels automatically; product-specific facts remain
        # in params and are never guessed here.
        path=str(p.get("category_path") or "").strip()
        levels=[x.strip() for x in path.split(" > ") if x.strip()]
        params=dict(p.get("params") or {})
        if len(levels) >= 3 and levels[0] == "Услуги" and levels[1] == "Предложение услуг":
            p["category"]="Предложение услуг"
            p["service_type"]=p.get("service_type") or levels[2]
            if len(levels) >= 4:
                p["service_subtype"]=p.get("service_subtype") or levels[3]
            if len(levels) >= 5:
                params.setdefault("ServiceSubspecies", levels[4])
        elif len(levels) >= 3:
            # For goods, the first level is the marketplace super-section and the
            # second level is the XML Category used by the current Avito format.
            p["category"]=p.get("category") or levels[1]
            p["goods_type"]=p.get("goods_type") or levels[2]
            if len(levels) >= 4:
                p["goods_subtype"]=p.get("goods_subtype") or levels[3]
                # Some Avito goods branches call the fourth hierarchy level
                # GoodsSubCategory instead of GoodsSubType. Emitting the exact leaf-derived
                # value is deterministic category metadata, not a seller fact.
                if levels[1] == "Спорт и отдых":
                    params.setdefault("GoodsSubCategory", levels[3])
            # Building materials has two extra deterministic hierarchy tags that Avito's
            # live XML checker requires. They come directly from CategoryTreeLeaf.path.
            if len(levels) >= 5 and levels[2] == "Стройматериалы" and levels[3] == "Отделка":
                params.setdefault("FinishingMaterialsType", levels[4])
                if len(levels) >= 6 and levels[4] == "Наружная отделка и декоративный камень":
                    params.setdefault("ExteriorFinishingDecorativeStoneSubType", levels[5])
        # Promote known top-level Avito tags out of params before removing them,
        # so every tag is emitted exactly once through FeedItem.
        _promote={"WorkExperience":"work_experience","Guarantee":"guarantee","AdType":"ad_type","Condition":"condition","KitchenType":"kitchen_type","PriceType":"price_type","Color":"color","GoodsType":"goods_type","GoodsSubType":"goods_subtype","ServiceType":"service_type","ServiceSubtype":"service_subtype"}
        for _tag,_attr in _promote.items():
            if params.get(_tag) not in (None,"") and not p.get(_attr):
                p[_attr]=params.get(_tag)
        # Tags emitted through dedicated FeedItem fields must not also be emitted
        # from params, otherwise XML can contain duplicate nodes. Keep only true
        # category-specific parameters in params.
        for _core_tag in ("Category","ServiceType","ServiceSubtype","GoodsType","GoodsSubType","AdType","Condition","KitchenType","PriceType","Color","WorkExperience","Guarantee"):
            params.pop(_core_tag,None)
        p["params"]=params
        items.append(_draft_to_feed_item(p))
    xml=_build_xml(items)
    return items, xml


def _ff_avito_autoload_capability(db, account_id: str, refresh: bool = False) -> dict:
    """Cached read-only probe of real Avito Autoload access.

    Client ID presence is not enough: Avito can accept OAuth credentials while
    returning 403 for autoload/profile. Cache for 10 minutes to keep UI cheap;
    delivery always forces refresh.
    """
    import datetime as _dt, json as _json
    from app.models.account import Account
    from app.models.storage import Storage
    key="avito_autoload_capability_v1"
    row=db.query(Storage).filter(Storage.account_id==account_id,Storage.key==key).first()
    cached={}
    if row and row.value:
        try: cached=_json.loads(row.value) or {}
        except Exception: cached={}
    if cached and not refresh:
        try:
            ts=_dt.datetime.fromisoformat(str(cached.get("checked_at") or "").replace("Z","+00:00"))
            now=_dt.datetime.now(_dt.timezone.utc)
            if ts.tzinfo is None: ts=ts.replace(tzinfo=_dt.timezone.utc)
            if (now-ts).total_seconds() < 600:
                return cached
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=1909)
    acc=db.query(Account).filter(Account.account_id==account_id).first()
    client_id_enc=str(acc.avito_client_id) if acc and acc.avito_client_id else ""
    client_secret_enc=str(acc.avito_client_secret) if acc and acc.avito_client_secret else ""
    # End the read transaction before token/profile HTTP. Re-query cache after
    # network instead of keeping an ORM Storage row attached across the boundary.
    db.rollback()
    result={"connected":False,"autoload_ready":False,"http_status":None,"message":"Avito не подключён","checked_at":_dt.datetime.now(_dt.timezone.utc).isoformat()}
    if client_id_enc and client_secret_enc:
        try:
            import httpx as _httpx
            from app.crypto_utils import decrypt_secret
            # CAMPAIGN_AUTOLOAD_SHARED_ACCOUNT_THROTTLE_V1: readiness probes are
            # read-only but still consume Avito quota. Honor tenant Retry-After
            # before token/profile I/O and publish local 429 for sibling workers.
            from app.services.avito_account_throttle import account_throttle_remaining as _camp_throttle_remaining, record_account_throttle as _camp_record_throttle
            def _camp_retry_after(resp):
                raw=str(resp.headers.get("Retry-After") or "").strip()
                if not raw: return 30
                try: return max(0,int(float(raw)))
                except Exception:
                    try:
                        from email.utils import parsedate_to_datetime as _parse_camp_ra
                        dt=_parse_camp_ra(raw)
                        if dt.tzinfo is None: dt=dt.replace(tzinfo=_dt.timezone.utc)
                        return max(0,int((dt.astimezone(_dt.timezone.utc)-_dt.datetime.now(_dt.timezone.utc)).total_seconds()))
                    except Exception: return 30
            if _camp_throttle_remaining(account_id) > 0:
                # CAMPAIGN_AUTOLOAD_LAST_GOOD_ON_THROTTLE_V1: keep the previous
                # confirmed readiness snapshot for reporting, but mark it degraded
                # so throttled data can never masquerade as a fresh provider proof.
                if isinstance(cached,dict) and cached:
                    result=dict(cached)
                result["status"]="degraded"
                result["reason"]="avito_account_throttled"
                result["http_status"]=429
                result["fresh_provider_proof"]=False
                result["checked_at"]=_dt.datetime.now(_dt.timezone.utc).isoformat()
                result["message"]="Avito временно ограничил запросы по аккаунту; показан последний подтверждённый статус"
                token_resp=None
                token=None
            else:
                token_resp=_httpx.post("https://api.avito.ru/token/",data={"grant_type":"client_credentials","client_id":decrypt_secret(client_id_enc),"client_secret":decrypt_secret(client_secret_enc)},timeout=20)
                if token_resp.status_code==429:
                    _camp_record_throttle(account_id,_camp_retry_after(token_resp),source="campaign_autoload_token_429")
                    # CAMPAIGN_AUTOLOAD_LOCAL_429_LAST_GOOD_V1: a local token 429
                    # degrades freshness but must not erase the last confirmed
                    # readiness snapshot used for reporting.
                    if isinstance(cached,dict) and cached:
                        result=dict(cached)
                    result["status"]="degraded"
                    result["reason"]="avito_account_throttled"
                    result["http_status"]=429
                    result["fresh_provider_proof"]=False
                    result["checked_at"]=_dt.datetime.now(_dt.timezone.utc).isoformat()
                    result["message"]="Avito временно ограничил запросы по аккаунту; показан последний подтверждённый статус"
                token=(token_resp.json() or {}).get("access_token") if token_resp.status_code==200 else None
            if token and _camp_throttle_remaining(account_id) <= 0:
                result["connected"]=True
                prof=_httpx.get("https://api.avito.ru/autoload/v2/profile",headers={"Authorization":"Bearer "+token},timeout=20)
                if prof.status_code==429:
                    _camp_record_throttle(account_id,_camp_retry_after(prof),source="campaign_autoload_profile_429")
                result["http_status"]=prof.status_code
                _profile=(prof.json() or {}) if prof.status_code==200 else {}
                _feeds=[x for x in (_profile.get("feeds_data") or []) if isinstance(x,dict)]
                _expected=f"https://boris-ai.pro/api/avito/feed/{account_id}.xml"
                _configured_urls=[str(x.get("feed_url") or "").strip() for x in _feeds if str(x.get("feed_url") or "").strip()]
                result["autoload_enabled"]=bool(_profile.get("autoload_enabled")) if prof.status_code==200 else False
                result["feed_configured"]=bool(_configured_urls)
                result["expected_feed_configured"]=_expected in _configured_urls
                result["configured_feed_count"]=len(_configured_urls)
                result["autoload_ready"]=bool(prof.status_code==200 and result["autoload_enabled"])
                if prof.status_code==200 and not result["autoload_enabled"]:
                    result["message"]="Автозагрузка Avito выключена в профиле."
                elif prof.status_code==200 and not result["expected_feed_configured"]:
                    result["message"]="Автозагрузка доступна. BORIS прикрепит свой XML-фид автоматически перед отправкой."
                elif prof.status_code==200:
                    result["message"]="Автозагрузка Avito настроена и готова к отправке"
                elif prof.status_code in (401,403):
                    result["message"]="Avito не разрешил BORIS прочитать настройки Автозагрузки по действующим API-ключам."
                else:
                    result["message"]="Avito не подтвердил доступ к автозагрузке"
                if prof.status_code==429:
                    # CAMPAIGN_AUTOLOAD_PROFILE_429_LAST_GOOD_V1: preserve the
                    # previous confirmed readiness for reporting, but explicitly
                    # mark it degraded/non-fresh after a local profile throttle.
                    if isinstance(cached,dict) and cached:
                        result=dict(cached)
                    result["status"]="degraded"
                    result["reason"]="avito_account_throttled"
                    result["http_status"]=429
                    result["fresh_provider_proof"]=False
                    result["checked_at"]=_dt.datetime.now(_dt.timezone.utc).isoformat()
                    result["message"]="Avito временно ограничил запросы по аккаунту; показан последний подтверждённый статус"
            elif int(result.get("http_status") or 0) != 429:
                result["message"]="Avito не принял Client ID / Client Secret"
        except Exception as e:
            result["message"]="Не удалось проверить доступ к автозагрузке: "+str(e)[:160]
    raw=_json.dumps(result,ensure_ascii=False)
    row=db.query(Storage).filter(Storage.account_id==account_id,Storage.key==key).first()
    if row: row.value=raw
    else: db.add(Storage(account_id=account_id,key=key,value=raw))
    db.flush()
    return result


def _ff_delivery_snapshot(db, campaign, allowed, account_id:str, refresh_autoload: bool=False):
    import hashlib
    from app.models.account import Account
    rows,scoped,approved,accounts,images,cities=_ff_activation_snapshot(db,campaign.id,allowed,account_id)
    if not scoped or accounts != [account_id]:
        raise HTTPException(409, detail={"message":"Кампания содержит объявления другого аккаунта. Отправка заблокирована."})
    items,xml=_ff_delivery_projection(scoped,db); current_sha=hashlib.sha256(xml.encode("utf-8")).hexdigest()
    _,ff=_ff_state_load(campaign); check=ff.get("official_xml_check") or ff.get("official_xmlcheck") or {}
    validated_sha=str(check.get("sha256") or "").strip(); check_status=str(check.get("status") or "").lower()
    validation_state="READY" if check_status in {"pass","ok"} and validated_sha and validated_sha==current_sha else ("STALE" if check_status in {"pass","ok"} else ("CHECKING" if check_status in {"pending","checking","processing"} else ("ERROR" if check_status in {"error","errors","failed"} else "NOT_CHECKED")))
    activation=dict(ff.get("owner_activation") or {})
    publication_locked=bool(_ff_publication_locked(ff))
    submitted=dict(activation.get("submitted_snapshot") or {})
    submitted_sha=str(submitted.get("sha256") or "").strip()
    submitted_validated_sha=str(submitted.get("validated_sha256") or "").strip()
    submitted_validation_state=("READY" if submitted_sha and submitted_validated_sha==submitted_sha else ("UNKNOWN" if publication_locked else None))
    working_copy_changed_after_submit=bool(publication_locked and submitted_sha and current_sha!=submitted_sha)
    acc=db.query(Account).filter(Account.account_id==account_id).first()
    categories=sorted({str(_ff_payload(r).get("category_path") or _ff_payload(r).get("category") or "").strip() for r in scoped if str(_ff_payload(r).get("category_path") or _ff_payload(r).get("category") or "").strip()})
    price_modes=sorted({str(_ff_payload(r).get("price_unit") or _ff_payload(r).get("price_type") or "за объявление").strip() for r in scoped})
    all_approved=len(approved)==len(scoped)==len(rows)
    intake=_ff_direction_intake(db,campaign,ff,scoped); intake_ready=bool(intake.get("all_complete")); intake_pending=[d.get("direction") for d in intake.get("directions") or [] if not d.get("complete")]
    semantic_reports=[_ff_validate_row(r,db) for r in scoped]
    # Feed validation contains contract/copy/media errors together. Feed Control must
    # not mislabel newly-required Avito parameters as a photo semantic failure.
    # Count only errors emitted by the media semantic guard in this UI indicator.
    _media_codes={"legacy_visual_provider_blocked"}
    semantic_invalid=[]
    for _report in semantic_reports:
        _media_errors=[e for e in (_report.get("errors") or []) if str(e.get("code") or "").startswith("media_semantic_") or str(e.get("code") or "") in _media_codes]
        if _media_errors:
            semantic_invalid.append({**_report,"errors":_media_errors})
    semantic_ready=not semantic_invalid
    near_duplicate_pairs=_ff_near_duplicate_pairs(scoped)
    cross_campaign_near=_ff_cross_campaign_near_duplicate_conflicts(db,campaign.id,scoped)
    content_unique=(not near_duplicate_pairs) and (not cross_campaign_near)
    autoload=_ff_avito_autoload_capability(db,account_id,refresh=bool(refresh_autoload))
    autoload_ready=bool(autoload.get("autoload_ready"))
    can_send=(not publication_locked) and all_approved and intake_ready and semantic_ready and content_unique and validation_state=="READY" and autoload_ready
    reason=None
    if publication_locked:
        reason="Кампания уже передана Avito. BORIS отслеживает итог публикации; повторная отправка заблокирована."
    elif not intake_ready: reason="Перед публикацией ответьте на вопросы по направлениям: "+", ".join(intake_pending[:8])
    elif not all_approved: reason=f"Сначала согласуйте объявления. Сейчас согласовано {len(approved)} из {len(scoped)}."
    elif not semantic_ready: reason=f"Фотографии/контент не прошли обязательную смысловую проверку: заблокировано {len(semantic_invalid)} объявлений."
    elif not content_unique:
        _cross_count=sum(len(v) for v in cross_campaign_near.values())
        reason=f"Отправка заблокирована: найдено {len(near_duplicate_pairs)} пар почти одинаковых текстов внутри кампании и {_cross_count} совпадений с уже опубликованными объявлениями. BORIS должен сначала реально уникализировать основную часть."
    elif validation_state=="STALE": reason="Фид изменился после последней проверки Avito. Проверьте фид ещё раз."
    elif validation_state!="READY": reason="Перед отправкой нужна успешная актуальная проверка Avito."
    elif not bool(autoload.get("connected")): reason="Для аккаунта не настроено рабочее подключение Avito."
    elif not autoload_ready: reason=autoload.get("message") or "Автозагрузка Avito недоступна для этого аккаунта."
    return {"status":"ok","campaign_id":campaign.id,"campaign_name":campaign.name,"account_id":account_id,"account_name":getattr(acc,"name",None) or account_id,
            "feed_url":f"https://boris-ai.pro/api/avito/feed/{account_id}.xml","feed_url_class":"PUBLIC","feed_mode":"Автозагрузка Avito XML","publication_mode":"Только после подтверждения владельца",
            "items":len(scoped),"approved":len(approved),"photos":images,"cities":cities,"categories":categories,"price_modes":price_modes,
            "current_sha":current_sha,"validated_sha":validated_sha or None,"validation_state":validation_state,"validation_message":check.get("message") or check.get("result") or "",
            "publication_locked":publication_locked,"submitted_snapshot":submitted or None,"submitted_sha":submitted_sha or None,
            "submitted_validation_state":submitted_validation_state,"working_copy_changed_after_submit":working_copy_changed_after_submit,
            "last_validation":check.get("checked_at"),"warnings":len(check.get("warnings") or []),"errors":len(check.get("errors") or []),
            "activation":activation,"last_send":activation.get("sent_at") or activation.get("updated_at"),"last_result":activation.get("message") or "",
            "intake_ready":intake_ready,"intake_pending":intake_pending,
            "semantic_ready":semantic_ready,"semantic_blocked":len(semantic_invalid),
            "content_unique":content_unique,"near_duplicate_pairs":near_duplicate_pairs[:100],
            "cross_campaign_near_duplicates":cross_campaign_near,
            "account_bound":accounts==[account_id],"avito_connected":bool(autoload.get("connected")),
            "autoload_ready":autoload_ready,"autoload_http_status":autoload.get("http_status"),"autoload_message":autoload.get("message"),
            "autoload_enabled":autoload.get("autoload_enabled"),"feed_configured":autoload.get("feed_configured"),"expected_feed_configured":autoload.get("expected_feed_configured"),
            "can_send":can_send,"blocked_reason":reason}


def _campaign_guardian_job_public(job):
    from app.services.jobs import job_public
    data = job_public(job)
    result = job.result_json or {}
    data["stage"] = result.get("stage") or (job.payload_json or {}).get("stage") or ""
    data["message"] = result.get("message") or ""
    data["result"] = {k: result.get(k) for k in ("stage","campaign_id","account_id","items","photos","validation_state","avito_connected","can_send","blocked_reason","external_upload","upload_id","report") if k in result}
    if result.get("questions"):
        data["questions"] = result.get("questions")
    if result.get("intake"):
        data["intake"] = result.get("intake")
    return data


def _campaign_guardian_enqueue(db, campaign, user, account_id, kind):
    from app.services.jobs import enqueue
    from app.models.background_job import BackgroundJob
    fingerprint = f"{kind}:{campaign.id}:{account_id}"
    # Never resurrect preparatory work after an owner-confirmed Avito handoff.
    # The publication watcher is the only job allowed to continue in that state.
    # This closes queue pollution where a stale UI click or a late banner/enhance
    # completion could requeue campaign_prepare after the submitted XML was frozen.
    _, _guard_ff = _ff_state_load(campaign)
    if _ff_publication_locked(_guard_ff):
        row=db.query(BackgroundJob).filter(BackgroundJob.fingerprint==fingerprint).first()
        if kind in {"campaign_prepare","campaign_banners","campaign_enhance"}:
            if row is None:
                row, _ = enqueue(db, kind, payload={"campaign_id":campaign.id,"account_id":account_id,"stage":"publication_locked"},
                                 owner_user_id=getattr(user,"id",None), account_id=account_id,
                                 fingerprint=fingerprint, progress_total=(8 if kind=="campaign_prepare" else 4), status="completed")
            else:
                row.status="completed"; row.started_at=None; row.scheduled_at=None
            row.result_json={"stage":"publication_locked","campaign_id":campaign.id,"account_id":account_id,
                             "message":"Кампания уже передана Avito. BORIS не запускает повторную подготовку поверх опубликованной версии."}
            row.error_text=None; row.finished_at=__import__('datetime').datetime.now(__import__('datetime').timezone.utc)
            db.commit(); db.refresh(row); return row
        if kind=="campaign_deliver":
            # Critical exactly-once boundary: never let enqueue() reset a terminal
            # delivery row just because the owner/browser repeated the same click.
            # A retry of one failed ad is a separate explicit workflow/artifact.
            if row is not None:
                db.commit(); db.refresh(row); return row
            raise HTTPException(status_code=409,detail={"code":"campaign_already_submitted",
                "message":"Кампания уже передана Avito. Повторная отправка всей кампании заблокирована."})
    row, created = enqueue(db, kind, payload={"campaign_id":campaign.id,"account_id":account_id,"stage":"queued"},
                           owner_user_id=getattr(user,"id",None), account_id=account_id,
                           fingerprint=fingerprint, progress_total=(8 if kind=="campaign_prepare" else 4))
    # A terminal Avito publication report must not be silently replayed by a second
    # click: that could duplicate already-published items. Repair/retry requires the
    # campaign to move back through preparation/revalidation first.
    _existing_stage=str((row.result_json or {}).get("stage") or "")
    if not created and kind=="campaign_deliver" and _existing_stage in {"publication_partial","publication_failed","publication_pending_review","delivered"}:
        db.commit(); db.refresh(row); return row
    # A waiting/failed/completed row is the durable identity for this campaign action.
    # Explicit user continuation resumes the same row; no duplicate job is created.
    if not created and row.status in ("awaiting_confirmation","failed","partial","completed","cancelled"):
        row.status = "queued"
        row.result_json = None
        row.error_text = None
        row.started_at = None
        row.finished_at = None
        row.progress_done = 0
        p = dict(row.payload_json or {})
        p.update({"campaign_id":campaign.id,"account_id":account_id,"stage":"queued"})
        row.payload_json = p
    db.commit(); db.refresh(row)
    return row


@router.post("/{campaign_id}/guardian/prepare")
def campaign_guardian_prepare(campaign_id:int, body:dict|None=None, db=Depends(_db), user=Depends(get_current_user)):
    allowed=_allowed_accounts(db,user); campaign=_campaign_or_404(db,campaign_id,allowed)
    body=body or {}; target=str(body.get("account_id") or campaign.default_account_id or "").strip()
    if not target or target not in allowed: raise HTTPException(403,"Нет доступа к аккаунту")
    if body.get("content_mode") == "local_verified_v2":
        from app.services.local_content_worker import enqueue_local
        from app.services.local_content_worker.boris_content import Blocked
        try:
            row, _ = enqueue_local(db, campaign.id, target, user.id,
                                   local_model=body.get("local_model", "qwen3:1.7b"),
                                   rewrite=body.get("rewrite", False))
        except Blocked as exc:
            raise HTTPException(422, detail={"code": str(exc)}) from exc
        db.commit(); db.refresh(row)
    else:
        row=_campaign_guardian_enqueue(db,campaign,user,target,"campaign_prepare")
    return {"status":"ok","job":_campaign_guardian_job_public(row),"persistent":True,"account_scoped":True}


@router.post("/{campaign_id}/guardian/deliver")
def campaign_guardian_deliver(campaign_id:int, body:FFOwnerActivationBody, db=Depends(_db), user=Depends(get_current_user)):
    if not body.confirmed:
        raise HTTPException(409,detail={"message":"Нужно подтвердить размещение объявлений в Avito."})
    allowed=_allowed_accounts(db,user); campaign=_campaign_or_404(db,campaign_id,allowed)
    target=str(body.account_id or campaign.default_account_id or "").strip()
    if not target or target not in allowed: raise HTTPException(403,"Нет доступа к аккаунту")
    row=_campaign_guardian_enqueue(db,campaign,user,target,"campaign_deliver")
    return {"status":"ok","job":_campaign_guardian_job_public(row),"persistent":True,"account_scoped":True}



@router.get("/{campaign_id}/guardian/status")
def campaign_guardian_status(campaign_id:int, account_id:str="", db=Depends(_db), user=Depends(get_current_user)):
    from app.models.background_job import BackgroundJob
    allowed=_allowed_accounts(db,user); campaign=_campaign_or_404(db,campaign_id,allowed)
    target=str(account_id or campaign.default_account_id or "").strip()
    if not target or target not in allowed: raise HTTPException(403,"Нет доступа к аккаунту")
    rows=db.query(BackgroundJob).filter(BackgroundJob.account_id==target,
        BackgroundJob.kind.in_(("campaign_prepare","campaign_deliver","campaign_enhance"))).order_by(BackgroundJob.id.desc()).limit(20).all()
    rows=[j for j in rows if int((j.payload_json or {}).get("campaign_id") or 0)==campaign_id]
    return {"status":"ok","campaign_id":campaign_id,"account_id":target,"jobs":[_campaign_guardian_job_public(j) for j in rows],
            "running":any(j.status in ("queued","running","validating") for j in rows)}


@router.get("/{campaign_id}/feed_control")
def campaign_feed_control(campaign_id:int, account_id:str="", db=Depends(_db), user=Depends(get_current_user)):
    allowed=_allowed_accounts(db,user); campaign=_campaign_or_404(db,campaign_id,allowed)
    target=(account_id or campaign.default_account_id or "").strip()
    if not target or target not in allowed: raise HTTPException(403,"Нет доступа к аккаунту")
    return _ff_delivery_snapshot(db,campaign,allowed,target)


@router.post("/{campaign_id}/feed_control/validate")
def campaign_feed_validate(campaign_id:int, body:FFOwnerActivationBody, db=Depends(_db), user=Depends(get_current_user)):
    import asyncio, hashlib, datetime, os
    allowed=_allowed_accounts(db,user); campaign=_campaign_or_404(db,campaign_id,allowed); target=(body.account_id or campaign.default_account_id or "").strip()
    if not target or target not in allowed: raise HTTPException(403,"Нет доступа к аккаунту")
    rows,scoped,approved,accounts,images,cities=_ff_activation_snapshot(db,campaign_id,allowed,target)
    if not scoped or accounts != [target]: raise HTTPException(409,detail={"message":"Account scope фида не совпадает с Campaign. Проверка заблокирована."})
    _,ff_now=_ff_state_load(campaign); intake=_ff_direction_intake(db,campaign,ff_now,scoped)
    if not intake.get("all_complete"):
        raise HTTPException(409,detail={"message":"Проверка фида заблокирована: сначала завершите опрос по каждому направлению.","directions":[d.get("direction") for d in intake.get("directions") or [] if not d.get("complete")]})
    semantic_reports=[_ff_validate_row(r,db) for r in scoped]; semantic_bad=[x for x in semantic_reports if not x.get("valid")]
    if semantic_bad:
        raise HTTPException(409,detail={"message":"Проверка Avito заблокирована: сначала исправьте несоответствие фотографий названию/описанию/категории.","semantic_invalid":[x.get("id") for x in semantic_bad]})
    items,xml=_ff_delivery_projection(scoped,db); sha=hashlib.sha256(xml.encode("utf-8")).hexdigest()
    root="/root/BORIS/backend/images"; rel=f"{target}/feed_factory_validation/campaign_{campaign_id}_owner_check.xml"; path=os.path.join(root,rel); os.makedirs(os.path.dirname(path),exist_ok=True)
    with open(path,"w",encoding="utf-8") as f: f.write(xml)
    url="https://boris-ai.pro/images/"+rel
    from app.api.parser import validate_feed_xmlcheck
    result={"status":"error","errors":["Avito validator did not complete"],"raw_text":"","issues":[]}
    # Everything required by the validator is now scalar/file state. Close all
    # campaign reads before external Avito xmlcheck; ORM access resumes afterwards.
    db.commit()
    # Retry only transport/unknown failures. A deterministic Avito schema error must not
    # be submitted three times: we learn it immediately and surface the missing fact.
    for _attempt in range(3):
        try: result=asyncio.run(validate_feed_xmlcheck(url)) or {}
        except Exception as e: result={"status":"error","errors":[str(e)],"raw_text":"","issues":[]}
        _st=str(result.get("status") or "").lower()
        if _st in {"ok","errors","pending"}: break
    _result_status=str(result.get("status") or "").lower()
    ok=_result_status=="ok"
    pending=_result_status=="pending"
    now=datetime.datetime.now(datetime.timezone.utc).isoformat()
    _,ff=_ff_state_load(campaign)

    # Learn exact missing XML tags from Avito's own report and attach them to the
    # corresponding template contract. This is authoritative feedback, not AI guessing.
    learned=[]; docs_enriched=[]
    try:
        from app.models.category_template import CategoryTemplate
        from app.services.campaign_identity import ensure_feed_identity
        by_identity={str(ensure_feed_identity(r)):r for r in scoped}
        _label_fallback={
            "FinishingMaterialsType":"Тип отделочных материалов",
            "AutoserviceServiceType":"Тип услуги автосервиса",
            "WorkExperience":"Опыт работы",
            "Guarantee":"Гарантия",
            "GoodsSubCategory":"Подвид товара",
        }
        for issue in (result.get("issues") or []):
            field=str(issue.get("field") or "").strip(); ad_id=str(issue.get("ad_id") or "").strip()
            row=by_identity.get(ad_id)
            if not field or not row: continue
            p=_ff_payload(row); tid=str(p.get("template_id") or "").strip()
            if not tid: continue
            tpl=db.query(CategoryTemplate).filter(CategoryTemplate.template_id==tid).first()
            if not tpl:
                # CategoryTreeLeaf can exist before a parsed CategoryTemplate cache row.
                # Create the cache row from the authoritative leaf so official xmlcheck
                # feedback is not lost for exactly those categories with sparse docs.
                from app.models.category_template import CategoryTreeLeaf
                leaf=db.query(CategoryTreeLeaf).filter(CategoryTreeLeaf.template_id==tid).first()
                _path=str(getattr(leaf,"path","") or p.get("category_path") or "")
                tpl=CategoryTemplate(category_id="official_template_"+tid,category_name=_path,template_id=tid,
                                     required_fields="[]",field_rules="{}",enum_values="{}")
                db.add(tpl); db.flush()
            try: docs=json.loads(tpl.required_fields or "[]") if tpl.required_fields else []
            except Exception: docs=[]
            # Optional warning fields such as AdType are not promoted to required.
            required=str(issue.get("severity") or "").lower()!="warning"
            label=str(issue.get("label") or _label_fallback.get(field) or field).strip()
            existing_doc=next((x for x in docs if str(x.get("tag") or "")==field),None)
            if existing_doc:
                # Official live checker outranks stale scraped docs. If Avito says an
                # already-known optional field is required now, promote it in place.
                if required and not bool(existing_doc.get("required")):
                    existing_doc["required"]=True
                    existing_doc["source"]="official_avito_xmlcheck"
                    existing_doc["name_ru"]=_label_fallback.get(field) or label
                    tpl.required_fields=json.dumps(docs,ensure_ascii=False)
                    learned.append({"template_id":tid,"field":field,"label":_label_fallback.get(field) or label,"required":True,"ad_id":ad_id})
                continue
            docs.append({"tag":field,"name_ru":_label_fallback.get(field) or label,"required":required,
                         "depends_on":None,"allowed_values":None,"format_rule":None,"example":None,
                         "source":"official_avito_xmlcheck"})
            tpl.required_fields=json.dumps(docs,ensure_ascii=False)
            # This field came from the official live validator, so treat the contract as
            # freshly observed and do not immediately overwrite it with a blocked docs refresh.
            try: tpl.fetched_at=datetime.datetime.utcnow()
            except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=2286)
            learned.append({"template_id":tid,"field":field,"label":_label_fallback.get(field) or label,"required":required,"ad_id":ad_id})
        if learned:
            db.flush()
            # Live xmlcheck can move a service to a newer User Docs branch while our
            # cached template_id/category leaf is still stale. Enrich newly learned
            # fields from the exact live ServiceType/ServiceSubtype emitted by the card,
            # so enums such as Consultations/Specialty/WorkWithContract are recovered
            # immediately instead of becoming free-text owner questions.
            try:
                from app.services.category_resolver import refresh_category_template_from_account_docs
                _seen_live_docs=set()
                for _learn in learned:
                    _row=by_identity.get(str(_learn.get("ad_id") or ""))
                    if not _row: continue
                    _p=_ff_payload(_row); _tid=str(_p.get("template_id") or _learn.get("template_id") or "").strip()
                    _stype=str(_p.get("service_type") or "").strip(); _ssub=str(_p.get("service_subtype") or "").strip()
                    if not _tid or not _stype or not _ssub: continue
                    _live_path="Услуги > Предложение услуг > %s > %s" % (_stype,_ssub)
                    _key=(_tid,_live_path)
                    if _key in _seen_live_docs: continue
                    _seen_live_docs.add(_key)
                    from app.api.feed_contract import resolve_feed_contract as _resolve_live_base
                    _cached_path=str((_resolve_live_base(_tid,_db=db) or {}).get("category_path") or "").strip()
                    if _cached_path and _cached_path != _live_path:
                        # Do not rewrite a global template cache when only the live card
                        # proves a newer branch. Fetch the exact authenticated User Docs
                        # node read-only and persist that knowledge only on matching cards.
                        _ro=refresh_category_template_from_account_docs(_tid,_live_path,target,db,persist=False,force=True)
                        _official=list((_ro or {}).get("official_fields") or [])
                        if _official:
                            import hashlib as _hashlib
                            _overlay_fields={}
                            for _f in _official:
                                _name=str(_f.get("tag") or "").strip()
                                if not _name: continue
                                _overlay_fields[_name]={"required":bool(_f.get("required")),"allowed":list(_f.get("allowed_values") or []),
                                    "format_rule":str(_f.get("format_rule") or ""),"name_ru":str(_f.get("name_ru") or _name),
                                    "depends_on":str(_f.get("depends_on") or ""),"multiple":bool(_f.get("multiple")),"source":"official_avito_user_docs"}
                            _basis=json.dumps({"path":_live_path,"fields":_overlay_fields},ensure_ascii=False,sort_keys=True,separators=(",",":"))
                            _version="live-"+_hashlib.sha256(_basis.encode("utf-8")).hexdigest()[:12]
                            _applied=0
                            for _candidate in scoped:
                                _cp=_ff_payload(_candidate)
                                if str(_cp.get("template_id") or "").strip()!=_tid: continue
                                if str(_cp.get("service_type") or "").strip()!=_stype or str(_cp.get("service_subtype") or "").strip()!=_ssub: continue
                                _cp["live_contract_overlay"]={"source":"official_avito_user_docs","node_slug":str((_ro or {}).get("node_slug") or ""),
                                    "official_path":_live_path,"contract_version":_version,"fields":_overlay_fields,"observed_at":now}
                                _candidate.payload_json=json.dumps(_cp,ensure_ascii=False); _applied+=1
                            db.flush()
                            docs_enriched.append({"template_id":_tid,"category_path":_live_path,"cached_category_path":_cached_path,
                                                  "status":"schema_drift_scoped","refreshed":False,"scoped_overlay":_version,
                                                  "fields":len(_overlay_fields),"items":_applied,"node_slug":str((_ro or {}).get("node_slug") or "")})
                        else:
                            docs_enriched.append({"template_id":_tid,"category_path":_live_path,"cached_category_path":_cached_path,
                                                  "status":"schema_drift_scoped_unresolved","refreshed":False,"reason":str((_ro or {}).get("status") or "no_official_fields")})
                        continue
                    _enrich=refresh_category_template_from_account_docs(_tid,_live_path,target,db)
                    docs_enriched.append({"template_id":_tid,"category_path":_live_path,**(_enrich or {})})
            except Exception as _docs_exc:
                docs_enriched.append({"status":"error","reason":type(_docs_exc).__name__})
    except Exception:
        learned=[]; docs_enriched=[]

    ff["official_required_feedback"]={"issues":result.get("issues") or [],"learned":learned,"docs_enriched":docs_enriched,"checked_at":now}
    _check_status="pass" if ok else ("pending" if pending else "error")
    _check_message="XML соответствует формату" if ok else ("Avito ещё формирует отчёт — BORIS продолжит проверку автоматически" if pending else "Проверка Avito не пройдена")
    ff["official_xml_check"]={"status":_check_status,"checker":"Avito xmlcheck","message":_check_message,"ready":len(scoped) if ok else 0,"total":len(scoped),"sha256":sha,"url":url,"checked_at":now,"media_placements":images,"warnings":result.get("warnings") or [],"errors":result.get("errors") or [],"issues":result.get("issues") or [],"learned":learned,"transport":result.get("transport"),"check_id":result.get("check_id")}
    ff["owner_ready_xml"]={"artifact":"/images/"+rel,"sha256":sha,"items":len(scoped),"media_placements":images,"generated_at":now,"external_upload":False,"official_xmlcheck":{"status":_check_status,"message":_check_message,"checked_items":len(scoped),"checked_at":now}}
    _ff_state_save(db,campaign,ff)
    # Official XML validation is also the natural point to refresh real Autoload
    # capability, so READY never means "OAuth keys exist but upload is forbidden".
    return _ff_delivery_snapshot(db,campaign,allowed,target,refresh_autoload=True)


@router.get("/{campaign_id}/owner_activation")
def campaign_owner_activation(campaign_id:int, account_id:str="", db=Depends(_db), user=Depends(get_current_user)):
    allowed=_allowed_accounts(db,user); campaign=_campaign_or_404(db,campaign_id,allowed)
    target=(account_id or campaign.default_account_id or "").strip()
    if not target or target not in allowed: raise HTTPException(403,"Нет доступа к аккаунту")
    from app.services.reliability import module_blocked as _module_blocked
    _disabled,_disabled_reason=_module_blocked(db,"marketing",target)
    rows,scoped,approved,accounts,images,cities=_ff_activation_snapshot(db,campaign_id,allowed,target)
    _,ff=_ff_state_load(campaign)
    xml=ff.get("official_xml_check") or ff.get("official_xmlcheck") or {}
    activation=ff.get("owner_activation") or {}
    _publication_locked=bool(_ff_publication_locked(ff))
    _retry_candidate=ff.get("safe_retry_candidate") or None
    if _publication_locked:
        _snap=dict(activation.get("submitted_snapshot") or {})
        return {"status":"ok","campaign_id":campaign_id,"account_id":target,
                "total":int(_snap.get("items") or activation.get("items") or len(scoped)),"approved":len(approved),
                "all_approved":False,"cities":cities,"photos":int(_snap.get("photos") or images),
                "avito_check":{"status":"SUBMITTED","checked":int(_snap.get("items") or len(scoped)),"sha256":_snap.get("validated_sha256") or _snap.get("sha256")},
                "activation":activation,"publication_locked":True,"submitted_snapshot":_snap or None,
                "safe_retry_candidate":_retry_candidate,"can_activate":False,
                "blocked_reason":"Avito обрабатывает уже отправленную кампанию. Повторная отправка и изменения временно заблокированы до итогового отчёта."}
    all_approved=bool(scoped) and len(approved)==len(scoped) and len(scoped)==len(rows) and accounts==[target]
    delivery=_ff_delivery_snapshot(db,campaign,allowed,target)
    return {"status":"ok","campaign_id":campaign_id,"account_id":target,"total":len(scoped),"approved":len(approved),
            "all_approved":all_approved,"cities":cities,"photos":images,
            "avito_check":{"status":delivery.get("validation_state"),"checked":xml.get("checked") or xml.get("items") or len(scoped)},
            "activation":activation,"publication_locked":False,"submitted_snapshot":activation.get("submitted_snapshot"),
            "safe_retry_candidate":_retry_candidate,
            "can_activate":bool((not _disabled) and all_approved and delivery.get("can_send")),
            "blocked_reason":(_disabled_reason or "Действия BORIS для аккаунта отключены") if _disabled else (delivery.get("blocked_reason") or (None if all_approved else f"Согласуйте объявления перед размещением. Сейчас согласовано {len(approved)} из {len(scoped)}."))}


@router.post("/{campaign_id}/owner_activation")
def campaign_owner_activate(campaign_id:int, body:FFOwnerActivationBody, db=Depends(_db), user=Depends(get_current_user)):
    # Owner-confirmed bridge only: CampaignItem remains source of truth; the existing Avito feed store/uploader is reused.
    allowed=_allowed_accounts(db,user); campaign=_campaign_or_404(db,campaign_id,allowed)
    target=(body.account_id or "").strip()
    if target not in allowed: raise HTTPException(403,"Нет доступа к аккаунту")
    from app.services.reliability import module_blocked as _module_blocked
    _disabled,_disabled_reason=_module_blocked(db,"marketing",target)
    if _disabled:
        raise HTTPException(409,detail={"code":"account_disabled","message":_disabled_reason or "Действия BORIS для аккаунта отключены","changed_avito":False})
    rows,scoped,approved,accounts,images,cities=_ff_activation_snapshot(db,campaign_id,allowed,target)
    if not scoped or len(approved)!=len(scoped) or len(scoped)!=len(rows) or accounts!=[target]:
        raise HTTPException(409,detail={"message":f"Согласуйте объявления перед размещением. Сейчас согласовано {len(approved)} из {len(scoped)}.","approved":len(approved),"total":len(scoped)})
    # Delivery always refreshes real Autoload capability immediately before any
    # persistent feed-store mutation or external upload.
    delivery=_ff_delivery_snapshot(db,campaign,allowed,target,refresh_autoload=True)
    if not delivery.get("semantic_ready"):
        raise HTTPException(409,detail={"message":delivery.get("blocked_reason") or "Отправка заблокирована: фотографии не соответствуют названию/описанию/категории.","semantic_blocked":delivery.get("semantic_blocked")})
    if delivery.get("validation_state") != "READY":
        raise HTTPException(409,detail={"message":delivery.get("blocked_reason") or "Перед размещением нужна успешная актуальная проверка Avito.","avito_check":delivery.get("validation_state"),"current_sha":delivery.get("current_sha"),"validated_sha":delivery.get("validated_sha")})
    if not delivery.get("autoload_ready"):
        raise HTTPException(409,detail={"message":delivery.get("autoload_message") or delivery.get("blocked_reason") or "Автозагрузка Avito недоступна для этого аккаунта.","avito_connected":delivery.get("avito_connected"),"autoload_ready":False,"autoload_http_status":delivery.get("autoload_http_status")})
    if not body.confirmed:
        return {"status":"needs_confirmation","campaign_id":campaign_id,"account_id":target,"items":len(scoped),"cities":cities,"photos":images}

    # Reuse the EXACT projection that passed official Avito validation. Never rebuild
    # publication items through a second mapping path after the SHA was approved.
    from app.api.avito import (
        _upsert_feed_items, _save_feed_items, _load_feed_items, _mutate_feed_items,
        _autoload_upload_snapshot,
        get_avito_token, _extract_token, _avito_http_get, feed_send_to_avito,
    )
    campaign_items,_validated_campaign_xml=_ff_delivery_projection(scoped,db)

    # CAMPAIGN_EXPLICIT_CUTOVER_OLD_ABSENCE_V1:
    # A replacement campaign must never be merged with the old identities. Avito
    # rejects that as duplicate code 2010. Full feed replacement is allowed only
    # for an explicit old->new cutover AND only after read-only Core API evidence
    # proves every old Avito item id is absent. Any uncertainty is fail-closed.
    try:
        _campaign_settings=json.loads(campaign.settings_json or "{}")
    except Exception:
        _campaign_settings={}
    _cutover=dict((_campaign_settings or {}).get("cutover") or {})
    _explicit_cutover=bool(
        str((_campaign_settings or {}).get("publication_mode") or "")=="replace_old_campaign_with_new_identities"
        and int(_cutover.get("old_campaign_id") or 0)>0
    )
    _cutover_evidence=None
    _targeted_replacement=bool(
        str((_campaign_settings or {}).get("publication_mode") or "")=="replace_single_rejected_feed_identity"
        and str((_campaign_settings or {}).get("replace_feed_identity") or "").strip()
        and int((_campaign_settings or {}).get("parent_campaign_item_id") or 0)>0
    )
    _targeted_replacement_evidence=None
    if _targeted_replacement:
        if _explicit_cutover or len(campaign_items)!=1:
            raise HTTPException(status_code=409,detail={
                "code":"targeted_replacement_scope_invalid",
                "message":"Точечная замена остановлена: repair-кампания должна содержать ровно одно объявление и не смешиваться с полным cutover.",
                "changed_avito":False,
            })
        from app.models.campaign_item import CampaignItem as _RepairCampaignItem
        _old_fid=str((_campaign_settings or {}).get("replace_feed_identity") or "").strip()
        _old_aid=str((_campaign_settings or {}).get("replace_avito_item_id") or "").strip()
        _parent_item_id=int((_campaign_settings or {}).get("parent_campaign_item_id") or 0)
        _reject_upload=int((_campaign_settings or {}).get("provider_rejection_upload_id") or 0)
        _reject_code=int((_campaign_settings or {}).get("provider_rejection_code") or 0)
        _new_fid=str(getattr(campaign_items[0],"id","") or "").strip()
        if not _new_fid or _new_fid==_old_fid or not _old_aid or _reject_upload<=0 or _reject_code!=2014:
            raise HTTPException(status_code=409,detail={
                "code":"targeted_replacement_contract_invalid",
                "message":"Точечная замена остановлена: не хватает точного rejected-контракта Avito.",
                "changed_avito":False,
            })
        _parent_row=(db.query(_RepairCampaignItem)
                     .filter(_RepairCampaignItem.id==_parent_item_id,
                             _RepairCampaignItem.account_id==target,
                             _RepairCampaignItem.feed_identity==_old_fid,
                             _RepairCampaignItem.avito_item_id==_old_aid)
                     .first())
        if _parent_row is None:
            raise HTTPException(status_code=409,detail={
                "code":"targeted_replacement_parent_mismatch",
                "message":"Точечная замена остановлена: исходная rejected-карточка не совпала с сохранённым контрактом.",
                "changed_avito":False,
            })
        _parent_payload=_ff_payload(_parent_row)
        _parent_identity=dict((_parent_payload or {}).get("identity") or {})
        if (str(getattr(_parent_row,"identity_status","") or "")!="publication_rejected_bound"
                or str(_parent_identity.get("publication_state") or "")!="rejected"
                or int(_parent_identity.get("publication_rejection_upload_id") or 0)<_reject_upload):
            raise HTTPException(status_code=409,detail={
                "code":"targeted_replacement_rejection_not_proven",
                "message":"Точечная замена остановлена: rejected-состояние исходной карточки не доказано.",
                "changed_avito":False,
            })

        _provider_snap=_autoload_upload_snapshot(target,None,expected_ad_ids=[_old_fid])
        _provider_scope=dict((_provider_snap or {}).get("scope") or {})
        _provider_errors=[dict(x or {}) for x in (_provider_scope.get("item_errors") or [])]
        _provider_error=next((x for x in _provider_errors
                              if str(x.get("ad_id") or "").strip()==_old_fid
                              and int(x.get("message_code") or 0)==_reject_code),None)
        if (str((_provider_snap or {}).get("status") or "")!="ok"
                or not bool(_provider_scope.get("terminal"))
                or int(_provider_scope.get("active") or 0)!=0
                or _provider_error is None):
            raise HTTPException(status_code=409,detail={
                "code":"targeted_replacement_provider_rejection_unproven",
                "message":"Точечная замена остановлена: Avito не подтвердил исходный reject 2014 прямо сейчас.",
                "changed_avito":False,
                "upload_id":(_provider_snap or {}).get("upload_id"),
            })

        _before_models=_load_feed_items(target)
        _before_ids={str(getattr(x,"id","") or "").strip() for x in _before_models}
        _before_ids.discard("")
        if _old_fid not in _before_ids and _new_fid not in _before_ids:
            raise HTTPException(status_code=409,detail={
                "code":"targeted_replacement_old_identity_missing",
                "message":"Точечная замена остановлена: исходный feed identity уже отсутствует, а новый ещё не установлен.",
                "changed_avito":False,
            })
        if _old_fid in _before_ids and _new_fid in _before_ids:
            raise HTTPException(status_code=409,detail={
                "code":"targeted_replacement_both_identities_present",
                "message":"Точечная замена остановлена: старый и новый identity одновременно присутствуют в feed.",
                "changed_avito":False,
            })
        if _old_fid in _before_ids:
            _expected_after=(_before_ids-{_old_fid})|{_new_fid}
            def _swap_one(_latest):
                _latest_ids={str(getattr(x,"id","") or "").strip() for x in _latest}
                _latest_ids.discard("")
                if _latest_ids!=_before_ids:
                    raise RuntimeError("TARGETED_REPLACEMENT_SCOPE_DRIFT")
                _out=[]; _replaced=0
                for _x in _latest:
                    if str(getattr(_x,"id","") or "").strip()==_old_fid:
                        _out.append(campaign_items[0]); _replaced+=1
                    else:
                        _out.append(_x)
                if _replaced!=1:
                    raise RuntimeError("TARGETED_REPLACEMENT_OLD_ID_COUNT")
                return _out
            try:
                _mutate_feed_items(target,_swap_one)
            except Exception as _exc:
                raise HTTPException(status_code=409,detail={
                    "code":"targeted_replacement_atomic_mutation_failed",
                    "message":"Точечная замена остановлена: canonical feed изменился параллельно.",
                    "error_type":type(_exc).__name__,"changed_avito":False,
                })
        else:
            _expected_after=set(_before_ids)

        _after_ids={str(getattr(x,"id","") or "").strip() for x in _load_feed_items(target)}
        _after_ids.discard("")
        if _after_ids!=_expected_after or _old_fid in _after_ids or _new_fid not in _after_ids:
            raise HTTPException(status_code=409,detail={
                "code":"targeted_replacement_verify_failed",
                "message":"Точечная замена остановлена: итоговый feed не совпал с ожидаемым составом.",
                "changed_avito":False,
            })
        import datetime as _dt_repair
        _repair_verified_at=_dt_repair.datetime.now(_dt_repair.timezone.utc).isoformat()
        _repair_state=dict((_campaign_settings or {}).get("targeted_replacement") or {})
        _repair_state.update({
            "policy_version":"TARGETED_REJECTED_IDENTITY_REPLACEMENT_V1",
            "state":"armed_waiting_avito",
            "old_feed_identity":_old_fid,
            "new_feed_identity":_new_fid,
            "old_avito_item_id":_old_aid,
            "provider_rejection_upload_id":_reject_upload,
            "provider_rejection_code":_reject_code,
            "provider_current_upload_id":int((_provider_snap or {}).get("upload_id") or 0) or None,
            "feed_items_before":len(_before_ids),
            "feed_items_after":len(_after_ids),
            "verified_at":_repair_verified_at,
            "owner_action_required":False,
        })
        _campaign_settings["targeted_replacement"]=_repair_state
        campaign.settings_json=json.dumps(_campaign_settings,ensure_ascii=False)
        db.commit()
        _targeted_replacement_evidence={
            "verified":True,
            "old_feed_identity":_old_fid,
            "new_feed_identity":_new_fid,
            "provider_rejection_code":_reject_code,
            "provider_rejection_upload_id":_reject_upload,
            "feed_items_before":len(_before_ids),
            "feed_items_after":len(_after_ids),
            "verified_at":_repair_verified_at,
        }

    if _explicit_cutover:
        _old_campaign_id=int(_cutover.get("old_campaign_id") or 0)
        _preserve=int((_campaign_settings or {}).get("preserve_existing_feed_items") or 0)
        _expected_total=int((_campaign_settings or {}).get("feed_total_after_merge") or 0)
        _pilot_id=int(_cutover.get("pilot_campaign_id") or 0)
        _cutover_state=str(_cutover.get("state") or "")
        if _pilot_id and _cutover_state not in {
            "pilot_proved_scale_queued","new_feed_armed_waiting_old_removal",
            "new_feed_armed_after_scope_verified","completed"
        }:
            raise HTTPException(status_code=409,detail={
                "code":"cutover_pilot_not_proven",
                "message":"Масштабирование остановлено: сначала пилот должен получить подтверждённый успешный результат Avito.",
                "changed_avito":False,"pilot_campaign_id":_pilot_id,
                "cutover_state":_cutover_state,
            })

        _new_ids={str(getattr(x,"id","") or "").strip() for x in campaign_items}
        _new_ids.discard("")
        _old_rows=[r for r in _ff_items(db,_old_campaign_id,allowed)
                   if str(r.account_id or "")==str(target) and str(r.status or "")=="published"]
        _old_ids={str(getattr(r,"feed_identity","") or "").strip() for r in _old_rows}
        _old_ids.discard("")
        _old_avito_ids={str(getattr(r,"avito_item_id","") or "").strip() for r in _old_rows}
        _old_avito_ids.discard("")

        _preserve_campaign_ids=sorted({
            int(x) for x in (_cutover.get("preserve_campaign_ids") or [])
            if str(x).isdigit() and int(x)>0
        })
        _preserve_ids=set()
        for _pcid in _preserve_campaign_ids:
            _prows=[r for r in _ff_items(db,_pcid,allowed)
                    if str(r.account_id or "")==str(target)]
            if not _prows:
                raise HTTPException(status_code=409,detail={
                    "code":"cutover_preserve_campaign_missing",
                    "message":"Безопасная замена остановлена: сохранённая параллельная кампания не найдена.",
                    "changed_avito":False,"preserve_campaign_id":_pcid,
                })
            for _pr in _prows:
                _fid=str(getattr(_pr,"feed_identity","") or "").strip()
                if not _fid:
                    raise HTTPException(status_code=409,detail={
                        "code":"cutover_preserve_identity_missing",
                        "message":"Безопасная замена остановлена: у сохраняемого объявления нет feed identity.",
                        "changed_avito":False,"preserve_campaign_id":_pcid,
                    })
                _preserve_ids.add(_fid)

        _current_models=_load_feed_items(target)
        _current_ids={str(getattr(x,"id","") or "").strip() for x in _current_models}
        _current_ids.discard("")
        _expected_old_scope=_old_ids | _preserve_ids
        _expected_new_scope=_new_ids | _preserve_ids

        if (not _new_ids
                or len(_old_ids)!=len(_old_avito_ids) or not _old_ids
                or _preserve!=len(_preserve_ids)
                or _expected_total!=len(_expected_new_scope)
                or _current_ids not in (_expected_old_scope,_expected_new_scope)):
            raise HTTPException(status_code=409,detail={
                "code":"cutover_scope_not_proven",
                "message":"Безопасная замена остановлена: состав старого/нового/сохраняемого фида не доказан однозначно.",
                "changed_avito":False,
                "current_feed_items":len(_current_ids),"old_feed_items":len(_old_ids),
                "old_avito_ids":len(_old_avito_ids),"new_feed_items":len(_new_ids),
                "preserved_feed_items":len(_preserve_ids),
                "preserve_campaign_ids":_preserve_campaign_ids,
                "expected_feed_total":_expected_total,
                "preserve_existing_feed_items":_preserve,
            })

        # Provider read is evidence, not a prerequisite that old ids already be
        # gone. In a scheduled-URL cutover the authoritative first step is to
        # remove old identities from the canonical feed. Avito may report one
        # transition upload with duplicate 2010; the durable watcher then waits
        # for the next cycle instead of replaying or declaring false success.
        # CAMPAIGN_CUTOVER_SHARED_ACCOUNT_THROTTLE_V1: provider-scope proof is
        # read-only but still consumes tenant quota. Respect any shared Retry-After
        # before token/provider I/O and fail closed without mutating the canonical feed.
        from app.services.avito_account_throttle import account_throttle_remaining as _cutover_throttle_remaining, record_account_throttle as _cutover_record_throttle
        _cutover_retry=int(_cutover_throttle_remaining(target) or 0)
        if _cutover_retry>0:
            raise HTTPException(status_code=409,detail={
                "code":"cutover_provider_throttled",
                "message":"Безопасная замена отложена: Avito временно ограничил запросы по аккаунту.",
                "retry_after_seconds":_cutover_retry,"changed_avito":False,
            })
        _token_data=get_avito_token(target)
        if not isinstance(_token_data,dict) or not _token_data.get("access_token"):
            raise HTTPException(status_code=409,detail={
                "code":"cutover_provider_scope_unproven",
                "message":"Безопасная замена остановлена: BORIS не смог прочитать текущий состав аккаунта Avito.",
                "changed_avito":False,
            })
        _token=_extract_token(_token_data)
        _provider_ids=set()
        for _page in range(1,21):
            _cutover_retry=int(_cutover_throttle_remaining(target) or 0)
            if _cutover_retry>0:
                raise HTTPException(status_code=409,detail={
                    "code":"cutover_provider_throttled",
                    "message":"Безопасная замена отложена: Avito временно ограничил запросы по аккаунту.",
                    "retry_after_seconds":_cutover_retry,"changed_avito":False,
                })
            try:
                _rr=_avito_http_get(
                    "https://api.avito.ru/core/v1/items",
                    headers={"Authorization":f"Bearer {_token}"},
                    params={"per_page":100,"page":_page},
                )
            except Exception as _exc:
                raise HTTPException(status_code=409,detail={
                    "code":"cutover_provider_scope_unproven",
                    "message":"Безопасная замена остановлена: Avito временно не подтвердил состав объявлений.",
                    "error_type":type(_exc).__name__,"changed_avito":False,
                })
            if int(getattr(_rr,"status_code",0) or 0)!=200:
                if int(getattr(_rr,"status_code",0) or 0)==429:
                    _retry_after=30
                    try:
                        _raw_ra=str(_rr.headers.get("Retry-After") or "").strip()
                        if _raw_ra:
                            try:
                                _retry_after=max(0,int(float(_raw_ra)))
                            except Exception:
                                from email.utils import parsedate_to_datetime as _parse_cutover_ra
                                from datetime import datetime as _cutover_dt, timezone as _cutover_tz
                                _when=_parse_cutover_ra(_raw_ra)
                                if _when.tzinfo is None: _when=_when.replace(tzinfo=_cutover_tz.utc)
                                _retry_after=max(0,int((_when.astimezone(_cutover_tz.utc)-_cutover_dt.now(_cutover_tz.utc)).total_seconds()))
                    except Exception:
                        _retry_after=30
                    _cutover_retry=int(_cutover_record_throttle(target,_retry_after or 30,source="campaign_cutover_inventory_429") or (_retry_after or 30))
                    raise HTTPException(status_code=409,detail={
                        "code":"cutover_provider_throttled",
                        "message":"Безопасная замена отложена: Avito временно ограничил запросы по аккаунту.",
                        "retry_after_seconds":_cutover_retry,"changed_avito":False,
                    })
                raise HTTPException(status_code=409,detail={
                    "code":"cutover_provider_scope_unproven",
                    "message":"Безопасная замена остановлена: Avito временно не подтвердил состав объявлений.",
                    "http_status":int(getattr(_rr,"status_code",0) or 0),"changed_avito":False,
                })
            _resources=list((_rr.json() or {}).get("resources") or [])
            for _item in _resources:
                if (_item or {}).get("id") is not None:
                    _provider_ids.add(str((_item or {}).get("id")))
            if len(_resources)<100:
                break

        _old_still_present=sorted(_old_avito_ids & _provider_ids)
        if _current_ids==_expected_old_scope:
            _preserved_models=[x for x in _current_models
                               if str(getattr(x,"id","") or "").strip() in _preserve_ids]
            _save_feed_items(target,_preserved_models+campaign_items)

        _verify_ids={str(getattr(x,"id","") or "").strip() for x in _load_feed_items(target)}
        _verify_ids.discard("")
        if _verify_ids!=_expected_new_scope:
            raise HTTPException(status_code=409,detail={
                "code":"cutover_feed_replace_verify_failed",
                "message":"Безопасная замена остановлена: новый фид не совпал с подтверждённой кампанией и разрешённым preserve-scope.",
                "changed_avito":False,
                "expected":len(_expected_new_scope),"actual":len(_verify_ids),
            })

        import datetime as _dt_cutover
        _verified_at=_dt_cutover.datetime.now(_dt_cutover.timezone.utc).isoformat()
        _cutover.update({
            "state":"new_feed_armed_waiting_old_removal" if _old_still_present else "new_feed_armed_after_scope_verified",
            "provider_scope_checked_at":_verified_at,
            "old_provider_ids_still_present_before_cutover":len(_old_still_present),
            "old_provider_id_examples":_old_still_present[:10],
            "provider_scope_source":"core_v1_items_all_pages",
            "feed_replaced_with_new_identities_at":_verified_at,
            "feed_items_after_cutover":len(_verify_ids),
            "preserved_feed_items":len(_preserve_ids),
            "preserve_campaign_ids":_preserve_campaign_ids,
        })
        _campaign_settings["cutover"]=_cutover
        campaign.settings_json=json.dumps(_campaign_settings,ensure_ascii=False)
        db.commit(); db.refresh(campaign)
        _cutover_evidence={
            "verified":True,
            "old_provider_ids_still_present":len(_old_still_present),
            "old_feed_items_replaced":len(_old_ids),
            "new_feed_items":len(_new_ids),
            "preserved_feed_items":len(_preserve_ids),
            "preserve_campaign_ids":_preserve_campaign_ids,
            "final_feed_items":len(_verify_ids),
            "verified_at":_verified_at,
        }

    # CAMPAIGN_ACTIVATION_FEED_ATOMIC_V1: normal campaigns only upsert their
    # scope. Explicit replacement uses the guarded full replacement above.
    if not _explicit_cutover and not _targeted_replacement:
        _upsert_feed_items(target,campaign_items)
    import datetime as _dt_activation
    _pre_sent_at=_dt_activation.datetime.now(_dt_activation.timezone.utc).isoformat()
    # SUBMITTED_VALIDATED_ARTIFACT_V1: feed_url is a live account endpoint and can
    # change on a later revision. Persist the immutable XML artifact whose SHA was
    # officially validated so post-publication review never depends on mutable bytes.
    _,_submit_ff=_ff_state_load(campaign)
    _ready_xml=dict(_submit_ff.get("owner_ready_xml") or {})
    _validated_artifact=None
    if str(_ready_xml.get("sha256") or "").strip()==str(delivery.get("validated_sha") or "").strip():
        _validated_artifact=str(_ready_xml.get("artifact") or "").strip() or None
    _submitted_snapshot={"sha256":delivery.get("current_sha"),"validated_sha256":delivery.get("validated_sha"),
                         "validation_state":delivery.get("validation_state"),"semantic_ready":bool(delivery.get("semantic_ready")),
                         "cutover_evidence":_cutover_evidence,
                         "targeted_replacement_evidence":_targeted_replacement_evidence,
                         "semantic_blocked":int(delivery.get("semantic_blocked") or 0),"intake_ready":bool(delivery.get("intake_ready")),
                         "items":len(campaign_items),"approved":len(approved),"photos":delivery.get("photos"),"feed_url":delivery.get("feed_url"),
                         "validated_artifact":_validated_artifact,
                         "official_checked_at":delivery.get("last_validation"),"captured_at":_pre_sent_at}
    _,ff=_ff_state_load(campaign); ff["step"]="publication_processing"; ff["owner_activation"]={"status":"processing","account_id":target,"items":len(campaign_items),"submitted_snapshot":_submitted_snapshot,"updated_at":_pre_sent_at}; _ff_state_save(db,campaign,ff)
    result=feed_send_to_avito(target)
    _,ff=_ff_state_load(campaign); _sent_at=_dt_activation.datetime.now(_dt_activation.timezone.utc).isoformat()
    if result.get("status")=="ok":
        ff["step"]="publication_processing"; ff["owner_activation"]={"status":"processing","account_id":target,"items":len(campaign_items),"message":result.get("message") or "Avito обрабатывает фид","upload_id":result.get("upload_id"),"delivery_state":result.get("delivery_state") or "submitted","submitted_snapshot":_submitted_snapshot,"sent_at":_sent_at,"updated_at":_sent_at}
    else:
        ff["step"]="publication_failed"; ff["owner_activation"]={"status":"failed","account_id":target,"items":len(campaign_items),"message":result.get("message") or "Не удалось передать объявления","submitted_snapshot":_submitted_snapshot,"sent_at":_sent_at,"updated_at":_sent_at}
    _ff_state_save(db,campaign,ff)
    return {"status":result.get("status"),"campaign_id":campaign_id,"account_id":target,"items":len(campaign_items),
            "activation":ff["owner_activation"],"external_upload":result.get("delivery_state") in {"submitted","submitted_reconciled"},
            "delivery_state":result.get("delivery_state"),"upload_id":result.get("upload_id"),
            "baseline_upload_id":result.get("baseline_upload_id")}


@router.post("/{campaign_id}/safe_export")
def campaign_safe_export(campaign_id:int, db=Depends(_db), user=Depends(get_current_user)):
    import os,hashlib,datetime
    allowed=_allowed_accounts(db,user); campaign=_campaign_or_404(db,campaign_id,allowed)
    all_rows=_ff_items(db,campaign_id,allowed); rows=[r for r in all_rows if r.status=="ready"]
    _,ff_now=_ff_state_load(campaign); intake=_ff_direction_intake(db,campaign,ff_now,all_rows)
    if not intake.get("all_complete"):
        raise HTTPException(status_code=409,detail={"message":"Экспорт заблокирован: сначала завершите опрос по каждому направлению.","directions":[d.get("direction") for d in intake.get("directions") or [] if not d.get("complete")]})
    reports=[_ff_validate_row(r,db) for r in rows]; bad=[x for x in reports if not x["valid"]]
    if not rows: raise HTTPException(status_code=409,detail={"message":"Нет согласованных объявлений для экспорта","invalid":[],"not_approved":[r.id for r in all_rows]})
    if bad: raise HTTPException(status_code=409,detail={"message":"Экспорт заблокирован: в согласованных объявлениях есть ошибки","invalid":[x["id"] for x in bad],"not_approved":[]})
    # Safe export must use the same canonical MediaLink-aware projection that
    # official xmlcheck and owner publication use. Reading payload images here
    # would re-introduce a second gallery mapping path and could silently drop
    # linked media after media/photo-stack changes.
    _,xml=_ff_delivery_projection(rows,db); xml_sha=hashlib.sha256(xml.encode()).hexdigest()
    root="/root/BORIS/backend/data/feed_exports"; os.makedirs(root,exist_ok=True)
    # Multiple HA replicas may validate/export in the same second. Give every
    # response its own immutable artifact path so one replica can never overwrite
    # another response's file while it is being inspected.
    stamp=datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path=os.path.join(root,f"campaign_{campaign_id}_{stamp}_{xml_sha[:12]}.xml")
    with open(path,"x",encoding="utf-8") as f: f.write(xml)
    result={"status":"ok","external_upload":False,"campaign_id":campaign_id,"items":len(rows),"file":path,"sha256":xml_sha,"generated_at":datetime.datetime.now().isoformat()}
    try:
        _,ff=_ff_state_load(campaign)
        _official=ff.get("official_xml_check") or ff.get("official_xmlcheck") or {}
        _official_status=str(_official.get("status") or "").lower()
        _official_sha=str(_official.get("sha256") or "").strip()
        # "safe_feed" is a verified state, not merely "an XML file was written".
        # If content/media changed after Avito xmlcheck, keep the workflow at
        # validation and expose the stale SHA instead of presenting a green step.
        _verified=bool(_official_status in {"pass","ok"} and _official_sha and _official_sha==xml_sha)
        ff["step"]="safe_feed" if _verified else "validation"
        ff["last_export"]={k:result[k] for k in ("items","file","sha256","generated_at")}
        ff["last_export"]["official_validation_current"]=_verified
        ff["last_export"]["validated_sha256"]=_official_sha or None
        _ff_state_save(db,campaign,ff)
    except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=2859)
    return result


# ===== FEED FACTORY STATEFUL WORKFLOW V2 =====
# Mutation layer for CampaignItem. No endpoint below performs external Avito upload/publication.
class FFWorkflowBriefBody(BaseModel):
    brief: str = ""
    directions: list[str] = []
    cities: list[str] = []
    variants: int = 1
    quantity: int = 0
    price: int = 0
    defaults: dict = {}
    category_hints: dict = {}

class FFMaterializeBody(BaseModel):
    account_id: str = ""

class FFMutationBody(BaseModel):
    item_ids: list[int] = []
    patch: dict = {}
    media_ids: list[int] = []
    image_urls: list[str] = []
    hero_media_id: int | None = None

class FFTextBody(BaseModel):
    item_ids: list[int] = []
    instructions: str = ""
    goal: str = "message"
    length: str = "medium"
    # Background workers pass a stable per-item key so a crash after a paid
    # generation can reuse the durable result instead of buying it twice.
    idempotency_key: str = ""
    # Synchronous text generation is intentionally bounded. Larger explicit
    # batches require a second-step confirmation with the exact expected count.
    confirmed_paid_items: int = 0

class FFBannerBody(BaseModel):
    item_ids: list[int] = []
    goal: str = "offer"
    force: bool = False
    # Paid regeneration must be an explicit, unique owner intent. `force=True`
    # alone is not sufficient because a retried HTTP/job request could buy the
    # same banner twice after an ambiguous network/worker failure.
    idempotency_key: str = ""
    # Financial guard: a normal UI/job request may buy at most this many new
    # images. Larger batches require an explicit second-step confirmation with
    # the exact expected number of paid images.
    confirmed_paid_items: int = 0

class FFAutoMediaBody(BaseModel):
    item_ids: list[int] = []
    count_per_item: int = 1

class FFPhotoStackBody(BaseModel):
    item_ids: list[int] = []
    media_ids: list[int] = []
    randomize: bool = True
    after_banner: bool = True
    photos_per_item: int = 6

class FFEnhanceBody(BaseModel):
    item_ids: list[int] = []
    prompt: str = ""

class FFCompetitorCopyBody(BaseModel):
    query: str = ""
    city: str = ""
    count: int = 10
    item_ids: list[int] = []
    apply: bool = False

class FFPhotoAnglesBody(BaseModel):
    target_count: int = 20
    source_count: int = 5


class FFReferenceBody(BaseModel):
    links: list[str] = []
    text: str = ""
    mode: str = "inspired"
    media_ids: list[int] = []
    uniqueness_parameters: str = ""


def _ff_state_load(campaign):
    try:
        root = json.loads(campaign.settings_json or "{}")
    except Exception:
        root = {}
    if not isinstance(root, dict):
        root = {}
    ff = root.get("feed_factory") or {}
    if not isinstance(ff, dict):
        ff = {}
    return root, ff


def _ff_publication_locked(ff):
    """True while an owner-confirmed Avito publication is in-flight or terminal.

    Once a campaign has been handed to Avito, preparatory workflows must not mutate
    texts/media/category identity behind the submitted XML. Review/result tracking is
    still allowed; a fresh owner action is required for any later rework.
    """
    if not isinstance(ff, dict):
        return False
    activation=dict(ff.get("owner_activation") or {})
    status=str(activation.get("status") or "").lower()
    step=str(ff.get("step") or "").lower()
    return status in {"processing","scheduled","pending_review","completed","partial"} or step in {
        "publication_processing","publication_scheduled","publication_pending_review",
        "publication_completed","publication_partial"
    }


def _ff_assert_mutable(campaign):
    _, ff=_ff_state_load(campaign)
    if _ff_publication_locked(ff):
        raise HTTPException(status_code=409, detail={
            "code":"campaign_publication_locked",
            "message":"Кампания уже передана Avito. Подготовительные изменения заблокированы до завершения публикации.",
            "publication_status":str((ff.get("owner_activation") or {}).get("status") or ""),
            "step":str(ff.get("step") or ""),
        })
    return ff


def _ff_state_save(db, campaign, ff):
    import datetime as _dt
    root, _ = _ff_state_load(campaign)
    ff["updated_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
    root["feed_factory"] = ff
    campaign.settings_json = json.dumps(root, ensure_ascii=False)
    db.commit()
    db.refresh(campaign)
    # SQLAlchemy refresh() opens a new implicit transaction. Close it before
    # callers enter any external HTTP/AI boundary; otherwise a harmless refresh
    # can remain idle for the whole provider round-trip.
    db.commit()
    return ff


def _ff_account_context(db, account_id):
    row = db.execute(_t("SELECT name,company_niche,company_tone,company_description,company_advantages,company_website,company_client_description FROM accounts WHERE account_id=:a"), {"a": account_id}).fetchone()
    if not row:
        return {}
    return {"name": row[0] or "", "niche": row[1] or "", "tone": row[2] or "",
            "description": row[3] or "", "advantages": row[4] or "", "website": row[5] or "",
            "client_description": row[6] or ""}


def _ff_confirmed_price_default(db, account_id: str, direction: str) -> dict:
    """Return a conservative owner-confirmed campaign price, never an AI guess.

    Priority is explicit `campaign_price_benchmark` memory. The helper only accepts
    an `Ориентир ... N ₽` phrase from a fact semantically matching the direction;
    broad company turnover/check ranges are intentionally ignored.
    """
    import re as _re
    aid=str(account_id or "").strip(); text=str(direction or "").strip().lower()
    if not aid or not text:
        return {}
    semantic={
        "kitchen":("кухн",), "wardrobe":("гардероб",), "hallway":("прихож",),
        "bathroom":("ванн","сануз"), "plaster":("штукатур",),
    }
    words=_re.findall(r"[а-яёa-z0-9]+",text)
    stems=[w[:5] for w in words if len(w)>=4 and w not in {"заказ","услуг","товар","город"}]
    matched_families={family for family,aliases in semantic.items() if any(any(w.startswith(alias) for w in words) for alias in aliases)}
    rows=db.execute(_t("""
        SELECT id,category,name,value,source_ref,confirmed_at
        FROM client_facts
        WHERE account_id=:a AND status='confirmed' AND category='campaign_price_benchmark'
        ORDER BY updated_at DESC NULLS LAST,id DESC
        LIMIT 60
    """),{"a":aid}).mappings().all()
    for row in rows:
        name=str(row.get("name") or "").strip().lower(); value=str(row.get("value") or "").strip()
        # Named benchmark families are authoritative. If the direction maps to a
        # known family, never fall through to loose prose matching from another
        # family. For unknown directions, only compare stems against the fact name,
        # not its long value (cities and generic words there create false matches).
        relevant=(name in matched_families) if matched_families else bool(stems and any(s in name for s in stems))
        if not relevant:
            continue
        matches=list(_re.finditer(r"ориентир(?:\s+(?:кампании|карточки))?\s*[:—-]?\s*([0-9]+(?:[\s.,][0-9]+)*)\s*(тыс(?:\.|яч[аи]?)?)?\s*₽",value,_re.IGNORECASE))
        if not matches:
            continue
        m=matches[-1]; raw=m.group(1).replace(" ","").replace(",",".")
        try: number=float(raw)
        except Exception: continue
        if m.group(2): number*=1000
        price=int(round(number))
        if price<=0:
            continue
        return {"price":price,"source":"confirmed_campaign_price_benchmark","fact_id":int(row.get("id") or 0),"fact_name":name,"source_ref":str(row.get("source_ref") or "")}
    # Fallback to the client's own previously stored feed ONLY on an exact
    # normalized direction/title match. This is observed business data, not an AI
    # estimate, and safely heals categories where Avito later promotes Price to a
    # required field. Loose semantic matching is intentionally forbidden here.
    try:
        from app.models.storage import Storage
        _fr=db.query(Storage).filter(Storage.account_id==aid,Storage.key=="feed_items").first()
        _items=json.loads(_fr.value or "[]") if _fr else []
        def _norm(v):
            return " ".join(_re.findall(r"[а-яёa-z0-9]+",str(v or "").lower().replace("ё","е")))
        _target=_norm(direction)
        # An exact title is not enough when a legacy feed contains several cards
        # with the same title but different prices (cities/variants/promotions).
        # Such rows are ambiguous evidence: fail closed and ask the normal intake
        # instead of silently taking whichever duplicate happened to be first.
        _exact=[]
        for _it in (_items if isinstance(_items,list) else []):
            if not isinstance(_it,dict) or _norm(_it.get("title"))!=_target:
                continue
            try: _price=int(float(str(_it.get("price") or 0).replace(" ","").replace(",",".")))
            except Exception: _price=0
            if _price>0:
                _exact.append((_price,str(_it.get("id") or "")))
        _prices={x[0] for x in _exact}
        if len(_prices)==1 and _exact:
            _price,_source_ref=_exact[0]
            return {"price":_price,"source":"existing_account_feed_exact","source_ref":_source_ref,"fact_name":_target}
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=3084)
    return {}


def _ff_category_candidates(db, direction, limit=4, path_hint: str | None = None):
    from app.models.category_template import CategoryTreeLeaf
    import re as _re
    text = str(direction or "").strip().lower()
    # CATEGORY_PATH_HINT_EVIDENCE_V1: an exact GoodsType/category group already
    # observed in this client's feed is stronger than free-text token order. It
    # narrows candidate search but never chooses a leaf by itself.
    _path_hint = str(path_hint or "").strip()
    _category_stopwords={"для","под","над","при","без","или","как","это","эта","этот","ваш","ваша","ваши","наша","наши","услуга","услуги","товар","товары","заказ","заказать","продажа","продажи","купить","сделать","сделайте","получить"}
    words = [w for w in _re.findall(r"[0-9a-zа-яё]+", text) if len(w) >= 3 and w not in _category_stopwords]
    automotive = any(x in text for x in ("фар", "авто", "машин", "bmw", "mercedes", "мерседес", "стекл фар")) and any(x in text for x in ("ремонт", "замен", "восстанов", "полиров", "сервис"))
    diamond_drilling = any(x in text for x in ("алмазн", "бурен", "сверлен")) and any(x in text for x in ("бурен", "сверлен", "отверст", "бетон"))
    plaster_service = any(x in text for x in ("штукатур", "оштукатур")) and not any(x in text for x in ("смесь", "мешок", "материал", "продаж"))
    real_estate_service = any(x in text for x in ("квартир", "недвижимост", "ипотек", "ипотеч")) and any(x in text for x in ("подбор", "брокер", "риелтор", "риэлтор", "сопровожд", "консультац", "одобрен", "ипотек", "ипотеч"))
    broad_real_estate = any(x in text for x in ("недвижимост", "недвижимости")) and not any(x in text for x in ("квартир", "дом", "дач", "коттедж", "участ", "коммерч", "гараж", "машиномест", "комнат", "зарубеж", "аренд", "сдам", "сним", "продам", "куплю"))
    finance_service = any(x in text for x in ("ипотек", "рефинанс", "кредит", "залог недвижим"))
    paver_product = any(x in text for x in ("брусчат", "тротуарн") ) and not any(x in text for x in ("уклад", "монтаж", "услуг"))
    paving_border_product = any(x in text for x in ("бордюр", "поребрик")) and any(x in text for x in ("садов", "тротуар", "дорож", "бетон", "поребрик", "производител")) and not any(x in text for x in ("уклад", "монтаж", "услуг"))
    ebike_product = any(x in text for x in ("электровелосип", "электробайк", "велогибрид"))
    concrete_product = (("товарн" in text and "бетон" in text) or any(x in text for x in ("бетон м", "бетон в", "бетон от", "бетон и раствор")))
    hydro_gloves_product = any(x in text for x in ("гидроперчат", "перчатк jetpilot", "перчатки jetpilot", "неопренов", "jetpilot vault")) and any(x in text for x in ("перчат", "неопрен"))
    water_accessory_product = any(x in text for x in ("очки для гидро", "очки jetpilot", "vault vision", "jetpilot shield"))
    shower_partition_product = ("перегород" in text or "огражден" in text) and any(x in text for x in ("душ", "душев"))
    interior_partition_product = "перегород" in text and any(x in text for x in ("межкомнат", "офисн", "стекл")) and not shower_partition_product
    bed_product = "кроват" in text
    custom_song_service = any(x in text for x in ("песня", "песню", "музыкальн подарок")) and any(x in text for x in ("заказ", "подар", "сюрприз", "поздрав"))
    cabin_product = any(x in text for x in ("бытовк", "блок-контейнер")) and not any(x in text for x in ("аренд", "сдам", "прокат"))
    furniture_leaf = None
    if "кух" in text and any(x in text for x in ("мебел", "гарнитур", "заказ", "кухн")):
        furniture_leaf = "Кухни"
    elif "прихож" in text:
        furniture_leaf = "Прихожие и обувницы"
    elif "гардероб" in text:
        furniture_leaf = "Гардеробные системы и вешалки"
    elif "ванн" in text and "мебел" in text:
        furniture_leaf = "Мебель для ванной"
    q = db.query(CategoryTreeLeaf).filter(CategoryTreeLeaf.template_id.isnot(None))
    if _path_hint:
        q = q.filter(CategoryTreeLeaf.path.ilike("%" + _path_hint + "%"))
    if finance_service:
        q = q.filter(CategoryTreeLeaf.template_id == "133102")
    elif paver_product:
        q = q.filter(CategoryTreeLeaf.template_id == "111166")
    elif paving_border_product:
        q = q.filter(CategoryTreeLeaf.template_id == "107132")
    elif ebike_product:
        q = q.filter(CategoryTreeLeaf.template_id == "126420")
    elif concrete_product:
        q = q.filter(CategoryTreeLeaf.template_id == "90917")
    elif hydro_gloves_product:
        q = q.filter(CategoryTreeLeaf.template_id == "119484")
    elif water_accessory_product:
        q = q.filter(CategoryTreeLeaf.template_id == "119478")
    elif shower_partition_product:
        q = q.filter(CategoryTreeLeaf.template_id == "107907")
    elif interior_partition_product:
        q = q.filter(CategoryTreeLeaf.template_id == "106836")
    elif bed_product:
        q = q.filter(CategoryTreeLeaf.template_id == "68070")
    elif custom_song_service:
        q = q.filter(CategoryTreeLeaf.template_id == "125593")
    elif cabin_product:
        q = q.filter(CategoryTreeLeaf.template_id == "90576")
    elif automotive:
        q = q.filter(CategoryTreeLeaf.path.ilike("Услуги > Предложение услуг > Автосервис, аренда > Автосервисы для автомобилей%"))
    elif diamond_drilling:
        q = q.filter(CategoryTreeLeaf.path.ilike("Услуги > Предложение услуг > Ремонт и отделка > Другое%"))
    elif plaster_service:
        q = q.filter(CategoryTreeLeaf.path.ilike("Услуги > Предложение услуг > Ремонт и отделка > Штукатурные работы%"))
    elif real_estate_service:
        # Broker/agent/mortgage services are services, not property-sale listings.
        # Keep only semantically relevant intermediary leaves; never show tourism/import/buyout noise.
        q = q.filter(CategoryTreeLeaf.path.ilike("Услуги > Предложение услуг > Услуги посредников >%"),
                     CategoryTreeLeaf.leaf_name.in_(["Консультации", "Помощь с финансовыми вопросами"]))
    elif broad_real_estate:
        q = q.filter(CategoryTreeLeaf.path.ilike("Недвижимость >%"))
    elif furniture_leaf:
        q = q.filter(CategoryTreeLeaf.leaf_name == furniture_leaf)
    elif words:
        q = q.filter(CategoryTreeLeaf.path.ilike("%" + words[0] + "%"))
    rows = q.limit(250).all()
    if not rows and words and not real_estate_service:
        # Never return the first arbitrary 250 taxonomy rows: that used to surface
        # nonsense such as «Автодома» for copywriting or concrete. The taxonomy is
        # small (~1.6k leaves), so score the current allowed subtree and keep only
        # rows with actual lexical/semantic evidence below. A proven path_hint must
        # survive fallback; dropping it here would re-open cross-tree guesses.
        _fallback_q = db.query(CategoryTreeLeaf).filter(CategoryTreeLeaf.template_id.isnot(None))
        if _path_hint:
            _fallback_q = _fallback_q.filter(CategoryTreeLeaf.path.ilike("%" + _path_hint + "%"))
        rows = _fallback_q.all()
    scored = []
    for r in rows:
        path = str(r.path or "").lower()
        score = sum(3 if w in path else 0 for w in words)
        if finance_service and str(r.template_id or "") == "133102": score += 50
        if paver_product and str(r.template_id or "") == "111166": score += 50
        if paving_border_product and str(r.template_id or "") == "107132": score += 50
        if ebike_product and str(r.template_id or "") == "126420": score += 50
        if concrete_product and str(r.template_id or "") == "90917": score += 50
        if hydro_gloves_product and str(r.template_id or "") == "119484": score += 50
        if water_accessory_product and str(r.template_id or "") == "119478": score += 50
        if shower_partition_product and str(r.template_id or "") == "107907": score += 50
        if interior_partition_product and str(r.template_id or "") == "106836": score += 50
        if bed_product and str(r.template_id or "") == "68070": score += 50
        if custom_song_service and str(r.template_id or "") == "125593": score += 50
        if cabin_product and str(r.template_id or "") == "90576": score += 50
        if automotive and ("диагностика и ремонт авто" in path or "кузовной ремонт" in path): score += 20
        if diamond_drilling and "ремонт и отделка > другое" in path: score += 30
        if plaster_service and "штукатурные работы" in path: score += 40
        if furniture_leaf and str(r.leaf_name or "").strip().lower() == furniture_leaf.lower(): score += 40
        if real_estate_service:
            if "услуги посредников" in path: score += 20
            if "консультации" in path: score += 16
            if ("ипот" in text or "ипотеч" in text) and "финансов" in path: score += 24
            if "ипот" not in text and "ипотеч" not in text and "финансов" in path: score += 4
            if any(x in path for x in ("туристические услуги","поставка товаров","услуги выкупа")): score -= 30
        if text and text in path: score += 10
        scored.append((score, r))
    scored.sort(key=lambda x: (-x[0], len(str(x[1].path or ""))))
    out = []
    for score, r in scored:
        if score <= 0:
            continue
        out.append({"template_id": str(r.template_id or ""), "path": r.path or "",
                    "leaf_name": r.leaf_name or "", "score": score})
        if len(out) >= limit:
            break
    return out


def _ff_direction_requires_category_choice(direction: str) -> bool:
    text=str(direction or "").strip().lower()
    if any(x in text for x in ("квартир", "недвижимост", "ипотек", "ипотеч")) and any(x in text for x in ("подбор", "брокер", "риелтор", "риэлтор", "сопровожд", "консультац", "одобрен", "ипотек", "ипотеч")):
        return True
    if any(x in text for x in ("недвижимост", "недвижимости")) and not any(x in text for x in ("квартир", "дом", "дач", "коттедж", "участ", "коммерч", "гараж", "машиномест", "комнат", "зарубеж", "аренд", "сдам", "сним", "продам", "куплю")):
        return True
    return False


def _ff_direction_is_broad_category(direction: str) -> bool:
    text=str(direction or "").strip().casefold()
    if not text: return True
    if ("корпусн" in text and "мебел" in text) or ("мягк" in text and "мебел" in text): return True
    if text in {"мебель", "мебель на заказ", "бытовая техника", "электроника", "стройматериалы", "строительные товары и материалы"}: return True
    if "экипиров" in text and "водн" in text and "спорт" in text: return True
    return False


def _ff_auto_category_candidate(direction: str, candidates: list[dict]) -> dict:
    """Fail closed: auto-pick Avito category only when ranking has real semantic evidence."""
    if not candidates or _ff_direction_is_broad_category(direction):
        return {}
    top=dict(candidates[0] or {})
    try: score=int(top.get("score") or 0)
    except Exception: score=0
    # CATEGORY_TIE_FAIL_CLOSED_V1: database row order is not semantic evidence.
    # If two different leaves have the same top score, do not pick the first one
    # by accident. Existing feed/category evidence can narrow the candidate set
    # before this function; otherwise the workflow must ask/derive more evidence.
    if len(candidates) > 1:
        second=dict(candidates[1] or {})
        try: second_score=int(second.get("score") or 0)
        except Exception: second_score=0
        if second_score == score and str(second.get("template_id") or "") != str(top.get("template_id") or ""):
            return {}
    # Real-estate/finance wording used to force an owner choice even when the
    # constrained scorer had overwhelming evidence. Keep fail-closed behavior for
    # weak/ambiguous matches, but do not turn a 40+ confidence service match into
    # unnecessary operator work.
    if _ff_direction_requires_category_choice(direction) and score < 20:
        return {}
    text=str(direction or "").strip().lower().replace("ё","е")
    path=str(top.get("path") or "").lower().replace("ё","е")
    leaf=str(top.get("leaf_name") or "").lower().replace("ё","е")
    words=[w for w in __import__('re').findall(r"[а-яa-z0-9]+",text) if len(w)>=5]
    lexical=any((w in leaf or w[:6] in leaf or w in path) for w in words)
    # Strong rank, explicit phrase match, or a modest score backed by lexical identity.
    if score >= 8 or (text and text in path) or (score >= 3 and lexical):
        return top
    return {}


def _ff_contract_missing(db, template_id, params):
    from app.api.feed_contract import resolve_feed_contract, _required_now, SYSTEM_FIELDS
    contract = resolve_feed_contract(template_id, _db=db)
    missing = []
    if not contract.get("ok"):
        return missing
    params = params or {}
    fields = contract.get("fields") or {}
    for name, spec in fields.items():
        if name in SYSTEM_FIELDS:
            continue
        if name in params or not _required_now(name, spec, fields, params):
            continue
        allowed = spec.get("allowed") or []
        if len(allowed) == 1:
            continue
        missing.append({"field": name, "label": spec.get("name_ru") or name,
                        "allowed_values": allowed, "multiple": bool(spec.get("multiple")), "template_id": str(template_id)})
    return missing


def _ff_questions(db, campaign, ff):
    """Ask only facts that are truly required to continue. Marketing niceties never block zero-touch."""
    questions = []
    account_id = ff.get("account_id") or campaign.default_account_id or ""
    ctx = _ff_account_context(db, account_id)
    defaults = ff.get("defaults") or {}
    cities = ff.get("cities") or []
    directions = ff.get("directions") or []
    if not cities:
        # Recover geography from already materialized campaign cards. A saved card
        # is authoritative enough to avoid asking the owner the same city again.
        try:
            _city_rows=_ff_items(db,campaign.id,_allowed_accounts_for_campaign_rows(db,campaign))
            _recovered=[]
            for _r in _city_rows:
                _p=_ff_payload(_r)
                _c=str(_r.geo or _p.get("city") or _p.get("geo") or "").strip()
                if _c and _c not in _recovered: _recovered.append(_c)
            if _recovered:
                cities=_recovered
                ff["cities"]=_recovered
                _ff_state_save(db,campaign,ff)
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=3315)
    if not cities:
        questions.append({"key": "cities", "label": "В каких городах размещать объявления?", "type": "list"})
    # Price is NOT a universal publication requirement. Never stop a campaign merely
    # because the prompt did not contain a price; ask it only if the selected Avito
    # category contract explicitly requires a price-like category field.
    seen = set()
    direction_state = dict(ff.get("direction_intake") or {})
    try:
        current_rows=_ff_items(db,campaign.id,_allowed_accounts_for_campaign_rows(db,campaign))
    except Exception:
        current_rows=[]
    for row in ff.get("matrix") or []:
        tid = str(row.get("template_id") or "")
        direction = str(row.get("direction") or "")
        if not tid:
            continue
        entry = dict(direction_state.get(direction) or {})
        answer_params = dict(defaults.get("params") or {})
        # Existing materialized cards are authoritative for facts already applied
        # consistently to every item in the direction. Do not ask the owner again.
        answer_params.update(_ff_intake_current_common(current_rows,direction))
        for name, answer in (entry.get("answers") or {}).items():
            if isinstance(answer, dict) and str(answer.get("mode") or "exact") == "exact" and answer.get("value") not in (None, ""):
                answer_params[name] = answer.get("value")
        # Legacy campaigns may have matrix directions whose intake state was never
        # persisted. Re-read identical params directly from their existing cards.
        _matching=[r for r in current_rows if str(r.segment or _ff_payload(r).get("direction") or "")==direction]
        if _matching:
            _param_maps=[dict(_ff_payload(r).get("params") or {}) for r in _matching]
            _keys=set.intersection(*(set(x.keys()) for x in _param_maps)) if _param_maps else set()
            for _k in _keys:
                _vals=[json.dumps(x.get(_k),ensure_ascii=False,sort_keys=True) if isinstance(x.get(_k),(list,dict)) else str(x.get(_k)) for x in _param_maps]
                if _vals and len(set(_vals))==1 and _param_maps[0].get(_k) not in (None,""):
                    answer_params[_k]=_param_maps[0].get(_k)
        for field in _ff_contract_missing(db, tid, answer_params):
            uniq = (tid, field["field"])
            if uniq in seen:
                continue
            seen.add(uniq)
            _question_label=("Как вы работаете: в офисе, онлайн или с выездом?" if field["field"]=="Place" else "Для категории нужно поле «%s»" % field["label"])
            questions.append({"key": "param:" + field["field"], "label": _question_label,
                              "type": "multienum" if field.get("multiple") and field["allowed_values"] else ("enum" if field["allowed_values"] else "text"),
                              "multiple": bool(field.get("multiple")),
                              "allowed_values": field["allowed_values"], "template_id": tid,
                              "direction": direction})
    return questions[:40]


def _ff_intake_field_public(name, spec):
    rule=str(spec.get("format_rule") or ""); allowed=list(spec.get("allowed") or [])
    numeric=any(x in rule.lower() for x in ("число","number","целое","integer")) and not allowed
    multiple=bool(spec.get("multiple"))
    return {"field":name,"label":spec.get("name_ru") or name,"required":bool(spec.get("required")),
            "allowed_values":allowed,"format_rule":rule,"depends_on":spec.get("depends_on") or "",
            "multiple":multiple,
            "input_type":"multienum" if allowed and multiple else ("enum" if allowed else ("number" if numeric else "text")),"range_allowed":bool(numeric)}


def _ff_account_product_origin_defaults(db, account_id: str, contract_fields: dict) -> dict:
    """Infer only low-risk product-origin fields from persisted company evidence.

    This is deliberately conservative: no web request occurs here. BORIS may use
    the account profile plus previously persisted public-business evidence. A field
    is auto-filled only when the evidence clearly says the company manufactures /
    makes products to order and the value is one of the official Avito enum values.
    """
    account_id=str(account_id or "").strip()
    if not account_id or not contract_fields:
        return {}
    text_parts=[]
    try:
        from app.models.account import Account
        acc=db.query(Account).filter(Account.account_id==account_id).first()
        if acc:
            text_parts += [str(getattr(acc,k,None) or "") for k in ("company_niche","company_description","company_advantages","company_client_description")]
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=3392)
    try:
        from app.models.storage import Storage
        ev=db.query(Storage).filter(Storage.account_id==account_id,Storage.key=="public_business_evidence_v1").first()
        if ev and ev.value:
            text_parts.append(str(ev.value))
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=3399)
    low=" ".join(text_parts).lower().replace("ё","е")
    manufacturer=any(x in low for x in ("собственное производство","мы производим","производитель","изготовление изделий","изготавливаем","изготовление стеклянных","изготовление конструкций"))
    made_to_order=any(x in low for x in ("на заказ","под ваши размеры","индивидуальное изготовление","изготавливаем"))
    out={}
    def _enum_pick(field, predicate):
        spec=dict(contract_fields.get(field) or {}); allowed=list(spec.get("allowed") or [])
        for value in allowed:
            if predicate(str(value).lower().replace("ё","е")):
                return value
        return None
    if manufacturer:
        ad_type=_enum_pick("AdType",lambda x:"производ" in x)
        if ad_type is not None:
            out["AdType"]={"mode":"exact","value":ad_type,"source":"account_manufacturer_evidence"}
    if manufacturer and made_to_order:
        condition=_enum_pick("Condition",lambda x:x.strip()=="новое" or x.startswith("нов"))
        if condition is not None:
            out["Condition"]={"mode":"exact","value":condition,"source":"account_manufacturer_evidence"}
        availability=_enum_pick("Availability",lambda x:"под заказ" in x)
        if availability is not None:
            out["Availability"]={"mode":"exact","value":availability,"source":"account_made_to_order_evidence"}
    return out


def _ff_intake_current_common(rows, direction):
    """Read-only hint from existing cards; never treated as client confirmation."""
    vals={}; selected=[r for r in rows if str(r.segment or _ff_payload(r).get("direction") or "")==str(direction)]
    if not selected: return {}
    keys=set()
    parsed=[]
    for r in selected:
        p=_ff_payload(r); pp=dict(p.get("params") or {}); parsed.append(pp); keys.update(pp)
    for k in keys:
        raw_seq=[p.get(k) for p in parsed if p.get(k) not in (None,"")]
        canon=[json.dumps(v,ensure_ascii=False,sort_keys=True) if isinstance(v,(list,dict)) else str(v) for v in raw_seq]
        if canon and len(canon)==len(parsed) and len(set(canon))==1: vals[k]=raw_seq[0]
    return vals


def _ff_profile_key(direction: str) -> str:
    return "feed_direction_profile:" + str(direction or "").strip().lower()


def _ff_profile_load(db, account_id: str, direction: str) -> dict:
    if not account_id or not direction:
        return {}
    from app.models.storage import Storage
    row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == _ff_profile_key(direction)).first()
    if not row:
        return {}
    try:
        value = json.loads(row.value or "{}")
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _ff_profile_save(db, account_id: str, direction: str, entry: dict) -> None:
    if not account_id or not direction:
        return
    from app.models.storage import Storage
    key = _ff_profile_key(direction)
    payload = dict(entry or {})
    payload["direction"] = direction
    payload["account_id"] = account_id
    payload["memory_scope"] = "account_direction"
    row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == key).first()
    raw = json.dumps(payload, ensure_ascii=False)
    if row:
        row.value = raw
    else:
        db.add(Storage(account_id=account_id, key=key, value=raw))


def _ff_profile_load_compatible(db, account_id: str, direction: str, template_id: str) -> dict:
    """Reuse confirmed intake for a close synonym in the same Avito leaf.

    Exact direction memory remains first priority. Compatible reuse requires the
    same template_id AND at least two meaningful lexical stems in common, so
    «Механизированная штукатурка квартир» can reuse the already confirmed seller
    facts for «Механизированная штукатурка стен» without leaking settings across
    unrelated services that happen to live in the same broad category.
    """
    import re as _re
    from app.models.storage import Storage
    aid=str(account_id or "").strip(); direction=str(direction or "").strip(); tid=str(template_id or "").strip()
    if not aid or not direction or not tid:
        return {}
    stop={"заказ","услуг","услуга","товар","работ","работа","город","квартир","квартира","стен","стена"}
    def terms(value):
        return {w[:6] for w in _re.findall(r"[а-яёa-z0-9]+",str(value or "").lower()) if len(w)>=5 and w not in stop}
    target=terms(direction)
    if not target:
        return {}
    rows=(db.query(Storage).filter(Storage.account_id==aid,Storage.key.like("feed_direction_profile:%")).all())
    best=None; best_score=0
    for row in rows:
        try: obj=json.loads(row.value or "{}")
        except Exception: continue
        if not isinstance(obj,dict) or not obj.get("confirmed") or str(obj.get("template_id") or "")!=tid:
            continue
        source_direction=str(obj.get("direction") or "").strip(); common=target & terms(source_direction); score=len(common)
        if score>=2 and score>best_score:
            best=dict(obj); best_score=score
    if not best:
        return {}
    best["memory_scope"]="account_template_semantic"
    best["memory_from_direction"]=str(best.get("direction") or "")
    best["direction"]=direction
    return best


def _ff_hydrate_profiles(db, campaign, ff: dict) -> dict:
    """Copy confirmed account memory into a new campaign once, preserving campaign overrides."""
    account_id = campaign.default_account_id or ff.get("account_id") or ""
    state = dict(ff.get("direction_intake") or {})
    matrix=list(ff.get("matrix") or [])
    for direction in [str(x).strip() for x in (ff.get("directions") or []) if str(x).strip()]:
        if direction in state:
            continue
        remembered = _ff_profile_load(db, account_id, direction)
        if not (remembered.get("confirmed") and remembered.get("template_id")):
            proto=next((x for x in matrix if str(x.get("direction") or "")==direction),{})
            remembered=_ff_profile_load_compatible(db,account_id,direction,str(proto.get("template_id") or ""))
        if remembered.get("confirmed") and remembered.get("template_id"):
            # Newer account-level negative commercial rules outrank an older
            # direction profile. Example: a previously saved «Гарантия: Есть» must
            # never survive after the company profile explicitly says «гарантию не
            # обещать / условия подтверждает менеджер». For Avito's binary enum the
            # safe truthful projection is «Нет» — BORIS does not advertise a promise
            # the owner has explicitly withdrawn.
            _ctx=_ff_account_context(db,account_id)
            _policy_text=(str(_ctx.get("description") or "")+" "+str(_ctx.get("advantages") or "")).lower()
            if ("гарант" in _policy_text and (("не обещ" in _policy_text) or ("не гарант" in _policy_text))):
                _answers=dict(remembered.get("answers") or {})
                if "Guarantee" in _answers:
                    _answers["Guarantee"]={"mode":"exact","value":"Нет","source":"account_negative_rule"}
                    remembered={**remembered,"answers":_answers,"policy_override":"guarantee_not_promised"}
            state[direction] = remembered
    ff["direction_intake"] = state
    return ff


def _ff_direction_intake(db, campaign, ff, rows=None):
    """One-time direction-scoped Avito intake with reusable account memory."""
    from app.api.feed_contract import resolve_feed_contract, _required_now, SYSTEM_FIELDS
    rows=rows if rows is not None else _ff_items(db,campaign.id,_allowed_accounts_for_campaign_rows(db,campaign))
    state=dict(ff.get("direction_intake") or {})
    directions=[str(x).strip() for x in (ff.get("directions") or []) if str(x).strip()]
    if not directions:
        directions=[]
        for r in rows:
            d=str(r.segment or _ff_payload(r).get("direction") or "").strip()
            if d and d not in directions: directions.append(d)
    matrix=ff.get("matrix") or []
    out=[]; all_complete=bool(directions)
    for direction in directions:
        remembered = _ff_profile_load(db, campaign.default_account_id or ff.get("account_id") or "", direction)
        entry=dict(remembered)
        entry.update(dict(state.get(direction) or {}))
        proto=next((x for x in matrix if str(x.get("direction") or "")==direction),{})
        proto_candidates=list(proto.get("category_candidates") or [])
        if proto_candidates and max([float(x.get("score") or 0) for x in proto_candidates] or [0]) > 0:
            candidates=proto_candidates
        else:
            candidates=_ff_category_candidates(db,direction,limit=8)
        explicit_choice=_ff_direction_requires_category_choice(direction)
        auto_candidate=_ff_auto_category_candidate(direction,candidates)
        template_id=str(entry.get("template_id") or ("" if explicit_choice else proto.get("template_id")) or ("" if explicit_choice else auto_candidate.get("template_id")) or "")
        # Existing cards can predate direction_intake. If every card in this
        # direction already carries the same official template/category, reuse it
        # instead of forcing a category question or running fuzzy matching again.
        _dir_rows=[r for r in rows if str(r.segment or _ff_payload(r).get("direction") or "")==direction]
        _existing_tids=[]; _existing_paths=[]
        for _r in _dir_rows:
            _rp=_ff_payload(_r); _tid=str(_rp.get("template_id") or "").strip(); _path=str(_rp.get("category_path") or "").strip()
            if _tid: _existing_tids.append(_tid)
            if _path: _existing_paths.append(_path)
        if _existing_tids and len(set(_existing_tids))==1:
            template_id=_existing_tids[0]
            explicit_choice=False
            if _existing_paths and len(set(_existing_paths))==1:
                proto={**proto,"category_path":_existing_paths[0]}
        template_refresh={"status":"missing","refreshed":False,"fields":0}
        if template_id:
            try:
                from app.services.category_resolver import refresh_category_template_if_stale
                template_refresh=refresh_category_template_if_stale(template_id,db,max_age_days=7)
            except Exception:
                template_refresh={"status":"stale_cached","refreshed":False,"fields":0}
        contract=resolve_feed_contract(template_id,_db=db) if template_id else {"ok":False,"fields":{},"category_path":"","contract_version":""}
        if template_id:
            contract=_ff_contract_with_payload_overlay(db,{"template_id":template_id},contract,account_id=str(campaign.default_account_id or ff.get("account_id") or ""))
        # Authenticated User Docs are authoritative when the public cached template
        # is incomplete (common for new Avito service enums). Generic across accounts.
        if template_id and contract.get("ok"):
            try:
                from app.services.category_resolver import refresh_category_template_from_account_docs
                _account_docs=refresh_category_template_from_account_docs(template_id,contract.get("category_path") or proto.get("category_path") or "",campaign.default_account_id or ff.get("account_id") or "",db)
                if _account_docs.get("refreshed"):
                    contract=resolve_feed_contract(template_id,_db=db)
                    template_refresh={**template_refresh,"account_docs":_account_docs}
            except Exception as _suppressed_exc:
                observe_suppressed(__name__, _suppressed_exc, line=3603)
        # If every existing card in this direction carries the same campaign-scoped
        # live contract overlay, use that overlay for intake too. This keeps official
        # Avito select options visible in the UI without mutating a legacy global
        # CategoryTemplate that may still be referenced by older campaigns.
        _overlay_rows=[_r for _r in _dir_rows if dict(_ff_payload(_r).get("live_contract_overlay") or {})]
        if _dir_rows and len(_overlay_rows)==len(_dir_rows):
            _overlay_versions={str((dict(_ff_payload(_r).get("live_contract_overlay") or {})).get("contract_version") or "") for _r in _overlay_rows}
            if len(_overlay_versions)==1:
                contract=_ff_contract_with_payload_overlay(db,_ff_payload(_overlay_rows[0]),contract)
                template_refresh={**template_refresh,"scoped_overlay":next(iter(_overlay_versions))}
        fields=[_ff_intake_field_public(n,s) for n,s in (contract.get("fields") or {}).items() if n not in SYSTEM_FIELDS]
        answers=dict(entry.get("answers") or {}); mode=str(entry.get("mode") or "shared"); confirmed=bool(entry.get("confirmed"))
        # Answers already given in the one compact campaign-level question step satisfy
        # matching Avito fields here; do not ask the same fact again per direction.
        for _name,_value in dict((ff.get("defaults") or {}).get("params") or {}).items():
            if _name in (contract.get("fields") or {}) and _name not in answers and _value not in (None,""):
                answers[_name]={"mode":"exact","value":_value,"source":"campaign_answer"}
        # A single official enum value is a category constant, not a client fact.
        # Auto-correct stale/legacy guesses to that exact value and never ask the owner.
        for _name,_spec in (contract.get("fields") or {}).items():
            _allowed=list(_spec.get("allowed") or [])
            if len(_allowed)==1:
                _ans=answers.get(_name)
                _cur=(_ans.get("value") if isinstance(_ans,dict) else None)
                if str(_cur or "") != str(_allowed[0]):
                    answers[_name]={"mode":"exact","value":_allowed[0],"source":"official_single_value"}

        # A materialized campaign may already contain authoritative values in every
        # card of this direction (e.g. an older campaign prepared before intake memory
        # existed). Reuse only values that are identical across ALL direction cards.
        for _name,_value in _ff_intake_current_common(rows,direction).items():
            if _name in (contract.get("fields") or {}) and _name not in answers and _value not in (None,""):
                answers[_name]={"mode":"exact","value":_value,"source":"existing_cards"}
        # Low-risk account evidence can satisfy generic product-origin fields without
        # turning the owner into an operator. Explicit campaign answers and existing
        # card values always win over this evidence-derived fallback.
        for _name,_answer in _ff_account_product_origin_defaults(db,campaign.default_account_id or ff.get("account_id") or "",contract.get("fields") or {}).items():
            if _name not in answers:
                answers[_name]=_answer
        missing=[]
        if mode=="shared":
            params={}
            for name,spec in (contract.get("fields") or {}).items():
                if name in SYSTEM_FIELDS: continue
                ans=answers.get(name)
                if isinstance(ans,dict) and ans.get("mode")=="exact" and ans.get("value") not in (None,""):
                    params[name]=ans.get("value")
            for name,spec in (contract.get("fields") or {}).items():
                if name in SYSTEM_FIELDS: continue
                if not _required_now(name,spec,contract.get("fields") or {},params): continue
                allowed=spec.get("allowed") or []; ans=answers.get(name)
                if len(allowed)==1 and not ans: continue
                if not isinstance(ans,dict): missing.append(name); continue
                kind=str(ans.get("mode") or "exact")
                if kind=="range":
                    if not _ff_intake_field_public(name,spec).get("range_allowed") or ans.get("min") in (None,"") or ans.get("max") in (None,""):
                        missing.append(name)
                elif ans.get("value") in (None,""):
                    missing.append(name)
        else:
            # Per-card mode becomes complete only after every materialized card has valid exact contract params.
            dir_rows=[r for r in rows if str(r.segment or _ff_payload(r).get("direction") or "")==direction]
            if not dir_rows: missing.append("__per_item_not_materialized__")
            else:
                from app.api.feed_contract import validate_feed_item
                for r in dir_rows:
                    p=_ff_payload(r); tid=str(p.get("template_id") or template_id or ""); c=resolve_feed_contract(tid,_db=db)
                    vr=validate_feed_item({"id":str(r.id),"template_id":tid,"params":dict(p.get("params") or {})},c)
                    if not vr.get("valid"): missing.append("item:"+str(r.id))
        # Safe auto-confirm: category has passed confidence guard and every currently
        # required Avito field is known. Client confirmation is reserved for unknown facts.
        if not confirmed and template_id and contract.get("ok") and not missing and not explicit_choice:
            confirmed=True
        complete=bool(confirmed and contract.get("ok") and not missing)
        all_complete=all_complete and complete
        answered_fields=sum(1 for a in answers.values() if isinstance(a,dict) and (a.get("value") not in (None,"") or (a.get("mode")=="range" and a.get("min") not in (None,"") and a.get("max") not in (None,""))))
        dependent_fields=sum(1 for f in fields if f.get("depends_on"))
        required_fields=sum(1 for f in fields if f.get("required"))
        out.append({"direction":direction,"mode":mode,"confirmed":confirmed,"complete":complete,"missing":missing,
                    "template_id":template_id,"category_path":contract.get("category_path") or proto.get("category_path") or "",
                    "contract_version":contract.get("contract_version") or "","candidates":candidates,"fields":fields,"answers":answers,
                    "template_refresh":template_refresh,
                    "field_stats":{"available":len(fields),"required":required_fields,"dependent":dependent_fields,"answered":answered_fields},
                    "current_common":_ff_intake_current_common(rows,direction),
                    "notice":"BORIS сохраняет все заполненные параметры этой подкатегории — обязательные, дополнительные и зависимые — и передаёт применимые значения в каждую карточку и XML-фид."})
    return {"status":"ok","campaign_id":campaign.id,"all_complete":all_complete,"directions":out,"one_time_notice":"Это базовая настройка направления: обычно её достаточно заполнить один раз. При необходимости можно выбрать индивидуальные параметры для каждой карточки."}


def _allowed_accounts_for_campaign_rows(db,campaign):
    """Internal helper for intake read; strictly limited to campaign's own item accounts/default."""
    from app.models.campaign_item import CampaignItem
    ids=[r[0] for r in db.query(CampaignItem.account_id).filter(CampaignItem.campaign_id==campaign.id).distinct().all() if r[0]]
    if campaign.default_account_id and campaign.default_account_id not in ids: ids.append(campaign.default_account_id)
    return ids


def _ff_intake_params_for_cell(ff, direction, variant=1):
    """Resolve direction answers to exact Avito params. Numeric ranges are deterministically spread by variant."""
    entry=dict((ff.get("direction_intake") or {}).get(str(direction)) or {})
    if str(entry.get("mode") or "shared")!="shared": return {},{}
    params={}; ranges={}
    for name,ans in (entry.get("answers") or {}).items():
        if not isinstance(ans,dict): continue
        if str(ans.get("mode") or "exact")=="range":
            try:
                lo=float(str(ans.get("min")).replace(",",".")); hi=float(str(ans.get("max")).replace(",","."))
                if hi<lo: lo,hi=hi,lo
                # Stable value inside client's allowed range; variants rotate min/mid/max.
                slots=[lo,(lo+hi)/2.0,hi]; val=slots[(max(1,int(variant))-1)%3]
                params[name]=str(int(round(val))) if float(val).is_integer() else ("%.2f"%val).rstrip("0").rstrip(".")
                ranges[name]={"min":lo,"max":hi}
            except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=3716)
        elif ans.get("value") not in (None,""):
            params[name]=ans.get("value")
    return params,ranges


def _ff_validated_category_hint(db, direction: str, hints: dict) -> dict:
    """Canonicalize a launcher category hint against the current Avito tree.

    Hints only accelerate categories already observed in this account. The client
    cannot invent a path: template_id is resolved back to the server-side tree.
    """
    from app.models.category_template import CategoryTreeLeaf
    if not isinstance(hints, dict) or not hints:
        return {}
    key=str(direction or "").strip().casefold()
    raw=None
    for k,v in hints.items():
        if str(k or "").strip().casefold()==key and isinstance(v,dict):
            raw=v; break
    if not raw:
        return {}
    tid=str(raw.get("template_id") or "").strip()
    if not tid:
        return {}
    row=db.query(CategoryTreeLeaf).filter(CategoryTreeLeaf.template_id==tid).first()
    if not row:
        return {}
    return {"template_id":str(row.template_id or ""),"path":str(row.path or ""),"leaf_name":str(row.leaf_name or ""),"score":100,"source":"existing_account_feed"}


def _ff_extract_plan(db, campaign, body):
    import re as _re
    brief = str(body.brief or "").strip()
    directions = [str(x).strip() for x in (body.directions or []) if str(x).strip()]
    cities = [str(x).strip() for x in (body.cities or []) if str(x).strip()]
    def _normal_ru_phrase(value, title=False):
        text_value=str(value or "").strip()
        try:
            import pymorphy2
            morph=pymorphy2.MorphAnalyzer()
            words=[]
            for token in text_value.split():
                parts=token.split("-"); norm=[]
                for part in parts:
                    parsed=morph.parse(part)[0].normal_form if part else part
                    norm.append(parsed)
                words.append("-".join(norm))
            out=" ".join(words)
            if title:
                out=" ".join("-".join(p[:1].upper()+p[1:] for p in w.split("-")) for w in out.split())
            return out or text_value
        except Exception:
            # Lightweight fallback for the most common Russian locative city forms
            # when pymorphy2 is not installed in production.
            if title:
                known={"москве":"Москва","санкт-петербурге":"Санкт-Петербург","питере":"Санкт-Петербург","уфе":"Уфа","пензе":"Пенза","сочи":"Сочи","ижевске":"Ижевск","калуге":"Калуга","казани":"Казань","самаре":"Самара","туле":"Тула","твери":"Тверь","воронеже":"Воронеж","краснодаре":"Краснодар","новосибирске":"Новосибирск","екатеринбурге":"Екатеринбург"}
                if text_value.lower() in known: return known[text_value.lower()]
            return text_value
    creative_angles=[]
    angle_match=_re.search(r"(?:разн(?:ые|ыми)\s+посыл(?:ы|ами)|посылы|акценты)\s*[:—-]\s*([^\n.]{3,500})",brief,_re.IGNORECASE)
    if angle_match:
        creative_angles=[x.strip(" -•\t.:;") for x in _re.split(r"[,;]+",angle_match.group(1)) if x.strip(" -•\t.:;")][:20]

    # Multi-offer brief: a single client prompt may contain numbered services/products.
    # Parse each numbered row as an independent direction with its own geo/price so
    # "one ad per direction" does not become a cartesian product of every city.
    parsed_cells=[]
    _instruction_heading_re=_re.compile(
        r"^(?:сначала\s+)?(?:проанализируй|проведи|используй|сделай|подготовь|собери|проверь|финальн(?:ая|ый)|seo\b|ключев(?:ые|ая)\b|добавь|найди|создай|сохрани|рандомизируй|не\s+используй|перед\s+показом)",
        _re.IGNORECASE,
    )
    for _line in brief.splitlines():
        _m=_re.match(r"^\s*\d{1,3}[.)]\s+(.+?)\s*$",_line)
        if not _m: continue
        _row=_m.group(1).strip()
        # Numbered sections in a long marketing brief are workflow instructions, not
        # products/services. Treat only compact commercial rows as directions.
        if _instruction_heading_re.match(_row):
            continue
        # Compact client format: «Услуга — Санкт-Петербург — от 3 500 ₽».
        # Parse the right-most dash-separated price/geo cells before falling
        # back to natural-language «в городе / Цена ...» extraction.
        _dash_parts=[x.strip(" .;:\t") for x in _re.split(r"\s+[—–-]\s+",_row) if x.strip(" .;:\t")]
        _price=0; _price_mode=""; _price_unit=""; _geo=""
        _price_re=r"^(от\s*)?([0-9][0-9\s]*)(?:\s*₽|\s*руб(?:\.|лей)?)?(?:\s*/\s*(м²|м2|м\^2|кв\.?(?:м|метр)|шт\.?|час))?\s*$"
        _compact_price=_re.match(_price_re,_dash_parts[-1],_re.IGNORECASE) if len(_dash_parts)>=2 else None
        if _compact_price:
            try: _price=int(_compact_price.group(2).replace(" ",""))
            except Exception: _price=0
            _price_mode="from" if _compact_price.group(1) else "exact"
            _unit=(_compact_price.group(3) or "").lower()
            if _unit in {"м²","м2","м^2","кв.м","квм","кв.метр"}: _price_unit="за м²"
            elif _unit.startswith("шт"): _price_unit="за шт."
            elif _unit=="час": _price_unit="за час"
            _dash_parts=_dash_parts[:-1]
        if len(_dash_parts)>=2:
            _geo_candidate=_dash_parts[-1]
            # A dedicated dash cell between offer and price is treated as geo;
            # this supports Москва, Санкт-Петербург, Московская область, etc.
            if _re.match(r"^[А-ЯЁA-Z][А-Яа-яЁёA-Za-z-]+(?:\s+[А-Яа-яЁёA-Za-z-]+){0,3}$",_geo_candidate):
                _geo=_normal_ru_phrase(_geo_candidate,title=True)
                _dash_parts=_dash_parts[:-1]
        _first=" — ".join(_dash_parts).strip(" .;:") if _compact_price or len(_dash_parts)<len(_re.split(r"\s+[—–-]\s+",_row)) else _row.split(". Цена",1)[0].split(". Стоимость",1)[0].strip(" .;:")
        if not _geo:
            _gm=_re.search(r"\s+в\s+([А-ЯЁA-Z][А-Яа-яЁёA-Za-z-]+(?:\s+(?:области|крае|республике|округе|районе|[А-ЯЁA-Z][А-Яа-яЁёA-Za-z-]+))?)\s*$",_first)
            if _gm:
                _geo=_normal_ru_phrase(_gm.group(1).strip(),title=True)
                _offer=_first[:_gm.start()].strip(" -—–•\t.:")
            else:
                _offer=_first.strip(" -—–•\t.:")
        else:
            _offer=_first.strip(" -—–•\t.:")
        if not _compact_price:
            _pm=_re.search(r"(?:цена|стоимость)\s*(от\s*)?([0-9][0-9\s]{2,})",_row,_re.IGNORECASE)
            if _pm:
                try: _price=int(_pm.group(2).replace(" ",""))
                except Exception: _price=0
                _price_mode="from" if _pm.group(1) else "exact"
                _tail=_row[_pm.end():].lower()
                if "м²" in _tail or "м2" in _tail or "кв.м" in _tail: _price_unit="за м²"
                elif _re.search(r"(?:за\s*)?шт\.?",_tail): _price_unit="за шт."
                elif _re.search(r"(?:за\s*)?час",_tail): _price_unit="за час"
        if 2 <= len(_offer) <= 160:
            parsed_cells.append({"direction":_normal_ru_phrase(_offer),"city":_geo,"price":_price,"price_mode":_price_mode,"price_unit":_price_unit})
    if not directions and parsed_cells:
        directions=list(dict.fromkeys(x["direction"] for x in parsed_cells))

    def _ff_non_offer_quantity_fragment(value: str) -> bool:
        """Reject execution/count fragments that are not products or services.

        Natural client prompts are often comma-separated:
        «штукатурка в Сочи, 20 объявлений, цена от 3500».
        The old fallback treated «20 объявлений» as a second commercial direction,
        which created an impossible Avito-intake step with no category/fields.
        """
        low=str(value or "").strip().lower()
        return bool(_re.match(
            r"^(?:(?:сделай|создай|подготовь|нужно|нужно\s+сделать|нужно\s+создать)\s+)?"
            r"\d{1,4}\s+(?:боев(?:ое|ых)\s+)?"
            r"(?:объявлен\w*|карточ\w*|вариант\w*|фото(?:граф\w*)?|баннер\w*)\b",
            low,
            _re.IGNORECASE,
        ))

    if parsed_cells:
        parsed_cells=[x for x in parsed_cells if not _ff_non_offer_quantity_fragment(x.get("direction") or "")]
        if not body.directions:
            directions=list(dict.fromkeys(x["direction"] for x in parsed_cells))

    if not directions:
        # Launcher and normal client prompts quote the actual offer. Parse the full
        # quoted phrase first so internal prepositions such as «в эвакуатор»,
        # «в душ» or «для дома» can never be mistaken for a geography boundary.
        quoted_offer=_re.search(r"по\s+(?:услуг(?:е|и|у|ой)?|товар(?:у|е|а|ом)?)\s+[«\"]([^»\"]{2,160})[»\"]",brief,_re.IGNORECASE)
        if quoted_offer:
            candidate=quoted_offer.group(1).strip(" «»\"' -•\t.:")
            if 2 <= len(candidate) <= 160:
                directions=[_normal_ru_phrase(candidate)]
        # Common natural-language form: "30 объявлений по ремонту фар в Санкт-Петербурге".
        # Prefer the actual offer after "по", not the whole instruction or its creative angles.
        if not directions:
            dm=_re.search(r"(?:объявлен\w*\s+)?по\s+(.+?)(?=\s+в\s+[А-ЯЁA-Z]|[.;\n]|$)",brief,_re.IGNORECASE)
            if dm:
                candidate=dm.group(1).strip(" -•\t.:")
                candidate=_re.sub(r"^(?:услуг(?:е|и|у|ой)?|товар(?:у|е|а|ом)?)\s+[«\"']?", "", candidate, flags=_re.IGNORECASE)
                candidate=candidate.strip(" «»\"' -•\t.:")
                if 2 <= len(candidate) <= 160:
                    directions=[_normal_ru_phrase(candidate)]
        if not directions:
            # Long natural-language briefs often describe the offer in the account context
            # and then contain numbered execution steps. Prefer a concrete named product
            # mentioned in the brief; otherwise use the connected account niche.
            named_offer=""
            for pat in (r"(?:по|для|объявлен(?:ия|ий)?)\s+(Toyota\s+Camry)\b", r"\b(Toyota\s+Camry)\b"):
                mm=_re.search(pat,brief,_re.IGNORECASE)
                if mm:
                    named_offer=mm.group(1).strip(); break
            if named_offer:
                directions=[named_offer]
            elif str((_ff_account_context(db,campaign.default_account_id or "") or {}).get("niche") or "").strip():
                directions=[str((_ff_account_context(db,campaign.default_account_id or "") or {}).get("niche") or "").strip()]
        if not directions:
            raw = [x.strip(" -•\t.:") for x in _re.split(r"[\n;,]+", brief) if x.strip(" -•\t.:")]
            stop = ("нужно создать объявления", "создать объявления", "объявления", "нужно", "нужно создать")
            directions = []
            for x in raw:
                low=x.lower().strip()
                if _ff_non_offer_quantity_fragment(x):
                    continue
                if low in stop or low.startswith("нужно создать объяв") or low.startswith(("разные посыл","посылы","акценты")):
                    continue
                if _re.match(r"^(?:в|город|г\.)\s+[А-ЯЁA-Z]", x):
                    continue
                if low.startswith(("опыт ","гарант","цена","стоимость","доставка","адрес","телефон","работаем","преимуществ","усп ")):
                    continue
                directions.append(x)
    # Normalize prose tails and enrich a named model with the connected account's
    # actual commercial intent. This keeps "Toyota Camry и близким направлениям"
    # from becoming a fake service and gives category resolution the rental context.
    if directions:
        cleaned=[]
        ctx_niche=str((_ff_account_context(db,campaign.default_account_id or "") or {}).get("niche") or "").strip()
        for raw_d in directions:
            d=_re.sub(r"\s+и\s+близк(?:им|ие)\s+направлен(?:иям|ия)\s*$","",str(raw_d),flags=_re.IGNORECASE).strip()
            if _re.search(r"\bToyota\s+Camry\b",d,_re.IGNORECASE) and "аренд" in ctx_niche.lower():
                d="Аренда Toyota Camry с выкупом" if "выкуп" in ctx_niche.lower() else "Аренда Toyota Camry"
            if d and d not in cleaned: cleaned.append(d)
        directions=cleaned
    if not cities:
        # Geo must come from an explicit placement instruction, not from examples, CTA text
        # or mentions such as «в Avito». Inspect only the first task sentence/line and
        # require a placement verb/preposition. If geo is absent, ask the client once.
        first_task=(brief.splitlines()[0] if brief.splitlines() else brief).strip()
        # Quoted offer names may themselves contain capitalized words and the token «в».
        # Remove quoted spans before geo regexes so «перегородки из стекла» can never
        # be misread as a city merely because the sentence later contains a real geo.
        _geo_task=_re.sub(r"[«\"'][^»\"']{1,180}[»\"']", " OFFER ", first_task)
        geo_matches=[]
        for pat in (
            r"(?:\bразмест(?:ить|и|ить объявления|ить рекламу)\s+(?:в|по)|\bгеограф(?:ия|ии)\s*[:—-]?|\bгород(?:а|е)?\s*[:—-]?)\s*([А-ЯЁ][А-Яа-яЁё-]+(?:\s+(?:область|области|край|крае|республика|республике|округ|округе|район|районе|[А-ЯЁ][А-Яа-яЁё-]+)){0,2})",
            r"(?:объявлен\w*|реклам\w*)[^.\n]{0,80}?\s+в\s+(?:городе\s+|г\.\s*)?([А-ЯЁ][А-Яа-яЁё-]+(?:\s+(?:области|крае|республике|округе|районе|[А-ЯЁ][А-Яа-яЁё-]+)){0,2})(?=[,.;\n]|$)",
        ):
            geo_matches.extend(_re.findall(pat,_geo_task,_re.IGNORECASE))
        rejected={"avito","авито","boris","борис","telegram","телеграм","vk","вк"}
        cities=[]
        for raw_city in geo_matches:
            city=_normal_ru_phrase(str(raw_city).strip(),title=True)
            if city and city.lower() not in rejected and city not in cities:
                cities.append(city)
        # Compact natural form: «Уборка в Москве, 1 объявление, цена от ...».
        # Here there is no explicit word «объявление» before the city, so the strict
        # patterns above intentionally do not match it. Accept only a conservative
        # set of common city forms to avoid treating product phrases such as
        # «перегородки в душ» as geography.
        if not cities:
            _first_cell=_re.split(r"[,;]",first_task,1)[0].strip()
            _tail_geo=_re.search(r"\s+в\s+(?:городе\s+|г\.\s*)?([А-ЯЁA-Z][А-Яа-яЁёA-Za-z-]+(?:\s+[А-ЯЁA-Z][А-Яа-яЁёA-Za-z-]+)?)\s*$",_first_cell)
            if _tail_geo:
                _raw=_tail_geo.group(1).strip()
                _known_geo={
                    "москва","москве","санкт-петербург","санкт-петербурге","питер","питере",
                    "сочи","уфа","уфе","пенза","пензе","ижевск","ижевске","калуга","калуге",
                    "казань","казани","самара","самаре","тула","туле","тверь","твери",
                    "воронеж","воронеже","краснодар","краснодаре","новосибирск","новосибирске",
                    "екатеринбург","екатеринбурге","ростов-на-дону","нижний новгород","нижнем новгороде",
                    "челябинск","челябинске","омск","омске","пермь","перми","волгоград","волгограде",
                }
                if _raw.lower() in _known_geo:
                    city=_normal_ru_phrase(_raw,title=True)
                    if city and city.lower() not in rejected:
                        cities=[city]
                        # The compact geo belongs to placement, not to the product/service
                        # name. Strip it from the direction so category matching receives
                        # «Тестовая уборка», not «Тестовая уборка в Москве».
                        _geo_suffix=_re.compile(r"\s+в\s+"+_re.escape(_raw)+r"\s*$",_re.IGNORECASE)
                        directions=[_geo_suffix.sub("",str(d)).strip() for d in directions]
                        directions=[d for d in directions if d]
        cities=cities[:20]
    variants = max(1, min(int(body.variants or 1), 10))
    defaults = dict(body.defaults or {})
    if body.price:
        defaults["price"] = int(body.price)
    account_id = campaign.default_account_id or ""
    matrix = []
    if parsed_cells and not body.directions:
        for _cell in parsed_cells[:50]:
            d=_cell["direction"]
            candidates=_ff_category_candidates(db,d)
            hinted=_ff_validated_category_hint(db,d,body.category_hints)
            if hinted:
                candidates=[hinted]+[x for x in candidates if str(x.get("template_id") or "")!=str(hinted.get("template_id") or "")]
            candidate=hinted or _ff_auto_category_candidate(d,candidates)
            for variant in range(1,variants+1):
                matrix.append({"direction":d,"city":_cell.get("city") or "","price":_cell.get("price") or 0,"price_mode":_cell.get("price_mode") or "","price_unit":_cell.get("price_unit") or "","variant":variant,
                               "template_id":candidate.get("template_id",""),"category_path":candidate.get("path",""),
                               "category_candidates":candidates})
        cities=list(dict.fromkeys(x.get("city") for x in parsed_cells if x.get("city")))
    else:
        for d in directions[:50]:
            candidates = _ff_category_candidates(db, d)
            hinted = _ff_validated_category_hint(db,d,body.category_hints)
            if hinted:
                candidates=[hinted]+[x for x in candidates if str(x.get("template_id") or "")!=str(hinted.get("template_id") or "")]
            candidate = hinted or _ff_auto_category_candidate(d,candidates)
            for city in (cities or [""]):
                for variant in range(1, variants + 1):
                    matrix.append({"direction": d, "city": city, "variant": variant,
                                   "template_id": candidate.get("template_id", ""),
                                   "category_path": candidate.get("path", ""),
                                   "category_candidates": candidates})
    if body.quantity and matrix:
        matrix = matrix[:max(1, min(int(body.quantity), 1000))]
    ctx=_ff_account_context(db,account_id)
    qty = int(body.quantity or 0)
    if not qty:
        if parsed_cells and _re.search(r"по\s+одному\s+объявлен\w*\s+на\s+кажд",brief,_re.IGNORECASE):
            qty=len(matrix)
        else:
            qm=_re.search(r"(\d{1,4})\s+.{0,40}?(?:объявлен\w*|карточ\w*)",brief.lower())
            qty=int(qm.group(1)) if qm else len(matrix)
    qty=max(0,min(qty,1000))
    # The requested quantity is a real materialization contract, not a label.
    # Repeat direction × city cells with increasing creative variant numbers
    # so "30 объявлений" actually produces 30 independent campaign cards.
    if qty and matrix and len(matrix) != qty:
        seed=list(matrix); expanded=[]
        for idx in range(qty):
            src=dict(seed[idx % len(seed)])
            src["variant"]=idx+1
            expanded.append(src)
        matrix=expanded
    audience=""
    # Never treat the generic preposition «для» as audience on its own. Campaign
    # prompts naturally contain «релевантные для товара», «для результата»,
    # «для баннера» etc.; the previous broad regex silently turned the offer itself
    # into an audience. Accept an audience only behind an explicit audience marker
    # or a tightly bounded human/business-segment phrase.
    am=_re.search(r"(?:целевая\s+аудитория|аудитория|для\s+кого)\s*[:—-]?\s*([^\n,;.]{4,120})",brief,_re.IGNORECASE)
    if not am:
        am=_re.search(r"\bдля\s+((?:клиент(?:ов|а|ы)|владельц(?:ев|а|ы)|собственник(?:ов|а|и)|застройщик(?:ов|а|и)|подрядчик(?:ов|а|и)|прораб(?:ов|а|ы)|компан(?:ий|ии)|бизнеса|частных\s+лиц|семей|пар|мужчин|женщин)[^\n,;.]*)",brief,_re.IGNORECASE)
    if am:
        candidate_audience=am.group(1).strip(" «»\"'")
        _aud_low=candidate_audience.lower()
        _aud_instruction=any(x in _aud_low for x in (
            "результат", "баннер", "фото", "изображен", "avito", "авито",
            "карточ", "объявлен", "генерац", "провер", "текст", "публикац",
        ))
        if not _aud_instruction and not _aud_low.startswith(("каждого направления","каждое направление","каждого товара","каждой услуги")):
            audience=candidate_audience
    def _is_negative_fact_line(x):
        low=str(x or "").lower()
        return bool(_re.search(r"(?:не\s+(?:выдум|придум|добав)|без\s+выдум)",low))
    usp_parts=[x.strip() for x in _re.split(r"[\n;]+",brief) if not _is_negative_fact_line(x) and any(k in x.lower() for k in ("гарант","опыт","быстр","проф","сложн","достав"))]
    gm=_re.search(r"гарант(?:ия|ии|ией)?\s*[:—-]?\s*([^\n,;.]{2,60})",brief,_re.IGNORECASE)
    negative_guarantee=bool(_re.search(r"(?:не\s+(?:выдум|придум|добав)[^\n;.]{0,50}гарант|без[^\n;.]{0,30}гарант)",brief,_re.IGNORECASE))
    if gm and not negative_guarantee and not defaults.get("guarantee"):
        value=gm.group(1).strip()
        if len(value)>=2 and value.lower() not in {"ии","ия","ией"}: defaults["guarantee"]=value
    # Global price is accepted only from an explicit dedicated line. Mentions inside
    # examples ("например ... цена от 10 млн") are descriptive context, not seller facts.
    pm=_re.search(r"(?im)^\s*(?:цена|стоимость)\s*[:—-]\s*(?:от\s*)?([0-9][0-9\s]{2,})\s*(?:₽|руб(?:\.|лей)?)?\s*$",brief)
    if pm and not defaults.get("price"):
        try: defaults["price"]=int(pm.group(1).replace(" ",""))
        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=4060)
    if not defaults.get("usp") and usp_parts: defaults["usp"]="; ".join(usp_parts[:5])
    if not defaults.get("usp") and ctx.get("advantages"): defaults["usp"]=ctx.get("advantages")
    return {"version": 2, "step": "questions", "brief": brief, "account_id": account_id,
            "account_context": ctx, "directions": directions, "services_products": directions,
            "creative_angles": creative_angles,
            "audience": audience, "cities": cities, "variants": variants, "quantity": qty,
            "defaults": defaults, "matrix": matrix, "items_planned": len(matrix)}


def _ff_item_by_id(db, campaign_id, item_id, allowed):
    from app.models.campaign_item import CampaignItem
    return db.query(CampaignItem).filter(CampaignItem.id == int(item_id),
        CampaignItem.campaign_id == campaign_id,
        CampaignItem.account_id.in_(list(allowed) or [""])).first()


@router.get("/{campaign_id}/workflow")
def ff_workflow_get(campaign_id:int, db=Depends(_db), user=Depends(get_current_user)):
    import datetime as _dt_scope
    allowed = _allowed_accounts(db,user); campaign = _campaign_or_404(db,campaign_id,allowed)
    _, ff = _ff_state_load(campaign)
    rows = _ff_items(db,campaign_id,allowed)
    # FEED_FACTORY_CURRENT_SCOPE_RECONCILE_V1: historical/superseded rows stay in
    # the DB for audit, but a stale saved item_ids list must never make the UI
    # operate on them. Reconcile only when the live Feed Factory scope is exact
    # and unambiguous against items_planned; otherwise fail closed with warning.
    if ff:
        live_ff=[r for r in rows if str(getattr(r,"source","") or "")=="feed_factory"]
        live_ids=[int(r.id) for r in live_ff]
        saved_ids=[int(x) for x in (ff.get("item_ids") or []) if str(x).isdigit()]
        planned=int(ff.get("items_planned") or ff.get("quantity") or 0)
        if saved_ids != live_ids:
            if planned>0 and len(live_ids)==planned and len(set(live_ids))==planned:
                ff=dict(ff); ff["item_ids"]=live_ids
                ff["scope_reconcile"]={"status":"repaired","at":_dt_scope.datetime.now(_dt_scope.timezone.utc).isoformat(),"live_items":len(live_ids),"previous_items":len(saved_ids)}
                _ff_state_save(db,campaign,ff)
            else:
                ff=dict(ff); ff["scope_reconcile"]={"status":"blocked_ambiguous","live_items":len(live_ids),"saved_items":len(saved_ids),"planned":planned}
    questions = _ff_questions(db,campaign,ff) if ff else []
    return {"status":"ok", "workflow":ff, "questions":questions,
            "items_total":len(rows), "counts":CS.status_counts(db,campaign_id,allowed_accounts=allowed)}


@router.post("/{campaign_id}/workflow/brief")
def ff_workflow_brief(campaign_id:int, body:FFWorkflowBriefBody, db=Depends(_db), user=Depends(get_current_user)):
    allowed = _allowed_accounts(db,user); campaign = _campaign_or_404(db,campaign_id,allowed)
    _ff_assert_mutable(campaign)
    ff = _ff_extract_plan(db,campaign,body)
    ff = _ff_hydrate_profiles(db,campaign,ff)
    ff["questions"] = _ff_questions(db,campaign,ff)
    _ff_state_save(db,campaign,ff)
    return {"status":"ok", "workflow":ff, "questions":ff["questions"]}


@router.post("/{campaign_id}/workflow/answers")
def ff_workflow_answers(campaign_id:int, body:dict, db=Depends(_db), user=Depends(get_current_user)):
    allowed = _allowed_accounts(db,user); campaign = _campaign_or_404(db,campaign_id,allowed)
    _ff_assert_mutable(campaign)
    _, ff = _ff_state_load(campaign); defaults = dict(ff.get("defaults") or {})
    answers = dict(body.get("answers") or body or {})
    if answers.get("cities"):
        cities = answers["cities"] if isinstance(answers["cities"],list) else [x.strip() for x in str(answers["cities"]).split(",") if x.strip()]
        ff["cities"] = cities
    for key,val in answers.items():
        if key.startswith("param:"):
            defaults.setdefault("params",{})[key.split(":",1)[1]] = val
        elif key != "cities" and val not in (None,""):
            defaults[key] = val
    ff["defaults"] = defaults
    # Rebuild matrix geo while preserving category candidates.
    old = ff.get("matrix") or []; dirs = ff.get("directions") or []
    if answers.get("cities") and ff.get("cities") and dirs:
        lookup = {x.get("direction"):x for x in old}
        matrix=[]
        for d in dirs:
            proto=lookup.get(d) or {}; cands=proto.get("category_candidates") or _ff_category_candidates(db,d); cand=cands[0] if cands else {}
            for city in ff["cities"]:
                for v in range(1,int(ff.get("variants") or 1)+1):
                    matrix.append({"direction":d,"city":city,"variant":v,"template_id":cand.get("template_id",proto.get("template_id","")),"category_path":cand.get("path",proto.get("category_path","")),"category_candidates":cands})
        # Preserve the quantity requested in the original brief. A geo answer must
        # not collapse e.g. «20 объявлений» back to variants × cities.
        qty=max(0,min(int(ff.get("quantity") or 0),1000))
        if qty and matrix and len(matrix)!=qty:
            seed=list(matrix); expanded=[]
            for idx in range(qty):
                src=dict(seed[idx % len(seed)])
                # Variant is part of the stable Feed Factory identity and must be
                # unique for repeated quantity expansion, otherwise rows collapse.
                src["variant"]=idx+1
                expanded.append(src)
            matrix=expanded
        ff["matrix"] = matrix; ff["items_planned"] = len(matrix)
    ff["questions"] = _ff_questions(db,campaign,ff); ff["step"] = "structure" if not ff["questions"] else "questions"
    _ff_state_save(db,campaign,ff)
    return {"status":"ok","workflow":ff,"questions":ff["questions"]}


@router.get("/{campaign_id}/workflow/intake")
def ff_workflow_intake_get(campaign_id:int, db=Depends(_db), user=Depends(get_current_user)):
    allowed=_allowed_accounts(db,user); campaign=_campaign_or_404(db,campaign_id,allowed); _,ff=_ff_state_load(campaign)
    rows=_ff_items(db,campaign_id,allowed)
    return _ff_direction_intake(db,campaign,ff,rows)


def _ff_title_variants(direction: str, brief: str = "") -> list[str]:
    d=str(direction or "").strip(); low=d.lower(); out=[]
    if "продаж" in low and "кварт" in low:
        out=["Брокер по продаже квартир", "Помощь в продаже квартиры под ключ", "Продажа квартиры с брокером", "Риелтор по продаже квартир", "Сопровождение продажи квартиры"]
    elif ("подбор" in low or "покуп" in low) and "кварт" in low:
        out=["Брокер по подбору квартиры", "Подбор квартиры под ваш бюджет", "Помощь в покупке квартиры", "Риелтор для покупки квартиры", "Сопровождение покупки квартиры"]
    elif "инвест" in low:
        out=["Инвестиционный брокер по недвижимости", "Подбор недвижимости для инвестиций", "Инвестиции в недвижимость с брокером", "Подбор инвестиционной квартиры", "Консультация инвестора по недвижимости"]
    elif "ипот" in low:
        out=["Ипотечный брокер", "Помощь с ипотекой под ключ", "Ипотечное сопровождение", "Подбор ипотеки и сопровождение сделки", "Консультация ипотечного брокера"]
    else:
        out=[d, f"{d} под ключ", f"Консультация: {d}", f"{d} — сопровождение", f"Помощь специалиста: {d}"]
    return list(dict.fromkeys([x[:50] for x in out if x.strip()]))[:8]


def _ff_avito_search_recommendations(db, account_id: str, direction: str, refresh: bool=False, city: str="") -> dict:
    import datetime as _dt, hashlib as _hl, time as _time
    from app.models.storage import Storage
    key="avito_search_reco:"+_hl.sha1((str(direction).strip().lower()+"|"+str(city or "").strip().lower()).encode("utf-8")).hexdigest()[:16]
    row=db.query(Storage).filter(Storage.account_id==account_id,Storage.key==key).first()
    now=_dt.datetime.now(_dt.timezone.utc)
    if row and not refresh:
        try:
            cached=json.loads(row.value or "{}"); ts=_dt.datetime.fromisoformat(str(cached.get("checked_at") or ""))
            if (now-ts).total_seconds()<86400: cached["source_status"]="cached" if cached.get("source_status")=="live" else cached.get("source_status"); return cached
        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=4190)
    started=_time.monotonic(); observed=[]; status="unavailable"; error=""
    query=str(direction or "").strip()
    try:
        import requests
        from app.api.parser import _extract_listing_items
        search_phrase=(query+" "+str(city or "").strip()).strip()
        url="https://www.avito.ru/rossiya?q="+requests.utils.quote(search_phrase)
        headers={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
        r=requests.get(url,timeout=6,headers=headers)
        if r.status_code==200:
            observed=[str(x.get("title") or "").strip() for x in _extract_listing_items(r.text) if str(x.get("title") or "").strip()][:12]
            status="live" if observed else "live_empty"
        elif r.status_code==429: status="rate_limited"; error="Avito временно ограничил частоту запросов"
        else: status="http_error"; error=f"Avito HTTP {r.status_code}"
    except Exception as e:
        status="unavailable"; error=str(e)[:180]
    result={"query":query,"city":str(city or "").strip(),"source":"Avito live search","source_status":status,"observed_titles":list(dict.fromkeys(observed))[:10],
            "title_variants":_ff_title_variants(direction),"checked_at":now.isoformat(),"took_ms":int((_time.monotonic()-started)*1000),
            "cache_ttl_hours":24,"estimated_external_cost_rub":0,"message":error}
    if row: row.value=json.dumps(result,ensure_ascii=False)
    else: db.add(Storage(account_id=account_id,key=key,value=json.dumps(result,ensure_ascii=False)))
    db.commit(); return result


@router.get("/{campaign_id}/workflow/search_recommendations")
def ff_workflow_search_recommendations(campaign_id:int, direction:str="", city:str="", refresh:bool=False, db=Depends(_db), user=Depends(get_current_user)):
    allowed=_allowed_accounts(db,user); campaign=_campaign_or_404(db,campaign_id,allowed); _,ff=_ff_state_load(campaign)
    d=str(direction or "").strip()
    if d not in [str(x) for x in (ff.get("directions") or [])]: raise HTTPException(422,"Направление не относится к этой кампании")
    account_id=campaign.default_account_id or ff.get("account_id") or ""
    return {"status":"ok","direction":d,**_ff_avito_search_recommendations(db,account_id,d,refresh=refresh,city=city)}


@router.post("/{campaign_id}/workflow/search_recommendations/apply")
def ff_workflow_search_recommendations_apply(campaign_id:int, body:dict, db=Depends(_db), user=Depends(get_current_user)):
    allowed=_allowed_accounts(db,user); campaign=_campaign_or_404(db,campaign_id,allowed)
    _ff_assert_mutable(campaign)
    direction=str(body.get("direction") or "").strip(); selected=[str(x or "").strip() for x in (body.get("titles") or []) if str(x or "").strip()]
    if not selected: raise HTTPException(422,"Выберите хотя бы один вариант названия")
    rows=[r for r in _ff_items(db,campaign_id,allowed) if str(r.segment or _ff_payload(r).get("direction") or "").strip()==direction]
    if not rows: raise HTTPException(404,"Карточки направления не найдены")
    changed=[]
    for i,row in enumerate(rows):
        p=_ff_payload(row); p["title"]=_ff_clean_title_for_avito(selected[i % len(selected)],direction); p["search_title_source"]="owner_selected_avito_recommendation"
        row.payload_json=json.dumps(p,ensure_ascii=False); row.content_hash=CS.content_hash(p)
        if row.status in ("ready","needs_data","imported"): row.status="draft"
        changed.append(row.id)
    db.commit(); return {"status":"ok","changed":changed,"selected_titles":selected}


@router.post("/{campaign_id}/workflow/intake")
def ff_workflow_intake_save(campaign_id:int, body:dict, db=Depends(_db), user=Depends(get_current_user)):
    from app.api.feed_contract import resolve_feed_contract, SYSTEM_FIELDS
    allowed=_allowed_accounts(db,user); campaign=_campaign_or_404(db,campaign_id,allowed); _,ff=_ff_state_load(campaign)
    _ff_assert_mutable(campaign)
    direction=str(body.get("direction") or "").strip(); template_id=str(body.get("template_id") or "").strip(); mode=str(body.get("mode") or "shared").strip()
    if direction not in [str(x) for x in (ff.get("directions") or [])] and direction not in [str(r.segment or _ff_payload(r).get("direction") or "") for r in _ff_items(db,campaign_id,allowed)]:
        raise HTTPException(422,"Направление не относится к этой кампании")
    if mode not in {"shared","per_item"}: raise HTTPException(422,"Неизвестный режим параметров")
    candidates=_ff_category_candidates(db,direction,limit=12); allowed_tids={str(x.get("template_id") or "") for x in candidates}
    # Preserve exact current matrix/category if it is authoritative but ranking no longer returns it.
    for cell in ff.get("matrix") or []:
        if str(cell.get("direction") or "")==direction and cell.get("template_id"): allowed_tids.add(str(cell.get("template_id")))
    if template_id not in allowed_tids:
        raise HTTPException(422,{"message":"Категория Avito не входит в варианты этого направления","template_id":template_id})
    contract=resolve_feed_contract(template_id,_db=db)
    if not contract.get("ok"): raise HTTPException(422,{"message":"Контракт категории Avito не найден","template_id":template_id})
    raw_answers=body.get("answers") or {}; answers={}
    fields=contract.get("fields") or {}
    for name,val in raw_answers.items():
        if name not in fields: raise HTTPException(422,{"message":"Параметра нет в выбранной категории","field":name})
        spec=fields[name]; allowed_values=[str(x) for x in (spec.get("allowed") or [])]
        ans=val if isinstance(val,dict) else {"mode":"exact","value":val}; kind=str(ans.get("mode") or "exact")
        if kind=="range":
            if not _ff_intake_field_public(name,spec).get("range_allowed"):
                raise HTTPException(422,{"message":"Для этого параметра диапазон не поддерживается","field":name})
            try: lo=float(str(ans.get("min")).replace(",",".")); hi=float(str(ans.get("max")).replace(",","."))
            except Exception: raise HTTPException(422,{"message":"Укажите числовой диапазон","field":name})
            if hi<lo: lo,hi=hi,lo
            answers[name]={"mode":"range","min":lo,"max":hi}
        else:
            value=ans.get("value")
            if value in (None,""): continue
            multiple=bool(spec.get("multiple"))
            values=(list(value) if isinstance(value,(list,tuple,set)) else [value])
            if multiple:
                values=[str(x).strip() for x in values if str(x).strip()]
                if allowed_values and any(v not in allowed_values for v in values):
                    raise HTTPException(422,{"message":"Значение вне допустимого списка Avito","field":name,"allowed_values":allowed_values})
                answers[name]={"mode":"exact","value":values}
            else:
                if allowed_values and str(value) not in allowed_values:
                    raise HTTPException(422,{"message":"Значение вне допустимого списка Avito","field":name,"allowed_values":allowed_values})
                answers[name]={"mode":"exact","value":value}
    for name,spec in fields.items():
        if name in SYSTEM_FIELDS: continue
        allowed_values=list(spec.get("allowed") or [])
        if len(allowed_values)==1 and name not in answers:
            answers[name]={"mode":"exact","value":allowed_values[0],"source":"template_constant"}
    state=dict(ff.get("direction_intake") or {})
    state[direction]={"direction":direction,"template_id":template_id,"category_path":contract.get("category_path") or "",
                      "contract_version":contract.get("contract_version") or "","mode":mode,"answers":answers,
                      "confirmed":bool(body.get("confirmed")),"updated_at":__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()}
    ff["direction_intake"]=state; ff["step"]="questions"
    if bool(body.get("confirmed")):
        _account_id = campaign.default_account_id or ff.get("account_id") or ""
        _ff_profile_save(db, _account_id, direction, state[direction])
        # Feed Factory already resolved the exact Avito category path. Reuse that
        # evidence for the existing AI Marketing tariff gate instead of running a
        # second category detector. The top-level path decides services vs goods.
        if _account_id and state[direction].get("category_path"):
            from app.models.storage import Storage as _CategoryStorage
            _dc=db.query(_CategoryStorage).filter(_CategoryStorage.account_id==_account_id,_CategoryStorage.key=="detected_category").first()
            _path=str(state[direction].get("category_path") or "")
            _top=(_path.split(">",1)[0].strip() if _path else "")
            _val=json.dumps({"category":_top,"category_path":_path,"template_id":template_id,"source":"feed_factory_confirmed_category"},ensure_ascii=False)
            if _dc: _dc.value=_val
            else: db.add(_CategoryStorage(account_id=_account_id,key="detected_category",value=_val))
            db.commit()
    _ff_state_save(db,campaign,ff)
    return _ff_direction_intake(db,campaign,ff,_ff_items(db,campaign_id,allowed))


@router.post("/{campaign_id}/workflow/direction")
def ff_workflow_add_direction(campaign_id:int, body:dict, db=Depends(_db), user=Depends(get_current_user)):
    """Add one product/service direction without recreating the campaign."""
    allowed=_allowed_accounts(db,user); campaign=_campaign_or_404(db,campaign_id,allowed); _,ff=_ff_state_load(campaign)
    _ff_assert_mutable(campaign)
    direction=str(body.get("direction") or "").strip()
    if len(direction) < 2:
        raise HTTPException(422,"Укажите товар или услугу")
    directions=[str(x).strip() for x in (ff.get("directions") or []) if str(x).strip()]
    if direction.lower() not in {x.lower() for x in directions}:
        directions.append(direction)
    ff["directions"]=directions; ff["services_products"]=directions
    cities=[str(x).strip() for x in (ff.get("cities") or []) if str(x).strip()] or [""]
    variants=max(1,min(int(ff.get("variants") or 1),10))
    matrix=list(ff.get("matrix") or [])
    if not any(str(x.get("direction") or "").lower()==direction.lower() for x in matrix):
        candidates=_ff_category_candidates(db,direction,limit=12); candidate=_ff_auto_category_candidate(direction,candidates)
        for city in cities:
            for variant in range(1,variants+1):
                matrix.append({"direction":direction,"city":city,"variant":variant,
                               "template_id":candidate.get("template_id", ""),
                               "category_path":candidate.get("path", ""),
                               "category_candidates":candidates})
    ff["matrix"]=matrix; ff["items_planned"]=len(matrix); ff["step"]="questions"
    _ff_state_save(db,campaign,ff)
    return {"status":"ok","workflow":ff,
            "intake":_ff_direction_intake(db,campaign,ff,_ff_items(db,campaign_id,allowed))}


@router.post("/{campaign_id}/workflow/materialize")
def ff_workflow_materialize(campaign_id:int, body:FFMaterializeBody, db=Depends(_db), user=Depends(get_current_user)):
    import hashlib as _hashlib
    from app.models.campaign_item import CampaignItem
    from app.api.feed_contract import resolve_feed_contract, resolve_applicable_params
    allowed = _allowed_accounts(db,user); campaign = _campaign_or_404(db,campaign_id,allowed)
    _ff_assert_mutable(campaign)
    _, ff = _ff_state_load(campaign); account_id=(body.account_id or ff.get("account_id") or campaign.default_account_id or "").strip()
    if not account_id or account_id not in allowed: raise HTTPException(400,"Выберите доступный аккаунт")
    intake=_ff_direction_intake(db,campaign,ff,_ff_items(db,campaign_id,allowed)); pending=[]
    for d in intake.get("directions") or []:
        if not d.get("confirmed") or not d.get("template_id"): pending.append(d.get("direction"))
        elif d.get("mode")=="shared" and not d.get("complete"): pending.append(d.get("direction"))
    if pending:
        raise HTTPException(409,{"message":"Сначала ответьте на вопросы по каждому направлению и подтвердите параметры.","directions":pending})
    defaults=ff.get("defaults") or {}; created=0; updated=0; ids=[]; created_refs=[]
    # A prior concurrent quota race may have created local draft rows but failed the
    # final atomic tariff consume. Settle that debt before any further workflow step;
    # an idempotent retry must never turn those rows into free uncharged inventory.
    _pending_listing_charge=max(0,int(ff.get("billing_pending_listings") or 0))
    _pending_listing_billing_key=str(ff.get("billing_pending_listings_key") or "").strip()
    if _pending_listing_charge:
        from app.api.billing import check_and_consume as _billing_consume
        if not _pending_listing_billing_key:
            return {"status":"billing_pending","code":"billing_idempotency_missing","unit":"listings",
                    "pending_charge":_pending_listing_charge,
                    "message":"Старое незавершённое списание карточек требует безопасной сверки; повторное создание не запускается."}
        _settle=_billing_consume(account_id,"listings",_pending_listing_charge,
                                 idempotency_key=_pending_listing_billing_key)
        if not _settle.get("allowed",False):
            return {"status":"billing_limit","code":"billing_limit","unit":"listings",
                    "requested":_pending_listing_charge,"remaining":0,
                    "message":_settle.get("message") or "Не удалось подтвердить лимит ранее созданных карточек."}
        ff.setdefault("billing_charged",{})["listings"]=int(ff.get("billing_charged",{}).get("listings") or 0)+_pending_listing_charge
        ff["billing_pending_listings"]=0
        ff["billing_pending_listings_key"]=""
        _ff_state_save(db,campaign,ff)
    # Billing preflight for NEW campaign cards only. Existing idempotent rows do not
    # consume quota again on retries/resume. This keeps large 100–1000 item jobs from
    # silently bypassing the tariff listing allowance.
    matrix=list(ff.get("matrix") or [])
    new_needed=0
    for _cell in matrix:
        _direction=str(_cell.get("direction") or "")
        _entry=dict((ff.get("direction_intake") or {}).get(_direction) or {})
        _tid=str(_entry.get("template_id") or _cell.get("template_id") or "")
        _raw="|".join([_direction,str(_cell.get("city") or ""),_tid,str(_cell.get("variant") or 1)])
        _ref="ff:"+_hashlib.sha1(_raw.encode("utf-8")).hexdigest()[:20]
        _exists=db.query(CampaignItem.id).filter(CampaignItem.campaign_id==campaign_id,CampaignItem.account_id==account_id,CampaignItem.source=="feed_factory",CampaignItem.source_ref==_ref,CampaignItem.status != "superseded").first()
        if not _exists: new_needed+=1
    # Once this campaign has real Avito identities, expanding it with a second
    # materialization is unsafe: old ads and new blank rows become one mixed scope.
    # Updating the same idempotent source_refs is allowed; adding new rows requires
    # a new campaign/version so publication receipts remain one-to-one.
    published_existing=db.query(CampaignItem.id).filter(
        CampaignItem.campaign_id==campaign_id,
        CampaignItem.account_id==account_id,
        CampaignItem.avito_item_id.isnot(None),
        CampaignItem.status != "superseded",
    ).count()
    if published_existing and new_needed:
        raise HTTPException(409,{"code":"published_campaign_scope_locked","published_items":published_existing,"new_items":new_needed,
            "message":"Эта кампания уже связана с опубликованными объявлениями Avito. Для нового набора создайте новую кампанию — BORIS не смешивает опубликованный и новый контур."})
    if new_needed:
        try:
            from app.api.billing import get_status as _billing_status
            _bst=_billing_status(account_id)
            if not _bst.get("unlimited"):
                _remaining=int(((_bst.get("usage") or {}).get("listings") or {}).get("remaining") or 0)
                if new_needed > _remaining:
                    raise HTTPException(409,{"code":"billing_limit","unit":"listings","requested":new_needed,"remaining":_remaining,
                        "message":f"По тарифу доступно ещё {_remaining} объявлений, а кампания требует {new_needed}. Уменьшите объём или увеличьте лимит."})
        except HTTPException:
            raise
        except Exception as _billing_exc:
            # Billing infrastructure errors must not be mistaken for an exhausted quota.
            print(f"[campaign billing] listing preflight unavailable: {_billing_exc}", flush=True)
    for cell in matrix:
        direction=str(cell.get("direction") or ""); entry=dict((ff.get("direction_intake") or {}).get(direction) or {})
        selected_tid=str(entry.get("template_id") or cell.get("template_id") or ""); selected_path=str(entry.get("category_path") or cell.get("category_path") or "")
        raw="|".join([direction,str(cell.get("city") or ""),selected_tid,str(cell.get("variant") or 1)])
        ref="ff:"+_hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]
        row=db.query(CampaignItem).filter(CampaignItem.campaign_id==campaign_id,CampaignItem.account_id==account_id,CampaignItem.source=="feed_factory",CampaignItem.source_ref==ref,CampaignItem.status != "superseded").first()
        path=selected_path; parts=[x.strip() for x in path.split(">") if x.strip()]
        category=parts[1] if len(parts)>=2 else (parts[0] if parts else "")
        price_by_city=defaults.get("price_by_city") or {}; price=cell.get("price") or price_by_city.get(cell.get("city"),defaults.get("price",0))
        price_fact={}
        if not int(price or 0):
            price_fact=_ff_confirmed_price_default(db,account_id,direction)
            if price_fact.get("price"):
                price=int(price_fact["price"])
        intake_params,param_ranges=_ff_intake_params_for_cell(ff,direction,cell.get("variant") or 1)
        base_params={} if str(entry.get("mode") or "shared")=="per_item" else dict(defaults.get("params") or {})
        base_params.update(intake_params)
        contract=_ff_contract_with_payload_overlay(db,{"template_id":selected_tid},resolve_feed_contract(selected_tid,_db=db),account_id=account_id)
        # The resolved contract is authoritative over remembered/matrix labels.
        # This prevents a concurrent category refresh from persisting a path that
        # belongs to another branch while keeping the same template_id.
        if contract.get("ok") and str(contract.get("category_path") or "").strip():
            path=str(contract.get("category_path") or "").strip()
            parts=[x.strip() for x in path.split(">") if x.strip()]
            category=parts[1] if len(parts)>=2 else (parts[0] if parts else category)
        if contract.get("ok"):
            for _name,_spec in (contract.get("fields") or {}).items():
                _allowed=list(_spec.get("allowed") or [])
                if len(_allowed)==1 and str(base_params.get(_name) or "") != str(_allowed[0]):
                    base_params[_name]=_allowed[0]
        coverage=resolve_applicable_params(base_params,contract) if contract.get("ok") else {"params":base_params,"removed_non_applicable":[],"available_fields":0,"filled_fields":len(base_params)}
        base_params=coverage.get("params") or {}
        angle_list=list(ff.get("creative_angles") or [])
        angle=angle_list[(max(1,int(cell.get("variant") or 1))-1)%len(angle_list)] if angle_list else ""
        payload={"direction":direction,"variant":cell.get("variant",1),"creative_angle":angle,"title":"","description":"","price":price or 0,"price_mode":cell.get("price_mode") or "","price_unit":cell.get("price_unit") or "",
                 "price_benchmark_logic":("confirmed campaign benchmark" if price_fact else ""),"price_source":(price_fact.get("source") if price_fact else ""),"price_fact_id":(price_fact.get("fact_id") if price_fact else None),
                 "city":cell.get("city", ""),"address":cell.get("city", ""),"category":category,"template_id":selected_tid,
                 "category_path":path,"params":base_params,"parameter_mode":str(entry.get("mode") or "shared"),"parameter_ranges":param_ranges,
                 "intake_contract_version":entry.get("contract_version") or "","parameter_coverage":{"available":coverage.get("available_fields",0),"filled":coverage.get("filled_fields",0),"removed_non_applicable":coverage.get("removed_non_applicable") or []},"images":[],"feed_factory":True}
        payload["media_semantic_guard"]={"mode":"universal_text_image","min_confidence":0.80,"source_of_truth":"title+description+category+direction"}
        if defaults.get("guarantee"): payload["guarantee"]=defaults["guarantee"]
        if defaults.get("phone"): payload["phone"]=defaults["phone"]
        if defaults.get("manager"): payload["manager"]=defaults["manager"]
        if row:
            old=_ff_payload(row)
            for k,v in payload.items():
                if k not in old or old.get(k) in (None,"",[],{}): old[k]=v
            # A zero price is not an authoritative seller fact; it is the absence
            # of one. If confirmed campaign-price memory has since become available,
            # an idempotent materialize retry must heal the existing draft instead
            # of preserving 0 forever and failing official Avito XML validation.
            if price_fact and int(old.get("price") or 0)<=0:
                old["price"]=int(price_fact["price"])
                old["price_benchmark_logic"]="confirmed campaign benchmark"
                old["price_source"]=str(price_fact.get("source") or "")
                old["price_fact_id"]=int(price_fact.get("fact_id") or 0) or None
            # Reconcile visible monetary claims on EVERY idempotent materialize,
            # not only in the exact moment when price changes from 0. A previous
            # run may already have persisted canonical price while stale AI prose
            # still contains another amount. Canonical payload price always wins.
            if str(old.get("description") or "").strip():
                old["description"]=_ff_reconcile_copy_price(str(old.get("description") or ""),int(old.get("price") or 0),str(old.get("price_mode") or ""))
            if str(entry.get("mode") or "shared")=="shared":
                # Newly confirmed direction settings are authoritative for all Feed Factory cards of this direction.
                merged_params=dict(old.get("params") or {}); merged_params.update(base_params)
                resolved_existing=resolve_applicable_params(merged_params,contract) if contract.get("ok") else {"params":merged_params,"removed_non_applicable":[],"available_fields":0,"filled_fields":len(merged_params)}
                old["params"]=resolved_existing.get("params") or {}
                old["parameter_ranges"]=param_ranges
                old["parameter_mode"]="shared"
                old["intake_contract_version"]=entry.get("contract_version") or ""
                old["parameter_coverage"]={"available":resolved_existing.get("available_fields",0),"filled":resolved_existing.get("filled_fields",0),"removed_non_applicable":resolved_existing.get("removed_non_applicable") or []}
            row.payload_json=json.dumps(old,ensure_ascii=False); row.geo=cell.get("city") or row.geo; row.content_hash=CS.content_hash(old); updated+=1
        else:
            row=CS.add_item(db,campaign_id,account_id,payload=payload,source="feed_factory",source_ref=ref,segment=str(cell.get("direction") or ""),geo=str(cell.get("city") or ""),offer=str(cell.get("direction") or ""),status="needs_data")
            db.flush(); created+=1; created_refs.append(ref)
        ids.append(row.id)

    # CAMPAIGN_LISTING_TARIFF_BEFORE_COMMIT_V1: new campaign cards must never
    # become durable before their exact tariff intent is settled. The intent key
    # is based on deterministic source_ref values, not transient database IDs, so
    # a crash after tariff success but before DB commit is safe: retry replays the
    # same tariff intent and recreates/commits the same logical rows without charge.
    if created:
        _listing_billing_basis=(str(campaign_id)+"|"+str(account_id)+"|"+",".join(sorted(created_refs)))
        _listing_billing_key="campaign-listings:"+_hashlib.sha256(_listing_billing_basis.encode("utf-8")).hexdigest()[:32]
        try:
            from app.api.billing import check_and_consume as _billing_consume
            _charge=_billing_consume(account_id,"listings",created,
                                     idempotency_key=_listing_billing_key)
        except Exception as _billing_exc:
            db.rollback()
            print(f"[campaign billing] listing precommit consume failed: {_billing_exc}", flush=True)
            return {"status":"billing_pending","code":"billing_pending","unit":"listings",
                    "created":0,"updated":0,"item_ids":[],"items_total":0,
                    "pending_charge":created,"message":"Тарифный учёт временно недоступен. Новые карточки не сохранены; безопасный повтор не создаст двойного списания."}
        if not _charge.get("allowed",False):
            db.rollback()
            return {"status":"billing_limit","code":"billing_limit","unit":"listings",
                    "created":0,"updated":0,"item_ids":[],"items_total":0,
                    "pending_charge":created,
                    "message":_charge.get("message") or "Тарифный лимит изменился параллельно. Новые карточки не сохранены."}
        ff.setdefault("billing_charged",{})["listings"]=int(ff.get("billing_charged",{}).get("listings") or 0)+created
        ff["billing_pending_listings"]=0
        ff["billing_pending_listings_key"]=""
        # Persist the accounting summary in the SAME campaign transaction as the
        # created rows; do not call _ff_state_save here because it commits early.
        _ff_root,_=_ff_state_load(campaign)
        _ff_root["feed_factory"]=ff
        campaign.settings_json=json.dumps(_ff_root,ensure_ascii=False)
    db.commit()
    ff["item_ids"] = ids; ff["step"]="texts"; _ff_state_save(db,campaign,ff)
    return {"status":"ok","created":created,"updated":updated,"item_ids":ids,"items_total":len(ids)}


@router.post("/{campaign_id}/workflow/mutate")
def ff_workflow_mutate(campaign_id:int, body:FFMutationBody, db=Depends(_db), user=Depends(get_current_user)):
    from app.models.media_asset import MediaAsset
    from app.models.media_link import MediaLink
    from app.services.media_service import asset_public
    allowed=_allowed_accounts(db,user); campaign=_campaign_or_404(db,campaign_id,allowed); changed=[]
    _ff_assert_mutable(campaign)
    wanted=body.item_ids or [r.id for r in _ff_items(db,campaign_id,allowed)]
    media_urls=[]; media_rows=[]
    for mid in body.media_ids or []:
        a=db.query(MediaAsset).filter(MediaAsset.id==int(mid),MediaAsset.deleted_at.is_(None)).first()
        if not a or (a.account_id and a.account_id not in allowed): raise HTTPException(403,"Нет доступа к media")
        pub=asset_public(a); url=pub.get("original_url") or pub.get("preview_url")
        if url: media_urls.append(url); media_rows.append(a)
    media_urls += [str(x) for x in (body.image_urls or []) if str(x).strip()]
    for iid in wanted:
        row=_ff_item_by_id(db,campaign_id,iid,allowed)
        if not row: continue
        p=_ff_payload(row)
        for k,v in (body.patch or {}).items():
            if k in {"title","description","price","city","address","category","template_id","category_path","params","phone","manager","delivery","active","replacement_state","banner","banner_url","images"}:
                p[k]=v
        if media_urls:
            existing_images=list(p.get("images") or [])
            p["images"]=list(dict.fromkeys(media_urls + existing_images))
        if body.hero_media_id:
            row.hero_media_id=int(body.hero_media_id)
        elif media_rows and not row.hero_media_id:
            row.hero_media_id=media_rows[0].id
        for pos,a in enumerate(media_rows):
            role="hero" if row.hero_media_id==a.id else "gallery"
            exists=db.query(MediaLink).filter(MediaLink.media_id==a.id,MediaLink.entity_type=="campaign_item",MediaLink.entity_id==str(row.id),MediaLink.role==role).first()
            if not exists:
                # One authoritative media-link gate for every campaign path.  Do not
                # bypass MediaService.link(), otherwise legacy low-quality providers
                # could be re-attached by this endpoint after the global Avito ban.
                M.link(db,a,"campaign_item",str(row.id),role,pos,created_by=getattr(user,"id",None))
        if "city" in p: row.geo=str(p.get("city") or "")
        _patch_keys=set((body.patch or {}).keys())
        _copy_only={"title","description","price","city","address","phone","manager","delivery","active","replacement_state"}
        if _patch_keys and _patch_keys.issubset(_copy_only) and p.get("media_semantic_evidence"):
            # Explicit copy edits do not change the advertised object/category. Preserve
            # already approved media and bind the evidence to the new copy fingerprint.
            try:
                from app.services.media_semantic_validator import semantic_content_fingerprint
                _fp=semantic_content_fingerprint(p)
                for _ev in (p.get("media_semantic_evidence") or []):
                    if isinstance(_ev,dict) and bool(_ev.get("match")): _ev["content_fingerprint"]=_fp
            except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=4582)
        row.payload_json=json.dumps(p,ensure_ascii=False); row.content_hash=CS.content_hash(p)
        if row.status in ("imported","needs_data","ready"): row.status="draft"
        changed.append(row.id)
    db.commit(); _,ff=_ff_state_load(campaign); ff["step"]="validation"; _ff_state_save(db,campaign,ff)
    return {"status":"ok","changed":changed}


@router.post("/{campaign_id}/workflow/text-file")
async def ff_workflow_text_file(campaign_id:int, file:UploadFile=File(...), db=Depends(_db), user=Depends(get_current_user)):
    """Extract owner-authored ad copy from DOCX/TXT/MD without polluting account knowledge."""
    allowed=_allowed_accounts(db,user); _campaign_or_404(db,campaign_id,allowed)
    name=str(file.filename or "").strip(); low=name.lower()
    if not low.endswith((".docx",".txt",".md")):
        raise HTTPException(422,"Поддерживаются Word DOCX, TXT и MD")
    data=await file.read()
    if not data: raise HTTPException(400,"Файл пустой")
    if len(data)>10*1024*1024: raise HTTPException(413,"Файл больше 10 МБ")
    try:
        if low.endswith(".docx"):
            import io, docx
            doc=docx.Document(io.BytesIO(data))
            parts=[p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]
            for table in doc.tables:
                for row in table.rows:
                    vals=[c.text.strip() for c in row.cells if c.text and c.text.strip()]
                    if vals: parts.append(" | ".join(vals))
            text="\n".join(parts).strip()
        else:
            text=data.decode("utf-8",errors="ignore").strip()
    except Exception as exc:
        raise HTTPException(422,f"Не удалось прочитать файл: {str(exc)[:120]}")
    if not text: raise HTTPException(422,"BORIS не нашёл текст в файле")
    text=text[:30000]
    return {"status":"ok","filename":name,"text":text,"extracted_chars":len(text),"mode":"preserve_text"}


@router.get("/{campaign_id}/workflow/references")
def ff_workflow_references_get(campaign_id:int, db=Depends(_db), user=Depends(get_current_user)):
    allowed=_allowed_accounts(db,user); campaign=_campaign_or_404(db,campaign_id,allowed); _,ff=_ff_state_load(campaign)
    return {"status":"ok","references":dict(ff.get("references") or {})}


@router.post("/{campaign_id}/workflow/references")
def ff_workflow_references_save(campaign_id:int, body:FFReferenceBody, db=Depends(_db), user=Depends(get_current_user)):
    from urllib.parse import urlparse
    from app.models.media_asset import MediaAsset
    allowed=_allowed_accounts(db,user); campaign=_campaign_or_404(db,campaign_id,allowed); _,ff=_ff_state_load(campaign)
    _ff_assert_mutable(campaign)
    links=[]
    for raw in (body.links or [])[:10]:
        u=str(raw or "").strip()
        if not u: continue
        try:
            p=urlparse(u)
            if p.scheme not in {"http","https"} or not p.netloc: raise ValueError()
        except Exception:
            raise HTTPException(422,{"message":"Некорректная ссылка на пример","url":u[:200]})
        links.append(u[:1000])
    media_ids=[]
    for mid in (body.media_ids or [])[:20]:
        a=db.query(MediaAsset).filter(MediaAsset.id==int(mid),MediaAsset.deleted_at.is_(None)).first()
        if not a or (a.account_id and a.account_id not in allowed):
            raise HTTPException(403,"Нет доступа к приложенному скриншоту")
        media_ids.append(int(mid))
    mode=str(body.mode or "inspired").strip()
    if mode not in {"inspired","preserve_text"}: raise HTTPException(422,"Неизвестный режим референсов")
    image_analysis=""
    if media_ids:
        try:
            import base64, mimetypes, os
            from app.services import media_storage as _mst
            api_key=os.getenv("OPENAI_API_KEY")
            if api_key:
                content=[{"type":"input_text","text":(
                    "Проанализируй ОБЕЗЛИЧЕННЫЕ скриншоты рекламных объявлений как визуальные референсы для маркетолога BORIS. "
                    "Оцени только структуру, крупные визуальные блоки, тип подачи, цветовые акценты, иконки, иерархию и общий тон. "
                    "Не пытайся читать, восстанавливать или угадывать имена, телефоны, адреса, номера, лица, цены или другой мелкий/идентифицирующий текст. "
                    "Если текст неразборчив — так и считай: используй только безопасно видимую композицию.") }]
                for mid in media_ids[:8]:
                    a=db.query(MediaAsset).get(int(mid))
                    if not a or not a.storage_key or not _mst.exists(a.storage_key):
                        continue
                    path=_mst.abs_path(a.storage_key)
                    # External vision never receives the raw client screenshot:
                    # strip metadata, downscale and blur fine text/identifiers.
                    from app.services.ai_privacy import sanitize_external_reference_image_bytes
                    with open(path,"rb") as fh:
                        raw, mime = sanitize_external_reference_image_bytes(
                            fh.read(), max_side=512, blur_radius=3.5
                        )
                    data=base64.b64encode(raw).decode("ascii")
                    content.append({"type":"input_image","image_url":f"data:{mime};base64,{data}"})
                if len(content)>1:
                    import hashlib as _hashlib_ref, json as _json_ref
                    fp=_hashlib_ref.sha256(_json_ref.dumps({"media_ids":media_ids,"content":str(body.text or "")[:30000],"mode":mode},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
                    previous=dict(ff.get("references") or {})
                    if previous.get("image_analysis_fingerprint")==fp and previous.get("image_analysis"):
                        image_analysis=str(previous.get("image_analysis") or "")[:12000]
                    else:
                        paid=_ff_guarded_openai_response(str(campaign.default_account_id or ""),"campaign_reference_vision","gpt-5-mini",[{"role":"user","content":content}],1800,"ref:"+fp,timeout=120)
                        image_analysis=str(paid.get("text") or "")[:12000]
        except Exception as exc:
            image_analysis=""
    refs={"links":links,"text":str(body.text or "")[:30000],"mode":mode,"media_ids":media_ids,
          "uniqueness_parameters":str(body.uniqueness_parameters or "")[:8000],
          "image_analysis":image_analysis,
          "image_analysis_fingerprint":(locals().get("fp") if image_analysis else ""),
          "notice":"BORIS использует референсы как ориентир по структуре и подаче. Факты клиента остаются приоритетом."}
    ff["references"]=refs; _ff_state_save(db,campaign,ff)
    return {"status":"ok","references":refs,"screenshots_analyzed":bool(image_analysis)}


def _ff_clean_title_for_avito(raw_title: str, topic: str = "") -> str:
    import re as _re
    title=str(raw_title or "").strip()
    # Feed Factory shows one concrete client-ready ad, never raw spintax syntax.
    m=_re.search(r"\{([^{}]+)\}",title)
    if m:
        variants=[x.strip() for x in m.group(1).split("|") if x.strip()]
        title=(variants[0] if variants else title)
    title=_re.sub(r"[{}|]"," ",title)
    title=_re.sub(r"<[^>]+>"," ",title)
    # Global BORIS rule for every Avito account: titles contain only letters,
    # digits and spaces. No punctuation, currency signs, emoji or separators.
    title=_re.sub(r"[^0-9A-Za-zА-Яа-яЁё ]+"," ",title)
    title=" ".join(title.split()).strip()
    if not title: title=" ".join(str(topic or "Объявление").split())
    if len(title)>50:
        title=(title[:50].rsplit(" ",1)[0] or title[:50]).strip(" -–—,.;:!")
    return title


def _ff_explicit_copy_price_claims(description: str) -> list[int]:
    """Extract only listing-price-like money claims from copy.

    Deliberately ignores unrelated sums such as «монтаж 1500 ₽» or delivery
    tariffs. A claim is considered the listing price only when the amount is
    directly governed by «цена/стоимость» (optionally товара/изделия/карточки/
    объявления). This is strict enough to be a validation invariant without
    rewriting legitimate secondary commercial facts.
    """
    import re as _re
    text=str(description or "")
    rub=r"(?:₽|руб(?:\.|лей|ля|ль)?)"
    num=r"[0-9][0-9\s]*(?:[.,][0-9]+)?"
    pat=_re.compile(rf"(?i)\b(?:цена|стоимость)(?:\s+(?:товара|изделия|карточки|объявления))?\s*(?:[:.\-–—]|составляет|от|—)?\s*(?:от\s+)?(?P<amount>{num})\s*{rub}")
    out=[]
    for m in pat.finditer(text):
        raw=str(m.group('amount') or '').replace(' ','').replace(',','.')
        try: value=int(round(float(raw)))
        except Exception: continue
        if value>0: out.append(value)
    return out


def _ff_reconcile_copy_price(description: str, price: int = 0, price_mode: str = "") -> str:
    """Keep visible card-price claims consistent with the canonical payload price.

    The model is allowed to write sales prose, but it is never the source of truth
    for money. We touch only explicit card-price contexts ("цена", "стоимость",
    "от N ₽") and leave unrelated confirmed sums/tariffs in factual company copy
    alone. With no canonical price, unsupported explicit card-price claims are
    removed rather than guessed.
    """
    import re as _re
    text=str(description or "")
    try: canonical=int(price or 0)
    except Exception: canonical=0
    rub=r"(?:₽|руб(?:\.|лей|ля|ль)?)"
    num=r"[0-9][0-9\s]*(?:[.,][0-9]+)?"
    # Whole standalone lines labelled as the listing price are unambiguously the
    # card price and can be deterministically healed/removed.
    line_pat=_re.compile(rf"(?im)^\s*(?:цена|стоимость)(?:\s+(?:товара|изделия|карточки|объявления))?\s*[:.\-–—]?\s*(?:от\s+)?{num}\s*{rub}\s*[.!]?\s*$")
    # Inline lead phrases such as "сориентируем по стоимости от 7280 ₽" are also
    # listing-price claims. Bound the replacement to the amount itself so the prose
    # remains intact.
    inline_pat=_re.compile(rf"(?i)(?P<prefix>\b(?:цена|стоимость)(?:\s+(?:товара|изделия|карточки|объявления))?\s*(?:[:.\-–—]|составляет|от|—)?\s*)(?P<from>от\s+)?(?P<amount>{num})\s*(?P<rub>{rub})")
    generic_from_pat=_re.compile(rf"(?i)(?<![0-9])от\s+(?P<amount>{num})\s*(?P<rub>{rub})")
    mode_from=str(price_mode or "").strip().lower()=="from"
    def _line(m):
        if canonical<=0:
            return ""
        lead="Цена: "
        return f"{lead}{'от ' if mode_from else ''}{canonical} ₽."
    text=line_pat.sub(_line,text)
    def _inline(m):
        if canonical<=0:
            return m.group('prefix').rstrip(' :.-–—')
        # Preserve explicit "от" already present even when price_mode was not
        # separately persisted; never strengthen an "от" claim into fixed price.
        prefix=m.group('prefix')
        has_from=bool(m.group('from')) or bool(_re.search(r"\bот\s*$",prefix,_re.I)) or mode_from
        if _re.search(r"\bот\s*$",prefix,_re.I):
            return prefix+f"{canonical} ₽"
        return prefix+("от " if has_from else "")+f"{canonical} ₽"
    text=inline_pat.sub(_inline,text)
    # A bare "от N ₽" in sales copy is almost always a card-price claim. Change
    # only when a canonical price exists; when absent, strip the numeric claim.
    def _generic(m):
        if canonical<=0: return ""
        return f"от {canonical} ₽"
    text=generic_from_pat.sub(_generic,text)
    text=_re.sub(r"[ \t]+\n","\n",text)
    text=_re.sub(r"\n{3,}","\n\n",text).strip()
    return text


def _ff_plain_description_for_avito(raw: str, *, brief: str = "", price: int = 0, price_mode: str = "") -> str:
    import html as _html, re as _re
    text=str(raw or "")
    text=_re.sub(r"(?i)<br\s*/?>","\n",text)
    text=_re.sub(r"(?i)<li[^>]*>","• ",text)
    text=_re.sub(r"(?i)</li>","\n",text)
    text=_re.sub(r"(?i)</?(?:ul|ol|p|div|em|i|span)[^>]*>","",text)
    # Avito supports emphasis in description; preserve <strong>/<b> so client-facing block headings and key benefits stay visually scannable.
    text=_re.sub(r"(?i)<b([^>]*)>","<strong>",text)
    text=_re.sub(r"(?i)</b>","</strong>",text)
    text=_re.sub(r"(?i)<strong[^>]*>","<strong>",text)
    text=_re.sub(r"<[^>]+>","",text)
    text=_html.unescape(text)
    # Render every remaining spintax group to the first concrete option.
    for _ in range(8):
        new=_re.sub(r"\{([^{}]+)\}",lambda m:(m.group(1).split("|")[0].strip()),text)
        if new==text: break
        text=new
    strict=bool(_re.search(r"(?:не\s+(?:выдум|придум|добав)|без\s+выдум|только\s+подтвержд[её]нн(?:ые|ая|ую|ых)\s+(?:данн|факт)|используй\s+только\s+подтвержд)",str(brief or ""),_re.I))
    if strict:
        # Remove fabricated example blocks and absolute claims when client explicitly forbids invention.
        lines=text.splitlines(); out=[]; skipping_example=False
        risky=_re.compile(r"(?:гарант(?:ия|ируем)|идеальн|как\s+нов|первоздан|любой\s+сложност|ответим\s+за|за\s+\d+\s*минут|всего\s+за\s+несколько|уже\s+сегодня|быстр(?:ая|ую|о)\s+достав|обещаю|досрочн(?:ый|ого)\s+выкуп|фиксированн(?:ая|ой)\s+аренд|первоначальн(?:ый|ого)\s+взнос|без\s+комисси|без\s+депозит|без\s+залог|страховк|техобслужив|\bТО\b|ремонт\s+(?:включ|за\s+наш)|для\s+работы\s+в\s+такси|такси-сервис|полностью\s+рабоч|хорошем\s+состоянии|без\s+рисков)",_re.I)
        resume=_re.compile(r"^(?:✅|🎁|Цена|Стоимость|Поводы|Что учиты|Вы получите)",_re.I)
        for line in lines:
            stripped=line.strip()
            if _re.search(r"пример\s+(?:моей|нашей)\s+работ",stripped,_re.I):
                skipping_example=True; continue
            if skipping_example:
                if resume.search(stripped): skipping_example=False
                else: continue
            if risky.search(stripped):
                # Preserve only if the same factual phrase is explicitly present in the client's brief.
                words=[w for w in _re.findall(r"[а-яa-z0-9]+",stripped.lower()) if len(w)>5]
                if not any(w in str(brief or "").lower() for w in words[:4]):
                    continue
            out.append(line)
        text="\n".join(out)
    if price and str(price_mode or "")=="from":
        # The generic generator treats equal min/max as fixed; Feed Factory preserves client's «от» semantics.
        text=_re.sub(rf"(?i)(?:ровно|фиксированн(?:ая|ой|ую)?\s+(?:цена|стоимость)?\s*)\s*{int(price)}\s*(?:₽|руб(?:лей|\.)?)?",f"от {int(price)} ₽",text)
        text=_re.sub(rf"(?i)цена:\s*(?:ровно\s*)?{int(price)}\s*(?:₽|руб(?:лей|\.)?)?",f"Цена: от {int(price)} ₽",text)
    text=_re.sub(r"(?m)^\s*#[^\n]+$","",text)
    # BORIS readability contract: one sentence per line inside each semantic block.
    # This prevents Avito descriptions from collapsing into a dense wall of text.
    _blocks=[]
    for _block in _re.split(r"\n\s*\n",text):
        _lines=[]
        for _line in _block.splitlines():
            _line=_line.strip()
            if not _line: continue
            # Keep bullet/list rows intact; split prose after terminal punctuation.
            if _re.match(r"^(?:[•✓✔✅❌—-]|\d+[.)])\s*",_line):
                _parts=[_line]
            else:
                _parts=[x.strip() for x in _re.split(r"(?<=[.!?])\s+(?=[А-ЯЁA-Z0-9«])",_line) if x.strip()]
            for _part in _parts:
                # Add a terminal full stop to ordinary prose that accidentally lacks punctuation.
                _plain=_re.sub(r"<[^>]+>","",_part).strip()
                if _plain and not _re.search(r"[.!?:;…]$",_plain) and not _re.match(r"^[A-ZА-ЯЁ0-9 \-/]{4,}$",_plain):
                    _part=_part+"."
                _lines.append(_part)
        if _lines: _blocks.append("\n".join(_lines))
    text="\n\n".join(_blocks)
    text=_re.sub(r"[ \t]+\n","\n",text)
    text=_re.sub(r"\n{3,}","\n\n",text).strip()
    return text


def _ff_bulk_safe_copy(topic: str, payload: dict, ff: dict) -> tuple[str,str]:
    """Fast factual copy for large strict-facts campaigns; generic across accounts/niches."""
    variant=max(1,int(payload.get("variant") or 1)); city=str(payload.get("city") or "").strip()
    ctx=ff.get("account_context") or {}; client=str(ctx.get("client_description") or "")
    clean_topic=" ".join(str(topic or "Предложение").split()).strip()
    titles=_ff_title_variants(clean_topic,str(ff.get("brief") or "")); title=titles[(variant-1)%len(titles)] if titles else clean_topic
    openings=[
        f"{clean_topic}: начнём с вашей задачи и исходных условий.",
        f"По направлению «{clean_topic}» сначала уточним, какой результат вам нужен.",
        f"Нужен {clean_topic.lower()}? Опишите задачу — разберём подходящий следующий шаг.",
        f"{clean_topic} — обсудим ваш запрос и важные для выбора условия.",
        f"Если рассматриваете {clean_topic.lower()}, начнём с короткого разбора вашей ситуации.",
        f"По услуге «{clean_topic}» работаем от конкретной задачи клиента, без неподтверждённых обещаний.",
        f"{clean_topic}: уточним ваши требования и определим, что важно проверить в первую очередь.",
        f"Перед началом по направлению «{clean_topic}» разберём исходные данные и цель обращения.",
        f"{clean_topic}: расскажите, что именно требуется — предложим понятный следующий шаг.",
        f"Обращение по теме «{clean_topic}» начинаем с уточнения задачи и ваших критериев.",
        f"{clean_topic}: можно начать с консультации и разбора исходных условий.",
        f"Если у вас задача по теме «{clean_topic}», опишите её в сообщении — разберём детали.",
        f"По направлению «{clean_topic}» сначала фиксируем требования клиента, затем обсуждаем варианты.",
        f"{clean_topic}: обсудим цель обращения и ограничения, которые важно учитывать.",
        f"Для задачи «{clean_topic}» начнём с того, что уже известно и что нужно уточнить.",
        f"{clean_topic}: разберём запрос последовательно и без выдуманных условий.",
        f"Если вам актуален {clean_topic.lower()}, напишите исходные данные — начнём с них.",
        f"По теме «{clean_topic}» можно сначала получить разбор задачи и понять дальнейшие действия.",
        f"{clean_topic}: уточним контекст обращения и критерии, которые важны именно вам.",
        f"Начать работу по направлению «{clean_topic}» можно с сообщения с вашей задачей и вводными.",
    ]
    goals=[
        "Напишите в Avito — обсудим вашу ситуацию и следующий шаг.",
        "Оставьте сообщение в Avito, чтобы начать с консультации.",
        "Напишите, что для вас важно — начнём с уточнения задачи.",
        "Свяжитесь через сообщения Avito — разберём исходные данные.",
        "Опишите задачу в сообщении, чтобы определить следующий шаг.",
        "Напишите в Avito — ответим по существу вашего запроса.",
        "Начните с сообщения: укажите цель обращения и важные условия.",
        "Оставьте сообщение — обсудим, что нужно проверить перед началом работы.",
    ]
    opening=openings[(variant-1)%len(openings)]
    if city:
        opening=f"{clean_topic} · {city} — "+opening.split(":",1)[-1].strip().lstrip("— ")
    lines=[opening]
    lines.append("Сначала разберём сам запрос: что нужно получить, какие исходные условия уже известны и какие параметры для вас принципиальны. Так разговор начинается не с общих обещаний, а с конкретной задачи, которую можно последовательно уточнить.")
    lines.append("Дальше сопоставим ваши вводные с доступными вариантами решения и обозначим, что нужно уточнить до следующего шага. Если часть данных пока неизвестна, это нормально: начните с того, что уже есть, без необходимости заранее собирать полный технический список.")
    lines.append("До принятия решения можно задать вопросы по тем условиям, которые для вас важны. В объявлении используются только подтверждённые сведения: без выдуманных сроков, гарантий или характеристик, которых нет в исходных данных.")
    # Add only facts explicitly present in account knowledge and relevant to this topic.
    low=(client+" "+clean_topic).lower()
    if any(x in clean_topic.lower() for x in ("квартир","недвижимост","ипотек")) and client:
        facts=[]
        if "семь" in client.lower(): facts.append("семьи с детьми")
        if "студент" in client.lower(): facts.append("покупка квартиры для ребёнка-студента")
        if "инвестор" in client.lower(): facts.append("инвестиционные задачи")
        if facts: lines.append("Среди подтверждённых задач: "+", ".join(facts)+".")
        if "банкрот" in client.lower(): lines.append("Важно: после банкротства возможность работы ограничена правилами агентства.")
    lines.append(goals[(variant-1)%len(goals)])
    return _ff_clean_title_for_avito(title,clean_topic),"\n\n".join(lines)


def _ff_zero_touch_safe_description(topic: str, payload: dict, brief: str = "") -> str:
    """Honest but useful fallback when the client explicitly forbids invented facts."""
    import re as _re
    topic=" ".join(str(topic or "").split()).strip()
    city=" ".join(str(payload.get("city") or "").split()).strip()
    price=int(payload.get("price") or 0)
    price_mode=str(payload.get("price_mode") or "")
    unit=str(payload.get("price_unit") or "").strip()
    low=topic.lower()
    product=any(x in low for x in ("плитк","самокат","мебел","шкаф","кухн","материал","товар","бетон","кирпич","оборудован","техник"))
    lines=[topic]
    if city: lines.append("География: "+city+".")
    if price:
        prefix="от " if price_mode=="from" else ""
        suffix=(" "+unit) if unit else ""
        lines.append(f"Стоимость: {prefix}{price} ₽{suffix}.")
    if product:
        lines.append("Напишите в сообщения Avito, какой вариант и количество вам нужны. Уточним параметры и итоговую стоимость по вашей задаче.")
    else:
        lines.append("Опишите задачу в сообщениях Avito. Если удобно, приложите фото — уточним объём работ и итоговую стоимость до начала работ.")
    lines.append("Нажмите «Написать», чтобы получить ответ по вашей задаче.")
    return "\n\n".join(lines)



@router.get("/{campaign_id}/workflow/photo-angles")
def ff_photo_angles_status(campaign_id:int, db=Depends(_db), user=Depends(get_current_user)):
    from app.models.photo_session import PhotoSession
    from app.models.photo_shot import PhotoShot
    allowed=_allowed_accounts(db,user); campaign=_campaign_or_404(db,campaign_id,allowed); _,ff=_ff_state_load(campaign)
    account_id=str(ff.get("account_id") or campaign.default_account_id or "")
    sessions=[]
    for s in db.query(PhotoSession).filter(PhotoSession.account_id==account_id).order_by(PhotoSession.id.desc()).limit(100).all():
        st=dict(s.settings or {})
        if int(st.get("campaign_id") or 0)!=int(campaign_id): continue
        sessions.append(s)
    session_ids=[int(s.id) for s in sessions]
    shots=(db.query(PhotoShot).filter(PhotoShot.session_id.in_(session_ids or [0])).all())
    approved=[x for x in shots if x.status=="approved" and x.media_id]
    safe=[x for x in approved if int(x.consistency_score or 0)>=85 and int(x.photo_score or 0)>=85]
    return {"status":"ok","campaign_id":campaign_id,"account_id":account_id,"target":int(ff.get("generated_photo_target") or 20),
            "generated":len(approved),"safe_auto":len(safe),"source_count":len(sessions),
            "completed":bool(len(approved)>=int(ff.get("generated_photo_target") or 20)),
            "sessions":[{"id":x.id,"status":x.status,"planned":x.shots_planned,"done":x.shots_done,"rejected":x.shots_rejected} for x in sessions]}

@router.post("/{campaign_id}/workflow/photo-angles")
def ff_photo_angles_generate(campaign_id:int, body:FFPhotoAnglesBody, db=Depends(_db), user=Depends(get_current_user)):
    """Generate a durable set of new camera angles from the best owner-uploaded campaign photos.

    Reuses the authoritative Photo Studio engine. No second image pipeline. Existing
    completed campaign batch is idempotent and will not be charged/generated again.
    """
    import math
    from app.models.media_asset import MediaAsset
    from app.models.media_folder import MediaFolder
    from app.models.photo_session import PhotoSession
    from app.services import media_storage as _st
    from app.services import photo_studio as _PS
    from app.services import jobs as _jobs
    from app.api.photo import SessionBody as _SessionBody, create_session as _create_photo_session, start_master as _start_photo_master
    allowed=_allowed_accounts(db,user); campaign=_campaign_or_404(db,campaign_id,allowed); _,ff=_ff_state_load(campaign)
    account_id=str(ff.get("account_id") or campaign.default_account_id or "")
    if not account_id or account_id not in allowed: raise HTTPException(403,"Нет доступа к аккаунту")
    target=max(4,min(int(body.target_count or 20),40)); source_count=max(1,min(int(body.source_count or math.ceil(target/4)),10))
    existing=[]
    for sess in db.query(PhotoSession).filter(PhotoSession.account_id==account_id).order_by(PhotoSession.id.desc()).limit(100).all():
        st=dict(sess.settings or {})
        if int(st.get("campaign_id") or 0)==int(campaign_id): existing.append(sess)
    if existing and sum(int(x.shots_done or 0) for x in existing)>=target:
        return {"status":"ok","already_ready":True,"target":target,"generated":sum(int(x.shots_done or 0) for x in existing),"session_ids":[x.id for x in existing]}
    folder=(db.query(MediaFolder).filter(MediaFolder.account_id==account_id,MediaFolder.name==f"campaign_{campaign_id}_manual_photos",MediaFolder.deleted_at.is_(None)).order_by(MediaFolder.id.desc()).first())
    if not folder: raise HTTPException(409,"Сначала добавьте исходные фотографии клиента")
    assets=(db.query(MediaAsset).filter(MediaAsset.account_id==account_id,MediaAsset.folder_id==folder.id,MediaAsset.deleted_at.is_(None),MediaAsset.source_type=="upload").order_by(MediaAsset.id.asc()).all())
    if not assets: raise HTTPException(409,"Не найдены исходные фотографии клиента")
    ranked=[]
    for a in assets:
        try:
            path=_st.abs_path(a.storage_key); q=_PS.validate_shot(path); score=int(q.get("photo_score") or 0)
            if q.get("critical_errors"): score-=40
        except Exception: score=0
        ranked.append((score,a))
    ranked.sort(key=lambda x:(x[0],-int(x[1].id)),reverse=True); picked=[a for _,a in ranked[:source_count]]
    per_source=max(1,min(3,int(math.ceil(target/max(1,len(picked))))-1))
    codes=["VEHICLE_FRONT_3Q","VEHICLE_REAR_3Q","VEHICLE_SIDE"][:per_source]
    made=[]
    for score,a in ranked[:len(picked)]:
        res=_create_photo_session(_SessionBody(account_id=account_id,reference_media_id=int(a.id),preset="client_auto",shots=codes,mode="preserve_reference",prompt="Сохранить точно тот же автомобиль и показать его с других естественных ракурсов. Не менять марку, модель, поколение, кузов, цвет, диски, оптику, номер и видимые особенности.",batch_confirmed=True),db,user)
        sess=db.query(PhotoSession).get(int(res["session_id"])); st=dict(sess.settings or {}); st.update({"campaign_id":campaign_id,"campaign_photo_angles":True,"source_quality_score":int(score),"target_count":target}); sess.settings=st; db.commit()
        started=_start_photo_master(sess.id,db,user); made.append({"session_id":sess.id,"reference_media_id":int(a.id),"source_quality_score":int(score),"job_id":started.get("job_id")})
    ff["creative_mode"]="photo_angles"; ff["banner_mode"]="disabled"; ff["banner_required"]=False; ff["generated_photo_target"]=target; ff["generated_photo_source_count"]=len(made); _ff_state_save(db,campaign,ff)
    return {"status":"ok","already_ready":False,"target":target,"source_count":len(made),"sessions":made,"message":f"BORIS создаёт до {target} новых ракурсов из лучших исходных фотографий."}

@router.post("/{campaign_id}/workflow/copywriter/competitors")
def ff_competitor_copywriter(campaign_id:int, body:FFCompetitorCopyBody, db=Depends(_db), user=Depends(get_current_user)):
    """Top-10 Avito copy audit -> reusable positioning brief; optional durable rewrite."""
    import asyncio, os, json as _j
    from app.api import parser as P
    allowed=_allowed_accounts(db,user); campaign=_campaign_or_404(db,campaign_id,allowed); _,ff=_ff_state_load(campaign)
    rows=_ff_items(db,campaign_id,allowed); wanted=set(int(x) for x in (body.item_ids or [])); scoped=[r for r in rows if not wanted or r.id in wanted]
    if not scoped: raise HTTPException(400,"Нет объявлений для анализа")
    first=_ff_payload(scoped[0]); query=str(body.query or first.get("direction") or scoped[0].segment or first.get("title") or "").strip(); city=str(body.city or first.get("city") or scoped[0].geo or "Москва").strip()
    if len(query)<3: raise HTTPException(400,"Не удалось определить запрос для анализа конкурентов")
    async def collect():
        import re as _re_comp, asyncio as _aio_comp
        # One Avito query can be captchaed or too narrow. Reuse explicit commercial
        # queries from the owner's brief, then fall back to the resolved direction.
        quoted=[]
        for _m in _re_comp.findall(r"[«\"]([^»\"]{3,120})[»\"]", str(ff.get("brief") or "")):
            _q=" ".join(str(_m).split()).strip()
            if _q and _q.lower() not in {x.lower() for x in quoted}: quoted.append(_q)
        queries=[]
        for _q in [query]+quoted:
            _q=" ".join(str(_q or "").split()).strip()
            if len(_q)>=3 and _q.lower() not in {x.lower() for x in queries}: queries.append(_q)
            if len(queries)>=6: break
        found=[]; seen=set()
        for _q in queries:
            url=f"https://www.avito.ru/{P._normalize_city(city)}?q={_q.replace(' ','+')}"; items=[]; html=""
            for _ in range(4):
                try: html=(await P._fetch_avito_page_headed(url))["html"]
                except Exception: continue
                if "firewallCaptcha" in html or "geetest_captcha" in html: continue
                items=P._extract_listing_items(html)
                if items: break
            for _it in items:
                _url=str(_it.get("url") or "").strip(); _key=_url or (str(_it.get("title") or "").strip().lower()+"|"+str(_it.get("price") or ""))
                if not _key or _key in seen: continue
                seen.add(_key); found.append(_it)
                if len(found)>=requested_count: break
            if len(found)>=requested_count: break
        picked=found[:requested_count]
        # Detail pages are independent; parallelize conservatively so top-10 analysis
        # does not turn into a 3-5 minute serial browser task.
        _sem=_aio_comp.Semaphore(3)
        async def _one(it):
            detail={}
            if it.get("url"):
                async with _sem:
                    try: detail=await P._fetch_item_detail_headed(it.get("url") or "")
                    except Exception: detail={}
            return {"title":it.get("title") or "","price":it.get("price"),"url":it.get("url") or "","description":str(detail.get("description") or "")[:5000],"photos_count":detail.get("images_count")}
        return await _aio_comp.gather(*[_one(it) for it in picked]) if picked else []
    requested_count=max(1,min(int(body.count or 10),10))
    competitors=asyncio.run(collect())[:requested_count]
    min_needed=min(3,requested_count)
    if len(competitors)<min_needed: raise HTTPException(503,{"message":"Avito сейчас не дал достаточно объявлений для качественного сравнения. BORIS не будет выдумывать анализ.","found":len(competitors)})
    client_ctx=ff.get("account_context") or {}; brief=str(ff.get("brief") or ""); own=[{"title":_ff_payload(r).get("title"),"description":str(_ff_payload(r).get("description") or "")[:2500]} for r in scoped[:5]]
    prompt=(f"Ты — ведущий performance-копирайтер Avito. Проанализируй {len(competitors)} реально найденных конкурентов. Не копируй чужие фразы дословно и не приписывай клиенту чужие факты. "
            "Верни СТРОГО JSON: {summary:string, strong_patterns:[string], weak_patterns:[string], differentiation:[string], hooks:[string], seo_phrases:[string], copy_brief:string}. "
            "strong_patterns — сильные приемы подачи/структуры; weak_patterns — клише и слабости рынка; differentiation — как отстроиться только на подтвержденных фактах клиента; hooks — 5-8 вариантов первых строк/углов без выдуманных обещаний; seo_phrases — релевантные формулировки из рынка; copy_brief — подробная инструкция сильному копирайтеру: конкретика, сегмент, боль→решение→доказательство→CTA, короткие абзацы, без воды.\n"
            "КЛИЕНТ:\n"+_j.dumps({"brief":brief,"context":client_ctx,"own_ads":own},ensure_ascii=False)+"\nКОНКУРЕНТЫ:\n"+_j.dumps(competitors,ensure_ascii=False))
    api_key=os.getenv("OPENAI_API_KEY"); analysis={}
    if not api_key: raise HTTPException(503,"Модуль копирайтера временно недоступен")
    try:
        import hashlib as _hashlib_comp
        comp_fp=_hashlib_comp.sha256(_j.dumps({"query":query,"city":city,"competitors":competitors,"client":client_ctx,"brief":brief},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        previous_comp=((ff.get("references") or {}).get("competitor_audit") or {})
        if previous_comp.get("fingerprint")==comp_fp and isinstance(previous_comp.get("analysis"),dict):
            analysis=dict(previous_comp.get("analysis") or {})
        else:
            paid=_ff_guarded_openai_response(str(campaign.default_account_id or ""),"campaign_competitor_copy_audit","gpt-5.4-mini",prompt,3000,"competitor:"+comp_fp,timeout=120)
            raw=str(paid.get("text") or "").strip(); raw=raw.removeprefix("```json").removesuffix("```").strip(); analysis=_j.loads(raw)
    except Exception as exc: raise HTTPException(502,"Не удалось завершить разбор конкурентов") from exc
    refs=dict(ff.get("references") or {}); refs["competitor_audit"]={"query":query,"city":city,"requested_count":requested_count,"competitors":competitors,"analysis":analysis,"count":len(competitors),"fingerprint":comp_fp}; refs["competitor_audit_text"]=_j.dumps(analysis,ensure_ascii=False); ff["references"]=refs; _ff_state_save(db,campaign,ff)
    rewrite=None
    if body.apply:
        instruction=("Используй свежий анализ ТОП-конкурентов BORIS ниже. Возьми сильные ПРИНЦИПЫ, но не копируй чужой текст и чужие факты. Сделай текст заметно сильнее рынка и отстрой позиционирование.\n"+str(analysis.get("copy_brief") or "")+"\nОТСТРОЙКА:\n"+"\n".join(str(x) for x in (analysis.get("differentiation") or [])))
        rewrite=ff_workflow_enhance(campaign_id,FFEnhanceBody(item_ids=[r.id for r in scoped],prompt=instruction),db,user)
    return {"status":"ok","query":query,"city":city,"count":len(competitors),"competitors":competitors,"analysis":analysis,"rewrite":rewrite}


def _ff_verified_vehicle_facts(payload: dict) -> dict:
    """Canonical vehicle fact projection. Never infers missing year/mileage/specs."""
    params=dict(payload.get("params") or {})
    aliases={
        "year":["year","Year","YearIssue","ProductionYear","Год выпуска"],
        "mileage":["mileage","Mileage","Kilometrage","Пробег"],
        "color":["color","Color","Цвет"],
        "generation":["generation","Generation","Поколение"],
        "body_type":["body_type","BodyType","Кузов"],
        "transmission":["transmission","Transmission","Gearbox","Коробка передач"],
        "engine":["engine","Engine","EngineVolume","Двигатель"],
        "trim":["trim","Trim","Modification","Комплектация"],
        "drive":["drive","DriveType","Привод"],
        "plate":["plate","license_plate","Plate","Госномер"],
    }
    out={}
    for dst,keys in aliases.items():
        for k in keys:
            v=payload.get(k) if k in payload else params.get(k)
            if v not in (None,"",[],{}): out[dst]=v; break
    return out


def _ff_paid_qa_forbidden(campaign) -> bool:
    """Fail closed for explicit QA campaigns/accounts unless paid QA was deliberately enabled."""
    import os as _os, re as _re
    if str(_os.environ.get("BORIS_QA_ALLOW_PAID", "")).strip().lower() in {"1","true","yes","on"}:
        return False
    name=str(getattr(campaign,"name","") or "").strip().lower()
    account=str(getattr(campaign,"default_account_id","") or "").strip().lower()
    explicit_qa=bool(_re.search(r"(^|[^a-z])qa([^a-z]|$)", name))
    acceptance_marker=bool(
        _re.search(r"(^|[^a-z0-9])e2e([^a-z0-9]|$)", name)
        or "боевой тест" in name
        or "production test" in name
        or "prod test" in name
        or "acceptance test" in name
    )
    return account.startswith("qa_") or explicit_qa or acceptance_marker


def _ff_paid_qa_response():
    return {
        "status":"qa_paid_blocked", "changed":[], "errors":[],
        "code":"qa_paid_ai_disabled",
        "message":"Платный AI для QA-кампаний отключён в production. Используйте mock/zero-paid проверку."
    }


@router.post("/{campaign_id}/workflow/enhance")
def ff_workflow_enhance(campaign_id:int, body:FFEnhanceBody, db=Depends(_db), user=Depends(get_current_user)):
    """Durable prompt-based rewrite for selected/all ads. Returns immediately."""
    import hashlib
    from app.services.jobs import enqueue, job_public
    allowed=_allowed_accounts(db,user); campaign=_campaign_or_404(db,campaign_id,allowed)
    _ff_assert_mutable(campaign)
    if _ff_paid_qa_forbidden(campaign): return _ff_paid_qa_response()
    prompt=str(body.prompt or "").strip()
    if not prompt: raise HTTPException(400,"Напишите, как усилить объявления")
    rows=_ff_items(db,campaign_id,allowed); ids=[int(x) for x in (body.item_ids or [r.id for r in rows])]
    ids=[r.id for r in rows if r.id in set(ids)]
    if not ids: raise HTTPException(400,"Не выбраны объявления")
    account_id=str(campaign.default_account_id or "").strip()
    if not account_id or account_id not in allowed: raise HTTPException(403,"Нет доступа к аккаунту")
    digest=hashlib.sha1((prompt+"|"+",".join(map(str,ids))).encode("utf-8")).hexdigest()[:20]
    row,created=enqueue(db,"campaign_enhance",payload={"campaign_id":campaign_id,"account_id":account_id,"stage":"enhance_texts","item_ids":ids,"remaining":ids,"prompt":prompt},
                        owner_user_id=getattr(user,"id",None),account_id=account_id,fingerprint=f"campaign_enhance:{campaign_id}:{digest}",progress_total=len(ids))
    db.commit(); db.refresh(row)
    return {"status":"ok","job":job_public(row),"created":created,"persistent":True,"items":len(ids)}


@router.post("/{campaign_id}/workflow/texts")
def ff_workflow_texts(campaign_id:int, body:FFTextBody, db=Depends(_db), user=Depends(get_current_user)):
    import os
    from app.api.avito import GenerateAdsRequest, generate_ads
    allowed=_allowed_accounts(db,user); campaign=_campaign_or_404(db,campaign_id,allowed); _,ff=_ff_state_load(campaign)
    _ff_assert_mutable(campaign)
    if _ff_paid_qa_forbidden(campaign): return _ff_paid_qa_response()
    requested_ids=[int(x) for x in (body.item_ids or []) if int(x)>0]
    if not requested_ids:
        return {"status":"selection_required","changed":[],"errors":[],"code":"text_selection_required",
                "message":"Выберите объявления, для которых нужно создать или обновить тексты."}
    wanted=set(requested_ids); rows=[r for r in _ff_items(db,campaign_id,allowed) if r.id in wanted]
    _idem=str(body.idempotency_key or "").strip()
    _text_cap=max(1,min(100,int(os.environ.get("BORIS_PAID_TEXT_BATCH_CAP","20") or 20)))
    _confirmed=max(0,int(getattr(body,"confirmed_paid_items",0) or 0))
    if len(rows)>1 and not _idem:
        return {"status":"idempotency_required","changed":[],"errors":[],"code":"paid_text_idempotency_required",
                "requested":len(rows),"message":"Для нескольких платных текстов нужен ключ операции, чтобы повтор запроса не создавал повторный расход."}
    if len(rows)>_text_cap and _confirmed!=len(rows):
        return {"status":"paid_confirmation_required","changed":[],"errors":[],"code":"paid_text_batch_confirmation_required",
                "requested":len(rows),"normal_paid_cap":_text_cap,
                "message":f"Защита бюджета остановила массовую генерацию {len(rows)} текстов. Подтвердите точный объём отдельно или запускайте подготовку BORIS — она работает безопасными порциями."}
    ctx=ff.get("account_context") or {}; brief=ff.get("brief") or ""; defaults=ff.get("defaults") or {}; refs=dict(ff.get("references") or {}); changed=[]; errors=[]
    for row in rows:
        p=_ff_payload(row); topic=str(p.get("direction") or row.segment or p.get("title") or "").strip()
        if not topic: errors.append({"id":row.id,"message":"Нет направления"}); continue
        angle=str(p.get("creative_angle") or "").strip()
        angle_instruction=("Главный рекламный посыл этого варианта: "+angle) if angle else ""
        ref_text=str(refs.get("text") or "").strip()
        ref_links="\n".join(str(x) for x in (refs.get("links") or [])[:10])
        ref_images=str(refs.get("image_analysis") or "").strip()
        ref_competitor=str(refs.get("competitor_audit_text") or "").strip()
        ref_portfolio=str(refs.get("portfolio_evidence_text") or "").strip()
        uniqueness_parameters=str(refs.get("uniqueness_parameters") or "").strip()
        uniqueness_instruction=(("ПАРАМЕТРЫ ПРЕДМЕТНОЙ УНИКАЛИЗАЦИИ ОТ КЛИЕНТА: "+uniqueness_parameters+". Для каждого объявления выбирай отличающуюся комбинацию ТОЛЬКО в указанных клиентом диапазонах/вариантах. Внизу добавь отдельный блок «Пример услуги» или «Пример товара» с конкретной комбинацией. ГЛОБАЛЬНОЕ ПРАВИЛО ЦВЕТА: если результат услуги, товар, материал, покрытие, отделка, мебель, автомобиль, одежда или иной объект визуально имеет цвет, блок предметной уникализации ОБЯЗАТЕЛЬНО содержит релевантное поле цвета (например «Цвет отделки», «Цвет фасадов», «Цвет кузова»). Для стандартизируемых окрашиваемых поверхностей используй только реальные коды международной системы RAL Classic и человекочитаемое название цвета; не приписывай конкретному товару фактический цвет, которого нет в подтверждённых данных. Если клиент разрешил выбор цвета по каталогу RAL, разные объявления могут получать разные реальные RAL-варианты. Если цвет объективно неприменим к услуге, не добавляй его. Не придумывай остальные параметры вне разрешённого диапазона. ") if uniqueness_parameters else "") + ("ГЛОБАЛЬНОЕ ПРАВИЛО ЦВЕТА BORIS: если товар, объект, материал, результат услуги или иная часть предложения может быть визуально увидена глазами и у неё есть цвет, ЦВЕТ обязан участвовать в предметной уникализации и в блоке примера. Используй только подтверждённый цвет или разрешённый клиентом диапазон цветов. Если цвет визуально применим, но подтверждённых вариантов цвета нет, НЕ выдумывай цвет — пометь это как недостающий параметр для дальнейшей генерации/уникализации. Для нематериальных услуг, где физического визуального результата/объекта нет, цвет не выдумывай.")
        if refs.get("mode")=="preserve_text" and ref_text:
            ref_instruction=("РЕЖИМ РЕДАКТОРА: исходный текст ниже считать единственным источником фактов. Сохраняй факты и смысл, "
                             "не придумывай новых условий, цифр, гарантий или характеристик. При этом текущий вариант объявления должен быть реально уникальным по подаче: "
                             "другое первое предложение, другой порядок смысловых блоков, другая формулировка выгод и другой CTA. Не делай косметическую замену нескольких слов. "
                             "Если в кампании несколько карточек, не повторяй первый абзац и структуру предыдущих вариантов. Улучши читаемость: короткие блоки, понятные подзаголовки, уместные иконки. "
                             "Avito использует обычный текст, поэтому не добавляй HTML/Markdown-разметку.\nНОМЕР ВАРИАНТА: "+str(p.get("variant") or 1)+"\nИСХОДНЫЙ ТЕКСТ:\n"+ref_text)
        elif ref_text or ref_links or ref_images or ref_competitor or ref_portfolio:
            ref_instruction=("ДОКАЗАННАЯ ФАКТУРА И РЫНОЧНЫЕ РЕФЕРЕНСЫ: используй факты собственного аккаунта как факты клиента; конкурентный анализ — только как ориентир по структуре, болям и рекламным акцентам. "
                             "Не копируй чужой текст дословно и не переноси неподтверждённые конкурентные условия.\n"+
                             (("Тексты/описания клиента или референсы:\n"+ref_text) if ref_text else "")+
                             (("\nСсылки-референсы:\n"+ref_links) if ref_links else "")+
                             (("\nВыжимка из скриншотов:\n"+ref_images) if ref_images else "")+
                             (("\nФакты из собственного Avito-портфеля клиента:\n"+ref_portfolio) if ref_portfolio else "")+
                             (("\nАнализ ТОП-конкурентов BORIS (принципы, не чужие факты):\n"+ref_competitor) if ref_competitor else ""))
        else:
            ref_instruction=""
        vehicle_facts=_ff_verified_vehicle_facts(p)
        vehicle_instruction=("ПОДТВЕРЖДЁННЫЕ ХАРАКТЕРИСТИКИ АВТО: "+json.dumps(vehicle_facts,ensure_ascii=False)+". Используй их для предметной уникализации карточки: год, пробег, цвет, поколение, кузов, коробка, двигатель, комплектация и другие поля — ТОЛЬКО если они присутствуют здесь. Не придумывай отсутствующие характеристики. Для массовой кампании меняй рекламный угол, структуру, SEO-формулировки и порядок подтверждённых фактов, а не факты автомобиля." if vehicle_facts else "Характеристики автомобиля сверх уже подтверждённых данных не найдены. Не придумывай год, пробег, цвет, комплектацию или другие параметры ради уникализации.")
        p["vehicle_fact_basis"]=vehicle_facts
        safety_instruction=(
            "FEED FACTORY STRICT FACTS: создавай один конкретный готовый вариант объявления, не показывай клиенту спинтакс. "
            "Не придумывай сроки, гарантии, наличие, доставку, опыт, кейсы, результат, скорость ответа, характеристики товара или примеры выполненных работ, "
            "если этого нет в задаче/профиле клиента. Не используй абсолютные обещания вроде «идеально», «как новые», «любой сложности». "
            "Описание делай содержательным и продающим: ориентир 1200–2000 символов, не сокращай полезную фактуру. Стиль BORIS — как в утверждённых сильных примерах: мощный первый заход; короткие абзацы; жирные по смыслу заголовки там, где канал поддерживает форматирование; уместные иконки в начале блоков; боль/желание → решение → процесс/выгоды → снятие возражений → предметный пример → география → сильный естественный CTA. Используй разные маркетинговые модели между объявлениями (AIDA, PAS, экспертный, срочность, результат, сопровождение, конкретный сценарий клиента), а не косметическую перестановку слов. Каждое предложение заканчивай корректным знаком препинания. "
            "Если клиент написал цену «от», сохраняй формулировку «от», не превращай её в фиксированную цену. Заголовок до 50 символов."
        )
        current_instruction=""
        if str(body.instructions or "").strip() and (p.get("title") or p.get("description")):
            _city=str(p.get("city") or p.get("address") or defaults.get("city") or "").strip()
            current_instruction=("РЕЖИМ СИЛЬНОГО КОПИРАЙТЕРА BORIS: перепиши готовое объявление существенно сильнее и подробнее, сохраняя только подтверждённые факты. "
                                 "Описание должно быть 900–1500 символов обычного текста и состоять из нескольких продающих блоков: сильное первое предложение, проблема/желание клиента, что именно делает специалист, ценность для клиента, социальное доказательство ТОЛЬКО из подтверждённой фактуры, снятие возражений и 2 естественных CTA. "
                                 "Используй мягкие NLP-приёмы: конкретизация результата, будущая картина, выбор без давления, вопросы вовлечения. Не манипулируй и не придумывай кейсы/цифры. "
                                 "КРИТИЧНО: ПЕРВОЕ ПРЕДЛОЖЕНИЕ каждого варианта должно отличаться и содержать конкретное название услуги + город"+((" "+_city) if _city else "")+" + естественный призыв написать/обратиться. Не начинай варианты одинаковыми фразами. "
                                 "В конце не добавляй выдуманные факты объекта; техническая уникализация добавляется BORIS отдельно после генерации. "
                                 "Не добавляй новых цен, гарантий, характеристик или условий.\nТЕКУЩИЙ ЗАГОЛОВОК: "+str(p.get("title") or "")+
                                 "\nТЕКУЩЕЕ ОПИСАНИЕ:\n"+str(p.get("description") or ""))
        price_fact_instruction=(
            "ЦЕНА КАРТОЧКИ НЕ ПОДТВЕРЖДЕНА. Не придумывай и не выводи отдельную цену объявления. "
            "Можно упоминать только те тарифы/диапазоны цен, которые дословно есть в подтвержденных данных компании ниже."
            if not int(p.get("price") or 0) else
            ("ПОДТВЕРЖДЕННАЯ ЦЕНА КАРТОЧКИ: %s ₽. Не заменяй ею другие подтвержденные тарифы или суммы из профиля клиента." % int(p.get("price") or 0))
        )
        extra="\n".join(x for x in [brief,safety_instruction,price_fact_instruction,vehicle_instruction,uniqueness_instruction,current_instruction,angle_instruction,ref_instruction,ctx.get("description",""),ctx.get("advantages",""),str(defaults.get("usp") or ""),body.instructions] if x)
        try:
            _strict_requested=bool(__import__('re').search(r"(?:не\s+(?:выдум|придум|добав)|без\s+выдум)",brief,__import__('re').I))
            _refs=dict(ff.get("references") or {})
            _ctx=dict(ff.get("account_context") or {})
            _evidence_text=" ".join(str(x or "") for x in (_ctx.get("description"),_ctx.get("advantages"),_ctx.get("client_description"),_refs.get("text"),_refs.get("image_analysis"),_refs.get("portfolio_evidence_text")))
            # CONTENT_LOCAL_FIRST_V1: automatic Feed Factory preparation must not
            # consume model credits merely to obtain a usable first draft. BORIS
            # already owns a deterministic, factual copy builder; use it as the
            # canonical default. Paid generation is an explicitly enabled reserve,
            # never an implicit fallback from a worker retry.
            from app.services.avito_content_policy import (
                local_text_first_enabled as _content_local_text_first,
                paid_fallback_enabled as _content_paid_fallback,
            )
            strict_bulk=_strict_requested and int(ff.get("quantity") or 0)>=10 and not str(body.instructions or "").strip() and len(_evidence_text.strip())<80
            _use_local_text=bool(_content_local_text_first() or strict_bulk or not _content_paid_fallback())
            result={}
            if _use_local_text:
                bt,bd=_ff_bulk_safe_copy(topic,p,ff)
                ad={"title":bt,"description":bd,"price":int(p.get("price") or 0)}
                p["copy_provider"]="boris_deterministic_local_v1"
                p["copy_fallback_reason"]=("insufficient_verified_evidence" if strict_bulk else "local_first_policy")
            else:
                _batch_idem=str(body.idempotency_key or "").strip()
                _item_idem=((_batch_idem+":item:"+str(int(row.id))) if _batch_idem else "")
                result=generate_ads(GenerateAdsRequest(topic=topic,count=1,price_from=int(p.get("price") or 0),price_to=int(p.get("price") or 0),extra=extra,goal=body.goal,length=body.length,use_my_ads=True,account_id=row.account_id,idempotency_key=_item_idem,single_paid_attempt=True,paid_provenance={"campaign_id":int(campaign_id),"item_id":int(row.id),"source":"feed_factory_text"}))
                _generated_ads=(result.get("ads") or []) if isinstance(result,dict) else []
                ad=_generated_ads[0] if _generated_ads else None
                p["copy_provider"]="paid_single_attempt"
            if not ad: errors.append({"id":row.id,"message":str(result.get("message") if isinstance(result,dict) else "Генератор не вернул текст")}); continue
            generated_title=_ff_clean_title_for_avito(ad.get("title") or p.get("title","") , topic)
            # Guarantee visible title diversity for bulk variants even if the LLM
            # repeats itself. Deterministic, factual variants use only the service name.
            title_pool=_ff_title_variants(topic,brief)
            variant_no=max(1,int(p.get("variant") or 1))
            # Preserve the strong model-generated headline. The deterministic pool is
            # only an emergency fallback for strict_bulk/no-evidence mode; previously
            # it overwrote every variant 2+ with generic «под ключ/консультация» copy.
            if strict_bulk and title_pool:
                p["title"]=title_pool[(variant_no-1)%len(title_pool)]
            else:
                p["title"]=generated_title
            p["description"]=_ff_plain_description_for_avito(ad.get("description") or p.get("description","") , brief=brief, price=int(p.get("price") or 0), price_mode=str(p.get("price_mode") or ""))
            p["description"]=_ff_enforce_sales_copy_shape(
                p["description"], topic, str(p.get("city") or p.get("address") or ""), max_chars=2400
            )
            p["description"]=_ff_reconcile_copy_price(p["description"],int(p.get("price") or 0),str(p.get("price_mode") or ""))
            p["description"],_negative_removed=_ff_autosanitize_explicit_negative_claims(p["description"],brief)
            if _negative_removed:
                p["negative_claim_autosanitize"]={"removed_count":len(_negative_removed),"removed_lines":_negative_removed[-10:],"policy":"explicit_brief_prohibition_only_v1"}
            # Every text produced by the current Feed Factory copywriter must pass
            # the same acceptance contract before READY/approval. This converts
            # copy quality from a prompt preference into a production invariant.
            p["copy_quality_contract"]="sales_v3"
            if __import__('re').search(r"(?:не\s+(?:выдум|придум|добав)|без\s+выдум)",brief,__import__('re').I):
                # The generator already receives a strict-facts instruction. Sanitize
                # its useful copy instead of replacing every variant with one generic
                # template; this preserves the client's requested semantic diversity.
                p["description"]=_ff_plain_description_for_avito(p["description"],brief=brief,price=int(p.get("price") or 0),price_mode=str(p.get("price_mode") or ""))
                p["content_policy"]="strict_known_facts_only"
            # Production invariant requested by the Campaign UI: every generated
            # description ends with its own concrete, factual example/scenario.
            # This is deterministic and free, so it cannot trigger another paid call.
            p["description"]=_ff_append_uniqueness_footer(
                p["description"], topic, str(p.get("city") or p.get("address") or ""),
                variant=variant_no, item_id=int(row.id), max_chars=2400
            )
            p["uniqueness_footer"]={"present":True,"policy":"direction_geo_rotating_v1","position":"bottom"}
            # Single-paid-attempt policy: quality validation may reject the first
            # draft, but BORIS must never silently buy a second model response.
            # The failed draft stays draft/fail-closed; an owner/job may submit a
            # NEW explicit text intent (new idempotency key) if another paid rewrite
            # is desired. Formatting repair inside generate_ads remains local/free.
            _quality_first=_ff_copy_quality_errors(p)
            if _quality_first and not strict_bulk:
                p["copy_quality_repair"]={
                    "attempted":False,
                    "policy":"single_paid_attempt_fail_closed",
                    "reason_codes":sorted({str(x.get("code") or "") for x in _quality_first if x.get("code")}),
                    "remaining_codes":sorted({str(x.get("code") or "") for x in _quality_first if x.get("code")}),
                    "next_action":"explicit_new_text_intent_required",
                }
            else:
                # A previous failed text attempt must not keep a stale red warning
                # after a later explicit regeneration passes the acceptance gate.
                p.pop("copy_quality_repair", None)
            # Never promote a model-returned price into canonical campaign data.
            # If the owner supplied a price, materialize already put it in p.price;
            # if p.price is empty, the generator's numeric field is not evidence.
            if str(body.instructions or "").strip() and str(topic).lower().find("квартир") >= 0:
                # Real-estate service enhancement: deterministic first line + hallucination scrub.
                # The LLM writes the body; BORIS owns the lead and unique search-scenario footer.
                import re as _re_lead
                _city0=str(p.get("city") or p.get("address") or defaults.get("city") or "Москва").strip()
                _vn=max(1,int(p.get("variant") or 1))
                _services=["Подбор квартиры","Брокер по подбору квартиры","Сопровождение покупки квартиры","Поиск квартиры под ваши параметры","Помощь с подбором квартиры","Подбор жилья для покупки","Консультация по подбору квартиры","Подбор квартиры с сопровождением"]
                _ctas=["напишите — начнём с ваших параметров","расскажите, что ищете — подберём подходящие варианты","напишите в Avito — разберём вашу задачу","обратитесь — начнём поиск под ваши требования","напишите, что для вас важно — соберём критерии поиска"]
                _lead=f"{_services[(_vn-1)%len(_services)]} · {_city0} — {_ctas[((_vn-1)//len(_services)+_vn)%len(_ctas)]}."
                _body=str(p.get("description") or "").strip()
                # Remove model-invented pseudo-object examples/technical uniqueness. BORIS appends its own disclosed scenario below.
                _body=_re_lead.sub(r"(?ims)^\s*(?:Пример квартиры|Пример объекта|Пример варианта|Уникализация)\s*:.*?(?=\n\s*\n|\Z)","",_body).strip()
                # Replace the opening paragraph with the controlled lead while preserving the selling body.
                _parts=[x.strip() for x in _re_lead.split(r"\n\s*\n",_body) if x.strip()]
                if _parts:
                    _parts=_parts[1:] if len(_parts[0])<260 else _parts
                _selling="\n\n".join(_parts) if _parts else ""
                # Keep the selling body substantial but bounded; cut only at paragraph boundaries.
                if len(_selling)>1120:
                    _kept=[]; _n=0
                    for _par in _parts:
                        _add=len(_par)+(2 if _kept else 0)
                        if _kept and _n+_add>1120: break
                        _kept.append(_par); _n+=_add
                    _selling="\n\n".join(_kept)
                p["description"]=_lead+("\n\n"+_selling if _selling else "")

            if str(body.instructions or "").strip() and str(topic).lower().find("квартир") >= 0:
                # REAL_ESTATE_NO_INVENTED_UNIQUENESS_V1.
                # Never invent rooms/area/floor merely to make cards look different.
                # Only confirmed card facts may participate in deterministic uniqueness.
                _city=str(p.get("city") or p.get("address") or defaults.get("city") or "").strip()
                p["description"]=_ff_append_uniqueness_footer(
                    str(p.get("description") or ""),
                    str(topic or ""),
                    _city,
                    int(p.get("variant") or 1),
                    int(row.id),
                )
                p["uniqueness_scenario"]={
                    "city":_city,
                    "source":"canonical_card_geo_only",
                    "disclosure":"no_invented_numeric_parameters",
                }

            if str(body.instructions or "").strip():
                # This endpoint changes copy only, never the sold object/category. Keep already
                # approved media attached and refresh the proof fingerprint to the new copy.
                try:
                    from app.services.media_semantic_validator import semantic_content_fingerprint
                    _new_fp=semantic_content_fingerprint(p)
                    for _ev in (p.get("media_semantic_evidence") or []):
                        if isinstance(_ev,dict) and bool(_ev.get("match")):
                            _ev["content_fingerprint"]=_new_fp
                    p["last_text_enhancement"]={"prompt":str(body.instructions)[:2000],"updated_at":__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()}
                except Exception as _suppressed_exc:
                    observe_suppressed(__name__, _suppressed_exc, line=5369)
            row.payload_json=json.dumps(p,ensure_ascii=False); row.content_hash=CS.content_hash(p); row.status="draft"; changed.append(row.id)
        except Exception as exc: errors.append({"id":row.id,"message":str(exc)[:240]})
    db.commit(); ff["step"]="media" if changed else "texts"; _ff_state_save(db,campaign,ff)
    return {"status":"ok" if changed else "error","changed":changed,"errors":errors}


@router.post("/{campaign_id}/workflow/auto-media")
def ff_workflow_auto_media(campaign_id:int, body:FFAutoMediaBody, db=Depends(_db), user=Depends(get_current_user)):
    from app.api.media import StockCollectBody, collect_stock
    from app.models.media_asset import MediaAsset
    from app.models.media_link import MediaLink
    from app.services import media_service as M
    from app.services.media_semantic_validator import classify_item_media_vision, stamp_item_semantic_evidence
    allowed=_allowed_accounts(db,user); campaign=_campaign_or_404(db,campaign_id,allowed); _,ff=_ff_state_load(campaign)
    _ff_assert_mutable(campaign)
    if _ff_paid_qa_forbidden(campaign): return _ff_paid_qa_response()
    # IMPORTANT: empty item_ids must never silently become "all campaign items".
    # Every media mutation is explicitly scoped by the caller; stock selection itself
    # is not a paid image-generation operation.
    requested_ids=[int(x) for x in (body.item_ids or []) if int(x)>0]
    if not requested_ids:
        return {
            "status":"selection_required", "changed":[], "errors":[],
            "code":"media_selection_required",
            "message":"Выберите объявления, для которых нужно подобрать фотографии."
        }
    wanted=set(requested_ids); changed=[]; generated=[]; errors=[]
    _rows=[r for r in _ff_items(db,campaign_id,allowed) if r.id in wanted]
    # Stock/owner-media assignment is NOT an OpenAI image purchase.
    # Keep explicit item scoping, but never route this path through paid-image
    # confirmation/billing preflight copied from the banner endpoint.
    for row in _rows:
        if wanted and row.id not in wanted: continue
        p=_ff_payload(row); topic=str(p.get("direction") or row.segment or p.get("title") or "").strip()
        _row_id=int(row.id); _row_account_id=str(row.account_id)
        desired_count=max(1,min(int(body.count_per_item or 1),3))
        from app.services.media_semantic_validator import semantic_content_fingerprint as _semantic_media_fp
        _semantic_fp=_semantic_media_fp(p)
        _rejected_media_ids=set()
        for _rej in (p.get("media_semantic_rejections") or []):
            if not isinstance(_rej,dict) or str(_rej.get("content_fingerprint") or "")!=_semantic_fp: continue
            try: _rejected_media_ids.add(int(_rej.get("media_id") or 0))
            except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=5413)
        if not topic: errors.append({"id":_row_id,"message":"Нет направления для подбора фотографии"}); continue
        try:
            # Reuse an already vision-approved asset for the exact topic before
            # calling the external stock provider again. Bulk variants must not
            # download/revalidate the same photo twenty times.
            from app.models.media_folder import MediaFolder
            # OWNER MEDIA FIRST: uploads made inside this exact campaign are authoritative
            # and must beat stock. Scope is account+campaign folder, so another client's
            # media can never leak here. Rotate the hero across variants for diversity.
            owner_folder=db.query(MediaFolder).filter(MediaFolder.account_id==row.account_id,MediaFolder.name==f"campaign_{campaign_id}_manual_photos",MediaFolder.deleted_at.is_(None)).order_by(MediaFolder.id.desc()).first()
            owner_assets=(db.query(MediaAsset).filter(MediaAsset.account_id==row.account_id,MediaAsset.folder_id==owner_folder.id,MediaAsset.deleted_at.is_(None),MediaAsset.source_type=="upload").order_by(MediaAsset.id.asc()).all()) if owner_folder else []
            if owner_assets:
                _idx=max(0,(int(p.get("variant") or row.id)-1)%len(owner_assets))
                rotated=owner_assets[_idx:]+owner_assets[:_idx]
                items=[{"id":a.id} for a in rotated[:desired_count]]
            else:
                folder=db.query(MediaFolder).filter(MediaFolder.account_id==row.account_id,MediaFolder.name==topic,MediaFolder.deleted_at.is_(None)).order_by(MediaFolder.id.desc()).first()
                priors=(db.query(MediaAsset).filter(MediaAsset.account_id==row.account_id,MediaAsset.folder_id==folder.id,MediaAsset.deleted_at.is_(None),MediaAsset.status=="approved",~MediaAsset.id.in_(_rejected_media_ids) if _rejected_media_ids else True).order_by(MediaAsset.id.desc()).limit(desired_count).all()) if folder else []
                if len(priors) >= desired_count:
                    items=[{"id":a.id} for a in priors[:desired_count]]
                else:
                    # Stock discovery performs external HTTP. Persist/release all
                    # campaign reads before it so a slow stock provider cannot leave
                    # PostgreSQL idle in transaction. collect_stock owns its own
                    # DB mutations through this same session and commits them.
                    db.commit()
                    stock=collect_stock(StockCollectBody(
                        account_id=_row_account_id, topic=topic, count=desired_count,
                        provenance={"source":"campaign_stock_collect","campaign_id":int(campaign_id),"item_id":_row_id},
                    ),db=db,user=user)
                    items=list(stock.get("items") or []) if isinstance(stock,dict) else []
            if len(items) < desired_count:
                folder=db.query(MediaFolder).filter(MediaFolder.account_id==row.account_id,MediaFolder.name==topic,MediaFolder.deleted_at.is_(None)).order_by(MediaFolder.id.desc()).first()
                if folder:
                    more=(db.query(MediaAsset).filter(MediaAsset.account_id==row.account_id,MediaAsset.folder_id==folder.id,MediaAsset.deleted_at.is_(None),MediaAsset.status=="approved",~MediaAsset.id.in_(_rejected_media_ids) if _rejected_media_ids else True).order_by(MediaAsset.id.desc()).limit(desired_count*4).all())
                    seen={int(x.get("id") or 0) for x in items}
                    for a in more:
                        if int(a.id) not in seen:
                            items.append({"id":int(a.id)}); seen.add(int(a.id))
                        if len(items)>=desired_count: break
            if len(items) < desired_count:
                # Reuse other approved stock from this SAME account when its stored
                # topic/tags share the commercial subject stem. The canonical
                # photo-stack semantic QA below still validates every candidate
                # against the exact card before any link mutation, so this broadens
                # reuse without weakening the fail-closed category/image contract.
                import re as _media_re
                stems=[w[:4] for w in _media_re.findall(r"[а-яёa-z0-9]+",topic.lower()) if len(w)>=4 and w not in {"заказ","услуг","товар","город"}]
                pool=(db.query(MediaAsset).filter(MediaAsset.account_id==row.account_id,MediaAsset.deleted_at.is_(None),MediaAsset.status=="approved",MediaAsset.source_provider=="pexels",~MediaAsset.id.in_(_rejected_media_ids) if _rejected_media_ids else True).order_by(MediaAsset.id.desc()).limit(160).all())
                seen={int(x.get("id") or 0) for x in items}
                for a in pool:
                    hay=(str(a.storage_key or "")+" "+" ".join(str(x) for x in (a.tags or []))).lower()
                    if stems and any(stem in hay for stem in stems) and int(a.id) not in seen:
                        items.append({"id":int(a.id)}); seen.add(int(a.id))
                    if len(items)>=desired_count: break
            if len(items) < desired_count:
                raise RuntimeError(f"Найдено только {len(items)} из {desired_count} фотографий, прошедших строгую визуальную проверку")
            mids=[]
            for candidate in items:
                mid=int(candidate.get("id") or 0)
                if not mid or mid in mids:
                    continue
                asset=db.query(MediaAsset).filter(MediaAsset.id==mid,MediaAsset.deleted_at.is_(None)).first()
                if asset and mid not in _rejected_media_ids and (not asset.account_id or str(asset.account_id)==_row_account_id):
                    mids.append(mid)
                if len(mids)>=desired_count:
                    break
            if len(mids) < desired_count:
                raise RuntimeError(f"Проверенные фотографии не зарегистрированы полностью: {len(mids)} из {desired_count}")

            # Reuse the canonical photo-stack mutation instead of maintaining a
            # second, subtly different gallery writer here. This guarantees that
            # `count_per_item=3` means exactly three selected photos, preserves an
            # existing campaign banner as the first image, removes stale gallery
            # leftovers, and runs the same fail-closed semantic QA as manual UI.
            stack_result=ff_workflow_photo_stack(
                campaign_id,
                FFPhotoStackBody(
                    item_ids=[_row_id], media_ids=mids,
                    photos_per_item=desired_count, randomize=False, after_banner=True,
                ),
                db,
                user,
            )
            if str(stack_result.get("status") or "")!="ok" or _row_id not in set(stack_result.get("changed") or []):
                raise RuntimeError(str(stack_result.get("message") or "Не удалось применить проверенный фотостек"))
            changed.append(_row_id)
            generated.append(_row_id)
        except Exception as exc:
            db.rollback(); errors.append({"id":row.id,"message":str(exc)[:240]})
    _,ff=_ff_state_load(campaign); ff["step"]="banners" if changed else "media"; _ff_state_save(db,campaign,ff)
    return {"status":"ok" if changed and not errors else ("partial" if changed else "error"),"changed":changed,"errors":errors,"policy":"strict_stock_vision_then_item_vision_fail_closed"}


@router.post("/{campaign_id}/workflow/photo-stack")
def ff_workflow_photo_stack(campaign_id:int, body:FFPhotoStackBody, db=Depends(_db), user=Depends(get_current_user)):
    """Attach an owner-uploaded photo stack to selected/all campaign ads.

    The banner remains first. Uploaded photos are distributed in a deterministic
    per-card random order so every ad gets a different gallery sequence. This is a
    pure CampaignItem/media mutation: it never publishes to Avito.
    """
    import random, datetime
    from app.models.media_asset import MediaAsset
    from app.models.media_link import MediaLink
    from app.services import media_service as M
    from app.services.media_semantic_validator import (
        classify_item_media_vision, stamp_item_semantic_evidence,
        validate_linked_media_semantics,
    )
    allowed=_allowed_accounts(db,user); campaign=_campaign_or_404(db,campaign_id,allowed)
    _ff_assert_mutable(campaign)
    rows=_ff_items(db,campaign_id,allowed); wanted=set(body.item_ids or [r.id for r in rows]); rows=[r for r in rows if r.id in wanted]
    if not rows: raise HTTPException(400,"Не выбраны объявления")
    mids=[]
    for raw in body.media_ids or []:
        try: mid=int(raw)
        except Exception: continue
        a=db.query(MediaAsset).filter(MediaAsset.id==mid,MediaAsset.deleted_at.is_(None)).first()
        if not a or (a.account_id and a.account_id not in allowed): raise HTTPException(403,"Нет доступа к фотографии")
        if a.account_id and any(r.account_id!=a.account_id for r in rows): raise HTTPException(409,"Фотографии и объявления должны быть из одного аккаунта")
        mids.append(mid)
    mids=list(dict.fromkeys(mids))
    if not mids: raise HTTPException(400,"Загрузите хотя бы одну фотографию")
    per=max(1,min(int(body.photos_per_item or 6),9))
    changed=[]
    for row in rows:
        p=_ff_payload(row)
        links=(db.query(MediaLink,MediaAsset).join(MediaAsset,MediaAsset.id==MediaLink.media_id)
               .filter(MediaLink.entity_type=="campaign_item",MediaLink.entity_id==str(row.id),MediaAsset.deleted_at.is_(None))
               .order_by(MediaLink.position.asc(),MediaLink.id.asc()).all())
        by_id={int(a.id):(l,a) for l,a in links}
        banner_pair=next(((l,a) for l,a in links if str(getattr(a,"source_provider","") or "").lower()=="openai" or "/banners/campaign_openai/" in str(getattr(a,"storage_key","") or "")),None)
        chosen=list(mids)
        if body.randomize:
            random.Random(f"{campaign_id}:{row.id}:{','.join(map(str,mids))}").shuffle(chosen)
        chosen=chosen[:per]
        ordered=[]
        if body.after_banner and banner_pair: ordered.append(banner_pair[1])
        for mid in chosen:
            a=db.query(MediaAsset).get(mid)
            if a and all(int(x.id)!=int(a.id) for x in ordered): ordered.append(a)
        # The selected photo stack is authoritative. Do not silently append old
        # gallery leftovers: when the owner asks for 3 photos, the card must show
        # exactly those 3 photos plus the current banner (if present).
        if banner_pair and not body.after_banner and all(int(x.id)!=int(banner_pair[1].id) for x in ordered):
            ordered.insert(0,banner_pair[1])
        ordered=ordered[:10]
        # Validate the intended gallery using detached scalar snapshots BEFORE
        # deleting/rebuilding MediaLink. External vision must never sit inside the
        # mutation transaction, and a failed verdict must leave the old gallery intact.
        from types import SimpleNamespace as _SNS
        def _asnap(a): return _SNS(id=int(a.id),storage_key=str(a.storage_key),tags=list(a.tags or []),account_id=str(a.account_id or ''),source_provider=str(a.source_provider or ''),source_ref=str(a.source_ref or ''))
        _planned=[(_SNS(position=pos),_asnap(a)) for pos,a in enumerate(ordered)]
        _row_id=int(row.id); _row_account=str(row.account_id); _ordered_ids=[int(a.id) for a in ordered]
        _planned_urls=[]
        for a in ordered:
            pub=M.asset_public(a); url=pub.get("original_url") or pub.get("preview_url")
            if url: _planned_urls.append(url)
        p["images"]=_planned_urls
        now=datetime.datetime.now(datetime.timezone.utc).isoformat()
        p["photo_stack"]={"media_ids":chosen,"randomize":bool(body.randomize),"after_banner":bool(body.after_banner),"updated_at":now}
        db.commit()
        try:
            verdicts=classify_item_media_vision(p,_planned,account_id=_row_account,provenance={"campaign_id":int(campaign_id),"item_id":int(_row_id),"source":"campaign_photo_stack"})
        except Exception as exc:
            db.rollback()
            raise HTTPException(422, detail={"code":"gallery_semantic_check_failed","message":"Не удалось завершить смысловую проверку выбранных фотографий. Старую галерею BORIS не изменил.","item_id":_row_id}) from exc
        semantic_bad=[v for v in verdicts if (not bool(v.get("match"))) or float(v.get("confidence") or 0)<0.80]
        if semantic_bad:
            db.rollback()
            raise HTTPException(422, detail={"code":"gallery_semantic_mismatch","message":"Одна или несколько фотографий не соответствуют товару или услуге. BORIS ничего не применил.","item_id":_row_id,"rejected":len(semantic_bad)})
        from app.models.campaign_item import CampaignItem
        row=db.query(CampaignItem).filter(CampaignItem.id==_row_id).first()
        _assets=db.query(MediaAsset).filter(MediaAsset.id.in_(_ordered_ids),MediaAsset.deleted_at.is_(None)).all()
        _amap={int(a.id):a for a in _assets}; ordered=[_amap[i] for i in _ordered_ids if i in _amap]
        if len(ordered)!=len(_ordered_ids): raise RuntimeError("Фотографии изменились во время проверки")
        db.query(MediaLink).filter(MediaLink.entity_type=="campaign_item",MediaLink.entity_id==str(row.id)).delete(synchronize_session=False); db.flush()
        for pos,a in enumerate(ordered):
            M.link(db,a,"campaign_item",str(row.id),"hero" if pos==0 else "gallery",pos,created_by=getattr(user,"id",None))
        db.flush(); row.hero_media_id=int(ordered[0].id) if ordered else None
        fresh_links=(db.query(MediaLink,MediaAsset).join(MediaAsset,MediaAsset.id==MediaLink.media_id).filter(MediaLink.entity_type=="campaign_item",MediaLink.entity_id==str(row.id),MediaAsset.deleted_at.is_(None)).order_by(MediaLink.position.asc(),MediaLink.id.asc()).all())
        stamp_item_semantic_evidence(p,fresh_links,verdicts)
        semantic_errors=validate_linked_media_semantics(db,row,p)
        if semantic_errors:
            db.rollback()
            raise HTTPException(422, detail={"code":"gallery_semantic_validation_failed","message":"Выбранная галерея не прошла финальную проверку соответствия объявлению. BORIS не сохранил изменение.","item_id":_row_id})

        row.payload_json=json.dumps(p,ensure_ascii=False); row.content_hash=CS.content_hash(p); row.status="draft"; changed.append(row.id)
    db.commit(); _,ff=_ff_state_load(campaign); ff["step"]="validation"; _ff_state_save(db,campaign,ff)
    return {"status":"ok","changed":changed,"photos_per_item":per,"randomize":bool(body.randomize),"after_banner":bool(body.after_banner)}


@router.post("/{campaign_id}/workflow/banners")
def ff_workflow_banners(campaign_id:int, body:FFBannerBody, db=Depends(_db), user=Depends(get_current_user)):
    """Generate Avito campaign banners with AI visual + deterministic local text.

    Existing ad copy is preserved. OpenAI is forbidden from rendering visible
    text; the final headline is composed locally for mobile readability.
    """
    import hashlib, os, re, time

    allowed=_allowed_accounts(db,user); campaign=_campaign_or_404(db,campaign_id,allowed); _,ff=_ff_state_load(campaign)
    _ff_assert_mutable(campaign)
    if _ff_paid_qa_forbidden(campaign): return _ff_paid_qa_response()
    if bool(getattr(body, "force", False)) and not str(getattr(body, "idempotency_key", "") or "").strip():
        raise HTTPException(409, "Для повторной платной генерации нужен уникальный ключ операции")
    # IMPORTANT: empty item_ids must never silently become "all campaign items"
    # for a paid operation. The old fallback to ff.item_ids caused a 35-image
    # batch when the frontend sent an empty selection.
    requested_ids=[int(x) for x in (body.item_ids or []) if int(x)>0]
    if not requested_ids:
        return {
            "status":"selection_required", "changed":[], "errors":[],
            "code":"paid_selection_required",
            "message":"Выберите объявления, для которых нужно создать баннеры."
        }
    wanted=set(requested_ids); changed=[]; generated=[]; errors=[]
    _rows=[r for r in _ff_items(db,campaign_id,allowed) if r.id in wanted]
    _found_ids={int(r.id) for r in _rows}
    _missing_ids=sorted(wanted-_found_ids)
    if _missing_ids:
        return {"status":"selection_invalid","changed":[],"errors":[],"code":"campaign_item_selection_invalid",
                "missing_item_ids":_missing_ids,"message":"Часть выбранных объявлений уже недоступна или относится к другому аккаунту. Обновите экран."}
    _row_accounts=sorted({str(r.account_id or "").strip() for r in _rows if str(r.account_id or "").strip()})
    if len(_row_accounts) != 1:
        return {"status":"account_scope_mismatch","changed":[],"errors":[],"code":"campaign_account_scope_mismatch",
                "accounts":_row_accounts,"message":"Одна платная операция баннеров может относиться только к одному аккаунту."}
    _account_for_billing=_row_accounts[0]

    _pending_banner_charge=max(0,int(ff.get("billing_pending_banners") or 0))
    _pending_banner_billing_key=str(ff.get("billing_pending_banners_key") or "").strip()
    if _pending_banner_charge and _account_for_billing:
        from app.api.billing import check_and_consume as _billing_consume
        if not _pending_banner_billing_key:
            # Old pending rows predate durable billing idempotency. Do not guess an
            # economic identity and risk a duplicate tariff charge. Keep fail-closed.
            return {"status":"billing_pending","changed":[],"errors":[],"code":"billing_idempotency_missing",
                    "unit":"banners","pending_charge":_pending_banner_charge,
                    "message":"Старое незавершённое списание требует безопасной сверки; повторная генерация не запускается."}
        _settle=_billing_consume(_account_for_billing,"banners",_pending_banner_charge,
                                 idempotency_key=_pending_banner_billing_key)
        if not _settle.get("allowed",False):
            return {"status":"billing_limit","changed":[],"errors":[],"code":"billing_limit","unit":"banners",
                    "requested":_pending_banner_charge,"remaining":0,
                    "message":_settle.get("message") or "Не удалось подтвердить лимит ранее созданных баннеров."}
        ff.setdefault("billing_charged",{})["banners"]=int(ff.get("billing_charged",{}).get("banners") or 0)+_pending_banner_charge
        ff["billing_pending_banners"]=0
        ff["billing_pending_banners_key"]=""
        _ff_state_save(db,campaign,ff)

    # Count only calls that can actually become paid. Existing OpenAI banners
    # are free cache hits unless force=True; retries with the same batch key are
    # also free. Normal batch ceiling is deliberately small (~10 medium images).
    _force=bool(getattr(body, "force", False))
    _idem=str(getattr(body, "idempotency_key", "") or "").strip()
    _paid_rows=[]
    for _r in _rows:
        _p=_ff_payload(_r)
        _has_banner=bool(str(_p.get("banner_url") or "").strip() and str(_p.get("banner_provider") or "").lower() in ("openai","openai_visual_local_text","stock_visual_local_text"))
        _same_retry=bool(_force and _idem and str(_p.get("banner_regeneration_key") or "").strip()==_idem and str(_p.get("banner_url") or "").strip())
        if _same_retry or ((not _force) and _has_banner):
            continue
        _paid_rows.append(_r)
    _paid_count=len(_paid_rows)
    from app.services.avito_content_policy import (
        local_banner_first_enabled as _content_local_banner_first,
        paid_fallback_enabled as _content_paid_fallback,
    )
    # CONTENT_BANNER_LOCAL_FIRST_V1: if paid fallback is disabled, the batch
    # contains zero authorized paid image intents even when cards still need a
    # banner. Each card must use owner/approved media locally or fail closed and
    # return to the media stage; it must never turn a retry into a purchase.
    if _content_local_banner_first() and not _content_paid_fallback():
        _paid_count=0
    _normal_paid_cap=max(1,min(10,int(os.environ.get("BORIS_PAID_IMAGE_BATCH_CAP","10") or 10)))
    # Economic confirmation is intentionally stricter than the technical burst
    # ceiling. One new image may run as the selected item action; every paid
    # multi-image batch must echo the exact paid count before any provider call.
    _confirm_threshold=max(1,min(_normal_paid_cap,int(os.environ.get("BORIS_PAID_IMAGE_CONFIRM_THRESHOLD","1") or 1)))
    _confirmed=max(0,int(getattr(body,"confirmed_paid_items",0) or 0))
    if _paid_count > _confirm_threshold and _confirmed != _paid_count:
        _est_rub=round(_paid_count*5.5,2)
        return {
            "status":"paid_confirmation_required", "changed":[], "errors":[],
            "code":"paid_batch_confirmation_required",
            "requested":len(_rows), "paid_images":_paid_count,
            "normal_paid_cap":_normal_paid_cap, "estimated_cost_rub":_est_rub,
            "message":f"Нужно отдельное подтверждение: будет создано {_paid_count} платных изображений (ориентир до {_est_rub} ₽)."
        }
    _burst_override=bool(_paid_count > _normal_paid_cap and _confirmed == _paid_count)
    if _rows and _account_for_billing:
        try:
            from app.api.billing import get_status as _billing_status
            _bst=_billing_status(_account_for_billing)
            if not _bst.get("unlimited"):
                _remaining=int(((_bst.get("usage") or {}).get("banners") or {}).get("remaining") or 0)
                if _paid_count > _remaining:
                    return {"status":"billing_limit","changed":[],"errors":[],"code":"billing_limit","unit":"banners",
                            "requested":_paid_count,"remaining":_remaining,
                            "message":f"По тарифу доступно ещё {_remaining} баннеров, а новых требуется {_paid_count}."}
        except Exception as _billing_exc:
            print(f"[campaign billing] banner preflight failed closed: {_billing_exc}", flush=True)
            return {"status":"billing_pending","changed":[],"generated":[],"errors":[],
                    "code":"billing_pending","unit":"banners","retryable":True,
                    "message":"BORIS временно не смог подтвердить тарифный лимит. Платная генерация не запускалась; проверка повторится автоматически."}
    for row in _rows:
        if wanted and row.id not in wanted: continue
        p=_ff_payload(row)
        _row_id=int(row.id); _row_account_id=str(row.account_id)
        # Paid-generation idempotency. Normal workflow never replaces an existing
        # OpenAI banner. Explicit regeneration is additionally keyed so a retry of
        # the SAME owner/job intent reuses the persisted result instead of paying twice.
        _force=bool(getattr(body, "force", False)); _idem=str(getattr(body, "idempotency_key", "") or "").strip()
        _prior_idem=str(p.get("banner_regeneration_key") or "").strip()
        if (not _force and str(p.get("banner_url") or "").strip() and str(p.get("banner_provider") or "").lower() in ("openai","openai_visual_local_text","stock_visual_local_text")) or (_force and _idem and _prior_idem==_idem and str(p.get("banner_url") or "").strip()):
            changed.append(row.id)
            continue
        raw_title=str(p.get("title") or "").replace("{","").replace("}","")
        title_candidates=[x.strip() for x in raw_title.split("|") if x.strip()]
        title=(title_candidates[0] if title_candidates else str(p.get("direction") or row.segment or "Предложение")).strip()
        title=" ".join(title.split()[:6])[:60] or "Предложение"
        desc=str(p.get("description") or "").strip()
        if not desc:
            desc=" · ".join(x for x in [str(p.get("direction") or row.segment or "").strip(), str(p.get("category_path") or p.get("category") or "").strip()] if x) or title
        theme=". ".join(x for x in [title, desc[:1200], str(p.get("city") or "").strip()] if x)
        old_banner=str(p.get("banner_url") or "").strip()
        # Same operation key must resolve to the same artifact path. This makes
        # an HTTP retry after an ambiguous disconnect converge on the existing
        # paid bytes even if payload persistence had not completed yet. A new
        # owner intent gets a new key and therefore a new creative path.
        if _idem:
            _artifact_intent=f"explicit:{campaign_id}:{_row_id}:{_idem}"
        else:
            # Normal first generation is also a stable economic intent. A provider
            # success followed by an HTTP/DB disconnect must converge on the same
            # artifact instead of creating a new timestamp path and paying again.
            # Copy/content changes intentionally create a new normal intent; an
            # explicit regeneration still requires the owner supplied key above.
            from app.services.media_semantic_validator import semantic_content_fingerprint as _semantic_banner_fp
            _normal_basis={
                "campaign_id":int(campaign_id), "item_id":_row_id,
                "title":title, "theme":theme,
                "variant":int(p.get("variant") or 1),
                "semantic_fingerprint":_semantic_banner_fp(p),
            }
            _artifact_intent="normal:"+json.dumps(_normal_basis,ensure_ascii=False,sort_keys=True,separators=(",",":"))
        _artifact_op=hashlib.sha256(_artifact_intent.encode("utf-8")).hexdigest()[:20]
        rel=f"{_row_account_id}/banners/campaign_openai/campaign_{campaign_id}_item_{_row_id}_op_{_artifact_op}.png"
        fpath=os.path.join("/root/BORIS/backend/images",rel)
        # AI is allowed to create ONLY the photographic visual. Visible Russian
        # copy is rendered locally afterwards by the deterministic Avito compositor.
        # Keep the intermediate visual outside the account/public banner folder.
        _visual_dir="/root/BORIS/backend/images/_generated_cache/campaign_visual"
        os.makedirs(_visual_dir,exist_ok=True)
        _visual_path=os.path.join(_visual_dir,f"{_row_account_id}_{campaign_id}_{_row_id}_{_artifact_op}.png")
        try:
            from types import SimpleNamespace as _BannerSnapshot
            from app.services.media_semantic_validator import validate_campaign_banner_vision
            _reference_qa_asset=None
            _used_stock_visual_fallback=False
            _used_clean_ai_visual_fallback=False
            # If the owner uploaded campaign-specific media, it is the visual source of truth.
            # Use OpenAI image edit with the real object as reference; only fall back to
            # text-to-image when this exact campaign has no owner upload.
            from app.models.media_asset import MediaAsset as _BannerMediaAsset
            from app.models.media_folder import MediaFolder as _BannerMediaFolder
            _bf=db.query(_BannerMediaFolder).filter(_BannerMediaFolder.account_id==row.account_id,_BannerMediaFolder.name==f"campaign_{campaign_id}_manual_photos",_BannerMediaFolder.deleted_at.is_(None)).order_by(_BannerMediaFolder.id.desc()).first()
            _owner_banner_assets=(db.query(_BannerMediaAsset).filter(_BannerMediaAsset.account_id==row.account_id,_BannerMediaAsset.folder_id==_bf.id,_BannerMediaAsset.deleted_at.is_(None),_BannerMediaAsset.source_type=="upload").order_by(_BannerMediaAsset.id.asc()).all()) if _bf else []
            _reference_asset=_owner_banner_assets[(max(1,int(p.get("variant") or 1))-1)%len(_owner_banner_assets)] if _owner_banner_assets else None
            if _reference_asset:
                from app.services import media_storage as _banner_storage
                _reference_storage_key=str(_reference_asset.storage_key)
                _reference_path=_banner_storage.abs_path(_reference_storage_key)
                _reference_asset_id=int(_reference_asset.id)
                _reference_qa_asset=_BannerSnapshot(id=_reference_asset_id,storage_key=_reference_storage_key)
                if _content_local_banner_first():
                    # Owner media is already the strongest identity source. Do not
                    # spend credits restyling it by default: compose the verified
                    # title locally on the real photo and keep the object truthful.
                    _visual_path=_reference_path
                    _used_stock_visual_fallback=True
                    p["banner_reference_media_id"]=_reference_asset_id
                    p["banner_identity_policy"]="owner_reference_direct_local_composition"
                    p["banner_visual_renderer"]="approved_semantic_media"
                    p["banner_text_renderer"]="local_pillow"
                    p["banner_text_source"]="verified_title_prefix"
                    db.commit()
                else:
                    if not _content_paid_fallback():
                        raise RuntimeError("CONTENT_PAID_FALLBACK_DISABLED")
                    from app.services import image_service as _IMG
                    # Image generation is an external/paid network operation and can
                    # take tens of seconds. Never hold an ORM transaction across it.
                    db.commit()
                    _banner_ref_prompt=(
                        "Create ONLY a premium realistic 1:1 photographic visual for an Avito advertisement using the supplied source photo as the AUTHORITATIVE vehicle/object. "
                        "REFERENCE OBJECT LOCK: preserve the same make/model/generation, body shape, paint color, wheels, lights, trim and non-identifying visible marks. "
                        "PRIVACY: never reproduce a real license plate, VIN, serial number, phone number, address, personal name, face or other identifying detail from the source. "
                        "Make any such identifier neutral and unreadable; do not invent a replacement identity. "
                        "Do not substitute another vehicle. Compose one strong subject with clean negative space. "
                        "CRITICAL: NO TEXT, NO LETTERS, NO WORDS, NO NUMBERS, NO PRICE BADGES, NO LOGOS, NO UI, NO POSTER LAYOUT. "
                        "The final Russian headline will be rendered later by BORIS locally. Business context only: "+theme[:1200]
                    )
                    _out=_IMG.generate(db,_row_account_id,_banner_ref_prompt,_visual_path,model=_IMG.PREMIUM_MODEL,quality="medium",size="1024x1024",operation="campaign_openai_reference_visual",reference_path=_reference_path,burst_override=_burst_override)
                    if not _out.get("ok"):
                        raise RuntimeError(_out.get("error") or "REFERENCE_BANNER_FAILED")
                    p["banner_reference_media_id"]=_reference_asset_id
                    p["banner_identity_policy"]="owner_reference_object_and_plate_lock"
            else:
                # Retry convergence: when a prior stock composition already triggered
                # and successfully produced the deterministic clean AI visual, reuse
                # those bytes FIRST. Otherwise every retry starts again from the same
                # busy stock photo and can hot-loop before reaching the good cached
                # fallback. This is a free artifact cache hit, never a second purchase.
                _clean_cached_path=os.path.join(_visual_dir,f"{_row_account_id}_{campaign_id}_{_row_id}_{_artifact_op}_clean_v1.png")
                if os.path.isfile(_clean_cached_path) and os.path.getsize(_clean_cached_path)>0:
                    _visual_path=_clean_cached_path
                    _used_clean_ai_visual_fallback=True
                    p["banner_identity_policy"]="no_owner_reference_available"
                    p["banner_visual_renderer"]="openai_visual_only"
                    p["banner_text_renderer"]="local_pillow"
                    p["banner_text_source"]="verified_title_prefix"
                    p["banner_visual_fallback"]="clean_ai_cached_after_stock_composition_qa"
                    db.commit()
                else:
                    # If this exact card already has a strict semantic-approved photo,
                    # use it as the banner visual source. This is both more truthful and
                    # more reliable than inventing another scene, and it avoids a paid
                    # image call entirely. OpenAI visual generation remains the fallback
                    # only when the card has no verified visual yet.
                    from app.models.media_link import MediaLink as _ExistingVisualLink
                    from app.models.media_asset import MediaAsset as _ExistingVisualAsset
                    _verified_pair=(db.query(_ExistingVisualLink,_ExistingVisualAsset)
                        .join(_ExistingVisualAsset,_ExistingVisualAsset.id==_ExistingVisualLink.media_id)
                        .filter(_ExistingVisualLink.entity_type=="campaign_item",
                                _ExistingVisualLink.entity_id==str(row.id),
                                _ExistingVisualAsset.deleted_at.is_(None),
                                _ExistingVisualAsset.status=="approved",
                                _ExistingVisualAsset.source_provider!="openai")
                        .order_by(_ExistingVisualLink.position.asc(),_ExistingVisualLink.id.asc()).first())
                    if _verified_pair:
                        from app.services import media_storage as _verified_storage
                        _visual_path=_verified_storage.abs_path(str(_verified_pair[1].storage_key))
                        _used_stock_visual_fallback=True
                        p["banner_identity_policy"]="approved_semantic_gallery_visual"
                        p["banner_visual_renderer"]="approved_semantic_media"
                        p["banner_text_renderer"]="local_pillow"
                        p["banner_text_source"]="verified_title_prefix"
                        db.commit()
                    else:
                        if not _content_paid_fallback():
                            raise RuntimeError("CONTENT_PAID_FALLBACK_DISABLED_NO_VERIFIED_MEDIA")
                        from app.services import image_service as _IMG
                        db.commit()
                        _banner_prompt=(
                            "Create ONLY a premium realistic 1:1 photographic visual for an Avito advertisement. "
                            "One strong realistic subject; clean editorial lighting; subject placed mainly on the right; generous negative space on the left. "
                            "No grey flyers, card grids, pseudo-interface, frames, tiny badges, collages or decorative clutter. "
                            "CRITICAL: NO TEXT, NO LETTERS, NO WORDS, NO NUMBERS, NO PRICE BADGES, NO LOGOS, NO UI, NO POSTER LAYOUT. "
                            "The final Russian headline will be rendered later by BORIS locally. Business context only: "+theme[:1200]
                        )
                        _out=_IMG.generate_banner_financially_guarded(
                            db,_row_account_id,_banner_prompt,_visual_path,model=_IMG.PREMIUM_MODEL,
                            quality="medium",size="1024x1024",operation="campaign_openai_visual_only",
                            burst_override=_burst_override, provenance={"campaign_id":int(campaign_id),"item_id":int(_row_id),"source":"campaign_banner_visual"}
                        )
                        if not _out.get("ok"):
                            raise RuntimeError(_out.get("error") or "CAMPAIGN_BANNER_BUDGET_OR_GENERATION_FAILED")
                        p["banner_identity_policy"]="no_owner_reference_available"

            # Deterministic local typography is the only allowed visible-text layer.
            # This removes AI-generated Cyrillic, tiny copy and skewed poster artifacts.
            from app.services.avito_premium_compositor import compose_premium_avito_banner
            _composed=compose_premium_avito_banner(_visual_path,fpath,title)
            if not _composed.get("ok") or not os.path.isfile(fpath) or os.path.getsize(fpath)<=0:
                raise RuntimeError("CAMPAIGN_LOCAL_TEXT_COMPOSITOR_FAILED")
            p["banner_layout"]=str(_composed.get("layout") or "premium_photo_bottom_caption_v3")
            p["banner_text_renderer"]="local_pillow"
            p["banner_text_source"]="verified_title_prefix"
            p["banner_display_title"]=str(_composed.get("display_title") or "")
            p["banner_mobile_text_policy"]=str(_composed.get("text_policy") or "complete_title_prefix_max4_two_lines_min58")
            p["banner_font_size"]=int(_composed.get("font_size") or 0)
            p["banner_line_count"]=int(_composed.get("line_count") or 0)

            # Generated bytes now exist on disk, while no new MediaAsset/MediaLink
            # transaction has started yet. Run the slow external vision QA here
            # using detached scalar snapshots. A rejected creative leaves no DB
            # mutation behind and cannot hold PostgreSQL idle in transaction.
            _banner_qa_asset=_BannerSnapshot(id=0,storage_key=rel)
            banner_qa=validate_campaign_banner_vision(p,_banner_qa_asset,_reference_qa_asset,account_id=_row_account_id,provenance={"campaign_id":int(campaign_id),"item_id":int(_row_id),"source":"campaign_banner_final_qa"})

            # A semantic-approved stock photo can still be compositionally too busy
            # for an advertising banner. Do not hot-loop the same stock composition.
            # If (and only if) stock failed final composition/hierarchy QA and there is
            # no owner-locked physical reference, buy at most ONE deterministic clean
            # visual intent, then keep the same local verified-title compositor. The
            # save_path is stable, so retries are free cache hits and cannot repurchase.
            if (not banner_qa.get("pass") and _used_stock_visual_fallback and _reference_asset is None
                    and _content_paid_fallback()
                    and bool(banner_qa.get("mobile_ok")) and bool(banner_qa.get("claims_ok"))
                    and (not bool(banner_qa.get("composition_ok")) or not bool(banner_qa.get("visual_hierarchy_ok")))):
                from app.services import image_service as _CleanIMG
                _clean_visual_path=os.path.join(_visual_dir,f"{_row_account_id}_{campaign_id}_{_row_id}_{_artifact_op}_clean_v1.png")
                _clean_prompt=(
                    "Create ONLY a premium photorealistic square product photograph for an Avito advertisement. "
                    "One dominant, clearly recognizable subject matching this business context: "+theme[:900]+". "
                    "COMPOSITION: keep the main subject dominant in the upper and central 65 percent; keep the lower 30 percent visually simple and uncluttered for a later local headline overlay. "
                    "Clean editorial lighting, realistic materials, straight geometry, generous negative space, premium product photography. "
                    "NO TEXT, NO LETTERS, NO WORDS, NO NUMBERS, NO LOGOS, NO PRICE, NO UI, NO POSTER, NO COLLAGE, NO BADGES."
                )
                db.commit()
                _clean_out=_CleanIMG.generate_banner_financially_guarded(
                    db,_row_account_id,_clean_prompt,_clean_visual_path,model=_CleanIMG.PREMIUM_MODEL,
                    quality="medium",size="1024x1024",operation="campaign_banner_clean_visual_fallback",
                    burst_override=_burst_override,provenance={"campaign_id":int(campaign_id),"item_id":int(_row_id),"source":"campaign_banner_clean_visual_fallback"}
                )
                if _clean_out.get("ok"):
                    _clean_composed=compose_premium_avito_banner(_clean_visual_path,fpath,title)
                    if _clean_composed.get("ok") and os.path.isfile(fpath) and os.path.getsize(fpath)>0:
                        _clean_qa_asset=_BannerSnapshot(id=0,storage_key=rel)
                        _clean_payload=dict(p)
                        _clean_payload["banner_visual_renderer"]="openai_visual_only"
                        _clean_payload["banner_text_renderer"]="local_pillow"
                        _clean_payload["banner_text_source"]="verified_title_prefix"
                        _clean_payload["banner_layout"]=str(_clean_composed.get("layout") or "premium_photo_bottom_caption_v3")
                        _clean_payload["banner_display_title"]=str(_clean_composed.get("display_title") or "")
                        _clean_payload["banner_mobile_text_policy"]=str(_clean_composed.get("text_policy") or "complete_title_prefix_max4_two_lines_min58")
                        _clean_payload["banner_font_size"]=int(_clean_composed.get("font_size") or 0)
                        _clean_payload["banner_line_count"]=int(_clean_composed.get("line_count") or 0)
                        _clean_qa=validate_campaign_banner_vision(_clean_payload,_clean_qa_asset,None,account_id=_row_account_id,provenance={"campaign_id":int(campaign_id),"item_id":int(_row_id),"source":"campaign_banner_clean_visual_qa"})
                        if _clean_qa.get("pass"):
                            banner_qa=_clean_qa; _banner_qa_asset=_clean_qa_asset
                            _used_stock_visual_fallback=False; _used_clean_ai_visual_fallback=True
                            p.update({
                                "banner_identity_policy":"no_owner_reference_available",
                                "banner_visual_renderer":"openai_visual_only",
                                "banner_text_renderer":"local_pillow",
                                "banner_text_source":"verified_title_prefix",
                                "banner_layout":str(_clean_composed.get("layout") or "premium_photo_bottom_caption_v3"),
                                "banner_display_title":str(_clean_composed.get("display_title") or ""),
                                "banner_mobile_text_policy":str(_clean_composed.get("text_policy") or "complete_title_prefix_max4_two_lines_min58"),
                                "banner_font_size":int(_clean_composed.get("font_size") or 0),
                                "banner_line_count":int(_clean_composed.get("line_count") or 0),
                                "banner_visual_fallback":"clean_ai_after_stock_composition_qa",
                            })

            if not banner_qa.get("pass"):
                # One paid image attempt is enough. If the AI visual itself is good but
                # the composed banner fails copy/readability QA, BORIS must not buy a
                # second image. Re-compose the same verified headline on the first
                # already-approved semantic gallery photo and re-run only the cheap QA.
                from app.models.media_link import MediaLink as _FallbackMediaLink
                from app.models.media_asset import MediaAsset as _FallbackMediaAsset
                from app.services import media_storage as _fallback_storage
                _fallback_pair=(db.query(_FallbackMediaLink,_FallbackMediaAsset)
                    .join(_FallbackMediaAsset,_FallbackMediaAsset.id==_FallbackMediaLink.media_id)
                    .filter(_FallbackMediaLink.entity_type=="campaign_item",
                            _FallbackMediaLink.entity_id==str(row.id),
                            _FallbackMediaAsset.deleted_at.is_(None),
                            _FallbackMediaAsset.status=="approved",
                            _FallbackMediaAsset.source_provider!="openai")
                    .order_by(_FallbackMediaLink.position.asc(),_FallbackMediaLink.id.asc()).first())
                if _fallback_pair:
                    _fallback_asset=_fallback_pair[1]
                    _fallback_source=_fallback_storage.abs_path(str(_fallback_asset.storage_key))
                    _fallback_rel=f"{_row_account_id}/banners/campaign_openai/campaign_{campaign_id}_item_{_row_id}_op_{_artifact_op}_stockfallback.png"
                    _fallback_fpath=os.path.join("/root/BORIS/backend/images",_fallback_rel)
                    _fallback_composed=compose_premium_avito_banner(_fallback_source,_fallback_fpath,title)
                    if _fallback_composed.get("ok") and os.path.isfile(_fallback_fpath) and os.path.getsize(_fallback_fpath)>0:
                        _fallback_qa_asset=_BannerSnapshot(id=0,storage_key=_fallback_rel)
                        _fallback_qa=validate_campaign_banner_vision(p,_fallback_qa_asset,None,account_id=_row_account_id,provenance={"campaign_id":int(campaign_id),"item_id":int(_row_id),"source":"campaign_banner_fallback_qa"})
                        if _fallback_qa.get("pass"):
                            rel=_fallback_rel; fpath=_fallback_fpath; _banner_qa_asset=_fallback_qa_asset
                            banner_qa=_fallback_qa; _used_stock_visual_fallback=True
                            p["banner_identity_policy"]="approved_semantic_gallery_visual"
                            p["banner_visual_renderer"]="approved_semantic_media"
                            p["banner_text_renderer"]="local_pillow"
                            p["banner_text_source"]="verified_title_prefix"
                            p["banner_display_title"]=str(_fallback_composed.get("display_title") or "")
                            p["banner_mobile_text_policy"]=str(_fallback_composed.get("text_policy") or "title_prefix_max4_two_lines_min44")
                            p["banner_font_size"]=int(_fallback_composed.get("font_size") or 0)
                            p["banner_line_count"]=int(_fallback_composed.get("line_count") or 0)
            p["banner_qa"]={"passed":bool(banner_qa.get("pass")),"identity_ok":bool(banner_qa.get("identity_ok")),"text_ok":bool(banner_qa.get("text_ok")),"mobile_ok":bool(banner_qa.get("mobile_ok")),"claims_ok":bool(banner_qa.get("claims_ok")),"plate_ok":bool(banner_qa.get("plate_ok")),"composition_ok":bool(banner_qa.get("composition_ok")),"professional_ok":bool(banner_qa.get("professional_ok")),"visual_hierarchy_ok":bool(banner_qa.get("visual_hierarchy_ok")),"issues":list(banner_qa.get("issues") or [])[:8]}
            if not banner_qa.get("pass"):
                raise RuntimeError("Баннер не прошёл финальную проверку текста, автомобиля или мобильной читаемости")

            # Generic item/gallery semantics is also external paid vision. Plan the
            # post-regeneration gallery with detached scalar objects and validate it
            # before registering/deleting any MediaAsset/MediaLink rows.
            from types import SimpleNamespace as _SNS
            from app.models.media_link import MediaLink
            from app.models.media_asset import MediaAsset
            from app.services.media_semantic_validator import classify_item_media_vision, stamp_item_semantic_evidence
            _existing_links=(db.query(MediaLink,MediaAsset).join(MediaAsset,MediaAsset.id==MediaLink.media_id)
                             .filter(MediaLink.entity_type=="campaign_item",MediaLink.entity_id==str(row.id),MediaAsset.deleted_at.is_(None))
                             .order_by(MediaLink.position.asc(),MediaLink.id.asc()).all())
            def _asnap(a): return _SNS(id=int(a.id),storage_key=str(a.storage_key),tags=list(a.tags or []),account_id=str(a.account_id or ''),source_provider=str(a.source_provider or ''),source_ref=str(a.source_ref or ''))
            _new_banner=_SNS(id=-max(1,int(row.id)),storage_key=str(rel),tags=[],account_id=str(row.account_id),source_provider=("boris_local" if _used_stock_visual_fallback else "openai"),source_ref=("campaign_stock_visual_local_text_v1" if _used_stock_visual_fallback else "campaign_openai_visual_local_text_v1"))
            _planned=[(_SNS(position=0),_new_banner)]
            _preserved_asset_ids=[]
            _next_gallery_pos=1
            for _lnk,_a in _existing_links:
                _source_ref=str(_a.source_ref or '')
                _asset_url="/images/"+str(_a.storage_key or "").lstrip("/")
                # Never preserve the banner being replaced as gallery photo #1.
                # Legacy review banners can have historical source_ref values that are
                # not in the current allow/deny list, so source_ref alone is unsafe.
                # The payload's exact previous banner_url is the authoritative identity.
                _is_replaced_banner=bool(old_banner and (_asset_url==old_banner or str(old_banner).endswith("/"+str(_a.storage_key or "").lstrip("/"))))
                if _is_replaced_banner or _source_ref in ('campaign_openai_gpt_image_2','campaign_openai_visual_local_text_v1','campaign_stock_visual_local_text_v1'):
                    continue
                # A genuine pre-banner hero (for example a Pexels/source photo) must
                # become gallery photo #1. Only the exact previous banner is removed.
                _planned.append((_SNS(position=_next_gallery_pos),_asnap(_a)))
                _preserved_asset_ids.append(int(_a.id))
                _next_gallery_pos+=1
            _row_id=int(row.id); _row_account=str(row.account_id)
            db.commit()
            _semantic_verdicts=classify_item_media_vision(p,_planned,account_id=_row_account,provenance={"campaign_id":int(campaign_id),"item_id":int(_row_id),"source":"campaign_banner_regeneration"})
            _semantic_bad=[v for v in _semantic_verdicts if (not bool(v.get("match"))) or float(v.get("confidence") or 0)<0.80]
            if _semantic_bad:
                # Persist exact rejected gallery IDs before returning. This turns a
                # probabilistic banner retry loop into deterministic self-repair:
                # the worker can unlink only bad stock, AUTO-MEDIA excludes it, and
                # the already-good banner/other photos are not needlessly repurchased.
                from app.services.media_semantic_validator import semantic_content_fingerprint as _semantic_reject_fp
                _fp=_semantic_reject_fp(p); _rejs=[]; _known=set()
                for _oldrej in (p.get("media_semantic_rejections") or []):
                    if isinstance(_oldrej,dict) and str(_oldrej.get("content_fingerprint") or "")==_fp:
                        _rejs.append(_oldrej)
                        try: _known.add(int(_oldrej.get("media_id") or 0))
                        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=6018)
                for (_pl,_pa),_vv in zip(_planned,_semantic_verdicts):
                    try: _cc=float(_vv.get("confidence") or 0)
                    except Exception: _cc=0.0
                    if int(getattr(_pa,"id",0) or 0)>0 and ((not bool(_vv.get("match"))) or _cc<0.80) and int(_pa.id) not in _known:
                        _rejs.append({"media_id":int(_pa.id),"content_fingerprint":_fp,"subject":str(_vv.get("subject") or "")[:180],"reason":str(_vv.get("reason") or "")[:240],"confidence":_cc})
                        _known.add(int(_pa.id))
                from app.models.campaign_item import CampaignItem as _BadCampaignItem
                _badrow=db.query(_BadCampaignItem).filter(_BadCampaignItem.id==_row_id).first()
                if _badrow:
                    _badp=_ff_payload(_badrow); _badp["media_semantic_rejections"]=_rejs[-40:]
                    _badrow.payload_json=json.dumps(_badp,ensure_ascii=False); db.commit()
                raise RuntimeError("Баннер или фотография не прошли финальную смысловую проверку объявления")
            # CAMPAIGN_BANNER_CHILD_TARIFF_EXACTLY_ONCE_V1: all expensive visual and gallery QA has passed. Settle the exact
            # campaign-item tariff intent immediately before canonical media/payload
            # mutation. A crash replays the same artifact and billing key safely.
            from app.api.billing import check_and_consume as _billing_consume_child
            _child_tariff_key=f"campaign-banner-item:{campaign_id}:{_row_id}:{_artifact_op}"
            try:
                _child_charge=_billing_consume_child(_account_for_billing,"banners",1,idempotency_key=_child_tariff_key)
            except Exception as _child_bill_exc:
                raise RuntimeError("CAMPAIGN_BANNER_TARIFF_PENDING:"+str(_child_bill_exc)[:120])
            if not _child_charge.get("allowed",False):
                raise RuntimeError("CAMPAIGN_BANNER_TARIFF_BLOCKED:"+str(_child_charge.get("blocked_reason") or _child_charge.get("message") or "billing_pending")[:120])

            from app.models.campaign_item import CampaignItem
            row=db.query(CampaignItem).filter(CampaignItem.id==_row_id).first()
            if not row: raise RuntimeError("Объявление изменилось во время смысловой проверки")
            url="/images/"+rel
            existing=[str(x) for x in (p.get("images") or []) if str(x).strip() and str(x).strip()!=old_banner and str(x).strip()!=url]
            p["banner_url"]=url
            p["images"]=[url]+existing
            p["banner_engine"]=("approved_stock_visual_local_text_v1" if _used_stock_visual_fallback else "openai_visual_local_text_v1")
            p["banner_model"]=("approved-semantic-photo+local-pillow" if _used_stock_visual_fallback else "gpt-image-2+local-pillow")
            p["banner_provider"]=("stock_visual_local_text" if _used_stock_visual_fallback else "openai_visual_local_text")
            p["banner_visual_renderer"]=("approved_semantic_media" if _used_stock_visual_fallback else "openai_visual_only")
            p["banner_text_renderer"]="local_pillow"
            if _force and _idem:
                p["banner_regeneration_key"]=_idem
            p["banner_generated_at"] = __import__("datetime").datetime.utcnow().isoformat()

            # Banner is a real campaign image, not merely a URL in payload.
            # Register/link it into the canonical media graph so Feed validation
            # sees exactly the same images the client sees on the final screen.
            from app.services import media_service as M
            owner_uid=M.owner_of_account(db,row.account_id) or M._uid(user)
            if owner_uid is None:
                raise RuntimeError("Не удалось определить владельца медиаматериала")
            asset,_created=M.register_asset(
                db,owner_uid,row.account_id,rel,__import__('os').path.basename(rel),
                media_type="image",source_type=("derived" if _used_stock_visual_fallback else "ai_generated"),created_by=M._uid(user),
                source_provider=("boris_local" if _used_stock_visual_fallback else "openai"),
                source_ref=("campaign_stock_visual_local_text_v1" if _used_stock_visual_fallback else "campaign_openai_visual_local_text_v1"))
            # Rebuild the canonical graph atomically: exactly one banner hero,
            # followed by every previously selected non-banner photo. This avoids
            # losing the former stock hero when the banner takes position 0 and
            # also removes stale banner links from previous regeneration attempts.
            db.query(MediaLink).filter(MediaLink.entity_type=="campaign_item",MediaLink.entity_id==str(row.id)).delete(synchronize_session=False)
            db.flush()
            M.link(db,asset,"campaign_item",str(row.id),"hero",0,created_by=M._uid(user))
            if _preserved_asset_ids:
                _preserved_rows=db.query(MediaAsset).filter(MediaAsset.id.in_(_preserved_asset_ids),MediaAsset.deleted_at.is_(None)).all()
                _preserved_map={int(a.id):a for a in _preserved_rows}
                for _pos,_aid in enumerate(_preserved_asset_ids,1):
                    _pa=_preserved_map.get(int(_aid))
                    if not _pa:
                        raise RuntimeError("Выбранная фотография изменилась во время сохранения баннера")
                    M.link(db,_pa,"campaign_item",str(row.id),"gallery",_pos,created_by=M._uid(user))
            row.hero_media_id=int(asset.id)
            db.flush()
            links=(db.query(MediaLink,MediaAsset).join(MediaAsset,MediaAsset.id==MediaLink.media_id)
                   .filter(MediaLink.entity_type=="campaign_item",MediaLink.entity_id==str(row.id),MediaAsset.deleted_at.is_(None))
                   .order_by(MediaLink.position.asc(),MediaLink.id.asc()).all())
            _vby={str(a.storage_key):v for (_l,a),v in zip(_planned,_semantic_verdicts)}
            _ordered_verdicts=[_vby.get(str(a.storage_key)) for _l,a in links]
            if any(v is None for v in _ordered_verdicts):
                raise RuntimeError("Галерея изменилась во время смысловой проверки")
            stamp_item_semantic_evidence(p,links,_ordered_verdicts)
            row.payload_json=json.dumps(p,ensure_ascii=False); row.content_hash=CS.content_hash(p); row.status="draft"
            db.commit()
            changed.append(row.id)
            generated.append(row.id)
        except Exception as exc:
            db.rollback(); errors.append({"id":row.id,"message":str(exc)[:240]})
    # Tariff units were settled per item before any campaign mutation. Keep the
    # workflow counter informational only; never perform a second aggregate charge.
    if generated:
        ff.setdefault("billing_charged",{})["banners"]=int(ff.get("billing_charged",{}).get("banners") or 0)+len(generated)
        ff["billing_pending_banners"]=0
        ff["billing_pending_banners_key"]=""

    ff["step"]="category" if changed else "media"; _ff_state_save(db,campaign,ff)
    return {"status":"ok" if changed else "error","changed":changed,"generated":generated,"errors":errors,"banner_engine":"openai_visual_local_text_v1","banner_model":"gpt-image-2+local-pillow","text_renderer":"local_pillow"}


@router.post("/{campaign_id}/workflow/category/{item_id}")
def ff_workflow_category(campaign_id:int,item_id:int,body:dict,db=Depends(_db),user=Depends(get_current_user)):
    from app.api.feed_contract import resolve_feed_contract, resolve_applicable_params, SYSTEM_FIELDS
    allowed=_allowed_accounts(db,user); campaign=_campaign_or_404(db,campaign_id,allowed); row=_ff_item_by_id(db,campaign_id,item_id,allowed)
    _ff_assert_mutable(campaign)
    if not row: raise HTTPException(404,"Объявление не найдено")
    p=_ff_payload(row); tid=str(body.get("template_id") or p.get("template_id") or "").strip(); contract=resolve_feed_contract(tid,_db=db)
    if not contract.get("ok"): raise HTTPException(422,{"message":"Категория не найдена","code":contract.get("code")})
    p["template_id"]=tid; p["category_path"]=contract.get("category_path") or p.get("category_path",""); parts=[x.strip() for x in str(p["category_path"]).split(">") if x.strip()]; p["category"]=parts[1] if len(parts)>=2 else (parts[0] if parts else p.get("category",""))
    incoming=dict(body.get("params") or {}); fields=contract.get("fields") or {}
    unknown=[k for k in incoming if k not in fields and k not in SYSTEM_FIELDS]
    if unknown: raise HTTPException(422,{"message":"Есть параметры, которых нет в выбранной подкатегории Avito","fields":unknown})
    params=dict(p.get("params") or {})
    for k,v in incoming.items():
        if v in (None,""): params.pop(k,None)
        else: params[k]=v
    coverage=resolve_applicable_params(params,contract); params=coverage.get("params") or {}; p["params"]=params
    p["intake_contract_version"]=contract.get("contract_version") or ""
    p["parameter_coverage"]={"available":coverage.get("available_fields",0),"filled":coverage.get("filled_fields",0),"removed_non_applicable":coverage.get("removed_non_applicable") or []}
    row.payload_json=json.dumps(p,ensure_ascii=False); row.content_hash=CS.content_hash(p); row.status="draft"; db.commit()
    missing=_ff_contract_missing(db,tid,params); _,ff=_ff_state_load(campaign); ff["step"]="settings"; _ff_state_save(db,campaign,ff)
    return {"status":"ok","item":CS.item_public(row),"missing_required":missing,"parameter_coverage":p["parameter_coverage"]}


@router.get("/{campaign_id}/workflow/preview")
def ff_workflow_preview(campaign_id:int,db=Depends(_db),user=Depends(get_current_user)):
    # Same actual CampaignItem payload used by validation/export; no parallel preview DTO source.
    return campaign_preview(campaign_id,db,user)


@router.post("/{campaign_id}/workflow/autopilot-mutate")
def ff_autopilot_mutate(campaign_id:int,body:FFMutationBody,db=Depends(_db),user=Depends(get_current_user)):
    # AI Marketing Autopilot mutation layer. Allowed fields are enforced by workflow/mutate.
    return ff_workflow_mutate(campaign_id,body,db,user)


# Publication identity callback for the existing Feed Factory uploader.
# This endpoint records an already-returned Avito item id; it never uploads.
class FFPublicationIdentityBody(BaseModel):
    account_id: str
    feed_identity: str
    avito_item_id: str
    published_at: str | None = None
    publication_version: str | None = None


@router.post("/{campaign_id}/publication_identity")
def campaign_publication_identity(campaign_id: int, body: FFPublicationIdentityBody,
                                  db=Depends(_db), user=Depends(get_current_user)):
    allowed = _allowed_accounts(db, user)
    _campaign_or_404(db, campaign_id, allowed)
    if body.account_id not in set(allowed or []):
        raise HTTPException(status_code=403, detail="account forbidden")
    from app.models.campaign_item import CampaignItem
    row = db.query(CampaignItem).filter(
        CampaignItem.campaign_id == campaign_id,
        CampaignItem.account_id == body.account_id,
        CampaignItem.feed_identity == body.feed_identity,
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="feed identity not found in campaign")
    from app.services.campaign_identity import bind_publication
    result = bind_publication(
        db, body.account_id, body.feed_identity, body.avito_item_id,
        published_at=body.published_at,
        publication_version=body.publication_version,
    )
    if result.get("status") == "blocked":
        db.rollback()
        raise HTTPException(status_code=409, detail=result)
    db.commit()
    return {**result, "external_upload": False, "campaign_id": campaign_id}
