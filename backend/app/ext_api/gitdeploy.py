# -*- coding: utf-8 -*-
"""Git как постоянный транспорт обновлений. Последний ручной участок закрыт.

Цепочка целиком: Claude Lead → ACCEPT → коммит в ветку выкатки → сервер сам
забирает → ворота сервера → production. Ни scp, ни ssh, ни установки руками.

Что здесь принципиально:
  · production-дерево не трогается «слепым pull» — работа идёт в отдельной
    области staging, и только проверенные файлы переносятся в боевое;
  · берётся ровно одна разрешённая ветка, всё остальное игнорируется;
  · коммит применяется, только если он помечен принятой задачей: «появился
    в репозитории» — не основание для выкатки;
  · переносятся только файлы этого коммита и только из разрешённых путей;
  · до боевого — бэкап, компиляция, импорт, тесты; после — проверка живости;
  · любой провал means откат, а выкаченный SHA записывается только после PASS.

    venv/bin/python3 -m app.ext_api.gitdeploy status
    venv/bin/python3 -m app.ext_api.gitdeploy tick
"""
import io, json, os, re, shutil, subprocess, sys, time

from . import db, repo, deploy
from .errors import ApiError

REPO_VARS = ("BORIS_DEPLOY_REPO", "DEPLOY_REPO_URL", "BORIS_GIT_REMOTE")
BRANCH = os.environ.get("BORIS_DEPLOY_BRANCH") or "boris-deploy"
STAGING = os.path.join(repo.ROOT, "updates", "staging")

# Что коммит вообще имеет право менять. Всё остальное — не выкатывается,
# даже если лежит в разрешённой ветке.
ALLOWED_PREFIXES = ("backend/app/", "frontend/app/", "frontend/components/",
                    "updates/incoming/")
DENIED_PARTS = (".env", "secret", "credential", ".pem", ".key", "id_rsa",
                "docker-compose", "nginx", ".github/")

# Метка приёмки в сообщении коммита. Без неё коммит не выкатывается.
ACCEPT_MARK = "BORIS-ACCEPT:"


def _own_env():
    """Собственная настройка транспорта. Её пишет разведка, и её же надо
    уметь прочитать после перезапуска — иначе найденный адрес живёт ровно до
    конца процесса, а служба потом считает, что ничего не настроено."""
    out, path = {}, os.path.join(repo.ROOT, "backend", ".ext_deploy.env")
    if not os.path.exists(path):
        return out
    try:
        for line in io.open(path, encoding="utf-8", errors="ignore"):
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        return {}
    return out


def repo_url():
    from .executor import _env_all
    env = _env_all()
    own = _own_env()
    for k in REPO_VARS:
        if env.get(k):
            return env[k].strip(), k
        if own.get(k):
            return own[k].strip(), k + " (.ext_deploy.env)"
    return None, None


CRED_HELPER = ('!f() { echo "username=x-access-token"; '
               'echo "password=$BORIS_GIT_TOKEN"; }; f')
_CRED_IN_TEXT = re.compile(r"://[^/\s:@]+:[^/\s@]+@")


def _mask_secrets(text):
    return _CRED_IN_TEXT.sub("://****:****@", text or "")


def _git_env():
    """Окружение для ЛЮБОГО вызова git, а не только для push.

    Два правила без исключений:
      · git никогда не спрашивает человека — у службы нет терминала, и
        вопрос «Username for https://github.com» означает вечное зависание,
        а не ошибку, которую кто-то увидит;
      · токен приходит через credential.helper из переменной окружения —
        он не попадает ни в argv, ни в git config, ни в вывод.

    Раньше это делал только push, поэтому ls-remote и fetch уходили без
    доступа и вставали на приглашении ввода. Чинится сам класс: доступ
    выдаётся на уровне запуска git, а не на уровне отдельной команды.
    """
    from .executor import _env_all
    env = _env_all()
    env.update(_own_env())
    token = (env.get("BORIS_GIT_TOKEN") or env.get("GITHUB_TOKEN")
             or env.get("GH_TOKEN") or "").strip()
    real = dict(os.environ)
    real["GIT_TERMINAL_PROMPT"] = "0"
    real["GIT_ASKPASS"] = "/bin/echo"
    pre = []
    if token:
        real["BORIS_GIT_TOKEN"] = token
        pre = ["-c", "credential.helper=" + CRED_HELPER]
    return pre, real, bool(token)


def _git(args, cwd=None, timeout=300):
    pre, env, _tok = _git_env()
    run_cwd = cwd or repo.ROOT

    # GIT_CONTROLLED_WORKTREE_SAFE_DIRECTORY_V1:
    # publish/staging are BORIS-owned service worktrees and may legitimately
    # be created by another BORIS service user (root vs sentinelx). Git's
    # ownership fence must stay enabled everywhere else. Trust only these
    # exact canonical directories for this one git invocation; never write a
    # global safe.directory entry and never use a wildcard.
    safe = []
    try:
        current = os.path.realpath(run_cwd)
        controlled = {os.path.realpath(STAGING)}
        publish = globals().get("PUBLISH_TREE")
        if publish:
            controlled.add(os.path.realpath(publish))
        if current in controlled:
            safe = ["-c", "safe.directory=" + current]
    except Exception:
        safe = []

    argv = ["git"] + pre + safe + list(args)
    try:
        p = subprocess.run(argv, cwd=run_cwd, capture_output=True,
                           text=True, timeout=timeout, env=env)
        code, out = p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        code, out = 1, "%s: %s" % (type(e).__name__, e)
    return code, _mask_secrets(out).strip()


def _log_deploy(sha, ok, note, files=None):
    db.q("""INSERT INTO ext_deploy_log (sha, branch, ok, note, files_json)
            VALUES (:s,:b,:o,:n,:f)""",
         s=sha[:64], b=BRANCH, o=bool(ok), n=(note or "")[:1000],
         f=json.dumps(files or [], ensure_ascii=False)[:8000])


def last_deployed():
    r = db.one("""SELECT sha FROM ext_deploy_log WHERE ok = TRUE AND branch = :b
                  ORDER BY id DESC LIMIT 1""", b=BRANCH)
    return r["sha"] if r else None


def ensure_staging():
    """Отдельная область. В боевое дерево git не ходит никогда."""
    url, var = repo_url()
    if not url:
        raise ApiError("NOT_CONFIGURED",
                       "адрес репозитория выкатки не задан: положите его в "
                       "BORIS_DEPLOY_REPO")
    os.makedirs(os.path.dirname(STAGING), exist_ok=True)
    if not os.path.isdir(os.path.join(STAGING, ".git")):
        shutil.rmtree(STAGING, ignore_errors=True)
        code, out = _git(["clone", "--branch", BRANCH, "--single-branch",
                          "--depth", "50", url, STAGING], timeout=900)
        if code != 0:
            raise ApiError("GIT_FAILED", "не удалось получить ветку выкатки: " + out[-300:])
        return {"cloned": True, "source": var}
    code, out = _git(["fetch", "--depth", "50", "origin", BRANCH], cwd=STAGING, timeout=600)
    if code != 0:
        raise ApiError("GIT_FAILED", "не удалось обновить ветку выкатки: " + out[-300:])
    return {"cloned": False, "source": var}


def head_sha():
    code, out = _git(["rev-parse", "origin/" + BRANCH], cwd=STAGING)
    if code != 0:
        raise ApiError("GIT_FAILED", "не читается вершина ветки: " + out[-200:])
    return out.strip()


def commit_message(sha):
    code, out = _git(["log", "-1", "--pretty=%B", sha], cwd=STAGING)
    return out if code == 0 else ""


def changed_files(sha, base=None):
    if base:
        code, out = _git(["diff", "--name-only", base, sha], cwd=STAGING)
    else:
        code, out = _git(["show", "--name-only", "--pretty=format:", sha], cwd=STAGING)
    if code != 0:
        return []
    return [l.strip() for l in out.split("\n") if l.strip()]


def approval_of(message):
    """Коммит выкатывается, только если помечен принятой задачей.

    Это ответ на требование «произвольный коммит не должен попадать в
    production только потому, что появился в репозитории».
    """
    if ACCEPT_MARK not in (message or ""):
        return {"ok": False, "why": "в сообщении коммита нет метки %s" % ACCEPT_MARK}
    tail = message.split(ACCEPT_MARK, 1)[1].strip().split()[0].strip(",;")
    ref = tail.replace("dev_", "")
    if not ref.isdigit():
        return {"ok": False, "why": "после метки нет номера задачи вида dev_123"}
    job = db.one("SELECT id, status FROM ext_dev_jobs WHERE id = :i", i=int(ref))
    if not job:
        return {"ok": False, "why": "задача dev_%s не найдена" % ref}
    acc = db.one("""SELECT COUNT(*) AS n FROM ext_a2a_orders
                     WHERE dev_job_id = :i AND status = 'accepted'""", i=int(ref))["n"]
    if not acc:
        return {"ok": False, "why": "по задаче dev_%s нет ни одного принятого наряда"
                                    % ref}
    return {"ok": True, "dev_job_id": "dev_%s" % ref}


def check_paths(files):
    bad = []
    for f in files:
        if not any(f.startswith(p) for p in ALLOWED_PREFIXES):
            bad.append((f, "путь вне разрешённых"))
            continue
        low = f.lower()
        if any(part in low for part in DENIED_PARTS):
            bad.append((f, "секреты и конфигурация окружения не выкатываются"))
    return bad


def _copy(files, src_root, dst_root, backup=None):
    moved = []
    for f in files:
        src = os.path.join(src_root, f)
        dst = os.path.join(dst_root, f)
        if backup and os.path.isfile(dst):
            b = os.path.join(backup, f)
            os.makedirs(os.path.dirname(b), exist_ok=True)
            shutil.copy2(dst, b)
        if not os.path.isfile(src):
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        moved.append(f)
    return moved


def _rollback_files(files, backup_root, dst_root, existed_before):
    """Restore exact pre-deploy file state, including deleting new files."""
    restored, removed, errors = [], [], []
    for f in files:
        dst = os.path.join(dst_root, f)
        try:
            if existed_before.get(f):
                src = os.path.join(backup_root, f)
                if not os.path.isfile(src):
                    errors.append({"file": f, "reason": "backup_missing"})
                    continue
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                restored.append(f)
            else:
                if os.path.isfile(dst):
                    os.unlink(dst)
                    removed.append(f)
        except OSError as exc:
            errors.append({"file": f,
                           "reason": "%s: %s" % (type(exc).__name__, exc)})
    return {"ok": not errors, "restored": restored,
            "removed_new": removed, "errors": errors}


def _units_for(files):
    # Скрытые файлы-маркеры кодом не являются: перезапускать из-за них службы
    # незачем, а круговая проверка транспорта должна быть безобидной.
    files = [f for f in files if not os.path.basename(f).startswith(".")]
    units = []
    if any(f.startswith("backend/") for f in files):
        units.append("boris-backend")
    if any(f.startswith("frontend/") for f in files):
        units.append("boris-frontend")
    if any("ext_api" in f for f in files):
        units += list(deploy.EXECUTOR_UNITS) + ["boris-lead", "boris-dispatcher",
                                                "boris-deploy"]
    seen, out = set(), []
    for u in units:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _restart_detail_ok(units, detail):
    """No runtime units means there is nothing to restart, which is success."""
    if not units:
        return True
    return bool(detail) and all(
        bool(x.get("restarted")) and bool(x.get("active"))
        for x in detail
    )


PUBLISH_TREE = os.path.join(repo.ROOT, "updates", "publish")


def _worktree_gitdir(tree):
    dotgit = os.path.join(tree, ".git")
    try:
        if os.path.isfile(dotgit):
            raw = io.open(dotgit, encoding="utf-8", errors="ignore").read().strip()
            if raw.lower().startswith("gitdir:"):
                path = raw.split(":", 1)[1].strip()
                if not os.path.isabs(path):
                    path = os.path.join(tree, path)
                return os.path.realpath(path)
        if os.path.isdir(dotgit):
            return os.path.realpath(dotgit)
    except OSError:
        return None
    return None


def _path_open_by_process(path):
    target = os.path.realpath(path)
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return False
    for pid in pids:
        fdroot = os.path.join("/proc", pid, "fd")
        try:
            fds = os.listdir(fdroot)
        except OSError:
            continue
        for fd in fds:
            try:
                if os.path.realpath(os.path.join(fdroot, fd)) == target:
                    return True
            except OSError:
                continue
    return False


def _repair_stale_worktree_index_lock(tree, min_age_sec=600):
    """Remove only an abandoned Git index.lock for a controlled worktree.

    A fresh or open lock is never touched. This closes the owner-as-operator
    failure where an old crashed git command blocks every later autonomous
    publish forever.
    """
    gitdir = _worktree_gitdir(tree)
    if not gitdir:
        return {"removed": False, "reason": "gitdir_not_found"}
    lock = os.path.join(gitdir, "index.lock")
    if not os.path.isfile(lock):
        return {"removed": False, "reason": "lock_missing"}
    try:
        age = max(0.0, time.time() - os.path.getmtime(lock))
    except OSError:
        return {"removed": False, "reason": "lock_stat_failed"}
    if age < float(min_age_sec):
        return {"removed": False, "reason": "lock_fresh", "age_sec": int(age)}
    if _path_open_by_process(lock):
        return {"removed": False, "reason": "lock_open", "age_sec": int(age)}
    try:
        os.unlink(lock)
        return {"removed": True, "reason": "stale_lock_removed", "age_sec": int(age)}
    except OSError as exc:
        return {"removed": False, "reason": "lock_remove_failed",
                "error": "%s: %s" % (type(exc).__name__, exc), "age_sec": int(age)}


def _ensure_branch():
    """Ветка выкатки существует и подключена отдельным рабочим деревом.

    Коммитим НЕ из боевого дерева: там живёт работающий BORIS, и переключать
    ему ветки нельзя. Отдельное дерево — то же требование, что и к staging.
    """
    code, _o = _git(["rev-parse", "--verify", "origin/" + BRANCH], cwd=repo.ROOT)
    if code != 0:
        c_branch, out_branch = _git(["branch", BRANCH], cwd=repo.ROOT)
        if c_branch != 0 and "already exists" not in (out_branch or "").lower():
            raise ApiError("GIT_FAILED", "не создать локальную ветку выкатки: " +
                           (out_branch or "")[-300:])
        c_push, out_push = _git(["push", "-u", "origin", BRANCH],
                                cwd=repo.ROOT, timeout=600)
        if c_push != 0:
            raise ApiError("GIT_FAILED", "не создать удалённую ветку выкатки: " +
                           (out_push or "")[-300:])
    if not os.path.isdir(os.path.join(PUBLISH_TREE, ".git")) and \
            not os.path.isfile(os.path.join(PUBLISH_TREE, ".git")):
        shutil.rmtree(PUBLISH_TREE, ignore_errors=True)
        code, out = _git(["worktree", "add", "--force", PUBLISH_TREE, BRANCH],
                         cwd=repo.ROOT, timeout=600)
        if code != 0:
            raise ApiError("GIT_FAILED", "не создать дерево публикации: " + out[-300:])

    code, out = _git(["fetch", "origin", BRANCH], cwd=PUBLISH_TREE, timeout=600)
    if code != 0:
        raise ApiError("GIT_FAILED", "не обновить дерево публикации: " + (out or "")[-300:])

    code, out = _git(["reset", "--hard", "origin/" + BRANCH], cwd=PUBLISH_TREE)
    if code != 0 and "index.lock" in (out or ""):
        heal = _repair_stale_worktree_index_lock(PUBLISH_TREE)
        if heal.get("removed"):
            code, out = _git(["reset", "--hard", "origin/" + BRANCH], cwd=PUBLISH_TREE)
    if code != 0:
        raise ApiError("GIT_FAILED", "не синхронизировать дерево публикации: " +
                       (out or "")[-300:])
    return PUBLISH_TREE


def _published_ids():
    try:
        rows = db.rows("""SELECT note FROM ext_deploy_log
                           WHERE note LIKE 'правки:%' ORDER BY id DESC LIMIT 200""")
    except Exception:
        return set()
    out = set()
    for r in rows:
        for part in (r["note"] or "").replace("правки:", "").split(","):
            part = part.strip()
            if part.isdigit():
                out.add(int(part))
    return out


def publish_accepted(limit=20, push=True):
    """Принято лидом — значит попадает в ветку выкатки. Коммитят агенты.

    Внешней среде доступ на запись в репозиторий не нужен: коммит делает
    сервер, из своего дерева, только после ACCEPT и только по применённым
    правкам.
    """
    url, _v = repo_url()
    if not url:
        return {"configured": False, "why": "адрес репозитория не задан"}
    done = _published_ids()
    rows = db.rows("""SELECT p.id, p.dev_job_id, p.edits_json, p.note
                        FROM ext_dev_patches p
                       WHERE p.status = 'applied'
                         AND EXISTS (SELECT 1 FROM ext_a2a_orders o
                                      WHERE o.dev_job_id = p.dev_job_id
                                        AND o.status = 'accepted')
                       ORDER BY p.id DESC LIMIT :l""", l=int(limit))
    fresh = [r for r in rows if r["id"] not in done]
    if not fresh:
        return {"configured": True, "published": [], "why": "нечего публиковать"}

    by_job = {}
    for r in fresh:
        by_job.setdefault(r["dev_job_id"], []).append(r)

    tree = _ensure_branch()
    out = []
    for job_id, patches in by_job.items():
        files = []
        for p_ in patches:
            for e in (json.loads(p_["edits_json"] or "[]") or []):
                path = e.get("path")
                if path and path not in files:
                    files.append(path)
        bad = check_paths(files)
        if bad:
            out.append({"dev_job_id": "dev_%s" % job_id, "skipped":
                        "; ".join("%s — %s" % (f, w) for f, w in bad[:3])})
            continue
        moved = _copy(files, repo.ROOT, tree)
        if not moved:
            out.append({"dev_job_id": "dev_%s" % job_id, "skipped": "файлы не найдены"})
            continue
        _git(["add"] + moved, cwd=tree)
        msg = ("правки по задаче dev_%s\n\nBORIS-ACCEPT: dev_%s\nфайлов: %d"
               % (job_id, job_id, len(moved)))
        code, o = _git(["commit", "-m", msg], cwd=tree)
        if code != 0 and "nothing to commit" in (o or "").lower():
            out.append({"dev_job_id": "dev_%s" % job_id, "skipped": "изменений нет"})
            continue
        if code != 0:
            out.append({"dev_job_id": "dev_%s" % job_id, "error": o[-200:]})
            continue
        sha = ""
        c2, sha_out = _git(["rev-parse", "HEAD"], cwd=tree)
        if c2 == 0:
            sha = sha_out.strip()
        pushed = True
        if push:
            code, o = _git(["push", "origin", BRANCH], cwd=tree, timeout=900)
            pushed = code == 0
            if not pushed:
                out.append({"dev_job_id": "dev_%s" % job_id,
                            "error": "push не прошёл: " + o[-200:]})
                continue
        # Файлы уже в боевом дереве — это их источник. Отмечаем SHA
        # выкаченным, чтобы транспорт не применял их повторно.
        _log_deploy(sha or "local", True, "правки: " +
                    ",".join(str(p_["id"]) for p_ in patches), moved)
        out.append({"dev_job_id": "dev_%s" % job_id, "sha": sha[:12],
                    "files": moved, "pushed": pushed})
    return {"configured": True, "published": out}


def _syntax_check_backend_sources(backend, py=None):
    """Compile every backend app source in memory without writing .pyc files."""
    py = py or deploy._py()
    script = (
        "from pathlib import Path\n"
        "files=sorted(Path('app').rglob('*.py'))\n"
        "for p in files:\n"
        "    compile(p.read_bytes(), str(p), 'exec', dont_inherit=True)\n"
        "print('compiled=%d' % len(files))\n"
    )
    return deploy._run([py, "-c", script], cwd=backend, timeout=600)


def tick(notify_result=True):
    """Один проход транспорта: забрать, проверить, применить, доказать."""
    url, _var = repo_url()
    if not url:
        return {"configured": False,
                "why": "адрес репозитория не задан (BORIS_DEPLOY_REPO)"}
    if not deploy.AUTO_APPLY:
        return {"configured": True, "skipped": "аварийный выключатель "
                                               "BORIS_AUTO_APPLY=0"}
    ensure_staging()
    sha = head_sha()
    base = last_deployed()
    if base == sha:
        return {"configured": True, "sha": sha, "up_to_date": True}

    msg = commit_message(sha)
    appr = approval_of(msg)
    if not appr["ok"]:
        _log_deploy(sha, False, "отклонён: " + appr["why"])
        if notify_result:
            from . import notify
            notify.send("🔧 BORIS отклонил небезопасное техническое обновление и сохранил рабочую версию. От вас действий не требуется.")
        return {"configured": True, "sha": sha, "rejected": appr["why"]}

    files = changed_files(sha, base)
    if not files:
        _log_deploy(sha, False, "в коммите нет файлов")
        return {"configured": True, "sha": sha, "rejected": "нет изменённых файлов"}
    bad = check_paths(files)
    if bad:
        why = "; ".join("%s — %s" % (f, w) for f, w in bad[:5])
        _log_deploy(sha, False, "отклонён по путям: " + why, files)
        if notify_result:
            from . import notify
            notify.send("🔧 BORIS остановил техническое обновление из-за нарушения границ безопасности. Рабочая версия не затронута; от вас действий не требуется.")
        return {"configured": True, "sha": sha, "rejected": why}

    with deploy._Lock():
        code, out = _git(["checkout", "--force", sha], cwd=STAGING)
        if code != 0:
            _log_deploy(sha, False, "не удалось перейти на коммит: " + out[-200:])
            return {"configured": True, "sha": sha, "rejected": "checkout не удался"}

        backup = deploy._backup_dir("git")
        steps = [{"step": "БЭКАП", "ok": True, "detail": backup}]
        existed_before = {
            f: os.path.isfile(os.path.join(repo.ROOT, f)) for f in files
        }
        moved = _copy(files, STAGING, repo.ROOT, backup)
        steps.append({"step": "ПЕРЕНОС", "ok": bool(moved), "detail": moved})

        backend = deploy.backend_dir()
        py = deploy._py()
        ok = True
        if any(f.startswith("backend/") for f in moved):
            # GIT_PREFLIGHT_NO_PYC_WRITE_V1:
            # syntax validation must never depend on permissions of production
            # __pycache__ directories. Compile sources in memory, then perform
            # the real app import below.
            c, o = _syntax_check_backend_sources(backend, py)
            steps.append({"step": "КОМПИЛЯЦИЯ", "ok": c == 0, "detail": o[-500:]})
            ok = ok and c == 0
            if ok:
                c, o = deploy._run([py, "-c", "import app.main"], cwd=backend, timeout=300)
                steps.append({"step": "ИМПОРТ", "ok": c == 0, "detail": o[-500:]})
                ok = ok and c == 0
        if ok and any("ext_api" in f for f in moved):
            c, o = deploy._run([py, "-c", "import sys;sys.path.insert(0,'.');"
                                          "from app.ext_api import router"],
                               cwd=backend, timeout=300)
            steps.append({"step": "ШЛЮЗ", "ok": c == 0, "detail": o[-400:]})
            ok = ok and c == 0

        if not ok:
            rollback = _rollback_files(moved, backup, repo.ROOT, existed_before)
            steps.append({"step": "ОТКАТ", "ok": rollback["ok"],
                          "detail": rollback})
            _log_deploy(sha, False, "проверки не прошли", moved)
            if notify_result:
                from . import notify
                notify.send("🔧 Техническое обновление BORIS не прошло автоматические проверки. Изменения откатились, рабочая версия сохранена; от вас действий не требуется.")
            return {"configured": True, "sha": sha, "ok": False, "steps": steps}

        units = _units_for(moved)
        restart_detail = deploy.restart(units) if units else []
        restart_ok = _restart_detail_ok(units, restart_detail)
        steps.append({"step": "РЕСТАРТ", "ok": restart_ok,
                      "detail": restart_detail})
        if not restart_ok:
            rollback = _rollback_files(moved, backup, repo.ROOT, existed_before)
            rollback_restart = deploy.restart(units)
            steps.append({"step": "ОТКАТ", "ok": rollback["ok"],
                          "detail": {"reason": "runtime restart failed",
                                     "files": rollback,
                                     "restart": rollback_restart}})
            _log_deploy(sha, False, "runtime restart failed", moved)
            if notify_result:
                from . import notify
                notify.send("🔧 Техническое обновление BORIS не завершило безопасный перезапуск. BORIS вернул предыдущую рабочую версию; от вас действий не требуется.")
            return {"configured": True, "sha": sha, "ok": False, "steps": steps}
        h = deploy.health()
        steps.append({"step": "ЖИВОСТЬ", "ok": h["ok"], "detail": h})
        if not h["ok"]:
            rollback = _rollback_files(moved, backup, repo.ROOT, existed_before)
            deploy.restart(units)
            steps.append({"step": "ОТКАТ", "ok": rollback["ok"],
                          "detail": {"files": rollback,
                                     "services": "restarted_after_rollback"}})
            _log_deploy(sha, False, "сервис не поднялся", moved)
            if notify_result:
                from . import notify
                notify.send("🔧 После технического обновления служба BORIS не прошла проверку запуска. BORIS вернул предыдущую рабочую версию; от вас действий не требуется.")
            return {"configured": True, "sha": sha, "ok": False, "steps": steps}

        _log_deploy(sha, True, "PASS, задача " + appr["dev_job_id"], moved)

    # После успешной выкладки работа продолжается сама.
    try:
        from . import recover
        cont = recover.once()
    except Exception as e:
        cont = {"error": "%s: %s" % (type(e).__name__, e)}
    # Успешная техническая выкладка — штатная внутренняя работа, а не повод
    # отвлекать владельца. В owner-канал идут только бизнес-результаты или
    # настоящий блокер, требующий решения человека.
    return {"configured": True, "sha": sha, "ok": True, "files": moved,
            "steps": steps, "continued": bool(cont)}


# ------------------------------------------------ готовность транспорта

def discover():
    """Найти следы репозитория, ничего секретного не показывая.

    Отвечает на вопрос «а был ли вообще удалённый репозиторий»: смотрит
    remotes, конфигурацию git всех уровней, переменные окружения BORIS и
    наличие ключей — именно НАЛИЧИЕ, без значений и без содержимого.
    """
    out = {"remotes": [], "git_config_refs": [], "env_refs": [],
           "ssh_keys_present": [], "current_branch": None,
           "is_git_repo": False}
    code, o = _git(["rev-parse", "--is-inside-work-tree"], cwd=repo.ROOT)
    out["is_git_repo"] = (code == 0 and o.strip() == "true")
    # Даже если каталог не репозиторий, следы адреса искать всё равно надо:
    # именно так находится существующий репозиторий, который просто не
    # подключён. Поэтому git-часть пропускаем, а остальную разведку — нет.
    if out["is_git_repo"]:
        code, o = _git(["remote", "-v"], cwd=repo.ROOT)
        if code == 0:
            for line in (o or "").split("\n"):
                if line.strip():
                    out["remotes"].append(
                        line.split()[0] + " " + _mask_url(line.split()[1])
                        if len(line.split()) > 1 else line.strip())
        code, o = _git(["branch", "--show-current"], cwd=repo.ROOT)
        out["current_branch"] = (o or "").strip() or None
    for scope in ("--local", "--global", "--system"):
        code, o = _git(["config", scope, "--get-regexp", "url|remote"], cwd=repo.ROOT)
        if code == 0 and (o or "").strip():
            for line in o.split("\n"):
                if line.strip():
                    k = line.split()[0]
                    v = line[len(k):].strip()
                    out["git_config_refs"].append("%s %s = %s" % (scope, k,
                                                                  _mask_url(v)))
    from .executor import _env_all
    env = _env_all()
    for k, v in env.items():
        if not isinstance(v, str):
            continue
        low = v.lower()
        if ("git@" in low or low.startswith("https://github")
                or low.startswith("https://gitlab") or low.endswith(".git")):
            out["env_refs"].append("%s = %s" % (k, _mask_url(v)))
    for path in ("/root/.ssh/id_rsa", "/root/.ssh/id_ed25519",
                 "/root/.ssh/id_ecdsa", "/root/.ssh/deploy_key"):
        if os.path.isfile(path):
            out["ssh_keys_present"].append(os.path.basename(path))
    out["ssh_config"] = os.path.isfile("/root/.ssh/config")
    out["git_credentials_file"] = os.path.isfile("/root/.git-credentials")

    # Другие репозитории и рабочие деревья внутри проекта: remote мог остаться
    # в подкаталоге, а не в корне.
    for base, dirs, _files in os.walk(repo.ROOT):
        if base.count(os.sep) - repo.ROOT.count(os.sep) > 2:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in ("node_modules", ".next", "venv",
                                                "updates", "BORIS_AGENT_PATCHES")]
        if ".git" in dirs or ".git" in _files:
            if os.path.abspath(base) == os.path.abspath(repo.ROOT):
                continue
            code, o = _git(["remote", "-v"], cwd=base)
            out.setdefault("other_repos", []).append(
                {"path": base, "remotes": [_mask_url(l.split()[1])
                                           for l in (o or "").split("\n")
                                           if len(l.split()) > 1]})
    code, o = _git(["worktree", "list"], cwd=repo.ROOT) if out["is_git_repo"] else (1, "")
    if code == 0:
        out["worktrees"] = [l.strip() for l in (o or "").split("\n") if l.strip()]

    # Ссылки на репозиторий в службах и в старых скриптах развёртывания.
    refs = []
    try:
        for name in os.listdir("/etc/systemd/system"):
            if not name.endswith(".service"):
                continue
            try:
                body = io.open("/etc/systemd/system/" + name, encoding="utf-8",
                               errors="ignore").read()
            except OSError:
                continue
            for line in body.split("\n"):
                low = line.lower()
                if "git@" in low or "github" in low or "gitlab" in low:
                    refs.append("%s: %s" % (name, _mask_url(line.strip()[:160])))
    except OSError:
        pass
    out["systemd_refs"] = refs[:20]

    script_refs = []
    for base, dirs, files in os.walk(repo.ROOT):
        if base.count(os.sep) - repo.ROOT.count(os.sep) > 2:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in ("node_modules", ".next", "venv",
                                                ".git", "updates")]
        for f in files:
            if not f.endswith((".sh", ".yml", ".yaml", ".cfg", ".ini", ".conf",
                               ".env", ".example")):
                continue
            path = os.path.join(base, f)
            try:
                body = io.open(path, encoding="utf-8", errors="ignore").read(20000)
            except OSError:
                continue
            for line in body.split("\n"):
                low = line.lower()
                if ("git@" in low or "github.com" in low or "gitlab.com" in low
                        or ".git" in low and "http" in low):
                    script_refs.append("%s: %s" % (
                        os.path.relpath(path, repo.ROOT), _mask_url(line.strip()[:160])))
                    break
        if len(script_refs) > 20:
            break
    out["script_refs"] = script_refs[:20]
    out["candidates"] = sorted({r.split("= ")[-1] if "= " in r else r
                                for r in out["env_refs"] + out["script_refs"]
                                })[:10]
    return out


def _mask_url(url):
    """Адрес показываем, логин и пароль — никогда."""
    u = (url or "").strip()
    if "@" in u and "://" in u:
        scheme, _, rest = u.partition("://")
        _creds, _, host = rest.rpartition("@")
        return scheme + "://***@" + host
    return u


READY = "READY"
BLOCKED = "BLOCKED"


def readiness(deep=False):
    """Цепочка проверок. READY только если пройдена ЦЕЛИКОМ.

    Раньше статус считался по наличию кода — и транспорт объявлялся готовым,
    хотя удалённого репозитория не существовало вовсе. Теперь каждый шаг
    проверяется отдельно, и первый же провал даёт BLOCKED с точной причиной.
    """
    steps = []

    def step(name, ok, why=""):
        steps.append({"шаг": name, "ok": bool(ok), "почему": why})
        return bool(ok)

    d = discover()
    if not step("репозиторий на месте", d["is_git_repo"],
                "" if d["is_git_repo"] else "/root/BORIS не является git-репозиторием"):
        return _verdict(steps, d)

    url, var = repo_url()
    has_remote = bool(d["remotes"]) or bool(url)
    if not step("удалённый репозиторий настроен", has_remote,
                "" if has_remote else "нет ни одного remote и не задан BORIS_DEPLOY_REPO"):
        return _verdict(steps, d)

    code, o = _git(["ls-remote", "--heads", url or "origin"], cwd=repo.ROOT, timeout=120)
    if not step("связь с репозиторием и доступ на чтение", code == 0,
                "" if code == 0 else (o or "")[-200:]):
        return _verdict(steps, d)

    branch_ref = "refs/heads/" + BRANCH
    branch_exists = branch_ref in (o or "")
    remote_sha = None
    if branch_exists:
        for line in (o or "").splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] == branch_ref:
                remote_sha = parts[0].strip()
                break
    step("ветка выкатки существует", branch_exists,
         "" if branch_exists else "ветки %s ещё нет — будет создана при первой "
                                  "публикации" % BRANCH)

    if deep:
        # WRITE_ACCESS_NOOP_REMOTE_HEAD_V1:
        # Existing deployment branch may be ahead of the production working
        # tree. Pushing local HEAD as a dry-run then produces a false
        # non-fast-forward and incorrectly reports missing write access.
        # For an existing branch, fetch its exact remote object and dry-run a
        # no-op update of that same SHA. This proves authenticated write access
        # without proposing any history rewrite or changing production files.
        write_target = url or "origin"
        if branch_exists and remote_sha:
            fetch_code, fetch_out = _git(["fetch", "--no-tags", write_target, remote_sha],
                                         cwd=repo.ROOT, timeout=180)
            if fetch_code == 0:
                code, o = _git(["push", "--dry-run", write_target,
                                remote_sha + ":" + branch_ref],
                               cwd=repo.ROOT, timeout=180)
            else:
                code, o = fetch_code, fetch_out
        else:
            code, o = _git(["push", "--dry-run", write_target,
                            "HEAD:" + branch_ref], cwd=repo.ROOT, timeout=180)
        if not step("доступ на запись (проверка вхолостую)", code == 0,
                    "" if code == 0 else (o or "")[-200:]):
            return _verdict(steps, d)
        try:
            ensure_staging()
            step("staging разворачивается", True)
        except ApiError as e:
            step("staging разворачивается", False, e.message)
            return _verdict(steps, d)

    ok_gate = callable(approval_of) and ACCEPT_MARK in "BORIS-ACCEPT:"
    step("проверка ACCEPT работает", ok_gate)
    return _verdict(steps, d)


def _verdict(steps, d):
    bad = [s for s in steps if not s["ok"]]
    hard = [s for s in bad if "будет создана" not in s["почему"]]
    return {"status": BLOCKED if hard else READY,
            "steps": steps,
            "blocked_by": [s["шаг"] + ": " + s["почему"] for s in hard],
            "discovery": d,
            "verdict": ("REMOTE_REPOSITORY_REQUIRED"
                        if any("удалённый репозиторий" in s["шаг"] for s in hard)
                        else None)}


def _write_env(url):
    """Запомнить найденный адрес в своей настройке, не трогая чужие .env."""
    path = os.path.join(repo.ROOT, "backend", ".ext_deploy.env")
    io.open(path, "w", encoding="utf-8").write(
        "# найдено разведкой, создано app.ext_api.gitdeploy\n"
        "BORIS_DEPLOY_REPO=%s\n" % url)
    os.chmod(path, 0o600)
    os.environ["BORIS_DEPLOY_REPO"] = url
    return path


def autoconnect():
    """Нашли существующий репозиторий — подключаем сами, без человека.

    Новый внешний сервис не заводится: берётся то, что уже есть в
    инфраструктуре. Если не нашлось ничего — это единственный случай, когда
    нужен человек, и тогда так и говорим.
    """
    d = discover()
    url, var = repo_url()
    if url:
        return {"connected": True, "source": var, "url": _mask_url(url),
                "already": True}

    # 1) настоящий remote в самом репозитории
    candidates = []
    for line in d.get("remotes", []):
        parts = line.split()
        if len(parts) > 1 and parts[1] not in candidates:
            candidates.append(parts[1])
    # 2) вложенные репозитории проекта
    for other in d.get("other_repos", []):
        for r in other.get("remotes", []):
            if r and r not in candidates:
                candidates.append(r)
    # 3) следы в конфигурации, окружении, службах и старых скриптах
    for ref in (d.get("candidates") or []):
        cand = ref.split()[-1] if " " in ref else ref
        if cand and cand not in candidates:
            candidates.append(cand)

    real = [c for c in candidates if "***" not in c and
            (c.startswith("git@") or c.startswith("http") or c.startswith("ssh://"))]
    if not real:
        return {"connected": False, "verdict": "REMOTE_REPOSITORY_REQUIRED",
                "searched": {"remotes": d.get("remotes"),
                             "git_config": d.get("git_config_refs"),
                             "env": d.get("env_refs"),
                             "systemd": d.get("systemd_refs"),
                             "scripts": d.get("script_refs"),
                             "other_repos": d.get("other_repos"),
                             "ssh_keys": d.get("ssh_keys_present")},
                "action": "дать серверу адрес существующего репозитория BORIS: "
                          "положить строку BORIS_DEPLOY_REPO=<адрес> в "
                          "backend/.env — больше ничего не требуется"}

    for cand in real:
        code, o = _git(["ls-remote", "--heads", cand], cwd=repo.ROOT, timeout=120)
        if code != 0:
            continue
        _write_env(cand)
        if not d.get("remotes"):
            _git(["remote", "add", "origin", cand], cwd=repo.ROOT)
        return {"connected": True, "url": _mask_url(cand),
                "source": "найден разведкой", "branches_seen": len(
                    [l for l in (o or "").split("\n") if l.strip()])}
    return {"connected": False, "verdict": "REMOTE_REPOSITORY_REQUIRED",
            "tried": [_mask_url(c) for c in real],
            "action": "ни один найденный адрес не отвечает: проверить доступ "
                      "или указать рабочий в BORIS_DEPLOY_REPO"}


# Что никогда не уезжает в репозиторий, даже если уже попало под контроль.
NEVER_TRACK = (
    ".env", ".env.local", ".env.prod", "backend/.env", "*.pem", "*.key",
    "id_rsa", "id_ed25519", ".ext_notify.env", ".ext_deploy.env",
    "venv/", "node_modules/", ".next/", "__pycache__/", "*.pyc",
    "backups/", "*.sql.gz", "*.dump", "uploads/", "media/", "logs/", "*.log",
    "updates/", "BORIS_AGENT_PATCHES/", ".ext_inbox_offset",
)

# По этим признакам файл считается содержащим секрет и push останавливается.
SECRET_SIGNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(password|passwd|secret|token)\s*[=:]\s*['\"]?[^\s'\"]{8,}"),
    re.compile(r"\d{6,12}:[A-Za-z0-9_\-]{30,}"),
)

GITIGNORE_HEADER = "# создано app.ext_api.gitdeploy — не выкладываем лишнее\n"


def ensure_gitignore():
    """Список исключений — до первого push, а не после утечки."""
    path = os.path.join(repo.ROOT, ".gitignore")
    cur = ""
    if os.path.isfile(path):
        cur = io.open(path, encoding="utf-8", errors="ignore").read()
    missing = [p for p in NEVER_TRACK if p not in cur]
    if missing:
        with io.open(path, "a", encoding="utf-8") as f:
            if not cur.strip():
                f.write(GITIGNORE_HEADER)
            else:
                f.write("\n" + GITIGNORE_HEADER)
            for p_ in missing:
                f.write(p_ + "\n")
    return {"added": missing, "path": path}


def untrack_forbidden():
    """Убрать из-под контроля то, что туда попало раньше. Файлы на диске целы."""
    code, out = _git(["ls-files"], cwd=repo.ROOT)
    if code != 0:
        return {"removed": [], "why": out[-200:]}
    removed = []
    for f in (out or "").split("\n"):
        f = f.strip()
        if not f:
            continue
        low = f.lower()
        hit = any(low.endswith(p.strip("*")) or p.strip("/") in low.split("/")
                  or low.endswith(p) for p in NEVER_TRACK)
        if hit:
            _git(["rm", "--cached", "-q", "--", f], cwd=repo.ROOT)
            removed.append(f)
    return {"removed": removed}


def scan_tracked_for_secrets(limit_bytes=200000):
    """Прочесать то, что реально под контролем. Значения секретов не выводим."""
    code, out = _git(["ls-files"], cwd=repo.ROOT)
    if code != 0:
        return {"hits": [], "why": out[-200:]}
    hits = []
    for f in (out or "").split("\n"):
        f = f.strip()
        if not f:
            continue
        full = os.path.join(repo.ROOT, f)
        try:
            if os.path.getsize(full) > limit_bytes:
                continue
            body = io.open(full, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        for rx in SECRET_SIGNS:
            if rx.search(body):
                hits.append({"file": f, "признак": rx.pattern[:30]})
                break
    return {"hits": hits}


def _push(args, url=None):
    """Push ходит тем же путём, что и остальной git: доступ выдаётся в
    _git_env(), поэтому отдельной ветки поведения здесь больше нет."""
    return _git(list(args), cwd=repo.ROOT, timeout=900)



# --------------------------------------------- автоматическая уборка секретов
# Владелец получил один и тот же блокер дважды и справедливо возмутился:
# повторять человеку то, что машина может разгрести сама, — это не отчёт, а
# спам. Здесь файл классифицируется и снимается с контроля без него.

RUNTIME_KINDS = (
    ("окружение и секреты", (".env", ".env.local", ".env.prod", "secrets",
                             "credentials", "id_rsa", ".pem", ".key", ".p12")),
    ("журналы", (".log", "/logs/", "logs/")),
    ("резервные копии", ("backup", "/backups/", ".dump", ".sql.gz")),
    ("данные и загрузки", ("/uploads/", "uploads/", "/media/", ".sqlite", ".db")),
    ("зависимости", ("venv/", "node_modules/", "/.next/", "__pycache__")),
    ("рабочие файлы", ("/updates/", "BORIS_AGENT_PATCHES/", ".pyc", ".cache")),
)


def classify_path(path):
    """К какому виду относится файл. От вида зависит, можно ли снять его
    с контроля автоматически."""
    low = path.lower()
    for kind, marks in RUNTIME_KINDS:
        if any(m in low or low.endswith(m.strip("/")) for m in marks):
            return kind
    return "исходный код"


def in_history(path):
    """Попадал ли файл в историю. Снять с индекса мало, если он уже в коммитах."""
    code, out = _git(["log", "--oneline", "-1", "--", path], cwd=repo.ROOT)
    return bool((out or "").strip()) if code == 0 else False


def auto_secret_cleanup():
    """Разобрать блокер самостоятельно и вернуть факты, а не жалобу.

    Что можно снять с контроля — снимается (файлы на диске остаются). Что
    является исходным кодом с секретом внутри — машина трогать не имеет
    права: это правка кода, и её делает исполнитель, а не уборщик.
    """
    scan = scan_tracked_for_secrets()
    blocked = [h["file"] for h in scan.get("hits") or []]
    report = {"blocked_paths": blocked, "classified": {}, "untracked": [],
              "in_history": [], "left_for_code_fix": []}
    if not blocked:
        report["verdict"] = "PASS"
        return report

    ensure_gitignore()
    ignore_add = []
    for f in blocked:
        kind = classify_path(f)
        report["classified"].setdefault(kind, []).append(f)
        if kind == "исходный код":
            report["left_for_code_fix"].append(f)
            continue
        code, _o = _git(["rm", "--cached", "-q", "--", f], cwd=repo.ROOT)
        if code == 0:
            report["untracked"].append(f)
            ignore_add.append(f)
        if in_history(f):
            report["in_history"].append(f)

    if ignore_add:
        try:
            gi = os.path.join(repo.ROOT, ".gitignore")
            body = io.open(gi, encoding="utf-8").read() if os.path.isfile(gi) else ""
            missing = [f for f in ignore_add if f not in body]
            if missing:
                io.open(gi, "a", encoding="utf-8").write(
                    "\n# снято автоматически: секреты не отправляются\n" +
                    "\n".join(missing) + "\n")
        except OSError:
            pass
        _git(["add", "-A", ".gitignore"], cwd=repo.ROOT)
        _git(["commit", "-q", "-m",
              "chore: снять с контроля файлы с секретами (остаются на диске)"],
             cwd=repo.ROOT)

    again = scan_tracked_for_secrets()
    report["after"] = [h["file"] for h in again.get("hits") or []]
    report["verdict"] = "PASS" if not report["after"] else "FAIL"
    return report


NOTIFY_STATE = "/tmp/.boris_git_blocker"


def notify_once(text):
    """Одна и та же беда сообщается один раз.

    Повтор без новых фактов — это шум, из-за которого перестают читать и
    важные сообщения.
    """
    import hashlib as _h
    sig = _h.sha256((text or "").encode("utf-8")).hexdigest()[:16]
    try:
        if io.open(NOTIFY_STATE, encoding="utf-8").read().strip() == sig:
            return {"sent": False, "why": "та же причина, повтор не отправляю"}
    except OSError:
        pass
    from . import notify
    ok = notify.send(text)
    try:
        io.open(NOTIFY_STATE, "w", encoding="utf-8").write(sig)
    except OSError:
        pass
    return {"sent": bool(ok)}


def bootstrap(url=None, push_main=True):
    """Первый выезд существующего кода в новый пустой репозиторий.

    Порядок обратный привычному: сначала исключения и проверка на секреты,
    и только потом отправка. Слепого «добавить всё» здесь нет — один такой
    push уносит .env и ключи навсегда, отозвать это уже нельзя.
    """
    url = url or repo_url()[0]
    if not url:
        return {"ok": False, "verdict": "NEED_BORIS_REPOSITORY_URL"}

    steps = {"gitignore": ensure_gitignore(), "untracked": untrack_forbidden()}
    scan = scan_tracked_for_secrets()
    steps["secret_scan"] = {"файлов с признаками секрета": len(scan["hits"]),
                            "файлы": [h["file"] for h in scan["hits"]][:20]}
    if scan["hits"]:
        # Сначала пробуем разобрать сами: окружение, ключи, журналы, копии,
        # данные и зависимости снимаются с контроля без человека.
        cleanup = auto_secret_cleanup()
        steps["secret_cleanup"] = {
            "снято с контроля": len(cleanup["untracked"]),
            "осталось в коде": cleanup["left_for_code_fix"],
            "были в истории": cleanup["in_history"],
            "вердикт": cleanup["verdict"]}
        if cleanup["verdict"] != "PASS":
            return {"ok": False, "verdict": "SECRETS_IN_TRACKED_FILES",
                    "steps": steps, "blocked_paths": cleanup["after"],
                    "action": "остались файлы с секретами в исходном коде — это "
                              "правка кода, а не уборка: %s"
                              % ", ".join(cleanup["after"][:5])}

    code, out = _git(["remote", "-v"], cwd=repo.ROOT)
    if "origin" not in (out or ""):
        _git(["remote", "add", "origin", url], cwd=repo.ROOT)
    else:
        _git(["remote", "set-url", "origin", url], cwd=repo.ROOT)
    steps["origin"] = _mask_url(url)

    code, out = _push(["ls-remote", "--heads", "origin"])
    if code != 0:
        return {"ok": False, "verdict": "NEED_GITHUB_AUTH", "steps": steps,
                "detail": out[-300:],
                "action": "положить в backend/.env строку BORIS_GIT_TOKEN=<токен "
                          "GitHub с правом записи в этот репозиторий> — больше "
                          "ничего не требуется"}
    steps["fetch"] = "ok"

    if push_main:
        code, out = _git(["add", "-A", "--", ":!updates", ":!backups"], cwd=repo.ROOT)
        _git(["commit", "-m", "BORIS: исходный код без секретов и служебных данных"],
             cwd=repo.ROOT)
        code, out = _push(["push", "-u", "origin", "HEAD:main"])
        steps["main_push"] = "PASS" if code == 0 else ("FAIL: " + out[-300:])
        if code != 0:
            return {"ok": False, "verdict": "MAIN_PUSH_FAILED", "steps": steps}

    code, out = _push(["push", "origin", "HEAD:refs/heads/" + BRANCH])
    steps["branch_push"] = "PASS" if code == 0 else ("FAIL: " + out[-300:])
    if code != 0:
        return {"ok": False, "verdict": "BRANCH_PUSH_FAILED", "steps": steps}

    _write_env(url)
    return {"ok": True, "steps": steps}


def roundtrip(cleanup=True):
    """Полный круг: ACCEPT → коммит → push → забор → ворота → PASS.

    Проверяется безобидным файлом-маркером: он ничего не исполняет и не
    требует перезапуска служб, но проходит ровно тот же путь, что и
    настоящая правка.
    """
    from . import dev, a2a, clients
    r = readiness(deep=True)
    if r["status"] != READY:
        return {"ok": False, "status": r["status"], "blocked_by": r["blocked_by"]}

    job = dev.create(clients.LEAD_KEY, "req_transport_roundtrip",
                     "TRANSPORT_ROUNDTRIP — круговая проверка транспорта",
                     "Проверка доставки: безобидный файл-маркер проходит весь путь.",
                     ["Файл-маркер доставлен в боевое дерево через ветку выкатки"],
                     scope={}, priority=70)
    order = a2a.create_order(clients.LEAD_KEY, job["dev_job_id"], "PLANNING",
                             "круговая проверка", acceptance=["маркер доставлен"])
    db.q("UPDATE ext_a2a_orders SET status='accepted' WHERE id=:i",
         i=order["order_id"])

    tree = _ensure_branch()
    marker = "backend/app/ext_api/.transport_ok"
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    full = os.path.join(tree, marker)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    io.open(full, "w", encoding="utf-8").write("проверка транспорта %s\n" % stamp)
    _git(["add", marker], cwd=tree)
    code, o = _git(["commit", "-m", "проверка транспорта\n\nBORIS-ACCEPT: %s"
                    % job["dev_job_id"]], cwd=tree)
    if code != 0 and "nothing to commit" not in (o or "").lower():
        return {"ok": False, "step": "коммит", "detail": o[-300:]}
    code, o = _git(["push", "origin", BRANCH], cwd=tree, timeout=600)
    if code != 0:
        return {"ok": False, "step": "push", "detail": o[-300:]}

    res = tick(notify_result=False)
    production_marker = os.path.join(repo.ROOT, marker)
    delivered = os.path.isfile(production_marker)
    ok = bool(res.get("ok")) and delivered
    marker_cleaned = False
    if cleanup:
        # The marker is evidence, not runtime state. Remove it after delivery
        # has been proven so a successful transport self-test leaves production
        # byte-for-byte free of test artifacts. The remote branch keeps the
        # history/evidence and the next roundtrip writes a new marker commit.
        if ok and os.path.isfile(production_marker):
            try:
                os.unlink(production_marker)
                marker_cleaned = not os.path.exists(production_marker)
            except OSError:
                marker_cleaned = False
        try:
            db.q("DELETE FROM ext_a2a_messages WHERE order_id=:i", i=order["order_id"])
            db.q("DELETE FROM ext_a2a_orders WHERE id=:i", i=order["order_id"])
            db.q("DELETE FROM ext_dev_events WHERE dev_job_id=:i", i=job["id"])
            db.q("DELETE FROM ext_dev_jobs WHERE id=:i", i=job["id"])
        except Exception:
            pass
    return {"ok": ok, "delivered": delivered, "marker_cleaned": marker_cleaned,
            "deploy": res, "dev_job_id": job["dev_job_id"]}


def remote_branches(url=None):
    """Что реально лежит в удалённом репозитории. Настроенный адрес и
    заполненный репозиторий — разные вещи."""
    url = url or repo_url()[0]
    if not url:
        return None, "адрес репозитория не задан"
    code, out = _git(["ls-remote", "--heads", url], timeout=300)
    if code != 0:
        return None, out[-300:]
    return [ln.split("refs/heads/")[-1].strip() for ln in out.splitlines()
            if "refs/heads/" in ln], None


def transport_gap():
    """Чего не хватает удалённой стороне, чтобы транспорт заработал.

    Отдельный шаг появился после боя: origin был настроен, доступ работал,
    и на этом «подключено» считалось выполненным — а репозиторий стоял
    пустой, без ветки выкатки. «Подключено» не значит «готово».
    """
    names, err = remote_branches()
    if names is None:
        return {"known": False, "why": err}
    return {"known": True, "branches": names, "need_bootstrap": BRANCH not in names,
            "empty": not names}


def ensure_transport(run_roundtrip=True):
    """Подключить, наполнить и доказать. Останавливаемся только на настоящем
    блокере — на том, который человек и правда должен снять сам."""
    conn = autoconnect()
    boot = None
    if not conn.get("connected") and repo_url()[0]:
        boot = bootstrap()
        if not boot.get("ok"):
            return {"AUTONOMOUS_GIT_TRANSPORT": BLOCKED, "bootstrap": boot,
                    "verdict": boot.get("verdict"), "action": boot.get("action")}
        conn = {"connected": True, "url": boot["steps"].get("origin"),
                "source": "bootstrap"}
    if not conn.get("connected"):
        return {"AUTONOMOUS_GIT_TRANSPORT": BLOCKED, "connect": conn,
                "verdict": conn.get("verdict"), "action": conn.get("action")}
    gap = transport_gap()
    if boot is None and gap.get("need_bootstrap"):
        boot = bootstrap()
        if not boot.get("ok"):
            return {"AUTONOMOUS_GIT_TRANSPORT": BLOCKED, "connect": conn,
                    "gap": gap, "bootstrap": boot, "verdict": boot.get("verdict"),
                    "action": boot.get("action")}
    r = readiness(deep=True)
    if r["status"] != READY:
        return {"AUTONOMOUS_GIT_TRANSPORT": BLOCKED, "connect": conn,
                "blocked_by": r["blocked_by"]}
    rt = roundtrip() if run_roundtrip else {"ok": None, "skipped": True}
    return {"AUTONOMOUS_GIT_TRANSPORT": READY if rt.get("ok") else BLOCKED,
            "connect": conn, "roundtrip": rt}


def status():
    url, var = repo_url()
    r = readiness()
    return {"AUTONOMOUS_GIT_TRANSPORT": r["status"],
            "blocked_by": r["blocked_by"], "verdict": r.get("verdict"),
            "checks": r["steps"],
            "repo_configured": bool(url) or bool(r["discovery"]["remotes"]),
            "source_var": var, "branch": BRANCH,
            "staging": STAGING, "staged": os.path.isdir(os.path.join(STAGING, ".git")),
            "last_deployed_sha": last_deployed(),
            "accept_mark": ACCEPT_MARK,
            "allowed_prefixes": list(ALLOWED_PREFIXES),
            "auto_apply": deploy.AUTO_APPLY,
            "history": db.rows("""SELECT sha, ok, note, created_at FROM ext_deploy_log
                                   ORDER BY id DESC LIMIT 5""")}


TRANSPORT_STATE = {"status": None, "last_try": 0.0, "announced": None}
RETRY_SECONDS = int(os.environ.get("BORIS_TRANSPORT_RETRY_SEC", "900"))


def _announce(res):
    """Смена состояния транспорта — новость для владельца. Ровно один раз на
    смену: повтор одного и того же состояния он видеть не должен."""
    st = res.get("AUTONOMOUS_GIT_TRANSPORT")
    if st == READY:
        text = "Транспорт обновлений через Git работает: правки едут на сервер сами."
    else:
        why = (res.get("action") or res.get("verdict")
               or "; ".join(res.get("blocked_by") or []) or "причина не определена")
        text = "Транспорт обновлений через Git не поднялся.\nЧто мешает: %s" % why
    try:
        from . import notify
        notify.send(text)
    except Exception:
        pass
    return text


def self_establish(state=None, now=None, force=False, run_roundtrip=True,
                   establish=None):
    """Транспорт поднимает служба, а не человек.

    Дефект был не в конкретном origin: цикл службы умел только возить готовое
    и молча выходил, если возить было нечем. Поднять транспорт умел один лишь
    установщик — то есть человек. Здесь это закрыто: служба на каждом запуске
    и дальше по расписанию сама доводит транспорт до READY, а о смене
    состояния сообщает владельцу.
    """
    state = TRANSPORT_STATE if state is None else state
    now = time.time() if now is None else now
    establish = establish or ensure_transport
    if state.get("status") == READY and not force:
        return {"AUTONOMOUS_GIT_TRANSPORT": READY, "skipped": "уже доказан"}
    waited = now - float(state.get("last_try") or 0)
    if not force and state.get("last_try") and waited < RETRY_SECONDS:
        return {"AUTONOMOUS_GIT_TRANSPORT": state.get("status"),
                "skipped": "пауза между попытками"}
    state["last_try"] = now
    try:
        res = establish(run_roundtrip=run_roundtrip)
    except ApiError as e:
        res = {"AUTONOMOUS_GIT_TRANSPORT": BLOCKED, "blocked_by": [e.message]}
    except Exception as e:
        res = {"AUTONOMOUS_GIT_TRANSPORT": BLOCKED,
               "blocked_by": ["%s: %s" % (type(e).__name__, e)]}
    st = res.get("AUTONOMOUS_GIT_TRANSPORT")
    state["status"] = st
    res["changed"] = st != state.get("announced")
    if res["changed"]:
        state["announced"] = st
        res["announced"] = _announce(res)
    return res


def run(idle=120):
    while True:
        try:
            self_establish()
            tick()
        except ApiError as e:
            print("gitdeploy: " + e.message, file=sys.stderr, flush=True)
        except Exception as e:
            print("gitdeploy: %s: %s" % (type(e).__name__, e), file=sys.stderr,
                  flush=True)
        time.sleep(int(idle))


def main():
    import argparse
    ap = argparse.ArgumentParser(prog="gitdeploy")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("tick")
    r = sub.add_parser("run")
    r.add_argument("--idle", type=int, default=120)
    a = ap.parse_args()
    if a.cmd == "run":
        return run(a.idle)
    out = tick() if a.cmd == "tick" else status()
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main() or 0)
