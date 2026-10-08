from __future__ import print_function

import json
import os
import traceback

import NXOpen
import NXOpen.Assemblies


def main():
    task_path = os.environ["UGNXSIM_TASK"]
    with open(task_path, "r", encoding="utf-8-sig") as stream:
        task = json.load(stream)
    result = {"ok": False}
    try:
        expected = set(spec["nx_component_name"] for spec in task["fov_specs"])
        session = NXOpen.Session.GetSession()
        _, load_status = session.Parts.OpenBaseDisplay(task["final_assembly"])
        load_status.Dispose()
        work_part = session.Parts.Work
        components = [
            component
            for component in work_part.ComponentAssembly.RootComponent.GetChildren()
            if component.Name in expected
        ]
        if len(components) != len(expected):
            raise RuntimeError("Not all configured FOV components were found")
        modification = session.DisplayManager.NewDisplayModification()
        try:
            modification.ApplyToAllFaces = True
            modification.NewTranslucency = int(task["translucency"])
            modification.Apply(components)
        finally:
            modification.Dispose()
        save_status = work_part.Save(
            NXOpen.BasePart.SaveComponents.TrueValue,
            NXOpen.BasePart.CloseAfterSave.FalseValue,
        )
        save_status.Dispose()
        result = {"ok": True, "styled_count": len(components), "translucency": int(task["translucency"])}
        print("UGNXSIM_FOV_TRANSLUCENCY={}".format(task["translucency"]))
    except Exception:
        result = {"ok": False, "error": traceback.format_exc()}
    with open(task["result_path"], "w") as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
    if not result["ok"]:
        raise RuntimeError(result["error"])


if __name__ == "__main__":
    main()
