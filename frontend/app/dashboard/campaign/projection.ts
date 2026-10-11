export type CampaignProjection = {
  id?: number | string;
  status?: string;
  current?: boolean;
  is_current?: boolean;
  updated_at?: string;
};

export type FeedProjection = {
  campaign_id?: number | string;
  owning_campaign_id?: number | string;
  item_id?: number | string;
  feed_identity?: string;
  campaignId?: number | string;
  owningCampaignId?: number | string;
  itemId?: number | string;
  feedIdentity?: string;
};

function positiveId(value: unknown): number {
  const id = Number(value);
  return Number.isSafeInteger(id) && id > 0 ? id : 0;
}

export function isSelectableCampaign(campaign: CampaignProjection | null | undefined): boolean {
  return Boolean(campaign && positiveId(campaign.id) && String(campaign.status || "").toLowerCase() !== "superseded");
}

export function deterministicCurrentCampaignId(campaigns: CampaignProjection[]): number {
  const selectable = campaigns.filter(isSelectableCampaign);
  if (!selectable.length) return 0;
  const explicit = selectable.find((campaign) => campaign.current === true || campaign.is_current === true);
  if (explicit) return positiveId(explicit.id);
  const ordered = [...selectable].sort((a, b) => {
    const at = Date.parse(String(a.updated_at || "")) || 0;
    const bt = Date.parse(String(b.updated_at || "")) || 0;
    return bt - at || positiveId(b.id) - positiveId(a.id);
  });
  return positiveId(ordered[0]?.id);
}

export function resolveOwningCampaignId(
  campaigns: CampaignProjection[],
  projection?: FeedProjection | null,
  selectedCampaignId?: number | string | null,
): number {
  const selectableIds = new Set(campaigns.filter(isSelectableCampaign).map((campaign) => positiveId(campaign.id)));
  const selected = positiveId(selectedCampaignId);
  if (selected && selectableIds.has(selected)) return selected;
  const owning = positiveId(projection?.campaign_id ?? projection?.owning_campaign_id ?? projection?.campaignId ?? projection?.owningCampaignId);
  if (owning && selectableIds.has(owning)) return owning;
  const deterministic = deterministicCurrentCampaignId(campaigns);
  return deterministic && selectableIds.has(deterministic) ? deterministic : 0;
}

export function resolveFeedIdentityCampaignId(
  campaigns: CampaignProjection[],
  mappings: FeedProjection[],
  feedIdentity?: string,
  selectedCampaignId?: number | string | null,
): number {
  if (feedIdentity) {
    const mapping = mappings.find((entry) => String(entry.feed_identity ?? entry.feedIdentity ?? "") === String(feedIdentity));
    const mapped = resolveOwningCampaignId(campaigns, mapping, selectedCampaignId);
    if (mapped) return mapped;
  }
  return resolveOwningCampaignId(campaigns, undefined, selectedCampaignId);
}
