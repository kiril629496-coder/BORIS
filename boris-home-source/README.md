# BORIS static home source

Authoritative source for the exact root landing served from /var/www/boris-home/index.html.

Rules:
- edit index.html here, not directly in /var/www/boris-home;
- run DRY_RUN=1 ./deploy.sh;
- then run sudo ./deploy.sh;
- deploy is atomic for index.html and validates canonical, SEO metadata, Yandex Metrika goals, the Avito/CRM/AI commercial section and IndexNow key;
- Next.js remains authoritative for application routes; this directory owns only the separate static root landing and its local static assets.
