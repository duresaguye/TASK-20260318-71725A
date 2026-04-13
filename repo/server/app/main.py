import asyncio
import contextlib
import os

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

import app.models.audit_log  # noqa: F401
import app.models.attachment_metadata  # noqa: F401
import app.models.data_dictionary  # noqa: F401
import app.models.data_import_batch  # noqa: F401
import app.models.data_import_error  # noqa: F401
import app.models.data_lineage  # noqa: F401
import app.models.data_quality_rule  # noqa: F401
import app.models.data_version  # noqa: F401
import app.models.export_job  # noqa: F401
import app.models.hospital_records  # noqa: F401
import app.models.idempotency_key  # noqa: F401
import app.models.maintenance_job  # noqa: F401
import app.models.operational_metric_snapshot  # noqa: F401
import app.models.organization  # noqa: F401
import app.models.password_reset_token  # noqa: F401
import app.models.process_definition  # noqa: F401
import app.models.process_instance  # noqa: F401
import app.models.revoked_token  # noqa: F401
import app.models.task_comment  # noqa: F401
import app.models.user  # noqa: F401
import app.models.workflow_task  # noqa: F401
import app.models.workflow_submission_record  # noqa: F401
from app.api.deps.auth import get_current_user
from app.api.routes.auth import router as auth_router
from app.api.routes.analytics import router as analytics_router
from app.api.routes.data_governance import router as data_governance_router
from app.api.routes.exports import router as exports_router
from app.api.routes.organizations import router as organizations_router
from app.api.routes.workflows import router as workflows_router
from app.core.error_handlers import register_error_handlers
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.services.maintenance_service import run_maintenance_monitor
from app.services.workflow_sla_service import run_sla_monitor


app = FastAPI(
    title="Medical Operations and Process Governance Middle Platform API Service"
)
register_error_handlers(app)
REQUIRE_HTTPS = os.getenv("REQUIRE_HTTPS", "true").lower() == "true"


@app.middleware("http")
async def enforce_https(request: Request, call_next):
    if REQUIRE_HTTPS:
        if request.url.scheme != "https":
            return JSONResponse(
                status_code=400,
                content={"error_code": "HTTPS_REQUIRED", "message": "HTTPS is required"},
            )
    return await call_next(request)


app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")


@app.on_event("startup")
async def on_startup() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        from app.core.access_policy import AccessPolicy

        AccessPolicy.seed_default_permissions(db)
    finally:
        db.close()
    app.state.sla_monitor_task = asyncio.create_task(run_sla_monitor())
    app.state.maintenance_monitor_task = asyncio.create_task(run_maintenance_monitor())


@app.on_event("shutdown")
async def on_shutdown() -> None:
    sla_task = getattr(app.state, "sla_monitor_task", None)
    maintenance_task = getattr(app.state, "maintenance_monitor_task", None)
    if sla_task is not None:
        sla_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sla_task
    if maintenance_task is not None:
        maintenance_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await maintenance_task


app.include_router(auth_router)
app.include_router(analytics_router)
app.include_router(organizations_router)
app.include_router(workflows_router)
app.include_router(exports_router)
app.include_router(data_governance_router)


@app.get("/health", tags=["system"])
def health_check(_: object = Depends(get_current_user)) -> dict[str, str]:
    return {"status": "ok"}
