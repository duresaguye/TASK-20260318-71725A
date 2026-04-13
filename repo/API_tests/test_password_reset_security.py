from uuid import uuid4

from conftest import auth_headers, register_and_login


def _issue_reset_token(client, username: str, monkeypatch) -> str:
    monkeypatch.setattr("app.services.auth_service.AuthService.EXPOSE_RESET_TOKEN", True)
    response = client.post(
        "/api/v1/auth/password-recovery/request",
        json={"username": username},
        headers={"x-forwarded-proto": "https"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert "reset_token" in payload
    return payload["reset_token"]


def test_password_reset_invalidates_old_token(client, monkeypatch):
    username = f"reset-invalidates-{uuid4().hex[:8]}"
    old_password = "Strong123"
    new_password = "NewStrong123"
    old_token = register_and_login(client, username, old_password)

    reset_token = _issue_reset_token(client, username, monkeypatch)
    reset_response = client.post(
        "/api/v1/auth/password-recovery/reset",
        json={"token": reset_token, "new_password": new_password},
        headers={"x-forwarded-proto": "https"},
    )
    assert reset_response.status_code == 200

    old_token_response = client.get("/health", headers=auth_headers(old_token))
    assert old_token_response.status_code == 401


def test_login_after_reset_required(client, monkeypatch):
    username = f"reset-login-{uuid4().hex[:8]}"
    old_password = "Strong123"
    new_password = "NewStrong123"
    register_and_login(client, username, old_password)

    reset_token = _issue_reset_token(client, username, monkeypatch)
    reset_response = client.post(
        "/api/v1/auth/password-recovery/reset",
        json={"token": reset_token, "new_password": new_password},
        headers={"x-forwarded-proto": "https"},
    )
    assert reset_response.status_code == 200

    old_login_response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": old_password},
        headers={"x-forwarded-proto": "https"},
    )
    assert old_login_response.status_code == 401

    new_login_response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": new_password},
        headers={"x-forwarded-proto": "https"},
    )
    assert new_login_response.status_code == 200


def test_old_jwt_rejected_after_reset(client, monkeypatch):
    username = f"reset-jwt-{uuid4().hex[:8]}"
    old_password = "Strong123"
    new_password = "NewStrong123"
    old_token = register_and_login(client, username, old_password)

    reset_token = _issue_reset_token(client, username, monkeypatch)
    reset_response = client.post(
        "/api/v1/auth/password-recovery/reset",
        json={"token": reset_token, "new_password": new_password},
        headers={"x-forwarded-proto": "https"},
    )
    assert reset_response.status_code == 200

    logout_response = client.post("/api/v1/auth/logout", headers=auth_headers(old_token))
    assert logout_response.status_code == 401


def test_password_reset_request_is_generic_for_existing_and_missing_accounts(client, monkeypatch):
    existing_username = f"reset-generic-{uuid4().hex[:8]}"
    missing_username = f"missing-generic-{uuid4().hex[:8]}"

    register_and_login(client, existing_username, "Strong123")
    monkeypatch.setattr("app.services.auth_service.AuthService.EXPOSE_RESET_TOKEN", False)

    existing_response = client.post(
        "/api/v1/auth/password-recovery/request",
        json={"username": existing_username},
        headers={"x-forwarded-proto": "https"},
    )
    missing_response = client.post(
        "/api/v1/auth/password-recovery/request",
        json={"username": missing_username},
        headers={"x-forwarded-proto": "https"},
    )

    assert existing_response.status_code == 200
    assert missing_response.status_code == 200
    assert existing_response.json() == missing_response.json()
    assert existing_response.json()["message"] == "If the account exists, a reset link has been sent"
