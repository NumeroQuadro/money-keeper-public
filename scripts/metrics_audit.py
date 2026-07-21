#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import psycopg
from psycopg.rows import dict_row

API_DIR = Path(__file__).resolve().parents[1] / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from app.domain.metrics_quality import (  # noqa: E402
    attach_legacy_parity,
    build_metrics_quality_payload,
    attach_quality_summary,
)
from app.domain.cashflow_lens import (  # noqa: E402
    HIGH_CONFIDENCE_TRANSFER_MIN_IN_AMOUNT,
    HIGH_CONFIDENCE_TRANSFER_MIN_OUT_AMOUNT,
    HIGH_CONFIDENCE_TRANSFER_PHRASE_LIKE_PATTERNS,
    INTERNAL_TRANSFER_MEANING,
    EXTERNAL_TRANSFER_MEANING,
    TRANSFER_BANK_CATEGORY,
    STRICT_TRANSFER_ACCOUNT_FLOW_PATTERNS,
    STRICT_TRANSFER_HINT_LIKE_PATTERNS,
    STRICT_TRANSFER_HINT_EXCEPTION_PERSONAL_TRANSFER_PATTERNS,
    STRICT_TRANSFER_HINT_EXCEPTION_SBER_NARRATIVE_PATTERN,
    STRICT_TRANSFER_HINT_EXCEPTION_FEE_PATTERNS,
)
from app.database_url import (  # noqa: E402
    validate_database_search_path as validate_database_search_path_shared,
    validate_database_url as validate_database_url_shared,
)

DEFAULT_REFERENCE_CSV = (
    Path(__file__).resolve().parents[1] / "data" / "reference" / "transactions.csv"
)
DEFAULT_REFERENCE_SCOPE_REGEX = r"all_[^/]+\.pdf$"
SOURCE_FILE_SUFFIX_RE = re.compile(r"(all_[^/]+\.pdf)$")
FINGERPRINT_WHITESPACE_RE = re.compile(r"\s+")


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _database_url_from_env() -> str:
    value = os.environ.get("DATABASE_URL", "")
    return value.strip().strip('"').strip("'")


def _validate_database_url(url: str) -> None:
    validate_database_url_shared(url)


def _database_search_path_from_env() -> str:
    return (os.environ.get("DB_SEARCH_PATH") or "").strip()


def _validate_database_search_path(value: str) -> str:
    return validate_database_search_path_shared(value)


def _run_query(conn: psycopg.Connection[Any], query: str) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(query)
        row = cur.fetchone()
    return dict(row) if row else {}


def _run_query_with_params(
    conn: psycopg.Connection[Any],
    query: str,
    params: tuple[Any, ...],
) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(query, params)
        row = cur.fetchone()
    return dict(row) if row else {}


def _run_many_with_params(
    conn: psycopg.Connection[Any],
    query: str,
    params: tuple[Any, ...],
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall() or []
    return [dict(row) for row in rows]


def _table_exists(conn: psycopg.Connection[Any], table_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "select to_regclass(%s) is not null as table_exists",
            (table_name,),
        )
        row = cur.fetchone() or {}
    return bool(row.get("table_exists"))


# Backward-compatible wrappers for unit tests that import script internals.
def _attach_legacy_parity(report: dict[str, Any]) -> None:
    attach_legacy_parity(report)


def _attach_quality_summary(report: dict[str, Any]) -> None:
    attach_quality_summary(report)


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


def _is_trueish(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return text in {"1", "true", "t", "yes", "y"}


def _normalize_source_file_key(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("\\", "/")
    if not normalized:
        return ""
    match = SOURCE_FILE_SUFFIX_RE.search(normalized)
    if match:
        return match.group(1)
    return normalized.rsplit("/", 1)[-1]


def _normalize_fingerprint_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    return FINGERPRINT_WHITESPACE_RE.sub(" ", text) if text else ""


def _timestamp_fingerprint_key(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    else:
        raw = str(value).strip()
        if not raw:
            return ""
        normalized = raw.replace(" ", "T")
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError:
            return raw
    return dt.replace(microsecond=0).isoformat()


def _timestamp_fingerprint_key_utc_naive(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    else:
        raw = str(value).strip()
        if not raw:
            return ""
        normalized = raw.replace(" ", "T")
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError:
            return raw
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.replace(microsecond=0).isoformat()


def _amount_cents(value: Any) -> int:
    amount = abs(_to_decimal(value)).quantize(Decimal("0.01"))
    return int(amount * Decimal("100"))


def _statement_row_fingerprint_payload(*, account_id: str, row: Mapping[str, Any]) -> str:
    components = [
        "row-v1",
        str(account_id or ""),
        _normalize_fingerprint_text(row.get("row_currency") or "RUB"),
        _normalize_fingerprint_text(row.get("row_direction") or "out"),
        str(_amount_cents(row.get("row_amount"))),
        _timestamp_fingerprint_key(row.get("operation_date")),
        _timestamp_fingerprint_key(row.get("posting_date")),
        _normalize_fingerprint_text(row.get("row_bank_reference_id")),
        _normalize_fingerprint_text(row.get("row_bank_category")),
        _normalize_fingerprint_text(row.get("row_raw_text")),
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


def _zeroed_bucket() -> dict[str, Any]:
    return {"tx_count": 0, "inflow": Decimal("0"), "outflow": Decimal("0"), "net": Decimal("0")}


def _apply_amount_bucket(bucket: dict[str, Any], amount: Decimal) -> None:
    bucket["tx_count"] += 1
    if amount > 0:
        bucket["inflow"] += amount
    elif amount < 0:
        bucket["outflow"] += -amount
    bucket["net"] = bucket["inflow"] - bucket["outflow"]


def _build_reference_metrics_from_rows(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    gross = _zeroed_bucket()
    tier_b = _zeroed_bucket()
    tier_c = _zeroed_bucket()
    all_transfers = _zeroed_bucket()
    high_transfers = _zeroed_bucket()
    medium_transfers = _zeroed_bucket()
    per_file: dict[str, dict[str, Any]] = {}
    per_file_tier_b: dict[str, dict[str, Any]] = {}
    per_file_tier_c: dict[str, dict[str, Any]] = {}
    min_date: str | None = None
    max_date: str | None = None

    for row in rows:
        amount = _to_decimal(row.get("amount_rub"))
        is_transfer = _is_trueish(row.get("is_transfer"))
        transfer_confidence = str(row.get("transfer_confidence") or "").strip().lower()
        source_file = _normalize_source_file_key(row.get("source_file"))
        operation_date = str(row.get("operation_date") or "").strip()

        if operation_date:
            if min_date is None or operation_date < min_date:
                min_date = operation_date
            if max_date is None or operation_date > max_date:
                max_date = operation_date

        _apply_amount_bucket(gross, amount)

        if is_transfer:
            _apply_amount_bucket(all_transfers, amount)
            if transfer_confidence == "high":
                _apply_amount_bucket(high_transfers, amount)
            elif transfer_confidence == "medium":
                _apply_amount_bucket(medium_transfers, amount)
        else:
            _apply_amount_bucket(tier_c, amount)

        if not (is_transfer and transfer_confidence == "high"):
            _apply_amount_bucket(tier_b, amount)
            if source_file:
                tier_b_bucket = per_file_tier_b.setdefault(source_file, _zeroed_bucket())
                _apply_amount_bucket(tier_b_bucket, amount)

        if not is_transfer and source_file:
            tier_c_bucket = per_file_tier_c.setdefault(source_file, _zeroed_bucket())
            _apply_amount_bucket(tier_c_bucket, amount)

        if source_file:
            bucket = per_file.setdefault(source_file, _zeroed_bucket())
            _apply_amount_bucket(bucket, amount)

    tier_c_income = tier_c["inflow"]
    tier_c_spend = tier_c["outflow"]
    tier_c_net = tier_c_income - tier_c_spend

    return {
        "date_range": {"start": min_date, "end": max_date},
        "gross": gross,
        "tier_b_excluding_high_confidence_transfers": tier_b,
        "tier_c_excluding_all_transfers": {
            "tx_count": tier_c["tx_count"],
            "income": tier_c_income,
            "spend": tier_c_spend,
            "net": tier_c_net,
        },
        "transfer_stats": {
            "all_transfers": all_transfers,
            "high_confidence": high_transfers,
            "medium_confidence": medium_transfers,
        },
        "per_file_gross": dict(sorted(per_file.items())),
        "per_file_tier_b_excluding_high_confidence_transfers": dict(
            sorted(per_file_tier_b.items())
        ),
        "per_file_tier_c_excluding_all_transfers": dict(sorted(per_file_tier_c.items())),
    }


def _load_reference_metrics(csv_path: Path) -> dict[str, Any]:
    rows = _load_reference_rows(csv_path)
    metrics = _build_reference_metrics_from_rows(rows)
    metrics["reference_csv_path"] = str(csv_path)
    return metrics


def _load_reference_rows(csv_path: Path) -> list[dict[str, Any]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _query_statement_row_metrics_for_scope(
    conn: psycopg.Connection[Any],
    scope_regex: str,
) -> dict[str, Any]:
    scoped_cte = """
        with scoped as (
          select
            sr.amount,
            sr.direction,
            lower(regexp_replace(s.pdf_path, '^.*/', '')) as source_file
          from public.statement_rows sr
          join public.statements s on s.id = sr.statement_id
          where lower(regexp_replace(s.pdf_path, '^.*/', '')) ~ %s
        )
    """
    totals_query = (
        scoped_cte
        + """
        select
          count(*)::int as tx_count,
          coalesce(sum(case when direction = 'in' then amount else 0 end), 0) as inflow,
          coalesce(sum(case when direction = 'out' then amount else 0 end), 0) as outflow
        from scoped
        """
    )
    per_file_query = (
        scoped_cte
        + """
        select
          source_file,
          count(*)::int as tx_count,
          coalesce(sum(case when direction = 'in' then amount else 0 end), 0) as inflow,
          coalesce(sum(case when direction = 'out' then amount else 0 end), 0) as outflow
        from scoped
        group by source_file
        order by source_file
        """
    )

    totals = _run_query_with_params(conn, totals_query, (scope_regex,))
    totals_inflow = _to_decimal(totals.get("inflow"))
    totals_outflow = _to_decimal(totals.get("outflow"))
    totals["inflow"] = totals_inflow
    totals["outflow"] = totals_outflow
    totals["net"] = totals_inflow - totals_outflow

    per_file_rows = _run_many_with_params(conn, per_file_query, (scope_regex,))
    per_file: dict[str, dict[str, Any]] = {}
    for row in per_file_rows:
        source_file = _normalize_source_file_key(row.get("source_file"))
        if not source_file:
            continue
        inflow = _to_decimal(row.get("inflow"))
        outflow = _to_decimal(row.get("outflow"))
        per_file[source_file] = {
            "tx_count": int(row.get("tx_count") or 0),
            "inflow": inflow,
            "outflow": outflow,
            "net": inflow - outflow,
        }

    totals["per_file_gross"] = per_file
    return totals


def _query_canonical_metrics_for_scope(
    conn: psycopg.Connection[Any],
    scope_regex: str,
) -> dict[str, Any]:
    high_confidence_transfer_predicate = _high_confidence_transfer_predicate_sql(
        table_alias=""
    )
    transfer_like_predicate = _transfer_like_predicate_sql(table_alias="")
    query = """
        with scoped_tx as (
          select distinct
            t.id,
            t.amount,
            t.direction,
            coalesce(t.meaning, '') as meaning,
            coalesce(t.bank_category, '') as bank_category,
            coalesce(t.description_raw, '') as description_raw,
            coalesce(t.bank_reference_id, '') as bank_reference_id
          from public.transactions t
          join public.transaction_statement_links tsl on tsl.transaction_id = t.id
          join public.statement_rows sr on sr.id = tsl.statement_row_id
          join public.statements s on s.id = sr.statement_id
          where lower(regexp_replace(s.pdf_path, '^.*/', '')) ~ %s
        )
        select
          count(*)::int as tx_count,
          coalesce(sum(case when direction = 'in' then amount else 0 end), 0) as inflow,
          coalesce(sum(case when direction = 'out' then amount else 0 end), 0) as outflow,
          count(*) filter (where meaning = 'internal_transfer')::int as internal_transfer_count,
          coalesce(sum(case when meaning = 'internal_transfer' and direction = 'in' then amount else 0 end), 0) as internal_inflow,
          coalesce(sum(case when meaning = 'internal_transfer' and direction = 'out' then amount else 0 end), 0) as internal_outflow,
          count(*) filter (where meaning <> 'internal_transfer')::int as prd_true_tx_count,
          coalesce(sum(case when meaning <> 'internal_transfer' and direction = 'in' then amount else 0 end), 0) as prd_true_income,
          coalesce(sum(case when meaning <> 'internal_transfer' and direction = 'out' then amount else 0 end), 0) as prd_true_spend,
          count(*) filter (where not ({high_confidence_transfer_predicate}))::int as high_conf_true_tx_count,
          coalesce(sum(case when not ({high_confidence_transfer_predicate}) and direction = 'in' then amount else 0 end), 0) as high_conf_true_income,
          coalesce(sum(case when not ({high_confidence_transfer_predicate}) and direction = 'out' then amount else 0 end), 0) as high_conf_true_spend,
          count(*) filter (where not ({transfer_like_predicate}))::int as strict_true_tx_count,
          coalesce(sum(case when not ({transfer_like_predicate}) and direction = 'in' then amount else 0 end), 0) as strict_true_income,
          coalesce(sum(case when not ({transfer_like_predicate}) and direction = 'out' then amount else 0 end), 0) as strict_true_spend
        from scoped_tx
    """.format(
        high_confidence_transfer_predicate=high_confidence_transfer_predicate,
        transfer_like_predicate=transfer_like_predicate,
    )
    row = _run_query_with_params(conn, query, (scope_regex,))
    inflow = _to_decimal(row.get("inflow"))
    outflow = _to_decimal(row.get("outflow"))
    internal_inflow = _to_decimal(row.get("internal_inflow"))
    internal_outflow = _to_decimal(row.get("internal_outflow"))
    prd_true_income = _to_decimal(row.get("prd_true_income"))
    prd_true_spend = _to_decimal(row.get("prd_true_spend"))
    high_conf_true_income = _to_decimal(row.get("high_conf_true_income"))
    high_conf_true_spend = _to_decimal(row.get("high_conf_true_spend"))
    strict_true_income = _to_decimal(row.get("strict_true_income"))
    strict_true_spend = _to_decimal(row.get("strict_true_spend"))

    return {
        "tx_count": int(row.get("tx_count") or 0),
        "inflow": inflow,
        "outflow": outflow,
        "net": inflow - outflow,
        "internal_transfer_count": int(row.get("internal_transfer_count") or 0),
        "internal_inflow": internal_inflow,
        "internal_outflow": internal_outflow,
        "prd_true_cashflow_excluding_internal_transfers": {
            "tx_count": int(row.get("prd_true_tx_count") or 0),
            "income": prd_true_income,
            "spend": prd_true_spend,
            "net": prd_true_income - prd_true_spend,
        },
        "high_confidence_cashflow_excluding_transfer_like": {
            "tx_count": int(row.get("high_conf_true_tx_count") or 0),
            "income": high_conf_true_income,
            "spend": high_conf_true_spend,
            "net": high_conf_true_income - high_conf_true_spend,
        },
        "strict_cashflow_excluding_transfer_like": {
            "tx_count": int(row.get("strict_true_tx_count") or 0),
            "income": strict_true_income,
            "spend": strict_true_spend,
            "net": strict_true_income - strict_true_spend,
        },
    }


def _high_confidence_transfer_predicate_sql(*, table_alias: str) -> str:
    alias = f"{table_alias}." if table_alias else ""
    high_confidence_patterns = [
        pattern.replace("%", "%%")
        for pattern in HIGH_CONFIDENCE_TRANSFER_PHRASE_LIKE_PATTERNS
    ]
    in_patterns = high_confidence_patterns[:4]
    out_patterns = high_confidence_patterns[4:7]
    return (
        f"coalesce({alias}meaning, '') = '{INTERNAL_TRANSFER_MEANING}' "
        f"or coalesce({alias}meaning, '') = '{EXTERNAL_TRANSFER_MEANING}' "
        f"or (lower(coalesce({alias}bank_category, '')) = '{TRANSFER_BANK_CATEGORY}' "
        f"and coalesce({alias}bank_reference_id, '') <> '' "
        f"and ((coalesce({alias}direction, '') = 'in' and coalesce({alias}amount, 0) >= {HIGH_CONFIDENCE_TRANSFER_MIN_IN_AMOUNT} "
        f"and (lower(coalesce({alias}description_raw, '')) like '{in_patterns[0]}' "
        f"or lower(coalesce({alias}description_raw, '')) like '{in_patterns[1]}' "
        f"or lower(coalesce({alias}description_raw, '')) like '{in_patterns[2]}' "
        f"or lower(coalesce({alias}description_raw, '')) like '{in_patterns[3]}')) "
        f"or (coalesce({alias}direction, '') <> 'in' and coalesce({alias}amount, 0) >= {HIGH_CONFIDENCE_TRANSFER_MIN_OUT_AMOUNT} "
        f"and (lower(coalesce({alias}description_raw, '')) like '{out_patterns[0]}' "
        f"or lower(coalesce({alias}description_raw, '')) like '{out_patterns[1]}' "
        f"or lower(coalesce({alias}description_raw, '')) like '{out_patterns[2]}'))))"
    )


def _transfer_like_predicate_sql(*, table_alias: str) -> str:
    alias = f"{table_alias}." if table_alias else ""
    description = f"lower(coalesce({alias}description_raw, ''))"

    strict_account_flow_patterns = [
        pattern.replace("%", "%%") for pattern in STRICT_TRANSFER_ACCOUNT_FLOW_PATTERNS
    ]
    strict_transfer_like_patterns = [
        pattern.replace("%", "%%") for pattern in STRICT_TRANSFER_HINT_LIKE_PATTERNS
    ]
    strict_personal_transfer_exception_patterns = [
        pattern.replace("%", "%%")
        for pattern in STRICT_TRANSFER_HINT_EXCEPTION_PERSONAL_TRANSFER_PATTERNS
    ]
    strict_sber_narrative_exception_pattern = (
        STRICT_TRANSFER_HINT_EXCEPTION_SBER_NARRATIVE_PATTERN.replace("%", "%%")
    )
    strict_fee_exception_patterns = [
        pattern.replace("%", "%%") for pattern in STRICT_TRANSFER_HINT_EXCEPTION_FEE_PATTERNS
    ]

    strict_hint_expression = " or ".join(
        f"{description} like '{pattern}'" for pattern in strict_transfer_like_patterns
    )
    strict_exception_expression = (
        f"(({description} like '{strict_personal_transfer_exception_patterns[0]}' "
        f"and {description} like '{strict_personal_transfer_exception_patterns[1]}') "
        f"or ({description} like '{strict_sber_narrative_exception_pattern}') "
        f"or ({description} like '{strict_fee_exception_patterns[0]}' "
        f"and {description} like '{strict_fee_exception_patterns[1]}'))"
    )

    return (
        f"(coalesce({alias}meaning, '') = '{INTERNAL_TRANSFER_MEANING}' "
        f"or coalesce({alias}meaning, '') = '{EXTERNAL_TRANSFER_MEANING}' "
        f"or {description} like '{strict_account_flow_patterns[0]}' "
        f"or {description} like '{strict_account_flow_patterns[1]}' "
        f"or (({strict_hint_expression}) and not {strict_exception_expression}))"
    )


def _query_canonical_lens_metrics_per_file_for_scope(
    conn: psycopg.Connection[Any],
    scope_regex: str,
) -> dict[str, dict[str, dict[str, Any]]]:
    high_confidence_transfer_predicate = _high_confidence_transfer_predicate_sql(
        table_alias=""
    )
    transfer_like_predicate = _transfer_like_predicate_sql(table_alias="")
    query = """
        with scoped_tx as (
          select distinct
            t.id,
            t.direction,
            t.amount,
            coalesce(t.meaning, '') as meaning,
            coalesce(t.bank_category, '') as bank_category,
            coalesce(t.description_raw, '') as description_raw,
            coalesce(t.bank_reference_id, '') as bank_reference_id,
            lower(regexp_replace(s.pdf_path, '^.*/', '')) as source_file
          from public.transactions t
          join public.transaction_statement_links tsl on tsl.transaction_id = t.id
          join public.statement_rows sr on sr.id = tsl.statement_row_id
          join public.statements s on s.id = sr.statement_id
          where lower(regexp_replace(s.pdf_path, '^.*/', '')) ~ %s
        )
        select
          source_file,
          count(*) filter (where meaning <> 'internal_transfer')::int as prd_true_tx_count,
          coalesce(sum(case when meaning <> 'internal_transfer' and direction = 'in' then amount else 0 end), 0) as prd_true_income,
          coalesce(sum(case when meaning <> 'internal_transfer' and direction = 'out' then amount else 0 end), 0) as prd_true_spend,
          count(*) filter (where not ({high_confidence_transfer_predicate}))::int as high_conf_true_tx_count,
          coalesce(sum(case when not ({high_confidence_transfer_predicate}) and direction = 'in' then amount else 0 end), 0) as high_conf_true_income,
          coalesce(sum(case when not ({high_confidence_transfer_predicate}) and direction = 'out' then amount else 0 end), 0) as high_conf_true_spend,
          count(*) filter (where not ({transfer_like_predicate}))::int as strict_true_tx_count,
          coalesce(sum(case when not ({transfer_like_predicate}) and direction = 'in' then amount else 0 end), 0) as strict_true_income,
          coalesce(sum(case when not ({transfer_like_predicate}) and direction = 'out' then amount else 0 end), 0) as strict_true_spend
        from scoped_tx
        group by source_file
        order by source_file
    """.format(
        high_confidence_transfer_predicate=high_confidence_transfer_predicate,
        transfer_like_predicate=transfer_like_predicate,
    )
    rows = _run_many_with_params(conn, query, (scope_regex,))
    prd_per_file: dict[str, dict[str, Any]] = {}
    high_conf_per_file: dict[str, dict[str, Any]] = {}
    strict_per_file: dict[str, dict[str, Any]] = {}
    for row in rows:
        source_file = _normalize_source_file_key(row.get("source_file"))
        if not source_file:
            continue
        prd_income = _to_decimal(row.get("prd_true_income"))
        prd_spend = _to_decimal(row.get("prd_true_spend"))
        prd_per_file[source_file] = {
            "tx_count": int(row.get("prd_true_tx_count") or 0),
            "inflow": prd_income,
            "outflow": prd_spend,
            "net": prd_income - prd_spend,
        }
        high_conf_income = _to_decimal(row.get("high_conf_true_income"))
        high_conf_spend = _to_decimal(row.get("high_conf_true_spend"))
        high_conf_per_file[source_file] = {
            "tx_count": int(row.get("high_conf_true_tx_count") or 0),
            "inflow": high_conf_income,
            "outflow": high_conf_spend,
            "net": high_conf_income - high_conf_spend,
        }
        strict_income = _to_decimal(row.get("strict_true_income"))
        strict_spend = _to_decimal(row.get("strict_true_spend"))
        strict_per_file[source_file] = {
            "tx_count": int(row.get("strict_true_tx_count") or 0),
            "inflow": strict_income,
            "outflow": strict_spend,
            "net": strict_income - strict_spend,
        }
    return {
        "prd_true_cashflow_excluding_internal_transfers": dict(sorted(prd_per_file.items())),
        "high_confidence_cashflow_excluding_transfer_like": dict(
            sorted(high_conf_per_file.items())
        ),
        "strict_cashflow_excluding_transfer_like": dict(sorted(strict_per_file.items())),
    }


def _normalize_page_number(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _high_confidence_classification_key(
    *,
    source_file: Any,
    operation_datetime: Any,
    direction: Any,
    amount: Any,
    page_number: Any,
) -> tuple[str, str, str, int, int]:
    return (
        _normalize_source_file_key(source_file),
        _timestamp_fingerprint_key_utc_naive(operation_datetime),
        str(direction or "").strip().lower(),
        _amount_cents(amount),
        _normalize_page_number(page_number),
    )


def _is_high_confidence_transfer_like_row(row: Mapping[str, Any]) -> bool:
    meaning = str(row.get("meaning") or "").strip().lower()
    if meaning in {INTERNAL_TRANSFER_MEANING, EXTERNAL_TRANSFER_MEANING}:
        return True

    bank_category = str(row.get("bank_category") or "").strip().lower()
    bank_reference_id = str(row.get("bank_reference_id") or "").strip()
    if bank_category != TRANSFER_BANK_CATEGORY or not bank_reference_id:
        return False

    direction = str(row.get("direction") or "").strip().lower()
    amount = abs(_to_decimal(row.get("amount")))
    if direction == "in":
        if amount < HIGH_CONFIDENCE_TRANSFER_MIN_IN_AMOUNT:
            return False
    else:
        if amount < HIGH_CONFIDENCE_TRANSFER_MIN_OUT_AMOUNT:
            return False

    description = str(row.get("description_raw") or "").strip().lower()
    patterns = (
        HIGH_CONFIDENCE_TRANSFER_PHRASE_LIKE_PATTERNS[:4]
        if direction == "in"
        else HIGH_CONFIDENCE_TRANSFER_PHRASE_LIKE_PATTERNS[4:7]
    )
    for pattern in patterns:
        token = pattern.replace("%", "").strip().lower()
        if token and token in description:
            return True
    return False


def _query_canonical_high_confidence_classification_rows_for_scope(
    conn: psycopg.Connection[Any],
    scope_regex: str,
) -> list[dict[str, Any]]:
    query = """
        with scoped_rows as (
          select distinct
            sr.id as statement_row_id,
            lower(regexp_replace(s.pdf_path, '^.*/', '')) as source_file,
            sr.operation_date as operation_datetime,
            sr.page_number,
            coalesce(sr.direction, t.direction, '') as direction,
            coalesce(sr.amount, t.amount, 0) as amount,
            coalesce(t.meaning, '') as meaning,
            coalesce(t.bank_category, '') as bank_category,
            coalesce(t.bank_reference_id, '') as bank_reference_id,
            coalesce(t.description_raw, '') as description_raw
          from public.statement_rows sr
          join public.statements s on s.id = sr.statement_id
          join public.transaction_statement_links tsl on tsl.statement_row_id = sr.id
          join public.transactions t on t.id = tsl.transaction_id
          where lower(regexp_replace(s.pdf_path, '^.*/', '')) ~ %s
        )
        select
          source_file,
          operation_datetime,
          page_number,
          direction,
          amount,
          meaning,
          bank_category,
          bank_reference_id,
          description_raw
        from scoped_rows
    """
    return _run_many_with_params(conn, query, (scope_regex,))


def _high_confidence_phrase_role_tags(
    *,
    direction: str,
    canonical_examples: Sequence[str],
    reference_examples: Sequence[str],
) -> tuple[str, ...]:
    evidence_text = " ".join([*canonical_examples, *reference_examples]).strip().lower()
    tags: list[str] = []

    if "перевод от " in evidence_text or "отправитель:" in evidence_text:
        tags.append("sender_role")
    if "перевод для " in evidence_text or "получатель:" in evidence_text:
        tags.append("recipient_role")
    if "входящий перевод" in evidence_text:
        tags.append("incoming_transfer")
    if "исходящий перевод" in evidence_text:
        tags.append("outgoing_transfer")
    if re.search(r"\bзачисление\b", evidence_text):
        tags.append("generic_credit")
    if re.search(r"\bсписание\b", evidence_text):
        tags.append("generic_debit")
    if "сбп" in evidence_text and not any(
        tag in tags
        for tag in ("sender_role", "recipient_role", "incoming_transfer", "outgoing_transfer")
    ):
        tags.append("sbp_protocol_only")

    if not tags:
        if direction == "in":
            tags.append("direction_in_other")
        elif direction == "out":
            tags.append("direction_out_other")
        else:
            tags.append("other")

    # Preserve insertion order while removing duplicates.
    return tuple(dict.fromkeys(tags))


def _build_high_confidence_classification_mismatch(
    *,
    reference_rows: Iterable[Mapping[str, Any]],
    canonical_rows: Iterable[Mapping[str, Any]],
    scope_regex: str,
    sample_limit: int = 25,
) -> dict[str, Any]:
    scope_re = re.compile(scope_regex)
    buckets: dict[tuple[str, str, str, int, int], dict[str, Any]] = {}
    reference_row_count = 0
    canonical_row_count = 0
    reference_high_conf_row_count = 0
    canonical_high_conf_row_count = 0

    def ensure_bucket(
        key: tuple[str, str, str, int, int],
    ) -> dict[str, Any]:
        source_file, operation_datetime, direction, amount_cents, page_number = key
        return buckets.setdefault(
            key,
            {
                "source_file": source_file,
                "operation_datetime": operation_datetime,
                "direction": direction,
                "amount_cents": amount_cents,
                "page_number": page_number,
                "reference_rows": 0,
                "reference_high_conf_rows": 0,
                "canonical_rows": 0,
                "canonical_high_conf_rows": 0,
                "reference_examples": [],
                "canonical_examples": [],
                "canonical_meaning_counts": {},
            },
        )

    for row in reference_rows:
        key = _high_confidence_classification_key(
            source_file=row.get("source_file"),
            operation_datetime=row.get("operation_datetime") or row.get("operation_date"),
            direction=row.get("direction"),
            amount=row.get("amount_rub"),
            page_number=row.get("page"),
        )
        if not key[0] or not scope_re.search(key[0]):
            continue
        reference_row_count += 1
        bucket = ensure_bucket(key)
        bucket["reference_rows"] += 1

        is_high_conf = _is_trueish(row.get("is_transfer")) and (
            str(row.get("transfer_confidence") or "").strip().lower() == "high"
        )
        if is_high_conf:
            bucket["reference_high_conf_rows"] += 1
            reference_high_conf_row_count += 1

        example = str(row.get("description") or "").strip()
        if example and example not in bucket["reference_examples"]:
            bucket["reference_examples"].append(example)
            bucket["reference_examples"] = bucket["reference_examples"][:3]

    for row in canonical_rows:
        key = _high_confidence_classification_key(
            source_file=row.get("source_file"),
            operation_datetime=row.get("operation_datetime"),
            direction=row.get("direction"),
            amount=row.get("amount"),
            page_number=row.get("page_number"),
        )
        if not key[0] or not scope_re.search(key[0]):
            continue
        canonical_row_count += 1
        bucket = ensure_bucket(key)
        bucket["canonical_rows"] += 1

        canonical_meaning = str(row.get("meaning") or "").strip().lower() or "unknown"
        meaning_counts = bucket.get("canonical_meaning_counts")
        if isinstance(meaning_counts, dict):
            meaning_counts[canonical_meaning] = int(meaning_counts.get(canonical_meaning, 0)) + 1

        if _is_high_confidence_transfer_like_row(row):
            bucket["canonical_high_conf_rows"] += 1
            canonical_high_conf_row_count += 1

        example = str(row.get("description_raw") or "").strip()
        if example and example not in bucket["canonical_examples"]:
            bucket["canonical_examples"].append(example)
            bucket["canonical_examples"] = bucket["canonical_examples"][:3]

    mismatches: list[dict[str, Any]] = []
    key_count_mismatches = 0
    high_conf_count_mismatches = 0
    by_source_file: dict[str, dict[str, Any]] = {}
    phrase_role_breakdown: dict[str, dict[str, Any]] = {}
    meaning_breakdown: dict[str, dict[str, Any]] = {}
    for bucket in buckets.values():
        row_delta = int(bucket["canonical_rows"]) - int(bucket["reference_rows"])
        high_delta = int(bucket["canonical_high_conf_rows"]) - int(
            bucket["reference_high_conf_rows"]
        )
        if row_delta != 0:
            key_count_mismatches += 1
        if high_delta != 0:
            high_conf_count_mismatches += 1
        if row_delta == 0 and high_delta == 0:
            continue

        source_file = str(bucket["source_file"] or "unknown")
        amount = Decimal(bucket["amount_cents"]) / Decimal("100")
        phrase_role_tags = _high_confidence_phrase_role_tags(
            direction=str(bucket["direction"] or ""),
            canonical_examples=tuple(bucket["canonical_examples"]),
            reference_examples=tuple(bucket["reference_examples"]),
        )
        canonical_meaning_counts = bucket.get("canonical_meaning_counts")
        primary_meaning = "unknown"
        if isinstance(canonical_meaning_counts, dict) and canonical_meaning_counts:
            primary_meaning = sorted(
                canonical_meaning_counts.items(),
                key=lambda item: (-int(item[1]), str(item[0])),
            )[0][0]

        file_bucket = by_source_file.setdefault(
            source_file,
            {
                "source_file": source_file,
                "mismatch_keys": 0,
                "row_count_delta": 0,
                "high_conf_row_delta": 0,
            },
        )
        file_bucket["mismatch_keys"] += 1
        file_bucket["row_count_delta"] += row_delta
        file_bucket["high_conf_row_delta"] += high_delta

        meaning_bucket = meaning_breakdown.setdefault(
            primary_meaning,
            {
                "meaning": primary_meaning,
                "mismatch_keys": 0,
                "row_count_delta": 0,
                "high_conf_row_delta": 0,
                "high_conf_inflow_amount_delta": Decimal("0"),
                "high_conf_outflow_amount_delta": Decimal("0"),
                "source_files": set(),
                "sample_examples": [],
            },
        )
        meaning_bucket["mismatch_keys"] += 1
        meaning_bucket["row_count_delta"] += row_delta
        meaning_bucket["high_conf_row_delta"] += high_delta
        if bucket["direction"] == "in":
            meaning_bucket["high_conf_inflow_amount_delta"] += amount * high_delta
        elif bucket["direction"] == "out":
            meaning_bucket["high_conf_outflow_amount_delta"] += amount * high_delta
        meaning_bucket["source_files"].add(source_file)

        sample_example = ""
        if bucket["canonical_examples"]:
            sample_example = str(bucket["canonical_examples"][0])
        elif bucket["reference_examples"]:
            sample_example = str(bucket["reference_examples"][0])
        if (
            sample_example
            and sample_example not in meaning_bucket["sample_examples"]
            and len(meaning_bucket["sample_examples"]) < 3
        ):
            meaning_bucket["sample_examples"].append(sample_example)

        for phrase_role in phrase_role_tags:
            role_bucket = phrase_role_breakdown.setdefault(
                phrase_role,
                {
                    "phrase_role": phrase_role,
                    "mismatch_keys": 0,
                    "row_count_delta": 0,
                    "high_conf_row_delta": 0,
                    "high_conf_inflow_amount_delta": Decimal("0"),
                    "high_conf_outflow_amount_delta": Decimal("0"),
                    "source_files": set(),
                    "sample_examples": [],
                },
            )
            role_bucket["mismatch_keys"] += 1
            role_bucket["row_count_delta"] += row_delta
            role_bucket["high_conf_row_delta"] += high_delta
            if bucket["direction"] == "in":
                role_bucket["high_conf_inflow_amount_delta"] += amount * high_delta
            elif bucket["direction"] == "out":
                role_bucket["high_conf_outflow_amount_delta"] += amount * high_delta
            role_bucket["source_files"].add(source_file)

            if (
                sample_example
                and sample_example not in role_bucket["sample_examples"]
                and len(role_bucket["sample_examples"]) < 3
            ):
                role_bucket["sample_examples"].append(sample_example)

        mismatches.append(
            {
                "source_file": bucket["source_file"],
                "operation_datetime": bucket["operation_datetime"],
                "direction": bucket["direction"],
                "amount": amount,
                "page_number": bucket["page_number"],
                "reference_rows": bucket["reference_rows"],
                "reference_high_conf_rows": bucket["reference_high_conf_rows"],
                "canonical_rows": bucket["canonical_rows"],
                "canonical_high_conf_rows": bucket["canonical_high_conf_rows"],
                "row_count_delta": row_delta,
                "high_conf_row_delta": high_delta,
                "canonical_primary_meaning": primary_meaning,
                "phrase_role_tags": list(phrase_role_tags),
                "reference_examples": bucket["reference_examples"],
                "canonical_examples": bucket["canonical_examples"],
            }
        )

    ranked_mismatches = sorted(
        mismatches,
        key=lambda item: (
            -abs(int(item["high_conf_row_delta"])),
            -abs(int(item["row_count_delta"])),
            str(item["source_file"]),
            str(item["operation_datetime"]),
            str(item["direction"]),
            _to_decimal(item["amount"]),
            int(item["page_number"]),
        ),
    )
    ranked_files = sorted(
        by_source_file.values(),
        key=lambda item: (
            -abs(int(item["high_conf_row_delta"])),
            -abs(int(item["row_count_delta"])),
            str(item["source_file"]),
        ),
    )
    ranked_phrase_roles = sorted(
        phrase_role_breakdown.values(),
        key=lambda item: (
            -abs(int(item["high_conf_row_delta"])),
            -abs(int(item["row_count_delta"])),
            str(item["phrase_role"]),
        ),
    )
    ranked_meanings = sorted(
        meaning_breakdown.values(),
        key=lambda item: (
            -abs(int(item["high_conf_row_delta"])),
            -abs(int(item["row_count_delta"])),
            str(item["meaning"]),
        ),
    )
    phrase_role_output: list[dict[str, Any]] = []
    for item in ranked_phrase_roles:
        phrase_role_output.append(
            {
                "phrase_role": item["phrase_role"],
                "mismatch_keys": int(item["mismatch_keys"]),
                "row_count_delta": int(item["row_count_delta"]),
                "high_conf_row_delta": int(item["high_conf_row_delta"]),
                "high_conf_inflow_amount_delta": item["high_conf_inflow_amount_delta"],
                "high_conf_outflow_amount_delta": item["high_conf_outflow_amount_delta"],
                "source_file_count": len(item["source_files"]),
                "sample_examples": list(item["sample_examples"]),
            }
        )
    meaning_output: list[dict[str, Any]] = []
    for item in ranked_meanings:
        meaning_output.append(
            {
                "meaning": item["meaning"],
                "mismatch_keys": int(item["mismatch_keys"]),
                "row_count_delta": int(item["row_count_delta"]),
                "high_conf_row_delta": int(item["high_conf_row_delta"]),
                "high_conf_inflow_amount_delta": item["high_conf_inflow_amount_delta"],
                "high_conf_outflow_amount_delta": item["high_conf_outflow_amount_delta"],
                "source_file_count": len(item["source_files"]),
                "sample_examples": list(item["sample_examples"]),
            }
        )
    return {
        "status": "ok" if not ranked_mismatches else "warning",
        "scope_regex": scope_regex,
        "key_count": len(buckets),
        "reference_row_count": reference_row_count,
        "canonical_row_count": canonical_row_count,
        "reference_high_conf_row_count": reference_high_conf_row_count,
        "canonical_high_conf_row_count": canonical_high_conf_row_count,
        "row_count_delta": canonical_row_count - reference_row_count,
        "high_conf_row_delta": canonical_high_conf_row_count
        - reference_high_conf_row_count,
        "key_count_mismatches": key_count_mismatches,
        "high_conf_count_mismatches": high_conf_count_mismatches,
        "phrase_role_breakdown": phrase_role_output,
        "meaning_breakdown": meaning_output,
        "by_source_file": ranked_files,
        "sample_mismatches": ranked_mismatches[: max(0, sample_limit)],
    }


def _query_transfer_link_counts_for_scope(
    conn: psycopg.Connection[Any],
    scope_regex: str,
) -> dict[str, int]:
    query = """
        with scoped_tx as (
          select distinct t.id
          from public.transactions t
          join public.transaction_statement_links tsl on tsl.transaction_id = t.id
          join public.statement_rows sr on sr.id = tsl.statement_row_id
          join public.statements s on s.id = sr.statement_id
          where lower(regexp_replace(s.pdf_path, '^.*/', '')) ~ %s
        )
        select tl.status, count(*)::int as link_count
        from public.transfer_links tl
        join scoped_tx tx_out on tx_out.id = tl.transaction_out_id
        join scoped_tx tx_in on tx_in.id = tl.transaction_in_id
        group by tl.status
    """
    rows = _run_many_with_params(conn, query, (scope_regex,))
    return {str(row.get("status") or ""): int(row.get("link_count") or 0) for row in rows}


def _summarize_canonical_collapse_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    detail_limit: int,
) -> dict[str, Any]:
    max_examples = max(0, detail_limit)
    collapse_type_breakdown: dict[str, dict[str, int]] = {}
    collapse_by_file: dict[str, dict[str, Any]] = {}
    collapsed_transaction_count = 0
    collapsed_row_surplus = 0
    expected_overlap_collapsed_rows = 0
    potential_unintended_collapsed_rows = 0
    sample_collapses: list[dict[str, Any]] = []

    for row in rows:
        supporting_rows = int(row.get("supporting_rows") or 0)
        if supporting_rows <= 1:
            continue
        collapsed_transaction_count += 1
        collapsed_rows = supporting_rows - 1
        collapsed_row_surplus += collapsed_rows

        account_id = str(row.get("account_id") or "")
        raw_details = row.get("supporting_rows_detail") or []
        normalized_details: list[dict[str, Any]] = []
        for detail in raw_details if isinstance(raw_details, list) else []:
            if not isinstance(detail, Mapping):
                continue
            source_file = _normalize_source_file_key(detail.get("source_file"))
            detail_row = {
                "statement_row_id": str(detail.get("statement_row_id") or ""),
                "statement_id": str(detail.get("statement_id") or ""),
                "source_file": source_file,
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
            payload = _statement_row_fingerprint_payload(account_id=account_id, row=detail_row)
            detail_row["row_fingerprint_payload"] = payload
            detail_row["row_fingerprint"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            normalized_details.append(detail_row)

        normalized_details.sort(
            key=lambda item: (
                item["source_file"],
                item["statement_id"],
                item["row_index"],
                item["statement_row_id"],
            )
        )
        unique_row_fingerprints = len({item["row_fingerprint"] for item in normalized_details})
        distinct_statements = int(row.get("distinct_statements") or 0)
        collapse_type = _classify_collapse_type(
            distinct_statements=distinct_statements,
            unique_row_fingerprints=unique_row_fingerprints,
        )
        collapse_expected = collapse_type == "expected_overlap_dedupe"
        if collapse_expected:
            expected_overlap_collapsed_rows += collapsed_rows
        else:
            potential_unintended_collapsed_rows += collapsed_rows

        type_bucket = collapse_type_breakdown.setdefault(
            collapse_type,
            {"transactions": 0, "collapsed_rows": 0},
        )
        type_bucket["transactions"] += 1
        type_bucket["collapsed_rows"] += collapsed_rows

        transaction_id = str(row.get("transaction_id") or "")
        for extra_detail in normalized_details[1:]:
            source_file = extra_detail.get("source_file") or "unknown"
            file_bucket = collapse_by_file.setdefault(
                source_file,
                {"collapsed_rows": 0, "transaction_ids": set()},
            )
            file_bucket["collapsed_rows"] += 1
            file_bucket["transaction_ids"].add(transaction_id)

        if len(sample_collapses) < max_examples:
            sample_collapses.append(
                {
                    "transaction_id": transaction_id,
                    "dedup_key": str(row.get("dedup_key") or ""),
                    "account_id": account_id,
                    "transaction_amount": _to_decimal(row.get("transaction_amount")),
                    "transaction_currency": str(row.get("transaction_currency") or ""),
                    "transaction_direction": str(row.get("transaction_direction") or ""),
                    "operation_datetime": row.get("operation_datetime"),
                    "posting_datetime": row.get("posting_datetime"),
                    "transaction_bank_reference_id": str(
                        row.get("transaction_bank_reference_id") or ""
                    ),
                    "transaction_bank_category": str(row.get("transaction_bank_category") or ""),
                    "transaction_description_raw": str(
                        row.get("transaction_description_raw") or ""
                    ),
                    "supporting_rows": supporting_rows,
                    "collapsed_rows": collapsed_rows,
                    "distinct_statements": distinct_statements,
                    "distinct_source_files": int(row.get("distinct_source_files") or 0),
                    "unique_row_fingerprints": unique_row_fingerprints,
                    "collapse_type": collapse_type,
                    "collapse_expected": collapse_expected,
                    "supporting_rows_detail": normalized_details,
                }
            )

    by_file_output = {
        source_file: {
            "collapsed_rows": int(data["collapsed_rows"]),
            "transactions": len(data["transaction_ids"]),
        }
        for source_file, data in sorted(collapse_by_file.items())
    }
    return {
        "status": "warning" if potential_unintended_collapsed_rows > 0 else "ok",
        "collapsed_transaction_count": collapsed_transaction_count,
        "collapsed_row_surplus": collapsed_row_surplus,
        "expected_overlap_collapsed_rows": expected_overlap_collapsed_rows,
        "potential_unintended_collapsed_rows": potential_unintended_collapsed_rows,
        "collapse_type_breakdown": dict(sorted(collapse_type_breakdown.items())),
        "collapsed_row_surplus_by_source_file": by_file_output,
        "sample_collapses": sample_collapses,
    }


def _query_canonical_row_collapse_audit_for_scope(
    conn: psycopg.Connection[Any],
    scope_regex: str,
    *,
    detail_limit: int,
) -> dict[str, Any]:
    query = """
        with scoped_links as (
          select
            t.id as transaction_id,
            t.dedup_key,
            t.account_id,
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
          max(dedup_key) as dedup_key,
          max(account_id) as account_id,
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
    rows = _run_many_with_params(conn, query, (scope_regex,))
    audit = _summarize_canonical_collapse_rows(rows, detail_limit=detail_limit)
    audit["scope_regex"] = scope_regex
    return audit


def _build_analytics_source_recommendation(
    *,
    statement_rows: Mapping[str, Any],
    canonical: Mapping[str, Any],
    collapse_audit: Mapping[str, Any],
) -> dict[str, Any]:
    statement_row_count = int(statement_rows.get("tx_count") or 0)
    canonical_count = int(canonical.get("tx_count") or 0)
    collapsed_row_surplus = int(collapse_audit.get("collapsed_row_surplus") or 0)
    unexpected_surplus = int(collapse_audit.get("potential_unintended_collapsed_rows") or 0)
    recommendation_lines = [
        (
            "Use canonical transactions for transfer-aware analytics (Tier B/C lenses), "
            "because meaning/transfer-link semantics live only in canonical rows."
        ),
    ]
    if collapsed_row_surplus > 0:
        recommendation_lines.append(
            "Use statement_rows for Tier A gross parity charts/tables until canonical row collapse is zero."
        )
        recommendation_lines.append(
            "Scoped count delta: "
            f"statement_rows={statement_row_count} vs canonical={canonical_count} "
            f"(surplus={collapsed_row_surplus})."
        )
    else:
        recommendation_lines.append(
            "Scoped counts are parity-safe, so canonical rows are acceptable for gross analytics."
        )
    if unexpected_surplus > 0:
        recommendation_lines.append(
            f"{unexpected_surplus} collapsed rows appear unintended; prioritize parser/dedup remediation."
        )
    else:
        recommendation_lines.append("Collapsed rows are fully explained by expected overlap dedupe.")
    return {
        "tier_a_gross_source": "statement_rows" if collapsed_row_surplus > 0 else "canonical_transactions",
        "transfer_aware_source": "canonical_transactions",
        "status": "warning" if collapsed_row_surplus > 0 else "ok",
        "reasoning": recommendation_lines,
    }


def _bucket_delta(actual: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    actual_inflow = _to_decimal(actual.get("inflow"))
    actual_outflow = _to_decimal(actual.get("outflow"))
    actual_net = _to_decimal(actual.get("net"))
    expected_inflow = _to_decimal(expected.get("inflow"))
    expected_outflow = _to_decimal(expected.get("outflow"))
    expected_net = _to_decimal(expected.get("net"))
    return {
        "tx_count": int(actual.get("tx_count") or 0) - int(expected.get("tx_count") or 0),
        "inflow": actual_inflow - expected_inflow,
        "outflow": actual_outflow - expected_outflow,
        "net": actual_net - expected_net,
    }


def _find_per_file_mismatches(
    actual: dict[str, dict[str, Any]],
    expected: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    all_keys = sorted(set(actual.keys()) | set(expected.keys()))
    for source_file in all_keys:
        actual_bucket = actual.get(source_file, _zeroed_bucket())
        expected_bucket = expected.get(source_file, _zeroed_bucket())
        delta = _bucket_delta(actual_bucket, expected_bucket)
        if (
            delta["tx_count"] != 0
            or delta["inflow"] != 0
            or delta["outflow"] != 0
            or delta["net"] != 0
        ):
            mismatches.append(
                {
                    "source_file": source_file,
                    "actual": actual_bucket,
                    "expected": expected_bucket,
                    "delta": delta,
                }
            )
    return mismatches


def _build_reference_reconciliation(
    conn: psycopg.Connection[Any],
    *,
    reference_csv_path: Path,
    scope_regex: str,
    collapse_audit_limit: int = 25,
) -> dict[str, Any]:
    reference_rows = _load_reference_rows(reference_csv_path)
    reference = _build_reference_metrics_from_rows(reference_rows)
    reference["reference_csv_path"] = str(reference_csv_path)
    statement_rows = _query_statement_row_metrics_for_scope(conn, scope_regex)
    canonical = _query_canonical_metrics_for_scope(conn, scope_regex)
    canonical_per_file = _query_canonical_lens_metrics_per_file_for_scope(conn, scope_regex)
    high_conf_classification_rows = (
        _query_canonical_high_confidence_classification_rows_for_scope(conn, scope_regex)
    )
    transfer_links = _query_transfer_link_counts_for_scope(conn, scope_regex)
    collapse_audit = _query_canonical_row_collapse_audit_for_scope(
        conn,
        scope_regex,
        detail_limit=collapse_audit_limit,
    )
    analytics_source_recommendation = _build_analytics_source_recommendation(
        statement_rows=statement_rows,
        canonical=canonical,
        collapse_audit=collapse_audit,
    )

    smoke_delta = _bucket_delta(statement_rows, reference["gross"])
    per_file_mismatches = _find_per_file_mismatches(
        statement_rows["per_file_gross"],
        reference["per_file_gross"],
    )

    prd_lens = canonical["prd_true_cashflow_excluding_internal_transfers"]
    high_conf_lens = canonical["high_confidence_cashflow_excluding_transfer_like"]
    strict_lens = canonical["strict_cashflow_excluding_transfer_like"]
    reference_tier_b = reference["tier_b_excluding_high_confidence_transfers"]
    reference_tier_c = reference["tier_c_excluding_all_transfers"]
    prd_lens_as_bucket = {
        "tx_count": prd_lens["tx_count"],
        "inflow": prd_lens["income"],
        "outflow": prd_lens["spend"],
        "net": prd_lens["net"],
    }
    high_conf_lens_as_bucket = {
        "tx_count": high_conf_lens["tx_count"],
        "inflow": high_conf_lens["income"],
        "outflow": high_conf_lens["spend"],
        "net": high_conf_lens["net"],
    }
    strict_lens_as_bucket = {
        "tx_count": strict_lens["tx_count"],
        "inflow": strict_lens["income"],
        "outflow": strict_lens["spend"],
        "net": strict_lens["net"],
    }
    tier_b_gap = _bucket_delta(prd_lens_as_bucket, reference_tier_b)
    high_conf_tier_b_gap = _bucket_delta(high_conf_lens_as_bucket, reference_tier_b)
    tier_c_gap = _bucket_delta(prd_lens_as_bucket, reference_tier_c)
    strict_tier_c_gap = _bucket_delta(strict_lens_as_bucket, reference_tier_c)
    prd_tier_b_gap_by_file = _find_per_file_mismatches(
        canonical_per_file["prd_true_cashflow_excluding_internal_transfers"],
        reference["per_file_tier_b_excluding_high_confidence_transfers"],
    )
    high_conf_tier_b_gap_by_file = _find_per_file_mismatches(
        canonical_per_file["high_confidence_cashflow_excluding_transfer_like"],
        reference["per_file_tier_b_excluding_high_confidence_transfers"],
    )
    strict_tier_c_gap_by_file = _find_per_file_mismatches(
        canonical_per_file["strict_cashflow_excluding_transfer_like"],
        reference["per_file_tier_c_excluding_all_transfers"],
    )
    high_conf_classification_mismatch = _build_high_confidence_classification_mismatch(
        reference_rows=reference_rows,
        canonical_rows=high_conf_classification_rows,
        scope_regex=scope_regex,
        sample_limit=max(0, collapse_audit_limit),
    )

    return {
        "status": "ok"
        if (
            smoke_delta["tx_count"] == 0
            and smoke_delta["inflow"] == 0
            and smoke_delta["outflow"] == 0
            and smoke_delta["net"] == 0
            and not per_file_mismatches
        )
        else "warning",
        "scope_regex": scope_regex,
        "reference_csv_path": str(reference_csv_path),
        "reference": reference,
        "database": {
            "statement_rows_gross": statement_rows,
            "canonical_transactions": canonical,
            "canonical_lens_per_file": canonical_per_file,
            "transfer_links_by_status": transfer_links,
        },
        "parity": {
            "smoke_delta_statement_rows_vs_reference_gross": smoke_delta,
            "per_file_gross_mismatches": per_file_mismatches,
            "prd_internal_transfer_lens_vs_reference_tier_b_gap": tier_b_gap,
            "prd_internal_transfer_lens_vs_reference_tier_b_gap_by_file": prd_tier_b_gap_by_file,
            "high_confidence_transfer_like_lens_vs_reference_tier_b_gap": high_conf_tier_b_gap,
            "high_confidence_transfer_like_lens_vs_reference_tier_b_gap_by_file": high_conf_tier_b_gap_by_file,
            "high_confidence_transfer_like_classification_mismatch": high_conf_classification_mismatch,
            "prd_internal_transfer_lens_vs_reference_tier_c_gap": tier_c_gap,
            "strict_transfer_like_lens_vs_reference_tier_c_gap": strict_tier_c_gap,
            "strict_transfer_like_lens_vs_reference_tier_c_gap_by_file": strict_tier_c_gap_by_file,
            "canonical_row_collapse_audit": collapse_audit,
            "analytics_source_recommendation": analytics_source_recommendation,
        },
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Emit metrics-quality diagnostics and optional reference reconciliation data.",
    )
    parser.add_argument(
        "--reference-csv",
        default=str(DEFAULT_REFERENCE_CSV),
        help=(
            "Path to a local reference CSV for reconciliation. "
            "Ignored when --skip-reference-reconciliation is used."
        ),
    )
    parser.add_argument(
        "--reference-scope-regex",
        default=DEFAULT_REFERENCE_SCOPE_REGEX,
        help=(
            "Regex applied to statement PDF basename for scoped reconciliation "
            "(default matches all_* statement files)."
        ),
    )
    parser.add_argument(
        "--skip-reference-reconciliation",
        action="store_true",
        help="Skip reference CSV reconciliation block.",
    )
    parser.add_argument(
        "--collapse-audit-limit",
        type=int,
        default=25,
        help="Max collapsed canonical transactions to include with row-level fingerprint diffs.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    database_url = _database_url_from_env()
    if not database_url:
        print("DATABASE_URL is required.", file=sys.stderr)
        return 1

    search_path = ""
    try:
        _validate_database_url(database_url)
        search_path = _validate_database_search_path(_database_search_path_from_env())
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        if search_path:
            with conn.cursor() as cur:
                cur.execute("select set_config('search_path', %s, false)", (search_path,))

        def run_query(query: str) -> dict[str, Any]:
            return _run_query(conn, query)

        def table_exists(qualified_table: str) -> bool:
            return _table_exists(conn, qualified_table)

        report = build_metrics_quality_payload(run_query=run_query, table_exists=table_exists)

        if args.skip_reference_reconciliation:
            report["reference_reconciliation"] = {
                "status": "skipped",
                "reason": "skip flag enabled",
            }
        else:
            reference_csv_path = Path(args.reference_csv).expanduser()
            if not reference_csv_path.is_absolute():
                reference_csv_path = (Path.cwd() / reference_csv_path).resolve()

            if reference_csv_path.exists():
                report["reference_reconciliation"] = _build_reference_reconciliation(
                    conn,
                    reference_csv_path=reference_csv_path,
                    scope_regex=args.reference_scope_regex,
                    collapse_audit_limit=max(0, args.collapse_audit_limit),
                )
            else:
                report["reference_reconciliation"] = {
                    "status": "reference_csv_missing",
                    "reference_csv_path": str(reference_csv_path),
                    "scope_regex": args.reference_scope_regex,
                }

    report["generated_at"] = (
        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    if search_path:
        report["configured_search_path"] = search_path
    print(json.dumps(report, ensure_ascii=False, indent=2, default=_json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
