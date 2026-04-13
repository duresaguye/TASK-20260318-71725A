import csv
import io
import json
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.access_policy import AccessPolicy, ResourceContext
from app.core.rbac import normalize_role
from app.models.export_job import ExportJob
from app.models.process_instance import ProcessInstance
from app.models.user import User
from app.models.workflow_task import WorkflowTask
from app.schemas.export import ExportJobCreate
from app.db.session import SessionLocal
from app.services.audit_service import AuditService
from app.services.security_hardening_service import SecurityHardeningService


class ExportService:
    SAFE_FIELD_WHITELIST: dict[str, set[str]] = {
        "tasks": {
            "id",
            "process_instance_id",
            "assigned_user_id",
            "status",
            "step_index",
            "step_name",
            "created_at",
            "acted_at",
            "sla_due_at",
        },
        "workflows": {
            "id",
            "process_definition_id",
            "started_by_user_id",
            "status",
            "current_step_index",
            "created_at",
            "completed_at",
        },
        "users": {
            "id",
            "username",
            "role",
            "is_active",
            "created_at",
            "organization_id",
            "locked_until",
            "failed_login_attempts",
        },
        "analytics": {
            "total_users",
            "active_users",
            "total_workflows",
            "completed_workflows",
            "rejected_workflows",
            "total_tasks",
            "pending_tasks",
            "overdue_tasks",
        },
    }

    ROLE_ALLOWED_TYPES: dict[str, set[str]] = {
        "administrator": {"tasks", "workflows", "users", "analytics"},
        "auditor": {"tasks", "workflows", "users", "analytics"},
        "reviewer": {"tasks", "workflows", "analytics"},
        "general_user": {"tasks", "workflows", "analytics"},
    }

    ROLE_FIELD_LIMITS: dict[str, dict[str, set[str]]] = {
        "administrator": SAFE_FIELD_WHITELIST,
        "auditor": SAFE_FIELD_WHITELIST,
        "reviewer": {
            "tasks": {
                "id",
                "process_instance_id",
                "status",
                "step_index",
                "step_name",
                "created_at",
                "acted_at",
                "sla_due_at",
            },
            "workflows": {
                "id",
                "process_definition_id",
                "status",
                "current_step_index",
                "created_at",
                "completed_at",
            },
            "users": set(),
            "analytics": SAFE_FIELD_WHITELIST["analytics"],
        },
        "general_user": {
            "tasks": {
                "id",
                "process_instance_id",
                "status",
                "step_index",
                "step_name",
                "created_at",
                "acted_at",
                "sla_due_at",
            },
            "workflows": {
                "id",
                "process_definition_id",
                "status",
                "current_step_index",
                "created_at",
                "completed_at",
            },
            "users": set(),
            "analytics": SAFE_FIELD_WHITELIST["analytics"],
        },
    }

    @staticmethod
    def create_export_job(
        db: Session,
        current_user: User,
        payload: ExportJobCreate,
        idempotency_key: str | None = None,
    ) -> tuple[ExportJob, bool]:
        AccessPolicy.require(role=current_user.role, domain="export", action="create", db=db, organization_id=current_user.organization_id)
        if current_user.organization_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User is not assigned to an organization",
            )

        role = normalize_role(current_user.role)
        ExportService._authorize_export_type(role=role, export_type=payload.export_type)
        idempotency_record, replayed = SecurityHardeningService.get_or_create_idempotency_key(
            db=db,
            current_user=current_user,
            scope="export.create",
            raw_key=idempotency_key,
            payload=payload.model_dump(mode="json"),
        )
        if replayed:
            if idempotency_record is None or idempotency_record.resource_id is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="A matching export request is already being processed",
                )
            existing_job = db.get(ExportJob, idempotency_record.resource_id)
            if existing_job is None or existing_job.organization_id != current_user.organization_id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Export job not found",
                )
            return existing_job, False

        selected_fields = ExportService._resolve_fields(
            export_type=payload.export_type,
            requested_fields=payload.fields,
            role=role,
        )

        job = ExportJob(
            organization_id=current_user.organization_id,
            requested_by=current_user.id,
            status="pending",
            export_type=payload.export_type,
            filters=payload.filters or {},
            requested_fields=selected_fields,
            file_format=payload.file_format,
        )
        db.add(job)
        db.flush()

        AuditService.log_event(
            db=db,
            organization_id=current_user.organization_id,
            action="export.requested",
            entity_type="export_job",
            entity_id=job.id,
            user_id=current_user.id,
            details={
                "export_type": payload.export_type,
                "filters": payload.filters or {},
                "fields": selected_fields,
                "file_format": payload.file_format,
            },
        )

        SecurityHardeningService.finalize_idempotency_key(
            idempotency_record,
            resource_type="export_job",
            resource_id=job.id,
            response_payload={"export_job_id": str(job.id)},
        )

        db.commit()
        db.refresh(job)
        return job, True

    @staticmethod
    def process_export_job(job_id: UUID, requested_by_user_id: UUID | None = None) -> None:
        db = SessionLocal()
        try:
            job = db.get(ExportJob, job_id)
            if job is None or job.status == "completed":
                return

            current_user = db.get(User, requested_by_user_id or job.requested_by)
            if current_user is None or current_user.organization_id != job.organization_id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Export job not found",
                )

            role = normalize_role(current_user.role)
            ExportService._authorize_export_type(role=role, export_type=job.export_type)

            job.status = "processing"
            db.commit()

            dataset = ExportService._collect_dataset(
                db=db,
                organization_id=current_user.organization_id,
                export_type=job.export_type,
                filters=job.filters or {},
                current_user=current_user,
            )
            transformed = ExportService._apply_transformations(
                rows=dataset,
                fields=job.requested_fields or [],
                role=role,
            )
            file_name, content_type, content = ExportService._render_export(
                export_type=job.export_type,
                file_format=job.file_format,
                rows=transformed,
                fields=job.requested_fields or [],
            )

            job.file_name = file_name
            job.content_type = content_type
            job.file_content = content
            job.file_size = len(content.encode("utf-8"))
            job.status = "completed"
            job.completed_at = datetime.now(timezone.utc)
            AuditService.log_event(
                db=db,
                organization_id=current_user.organization_id,
                action="export.completed",
                entity_type="export_job",
                entity_id=job.id,
                user_id=current_user.id,
                details={"file_name": file_name, "file_size": job.file_size},
            )
            db.commit()
        except HTTPException:
            job = db.get(ExportJob, job_id)
            if job is not None:
                job.status = "failed"
                job.completed_at = datetime.now(timezone.utc)
                AuditService.log_event(
                    db=db,
                    organization_id=job.organization_id,
                    action="export.failed",
                    entity_type="export_job",
                    entity_id=job.id,
                    user_id=requested_by_user_id,
                    details={"reason": "validation_or_policy_error"},
                )
                db.commit()
        except Exception:
            job = db.get(ExportJob, job_id)
            if job is not None:
                job.status = "failed"
                job.completed_at = datetime.now(timezone.utc)
                AuditService.log_event(
                    db=db,
                    organization_id=job.organization_id,
                    action="export.failed",
                    entity_type="export_job",
                    entity_id=job.id,
                    user_id=requested_by_user_id,
                    details={"reason": "unexpected_error"},
                )
                db.commit()
        finally:
            db.close()

    @staticmethod
    def get_export_job(db: Session, current_user: User, job_id: UUID) -> ExportJob:
        job = ExportService._get_job_in_org(db=db, current_user=current_user, job_id=job_id)

        AccessPolicy.require_domain(
            role=current_user.role,
            domain="export",
            action="read",
            db=db,
            organization_id=current_user.organization_id,
            context=ResourceContext(
                organization_id=current_user.organization_id,
                owner_user_id=job.requested_by,
                is_personal=job.requested_by == current_user.id,
            ),
        )

        return job

    @staticmethod
    def get_download_payload(db: Session, current_user: User, job_id: UUID) -> tuple[str, str, str]:
        job = ExportService._get_job_in_org(db=db, current_user=current_user, job_id=job_id)

        role = normalize_role(current_user.role)
        ExportService._authorize_export_type(role=role, export_type=job.export_type)
        AccessPolicy.require_domain(
            role=current_user.role,
            domain="export",
            action="download",
            db=db,
            organization_id=current_user.organization_id,
            context=ResourceContext(
                organization_id=current_user.organization_id,
                owner_user_id=job.requested_by,
                is_personal=job.requested_by == current_user.id,
            ),
        )

        if job.status != "completed" or job.file_content is None or job.file_name is None or job.content_type is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Export job is not ready for download",
            )

        AuditService.log_event(
            db=db,
            organization_id=job.organization_id,
            action="export.downloaded",
            entity_type="export_job",
            entity_id=job.id,
            user_id=current_user.id,
            details={"file_name": job.file_name},
        )
        db.commit()

        return job.file_name, job.content_type, job.file_content

    @staticmethod
    def _authorize_export_type(role: str, export_type: str) -> None:
        allowed_types = ExportService.ROLE_ALLOWED_TYPES.get(role, set())
        if export_type not in allowed_types:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Role is not allowed to export this dataset",
            )

    @staticmethod
    def _resolve_fields(export_type: str, requested_fields: list[str] | None, role: str) -> list[str]:
        whitelist = ExportService.SAFE_FIELD_WHITELIST[export_type]
        allowed_for_role = ExportService.ROLE_FIELD_LIMITS[role][export_type]

        if len(allowed_for_role) == 0:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Role has no export field permissions for this dataset",
            )

        fields = requested_fields or sorted(list(allowed_for_role))
        unknown_fields = [field for field in fields if field not in whitelist]
        if unknown_fields:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown or unsafe fields requested: {', '.join(unknown_fields)}",
            )

        disallowed_fields = [field for field in fields if field not in allowed_for_role]
        if disallowed_fields:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role cannot export fields: {', '.join(disallowed_fields)}",
            )

        return fields

    @staticmethod
    def _collect_dataset(
        db: Session,
        organization_id: UUID,
        export_type: str,
        filters: dict,
        current_user: User | None = None,
    ) -> list[dict]:
        role = normalize_role(current_user.role) if current_user is not None else "administrator"
        if export_type == "tasks":
            query = select(WorkflowTask).where(WorkflowTask.organization_id == organization_id)
            if role == "general_user" and current_user is not None:
                query = query.where(WorkflowTask.assigned_user_id == current_user.id)
            status_filter = filters.get("status")
            if status_filter is not None:
                query = query.where(WorkflowTask.status == status_filter)
            assigned_user_id = filters.get("assigned_user_id")
            if assigned_user_id is not None:
                query = query.where(WorkflowTask.assigned_user_id == assigned_user_id)

            rows = db.scalars(query.order_by(WorkflowTask.created_at.desc())).all()
            return [
                {
                    "id": row.id,
                    "process_instance_id": row.process_instance_id,
                    "assigned_user_id": row.assigned_user_id,
                    "status": row.status,
                    "step_index": row.step_index,
                    "step_name": row.step_name,
                    "created_at": row.created_at,
                    "acted_at": row.acted_at,
                    "sla_due_at": row.sla_due_at,
                }
                for row in rows
            ]

        if export_type == "workflows":
            query = select(ProcessInstance).where(ProcessInstance.organization_id == organization_id)
            if role == "general_user" and current_user is not None:
                query = query.where(ProcessInstance.started_by_user_id == current_user.id)
            status_filter = filters.get("status")
            if status_filter is not None:
                query = query.where(ProcessInstance.status == status_filter)

            rows = db.scalars(query.order_by(ProcessInstance.created_at.desc())).all()
            return [
                {
                    "id": row.id,
                    "process_definition_id": row.process_definition_id,
                    "started_by_user_id": row.started_by_user_id,
                    "status": row.status,
                    "current_step_index": row.current_step_index,
                    "created_at": row.created_at,
                    "completed_at": row.completed_at,
                }
                for row in rows
            ]

        if export_type == "users":
            if role not in {"administrator", "auditor"}:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Role is not allowed to export user datasets",
                )
            query = select(User).where(User.organization_id == organization_id)
            role_filter = filters.get("role")
            if role_filter is not None:
                query = query.where(User.role == role_filter)
            active_filter = filters.get("is_active")
            if active_filter is not None:
                query = query.where(User.is_active == bool(active_filter))

            rows = db.scalars(query.order_by(User.created_at.desc())).all()
            return [
                {
                    "id": row.id,
                    "username": row.username,
                    "role": row.role,
                    "is_active": row.is_active,
                    "created_at": row.created_at,
                    "organization_id": row.organization_id,
                    "locked_until": row.locked_until,
                    "failed_login_attempts": row.failed_login_attempts,
                }
                for row in rows
            ]

        if export_type == "analytics":
            total_users = db.scalar(
                select(func.count(User.id)).where(User.organization_id == organization_id)
            )
            active_users = db.scalar(
                select(func.count(User.id)).where(
                    User.organization_id == organization_id,
                    User.is_active.is_(True),
                )
            )
            total_workflows = db.scalar(
                select(func.count(ProcessInstance.id)).where(ProcessInstance.organization_id == organization_id)
            )
            completed_workflows = db.scalar(
                select(func.count(ProcessInstance.id)).where(
                    ProcessInstance.organization_id == organization_id,
                    ProcessInstance.status == "completed",
                )
            )
            rejected_workflows = db.scalar(
                select(func.count(ProcessInstance.id)).where(
                    ProcessInstance.organization_id == organization_id,
                    ProcessInstance.status == "rejected",
                )
            )
            total_tasks = db.scalar(
                select(func.count(WorkflowTask.id)).where(WorkflowTask.organization_id == organization_id)
            )
            pending_tasks = db.scalar(
                select(func.count(WorkflowTask.id)).where(
                    WorkflowTask.organization_id == organization_id,
                    WorkflowTask.status == "pending",
                )
            )
            overdue_tasks = db.scalar(
                select(func.count(WorkflowTask.id)).where(
                    WorkflowTask.organization_id == organization_id,
                    WorkflowTask.status == "overdue",
                )
            )

            return [
                {
                    "total_users": int(total_users or 0),
                    "active_users": int(active_users or 0),
                    "total_workflows": int(total_workflows or 0),
                    "completed_workflows": int(completed_workflows or 0),
                    "rejected_workflows": int(rejected_workflows or 0),
                    "total_tasks": int(total_tasks or 0),
                    "pending_tasks": int(pending_tasks or 0),
                    "overdue_tasks": int(overdue_tasks or 0),
                }
            ]

        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid export type")

    @staticmethod
    def _apply_transformations(rows: list[dict], fields: list[str], role: str) -> list[dict]:
        transformed: list[dict] = []
        for row in rows:
            item: dict = {}
            for field in fields:
                value = row.get(field)
                item[field] = ExportService._desensitize_value(field=field, value=value, role=role)
            transformed.append(item)
        return transformed

    @staticmethod
    def _desensitize_value(field: str, value, role: str):
        if value is None or role == "administrator":
            return ExportService._serialize_value(value)

        field_lower = field.lower()

        if "email" in field_lower:
            return ExportService._mask_email(str(value))

        if "phone" in field_lower:
            return ExportService._mask_phone(str(value))

        if field_lower.endswith("id") or field_lower.endswith("_id"):
            if role in {"auditor", "reviewer", "general_user"}:
                return ExportService._mask_identifier(str(value))

        if role in {"reviewer", "general_user"} and field_lower in {"username"}:
            return ExportService._mask_identifier(str(value))

        return ExportService._serialize_value(value)

    @staticmethod
    def _serialize_value(value):
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, UUID):
            return str(value)
        return value

    @staticmethod
    def _mask_email(email: str) -> str:
        if "@" not in email:
            return "***"
        local, domain = email.split("@", 1)
        if len(local) <= 2:
            masked_local = "*" * len(local)
        else:
            masked_local = local[:1] + "***" + local[-1:]
        return f"{masked_local}@{domain}"

    @staticmethod
    def _mask_phone(phone: str) -> str:
        tail = phone[-4:] if len(phone) >= 4 else phone
        return "***-***-" + tail

    @staticmethod
    def _mask_identifier(identifier: str) -> str:
        if len(identifier) <= 8:
            return "***"
        return identifier[:4] + "***" + identifier[-4:]

    @staticmethod
    def _render_export(
        export_type: str,
        file_format: str,
        rows: list[dict],
        fields: list[str],
    ) -> tuple[str, str, str]:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

        if file_format == "json":
            content = json.dumps(rows, default=str, ensure_ascii=True)
            file_name = f"{export_type}_export_{timestamp}.json"
            return file_name, "application/json", content

        if file_format == "csv":
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
            content = output.getvalue()
            file_name = f"{export_type}_export_{timestamp}.csv"
            return file_name, "text/csv", content

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported export format",
        )

    @staticmethod
    def _get_job_in_org(db: Session, current_user: User, job_id: UUID) -> ExportJob:
        if current_user.organization_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User is not assigned to an organization",
            )

        job = db.scalar(
            select(ExportJob).where(
                ExportJob.id == job_id,
                ExportJob.organization_id == current_user.organization_id,
            )
        )
        if job is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Export job not found",
            )

        return job
