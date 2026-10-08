import fs from 'node:fs/promises'
import path from 'node:path'
import os from 'node:os'
import {fileURLToPath} from 'node:url'
const name=process.argv[2]
if(!name||!/^[A-Za-z0-9_-]+$/.test(name))throw Error('用法：node install-profile.mjs <现有 profile 名称>。也可直接在 DSH 插件管理中安装 tgz。')
const root=path.dirname(fileURLToPath(import.meta.url)),profile=path.join(process.env.DSH_HOME||path.join(os.homedir(),'.dsh'),'profiles',name)
const manifestFile=path.join(profile,'package.json'),original=await fs.readFile(manifestFile,'utf8'),manifest=JSON.parse(original)
if(!Array.isArray(manifest.dsh?.profile?.bundles))throw Error('目标不是有效 DSH profile')
const id='@lbk/dsh-satellite-thermal',link=path.join(profile,'node_modules',...id.split('/'))
try{if(path.resolve(await fs.realpath(link)).toLowerCase()!==root.toLowerCase())throw Error('此 profile 已安装另一个热插件副本，请通过原插件管理器升级。')}catch(e){if(e.code!=='ENOENT')throw e;await fs.mkdir(path.dirname(link),{recursive:true});await fs.symlink(root,link,process.platform==='win32'?'junction':'dir')}
await fs.writeFile(manifestFile+'.thermal-backup-'+Date.now(),original)
manifest.dependencies||={};manifest.dependencies[id]='link:'+root.replaceAll('\\','/')
if(!manifest.dsh.profile.bundles.includes(id))manifest.dsh.profile.bundles.push(id)
await fs.writeFile(manifestFile,JSON.stringify(manifest,null,2)+'\n')
console.log(`已加入 ${name}，保留原有全部 bundles 与配置。重启该 DSH profile 后使用。`)
