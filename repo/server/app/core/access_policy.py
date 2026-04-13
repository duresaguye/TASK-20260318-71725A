from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.rbac import normalize_role
from app.models.role_authorization import RoleAuthorization


ALL_DOMAINS = {
    "auth",
    "organization",
    "workflow",
    "export",
    "governance",
    "analytics",
    "attachments",
    "operations",
}

ROLE_PERMISSIONS = {
    "administrator": {
        "auth": {"self"},
        "organization": {"create", "join", "read", "assign_role"},
        "workflow": {"create_definition", "start", "read", "approve", "reject", "comment", "attachment_upload", "attachment_read"},
        "export": {"create", "read", "download"},
        "governance": {"import", "errors", "rollback", "lineage_read"},
        "analytics": {"dashboard", "activity", "sla", "search"},
        "attachments": {"upload", "read", "download"},
        "operations": {"backup", "archive", "monitor"},
    },
    "auditor": {
        "auth": {"self"},
        "organization": {"read"},
        "workflow": {"read", "attachment_read"},
        "export": {"read", "download"},
        "governance": {"errors", "lineage_read"},
        "analytics": {"dashboard", "activity", "sla", "search"},
        "attachments": {"read", "download"},
        "operations": {"monitor"},
    },
    "reviewer": {
        "auth": {"self"},
        "organization": set(),
        "workflow": {"read", "approve", "reject", "comment", "attachment_read"},
        "export": {"read"},
        "governance": {"import", "errors", "lineage_read"},
        "analytics": {"dashboard", "activity", "sla", "search"},
        "attachments": {"read", "download"},
        "operations": {"monitor"},
    },
    "general_user": {
        "auth": {"self"},
        "organization": {"create", "join", "read_own"},
        "workflow": {"read", "start", "comment", "attachment_upload", "attachment_read", "attachment_read_own"},
        "export": {"read", "download", "read_own"},
        "governance": set(),
        "analytics": {"dashboard", "activity", "sla", "search"},
        "attachments": {"upload", "read", "download", "read_own", "download_own"},
        "operations": {"monitor"},
    },
}


@dataclass(frozen=True)
class ResourceContext:
    organization_id: object | None = None
    owner_user_id: object | None = None
    resource_user_id: object | None = None
    is_personal: bool = False
    is_read_only: bool = False


class AccessPolicy:
    @staticmethod
    def allowed(role: str, domain: str, action: str, db: Session | None = None, organization_id=None) -> bool:
        normalized_role = normalize_role(role)
        if normalized_role == "administrator":
            return True
        if db is not None:
            db_permission = AccessPolicy._db_allowed(
                db=db,
                role=normalized_role,
                domain=domain,
                action=action,
                organization_id=organization_id,
            )
            if db_permission is not None:
                return db_permission
            return False
        return action in ROLE_PERMISSIONS.get(normalized_role, {}).get(domain, set())

    @staticmethod
    def require(role: str, domain: str, action: str, db: Session | None = None, organization_id=None) -> None:
        if not AccessPolicy.allowed(role, domain, action, db=db, organization_id=organization_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permission for this resource domain",
            )

    @staticmethod
    def require_domain(
        role: str,
        domain: str,
        action: str,
        context: ResourceContext | None = None,
        db: Session | None = None,
        organization_id=None,
    ) -> None:
        AccessPolicy.require(role=role, domain=domain, action=action, db=db, organization_id=organization_id)
        if context is None:
            return

        normalized_role = normalize_role(role)
        if normalized_role == "general_user":
            if action in {"read", "download", "lineage_read", "errors", "read_own", "attachment_read_own", "download_own", "errors_own", "lineage_read_own"} and not context.is_personal:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Personal access is required for this resource",
                )
            if action in {"comment", "upload"} and context.owner_user_id is not None and not context.is_personal:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access to this resource is restricted",
                )
            if action in {"create", "approve", "reject", "comment", "start", "upload"} and context.is_read_only:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Role has no write access for this resource",
                )

            if action in {"read", "read_own"} and context.owner_user_id is not None and not context.is_personal:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access to this resource is restricted",
                )

        if normalized_role == "auditor" and action not in {"read", "download", "dashboard", "activity", "sla", "search", "monitor"}:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Auditor role is read-only",
            )

    @staticmethod
    def is_personal_access(current_user, owner_user_id) -> bool:
        return owner_user_id is not None and owner_user_id == current_user.id

    @staticmethod
    def seed_default_permissions(db: Session) -> None:
        for role, domains in ROLE_PERMISSIONS.items():
            for domain, actions in domains.items():
                for action in actions:
                    existing = db.scalar(
                        select(RoleAuthorization.id).where(
                            RoleAuthorization.organization_id.is_(None),
                            RoleAuthorization.role == role,
                            RoleAuthorization.domain == domain,
                            RoleAuthorization.action == action,
                        )
                    )
                    if existing is not None:
                        continue
                    db.add(
                        RoleAuthorization(
                            organization_id=None,
                            role=role,
                            domain=domain,
                            action=action,
                            is_allowed=True,
                        )
                    )
        db.commit()

    @staticmethod
    def _db_allowed(db: Session, role: str, domain: str, action: str, organization_id=None) -> bool | None:
        query = select(RoleAuthorization).where(
            RoleAuthorization.role == role,
            RoleAuthorization.domain == domain,
            RoleAuthorization.action == action,
        )
        if organization_id is not None:
            query = query.where(
                (RoleAuthorization.organization_id == organization_id) | (RoleAuthorization.organization_id.is_(None))
            )
        row = db.scalar(query.order_by(RoleAuthorization.organization_id.desc().nullslast(), RoleAuthorization.created_at.desc()))
        if row is None:
            return None
        return bool(row.is_allowed)
