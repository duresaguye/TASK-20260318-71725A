from uuid import UUID, uuid4

from conftest import (
    assign_user_role_via_api,
    auth_headers,
    create_organization_via_api,
    get_user,
    register_and_login,
)


def test_create_organization_success(client, db_session):
    username = f"bootstrap-owner-{uuid4().hex[:8]}"
    token = register_and_login(client, username, "Strong123")

    organization = create_organization_via_api(
        client,
        token,
        name=f"Bootstrap Org {uuid4().hex[:8]}",
        code=f"BO{uuid4().hex[:8].upper()}",
    )

    owner = get_user(db_session, username)
    assert organization["name"].startswith("Bootstrap Org")
    assert owner.organization_id == UUID(organization["id"])
    assert owner.role == "administrator"


def test_join_organization_success(client, db_session):
    owner_username = f"join-owner-{uuid4().hex[:8]}"
    member_username = f"join-member-{uuid4().hex[:8]}"

    owner_token = register_and_login(client, owner_username, "Strong123")
    organization = create_organization_via_api(
        client,
        owner_token,
        name=f"Join Org {uuid4().hex[:8]}",
        code=f"JO{uuid4().hex[:8].upper()}",
    )

    member_token = register_and_login(client, member_username, "Strong123")
    join_response = client.post(
        f"/api/v1/organizations/{organization['id']}/join",
        json={"role": "general_user"},
        headers=auth_headers(member_token),
    )
    assert join_response.status_code == 200

    joined_user = join_response.json()
    assert joined_user["organization_id"] == organization["id"]
    assert joined_user["role"] == "general_user"
    assert get_user(db_session, member_username).organization_id == UUID(organization["id"])


def test_assign_organization_role_via_api(client, db_session):
    owner_username = f"role-owner-{uuid4().hex[:8]}"
    member_username = f"role-member-{uuid4().hex[:8]}"

    owner_token = register_and_login(client, owner_username, "Strong123")
    organization = create_organization_via_api(
        client,
        owner_token,
        name=f"Role Org {uuid4().hex[:8]}",
        code=f"RO{uuid4().hex[:8].upper()}",
    )

    member_token = register_and_login(client, member_username, "Strong123")
    join_response = client.post(
        f"/api/v1/organizations/{organization['id']}/join",
        json={"role": "general_user"},
        headers=auth_headers(member_token),
    )
    assert join_response.status_code == 200

    member = get_user(db_session, member_username)
    role_response = assign_user_role_via_api(
        client,
        owner_token,
        org_id=organization["id"],
        user_id=str(member.id),
        role="reviewer",
    )
    assert role_response["role"] == "reviewer"
    assert get_user(db_session, member_username).role == "reviewer"


def test_user_can_use_system_after_join(client):
    owner_username = f"system-owner-{uuid4().hex[:8]}"
    member_username = f"system-member-{uuid4().hex[:8]}"

    owner_token = register_and_login(client, owner_username, "Strong123")
    organization = create_organization_via_api(
        client,
        owner_token,
        name=f"System Org {uuid4().hex[:8]}",
        code=f"SY{uuid4().hex[:8].upper()}",
    )

    member_token = register_and_login(client, member_username, "Strong123")
    join_response = client.post(
        f"/api/v1/organizations/{organization['id']}/join",
        json={"role": "general_user"},
        headers=auth_headers(member_token),
    )
    assert join_response.status_code == 200

    usage_response = client.get("/api/v1/workflows/tasks/my", headers=auth_headers(member_token))
    assert usage_response.status_code == 200
    assert usage_response.json() == []


def test_cross_org_isolation(client, db_session):
    owner_one_username = f"isolated-owner-one-{uuid4().hex[:8]}"
    owner_two_username = f"isolated-owner-two-{uuid4().hex[:8]}"

    owner_one_token = register_and_login(client, owner_one_username, "Strong123")
    create_organization_via_api(
        client,
        owner_one_token,
        name=f"Isolation Org A {uuid4().hex[:8]}",
        code=f"IA{uuid4().hex[:8].upper()}",
    )

    owner_two_token = register_and_login(client, owner_two_username, "Strong123")
    create_organization_via_api(
        client,
        owner_two_token,
        name=f"Isolation Org B {uuid4().hex[:8]}",
        code=f"IB{uuid4().hex[:8].upper()}",
    )

    definition_response = client.post(
        "/api/v1/workflows/definitions",
        json={
            "name": f"Isolation Flow {uuid4().hex[:8]}",
            "workflow_type": "resource_application",
            "steps": [
                {
                    "name": "review",
                    "approver_ids": [str(get_user(db_session, owner_one_username).id)],
                    "parallel_approval": True,
                    "reminder_after_hours": 24,
                }
            ],
            "reminders_enabled": True,
        },
        headers=auth_headers(owner_one_token),
    )
    assert definition_response.status_code == 201

    start_response = client.post(
        "/api/v1/workflows/instances/start",
        json={
            "process_definition_id": definition_response.json()["id"],
            "business_number": f"BN-{uuid4().hex[:12]}",
            "payload": {"origin": "bootstrap-test"},
        },
        headers=auth_headers(owner_one_token),
    )
    assert start_response.status_code == 201
    process_instance_id = start_response.json()["id"]

    cross_org_response = client.get(
        f"/api/v1/workflows/instances/{process_instance_id}/tasks",
        headers=auth_headers(owner_two_token),
    )
    assert cross_org_response.status_code == 404
