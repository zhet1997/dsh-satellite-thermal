---
name: nx-ug-thermal
description: Build steady Siemens NX/Simcenter thermal models from a simplified assembly STEP plus component/layout metadata, or inspect, run, repair, and validate existing NX engineering cases through NXOpen. Use when NX/UG is explicitly selected for thermal work; retained utilities also support existing FOV and structural workflows. Do not use for COMSOL.
---

# NX/UG Thermal

Run from Linux. The `remoteTarget` object under `dsh.mcpServers.windows-powershell` in Agent Web's `config.json` is authoritative for the Windows workspace, download output directory, and NX Journal executable. Read [references/remote-execution.md](references/remote-execution.md) before the first remote action. Validate the signed upload and download data paths before starting NX; MCP tool success alone does not validate their HTTP PUT/GET endpoints. If configuration or MCP capability validation fails, stop instead of inferring alternate Windows or NX paths.

## Route the task

- For `01_cad/simplified/assembly.step` without reusable FEM/SIM state, use the raw STEP steady workflow below. Read [references/thermal-data-preparation.md](references/thermal-data-preparation.md), [references/blank-step-modeling.md](references/blank-step-modeling.md), and [references/result-extraction.md](references/result-extraction.md).
- For an existing .prt, .fem, .sim, or .bun case, read [references/analysis-brief.md](references/analysis-brief.md), [references/workflows.md](references/workflows.md), [references/runtime-and-cli.md](references/runtime-and-cli.md), and [references/evidence-and-gates.md](references/evidence-and-gates.md).
- Keep the existing FOV, structural, transient, presentation, and repair paths available. Use their existing doctor, check, start, stage, run, and status commands where applicable. Read [references/repair-loop.md](references/repair-loop.md) only for explicit repair work or legacy case failure triage.

## Raw STEP steady workflow

The default blank-model path is:

    Linux normalize -> freeze ZIP -> MCP upload
    -> preflight/model/solve/extract/finalize
    -> MCP download -> Linux publish

Use the Linux-side nxcase commands prepare, package, push, status, pull, and publish for this path. These commands are not the complete CLI surface; the retained commands above remain supported.

1. Run `prepare --workspace <version-workspace>` to read the canonical CAD Builder inputs and outputs documented in `thermal-data-preparation.md`, then create the seven normalized files under `02_sim/thermal/inputs/`. Use `assembly.step` unchanged and require it to contain one solid for the shell and one solid for each component; a component assembled from many `layout.boxes` remains one fused business component, not one component per box. Do not generate, unite, repair, or independently classify geometry in this Skill. CAD Builder's hashed `geometry-contract.json` is authoritative for STEP body order, actual fused-body bounds, shell faces, and component mount-face sets. Bind imported NX bodies by that sequence and map every contracted mount face to an NX face within tolerance. A component may map to several contacts. During Linux preparation, derive each fused multi-box component's initial TET4 size from its contracted actual bounding box and freeze the size and retry scales in `geometry-semantics.json`; Windows must consume that policy without deciding density from child-box count. Retries only increase element size, never refine it. Exclude every component named by canonical `01_cad/mount_face_check.json#/problem_component_ids` or by either side of a canonical `01_cad/collision_check.json#/collision_pairs` entry. Freeze those exclusions before transfer so Windows neither meshes nor loads the excluded bodies and does not rerun either CAD check. Never substitute another exclusion file. Missing thermal values do not block this demo: apply the documented deterministic estimates/defaults and record every substitution in `assumptions.json`.
2. Run package to create a unique ASCII run under 02_sim/thermal/runs/<run_id>/. Packaging verifies the source manifest and freezes STEP, normalized data, NX12 journals, and the Windows runner.
3. Run push and follow its structured transfer plan: call `windows-powershell.create_upload`, PUT the exact `transfer.zip` bytes to the returned URL using the direct-transfer rules in `remote-execution.md`, call `complete_upload`, then substitute its `targetPath` into the returned PowerShell template and execute it with `windows-powershell.powershell_execute`.
4. Advance Windows stages one at a time in order: preflight, model, solve, extract, finalize. The packaged runner targets Windows PowerShell 5.1; use it unchanged so JSON is read and written as explicit UTF-8 and empty artifact arrays remain valid. Use status and the recorded PID after disconnects. Never start a recorded stage again.
5. `finalize` writes the return bundle under `D:\data\mcp-downloads`. After it succeeds, run pull to obtain the `create_download` request. Call `windows-powershell.create_download`, GET the returned URL into the reported local path using the direct-transfer rules, rerun pull to verify and unpack it, then publish. Do not retry a deterministic download snapshot-validation 409; report the MCP service incompatibility identified in `remote-execution.md`.

The raw STEP path is steady-state only. It must not modify, trim, move, delete, or automatically repair source geometry. A stage failure preserves evidence and stops; do not alter the model, hot-patch a frozen Windows run, or silently retry inside that run. Apply runner fixes in the Skill, package a new run, and preserve the failed run for diagnosis.

## Completion

Windows retains .prt, .fem, .sim, and .bun. Linux completion requires these five non-empty published files under 02_sim/simulation/:

    native.vtu
    summary.json
    energy_balance.json
    run-manifest.json
    solver-log-summary.txt

Report only completed stages and verified artifacts. For the raw STEP demo, successful execution without NX or solver errors is the acceptance criterion; there is no temperature engineering threshold. Existing-case workflows retain their case-specific Gates and presentation checks.
