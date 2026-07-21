import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, vi } from 'vitest';
import { apiClientMock, resetApiClientMock } from './apiClientMock';

const DEFAULT_APP_CONFIG = {
  apiBase: '/api',
  appTitle: 'Money Keeper',
  currency: 'RUB',
};

const localStorageState = new Map<string, string>();
const localStorageMock: Storage = {
  get length() {
    return localStorageState.size;
  },
  clear() {
    localStorageState.clear();
  },
  getItem(key: string) {
    return localStorageState.get(key) ?? null;
  },
  key(index: number) {
    return Array.from(localStorageState.keys())[index] ?? null;
  },
  removeItem(key: string) {
    localStorageState.delete(key);
  },
  setItem(key: string, value: string) {
    localStorageState.set(key, String(value));
  },
};

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return {
    ...actual,
    apiClient: apiClientMock,
  };
});

declare global {
  interface Window {
    APP_CONFIG?: {
      apiBase?: string;
      adminToken?: string;
      appTitle?: string;
      currency?: string;
    };
  }
}

afterEach(() => {
  cleanup();
  resetApiClientMock();
  localStorageMock.clear();
  window.APP_CONFIG = { ...DEFAULT_APP_CONFIG };
});

Object.defineProperty(window, 'localStorage', {
  configurable: true,
  value: localStorageMock,
});

window.APP_CONFIG = { ...DEFAULT_APP_CONFIG };
