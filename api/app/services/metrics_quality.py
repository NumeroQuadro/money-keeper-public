from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..domain.metrics_quality import (
    build_metrics_quality_payload,
)


def _run_query(db: Session, query: str) -> dict[str, Any]:
    result = db.execute(text(query)).mappings().first()
    return dict(result) if result else {}


def _table_exists(db: Session, table_name: str) -> bool:
    result = (
        db.execute(
            text("select to_regclass(:table_name) is not null as table_exists"),
            {"table_name": table_name},
        )
        .mappings()
        .first()
    )
    return bool((result or {}).get("table_exists"))


def build_metrics_quality_report(db: Session) -> dict[str, Any]:
    def run_query(query: str) -> Mapping[str, Any]:
        return _run_query(db, query)

    def table_exists(qualified_table: str) -> bool:
        return _table_exists(db, qualified_table)

    report = build_metrics_quality_payload(run_query=run_query, table_exists=table_exists)
    report["generated_at"] = datetime.now(timezone.utc)
    return report
