# Docker

Контейнеризация BORIS SaaS через **Docker Compose**.

## Сервисы

| Сервис | Порт | Описание |
|--------|------|----------|
| `postgres` | 5432 | PostgreSQL 16 |
| `backend` | 8000 | FastAPI API |
| `frontend` | 3000 | Next.js UI |

## Запуск

```bash
cd docker
docker compose up -d
```

## Остановка

```bash
docker compose down
```

С удалением volumes (данные БД):

```bash
docker compose down -v
```

## Логи

```bash
docker compose logs -f backend
docker compose logs -f frontend
```

## Переменные окружения

Скопируйте `.env.example` из корня проекта в `docker/.env` или используйте корневой `.env`.

## Сборка образов

```bash
docker compose build --no-cache
```
