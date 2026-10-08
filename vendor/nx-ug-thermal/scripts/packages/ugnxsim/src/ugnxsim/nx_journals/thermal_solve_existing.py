from __future__ import print_function

import json
import os
import time
import traceback

import NXOpen
import NXOpen.CAE


def read_json(path):
    with open(path, "r", encoding="utf-8-sig") as stream:
        return json.load(stream)


def write_json(path, value):
    with open(path, "w") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)


def open_base_display(session, path):
    opened = session.Parts.OpenBaseDisplay(path)
    return opened[0] if isinstance(opened, tuple) else opened


def remove_stale_outputs(sim_path, solution_name):
    directory = os.path.dirname(sim_path)
    prefix = (os.path.splitext(os.path.basename(sim_path))[0] + "-" + solution_name).lower()
    removed = []
    for name in os.listdir(directory):
        path = os.path.join(directory, name)
        if name.lower().startswith(prefix) and os.path.isfile(path):
            os.remove(path)
            removed.append(path)
    return removed


def wait_for_solver(sim_path, solution_name, timeout_seconds):
    directory = os.path.dirname(sim_path)
    prefix = os.path.splitext(os.path.basename(sim_path))[0] + "-" + solution_name
    log_path = os.path.join(directory, prefix + ".log")
    result_path = os.path.join(directory, prefix + ".bun")
    deadline = time.time() + float(timeout_seconds)
    while time.time() < deadline:
        complete = False
        if os.path.isfile(log_path):
            with open(log_path, "r", errors="replace") as stream:
                complete = "End of run" in stream.read()
        if complete and os.path.isfile(result_path) and os.path.getsize(result_path) > 0:
            return {"log_path": log_path, "result_path": result_path, "end_of_run": True}
        time.sleep(5.0)
    raise RuntimeError("timed out waiting for SST completion: " + prefix)


def main():
    task = read_json(os.environ["UGNXSIM_TASK"])
    result = {"ok": False}
    try:
        session = NXOpen.Session.GetSession()
        sim = open_base_display(session, task["sim_path"])
        session.Parts.SetWork(sim)
        solutions = list(sim.Simulation.Solutions)
        if not solutions:
            raise RuntimeError("SIM contains no solutions")
        requested = task.get("solution_name")
        solution = next((item for item in solutions if not requested or item.Name == requested), None)
        if solution is None:
            raise RuntimeError("solution not found: " + str(requested))
        removed = remove_stale_outputs(task["sim_path"], solution.Name)
        setup_status = solution.Solve(
            NXOpen.CAE.SimSolution.SolveOption.WriteSolverInputFile,
            NXOpen.CAE.SimSolution.SetupCheckOption.CompleteCheckAndOutputErrors,
        )
        solve_status = solution.Solve(
            NXOpen.CAE.SimSolution.SolveOption.Solve,
            NXOpen.CAE.SimSolution.SetupCheckOption.CompleteCheckAndOutputErrors,
        )
        files = wait_for_solver(task["sim_path"], solution.Name, task.get("solver_timeout_seconds", 7200))
        sim.Save(sim.SaveComponents.TrueValue, sim.CloseAfterSave.FalseValue)
        result = {
            "ok": True,
            "solution_name": solution.Name,
            "setup_status": str(setup_status),
            "solve_status": str(solve_status),
            "removed_stale_outputs": removed,
            "native_results": files,
        }
        print("UGNXSIM_THERMAL_SOLVE_OK=1")
    except Exception:
        result = {"ok": False, "error": traceback.format_exc()}
    write_json(task["result_path"], result)
    if not result["ok"]:
        raise RuntimeError(result["error"])


if __name__ == "__main__":
    main()
