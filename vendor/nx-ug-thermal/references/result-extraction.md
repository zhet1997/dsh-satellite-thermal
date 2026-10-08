# Steady thermal result extraction

The raw STEP workflow extracts in a new NX process after solve has succeeded. Never combine solve and extract into one run_journal.exe process or infer success only from a readable .sim.

## Native inputs and retained state

Extraction opens nx/thermal_case.sim, selects solution steady_state_thermal, and reads the newly generated .bun. For steady state, inspect iterations from newest to oldest and read only the final usable nodal-temperature dataset. Use NX12's array overload `ResultAccess.AskNodalResult(Int32[])` in bounded batches; do not issue one remote NXOpen call per node or reread every historical iteration. Accumulate component statistics during that same pass. Windows retains:

    nx/geometry_master.prt
    nx/case.fem
    nx/thermal_case.sim
    nx/thermal_case-steady_state_thermal.bun

Raw node and element tables remain under nx/raw_results/ for evidence and troubleshooting.

## Return contract

finalize permits exactly these five returned files:

| File | Required content |
|---|---|
| native.vtu | Non-empty VTK XML unstructured grid; point array T; temperature unit K |
| summary.json | Node/element counts, global temperature statistics, and per-component statistics |
| energy_balance.json | Input power and available residual fields; zero total input is represented as zero_input with no invalid division |
| run-manifest.json | Completed stage states and SHA-256 records for .prt/.fem/.sim/.bun |
| solver-log-summary.txt | Compact native solver completion/error summary |

The VTU writer uses the Python standard library only. Connectivity must reference existing nodes, temperature values must be finite, and an empty point/cell set is a failure.

Windows packages these files plus `return-manifest.json` as one return ZIP. Linux `nxcase pull` first returns the arguments for `windows-powershell.create_download`; after the Agent streams the signed GET response into the reported local path, a second `pull` verifies the ZIP member set, size, and SHA-256 before replacing local returned files. `nxcase publish` then atomically publishes all five under `02_sim/simulation/`.

For this demo, acceptance is an error-free steady solve plus the verified artifacts above. Keep legacy existing-case physical Gates, snapshot review, and presentation checks when those workflows are selected.
