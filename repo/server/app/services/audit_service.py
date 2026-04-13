from uuid import UUID

from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog


class AuditService:
    SENSITIVE_KEYS = {
        "authorization",
        "comment",
        "current_password",
        "email",
        "filters",
        "new_password",
        "password",
        "raw_payload",
        "reset_token",
        "token",
        "username",
    }

    @staticmethod
    def log_event(
        db: Session,
        organization_id: UUID | None,
        action: str,
        entity_type: str,
        entity_id: UUID | None,
        user_id: UUID | None,
        details: dict | None = None,
        audit_scope: str | None = None,
    ) -> AuditLog:
        resolved_scope = audit_scope or ("system" if organization_id is None else "organization")
        log = AuditLog(
            organization_id=organization_id,
            audit_scope=resolved_scope,
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=AuditService._sanitize_details(details),
        )
        db.add(log)
        db.flush()
        return log

    @staticmethod
    def _sanitize_details(details: dict | None):
        if details is None:
            return None
        sanitized: dict = {}
        for key, value in details.items():
            if key.lower() in AuditService.SENSITIVE_KEYS:
                sanitized[key] = "***redacted***"
            elif isinstance(value, dict):
                sanitized[key] = AuditService._sanitize_details(value)
            elif isinstance(value, list):
                sanitized[key] = [
                    AuditService._sanitize_details(item) if isinstance(item, dict) else item
                    for item in value
                ]
            else:
                sanitized[key] = value
        return sanitized
