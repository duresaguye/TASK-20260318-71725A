import uuid

from sqlalchemy import Column, DateTime, ForeignKey, JSON, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class OperationalMetricSnapshot(Base):
    __tablename__ = "operational_metric_snapshots"
    __table_args__ = (
        UniqueConstraint("organization_id", "metric_key", "snapshot_date", name="uq_metric_snapshot_org_key_date"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    metric_key = Column(String(128), nullable=False, index=True)
    metric_value = Column(JSON, nullable=False)
    snapshot_date = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
