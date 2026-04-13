from app.core.rbac import normalize_role


class ResponseSecurityService:
    @staticmethod
    def mask_email(email: str) -> str:
        if "@" not in email:
            return "***"
        local, domain = email.split("@", 1)
        if len(local) <= 2:
            masked_local = "*" * len(local)
        else:
            masked_local = local[:1] + "***" + local[-1:]
        return f"{masked_local}@{domain}"

    @staticmethod
    def mask_phone(phone: str) -> str:
        tail = phone[-4:] if len(phone) >= 4 else phone
        return "***-***-" + tail

    @staticmethod
    def mask_identifier(identifier: str | None) -> str | None:
        if identifier is None:
            return None
        if len(identifier) <= 8:
            return "***"
        return identifier[:4] + "***" + identifier[-4:]

    @staticmethod
    def mask_username(username: str) -> str:
        if len(username) <= 2:
            return "*" * len(username)
        return username[:1] + "***" + username[-1:]

    @staticmethod
    def mask_user_summary(current_role: str, user) -> dict:
        role = normalize_role(current_role)
        if role == "administrator":
            return {
                "id": str(user.id),
                "username": user.username,
                "organization_id": str(user.organization_id) if user.organization_id else None,
                "role": user.role,
                "is_active": user.is_active,
                "created_at": user.created_at,
            }
        if role == "auditor":
            return {
                "id": ResponseSecurityService.mask_identifier(str(user.id)),
                "username": ResponseSecurityService.mask_username(user.username),
                "organization_id": ResponseSecurityService.mask_identifier(str(user.organization_id)) if user.organization_id else None,
                "role": user.role,
                "is_active": user.is_active,
                "created_at": user.created_at,
            }
        return {
            "id": ResponseSecurityService.mask_identifier(str(user.id)),
            "username": ResponseSecurityService.mask_username(user.username),
            "organization_id": ResponseSecurityService.mask_identifier(str(user.organization_id)) if user.organization_id else None,
            "role": user.role,
            "is_active": user.is_active,
            "created_at": user.created_at,
        }

    @staticmethod
    def mask_search_data(current_role: str, data: dict) -> dict:
        role = normalize_role(current_role)
        if role == "administrator":
            return data
        return ResponseSecurityService._mask_mapping(data)

    @staticmethod
    def _mask_mapping(value):
        if isinstance(value, dict):
            return {key: ResponseSecurityService._mask_field(str(key), item) for key, item in value.items()}
        if isinstance(value, list):
            return [ResponseSecurityService._mask_mapping(item) for item in value]
        return value

    @staticmethod
    def _mask_field(key: str, value):
        if isinstance(value, dict):
            return ResponseSecurityService._mask_mapping(value)
        if isinstance(value, list):
            return [ResponseSecurityService._mask_mapping(item) for item in value]
        if value is None:
            return None

        key_lower = key.lower()
        text_value = str(value)

        if "email" in key_lower:
            return ResponseSecurityService.mask_email(text_value)
        if "phone" in key_lower:
            return ResponseSecurityService.mask_phone(text_value)
        if key_lower in {"username", "full_name", "name", "campaign_name", "step_name"}:
            return ResponseSecurityService.mask_username(text_value)
        if key_lower.endswith("id") or key_lower.endswith("_id"):
            return ResponseSecurityService.mask_identifier(text_value)
        if key_lower in {
            "medical_record_number",
            "appointment_number",
            "expense_number",
            "attendance_number",
            "message_number",
            "employee_number",
        }:
            return ResponseSecurityService.mask_identifier(text_value)
        return value
