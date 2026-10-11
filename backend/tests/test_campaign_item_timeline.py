from app.services.campaign_item_timeline import _iso, _job_mentions_item, revision_event_label

def test_revision_labels_are_human():
    assert revision_event_label(["title"])=="Текст объявления обновлён"
    assert revision_event_label(["price"])=="Цена обновлена"
    assert revision_event_label(["category_path","params"])=="Категория и параметры обновлены"
    assert revision_event_label(["images","media"])=="Фотографии обновлены"
    assert revision_event_label(["banner_url","images"])=="Баннер обновлён"

def test_job_scope_exact_single_and_batch():
    assert _job_mentions_item({"campaign_id":517,"campaign_item_id":5859},517,5859)
    assert not _job_mentions_item({"campaign_id":517,"campaign_item_id":5860},517,5859)
    assert _job_mentions_item({"campaign_id":517,"item_ids":[5859,5860]},517,5859)
    assert not _job_mentions_item({"campaign_id":517,"item_ids":[5860,5861]},517,5859)
    assert _job_mentions_item({"campaign_id":517,"stage":"banners"},517,5859)
    assert not _job_mentions_item({"campaign_id":518,"stage":"banners"},517,5859)

def test_iso_never_invents_invalid_timestamp():
    assert _iso(None) is None
    assert _iso("") is None
    assert _iso("not-a-date") is None
    assert _iso("2026-10-07T12:00:00+00:00").startswith("2026-10-07T12:00:00")
