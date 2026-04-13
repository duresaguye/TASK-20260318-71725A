from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException, status as http_status
from sqlalchemy import and_, cast, distinct, func, literal, or_, select, String
from sqlalchemy.orm import Session

from app.core.rbac import normalize_role
from app.core.access_policy import AccessPolicy, ResourceContext
from app.core.encryption import decrypt_string
from app.models.audit_log import AuditLog
from app.models.hospital_records import Appointment, AttendanceRecord, CommunicationMessage, Doctor, Expense, Patient
from app.models.process_definition import ProcessDefinition
from app.models.process_instance import ProcessInstance
from app.models.user import User
from app.models.workflow_task import WorkflowTask
from app.schemas.analytics import (
    AppointmentMetricsOut,
    ActivityAnalyticsOut,
    AnalyticsDashboardOut,
    AnalyticsSearchResponseOut,
    AnalyticsSearchResultOut,
    ApprovalTrendPointOut,
    AttendanceMetricsOut,
    DoctorMetricsOut,
    ExpenseMetricsOut,
    HospitalMetricsOut,
    Granularity,
    MessageMetricsOut,
    PatientMetricsOut,
    SlaAnalyticsOut,
    SlaMetricsOut,
    SlaTrendPointOut,
    TaskMetricsOut,
    TimeSeriesPointOut,
    UserActivityMetricsOut,
    WorkflowMetricsOut,
    WorkflowResponseTimeOut,
)
from app.services.response_security_service import ResponseSecurityService


class AnalyticsService:
    SLA_TARGET_HOURS = 48
    ACTIVE_USER_WINDOW_DAYS = 30
    FULL_ACCESS_ROLES = {"administrator", "auditor"}
    REVIEWER_ROLE = "reviewer"
    USER_ROLE = "general_user"
    SEARCH_ROLE_MATRIX = {
        "administrator": {"user", "task", "workflow", "audit", "patient", "doctor", "appointment", "expense", "attendance", "message"},
        "auditor": {"user", "task", "workflow", "audit", "patient", "doctor", "appointment", "expense", "attendance", "message"},
        "reviewer": {"task", "workflow", "patient", "doctor", "appointment", "expense", "attendance", "message"},
        "general_user": {"task", "workflow", "patient", "doctor", "appointment", "expense", "attendance", "message"},
    }

    @staticmethod
    def get_dashboard(db: Session, current_user: User) -> AnalyticsDashboardOut:
        org_id, role = AnalyticsService._access_context(current_user)
        AccessPolicy.require(role=current_user.role, domain="analytics", action="dashboard", db=db, organization_id=org_id)
        workflow_scope = AnalyticsService._workflow_filters(current_user=current_user)
        task_scope = AnalyticsService._task_filters(current_user=current_user)

        workflow_metrics_row = db.execute(
            select(
                func.count(ProcessInstance.id).label("total_process_instances"),
                func.count(ProcessInstance.id).filter(ProcessInstance.status == "in_progress").label("active_processes"),
                func.count(ProcessInstance.id).filter(ProcessInstance.status == "completed").label("completed_processes"),
                func.count(ProcessInstance.id).filter(ProcessInstance.status == "rejected").label("rejected_processes"),
            ).where(*workflow_scope)
        ).one()

        task_metrics_row = db.execute(
            select(
                func.count(WorkflowTask.id).label("total_tasks"),
                func.count(WorkflowTask.id).filter(WorkflowTask.status == "pending").label("pending_tasks"),
                func.count(WorkflowTask.id).filter(WorkflowTask.status == "completed").label("completed_tasks"),
                func.count(WorkflowTask.id).filter(WorkflowTask.status == "overdue").label("overdue_tasks"),
            ).where(*task_scope)
        ).one()

        if role in AnalyticsService.FULL_ACCESS_ROLES:
            active_threshold = datetime.now(timezone.utc) - timedelta(days=AnalyticsService.ACTIVE_USER_WINDOW_DAYS)
            user_activity_row = db.execute(
                select(
                    func.count(User.id).label("total_users"),
                    func.count(distinct(AuditLog.user_id))
                    .filter(
                        AuditLog.created_at >= active_threshold,
                        AuditLog.user_id.is_not(None),
                    )
                    .label("active_users"),
                )
                .select_from(User)
                .outerjoin(
                    AuditLog,
                    and_(
                        AuditLog.organization_id == User.organization_id,
                        AuditLog.user_id == User.id,
                    ),
                )
                .where(User.organization_id == org_id)
            ).one()
            user_metrics = UserActivityMetricsOut(
                active_users=int(user_activity_row.active_users or 0),
                total_users=int(user_activity_row.total_users or 0),
            )
        elif role == AnalyticsService.REVIEWER_ROLE:
            user_metrics = UserActivityMetricsOut(active_users=None, total_users=None)
        else:
            recent_activity = db.execute(
                select(func.count(AuditLog.id))
                .where(
                    AuditLog.organization_id == org_id,
                    AuditLog.user_id == current_user.id,
                    AuditLog.created_at >= datetime.now(timezone.utc) - timedelta(days=AnalyticsService.ACTIVE_USER_WINDOW_DAYS),
                )
            ).scalar_one()
            user_metrics = UserActivityMetricsOut(
                active_users=1 if recent_activity > 0 else 0,
                total_users=1,
            )

        sla_row = db.execute(
            select(
                func.count(WorkflowTask.id)
                .filter(
                    WorkflowTask.status == "completed",
                    WorkflowTask.acted_at.is_not(None),
                    WorkflowTask.acted_at <= WorkflowTask.sla_due_at,
                )
                .label("sla_met_tasks"),
                func.count(WorkflowTask.id)
                .filter(
                    or_(
                        and_(
                            WorkflowTask.status == "completed",
                            WorkflowTask.acted_at.is_not(None),
                            WorkflowTask.acted_at > WorkflowTask.sla_due_at,
                        ),
                        WorkflowTask.status == "overdue",
                    )
                )
                .label("sla_breached_tasks"),
                (
                    func.avg(
                        func.extract("epoch", WorkflowTask.acted_at - WorkflowTask.created_at) / 3600.0
                    )
                    .filter(
                        WorkflowTask.status == "completed",
                        WorkflowTask.acted_at.is_not(None),
                    )
                    .label("average_completion_time_hours")
                ),
            ).where(*task_scope)
        ).one()

        sla_met_tasks = int(sla_row.sla_met_tasks or 0)
        sla_breached_tasks = int(sla_row.sla_breached_tasks or 0)
        total_sla_tasks = sla_met_tasks + sla_breached_tasks
        sla_breach_rate = round((sla_breached_tasks / total_sla_tasks) * 100, 2) if total_sla_tasks else 0.0

        return AnalyticsDashboardOut(
            workflow_metrics=WorkflowMetricsOut(
                total_process_instances=int(workflow_metrics_row.total_process_instances or 0),
                active_processes=int(workflow_metrics_row.active_processes or 0),
                completed_processes=int(workflow_metrics_row.completed_processes or 0),
                rejected_processes=int(workflow_metrics_row.rejected_processes or 0),
            ),
            task_metrics=TaskMetricsOut(
                total_tasks=int(task_metrics_row.total_tasks or 0),
                pending_tasks=int(task_metrics_row.pending_tasks or 0),
                completed_tasks=int(task_metrics_row.completed_tasks or 0),
                overdue_tasks=int(task_metrics_row.overdue_tasks or 0),
            ),
            user_activity_metrics=user_metrics,
            sla_metrics=SlaMetricsOut(
                sla_met_tasks=sla_met_tasks,
                sla_breached_tasks=sla_breached_tasks,
                sla_breach_rate=sla_breach_rate,
                average_completion_time_hours=(
                    round(float(sla_row.average_completion_time_hours), 2)
                    if sla_row.average_completion_time_hours is not None
                    else None
                ),
            ),
            hospital_metrics=(
                AnalyticsService._hospital_dashboard_metrics(db=db, org_id=org_id, role=role)
                if role in AnalyticsService.FULL_ACCESS_ROLES
                else None
            ),
        )

    @staticmethod
    def get_activity_analytics(
        db: Session,
        current_user: User,
        start_date: datetime,
        end_date: datetime,
        granularity: Granularity,
    ) -> ActivityAnalyticsOut:
        AnalyticsService._access_context(current_user)
        AccessPolicy.require(role=current_user.role, domain="analytics", action="activity", db=db, organization_id=AnalyticsService._require_org_id(current_user))
        AnalyticsService._validate_date_range(start_date=start_date, end_date=end_date)
        task_scope = AnalyticsService._task_filters(current_user=current_user)
        workflow_scope = AnalyticsService._workflow_filters(current_user=current_user)
        audit_scope = AnalyticsService._audit_filters(current_user=current_user)
        bucket_name = AnalyticsService._bucket_name(granularity)
        created_bucket = func.date_trunc(bucket_name, WorkflowTask.created_at)
        completed_bucket = func.date_trunc(bucket_name, WorkflowTask.acted_at)
        workflow_bucket = func.date_trunc(bucket_name, ProcessInstance.created_at)
        audit_bucket = func.date_trunc(bucket_name, AuditLog.created_at)

        created_rows = db.execute(
            select(
                created_bucket.label("bucket"),
                func.count(WorkflowTask.id).label("value"),
            )
            .where(
                *task_scope,
                WorkflowTask.created_at >= start_date,
                WorkflowTask.created_at <= end_date,
            )
            .group_by(created_bucket)
            .order_by(created_bucket)
        ).all()

        completed_rows = db.execute(
            select(
                completed_bucket.label("bucket"),
                func.count(WorkflowTask.id).label("value"),
            )
            .where(
                *task_scope,
                WorkflowTask.acted_at.is_not(None),
                WorkflowTask.acted_at >= start_date,
                WorkflowTask.acted_at <= end_date,
                WorkflowTask.status == "completed",
            )
            .group_by(completed_bucket)
            .order_by(completed_bucket)
        ).all()

        workflow_rows = db.execute(
            select(
                workflow_bucket.label("bucket"),
                func.count(ProcessInstance.id).label("value"),
            )
            .where(
                *workflow_scope,
                ProcessInstance.created_at >= start_date,
                ProcessInstance.created_at <= end_date,
            )
            .group_by(workflow_bucket)
            .order_by(workflow_bucket)
        ).all()

        approval_rows = db.execute(
            select(
                audit_bucket.label("bucket"),
                func.count(AuditLog.id)
                .filter(AuditLog.action == "workflow.task.approved")
                .label("approvals"),
                func.count(AuditLog.id)
                .filter(AuditLog.action == "workflow.task.rejected")
                .label("rejections"),
            )
            .where(
                *audit_scope,
                AuditLog.created_at >= start_date,
                AuditLog.created_at <= end_date,
                AuditLog.action.in_(["workflow.task.approved", "workflow.task.rejected"]),
            )
            .group_by(audit_bucket)
            .order_by(audit_bucket)
        ).all()

        return ActivityAnalyticsOut(
            granularity=granularity,
            daily_task_creation_count=AnalyticsService._time_series(created_rows),
            daily_task_completion_count=AnalyticsService._time_series(completed_rows),
            workflow_start_frequency=AnalyticsService._time_series(workflow_rows),
            approval_rejection_trends=[
                ApprovalTrendPointOut(
                    period_start=row.bucket,
                    approvals=int(row.approvals or 0),
                    rejections=int(row.rejections or 0),
                )
                for row in approval_rows
            ],
        )

    @staticmethod
    def get_sla_analytics(
        db: Session,
        current_user: User,
        start_date: datetime,
        end_date: datetime,
        granularity: Granularity,
    ) -> SlaAnalyticsOut:
        AnalyticsService._access_context(current_user)
        AccessPolicy.require(role=current_user.role, domain="analytics", action="sla", db=db, organization_id=AnalyticsService._require_org_id(current_user))
        AnalyticsService._validate_date_range(start_date=start_date, end_date=end_date)
        task_scope = AnalyticsService._task_filters(current_user=current_user)
        bucket_name = AnalyticsService._bucket_name(granularity)

        summary_row = db.execute(
            select(
                func.count(WorkflowTask.id)
                .filter(
                    WorkflowTask.status == "completed",
                    WorkflowTask.acted_at.is_not(None),
                    WorkflowTask.acted_at <= WorkflowTask.sla_due_at,
                )
                .label("tasks_within_sla"),
                func.count(WorkflowTask.id)
                .filter(
                    or_(
                        and_(
                            WorkflowTask.status == "completed",
                            WorkflowTask.acted_at.is_not(None),
                            WorkflowTask.acted_at > WorkflowTask.sla_due_at,
                        ),
                        WorkflowTask.status == "overdue",
                    )
                )
                .label("tasks_breaching_sla"),
            )
            .where(
                *task_scope,
                WorkflowTask.created_at >= start_date,
                WorkflowTask.created_at <= end_date,
            )
        ).one()

        avg_rows = db.execute(
            select(
                ProcessDefinition.name.label("workflow_type"),
                func.avg(func.extract("epoch", WorkflowTask.acted_at - WorkflowTask.created_at) / 3600.0).label(
                    "average_response_time_hours"
                ),
            )
            .select_from(WorkflowTask)
            .join(ProcessInstance, ProcessInstance.id == WorkflowTask.process_instance_id)
            .join(ProcessDefinition, ProcessDefinition.id == ProcessInstance.process_definition_id)
            .where(
                *task_scope,
                WorkflowTask.status == "completed",
                WorkflowTask.acted_at.is_not(None),
                WorkflowTask.created_at >= start_date,
                WorkflowTask.created_at <= end_date,
            )
            .group_by(ProcessDefinition.name)
            .order_by(ProcessDefinition.name.asc())
        ).all()

        overdue_rows = db.execute(
            select(
                func.date_trunc(bucket_name, WorkflowTask.created_at).label("bucket"),
                func.count(WorkflowTask.id)
                .filter(
                    or_(
                        WorkflowTask.status == "overdue",
                        and_(
                            WorkflowTask.status == "completed",
                            WorkflowTask.acted_at.is_not(None),
                            WorkflowTask.acted_at > WorkflowTask.sla_due_at,
                        ),
                    )
                )
                .label("overdue_tasks"),
            )
            .where(
                *task_scope,
                WorkflowTask.created_at >= start_date,
                WorkflowTask.created_at <= end_date,
            )
            .group_by(func.date_trunc(bucket_name, WorkflowTask.created_at))
            .order_by(func.date_trunc(bucket_name, WorkflowTask.created_at))
        ).all()

        tasks_within_sla = int(summary_row.tasks_within_sla or 0)
        tasks_breaching_sla = int(summary_row.tasks_breaching_sla or 0)
        total = tasks_within_sla + tasks_breaching_sla

        return SlaAnalyticsOut(
            tasks_within_sla=tasks_within_sla,
            tasks_breaching_sla=tasks_breaching_sla,
            sla_compliance_percentage=round((tasks_within_sla / total) * 100, 2) if total else 0.0,
            average_response_time_per_workflow_type=[
                WorkflowResponseTimeOut(
                    workflow_type=row.workflow_type,
                    average_response_time_hours=(
                        round(float(row.average_response_time_hours), 2)
                        if row.average_response_time_hours is not None
                        else None
                    ),
                )
                for row in avg_rows
            ],
            overdue_trend_over_time=[
                SlaTrendPointOut(period_start=row.bucket, overdue_tasks=int(row.overdue_tasks or 0))
                for row in overdue_rows
            ],
        )

    @staticmethod
    def search(
        db: Session,
        current_user: User,
        search_type: str,
        status_filter: str | None,
        user_id: str | None,
        assignee_id: str | None,
        start_date: datetime | None,
        end_date: datetime | None,
        keyword: str | None,
        limit: int,
        offset: int,
    ) -> AnalyticsSearchResponseOut:
        org_id, role = AnalyticsService._access_context(current_user)
        AccessPolicy.require(role=current_user.role, domain="analytics", action="search", db=db, organization_id=org_id)
        AnalyticsService._authorize_search_type(role=role, search_type=search_type)
        if start_date is not None and end_date is not None:
            AnalyticsService._validate_date_range(start_date=start_date, end_date=end_date)

        parsed_user_id = AnalyticsService._parse_uuid(user_id) if user_id else None
        parsed_assignee_id = AnalyticsService._parse_uuid(assignee_id) if assignee_id else None

        if search_type == "user":
            query = AnalyticsService._search_users_query(
                current_user=current_user,
                org_id=org_id,
                status_filter=status_filter,
                user_id=parsed_user_id,
                start_date=start_date,
                end_date=end_date,
                keyword=keyword,
            )
        elif search_type == "task":
            query = AnalyticsService._search_tasks_query(
                current_user=current_user,
                org_id=org_id,
                status_filter=status_filter,
                user_id=parsed_user_id,
                assignee_id=parsed_assignee_id,
                start_date=start_date,
                end_date=end_date,
                keyword=keyword,
            )
        elif search_type == "workflow":
            query = AnalyticsService._search_workflows_query(
                current_user=current_user,
                org_id=org_id,
                status_filter=status_filter,
                user_id=parsed_user_id,
                start_date=start_date,
                end_date=end_date,
                keyword=keyword,
            )
        else:
            if search_type == "audit":
                query = AnalyticsService._search_audits_query(
                    current_user=current_user,
                    org_id=org_id,
                    status_filter=status_filter,
                    user_id=parsed_user_id,
                    start_date=start_date,
                    end_date=end_date,
                    keyword=keyword,
                )
            elif search_type == "patient":
                query = AnalyticsService._search_patients_query(
                    current_user=current_user,
                    org_id=org_id,
                    status_filter=status_filter,
                    user_id=parsed_user_id,
                    start_date=start_date,
                    end_date=end_date,
                    keyword=keyword,
                )
            elif search_type == "doctor":
                query = AnalyticsService._search_doctors_query(
                    current_user=current_user,
                    org_id=org_id,
                    status_filter=status_filter,
                    user_id=parsed_user_id,
                    start_date=start_date,
                    end_date=end_date,
                    keyword=keyword,
                )
            elif search_type == "appointment":
                query = AnalyticsService._search_appointments_query(
                    current_user=current_user,
                    org_id=org_id,
                    status_filter=status_filter,
                    user_id=parsed_user_id,
                    assignee_id=parsed_assignee_id,
                    start_date=start_date,
                    end_date=end_date,
                    keyword=keyword,
                )
            elif search_type == "expense":
                query = AnalyticsService._search_expenses_query(
                    current_user=current_user,
                    org_id=org_id,
                    status_filter=status_filter,
                    user_id=parsed_user_id,
                    assignee_id=parsed_assignee_id,
                    start_date=start_date,
                    end_date=end_date,
                    keyword=keyword,
                )
            elif search_type == "attendance":
                query = AnalyticsService._search_attendance_query(
                    current_user=current_user,
                    org_id=org_id,
                    status_filter=status_filter,
                    user_id=parsed_user_id,
                    assignee_id=parsed_assignee_id,
                    start_date=start_date,
                    end_date=end_date,
                    keyword=keyword,
                )
            else:
                query = AnalyticsService._search_messages_query(
                    current_user=current_user,
                    org_id=org_id,
                    status_filter=status_filter,
                    user_id=parsed_user_id,
                    assignee_id=parsed_assignee_id,
                    start_date=start_date,
                    end_date=end_date,
                    keyword=keyword,
                )

        total = db.execute(select(func.count()).select_from(query.subquery())).scalar_one()
        rows = db.execute(query.limit(limit).offset(offset)).mappings().all()

        return AnalyticsSearchResponseOut(
            total=int(total),
            limit=limit,
            offset=offset,
            items=[
                # Search payloads are built from ORM-backed values, so decrypt the
                # sensitive fields here before role-based masking runs.
                # This keeps the downstream response readable for authorized roles.
                AnalyticsSearchResultOut(
                    type=row["type"],
                    id=row["id"],
                    status=row.get("status"),
                    created_at=row.get("created_at"),
                    data=ResponseSecurityService.mask_search_data(
                        current_role=current_user.role,
                        data=AnalyticsService._decrypt_search_data(
                            search_type=search_type,
                            data=row.get("data") or {},
                        ),
                    ),
                )
                for row in rows
            ],
        )

    @staticmethod
    def _search_users_query(
        current_user: User,
        org_id: UUID,
        status_filter: str | None,
        user_id: UUID | None,
        start_date: datetime | None,
        end_date: datetime | None,
        keyword: str | None,
    ):
        role = normalize_role(current_user.role)
        if role not in AnalyticsService.FULL_ACCESS_ROLES:
            raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN, detail="User analytics are not permitted")

        query = select(
            literal("user").label("type"),
            User.id.label("id"),
            cast(User.is_active, String).label("status"),
            User.created_at.label("created_at"),
            func.json_build_object(
                "username",
                User.username,
                "role",
                User.role,
                "organization_id",
                cast(User.organization_id, String),
                "is_active",
                User.is_active,
            ).label("data"),
        ).where(User.organization_id == org_id)

        if status_filter is not None:
            normalized_status = status_filter.lower()
            if normalized_status in {"active", "inactive"}:
                query = query.where(User.is_active.is_(normalized_status == "active"))
        if user_id is not None:
            query = query.where(User.id == user_id)
        if start_date is not None:
            query = query.where(User.created_at >= start_date)
        if end_date is not None:
            query = query.where(User.created_at <= end_date)
        if keyword:
            query = query.where(
                or_(
                    User.username.ilike(f"%{keyword}%"),
                    User.role.ilike(f"%{keyword}%"),
                )
            )

        return query.order_by(User.created_at.desc(), User.id.desc())

    @staticmethod
    def _search_tasks_query(
        current_user: User,
        org_id: UUID,
        status_filter: str | None,
        user_id: UUID | None,
        assignee_id: UUID | None,
        start_date: datetime | None,
        end_date: datetime | None,
        keyword: str | None,
    ):
        query = select(
            literal("task").label("type"),
            WorkflowTask.id.label("id"),
            WorkflowTask.status.label("status"),
            WorkflowTask.created_at.label("created_at"),
            func.json_build_object(
                "process_instance_id",
                cast(WorkflowTask.process_instance_id, String),
                "assigned_user_id",
                cast(WorkflowTask.assigned_user_id, String),
                "step_index",
                WorkflowTask.step_index,
                "step_name",
                WorkflowTask.step_name,
                "acted_at",
                WorkflowTask.acted_at,
                "sla_due_at",
                WorkflowTask.sla_due_at,
            ).label("data"),
        ).where(*AnalyticsService._task_filters(current_user=current_user))

        if status_filter is not None:
            query = query.where(WorkflowTask.status == status_filter)
        if user_id is not None:
            query = query.where(WorkflowTask.assigned_user_id == user_id)
        if assignee_id is not None:
            query = query.where(WorkflowTask.assigned_user_id == assignee_id)
        if start_date is not None:
            query = query.where(WorkflowTask.created_at >= start_date)
        if end_date is not None:
            query = query.where(WorkflowTask.created_at <= end_date)
        if keyword:
            query = query.where(
                or_(
                    WorkflowTask.step_name.ilike(f"%{keyword}%"),
                    WorkflowTask.decision_comment.ilike(f"%{keyword}%"),
                    cast(WorkflowTask.process_instance_id, String).ilike(f"%{keyword}%"),
                )
            )

        return query.order_by(WorkflowTask.created_at.desc(), WorkflowTask.id.desc())

    @staticmethod
    def _search_workflows_query(
        current_user: User,
        org_id: UUID,
        status_filter: str | None,
        user_id: UUID | None,
        start_date: datetime | None,
        end_date: datetime | None,
        keyword: str | None,
    ):
        query = (
            select(
                literal("workflow").label("type"),
                ProcessInstance.id.label("id"),
                ProcessInstance.status.label("status"),
                ProcessInstance.created_at.label("created_at"),
                func.json_build_object(
                    "process_definition_id",
                    cast(ProcessInstance.process_definition_id, String),
                    "workflow_name",
                    ProcessDefinition.name,
                    "started_by_user_id",
                    cast(ProcessInstance.started_by_user_id, String),
                    "current_step_index",
                    ProcessInstance.current_step_index,
                    "completed_at",
                    ProcessInstance.completed_at,
                ).label("data"),
            )
            .select_from(ProcessInstance)
            .join(ProcessDefinition, ProcessDefinition.id == ProcessInstance.process_definition_id)
            .where(*AnalyticsService._workflow_filters(current_user=current_user))
        )

        if status_filter is not None:
            query = query.where(ProcessInstance.status == status_filter)
        if user_id is not None:
            query = query.where(ProcessInstance.started_by_user_id == user_id)
        if start_date is not None:
            query = query.where(ProcessInstance.created_at >= start_date)
        if end_date is not None:
            query = query.where(ProcessInstance.created_at <= end_date)
        if keyword:
            query = query.where(
                or_(
                    ProcessDefinition.name.ilike(f"%{keyword}%"),
                    cast(ProcessInstance.id, String).ilike(f"%{keyword}%"),
                )
            )

        return query.order_by(ProcessInstance.created_at.desc(), ProcessInstance.id.desc())

    @staticmethod
    def _search_audits_query(
        current_user: User,
        org_id: UUID,
        status_filter: str | None,
        user_id: UUID | None,
        start_date: datetime | None,
        end_date: datetime | None,
        keyword: str | None,
    ):
        role = normalize_role(current_user.role)
        if role not in AnalyticsService.FULL_ACCESS_ROLES:
            raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN, detail="Audit analytics are not permitted")

        query = select(
            literal("audit").label("type"),
            AuditLog.id.label("id"),
            AuditLog.action.label("status"),
            AuditLog.created_at.label("created_at"),
            func.json_build_object(
                "entity_type",
                AuditLog.entity_type,
                "entity_id",
                cast(AuditLog.entity_id, String),
                "user_id",
                cast(AuditLog.user_id, String),
                "details",
                AuditLog.details,
            ).label("data"),
        ).where(AuditLog.organization_id == org_id)

        if status_filter is not None:
            query = query.where(AuditLog.action == status_filter)
        if user_id is not None:
            query = query.where(AuditLog.user_id == user_id)
        if start_date is not None:
            query = query.where(AuditLog.created_at >= start_date)
        if end_date is not None:
            query = query.where(AuditLog.created_at <= end_date)
        if keyword:
            query = query.where(
                or_(
                    AuditLog.action.ilike(f"%{keyword}%"),
                    AuditLog.entity_type.ilike(f"%{keyword}%"),
                    cast(AuditLog.details, String).ilike(f"%{keyword}%"),
                )
            )

        return query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())

    @staticmethod
    def _search_patients_query(
        current_user: User,
        org_id: UUID,
        status_filter: str | None,
        user_id: UUID | None,
        start_date: datetime | None,
        end_date: datetime | None,
        keyword: str | None,
    ):
        query = select(
            literal("patient").label("type"),
            Patient.id.label("id"),
            Patient.status.label("status"),
            Patient.created_at.label("created_at"),
            func.json_build_object(
                "medical_record_number",
                Patient.medical_record_number,
                "full_name",
                Patient.full_name,
                "email",
                Patient.email,
                "phone",
                Patient.phone,
                "created_by_user_id",
                cast(Patient.created_by_user_id, String),
            ).label("data"),
        ).where(Patient.organization_id == org_id)

        if status_filter is not None:
            query = query.where(Patient.status == status_filter)
        if user_id is not None:
            query = query.where(Patient.created_by_user_id == user_id)
        if start_date is not None:
            query = query.where(Patient.created_at >= start_date)
        if end_date is not None:
            query = query.where(Patient.created_at <= end_date)
        if keyword:
            query = query.where(
                or_(
                    Patient.medical_record_number_search.ilike(f"%{keyword}%"),
                    Patient.full_name.ilike(f"%{keyword}%"),
                    Patient.email_search.ilike(f"%{keyword}%"),
                    Patient.phone_search.ilike(f"%{keyword}%"),
                )
            )

        return query.order_by(Patient.created_at.desc(), Patient.id.desc())

    @staticmethod
    def _search_doctors_query(
        current_user: User,
        org_id: UUID,
        status_filter: str | None,
        user_id: UUID | None,
        start_date: datetime | None,
        end_date: datetime | None,
        keyword: str | None,
    ):
        query = select(
            literal("doctor").label("type"),
            Doctor.id.label("id"),
            cast(Doctor.is_active, String).label("status"),
            Doctor.created_at.label("created_at"),
            func.json_build_object(
                "employee_number",
                Doctor.employee_number,
                "full_name",
                Doctor.full_name,
                "specialty",
                Doctor.specialty,
                "is_on_call",
                Doctor.is_on_call,
                "created_by_user_id",
                cast(Doctor.created_by_user_id, String),
            ).label("data"),
        ).where(Doctor.organization_id == org_id)

        if status_filter is not None:
            normalized_status = status_filter.lower()
            if normalized_status in {"active", "inactive"}:
                query = query.where(Doctor.is_active.is_(normalized_status == "active"))
        if user_id is not None:
            query = query.where(Doctor.created_by_user_id == user_id)
        if start_date is not None:
            query = query.where(Doctor.created_at >= start_date)
        if end_date is not None:
            query = query.where(Doctor.created_at <= end_date)
        if keyword:
            query = query.where(
                or_(
                    Doctor.employee_number.ilike(f"%{keyword}%"),
                    Doctor.full_name.ilike(f"%{keyword}%"),
                    Doctor.specialty.ilike(f"%{keyword}%"),
                )
            )

        return query.order_by(Doctor.created_at.desc(), Doctor.id.desc())

    @staticmethod
    def _search_appointments_query(
        current_user: User,
        org_id: UUID,
        status_filter: str | None,
        user_id: UUID | None,
        assignee_id: UUID | None,
        start_date: datetime | None,
        end_date: datetime | None,
        keyword: str | None,
    ):
        query = (
            select(
                literal("appointment").label("type"),
                Appointment.id.label("id"),
                Appointment.status.label("status"),
                Appointment.created_at.label("created_at"),
                func.json_build_object(
                    "appointment_number",
                    Appointment.appointment_number,
                    "patient_id",
                    cast(Appointment.patient_id, String),
                    "doctor_id",
                    cast(Appointment.doctor_id, String),
                    "department",
                    Appointment.department,
                    "channel",
                    Appointment.channel,
                    "scheduled_at",
                    Appointment.scheduled_at,
                    "completed_at",
                    Appointment.completed_at,
                ).label("data"),
            )
            .select_from(Appointment)
            .where(Appointment.organization_id == org_id)
        )

        if status_filter is not None:
            query = query.where(Appointment.status == status_filter)
        if user_id is not None:
            query = query.where(Appointment.created_by_user_id == user_id)
        if assignee_id is not None:
            query = query.where(Appointment.doctor_id == assignee_id)
        if start_date is not None:
            query = query.where(Appointment.created_at >= start_date)
        if end_date is not None:
            query = query.where(Appointment.created_at <= end_date)
        if keyword:
            query = query.where(
                or_(
                    Appointment.appointment_number_search.ilike(f"%{keyword}%"),
                    Appointment.department.ilike(f"%{keyword}%"),
                    Appointment.reason.ilike(f"%{keyword}%"),
                    Appointment.channel.ilike(f"%{keyword}%"),
                    cast(Appointment.patient_id, String).ilike(f"%{keyword}%"),
                    cast(Appointment.doctor_id, String).ilike(f"%{keyword}%"),
                )
            )

        return query.order_by(Appointment.created_at.desc(), Appointment.id.desc())

    @staticmethod
    def _search_expenses_query(
        current_user: User,
        org_id: UUID,
        status_filter: str | None,
        user_id: UUID | None,
        assignee_id: UUID | None,
        start_date: datetime | None,
        end_date: datetime | None,
        keyword: str | None,
    ):
        query = select(
            literal("expense").label("type"),
            Expense.id.label("id"),
            Expense.status.label("status"),
            Expense.created_at.label("created_at"),
            func.json_build_object(
                "expense_number",
                Expense.expense_number,
                "category",
                Expense.category,
                "amount",
                Expense.amount,
                "patient_id",
                cast(Expense.patient_id, String),
                "doctor_id",
                cast(Expense.doctor_id, String),
                "created_by_user_id",
                cast(Expense.created_by_user_id, String),
            ).label("data"),
        ).where(Expense.organization_id == org_id)

        if status_filter is not None:
            query = query.where(Expense.status == status_filter)
        if user_id is not None:
            query = query.where(Expense.created_by_user_id == user_id)
        if assignee_id is not None:
            query = query.where(Expense.doctor_id == assignee_id)
        if start_date is not None:
            query = query.where(Expense.created_at >= start_date)
        if end_date is not None:
            query = query.where(Expense.created_at <= end_date)
        if keyword:
            query = query.where(
                or_(
                    Expense.expense_number_search.ilike(f"%{keyword}%"),
                    Expense.category.ilike(f"%{keyword}%"),
                    cast(Expense.amount, String).ilike(f"%{keyword}%"),
                )
            )

        return query.order_by(Expense.created_at.desc(), Expense.id.desc())

    @staticmethod
    def _search_attendance_query(
        current_user: User,
        org_id: UUID,
        status_filter: str | None,
        user_id: UUID | None,
        assignee_id: UUID | None,
        start_date: datetime | None,
        end_date: datetime | None,
        keyword: str | None,
    ):
        query = select(
            literal("attendance").label("type"),
            AttendanceRecord.id.label("id"),
            AttendanceRecord.status.label("status"),
            AttendanceRecord.recorded_at.label("created_at"),
            func.json_build_object(
                "attendance_number",
                AttendanceRecord.attendance_number,
                "user_id",
                cast(AttendanceRecord.user_id, String),
                "shift_name",
                AttendanceRecord.shift_name,
                "anomaly_type",
                AttendanceRecord.anomaly_type,
                "location",
                AttendanceRecord.location,
                "check_in_at",
                AttendanceRecord.check_in_at,
                "check_out_at",
                AttendanceRecord.check_out_at,
            ).label("data"),
        ).where(AttendanceRecord.organization_id == org_id)

        if status_filter is not None:
            query = query.where(AttendanceRecord.status == status_filter)
        if user_id is not None:
            query = query.where(AttendanceRecord.created_by_user_id == user_id)
        if assignee_id is not None:
            query = query.where(AttendanceRecord.user_id == assignee_id)
        if start_date is not None:
            query = query.where(AttendanceRecord.recorded_at >= start_date)
        if end_date is not None:
            query = query.where(AttendanceRecord.recorded_at <= end_date)
        if keyword:
            query = query.where(
                or_(
                    AttendanceRecord.attendance_number_search.ilike(f"%{keyword}%"),
                    AttendanceRecord.shift_name.ilike(f"%{keyword}%"),
                    AttendanceRecord.anomaly_type.ilike(f"%{keyword}%"),
                    AttendanceRecord.location.ilike(f"%{keyword}%"),
                )
            )

        return query.order_by(AttendanceRecord.recorded_at.desc(), AttendanceRecord.id.desc())

    @staticmethod
    def _search_messages_query(
        current_user: User,
        org_id: UUID,
        status_filter: str | None,
        user_id: UUID | None,
        assignee_id: UUID | None,
        start_date: datetime | None,
        end_date: datetime | None,
        keyword: str | None,
    ):
        query = select(
            literal("message").label("type"),
            CommunicationMessage.id.label("id"),
            CommunicationMessage.delivery_status.label("status"),
            CommunicationMessage.created_at.label("created_at"),
            func.json_build_object(
                "message_number",
                CommunicationMessage.message_number,
                "channel",
                CommunicationMessage.channel,
                "campaign_name",
                CommunicationMessage.campaign_name,
                "recipient_user_id",
                cast(CommunicationMessage.recipient_user_id, String),
                "created_by_user_id",
                cast(CommunicationMessage.created_by_user_id, String),
                "reach_status",
                CommunicationMessage.reach_status,
                "sent_at",
                CommunicationMessage.sent_at,
                "delivered_at",
                CommunicationMessage.delivered_at,
                "opened_at",
                CommunicationMessage.opened_at,
            ).label("data"),
        ).where(CommunicationMessage.organization_id == org_id)

        if status_filter is not None:
            query = query.where(CommunicationMessage.delivery_status == status_filter)
        if user_id is not None:
            query = query.where(CommunicationMessage.created_by_user_id == user_id)
        if assignee_id is not None:
            query = query.where(CommunicationMessage.recipient_user_id == assignee_id)
        if start_date is not None:
            query = query.where(CommunicationMessage.created_at >= start_date)
        if end_date is not None:
            query = query.where(CommunicationMessage.created_at <= end_date)
        if keyword:
            query = query.where(
                or_(
                    CommunicationMessage.message_number_search.ilike(f"%{keyword}%"),
                    CommunicationMessage.channel.ilike(f"%{keyword}%"),
                    CommunicationMessage.campaign_name.ilike(f"%{keyword}%"),
                    CommunicationMessage.reach_status.ilike(f"%{keyword}%"),
                )
            )

        return query.order_by(CommunicationMessage.created_at.desc(), CommunicationMessage.id.desc())

    @staticmethod
    def _workflow_filters(current_user: User) -> list:
        filters = [ProcessInstance.organization_id == AnalyticsService._require_org_id(current_user)]
        if normalize_role(current_user.role) == AnalyticsService.USER_ROLE:
            filters.append(ProcessInstance.started_by_user_id == current_user.id)
        return filters

    @staticmethod
    def _task_filters(current_user: User) -> list:
        filters = [WorkflowTask.organization_id == AnalyticsService._require_org_id(current_user)]
        if normalize_role(current_user.role) == AnalyticsService.USER_ROLE:
            filters.append(WorkflowTask.assigned_user_id == current_user.id)
        return filters

    @staticmethod
    def _audit_filters(current_user: User) -> list:
        filters = [AuditLog.organization_id == AnalyticsService._require_org_id(current_user)]
        if normalize_role(current_user.role) == AnalyticsService.USER_ROLE:
            filters.append(AuditLog.user_id == current_user.id)
        return filters

    @staticmethod
    def _hospital_dashboard_metrics(db: Session, org_id: UUID, role: str) -> HospitalMetricsOut:
        patient_row = db.execute(
            select(
                func.count(Patient.id).label("total_patients"),
                func.count(Patient.id).filter(Patient.status == "active").label("active_patients"),
                func.count(Patient.id).filter(Patient.status == "discharged").label("discharged_patients"),
            ).where(Patient.organization_id == org_id)
        ).one()

        doctor_row = db.execute(
            select(
                func.count(Doctor.id).label("total_doctors"),
                func.count(Doctor.id).filter(Doctor.is_active.is_(True)).label("active_doctors"),
                func.count(Doctor.id).filter(Doctor.is_on_call.is_(True)).label("on_call_doctors"),
            ).where(Doctor.organization_id == org_id)
        ).one()

        appointment_row = db.execute(
            select(
                func.count(Appointment.id).label("total_appointments"),
                func.count(Appointment.id).filter(Appointment.status == "scheduled").label("scheduled_appointments"),
                func.count(Appointment.id).filter(Appointment.status == "completed").label("completed_appointments"),
                func.count(Appointment.id).filter(Appointment.status == "cancelled").label("cancelled_appointments"),
                func.count(Appointment.id).filter(Appointment.status == "no_show").label("no_show_appointments"),
            ).where(Appointment.organization_id == org_id)
        ).one()

        expense_row = db.execute(
            select(
                func.count(Expense.id).label("total_expenses"),
                func.count(Expense.id).filter(Expense.status == "approved").label("approved_expenses"),
                func.count(Expense.id).filter(Expense.status == "pending").label("pending_expenses"),
                func.coalesce(func.sum(Expense.amount), 0).label("total_expense_amount"),
            ).where(Expense.organization_id == org_id)
        ).one()

        attendance_row = db.execute(
            select(
                func.count(AttendanceRecord.id).label("total_attendance_records"),
                func.count(AttendanceRecord.id).filter(AttendanceRecord.status == "late").label("late_arrivals"),
                func.count(AttendanceRecord.id).filter(AttendanceRecord.status == "absent").label("absences"),
                func.count(AttendanceRecord.id).filter(AttendanceRecord.anomaly_type.is_not(None)).label("anomaly_records"),
            ).where(AttendanceRecord.organization_id == org_id)
        ).one()

        message_row = db.execute(
            select(
                func.count(CommunicationMessage.id).label("total_messages"),
                func.count(CommunicationMessage.id).filter(
                    CommunicationMessage.delivery_status == "delivered"
                ).label("delivered_messages"),
                func.count(CommunicationMessage.id).filter(
                    CommunicationMessage.delivery_status.in_(["failed", "bounced"])
                ).label("failed_messages"),
            ).where(CommunicationMessage.organization_id == org_id)
        ).one()

        total_messages = int(message_row.total_messages or 0)
        delivered_messages = int(message_row.delivered_messages or 0)
        reach_rate = round((delivered_messages / total_messages) * 100, 2) if total_messages else 0.0

        if role not in AnalyticsService.FULL_ACCESS_ROLES:
            return HospitalMetricsOut()

        return HospitalMetricsOut(
            appointments=AppointmentMetricsOut(
                total_appointments=int(appointment_row.total_appointments or 0),
                scheduled_appointments=int(appointment_row.scheduled_appointments or 0),
                completed_appointments=int(appointment_row.completed_appointments or 0),
                cancelled_appointments=int(appointment_row.cancelled_appointments or 0),
                no_show_appointments=int(appointment_row.no_show_appointments or 0),
            ),
            patients=PatientMetricsOut(
                total_patients=int(patient_row.total_patients or 0),
                active_patients=int(patient_row.active_patients or 0),
                discharged_patients=int(patient_row.discharged_patients or 0),
            ),
            doctors=DoctorMetricsOut(
                total_doctors=int(doctor_row.total_doctors or 0),
                active_doctors=int(doctor_row.active_doctors or 0),
                on_call_doctors=int(doctor_row.on_call_doctors or 0),
            ),
            expenses=ExpenseMetricsOut(
                total_expenses=int(expense_row.total_expenses or 0),
                approved_expenses=int(expense_row.approved_expenses or 0),
                pending_expenses=int(expense_row.pending_expenses or 0),
                total_expense_amount=round(float(expense_row.total_expense_amount or 0), 2),
            ),
            attendance=AttendanceMetricsOut(
                total_attendance_records=int(attendance_row.total_attendance_records or 0),
                late_arrivals=int(attendance_row.late_arrivals or 0),
                absences=int(attendance_row.absences or 0),
                anomaly_records=int(attendance_row.anomaly_records or 0),
            ),
            messages=MessageMetricsOut(
                total_messages=total_messages,
                delivered_messages=delivered_messages,
                failed_messages=int(message_row.failed_messages or 0),
                reach_rate=reach_rate,
            ),
        )

    @staticmethod
    def _bucket_name(granularity: Granularity) -> str:
        return "day" if granularity == "daily" else "week"

    @staticmethod
    def _time_series(rows) -> list[TimeSeriesPointOut]:
        return [TimeSeriesPointOut(period_start=row.bucket, value=int(row.value or 0)) for row in rows]

    @staticmethod
    def _decrypt_search_data(search_type: str, data: dict) -> dict:
        sensitive_keys_by_type = {
            "patient": {"medical_record_number", "email", "phone"},
            "appointment": {"appointment_number"},
            "expense": {"expense_number"},
            "attendance": {"attendance_number"},
            "message": {"message_number"},
        }
        sensitive_keys = sensitive_keys_by_type.get(search_type, set())

        if not isinstance(data, dict):
            return data

        decrypted: dict = {}
        for key, value in data.items():
            decrypted[key] = AnalyticsService._decrypt_search_value(key, value, sensitive_keys)
        return decrypted

    @staticmethod
    def _decrypt_search_value(key: str, value, sensitive_keys: set[str]):
        if isinstance(value, dict):
            return {
                nested_key: AnalyticsService._decrypt_search_value(nested_key, nested_value, sensitive_keys)
                for nested_key, nested_value in value.items()
            }
        if isinstance(value, list):
            return [AnalyticsService._decrypt_search_value(key, item, sensitive_keys) for item in value]

        if value is None:
            return None

        if key.lower() in sensitive_keys:
            return decrypt_string(str(value))
        return value

    @staticmethod
    def _authorize_search_type(role: str, search_type: str) -> None:
        if search_type not in AnalyticsService.SEARCH_ROLE_MATRIX[role]:
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail="Search type is not permitted for this role",
            )

    @staticmethod
    def _validate_date_range(start_date: datetime, end_date: datetime) -> None:
        if end_date < start_date:
            raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="Invalid date range")

    @staticmethod
    def _parse_uuid(value: str) -> UUID:
        try:
            return UUID(value)
        except ValueError as exc:
            raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="Invalid UUID value") from exc

    @staticmethod
    def _require_org_id(current_user: User) -> UUID:
        if current_user.organization_id is None:
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail="User is not assigned to an organization",
            )
        return current_user.organization_id

    @staticmethod
    def _access_context(current_user: User) -> tuple[UUID, str]:
        org_id = AnalyticsService._require_org_id(current_user)
        role = normalize_role(current_user.role)
        return org_id, role
