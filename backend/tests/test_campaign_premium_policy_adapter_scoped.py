from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")

def test_campaign_full_ai_account_policy_is_fail_closed_and_editable_after_terminal_publish():
    src=_read("app/api/campaigns.py")
    assert 'key=="campaign_ai_policy"' in src
    assert '"banner_engine") or "").strip().lower()=="full_ai_premium"' in src
    assert '_ff_assert_card_editor_mutable(campaign)' in src
    assert '_ff_full_ai_premium_banner_batch' in src
    assert 'stock_fallback":False' in src
    assert 'exact_subtitle=""' in src
    assert 'exact_cta=""' in src
    assert 'banner_headline_map' in src
    assert 'banner_art_directions' in src
    assert 'full_ai_premium_banner_plus_existing_verified_gallery' in src
    assert '_ff_mark_post_publication_revision(ff,row_id,["images","banner"])' in src

def test_campaign_paid_sales_v4_uses_single_openai_lane_and_rejects_local_template_fallback():
    src=_read("app/api/campaigns.py")
    pool=_read("gigachat_pool.py")
    assert '_force_sales_v4=str(_campaign_ai_policy.get("copy_engine") or "").strip().lower()=="paid_sales_v4"' in src
    assert 'single_paid_attempt=True' in src
    assert 'if _force_sales_v4 and isinstance(result,dict) and bool(result.get("local_fallback")):' in src
    assert 'p["copy_provider"]="openai_gpt_5_4_single" if _force_sales_v4 else "paid_single_attempt"' in src
    assert 'if single_paid_attempt:' in pool
    assert 'return _openai_fallback_completion(' in pool

def test_premium_engine_contract_is_gpt_image_2_low_under_four_rubles():
    src=_read("app/services/banner_full_ai_premium.py")
    assert 'MODEL = "gpt-image-2"' in src
    assert 'QUALITY = "low"' in src
    assert 'MAX_COST_RUB = 4.0' in src
    assert 'ENGINE = "full_ai_premium"' in src
    assert 'ENGINE_SOURCE = "july_2026_restored"' in src
    assert '"auto_retry": False' in src
