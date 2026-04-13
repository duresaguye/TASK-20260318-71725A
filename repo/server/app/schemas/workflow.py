from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


WorkflowType = Literal["resource_application", "credit_change"]
WorkflowFamily = Literal["clinical_operations", "financial_operations"]


class StepCondition(BaseModel):
    field: str = Field(min_length=1, max_length=128)
    operator: Literal["eq", "neq", "gt", "gte", "lt", "lte", "in"]
    value: Any


class ProcessStepCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    approver_ids: list[UUID] = Field(min_length=1)
    condition: StepCondition | None = None
    parallel_approval: bool = True
    reminder_after_hours: int | None = Field(default=24, ge=1, le=47)


class ProcessDefinitionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    workflow_type: WorkflowType
    workflow_family: WorkflowFamily | None = None
    version: int = Field(default=1, ge=1)
    steps: list[ProcessStepCreate] = Field(min_length=1)
    reminders_enabled: bool = True


class ProcessDefinitionOut(BaseModel):
    id: UUID
    name: str
    workflow_family: WorkflowFamily
    workflow_type: WorkflowType
    version: int
    organization_id: UUID
    steps: list[dict[str, Any]]
    reminders_enabled: bool
    created_at: datetime


class ProcessInstanceStart(BaseModel):
    process_definition_id: UUID
    business_number: str = Field(min_length=1, max_length=128)
    payload: dict[str, Any] | None = None


class ProcessInstanceOut(BaseModel):
    id: UUID
    process_definition_id: UUID
    organization_id: UUID
    business_number: str
    status: str
    current_step_index: int
    created_at: datetime
    completed_at: datetime | None


class TaskActionRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=2000)


class TaskCommentRequest(BaseModel):
    comment: str = Field(min_length=1, max_length=4000)


class TaskOut(BaseModel):
    id: UUID
    process_instance_id: UUID
    organization_id: UUID
    step_index: int
    step_name: str
    assigned_user_id: UUID
    status: str
    decision_comment: str | None
    created_at: datetime
    acted_at: datetime | None
    sla_due_at: datetime
    reminder_due_at: datetime | None


class TaskCommentOut(BaseModel):
    id: UUID
    task_id: UUID
    organization_id: UUID
    author_user_id: UUID | None
    comment: str
    created_at: datetime


class AttachmentOut(BaseModel):
    id: UUID
    process_instance_id: UUID
    workflow_task_id: UUID | None
    file_name: str
    content_type: str
    file_size: int
    fingerprint_sha256: str
    uploaded_by: UUID | None
    created_at: datetime
