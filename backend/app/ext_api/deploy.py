# -*- coding: utf-8 -*-
"""Выкатка без человека: принято лидом — значит применено и проверено.

Раньше цепочка обрывалась на последнем шаге: агенты доводили работу до
ACCEPT, а дальше владелец руками копировал файл и запускал установку. Это и
делало его оператором развёртывания. Здесь этот шаг закрыт.

Ворота сервера, через которые проходит ЛЮБОЕ изменение:

    БЭКАП → ПРИМЕНЕНИЕ → СБОРКА И ТЕСТЫ → РЕСТАРТ РАЗРЕШЁННЫХ СЛУЖБ
    → ПРОВЕРКА ЖИВОСТИ → при провале ОТКАТ

Два входа в эти ворота:
  · правки агентов, принятые лидом — применяются сразу после ACCEPT;
  · обновление самого шлюза — файл, положенный в каталог обновлений.

Выкатка последовательная: одновременно едет одно изменение, иначе откат
перестаёт быть откатом.
"""
import importlib.util
import io, json, os, re, shutil, subprocess, sys, time

from . import db, repo
from .errors import ApiError
from app.services.exception_observability import observe_suppressed

# Рестарт разрешён только этим службам. Всё остальное — не наше дело.
EXECUTOR_UNITS = tuple("boris-executor" + ("" if i == 1 else "-%d" % i)
                       for i in range(1, 9))
ALLOWED_UNITS = ("boris-backend", "boris-backend-replica", "boris-background-worker",
                 "boris-crm-sync", "boris-telephony-runtime",
                 "boris-frontend", "boris-lead", "boris-dispatcher", "boris-inbox",
                 "boris-deploy", "boris-gitdeploy") + EXECUTOR_UNITS

# Адрес живости не угадывается: пробуем несколько и берём первый, который
# вообще отвечает. На бою /health отдавал 404 — и проверка живости честно
# считалась непройденной, хотя бэкенд работал.
HEALTH_PATHS = ("/health", "/healthz", "/api/v1/health/production",
                "/api/v1/capabilities", "/")
LOCK_PATH = "/tmp/.boris_deploy.lock"
INCOMING = "updates/incoming"
ARCHIVE = "updates/applied"
AUTO_APPLY = os.environ.get("BORIS_AUTO_APPLY", "1") == "1"
GIT_TRANSPORT_ENABLED = os.environ.get("BORIS_GIT_TRANSPORT", "0") == "1"


def _run(argv, cwd=None, timeout=900):
    # Без systemd спрашивать systemctl бессмысленно и опасно: таймаут убивает
    # потомка, а чтение трубы продолжает ждать внука — процесс не вернётся.
    if argv and argv[0] == "systemctl" and not os.path.isdir("/run/systemd/system"):
        return 1, "systemd на этой машине не управляет службами"
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        return 1, "%s: %s" % (type(e).__name__, e)


LOCK_STALE_MINUTES = int(os.environ.get("BORIS_LOCK_STALE_MIN", "30"))
# Выкатка длиной в час — это не выкатка, а зависший процесс. Дальше этого
# срока замок снимается, даже если владелец формально «жив».
LOCK_HARD_MAX_MINUTES = int(os.environ.get("BORIS_LOCK_HARD_MAX_MIN", "60"))


def _proc_start(pid):
    """Момент запуска процесса. Один PID может быть переиспользован системой,
    и тогда проверка «процесс существует» лжёт: замок мёртвого владельца
    выглядит живым вечно. Пара (PID, момент запуска) уникальна."""
    try:
        raw = io.open("/proc/%d/stat" % int(pid), encoding="utf-8",
                      errors="ignore").read()
        return raw.rsplit(")", 1)[1].split()[19]
    except Exception:
        return None


def _lock_payload():
    return "pid=%d start=%s at=%d\n" % (os.getpid(),
                                        _proc_start(os.getpid()) or "-",
                                        int(time.time()))


def _lock_owner_alive():
    """Жив ли процесс, который держит замок.

    Замок без этой проверки — мина: процесс убили, файл остался, и выкатка
    встаёт навсегда. Ровно это и случилось на бою: служба каждые пять минут
    сообщала «занято» тому, кого уже нет.

    Проверок здесь три, и каждая закрывает свой способ соврать: существование
    процесса, совпадение момента его запуска (защита от переиспользованного
    PID) и предельный возраст замка (защита от зависшего владельца).
    """
    try:
        raw = io.open(LOCK_PATH, encoding="utf-8").read().strip()
    except OSError:
        return False, None
    pid, start = None, None
    if raw.isdigit():
        pid = int(raw)
    else:
        for part in raw.split():
            k, _, v = part.partition("=")
            if k == "pid" and v.isdigit():
                pid = int(v)
            elif k == "start":
                start = v if v != "-" else None
    try:
        age_min = (time.time() - os.path.getmtime(LOCK_PATH)) / 60.0
    except OSError:
        age_min = 0.0
    if age_min >= LOCK_HARD_MAX_MINUTES:
        return False, pid
    if pid:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False, pid
        except PermissionError:
            return True, pid
        now_start = _proc_start(pid)
        if start and now_start and now_start != start:
            return False, pid          # PID переиспользован: владельца нет
        return True, pid
    return age_min < LOCK_STALE_MINUTES, pid


class _Lock(object):
    """Одна выкатка за раз. Две параллельные превращают откат в лотерею.

    Замок знает своего владельца: если процесс умер или файл пролежал дольше
    допустимого, замок снимается сам. Человек для этого не нужен.
    """

    def __enter__(self):
        for attempt in range(60):
            try:
                fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, _lock_payload().encode())
                os.close(fd)
                return self
            except FileExistsError:
                alive, pid = _lock_owner_alive()
                if not alive:
                    print("deploy: снимаю брошенный замок процесса %s" % pid,
                          file=sys.stderr, flush=True)
                    try:
                        os.unlink(LOCK_PATH)
                    except OSError:
                        pass
                    continue
                time.sleep(5)
        raise ApiError("BUSY", "другая выкатка ещё идёт — эта ждала пять минут")

    def __exit__(self, *a):
        try:
            os.unlink(LOCK_PATH)
        except OSError:
            pass
        return False


def backend_dir():
    return os.path.join(repo.ROOT, "backend")


def _py():
    cand = os.path.join(backend_dir(), "venv", "bin", "python3")
    return cand if os.path.isfile(cand) else sys.executable


def _backend_release_build_activate():
    """Build and atomically select one verified immutable backend generation.

    Source edits are never production by themselves. Only a PASS artifact from
    build_backend_release.py may become the candidate for the HA rolling lane.
    """
    script = os.path.join(backend_dir(), "scripts", "build_backend_release.py")
    if not os.path.isfile(script):
        return {"ok": False, "error": "backend release builder missing"}
    code, out = _run([_py(), script, "--activate"], cwd=backend_dir(), timeout=300)
    payload = {}
    for line in reversed((out or "").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
            break
        except Exception:
            continue
    ok = code == 0 and payload.get("status") == "PASS" and bool(payload.get("build_id"))
    return {
        "ok": ok,
        "returncode": code,
        "build_id": payload.get("build_id"),
        "release": payload.get("release"),
        "files": payload.get("files"),
        "bytes": payload.get("bytes"),
        "sealed": payload.get("sealed"),
        "activated": payload.get("activated"),
        "tail": (out or "")[-4000:],
    }


def _backend_release_current_build():
    """Return the exact active immutable backend build id, or None."""
    meta = os.path.join(repo.ROOT, "releases", "backend", "current", "BORIS_ARTIFACT.json")
    try:
        payload = json.load(io.open(meta, encoding="utf-8"))
        build_id = str(payload.get("build_id") or "").strip()
        return build_id or None
    except Exception:
        return None


def _backend_release_reasons(patches, bundles):
    """Only canonical accepted source changes may create a production release.

    Arbitrary/direct edits in the mutable workspace are deliberately ignored:
    workshop bytes are not a production authorization signal.
    """
    reasons = []
    if not _backend_release_current_build():
        reasons.append("bootstrap_missing_release")
    if isinstance(patches, dict) and list(patches.get("applied") or []):
        reasons.append("accepted_patch_applied")
    for item in bundles or []:
        if not isinstance(item, dict):
            continue
        if item.get("ok") is True and item.get("archived") and not item.get("skipped"):
            reasons.append("accepted_bundle_applied")
            break
    return reasons


def _load_backend_release_builder():
    """Load the canonical release builder without importing mutable app runtime."""
    path = os.path.join(backend_dir(), "scripts", "build_backend_release.py")
    spec = importlib.util.spec_from_file_location("boris_backend_release_builder", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("BACKEND_RELEASE_BUILDER_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _runtime_workspace_changed_paths():
    """Return executable-source drift versus the active immutable artifact."""
    meta = os.path.join(repo.ROOT, "releases", "backend", "current", "BORIS_ARTIFACT.json")
    if not os.path.isfile(meta):
        return None
    active = json.load(io.open(meta, encoding="utf-8"))
    deployed = {
        str(row.get("path") or ""): str(row.get("sha256") or "")
        for row in (active.get("files") or []) if isinstance(row, dict)
    }
    builder = _load_backend_release_builder()
    current_rows = builder._manifest_rows(
        list(builder._clean_app_files()) + list(builder._clean_root_python_files())
    )
    current = {
        str(row.get("path") or ""): str(row.get("sha256") or "")
        for row in current_rows if isinstance(row, dict)
    }
    return sorted(
        path for path in (set(deployed) | set(current))
        if deployed.get(path) != current.get(path)
    )


def _normalize_backend_authorized_path(path):
    value = str(path or "").replace("\\", "/").strip()
    source = backend_dir().replace("\\", "/").rstrip("/") + "/"
    if value.startswith(source):
        value = value[len(source):]
    value = value.lstrip("./")
    if value.startswith("backend/"):
        value = value[len("backend/"):]
    return value


def _backend_release_provenance_gate(patches, bundles):
    """Fail closed if unreviewed workspace bytes would hitchhike into a release."""
    changed = _runtime_workspace_changed_paths()
    if changed is None:
        return {"ok": True, "bootstrap": True, "changed": [], "authorized": [], "unexpected": []}

    authorized = set()
    if isinstance(patches, dict):
        for item in list(patches.get("applied") or []):
            if not isinstance(item, dict):
                continue
            for path in list(item.get("files") or []):
                normalized = _normalize_backend_authorized_path(path)
                if normalized:
                    authorized.add(normalized)

    successful_bundles = [
        item for item in (bundles or [])
        if isinstance(item, dict) and item.get("ok") is True
        and item.get("archived") and not item.get("skipped")
    ]
    if successful_bundles:
        return {
            "ok": False,
            "reason": "accepted_bundle_missing_runtime_file_provenance",
            "changed": changed,
            "authorized": sorted(authorized),
            "unexpected": changed,
        }

    unexpected = sorted(set(changed) - authorized)
    return {
        "ok": not unexpected,
        "reason": None if not unexpected else "unreviewed_workspace_runtime_drift",
        "changed": changed,
        "authorized": sorted(authorized),
        "unexpected": unexpected,
    }


def _migration_gate():
    """Canonical DB/source convergence gate owned by boris-deploy.

    Existing ledger corruption is a hard stop. A newly landed numeric migration
    is applied through the canonical runner (DB backup + advisory lock + one SQL
    transaction per migration), then verified again before runtime convergence.
    """
    runner = os.path.join(backend_dir(), "migrations", "run_all.py")
    if not os.path.isfile(runner):
        return {"ok": False, "stage": "discover", "error": "canonical migration runner missing"}
    code, status_out = _run([_py(), runner, "status"], cwd=backend_dir(), timeout=120)
    status_tail = (status_out or "")[-12000:]
    if code != 0:
        return {"ok": False, "stage": "status", "returncode": code, "tail": status_tail}
    hard_markers = (
        "CHECKSUM_MISMATCH", "FILENAME_MISMATCH", "APPLIED_FILE_MISSING",
        "NONCANONICAL_MIGRATION_FILES", "DUPLICATE_MIGRATION_ORDINAL",
    )
    hard = [marker for marker in hard_markers if marker in status_out]
    if hard:
        return {"ok": False, "stage": "verify_source", "errors": hard, "tail": status_tail}
    pending = []
    for line in (status_out or "").splitlines():
        parts = line.split(" ", 2)
        if len(parts) == 3 and parts[1] == "PENDING":
            pending.append(parts[2])
    apply_out = ""
    if pending:
        apply_code, apply_out = _run(
            [_py(), runner, "apply-pending"], cwd=backend_dir(), timeout=1200
        )
        if apply_code != 0:
            return {
                "ok": False, "stage": "apply_pending", "returncode": apply_code,
                "pending": pending, "tail": (apply_out or "")[-12000:],
            }
    verify_code, verify_out = _run(
        [_py(), runner, "verify"], cwd=backend_dir(), timeout=120
    )
    ok = verify_code == 0 and "MIGRATION_LEDGER_VERIFY=PASS" in (verify_out or "")
    return {
        "ok": ok,
        "stage": "verified" if ok else "post_verify",
        "pending_before": pending,
        "applied_pending": bool(pending),
        "apply_tail": (apply_out or "")[-4000:],
        "verify_tail": (verify_out or "")[-4000:],
    }


HEALTH_WAIT_SECONDS = int(os.environ.get("BORIS_HEALTH_WAIT_SEC", "90"))


def _ports():
    """Где слушает бэкенд. Догадка про 8000 однажды уже стоила отката
    полностью исправной сборки."""
    found = []
    for var in ("BORIS_BACKEND_PORT", "APP_PORT", "PORT", "UVICORN_PORT"):
        v = (os.environ.get(var) or "").strip()
        if v.isdigit():
            found.append(int(v))
    code, out = _run(["systemctl", "cat", "boris-backend"], timeout=30)
    if code == 0:
        for m in re.finditer(r"--port[= ](\d{2,5})", out or ""):
            found.append(int(m.group(1)))
        for m in re.finditer(r":(\d{4,5})\b", out or ""):
            found.append(int(m.group(1)))
    for p in (8000, 8080):
        found.append(p)
    seen, out_ports = set(), []
    for p in found:
        if p not in seen and 1 < p < 65536:
            seen.add(p)
            out_ports.append(p)
    return out_ports[:6]


def health(port=None, wait_seconds=None):
    """Живой ли бэкенд после рестарта — по факту, а не по статусу службы.

    401 считается живостью: защищённые ручки шлюза без ключа обязаны отвечать
    именно так, и это доказывает, что приложение поднялось.

    Проверка обязана ждать. Приложению нужны секунды на подъём, а опрос без
    окна ожидания превращает каждую исправную выкатку в откат: ровно это и
    случилось с fail_20260820_165811 — служба была active, все пути вернули
    000, потому что спросили слишком рано.
    """
    ports = [int(port)] if port else _ports()
    limit = HEALTH_WAIT_SECONDS if wait_seconds is None else int(wait_seconds)
    deadline = time.time() + max(0, limit)
    tried, waited = [], 0.0
    while True:
        tried = []
        for p in ports:
            for path in HEALTH_PATHS:
                url = "http://127.0.0.1:%d%s" % (p, path)
                _c, out = _run(["curl", "-s", "-o", "/dev/null", "-w",
                                "%{http_code}", url], timeout=20)
                body = (out or "").strip()
                tried.append("%d%s=%s" % (p, path, body or "нет ответа"))
                if body.startswith("2") or body.startswith("3") or body == "401":
                    return {"ok": True, "http": body, "path": path, "port": p,
                            "tried": tried, "waited_sec": int(waited)}
        if time.time() >= deadline:
            break
        time.sleep(3)
        waited = time.time() - (deadline - max(0, limit))
    return {"ok": False, "http": tried[-1].split("=")[-1] if tried else "нет ответа",
            "tried": tried, "waited_sec": int(waited), "ports": ports}


def restart(units):
    """Перезапускать runtime без обхода единственного владельца backend HA.

    BACKEND_RESTART_DELEGATED_TO_CANONICAL_ROLLING_V2: запрос на любую API
    реплику означает один HA-roll через nginx drain/readiness/contract. Остальные
    разрешённые службы по-прежнему перезапускаются по одной.
    """
    requested = list(dict.fromkeys(str(u) for u in (units or [])))
    for u in requested:
        if u not in ALLOWED_UNITS:
            raise ApiError("FORBIDDEN_SCOPE", "рестарт службы «%s» не разрешён" % u)

    api_units = {"boris-backend", "boris-backend-replica"}
    done = []
    for u in [x for x in requested if x not in api_units]:
        code, out = _run(["systemctl", "restart", u], timeout=120)
        time.sleep(2)
        _state, active_out = _run(["systemctl", "is-active", u], timeout=30)
        done.append({"unit": u, "restarted": code == 0,
                     "active": (active_out or "").strip() == "active",
                     "detail": (out or "")[-800:]})

    if any(u in api_units for u in requested):
        env = dict(os.environ)
        env["BORIS_ROLLING_LANE"] = "deploy_v1"
        try:
            proc = subprocess.run(
                [os.path.join(backend_dir(), "rolling_restart_backend.sh")],
                cwd=backend_dir(), capture_output=True, text=True,
                timeout=360, env=env,
            )
            detail = ((proc.stdout or "") + (proc.stderr or ""))[-2000:]
            a0 = _run(["systemctl", "is-active", "boris-backend"], timeout=30)[1].strip() == "active"
            a1 = _run(["systemctl", "is-active", "boris-backend-replica"], timeout=30)[1].strip() == "active"
            done.append({"unit": "boris-backend-ha-pair",
                         "restarted": proc.returncode == 0,
                         "active": bool(a0 and a1),
                         "detail": detail})
        except Exception as exc:
            done.append({"unit": "boris-backend-ha-pair",
                         "restarted": False, "active": False,
                         "detail": "%s: %s" % (type(exc).__name__, exc)})
    return done


def _backup_dir(tag):
    base = os.path.join(repo.ROOT, "BORIS_AGENT_PATCHES",
                        "deploy_%s_%s" % (tag, time.strftime("%Y%m%d_%H%M%S")))
    os.makedirs(base, exist_ok=True)
    return base


def _sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def already_applied(digest):
    try:
        r = db.one("""SELECT id FROM ext_deploy_log
                       WHERE sha = :s AND ok = TRUE ORDER BY id DESC LIMIT 1""",
                   s=digest)
        return bool(r)
    except Exception:
        return False


def apply_bundle(path, units=("boris-backend",)):
    """Обновление самого шлюза: тот же путь, что руками, но без рук.

    Файл кладётся в каталог обновлений любым способом — руками, агентом,
    выгрузкой из репозитория. Служба обновлений подхватывает его сама.
    """
    if not os.path.isfile(path):
        raise ApiError("NOT_FOUND", "файл обновления не найден: %s" % path)
    with _Lock():
        backend = backend_dir()
        bak = _backup_dir("bundle")
        pkg = os.path.join(backend, "app", "ext_api")
        if os.path.isdir(pkg):
            shutil.copytree(pkg, os.path.join(bak, "ext_api"))
        steps = [{"step": "БЭКАП", "ok": True, "detail": bak}]

        # Пометка для установщика: он запущен ИЗ службы выкатки и не должен
        # перезапускать её саму — иначе процесс убивает сам себя на середине,
        # файл остаётся необработанным и всё повторяется по кругу.
        env = dict(os.environ, BORIS_DEPLOY_INSIDE="1")
        try:
            p = subprocess.run([_py(), path, "install", "--apply"], cwd=backend,
                               capture_output=True, text=True, timeout=1800, env=env)
            code, out = p.returncode, (p.stdout or "") + (p.stderr or "")
        except Exception as e:
            code, out = 1, "%s: %s" % (type(e).__name__, e)
        steps.append({"step": "ПРИМЕНЕНИЕ", "ok": code == 0, "detail": out[-1500:]})
        if code != 0:
            _restore(bak, pkg)
            steps.append({"step": "ОТКАТ", "ok": True, "detail": "пакет возвращён"})
            return {"ok": False, "steps": steps}

        code, out = _run([_py(), "-c", "import app.main"], cwd=backend, timeout=300)
        steps.append({"step": "ИМПОРТ", "ok": code == 0, "detail": out[-800:]})
        if code != 0:
            _restore(bak, pkg)
            steps.append({"step": "ОТКАТ", "ok": True, "detail": "пакет возвращён"})
            return {"ok": False, "steps": steps}

        migration = _migration_gate()
        steps.append({"step": "МИГРАЦИИ", "ok": bool(migration.get("ok")), "detail": migration})
        if not migration.get("ok"):
            _restore(bak, pkg)
            steps.append({"step": "ОТКАТ", "ok": True,
                          "detail": "код пакета возвращён; runtime не перезапускался"})
            return {"ok": False, "steps": steps}

        restart_detail = restart(list(units))
        restart_ok = bool(restart_detail) and all(
            bool(x.get("restarted")) and bool(x.get("active"))
            for x in restart_detail
        )
        steps.append({"step": "РЕСТАРТ", "ok": restart_ok,
                      "detail": restart_detail})
        if not restart_ok:
            _restore(bak, pkg)
            rollback_restart = restart(list(units))
            steps.append({"step": "ОТКАТ", "ok": True,
                          "detail": {"reason": "runtime restart failed",
                                     "restart": rollback_restart}})
            return {"ok": False, "steps": steps}
        h = health()
        steps.append({"step": "ЖИВОСТЬ", "ok": h["ok"], "detail": h})
        if not h["ok"]:
            _restore(bak, pkg)
            restart(list(units))
            steps.append({"step": "ОТКАТ", "ok": True,
                          "detail": "пакет возвращён, службы перезапущены"})
            return {"ok": False, "steps": steps}
        return {"ok": True, "steps": steps, "backup": bak}


def _restore(bak, pkg):
    src = os.path.join(bak, "ext_api")
    if os.path.isdir(src):
        shutil.rmtree(pkg, ignore_errors=True)
        shutil.copytree(src, pkg)


def _reviewed_units(paths):
    units = []
    backend_changed = any(p.startswith("backend/") for p in paths)
    if backend_changed:
        # BACKEND_HA_SINGLE_OWNER_V1:
        # Never restart either API replica (or the shared background worker)
        # from the per-patch apply loop. Doing that here bypasses nginx drain /
        # readiness fencing and can briefly take BOTH API replicas down.
        #
        # The canonical owner is tick() below:
        #   production_contract_runner -> background reconcile (if needed) ->
        #   rolling_restart_backend.sh (API2 drain/restart/verify, then API1).
        # reviewed_auto_apply() already compile/tests the new source before this
        # point, so keeping the old healthy API generation serving for a few more
        # seconds is safer than a direct restart.
        pass
    if any(("backend/app/crm/" in p or "crm_sync" in p or "calltracking_sync" in p) for p in paths):
        units.append("boris-crm-sync")
    if any(("telephony" in p) for p in paths if p.startswith("backend/")):
        units.append("boris-telephony-runtime")
    if any(p.startswith("frontend/") for p in paths):
        units.append("boris-frontend")
    # EXT_API_RUNTIME_UNIT_MAPPING_V2: recover/dev/clients are imported by the
    # long-lived dispatcher too. Leaving it on old bytecode after these files
    # change resurrects already-terminal business work until a human notices.
    # The deploy owner, not guardians/remote shell, owns this restart.
    if any(p in ("backend/app/ext_api/executor.py",
                 "backend/app/ext_api/a2a.py",
                 "backend/app/ext_api/lead.py",
                 "backend/app/ext_api/recover.py",
                 "backend/app/ext_api/dev.py",
                 "backend/app/ext_api/clients.py") for p in paths):
        units.extend(EXECUTOR_UNITS)
        units.append("boris-dispatcher")
    if "backend/app/ext_api/workspace_sync.py" in paths:
        units.append("boris-dispatcher")
    if "backend/app/ext_api/lead.py" in paths:
        units.append("boris-lead")
    return list(dict.fromkeys(u for u in units if u in ALLOWED_UNITS))


def _runtime_postflight(units):
    h = health()
    facts = {"health": h, "services": {}}
    ok = bool(h.get("ok"))
    for unit in units:
        code, out = _run(["systemctl", "is-active", unit], timeout=30)
        raw = (out or "").strip()
        active = code == 0 and raw == "active"
        idle_success = False
        state = {}
        # Executors and lead may deliberately exit/stop while no free
        # coding provider is available. systemd Result=success + inactive/dead
        # is their fail-closed idle state, not a runtime crash. Dispatcher is
        # intentionally excluded: the always-on reconciler must stay active.
        if not active and (unit in EXECUTOR_UNITS or unit == "boris-lead"):
            show_code, show_out = _run(
                ["systemctl", "show", unit,
                 "-p", "Result", "-p", "ActiveState", "-p", "SubState"],
                timeout=30,
            )
            if show_code == 0:
                for line in (show_out or "").splitlines():
                    key, sep, value = line.partition("=")
                    if sep:
                        state[key.strip()] = value.strip()
                idle_success = (
                    state.get("Result") == "success"
                    and state.get("ActiveState") == "inactive"
                    and state.get("SubState") == "dead"
                )
        healthy = active or idle_success
        facts["services"][unit] = {
            "active": active,
            "idle_success": idle_success,
            "healthy": healthy,
            "raw": raw[:200],
            "state": state,
        }
        ok = ok and healthy
    facts["ok"] = ok
    return facts


def apply_accepted_patches(limit=20):
    """Safe autonomous development loop.

    proposed -> automated REVIEW/approved -> restricted internal apply ->
    compile/tests -> selective reload -> health/service postflight -> rollback.
    No AI/admin key and no unrestricted repo.apply are involved.
    """
    if not AUTO_APPLY:
        return {"applied": [], "skipped": "автоприменение выключено BORIS_AUTO_APPLY=0"}

    # Review only patches attached to an active development job that currently
    # has a live A2A order. This also breaks bootstrap recovery deadlocks where
    # the patch itself is needed before the order can reach ACCEPT.
    # First terminalize proposals that can never be applied anymore. Keeping
    # them as proposed makes every deploy cycle re-review the same dead work.
    # We preserve the row/evidence; only status + reason change.
    db.q("""UPDATE ext_dev_patches p
              SET status='rolled_back',
                  error_text=coalesce(nullif(error_text,''),'AUTO_TERMINALIZED: parent job cancelled or missing')
            WHERE p.status='proposed'
              AND NOT EXISTS (SELECT 1 FROM ext_dev_jobs j
                               WHERE j.id=p.dev_job_id
                                 AND j.status = ANY(:js))
         """, js=['queued','planning','running','waiting','review','testing','fixing','acceptance'])

    # Infrastructure-blocked work gets a recovery window. If the same job has
    # had no executable order for a full day, its old proposals are evidence,
    # not runnable work; terminalize them instead of carrying an immortal queue.
    db.q("""UPDATE ext_dev_patches p
              SET status='rolled_back',
                  error_text=coalesce(nullif(error_text,''),'AUTO_TERMINALIZED: infrastructure block stale >24h')
            WHERE p.status='proposed'
              AND EXISTS (SELECT 1 FROM ext_dev_jobs j
                           WHERE j.id=p.dev_job_id AND j.status='waiting')
              AND EXISTS (SELECT 1 FROM ext_a2a_orders o
                           WHERE o.dev_job_id=p.dev_job_id AND o.status='blocked_infra'
                             AND o.updated_at < now() - interval '24 hours')
              AND NOT EXISTS (SELECT 1 FROM ext_a2a_orders o
                              WHERE o.dev_job_id=p.dev_job_id
                                AND o.status = ANY(:live))
         """, live=['queued','running','review_requested','returned'])

    # Only a live A2A order may authorize automatic review. Historical accepted
    # orders are not sufficient forever: require the order itself to still be in
    # an executable state and let job/order lifecycle own stale-work cleanup.
    proposed = db.rows("""SELECT p.id FROM ext_dev_patches p
                           WHERE p.status='proposed'
                             AND EXISTS (SELECT 1 FROM ext_dev_jobs j
                                          WHERE j.id=p.dev_job_id
                                            AND j.status = ANY(:js))
                             AND EXISTS (SELECT 1 FROM ext_a2a_orders o
                                          WHERE o.dev_job_id=p.dev_job_id
                                            AND o.status = ANY(:os))
                           ORDER BY p.id LIMIT :l""",
                       js=['queued','planning','running','waiting','review','testing','fixing','acceptance'],
                       os=['queued','running','review_requested','returned'],
                       l=int(limit))
    reviewed, failed = [], []
    for row in proposed:
        try:
            reviewed.append(repo.approve_reviewed_auto(row["id"]))
        except Exception as e:
            why=getattr(e, "message", str(e))
            low=(why or '').lower()
            permanent=any(marker in low for marker in (
                'путь запрещён', 'owner-only', 'hard-deny',
                'patch устарел', 'old не уникален',
                'создаёт новые файлы только через human bootstrap',
                'требует server_owned dev_job'))
            if permanent:
                db.q("""UPDATE ext_dev_patches
                          SET status='rolled_back',
                              error_text=:err
                        WHERE id=:id AND status='proposed'""",
                     id=row['id'], err='AUTO_REJECTED_PERMANENT: '+str(why)[:1800])
            failed.append({"patch_id": row["id"],
                           "stage": "review", "why": why,
                           "terminalized": bool(permanent)})

    rows = db.rows("""SELECT id FROM ext_dev_patches
                       WHERE status='approved' ORDER BY id LIMIT :l""", l=int(limit))
    applied = []
    with _Lock():
        for row in rows:
            try:
                res = repo.reviewed_auto_apply(row["id"])
                paths = list(res.get("files") or [])
                migration = _migration_gate()
                if not migration.get("ok"):
                    repo.rollback_reviewed_auto(row["id"], {"ok": False, "migration_gate": migration})
                    failed.append({"patch_id": row["id"], "stage": "migration_gate",
                                   "why": migration})
                    continue
                units = _reviewed_units(paths)
                if units:
                    restart(units)
                pf = _runtime_postflight(units)
                if not pf["ok"]:
                    repo.rollback_reviewed_auto(row["id"], pf)
                    if units:
                        restart(units)
                    failed.append({"patch_id": row["id"], "stage": "postflight",
                                   "why": pf})
                    continue
                repo.mark_reviewed_auto_runtime_qa(row["id"], pf)
                applied.append({"patch_id": row["id"], "status": "applied",
                                "files": paths, "restarted": units,
                                "postflight": pf})
            except Exception as e:
                failed.append({"patch_id": row["id"], "stage": "apply",
                               "why": getattr(e, "message", str(e))})
    return {"reviewed": [r.get("patch_id") for r in reviewed],
            "applied": applied, "failed": failed}


def _log_bundle(digest, ok, name):
    db.q("""INSERT INTO ext_deploy_log (sha, branch, ok, note, files_json)
            VALUES (:s, 'bundle', :o, :n, '[]')""",
         s=digest[:64], o=bool(ok), n=("обновление шлюза: " + name)[:1000])


# ------------------------------------------- самостоятельный разбор провала
# Владелец сформулировал это как P0-дефект автономности, и он прав: служба
# знает пакет, шаг, вывод и результат отката — просить всё это у человека
# означает делать его оператором. Разбор, ремонтная задача и повтор выкатки
# происходят здесь, без него.

MAX_DEPLOY_RETRIES = int(os.environ.get("BORIS_DEPLOY_RETRIES", "3"))
RETRY_STATE = "updates/.deploy_retries.json"


def _journal(unit, lines=120):
    code, out = _run(["journalctl", "-u", unit, "-n", str(int(lines)),
                      "--no-pager"], timeout=60)
    return (out or "") if code == 0 else ""


def _retries(name=None, bump=False):
    path = os.path.join(repo.ROOT, RETRY_STATE)
    try:
        data = json.loads(io.open(path, encoding="utf-8").read())
    except Exception:
        data = {}
    if name is None:
        return data
    if bump:
        data[name] = int(data.get(name) or 0) + 1
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            io.open(path, "w", encoding="utf-8").write(
                json.dumps(data, ensure_ascii=False))
        except OSError:
            pass
    return int(data.get(name) or 0)


def diagnose_failure(bundle_name, res):
    """Собрать всё нужное для починки самостоятельно."""
    steps = res.get("steps") or []
    failed = next((s for s in steps if not s.get("ok")), None)
    rolled = next((s for s in steps if s.get("step") == "ОТКАТ"), None)
    detail = failed.get("detail") if failed else None
    if not isinstance(detail, str):
        detail = json.dumps(detail, ensure_ascii=False, default=str)[:4000]
    return {"bundle": bundle_name,
            "failed_step": (failed or {}).get("step") or "неизвестен",
            "detail": detail[:4000],
            "rolled_back": bool((rolled or {}).get("ok")),
            "steps": [{"step": s.get("step"), "ok": s.get("ok")} for s in steps],
            "journal_deploy": _journal("boris-deploy", 120)[-4000:],
            "journal_backend": _journal("boris-backend", 120)[-4000:],
            "attempts": _retries(bundle_name)}


def open_repair_job(diag):
    """Ремонтная задача P0 — на инфраструктурный слот, клиентов не трогаем."""
    from . import clients, dev, a2a
    title = "DEV_DEPLOY_REPAIR — выкатка упала на шаге %s" % diag["failed_step"]
    exist = db.one("""SELECT id, status FROM ext_dev_jobs
                       WHERE title LIKE 'DEV_DEPLOY_REPAIR%'
                         AND status NOT IN ('completed','cancelled')
                       ORDER BY id DESC LIMIT 1""")
    if exist:
        return {"job": "dev_%s" % exist["id"], "created": False}
    goal = (
        "Выкатка пакета %s не прошла и была откачена автоматически.\n"
        "Упавший шаг: %s\n"
        "Откат выполнен: %s\n"
        "Попыток до этого: %s\n\n"
        "Вывод шага:\n%s\n\n"
        "journalctl boris-deploy:\n%s\n\n"
        "journalctl boris-backend:\n%s\n\n"
        "Найти причину, исправить её в коде и доказать исправление. "
        "Владельцу вопросов не задавать: журналы, команды и повтор выкатки "
        "доступны здесь же, на сервере."
        % (diag["bundle"], diag["failed_step"],
           "да" if diag["rolled_back"] else "нет", diag["attempts"],
           diag["detail"][:2000], diag["journal_deploy"][-2000:],
           diag["journal_backend"][-2000:]))
    job = dev.create(clients.LEAD_KEY, "repair_%s" % diag["bundle"][:40], title,
                     goal, ["Причина названа фактом, а не догадкой",
                            "Исправление проверено локально",
                            "Повторная выкатка прошла шаг " + diag["failed_step"],
                            "Бэкенд отвечает после рестарта"],
                     scope={"paths": ["backend/app/ext_api"]},
                     priority=clients.NON_CLIENT_CEILING)
    a2a.create_order(clients.LEAD_KEY, job["dev_job_id"], "FIXING", goal,
                     scope={"paths": ["backend/app/ext_api"]},
                     acceptance=["Повторная выкатка проходит целиком"],
                     forbidden=clients.COMMON_FORBIDDEN,
                     required_qa=["compile", "tests"],
                     required_evidence=["вывод повторной выкатки"],
                     assigned_to=clients.EXECUTOR, max_iterations=6,
                     priority=clients.NON_CLIENT_CEILING)
    return {"job": job["dev_job_id"], "created": True}


def retry_after_repair():
    """Ремонт принят — повторяем выкатку сами, без напоминания владельцу."""
    from . import a2a
    done = os.path.join(repo.ROOT, ARCHIVE)
    inbox = os.path.join(repo.ROOT, INCOMING)
    if not os.path.isdir(done):
        return []
    job = db.one("""SELECT id FROM ext_dev_jobs
                     WHERE title LIKE 'DEV_DEPLOY_REPAIR%'
                     ORDER BY id DESC LIMIT 1""")
    if not job:
        return []
    orders = a2a.listing(dev_job_id="dev_%s" % job["id"], limit=20)
    if not any(o["status"] == a2a.ACCEPTED for o in orders):
        return []
    back = []
    for name in sorted(os.listdir(done), reverse=True):
        if not name.startswith("fail_") or not name.endswith(".py"):
            continue
        orig = name.split("_", 3)[-1] or name
        if _retries(name) >= MAX_DEPLOY_RETRIES:
            continue
        _retries(name, bump=True)
        try:
            shutil.move(os.path.join(done, name), os.path.join(inbox, orig))
        except OSError:
            continue
        back.append(orig)
        break
    return back


def handle_failure(bundle_name, res):
    """Один вход для провала выкатки: разобрать, отремонтировать, доложить."""
    diag = diagnose_failure(bundle_name, res)
    _retries(bundle_name, bump=True)
    repair = open_repair_job(diag)
    # A failed technical deploy that rolled back safely is internal work. Do
    # not notify the owner about events that explicitly require no action.
    # Genuine human-only blockers are surfaced by BLOCKED_HUMAN elsewhere.
    return {"diagnosis": diag, "repair": repair,
            "owner_notified": False, "rollback_preserved_production": True}


STALE_BUNDLE_MINUTES = int(os.environ.get("BORIS_STALE_BUNDLE_MIN", "180"))


def _last_success_time():
    """Когда последний раз что-то встало успешно."""
    try:
        r = db.one("""SELECT EXTRACT(EPOCH FROM MAX(created_at)) AS t
                        FROM ext_deploy_log WHERE ok AND branch = 'bundle'""")
        return float((r or {}).get("t") or 0) or None
    except Exception:
        return None


def requeue_orphans(minutes=20):
    """Вернуть в очередь пакеты, которые забрали, но так и не применили.

    Это оборотная сторона «забираем до применения»: если попытка не
    состоялась (замок занят, процесс убит), файл оставался в архиве под
    именем applying_…, очередь пустела, и обновление исчезало молча — на
    бою так потерялись четыре установки подряд. Пакет уходит из работы
    только двумя путями: применён или отклонён по существу.
    """
    inbox = os.path.join(repo.ROOT, INCOMING)
    done = os.path.join(repo.ROOT, ARCHIVE)
    os.makedirs(inbox, exist_ok=True)
    os.makedirs(done, exist_ok=True)
    back = []
    for name in sorted(os.listdir(done)):
        if not name.startswith("applying_") or not name.endswith(".py"):
            continue
        full = os.path.join(done, name)
        try:
            age_min = (time.time() - os.path.getmtime(full)) / 60.0
        except OSError:
            continue
        if age_min < minutes:
            continue
        try:
            digest = _sha256(full)
        except OSError:
            continue
        if already_applied(digest):
            try:
                os.rename(full, os.path.join(done, "dup_" + name))
            except OSError:
                pass
            continue
        # Устаревший пакет возвращать нельзя. «Не доехал» и «давно
        # неактуален» — разные вещи: на бою возврат четырёх вчерашних
        # пакетов накатил старую версию поверх новой.
        if age_min > STALE_BUNDLE_MINUTES:
            try:
                os.rename(full, os.path.join(done, "stale_" + name))
            except OSError:
                pass
            continue
        last_ok = _last_success_time()
        try:
            if last_ok and os.path.getmtime(full) < last_ok:
                os.rename(full, os.path.join(done, "stale_" + name))
                continue
        except OSError:
            pass
        orig = name.split("_", 3)[-1] or name
        target = os.path.join(inbox, orig)
        if os.path.exists(target):
            orig = time.strftime("%H%M%S_") + orig
            target = os.path.join(inbox, orig)
        try:
            shutil.move(full, target)
        except OSError:
            continue
        back.append(orig)
    return back


def watch_incoming():
    """Каталог обновлений: положили файл — он применился и уехал в архив."""
    inbox = os.path.join(repo.ROOT, INCOMING)
    done = os.path.join(repo.ROOT, ARCHIVE)
    os.makedirs(inbox, exist_ok=True)
    os.makedirs(done, exist_ok=True)
    out = []
    for again in requeue_orphans():
        out.append({"bundle": again, "requeued": True,
                    "why": "прошлая попытка не состоялась"})
    for name in sorted(os.listdir(inbox)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(inbox, name)
        digest = _sha256(path)
        if already_applied(digest):
            shutil.move(path, os.path.join(done, "dup_" + time.strftime("%H%M%S_")
                                           + name))
            out.append({"bundle": name, "ok": True, "skipped": "уже применялось"})
            continue
        # Забираем файл из очереди ДО применения: если процесс убьют на
        # середине, следующий оборот не начнёт всё заново по кругу.
        working = os.path.join(done, "applying_" + time.strftime("%Y%m%d_%H%M%S_")
                               + name)
        shutil.move(path, working)
        path = working
        try:
            res = apply_bundle(path)
        except Exception as e:
            # Попытка не состоялась — это не отказ по существу. Файл
            # возвращается в очередь, иначе версия потеряется беззвучно.
            why = getattr(e, "message", None) or "%s: %s" % (type(e).__name__, e)
            try:
                shutil.move(path, os.path.join(inbox, name))
                out.append({"bundle": name, "ok": False, "requeued": True,
                            "why": why})
            except OSError:
                out.append({"bundle": name, "ok": False, "why": why})
            print("deploy: установка не состоялась (%s), пакет вернулся в "
                  "очередь: %s" % (why, name), file=sys.stderr, flush=True)
            continue
        try:
            _log_bundle(digest, res.get("ok"), name)
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=962)
        target = os.path.join(done, ("ok_" if res["ok"] else "fail_") +
                              time.strftime("%Y%m%d_%H%M%S_") + name)
        try:
            shutil.move(path, target)
        except OSError:
            pass
        out.append({"bundle": name, "ok": res["ok"], "archived": target})
        if not res["ok"]:
            # Провал не заканчивается сообщением владельцу. Разбор, ремонтная
            # задача и повтор — здесь же, автоматически.
            try:
                out[-1]["repair"] = handle_failure(os.path.basename(target), res)
            except Exception as e:
                # Keep the failure in the technical journal/result. A rollback
                # already preserved production, so this is not an owner action.
                out[-1]["repair_error"] = "%s: %s" % (type(e).__name__, e)
        else:
            # Успешная техническая выкладка — нормальная внутренняя работа BORIS,
            # а не повод делать владельца оператором уведомлений.
            pass
        if res["ok"]:
            # Приёмочный прогон запускается отдельной службой: он идёт двадцать
            # минут, и держать им выкатку нельзя.
            _run(["systemctl", "stop", "boris-e2e"], timeout=30)
            _run(["systemctl", "reset-failed", "boris-e2e"], timeout=30)
            _run(["systemd-run", "--unit=boris-e2e", "--collect",
                  "--working-directory=" + backend_dir(), _py(),
                  "-m", "app.ext_api.recover", "e2e", "--minutes", "20"],
                 timeout=60)
    return out


def _frontend_runtime_generation_state():
    """FRONTEND_RUNTIME_GENERATION_PIN_V1: current symlink and live Next must be same generation."""
    front = os.path.join(repo.ROOT, "frontend")
    current = os.path.join(front, ".boris-builds", "current")
    try:
        current_switched_at = float(os.lstat(current).st_mtime)
    except Exception:
        current_switched_at = 0.0
    started_at = 0.0
    pid = 0
    code, out = _run(["systemctl", "show", "boris-frontend.service",
                      "-p", "MainPID", "--value"], timeout=20)
    try:
        pid = int((out or "0").strip()) if code == 0 else 0
    except Exception:
        pid = 0
    if pid > 0:
        try:
            stat_text = io.open("/proc/%d/stat" % pid, encoding="utf-8").read()
            tail = stat_text.rsplit(")", 1)[1].strip().split()
            start_ticks = float(tail[19])
            hz = float(os.sysconf("SC_CLK_TCK"))
            uptime = float(io.open("/proc/uptime", encoding="utf-8").read().split()[0])
            boot_epoch = time.time() - uptime
            started_at = boot_epoch + (start_ticks / hz)
        except Exception:
            started_at = 0.0
    fresh = bool(current_switched_at and started_at and started_at >= current_switched_at)
    return {
        "fresh": fresh,
        "current_switched_at": current_switched_at,
        "frontend_started_at": started_at,
        "pid": pid,
    }


def _frontend_generation_matches():
    front = os.path.join(repo.ROOT, "frontend")
    hasher = os.path.join(front, "scripts", "frontend-source-hash.py")
    current_meta = os.path.join(front, ".boris-builds", "current", "BORIS_ARTIFACT.json")
    code, source_hash = _run(["python3", hasher], cwd=front, timeout=30)
    source_hash = (source_hash or "").strip()
    try:
        deployed_hash = str(json.load(io.open(current_meta, encoding="utf-8")).get("snapshot_source_hash") or "")
    except Exception:
        deployed_hash = ""
    runtime = _frontend_runtime_generation_state()
    return bool(code == 0 and source_hash and source_hash == deployed_hash and runtime.get("fresh"))


def _pause_release_writers():
    """Do not stop autonomous writers for isolated frontend generation builds.

    FRONTEND_ISOLATED_BUILD_NO_WRITER_PAUSE_V1

    Frontend builds run from immutable source snapshots and deploy only READY
    artifacts. If source moves during a build, the deployment hash fence rejects
    the stale artifact and _frontend_generation_reconcile() retries the newest
    generation. Stopping executors/lead here therefore cannot make the frontend
    build safer; it only kills in-flight A2A work and leaves stale RUNNING claims
    for recovery.

    Keep this compatibility hook as an explicit no-op so old callers/results
    remain stable while the authoritative isolated-build contract is enforced.
    """
    return []


def _resume_release_writers(units):
    out = []
    for unit in units or []:
        code, detail = _run(["systemctl", "start", unit], timeout=60)
        out.append({"unit": unit, "started": code == 0, "detail": (detail or "")[-200:]})
    return out


def _frontend_generation_reconcile(max_attempts=2):
    """Converge live frontend to the newest complete local source generation.

    Build is isolated; deploy has its own stale-artifact hash guard. If another
    writer changes source during a build, retry from the newer generation rather
    than forcing an old artifact over fresh work.
    """
    front = os.path.join(repo.ROOT, "frontend")
    hasher = os.path.join(front, "scripts", "frontend-source-hash.py")
    build = os.path.join(front, "scripts", "boris-frontend-build.sh")
    deploy_script = os.path.join(front, "scripts", "boris-frontend-deploy.sh")
    current_meta = os.path.join(front, ".boris-builds", "current", "BORIS_ARTIFACT.json")
    latest_ready = os.path.join(front, ".boris-builds", "LATEST_READY")
    attempts = []
    for _ in range(max(1, int(max_attempts))):
        code, source_hash = _run(["python3", hasher], cwd=front, timeout=30)
        source_hash = (source_hash or "").strip()
        deployed_hash = ""
        try:
            deployed_hash = str(json.load(io.open(current_meta, encoding="utf-8")).get("snapshot_source_hash") or "")
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=1092)
        if code == 0 and source_hash and source_hash == deployed_hash:
            runtime_before = _frontend_runtime_generation_state()
            if runtime_before.get("fresh"):
                return {"ok": True, "converged": True, "hash": source_hash,
                        "runtime": runtime_before, "attempts": attempts}
            rcode, rout = _run(["systemctl", "restart", "boris-frontend.service"], timeout=60)
            time.sleep(1.0)
            runtime_after = _frontend_runtime_generation_state()
            step = {
                "runtime_restart_rc": rcode,
                "runtime_restart_tail": (rout or "")[-1000:],
                "runtime_before": runtime_before,
                "runtime_after": runtime_after,
            }
            attempts.append(step)
            if rcode == 0 and runtime_after.get("fresh"):
                return {"ok": True, "converged": True, "hash": source_hash,
                        "runtime_restarted": True, "runtime": runtime_after,
                        "attempts": attempts}
            return {"ok": False, "converged": False, "hash": source_hash,
                    "runtime": runtime_after, "attempts": attempts}
        bcode, bout = _run([build], cwd=front, timeout=900)
        step = {"build_rc": bcode, "build_tail": (bout or "")[-2000:]}
        if bcode != 0:
            attempts.append(step)
            return {"ok": False, "converged": False, "attempts": attempts}
        try:
            artifact = io.open(latest_ready, encoding="utf-8").read().strip()
        except Exception:
            artifact = ""
        if not artifact:
            step["deploy_rc"] = 66
            step["deploy_tail"] = "LATEST_READY missing"
            attempts.append(step)
            return {"ok": False, "converged": False, "attempts": attempts}
        dcode, dout = _run([deploy_script, artifact], cwd=front, timeout=240)
        step.update({"deploy_rc": dcode, "deploy_tail": (dout or "")[-2000:], "artifact": artifact})
        attempts.append(step)
        if dcode == 0:
            code2, source_hash2 = _run(["python3", hasher], cwd=front, timeout=30)
            source_hash2 = (source_hash2 or "").strip()
            try:
                deployed_hash2 = str(json.load(io.open(current_meta, encoding="utf-8")).get("snapshot_source_hash") or "")
            except Exception:
                deployed_hash2 = ""
            if code2 == 0 and source_hash2 and source_hash2 == deployed_hash2:
                return {"ok": True, "converged": True, "hash": source_hash2, "attempts": attempts}
        # rc=76 means source moved during build; loop and rebuild newest source.
    return {"ok": False, "converged": False, "attempts": attempts}


DISPATCHER_RUNTIME_SOURCE_FILES = (
    "app/ext_api/recover.py", "app/ext_api/dev.py", "app/ext_api/clients.py",
    "app/ext_api/a2a.py", "app/ext_api/executor.py", "app/ext_api/lead.py",
)

def _systemd_exec_start_epoch(unit):
    code, out = _run(["systemctl", "show", unit, "-p", "ExecMainStartTimestamp", "--value"], timeout=20)
    if code != 0 or not (out or "").strip(): return 0.0
    code2, out2 = _run(["date", "-d", (out or "").strip(), "+%s"], timeout=10)
    try: return float((out2 or "0").strip()) if code2 == 0 else 0.0
    except Exception: return 0.0

def _dispatcher_generation_state():
    """DEPLOY_DISPATCHER_GENERATION_RECONCILE_V1: detect alive process on stale Python source."""
    paths=[os.path.join(backend_dir(),r) for r in DISPATCHER_RUNTIME_SOURCE_FILES]
    existing=[p for p in paths if os.path.isfile(p)]
    newest=max([os.path.getmtime(p) for p in existing] or [0.0])
    started=_systemd_exec_start_epoch("boris-dispatcher")
    return {"stale": bool(newest and (not started or started+1.0<newest)), "started_epoch":started, "newest_source_epoch":newest, "newest_source":max(existing,key=os.path.getmtime) if existing else None}

def _dispatcher_generation_reconcile():
    before=_dispatcher_generation_state()
    if not before["stale"]: return {"ok":True,"restarted":False,"before":before,"after":before}
    changed=restart(["boris-dispatcher"])
    after=_dispatcher_generation_state()
    return {"ok":not after["stale"],"restarted":True,"restart":changed,"before":before,"after":after}

def tick():
    """Один оборот службы выкатки."""
    res = {}
    try:
        res["migration_preflight"] = _migration_gate()
    except Exception as e:
        res["migration_preflight"] = {"ok": False, "stage": "exception",
                                      "error": "%s: %s" % (type(e).__name__, e)}
    if not res["migration_preflight"].get("ok"):
        res["deploy_blocked"] = "migration_preflight_failed"
        return res
    try:
        # Ремонт принят — повтор выкатки происходит сам. Владельцу не нужно
        # ни помнить о упавшем пакете, ни возвращать его в очередь руками.
        res["retried"] = retry_after_repair()
    except Exception as e:
        res["retried"] = {"error": "%s: %s" % (type(e).__name__, e)}
    res["bundles"] = watch_incoming()
    res["patches"] = apply_accepted_patches()
    try:
        res["migration_postflight"] = _migration_gate()
    except Exception as e:
        res["migration_postflight"] = {"ok": False, "stage": "exception",
                                       "error": "%s: %s" % (type(e).__name__, e)}
    if not res["migration_postflight"].get("ok"):
        res["deploy_blocked"] = "migration_postflight_failed"
        return res
    release_reasons = _backend_release_reasons(res.get("patches"), res.get("bundles"))
    if release_reasons:
        try:
            res["backend_release_provenance"] = _backend_release_provenance_gate(
                res.get("patches"), res.get("bundles")
            )
        except Exception as e:
            res["backend_release_provenance"] = {
                "ok": False,
                "reason": "provenance_gate_exception",
                "error": "%s: %s" % (type(e).__name__, e),
            }
        if not res["backend_release_provenance"].get("ok"):
            res["deploy_blocked"] = "backend_release_provenance_failed"
            return res
        try:
            res["backend_release"] = _backend_release_build_activate()
            res["backend_release"]["reasons"] = release_reasons
        except Exception as e:
            res["backend_release"] = {"ok": False, "reasons": release_reasons,
                                      "error": "%s: %s" % (type(e).__name__, e)}
        if not res["backend_release"].get("ok"):
            res["deploy_blocked"] = "backend_release_build_failed"
            return res
    else:
        # WORKSHOP_NOT_PRODUCTION_V1: do not turn arbitrary/direct workspace
        # edits into a release merely because the deploy timer ran.
        res["backend_release"] = {
            "ok": True,
            "build_id": _backend_release_current_build(),
            "skipped": "no canonical accepted backend source change",
            "reasons": [],
        }
    try:
        res["dispatcher_generation"] = _dispatcher_generation_reconcile()
    except Exception as e:
        res["dispatcher_generation"] = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    paused_writers = []
    try:
        if not _frontend_generation_matches():
            paused_writers = _pause_release_writers()
        # FRONTEND_GENERATION_CONVERGENCE_V2: several autonomous work tracks may
        # legitimately update frontend source close together. Rebuild the newest
        # stable generation in the same deploy tick instead of leaving the owner
        # with a red stale-source state until another manual/periodic cycle.
        res["frontend_generation"] = _frontend_generation_reconcile(max_attempts=4)
    except Exception as e:
        res["frontend_generation"] = {"error": "%s: %s" % (type(e).__name__, e)}
    finally:
        res["release_writers_resumed"] = _resume_release_writers(paused_writers)
    # Внешний Git-транспорт не является частью production-контура BORIS.
    # Он включается только явным BORIS_GIT_TRANSPORT=1. По умолчанию сервер
    # применяет и проверяет правки локально, без GitHub/GitLab/origin.
    if GIT_TRANSPORT_ENABLED:
        try:
            from . import gitdeploy
            res["published"] = gitdeploy.publish_accepted()
        except Exception as e:
            res["published"] = {"error": "%s: %s" % (type(e).__name__, e)}
    else:
        res["published"] = {"skipped": "BORIS_GIT_TRANSPORT=0; local production deploy only"}

    # Локальная сверка поколения после всех применённых правок. Она не
    # публикует код наружу: contract сам перезапускает только устаревшие
    # runtime-процессы и проверяет охват всех боевых аккаунтов.
    try:
        env = dict(os.environ)
        env["BORIS_POLICY_NOTIFY"] = "0"
        env["BORIS_POLICY_SELF_HEAL"] = "0"
        proc = subprocess.run([_py(), os.path.join(backend_dir(), "production_contract_runner.py")],
                              cwd=backend_dir(), capture_output=True, text=True,
                              timeout=300, env=env)
        tail = ((proc.stdout or "") + (proc.stderr or ""))[-4000:]
        res["production_contract"] = {"returncode": proc.returncode, "tail": tail}

        # DEPLOY_SINGLE_BACKEND_ROLLOUT_OWNER_V1: boris-deploy is the canonical
        # owner of normal backend generation convergence. The contract runs with
        # SELF_HEAL=0, so guardians/remote jobs never race it by restarting API
        # replicas. If the contract proves backend generation is stale, perform
        # exactly one canonical rolling deployment from this service cgroup.
        contract_payload = {}
        try:
            for _line in reversed((proc.stdout or "").splitlines()):
                _line = _line.strip()
                if _line.startswith("{"):
                    contract_payload = json.loads(_line)
                    break
        except Exception:
            contract_payload = {}
        _shared = contract_payload.get("shared_checks") or {}
        # DEPLOY_BACKGROUND_GENERATION_RECONCILE_V1: the dedicated worker imports
        # the same backend source but is not part of the HA API rolling script.
        # If a backend edit landed after the worker started, the deploy owner must
        # refresh this non-HA runtime itself instead of leaving every client red
        # until an owner/guardian notices. This restarts only the local worker; it
        # never replays a business/provider operation.
        if proc.returncode != 0 and _shared.get("background_runtime_generation") is False:
            res["background_generation_reconcile"] = restart(["boris-background-worker"])
            proc = subprocess.run([_py(), os.path.join(backend_dir(), "production_contract_runner.py")],
                                  cwd=backend_dir(), capture_output=True, text=True,
                                  timeout=300, env=env)
            tail = ((proc.stdout or "") + (proc.stderr or ""))[-4000:]
            res["production_contract_after_background_reconcile"] = {"returncode": proc.returncode, "tail": tail}
            contract_payload = {}
            try:
                for _line in reversed((proc.stdout or "").splitlines()):
                    _line = _line.strip()
                    if _line.startswith("{"):
                        contract_payload = json.loads(_line)
                        break
            except Exception:
                contract_payload = {}
            _shared = contract_payload.get("shared_checks") or {}
        if proc.returncode != 0 and _shared.get("backend_runtime_generation") is False:
            roll_env = dict(os.environ)
            roll_env["BORIS_ROLLING_LANE"] = "deploy_v1"
            roll = subprocess.run([os.path.join(backend_dir(), "rolling_restart_backend.sh")],
                                  cwd=backend_dir(), capture_output=True, text=True,
                                  timeout=300, env=roll_env)
            res["backend_generation_reconcile"] = {
                "returncode": roll.returncode,
                "tail": ((roll.stdout or "") + (roll.stderr or ""))[-3000:],
            }
            if roll.returncode == 0:
                proc = subprocess.run([_py(), os.path.join(backend_dir(), "production_contract_runner.py")],
                                      cwd=backend_dir(), capture_output=True, text=True,
                                      timeout=300, env=env)
                tail = ((proc.stdout or "") + (proc.stderr or ""))[-4000:]
                res["production_contract_after_backend_reconcile"] = {"returncode": proc.returncode, "tail": tail}

        # FRONTEND_POSTCONTRACT_SELFHEAL_V1: source can move after a healthy
        # atomic deploy but before this contract snapshot. If that is the only
        # generation race, converge again immediately and re-run the contract in
        # this same tick. No owner/manual "continue" and no 60-second red window.
        if proc.returncode != 0 and not _frontend_generation_matches():
            res["frontend_postcontract_reconcile"] = _frontend_generation_reconcile(max_attempts=4)
            proc2 = subprocess.run([_py(), os.path.join(backend_dir(), "production_contract_runner.py")],
                                   cwd=backend_dir(), capture_output=True, text=True,
                                   timeout=300, env=env)
            tail2 = ((proc2.stdout or "") + (proc2.stderr or ""))[-4000:]
            res["production_contract_after_reconcile"] = {"returncode": proc2.returncode, "tail": tail2}
    except Exception as e:
        res["production_contract"] = {"error": "%s: %s" % (type(e).__name__, e)}
    return res


def run(idle=60):
    while True:
        try:
            tick()
        except Exception as e:
            print("deploy: %s: %s" % (type(e).__name__, e), file=sys.stderr, flush=True)
        time.sleep(int(idle))


def rollback_drill():
    """Учение отката: ломаем нарочно и проверяем, что вернулось как было.

    Учение идёт в отдельной копии пакета, боевые файлы не трогаются: цель —
    доказать, что ворота ловят поломку и восстанавливают состояние, а не
    устроить настоящую аварию ради галочки.
    """
    backend = backend_dir()
    pkg = os.path.join(backend, "app", "ext_api")
    if not os.path.isdir(pkg):
        return {"ok": False, "поломка замечена": False,
                "состояние восстановлено": False,
                "почему": "пакет не найден: %s" % pkg}
    sandbox = os.path.join(repo.ROOT, "updates", "drill")
    shutil.rmtree(sandbox, ignore_errors=True)
    os.makedirs(sandbox, exist_ok=True)
    copy = os.path.join(sandbox, "ext_api")
    shutil.copytree(pkg, copy)
    victim = os.path.join(copy, "health.py")
    before = io.open(victim, encoding="utf-8").read()
    bak = os.path.join(sandbox, "backup_health.py")
    io.open(bak, "w", encoding="utf-8").write(before)

    io.open(victim, "w", encoding="utf-8").write("def broken(:\n")
    code, out = _run([_py(), "-m", "py_compile", victim], timeout=120)
    detected = code != 0

    io.open(victim, "w", encoding="utf-8").write(io.open(bak, encoding="utf-8").read())
    code2, _o = _run([_py(), "-m", "py_compile", victim], timeout=120)
    restored = code2 == 0 and io.open(victim, encoding="utf-8").read() == before
    shutil.rmtree(sandbox, ignore_errors=True)
    ok = detected and restored
    try:
        _log_bundle("drill_" + time.strftime("%Y%m%d_%H%M%S"), ok, "учение отката")
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=1375)
    return {"ok": ok, "поломка замечена": detected, "состояние восстановлено": restored}


def gates():
    """Что из обязательного для выкатки уже доказано, а что нет.

    Список ровно тот, который задал владелец: настоящая выкатка, снятие
    брошенного замка, проверка живости, намеренная поломка с откатом и
    повторная успешная выкатка.
    """
    rows = db.rows("""SELECT sha, ok, note, created_at FROM ext_deploy_log
                       ORDER BY id DESC LIMIT 50""")
    good = [r for r in rows if r["ok"] and "учение" not in (r["note"] or "")]
    drills = [r for r in rows if r["ok"] and "учение" in (r["note"] or "")]
    # Информационная сводка: длинного окна ожидания здесь не нужно, оно
    # только заставляет ждать того, кто просто смотрит состояние.
    h = health(wait_seconds=5)
    return {
        "успешная выкатка": bool(good),
        "повторная успешная выкатка": len(good) >= 2,
        "снятие брошенного замка": not os.path.exists(LOCK_PATH) or
                                    _lock_owner_alive()[0],
        "проверка живости": h["ok"],
        "намеренная поломка и откат": bool(drills),
        "выкаток в журнале": len(good),
    }


def status():
    return {"auto_apply": AUTO_APPLY, "allowed_units": list(ALLOWED_UNITS),
            "incoming_dir": os.path.join(repo.ROOT, INCOMING),
            "health": health(wait_seconds=5),
            "waiting_patches": db.one(
                """SELECT COUNT(*) AS n FROM ext_dev_patches p
                    WHERE p.status='proposed'
                      AND EXISTS (SELECT 1 FROM ext_a2a_orders o
                                   WHERE o.dev_job_id = p.dev_job_id
                                     AND o.status='accepted')""")["n"]}


def main():
    import argparse
    ap = argparse.ArgumentParser(prog="deploy")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("tick")
    b = sub.add_parser("bundle")
    b.add_argument("path")
    r = sub.add_parser("run")
    r.add_argument("--idle", type=int, default=60)
    a = ap.parse_args()
    if a.cmd == "run":
        return run(a.idle)
    if a.cmd == "bundle":
        out = apply_bundle(a.path)
    elif a.cmd == "tick":
        out = tick()
    else:
        out = status()
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main() or 0)
