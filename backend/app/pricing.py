"""Цена подписки от числа подключённых аккаунтов.
Правило (19.07): доплата за новый аккаунт — сразу, пропорционально остатку дней,
по ТЕКУЩЕЙ цене вилки. Переход в более дешёвую вилку — только со следующего периода."""

# (максимум аккаунтов в вилке, цена тариф_1, цена тариф_2)
TIERS = [
    (1,           7000, 14000),   # частник
    (4,           6000, 12000),   # 2–4
    (14,          5000, 10000),   # 5–14
    (float("inf"), 4500,  9000),  # 15+
]


def price_per_account(n_accounts: int, tariff: str = "tariff_1") -> int:
    n = max(1, int(n_accounts or 1))
    for limit, p1, p2 in TIERS:
        if n <= limit:
            return p1 if tariff == "tariff_1" else p2
    return TIERS[-1][1] if tariff == "tariff_1" else TIERS[-1][2]


def period_total(n_accounts: int, tariff: str = "tariff_1") -> int:
    """Сумма за период. Пол: переход в более дешёвую вилку не может уменьшить счёт —
    иначе 15 аккаунтов стоили бы дешевле, чем 14."""
    n = max(1, int(n_accounts or 1))
    total = price_per_account(n, tariff) * n
    floor = 0
    for limit, p1, p2 in TIERS:
        if limit != float("inf") and limit < n:
            floor = max(floor, (p1 if tariff == "tariff_1" else p2) * limit)
    return max(total, floor)


def upgrade_charge(current_n: int, added: int, days_left: int,
                   period_days: int = 30, tariff: str = "tariff_1") -> int:
    """Доплата за добавленные аккаунты до конца оплаченного периода.
    Считается по ТЕКУЩЕЙ цене вилки — скидка новой вилки включится со следующего периода."""
    rate_now = price_per_account(current_n, tariff)
    days_left = max(0, min(days_left, period_days))
    return round(rate_now * added * days_left / period_days)
