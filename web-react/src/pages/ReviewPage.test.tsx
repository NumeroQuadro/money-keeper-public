import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it } from 'vitest';
import { ApiError } from '../api/client';
import { apiClientMock } from '../test/apiClientMock';
import { ReviewPage } from './ReviewPage';

describe('ReviewPage', () => {
  beforeEach(() => {
    apiClientMock.accounts.mockResolvedValue([
      {
        id: 'acc-1',
        provider: 'sber',
        account_type: 'card',
        display_name: 'Sber Card',
        masked_identifier: '**** 1234',
      },
      {
        id: 'acc-2',
        provider: 'ozon',
        account_type: 'wallet',
        display_name: 'Ozon Wallet',
        masked_identifier: '**** 6789',
      },
    ]);
    apiClientMock.importBatches.mockResolvedValue([
      {
        id: 'batch-1',
        source: 'telegram',
        status: 'processed',
        summary: { files_received: 2 },
        created_at: '2026-03-18T14:00:00',
        files: [],
      },
    ]);
    apiClientMock.exceptions.mockResolvedValue([
      {
        id: 'ex-1',
        status: 'open',
        exception_type: 'ambiguous_category',
        severity: 'medium',
        entity_type: 'transaction',
        entity_id: 'tx-1',
        rationale: 'Needs category confirmation',
        payload: { suggested_category: 'Groceries' },
      },
      {
        id: 'ex-2',
        status: 'open',
        exception_type: 'reconciliation_mismatch',
        severity: 'high',
        entity_type: 'statement',
        entity_id: 'statement-1',
        rationale: 'Closing balance does not reconcile.',
        payload: {},
      },
    ]);
    apiClientMock.transferLinks.mockResolvedValue([
      {
        id: 'link-1',
        status: 'suggested',
        transaction_out_id: 'tx-4',
        transaction_in_id: 'tx-5',
        match_score: 0.91,
        rationale: 'Possible internal transfer.',
      },
    ]);
    apiClientMock.transactions.mockImplementation(async (query?: Record<string, unknown>) => {
      if (query?.needs_human_review === true) {
        return {
          total: 1,
          items: [
            {
              id: 'tx-3',
              account_id: 'acc-2',
              operation_datetime: '2026-03-15T09:00:00',
              posting_datetime: '2026-03-15T09:00:00',
              amount: 1200,
              currency: 'RUB',
              direction: 'out',
              category: '',
              description_raw: 'Taxi ride',
              merchant_normalized: 'Yandex Go',
              bank_category: 'transport',
              tags: [],
              meaning: 'spend',
              review_status: 'reviewed',
              review_reasons: ['uncategorized_needs_review'],
              needs_human_review: true,
              source_statement_id: 'st-1',
              source_page_number: 2,
              source_row_index: 14,
            },
          ],
        };
      }

      return {
        total: 0,
        items: [],
      };
    });
    apiClientMock.transactionById.mockImplementation(async (id: string) => {
      if (id === 'tx-1') {
        return {
          id,
          account_id: 'acc-1',
          operation_datetime: '2026-03-14T12:30:00',
          posting_datetime: '2026-03-14T12:30:00',
          amount: 3500,
          currency: 'RUB',
          direction: 'out',
          category: '',
          description_raw: 'Store purchase',
          merchant_normalized: 'Store purchase',
          bank_category: 'shopping',
          tags: [],
          meaning: 'spend',
          review_status: 'needs_review',
          review_reasons: [],
          needs_human_review: false,
          source_statement_id: 'st-11',
          source_page_number: 1,
          source_row_index: 9,
        };
      }

      if (id === 'tx-4') {
        return {
          id,
          account_id: 'acc-1',
          operation_datetime: '2026-03-14T12:30:00',
          posting_datetime: '2026-03-14T12:30:00',
          amount: 4900,
          currency: 'RUB',
          direction: 'out',
          category: 'Transfers',
          description_raw: 'Wallet top-up',
          merchant_normalized: 'Wallet top-up',
          bank_category: 'transfer',
          tags: [],
          meaning: 'unknown',
          review_status: 'needs_review',
          review_reasons: [],
          needs_human_review: false,
          source_statement_id: 'st-12',
          source_page_number: 3,
          source_row_index: 7,
        };
      }

      return {
        id,
        account_id: 'acc-2',
        operation_datetime: '2026-03-14T12:31:00',
        posting_datetime: '2026-03-14T12:31:00',
        amount: 4900,
        currency: 'RUB',
        direction: 'in',
        category: 'Transfers',
        description_raw: 'Wallet refill',
        merchant_normalized: 'Wallet refill',
        bank_category: 'transfer',
        tags: [],
        meaning: 'unknown',
        review_status: 'needs_review',
        review_reasons: [],
        needs_human_review: false,
        source_statement_id: 'st-13',
        source_page_number: 4,
        source_row_index: 2,
      };
    });
    apiClientMock.approveExceptionCategory.mockResolvedValue({
      id: 'ex-1',
      status: 'resolved',
      exception_type: 'ambiguous_category',
      severity: 'medium',
      entity_type: 'transaction',
      entity_id: 'tx-1',
      rationale: 'approved',
      payload: {},
    });
    apiClientMock.resolveException.mockResolvedValue({
      id: 'ex-2',
      status: 'resolved',
      exception_type: 'reconciliation_mismatch',
      severity: 'high',
      entity_type: 'statement',
      entity_id: 'statement-1',
      rationale: 'resolved',
      payload: {},
    });
    apiClientMock.ignoreException.mockResolvedValue({
      id: 'ex-2',
      status: 'resolved',
      exception_type: 'reconciliation_mismatch',
      severity: 'high',
      entity_type: 'statement',
      entity_id: 'statement-1',
      rationale: 'ignored',
      payload: {},
    });
    apiClientMock.markExceptionDuplicate.mockResolvedValue({
      id: 'ex-1',
      status: 'resolved',
      exception_type: 'duplicate',
      severity: 'medium',
      entity_type: 'transaction',
      entity_id: 'tx-1',
      rationale: 'duplicate',
      payload: {},
    });
    apiClientMock.confirmTransferLink.mockResolvedValue({
      id: 'link-1',
      status: 'confirmed',
      transaction_out_id: 'tx-4',
      transaction_in_id: 'tx-5',
      match_score: 0.91,
      rationale: 'confirmed',
    });
    apiClientMock.rejectTransferLink.mockResolvedValue({
      id: 'link-1',
      status: 'rejected',
      transaction_out_id: 'tx-4',
      transaction_in_id: 'tx-5',
      match_score: 0.91,
      rationale: 'rejected',
    });
    apiClientMock.approveTransactionCategory.mockResolvedValue({
      id: 'tx-3',
      account_id: 'acc-2',
      operation_datetime: '2026-03-15T09:00:00',
      posting_datetime: '2026-03-15T09:00:00',
      amount: 1200,
      currency: 'RUB',
      direction: 'out',
      category: 'Transport',
      description_raw: 'Taxi ride',
      merchant_normalized: 'Yandex Go',
      bank_category: 'transport',
      tags: [],
      meaning: 'spend',
      review_status: 'reviewed',
      review_reasons: [],
      needs_human_review: false,
      source_statement_id: 'st-1',
      source_page_number: 2,
      source_row_index: 14,
    });
    apiClientMock.markTransactionReviewed.mockResolvedValue({
      id: 'tx-3',
      account_id: 'acc-2',
      operation_datetime: '2026-03-15T09:00:00',
      posting_datetime: '2026-03-15T09:00:00',
      amount: 1200,
      currency: 'RUB',
      direction: 'out',
      category: 'Transport',
      description_raw: 'Taxi ride',
      merchant_normalized: 'Yandex Go',
      bank_category: 'transport',
      tags: [],
      meaning: 'spend',
      review_status: 'reviewed',
      review_reasons: [],
      needs_human_review: false,
      source_statement_id: 'st-1',
      source_page_number: 2,
      source_row_index: 14,
    });
    apiClientMock.markTransactionDuplicate.mockResolvedValue({
      id: 'tx-3',
      account_id: 'acc-2',
      operation_datetime: '2026-03-15T09:00:00',
      posting_datetime: '2026-03-15T09:00:00',
      amount: 1200,
      currency: 'RUB',
      direction: 'out',
      category: 'Duplicate',
      description_raw: 'Taxi ride',
      merchant_normalized: 'Yandex Go',
      bank_category: 'transport',
      tags: ['duplicate'],
      meaning: 'spend',
      review_status: 'reviewed',
      review_reasons: [],
      needs_human_review: false,
      source_statement_id: 'st-1',
      source_page_number: 2,
      source_row_index: 14,
    });
  });

  it('renders a review inbox with a compact queue summary and a detail panel', async () => {
    render(
      <MemoryRouter>
        <ReviewPage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(apiClientMock.exceptions).toHaveBeenCalledWith({ status: 'open' });
    });

    expect(screen.getByRole('heading', { name: 'Review' })).toBeInTheDocument();
    expect(screen.getByText('Items pending owner decision.')).toBeInTheDocument();
    expect(await screen.findByText('4 unresolved')).toBeInTheDocument();
    expect(document.querySelector('.review-summary-grid')).toBeNull();
    expect(screen.getByText('Queue order')).toBeInTheDocument();
    expect(screen.getByText(/Confidence 91%/)).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByLabelText('Anomaly detail')).toBeInTheDocument();
    });
    expect(document.querySelectorAll('[data-review-item-key]')).toHaveLength(4);

    const keys = Array.from(document.querySelectorAll('[data-review-item-key]')).map((node) =>
      (node as HTMLElement).dataset.reviewItemKey,
    );
    expect(keys[0]).toBe('exception:ex-2');
    expect(keys).toEqual(['exception:ex-2', 'exception:ex-1', 'transfer:link-1', 'transaction:tx-3']);
  });

  it('updates the drawer when another queue item is selected', async () => {
    const user = userEvent.setup();

    render(
      <MemoryRouter>
        <ReviewPage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(document.querySelector('[data-review-item-key="exception:ex-1"]')).not.toBeNull();
    });
    const exceptionRow = document.querySelector(
      '[data-review-item-key="exception:ex-1"]',
    ) as HTMLElement | null;
    await user.click((exceptionRow as HTMLElement).querySelector('.review-row-main') as HTMLElement);

    const detailDrawer = await screen.findByLabelText('Exception detail');
    expect(detailDrawer).toBeInTheDocument();
    expect(within(detailDrawer).getAllByText('Ambiguous Category').length).toBeGreaterThan(0);
    expect(within(detailDrawer).getByText('Suggested category')).toBeInTheDocument();
    expect(within(detailDrawer).getByText('Groceries')).toBeInTheDocument();
  });

  it('applies inline actions for each queue type', async () => {
    const user = userEvent.setup();

    render(
      <MemoryRouter>
        <ReviewPage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(document.querySelector('[data-review-item-key="exception:ex-2"]')).not.toBeNull();
    });
    await waitFor(() => {
      expect(document.querySelector('[data-review-item-key="exception:ex-2"]')).toHaveClass('is-selected');
    });

    await user.click(screen.getByRole('button', { name: 'Ignore' }));
    await waitFor(() => {
      expect(apiClientMock.ignoreException).toHaveBeenCalledWith('ex-2');
    });

    const exceptionRow = document.querySelector('[data-review-item-key="exception:ex-1"]') as HTMLElement;
    await user.click(within(exceptionRow).getByRole('button'));
    await user.click(screen.getByRole('button', { name: 'Assign Groceries' }));
    await waitFor(() => {
      expect(apiClientMock.approveExceptionCategory).toHaveBeenCalledWith('ex-1');
    });

    const transactionRow = document.querySelector('[data-review-item-key="transaction:tx-3"]') as HTMLElement;
    await user.click(within(transactionRow).getByRole('button'));
    await user.click(screen.getByRole('button', { name: 'Assign Transport' }));
    await waitFor(() => {
      expect(apiClientMock.approveTransactionCategory).toHaveBeenCalledWith('tx-3');
    });

    const transferRow = document.querySelector('[data-review-item-key="transfer:link-1"]') as HTMLElement;
    await user.click(within(transferRow).getByRole('button'));
    await user.click(screen.getByRole('button', { name: 'Confirm' }));
    await waitFor(() => {
      expect(apiClientMock.confirmTransferLink).toHaveBeenCalledWith('link-1');
    });
  });

  it('surfaces a helpful message when an action is unauthorized', async () => {
    const user = userEvent.setup();
    apiClientMock.confirmTransferLink.mockRejectedValueOnce(new ApiError('unauthorized', 401));

    render(
      <MemoryRouter>
        <ReviewPage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(document.querySelector('[data-review-item-key="transfer:link-1"]')).not.toBeNull();
    });

    const transferRow = document.querySelector('[data-review-item-key="transfer:link-1"]') as HTMLElement;
    await user.click(within(transferRow).getByRole('button'));
    await user.click(screen.getByRole('button', { name: 'Confirm' }));

    expect(
      await screen.findByText(/Not authorized \(missing or invalid admin token\)\. Set it in Settings/i),
    ).toBeInTheDocument();
  });

  it('highlights the requested queue item from legacy deep-link state', async () => {
    render(
      <MemoryRouter
        initialEntries={[
          {
            pathname: '/review',
            state: {
              openQueueItemId: 'ex-1',
              openQueueKind: 'exception',
            },
          },
        ]}
      >
        <ReviewPage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(document.querySelector('[data-review-item-key="exception:ex-1"]')).toHaveClass(
        'is-selected',
      );
    });
  });

  it('supports arrow-key review movement and escape to close detail', async () => {
    const user = userEvent.setup();

    render(
      <MemoryRouter>
        <ReviewPage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(document.querySelector('[data-review-item-key="exception:ex-2"]')).toHaveClass(
        'is-selected',
      );
    });

    await user.keyboard('{ArrowDown}');
    await waitFor(() => {
      expect(document.querySelector('[data-review-item-key="exception:ex-1"]')).toHaveClass(
        'is-selected',
      );
    });

    await user.keyboard('{Escape}');
    expect(await screen.findByText('Detail drawer')).toBeInTheDocument();
  });
});
