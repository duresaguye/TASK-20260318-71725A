import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, JSON, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class DataQualityRule(Base):
    __tablename__ = "data_quality_rules"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "entity_type",
            "field_name",
            "rule_type",
            name="uq_data_quality_rule_org_entity_field_type",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    entity_type = Column(String(64), nullable=False, index=True)
    field_name = Column(String(128), nullable=False)
    rule_type = Column(String(32), nullable=False, index=True)
    config = Column(JSON, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
