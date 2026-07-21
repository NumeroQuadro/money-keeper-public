import type { RuntimeConfig } from '../types/runtime';

const trimTrailingSlash = (value: string): string => value.replace(/\/+$/, '');
const ADMIN_TOKEN_STORAGE_KEY = 'mk_admin_token';

function readStoredAdminToken(): string {
  try {
    if (typeof window === 'undefined' || !window.localStorage) {
      return '';
    }
    return window.localStorage.getItem(ADMIN_TOKEN_STORAGE_KEY) || '';
  } catch {
    return '';
  }
}

export function getRuntimeConfig(): RuntimeConfig {
  const runtime = window.APP_CONFIG ?? {};
  const storedAdminToken = readStoredAdminToken();

  const apiBase = trimTrailingSlash(
    runtime.apiBase || import.meta.env.VITE_API_BASE || '/api',
  );

  return {
    apiBase,
    currency: runtime.currency || import.meta.env.VITE_CURRENCY || 'RUB',
    adminToken:
      runtime.adminToken ||
      storedAdminToken ||
      import.meta.env.VITE_ADMIN_TOKEN ||
      '',
    appTitle:
      runtime.appTitle ||
      import.meta.env.VITE_APP_TITLE ||
      'Money Keeper',
  };
}
