import { NextResponse } from "next/server";
import {
  CheckpointBridgeError,
  completeCheckpoint,
  noStoreHeaders,
  requireCheckpointOwner,
} from "@/lib/server/seoCheckpointBridge";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 120;

function text(value: unknown, max: number) {
  const result = typeof value === "string" ? value.trim() : "";
  return result.slice(0, max);
}

export async function POST(request: Request) {
  try {
    await requireCheckpointOwner(request);
    const body = await request.json();
    const entryId = text(body?.entry_id, 300);
    const platform = text(body?.platform, 160);
    const targetSite = text(body?.target_site, 200);
    const pageUrl = text(body?.page_url, 2000);

    if (!entryId || !platform) {
      return NextResponse.json(
        { status: "error", detail: "entry_id and platform are required" },
        { status: 400, headers: noStoreHeaders() }
      );
    }

    const payload = await completeCheckpoint({
      entry_id: entryId,
      platform,
      target_site: targetSite || null,
      page_url: pageUrl || null,
    });
    return NextResponse.json(payload, { headers: noStoreHeaders() });
  } catch (error) {
    const status =
      error instanceof CheckpointBridgeError ? error.status : 500;
    const detail =
      error instanceof Error ? error.message : "Checkpoint completion failed";
    return NextResponse.json(
      { status: "error", detail },
      { status, headers: noStoreHeaders() }
    );
  }
}
