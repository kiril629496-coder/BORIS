import assert from "node:assert/strict";
import fs from "node:fs";

const source = fs.readFileSync("app/dashboard/campaign/page.tsx", "utf8");

assert.match(source, /accountLoadSequence\s*=\s*useRef/);
assert.match(source, /campaignScopeSequence\s*=\s*useRef/);
assert.match(source, /selectedCampaignRef\s*=\s*useRef/);
assert.match(source, /const load = useCallback\(async \(silent = false\) => \{[\s\S]*?\}, \[account\]\)/);
assert.match(source, /apiGet\(`\/api\/campaign\/\$\{requestedCampaignId\}\/readiness\?account_id=/);
assert.match(source, /apiGet\(`\/api\/campaign\/\$\{requestedCampaignId\}\/ai-marketer\?account_id=/);
assert.match(source, /const scopeRequest = \+\+campaignScopeSequence\.current/);
assert.match(source, /const isCurrentScope = \(\) => selectedCampaignRef\.current === requestedCampaignId/);
assert.match(source, /selectedCampaignRef\.current === requestedCampaignId/);
assert.match(source, /CAMPAIGN_SCOPE_MISMATCH/);
assert.match(source, /setSelectedCampaignId\(requested\)/);
assert.doesNotMatch(source, /\}, \[account, selectedCampaignId\]\);\n\s*useEffect\(\(\) => \{ void load\(\)/);

console.log("CAMPAIGN_LIFECYCLE_REGRESSION=PASS");
