$ErrorActionPreference = 'Stop'
$thermalRoot = Split-Path -Parent $PSScriptRoot
Push-Location (Join-Path $thermalRoot 'web')
try {
  & npm.cmd ci --no-audit --no-fund
  if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed' }
  & npm.cmd run build
  if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
} finally { Pop-Location }
Push-Location (Join-Path $thermalRoot 'plugin')
try {
  & npm.cmd ci --no-audit --no-fund
  if ($LASTEXITCODE -ne 0) { throw 'Plugin dependency installation failed' }
  & npm.cmd run build
  if ($LASTEXITCODE -ne 0) { throw 'Plugin build failed' }
  & npm.cmd test
  if ($LASTEXITCODE -ne 0) { throw 'Plugin tests failed' }
  New-Item -ItemType Directory -Force (Join-Path $thermalRoot 'dist') | Out-Null
  & npm.cmd pack --pack-destination ../dist
  if ($LASTEXITCODE -ne 0) { throw 'Plugin packaging failed' }
} finally { Pop-Location }
Push-Location $thermalRoot
try {
  & python -m pip wheel ./nx-worker --no-deps --wheel-dir dist
  if ($LASTEXITCODE -ne 0) { throw 'NX worker packaging failed' }
  & python scripts/package-source.py
  if ($LASTEXITCODE -ne 0) { throw 'Source packaging failed' }
  & python scripts/package-installation.py
  if ($LASTEXITCODE -ne 0) { throw 'Installation kit packaging failed' }
} finally { Pop-Location }
Write-Host 'Plugin, worker and source packages are ready in dist. Deploy service and NX worker separately.'
