import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class ExportJob(Base):
    __tablename__ = "export_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    requested_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    status = Column(String(32), nullable=False, default="pending", index=True)
    export_type = Column(String(32), nullable=False, index=True)
    filters = Column(JSON, nullable=True)
    requested_fields = Column(JSON, nullable=True)
    file_format = Column(String(16), nullable=False, default="json")
    file_name = Column(String(255), nullable=True)
    file_content = Column(Text, nullable=True)
    file_size = Column(Integer, nullable=True)
    content_type = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
