# БОРИС - контекст проекта

## Стек
- Бэкенд: FastAPI + Python (папка backend)
- Фронтенд: Next.js (папка frontend)
- БД: PostgreSQL
- ИИ: Groq (llama-3.3-70b)
- Авито API: подключён, работает

## Что сделано
- Бэкенд запускается
- Авито API подключён (ID аккаунта: 426007804)
- Получение списка объявлений работает
- Чат с Борисом работает

## Ключи хранятся в
backend/.env

## Запуск
cd ~/Desktop/BORIS/backend
source venv/bin/activate
export $(cat .env | xargs) && python -m uvicorn app.main:app --reload
