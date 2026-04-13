from uuid import UUID, uuid4

from app.core.encryption import decrypt_string
from app.models.audit_log import AuditLog
from app.models.data_import_error import DataImportError
from conftest import auth_headers, create_organization_via_api, get_user, register_and_login


def _create_org_with_owner(client):
    owner_username = f"security-owner-{uuid4().hex[:8]}"
    owner_token = register_and_login(client, owner_username, "Strong123")
    organization = create_organization_via_api(
        client,
        owner_token,
        name=f"Security Org {uuid4().hex[:8]}",
        code=f"SC{uuid4().hex[:8].upper()}",
    )
    return owner_username, owner_token, organization


def _create_workflow_definition(client, owner_token, approver_id: str):
    response = client.post(
        "/api/v1/workflows/definitions",
        json={
            "name": f"Security Flow {uuid4().hex[:8]}",
            "workflow_type": "resource_application",
            "steps": [
                {
                    "name": "review",
                    "approver_ids": [approver_id],
                    "parallel_approval": True,
                    "reminder_after_hours": 24,
                }
            ],
            "reminders_enabled": True,
        },
        headers=auth_headers(owner_token),
    )
    assert response.status_code == 201
    return response.json()["id"]


def _start_workflow(client, owner_token, definition_id: str, business_number: str, payload: dict, idempotency_key: str):
    response = client.post(
        "/api/v1/workflows/instances/start",
        json={
            "process_definition_id": definition_id,
            "business_number": business_number,
            "payload": payload,
        },
        headers={**auth_headers(owner_token), "Idempotency-Key": idempotency_key},
    )
    return response


def test_https_enforcement_allows_forwarded_https_and_rejects_plain_http(client):
    assert client.get("/health").status_code == 400
    assert client.get("/health", headers={"x-forwarded-proto": "https"}).status_code == 401


def test_password_reset_requests_are_generic(client, monkeypatch):
    username = f"reset-security-{uuid4().hex[:8]}"
    register_and_login(client, username, "Strong123")
    monkeypatch.setattr("app.services.auth_service.AuthService.EXPOSE_RESET_TOKEN", False)

    existing = client.post(
        "/api/v1/auth/password-recovery/request",
        json={"username": username},
        headers={"x-forwarded-proto": "https"},
    )
    missing = client.post(
        "/api/v1/auth/password-recovery/request",
        json={"username": f"missing-{uuid4().hex[:8]}"},
        headers={"x-forwarded-proto": "https"},
    )

    assert existing.status_code == 200
    assert missing.status_code == 200
    assert existing.json() == missing.json()
    assert existing.json()["message"] == "If the account exists, a reset link has been sent"


def test_audit_logs_cover_register_org_create_and_password_reset(client, db_session):
    owner_username = f"audit-owner-{uuid4().hex[:8]}"
    owner_token = register_and_login(client, owner_username, "Strong123")
    organization = create_organization_via_api(
        client,
        owner_token,
        name=f"Audit Org {uuid4().hex[:8]}",
        code=f"AU{uuid4().hex[:8].upper()}",
    )

    reset_response = client.post(
        "/api/v1/auth/password-recovery/request",
        json={"username": owner_username},
        headers={"x-forwarded-proto": "https"},
    )
    assert reset_response.status_code == 200

    logs = db_session.query(AuditLog).filter(AuditLog.action.in_(
        ["auth.register", "organization.created", "auth.password_reset.requested"]
    )).all()
    actions = {log.action for log in logs}
    assert {"auth.register", "organization.created", "auth.password_reset.requested"} <= actions

    register_log = next(log for log in logs if log.action == "auth.register")
    assert register_log.user_id is not None
    assert register_log.organization_id is None

    org_log = next(log for log in logs if log.action == "organization.created")
    assert str(org_log.organization_id) == organization["id"]
    assert org_log.user_id is not None

    reset_log = next(log for log in logs if log.action == "auth.password_reset.requested")
    assert str(reset_log.organization_id) == organization["id"]
    assert reset_log.user_id is not None
    assert "password" not in str(reset_log.details).lower()


def test_import_errors_store_encrypted_payload_not_plaintext(client, db_session):
    owner_username, owner_token, organization = _create_org_with_owner(client)
    owner = get_user(db_session, owner_username)
    assert owner is not None

    import_response = client.post(
        "/api/v1/data/import",
        json={
            "entity_type": "patients",
            "rows": [
                {"patient_id": "patient-1", "email": "alice@example.com"},
                {"patient_id": "patient-1", "email": "alice@example.com"},
            ],
            "reject_invalid_records": False,
            "source_system": "security-test",
            "rules": [],
            "persist_rules": False,
        },
        headers=auth_headers(owner_token),
    )
    assert import_response.status_code == 201
    batch_id = import_response.json()["id"]

    error = db_session.query(DataImportError).filter(DataImportError.batch_id == UUID(batch_id)).one()
    assert error.row_data_raw_encrypted is not None
    assert "alice@example.com" not in error.row_data_raw_encrypted
    decrypted = decrypt_string(error.row_data_raw_encrypted)
    assert decrypted is not None
    assert "alice@example.com" in decrypted


def test_idempotency_key_reuse_with_different_payload_returns_409(client, db_session):
    owner_username, owner_token, organization = _create_org_with_owner(client)
    owner = get_user(db_session, owner_username)
    definition_id = _create_workflow_definition(client, owner_token, approver_id=str(owner.id))

    first_response = _start_workflow(
        client,
        owner_token,
        definition_id,
        business_number=f"BN-{uuid4().hex[:12]}",
        payload={"source": "first"},
        idempotency_key="same-key-different-payload",
    )
    assert first_response.status_code == 201

    second_response = _start_workflow(
        client,
        owner_token,
        definition_id,
        business_number=f"BN-{uuid4().hex[:12]}",
        payload={"source": "second"},
        idempotency_key="same-key-different-payload",
    )
    assert second_response.status_code == 409


def test_large_attachment_is_rejected(client, db_session):
    owner_username, owner_token, organization = _create_org_with_owner(client)
    owner = get_user(db_session, owner_username)
    definition_id = _create_workflow_definition(client, owner_token, approver_id=str(owner.id))
    start_response = _start_workflow(
        client,
        owner_token,
        definition_id,
        business_number=f"BN-{uuid4().hex[:12]}",
        payload={"source": "attachment"},
        idempotency_key="attachment-size-key",
    )
    assert start_response.status_code == 201
    process_instance_id = start_response.json()["id"]

    large_file = b"x" * (20 * 1024 * 1024 + 1)
    upload_response = client.post(
        f"/api/v1/workflows/instances/{process_instance_id}/attachments",
        files={"file": ("large.txt", large_file, "text/plain")},
        headers=auth_headers(owner_token),
    )
    assert upload_response.status_code == 413
