# Runtime and CLI

## Linux raw STEP control

Run these commands from the deployed Skill on Linux. Each prints one compact JSON object:

| Command | Effect |
|---|---|
| prepare --workspace <workspace> | Normalize the seven-file thermal input interface |
| package --workspace <workspace> | Verify sources and create a unique frozen run plus `transfer.zip` |
| push --run <run> | Return a structured `create_upload` request and post-upload bootstrap template |
| status --run <run> | Return read-only PowerShell for Windows stage/PID inspection |
| pull --run <run> | Before download, return a `create_download` request; after GET, verify and unpack the exact five results |
| publish --run <run> | Atomically publish the five files under 02_sim/simulation/ |

The raw path advances Windows stages through `windows-powershell.powershell_execute` and the packaged Start-NxStage.ps1; see [remote-execution.md](remote-execution.md). `ok: true, ready: false` means the command produced a valid next-tool request, not that the transfer or a later Windows stage succeeded.

## Retained case runtime

`nxcase` adds environment probes, bounded execution, stable JSON output, logs, source freezing, ordered checkpoints, result extraction, validation, and snapshots around NXOpen and the native thermal solver.

The commands below remain available for existing-case thermal, FOV, structural, transient, repair, and presentation workflows. They may use the retained Windows workspace contract. They are not removed or replaced by the raw STEP commands.

```powershell
python scripts\nxcase --help
```

## Probe the environment

Run before the first native execution:

```powershell
python scripts\nxcase doctor --domain thermal --workspace-config C:\ProgramData\PowerShellMcp\config\nx-ug-thermal-workspace.json
```

The default probe uses the NX installation containing the configured `remoteTarget.run_journal`, then checks version evidence, thermal solver files/templates, and a real NXOpen session. A detected installation is not enough if the NXOpen probe fails. Use an ASCII-only work directory for NX12.

## Check and start a case

```powershell
python scripts\nxcase check --case <case-root> --source-run <source-run> --workspace-config <workspace-config>
python scripts\nxcase start --case <case-root> --source-run <source-run> --workspace-config <workspace-config>
```

`check` requires a non-empty analysis brief, valid `analysis/case.py`, required thermal stage scripts, and all declared source files. `start` creates a unique run, freezes case source, hashes source inputs, records the NX base, and initializes checkpoints. It refuses to overwrite a run.

## Execute and inspect one stage

```powershell
python scripts\nxcase stage <stage> --run <run> --runtime-python <python-executable>
python scripts\nxcase stage publish --run <run> --remote-workspace <5090-active-version-workspace>
python scripts\nxcase status --run <run>
```

The stage command verifies order, writes a frozen context, runs the case script or managed runtime in a bounded subprocess, writes `logs/<stage>.log`, parses the final JSON object, and appends a checkpoint only when the returned machine result passes. NXOpen journals use NX12's embedded Python through `run_journal.exe`; `--runtime-python` selects the stage-worker interpreter.

## Full-run convenience

```powershell
python scripts\nxcase run --case <case-root> --source-run <source-run> --workspace-config <workspace-config> --remote-workspace <5090-active-version-workspace> --runtime-python <python-executable>
```

Use `run` only after the same case has passed stage-by-stage in the current environment. A failure stops the command and is not retried. Thermal `publish` requires the preceding `snapshot` checkpoint and uploads only `reports/native.vtu` to the fixed relative workspace path.

Every command prints one final compact JSON object. `ok: true` means that command's checks passed; a nonzero exit means it failed. Native solver exit codes remain diagnostic because final acceptance also requires native completion markers, result artifacts, physical Gates, and snapshot review.
