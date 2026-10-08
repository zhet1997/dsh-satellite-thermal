import test from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import {apply} from '../src/index.mjs'

test('connection check tests unsaved settings without persisting or exposing credentials',async()=>{
  const previousUrl=process.env.THERMAL_SERVICE_URL,previousToken=process.env.THERMAL_SERVICE_TOKEN
  delete process.env.THERMAL_SERVICE_URL;delete process.env.THERMAL_SERVICE_TOKEN
  const storedToken='stored-settings-test-secret',candidateToken='candidate-settings-test-secret'
  const seen=[],mutations=[],routes=new Map()
  const makeApi=label=>http.createServer((req,res)=>{
    seen.push({label,url:req.url,authorization:req.headers.authorization})
    const accepted=label==='stored'?req.headers.authorization===`Bearer ${storedToken}`:[undefined,`Bearer ${candidateToken}`,`Bearer ${storedToken}`].includes(req.headers.authorization)
    res.writeHead(accepted?200:401,{'content-type':'application/json'})
    res.end(JSON.stringify(accepted?{status:'ok',source:label}:{detail:'Unauthorized'}))
  })
  const stored=makeApi('stored'),candidate=makeApi('candidate')
  try{
    await Promise.all([stored,candidate].map(server=>new Promise(resolve=>server.listen(0,'127.0.0.1',resolve))))
    const storedUrl=`http://127.0.0.1:${stored.address().port}`,candidateUrl=`http://127.0.0.1:${candidate.address().port}`
    const ctx={
      effect:fn=>fn(),fiber:{},inject:()=>{},
      settings:{configure:()=>()=>{},update:async(...args)=>mutations.push(['settings',...args])},
      credentials:{resolve:async()=>({value:storedToken}),set:async(...args)=>mutations.push(['set',...args]),unset:async(...args)=>mutations.push(['unset',...args])},
      connection:{fetch:{register:route=>{routes.set(route.path,route.fetch);return()=>{}}}},
      webServer:{register:()=>()=>{}}
    }
    await apply(ctx,{serviceUrl:{get:()=>storedUrl},connectMcp:{get:()=>false}})
    const call=async(operation,payload={})=>{
      const response=await routes.get(`/api/satellite-thermal/${operation}`)(new Request(`http://dsh.test/api/satellite-thermal/${operation}`,{
        method:'POST',headers:{'content-type':'application/json'},
        body:JSON.stringify({type:'client-request',rpcId:'settings-regression',method:`satellite-thermal/${operation}`,payload})
      }))
      const body=await response.json()
      for(const secret of [storedToken,candidateToken,'invalid-test-secret'])assert.equal(JSON.stringify(body).includes(secret),false)
      return body.result
    }
    const checked=await call('check',{serviceUrl:candidateUrl+'/',token:` ${candidateToken} `})
    assert.equal(checked.ok,true);assert.equal(checked.value.source,'candidate')
    assert.deepEqual(seen,[{label:'candidate',url:'/api/health',authorization:`Bearer ${candidateToken}`}])
    const settings=await call('settings')
    assert.deepEqual(settings.value,{serviceUrl:storedUrl,tokenConfigured:true})
    assert.deepEqual(mutations,[])

    const rejected=await call('check',{serviceUrl:candidateUrl,token:'invalid-test-secret'})
    assert.equal(rejected.ok,false);assert.match(rejected.error.message,/401/)
    assert.equal(seen.at(-1).label,'candidate')

    const fallback=await call('check')
    assert.equal(fallback.ok,true);assert.equal(fallback.value.source,'stored')
    assert.equal(seen.at(-1).authorization,`Bearer ${storedToken}`)
    await call('check',{serviceUrl:candidateUrl,token:' '})
    assert.equal(seen.at(-1).authorization,`Bearer ${storedToken}`)
    await call('check',{serviceUrl:candidateUrl,clearToken:true})
    assert.equal(seen.at(-1).authorization,undefined)
    const count=seen.length
    const invalid=await call('check',{serviceUrl:candidateUrl+'/api'})
    assert.equal(invalid.ok,false);assert.equal(seen.length,count)
    assert.deepEqual(mutations,[])
    assert.deepEqual((await call('settings')).value,{serviceUrl:storedUrl,tokenConfigured:true})
  }finally{
    for(const server of [stored,candidate]){server.closeAllConnections();await new Promise(resolve=>server.close(resolve))}
    if(previousUrl===undefined)delete process.env.THERMAL_SERVICE_URL;else process.env.THERMAL_SERVICE_URL=previousUrl
    if(previousToken===undefined)delete process.env.THERMAL_SERVICE_TOKEN;else process.env.THERMAL_SERVICE_TOKEN=previousToken
  }
})
