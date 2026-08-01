from sqlalchemy import Column, Integer, String, Text, DateTime, func
from app.db.base import Base


class CategoryTemplate(Base):
    """База знаний обязательных полей категорий Avito - кэш документации шаблона
    (autoload/documentation/templates/{id}), чтобы не ходить туда на каждый черновик."""
    __tablename__ = "category_templates"

    id = Column(Integer, primary_key=True, index=True)
    category_id = Column(String, unique=True, index=True)  # нормализованный ключ (ниша/topic)
    category_name = Column(String, nullable=True)           # человекочитаемый путь категории (breadcrumbs)
    template_id = Column(String, index=True, nullable=True)
    # JSON: [{tag, name_ru, required, depends_on, example}] - несмотря на имя, здесь лежат ВСЕ
    # поля категории из документации шаблона, а не только обязательные (required=True/False - это
    # признак ВНУТРИ каждого элемента списка, а не фильтр самого списка)
    required_fields = Column(Text, nullable=True)
    field_rules = Column(Text, nullable=True)       # JSON: {tag: "правило формата в свободном тексте"}
    enum_values = Column(Text, nullable=True)       # JSON: {tag: ["допустимое значение", ...]}
    fetched_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class CategoryTreeLeaf(Base):
    """Полная карта дерева документации Автозагрузки Avito (.../autoload/documentation/templates) -
    строится один раз обходом всех веток (category_resolver.crawl_category_tree), дальше используется
    для резолвинга template_id по нише клиента без хрупкого поиска по объявлениям (breadcrumbs)."""
    __tablename__ = "category_tree_leaves"

    id = Column(Integer, primary_key=True, index=True)
    top_level = Column(String, index=True)   # верхнеуровневая ветка дерева (например "Хобби и отдых")
    path = Column(String)                     # полный путь через " > " до листа
    leaf_name = Column(String, index=True)    # название листа (последний сегмент path)
    template_id = Column(String, unique=True, index=True)
    fetched_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
