import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _strategy_source():
    src = (ROOT / "app/api/campaigns.py").read_text(encoding="utf-8")
    return src[src.index('@router.get("/strategy")'):src.index('@router.get("/context")')]


def test_strategy_get_put_routes_and_account_guard_exist():
    src = _strategy_source()
    assert '@router.get("/strategy")' in src
    assert '@router.put("/strategy")' in src
    assert src.count("if aid not in _allowed_accounts(db, user):") == 2
    assert src.count('raise HTTPException(status_code=404, detail="Аккаунт не найден")') == 2


def test_strategy_storage_is_scoped_by_account_id_on_read_and_write():
    src = _strategy_source()
    assert src.count("key='campaign_strategy'") >= 2
    assert src.count("account_id=:a") >= 2
    assert "INSERT INTO storage (account_id, key, value)" in src
    assert "UPDATE storage SET value=:v WHERE id=:id" in src


def test_strategy_cross_account_denial_happens_before_storage_io():
    src = _strategy_source()
    for name in ("def campaign_strategy", "def save_campaign_strategy"):
        block = src[src.index(name):]
        if name == "def campaign_strategy":
            block = block[:block.index("class CampaignStrategyBody")]
        assert block.index("if aid not in _allowed_accounts(db, user):") < block.index("storage")


def test_strategy_target_active_ads_is_clamped_to_safe_bounds():
    src = _strategy_source()
    assert 'max(0, min(int(payload.get("target_active_ads") or 0), 100000))' in src


def test_strategy_missing_or_corrupt_storage_returns_empty_object():
    src = _strategy_source()
    assert "strategy = {}" in src
    assert "strategy = json.loads(raw) if raw else {}" in src
    assert "strategy if isinstance(strategy, dict) else {}" in src


def test_strategy_put_persists_flags_without_publication_or_provider_actions():
    src = _strategy_source()
    for field in (
        "directions", "cities", "use_price_list", "use_site_services",
        "unique_texts", "unique_images", "require_verified_price", "require_preflight",
    ):
        assert field in src

    module = ast.parse(src)
    fn = next(
        node for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "save_campaign_strategy"
    )
    call_names = {
        node.func.id if isinstance(node.func, ast.Name) else node.func.attr
        for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Name, ast.Attribute))
    }
    forbidden = {
        "publish", "publication", "enqueue", "start_job", "create_job",
        "publish_campaign", "materialize_campaign", "send_provider_request",
        "avito_write", "post_avito", "update_avito_listing",
    }
    assert call_names.isdisjoint(forbidden)
    assert "db.commit()" in src


def test_campaign_price_contract_has_source_priority_and_fail_closed_states():
    src = Path(ROOT / "app/api/campaigns.py").read_text(encoding="utf-8")
    for source in (
        "price_list", "structured_website", "confirmed_knowledge",
        "existing_ads", "feed", "pricing_rules",
    ):
        assert source in src
    for field in (
        "price_type", "source_reference", "confidence", "verified",
        "updated_at", "conflict", "candidates", "PRICE_CONFLICT", "NEEDS_DATA",
    ):
        assert field in src
    assert "WHERE account_id=:a" in src
    assert "status='confirmed' OR confidence >= 80" in src


def test_campaign_price_contract_rejects_unscoped_or_freeform_values():
    src = Path(ROOT / "app/api/campaigns.py").read_text(encoding="utf-8")
    resolver = src[src.index("def _campaign_resolve_prices"):src.index('@router.get("/strategy")')]
    assert "WHERE account_id=:a" in resolver
    assert "category='cena'" in resolver
    # Numeric parsing is centralized in the helper; the resolver only consumes
    # normalized numeric evidence and never interprets arbitrary prose itself.
    assert "_campaign_price_from_row" in resolver
    assert "re.findall" in src
    assert "source_reference" in resolver
    assert "\\u00A0" in src
    assert "\\u202F" in src
    assert "(?:\\d{1,3}" in src
    assert 'r"[ \\t\\r\\n]+"' in src


def test_campaign_price_contract_prioritizes_sources_and_surfaces_conflicts():
    src = Path(ROOT / "app/api/campaigns.py").read_text(encoding="utf-8")
    resolver = src[src.index("def _campaign_resolve_prices"):src.index('@router.get("/strategy")')]
    priority = resolver.index('priority = {')
    assert resolver.index('"price_list": 0', priority) < resolver.index('"structured_website": 1', priority)
    assert resolver.index('"structured_website": 1', priority) < resolver.index('"confirmed_knowledge": 2', priority)
    assert resolver.index('"confirmed_knowledge": 2', priority) < resolver.index('"existing_ads": 3', priority)
    assert resolver.index('"existing_ads": 3', priority) < resolver.index('"feed": 4', priority)
    assert resolver.index('"feed": 4', priority) < resolver.index('"pricing_rules": 5', priority)
    assert '"status": "PRICE_CONFLICT" if conflict else' in resolver
    assert '"READY" if chosen else "NEEDS_DATA"' in resolver
    assert 'source = "existing_ads" if source_type == "avito_item"' in resolver


def test_campaign_price_contract_tenant_scopes_every_price_read():
    src = Path(ROOT / "app/api/campaigns.py").read_text(encoding="utf-8")
    resolver = src[src.index("def _campaign_resolve_prices"):src.index('@router.get("/strategy")')]
    assert resolver.count("account_id=:a") >= 3
    assert resolver.count('db.execute(') >= 3
    assert "WHERE account_id=:a" in resolver


def test_campaign_text_contract_is_sales_v4_formula_and_fact_guarded():
    src = Path(ROOT / "app/api/campaigns.py").read_text(encoding="utf-8")
    for marker in (
        "def _ff_formula_meta",
        "copy_formula_id",
        "def _ff_search_keywords",
        "def _ff_factual_example",
        'p["copy_quality_contract"]="sales_v4"',
        'p["seo_search_block"]={"present":True,"position":"before_uniqueness"',
        'p["content_needs_data"]={"code":"factual_uniqueness_parameters_missing"',
    ):
        assert marker in src


def test_campaign_text_contract_requires_seo_then_factual_example():
    src = Path(ROOT / "app/api/campaigns.py").read_text(encoding="utf-8")
    quality = src[src.index("def _ff_copy_quality_errors"):src.index("def _ff_contract_with_payload_overlay")]
    assert "copy_seo_block_missing" in quality
    assert "copy_uniqueness_footer_missing" in quality
    assert "Звоните нам, если ищете:" in src
    assert "Пример товара" in src
    assert "Пример услуги" in src


def test_sales_v4_remains_strict_for_uniqueness_and_duplicate_guards():
    src = Path(ROOT / "app/api/campaigns.py").read_text(encoding="utf-8")
    uniqueness = src[src.index("def _ff_uniqueness_required"):src.index("def _ff_similarity_text")]
    validation = src[src.index("def campaign_validation"):src.index("def campaign_approval")]
    strict = '{"sales_v2","sales_v3","sales_v4"}'
    assert strict in uniqueness
    assert validation.count(strict) >= 2


def test_campaign_validation_releases_read_transaction_before_duplicate_scan():
    src = Path(ROOT / "app/api/campaigns.py").read_text(encoding="utf-8")
    validation = src[src.index("def campaign_validation"):src.index("def campaign_approval")]
    assert "SimpleNamespace" in validation
    assert "if (_idx+1)%8==0:" in validation
    assert validation.count("db.commit()") >= 2
    assert validation.index("db.commit()") < validation.index("_ff_near_duplicate_pairs(rows)")


def test_campaign_validation_does_not_swallow_broken_db_transactions():
    src = Path(ROOT / "app/api/campaigns.py").read_text(encoding="utf-8")
    validation = src[src.index("def _ff_validate_row"):src.index("def _ff_uniqueness_required")]
    assert "DBAPIError as _ValidationDBAPIError" in validation
    assert validation.count("except _ValidationDBAPIError:") >= 2
    assert validation.count("except _ValidationDBAPIError:") == validation.count("except _ValidationDBAPIError:\n        raise") + validation.count("except _ValidationDBAPIError:\n            raise")


def test_campaign_approval_and_readiness_bound_large_validation_transactions():
    src = Path(ROOT / "app/api/campaigns.py").read_text(encoding="utf-8")
    approval = src[src.index("def campaign_approval"):src.index("def campaign_preview")]
    assert "CAMPAIGN_APPROVAL_SCOPE_EXPENSIVE_CHECKS_V1" in approval
    assert "_approval_rows=[r for r in rows if int(r.id) in wanted]" in approval
    assert "_ff_cross_campaign_duplicate_conflicts(db,campaign_id,_approval_rows)" in approval
    assert "_ff_cross_campaign_near_duplicate_conflicts(db,campaign_id,_approval_rows)" in approval
    assert "_ff_near_duplicate_pairs(_approval_rows)" in approval
    assert "CAMPAIGN_APPROVAL_LONG_BATCH_TX_BOUNDARY_V1" in approval
    assert "if changed % 8 == 0:" in approval
    readiness = src[src.index("def _ff_delivery_snapshot"):src.index("def _ff_target_readiness_snapshot")]
    assert "DELIVERY_SNAPSHOT_NO_EXPIRE_V1" in readiness
    assert "db.expire_on_commit = False" in readiness
    assert "_campaign_id=int(campaign.id)" in readiness
    assert "for _semantic_idx,_semantic_row in enumerate(scoped):" in readiness
    assert "if (_semantic_idx+1)%8==0:" in readiness

    media = src[src.index("def _ff_media_near_duplicate_pairs"):src.index("def campaign_validation")]
    assert "MEDIA_NEAR_DUPLICATE_TX_BOUNDARY_V1" in media
    assert "raw_entries=[]" in media
    assert "db.commit()" in media
    assert media.index("db.commit()") < media.index("for i in range(len(entries))")


def test_campaign_feed_validation_releases_long_item_validation_transaction():
    src = Path(ROOT / "app/api/campaigns.py").read_text(encoding="utf-8")
    validation = src[src.index("def campaign_feed_validate"):src.index("def campaign_owner_activation")]
    assert "for _idx,_row in enumerate(scoped):" in validation
    assert "if (_idx+1)%8==0:" in validation
    assert validation.index("db.commit()") < validation.index("validate_feed_xmlcheck(url)")


def test_campaign_stock_banner_clean_fallback_covers_all_visual_quality_failures():
    src = Path(ROOT / "app/api/campaigns.py").read_text(encoding="utf-8")
    needle = "if (not banner_qa.get(\"pass\") and _used_stock_visual_fallback"
    start = src.index(needle)
    guard = src[start:start+900]
    assert 'bool(banner_qa.get("claims_ok"))' in guard
    assert 'not bool(banner_qa.get("mobile_ok"))' in guard
    assert 'not bool(banner_qa.get("composition_ok"))' in guard
    assert 'not bool(banner_qa.get("professional_ok"))' in guard
    assert 'not bool(banner_qa.get("visual_hierarchy_ok"))' in guard


def test_sales_v4_content_needs_data_is_an_explicit_fail_closed_validation_error():
    src = Path(ROOT / "app/api/campaigns.py").read_text(encoding="utf-8")
    validation = src[src.index("def _ff_validate_row"):src.index("def _ff_uniqueness_required")]
    assert 'p.get("content_needs_data")' in validation
    assert '"code":"content_needs_data"' in validation


def test_copy_price_claim_parser_handles_nbsp_thousands_without_false_secondary_sums():
    src = Path(ROOT / "app/api/campaigns.py").read_text(encoding="utf-8")
    module = ast.parse(src)
    node = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == "_ff_explicit_copy_price_claims")
    unit = ast.Module(body=[node], type_ignores=[])
    ns = {}
    exec(compile(ast.fix_missing_locations(unit), "campaigns-price-helper", "exec"), ns)
    parse = ns["_ff_explicit_copy_price_claims"]
    assert parse("Цена 15 000 ₽") == [15000]
    assert parse("Цена 15\u00a0000 ₽") == [15000]
    assert parse("Стоимость от 15\u202f000 руб.") == [15000]
    assert parse("монтаж 1500 ₽") == []



def test_owner_activation_requires_fresh_delivery_can_send_gate():
    from pathlib import Path

    src = Path(ROOT / "app/api/campaigns.py").read_text(encoding="utf-8")
    activation = src[src.index("def campaign_owner_activate"):src.index("def campaign_safe_export")]
    assert "CAMPAIGN_OWNER_ACTIVATION_CAN_SEND_V1" in activation
    assert 'if not delivery.get("can_send"):' in activation
    assert '"image_unique":bool(delivery.get("image_unique"))' in activation
    assert '"content_unique":bool(delivery.get("content_unique"))' in activation
    assert '"intake_ready":bool(delivery.get("intake_ready"))' in activation
    assert '"account_bound":bool(delivery.get("account_bound"))' in activation
    assert activation.index('if not delivery.get("can_send"):') < activation.index("if not body.confirmed:")



def test_auto_media_enforces_cross_item_unique_asset_allocation():
    from pathlib import Path

    src = Path(ROOT / "app/api/campaigns.py").read_text(encoding="utf-8")
    auto = src[src.index("def ff_workflow_auto_media"):src.index('@router.post("/{campaign_id}/workflow/photo-stack")')]
    assert "CAMPAIGN_MEDIA_UNIQUE_ALLOCATION_V1" in auto
    assert "_campaign_media_owners" in auto
    assert "_used_by_other_items" in auto
    assert "_campaign_excluded_media_ids" in auto
    assert "exclude_media_ids=sorted(_campaign_excluded_media_ids)" in auto
    assert "mid not in _campaign_excluded_media_ids" in auto
    assert "_allocated_media_ids.update(int(x) for x in mids)" in auto
