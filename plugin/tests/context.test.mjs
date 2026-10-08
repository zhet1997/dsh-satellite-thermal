import test from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import {thermalContext} from '../src/context.mjs'

test('session context is bounded, isolated, and evidence rather than system instructions',async()=>{
  const server=http.createServer((req,res)=>{
    const sessionId=decodeURIComponent(req.url.split('/')[3])
    res.setHeader('content-type','application/json')
    res.end(JSON.stringify(sessionId==='a'?{bound:true,sessionId,projectId:'p-a',excerpt:'x'.repeat(30000)}:{bound:false,sessionId}))
  })
  await new Promise(r=>server.listen(0,'127.0.0.1',r))
  try{
    const endpoint=`http://127.0.0.1:${server.address().port}`
    const a=await thermalContext(endpoint,'','a')
    assert.match(a.text,/p-a/)
    assert.ok(a.text.length<18200)
    const b=await thermalContext(endpoint,'','b')
    assert.match(b.text,/尚未绑定/)
    assert.doesNotMatch(b.text,/p-a/)
  }finally{server.closeAllConnections();await new Promise(r=>server.close(r))}
})
