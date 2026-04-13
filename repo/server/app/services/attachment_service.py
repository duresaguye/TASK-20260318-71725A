import os
import re
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.access_policy import AccessPolicy, ResourceContext
from app.models.attachment_metadata import AttachmentMetadata
from app.models.process_instance import ProcessInstance
from app.models.workflow_task import WorkflowTask
from app.models.user import User
from app.services.audit_service import AuditService
from app.services.file_security_service import FileSecurityService


ATTACHMENT_ROOT = Path(os.getenv("ATTACHMENT_STORAGE_ROOT", "/tmp/medical_platform_uploads"))


def _sanitize_filename(filename: str | None) -> str:
    if filename is None:
        return "upload.bin"
    safe_name = Path(filename).name.strip()
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", safe_name)
    return safe_name or "upload.bin"


class AttachmentService:
    @staticmethod
    async def upload_attachment(
        db: Session,
        current_user: User,
        process_instance_id: UUID,
        file: UploadFile,
        workflow_task_id: UUID | None = None,
    ) -> AttachmentMetadata:
        AccessPolicy.require(role=current_user.role, domain="attachments", action="upload", db=db, organization_id=current_user.organization_id)
        if current_user.organization_id is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is not assigned to an organization")

        instance = db.scalar(
            select(ProcessInstance).where(
                ProcessInstance.id == process_instance_id,
                ProcessInstance.organization_id == current_user.organization_id,
            )
        )
        if instance is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Process instance not found")
        AccessPolicy.require_domain(
            role=current_user.role,
            domain="attachments",
            action="upload",
            db=db,
            organization_id=current_user.organization_id,
            context=ResourceContext(
                organization_id=current_user.organization_id,
                owner_user_id=instance.started_by_user_id,
                is_personal=instance.started_by_user_id == current_user.id,
            ),
        )

        task = None
        if workflow_task_id is not None:
            task = db.scalar(
                select(WorkflowTask).where(
                    WorkflowTask.id == workflow_task_id,
                    WorkflowTask.process_instance_id == process_instance_id,
                    WorkflowTask.organization_id == current_user.organization_id,
                )
            )
            if task is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow task not found")

        content = await file.read()
        safe_file_name = _sanitize_filename(file.filename)
        fingerprint = FileSecurityService.validate_upload(
            file_name=safe_file_name,
            content_type=file.content_type or "application/octet-stream",
            content=content,
        )

        existing = db.scalar(
            select(AttachmentMetadata).where(
                AttachmentMetadata.organization_id == current_user.organization_id,
                AttachmentMetadata.process_instance_id == process_instance_id,
                AttachmentMetadata.fingerprint_sha256 == fingerprint,
            )
        )
        if existing is not None:
            return existing

        org_root = ATTACHMENT_ROOT / str(current_user.organization_id)
        org_root.mkdir(parents=True, exist_ok=True)
        file_path = org_root / fingerprint
        created_file = not file_path.exists()
        if created_file:
            file_path.write_bytes(content)

        try:
            attachment = AttachmentMetadata(
                organization_id=current_user.organization_id,
                process_instance_id=process_instance_id,
                workflow_task_id=workflow_task_id,
                uploaded_by=current_user.id,
                business_number=instance.business_number,
                file_name=safe_file_name,
                content_type=file.content_type or "application/octet-stream",
                file_size=len(content),
                fingerprint_sha256=fingerprint,
                storage_path=str(file_path),
            )
            db.add(attachment)
            db.flush()

            AuditService.log_event(
                db=db,
                organization_id=current_user.organization_id,
                action="workflow.attachment.uploaded",
                entity_type="attachment_metadata",
                entity_id=attachment.id,
                user_id=current_user.id,
                details={"process_instance_id": str(process_instance_id), "workflow_task_id": str(workflow_task_id) if workflow_task_id else None},
            )
            db.commit()
            db.refresh(attachment)
            return attachment
        except Exception:
            db.rollback()
            if created_file and file_path.exists():
                file_path.unlink()
            raise

    @staticmethod
    def list_attachments(db: Session, current_user: User, process_instance_id: UUID) -> list[AttachmentMetadata]:
        instance = AttachmentService._get_accessible_instance(db=db, current_user=current_user, process_instance_id=process_instance_id)
        has_review_access = instance.started_by_user_id == current_user.id or AttachmentService._has_review_access(
            db=db,
            current_user=current_user,
            process_instance_id=process_instance_id,
        )
        AccessPolicy.require_domain(
            role=current_user.role,
            domain="attachments",
            action="read",
            db=db,
            organization_id=current_user.organization_id,
            context=ResourceContext(
                organization_id=current_user.organization_id,
                owner_user_id=instance.started_by_user_id,
                is_personal=has_review_access,
            ),
        )
        attachments = db.scalars(
            select(AttachmentMetadata)
            .where(
                AttachmentMetadata.organization_id == current_user.organization_id,
                AttachmentMetadata.process_instance_id == process_instance_id,
            )
            .order_by(AttachmentMetadata.created_at.asc())
        ).all()
        return list(attachments)

    @staticmethod
    def get_download_payload(
        db: Session,
        current_user: User,
        process_instance_id: UUID,
        attachment_id: UUID,
    ) -> tuple[str, str, bytes]:
        instance = AttachmentService._get_accessible_instance(db=db, current_user=current_user, process_instance_id=process_instance_id)
        has_review_access = instance.started_by_user_id == current_user.id or AttachmentService._has_review_access(
            db=db,
            current_user=current_user,
            process_instance_id=process_instance_id,
        )
        AccessPolicy.require_domain(
            role=current_user.role,
            domain="attachments",
            action="download",
            db=db,
            organization_id=current_user.organization_id,
            context=ResourceContext(
                organization_id=current_user.organization_id,
                owner_user_id=instance.started_by_user_id,
                is_personal=has_review_access,
            ),
        )
        attachment = db.scalar(
            select(AttachmentMetadata).where(
                AttachmentMetadata.id == attachment_id,
                AttachmentMetadata.process_instance_id == process_instance_id,
                AttachmentMetadata.organization_id == current_user.organization_id,
            )
        )
        if attachment is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")
        file_path = Path(attachment.storage_path)
        if not file_path.exists():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment content not found")
        return attachment.file_name, attachment.content_type, file_path.read_bytes()

    @staticmethod
    def _get_accessible_instance(db: Session, current_user: User, process_instance_id: UUID) -> ProcessInstance:
        if current_user.organization_id is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is not assigned to an organization")
        instance = db.scalar(
            select(ProcessInstance).where(
                ProcessInstance.id == process_instance_id,
                ProcessInstance.organization_id == current_user.organization_id,
            )
        )
        if instance is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Process instance not found")

        is_assigned_reviewer = AttachmentService._has_review_access(
            db=db,
            current_user=current_user,
            process_instance_id=process_instance_id,
        )
        AccessPolicy.require_domain(
            role=current_user.role,
            domain="attachments",
            action="download",
            db=db,
            organization_id=current_user.organization_id,
            context=ResourceContext(
                organization_id=current_user.organization_id,
                owner_user_id=instance.started_by_user_id,
                is_personal=instance.started_by_user_id == current_user.id or is_assigned_reviewer,
            ),
        )
        return instance

    @staticmethod
    def _has_review_access(db: Session, current_user: User, process_instance_id: UUID) -> bool:
        if current_user.organization_id is None:
            return False

        assigned_task = db.scalar(
            select(WorkflowTask.id).where(
                WorkflowTask.process_instance_id == process_instance_id,
                WorkflowTask.organization_id == current_user.organization_id,
                WorkflowTask.assigned_user_id == current_user.id,
            )
        )
        return assigned_task is not None
