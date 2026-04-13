import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class AttachmentMetadata(Base):
    __tablename__ = "attachment_metadata"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "process_instance_id",
            "fingerprint_sha256",
            name="uq_attachment_org_instance_fingerprint",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    process_instance_id = Column(UUID(as_uuid=True), ForeignKey("process_instances.id", ondelete="CASCADE"), nullable=False, index=True)
    workflow_task_id = Column(UUID(as_uuid=True), ForeignKey("workflow_tasks.id", ondelete="SET NULL"), nullable=True, index=True)
    uploaded_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    business_number = Column(String(128), nullable=True, index=True)
    file_name = Column(String(255), nullable=False)
    content_type = Column(String(128), nullable=False, index=True)
    file_size = Column(Integer, nullable=False)
    fingerprint_sha256 = Column(String(64), nullable=False, index=True)
    storage_path = Column(String(1024), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
