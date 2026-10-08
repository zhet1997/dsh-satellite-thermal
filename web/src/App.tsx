import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowDownToLine, FileInput, GitBranch, Layers3, MessageSquare, Plus, RefreshCw, SlidersHorizontal, X } from 'lucide-react';
import { api, ApiError } from './api';
import type { Health, Project, ProjectPatch, Selection } from './types';
import { activeRun } from './types';
import { Badge, Button, Field, Modal } from './components/Common';
import { Inputs } from './components/Inputs';
import { Workbench } from './components/Workbench';
import { Parameters } from './components/Parameters';
import { Runs } from './components/Runs';

type Tab='inputs'|'model'|'parameters'|'runs';
const tabs=[{id:'inputs' as Tab,name:'资料与几何',icon:FileInput},{id:'model' as Tab,name:'热模型',icon:GitBranch},{id:'parameters' as Tab,name:'参数核查',icon:SlidersHorizontal},{id:'runs' as Tab,name:'运行与结果',icon:Layers3}];
const sessionId=new URLSearchParams(location.search).get('sessionId');
export default function App(){
  const [project,setProject]=useState<Project|null>(null),[projects,setProjects]=useState<Project[]>([]),[health,setHealth]=useState<Health|null>(null);
  const [tab,setTab]=useState<Tab>('inputs'),[selection,setSelection]=useState<Selection>({}),[error,setError]=useState(''),[toast,setToast]=useState('');
  const [loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[newModal,setNewModal]=useState(false),[name,setName]=useState(''),[chat,setChat]=useState(false),[prompt,setPrompt]=useState(''),[createError,setCreateError]=useState('');
  const projectRef=useRef(project),switching=useRef(false);projectRef.current=project;
  const accept=useCallback((p:Project)=>{if(projectRef.current&&projectRef.current.id!==p.id)return;if(projectRef.current?.id===p.id&&projectRef.current.revision>p.revision)return;projectRef.current=p;setProject(p);setProjects(previous=>[p,...previous.filter(x=>x.id!==p.id)]);},[]);
  const report=useCallback((e:unknown)=>setError(e instanceof Error?e.message:'操作未完成，请重试。'),[]);
  const refresh=useCallback(async()=>{
    setLoading(true);
    const states=await Promise.allSettled([api.health(),api.list()]);
    if(states[0].status==='fulfilled')setHealth(states[0].value);else setHealth(null);
    if(states[1].status==='fulfilled')setProjects(states[1].value.projects);else report(states[1].reason);
    if(states[0].status==='fulfilled'&&!states[0].value.capabilities.sessionBindings){setError(`当前热服务版本为 ${states[0].value.version}。请先按安装说明升级轻量热服务到 0.2.0 或兼容版本，再使用会话工作台。`);setLoading(false);return;}
    try{const binding=sessionId?await api.binding(sessionId):null;const id=binding?.projectId||(!sessionId?projectRef.current?.id:null);if(id){const restored=await api.get(id);projectRef.current=restored;accept(restored);if(binding)setSelection(binding.selection);}else if(sessionId){projectRef.current=null;setProject(null);} }catch(e){report(e);}finally{setLoading(false);}
  },[accept,report]);
  useEffect(()=>{void refresh();},[refresh]);
  useEffect(()=>{let stopped=false;let handle:ReturnType<typeof setTimeout>;
    async function poll(){await Promise.allSettled([api.health().then(h=>{if(!stopped)setHealth(h);}).catch(()=>{if(!stopped)setHealth(null);}), (async()=>{
      const binding=sessionId?await api.binding(sessionId):null;
      if(stopped||switching.current)return;
      const id=binding?.projectId||(!sessionId?projectRef.current?.id:null);
      if(!id)return;
      const p=await api.get(id);
      if(stopped||switching.current)return;
      if(binding&&projectRef.current?.id!==id){projectRef.current=p;setSelection(binding.selection);}
      if(projectRef.current?.id===id)accept(p);
    })()]);if(!stopped)handle=setTimeout(()=>void poll(),4000);}
    handle=setTimeout(()=>void poll(),4000);return()=>{stopped=true;clearTimeout(handle);};},[accept]);
  useEffect(()=>{if(!toast)return;const handle=setTimeout(()=>setToast(''),4500);return()=>clearTimeout(handle);},[toast]);
  const save=useCallback(async(patch:ProjectPatch)=>{const p=projectRef.current;if(!p)return;setBusy(true);setError('');try{const next=await api.save(p.id,p.revision,patch);accept(next);setToast(next.revision!==p.revision?'修改已保存；可在运行与结果中新建计算':'参数未变化');return next;}catch(e){if(e instanceof ApiError&&e.status===409){accept(await api.get(p.id));setError('输入已被其他窗口或对话更新，请核对最新版本后重新保存。');}else report(e);}finally{setBusy(false);}},[accept,report]);
  async function choose(p:Project){if(sessionId)await api.bind(sessionId,p.id);projectRef.current=p;accept(p);setSelection({});}
  async function create(example=false){if(busy)return;if(health&&!health.capabilities.sessionBindings){setCreateError('请先升级轻量热服务到 0.2.0 或兼容版本。');return;}setBusy(true);setCreateError('');setError('');switching.current=true;try{const p=example?await api.example():await api.create(name.trim()||'新的热设计项目');setProjects(list=>[p,...list.filter(x=>x.id!==p.id)]);await choose(p);setNewModal(false);setName('');setTab(example?'model':'inputs');}catch(e){report(e);setCreateError(e instanceof Error?e.message:'创建失败');}finally{switching.current=false;setBusy(false);}}
  async function switchProject(id:string){if(!id||busy)return;setBusy(true);switching.current=true;try{await choose(await api.get(id));}catch(e){report(e);}finally{switching.current=false;setBusy(false);}}
  function select(s:Selection){setSelection(s);const p=projectRef.current;if(sessionId&&p)void api.bind(sessionId,p.id,s,true).catch(report);}
  async function sendChat(){if(!project||!prompt.trim())return;try{if(sessionId)await api.bind(sessionId,project.id,selection,true);const context={type:'satellite-thermal:chat',projectId:project.id,revision:project.revision,selection,prompt:prompt.trim()};if(window.parent!==window){window.parent.postMessage(context,location.origin);setToast('已带入左侧对话草稿');}else{await navigator.clipboard.writeText(`${prompt}\n项目 ${project.id} / v${project.revision} / ${JSON.stringify(selection)}`);setToast('已复制问题与项目上下文');}setChat(false);setPrompt('');}catch(e){report(e);}}
  return <div className="engineering-shell">
    <header className="engineering-toolbar"><strong>热设计</strong><select aria-label={sessionId?'关联当前会话的项目':'选择项目'} value={project?.id||''} disabled={busy||loading} onChange={e=>void switchProject(e.target.value)}><option value="">{sessionId?'本会话尚未关联项目':'选择项目'}</option>{projects.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select><Button title="新建项目" aria-label="新建项目" disabled={busy} onClick={()=>{setCreateError('');setNewModal(true);}}><Plus size={16}/></Button><Badge>v{project?.revision||0}</Badge>{project?.geometry.source==='example'&&<Badge tone="amber">示例</Badge>}<span className={`connection-dot ${health?'online':''}`} title={health?`服务在线 · NX ${health.solver.connected?'已连接':'未连接'}`:'服务未连接'}/><button className="icon-button" title="刷新" aria-label="刷新服务" onClick={()=>void refresh()} disabled={loading}><RefreshCw size={16}/></button>{project&&<a className="icon-button" href={api.exportUrl(project.id)} download title="导出项目" aria-label="导出项目"><ArrowDownToLine size={16}/></a>}<Button disabled={!project} onClick={()=>setChat(true)}><MessageSquare size={15}/>对话</Button></header>
    <nav className="engineering-tabs">{tabs.map(t=><button key={t.id} onClick={()=>setTab(t.id)} className={tab===t.id?'active':''}><t.icon size={15}/>{t.name}{t.id==='runs'&&!!project?.runs.filter(activeRun).length&&<Badge>{project.runs.filter(activeRun).length}</Badge>}</button>)}</nav>
    {error&&<div role="alert" className="error-banner"><span>{error}</span><button className="icon-button" aria-label="关闭提示" onClick={()=>setError('')}><X size={16}/></button></div>}
    <main className={`engineering-content tab-${tab}`}>
      {!project?<section className="card project-empty"><h2>{sessionId?'为当前会话创建或关联热项目':'热设计项目'}</h2><p>从上方选择已有项目，或新建后导入 STEP 和资料。</p><div className="row"><Button variant="primary" busy={busy} onClick={()=>setNewModal(true)}>新建项目</Button><Button busy={busy} onClick={()=>void create(true)}>载入示例（未求解）</Button></div></section>:<>
      {tab==='inputs'&&<Inputs key={project.id} project={project} accept={accept} onError={report}/>}
      {tab==='model'&&<Workbench key={project.id} project={project} save={save} selection={selection} setSelection={select} busy={busy} onImport={()=>setTab('inputs')} onChat={()=>setChat(true)} accept={accept} onError={report}/>}
      {tab==='parameters'&&<Parameters key={project.id} project={project} save={save} busy={busy} selection={selection} setSelection={select} onError={report}/>}
      {tab==='runs'&&<Runs key={project.id} project={project} accept={accept} onError={report} busy={busy}/>}
      </>}
    </main>
    <footer className="engineering-status"><span>{project?`${project.components.length} 个组件 · ${project.links.length} 条连接`:'未选择项目'}</span><span>{sessionId?'当前会话绑定':'独立工作台'} · 保存后手动计算</span></footer>
    {toast&&<div role="status" className="toast">{toast}</div>}
    {newModal&&<Modal title="新建热设计项目" onClose={()=>{if(!busy)setNewModal(false);}}>{createError&&<div role="alert" className="error-banner">{createError}</div>}<form onSubmit={e=>{e.preventDefault();void create();}}><Field label="项目名称"><input autoFocus value={name} maxLength={120} onChange={e=>setName(e.target.value)} placeholder="项目名称"/></Field><div className="modal-actions"><Button type="button" disabled={busy} onClick={()=>setNewModal(false)}>取消</Button><Button type="submit" busy={busy} variant="primary">创建项目</Button></div>{busy&&<p role="status">正在保存项目；连接异常会在 20 秒内提示。</p>}</form></Modal>}
    {chat&&<Modal title="与当前会话协作" onClose={()=>setChat(false)}><div className="context-chips"><Badge>{project?.name}</Badge><Badge>v{project?.revision}</Badge></div><textarea rows={4} value={prompt} onChange={e=>setPrompt(e.target.value)} placeholder="描述要核查或修改的内容…"/><div className="modal-actions"><Button variant="primary" disabled={!prompt.trim()} onClick={()=>void sendChat()}>{window.parent!==window?'带入左侧对话草稿':'复制问题与上下文'}</Button></div></Modal>}
  </div>;
}
