# Database

Конфигурация **PostgreSQL** для BORIS SaaS.

## Структура

```
database/
├── init.sql             # Начальная схема БД
└── migrations/          # SQL-миграции
```

## Подключение

Параметры по умолчанию (см. `.env.example`):

| Параметр | Значение |
|----------|----------|
| Host | `localhost` |
| Port | `5432` |
| User | `boris` |
| Password | `boris_secret` |
| Database | `boris` |

Connection string:

```
postgresql://boris:boris_secret@localhost:5432/boris
```

## Запуск через Docker

PostgreSQL поднимается автоматически через `docker/docker-compose.yml`.

## Миграции

SQL-файлы в `migrations/` применяются в алфавитном порядке при первом запуске контейнера (через `init.sql` или volume mount).

Рекомендуется именовать файлы: `001_initial.sql`, `002_add_users.sql`, и т.д.
