import inspect
import posting_runner as pr


def test_banner_prompt_is_semantically_bound_to_final_post():
    prompt = pr._build_banner_prompt(
        "как выбрать правильную категорию на Авито",
        "",
        "Категория решает",
        "",
        post_text=(
            "<b>Неверная категория режет охват</b>\n\n"
            "Когда товар попадает не в тот раздел Авито, покупатель просто не находит объявление."
        ),
    )
    assert "СМЫСЛ ГОТОВОГО ПОСТА" in prompt
    assert "Неверная категория режет охват" in prompt
    assert "покупатель просто не находит объявление" in prompt
    assert "Один баннер = одна мысль" in prompt
    assert "<b>" not in prompt


def test_direct_social_ai_banner_uses_low_cost_and_final_text_context():
    src = inspect.getsource(pr.generate_post)
    assert 'post_text=result.get("text", "")' in src
    assert '"gpt-image-2|1024x1024|low|banner_social_post_v2"' in src
    assert 'quality="low"' in src
    assert "quality=low semantic_link=v2" in src
