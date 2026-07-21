import type { AccountSummary, TransactionSummary } from '../api/client';

type ReviewStateInput =
  | string
  | Pick<TransactionSummary, 'review_status' | 'review_reasons' | 'needs_human_review'>
  | null
  | undefined;

interface ProviderTone {
  label: string;
  shortLabel: string;
  accent: string;
  background: string;
}

const DEFAULT_PROVIDER: ProviderTone = {
  label: 'Счет',
  shortLabel: 'Счет',
  accent: '#355070',
  background: 'rgba(53, 80, 112, 0.12)',
};

const PROVIDER_TONES: Record<string, ProviderTone> = {
  ozon: {
    label: 'Ozon Bank',
    shortLabel: 'Ozon',
    accent: '#2667ff',
    background: 'rgba(38, 103, 255, 0.12)',
  },
  sber: {
    label: 'Sber',
    shortLabel: 'Sber',
    accent: '#1f9d55',
    background: 'rgba(31, 157, 85, 0.12)',
  },
  sberbank: {
    label: 'Sber',
    shortLabel: 'Sber',
    accent: '#1f9d55',
    background: 'rgba(31, 157, 85, 0.12)',
  },
  yandex: {
    label: 'Yandex Bank',
    shortLabel: 'Yandex',
    accent: '#ff8c42',
    background: 'rgba(255, 140, 66, 0.14)',
  },
  spb: {
    label: 'BSPB',
    shortLabel: 'BSPB',
    accent: '#b83280',
    background: 'rgba(184, 50, 128, 0.12)',
  },
  bspb: {
    label: 'BSPB',
    shortLabel: 'BSPB',
    accent: '#b83280',
    background: 'rgba(184, 50, 128, 0.12)',
  },
};

const MEANING_LABELS: Record<string, string> = {
  spend: 'Расход',
  income: 'Поступление',
  internal_transfer: 'Свой перевод',
  refund: 'Возврат',
  cashback: 'Кэшбэк',
  interest: 'Проценты',
  fee: 'Комиссия',
  unknown: 'Нужно внимание',
};

const MEANING_DESCRIPTIONS: Record<string, string> = {
  spend: 'Учитываем в расходах.',
  income: 'Учитываем в поступлениях.',
  internal_transfer: 'Это перевод между вашими счетами. В траты и поступления не попадает.',
  refund: 'Учитываем как возврат.',
  cashback: 'Учитываем как кэшбэк.',
  interest: 'Учитываем как проценты.',
  fee: 'Учитываем как комиссию.',
  unknown: 'Нужна проверка, чтобы точно учесть операцию.',
};

export function getMeaningLabel(meaning: string | null | undefined): string {
  if (!meaning) {
    return MEANING_LABELS.unknown;
  }

  return MEANING_LABELS[meaning] || meaning.replace(/_/g, ' ');
}

export function getMeaningDescription(meaning: string | null | undefined): string {
  if (!meaning) {
    return MEANING_DESCRIPTIONS.unknown;
  }

  return MEANING_DESCRIPTIONS[meaning] || MEANING_DESCRIPTIONS.unknown;
}

function getReviewStateParts(input: ReviewStateInput): {
  reviewStatus: string | null | undefined;
  reviewReasons: string[];
  needsHumanReview: boolean | null;
} {
  if (typeof input === 'string' || input === null || input === undefined) {
    return {
      reviewStatus: input,
      reviewReasons: [],
      needsHumanReview: null,
    };
  }

  return {
    reviewStatus: input.review_status,
    reviewReasons: Array.isArray(input.review_reasons) ? input.review_reasons : [],
    needsHumanReview:
      typeof input.needs_human_review === 'boolean' ? input.needs_human_review : null,
  };
}

export function getReviewLabel(input: ReviewStateInput): string {
  return getReviewTone(input) === 'ready' ? 'Готово' : 'Проверить';
}

export function getReviewTone(input: ReviewStateInput): 'ready' | 'attention' {
  const { reviewStatus, reviewReasons, needsHumanReview } = getReviewStateParts(input);
  if (needsHumanReview !== null) {
    return needsHumanReview ? 'attention' : 'ready';
  }
  if (reviewReasons.length > 0) {
    return 'attention';
  }
  return reviewStatus === 'reviewed' ? 'ready' : 'attention';
}

export function getTransactionTitle(item: TransactionSummary): string {
  return item.merchant_normalized || item.description_raw || 'Без названия';
}

export function getTransactionSignedAmount(item: TransactionSummary): number {
  const amount = Math.abs(Number(item.amount) || 0);
  return item.direction === 'out' ? -amount : amount;
}

export function getProviderTone(provider: string | null | undefined): ProviderTone {
  if (!provider) {
    return DEFAULT_PROVIDER;
  }

  return PROVIDER_TONES[provider.toLowerCase()] || DEFAULT_PROVIDER;
}

export function getAccountDisplay(account: AccountSummary | null | undefined): string {
  if (!account) {
    return 'Счет не найден';
  }

  return account.display_name || account.masked_identifier || account.account_type || 'Счет';
}

export function getAccountTone(account: AccountSummary | null | undefined): ProviderTone {
  if (!account) {
    return DEFAULT_PROVIDER;
  }

  const tone = getProviderTone(account.provider);
  return {
    ...tone,
    label: `${tone.label} · ${getAccountDisplay(account)}`,
  };
}

export function getTransactionMeta(
  item: TransactionSummary,
  account: AccountSummary | null | undefined,
): string {
  const parts = [item.category || getMeaningLabel(item.meaning)];

  if (account) {
    parts.push(getAccountDisplay(account));
  }

  if (getReviewTone(item) === 'attention') {
    parts.push(getReviewLabel(item));
  }

  return parts.filter(Boolean).join(' · ');
}
