[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("preflight", "model", "solve", "extract", "finalize")]
    [string]$Stage,

    [Parameter(Mandatory = $true)]
    [string]$RunRoot
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

function Write-CompactJson {
    param([Parameter(Mandatory = $true)]$Value)
    $Value | ConvertTo-Json -Depth 40 -Compress
}

function Read-Utf8JsonWithRetry {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [int]$Attempts = 20
    )
    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            return [IO.File]::ReadAllText($Path, [Text.Encoding]::UTF8) | ConvertFrom-Json
        }
        catch [IO.IOException] {
            if ($attempt -eq $Attempts) { throw }
            Start-Sleep -Milliseconds 50
        }
    }
}

$resolvedRunRoot = [IO.Path]::GetFullPath($RunRoot).TrimEnd("\")
if (-not (Test-Path -LiteralPath $resolvedRunRoot -PathType Container)) {
    throw "NX thermal run directory is missing: $resolvedRunRoot"
}
$manifestPath = Join-Path $resolvedRunRoot "run-manifest.json"
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "NX thermal run manifest is missing: $manifestPath"
}
$manifest = [IO.File]::ReadAllText($manifestPath, [Text.Encoding]::UTF8) | ConvertFrom-Json
$runsRoot = [IO.Path]::GetFullPath((Join-Path ([string]$manifest.workspace_dir) "runs")).TrimEnd("\")
if (-not $resolvedRunRoot.StartsWith($runsRoot + "\", [StringComparison]::OrdinalIgnoreCase)) {
    throw "RunRoot must be below the configured Windows workspace runs directory"
}
$runId = Split-Path -Leaf $resolvedRunRoot
if ($runId -notmatch '^thermal_[0-9]{8}_[0-9]{6}_[a-f0-9]{8}$') {
    throw "Invalid NX thermal run id: $runId"
}
$stateDirectory = Join-Path $resolvedRunRoot "state"
New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null
$statePath = Join-Path $stateDirectory ($Stage + ".json")
if (Test-Path -LiteralPath $statePath -PathType Leaf) {
    $existing = [IO.File]::ReadAllText($statePath, [Text.Encoding]::UTF8) | ConvertFrom-Json
    if ($existing.status -eq "running") {
        $worker = Get-Process -Id ([int]$existing.pid) -ErrorAction SilentlyContinue
        if ($null -eq $worker) {
            throw "Stage $Stage is recorded as running but PID $($existing.pid) is no longer present; preserve evidence and stop"
        }
    }
    Write-CompactJson $existing
    exit 0
}

$orderedStages = @("preflight", "model", "solve", "extract", "finalize")
$stageIndex = [Array]::IndexOf($orderedStages, $Stage)
for ($index = 0; $index -lt $stageIndex; $index++) {
    $predecessor = $orderedStages[$index]
    $predecessorPath = Join-Path $stateDirectory ($predecessor + ".json")
    if (-not (Test-Path -LiteralPath $predecessorPath -PathType Leaf)) {
        throw "Stage $Stage requires missing predecessor $predecessor"
    }
    $predecessorState = [IO.File]::ReadAllText($predecessorPath, [Text.Encoding]::UTF8) | ConvertFrom-Json
    if ($predecessorState.status -ne "succeeded") {
        throw "Stage $Stage requires predecessor $predecessor to have status succeeded"
    }
}

$workerScript = Join-Path $resolvedRunRoot "package\Invoke-NxStage.ps1"
if (-not (Test-Path -LiteralPath $workerScript -PathType Leaf)) {
    throw "Frozen package is not expanded or has no stage worker: $workerScript"
}
$powerShell = Join-Path $PSHOME "powershell.exe"
if (-not (Test-Path -LiteralPath $powerShell -PathType Leaf)) {
    $powerShell = "powershell.exe"
}
$arguments = @(
    "-NoLogo",
    "-NoProfile",
    "-NonInteractive",
    "-ExecutionPolicy", "Bypass",
    "-File", ('"' + $workerScript + '"'),
    "-RunRoot", ('"' + $resolvedRunRoot + '"'),
    "-Stage", $Stage
)
$process = Start-Process -FilePath $powerShell -ArgumentList $arguments -WorkingDirectory $resolvedRunRoot -WindowStyle Hidden -PassThru

$deadline = [DateTime]::UtcNow.AddSeconds(15)
while ([DateTime]::UtcNow -lt $deadline) {
    if (Test-Path -LiteralPath $statePath -PathType Leaf) {
        $state = Read-Utf8JsonWithRetry -Path $statePath
        Write-CompactJson $state
        exit 0
    }
    if ($process.HasExited) {
        throw "Stage worker exited before writing state for $Stage (exit code $($process.ExitCode))"
    }
    Start-Sleep -Milliseconds 100
    $process.Refresh()
}
throw "Stage worker PID $($process.Id) did not create state within 15 seconds"
