from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import MetricsQualityOut
from ..services.metrics_quality import build_metrics_quality_report

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("/quality", response_model=MetricsQualityOut)
def metrics_quality(db: Session = Depends(get_db)):
    return build_metrics_quality_report(db)
