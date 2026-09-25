"""MediaStorageService — единственное место, которое знает, где лежит файл.

Бизнес-логика оперирует storage_key (относительный путь), а не абсолютными
путями. Сегодня backend = local (тот же каталог, что отдаёт /images), завтра
S3/CDN: меняется только этот модуль.

Инвариант Stage A: сервис НЕ двигает, НЕ переименовывает и НЕ удаляет
существующие файлы. Публичные /images/... обязаны работать byte-for-byte.
"""
import hashlib
import os
import re
import unicodedata

IMAGES_DIR = "/root/BORIS/backend/images"
PUBLIC_PREFIX = "/images"

SERVICE_DIRS = {"qa", "_sitebuild", "_thumbs", "_prev"}
THUMB_ROOT = "_thumbs"
PREV_ROOT = "_prev"

_SAFE = re.compile(r"[^0-9A-Za-zА-Яа-яЁё._ -]+")


def abs_path(key: str) -> str:
    """Абсолютный путь по ключу. Защита от выхода за пределы IMAGES_DIR."""
    key = (key or "").lstrip("/")
    full = os.path.normpath(os.path.join(IMAGES_DIR, key))
    if not full.startswith(IMAGES_DIR + os.sep) and full != IMAGES_DIR:
        raise ValueError("storage_key вне хранилища: %s" % key)
    return full


def exists(key: str) -> bool:
    try:
        return os.path.isfile(abs_path(key))
    except ValueError:
        return False


def size_of(key: str) -> int:
    try:
        return os.path.getsize(abs_path(key))
    except OSError:
        return 0


def public_url(key) -> str:
    """URL для браузера. None -> None, чтобы API не отдавал битую ссылку."""
    if not key:
        return None
    return PUBLIC_PREFIX + "/" + str(key).lstrip("/")


def sha256_of(key: str) -> str:
    h = hashlib.sha256()
    with open(abs_path(key), "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 512), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_name(name: str) -> str:
    """Имя файла без сюрпризов, но с сохранением читаемости (кириллица ок)."""
    name = unicodedata.normalize("NFC", (name or "file").strip())
    name = name.replace("/", "_").replace("\\", "_")
    name = _SAFE.sub("", name)
    name = name.strip(". ") or "file"
    return name[:120]


def derived_key(key: str, root: str) -> str:
    """Ключ производного файла (миниатюра/превью) для исходного ключа."""
    base = (key or "").lstrip("/")
    stem = os.path.splitext(base)[0]
    return "%s/%s.jpg" % (root, stem)


def ensure_parent(key: str) -> str:
    full = abs_path(key)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    return full


def write_bytes(key: str, data: bytes) -> str:
    """Запись НОВОГО файла. Существующий не перезаписывается."""
    full = ensure_parent(key)
    if os.path.exists(full):
        raise FileExistsError(key)
    with open(full, "wb") as f:
        f.write(data)
    return key


def free_key(account_id: str, folder_rel: str, filename: str) -> str:
    """Свободный ключ вида <account>/<folder>/up_<имя>. Коллизий не создаёт."""
    acc = safe_name(account_id or "common")
    folder = "/".join(safe_name(p) for p in (folder_rel or "общая").split("/") if p)
    base = safe_name(filename)
    stem, ext = os.path.splitext(base)
    ext = (ext or ".jpg").lower()
    i = 0
    while True:
        suffix = "" if i == 0 else "_%d" % i
        key = "%s/%s/up_%s%s%s" % (acc, folder, stem, suffix, ext)
        if not exists(key):
            return key
        i += 1
        if i > 9999:
            raise RuntimeError("не удалось подобрать имя файла")


def is_service_path(rel: str) -> bool:
    parts = (rel or "").split("/")
    return bool(parts) and parts[0] in SERVICE_DIRS
