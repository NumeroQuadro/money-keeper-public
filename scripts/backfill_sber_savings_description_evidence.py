#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
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

DEFAULT_SCOPE_REGEX = r"all_saving_sberbank_[^/]+\.pdf$"


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


def _normalize_whitespace(value: str) -> str:
    return " ".join((value or "").strip().split())


def _normalize_statement_raw_text_for_description(raw_text: str) -> str:
    text = _normalize_whitespace(raw_text)
    if not text:
        return ""
    text = re.sub(r"^\d{2}\.\d{2}\.\d{4}(?:\s+\d{2}:\d{2})?\s+", "", text)

    # Trim common cross-line footer/table noise that may be merged into raw_text.
    lower = text.lower()
    for marker in (
        "дополнительная информация",
        "шифр, № документа",
        "на счёте",
        "страница",
    ):
        idx = lower.find(marker)
        if idx > 0:
            text = text[:idx].strip()
            lower = text.lower()

    return _normalize_whitespace(text)


def _query_candidate_rows(
    conn: psycopg.Connection[Any], *, scope_regex: str
) -> list[dict[str, Any]]:
    query = """
        with scoped_links as (
          select distinct
            t.id as transaction_id,
            lower(regexp_replace(s.pdf_path, '^.*/', '')) as source_file,
            coalesce(t.description_raw, '') as description_raw,
            coalesce(t.bank_category, '') as bank_category,
            coalesce(t.bank_reference_id, '') as bank_reference_id,
            coalesce(t.meaning, '') as meaning,
            coalesce(sr.raw_text, '') as statement_row_raw_text,
            sr.id as statement_row_id
          from public.transactions t
          join public.transaction_statement_links tsl on tsl.transaction_id = t.id
          join public.statement_rows sr on sr.id = tsl.statement_row_id
          join public.statements s on s.id = sr.statement_id
          where lower(regexp_replace(s.pdf_path, '^.*/', '')) ~ %s
            and lower(coalesce(t.bank_category, '')) = 'transfer'
            and coalesce(t.bank_reference_id, '') <> ''
            and coalesce(t.description_raw, '') in ('Зачисление', 'Списание')
        )
        select *
        from scoped_links
        order by transaction_id, statement_row_id
    """
    return _run_many_with_params(conn, query, (scope_regex,))


def _choose_best_description(raw_texts: Iterable[str]) -> str:
    normalized = [
        _normalize_statement_raw_text_for_description(text)
        for text in raw_texts
        if _normalize_statement_raw_text_for_description(text)
    ]
    if not normalized:
        return ""
    # Prefer richest evidence text (longest), deterministic tie-break (lexicographic).
    normalized.sort(key=lambda item: (-len(item), item))
    return normalized[0]


def _plan_updates(rows: Iterable[Mapping[str, Any]], *, sample_limit: int) -> dict[str, Any]:
    grouped: dict[str, dict[str, Any]] = {}
    scanned_rows = 0

    for row in rows:
        scanned_rows += 1
        tx_id = str(row.get("transaction_id") or "")
        if not tx_id:
            continue
        bucket = grouped.setdefault(
            tx_id,
            {
                "transaction_id": tx_id,
                "source_file": str(row.get("source_file") or ""),
                "before_description_raw": str(row.get("description_raw") or ""),
                "statement_row_ids": set(),
                "statement_row_raw_texts": [],
            },
        )
        statement_row_id = str(row.get("statement_row_id") or "")
        if statement_row_id:
            bucket["statement_row_ids"].add(statement_row_id)
        bucket["statement_row_raw_texts"].append(str(row.get("statement_row_raw_text") or ""))

    operations: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    for tx_id, bucket in sorted(grouped.items()):
        before_description = str(bucket.get("before_description_raw") or "")
        after_description = _choose_best_description(bucket.get("statement_row_raw_texts") or [])
        if not after_description or after_description == before_description:
            continue

        operation = {
            "transaction_id": tx_id,
            "source_file": str(bucket.get("source_file") or ""),
            "statement_row_count": len(bucket.get("statement_row_ids") or []),
            "before_description_raw": before_description,
            "after_description_raw": after_description,
        }
        operations.append(operation)
        if len(samples) < max(0, sample_limit):
            samples.append(operation)

    return {
        "scanned_rows": scanned_rows,
        "candidate_transactions": len(grouped),
        "planned_updates": len(operations),
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
                   set description_raw = %s
                 where id = %s
                """,
                (
                    str(operation.get("after_description_raw") or ""),
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
            "Backfill Sber savings transfer descriptions using linked statement-row raw text "
            "to preserve transfer evidence (k/s + operation code + inline reference)."
        )
    )
    parser.add_argument(
        "--scope-regex",
        default=DEFAULT_SCOPE_REGEX,
        help="Regex applied to statement source-file suffix.",
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
        rows = _query_candidate_rows(conn, scope_regex=args.scope_regex)
        plan = _plan_updates(rows, sample_limit=args.sample_limit)

        result: dict[str, Any] = {
            "scope_regex": args.scope_regex,
            "dry_run": not args.apply,
            "plan": {
                key: value
                for key, value in plan.items()
                if key not in {"operations"}
            },
            "sample": plan.get("sample", []),
        }

        if args.apply:
            result["apply"] = _apply_updates(conn, plan.get("operations", []))

    print(json.dumps(result, ensure_ascii=False, indent=2, default=_json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
