from types import SimpleNamespace
from uuid import UUID, uuid4

from sqlalchemy import select

from app.models.workflow_task import WorkflowTask
from conftest import (
    assign_user_role_via_api,
    auth_headers,
    create_organization_via_api,
    get_user,
    register_and_login,
    update_user_membership,
)


def _bootstrap_attachment_org(client, db_session):
    owner_username = f"attachment-owner-{uuid4().hex[:8]}"
    reviewer_username = f"attachment-reviewer-{uuid4().hex[:8]}"
    outsider_username = f"attachment-outsider-{uuid4().hex[:8]}"

    owner_token = register_and_login(client, owner_username, "Strong123")
    organization = create_organization_via_api(
        client,
        owner_token,
        name=f"Attachment Org {uuid4().hex[:8]}",
        code=f"AT{uuid4().hex[:8].upper()}",
    )

    reviewer_token = register_and_login(client, reviewer_username, "Strong123")
    outsider_token = register_and_login(client, outsider_username, "Strong123")

    reviewer = update_user_membership(
        db_session,
        reviewer_username,
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
        user_id=str(reviewer.id),
        role="reviewer",
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
        reviewer_username=reviewer_username,
        outsider_username=outsider_username,
        owner_token=owner_token,
        reviewer_token=reviewer_token,
        outsider_token=outsider_token,
        owner=get_user(db_session, owner_username),
        reviewer=reviewer,
        outsider=outsider,
        organization=organization,
    )


def test_attachment_access_allows_assigned_reviewer_and_blocks_outsider(client, db_session):
    setup = _bootstrap_attachment_org(client, db_session)

    definition_response = client.post(
        "/api/v1/workflows/definitions",
        json={
            "name": f"Attachment Workflow {uuid4().hex[:8]}",
            "workflow_type": "resource_application",
            "steps": [
                {
                    "name": "review",
                    "approver_ids": [str(setup.reviewer.id)],
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
            "payload": {"source": "attachment-test"},
        },
        headers={**auth_headers(setup.owner_token), "Idempotency-Key": "attachment-workflow-key"},
    )
    assert instance_response.status_code == 201
    process_instance_id = UUID(instance_response.json()["id"])

    task = db_session.scalar(
        select(WorkflowTask).where(
            WorkflowTask.process_instance_id == process_instance_id,
            WorkflowTask.organization_id == setup.owner.organization_id,
            WorkflowTask.assigned_user_id == setup.reviewer.id,
        )
    )
    assert task is not None

    upload_response = client.post(
        f"/api/v1/workflows/instances/{process_instance_id}/attachments",
        params={"workflow_task_id": str(task.id)},
        files={"file": ("report.txt", b"reviewable attachment", "text/plain")},
        headers=auth_headers(setup.owner_token),
    )
    assert upload_response.status_code == 201
    attachment = upload_response.json()
    assert attachment["workflow_task_id"] == str(task.id)

    reviewer_list_response = client.get(
        f"/api/v1/workflows/instances/{process_instance_id}/attachments",
        headers=auth_headers(setup.reviewer_token),
    )
    assert reviewer_list_response.status_code == 200
    assert reviewer_list_response.json()[0]["id"] == attachment["id"]

    reviewer_download_response = client.get(
        f"/api/v1/workflows/instances/{process_instance_id}/attachments/{attachment['id']}/download",
        headers=auth_headers(setup.reviewer_token),
    )
    assert reviewer_download_response.status_code == 200
    assert reviewer_download_response.content == b"reviewable attachment"

    outsider_response = client.get(
        f"/api/v1/workflows/instances/{process_instance_id}/attachments",
        headers=auth_headers(setup.outsider_token),
    )
    assert outsider_response.status_code == 403
