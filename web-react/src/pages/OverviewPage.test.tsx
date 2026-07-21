import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { formatMoney } from '../app/formatters';
import { apiClientMock } from '../test/apiClientMock';
import { OverviewPage } from './OverviewPage';

vi.mock('../app/dateRange', async () => {
  const actual = await vi.importActual<typeof import('../app/dateRange')>('../app/dateRange');
  return {
    ...actual,
    currentMonthValue: () => '2026-03',
  };
});

function LocationStateProbe() {
  const location = useLocation();
  return <pre data-testid="location-state">{JSON.stringify(location.state || {})}</pre>;
}

describe('OverviewPage', () => {
  beforeEach(() => {
    apiClientMock.accounts.mockResolvedValue([]);
    apiClientMock.monthlyFlow.mockResolvedValue({
      generated_at: '2026-03-15T00:00:00Z',
      items: [
        { period: '2026-01', inflow: 100000, outflow: 80000, net: 20000, tx_count: 42 },
        { period: '2026-02', inflow: 110000, outflow: 76000, net: 34000, tx_count: 38 },
        { period: '2026-03', inflow: 120000, outflow: 90000, net: 30000, tx_count: 40 },
      ],
    });
    apiClientMock.transactions.mockResolvedValue({
      total: 0,
      items: [],
    });
    apiClientMock.transactions.mockImplementation(async (query?: Record<string, unknown>) => {
      if (query?.needs_human_review === true) {
        return {
          total: 0,
          items: [],
        };
      }

      return {
        total: 2,
        items: [
          {
            id: 'tx-1',
            account_id: 'acc-1',
            operation_datetime: '2026-03-14T12:30:00',
            posting_datetime: '2026-03-14T12:30:00',
            amount: 3500,
            currency: 'RUB',
            direction: 'out',
            category: 'Продукты',
            description_raw: 'Самокат заказ',
            merchant_normalized: 'Самокат',
            bank_category: 'merchant',
            tags: ['еда'],
            meaning: 'spend',
            review_status: 'reviewed',
            review_reasons: [],
            needs_human_review: false,
          },
          {
            id: 'tx-2',
            account_id: 'acc-1',
            operation_datetime: '2026-03-12T09:15:00',
            posting_datetime: '2026-03-12T09:15:00',
            amount: 24000,
            currency: 'RUB',
            direction: 'in',
            category: 'Salary',
            description_raw: 'Salary',
            merchant_normalized: 'Employer',
            bank_category: 'income',
            tags: [],
            meaning: 'income',
            review_status: 'reviewed',
            review_reasons: [],
            needs_human_review: false,
          },
        ],
      };
    });
    apiClientMock.netWorthCurrent.mockResolvedValue({
      totals: [{ currency: 'RUB', total_balance: 345000 }],
      accounts: [
        {
          account_id: 'acc-1',
          provider: 'ozon',
          account_type: 'card',
          display_name: 'Everyday',
          masked_identifier: '1234',
          balance: 345000,
          currency: 'RUB',
          as_of: '2026-03-15T00:00:00Z',
        },
      ],
    });
    apiClientMock.exceptions.mockImplementation(async (query?: { status?: string }) => {
      if (query?.status === 'open') {
        return [
          {
            id: 'ex-1',
            status: 'open',
            exception_type: 'ambiguous_category',
            severity: 'medium',
            entity_type: 'transaction',
            entity_id: 'tx-1',
            rationale: 'Needs confirmation',
            payload: {},
          },
        ];
      }

      return [];
    });
    apiClientMock.transferLinks.mockResolvedValue([]);
  });

  it('renders the overview monthly story with one hero, one trend chart, supporting rail, and recent transactions', async () => {
    render(
      <MemoryRouter>
        <OverviewPage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(apiClientMock.monthlyFlow).toHaveBeenCalledTimes(1);
    });

    expect(await screen.findByText('Spent (excluding transfers)')).toBeInTheDocument();
    expect(screen.getByText('Income')).toBeInTheDocument();
    expect(screen.getByText('Net cashflow')).toBeInTheDocument();
    expect(screen.getByText('Needs review')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Monthly spending trend' })).toBeInTheDocument();
    expect(screen.getByRole('list', { name: 'Recent transactions' })).toBeInTheDocument();
  });

  it('meets the Overview page contract guardrails', async () => {
    render(
      <MemoryRouter>
        <OverviewPage />
      </MemoryRouter>,
    );

    await screen.findByText('Spent (excluding transfers)');

    const page = screen.getByTestId('overview-page');
    const top = screen.getByTestId('overview-top');

    expect(screen.getAllByTestId('overview-hero')).toHaveLength(1);

    const helperParagraphs = top.querySelectorAll('p');
    expect(helperParagraphs.length).toBeLessThanOrEqual(1);
    helperParagraphs.forEach((paragraph) => {
      expect(paragraph.textContent?.trim().length ?? 0).toBeLessThanOrEqual(120);
    });

    const forbidden = [
      /\bportfolio\b/i,
      /\binvest(ment|ing)?\b/i,
      /\bcrypto\b/i,
      /\bnft\b/i,
      /\bmarket data\b/i,
      /\byield\b/i,
      /\bintelligence\b/i,
      /\bvault\b/i,
      /\bcurator\b/i,
      /\bpremium insights\b/i,
      /\bassets\b/i,
      /\bpredictive curation\b/i,
    ];
    const visibleText = (page.textContent || '').replace(/\s+/g, ' ').trim();
    forbidden.forEach((pattern) => {
      expect(visibleText).not.toMatch(pattern);
    });

    expect(top).toMatchSnapshot();
  });

  it('opens the dedicated review workflow from the overview attention actions', async () => {
    const user = userEvent.setup();

    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes>
          <Route path="/overview" element={<OverviewPage />} />
          <Route path="/review" element={<LocationStateProbe />} />
        </Routes>
      </MemoryRouter>,
    );

    const buttons = await screen.findAllByRole('button', { name: 'Open review' });
    await user.click(buttons[0]);

    expect(await screen.findByTestId('location-state')).toHaveTextContent('"openTransactionId":"tx-1"');
  });

  it('keeps the review queue calm when nothing needs input', async () => {
    apiClientMock.exceptions.mockResolvedValue([]);
    apiClientMock.transferLinks.mockResolvedValue([]);

    render(
      <MemoryRouter>
        <OverviewPage />
      </MemoryRouter>,
    );

    expect(screen.queryByRole('button', { name: 'Open review' })).not.toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'All transactions' }).length).toBeGreaterThan(0);
  });

  it('updates the trend summary when hovering months in the chart', async () => {
    render(
      <MemoryRouter>
        <OverviewPage />
      </MemoryRouter>,
    );

    await screen.findByText('Spent (excluding transfers)');

    const chart = screen.getByRole('img', { name: 'Monthly spending trend' });
    const summary = document.querySelector('.overview-trend-summary');
    expect(summary).not.toBeNull();
    if (!summary) return;

    expect(summary.textContent || '').toContain('March 2026');
    expect(summary.textContent || '').toContain(formatMoney(90000));

    const janBar = chart.querySelector('rect[data-period="2026-01"]');
    expect(janBar).not.toBeNull();
    if (!janBar) return;

    fireEvent.pointerEnter(janBar);
    expect(summary.textContent || '').toContain('January 2026');
    expect(summary.textContent || '').toContain(formatMoney(80000));

    const svg = chart.querySelector('svg');
    expect(svg).not.toBeNull();
    if (!svg) return;

    fireEvent.pointerLeave(svg);
    expect(summary.textContent || '').toContain('March 2026');
    expect(summary.textContent || '').toContain(formatMoney(90000));
  });
});
