# BORIS SaaS

Платформа BORIS — SaaS-решение с FastAPI backend, Next.js frontend и PostgreSQL.

## Структура проекта

```
BORIS/
├── backend/        # FastAPI REST API
├── frontend/       # Next.js + TypeScript UI
├── database/       # PostgreSQL конфигурация и миграции
├── docker/         # Docker Compose и Dockerfile'ы
├── services/       # Бизнес-логика
├── ai/             # Модули работы с LLM
├── integrations/   # Внешние интеграции (Avito и др.)
└── docs/           # Документация
```

## Быстрый старт

### Требования

- Docker & Docker Compose
- Python 3.12+
- Node.js 20+

### Запуск через Docker

```bash
cd docker
docker compose up -d
```

- Frontend: http://localhost:3000
- Backend API: http://localhost:8000
- API Docs: http://localhost:8000/docs

### Локальная разработка

**Backend:**

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

**Frontend:**

```bash
cd frontend
npm install
npm run dev
```

## Переменные окружения

Скопируйте `.env.example` в `.env` и заполните значения.

## Лицензия

Proprietary — все права защищены.
