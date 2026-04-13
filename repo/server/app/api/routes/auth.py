from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps.auth import get_current_token, get_current_user
from app.db.deps import get_db
from app.models.user import User
from app.schemas.auth import (
    PasswordRecoveryRequest,
    PasswordResetRequest,
    Token,
    UserCreate,
    UserLogin,
)
from app.services.auth_service import AuthService


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(user_data: UserCreate, db: Session = Depends(get_db)) -> dict[str, str]:
    user = AuthService.register_user(db=db, user_data=user_data)
    return {
        "message": "User registered successfully",
        "username": user.username,
    }


@router.post("/login", response_model=Token)
def login(login_data: UserLogin, db: Session = Depends(get_db)) -> Token:
    return AuthService.login_user(db=db, login_data=login_data)


@router.post("/password-recovery/request")
@router.post("/recover")
def request_password_recovery(
    payload: PasswordRecoveryRequest,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    return AuthService.request_password_recovery(db=db, username=payload.username)


@router.post("/password-recovery/reset")
def reset_password(
    payload: PasswordResetRequest,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    return AuthService.reset_password(db=db, token=payload.token, new_password=payload.new_password)


@router.post("/logout")
def logout(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    token: str = Depends(get_current_token),
) -> dict[str, str]:
    return AuthService.logout_user(db=db, current_user=current_user, token=token)
