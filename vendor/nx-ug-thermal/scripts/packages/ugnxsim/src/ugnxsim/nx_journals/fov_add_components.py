from __future__ import print_function

import json
import os
import shutil
import traceback

import NXOpen
import NXOpen.Assemblies


def nx_matrix(rows):
    matrix = NXOpen.Matrix3x3()
    matrix.Xx, matrix.Xy, matrix.Xz = rows[0]
    matrix.Yx, matrix.Yy, matrix.Yz = rows[1]
    matrix.Zx, matrix.Zy, matrix.Zz = rows[2]
    return matrix


def main():
    task_path = os.environ["UGNXSIM_TASK"]
    with open(task_path, "r", encoding="utf-8-sig") as stream:
        task = json.load(stream)
    result = {"ok": False}
    try:
        if os.path.exists(task["final_assembly"]):
            raise RuntimeError("Refusing to overwrite final assembly: " + task["final_assembly"])
        shutil.copy2(task["source_top"], task["final_assembly"])
        session = NXOpen.Session.GetSession()
        _, load_status = session.Parts.OpenBaseDisplay(task["final_assembly"])
        load_status.Dispose()
        work_part = session.Parts.Work
        assembly = work_part.ComponentAssembly
        existing = set(component.Name for component in assembly.RootComponent.GetChildren())
        added = []
        for spec in task["fov_specs"]:
            name = spec["nx_component_name"]
            part_path = os.path.join(task["fov_parts_dir"], spec["nx_part_file"])
            if name in existing:
                raise RuntimeError("FOV component already exists: " + name)
            if not os.path.isfile(part_path):
                raise RuntimeError("FOV part is missing: " + part_path)
            component, status = assembly.AddComponent(
                part_path,
                "Entire Part",
                name,
                NXOpen.Point3d(*spec["origin_mm"]),
                nx_matrix(spec["orientation_rows"]),
                -1,
            )
            status.Dispose()
            added.append(component.Name)
        save_status = work_part.Save(
            NXOpen.BasePart.SaveComponents.TrueValue,
            NXOpen.BasePart.CloseAfterSave.FalseValue,
        )
        save_status.Dispose()
        result = {"ok": True, "added_count": len(added), "component_names": added}
        print("UGNXSIM_FOV_COMPONENTS_ADDED={}".format(len(added)))
    except Exception:
        result = {"ok": False, "error": traceback.format_exc()}
    with open(task["result_path"], "w") as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
    if not result["ok"]:
        raise RuntimeError(result["error"])


if __name__ == "__main__":
    main()
