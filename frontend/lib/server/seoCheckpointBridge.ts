import "server-only";

import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

const PYTHON = "/root/BORIS/backend/venv/bin/python";
const BACKEND_CWD = "/root/BORIS/backend";
const AUTH_BASES = [
  "http://127.0.0.1:8000",
  "http://127.0.0.1:8001",
];

export class CheckpointBridgeError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function bearerFrom(request: Request) {
  const value = String(request.headers.get("authorization") || "").trim();
  if (!value.toLowerCase().startsWith("bearer ")) {
    throw new CheckpointBridgeError(401, "Authorization required");
  }
  return value;
}

export async function requireCheckpointOwner(request: Request) {
  const authorization = bearerFrom(request);
  let lastStatus = 503;

  for (const base of AUTH_BASES) {
    try {
      const response = await fetch(base + "/api/auth/me", {
        method: "GET",
        headers: { Authorization: authorization },
        cache: "no-store",
        signal: AbortSignal.timeout(5000),
      });
      lastStatus = response.status;
      if (!response.ok) {
        if (response.status === 401 || response.status === 403) {
          throw new CheckpointBridgeError(response.status, "Authorization denied");
        }
        continue;
      }
      const payload = await response.json();
      const user = payload?.user || {};
      if (String(user.role || "").toLowerCase() !== "owner") {
        throw new CheckpointBridgeError(403, "Owner access required");
      }
      return {
        id: user.id,
        role: user.role,
        email: user.email,
      };
    } catch (error) {
      if (error instanceof CheckpointBridgeError) throw error;
      continue;
    }
  }

  throw new CheckpointBridgeError(
    lastStatus === 401 || lastStatus === 403 ? lastStatus : 503,
    "BORIS auth verification unavailable"
  );
}

function parseBridgeOutput(stdout: string) {
  const prefix = "BORIS_CHECKPOINT_BRIDGE_JSON:";
  const lines = String(stdout || "").split(/\r?\n/);
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    const line = lines[i];
    if (!line.startsWith(prefix)) continue;
    return JSON.parse(line.slice(prefix.length));
  }
  throw new CheckpointBridgeError(500, "Checkpoint bridge output invalid");
}

async function runPython(
  code: string,
  payload: Record<string, unknown> = {},
  timeoutMs = 30000
) {
  try {
    const { stdout } = await execFileAsync(
      PYTHON,
      ["-c", code, JSON.stringify(payload)],
      {
        cwd: BACKEND_CWD,
        env: {
          ...process.env,
          PYTHONPATH: BACKEND_CWD,
        },
        timeout: timeoutMs,
        maxBuffer: 12 * 1024 * 1024,
        windowsHide: true,
      }
    );
    return parseBridgeOutput(stdout);
  } catch (error: any) {
    if (error instanceof CheckpointBridgeError) throw error;
    const detail = String(error?.stderr || error?.message || error || "")
      .replace(/[\r\n]+/g, " ")
      .slice(-1200);
    throw new CheckpointBridgeError(
      500,
      "Checkpoint bridge execution failed" + (detail ? ": " + detail : "")
    );
  }
}

export async function loadCheckpointCenter(includeSecrets: boolean) {
  const code = String.raw`
import json, sys
from app.services import seo_checkpoint_center as center
payload = json.loads(sys.argv[1])
result = center.build_center_payload(include_secrets=bool(payload.get("include_secrets")))
print("BORIS_CHECKPOINT_BRIDGE_JSON:" + json.dumps(result, ensure_ascii=False))
`;
  return runPython(code, { include_secrets: includeSecrets }, 30000);
}

export async function completeCheckpoint(payload: Record<string, unknown>) {
  const code = String.raw`
import json, sys
from app.services import seo_checkpoint_center as center
payload = json.loads(sys.argv[1])
result = center.human_completed(
    entry_id=str(payload.get("entry_id") or ""),
    platform=str(payload.get("platform") or ""),
    target_site=(str(payload.get("target_site")) if payload.get("target_site") else None),
    page_url=(str(payload.get("page_url")) if payload.get("page_url") else None),
)
print("BORIS_CHECKPOINT_BRIDGE_JSON:" + json.dumps(result, ensure_ascii=False))
`;
  return runPython(code, payload, 120000);
}

export function noStoreHeaders() {
  return {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    Pragma: "no-cache",
    "X-Content-Type-Options": "nosniff",
  };
}
