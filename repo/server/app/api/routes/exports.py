from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Response, status
from sqlalchemy.orm import Session

from app.api.deps.auth import require_domain_permission
from app.db.deps import get_db
from app.models.user import User
from app.schemas.export import ExportJobCreate, ExportJobOut
from app.services.export_service import ExportService


router = APIRouter(prefix="/api/v1/exports", tags=["exports"])


@router.post("", response_model=ExportJobOut, status_code=status.HTTP_201_CREATED)
def create_export_job(
    payload: ExportJobCreate,
    background_tasks: BackgroundTasks,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_domain_permission("export", "create")),
) -> ExportJobOut:
    job, should_process = ExportService.create_export_job(
        db=db,
        current_user=current_user,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    if should_process:
        background_tasks.add_task(ExportService.process_export_job, job.id, current_user.id)
    return job


@router.get("/{job_id}", response_model=ExportJobOut)
def get_export_job(
    job_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_domain_permission("export", "read")),
) -> ExportJobOut:
    return ExportService.get_export_job(db=db, current_user=current_user, job_id=job_id)


@router.get("/{job_id}/download")
def download_export(
    job_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_domain_permission("export", "download")),
) -> Response:
    file_name, content_type, content = ExportService.get_download_payload(
        db=db,
        current_user=current_user,
        job_id=job_id,
    )
    return Response(
        content=content,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )
