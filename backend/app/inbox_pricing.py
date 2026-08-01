"""Цены слотов «Единого центра сообщений».

Сетка (Кирилл, 30.07): 1-3 -> 4000 руб/слот, 4-8 -> 3000, 9-15 -> 2000.
Пол по сумме обязателен: без него 9 слотов стоили бы 18000, дешевле
восьми за 24000. Тот же принцип, что в pricing.period_total.
"""

PRODUCT = "inbox"
TIERS = ((3, 4000), (8, 3000), (15, 2000))
TAIL_PRICE = 2000


def price_per_slot(count):
    n = max(int(count or 0), 0)
    if n <= 0:
        return 0
    for limit, price in TIERS:
        if n <= limit:
            return price
    return TAIL_PRICE


def _naive_total(n):
    return price_per_slot(n) * n


def slots_total(count):
    """Итого в месяц за count слотов, с полом по сумме."""
    n = max(int(count or 0), 0)
    total = 0
    for k in range(1, n + 1):
        naive = _naive_total(k)
        if naive > total:
            total = naive
    return total


def add_slots_price(current, adding=1):
    """Доплата за месяц при добавлении слотов к уже оплаченным.

    Внутри периода поверх этой суммы применяется существующий
    upgrade_charge из app.pricing (пропорция по остатку дней).
    """
    cur = max(int(current or 0), 0)
    add = max(int(adding or 0), 0)
    return slots_total(cur + add) - slots_total(cur)


def price_table(max_slots=15):
    rows, prev = [], 0
    for n in range(1, max_slots + 1):
        total = slots_total(n)
        rows.append({
            "slots": n,
            "per_slot": price_per_slot(n),
            "total": total,
            "effective_per_slot": round(total / n),
            "floored": total != _naive_total(n),
            "delta": total - prev,
        })
        prev = total
    return rows
