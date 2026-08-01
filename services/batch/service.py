"""Batch announcements domain service."""

from dataclasses import dataclass


@dataclass
class BatchItemData:
    title: str
    description: str
    price: int
    city: str


class BatchService:
    def create_batch(
        self,
        product: str,
        price: int,
        city: str,
        count: int,
    ) -> list[BatchItemData]:
        description = f"Продажа {product} в г. {city}. Цена: {price} ₽."

        return [
            BatchItemData(
                title=product,
                description=description,
                price=price,
                city=city,
            )
            for _ in range(count)
        ]
