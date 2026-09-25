"""Media asset: one image file registered as an entity.

Files are never moved by the media library: storage_key holds the existing
relative path under images/, so published /images/... URLs keep working.
file_state is technical (present|missing) and must not be mixed with the
user-facing status (draft|review|approved|rejected|archived).
"""

from sqlalchemy import (BigInteger, Boolean, Column, DateTime, Index, Integer,
                        String, Text, func)
from sqlalchemy.dialects.postgresql import JSONB

from app.db.base import Base


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id = Column(BigInteger, primary_key=True, index=True)
    owner_user_id = Column(Integer, nullable=False, index=True)
    account_id = Column(String(255), nullable=True, index=True)
    folder_id = Column(BigInteger, nullable=True, index=True)

    filename = Column(String(512), nullable=False)
    original_filename = Column(String(512), nullable=True)
    storage_key = Column(String(1024), nullable=False)
    thumb_key = Column(String(1024), nullable=True)
    preview_key = Column(String(1024), nullable=True)

    mime = Column(String(64), nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    file_size = Column(BigInteger, nullable=True)
    sha256 = Column(String(64), nullable=True, index=True)
    phash = Column(String(32), nullable=True, index=True)

    media_type = Column(String(32), nullable=False, server_default="other")
    status = Column(String(16), nullable=False, server_default="approved")
    file_state = Column(String(16), nullable=False, server_default="present")

    source_type = Column(String(32), nullable=True)
    source_provider = Column(String(64), nullable=True)
    source_ref = Column(String(1024), nullable=True)
    license_json = Column(JSONB, nullable=True)

    derived_from_id = Column(BigInteger, nullable=True, index=True)
    generation_id = Column(String(128), nullable=True)

    is_favorite = Column(Boolean, nullable=False, server_default="false")
    tags = Column(JSONB, nullable=True)

    created_by = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    error_text = Column(Text, nullable=True)


Index("ix_media_assets_owner_sha", MediaAsset.owner_user_id, MediaAsset.sha256,
      unique=True, postgresql_where=MediaAsset.deleted_at.is_(None))
Index("ix_media_assets_owner_live", MediaAsset.owner_user_id, MediaAsset.deleted_at,
      MediaAsset.media_type)
Index("ix_media_assets_owner_folder", MediaAsset.owner_user_id, MediaAsset.folder_id)
Index("ix_media_assets_storage_key", MediaAsset.storage_key)
Index("ix_media_assets_state", MediaAsset.file_state, MediaAsset.status)
