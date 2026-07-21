import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it } from 'vitest';
import { apiClientMock } from '../test/apiClientMock';
import { RulesPage } from './RulesPage';

function LocationStateProbe() {
  const location = useLocation();
  return <pre data-testid="location-state">{JSON.stringify(location.state || {})}</pre>;
}

describe('RulesPage', () => {
  beforeEach(() => {
    apiClientMock.rules.mockResolvedValue([
      {
        id: 'rule-1',
        name: 'Переводы между своими счетами',
        pattern: 'пополнение накопительного',
        priority: 10,
        enabled: true,
        actions: { set_meaning: 'internal_transfer' },
      },
      {
        id: 'rule-2',
        name: 'Зарплата',
        pattern: 'salary',
        priority: 20,
        enabled: false,
        actions: { set_category: 'Доходы' },
      },
    ]);
    apiClientMock.previewRules.mockResolvedValue({
      transactions_scanned: 12,
      transactions_matched: 3,
      transactions_changed: 2,
      transactions_updated: 0,
      sample: [],
    });
    apiClientMock.applyRules.mockResolvedValue({
      transactions_scanned: 12,
      transactions_matched: 3,
      transactions_changed: 2,
      transactions_updated: 2,
      sample: [],
    });
    apiClientMock.createRule.mockResolvedValue({
      id: 'rule-3',
      name: 'Кофе',
      pattern: 'coffee',
      priority: 100,
      enabled: true,
      actions: {},
    });
  });

  it('keeps the rules list as the default surface and hides mutation tools', async () => {
    render(
      <MemoryRouter>
        <RulesPage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('heading', { name: 'Правила автоматизации' })).toBeInTheDocument();
    expect(await screen.findByText('Переводы между своими счетами')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Создать правило' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Проверить влияние' })).not.toBeInTheDocument();
  });

  it('shows advanced tools only on demand and keeps actions working', async () => {
    const user = userEvent.setup();

    render(
      <MemoryRouter>
        <RulesPage />
      </MemoryRouter>,
    );

    await user.click(await screen.findByRole('button', { name: 'Показать инструменты правил' }));

    await user.click(await screen.findByRole('button', { name: 'Проверить влияние' }));
    await waitFor(() => {
      expect(apiClientMock.previewRules).toHaveBeenCalled();
    });

    await user.click(screen.getByRole('button', { name: 'Применить к операциям' }));
    await waitFor(() => {
      expect(apiClientMock.applyRules).toHaveBeenCalledWith(
        expect.objectContaining({
          include_transfers: false,
          dry_run: false,
        }),
      );
    });

    await user.type(screen.getByLabelText('Название'), 'Кофе');
    await user.type(screen.getByLabelText('Что искать в тексте'), 'coffee');
    await user.click(screen.getByRole('button', { name: 'Создать правило' }));

    await waitFor(() => {
      expect(apiClientMock.createRule).toHaveBeenCalledWith({
        name: 'Кофе',
        pattern: 'coffee',
        priority: 100,
        enabled: true,
        actions: {},
        conditions: {},
      });
    });
  });

  it('opens matching transactions directly from a rule row', async () => {
    const user = userEvent.setup();
    apiClientMock.previewRules.mockResolvedValueOnce({
      transactions_scanned: 10,
      transactions_matched: 2,
      transactions_changed: 2,
      transactions_updated: 0,
      sample: [
        {
          transaction_id: 'tx-42',
          matched_rule_ids: ['rule-1'],
          before_category: '',
          after_category: 'Перевод',
          before_tags: [],
          after_tags: [],
          before_meaning: 'unknown',
          after_meaning: 'internal_transfer',
          before_review_status: 'needs_review',
          after_review_status: 'needs_review',
        },
      ],
    });

    render(
      <MemoryRouter initialEntries={['/rules']}>
        <Routes>
          <Route path="/rules" element={<RulesPage />} />
          <Route path="/transactions" element={<LocationStateProbe />} />
        </Routes>
      </MemoryRouter>,
    );

    const openButtons = await screen.findAllByRole('button', { name: 'Открыть операции' });
    await user.click(openButtons[0]);

    await waitFor(() => {
      expect(apiClientMock.previewRules).toHaveBeenLastCalledWith({
        q: 'пополнение накопительного',
        limit: '200',
        sample_limit: '20',
      });
    });
    expect(await screen.findByTestId('location-state')).toHaveTextContent('"query":"пополнение накопительного"');
    expect(screen.getByTestId('location-state')).toHaveTextContent('"openTransactionId":"tx-42"');
  });
});
