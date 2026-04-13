import hashlib
import os

from fastapi import HTTPException, status


class FileSecurityService:
    MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024
    ALLOWED_CONTENT_TYPES = {
        "application/pdf",
        "text/csv",
        "application/json",
        "text/plain",
    }
    BLOCKED_EXTENSIONS = {
        ".exe",
        ".bat",
        ".cmd",
        ".sh",
        ".js",
        ".msi",
        ".dll",
        ".com",
    }

    @staticmethod
    def validate_upload(*, file_name: str, content_type: str, content: bytes) -> str:
        if len(content) > FileSecurityService.MAX_FILE_SIZE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="File exceeds maximum allowed size",
            )

        extension = os.path.splitext(file_name)[1].lower()
        if extension in FileSecurityService.BLOCKED_EXTENSIONS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unsafe file extension is not allowed",
            )

        if content_type not in FileSecurityService.ALLOWED_CONTENT_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unsupported file type",
            )

        return hashlib.sha256(content).hexdigest()
