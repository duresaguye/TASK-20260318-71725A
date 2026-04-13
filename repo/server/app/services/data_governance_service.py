import json
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.models.data_import_batch import DataImportBatch
from app.models.data_import_error import DataImportError
from app.models.data_lineage import DataLineage
from app.models.data_quality_rule import DataQualityRule
from app.models.data_version import DataVersion
from app.models.process_definition import ProcessDefinition
from app.models.process_instance import ProcessInstance
from app.models.user import User
from app.models.workflow_task import WorkflowTask
from app.schemas.data_governance import DataImportRequest, DataQualityRuleInput
from app.core.access_policy import AccessPolicy
from app.core.encryption import encrypt_string
from app.services.audit_service import AuditService
from app.services.response_security_service import ResponseSecurityService
from app.services.security_hardening_service import SecurityHardeningService


class DataGovernanceService:
    ENTITY_MAP = {
        "user": (User, "user"),
        "users": (User, "user"),
        "workflow": (ProcessInstance, "workflow"),
        "workflows": (ProcessInstance, "workflow"),
        "process_instance": (ProcessInstance, "workflow"),
        "task": (WorkflowTask, "workflow_task"),
        "tasks": (WorkflowTask, "workflow_task"),
        "workflow_task": (WorkflowTask, "workflow_task"),
        "process_definition": (ProcessDefinition, "process_definition"),
    }

    @staticmethod
    def import_data(
        db: Session,
        current_user: User,
        payload: DataImportRequest,
    ) -> DataImportBatch:
        AccessPolicy.require(role=current_user.role, domain="governance", action="import", db=db, organization_id=current_user.organization_id)
        organization_id = DataGovernanceService._require_organization(current_user)
        entity_type = payload.entity_type.strip().lower()

        if payload.persist_rules and payload.rules:
            DataGovernanceService._upsert_rules(
                db=db,
                organization_id=organization_id,
                actor_user_id=current_user.id,
                entity_type=entity_type,
                rules=payload.rules,
            )

        db_rules = DataGovernanceService._get_quality_rules(
            db=db,
            organization_id=organization_id,
            entity_type=entity_type,
        )
        effective_rules = payload.rules if payload.rules else db_rules

        batch = DataImportBatch(
            organization_id=organization_id,
            created_by=current_user.id,
            entity_type=entity_type,
            source_system=payload.source_system,
            status="processing",
            total_rows=len(payload.rows),
            metadata_json={"reject_invalid_records": payload.reject_invalid_records},
        )
        db.add(batch)
        db.flush()

        errors: list[DataImportError] = []
        seen_rows: dict[str, int] = {}
        for index, row in enumerate(payload.rows, start=1):
            row_errors = DataGovernanceService._validate_row(
                row=row,
                row_number=index,
                rules=effective_rules,
                seen_rows=seen_rows,
            )
            for error in row_errors:
                errors.append(
                    DataImportError(
                        batch_id=batch.id,
                        organization_id=organization_id,
                        row_number=index,
                        validation_type=error["validation_type"],
                        field_name=error.get("field_name"),
                        error_reason=error["error_reason"],
                        row_data=DataGovernanceService._mask_row_value(row),
                        row_data_raw_encrypted=DataGovernanceService._encrypt_row_payload(row),
                    )
                )

        if errors:
            db.add_all(errors)
            for error in errors:
                AuditService.log_event(
                    db=db,
                    organization_id=organization_id,
                    action="data.validation.failed",
                    entity_type="data_import_batch",
                    entity_id=batch.id,
                    user_id=current_user.id,
                    details={
                        "row_number": error.row_number,
                        "validation_type": error.validation_type,
                        "field_name": error.field_name,
                        "error_reason": error.error_reason,
                    },
                )

        invalid_rows = len({error.row_number for error in errors})
        valid_rows = batch.total_rows - invalid_rows
        error_summaries = [
            {
                "row_number": error.row_number,
                "validation_type": error.validation_type,
                "field_name": error.field_name,
                "error_reason": error.error_reason,
            }
            for error in errors
        ]
        batch.valid_rows = valid_rows
        batch.invalid_rows = invalid_rows
        batch.accepted_rows = valid_rows if payload.reject_invalid_records else batch.total_rows
        batch.rejected_rows = invalid_rows if payload.reject_invalid_records else 0
        batch.status = "completed" if invalid_rows == 0 else "completed_with_errors"
        batch.metadata_json = {
            "reject_invalid_records": payload.reject_invalid_records,
            "rules_applied": [DataGovernanceService._rule_to_dict(rule) for rule in effective_rules],
            "errors": error_summaries,
            "error_count": len(errors),
            "invalid_row_count": invalid_rows,
        }

        AuditService.log_event(
            db=db,
            organization_id=organization_id,
            action="data.import.created",
            entity_type="data_import_batch",
            entity_id=batch.id,
            user_id=current_user.id,
            details={
                "entity_type": entity_type,
                "source_system": payload.source_system,
                "total_rows": batch.total_rows,
                "valid_rows": valid_rows,
                "invalid_rows": invalid_rows,
            },
        )
        DataGovernanceService._create_lineage_entry(
            db=db,
            organization_id=organization_id,
            entity_type="data_import_batch",
            entity_id=batch.id,
            source_system=payload.source_system,
            transformation_step="import_validation",
            created_by=current_user.id,
            metadata={
                "target_entity": entity_type,
                "total_rows": batch.total_rows,
                "invalid_rows": invalid_rows,
            },
        )

        db.commit()
        db.refresh(batch)
        return batch

    @staticmethod
    def get_import_errors(
        db: Session,
        current_user: User,
        batch_id: UUID,
    ) -> list[DataImportError]:
        organization_id = DataGovernanceService._require_organization(current_user)
        batch = db.scalar(
            select(DataImportBatch).where(
                DataImportBatch.id == batch_id,
                DataImportBatch.organization_id == organization_id,
            )
        )
        if batch is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import batch not found")
        AccessPolicy.require(role=current_user.role, domain="governance", action="errors", db=db, organization_id=current_user.organization_id)

        return list(
            db.scalars(
                select(DataImportError)
                .where(
                    DataImportError.batch_id == batch_id,
                    DataImportError.organization_id == organization_id,
                )
                .order_by(DataImportError.row_number.asc(), DataImportError.created_at.asc())
            ).all()
        )

    @staticmethod
    def create_version_snapshot(
        db: Session,
        entity: str,
        instance: object,
        actor_user_id: UUID | None,
        change_reason: str,
        source_system: str,
        transformation_step: str,
        metadata: dict | None = None,
        is_rollback: bool = False,
    ) -> DataVersion:
        model_cls, canonical_entity = DataGovernanceService._resolve_entity(entity)
        if not isinstance(instance, model_cls):
            raise ValueError("Snapshot instance does not match entity mapping")

        organization_id = getattr(instance, "organization_id", None)
        if organization_id is None:
            raise ValueError("Entity does not have organization scope")

        latest = db.scalar(
            select(DataVersion)
            .where(
                DataVersion.organization_id == organization_id,
                DataVersion.entity_type == canonical_entity,
                DataVersion.entity_id == instance.id,
            )
            .order_by(DataVersion.version_number.desc())
        )
        next_version = 1 if latest is None else latest.version_number + 1

        version = DataVersion(
            organization_id=organization_id,
            created_by=actor_user_id,
            entity_type=canonical_entity,
            entity_id=instance.id,
            version_number=next_version,
            snapshot=DataGovernanceService._model_to_snapshot(instance),
            change_reason=change_reason,
            is_rollback=is_rollback,
        )
        db.add(version)
        db.flush()

        DataGovernanceService._create_lineage_entry(
            db=db,
            organization_id=organization_id,
            entity_type=canonical_entity,
            entity_id=instance.id,
            source_system=source_system,
            transformation_step=transformation_step,
            created_by=actor_user_id,
            metadata={
                "version_number": next_version,
                "change_reason": change_reason,
                **(metadata or {}),
            },
        )
        return version

    @staticmethod
    def rollback_entity(
        db: Session,
        current_user: User,
        entity: str,
        entity_id: UUID,
    ) -> dict:
        AccessPolicy.require(role=current_user.role, domain="governance", action="rollback", db=db, organization_id=current_user.organization_id)
        organization_id = DataGovernanceService._require_organization(current_user)
        model_cls, canonical_entity = DataGovernanceService._resolve_entity(entity)
        instance = db.scalar(
            select(model_cls).where(
                model_cls.id == entity_id,
                model_cls.organization_id == organization_id,
            )
        )
        if instance is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entity not found")

        versions = list(
            db.scalars(
                select(DataVersion)
                .where(
                    DataVersion.organization_id == organization_id,
                    DataVersion.entity_type == canonical_entity,
                    DataVersion.entity_id == entity_id,
                )
                .order_by(DataVersion.version_number.desc())
            ).all()
        )
        if not versions:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No version history available")

        current_snapshot = DataGovernanceService._model_to_snapshot(instance)
        target_version = next(
            (version for version in versions if version.snapshot != current_snapshot),
            None,
        )
        if target_version is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No previous valid version available")

        rollback_version = DataGovernanceService.create_version_snapshot(
            db=db,
            entity=canonical_entity,
            instance=instance,
            actor_user_id=current_user.id,
            change_reason="rollback_pre_restore",
            source_system="manual",
            transformation_step="rollback_snapshot",
            metadata={"target_version": target_version.version_number},
            is_rollback=True,
        )

        DataGovernanceService._restore_snapshot(instance=instance, snapshot=target_version.snapshot)

        AuditService.log_event(
            db=db,
            organization_id=organization_id,
            action="data.rollback.executed",
            entity_type=canonical_entity,
            entity_id=entity_id,
            user_id=current_user.id,
            details={
                "restored_version": target_version.version_number,
                "rollback_version": rollback_version.version_number,
            },
        )
        AuditService.log_event(
            db=db,
            organization_id=organization_id,
            action="data.correction.applied",
            entity_type=canonical_entity,
            entity_id=entity_id,
            user_id=current_user.id,
            details={"restored_version": target_version.version_number},
        )
        DataGovernanceService._create_lineage_entry(
            db=db,
            organization_id=organization_id,
            entity_type=canonical_entity,
            entity_id=entity_id,
            source_system="manual",
            transformation_step="rollback_restore",
            created_by=current_user.id,
            metadata={
                "restored_version": target_version.version_number,
                "rollback_version": rollback_version.version_number,
            },
        )

        db.commit()
        return {
            "entity_type": canonical_entity,
            "entity_id": entity_id,
            "restored_version": target_version.version_number,
            "rollback_version": rollback_version.version_number,
            "status": "rolled_back",
        }

    @staticmethod
    def get_lineage(
        db: Session,
        current_user: User,
        entity: str,
        entity_id: UUID,
    ) -> list[DataLineage]:
        organization_id = DataGovernanceService._require_organization(current_user)
        _, canonical_entity = DataGovernanceService._resolve_entity(entity)
        AccessPolicy.require(role=current_user.role, domain="governance", action="lineage_read", db=db, organization_id=current_user.organization_id)
        return list(
            db.scalars(
                select(DataLineage)
                .where(
                    DataLineage.organization_id == organization_id,
                    DataLineage.entity_type == canonical_entity,
                    DataLineage.entity_id == entity_id,
                )
                .order_by(DataLineage.created_at.asc())
            ).all()
        )

    @staticmethod
    def _validate_row(
        row: dict,
        row_number: int,
        rules: list[DataQualityRuleInput | DataQualityRule],
        seen_rows: dict[str, int],
    ) -> list[dict]:
        errors: list[dict] = []

        for rule in rules:
            rule_type = getattr(rule, "rule_type")
            field_name = getattr(rule, "field_name")
            config = getattr(rule, "config", None)

            if rule_type == "required":
                value = row.get(field_name)
                if value is None or (isinstance(value, str) and value.strip() == ""):
                    errors.append(
                        {
                            "validation_type": "missing",
                            "field_name": field_name,
                            "error_reason": f"Missing required field: {field_name}",
                        }
                    )
            elif rule_type == "range":
                value = row.get(field_name)
                if value is None:
                    continue
                range_error = DataGovernanceService._validate_range(field_name=field_name, value=value, config=config)
                if range_error is not None:
                    errors.append(range_error)

        duplicate_key = DataGovernanceService._build_duplicate_key(row)
        if duplicate_key in seen_rows:
            errors.append(
                {
                    "validation_type": "duplicate",
                    "field_name": None,
                    "error_reason": f"Duplicate record matches row {seen_rows[duplicate_key]}",
                }
            )
        else:
            seen_rows[duplicate_key] = row_number

        return errors

    @staticmethod
    def _validate_range(field_name: str, value: object, config: object | None) -> dict | None:
        config_data = DataGovernanceService._rule_config_dict(config)

        if any(config_data.get(key) is not None for key in ("min_value", "max_value")):
            try:
                numeric_value = float(value)
            except (TypeError, ValueError):
                return {
                    "validation_type": "out_of_bounds",
                    "field_name": field_name,
                    "error_reason": f"Field {field_name} must be numeric for range validation",
                }

            min_value = config_data.get("min_value")
            max_value = config_data.get("max_value")
            if min_value is not None and numeric_value < float(min_value):
                return {
                    "validation_type": "out_of_bounds",
                    "field_name": field_name,
                    "error_reason": f"Field {field_name} is below minimum value {min_value}",
                }
            if max_value is not None and numeric_value > float(max_value):
                return {
                    "validation_type": "out_of_bounds",
                    "field_name": field_name,
                    "error_reason": f"Field {field_name} exceeds maximum value {max_value}",
                }

        if any(config_data.get(key) is not None for key in ("min_date", "max_date")):
            try:
                date_value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError:
                return {
                    "validation_type": "out_of_bounds",
                    "field_name": field_name,
                    "error_reason": f"Field {field_name} must be an ISO datetime/date string",
                }

            min_date = config_data.get("min_date")
            max_date = config_data.get("max_date")
            if min_date is not None:
                parsed_min_date = datetime.fromisoformat(str(min_date).replace("Z", "+00:00"))
                if date_value < parsed_min_date:
                    return {
                        "validation_type": "out_of_bounds",
                        "field_name": field_name,
                        "error_reason": f"Field {field_name} is before minimum date {min_date}",
                    }
            if max_date is not None:
                parsed_max_date = datetime.fromisoformat(str(max_date).replace("Z", "+00:00"))
                if date_value > parsed_max_date:
                    return {
                        "validation_type": "out_of_bounds",
                        "field_name": field_name,
                        "error_reason": f"Field {field_name} is after maximum date {max_date}",
                    }

        return None

    @staticmethod
    def _get_quality_rules(
        db: Session,
        organization_id: UUID,
        entity_type: str,
    ) -> list[DataQualityRule]:
        return list(
            db.scalars(
                select(DataQualityRule)
                .where(
                    DataQualityRule.organization_id == organization_id,
                    DataQualityRule.entity_type == entity_type,
                    DataQualityRule.is_active.is_(True),
                )
                .order_by(DataQualityRule.created_at.asc())
            ).all()
        )

    @staticmethod
    def _upsert_rules(
        db: Session,
        organization_id: UUID,
        actor_user_id: UUID,
        entity_type: str,
        rules: list[DataQualityRuleInput],
    ) -> None:
        for rule in rules:
            existing = db.scalar(
                select(DataQualityRule).where(
                    DataQualityRule.organization_id == organization_id,
                    DataQualityRule.entity_type == entity_type,
                    DataQualityRule.field_name == rule.field_name,
                    DataQualityRule.rule_type == rule.rule_type,
                )
            )
            rule_config = DataGovernanceService._rule_config_dict(rule.config)
            if existing is None:
                db.add(
                    DataQualityRule(
                        organization_id=organization_id,
                        created_by=actor_user_id,
                        entity_type=entity_type,
                        field_name=rule.field_name,
                        rule_type=rule.rule_type,
                        config=rule_config,
                        is_active=True,
                    )
                )
            else:
                existing.config = rule_config
                existing.is_active = True

    @staticmethod
    def _create_lineage_entry(
        db: Session,
        organization_id: UUID,
        entity_type: str,
        entity_id: UUID,
        source_system: str,
        transformation_step: str,
        created_by: UUID | None,
        metadata: dict | None,
    ) -> DataLineage:
        lineage = DataLineage(
            organization_id=organization_id,
            entity_type=entity_type,
            entity_id=entity_id,
            source_system=source_system,
            transformation_step=transformation_step,
            created_by=created_by,
            metadata_json=DataGovernanceService._serialize_value(metadata) if metadata is not None else None,
        )
        db.add(lineage)
        db.flush()
        return lineage

    @staticmethod
    def _restore_snapshot(instance: object, snapshot: dict) -> None:
        mapper = inspect(type(instance))
        for column in mapper.columns:
            key = column.key
            if key == "id":
                continue
            if key not in snapshot:
                continue
            setattr(instance, key, DataGovernanceService._deserialize_value(column.type.python_type, snapshot[key]))

    @staticmethod
    def _model_to_snapshot(instance: object) -> dict:
        mapper = inspect(type(instance))
        snapshot: dict[str, object] = {}
        for column in mapper.columns:
            snapshot[column.key] = DataGovernanceService._serialize_value(getattr(instance, column.key))
        return snapshot

    @staticmethod
    def _serialize_value(value: object) -> object:
        if isinstance(value, dict):
            return {str(key): DataGovernanceService._serialize_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [DataGovernanceService._serialize_value(item) for item in value]
        if isinstance(value, tuple):
            return [DataGovernanceService._serialize_value(item) for item in value]
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Decimal):
            return str(value)
        return value

    @staticmethod
    def _encrypt_row_payload(row: dict) -> str | None:
        try:
            serialized = json.dumps(DataGovernanceService._serialize_value(row), sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError):
            return None
        try:
            return encrypt_string(serialized)
        except RuntimeError:
            return None

    @staticmethod
    def _mask_row_value(value: object) -> object:
        if isinstance(value, dict):
            return {
                str(key): DataGovernanceService._mask_row_field(str(key), item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [DataGovernanceService._mask_row_value(item) for item in value]
        return value

    @staticmethod
    def _mask_row_field(key: str, value: object) -> object:
        if isinstance(value, dict):
            return DataGovernanceService._mask_row_value(value)
        if isinstance(value, list):
            return [DataGovernanceService._mask_row_value(item) for item in value]
        if value is None:
            return None

        key_lower = key.lower()
        text_value = str(value)
        if "email" in key_lower:
            return ResponseSecurityService.mask_email(text_value)
        if "phone" in key_lower:
            return ResponseSecurityService.mask_phone(text_value)
        if key_lower.endswith("id") or key_lower.endswith("_id"):
            return ResponseSecurityService.mask_identifier(text_value)
        if key_lower in {
            "medical_record_number",
            "appointment_number",
            "expense_number",
            "attendance_number",
            "message_number",
            "employee_number",
            "username",
            "full_name",
            "name",
        }:
            return ResponseSecurityService.mask_identifier(text_value)
        return value

    @staticmethod
    def _deserialize_value(expected_type: type, value: object) -> object:
        if value is None:
            return None
        if expected_type is UUID:
            return UUID(str(value))
        if expected_type is datetime:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if expected_type is Decimal:
            return Decimal(str(value))
        return value

    @staticmethod
    def _resolve_entity(entity: str) -> tuple[type, str]:
        resolved = DataGovernanceService.ENTITY_MAP.get(entity.strip().lower())
        if resolved is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported entity type")
        return resolved

    @staticmethod
    def _require_organization(current_user: User) -> UUID:
        if current_user.organization_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User is not assigned to an organization",
            )
        return current_user.organization_id

    @staticmethod
    def _build_duplicate_key(row: dict) -> str:
        normalized = {
            str(key): DataGovernanceService._serialize_value(value)
            for key, value in sorted(row.items(), key=lambda item: str(item[0]))
        }
        return repr(normalized)

    @staticmethod
    def _rule_config_dict(config: object | None) -> dict:
        if config is None:
            return {}
        if hasattr(config, "model_dump"):
            return config.model_dump(exclude_none=True)
        if isinstance(config, dict):
            return {key: value for key, value in config.items() if value is not None}
        return {}

    @staticmethod
    def _rule_to_dict(rule: DataQualityRuleInput | DataQualityRule) -> dict:
        return {
            "field_name": getattr(rule, "field_name"),
            "rule_type": getattr(rule, "rule_type"),
            "config": DataGovernanceService._rule_config_dict(getattr(rule, "config", None)),
        }
