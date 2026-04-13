from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


ValidationType = Literal["missing", "duplicate", "out_of_bounds"]
RuleType = Literal["required", "range"]


class DataQualityRuleConfig(BaseModel):
    min_value: float | None = None
    max_value: float | None = None
    min_date: datetime | None = None
    max_date: datetime | None = None


class DataQualityRuleInput(BaseModel):
    field_name: str = Field(min_length=1, max_length=128)
    rule_type: RuleType
    config: DataQualityRuleConfig | None = None


class DataImportRequest(BaseModel):
    entity_type: str = Field(min_length=1, max_length=64)
    rows: list[dict[str, Any]] = Field(min_length=1)
    reject_invalid_records: bool = False
    source_system: str = Field(default="import", min_length=1, max_length=64)
    rules: list[DataQualityRuleInput] = Field(default_factory=list)
    persist_rules: bool = False


class DataImportBatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    entity_type: str
    source_system: str
    status: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    accepted_rows: int
    rejected_rows: int
    metadata_json: dict[str, Any] | None
    created_at: datetime


class DataImportErrorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    batch_id: UUID
    organization_id: UUID
    row_number: int
    validation_type: str
    field_name: str | None
    error_reason: str
    row_data: dict[str, Any] | None
    created_at: datetime


class DataImportErrorListOut(BaseModel):
    batch_id: UUID
    errors: list[DataImportErrorOut]


class DataRollbackOut(BaseModel):
    entity_type: str
    entity_id: UUID
    restored_version: int
    rollback_version: int
    status: str


class DataLineageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    entity_type: str
    entity_id: UUID
    source_system: str
    transformation_step: str
    created_by: UUID | None
    metadata_json: dict[str, Any] | None
    created_at: datetime
