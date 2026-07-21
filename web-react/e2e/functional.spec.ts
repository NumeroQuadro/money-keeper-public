import fs from 'node:fs';
import path from 'node:path';
import { expect, test } from '@playwright/test';
import { CoverageReport } from 'monocart-coverage-reports';

const FIXED_TIME_ISO = '2026-03-15T12:00:00.000Z';

type CoverageRange = {
  startOffset: number;
  endOffset: number;
  count: number;
};

type CoverageFunction = {
  ranges: CoverageRange[];
};

type CoverageEntry = {
  url: string;
  text?: string;
  source?: string;
  functions: CoverageFunction[];
};

const coverageEntries: CoverageEntry[] = [];

function jsonHeaders() {
  return {
    'Content-Type': 'application/json',
    'Access-Control-Allow-Origin': '*',
  };
}


function buildScenario() {
  const accounts = [
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

  const transactions = [
    {
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
    },
    {
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
    },
    {
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
    },
    {
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
    },
    {
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
    },
  ];

  const monthlyFlow = {
    generated_at: '2026-03-15T00:00:00Z',
    items: [
      { period: '2026-01', inflow: 100000, outflow: 80000, net: 20000, tx_count: 42 },
      { period: '2026-02', inflow: 110000, outflow: 76000, net: 34000, tx_count: 38 },
      { period: '2026-03', inflow: 120000, outflow: 90000, net: 30000, tx_count: 40 },
    ],
  };

  const netWorthCurrent = {
    totals: [{ currency: 'RUB', total_balance: 345000 }],
    accounts: [
      {
        account_id: 'acc-1',
        provider: 'sber',
        account_type: 'card',
        display_name: 'Sber Card',
        masked_identifier: '**** 1234',
        balance: 123500,
        currency: 'RUB',
        as_of: '2026-03-15T00:00:00Z',
      },
      {
        account_id: 'acc-2',
        provider: 'ozon',
        account_type: 'wallet',
        display_name: 'Ozon Wallet',
        masked_identifier: '**** 6789',
        balance: 221500,
        currency: 'RUB',
        as_of: '2026-03-15T00:00:00Z',
      },
    ],
  };

  const netWorthTimeline = {
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

  const batches = [
    {
      id: 'batch-failed',
      source: 'telegram',
      status: 'failed',
      summary: { files_received: 1, files_queued: 0, failed: 1 },
      created_at: '2026-03-18T14:00:00',
      files: [
        {
          id: 'file-failed-1',
          batch_id: 'batch-failed',
          file_name: 'broken.pdf',
          file_path: '/uploads/broken.pdf',
          file_hash: 'hash-broken',
          status: 'failed',
          error_message: 'Checksum mismatch',
          created_at: '2026-03-18T14:00:00',
        },
      ],
    },
    {
      id: 'batch-ok',
      source: 'telegram',
      status: 'processed',
      summary: { files_received: 1, files_queued: 0, failed: 0 },
      created_at: '2026-03-19T09:00:00',
      files: [
        {
          id: 'file-ok-1',
          batch_id: 'batch-ok',
          file_name: 'march.pdf',
          file_path: '/uploads/march.pdf',
          file_hash: 'hash-march',
          status: 'processed',
          error_message: '',
          created_at: '2026-03-19T09:00:00',
        },
      ],
    },
  ];

  const statements = [
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

  const statementRows = {
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
    'st-12': [
      {
        id: 'row-3',
        statement_id: 'st-12',
        row_index: 4,
        page_number: 1,
        raw_text: 'Taxi ride',
        amount: 1200,
        currency: 'RUB',
        direction: 'out',
        operation_date: '2026-03-15',
        posting_date: '2026-03-15',
        parse_confidence: 0.89,
      },
    ],
  };

  const exceptions = [
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

  const transferLinks = [
    {
      id: 'link-1',
      status: 'suggested',
      transaction_out_id: 'tx-4',
      transaction_in_id: 'tx-5',
      match_score: 0.91,
      rationale: 'Amounts and timestamps line up for an internal transfer.',
      fee_amount: null,
    },
  ];

  return {
    accounts,
    transactions,
    monthlyFlow,
    netWorthCurrent,
    netWorthTimeline,
    batches,
    statements,
    statementRows,
    exceptions,
    transferLinks,
    uploadCounter: 0,
  };
}

function normalizePath(url: string): string {
  return new URL(url).pathname;
}

function sortTransactions(items: Array<Record<string, unknown>>) {
  return [...items].sort((left, right) => {
    const leftKey = String(left.operation_datetime || left.posting_datetime || '');
    const rightKey = String(right.operation_datetime || right.posting_datetime || '');
    return rightKey.localeCompare(leftKey);
  });
}

function findTransaction(state: ReturnType<typeof buildScenario>, transactionId: string) {
  return state.transactions.find((item) => item.id === transactionId) || null;
}

function suggestedCategoryForTransaction(item: Record<string, unknown>): string {
  const merchant = String(item.merchant_normalized || '').toLowerCase();
  if (merchant.includes('yandex')) {
    return 'Transport';
  }
  if (String(item.direction || '') === 'in') {
    return 'Income';
  }
  return 'Reviewed';
}

function buildTransactionsResponse(
  state: ReturnType<typeof buildScenario>,
  params: URLSearchParams,
) {
  let items = sortTransactions(state.transactions);
  const query = (params.get('q') || '').trim().toLowerCase();
  const accountId = params.get('account_id') || '';
  const direction = params.get('direction') || '';
  const needsHumanReview = params.get('needs_human_review') === 'true';

  if (query) {
    items = items.filter((item) =>
      [item.description_raw, item.merchant_normalized, item.category, item.bank_category]
        .map((value) => String(value || '').toLowerCase())
        .some((value) => value.includes(query)),
    );
  }

  if (accountId) {
    items = items.filter((item) => item.account_id === accountId);
  }

  if (direction) {
    items = items.filter((item) => item.direction === direction);
  }

  if (needsHumanReview) {
    items = items.filter((item) => Boolean(item.needs_human_review));
  }

  const offset = Number(params.get('offset') || '0');
  const limit = Number(params.get('limit') || String(items.length || 100));
  const pagedItems = items.slice(offset, offset + limit);

  return {
    total: items.length,
    items: pagedItems,
  };
}


async function installScenario(page: import('@playwright/test').Page, state: ReturnType<typeof buildScenario>) {
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

    window.confirm = () => true;

    // eslint-disable-next-line no-global-assign
    Date = MockDate;
  }, { iso: FIXED_TIME_ISO });

  await page.route(/:\/\/[^/]+\/api(\/|$)/, async (route) => {
    const request = route.request();
    const pathName = normalizePath(request.url());
    const params = new URL(request.url()).searchParams;

    const fulfillJson = async (payload: unknown, status = 200) => {
      await route.fulfill({
        status,
        headers: jsonHeaders(),
        body: JSON.stringify(payload),
      });
    };

    if (request.method() === 'GET') {
      if (pathName === '/api/analytics/monthly-flow') {
        await fulfillJson(state.monthlyFlow);
        return;
      }

      if (pathName === '/api/accounts/' || pathName === '/api/accounts') {
        await fulfillJson(state.accounts);
        return;
      }

      if (pathName === '/api/networth/current') {
        await fulfillJson(state.netWorthCurrent);
        return;
      }

      if (pathName === '/api/networth/timeline') {
        await fulfillJson(state.netWorthTimeline);
        return;
      }

      if (pathName === '/api/exceptions/' || pathName === '/api/exceptions') {
        const status = params.get('status');
        await fulfillJson(status === 'open' ? state.exceptions.filter((item) => item.status === 'open') : state.exceptions);
        return;
      }

      if (pathName === '/api/transfers/links') {
        const status = params.get('status');
        const items = status
          ? state.transferLinks.filter((item) => item.status === status)
          : state.transferLinks;
        await fulfillJson(items);
        return;
      }

      if (pathName === '/api/imports/batches') {
        await fulfillJson(state.batches);
        return;
      }

      if (pathName === '/api/statements/' || pathName === '/api/statements') {
        await fulfillJson(state.statements);
        return;
      }

      const statementRowsMatch = pathName.match(/^\/api\/statements\/([^/]+)\/rows$/);
      if (statementRowsMatch) {
        await fulfillJson(state.statementRows[statementRowsMatch[1]] || []);
        return;
      }

      if (pathName === '/api/transactions/' || pathName === '/api/transactions') {
        await fulfillJson(buildTransactionsResponse(state, params));
        return;
      }

      const transactionMatch = pathName.match(/^\/api\/transactions\/([^/]+)$/);
      if (transactionMatch) {
        const tx = findTransaction(state, transactionMatch[1]);
        if (!tx) {
          await fulfillJson({ detail: 'not found' }, 404);
          return;
        }
        await fulfillJson(tx);
        return;
      }

      await fulfillJson({ detail: `unhandled GET ${pathName}` }, 404);
      return;
    }

    const transferConfirmMatch = pathName.match(/^\/api\/transfers\/links\/([^/]+)\/confirm$/);
    if (request.method() === 'POST' && transferConfirmMatch) {
      const link = state.transferLinks.find((item) => item.id === transferConfirmMatch[1]);
      if (!link) {
        await fulfillJson({ detail: 'not found' }, 404);
        return;
      }
      link.status = 'confirmed';
      await fulfillJson(link);
      return;
    }

    const transferRejectMatch = pathName.match(/^\/api\/transfers\/links\/([^/]+)\/reject$/);
    if (request.method() === 'POST' && transferRejectMatch) {
      const link = state.transferLinks.find((item) => item.id === transferRejectMatch[1]);
      if (!link) {
        await fulfillJson({ detail: 'not found' }, 404);
        return;
      }
      link.status = 'rejected';
      await fulfillJson(link);
      return;
    }

    const markReviewedMatch = pathName.match(/^\/api\/transactions\/([^/]+)\/mark-reviewed$/);
    if (request.method() === 'POST' && markReviewedMatch) {
      const tx = findTransaction(state, markReviewedMatch[1]);
      if (!tx) {
        await fulfillJson({ detail: 'not found' }, 404);
        return;
      }
      tx.review_status = 'reviewed';
      tx.review_reasons = [];
      tx.needs_human_review = false;
      await fulfillJson(tx);
      return;
    }

    const markDuplicateMatch = pathName.match(/^\/api\/transactions\/([^/]+)\/mark-duplicate$/);
    if (request.method() === 'POST' && markDuplicateMatch) {
      const tx = findTransaction(state, markDuplicateMatch[1]);
      if (!tx) {
        await fulfillJson({ detail: 'not found' }, 404);
        return;
      }
      const nextTags = new Set(Array.isArray(tx.tags) ? tx.tags.map(String) : []);
      nextTags.add('duplicate');
      tx.tags = Array.from(nextTags);
      tx.review_status = 'reviewed';
      tx.review_reasons = [];
      tx.needs_human_review = false;
      if (!String(tx.category || '').trim()) {
        tx.category = 'Duplicate';
      }
      await fulfillJson(tx);
      return;
    }

    const approveCategoryMatch = pathName.match(/^\/api\/transactions\/([^/]+)\/approve-category$/);
    if (request.method() === 'POST' && approveCategoryMatch) {
      const tx = findTransaction(state, approveCategoryMatch[1]);
      if (!tx) {
        await fulfillJson({ detail: 'not found' }, 404);
        return;
      }
      tx.category = suggestedCategoryForTransaction(tx);
      tx.review_status = 'reviewed';
      tx.review_reasons = [];
      tx.needs_human_review = false;
      await fulfillJson(tx);
      return;
    }

    const uploadMatch = pathName === '/api/imports/pdf';
    if (request.method() === 'POST' && uploadMatch) {
      state.uploadCounter += 1;
      const payload = request.postDataBuffer()?.toString('utf8') || '';
      const fileCount = Math.max(1, payload.split('filename="').length - 1);
      const batchId = `batch-upload-${state.uploadCounter}`;
      const files = Array.from({ length: fileCount }, (_, index) => ({
        id: `${batchId}-file-${index + 1}`,
        batch_id: batchId,
        file_name: `uploaded-${index + 1}.pdf`,
        file_path: `/uploads/${batchId}-${index + 1}.pdf`,
        file_hash: `${batchId}-hash-${index + 1}`,
        status: 'queued',
        error_message: '',
        created_at: '2026-03-20T10:00:00',
      }));
      const batch = {
        id: batchId,
        source: 'web',
        status: 'queued',
        summary: { files_received: fileCount, files_queued: fileCount, duplicates: 0, failed: 0 },
        created_at: '2026-03-20T10:00:00',
        files,
      };
      state.batches = [batch, ...state.batches];
      await fulfillJson(batch);
      return;
    }

    const reprocessMatch = pathName.match(/^\/api\/imports\/batches\/([^/]+)\/reprocess$/);
    if (request.method() === 'POST' && reprocessMatch) {
      const batch = state.batches.find((item) => item.id === reprocessMatch[1]);
      if (!batch) {
        await fulfillJson({ detail: 'not found' }, 404);
        return;
      }
      batch.status = 'processed';
      batch.files = batch.files.map((file) => ({
        ...file,
        status: file.status === 'failed' ? 'processed' : file.status,
        error_message: '',
      }));
      batch.summary = {
        files_received: batch.files.length,
        files_queued: 0,
        duplicates: batch.files.filter((file) => file.status === 'duplicate').length,
        failed: batch.files.filter((file) => file.status === 'failed').length,
      };
      await fulfillJson(batch);
      return;
    }

    const deleteBatchMatch = pathName.match(/^\/api\/imports\/batches\/([^/]+)$/);
    if (request.method() === 'DELETE' && deleteBatchMatch) {
      const batchId = deleteBatchMatch[1];
      const batch = state.batches.find((item) => item.id === batchId);
      state.batches = state.batches.filter((item) => item.id !== batchId);
      await fulfillJson({
        batch_id: batchId,
        deleted_import_files: batch?.files.length || 0,
        deleted_statements: 0,
        deleted_statement_rows: 0,
        deleted_transactions: 0,
        deleted_transfer_links: 0,
        deleted_balance_snapshots: 0,
        deleted_exceptions: 0,
        deleted_disk_files: batch?.files.length || 0,
      });
      return;
    }

    await fulfillJson({ detail: `unhandled ${request.method()} ${pathName}` }, 404);
  });
}

test.beforeEach(async ({ page }) => {
  const state = buildScenario();
  await page.coverage.startJSCoverage({
    resetOnNavigation: false,
    reportAnonymousScripts: false,
    includeRawScriptCoverage: true,
  });
  await installScenario(page, state);
});

test.afterEach(async ({ page }) => {
  const entries = (await page.coverage.stopJSCoverage()) as CoverageEntry[];
  coverageEntries.push(...entries);
});

test.afterAll(async () => {
  const reportDir = path.join(process.cwd(), 'coverage');
  const monocartDir = path.join(reportDir, 'e2e-monocart');
  const coverageReport = new CoverageReport({
    name: 'Playwright functional e2e',
    outputDir: monocartDir,
    reports: [[
      'json-summary',
      { file: 'summary.json' },
    ]],
    sourcePath: (filePath: string) => (filePath.includes('/src/') ? filePath : ''),
  });

  await coverageReport.add(coverageEntries);
  await coverageReport.generate();

  const summaryPath = path.join(monocartDir, 'summary.json');
  const summary = JSON.parse(fs.readFileSync(summaryPath, 'utf8'));

  fs.mkdirSync(reportDir, { recursive: true });
  fs.writeFileSync(
    path.join(reportDir, 'e2e-coverage-summary.json'),
    JSON.stringify(summary, null, 2),
    'utf8',
  );
});

test('Overview exposes trend details for the current month story', async ({ page }) => {
  await page.goto('/');
  await page.getByTestId('overview-page').waitFor();

  const chart = page.getByRole('img', { name: 'Monthly spending trend' });
  await chart.locator('rect[data-period="2026-01"]').hover();

  await expect(chart.locator('.overview-trend-callout')).toBeVisible();
  await expect(chart.locator('.overview-trend-callout-title')).toHaveText(/January 2026/);
  await expect(chart.locator('.overview-trend-callout-value')).toHaveText(/80\s*000/);
  await expect(page.locator('.overview-trend-summary')).toContainText('January 2026');
});

test('Transactions deep-link a focused uncategorized item into Review', async ({ page }) => {
  await page.goto('/transactions');
  await page.getByRole('searchbox', { name: 'Поиск' }).waitFor();

  await page.getByRole('button', { name: /Фильтры/ }).click();
  await page.getByRole('combobox', { name: 'Режим списка' }).selectOption('uncategorized');

  await expect(page.locator('.tx-ledger-row')).toHaveCount(1);
  await page.locator('.tx-ledger-row').first().click();

  await expect(page.getByRole('dialog')).toContainText('Yandex Go');
  await expect(page.getByRole('dialog')).toContainText('Нужно внимание');

  await page.getByRole('button', { name: 'Открыть проверку' }).click();

  await expect(page).toHaveURL(/\/review$/);
  await expect(page.locator('.review-detail')).toContainText('Yandex Go');
  await expect(page.locator('.review-detail')).toContainText('Assign Transport');
});

test('Review confirms a suggested transfer and updates the queue summary', async ({ page }) => {
  await page.goto('/review');
  await page.locator('.review-header-meta').waitFor();

  await expect(page.locator('.review-header-meta')).toContainText('3 unresolved');
  await page.locator('[data-review-lane="transfer"] .review-row-main').click();
  await page
    .getByRole('group', { name: 'Actions' })
    .getByRole('button', { name: 'Confirm', exact: true })
    .click();

  await expect(page.locator('.wf-feedback.is-success')).toHaveText('Transfer confirmed.');
  await expect(page.locator('.review-header-meta')).toContainText('2 unresolved');
  await expect(page.locator('[data-review-lane="transfer"]')).toHaveCount(0);
});

test('Accounts open Transactions with the selected account already applied', async ({ page }) => {
  await page.goto('/accounts');
  await page.getByRole('main').getByRole('heading', { name: 'Accounts' }).waitFor();

  await page.locator('.accts-card').first().getByRole('button', { name: 'Transactions' }).click();

  await expect(page).toHaveURL(/\/transactions$/);
  await page.getByRole('searchbox', { name: 'Поиск' }).waitFor();
  await page.getByRole('button', { name: /Фильтры/ }).click();

  await expect(page.getByRole('combobox', { name: 'Счет' })).toHaveValue('acc-1');
  await expect(page.locator('.tx-ledger-row', { hasText: 'Store' })).toHaveCount(1);
  await expect(page.locator('.tx-ledger-row', { hasText: 'Yandex Go' })).toHaveCount(0);
});

test('Statements upload a PDF, expand parsed rows, and reprocess a failed batch', async ({ page }) => {
  await page.goto('/statements');
  await page.getByRole('main').getByRole('heading', { name: 'Statements', level: 2 }).waitFor();

  await page.locator('input[type="file"]').setInputFiles({
    name: 'statement.pdf',
    mimeType: 'application/pdf',
    buffer: Buffer.from('%PDF-1.4 functional test'),
  });

  await expect(page.locator('.stmts-banner')).toContainText('1 queued for processing');

  await page.getByRole('button', { name: 'Show' }).first().click();
  await expect(page.getByRole('table', { name: 'Statement rows' })).toContainText('Salary');

  await page.getByRole('button', { name: 'Reprocess' }).click();
  await expect(page.locator('.stmts-batches')).toContainText('1 processed');
});
