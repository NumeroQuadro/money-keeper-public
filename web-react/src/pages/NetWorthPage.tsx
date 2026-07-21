import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { formatDate, formatMoney } from '../app/formatters';
import { getProviderTone } from '../app/financePresentation';
import { apiClient, type NetWorthCurrentResponse, type NetWorthTimelineResponse } from '../api/client';
import './networth.css';

type Granularity = 'raw' | 'week' | 'month';
type TimelinePoint = NetWorthTimelineResponse['series'][number]['points'][number];
type AccountRow = NetWorthCurrentResponse['accounts'][number];

const GRAN_OPTIONS: Array<{ value: Granularity; label: string }> = [
  { value: 'raw', label: 'By statement' },
  { value: 'week', label: 'Weekly' },
  { value: 'month', label: 'Monthly' },
];

const STALE_ACCOUNT_DAYS = 30;

function maskIdentifier(value: string | null | undefined): string {
  if (!value) return '';
  const digits = String(value).replace(/\D/g, '');
  if (!digits) return '';
  return `**** ${digits.slice(-4)}`;
}

function shortDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short' });
}

function parseDate(value: string | null | undefined): Date | null {
  if (!value) {
    return null;
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return null;
  }

  return date;
}

function formatSignedMoney(value: number | null): string {
  if (value === null || !Number.isFinite(value)) {
    return '—';
  }

  const abs = formatMoney(Math.abs(value));
  if (value > 0) {
    return `+${abs}`;
  }

  if (value < 0) {
    return `-${abs}`;
  }

  return abs;
}

function formatShare(value: number | null): string {
  if (value === null || !Number.isFinite(value)) {
    return '—';
  }

  return `${(value * 100).toFixed(1)}%`;
}

function isAccountStale(asOf: string | null | undefined): boolean {
  const date = parseDate(asOf);
  if (!date) {
    return true;
  }

  const ageMs = Date.now() - date.getTime();
  const ageDays = ageMs / (1000 * 60 * 60 * 24);
  return ageDays > STALE_ACCOUNT_DAYS;
}

function accountIdentity(account: AccountRow): string {
  return account.masked_identifier || maskIdentifier(account.display_name) || '—';
}

function accountDescriptor(account: AccountRow): string {
  return [account.provider, account.account_type].filter(Boolean).join(' · ') || 'account';
}

function BalanceChart({ points }: { points: TimelinePoint[] }) {
  const W = 560;
  const H = 160;
  const PL = 4;
  const PR = 4;
  const PT = 12;
  const PB = 26;
  const cw = W - PL - PR;
  const ch = H - PT - PB;

  if (points.length < 2) {
    return <p className="accts-chart-empty">Not enough data to plot a chart.</p>;
  }

  const vals = points.map((p) => p.total_balance);
  const minV = Math.min(...vals);
  const maxV = Math.max(...vals);
  const range = maxV - minV || 1;

  const toX = (i: number) => PL + (i / (points.length - 1)) * cw;
  const toY = (v: number) => PT + ch - ((v - minV) / range) * ch;

  const linePts = points.map((p, i) => `${toX(i)},${toY(p.total_balance)}`).join(' ');
  const areaPts = [
    `${PL},${PT + ch}`,
    ...points.map((p, i) => `${toX(i)},${toY(p.total_balance)}`),
    `${toX(points.length - 1)},${PT + ch}`,
  ].join(' ');

  const step = Math.max(1, Math.ceil((points.length - 1) / 5));
  const labelIdxs: number[] = [];
  for (let i = 0; i < points.length; i += step) labelIdxs.push(i);
  if (labelIdxs[labelIdxs.length - 1] !== points.length - 1) {
    labelIdxs.push(points.length - 1);
  }

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="accts-chart-svg"
      aria-label="Balance over time"
      role="img"
    >
      <polygon points={areaPts} className="accts-chart-area" />
      <polyline points={linePts} className="accts-chart-line" />
      {points.map((p, i) => (
        <circle
          key={`${p.timestamp}-${i}`}
          cx={toX(i)}
          cy={toY(p.total_balance)}
          r="2.5"
          className="accts-chart-dot"
        />
      ))}
      {labelIdxs.map((i) => (
        <text
          key={i}
          x={toX(i)}
          y={H - 4}
          className="accts-chart-label"
          textAnchor={i === 0 ? 'start' : i === points.length - 1 ? 'end' : 'middle'}
        >
          {shortDate(points[i].timestamp)}
        </text>
      ))}
    </svg>
  );
}

function AccountsPage() {
  const navigate = useNavigate();
  const [granularity, setGranularity] = useState<Granularity>('raw');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [showDateRange, setShowDateRange] = useState(false);

  const [totals, setTotals] = useState<NetWorthCurrentResponse['totals']>([]);
  const [accounts, setAccounts] = useState<NetWorthCurrentResponse['accounts']>([]);
  const [points, setPoints] = useState<TimelinePoint[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');

  const load = async (gran: Granularity, s: string, e: string) => {
    setIsLoading(true);
    setError('');
    try {
      const [current, timeline] = await Promise.all([
        apiClient.netWorthCurrent(),
        apiClient.netWorthTimeline({
          granularity: gran,
          start: s || undefined,
          end: e || undefined,
        }),
      ]);
      setTotals(current.totals || []);
      setAccounts(current.accounts || []);
      setPoints(timeline.series?.[0]?.points || []);
    } catch {
      setTotals([]);
      setAccounts([]);
      setPoints([]);
      setError('Balance data is temporarily unavailable.');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    void load('raw', '', '');
  }, []);

  const latestPoint = points.at(-1) ?? null;
  const priorPoint = points.length > 1 ? points[points.length - 2] : null;
  const totalBalance = totals[0]?.total_balance ?? latestPoint?.total_balance ?? null;
  const totalLabel =
    totalBalance !== null
      ? formatMoney(totalBalance)
      : totals.length
        ? totals.map((t) => formatMoney(t.total_balance)).join(' · ')
        : '—';
  const asOfLabel = latestPoint?.timestamp ? formatDate(latestPoint.timestamp) : '—';
  const coverageLabel = latestPoint?.accounts_total
    ? `${latestPoint.accounts_with_snapshot ?? 0}/${latestPoint.accounts_total} accounts`
    : `${accounts.length} accounts`;
  const freshnessStaleCount = accounts.filter((account) => isAccountStale(account.as_of)).length;
  const freshnessLabel = freshnessStaleCount
    ? `${freshnessStaleCount} stale statement${freshnessStaleCount === 1 ? '' : 's'}`
    : 'All statement dates are current';
  const movementLabel =
    latestPoint && priorPoint
      ? formatSignedMoney(latestPoint.total_balance - priorPoint.total_balance)
      : null;
  const movementTone = movementLabel
    ? movementLabel.startsWith('-')
      ? 'is-negative'
      : 'is-positive'
    : '';
  const shareBase =
    totalBalance !== null && Math.abs(totalBalance) > 0
      ? Math.abs(totalBalance)
      : accounts.reduce((sum, account) => sum + Math.abs(account.balance ?? 0), 0);

  return (
    <section className="accts-view fade-in">
      <article className="ledger-document accts-document">
        <header className="ledger-doc-head">
          <div className="ledger-section-copy">
            <h2>Accounts</h2>
            <span className="ledger-note">
              Consolidated cash position and statement freshness by account.
            </span>
          </div>
          <span className="ledger-doc-sub">Folio IV · As of {asOfLabel}</span>
        </header>

        {error ? (
          <div className="accts-error" role="alert">
            {error}
          </div>
        ) : null}

        <section className="ledger-kpi-strip accts-kpi-strip" aria-label="Account summary">
          <div className="ledger-kpi-cell">
            <span className="ledger-kpi-label">Total cash position</span>
            <strong className="ledger-kpi-value">{totalLabel}</strong>
            <span className="ledger-kpi-note">Snapshot as of {asOfLabel}</span>
          </div>
          <div className="ledger-kpi-cell">
            <span className="ledger-kpi-label">Change since prior</span>
            <strong className={`ledger-kpi-value ${movementTone}`.trim()}>
              {movementLabel || '—'}
            </strong>
            <span className="ledger-kpi-note">
              {movementLabel ? 'Latest movement in the selected timeline' : 'No prior snapshot in range'}
            </span>
          </div>
          <div className="ledger-kpi-cell">
            <span className="ledger-kpi-label">Accounts in scope</span>
            <strong className="ledger-kpi-value">{coverageLabel}</strong>
            <span className="ledger-kpi-note">Current balance coverage from imported statements</span>
          </div>
          <div className="ledger-kpi-cell">
            <span className="ledger-kpi-label">Statement freshness</span>
            <strong className="ledger-kpi-value">{freshnessStaleCount}</strong>
            <span className="ledger-kpi-note">{freshnessLabel}</span>
          </div>
        </section>

        <div className="accts-layout">
          <section className="ledger-panel accts-chart-panel" aria-label="Balance timeline">
            <div className="ledger-section-head accts-panel-head">
              <div className="ledger-section-copy">
                <h3>Balance exhibit</h3>
                <span className="ledger-section-note">Trend context for the selected range</span>
              </div>

              <div className="accts-chart-controls">
                <div className="accts-gran-group" role="group" aria-label="Granularity">
                  {GRAN_OPTIONS.map((opt) => (
                    <button
                      key={opt.value}
                      type="button"
                      className={`accts-gran-btn${granularity === opt.value ? ' is-active' : ''}`}
                      aria-pressed={granularity === opt.value}
                      onClick={() => setGranularity(opt.value)}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
                <button
                  type="button"
                  className="accts-apply-btn"
                  onClick={() => void load(granularity, start, end)}
                >
                  Apply
                </button>
              </div>
            </div>

            {isLoading ? <p className="accts-chart-empty">Loading…</p> : <BalanceChart points={points} />}

            <div className="accts-chart-footer">
              <button
                type="button"
                className="accts-period-toggle"
                onClick={() => setShowDateRange((value) => !value)}
              >
                {showDateRange ? 'Hide date range ↑' : 'Set date range ↓'}
              </button>
              <span className="ledger-meta">{points.length ? `${points.length} plotted points` : 'Awaiting statement snapshots'}</span>
            </div>

            {showDateRange ? (
              <div className="accts-date-range">
                <label>
                  <span>From</span>
                  <input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
                </label>
                <label>
                  <span>To</span>
                  <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
                </label>
              </div>
            ) : null}
          </section>

          <section className="ledger-panel accts-schedule-panel">
            <div className="ledger-section-head">
              <div className="ledger-section-copy">
                <h3>Account schedule</h3>
                <span className="ledger-section-note">Current balances prepared from the latest imported statements</span>
              </div>
            </div>

            {!isLoading && accounts.length === 0 ? (
              <div className="ledger-empty">No accounts found.</div>
            ) : null}

            {accounts.length > 0 ? (
              <>
                <div className="accts-table-wrap">
                  <table className="ledger-table accts-table" aria-label="Account balances">
                    <thead>
                      <tr>
                        <th scope="col">Account</th>
                        <th scope="col">Last statement</th>
                        <th scope="col" className="is-right">Balance</th>
                        <th scope="col" className="is-right">Share</th>
                        <th scope="col" className="is-right">Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {accounts.map((account) => {
                        const tone = getProviderTone(account.provider);
                        const stale = isAccountStale(account.as_of);
                        const share =
                          account.balance !== null && shareBase > 0
                            ? Math.abs(account.balance) / shareBase
                            : null;

                        return (
                          <tr key={account.account_id}>
                            <td>
                              <div className="accts-account-cell">
                                <span className="accts-provider-line">
                                  <span
                                    className="accts-provider-dot"
                                    aria-hidden="true"
                                    style={{ backgroundColor: tone.accent }}
                                  />
                                  {accountDescriptor(account)}
                                </span>
                                <strong>{accountIdentity(account)}</strong>
                              </div>
                            </td>
                            <td>
                              <div className="accts-statement-cell">
                                <span className={`ledger-tag ${stale ? 'is-warn' : 'is-ok'}`}>
                                  {stale ? 'Stale' : 'Fresh'}
                                </span>
                                <span className="accts-table-note">
                                  {account.as_of ? formatDate(account.as_of) : 'No statement date'}
                                </span>
                              </div>
                            </td>
                            <td className="is-right accts-balance-cell">
                              {account.balance == null ? '—' : formatMoney(account.balance)}
                            </td>
                            <td className="is-right">{formatShare(share)}</td>
                            <td className="is-right">
                              <button
                                type="button"
                                className="accts-open-link"
                                onClick={() =>
                                  void navigate('/transactions', {
                                    state: {
                                      accountId: account.account_id,
                                    },
                                  })
                                }
                              >
                                Transactions
                              </button>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>

                <div className="accts-mobile-list">
                  {accounts.map((account) => {
                    const tone = getProviderTone(account.provider);
                    const stale = isAccountStale(account.as_of);
                    const share =
                      account.balance !== null && shareBase > 0
                        ? Math.abs(account.balance) / shareBase
                        : null;

                    return (
                      <article key={account.account_id} className="accts-mobile-card">
                        <div className="accts-mobile-topline">
                          <div className="accts-account-cell">
                            <span className="accts-provider-line">
                              <span
                                className="accts-provider-dot"
                                aria-hidden="true"
                                style={{ backgroundColor: tone.accent }}
                              />
                              {accountDescriptor(account)}
                            </span>
                            <strong>{accountIdentity(account)}</strong>
                          </div>
                          <span className={`ledger-tag ${stale ? 'is-warn' : 'is-ok'}`}>
                            {stale ? 'Stale' : 'Fresh'}
                          </span>
                        </div>

                        <dl className="accts-mobile-facts">
                          <div>
                            <dt>Balance</dt>
                            <dd>{account.balance == null ? '—' : formatMoney(account.balance)}</dd>
                          </div>
                          <div>
                            <dt>Share</dt>
                            <dd>{formatShare(share)}</dd>
                          </div>
                          <div>
                            <dt>Last statement</dt>
                            <dd>{account.as_of ? formatDate(account.as_of) : 'No statement date'}</dd>
                          </div>
                        </dl>

                        <button
                          type="button"
                          className="accts-open-link"
                          onClick={() =>
                            void navigate('/transactions', {
                              state: {
                                accountId: account.account_id,
                              },
                            })
                          }
                        >
                          Transactions
                        </button>
                      </article>
                    );
                  })}
                </div>
              </>
            ) : null}
          </section>
        </div>
      </article>
    </section>
  );
}

export { AccountsPage as NetWorthPage };
