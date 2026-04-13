import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class DataImportBatch(Base):
    __tablename__ = "data_import_batches"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    entity_type = Column(String(64), nullable=False, index=True)
    source_system = Column(String(64), nullable=False, default="import", server_default="import")
    status = Column(String(32), nullable=False, default="completed", server_default="completed")
    total_rows = Column(Integer, nullable=False, default=0, server_default="0")
    valid_rows = Column(Integer, nullable=False, default=0, server_default="0")
    invalid_rows = Column(Integer, nullable=False, default=0, server_default="0")
    accepted_rows = Column(Integer, nullable=False, default=0, server_default="0")
    rejected_rows = Column(Integer, nullable=False, default=0, server_default="0")
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)

    errors = relationship("DataImportError", back_populates="batch", cascade="all, delete-orphan")
