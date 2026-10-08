import React, {useEffect,useRef,useState} from 'react'
const button={padding:'7px 12px',border:'1px solid #ccd8dd',borderRadius:8,background:'#edf6f4',color:'#155d58',cursor:'pointer',fontSize:12}
function ThermalDock({openWorkbench}) {
  return <div style={{padding:'5px 10px'}}><button style={button} onClick={openWorkbench}>◈ 热建模工作台</button></div>
}
function ThermalPanel({sessionId,useInput,inputActions}) {
  const iframe=useRef(null)
  const input=useInput(s=>s)
  const live=useRef({input,inputActions});live.current={input,inputActions}
  const [notice,setNotice]=useState('')
  useEffect(()=>{
    const receive=event=>{
      if(event.origin!==location.origin||event.source!==iframe.current?.contentWindow)return
      const d=event.data
      if(d?.type!=='satellite-thermal:chat'||typeof d.projectId!=='string'||!Number.isInteger(d.revision)||typeof d.prompt!=='string'||d.prompt.length>20000)return
      const selection={}
      for(const k of ['componentId','faceId','linkId','boundaryId','caseId','environment'])if(typeof d.selection?.[k]==='string')selection[k]=d.selection[k].slice(0,200)
      const draft=live.current.input.draft||''
      live.current.inputActions.setDraft(`${draft}${draft?'\n\n':''}热建模项目 ${d.projectId}，修订 ${d.revision}，选择 ${JSON.stringify(selection)}。请先读取 satellite_thermal 的项目与标准参数，修改前检查修订号。\n${d.prompt}`)
      setNotice('已带入左侧对话草稿，请检查后发送。')
    }
    window.addEventListener('message',receive)
    return()=>window.removeEventListener('message',receive)
  },[sessionId])
  return <div style={{height:'100%',minHeight:0,display:'flex',flexDirection:'column'}}>
    {notice&&<button style={button} onClick={()=>setNotice('')} role="status">{notice} ×</button>}
    <iframe key={sessionId} ref={iframe} title="卫星热建模" src={`/satellite-thermal/?sessionId=${encodeURIComponent(sessionId)}`} style={{border:0,width:'100%',flex:1,minHeight:0}} />
  </div>
}
function SettingsCard({rpc}) {
  const [url,setUrl]=useState('http://127.0.0.1:8790'),[token,setToken]=useState(''),[configured,setConfigured]=useState(false),[status,setStatus]=useState('')
  const call=(op,p={})=>rpc.call('/api',`satellite-thermal/${op}`,p)
  useEffect(()=>{call('settings').then(r=>{if(r.ok){setUrl(r.value.serviceUrl);setConfigured(r.value.tokenConfigured)}}).catch(e=>setStatus(e.message))},[rpc])
  const action=async op=>{setStatus('连接中…');try{const r=await call(op,{serviceUrl:url,...(token?{token}:{})});if(!r.ok)throw Error(r.error.message);if(op==='configure'){setConfigured(r.value.tokenConfigured);setToken('');setStatus('连接设置已保存。')}else setStatus(`热服务已连接。${r.value.solver?.message||''}`)}catch(e){setStatus(e.message)}}
  return <div style={{padding:24,maxWidth:700}}><h3>卫星热建模</h3><p>连接独立热建模服务。NX / Simcenter 运行在独立工程工作站上。</p><label>热服务地址<input value={url} onChange={e=>setUrl(e.target.value)} style={{display:'block',width:'100%',padding:9,margin:'8px 0 16px'}}/></label><label>服务访问令牌（{configured?'已配置，留空保留':'本机服务可留空'}）<input type="password" autoComplete="off" value={token} onChange={e=>setToken(e.target.value)} style={{display:'block',width:'100%',padding:9,margin:'8px 0 16px'}}/></label><div style={{display:'flex',gap:8}}><button style={button} onClick={()=>action('configure')}>保存连接</button><button style={button} onClick={()=>action('check')}>检查连接</button><a href="/satellite-thermal/" target="_blank" rel="noreferrer" style={button}>打开工作台</a></div><p role="status">{status}</p></div>
}
export const inject=['slots','connection','sidebarRight','sidebarRightTabs']
export function apply(ctx) {
  ctx.effect(()=>ctx.sidebarRightTabs.register({id:'satellite-thermal',kind:'satellite-thermal',title:()=> '热建模',keepMounted:true,guide:[{id:'thermal-workbench',order:21,title:()=> '热建模工作台'}]}))
  ctx.slots.inject('sidebar.right.pane.tab',()=>ctx.slots.register({name:'sidebar.right.pane.tab',key:'satellite-thermal'},ThermalPanel))
  ctx.slots.inject('conversation.input.dock',()=>ctx.slots.register({name:'conversation.input.dock',id:'satellite-thermal',order:-9,inject:()=>({openWorkbench:()=>ctx.sidebarRight.openTab('satellite-thermal')})},ThermalDock))
  ctx.slots.inject('settings.plugins.tab',()=>ctx.slots.register({name:'settings.plugins.tab',id:'satellite-thermal',label:()=> '卫星热建模',order:21,inject:()=>({rpc:ctx.connection.rpc})},SettingsCard))
}
