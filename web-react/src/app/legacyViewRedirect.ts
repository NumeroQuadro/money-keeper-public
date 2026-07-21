export type LegacyQueueKind = 'exception' | 'transfer';

export interface LegacyReviewState {
  openQueueItemId?: string;
  openQueueKind?: LegacyQueueKind;
  openTransactionId?: string;
}

export interface LegacyRedirectTarget {
  to: string;
  state?: LegacyReviewState;
}

function normalizeView(value: string | null): string {
  return (value || '').trim().toLowerCase();
}

function normalizeId(value: string | null): string {
  return (value || '').trim();
}

export function resolveLegacyViewRedirect(search: string): LegacyRedirectTarget {
  const params = new URLSearchParams(search);
  const view = normalizeView(params.get('view'));
  const itemId = normalizeId(params.get('id'));

  switch (view) {
    case 'overview':
    case '':
      return { to: '/overview' };
    case 'transactions':
      return itemId
        ? {
            to: '/transactions',
            state: { openTransactionId: itemId },
          }
        : { to: '/transactions' };
    case 'review':
    case 'exceptions':
      return itemId
        ? {
            to: '/review',
            state: {
              openQueueItemId: itemId,
              openQueueKind: 'exception',
            },
          }
        : { to: '/review' };
    case 'transfers':
      return itemId
        ? {
            to: '/review',
            state: {
              openQueueItemId: itemId,
              openQueueKind: 'transfer',
            },
          }
        : { to: '/review' };
    case 'rules':
      return { to: '/settings/automation' };
    case 'net-worth':
    case 'networth':
    case 'accounts':
    case 'balances':
      return { to: '/accounts' };
    default:
      return { to: '/overview' };
  }
}
