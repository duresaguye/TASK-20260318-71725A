import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class RoleAuthorization(Base):
    __tablename__ = "role_authorizations"
    __table_args__ = (
        UniqueConstraint("organization_id", "role", "domain", "action", name="uq_role_auth_org_role_domain_action"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True)
    role = Column(String(32), nullable=False, index=True)
    domain = Column(String(64), nullable=False, index=True)
    action = Column(String(64), nullable=False, index=True)
    is_allowed = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
