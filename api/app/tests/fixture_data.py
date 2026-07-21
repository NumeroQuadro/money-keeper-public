from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from app.domain.net_worth import AccountSnapshotScope, StatementBalanceInput
from app.services.statement_parser import (
    ParsedRow,
    ParsedStatement,
    ParsedStatementBundle,
    ParsedTransaction,
)


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _parse_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def load_crossbank_dataset() -> dict[str, Any]:
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "dataset_crossbank.yaml"
    with fixture_path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _rows_by_statement(dataset: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    rows_by_statement: dict[str, list[dict[str, Any]]] = {}
    for row in dataset["rows"]:
        rows_by_statement.setdefault(row["statement_id"], []).append(row)
    return rows_by_statement


def _build_bundle(
    statement_data: dict[str, Any], rows_by_statement: dict[str, list[dict[str, Any]]]
) -> ParsedStatementBundle:
    statement_id = statement_data["id"]
    parsed_rows: list[ParsedRow] = []
    parsed_txs: list[tuple[int, ParsedTransaction]] = []

    for row_data in sorted(
        rows_by_statement.get(statement_id, []), key=lambda item: item["row_index"]
    ):
        parsed_rows.append(
            ParsedRow(
                row_index=int(row_data["row_index"]),
                page_number=int(row_data["page_number"]),
                raw_text=row_data["raw_text"],
                raw_data={"fixture_statement_id": statement_id},
                operation_date=_parse_datetime(row_data.get("operation_date")),
                posting_date=_parse_datetime(row_data.get("posting_date")),
                amount=_parse_decimal(row_data.get("amount")),
                currency=row_data.get("currency", "RUB"),
                direction=row_data.get("direction", "out"),
                parse_confidence=Decimal("1.00"),
                timestamp_precision=row_data.get("timestamp_precision", "unknown"),
            )
        )

        tx_data = row_data["tx"]
        parsed_txs.append(
            (
                int(row_data["row_index"]),
                ParsedTransaction(
                    amount=_parse_decimal(tx_data.get("amount")) or Decimal("0"),
                    currency=tx_data.get("currency", "RUB"),
                    direction=tx_data.get("direction", "out"),
                    operation_datetime=_parse_datetime(tx_data.get("operation_datetime")),
                    posting_datetime=_parse_datetime(tx_data.get("posting_datetime")),
                    description_raw=tx_data.get("description_raw", ""),
                    merchant_normalized=tx_data.get("merchant_normalized", ""),
                    bank_reference_id=tx_data.get("bank_reference_id", ""),
                    bank_category=tx_data.get("bank_category", ""),
                    meaning=tx_data.get("meaning", "unknown"),
                    meaning_confidence=_parse_decimal(tx_data.get("meaning_confidence"))
                    or Decimal("0"),
                    category=tx_data.get("category", ""),
                    tags=list(tx_data.get("tags", []) or []),
                    timestamp_precision=tx_data.get("timestamp_precision", "unknown"),
                ),
            )
        )

    return ParsedStatementBundle(
        meta=ParsedStatement(
            provider=statement_data.get("provider", "unknown"),
            statement_type=statement_data.get("statement_type", "unknown"),
            currency=statement_data.get("currency", "RUB"),
            account_display=statement_data["account_id"],
            period_start=_parse_datetime(statement_data.get("period_start")),
            period_end=_parse_datetime(statement_data.get("period_end")),
            generated_at=_parse_datetime(statement_data.get("generated_at")),
            opening_balance=_parse_decimal(statement_data.get("opening_balance")),
            closing_balance=_parse_decimal(statement_data.get("closing_balance")),
            total_credits=_parse_decimal(statement_data.get("total_credits")),
            total_debits=_parse_decimal(statement_data.get("total_debits")),
            reconcile_status=statement_data.get("reconcile_status", "unknown"),
            parse_confidence=_parse_decimal(statement_data.get("parse_confidence")) or Decimal("0"),
        ),
        rows=parsed_rows,
        txs=parsed_txs,
    )


def build_parsed_bundle_map(dataset: dict[str, Any]) -> dict[str, ParsedStatementBundle]:
    rows_by_statement = _rows_by_statement(dataset)
    return {
        statement_data["id"]: _build_bundle(statement_data, rows_by_statement)
        for statement_data in dataset["statements"]
    }


def build_parsed_bundles(dataset: dict[str, Any]) -> list[ParsedStatementBundle]:
    return list(build_parsed_bundle_map(dataset).values())


def build_statement_balance_inputs(dataset: dict[str, Any]) -> list[StatementBalanceInput]:
    return [
        StatementBalanceInput(
            account_id=statement["account_id"],
            statement_id=statement["id"],
            currency=statement.get("currency", "RUB"),
            period_start=_parse_datetime(statement.get("period_start")),
            period_end=_parse_datetime(statement.get("period_end")),
            generated_at=_parse_datetime(statement.get("generated_at")),
            created_at=_parse_datetime(statement.get("generated_at"))
            or _parse_datetime(statement.get("period_end")),
            opening_balance=_parse_decimal(statement.get("opening_balance")),
            closing_balance=_parse_decimal(statement.get("closing_balance")),
            parse_confidence=_parse_decimal(statement.get("parse_confidence")),
            reconcile_status=statement.get("reconcile_status", "unknown"),
        )
        for statement in dataset["statements"]
    ]


def build_account_scopes(dataset: dict[str, Any]) -> list[AccountSnapshotScope]:
    return [
        AccountSnapshotScope(
            account_id=account["id"],
            currency=account.get("currency", "RUB"),
            include_in_net_worth=True,
        )
        for account in dataset["accounts"]
    ]
