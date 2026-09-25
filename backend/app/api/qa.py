import os, subprocess, glob, json
from fastapi import APIRouter
from app.db.session import SessionLocal
from app.models.storage import Storage
from app.services.exception_observability import observe_suppressed

router = APIRouter(prefix="/api/qa", tags=["qa"])
RUNTIME_BACKEND = os.environ.get("BORIS_RUNTIME_BACKEND_ROOT", "/root/BORIS/backend")

def _run_bg():
    subprocess.Popen(
        ["/root/BORIS/backend/venv/bin/python3", os.path.join(RUNTIME_BACKEND, "qa_tester.py")],
        cwd="/root/BORIS/backend",
        stdout=open("/root/BORIS/backend/images/qa/last_run.log","w"),
        stderr=subprocess.STDOUT,
    )

from fastapi import Depends as _DepSec
from app.api.auth import require_owner as _ReqOwner
from app.services.ai_costs_private_access import require_ai_costs_private_owner as _ReqPrivateOwner
@router.post("/run")
def qa_run(_=_DepSec(_ReqOwner)):
    _run_bg()
    return {"status": "ok", "message": "Тест запущен в фоне. Отчёт придёт в Telegram через 2-3 минуты."}


def _run_landing_bg():
    subprocess.Popen(
        ["/root/BORIS/backend/venv/bin/python3", os.path.join(RUNTIME_BACKEND, "qa_tester.py"), "--landing"],
        cwd="/root/BORIS/backend",
        stdout=open("/root/BORIS/backend/images/qa/last_landing.log","w"),
        stderr=subprocess.STDOUT,
    )

@router.post("/landing")
def qa_landing(_=_DepSec(_ReqOwner)):
    _run_landing_bg()
    return {"status": "ok", "message": "Аудит лендинга запущен (ПК+моб, vision). Отчёт придёт в Telegram через 1-2 минуты."}

@router.get("/reports")
def qa_reports(_=_DepSec(_ReqOwner)):
    files = sorted(glob.glob("/root/BORIS/backend/images/qa/report_*.html"), reverse=True)[:20]
    reports = [{"name": os.path.basename(f), "url": f"/images/qa/{os.path.basename(f)}"} for f in files]
    return {"status": "ok", "reports": reports}

@router.get("/auto")
def qa_auto_get(_=_DepSec(_ReqOwner)):
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id=="__qa__", Storage.key=="auto").first()
        return {"status": "ok", "enabled": (row.value == "1") if row else False}
    finally:
        db.close()

@router.post("/auto")
def qa_auto_set(body: dict, _=_DepSec(_ReqOwner)):
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


# === BORIS UX CLIENT TESTER V1 ===
UX_CLIENT_DIR = "/root/BORIS/backend/images/qa/ux_client"
UX_CLIENT_LOG = os.path.join(UX_CLIENT_DIR, "last_run.log")
UX_CLIENT_PID = os.path.join(UX_CLIENT_DIR, "running.pid")

def _ux_client_running():
    try:
        with open(UX_CLIENT_PID, encoding="utf-8") as _fh:
            pid=int(_fh.read().strip())
        os.kill(pid,0)
        stat_path=f"/proc/{pid}/stat"
        if os.path.isfile(stat_path):
            with open(stat_path, encoding="utf-8") as _fh:
                state=_fh.read().split()[2]
            if state == "Z": return False,None
        return True,pid
    except Exception:
        return False,None

@router.post("/ux-client/run")
def ux_client_run(body: dict, _=_DepSec(_ReqPrivateOwner)):
    os.makedirs(UX_CLIENT_DIR, exist_ok=True)
    running,pid=_ux_client_running()
    if running:
        return {"status":"running","pid":pid,"message":"UX-тестировщик уже выполняет сценарии"}
    task=str(body.get("task") or "Пройти BORIS как реальный клиент: оценить удобство, логику, понятность, лишние действия и следующий шаг.").strip()[:2000]
    env=os.environ.copy(); env["BORIS_UX_TASK"]=task
    log=open(UX_CLIENT_LOG,"w")
    proc=subprocess.Popen(["/root/BORIS/backend/venv/bin/python3",os.path.join(RUNTIME_BACKEND,"ux_client_tester.py")],cwd="/root/BORIS/backend",stdout=log,stderr=subprocess.STDOUT,env=env,start_new_session=True)
    with open(UX_CLIENT_PID,"w", encoding="utf-8") as _fh:
        _fh.write(str(proc.pid))
    return {"status":"started","pid":proc.pid,"task":task,"message":"UX-тестировщик начал проходить BORIS глазами разных клиентов"}

@router.get("/ux-client/status")
def ux_client_status(_=_DepSec(_ReqPrivateOwner)):
    running,pid=_ux_client_running()
    latest=os.path.join(UX_CLIENT_DIR,"latest.json")
    report=None
    if os.path.isfile(latest):
        try:
            with open(latest,encoding="utf-8") as _fh:
                report=json.load(_fh)
        except Exception: report=None
    log_tail=""
    if os.path.isfile(UX_CLIENT_LOG):
        try:
            with open(UX_CLIENT_LOG,encoding="utf-8",errors="ignore") as _fh:
                log_tail=_fh.read()[-3000:]
        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=116)
    return {"status":"ok","running":running,"pid":pid,"latest":report,"log_tail":log_tail}

@router.get("/ux-client/reports")
def ux_client_reports(_=_DepSec(_ReqPrivateOwner)):
    os.makedirs(UX_CLIENT_DIR, exist_ok=True)
    files=sorted(glob.glob(os.path.join(UX_CLIENT_DIR,"report_*.html")),reverse=True)[:30]
    return {"status":"ok","reports":[{"name":os.path.basename(f),"url":"/images/qa/ux_client/"+os.path.basename(f)} for f in files]}
# === /BORIS UX CLIENT TESTER V1 ===
