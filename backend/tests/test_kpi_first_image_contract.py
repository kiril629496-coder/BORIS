from app.api.avito import (
    KPI_APPLY_ALLOWED_FIELDS,
    KPI_APPLY_PROTECTED_FIELDS,
    _kpi_compare_expected_with_live,
    _kpi_compare_rollback_snapshot_with_live,
    _kpi_normalize_feed_field,
    _kpi_partial_first_image_overwrite_recovery,
    _assert_feed_gallery_unique,
)


def test_images_are_autonomous_only_through_guarded_apply_contract():
    assert "images" in KPI_APPLY_ALLOWED_FIELDS
    assert "images" not in KPI_APPLY_PROTECTED_FIELDS


def test_feed_field_normalizer_preserves_legacy_images_key():
    assert _kpi_normalize_feed_field({"Images": ["a", "b"]}, "images") == "Images"
    assert _kpi_normalize_feed_field({"images": ["a", "b"]}, "images") == "images"


def test_first_image_confirmation_checks_first_slot_only():
    op = {"changes": {"images": {"old": ["a", "b", "c"], "new": ["b", "a", "c"]}}}
    ok = _kpi_compare_expected_with_live(op, {"images": ["b", "normalized-a"]})
    assert ok["status"] == "ok"
    assert ok["all_match"] is True

    bad = _kpi_compare_expected_with_live(op, {"images": ["a", "b", "c"]})
    assert bad["all_match"] is False


def test_rollback_confirmation_checks_original_first_image():
    op = {
        "changes": {"images": {"old": ["a", "b", "c"], "new": ["b", "a", "c"]}},
        "rollback": {"snapshot": {"images": ["a", "b", "c"]}},
    }
    ok = _kpi_compare_rollback_snapshot_with_live(op, {"images": ["a", "provider-normalized"]})
    assert ok["status"] == "ok"
    assert ok["all_match"] is True

    bad = _kpi_compare_rollback_snapshot_with_live(op, {"images": ["b", "a", "c"]})
    assert bad["all_match"] is False


def test_rollback_confirmation_checks_only_mutated_text_fields():
    op = {
        "changes": {"title": {"old": "Старый", "new": "Новый"}},
        "rollback": {"snapshot": {"title": "Старый", "description": "Описание"}},
    }
    ok = _kpi_compare_rollback_snapshot_with_live(op, {"title": "Старый", "description": "Другое"})
    assert ok["all_match"] is True


def test_partial_first_image_overwrite_is_recovered_only_for_exact_signature():
    delta={"old":["a","b","c"],"new":["b","a","c"]}
    assert _kpi_partial_first_image_overwrite_recovery(["b","b","c"],delta)==["a","b","c"]
    assert _kpi_partial_first_image_overwrite_recovery(["b","x","c"],delta) is None
    assert _kpi_partial_first_image_overwrite_recovery(["b","a","c"],delta) is None


def test_feed_gallery_duplicate_image_fails_closed():
    _assert_feed_gallery_unique({"id":"ok","images":["a","b","c"]})
    try:
        _assert_feed_gallery_unique({"id":"bad","images":["a","a","c"]})
    except RuntimeError as exc:
        assert str(exc)=="FEED_GALLERY_DUPLICATE_IMAGE:bad"
    else:
        raise AssertionError("duplicate gallery must fail closed")


def test_public_gallery_parser_selects_target_imageurls_not_unrelated_images():
    from app.api.avito import _kpi_extract_public_first_image_url
    page = (
        'avatar=https://20.img.avito.st/image/avatar '
        '\\\"imageUrls\\\":[{\\\"1280x1280\\\":\\\"'
        'https://70.img.avito.st/image/1/live-first\\\",'
        '\\\"640x480\\\":\\\"https://70.img.avito.st/image/1/live-small\\\"}]'
    )
    assert _kpi_extract_public_first_image_url(page) == 'https://70.img.avito.st/image/1/live-first'


def test_image_confirmation_uses_public_phash_proof_when_inventory_omits_images(monkeypatch):
    import app.api.avito as avito
    seen = {}

    def fake_proof(expected, url):
        seen['expected'] = expected
        seen['url'] = url
        return {
            'source': 'avito_public_item_gallery_phash',
            'match': True,
            'distance': 6,
            'threshold': 8,
        }

    monkeypatch.setattr(avito, '_kpi_public_first_image_proof', fake_proof)
    expected = '/images/account/session/expected.png'
    op = {'changes': {'images': {'old': ['old'], 'new': [expected, 'second']}}}
    result = avito._kpi_compare_expected_with_live(
        op,
        {'url': 'https://www.avito.ru/city/category/item_123', 'title': 'irrelevant'},
    )
    assert result['all_match'] is True
    assert result['checks'][0]['proof']['distance'] == 6
    assert seen == {
        'expected': expected,
        'url': 'https://www.avito.ru/city/category/item_123',
    }


def test_image_confirmation_public_proof_fails_closed(monkeypatch):
    import app.api.avito as avito
    monkeypatch.setattr(
        avito,
        '_kpi_public_first_image_proof',
        lambda expected, url: {
            'source': 'avito_public_item_gallery_phash',
            'match': False,
            'reason': 'public_gallery_not_found',
        },
    )
    op = {'changes': {'images': {'old': ['old'], 'new': ['/images/a.png']}}}
    result = avito._kpi_compare_expected_with_live(
        op,
        {'url': 'https://www.avito.ru/city/category/item_123'},
    )
    assert result['all_match'] is False
    assert result['checks'][0]['proof']['reason'] == 'public_gallery_not_found'
