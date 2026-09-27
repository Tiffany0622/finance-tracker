import {afterEach, beforeEach, expect, test, vi} from 'vitest';
beforeEach(()=>{vi.resetModules();vi.unstubAllGlobals();});
afterEach(()=>{vi.useRealTimers();});
test('concurrent startup and mutations share one CSRF token request',async()=>{
  let release!: (value: Response) => void;
  const tokenResponse=new Promise<Response>(resolve=>{release=resolve;});
  const tokens: string[]=[];
  const fetchMock=vi.fn(async(url:string,options?:RequestInit)=>{
    if(url.endsWith('/csrf'))return tokenResponse;
    tokens.push(new Headers(options?.headers).get('X-CSRF-Token')??'');
    return Response.json({ok:true});
  });
  vi.stubGlobal('fetch',fetchMock);
  const {api,prepareCsrf}=await import('../../src/api/client');
  const requests=[prepareCsrf(),prepareCsrf(),api('/auth/login',{method:'POST'}),api('/settings',{method:'PUT'})];
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(tokens).toEqual([]);
  release(Response.json({token:'shared-signed-token'}));
  await Promise.all(requests);
  expect(tokens).toEqual(['shared-signed-token','shared-signed-token']);
  expect(fetchMock.mock.calls.filter(([url])=>url.endsWith('/csrf'))).toHaveLength(1);
});
test('failed CSRF initialization can be retried without a stuck promise',async()=>{
  const fetchMock=vi.fn().mockResolvedValueOnce(Response.json({}, {status:503})).mockResolvedValueOnce(Response.json({token:'recovered-token'}));
  vi.stubGlobal('fetch',fetchMock);
  const {prepareCsrf}=await import('../../src/api/client');
  const results=await Promise.allSettled([prepareCsrf(),prepareCsrf()]);
  expect(results.map(result=>result.status)).toEqual(['rejected','rejected']);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  await prepareCsrf();
  expect(fetchMock).toHaveBeenCalledTimes(2);
});
test('concurrent expired requests rotate the session once and resume',async()=>{
  let refreshed=false;let rotations=0;
  const fetchMock=vi.fn(async(url:string)=>{
    if(url.endsWith('/csrf'))return Response.json({token:'signed-token'});
    if(url.endsWith('/refresh')){rotations++;await new Promise(r=>setTimeout(r,5));refreshed=true;return Response.json({message:'ok'});}
    return refreshed?Response.json({ok:true}):Response.json({code:'login_required'},{status:401});
  });
  vi.stubGlobal('fetch',fetchMock);
  const {api}=await import('../../src/api/client');
  const results=await Promise.all([api('/status'),api('/auth/me')]);
  expect(rotations).toBe(1);expect(results).toEqual([{ok:true},{ok:true}]);
});
test('network failure never returns a successful stale response',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockRejectedValue(new TypeError('network')));
  const {api}=await import('../../src/api/client');
  await expect(api('/status')).rejects.toMatchObject({code:'offline',status:0});
});

function stalled(signal:AbortSignal) {
  return new Promise<Response>((_,reject)=>signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true}));
}
test('stalled request times out once, aborts and permits an explicit retry',async()=>{
  vi.useFakeTimers();
  let signal!:AbortSignal;
  const fetchMock=vi.fn((_url:string,options:RequestInit)=>{signal=options.signal!;return stalled(signal);});
  vi.stubGlobal('fetch',fetchMock);
  const {api}=await import('../../src/api/client');
  const failure=expect(api('/capture/drafts')).rejects.toMatchObject({code:'request_timeout'});
  await vi.advanceTimersByTimeAsync(30000);await failure;
  expect(signal.aborted).toBe(true);expect(fetchMock).toHaveBeenCalledTimes(1);
  fetchMock.mockResolvedValueOnce(Response.json({ok:true}));
  expect(await api('/capture/drafts')).toEqual({ok:true});expect(vi.getTimerCount()).toBe(0);
});
test('timeout also covers a response body that never finishes',async()=>{
  vi.useFakeTimers();
  vi.stubGlobal('fetch',vi.fn(async(_url:string,options:RequestInit)=>({ok:true,status:200,json:()=>stalled(options.signal!)})));
  const {api}=await import('../../src/api/client');
  const failure=expect(api('/capture/drafts')).rejects.toMatchObject({code:'request_timeout'});
  await vi.advanceTimersByTimeAsync(30000);await failure;expect(vi.getTimerCount()).toBe(0);
});
test('CSRF timeout releases the shared promise so forms can retry',async()=>{
  vi.useFakeTimers();
  const fetchMock=vi.fn((_url:string,options:RequestInit)=>stalled(options.signal!));
  vi.stubGlobal('fetch',fetchMock);
  const {prepareCsrf}=await import('../../src/api/client');
  const result=Promise.allSettled([prepareCsrf(),prepareCsrf()]);
  await vi.advanceTimersByTimeAsync(30000);
  expect((await result).map(r=>r.status)).toEqual(['rejected','rejected']);expect(fetchMock).toHaveBeenCalledTimes(1);
  fetchMock.mockResolvedValueOnce(Response.json({token:'synthetic-recovered'}));
  await prepareCsrf();expect(fetchMock).toHaveBeenCalledTimes(2);
});
test('refresh timeout releases every waiting request without replaying mutations',async()=>{
  vi.useFakeTimers();let rotations=0;
  vi.stubGlobal('fetch',vi.fn(async(url:string,options:RequestInit)=>{
    if(url.endsWith('/csrf'))return Response.json({token:'synthetic-token'});
    if(url.endsWith('/refresh')){rotations++;return stalled(options.signal!);}
    return Response.json({code:'login_required'},{status:401});
  }));
  const {api}=await import('../../src/api/client');
  const result=Promise.allSettled([api('/status'),api('/auth/me')]);
  await vi.advanceTimersByTimeAsync(30000);
  expect((await result).map(r=>r.status)).toEqual(['rejected','rejected']);expect(rotations).toBe(1);
});
test('file upload has a bounded longer deadline and never retries itself',async()=>{
  vi.useFakeTimers();let uploads=0;
  vi.stubGlobal('fetch',vi.fn(async(url:string,options:RequestInit)=>{
    if(url.endsWith('/csrf'))return Response.json({token:'synthetic-token'});
    uploads++;return stalled(options.signal!);
  }));
  const {api}=await import('../../src/api/client');
  let finished=false;
  const request=api('/capture/image',{method:'POST',body:new Blob(['synthetic'])}).finally(()=>{finished=true;});
  const failure=expect(request).rejects.toMatchObject({code:'request_timeout'});
  await vi.advanceTimersByTimeAsync(30000);expect(finished).toBe(false);
  await vi.advanceTimersByTimeAsync(90000);await failure;expect(uploads).toBe(1);
});
