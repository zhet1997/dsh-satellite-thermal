import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';

// Exercise the same fetch client used by create/save and their UI finally blocks.
globalThis.window={THERMAL_API_BASE:'http://127.0.0.1'};
const {request,api,ApiError,REQUEST_TIMEOUTS}=await import('../src/api.ts');
const nativeFetch=globalThis.fetch;

async function stalledService(t,sendHeaders=false){
  let calls=0;
  const server=http.createServer((req,res)=>{
    calls++;
    req.resume();
    if(sendHeaders){res.writeHead(200,{'content-type':'application/json'});res.write('{"id":');}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(async()=>{server.closeAllConnections();await new Promise(resolve=>server.close(resolve));});
  let signal;
  t.mock.method(globalThis,'fetch',(url,options)=>{
    signal=options.signal;
    return nativeFetch(`http://127.0.0.1:${server.address().port}${new URL(url).pathname}`,options);
  });
  return {calls:()=>calls,signal:()=>signal};
}

for(const sendHeaders of [false,true])test(`stalled create ${sendHeaders?'response body':'connection response'} settles and aborts without retry`,async t=>{
  const service=await stalledService(t,sendHeaders);
  const start=performance.now();
  let waiting=true;
  const operation=request('/projects',{method:'POST',body:JSON.stringify({name:'Timeout regression'})},200).finally(()=>{waiting=false;});
  await assert.rejects(operation,error=>{
    assert.ok(error instanceof ApiError);
    assert.equal(error.status,408);
    assert.match(error.message,/先刷新项目/);
    return true;
  });
  assert.equal(waiting,false,'the caller can leave its busy state');
  assert.equal(service.signal().aborted,true,'the stalled transport is cancelled');
  assert.equal(service.calls(),1,'a timed-out write is never automatically repeated');
  assert.ok(performance.now()-start<2000,'the entire request has a finite deadline');
});

test('CRUD and health time out early while geometry, example and processing retain their longer allowance',async t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  t.mock.method(globalThis,'fetch',()=>new Promise(()=>{}));
  const outcome={};
  const capture=(name,promise)=>promise.catch(error=>{outcome[name]=error;});
  const requests=[
    capture('health',api.health()),capture('create',api.create('project')),
    capture('example',api.example()),capture('geometry',api.upload('p','geometry',new File(['STEP'],'sample.step'))),
    capture('documents',api.upload('p','documents',new File(['power'],'input.txt'))),
    capture('text',api.text('p','8 W')),capture('heatflux',api.heatflux('p',{}))
  ];
  t.mock.timers.tick(REQUEST_TIMEOUTS.health);
  await Promise.resolve();await Promise.resolve();await Promise.resolve();
  assert.equal(outcome.health?.status,408);
  assert.equal(outcome.create,undefined);
  t.mock.timers.tick(REQUEST_TIMEOUTS.ordinary-REQUEST_TIMEOUTS.health);
  await Promise.resolve();await Promise.resolve();await Promise.resolve();
  assert.equal(outcome.create?.status,408);
  for(const name of ['example','geometry','documents','text','heatflux'])assert.equal(outcome[name],undefined,`${name} must remain available for longer processing`);
  t.mock.timers.tick(REQUEST_TIMEOUTS.processing-REQUEST_TIMEOUTS.ordinary);
  await Promise.all(requests);
  for(const value of Object.values(outcome))assert.equal(value.status,408);
});

test('completed creation preserves the response and clears its deadline',async t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  let signal;
  t.mock.method(globalThis,'fetch',async(_url,options)=>{signal=options.signal;return Response.json({id:'created',revision:1});});
  assert.deepEqual(await api.create('project'),{id:'created',revision:1});
  t.mock.timers.tick(REQUEST_TIMEOUTS.processing);
  assert.equal(signal.aborted,false,'a completed request has no residual abort timer');
});

test('service error details remain actionable and external cancellation is respected',async t=>{
  t.mock.method(globalThis,'fetch',async()=>Response.json({detail:'输入已被其他窗口更新'},{status:409}));
  await assert.rejects(request('/projects/p',{method:'PATCH'}),error=>error instanceof ApiError&&error.status===409&&error.message==='输入已被其他窗口更新');
  const controller=new AbortController();controller.abort();
  let called=false;
  t.mock.method(globalThis,'fetch',async()=>{called=true;return Response.json({});});
  await assert.rejects(request('/projects',{signal:controller.signal}),error=>error instanceof ApiError&&error.status===0);
  assert.equal(called,false);
});
