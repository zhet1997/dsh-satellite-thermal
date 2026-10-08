import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowDownToLine, ArrowUpRight, Box, ChevronDown, CircleHelp, FileInput, FolderOpen, GitBranch, Layers3, Menu, MessageSquare, Plus, RefreshCw, Rocket, Satellite, SlidersHorizontal, Sparkles, Sun, X } from 'lucide-react';
import { api, ApiError } from './api';
import type { Health, Project, ProjectPatch, Selection } from './types';
import { activeRun } from './types';
import { Badge, Button, Field, Modal, Notice, Steps } from './components/Common';
import { Inputs } from './components/Inputs';
import { Workbench } from './components/Workbench';
import { Parameters } from './components/Parameters';
import { OrbitCases } from './components/OrbitCases';
import { Runs } from './components/Runs';

type Tab='inputs'|'model'|'parameters'|'orbit'|'runs';
const tabs = [{id:'inputs' as Tab,name:'资料与几何',detail:'从已有资料开始',icon:FileInput},{id:'model' as Tab,name:'热模型',detail:'拓扑 · 三维 · 连接',icon:GitBranch},{id:'parameters' as Tab,name:'参数核查',detail:'确定的标准输入',icon:SlidersHorizontal},{id:'orbit' as Tab,name:'轨道与工况',detail:'冷态 · 热态 · 自定义',icon:Sun},{id:'runs' as Tab,name:'运行与结果',detail:'版本 · 核查 · 温度',icon:Layers3}];
export default function App() {
  const [project,setProject]=useState<Project|null>(null), [projects,setProjects]=useState<Project[]>([]), [health,setHealth]=useState<Health|null>(null);
  const [tab,setTab]=useState<Tab>('inputs'),[selection,setSelection]=useState<Selection>({}),[error,setError]=useState(''),[toast,setToast]=useState('');
  const [loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[newModal,setNewModal]=useState(false),[name,setName]=useState(''),[menu,setMenu]=useState(false),[help,setHelp]=useState(false),[chat,setChat]=useState(false),[prompt,setPrompt]=useState('');
  const [createError,setCreateError]=useState('');
  const projectRef=useRef(project); projectRef.current=project;
  const accept=useCallback((p:Project)=>{if(projectRef.current?.id===p.id&&projectRef.current.revision>p.revision)return;setProject(p);localStorage.setItem('thermal-project',p.id);setProjects(previous=>[p,...previous.filter(x=>x.id!==p.id)]);},[]);
  const report=useCallback((e:unknown)=>setError(e instanceof Error?e.message:'操作未完成，请重试。'),[]);
  const refresh=useCallback(async()=>{try{const [h,list]=await Promise.all([api.health(),api.list()]);setHealth(h);setProjects(list.projects);const last=localStorage.getItem('thermal-project');const id=projectRef.current?.id||list.projects.find(p=>p.id===last)?.id;if(id)accept(await api.get(id));setError('');}catch(e){report(e);}finally{setLoading(false);}},[accept,report]);
  useEffect(()=>{void refresh();},[refresh]);
  useEffect(()=>{
    let stopped=false;
    let handle:ReturnType<typeof setTimeout>;
    async function poll(){
      const id=projectRef.current?.id;
      await Promise.allSettled([
        api.health().then(h=>{if(!stopped)setHealth(h);}).catch(()=>{if(!stopped)setHealth(null);}),
        ...(id?[api.get(id).then(p=>{if(!stopped&&projectRef.current?.id===id)accept(p);})]:[])
      ]);
      if(!stopped)handle=setTimeout(()=>void poll(),4000);
    }
    handle=setTimeout(()=>void poll(),4000);
    return()=>{stopped=true;clearTimeout(handle);};
  },[accept]);
  useEffect(()=>{if(!toast)return;const handle=setTimeout(()=>setToast(''),5500);return()=>clearTimeout(handle);},[toast]);
  const save=useCallback(async(patch:ProjectPatch)=>{const p=projectRef.current;if(!p)return;setBusy(true);setError('');try {const next=await api.save(p.id,p.revision,patch);accept(next);setToast(next.revision!==p.revision?(next.settings.autoRun&&next.geometry.status==='ready'?'已保存，新版本已加入运行队列':'已保存为新输入版本'):'参数未变化');return next;}catch(e){if(e instanceof ApiError&&e.status===409){accept(await api.get(p.id));setError('输入已被其他窗口或对话更新，已载入最新版本。请核对后重新保存你的修改。');}else report(e);}finally{setBusy(false);}},[accept,report]);
  function openNewProject(){setCreateError('');setNewModal(true);}
  async function create(example=false){if(busy)return;setBusy(true);setError('');setCreateError('');try{accept(example?await api.example():await api.create(name.trim()||'新的热设计项目'));setNewModal(false);setName('');setTab(example?'model':'inputs');setSelection({});}catch(e){report(e);if(!example)setCreateError(e instanceof Error?e.message:'项目创建未完成，请检查服务连接。');}finally{setBusy(false);}}
  async function switchProject(id:string){setBusy(true);try{accept(await api.get(id));setSelection({});}catch(e){report(e);}finally{setBusy(false);}}
  async function startRun(){if(!project)return;setBusy(true);try{await api.run(project.id,project.revision);accept(await api.get(project.id));setTab('runs');setToast('运行已提交，进度会自动更新。');}catch(e){report(e);}finally{setBusy(false);}}
  function openTab(t:Tab){setTab(t);setMenu(false);}
  function sendChat(){if(!project||!prompt.trim())return;const context={type:'satellite-thermal:chat',projectId:project.id,revision:project.revision,selection,prompt:prompt.trim()};if(window.parent!==window){window.parent.postMessage(context,window.location.origin);setToast('已将问题和当前选区发送到 DSH 对话。');setChat(false);setPrompt('');}else{void navigator.clipboard.writeText(`${prompt}\n\n热项目：${project.id}\n输入版本：${project.revision}\n选中对象：${JSON.stringify(selection)}`).then(()=>setToast('已复制问题和项目上下文，可粘贴到 DSH 对话。')).catch(()=>setError('浏览器未允许复制，请手动复制输入内容。'));}}
  const current=tabs.find(t=>t.id===tab)!;const running=project?.runs.filter(activeRun).length||0;
  return <div className="app-shell">
    {menu&&<div className="sidebar-scrim" onClick={()=>setMenu(false)}/>}
    <aside className={`sidebar ${menu?'open':''}`}><a className="brand" href="#" onClick={e=>{e.preventDefault();openTab('inputs');}}><span className="brand-mark"><Satellite size={24}/></span><div>热设计工作台<small>SATELLITE THERMAL</small></div></a><div className="sidebar-label">工程工作空间</div><nav>{tabs.map(t=><button key={t.id} className={`nav-item ${tab===t.id?'active':''}`} onClick={()=>openTab(t.id)}><t.icon size={19}/><span>{t.name}<small>{t.detail}</small></span>{t.id==='runs'&&running>0&&<span className="nav-count">{running}</span>}</button>)}</nav><div className="sidebar-bottom"><div className="engine-card"><span className={`connection-dot ${health?.solver.connected?'online':''}`}/><div>NX / Simcenter<small>{health?.solver.connected?'执行端已连接':health?.solver.configured?'正在等待执行端':'尚未连接执行端'}</small></div><span className="engine-version">稳态</span></div><button className="sidebar-help" onClick={()=>setHelp(true)}><CircleHelp size={17}/> 工作台使用说明 <ArrowUpRight size={14}/></button><div className="sidebar-footer">DSH 插件 <span>v0.1</span></div></div></aside>
    <div className="main-shell"><header className="topbar"><div className="topbar-left"><button className="icon-button mobile-only" onClick={()=>setMenu(true)} aria-label="打开导航"><Menu size={20}/></button><span className="breadcrumb">工作空间 <span>/</span></span><div className="project-switch"><FolderOpen size={17}/><select value={project?.id||''} onChange={e=>{if(e.target.value)void switchProject(e.target.value);}} aria-label="切换项目"><option value="">选择项目</option>{projects.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select><ChevronDown size={14}/></div><button className="icon-button" aria-label="新建项目" title="新建项目" onClick={openNewProject}><Plus size={17}/></button></div><div className="topbar-right"><span className="service-status"><span className={`connection-dot ${health?'online':''}`}/>{health?'服务在线':'服务未连接'}</span><button className="icon-button" onClick={()=>void refresh()} aria-label="刷新服务"><RefreshCw size={16}/></button><Button variant="subtle" onClick={()=>setChat(true)} disabled={!project}><Sparkles size={16}/><span className="hide-small">对话协作</span></Button></div></header>
    <main><div className="page-heading"><div><div className="eyebrow">THERMAL WORKSPACE <span>稳态热分析</span></div><h1>{project?current.name:'从几何到可信的热模型'}</h1><p>{project?({inputs:'让零散的工程资料，成为可追溯的模型输入。',model:'在同一处看清组件、传热关系与真实几何。',parameters:'每个采用值都有来源，每次修改都保留新版本。',orbit:'以同一时刻的六向载荷，定义清晰可核查的工况。',runs:'从输入快照到 NX 结果，始终知道计算走到了哪里。'}[tab]):'导入几何与热参数，梳理传热关系，自动生成可核查的工程模型。'}</p></div><div className="heading-actions">{project&&<><a className="button subtle" href={api.exportUrl(project.id)} download><ArrowDownToLine size={16}/> 导出项目</a><Button variant="primary" busy={busy} disabled={project.geometry.status!=='ready'} onClick={()=>void startRun()}><Rocket size={16}/> 运行模型</Button></>}</div></div>
    {error&&<div role="alert" className="error-banner"><span>{error}</span><button className="icon-button" aria-label="关闭提示" onClick={()=>setError('')}><X size={17}/></button></div>}
    {project?.geometry.source==='example'&&<Notice tone="example">当前是<strong>体验示例</strong>。几何与参数用于探索工作台，尚未经过 NX 求解；运行结果只显示执行端真实回传的数据。</Notice>}
    {!project?<div className="welcome"><Steps/><div className="welcome-hero"><div className="welcome-copy"><Badge tone="teal">一个项目，一份确定的输入</Badge><h2>把热设计关系<br/>看清楚，再算清楚。</h2><p>STEP、几何交付包、表格或一段文字，都可以作为起点。缺失参数会补充并标记，您可以在任意阶段查看、调整与重算。</p><div className="row"><Button variant="primary large" busy={busy||loading} onClick={openNewProject}><Plus size={18}/> 新建热设计项目</Button><Button variant="subtle large" busy={busy} onClick={()=>void create(true)}><Sparkles size={17}/> 体验示例</Button></div><small>首版支持实体与薄板混合建模 · 稳态热分析</small></div><div className="welcome-art" aria-hidden="true"><div className="orbit-ring one"/><div className="orbit-ring two"/><div className="sat-core"><Box size={72} strokeWidth={1}/></div><span className="art-card power"><Sun size={18}/> 外部热流 <b>六向工况</b></span><span className="art-card material"><Layers3 size={18}/> 工程几何 <b>实体 / 薄板</b></span><span className="art-card connection"><GitBranch size={18}/> 传热关系 <b>接触 · 辐射</b></span></div></div><div className="feature-grid">{[{n:'01',title:'输入自由，交接确定',text:'统一单位、补全参数、保留来源，让下游读取完整的标准输入。'},{n:'02',title:'看见模型如何建立',text:'拓扑、三维与参数联动，清楚区分配置输入与 NX 实际回读。'},{n:'03',title:'每次变化，都有记录',text:'修改后自动生成新的运行，旧结果与原始输入始终对应。'}].map(f=><div className="feature-card" key={f.n}><span>{f.n}</span><h3>{f.title}</h3><p>{f.text}</p></div>)}</div></div>:<>
    {tab==='inputs'&&<Inputs project={project} accept={accept} onError={report} onContinue={()=>setTab('model')} />}
    {tab==='model'&&<Workbench project={project} save={save} selection={selection} setSelection={setSelection} busy={busy} onImport={()=>setTab('inputs')} onChat={()=>setChat(true)}/>}
    {tab==='parameters'&&<Parameters project={project} save={save} busy={busy} selection={selection} setSelection={setSelection} onError={report}/>}
    {tab==='orbit'&&<OrbitCases project={project} save={save} accept={accept} onError={report}/>}
    {tab==='runs'&&<Runs project={project} accept={accept} onError={report} onStart={startRun} busy={busy}/>}
    </>}
    <footer className="page-footer"><span><Layers3 size={13}/> {project?`${project.components.length} 个组件 · 输入版本 ${project.revision}`:'开放的输入，可追溯的工程过程'}</span><span>{project?.settings.autoRun?'修改输入后自动排队重算':'热建模与求解 · NX / Simcenter'}</span></footer></main></div>
    {toast&&<div role="status" className="toast"><span className="toast-dot"/>{toast}</div>}
    {newModal&&<Modal title="新建热设计项目" onClose={()=>setNewModal(false)}><p className="muted">给项目起一个容易识别的名字，随后导入几何和热参数。</p>{createError&&<div role="alert" className="error-banner"><span>{createError}</span></div>}<form aria-busy={busy} onSubmit={e=>{e.preventDefault();void create();}}><Field label="项目名称"><input autoFocus value={name} onChange={e=>setName(e.target.value)} placeholder="例如：立方星平台热设计" maxLength={120}/></Field><div className="modal-actions"><Button onClick={()=>setNewModal(false)} type="button">取消</Button><Button variant="primary" busy={busy} type="submit">{busy?'正在创建项目…':'创建项目'} <ArrowUpRight size={16}/></Button></div>{busy&&<p className="muted" role="status">正在保存项目信息，通常只需几秒；连接异常时会在 20 秒内给出提示。</p>}</form></Modal>}
    {chat&&<Modal title="带着工程上下文开始对话" onClose={()=>setChat(false)}><p className="muted">当前项目、输入版本和选中对象会一并交给 DSH。对话通过同一份项目数据修改模型。</p><div className="context-chips"><Badge>{project?.name}</Badge><Badge>版本 {project?.revision}</Badge>{Object.entries(selection).map(([k,v])=><Badge key={k}>{v}</Badge>)}</div><textarea rows={5} value={prompt} onChange={e=>setPrompt(e.target.value)} placeholder="例如：检查选中设备的材料和功耗，列出需要补充的资料。"/><div className="modal-actions"><Button variant="primary" disabled={!prompt.trim()} onClick={sendChat}><MessageSquare size={16}/>{window.parent!==window?'发送到 DSH 对话':'复制问题与上下文'}</Button></div></Modal>}
    {help&&<Modal title="使用热设计工作台" onClose={()=>setHelp(false)}><ol className="help-list"><li><b>导入几何和资料</b><p>上传 STEP 或几何插件交付包，再补充表格、Word、文字 PDF 或文本。</p></li><li><b>看清热模型</b><p>点选组件查看参数；从组件的右侧端口拖动到另一个组件，建立热连接。拓扑排版不会改变工程几何。</p></li><li><b>核查采用值和工况</b><p>估算参数会带有标记。可查看标准输入、批量修改参数、计算六向热流并选取冷态或热态。</p></li><li><b>跟踪真实运行</b><p>保存修改默认生成新运行，旧运行继续。NX 实际模型与结果只在执行端回传后展示；可随时下载项目数据包。</p></li></ol></Modal>}
  </div>;
}
