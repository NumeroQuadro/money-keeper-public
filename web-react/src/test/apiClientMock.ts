import { vi } from 'vitest';

export const apiClientMock = {
  health: vi.fn(),
  metricsQuality: vi.fn(),
  accounts: vi.fn(),
  transactions: vi.fn(),
  transactionById: vi.fn(),
  importBatches: vi.fn(),
  statements: vi.fn(),
  statementRows: vi.fn(),
  uploadStatements: vi.fn(),
  reprocessBatch: vi.fn(),
  deleteBatch: vi.fn(),
  transferLinks: vi.fn(),
  confirmTransferLink: vi.fn(),
  rejectTransferLink: vi.fn(),
  approveTransactionCategory: vi.fn(),
  markTransactionReviewed: vi.fn(),
  markTransactionDuplicate: vi.fn(),
  rules: vi.fn(),
  createRule: vi.fn(),
  previewRules: vi.fn(),
  applyRules: vi.fn(),
  exceptions: vi.fn(),
  resolveException: vi.fn(),
  ignoreException: vi.fn(),
  approveExceptionCategory: vi.fn(),
  markExceptionDuplicate: vi.fn(),
  netWorthCurrent: vi.fn(),
  netWorthTimeline: vi.fn(),
  monthlyFlow: vi.fn(),
  spendMix: vi.fn(),
  topMerchants: vi.fn(),
};

export function resetApiClientMock(): void {
  (Object.values(apiClientMock) as Array<ReturnType<typeof vi.fn>>).forEach((mockFn) => {
    mockFn.mockReset();
  });
}
