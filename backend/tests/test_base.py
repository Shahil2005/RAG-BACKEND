from fastapi import status
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_pulse() -> None:
    """Liveness endpoint — no DB required."""
    response = client.get("/api/v1/pulse")
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"message": "I'm Alive"}
