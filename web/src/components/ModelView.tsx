import { componentColor } from '../componentColors';
import { useEffect, useRef, useState } from 'react';
import { Box, Focus, Grid2X2, MousePointer2 } from 'lucide-react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { artifactUrl } from '../api';
import type { Component, Selection } from '../types';
import { temperatureDisplay, temperatureGradient, type TemperatureColorMap } from '../resultPresentation';
import { Badge, numberLabel } from './Common';

type Props={url?:string;components:Component[];selection:Selection;relatedSelections?:Selection[];onSelect?:(s:Selection)=>void;source?:'step'|'nx'|'example';compact?:boolean;temperatureResult?:boolean;temperatureRangeK?:[number,number];temperatureColorMap?:TemperatureColorMap};
type DisplayMaterial=THREE.MeshStandardMaterial|THREE.MeshBasicMaterial;
type MeshRecord={mesh:THREE.Mesh;componentId?:string;faceId?:string;material:DisplayMaterial;vertexColored:boolean};
export function ModelView({url,components,selection,relatedSelections=[],onSelect,source='step',compact=false,temperatureResult=false,temperatureRangeK,temperatureColorMap}:Props) {
  const container=useRef<HTMLDivElement>(null),selected=useRef<Selection[]>([selection]),pick=useRef(onSelect),fit=useRef<()=>void>(()=>{}),focusSelected=useRef<()=>void>(()=>{}),materials=useRef<MeshRecord[]>([]);
  const [state,setState]=useState<'loading'|'ready'|'missing'|'error'>(url?'loading':'missing'),[error,setError]=useState(''),[wire,setWire]=useState(false),[colorCounts,setColorCounts]=useState({colored:0,total:0});
  selected.current=[selection,...relatedSelections];pick.current=onSelect;
  useEffect(()=>{
    if(!container.current)return;
    setColorCounts({colored:0,total:0});
    if(!url){setState('missing');return;}
    setState('loading');setError('');
    const element=container.current;let disposed=false,frame=0;let renderer:THREE.WebGLRenderer;
    try{renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});}catch{setState('error');setError('浏览器无法启用三维显示，仍可使用拓扑图和参数表。');return;}
    renderer.setPixelRatio(Math.min(window.devicePixelRatio,2));renderer.setClearColor(0xf2f6f7,1);renderer.outputColorSpace=THREE.SRGBColorSpace;element.appendChild(renderer.domElement);
    const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(38,1,0.1,100000);
    camera.up.set(0,0,1);camera.position.set(650,-650,500);
    const controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=true;controls.dampingFactor=.08;
    scene.add(new THREE.AmbientLight(0xffffff,2));
    const sun=new THREE.DirectionalLight(0xffffff,3);sun.position.set(700,-500,1000);scene.add(sun);
    const fill=new THREE.DirectionalLight(0xbcdce6,2);fill.position.set(-500,500,300);scene.add(fill);
    const grid=new THREE.GridHelper(1000,20,0xcbd7da,0xe0e7e9);grid.rotation.x=Math.PI/2;grid.position.z=-1;scene.add(grid);
    const axis=new THREE.AxesHelper(80);scene.add(axis);
    const group=new THREE.Group();scene.add(group);materials.current=[];
    const selectedBox=new THREE.Box3(),outline=new THREE.Box3Helper(selectedBox,0x455a61);outline.visible=false;scene.add(outline);
    const size=()=>{const w=element.clientWidth||300,h=element.clientHeight||300;renderer.setSize(w,h);camera.aspect=w/h;camera.updateProjectionMatrix();};
    const observer=new ResizeObserver(size);observer.observe(element);size();
    fit.current=()=>{if(!group.children.length)return;const bounds=new THREE.Box3().setFromObject(group),center=bounds.getCenter(new THREE.Vector3()),extent=bounds.getSize(new THREE.Vector3()),radius=Math.max(extent.x,extent.y,extent.z,1);camera.near=radius/1000;camera.far=radius*100;camera.position.copy(center).add(new THREE.Vector3(1.3,-1.6,1.2).multiplyScalar(radius));controls.target.copy(center);camera.updateProjectionMatrix();grid.scale.setScalar(radius*2/1000);grid.position.set(center.x,center.y,bounds.min.z-radius*.03);axis.position.set(bounds.min.x-radius*.1,bounds.min.y-radius*.1,bounds.min.z);axis.scale.setScalar(radius*.18/80);controls.update();};
    const isSelected=(m:MeshRecord)=>selected.current.some(s=>!!s.componentId&&m.componentId===s.componentId&&(!s.faceId||!m.faceId||m.faceId===s.faceId));
    const updateOutline=()=>{selectedBox.makeEmpty();const chosen=materials.current.filter(m=>m.vertexColored&&isSelected(m));chosen.forEach(m=>selectedBox.union(new THREE.Box3().setFromObject(m.mesh)));outline.visible=chosen.length>0;};
    focusSelected.current=()=>{updateOutline();const chosen=materials.current.filter(isSelected);if(!chosen.length)return;const bounds=new THREE.Box3();chosen.forEach(m=>bounds.union(new THREE.Box3().setFromObject(m.mesh)));const center=bounds.getCenter(new THREE.Vector3()),delta=center.clone().sub(controls.target);controls.target.copy(center);camera.position.add(delta);controls.update();};
    new GLTFLoader().load(artifactUrl(url),gltf=>{
      if(disposed){gltf.scene.traverse(obj=>{if(obj instanceof THREE.Mesh){obj.geometry.dispose();const mats=Array.isArray(obj.material)?obj.material:[obj.material];mats.forEach(m=>m.dispose());}});return;}
      group.add(gltf.scene);let colored=0,total=0;
      gltf.scene.traverse(obj=>{
        if(!(obj instanceof THREE.Mesh))return;total++;
        let cursor:THREE.Object3D|null=obj,componentId:string|undefined,faceId:string|undefined;
        while(cursor){const data=cursor.userData;componentId ||= data.componentId||data.component_id||data.partId;faceId ||= data.faceId||data.face_id;const match=components.find(c=>c.id===cursor?.name||c.name===cursor?.name);if(!componentId&&match)componentId=match.id;cursor=cursor.parent;}
        const index=Math.max(components.findIndex(c=>c.id===componentId),0),fallback=['#63929d','#c6b887','#7a9faa','#93bcb2','#8797ad','#b0b8be'][index%6];
        const colors=obj.geometry.getAttribute('color'),positions=obj.geometry.getAttribute('position');
        const vertexColored=source==='nx'&&!!colors&&colors.itemSize>=3&&colors.count>0&&colors.count===positions?.count;
        // Unlit vertex colors preserve the execution-side temperature palette.
        // Selection uses an outline rather than tinting a numerical heat map.
        const material:DisplayMaterial=vertexColored?new THREE.MeshBasicMaterial({color:0xffffff,vertexColors:true,side:THREE.DoubleSide}):new THREE.MeshStandardMaterial({color:componentId?componentColor(componentId):fallback,metalness:.28,roughness:.6,side:THREE.DoubleSide});
        if(vertexColored)colored++;
        obj.material=material;materials.current.push({mesh:obj,componentId,faceId,material,vertexColored});
      });
      setColorCounts({colored,total});fit.current();setState('ready');
    },undefined,()=>{if(!disposed){setState('error');setError('三维预览暂时无法读取，请检查几何预览文件或重新导入。');}});
    let down={x:0,y:0};
    const pointerDown=(e:PointerEvent)=>{down={x:e.clientX,y:e.clientY};};
    const pointerUp=(e:PointerEvent)=>{if(Math.hypot(e.clientX-down.x,e.clientY-down.y)>6)return;const rect=renderer.domElement.getBoundingClientRect(),pointer=new THREE.Vector2((e.clientX-rect.left)/rect.width*2-1,-(e.clientY-rect.top)/rect.height*2+1),ray=new THREE.Raycaster();ray.setFromCamera(pointer,camera);const hit=ray.intersectObjects(group.children,true)[0];if(hit){const found=materials.current.find(m=>m.mesh===hit.object);if(found?.componentId)pick.current?.({componentId:found.componentId});}};
    renderer.domElement.addEventListener('pointerdown',pointerDown);renderer.domElement.addEventListener('pointerup',pointerUp);
    let selectionKey='';
    const animate=()=>{frame=requestAnimationFrame(animate);controls.update();const key=JSON.stringify(selected.current);if(key!==selectionKey){updateOutline();selectionKey=key;}materials.current.forEach(m=>{if(m.material instanceof THREE.MeshStandardMaterial){const chosen=isSelected(m);m.material.emissive.set(chosen?0x166b59:0x000000);m.material.emissiveIntensity=chosen?.4:0;}});renderer.render(scene,camera);};animate();
    return()=>{disposed=true;cancelAnimationFrame(frame);observer.disconnect();controls.dispose();renderer.domElement.removeEventListener('pointerdown',pointerDown);renderer.domElement.removeEventListener('pointerup',pointerUp);scene.traverse(obj=>{if(obj instanceof THREE.Mesh||obj instanceof THREE.LineSegments){obj.geometry.dispose();const mats=Array.isArray(obj.material)?obj.material:[obj.material];mats.forEach(m=>m.dispose());}});renderer.dispose();renderer.domElement.remove();materials.current=[];};
  },[url,source,components.map(c=>`${c.id}:${c.name}`).join('|')]);
  useEffect(()=>{materials.current.forEach(m=>{m.material.wireframe=wire;});},[wire,state]);
  useEffect(()=>{if(state==='ready')focusSelected.current();},[selection.componentId,selection.faceId,relatedSelections.map(s=>`${s.componentId}:${s.faceId}`).join('|'),state]);
  const temperature=temperatureDisplay(temperatureRangeK,colorCounts.colored,colorCounts.total),showTemperature=temperatureResult&&state==='ready'&&temperature.showTemperature;
  const gradient=temperatureGradient(temperatureColorMap,temperature.rangeC?.[0]===temperature.rangeC?.[1]);
  const viewLabel=source==='nx'?(showTemperature?(temperature.partial?'NX 温度着色 · 部分网格':'NX 温度云图'):'NX 实际网格'):source==='example'?'体验示例几何':'源几何预览';
  return <div className={`model-view ${compact?'compact':''} ${temperatureResult?'temperature-result':''}`}>
    <div className="viewer-toolbar"><Badge tone={source==='nx'?'teal':'neutral'}>{viewLabel}</Badge><div><button className={`icon-button ${wire?'selected':''}`} aria-label="显示网格线" title="显示预览三角网格线" onClick={()=>setWire(!wire)} disabled={state!=='ready'}><Grid2X2 size={16}/></button><button className="icon-button" aria-label="适合窗口" title="适合窗口" onClick={()=>fit.current()} disabled={state!=='ready'}><Focus size={17}/></button></div></div>
    <div className="three-container" ref={container}/>
    {state!=='ready'&&<div className="model-overlay"><Box size={34} strokeWidth={1.3}/><strong>{state==='loading'?'正在载入三维模型':state==='error'?'三维预览暂不可用':source==='nx'?'尚未收到 NX 模型':'等待几何预览'}</strong><p>{state==='loading'?'正在读取实际几何数据…':state==='error'?error:source==='nx'?'执行端回传后，将在这里展示实际生成的网格。':'导入带三维预览的几何，或启用服务端几何转换后即可查看。'}</p></div>}
    {showTemperature&&temperature.rangeC&&<div className="temperature-legend" aria-label="实际节点温度范围"><div><strong>节点温度</strong><span>°C</span></div>{gradient?<div className="temperature-scale" style={{background:gradient}}/>:<small className="legend-unavailable">执行端未提供色带定义</small>}<div className="temperature-extremes"><span>{numberLabel(temperature.rangeC[0])}<small>最低</small></span><span>{numberLabel(temperature.rangeC[1])}<small>最高</small></span></div>{temperature.rangeC[0]===temperature.rangeC[1]&&<small className="constant-temperature">全场温度一致</small>}</div>}
    <div className="viewer-bottom"><span><MousePointer2 size={12}/>旋转 · 滚轮缩放 · 点击选择</span><span>Z ↑ · mm</span></div>
    {temperatureResult&&state==='ready'&&<div className={`viewer-source temperature-note ${showTemperature?'':'unavailable'}`}>{showTemperature?(temperature.partial?'部分网格未包含温度颜色，温标仅对应已着色区域。':'真实节点温度着色 · 温标来自本次 NX 结果'):temperature.reason}</div>}
    {source!=='nx'&&<div className="viewer-source">{source==='example'?'示例形状，仅用于操作体验':'源几何的三角化预览，非 NX 有限元网格'}</div>}
  </div>;
}
