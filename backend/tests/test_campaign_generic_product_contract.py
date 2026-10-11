from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGNS = (ROOT / "app/api/campaigns.py").read_text(encoding="utf-8")
POST_WATCH = (ROOT / "app/services/campaign_post_publish_watch.py").read_text(encoding="utf-8")


def _block(start: str, end: str) -> str:
    a = CAMPAIGNS.index(start)
    b = CAMPAIGNS.index(end, a)
    return CAMPAIGNS[a:b]


def test_core_campaign_pipeline_has_no_planeta_account_or_campaign_ids():
    # Legacy post-publish recovery may stay PLANETA-scoped, but the universal
    # creation/readiness/feed pipeline must not depend on that fixture.
    core = "\n".join([
        _block("def _campaign_resolve_prices", '@router.get("/strategy")'),
        _block("def _ff_formula_meta", "def _ff_validate_row"),
        _block("def _ff_validate_row", "def _ff_delivery_snapshot"),
        _block("def _ff_delivery_projection", "def _ff_avito_autoload_capability"),
        _block("def _ff_extract_plan", '@router.post("/{campaign_id}/workflow/brief")'),
    ])
    for forbidden in (
        "planetazayavki_65985",
        "PLANETA_ACCOUNT_ID",
        "campaign_id == 249",
        "campaign_id == 250",
        "campaign_id == 252",
        "campaign_id.in_((249, 250, 252))",
    ):
        assert forbidden not in core


def test_planeta_recovery_is_explicitly_isolated_from_generic_core():
    assert 'PLANETA_ACCOUNT_ID = "planetazayavki_65985"' in POST_WATCH
    assert 'if str(account_id or "") != PLANETA_ACCOUNT_ID' in POST_WATCH
    assert "PLANETA_DISTINCT_SLOT_IDENTITY_FALLBACK_V1" in POST_WATCH


def test_dynamic_quantity_is_a_real_materialization_contract():
    plan = _block("def _ff_extract_plan", '@router.post("/{campaign_id}/workflow/brief")')
    assert "qty=max(0,min(qty,1000))" in plan
    assert "for idx in range(qty)" in plan
    assert 'src["variant"]=idx+1' in plan
    assert "matrix=expanded" in plan
    assert '"quantity": qty' in plan


def test_geo_answer_preserves_requested_quantity():
    answers = _block("def ff_workflow_answers", '@router.get("/{campaign_id}/workflow/intake")')
    assert 'qty=max(0,min(int(ff.get("quantity") or 0),1000))' in answers
    assert "for idx in range(qty)" in answers
    assert 'src["variant"]=idx+1' in answers


def test_price_resolver_is_account_scoped_and_fail_closed():
    resolver = _block("def _campaign_resolve_prices", '@router.get("/strategy")')
    assert resolver.count("account_id=:a") >= 3
    for source in ("price_list","structured_website","confirmed_knowledge","existing_ads","feed","pricing_rules"):
        assert source in resolver
    assert '"status": "PRICE_CONFLICT" if conflict else' in resolver
    assert '"READY" if chosen else "NEEDS_DATA"' in resolver


def test_readiness_is_account_scoped_and_not_first_item_proxy():
    readiness = _block("def _ff_target_readiness_snapshot", "def _ff_ai_marketer_projection")
    for gate in (
        "price_pass","text_pass","content_qa_pass","formula_assigned",
        "hypothesis_assigned","image_pass","image_unique","banner_pass",
        "category_pass","attributes_pass","account_scope_pass","no_conflict",
        "no_needs_data",
    ):
        assert gate in readiness
    assert "db.commit()" not in readiness
    assert "feed_send_to_avito" not in readiness


def test_empty_new_campaign_readiness_is_truthful_not_cross_account():
    readiness = _block("def _ff_target_readiness_snapshot", "def _ff_ai_marketer_projection")
    assert "if rows:" in readiness
    assert '"EMPTY_CAMPAIGN"' in readiness
    assert 'not rows and delivery.get("account_bound")' in readiness
    assert '"validation_state": "NOT_CHECKED"' in readiness
    assert '"can_send": False' in readiness


def test_dry_delivery_projection_is_pure_and_media_link_aware():
    delivery = _block("def _ff_delivery_projection", "def _ff_avito_autoload_capability")
    assert "MediaLink" in delivery
    assert "MediaAsset" in delivery
    assert "_build_xml" in delivery
    assert "db.commit()" not in delivery
    assert "feed_send_to_avito" not in delivery


def test_campaign_geo_parser_has_no_finite_city_whitelist():
    plan = _block("def _ff_extract_plan", '@router.post("/{campaign_id}/workflow/brief")')
    assert "_known_geo" not in plan
    assert "capitalized Cyrillic geo tail" in plan


def test_compact_prompt_supports_new_russian_city_without_code_change(monkeypatch):
    from types import SimpleNamespace
    import app.api.campaigns as campaigns

    # This is a parser/bootstrap contract, not a PostgreSQL integration test.
    # Keep it runnable in an isolated regression environment so a missing local
    # DATABASE_URL can never hide a city-parser regression behind auth failure.
    monkeypatch.setattr(campaigns, "_ff_account_context", lambda db, account_id: {})
    monkeypatch.setattr(campaigns, "_ff_category_candidates", lambda db, direction: [])
    monkeypatch.setattr(campaigns, "_ff_validated_category_hint", lambda db, direction, hints: {})

    c = SimpleNamespace(default_account_id="__qa_campaign_generic_city__")
    r = campaigns._ff_extract_plan(
        object(),
        c,
        campaigns.FFWorkflowBriefBody(
            brief="Ремонт кофемашин в Тюмень, 1 объявление",
            variants=1,
        ),
    )
    assert r.get("cities") == ["Тюмень"], r
    assert r.get("directions") == ["Ремонт кофемашин"], r
    assert len(r.get("matrix") or []) == 1, r


def test_readiness_delivery_projection_is_fail_closed_for_incomplete_legacy_rows():
    readiness = _block("def _ff_delivery_snapshot", "def _ff_target_readiness_snapshot")
    assert "projection_error=None" in readiness
    assert "except Exception as exc" in readiness
    assert 'validation_state="ERROR"' in readiness
    assert "Do not invent" in readiness
    assert '"projection_error":projection_error' in readiness


def test_readiness_and_ai_use_durable_campaignitem_hypothesis_assignment():
    readiness = _block("def _ff_target_readiness_snapshot", "def _ff_ai_marketer_projection")
    ai = _block("def _ff_ai_marketer_projection", "def _campaign_guardian_job_public")
    assert 'getattr(row,"hypothesis_id",None)' in readiness
    assert 'durable_hypothesis=str(getattr(row,"hypothesis_id",None)' in ai


def test_readiness_fail_closed_when_price_status_needs_data():
    readiness = _block("def _ff_target_readiness_snapshot", "def _ff_ai_marketer_projection")
    assert 'str(p.get("price_status") or "").strip().upper() == "NEEDS_DATA"' in readiness


def test_readiness_no_needs_data_tracks_required_verified_price_truth():
    readiness = _block("def _ff_target_readiness_snapshot", "def _ff_ai_marketer_projection")
    assert 'require_verified_price = bool(strategy.get("require_verified_price", True))' in readiness
    assert 'and (all_price or not require_verified_price)' in readiness


def test_price_readiness_rejects_verified_zero_except_explicit_not_applicable():
    from app.api.campaigns import _campaign_price_readiness_pass
    cases = [
        ({"verified_price": True, "price_status": "VERIFIED", "price": 1500}, True),
        ({"price_verified": True, "price_status": "VERIFIED", "price": "1 500"}, True),
        ({"verified_price": True, "price_status": "VERIFIED", "price": 0}, False),
        ({"verified_price": True, "price": 0}, False),
        ({"verified_price": True, "price_status": "NEEDS_DATA", "price": 1500}, False),
        ({"verified_price": True, "price_status": "PRICE_CONFLICT", "price": 1500}, False),
        ({"verified_price": True, "price_status": "NOT_APPLICABLE", "price": 0}, True),
        ({"verified_price": False, "price_status": "VERIFIED", "price": 1500}, False),
    ]
    for payload, expected in cases:
        assert _campaign_price_readiness_pass(payload) is expected


def test_service_title_with_product_word_preserves_service_intent():
    from app.api.campaigns import _ff_core_title
    assert _ff_core_title("Чистка мягкой мебели") == "Чистка мягкой мебели"
    assert _ff_core_title("Химчистка дивана") == "Химчистка дивана"


def test_factual_example_accepts_direction_category_and_city_without_invented_specs():
    from app.api.campaigns import _ff_factual_example
    text, meta = _ff_factual_example(
        "Чистка мягкой мебели",
        "Казань",
        {"category_path": "Услуги > Предложение услуг > Уборка > Чистка мягкой мебели"},
    )
    assert meta.get("present") is True
    assert meta.get("needs_data") is False
    assert "Чистка мягкой мебели" in text
    assert "Казань" in text


def test_local_first_copy_meets_minimum_sales_length_without_invented_price():
    from app.api.campaigns import _ff_bulk_safe_copy
    payload = {
        "variant": 1,
        "city": "Казань",
        "category_path": "Услуги > Предложение услуг > Уборка > Чистка мягкой мебели",
        "price": 0,
    }
    title, description = _ff_bulk_safe_copy(
        "Чистка мягкой мебели",
        payload,
        {"brief": "Чистка мягкой мебели в Казани. Не выдумывать цену.", "account_context": {}},
    )
    assert title == "Чистка мягкой мебели"
    assert len(description) >= 650
    assert "₽" not in description


def test_price_confirmation_is_separate_from_generic_mutate_and_sets_provenance():
    price = _block('@router.post("/{campaign_id}/workflow/price-confirm")', '@router.post("/{campaign_id}/workflow/text-file")')
    assert 'p["price_verified"]=True' in price
    assert 'p["verified_price"]=True' in price
    assert 'p["price_status"]="VERIFIED"' in price
    assert 'p["price_source"]="owner_confirmed"' in price
    assert 'p["price_source_reference"]=f"campaign_ui:item:{row.id}"' in price
    assert 'p["price_status"]="NEEDS_DATA"' in price
    assert 'p["price_status"]="NOT_APPLICABLE"' in price


def test_campaign_frontend_uses_price_confirmation_route():
    page = (ROOT.parent / "frontend/app/dashboard/campaign/[id]/page.tsx").read_text(encoding="utf-8")
    assert '"/workflow/price-confirm"' in page
    save = page.split("const save = async", 1)[1].split("const saveAnswers", 1)[0]
    assert 'patch.price' not in save
    assert 'clear: true' in save
    assert 'price: numericPrice' in save


def test_campaign_media_replace_imports_authoritative_media_service_and_invalidates_old_proof():
    mutate = _block('@router.post("/{campaign_id}/workflow/mutate")', '@router.post("/{campaign_id}/workflow/price-confirm")')
    assert 'from app.services import media_service as M' in mutate
    assert 'gallery=[x for x in existing_images if x and x not in {new_hero_url,old_hero_url}]' in mutate
    assert 'p["images"]=[new_hero_url]+gallery' in mutate
    assert 'p["media_semantic_evidence"]=[]' in mutate
    assert 'M.link(db,a,"campaign_item"' in mutate


def test_campaign_readiness_has_near_phash_guard():
    src = CAMPAIGNS
    assert 'def _ff_media_near_duplicate_pairs' in src
    delivery = _block("def _ff_delivery_snapshot", "def _ff_target_readiness_snapshot")
    assert '_ff_media_near_duplicate_pairs(db,scoped,max_distance=6)' in delivery
    assert 'media_near_duplicate_pairs' in delivery
    assert 'image_unique=(not image_duplicate_reports) and (not media_near_duplicate_pairs)' in delivery


def test_campaign_strategy_requires_banners_by_default():
    assert 'require_banners: bool = True' in CAMPAIGNS
    readiness = _block("def _ff_target_readiness_snapshot", "def _ff_ai_marketer_projection")
    assert 'strategy.get("require_banners", True)' in readiness


def test_campaign_banner_gate_checks_each_item_not_unique_banner_urls():
    readiness = _block("def _ff_target_readiness_snapshot", "def _ff_ai_marketer_projection")
    assert "banner_assignments = [" in readiness
    assert "or (all(banner_assignments) and not banner_qa_explicit_failures)" in readiness
    assert "len(banners) == len(scoped)" not in readiness


def test_campaign_editor_has_real_dirty_and_cancel_semantics():
    page = (ROOT.parent / "frontend/app/dashboard/campaign/[id]/page.tsx").read_text(encoding="utf-8")
    assert 'const [draftDirty, setDraftDirty] = useState(false)' in page
    assert 'Есть несохранённые изменения.' in page
    assert 'window.addEventListener("beforeunload", warn)' in page
    cancel = page.split("const cancelEdit = () =>", 1)[1].split("const save = async", 1)[0]
    assert "clearCampaignDraft(scope)" in cancel
    assert 'setDraft({})' in cancel
    assert 'setOpen(null)' in cancel
    assert 'onClick={cancelEdit}>Отмена</Button>' in page


def test_semantic_copy_changes_invalidate_media_and_banner_proof():
    mutate = _block('@router.post("/{campaign_id}/workflow/mutate")', '@router.post("/{campaign_id}/workflow/price-confirm")')
    assert '_semantic_copy_keys={"title","description"}' in mutate
    assert 'p["media_semantic_evidence"]=[]' in mutate
    assert '"copy_changed_requires_recheck"' in mutate


def test_campaign_save_sends_only_changed_fields_and_skips_unchanged_category():
    page = (ROOT.parent / "frontend/app/dashboard/campaign/[id]/page.tsx").read_text(encoding="utf-8")
    save = page.split("const save = async", 1)[1].split("const runImport", 1)[0]
    assert 'const patch: any = {}' in save
    assert 'patch.title = draft.title' in save
    assert 'patch.description = draft.description' in save
    assert 'Object.keys(patch).length' in save
    assert 'String(draft.template_id) !== currentTemplateId || nextParams !== currentParams' in save
    assert 'images:' not in save


def test_semantic_proof_invalidates_only_when_copy_value_really_changes():
    mutate = _block('@router.post("/{campaign_id}/workflow/mutate")', '@router.post("/{campaign_id}/workflow/price-confirm")')
    assert '_semantic_before=' in mutate
    assert '_semantic_changed=any(' in mutate
    assert 'str(p.get(key) or "") != _semantic_before.get(key,"")' in mutate
    assert 'if _semantic_changed:' in mutate


def test_price_change_rebinds_media_but_invalidates_explicit_banner_qa():
    price = _block('@router.post("/{campaign_id}/workflow/price-confirm")', '@router.post("/{campaign_id}/workflow/text-file")')
    assert 'semantic_content_fingerprint' in price
    assert '_ev["content_fingerprint"]=_fp' in price
    assert '"price_changed_requires_recheck"' in price
    readiness = _block("def _ff_target_readiness_snapshot", "def _ff_ai_marketer_projection")
    assert 'banner_qa_explicit_failures' in readiness
    assert 'p.get("banner_qa",{}).get("passed") is False' in readiness


def test_category_or_params_change_invalidates_visual_proof():
    category = _block('@router.post("/{campaign_id}/workflow/category/{item_id}")', '@router.get("/{campaign_id}/workflow/preview")')
    assert 'category_changed=' in category
    assert 'params_changed=' in category
    assert 'p["media_semantic_evidence"]=[]' in category
    assert '"category_or_params_changed_requires_recheck"' in category


def test_readiness_media_provenance_uses_durable_media_links():
    readiness = _block("def _ff_target_readiness_snapshot", "def _ff_ai_marketer_projection")
    assert "MEDIA_PROVENANCE_TRUTH_V1" in readiness
    assert "MediaLink as _ReadinessMediaLink" in readiness
    assert "MediaAsset as _ReadinessMediaAsset" in readiness
    assert '"campaign_item_id": int(row.id)' in readiness
    assert '"asset_id": asset_id' in readiness
    assert '"sha256": asset.sha256' in readiness
    assert '"phash": asset.phash' in readiness
    assert 'selected_source, selection_reason = "NO_MEDIA"' in readiness


def test_category_change_resets_previous_contract_params():
    category = _block('@router.post("/{campaign_id}/workflow/category/{item_id}")', '@router.get("/{campaign_id}/workflow/preview")')
    assert "CATEGORY_PARAM_RESET_V1" in category
    assert 'params={} if tid != old_tid else dict(p.get("params") or {})' in category


def test_frontend_category_change_clears_stale_param_draft():
    frontend = (ROOT.parent / "frontend/app/dashboard/campaign/[id]/page.tsx").read_text(encoding="utf-8")
    assert 'const templateChanged=String(next)!==String(it.payload?.template_id||"");' in frontend
    assert 'params:templateChanged?"{}":draft.params' in frontend


def test_frontend_single_campaign_scope_retry_is_generic_and_bounded():
    frontend = (ROOT.parent / "frontend/app/dashboard/campaign/page.tsx").read_text(encoding="utf-8")
    assert "SINGLE_CAMPAIGN_SCOPE_RETRY_V1" in frontend
    assert "SINGLE_CAMPAIGN_REF_SYNC_V2" in frontend
    assert "campaigns.length !== 1" in frontend
    assert "selectedCampaignRef.current !== onlyId" in frontend
    assert "selectedCampaignRef.current = onlyId" in frontend
    assert 'const retryKey = account + ":" + onlyId;' in frontend
    assert "singleCampaignScopeRetryRef.current === retryKey" in frontend
    assert "refreshCampaignScope(onlyId, account)" in frontend
    assert "pbi_artashes_plitka_mo_83993" not in frontend


def test_frontend_campaign_scope_guard_uses_selected_campaign_identity_without_lagging_ref():
    frontend = (ROOT.parent / "frontend/app/dashboard/campaign/page.tsx").read_text(encoding="utf-8")
    assert "CURRENT_SCOPE_CAMPAIGN_FENCE_V4" in frontend
    scope_block = frontend.split("CURRENT_SCOPE_CAMPAIGN_FENCE_V4", 1)[1].split("const rejectScopeMismatch", 1)[0]
    assert "selectedCampaignRef.current === requestedCampaignId" in scope_block
    assert "scopeRequest === campaignScopeSequence.current" not in scope_block
    assert "activeAccountRef.current" not in scope_block


def test_campaign_overview_humanizes_readiness_and_feed_status():
    frontend = (ROOT.parent / "frontend/app/dashboard/campaign/page.tsx").read_text(encoding="utf-8")
    detail = (ROOT.parent / "frontend/app/dashboard/campaign/[id]/page.tsx").read_text(encoding="utf-8")
    assert "Готовность объявлений" in frontend
    assert "Нужно подтвердить цену" in frontend
    assert "Нужно закончить проверку Avito" in frontend
    assert "Все объявления Avito связаны с текущими проектами." in frontend
    assert "Block reasons:" not in frontend
    assert "Feed reconcile:" not in frontend
    assert 'id="campaign-items"' in detail
    assert 'id="avito-feed-control"' in detail


def test_frontend_same_scope_refresh_preserves_valid_readiness_until_replacement_arrives():
    frontend = (ROOT.parent / "frontend/app/dashboard/campaign/page.tsx").read_text(encoding="utf-8")
    assert "SAME_SCOPE_REFRESH_NO_BLANK_V1" in frontend
    refresh = frontend.split("const refreshCampaignScope =", 1)[1].split("// Campaign selection must load", 1)[0]
    invalid_branch = refresh.split('if (!requestedAccount', 1)[1].split('// SAME_SCOPE_REFRESH_NO_BLANK_V1', 1)[0]
    assert "setCampaignReadiness({});" in invalid_branch
    assert "setAiMarketer({});" in invalid_branch
    preserved = refresh.split("// SAME_SCOPE_REFRESH_NO_BLANK_V1", 1)[1]
    assert "setCampaignReadiness({});" not in preserved.split("// CAMPAIGN_SCOPE_RESPONSE_FENCE_V2", 1)[0]
    assert "setAiMarketer({});" not in preserved.split("// CAMPAIGN_SCOPE_RESPONSE_FENCE_V2", 1)[0]


def test_campaign_source_contract_and_product_binding_are_fail_closed():
    import app.api.campaigns as campaigns
    assert campaigns._ff_normalize_source_urls(["site.ru/catalog", "https://site.ru/catalog/", "bad value"]) == ["https://site.ru/catalog"]
    assert campaigns._ff_product_price_value({"price": "1 490 ₽"}) == 1490
    src=(ROOT/"app/api/campaigns.py").read_text(encoding="utf-8")
    assert "CAMPAIGN_SOURCE_CONTRACT_V1" in src
    assert "PRODUCT_BINDING_MATRIX_V1" in src
    assert '"binding_rule":"one_product_one_photos_features_price"' in src
    assert '"policy":"exact_source_product_only"' in src
    assert 'source_provider="site_parser"' in src
    assert '"source_product_characteristics"' in src
    assert '"price_status":("VERIFIED"' in src


def test_campaign_inventory_policy_is_plan_only_before_guarded_execution():
    src=(ROOT/"app/api/campaigns.py").read_text(encoding="utf-8")
    assert '@router.get("/inventory-policy")' in src
    assert '@router.put("/inventory-policy")' in src
    assert '@router.get("/inventory-policy/plan")' in src
    assert '"mode":"plan_only"' in src
    assert '"external_mutations":0' in src
    assert '"reactivate":reactivate_planned' in src
    assert '"create_new":create_planned' in src
    assert '"edit_active":len(active)' in src


def test_campaign_frontend_has_durable_progress_sources_and_maintenance_mode():
    overview=(ROOT.parent/"frontend/app/dashboard/campaign/page.tsx").read_text(encoding="utf-8")
    detail=(ROOT.parent/"frontend/app/dashboard/campaign/[id]/page.tsx").read_text(encoding="utf-8")
    assert 'data-testid="campaign-source-contract"' in overview
    assert 'source_urls: sourceUrlList' in overview
    assert 'bind_product_data: bindProductData' in overview
    assert 'id="campaign-maintain-active"' in overview
    assert 'data-testid="campaign-inventory-plan"' in overview
    assert '/api/campaign/inventory-policy/plan?account_id=' in overview
    assert 'data-testid="campaign-durable-progress"' in detail
    assert 'role="progressbar"' in detail
    assert "Прогресс хранится на сервере" in detail


def test_campaign_overview_uses_durable_background_job_progress():
    backend = (ROOT / "app/api/campaigns.py").read_text(encoding="utf-8")
    overview = (ROOT.parent / "frontend/app/dashboard/campaign/page.tsx").read_text(encoding="utf-8")
    assert "CAMPAIGN_OVERVIEW_DURABLE_PROGRESS_V1" in backend
    assert '"active_job"' in backend
    assert "CAMPAIGN_OVERVIEW_PROGRESS_POLL_V1" in overview
    assert 'data-testid="campaign-card-durable-progress"' in overview
    assert 'aria-label="Прогресс проекта"' in overview
    assert "Можно закрыть или обновить страницу — BORIS продолжит эту же задачу." in overview


def test_campaign_inventory_maintenance_has_durable_run_status_and_worker():
    api=(ROOT/"app/api/campaigns.py").read_text(encoding="utf-8")
    jobs=(ROOT/"app/services/jobs.py").read_text(encoding="utf-8")
    reliability=(ROOT/"app/services/reliability.py").read_text(encoding="utf-8")
    worker=(ROOT/"boris_background_worker.py").read_text(encoding="utf-8")
    overview=(ROOT.parent/"frontend/app/dashboard/campaign/page.tsx").read_text(encoding="utf-8")
    assert '@router.post("/inventory-policy/run")' in api
    assert '@router.get("/inventory-policy/status")' in api
    assert "_campaign_inventory_plan_snapshot" in api
    assert '"campaign_inventory_maintain": _run_campaign_inventory_maintain' in jobs
    assert "def _inventory_update_managed_prices" in jobs
    assert "def _inventory_create_deficit_campaign" in jobs
    assert '"campaign_inventory_maintain"' in reliability
    assert "def _campaign_inventory_scheduler_loop" in worker
    assert '("campaign_inventory_scheduler", _campaign_inventory_scheduler_loop)' in worker
    assert '/api/campaign/inventory-policy/run?account_id=' in overview
    assert '/api/campaign/inventory-policy/status?account_id=' in overview
    assert 'data-testid="campaign-inventory-progress"' in overview
    assert 'aria-label="Прогресс поддержания активных объявлений"' in overview
    assert "Запустить сейчас" in overview


def test_inventory_maintenance_is_fail_closed_for_unknown_products_and_legacy_ads():
    jobs=(ROOT/"app/services/jobs.py").read_text(encoding="utf-8")
    portfolio=(ROOT/"app/services/initial_portfolio.py").read_text(encoding="utf-8")
    assert "verified_product_source_missing" in jobs
    assert "legacy_isolated_browser_required" in jobs
    assert "legacy_price_browser_required" in jobs
    assert "verified_price_missing" in jobs
    assert "def reactivate_for_target_deficit" in portfolio
    assert "external_reactivation_unavailable" in portfolio
    assert "apply_external: bool = False" in portfolio


def test_inventory_range_price_is_deterministic_and_single_feed_send():
    jobs=(ROOT/"app/services/jobs.py").read_text(encoding="utf-8")
    assert 'seed=f"{account_id}|{fid}|{marketing_today_iso()}"' in jobs
    assert 'target_price=low+(n % (high-low+1))' in jobs
    handler=jobs.split("def _run_campaign_inventory_maintain",1)[1].split("def _run_client_owner_contract",1)[0]
    assert "reactivate_for_target_deficit(account_id,requested,apply_external=False)" in handler
    assert "_inventory_update_managed_prices(db,account_id,plan,policy,apply_external=False)" in handler
    assert "feed_send_to_avito(account_id=account_id)" in handler


def test_inventory_legacy_browser_editor_is_account_isolated_and_fail_closed():
    mass=(ROOT/"app/api/mass_editor.py").read_text(encoding="utf-8")
    jobs=(ROOT/"app/services/jobs.py").read_text(encoding="utf-8")
    assert "LEGACY_CORE_EDITOR_FIELDS_V1" in mass
    assert "def queue_legacy_inventory_job" in mass
    assert "def _resolve_legacy_image_paths" in mass
    assert '"mode":"inventory_legacy"' in mass
    assert '"executor":"isolated_browser"' in mass
    assert 'item["run_status"]="reactivate_submitting"' in mass
    assert 'item["run_status"]="reactivate_submitted"' in mass
    assert '"pending_external_verification"' in mass
    assert 'if await _verify_field(page, field, value):' in mass
    assert "def _inventory_queue_legacy_browser" in jobs
    assert "exact_source_product_missing" in jobs
    assert '"custom:"+str(key).strip()' in jobs
    assert '"images"=images' not in jobs  # protect against invalid assignment typo
    assert 'changes["images"]=images' in jobs


def test_inventory_parent_waits_for_legacy_browser_child():
    jobs=(ROOT/"app/services/jobs.py").read_text(encoding="utf-8")
    handler=jobs.split("def _run_campaign_inventory_maintain",1)[1].split("def _run_client_owner_contract",1)[0]
    assert "legacy_browser_job_id" in handler
    assert '"stage":"legacy_browser"' in handler
    assert "BORIS редактирует старые объявления в изолированной Avito-сессии" in handler
    assert "legacy_browser_not_fully_verified" in handler
    assert '"legacy_external_changed":int(legacy_job.get("changed") or 0) if legacy_job_id else 0' in handler


def test_campaign_client_workspace_is_humanized_and_progressive():
    overview = (ROOT.parent / "frontend/app/dashboard/campaign/page.tsx").read_text(encoding="utf-8")
    detail = (ROOT.parent / "frontend/app/dashboard/campaign/[id]/page.tsx").read_text(encoding="utf-8")
    for label in ("Проекты", "Объявления", "Эксперименты", "Автоматизация", "Настройки"):
        assert label in overview
    assert 'data-testid="campaign-client-hero"' in overview
    assert 'data-testid="campaign-client-tabs"' in overview
    assert "Как работает «Объявления под ключ»" in overview
    assert 'data-testid="campaign-source-import-progress"' in overview
    assert "Можно закрыть или обновить страницу — BORIS продолжит ту же задачу." in overview
    assert 'data-testid="campaign-ad-card-grid"' in detail
    assert "BORIS предлагает улучшить текст" in detail
    for label in ("Сделать короче", "Сделать продающее", "Сделать проще", "Добавить выгоды", "Добавить ключевые слова", "Изменить первый абзац", "Переписать полностью", "Изменить через BORIS"):
        assert label in detail
    # Internal vocabulary may remain in source logic, but these old raw labels
    # must not be rendered to the client as primary settings.
    assert ">TARGET ACTIVE ADS <" not in overview
    assert ">ACTIVE: " not in overview
    assert ">DEFICIT: " not in overview


def test_inventory_daily_mode_executes_exact_legacy_edits_with_durable_child_job():
    jobs=(ROOT/"app/services/jobs.py").read_text(encoding="utf-8")
    mass=(ROOT/"app/api/mass_editor.py").read_text(encoding="utf-8")
    worker=(ROOT/"boris_background_worker.py").read_text(encoding="utf-8")
    assert "def _inventory_queue_legacy_browser" in jobs
    assert "queue_legacy_inventory_job" in jobs
    assert '"allow_edit_existing"' in jobs
    assert '"allow_update_price"' in jobs
    assert '"allow_update_text"' in jobs
    assert '"allow_update_media"' in jobs
    assert '"allow_update_category"' in jobs
    assert '"legacy_browser_job_id"' in jobs
    assert '"legacy_browser"' in jobs
    assert "exact_source_product_missing" in jobs
    assert "def queue_legacy_inventory_job" in mass
    for field in ('"title"', '"description"', '"price"', '"category"', '"images"'):
        assert field in mass
    assert 'action not in {"edit","reactivate"}' in mass
    assert "def _campaign_inventory_scheduler_loop" in worker
    assert "once-per-policy/day" in worker


def test_project_discussion_context_is_read_only_and_price_fail_closed():
    api=(ROOT/"app/api/campaigns.py").read_text(encoding="utf-8")
    overview=(ROOT.parent/"frontend/app/dashboard/campaign/page.tsx").read_text(encoding="utf-8")
    block=api.split('@router.get("/project-context")',1)[1].split('@router.get("/context")',1)[0]
    assert "existing_manus_context" in block
    assert "project_discussion_context" in block
    assert '"prices"' not in block
    assert "discussion_prices_require_current_verified_source" in block
    assert "INSERT " not in block.upper()
    assert "UPDATE " not in block.upper()
    assert "DELETE " not in block.upper()
    assert '/api/campaign/project-context?account_id=' in overview
    assert 'data-testid="campaign-project-context"' in overview
    assert "BORIS знает о проекте" in overview
    assert "Из прошлых обсуждений" in overview


def test_inventory_legacy_category_change_requires_explicit_avito_category():
    jobs=(ROOT/"app/services/jobs.py").read_text(encoding="utf-8")
    block=jobs.split("def _inventory_queue_legacy_browser",1)[1].split("def _run_campaign_inventory_maintain",1)[0]
    assert "LEGACY_CATEGORY_EXPLICIT_AVITO_ONLY_V1" in block
    assert 'product.get("avito_category") or product.get("category_avito")' in block
    assert 'changes["category"]=avito_category' in block
    assert 'category_path.split(">")[-1]' not in block


def test_campaign_card_editor_has_human_tools_and_durable_history():
    api=(ROOT/"app/api/campaigns.py").read_text(encoding="utf-8")
    detail=(ROOT.parent/"frontend/app/dashboard/campaign/[id]/page.tsx").read_text(encoding="utf-8")
    assert "def _ff_push_edit_history" in api
    assert "edit_history" in api
    assert "remove_media_ids" in api
    assert 'item["media"]=media_by_item.get(str(rid),[])' in api
    assert 'item["hero_media_id"]=' in api
    for label in (
        "Сделать короче","Сделать продающее","Сделать проще","Добавить выгоды",
        "Добавить ключевые слова","Изменить первый абзац","Переписать полностью",
        "Изменить через BORIS",
    ):
        assert label in detail
    for label in ("Сделать главным","Удалить","Добавить / заменить фото","Подобрать через BORIS",
                  "Сделать другой","Изменить текст","Заменить фото","История версий","Вернуть этот текст"):
        assert label in detail
    assert 'String(draft.title||"").length} / 50' in detail


def test_campaign_projects_are_visible_human_cards_not_autoload_debug():
    overview=(ROOT.parent/"frontend/app/dashboard/campaign/page.tsx").read_text(encoding="utf-8")
    assert "Проекты объявлений" in overview
    assert 'data-testid="campaign-card-durable-progress"' in overview
    assert "Показать проекты автозагрузки" not in overview
    assert ">Проекты автозагрузки<" not in overview


def test_campaign_project_picker_and_bulk_editor_are_client_facing():
    overview=(ROOT.parent/"frontend/app/dashboard/campaign/page.tsx").read_text(encoding="utf-8")
    detail=(ROOT.parent/"frontend/app/dashboard/campaign/[id]/page.tsx").read_text(encoding="utf-8")
    assert 'data-testid="campaign-project-picker"' in overview
    assert "Открыть результаты A/B" in overview
    assert 'data-testid="campaign-bulk-editor"' in detail
    assert "Массовая настройка BORIS" in detail
    assert "Переменные уникализации" in detail
    for token in ("{товар}","{услуга}","{город}","{цена}","{категория}","{номер}","{вариант}"):
        assert token in detail
    assert "Посмотреть результат" in detail
    assert "Применить к выбранным" in detail
    assert "Подобрать фото" in detail
    assert "Сделать баннеры" in detail


def test_bulk_transform_is_preview_first_and_product_scoped():
    api=(ROOT/"app/api/campaigns.py").read_text(encoding="utf-8")
    assert "class FFBulkTransformBody" in api
    assert "def _ff_bulk_variable_map" in api
    assert "source_product_characteristics" in api
    assert "def _ff_render_bulk_template" in api
    block=api.split('@router.post("/{campaign_id}/workflow/bulk-transform")',1)[1].split('@router.post("/{campaign_id}/workflow/price-confirm")',1)[0]
    assert "preview_only" in block
    assert "confirmation_required" in block
    assert "missing_variables" in block
    assert "validate_feed_item" in block
    assert "category_fields_missing" in block
    assert "bulk_change_requires_recheck" in block
    assert "owner_confirmed_bulk" in block


def test_ab_tests_auto_enroll_and_owner_actions_are_explicit():
    api=(ROOT/"app/api/campaigns.py").read_text(encoding="utf-8")
    panel=(ROOT.parent/"frontend/app/dashboard/campaign/AvitoABTestsPanel.tsx").read_text(encoding="utf-8")
    page=(ROOT.parent/"frontend/app/dashboard/campaign/ab-tests/page.tsx").read_text(encoding="utf-8")
    assert "AB_AUTO_ENROLL_PUBLISHED_V1" in api
    assert "AB_CURRENT_WORKING_SET_V1" in api
    assert "def _ab_row_is_current" in api
    assert 'lifecycle in {"published","publishing"}' in api
    assert 'provider in {"active","old","inactive","closed","expired","removed"}' in api
    assert "published_rows=account_rows" in api
    assert "experiment_state_label" in api
    assert "formula_label" in api and "hypothesis_label" in api
    assert '@router.post("/ab-tests/action")' in api
    action=api.split('@router.post("/ab-tests/action")',1)[1].split('@router.get("/{campaign_id}")',1)[0]
    assert 'action=="finish"' in action
    assert "confirmation_required" in action
    assert "_ab_finish_managed_feed_item" in action
    assert "feed_send_to_avito" in action
    assert 'action=="scale"' in action
    assert 'scenario_type="ab_scale"' in action
    assert '"campaign_prepare"' in action
    assert "Результаты A/B тестов" in page
    assert "Масштабировать" in panel
    assert "В завершённые" in panel
    assert "Что тестируем" in panel
    assert "Сколько новых объявлений создать" in panel


def test_media_library_uses_human_display_names():
    media_api=(ROOT/"app/api/media.py").read_text(encoding="utf-8")
    media_ui=(ROOT.parent/"frontend/app/ui/ec.tsx").read_text(encoding="utf-8")
    media_page=(ROOT.parent/"frontend/app/dashboard/campaign/media/page.tsx").read_text(encoding="utf-8")
    assert "MEDIA_HUMAN_LABELS_V1" in media_api
    assert 'asset["display_name"]' in media_api
    assert 'asset["display_context"]' in media_api
    assert "source_product" in media_api
    assert "stock_topic:" in media_api
    assert "legacy_position_by_media" in media_api
    assert 'item_(\\d+)' in media_api
    assert 'display_name=f"{clean_base} · {material_type} {display_number}"' in media_api
    assert "item.display_name || item.filename" in media_ui
    assert 'title="Мои материалы"' in media_page
    assert 'data-testid="media-folder-picker"' in media_page
    assert "Все материалы" in media_page
    assert "Техническая информация" in media_page


def test_ab_tests_auto_enroll_and_owner_actions_are_productized():
    api=(ROOT/"app/api/campaigns.py").read_text(encoding="utf-8")
    panel=(ROOT.parent/"frontend/app/dashboard/campaign/AvitoABTestsPanel.tsx").read_text(encoding="utf-8")
    page=(ROOT.parent/"frontend/app/dashboard/campaign/ab-tests/page.tsx").read_text(encoding="utf-8")
    assert "AB_AUTO_ENROLL_PUBLISHED_V1" in api
    assert '@router.get("/ab-tests")' in api
    assert '@router.post("/ab-tests/action")' in api
    assert 'if action=="finish":' in api
    assert 'if action=="scale":' in api
    assert "_ab_finish_managed_feed_item" in api
    assert 'scenario_type="ab_scale"' in api
    assert "Результаты A/B тестов" in page
    assert "Каждое объявление, которое BORIS публикует в Avito, автоматически попадает сюда" in panel
    assert "Масштабировать" in panel
    assert "В завершённые" in panel
    assert '"Создать "+scaleCount+" вариантов"' in panel


def test_campaign_bulk_editor_supports_variables_visuals_and_avito_contract():
    detail=(ROOT.parent/"frontend/app/dashboard/campaign/[id]/page.tsx").read_text(encoding="utf-8")
    api=(ROOT/"app/api/campaigns.py").read_text(encoding="utf-8")
    assert 'data-testid="campaign-bulk-editor"' in detail
    assert "Переменные уникализации" in detail
    assert "{товар}" in detail and "{город}" in detail and "{цена}" in detail
    assert "Шаблон названия" in detail
    assert "Что добавить в описание" in detail
    assert "Категория и характеристики Avito" in detail
    assert "Загрузить свои фото" in detail
    assert "Подобрать фото" in detail
    assert "Сделать баннеры" in detail
    assert "Посмотреть результат" in detail
    assert "Применить к выбранным" in detail
    assert 'workflow/bulk-transform' in detail
    assert '@router.post("/{campaign_id}/workflow/bulk-transform")' in api


def test_ab_auto_enroll_uses_current_publication_state_not_source_label():
    """Real CampaignItems with client-specific source labels must enter A/B when they are in the current Avito working set."""
    from pathlib import Path
    api=(Path(__file__).resolve().parents[1]/"app/api/campaigns.py").read_text(encoding="utf-8")
    assert "AB_CURRENT_WORKING_SET_V1" in api
    block=api[api.index("AB_CURRENT_WORKING_SET_V1"):api.index("formula_names=", api.index("AB_CURRENT_WORKING_SET_V1"))]
    assert "def _ab_row_is_current" in block
    assert 'lifecycle in {"published","publishing"}' in block
    assert 'provider in {"active","old","inactive","closed","expired","removed"}' in block
    assert "published_rows=account_rows" in block
    assert "_ABCampaignItem.source.in_" not in block
