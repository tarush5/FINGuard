/**
 * API client.
 *
 * Handles the auth header, transparent refresh-token rotation (a single
 * in-flight refresh shared by all concurrent 401s), and turns the backend's
 * error envelope into a typed `ApiError` the UI can render.
 */

export const API_BASE = import.meta.env.VITE_API_URL ?? '/api/v1';

const ACCESS_KEY = 'finguard.access';
const REFRESH_KEY = 'finguard.refresh';

export interface ApiErrorBody {
  code: string;
  message: string;
  request_id?: string;
  details?: Record<string, unknown>;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId?: string;
  readonly details?: Record<string, unknown>;

  constructor(status: number, body: ApiErrorBody) {
    super(body.message);
    this.name = 'ApiError';
    this.status = status;
    this.code = body.code;
    this.requestId = body.request_id;
    this.details = body.details;
  }
}

export const tokenStore = {
  access: () => localStorage.getItem(ACCESS_KEY),
  refresh: () => localStorage.getItem(REFRESH_KEY),
  set(access: string, refresh: string) {
    localStorage.setItem(ACCESS_KEY, access);
    localStorage.setItem(REFRESH_KEY, refresh);
  },
  clear() {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
  },
};

type Listener = () => void;
const unauthorizedListeners = new Set<Listener>();

export function onUnauthorized(listener: Listener): () => void {
  unauthorizedListeners.add(listener);
  return () => unauthorizedListeners.delete(listener);
}

function notifyUnauthorized() {
  tokenStore.clear();
  unauthorizedListeners.forEach((listener) => listener());
}

let refreshInFlight: Promise<boolean> | null = null;

async function refreshAccessToken(): Promise<boolean> {
  const refresh = tokenStore.refresh();
  if (!refresh) return false;

  // Collapse concurrent refreshes: the server rotates (and invalidates) the
  // token, so a second call with the same token would be rejected as reuse.
  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      try {
        const response = await fetch(`${API_BASE}/auth/refresh`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: refresh }),
        });
        if (!response.ok) return false;
        const data = await response.json();
        tokenStore.set(data.access_token, data.refresh_token);
        return true;
      } catch {
        return false;
      } finally {
        // Release on the next tick so queued callers observe the result.
        setTimeout(() => {
          refreshInFlight = null;
        }, 0);
      }
    })();
  }
  return refreshInFlight;
}

export interface RequestOptions extends Omit<RequestInit, 'body'> {
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined | null>;
  skipAuth?: boolean;
  retryOn401?: boolean;
}

export function buildUrl(path: string, query?: RequestOptions['query']): string {
  const url = `${API_BASE}${path.startsWith('/') ? path : `/${path}`}`;
  if (!query) return url;
  const params = new URLSearchParams();
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      params.append(key, String(value));
    }
  });
  const qs = params.toString();
  return qs ? `${url}?${qs}` : url;
}

export async function request<T = unknown>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, query, skipAuth, retryOn401 = true, headers, ...rest } = options;

  const finalHeaders = new Headers(headers);
  if (body !== undefined && !(body instanceof FormData)) {
    finalHeaders.set('Content-Type', 'application/json');
  }
  if (!skipAuth) {
    const token = tokenStore.access();
    if (token) finalHeaders.set('Authorization', `Bearer ${token}`);
  }

  const response = await fetch(buildUrl(path, query), {
    ...rest,
    headers: finalHeaders,
    body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
  });

  if (response.status === 401 && !skipAuth && retryOn401) {
    const refreshed = await refreshAccessToken();
    if (refreshed) {
      return request<T>(path, { ...options, retryOn401: false });
    }
    notifyUnauthorized();
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const payload = text ? safeJson(text) : null;

  if (!response.ok) {
    const envelope = (payload as { error?: ApiErrorBody } | null)?.error;
    throw new ApiError(response.status, {
      code: envelope?.code ?? `HTTP_${response.status}`,
      message: envelope?.message ?? response.statusText ?? 'The request failed.',
      request_id: envelope?.request_id,
      details: envelope?.details,
    });
  }
  return payload as T;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return { raw: text };
  }
}

export const api = {
  get: <T>(path: string, query?: RequestOptions['query']) => request<T>(path, { method: 'GET', query }),
  post: <T>(path: string, body?: unknown, query?: RequestOptions['query']) =>
    request<T>(path, { method: 'POST', body, query }),
  patch: <T>(path: string, body?: unknown) => request<T>(path, { method: 'PATCH', body }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
};

/**
 * Subscribe to the transaction SSE stream.
 *
 * `EventSource` cannot send an Authorization header, and putting a token in a
 * query string would leak it into access logs, so the stream is read with
 * `fetch` + a streaming reader instead.
 *
 * Returns an unsubscribe function that aborts the request.
 */
export function streamTransactions(
  onBatch: (transactions: any[]) => void,
  options: { limit?: number; interval?: number; onError?: (error: unknown) => void } = {},
): () => void {
  const controller = new AbortController();
  const { limit = 12, interval = 2, onError } = options;

  (async () => {
    try {
      const token = tokenStore.access();
      const response = await fetch(buildUrl('/transactions/live', { limit, interval }), {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
        signal: controller.signal,
      });
      if (!response.ok || !response.body) {
        onError?.(new Error(`stream failed: ${response.status}`));
        return;
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split('\n\n');
        buffer = frames.pop() ?? '';
        for (const frame of frames) {
          const line = frame.split('\n').find((l) => l.startsWith('data:'));
          if (!line) continue;
          try {
            const payload = JSON.parse(line.slice(5).trim());
            if (Array.isArray(payload.transactions) && payload.transactions.length) {
              onBatch(payload.transactions);
            }
          } catch {
            /* keep-alive or partial frame; ignore */
          }
        }
      }
    } catch (error) {
      if ((error as { name?: string })?.name !== 'AbortError') onError?.(error);
    }
  })();

  return () => controller.abort();
}
