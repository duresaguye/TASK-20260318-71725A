from types import SimpleNamespace
from uuid import uuid4

from conftest import (
    assign_user_role_via_api,
    auth_headers,
    create_organization_via_api,
    get_user,
    register_and_login,
    update_user_membership,
)


def _bootstrap_governance_org(client, db_session):
    admin_username = f"governance-admin-{uuid4().hex[:8]}"
    member_username = f"governance-member-{uuid4().hex[:8]}"

    admin_token = register_and_login(client, admin_username, "Strong123")
    organization = create_organization_via_api(
        client,
        admin_token,
        name=f"Governance Org {uuid4().hex[:8]}",
        code=f"GV{uuid4().hex[:8].upper()}",
    )

    member_token = register_and_login(client, member_username, "Strong123")
    member = update_user_membership(
        db_session,
        member_username,
        organization_id=organization["id"],
    )
    assign_user_role_via_api(
        client,
        admin_token,
        org_id=organization["id"],
        user_id=str(member.id),
        role="general_user",
    )

    return SimpleNamespace(
        admin_username=admin_username,
        member_username=member_username,
        admin_token=admin_token,
        member_token=member_token,
        admin=get_user(db_session, admin_username),
        member=member,
        organization=organization,
    )


def test_data_import_validation_stores_errors_and_blocks_non_privileged_users(client, db_session):
    setup = _bootstrap_governance_org(client, db_session)

    import_response = client.post(
        "/api/v1/data/import",
        json={
            "entity_type": "patients",
            "rows": [
                {"patient_id": "p-1", "age": 34},
                {"patient_id": "p-1", "age": 34},
            ],
            "reject_invalid_records": False,
            "source_system": "integration-test",
            "rules": [],
            "persist_rules": False,
        },
        headers=auth_headers(setup.admin_token),
    )
    assert import_response.status_code == 201
    batch = import_response.json()
    assert batch["status"] == "completed_with_errors"
    assert batch["invalid_rows"] == 1
    assert batch["accepted_rows"] == 2
    assert batch["metadata_json"]["error_count"] == 1
    assert len(batch["metadata_json"]["errors"]) == 1
    assert batch["metadata_json"]["errors"][0]["validation_type"] == "duplicate"

    forbidden_response = client.get(
        f"/api/v1/data/import/{batch['id']}/errors",
        headers=auth_headers(setup.member_token),
    )
    assert forbidden_response.status_code == 403

    errors_response = client.get(
        f"/api/v1/data/import/{batch['id']}/errors",
        headers=auth_headers(setup.admin_token),
    )
    assert errors_response.status_code == 200
    errors = errors_response.json()["errors"]
    assert len(errors) == 1
    assert errors[0]["validation_type"] == "duplicate"


def test_data_governance_rollback_and_lineage_history(client, db_session):
    setup = _bootstrap_governance_org(client, db_session)

    wrong_login = client.post(
        "/api/v1/auth/login",
        json={"username": setup.member_username, "password": "Wrong123"},
        headers={"x-forwarded-proto": "https"},
    )
    assert wrong_login.status_code == 401

    correct_login = client.post(
        "/api/v1/auth/login",
        json={"username": setup.member_username, "password": "Strong123"},
        headers={"x-forwarded-proto": "https"},
    )
    assert correct_login.status_code == 200
    member_token = correct_login.json()["access_token"]

    forbidden_rollback = client.post(
        f"/api/v1/data/user/{setup.member.id}/rollback",
        headers=auth_headers(member_token),
    )
    assert forbidden_rollback.status_code == 403

    rollback_response = client.post(
        f"/api/v1/data/user/{setup.member.id}/rollback",
        headers=auth_headers(setup.admin_token),
    )
    assert rollback_response.status_code == 200
    rollback = rollback_response.json()
    assert rollback["status"] == "rolled_back"

    lineage_response = client.get(
        f"/api/v1/data/lineage/user/{setup.member.id}",
        headers=auth_headers(setup.admin_token),
    )
    assert lineage_response.status_code == 200
    lineage = lineage_response.json()
    assert len(lineage) >= 2
    assert any(entry["transformation_step"] == "rollback_restore" for entry in lineage)
