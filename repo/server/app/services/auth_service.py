import os
import re
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.rbac import ensure_valid_role
from app.core.security import (
    PASSWORD_RESET_TOKEN_EXPIRE_MINUTES,
    create_access_token,
    decode_access_token,
    generate_password_reset_token,
    hash_password,
    hash_reset_token,
    verify_password,
)
from app.models.password_reset_token import PasswordResetToken
from app.models.user import User
from app.schemas.auth import Token, UserCreate, UserLogin
from app.services.audit_service import AuditService
from app.services.data_governance_service import DataGovernanceService
from app.services.security_hardening_service import SecurityHardeningService


class AuthService:
    MAX_FAILED_LOGINS = 5
    FAILED_WINDOW_MINUTES = 10
    LOCK_DURATION_MINUTES = 30
    EXPOSE_RESET_TOKEN = os.getenv("EXPOSE_RESET_TOKEN", "false").lower() == "true"

    @staticmethod
    def register_user(db: Session, user_data: UserCreate, organization_id: UUID | None = None) -> User:
        AuthService._validate_password_strength(user_data.password)

        existing_user = db.scalar(select(User).where(User.username == user_data.username))
        if existing_user is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already registered",
            )

        user = User(
            username=user_data.username,
            hashed_password=hash_password(user_data.password),
            organization_id=organization_id,
            role="general_user",
        )

        db.add(user)
        db.flush()
        AuditService.log_event(
            db=db,
            organization_id=user.organization_id,
            action="auth.register",
            entity_type="user",
            entity_id=user.id,
            user_id=user.id,
            details={"result": "created"},
        )
        db.commit()
        db.refresh(user)
        return user

    @staticmethod
    def login_user(db: Session, login_data: UserLogin, organization_id: UUID | None = None) -> Token:
        _ = organization_id

        user = db.scalar(select(User).where(User.username == login_data.username))

        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        AuthService._check_login_lock(user)

        if not verify_password(login_data.password, user.hashed_password):
            AuthService._register_failed_login(db=db, user=user)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if (
            user.organization_id is not None
            and (
                user.failed_login_attempts != 0
                or user.last_failed_login_at is not None
                or user.locked_until is not None
            )
        ):
            DataGovernanceService.create_version_snapshot(
                db=db,
                entity="user",
                instance=user,
                actor_user_id=user.id,
                change_reason="login_state_reset",
                source_system="manual",
                transformation_step="auth_successful_login",
            )
        AuthService._clear_failed_logins(user)

        if not user.is_active:
            AuditService.log_event(
                db=db,
                organization_id=user.organization_id,
                action="auth.login.denied",
                entity_type="user",
                entity_id=user.id,
                user_id=user.id,
                details={"reason": "inactive_user"},
            )
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Inactive user",
            )

        normalized_role = ensure_valid_role(user.role)
        if normalized_role != user.role and user.organization_id is not None:
            DataGovernanceService.create_version_snapshot(
                db=db,
                entity="user",
                instance=user,
                actor_user_id=user.id,
                change_reason="role_normalization",
                source_system="manual",
                transformation_step="auth_login_role_normalization",
            )
        user.role = normalized_role
        db.commit()

        access_token = create_access_token(
            {
                "sub": str(user.id),
                "user_id": str(user.id),
                "username": user.username,
                "org_id": str(user.organization_id) if user.organization_id else None,
                "role": user.role,
                "token_version": user.token_version,
            }
        )
        AuditService.log_event(
            db=db,
            organization_id=user.organization_id,
            action="auth.login.succeeded",
            entity_type="user",
            entity_id=user.id,
            user_id=user.id,
            details=None,
        )
        db.commit()

        return Token(access_token=access_token)

    @staticmethod
    def request_password_recovery(db: Session, username: str) -> dict[str, str]:
        user = db.scalar(select(User).where(User.username == username))
        audit_details = {"result": "accepted"}
        if user is None:
            AuditService.log_event(
                db=db,
                organization_id=None,
                action="auth.password_reset.requested",
                entity_type="user",
                entity_id=None,
                user_id=None,
                details=audit_details,
                audit_scope="system",
            )
            db.commit()
            return {"message": "If the account exists, a reset link has been sent"}

        raw_token = generate_password_reset_token()
        hashed_token = hash_reset_token(raw_token)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=PASSWORD_RESET_TOKEN_EXPIRE_MINUTES)

        db.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=hashed_token,
                expires_at=expires_at,
                is_used=False,
            )
        )
        AuditService.log_event(
            db=db,
            organization_id=user.organization_id,
            action="auth.password_reset.requested",
            entity_type="user",
            entity_id=user.id,
            user_id=user.id,
            details=audit_details,
        )
        db.commit()

        response = {"message": "If the account exists, a reset link has been sent"}
        if AuthService.EXPOSE_RESET_TOKEN:
            response["reset_token"] = raw_token
        return response

    @staticmethod
    def reset_password(db: Session, token: str, new_password: str) -> dict[str, str]:
        AuthService._validate_password_strength(new_password)

        token_hash = hash_reset_token(token)
        reset_token = db.scalar(
            select(PasswordResetToken).where(
                PasswordResetToken.token_hash == token_hash,
                PasswordResetToken.is_used.is_(False),
            )
        )

        now = datetime.now(timezone.utc)
        if reset_token is None or reset_token.expires_at < now:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired reset token",
            )

        user = db.get(User, reset_token.user_id)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        if user.organization_id is not None:
            DataGovernanceService.create_version_snapshot(
                db=db,
                entity="user",
                instance=user,
                actor_user_id=user.id,
                change_reason="password_reset",
                source_system="manual",
                transformation_step="auth_password_reset",
            )
        user.hashed_password = hash_password(new_password)
        user.token_version = (user.token_version or 0) + 1
        AuthService._clear_failed_logins(user)
        reset_token.is_used = True

        AuditService.log_event(
            db=db,
            organization_id=user.organization_id,
            action="auth.password_reset.confirmed",
            entity_type="user",
            entity_id=user.id,
            user_id=user.id,
            details={"result": "confirmed"},
        )
        db.commit()
        return {"message": "Password has been reset successfully"}

    @staticmethod
    def logout_user(db: Session, current_user: User, token: str) -> dict[str, str]:
        payload = decode_access_token(token)
        SecurityHardeningService.revoke_token(
            db=db,
            jti=payload["jti"],
            user_id=current_user.id,
            organization_id=current_user.organization_id,
            expires_at=payload["exp"],
        )
        AuditService.log_event(
            db=db,
            organization_id=current_user.organization_id,
            action="auth.logout",
            entity_type="user",
            entity_id=current_user.id,
            user_id=current_user.id,
            details=None,
        )
        db.commit()
        return {"message": "Logout successful"}

    @staticmethod
    def _validate_password_strength(password: str) -> None:
        if len(password) < 8:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password must be at least 8 characters long",
            )

        if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password must include both letters and numbers",
            )

    @staticmethod
    def _check_login_lock(user: User) -> None:
        now = datetime.now(timezone.utc)
        if user.locked_until is not None and user.locked_until > now:
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail="Account is temporarily locked due to failed login attempts",
            )

    @staticmethod
    def _register_failed_login(db: Session, user: User) -> None:
        if user.organization_id is not None:
            DataGovernanceService.create_version_snapshot(
                db=db,
                entity="user",
                instance=user,
                actor_user_id=user.id,
                change_reason="failed_login_state_change",
                source_system="manual",
                transformation_step="auth_failed_login",
            )
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(minutes=AuthService.FAILED_WINDOW_MINUTES)

        if user.last_failed_login_at is None or user.last_failed_login_at < window_start:
            user.failed_login_attempts = 1
        else:
            user.failed_login_attempts += 1

        user.last_failed_login_at = now

        if user.failed_login_attempts >= AuthService.MAX_FAILED_LOGINS:
            user.locked_until = now + timedelta(minutes=AuthService.LOCK_DURATION_MINUTES)
            user.failed_login_attempts = 0

        AuditService.log_event(
            db=db,
            organization_id=user.organization_id,
            action="auth.login.failed",
            entity_type="user",
            entity_id=user.id,
            user_id=user.id,
            details={"locked_until": user.locked_until.isoformat() if user.locked_until else None},
        )
        db.commit()

    @staticmethod
    def _clear_failed_logins(user: User) -> None:
        user.failed_login_attempts = 0
        user.last_failed_login_at = None
        user.locked_until = None
