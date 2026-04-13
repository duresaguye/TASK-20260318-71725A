from types import SimpleNamespace
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


def _bootstrap_workflow_org(client, db_session):
    owner_username = f"owner-{uuid4().hex[:8]}"
    reviewer_username = f"reviewer-{uuid4().hex[:8]}"
    outsider_username = f"outsider-{uuid4().hex[:8]}"

    owner_token = register_and_login(client, owner_username, "Strong123")
    organization = create_organization_via_api(
        client,
        owner_token,
        name=f"Workflow Org {uuid4().hex[:8]}",
        code=f"WF{uuid4().hex[:8].upper()}",
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


def test_workflow_idempotent_start_and_reviewer_approval_completes_instance(client, db_session):
    setup = _bootstrap_workflow_org(client, db_session)

    definition_response = client.post(
        "/api/v1/workflows/definitions",
        json={
            "name": f"Review Flow {uuid4().hex[:8]}",
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
    process_definition_id = definition_response.json()["id"]

    start_payload = {
        "process_definition_id": process_definition_id,
        "business_number": f"BN-{uuid4().hex[:12]}",
        "payload": {"source": "api-test"},
    }

    first_start = client.post(
        "/api/v1/workflows/instances/start",
        json=start_payload,
        headers={**auth_headers(setup.owner_token), "Idempotency-Key": "workflow-idempotency-key"},
    )
    assert first_start.status_code == 201
    first_instance = first_start.json()
    first_instance_id = UUID(first_instance["id"])

    second_start = client.post(
        "/api/v1/workflows/instances/start",
        json=start_payload,
        headers={**auth_headers(setup.owner_token), "Idempotency-Key": "workflow-idempotency-key"},
    )
    assert second_start.status_code == 201
    assert second_start.json()["id"] == first_instance["id"]

    reviewer_tasks_response = client.get("/api/v1/workflows/tasks/my", headers=auth_headers(setup.reviewer_token))
    assert reviewer_tasks_response.status_code == 200
    reviewer_tasks = reviewer_tasks_response.json()
    assert any(task["assigned_user_id"] == str(setup.reviewer.id) for task in reviewer_tasks)

    review_task_id = next(
        task["id"]
        for task in reviewer_tasks
        if task["process_instance_id"] == str(first_instance_id)
    )

    approve_response = client.post(
        f"/api/v1/workflows/tasks/{review_task_id}/approve",
        json={"comment": "Approved"},
        headers=auth_headers(setup.reviewer_token),
    )
    assert approve_response.status_code == 200
    assert approve_response.json()["status"] == "completed"
    assert db_session.get(ProcessInstance, first_instance_id).status == "completed"

    rejection_definition_response = client.post(
        "/api/v1/workflows/definitions",
        json={
            "name": f"Reject Flow {uuid4().hex[:8]}",
            "workflow_type": "credit_change",
            "steps": [
                {
                    "name": "reject-review",
                    "approver_ids": [str(setup.reviewer.id)],
                    "parallel_approval": True,
                    "reminder_after_hours": 24,
                }
            ],
            "reminders_enabled": True,
        },
        headers=auth_headers(setup.owner_token),
    )
    assert rejection_definition_response.status_code == 201

    reject_instance_response = client.post(
        "/api/v1/workflows/instances/start",
        json={
            "process_definition_id": rejection_definition_response.json()["id"],
            "business_number": f"BN-{uuid4().hex[:12]}",
            "payload": {"source": "api-test"},
        },
        headers={**auth_headers(setup.owner_token), "Idempotency-Key": "workflow-reject-key"},
    )
    assert reject_instance_response.status_code == 201
    reject_instance = reject_instance_response.json()
    reject_instance_id = UUID(reject_instance["id"])

    reject_tasks = client.get("/api/v1/workflows/tasks/my", headers=auth_headers(setup.reviewer_token))
    assert reject_tasks.status_code == 200
    reject_task_id = next(
        task["id"]
        for task in reject_tasks.json()
        if task["process_instance_id"] == str(reject_instance_id)
    )

    reject_response = client.post(
        f"/api/v1/workflows/tasks/{reject_task_id}/reject",
        json={"comment": "Rejected"},
        headers=auth_headers(setup.reviewer_token),
    )
    assert reject_response.status_code == 200
    assert reject_response.json()["status"] == "rejected"
    rejected_instance = db_session.get(ProcessInstance, reject_instance_id)
    assert rejected_instance is not None
    assert rejected_instance.status == "rejected"
    assert rejected_instance.payload["workflow_result"]["status"] == "rejected"
    assert rejected_instance.payload["workflow_result"]["comment"] == "Rejected"

    forbidden_response = client.post(
        f"/api/v1/workflows/tasks/{reject_task_id}/approve",
        json={"comment": "No access"},
        headers=auth_headers(setup.outsider_token),
    )
    assert forbidden_response.status_code == 403


def test_workflow_reviewer_can_be_assigned_through_tasks_and_outsider_is_blocked(client, db_session):
    setup = _bootstrap_workflow_org(client, db_session)

    definition_response = client.post(
        "/api/v1/workflows/definitions",
        json={
            "name": f"Assignment Flow {uuid4().hex[:8]}",
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

    start_response = client.post(
        "/api/v1/workflows/instances/start",
        json={
            "process_definition_id": definition_response.json()["id"],
            "business_number": f"BN-{uuid4().hex[:12]}",
            "payload": {"source": "api-test"},
        },
        headers={**auth_headers(setup.owner_token), "Idempotency-Key": "workflow-assignment-key"},
    )
    assert start_response.status_code == 201
    process_instance_id = UUID(start_response.json()["id"])

    reviewer_tasks = client.get("/api/v1/workflows/tasks/my", headers=auth_headers(setup.reviewer_token))
    assert reviewer_tasks.status_code == 200
    assert any(task["process_instance_id"] == str(process_instance_id) for task in reviewer_tasks.json())

    outsider_attempt = client.post(
        f"/api/v1/workflows/tasks/{next(task['id'] for task in reviewer_tasks.json() if task['process_instance_id'] == str(process_instance_id))}/approve",
        json={"comment": "Blocked"},
        headers=auth_headers(setup.outsider_token),
    )
    assert outsider_attempt.status_code == 403
