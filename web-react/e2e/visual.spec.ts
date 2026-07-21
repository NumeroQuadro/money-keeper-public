import { expect, test } from '@playwright/test';

const FIXED_TIME_ISO = '2026-03-15T12:00:00.000Z';

function jsonHeaders() {
  return {
    'Content-Type': 'application/json',
    'Access-Control-Allow-Origin': '*',
  };
}

const ACCOUNTS = [
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
];

const TX_1 = {
  id: 'tx-1',
  account_id: 'acc-1',
  operation_datetime: '2026-03-14T12:30:00',
  posting_datetime: '2026-03-14T12:30:00',
  timestamp_precision: 'exact',
  amount: 3500,
  currency: 'RUB',
  direction: 'out',
  category: 'Groceries',
  description_raw: 'Store purchase',
  merchant_normalized: 'Store',
  bank_reference_id: 'ref-1',
  bank_category: 'merchant',
  tags: ['food'],
  meaning: 'spend',
  review_status: 'reviewed',
  review_reasons: [],
  needs_human_review: false,
  source_statement_id: 'st-11',
  source_page_number: 1,
  source_row_index: 9,
};

const TX_2 = {
  id: 'tx-2',
  account_id: 'acc-1',
  operation_datetime: '2026-03-12T09:15:00',
  posting_datetime: '2026-03-12T09:15:00',
  timestamp_precision: 'exact',
  amount: 24000,
  currency: 'RUB',
  direction: 'in',
  category: 'Salary',
  description_raw: 'Salary',
  merchant_normalized: 'Employer',
  bank_reference_id: 'ref-2',
  bank_category: 'income',
  tags: [],
  meaning: 'income',
  review_status: 'reviewed',
  review_reasons: [],
  needs_human_review: false,
  source_statement_id: 'st-12',
  source_page_number: 1,
  source_row_index: 1,
};

const TX_3 = {
  id: 'tx-3',
  account_id: 'acc-2',
  operation_datetime: '2026-03-15T09:00:00',
  posting_datetime: '2026-03-15T09:00:00',
  timestamp_precision: 'exact',
  amount: 1200,
  currency: 'RUB',
  direction: 'out',
  category: '',
  description_raw: 'Taxi ride',
  merchant_normalized: 'Yandex Go',
  bank_reference_id: 'ref-3',
  bank_category: 'transport',
  tags: [],
  meaning: 'spend',
  review_status: 'reviewed',
  review_reasons: ['uncategorized_needs_review'],
  needs_human_review: true,
  source_statement_id: 'st-13',
  source_page_number: 2,
  source_row_index: 14,
};

const TX_4 = {
  id: 'tx-4',
  account_id: 'acc-1',
  operation_datetime: '2026-03-10T12:30:00',
  posting_datetime: '2026-03-10T12:30:00',
  timestamp_precision: 'exact',
  amount: 4900,
  currency: 'RUB',
  direction: 'out',
  category: 'Transfers',
  description_raw: 'Wallet top-up',
  merchant_normalized: 'Wallet top-up',
  bank_reference_id: 'ref-4',
  bank_category: 'transfer',
  tags: [],
  meaning: 'unknown',
  review_status: 'needs_review',
  review_reasons: [],
  needs_human_review: false,
  source_statement_id: 'st-14',
  source_page_number: 3,
  source_row_index: 7,
};

const TX_5 = {
  id: 'tx-5',
  account_id: 'acc-2',
  operation_datetime: '2026-03-10T12:31:00',
  posting_datetime: '2026-03-10T12:31:00',
  timestamp_precision: 'exact',
  amount: 4900,
  currency: 'RUB',
  direction: 'in',
  category: 'Transfers',
  description_raw: 'Wallet refill',
  merchant_normalized: 'Wallet refill',
  bank_reference_id: 'ref-5',
  bank_category: 'transfer',
  tags: [],
  meaning: 'unknown',
  review_status: 'needs_review',
  review_reasons: [],
  needs_human_review: false,
  source_statement_id: 'st-15',
  source_page_number: 4,
  source_row_index: 2,
};

const MONTHLY_FLOW = {
  generated_at: '2026-03-15T00:00:00Z',
  items: [
    { period: '2026-01', inflow: 100000, outflow: 80000, net: 20000, tx_count: 42 },
    { period: '2026-02', inflow: 110000, outflow: 76000, net: 34000, tx_count: 38 },
    { period: '2026-03', inflow: 120000, outflow: 90000, net: 30000, tx_count: 40 },
  ],
};

const NET_WORTH_CURRENT = {
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
};

const NET_WORTH_TIMELINE = {
  series: [
    {
      currency: 'RUB',
      points: [
        {
          timestamp: '2026-02-01T00:00:00Z',
          total_balance: 320000,
          accounts_total: 2,
          accounts_with_snapshot: 2,
          accounts_missing: 0,
          completeness: 1,
        },
        {
          timestamp: '2026-03-01T00:00:00Z',
          total_balance: 335000,
          accounts_total: 2,
          accounts_with_snapshot: 2,
          accounts_missing: 0,
          completeness: 1,
        },
        {
          timestamp: '2026-03-15T00:00:00Z',
          total_balance: 345000,
          accounts_total: 2,
          accounts_with_snapshot: 2,
          accounts_missing: 0,
          completeness: 1,
        },
      ],
    },
  ],
};

const IMPORT_BATCHES = [
  {
    id: 'batch-1',
    source: 'telegram',
    status: 'processed',
    summary: { files_received: 2 },
    created_at: '2026-03-18T14:00:00',
    files: [],
  },
];

const STATEMENTS = [
  {
    id: 'st-11',
    provider: 'sber',
    account_id: 'acc-1',
    account_display: 'Sber Card · **** 1234',
    statement_type: 'card',
    period_start: '2026-03-01',
    period_end: '2026-03-31',
    generated_at: '2026-04-01T00:00:00Z',
    currency: 'RUB',
    opening_balance: 120000,
    closing_balance: 123500,
    total_credits: 24000,
    total_debits: 3500,
    parse_confidence: 0.94,
    reconcile_status: 'ok',
    pdf_path: '/uploads/st-11.pdf',
    created_at: '2026-04-01T00:00:00Z',
  },
  {
    id: 'st-12',
    provider: 'ozon',
    account_id: 'acc-2',
    account_display: 'Ozon Wallet · **** 6789',
    statement_type: 'wallet',
    period_start: '2026-03-01',
    period_end: '2026-03-31',
    generated_at: '2026-04-01T00:00:00Z',
    currency: 'RUB',
    opening_balance: 50000,
    closing_balance: 47200,
    total_credits: 0,
    total_debits: 2800,
    parse_confidence: 0.88,
    reconcile_status: 'pending',
    pdf_path: '/uploads/st-12.pdf',
    created_at: '2026-04-01T00:00:00Z',
  },
];

const STATEMENT_ROWS: Record<string, unknown> = {
  'st-11': [
    {
      id: 'row-1',
      statement_id: 'st-11',
      row_index: 1,
      page_number: 1,
      raw_text: 'Salary',
      amount: 24000,
      currency: 'RUB',
      direction: 'in',
      operation_date: '2026-03-12',
      posting_date: '2026-03-12',
      parse_confidence: 0.95,
    },
    {
      id: 'row-2',
      statement_id: 'st-11',
      row_index: 9,
      page_number: 1,
      raw_text: 'Store purchase',
      amount: 3500,
      currency: 'RUB',
      direction: 'out',
      operation_date: '2026-03-14',
      posting_date: '2026-03-14',
      parse_confidence: 0.92,
    },
  ],
};

const EXCEPTIONS_OPEN = [
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
];

const TRANSFER_LINKS_SUGGESTED = [
  {
    id: 'link-1',
    status: 'suggested',
    transaction_out_id: 'tx-4',
    transaction_in_id: 'tx-5',
    match_score: 0.91,
    rationale:
      'amount_match=0.00 (score=1.00); dt=0s; hints=1/1; same_day=1; in_after_out=1; ref_match=0; out_generic_no_hint=0; counterparty_overlap=0; known_lane_marker=0/0; ambiguous=1; auto_guard=none; auto_override=none; auto_lane=none; tiebreak_out=none; tiebreak_in=single',
  },
];

const TRANSACTIONS_FOR_OVERVIEW = { total: 2, items: [TX_1, TX_2] };
const TRANSACTIONS_FOR_TRANSACTIONS_PAGE = { total: 3, items: [TX_1, TX_2, TX_3] };
const TRANSACTIONS_FOR_REVIEW = { total: 1, items: [TX_3] };

const TRANSACTION_BY_ID: Record<string, unknown> = {
  [TX_1.id]: TX_1,
  [TX_2.id]: TX_2,
  [TX_3.id]: TX_3,
  [TX_4.id]: TX_4,
  [TX_5.id]: TX_5,
};

test.beforeEach(async ({ page }) => {
  await page.addInitScript(({ iso }) => {
    const fixed = new Date(iso);
    const OriginalDate = Date as unknown as DateConstructor;

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const MockDate: any = function (...args: Array<unknown>) {
      if (this instanceof MockDate) {
        if (args.length === 0) {
          return new OriginalDate(fixed.getTime());
        }
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        return new OriginalDate(...(args as any));
      }

      return new OriginalDate(fixed.getTime()).toString();
    };

    MockDate.now = () => fixed.getTime();
    MockDate.UTC = OriginalDate.UTC;
    MockDate.parse = OriginalDate.parse;
    MockDate.prototype = OriginalDate.prototype;

    // eslint-disable-next-line no-global-assign
    Date = MockDate;
  }, { iso: FIXED_TIME_ISO });

  await page.route(/:\/\/[^/]+\/api(\/|$)/, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const params = url.searchParams;

    if (request.method() !== 'GET') {
      await route.fulfill({ status: 204, headers: jsonHeaders(), body: '' });
      return;
    }

    const fulfillJson = async (payload: unknown, status = 200) => {
      await route.fulfill({
        status,
        headers: jsonHeaders(),
        body: JSON.stringify(payload),
      });
    };

    if (path === '/api/analytics/monthly-flow') {
      await fulfillJson(MONTHLY_FLOW);
      return;
    }

    if (path === '/api/accounts/' || path === '/api/accounts') {
      await fulfillJson(ACCOUNTS);
      return;
    }

    if (path === '/api/networth/current') {
      await fulfillJson(NET_WORTH_CURRENT);
      return;
    }

    if (path === '/api/networth/timeline') {
      await fulfillJson(NET_WORTH_TIMELINE);
      return;
    }

    if (path === '/api/exceptions/' || path === '/api/exceptions') {
      const status = params.get('status');
      await fulfillJson(status === 'open' ? EXCEPTIONS_OPEN : []);
      return;
    }

    if (path === '/api/transfers/links') {
      const status = params.get('status');
      await fulfillJson(status === 'suggested' ? TRANSFER_LINKS_SUGGESTED : []);
      return;
    }

    if (path === '/api/imports/batches') {
      await fulfillJson(IMPORT_BATCHES);
      return;
    }

    if (path === '/api/statements/' || path === '/api/statements') {
      await fulfillJson(STATEMENTS);
      return;
    }

    const stmtRowsMatch = path.match(/^\/api\/statements\/([^/]+)\/rows$/);
    if (stmtRowsMatch) {
      const statementId = stmtRowsMatch[1];
      await fulfillJson(STATEMENT_ROWS[statementId] ?? []);
      return;
    }

    if (path === '/api/transactions/' || path === '/api/transactions') {
      if (params.get('needs_human_review') === 'true') {
        await fulfillJson(TRANSACTIONS_FOR_REVIEW);
        return;
      }

      const limit = params.get('limit');
      if (limit === '6') {
        await fulfillJson(TRANSACTIONS_FOR_OVERVIEW);
        return;
      }

      await fulfillJson(TRANSACTIONS_FOR_TRANSACTIONS_PAGE);
      return;
    }

    const txMatch = path.match(/^\/api\/transactions\/([^/]+)$/);
    if (txMatch) {
      const txId = txMatch[1];
      const payload = TRANSACTION_BY_ID[txId];
      if (!payload) {
        await fulfillJson({ detail: 'not found' }, 404);
        return;
      }
      await fulfillJson(payload);
      return;
    }

    await fulfillJson({ detail: `unhandled ${path}` }, 404);
  });

  await page.addStyleTag({
    content: `
      *, *::before, *::after {
        transition-duration: 0s !important;
        animation-duration: 0s !important;
        animation-delay: 0s !important;
        scroll-behavior: auto !important;
        caret-color: transparent !important;
      }
    `,
  });
});

test('Overview desktop', async ({ page }) => {
  await page.goto('/');
  await page.getByTestId('overview-page').waitFor();
  await expect(page).toHaveScreenshot('overview-desktop.png', {
    fullPage: true,
    animations: 'disabled',
  });
});

test('Overview mobile', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 740 });
  await page.goto('/');
  await page.getByTestId('overview-page').waitFor();
  await expect(page).toHaveScreenshot('overview-mobile.png', {
    fullPage: true,
    animations: 'disabled',
  });
});

test('Overview chart hover reveals exact values', async ({ page }) => {
  await page.goto('/');
  await page.getByTestId('overview-page').waitFor();

  const chart = page.getByRole('img', { name: 'Monthly spending trend' });
  await chart.locator('rect[data-period="2026-01"]').hover();

  await expect(chart.locator('.overview-trend-callout')).toBeVisible();
  await expect(chart.locator('.overview-trend-callout-title')).toHaveText(/January 2026/);
  await expect(chart.locator('.overview-trend-callout-value')).toHaveText(/80\s*000/);
  await expect(page.locator('.overview-trend-summary')).toContainText('January 2026');
});

test('Transactions desktop', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('link', { name: 'Transactions' }).click();
  await page.getByRole('searchbox', { name: 'Поиск' }).waitFor();
  await expect(page).toHaveScreenshot('transactions-desktop.png', {
    fullPage: true,
    animations: 'disabled',
  });
});

test('Transactions mobile', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 740 });
  await page.goto('/transactions');
  await page.getByRole('searchbox', { name: 'Поиск' }).waitFor();
  await page.getByRole('combobox', { name: 'Направление' }).selectOption('in');
  await expect(page).toHaveScreenshot('transactions-mobile.png', {
    fullPage: true,
    animations: 'disabled',
  });
});

test('Review desktop', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('link', { name: 'Review' }).click();
  await page.getByRole('group', { name: 'Actions' }).waitFor();
  await expect(page).toHaveScreenshot('review-desktop.png', {
    fullPage: true,
    animations: 'disabled',
  });
});

test('Review mobile', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 740 });
  await page.goto('/review');
  await page.getByRole('group', { name: 'Actions' }).waitFor();
  await expect(page).toHaveScreenshot('review-mobile.png', {
    fullPage: true,
    animations: 'disabled',
  });
});

test('Accounts desktop', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('link', { name: 'Accounts' }).click();
  await page.getByRole('main').getByRole('heading', { name: 'Accounts' }).waitFor();
  await expect(page).toHaveScreenshot('accounts-desktop.png', {
    fullPage: true,
    animations: 'disabled',
  });
});

test('Accounts mobile', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 740 });
  await page.goto('/accounts');
  await page.getByRole('main').getByRole('heading', { name: 'Accounts' }).waitFor();
  await expect(page).toHaveScreenshot('accounts-mobile.png', {
    fullPage: true,
    animations: 'disabled',
  });
});

test('Statements desktop', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('link', { name: 'Statements' }).click();
  await page
    .getByRole('main')
    .getByRole('heading', { name: 'Statements', level: 2 })
    .waitFor();
  await expect(page).toHaveScreenshot('statements-desktop.png', {
    fullPage: true,
    animations: 'disabled',
  });
});

test('Statements mobile', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 740 });
  await page.goto('/statements');
  await page
    .getByRole('main')
    .getByRole('heading', { name: 'Statements', level: 2 })
    .waitFor();
  await expect(page).toHaveScreenshot('statements-mobile.png', {
    fullPage: true,
    animations: 'disabled',
  });
});
