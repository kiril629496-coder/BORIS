# BORIS — SaaS автоматизации Avito

## Стек
- Frontend: Next.js 16 (Turbopack), TypeScript, порт 3002, сервис `boris-frontend`
- Backend: FastAPI, порт 8000, сервис `boris-backend` (под xvfb-run)
- БД: PostgreSQL
- Домен: boris-ai.pro (nginx + Let's Encrypt)

## Пути
- Фронт: /root/BORIS/frontend — главный файл `app/dashboard/page.tsx` (~6000 строк, весь UI дашборда)
- Бэк: /root/BORIS/backend/app — api/avito.py, api/banners.py, api/messenger.py, api/plan_items.py, api/tasks.py, api/parser.py, api/billing.py
- Секреты: /root/BORIS/backend/.env — НИКОГДА не коммитить, не выводить в консоль, не показывать

## Команды
- Сборка фронта: `cd /root/BORIS/frontend && npm run build`
- Рестарт фронта: `sudo systemctl restart boris-frontend`
- Рестарт бэка: `sudo systemctl restart boris-backend`
- Логи: `journalctl -u boris-backend -n 50 --no-pager`

## КРИТИЧЕСКИЕ ПРАВИЛА
- ВСЕГДА `npm run build` после правки фронта. `next start` — продакшн-режим, рестарт без сборки НЕ подхватит изменения и замаскирует ошибки старым билдом.
- НИКОГДА не рефакторить по своей инициативе. Только то, что попросили.
- НИКОГДА не трогать бэкенд, если задача про фронт (и наоборот).
- НИКОГДА не удалять файлы и не откатывать чужие правки без явного запроса.
- Одна задача за раз. Сделал → собрал → показал результат → ждёшь следующую.
- Не уверен — СПРОСИ, не угадывай.
- Не перезапускать сервисы без спроса.
- Не коммитить в git без явной просьбы.

## Грабли (уже наступали, не повторять)
- При переносе блока JSX переносить ВМЕСТЕ с окружающим Fragment `<>...</>`. Однажды вырезали `<style jsx global>` из ветки тернарника, оставили осиротевший `<>` — сборка падала, разбирались час.
- Turbopack при ошибке JSX часто указывает на конец файла, а не на реальное место. Для точной локализации: `npx tsc --noEmit -p .`
- Нельзя оставлять два атрибута `className` в одном теге (частая ошибка при замене инлайн-стилей на классы).
- `activeTab` типизирован как `useState<string>`, иначе TS сужает тип до литерала.

## Дизайн-система (единый стиль, эталон — вкладка «Тарифы и Финансы»)
CSS-классы в `<style jsx global>` внутри dashboard/page.tsx:
- `.b-card` — белая карточка, обводка #E3E7F0, радиус 16, hover-подъём
- `.b-panel` — то же без hover
- `.b-title` / `.b-sub` — заголовок и подзаголовок
- `.b-label` / `.b-input` — подпись и поле ввода (фокус — синяя обводка)
- `.b-btn` + `.b-btn-primary` / `.b-btn-ghost` / `.b-btn-soft` — кнопки
- `.b-icon` (64px) / `.b-icon-sm` (36px) — круглые градиентные иконки-аватары
- `.b-chip` — бейдж, `.b-blob` — декоративный круг на фоне, `.b-grid` — сетка карточек

ЗАДАЧА ПО ДИЗАЙНУ: привести все вкладки к этому стилю. Правила безопасности:
менять ТОЛЬКО атрибуты (`style={{...}}` → `className="..."`), НЕ трогать структуру тегов,
не добавлять новых `<>` и скобок. После каждой вкладки — сборка и проверка.

## Вкладки дашборда (activeTab)
listings (Объявления, внутри под-фильтры: all/active/inactive/drafts/search/feeds/templates_lib/parsed),
plan (Задачи и план), settings (Автопилот), analysis (Аналитика по городам), marketing,
sales (Продажи), webdesign, sitebuild, stats, billing (Тариф и лимиты), schedule, company, templates.
Плюс режимы-переключатели в сайдбаре: Директор, Советник, Тарифы и Финансы.

## Клиенты (account_id)
otdushi, andrey_launzh_mebel_akkaunt_penza_82435, andrey_mebel_launzh_moskva_69737,
garik_plitka_mo_58647, dinara_spb_vodnaya_ekipirovka_18294, pbi_artashes_plitka_mo_83993,
grant_kaluzhskaya_oblast_beton_plity_77392, evz_denis_aleksandra_evakuatory_i_pricepy_22264,
georgiy_mo_elektrosamokaty_88067
