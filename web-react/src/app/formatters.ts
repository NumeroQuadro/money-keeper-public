import { getRuntimeConfig } from './runtimeConfig';

const toDate = (value: string | null | undefined): Date | null => {
  if (!value) {
    return null;
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return null;
  }

  return date;
};

export function formatMoney(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return '—';
  }

  return new Intl.NumberFormat('ru-RU', {
    style: 'currency',
    currency: getRuntimeConfig().currency,
    maximumFractionDigits: 2,
  }).format(value);
}

export function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return '0';
  }

  return new Intl.NumberFormat('ru-RU').format(value);
}

export function formatDate(value: string | null | undefined): string {
  const date = toDate(value);
  if (!date) {
    return value || '—';
  }

  return date.toLocaleDateString('ru-RU', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  });
}

export function formatDateTime(value: string | null | undefined): string {
  const date = toDate(value);
  if (!date) {
    return value || '—';
  }

  return date.toLocaleString('ru-RU', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}
