// HTTP contract verification only; does not automate or render a browser.
import fs from 'node:fs/promises'
import path from 'node:path'
import assert from 'node:assert/strict'
import {fileURLToPath} from 'node:url'
import {createHash} from 'node:crypto'
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..')
const log=await fs.readFile(path.join(root,'runtime/dsh.stdout.log'),'utf8')
const launch=log.match(/http:\/\/127\.0\.0\.1:3083\/\?token=[^\s]+/)?.[0]
assert.ok(launch,'Validation host launch URL available')
const base='http://127.0.0.1:3083',checks=[]
const denied=await fetch(base+'/api/satellite-thermal/service/health')
assert.equal(denied.status,401);checks.push('anonymous API rejected')
const login=await fetch(launch,{redirect:'manual'})
assert.ok([302,303].includes(login.status))
const cookie=login.headers.getSetCookie().map(s=>s.split(';')[0]).join('; ')
assert.ok(cookie)
const headers={cookie,origin:base}
const health=await fetch(base+'/api/satellite-thermal/service/health',{headers})
assert.equal(health.status,200);assert.equal((await health.json()).status,'ok');checks.push('authenticated proxy reaches service')
const html=await fetch(base+'/satellite-thermal/',{headers})
assert.equal(html.status,200)
const text=await html.text();assert.match(text,/THERMAL_API_BASE="\/api\/satellite-thermal\/service"/)
const resources=[...text.matchAll(/(?:src|href)="\.\/([^\"]+\.(?:js|css))"/g)].map(m=>m[1])
assert.ok(resources.length>=2)
for(const resource of resources){const r=await fetch(base+'/satellite-thermal/'+resource,{headers});assert.equal(r.status,200);assert.ok(Number(r.headers.get('content-length'))>0||Number((await r.arrayBuffer()).byteLength)>0)}
checks.push('packed HTML and all declared JS/CSS resources served')
const settings=await fetch(base+'/api/satellite-thermal/settings',{method:'POST',headers:{...headers,'content-type':'application/json'},body:JSON.stringify({type:'client-request',rpcId:'thermal-install-check',method:'satellite-thermal/settings',payload:{}})})
assert.equal(settings.status,200);const body=await settings.json();assert.equal(body.result.ok,true);assert.equal(body.result.value.serviceUrl,'http://127.0.0.1:8790');assert.equal('token' in body.result.value,false);checks.push('native plugin settings respond without secrets')
const blocked=await fetch(base+'/api/satellite-thermal/service/health',{headers:{cookie,origin:'https://foreign.invalid'}})
assert.equal(blocked.status,403);checks.push('cross-origin API rejected')
const profile=path.join(root,'validation/dsh-home/profiles/thermal-packaged')
const manifest=JSON.parse(await fs.readFile(path.join(profile,'package.json'),'utf8'))
assert.ok(manifest.dsh.profile.bundles.includes('@lbk/dsh-satellite-geometry'))
assert.ok(manifest.dsh.profile.bundles.includes('@lbk/dsh-satellite-thermal'))
for(const file of ['src/index.mjs','lib/client.js','mcp/server.mjs','public/index.html']){
  assert.deepEqual(await fs.readFile(path.join(profile,'node_modules/@lbk/dsh-satellite-thermal',file)),await fs.readFile(path.join(root,'plugin',file)),`Installed package matches current build: ${file}`)
}
checks.push('current packed build installed alongside existing geometry plugin')
const packageSha256=createHash('sha256').update(await fs.readFile(path.join(root,'dist/lbk-dsh-satellite-thermal-0.1.0.tgz'))).digest('hex')
const report={time:new Date().toISOString(),hostVersion:'0.2.0-rc.2',package:'@lbk/dsh-satellite-thermal@0.1.0',packageSha256,coexistingBundles:manifest.dsh.profile.bundles,checks,browserVisualVerification:false}
await fs.writeFile(path.join(root,'validation/dsh-install-result.json'),JSON.stringify(report,null,2)+'\n')
console.log(JSON.stringify(report,null,2))
