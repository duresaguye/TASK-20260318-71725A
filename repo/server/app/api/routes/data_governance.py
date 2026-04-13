from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps.auth import require_domain_permission
from app.db.deps import get_db
from app.models.user import User
from app.schemas.data_governance import (
    DataImportBatchOut,
    DataImportErrorListOut,
    DataImportRequest,
    DataLineageOut,
    DataRollbackOut,
)
from app.services.data_governance_service import DataGovernanceService


router = APIRouter(prefix="/api/v1/data", tags=["data-governance"])


@router.post("/import", response_model=DataImportBatchOut, status_code=status.HTTP_201_CREATED)
def import_data(
    payload: DataImportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_domain_permission("governance", "import")),
) -> DataImportBatchOut:
    return DataGovernanceService.import_data(db=db, current_user=current_user, payload=payload)


@router.get("/import/{batch_id}/errors", response_model=DataImportErrorListOut)
def get_import_errors(
    batch_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_domain_permission("governance", "errors")),
) -> DataImportErrorListOut:
    errors = DataGovernanceService.get_import_errors(db=db, current_user=current_user, batch_id=batch_id)
    return DataImportErrorListOut(batch_id=batch_id, errors=errors)


@router.post("/{entity}/{entity_id}/rollback", response_model=DataRollbackOut)
def rollback_entity(
    entity: str,
    entity_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_domain_permission("governance", "rollback")),
) -> DataRollbackOut:
    return DataGovernanceService.rollback_entity(
        db=db,
        current_user=current_user,
        entity=entity,
        entity_id=entity_id,
    )


@router.get("/lineage/{entity}/{entity_id}", response_model=list[DataLineageOut])
def get_lineage(
    entity: str,
    entity_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_domain_permission("governance", "lineage_read")),
) -> list[DataLineageOut]:
    return DataGovernanceService.get_lineage(
        db=db,
        current_user=current_user,
        entity=entity,
        entity_id=entity_id,
    )
