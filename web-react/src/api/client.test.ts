import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.unmock('./client');

interface MockResponseOptions {
  ok?: boolean;
  status?: number;
  json?: unknown;
}

function mockResponse(options: MockResponseOptions = {}): Response {
  const {
    ok = true,
    status = 200,
    json = {},
  } = options;

  return {
    ok,
    status,
    json: vi.fn().mockResolvedValue(json),
  } as unknown as Response;
}

describe('apiClient', () => {
  beforeEach(() => {
    window.localStorage.removeItem('mk_admin_token');
    window.APP_CONFIG = {
      apiBase: '/api/',
      appTitle: 'Money Keeper',
      currency: 'RUB',
    };
    vi.restoreAllMocks();
  });

  it('builds GET requests with trimmed base URLs, filtered query params, and stored admin token headers', async () => {
    window.localStorage.setItem('mk_admin_token', 'stored-token');

    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(mockResponse({ json: { items: [], total: 0 } }));

    const { apiClient } = await import('./client');

    await apiClient.transactions({
      q: 'coffee',
      start: '2026-03-01',
      end: '2026-03-31',
      include_transfers: true,
      empty: '',
      ignored: null,
      skipped: undefined,
    });

    expect(fetchSpy).toHaveBeenCalledTimes(1);

    const [requestUrl, requestInit] = fetchSpy.mock.calls[0];
    const url = new URL(String(requestUrl));
    const headers = requestInit?.headers as Headers;

    expect(url.pathname).toBe('/api/transactions/');
    expect(url.searchParams.get('q')).toBe('coffee');
    expect(url.searchParams.get('start')).toBe('2026-03-01');
    expect(url.searchParams.get('end')).toBe('2026-03-31');
    expect(url.searchParams.get('include_transfers')).toBe('true');
    expect(url.searchParams.has('empty')).toBe(false);
    expect(url.searchParams.has('ignored')).toBe(false);
    expect(requestInit?.method).toBe('GET');
    expect(headers.get('Accept')).toBe('application/json');
    expect(headers.get('X-Admin-Token')).toBe('stored-token');
  });

  it('serializes JSON, deletes, and multipart uploads through the matching helpers', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(mockResponse({ json: { ok: true } }));

    const { apiClient } = await import('./client');

    await apiClient.confirmTransferLink('link-1');
    await apiClient.deleteBatch('batch-1');
    await apiClient.uploadStatements([
      new File(['first'], 'one.pdf', { type: 'application/pdf' }),
      new File(['second'], 'two.pdf', { type: 'application/pdf' }),
    ]);

    expect(fetchSpy).toHaveBeenCalledTimes(3);

    const [, postInit] = fetchSpy.mock.calls[0];
    expect(postInit?.method).toBe('POST');
    expect((postInit?.headers as Headers).get('Content-Type')).toBeNull();

    const [, deleteInit] = fetchSpy.mock.calls[1];
    expect(deleteInit?.method).toBe('DELETE');

    const [, uploadInit] = fetchSpy.mock.calls[2];
    const uploadBody = uploadInit?.body as FormData;

    expect(uploadInit?.method).toBe('POST');
    expect(uploadBody.get('source')).toBe('web');
    expect(uploadBody.getAll('files')).toHaveLength(2);
  });

  it('raises ApiError with the response status for failed requests', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(mockResponse({ ok: false, status: 503 }));

    const { apiClient } = await import('./client');

    await expect(apiClient.rules()).rejects.toEqual(
      expect.objectContaining({
        name: 'ApiError',
        status: 503,
      }),
    );

    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });
});
