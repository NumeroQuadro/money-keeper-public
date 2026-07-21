import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it } from 'vitest';
import { apiClientMock } from '../test/apiClientMock';
import { NetWorthPage } from './NetWorthPage';

describe('AccountsPage', () => {
  beforeEach(() => {
    apiClientMock.netWorthCurrent.mockResolvedValue({
      totals: [{ currency: 'RUB', total_balance: 345000 }],
      accounts: [
        {
          account_id: 'acc-1',
          provider: 'ozon',
          account_type: 'card',
          display_name: 'Main account',
          masked_identifier: '**** 1234',
          balance: 125000,
          currency: 'RUB',
          as_of: '2026-03-16T10:00:00Z',
        },
      ],
    });

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
  });

  it('renders hero with total balance and account cards in the rail', async () => {
    render(
      <MemoryRouter>
        <NetWorthPage />
      </MemoryRouter>,
    );

    // Hero heading
    expect(await screen.findByRole('heading', { name: 'Accounts' })).toBeInTheDocument();
    expect(screen.getByText('Consolidated cash position and statement freshness by account.')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Account schedule' })).toBeInTheDocument();

    // Account card in rail
    expect(screen.getAllByText('ozon · card').length).toBeGreaterThan(0);
    expect(screen.getAllByText('**** 1234').length).toBeGreaterThan(0);
    expect(screen.getAllByRole('button', { name: 'Transactions' }).length).toBeGreaterThan(0);

    // Chart SVG
    expect(screen.getByRole('img', { name: 'Balance over time' })).toBeInTheDocument();

    // Granularity toggle present
    expect(screen.getByRole('button', { name: 'By statement', pressed: true })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Weekly' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Monthly' })).toBeInTheDocument();
  });

  it('date range inputs are hidden by default and shown when toggled', async () => {
    const user = userEvent.setup();

    render(
      <MemoryRouter>
        <NetWorthPage />
      </MemoryRouter>,
    );

    await screen.findByRole('heading', { name: 'Accounts' });

    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(screen.queryByText('From')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /set date range/i }));

    expect(await screen.findByText('From')).toBeInTheDocument();
    expect(screen.getByText('To')).toBeInTheDocument();
  });

  it('switching granularity and applying reloads timeline with correct params', async () => {
    const user = userEvent.setup();

    render(
      <MemoryRouter>
        <NetWorthPage />
      </MemoryRouter>,
    );

    await screen.findByRole('heading', { name: 'Accounts' });

    await user.click(screen.getByRole('button', { name: 'Monthly' }));
    await user.click(screen.getByRole('button', { name: 'Apply' }));

    await waitFor(() => {
      expect(apiClientMock.netWorthTimeline).toHaveBeenCalledWith(
        expect.objectContaining({ granularity: 'month' }),
      );
    });
  });

  it('masks account identifier by default', async () => {
    render(
      <MemoryRouter>
        <NetWorthPage />
      </MemoryRouter>,
    );

    expect((await screen.findAllByText('**** 1234')).length).toBeGreaterThan(0);
    expect(screen.queryByText('Main account')).not.toBeInTheDocument();
  });

  it('shows error state when API fails', async () => {
    apiClientMock.netWorthCurrent.mockRejectedValue(new Error('network'));
    apiClientMock.netWorthTimeline.mockRejectedValue(new Error('network'));

    render(
      <MemoryRouter>
        <NetWorthPage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('temporarily unavailable');
  });
});
