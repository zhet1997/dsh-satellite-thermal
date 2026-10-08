"""Normalize solver-reported heat balances without labelling all sinks radiation."""
import math
from pathlib import Path
import re

def solver_balance(directory, input_power):
    directory=Path(directory)
    candidates=[directory/"nx"/"MSGF"]+list((directory/"nx").glob("*.log"))
    for path in candidates:
        if not path.is_file():
            continue
        text=path.read_text(encoding="utf-8",errors="replace")
        labels={"heatFlowIntoSinksW":"Heat flow into sinks","heatFlowFromNonFluidSinksW":"Heat flow from non-fluid sinks","heatLoadIntoElementsW":"Heat load into elements","heatLoadIntoSinksW":"Heat load into sinks","heatFlowFromFluidSinksW":"Heat flow from fluid sinks","residualW":"Deviation from heat balance"}
        values={}
        for key,label in labels.items():
            matches=re.findall(re.escape(label)+r"\s*=\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)",text)
            if matches:
                value=float(matches[-1])*1e-6
                if math.isfinite(value):
                    values[key]=value
        if "residualW" in values and "heatFlowIntoSinksW" in values:
            incoming=sum(values.get(k,0) for k in ("heatFlowFromNonFluidSinksW","heatLoadIntoElementsW","heatLoadIntoSinksW","heatFlowFromFluidSinksW"))
            scale=max(abs(incoming),abs(values["heatFlowIntoSinksW"]))
            return dict(values,status="available",sourceArtifact=path.name,solverReportedUnit="microW",inputComponentPowerW=input_power,absoluteResidualW=abs(values["residualW"]),relativeResidual=abs(values["residualW"])/scale if scale>0 else None,normalizationPowerW=scale,normalization="max(abs(reported incoming heat flows and loads), abs(reported heat flow into sinks)); no sink type inferred")
    return {"status":"unavailable","reason":"Native solver heat-balance quantities not found","inputComponentPowerW":input_power}
