import asyncio
import os
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.workflow_task import WorkflowTask
from app.services.audit_service import AuditService
from app.services.data_governance_service import DataGovernanceService


SLA_CHECK_INTERVAL_SECONDS = int(os.getenv("SLA_CHECK_INTERVAL_SECONDS", "60"))


class WorkflowSLAService:
    @staticmethod
    def mark_overdue_tasks(db: Session) -> int:
        now = datetime.now(timezone.utc)
        reminder_tasks = db.scalars(
            select(WorkflowTask).where(
                WorkflowTask.status == "pending",
                WorkflowTask.reminder_due_at.is_not(None),
                WorkflowTask.reminder_due_at <= now,
                WorkflowTask.reminder_sent_at.is_(None),
            )
        ).all()
        for task in reminder_tasks:
            task.reminder_sent_at = now
            AuditService.log_event(
                db=db,
                organization_id=task.organization_id,
                action="workflow.task.reminder_sent",
                entity_type="workflow_task",
                entity_id=task.id,
                user_id=None,
                details={"process_instance_id": str(task.process_instance_id)},
            )

        overdue_tasks = db.scalars(
            select(WorkflowTask).where(
                WorkflowTask.status == "pending",
                WorkflowTask.sla_due_at < now,
            )
        ).all()

        count = 0
        for task in overdue_tasks:
            DataGovernanceService.create_version_snapshot(
                db=db,
                entity="task",
                instance=task,
                actor_user_id=None,
                change_reason="sla_overdue_update",
                source_system="workflow",
                transformation_step="workflow_sla_overdue",
            )
            task.status = "overdue"
            AuditService.log_event(
                db=db,
                organization_id=task.organization_id,
                action="task.overdue",
                entity_type="workflow_task",
                entity_id=task.id,
                user_id=None,
                details={"process_instance_id": str(task.process_instance_id)},
            )
            count += 1

        if count > 0 or len(reminder_tasks) > 0:
            db.commit()

        return count


async def run_sla_monitor() -> None:
    while True:
        db = SessionLocal()
        try:
            WorkflowSLAService.mark_overdue_tasks(db)
        except Exception:
            db.rollback()
        finally:
            db.close()
        await asyncio.sleep(SLA_CHECK_INTERVAL_SECONDS)
