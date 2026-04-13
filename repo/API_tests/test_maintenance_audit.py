from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.maintenance_job import MaintenanceJob
from app.services.maintenance_service import MaintenanceService
from conftest import create_organization_via_api, register_and_login


def _create_maintenance_job(db_session, organization_id=None, *, max_retries: int = 1) -> MaintenanceJob:
    job = MaintenanceJob(
        organization_id=UUID(str(organization_id)) if organization_id is not None else None,
        job_type="full_backup",
        status="pending",
        retry_count=0,
        max_retries=max_retries,
        next_run_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        details={},
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def test_maintenance_failure_is_audited(client, db_session, monkeypatch):
    owner_username = f"maintenance-owner-{uuid4().hex[:8]}"
    owner_token = register_and_login(client, owner_username, "Strong123")
    organization = create_organization_via_api(
        client,
        owner_token,
        name=f"Maintenance Org {uuid4().hex[:8]}",
        code=f"MT{uuid4().hex[:8].upper()}",
    )

    _create_maintenance_job(db_session, None, max_retries=1)

    monkeypatch.setattr(
        "app.services.maintenance_service.MaintenanceService._fetch_raw_rows",
        lambda **_: (_ for _ in ()).throw(RuntimeError("backup failed")),
    )

    MaintenanceService.run_due_jobs(db_session)

    failure_logs = db_session.scalars(
        select(AuditLog).where(
            AuditLog.organization_id.is_(None),
            AuditLog.audit_scope == "system",
            AuditLog.action.in_(["job_started", "job_failed"]),
        )
    ).all()
    assert any(log.action == "job_started" for log in failure_logs)
    assert any(log.action == "job_failed" for log in failure_logs)


def test_maintenance_retry_logs_are_created(client, db_session, monkeypatch):
    owner_username = f"maintenance-retry-owner-{uuid4().hex[:8]}"
    owner_token = register_and_login(client, owner_username, "Strong123")
    organization = create_organization_via_api(
        client,
        owner_token,
        name=f"Maintenance Retry Org {uuid4().hex[:8]}",
        code=f"MR{uuid4().hex[:8].upper()}",
    )

    job = _create_maintenance_job(db_session, organization["id"], max_retries=2)

    monkeypatch.setattr(
        "app.services.maintenance_service.MaintenanceService._fetch_raw_rows",
        lambda **_: (_ for _ in ()).throw(RuntimeError("temporary backup failure")),
    )

    MaintenanceService.run_due_jobs(db_session)
    db_session.refresh(job)
    assert job.status == "pending"
    assert job.retry_count == 1

    retry_logs = db_session.scalars(
        select(AuditLog).where(
            AuditLog.organization_id == UUID(organization["id"]),
            AuditLog.action == "job_retry",
        )
    ).all()
    assert len(retry_logs) == 1

    job.next_run_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()

    monkeypatch.setattr(
        "app.services.maintenance_service.MaintenanceService._fetch_raw_rows",
        lambda **_: [],
    )

    MaintenanceService.run_due_jobs(db_session)
    db_session.refresh(job)
    assert job.status == "completed"

    success_logs = db_session.scalars(
        select(AuditLog).where(
            AuditLog.organization_id == UUID(organization["id"]),
            AuditLog.action == "job_success",
        )
    ).all()
    assert len(success_logs) == 1
