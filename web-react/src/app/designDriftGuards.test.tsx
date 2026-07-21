import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it } from 'vitest';
import App from '../App';
import { apiClientMock } from '../test/apiClientMock';

const FORBIDDEN_COPY_PATTERNS = [
  /\bcurator\b/i,
  /\bmarket\s+data\b/i,
  /\bvault\b/i,
  /\bintelligence\b/i,
  /\byield\b/i,
  /\bpremium\s+insights\b/i,
  /\bassets\b/i,
  /\bpredictive\s+curation\b/i,
];

function expectNoForbiddenCopy(root: HTMLElement) {
  const visibleText = (root.textContent || '').replace(/\s+/g, ' ').trim();
  FORBIDDEN_COPY_PATTERNS.forEach((pattern) => {
    expect(visibleText).not.toMatch(pattern);
  });
}

describe('Design drift guardrails', () => {
  beforeEach(() => {
    apiClientMock.accounts.mockResolvedValue([]);
    apiClientMock.monthlyFlow.mockResolvedValue({ generated_at: '2026-03-15T00:00:00Z', items: [] });
    apiClientMock.transactions.mockResolvedValue({ total: 0, items: [] });
    apiClientMock.netWorthCurrent.mockResolvedValue({ totals: [], accounts: [] });
    apiClientMock.netWorthTimeline.mockResolvedValue({
      series: [
        {
          currency: 'RUB',
          points: [
            {
              timestamp: '2026-03-15T10:00:00Z',
              total_balance: 320000,
              accounts_total: 1,
              accounts_with_snapshot: 1,
              accounts_missing: 0,
              completeness: 1,
            },
            {
              timestamp: '2026-03-16T10:00:00Z',
              total_balance: 345000,
              accounts_total: 1,
              accounts_with_snapshot: 1,
              accounts_missing: 0,
              completeness: 1,
            },
          ],
        },
      ],
    });
    apiClientMock.exceptions.mockResolvedValue([]);
    apiClientMock.transferLinks.mockResolvedValue([]);
    apiClientMock.importBatches.mockResolvedValue([]);
    apiClientMock.statements.mockResolvedValue([]);
    apiClientMock.statementRows.mockResolvedValue([]);
  });

  it.each([
    {
      route: '/overview',
      ready: async () => {
        await screen.findByRole('img', { name: 'Monthly spending trend' });
        await waitFor(() => {
          expect(apiClientMock.monthlyFlow).toHaveBeenCalledTimes(1);
        });
      },
    },
    {
      route: '/transactions',
      ready: async () => {
        await screen.findByRole('searchbox', { name: 'Поиск' });
        await waitFor(() => {
          expect(apiClientMock.transactions).toHaveBeenCalled();
        });
      },
    },
    {
      route: '/review',
      ready: async () => {
        await screen.findByLabelText('Queue summary');
        await waitFor(() => {
          expect(apiClientMock.exceptions).toHaveBeenCalledWith({ status: 'open' });
        });
      },
    },
    {
      route: '/accounts',
      ready: async () => {
        await screen.findByRole('img', { name: 'Balance over time' });
        await waitFor(() => {
          expect(apiClientMock.netWorthCurrent).toHaveBeenCalledTimes(1);
        });
      },
    },
    {
      route: '/statements',
      ready: async () => {
        await screen.findByRole('heading', { name: 'Import batches' });
        await waitFor(() => {
          expect(apiClientMock.importBatches).toHaveBeenCalledWith({ limit: 30 });
        });
      },
    },
  ])('does not introduce forbidden investment copy on $route', async ({ route, ready }) => {
    render(
      <MemoryRouter initialEntries={[route]}>
        <App />
      </MemoryRouter>,
    );

    await ready();
    expectNoForbiddenCopy(document.body);
  });
});
