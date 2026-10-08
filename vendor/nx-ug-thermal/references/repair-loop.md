# Thermal repair loop

This repair workflow remains available for explicit repair tasks and retained existing-case workflows. It does not authorize automatic repair of a failed raw STEP demo run. For that path, inspect only recorded stage state, logs, and PID; preserve evidence and stop on failed, timed_out, or dead-PID state.

Read this reference when an environment probe, setup stage, native solve, extraction, validation, or snapshot fails.

## Find the first causal failure

```text
environment/license
-> NX launch
-> source model
-> mesh
-> materials/loads/BCs
-> thermal solve
-> result extraction
-> machine validation
-> snapshot review
```

Later errors may be consequences. Do not alter geometry, mesh controls, physics, loads, or acceptance criteria to work around an unproven environment failure.

## Loop

1. Stop after the first failed command; do not silently retry.
2. Read `logs/<stage>.log`, the relevant NXOpen/native log, and `reports/checkpoints.json`.
3. State one failure hypothesis and the evidence expected to change.
4. Run the smallest read-only or stage-specific probe that can test it.
5. Change the smallest responsible case source or shared runtime section.
6. Create a new run and replay required predecessor stages.
7. Rerun every downstream Gate and snapshot affected by the change.
8. Record remaining risk, deliberate simplifications, and unsupported claims.

Never edit a failed run into a passing run.

## Common failure classes

- Environment/runtime: verify the configured `run_journal.exe`, NX version, ASCII work path, and thermal dependencies with `nxcase doctor --domain thermal`.
- Source state: inspect actual PRT/FEM/SIM paths, solution name, semantic body/face IDs, and relative references; do not guess identifiers from another case.
- Mesh: inspect the first rejected body/element and repair the responsible case setup before rerunning model and mesh in a new run.
- Materials/conditions: compare the brief, case dictionary, semantic IDs, units, and setup report. Change physical input only after resolving it from the user or an authoritative source.
- Native solve: inspect the solver log first. Treat a new BUN and `End of run` as authoritative; do not weaken the Gate for a timeout or incomplete solve.
- Extraction/validation: verify the BUN contains the requested nodal temperature and iterations. A physical threshold failure is a result, not automatically a parser bug.
- Snapshot: decide whether the issue is presentation-only or reveals bad data. Repair framing only for presentation problems; otherwise return to the first responsible setup/result stage.

## Known v0009 Windows failure signatures

- A preflight worker that exits before writing its first state can be PowerShell 5.1 rejecting an empty mandatory array. Keep `[AllowEmptyCollection()]` on the stage artifact parameter.
- Mojibake in a Chinese STEP filename means JSON was read with the PowerShell default encoding. Read JSON with `[IO.File]::ReadAllText(..., [Text.Encoding]::UTF8)`, write BOM-free UTF-8, and replace state files with `Move-Item -Force`.
- A missing `run-root\cad\geometry_manifest.json` is a task-contract bug. The model task must pass `package\cad\geometry_manifest.json` explicitly as `geometry_manifest_path`; journals must not infer package paths from `run_dir`.
- An empty or unavailable `ExitCode` can hide the useful journal result on PowerShell 5.1. Refresh the process, parse the required journal result first, and treat the exit code as a failure only when it is non-null and nonzero.
- `STEP translator produced no NX part` together with `No parts in current input file` is a source-geometry failure, not a filename filter failure. The v0009 per-component files included open shells and surface models. Use the frozen `simplified/assembly.step`, whose expected solid bodies are checked and bound by bounding box, instead of retrying those files or enabling broad automatic sewing.

If the path still cannot pass, report the first failed stage, runtime/NX version, causal log evidence, attempted repair, usable artifacts, claims that cannot be made, and the next smallest correction or required user input.
