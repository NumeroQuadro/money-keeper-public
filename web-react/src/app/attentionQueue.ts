import type {
  ExceptionSummary,
  TransactionSummary,
  TransferLinkSummary,
} from '../api/client';

const ANOMALY_EXCEPTION_TYPES = new Set(['parsing_anomaly', 'reconciliation_mismatch']);

export interface AttentionTarget {
  path: '/transactions' | '/review';
  transactionId: string | null;
}

export function getSuggestedTransfers(items: TransferLinkSummary[]): TransferLinkSummary[] {
  return items.filter((item) => item.status === 'suggested');
}

export function isAnomalyException(item: ExceptionSummary): boolean {
  return ANOMALY_EXCEPTION_TYPES.has((item.exception_type || '').trim().toLowerCase());
}

function compareTransactionEvent(left: TransactionSummary, right: TransactionSummary): number {
  const leftKey = left.operation_datetime || left.posting_datetime || '';
  const rightKey = right.operation_datetime || right.posting_datetime || '';
  return rightKey.localeCompare(leftKey) || right.id.localeCompare(left.id);
}

export function mergeReviewTransactions(...groups: TransactionSummary[][]): TransactionSummary[] {
  const deduped = new Map<string, TransactionSummary>();

  groups.forEach((group) => {
    group.forEach((item) => {
      if (!item?.id || deduped.has(item.id)) {
        return;
      }
      deduped.set(item.id, item);
    });
  });

  return Array.from(deduped.values()).sort(compareTransactionEvent);
}

export function filterStandaloneReviewTransactions(
  items: TransactionSummary[],
  openExceptions: ExceptionSummary[],
  suggestedTransfers: TransferLinkSummary[],
): TransactionSummary[] {
  const queuedTransactionIds = new Set<string>();

  openExceptions.forEach((item) => {
    if (item.entity_type === 'transaction' && item.entity_id) {
      queuedTransactionIds.add(item.entity_id);
    }
  });

  suggestedTransfers.forEach((item) => {
    if (item.transaction_out_id) {
      queuedTransactionIds.add(item.transaction_out_id);
    }
    if (item.transaction_in_id) {
      queuedTransactionIds.add(item.transaction_in_id);
    }
  });

  return items.filter((item) => !queuedTransactionIds.has(item.id));
}

export function resolveAttentionTarget(
  openExceptions: ExceptionSummary[],
  suggestedTransfers: TransferLinkSummary[],
  reviewTransactions: TransactionSummary[] = [],
): AttentionTarget {
  const exceptionWithTransaction = openExceptions.find(
    (item) => item.entity_type === 'transaction' && item.entity_id,
  );
  if (exceptionWithTransaction?.entity_id) {
    return {
      path: '/review',
      transactionId: exceptionWithTransaction.entity_id,
    };
  }

  if (openExceptions.length > 0) {
    return {
      path: '/review',
      transactionId: null,
    };
  }

  const suggestedTransfer = suggestedTransfers.find(
    (item) => item.transaction_out_id || item.transaction_in_id,
  );
  const transferTransactionId = suggestedTransfer?.transaction_out_id || suggestedTransfer?.transaction_in_id;

  if (transferTransactionId) {
    return {
      path: '/review',
      transactionId: transferTransactionId,
    };
  }

  if (suggestedTransfers.length > 0) {
    return {
      path: '/review',
      transactionId: null,
    };
  }

  const firstReviewTransaction = reviewTransactions.find((item) => item.id);
  if (firstReviewTransaction?.id) {
    return {
      path: '/review',
      transactionId: firstReviewTransaction.id,
    };
  }

  if (reviewTransactions.length > 0) {
    return {
      path: '/review',
      transactionId: null,
    };
  }

  return {
    path: '/transactions',
    transactionId: null,
  };
}
