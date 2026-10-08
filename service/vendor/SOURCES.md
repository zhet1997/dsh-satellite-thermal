# Vendored orbital heat-flux source

`compute_heat_flux.py` is an unmodified copy of the user's Agent Web working-tree file:

- Host: `work-server-5090-ali`
- Path: `/data/lbk/codex_web/agent-web/services/cad-assembly/backend/computation/python/compute_heat_flux.py`
- Copied: 2026-10-07
- SHA-256: `d18a20ab09e2c42f5d63448f73e6f9acd90a417fa2918f09990d5bc75c2e41f5`
- The handoff reports repository HEAD `2b31f95f21ff0010ced4a63184e0e79a9343d3d6`; the computation directory had untracked changes. This checksum identifies the actual copied bytes, not just the commit.
- No `LICENSE*` file was found at the source repository root during extraction. No public redistribution license is asserted. This copy is for the user's authorized private migration; preserve this notice when handing over source.

The adapter calls `run_case`, preserves direct solar, reflected solar and Earth infrared components, and never uses the script's example fixed-coefficient absorbed values for engineering inputs. Cold/hot recommendations use the sum of all six incident directional fluxes at one common timestamp. The underlying model is approximate two-body/LVLH with cylindrical shadow; it is not a validated STK/GMAT model or a prediction of thermal temperature extremes.
