BORIS PHOTO FACTORY GPU WORKER
==============================

Created: 20260821_164611

Files:
  install_gpu_worker.sh
  boris_qwen_worker.py
  worker.env.example
  boris-photo-worker.service.example

BORIS worker token:
  DO NOT publish it.
  Source on BORIS:
  /root/BORIS/secrets/photo_factory_worker_token

Architecture:

  BORIS Photo Factory job
        |
        v
  POST /worker/claim
        |
        v
  GPU worker downloads references
        |
        v
  Qwen-Image-Edit-2511
        |
        v
  POST /worker/result
        |
        v
  /images/photo_factory/<job>/<image>
        |
        v
  ЕЦО Photo Factory gallery

The actual Qwen model is intentionally NOT installed
on the primary BORIS server.

The production GPU machine needs:
  NVIDIA GPU
  >=24GB VRAM for this standard profile
  >=90GB free disk
  internet for first model download
