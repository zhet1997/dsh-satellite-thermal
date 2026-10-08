# Thermal analysis brief

This reference governs retained existing-case work. For the raw STEP steady demo, use the normalized records in [thermal-data-preparation.md](thermal-data-preparation.md): missing thermal values are estimated/defaulted and logged instead of blocking preparation.

Write a concise `analysis-brief.md` from the user's request and the actual NX artifacts before changing a case or launching NX. Do not ask the user to fill a generic schema.

## Resolve inputs

Separate inputs into:

- Physical decisions: geometry, material properties, thermal formulation, initial conditions, heat loads, environmental conditions, contacts, mesh intent, requested outputs, and acceptance thresholds. Ask when a required value is missing or contradictory.
- Operational choices: run root, timeouts, interpreter, and log detail. Select reasonable values and disclose them.
- File-derived facts: NX version, solution name, body and face IDs, mesh counts, result types, and native artifact names. Inspect the actual files instead of copying a similar case.

Dimensioned or structured source data wins over visual estimates. When authoritative inputs conflict, record the conflict and stop before setup.

## Suggested format

```text
Analysis brief:
- Objective: <thermal question>
- Source state: <PRT/FEM/SIM/BUN paths, versions, and reusable state>
- Units and coordinates: <unit system, origin, axes, and sign convention>
- Geometry/model: <included bodies, exclusions, simplifications, and labels>
- Materials/physics: <material values and steady/transient formulation>
- Mesh state: <element families, counts, quality targets, and reuse policy>
- Conditions: <initial temperature, loads, BCs, contacts, radiation, and time controls>
- Stage plan: <outputs and checks for each stage>
- Acceptance: <bounded temperature/result checks and required snapshot>
- Engineering outputs: <expected PRT/FEM/SIM/BUN and reports>
- Assumptions: <case-specific assumptions>
- Non-claims: <fidelity, couplings, or setup work not implemented>
```

The brief is ready when another engineer can identify what is reused, what each stage proves, which native artifacts must be created, and which machine quantities decide pass or fail without reading the implementation.

Values from reference cases remain reference inputs; never promote them into general Skill defaults.
