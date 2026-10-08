import test from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import net from 'node:net'
import {setTimeout as delay} from 'node:timers/promises'
import {normalizeEndpoint,safeFile,proxyService} from '../src/http.mjs'
test('service configuration cannot smuggle credentials, queries, paths or protocols',()=>{
  assert.equal(normalizeEndpoint('https://thermal.example:9000/'),'https://thermal.example:9000')
  for(const u of ['file:///tmp','https://user:password@example.com','https://example.com/?key=x','http://a/api','http://a/#x'])assert.throws(()=>normalizeEndpoint(u))
  assert.equal(safeFile(process.cwd(),'../secret'),null)
})
test('authenticated proxy preserves multipart and PATCH while stripping browser credentials',async()=>{
  let seen
  const upstream=http.createServer(async(req,res)=>{
    const chunks=[];for await(const c of req)chunks.push(c)
    seen={url:req.url,method:req.method,headers:req.headers,body:Buffer.concat(chunks).toString()}
    res.writeHead(200,{'content-type':'application/json','set-cookie':'unsafe=1'});res.end('{"ok":true}')
  })
  await new Promise(r=>upstream.listen(0,'127.0.0.1',r))
  const proxy=http.createServer((req,res)=>proxyService(req,res,`http://127.0.0.1:${upstream.address().port}`,'service-secret','/api/satellite-thermal/service'))
  await new Promise(r=>proxy.listen(0,'127.0.0.1',r))
  try {
    const r=await fetch(`http://127.0.0.1:${proxy.address().port}/api/satellite-thermal/service/projects/p-test?x=1`,{method:'PATCH',headers:{authorization:'Bearer browser-secret',cookie:'dsh=cookie','content-type':'application/json'},body:'{"revision":2}'})
    assert.equal(r.status,200);assert.equal(r.headers.get('set-cookie'),null)
    assert.equal(seen.url,'/api/projects/p-test?x=1');assert.equal(seen.method,'PATCH');assert.equal(seen.body,'{"revision":2}')
    assert.equal(seen.headers.authorization,'Bearer service-secret');assert.equal(seen.headers.cookie,undefined)
  }finally{proxy.closeAllConnections();upstream.closeAllConnections();await Promise.all([new Promise(r=>proxy.close(r)),new Promise(r=>upstream.close(r))])}
})

async function listen(server) {
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve))
  return `http://127.0.0.1:${server.address().port}`
}
async function close(server) {
  server.closeAllConnections?.()
  await new Promise(resolve=>server.close(resolve))
}
async function startProxy(t,endpoint,timeouts) {
  const server=http.createServer((req,res)=>proxyService(req,res,endpoint,'','/thermal',timeouts))
  const base=await listen(server)
  t.after(()=>close(server))
  return base
}

test('DNS resolution stall returns a bounded actionable 504 and destroys upstream',async t=>{
  const request=http.request
  let pending, resolving=false
  t.mock.method(http,'request',(target,options,callback)=>{
    pending=request(target,{...options,lookup:()=>{resolving=true}},callback)
    return pending
  })
  const base=await startProxy(t,'http://thermal-unreachable.invalid',{connectTimeoutMs:80,responseTimeoutMs:500})
  const response=await fetch(`${base}/thermal/projects`,{method:'POST',headers:{'content-type':'application/json'},body:'{"name":"new"}'})
  assert.equal(resolving,true)
  assert.equal(response.status,504)
  assert.equal((await response.json()).code,'thermal_connect_timeout')
  assert.equal(pending.destroyed,true)
})

test('TLS handshake stall is also bounded by the connection deadline',async t=>{
  const sockets=new Set()
  const upstream=net.createServer(socket=>{sockets.add(socket);socket.on('close',()=>sockets.delete(socket))})
  const endpoint=(await listen(upstream)).replace('http:','https:')
  t.after(async()=>{for(const socket of sockets)socket.destroy();await close(upstream)})
  const base=await startProxy(t,endpoint,{connectTimeoutMs:80,responseTimeoutMs:500})
  const response=await fetch(`${base}/thermal/projects`)
  assert.equal(response.status,504)
  assert.equal((await response.json()).code,'thermal_connect_timeout')
})

test('connected service that never answers returns 504 and closes its request',async t=>{
  const disconnected=Promise.withResolvers()
  const upstream=http.createServer((req,res)=>{req.resume();res.on('close',disconnected.resolve)})
  const endpoint=await listen(upstream)
  t.after(()=>close(upstream))
  const base=await startProxy(t,endpoint,{connectTimeoutMs:500,responseTimeoutMs:80})
  const response=await fetch(`${base}/thermal/projects`,{method:'POST',headers:{'content-type':'application/json'},body:'{"name":"new"}'})
  assert.equal(response.status,504)
  const error=await response.json()
  assert.equal(error.code,'thermal_response_timeout')
  assert.match(error.detail,/刷新项目列表/)
  await Promise.race([disconnected.promise,delay(1000).then(()=>assert.fail('upstream request remained open'))])
})

test('response deadline excludes active upload and streaming download time',async t=>{
  let received=''
  const upstream=http.createServer(async(req,res)=>{
    for await(const chunk of req)received+=chunk.toString()
    res.writeHead(200,{'content-type':'text/plain'})
    res.write('one')
    await delay(120)
    res.end('two')
  })
  const endpoint=await listen(upstream)
  t.after(()=>close(upstream))
  const base=await startProxy(t,endpoint,{connectTimeoutMs:500,responseTimeoutMs:70,transferTimeoutMs:500})
  const result=Promise.withResolvers()
  const request=http.request(`${base}/thermal/projects/p-one/geometry`,{method:'POST'},response=>{
    let body=''
    response.on('data',chunk=>body+=chunk)
    response.on('end',()=>result.resolve({status:response.statusCode,body}))
    response.on('error',result.reject)
  })
  request.on('error',result.reject)
  request.write('a')
  await delay(120)
  request.end('b')
  assert.deepEqual(await result.promise,{status:200,body:'onetwo'})
  assert.equal(received,'ab')
})

test('cancelled browser request closes pending upstream without waiting for timeout',async t=>{
  const received=Promise.withResolvers(),disconnected=Promise.withResolvers()
  const upstream=http.createServer((req,res)=>{received.resolve();req.resume();res.on('close',disconnected.resolve)})
  const endpoint=await listen(upstream)
  t.after(()=>close(upstream))
  const base=await startProxy(t,endpoint,{connectTimeoutMs:500,responseTimeoutMs:30000})
  const controller=new AbortController()
  const response=fetch(`${base}/thermal/projects`,{signal:controller.signal}).catch(error=>error)
  await received.promise
  controller.abort()
  assert.equal((await response).name,'AbortError')
  await Promise.race([disconnected.promise,delay(1000).then(()=>assert.fail('upstream request remained open'))])
})
