import path from 'node:path'
import {fileURLToPath} from 'node:url'
import z from '@deepseek-ai/schemastery'
import * as McpClient from '@deepseek-ai/dsh-mcp-client'
import * as SkillFilesystem from '@deepseek-ai/dsh-skill-filesystem'
import {normalizeEndpoint,json,proxyService,safeFile,staticFile} from './http.mjs'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..')
const ns = 'satellite-thermal'
export const name = ns
export const inject = ['webServer','connection','settings','credentials']
export const Config = z.object({serviceUrl:z.string().default('http://127.0.0.1:8790').volatile(),connectMcp:z.boolean().default(true).volatile()})
export async function apply(ctx,config) {
  ctx.effect(()=>ctx.settings.configure({auto:false},ctx.fiber))
  let endpoint = normalizeEndpoint(process.env.THERMAL_SERVICE_URL || config.serviceUrl.get())
  let mcp
  const token = async()=>(await ctx.credentials.resolve('THERMAL_SERVICE_TOKEN'))?.value || process.env.THERMAL_SERVICE_TOKEN || ''
  const connect = async()=>{
    if(mcp) {await mcp.dispose();mcp=undefined}
    if(config.connectMcp.get()) {
      mcp=ctx.plugin(McpClient,{serverName:'satellite_thermal',transport:'stdio',command:process.execPath,args:[path.join(root,'mcp/server.mjs')],env:{THERMAL_SERVICE_URL:endpoint,THERMAL_SERVICE_TOKEN:await token()},cwd:root,toolCallTimeoutMs:120000,failOnStartupError:true})
      await mcp.await()
    }
  }
  await connect()
  ctx.inject(['skills'],skillCtx=>skillCtx.plugin(SkillFilesystem,{providerName:ns,includeDefaultRoots:false,customSkillDirs:[path.join(root,'skills')],watch:false}))
  for(const operation of ['settings','configure','check']) {
    ctx.effect(()=>ctx.connection.fetch.register({path:`/api/${ns}/${operation}`,methods:['POST'],requestBody:'buffered',fetch:async request=>{
      let msg
      try {msg=await request.json()} catch{return new Response('Invalid JSON',{status:400})}
      if(msg?.type!=='client-request'||typeof msg.rpcId!=='string'||msg.method!==`${ns}/${operation}`)return new Response('Invalid envelope',{status:400})
      let result
      try {
        const p=msg.payload||{}
        if(operation==='configure') {
          const next=normalizeEndpoint(p.serviceUrl)
          if(p.clearToken===true)await ctx.credentials.unset('THERMAL_SERVICE_TOKEN')
          else if(typeof p.token==='string'&&p.token.trim())await ctx.credentials.set('THERMAL_SERVICE_TOKEN',p.token.trim())
          await ctx.settings.update(ns,{serviceUrl:next});endpoint=next;await connect()
        }
        let value={serviceUrl:endpoint,tokenConfigured:Boolean(await token())}
        if(operation==='check') {
          const candidateEndpoint=Object.hasOwn(p,'serviceUrl')?normalizeEndpoint(p.serviceUrl):endpoint
          const bearer=p.clearToken===true?'':typeof p.token==='string'&&p.token.trim()?p.token.trim():await token()
          const r=await fetch(`${candidateEndpoint}/api/health`,{headers:bearer?{authorization:`Bearer ${bearer}`}:{},signal:AbortSignal.any([request.signal,AbortSignal.timeout(7000)])})
          if(!r.ok)throw new Error(`热服务返回 ${r.status}`)
          value=await r.json()
        }
        result={ok:true,value}
      }catch(e){result={ok:false,error:{code:'thermal_connection',message:e.message}}}
      return Response.json({type:'server-response',rpcId:msg.rpcId,result})
    }}))
  }
  const prefix='/api/satellite-thermal/service'
  ctx.effect(()=>ctx.webServer.register({kind:'prefix',path:prefix,handler:async(req,res)=>{
    const rejection=ctx.connection.requestRejection(req)
    if(rejection)return json(res,rejection,{detail:'请先登录 DSH 后打开热建模工作台。'})
    return proxyService(req,res,endpoint,await token(),prefix)
  }}))
  ctx.effect(()=>ctx.webServer.register({kind:'prefix',path:'/satellite-thermal',handler:(req,res)=>{
    if(!ctx.connection.authorizeIndex(req,res))return
    if(!['GET','HEAD'].includes(req.method))return json(res,405,{detail:'Method not allowed'})
    let rel
    try {rel=decodeURIComponent(new URL(req.url,'http://localhost').pathname).slice('/satellite-thermal/'.length)}catch{return json(res,400,{detail:'Invalid path'})}
    return staticFile(req,res,safeFile(path.join(root,'public'),rel||'index.html'))
  }}))
}
