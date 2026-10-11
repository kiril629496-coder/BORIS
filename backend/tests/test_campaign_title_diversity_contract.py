from app.api.campaigns import _ff_select_title, _ff_title_variants


def test_soft_furniture_exact10_has_at_least_six_fact_safe_titles():
    titles = [_ff_select_title("Чистка мягкой мебели", i) for i in range(1, 11)]
    assert len(set(titles)) >= 6, titles
    banned = ("скидк", "гарант", "за час", "лучший", "дешев", "срочн")
    assert all(not any(token in title.casefold() for token in banned) for title in titles)
    assert all(1 <= len(title.split()) <= 3 for title in titles)


def test_narrow_title_pool_returns_to_core_after_safe_alternatives():
    pool = _ff_title_variants("Кухня на заказ")
    titles = [_ff_select_title("Кухня на заказ", i) for i in range(1, 6)]
    assert titles[0] == pool[0]
    assert set(titles).issubset(set(pool))
    assert titles[-1] == pool[0]
