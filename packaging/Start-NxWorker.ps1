param(
  [string]$Python = 'C:\Users\49076\AppData\Local\Programs\Python\Python312\python.exe',
  [string]$NxJournal = 'D:\Install\NX12\NXBIN\run_journal.exe',
  [string]$WorkRoot = 'D:\ThermalRuns',
  [string]$ListenAddress = '127.0.0.1',
  [ValidateRange(1024,65535)][int]$Port = 8791,
  [switch]$InstallDependencies,
  [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$thermalKit = Split-Path -Parent $PSScriptRoot
$thermalWheel = Join-Path $thermalKit 'dsh_satellite_thermal_executor-0.1.0-py3-none-any.whl'
if (-not (Test-Path -LiteralPath $NxJournal)) { throw "NX run_journal.exe was not found: $NxJournal" }
$WorkRoot = [IO.Path]::GetFullPath($WorkRoot)
if ($WorkRoot -match '[^\x00-\x7F]') { throw 'NX 12 requires an ASCII work directory, for example D:\ThermalRuns.' }
if ($ListenAddress -notin @('127.0.0.1','localhost','::1') -and -not $env:NX_WORKER_TOKEN) { throw 'Set NX_WORKER_TOKEN before listening on a network interface.' }
if ($InstallDependencies -and -not $CheckOnly) {
  & $Python -m pip install $thermalWheel
  if ($LASTEXITCODE -ne 0) { throw 'Worker dependency installation failed.' }
}
& $Python -c 'import thermal_executor,fastapi,uvicorn,psutil'
if ($LASTEXITCODE -ne 0) { throw 'Install the supplied worker wheel using this Python, or rerun with -InstallDependencies.' }
Write-Output "Worker: http://${ListenAddress}:$Port/health"
Write-Output "NX executable: $NxJournal"
Write-Output "Native engineering files: $WorkRoot"
if ($CheckOnly) { Write-Output 'Preflight passed. NX has not been launched or license-tested.'; return }
$env:NX_JOURNAL_EXECUTABLE=$NxJournal
$env:NX_WORK_ROOT=$WorkRoot
$env:NX_WORKER_HOST=$ListenAddress
$env:NX_WORKER_PORT=[string]$Port
& $Python -m thermal_executor.app
if ($LASTEXITCODE -ne 0) { throw 'NX worker exited with an error; inspect the output above.' }
