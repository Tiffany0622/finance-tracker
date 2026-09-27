let csrf = '';
let csrfPromise: Promise<void> | null = null;
let refreshPromise: Promise<void> | null = null;
export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}
// Bound both fetching headers and reading the body. Otherwise a stalled response
// leaves every form's busy state (including its close button) locked indefinitely.
async function request(path:string, options:RequestInit, responseKind:'json'|'blob'='json') {
  const controller=new AbortController();
  let timedOut=false;
  const abort=()=>controller.abort();
  if(options.signal?.aborted)abort();
  options.signal?.addEventListener('abort',abort,{once:true});
  const timer=setTimeout(()=>{timedOut=true;controller.abort();},options.body instanceof Blob||responseKind==='blob'?120000:30000);
  try {
    const response=await fetch('/api/v1'+path,{...options,signal:controller.signal,credentials:'same-origin',cache:'no-store'});
    let data:unknown;
    try {data=response.ok&&responseKind==='blob'?await response.blob():await response.json();}
    catch(e){if(controller.signal.aborted)throw e;throw new ApiError(response.status,'invalid_response','服務暫時無法回應，請稍後重試。');}
    return {response,data};
  } catch(e) {
    if(e instanceof ApiError)throw e;
    if(timedOut)throw new ApiError(0,'request_timeout','連線等待逾時，已保留畫面上的輸入。伺服器可能已完成操作，請重新載入確認後再重試。');
    if(options.signal?.aborted)throw new ApiError(0,'request_cancelled','已停止等待回應，請重新載入確認操作結果。');
    throw new ApiError(0,'offline','目前無法連線，畫面上的資料可能已過期。請重新載入確認操作結果後再重試。');
  } finally {
    clearTimeout(timer);options.signal?.removeEventListener('abort',abort);
  }
}
export function prepareCsrf(): Promise<void> {
  if (!csrfPromise) {
    csrfPromise = (async () => {
      const {response,data} = await request('/auth/csrf',{});
      if (!response.ok) throw new Error('無法建立安全連線，請稍後重試。');
      csrf = (data as {token:string}).token;
    })().finally(() => { csrfPromise = null; });
  }
  return csrfPromise;
}
export async function api<T>(path: string, options: RequestInit = {}, retry = true, responseKind: 'json' | 'blob' = 'json'): Promise<T> {
  const method = options.method ?? 'GET';
  if (method !== 'GET' && !csrf) await prepareCsrf();
  const {response,data}=await request(path,{...options,
    headers:{'Content-Type':'application/json',...(method!=='GET'?{'X-CSRF-Token':csrf}:{}),...options.headers}},responseKind);
  if (response.status === 401 && retry && !path.startsWith('/auth/login') && !path.startsWith('/auth/refresh') && !path.startsWith('/auth/totp')) {
    if (!refreshPromise) refreshPromise = api('/auth/refresh', {method: 'POST'}, false).then(() => {}).finally(() => { refreshPromise = null; });
    await refreshPromise;
    return api<T>(path, options, false, responseKind);
  }
  if (!response.ok) {const error=data as {code:string;message?:string}|null;throw new ApiError(response.status,error?.code??'request_failed',error?.message||'操作失敗，請重試。');}
  if (path === '/auth/logout') csrf = '';
  return data as T;
}
