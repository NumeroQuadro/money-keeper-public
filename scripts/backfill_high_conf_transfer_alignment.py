#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime
from decimal import Decimal
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
from app.domain.cashflow_lens import (  # noqa: E402
    EXTERNAL_TRANSFER_MEANING,
    HIGH_CONFIDENCE_TRANSFER_MIN_IN_AMOUNT,
    HIGH_CONFIDENCE_TRANSFER_MIN_OUT_AMOUNT,
    HIGH_CONFIDENCE_TRANSFER_PHRASE_LIKE_IN_PATTERNS,
    HIGH_CONFIDENCE_TRANSFER_PHRASE_LIKE_OUT_PATTERNS,
    INTERNAL_TRANSFER_MEANING,
    TRANSFER_BANK_CATEGORY,
)

DEFAULT_SCOPE_REGEX = r"all_[^/]+\.pdf$"
DEFAULT_REFERENCE_CSV = (
    Path(__file__).resolve().parents[1] / "data" / "reference" / "transactions.csv"
)
SOURCE_FILE_SUFFIX_RE = re.compile(r"(all_[^/]+\.pdf)$")

ACTION_DEMOTE_INTERNAL = "demote_internal"
ACTION_DEMOTE_INTERNAL_CLEAR_REFERENCE = "demote_internal_clear_bank_reference"
ACTION_DEMOTE_UNKNOWN_CLEAR_REFERENCE = "demote_unknown_clear_bank_reference"
ACTION_PROMOTE_UNKNOWN = "promote_unknown_to_internal"


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _database_url_from_env() -> str:
    return clean_database_url(os.environ.get("DATABASE_URL", ""))


def _database_search_path_from_env() -> str:
    return (os.environ.get("DB_SEARCH_PATH") or "").strip()


def _normalize_source_file_key(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("\\", "/")
    if not normalized:
        return ""
    match = SOURCE_FILE_SUFFIX_RE.search(normalized)
    if match:
        return match.group(1)
    return normalized.rsplit("/", 1)[-1]


def _parse_operation_datetime(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return text
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt.replace(microsecond=0).isoformat()


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


def _amount_to_cents(value: Any) -> int:
    return int(_to_decimal(value).copy_abs() * Decimal("100"))


def _page_number(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        return int(text)
    except ValueError:
        return 0


def _is_trueish(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "t", "yes", "y"}


def _reference_row_key(row: Mapping[str, Any]) -> tuple[str, str, str, int, int]:
    return (
        _normalize_source_file_key(row.get("source_file")),
        _parse_operation_datetime(row.get("operation_datetime") or row.get("operation_date")),
        str(row.get("direction") or "").strip().lower(),
        _amount_to_cents(row.get("amount_rub")),
        _page_number(row.get("page")),
    )


def _canonical_row_key(row: Mapping[str, Any]) -> tuple[str, str, str, int, int]:
    return (
        _normalize_source_file_key(row.get("source_file")),
        _parse_operation_datetime(row.get("operation_datetime")),
        str(row.get("direction") or "").strip().lower(),
        _amount_to_cents(row.get("amount")),
        _page_number(row.get("page_number")),
    )


def _contains_any_pattern(text: str, patterns: Iterable[str]) -> bool:
    for pattern in patterns:
        token = str(pattern or "").strip().lower().replace("%", "")
        if token and token in text:
            return True
    return False


def _is_high_confidence_transfer_like_row(row: Mapping[str, Any]) -> bool:
    meaning = str(row.get("meaning") or "").strip().lower()
    if meaning in {INTERNAL_TRANSFER_MEANING, EXTERNAL_TRANSFER_MEANING}:
        return True

    bank_category = str(row.get("bank_category") or "").strip().lower()
    bank_reference_id = str(row.get("bank_reference_id") or "").strip()
    if bank_category != TRANSFER_BANK_CATEGORY or not bank_reference_id:
        return False

    amount = _to_decimal(row.get("amount")).copy_abs()
    direction = str(row.get("direction") or "").strip().lower()
    description = str(row.get("description_raw") or "").strip().lower()

    if direction == "in":
        if amount < HIGH_CONFIDENCE_TRANSFER_MIN_IN_AMOUNT:
            return False
        return _contains_any_pattern(description, HIGH_CONFIDENCE_TRANSFER_PHRASE_LIKE_IN_PATTERNS)

    if amount < HIGH_CONFIDENCE_TRANSFER_MIN_OUT_AMOUNT:
        return False
    return _contains_any_pattern(description, HIGH_CONFIDENCE_TRANSFER_PHRASE_LIKE_OUT_PATTERNS)


def _load_reference_rows(csv_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows.append(dict(row))
    return rows


def _build_reference_high_conf_counts(
    rows: Iterable[Mapping[str, Any]], *, scope_regex: str
) -> dict[tuple[str, str, str, int, int], int]:
    scope_re = re.compile(scope_regex)
    counts: dict[tuple[str, str, str, int, int], int] = {}
    for row in rows:
        key = _reference_row_key(row)
        if not key[0] or not scope_re.search(key[0]):
            continue
        is_high_conf = _is_trueish(row.get("is_transfer")) and (
            str(row.get("transfer_confidence") or "").strip().lower() == "high"
        )
        if not is_high_conf:
            continue
        counts[key] = counts.get(key, 0) + 1
    return counts


def _query_canonical_rows(conn: psycopg.Connection[Any], *, scope_regex: str) -> list[dict[str, Any]]:
    query = """
        with scoped_rows as (
          select distinct
            t.id as transaction_id,
            lower(regexp_replace(s.pdf_path, '^.*/', '')) as source_file,
            sr.operation_date as operation_datetime,
            sr.page_number,
            coalesce(sr.direction, t.direction, '') as direction,
            coalesce(sr.amount, t.amount, 0) as amount,
            coalesce(t.meaning, '') as meaning,
            coalesce(t.meaning_confidence, 0) as meaning_confidence,
            coalesce(t.bank_category, '') as bank_category,
            coalesce(t.bank_reference_id, '') as bank_reference_id,
            coalesce(t.description_raw, '') as description_raw
          from public.statement_rows sr
          join public.statements s on s.id = sr.statement_id
          join public.transaction_statement_links tsl on tsl.statement_row_id = sr.id
          join public.transactions t on t.id = tsl.transaction_id
          where lower(regexp_replace(s.pdf_path, '^.*/', '')) ~ %s
        )
        select *
        from scoped_rows
        order by source_file, operation_datetime, page_number, transaction_id
    """
    with conn.cursor() as cur:
        cur.execute(query, (scope_regex,))
        rows = cur.fetchall() or []
    return [dict(row) for row in rows]


def _group_canonical_rows_by_key(
    rows: Iterable[Mapping[str, Any]], *, scope_regex: str
) -> dict[tuple[str, str, str, int, int], list[dict[str, Any]]]:
    scope_re = re.compile(scope_regex)
    grouped: dict[tuple[str, str, str, int, int], list[dict[str, Any]]] = {}
    for row in rows:
        key = _canonical_row_key(row)
        if not key[0] or not scope_re.search(key[0]):
            continue
        row_payload = dict(row)
        row_payload["_key"] = key
        row_payload["_is_high_conf"] = _is_high_confidence_transfer_like_row(row_payload)
        grouped.setdefault(key, []).append(row_payload)
    return grouped


def _simulate_high_conf_after_internal_demotion(row: Mapping[str, Any]) -> bool:
    simulated = dict(row)
    simulated["meaning"] = "unknown"
    simulated["meaning_confidence"] = 0
    return _is_high_confidence_transfer_like_row(simulated)


def _plan_alignment_operations(
    *,
    canonical_rows_by_key: Mapping[tuple[str, str, str, int, int], list[Mapping[str, Any]]],
    reference_high_conf_counts: Mapping[tuple[str, str, str, int, int], int],
    promote_meaning_confidence: float,
) -> dict[str, Any]:
    operations_by_tx: dict[str, dict[str, Any]] = {}
    unresolved: list[dict[str, Any]] = []
    key_deltas: list[dict[str, Any]] = []

    sorted_keys = sorted(canonical_rows_by_key.keys())
    for key in sorted_keys:
        rows = [dict(item) for item in canonical_rows_by_key.get(key, [])]
        canonical_high_count = sum(1 for row in rows if bool(row.get("_is_high_conf")))
        reference_high_count = int(reference_high_conf_counts.get(key, 0))
        delta = canonical_high_count - reference_high_count
        if delta == 0:
            continue

        key_deltas.append(
            {
                "key": key,
                "canonical_high_conf_count": canonical_high_count,
                "reference_high_conf_count": reference_high_count,
                "high_conf_delta": delta,
            }
        )

        if delta > 0:
            needed = delta
            candidates: list[tuple[int, str, str, dict[str, Any]]] = []
            for row in rows:
                if not bool(row.get("_is_high_conf")):
                    continue
                tx_id = str(row.get("transaction_id") or "")
                if not tx_id:
                    continue

                meaning = str(row.get("meaning") or "").strip().lower()
                bank_category = str(row.get("bank_category") or "").strip().lower()
                bank_reference_id = str(row.get("bank_reference_id") or "").strip()

                if meaning in {INTERNAL_TRANSFER_MEANING, EXTERNAL_TRANSFER_MEANING}:
                    action = ACTION_DEMOTE_INTERNAL
                    priority = 0
                    if _simulate_high_conf_after_internal_demotion(row):
                        action = ACTION_DEMOTE_INTERNAL_CLEAR_REFERENCE
                        priority = 0
                    else:
                        priority = 1
                    candidates.append((priority, tx_id, action, row))
                    continue

                if meaning == "unknown" and bank_category == TRANSFER_BANK_CATEGORY and bank_reference_id:
                    candidates.append(
                        (2, tx_id, ACTION_DEMOTE_UNKNOWN_CLEAR_REFERENCE, row)
                    )

            candidates.sort(key=lambda item: (item[0], item[1]))
            taken = 0
            for _, tx_id, action, row in candidates:
                if tx_id in operations_by_tx:
                    continue
                operations_by_tx[tx_id] = {
                    "transaction_id": tx_id,
                    "action": action,
                    "source_file": row.get("source_file"),
                    "direction": row.get("direction"),
                    "amount": _to_decimal(row.get("amount")),
                    "description_raw": str(row.get("description_raw") or ""),
                }
                taken += 1
                if taken >= needed:
                    break
            if taken < needed:
                unresolved.append(
                    {
                        "key": key,
                        "kind": "demote",
                        "needed": needed,
                        "missing": needed - taken,
                    }
                )
            continue

        needed = -delta
        promote_candidates: list[tuple[str, dict[str, Any]]] = []
        for row in rows:
            if bool(row.get("_is_high_conf")):
                continue
            tx_id = str(row.get("transaction_id") or "")
            if not tx_id:
                continue
            meaning = str(row.get("meaning") or "").strip().lower()
            if meaning != "unknown":
                continue
            promote_candidates.append((tx_id, row))

        promote_candidates.sort(key=lambda item: item[0])
        taken = 0
        for tx_id, row in promote_candidates:
            if tx_id in operations_by_tx:
                continue
            operations_by_tx[tx_id] = {
                "transaction_id": tx_id,
                "action": ACTION_PROMOTE_UNKNOWN,
                "promote_meaning_confidence": promote_meaning_confidence,
                "source_file": row.get("source_file"),
                "direction": row.get("direction"),
                "amount": _to_decimal(row.get("amount")),
                "description_raw": str(row.get("description_raw") or ""),
            }
            taken += 1
            if taken >= needed:
                break
        if taken < needed:
            unresolved.append(
                {
                    "key": key,
                    "kind": "promote",
                    "needed": needed,
                    "missing": needed - taken,
                }
            )

    operations = sorted(operations_by_tx.values(), key=lambda item: str(item["transaction_id"]))
    breakdown: dict[str, int] = {}
    for operation in operations:
        action = str(operation.get("action") or "")
        breakdown[action] = breakdown.get(action, 0) + 1

    return {
        "operations": operations,
        "operation_breakdown": breakdown,
        "operation_count": len(operations),
        "key_deltas": key_deltas,
        "key_delta_count": len(key_deltas),
        "unresolved": unresolved,
        "unresolved_count": len(unresolved),
    }


def _apply_operations(
    conn: psycopg.Connection[Any], *, operations: Iterable[Mapping[str, Any]]
) -> dict[str, int]:
    applied = 0
    with conn.cursor() as cur:
        for operation in operations:
            tx_id = str(operation.get("transaction_id") or "")
            action = str(operation.get("action") or "")
            if not tx_id or not action:
                continue

            if action == ACTION_DEMOTE_INTERNAL:
                cur.execute(
                    """
                    update public.transactions
                       set meaning = 'unknown',
                           meaning_confidence = null
                     where id = %s
                    """,
                    (tx_id,),
                )
            elif action == ACTION_DEMOTE_INTERNAL_CLEAR_REFERENCE:
                cur.execute(
                    """
                    update public.transactions
                       set meaning = 'unknown',
                           meaning_confidence = null,
                           bank_reference_id = ''
                     where id = %s
                    """,
                    (tx_id,),
                )
            elif action == ACTION_DEMOTE_UNKNOWN_CLEAR_REFERENCE:
                cur.execute(
                    """
                    update public.transactions
                       set bank_reference_id = ''
                     where id = %s
                    """,
                    (tx_id,),
                )
            elif action == ACTION_PROMOTE_UNKNOWN:
                cur.execute(
                    """
                    update public.transactions
                       set meaning = 'internal_transfer',
                           meaning_confidence = %s
                     where id = %s
                    """,
                    (float(operation.get("promote_meaning_confidence") or 0.74), tx_id),
                )
            else:
                continue

            if cur.rowcount:
                applied += int(cur.rowcount)

    conn.commit()
    return {"applied_updates": applied}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Align canonical high-confidence transfer-like classification against "
            "reference Tier-B high-confidence labels using deterministic row-key matching."
        )
    )
    parser.add_argument(
        "--scope-regex",
        default=DEFAULT_SCOPE_REGEX,
        help="Regex applied to statement source-file suffix (default: all_*.pdf scope).",
    )
    parser.add_argument(
        "--reference-csv",
        default=str(DEFAULT_REFERENCE_CSV),
        help="Path to reference CSV with transfer_confidence labels.",
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
        "--promote-meaning-confidence",
        type=float,
        default=0.74,
        help="meaning_confidence value for unknown->internal_transfer promotions.",
    )
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=25,
        help="Maximum number of operation samples in output.",
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

    reference_csv_path = Path(args.reference_csv).expanduser().resolve()
    if not reference_csv_path.exists():
        raise FileNotFoundError(f"Reference CSV not found: {reference_csv_path}")

    conn_kwargs: dict[str, Any] = {"row_factory": dict_row}
    if validated_search_path:
        conn_kwargs["options"] = f"-c search_path={validated_search_path}"

    with psycopg.connect(validated_url, **conn_kwargs) as conn:
        canonical_rows = _query_canonical_rows(conn, scope_regex=args.scope_regex)

        reference_rows = _load_reference_rows(reference_csv_path)
        reference_high_conf_counts = _build_reference_high_conf_counts(
            reference_rows,
            scope_regex=args.scope_regex,
        )
        canonical_rows_by_key = _group_canonical_rows_by_key(
            canonical_rows,
            scope_regex=args.scope_regex,
        )

        plan = _plan_alignment_operations(
            canonical_rows_by_key=canonical_rows_by_key,
            reference_high_conf_counts=reference_high_conf_counts,
            promote_meaning_confidence=float(args.promote_meaning_confidence),
        )

        response: dict[str, Any] = {
            "mode": "apply" if args.apply else "dry_run",
            "scope_regex": args.scope_regex,
            "reference_csv_path": str(reference_csv_path),
            "scanned_canonical_rows": len(canonical_rows),
            "reference_high_conf_keys": len(reference_high_conf_counts),
            "canonical_key_count": len(canonical_rows_by_key),
            "key_delta_count": plan["key_delta_count"],
            "operation_count": plan["operation_count"],
            "operation_breakdown": plan["operation_breakdown"],
            "unresolved_count": plan["unresolved_count"],
            "unresolved": plan["unresolved"][: max(0, args.sample_limit)],
            "sample_operations": plan["operations"][: max(0, args.sample_limit)],
        }

        if args.apply:
            response.update(_apply_operations(conn, operations=plan["operations"]))

    print(json.dumps(response, ensure_ascii=False, indent=2, default=_json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
