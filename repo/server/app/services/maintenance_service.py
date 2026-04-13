import asyncio
import base64
import gzip
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import re
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
import app.models.data_import_batch  # noqa: F401
import app.models.data_import_error  # noqa: F401
import app.models.data_lineage  # noqa: F401
import app.models.data_quality_rule  # noqa: F401
import app.models.data_version  # noqa: F401
from app.db.base import Base
from app.services.audit_service import AuditService
from app.services.data_governance_service import DataGovernanceService
from app.models.maintenance_job import MaintenanceJob
from app.models.process_instance import ProcessInstance
from app.models.workflow_task import WorkflowTask
from app.db.session import SessionLocal


BACKUP_ROOT = Path(os.getenv("BACKUP_ROOT", "/tmp/medical_platform_backups"))
BACKUP_INTERVAL_SECONDS = int(os.getenv("BACKUP_INTERVAL_SECONDS", "86400"))
ARCHIVE_RETENTION_DAYS = int(os.getenv("ARCHIVE_RETENTION_DAYS", "30"))
BACKUP_COMPRESS = os.getenv("BACKUP_COMPRESS", "true").lower() == "true"


class MaintenanceService:
    @staticmethod
    def run_due_jobs(db: Session) -> None:
        now = datetime.now(timezone.utc)
        jobs = db.scalars(
            select(MaintenanceJob).where(
                MaintenanceJob.status.in_(["pending", "failed"]),
                (MaintenanceJob.next_run_at.is_(None)) | (MaintenanceJob.next_run_at <= now),
            )
        ).all()
        for job in jobs:
            MaintenanceService._audit_job_event(
                db=db,
                job=job,
                action="job_started",
                timestamp=now,
            )
            try:
                job.status = "processing"
                if job.job_type == "full_backup":
                    MaintenanceService._run_full_backup(db=db, job=job)
                elif job.job_type == "archive_completed_workflows":
                    MaintenanceService._archive_completed_workflows(db=db, job=job)
                job.status = "completed"
                job.completed_at = now
                MaintenanceService._audit_job_event(
                    db=db,
                    job=job,
                    action="job_success",
                    timestamp=now,
                )
            except Exception as exc:
                job.retry_count += 1
                job.details = {"error": str(exc)}
                effective_max_retries = min(job.max_retries, 3)
                if job.retry_count >= effective_max_retries:
                    job.status = "failed"
                    MaintenanceService._audit_job_event(
                        db=db,
                        job=job,
                        action="job_failed",
                        timestamp=now,
                        error=str(exc),
                    )
                else:
                    job.status = "pending"
                    job.next_run_at = now + timedelta(minutes=10)
                    MaintenanceService._audit_job_event(
                        db=db,
                        job=job,
                        action="job_retry",
                        timestamp=now,
                        error=str(exc),
                    )
            db.commit()

        MaintenanceService._ensure_recurring_jobs(db=db)

    @staticmethod
    def _ensure_recurring_jobs(db: Session) -> None:
        now = datetime.now(timezone.utc)
        MaintenanceService._cleanup_old_backups()
        backup_job = db.scalar(
            select(MaintenanceJob).where(
                MaintenanceJob.job_type == "full_backup",
                MaintenanceJob.status.in_(["pending", "processing"]),
            )
        )
        if backup_job is None:
            db.add(
                MaintenanceJob(
                    job_type="full_backup",
                    status="pending",
                    next_run_at=now + timedelta(seconds=BACKUP_INTERVAL_SECONDS),
                    max_retries=3,
                )
            )

        archive_job = db.scalar(
            select(MaintenanceJob).where(
                MaintenanceJob.job_type == "archive_completed_workflows",
                MaintenanceJob.status.in_(["pending", "processing"]),
            )
        )
        if archive_job is None:
            db.add(
                MaintenanceJob(
                    job_type="archive_completed_workflows",
                    status="pending",
                    next_run_at=now + timedelta(seconds=BACKUP_INTERVAL_SECONDS),
                    max_retries=3,
                )
            )
        db.commit()

    @staticmethod
    def _run_full_backup(db: Session, job: MaintenanceJob) -> None:
        BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
        created_at = datetime.now(timezone.utc)

        tables_payload: dict[str, list[dict]] = {}
        for table in Base.metadata.sorted_tables:
            rows = MaintenanceService._fetch_raw_rows(
                db=db,
                table_name=table.name,
                columns=[column.name for column in table.columns],
            )
            tables_payload[table.name] = [
                {
                    key: MaintenanceService._serialize_value(value)
                    for key, value in row.items()
                }
                for row in rows
            ]

        payload = {
            "created_at": created_at.isoformat(),
            "tables": tables_payload,
        }
        target = BACKUP_ROOT / f"backup_{created_at.strftime('%Y%m%d%H%M%S')}.json.enc"
        target.write_bytes(MaintenanceService._encrypt_backup_payload(payload))
        MaintenanceService._cleanup_old_backups()
        job.details = {"backup_path": str(target)}
        job.next_run_at = created_at + timedelta(seconds=BACKUP_INTERVAL_SECONDS)

    @staticmethod
    def _cleanup_old_backups() -> None:
        if not BACKUP_ROOT.exists():
            return

        threshold = datetime.now(timezone.utc) - timedelta(days=ARCHIVE_RETENTION_DAYS)
        for backup_file in BACKUP_ROOT.glob("backup_*.json.enc"):
            backup_timestamp = MaintenanceService._extract_backup_timestamp(backup_file)
            if backup_timestamp is None:
                backup_timestamp = datetime.fromtimestamp(backup_file.stat().st_mtime, tz=timezone.utc)
            if backup_timestamp < threshold:
                backup_file.unlink(missing_ok=True)

    @staticmethod
    def _extract_backup_timestamp(backup_file: Path) -> datetime | None:
        match = re.match(r"backup_(\d{14})\.json\.enc$", backup_file.name)
        if match is None:
            return None
        return datetime.strptime(match.group(1), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)

    @staticmethod
    def _fetch_raw_rows(db: Session, table_name: str, columns: list[str]) -> list[dict]:
        column_sql = ", ".join(f'"{column}"' for column in columns)
        query = f'SELECT {column_sql} FROM "{table_name}"'
        result = db.connection().exec_driver_sql(query)
        return [dict(row._mapping) for row in result]

    @staticmethod
    def _encrypt_backup_payload(payload: dict) -> bytes:
        raw_key = MaintenanceService._resolve_backup_key()
        aes_key = hashlib.sha256(raw_key.encode("utf-8")).digest()
        aesgcm = AESGCM(aes_key)
        nonce = os.urandom(12)
        serialized = json.dumps(payload, ensure_ascii=True, default=str).encode("utf-8")
        if BACKUP_COMPRESS:
            serialized = gzip.compress(serialized)
        ciphertext = aesgcm.encrypt(nonce, serialized, b"medical-platform-backup")
        envelope = {
            "version": 1,
            "algorithm": "AES-256-GCM",
            "compression": "gzip" if BACKUP_COMPRESS else "none",
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        }
        return json.dumps(envelope, ensure_ascii=True).encode("utf-8")

    @staticmethod
    def _resolve_backup_key() -> str:
        key = os.getenv("BACKUP_ENCRYPTION_KEY") or os.getenv("SENSITIVE_DATA_KEY")
        if not key:
            raise RuntimeError("BACKUP_ENCRYPTION_KEY or SENSITIVE_DATA_KEY must be set")
        return key

    @staticmethod
    def _serialize_value(value):
        if isinstance(value, dict):
            return {str(key): MaintenanceService._serialize_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [MaintenanceService._serialize_value(item) for item in value]
        if isinstance(value, tuple):
            return [MaintenanceService._serialize_value(item) for item in value]
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Decimal):
            return float(value)
        return value

    @staticmethod
    def _archive_completed_workflows(db: Session, job: MaintenanceJob) -> None:
        threshold = datetime.now(timezone.utc) - timedelta(days=ARCHIVE_RETENTION_DAYS)
        archived = db.scalars(
            select(ProcessInstance).where(
                ProcessInstance.completed_at.is_not(None),
                ProcessInstance.completed_at < threshold,
                ProcessInstance.status.in_(["completed", "rejected"]),
            )
        ).all()
        archived_count = 0
        for instance in archived:
            payload = dict(instance.payload or {})
            payload["archived_at"] = datetime.now(timezone.utc).isoformat()
            payload["archival_policy_days"] = ARCHIVE_RETENTION_DAYS
            instance.payload = payload
            DataGovernanceService.create_version_snapshot(
                db=db,
                entity="workflow",
                instance=instance,
                actor_user_id=None,
                change_reason="workflow_archived",
                source_system="maintenance",
                transformation_step="maintenance_archive_completed_workflow",
                metadata={"archival_policy_days": ARCHIVE_RETENTION_DAYS},
            )
            AuditService.log_event(
                db=db,
                organization_id=instance.organization_id,
                action="workflow.archived",
                entity_type="process_instance",
                entity_id=instance.id,
                user_id=None,
                details={
                    "archival_policy_days": ARCHIVE_RETENTION_DAYS,
                    "completed_at": instance.completed_at.isoformat() if instance.completed_at else None,
                },
            )
            archived_count += 1
        job.details = {"archived_count": archived_count}
        job.next_run_at = datetime.now(timezone.utc) + timedelta(seconds=BACKUP_INTERVAL_SECONDS)

    @staticmethod
    def _audit_job_event(
        db: Session,
        job: MaintenanceJob,
        action: str,
        timestamp: datetime,
        error: str | None = None,
    ) -> None:
        details = {
            "job_id": str(job.id),
            "job_type": job.job_type,
            "timestamp": timestamp.isoformat(),
        }
        if job.organization_id is not None:
            details["org_id"] = str(job.organization_id)
        if error is not None:
            details["error"] = error

        AuditService.log_event(
            db=db,
            organization_id=job.organization_id,
            audit_scope="system" if job.organization_id is None else "organization",
            action=action,
            entity_type="maintenance_job",
            entity_id=job.id,
            user_id=None,
            details=details,
        )


async def run_maintenance_monitor() -> None:
    while True:
        db = SessionLocal()
        try:
            MaintenanceService.run_due_jobs(db)
        except Exception:
            db.rollback()
        finally:
            db.close()
        await asyncio.sleep(60)
