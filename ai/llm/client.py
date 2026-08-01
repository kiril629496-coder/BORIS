"""LLM client abstraction."""

import os
from dataclasses import dataclass


@dataclass
class LLMResponse:
    text: str
    model: str
    tokens_used: int | None = None


class LLMClient:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.model = model or os.getenv("LLM_MODEL", "gpt-4o-mini")

    async def complete(self, prompt: str, *, system: str | None = None) -> LLMResponse:
        # TODO: integrate with OpenAI / other provider
        return LLMResponse(
            text=f"[stub] Response to: {prompt[:50]}...",
            model=self.model,
        )
