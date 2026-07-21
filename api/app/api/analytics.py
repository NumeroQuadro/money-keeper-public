from __future__ import annotations

from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..domain.cashflow_lens import CashflowLens, DEFAULT_CASHFLOW_LENS
from ..schemas import (
    AnalyticsIncomeBreakdownOut,
    AnalyticsMonthlyFlowOut,
    AnalyticsSpendMixOut,
    AnalyticsTopMerchantsOut,
)
from ..services.analytics import (
    get_income_breakdown,
    get_monthly_flow,
    get_spend_mix,
    get_top_merchants,
)

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/monthly-flow", response_model=AnalyticsMonthlyFlowOut)
def analytics_monthly_flow(
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    currency: Optional[str] = None,
    account_id: Optional[str] = None,
    include_transfers: bool = False,
    cashflow_lens: CashflowLens = DEFAULT_CASHFLOW_LENS,
    db: Session = Depends(get_db),
):
    return get_monthly_flow(
        db,
        start=start,
        end=end,
        currency=currency,
        account_id=account_id,
        include_transfers=include_transfers,
        cashflow_lens=cashflow_lens,
    )


@router.get("/spend-mix", response_model=AnalyticsSpendMixOut)
def analytics_spend_mix(
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    currency: Optional[str] = None,
    account_id: Optional[str] = None,
    include_transfers: bool = False,
    cashflow_lens: CashflowLens = DEFAULT_CASHFLOW_LENS,
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    db: Session = Depends(get_db),
):
    return get_spend_mix(
        db,
        start=start,
        end=end,
        currency=currency,
        account_id=account_id,
        include_transfers=include_transfers,
        cashflow_lens=cashflow_lens,
        limit=limit,
    )


@router.get("/income-breakdown", response_model=AnalyticsIncomeBreakdownOut)
def analytics_income_breakdown(
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    currency: Optional[str] = None,
    account_id: Optional[str] = None,
    include_transfers: bool = False,
    cashflow_lens: CashflowLens = DEFAULT_CASHFLOW_LENS,
    db: Session = Depends(get_db),
):
    return get_income_breakdown(
        db,
        start=start,
        end=end,
        currency=currency,
        account_id=account_id,
        include_transfers=include_transfers,
        cashflow_lens=cashflow_lens,
    )


@router.get("/top-merchants", response_model=AnalyticsTopMerchantsOut)
def analytics_top_merchants(
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    currency: Optional[str] = None,
    account_id: Optional[str] = None,
    include_transfers: bool = False,
    cashflow_lens: CashflowLens = DEFAULT_CASHFLOW_LENS,
    limit: Annotated[int, Query(ge=1, le=200)] = 30,
    db: Session = Depends(get_db),
):
    return get_top_merchants(
        db,
        start=start,
        end=end,
        currency=currency,
        account_id=account_id,
        include_transfers=include_transfers,
        cashflow_lens=cashflow_lens,
        limit=limit,
    )
