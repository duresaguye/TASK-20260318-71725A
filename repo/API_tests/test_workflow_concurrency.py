from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.db.deps import get_db
from app.db.session import SessionLocal
from app.main import app
from app.models.organization import Organization
from app.models.process_instance import ProcessInstance
from app.models.user import User
from conftest import auth_headers, create_organization_via_api, register_and_login


def _per_request_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _cleanup_workflow_concurrency_data(organization_id: UUID, usernames: list[str]) -> None:
    cleanup_db = SessionLocal()
    try:
        cleanup_db.execute(delete(User).where(User.username.in_(usernames)))
        cleanup_db.execute(delete(Organization).where(Organization.id == organization_id))
        cleanup_db.commit()
    finally:
        cleanup_db.close()


def test_workflow_concurrency_is_idempotent_under_parallel_submissions():
    organization = {"id": None}
    owner_username = None
    app.dependency_overrides[get_db] = _per_request_db
    try:
        with TestClient(app) as local_client:
            owner_username = f"concurrency-owner-{uuid4().hex[:8]}"
            owner_token = register_and_login(local_client, owner_username, "Strong123")
            organization = create_organization_via_api(
                local_client,
                owner_token,
                name=f"Concurrency Org {uuid4().hex[:8]}",
                code=f"CN{uuid4().hex[:8].upper()}",
            )

            owner_session = SessionLocal()
            try:
                owner_id = owner_session.scalar(select(User.id).where(User.username == owner_username))
                assert owner_id is not None
            finally:
                owner_session.close()

            definition_response = local_client.post(
                "/api/v1/workflows/definitions",
                json={
                    "name": f"Concurrency Flow {uuid4().hex[:8]}",
                    "workflow_type": "resource_application",
                    "steps": [
                        {
                            "name": "review",
                            "approver_ids": [str(owner_id)],
                            "parallel_approval": True,
                            "reminder_after_hours": 24,
                        }
                    ],
                    "reminders_enabled": True,
                },
                headers=auth_headers(owner_token),
            )
            assert definition_response.status_code == 201
            process_definition_id = definition_response.json()["id"]

            business_number = f"BN-{uuid4().hex[:12]}"
            start_payload = {
                "process_definition_id": process_definition_id,
                "business_number": business_number,
                "payload": {"source": "concurrency-test"},
            }

            def submit_workflow():
                return local_client.post(
                    "/api/v1/workflows/instances/start",
                    json=start_payload,
                    headers=auth_headers(owner_token),
                )

            with ThreadPoolExecutor(max_workers=8) as executor:
                responses = list(executor.map(lambda _: submit_workflow(), range(8)))

            assert all(response.status_code == 201 for response in responses)
            instance_ids = {response.json()["id"] for response in responses}
            assert len(instance_ids) == 1

            verification_db = SessionLocal()
            try:
                row_count = verification_db.scalar(
                    select(func.count(ProcessInstance.id)).where(
                        ProcessInstance.organization_id == UUID(organization["id"]),
                        ProcessInstance.business_number == business_number,
                    )
                )
                assert row_count == 1
            finally:
                verification_db.close()
    finally:
        app.dependency_overrides.clear()
        if organization.get("id") is not None and owner_username is not None:
            _cleanup_workflow_concurrency_data(
                organization_id=UUID(organization["id"]),
                usernames=[owner_username],
            )
