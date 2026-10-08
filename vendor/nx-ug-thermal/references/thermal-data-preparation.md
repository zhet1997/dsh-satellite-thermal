# Thermal data preparation

Read this reference before nxcase prepare for the raw STEP steady workflow. All data normalization happens on Linux before anything is transferred to Windows.

## Source contract

By default, prepare reads:

    00_inputs/component.json
    01_cad/planner/layout.json
    01_cad/simplified/assembly.step
    01_cad/simplified/geometry-contract.json
    01_cad/simplified/manifest.json
    01_cad/collision_check.json
    01_cad/mount_face_check.json

`component.json` supplies business identity and thermal metadata. `layout.json` supplies
placement provenance. `assembly.step` is the unchanged geometry transferred to NX.
`geometry-contract.json`, produced by the same CAD Builder export, supplies the exact
STEP body sequence, actual fused-body bounds/volume, shell surfaces, and every actual
OCCT face belonging to each mounting region, including that face's actual planar
normal. The simplified manifest must hash both
the STEP and geometry contract. `collision_check.json` and `mount_face_check.json`
are the canonical results of the CAD Builder Python check APIs; NX records them as
sources and does not rerun either algorithm. Exclude the union of mounting-face
problem IDs and both component IDs in every positive-volume collision pair. Keep
the bodies in the unchanged STEP, but freeze them with `analysis_enabled: false`,
remove their thermal contacts and power from the active case, and record each
exclusion source in `assumptions.json`. These four CAD artifacts have fixed canonical paths and no CLI
override, preventing an unrelated exclusion JSON from being passed as a mount check.
Optional `--overrides <json>` supplies thermal values. Explicit alternatives remain
available only for component, layout, and assembly STEP paths inside the workspace.

The Windows model stage imports the assembly once and binds flattened NX bodies by
the contract's stable sequence. Each contracted mounting face is mapped independently
by actual face bounds; multiple faces produce multiple thermal contacts for the same
component. NX does not create a theoretical rectangular face from `dimensions_mm`.

Do not crop, move, delete, heal, or otherwise repair STEP geometry. Preserve the source placement and hash.

## Data priority and quantities

Resolve each quantity in this order:

    explicit user override
    -> traceable component/CAD metadata or geometric derivation
    -> automatic estimate
    -> global demo default

Each normalized quantity is an object with these semantic fields:

    {
      "value": 0.0,
      "unit": "W",
      "status": "global_default",
      "source_locator": "config.defaults.power_W",
      "method": "demo continuity default"
    }

status is one of provided, derived, estimated, or global_default. Record every estimated or global_default substitution in assumptions.json; missing values do not block this demo.

Defaults are:

| Quantity | Default |
|---|---:|
| Component power | 0 W |
| Conductivity | 167 W/(m*K) |
| Emissivity | 0.8 |
| Area thermal contact resistance | 0.001 m2*K/W |
| External heat flux on each of six directions | 0 W/m2 |
| Initial temperature | 100 K |
| Space temperature | 3 K |
| View factor | 1.0 |

The demo may use uniform conductivity and emissivity. Zero-power components are valid. Zero external heat flux is valid and is not converted into a meaningless NX Heat Flux boundary condition.

## Optional override shape

    {
      "components": {
        "P001": {
          "power_W": 5.0,
          "conductivity_W_mK": 42.0,
          "emissivity": 0.55
        }
      },
      "contacts": {
        "CONTACT_P001": {"resistance_m2K_W": 0.002}
      },
      "environment": {"x-": 0.0, "x+": 10.0},
      "global": {
        "initial_temperature_K": 100.0,
        "space_temperature_K": 3.0,
        "view_factor": 1.0
      }
    }

The six environment keys are always x-, x+, y-, y+, z-, and z+.

## Persisted interface

prepare writes exactly these seven files:

    02_sim/thermal/inputs/
      thermal-inputs.json
      geometry-semantics.json
      source-manifest.json
      assumptions.json
      components.csv
      contacts.csv
      environment.csv

`thermal-inputs.json` uses schema `nx-ug-thermal-inputs/1`. `components.csv` contains component ID, the common assembly STEP package path, and the value/status/source for power, conductivity, and emissivity. `contacts.csv` contains component and oriented face pairs, derived area, and contact resistance provenance. `environment.csv` always contains the six directional boundaries and heat-flux provenance.

`geometry-semantics.json` uses schema `nx-ug-thermal-geometry-semantics/2`. It records
the assembly hash and CAD Builder-derived bodies, faces, and contacts. NX binding
evidence records the imported STEP bounds. Each component body also records
`source_box_count` from its layout placement. A fused multi-box body also records a
Linux-derived `mesh` object containing `requested_size_mm`, `retry_scales`, the
policy identifier, and the actual shortest bounding-box extent used by the policy;
Windows consumes these frozen values rather than deciding multi-box density.
`source-manifest.json` records the seven
default source files plus an override file when supplied. Packaging verifies them
before freezing the exact STEP and normalized semantics.

The NX-facing thermal-case.json is a deterministic derivative created only inside the frozen ZIP; it is not part of the persisted Linux input interface.
