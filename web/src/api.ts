import type { Health, Project, ProjectPatch, Run } from './types';
declare global { interface Window { THERMAL_API_BASE?:string } }
export const API_BASE = (window.THERMAL_API_BASE || '/api').replace(/\/$/,'');
export class ApiError extends Error {
  status:number;
  constructor(message:string, status:number) {super(message);this.name='ApiError';this.status=status;}
}
export const REQUEST_TIMEOUTS = {health:8_000, ordinary:20_000, processing:300_000} as const;
export async function request<T>(path:string, options:RequestInit = {}, timeoutMs:number = REQUEST_TIMEOUTS.ordinary):Promise<T> {
  const controller = new AbortController();
  const externalSignal = options.signal;
  let timer:ReturnType<typeof setTimeout>|undefined;
  let cancel:(()=>void)|undefined;
  // Bound the whole response, including its body. A received header is not a completed operation.
  const deadline = new Promise<never>((_,reject)=>{
    cancel=()=>{reject(new ApiError('已停止等待本次请求。操作可能已提交，请刷新项目确认状态。',0));controller.abort();};
    if(externalSignal?.aborted){cancel();return;}
    externalSignal?.addEventListener('abort',cancel,{once:true});
    timer=setTimeout(()=>{
      reject(new ApiError(`热服务未在 ${Math.ceil(timeoutMs/1000)} 秒内完成响应。请检查服务连接；恢复后先刷新项目，确认操作是否已完成，再重试。`,408));
      controller.abort();
    },timeoutMs);
  });
  try {
    const response = (async()=>{
      if(controller.signal.aborted) throw new ApiError('已停止等待本次请求。',0);
      const result=await fetch(`${API_BASE}${path}`,{...options,signal:controller.signal,headers:{...(options.body instanceof FormData?{}:{'Content-Type':'application/json'}),...options.headers}});
      if(!result.ok){const body=await result.json().catch(()=>({}));throw new ApiError(typeof body.detail==='string'?body.detail:`请求未完成（${result.status}）`,result.status);}
      return await result.json() as T;
    })();
    return await Promise.race([response,deadline]);
  } catch(error) {
    if(error instanceof ApiError) throw error;
    if(error instanceof SyntaxError) throw new ApiError('热服务返回的数据不完整，请刷新项目或检查服务连接。',502);
    throw new ApiError('暂时无法连接热设计服务，请在 DSH 设置 → 内置插件 → 卫星热建模中检查连接。',0);
  } finally {
    clearTimeout(timer);
    if(cancel) externalSignal?.removeEventListener('abort',cancel);
  }
}
const projectPath = (id:string) => `/projects/${encodeURIComponent(id)}`;
export const api = {
  health:()=>request<Health>('/health',{},REQUEST_TIMEOUTS.health), list:()=>request<{projects:Project[]}>('/projects'),
  create:(name:string)=>request<Project>('/projects',{method:'POST',body:JSON.stringify({name})}),
  example:()=>request<Project>('/projects/example',{method:'POST'},REQUEST_TIMEOUTS.processing),
  get:(id:string)=>request<Project>(projectPath(id)),
  save:(id:string,revision:number,patch:ProjectPatch)=>request<Project>(projectPath(id),{method:'PATCH',body:JSON.stringify({revision,...patch})}),
  upload:(id:string,type:'geometry'|'documents',file:File)=>{const body=new FormData();body.append('file',file);return request<Project>(`${projectPath(id)}/${type}`,{method:'POST',body},REQUEST_TIMEOUTS.processing);},
  text:(id:string,text:string)=>request<Project>(`${projectPath(id)}/text`,{method:'POST',body:JSON.stringify({text})},REQUEST_TIMEOUTS.processing),
  standard:(id:string)=>request<Record<string,unknown>>(`${projectPath(id)}/standard`),
  heatflux:(id:string,body:unknown)=>request<Project>(`${projectPath(id)}/heatflux`,{method:'POST',body:JSON.stringify(body)},REQUEST_TIMEOUTS.processing),
  run:(id:string,revision:number,caseId?:string)=>request<Run>(`${projectPath(id)}/runs`,{method:'POST',body:JSON.stringify({revision,caseId})}),
  getRun:(id:string,runId:string)=>request<Run>(`${projectPath(id)}/runs/${encodeURIComponent(runId)}`),
  cancel:(id:string,runId:string)=>request<Run>(`${projectPath(id)}/runs/${encodeURIComponent(runId)}/cancel`,{method:'POST'}),
  exportUrl:(id:string)=>`${API_BASE}${projectPath(id)}/export`
};
export function artifactUrl(url:string) { if(url.startsWith('/api/')) return `${API_BASE}${url.slice(4)}`;return url; }
