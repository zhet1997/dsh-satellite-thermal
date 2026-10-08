# Service validation — 2026-10-07

The Python service suite passed **21 tests** in WSL after the real integration fix for changing intermediate NX artifacts. The preceding 20-test suite also passed in the Docker image; the additional live-artifact regression is covered by the final container check. One upstream Starlette/httpx deprecation warning remains. The actual service-to-NX conduction benchmark also passed; see `validation/NX-SERVICE-INTEGRATION.md` at repository root. This does not claim full-satellite or all-physics engineering acceptance.

Coverage includes finite nonblocking estimates and provenance, mixed solid/sheet import, optimistic revision conflicts, immutable run snapshots, idempotent queues, separate cold/hot runs, active cancellation and lost-submission-response reconciliation, document component identity and units, archive path attacks, bearer authentication, material-change estimates, and rejection of empty NX completion evidence.

Actual geometry fixtures:

| Fixture | Verified evidence |
|---|---|
| Generated STEP demonstration | 3 real solids, 18 identified faces, 2 actual coplanar contacts, GLB |
| Mixed solid/sheet STEP | 2 components, 7 faces; free sheet retained as shell |
| Handoff valid planetary gearbox STEP | 1 solid, 133 faces, real tessellation; SHA-256 `eabc4c4634b8bcdb27795900dae4c07394c891ba40a01352448945ae94b249d1` |
| Official geometry plugin `satellite-package.zip` | Names/source IDs matched by unique measured geometry; contact links retained |

The retained full satellite source files have documented defects and were not presented as a passed full-satellite thermal benchmark. The valid gearbox is one genuine component from the handoff, not an entire satellite.

Container evidence:

- Image: `dsh-satellite-thermal-service:0.1.0`.
- Final manifest: `sha256:111c5ab2e3471ec49ea6998fa7905afc818bbb722182ddb70e618c7efb6b0408`.
- Built using the optional `THERMAL_BASE_IMAGE=dsh-satellite-geometry-engine:0.1.0`, which is a pre-existing Python/CadQuery dependency-only image, and the public Tsinghua PyPI mirror via `THERMAL_PIP_INDEX_URL`.
- Default Dockerfile remains based on official Python and installs dependencies independently. A full cold build was interrupted after repeated Debian/PyPI download timeouts; that network-dependent path has not been completed locally.
- An isolated container on port 8792 returned healthy, generated a real 3-component/2-contact example, served a 12,244-byte GLB, computed two six-flux cases, retained three separate runs (original plus cold/hot), and exported a 65,541-byte ZIP with `nxExecution: not-completed`. The temporary smoke container was then stopped and removed.
- Compose configuration parsed successfully. Existing production geometry containers and data were not modified.

NX integration preparation:

- WSL can read the Windows worker's `http://127.0.0.1:8791/health` successfully, without firewall/network changes.
- The development service on 8790 is configured to that worker, with clean `runtime/integration-service-data`. Earlier UI/parameter test data remains under `runtime/service-data`.
- `scripts/prepare_validation.py` generated real STEP files and standard snapshots for example, solid/sheet, conduction, contact and radiation cases. `runtime/nx-test-inputs.json` indexes them.
- `runtime/nx-validation-inputs/analytic-expected.csv` holds analytic references only: 2 W Fourier heat flow and 310 K bar mean; 1.333333 W contact heat flow and 306.666667/313.333333 K interface temperatures; 246.199268873823 K radiation isothermal-limit reference and 10 W energy balance. These are not NX results. Actual execution evidence must be recorded separately by the worker/integration validation.
- The final real service-to-worker conduction run `run-6b1bfe88d9f140f287f3db7fbd5b7a23` completed with 300–320 K, mean 310.01361088344026 K, reported through-flow 2 W and relative energy residual 8.68e-6. Actual material/temperature boundary/face mappings and 51 artifact downloads were verified. The reopened NX SIM's actual `TargetSetManager`/`GetEntities` memberships, face identities, member counts and member SHA-256 hashes also match the frozen boundary targets and committed groups. `expectedTargets` is input evidence; `targetSets` and `targets` are independently read native membership evidence. Verification version `2-native-target-set-membership` records both native target-set checks as passed.
- The final export is 3,202,182 bytes with SHA-256 `ada51d315cbb82b35e4f26091da2403eb8302de175bdacd38d45716e346757ca`. `validation/integration-result.json` retains final evidence and the failed/earlier successful attempts. The earlier complete record is archived under `validation/integration-history/`; all runs and input snapshots remain immutable.
