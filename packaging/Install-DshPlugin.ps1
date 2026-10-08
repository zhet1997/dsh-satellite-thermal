param(
  [ValidatePattern('^[A-Za-z0-9_-]+$')][string]$Profile = 'web',
  [string]$DshHome = '',
  [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$thermalKit = Split-Path -Parent $PSScriptRoot
$thermalRelease = Get-Content -LiteralPath (Join-Path $thermalKit 'installation-manifest.json') -Raw | ConvertFrom-Json
$thermalPackageName = 'lbk-dsh-satellite-thermal-' + $thermalRelease.version + '.tgz'
$thermalPackage = Join-Path $thermalKit $thermalPackageName
if (-not (Test-Path -LiteralPath $thermalPackage)) { throw "Plugin package not found: $thermalPackage" }
$thermalDsh = Get-Command dsh.cmd -ErrorAction SilentlyContinue
if (-not $thermalDsh) { $thermalDsh = Get-Command dsh -ErrorAction Stop }
if ($DshHome) { $env:DSH_HOME = [IO.Path]::GetFullPath($DshHome) }
$thermalHome = if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $env:USERPROFILE '.dsh' }
$thermalManifest = Join-Path $thermalHome "profiles/$Profile/package.json"
if (-not (Test-Path -LiteralPath $thermalManifest)) { throw "Existing DSH profile not found: $thermalManifest" }
$thermalProfileConfig = Get-Content -LiteralPath $thermalManifest -Raw | ConvertFrom-Json
if (-not $thermalProfileConfig.dsh.profile.bundles) { throw 'Target profile has no DSH bundle configuration.' }
if ($Profile -eq 'desktop') { throw 'This package installer targets DSH Web profiles, not Electron desktop.' }
$thermalVersion = & $thermalDsh.Source --version
if ($LASTEXITCODE -ne 0) { throw 'Cannot read DSH version.' }
Write-Output "DSH: $thermalVersion; profile: $Profile; data root: $thermalHome"
Write-Output "Package: $thermalPackage"
if ([string]$thermalVersion -notmatch '0\.2\.0-rc\.2') { Write-Warning 'Only DSH Web 0.2.0-rc.2 has been verified for this release.' }
# The desktop-launched Web host does not inherit Codex's private runtime PATH.
$thermalModulesMetadata = Join-Path (Split-Path -Parent $thermalManifest) 'node_modules/.modules.yaml'
$thermalExistingPnpmVersion = $null
if (Test-Path -LiteralPath $thermalModulesMetadata) {
  $thermalModulesText = Get-Content -LiteralPath $thermalModulesMetadata -Raw
  if ($thermalModulesText -match '(?m)^\s*["'']?packageManager["'']?\s*:\s*["'']?pnpm@(?<version>\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?)') {
    $thermalExistingPnpmVersion = $Matches.version
  }
}
$thermalRecommendedPnpm = if ($thermalExistingPnpmVersion) { "pnpm@$thermalExistingPnpmVersion" } else { 'pnpm' }
$thermalPnpmHelp = "Install pnpm in a normal PowerShell window with: npm.cmd install --global $thermalRecommendedPnpm`nUse the version that matches the existing DSH profile. Ensure the npm global prefix is on the Windows user PATH, reopen the terminal and restart DSH (and its launcher if PATH changed), then retry."
$thermalResolvedPnpm = Get-Command pnpm.cmd -ErrorAction SilentlyContinue
$thermalPersistentPath = @(
  [Environment]::GetEnvironmentVariable('Path', 'Machine'),
  [Environment]::GetEnvironmentVariable('Path', 'User')
) -join ';'
$thermalDurablePnpm = $null
foreach ($thermalPathEntry in ($thermalPersistentPath -split ';')) {
  if (-not $thermalPathEntry.Trim()) { continue }
  $thermalPathEntry = [Environment]::ExpandEnvironmentVariables($thermalPathEntry.Trim().Trim('"'))
  if ($thermalPathEntry -match '[\\/]codex-runtimes[\\/]') { continue }
  $thermalPnpmCandidate = Join-Path $thermalPathEntry 'pnpm.cmd'
  if (Test-Path -LiteralPath $thermalPnpmCandidate -PathType Leaf) { $thermalDurablePnpm = $thermalPnpmCandidate; break }
}
if ($thermalDurablePnpm) {
  $thermalPnpmPath = $thermalDurablePnpm
} elseif ($thermalResolvedPnpm -and $thermalResolvedPnpm.Source -notmatch '[\\/]codex-runtimes[\\/]') {
  $thermalPnpmPath = $thermalResolvedPnpm.Source
  Write-Warning 'pnpm is available in this terminal but was not found on the saved Windows PATH. The DSH Web launcher must provide the same PATH before webpage installation can work.'
} else {
  throw "DSH Web installation requires pnpm.cmd outside the Codex private runtime. $thermalPnpmHelp"
}
$thermalPnpmVersion = & $thermalPnpmPath --version
if ($LASTEXITCODE -ne 0) { throw "pnpm cannot run: $thermalPnpmPath . $thermalPnpmHelp" }
if ([string]$thermalPnpmVersion -notmatch '^(?<major>\d+)\.\d+\.\d+') { throw "Cannot parse pnpm version: $thermalPnpmVersion" }
$thermalPnpmMajor = $Matches.major
if ($thermalExistingPnpmVersion -and $thermalExistingPnpmVersion.Split('.')[0] -ne $thermalPnpmMajor) {
  throw "Existing DSH dependencies use pnpm@$thermalExistingPnpmVersion, but this pnpm is $thermalPnpmVersion. This may cause ERR_PNPM_UNEXPECTED_STORE. Keep the existing profile and dependencies. $thermalPnpmHelp"
}
Write-Output "pnpm: $thermalPnpmVersion; executable: $thermalPnpmPath"
$thermalEntry = $thermalRelease.files | Where-Object { $_.path -eq $thermalPackageName }
if (-not $thermalEntry -or (Get-FileHash -LiteralPath $thermalPackage -Algorithm SHA256).Hash -ne $thermalEntry.sha256) { throw 'Plugin checksum does not match installation manifest.' }
if ($CheckOnly) { Write-Output 'Preflight passed. No profile was changed.'; return }
Copy-Item -LiteralPath $thermalManifest -Destination ($thermalManifest + '.thermal-backup-' + (Get-Date -Format 'yyyyMMddHHmmssfff'))
$thermalOriginalPath = $env:PATH
try {
  $env:PATH = (Split-Path -Parent $thermalPnpmPath) + ';' + $thermalOriginalPath
  & $thermalDsh.Source plugin --profile $Profile add $thermalPackage
  if ($LASTEXITCODE -ne 0) { throw 'Official DSH installation failed. Inspect its dependency/network error; do not replace the existing profile.' }
} finally {
  $env:PATH = $thermalOriginalPath
}
Write-Output 'Installation finished. Refresh DSH Web; restart the existing instance only if DSH requests it or the plugin is not loaded. Configure Satellite Thermal under Settings > Built-in Plugins.'
