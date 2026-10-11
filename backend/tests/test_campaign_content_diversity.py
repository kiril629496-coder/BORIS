from app.services.campaign_content_diversity import analyze_content_diversity,hypothesis_family,normalize_copy

def item(i,title,desc,family):
    return {"id":i,"title":title,"description":desc,"hypothesis_family":family}

def test_unique_campaign_passes():
    nouns=["крыша","фасад","забор","сайдинг","водосток","утепление","монтаж","доставка","замер","комплект",
           "профнастил","черепица","панели","доборы","крепёж","гарантия","проект","склад","расчёт","подбор"]
    rows=[item(i,f"{word.capitalize()} для вашего дома",f"{word.capitalize()} — отдельное полезное предложение с подробным сценарием выбора и покупки "+("x"*30),f"family-{i%4}") for i,word in enumerate(nouns,1)]
    out=analyze_content_diversity(rows)
    assert out["status"]=="PASS"
    assert not out["blockers"]

def test_repeated_short_titles_warn_but_do_not_create_hidden_publish_gate():
    nouns=["крыша","фасад","забор","сайдинг","водосток","утепление","монтаж","доставка","замер","комплект",
           "профнастил","черепица","панели","доборы","крепёж","гарантия","проект","склад","расчёт","подбор"]
    rows=[]
    for i,word in enumerate(nouns,1):
        title="Кровля Grand Line" if i in {1,2} else f"{word.capitalize()} Grand Line"
        rows.append(item(i,title,f"{word.capitalize()} — отдельный сценарий выбора, выгоды и следующего шага "+("y"*30),f"family-{i%5}"))
    out=analyze_content_diversity(rows)
    assert out["status"]=="PASS_WITH_WARNINGS"
    assert "title_exact_repeats" in out["warnings"]
    assert not out["blockers"]

def test_monoculture_fails():
    rows=[item(i,f"Товар {i}",f"Одинаковое описание {i}", "same") for i in range(1,21)]
    out=analyze_content_diversity(rows)
    assert out["status"]=="FAIL"
    assert "hypothesis_family_count" in out["blockers"]
    assert "hypothesis_monoculture" in out["blockers"]

def test_normalization_removes_geo_and_numbers():
    assert normalize_copy("Кухня Москва 2026") == normalize_copy("Кухня Санкт-Петербург 777")

def test_hypothesis_family_prefers_creative_angle():
    assert hypothesis_family({"creative_angle":"problem_solution","hypothesis_id":"formula:pas:x"})=="problem_solution"
    assert hypothesis_family({"hypothesis_id":"mortgage:7"})=="mortgage"
