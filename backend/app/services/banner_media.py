"""
Bridge: legacy banner filesystem -> Unified Media Library.

Legacy banner remains in /images/<account>/banners/... unchanged.
A byte-for-byte COPY is written into Media Storage.
No rename, remove or move is performed.
Registration is idempotent through the generated storage key / sha.
"""

import hashlib
from pathlib import Path

from app.db.session import SessionLocal
from app.services import media_service as media
from app.services import media_storage as storage
from app.services.command_limits import resolve_billing_owner


IMAGES_ROOT = Path("/root/BORIS/backend/images").resolve()


def register_banner(account_id: str, absolute_path: str, source_provider: str = "openai", source_ref: str | None = None):
    if not account_id:
        raise ValueError("account_id обязателен")

    src = Path(absolute_path).resolve()

    if not src.exists() or not src.is_file():
        raise ValueError("Файл баннера не найден")

    expected = (IMAGES_ROOT / account_id / "banners").resolve()

    try:
        src.relative_to(expected)
    except ValueError:
        raise ValueError(
            "Баннер находится вне каталога выбранного аккаунта"
        )

    data = src.read_bytes()

    if not data:
        raise ValueError("Файл баннера пуст")

    sha = hashlib.sha256(data).hexdigest()

    db = SessionLocal()

    try:
        owner = resolve_billing_owner(db, account_id)

        if not owner:
            raise ValueError(
                f"У аккаунта {account_id} не найден владелец"
            )

        # resolve_billing_owner может возвращать:
        #   owner_user_id
        # либо:
        #   (owner_user_id, ...)
        # Медиамосту нужен только числовой ID владельца.
        if isinstance(owner, tuple):
            # Контракт resolve_billing_owner:
            # (billing_owner_kind, owner_user_id)
            if len(owner) != 2:
                raise ValueError(
                    f"Неожиданный формат владельца: {owner!r}"
                )
            owner_kind, owner_user_id = owner
        elif isinstance(owner, dict):
            owner_user_id = (
                owner.get("owner_user_id")
                or owner.get("user_id")
                or owner.get("id")
            )
        else:
            owner_user_id = owner

        if owner_user_id is None:
            raise ValueError(
                f"Не удалось определить owner_user_id для {account_id}"
            )

        # Сначала ищем точный контент у этого владельца.
        duplicate = media.find_duplicate(
            db,
            int(owner_user_id),
            sha,
        )

        if (
            duplicate is not None
            and duplicate.account_id == account_id
            and duplicate.media_type == "banner"
        ):
            return {
                "id": duplicate.id,
                "created": False,
                "storage_key": duplicate.storage_key,
            }

        key = storage.free_key(
            account_id,
            "banners",
            src.name,
        )

        # Только создание нового файла.
        # Никакого изменения legacy /images.
        storage.write_bytes(key, data)

        try:
            row, created = media.register_asset(
                db=db,
                owner_user_id=int(owner_user_id),
                account_id=account_id,
                storage_key=key,
                filename=src.name,
                media_type="banner",
                source_type="banner",
                source_provider=str(source_provider or "openai"),
                source_ref=str(source_ref or src),
                sha256=sha,
                file_size=len(data),
                status="approved",
            )

            db.commit()

        except Exception:
            db.rollback()

            # Если БД не зарегистрировалась, не оставляем сироту
            # намеренно здесь НЕ удаляем файл автоматически:
            # fail-safe правило BORIS — данные не уничтожаем.
            raise

        return {
            "id": row.id,
            "created": bool(created),
            "storage_key": row.storage_key,
        }

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()
