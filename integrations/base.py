"""Base integration client."""

from abc import ABC, abstractmethod


class BaseIntegration(ABC):
    @abstractmethod
    async def health_check(self) -> bool:
        """Verify connection to external service."""
