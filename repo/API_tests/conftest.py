import os
from pathlib import Path
from uuid import UUID, uuid4

os.environ.setdefault("ATTACHMENT_STORAGE_ROOT", str(Path("/tmp") / "medical_platform_uploads_test"))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from app.core.security import hash_password
from app.db.deps import get_db
from app.db.session import SessionLocal, engine
from app.main import app
from app.models.user import User


@pytest.fixture()
def db_session():
    connection = engine.connect()
    transaction = connection.begin()
    session = SessionLocal(bind=connection)
    session.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess, trans):
        if trans.nested and not trans._parent.nested:
            sess.begin_nested()

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture()
def client(db_session):
    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def auth_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "x-forwarded-proto": "https",
    }


def register_and_login(client: TestClient, username: str, password: str) -> str:
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
    return login_response.json()["access_token"]


def get_user(db_session, username: str) -> User:
    user = db_session.scalar(select(User).where(User.username == username))
    assert user is not None
    return user


def update_user_membership(
    db_session,
    username: str,
    *,
    organization_id=None,
    role: str | None = None,
    is_active: bool | None = None,
) -> User:
    user = get_user(db_session, username)
    if organization_id is not None:
        if isinstance(organization_id, str):
            organization_id = UUID(organization_id)
        user.organization_id = organization_id
    if role is not None:
        user.role = role
    if is_active is not None:
        user.is_active = is_active
    db_session.commit()
    db_session.refresh(user)
    return user


def assign_user_role_via_api(
    client: TestClient,
    acting_token: str,
    *,
    org_id: str,
    user_id: str,
    role: str,
):
    response = client.post(
        f"/api/v1/organizations/{org_id}/users/{user_id}/roles",
        json={"role": role},
        headers=auth_headers(acting_token),
    )
    assert response.status_code == 200
    return response.json()


def create_organization_via_api(client: TestClient, token: str, name: str, code: str):
    response = client.post(
        "/api/v1/organizations",
        json={"name": name, "code": code},
        headers=auth_headers(token),
    )
    assert response.status_code == 200
    return response.json()


def create_user_record(
    db_session,
    *,
    username: str | None = None,
    password: str = "Strong123",
    organization_id=None,
    role: str = "general_user",
    is_active: bool = True,
) -> User:
    user = User(
        username=username or f"user-{uuid4()}",
        hashed_password=hash_password(password),
        organization_id=organization_id,
        role=role,
        is_active=is_active,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user
