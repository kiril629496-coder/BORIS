import os, subprocess, glob, json
from fastapi import APIRouter
from app.db.session import SessionLocal
from app.models.storage import Storage

router = APIRouter(prefix="/api/qa", tags=["qa"])

def _run_bg():
    subprocess.Popen(
        ["/root/BORIS/backend/venv/bin/python3", "/root/BORIS/backend/qa_tester.py"],
        cwd="/root/BORIS/backend",
        stdout=open("/root/BORIS/backend/images/qa/last_run.log","w"),
        stderr=subprocess.STDOUT,
    )

from fastapi import Depends as _DepSec
from app.api.auth import require_owner as _ReqOwner
@router.post("/run")
def qa_run():
    _run_bg()
    return {"status": "ok", "message": "Тест запущен в фоне. Отчёт придёт в Telegram через 2-3 минуты."}


def _run_landing_bg():
    subprocess.Popen(
        ["/root/BORIS/backend/venv/bin/python3", "/root/BORIS/backend/qa_tester.py", "--landing"],
        cwd="/root/BORIS/backend",
        stdout=open("/root/BORIS/backend/images/qa/last_landing.log","w"),
        stderr=subprocess.STDOUT,
    )

@router.post("/landing")
def qa_landing():
    _run_landing_bg()
    return {"status": "ok", "message": "Аудит лендинга запущен (ПК+моб, vision). Отчёт придёт в Telegram через 1-2 минуты."}

@router.get("/reports")
def qa_reports(_=_DepSec(_ReqOwner)):
    files = sorted(glob.glob("/root/BORIS/backend/images/qa/report_*.html"), reverse=True)[:20]
    reports = [{"name": os.path.basename(f), "url": f"/images/qa/{os.path.basename(f)}"} for f in files]
    return {"status": "ok", "reports": reports}

@router.get("/auto")
def qa_auto_get():
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id=="__qa__", Storage.key=="auto").first()
        return {"status": "ok", "enabled": (row.value == "1") if row else False}
    finally:
        db.close()

@router.post("/auto")
def qa_auto_set(body: dict):
    db = SessionLocal()
    try:
        val = "1" if body.get("enabled") else "0"
        row = db.query(Storage).filter(Storage.account_id=="__qa__", Storage.key=="auto").first()
        if row: row.value = val
        else: db.add(Storage(account_id="__qa__", key="auto", value=val))
        db.commit()
        return {"status": "ok", "enabled": val=="1"}
    finally:
        db.close()
