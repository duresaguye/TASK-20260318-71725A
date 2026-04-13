import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.rbac import normalize_role
from app.models.idempotency_key import IdempotencyKey
from app.models.revoked_token import RevokedToken
from app.models.user import User


class SecurityHardeningService:
    READ_ONLY_ROLES = {"auditor"}
    IDEMPOTENCY_WINDOW_HOURS = 24

    @staticmethod
    def ensure_org_membership(current_user: User) -> None:
        if current_user.organization_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User is not assigned to an organization",
            )

    @staticmethod
    def ensure_write_access(current_user: User) -> None:
        role = normalize_role(current_user.role)
        if role in SecurityHardeningService.READ_ONLY_ROLES:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Role has read-only access",
            )

    @staticmethod
    def ensure_personal_or_privileged_access(current_user: User, owner_user_id) -> None:
        role = normalize_role(current_user.role)
        if role in {"administrator", "auditor", "reviewer"}:
            return
        if owner_user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access to this resource is restricted",
            )

    @staticmethod
    def revoke_token(
        db: Session,
        *,
        jti: str,
        user_id,
        organization_id,
        expires_at: datetime,
    ) -> RevokedToken:
        if not isinstance(expires_at, datetime):
            expires_at = datetime.fromtimestamp(float(expires_at), tz=timezone.utc)
        existing = db.scalar(select(RevokedToken).where(RevokedToken.jti == jti))
        if existing is not None:
            return existing

        revoked = RevokedToken(
            jti=jti,
            token_type="access",
            user_id=user_id,
            organization_id=organization_id,
            expires_at=expires_at,
        )
        db.add(revoked)
        db.flush()
        return revoked

    @staticmethod
    def is_token_revoked(db: Session, *, jti: str) -> bool:
        return db.scalar(select(RevokedToken.id).where(RevokedToken.jti == jti)) is not None

    @staticmethod
    def build_request_hash(payload: Any) -> str:
        serialized = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @staticmethod
    def build_key_hash(key: str) -> str:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    @staticmethod
    def get_or_create_idempotency_key(
        db: Session,
        *,
        current_user: User,
        scope: str,
        raw_key: str | None,
        payload: Any,
    ) -> tuple[IdempotencyKey | None, bool]:
        if raw_key is None:
            return None, False

        SecurityHardeningService.ensure_org_membership(current_user)
        request_hash = SecurityHardeningService.build_request_hash(payload)
        key_hash = SecurityHardeningService.build_key_hash(raw_key)
        now = datetime.now(timezone.utc)

        db.query(IdempotencyKey).filter(
            IdempotencyKey.organization_id == current_user.organization_id,
            IdempotencyKey.user_id == current_user.id,
            IdempotencyKey.scope == scope,
            IdempotencyKey.key_hash == key_hash,
            IdempotencyKey.expires_at < now,
        ).delete(synchronize_session=False)

        existing = db.scalar(
            select(IdempotencyKey).where(
                IdempotencyKey.organization_id == current_user.organization_id,
                IdempotencyKey.user_id == current_user.id,
                IdempotencyKey.scope == scope,
                IdempotencyKey.key_hash == key_hash,
                IdempotencyKey.expires_at >= now,
            )
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Idempotency key reuse with different request payload is not allowed",
                )
            return existing, True

        record = IdempotencyKey(
            organization_id=current_user.organization_id,
            user_id=current_user.id,
            scope=scope,
            key_hash=key_hash,
            request_hash=request_hash,
            expires_at=now + timedelta(hours=SecurityHardeningService.IDEMPOTENCY_WINDOW_HOURS),
            status="pending",
        )
        db.add(record)
        db.flush()
        return record, False

    @staticmethod
    def finalize_idempotency_key(
        record: IdempotencyKey | None,
        *,
        resource_type: str,
        resource_id,
        response_payload: dict[str, Any] | None,
    ) -> None:
        if record is None:
            return
        record.resource_type = resource_type
        record.resource_id = resource_id
        record.response_payload = response_payload
        record.status = "completed"
