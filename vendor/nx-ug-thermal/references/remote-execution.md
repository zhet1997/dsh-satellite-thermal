# Agent Web remote execution

Agent Web and `nxcase` run on Linux. Siemens NX12 runs only through the default `dsh.mcpServers.windows-powershell` MCP server in Agent Web's `config.json`. Read the target from that server's `remoteTarget` object:

    workspace_dir: D:\data\mcp-uploads
    download_dir: D:\data\mcp-downloads
    run_journal: E:\nx12\NXBIN\run_journal.exe

Use `powershell_execute` for control and `create_upload`, `complete_upload`, and `create_download` for files. Do not infer alternate Windows or NX paths.

## Validate the data path

MCP JSON-RPC success does not prove that the separate signed HTTP PUT and GET routes work. Before preparing or starting a long NX run, perform a small download probe: use `powershell_execute` to create a uniquely named nonempty probe file under the configured `download_dir`, call `create_download` for that exact file, GET it to a temporary Linux path, compare its bytes, and remove only those two probe files. Always apply the direct-transfer rule below to the probe. Stop before NX if the probe fails.

For every signed upload or download URL, parse the URL's hostname and explicitly bypass proxies for that hostname in the HTTP client. With curl, use `--noproxy <signed-url-host>` on every PUT and GET. Do this even when `NO_PROXY` and `no_proxy` currently contain the host: a managed turn may retain an older shell snapshot. Never disable proxies globally, and never hard-code the current MCP host in the Skill.

## Upload a frozen run

Run `nxcase push --run <run>`. Its JSON result contains the exact MCP server, `create_upload` arguments, local file path, and a PowerShell template.

1. Call `create_upload` once with the returned `arguments`.
2. Parse the hostname from `uploadUrl`, then stream the local `transfer.zip` using HTTP PUT, the returned headers, and an explicit per-host proxy bypass. Do not load the archive into the conversation.
3. Call `complete_upload` with the returned `uploadId`. Completion and SHA-256 verification are mandatory.
4. Replace `__COMPLETED_UPLOAD_TARGET_PATH__` in `powershell_template` with the exact Windows `targetPath` returned by `complete_upload`.
5. Call `powershell_execute` with that script. It verifies and expands the bundle, creates the unique Windows run, and starts `preflight`.

Signed URLs are secrets. Do not print or persist them in run manifests or logs. If PUT or completion fails, stop; obtain a new upload request rather than reusing an expired or consumed URL.

## Execute Windows stages

Run only the PowerShell returned or prescribed by this Skill through `windows-powershell.powershell_execute`. Advance `preflight`, `model`, `solve`, `extract`, and `finalize` in order. Use `nxcase status --run <run>` and its read-only PowerShell after disconnects. A recorded stage must never be started again.

The target shell is Windows PowerShell 5.1. The packaged scripts therefore read JSON with `[IO.File]::ReadAllText(..., [Text.Encoding]::UTF8)`, write BOM-free UTF-8 with `UTF8Encoding($false)`, accept empty artifact arrays, and replace same-directory state temporaries with `Move-Item -Force`. Keep these compatibility behaviors in the canonical Skill scripts and in the inline bootstrap/status PowerShell emitted by `nxcase`; do not substitute `Get-Content` default decoding, `Set-Content -Encoding UTF8`, or `File.Replace` for these paths.

If a worker exits while a state file still says `running`, query the recorded PID and inspect the state directory for a `<state>.<pid>.tmp` file. Treat a dead PID or stranded temporary as a failed state transition, not as stage success. Do not edit or restart that frozen run. Correct the canonical runner, run its tests, package a new unique run, and retain the failed run as evidence.

## Download finalized results

`finalize` writes `<run_id>-return.zip` under configured `D:\data\mcp-downloads`. After it succeeds, run `nxcase pull --run <run>`. If the local return bundle is absent, the result contains the exact `create_download` arguments and `download_to` path.

1. Call `create_download` once with the returned `arguments`.
2. Parse the hostname from `downloadUrl`, then stream the signed HTTP GET response into the exact `download_to` path with an explicit per-host proxy bypass. Range requests may be used to resume; do not expose the signed URL.
3. Run the same `nxcase pull --run <run>` command again. It accepts only the expected six ZIP members, verifies the five result records against `return-manifest.json`, and installs them under the run's `returned/` directory.
4. Run `nxcase publish --run <run>` only after pull returns `ready: true`.

The retained existing-case `publish` stage also returns a `create_download` request. Stream its GET response into `download_to` and verify the returned `size_bytes` and `sha256` before reporting publication complete.

If the GET fails because the URL expired or the connection was interrupted, delete only the incomplete local `return-bundle.zip`, request a fresh URL, and repeat the download. Never reuse a failed signed URL or bypass the manifest checks.

Treat HTTP 409 with `Source file changed after the download URL was created` as non-retryable when a fresh URL fails the same way and a read-only Windows check shows stable size, modification time, and SHA-256. Known-incompatible Windows MCP builds create the session with `stat(path)` but validate it with `fileHandle.stat()` and compare `dev`; on Node.js for Windows those calls can report different device IDs for the same unchanged file. Stop and report that the MCP server must build and validate the snapshot with the same stat API. Do not change timestamps, recopy the bundle, issue more signed URLs, return the archive as Base64 tool output, or bypass `create_download -> GET -> pull` in the Skill.

## Required MCP capabilities

The selected server must expose `powershell_execute`, `create_upload`, `complete_upload`, and `create_download`, with upload and download enabled and URLs reachable from the Linux host. Stop before Windows execution when any capability is missing.
