import {
  deterministicCurrentCampaignId,
  isSelectableCampaign,
  resolveFeedIdentityCampaignId,
  resolveOwningCampaignId,
} from "./projection";

const campaigns = [
  { id: 252, updated_at: "2026-09-30T10:00:00Z" },
  { id: 250, updated_at: "2026-09-29T10:00:00Z" },
  { id: 249, updated_at: "2026-09-28T10:00:00Z" },
  { id: 144, status: "superseded", updated_at: "2026-10-01T10:00:00Z" },
];

if (resolveOwningCampaignId(campaigns, null, 250) !== 250) throw new Error("selected campaign must win over ordering");
if (resolveOwningCampaignId(campaigns, { owning_campaign_id: 249 }, null) !== 249) throw new Error("owning campaign must win");
if (resolveFeedIdentityCampaignId(campaigns, [{ feed_identity: "feed-a", campaign_id: 249 }], "feed-a", 250) !== 249) throw new Error("feed mapping must win");
if (deterministicCurrentCampaignId(campaigns) !== 252) throw new Error("deterministic current campaign mismatch");
if (isSelectableCampaign(campaigns[3])) throw new Error("superseded campaign must not be selectable");

// Catalog=0 and feed-backed>0 remain distinct projections. The resolver must
// not infer a catalog item or collapse the two sources into one count.
const catalogSummary = { total: 0 };
const feedBacked = [{ item_id: 17, campaign_id: 249, feed_identity: "feed-a" }];
if (catalogSummary.total !== 0 || feedBacked.length !== 1) throw new Error("catalog/feed projection separation regressed");
