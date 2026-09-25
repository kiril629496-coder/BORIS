"""Link between a media asset and any entity that uses it.

entity_id is a string on purpose: campaign_item ids are numeric, banners are
numeric, future catalog products may use slugs.
"""

from sqlalchemy import BigInteger, Column, DateTime, Index, Integer, String, func

from app.db.base import Base


class MediaLink(Base):
    __tablename__ = "media_links"

    id = Column(BigInteger, primary_key=True, index=True)
    media_id = Column(BigInteger, nullable=False, index=True)
    entity_type = Column(String(32), nullable=False)
    entity_id = Column(String(128), nullable=False)
    role = Column(String(16), nullable=False, server_default="gallery")
    position = Column(Integer, nullable=False, server_default="0")
    created_by = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


Index("uq_media_links_unique", MediaLink.media_id, MediaLink.entity_type,
      MediaLink.entity_id, MediaLink.role, unique=True)
Index("ix_media_links_entity", MediaLink.entity_type, MediaLink.entity_id)
