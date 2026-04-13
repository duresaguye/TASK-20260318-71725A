import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class ProcessInstance(Base):
    __tablename__ = "process_instances"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    process_definition_id = Column(UUID(as_uuid=True), ForeignKey("process_definitions.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    started_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    business_number = Column(String(128), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="in_progress")
    current_step_index = Column(Integer, nullable=False, default=0)
    payload = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    process_definition = relationship("ProcessDefinition", back_populates="instances")
    tasks = relationship("WorkflowTask", back_populates="process_instance")
