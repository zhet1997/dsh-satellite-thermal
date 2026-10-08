from __future__ import print_function

import csv
import json
import math
import os
import re
import time
import traceback
import xml.etree.ElementTree as ET

import NXOpen
import NXOpen.CAE


VTK_TYPES = {
    "TET4": (4, 10),
    "CTETRA": (4, 10),
    "TET10": (10, 24),
    "CTETRA10": (10, 24),
    "HEX8": (8, 12),
    "CHEXA": (8, 12),
    "HEX20": (20, 25),
    "CHEXA20": (20, 25),
    "WEDGE6": (6, 13),
    "PENTA6": (6, 13),
    "CPENTA": (6, 13),
    "WEDGE15": (15, 26),
    "PENTA15": (15, 26),
    "PYRAMID5": (5, 14),
    "CPYRAM": (5, 14),
    "PYRAMID13": (13, 27),
    "TRI3": (3, 5),
    "CTRIA3": (3, 5),
    "TRI6": (6, 22),
    "CTRIA6": (6, 22),
    "QUAD4": (4, 9),
    "CQUAD4": (4, 9),
    "QUAD8": (8, 23),
    "CQUAD8": (8, 23),
}
RESULT_BATCH_SIZE = 8192


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
        if not any(token in lowered for token in ("temperature", "温度")) or any(
            token in lowered
            for token in ("gradient", "maximum", "minimum", "max ", "min ", "error", "梯度", "最大", "最小", "误差")
        ):
            continue
        priority = 0 if any(token in lowered for token in ("nodal", "节点")) else 1
        candidates.append((priority, index, str(name)))
    if candidates:
        _, index, name = sorted(candidates)[0]
        return result_types[index], name
    raise RuntimeError("temperature result type was not found: %s" % list(names))


def iteration_time(label, fallback):
    matches = re.findall(r"[+-]?\d+(?:\.\d*)?(?:[Ee][+-]?\d+)?", str(label))
    return float(matches[-1]) if matches else float(fallback)


def number_text(values):
    return " ".join("%.17g" % value if isinstance(value, float) else str(value) for value in values)


def data_array(parent, data_type, name, values, attributes=None):
    options = {"type": data_type, "format": "ascii"}
    if name is not None:
        options["Name"] = name
    if attributes:
        options.update(attributes)
    ET.SubElement(parent, "DataArray", options).text = number_text(values)


def write_vtu(nodes, elements, path):
    if not nodes or not elements:
        raise RuntimeError("VTU output requires non-empty nodes and elements")
    node_index = dict((int(row["node_id"]), index) for index, row in enumerate(nodes))
    if len(node_index) != len(nodes):
        raise RuntimeError("duplicate node id in extracted NX mesh")
    points = []
    temperatures = []
    for row in nodes:
        points.extend((float(row["x_mm"]), float(row["y_mm"]), float(row["z_mm"])))
        temperature = float(row["temperature_K"])
        if not math.isfinite(temperature) or temperature <= 0.0:
            raise RuntimeError("invalid Kelvin temperature at node %s" % row["node_id"])
        temperatures.append(temperature)
    connectivity = []
    offsets = []
    cell_types = []
    type_counts = {}
    for row in elements:
        raw_type = str(row.get("element_type") or ("HEX8" if row.get("n8") else "TET4")).upper()
        if raw_type not in VTK_TYPES:
            raise RuntimeError("unsupported VTK element type: %s" % raw_type)
        count, vtk_type = VTK_TYPES[raw_type]
        ids = [int(row["n%d" % index]) for index in range(1, count + 1)]
        missing = [value for value in ids if value not in node_index]
        if missing:
            raise RuntimeError("element %s references missing nodes %s" % (row.get("element_id"), missing))
        connectivity.extend(node_index[value] for value in ids)
        offsets.append(len(connectivity))
        cell_types.append(vtk_type)
        type_counts[raw_type] = type_counts.get(raw_type, 0) + 1

    root = ET.Element("VTKFile", {"type": "UnstructuredGrid", "version": "0.1", "byte_order": "LittleEndian"})
    grid = ET.SubElement(root, "UnstructuredGrid")
    piece = ET.SubElement(grid, "Piece", {"NumberOfPoints": str(len(nodes)), "NumberOfCells": str(len(elements))})
    point_data = ET.SubElement(piece, "PointData", {"Scalars": "T"})
    data_array(point_data, "Float64", "T", temperatures, {"Units": "K"})
    ET.SubElement(piece, "CellData")
    point_element = ET.SubElement(piece, "Points")
    data_array(point_element, "Float64", None, points, {"NumberOfComponents": "3", "Units": "mm"})
    cells = ET.SubElement(piece, "Cells")
    data_array(cells, "Int64", "connectivity", connectivity)
    data_array(cells, "Int64", "offsets", offsets)
    data_array(cells, "UInt8", "types", cell_types)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    if not os.path.isfile(path) or os.path.getsize(path) <= 0:
        raise RuntimeError("VTU writer produced no output")
    return type_counts


def parse_solver_value(text, label):
    match = re.search(re.escape(label) + r"\s*=\s*([+-]?\d+(?:\.\d*)?[Ee][+-]?\d+)", text)
    return float(match.group(1)) if match else None


def build_energy_balance(run_dir, thermal_case, solve_report):
    requested_power = float(thermal_case.get("total_power_W", 0.0))
    candidates = [os.path.join(run_dir, "nx", "MSGF")]
    solver_log = solve_report.get("solver_files", {}).get("log_path")
    if solver_log:
        candidates.append(solver_log)
    source = next((path for path in candidates if os.path.isfile(path)), None)
    base = {
        "schema_version": "nx-ug-thermal-energy-balance/1",
        "input_power_W": requested_power,
        "relative_residual": None,
    }
    if source is None:
        base.update({
            "status": "zero_input" if requested_power == 0.0 else "unavailable",
            "reason": "solver heat-balance report is missing",
        })
        return base
    with open(source, "r", errors="replace") as stream:
        text = stream.read()
    solver_load = parse_solver_value(text, "Heat load into elements")
    sink_flow = parse_solver_value(text, "Heat flow into sinks")
    deviation = parse_solver_value(text, "Deviation from heat balance")
    base["source"] = source
    if None in (solver_load, sink_flow, deviation):
        base.update({
            "status": "zero_input" if requested_power == 0.0 else "unavailable",
            "reason": "solver heat-balance values were not found",
        })
        return base
    scale = 1.0e-6
    residual = float(deviation) * scale
    base.update({
        "status": "zero_input" if requested_power == 0.0 else "available",
        "solver_heat_load_W": float(solver_load) * scale,
        "radiation_heat_loss_W": float(sink_flow) * scale,
        "residual_W": residual,
        "absolute_residual_W": abs(residual),
        "relative_residual": abs(residual) / requested_power if requested_power else None,
        "solver_reported_unit": "microW",
    })
    return base


def write_solver_log_summary(path, solve_report, extraction):
    solver_log = solve_report.get("solver_files", {}).get("log_path")
    selected = []
    if solver_log and os.path.isfile(solver_log):
        with open(solver_log, "r", errors="replace") as stream:
            for line in stream:
                lowered = line.lower()
                if any(token in lowered for token in ("end of run", "warning", "error", "heat load", "heat flow", "deviation")):
                    selected.append(line.rstrip())
    lines = [
        "NX/UG steady thermal solver summary",
        "solver_completed=%s" % bool(solve_report.get("solver_completed")),
        "solution=steady_state_thermal",
        "nodes=%d" % extraction["node_count"],
        "elements=%d" % extraction["element_count"],
        "temperature_unit=K",
        "solver_log=%s" % (solver_log or "unavailable"),
    ]
    if selected:
        lines.append("selected_solver_lines:")
        lines.extend(selected[-80:])
    else:
        lines.append("selected_solver_lines=unavailable")
    with open(path, "w") as stream:
        stream.write("\n".join(lines) + "\n")


def main():
    started = time.perf_counter()
    task = read_json(os.environ["NX_THERMAL_TASK"])
    result = {"ok": False}
    parameters = None
    access = None
    manager = None
    try:
        session = NXOpen.Session.GetSession()
        sim = open_base_display(session, task["sim_path"])
        session.Parts.SetWork(sim)
        solutions = list(sim.Simulation.Solutions)
        solution_name = task.get("solution_name", "steady_state_thermal")
        solution = next(item for item in solutions if item.Name == solution_name)
        manager = session.ResultManager
        solution_result = manager.CreateSolutionResult(solution)
        geometry_path = os.path.join(task["raw_results_dir"], "nodes_geometry.csv")
        nodes_path = os.path.join(task["raw_results_dir"], "nodes.csv")
        element_path = os.path.join(task["raw_results_dir"], "elements.csv")
        nodes = []
        with open(geometry_path, "r", newline="") as stream:
            for row in csv.DictReader(stream):
                row["node_id"] = int(row["node_id"])
                row["result_node_index"] = int(solution_result.AskNodeIndex(row["node_id"]))
                nodes.append(row)
        if not nodes:
            raise RuntimeError("NX mesh contains no nodes")
        with open(element_path, "r", newline="") as stream:
            elements = list(csv.DictReader(stream))
        if not elements:
            raise RuntimeError("NX mesh contains no elements")
        mesh_loaded_at = time.perf_counter()

        loadcases = list(solution_result.AskLoadcases())
        if not loadcases:
            raise RuntimeError("solution result contains no loadcases")
        loadcase = len(loadcases) - 1
        iteration_labels = list(solution_result.AskIterations(loadcase))
        if not iteration_labels:
            raise RuntimeError("solution result contains no iterations")
        final_temperatures = None
        final_iteration = None
        result_name = None
        skipped_iterations = []
        component_accumulators = None
        # A steady-state report only needs the last usable dataset. Reading every
        # solver iteration multiplies expensive NXOpen nodal calls without changing
        # native.vtu or summary.json.
        for iteration in reversed(range(len(iteration_labels))):
            label = iteration_labels[iteration]
            try:
                result_type, current_name = choose_temperature_result(solution_result, loadcase, iteration)
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
            values = []
            accumulators = {}
            try:
                for start in range(0, len(nodes), RESULT_BATCH_SIZE):
                    batch = nodes[start:start + RESULT_BATCH_SIZE]
                    indices = [row["result_node_index"] for row in batch]
                    temperatures = list(access.AskNodalResult(indices))
                    if len(temperatures) != len(batch):
                        raise RuntimeError(
                            "NX returned %d nodal results for a batch of %d" % (len(temperatures), len(batch))
                        )
                    for row, raw_temperature in zip(batch, temperatures):
                        temperature = float(raw_temperature)
                        if not math.isfinite(temperature) or temperature <= 0.0:
                            raise RuntimeError("invalid Kelvin temperature at node %s" % row["node_id"])
                        values.append(temperature)
                        row["temperature_K"] = temperature
                        business_id = row["business_id"]
                        stats = accumulators.setdefault(business_id, [0, temperature, temperature, 0.0])
                        stats[0] += 1
                        stats[1] = min(stats[1], temperature)
                        stats[2] = max(stats[2], temperature)
                        stats[3] += temperature
            finally:
                manager.DeleteResultAccess(access)
                access = None
                manager.DeleteResultParameters(parameters)
                parameters = None
            final_temperatures = values
            final_iteration = iteration
            result_name = current_name
            component_accumulators = accumulators
            break
        if not final_temperatures:
            raise RuntimeError("no usable nodal temperature dataset was found")
        results_read_at = time.perf_counter()
        with open(nodes_path, "w", newline="") as stream:
            fields = ["node_id", "x_mm", "y_mm", "z_mm", "temperature_K", "business_id"]
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for row in nodes:
                writer.writerow(dict((key, row[key]) for key in fields))

        output_dir = task["output_dir"]
        if not os.path.isdir(output_dir):
            os.makedirs(output_dir)
        vtu_path = os.path.join(output_dir, "native.vtu")
        type_counts = write_vtu(nodes, elements, vtu_path)
        component_stats = dict(
            (business_id, {
                "node_count": values[0],
                "min_K": values[1],
                "max_K": values[2],
                "mean_K": values[3] / values[0],
            })
            for business_id, values in sorted(component_accumulators.items())
        )
        vtu_written_at = time.perf_counter()
        solve_report = read_json(os.path.join(task["run_dir"], "nx", "solve_report.json"))
        extraction = {
            "node_count": len(nodes),
            "element_count": len(elements),
            "element_types": type_counts,
            "temperature_min_K": min(final_temperatures),
            "temperature_max_K": max(final_temperatures),
            "temperature_mean_K": sum(final_temperatures) / len(final_temperatures),
            "result_name": result_name,
            "loadcase": loadcase,
            "iteration": final_iteration,
            "iteration_count": len(iteration_labels),
            "iteration_labels": [str(value) for value in iteration_labels],
            "skipped_iterations": skipped_iterations,
            "timings_seconds": {
                "open_and_index_mesh": mesh_loaded_at - started,
                "read_final_temperature": results_read_at - mesh_loaded_at,
                "write_nodes_and_vtu": vtu_written_at - results_read_at,
                "total_before_reports": vtu_written_at - started,
            },
        }
        summary = {
            "schema_version": "nx-ug-thermal-summary/1",
            "analysis_type": "steady_state",
            "solution_name": solution_name,
            "temperature_unit": "K",
            "coordinate_unit": "mm",
            "point_data_array": "T",
            "node_count": extraction["node_count"],
            "element_count": extraction["element_count"],
            "element_types": extraction["element_types"],
            "temperature_min_K": extraction["temperature_min_K"],
            "temperature_max_K": extraction["temperature_max_K"],
            "temperature_mean_K": extraction["temperature_mean_K"],
            "component_temperatures_K": component_stats,
            "extraction_timings_seconds": extraction["timings_seconds"],
        }
        write_json(os.path.join(output_dir, "summary.json"), summary)
        thermal_case = read_json(task["thermal_case_path"])
        write_json(os.path.join(output_dir, "energy_balance.json"), build_energy_balance(task["run_dir"], thermal_case, solve_report))
        write_solver_log_summary(os.path.join(output_dir, "solver-log-summary.txt"), solve_report, extraction)
        result = {"ok": True, "results": extraction, "outputs": ["native.vtu", "summary.json", "energy_balance.json", "solver-log-summary.txt"]}
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
