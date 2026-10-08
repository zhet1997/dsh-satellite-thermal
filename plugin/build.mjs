import fs from 'node:fs/promises'
import path from 'node:path'
import {fileURLToPath} from 'node:url'
import {build} from 'esbuild'
const root=path.dirname(fileURLToPath(import.meta.url))
await fs.mkdir(path.join(root,'lib'),{recursive:true})
const result=await build({entryPoints:[path.join(root,'src/client.jsx')],bundle:true,write:false,format:'cjs',platform:'browser',external:['react'],target:'es2022'})
await fs.writeFile(path.join(root,'lib/client.js'),`window.__ModuleLoader__.load({id:"@lbk/dsh-satellite-thermal",factory:(require)=>{var module={exports:{}};var exports=module.exports;\n${result.outputFiles[0].text}\nreturn module.exports;}});\n`)
await fs.access(path.join(root,'../web/dist/index.html'))
await fs.rm(path.join(root,'public'),{recursive:true,force:true})
await fs.cp(path.join(root,'../web/dist'),path.join(root,'public'),{recursive:true})
const index=path.join(root,'public/index.html')
const html=await fs.readFile(index,'utf8')
await fs.writeFile(index,html.replace('<head>','<head><script>window.THERMAL_API_BASE="/api/satellite-thermal/service";</script>'))
console.log('DSH plugin client and workbench built.')
