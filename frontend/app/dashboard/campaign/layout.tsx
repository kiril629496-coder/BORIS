"use client";

import { Shell } from "@/app/components/shell/LegacyShellAdapter";

export default function CampaignLayout({ children }: { children: React.ReactNode }) {
  return (
    <Shell activeKey="listings">
      {/* Keep the sidebar key as "listings", but expose the canonical campaign
          scope so campaign-specific responsive/product styles actually apply. */}
      <div data-boris-area="campaign" style={{ minWidth: 0, width: "100%" }}>
        {children}
      </div>
    </Shell>
  );
}
