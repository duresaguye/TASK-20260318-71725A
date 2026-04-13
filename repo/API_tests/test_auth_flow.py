from uuid import uuid4

from conftest import auth_headers, register_and_login


def test_auth_register_login_logout_and_token_reuse(client):
    username = f"auth-{uuid4().hex[:8]}"
    password = "Strong123"

    register_response = client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": password},
        headers={"x-forwarded-proto": "https"},
    )
    assert register_response.status_code == 201

    login_response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers={"x-forwarded-proto": "https"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    logout_response = client.post("/api/v1/auth/logout", headers=auth_headers(token))
    assert logout_response.status_code == 200

    reuse_response = client.post("/api/v1/auth/logout", headers=auth_headers(token))
    assert reuse_response.status_code == 401


def test_auth_invalid_credentials_rejected(client):
    username = f"auth-invalid-{uuid4().hex[:8]}"
    password = "Strong123"
    register_and_login(client, username, password)

    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "Wrong123"},
        headers={"x-forwarded-proto": "https"},
    )
    assert response.status_code == 401
