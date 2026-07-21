import { describe, expect, it } from 'vitest';
import { resolveLegacyViewRedirect } from './legacyViewRedirect';

describe('resolveLegacyViewRedirect', () => {
  it('keeps default root landing on overview when no legacy params are present', () => {
    expect(resolveLegacyViewRedirect('')).toEqual({ to: '/overview' });
    expect(resolveLegacyViewRedirect('?unknown=1')).toEqual({ to: '/overview' });
  });

  it('maps legacy exception links to review queue focus', () => {
    expect(resolveLegacyViewRedirect('?view=exceptions&id=ex-42')).toEqual({
      to: '/review',
      state: {
        openQueueItemId: 'ex-42',
        openQueueKind: 'exception',
      },
    });
  });

  it('maps legacy transfer links to review queue focus', () => {
    expect(resolveLegacyViewRedirect('?view=transfers&id=link-9')).toEqual({
      to: '/review',
      state: {
        openQueueItemId: 'link-9',
        openQueueKind: 'transfer',
      },
    });
  });

  it('maps legacy transaction links to transactions drawer focus', () => {
    expect(resolveLegacyViewRedirect('?view=transactions&id=tx-7')).toEqual({
      to: '/transactions',
      state: {
        openTransactionId: 'tx-7',
      },
    });
  });

  it('supports legacy net worth aliases', () => {
    expect(resolveLegacyViewRedirect('?view=networth')).toEqual({ to: '/accounts' });
    expect(resolveLegacyViewRedirect('?view=balances')).toEqual({ to: '/accounts' });
  });

  it('routes legacy rules view into settings automation', () => {
    expect(resolveLegacyViewRedirect('?view=rules')).toEqual({ to: '/settings/automation' });
  });
});
