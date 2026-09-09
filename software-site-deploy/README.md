# SYSTEMS.AI — production-сайт разработки

Этот каталог содержит выпуск сайта услуг разработки отдельно от основного BORIS.

## Что уже является частью production-контура

- публичный сайт разработки;
- закрытая админка заявок;
- формы с серверным сохранением;
- отправка заявок на две рабочие почты;
- серверный архив доставки по каждому получателю;
- редактирование услуг, прайса и контактов;
- поиск, фильтр, комментарии и CSV по заявкам;
- PWA для установки на экран телефона;
- 10 реальных ПК-экранов BORIS и 5 экранов BORIS Phone;
- clean URLs;
- route-specific title / description / canonical / OpenGraph;
- JSON-LD Organization / WebSite / Service / SoftwareApplication;
- robots.txt и sitemap.xml;
- production JS/CSS без Babel и Tailwind CDN;
- CSP и базовые security headers;
- автоматический health-check сайта.

## Сборка

Исходник интерфейса:

```
software-site-build/index.source.html
```

Сборка:

```bash
./software-site-build/build.sh ./software-site
```

При первом запуске `bootstrap.sh` скачивает только build-time зависимости React/Babel.
Babel не попадает в браузерную production-сборку.

## Проверочная версия

Сейчас она размещается на:

```
https://boris-ai.pro/software-dev/
```

Она закрыта от индексации заголовком `X-Robots-Tag: noindex, nofollow`.

Закрытая админка:

```
https://boris-ai.pro/software-dev-admin/
```

Админка и private API защищены Basic Auth на Nginx.
Сам backend private API дополнительно принимает запрос только с loopback/Nginx.

## Выпуск на отдельный домен

После настройки A-записи:

```bash
sudo ./software-site-deploy/finish-domain.sh example.ru
```

Скрипт:

1. проверяет DNS;
2. запускает безопасную сборку в staging;
3. создаёт production Nginx;
4. создаёт 33 SEO HTML-маршрута;
5. генерирует robots.txt и sitemap.xml;
6. выпускает HTTPS через Certbot;
7. запускает полный HTTP/SEO/browser QA.

Если `www.example.ru` присутствует в DNS, он автоматически добавляется в сертификат и редиректится на основной домен.

## Только подготовка без изменения production

```bash
DRY_RUN=1 ./software-site-deploy/deploy-domain.sh example.test
```

Результат создаётся в `/tmp/software-site-production-example.test`.

## Финальный QA существующего домена

```bash
./software-site-deploy/qa-domain.sh example.ru
```

Проверяются:

- ключевые страницы;
- clean URLs;
- title/canonical/schema;
- robots/sitemap;
- PWA;
- форма/SMTP health;
- отсутствие публичного доступа к admin/private API;
- мобильная и desktop-вёрстка через Playwright;
- битые изображения, JS ошибки и горизонтальный скролл.

## Автоконтроль

Установить watchdog:

```bash
sudo ./software-site-deploy/install-watchdog.sh example.ru
```

Он проверяет сайт каждые 5 минут. При первом сбое выполняется безопасная проверка Nginx и одна попытка reload, затем повторная проверка.

До отдельного домена watchdog может следить за preview:

```bash
sudo SOFTWARE_SITE_BASE_URL=https://boris-ai.pro/software-dev \
  ./software-site-deploy/install-watchdog.sh boris-ai.pro
```

## После запуска

Добавить новый домен:

- Яндекс Вебмастер;
- Google Search Console;
- при необходимости Яндекс Метрику / Google Analytics;
- проверить индексацию sitemap;
- заменить preview URL во внешних материалах.

Cloudflare сейчас не подключён к SentinelX, поэтому DNS-записи нового домена меняются только после появления доступа к DNS-провайдеру.
