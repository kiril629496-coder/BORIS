"""
BORIS — shared OpenAI image HTTP transport.

ВАЖНО:
    Этот модуль НЕ управляет бюджетом.
    Он НЕ пишет в БД.
    Он НЕ вызывает banner_generator.
    Он НЕ вызывает image_service.

Финансовый контур находится выше:
    reserve -> allow_next -> HTTP -> usage -> commit/release
"""

import os
import base64
import requests
from app.services.reliability import dependency_call, ProviderDeferred, CircuitOpen
from app.services.ai_privacy import safe_external_ai_payload
from dotenv import load_dotenv

load_dotenv()

OPENAI_IMAGES_URL = "https://api.openai.com/v1/images/generations"


def generate_image_http(
    prompt: str,
    size: str = "1024x1024",
    quality: str = "low",
    model: str = "gpt-image-1-mini",
    max_attempts: int = 1,
    account_id: str | None = None,
    idempotency_key: str | None = None,
):
    """
    Только transport.

    Никаких DB/budget операций здесь нет.

    Возвращает:
        {
            "ok": bool,
            "path_data": bytes | None,
            "request_id": str | None,
            "usage": dict | None,
            "size": str,
            "quality": str,
            "status_code": int | None,
        }
    """

    api_key = os.getenv("OPENAI_API_KEY")
    external_prompt, _privacy_vault = safe_external_ai_payload(str(prompt))

    if not api_key:
        return {
            "ok": False,
            "path_data": None,
            "request_id": None,
            "usage": None,
            "size": size,
            "quality": quality,
            "status_code": None,
        }

    # Paid image transport is intentionally single-attempt by default.
    # The caller owns any explicit retry as a new economic intent; ambiguous
    # transport/provider failures must never silently multiply image charges.
    max_attempts = max(1, min(int(max_attempts or 1), 1))
    for attempt in range(1, max_attempts + 1):
        try:
            response = dependency_call(
                "openai.images", requests.post, OPENAI_IMAGES_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    **({"Idempotency-Key": str(idempotency_key)} if idempotency_key else {}),
                },
                json={
                    "model": model,
                    "prompt": external_prompt,
                    "size": size,
                    "quality": quality,
                    "n": 1,
                },
                timeout=240, account_id=account_id, tenant_limit_per_minute=12,
            )

            request_id = response.headers.get("x-request-id")

            if response.status_code != 200:
                print(
                    "[openai_image_transport] "
                    f"attempt={attempt} "
                    f"status={response.status_code} "
                    f"request_id={request_id}",
                    flush=True,
                )
                return {
                    "ok": False, "path_data": None, "request_id": request_id,
                    "usage": None, "size": size, "quality": quality,
                    "status_code": response.status_code,
                    "ambiguous_transport": False, "error_code": "provider_http_error",
                }

            try:
                data = response.json()
                first = (data.get("data") or [{}])[0]

                img_b64 = first.get("b64_json")

                usage = data.get("usage")
                actual_size = data.get("size") or size
                actual_quality = data.get("quality") or quality

            except Exception as exc:
                print(
                    "[openai_image_transport] "
                    f"json_parse_failed attempt={attempt}: "
                    f"{str(exc)[:160]}",
                    flush=True,
                )
                return {
                    "ok": False, "path_data": None, "request_id": request_id,
                    "usage": None, "size": size, "quality": quality,
                    "status_code": response.status_code,
                    "ambiguous_transport": True, "error_code": "provider_200_unparseable",
                }

            if not img_b64:
                print(
                    "[openai_image_transport] "
                    f"empty_b64 attempt={attempt} "
                    f"request_id={request_id}",
                    flush=True,
                )
                return {
                    "ok": False, "path_data": None, "request_id": request_id,
                    "usage": usage, "size": actual_size, "quality": actual_quality,
                    "status_code": response.status_code,
                    "ambiguous_transport": True, "error_code": "provider_200_empty_image",
                }

            try:
                decoded = base64.b64decode(img_b64)
            except Exception as exc:
                print(
                    "[openai_image_transport] "
                    f"base64_failed attempt={attempt}: "
                    f"{str(exc)[:160]}",
                    flush=True,
                )
                return {
                    "ok": False, "path_data": None, "request_id": request_id,
                    "usage": usage, "size": actual_size, "quality": actual_quality,
                    "status_code": response.status_code,
                    "ambiguous_transport": True, "error_code": "provider_200_bad_image_payload",
                }

            return {
                "ok": True,
                "path_data": decoded,
                "request_id": request_id,
                "usage": usage,
                "size": actual_size,
                "quality": actual_quality,
                "status_code": response.status_code,
            }

        except (ProviderDeferred, CircuitOpen):
            raise
        except Exception as exc:
            print(
                "[openai_image_transport] "
                f"attempt={attempt} failed: {str(exc)[:200]}",
                flush=True,
            )
            # A disconnect/timeout can happen after the provider accepted and
            # charged the request. Surface this explicitly so the financial
            # layer can quarantine the stable intent instead of buying it again.
            return {
                "ok": False, "path_data": None, "request_id": None,
                "usage": None, "size": size, "quality": quality,
                "status_code": None, "ambiguous_transport": True,
                "error_code": "transport_outcome_unknown",
            }

    return {
        "ok": False,
        "path_data": None,
        "request_id": None,
        "usage": None,
        "size": size,
        "quality": quality,
        "status_code": None,
        "ambiguous_transport": False,
        "error_code": "image_generation_failed",
    }
