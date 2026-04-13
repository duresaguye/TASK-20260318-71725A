import uuid

from sqlalchemy import Column, DateTime, ForeignKey, JSON, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "user_id",
            "scope",
            "key_hash",
            name="uq_idempotency_keys_org_user_scope_key",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    scope = Column(String(64), nullable=False, index=True)
    key_hash = Column(String(128), nullable=False, index=True)
    request_hash = Column(String(128), nullable=False)
    resource_type = Column(String(64), nullable=True, index=True)
    resource_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    response_payload = Column(JSON, nullable=True)
    status = Column(String(32), nullable=False, default="pending", server_default="pending", index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
