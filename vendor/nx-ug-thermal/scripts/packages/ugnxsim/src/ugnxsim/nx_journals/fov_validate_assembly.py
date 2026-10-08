from __future__ import print_function

import json
import os
import traceback

import NXOpen
import NXOpen.Assemblies


def point_values(point):
    return [round(point.X, 6), round(point.Y, 6), round(point.Z, 6)]


def matrix_values(matrix):
    return [
        [round(matrix.Xx, 6), round(matrix.Xy, 6), round(matrix.Xz, 6)],
        [round(matrix.Yx, 6), round(matrix.Yy, 6), round(matrix.Yz, 6)],
        [round(matrix.Zx, 6), round(matrix.Zy, 6), round(matrix.Zz, 6)],
    ]


def close_values(actual, expected, tolerance=1.0e-5):
    if isinstance(expected[0], list):
        return all(
            abs(actual[row][column] - expected[row][column]) <= tolerance
            for row in range(3)
            for column in range(3)
        )
    return all(abs(actual[index] - expected[index]) <= tolerance for index in range(3))


def main():
    task_path = os.environ["UGNXSIM_TASK"]
    with open(task_path, "r", encoding="utf-8-sig") as stream:
        task = json.load(stream)
    result = {"ok": False}
    try:
        expected = dict((spec["nx_component_name"], spec) for spec in task["fov_specs"])
        session = NXOpen.Session.GetSession()
        _, load_status = session.Parts.OpenBaseDisplay(task["final_assembly"])
        load_status.Dispose()
        work_part = session.Parts.Work
        children = work_part.ComponentAssembly.RootComponent.GetChildren()
        rows = []
        for component in children:
            if component.Name not in expected:
                continue
            position, orientation = component.GetPosition()
            origin = point_values(position)
            matrix = matrix_values(orientation)
            spec = expected[component.Name]
            rows.append(
                {
                    "name": component.Name,
                    "origin_mm": origin,
                    "orientation_rows": matrix,
                    "origin_matches": close_values(origin, spec["origin_mm"]),
                    "orientation_matches": close_values(matrix, spec["orientation_rows"]),
                }
            )
        passed = len(rows) == len(expected) and all(
            row["origin_matches"] and row["orientation_matches"] for row in rows
        )
        result = {
            "ok": passed,
            "pass": passed,
            "top_level_component_count": len(children),
            "fov_components": rows,
        }
        with open(task["validation_path"], "w") as stream:
            json.dump(result, stream, indent=2, sort_keys=True)
        print(json.dumps(result, sort_keys=True))
    except Exception:
        result = {"ok": False, "pass": False, "error": traceback.format_exc()}
    with open(task["result_path"], "w") as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
    if not result["ok"]:
        raise RuntimeError(result.get("error", "NX FOV assembly validation failed"))


if __name__ == "__main__":
    main()
