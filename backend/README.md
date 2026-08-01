# Backend

REST API на **FastAPI** (Python 3.12+).

## Структура

```
backend/
├── app/
│   ├── main.py          # Точка входа FastAPI
│   ├── config.py        # Настройки приложения
│   └── api/
│       └── routes/      # HTTP-маршруты
├── tests/               # Тесты
└── requirements.txt
```

## Запуск

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## API

| Endpoint | Описание |
|----------|----------|
| `GET /health` | Проверка состояния сервиса |
| `GET /docs` | Swagger UI |
| `GET /redoc` | ReDoc |

## Зависимости от других модулей

- `services/` — бизнес-логика
- `ai/` — работа с LLM
- `integrations/` — внешние API

При локальной разработке добавьте корень проекта в `PYTHONPATH`:

```bash
export PYTHONPATH="${PYTHONPATH}:$(pwd)/.."
```
