import pytest

from app.core.access_policy import AccessPolicy
from app.services.auth_service import AuthService
from app.services.export_service import ExportService
from app.schemas.organization import OrganizationJoin


def test_password_policy_rejects_weak_password():
    with pytest.raises(Exception):
        AuthService._validate_password_strength("weak")


def test_password_policy_accepts_letters_and_numbers():
    AuthService._validate_password_strength("Strong123")


def test_export_role_cannot_access_users_dataset_for_general_user():
    with pytest.raises(Exception):
        ExportService._resolve_fields("users", ["id"], "general_user")


def test_export_role_can_access_task_dataset_for_reviewer():
    fields = ExportService._resolve_fields("tasks", ["id", "status"], "reviewer")
    assert fields == ["id", "status"]


def test_organization_join_does_not_accept_privileged_roles():
    with pytest.raises(Exception):
        OrganizationJoin(role="administrator")


def test_access_policy_includes_role_assignment_and_reviewer_attachment_download():
    assert AccessPolicy.allowed("administrator", "organization", "assign_role") is True
    assert AccessPolicy.allowed("reviewer", "attachments", "download") is True
