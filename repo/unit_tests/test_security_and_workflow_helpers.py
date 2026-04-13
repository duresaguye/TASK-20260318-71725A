import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.deps.auth import get_current_user
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
)
from app.core.encryption import encrypt_string
from app.services.auth_service import AuthService
from app.services.attachment_service import AttachmentService
from app.core.access_policy import AccessPolicy, ResourceContext
from app.services.audit_service import AuditService
from app.services.analytics_service import AnalyticsService
from app.models.attachment_metadata import AttachmentMetadata
from app.schemas.data_governance import DataImportErrorOut
from app.schemas.workflow import ProcessDefinitionCreate, ProcessStepCreate
from app.services.data_governance_service import DataGovernanceService
from app.services.export_service import ExportService
from app.services.file_security_service import FileSecurityService
from app.services.response_security_service import ResponseSecurityService
from app.services.security_hardening_service import SecurityHardeningService
from app.services.workflow_service import WorkflowService


def test_access_token_round_trip_contains_jti_and_type():
    token = create_access_token({"sub": "user-1"})
    payload = decode_access_token(token)
    assert payload["sub"] == "user-1"
    assert payload["token_type"] == "access"
    assert "jti" in payload


def test_login_flow_returns_bearer_token():
    user = SimpleNamespace(
        id=uuid4(),
        username="alice",
        hashed_password=hash_password("Strong123"),
        organization_id=uuid4(),
        role="general_user",
        is_active=True,
        failed_login_attempts=0,
        last_failed_login_at=None,
        locked_until=None,
    )

    class _FakeDb:
        def scalar(self, _query):
            return user

        def commit(self):
            return None

        def add(self, _obj):
            return None

        def flush(self):
            return None

        def refresh(self, _obj):
            return None

    token = AuthService.login_user(db=_FakeDb(), login_data=SimpleNamespace(username="alice", password="Strong123"))
    payload = decode_access_token(token.access_token)
    assert payload["user_id"] == str(user.id)
    assert payload["token_type"] == "access"


def test_login_success_is_audited_for_orgless_users(monkeypatch):
    user = SimpleNamespace(
        id=uuid4(),
        username="alice",
        hashed_password=hash_password("Strong123"),
        organization_id=None,
        role="general_user",
        is_active=True,
        failed_login_attempts=0,
        last_failed_login_at=None,
        locked_until=None,
        token_version=0,
    )
    audit_calls = []

    class _FakeDb:
        def scalar(self, _query):
            return user

        def commit(self):
            return None

        def add(self, _obj):
            return None

        def flush(self):
            return None

        def refresh(self, _obj):
            return None

    monkeypatch.setattr("app.services.auth_service.AuditService.log_event", lambda **kwargs: audit_calls.append(kwargs))

    token = AuthService.login_user(db=_FakeDb(), login_data=SimpleNamespace(username="alice", password="Strong123"))
    assert decode_access_token(token.access_token)["sub"] == str(user.id)
    assert any(call["action"] == "auth.login.succeeded" for call in audit_calls)
    assert any(call["organization_id"] is None for call in audit_calls)


def test_expired_access_token_is_rejected(monkeypatch):
    monkeypatch.setattr("app.core.security.ACCESS_TOKEN_EXPIRE_MINUTES", -1)
    token = create_access_token({"sub": "user-1"})
    with pytest.raises(ValueError):
        decode_access_token(token)


def test_revoked_token_is_rejected(monkeypatch):
    token = create_access_token({"sub": str(uuid4()), "user_id": str(uuid4())})
    user = SimpleNamespace(id=uuid4(), organization_id=uuid4(), is_active=True, role="general_user")

    class _FakeDb:
        def get(self, _model, _key):
            return user

    monkeypatch.setattr(
        "app.api.deps.auth.SecurityHardeningService.is_token_revoked",
        lambda **_: True,
    )

    with pytest.raises(Exception):
        get_current_user(token=token, db=_FakeDb())


def test_audit_service_sanitizes_sensitive_fields():
    sanitized = AuditService._sanitize_details(
        {"comment": "secret", "filters": {"email": "demo@example.com"}, "safe": "value"}
    )
    assert sanitized["comment"] == "***redacted***"
    assert sanitized["filters"] == "***redacted***"
    assert sanitized["safe"] == "value"


def test_file_security_rejects_large_or_blocked_files():
    with pytest.raises(Exception):
        FileSecurityService.validate_upload(
            file_name="payload.exe",
            content_type="application/octet-stream",
            content=b"bad",
        )


def test_file_security_returns_sha256_for_allowed_payload():
    fingerprint = FileSecurityService.validate_upload(
        file_name="report.json",
        content_type="application/json",
        content=b'{"ok": true}',
    )
    assert len(fingerprint) == 64


def test_response_security_masks_usernames_and_ids():
    masked_id = ResponseSecurityService.mask_identifier("1234567890abcdef")
    masked_username = ResponseSecurityService.mask_username("clinician")
    assert masked_id.startswith("1234")
    assert masked_username.startswith("c")


def test_response_security_masks_search_payload_for_non_admin():
    masked = ResponseSecurityService.mask_search_data(
        "auditor",
        {
            "id": "1234567890abcdef",
            "username": "clinician",
            "email": "demo@example.com",
            "full_name": "Jane Doe",
            "nested": {"patient_id": "abcdef1234567890"},
        },
    )
    assert masked["id"].startswith("1234")
    assert masked["username"].startswith("c")
    assert masked["email"].endswith("@example.com")
    assert masked["full_name"].startswith("J")
    assert masked["nested"]["patient_id"].startswith("abcd")


def test_analytics_search_decrypts_sensitive_fields_before_masking():
    encrypted_mrn = encrypt_string("MRN-123")
    decrypted = AnalyticsService._decrypt_search_data(
        "patient",
        {
            "medical_record_number": encrypted_mrn,
            "full_name": "Jane Doe",
        },
    )
    assert decrypted["medical_record_number"] == "MRN-123"
    assert decrypted["full_name"] == "Jane Doe"


def test_workflow_condition_resolution_skips_unmatched_steps():
    steps = [
        {"name": "initial", "condition": None},
        {"name": "credit-review", "condition": {"field": "credit_delta", "operator": "gt", "value": 1000}},
        {"name": "final", "condition": None},
    ]
    next_index = WorkflowService._resolve_next_step_index(steps=steps, payload={"credit_delta": 200}, candidate_index=1)
    assert next_index == 2


def test_workflow_condition_matches_equality():
    assert WorkflowService._condition_matches(
        {"field": "department", "operator": "eq", "value": "finance"},
        {"department": "finance"},
    )


def test_workflow_family_mapping_is_explicit():
    assert WorkflowService.WORKFLOW_FAMILY_MAP["resource_application"] == "clinical_operations"
    assert WorkflowService.WORKFLOW_FAMILY_MAP["credit_change"] == "financial_operations"


def test_workflow_definition_rejects_approvers_without_approve_permission(monkeypatch):
    approver = SimpleNamespace(id=uuid4(), username="alice", organization_id=uuid4(), role="general_user", is_active=True)

    class _FakeDb:
        def scalar(self, _query):
            return None

        def scalars(self, _query):
            return SimpleNamespace(all=lambda: [approver])

        def add(self, _obj):
            return None

        def flush(self):
            return None

        def commit(self):
            return None

        def refresh(self, _obj):
            return None

    monkeypatch.setattr("app.services.workflow_service.AccessPolicy.require", lambda **_: None)

    payload = ProcessDefinitionCreate(
        name="approval-flow",
        workflow_type="resource_application",
        steps=[ProcessStepCreate(name="review", approver_ids=[approver.id])],
    )

    with pytest.raises(Exception):
        WorkflowService.create_process_definition(
            db=_FakeDb(),
            current_user=SimpleNamespace(id=uuid4(), organization_id=approver.organization_id, role="administrator"),
            payload=payload,
        )


def test_access_policy_blocks_auditor_writes():
    assert AccessPolicy.allowed("auditor", "workflow", "approve") is False


def test_access_policy_allows_personal_general_user_read():
    AccessPolicy.require_domain(
        role="general_user",
        domain="workflow",
        action="read",
        context=ResourceContext(is_personal=True),
    )


def test_access_policy_blocks_cross_user_general_read():
    with pytest.raises(Exception):
        AccessPolicy.require_domain(
            role="general_user",
            domain="workflow",
            action="read",
            context=ResourceContext(is_personal=False),
        )


def test_analytics_full_access_roles_can_search_hospital_types():
    AnalyticsService._authorize_search_type("administrator", "patient")
    AnalyticsService._authorize_search_type("auditor", "appointment")


def test_analytics_reviewer_can_search_hospital_types():
    AnalyticsService._authorize_search_type("reviewer", "patient")
    AnalyticsService._authorize_search_type("general_user", "appointment")


def test_datetime_masking_passthrough_still_supported():
    now = datetime.now(timezone.utc)
    summary = ResponseSecurityService.mask_identifier(str(now.timestamp()))
    assert summary is not None


def test_idempotency_cleanup_removes_expired_rows_before_reuse():
    class _FakeDeleteQuery:
        def __init__(self, db):
            self.db = db

        def filter(self, *_, **__):
            return self

        def delete(self, synchronize_session=False):
            self.db.delete_called = True
            return 1

    class _FakeDb:
        def __init__(self):
            self.delete_called = False

        def query(self, _model):
            return _FakeDeleteQuery(self)

        def scalar(self, _query):
            return None

        def add(self, _obj):
            return None

        def flush(self):
            return None

    current_user = SimpleNamespace(id=uuid4(), organization_id=uuid4())
    db_db = _FakeDb()

    record, replayed = SecurityHardeningService.get_or_create_idempotency_key(
        db=db_db,
        current_user=current_user,
        scope="workflow.start",
        raw_key="idempotency-key",
        payload={"business_number": "BN-1"},
    )

    assert record is not None
    assert replayed is False
    assert db_db.delete_called is True


def test_idempotency_replays_same_record_for_same_key(monkeypatch):
    class _ExistingRecord:
        resource_id = uuid4()
        request_hash = SecurityHardeningService.build_request_hash({"business_number": "BN-1"})
        expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        status = "completed"

    class _FakeDb:
        def query(self, _model):
            class _Q:
                def filter(self, *_, **__):
                    return self

                def delete(self, synchronize_session=False):
                    return 0

            return _Q()

        def scalar(self, _query):
            return _ExistingRecord()

    current_user = SimpleNamespace(id=uuid4(), organization_id=uuid4())
    record, replayed = SecurityHardeningService.get_or_create_idempotency_key(
        db=_FakeDb(),
        current_user=current_user,
        scope="workflow.start",
        raw_key="idempotency-key",
        payload={"business_number": "BN-1"},
    )

    assert replayed is True
    assert record is not None


def test_start_process_instance_finalizes_idempotency_for_existing_business_number(monkeypatch):
    existing_instance = SimpleNamespace(id=uuid4(), organization_id=uuid4(), business_number="BN-1", started_by_user_id=uuid4())
    idempotency_record = SimpleNamespace(resource_id=None, resource_type=None, response_payload=None, status="pending")
    commit_called = {"value": False}
    finalize_called = {"value": False}

    class _FakeDb:
        def scalar(self, _query):
            return existing_instance

        def commit(self):
            commit_called["value"] = True

    monkeypatch.setattr("app.services.workflow_service.AccessPolicy.require", lambda **_: None)
    monkeypatch.setattr(
        "app.services.workflow_service.SecurityHardeningService.get_or_create_idempotency_key",
        lambda **_: (idempotency_record, False),
    )

    def _finalize(record, *, resource_type, resource_id, response_payload):
        finalize_called["value"] = True
        record.resource_type = resource_type
        record.resource_id = resource_id
        record.response_payload = response_payload
        record.status = "completed"

    monkeypatch.setattr("app.services.workflow_service.SecurityHardeningService.finalize_idempotency_key", _finalize)

    result = WorkflowService.start_process_instance(
        db=_FakeDb(),
        current_user=SimpleNamespace(id=uuid4(), organization_id=existing_instance.organization_id, role="general_user"),
        payload=SimpleNamespace(
            business_number=existing_instance.business_number,
            process_definition_id=uuid4(),
            payload={},
            model_dump=lambda mode="json": {"business_number": existing_instance.business_number},
        ),
        idempotency_key="key-1",
    )

    assert result is existing_instance
    assert finalize_called["value"] is True
    assert commit_called["value"] is True
    assert idempotency_record.resource_id == existing_instance.id
    assert idempotency_record.status == "completed"


def test_workflow_lifecycle_start_approve_complete(monkeypatch):
    org_id = uuid4()
    actor_id = uuid4()
    approver_id = uuid4()
    process_definition_id = uuid4()
    task_id = uuid4()

    process_definition = SimpleNamespace(
        id=process_definition_id,
        organization_id=org_id,
        workflow_type="resource_application",
        steps=[{"name": "review", "approver_ids": [str(approver_id)], "parallel_approval": True, "condition": None, "reminder_after_hours": 24}],
    )
    idempotency_record = SimpleNamespace(resource_id=None, resource_type=None, response_payload=None, status="pending")
    commit_calls = {"count": 0}
    created = {"instance": None}
    task = {"value": None}

    class _StartDb:
        def __init__(self):
            self.calls = 0

        def scalar(self, _query):
            self.calls += 1
            if self.calls == 1:
                return None
            if self.calls == 2:
                return process_definition
            return None

        def add(self, _obj):
            if getattr(_obj, "__class__", None).__name__ == "ProcessInstance":
                created["instance"] = _obj
                _obj.process_definition = process_definition

        def flush(self):
            if created["instance"] is not None and getattr(created["instance"], "id", None) is None:
                created["instance"].id = uuid4()
            return None

        def commit(self):
            commit_calls["count"] += 1

        def refresh(self, _obj):
            return None

    class _ApproveDb:
        def commit(self):
            commit_calls["count"] += 1

        def refresh(self, _obj):
            return None

    monkeypatch.setattr("app.services.workflow_service.AccessPolicy.require", lambda **_: None)
    monkeypatch.setattr("app.services.workflow_service.AccessPolicy.require_domain", lambda **_: None)
    monkeypatch.setattr("app.services.workflow_service.SecurityHardeningService.get_or_create_idempotency_key", lambda **_: (idempotency_record, False))
    monkeypatch.setattr("app.services.workflow_service.SecurityHardeningService.finalize_idempotency_key", lambda *_, **__: setattr(idempotency_record, "status", "completed"))
    def _create_tasks_for_step(**kwargs):
        instance = kwargs["instance"]
        task["value"] = SimpleNamespace(
            id=task_id,
            process_instance_id=instance.id,
            process_instance=instance,
            organization_id=org_id,
            step_index=0,
            step_name="review",
            assigned_user_id=approver_id,
            status="pending",
            decision_comment=None,
            sla_due_at=datetime.now(timezone.utc) + timedelta(hours=48),
            reminder_due_at=None,
            acted_at=None,
        )
        return [task["value"]]

    monkeypatch.setattr("app.services.workflow_service.WorkflowService._create_tasks_for_step", _create_tasks_for_step)
    monkeypatch.setattr("app.services.workflow_service.WorkflowService._get_task_for_user", lambda **_: task["value"])
    monkeypatch.setattr("app.services.workflow_service.DataGovernanceService.create_version_snapshot", lambda **_: None)
    monkeypatch.setattr("app.services.workflow_service.AuditService.log_event", lambda **_: None)

    started = WorkflowService.start_process_instance(
        db=_StartDb(),
        current_user=SimpleNamespace(id=actor_id, organization_id=org_id, role="general_user"),
        payload=SimpleNamespace(
            business_number="BN-1",
            process_definition_id=process_definition_id,
            payload={},
            model_dump=lambda mode="json": {"business_number": "BN-1", "process_definition_id": str(process_definition_id), "payload": {}},
        ),
        idempotency_key="idem-1",
    )
    assert started is created["instance"]
    assert created["instance"] is not None

    monkeypatch.setattr(
        "app.services.workflow_service.WorkflowService._advance_instance_if_step_resolved",
        lambda **kwargs: setattr(created["instance"], "status", "completed") or setattr(created["instance"], "completed_at", datetime.now(timezone.utc)),
    )
    approved = WorkflowService.approve_task(
        db=_ApproveDb(),
        current_user=SimpleNamespace(id=approver_id, organization_id=org_id, role="reviewer"),
        task_id=task_id,
        payload=SimpleNamespace(comment="approved"),
    )

    assert approved.status == "completed"
    assert created["instance"].status == "completed"
    assert created["instance"].completed_at is not None


def test_attachment_storage_uses_safe_server_side_name(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.attachment_service.ATTACHMENT_ROOT", tmp_path)
    fingerprint = "a" * 64
    org_id = uuid4()
    process_instance_id = uuid4()

    class _FakeFile:
        filename = "../../evil.txt"
        content_type = "text/plain"

        async def read(self):
            return b"content"

    class _FakeDb:
        def __init__(self):
            self.calls = 0
            self.created = None

        def scalar(self, _query):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(id=process_instance_id, organization_id=org_id, business_number="BN-1", started_by_user_id=user_id)
            return None

        def add(self, obj):
            self.created = obj

        def flush(self):
            return None

        def commit(self):
            return None

        def refresh(self, _obj):
            return None

    monkeypatch.setattr("app.services.file_security_service.FileSecurityService.validate_upload", lambda **_: fingerprint)
    monkeypatch.setattr("app.services.attachment_service.AccessPolicy.require", lambda **_: None)
    monkeypatch.setattr("app.services.audit_service.AuditService.log_event", lambda **_: None)

    user_id = uuid4()

    attachment = asyncio.run(
        AttachmentService.upload_attachment(
            db=_FakeDb(),
            current_user=SimpleNamespace(id=user_id, organization_id=org_id, role="general_user"),
            process_instance_id=process_instance_id,
            file=_FakeFile(),
        )
    )

    assert Path(attachment.storage_path).name == fingerprint
    assert Path(attachment.storage_path).parent.name == str(org_id)
    assert attachment.file_name == "evil.txt"


def test_attachment_access_hides_foreign_instance(monkeypatch):
    current_user = SimpleNamespace(id=uuid4(), organization_id=uuid4(), role="general_user")

    class _FakeDb:
        def scalar(self, _query):
            return SimpleNamespace(id=uuid4(), organization_id=current_user.organization_id, started_by_user_id=uuid4())

    with pytest.raises(Exception):
        AttachmentService._get_accessible_instance(db=_FakeDb(), current_user=current_user, process_instance_id=uuid4())


def test_export_download_hides_non_creator_for_non_admin(monkeypatch):
    job = SimpleNamespace(id=uuid4(), organization_id=uuid4(), requested_by=uuid4(), status="completed", file_content="data", file_name="export.json", content_type="application/json", export_type="tasks")
    current_user = SimpleNamespace(id=uuid4(), organization_id=job.organization_id, role="auditor")

    monkeypatch.setattr("app.services.export_service.ExportService._get_job_in_org", lambda **_: job)
    monkeypatch.setattr("app.services.export_service.AccessPolicy.require", lambda **_: None)

    with pytest.raises(Exception):
        ExportService.get_download_payload(db=SimpleNamespace(), current_user=current_user, job_id=job.id)


def test_workflow_task_access_denied_for_non_assignee(monkeypatch):
    current_user = SimpleNamespace(id=uuid4(), organization_id=uuid4(), role="general_user")
    task = SimpleNamespace(
        id=uuid4(),
        organization_id=current_user.organization_id,
        assigned_user_id=uuid4(),
        process_instance=SimpleNamespace(status="in_progress"),
    )

    class _FakeDb:
        def scalar(self, _query):
            return task

    monkeypatch.setattr("app.services.workflow_service.AccessPolicy.require", lambda **_: None)
    with pytest.raises(Exception):
        WorkflowService._get_task_for_user(db=_FakeDb(), current_user=current_user, task_id=task.id)


def test_import_error_response_is_masked():
    raw_row = {"email": "demo@example.com", "patient_id": "abcd1234", "note": "ok"}
    error = SimpleNamespace(
        id=uuid4(),
        batch_id=uuid4(),
        organization_id=uuid4(),
        row_number=1,
        validation_type="missing",
        field_name="email",
        error_reason="Missing required field: email",
        row_data=DataGovernanceService._mask_row_value(raw_row),
        row_data_raw_encrypted="ciphertext",
        created_at=datetime.now(timezone.utc),
    )
    payload = DataImportErrorOut.model_validate(error).model_dump()
    assert payload["row_data"]["email"] != "demo@example.com"
    assert payload["row_data"]["patient_id"] != "abcd1234"
    assert "row_data_raw" not in payload


def test_import_row_masking_helper_redacts_sensitive_fields():
    masked = DataGovernanceService._mask_row_value(
        {
            "email": "demo@example.com",
            "phone": "5551234567",
            "medical_record_number": "MRN-001",
            "note": "ok",
        }
    )
    assert masked["email"] != "demo@example.com"
    assert masked["phone"] != "5551234567"
    assert masked["medical_record_number"] != "MRN-001"
    assert masked["note"] == "ok"


def test_attachment_upload_rolls_back_file_when_db_write_fails(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.attachment_service.ATTACHMENT_ROOT", tmp_path)
    fingerprint = "b" * 64
    org_id = uuid4()
    process_instance_id = uuid4()
    user_id = uuid4()

    class _FakeFile:
        filename = "report.txt"
        content_type = "text/plain"

        async def read(self):
            return b"content"

    class _FakeDb:
        def __init__(self):
            self.calls = 0

        def scalar(self, _query):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(id=process_instance_id, organization_id=org_id, business_number="BN-1", started_by_user_id=user_id)
            return None

        def add(self, _obj):
            return None

        def flush(self):
            raise RuntimeError("db failed")

        def rollback(self):
            return None

    monkeypatch.setattr("app.services.file_security_service.FileSecurityService.validate_upload", lambda **_: fingerprint)
    monkeypatch.setattr("app.services.attachment_service.AccessPolicy.require", lambda **_: None)
    monkeypatch.setattr("app.services.audit_service.AuditService.log_event", lambda **_: None)

    with pytest.raises(RuntimeError):
        asyncio.run(
            AttachmentService.upload_attachment(
                db=_FakeDb(),
                current_user=SimpleNamespace(id=user_id, organization_id=org_id, role="general_user"),
                process_instance_id=process_instance_id,
                file=_FakeFile(),
            )
        )

    assert not (tmp_path / str(org_id) / fingerprint).exists()


def test_attachment_unique_constraint_includes_process_instance():
    constraint = next(
        c for c in AttachmentMetadata.__table__.constraints if getattr(c, "name", "") == "uq_attachment_org_instance_fingerprint"
    )
    assert {column.name for column in constraint.columns} == {"organization_id", "process_instance_id", "fingerprint_sha256"}


def test_reset_tokens_are_not_exposed_by_default():
    assert AuthService.EXPOSE_RESET_TOKEN is False
