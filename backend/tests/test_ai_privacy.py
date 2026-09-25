from app.services.ai_privacy import (
    anonymize_text,
    safe_external_ai_payload,
    restore_external_ai_output,
    contains_obvious_pii,
    sanitize_external_reference_image_bytes,
)


def test_text_redaction_and_restore():
    src = (
        "Клиент: Иванов Иван Иванович; телефон +7 (999) 123-45-67; "
        "email ivan.petrov@example.com; Telegram @ivan_petrov; "
        "адрес: г. Москва, ул. Тверская, д. 10, кв. 5; "
        "IP 192.168.10.55; ИНН 123456789012."
    )
    clean, vault = anonymize_text(src)
    assert "ivan.petrov@example.com" not in clean
    assert "999" not in clean
    assert "@ivan_petrov" not in clean
    assert "192.168.10.55" not in clean
    assert "123456789012" not in clean
    assert "Иванов Иван Иванович" not in clean
    assert "[[BORIS_" in clean
    assert not contains_obvious_pii(clean)
    assert restore_external_ai_output(clean, vault) == src


def test_nested_messages_share_one_vault():
    payload = {
        "messages": [
            {"role": "user", "content": "Позвони +7 999 123-45-67"},
            {"role": "assistant", "content": "Телефон +7 999 123-45-67 записан"},
        ]
    }
    clean, vault = safe_external_ai_payload(payload)
    assert "+7 999 123-45-67" not in str(clean)
    tokens = [k for k in vault.mapping if "PHONE" in k]
    assert len(tokens) == 1
    restored = restore_external_ai_output(clean, vault)
    assert restored == payload


def test_non_pii_business_text_survives():
    src = "Нужна CRM для отдела продаж: 70 лидов в день, бюджет 150000 ₽."
    clean, vault = anonymize_text(src)
    assert clean == src
    assert vault.mapping == {}


def test_structured_personal_fields_are_tokenized():
    payload = {
        "client_name": "Анна",
        "phone": "+374 99 123456",
        "email": "anna@example.com",
        "deal_id": 123456,
        "model": "deepseek-v4-flash",
    }
    clean, vault = safe_external_ai_payload(payload)
    assert clean["client_name"].startswith("[[BORIS_PERSON_")
    assert clean["phone"].startswith("[[BORIS_PHONE_")
    assert clean["email"].startswith("[[BORIS_EMAIL_")
    assert clean["deal_id"].startswith("[[BORIS_ID_")
    assert clean["model"] == "deepseek-v4-flash"
    assert restore_external_ai_output(clean, vault) == {
        **payload,
        "deal_id": "123456",
    }


def test_external_reference_image_is_downscaled_reencoded_and_metadata_free():
    from io import BytesIO
    from PIL import Image, PngImagePlugin

    img = Image.new("RGB", (1200, 800), "white")
    meta = PngImagePlugin.PngInfo()
    meta.add_text("Owner", "Иван Иванов +7 999 123-45-67")
    src = BytesIO()
    img.save(src, format="PNG", pnginfo=meta)

    safe, mime = sanitize_external_reference_image_bytes(src.getvalue())
    assert mime == "image/jpeg"
    assert "Иван Иванов".encode("utf-8") not in safe
    with Image.open(BytesIO(safe)) as out:
        assert max(out.size) <= 384
        assert out.format == "JPEG"
        assert not out.getexif()


def test_structured_account_org_and_secret_fields_are_minimized():
    payload = {
        "account_id": "mbroker_27859",
        "company_name": "ООО Ромашка",
        "access_token": "super-secret-token-value",
        "password": "not-for-provider",
        "message": "Работаем по заявке",
    }
    clean, vault = safe_external_ai_payload(payload)
    assert clean["account_id"].startswith("[[BORIS_ID_")
    assert clean["company_name"].startswith("[[BORIS_ORG_")
    assert clean["access_token"] == "[[BORIS_SECRET_REDACTED]]"
    assert clean["password"] == "[[BORIS_SECRET_REDACTED]]"
    restored = restore_external_ai_output(clean, vault)
    assert restored["account_id"] == "mbroker_27859"
    assert restored["company_name"] == "ООО Ромашка"
    assert restored["access_token"] == "[[BORIS_SECRET_REDACTED]]"
    assert restored["password"] == "[[BORIS_SECRET_REDACTED]]"


def test_raw_labelled_ids_orgs_and_url_secrets_are_minimized():
    raw = (
        "Компания: ООО Ромашка\n"
        "account_id=mbroker_27859\n"
        "Ссылка https://example.com/callback?token=abc123secret&item=42\n"
        "api_key=very-secret-key"
    )
    clean, vault = safe_external_ai_payload(raw)
    assert "ООО Ромашка" not in clean
    assert "mbroker_27859" not in clean
    assert "abc123secret" not in clean
    assert "very-secret-key" not in clean
    assert "[[BORIS_ORG_" in clean
    assert "[[BORIS_ID_" in clean
    assert clean.count("[[BORIS_SECRET_REDACTED]]") >= 2
    restored = restore_external_ai_output(clean, vault)
    assert "ООО Ромашка" in restored
    assert "mbroker_27859" in restored
    assert "abc123secret" not in restored
    assert "very-secret-key" not in restored
