import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  filterStandaloneReviewTransactions,
  getSuggestedTransfers,
  mergeReviewTransactions,
  resolveAttentionTarget,
} from '../app/attentionQueue';
import { currentMonthValue, monthRange, monthRangeMonthsAgo } from '../app/dateRange';
import { formatCount, formatMoney } from '../app/formatters';
import {
  getProviderTone,
  getReviewLabel,
  getReviewTone,
  getTransactionSignedAmount,
  getTransactionTitle,
} from '../app/financePresentation';
import {
  apiClient,
  type AnalyticsMonthlyFlowResponse,
  type ExceptionSummary,
  type NetWorthCurrentResponse,
  type TransactionSummary,
  type TransferLinkSummary,
} from '../api/client';
import './overview.css';

type MonthlyFlowItem = AnalyticsMonthlyFlowResponse['items'][number];
type BalanceSnapshot = NetWorthCurrentResponse['accounts'][number];

const TREND_MONTH_WINDOW = 5;
const RECENT_LIMIT = '6';
const REVIEW_FETCH_LIMIT = 200;

function emptyMonthlyFlow(period: string): MonthlyFlowItem {
  return {
    period,
    inflow: 0,
    outflow: 0,
    net: 0,
    tx_count: 0,
  };
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

function formatPeriodLabel(period: string): string {
  if (!period || !/^\d{4}-\d{2}$/.test(period)) {
    return 'This month';
  }

  const [yearRaw, monthRaw] = period.split('-').map(Number);
  const date = new Date(yearRaw, (monthRaw || 1) - 1, 1);
  return date.toLocaleDateString('en-US', {
    month: 'long',
    year: 'numeric',
  });
}

function formatPeriodShort(period: string): string {
  if (!period || !/^\d{4}-\d{2}$/.test(period)) {
    return 'This month';
  }

  const [yearRaw, monthRaw] = period.split('-').map(Number);
  const date = new Date(yearRaw, (monthRaw || 1) - 1, 1);
  return date.toLocaleDateString('en-US', {
    month: 'short',
  });
}

function formatShortDate(value: string | null | undefined): string {
  const date = parseDate(value);
  if (!date) {
    return value || '—';
  }

  return date.toLocaleDateString('en-GB', {
    day: '2-digit',
    month: 'short',
  });
}

function formatSignedMoney(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
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

function formatPercentDelta(value: number | null): string {
  if (value === null || !Number.isFinite(value)) {
    return '—';
  }

  const percent = value * 100;
  const rounded = Math.abs(percent) >= 10 ? percent.toFixed(0) : percent.toFixed(1);
  return `${percent > 0 ? '+' : ''}${rounded}%`;
}

function ensureCurrentMonth(items: MonthlyFlowItem[], currentMonth: string): MonthlyFlowItem[] {
  const rows = [...items];
  if (!rows.some((item) => item.period === currentMonth)) {
    rows.push(emptyMonthlyFlow(currentMonth));
  }

  return rows.sort((left, right) => left.period.localeCompare(right.period));
}

function getSpendChangeTone(delta: number | null): string {
  if (delta === null || delta === 0) {
    return '';
  }

  return delta > 0 ? 'is-negative' : 'is-positive';
}

function getBalanceLabel(account: BalanceSnapshot): string {
  return account.display_name || account.masked_identifier || account.account_type || 'Account';
}

function MonthlySpendChart({
  items,
  currentPeriod,
  onPeriodFocusChange,
}: {
  items: MonthlyFlowItem[];
  currentPeriod: string;
  onPeriodFocusChange: (period: string | null) => void;
}) {
  const safeItems = items.length ? items : [emptyMonthlyFlow(currentPeriod)];
  const maxOutflow = Math.max(...safeItems.map((item) => item.outflow), 1);

  const width = 720;
  const height = 240;
  const paddingX = 18;
  const paddingTop = 16;
  const paddingBottom = 44;
  const chartWidth = width - paddingX * 2;
  const chartHeight = height - paddingTop - paddingBottom;
  const baselineY = paddingTop + chartHeight;

  const step = chartWidth / safeItems.length;
  const barWidth = Math.max(12, step * 0.58);

  const [hoveredPeriod, setHoveredPeriod] = useState<string | null>(null);
  const [pinnedPeriod, setPinnedPeriod] = useState<string | null>(null);
  const activePeriod = hoveredPeriod ?? pinnedPeriod;

  const bars = safeItems.map((item, index) => {
    const value = item.outflow || 0;
    const normalizedHeight = maxOutflow > 0 ? (value / maxOutflow) * chartHeight : 0;
    const barHeight = Math.max(2, normalizedHeight);
    const x = paddingX + index * step + (step - barWidth) / 2;
    const y = baselineY - barHeight;

    return {
      period: item.period,
      value,
      x,
      y,
      barHeight,
      isCurrent: item.period === currentPeriod,
    };
  });

  const activeBar = activePeriod ? bars.find((bar) => bar.period === activePeriod) || null : null;

  const callout = activeBar
    ? (() => {
        const title = formatPeriodLabel(activeBar.period);
        const valueText = formatMoney(activeBar.value);

        const padding = 12;
        const lineHeight = 14;
        const boxHeight = padding * 2 + lineHeight * 2 + 4;
        const approxChar = 6.4;
        const widest = Math.max(title.length, valueText.length);
        const boxWidth = Math.max(140, Math.min(240, widest * approxChar + padding * 2));

        const centerX = activeBar.x + barWidth / 2;
        const rawX = centerX - boxWidth / 2;
        const boxX = Math.max(paddingX, Math.min(width - paddingX - boxWidth, rawX));
        const rawY = activeBar.y - boxHeight - 12;
        const boxY = Math.max(6, rawY);

        const leaderStartY = boxY + boxHeight;
        const leaderEndY = Math.max(paddingTop + 2, activeBar.y - 4);
        const showLeader = leaderStartY + 6 < leaderEndY;

        return {
          title,
          valueText,
          boxX,
          boxY,
          boxWidth,
          boxHeight,
          centerX,
          titleY: boxY + padding + lineHeight - 2,
          valueY: boxY + padding + lineHeight * 2 + 4,
          showLeader,
          leaderStartY,
          leaderEndY,
        };
      })()
    : null;

  return (
    <div className="overview-trend-chart" role="img" aria-label="Monthly spending trend">
      <svg
        className="overview-trend-svg"
        viewBox={`0 0 ${width} ${height}`}
        aria-hidden="true"
        onPointerLeave={() => {
          setHoveredPeriod(null);
          onPeriodFocusChange(pinnedPeriod);
        }}
      >
        <defs>
          <linearGradient id="overview-monthly-bar" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#33c5bb" stopOpacity="1" />
            <stop offset="100%" stopColor="#0e8a80" stopOpacity="1" />
          </linearGradient>
        </defs>

        {[0.25, 0.5, 0.75].map((ratio) => (
          <line
            key={ratio}
            x1={paddingX}
            x2={width - paddingX}
            y1={paddingTop + chartHeight * ratio}
            y2={paddingTop + chartHeight * ratio}
            className="overview-trend-grid"
          />
        ))}

        {activeBar ? (
          <line
            x1={activeBar.x + barWidth / 2}
            x2={activeBar.x + barWidth / 2}
            y1={paddingTop}
            y2={baselineY}
            className="overview-trend-guide"
          />
        ) : null}

        {bars.map((bar) => {
          const isActive = activePeriod === bar.period;
          const isDimmed = activePeriod !== null && !isActive;
          return (
            <g key={bar.period}>
              <rect
                x={bar.x}
                y={bar.y}
                width={barWidth}
                height={bar.barHeight}
                rx={10}
                data-period={bar.period}
                className={[
                  'overview-trend-bar',
                  bar.isCurrent ? 'is-current' : '',
                  isActive ? 'is-active' : '',
                  isDimmed ? 'is-dimmed' : '',
                ]
                  .filter(Boolean)
                  .join(' ')}
                onPointerEnter={() => {
                  setHoveredPeriod(bar.period);
                  onPeriodFocusChange(bar.period);
                }}
                onClick={() => {
                  const nextPinned = pinnedPeriod === bar.period ? null : bar.period;
                  setPinnedPeriod(nextPinned);
                  onPeriodFocusChange(hoveredPeriod ?? nextPinned);
                }}
              >
                <title>
                  {formatPeriodLabel(bar.period)}: {formatMoney(bar.value)}
                </title>
              </rect>
              <text
                x={bar.x + barWidth / 2}
                y={height - 18}
                textAnchor="middle"
                className={[
                  'overview-trend-label',
                  bar.isCurrent ? 'is-current' : '',
                  isActive ? 'is-active' : '',
                ]
                  .filter(Boolean)
                  .join(' ')}
              >
                {formatPeriodShort(bar.period)}
              </text>
            </g>
          );
        })}

        {callout ? (
          <g className="overview-trend-callout" aria-hidden="true">
            {callout.showLeader ? (
              <line
                x1={callout.centerX}
                x2={callout.centerX}
                y1={callout.leaderStartY}
                y2={callout.leaderEndY}
                className="overview-trend-callout-leader"
              />
            ) : null}
            <rect
              x={callout.boxX}
              y={callout.boxY}
              width={callout.boxWidth}
              height={callout.boxHeight}
              rx={14}
              className="overview-trend-callout-bg"
            />
            <text
              x={callout.boxX + callout.boxWidth / 2}
              y={callout.titleY}
              textAnchor="middle"
              className="overview-trend-callout-title"
            >
              {callout.title}
            </text>
            <text
              x={callout.boxX + callout.boxWidth / 2}
              y={callout.valueY}
              textAnchor="middle"
              className="overview-trend-callout-value"
            >
              {callout.valueText}
            </text>
          </g>
        ) : null}
      </svg>
    </div>
  );
}

export function OverviewPage() {
  const navigate = useNavigate();
  const currentMonth = currentMonthValue();
  const [monthlyFlow, setMonthlyFlow] = useState<MonthlyFlowItem[]>([]);
  const [recentItems, setRecentItems] = useState<TransactionSummary[]>([]);
  const [netWorth, setNetWorth] = useState<NetWorthCurrentResponse | null>(null);
  const [openExceptions, setOpenExceptions] = useState<ExceptionSummary[]>([]);
  const [suggestedTransfers, setSuggestedTransfers] = useState<TransferLinkSummary[]>([]);
  const [reviewTransactions, setReviewTransactions] = useState<TransactionSummary[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [errorMessage, setErrorMessage] = useState<string>('');
  const [trendFocus, setTrendFocus] = useState<{ month: string; period: string | null }>(() => ({
    month: currentMonth,
    period: null,
  }));

  useEffect(() => {
    let mounted = true;

    const loadOverview = async () => {
      const currentScope = monthRange(currentMonth);
      const trendScope = monthRangeMonthsAgo(TREND_MONTH_WINDOW);

      if (!currentScope) {
        return;
      }

      setIsLoading(true);
      setErrorMessage('');

      const monthStart = `${currentScope.start}T00:00:00`;
      const monthEnd = `${currentScope.end}T23:59:59`;
      const trendStart = `${trendScope.start}T00:00:00`;

      const results = await Promise.allSettled([
        apiClient.monthlyFlow({
          start: trendStart,
          end: monthEnd,
        }),
        apiClient.transactions({
          start: monthStart,
          end: monthEnd,
          limit: RECENT_LIMIT,
        }),
        apiClient.netWorthCurrent(),
        apiClient.exceptions({ status: 'open' }),
        apiClient.transferLinks({ status: 'suggested' }),
        apiClient.transactions({
          needs_human_review: true,
          include_transfers: true,
          limit: REVIEW_FETCH_LIMIT,
        }),
      ]);

      if (!mounted) {
        return;
      }

      const [
        monthlyFlowResult,
        recentTransactionsResult,
        netWorthResult,
        exceptionsResult,
        suggestedTransfersResult,
        reviewTransactionsResult,
      ] = results;

      setMonthlyFlow(
        monthlyFlowResult.status === 'fulfilled' ? monthlyFlowResult.value.items || [] : [],
      );
      setRecentItems(
        recentTransactionsResult.status === 'fulfilled'
          ? recentTransactionsResult.value.items || []
          : [],
      );
      setNetWorth(netWorthResult.status === 'fulfilled' ? netWorthResult.value : null);
      setOpenExceptions(
        exceptionsResult.status === 'fulfilled' ? exceptionsResult.value || [] : [],
      );
      setSuggestedTransfers(
        suggestedTransfersResult.status === 'fulfilled'
          ? getSuggestedTransfers(suggestedTransfersResult.value)
          : [],
      );

      const mergedReviewTransactions = mergeReviewTransactions(
        reviewTransactionsResult.status === 'fulfilled'
          ? reviewTransactionsResult.value.items || []
          : [],
      );
      const nextExceptions =
        exceptionsResult.status === 'fulfilled' ? exceptionsResult.value || [] : [];
      const nextSuggestedTransfers =
        suggestedTransfersResult.status === 'fulfilled'
          ? getSuggestedTransfers(suggestedTransfersResult.value)
          : [];
      setReviewTransactions(
        filterStandaloneReviewTransactions(
          mergedReviewTransactions,
          nextExceptions,
          nextSuggestedTransfers,
        ),
      );

      if (!results.some((result) => result.status === 'fulfilled')) {
        setErrorMessage('Overview is currently unavailable. Check the API connection and refresh.');
      }

      setIsLoading(false);
    };

    void loadOverview();

	    return () => {
	      mounted = false;
	    };
	  }, [currentMonth]);

  const trendFocusPeriod = trendFocus.month === currentMonth ? trendFocus.period : null;
  const onTrendFocusChange = (period: string | null) => setTrendFocus({ month: currentMonth, period });

  const trendRows = useMemo(
    () => ensureCurrentMonth(monthlyFlow, currentMonth),
    [currentMonth, monthlyFlow],
  );
  const trendDisplayRows = useMemo(() => trendRows.slice(-6), [trendRows]);

  const currentMonthSnapshot =
    trendRows.find((item) => item.period === currentMonth) || emptyMonthlyFlow(currentMonth);
  const focusedMonthSnapshot = trendFocusPeriod
    ? trendRows.find((item) => item.period === trendFocusPeriod) || currentMonthSnapshot
    : currentMonthSnapshot;
  const previousMonthSnapshot =
    [...trendRows].filter((item) => item.period < currentMonth).slice(-1)[0] || null;

  const balanceRowsById = useMemo(
    () => new Map((netWorth?.accounts || []).map((account) => [account.account_id, account])),
    [netWorth],
  );

  const attentionCount = openExceptions.length + suggestedTransfers.length + reviewTransactions.length;
  const attentionTarget = resolveAttentionTarget(
    openExceptions,
    suggestedTransfers,
    reviewTransactions,
  );
  const cashPosition = netWorth?.totals?.[0]?.total_balance ?? null;
  const cashPositionNote = netWorth?.accounts?.length
    ? `${formatCount(netWorth.accounts.length)} accounts in scope`
    : 'Balance snapshot unavailable';
  const attentionSummary = attentionCount > 0 ? `${formatCount(attentionCount)} pending` : 'Queue clear';

  const spendDelta = previousMonthSnapshot
    ? currentMonthSnapshot.outflow - previousMonthSnapshot.outflow
    : null;
  const spendDeltaPercent =
    previousMonthSnapshot && previousMonthSnapshot.outflow > 0 && spendDelta !== null
      ? spendDelta / previousMonthSnapshot.outflow
      : null;
  const totalSpend = currentMonthSnapshot.outflow || 0;
  const spendChangeSummary = previousMonthSnapshot
    ? `${formatSignedMoney(spendDelta)} vs ${formatPeriodShort(previousMonthSnapshot.period)}`
    : null;
  const spendChangePercentLabel = previousMonthSnapshot
    ? formatPercentDelta(spendDeltaPercent)
    : null;

  const openAttentionTarget = () => {
    if (attentionCount <= 0) {
      navigate('/transactions');
      return;
    }

    if (attentionTarget.transactionId) {
      navigate('/review', {
        state: {
          openTransactionId: attentionTarget.transactionId,
        },
      });
      return;
    }

    navigate('/review');
  };

  return (
    <section className="overview-page fade-in" data-testid="overview-page">
      {errorMessage ? <div className="overview-inline-warning">{errorMessage}</div> : null}

      <article className="ledger-document overview-document">
        <header className="ledger-doc-head">
          <div className="ledger-section-copy">
            <h2>Statement of account activity</h2>
            <span className="ledger-note">Monthly movement across your tracked cash accounts.</span>
          </div>
          <span className="ledger-doc-sub">{formatPeriodLabel(currentMonthSnapshot.period)}</span>
        </header>

        <section className="overview-top-grid" data-testid="overview-top">
          <article className="overview-hero" data-testid="overview-hero">
            <div className="overview-hero-topline">
              <span className="overview-hero-kicker">Spent (excluding transfers)</span>
              {spendChangeSummary ? (
                <span className={`overview-hero-pill ${getSpendChangeTone(spendDelta)}`.trim()}>
                  {spendChangeSummary}
                  {spendChangePercentLabel ? ` · ${spendChangePercentLabel}` : ''}
                </span>
              ) : null}
            </div>

            <div className="overview-hero-copy">
              <h3>Monthly outflow under review</h3>
              <strong className="overview-hero-amount">{formatMoney(totalSpend)}</strong>
              <p className="overview-hero-note">
                Calculated from counted outflows for the selected month after linked internal transfers are excluded.
              </p>
            </div>

            <div className="overview-hero-actions">
              <button
                type="button"
                className="overview-primary-button"
                onClick={() => navigate('/transactions', { state: { direction: 'out' } })}
              >
                Open transactions
              </button>
              {attentionCount > 0 ? (
                <button
                  type="button"
                  className="overview-secondary-button"
                  onClick={openAttentionTarget}
                >
                  Open review
                </button>
              ) : null}
            </div>
          </article>

          <aside className="overview-rail" aria-label="Supporting metrics">
            <div className="overview-metric-card">
              <span className="overview-metric-label">Cash position</span>
              <strong className="overview-metric-value">
                {cashPosition === null ? '—' : formatMoney(cashPosition)}
              </strong>
              <span className="overview-metric-note">{cashPositionNote}</span>
            </div>

            <div className={`overview-metric-card ${attentionCount > 0 ? 'is-warning' : ''}`.trim()}>
              <span className="overview-metric-label">Review desk</span>
              <strong className="overview-metric-value">{attentionSummary}</strong>
              <span className="overview-metric-note">Exceptions, transfer suggestions, and uncategorized rows.</span>
            </div>
          </aside>
        </section>

        <section className="ledger-kpi-strip overview-kpi-strip">
          <div className="ledger-kpi-cell">
            <span className="ledger-kpi-label">Income</span>
            <strong className="ledger-kpi-value is-positive">{formatMoney(currentMonthSnapshot.inflow)}</strong>
            <span className="ledger-kpi-note">Counted inflows for the selected month</span>
          </div>
          <div className="ledger-kpi-cell">
            <span className="ledger-kpi-label">Spending</span>
            <strong className="ledger-kpi-value is-negative">{formatMoney(currentMonthSnapshot.outflow)}</strong>
            <span className="ledger-kpi-note">Counted outflows after transfer filtering</span>
          </div>
          <div className="ledger-kpi-cell">
            <span className="ledger-kpi-label">Net cashflow</span>
            <strong
              className={`ledger-kpi-value ${currentMonthSnapshot.net >= 0 ? 'is-positive' : 'is-negative'}`}
            >
              {formatSignedMoney(currentMonthSnapshot.net)}
            </strong>
            <span className="ledger-kpi-note">Movement for {formatPeriodShort(currentMonthSnapshot.period)}</span>
          </div>
          <div className="ledger-kpi-cell">
            <span className="ledger-kpi-label">Needs review</span>
            <strong className="ledger-kpi-value">{formatCount(attentionCount)}</strong>
            <span className="ledger-kpi-note">{attentionCount > 0 ? 'Open items remain unresolved' : 'Nothing is waiting for input'}</span>
          </div>
        </section>

        <section className="overview-surface overview-trend-panel ledger-panel">
          <div className="ledger-section-head">
            <div className="ledger-section-copy">
              <h3>Spending trend</h3>
              <span className="ledger-section-note">Last six reported months</span>
            </div>
            <div className="overview-trend-summary">
              <span>{formatPeriodLabel(focusedMonthSnapshot.period)}</span>
              <strong>{formatMoney(focusedMonthSnapshot.outflow)}</strong>
              <small>{formatCount(focusedMonthSnapshot.tx_count)} rows</small>
            </div>
          </div>

          <MonthlySpendChart
            key={currentMonthSnapshot.period}
            items={trendDisplayRows}
            currentPeriod={currentMonthSnapshot.period}
            onPeriodFocusChange={onTrendFocusChange}
          />
        </section>

        <section className="overview-surface overview-panel ledger-panel">
          <div className="ledger-section-head">
            <div className="ledger-section-copy">
              <h3>Recent activity</h3>
              <span className="ledger-section-note">Most recent entries in the current month</span>
            </div>
            <button
              type="button"
              className="overview-link-button"
              onClick={() => navigate('/transactions')}
            >
              All transactions
            </button>
          </div>

          <div className="overview-recent-head" aria-hidden="true">
            <span>Date</span>
            <span>Description</span>
            <span>Account</span>
            <span className="is-right">Amount</span>
          </div>

          <div className="overview-recent-list" role="list" aria-label="Recent transactions">
            {recentItems.map((item) => {
              const account = balanceRowsById.get(item.account_id || '');
              const providerTone = getProviderTone(account?.provider);
              const amount = getTransactionSignedAmount(item);
              const reviewTone = getReviewTone(item);
              const meta = [item.category || 'Uncategorized', account ? getBalanceLabel(account) : null]
                .filter(Boolean)
                .join(' · ');

              return (
                <button
                  key={item.id}
                  type="button"
                  className="overview-recent-row"
                  onClick={() => navigate('/transactions', { state: { openTransactionId: item.id } })}
                >
                  <span className="overview-recent-date">{formatShortDate(item.operation_datetime || item.posting_datetime)}</span>
                  <span className="overview-recent-copy">
                    <span className="overview-recent-topline">
                      <span
                        className="overview-recent-accent"
                        style={{ backgroundColor: providerTone.accent }}
                        aria-hidden="true"
                      />
                      <strong>{getTransactionTitle(item)}</strong>
                    </span>
                    <span className="overview-recent-meta">
                      <span>{reviewTone === 'attention' ? getReviewLabel(item) : meta}</span>
                    </span>
                  </span>
                  <span className="overview-recent-account">{account ? getBalanceLabel(account) : 'Account unavailable'}</span>
                  <strong
                    className={
                      amount >= 0 ? 'overview-amount is-positive' : 'overview-amount is-negative'
                    }
                  >
                    {formatSignedMoney(amount)}
                  </strong>
                </button>
              );
            })}

            {!recentItems.length && !isLoading ? (
              <div className="overview-empty">No transactions yet for this month.</div>
            ) : null}
          </div>
        </section>
      </article>
    </section>
  );
}
