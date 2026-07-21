#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

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

DEFAULT_SCOPE_REGEX = r"all_[^/]+\.pdf$"
WHITESPACE_RE = re.compile(r"\s+")
SOURCE_FILE_SUFFIX_RE = re.compile(r"(all_[^/]+\.pdf)$")
FINGERPRINT_VERSION = "v1"


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _database_url_from_env() -> str:
    return clean_database_url(os.environ.get("DATABASE_URL", ""))


def _database_search_path_from_env() -> str:
    return (os.environ.get("DB_SEARCH_PATH") or "").strip()


def _to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None:
        return Decimal("0")
    text = str(value).strip()
    if not text:
        return Decimal("0")
    normalized = text.replace("\u00a0", "").replace(" ", "").replace(",", ".")
    return Decimal(normalized)


def _normalize_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    return WHITESPACE_RE.sub(" ", text) if text else ""


def _normalize_source_file_key(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("\\", "/")
    if not normalized:
        return ""
    match = SOURCE_FILE_SUFFIX_RE.search(normalized)
    if match:
        return match.group(1)
    return normalized.rsplit("/", 1)[-1]


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    raw = str(value).strip()
    if not raw:
        return None
    normalized = raw.replace(" ", "T")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _timestamp_key(value: Any) -> str:
    dt = _as_datetime(value)
    if dt is None:
        return ""
    return dt.replace(microsecond=0).isoformat()


def _amount_cents(value: Any) -> int:
    amount = _to_decimal(value).quantize(Decimal("0.01"))
    return int(amount * Decimal("100"))


def _statement_row_fingerprint_payload(*, account_id: str, row: Mapping[str, Any]) -> str:
    components = [
        "row-v1",
        str(account_id or ""),
        _normalize_text(row.get("row_currency") or "RUB"),
        _normalize_text(row.get("row_direction") or "out"),
        str(abs(_amount_cents(row.get("row_amount")))),
        _timestamp_key(row.get("operation_date")),
        _timestamp_key(row.get("posting_date")),
        _normalize_text(row.get("row_bank_reference_id")),
        _normalize_text(row.get("row_bank_category")),
        _normalize_text(row.get("row_raw_text")),
    ]
    return "|".join(components)


def _classify_collapse_type(*, distinct_statements: int, unique_row_fingerprints: int) -> str:
    if distinct_statements > 1 and unique_row_fingerprints == 1:
        return "expected_overlap_dedupe"
    if distinct_statements > 1:
        return "cross_statement_variance_merge"
    if unique_row_fingerprints == 1:
        return "same_statement_duplicate_collapse"
    return "same_statement_variance_merge"


def _transaction_fingerprint(
    *,
    account_id: str,
    currency: str,
    direction: str,
    amount: Any,
    operation_datetime: Any,
    posting_datetime: Any,
    bank_reference_id: str,
    description_raw: str,
    bank_category: str,
    raw_text: str,
) -> str:
    components = [
        FINGERPRINT_VERSION,
        str(account_id or ""),
        str(currency or "RUB"),
        str(direction or "out"),
        str(_amount_cents(amount)),
        _timestamp_key(operation_datetime),
        _timestamp_key(posting_datetime),
        _normalize_text(bank_reference_id),
        _normalize_text(description_raw),
        _normalize_text(bank_category),
        _normalize_text(raw_text),
    ]
    payload = "|".join(components)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _stmtdup_group_key(row: Mapping[str, Any], detail: Mapping[str, Any]) -> tuple[str, ...]:
    tx_amount = _to_decimal(row.get("transaction_amount") or detail.get("row_amount"))
    return (
        str(row.get("account_id") or ""),
        str(row.get("transaction_currency") or detail.get("row_currency") or "RUB"),
        str(row.get("transaction_direction") or detail.get("row_direction") or "out"),
        f"{tx_amount.quantize(Decimal('0.01'))}",
        _timestamp_key(row.get("operation_datetime") or detail.get("operation_date")),
        _timestamp_key(row.get("posting_datetime") or detail.get("posting_date")),
        _normalize_text(row.get("transaction_description_raw")),
        _normalize_text(row.get("transaction_bank_category") or detail.get("row_bank_category")),
        _normalize_text(detail.get("row_raw_text")),
    )


def _synthetic_reference(*, statement_id: str, group_key: Sequence[str], occurrence: int) -> str:
    payload = "|".join(("stmtdup", statement_id, *group_key, str(occurrence)))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:24]


def _run_many_with_params(
    conn: psycopg.Connection[Any],
    query: str,
    params: tuple[Any, ...],
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall() or []
    return [dict(row) for row in rows]


def _run_one_with_params(
    conn: psycopg.Connection[Any],
    query: str,
    params: tuple[Any, ...],
) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(query, params)
        row = cur.fetchone()
    return dict(row) if row else None


def _query_collapsed_rows(
    conn: psycopg.Connection[Any], *, scope_regex: str
) -> list[dict[str, Any]]:
    query = """
        with scoped_links as (
          select
            t.id as transaction_id,
            t.account_id,
            t.dedup_key,
            t.amount as transaction_amount,
            t.currency as transaction_currency,
            t.direction as transaction_direction,
            t.operation_datetime,
            t.posting_datetime,
            coalesce(t.bank_reference_id, '') as transaction_bank_reference_id,
            coalesce(t.bank_category, '') as transaction_bank_category,
            coalesce(t.description_raw, '') as transaction_description_raw,
            s.id as statement_id,
            lower(regexp_replace(s.pdf_path, '^.*/', '')) as source_file,
            sr.id as statement_row_id,
            sr.row_index,
            sr.page_number,
            sr.amount as row_amount,
            sr.currency as row_currency,
            sr.direction as row_direction,
            sr.operation_date,
            sr.posting_date,
            coalesce(sr.raw_text, '') as row_raw_text,
            coalesce(sr.raw_data->>'bank_reference_id', '') as row_bank_reference_id,
            coalesce(sr.raw_data->>'bank_category', '') as row_bank_category
          from public.transactions t
          join public.transaction_statement_links tsl on tsl.transaction_id = t.id
          join public.statement_rows sr on sr.id = tsl.statement_row_id
          join public.statements s on s.id = sr.statement_id
          where lower(regexp_replace(s.pdf_path, '^.*/', '')) ~ %s
        )
        select
          transaction_id,
          max(account_id) as account_id,
          max(dedup_key) as dedup_key,
          max(transaction_amount) as transaction_amount,
          max(transaction_currency) as transaction_currency,
          max(transaction_direction) as transaction_direction,
          max(operation_datetime) as operation_datetime,
          max(posting_datetime) as posting_datetime,
          max(transaction_bank_reference_id) as transaction_bank_reference_id,
          max(transaction_bank_category) as transaction_bank_category,
          max(transaction_description_raw) as transaction_description_raw,
          count(*)::int as supporting_rows,
          count(distinct statement_id)::int as distinct_statements,
          count(distinct source_file)::int as distinct_source_files,
          jsonb_agg(
            jsonb_build_object(
              'statement_row_id', statement_row_id,
              'statement_id', statement_id,
              'source_file', source_file,
              'row_index', row_index,
              'page_number', page_number,
              'row_direction', row_direction,
              'row_amount', row_amount,
              'row_currency', row_currency,
              'operation_date', operation_date,
              'posting_date', posting_date,
              'row_bank_reference_id', row_bank_reference_id,
              'row_bank_category', row_bank_category,
              'row_raw_text', row_raw_text
            )
            order by source_file, statement_id, row_index, statement_row_id
          ) as supporting_rows_detail
        from scoped_links
        group by transaction_id
        having count(*) > 1
        order by supporting_rows desc, transaction_id
    """
    return _run_many_with_params(conn, query, (scope_regex,))


def _normalize_supporting_row_details(
    row: Mapping[str, Any],
) -> list[dict[str, Any]]:
    account_id = str(row.get("account_id") or "")
    details: list[dict[str, Any]] = []
    for detail in row.get("supporting_rows_detail") or []:
        if not isinstance(detail, Mapping):
            continue
        normalized = {
            "statement_row_id": str(detail.get("statement_row_id") or ""),
            "statement_id": str(detail.get("statement_id") or ""),
            "source_file": _normalize_source_file_key(detail.get("source_file")),
            "row_index": int(detail.get("row_index") or 0),
            "page_number": int(detail.get("page_number") or 0),
            "row_direction": str(detail.get("row_direction") or ""),
            "row_amount": _to_decimal(detail.get("row_amount")),
            "row_currency": str(detail.get("row_currency") or ""),
            "operation_date": detail.get("operation_date"),
            "posting_date": detail.get("posting_date"),
            "row_bank_reference_id": str(detail.get("row_bank_reference_id") or ""),
            "row_bank_category": str(detail.get("row_bank_category") or ""),
            "row_raw_text": str(detail.get("row_raw_text") or ""),
        }
        payload = _statement_row_fingerprint_payload(account_id=account_id, row=normalized)
        normalized["row_fingerprint"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        details.append(normalized)
    details.sort(
        key=lambda item: (
            item["source_file"],
            item["statement_id"],
            item["row_index"],
            item["statement_row_id"],
        )
    )
    return details


def _collect_actionable_collapses(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    all_collapse_types: dict[str, dict[str, int]] = {}
    blocked_by_reference_rows = 0
    collapsed_row_surplus = 0
    collapses: list[dict[str, Any]] = []

    for row in rows:
        supporting_rows = int(row.get("supporting_rows") or 0)
        if supporting_rows <= 1:
            continue

        collapsed_rows = supporting_rows - 1
        collapsed_row_surplus += collapsed_rows
        details = _normalize_supporting_row_details(row)
        unique_row_fingerprints = len({item["row_fingerprint"] for item in details})
        distinct_statements = int(row.get("distinct_statements") or 0)
        collapse_type = _classify_collapse_type(
            distinct_statements=distinct_statements,
            unique_row_fingerprints=unique_row_fingerprints,
        )
        bucket = all_collapse_types.setdefault(
            collapse_type,
            {"transactions": 0, "collapsed_rows": 0},
        )
        bucket["transactions"] += 1
        bucket["collapsed_rows"] += collapsed_rows

        if collapse_type != "same_statement_duplicate_collapse":
            continue

        tx_ref = _normalize_text(row.get("transaction_bank_reference_id"))
        row_refs = [detail for detail in details if _normalize_text(detail.get("row_bank_reference_id"))]
        if tx_ref or row_refs:
            blocked_by_reference_rows += collapsed_rows
            continue

        details_by_occurrence = sorted(details, key=lambda item: item["statement_row_id"])
        operations: list[dict[str, Any]] = []
        for occurrence, detail in enumerate(details_by_occurrence, start=1):
            group_key = _stmtdup_group_key(row, detail)
            synthetic_ref = _synthetic_reference(
                statement_id=detail["statement_id"],
                group_key=group_key,
                occurrence=occurrence,
            )
            dedup_key = _transaction_fingerprint(
                account_id=str(row.get("account_id") or ""),
                currency=str(row.get("transaction_currency") or detail.get("row_currency") or "RUB"),
                direction=str(row.get("transaction_direction") or detail.get("row_direction") or "out"),
                amount=row.get("transaction_amount") or detail.get("row_amount"),
                operation_datetime=row.get("operation_datetime") or detail.get("operation_date"),
                posting_datetime=row.get("posting_datetime") or detail.get("posting_date"),
                bank_reference_id=synthetic_ref,
                description_raw=str(row.get("transaction_description_raw") or detail.get("row_raw_text") or ""),
                bank_category=str(row.get("transaction_bank_category") or detail.get("row_bank_category") or ""),
                raw_text=str(detail.get("row_raw_text") or ""),
            )
            detail["synthetic_bank_reference_id"] = synthetic_ref
            detail["synthetic_dedup_key"] = dedup_key
            if occurrence == 1:
                continue
            operations.append(
                {
                    "statement_row_id": detail["statement_row_id"],
                    "statement_id": detail["statement_id"],
                    "source_file": detail["source_file"],
                    "occurrence": occurrence,
                    "synthetic_bank_reference_id": synthetic_ref,
                    "dedup_key": dedup_key,
                }
            )

        if not operations:
            continue

        collapses.append(
            {
                "transaction_id": str(row.get("transaction_id") or ""),
                "account_id": str(row.get("account_id") or ""),
                "dedup_key": str(row.get("dedup_key") or ""),
                "collapse_type": collapse_type,
                "supporting_rows": supporting_rows,
                "collapsed_rows": collapsed_rows,
                "distinct_statements": distinct_statements,
                "distinct_source_files": int(row.get("distinct_source_files") or 0),
                "keep_statement_row_id": details_by_occurrence[0]["statement_row_id"],
                "supporting_rows_detail": details_by_occurrence,
                "operations": operations,
            }
        )

    diagnostics = {
        "collapsed_transaction_count": len(rows),
        "collapsed_row_surplus": collapsed_row_surplus,
        "collapse_type_breakdown": dict(sorted(all_collapse_types.items())),
        "blocked_by_reference_rows": blocked_by_reference_rows,
        "actionable_transaction_count": len(collapses),
        "planned_split_rows": sum(len(item["operations"]) for item in collapses),
    }
    return collapses, diagnostics


def _find_transaction_by_dedup_key(
    conn: psycopg.Connection[Any], *, account_id: str, dedup_key: str
) -> str | None:
    row = _run_one_with_params(
        conn,
        """
        select id
        from public.transactions
        where account_id is not distinct from %s
          and dedup_key = %s
        limit 1
        """,
        (account_id, dedup_key),
    )
    if not row:
        return None
    return str(row.get("id") or "")


def _clone_transaction(
    conn: psycopg.Connection[Any],
    *,
    source_transaction_id: str,
    dedup_key: str,
    synthetic_bank_reference_id: str,
) -> str | None:
    new_transaction_id = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into public.transactions (
              id,
              account_id,
              dedup_key,
              operation_datetime,
              posting_datetime,
              amount,
              currency,
              direction,
              description_raw,
              merchant_normalized,
              bank_reference_id,
              bank_category,
              meaning,
              meaning_confidence,
              category,
              tags,
              review_status
            )
            select
              %s,
              t.account_id,
              %s,
              t.operation_datetime,
              t.posting_datetime,
              t.amount,
              t.currency,
              t.direction,
              t.description_raw,
              t.merchant_normalized,
              %s,
              t.bank_category,
              t.meaning,
              t.meaning_confidence,
              t.category,
              t.tags,
              t.review_status
            from public.transactions t
            where t.id = %s
            """,
            (
                new_transaction_id,
                dedup_key,
                synthetic_bank_reference_id,
                source_transaction_id,
            ),
        )
        if cur.rowcount != 1:
            return None
    return new_transaction_id


def _apply_split_plan(
    conn: psycopg.Connection[Any], collapses: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    inserted_transactions = 0
    reused_transactions = 0
    moved_statement_links = 0
    skipped_rows = 0
    applied_operations: list[dict[str, Any]] = []

    with conn.transaction():
        for collapse in collapses:
            source_transaction_id = str(collapse.get("transaction_id") or "")
            account_id = str(collapse.get("account_id") or "")
            for operation in collapse.get("operations") or []:
                if not isinstance(operation, Mapping):
                    continue
                statement_row_id = str(operation.get("statement_row_id") or "")
                if not statement_row_id:
                    skipped_rows += 1
                    continue

                row_owner = _run_one_with_params(
                    conn,
                    """
                    select transaction_id
                    from public.transaction_statement_links
                    where statement_row_id = %s
                    """,
                    (statement_row_id,),
                )
                if not row_owner:
                    skipped_rows += 1
                    continue
                current_owner = str(row_owner.get("transaction_id") or "")
                if current_owner != source_transaction_id:
                    skipped_rows += 1
                    continue

                dedup_key = str(operation.get("dedup_key") or "")
                synthetic_ref = str(operation.get("synthetic_bank_reference_id") or "")
                if not dedup_key or not synthetic_ref:
                    skipped_rows += 1
                    continue

                target_transaction_id = _find_transaction_by_dedup_key(
                    conn,
                    account_id=account_id,
                    dedup_key=dedup_key,
                )
                created_new = False
                if target_transaction_id:
                    reused_transactions += 1
                else:
                    target_transaction_id = _clone_transaction(
                        conn,
                        source_transaction_id=source_transaction_id,
                        dedup_key=dedup_key,
                        synthetic_bank_reference_id=synthetic_ref,
                    )
                    if not target_transaction_id:
                        skipped_rows += 1
                        continue
                    created_new = True
                    inserted_transactions += 1

                if target_transaction_id == source_transaction_id:
                    skipped_rows += 1
                    continue

                with conn.cursor() as cur:
                    cur.execute(
                        """
                        update public.transaction_statement_links
                        set transaction_id = %s
                        where transaction_id = %s
                          and statement_row_id = %s
                        """,
                        (
                            target_transaction_id,
                            source_transaction_id,
                            statement_row_id,
                        ),
                    )
                    if cur.rowcount != 1:
                        if created_new:
                            cur.execute(
                                "delete from public.transactions where id = %s",
                                (target_transaction_id,),
                            )
                            inserted_transactions -= 1
                        skipped_rows += 1
                        continue

                moved_statement_links += 1
                applied_operations.append(
                    {
                        "source_transaction_id": source_transaction_id,
                        "target_transaction_id": target_transaction_id,
                        "statement_row_id": statement_row_id,
                        "source_file": str(operation.get("source_file") or ""),
                        "dedup_key": dedup_key,
                        "synthetic_bank_reference_id": synthetic_ref,
                    }
                )

    return {
        "inserted_transactions": inserted_transactions,
        "reused_transactions": reused_transactions,
        "moved_statement_links": moved_statement_links,
        "skipped_rows": skipped_rows,
        "applied_operations": applied_operations,
    }


def _rebuild_transfer_links() -> dict[str, Any]:
    from app.db import SessionLocal
    from app.services.transfers import rebuild_all_transfer_links_in_session

    with SessionLocal() as db:
        result = rebuild_all_transfer_links_in_session(db)
        db.commit()
    return {
        "links_created": result.links_created,
        "auto_links_created": result.auto_links_created,
        "suggested_links_created": result.suggested_links_created,
        "transactions_marked_internal": result.transactions_marked_internal,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Deterministic backfill for canonical same-statement duplicate collapses. "
            "Default mode is dry-run."
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
        help="Override DATABASE_URL for this run.",
    )
    parser.add_argument(
        "--search-path",
        default="",
        help="Optional search_path override. Defaults to DB_SEARCH_PATH.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes. Without this flag the script only reports the plan.",
    )
    parser.add_argument(
        "--rebuild-transfer-links",
        action="store_true",
        help="After apply, run full transfer-link rebuild using app transfer service.",
    )
    parser.add_argument(
        "--detail-limit",
        type=int,
        default=25,
        help="Max actionable collapse entries to include in preview output.",
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
        before_rows = _query_collapsed_rows(conn, scope_regex=args.scope_regex)
        collapses, before_diag = _collect_actionable_collapses(before_rows)

        result: dict[str, Any] = {
            "mode": "apply" if args.apply else "dry_run",
            "scope_regex": args.scope_regex,
            "database_search_path": validated_search_path or None,
            "before": before_diag,
            "actionable_preview": collapses[: max(0, args.detail_limit)],
        }

        if args.rebuild_transfer_links and not args.apply:
            result["warning"] = (
                "--rebuild-transfer-links was requested without --apply; "
                "transfer rebuild was skipped."
            )

        if args.apply:
            apply_result = _apply_split_plan(conn, collapses)
            result["apply"] = apply_result
            after_rows = _query_collapsed_rows(conn, scope_regex=args.scope_regex)
            _, after_diag = _collect_actionable_collapses(after_rows)
            result["after"] = after_diag

            if args.rebuild_transfer_links:
                result["transfer_rebuild"] = _rebuild_transfer_links()

    print(json.dumps(result, indent=2, sort_keys=True, default=_json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
