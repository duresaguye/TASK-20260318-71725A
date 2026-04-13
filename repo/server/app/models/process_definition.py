import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class ProcessDefinition(Base):
    __tablename__ = "process_definitions"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", "version", name="uq_process_def_org_name_version"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    workflow_family = Column(String(64), nullable=False, default="operations", index=True)
    workflow_type = Column(String(64), nullable=False, default="resource_application", index=True)
    version = Column(Integer, nullable=False, default=1)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    steps = Column(JSON, nullable=False)
    reminders_enabled = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    organization = relationship("Organization")
    instances = relationship("ProcessInstance", back_populates="process_definition")
