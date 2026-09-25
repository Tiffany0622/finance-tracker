let csrf = '';
let csrfPromise: Promise<void> | null = null;
let refreshPromise: Promise<void> | null = null;
export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}
export function prepareCsrf(): Promise<void> {
  if (!csrfPromise) {
    csrfPromise = (async () => {
      const response = await fetch('/api/v1/auth/csrf', {credentials: 'same-origin', cache: 'no-store'});
      if (!response.ok) throw new Error('無法建立安全連線，請稍後重試。');
      csrf = (await response.json()).token;
    })().finally(() => { csrfPromise = null; });
  }
  return csrfPromise;
}
export async function api<T>(path: string, options: RequestInit = {}, retry = true, responseKind: 'json' | 'blob' = 'json'): Promise<T> {
  const method = options.method ?? 'GET';
  if (method !== 'GET' && !csrf) await prepareCsrf();
  let response: Response;
  try {
    response = await fetch('/api/v1' + path, {...options, credentials: 'same-origin', cache: 'no-store',
      headers: {'Content-Type': 'application/json', ...(method !== 'GET' ? {'X-CSRF-Token': csrf} : {}), ...options.headers}});
  } catch { throw new ApiError(0, 'offline', '目前無法連線，畫面上的資料可能已過期。'); }
  if (response.status === 401 && retry && !path.startsWith('/auth/login') && !path.startsWith('/auth/refresh') && !path.startsWith('/auth/totp')) {
    if (!refreshPromise) refreshPromise = api('/auth/refresh', {method: 'POST'}, false).then(() => {}).finally(() => { refreshPromise = null; });
    await refreshPromise;
    return api<T>(path, options, false, responseKind);
  }
  if (response.ok && responseKind === 'blob') return await response.blob() as T;
  let data;
  try { data = await response.json(); }
  catch { throw new ApiError(response.status, 'invalid_response', '服務暫時無法回應，請稍後重試。'); }
  if (!response.ok) throw new ApiError(response.status, data.code, data.message || '操作失敗，請重試。');
  if (path === '/auth/logout') csrf = '';
  return data as T;
}
