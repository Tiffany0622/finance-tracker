import {beforeEach, expect, test, vi} from 'vitest';
beforeEach(()=>{vi.resetModules();vi.unstubAllGlobals();});
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
