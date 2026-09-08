#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Zero-paid-call regression contract for BORIS Avito visuals.

Checks code + DB + local compositor. Does not call OpenAI or Avito and does not publish.
"""
from __future__ import annotations
import json, os, tempfile
from pathlib import Path
from PIL import Image
from sqlalchemy import text
from app.db.session import SessionLocal
from app.services.avito_premium_compositor import compose_premium_avito_banner

ROOT=Path('/root/BORIS/backend')
LEGACY_CALL_TOKENS=('create_full_ai_banner(', 'FullAiRequest(', '/api/banners/full_ai')
PRODUCTION_CALLERS=(
    ROOT/'app/api/avito.py', ROOT/'app/api/plan_items.py', ROOT/'app/api/tasks.py', ROOT/'app/api/campaigns.py',
    Path('/root/BORIS/frontend/app/dashboard/page.tsx')
)

def check(cond, name, detail=''):
    return {'name':name,'ok':bool(cond),'detail':str(detail)[:600]}

def main():
    checks=[]
    for path in PRODUCTION_CALLERS:
        body=path.read_text(encoding='utf-8')
        # Comments are allowed to name the forbidden endpoint; executable invocation is not.
        offenders=[]
        for i,line in enumerate(body.splitlines(),1):
            code=line.split('#',1)[0]
            if any(tok in code for tok in LEGACY_CALL_TOKENS): offenders.append(f'{i}:{code.strip()}')
        checks.append(check(not offenders, f'no_legacy_full_ai_caller:{path.name}', offenders[:8]))

    camp=(ROOT/'app/api/campaigns.py').read_text(encoding='utf-8')
    required=(
        'campaign_openai_visual_local_text_v1', 'compose_premium_avito_banner',
        'campaign_openai_visual_only', 'campaign_openai_reference_visual',
        'banner_text_renderer', 'local_pillow', 'banner_visual_renderer', 'openai_visual_only',
        'banner_engine', 'openai_visual_local_text_v1', 'banner_provider', 'openai_visual_local_text',
        'NO TEXT, NO LETTERS, NO WORDS, NO NUMBERS'
    )
    for token in required:
        checks.append(check(token in camp, f'campaign_contract:{token}'))
    # The old source ref may remain only in explicit quarantine/cleanup selectors.
    checks.append(check('source_ref="campaign_openai_gpt_image_2"' not in camp,
                        'campaign_never_registers_legacy_source_ref'))
    checks.append(check('openai_visual_local_text' in camp and 'stock_visual_local_text' in camp and '_has_banner' in camp,
                        'campaign_cache_recognizes_safe_provider'))
    checks.append(check('CAMPAIGN_BANNER_CHILD_TARIFF_EXACTLY_ONCE_V1' in camp and
                        '_billing_consume_child(_account_for_billing,"banners",1,idempotency_key=_child_tariff_key)' in camp and
                        'Tariff units were settled per item before any campaign mutation' in camp,
                        'billing_only_counts_newly_generated_banners'))
    checks.append(check('billing_pending_banners' in camp and 'billing_pending_listings' in camp and 'if _paid_count > _remaining' in camp,
                        'campaign_billing_pending_is_recoverable_and_free_replays_not_blocked'))
    checks.append(check('CAMPAIGN_BANNER_CHILD_TARIFF_EXACTLY_ONCE_V1' in camp and 'campaign-banner-item:{campaign_id}:{_row_id}:{_artifact_op}' in camp and 'Tariff units were settled per item before any campaign mutation' in camp,
                        'campaign_banner_tariff_settles_per_item_before_media_mutation'))
    checks.append(check('CAMPAIGN_LISTING_TARIFF_BEFORE_COMMIT_V1' in camp and 'created_refs.append(ref)' in camp and 'sorted(created_refs)' in camp and 'listing precommit consume failed' in camp,
                        'campaign_listing_tariff_settles_before_durable_row_commit'))
    billing=(ROOT/'app/api/billing.py').read_text(encoding='utf-8')
    checks.append(check('BILLING_USAGE_IDEMPOTENCY_V1' in billing and 'boris_billing_usage_intents' in billing and 'idempotency_replay' in billing,
                        'tariff_usage_has_durable_idempotency_ledger'))
    checks.append(check('BILLING_USAGE_IDEMPOTENCY_SELF_HEAL_V1' in billing and 'CREATE TABLE IF NOT EXISTS boris_billing_usage_intents' in billing,
                        'tariff_usage_idempotency_schema_self_heals'))
    checks.append(check('billing_pending_banners_key' in camp and 'idempotency_key=_pending_banner_billing_key' in camp,
                        'campaign_pending_banner_charge_replays_same_economic_intent'))
    checks.append(check('campaign_account_scope_mismatch' in camp and 'campaign_item_selection_invalid' in camp,
                        'campaign_paid_banner_selection_is_single_account_and_complete'))
    checks.append(check('banner preflight failed closed' in camp and 'Платная генерация не запускалась' in camp,
                        'campaign_paid_banner_billing_preflight_fails_closed_before_provider'))

    avito=(ROOT/'app/api/avito.py').read_text(encoding='utf-8')
    checks.append(check('banners preflight failed closed' in avito and '_allowed_new = 0' in avito and 'billing_pending' in avito[avito.find('def apply_banner_to_batch_impl'):avito.find('class ApplyBannerToBatchRequest')],
                        'batch_paid_banner_billing_preflight_fails_closed_but_replays_survive'))

    registry=(ROOT/'app/ext_api/registry.py').read_text(encoding='utf-8')
    checks.append(check('app.api.banners:full_ai' not in registry and 'app.services.banner_service:generate_banner' not in registry and
                        'executors=["app.api.avito:generate_banner"]' in registry,
                        'external_api_banner_registry_uses_only_safe_executor'))

    checks.append(check('"fullai_"' in avito and '"gptimg_"' in avito and 'campaign_openai_gpt_image_2' in avito,
                        'avito_storage_and_xml_guards_present'))
    image_service=(ROOT/'app/services/image_service.py').read_text(encoding='utf-8')
    checks.append(check('def _valid_image_artifact' in image_service and 'PAID_IMAGE_ARTIFACT_INVALID' in image_service and image_service.count('_valid_image_artifact(output_path)') >= 4,
                        'paid_image_cache_requires_decodable_artifact'))
    media=(ROOT/'app/services/media_service.py').read_text(encoding='utf-8')
    checks.append(check('LEGACY_AVITO_VISUAL_SOURCE_REFS' in media and 'campaign_openai_gpt_image_2' in media,
                        'media_link_guard_present'))
    checks.append(check('LEGACY_AVITO_TIMESTAMP_BANNER_RE' in media and 'source-laundered legacy Avito visual is forbidden' in media,
                        'media_service_blocks_timestamp_and_source_laundering'))
    banners=(ROOT/'app/api/banners.py').read_text(encoding='utf-8')
    checks.append(check('def create_avito_safe_banner' in banners and 'compose_banner as _compose_banner' in banners and 'v4_local_text' in banners,
                        'canonical_safe_banner_uses_local_compositor'))
    checks.append(check('legacy_text_in_image_forbidden' in banners and 'safe_endpoint' in banners,
                        'legacy_full_ai_non_template_disabled'))
    fullai_start=banners.find('def create_full_ai_banner(req: FullAiRequest):')
    fullai_end=banners.find('@router.delete', fullai_start)
    fullai_block=banners[fullai_start:fullai_end] if fullai_start>=0 and fullai_end>fullai_start else ''
    unsafe_prompt_tokens=('write exactly this Russian text','Below it, smaller, write this Russian subtitle','CRITICAL TEXT RULES: Every word')
    checks.append(check(not any(t in fullai_block for t in unsafe_prompt_tokens),
                        'legacy_full_ai_dead_ai_text_code_physically_removed'))
    guard_pos=fullai_block.find('if not req.use_template_library:')
    idem_pos=fullai_block.find('if not _idem:')
    checks.append(check(guard_pos>=0 and idem_pos>=0 and guard_pos < idem_pos,
                        'legacy_full_ai_rejected_before_paid_idempotency_logic', {'guard_pos':guard_pos,'idem_pos':idem_pos}))
    bg=(ROOT/'banner_generator.py').read_text(encoding='utf-8')
    checks.append(check(bg.count('ABSOLUTELY NO TEXT, NO LETTERS, NO WORDS, NO NUMBERS')>=3,
                        'all_legacy_local_banner_ai_backgrounds_are_visual_only'))
    fullai_start=banners.find('def create_full_ai_banner')
    fullai_end=banners.find('@router.delete("/delete")', fullai_start)
    fullai_block=banners[fullai_start:fullai_end] if fullai_start>=0 and fullai_end>fullai_start else ''
    checks.append(check('write exactly this Russian text' not in fullai_block and 'CRITICAL TEXT RULES' not in fullai_block,
                        'legacy_full_ai_dead_visible_text_code_removed'))
    checks.append(check('def restore(db, asset, user):' in media and 'Restore is another activation path' in media and
                        'getattr(asset, "sha256", None)' in media,
                        'media_restore_rechecks_visual_provenance'))
    checks.append(check('while _attempts < 3' not in avito and 'if "429" in str(e)' not in avito,
        'paid_avito_image_caller_has_no_automatic_retry'))
    checks.append(check('from app.services.banner_design_v4 import compose_banner as _compose_banner' not in banners and 'compose_banner_v4 as _compose_banner' in banners and 'compose_banner_v4 as _compose_v4' in banners,
        'safe_banner_helpers_import_real_v4_compositor'))
    frontend_dashboard=Path('/root/BORIS/frontend/app/dashboard/page.tsx').read_text(errors='ignore') if Path('/root/BORIS/frontend/app/dashboard/page.tsx').exists() else ''
    frontend_campaign=Path('/root/BORIS/frontend/app/dashboard/campaign/banners/page.tsx').read_text(errors='ignore') if Path('/root/BORIS/frontend/app/dashboard/campaign/banners/page.tsx').exists() else ''
    checks.append(check('/api/banners/v2/create-one' in frontend_dashboard and '/api/banners/v4/design-one' in frontend_campaign and '/api/banners/v2/design-one' not in frontend_dashboard+frontend_campaign,
        'active_frontend_uses_safe_banner_routes'))
    checks.append(check('draftBatchBannerKeyRef' in frontend_dashboard and 'pipelineBatchBannerKeyRef' in frontend_dashboard and frontend_dashboard.count('idempotency_key: draftBatchBannerKeyRef.current') >= 1 and frontend_dashboard.count('idempotency_key: pipelineBatchBannerKeyRef.current') >= 1,
        'active_batch_banner_ui_sends_stable_idempotency_keys'))
    _v2_create_block=banners[banners.find('@router.post("/v2/create-one")'):banners.find('class BannerV2CopyRequest')]
    _v4_design_block=banners[banners.find('@router.post("/v4/design-one")'):banners.find('# ============================================================================', banners.find('@router.post("/v4/design-one")'))]
    checks.append(check('idempotency_key: str = ""' in banners[banners.find('class BannerV2CreateOneRequest'):banners.find('@router.post("/v2/create-one")')] and 'paid_idempotency_required' in _v2_create_block and 'idempotency_key=idem' in _v2_create_block,
        'v2_interactive_paid_banner_requires_and_forwards_idempotency'))
    checks.append(check('idempotency_key: bannerGenerationKeyRef.current' in frontend_dashboard,
        'dashboard_banner_ui_sends_stable_idempotency_key'))
    checks.append(check('banner_v4_avito_idem_' in banners and 'idempotency_replay' in banners and banners.find('idempotency_replay') < banners.find('copy = _generate_marketing_copy', banners.find('def create_avito_safe_banner')),
        'canonical_safe_banner_replay_returns_before_paid_copy_or_image'))
    checks.append(check('banner_idempotency' in banners and '_fcntl.flock' in banners and 'idempotency_conflict' in banners and 'request_hash' in banners,
        'canonical_banner_intent_has_cross_process_lock_and_payload_conflict_guard'))
    checks.append(check('_intent_record.get("copy")' in banners and '"completed_at": _time.time()' in banners and '"storage_key": str(media_result["storage_key"])' in banners,
        'idempotent_banner_replay_preserves_original_copy_and_media_metadata'))
    checks.append(check('idempotency_key: str = ""' in banners[banners.find('class BannerV4DesignRequest'):banners.find('@router.post("/v4/copy")')] and 'paid_idempotency_required' in _v4_design_block and 'design_v4_idem_' in _v4_design_block and 'idempotency_replay' in _v4_design_block,
        'v4_interactive_design_requires_deterministic_idempotency'))
    checks.append(check('useRef' in frontend_campaign and 'idempotency_key:' in frontend_campaign and 'bannerGenerationKeyRef.current' in frontend_campaign and 'serverAnswered' in frontend_campaign,
        'v4_banner_ui_preserves_key_across_ambiguous_network_failure'))
    _v4_create_block=banners[banners.find('@router.post("/v4/create-one")'):]
    checks.append(check('idempotency_key: str = ""' in banners[banners.find('class BannerV4CreateRequest'):banners.find('_BANNER_V4_DIRECTIONS')] and 'paid_idempotency_required' in _v4_create_block and 'create_avito_safe_banner(' in _v4_create_block and 'idempotency_key=idem' in _v4_create_block,
        'v4_stage_e_reuses_canonical_idempotent_safe_banner_helper'))
    plan_items=(ROOT/'app/api/plan_items.py').read_text(encoding='utf-8')
    tasks=(ROOT/'app/api/tasks.py').read_text(encoding='utf-8')
    apply_block=avito[avito.find('def apply_banner_to_batch_impl'):avito.find('def _normalize_category', avito.find('def apply_banner_to_batch_impl'))]
    checks.append(check('idempotency_key: str = ""' in apply_block and 'newly_generated' in apply_block and 'banners_replayed' in apply_block and 'BILLING_CHILD_INTENT_EXACTLY_ONCE_V1' in apply_block and ':billing:banner:' in apply_block,
        'batch_banner_replay_does_not_double_consume_quota'))
    checks.append(check('_billing_pending' in apply_block and 'if _billing_pending:' in apply_block and 'BORIS не изменил черновики' in apply_block,
        'batch_banner_never_mutates_drafts_before_child_billing_settles'))
    checks.append(check('if not _batch_idem:' in apply_block and 'paid_idempotency_required' in apply_block,
        'batch_banner_impl_itself_requires_business_idempotency'))
    checks.append(check('avito_safe_banner_replay_available' in banners and '_replay_indices' in apply_block and '_new_candidates' in apply_block and '_selected_indices' in apply_block and '_allowed_new = min(_allowed_new, rem_banners)' in apply_block,
        'batch_quota_preflight_counts_only_missing_child_intents'))
    checks.append(check('idempotency_key=f"plan:{item.id}:{_step_index}:generate_banner"' in plan_items and 'publish_listings_banner' in plan_items and 'apply_banner_to_batch' in plan_items and 'edit_active_banner' in plan_items,
        'plan_item_banner_actions_have_stable_internal_intents'))
    checks.append(check('plan-listings:{item.id}:{_step_index}:{batch_id}:{len(candidate_drafts)}' in plan_items and 'idempotency_key=_listing_intent_key' in plan_items,
        'plan_item_listing_tariff_has_stable_business_intent'))
    checks.append(check('PLAN_DRAFT_APPEND_ATOMIC_V2' in plan_items and '_append_plan_drafts(item.account_id, new_drafts)' in plan_items and 'save_resp = _httpx.post("http://127.0.0.1:8000/api/avito/drafts"' not in plan_items,
        'plan_item_listing_save_avoids_self_http_after_tariff_charge'))
    checks.append(check('DRAFT_STORAGE_ATOMIC_MUTATION_V1' in avito and 'pg_advisory_xact_lock' in avito and 'DRAFT_PUBLISH_REMOVE_ATOMIC_V1' in avito and 'DRAFT_BATCH_BANNER_ATOMIC_MERGE_V1' in avito,
        'draft_storage_mutations_are_account_atomic'))
    contract=(ROOT/'production_contract_runner.py').read_text(encoding='utf-8')
    checks.append(check('CONTRACT_SOURCE_STABILITY_V1' in contract and 'CONTRACT_SOURCE_STABILITY_POSTSTAMP_V1' in contract and 'db.rollback()' in contract and '"status": "converging"' in contract, 'contract_source_generation_is_transaction_stable'))
    checks.append(check('FEED_STORAGE_ATOMIC_MUTATION_V1' in avito and '_upsert_feed_items' in avito and 'FEED_PUBLISH_UPSERT_ATOMIC_V1' in avito and 'FEED_ITEM_EDIT_ATOMIC_V1' in avito,
        'feed_storage_mutations_are_account_atomic'))
    checks.append(check('CAMPAIGN_ACTIVATION_FEED_ATOMIC_V1' in camp and '_upsert_feed_items(target,campaign_items)' in camp,
        'campaign_activation_upserts_only_its_feed_scope'))
    checks.append(check('AUTO_DUPLICATE_FREE_PREVIEW_V1' in avito and 'AUTO_DUPLICATE_LISTING_TARIFF_EXACTLY_ONCE_V1' in avito and 'AUTO_DUPLICATE_MANIFEST_EXACTLY_ONCE_V1' in avito,
        'auto_duplicate_is_free_preview_and_exactly_once_mutation'))
    home_src=(ROOT/'app/api/home.py').read_text(encoding='utf-8')
    checks.append(check('SCENARIO_DUPLICATE_RUN_IDEMPOTENCY_V1' in home_src and 'idempotency_key=(str(_policy.get("approval_fingerprint") or _execution_fp)' in home_src,
        'scenario_duplicate_run_reuses_approval_business_intent'))
    checks.append(check('PIPELINE_TEXT_ECONOMIC_INTENT_V1' in tasks and 'PIPELINE_DRAFT_TARIFF_EXACTLY_ONCE_V1' in tasks and '_append_drafts(account_id, charged_drafts)' in tasks,
        'pipeline_drafts_are_idempotent_billed_and_atomic'))
    parser_src=(ROOT/'app/api/parser.py').read_text(encoding='utf-8')
    checks.append(check('PARSED_DRAFT_TARIFF_EXACTLY_ONCE_V1' in parser_src and 'PARSED_DRAFT_APPEND_ATOMIC_V1' in parser_src and 'parsed-draft:{req.account_id}:{_did}' in parser_src,
        'parsed_product_drafts_are_idempotent_billed_and_atomic'))
    checks.append(check('task-product-banner:' in tasks and 'pipeline-banners:' in tasks and '_run_product_banner(payload, task=task)' in tasks,
        'task_banner_actions_have_stable_internal_intents'))
    gen_block2=avito[avito.find('def generate_banner(req: GenerateBannerRequest):'):avito.find('class DownloadImageRequest', avito.find('def generate_banner(req: GenerateBannerRequest):'))]
    checks.append(check('if not _idem:' in gen_block2 and 'idempotency_key=(_idem + ":ad-banner:" + str(i))' in gen_block2,
        'interactive_generate_banner_requires_idempotency_for_single_paid_image_too'))
    _v2_copy_block=banners[banners.find('@router.post("/v2/copy")'):banners.find('@router.post("/v2/design-one")')]
    _v2_design_block=banners[banners.find('@router.post("/v2/design-one")'):banners.find('class BannerV3CopyRequest')]
    _v3_copy_block=banners[banners.find('@router.post("/v3/copy")'):banners.find('@router.post("/v3/design-one")')]
    _v3_design_block=banners[banners.find('@router.post("/v3/design-one")'):banners.find('# ===== BORIS BANNER V4 STAGE E =====')]
    checks.append(check(all('legacy_banner_stage_retired' in block for block in (_v2_copy_block,_v2_design_block,_v3_copy_block,_v3_design_block)),
        'inactive_v2_v3_paid_banner_stages_are_retired'))
    quarantine_script=Path('/root/BORIS/backend/scripts/quarantine_legacy_visual_files.py').read_text(errors='ignore')
    watchdog=Path('/root/BORIS/backend/scripts/avito_visual_guard_watchdog.sh').read_text(errors='ignore')
    checks.append(check("MARKERS = ('fullai_', 'gptimg_')" in quarantine_script and 'os.replace(real, target)' in quarantine_script and 'manifest.json' in quarantine_script,
        'filesystem_legacy_visual_quarantine_preserves_evidence'))
    checks.append(check('quarantine_legacy_visual_files.py' in watchdog and watchdog.find('quarantine_legacy_visual_files.py') < watchdog.find('avito_media_quality_audit.py'),
        'watchdog_self_heals_legacy_files_before_audit'))
    media_audit=Path('/root/BORIS/backend/scripts/avito_media_quality_audit.py').read_text(errors='ignore')
    checks.append(check('campaign_feed_drift' in media_audit and 'published_identity_bound' in media_audit and 'campaign_images[0] != feed_images[0]' in media_audit,
        'media_watchdog_detects_published_campaign_feed_image_drift'))
    checks.append(check('missing_published_feed_identity' in media_audit and 'published_identity_bound_missing_from_feed_items' in media_audit,
        'media_watchdog_detects_missing_published_feed_identity'))
    checks.append(check('missing_published_identity_contract' in watchdog and watchdog.find('missing_published_identity_contract') < watchdog.find('run_step media_audit'),
        'watchdog_tests_missing_published_identity_before_media_audit'))
    checks.append(check('feed_lifecycle_contract' in watchdog and 'feed_lifecycle_guard env' in watchdog and watchdog.find('feed_lifecycle_guard env') < watchdog.find('run_step media_audit'),
        'watchdog_tracks_feed_lifecycle_before_media_audit'))
    _hero_contract_pos=watchdog.find('run_step hero_feed_selfheal_contract ')
    _hero_repair_pos=watchdog.find('run_step hero_feed_selfheal env ')
    _media_audit_pos=watchdog.find('run_step media_audit env ')
    checks.append(check(min(_hero_contract_pos,_hero_repair_pos,_media_audit_pos) >= 0 and _hero_contract_pos < _hero_repair_pos < _media_audit_pos,
        'watchdog_selfheal_contract_and_repair_precede_media_audit'))
    checks.append(check('uniquify_content_identity' in watchdog and watchdog.find('uniquify_content_identity') < watchdog.find('run_step media_audit'),
        'watchdog_xml_content_identity_contract_precedes_media_audit'))
    checks.append(check('def create_infographic(req: InfographicRequest, current_user=_DepSec(_CurUser))' in banners and 'account_id = _banner_v2_account_access(req.account_id, current_user)' in banners,
        'active_own_photo_banner_route_requires_account_access'))
    _infographic_block=banners[banners.find('@router.post("/infographic")'):banners.find('@router.post("/diagonal")')]
    checks.append(check('own_photo_path=own_photo_path, account_id=account_id' in _infographic_block and 'own_photo_path=None' not in _infographic_block,
        'own_photo_banner_actually_uses_selected_photo'))
    checks.append(check('_candidate.relative_to(_root / account_id)' in banners and 'Выбранное фото недоступно в медиатеке этого аккаунта' in banners,
        'own_photo_banner_blocks_cross_account_path_traversal'))
    checks.append(check('split()[:6]' in _infographic_block and 'split()[:8]' in _infographic_block and 'max(52, int(size * 0.048))' in Path('/root/BORIS/backend/banner_generator.py').read_text(errors='ignore'),
        'own_photo_banner_enforces_mobile_copy_and_font_floor'))
    checks.append(check('own_photo_local_text_v1' in _infographic_block and '_canonical_url = "/images/" + str(_media["storage_key"])' in _infographic_block and '"media_id": _media["id"]' in _infographic_block,
        'own_photo_banner_registers_canonical_media'))
    checks.append(check('and not req.own_photo_url' in _infographic_block and _infographic_block.find('and not req.own_photo_url') < _infographic_block.find('_generate_marketing_copy'),
        'own_photo_mode_skips_hidden_marketing_copy_provider_call'))
    checks.append(check('legacy_infographic_ai_mode_retired' in _infographic_block and '/api/banners/v2/create-one' in _infographic_block and _infographic_block.find('legacy_infographic_ai_mode_retired') < _infographic_block.find('_generate_marketing_copy'),
        'legacy_infographic_ai_background_mode_retired_before_paid_logic'))
    _diagonal_block=banners[banners.find('@router.post("/diagonal")'):banners.find('@router.post("/carousel")')]
    _carousel_block=banners[banners.find('@router.post("/carousel")'):banners.find('class FullAiRequest')]
    checks.append(check('legacy_banner_layout_retired' in _diagonal_block and '/api/banners/v4/design-one' in _diagonal_block and _diagonal_block.find('legacy_banner_layout_retired') < _diagonal_block.find('_generate_diagonal_slide'),
        'legacy_diagonal_paid_layout_retired_before_provider_logic'))
    checks.append(check('legacy_banner_layout_retired' in _carousel_block and '/api/banners/v4/design-one' in _carousel_block and _carousel_block.find('legacy_banner_layout_retired') < _carousel_block.find('generate_carousel_banner_v2'),
        'legacy_carousel_paid_layout_retired_before_provider_logic'))
    main_py=Path('/root/BORIS/backend/app/main.py').read_text(errors='ignore')
    nginx_live=Path('/etc/nginx/sites-enabled/boris-ai').read_text(errors='ignore') if Path('/etc/nginx/sites-enabled/boris-ai').exists() else ''
    rolling=Path('/root/BORIS/backend/rolling_restart_backend.sh').read_text(errors='ignore')
    checks.append(check('no-store, no-cache, must-revalidate, max-age=0' in main_py and 'location = /health' in nginx_live and 'no-store, no-cache, must-revalidate, max-age=0' in nginx_live,
        'public_health_is_explicitly_non_cacheable'))
    checks.append(check('boris-backend-rolling.lock' in rolling and 'flock -n 9' in rolling and 'kpi_goal_runner.lock' in rolling,
        'rolling_restart_has_deploy_and_kpi_locks'))
    checks.append(check('func.lower(func.coalesce(MediaAsset.source_provider, ""))' in media,
                        'media_provider_guard_case_insensitive'))
    checks.append(check('source_provider="openai_visual_local_text"' in banners and 'canonical_url = "/images/" + str(media_result["storage_key"])' in banners,
                        'safe_banner_returns_canonical_media_url'))
    registry=(ROOT/'app/ext_api/registry.py').read_text(encoding='utf-8')
    checks.append(check('app.api.banners:full_ai' not in registry and 'executors=["app.api.avito:generate_banner"]' in registry,
                        'external_banner_action_has_single_safe_executor'))

    avito_code=(ROOT/'app/api/avito.py').read_text(encoding='utf-8')
    gen_start=avito_code.find('def generate_banner(req: GenerateBannerRequest):')
    gen_end=avito_code.find('class DownloadImageRequest', gen_start)
    gen_block=avito_code[gen_start:gen_end] if gen_start>=0 and gen_end>gen_start else ''
    checks.append(check('create_avito_safe_banner' in gen_block and 'Global Avito invariant' in gen_block,
                        'avito_generate_banner_routes_ad_styles_to_safe_compositor'))
    checks.append(check('write exactly this Russian text' not in gen_block and 'крупный главный оффер' not in gen_block,
                        'avito_generate_banner_has_no_ai_visible_text_prompt'))

    # Deterministic compositor smoke test: no network, no paid calls.
    with tempfile.TemporaryDirectory(prefix='boris_avito_visual_') as td:
        src=Path(td)/'source.png'; out=Path(td)/'banner.png'
        Image.new('RGB',(1024,1024),(120,130,140)).save(src,'PNG')
        res=compose_premium_avito_banner(str(src),str(out),'Шкафы купе на заказ')
        sig=out.read_bytes()[:8] if out.exists() else b''
        checks.append(check(res.get('ok') and out.exists() and sig==b'\x89PNG\r\n\x1a\n',
                            'compositor_emits_real_png', {'result':res,'signature':sig.hex()}))
        checks.append(check(int(res.get('font_size') or 0)>=42 and 1<=int(res.get('line_count') or 0)<=4,
                            'mobile_text_geometry', res))
        checks.append(check(res.get('text_renderer')=='local_pillow', 'local_text_renderer', res))

    # Completed idempotent replay must return before both paid copy and paid image
    # providers. The test pre-creates the deterministic final PNG and replaces all
    # provider-capable functions with crash sentinels; any accidental call fails.
    _idem_out=None
    _orig_copy=_orig_register=_orig_compose=None
    try:
        import hashlib as _h
        import app.api.banners as _banner_api
        import app.services.banner_media as _banner_media
        import app.services.banner_design_v4 as _banner_v4
        _aid='qa_visual_idempotency_replay'
        _idem='visual-contract-replay-v1'
        _intent=_h.sha256((_aid+'|'+_idem).encode('utf-8')).hexdigest()[:24]
        _idem_out=ROOT/'images'/ _aid / 'banners' / 'v4' / f'banner_v4_avito_idem_{_intent}_architecture.png'
        _idem_out.parent.mkdir(parents=True,exist_ok=True)
        Image.effect_noise((256,256),64).convert('RGB').save(_idem_out,'PNG')
        _orig_copy=_banner_api._generate_marketing_copy
        _orig_register=_banner_media.register_banner
        _orig_compose=_banner_v4.compose_banner_v4
        def _must_not_call(*args,**kwargs):
            raise AssertionError('paid/provider-capable path called during idempotency replay')
        _banner_api._generate_marketing_copy=_must_not_call
        _banner_v4.compose_banner_v4=_must_not_call
        _banner_media.register_banner=lambda **kwargs:{'id':999999,'storage_key':f'{_aid}/media/idempotent-replay.png','created':False}
        _replay=_banner_api.create_avito_safe_banner(
            account_id=_aid, description='Тест безопасного повтора', direction='architecture',
            exact_title='Безопасный повтор', idempotency_key=_idem,
        )
        checks.append(check(_replay.get('status')=='ok' and _replay.get('idempotency_replay') is True,
                            'idempotent_banner_replay_makes_zero_paid_provider_calls',_replay))
    except Exception as exc:
        checks.append(check(False,'idempotent_banner_replay_makes_zero_paid_provider_calls',repr(exc)))
    finally:
        try:
            if _orig_copy is not None: _banner_api._generate_marketing_copy=_orig_copy
            if _orig_register is not None: _banner_media.register_banner=_orig_register
            if _orig_compose is not None: _banner_v4.compose_banner_v4=_orig_compose
        except Exception:
            pass
        try:
            if _idem_out and _idem_out.exists(): _idem_out.unlink()
        except Exception:
            pass

    db=SessionLocal()
    try:
        q={
            'active_legacy_provider_assets': "select count(*) from media_assets where deleted_at is null and lower(coalesce(source_provider,'')) in ('boris_deterministic','boris_unique')",
            'active_legacy_campaign_assets': "select count(*) from media_assets where deleted_at is null and coalesce(source_ref,'')='campaign_openai_gpt_image_2'",
            'active_legacy_campaign_links': "select count(*) from media_links ml join media_assets ma on ma.id=ml.media_id where ma.deleted_at is null and coalesce(ma.source_ref,'')='campaign_openai_gpt_image_2'",
        }
        for name,sql in q.items():
            n=int(db.execute(text(sql)).scalar() or 0)
            checks.append(check(n==0, name, n))
        n=int(db.execute(text("select count(distinct a.id) from media_assets a join media_assets h on h.account_id=a.account_id and h.storage_key=a.storage_key and h.id<>a.id where a.deleted_at is null and (lower(coalesce(h.source_provider,'')) in ('boris_deterministic','boris_unique') or coalesce(h.source_ref,'')='campaign_openai_gpt_image_2')")).scalar() or 0)
        checks.append(check(n==0,'active_source_laundering_zero',n))
        n=int(db.execute(text("select count(*) from media_assets where deleted_at is null and storage_key ~ 'banners/campaign_openai/campaign_[0-9]+_item_[0-9]+_[0-9]{10,}\\.png$'")).scalar() or 0)
        checks.append(check(n==0,'legacy_timestamp_active_banners_zero',n))
        trigger_names={r[0] for r in db.execute(text("select tgname from pg_trigger where not tgisinternal and tgname like 'trg_boris_guard_%'")).fetchall()}
        needed={'trg_boris_guard_canonical_avito_storage','trg_boris_guard_legacy_avito_media_asset','trg_boris_guard_legacy_avito_media_link','trg_boris_guard_legacy_banner_row'}
        checks.append(check(needed.issubset(trigger_names),'db_last_line_guards_present',sorted(trigger_names)))

        # Runtime trigger probes are rolled back via SAVEPOINT; they make zero persistent mutations.
        def expect_db_block(name, sql, params):
            blocked=False; detail=''
            sp=db.begin_nested()
            try:
                db.execute(text(sql),params)
                db.flush()
            except Exception as exc:
                blocked=True; detail=str(exc).split('\n')[0][:300]
                sp.rollback()
            else:
                sp.rollback()
            checks.append(check(blocked,name,detail or 'unexpectedly allowed'))

        safe_asset=db.execute(text("select id from media_assets where deleted_at is null and coalesce(source_ref,'')<>'campaign_openai_gpt_image_2' and lower(coalesce(source_provider,'')) not in ('boris_deterministic','boris_unique') order by id desc limit 1")).first()
        if safe_asset:
            expect_db_block('db_trigger_blocks_legacy_asset_activation',
                "update media_assets set source_ref='campaign_openai_gpt_image_2' where id=:i", {'i':int(safe_asset.id)})
        quarantined=db.execute(text("select id from media_assets where deleted_at is not null and (coalesce(source_ref,'')='campaign_openai_gpt_image_2' or lower(coalesce(source_provider,'')) in ('boris_deterministic','boris_unique')) order by id desc limit 1")).first()
        if quarantined:
            expect_db_block('db_trigger_blocks_link_to_quarantine',
                "insert into media_links(media_id,entity_type,entity_id,role,position) values(:m,'contract_probe','0','gallery',0)", {'m':int(quarantined.id)})
        expect_db_block('db_trigger_blocks_legacy_timestamp_in_canonical_storage',
            "insert into storage(account_id,key,value) values('contract_probe','feed_items','[{\"id\":\"x\",\"images\":[\"/images/contract_probe/banners/campaign_openai/campaign_1_item_2_1788302097239.png\"]}]')", {})
        expect_db_block('db_trigger_blocks_fullai_operational_storage',
            "insert into storage(account_id,key,value) values('contract_probe','banner_prompt_guide','{\"url\":\"/images/x/fullai_bad.png\"}')", {})
        expect_db_block('db_trigger_blocks_gptimg_operational_storage',
            "insert into storage(account_id,key,value) values('contract_probe','feed_prefs','{\"url\":\"/images/x/gptimg_bad.png\"}')", {})
        expect_db_block('db_trigger_blocks_legacy_banner_table_row',
            "insert into banners(account_id,filename,folder,source,format) values('contract_probe','fullai_probe.png','infographic','full_ai','infographic')", {})
        expect_db_block('db_trigger_blocks_gptimg_banner_filename',
            "insert into banners(account_id,filename,folder,source,format) values('contract_probe','gptimg_probe.png','infographic','template','infographic')", {})
        expect_db_block('db_trigger_blocks_gptimg_media_asset',
            "insert into media_assets(owner_user_id,account_id,storage_key,filename,media_type,source_type,status,file_state,created_by) values(1,'contract_probe','contract_probe/banners/gptimg_probe.png','gptimg_probe.png','banner','generated','approved','present',1)", {})

        # Canonical DB must not contain the old filename class or resolve to old campaign assets.
        rows=db.execute(text("select account_id,key,value from storage where key in ('feed_items','drafts')")).all()
        fullai=0; oldref=0; malformed=0
        from urllib.parse import unquote
        for account,key,value in rows:
            try: items=json.loads(value or '[]')
            except Exception: malformed+=1; continue
            if not isinstance(items,list): malformed+=1; continue
            for item in items:
                if not isinstance(item,dict): continue
                for raw in item.get('images') or []:
                    url=str(raw or ''); clean=url.split('?',1)[0]
                    if any(m in clean.rsplit('/',1)[-1].lower() for m in ('fullai_','gptimg_')): fullai+=1
                    import re as _re
                    if _re.search(r'/banners/campaign_openai/campaign_\d+_item_\d+_\d{10,}\.png$', clean): oldref+=1
                    if '/images/' in url:
                        rel=unquote(url.split('/images/',1)[1].split('?',1)[0])
                        n=int(db.execute(text("select count(*) from media_assets ma where account_id=:a and storage_key=:k and (coalesce(source_ref,'')='campaign_openai_gpt_image_2' or lower(coalesce(source_provider,'')) in ('boris_deterministic','boris_unique') or exists (select 1 from media_assets h where h.account_id=ma.account_id and h.storage_key=ma.storage_key and h.id<>ma.id and (coalesce(h.source_ref,'')='campaign_openai_gpt_image_2' or lower(coalesce(h.source_provider,'')) in ('boris_deterministic','boris_unique'))))"),{'a':account,'k':rel}).scalar() or 0)
                        oldref+=n
        checks.append(check(fullai==0,'canonical_fullai_zero',fullai))
        checks.append(check(oldref==0,'canonical_legacy_campaign_ref_zero',oldref))
        checks.append(check(malformed==0,'canonical_storage_json_valid',malformed))
    finally:
        db.close()

    failed=[c for c in checks if not c['ok']]
    report={'status':'PASS' if not failed else 'FAIL','checks':len(checks),'failures':failed}
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if not failed else 2

if __name__=='__main__':
    raise SystemExit(main())
