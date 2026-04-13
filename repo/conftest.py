import os
import sys
from pathlib import Path


SERVER_ROOT = Path(__file__).resolve().parent / "server"

os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key")
os.environ.setdefault("SENSITIVE_DATA_KEY", "test-sensitive-data-key")
os.environ.setdefault("EXPOSE_RESET_TOKEN", "false")
os.environ.setdefault("REQUIRE_HTTPS", "true")

if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))
