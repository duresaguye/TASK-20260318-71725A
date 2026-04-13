from uuid import UUID

from fastapi import APIRouter, Depends, File, Header, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps.auth import get_current_user, require_domain_permission, require_role
from app.db.deps import get_db
from app.models.user import User
from app.schemas.workflow import (
    ProcessDefinitionCreate,
    ProcessDefinitionOut,
    ProcessInstanceOut,
    ProcessInstanceStart,
    AttachmentOut,
    TaskActionRequest,
    TaskCommentOut,
    TaskCommentRequest,
    TaskOut,
)
from app.services.workflow_service import WorkflowService
from app.services.attachment_service import AttachmentService


router = APIRouter(prefix="/api/v1/workflows", tags=["workflows"])


@router.post("/definitions", response_model=ProcessDefinitionOut, status_code=status.HTTP_201_CREATED)
def create_process_definition(
    payload: ProcessDefinitionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_domain_permission("workflow", "create_definition")),
) -> ProcessDefinitionOut:
    return WorkflowService.create_process_definition(db=db, current_user=current_user, payload=payload)


@router.post("/instances/start", response_model=ProcessInstanceOut, status_code=status.HTTP_201_CREATED)
def start_process_instance(
    payload: ProcessInstanceStart,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_domain_permission("workflow", "start")),
) -> ProcessInstanceOut:
    return WorkflowService.start_process_instance(
        db=db,
        current_user=current_user,
        payload=payload,
        idempotency_key=idempotency_key,
    )


@router.get("/tasks/my", response_model=list[TaskOut])
def list_my_tasks(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_domain_permission("workflow", "read")),
) -> list[TaskOut]:
    return WorkflowService.list_my_tasks(db=db, current_user=current_user)


@router.get("/instances/{process_instance_id}/tasks", response_model=list[TaskOut])
def list_instance_tasks(
    process_instance_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_domain_permission("workflow", "read")),
) -> list[TaskOut]:
    return WorkflowService.list_instance_tasks(
        db=db,
        current_user=current_user,
        process_instance_id=process_instance_id,
    )


@router.post("/tasks/{task_id}/approve", response_model=TaskOut)
def approve_task(
    task_id: UUID,
    payload: TaskActionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_domain_permission("workflow", "approve")),
) -> TaskOut:
    return WorkflowService.approve_task(db=db, current_user=current_user, task_id=task_id, payload=payload)


@router.post("/tasks/{task_id}/reject", response_model=TaskOut)
def reject_task(
    task_id: UUID,
    payload: TaskActionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_domain_permission("workflow", "reject")),
) -> TaskOut:
    return WorkflowService.reject_task(db=db, current_user=current_user, task_id=task_id, payload=payload)


@router.post("/tasks/{task_id}/comment", response_model=TaskCommentOut, status_code=status.HTTP_201_CREATED)
def comment_task(
    task_id: UUID,
    payload: TaskCommentRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_domain_permission("workflow", "comment")),
) -> TaskCommentOut:
    return WorkflowService.comment_task(db=db, current_user=current_user, task_id=task_id, payload=payload)


@router.post("/instances/{process_instance_id}/attachments", response_model=AttachmentOut, status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    process_instance_id: UUID,
    workflow_task_id: UUID | None = None,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_domain_permission("attachments", "upload")),
) -> AttachmentOut:
    return await AttachmentService.upload_attachment(
        db=db,
        current_user=current_user,
        process_instance_id=process_instance_id,
        file=file,
        workflow_task_id=workflow_task_id,
    )


@router.get("/instances/{process_instance_id}/attachments", response_model=list[AttachmentOut])
def list_attachments(
    process_instance_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_domain_permission("attachments", "read")),
) -> list[AttachmentOut]:
    return AttachmentService.list_attachments(
        db=db,
        current_user=current_user,
        process_instance_id=process_instance_id,
    )


@router.get("/instances/{process_instance_id}/attachments/{attachment_id}/download")
def download_attachment(
    process_instance_id: UUID,
    attachment_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_domain_permission("attachments", "download")),
) -> Response:
    file_name, content_type, content = AttachmentService.get_download_payload(
        db=db,
        current_user=current_user,
        process_instance_id=process_instance_id,
        attachment_id=attachment_id,
    )
    return Response(
        content=content,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )
