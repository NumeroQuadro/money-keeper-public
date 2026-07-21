import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it } from 'vitest';
import { apiClientMock } from '../test/apiClientMock';
import { StatementsPage } from './StatementsPage';

describe('StatementsPage', () => {
  beforeEach(() => {
    apiClientMock.importBatches.mockResolvedValue([
      {
        id: 'batch-1',
        source: 'telegram',
        status: 'processed',
        summary: { files_received: 2 },
        created_at: '2026-03-18T14:00:00Z',
        files: [
          {
            id: 'file-1',
            batch_id: 'batch-1',
            file_name: 'sber-march.pdf',
            file_path: '/data/sber-march.pdf',
            file_hash: 'hash-1',
            status: 'processed',
            error_message: '',
            created_at: '2026-03-18T14:00:00Z',
          },
          {
            id: 'file-2',
            batch_id: 'batch-1',
            file_name: 'ozon-march.pdf',
            file_path: '/data/ozon-march.pdf',
            file_hash: 'hash-2',
            status: 'duplicate',
            error_message: '',
            created_at: '2026-03-18T14:01:00Z',
          },
        ],
      },
    ]);

    apiClientMock.statements.mockResolvedValue([
      {
        id: 'stmt-1',
        provider: 'sber',
        account_id: 'acc-1',
        account_display: 'Main card',
        statement_type: 'card',
        period_start: '2026-03-01',
        period_end: '2026-03-31',
        generated_at: '2026-03-31T12:00:00Z',
        currency: 'RUB',
        opening_balance: 180000,
        closing_balance: 212400,
        total_credits: 40000,
        total_debits: 7600,
        parse_confidence: 0.97,
        reconcile_status: 'reconciled',
        pdf_path: '/data/sber.pdf',
        created_at: '2026-03-18T14:00:00Z',
      },
    ]);

    apiClientMock.statementRows.mockResolvedValue([
      {
        id: 'row-1',
        statement_id: 'stmt-1',
        row_index: 14,
        page_number: 2,
        raw_text: 'Lunch with team',
        amount: 3650,
        currency: 'RUB',
        direction: 'out',
        operation_date: '2026-03-17',
        posting_date: '2026-03-17',
        parse_confidence: 0.95,
      },
    ]);
  });

  it('renders import history, health strip, and statement status', async () => {
    render(
      <MemoryRouter>
        <StatementsPage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(apiClientMock.importBatches).toHaveBeenCalledWith({ limit: 30 });
    });

    expect(await screen.findByRole('heading', { name: 'Import batches' })).toBeInTheDocument();
    expect(screen.getAllByRole('heading', { name: 'Statements' }).length).toBeGreaterThan(0);
    expect(screen.getByText('Document register for imported PDFs and reconciliation status.')).toBeInTheDocument();
    expect(screen.getByText('Drop PDF statements to file')).toBeInTheDocument();
    expect(screen.getByText((_content, node) => node?.textContent === '1 statements')).toBeInTheDocument();
    expect(
      screen.getByText((_content, node) => node?.textContent === '1 import batches'),
    ).toBeInTheDocument();
    expect(screen.getAllByText(/duplicate/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText('Reconciled').length).toBeGreaterThan(0);
  }, 10000);

  it('expands batches and loads statement rows on demand', async () => {
    const user = userEvent.setup();

    render(
      <MemoryRouter>
        <StatementsPage />
      </MemoryRouter>,
    );

    await user.click(await screen.findByRole('button', { name: /1 processed, 1 duplicate/i }));
    expect(await screen.findByText('sber-march.pdf')).toBeInTheDocument();

    await user.click(screen.getAllByRole('button', { name: 'Show' })[0]);

    await waitFor(() => {
      expect(apiClientMock.statementRows).toHaveBeenCalledWith('stmt-1', { limit: 150, offset: 0 });
    });

    const rowsTables = await screen.findAllByRole('table', { name: 'Statement rows' });
    expect(rowsTables.length).toBeGreaterThan(0);
    expect(within(rowsTables[0]).getByText('Lunch with team')).toBeInTheDocument();
  });
});
