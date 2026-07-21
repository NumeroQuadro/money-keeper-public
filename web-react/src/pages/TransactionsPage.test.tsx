import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it } from 'vitest';
import { apiClientMock } from '../test/apiClientMock';
import { TransactionsPage } from './TransactionsPage';

function LocationStateProbe() {
  const location = useLocation();
  return <pre data-testid="location-state">{JSON.stringify(location.state || {})}</pre>;
}

describe('TransactionsPage', () => {
  beforeEach(() => {
    apiClientMock.accounts.mockResolvedValue([
      {
        id: 'acc-1',
        provider: 'ozon',
        account_type: 'card',
        display_name: 'Everyday',
        masked_identifier: '1234',
      },
    ]);

    const tx = {
      id: 'tx-1',
      account_id: 'acc-1',
      operation_datetime: '2026-03-14T12:30:00',
      posting_datetime: '2026-03-14T12:30:00',
      timestamp_precision: 'exact',
      amount: 4900,
      currency: 'RUB',
      direction: 'in',
      category: null,
      description_raw: 'Перевод от друга',
      merchant_normalized: 'Перевод от друга',
      bank_reference_id: 'ref-1',
      bank_category: 'transfer',
      tags: ['friend'],
      meaning: 'income',
      review_status: 'needs_review',
      source_statement_id: 'stmt-1',
      source_page_number: 2,
      source_row_index: 7,
    };

    apiClientMock.transactions.mockResolvedValue({
      total: 1,
      items: [tx],
    });
    apiClientMock.transactionById.mockResolvedValue(tx);
    apiClientMock.exceptions.mockResolvedValue([]);
    apiClientMock.transferLinks.mockResolvedValue([]);
  });

  it('renders a compact filter bar and keeps the register list first', async () => {
    render(
      <MemoryRouter initialEntries={['/transactions']}>
        <Routes>
          <Route path="/transactions" element={<TransactionsPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole('searchbox', { name: 'Поиск' })).toBeInTheDocument();
    expect(screen.getByLabelText('Период')).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Направление' })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'Учет переводов' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Фильтры' })).toBeInTheDocument();

    expect(await screen.findByText('Дата')).toBeInTheDocument();
    expect(screen.getByText('Операция')).toBeInTheDocument();
    expect(screen.getByText('Счет')).toBeInTheDocument();
    expect(screen.getByText('Категория')).toBeInTheDocument();
    expect(screen.getByText('Сумма')).toBeInTheDocument();

    expect(screen.queryByLabelText('Сводка журнала операций')).not.toBeInTheDocument();
    expect(screen.queryByText(/Откройте строку/i)).not.toBeInTheDocument();
  }, 10000);

  it('opens the period month picker', async () => {
    const user = userEvent.setup();

    render(
      <MemoryRouter initialEntries={['/transactions']}>
        <Routes>
          <Route path="/transactions" element={<TransactionsPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await user.click(await screen.findByRole('button', { name: 'Период' }));

    expect(await screen.findByRole('dialog', { name: 'Выбор месяца' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Янв' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Дек' })).toBeInTheDocument();
  }, 10000);

  it('opens a detail sheet on row click with provenance visible', async () => {
    const user = userEvent.setup();

    render(
      <MemoryRouter initialEntries={['/transactions']}>
        <Routes>
          <Route path="/transactions" element={<TransactionsPage />} />
          <Route path="/review" element={<LocationStateProbe />} />
        </Routes>
      </MemoryRouter>,
    );

    await user.click(await screen.findByRole('button', { name: /Перевод от друга/i }));

    expect(await screen.findByRole('heading', { name: 'Операция', level: 3 })).toBeInTheDocument();
    expect(await screen.findByText('Нужно внимание')).toBeInTheDocument();
    expect(await screen.findByText('Нужно ваше решение')).toBeInTheDocument();
    expect(await screen.findByText('Как считаем')).toBeInTheDocument();
    expect(await screen.findByText('Источник в выписке')).toBeInTheDocument();
    expect(screen.getByText(/stmt-1/i)).toBeInTheDocument();

    await user.click(await screen.findByRole('button', { name: 'Открыть проверку' }));
    expect(await screen.findByTestId('location-state')).toHaveTextContent('"openTransactionId":"tx-1"');
  }, 10000);

  it('internal transfer toggle changes the counted list correctly', async () => {
    const user = userEvent.setup();

    const regularTx = {
      id: 'tx-1',
      account_id: 'acc-1',
      operation_datetime: '2026-03-14T12:30:00',
      posting_datetime: '2026-03-14T12:30:00',
      timestamp_precision: 'exact',
      amount: 4900,
      currency: 'RUB',
      direction: 'in',
      category: null,
      description_raw: 'Перевод от друга',
      merchant_normalized: 'Перевод от друга',
      bank_reference_id: 'ref-1',
      bank_category: 'transfer',
      tags: ['friend'],
      meaning: 'income',
      review_status: 'needs_review',
      source_statement_id: 'stmt-1',
      source_page_number: 2,
      source_row_index: 7,
    };

    const internalTransfer = {
      id: 'tx-2',
      account_id: 'acc-1',
      operation_datetime: '2026-03-10T09:00:00',
      posting_datetime: '2026-03-10T09:00:00',
      timestamp_precision: 'exact',
      amount: 5000,
      currency: 'RUB',
      direction: 'out',
      category: null,
      description_raw: 'Перевод между счетами',
      merchant_normalized: 'Перевод между счетами',
      bank_reference_id: 'ref-2',
      bank_category: 'transfer',
      tags: [],
      meaning: 'internal_transfer',
      review_status: 'reviewed',
      source_statement_id: 'stmt-2',
      source_page_number: 1,
      source_row_index: 12,
    };

    apiClientMock.transactions.mockImplementation(async (query) => {
      const includeTransfers = (query || {})['include_transfers'] === 'true';
      return {
        total: includeTransfers ? 2 : 1,
        items: includeTransfers ? [regularTx, internalTransfer] : [regularTx],
      };
    });
    apiClientMock.transactionById.mockImplementation(async (id: string) => {
      if (id === 'tx-2') {
        return internalTransfer;
      }
      return regularTx;
    });

    render(
      <MemoryRouter initialEntries={['/transactions']}>
        <TransactionsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('button', { name: /Перевод от друга/i })).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /Перевод между счетами/i }),
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Все движения' }));

    expect(
      await screen.findByRole('button', { name: /Перевод между счетами/i }),
    ).toBeInTheDocument();
  });

  it('refetches data when the direction quick filter changes', async () => {
    const user = userEvent.setup();

    render(
      <MemoryRouter>
        <TransactionsPage />
      </MemoryRouter>,
    );

    await screen.findByRole('searchbox', { name: 'Поиск' });
    await user.selectOptions(screen.getByRole('combobox', { name: 'Направление' }), 'in');

    await waitFor(() => {
      expect(apiClientMock.transactions).toHaveBeenLastCalledWith(
        expect.objectContaining({
          direction: 'in',
        }),
      );
    });
  });

  it('keeps exact filters hidden until the user asks for them and avoids shortcut chip blocks', async () => {
    const user = userEvent.setup();

    render(
      <MemoryRouter>
        <TransactionsPage />
      </MemoryRouter>,
    );

    await screen.findByRole('searchbox', { name: 'Поиск' });
    expect(screen.queryByLabelText('Период с')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Фильтры' }));

    expect(await screen.findByLabelText('Период с')).toBeInTheDocument();
    expect(await screen.findByRole('combobox', { name: 'Тип операции' })).toBeInTheDocument();
    const listMode = await screen.findByRole('combobox', { name: 'Режим списка' });
    expect(listMode).toHaveValue('all');
    await user.selectOptions(listMode, 'uncategorized');
    expect(listMode).toHaveValue('uncategorized');
    expect(screen.queryByRole('button', { name: 'Без категории' })).not.toBeInTheDocument();
    expect(screen.queryByText('Магазины и сервисы')).not.toBeInTheDocument();
    expect(screen.queryByText('Категории появятся, когда будут расходы.')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'На проверке' })).not.toBeInTheDocument();
  });

  it('keeps advanced tools collapsed when opened from overview category callout', async () => {
    render(
      <MemoryRouter initialEntries={[{ pathname: '/transactions', state: { category: 'Продукты' } }]}>
        <TransactionsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('button', { name: 'Фильтры (1)' })).toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: 'Режим списка' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Период с')).not.toBeInTheDocument();

    await waitFor(() => {
      expect(apiClientMock.transactions).toHaveBeenLastCalledWith(
        expect.objectContaining({
          category: 'Продукты',
        }),
      );
    });
  });

  it('applies direction filter when opened from overview KPI drill-down', async () => {
    render(
      <MemoryRouter initialEntries={[{ pathname: '/transactions', state: { direction: 'out' } }]}>
        <TransactionsPage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(apiClientMock.transactions).toHaveBeenLastCalledWith(
        expect.objectContaining({
          direction: 'out',
        }),
      );
    });
  });

  it('applies account filter when opened from accounts', async () => {
    render(
      <MemoryRouter initialEntries={[{ pathname: '/transactions', state: { accountId: 'acc-1' } }]}>
        <TransactionsPage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(apiClientMock.transactions).toHaveBeenLastCalledWith(
        expect.objectContaining({
          account_id: 'acc-1',
        }),
      );
    });
  });

  it('keeps review-state controls out of the browsing toolbar', async () => {
    render(
      <MemoryRouter>
        <TransactionsPage />
      </MemoryRouter>,
    );

    await screen.findByRole('searchbox', { name: 'Поиск' });
    expect(screen.queryByRole('button', { name: /Проверка:/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/строк ждут/i)).not.toBeInTheDocument();
  });
});
