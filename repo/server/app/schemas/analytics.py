from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


Granularity = Literal["daily", "weekly"]
SearchType = Literal[
    "user",
    "task",
    "workflow",
    "audit",
    "patient",
    "doctor",
    "appointment",
    "expense",
    "attendance",
    "message",
]


class WorkflowMetricsOut(BaseModel):
    total_process_instances: int
    active_processes: int
    completed_processes: int
    rejected_processes: int


class TaskMetricsOut(BaseModel):
    total_tasks: int
    pending_tasks: int
    completed_tasks: int
    overdue_tasks: int


class UserActivityMetricsOut(BaseModel):
    active_users: int | None
    total_users: int | None


class SlaMetricsOut(BaseModel):
    sla_target_hours: int = 48
    sla_met_tasks: int
    sla_breached_tasks: int
    sla_breach_rate: float
    average_completion_time_hours: float | None


class AppointmentMetricsOut(BaseModel):
    total_appointments: int | None
    scheduled_appointments: int | None
    completed_appointments: int | None
    cancelled_appointments: int | None
    no_show_appointments: int | None


class PatientMetricsOut(BaseModel):
    total_patients: int | None
    active_patients: int | None
    discharged_patients: int | None


class DoctorMetricsOut(BaseModel):
    total_doctors: int | None
    active_doctors: int | None
    on_call_doctors: int | None


class ExpenseMetricsOut(BaseModel):
    total_expenses: int | None
    approved_expenses: int | None
    pending_expenses: int | None
    total_expense_amount: float | None


class AttendanceMetricsOut(BaseModel):
    total_attendance_records: int | None
    late_arrivals: int | None
    absences: int | None
    anomaly_records: int | None


class MessageMetricsOut(BaseModel):
    total_messages: int | None
    delivered_messages: int | None
    failed_messages: int | None
    reach_rate: float | None


class HospitalMetricsOut(BaseModel):
    appointments: AppointmentMetricsOut | None = None
    patients: PatientMetricsOut | None = None
    doctors: DoctorMetricsOut | None = None
    expenses: ExpenseMetricsOut | None = None
    attendance: AttendanceMetricsOut | None = None
    messages: MessageMetricsOut | None = None


class AnalyticsDashboardOut(BaseModel):
    workflow_metrics: WorkflowMetricsOut
    task_metrics: TaskMetricsOut
    user_activity_metrics: UserActivityMetricsOut
    sla_metrics: SlaMetricsOut
    hospital_metrics: HospitalMetricsOut | None = None


class TimeSeriesPointOut(BaseModel):
    period_start: datetime
    value: int


class ApprovalTrendPointOut(BaseModel):
    period_start: datetime
    approvals: int
    rejections: int


class ActivityAnalyticsOut(BaseModel):
    granularity: Granularity
    daily_task_creation_count: list[TimeSeriesPointOut]
    daily_task_completion_count: list[TimeSeriesPointOut]
    workflow_start_frequency: list[TimeSeriesPointOut]
    approval_rejection_trends: list[ApprovalTrendPointOut]


class WorkflowResponseTimeOut(BaseModel):
    workflow_type: str
    average_response_time_hours: float | None


class SlaTrendPointOut(BaseModel):
    period_start: datetime
    overdue_tasks: int


class SlaAnalyticsOut(BaseModel):
    sla_target_hours: int = 48
    tasks_within_sla: int
    tasks_breaching_sla: int
    sla_compliance_percentage: float
    average_response_time_per_workflow_type: list[WorkflowResponseTimeOut]
    overdue_trend_over_time: list[SlaTrendPointOut]


class AnalyticsSearchResultOut(BaseModel):
    type: str
    id: UUID
    status: str | None = None
    created_at: datetime | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class AnalyticsSearchResponseOut(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[AnalyticsSearchResultOut]
