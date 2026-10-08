import { Activity } from 'lucide-react';
import { energyBalancePresentation } from '../resultPresentation';
import { Badge, Notice, numberLabel } from './Common';

export function EnergyBalance({value}:{value?:Record<string,unknown>}) {
  const summary=energyBalancePresentation(value);
  return <section className="energy-balance"><div className="energy-heading"><h4><Activity size={16}/>能量平衡</h4><Badge tone={summary.available?'teal':'amber'}>{summary.label}</Badge></div><div className="energy-metrics">{summary.metrics.map(metric=><div className={`energy-metric ${metric.key}`} key={metric.key}><span>{metric.label}</span><strong>{metric.value===undefined?'—':numberLabel(metric.value,Math.abs(metric.value)<.001?8:5)}<small>{metric.unit}</small></strong><p>{metric.detail}</p></div>)}</div><Notice tone={summary.available?'info':'warning'}>{summary.explanation}</Notice>{value&&<details className="evidence-details"><summary>查看原始能量平衡证据</summary><pre className="json-view short">{JSON.stringify(value,null,2)}</pre></details>}</section>;
}
