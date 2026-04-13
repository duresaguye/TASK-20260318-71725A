from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.access_policy import AccessPolicy, ResourceContext
from app.models.process_definition import ProcessDefinition
from app.models.process_instance import ProcessInstance
from app.models.task_comment import TaskComment
from app.models.user import User
from app.models.workflow_submission_record import WorkflowSubmissionRecord
from app.models.workflow_task import WorkflowTask
from app.schemas.workflow import (
    ProcessDefinitionCreate,
    ProcessInstanceStart,
    TaskActionRequest,
    TaskCommentRequest,
)
from app.services.audit_service import AuditService
from app.services.data_governance_service import DataGovernanceService
from app.services.security_hardening_service import SecurityHardeningService


SLA_HOURS = 48


class WorkflowService:
    SUPPORTED_WORKFLOW_TYPES = {"resource_application", "credit_change"}
    WORKFLOW_FAMILY_MAP = {
        "resource_application": "clinical_operations",
        "credit_change": "financial_operations",
    }

    @staticmethod
    def create_process_definition(
        db: Session,
        current_user: User,
        payload: ProcessDefinitionCreate,
    ) -> ProcessDefinition:
        AccessPolicy.require(role=current_user.role, domain="workflow", action="create_definition", db=db, organization_id=current_user.organization_id)
        if current_user.organization_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User is not assigned to an organization",
            )

        if payload.workflow_type not in WorkflowService.SUPPORTED_WORKFLOW_TYPES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported workflow type")
        expected_family = WorkflowService.WORKFLOW_FAMILY_MAP[payload.workflow_type]
        if payload.workflow_family is not None and payload.workflow_family != expected_family:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workflow family does not match workflow type")

        existing = db.scalar(
            select(ProcessDefinition).where(
                ProcessDefinition.organization_id == current_user.organization_id,
                ProcessDefinition.name == payload.name,
                ProcessDefinition.version == payload.version,
            )
        )
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Process definition with same name and version already exists",
            )

        steps_data = []
        for idx, step in enumerate(payload.steps):
            approvers = db.scalars(
                select(User).where(
                    User.id.in_(step.approver_ids),
                    User.organization_id == current_user.organization_id,
                    User.is_active.is_(True),
                )
            ).all()
            if len(approvers) != len(set(step.approver_ids)):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid approvers in step {idx + 1}",
                )
            for approver in approvers:
                if not AccessPolicy.allowed(approver.role, "workflow", "approve", db=db, organization_id=current_user.organization_id):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Approver {approver.username} does not have approval permission",
                    )
            steps_data.append(
                {
                    "step_index": idx,
                    "name": step.name,
                    "approver_ids": [str(aid) for aid in step.approver_ids],
                    "condition": step.condition.model_dump(mode="json") if step.condition else None,
                    "parallel_approval": step.parallel_approval,
                    "reminder_after_hours": step.reminder_after_hours,
                }
            )

        process_definition = ProcessDefinition(
            name=payload.name,
            workflow_family=expected_family,
            workflow_type=payload.workflow_type,
            version=payload.version,
            organization_id=current_user.organization_id,
            steps=steps_data,
            reminders_enabled=payload.reminders_enabled,
        )

        db.add(process_definition)
        db.flush()

        AuditService.log_event(
            db=db,
            organization_id=current_user.organization_id,
            action="workflow.definition.created",
            entity_type="process_definition",
            entity_id=process_definition.id,
            user_id=current_user.id,
            details={"name": payload.name, "version": payload.version, "workflow_type": payload.workflow_type},
        )

        db.commit()
        db.refresh(process_definition)
        return process_definition

    @staticmethod
    def start_process_instance(
        db: Session,
        current_user: User,
        payload: ProcessInstanceStart,
        idempotency_key: str | None = None,
    ) -> ProcessInstance:
        AccessPolicy.require(role=current_user.role, domain="workflow", action="start", db=db, organization_id=current_user.organization_id)
        if current_user.organization_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User is not assigned to an organization",
            )

        idempotency_record, replayed = SecurityHardeningService.get_or_create_idempotency_key(
            db=db,
            current_user=current_user,
            scope="workflow.start",
            raw_key=idempotency_key,
            payload=payload.model_dump(mode="json"),
        )
        if replayed:
            if idempotency_record is None or idempotency_record.resource_id is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="A matching workflow request is already being processed",
                )
            existing_instance = db.get(ProcessInstance, idempotency_record.resource_id)
            if existing_instance is None or existing_instance.organization_id != current_user.organization_id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Process instance not found",
                )
            return existing_instance

        try:
            return WorkflowService._start_process_instance_atomic(
                db=db,
                current_user=current_user,
                payload=payload,
                idempotency_record=idempotency_record,
            )
        except IntegrityError:
            db.rollback()
            return WorkflowService._start_process_instance_atomic(
                db=db,
                current_user=current_user,
                payload=payload,
                idempotency_record=idempotency_record,
            )

    @staticmethod
    def list_my_tasks(db: Session, current_user: User) -> list[WorkflowTask]:
        if current_user.organization_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User is not assigned to an organization",
            )

        AccessPolicy.require(role=current_user.role, domain="workflow", action="read", db=db, organization_id=current_user.organization_id)
        tasks = db.scalars(
            select(WorkflowTask)
            .where(
                WorkflowTask.organization_id == current_user.organization_id,
                WorkflowTask.assigned_user_id == current_user.id,
            )
            .order_by(WorkflowTask.created_at.desc())
        ).all()
        return list(tasks)

    @staticmethod
    def list_instance_tasks(
        db: Session,
        current_user: User,
        process_instance_id: UUID,
    ) -> list[WorkflowTask]:
        if current_user.organization_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User is not assigned to an organization",
            )

        instance = db.scalar(
            select(ProcessInstance).where(
                ProcessInstance.id == process_instance_id,
                ProcessInstance.organization_id == current_user.organization_id,
            )
        )
        if instance is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Process instance not found",
            )
        AccessPolicy.require_domain(
            role=current_user.role,
            domain="workflow",
            action="read",
            db=db,
            organization_id=current_user.organization_id,
            context=ResourceContext(
                organization_id=current_user.organization_id,
                owner_user_id=instance.started_by_user_id,
                is_personal=instance.started_by_user_id == current_user.id,
            ),
        )

        tasks = db.scalars(
            select(WorkflowTask)
            .where(
                WorkflowTask.process_instance_id == process_instance_id,
                WorkflowTask.organization_id == current_user.organization_id,
            )
            .order_by(WorkflowTask.step_index.asc(), WorkflowTask.created_at.asc())
        ).all()
        return list(tasks)

    @staticmethod
    def approve_task(
        db: Session,
        current_user: User,
        task_id: UUID,
        payload: TaskActionRequest,
    ) -> WorkflowTask:
        AccessPolicy.require(role=current_user.role, domain="workflow", action="approve", db=db, organization_id=current_user.organization_id)
        task = WorkflowService._get_task_for_user(db=db, current_user=current_user, task_id=task_id)

        if task.status not in {"pending", "overdue"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Task cannot be approved in current status",
            )

        DataGovernanceService.create_version_snapshot(
            db=db,
            entity="task",
            instance=task,
            actor_user_id=current_user.id,
            change_reason="task_approval",
            source_system="workflow",
            transformation_step="workflow_task_approve",
        )
        task.status = "completed"
        task.acted_at = datetime.now(timezone.utc)
        task.decision_comment = payload.comment

        AuditService.log_event(
            db=db,
            organization_id=task.organization_id,
            action="workflow.task.approved",
            entity_type="workflow_task",
            entity_id=task.id,
            user_id=current_user.id,
            details={"process_instance_id": str(task.process_instance_id), "comment": payload.comment},
        )

        WorkflowService._advance_instance_if_step_resolved(
            db=db,
            task=task,
            actor_user_id=current_user.id,
        )

        db.commit()
        db.refresh(task)
        return task

    @staticmethod
    def reject_task(
        db: Session,
        current_user: User,
        task_id: UUID,
        payload: TaskActionRequest,
    ) -> WorkflowTask:
        AccessPolicy.require(role=current_user.role, domain="workflow", action="reject", db=db, organization_id=current_user.organization_id)
        task = WorkflowService._get_task_for_user(db=db, current_user=current_user, task_id=task_id)

        if task.status not in {"pending", "overdue"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Task cannot be rejected in current status",
            )

        DataGovernanceService.create_version_snapshot(
            db=db,
            entity="task",
            instance=task,
            actor_user_id=current_user.id,
            change_reason="task_rejection",
            source_system="workflow",
            transformation_step="workflow_task_reject",
        )
        task.status = "rejected"
        task.acted_at = datetime.now(timezone.utc)
        task.decision_comment = payload.comment

        instance = task.process_instance
        WorkflowService._finalize_rejection_payload(
            instance=instance,
            task=task,
            actor_user_id=current_user.id,
            comment=payload.comment,
        )
        DataGovernanceService.create_version_snapshot(
            db=db,
            entity="workflow",
            instance=instance,
            actor_user_id=current_user.id,
            change_reason="workflow_rejection_state_change",
            source_system="workflow",
            transformation_step="workflow_instance_reject",
        )
        instance.status = "rejected"
        instance.completed_at = datetime.now(timezone.utc)

        AuditService.log_event(
            db=db,
            organization_id=task.organization_id,
            action="workflow.task.rejected",
            entity_type="workflow_task",
            entity_id=task.id,
            user_id=current_user.id,
            details={"process_instance_id": str(task.process_instance_id), "comment": payload.comment},
        )
        AuditService.log_event(
            db=db,
            organization_id=task.organization_id,
            action="workflow.instance.rejected",
            entity_type="process_instance",
            entity_id=instance.id,
            user_id=current_user.id,
            details={"rejected_task_id": str(task.id)},
        )

        db.commit()
        db.refresh(task)
        return task

    @staticmethod
    def comment_task(
        db: Session,
        current_user: User,
        task_id: UUID,
        payload: TaskCommentRequest,
    ) -> TaskComment:
        AccessPolicy.require(role=current_user.role, domain="workflow", action="comment", db=db, organization_id=current_user.organization_id)
        if current_user.organization_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User is not assigned to an organization",
            )

        task = db.scalar(
            select(WorkflowTask).where(
                WorkflowTask.id == task_id,
                WorkflowTask.organization_id == current_user.organization_id,
            )
        )
        if task is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Task not found",
            )
        AccessPolicy.require_domain(
            role=current_user.role,
            domain="workflow",
            action="comment",
            db=db,
            organization_id=current_user.organization_id,
            context=ResourceContext(
                organization_id=current_user.organization_id,
                owner_user_id=task.assigned_user_id,
                is_personal=task.assigned_user_id == current_user.id,
            ),
        )

        comment = TaskComment(
            task_id=task.id,
            organization_id=task.organization_id,
            author_user_id=current_user.id,
            comment=payload.comment,
        )

        db.add(comment)
        db.flush()

        AuditService.log_event(
            db=db,
            organization_id=task.organization_id,
            action="workflow.task.commented",
            entity_type="workflow_task",
            entity_id=task.id,
            user_id=current_user.id,
            details={"comment_id": str(comment.id)},
        )

        db.commit()
        db.refresh(comment)
        return comment

    @staticmethod
    def _get_task_for_user(db: Session, current_user: User, task_id: UUID) -> WorkflowTask:
        if current_user.organization_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User is not assigned to an organization",
            )

        task = db.scalar(
            select(WorkflowTask).where(
                WorkflowTask.id == task_id,
                WorkflowTask.organization_id == current_user.organization_id,
                WorkflowTask.assigned_user_id == current_user.id,
            )
        )
        if task is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Task not found",
            )

        if task.process_instance.status not in {"in_progress"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Process instance is no longer active",
            )

        AccessPolicy.require(role=current_user.role, domain="workflow", action="read", db=db, organization_id=current_user.organization_id)
        AccessPolicy.require_domain(
            role=current_user.role,
            domain="workflow",
            action="read",
            db=db,
            organization_id=current_user.organization_id,
            context=ResourceContext(
                organization_id=current_user.organization_id,
                owner_user_id=task.assigned_user_id,
                is_personal=task.assigned_user_id == current_user.id,
            ),
        )
        return task

    @staticmethod
    def _advance_instance_if_step_resolved(
        db: Session,
        task: WorkflowTask,
        actor_user_id: UUID,
    ) -> None:
        instance = task.process_instance
        process_definition = instance.process_definition
        step_definition = process_definition.steps[task.step_index] if task.step_index < len(process_definition.steps) else {}

        if not step_definition.get("parallel_approval", True):
            approver_ids = [UUID(uid) for uid in step_definition.get("approver_ids", [])]
            completed_count = db.scalar(
                select(func.count(WorkflowTask.id)).where(
                    WorkflowTask.process_instance_id == task.process_instance_id,
                    WorkflowTask.step_index == task.step_index,
                    WorkflowTask.status == "completed",
                )
            ) or 0

            if completed_count < len(approver_ids):
                next_approver_id = approver_ids[completed_count]
                existing_next_task = db.scalar(
                    select(WorkflowTask.id).where(
                        WorkflowTask.process_instance_id == task.process_instance_id,
                        WorkflowTask.step_index == task.step_index,
                        WorkflowTask.assigned_user_id == next_approver_id,
                        WorkflowTask.status == "pending",
                    )
                )
                if existing_next_task is None:
                    next_task = WorkflowTask(
                        process_instance_id=instance.id,
                        organization_id=instance.organization_id,
                        step_index=task.step_index,
                        step_name=task.step_name,
                        assigned_user_id=next_approver_id,
                        status="pending",
                        sla_due_at=task.sla_due_at,
                        reminder_due_at=task.reminder_due_at,
                    )
                    db.add(next_task)
                    db.flush()
                    AuditService.log_event(
                        db=db,
                        organization_id=instance.organization_id,
                        action="workflow.task.created",
                        entity_type="workflow_task",
                        entity_id=next_task.id,
                        user_id=actor_user_id,
                        details={"process_instance_id": str(instance.id), "step_index": task.step_index},
                    )
                return

        sibling_tasks = db.scalars(
            select(WorkflowTask).where(
                WorkflowTask.process_instance_id == task.process_instance_id,
                WorkflowTask.step_index == task.step_index,
            )
        ).all()

        if any(t.status == "rejected" for t in sibling_tasks):
            DataGovernanceService.create_version_snapshot(
                db=db,
                entity="workflow",
                instance=instance,
                actor_user_id=actor_user_id,
                change_reason="workflow_step_rejected",
                source_system="workflow",
                transformation_step="workflow_step_resolution_rejected",
                metadata={"step_index": task.step_index},
            )
            instance.status = "rejected"
            instance.completed_at = datetime.now(timezone.utc)
            WorkflowService._finalize_rejection_payload(
                instance=instance,
                task=task,
                actor_user_id=actor_user_id,
                comment=task.decision_comment,
            )
            AuditService.log_event(
                db=db,
                organization_id=instance.organization_id,
                action="workflow.instance.rejected",
                entity_type="process_instance",
                entity_id=instance.id,
                user_id=actor_user_id,
                details={"step_index": task.step_index},
            )
            return

        if not all(t.status == "completed" for t in sibling_tasks):
            return

        next_step_index = task.step_index + 1
        next_step_index = WorkflowService._resolve_next_step_index(
            steps=process_definition.steps,
            payload=instance.payload or {},
            candidate_index=next_step_index,
        )

        if next_step_index >= len(process_definition.steps):
            DataGovernanceService.create_version_snapshot(
                db=db,
                entity="workflow",
                instance=instance,
                actor_user_id=actor_user_id,
                change_reason="workflow_completed",
                source_system="workflow",
                transformation_step="workflow_completion",
            )
            instance.status = "completed"
            instance.current_step_index = next_step_index
            instance.completed_at = datetime.now(timezone.utc)
            merged_payload = dict(instance.payload or {})
            merged_payload["workflow_result"] = {
                "status": "completed",
                "completed_at": instance.completed_at.isoformat(),
                "business_number": instance.business_number,
                "workflow_type": process_definition.workflow_type,
            }
            instance.payload = merged_payload
            AuditService.log_event(
                db=db,
                organization_id=instance.organization_id,
                action="workflow.instance.completed",
                entity_type="process_instance",
                entity_id=instance.id,
                user_id=actor_user_id,
                details=None,
            )
            return

        DataGovernanceService.create_version_snapshot(
            db=db,
            entity="workflow",
            instance=instance,
            actor_user_id=actor_user_id,
            change_reason="workflow_advanced",
            source_system="workflow",
            transformation_step="workflow_step_advance",
            metadata={"next_step_index": next_step_index},
        )
        instance.current_step_index = next_step_index
        WorkflowService._create_tasks_for_step(
            db=db,
            instance=instance,
            process_definition=process_definition,
            step_index=next_step_index,
            actor_user_id=actor_user_id,
        )

    @staticmethod
    def _create_tasks_for_step(
        db: Session,
        instance: ProcessInstance,
        process_definition: ProcessDefinition,
        step_index: int,
        actor_user_id: UUID,
    ) -> list[WorkflowTask]:
        if step_index >= len(process_definition.steps):
            return []

        step = process_definition.steps[step_index]
        step_name = str(step.get("name", f"step_{step_index}"))
        approver_ids = [UUID(uid) for uid in step.get("approver_ids", [])]
        reminder_after_hours = step.get("reminder_after_hours") or 24
        parallel_approval = bool(step.get("parallel_approval", True))

        if len(approver_ids) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"No approvers configured for step {step_index}",
            )

        approvers = db.scalars(
            select(User).where(
                User.id.in_(approver_ids),
                User.organization_id == instance.organization_id,
                User.is_active.is_(True),
            )
        ).all()

        if len(approvers) != len(set(approver_ids)):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid approver mapping for step {step_index}",
            )
        for approver in approvers:
            if not AccessPolicy.allowed(approver.role, "workflow", "approve", db=db, organization_id=instance.organization_id):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Approver {approver.username} does not have approval permission",
                )

        due_at = datetime.now(timezone.utc) + timedelta(hours=SLA_HOURS)
        reminder_due_at = (
            datetime.now(timezone.utc) + timedelta(hours=reminder_after_hours)
            if process_definition.reminders_enabled
            else None
        )
        tasks: list[WorkflowTask] = []
        scheduled_approver_ids = approver_ids if parallel_approval else approver_ids[:1]
        for approver_id in scheduled_approver_ids:
            task = WorkflowTask(
                process_instance_id=instance.id,
                organization_id=instance.organization_id,
                step_index=step_index,
                step_name=step_name,
                assigned_user_id=approver_id,
                status="pending",
                sla_due_at=due_at,
                reminder_due_at=reminder_due_at,
            )
            db.add(task)
            db.flush()
            tasks.append(task)

            AuditService.log_event(
                db=db,
                organization_id=instance.organization_id,
                action="workflow.task.created",
                entity_type="workflow_task",
                entity_id=task.id,
                user_id=actor_user_id,
                details={"process_instance_id": str(instance.id), "step_index": step_index},
            )

        return tasks

    @staticmethod
    def _finalize_rejection_payload(
        instance: ProcessInstance,
        task: WorkflowTask,
        actor_user_id: UUID,
        comment: str | None,
    ) -> None:
        merged_payload = dict(instance.payload or {})
        merged_payload["workflow_result"] = {
            "status": "rejected",
            "rejected_at": datetime.now(timezone.utc).isoformat(),
            "rejected_task_id": str(task.id),
            "business_number": instance.business_number,
            "workflow_type": instance.process_definition.workflow_type,
            "comment": comment,
            "actor_user_id": str(actor_user_id),
        }
        instance.payload = merged_payload

    @staticmethod
    def _start_process_instance_atomic(
        db: Session,
        current_user: User,
        payload: ProcessInstanceStart,
        idempotency_record,
    ) -> ProcessInstance:
        reservation = WorkflowService._reserve_workflow_submission(
            db=db,
            current_user=current_user,
            business_number=payload.business_number,
        )
        if reservation.process_instance_id is not None:
            existing_instance = db.get(ProcessInstance, reservation.process_instance_id)
            if existing_instance is not None and existing_instance.organization_id == current_user.organization_id:
                SecurityHardeningService.finalize_idempotency_key(
                    idempotency_record,
                    resource_type="process_instance",
                    resource_id=existing_instance.id,
                    response_payload={"process_instance_id": str(existing_instance.id)},
                )
                db.commit()
                db.refresh(existing_instance)
                return existing_instance

        process_definition = db.scalar(
            select(ProcessDefinition).where(
                ProcessDefinition.id == payload.process_definition_id,
                ProcessDefinition.organization_id == current_user.organization_id,
            )
        )
        if process_definition is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Process definition not found",
            )

        instance = ProcessInstance(
            process_definition_id=process_definition.id,
            organization_id=current_user.organization_id,
            started_by_user_id=current_user.id,
            business_number=payload.business_number,
            status="in_progress",
            current_step_index=0,
            payload=payload.payload,
        )

        db.add(instance)
        db.flush()
        reservation.process_instance_id = instance.id
        reservation.status = "completed"

        WorkflowService._create_tasks_for_step(
            db=db,
            instance=instance,
            process_definition=process_definition,
            step_index=0,
            actor_user_id=current_user.id,
        )

        AuditService.log_event(
            db=db,
            organization_id=current_user.organization_id,
            action="workflow.instance.started",
            entity_type="process_instance",
            entity_id=instance.id,
            user_id=current_user.id,
            details={
                "process_definition_id": str(process_definition.id),
                "workflow_type": process_definition.workflow_type,
                "business_number": payload.business_number,
            },
        )
        SecurityHardeningService.finalize_idempotency_key(
            idempotency_record,
            resource_type="process_instance",
            resource_id=instance.id,
            response_payload={"process_instance_id": str(instance.id)},
        )

        db.commit()
        db.refresh(instance)
        return instance

    @staticmethod
    def _reserve_workflow_submission(
        db: Session,
        current_user: User,
        business_number: str,
    ) -> WorkflowSubmissionRecord:
        now = datetime.now(timezone.utc)
        reservation = db.scalar(
            select(WorkflowSubmissionRecord)
            .where(
                WorkflowSubmissionRecord.organization_id == current_user.organization_id,
                WorkflowSubmissionRecord.business_number == business_number,
            )
            .with_for_update()
        )

        if reservation is not None:
            if reservation.expires_at < now:
                reservation.expires_at = now + timedelta(hours=24)
                reservation.process_instance_id = None
                reservation.status = "pending"
                db.flush()
            return reservation

        reservation = WorkflowSubmissionRecord(
            organization_id=current_user.organization_id,
            business_number=business_number,
            expires_at=now + timedelta(hours=24),
            status="pending",
        )
        db.add(reservation)
        db.flush()
        return reservation

    @staticmethod
    def _resolve_next_step_index(steps: list[dict], payload: dict, candidate_index: int) -> int:
        index = candidate_index
        while index < len(steps):
            if WorkflowService._condition_matches(steps[index].get("condition"), payload):
                return index
            index += 1
        return index

    @staticmethod
    def _condition_matches(condition: dict | None, payload: dict) -> bool:
        if not condition:
            return True
        field = condition.get("field")
        operator = condition.get("operator")
        expected = condition.get("value")
        actual = payload.get(field)
        if operator == "eq":
            return actual == expected
        if operator == "neq":
            return actual != expected
        if operator == "gt":
            return actual is not None and actual > expected
        if operator == "gte":
            return actual is not None and actual >= expected
        if operator == "lt":
            return actual is not None and actual < expected
        if operator == "lte":
            return actual is not None and actual <= expected
        if operator == "in":
            return actual in expected if isinstance(expected, list) else False
        return False
