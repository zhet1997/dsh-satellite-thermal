# NX workflows

## Raw STEP steady workflow

For a Linux workspace containing `01_cad/simplified/assembly.step`, its matching `component.json`/`layout.json`, and no reusable FEM/SIM, use:

    prepare -> package -> create_upload / PUT / complete_upload
    -> preflight -> model -> solve -> extract -> finalize
    -> create_download / GET -> pull -> publish

This path builds the master PRT, FEM, SIM, steady solution, and native results from the frozen package. Read [thermal-data-preparation.md](thermal-data-preparation.md), [blank-step-modeling.md](blank-step-modeling.md), [remote-execution.md](remote-execution.md), and [result-extraction.md](result-extraction.md). It does not use the case-owned stage interface described below.

## Retained existing-case workflow

The material below describes the retained case-owned path. Its FOV, structural, transient, repair, snapshot, and presentation capabilities remain available; the raw STEP path does not replace or remove them.

The thermal path freezes task-specific intent in a brief and small case scripts, then lets `nxcase` manage native execution and evidence:

```text
brief -> model -> mesh -> conditions -> solve -> summarize -> validate -> snapshot -> publish
```

| Ownership | Stages | Responsibility |
|---|---|---|
| Case-owned setup | `model`, `mesh`, `conditions` | Reuse, prepare, or verify task-specific NX state and return machine facts |
| Runtime-owned execution | `solve`, `summarize`, `validate`, `snapshot`, `publish` | Run NXOpen/SST, extract temperatures and VTU, apply Gates, render review evidence, and publish the fixed workspace artifact |

The current managed solve expects `nx/thermal_case.sim` with an existing thermal solution. Case-owned setup may freeze or adapt a prepared `.prt/.fem/.sim` source, but the shared runtime does not create a complete thermal model from raw geometry on its own.

## Stage outcomes

1. `model` freezes the editable model state and verifies required bodies and solution identity.
2. `mesh` freezes or prepares the mesh and checks coverage, element/node counts, element families, and case-specific quality targets.
3. `conditions` verifies materials, initial temperature, heat sources, contacts, environmental/radiation loads, steady/transient controls, and requested results.
4. `solve` invokes NX Space Systems Thermal through NXOpen and requires a new `.bun` with an `End of run` marker.
5. `summarize` reopens the solution result and exports nodal temperatures, result-time metadata, and `reports/native.vtu` with point array `T` in Kelvin.
6. `validate` combines native completion checks with case-defined physical acceptance.
7. `snapshot` renders the extracted temperature field for human review.
8. `publish` copies the VTU to the Windows workspace and returns a `windows-powershell.create_download` request. The Agent streams it to the active 5090 version workspace at `02_sim/simulation/native.vtu` and verifies the reported size and SHA-256.

## Case files

```text
<case-root>/
|-- analysis-brief.md
`-- analysis/
    |-- case.py
    |-- model.py
    |-- mesh.py
    `-- conditions.py
```

`analysis/case.py` defines a JSON-serializable `CASE` dictionary. Each stage script defines `run(context)` and returns a JSON-serializable dictionary. Keep physical values in these case-owned files. Solver process management, timeout loops, checkpoint mutation, result parsing, and snapshot logic belong to the bundled runtime.
