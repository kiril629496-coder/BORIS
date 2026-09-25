from app.main import app


def test_legacy_batch_create_is_not_publicly_mounted() -> None:
    """The old unauthenticated prototype must not reappear in production."""
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/batch/create" not in paths


def test_batch_domain_service_still_builds_expected_items() -> None:
    # Keep coverage of the small legacy domain helper without exposing its old
    # unauthenticated HTTP surface. Import from the repository root explicitly.
    import sys
    from pathlib import Path

    root = str(Path(__file__).resolve().parents[2])
    if root not in sys.path:
        sys.path.insert(0, root)
    from services.batch.service import BatchService

    items = BatchService().create_batch(
        product="iPhone 15",
        price=75000,
        city="Москва",
        count=3,
    )
    assert len(items) == 3
    item = items[0]
    assert item.title == "iPhone 15"
    assert item.price == 75000
    assert item.city == "Москва"
    assert "iPhone 15" in item.description
    assert "Москва" in item.description
