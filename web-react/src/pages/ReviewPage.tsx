import { useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import {
  filterStandaloneReviewTransactions,
  getSuggestedTransfers,
  isAnomalyException,
  mergeReviewTransactions,
} from '../app/attentionQueue';
import { formatCount, formatDateTime, formatMoney } from '../app/formatters';
import {
  getAccountTone,
  getProviderTone,
  getMeaningDescription,
  getMeaningLabel,
  getReviewLabel,
  getReviewTone,
  getTransactionSignedAmount,
  getTransactionTitle,
} from '../app/financePresentation';
import {
  ApiError,
  apiClient,
  type AccountSummary,
  type ExceptionSummary,
  type TransactionSummary,
  type TransferLinkSummary,
} from '../api/client';
import './workflows.css';

const REVIEW_FETCH_LIMIT = 200;

type ReviewLane = 'transfer' | 'exception' | 'category' | 'review' | 'anomaly';

type ReviewItem =
  | {
      kind: 'transfer';
      key: string;
      section: 'suggested_transfers';
      item: TransferLinkSummary;
      outTx: TransactionSummary | null;
      inTx: TransactionSummary | null;
    }
  | {
      kind: 'exception';
      key: string;
      section: 'open_exceptions' | 'anomalies';
      item: ExceptionSummary;
      relatedTx: TransactionSummary | null;
    }
  | {
      kind: 'transaction';
      key: string;
      section: 'needs_input';
      item: TransactionSummary;
    };

interface ReviewLocationState {
  openTransactionId?: string;
  openQueueItemId?: string;
  openQueueKind?: 'exception' | 'transfer';
}

function makeItemKey(kind: ReviewItem['kind'], id: string): string {
  return `${kind}:${id}`;
}

function humanizeExceptionType(value: string): string {
  if (!value) {
    return 'Exception';
  }

  return value
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

function titleCase(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) {
    return '';
  }

  return trimmed.charAt(0).toUpperCase() + trimmed.slice(1);
}

function getExceptionSuggestedCategory(item: ExceptionSummary): string | null {
  const payload = item.payload;
  if (!payload || typeof payload !== 'object') {
    return null;
  }

  const value = payload.suggested_category;
  if (typeof value === 'string' && value.trim()) {
    return value.trim();
  }

  return null;
}

function canMarkDuplicate(item: ExceptionSummary): boolean {
  return item.entity_type === 'transaction' || item.exception_type.toLowerCase().includes('duplicate');
}

function getTransactionSuggestedCategory(item: TransactionSummary): string {
  const category = item.category?.trim();
  if (category) {
    return category;
  }

  const bankCategory = item.bank_category?.trim();
  if (bankCategory) {
    return titleCase(bankCategory);
  }

  return 'Uncategorized';
}

function isCategoryEmpty(item: TransactionSummary): boolean {
  return !(item.category || '').trim();
}

function transferMatchScoreLabel(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return '—';
  }

  return `${Math.round(value * 100)}%`;
}

function getTransferPairTitle(outTx: TransactionSummary | null, inTx: TransactionSummary | null): string {
  const from = outTx ? getTransactionTitle(outTx) : 'Missing outflow';
  const to = inTx ? getTransactionTitle(inTx) : 'Missing inflow';
  return `${from} → ${to}`;
}

function getReviewLane(item: ReviewItem): ReviewLane {
  switch (item.kind) {
    case 'transfer':
      return 'transfer';
    case 'exception':
      return item.section === 'anomalies' ? 'anomaly' : 'exception';
    case 'transaction': {
      const needsCategory =
        isCategoryEmpty(item.item) ||
        (item.item.review_reasons || []).includes('uncategorized_needs_review');
      return needsCategory ? 'category' : 'review';
    }
  }
}

function getReviewLaneLabel(lane: ReviewLane): string {
  switch (lane) {
    case 'transfer':
      return 'Transfer';
    case 'exception':
      return 'Exception';
    case 'anomaly':
      return 'Anomaly';
    case 'category':
      return 'Category';
    case 'review':
      return 'Review';
  }
}

function severityScore(value: string | null | undefined): number {
  const normalized = (value || '').trim().toLowerCase();
  if (normalized === 'high') {
    return 3;
  }
  if (normalized === 'medium') {
    return 2;
  }
  if (normalized === 'low') {
    return 1;
  }
  return 0;
}

function getTransactionEventKey(item: TransactionSummary | null | undefined): string {
  if (!item) {
    return '';
  }
  return item.operation_datetime || item.posting_datetime || '';
}

function getTransferMagnitude(item: TransactionSummary | null): number {
  if (!item) {
    return 0;
  }
  return Math.abs(Number(item.amount) || 0);
}

function getTransferDelta(outTx: TransactionSummary | null, inTx: TransactionSummary | null): number {
  return Math.abs(getTransferMagnitude(outTx) - getTransferMagnitude(inTx));
}

function formatTimeDelta(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) {
    return '—';
  }

  if (seconds < 60) {
    return `${Math.round(seconds)}s`;
  }

  const minutes = seconds / 60;
  if (minutes < 60) {
    return `${Math.round(minutes)}m`;
  }

  const hours = minutes / 60;
  if (hours < 24) {
    return `${Math.round(hours)}h`;
  }

  const days = hours / 24;
  return `${Math.round(days)}d`;
}

function getTransferTimeDeltaLabel(outTx: TransactionSummary | null, inTx: TransactionSummary | null): string {
  if (!outTx || !inTx) {
    return '—';
  }

  const outAt = getTransactionEventKey(outTx);
  const inAt = getTransactionEventKey(inTx);
  if (!outAt || !inAt) {
    return '—';
  }

  const outDate = new Date(outAt);
  const inDate = new Date(inAt);
  if (Number.isNaN(outDate.getTime()) || Number.isNaN(inDate.getTime())) {
    return '—';
  }

  const seconds = Math.abs(inDate.getTime() - outDate.getTime()) / 1000;
  return formatTimeDelta(seconds);
}

function looksLikeTransferDebugRationale(value: string | null | undefined): boolean {
  const trimmed = (value || '').trim();
  if (!trimmed) {
    return false;
  }

  if (trimmed.length < 30) {
    return false;
  }

  return /[a-z0-9_]+=/.test(trimmed);
}

function getStatementProvenance(item: TransactionSummary | null): string {
  if (!item) {
    return 'Statement evidence is unavailable.';
  }

  const parts: string[] = [];

  if (item.source_statement_id) {
    parts.push(`Statement ${item.source_statement_id}`);
  }

  if ((item.source_page_number || 0) > 0) {
    parts.push(`page ${item.source_page_number}`);
  }

  if ((item.source_row_index || 0) > 0) {
    parts.push(`row ${item.source_row_index}`);
  }

  if (!parts.length) {
    return 'Statement evidence is unavailable.';
  }

  return parts.join(' · ');
}

function getAccountLabel(account: AccountSummary | null | undefined): string {
  return getAccountTone(account).label;
}

function getAccountIdentity(account: AccountSummary | null | undefined): string {
  if (!account) {
    return 'Account unavailable';
  }

  const parts = [account.display_name, account.masked_identifier].filter(
    (value): value is string => typeof value === 'string' && value.trim().length > 0,
  );

  if (parts.length) {
    return parts.join(' · ');
  }

  return account.account_type || 'Account';
}

function confidenceTone(score: number | null | undefined): 'high' | 'medium' | 'low' {
  const value = typeof score === 'number' ? score : null;
  if (value === null || !Number.isFinite(value)) {
    return 'medium';
  }

  if (value >= 0.95) {
    return 'high';
  }

  if (value >= 0.85) {
    return 'medium';
  }

  return 'low';
}

function deltaTone(delta: number): 'ok' | 'warn' {
  if (!Number.isFinite(delta)) {
    return 'warn';
  }

  return delta <= 0.01 ? 'ok' : 'warn';
}

function providerBadge(account: AccountSummary | null | undefined) {
  if (!account) {
    return (
      <span className="review-badge review-badge-provider tone-muted" title="Account">
        Account
      </span>
    );
  }

  const tone = getProviderTone(account.provider);
  const style = {
    background: tone.background,
    color: tone.accent,
    borderColor: tone.accent,
  } satisfies CSSProperties;

  return (
    <span
      className="review-badge review-badge-provider"
      style={style}
      title={tone.label}
      aria-label={tone.label}
    >
      <span className="review-badge-dot" style={{ backgroundColor: tone.accent }} aria-hidden="true" />
      {tone.shortLabel}
    </span>
  );
}

function getTransactionMeta(
  item: TransactionSummary | null,
  account: AccountSummary | null | undefined,
): string {
  if (!item) {
    return 'Linked transaction details are unavailable.';
  }

  const parts = [
    formatDateTime(item.operation_datetime || item.posting_datetime),
    formatMoney(getTransactionSignedAmount(item)),
    account ? getAccountLabel(account) : 'Account unavailable',
    item.category || getMeaningLabel(item.meaning),
  ];

  return parts.join(' · ');
}

function getTransactionReviewReason(item: TransactionSummary): string {
  if ((item.review_reasons || []).includes('uncategorized_needs_review')) {
    return `Category is still missing and this row needs a manual decision (${getTransactionSuggestedCategory(item)}).`;
  }

  if (isCategoryEmpty(item)) {
    return `Category still needs approval (${getTransactionSuggestedCategory(item)}).`;
  }

  if (getReviewTone(item) === 'attention') {
    return 'This transaction is still waiting for your approval.';
  }

  return 'Needs confirmation before it leaves the review queue.';
}

function matchesRequestedFocus(item: ReviewItem, focus: ReviewLocationState | null): boolean {
  if (!focus) {
    return false;
  }

  if (focus.openQueueItemId && focus.openQueueKind) {
    if (item.kind !== focus.openQueueKind) {
      return false;
    }

    return item.item.id === focus.openQueueItemId;
  }

  if (!focus.openTransactionId) {
    return false;
  }

  switch (item.kind) {
    case 'transaction':
      return item.item.id === focus.openTransactionId;
    case 'exception':
      return item.relatedTx?.id === focus.openTransactionId;
    case 'transfer':
      return (
        item.outTx?.id === focus.openTransactionId || item.inTx?.id === focus.openTransactionId
      );
  }
}

function renderTransactionDetailCard(
  item: TransactionSummary | null,
  account: AccountSummary | null | undefined,
  label: string,
) {
  if (!item) {
    return (
      <section className="review-detail-card">
        <div className="review-detail-eyebrow">{label}</div>
        <strong>Linked transaction unavailable</strong>
        <p>The queue item is still actionable, but the row details could not be loaded.</p>
      </section>
    );
  }

  return (
    <section className="review-detail-card">
      <div className="review-detail-eyebrow">{label}</div>
      <strong>{getTransactionTitle(item)}</strong>
      <p>{getTransactionMeta(item, account)}</p>
      <dl className="review-detail-facts">
        <div>
          <dt>Counting</dt>
          <dd>{getMeaningDescription(item.meaning)}</dd>
        </div>
        <div>
          <dt>Review status</dt>
          <dd>{getReviewLabel(item)}</dd>
        </div>
        <div>
          <dt>Bank category</dt>
          <dd>{item.bank_category || '—'}</dd>
        </div>
        <div>
          <dt>Statement evidence</dt>
          <dd>{getStatementProvenance(item)}</dd>
        </div>
      </dl>
      <p className="review-detail-raw">{item.description_raw || 'No raw description available.'}</p>
      <p className="review-detail-tags">Tags: {(item.tags || []).join(', ') || '—'}</p>
    </section>
  );
}

export function ReviewPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const initialFocusRef = useRef<ReviewLocationState | null>(
    (location.state as ReviewLocationState | null) || null,
  );
  const txCacheRef = useRef<Map<string, TransactionSummary>>(new Map());
  const containerRef = useRef<HTMLElement | null>(null);

  const [openExceptions, setOpenExceptions] = useState<ExceptionSummary[]>([]);
  const [suggestedTransfers, setSuggestedTransfers] = useState<TransferLinkSummary[]>([]);
  const [reviewCandidates, setReviewCandidates] = useState<TransactionSummary[]>([]);
  const [accounts, setAccounts] = useState<AccountSummary[]>([]);
  const [lastImportAt, setLastImportAt] = useState<string | null>(null);
  const [txCacheVersion, setTxCacheVersion] = useState<number>(0);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [isApplying, setIsApplying] = useState<boolean>(false);
  const [errorMessage, setErrorMessage] = useState<string>('');
  const [feedbackMessage, setFeedbackMessage] = useState<string>('');
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [hasInitializedSelection, setHasInitializedSelection] = useState<boolean>(false);
  const [skippedKeys, setSkippedKeys] = useState<Record<string, true>>({});

  const fetchQueue = async () => {
    setIsLoading(true);
    setErrorMessage('');
    setSkippedKeys({});

    const [
      exceptionsResult,
      transfersResult,
      accountsResult,
      batchesResult,
      reviewTransactionsResult,
    ] = await Promise.allSettled([
      apiClient.exceptions({ status: 'open' }),
      apiClient.transferLinks({ status: 'suggested' }),
      apiClient.accounts(),
      apiClient.importBatches({ limit: 1 }),
      apiClient.transactions({
        needs_human_review: true,
        include_transfers: true,
        limit: REVIEW_FETCH_LIMIT,
      }),
    ]);

    const exceptions = exceptionsResult.status === 'fulfilled' ? exceptionsResult.value : [];
    const transfers =
      transfersResult.status === 'fulfilled' ? getSuggestedTransfers(transfersResult.value) : [];
    const mergedTransactions = mergeReviewTransactions(
      reviewTransactionsResult.status === 'fulfilled'
        ? reviewTransactionsResult.value.items || []
        : [],
    );

    setOpenExceptions(exceptions);
    setSuggestedTransfers(transfers);
    setReviewCandidates(mergedTransactions);

    if (accountsResult.status === 'fulfilled') {
      setAccounts(accountsResult.value);
    } else {
      setAccounts([]);
    }

    if (batchesResult.status === 'fulfilled') {
      setLastImportAt(batchesResult.value[0]?.created_at || null);
    } else {
      setLastImportAt(null);
    }

    if (
      exceptionsResult.status === 'rejected' &&
      transfersResult.status === 'rejected' &&
      reviewTransactionsResult.status === 'rejected'
    ) {
      setErrorMessage('Review is currently unavailable.');
      setIsLoading(false);
      return;
    }

    let cacheUpdated = false;
    mergedTransactions.forEach((item) => {
      txCacheRef.current.set(item.id, item);
      cacheUpdated = true;
    });

    const txIds = new Set<string>();
    exceptions.forEach((item) => {
      if (item.entity_type === 'transaction' && item.entity_id) {
        txIds.add(item.entity_id);
      }
    });
    transfers.forEach((item) => {
      if (item.transaction_out_id) {
        txIds.add(item.transaction_out_id);
      }
      if (item.transaction_in_id) {
        txIds.add(item.transaction_in_id);
      }
    });

    await Promise.all(
      Array.from(txIds).map(async (txId) => {
        if (!txId || txCacheRef.current.has(txId)) {
          return;
        }

        try {
          const tx = await apiClient.transactionById(txId);
          txCacheRef.current.set(tx.id, tx);
          cacheUpdated = true;
        } catch {
          // Queue items remain actionable even if linked row details are unavailable.
        }
      }),
    );

    if (cacheUpdated) {
      setTxCacheVersion((prev) => prev + 1);
    }

    setIsLoading(false);
  };

  useEffect(() => {
    void fetchQueue();
  }, []);

  useEffect(() => {
    if (location.state) {
      navigate(location.pathname, { replace: true, state: null });
    }
  }, [location.pathname, location.state, navigate]);

  const runAction = async (action: () => Promise<unknown>, successMessage: string) => {
    setIsApplying(true);
    setErrorMessage('');
    setFeedbackMessage('');

    try {
      await action();
      setFeedbackMessage(successMessage);
      await fetchQueue();
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        setErrorMessage('Not authorized (missing or invalid admin token). Set it in Settings to apply actions.');
      } else {
        setErrorMessage('The action could not be applied. Refresh and try again.');
      }
    } finally {
      setIsApplying(false);
    }
  };

  const accountById = useMemo(() => {
    return new Map(accounts.map((account) => [account.id, account]));
  }, [accounts]);

  const standaloneTransactions = useMemo(
    () => filterStandaloneReviewTransactions(reviewCandidates, openExceptions, suggestedTransfers),
    [openExceptions, reviewCandidates, suggestedTransfers],
  );

  const suggestedTransferItems = useMemo<ReviewItem[]>(() => {
    void txCacheVersion;

    return suggestedTransfers.map((item) => ({
      kind: 'transfer',
      key: makeItemKey('transfer', item.id),
      section: 'suggested_transfers',
      item,
      outTx: txCacheRef.current.get(item.transaction_out_id) || null,
      inTx: txCacheRef.current.get(item.transaction_in_id) || null,
    }));
  }, [suggestedTransfers, txCacheVersion]);

  const openExceptionItems = useMemo<ReviewItem[]>(() => {
    void txCacheVersion;

    return openExceptions
      .filter((item) => !isAnomalyException(item))
      .map((item) => ({
        kind: 'exception',
        key: makeItemKey('exception', item.id),
        section: 'open_exceptions',
        item,
        relatedTx:
          item.entity_type === 'transaction' && item.entity_id
            ? txCacheRef.current.get(item.entity_id) || null
            : null,
      }));
  }, [openExceptions, txCacheVersion]);

  const anomalyItems = useMemo<ReviewItem[]>(() => {
    void txCacheVersion;

    return openExceptions
      .filter((item) => isAnomalyException(item))
      .map((item) => ({
        kind: 'exception',
        key: makeItemKey('exception', item.id),
        section: 'anomalies',
        item,
        relatedTx:
          item.entity_type === 'transaction' && item.entity_id
            ? txCacheRef.current.get(item.entity_id) || null
            : null,
      }));
  }, [openExceptions, txCacheVersion]);

  const needsInputItems = useMemo<ReviewItem[]>(() => {
    return standaloneTransactions.map((item) => ({
      kind: 'transaction',
      key: makeItemKey('transaction', item.id),
      section: 'needs_input',
      item,
    }));
  }, [standaloneTransactions]);

  const queueItems = useMemo<ReviewItem[]>(
    () => [...anomalyItems, ...suggestedTransferItems, ...openExceptionItems, ...needsInputItems],
    [anomalyItems, needsInputItems, openExceptionItems, suggestedTransferItems],
  );

  const displayItems = useMemo(() => {
    return queueItems
      .map((item) => {
        const lane = getReviewLane(item);
        const skipped = Boolean(skippedKeys[item.key]);
        const isTransfer = item.kind === 'transfer';
        const confidence = isTransfer ? item.item.match_score ?? 0 : 0;

        let urgencyScore = 0;
        let impactScore = 0;
        let eventKey = '';
        let delta = 0;

        switch (item.kind) {
          case 'transfer': {
            const outMagnitude = getTransferMagnitude(item.outTx);
            const inMagnitude = getTransferMagnitude(item.inTx);
            delta = Math.abs(outMagnitude - inMagnitude);

            urgencyScore = 3000 + Math.round(confidence * 100) - Math.min(50, Math.round(delta / 100));
            impactScore = outMagnitude + inMagnitude;
            eventKey = getTransactionEventKey(item.outTx) || getTransactionEventKey(item.inTx) || item.key;
            break;
          }
          case 'exception': {
            const base = lane === 'anomaly' ? 4000 : 3200;
            const severity = severityScore(item.item.severity);
            urgencyScore = base + severity * 100;
            impactScore = item.relatedTx ? Math.abs(Number(item.relatedTx.amount) || 0) : 0;
            eventKey = getTransactionEventKey(item.relatedTx) || item.key;
            break;
          }
          case 'transaction': {
            urgencyScore = lane === 'category' ? 2600 : 2400;
            impactScore = Math.abs(Number(item.item.amount) || 0);
            eventKey = getTransactionEventKey(item.item) || item.key;
            break;
          }
        }

        return { item, lane, skipped, urgencyScore, impactScore, eventKey, confidence, delta };
      })
      .sort((left, right) => {
        if (left.skipped !== right.skipped) {
          return left.skipped ? 1 : -1;
        }

        if (right.urgencyScore !== left.urgencyScore) {
          return right.urgencyScore - left.urgencyScore;
        }

        if (right.impactScore !== left.impactScore) {
          return right.impactScore - left.impactScore;
        }

        if (right.confidence !== left.confidence) {
          return right.confidence - left.confidence;
        }

        return right.eventKey.localeCompare(left.eventKey) || left.item.key.localeCompare(right.item.key);
      });
  }, [queueItems, skippedKeys]);

  useEffect(() => {
    if (!displayItems.length) {
      setSelectedKey(null);
      setHasInitializedSelection(false);
      return;
    }

    const availableKeys = new Set(displayItems.map((entry) => entry.item.key));

    if (selectedKey && availableKeys.has(selectedKey)) {
      return;
    }

    if (selectedKey && !availableKeys.has(selectedKey)) {
      setSelectedKey(displayItems[0].item.key);
      setHasInitializedSelection(true);
      return;
    }

    if (initialFocusRef.current) {
      const focusedItem = displayItems.find((entry) =>
        matchesRequestedFocus(entry.item, initialFocusRef.current),
      );
      if (focusedItem) {
        setSelectedKey(focusedItem.item.key);
        setHasInitializedSelection(true);
        initialFocusRef.current = null;
        return;
      }
    }

    if (!hasInitializedSelection) {
      setSelectedKey(displayItems[0].item.key);
      setHasInitializedSelection(true);
    }
  }, [displayItems, hasInitializedSelection, selectedKey]);

  useEffect(() => {
    if (!displayItems.length) {
      return;
    }

    const onKeyDown = (event: KeyboardEvent) => {
      const activeElement = document.activeElement;
      if (
        containerRef.current &&
        activeElement &&
        activeElement !== document.body &&
        activeElement !== document.documentElement &&
        !containerRef.current.contains(activeElement)
      ) {
        return;
      }

      if (event.key === 'Escape') {
        setSelectedKey(null);
        return;
      }

      if (
        activeElement instanceof HTMLInputElement ||
        activeElement instanceof HTMLSelectElement ||
        activeElement instanceof HTMLTextAreaElement ||
        (activeElement instanceof HTMLElement && activeElement.isContentEditable)
      ) {
        return;
      }

      const moveSelection = (direction: 'up' | 'down') => {
        const currentIndex = displayItems.findIndex((entry) => entry.item.key === selectedKey);
        const fallbackIndex = currentIndex === -1 ? 0 : currentIndex;
        const nextIndex =
          direction === 'down'
            ? Math.min(displayItems.length - 1, fallbackIndex + 1)
            : Math.max(0, fallbackIndex - 1);

        setSelectedKey(displayItems[nextIndex]?.item.key || null);
        setHasInitializedSelection(true);
      };

      const skipSelected = () => {
        if (!selectedKey) {
          return;
        }

        setSkippedKeys((prev) => ({ ...prev, [selectedKey]: true }));

        const currentIndex = displayItems.findIndex((entry) => entry.item.key === selectedKey);
        const fallbackIndex = currentIndex === -1 ? 0 : currentIndex;
        const next =
          displayItems
            .slice(fallbackIndex + 1)
            .find((entry) => entry.item.key !== selectedKey && !skippedKeys[entry.item.key]) ||
          displayItems
            .slice(0, fallbackIndex)
            .find((entry) => entry.item.key !== selectedKey && !skippedKeys[entry.item.key]);

        setSelectedKey(next?.item.key || null);
        setHasInitializedSelection(true);
      };

      if (event.key === 'ArrowDown') {
        event.preventDefault();
        moveSelection('down');
        return;
      }

      if (event.key === 'ArrowUp') {
        event.preventDefault();
        moveSelection('up');
        return;
      }

      if (event.metaKey || event.ctrlKey || event.altKey) {
        return;
      }

      if (event.key.length !== 1) {
        return;
      }

      const key = event.key.toLowerCase();
      if (key === 's') {
        skipSelected();
        return;
      }

      const selectedEntry = displayItems.find((entry) => entry.item.key === selectedKey);
      const focusedItem = selectedEntry?.item || null;
      if (!focusedItem || isApplying) {
        return;
      }

      if (focusedItem.kind === 'transfer') {
        if (key === 'c') {
          skipSelected();
          void runAction(
            () => apiClient.confirmTransferLink(focusedItem.item.id),
            'Transfer confirmed.',
          );
        }
        if (key === 'r') {
          skipSelected();
          void runAction(
            () => apiClient.rejectTransferLink(focusedItem.item.id),
            'Transfer suggestion rejected.',
          );
        }
        return;
      }

      if (focusedItem.kind === 'exception') {
        const suggestedCategory = getExceptionSuggestedCategory(focusedItem.item);
        const duplicateAllowed = canMarkDuplicate(focusedItem.item);
        const isAnomaly = focusedItem.section === 'anomalies';

        if (key === 'a' && suggestedCategory && focusedItem.item.entity_type === 'transaction') {
          skipSelected();
          void runAction(
            () => apiClient.approveExceptionCategory(focusedItem.item.id),
            `Category approved: ${suggestedCategory}.`,
          );
          return;
        }

        if (key === 'x') {
          skipSelected();
          void runAction(
            () => apiClient.resolveException(focusedItem.item.id),
            isAnomaly ? 'Anomaly resolved.' : 'Exception resolved.',
          );
          return;
        }

        if (key === 'i' && isAnomaly) {
          skipSelected();
          void runAction(() => apiClient.ignoreException(focusedItem.item.id), 'Anomaly ignored.');
          return;
        }

        if (key === 'd' && duplicateAllowed) {
          skipSelected();
          void runAction(() => apiClient.markExceptionDuplicate(focusedItem.item.id), 'Marked as duplicate.');
        }
        return;
      }

      if (focusedItem.kind === 'transaction') {
        const needsCategory = selectedEntry?.lane === 'category';

        if (key === 'a' && needsCategory) {
          skipSelected();
          void runAction(
            () => apiClient.approveTransactionCategory(focusedItem.item.id),
            `Category approved: ${getTransactionSuggestedCategory(focusedItem.item)}.`,
          );
          return;
        }

        if (key === 'x' && !needsCategory) {
          skipSelected();
          void runAction(
            () => apiClient.markTransactionReviewed(focusedItem.item.id),
            'Transaction marked as reviewed.',
          );
          return;
        }

        if (key === 'd') {
          skipSelected();
          void runAction(() => apiClient.markTransactionDuplicate(focusedItem.item.id), 'Marked as duplicate.');
        }
      }
    };

    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [displayItems, isApplying, runAction, selectedKey, skippedKeys]);

  const selectedEntry = useMemo(
    () => displayItems.find((entry) => entry.item.key === selectedKey) || null,
    [displayItems, selectedKey],
  );

  const selectedItem = selectedEntry?.item || null;

  const totalCount = displayItems.length;
  const lastImportText = lastImportAt ? formatDateTime(lastImportAt) : 'No completed imports yet';

  const representedAccountLabels = useMemo(() => {
    const accountIds = new Set<string>();

    displayItems.forEach((entry) => {
      const item = entry.item;
      switch (item.kind) {
        case 'transaction':
          if (item.item.account_id) {
            accountIds.add(item.item.account_id);
          }
          return;
        case 'exception':
          if (item.relatedTx?.account_id) {
            accountIds.add(item.relatedTx.account_id);
          }
          return;
        case 'transfer':
          if (item.outTx?.account_id) {
            accountIds.add(item.outTx.account_id);
          }
          if (item.inTx?.account_id) {
            accountIds.add(item.inTx.account_id);
          }
      }
    });

    return Array.from(accountIds)
      .map((accountId) => accountById.get(accountId))
      .filter((account): account is AccountSummary => Boolean(account))
      .map((account) => getAccountLabel(account))
      .sort((left, right) => left.localeCompare(right, 'en'));
  }, [accountById, displayItems]);

  const selectItem = (key: string) => {
    setSelectedKey(key);
    setHasInitializedSelection(true);
  };

  const selectedLaneLabel = selectedEntry ? getReviewLaneLabel(selectedEntry.lane) : '';

  const detailTitle = selectedItem ? `${selectedLaneLabel} detail` : 'Detail';

  const laneCounts = useMemo(() => {
    const counts: Record<ReviewLane, number> = {
      anomaly: 0,
      transfer: 0,
      exception: 0,
      category: 0,
      review: 0,
    };

    displayItems.forEach((entry) => {
      counts[entry.lane] = (counts[entry.lane] || 0) + 1;
    });

    return counts;
  }, [displayItems]);

  const selectedIndex = selectedKey
    ? displayItems.findIndex((entry) => entry.item.key === selectedKey)
    : -1;
  const selectedPosition = selectedIndex >= 0 ? `${selectedIndex + 1} of ${totalCount}` : null;
  const laneSummaryLabel = [
    `Transfers ${formatCount(laneCounts.transfer)}`,
    `Exceptions ${formatCount(laneCounts.exception)}`,
    `Category ${formatCount(laneCounts.category)}`,
    `Anomalies ${formatCount(laneCounts.anomaly)}`,
  ].join(' · ');

  useEffect(() => {
    if (!selectedKey) {
      return;
    }

    const selector = `[data-review-item-key="${selectedKey.replace(/"/g, '\\"')}"]`;
    const node = document.querySelector(selector);
    if (node && node instanceof HTMLElement && typeof node.scrollIntoView === 'function') {
      node.scrollIntoView({ block: 'nearest' });
    }
  }, [selectedKey]);

  const skipSelectedItem = () => {
    if (!selectedKey) {
      return;
    }

    setSkippedKeys((prev) => ({ ...prev, [selectedKey]: true }));

    const currentIndex = displayItems.findIndex((entry) => entry.item.key === selectedKey);
    const fallbackIndex = currentIndex === -1 ? 0 : currentIndex;
    const next =
      displayItems
        .slice(fallbackIndex + 1)
        .find((entry) => entry.item.key !== selectedKey && !skippedKeys[entry.item.key]) ||
      displayItems
        .slice(0, fallbackIndex)
        .find((entry) => entry.item.key !== selectedKey && !skippedKeys[entry.item.key]);

    setSelectedKey(next?.item.key || null);
    setHasInitializedSelection(true);
  };

  const runActionWithAdvance = (action: () => Promise<unknown>, successMessage: string) => {
    skipSelectedItem();
    void runAction(action, successMessage);
  };

  return (
    <section className="review-view fade-in" ref={containerRef}>
      <article className="ledger-document review-document">
        <header className="ledger-doc-head">
          <div className="ledger-section-copy">
            <h2>Review</h2>
            <span className="ledger-note">Items pending owner decision.</span>
          </div>
          <div className="review-doc-actions">
            <span className="ledger-doc-sub">
              Folio III · {totalCount > 0 ? `${formatCount(totalCount)} open items` : 'Queue clear'}
            </span>
            <button
              type="button"
              className="review-refresh"
              onClick={() => void fetchQueue()}
              disabled={isLoading || isApplying}
            >
              Refresh
            </button>
          </div>
        </header>

        {feedbackMessage ? <div className="wf-feedback is-success">{feedbackMessage}</div> : null}
        {errorMessage ? <div className="wf-feedback is-error">{errorMessage}</div> : null}

        <section className="review-summary-strip" aria-label="Queue summary">
          <div className="review-summary-cell">
            <span className="review-summary-label">Unresolved</span>
            <strong className="review-summary-value">
              {totalCount > 0 ? `${formatCount(totalCount)} unresolved` : 'Queue clear'}
            </strong>
            <span className="review-summary-note">Active items awaiting a decision</span>
          </div>
          <div className="review-summary-cell">
            <span className="review-summary-label">Lane counts</span>
            <strong className="review-summary-value">{laneSummaryLabel}</strong>
            <span className="review-summary-note">Merged from transfers, exceptions, and review rows</span>
          </div>
          <div className="review-summary-cell">
            <span className="review-summary-label">Last import</span>
            <strong className="review-summary-value">{lastImportText}</strong>
            <span className="review-summary-note">Most recent completed statement intake</span>
          </div>
          <div className="review-summary-cell">
            <span className="review-summary-label">Accounts</span>
            <strong className="review-summary-value">{formatCount(representedAccountLabels.length)}</strong>
            <span className="review-summary-note">
              {representedAccountLabels.length ? representedAccountLabels.join(' · ') : 'No linked accounts in view'}
            </span>
          </div>
        </section>

        <div className="review-layout">
          <div className="review-inbox" aria-label="Unresolved items">
            <div className="review-inbox-head">
              <div className="ledger-section-copy">
                <h3>Queue order</h3>
                <span className="ledger-section-note">Arrow keys move selection · C/R/A/X/I/D apply the current action · S skips.</span>
              </div>
              <span className="review-selected-meta">{selectedPosition || 'Awaiting selection'}</span>
            </div>

            {isLoading && !displayItems.length ? (
              <div className="review-empty review-empty-panel">Loading…</div>
            ) : null}

            {!isLoading && !displayItems.length ? (
              <div className="review-empty review-empty-panel">No unresolved items.</div>
            ) : null}

            {displayItems.length ? (
              <ul className="review-queue-list" aria-label="Review queue">
              {displayItems.map((entry) => {
                const queueItem = entry.item;
                const isSelected = queueItem.key === selectedKey;
                const laneLabel = getReviewLaneLabel(entry.lane);

                if (queueItem.kind === 'transfer') {
                  const outAccount = queueItem.outTx?.account_id
                    ? accountById.get(queueItem.outTx.account_id)
                    : null;
                  const inAccount = queueItem.inTx?.account_id
                    ? accountById.get(queueItem.inTx.account_id)
                    : null;
                  const outSigned = queueItem.outTx ? getTransactionSignedAmount(queueItem.outTx) : null;
                  const inSigned = queueItem.inTx ? getTransactionSignedAmount(queueItem.inTx) : null;
                  const delta = getTransferDelta(queueItem.outTx, queueItem.inTx);
                  const isSameAccount =
                    queueItem.outTx?.account_id &&
                    queueItem.inTx?.account_id &&
                    queueItem.outTx.account_id === queueItem.inTx.account_id;
                  const isSameProvider =
                    outAccount &&
                    inAccount &&
                    outAccount.provider.toLowerCase() === inAccount.provider.toLowerCase();

                  const scopeLabel = isSameAccount
                    ? 'Same account'
                    : isSameProvider
                      ? 'Same bank'
                      : outAccount && inAccount
                        ? 'Cross-bank'
                        : 'Accounts missing';
                  const scopeTone = isSameAccount ? 'tone-warn' : 'tone-muted';
                  const confidenceClass = `tone-${confidenceTone(queueItem.item.match_score)}`;
                  const deltaClass = deltaTone(delta) === 'ok' ? 'tone-muted' : 'tone-warn';

                  return (
                    <li
                      key={queueItem.key}
                      className={isSelected ? 'review-row is-selected' : 'review-row'}
                      data-review-item-key={queueItem.key}
                      data-review-lane={entry.lane}
                      aria-selected={isSelected}
                    >
                      <button
                        type="button"
                        className="review-row-main"
                        onClick={() => selectItem(queueItem.key)}
                      >
                        <div className="review-row-topline">
                          <div className="review-row-kicker">
                            <span className="review-row-eyebrow">{laneLabel}</span>
                            <span className="review-row-badges" aria-label="Match signals">
                              <span className={`review-badge review-badge-scope ${scopeTone}`.trim()}>
                                {scopeLabel}
                              </span>
                              <span className={`review-badge review-badge-metric ${deltaClass}`.trim()}>
                                Δ {formatMoney(delta)}
                              </span>
                              <span
                                className={`review-badge review-badge-metric ${confidenceClass}`.trim()}
                              >
                                Confidence {transferMatchScoreLabel(queueItem.item.match_score)}
                              </span>
                            </span>
                          </div>
                          <strong>{getTransferPairTitle(queueItem.outTx, queueItem.inTx)}</strong>
                        </div>
                        <div className="review-row-meta">
                          <span className="review-row-transfer-line">
                            <span className="review-badge review-badge-direction tone-out">Out</span>
                            {providerBadge(outAccount)}
                            <span className="review-row-transfer-amount tone-out">
                              {outSigned === null ? '—' : formatMoney(outSigned)}
                            </span>
                            <span className="review-row-transfer-account">
                              {getAccountIdentity(outAccount)}
                            </span>
                          </span>
                          <span className="review-row-transfer-line">
                            <span className="review-badge review-badge-direction tone-in">In</span>
                            {providerBadge(inAccount)}
                            <span className="review-row-transfer-amount tone-in">
                              {inSigned === null ? '—' : formatMoney(inSigned)}
                            </span>
                            <span className="review-row-transfer-account">
                              {getAccountIdentity(inAccount)}
                            </span>
                          </span>
                        </div>
                      </button>
                    </li>
                  );
                }

                if (queueItem.kind === 'exception') {
                  const suggestedCategory = getExceptionSuggestedCategory(queueItem.item);
                  const severity = (queueItem.item.severity || '').trim();

                  return (
                    <li
                      key={queueItem.key}
                      className={isSelected ? 'review-row is-selected' : 'review-row'}
                      data-review-item-key={queueItem.key}
                      data-review-lane={entry.lane}
                      aria-selected={isSelected}
                    >
                      <button
                        type="button"
                        className="review-row-main"
                        onClick={() => selectItem(queueItem.key)}
                      >
                        <div className="review-row-topline">
                          <span className="review-row-eyebrow">{laneLabel}</span>
                          <strong>{humanizeExceptionType(queueItem.item.exception_type)}</strong>
                        </div>
                        <p className="review-row-text">{queueItem.item.rationale || 'Needs a decision.'}</p>
                        <div className="review-row-meta">
                          {severity ? <span>Severity {severity}</span> : null}
                          {queueItem.relatedTx ? (
                            <span>
                              {formatMoney(getTransactionSignedAmount(queueItem.relatedTx))} ·{' '}
                              {queueItem.relatedTx.account_id
                                ? getAccountLabel(accountById.get(queueItem.relatedTx.account_id))
                                : 'Account unavailable'}
                            </span>
                          ) : null}
                          {queueItem.item.entity_type && queueItem.item.entity_id ? (
                            <span>
                              {titleCase(queueItem.item.entity_type)} {queueItem.item.entity_id}
                            </span>
                          ) : null}
                          {suggestedCategory ? <span>Suggested {suggestedCategory}</span> : null}
                        </div>
                      </button>
                    </li>
                  );
                }

                const tx = queueItem.item;
                const account = tx.account_id ? accountById.get(tx.account_id) : null;
                const suggestedCategory = getTransactionSuggestedCategory(tx);

                return (
                  <li
                    key={queueItem.key}
                    className={isSelected ? 'review-row is-selected' : 'review-row'}
                    data-review-item-key={queueItem.key}
                    data-review-lane={entry.lane}
                    aria-selected={isSelected}
                  >
                    <button
                      type="button"
                      className="review-row-main"
                      onClick={() => selectItem(queueItem.key)}
                    >
                      <div className="review-row-topline">
                        <span className="review-row-eyebrow">{laneLabel}</span>
                        <strong>{getTransactionTitle(tx)}</strong>
                      </div>
                      {entry.lane === 'category' ? (
                        <p className="review-row-text">Suggested {suggestedCategory}</p>
                      ) : entry.lane === 'review' ? (
                        <p className="review-row-text">Needs review.</p>
                      ) : null}
                      <div className="review-row-meta">
                        <span>
                          {formatMoney(getTransactionSignedAmount(tx))} ·{' '}
                          {account ? getAccountLabel(account) : 'Account unavailable'}
                        </span>
                        <span>{getStatementProvenance(tx)}</span>
                      </div>
                    </button>
                  </li>
                );
              })}
            </ul>
            ) : null}
          </div>

          <aside className="review-detail" aria-label={detailTitle}>
            {selectedItem ? (
              <>
                <div className="review-detail-header">
                  <div>
                    <p>
                      {selectedLaneLabel}
                      {selectedPosition ? ` · Item ${selectedPosition}` : ''}
                    </p>
                    <h3>
                      {selectedItem.kind === 'transaction'
                        ? getTransactionTitle(selectedItem.item)
                        : selectedItem.kind === 'transfer'
                          ? getTransferPairTitle(selectedItem.outTx, selectedItem.inTx)
                          : humanizeExceptionType(selectedItem.item.exception_type)}
                    </h3>
                  </div>
                  <button type="button" onClick={() => setSelectedKey(null)}>
                    Close
                  </button>
                </div>

                <div className="review-detail-actions" role="group" aria-label="Actions">
                {selectedItem.kind === 'transfer' ? (
                  <>
                    <button
                      type="button"
                      className="review-primary-action"
                      aria-keyshortcuts="c"
                      onClick={() =>
                        runActionWithAdvance(
                          () => apiClient.confirmTransferLink(selectedItem.item.id),
                          'Transfer confirmed.',
                        )
                      }
                      disabled={isApplying}
                    >
                      Confirm
                    </button>
                    <button
                      type="button"
                      className="ghost"
                      aria-keyshortcuts="r"
                      onClick={() =>
                        runActionWithAdvance(
                          () => apiClient.rejectTransferLink(selectedItem.item.id),
                          'Transfer suggestion rejected.',
                        )
                      }
                      disabled={isApplying}
                    >
                      Reject
                    </button>
                    <button
                      type="button"
                      className="ghost"
                      aria-keyshortcuts="s"
                      onClick={skipSelectedItem}
                      disabled={isApplying}
                    >
                      Skip
                    </button>
                  </>
                ) : null}

                {selectedItem.kind === 'exception' ? (
                  (() => {
                    const suggestedCategory = getExceptionSuggestedCategory(selectedItem.item);
                    const duplicateAllowed = canMarkDuplicate(selectedItem.item);
                    const isAnomaly = selectedItem.section === 'anomalies';

                    return (
                      <>
                        {suggestedCategory && selectedItem.item.entity_type === 'transaction' ? (
                          <button
                            type="button"
                            className="review-primary-action"
                            aria-keyshortcuts="a"
                            onClick={() =>
                              runActionWithAdvance(
                                () => apiClient.approveExceptionCategory(selectedItem.item.id),
                                `Category approved: ${suggestedCategory}.`,
                              )
                            }
                            disabled={isApplying}
                          >
                            Assign {suggestedCategory}
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="review-primary-action"
                            aria-keyshortcuts="x"
                            onClick={() =>
                              runActionWithAdvance(
                                () => apiClient.resolveException(selectedItem.item.id),
                                isAnomaly ? 'Anomaly resolved.' : 'Exception resolved.',
                              )
                            }
                            disabled={isApplying}
                          >
                            Resolve
                          </button>
                        )}

                        {suggestedCategory && selectedItem.item.entity_type === 'transaction' ? (
                          <button
                            type="button"
                            className="ghost"
                            aria-keyshortcuts="x"
                            onClick={() =>
                              runActionWithAdvance(
                                () => apiClient.resolveException(selectedItem.item.id),
                                isAnomaly ? 'Anomaly resolved.' : 'Exception resolved.',
                              )
                            }
                            disabled={isApplying}
                          >
                            Resolve
                          </button>
                        ) : null}

                        {isAnomaly ? (
                          <button
                            type="button"
                            className="ghost"
                            aria-keyshortcuts="i"
                            onClick={() =>
                              runActionWithAdvance(
                                () => apiClient.ignoreException(selectedItem.item.id),
                                'Anomaly ignored.',
                              )
                            }
                            disabled={isApplying}
                          >
                            Ignore
                          </button>
                        ) : null}

                        {duplicateAllowed ? (
                          <button
                            type="button"
                            className="ghost"
                            aria-keyshortcuts="d"
                            onClick={() =>
                              runActionWithAdvance(
                                () => apiClient.markExceptionDuplicate(selectedItem.item.id),
                                'Marked as duplicate.',
                              )
                            }
                            disabled={isApplying}
                          >
                            Mark duplicate
                          </button>
                        ) : null}

                        <button
                          type="button"
                          className="ghost"
                          aria-keyshortcuts="s"
                          onClick={skipSelectedItem}
                          disabled={isApplying}
                        >
                          Skip
                        </button>
                      </>
                    );
                  })()
                ) : null}

                {selectedItem.kind === 'transaction' ? (
                  (() => {
                    const lane = getReviewLane(selectedItem);
                    const needsCategory = lane === 'category';
                    const suggestedCategory = getTransactionSuggestedCategory(selectedItem.item);

                    return (
                      <>
                        <button
                          type="button"
                          className="review-primary-action"
                          aria-keyshortcuts={needsCategory ? 'a' : 'x'}
                          onClick={() =>
                            runActionWithAdvance(
                              () =>
                                needsCategory
                                  ? apiClient.approveTransactionCategory(selectedItem.item.id)
                                  : apiClient.markTransactionReviewed(selectedItem.item.id),
                              needsCategory
                                ? `Category approved: ${suggestedCategory}.`
                                : 'Transaction marked as reviewed.',
                            )
                          }
                          disabled={isApplying}
                        >
                          {needsCategory ? `Assign ${suggestedCategory}` : 'Mark reviewed'}
                        </button>
                        <button
                          type="button"
                          className="ghost"
                          aria-keyshortcuts="d"
                          onClick={() =>
                            runActionWithAdvance(
                              () => apiClient.markTransactionDuplicate(selectedItem.item.id),
                              'Marked as duplicate.',
                            )
                          }
                          disabled={isApplying}
                        >
                          Mark duplicate
                        </button>
                        <button
                          type="button"
                          className="ghost"
                          aria-keyshortcuts="s"
                          onClick={skipSelectedItem}
                          disabled={isApplying}
                        >
                          Skip
                        </button>
                      </>
                    );
                  })()
                ) : null}
              </div>

                <div className="review-detail-body">
                {selectedItem.kind === 'transfer' ? (
                  <>
                    <section className="review-detail-card">
                      <div className="review-detail-eyebrow">Match</div>
                      <strong>Potential internal transfer</strong>
                      <p className="review-transfer-guidance">
                        Compare the Outflow and Inflow rows below. If they represent the same movement between your
                        accounts, confirm. If they are unrelated, reject.
                      </p>
                      <dl className="review-detail-facts review-transfer-facts">
                        <div>
                          <dt>Confidence</dt>
                          <dd>{transferMatchScoreLabel(selectedItem.item.match_score)}</dd>
                        </div>
                        <div>
                          <dt>Amount Δ</dt>
                          <dd>
                            {selectedItem.outTx && selectedItem.inTx
                              ? formatMoney(getTransferDelta(selectedItem.outTx, selectedItem.inTx))
                              : '—'}
                          </dd>
                        </div>
                        <div>
                          <dt>Time Δ</dt>
                          <dd>{getTransferTimeDeltaLabel(selectedItem.outTx, selectedItem.inTx)}</dd>
                        </div>
                        <div>
                          <dt>Fee</dt>
                          <dd>
                            {selectedItem.item.fee_amount !== null &&
                            selectedItem.item.fee_amount !== undefined
                              ? formatMoney(selectedItem.item.fee_amount)
                              : '—'}
                          </dd>
                        </div>
                      </dl>

                      {selectedItem.item.rationale ? (
                        looksLikeTransferDebugRationale(selectedItem.item.rationale) ? (
                          <details className="review-detail-debug">
                            <summary>Technical match details</summary>
                            <pre>{selectedItem.item.rationale}</pre>
                          </details>
                        ) : (
                          <p className="review-transfer-hint">{selectedItem.item.rationale}</p>
                        )
                      ) : null}
                      {(selectedItem.outTx?.id || selectedItem.inTx?.id) ? (
                        <button
                          type="button"
                          className="review-detail-link"
                          onClick={() =>
                            navigate('/transactions', {
                              state: {
                                openTransactionId:
                                  selectedItem.outTx?.id || selectedItem.inTx?.id || undefined,
                              },
                            })
                          }
                        >
                          Open in Transactions
                        </button>
                      ) : null}
                    </section>

                    {renderTransactionDetailCard(
                      selectedItem.outTx,
                      selectedItem.outTx?.account_id
                        ? accountById.get(selectedItem.outTx.account_id)
                        : null,
                      'Outflow',
                    )}
                    {renderTransactionDetailCard(
                      selectedItem.inTx,
                      selectedItem.inTx?.account_id
                        ? accountById.get(selectedItem.inTx.account_id)
                        : null,
                      'Inflow',
                    )}
                  </>
                ) : null}

                {selectedItem.kind === 'exception' ? (
                  <>
                    <section className="review-detail-card">
                      <div className="review-detail-eyebrow">
                        {selectedItem.section === 'anomalies' ? 'Anomaly' : 'Exception'}
                      </div>
                      <strong>{humanizeExceptionType(selectedItem.item.exception_type)}</strong>
                      <p>{selectedItem.item.rationale || 'No rationale recorded.'}</p>
                      <dl className="review-detail-facts">
                        <div>
                          <dt>Status</dt>
                          <dd>{selectedItem.item.status}</dd>
                        </div>
                        <div>
                          <dt>Severity</dt>
                          <dd>{selectedItem.item.severity}</dd>
                        </div>
                        <div>
                          <dt>Suggested category</dt>
                          <dd>{getExceptionSuggestedCategory(selectedItem.item) || '—'}</dd>
                        </div>
                      </dl>
                    </section>

                    {renderTransactionDetailCard(
                      selectedItem.relatedTx,
                      selectedItem.relatedTx?.account_id
                        ? accountById.get(selectedItem.relatedTx.account_id)
                        : null,
                      'Linked transaction',
                    )}

                    {selectedItem.relatedTx?.id ? (
                      <button
                        type="button"
                        className="review-detail-link"
                        onClick={() =>
                          navigate('/transactions', {
                            state: {
                              openTransactionId: selectedItem.relatedTx?.id,
                            },
                          })
                        }
                      >
                        Open in Transactions
                      </button>
                    ) : null}
                  </>
                ) : null}

                {selectedItem.kind === 'transaction' ? (
                  <>
                    {renderTransactionDetailCard(
                      selectedItem.item,
                      selectedItem.item.account_id ? accountById.get(selectedItem.item.account_id) : null,
                      isCategoryEmpty(selectedItem.item) ? 'Needs category' : 'Needs review',
                    )}
                    <section className="review-detail-card">
                      <div className="review-detail-eyebrow">Resolution hint</div>
                      <strong>
                        {isCategoryEmpty(selectedItem.item)
                          ? `Assign category: ${getTransactionSuggestedCategory(selectedItem.item)}`
                          : 'Mark reviewed.'}
                      </strong>
                      <p>{getTransactionReviewReason(selectedItem.item)}</p>
                      <button
                        type="button"
                        className="review-detail-link"
                        onClick={() =>
                          navigate('/transactions', {
                            state: { openTransactionId: selectedItem.item.id },
                          })
                        }
                      >
                        Open in Transactions
                      </button>
                    </section>
                  </>
                ) : null}
                </div>
              </>
            ) : (
              <div className="review-detail-empty">
                <h3>Detail drawer</h3>
                <p>Select a queue item.</p>
              </div>
            )}
          </aside>
        </div>
      </article>
    </section>
  );
}
