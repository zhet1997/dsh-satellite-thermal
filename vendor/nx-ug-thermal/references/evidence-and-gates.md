# Thermal evidence and Gates

## Raw STEP demo evidence

The raw STEP workflow preserves run-manifest.json, per-stage JSON state, logs, package hashes, retained PRT/FEM/SIM/BUN files, raw node/element tables, and the five-file return manifest. Its current Gate is an error-free steady solve and verified publication of:

    native.vtu
    summary.json
    energy_balance.json
    run-manifest.json
    solver-log-summary.txt

No temperature engineering threshold is applied to this demo. Read [result-extraction.md](result-extraction.md) for the exact contract.

## Retained existing-case Gates

A run preserves enough information to prove what executed without requiring Agent Web to enter the NX object model directly:

```text
<run>/
|-- run.json
|-- logs/
|-- nx/
|   `-- raw_results/
|-- reports/
|   |-- checkpoints.json
|   |-- result_summary.json
|   `-- validation.json
`-- snapshots/
```

- `run.json` identifies the frozen case, source run, NX base, runtime version, and source-input hashes.
- `logs/` preserves stage, NXOpen, and solver output from this run.
- `reports/checkpoints.json` records successful stages in order.
- `reports/result_summary.json` contains extracted temperature quantities.
- `reports/validation.json` is the machine pass/fail Gate.
- `snapshots/` contains human review images.

## Artifact roles

| Role | Artifacts | Meaning |
|---|---|---|
| Editable state | `.prt`, `.fem`, `.sim` | Continue or inspect the NX model; not independent acceptance |
| Native result | `.bun` and native solver log | Proves the native thermal solver produced a result |
| Machine evidence | JSON/CSV under `reports/` and `nx/raw_results/` | Enables bounded checks outside NX |
| Human evidence | PNG under `snapshots/` | Reveals implausible fields or presentation problems |

No single role replaces the others.

## Checkpoint rules

1. Stages execute only in declared order.
2. A stage advances only after its process returns `ok: true`.
3. A completed stage cannot run again in the same run.
4. A repair uses a new run and replays required predecessors.
5. Never copy an old BUN or validation JSON into a new run as evidence of a new solve.

## Thermal Gate

Thermal validation normally requires:

- a newly generated, non-empty `.bun`;
- the native `End of run` marker;
- the requested minimum result-time count;
- finite, physically bounded extracted temperatures;
- a reviewed temperature snapshot.

Acceptance thresholds are physical decisions. Put them in `analysis/case.py` and explain them in `analysis-brief.md`; do not hide them in shared runtime code.

Before reporting success, run `nxcase status --run <run>`, read `reports/validation.json` and the native solver log, then inspect every generated snapshot for non-empty, correctly framed, legible output consistent with the machine result.
