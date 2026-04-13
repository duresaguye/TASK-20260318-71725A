import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class DataImportError(Base):
    __tablename__ = "data_import_errors"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id = Column(UUID(as_uuid=True), ForeignKey("data_import_batches.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    row_number = Column(Integer, nullable=False)
    validation_type = Column(String(32), nullable=False, index=True)
    field_name = Column(String(128), nullable=True)
    error_reason = Column(Text, nullable=False)
    row_data = Column(JSON, nullable=True)
    row_data_raw_encrypted = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)

    batch = relationship("DataImportBatch", back_populates="errors")
