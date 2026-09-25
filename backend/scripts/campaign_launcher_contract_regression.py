#!/usr/bin/env python3
from pathlib import Path
import sys

front=Path('/root/BORIS/frontend/app/dashboard/campaign/page.tsx').read_text(encoding='utf-8')
detail=Path('/root/BORIS/frontend/app/dashboard/campaign/[id]/page.tsx').read_text(encoding='utf-8')
layout=Path('/root/BORIS/frontend/app/dashboard/campaign/layout.tsx').read_text(encoding='utf-8')
css=Path('/root/BORIS/frontend/app/globals.css').read_text(encoding='utf-8')
back=Path('/root/BORIS/backend/app/api/campaigns.py').read_text(encoding='utf-8')
checks=[]
def ck(name, ok): checks.append((name,bool(ok)))
ck('backend_context_endpoint', '@router.get("/context")' in back and 'campaign_account_context' in back)
ck('hidden_snapshots_not_active', 'active_visibility = {"active", "visible", "published", "public"}' in back and 'if visibility not in active_visibility' in back)
ck('context_has_feed_fallback', 'feed_listing_titles' in back and 'feed_category_paths' in back)
ck('frontend_uses_selected_account', 'selectedAccount' in front and 'clientPromptExamples(selectedAccount, promptKind, campaignContext)' in front)
ck('frontend_uses_context_endpoint', '/api/campaign/context?account_id=' in front)
ck('launcher_prioritizes_active_feed_profile', 'active_listing_titles' in front and 'feed_listing_titles' in front and 'profileDirs' in front)
ck('launcher_uses_feed_categories', 'feed_category_paths' in front and 'categoryHints' in front)
ck('launcher_truthful_source_labels', 'из активных объявлений' in front and 'из текущего фида' in front and 'из данных компании' in front)
ck('mobile_banner_contract', 'один крупный заголовок максимум 6 слов' in front and 'без мелкого текста' in front)
ck('no_fake_facts_contract', 'Не придумывай цены, гарантии, сроки и характеристики.' in front)
ck('campaign_scope_applied', 'data-boris-area="campaign"' in layout)
ck('campaign_mobile_quantity_wrap', 'data-testid="campaign-quantity-row"' in detail and 'flexWrap:"wrap"' in detail)
ck('campaign_mobile_category_picker', 'data-testid="campaign-category-picker"' in detail and 'boris-intake-category-options' in detail)
ck('campaign_busy_lock_released', 'BORIS_CAMPAIGN_START_BUSY_CLEAR_V1' in detail and 'setFeedBusy("")' in detail)
ck('campaign_mobile_fullscreen_intake', '.boris-intake-dialog' in css and 'height:100dvh!important' in css and '.boris-intake-footer-actions' in css)
failed=[name for name,ok in checks if not ok]
print({'status':'PASS' if not failed else 'FAIL','checks':len(checks),'failures':failed})
sys.exit(0 if not failed else 2)
