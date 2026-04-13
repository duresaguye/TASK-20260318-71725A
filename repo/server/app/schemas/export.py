from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


ExportType = Literal["tasks", "workflows", "users", "analytics"]
ExportFormat = Literal["json", "csv"]
ExportStatus = Literal["pending", "processing", "completed", "failed"]


class ExportJobCreate(BaseModel):
    export_type: ExportType
    fields: list[str] | None = None
    filters: dict[str, Any] | None = None
    file_format: ExportFormat = "json"


class ExportJobOut(BaseModel):
    id: UUID
    organization_id: UUID
    requested_by: UUID | None
    status: ExportStatus
    export_type: ExportType
    filters: dict[str, Any] | None
    requested_fields: list[str] | None
    file_format: ExportFormat
    file_name: str | None
    file_size: int | None
    created_at: datetime
    completed_at: datetime | None


class ExportDownloadOut(BaseModel):
    file_name: str
    content_type: str
    content: str
