from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.deps.auth import get_current_user
from app.db.deps import get_db
from app.main import app


def _headers(include_auth: bool = False):
    headers = {"x-forwarded-proto": "https"}
    if include_auth:
        headers["Authorization"] = "Bearer fake-token"
    return headers


def _dummy_db():
    yield None


def test_health_requires_authentication():
    client = TestClient(app)
    response = client.get("/health", headers=_headers())
    assert response.status_code == 401


def test_health_rejects_plain_http_without_forwarded_https():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 400


def test_health_accepts_forwarded_https():
    client = TestClient(app)
    response = client.get("/health", headers=_headers())
    assert response.status_code == 401


def test_analytics_requires_authentication():
    client = TestClient(app)
    response = client.get(
        "/api/v1/analytics/dashboard",
        headers=_headers(),
    )
    assert response.status_code == 401


def test_governance_errors_endpoint_forbids_general_user():
    client = TestClient(app)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=uuid4(),
        organization_id=uuid4(),
        role="general_user",
        is_active=True,
    )
    app.dependency_overrides[get_db] = _dummy_db
    try:
        response = client.get(
            f"/api/v1/data/import/{uuid4()}/errors",
            headers=_headers(include_auth=True),
        )
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_organization_users_endpoint_blocks_cross_org_access():
    client = TestClient(app)
    current_org = uuid4()
    foreign_org = uuid4()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=uuid4(),
        organization_id=current_org,
        role="administrator",
        is_active=True,
    )
    app.dependency_overrides[get_db] = _dummy_db
    try:
        response = client.get(
            f"/api/v1/organizations/{foreign_org}/users",
            headers=_headers(include_auth=True),
        )
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_password_recovery_alias_is_available(monkeypatch):
    client = TestClient(app)
    app.dependency_overrides[get_db] = _dummy_db
    monkeypatch.setattr(
        "app.api.routes.auth.AuthService.request_password_recovery",
        lambda **_: {"message": "If the account exists, a reset link has been sent"},
    )
    try:
        response = client.post(
            "/api/v1/auth/recover",
            json={"username": "alice"},
            headers=_headers(),
        )
        assert response.status_code == 200
        assert response.json()["message"] == "If the account exists, a reset link has been sent"
    finally:
        app.dependency_overrides.clear()
