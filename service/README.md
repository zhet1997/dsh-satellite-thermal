# Independent thermal service

This service owns thermal projects, STEP import, document extraction, editable standard inputs, six-direction orbital heat flux, immutable run snapshots, and the NX execution queue. It runs separately from the DSH runtime. NX and its licenses remain on a separately configured workstation/worker; the Python service does not contain NX.

## Start

From the repository root, `docker compose up -d --build` starts port 8790 and a persistent Docker volume. The root Dockerfile uses the repository root as its build context. `service/Dockerfile` is also supplied for deployments with `service` as the context. Both start the same application and use exactly one Uvicorn worker.

For an offline/local deployment with an already verified Python/CadQuery dependency image, `--build-arg THERMAL_BASE_IMAGE=<your-image>` can reuse that dependency layer. The default remains the official `python:3.12-slim-bookworm` and installs all dependencies independently. This development session's cold build was interrupted after repeated Debian download timeouts; local container verification uses the existing `dsh-satellite-geometry-engine:0.1.0` dependency-only image. The thermal service does not call or require the geometry service.

For local development with Python 3.12:

```sh
cd service
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
THERMAL_DATA_DIR=/absolute/persistent/path .venv/bin/python -m uvicorn thermal_service.app:app --host 127.0.0.1 --port 8790 --workers 1
```

On Windows, use `.venv\Scripts\python.exe` and set environment variables using PowerShell. This development session uses the existing WSL CadQuery environment at `/home/zhet/.venvs/dsh-satellite-geometry/bin/python`, with isolated data under this repository's `runtime/service-data`.

The service is single-process by design: one lock serializes project changes and queue decisions, and atomic file replacement prevents partial project JSON. Do not add Uvicorn workers or mount the same storage concurrently in several service replicas. Back up the whole data directory including imports, documents and runs. Restarted active jobs are reconciled with their persisted worker IDs and immutable snapshots.

## Configuration

| Variable | Meaning |
|---|---|
| `THERMAL_DATA_DIR` | Persistent project directory; default `service/data` |
| `THERMAL_SERVICE_TOKEN` | Optional service bearer token; configure DSH host and MCP with the same value |
| `THERMAL_NX_WORKER_URL` | Worker HTTP(S) base URL, e.g. `http://nx-workstation:8791` |
| `THERMAL_NX_WORKER_TOKEN` | Worker bearer token, never sent to browser |
| `THERMAL_MAX_UPLOAD_MB` | File upload limit, default 200 MiB |

Network exposure is a deployment choice. Compose publishes on loopback by default. A service token is recommended whenever another machine can reach the service. The current storage namespace belongs to one configured DSH deployment; this version does not claim per-user project isolation within a shared service.

## Behavior and evidence

- `GET /api/health` separates service availability, geometry capability and NX connectivity. When no worker is configured, queued work becomes `waiting_connection`; users can keep editing or cancel it.
- All physical edits normalize units and missing/invalid values, label estimates, freeze a new input snapshot and queue a new run. Existing runs continue. Rename-only edits and unrelated source text do not trigger recalculation.
- Each run has one explicit `caseId`. If a save or manual request covers multiple cases, the service creates separate immutable queued runs for all of them. `POST /runs` without `caseId` returns the first run plus `queuedRunIds` for the group; project refresh returns every run. This prevents a two-case cold/hot setup from silently solving only its first case.
- STEP faces retain source hash, 1-based body/face indices and stable IDs. GLB meshes carry `componentId`/`faceId` extras. The source preview explicitly reports `source-step-tessellation`; it is never presented as an NX mesh.
- Ordinary STEP imports read names from XCAF occurrences where unambiguous. Geometry ZIP imports accept `simplified.step` and `properties.json`; identities use an explicit STEP index or a unique measured bounding-box/volume match. Ambiguous mappings remain visible and are not arbitrarily assigned.
- Sheet/shell geometry is retained alongside solids. Standard inputs include thickness, side conventions, density and heat capacity. NX must actually create compatible mixed dimensional elements; an unsupported worker must report the limitation instead of silently replacing shells with solid tetrahedra.
- CSV/XLSX/DOCX/JSON structured values are applied only to an exact unique component name/ID. Text/PDF prose and unmatched rows remain available via source/text download URLs for native DSH tools to interpret. No external LLM key is required by this service. Legacy `.xls`/`.doc` and scanned PDF OCR are not included.
- Orbital inputs preserve separate direct solar, reflected solar and Earth IR components before optical coefficients. Cold/hot cases select common timestamps using the disclosed six-direction incident sum; this does not predict temperature extrema. Source attribution is in `vendor/SOURCES.md`.
- A completed run requires actual NX readback, temperature statistics covering every component, and native/result artifacts. Disconnection or a missing solver never produces fabricated values.
- Condition readback separates `expectedTargets`/`expectedTargetSets` from actual `targetSets`. Actual sets are read from NX native groups and include set index, group, entity count, resolved component/face references, `memberIdentitySha256`, source and comparison status. `condition.targets` is the union of those actual memberships. The integration verifier compares these native memberships and hashes, then independently compares the PropertyTable temperature value and Kelvin unit; a saved input target list alone is insufficient evidence.
- Queue cancellation is immediate only for work not submitted. If a submission response was lost, a persisted dispatch marker forces idempotent reconciliation before requesting cancellation; execution ends only after the worker confirms.
- Exports contain frozen inputs, source references and actual evidence with an explicit execution manifest. Download paths come only from the project's registered artifact list. ZIP imports reject traversal, duplicate names, symlinks and oversized expansion.

API details are maintained in `docs/API-CONTRACT.md` at repository root. The independent worker protocol is multipart `POST /jobs` with `snapshot` JSON, `idempotencyKey` and `geometry` STEP. Its readback/results are passed through only with registered local artifact URLs.

## Validation

```sh
cd service
python -m pytest tests -q
```

Tests cover estimate normalization, complete finite shell inputs, revision conflicts, immutable snapshots, queue idempotency, cancellation including accepted-but-response-lost submission, identity-safe document extraction with units, geometry-backed examples, conservative real contacts, safe archive paths and consistent-time cold/hot recommendations. CadQuery-dependent tests are skipped only when the optional local geometry dependency is missing; the Docker image installs it.

An actual NX workstation is needed to validate real mixed solid/shell meshing, contact/radiation application, solver execution, cancellation of the actual process and engineering temperature/energy balance. Passing these Python tests does not substitute for that verification.
