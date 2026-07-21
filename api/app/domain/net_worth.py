from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional, Sequence


BALANCE_SNAPSHOT_METHOD_OPENING = "statement_opening_balance"
BALANCE_SNAPSHOT_METHOD_CLOSING = "statement_closing_balance"

_SNAPSHOT_METHOD_PRIORITY = {
    BALANCE_SNAPSHOT_METHOD_OPENING: 0,
    BALANCE_SNAPSHOT_METHOD_CLOSING: 2,
}


@dataclass(frozen=True)
class StatementBalanceInput:
    account_id: str
    statement_id: str | None
    currency: str
    period_start: Optional[datetime]
    period_end: Optional[datetime]
    generated_at: Optional[datetime]
    created_at: Optional[datetime]
    opening_balance: Optional[Decimal]
    closing_balance: Optional[Decimal]
    parse_confidence: Optional[Decimal]
    reconcile_status: str


@dataclass(frozen=True)
class BalanceSnapshotCandidate:
    account_id: str
    statement_id: str | None
    timestamp: datetime
    balance: Decimal
    method: str
    confidence: Optional[Decimal]
    currency: str


@dataclass(frozen=True)
class AccountSnapshotScope:
    account_id: str
    currency: str
    include_in_net_worth: bool = True


def _snapshot_confidence(*, parse_confidence: Optional[Decimal], reconcile_status: str) -> Decimal:
    base = parse_confidence if parse_confidence is not None else Decimal("0.50")
    if reconcile_status == "ok":
        multiplier = Decimal("1.0")
    elif reconcile_status == "mismatch":
        multiplier = Decimal("0.3")
    else:
        multiplier = Decimal("0.7")
    result = base * multiplier
    if result < Decimal("0"):
        return Decimal("0")
    if result > Decimal("1"):
        return Decimal("1")
    return result


def build_balance_snapshots(
    statements: Sequence[StatementBalanceInput],
) -> list[BalanceSnapshotCandidate]:
    snapshots: list[BalanceSnapshotCandidate] = []
    for statement in statements:
        confidence = _snapshot_confidence(
            parse_confidence=statement.parse_confidence,
            reconcile_status=statement.reconcile_status or "unknown",
        )
        opening_ts = statement.period_start or statement.generated_at or statement.created_at
        closing_ts = statement.period_end or statement.generated_at or statement.created_at

        if statement.opening_balance is not None and opening_ts is not None:
            snapshots.append(
                BalanceSnapshotCandidate(
                    account_id=statement.account_id,
                    statement_id=statement.statement_id,
                    timestamp=opening_ts,
                    balance=statement.opening_balance,
                    method=BALANCE_SNAPSHOT_METHOD_OPENING,
                    confidence=confidence,
                    currency=statement.currency or "RUB",
                )
            )

        if statement.closing_balance is not None and closing_ts is not None:
            snapshots.append(
                BalanceSnapshotCandidate(
                    account_id=statement.account_id,
                    statement_id=statement.statement_id,
                    timestamp=closing_ts,
                    balance=statement.closing_balance,
                    method=BALANCE_SNAPSHOT_METHOD_CLOSING,
                    confidence=confidence,
                    currency=statement.currency or "RUB",
                )
            )

    return snapshots


def _method_priority(method: str) -> int:
    return _SNAPSHOT_METHOD_PRIORITY.get(method, 1)


def compute_net_worth_timeline(
    *,
    snapshots: Sequence[BalanceSnapshotCandidate],
    accounts: Sequence[AccountSnapshotScope],
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    granularity: str = "raw",
) -> dict:
    if not accounts:
        return {"generated_at": datetime.now(tz=timezone.utc), "granularity": "raw", "series": []}

    normalized_granularity = (granularity or "raw").strip().lower()
    if normalized_granularity not in {"raw", "day", "week", "month"}:
        normalized_granularity = "raw"

    scoped_accounts = [account for account in accounts if account.include_in_net_worth]
    if not scoped_accounts:
        return {
            "generated_at": datetime.now(tz=timezone.utc),
            "granularity": normalized_granularity,
            "series": [],
        }

    accounts_by_currency: dict[str, list[AccountSnapshotScope]] = {}
    for account in scoped_accounts:
        accounts_by_currency.setdefault(account.currency or "RUB", []).append(account)

    snapshots_by_currency: dict[str, list[BalanceSnapshotCandidate]] = {}
    for snapshot in snapshots:
        snapshots_by_currency.setdefault(snapshot.currency or "RUB", []).append(snapshot)

    def _completeness(latest: dict[str, Decimal], total_accounts: int) -> dict:
        with_snapshot = len(latest)
        missing = max(0, total_accounts - with_snapshot)
        completeness = (with_snapshot / total_accounts) if total_accounts > 0 else 0.0
        return {
            "accounts_total": total_accounts,
            "accounts_with_snapshot": with_snapshot,
            "accounts_missing": missing,
            "completeness": completeness,
        }

    def _floor_bucket(ts: datetime, bucket: str) -> datetime:
        if bucket == "day":
            return ts.replace(hour=0, minute=0, second=0, microsecond=0)
        if bucket == "week":
            start_of_week = ts - timedelta(days=ts.weekday())
            return start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)
        if bucket == "month":
            return ts.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return ts

    def _next_bucket_start(ts: datetime, bucket: str) -> datetime:
        if bucket == "day":
            return (ts + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        if bucket == "week":
            return (ts + timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
        if bucket == "month":
            year = ts.year + (1 if ts.month == 12 else 0)
            month = 1 if ts.month == 12 else ts.month + 1
            return ts.replace(
                year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0
            )
        return ts

    series: list[dict] = []
    for currency, account_scope in sorted(accounts_by_currency.items(), key=lambda pair: pair[0]):
        account_ids = [account.account_id for account in account_scope]
        total_accounts = len(account_ids)
        currency_snapshots = [
            snapshot
            for snapshot in snapshots_by_currency.get(currency, [])
            if snapshot.account_id in account_ids
        ]

        currency_snapshots.sort(
            key=lambda item: (item.timestamp, _method_priority(item.method), item.account_id)
        )

        latest_by_account: dict[str, Decimal] = {}
        if start is not None:
            for account_id in account_ids:
                eligible = [
                    item
                    for item in currency_snapshots
                    if item.account_id == account_id and item.timestamp <= start
                ]
                if not eligible:
                    continue
                eligible.sort(key=lambda item: (item.timestamp, _method_priority(item.method)))
                latest_by_account[account_id] = eligible[-1].balance

        filtered_snapshots = []
        for snapshot in currency_snapshots:
            if start is not None and snapshot.timestamp < start:
                continue
            if end is not None and snapshot.timestamp > end:
                continue
            filtered_snapshots.append(snapshot)

        points: list[dict] = []
        if normalized_granularity == "raw":
            if start is not None and latest_by_account:
                points.append(
                    {
                        "timestamp": start,
                        "total_balance": float(sum(latest_by_account.values(), Decimal("0"))),
                        **_completeness(latest_by_account, total_accounts),
                    }
                )

            index = 0
            while index < len(filtered_snapshots):
                timestamp = filtered_snapshots[index].timestamp
                while (
                    index < len(filtered_snapshots)
                    and filtered_snapshots[index].timestamp == timestamp
                ):
                    snapshot = filtered_snapshots[index]
                    latest_by_account[snapshot.account_id] = snapshot.balance
                    index += 1
                points.append(
                    {
                        "timestamp": timestamp,
                        "total_balance": float(sum(latest_by_account.values(), Decimal("0"))),
                        **_completeness(latest_by_account, total_accounts),
                    }
                )
        else:
            if not filtered_snapshots and not latest_by_account:
                series.append({"currency": currency, "points": []})
                continue

            if filtered_snapshots:
                series_start = start or filtered_snapshots[0].timestamp
                series_end = end or filtered_snapshots[-1].timestamp
            else:
                series_start = start or datetime.now(tz=timezone.utc)
                series_end = end or series_start

            current = _floor_bucket(series_start, normalized_granularity)
            end_exclusive = series_end + timedelta(microseconds=1)
            index = 0
            while current <= series_end:
                next_bucket = _next_bucket_start(current, normalized_granularity)
                bucket_end_exclusive = min(next_bucket, end_exclusive)

                while (
                    index < len(filtered_snapshots)
                    and filtered_snapshots[index].timestamp < bucket_end_exclusive
                ):
                    snapshot = filtered_snapshots[index]
                    latest_by_account[snapshot.account_id] = snapshot.balance
                    index += 1

                points.append(
                    {
                        "timestamp": min(
                            series_end, bucket_end_exclusive - timedelta(microseconds=1)
                        ),
                        "total_balance": float(sum(latest_by_account.values(), Decimal("0"))),
                        **_completeness(latest_by_account, total_accounts),
                    }
                )
                current = next_bucket

        series.append({"currency": currency, "points": points})

    return {
        "generated_at": datetime.now(tz=timezone.utc),
        "granularity": normalized_granularity,
        "series": series,
    }
