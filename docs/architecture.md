# Архитектура BORIS SaaS

## Обзор

```mermaid
graph TB
    subgraph Client
        FE[Next.js Frontend]
    end

    subgraph Server
        API[FastAPI Backend]
        SVC[Services Layer]
        AI[AI / LLM]
        INT[Integrations]
    end

    subgraph Data
        PG[(PostgreSQL)]
    end

    subgraph External
        AVITO[Avito API]
        LLM[OpenAI / LLM]
    end

    FE -->|REST| API
    API --> SVC
    SVC --> AI
    SVC --> INT
    SVC --> PG
    INT -.-> AVITO
    AI -.-> LLM
```

## Слои

| Слой | Ответственность |
|------|-----------------|
| **Frontend** | UI, UX, клиентская логика |
| **Backend** | HTTP API, auth, routing |
| **Services** | Бизнес-логика, доменные правила |
| **AI** | LLM-запросы, промпты, AI-задачи |
| **Integrations** | Внешние API (Avito и др.) |
| **Database** | Хранение данных (PostgreSQL) |

## Поток запроса

1. Пользователь → Frontend (Next.js)
2. Frontend → Backend API (FastAPI)
3. Backend → Service layer (бизнес-логика)
4. Service → Database / AI / Integrations
5. Ответ возвращается по цепочке обратно

## Деплой

Все сервисы оркестрируются через `docker/docker-compose.yml`.
