from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    code: str = Field(min_length=2, max_length=32, pattern=r"^[A-Z0-9_-]+$")


class OrganizationJoin(BaseModel):
    role: Literal["general_user"] = "general_user"


class OrganizationRoleUpdate(BaseModel):
    role: Literal["admin", "administrator", "general_user", "reviewer", "auditor"]


class OrganizationOut(BaseModel):
    id: UUID
    name: str
    code: str
    created_at: datetime


class OrganizationUserOut(BaseModel):
    id: str
    username: str
    organization_id: str | None
    role: str
    is_active: bool
    created_at: datetime
