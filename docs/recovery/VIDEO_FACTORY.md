# BORIS Video Factory — PRODUCTION CORE — PASS

Date: 2026-09-01

VIDEO_FACTORY_CORE=PASS
LOCAL_RENDER=PASS
FFMPEG=PASS
QUEUE_WORKERS=PASS
VERSIONING=PASS
SEMANTIC_QA=PASS
RESPONSIVE_UI=PASS
TUTORIAL_CAPTURE_CONFIG_CONSISTENCY=PASS
NEW_PAID_VIDEO_QA_CALLS=0
NEW_TUTORIAL_CAPTURE_FOR_QA=NO

Canonical implementation remains `app/video_factory/*` + `app/api/video_factory.py`; no second video engine was created.

Production acceptance:
- public `/dashboard/video-factory` HTTP 200;
- `/api/video-factory/capabilities`, `/queue`, `/tutorial/readiness`, `/projects`, `/batches` HTTP 200;
- local render ready, ffmpeg ready, queue 0/100, two workers;
- paid AI video default is false;
- authenticated browser QA passed at 1440, 768 and 390 px with no horizontal overflow or page errors;
- production-visible ready sample exists with final H.264/yuv420p/audio technical QA PASS and semantic QA 95/100.

## Replica consistency repair

A real production inconsistency was proven: backend :8000 had `tutorial_capture.capture_ready=true`, while replica :8001 returned false because `30-video-demo.conf` existed only for `boris-backend.service`.

Minimal repair: copied only the canonical `30-video-demo.conf` drop-in to `boris-backend-replica.service.d`, leaving replica-specific runtime-role/proxy/resource settings untouched; daemon-reload + restart replica only.

Post-fix proof:
- :8000 demo_isolation_configured=true / capture_ready=true
- :8001 demo_isolation_configured=true / capture_ready=true
- desktop/tablet/mobile all render tutorial mode consistently enabled.

No new tutorial recording was created merely for QA, because existing production output already proves the render pipeline and an extra capture would leave unnecessary test residue. Reopen on new runtime evidence or when a real tutorial capture naturally runs.
