#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

import psycopg
from psycopg.rows import dict_row

API_DIR = Path(__file__).resolve().parents[1] / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from app.database_url import (  # noqa: E402
    clean_database_url,
    validate_database_search_path,
    validate_database_url,
)
from app.services.statement_parser import _extract_merchant_label  # noqa: E402

DEFAULT_SCOPE_REGEX = r"all_[^/]+\.pdf$"


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _database_url_from_env() -> str:
    value = os.environ.get("DATABASE_URL", "")
    return clean_database_url(value)


def _database_search_path_from_env() -> str:
    return (os.environ.get("DB_SEARCH_PATH") or "").strip()


def _run_many_with_params(
    conn: psycopg.Connection[Any], query: str, params: tuple[Any, ...]
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall() or []
    return [dict(row) for row in rows]


def _apply_directional_category_guardrail(
    *,
    direction: str,
    category: str,
    meaning: str,
    review_status: str,
) -> str:
    if (meaning or "").strip().lower() == "internal_transfer":
        return category
    if (review_status or "").strip().lower() == "reviewed":
        return category

    normalized_category = (category or "").strip().lower()
    normalized_direction = (direction or "").strip().lower()
    if normalized_direction == "out" and normalized_category == "income":
        return "Spending"
    if normalized_direction == "in" and normalized_category == "spending":
        return "Income"
    return category


def _derive_merchant_normalized(
    *,
    merchant_normalized: str,
    description_raw: str,
    bank_category: str,
    direction: str,
) -> str:
    current = (merchant_normalized or "").strip()
    if current:
        return current
    return _extract_merchant_label(
        description_raw=description_raw or "",
        bank_category=bank_category or "",
        direction=direction or "",
    ).strip()


def _query_scoped_transactions(
    conn: psycopg.Connection[Any], *, scope_regex: str
) -> list[dict[str, Any]]:
    query = """
        with scoped_tx as (
          select distinct
            t.id as transaction_id,
            coalesce(t.direction, '') as direction,
            coalesce(t.category, '') as category,
            coalesce(t.meaning, '') as meaning,
            coalesce(t.review_status, 'needs_review') as review_status,
            coalesce(t.description_raw, '') as description_raw,
            coalesce(t.bank_category, '') as bank_category,
            coalesce(t.merchant_normalized, '') as merchant_normalized
          from public.transactions t
          join public.transaction_statement_links tsl on tsl.transaction_id = t.id
          join public.statement_rows sr on sr.id = tsl.statement_row_id
          join public.statements s on s.id = sr.statement_id
          where lower(regexp_replace(s.pdf_path, '^.*/', '')) ~ %s
            and coalesce(t.meaning, '') <> 'internal_transfer'
        )
        select *
        from scoped_tx
        order by transaction_id
    """
    return _run_many_with_params(conn, query, (scope_regex,))


def _plan_updates(rows: Iterable[Mapping[str, Any]], *, sample_limit: int) -> dict[str, Any]:
    operations: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    scanned = 0
    category_updates = 0
    merchant_updates = 0
    both_updates = 0

    for row in rows:
        scanned += 1
        tx_id = str(row.get("transaction_id") or "")
        if not tx_id:
            continue

        before_category = str(row.get("category") or "")
        before_merchant = str(row.get("merchant_normalized") or "")

        after_category = _apply_directional_category_guardrail(
            direction=str(row.get("direction") or ""),
            category=before_category,
            meaning=str(row.get("meaning") or ""),
            review_status=str(row.get("review_status") or ""),
        )
        after_merchant = _derive_merchant_normalized(
            merchant_normalized=before_merchant,
            description_raw=str(row.get("description_raw") or ""),
            bank_category=str(row.get("bank_category") or ""),
            direction=str(row.get("direction") or ""),
        )

        category_changed = after_category != before_category
        merchant_changed = after_merchant != before_merchant
        if not category_changed and not merchant_changed:
            continue

        if category_changed:
            category_updates += 1
        if merchant_changed:
            merchant_updates += 1
        if category_changed and merchant_changed:
            both_updates += 1

        operation = {
            "transaction_id": tx_id,
            "before_category": before_category,
            "after_category": after_category,
            "before_merchant_normalized": before_merchant,
            "after_merchant_normalized": after_merchant,
            "category_changed": category_changed,
            "merchant_changed": merchant_changed,
        }
        operations.append(operation)
        if len(samples) < max(0, sample_limit):
            samples.append(operation)

    return {
        "scanned_transactions": scanned,
        "planned_updates": len(operations),
        "category_updates": category_updates,
        "merchant_updates": merchant_updates,
        "both_updates": both_updates,
        "operations": operations,
        "sample": samples,
    }


def _apply_updates(
    conn: psycopg.Connection[Any], operations: Iterable[Mapping[str, Any]]
) -> dict[str, int]:
    applied = 0
    with conn.cursor() as cur:
        for operation in operations:
            tx_id = str(operation.get("transaction_id") or "")
            if not tx_id:
                continue
            cur.execute(
                """
                update public.transactions
                   set category = %s,
                       merchant_normalized = %s
                 where id = %s
                """,
                (
                    str(operation.get("after_category") or ""),
                    str(operation.get("after_merchant_normalized") or ""),
                    tx_id,
                ),
            )
            if cur.rowcount:
                applied += int(cur.rowcount)
    conn.commit()
    return {"applied_updates": applied}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Deterministic backfill for directional category guardrail "
            "and merchant normalization in canonical transactions."
        )
    )
    parser.add_argument(
        "--scope-regex",
        default=DEFAULT_SCOPE_REGEX,
        help="Regex applied to statement source-file suffix (default: all_*.pdf scope).",
    )
    parser.add_argument(
        "--database-url",
        default="",
        help="Optional DATABASE_URL override.",
    )
    parser.add_argument(
        "--search-path",
        default="",
        help="Optional search_path override. Defaults to DB_SEARCH_PATH.",
    )
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=25,
        help="Max number of planned update samples in output.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply updates. Without this flag, script runs in dry-run mode.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    database_url = clean_database_url(args.database_url) or _database_url_from_env()
    search_path = args.search_path.strip() or _database_search_path_from_env()
    validated_url = validate_database_url(database_url)
    validated_search_path = validate_database_search_path(search_path)

    conn_kwargs: dict[str, Any] = {"row_factory": dict_row}
    if validated_search_path:
        conn_kwargs["options"] = f"-c search_path={validated_search_path}"

    with psycopg.connect(validated_url, **conn_kwargs) as conn:
        rows = _query_scoped_transactions(conn, scope_regex=args.scope_regex)
        plan = _plan_updates(rows, sample_limit=args.sample_limit)

        result: dict[str, Any] = {
            "mode": "apply" if args.apply else "dry_run",
            "scope_regex": args.scope_regex,
            "database_search_path": validated_search_path or None,
            "summary": {
                "scanned_transactions": plan["scanned_transactions"],
                "planned_updates": plan["planned_updates"],
                "category_updates": plan["category_updates"],
                "merchant_updates": plan["merchant_updates"],
                "both_updates": plan["both_updates"],
            },
            "sample": plan["sample"],
        }

        if args.apply:
            apply_result = _apply_updates(conn, plan["operations"])
            result["apply"] = apply_result

    print(json.dumps(result, indent=2, sort_keys=True, default=_json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
