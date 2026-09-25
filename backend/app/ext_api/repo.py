# -*- coding: utf-8 -*-
"""Доступ агентов к коду проекта — через API, без shell и без прямой записи.

Правила, которые здесь зашиты:

  ЧИТАТЬ  агент может файлы проекта, кроме секретов, бэкапов и мусора.
  ПИСАТЬ  агент не может. Он только ПРЕДЛАГАЕТ правку.
  ПРИМЕНЯЕТ правку человек своим ключом, и только через
          BACKUP -> WRITE -> VERIFY -> ROLLBACK-при-провале.

Так агент перестаёт зависеть от того, донёс ли владелец файл руками, и при
этом не получает ни shell, ни возможности молча переписать прод.
"""
import os, io, json, re, time, subprocess, hashlib, sys, fcntl
from contextlib import contextmanager
from datetime import datetime
from . import db, auth
from .errors import ApiError
from app.services.exception_observability import observe_suppressed

ROOT = os.environ.get("BORIS_REPO_ROOT", "/root/BORIS")

# Что не отдаём НИКОГДА — ни на чтение, ни в дерево.
DENY_PARTS = (".git/", "node_modules/", "__pycache__/", "/venv/", ".next/",
              "backups/", "BORIS_FACTORY_BACKUPS/")
DENY_NAMES = (".env", ".credentials.json", "credentials.json", "id_rsa", "id_ed25519")
DENY_SUFFIX = (".pem", ".key", ".p12", ".pfx", ".sqlite", ".db", ".log", ".zip",
               ".tar", ".gz", ".rar", ".png", ".jpg", ".jpeg", ".webp", ".mp4", ".pdf")
# Бэкапы вида page.tsx.before_xxx, main.py.bak_123 — шум, в дерево не пускаем.
BACKUP_RE = re.compile(r"\.(bak|backup|before|broken|step\d)[^/]*$", re.I)

MAX_FILE_BYTES = 400_000
MAX_TREE_ENTRIES = 800

# MONEY_CONTOUR_PROTECTED_V1: autonomous development may inspect/propose these
# files but cannot apply them to production. The marketing money lane is changed
# only by an operator-controlled deployment followed by dedicated money QA.
MONEY_CONTOUR_PROTECTED_PATHS = {
    "backend/app/api/cpx_advisor.py",
    "backend/app/services/autonomy.py",
    "backend/app/services/marketing_money_policy.py",
    "backend/app/services/intraday.py",
    "backend/cpx_advisor_runner.py",
    "backend/kpi_goal_runner.py",
    "backend/cpx_budget_brake.py",
    "backend/cpx_cap_reconciler.py",
    "backend/ai_marketer_hourly.sh",
    "backend/marketer_rollout_runner.py",
    "backend/late_day_spend_controller.py",
    "backend/new_ad_bid_watch_runner.py",
    "backend/qa_marketer_reach_safety.py",
    "backend/qa_marketer_rollout.py",
    "backend/boris_background_worker.py",
    "backend/production_contract_runner.py",
    "backend/sql/marketer_money_mandate_guard_v1.sql",
    "backend/app/ext_api/repo.py",
    "backend/app/ext_api/deploy.py",
}

def _deny_autonomous_money_contour(paths):
    hit = sorted(set(paths or []) & MONEY_CONTOUR_PROTECTED_PATHS)
    if hit:
        raise ApiError(
            "FORBIDDEN_SCOPE",
            "MONEY_CONTOUR_PROTECTED: autonomous apply denied for " + ", ".join(hit),
        )

# Чем проверяем правку. Ключ — префикс пути внутри проекта.
VERIFY = {
    "frontend/": {"cmd": ["npm", "run", "build"], "cwd": "frontend", "timeout": 900,
                  "note": "сборка Next.js"},
    "backend/":  {"cmd": [sys.executable, "-c", "import app.main"], "cwd": "backend",
                  "timeout": 180, "note": "импорт бэкенда без рестарта"},
}


# ------------------------------------------------------------------ пути

def safe_path(rel):
    """Возвращает абсолютный путь внутри проекта или бросает ошибку."""
    if not rel or not isinstance(rel, str):
        raise ApiError("VALIDATION_ERROR", "путь не задан")
    rel = rel.strip().lstrip("/")
    full = os.path.realpath(os.path.join(ROOT, rel))
    root = os.path.realpath(ROOT)
    if not (full == root or full.startswith(root + os.sep)):
        raise ApiError("FORBIDDEN_SCOPE", "путь выходит за пределы проекта")
    low = full.lower()
    name = os.path.basename(full)
    if any(p in low + "/" for p in DENY_PARTS):
        raise ApiError("FORBIDDEN_SCOPE", f"каталог закрыт для агентов: {rel}")
    if name in DENY_NAMES or any(name.lower().endswith(s) for s in DENY_SUFFIX):
        raise ApiError("FORBIDDEN_SCOPE", f"этот файл агентам не отдаётся: {name}")
    return full


def _rel(full):
    return os.path.relpath(full, os.path.realpath(ROOT))


def _skip(name, full):
    if BACKUP_RE.search(name):
        return True
    if name in DENY_NAMES or any(name.lower().endswith(s) for s in DENY_SUFFIX):
        return True
    return False


def tree(paths=None, max_entries=MAX_TREE_ENTRIES):
    """Дерево файлов по указанным путям. Бэкапы и секреты не показываются."""
    roots = [safe_path(p) for p in (paths or ["frontend/app", "backend/app"])]
    out, skipped = [], 0
    for r in roots:
        if os.path.isfile(r):
            out.append({"path": _rel(r), "size": os.path.getsize(r)})
            continue
        for dirpath, dirnames, filenames in os.walk(r):
            low = dirpath.lower() + "/"
            if any(p in low for p in DENY_PARTS):
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames
                           if not any(p.strip("/") == d for p in DENY_PARTS)]
            for fn in sorted(filenames):
                full = os.path.join(dirpath, fn)
                if _skip(fn, full):
                    skipped += 1
                    continue
                if len(out) >= max_entries:
                    return {"root": ROOT, "files": out, "truncated": True,
                            "skipped_backups_and_secrets": skipped}
                try:
                    out.append({"path": _rel(full), "size": os.path.getsize(full),
                                "lines": None})
                except OSError:
                    pass
    return {"root": ROOT, "files": sorted(out, key=lambda x: x["path"]),
            "truncated": False, "skipped_backups_and_secrets": skipped}


def read_file(rel, start=1, end=None):
    full = safe_path(rel)
    if not os.path.isfile(full):
        raise ApiError("ACCOUNT_NOT_FOUND", f"файла нет: {rel}")
    size = os.path.getsize(full)
    if size > MAX_FILE_BYTES:
        raise ApiError("TOO_MANY_ITEMS",
                       f"файл {rel} весит {size} байт, лимит {MAX_FILE_BYTES}; "
                       "запрашивайте диапазон строк через start и end")
    text = io.open(full, encoding="utf-8", errors="replace").read()
    lines = text.split("\n")
    total = len(lines)
    a = max(1, int(start or 1))
    b = min(total, int(end)) if end else total
    chunk = "\n".join(lines[a - 1:b])
    return {"path": rel, "total_lines": total, "from": a, "to": b,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
            "content": chunk}


def read_many(paths, max_total=250_000):
    out, total = [], 0
    for p in (paths or [])[:25]:
        try:
            r = read_file(p)
        except ApiError as e:
            out.append({"path": p, "error": e.code, "message": e.message})
            continue
        total += len(r["content"])
        if total > max_total:
            out.append({"path": p, "error": "TOO_MANY_ITEMS",
                        "message": "суммарный лимит выборки исчерпан, запрашивайте по частям"})
            break
        out.append(r)
    return {"files": out}


# --------------------------------------------------------------- правки

def propose(key, dev_job_id, edits, note=None):
    """Агент кладёт предложение. Ничего на диске не меняется."""
    from . import dev
    job = dev.get(dev_job_id)
    if not isinstance(edits, list) or not edits:
        raise ApiError("MISSING_INPUT", "нужен непустой список edits")
    prepared = []
    for e in edits:
        p = e.get("path")
        full = safe_path(p)
        mode = (e.get("mode") or "replace").lower()
        if mode not in ("replace", "full"):
            raise ApiError("VALIDATION_ERROR", "mode должен быть replace или full")
        if mode == "replace":
            if not e.get("old") or e.get("new") is None:
                raise ApiError("VALIDATION_ERROR",
                               f"{p}: для mode=replace нужны old и new")
            if not os.path.isfile(full):
                raise ApiError("MISSING_INPUT", f"файла нет: {p}")
            cur = io.open(full, encoding="utf-8", errors="replace").read()
            n = cur.count(e["old"])
            if n == 0:
                raise ApiError("VALIDATION_ERROR",
                               f"{p}: фрагмент old не найден в текущем файле — "
                               "перечитайте файл и повторите")
            if n > 1:
                raise ApiError("VALIDATION_ERROR",
                               f"{p}: фрагмент old встречается {n} раза, нужен уникальный")
        else:
            if e.get("content") is None:
                raise ApiError("VALIDATION_ERROR", f"{p}: для mode=full нужен content")
        prepared.append({"path": _rel(full), "mode": mode, "old": e.get("old"),
                         "new": e.get("new"), "content": e.get("content")})

    row = db.one("""INSERT INTO ext_dev_patches (dev_job_id, author, status, note, edits_json)
                    VALUES (:d,:a,'proposed',:n,:e) RETURNING id""",
                 d=job["id"], a=auth.actor_of(key)["name"], n=(note or "")[:500],
                 e=json.dumps(prepared, ensure_ascii=False)[:400000])
    dev.event(job["id"], job["current_phase"], auth.actor_of(key)["name"], "patch_proposed",
              {"patch_id": row["id"], "files": [x["path"] for x in prepared]})
    return get_patch(row["id"])


def get_patch(pid):
    p = db.one("SELECT * FROM ext_dev_patches WHERE id=:i", i=int(pid))
    if not p:
        raise ApiError("JOB_NOT_FOUND", "правка не найдена")
    try:
        edits = json.loads(p["edits_json"] or "[]")
    except Exception:
        edits = []
    return {"patch_id": p["id"], "dev_job_id": f"dev_{p['dev_job_id']}",
            "author": p["author"], "status": p["status"], "note": p.get("note"),
            "files": [e["path"] for e in edits],
            "edits": [{k: v for k, v in e.items() if k != "content"} for e in edits],
            "applied_by": p.get("applied_by"), "backup_dir": p.get("backup_dir"),
            "verify_output": (p.get("verify_output") or "")[-4000:],
            "error_text": p.get("error_text"),
            "created_at": p.get("created_at"), "applied_at": p.get("applied_at")}


def list_patches(dev_job_id=None, status=None, limit=50):
    sql, prm = "SELECT id FROM ext_dev_patches WHERE 1=1", {}
    if dev_job_id:
        from . import dev
        sql += " AND dev_job_id=:d"; prm["d"] = dev.get(dev_job_id)["id"]
    if status:
        sql += " AND status=:s"; prm["s"] = status
    sql += " ORDER BY id DESC LIMIT :l"; prm["l"] = int(limit)
    return [get_patch(r["id"]) for r in db.rows(sql, **prm)]


# --------------------------------------------------------------- применение

def _verify_for(paths):
    jobs = []
    for pref, cfg in VERIFY.items():
        if any(p.startswith(pref) for p in paths):
            jobs.append(cfg)
    return jobs


def apply(key, patch_id, confirm=False, skip_verify=False, manifest=None):
    """Human apply path serialized by the global production source lock."""
    with _patch_apply_lock(patch_id):
        return _human_apply_locked(key, patch_id, confirm, skip_verify, manifest)


class _HumanTransaction:
    """Linux regular-file transaction; caller holds the production writer lock.

    In-place writes retain inode/xattrs. Only FS_IMMUTABLE_FL may be toggled.
    Open descriptors and inode identity prove which new files we may remove.
    """
    GETFLAGS, SETFLAGS, IMMUTABLE = 0x80086601, 0x40086602, 0x10

    def __init__(self, edits, manifest, backup_dir):
        import stat
        self.rows, self.backup_dir = [], backup_dir
        self.journal = os.path.join(backup_dir, 'transaction.json')
        self.state = 'preflight'
        wanted = {}
        for e in edits: wanted.setdefault(e['path'], []).append(e)
        verified = {f['path']: f for f in manifest['files']}
        if len(verified) != len(manifest['files']) or set(wanted) != set(verified):
            raise ValueError('MANIFEST_SCOPE_MISMATCH')
        for rel, file_edits in wanted.items():
            if not isinstance(rel,str) or rel.startswith('/') or any(x in ('', '.', '..') for x in rel.split('/')):
                raise ValueError('UNSAFE_RELATIVE_PATH')
            full = safe_path(rel)
            if full != os.path.abspath(os.path.join(ROOT, rel)):
                raise ValueError('SYMLINK_PATH: ' + rel)
            exists = os.path.lexists(full)
            st = os.lstat(full) if exists else None
            if st and not stat.S_ISREG(st.st_mode):
                raise ValueError('NOT_SINGLE_REGULAR_FILE: ' + rel)
            if not os.path.isdir(os.path.dirname(full)):
                raise ValueError('PARENT_MISSING: ' + rel)
            old = self.read_file(full) if exists else b''
            digest = hashlib.sha256(old).hexdigest() if exists else None
            if digest != verified[rel]['before_sha256']:
                raise ValueError('EXPECTED_HASH_MISMATCH: ' + rel)
            new = old
            for e in file_edits:
                if e['mode'] == 'full':
                    new = e['content'].encode('utf-8')
                elif e['mode'] == 'replace' and e.get('old') and new.decode('utf-8').count(e['old']) == 1:
                    new = new.decode('utf-8').replace(e['old'], e['new'], 1).encode('utf-8')
                else:
                    raise ValueError('INVALID_EDIT: ' + rel)
            if exists and old == new:
                raise ValueError('NOOP_DESTINATION: ' + rel)
            if hashlib.sha256(new).hexdigest() != verified[rel]['after_sha256']:
                raise ValueError('AFTER_HASH_MISMATCH: ' + rel)
            fd = os.open(full, os.O_RDONLY | os.O_NOFOLLOW) if exists else None
            try:
                flags = self.flags(fd) if exists else None
            finally:
                if fd is not None: os.close(fd)
            if st and st.st_nlink > 1 and flags & self.IMMUTABLE:
                raise ValueError('IMMUTABLE_SHARED_INODE_REQUIRES_RECONCILIATION: ' + rel)
            self.rows.append(dict(path=rel, full=full, existed_before=exists,
                original_hash=digest, after_hash=verified[rel]['after_sha256'],
                mode=stat.S_IMODE(st.st_mode) if st else None, nlink=st.st_nlink if st else None,
                uid=st.st_uid if st else None, gid=st.st_gid if st else None,
                inode=[st.st_dev, st.st_ino] if st else None,
                filesystem_flags=flags, immutable=bool(flags and flags & self.IMMUTABLE),
                backup=os.path.join(backup_dir, str(len(self.rows)) + '.original'),
                new=new, fd=None, parent_fd=None,
                parent_inode=list(self.identity(os.stat(os.path.dirname(full)))),
                touched=False, modified=False, created_inode=None, replacement_inode=None, reserved_link=False))

    @staticmethod
    def read_file(path):
        with open(path, 'rb') as stream: return stream.read()

    @staticmethod
    def identity(st): return (st.st_dev, st.st_ino)

    def secure_parent(self, r):
        fd = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for component in r['path'].split('/')[:-1]:
                nxt = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd); fd = nxt
            if list(self.identity(os.fstat(fd))) != r['parent_inode']:
                raise ValueError('RACE_PARENT_CHANGED: ' + r['path'])
            return fd
        except BaseException:
            os.close(fd); raise

    def open_destination(self, r, flags, mode=0o644):
        parent = self.secure_parent(r)
        try: return os.open(os.path.basename(r['full']), flags | os.O_NOFOLLOW, mode, dir_fd=parent)
        finally: os.close(parent)

    def read_destination(self, r):
        fd = self.open_destination(r, os.O_RDONLY)
        with os.fdopen(fd, 'rb') as stream: return stream.read()

    @classmethod
    def flags(cls, fd):
        import array
        value = array.array('L', [0])
        fcntl.ioctl(fd, cls.GETFLAGS, value, True)
        return value[0]

    @classmethod
    def set_flags(cls, fd, flags):
        import array
        fcntl.ioctl(fd, cls.SETFLAGS, array.array('L', [flags]))

    def save(self, state):
        self.state = state
        value = dict(state=state, files=[{k:v for k,v in r.items() if k not in ('new','fd','parent_fd')} for r in self.rows])
        with open(self.journal + '.tmp', 'w') as out:
            json.dump(value, out, ensure_ascii=False); out.flush(); os.fsync(out.fileno())
        os.replace(self.journal + '.tmp', self.journal)
        fd = os.open(self.backup_dir, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)

    @staticmethod
    def metadata_unchanged(r, st):
        import stat
        if (stat.S_IMODE(st.st_mode), st.st_uid, st.st_gid, st.st_nlink) != (r['mode'], r['uid'], r['gid'], r['nlink'] + int(r['reserved_link'])):
            raise ValueError('RACE_METADATA_CHANGED: ' + r['path'])

    def unchanged(self, r):
        parent = self.secure_parent(r); os.close(parent)
        if not r['existed_before']:
            if os.path.lexists(r['full']): raise ValueError('RACE_NEW_PATH: ' + r['path'])
            return
        st = os.lstat(r['full'])
        self.metadata_unchanged(r, st)
        if [st.st_dev, st.st_ino] != r['inode'] or hashlib.sha256(self.read_destination(r)).hexdigest() != r['original_hash']:
            raise ValueError('RACE_SOURCE_CHANGED: ' + r['path'])

    def snapshot(self):
        for r in self.rows:
            self.unchanged(r)
            if r['existed_before']:
                with open(r['backup'], 'xb') as out:
                    out.write(self.read_destination(r)); out.flush(); os.fsync(out.fileno())
                if hashlib.sha256(self.read_file(r['backup'])).hexdigest() != r['original_hash']:
                    raise ValueError('BACKUP_HASH_MISMATCH')
                if r['nlink'] > 1:
                    parent = self.secure_parent(r)
                    try:
                        os.link(os.path.basename(r['full']),r['backup']+'.inode',src_dir_fd=parent,follow_symlinks=False)
                        r['reserved_link'] = True
                    finally: os.close(parent)
                    self.unchanged(r)
        self.save('backed_up')

    def owns(self, r):
        parent = self.secure_parent(r)
        try: st = os.stat(os.path.basename(r['full']),dir_fd=parent,follow_symlinks=False)
        finally: os.close(parent)
        return [st.st_dev, st.st_ino] == (r['replacement_inode'] or (r['inode'] if r['existed_before'] else r['created_inode']))

    def replace_shared_mutable(self, r):
        # Never modify the shared inode: keep its original bytes/metadata for aliases.
        self.unchanged(r)
        original = self.open_destination(r, os.O_RDONLY)
        temp = r['backup'] + '.replacement'
        fd = None
        try:
            if list(self.identity(os.fstat(original))) != r['inode']: raise ValueError('RACE_INODE_CHANGED')
            self.metadata_unchanged(r, os.fstat(original))
            if self.flags(original) != r['filesystem_flags']: raise ValueError('RACE_ATTRIBUTES_CHANGED')
            fd = os.open(temp, os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
            self.write_bytes(fd, r['new'])
            os.fchown(fd,r['uid'],r['gid']); os.fchmod(fd,r['mode'])
            for name in os.listxattr(original): os.setxattr(fd,name,os.getxattr(original,name))
            if self.flags(fd) != r['filesystem_flags']:
                # No broad flag handling: only an identical inherited mutable state is allowed.
                raise ValueError('SHARED_INODE_FLAGS_NOT_REPRODUCIBLE')
            os.fsync(fd)
            self.unchanged(r)
            parent = self.secure_parent(r)
            try:
                # Journal identity before atomic destination replacement, for recovery evidence.
                r['replacement_inode'] = list(self.identity(os.fstat(fd)))
                self.save('replacement_prepared')
                os.replace(temp,os.path.basename(r['full']),dst_dir_fd=parent)
                r['touched'] = True; r['modified'] = True
                r['fd'] = fd; fd = None
                os.fsync(parent)
            finally: os.close(parent)
            self.save('applying')
        finally:
            # A wrapper/syscall can report failure after rename took effect.
            # Recover ownership from the pre-journaled replacement identity.
            if fd is not None and r['replacement_inode']:
                try:
                    if self.owns(r):
                        r['touched'] = True; r['modified'] = True
                        r['fd'] = fd; fd = None
                    else:
                        r['replacement_inode'] = None
                except BaseException:
                    r['touched'] = True; r['modified'] = True
                    r['fd'] = fd; fd = None
            os.close(original)
            if fd is not None: os.close(fd)
            if os.path.exists(temp): os.unlink(temp)

    def write(self, r):
        # Repeat the expected hash check immediately before any attribute/write mutation.
        self.unchanged(r)
        if r['existed_before'] and r['nlink'] > 1:
            return self.replace_shared_mutable(r)
        if r['existed_before']:
            readfd = self.open_destination(r, os.O_RDONLY)
            if list(self.identity(os.fstat(readfd))) != r['inode']:
                os.close(readfd); raise ValueError('RACE_INODE_CHANGED')
            r['fd'] = readfd
            self.metadata_unchanged(r, os.fstat(readfd))
            if self.flags(readfd) != r['filesystem_flags']: raise ValueError('RACE_ATTRIBUTES_CHANGED')
            r['touched'] = True
            self.save('applying')
            if r['immutable']: self.set_flags(readfd, r['filesystem_flags'] & ~self.IMMUTABLE)
            self.unchanged(r)
            fd = self.open_destination(r, os.O_RDWR)
            st = os.fstat(fd)
            if [st.st_dev, st.st_ino] != r['inode']:
                os.close(fd); raise ValueError('RACE_INODE_CHANGED')
            os.close(readfd); r['fd'] = fd
        else:
            r['fd'] = self.open_destination(r, os.O_RDWR | os.O_CREAT | os.O_EXCL)
            st = os.fstat(r['fd']); r['created_inode'] = [st.st_dev, st.st_ino]
            r['touched'] = True
            self.save('applying')
        if r['existed_before']:
            self.metadata_unchanged(r, os.fstat(r['fd']))
            if hashlib.sha256(os.pread(r['fd'], os.fstat(r['fd']).st_size, 0)).hexdigest() != r['original_hash']:
                raise ValueError('RACE_SOURCE_CHANGED_BEFORE_WRITE')
        r['modified'] = True
        self.write_bytes(r['fd'], r['new'])
        if r['existed_before']:
            os.fchown(r['fd'], r['uid'], r['gid']); os.fchmod(r['fd'], r['mode'])
        self.save('applying')

    @staticmethod
    def write_bytes(fd, data):
        os.lseek(fd, 0, os.SEEK_SET)
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            if n <= 0: raise OSError('SHORT_WRITE')
            view = view[n:]
        os.ftruncate(fd, len(data)); os.fsync(fd)

    def restore_attributes(self, r):
        if r['existed_before'] and r['touched']:
            if list(self.identity(os.fstat(r['fd']))) != (r['replacement_inode'] or r['inode']): raise ValueError('ORIGINAL_FD_IDENTITY_LOST')
            current = self.flags(r['fd'])
            expected = r['filesystem_flags']
            if (current & ~self.IMMUTABLE) != (expected & ~self.IMMUTABLE):
                raise ValueError('UNEXPECTED_FILESYSTEM_FLAGS: ' + r['path'])
            if current != expected: self.set_flags(r['fd'], expected)
            if self.flags(r['fd']) != expected: raise ValueError('ATTRIBUTE_RESTORE_FAILED')

    def verify(self, rollback=False):
        import stat
        for r in self.rows:
            if rollback and not r['touched']: continue
            if rollback and r['existed_before'] and not r['modified']:
                if self.flags(r['fd']) != r['filesystem_flags']: raise ValueError('VERIFY_FLAGS_FAILED')
                continue
            if rollback and not r['existed_before']:
                if os.path.lexists(r['full']): raise ValueError('ROLLBACK_NEW_FILE_REMAINS')
                continue
            if not self.owns(r): raise ValueError('RACE_REPLACED_PATH: ' + r['path'])
            expected = r['original_hash'] if rollback else r['after_hash']
            if hashlib.sha256(self.read_destination(r)).hexdigest() != expected:
                raise ValueError('VERIFY_HASH_FAILED: ' + r['path'])
            if r['existed_before']:
                st = os.stat(r['full'])
                if (stat.S_IMODE(st.st_mode),st.st_uid,st.st_gid) != (r['mode'],r['uid'],r['gid']):
                    raise ValueError('VERIFY_METADATA_FAILED')
                if self.flags(r['fd']) != r['filesystem_flags']: raise ValueError('VERIFY_FLAGS_FAILED')

    def rollback(self):
        failures = []
        for r in reversed(self.rows):
            if not r['touched']: continue
            try:
                if not self.owns(r): raise ValueError('FOREIGN_INODE_NOT_TOUCHED: ' + r['path'])
                if r['replacement_inode'] and r['modified']:
                    alias = r['backup']+'.inode'
                    st = os.stat(alias,follow_symlinks=False)
                    if list(self.identity(st)) != r['inode']: raise ValueError('ROLLBACK_ORIGINAL_INODE_LOST')
                    if hashlib.sha256(self.read_file(alias)).hexdigest() != r['original_hash']: raise ValueError('ROLLBACK_SHARED_ORIGINAL_CHANGED')
                    parent = self.secure_parent(r)
                    try:
                        os.replace(alias,os.path.basename(r['full']),dst_dir_fd=parent)
                        r['reserved_link'] = False; r['replacement_inode'] = None
                        os.fsync(parent)
                    finally: os.close(parent)
                    if r['fd'] is not None: os.close(r['fd'])
                    r['fd'] = self.open_destination(r,os.O_RDONLY)
                elif r['existed_before'] and r['modified']:
                    current = self.flags(r['fd'])
                    if current & self.IMMUTABLE: self.set_flags(r['fd'], current & ~self.IMMUTABLE)
                    # The fd may still be read-only if failure occurred after unlocking.
                    fd = self.open_destination(r, os.O_RDWR)
                    try:
                        if [os.fstat(fd).st_dev,os.fstat(fd).st_ino] != r['inode']: raise ValueError('ROLLBACK_INODE_CHANGED')
                        self.write_bytes(fd, self.read_file(r['backup']))
                        os.fchown(fd,r['uid'],r['gid']); os.fchmod(fd,r['mode'])
                    finally: os.close(fd)
                elif not r['existed_before']:
                    parent = self.secure_parent(r)
                    try:
                        if not self.owns(r): raise ValueError('FOREIGN_NEW_FILE')
                        os.unlink(os.path.basename(r['full']),dir_fd=parent); os.fsync(parent)
                    finally: os.close(parent)
            except BaseException as exc:
                failures.append(str(exc))
            finally:
                try: self.restore_attributes(r)
                except BaseException as exc: failures.append(str(exc))
        try: self.verify(rollback=True)
        except BaseException as exc: failures.append(str(exc))
        self.save('rollback_failed' if failures else 'rolled_back')
        if failures: raise RuntimeError('ROLLBACK_FAILED: ' + '; '.join(failures))

    def close(self):
        for r in self.rows:
            if r['fd'] is not None: os.close(r['fd']); r['fd'] = None
            if r['reserved_link'] and (self.state in ('committed','rolled_back') or not r['touched']):
                alias = r['backup']+'.inode'
                if os.path.exists(alias) and list(self.identity(os.stat(alias,follow_symlinks=False))) == r['inode']:
                    os.unlink(alias); r['reserved_link'] = False


def _human_apply_locked(key, patch_id, confirm=False, skip_verify=False, manifest=None):
    """Human-only, manifest-bound transaction. Never grant autonomous privileges."""
    if 'admin' not in key['scope_set'] or key.get('actor_type') != 'human':
        raise ApiError('FORBIDDEN_SCOPE', 'применить правку может только человек ключом со scope admin')
    if not confirm: raise ApiError('CONFIRMATION_REQUIRED', 'confirm=true required')
    if skip_verify: raise ApiError('FORBIDDEN_SCOPE', 'human transaction requires verification')
    if not manifest: raise ApiError('MISSING_INPUT', 'verified manifest required')
    p = db.one('SELECT * FROM ext_dev_patches WHERE id=:i', i=int(patch_id))
    if not p: raise ApiError('JOB_NOT_FOUND', 'правка не найдена')
    if p['status'] == 'applied': return get_patch(patch_id)
    if p['status'] not in ('proposed','failed','rolled_back'): raise ApiError('JOB_NOT_RETRYABLE','patch state')
    edits = json.loads(p['edits_json'] or '[]')
    import tempfile
    parent = os.path.join(ROOT,'BORIS_AGENT_PATCHES'); os.makedirs(parent,exist_ok=True)
    backup_dir = tempfile.mkdtemp(prefix='patch_%s_' % p['id'],dir=parent)
    tx = _HumanTransaction(edits, manifest, backup_dir)
    log = ['BACKUP -> ' + backup_dir]
    try:
        tx.snapshot()
        for row in tx.rows: tx.write(row)
        for cfg in _verify_for([r['path'] for r in tx.rows]):
            result = subprocess.run(cfg['cmd'],cwd=os.path.join(ROOT,cfg['cwd']),capture_output=True,text=True,timeout=cfg['timeout'])
            log.append('VERIFY %s: %s\n%s' % (cfg['note'],result.returncode,((result.stdout or '')+(result.stderr or ''))[-3000:]))
            if result.returncode: raise RuntimeError('VERIFY_FAILED: ' + cfg['note'])
        for row in tx.rows: tx.restore_attributes(row)
        tx.verify(); tx.save('verified')
        db.q("UPDATE ext_dev_patches SET status='applied', applied_by=:a, applied_at=NOW(), backup_dir=:b, verify_output=:v, error_text=NULL WHERE id=:i",
             i=p['id'],a=auth.actor_of(key)['name'],b=backup_dir,v='\n'.join(log))
        tx.save('committed')
    except BaseException as exc:
        try: tx.rollback()
        except BaseException as rollback_error:
            try:
                db.q("UPDATE ext_dev_patches SET status='rollback_failed', backup_dir=:b, verify_output=:v, error_text=:e WHERE id=:i",i=p['id'],b=backup_dir,v='\n'.join(log),e=str(rollback_error)[:2000])
            except Exception as status_error:
                raise ApiError('POSTFLIGHT_MISMATCH', 'ROLLBACK_AND_STATUS_UPDATE_FAILED: '+str(rollback_error)+'; '+str(status_error), details={'backup_dir':backup_dir}) from exc
            raise ApiError('POSTFLIGHT_MISMATCH',str(rollback_error),details={'backup_dir':backup_dir}) from exc
        db.q("UPDATE ext_dev_patches SET status='rolled_back', backup_dir=:b, verify_output=:v, error_text=:e WHERE id=:i",i=p['id'],b=backup_dir,v='\n'.join(log),e=str(exc)[:2000])
        raise ApiError('POSTFLIGHT_MISMATCH','ROLLBACK_VERIFIED: '+str(exc),details={'backup_dir':backup_dir}) from exc
    finally: tx.close()
    return get_patch(p['id'])


# ------------------------------------------------ autonomous development apply
# Separate capability by design: existing human/admin apply() above is untouched.
AUTONOMOUS_POLICY_FILES = {
    "backend/app/ext_api/auth.py",
    "backend/app/ext_api/repo.py",
    "backend/app/ext_api/router.py",
    "backend/app/ext_api/schema.py",
    "backend/app/ext_api/mcp_server.py",
    "backend/app/ext_api/executor.py",
    "backend/app/ext_api/lead.py",
    "backend/app/ext_api/devflow.py",
    "backend/app/ext_api/keygen.py",
    "backend/app/ext_api/registry.py",
    "backend/app/ext_api/preflight.py",
    "backend/app/ext_api/postflight.py",
    "backend/app/ext_api/ops.py",
    "backend/app/ext_api/deploy.py",
    "backend/app/ext_api/gitdeploy.py",
    "backend/app/ext_api/ddl.sql",
}
AUTONOMOUS_DENY_PATH_PARTS = (
    "/migrations/", "/migration/", "/alembic/", "/systemd/", "/secrets/",
    "/credentials/", "/oauth/", "/billing/", "/payments/", "/payment/",
    "/telegram/", "/vk/", "/avito/", "/deploy/",
)
AUTONOMOUS_DENY_CONTENT = (
    re.compile(r"\b(?:DROP|TRUNCATE)\s+(?:TABLE|DATABASE|SCHEMA)\b", re.I),
    re.compile(r"\bALTER\s+TABLE\b", re.I),
    re.compile(r"\bDELETE\s+FROM\b", re.I),
    re.compile(r"\bsystemctl\b|\bservice\s+\S+\s+restart\b", re.I),
    re.compile(r"\b(?:avito|telegram|oauth|billing|payment[_ -]?provider)\b", re.I),
    re.compile(r"\b(?:client_secret|api_key|authorization)\s*=", re.I),
    re.compile(r"\b(?:grant|give|issue)[ _-]?admin\b", re.I),
)
AUTONOMOUS_OPEN_ORDER_STATES = ("queued", "running", "review_requested", "returned")
AUTONOMOUS_ACTIVE_JOB_STATES = (
    "queued", "planning", "running", "waiting", "review", "testing", "fixing", "acceptance"
)


def _norm_rel(path):
    return str(path or "").strip().lstrip("/").replace("\\", "/")


def _covers(root, path):
    root = _norm_rel(root).rstrip("/")
    path = _norm_rel(path)
    return bool(root) and (path == root or path.startswith(root + "/"))


def _overlap(a, b):
    return _covers(a, b) or _covers(b, a)


def _job_scope_paths(job):
    scope = job.get("scope") or {}
    out = []
    for key in ("paths", "files", "allowed_paths"):
        value = scope.get(key)
        if isinstance(value, str):
            out.append(value)
        elif isinstance(value, list):
            out.extend(str(x) for x in value if x)
    return [_norm_rel(x) for x in out if _norm_rel(x)]


def _autonomous_path_allowed(path):
    p = _norm_rel(path)
    low = "/" + p.lower()
    if p in AUTONOMOUS_POLICY_FILES:
        return False
    if any(part in low for part in AUTONOMOUS_DENY_PATH_PARTS):
        return False
    base = os.path.basename(p).lower()
    if base in ("schema.sql", "migration.sql") or base.startswith("migration_"):
        return False
    return True


def _preview_edit(edit):
    path = _norm_rel(edit.get("path"))
    full = safe_path(path)
    mode = (edit.get("mode") or "replace").lower()
    if mode == "full":
        if edit.get("content") is None:
            raise ApiError("VALIDATION_ERROR", f"{path}: content обязателен")
        return str(edit.get("content"))
    if mode != "replace":
        raise ApiError("VALIDATION_ERROR", f"{path}: неизвестный mode {mode}")
    if not os.path.isfile(full):
        raise ApiError("MISSING_INPUT", f"файла нет: {path}")
    cur = io.open(full, encoding="utf-8", errors="replace").read()
    old = edit.get("old")
    if not old or edit.get("new") is None:
        raise ApiError("VALIDATION_ERROR", f"{path}: нужны old/new")
    if cur.count(old) != 1:
        raise ApiError("VALIDATION_ERROR", f"{path}: patch устарел или old не уникален")
    return cur.replace(old, edit.get("new"), 1)


def _validate_autonomous_content(path, preview):
    if not _autonomous_path_allowed(path):
        raise ApiError("FORBIDDEN_SCOPE", f"autonomous-dev запрещено менять {path}")
    for rx in AUTONOMOUS_DENY_CONTENT:
        if rx.search(preview or ""):
            raise ApiError("FORBIDDEN_SCOPE",
                           f"{path}: изменение пересекает hard-deny development policy")


def _actor_has_job(order_actor, job):
    if order_actor in {job.get("owner_agent"), job.get("assigned_agent")}:
        return True
    rows = db.rows("""SELECT id, assigned_to FROM ext_a2a_orders
                     WHERE dev_job_id=:d AND status = ANY(:st)
                     ORDER BY id DESC LIMIT 20""",
                  d=job["id"], st=list(AUTONOMOUS_OPEN_ORDER_STATES))
    for row in rows:
        assigned = str(row.get("assigned_to") or "").strip().lower()
        actor = str(order_actor or "").strip().lower()
        if assigned == actor:
            return True
        # The one-action runner is a dedicated queue label, but execution is
        # still performed by the normal gpt-executor identity/capabilities.
        if assigned == "autonomous-dev-executor" and actor == "gpt-executor":
            return True
    return False


def _validate_autonomous_patch(key, patch_row, edits):
    from . import dev
    auth.need_scope(key, "write")
    auth.need_autonomous_dev(key)
    if patch_row["status"] != "approved":
        raise ApiError("FORBIDDEN_SCOPE",
                       "autonomous-dev применяет только patch после policy review ACCEPT")
    actor = auth.actor_of(key)["name"]
    if patch_row.get("author") != actor:
        raise ApiError("FORBIDDEN_SCOPE", "autonomous-dev применяет только собственный patch")

    job = dev.get(patch_row["dev_job_id"])
    if not _actor_has_job(actor, job):
        raise ApiError("FORBIDDEN_SCOPE", "actor не назначен на этот dev_job/A2A order")
    scope_flags = job.get("scope") or {}
    if scope_flags.get("coordination_only") is True:
        raise ApiError(
            "FORBIDDEN_SCOPE",
            "coordination-only dev_job не имеет права менять production-файлы; "
            "исправление должно выполняться специализированной scoped-задачей",
        )
    if scope_flags.get("read_only") is True:
        raise ApiError(
            "FORBIDDEN_SCOPE",
            "read-only dev_job не имеет права менять production-файлы/данные",
        )

    paths = [_norm_rel(e.get("path")) for e in edits]
    if not paths or any(not p for p in paths):
        raise ApiError("VALIDATION_ERROR", "patch не содержит допустимых файлов")
    _deny_autonomous_money_contour(paths)

    scope_paths = _job_scope_paths(job)
    locks = [_norm_rel(x) for x in (job.get("locks") or []) if _norm_rel(x)]
    if not scope_paths:
        raise ApiError("FORBIDDEN_SCOPE", "autonomous-dev требует явный file scope dev_job")
    if not locks:
        raise ApiError("FORBIDDEN_SCOPE", "autonomous-dev требует явные file locks dev_job")

    for edit, path in zip(edits, paths):
        safe_path(path)
        if not any(_covers(root, path) for root in scope_paths):
            raise ApiError("FORBIDDEN_SCOPE", f"{path}: вне scope dev_job")
        if not any(_covers(lock, path) for lock in locks):
            raise ApiError("FORBIDDEN_SCOPE", f"{path}: вне locks dev_job")
        _validate_autonomous_content(path, _preview_edit(edit))

    others = db.rows("""SELECT id, locks_json FROM ext_dev_jobs
                        WHERE id<>:d AND status = ANY(:st)""",
                     d=job["id"], st=list(AUTONOMOUS_ACTIVE_JOB_STATES))
    for other in others:
        try:
            other_locks = json.loads(other.get("locks_json") or "[]")
        except Exception:
            other_locks = []
        for path in paths:
            if any(_overlap(lock, path) for lock in other_locks):
                raise ApiError("FORBIDDEN_SCOPE",
                               f"{path}: конфликт file lock с dev_{other['id']}")
    _reviewed_server_owned_context(patch_row, job, paths)
    return job, paths


def _checked(cmd, cwd, timeout, label, log):
    t0 = time.time()
    r = subprocess.run(cmd, cwd=os.path.join(ROOT, cwd), capture_output=True,
                       text=True, timeout=timeout)
    tail = ((r.stdout or "") + (r.stderr or ""))[-5000:]
    log.append(f"{label}: exit_code={r.returncode}; {int(time.time()-t0)}s\n{tail}")
    if r.returncode != 0:
        raise RuntimeError(f"{label} failed with exit_code={r.returncode}")


@contextmanager
def _patch_apply_lock(patch_id):
    """Global cross-process single-writer lock for ALL production source writes.

    The previous lock name included patch_id, so two different patches could
    modify live production at the same time. One global lock is intentional:
    source mutation is serialized regardless of patch id or apply path.
    """
    pid = int(patch_id)
    lock_path = "/tmp/boris-production-source-write.lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o666)
    try:
        # All production writers currently run as root, but tests and operator
        # tooling may use a different service account. The lock contains no
        # secrets; make it writable so every legitimate writer contends on the
        # SAME inode instead of silently falling back to another lock.
        os.chmod(lock_path, 0o666)
    except OSError:
        pass
    fh = os.fdopen(fd, "r+", encoding="utf-8")
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fh.seek(0)
            owner = (fh.read() or "unknown").strip()
            raise ApiError(
                "JOB_NOT_RETRYABLE",
                f"production source already has an active writer ({owner}); patch {pid} blocked",
            )
        fh.seek(0)
        fh.truncate()
        fh.write(f"pid={os.getpid()} patch={pid} started={datetime.utcnow().isoformat()}Z")
        fh.flush()
        yield
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=595)
        fh.close()


def autonomous_apply(key, patch_id):
    """Policy-enforced apply for autonomous-dev. There is no skip_verify switch."""
    with _patch_apply_lock(patch_id):
        return _autonomous_apply_locked(key, patch_id)


def _autonomous_apply_locked(key, patch_id):
    p = db.one("SELECT * FROM ext_dev_patches WHERE id=:i", i=int(patch_id))
    if not p:
        raise ApiError("JOB_NOT_FOUND", "правка не найдена")
    edits = json.loads(p["edits_json"] or "[]")
    job, paths = _validate_autonomous_patch(key, p, edits)

    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(ROOT, "BORIS_AGENT_PATCHES", f"patch_{p['id']}_{stamp}")
    os.makedirs(backup_dir, exist_ok=True)
    written, existed, log = [], {}, []
    try:
        # Establish a baseline while the per-patch lock is held and before any
        # write. A dirty production suite may have known failures; autonomous
        # apply must reject only NEW failures introduced by this patch.
        backend_test_baseline = None
        if any(path.startswith("backend/") for path in paths):
            baseline_rc, baseline_out = _pytest_run("backend")
            baseline_ids = _pytest_failure_ids(baseline_out)
            if baseline_rc != 0 and not baseline_ids:
                raise ApiError(
                    "VALIDATION_ERROR",
                    "backend pytest baseline failed without classifiable FAILED/ERROR nodes",
                )
            backend_test_baseline = (baseline_rc, baseline_out)
            log.append(
                "TEST backend BASELINE rc=%s failures=%s"
                % (baseline_rc, len(baseline_ids))
            )

        # BACKUP before any write.
        for edit in edits:
            rel = edit["path"]
            full = safe_path(rel)
            existed[rel] = os.path.isfile(full)
            if existed[rel]:
                dst = os.path.join(backup_dir, rel.replace("/", "__"))
                io.open(dst, "w", encoding="utf-8").write(
                    io.open(full, encoding="utf-8", errors="replace").read())
        log.append(f"BACKUP=PASS {backup_dir}")

        # Revalidate ownership/scope/locks/content immediately before WRITE.
        _validate_autonomous_patch(key, p, edits)
        log.append("POLICY=PASS ownership scope locks content")

        for edit in edits:
            rel = edit["path"]
            full = safe_path(rel)
            if edit["mode"] == "full":
                os.makedirs(os.path.dirname(full), exist_ok=True)
                io.open(full, "w", encoding="utf-8").write(edit["content"])
            else:
                cur = io.open(full, encoding="utf-8", errors="replace").read()
                if cur.count(edit["old"]) != 1:
                    raise ApiError("VALIDATION_ERROR", f"{rel}: patch устарел перед write")
                io.open(full, "w", encoding="utf-8").write(
                    cur.replace(edit["old"], edit["new"], 1))
            written.append(rel)
        log.append("WRITE=PASS " + ", ".join(written))

        # Exact post-write content verification.
        for edit in edits:
            cur = io.open(safe_path(edit["path"]), encoding="utf-8", errors="replace").read()
            if edit["mode"] == "full" and cur != edit["content"]:
                raise RuntimeError(f"VERIFY content mismatch: {edit['path']}")
            if edit["mode"] == "replace" and edit["new"] not in cur:
                raise RuntimeError(f"VERIFY replacement missing: {edit['path']}")
        log.append("VERIFY=PASS content")

        py = [safe_path(path) for path in paths if path.endswith(".py")]
        if py:
            _checked([sys.executable, "-m", "py_compile"] + py, ".", 180,
                     "COMPILE", log)
        for cfg in _verify_for(paths):
            _checked(cfg["cmd"], cfg["cwd"], cfg["timeout"],
                     "VERIFY " + cfg["note"], log)
        if any(path.startswith("backend/") for path in paths):
            if backend_test_baseline is None:
                raise ApiError("VALIDATION_ERROR", "backend pytest baseline missing")
            post_rc, post_out = _pytest_run("backend")
            comparison = _pytest_regression_compare(
                backend_test_baseline[0], backend_test_baseline[1],
                post_rc, post_out,
            )
            comparison = _confirm_pytest_regression(
                "backend",
                backend_test_baseline[0],
                backend_test_baseline[1],
                comparison,
            )
            if not comparison.get("ok"):
                raise ApiError(
                    "VALIDATION_ERROR",
                    "backend pytest regression: %s; new=%s"
                    % (
                        comparison.get("reason") or "failed",
                        ",".join((comparison.get("new_failures") or [])[:20]),
                    ),
                )
            transient = comparison.get("transient_failures") or []
            log.append(
                "TEST backend REGRESSION_BASELINE=PASS baseline_failures=%s "
                "post_failures=%s new_failures=0 transient_confirmed=%s"
                % (
                    comparison.get("baseline_failures", 0),
                    comparison.get("post_failures", 0),
                    ",".join(transient[:20]) if transient else "none",
                )
            )

        db.q("""UPDATE ext_dev_patches SET status='applied', applied_by=:a, applied_at=NOW(),
                    backup_dir=:b, verify_output=:v, error_text=NULL WHERE id=:i""",
             i=p["id"], a=auth.actor_of(key)["name"], b=backup_dir,
             v="\n".join(log)[:60000])
        from . import dev
        dev.event(job["id"], job["current_phase"], auth.actor_of(key)["name"],
                  "autonomous_patch_applied",
                  {"patch_id": p["id"], "files": written, "review_required": True})
        return get_patch(p["id"])

    except Exception as ex:
        restored = []
        for rel in reversed(written):
            dst = safe_path(rel)
            src = os.path.join(backup_dir, rel.replace("/", "__"))
            if existed.get(rel) and os.path.isfile(src):
                io.open(dst, "w", encoding="utf-8").write(
                    io.open(src, encoding="utf-8", errors="replace").read())
                restored.append(rel)
            elif not existed.get(rel) and os.path.isfile(dst):
                os.remove(dst)
                restored.append(rel + " (removed)")
        log.append("ROLLBACK=PASS " + (", ".join(restored) or "nothing-written"))
        db.q("""UPDATE ext_dev_patches SET status='rolled_back', backup_dir=:b,
                    verify_output=:v, error_text=:e WHERE id=:i""",
             i=p["id"], b=backup_dir, v="\n".join(log)[:60000], e=str(ex)[:2000])
        raise ApiError("POSTFLIGHT_MISMATCH", f"autonomous patch rolled back: {ex}",
                       details={"patch_id": p["id"], "restored": restored,
                                "verify_tail": "\n".join(log)[-3000:]})


# ------------------------------------------------ reviewed service auto-apply
# This is intentionally NOT an API/admin capability. It is called only by the
# local boris-deploy daemon after automated review. The trusted bootstrap core
# remains human-owned; ordinary runtime patches can flow autonomously.
# Reviewed deploy may change ordinary application/core development files,
# including the development machinery itself. Owner-only surfaces remain
# fail-closed: credentials/auth key issuance and schema/data DDL are not an
# autonomous-development concern.
REVIEWED_AUTO_DENY_FILES = {
    "backend/app/ext_api/auth.py",
    "backend/app/ext_api/keygen.py",
    "backend/app/ext_api/ddl.sql",
    "backend/app/api/auth.py",
    "backend/app/services/platform_roles.py",
    # Bootstrap/security policy itself remains owner-only after this patch.
    "backend/app/ext_api/repo.py",
    "backend/app/ext_api/deploy.py",
    "backend/app/ext_api/router.py",
    "backend/app/ext_api/schema.py",
    "backend/app/ext_api/mcp_server.py",
}
REVIEWED_AUTO_CORE_FILES = {
    "backend/app/ext_api/repo.py",
    "backend/app/ext_api/a2a.py",
    "backend/app/ext_api/executor.py",
    "backend/app/ext_api/deploy.py",
    "backend/app/ext_api/devflow.py",
    "backend/app/ext_api/router.py",
    "backend/app/ext_api/schema.py",
    "backend/app/ext_api/db.py",
    "backend/app/ext_api/mcp_server.py",
    "backend/app/ext_api/gitdeploy.py",
    "backend/app/ext_api/lead.py",
    "backend/app/ext_api/preflight.py",
    "backend/app/ext_api/postflight.py",
    "backend/app/ext_api/registry.py",
}
REVIEWED_AUTO_PREFIXES = (
    # Modern BORIS application surfaces. Exact scope+locks on each dev_job,
    # hard-deny paths/content, money-contour protection, backup, build/tests and
    # runtime postflight remain mandatory. DB models/migrations/bootstrap policy
    # are intentionally not in this allowlist.
    "backend/app/ext_api/",
    "backend/app/api/",
    "backend/app/services/",
    "backend/app/crm/",
    "backend/app/director/",
    "backend/app/factory/",
    "backend/app/knowledge/",
    "backend/app/monitoring/",
    "backend/app/parsers/",
    "backend/app/repositories/",
    "backend/app/schemas/",
    "backend/app/video_factory/",
    "backend/tests/",
    "frontend/app/",
    "frontend/components/",
    "frontend/lib/",
    "frontend/src/",
)


def _server_owned_job(job):
    scope = job.get("scope") or {}
    if scope.get("server_owned") is True:
        return True
    # Backward-compatible proof for server-owned jobs created before the
    # explicit marker existed (notably dev_1056): require the exact lifecycle
    # provenance event written only by startDevelopmentTask.
    if scope.get("autonomous_dev") is not True:
        return False
    row = db.one("""SELECT id FROM ext_dev_events
                    WHERE dev_job_id=:d AND phase='PLANNING' AND kind='phase_running'
                      AND payload_json LIKE :needle
                    ORDER BY id LIMIT 1""",
                 d=job["id"], needle='%startDevelopmentTask: server-owned lifecycle%')
    return bool(row)


def _executor_patch_author(author):
    name = str(author or "").strip().lower()
    return bool(re.fullmatch(r"(?:gpt-executor|boris-executor)(?:-\d+)?", name))


def _accepted_review_for_patch(patch_row, paths):
    rows = db.rows("""SELECT o.id, o.last_evidence_json, m.created_at AS accepted_at
                      FROM ext_a2a_orders o
                      JOIN LATERAL (
                          SELECT created_at FROM ext_a2a_messages
                           WHERE order_id=o.id AND kind='ACCEPT'
                           ORDER BY id DESC LIMIT 1
                      ) m ON TRUE
                     WHERE o.dev_job_id=:d AND o.status='accepted'
                       AND m.created_at >= :created
                     ORDER BY o.id DESC LIMIT 20""",
                   d=patch_row["dev_job_id"], created=patch_row.get("created_at"))
    wanted = set(paths)
    for row in rows:
        try:
            ev = json.loads(row.get("last_evidence_json") or "{}")
        except Exception:
            ev = {}
        changed = {_norm_rel(x) for x in (ev.get("files_changed") or []) if _norm_rel(x)}
        if wanted and wanted.issubset(changed):
            return {"order_id": row["id"], "accepted_at": row.get("accepted_at"),
                    "files_changed": sorted(changed)}
    return None


def _reviewed_server_owned_context(patch_row, job, paths):
    if not _server_owned_job(job):
        raise ApiError("FORBIDDEN_SCOPE", "reviewed-auto требует SERVER_OWNED dev_job")
    if not _executor_patch_author(patch_row.get("author")):
        raise ApiError("FORBIDDEN_SCOPE", "reviewed-auto принимает только executor-authored patch")
    scope_paths = _job_scope_paths(job)
    locks = [_norm_rel(x) for x in (job.get("locks") or []) if _norm_rel(x)]
    for path in paths:
        if not any(_covers(root, path) for root in scope_paths):
            raise ApiError("FORBIDDEN_SCOPE", f"{path}: вне scope server-owned dev_job")
        if not any(_covers(lock, path) for lock in locks):
            raise ApiError("FORBIDDEN_SCOPE", f"{path}: вне locks server-owned dev_job")
    review = _accepted_review_for_patch(patch_row, paths)
    if not review:
        raise ApiError("FORBIDDEN_SCOPE",
                       "patch не покрыт последующим A2A review verdict ACCEPT с evidence")
    return review


def _reviewed_auto_validate(patch_row, edits):
    if patch_row["status"] not in ("proposed", "approved"):
        raise ApiError("JOB_NOT_RETRYABLE",
                       f"reviewed-auto ожидает proposed/approved, сейчас {patch_row['status']}")
    paths = []
    for edit in edits:
        path = _norm_rel(edit.get("path"))
        if not path or not any(path.startswith(pref) for pref in REVIEWED_AUTO_PREFIXES):
            raise ApiError("FORBIDDEN_SCOPE", f"reviewed-auto: путь запрещён: {path}")
        if path in REVIEWED_AUTO_DENY_FILES:
            raise ApiError("FORBIDDEN_SCOPE", f"reviewed-auto: owner-only file denied: {path}")
        if not _autonomous_path_allowed(path) and path not in REVIEWED_AUTO_CORE_FILES:
            raise ApiError("FORBIDDEN_SCOPE", f"reviewed-auto: core file denied: {path}")
        full = safe_path(path)
        if not os.path.isfile(full):
            raise ApiError("FORBIDDEN_SCOPE",
                           f"reviewed-auto создаёт новые файлы только через human bootstrap: {path}")
        preview = _preview_edit(edit)
        # Existing core files legitimately contain words such as systemctl,
        # oauth or payment while implementing guards. Judge only the proposed
        # changed fragment, otherwise any harmless edit to deploy.py is
        # permanently impossible. Hard-deny additions still fail closed.
        changed = (edit.get("content") if edit.get("mode") == "full" else edit.get("new")) or ""
        for rx in AUTONOMOUS_DENY_CONTENT:
            if rx.search(str(changed)):
                raise ApiError("FORBIDDEN_SCOPE",
                               f"{path}: hard-deny development policy")
        if path.endswith(".py"):
            try:
                compile(preview, path, "exec")
            except Exception as e:
                raise ApiError("VALIDATION_ERROR", f"{path}: preview compile failed: {e}")
        paths.append(path)
    _deny_autonomous_money_contour(paths)
    from . import dev
    job = dev.get(patch_row["dev_job_id"])
    _reviewed_server_owned_context(patch_row, job, paths)
    return paths


def approve_reviewed_auto(patch_id):
    """Automated REVIEW gate; records approval but changes no working files."""
    p = db.one("SELECT * FROM ext_dev_patches WHERE id=:i", i=int(patch_id))
    if not p:
        raise ApiError("JOB_NOT_FOUND", "правка не найдена")
    edits = json.loads(p["edits_json"] or "[]")
    paths = _reviewed_auto_validate(p, edits)
    db.q("""UPDATE ext_dev_patches SET status='approved',
                verify_output=COALESCE(verify_output,'') || :v,
                error_text=NULL WHERE id=:i AND status='proposed'""",
         i=p["id"], v="\nAUTO_REVIEW=PASS paths=" + ",".join(paths))
    from . import dev
    dev.event(p["dev_job_id"], "FIXING", "autonomous-dev", "patch_review_approved",
              {"patch_id": p["id"], "files": paths,
               "policy": "SERVER_OWNED+EXECUTOR_AUTHOR+ACCEPT+SCOPE+LOCKS+SAFETY+PREFLIGHT"})
    return get_patch(p["id"])


def _pytest_failure_ids(output):
    """Return stable failing pytest node ids from the short test summary."""
    ids = set()
    for raw in str(output or "").splitlines():
        line = raw.strip()
        prefix = None
        if line.startswith("FAILED "):
            prefix = "FAILED"
        elif line.startswith("ERROR "):
            prefix = "ERROR"
        if not prefix:
            continue
        rest = line[len(prefix):].strip()
        node = rest.split(" ", 1)[0].strip()
        if node:
            ids.add(prefix + ":" + node)
    return ids


def _pytest_regression_compare(baseline_rc, baseline_output, post_rc, post_output):
    """Fail only on a new test regression; never hide an unclassified runner error."""
    baseline_ids = _pytest_failure_ids(baseline_output)
    post_ids = _pytest_failure_ids(post_output)
    if int(post_rc) == 0:
        return {
            "ok": True,
            "baseline_failures": len(baseline_ids),
            "post_failures": 0,
            "new_failures": [],
        }
    if int(baseline_rc) == 0:
        return {
            "ok": False,
            "reason": "previously_green_suite_failed",
            "baseline_failures": 0,
            "post_failures": len(post_ids),
            "new_failures": sorted(post_ids),
        }
    if not baseline_ids:
        return {
            "ok": False,
            "reason": "baseline_pytest_failure_unclassified",
            "baseline_failures": 0,
            "post_failures": len(post_ids),
            "new_failures": sorted(post_ids),
        }
    if not post_ids:
        return {
            "ok": False,
            "reason": "post_pytest_failure_unclassified",
            "baseline_failures": len(baseline_ids),
            "post_failures": 0,
            "new_failures": [],
        }
    new_ids = sorted(post_ids - baseline_ids)
    return {
        "ok": not new_ids,
        "reason": None if not new_ids else "new_test_regressions",
        "baseline_failures": len(baseline_ids),
        "post_failures": len(post_ids),
        "new_failures": new_ids,
    }


def _pytest_run(cwd, timeout=900):
    workdir = os.path.join(ROOT, cwd)
    base = [sys.executable, "-m", "pytest", "tests", "-q", "--tb=no"]
    command = base
    run_timeout = int(timeout)
    # PRODUCTION_QA_RESOURCE_ENVELOPE_V1:
    # A full regression is heavy maintenance, never a reason to starve live MOP,
    # CRM, telephony or recovery. On the canonical root production host execute
    # pytest in a transient systemd unit capped to one CPU with low CPU/IO
    # priority and bounded RAM. RuntimeMaxSec also guarantees cleanup if the
    # caller dies or the suite hangs.
    if (
        os.path.abspath(ROOT) == "/root/BORIS"
        and os.geteuid() == 0
        and os.path.isfile("/usr/bin/systemd-run")
    ):
        unit = "boris-qa-%s-%s" % (os.getpid(), int(time.time() * 1000) % 1000000000)
        command = [
            "/usr/bin/systemd-run", "--wait", "--pipe", "--collect", "--quiet",
            "--unit=" + unit,
            "--property=CPUQuota=100%",
            "--property=Nice=15",
            "--property=IOSchedulingClass=idle",
            "--property=MemoryMax=2G",
            "--property=RuntimeMaxSec=%ss" % max(30, int(timeout)),
            "--working-directory=" + workdir,
        ] + base
        # systemd owns the hard runtime limit; give its client a short grace to
        # report the final exit status and resource summary.
        run_timeout = int(timeout) + 20
    try:
        p = subprocess.run(
            command,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=run_timeout,
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired as exc:
        out = ((exc.stdout or "") if isinstance(exc.stdout, str) else "")
        out += ((exc.stderr or "") if isinstance(exc.stderr, str) else "")
        return 124, out + "\nPYTEST_TIMEOUT"


def _confirm_pytest_regression(cwd, baseline_rc, baseline_output, comparison,
                               attempts=2):
    """Confirm a first-pass NEW pytest regression before rollback.

    A single full-suite run may be noisy in production. Only classifiable NEW
    failures are eligible for confirmation. Two consecutive full-suite reruns
    on the same written candidate must both compare cleanly to the original
    baseline. Any repeated/new/unclassified failure remains fail-closed.
    """
    if comparison.get("ok"):
        return comparison
    if comparison.get("reason") not in (
        "new_test_regressions", "previously_green_suite_failed"
    ):
        return comparison
    original_new = list(comparison.get("new_failures") or [])
    if not original_new:
        return comparison

    confirmations = []
    for idx in range(max(1, int(attempts))):
        rc, out = _pytest_run(cwd)
        check = _pytest_regression_compare(
            baseline_rc, baseline_output, rc, out
        )
        confirmations.append({
            "attempt": idx + 1,
            "ok": bool(check.get("ok")),
            "reason": check.get("reason"),
            "new_failures": list(check.get("new_failures") or []),
            "post_failures": check.get("post_failures", 0),
        })
        if not check.get("ok"):
            merged = sorted(set(
                original_new + list(check.get("new_failures") or [])
            ))
            return {
                **check,
                "ok": False,
                "reason": check.get("reason") or "confirmed_test_regression",
                "new_failures": merged,
                "initial_new_failures": original_new,
                "confirmations": confirmations,
            }

    return {
        **comparison,
        "ok": True,
        "reason": None,
        "new_failures": [],
        "transient_failures": original_new,
        "confirmations": confirmations,
    }


def reviewed_auto_apply(patch_id):
    """Restricted internal apply for an already approved patch."""
    with _patch_apply_lock(patch_id):
        return _reviewed_auto_apply_locked(patch_id)


def _reviewed_auto_apply_locked(patch_id):
    """Apply one reviewed patch while the shared per-patch lock is held.

    No key, admin scope or skip_verify exists on this path. Every target is an
    existing allowlisted file; compile + repository verify + backend tests run
    before status becomes applied. Any failure restores the backup.
    """
    p = db.one("SELECT * FROM ext_dev_patches WHERE id=:i", i=int(patch_id))
    if not p:
        raise ApiError("JOB_NOT_FOUND", "правка не найдена")
    if p["status"] != "approved":
        raise ApiError("FORBIDDEN_SCOPE", "reviewed-auto применяет только approved patch")
    edits = json.loads(p["edits_json"] or "[]")
    paths = _reviewed_auto_validate(p, edits)
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(ROOT, "BORIS_AGENT_PATCHES",
                              f"reviewed_{p['id']}_{stamp}")
    os.makedirs(backup_dir, exist_ok=True)
    written, log = [], []
    try:
        for edit in edits:
            rel = edit["path"]
            src = safe_path(rel)
            io.open(os.path.join(backup_dir, rel.replace("/", "__")),
                    "w", encoding="utf-8").write(
                        io.open(src, encoding="utf-8", errors="replace").read())
        log.append("BACKUP=PASS")
        backend_test_baseline = None
        if any(x.startswith("backend/") for x in paths):
            baseline_rc, baseline_out = _pytest_run("backend")
            baseline_ids = _pytest_failure_ids(baseline_out)
            if baseline_rc != 0 and not baseline_ids:
                raise ApiError(
                    "VALIDATION_ERROR",
                    "backend pytest baseline failed without classifiable FAILED/ERROR nodes",
                )
            backend_test_baseline = (baseline_rc, baseline_out)
            log.append(
                "TEST backend BASELINE rc=%s failures=%s"
                % (baseline_rc, len(baseline_ids))
            )
        _reviewed_auto_validate(p, edits)
        for edit in edits:
            rel = edit["path"]
            full = safe_path(rel)
            cur = io.open(full, encoding="utf-8", errors="replace").read()
            if edit["mode"] == "full":
                io.open(full, "w", encoding="utf-8").write(edit["content"])
            else:
                if cur.count(edit["old"]) != 1:
                    raise ApiError("VALIDATION_ERROR", f"{rel}: patch устарел перед write")
                io.open(full, "w", encoding="utf-8").write(
                    cur.replace(edit["old"], edit["new"], 1))
            written.append(rel)
        py = [safe_path(x) for x in paths if x.endswith(".py")]
        if py:
            _checked([sys.executable, "-m", "py_compile"] + py, ".", 180,
                     "COMPILE", log)
        for cfg in _verify_for(paths):
            _checked(cfg["cmd"], cfg["cwd"], cfg["timeout"],
                     "VERIFY " + cfg["note"], log)
        if any(x.startswith("backend/") for x in paths):
            if backend_test_baseline is None:
                raise ApiError("VALIDATION_ERROR", "backend pytest baseline missing")
            post_rc, post_out = _pytest_run("backend")
            comparison = _pytest_regression_compare(
                backend_test_baseline[0], backend_test_baseline[1],
                post_rc, post_out,
            )
            comparison = _confirm_pytest_regression(
                "backend",
                backend_test_baseline[0],
                backend_test_baseline[1],
                comparison,
            )
            if not comparison.get("ok"):
                raise ApiError(
                    "VALIDATION_ERROR",
                    "backend pytest regression: %s; new=%s"
                    % (
                        comparison.get("reason") or "failed",
                        ",".join((comparison.get("new_failures") or [])[:20]),
                    ),
                )
            transient = comparison.get("transient_failures") or []
            log.append(
                "TEST backend REGRESSION_BASELINE=PASS baseline_failures=%s "
                "post_failures=%s new_failures=0 transient_confirmed=%s"
                % (
                    comparison.get("baseline_failures", 0),
                    comparison.get("post_failures", 0),
                    ",".join(transient[:20]) if transient else "none",
                )
            )
        db.q("""UPDATE ext_dev_patches SET status='applied', applied_by='autonomous-dev',
                    applied_at=NOW(), backup_dir=:b, verify_output=COALESCE(verify_output,'') || :v,
                    error_text=NULL WHERE id=:i""",
             i=p["id"], b=backup_dir, v="\n" + "\n".join(log)[:50000])
        from . import dev
        job = dev.get(p["dev_job_id"])
        dev.event(job["id"], job["current_phase"], "autonomous-dev",
                  "autonomous_patch_applied",
                  {"patch_id": p["id"], "files": paths, "verify": "PASS"})
        return get_patch(p["id"])
    except Exception as ex:
        for rel in reversed(written):
            src = os.path.join(backup_dir, rel.replace("/", "__"))
            if os.path.isfile(src):
                io.open(safe_path(rel), "w", encoding="utf-8").write(
                    io.open(src, encoding="utf-8", errors="replace").read())
        db.q("""UPDATE ext_dev_patches SET status='rolled_back', backup_dir=:b,
                    error_text=:e, verify_output=COALESCE(verify_output,'') || :v WHERE id=:i""",
             i=p["id"], b=backup_dir, e=str(ex)[:2000],
             v="\nROLLBACK=PASS " + str(ex)[:3000])
        raise ApiError("POSTFLIGHT_MISMATCH", f"reviewed-auto rollback: {ex}")


def mark_reviewed_auto_runtime_qa(patch_id, facts):
    p = db.one("SELECT * FROM ext_dev_patches WHERE id=:i", i=int(patch_id))
    if not p or p["status"] != "applied":
        raise ApiError("JOB_NOT_RETRYABLE", "runtime QA требует applied patch")
    db.q("""UPDATE ext_dev_patches
               SET verify_output=COALESCE(verify_output,'') || :v
             WHERE id=:i""", i=p["id"],
         v="\nAUTO_RUNTIME_QA=PASS " + json.dumps(facts, ensure_ascii=False, default=str)[:4000])
    from . import dev
    job = dev.get(p["dev_job_id"])
    dev.event(job["id"], job["current_phase"], "autonomous-dev",
              "autonomous_apply_qa_pass", {"patch_id": p["id"], "facts": facts})
    return get_patch(p["id"])


def reviewed_order_apply_state(order):
    """Lead gate: accepted executor patches must be applied+runtime-QA before advance."""
    ev = order.get("last_evidence") or {}
    changed = {_norm_rel(x) for x in (ev.get("files_changed") or []) if _norm_rel(x)}
    if not changed:
        return {"ready": True, "patches": []}
    sql = """SELECT * FROM ext_dev_patches
              WHERE dev_job_id=:d AND created_at <= :accepted"""
    params = {
        "d": int(str(order["dev_job_id"]).replace("dev_", "")),
        "accepted": order.get("updated_at"),
    }
    # ORDER_PATCH_WINDOW_V1: an accepted retry order owns only patches created
    # during that order's lifetime. Without this lower bound, a rolled-back
    # patch from the previous retry remains "waiting" forever even after the
    # current retry has a newer APPLIED+runtime-QA patch for the same file.
    # Legacy/unit fixtures without created_at preserve the older query shape.
    if order.get("created_at") is not None:
        sql += " AND created_at >= :order_created"
        params["order_created"] = order.get("created_at")
    sql += " ORDER BY id"
    rows = db.rows(sql, **params)
    relevant = []
    for p in rows:
        if not _executor_patch_author(p.get("author")):
            continue
        edits = json.loads(p.get("edits_json") or "[]")
        paths = {_norm_rel(e.get("path")) for e in edits if _norm_rel(e.get("path"))}
        if paths and paths.issubset(changed):
            relevant.append(p)
    waiting = [p["id"] for p in relevant
               if p.get("status") != "applied" or
               "AUTO_RUNTIME_QA=PASS" not in (p.get("verify_output") or "")]
    return {"ready": not waiting, "patches": [p["id"] for p in relevant], "waiting": waiting}


def rollback_reviewed_auto(patch_id, reason):
    """Serialize runtime rollback with every other production source writer."""
    with _patch_apply_lock(patch_id):
        return _rollback_reviewed_auto_locked(patch_id, reason)


def _rollback_reviewed_auto_locked(patch_id, reason):
    """Rollback an applied reviewed patch after runtime/postflight failure."""
    p = db.one("SELECT * FROM ext_dev_patches WHERE id=:i", i=int(patch_id))
    if not p or p["status"] != "applied" or not p.get("backup_dir"):
        return False
    edits = json.loads(p["edits_json"] or "[]")
    restored = []
    for edit in edits:
        rel = edit["path"]
        src = os.path.join(p["backup_dir"], rel.replace("/", "__"))
        if os.path.isfile(src):
            io.open(safe_path(rel), "w", encoding="utf-8").write(
                io.open(src, encoding="utf-8", errors="replace").read())
            restored.append(rel)
    db.q("""UPDATE ext_dev_patches SET status='rolled_back', error_text=:e,
                verify_output=COALESCE(verify_output,'') || :v WHERE id=:i""",
         i=p["id"], e=str(reason)[:2000],
         v="\nRUNTIME_ROLLBACK=PASS " + ",".join(restored))
    return True
