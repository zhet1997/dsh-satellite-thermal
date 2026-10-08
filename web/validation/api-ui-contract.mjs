// Integration checks for the actual endpoints used by the UI. This creates one
// explicitly named verification project; it does not simulate NX execution.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
const base=(process.env.THERMAL_TEST_URL||'http://127.0.0.1:8790/api').replace(/\/$/,'');
const checks=[];
async function request(path,body,method=body===undefined?'GET':'POST'){
  const res=await fetch(`${base}${path}`,{method,headers:{'Content-Type':'application/json'},...(body!==undefined?{body:JSON.stringify(body)}:{})});
  const data=await res.json();if(!res.ok)throw new Error(`${method} ${path}: ${res.status} ${JSON.stringify(data)}`);return data;
}
function pass(name){checks.push(name);process.stdout.write(`PASS ${name}\n`);}
const health=await request('/health');assert.equal(health.status,'ok');pass('健康状态与求解器连接状态可读');
let p=await request('/projects/example',{});const id=p.id,path=`/projects/${id}`;
p=await request(path,{revision:p.revision,name:'前端接口核验 · 非 NX 验收'},'PATCH');
assert.ok(p.components.length>=2);assert.equal(p.geometry.status,'ready');assert.equal(p.geometry.source,'example');assert.ok(p.geometry.previewUrl);pass('体验示例明确标识，包含真实预览和组件数据');
const url=new URL(p.geometry.previewUrl,new URL(base).origin).href;
const glb=Buffer.from(await(await fetch(url)).arrayBuffer());assert.equal(glb.readUInt32LE(0),0x46546c67);const chunkLength=glb.readUInt32LE(12);assert.equal(glb.readUInt32LE(16),0x4e4f534a);const gltf=JSON.parse(glb.subarray(20,20+chunkLength).toString('utf8'));
const extras=[...(gltf.nodes||[]),...(gltf.meshes||[])].map(item=>item.extras||{}).filter(e=>e.componentId&&e.faceId);
assert.ok(extras.length>0);for(const c of p.components){for(const f of c.faces)assert.ok(extras.some(e=>e.componentId===c.id&&e.faceId===f.id),`Missing extras ${c.id}/${f.id}`);}
pass(`GLB extras 与全部 ${p.components.reduce((s,c)=>s+c.faces.length,0)} 个实际组件/面身份一致`);
const first=p.components[0],oldPower=first.power,initialRunCount=p.runs.length,initialRevision=p.revision;
const edited=p.components.map(c=>c.id===first.id?{...c,kind:'shell',thickness:2.4,power:oldPower+1,temperatureLimitMin:263.15,temperatureLimitMax:323.15,faces:c.faces.map((f,i)=>i===0?{...f,emissivity:.72,absorptivity:.25}:f)}:c);
p=await request(path,{revision:p.revision,components:edited},'PATCH');
const c=p.components.find(c=>c.id===first.id);assert.equal(c.kind,'shell');assert.equal(c.thickness,2.4);assert.equal(c.power,oldPower+1);assert.equal(c.faces[0].emissivity,.72);assert.equal(c.faces[0].absorptivity,.25);assert.ok(c.shell.sides.includes('top')&&c.shell.sides.includes('bottom'));assert.equal(p.revision,initialRevision+1);assert.equal(p.runs.length,initialRunCount+1);assert.deepEqual(c.faces.map(f=>f.id),first.faces.map(f=>f.id));
pass('壳厚、上下表面约定、面级光学参数与功耗一次保存，只创建一个新版本/运行');
const sameRevision=p.revision,sameRunCount=p.runs.length;
p=await request(path,{revision:p.revision,components:p.components},'PATCH');assert.equal(p.revision,sameRevision);assert.equal(p.runs.length,sameRunCount);pass('无变化保存不创建多余运行');
const frozenRun=await request(`${path}/runs/${p.runs[0].id}`);assert.ok(frozenRun.inputSnapshot);assert.ok(frozenRun.inputSnapshot.components.some(c=>c.id===first.id));pass('运行接口返回固定 inputSnapshot，UI 无须借用当前输入');
p=await request(path,{revision:p.revision,components:p.components.map(c=>c.id===first.id?{...c,temperatureLimitMin:null,temperatureLimitMax:null}:c)},'PATCH');assert.equal(p.components[0].temperatureLimitMin,undefined);assert.equal(p.components[0].temperatureLimitMax,undefined);pass('显式 null 可清空温限，不残留旧阈值');
const existing=p.links[0];if(existing){const before=p.runs.length;const nextLink={...existing,value:existing.value*1.1,provenance:{status:'manual',source:'网页编辑'}};p=await request(path,{revision:p.revision,links:p.links.map(l=>l.id===existing.id?nextLink:l)},'PATCH');const changed=p.links.find(l=>l.id===existing.id);assert.equal(changed.contactAreaMm2,existing.contactAreaMm2);assert.equal(changed.sourceFace,existing.sourceFace);assert.equal(changed.targetFace,existing.targetFace);assert.equal(p.runs.length,before+1);pass('修改接触参数保留两侧面及实际接触面积证据，只新增一个运行');}
const beforeOrbit=p.runs.length;p=await request(`${path}/heatflux`,{revision:p.revision,orbit:p.orbit,season:p.orbit.season});assert.ok(p.heatfluxProfile.length>5);assert.equal(p.runs.length,beforeOrbit+p.cases.length);
const axes=['+X','-X','+Y','-Y','+Z','-Z'];for(const kind of ['cold','hot']){const c=p.cases.find(c=>c.kind===kind),sample=p.heatfluxProfile.find(s=>s.time===c.time);assert.ok(sample);for(const axis of axes)assert.equal(c.flux[axis],sample.faces[axis]);assert.equal(c.recommendation.allDirectionsUseSameTime,true);assert.equal(c.opticalCoefficientsApplied,false);}
pass('冷/热工况六向值严格取同一推荐 timestamp，未提前乘光学系数');
const manualTime=Math.min(157,p.heatfluxProfile.at(-1).time);p=await request(`${path}/heatflux`,{revision:p.revision,orbit:p.orbit,season:p.orbit.season,time:manualTime});const manual=p.cases.find(c=>c.kind==='manual');assert.equal(manual.time,manualTime);const sample=p.heatfluxProfile.find(s=>s.time===manualTime);for(const axis of axes)assert.equal(manual.flux[axis],sample.faces[axis]);pass('指定时刻以统一插值时间生成六向工况');
const conflict=await fetch(`${base}${path}/heatflux`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:0,orbit:p.orbit})});assert.equal(conflict.status,409);pass('轨道计算使用 revision，过期输入返回 409');
const standard=await request(`${path}/standard`);assert.equal(standard.checks.parameterCompleteness,'complete');assert.ok(standard.expectedApplication.components.every(c=>c.faceIds));pass('标准输入包含完整性检查、来源及预期施加对象');
const oldAgain=await request(`${path}/runs/${frozenRun.id}`);assert.deepEqual(oldAgain.inputSnapshot,frozenRun.inputSnapshot);pass('后续面参数、热连接和工况编辑不会改写历史运行快照');
p=await request(path);if(!health.solver.connected){assert.ok(p.runs.every(r=>!r.result&&r.status!=='completed'));pass('NX 未连接时未产生伪造结果或完成状态');}
const exportRes=await fetch(`${base}${path}/export`);assert.equal(exportRes.status,200);assert.match(exportRes.headers.get('content-type'),/zip/);pass('项目数据包下载路由返回 ZIP');
// Leave the verification project and immutable snapshots reviewable, but remove
// its queued work so connecting a real NX worker later does not run test inputs.
for(const run of p.runs.filter(r=>!['completed','failed','cancelled'].includes(r.status)))await request(`${path}/runs/${run.id}/cancel`,{});
p=await request(path);assert.ok(p.runs.every(r=>['cancelled','cancel_requested','failed','completed'].includes(r.status)));pass('核验创建的等待任务均已取消，保留证据和输入快照');
const report={verifiedAt:new Date().toISOString(),serviceVersion:health.version,projectId:id,projectName:p.name,checks,browserVisualReview:'未执行：浏览器工具被站点访问规则拦截；没有采用其他浏览器或截图路径。',nxExecution:'未执行：NX 执行端未连接；没有模拟求解结果。'};
await fs.writeFile(new URL('./api-ui-contract-result.json',import.meta.url),JSON.stringify(report,null,2));
process.stdout.write(`Verified ${checks.length} checks; project ${id}.\n`);
