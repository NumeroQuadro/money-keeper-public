from __future__ import annotations

from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any

REPORTING_SOURCE_QUERY = """
select
  to_regclass('transactions')::text as canonical_relation,
  to_regclass('"transaction"')::text as legacy_relation,
  current_schema()::text as reporting_schema,
  current_setting('search_path', true)::text as active_search_path;
"""

CORE_QUERY = """
with unresolved as (
  select id, transaction_out_id, transaction_in_id
  from transfer_links
  where status = 'suggested'
),
unresolved_ids as (
  select distinct tx_id
  from (
    select transaction_out_id as tx_id from unresolved
    union all
    select transaction_in_id as tx_id from unresolved
  ) u
  where tx_id is not null
),
unresolved_tx as (
  select t.id, t.direction, t.amount
  from transactions t
  join unresolved_ids u on u.tx_id = t.id
),
orphans as (
  select count(*)::int as orphan_link_rows
  from transfer_links tl
  left join transactions t_out on t_out.id = tl.transaction_out_id
  left join transactions t_in on t_in.id = tl.transaction_in_id
  where t_out.id is null or t_in.id is null
)
select
  count(*)::int as transactions_total,
  count(*) filter (where direction = 'out')::int as outflow_count,
  count(*) filter (where direction = 'in')::int as inflow_count,
  coalesce(sum(case when direction = 'out' then amount else 0 end), 0) as gross_outflow_amount,
  coalesce(sum(case when direction = 'in' then amount else 0 end), 0) as gross_inflow_amount,
  count(*) filter (where coalesce(meaning, '') = 'internal_transfer')::int as internal_transfer_count,
  count(*) filter (where coalesce(meaning, '') <> 'internal_transfer' and direction = 'out')::int as true_spend_ops,
  count(*) filter (where coalesce(meaning, '') <> 'internal_transfer' and direction = 'in')::int as true_income_ops,
  coalesce(sum(case when coalesce(meaning, '') <> 'internal_transfer' and direction = 'out' then amount else 0 end), 0) as true_spend_amount,
  coalesce(sum(case when coalesce(meaning, '') <> 'internal_transfer' and direction = 'in' then amount else 0 end), 0) as true_income_amount,
  (select count(*)::int from transfer_links where status = 'auto') as auto_links,
  (select count(*)::int from transfer_links where status = 'suggested') as suggested_links,
  (select count(*)::int from transfer_links where status = 'confirmed') as confirmed_links,
  (select count(*)::int from transfer_links where status = 'rejected') as rejected_links,
  (select count(distinct id)::int from unresolved_tx) as unique_tx_in_suggested_links,
  (select coalesce(sum(case when direction = 'out' then amount else 0 end), 0) from unresolved_tx) as suggested_outflow_amount,
  (select coalesce(sum(case when direction = 'in' then amount else 0 end), 0) from unresolved_tx) as suggested_inflow_amount,
  (select orphan_link_rows from orphans) as orphan_link_rows
from transactions;
"""

LEGACY_QUERY = """
select
  count(*)::int as legacy_transactions_total,
  count(*) filter (where direction = 'out')::int as legacy_outflow_count,
  count(*) filter (where direction = 'in')::int as legacy_inflow_count,
  coalesce(sum(case when direction = 'out' then amount else 0 end), 0) as legacy_outflow_sum,
  coalesce(sum(case when direction = 'in' then amount else 0 end), 0) as legacy_inflow_sum
from "transaction";
"""

RECONCILIATION_MISMATCH_QUERY = """
select
  count(*)::int as reconciliation_mismatch_statements
from statements
where coalesce(reconcile_status, '') = 'mismatch';
"""

STATEMENT_LINK_INTEGRITY_QUERY = """
select
  count(*) filter (where t.id is null or sr.id is null)::int as orphan_statement_link_rows,
  count(*) filter (where t.id is null)::int as statement_links_missing_transaction,
  count(*) filter (where sr.id is null)::int as statement_links_missing_row
from transaction_statement_links tsl
left join transactions t on t.id = tsl.transaction_id
left join statement_rows sr on sr.id = tsl.statement_row_id;
"""

UNLINKED_STATEMENT_ROWS_QUERY = """
select
  count(*)::int as unlinked_statement_rows
from statement_rows sr
left join transaction_statement_links tsl on tsl.statement_row_id = sr.id
where tsl.statement_row_id is null;
"""

UNLINKED_TRANSACTIONS_QUERY = """
select
  count(*)::int as unlinked_transactions
from transactions t
left join transaction_statement_links tsl on tsl.transaction_id = t.id
where tsl.transaction_id is null;
"""

RLS_PUBLIC_EXPOSURE_QUERY = """
with anon_role as (
  select to_regrole('anon') as role_oid
),
flagged as (
  select c.relname
  from pg_class c
  join pg_namespace n on n.oid = c.relnamespace
  join anon_role ar on ar.role_oid is not null
  where n.nspname = 'public'
    and c.relkind = 'r'
    and c.relrowsecurity = false
    and has_table_privilege(ar.role_oid, c.oid, 'select')
)
select
  (select count(*)::int from flagged) as rls_disabled_public_tables,
  coalesce(
    (
      select array_agg(relname order by relname)
      from (
        select relname
        from flagged
        order by relname
        limit 10
      ) sample
    ),
    ARRAY[]::text[]
  ) as rls_disabled_public_table_samples;
"""

FUNCTION_SEARCH_PATH_EXPOSURE_QUERY = """
with anon_role as (
  select to_regrole('anon') as role_oid
),
flagged as (
  select p.oid::regprocedure::text as function_signature
  from pg_proc p
  join pg_namespace n on n.oid = p.pronamespace
  join anon_role ar on ar.role_oid is not null
  where n.nspname = 'public'
    and p.prokind = 'f'
    and not exists (
      select 1
      from pg_depend d
      where d.classid = 'pg_proc'::regclass
        and d.objid = p.oid
        and d.deptype = 'e'
    )
    and has_function_privilege(ar.role_oid, p.oid, 'execute')
    and not exists (
      select 1
      from unnest(coalesce(p.proconfig, ARRAY[]::text[])) cfg
      where cfg like 'search_path=%'
    )
)
select
  (select count(*)::int from flagged) as functions_without_explicit_search_path,
  coalesce(
    (
      select array_agg(function_signature order by function_signature)
      from (
        select function_signature
        from flagged
        order by function_signature
        limit 10
      ) sample
    ),
    ARRAY[]::text[]
  ) as functions_without_explicit_search_path_samples;
"""

INT_FIELDS = (
    "transactions_total",
    "outflow_count",
    "inflow_count",
    "internal_transfer_count",
    "true_spend_ops",
    "true_income_ops",
    "auto_links",
    "suggested_links",
    "confirmed_links",
    "rejected_links",
    "unique_tx_in_suggested_links",
    "orphan_link_rows",
    "legacy_transactions_total",
    "legacy_outflow_count",
    "legacy_inflow_count",
    "reconciliation_mismatch_statements",
    "orphan_statement_link_rows",
    "statement_links_missing_transaction",
    "statement_links_missing_row",
    "unlinked_statement_rows",
    "unlinked_transactions",
    "rls_disabled_public_tables",
    "functions_without_explicit_search_path",
)

FLOAT_FIELDS = (
    "gross_outflow_amount",
    "gross_inflow_amount",
    "true_spend_amount",
    "true_income_amount",
    "suggested_outflow_amount",
    "suggested_inflow_amount",
    "legacy_outflow_sum",
    "legacy_inflow_sum",
    "unresolved_transfer_net_impact",
    "unresolved_transfer_gross_impact",
)

QueryRunner = Callable[[str], Mapping[str, Any]]
TableExistsChecker = Callable[[str], bool]


def to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value in (None, ""):
        return Decimal("0")
    return Decimal(str(value))


def to_int(value: Any) -> int:
    if isinstance(value, int):
        return value
    return int(to_decimal(value))


def to_float(value: Any) -> float:
    return float(to_decimal(value))


def _percent_delta(delta: Decimal, base: Decimal) -> float | None:
    if base == 0:
        return None
    return float((delta / base) * Decimal("100"))


def _relation_name(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text


def _default_core_metrics() -> dict[str, Any]:
    return {
        "transactions_total": 0,
        "outflow_count": 0,
        "inflow_count": 0,
        "gross_outflow_amount": 0,
        "gross_inflow_amount": 0,
        "internal_transfer_count": 0,
        "true_spend_ops": 0,
        "true_income_ops": 0,
        "true_spend_amount": 0,
        "true_income_amount": 0,
        "auto_links": 0,
        "suggested_links": 0,
        "confirmed_links": 0,
        "rejected_links": 0,
        "unique_tx_in_suggested_links": 0,
        "suggested_outflow_amount": 0,
        "suggested_inflow_amount": 0,
        "orphan_link_rows": 0,
    }


def attach_legacy_parity(report: dict[str, Any]) -> None:
    legacy_outflow_raw = to_decimal(report.get("legacy_outflow_sum"))
    legacy_outflow_abs = abs(legacy_outflow_raw)
    canonical_outflow = to_decimal(report.get("gross_outflow_amount"))
    canonical_inflow = to_decimal(report.get("gross_inflow_amount"))
    legacy_inflow = to_decimal(report.get("legacy_inflow_sum"))

    transactions_total_delta = to_int(report.get("transactions_total")) - to_int(
        report.get("legacy_transactions_total")
    )
    outflow_count_delta = to_int(report.get("outflow_count")) - to_int(
        report.get("legacy_outflow_count")
    )
    inflow_count_delta = to_int(report.get("inflow_count")) - to_int(
        report.get("legacy_inflow_count")
    )
    outflow_amount_delta = canonical_outflow - legacy_outflow_abs
    inflow_amount_delta = canonical_inflow - legacy_inflow

    parity = {
        "transactions_total_delta": transactions_total_delta,
        "outflow_count_delta": outflow_count_delta,
        "inflow_count_delta": inflow_count_delta,
        "outflow_amount_delta": float(outflow_amount_delta),
        "inflow_amount_delta": float(inflow_amount_delta),
        "transactions_total_delta_pct": _percent_delta(
            Decimal(transactions_total_delta),
            Decimal(to_int(report.get("legacy_transactions_total"))),
        ),
        "outflow_count_delta_pct": _percent_delta(
            Decimal(outflow_count_delta),
            Decimal(to_int(report.get("legacy_outflow_count"))),
        ),
        "inflow_count_delta_pct": _percent_delta(
            Decimal(inflow_count_delta),
            Decimal(to_int(report.get("legacy_inflow_count"))),
        ),
        "outflow_amount_delta_pct": _percent_delta(outflow_amount_delta, legacy_outflow_abs),
        "inflow_amount_delta_pct": _percent_delta(inflow_amount_delta, legacy_inflow),
        "legacy_outflow_sign": "negative" if legacy_outflow_raw < 0 else "non_negative",
    }
    parity["status"] = (
        "ok"
        if (
            parity["transactions_total_delta"] == 0
            and parity["outflow_count_delta"] == 0
            and parity["inflow_count_delta"] == 0
            and parity["outflow_amount_delta"] == 0
            and parity["inflow_amount_delta"] == 0
        )
        else "drift"
    )
    report["legacy_parity"] = parity


def attach_reconciliation_signals(
    report: dict[str, Any],
    *,
    run_query: QueryRunner,
    table_exists: TableExistsChecker,
) -> None:
    report["reconciliation_mismatch_statements"] = 0
    report["orphan_statement_link_rows"] = 0
    report["statement_links_missing_transaction"] = 0
    report["statement_links_missing_row"] = 0
    report["unlinked_statement_rows"] = 0
    report["unlinked_transactions"] = 0

    if table_exists("statements"):
        result = run_query(RECONCILIATION_MISMATCH_QUERY)
        report.update(dict(result))

    has_transactions = table_exists("transactions")
    has_statement_rows = table_exists("statement_rows")
    has_statement_links = table_exists("transaction_statement_links")

    if has_transactions and has_statement_rows and has_statement_links:
        report.update(dict(run_query(STATEMENT_LINK_INTEGRITY_QUERY)))
        report.update(dict(run_query(UNLINKED_STATEMENT_ROWS_QUERY)))
        report.update(dict(run_query(UNLINKED_TRANSACTIONS_QUERY)))


def attach_security_signals(
    report: dict[str, Any],
    *,
    run_query: QueryRunner,
) -> None:
    report["rls_disabled_public_tables"] = 0
    report["rls_disabled_public_table_samples"] = []
    report["functions_without_explicit_search_path"] = 0
    report["functions_without_explicit_search_path_samples"] = []

    report.update(dict(run_query(RLS_PUBLIC_EXPOSURE_QUERY)))
    report.update(dict(run_query(FUNCTION_SEARCH_PATH_EXPOSURE_QUERY)))


def attach_quality_summary(report: dict[str, Any]) -> None:
    suggested_outflow = to_decimal(report.get("suggested_outflow_amount"))
    suggested_inflow = to_decimal(report.get("suggested_inflow_amount"))
    report["unresolved_transfer_net_impact"] = suggested_inflow - suggested_outflow
    report["unresolved_transfer_gross_impact"] = suggested_inflow + suggested_outflow

    flags: list[str] = []
    if not bool(report.get("canonical_table_exists", True)):
        flags.append("canonical_reporting_table_missing")
    legacy_parity = report.get("legacy_parity")
    if isinstance(legacy_parity, Mapping) and legacy_parity.get("status") == "drift":
        flags.append("legacy_canonical_drift")
    reporting_schema = str(report.get("reporting_schema") or "").strip().lower()
    if reporting_schema and reporting_schema not in {"public", "unknown"}:
        flags.append("non_public_reporting_schema")
    if to_int(report.get("suggested_links")) > 0:
        flags.append("suggested_transfer_links_pending")
    if to_int(report.get("orphan_link_rows")) > 0:
        flags.append("orphan_transfer_links")
    if to_int(report.get("orphan_statement_link_rows")) > 0:
        flags.append("orphan_statement_links")
    if to_int(report.get("reconciliation_mismatch_statements")) > 0:
        flags.append("statement_reconciliation_mismatch")
    if to_int(report.get("unlinked_statement_rows")) > 0:
        flags.append("statement_rows_unlinked")
    if to_int(report.get("rls_disabled_public_tables")) > 0:
        flags.append("public_tables_without_rls")
    if to_int(report.get("functions_without_explicit_search_path")) > 0:
        flags.append("functions_without_explicit_search_path")

    status = "ok"
    if flags:
        status = "warning"
    if (
        "canonical_reporting_table_missing" in flags
        or "orphan_transfer_links" in flags
        or "orphan_statement_links" in flags
    ):
        status = "critical"

    recommendations: list[str] = []
    if "canonical_reporting_table_missing" in flags:
        recommendations.append(
            "Canonical table 'transactions' is missing from the active schema/search_path; "
            "run migrations and verify DB search_path before trusting metrics."
        )
    if "legacy_canonical_drift" in flags:
        recommendations.append(
            "Use canonical table 'transactions' for reporting and migrate remaining legacy consumers."
        )
    if "non_public_reporting_schema" in flags:
        recommendations.append(
            "Metrics are resolving from a non-public schema. Confirm this is intentional and "
            "align API role search_path with production expectations."
        )
    if "suggested_transfer_links_pending" in flags:
        recommendations.append(
            "Review suggested transfer links to reduce unresolved transfer impact on spend/income."
        )
    if "statement_reconciliation_mismatch" in flags:
        recommendations.append("Investigate statements with reconcile_status='mismatch'.")
    if "statement_rows_unlinked" in flags:
        recommendations.append(
            "Backfill transaction_statement_links for parsed rows without canonical transactions."
        )
    if "public_tables_without_rls" in flags:
        recommendations.append(
            "Enable RLS (or remove anon SELECT grants) for exposed public tables not intended for direct PostgREST access."
        )
    if "functions_without_explicit_search_path" in flags:
        recommendations.append(
            "Set explicit search_path on exposed SQL/PLpgSQL functions to prevent mutable role search_path behavior."
        )
    if "orphan_transfer_links" in flags or "orphan_statement_links" in flags:
        recommendations.append(
            "Treat orphan links as data integrity incidents and repair missing referenced records."
        )

    report["quality"] = {"status": status, "flags": flags, "recommendations": recommendations}


def build_metrics_quality_payload(
    *,
    run_query: QueryRunner,
    table_exists: TableExistsChecker,
) -> dict[str, Any]:
    source_meta = dict(run_query(REPORTING_SOURCE_QUERY))
    canonical_relation = _relation_name(source_meta.get("canonical_relation"))
    legacy_relation = _relation_name(source_meta.get("legacy_relation"))
    canonical_exists = bool(canonical_relation) or table_exists("transactions")
    report: dict[str, Any] = (
        dict(run_query(CORE_QUERY)) if canonical_exists else _default_core_metrics()
    )

    report["canonical_table_exists"] = canonical_exists
    report["canonical_reporting_table"] = canonical_relation or "transactions"
    report["reporting_schema"] = (
        str(source_meta.get("reporting_schema") or "unknown").strip() or "unknown"
    )
    report["active_search_path"] = (
        str(source_meta.get("active_search_path") or "").strip() or "unknown"
    )

    legacy_exists = bool(legacy_relation) or table_exists("transaction")
    report["legacy_table_exists"] = legacy_exists
    report["legacy_reporting_table"] = legacy_relation if legacy_exists else None
    if legacy_exists:
        report.update(dict(run_query(LEGACY_QUERY)))
        attach_legacy_parity(report)
    else:
        report["legacy_parity"] = {"status": "not_present"}

    attach_reconciliation_signals(report, run_query=run_query, table_exists=table_exists)
    attach_security_signals(report, run_query=run_query)
    attach_quality_summary(report)
    coerce_numeric_types(report)
    return report


def coerce_numeric_types(report: dict[str, Any]) -> None:
    for name in INT_FIELDS:
        if name in report:
            report[name] = to_int(report.get(name))

    for name in FLOAT_FIELDS:
        if name in report:
            report[name] = to_float(report.get(name))
