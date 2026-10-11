import { NextResponse } from "next/server";
import {
  CheckpointBridgeError,
  loadCheckpointCenter,
  noStoreHeaders,
  requireCheckpointOwner,
} from "@/lib/server/seoCheckpointBridge";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  try {
    await requireCheckpointOwner(request);
    const payload = await loadCheckpointCenter(false);
    return NextResponse.json(payload, { headers: noStoreHeaders() });
  } catch (error) {
    const status =
      error instanceof CheckpointBridgeError ? error.status : 500;
    const detail =
      error instanceof Error ? error.message : "Checkpoint Center failed";
    return NextResponse.json(
      { status: "error", detail },
      { status, headers: noStoreHeaders() }
    );
  }
}
