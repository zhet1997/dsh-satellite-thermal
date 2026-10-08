import fs from 'node:fs'
import path from 'node:path'
import http from 'node:http'
import https from 'node:https'

export function normalizeEndpoint(value) {
  const u = new URL(value)
  if (!['http:', 'https:'].includes(u.protocol) || u.username || u.password || u.search || u.hash || (u.pathname !== '/' && u.pathname !== '')) throw new Error('请填写服务根地址，例如 http://127.0.0.1:8790，不含路径或密码')
  return u.origin
}
export function json(res, code, value) {
  res.writeHead(code, {'content-type':'application/json; charset=utf-8','cache-control':'no-store'})
  res.end(JSON.stringify(value))
}
export function safeFile(base, relative) {
  const file = path.resolve(base, relative)
  return file.startsWith(path.resolve(base) + path.sep) ? file : null
}
export function staticFile(req, res, file) {
  if (!file || !fs.existsSync(file) || !fs.statSync(file).isFile()) return json(res,404,{detail:'页面文件不存在，请重新安装完整插件包。'})
  const mime = {'.html':'text/html; charset=utf-8','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.png':'image/png','.woff2':'font/woff2','.glb':'model/gltf-binary'}[path.extname(file)] || 'application/octet-stream'
  res.writeHead(200,{'content-type':mime,'cache-control':'no-cache','x-content-type-options':'nosniff','content-security-policy':"frame-ancestors 'self'"})
  if(req.method === 'HEAD') res.end(); else fs.createReadStream(file).pipe(res)
}
export function proxyService(req, res, endpoint, token, prefix, timeouts = {}) {
  const incoming = new URL(req.url,'http://localhost')
  const target = new URL('/api' + incoming.pathname.slice(prefix.length) + incoming.search,endpoint)
  const headers = {...req.headers,host:target.host}
  for(const key of ['cookie','authorization','origin','referer','connection','proxy-authorization','transfer-encoding']) delete headers[key]
  if(token) headers.authorization = `Bearer ${token}`
  const slowOperation = /\/(?:geometry|documents|text|heatflux|example|export)$/.test(target.pathname)
  const connectTimeoutMs = timeouts.connectTimeoutMs ?? 5000
  const responseTimeoutMs = timeouts.responseTimeoutMs ?? (slowOperation ? 300000 : 15000)
  const transferTimeoutMs = timeouts.transferTimeoutMs ?? 180000
  let connectTimer, responseTimer, upstream, finished = false, responded = false
  const clearDeadlines = () => { clearTimeout(connectTimer); clearTimeout(responseTimer) }
  const fail = (status, code, detail) => {
    if(finished) return
    finished = true
    clearDeadlines()
    req.unpipe(proxy)
    req.resume()
    proxy.destroy()
    upstream?.destroy()
    if(res.destroyed) return
    if(!res.headersSent) json(res,status,{code,detail})
    else res.destroy()
  }
  const proxy = (target.protocol === 'https:' ? https : http).request(target,{method:req.method,headers},response=>{
    if(finished) { response.destroy(); return }
    upstream = response
    responded = true
    clearDeadlines()
    proxy.setTimeout(transferTimeoutMs)
    const h = {...response.headers,'cache-control':'no-store'}
    delete h['set-cookie']; delete h['connection']
    res.writeHead(response.statusCode,h); response.pipe(res)
    response.on('error',()=>fail(502,'thermal_response_interrupted','热建模服务中断了响应，请检查服务状态后重试。'))
    response.on('end',()=>{finished = true; clearDeadlines()})
  })
  // A socket inactivity timeout alone never bounds DNS, TCP or TLS setup.
  connectTimer = setTimeout(()=>fail(504,'thermal_connect_timeout','连接热建模服务超时。请检查轻量热服务是否启动，以及 DSH 设置 → 内置插件 → 卫星热建模中的服务地址。'),connectTimeoutMs)
  proxy.on('socket',socket=>{
    const connected = () => clearTimeout(connectTimer)
    if(proxy.reusedSocket || (!socket.connecting && !socket.encrypted)) connected()
    else socket.once(socket.encrypted ? 'secureConnect' : 'connect',connected)
  })
  proxy.setTimeout(transferTimeoutMs,()=>fail(504,'thermal_transfer_timeout','热建模服务的数据传输长时间没有进展，请检查服务状态后重试。'))
  proxy.on('finish',()=>{
    if(finished || responded) return
    // Start after upload completes; long, active transfers remain streaming.
    // NX solves run in the service queue and never hold this HTTP request open.
    proxy.setTimeout(Math.max(transferTimeoutMs,responseTimeoutMs))
    responseTimer = setTimeout(()=>fail(504,'thermal_response_timeout','热建模服务已连接，但未及时响应。服务可能正在忙碌或已卡住，请检查后台日志。操作可能已经保存，请刷新项目列表确认后再重试。'),responseTimeoutMs)
  })
  proxy.on('error',()=>fail(502,'thermal_service_unavailable','无法连接热建模服务。请确认轻量热服务已启动，并在 DSH 设置 → 内置插件 → 卫星热建模中检查服务地址。'))
  const disconnect = () => {
    finished = true
    clearDeadlines()
    req.unpipe(proxy)
    proxy.destroy()
    upstream?.destroy()
  }
  req.on('aborted',disconnect)
  res.on('close',()=>{if(!res.writableEnded) disconnect()})
  req.pipe(proxy)
}
