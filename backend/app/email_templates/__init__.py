"""
Реестр шаблонов писем BORIS AI.

Категория шаблона определяет отправителя, набор заголовков и оформление.
Вызывающий код категорий не знает — он передаёт только имя шаблона.

В HTML-версию значения контекста подставляются экранированными:
шаблон не может получить произвольную разметку из данных.
"""

import html as _html
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CATEGORY_SYSTEM = "system"
CATEGORY_SERVICE = "service"
CATEGORY_MARKETING = "marketing"
CATEGORY_HEALTH = "health"

# имя -> (категория, тема письма)
TEMPLATES = {
    "verify_email": (CATEGORY_SYSTEM, "БОРИС — код подтверждения почты"),
}


def render(name: str, ctx: dict) -> tuple:
    """
    Возвращает (subject, text, html, category).
    Файлы шаблона лежат в <категория>/<имя>.txt и .html.
    html может быть None, если файла нет. Текстовая версия обязательна.
    """
    if name not in TEMPLATES:
        raise KeyError("неизвестный шаблон письма: %s" % name)
    category, subject = TEMPLATES[name]
    folder = os.path.join(BASE_DIR, category)

    with open(os.path.join(folder, name + ".txt"), encoding="utf-8") as fh:
        text = fh.read().format(**ctx)

    html_path = os.path.join(folder, name + ".html")
    html_body = None
    if os.path.exists(html_path):
        safe = {k: _html.escape(str(v), quote=True) for k, v in ctx.items()}
        with open(html_path, encoding="utf-8") as fh:
            html_body = fh.read().format(**safe)

    return subject, text, html_body, category
