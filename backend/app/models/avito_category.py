from sqlalchemy import Column, Integer, String, DateTime, Boolean, Text, func
from app.db.base import Base

class AvitoCategory(Base):
    __tablename__ = "avito_categories"

    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String, index=True)          # напр. dlya_doma_i_dachi
    name = Column(String)                       # напр. "Для дома и дачи"
    parent_slug = Column(String, nullable=True, index=True)  # для будущей вложенности
    template_id = Column(String, nullable=True, index=True)  # id конкретного шаблона (templates/{id})
    template_label = Column(String, nullable=True)  # текст ссылки на шаблон (название подкатегории)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AvitoCategoryParam(Base):
    __tablename__ = "avito_category_params"

    id = Column(Integer, primary_key=True, index=True)
    template_id = Column(String, index=True)     # ссылка на AvitoCategory.template_id
    name = Column(String)                         # напр. "ImageUrls"
    required = Column(Boolean, default=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
