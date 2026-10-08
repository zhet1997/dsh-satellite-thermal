# Blank STEP steady modeling

Read this reference after Linux data preparation and before advancing Windows stages. This is the default raw STEP demo path; it does not replace the existing-case, FOV, structural, transient, repair, or presentation workflows.

## Run layout

Linux owns:

    02_sim/thermal/runs/<run_id>/
      payload.zip
      transfer.zip
      run-manifest.json
      state/
      returned/

Windows owns only the frozen execution run:

    D:\data\mcp-uploads\runs\<run_id>\
      payload.zip
      run-manifest.json
      package/
      state/
      logs/
      nx/
      results/
      return/

After `finalize`, Windows writes `D:\data\mcp-downloads\<run_id>-return.zip`. The MCP service owns the upload landing directory; use only the final `targetPath` returned by `complete_upload` when bootstrapping the Windows run.

run_id is an ASCII timestamp plus random suffix. Neither side may overwrite an existing run directory.

## Stage contract

1. preflight verifies fixed paths, NX12 journals/templates, ZIP hash, and every frozen package entry.
2. model imports the frozen `geometry/assembly.step` once with assembly flattening,
   binds every resulting body by the CAD Builder contract's STEP sequence, maps each
   contracted actual mount face to one NX face within tolerance, tags the business
   bodies/faces, records NX-measured bounds, and saves `nx/geometry_master.prt`.
   Multi-face mount regions remain multi-face; the stage does not synthesize a
   dimension-based rectangle, reapply placement, or modify geometry.
3. solve creates case.fem and thermal_case.sim, generates the mesh and thermal objects, runs the steady solver, and requires a new non-empty .bun plus the native completion marker.
4. extract runs in a separate NX process, reads the result, exports nodes/elements/temperatures and the standard result set.
5. finalize verifies the NX artifacts, freezes stage evidence, and creates the exact return manifest.

Each stage writes state/<stage>.json with status, PID, start/end time, exit code, error summary, and artifact hashes. It may be started only once per run.

## NX12 invariants

Use the verified solver/analysis creation calls:

    fem.FinalizeCreation(..., "NX SPACE SYSTEMS THERMAL", "Thermal", ...)
    sim.FinalizeCreation(fem, -1, [])
    simulation.CreateSolution(
        "NX SPACE SYSTEMS THERMAL",
        "Thermal",
        "Space Systems Thermal",
        "steady_state_thermal",
    )

Do not substitute the shortened Solution creation pattern that produced NX error 1775003 in the failed session.

Use TET4 for the default path. Requested edge sizes are 20 mm for the shell, 16 mm for wall bodies, and half the smallest bounding-box dimension clamped to 10-25 mm for ordinary components. For components smaller than 20 mm, keep the requested size at half the smallest dimension with a 0.25 mm floor.

Linux preparation selects a fused multi-box component's initial requested size from
the CAD Builder contract's actual body bounds. Divide the shortest bounding-box
extent by four, round upward to the next 5 mm, and clamp the result to 25-50 mm.
This intentionally ignores tiny child-box edges that would force excessive global
refinement. Freeze the selected size, the policy identifier, and retry scales
`[1, 1.5, 2, 3]` on the body in `geometry-semantics.json`. Windows consumes these
values and does not infer multi-box density from child-box count. Ordinary
components retain their dimension-based requested size.

When mapping a contracted mounting region onto a coarse mesh, require the element
face to lie on the contracted plane and have an overlapping projected bounding box.
Do not require every element-face node to be contained by a small source-box face;
that containment rule incorrectly rejects valid coarse boundary faces.
For a fused multi-box component only, allow the boundary plane tolerance to grow to
10% of the actual element size with a 2 mm cap, so stepped source-box faces can map
to the nearest retained coarse boundary. Keep the ordinary 0.001 mm tolerance for
all other components.

Apply the normalized initial temperature, materials, radiation, contacts, and nonzero loads. Use Contact Thermal Coupling for the thermal contact pairs. Do not create Heat Generation for a component whose power is zero, and do not create Heat Flux for a zero-flux boundary.

This path is steady-state and prioritizes completing the solve chain without NX errors. It defines no temperature threshold and performs no automatic model correction after failure.
