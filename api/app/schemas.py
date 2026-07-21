from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class AccountCreate(BaseModel):
    provider: str = "unknown"
    account_type: str = "unknown"
    display_name: str = ""
    masked_identifier: str = ""
    currency: str = "RUB"
    include_in_net_worth: bool = True


class AccountOut(AccountCreate):
    id: str
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class StatementOut(BaseModel):
    id: str
    provider: str
    account_id: Optional[str] = None
    account_display: str
    statement_type: str
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    generated_at: Optional[datetime] = None
    currency: str
    opening_balance: Optional[float] = None
    closing_balance: Optional[float] = None
    total_credits: Optional[float] = None
    total_debits: Optional[float] = None
    parse_confidence: Optional[float] = None
    reconcile_status: str
    pdf_path: str
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class StatementRowOut(BaseModel):
    id: str
    statement_id: str
    row_index: int
    page_number: int
    raw_text: str
    raw_data: Optional[dict[str, Any]] = None
    amount: Optional[float] = None
    currency: str
    direction: str
    operation_date: Optional[datetime] = None
    posting_date: Optional[datetime] = None
    parse_confidence: Optional[float] = None

    model_config = ConfigDict(from_attributes=True)


class TransactionCreate(BaseModel):
    account_id: Optional[str] = None
    operation_datetime: Optional[datetime] = None
    posting_datetime: Optional[datetime] = None
    timestamp_precision: str = "unknown"
    amount: float
    currency: str = "RUB"
    direction: str = "out"
    description_raw: str = ""
    merchant_normalized: str = ""
    bank_reference_id: str = ""
    bank_category: str = ""
    meaning: str = "unknown"
    meaning_confidence: Optional[float] = None
    category: str = ""
    tags: Optional[List[str]] = None
    review_status: str = "reviewed"
    source_statement_id: str = ""
    source_page_number: int = 0
    source_row_index: int = 0


class TransactionOut(TransactionCreate):
    id: str
    review_reasons: List[str] = Field(default_factory=list)
    needs_human_review: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class TransactionList(BaseModel):
    total: int
    items: List[TransactionOut]


class TransferLinkOut(BaseModel):
    id: str
    transaction_out_id: str
    transaction_in_id: str
    status: str
    match_score: Optional[float] = None
    rationale: str
    fee_amount: Optional[float] = None

    model_config = ConfigDict(from_attributes=True)


class TransferDetectOut(BaseModel):
    links_created: int
    auto_links_created: int
    suggested_links_created: int
    transactions_marked_internal: int


class MetricsQualitySummaryOut(BaseModel):
    status: str
    flags: List[str] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)


class LegacyParityOut(BaseModel):
    status: str
    transactions_total_delta: int = 0
    outflow_count_delta: int = 0
    inflow_count_delta: int = 0
    outflow_amount_delta: float = 0
    inflow_amount_delta: float = 0
    transactions_total_delta_pct: Optional[float] = None
    outflow_count_delta_pct: Optional[float] = None
    inflow_count_delta_pct: Optional[float] = None
    outflow_amount_delta_pct: Optional[float] = None
    inflow_amount_delta_pct: Optional[float] = None
    legacy_outflow_sign: Optional[str] = None


class MetricsQualityOut(BaseModel):
    generated_at: datetime

    quality: MetricsQualitySummaryOut
    canonical_table_exists: bool = True
    canonical_reporting_table: str
    reporting_schema: str = "unknown"
    active_search_path: str = "unknown"
    legacy_reporting_table: Optional[str] = None
    legacy_table_exists: bool
    legacy_parity: LegacyParityOut

    transactions_total: int
    outflow_count: int
    inflow_count: int
    gross_outflow_amount: float
    gross_inflow_amount: float
    internal_transfer_count: int

    true_spend_ops: int
    true_income_ops: int
    true_spend_amount: float
    true_income_amount: float

    auto_links: int
    suggested_links: int
    confirmed_links: int
    rejected_links: int
    unique_tx_in_suggested_links: int
    suggested_outflow_amount: float
    suggested_inflow_amount: float
    unresolved_transfer_net_impact: float
    unresolved_transfer_gross_impact: float
    orphan_link_rows: int
    reconciliation_mismatch_statements: int = 0
    orphan_statement_link_rows: int = 0
    statement_links_missing_transaction: int = 0
    statement_links_missing_row: int = 0
    unlinked_statement_rows: int = 0
    unlinked_transactions: int = 0
    rls_disabled_public_tables: int = 0
    rls_disabled_public_table_samples: List[str] = Field(default_factory=list)
    functions_without_explicit_search_path: int = 0
    functions_without_explicit_search_path_samples: List[str] = Field(default_factory=list)

    legacy_transactions_total: Optional[int] = None
    legacy_outflow_count: Optional[int] = None
    legacy_inflow_count: Optional[int] = None
    legacy_outflow_sum: Optional[float] = None
    legacy_inflow_sum: Optional[float] = None


class AnalyticsMonthlyPointOut(BaseModel):
    period: str
    inflow: float
    outflow: float
    net: float
    tx_count: int


class AnalyticsMonthlyFlowOut(BaseModel):
    generated_at: datetime
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    currency: Optional[str] = None
    account_id: Optional[str] = None
    include_transfers: bool = False
    cashflow_lens: str = "internal_only"
    items: List[AnalyticsMonthlyPointOut]


class AnalyticsSpendMixItemOut(BaseModel):
    category: str
    spent: float
    tx_count: int


class AnalyticsSpendMixOut(BaseModel):
    generated_at: datetime
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    currency: Optional[str] = None
    account_id: Optional[str] = None
    include_transfers: bool = False
    cashflow_lens: str = "internal_only"
    limit: int
    items: List[AnalyticsSpendMixItemOut]


class AnalyticsIncomeBreakdownItemOut(BaseModel):
    income_bucket: str
    income: float
    tx_count: int


class AnalyticsIncomeBreakdownOut(BaseModel):
    generated_at: datetime
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    currency: Optional[str] = None
    account_id: Optional[str] = None
    include_transfers: bool = False
    cashflow_lens: str = "internal_only"
    items: List[AnalyticsIncomeBreakdownItemOut]


class AnalyticsTopMerchantItemOut(BaseModel):
    merchant: str
    spent: float
    tx_count: int


class AnalyticsTopMerchantsOut(BaseModel):
    generated_at: datetime
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    currency: Optional[str] = None
    account_id: Optional[str] = None
    include_transfers: bool = False
    cashflow_lens: str = "internal_only"
    limit: int
    items: List[AnalyticsTopMerchantItemOut]


class RuleCreate(BaseModel):
    name: str
    pattern: str
    conditions: Optional[dict[str, Any]] = None
    actions: Optional[dict[str, Any]] = None
    priority: int = 100
    enabled: bool = True


class RuleOut(RuleCreate):
    id: str

    model_config = ConfigDict(from_attributes=True)


class RuleApplicationSampleOut(BaseModel):
    transaction_id: str
    matched_rule_ids: List[str]
    before_category: str
    after_category: str
    before_tags: List[str]
    after_tags: List[str]
    before_meaning: str
    after_meaning: str
    before_review_status: str
    after_review_status: str


class RuleApplicationOut(BaseModel):
    transactions_scanned: int
    transactions_matched: int
    transactions_changed: int
    transactions_updated: int
    sample: List[RuleApplicationSampleOut]


class RuleApplyRequest(BaseModel):
    q: Optional[str] = None
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    direction: Optional[str] = None
    meaning: Optional[str] = None
    category: Optional[str] = None
    include_transfers: bool = False
    limit: int = Field(50000, ge=1, le=500000)
    offset: int = Field(0, ge=0)
    sample_limit: int = Field(20, ge=0, le=200)
    dry_run: bool = False


class ExceptionOut(BaseModel):
    id: str
    exception_type: str
    severity: str
    status: str
    entity_type: str
    entity_id: str
    rationale: str
    payload: Optional[dict[str, Any]] = None
    created_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ImportFileOut(BaseModel):
    id: str
    batch_id: Optional[str] = None
    file_name: str
    file_path: str
    file_hash: str
    status: str
    error_message: str
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ImportBatchOut(BaseModel):
    id: str
    source: str
    status: str
    summary: Optional[dict[str, Any]] = None
    created_at: Optional[datetime] = None
    files: List[ImportFileOut] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class DeleteImportFileOut(BaseModel):
    file_id: str
    batch_id: Optional[str] = None
    deleted_statements: int
    deleted_statement_rows: int
    deleted_transactions: int
    deleted_transfer_links: int
    deleted_balance_snapshots: int
    deleted_exceptions: int
    deleted_import_batch: bool
    deleted_disk_file: bool


class DeleteImportBatchOut(BaseModel):
    batch_id: str
    deleted_import_files: int
    deleted_statements: int
    deleted_statement_rows: int
    deleted_transactions: int
    deleted_transfer_links: int
    deleted_balance_snapshots: int
    deleted_exceptions: int
    deleted_disk_files: int


class DeleteStatementOut(BaseModel):
    statement_id: str
    deleted_statement_rows: int
    deleted_transactions: int
    deleted_transfer_links: int
    deleted_balance_snapshots: int
    deleted_exceptions: int


class PurgeDataOut(BaseModel):
    deleted_import_batches: int
    deleted_import_files: int
    deleted_statements: int
    deleted_statement_rows: int
    deleted_transactions: int
    deleted_transfer_links: int
    deleted_balance_snapshots: int
    deleted_exceptions: int
    deleted_rules: int
    deleted_accounts: int
    deleted_disk_files: int


class NetWorthCurrencyTotalOut(BaseModel):
    currency: str
    total_balance: float


class NetWorthAccountOut(BaseModel):
    account_id: str
    provider: str
    account_type: str
    display_name: str
    masked_identifier: str = ""
    currency: str
    balance: Optional[float] = None
    as_of: Optional[datetime] = None
    confidence: Optional[float] = None
    statement_id: Optional[str] = None


class NetWorthCurrentOut(BaseModel):
    generated_at: datetime
    totals: List[NetWorthCurrencyTotalOut]
    accounts: List[NetWorthAccountOut]


class NetWorthTimelinePointOut(BaseModel):
    timestamp: datetime
    total_balance: float
    accounts_total: int
    accounts_with_snapshot: int
    accounts_missing: int
    completeness: float


class NetWorthTimelineSeriesOut(BaseModel):
    currency: str
    points: List[NetWorthTimelinePointOut]


class NetWorthTimelineOut(BaseModel):
    generated_at: datetime
    granularity: str
    series: List[NetWorthTimelineSeriesOut]


class NetWorthRebuildOut(BaseModel):
    statements_scanned: int
    statements_linked: int
    accounts_created: int
    snapshots_created: int
    exceptions_created: int
