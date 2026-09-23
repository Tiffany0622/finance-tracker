import {beforeEach, expect, test, vi} from 'vitest';
beforeEach(()=>{vi.resetModules();vi.unstubAllGlobals();});
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
