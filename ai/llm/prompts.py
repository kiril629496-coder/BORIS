"""Prompt templates for BORIS SaaS."""

SYSTEM_DEFAULT = "Ты — AI-ассистент платформы BORIS SaaS. Отвечай на русском языке."

CLASSIFY_INTENT = """Классифицируй намерение пользователя.
Варианты: question, action, complaint, other.
Текст: {text}
"""
