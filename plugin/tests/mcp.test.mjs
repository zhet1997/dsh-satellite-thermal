import test from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import {fileURLToPath} from 'node:url'
import {Client} from '@modelcontextprotocol/sdk/client/index.js'
import {StdioClientTransport} from '@modelcontextprotocol/sdk/client/stdio.js'
test('MCP discovers tools, forwards a frozen revision and reports service errors honestly',async()=>{
  let seen,requestCount=0
  const api=http.createServer(async(req,res)=>{
    const chunks=[];for await(const c of req)chunks.push(c)
    seen={url:req.url,method:req.method,authorization:req.headers.authorization,body:Buffer.concat(chunks).toString()};requestCount++
    if(req.url==='/api/health'){res.writeHead(503,{'content-type':'application/json'});res.end('{"detail":"NX disconnected"}')}
    else if(req.url==='/api/projects/p-test/heatflux'&&JSON.parse(seen.body).revision!==8){res.writeHead(409,{'content-type':'application/json'});res.end('{"detail":"Project revision conflict"}')}
    else {res.writeHead(200,{'content-type':'application/json'});res.end('{"revision":8}')}
  })
  await new Promise(r=>api.listen(0,'127.0.0.1',r))
  const client=new Client({name:'thermal-validation',version:'1'})
  const transport=new StdioClientTransport({command:process.execPath,args:[fileURLToPath(new URL('../mcp/server.mjs',import.meta.url))],env:{THERMAL_SERVICE_URL:`http://127.0.0.1:${api.address().port}`,THERMAL_SERVICE_TOKEN:'mcp-test-secret'}})
  try{
    await client.connect(transport)
    const {tools}=await client.listTools()
    assert.equal(tools.length,16)
    assert.equal(tools.find(t=>t.name==='thermal_heatflux').inputSchema.required.includes('revision'),true)
    const updated=await client.callTool({name:'thermal_update',arguments:{projectId:'p-test',revision:7,patch:{components:[]}}})
    assert.equal(updated.isError,undefined);assert.equal(seen.method,'PATCH');assert.equal(JSON.parse(seen.body).revision,7);assert.equal(seen.authorization,'Bearer mcp-test-secret')
    const health=await client.callTool({name:'thermal_health',arguments:{}})
    assert.equal(health.isError,true);assert.match(health.content[0].text,/NX disconnected/)
    const orbit={altitudeKm:550,inclinationDeg:97.6}
    const heatflux=await client.callTool({name:'thermal_heatflux',arguments:{projectId:'p-test',revision:8,orbit,time:120,season:'summer'}})
    assert.equal(heatflux.isError,undefined);assert.equal(seen.method,'POST');assert.equal(seen.url,'/api/projects/p-test/heatflux')
    assert.deepEqual(JSON.parse(seen.body),{revision:8,orbit,time:120,season:'summer'})
    const stale=await client.callTool({name:'thermal_heatflux',arguments:{projectId:'p-test',revision:7,orbit}})
    assert.equal(stale.isError,true);assert.match(stale.content[0].text,/revision conflict/)
    await client.callTool({name:'thermal_session_context',arguments:{sessionId:'session-a'}})
    assert.equal(seen.url,'/api/sessions/session-a/context')
    await client.callTool({name:'thermal_document_snippets',arguments:{projectId:'p-test',documentId:'doc-a',query:'温度'}})
    assert.equal(seen.url,'/api/projects/p-test/documents/doc-a/snippets?query='+encodeURIComponent('温度')+'&offset=0')
    const before=requestCount
    for(const revision of [undefined,-1,1.5]){
      const invalid=await client.callTool({name:'thermal_heatflux',arguments:{projectId:'p-test',...(revision===undefined?{}:{revision}),orbit}})
      assert.equal(invalid.isError,true);assert.match(invalid.content[0].text,/revision/)
    }
    assert.equal(requestCount,before,'invalid revisions must not reach the service')
  }finally{await client.close();await transport.close();api.closeAllConnections();await new Promise(r=>api.close(r))}
})
