from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.access_policy import AccessPolicy
from app.core.rbac import ensure_valid_role
from app.models.organization import Organization
from app.models.user import User
from app.schemas.organization import OrganizationCreate, OrganizationJoin, OrganizationRoleUpdate
from app.services.audit_service import AuditService
from app.services.data_governance_service import DataGovernanceService
from app.services.response_security_service import ResponseSecurityService


class OrganizationService:
    @staticmethod
    def create_organization(
        db: Session,
        current_user: User,
        org_data: OrganizationCreate,
    ) -> Organization:
        AccessPolicy.require(role=current_user.role, domain="organization", action="create", db=db, organization_id=current_user.organization_id)
        if current_user.organization_id is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User already belongs to an organization",
            )

        existing_org = db.scalar(select(Organization).where(Organization.name == org_data.name))
        if existing_org is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Organization name already exists",
            )
        existing_code = db.scalar(select(Organization).where(Organization.code == org_data.code))
        if existing_code is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Organization code already exists",
            )

        org = Organization(name=org_data.name, code=org_data.code)
        db.add(org)
        db.flush()

        current_user.organization_id = org.id
        current_user.role = "administrator"
        DataGovernanceService.create_version_snapshot(
            db=db,
            entity="user",
            instance=current_user,
            actor_user_id=current_user.id,
            change_reason="organization_created",
            source_system="manual",
            transformation_step="organization_create_membership_update",
        )
        AuditService.log_event(
            db=db,
            organization_id=org.id,
            action="organization.created",
            entity_type="organization",
            entity_id=org.id,
            user_id=current_user.id,
            details={"result": "created"},
        )

        db.commit()
        db.refresh(org)
        return org

    @staticmethod
    def join_organization(
        db: Session,
        current_user: User,
        org_id: UUID,
        join_data: OrganizationJoin,
    ) -> User:
        AccessPolicy.require(role=current_user.role, domain="organization", action="join", db=db, organization_id=current_user.organization_id)
        role = "general_user"

        org = db.get(Organization, org_id)
        if org is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Organization not found",
            )

        if current_user.organization_id is not None and current_user.organization_id != org_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User already belongs to another organization",
            )

        current_user.organization_id = org.id
        current_user.role = role
        DataGovernanceService.create_version_snapshot(
            db=db,
            entity="user",
            instance=current_user,
            actor_user_id=current_user.id,
            change_reason="organization_joined",
            source_system="manual",
            transformation_step="organization_join_membership_update",
        )
        AuditService.log_event(
            db=db,
            organization_id=org.id,
            action="organization.joined",
            entity_type="organization",
            entity_id=org.id,
            user_id=current_user.id,
            details={"result": "joined"},
        )
        db.commit()
        db.refresh(current_user)
        return current_user

    @staticmethod
    def list_organization_users(
        db: Session,
        org_id: UUID,
        current_user: User,
    ) -> list[dict]:
        AccessPolicy.require(role=current_user.role, domain="organization", action="read", db=db, organization_id=current_user.organization_id)
        if current_user.organization_id != org_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cross-organization access is forbidden",
            )
        users = db.scalars(
            select(User)
            .where(User.organization_id == org_id)
            .order_by(User.created_at.asc())
        ).all()
        return [
            ResponseSecurityService.mask_user_summary(current_role=current_user.role, user=user)
            for user in users
        ]

    @staticmethod
    def assign_user_role(
        db: Session,
        current_user: User,
        org_id: UUID,
        user_id: UUID,
        role_data: OrganizationRoleUpdate,
    ) -> User:
        AccessPolicy.require(
            role=current_user.role,
            domain="organization",
            action="assign_role",
            db=db,
            organization_id=current_user.organization_id,
        )
        if current_user.organization_id != org_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cross-organization access is forbidden",
            )

        target_user = db.scalar(
            select(User).where(
                User.id == user_id,
                User.organization_id == org_id,
            )
        )
        if target_user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        normalized_role = ensure_valid_role(role_data.role)
        if current_user.id == target_user.id and normalized_role != "administrator":
            other_admin_exists = db.scalar(
                select(User.id).where(
                    User.organization_id == org_id,
                    User.id != current_user.id,
                    User.role.in_(["admin", "administrator"]),
                )
            )
            if other_admin_exists is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Organization must retain at least one administrator",
                )

        target_user.role = normalized_role
        DataGovernanceService.create_version_snapshot(
            db=db,
            entity="user",
            instance=target_user,
            actor_user_id=current_user.id,
            change_reason="organization_role_assigned",
            source_system="manual",
            transformation_step="organization_assign_role",
        )
        AuditService.log_event(
            db=db,
            organization_id=org_id,
            action="organization.user.role_assigned",
            entity_type="user",
            entity_id=target_user.id,
            user_id=current_user.id,
            details={"role": normalized_role},
        )
        db.commit()
        db.refresh(target_user)
        return target_user
