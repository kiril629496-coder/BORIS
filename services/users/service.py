"""User domain service."""

from dataclasses import dataclass
from uuid import UUID


@dataclass
class User:
    id: UUID
    email: str
    name: str | None = None
    is_active: bool = True


class UserService:
    async def get_by_email(self, email: str) -> User | None:
        # TODO: implement with database repository
        return None
