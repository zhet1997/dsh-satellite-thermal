"""Run with run_journal.exe in a NEW directory; creates disposable probe parts only."""
from __future__ import print_function
import json
import os
import sys
import traceback

def main():
    root = os.environ["NX_THERMAL_PROBE_DIR"]
    source = os.environ["NX_THERMAL_VENDOR_JOURNALS"]
    sys.path.insert(0, source)
    result = {"ok": False, "session": False, "descriptors": {}, "mesh": {}}
    try:
        import NXOpen
        import NXOpen.CAE
        import build_and_solve as legacy
        session = NXOpen.Session.GetSession()
        result["session"] = True
        master = session.Parts.NewDisplay(os.path.join(root, "probe_geometry.prt"), NXOpen.Part.Units.Millimeters)
        master.Save(master.SaveComponents.FalseValue, master.CloseAfterSave.FalseValue)
        fem = legacy.create_fem(session, master, os.path.join(root, "probe.fem"), {"bodies": []})
        session.Parts.SetDisplay(fem, False, False)
        session.Parts.SetWork(fem)
        mesh = fem.BaseFEModel.MeshManager.CreateMesh2dBuilder(NXOpen.CAE.Mesh2d.Null)
        result["mesh"]["properties"] = table_info(mesh.PropertyTable)
        mesh.ElementType.ElementDimension = NXOpen.CAE.ElementTypeBuilder.ElementType.Shell
        result["mesh"]["elementTypes"] = list(mesh.ElementType.GetElementTypeNames())
        mesh.Destroy()
        for descriptor in ("Shell", "ThinShell", "Solid"):
            try:
                builder = fem.BaseFEModel.MeshManager.CreateCollectorBuilder(NXOpen.CAE.MeshCollector.Null, descriptor)
                result["mesh"][descriptor] = table_info(builder.PropertyTable)
                builder.Destroy()
            except Exception:
                result["mesh"][descriptor] = {"error": traceback.format_exc()}
        sim = legacy.create_sim(session, fem, os.path.join(root, "probe.sim"))
        session.Parts.SetWork(sim)
        for kind, descriptor in (("SimulationObject", "Radiation"), ("SimulationObject", "Radiation Thermal Coupling"), ("SimulationObject", "Contact Thermal Coupling"), ("Constraint", "Temperature"), ("Constraint", "Simple Environment Radiation"), ("Load", "Heat Generation"), ("Load", "Heat Flux")):
            try:
                method = getattr(sim.Simulation, "CreateBcBuilderFor" + kind + "Descriptor")
                builder = method(descriptor, "PROBE")
                result["descriptors"][descriptor] = {"properties": table_info(builder.PropertyTable), "targetSets": builder.TargetSetManager.TargetSetCount}
                builder.Destroy()
            except Exception:
                result["descriptors"][descriptor] = {"error": traceback.format_exc()}
        result["ok"] = True
    except Exception:
        result["error"] = traceback.format_exc()
    with open(os.path.join(root, "probe.json"), "w") as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(result))

def table_info(table, depth=0):
    rows = []
    for index in range(table.GetPropertyCount()):
        name = table.GetPropertyNameByIndex(index)
        row = {"name": name, "type": str(table.GetBasePropertyType(name))}
        for method in ("GetDoublePropertyValue", "GetIntegerPropertyValue", "GetBooleanPropertyValue", "GetBaseScalarWithDataPropertyValue", "GetScalarWithDataPropertyValue"):
            try:
                value = getattr(table, method)(name)
                if isinstance(value, tuple):
                    value = [x if isinstance(x,(int,float,bool)) else getattr(x,"Name",type(x).__name__) if x is not None else None for x in value]
                row[method] = value
            except Exception:
                pass
        if depth < 2 and hasattr(table,"GetNamedPropertyTablePropertyValue"):
            try:
                named=table.GetNamedPropertyTablePropertyValue(name)
                if named is not None:
                    row["referencedTable"]={"name":named.Name,"properties":table_info(named.PropertyTable,depth+1)}
            except Exception:
                pass
        rows.append(row)
    return rows

if __name__ == "__main__":
    main()
