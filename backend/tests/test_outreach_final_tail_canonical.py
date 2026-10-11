from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def test_software_site_status_sync_maps_to_existing_crm_truth():
    s=(ROOT/'app/api/software_site.py').read_text()
    for token in (
        'crm_status_map',
        '"Квалифицирована": "квалифицирован"',
        '"КП": "целевое действие"',
        '"Переговоры": "целевое действие"',
        '"Оплачено": "сделка"',
        '"Проиграно": "отказ"',
        "value::jsonb->>'crm_lead_id'",
    ):
        assert token in s

def test_tg_failed_today_excludes_terminal_suppressed_rows():
    s=(ROOT/'app/services/prospect_unified.py').read_text()
    assert "status NOT IN ('skipped','declined','lost','ignored')" in s
