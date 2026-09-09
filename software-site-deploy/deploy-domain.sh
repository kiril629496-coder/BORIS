#!/usr/bin/env bash
set -euo pipefail

DOMAIN="${1:-}"
if [[ -z "$DOMAIN" || ! "$DOMAIN" =~ ^([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}$ ]]; then
  echo "Использование: sudo $0 example.ru"
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
CANONICAL_SRC="$REPO/software-site"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

# Build into an isolated staging directory first. A failed build never mutates
# the live domain tree or the committed canonical package.
rsync -a "$CANONICAL_SRC/" "$STAGE/"
"$REPO/software-site-build/build.sh" "$STAGE"
SRC="$STAGE"

if [[ "${DRY_RUN:-0}" == "1" ]]; then
  DST="/tmp/software-site-production-${DOMAIN}"
  NGINX="/tmp/software-site-${DOMAIN}.conf"
  ENABLED=""
else
  DST="/var/www/software-site-production"
  NGINX="/etc/nginx/sites-available/software-site-${DOMAIN}"
  ENABLED="/etc/nginx/sites-enabled/software-site-${DOMAIN}"
fi

mkdir -p "$DST"
rsync -a --delete \
  --exclude='*.bak.*' \
  --exclude='probe.txt*' \
  --exclude='boris-live/' \
  "$SRC/" "$DST/"

# Convert preview-only /software-dev/ paths to root-domain paths and create
# route-specific HTML heads so search engines get title/description/canonical
# without waiting for JavaScript rendering.
python3 - "$DST" "$DOMAIN" <<'PY'
import html
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
domain = sys.argv[2]
index = root / "index.html"
base = index.read_text(encoding="utf-8")

base = base.replace('href="/software-dev/manifest.webmanifest"', 'href="/manifest.webmanifest"')
base = base.replace('href="/software-dev/icons/apple-touch-icon.png"', 'href="/icons/apple-touch-icon.png"')
base = base.replace('href="/software-dev/app-icon.svg"', 'href="/app-icon.svg"')
base = base.replace('/software-dev/assets/', '/assets/')
base = base.replace('href="/software-dev/" className="inline-flex justify-center', 'href="/" className="inline-flex justify-center')
base = base.replace(
    "navigator.serviceWorker.register('/software-dev/sw.js', { scope: '/software-dev/' })",
    "navigator.serviceWorker.register('/sw.js', { scope: '/' })",
)

default_title = "Разработка ПО, SaaS и мобильных приложений для бизнеса | SYSTEMS.AI"
default_desc = "Разработка мобильных приложений, SaaS, CRM, ИИ-систем и автоматизации бизнеса под ключ. От первой рабочей версии до запуска и развития."

pages = {
    "/": (default_title, default_desc),
    "/services": ("Разработка программного обеспечения на заказ | SYSTEMS.AI", "Мобильные приложения, SaaS, CRM, ИИ, интеграции, телефония и автоматизация бизнес-процессов."),
    "/industries": ("Разработка программ и автоматизация для отраслей бизнеса | SYSTEMS.AI", "Программные решения для недвижимости, строительства, торговли, производства, логистики, услуг и других отраслей."),
    "/cases": ("Проекты и примеры разработки программ | SYSTEMS.AI", "Реальные примеры разработки программных продуктов, мобильных приложений, SaaS, CRM, ИИ и автоматизации."),
    "/cases/boris-ai": ("BORIS AI — кейс разработки SaaS, CRM, ИИ и телефонии | SYSTEMS.AI", "Кейс большой SaaS-системы: CRM, сообщения, реклама, аналитика, ИИ, телефония и автоматизация. Реальные интерфейсы."),
    "/prices": ("Цены на разработку программ, SaaS и приложений | SYSTEMS.AI", "Ориентировочная стоимость мобильных приложений, SaaS, CRM, ИИ, интеграций, сайтов и автоматизации бизнеса."),
    "/estimate": ("Рассчитать стоимость разработки проекта | SYSTEMS.AI", "Предварительная оценка первой рабочей версии приложения, SaaS, CRM, сайта или автоматизации."),
    "/about": ("О компании SYSTEMS.AI — разработка программного обеспечения", "Подход к заказной разработке программ, приложений, SaaS и автоматизации для бизнеса."),
    "/blog": ("Блог о разработке ПО и автоматизации бизнеса | SYSTEMS.AI", "Практические материалы о мобильных приложениях, SaaS, CRM, ИИ, интеграциях и автоматизации."),
    "/contacts": ("Контакты SYSTEMS.AI — обсудить разработку проекта", "Свяжитесь с нами, чтобы обсудить приложение, SaaS, CRM, ИИ или автоматизацию бизнеса."),
}

services = {
    "mobile-app-development": ("Разработка мобильных приложений для iPhone и Android | SYSTEMS.AI", "Разработка мобильных приложений под ключ: iPhone, Android и устанавливаемые веб-приложения."),
    "ios-development": ("Разработка приложений для iPhone и iPad | SYSTEMS.AI", "Нативная разработка приложений для iPhone и iPad на Swift и SwiftUI."),
    "android-development": ("Разработка Android-приложений на заказ | SYSTEMS.AI", "Нативная разработка приложений Android на Kotlin и Java для бизнеса."),
    "cross-platform-app-development": ("Кроссплатформенная разработка Flutter и React Native | SYSTEMS.AI", "Одна кодовая база для iPhone и Android: Flutter или React Native для быстрого запуска продукта."),
    "saas-development": ("Разработка SaaS-платформ под ключ | SYSTEMS.AI", "Облачные SaaS-сервисы: личные кабинеты, роли, тарифы, подписки, платежи, аналитика, CRM и интеграции."),
    "web-app-development": ("Разработка веб-приложений и личных кабинетов | SYSTEMS.AI", "Сложные веб-сервисы, клиентские порталы, личные кабинеты и внутренние инструменты бизнеса."),
    "crm-development": ("Разработка собственной CRM под бизнес | SYSTEMS.AI", "Индивидуальная CRM под процессы продаж, клиентов, сделки, задачи, аналитику и интеграции."),
    "ai-automation": ("Автоматизация бизнеса с искусственным интеллектом | SYSTEMS.AI", "ИИ и LLM для продаж, поддержки, документов, обработки обращений и автоматизации повторяющихся операций."),
    "ai-agents": ("Разработка ИИ-агентов и виртуальных менеджеров | SYSTEMS.AI", "ИИ-менеджеры для обработки обращений, квалификации клиентов, продаж и контроля рабочих процессов."),
    "telegram-bots": ("Разработка Telegram-ботов и мини-приложений | SYSTEMS.AI", "Telegram-боты, магазины, запись, кабинеты, платежи и бизнес-логика внутри Telegram."),
    "api-integration": ("Интеграция программ через API и вебхуки | SYSTEMS.AI", "Связываем CRM, сайты, учётные системы, платежи, телефонию и другие сервисы без ручного переноса данных."),
    "1c-integration": ("Интеграция сайта, CRM и сервисов с 1С | SYSTEMS.AI", "Двусторонний обмен товарами, ценами, остатками, заказами и документами с 1С."),
    "ip-telephony": ("Разработка IP-телефонии и коллтрекинга | SYSTEMS.AI", "Виртуальная АТС, программная звонилка, запись разговоров, CRM, коллтрекинг и аналитика звонков."),
    "parsers": ("Разработка парсеров и систем мониторинга данных | SYSTEMS.AI", "Автоматический сбор данных, каталогов, цен и информации с сайтов и маркетплейсов."),
    "marketplace-integration": ("Интеграция с Wildberries, Ozon и Яндекс Маркет | SYSTEMS.AI", "Автоматизация остатков, цен, заказов и данных маркетплейсов в единой системе."),
    "admin-panel-development": ("Разработка админ-панелей и учётных систем | SYSTEMS.AI", "Панели управления, роли, права доступа, склад, закупки, процессы и бизнес-аналитика."),
    "devops": ("Настройка серверов, мониторинга и автоматического восстановления | SYSTEMS.AI", "Production-серверы, выкладка обновлений, мониторинг, резервирование и автоматическое восстановление сервисов."),
}
for slug, meta in services.items():
    pages[f"/services/{slug}"] = meta

blog = {
    "skolko-stoit-mobilnoe-prilozhenie": ("Сколько стоит разработать мобильное приложение в 2026 году | SYSTEMS.AI", "Из чего складывается стоимость приложения для iPhone и Android и что входит в первую рабочую версию."),
    "chto-vhodit-v-saas-mvp": ("Что входит в первую версию SaaS-сервиса (MVP) | SYSTEMS.AI", "Авторизация, роли, кабинет, тарифы, платежи, аналитика и панель управления в первой версии SaaS."),
    "kogda-biznesu-nuzhna-crm": ("Когда бизнесу нужна собственная CRM | SYSTEMS.AI", "Когда коробочной CRM недостаточно и имеет смысл разработать систему под собственные процессы."),
    "flutter-ili-native": ("Flutter или нативная разработка iOS и Android | SYSTEMS.AI", "Сравнение Flutter и отдельных приложений Swift/Kotlin по срокам, бюджету и возможностям."),
    "kak-avtomatizirovat-otdel-prodazh": ("Как автоматизировать отдел продаж с помощью ИИ | SYSTEMS.AI", "ИИ-менеджеры, обработка обращений, контроль диалогов и автоматизация рутинных действий отдела продаж."),
    "kak-svyazat-site-i-1c": ("Как связать сайт и 1С без ручного переноса данных | SYSTEMS.AI", "Обмен товарами, ценами, остатками, заказами и документами между сайтом и 1С."),
}
for slug, meta in blog.items():
    pages[f"/blog/{slug}"] = meta

def rendered(route: str, title: str, desc: str) -> str:
    out = base
    out = out.replace(
        "<title>Команда разработки ПО — Мобильные приложения, SaaS, AI и Автоматизация</title>",
        f"<title>{html.escape(title)}</title>",
        1,
    )
    out = out.replace(
        '<meta name="description" content="Разработка мобильных приложений, SaaS-платформ, CRM, AI-систем и автоматизации бизнеса под ключ. От первой рабочей версии до запуска и развития.">',
        f'<meta name="description" content="{html.escape(desc, quote=True)}">',
        1,
    )
    canonical = f"https://{domain}{route}"
    og_type = "article" if route.startswith("/blog/") else "website"
    graph = [
        {
            "@type": "Organization",
            "@id": f"https://{domain}/#organization",
            "name": "SYSTEMS.AI",
            "url": f"https://{domain}/",
            "email": ["eliseev-ko@mail.ru", "ostapenko-kirill-86@yandex.ru"],
        },
        {
            "@type": "WebSite",
            "@id": f"https://{domain}/#website",
            "url": f"https://{domain}/",
            "name": "SYSTEMS.AI",
            "publisher": {"@id": f"https://{domain}/#organization"},
            "inLanguage": "ru-RU",
        },
    ]
    if route.startswith("/services/"):
        graph.append({
            "@type": "Service",
            "name": title.replace(" | SYSTEMS.AI", ""),
            "description": desc,
            "url": canonical,
            "provider": {"@id": f"https://{domain}/#organization"},
        })
    if route == "/cases/boris-ai":
        graph.append({
            "@type": "SoftwareApplication",
            "name": "BORIS AI",
            "applicationCategory": "BusinessApplication",
            "operatingSystem": "Web, PWA",
            "description": desc,
            "url": canonical,
        })
    schema = json.dumps({"@context": "https://schema.org", "@graph": graph}, ensure_ascii=False)
    static_head = (
        f'    <link rel="canonical" href="{html.escape(canonical, quote=True)}">\n'
        f'    <meta property="og:type" content="{og_type}">\n'
        f'    <meta property="og:title" content="{html.escape(title, quote=True)}">\n'
        f'    <meta property="og:description" content="{html.escape(desc, quote=True)}">\n'
        f'    <meta property="og:url" content="{html.escape(canonical, quote=True)}">\n'
        f'    <script id="systems-ai-schema" type="application/ld+json">{schema}</script>\n'
    )
    out = out.replace("</head>", static_head + "</head>", 1)
    fallback = (
        '<noscript><main style="max-width:980px;margin:40px auto;padding:24px;font-family:Arial,sans-serif">'
        f'<h1>{html.escape(title.replace(" | SYSTEMS.AI", ""))}</h1>'
        f'<p>{html.escape(desc)}</p>'
        '<p><a href="/services">Услуги разработки</a> · <a href="/prices">Цены</a> · <a href="/cases">Проекты</a> · <a href="/contacts">Контакты</a></p>'
        '</main></noscript>'
    )
    out = out.replace('<div id="root"></div>', '<div id="root"></div>\n    ' + fallback, 1)
    return out

for route, (title, desc) in pages.items():
    if route == "/":
        target = root / "index.html"
    else:
        target = root / route.strip("/") / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered(route, title, desc), encoding="utf-8")

print(f"SEO HTML routes generated: {len(pages)}")
PY

cat > "$DST/robots.txt" <<EOF
User-agent: *
Allow: /
Disallow: /admin/
Disallow: /api/private/
Sitemap: https://$DOMAIN/sitemap.xml
EOF

python3 - "$DOMAIN" "$DST/sitemap.xml" <<'PY'
import sys
from datetime import date
from xml.sax.saxutils import escape

domain, target = sys.argv[1], sys.argv[2]
routes = [
    "/",
    "/services",
    "/services/mobile-app-development",
    "/services/ios-development",
    "/services/android-development",
    "/services/cross-platform-app-development",
    "/services/saas-development",
    "/services/web-app-development",
    "/services/crm-development",
    "/services/ai-automation",
    "/services/ai-agents",
    "/services/telegram-bots",
    "/services/api-integration",
    "/services/1c-integration",
    "/services/ip-telephony",
    "/services/parsers",
    "/services/marketplace-integration",
    "/services/admin-panel-development",
    "/services/devops",
    "/industries",
    "/cases",
    "/cases/boris-ai",
    "/prices",
    "/estimate",
    "/about",
    "/blog",
    "/blog/skolko-stoit-mobilnoe-prilozhenie",
    "/blog/chto-vhodit-v-saas-mvp",
    "/blog/kogda-biznesu-nuzhna-crm",
    "/blog/flutter-ili-native",
    "/blog/kak-avtomatizirovat-otdel-prodazh",
    "/blog/kak-svyazat-site-i-1c",
    "/contacts",
]
today = date.today().isoformat()
xml = ['<?xml version="1.0" encoding="UTF-8"?>',
       '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
for route in routes:
    loc = f"https://{domain}{route}"
    priority = "1.0" if route == "/" else ("0.9" if route in {"/services","/cases","/prices"} else "0.7")
    xml.append(f"  <url><loc>{escape(loc)}</loc><lastmod>{today}</lastmod><priority>{priority}</priority></url>")
xml.append("</urlset>")
open(target, "w", encoding="utf-8").write("\n".join(xml) + "\n")
PY

cat > "$NGINX" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN www.$DOMAIN;

    root $DST;
    index index.html;
    server_tokens off;

    if (\$host = www.$DOMAIN) {
        return 301 \$scheme://$DOMAIN\$request_uri;
    }

    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Permissions-Policy "camera=(), geolocation=(), payment=(), usb=()" always;
    add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com data:; img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'self'; form-action 'self'; worker-src 'self' blob:;" always;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    location ^~ /api/private/software-site/ {
        auth_basic "Private";
        auth_basic_user_file /etc/nginx/software-dev-admin.htpasswd;
        proxy_pass http://boris_api_pool;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        add_header X-Robots-Tag "noindex, nofollow" always;
    }

    location ^~ /api/public/software-site/ {
        limit_req zone=api_zone burst=30 nodelay;
        proxy_pass http://boris_api_pool;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location = /admin { return 301 /admin/; }

    location = /admin/manifest.webmanifest {
        auth_basic "Private";
        auth_basic_user_file /etc/nginx/software-dev-admin.htpasswd;
        alias $DST/manifest.webmanifest;
        default_type application/manifest+json;
        add_header X-Robots-Tag "noindex, nofollow" always;
    }

    location ^~ /admin/icons/ {
        auth_basic "Private";
        auth_basic_user_file /etc/nginx/software-dev-admin.htpasswd;
        alias $DST/icons/;
        add_header X-Robots-Tag "noindex, nofollow" always;
    }

    location = /admin/app-icon.svg {
        auth_basic "Private";
        auth_basic_user_file /etc/nginx/software-dev-admin.htpasswd;
        alias $DST/app-icon.svg;
        default_type image/svg+xml;
        add_header X-Robots-Tag "noindex, nofollow" always;
    }

    location ^~ /admin/ {
        auth_basic "Private";
        auth_basic_user_file /etc/nginx/software-dev-admin.htpasswd;
        try_files /index.html =404;
        add_header Cache-Control "no-store, max-age=0" always;
        add_header X-Robots-Tag "noindex, nofollow" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header X-Frame-Options "SAMEORIGIN" always;
        add_header Referrer-Policy "strict-origin-when-cross-origin" always;
        add_header Permissions-Policy "camera=(), geolocation=(), payment=(), usb=()" always;
        add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com data:; img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'self'; form-action 'self'; worker-src 'self' blob:;" always;
        add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    }

    location ^~ /assets/ {
        try_files \$uri =404;
        expires 1h;
    }

    location ^~ /icons/ {
        try_files \$uri =404;
        expires 7d;
    }

    location ^~ /boris-desktop/ {
        try_files \$uri =404;
        expires 1d;
    }

    location ^~ /boris-phone/ {
        try_files \$uri =404;
        expires 1d;
    }

    location = /manifest.webmanifest {
        default_type application/manifest+json;
        try_files \$uri =404;
        expires -1;
    }

    location = /sw.js {
        default_type application/javascript;
        expires -1;
        try_files \$uri =404;
    }

    location = /robots.txt { try_files \$uri =404; }
    location = /sitemap.xml { default_type application/xml; try_files \$uri =404; }

    # One canonical URL without a trailing slash for public pages.
    location ~ ^(.+)/$ {
        return 301 \$scheme://\$host\$1\$is_args\$args;
    }

    location / {
        try_files \$uri/index.html \$uri \$uri/ /index.html;
        expires -1;
    }
}
EOF

if [[ "${DRY_RUN:-0}" == "1" ]]; then
  echo "DRY RUN: файлы собраны в $DST"
  echo "DRY RUN: nginx-конфигурация: $NGINX"
  exit 0
fi

ln -sfn "$NGINX" "$ENABLED"
nginx -t
systemctl reload nginx

echo "HTTP-конфигурация для $DOMAIN установлена."
echo "После того как DNS A/AAAA указывает на этот сервер, выполни:"
echo "  certbot --nginx -d $DOMAIN -d www.$DOMAIN --redirect"
echo "После HTTPS проверь:"
echo "  curl -I https://$DOMAIN/"
echo "  curl https://$DOMAIN/robots.txt"
echo "  curl https://$DOMAIN/sitemap.xml"
