# AI

Модули работы с **LLM** (Large Language Models) для BORIS SaaS.

## Структура

```
ai/
├── llm/
│   ├── client.py        # Абстракция LLM-клиента
│   └── prompts.py       # Шаблоны промптов
└── tasks/               # AI-задачи (классификация, генерация и т.д.)
    └── __init__.py
```

## Конфигурация

| Переменная | Описание |
|------------|----------|
| `OPENAI_API_KEY` | API-ключ OpenAI |
| `LLM_MODEL` | Модель (по умолчанию `gpt-4o-mini`) |

## Использование

```python
from ai.llm.client import LLMClient

client = LLMClient()
response = await client.complete("Привет, BORIS!")
print(response.text)
```

## Расширение

Добавляйте новые провайдеры в `llm/client.py` (OpenAI, Anthropic, локальные модели).

Задачи предметной области — в `tasks/`.
