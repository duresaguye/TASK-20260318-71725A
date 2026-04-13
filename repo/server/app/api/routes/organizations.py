from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps.auth import get_current_user, require_domain_permission, require_org_access
from app.db.deps import get_db
from app.models.user import User
from app.schemas.organization import (
    OrganizationCreate,
    OrganizationJoin,
    OrganizationOut,
    OrganizationUserOut,
    OrganizationRoleUpdate,
)
from app.services.organization_service import OrganizationService


router = APIRouter(prefix="/api/v1/organizations", tags=["organizations"])


@router.post("", response_model=OrganizationOut)
def create_organization(
    org_data: OrganizationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_domain_permission("organization", "create")),
) -> OrganizationOut:
    return OrganizationService.create_organization(db=db, current_user=current_user, org_data=org_data)


@router.post("/{org_id}/join", response_model=OrganizationUserOut)
def join_organization(
    org_id: UUID,
    join_data: OrganizationJoin,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_domain_permission("organization", "join")),
) -> OrganizationUserOut:
    return OrganizationService.join_organization(
        db=db,
        current_user=current_user,
        org_id=org_id,
        join_data=join_data,
    )


@router.get("/{org_id}/users", response_model=list[OrganizationUserOut])
def list_users_in_organization(
    org_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_domain_permission("organization", "read")),
    _: User = Depends(require_org_access),
) -> list[OrganizationUserOut]:
    return OrganizationService.list_organization_users(db=db, org_id=org_id, current_user=current_user)


@router.post("/{org_id}/users/{user_id}/roles", response_model=OrganizationUserOut)
def assign_user_role(
    org_id: UUID,
    user_id: UUID,
    role_data: OrganizationRoleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_domain_permission("organization", "assign_role")),
) -> OrganizationUserOut:
    return OrganizationService.assign_user_role(
        db=db,
        current_user=current_user,
        org_id=org_id,
        user_id=user_id,
        role_data=role_data,
    )
