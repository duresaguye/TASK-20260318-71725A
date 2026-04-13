import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class DataDictionary(Base):
    __tablename__ = "data_dictionaries"
    __table_args__ = (
        UniqueConstraint("organization_id", "domain", "field_name", name="uq_data_dictionary_org_domain_field"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    domain = Column(String(64), nullable=False, index=True)
    field_name = Column(String(128), nullable=False, index=True)
    description = Column(Text, nullable=False)
    data_type = Column(String(64), nullable=False)
    is_sensitive = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
