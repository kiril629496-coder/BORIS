# Services

Слой **бизнес-логики** BORIS SaaS. Не зависит от HTTP-фреймворка — используется backend API и фоновыми задачами.

## Структура

```
services/
├── core/                # Базовые сервисы и утилиты
│   └── base.py
└── users/               # Пример доменного сервиса
    └── service.py
```

## Принципы

- Сервисы содержат бизнес-правила, валидацию и оркестрацию
- Не импортируют FastAPI или Next.js
- Получают зависимости (репозитории, клиенты) через конструктор

## Использование из backend

```python
from services.users.service import UserService

service = UserService()
user = await service.get_by_email("user@example.com")
```

## PYTHONPATH

При локальной разработке:

```bash
export PYTHONPATH="${PYTHONPATH}:$(pwd)/.."
```
