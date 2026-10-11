"""BORIS Messenger V10 bounded production media load QA.

QA tenant only. Generates tiny local fixtures, exercises production HTTP upload/commit,
waits for Messenger workers, captures latency/queue metrics, and removes exact QA rows/files.
No paid provider calls.
"""
from pathlib import Path
exec(Path("/root/BORIS/frontend/scripts/qa-messenger-v70-multiuser.py").read_text().split("access0,team0=set_viewer()")[0])

import concurrent.futures
import os
import statistics
import subprocess
import tempfile
import time
from uuid import uuid4

for _key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(_key, None)
os.environ["NO_PROXY"] = "127.0.0.1,localhost,boris-ai.pro"
os.environ["no_proxy"] = os.environ["NO_PROXY"]

VOICE_TIERS = (10, 20, 50)
VIDEO_TIERS = (3, 5, 10)
voice_assets = []
video_assets = []
message_ids = []
client_ids = []
storage_paths = []
sid = None
checks = {}
metrics = {"voice": {}, "video": {}}


def auth(token):
    return {"Authorization": "Bearer " + token}


def wait_api_generation(timeout=360):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        codes = []
        for port in (8000, 8001):
            try:
                r = requests.post(
                    f"http://127.0.0.1:{port}/api/internal-messenger/telemetry",
                    headers={"Content-Type": "application/json"},
                    json={},
                    timeout=3,
                )
                codes.append(r.status_code)
            except Exception:
                codes.append(0)
        if codes == [401, 401]:
            return True
        time.sleep(3)
    return False


def upload(token, cid, kind, path):
    started = time.monotonic()
    with open(path, "rb") as fh:
        mime = "audio/webm" if kind == "voice" else "video/webm"
        r = requests.post(
            WEB + f"/api/internal-messenger/conversations/{cid}/attachments/upload?attachment_kind={kind}",
            headers=auth(token),
            files={"file": (path.name, fh, mime)},
            timeout=60,
        )
    elapsed = (time.monotonic() - started) * 1000
    if r.status_code != 200:
        raise AssertionError((kind, r.status_code, r.text[:400]))
    payload = r.json()
    asset = str((payload.get("upload") or {}).get("asset_id") or "")
    if not asset:
        raise AssertionError((kind, "missing_asset_id", payload))
    return asset, elapsed


def commit(token, cid, kind, asset):
    client = str(uuid4())
    started = time.monotonic()
    body = {"message_type": kind, "client_message_id": client}
    if kind == "voice":
        body["voice_asset_id"] = asset
    else:
        body["video_asset_id"] = asset
    r = requests.post(
        WEB + f"/api/internal-messenger/conversations/{cid}/messages",
        headers={**auth(token), "Content-Type": "application/json"},
        json=body,
        timeout=30,
    )
    elapsed = (time.monotonic() - started) * 1000
    if r.status_code != 200:
        raise AssertionError((kind, r.status_code, r.text[:500]))
    mid = int(r.json()["message"]["id"])
    return mid, client, elapsed


def run_upload_tier(token, cid, kind, path, n):
    before = os.getloadavg()[0]
    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
        results = list(pool.map(lambda _: upload(token, cid, kind, path), range(n)))
    latencies = [x[1] for x in results]
    after = os.getloadavg()[0]
    return {
        "assets": [x[0] for x in results],
        "requests": n,
        "median_ms": round(statistics.median(latencies), 1),
        "p95_ms": round(sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)], 1),
        "max_ms": round(max(latencies), 1),
        "load1_before": round(before, 2),
        "load1_after": round(after, 2),
    }


def wait_jobs(kind, asset_ids, timeout):
    end = time.monotonic() + timeout
    terminal = {"completed", "failed", "cancelled"}
    while time.monotonic() < end:
        db = SessionLocal()
        try:
            rows = db.execute(text("""
                SELECT j.id,j.status,j.created_at,j.started_at,j.finished_at,j.attempts,j.error_text,
                       j.payload_json->>'asset_id' asset_id
                FROM background_jobs j
                WHERE j.kind=:kind AND j.payload_json->>'asset_id'=ANY(:ids)
                ORDER BY j.id
            """), {"kind": kind, "ids": asset_ids}).mappings().all()
        finally:
            db.close()
        if len(rows) == len(asset_ids) and all(row["status"] in terminal for row in rows):
            return rows
        time.sleep(1)
    raise AssertionError(f"{kind}_terminal_timeout")


def summarize_jobs(rows):
    queue = []
    processing = []
    failures = []
    for row in rows:
        if row["started_at"] and row["created_at"]:
            queue.append((row["started_at"] - row["created_at"]).total_seconds() * 1000)
        if row["finished_at"] and row["started_at"]:
            processing.append((row["finished_at"] - row["started_at"]).total_seconds() * 1000)
        if row["status"] != "completed":
            failures.append({"id": row["id"], "status": row["status"], "error": row["error_text"]})
    return {
        "jobs": len(rows),
        "completed": len(rows) - len(failures),
        "failures": failures,
        "queue_median_ms": round(statistics.median(queue), 1) if queue else None,
        "queue_max_ms": round(max(queue), 1) if queue else None,
        "processing_median_ms": round(statistics.median(processing), 1) if processing else None,
        "processing_max_ms": round(max(processing), 1) if processing else None,
    }


def api_probe(token):
    timings = []
    for _ in range(12):
        started = time.monotonic()
        r = requests.get(
            WEB + f"/api/internal-messenger/readiness?account_id={ACCOUNT}",
            headers=auth(token),
            timeout=10,
        )
        timings.append((time.monotonic() - started) * 1000)
        if r.status_code != 200:
            raise AssertionError(("readiness", r.status_code))
    return {
        "requests": len(timings),
        "median_ms": round(statistics.median(timings), 1),
        "max_ms": round(max(timings), 1),
    }


def collect_cleanup_paths(asset_ids, table):
    if not asset_ids:
        return []
    db = SessionLocal()
    try:
        if table == "voice":
            rows = db.execute(text("""
                SELECT conversation_id,original_storage_ref,playback_storage_ref
                FROM internal_voice_assets WHERE id::text=ANY(:ids)
            """), {"ids": asset_ids}).mappings().all()
            refs = [(r["conversation_id"], r["original_storage_ref"]) for r in rows]
            refs += [(r["conversation_id"], r["playback_storage_ref"]) for r in rows]
        else:
            rows = db.execute(text("""
                SELECT conversation_id,original_storage_ref,playback_storage_ref,thumbnail_storage_ref
                FROM internal_video_assets WHERE id::text=ANY(:ids)
            """), {"ids": asset_ids}).mappings().all()
            refs = []
            for r in rows:
                refs.extend([
                    (r["conversation_id"], r["original_storage_ref"]),
                    (r["conversation_id"], r["playback_storage_ref"]),
                    (r["conversation_id"], r["thumbnail_storage_ref"]),
                ])
        return [(c, ref) for c, ref in refs if ref]
    finally:
        db.close()


try:
    if not wait_api_generation():
        raise AssertionError("api_generation_not_converged")

    token, sid, _ = issue(OWNER_ID, "V10 media load")
    ensure = requests.post(
        WEB + "/api/internal-messenger/general/ensure",
        headers={**auth(token), "Content-Type": "application/json"},
        json={"account_id": ACCOUNT},
        timeout=20,
    )
    assert ensure.status_code == 200, (ensure.status_code, ensure.text[:500])
    cid = int(ensure.json()["conversation_id"])

    with tempfile.TemporaryDirectory(prefix="boris-v10-media-load-") as td:
        td = Path(td)
        voice = td / "voice.webm"
        video = td / "video.webm"
        subprocess.run([
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=740:sample_rate=48000",
            "-t", "0.45", "-c:a", "libopus", "-f", "webm", str(voice),
        ], check=True, timeout=30)
        subprocess.run([
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=12",
            "-f", "lavfi", "-i", "sine=frequency=620:sample_rate=48000",
            "-t", "0.8", "-c:v", "libvpx", "-c:a", "libopus",
            "-f", "webm", "-live", "1", str(video),
        ], check=True, timeout=30)

        for n in VOICE_TIERS:
            # Fail closed rather than overload the production host at the 50-way tier.
            if n == 50:
                cpu = max(1, os.cpu_count() or 1)
                deadline = time.monotonic() + 180
                while os.getloadavg()[0] > cpu * 1.2 and time.monotonic() < deadline:
                    time.sleep(5)
                if os.getloadavg()[0] > cpu * 1.2:
                    metrics["voice"][str(n)] = {
                        "status": "BLOCKED_CAPACITY",
                        "load1": round(os.getloadavg()[0], 2),
                        "cpu": cpu,
                    }
                    continue
            tier = run_upload_tier(token, cid, "voice", voice, n)
            voice_assets.extend(tier.pop("assets"))
            tier["status"] = "PASS"
            metrics["voice"][str(n)] = tier

        for n in VIDEO_TIERS:
            tier = run_upload_tier(token, cid, "video_note", video, n)
            video_assets.extend(tier.pop("assets"))
            tier["status"] = "PASS"
            metrics["video"][str(n)] = tier

        commit_latencies = {"voice": [], "video": []}
        for asset in voice_assets:
            mid, client, elapsed = commit(token, cid, "voice", asset)
            message_ids.append(mid); client_ids.append(client); commit_latencies["voice"].append(elapsed)
        for asset in video_assets:
            mid, client, elapsed = commit(token, cid, "video_note", asset)
            message_ids.append(mid); client_ids.append(client); commit_latencies["video"].append(elapsed)

        checks["API_DURING_QUEUE"] = {"ok": True, **api_probe(token)}
        vjobs = wait_jobs("messenger_voice_transcription", voice_assets, 600) if voice_assets else []
        xjobs = wait_jobs("messenger_video_processing", video_assets, 600) if video_assets else []
        checks["VOICE_JOBS"] = {"ok": not summarize_jobs(vjobs)["failures"], **summarize_jobs(vjobs)}
        checks["VIDEO_JOBS"] = {"ok": not summarize_jobs(xjobs)["failures"], **summarize_jobs(xjobs)}
        checks["VOICE_COMMIT"] = {
            "ok": bool(commit_latencies["voice"]),
            "count": len(commit_latencies["voice"]),
            "median_ms": round(statistics.median(commit_latencies["voice"]), 1) if commit_latencies["voice"] else None,
            "max_ms": round(max(commit_latencies["voice"]), 1) if commit_latencies["voice"] else None,
        }
        checks["VIDEO_COMMIT"] = {
            "ok": bool(commit_latencies["video"]),
            "count": len(commit_latencies["video"]),
            "median_ms": round(statistics.median(commit_latencies["video"]), 1) if commit_latencies["video"] else None,
            "max_ms": round(max(commit_latencies["video"]), 1) if commit_latencies["video"] else None,
        }

        storage_paths.extend(collect_cleanup_paths(voice_assets, "voice"))
        storage_paths.extend(collect_cleanup_paths(video_assets, "video"))

finally:
    # Exact test-row cleanup. Append-only audit/telemetry evidence intentionally remains.
    try:
        db = SessionLocal()
        try:
            all_assets = voice_assets + video_assets
            if all_assets:
                db.execute(text("DELETE FROM background_jobs WHERE payload_json->>'asset_id'=ANY(:ids)"), {"ids": all_assets})
            if client_ids:
                db.execute(text("""
                    DELETE FROM internal_message_receipts
                    WHERE conversation_id=:c AND user_id=:u AND client_message_id::text=ANY(:ids)
                """), {"c": cid if 'cid' in globals() else 0, "u": OWNER_ID, "ids": client_ids})
            if message_ids:
                ids = [str(x) for x in message_ids]
                db.execute(text("DELETE FROM internal_messenger_events WHERE entity_type='message' AND entity_id=ANY(:ids)"), {"ids": ids})
            if voice_assets:
                db.execute(text("DELETE FROM internal_voice_assets WHERE id::text=ANY(:ids)"), {"ids": voice_assets})
            if video_assets:
                db.execute(text("DELETE FROM internal_video_assets WHERE id::text=ANY(:ids)"), {"ids": video_assets})
            if message_ids:
                db.execute(text("DELETE FROM internal_message_attachments WHERE message_id=ANY(:ids)"), {"ids": message_ids})
                db.execute(text("DELETE FROM internal_messages WHERE id=ANY(:ids)"), {"ids": message_ids})
            db.commit()
            checks["CLEANUP_DB"] = {"ok": True, "messages": len(message_ids), "assets": len(voice_assets) + len(video_assets)}
        finally:
            db.close()
        from app.api import internal_messenger as core
        for conversation_id, ref in storage_paths:
            try:
                core._attachment_storage_path(ref, int(conversation_id)).unlink(missing_ok=True)
            except Exception:
                pass
        checks["CLEANUP_FILES"] = {"ok": True, "refs": len(storage_paths)}
    except Exception as cleanup_exc:
        checks["CLEANUP"] = {"ok": False, "error": type(cleanup_exc).__name__ + ":" + str(cleanup_exc)}
    if sid:
        revoke(OWNER_ID, sid)

voice50 = metrics["voice"].get("50", {})
performance_ok = (
    all(v.get("status") == "PASS" for k, v in metrics["voice"].items() if k in {"10", "20"})
    and all(v.get("status") == "PASS" for v in metrics["video"].values())
    and all(v.get("ok") for v in checks.values())
    and voice50.get("status") in {"PASS", "BLOCKED_CAPACITY"}
)
print(json.dumps({
    "status": "PASS" if performance_ok and voice50.get("status") == "PASS" else "PARTIAL",
    "metrics": metrics,
    "checks": checks,
    "capacity_boundary": None if voice50.get("status") == "PASS" else voice50,
}, ensure_ascii=False, default=str, indent=2))
raise SystemExit(0 if performance_ok else 1)
