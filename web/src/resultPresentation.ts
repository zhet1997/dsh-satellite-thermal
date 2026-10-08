export type TemperatureColorMap = {
  interpolation:'linear-rgb';
  stops:[number,[number,number,number]][];
};

export function validTemperatureRange(value:unknown):value is [number,number] {
  return Array.isArray(value) && value.length===2 && value.every(n=>typeof n==='number'&&Number.isFinite(n)&&n>=0) && value[1]>=value[0];
}

export const temperatureCelsius=(value:unknown)=>typeof value==='number'&&Number.isFinite(value)?value-273.15:undefined;

export function temperatureDisplay(range:unknown,coloredMeshes:number,totalMeshes:number) {
  const hasRange=validTemperatureRange(range),hasColors=coloredMeshes>0;
  return {
    showTemperature:hasRange&&hasColors,
    partial:hasRange&&hasColors&&coloredMeshes<totalMeshes,
    rangeC:hasRange?[range[0]-273.15,range[1]-273.15] as [number,number]:undefined,
    reason:!hasColors?'尚未回读逐节点温度颜色，当前只显示实际网格。':!hasRange?'未获得有效温度范围，当前顶点颜色没有可核查的温标。':''
  };
}

export function temperatureGradient(value:unknown,constant=false):string|undefined {
  if(!value||typeof value!=='object')return;
  const map=value as TemperatureColorMap;
  if(map.interpolation!=='linear-rgb'||!Array.isArray(map.stops)||map.stops.length<2)return;
  if(!map.stops.every((stop,i)=>Array.isArray(stop)&&stop.length===2&&Number.isFinite(stop[0])&&stop[0]>=0&&stop[0]<=1&&(!i||stop[0]>map.stops[i-1][0])&&Array.isArray(stop[1])&&stop[1].length===3&&stop[1].every(c=>typeof c==='number'&&Number.isFinite(c)&&c>=0&&c<=1)))return;
  if(map.stops[0][0]!==0||map.stops.at(-1)?.[0]!==1)return;
  const toSrgb=(channel:number)=>Math.round(255*(channel<=.0031308?12.92*channel:1.055*Math.pow(channel,1/2.4)-.055));
  const colorAt=(fraction:number)=>{
    const right=map.stops.findIndex(stop=>stop[0]>=fraction),upper=map.stops[Math.max(0,right)],lower=map.stops[Math.max(0,right-1)];
    const weight=upper[0]===lower[0]?0:(fraction-lower[0])/(upper[0]-lower[0]);
    return `rgb(${lower[1].map((c,i)=>toSrgb(c+(upper[1][i]-c)*weight)).join(', ')})`;
  };
  if(constant)return colorAt(0);
  // COLOR_0 values are linear RGB. CSS colors are sRGB, so use sufficiently
  // close converted samples rather than a misleading sRGB endpoint gradient.
  const fractions=[...new Set([...Array.from({length:33},(_,i)=>i/32),...map.stops.map(s=>s[0])])].sort((a,b)=>a-b);
  return `linear-gradient(to right, ${fractions.map(f=>`${colorAt(f)} ${f*100}%`).join(', ')})`;
}

export type EnergyMetric = {key:string;label:string;value?:number;unit:string;detail:string};
export function energyBalancePresentation(value?:Record<string,unknown>) {
  const finite=(key:string)=>typeof value?.[key]==='number'&&Number.isFinite(value[key])?value[key] as number:undefined;
  const input=finite('input_power_W'),relative=finite('relative_residual');
  const metrics:EnergyMetric[]=[
    {key:'input',label:'设备输入功耗',value:input,unit:'W',detail:'本次标准输入的功耗合计'},
    {key:'load',label:'求解器输入热流',value:finite('solver_heat_load_W'),unit:'W',detail:'实际施加到单元的热流'},
    {key:'loss',label:'流入热沉的热流',value:finite('radiation_heat_loss_W'),unit:'W',detail:'求解器回报的热沉收支'},
    {key:'residual',label:'收支偏差',value:finite('residual_W')??finite('absolute_residual_W'),unit:'W',detail:'保留求解器报告的正负号'},
    {key:'relative',label:'相对偏差',value:input===0?undefined:relative===undefined?undefined:relative*100,unit:'%',detail:input===0?'设备输入功耗为零，相对偏差不适用':'以设备输入功耗为基准'}
  ];
  const reasons:Record<string,string>={
    'solver heat-balance report is missing':'求解器尚未交付热平衡报告。',
    'solver heat-balance values were not found':'已读取求解器报告，但没有找到完整的热收支数值。'
  };
  const sourceReason=typeof value?.reason==='string'?value.reason:'';
  const hasSolverBalance=['load','loss','residual'].every(key=>metrics.some(m=>m.key===key&&m.value!==undefined));
  const available=!!value&&value.status!=='unavailable'&&hasSolverBalance;
  const label=!value?'尚未回读':available?(input===0?'零设备功耗工况':'已回读收支'):'数据不完整';
  const explanation=!value?'执行端尚未提供能量平衡数据。':reasons[sourceReason]||sourceReason||(!hasSolverBalance?'尚未获得完整的求解器热收支。':input===0?'此工况设备输入功耗为零；绝对热收支仍可查看，相对偏差不适用。':'这些是求解器实际报告的热收支；是否满足要求需结合项目允许的偏差。');
  return {metrics,available,label,explanation};
}

export function temperatureCheck(minK:number,maxK:number,lower?:number|null,upper?:number|null){
  const valid=Number.isFinite(minK)&&Number.isFinite(maxK)&&minK<=maxK;
  const hasLower=typeof lower==='number'&&Number.isFinite(lower),hasUpper=typeof upper==='number'&&Number.isFinite(upper);
  const low=valid&&hasLower&&minK<lower!,high=valid&&hasUpper&&maxK>upper!;
  return {valid,low,high,hasLimit:hasLower||hasUpper,lowMargin:low?lower!-minK:0,highMargin:high?maxK-upper!:0,
    label:!valid?'温度未完整回读':!hasLower&&!hasUpper?'未设置温限':low&&high?'低温 / 高温越限':low?'低于工作下限':high?'高于工作上限':hasLower&&hasUpper?'温限内':'已设单侧温限内'};
}
