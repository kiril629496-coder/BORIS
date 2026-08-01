from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_create_batch() -> None:
    response = client.post(
        "/batch/create",
        json={
            "product": "iPhone 15",
            "price": 75000,
            "city": "Москва",
            "count": 3,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["total"] == 3
    assert len(data["items"]) == 3

    item = data["items"][0]
    assert item["title"] == "iPhone 15"
    assert item["price"] == 75000
    assert item["city"] == "Москва"
    assert "iPhone 15" in item["description"]
    assert "Москва" in item["description"]
