ROLE_ALIASES = {
    "admin": "administrator",
    "administrator": "administrator",
    "reviewer": "reviewer",
    "user": "general_user",
    "general_user": "general_user",
    "auditor": "auditor",
}

VALID_ROLES = set(ROLE_ALIASES.keys())


def normalize_role(role: str) -> str:
    normalized = ROLE_ALIASES.get(role)
    if normalized is None:
        raise ValueError("Invalid role")
    return normalized


def ensure_valid_role(role: str) -> str:
    return normalize_role(role)
