# Thermal workbench interface v1

Implementation contract for parallel development. Service routes start at `/api`; the DSH host proxies them under `/api/satellite-thermal/service` and serves the built UI under `/satellite-thermal/`. UI reads its base from `window.THERMAL_API_BASE` or defaults to `/api`. Vite proxies `/api` to `http://127.0.0.1:8790`.

Responses are direct JSON objects. Errors: `{detail: string, code?: string}`. IDs are opaque strings. Geometry coordinates are mm, Z-up. Temperatures in results are K; UI can display °C. Project revisions are optimistic concurrency tokens. Tokens and server filesystem paths are never returned to the browser.

## Project shape

```ts
type Origin = {status: 'provided'|'estimated'|'manual'|'derived'; source: string; originalValue?: unknown; reason?: string}
type Face = {id:string; name:string; normal:[number,number,number]; center:[number,number,number]; areaMm2:number; side?:'top'|'bottom'}
type Component = {id:string; name:string; kind:'solid'|'shell'; material:string; conductivity:number; emissivity:number; absorptivity:number; power:number; thickness?:number; temperatureLimitMin?:number; temperatureLimitMax?:number; center:[number,number,number]; size:[number,number,number]; faces:Face[]; provenance:Record<string,Origin>}
type Link = {id:string; source:string; target:string; sourceFace?:string; targetFace?:string; kind:'contact'|'conductance'|'radiation'; value:number; unit:string; provenance:Origin}
type Boundary = {id:string; name:string; kind:'space'|'heatflux'|'temperature'; targets:{componentId:string;faceId?:string}[]; value:number; unit:string; provenance:Origin}
type Case = {id:string; name:string; kind:'cold'|'hot'|'manual'; time:number; season:string; flux:Record<string,number>; source:string}
type Issue = {id:string; severity:'info'|'warning'|'error'; componentId?:string; field?:string; message:string; resolution?:string}
type Project = {id:string;name:string;revision:number;createdAt:string;updatedAt:string;geometry:{status:'empty'|'importing'|'ready'|'error';filename?:string;source:'step'|'geometry-package'|'example';previewUrl?:string;error?:string};components:Component[];links:Link[];boundaries:Boundary[];cases:Case[];orbit:{semiMajorAxisKm:number;eccentricity:number;inclinationDeg:number;raanDeg:number;argumentOfPerigeeDeg:number;trueAnomalyDeg:number;season:string};settings:{autoRun:boolean;meshSizeMm:number;spaceTemperatureK:number};documents:{id:string;name:string;status:string;excerpt?:string}[];issues:Issue[];runs:Run[]}
type Run = {id:string;projectId:string;revision:number;createdAt:string;updatedAt:string;status:'queued'|'waiting_connection'|'preparing'|'modeling'|'checking'|'solving'|'extracting'|'completed'|'failed'|'cancel_requested'|'cancelled';stage:string;progress:number;message:string;caseId?:string;issues:Issue[];events:{time:string;stage:string;message:string}[];result?:{source:'nx';components:{id:string;minK:number;maxK:number;meanK:number}[];energyBalance?:Record<string,unknown>;meshUrl?:string};artifacts?:{name:string;url:string;size:number}[]}
```

Radiation `value` is a configured factor, not a fabricated view factor or computed heat flow. Actual radiative exchange must come from NX readback/results. A synthetic example project may demonstrate editing and geometry but must never claim NX execution.

## Routes

- `GET /health` → `{status:'ok',version,solver:{configured:boolean,connected:boolean,message:string},capabilities:{geometry:boolean,documents:boolean,nx:boolean}}`.
- `GET /projects` → `{projects:Project[]}` (summaries may omit large face arrays).
- `POST /projects` `{name}` → Project.
- `POST /projects/example` → Project with explicitly named example data and no fabricated solved result.
- `GET /projects/:id` → Project.
- `PATCH /projects/:id` `{revision,name?,components?,links?,boundaries?,cases?,orbit?,settings?}` → normalized Project. Arrays replace that section. Content-changing saves automatically freeze and queue a new run when geometry is ready and autoRun is true. Layout-only graph positions stay in browser storage and do not call PATCH.
- `POST /projects/:id/geometry` multipart file (`.step/.stp/.zip`) → Project, may be `importing`, poll project until ready/error.
- `POST /projects/:id/documents` multipart file → Project. Parse supported structured formats; unstructured text is preserved for native DSH tools to inspect and submit structured updates.
- `POST /projects/:id/text` `{text}` → Project with extracted quantities/retained source text.
- `GET /projects/:id/standard` → complete standardized execution snapshot including provenance/check results (no raw secrets).
- `POST /projects/:id/heatflux` `{orbit?,time?,season?}` → Project with six-direction profiles and cold/hot cases derived at consistent timestamps. UI may receive `heatfluxProfile:{time:number,faces:Record<string,number>}[]` on the project response.
- `POST /projects/:id/runs` `{revision,caseId?}` → Run; duplicate identical active request is idempotent.
- `GET /projects/:id/runs/:runId` → Run plus input snapshot/readback if available.
- `POST /projects/:id/runs/:runId/cancel` → Run; queued work can cancel immediately; active work waits for executor acknowledgement.
- `GET /projects/:id/export` → ZIP containing standardized inputs, references and run evidence/results if present; manifest states what has not been executed.
- `GET /projects/:id/artifacts/:artifactId` → allowlisted project artifact only.

Service authentication: optional `THERMAL_SERVICE_TOKEN` via bearer header (host and MCP attach it). Storage root `THERMAL_DATA_DIR`; no browser-selected absolute paths. NX executor configuration comes from service environment; lightweight runtime must not contain NX.

## Independent NX execution adapter

Worker HTTP API: `GET /health`, `POST /jobs` (frozen snapshot and artifact package or multipart bundle), `GET /jobs/:id`, `POST /jobs/:id/cancel`, `GET /jobs/:id/artifacts/:name`. Driver is separately deployed on an NX workstation. Root `service` must communicate via a small isolated adapter module; exact transfer fields will be coordinated before integration. Never use synthetic temperatures as a fallback for unavailable NX. Awaiting a connection is distinct from success.
