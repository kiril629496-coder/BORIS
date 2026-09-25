#!/usr/bin/env python3
from app.usage import PRICES, MODEL_ALIASES, USD_TO_RUB, calc_cost_detailed

def main():
    rates=PRICES.get(("openai","gpt-5-mini"))
    assert rates, "gpt-5-mini pricing missing"
    assert rates["in"] == 0.25
    assert rates["cached_in"] == 0.025
    assert rates["out"] == 2.00
    assert MODEL_ALIASES.get("gpt-5-mini-2025-08-07") == "gpt-5-mini"

    prompt=729
    completion=1221
    expected=round(((prompt*0.25)+(completion*2.00))/1_000_000*USD_TO_RUB,4)
    cost,src=calc_cost_detailed("openai","gpt-5-mini",prompt_tokens=prompt,completion_tokens=completion)
    assert src == "table", (src,cost)
    assert cost == expected, (cost,expected)

    alias_cost,alias_src=calc_cost_detailed("openai","gpt-5-mini-2025-08-07",prompt_tokens=prompt,completion_tokens=completion)
    assert alias_src == "table"
    assert alias_cost == expected
    print("GPT5_MINI_PRICING_REGRESSION=PASS")
    print(f"USD_RUB={USD_TO_RUB}")
    print(f"SAMPLE_COST_RUB={cost}")

if __name__ == "__main__":
    main()
