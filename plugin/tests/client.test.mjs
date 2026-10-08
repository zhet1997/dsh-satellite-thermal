import test from 'node:test'
import assert from 'node:assert/strict'
import vm from 'node:vm'
import {createRequire} from 'node:module'
import {build} from 'esbuild'
const requireWeb=createRequire(new URL('../../web/package.json',import.meta.url))
const React=requireWeb('react')
const {renderToStaticMarkup}=requireWeb('react-dom/server')

test('official keyed sidebar body preserves session identity and opens by registered kind',async()=>{
  const output=await build({entryPoints:[new URL('../src/client.jsx',import.meta.url).pathname.replace(/^\/(?=[A-Za-z]:)/,'')],bundle:true,write:false,format:'cjs',platform:'node',external:['react']})
  const module={exports:{}}
  vm.runInNewContext(output.outputFiles[0].text,{module,exports:module.exports,require:requireWeb,URLSearchParams})
  const registrations=[],types=[],opened=[]
  const ctx={effect:fn=>fn(),connection:{rpc:{}},sidebarRightTabs:{register:def=>types.push(def)},sidebarRight:{openTab:kind=>opened.push(kind)},slots:{inject:(_,fn)=>fn(),register:(def,body)=>{
    // DSH keyed seats use key, list seats use id.
    if(def.name==='sidebar.right.pane.tab')assert.equal(def.key,'satellite-thermal')
    registrations.push({def,body})
  }}}
  module.exports.apply(ctx)
  const panel=registrations.find(r=>r.def.name==='sidebar.right.pane.tab')
  assert.equal(panel.def.key,types[0].id)
  const html=renderToStaticMarkup(React.createElement(panel.body,{sessionId:'session/a',useInput:()=>({draft:''}),inputActions:{}}))
  assert.match(html,/sessionId=session%2Fa/)
  assert.doesNotMatch(html,/position:fixed/)
  const dock=registrations.find(r=>r.def.name==='conversation.input.dock')
  dock.def.inject().openWorkbench()
  assert.deepEqual(opened,[types[0].kind])
})
