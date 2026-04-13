import uuid

from sqlalchemy import event
from sqlalchemy import Column, DateTime, ForeignKey, JSON, String, func, text
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True)
    audit_scope = Column(String(32), nullable=False, default="organization", server_default=text("'organization'"))
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    entity_type = Column(String(64), nullable=False, index=True)
    entity_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    action = Column(String(64), nullable=False, index=True)
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)


@event.listens_for(AuditLog, "before_update", propagate=True)
def prevent_audit_log_update(*_) -> None:
    raise ValueError("Audit logs are immutable and cannot be updated")


@event.listens_for(AuditLog, "before_delete", propagate=True)
def prevent_audit_log_delete(*_) -> None:
    raise ValueError("Audit logs are immutable and cannot be deleted")
