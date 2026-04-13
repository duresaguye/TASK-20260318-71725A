from types import SimpleNamespace
import time
from uuid import UUID, uuid4

from app.models.process_instance import ProcessInstance
from conftest import (
    assign_user_role_via_api,
    auth_headers,
    create_organization_via_api,
    get_user,
    register_and_login,
    update_user_membership,
)


def _bootstrap_export_org(client, db_session):
    owner_username = f"export-owner-{uuid4().hex[:8]}"
    auditor_username = f"export-auditor-{uuid4().hex[:8]}"
    outsider_username = f"export-user-{uuid4().hex[:8]}"

    owner_token = register_and_login(client, owner_username, "Strong123")
    organization = create_organization_via_api(
        client,
        owner_token,
        name=f"Export Org {uuid4().hex[:8]}",
        code=f"EX{uuid4().hex[:8].upper()}",
    )

    auditor_token = register_and_login(client, auditor_username, "Strong123")
    outsider_token = register_and_login(client, outsider_username, "Strong123")

    auditor = update_user_membership(
        db_session,
        auditor_username,
        organization_id=organization["id"],
    )
    outsider = update_user_membership(
        db_session,
        outsider_username,
        organization_id=organization["id"],
    )
    assign_user_role_via_api(
        client,
        owner_token,
        org_id=organization["id"],
        user_id=str(auditor.id),
        role="auditor",
    )
    assign_user_role_via_api(
        client,
        owner_token,
        org_id=organization["id"],
        user_id=str(outsider.id),
        role="general_user",
    )

    return SimpleNamespace(
        owner_username=owner_username,
        auditor_username=auditor_username,
        outsider_username=outsider_username,
        owner_token=owner_token,
        auditor_token=auditor_token,
        outsider_token=outsider_token,
        owner=get_user(db_session, owner_username),
        auditor=auditor,
        outsider=outsider,
        organization=organization,
    )


def test_export_access_enforces_owner_auditor_and_self_scopes(client, db_session):
    setup = _bootstrap_export_org(client, db_session)

    definition_response = client.post(
        "/api/v1/workflows/definitions",
        json={
            "name": f"Export Workflow {uuid4().hex[:8]}",
            "workflow_type": "resource_application",
            "steps": [
                {
                    "name": "owner-review",
                    "approver_ids": [str(setup.owner.id)],
                    "parallel_approval": True,
                    "reminder_after_hours": 24,
                }
            ],
            "reminders_enabled": True,
        },
        headers=auth_headers(setup.owner_token),
    )
    assert definition_response.status_code == 201

    instance_response = client.post(
        "/api/v1/workflows/instances/start",
        json={
            "process_definition_id": definition_response.json()["id"],
            "business_number": f"BN-{uuid4().hex[:12]}",
            "payload": {"source": "export-test"},
        },
        headers={**auth_headers(setup.owner_token), "Idempotency-Key": "export-workflow-key"},
    )
    assert instance_response.status_code == 201
    assert db_session.get(ProcessInstance, UUID(instance_response.json()["id"])) is not None

    owner_export_response = client.post(
        "/api/v1/exports",
        json={"export_type": "workflows", "file_format": "json"},
        headers=auth_headers(setup.owner_token),
    )
    assert owner_export_response.status_code == 201
    owner_export = owner_export_response.json()
    assert owner_export["status"] in {"pending", "processing", "completed"}

    owner_export_id = owner_export["id"]
    for _ in range(10):
        owner_export_get = client.get(
            f"/api/v1/exports/{owner_export_id}",
            headers=auth_headers(setup.owner_token),
        )
        assert owner_export_get.status_code == 200
        if owner_export_get.json()["status"] == "completed":
            owner_export = owner_export_get.json()
            break
        time.sleep(0.05)
    assert owner_export["status"] == "completed"

    owner_get_response = client.get(
        f"/api/v1/exports/{owner_export_id}",
        headers=auth_headers(setup.owner_token),
    )
    assert owner_get_response.status_code == 200
    assert owner_get_response.json()["id"] == owner_export_id

    owner_download_response = client.get(
        f"/api/v1/exports/{owner_export_id}/download",
        headers=auth_headers(setup.owner_token),
    )
    assert owner_download_response.status_code == 200
    assert "attachment; filename=" in owner_download_response.headers["content-disposition"]

    auditor_get_response = client.get(
        f"/api/v1/exports/{owner_export_id}",
        headers=auth_headers(setup.auditor_token),
    )
    assert auditor_get_response.status_code == 200

    auditor_download_response = client.get(
        f"/api/v1/exports/{owner_export_id}/download",
        headers=auth_headers(setup.auditor_token),
    )
    assert auditor_download_response.status_code == 200

    forbidden_get_response = client.get(
        f"/api/v1/exports/{owner_export_id}",
        headers=auth_headers(setup.outsider_token),
    )
    assert forbidden_get_response.status_code == 403

    outsider_export_response = client.post(
        "/api/v1/exports",
        json={"export_type": "analytics", "file_format": "json"},
        headers=auth_headers(setup.outsider_token),
    )
    assert outsider_export_response.status_code == 201
    outsider_export = outsider_export_response.json()

    own_get_response = client.get(
        f"/api/v1/exports/{outsider_export['id']}",
        headers=auth_headers(setup.outsider_token),
    )
    assert own_get_response.status_code == 200

    own_download_response = client.get(
        f"/api/v1/exports/{outsider_export['id']}/download",
        headers=auth_headers(setup.outsider_token),
    )
    assert own_download_response.status_code == 200
