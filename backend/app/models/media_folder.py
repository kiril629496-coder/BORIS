"""Media library folders (Ad Center / ECO). Tree is built via parent_id."""

from sqlalchemy import BigInteger, Column, DateTime, Index, Integer, String, func

from app.db.base import Base


class MediaFolder(Base):
    __tablename__ = "media_folders"

    id = Column(BigInteger, primary_key=True, index=True)
    owner_user_id = Column(Integer, nullable=False, index=True)
    parent_id = Column(BigInteger, nullable=True, index=True)
    account_id = Column(String(255), nullable=True, index=True)  # ЕЦО: папка привязана к аккаунту
    name = Column(String(255), nullable=False)
    path = Column(String(1024), nullable=False, server_default="/")
    kind = Column(String(16), nullable=False, server_default="user")
    sort = Column(Integer, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)


Index("ix_media_folders_owner_parent", MediaFolder.owner_user_id, MediaFolder.parent_id)
