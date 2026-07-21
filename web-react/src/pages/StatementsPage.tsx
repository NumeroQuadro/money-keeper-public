import { Fragment, useEffect, useRef, useState } from 'react';
import { formatCount, formatDate, formatMoney } from '../app/formatters';
import {
  apiClient,
  type ImportBatchSummary,
  type StatementRowSummary,
  type StatementSummary,
} from '../api/client';
import './statements.css';

const ROW_FETCH_LIMIT = 150;

function statementPeriodLabel(statement: StatementSummary): string {
  if (!statement.period_start) {
    return '—';
  }

  return `${formatDate(statement.period_start)} - ${formatDate(statement.period_end)}`;
}

function reconcileLabel(status: string): string {
  if (status === 'ok' || status === 'reconciled') return 'Reconciled';
  if (status === 'mismatch') return 'Mismatch';
  if (status === 'pending') return 'Pending';
  return status || 'Unknown';
}

function reconcileTone(status: string): string {
  if (status === 'ok' || status === 'reconciled') return 'tone-positive';
  if (status === 'mismatch') return 'tone-danger';
  return 'tone-muted';
}

function fileStatusLabel(status: string): string {
  if (status === 'processed') return 'Processed';
  if (status === 'duplicate') return 'Duplicate';
  if (status === 'failed') return 'Failed';
  if (status === 'queued') return 'Queued';
  return status;
}

function fileStatusTone(status: string): string {
  if (status === 'processed') return 'tone-positive';
  if (status === 'duplicate') return 'tone-warning';
  if (status === 'failed') return 'tone-danger';
  return 'tone-muted';
}

function batchFileSummary(batch: ImportBatchSummary): string {
  const processed = batch.files.filter((f) => f.status === 'processed').length;
  const failed = batch.files.filter((f) => f.status === 'failed').length;
  const duplicates = batch.files.filter((f) => f.status === 'duplicate').length;
  const queued = batch.files.filter((f) => f.status === 'queued').length;
  const parts: string[] = [];
  if (processed) parts.push(`${processed} processed`);
  if (queued) parts.push(`${queued} queued`);
  if (duplicates) parts.push(`${duplicates} duplicate`);
  if (failed) parts.push(`${failed} failed`);
  return parts.join(', ') || 'No files';
}

function formatConfidence(value: number | null): string {
  if (value === null) return '—';
  return `${Math.round(value * 100)}%`;
}

function confidenceTone(value: number | null): string {
  if (value === null) return 'tone-muted';
  if (value >= 0.9) return 'tone-positive';
  if (value >= 0.75) return 'tone-warning';
  return 'tone-danger';
}

export function StatementsPage() {
  const [batches, setBatches] = useState<ImportBatchSummary[]>([]);
  const [statements, setStatements] = useState<StatementSummary[]>([]);
  const [expandedBatchIds, setExpandedBatchIds] = useState<Set<string>>(new Set());
  const [expandedStatementId, setExpandedStatementId] = useState<string | null>(null);
  const [statementRows, setStatementRows] = useState<Record<string, StatementRowSummary[]>>({});
  const [loadingRowsFor, setLoadingRowsFor] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [uploadPhase, setUploadPhase] = useState<'idle' | 'uploading' | 'done' | 'error'>('idle');
  const [uploadMessage, setUploadMessage] = useState('');
  const [actionError, setActionError] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);

  const loadAll = async () => {
    setIsLoading(true);
    setActionError('');
    try {
      const [batchResult, stmtResult] = await Promise.all([
        apiClient.importBatches({ limit: 30 }),
        apiClient.statements(),
      ]);
      setBatches(batchResult);
      setStatements(stmtResult);
    } catch {
      setActionError('Failed to load data. Check your connection and try refreshing.');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    void loadAll();
  }, []);

  const toggleBatch = (id: string) => {
    setExpandedBatchIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleStatementRows = async (id: string) => {
    if (expandedStatementId === id) {
      setExpandedStatementId(null);
      return;
    }
    setExpandedStatementId(id);
    if (statementRows[id]) return;
    setLoadingRowsFor(id);
    try {
      const result = await apiClient.statementRows(id, { limit: ROW_FETCH_LIMIT, offset: 0 });
      setStatementRows((prev) => ({ ...prev, [id]: result }));
    } catch {
      setStatementRows((prev) => ({ ...prev, [id]: [] }));
    } finally {
      setLoadingRowsFor(null);
    }
  };

  const handleUpload = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    if (fileInputRef.current) fileInputRef.current.value = '';
    setUploadPhase('uploading');
    setUploadMessage('');
    try {
      const batch = await apiClient.uploadStatements(files);
      const summary = batch.summary as Record<string, number> | null;
      const queued = summary?.files_queued ?? 0;
      const dupes = summary?.duplicates ?? 0;
      const failed = summary?.failed ?? 0;
      const parts: string[] = [];
      if (queued) parts.push(`${queued} queued for processing`);
      if (dupes) parts.push(`${dupes} duplicate`);
      if (failed) parts.push(`${failed} failed`);
      setUploadMessage(parts.join(', ') || 'Upload received.');
      setUploadPhase('done');
      void loadAll();
    } catch {
      setUploadPhase('error');
      setUploadMessage('Upload failed. Ensure the file is a valid PDF and try again.');
    }
  };

  const handleReprocess = async (batchId: string) => {
    setActionError('');
    try {
      const updated = await apiClient.reprocessBatch(batchId);
      setBatches((prev) => prev.map((b) => (b.id === updated.id ? updated : b)));
    } catch {
      setActionError('Reprocess request failed.');
    }
  };

  const handleDeleteBatch = async (batchId: string) => {
    if (!window.confirm('Delete this import batch? This will remove all associated statements and transactions.')) return;
    setActionError('');
    try {
      await apiClient.deleteBatch(batchId);
      setBatches((prev) => prev.filter((b) => b.id !== batchId));
      void loadAll();
    } catch {
      setActionError('Delete failed.');
    }
  };

  const mismatchCount = statements.filter((s) => s.reconcile_status === 'mismatch').length;
  const allFiles = batches.flatMap((b) => b.files);
  const failedFileCount = allFiles.filter((f) => f.status === 'failed').length;
  const duplicateFileCount = allFiles.filter((f) => f.status === 'duplicate').length;
  const showHealthStrip = statements.length > 0 || batches.length > 0;

  return (
    <section className="stmts-page fade-in">
      <article className="ledger-document stmts-document">
        <header className="ledger-doc-head">
          <div className="ledger-section-copy">
            <h2>Statements</h2>
            <span className="ledger-note">Document register for imported PDFs and reconciliation status.</span>
          </div>
          <span className="ledger-doc-sub">Folio V · {formatCount(statements.length)} documents on file</span>
        </header>

        <section className="stmts-deposit">
          <div className="stmts-deposit-copy">
            <span className="ledger-meta">Deposit area</span>
            <h3>Drop PDF statements to file</h3>
            <p>Providers supported: Ozon · Sber · Yandex · SPB</p>
          </div>
          <div className="stmts-header-actions">
            <input
              ref={fileInputRef}
              type="file"
              accept="application/pdf"
              multiple
              className="stmts-file-input"
              onChange={(e) => void handleUpload(e.target.files)}
            />
            <button
              type="button"
              className="stmts-btn-primary"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploadPhase === 'uploading'}
            >
              {uploadPhase === 'uploading' ? 'Uploading…' : 'Upload PDF'}
            </button>
            <button
              type="button"
              className="stmts-btn-secondary"
              onClick={() => void loadAll()}
              disabled={isLoading}
            >
              Refresh
            </button>
          </div>
        </section>

        {uploadPhase !== 'idle' && uploadMessage ? (
          <div className={`stmts-banner ${uploadPhase === 'error' ? 'stmts-banner--error' : 'stmts-banner--ok'}`}>
            <span>{uploadMessage}</span>
            <button
              type="button"
              className="stmts-banner-dismiss"
              onClick={() => {
                setUploadPhase('idle');
                setUploadMessage('');
              }}
            >
              ×
            </button>
          </div>
        ) : null}

        {actionError ? (
          <div className="stmts-banner stmts-banner--error">
            <span>{actionError}</span>
            <button type="button" className="stmts-banner-dismiss" onClick={() => setActionError('')}>
              ×
            </button>
          </div>
        ) : null}

        {showHealthStrip ? (
          <div className="stmts-health-strip">
            <span className="stmts-health-item">
              <strong>{formatCount(statements.length)}</strong> statements
            </span>
            <span className="stmts-health-item">
              <strong>{formatCount(batches.length)}</strong> import batches
            </span>
            {mismatchCount > 0 ? (
              <span className="stmts-health-item stmts-health-item--danger">
                <strong>{formatCount(mismatchCount)}</strong>{' '}
                reconciliation mismatch{mismatchCount !== 1 ? 'es' : ''}
              </span>
            ) : null}
            {failedFileCount > 0 ? (
              <span className="stmts-health-item stmts-health-item--danger">
                <strong>{formatCount(failedFileCount)}</strong>{' '}
                failed file{failedFileCount !== 1 ? 's' : ''}
              </span>
            ) : null}
            {duplicateFileCount > 0 ? (
              <span className="stmts-health-item stmts-health-item--warning">
                <strong>{formatCount(duplicateFileCount)}</strong>{' '}
                duplicate{duplicateFileCount !== 1 ? 's' : ''}
              </span>
            ) : null}
          </div>
        ) : null}

        <article className="ledger-panel stmts-section">
          <header className="stmts-section-header">
            <h3>Import batches</h3>
            <span>{formatCount(batches.length)} batches</span>
          </header>

          {isLoading && !batches.length ? <div className="stmts-empty">Loading…</div> : null}

          {!isLoading && !batches.length ? (
            <div className="stmts-empty">No import batches yet. Upload a PDF statement to start.</div>
          ) : null}

          <div className="stmts-batches">
            {batches.map((batch) => {
              const isExpanded = expandedBatchIds.has(batch.id);
              const hasFailed = batch.files.some((f) => f.status === 'failed');
              const hasDuplicate = batch.files.some((f) => f.status === 'duplicate');
              return (
                <div key={batch.id} className="stmts-batch">
                  <div className="stmts-batch-row">
                    <button
                      type="button"
                      className="stmts-batch-toggle"
                      onClick={() => toggleBatch(batch.id)}
                    >
                      <span className="stmts-batch-chevron">{isExpanded ? '▾' : '▸'}</span>
                      <span className="stmts-batch-date">{formatDate(batch.created_at)}</span>
                      <span className="stmts-badge tone-muted">{batch.source}</span>
                      <span className="stmts-batch-summary">{batchFileSummary(batch)}</span>
                      {hasFailed ? <span className="stmts-badge tone-danger">has failures</span> : null}
                      {hasDuplicate ? <span className="stmts-badge tone-warning">has duplicates</span> : null}
                    </button>
                    <div className="stmts-batch-actions">
                      {hasFailed ? (
                        <button
                          type="button"
                          className="stmts-action-link"
                          onClick={() => void handleReprocess(batch.id)}
                        >
                          Reprocess
                        </button>
                      ) : null}
                      <button
                        type="button"
                        className="stmts-action-link stmts-action-link--danger"
                        onClick={() => void handleDeleteBatch(batch.id)}
                      >
                        Delete
                      </button>
                    </div>
                  </div>

                  {isExpanded ? (
                    <div className="stmts-batch-files">
                      {batch.files.map((file) => (
                        <div key={file.id} className="stmts-batch-file">
                          <span className="stmts-batch-filename">{file.file_name}</span>
                          <span className={`stmts-badge ${fileStatusTone(file.status)}`}>
                            {fileStatusLabel(file.status)}
                          </span>
                          {file.error_message ? <span className="stmts-file-error">{file.error_message}</span> : null}
                          <span className="stmts-batch-file-date">{formatDate(file.created_at)}</span>
                        </div>
                      ))}
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
        </article>

        <article className="ledger-panel stmts-section">
          <header className="stmts-section-header">
            <h3>Document register</h3>
            <span>{formatCount(statements.length)} loaded</span>
          </header>

          {isLoading && !statements.length ? <div className="stmts-empty">Loading statements…</div> : null}

          {!isLoading && !statements.length ? (
            <div className="stmts-empty">No statements loaded. Import a PDF statement to begin.</div>
          ) : null}

          {statements.length > 0 ? (
            <>
              <div className="stmts-table-wrap">
                <table className="stmts-table">
                  <thead>
                    <tr>
                      <th scope="col">Provider / Account</th>
                      <th scope="col">Period</th>
                      <th scope="col">Ccy</th>
                      <th scope="col">Opening</th>
                      <th scope="col">Closing</th>
                      <th scope="col">Credits / Debits</th>
                      <th scope="col">Reconcile</th>
                      <th scope="col">Confidence</th>
                      <th scope="col">Rows</th>
                    </tr>
                  </thead>
                  <tbody>
                    {statements.map((stmt) => {
                      const isExpanded = expandedStatementId === stmt.id;
                      const rows = statementRows[stmt.id] ?? [];
                      return (
                        <Fragment key={stmt.id}>
                          <tr className={`stmts-row${isExpanded ? ' stmts-row--expanded' : ''}`}>
                            <td>
                              <strong>{stmt.provider.toUpperCase()}</strong>
                              <div className="stmts-row-sub">{stmt.account_display || 'Account unavailable'}</div>
                              <div className="stmts-row-sub">{stmt.statement_type}</div>
                            </td>
                            <td>
                              {statementPeriodLabel(stmt)}
                              <div className="stmts-row-sub">Added {formatDate(stmt.created_at)}</div>
                            </td>
                            <td>{stmt.currency}</td>
                            <td>{formatMoney(stmt.opening_balance)}</td>
                            <td>{formatMoney(stmt.closing_balance)}</td>
                            <td>
                              {stmt.total_credits !== null ? (
                                <span className="tone-positive">+{formatMoney(stmt.total_credits)}</span>
                              ) : (
                                '—'
                              )}
                              {' / '}
                              {stmt.total_debits !== null ? (
                                <span className="tone-negative">{formatMoney(stmt.total_debits)}</span>
                              ) : (
                                '—'
                              )}
                            </td>
                            <td>
                              <span className={`stmts-badge ${reconcileTone(stmt.reconcile_status)}`}>
                                {reconcileLabel(stmt.reconcile_status)}
                              </span>
                            </td>
                            <td>
                              <span className={confidenceTone(stmt.parse_confidence)}>
                                {formatConfidence(stmt.parse_confidence)}
                              </span>
                            </td>
                            <td>
                              <button
                                type="button"
                                className="stmts-action-link"
                                onClick={() => void toggleStatementRows(stmt.id)}
                              >
                                {isExpanded ? 'Hide' : 'Show'}
                              </button>
                            </td>
                          </tr>
                          {isExpanded ? (
                            <tr className="stmts-row-detail">
                              <td colSpan={9}>
                                {loadingRowsFor === stmt.id ? (
                                  <div className="stmts-empty">Loading rows…</div>
                                ) : rows.length === 0 ? (
                                  <div className="stmts-empty">No rows available for this statement.</div>
                                ) : (
                                  <div className="stmts-sub-table-wrap">
                                    <table className="stmts-sub-table" aria-label="Statement rows">
                                      <thead>
                                        <tr>
                                          <th scope="col">Row</th>
                                          <th scope="col">Page</th>
                                          <th scope="col">Date</th>
                                          <th scope="col">Dir</th>
                                          <th scope="col">Amount</th>
                                          <th scope="col">Raw text</th>
                                          <th scope="col">Conf.</th>
                                        </tr>
                                      </thead>
                                      <tbody>
                                        {rows.map((row) => (
                                          <tr key={row.id}>
                                            <td>{row.row_index}</td>
                                            <td>{row.page_number}</td>
                                            <td>{formatDate(row.operation_date ?? row.posting_date)}</td>
                                            <td>{row.direction || '—'}</td>
                                            <td>{formatMoney(row.amount)}</td>
                                            <td className="stmts-sub-raw">{row.raw_text || '—'}</td>
                                            <td>
                                              <span className={confidenceTone(row.parse_confidence)}>
                                                {formatConfidence(row.parse_confidence)}
                                              </span>
                                            </td>
                                          </tr>
                                        ))}
                                      </tbody>
                                    </table>
                                  </div>
                                )}
                              </td>
                            </tr>
                          ) : null}
                        </Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              <div className="stmts-mobile-list">
                {statements.map((stmt) => {
                  const isExpanded = expandedStatementId === stmt.id;
                  const rows = statementRows[stmt.id] ?? [];
                  return (
                    <article
                      key={stmt.id}
                      className={isExpanded ? 'stmts-mobile-card is-expanded' : 'stmts-mobile-card'}
                    >
                      <div className="stmts-mobile-head">
                        <div className="stmts-mobile-heading">
                          <strong>{stmt.provider.toUpperCase()}</strong>
                          <p>{stmt.account_display || 'Account unavailable'}</p>
                          <span>{stmt.statement_type}</span>
                        </div>
                        <span className={`stmts-badge ${reconcileTone(stmt.reconcile_status)}`}>
                          {reconcileLabel(stmt.reconcile_status)}
                        </span>
                      </div>

                      <dl className="stmts-mobile-facts">
                        <div>
                          <dt>Period</dt>
                          <dd>{statementPeriodLabel(stmt)}</dd>
                        </div>
                        <div>
                          <dt>Currency</dt>
                          <dd>{stmt.currency}</dd>
                        </div>
                        <div>
                          <dt>Opening</dt>
                          <dd>{formatMoney(stmt.opening_balance)}</dd>
                        </div>
                        <div>
                          <dt>Closing</dt>
                          <dd>{formatMoney(stmt.closing_balance)}</dd>
                        </div>
                        <div>
                          <dt>Credits / Debits</dt>
                          <dd>
                            {stmt.total_credits !== null ? `+${formatMoney(stmt.total_credits)}` : '—'}
                            {' / '}
                            {stmt.total_debits !== null ? formatMoney(stmt.total_debits) : '—'}
                          </dd>
                        </div>
                        <div>
                          <dt>Confidence</dt>
                          <dd className={confidenceTone(stmt.parse_confidence)}>
                            {formatConfidence(stmt.parse_confidence)}
                          </dd>
                        </div>
                      </dl>

                      <div className="stmts-mobile-actions">
                        <span className="stmts-row-sub">Added {formatDate(stmt.created_at)}</span>
                        <button
                          type="button"
                          className="stmts-action-link"
                          onClick={() => void toggleStatementRows(stmt.id)}
                        >
                          {isExpanded ? 'Hide rows' : 'Show rows'}
                        </button>
                      </div>

                      {isExpanded ? (
                        <div className="stmts-mobile-rows">
                          {loadingRowsFor === stmt.id ? (
                            <div className="stmts-empty">Loading rows…</div>
                          ) : rows.length === 0 ? (
                            <div className="stmts-empty">No rows available for this statement.</div>
                          ) : (
                            rows.map((row) => (
                              <div key={row.id} className="stmts-mobile-row">
                                <div className="stmts-mobile-row-topline">
                                  <strong>
                                    Row {row.row_index}
                                    {row.page_number ? ` · Page ${row.page_number}` : ''}
                                  </strong>
                                  <span>{formatMoney(row.amount)}</span>
                                </div>
                                <p>{row.raw_text || '—'}</p>
                                <div className="stmts-mobile-row-meta">
                                  <span>{formatDate(row.operation_date ?? row.posting_date)}</span>
                                  <span>{row.direction || '—'}</span>
                                  <span className={confidenceTone(row.parse_confidence)}>
                                    {formatConfidence(row.parse_confidence)}
                                  </span>
                                </div>
                              </div>
                            ))
                          )}
                        </div>
                      ) : null}
                    </article>
                  );
                })}
              </div>
            </>
          ) : null}
        </article>
      </article>
    </section>
  );
}
