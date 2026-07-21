from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..domain.cashflow_lens import (
    DEFAULT_CASHFLOW_LENS,
    normalize_cashflow_lens,
    transfer_exclusion_params,
    transfer_exclusion_predicate_sql,
)


def _to_float(value: Any) -> float:
    if isinstance(value, Decimal):
        return float(value)
    if value is None:
        return 0.0
    return float(value)


def _base_params(
    *,
    start: datetime | None,
    end: datetime | None,
    currency: str | None,
    account_id: str | None,
    include_transfers: bool,
) -> tuple[list[str], dict[str, Any]]:
    filters: list[str] = []
    params: dict[str, Any] = {
        "include_transfers": include_transfers,
    }
    if start is not None:
        filters.append("coalesce(t.operation_datetime, t.created_at) >= :start")
        params["start"] = start
    if end is not None:
        filters.append("coalesce(t.operation_datetime, t.created_at) <= :end")
        params["end"] = end
    if currency is not None:
        filters.append("t.currency = :currency")
        params["currency"] = currency
    if account_id is not None:
        filters.append("t.account_id = :account_id")
        params["account_id"] = account_id

    return filters, params


def get_monthly_flow(
    db: Session,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    currency: str | None = None,
    account_id: str | None = None,
    include_transfers: bool = False,
    cashflow_lens: str = DEFAULT_CASHFLOW_LENS,
) -> dict[str, Any]:
    normalized_lens = normalize_cashflow_lens(cashflow_lens)
    exclusion_sql = transfer_exclusion_predicate_sql(alias="t", cashflow_lens=normalized_lens)
    filters, params = _base_params(
        start=start,
        end=end,
        currency=currency,
        account_id=account_id,
        include_transfers=include_transfers,
    )
    where_clauses = [
        *filters,
        f"(:include_transfers = true or not ({exclusion_sql}))",
    ]
    query = f"""
        select
          date_trunc('month', coalesce(t.operation_datetime, t.created_at)) as period_start,
          count(*)::int as tx_count,
          coalesce(sum(case when t.direction = 'in' then t.amount else 0 end), 0) as inflow,
          coalesce(sum(case when t.direction = 'out' then t.amount else 0 end), 0) as outflow
        from transactions t
        where {" and ".join(where_clauses)}
        group by 1
        order by 1
    """
    params.update(transfer_exclusion_params())

    rows = db.execute(text(query), params).mappings().all()

    items = []
    for row in rows:
        period_start = row.get("period_start")
        period = period_start.strftime("%Y-%m") if isinstance(period_start, datetime) else ""
        inflow = _to_float(row.get("inflow"))
        outflow = _to_float(row.get("outflow"))
        items.append(
            {
                "period": period,
                "inflow": inflow,
                "outflow": outflow,
                "net": inflow - outflow,
                "tx_count": int(row.get("tx_count") or 0),
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc),
        "start": start,
        "end": end,
        "currency": currency,
        "account_id": account_id,
        "include_transfers": include_transfers,
        "cashflow_lens": normalized_lens,
        "items": items,
    }


def get_spend_mix(
    db: Session,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    currency: str | None = None,
    account_id: str | None = None,
    include_transfers: bool = False,
    cashflow_lens: str = DEFAULT_CASHFLOW_LENS,
    limit: int = 25,
) -> dict[str, Any]:
    normalized_lens = normalize_cashflow_lens(cashflow_lens)
    exclusion_sql = transfer_exclusion_predicate_sql(alias="t", cashflow_lens=normalized_lens)
    filters, params = _base_params(
        start=start,
        end=end,
        currency=currency,
        account_id=account_id,
        include_transfers=include_transfers,
    )
    where_clauses = [
        "t.direction = 'out'",
        *filters,
        f"(:include_transfers = true or not ({exclusion_sql}))",
    ]
    query = f"""
        select
          coalesce(nullif(btrim(t.category), ''), 'Uncategorized') as category,
          count(*)::int as tx_count,
          coalesce(
            sum(case when t.amount < 0 then -t.amount else t.amount end),
            0
          ) as spent
        from transactions t
        where {" and ".join(where_clauses)}
        group by 1
        order by spent desc, category asc
        limit :limit
    """
    params["limit"] = limit
    params.update(transfer_exclusion_params())

    rows = db.execute(text(query), params).mappings().all()
    items = [
        {
            "category": str(row.get("category") or ""),
            "spent": _to_float(row.get("spent")),
            "tx_count": int(row.get("tx_count") or 0),
        }
        for row in rows
    ]

    return {
        "generated_at": datetime.now(timezone.utc),
        "start": start,
        "end": end,
        "currency": currency,
        "account_id": account_id,
        "include_transfers": include_transfers,
        "cashflow_lens": normalized_lens,
        "limit": limit,
        "items": items,
    }


def get_income_breakdown(
    db: Session,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    currency: str | None = None,
    account_id: str | None = None,
    include_transfers: bool = False,
    cashflow_lens: str = DEFAULT_CASHFLOW_LENS,
) -> dict[str, Any]:
    normalized_lens = normalize_cashflow_lens(cashflow_lens)
    exclusion_sql = transfer_exclusion_predicate_sql(alias="t", cashflow_lens=normalized_lens)
    filters, params = _base_params(
        start=start,
        end=end,
        currency=currency,
        account_id=account_id,
        include_transfers=include_transfers,
    )
    where_clauses = [
        "t.direction = 'in'",
        *filters,
        f"(:include_transfers = true or not ({exclusion_sql}))",
    ]
    query = f"""
        select
          case
            when lower(coalesce(nullif(btrim(t.meaning), ''), 'unknown'))
              in ('salary', 'interest', 'cashback') then lower(t.meaning)
            when lower(coalesce(nullif(btrim(t.meaning), ''), 'unknown')) = 'refund' then 'refund'
            else 'other'
          end as income_bucket,
          count(*)::int as tx_count,
          coalesce(
            sum(case when t.amount < 0 then -t.amount else t.amount end),
            0
          ) as income
        from transactions t
        where {" and ".join(where_clauses)}
        group by 1
        order by income desc, income_bucket asc
    """
    params.update(transfer_exclusion_params())

    rows = db.execute(text(query), params).mappings().all()
    items = [
        {
            "income_bucket": str(row.get("income_bucket") or ""),
            "income": _to_float(row.get("income")),
            "tx_count": int(row.get("tx_count") or 0),
        }
        for row in rows
    ]

    return {
        "generated_at": datetime.now(timezone.utc),
        "start": start,
        "end": end,
        "currency": currency,
        "account_id": account_id,
        "include_transfers": include_transfers,
        "cashflow_lens": normalized_lens,
        "items": items,
    }


def get_top_merchants(
    db: Session,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    currency: str | None = None,
    account_id: str | None = None,
    include_transfers: bool = False,
    cashflow_lens: str = DEFAULT_CASHFLOW_LENS,
    limit: int = 30,
) -> dict[str, Any]:
    normalized_lens = normalize_cashflow_lens(cashflow_lens)
    exclusion_sql = transfer_exclusion_predicate_sql(alias="t", cashflow_lens=normalized_lens)
    filters, params = _base_params(
        start=start,
        end=end,
        currency=currency,
        account_id=account_id,
        include_transfers=include_transfers,
    )
    where_clauses = [
        "t.direction = 'out'",
        *filters,
        f"(:include_transfers = true or not ({exclusion_sql}))",
    ]
    query = f"""
        select
          coalesce(
            nullif(btrim(t.merchant_normalized), ''),
            nullif(btrim(t.bank_category), ''),
            'Unknown'
          ) as merchant,
          count(*)::int as tx_count,
          coalesce(
            sum(case when t.amount < 0 then -t.amount else t.amount end),
            0
          ) as spent
        from transactions t
        where {" and ".join(where_clauses)}
        group by 1
        order by spent desc, merchant asc
        limit :limit
    """
    params["limit"] = limit
    params.update(transfer_exclusion_params())

    rows = db.execute(text(query), params).mappings().all()
    items = [
        {
            "merchant": str(row.get("merchant") or ""),
            "spent": _to_float(row.get("spent")),
            "tx_count": int(row.get("tx_count") or 0),
        }
        for row in rows
    ]

    return {
        "generated_at": datetime.now(timezone.utc),
        "start": start,
        "end": end,
        "currency": currency,
        "account_id": account_id,
        "include_transfers": include_transfers,
        "cashflow_lens": normalized_lens,
        "limit": limit,
        "items": items,
    }
