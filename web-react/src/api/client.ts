import { getRuntimeConfig } from '../app/runtimeConfig';

type QueryValue = string | number | boolean | null | undefined;

export interface ApiListResult<T> {
  items: T[];
  total?: number;
}

export interface TransactionSummary {
  account_id?: string | null;
  id: string;
  operation_datetime: string | null;
  posting_datetime: string | null;
  timestamp_precision?: string | null;
  amount: number;
  currency: string;
  direction: string | null;
  category: string | null;
  description_raw: string | null;
  merchant_normalized: string | null;
  bank_reference_id?: string | null;
  bank_category: string | null;
  tags: string[] | null;
  meaning: string | null;
  review_status: string | null;
  review_reasons?: string[] | null;
  needs_human_review?: boolean | null;
  source_statement_id?: string | null;
  source_page_number?: number | null;
  source_row_index?: number | null;
}

export interface AccountSummary {
  id: string;
  provider: string;
  account_type: string | null;
  display_name: string | null;
  masked_identifier: string | null;
}

export interface TransferLinkSummary {
  id: string;
  status: string;
  transaction_out_id: string;
  transaction_in_id: string;
  match_score: number | null;
  rationale: string | null;
  fee_amount?: number | null;
}

export interface ImportFileSummary {
  id: string;
  batch_id: string | null;
  file_name: string;
  file_path: string;
  file_hash: string;
  status: string;
  error_message: string;
  created_at: string | null;
}

export interface ImportBatchSummary {
  id: string;
  source: string;
  status: string;
  summary: Record<string, unknown> | null;
  created_at: string | null;
  files: ImportFileSummary[];
}

export interface StatementSummary {
  id: string;
  provider: string;
  account_id: string | null;
  account_display: string;
  statement_type: string;
  period_start: string | null;
  period_end: string | null;
  generated_at: string | null;
  currency: string;
  opening_balance: number | null;
  closing_balance: number | null;
  total_credits: number | null;
  total_debits: number | null;
  parse_confidence: number | null;
  reconcile_status: string;
  pdf_path: string;
  created_at: string | null;
}

export interface StatementRowSummary {
  id: string;
  statement_id: string;
  row_index: number;
  page_number: number;
  raw_text: string;
  amount: number | null;
  currency: string;
  direction: string;
  operation_date: string | null;
  posting_date: string | null;
  parse_confidence: number | null;
}

export interface RuleActionSample {
  transaction_id: string;
  matched_rule_ids: string[];
  before_category: string;
  after_category: string;
  before_tags: string[];
  after_tags: string[];
  before_meaning: string;
  after_meaning: string;
  before_review_status: string;
  after_review_status: string;
}

export interface RuleActionResponse {
  transactions_scanned: number;
  transactions_matched: number;
  transactions_changed: number;
  transactions_updated: number;
  sample: RuleActionSample[];
}

export interface RuleSummary {
  id: string;
  name: string;
  pattern: string;
  priority: number;
  enabled: boolean;
  actions?: Record<string, unknown> | null;
}

export interface ExceptionSummary {
  id: string;
  status: string;
  exception_type: string;
  severity: string;
  entity_type: string | null;
  entity_id: string | null;
  rationale: string;
  payload?: Record<string, unknown> | null;
}

export type ExceptionStatusFilter = 'all' | 'open' | 'resolved';

export interface NetWorthCurrentResponse {
  totals: Array<{ currency: string; total_balance: number }>;
  accounts: Array<{
    account_id: string;
    provider: string;
    account_type: string;
    display_name: string | null;
    masked_identifier: string | null;
    balance: number | null;
    currency: string | null;
    as_of: string | null;
  }>;
}

export interface NetWorthTimelineResponse {
  series: Array<{
    currency: string;
    points: Array<{
      timestamp: string;
      total_balance: number;
      accounts_total: number;
      accounts_with_snapshot: number;
      accounts_missing: number;
      completeness: number;
    }>;
  }>;
}

export interface AnalyticsMonthlyFlowResponse {
  generated_at: string;
  items: Array<{
    period: string;
    inflow: number;
    outflow: number;
    net: number;
    tx_count: number;
  }>;
}

export interface AnalyticsSpendMixResponse {
  generated_at: string;
  items: Array<{
    category: string;
    spent: number;
    tx_count: number;
  }>;
}

export interface AnalyticsTopMerchantsResponse {
  generated_at: string;
  items: Array<{
    merchant: string;
    spent: number;
    tx_count: number;
  }>;
}

export interface HealthResponse {
  status: string;
}

export interface LegacyParitySummary {
  status: string;
  transactions_total_delta: number;
  outflow_count_delta: number;
  inflow_count_delta: number;
  outflow_amount_delta: number;
  inflow_amount_delta: number;
  transactions_total_delta_pct?: number | null;
  outflow_count_delta_pct?: number | null;
  inflow_count_delta_pct?: number | null;
  outflow_amount_delta_pct?: number | null;
  inflow_amount_delta_pct?: number | null;
  legacy_outflow_sign?: string | null;
}

export interface MetricsQualitySummary {
  status: string;
  flags: string[];
  recommendations?: string[] | null;
}

export interface MetricsQualityResponse {
  generated_at: string;
  quality: MetricsQualitySummary;
  canonical_table_exists: boolean;
  canonical_reporting_table: string;
  reporting_schema: string;
  active_search_path: string;
  legacy_reporting_table?: string | null;
  legacy_table_exists: boolean;
  legacy_parity: LegacyParitySummary;
  transactions_total: number;
  outflow_count: number;
  inflow_count: number;
  gross_outflow_amount: number;
  gross_inflow_amount: number;
  internal_transfer_count: number;
  true_spend_ops: number;
  true_income_ops: number;
  true_spend_amount: number;
  true_income_amount: number;
  auto_links: number;
  suggested_links: number;
  confirmed_links: number;
  rejected_links: number;
  unique_tx_in_suggested_links: number;
  suggested_outflow_amount: number;
  suggested_inflow_amount: number;
  unresolved_transfer_net_impact: number;
  unresolved_transfer_gross_impact: number;
  orphan_link_rows: number;
  reconciliation_mismatch_statements: number;
  orphan_statement_link_rows: number;
  statement_links_missing_transaction: number;
  statement_links_missing_row: number;
  unlinked_statement_rows: number;
  unlinked_transactions: number;
  rls_disabled_public_tables: number;
  rls_disabled_public_table_samples: string[];
  functions_without_explicit_search_path: number;
  functions_without_explicit_search_path_samples: string[];
  legacy_transactions_total?: number | null;
  legacy_outflow_count?: number | null;
  legacy_inflow_count?: number | null;
  legacy_outflow_sum?: number | null;
  legacy_inflow_sum?: number | null;
}

export interface DeleteImportFileOut {
  file_id: string;
  batch_id: string | null;
  deleted_statements: number;
  deleted_statement_rows: number;
  deleted_transactions: number;
  deleted_transfer_links: number;
  deleted_balance_snapshots: number;
  deleted_exceptions: number;
  deleted_import_batch: boolean;
  deleted_disk_file: boolean;
}

export interface DeleteImportBatchOut {
  batch_id: string;
  deleted_import_files: number;
  deleted_statements: number;
  deleted_statement_rows: number;
  deleted_transactions: number;
  deleted_transfer_links: number;
  deleted_balance_snapshots: number;
  deleted_exceptions: number;
  deleted_disk_files: number;
}

export class ApiError extends Error {
  public readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  const { apiBase } = getRuntimeConfig();
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  const url = new URL(`${apiBase}${normalizedPath}`, window.location.origin);

  if (!query) {
    return url.toString();
  }

  Object.entries(query).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') {
      return;
    }
    url.searchParams.set(key, String(value));
  });

  return url.toString();
}

async function apiGet<T>(
  path: string,
  query?: Record<string, QueryValue>,
): Promise<T> {
  const { adminToken } = getRuntimeConfig();
  const headers = new Headers({
    Accept: 'application/json',
  });

  if (adminToken) {
    headers.set('X-Admin-Token', adminToken);
  }

  const response = await fetch(buildUrl(path, query), {
    method: 'GET',
    headers,
  });

  if (!response.ok) {
    throw new ApiError(`API request failed (${response.status})`, response.status);
  }

  return (await response.json()) as T;
}

async function apiUpload<T>(path: string, formData: FormData): Promise<T> {
  const { adminToken } = getRuntimeConfig();
  const headers = new Headers({ Accept: 'application/json' });
  if (adminToken) {
    headers.set('X-Admin-Token', adminToken);
  }
  const response = await fetch(buildUrl(path), { method: 'POST', headers, body: formData });
  if (!response.ok) {
    throw new ApiError(`Upload failed (${response.status})`, response.status);
  }
  return (await response.json()) as T;
}

async function apiDelete<T>(path: string): Promise<T> {
  const { adminToken } = getRuntimeConfig();
  const headers = new Headers({ Accept: 'application/json' });
  if (adminToken) {
    headers.set('X-Admin-Token', adminToken);
  }
  const response = await fetch(buildUrl(path), { method: 'DELETE', headers });
  if (!response.ok) {
    throw new ApiError(`Delete failed (${response.status})`, response.status);
  }
  return (await response.json()) as T;
}

async function apiPost<T>(
  path: string,
  body?: unknown,
  query?: Record<string, QueryValue>,
): Promise<T> {
  const { adminToken } = getRuntimeConfig();
  const headers = new Headers({
    Accept: 'application/json',
  });

  if (adminToken) {
    headers.set('X-Admin-Token', adminToken);
  }

  let payload: string | undefined;
  if (body !== undefined) {
    headers.set('Content-Type', 'application/json');
    payload = JSON.stringify(body);
  }

  const response = await fetch(buildUrl(path, query), {
    method: 'POST',
    headers,
    body: payload,
  });

  if (!response.ok) {
    throw new ApiError(`API request failed (${response.status})`, response.status);
  }

  return (await response.json()) as T;
}

export const apiClient = {
  health: (): Promise<HealthResponse> => apiGet<HealthResponse>('/health'),

  metricsQuality: (): Promise<MetricsQualityResponse> =>
    apiGet<MetricsQualityResponse>('/metrics/quality'),

  accounts: (): Promise<AccountSummary[]> => apiGet<AccountSummary[]>('/accounts/'),

  transactions: (query?: Record<string, QueryValue>): Promise<ApiListResult<TransactionSummary>> =>
    apiGet<ApiListResult<TransactionSummary>>('/transactions/', query),

  transactionById: (transactionId: string): Promise<TransactionSummary> =>
    apiGet<TransactionSummary>(`/transactions/${transactionId}`),

  importBatches: (
    query?: Record<string, QueryValue>,
  ): Promise<ImportBatchSummary[]> =>
    apiGet<ImportBatchSummary[]>('/imports/batches', query),

  uploadStatements: (files: FileList | File[]): Promise<ImportBatchSummary> => {
    const form = new FormData();
    Array.from(files).forEach((file) => form.append('files', file));
    form.append('source', 'web');
    return apiUpload<ImportBatchSummary>('/imports/pdf', form);
  },

  reprocessBatch: (batchId: string): Promise<ImportBatchSummary> =>
    apiPost<ImportBatchSummary>(`/imports/batches/${batchId}/reprocess`),

  deleteBatch: (batchId: string): Promise<DeleteImportBatchOut> =>
    apiDelete<DeleteImportBatchOut>(`/imports/batches/${batchId}`),

  deleteImportFile: (fileId: string): Promise<DeleteImportFileOut> =>
    apiDelete<DeleteImportFileOut>(`/imports/files/${fileId}`),

  statements: (): Promise<StatementSummary[]> => apiGet<StatementSummary[]>('/statements/'),

  statementRows: (
    statementId: string,
    query?: Record<string, QueryValue>,
  ): Promise<StatementRowSummary[]> =>
    apiGet<StatementRowSummary[]>(`/statements/${statementId}/rows`, query),

  transferLinks: (query?: Record<string, QueryValue>): Promise<TransferLinkSummary[]> =>
    apiGet<TransferLinkSummary[]>('/transfers/links', query),

  confirmTransferLink: (linkId: string): Promise<TransferLinkSummary> =>
    apiPost<TransferLinkSummary>(`/transfers/links/${linkId}/confirm`),

  rejectTransferLink: (linkId: string): Promise<TransferLinkSummary> =>
    apiPost<TransferLinkSummary>(`/transfers/links/${linkId}/reject`),

  approveTransactionCategory: (transactionId: string): Promise<TransactionSummary> =>
    apiPost<TransactionSummary>(`/transactions/${transactionId}/approve-category`),

  markTransactionReviewed: (transactionId: string): Promise<TransactionSummary> =>
    apiPost<TransactionSummary>(`/transactions/${transactionId}/mark-reviewed`),

  markTransactionDuplicate: (transactionId: string): Promise<TransactionSummary> =>
    apiPost<TransactionSummary>(`/transactions/${transactionId}/mark-duplicate`),

  rules: (): Promise<RuleSummary[]> => apiGet<RuleSummary[]>('/rules/'),

  createRule: (payload: {
    name: string;
    pattern: string;
    conditions?: Record<string, unknown>;
    actions?: Record<string, unknown>;
    priority: number;
    enabled: boolean;
  }): Promise<RuleSummary> => apiPost<RuleSummary>('/rules/', payload),

  previewRules: (query?: Record<string, QueryValue>): Promise<RuleActionResponse> =>
    apiGet<RuleActionResponse>('/rules/preview', query),

  applyRules: (payload: Record<string, unknown>): Promise<RuleActionResponse> =>
    apiPost<RuleActionResponse>('/rules/apply', payload),

  exceptions: (
    query?: { status?: ExceptionStatusFilter },
  ): Promise<ExceptionSummary[]> => apiGet<ExceptionSummary[]>('/exceptions/', query),

  resolveException: (exceptionId: string): Promise<ExceptionSummary> =>
    apiPost<ExceptionSummary>(`/exceptions/${exceptionId}/resolve`),

  ignoreException: (exceptionId: string): Promise<ExceptionSummary> =>
    apiPost<ExceptionSummary>(`/exceptions/${exceptionId}/ignore`),

  approveExceptionCategory: (exceptionId: string): Promise<ExceptionSummary> =>
    apiPost<ExceptionSummary>(`/exceptions/${exceptionId}/approve-category`),

  markExceptionDuplicate: (exceptionId: string): Promise<ExceptionSummary> =>
    apiPost<ExceptionSummary>(`/exceptions/${exceptionId}/mark-duplicate`),

  netWorthCurrent: (query?: Record<string, QueryValue>): Promise<NetWorthCurrentResponse> =>
    apiGet<NetWorthCurrentResponse>('/networth/current', query),

  netWorthTimeline: (
    query?: Record<string, QueryValue>,
  ): Promise<NetWorthTimelineResponse> =>
    apiGet<NetWorthTimelineResponse>('/networth/timeline', query),

  monthlyFlow: (
    query?: Record<string, QueryValue>,
  ): Promise<AnalyticsMonthlyFlowResponse> =>
    apiGet<AnalyticsMonthlyFlowResponse>('/analytics/monthly-flow', query),

  spendMix: (
    query?: Record<string, QueryValue>,
  ): Promise<AnalyticsSpendMixResponse> =>
    apiGet<AnalyticsSpendMixResponse>('/analytics/spend-mix', query),

  topMerchants: (
    query?: Record<string, QueryValue>,
  ): Promise<AnalyticsTopMerchantsResponse> =>
    apiGet<AnalyticsTopMerchantsResponse>('/analytics/top-merchants', query),
};
