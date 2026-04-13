from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps.auth import require_domain_permission
from app.db.deps import get_db
from app.models.user import User
from app.schemas.analytics import (
    ActivityAnalyticsOut,
    AnalyticsDashboardOut,
    AnalyticsSearchResponseOut,
    Granularity,
    SearchType,
    SlaAnalyticsOut,
)
from app.services.analytics_service import AnalyticsService


router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


@router.get("/dashboard", response_model=AnalyticsDashboardOut)
def get_dashboard(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_domain_permission("analytics", "dashboard")),
) -> AnalyticsDashboardOut:
    return AnalyticsService.get_dashboard(db=db, current_user=current_user)


@router.get("/activity", response_model=ActivityAnalyticsOut)
def get_activity_analytics(
    start_date: datetime,
    end_date: datetime,
    granularity: Granularity = Query(default="daily"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_domain_permission("analytics", "activity")),
) -> ActivityAnalyticsOut:
    return AnalyticsService.get_activity_analytics(
        db=db,
        current_user=current_user,
        start_date=start_date,
        end_date=end_date,
        granularity=granularity,
    )


@router.get("/sla", response_model=SlaAnalyticsOut)
def get_sla_analytics(
    start_date: datetime,
    end_date: datetime,
    granularity: Granularity = Query(default="daily"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_domain_permission("analytics", "sla")),
) -> SlaAnalyticsOut:
    return AnalyticsService.get_sla_analytics(
        db=db,
        current_user=current_user,
        start_date=start_date,
        end_date=end_date,
        granularity=granularity,
    )


@router.get("/search", response_model=AnalyticsSearchResponseOut)
def search_analytics(
    type: SearchType,
    status: str | None = None,
    user_id: str | None = None,
    assignee_id: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    keyword: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_domain_permission("analytics", "search")),
) -> AnalyticsSearchResponseOut:
    return AnalyticsService.search(
        db=db,
        current_user=current_user,
        search_type=type,
        status_filter=status,
        user_id=user_id,
        assignee_id=assignee_id,
        start_date=start_date,
        end_date=end_date,
        keyword=keyword,
        limit=limit,
        offset=offset,
    )
