from __future__ import print_function

import csv
import json
import os
import re
import traceback

import NXOpen
import NXOpen.CAE


def read_json(path):
    with open(path, "r", encoding="utf-8-sig") as stream:
        return json.load(stream)


def write_json(path, value):
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with open(path, "w") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)


def open_base_display(session, path):
    opened = session.Parts.OpenBaseDisplay(path)
    return opened[0] if isinstance(opened, tuple) else opened


def choose_temperature_result(result, loadcase, iteration):
    result_types, names = result.AskResultTypes(loadcase, iteration)
    candidates = []
    for index, name in enumerate(names):
        lowered = str(name).lower()
        if "temperature" not in lowered or any(token in lowered for token in ("gradient", "maximum", "minimum", "max ", "min ", "error")):
            continue
        priority = 0 if "nodal" in lowered else 1
        candidates.append((priority, index, str(name)))
    if candidates:
        _, index, name = sorted(candidates)[0]
        return result_types[index], name
    raise RuntimeError("temperature result type was not found: %s" % list(names))


def iteration_time(label, fallback):
    text = str(label)
    matches = re.findall(r"[+-]?\d+(?:\.\d*)?(?:[Ee][+-]?\d+)?", text)
    if matches:
        return float(matches[-1])
    return float(fallback)


def main():
    task = read_json(os.environ["UGNXSIM_TASK"])
    result = {"ok": False}
    solution_result = None
    parameters = None
    access = None
    try:
        session = NXOpen.Session.GetSession()
        sim = open_base_display(session, task["sim_path"])
        session.Parts.SetWork(sim)
        solutions = list(sim.Simulation.Solutions)
        solution = next(item for item in solutions if item.Name == task.get("solution_name", "steady_state_thermal"))
        manager = session.ResultManager
        solution_result = manager.CreateSolutionResult(solution)
        geometry_path = os.path.join(task["raw_results_dir"], "nodes_geometry.csv")
        output_path = os.path.join(task["raw_results_dir"], "nodes.csv")
        rows = []
        with open(geometry_path, "r", newline="") as stream:
            for row in csv.DictReader(stream):
                row["node_id"] = int(row["node_id"])
                row["result_node_index"] = int(solution_result.AskNodeIndex(row["node_id"]))
                rows.append(row)

        loadcases = list(solution_result.AskLoadcases())
        if not loadcases:
            raise RuntimeError("solution result contains no loadcases")
        loadcase = len(loadcases) - 1
        iteration_labels = list(solution_result.AskIterations(loadcase))
        if not iteration_labels:
            raise RuntimeError("solution result contains no iterations")
        history_path = os.path.join(task["raw_results_dir"], "temperature_history.csv")
        history_rows = []
        final_temperatures = []
        result_name = None
        skipped_iterations = []
        final_iteration = None
        for iteration, label in enumerate(iteration_labels):
            try:
                result_type, result_name = choose_temperature_result(solution_result, loadcase, iteration)
            except RuntimeError:
                skipped_iterations.append({"iteration": iteration, "label": str(label), "reason": "no nodal temperature result"})
                continue
            parameters = manager.CreateResultParameters()
            parameters.SetLoadcaseIteration(loadcase, iteration)
            parameters.SetResultType(result_type)
            parameters.SetResultDataLocation(NXOpen.CAE.Result.Location.Nodal)
            parameters.SetResultComponent(NXOpen.CAE.Result.Component.Scalar)
            parameters.SetUnit(sim.UnitCollection.FindObject("Kelvin"))
            try:
                access = manager.CreateResultAccess(solution_result, parameters)
            except NXOpen.NXException:
                skipped_iterations.append({"iteration": iteration, "label": str(label), "reason": "Dataset Not Found"})
                manager.DeleteResultParameters(parameters)
                parameters = None
                continue
            by_body = {}
            all_values = []
            try:
                for row in rows:
                    temperature = float(access.AskNodalResult(row["result_node_index"]))
                    all_values.append(temperature)
                    by_body.setdefault(row["business_id"], []).append(temperature)
                final_temperatures = all_values
                final_iteration = iteration
            finally:
                manager.DeleteResultAccess(access)
                access = None
                manager.DeleteResultParameters(parameters)
                parameters = None
            time_s = iteration_time(label, iteration)
            groups = [("__all__", all_values)] + sorted(by_body.items())
            for business_id, values in groups:
                history_rows.append(
                    {
                        "iteration_index": iteration,
                        "time_s": time_s,
                        "iteration_label": str(label),
                        "business_id": business_id,
                        "node_count": len(values),
                        "temperature_min_K": min(values),
                        "temperature_max_K": max(values),
                        "temperature_mean_K": sum(values) / len(values),
                    }
                )

        with open(history_path, "w", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=[
                    "iteration_index",
                    "time_s",
                    "iteration_label",
                    "business_id",
                    "node_count",
                    "temperature_min_K",
                    "temperature_max_K",
                    "temperature_mean_K",
                ],
            )
            writer.writeheader()
            writer.writerows(history_rows)
        for row, temperature in zip(rows, final_temperatures):
            row["temperature_K"] = temperature
        with open(output_path, "w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["node_id", "x_mm", "y_mm", "z_mm", "temperature_K", "business_id"])
            writer.writeheader()
            for row in rows:
                writer.writerow(dict((key, row[key]) for key in writer.fieldnames))

        if not final_temperatures:
            raise RuntimeError("no nodal temperature dataset was extracted")
        with open(os.path.join(task["raw_results_dir"], "elements.csv"), "r", newline="") as stream:
            element_count = sum(1 for _ in csv.DictReader(stream))
        result_summary = {
            "node_count": len(rows),
            "element_count": element_count,
            "temperature_min_K": min(final_temperatures),
            "temperature_max_K": max(final_temperatures),
            "temperature_mean_K": sum(final_temperatures) / len(final_temperatures),
            "result_name": result_name,
            "loadcase": loadcase,
            "iteration": final_iteration,
            "iteration_count": len(set(row["iteration_index"] for row in history_rows)),
            "iteration_labels": [str(value) for value in iteration_labels],
            "skipped_iterations": skipped_iterations,
            "history_path": history_path,
        }
        result = {"ok": True, "results": result_summary}
    except Exception:
        result = {"ok": False, "error": traceback.format_exc()}
    finally:
        try:
            if access is not None:
                manager.DeleteResultAccess(access)
            if parameters is not None:
                manager.DeleteResultParameters(parameters)
        except Exception:
            pass
    write_json(task["result_path"], result)


if __name__ == "__main__":
    main()
