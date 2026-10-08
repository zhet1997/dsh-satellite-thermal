param(
  [string]$Distribution = 'Ubuntu',
  [string]$Python = '/home/zhet/.venvs/dsh-satellite-geometry/bin/python',
  [string]$DataDirectory = '',
  [string]$WorkerUrl = 'http://127.0.0.1:8791',
  [ValidateRange(1024,65535)][int]$Port = 8790,
  [switch]$Restart,
  [switch]$CheckOnly
)
# Local quick start uses the existing, verified WSL Python/CadQuery environment.
$ErrorActionPreference = 'Stop'
$thermalKit = Split-Path -Parent $PSScriptRoot
$thermalService = Join-Path $thermalKit 'service'
if (-not $DataDirectory) { $DataDirectory = Join-Path $thermalKit 'runtime/thermal-projects' }
$DataDirectory = [IO.Path]::GetFullPath($DataDirectory)
if (-not (Test-Path -LiteralPath (Join-Path $thermalService 'thermal_service/app.py'))) { throw 'Thermal service is missing from the installation kit.' }
$thermalLinuxService = (& wsl.exe -d $Distribution --exec wslpath -a -u $thermalService.Replace('\','/') | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Could not resolve service path in WSL.' }
$thermalLinuxData = (& wsl.exe -d $Distribution --exec wslpath -a -u $DataDirectory.Replace('\','/') | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Could not resolve data path in WSL.' }
& wsl.exe -d $Distribution --cd $thermalLinuxService --exec $Python -c 'import cadquery,fastapi,uvicorn,numpy,pandas,trimesh,openpyxl,docx,pypdf,httpx'
if ($LASTEXITCODE -ne 0) { throw 'Python dependencies are missing. See the installation guide for WSL setup or Docker deployment.' }
Write-Output "Service: http://127.0.0.1:$Port/api/health"
Write-Output "Project data: $DataDirectory"
Write-Output "NX worker: $WorkerUrl"
if ($CheckOnly) { Write-Output 'Preflight passed. No service was started.'; return }
if ($Restart) {
  # Match the thermal application and port, never all Python/WSL/NX processes.
  $thermalStopCode = @'
import os, signal, sys, time
from pathlib import Path
port = sys.argv[1]
def identity(pid):
    try:
        stat = (Path('/proc') / str(pid) / 'stat').read_text()
        return stat.rsplit(')', 1)[1].split()[19]
    except (OSError, IndexError):
        return None
targets = []
for entry in Path('/proc').iterdir():
    if not entry.name.isdigit():
        continue
    try:
        args = (entry / 'cmdline').read_bytes().decode().split('\0')
        executable = Path(args[0]).name
        matches = ('python' in executable or executable == 'uvicorn') and 'thermal_service.app:app' in args
        configured_port = args[args.index('--port') + 1] if '--port' in args else '8000'
        if matches and configured_port == port:
            pid = int(entry.name)
            targets.append((pid, identity(pid)))
    except (OSError, UnicodeError, IndexError):
        continue
for pid, started in targets:
    if started is not None and identity(pid) == started:
        print(f'Stopping thermal service PID {pid} on port {port}', flush=True)
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
deadline = time.monotonic() + 5
while targets and time.monotonic() < deadline:
    targets = [(pid, started) for pid, started in targets if started is not None and identity(pid) == started]
    if targets:
        time.sleep(.1)
for pid, started in targets:
    if identity(pid) == started:
        print(f'Thermal service PID {pid} did not stop; force-stopping this process only.', flush=True)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
'@
  & wsl.exe -d $Distribution --exec $Python -c $thermalStopCode $Port
  if ($LASTEXITCODE -ne 0) { throw 'Could not stop the existing thermal service. No new service was started.' }
}
$thermalEnvArgs = @("THERMAL_DATA_DIR=$thermalLinuxData", "THERMAL_NX_WORKER_URL=$WorkerUrl")
if ($env:THERMAL_GEOMETRY_TIMEOUT_SECONDS) { $thermalEnvArgs += "THERMAL_GEOMETRY_TIMEOUT_SECONDS=$env:THERMAL_GEOMETRY_TIMEOUT_SECONDS" }
if ($env:THERMAL_SERVICE_TOKEN) { $thermalEnvArgs += "THERMAL_SERVICE_TOKEN=$env:THERMAL_SERVICE_TOKEN" }
if ($env:THERMAL_NX_WORKER_TOKEN) { $thermalEnvArgs += "THERMAL_NX_WORKER_TOKEN=$env:THERMAL_NX_WORKER_TOKEN" }
& wsl.exe -d $Distribution --cd $thermalLinuxService --exec env @thermalEnvArgs $Python -m uvicorn thermal_service.app:app --host 127.0.0.1 --port $Port --workers 1
if ($LASTEXITCODE -ne 0) { throw 'Thermal service exited with an error; inspect the output above.' }
