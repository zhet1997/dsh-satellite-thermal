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

$script:RunRoot = [IO.Path]::GetFullPath($RunRoot).TrimEnd("\")
$script:RunId = Split-Path -Leaf $script:RunRoot
$script:RunManifestPath = Join-Path $script:RunRoot "run-manifest.json"
$script:RunManifest = [IO.File]::ReadAllText($script:RunManifestPath, [Text.Encoding]::UTF8) | ConvertFrom-Json
$script:RunJournal = [IO.Path]::GetFullPath([string]$script:RunManifest.run_journal)
$script:NxBase = Split-Path -Parent (Split-Path -Parent $script:RunJournal)
$script:PackageRoot = Join-Path $script:RunRoot "package"
$script:StateDirectory = Join-Path $script:RunRoot "state"
$script:StatePath = Join-Path $script:StateDirectory ($Stage + ".json")
$script:StartedAt = [DateTime]::UtcNow.ToString("o")
$script:StageTimeouts = @{preflight = 120; model = 1800; solve = 7800; extract = 1800; finalize = 120}
$script:OrderedStages = @("preflight", "model", "solve", "extract", "finalize")

function Write-JsonAtomic {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Value
    )
    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    $temporary = $Path + "." + $PID + ".tmp"
    try {
        $json = $Value | ConvertTo-Json -Depth 60
        [IO.File]::WriteAllText($temporary, $json, (New-Object Text.UTF8Encoding($false)))
        Move-Item -LiteralPath $temporary -Destination $Path -Force
    }
    finally {
        if (Test-Path -LiteralPath $temporary) {
            Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
        }
    }
}

function Get-FileRecord {
    param([Parameter(Mandatory = $true)][string]$Path)
    $item = Get-Item -LiteralPath $Path
    return [ordered]@{
        path = $item.FullName
        size_bytes = [int64]$item.Length
        sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $item.FullName).Hash.ToLowerInvariant()
    }
}

function Update-RunState {
    param([Parameter(Mandatory = $true)]$CurrentState)
    $stages = [ordered]@{}
    foreach ($name in $script:OrderedStages) {
        $path = Join-Path $script:StateDirectory ($name + ".json")
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            $stages[$name] = [IO.File]::ReadAllText($path, [Text.Encoding]::UTF8) | ConvertFrom-Json
        }
    }
    $runStatus = $CurrentState.status
    if ($Stage -ne "finalize" -and $runStatus -eq "succeeded") {
        $runStatus = "ready"
    }
    Write-JsonAtomic -Path (Join-Path $script:StateDirectory "run-state.json") -Value ([ordered]@{
        schema_version = "nx-ug-thermal-run-state/1"
        run_id = $script:RunId
        status = $runStatus
        current_stage = $Stage
        pid = $CurrentState.pid
        updated_at = [DateTime]::UtcNow.ToString("o")
        stages = $stages
    })
}

function Write-StageState {
    param(
        [Parameter(Mandatory = $true)][string]$Status,
        [AllowNull()][Nullable[int]]$ExitCode,
        [AllowNull()][string]$ErrorSummary,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][array]$Artifacts
    )
    $finishedAt = $null
    if ($Status -ne "running") {
        $finishedAt = [DateTime]::UtcNow.ToString("o")
    }
    if ($ErrorSummary -and $ErrorSummary.Length -gt 4000) {
        $ErrorSummary = $ErrorSummary.Substring(0, 4000)
    }
    $state = [ordered]@{
        schema_version = "nx-ug-thermal-stage-state/1"
        run_id = $script:RunId
        stage = $Stage
        status = $Status
        pid = $PID
        started_at = $script:StartedAt
        finished_at = $finishedAt
        exit_code = $ExitCode
        error_summary = $ErrorSummary
        artifacts = $Artifacts
    }
    Write-JsonAtomic -Path $script:StatePath -Value $state
    Update-RunState -CurrentState $state
}

function Assert-File {
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string]$Label)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label is missing: $Path"
    }
    if ((Get-Item -LiteralPath $Path).Length -le 0) {
        throw "$Label is empty: $Path"
    }
}

function Assert-Predecessors {
    $stageIndex = [Array]::IndexOf($script:OrderedStages, $Stage)
    for ($index = 0; $index -lt $stageIndex; $index++) {
        $name = $script:OrderedStages[$index]
        $path = Join-Path $script:StateDirectory ($name + ".json")
        Assert-File -Path $path -Label "Predecessor state"
        $state = [IO.File]::ReadAllText($path, [Text.Encoding]::UTF8) | ConvertFrom-Json
        if ($state.status -ne "succeeded") {
            throw "Stage $Stage requires predecessor $name to have status succeeded"
        }
    }
}

function Invoke-NxJournal {
    param(
        [Parameter(Mandatory = $true)][string]$JournalName,
        [Parameter(Mandatory = $true)]$Task,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds
    )
    $journal = Join-Path $script:PackageRoot ("journals\" + $JournalName + ".py")
    $taskPath = Join-Path $script:StateDirectory ($Stage + "-task.json")
    $resultPath = Join-Path $script:StateDirectory ($Stage + "-result.json")
    $stdoutPath = Join-Path $script:RunRoot ("logs\" + $Stage + ".stdout.log")
    $stderrPath = Join-Path $script:RunRoot ("logs\" + $Stage + ".stderr.log")
    $Task["result_path"] = $resultPath
    Write-JsonAtomic -Path $taskPath -Value $Task
    Assert-File -Path $journal -Label "NX journal"
    $env:UGII_BASE_DIR = $script:NxBase
    $env:UGII_TMP_DIR = Join-Path $script:RunRoot "tmp"
    $env:PATH = (Split-Path -Parent $script:RunJournal) + ";" + $env:PATH
    $env:NX_THERMAL_TASK = $taskPath
    New-Item -ItemType Directory -Path $env:UGII_TMP_DIR -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $script:RunRoot "logs") -Force | Out-Null
    $process = Start-Process -FilePath $script:RunJournal -ArgumentList @('"' + $journal + '"') -WorkingDirectory $script:RunRoot -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        try { $process.Kill() } catch {}
        throw [TimeoutException]::new("NX journal $JournalName exceeded $TimeoutSeconds seconds")
    }
    $process.Refresh()
    Assert-File -Path $resultPath -Label "NX journal result"
    $result = [IO.File]::ReadAllText($resultPath, [Text.Encoding]::UTF8) | ConvertFrom-Json
    if (-not $result.ok) {
        throw "NX journal $JournalName reported failure: $($result.error)"
    }
    if ($null -ne $process.ExitCode -and $process.ExitCode -ne 0) {
        throw "NX journal $JournalName exited with code $($process.ExitCode); inspect $stdoutPath and $stderrPath"
    }
    return $result
}

function Invoke-Preflight {
    $externalManifestPath = Join-Path $script:RunRoot "run-manifest.json"
    $packageManifestPath = Join-Path $script:PackageRoot "run-manifest.json"
    $payloadPath = Join-Path $script:RunRoot "payload.zip"
    Assert-File -Path $externalManifestPath -Label "External run manifest"
    Assert-File -Path $packageManifestPath -Label "Package run manifest"
    Assert-File -Path $payloadPath -Label "Frozen payload"
    $external = [IO.File]::ReadAllText($externalManifestPath, [Text.Encoding]::UTF8) | ConvertFrom-Json
    $manifest = [IO.File]::ReadAllText($packageManifestPath, [Text.Encoding]::UTF8) | ConvertFrom-Json
    if ($external.run_id -ne $script:RunId -or $manifest.run_id -ne $script:RunId) {
        throw "Run manifest id does not match directory name"
    }
    if ($manifest.mcp_server -ne "windows-powershell") {
        throw "Unexpected MCP server in frozen package"
    }
    if ($manifest.workspace_dir -ne $external.workspace_dir -or $manifest.download_dir -ne $external.download_dir -or $manifest.run_journal -ne $external.run_journal) {
        throw "Frozen Windows runtime configuration differs from the external manifest"
    }
    if ([IO.Path]::GetFullPath($manifest.windows_run_root).TrimEnd("\") -ne $script:RunRoot) {
        throw "Windows run directory differs from frozen manifest"
    }
    $payloadRecord = Get-FileRecord -Path $payloadPath
    if ($payloadRecord.sha256 -ne $external.payload.sha256 -or $payloadRecord.size_bytes -ne [int64]$external.payload.size_bytes) {
        throw "Frozen payload hash or size differs from Linux manifest"
    }
    $expected = @{}
    foreach ($record in $manifest.payload_entries) {
        $relative = [string]$record.path
        if ([IO.Path]::IsPathRooted($relative) -or $relative.Contains("..")) {
            throw "Unsafe package manifest path: $relative"
        }
        $path = Join-Path $script:PackageRoot ($relative.Replace("/", "\"))
        Assert-File -Path $path -Label "Frozen package entry"
        $actual = Get-FileRecord -Path $path
        if ($actual.sha256 -ne $record.sha256 -or $actual.size_bytes -ne [int64]$record.size_bytes) {
            throw "Frozen package entry failed verification: $relative"
        }
        $expected[$relative.ToLowerInvariant()] = $true
    }
    foreach ($file in Get-ChildItem -LiteralPath $script:PackageRoot -Recurse -File) {
        $relative = $file.FullName.Substring($script:PackageRoot.Length + 1).Replace("\", "/").ToLowerInvariant()
        if ($relative -ne "run-manifest.json" -and -not $expected.ContainsKey($relative)) {
            throw "Unexpected file in frozen package: $relative"
        }
    }
    foreach ($path in @(
        $script:RunJournal,
        (Join-Path $script:NxBase "SIMULATION\templates\FemSpaceSysThermalMetric.fem"),
        (Join-Path $script:NxBase "SIMULATION\templates\SimSpaceSysThermalMetric.sim")
    )) {
        Assert-File -Path $path -Label "NX12 runtime dependency"
    }
    $artifacts = @()
    $artifacts += Get-FileRecord -Path $payloadPath
    $artifacts += Get-FileRecord -Path $script:RunJournal
    return $artifacts
}

function Invoke-Model {
    $nxDirectory = Join-Path $script:RunRoot "nx"
    New-Item -ItemType Directory -Path $nxDirectory -Force | Out-Null
    $task = [ordered]@{
        run_dir = $script:RunRoot
        semantics_path = Join-Path $script:PackageRoot "inputs\geometry-semantics.json"
        geometry_manifest_path = Join-Path $script:PackageRoot "cad\geometry_manifest.json"
        output_part = Join-Path $nxDirectory "geometry_master.prt"
        nx_base = $script:NxBase
        mode = "build"
    }
    Invoke-NxJournal -JournalName "build_geometry_master" -Task $task -TimeoutSeconds 1800 | Out-Null
    $artifacts = @()
    $artifacts += Get-FileRecord -Path (Join-Path $nxDirectory "geometry_master.prt")
    $artifacts += Get-FileRecord -Path (Join-Path $nxDirectory "nx_binding.json")
    return $artifacts
}

function Invoke-Solve {
    $nxDirectory = Join-Path $script:RunRoot "nx"
    $rawDirectory = Join-Path $nxDirectory "raw_results"
    New-Item -ItemType Directory -Path $rawDirectory -Force | Out-Null
    $task = [ordered]@{
        run_dir = $script:RunRoot
        semantics_path = Join-Path $script:PackageRoot "inputs\geometry-semantics.json"
        thermal_case_path = Join-Path $script:PackageRoot "inputs\thermal-case.json"
        nx_binding_path = Join-Path $nxDirectory "nx_binding.json"
        master_part = Join-Path $nxDirectory "geometry_master.prt"
        fem_path = Join-Path $nxDirectory "case.fem"
        sim_path = Join-Path $nxDirectory "thermal_case.sim"
        raw_results_dir = $rawDirectory
        nx_base = $script:NxBase
        solver_timeout_seconds = 7600
    }
    Invoke-NxJournal -JournalName "build_and_solve" -Task $task -TimeoutSeconds 7800 | Out-Null
    $bunPath = Join-Path $nxDirectory "thermal_case-steady_state_thermal.bun"
    $artifacts = @()
    $artifacts += Get-FileRecord -Path (Join-Path $nxDirectory "geometry_master.prt")
    $artifacts += Get-FileRecord -Path (Join-Path $nxDirectory "case.fem")
    $artifacts += Get-FileRecord -Path (Join-Path $nxDirectory "thermal_case.sim")
    $artifacts += Get-FileRecord -Path $bunPath
    $artifacts += Get-FileRecord -Path (Join-Path $nxDirectory "solve_report.json")
    return $artifacts
}

function Invoke-Extract {
    $nxDirectory = Join-Path $script:RunRoot "nx"
    $resultDirectory = Join-Path $script:RunRoot "results"
    New-Item -ItemType Directory -Path $resultDirectory -Force | Out-Null
    $task = [ordered]@{
        run_dir = $script:RunRoot
        sim_path = Join-Path $nxDirectory "thermal_case.sim"
        raw_results_dir = Join-Path $nxDirectory "raw_results"
        thermal_case_path = Join-Path $script:PackageRoot "inputs\thermal-case.json"
        output_dir = $resultDirectory
        solution_name = "steady_state_thermal"
        analysis_mode = "steady"
    }
    Invoke-NxJournal -JournalName "extract_results" -Task $task -TimeoutSeconds 1800 | Out-Null
    $artifacts = @()
    foreach ($name in @("native.vtu", "summary.json", "energy_balance.json", "solver-log-summary.txt")) {
        $artifacts += Get-FileRecord -Path (Join-Path $resultDirectory $name)
    }
    $artifacts += Get-FileRecord -Path (Join-Path $nxDirectory "raw_results\nodes.csv")
    $artifacts += Get-FileRecord -Path (Join-Path $nxDirectory "raw_results\elements.csv")
    return $artifacts
}

function Invoke-Finalize {
    $resultDirectory = Join-Path $script:RunRoot "results"
    $returnDirectory = Join-Path $script:RunRoot "return"
    if (Test-Path -LiteralPath $returnDirectory) {
        throw "Return directory already exists; refusing to overwrite finalized results"
    }
    New-Item -ItemType Directory -Path $returnDirectory | Out-Null
    $sourceManifestPath = Join-Path $script:RunRoot "run-manifest.json"
    $sourceManifest = [IO.File]::ReadAllText($sourceManifestPath, [Text.Encoding]::UTF8) | ConvertFrom-Json
    $stageStates = @()
    foreach ($name in @("preflight", "model", "solve", "extract")) {
        $stageStatePath = Join-Path $script:StateDirectory ($name + ".json")
        $stageStates += [IO.File]::ReadAllText($stageStatePath, [Text.Encoding]::UTF8) | ConvertFrom-Json
    }
    $nxArtifacts = @()
    foreach ($name in @("geometry_master.prt", "case.fem", "thermal_case.sim", "thermal_case-steady_state_thermal.bun")) {
        $nxArtifacts += Get-FileRecord -Path (Join-Path $script:RunRoot ("nx\" + $name))
    }
    $completedManifest = [ordered]@{
        schema_version = "nx-ug-thermal-completed-run/1"
        run_id = $script:RunId
        analysis_type = "steady_state"
        solution_name = "steady_state_thermal"
        completed_at = [DateTime]::UtcNow.ToString("o")
        source_run_manifest = $sourceManifest
        stages = $stageStates
        nx_artifacts = $nxArtifacts
    }
    Write-JsonAtomic -Path (Join-Path $resultDirectory "run-manifest.json") -Value $completedManifest
    $required = @("native.vtu", "summary.json", "energy_balance.json", "run-manifest.json", "solver-log-summary.txt")
    foreach ($name in $required) {
        $source = Join-Path $resultDirectory $name
        Assert-File -Path $source -Label "Required result"
        Copy-Item -LiteralPath $source -Destination (Join-Path $returnDirectory $name)
    }
    $records = @()
    foreach ($name in $required) {
        $record = Get-FileRecord -Path (Join-Path $returnDirectory $name)
        $record.path = $name
        $records += $record
    }
    Write-JsonAtomic -Path (Join-Path $returnDirectory "return-manifest.json") -Value ([ordered]@{
        schema_version = "nx-ug-thermal-return-manifest/1"
        run_id = $script:RunId
        files = $records
    })
    New-Item -ItemType Directory -Path ([string]$sourceManifest.download_dir) -Force | Out-Null
    $bundlePath = Join-Path ([string]$sourceManifest.download_dir) ($script:RunId + "-return.zip")
    if (Test-Path -LiteralPath $bundlePath) {
        throw "Return download bundle already exists; refusing to overwrite it"
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::CreateFromDirectory(
        $returnDirectory,
        $bundlePath,
        [IO.Compression.CompressionLevel]::Optimal,
        $false
    )
    Assert-File -Path $bundlePath -Label "Return download bundle"
    if ((Get-Item -LiteralPath $bundlePath).Length -gt 10737418240) {
        throw "Return download bundle exceeds the MCP 10 GiB download limit"
    }
    $artifacts = @($records)
    $artifacts += Get-FileRecord -Path (Join-Path $returnDirectory "return-manifest.json")
    $artifacts += Get-FileRecord -Path $bundlePath
    return $artifacts
}

$lockDirectory = Join-Path $script:StateDirectory "locks"
New-Item -ItemType Directory -Path $lockDirectory -Force | Out-Null
$lockPath = Join-Path $lockDirectory ($Stage + ".lock")
$lockStream = $null
try {
    $lockStream = [IO.File]::Open($lockPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    $lockWriter = New-Object IO.StreamWriter($lockStream)
    $lockWriter.WriteLine($PID)
    $lockWriter.Flush()
    Assert-Predecessors
    Write-StageState -Status "running" -ExitCode $null -ErrorSummary $null -Artifacts @()
    $artifacts = switch ($Stage) {
        "preflight" { Invoke-Preflight }
        "model" { Invoke-Model }
        "solve" { Invoke-Solve }
        "extract" { Invoke-Extract }
        "finalize" { Invoke-Finalize }
    }
    Write-StageState -Status "succeeded" -ExitCode 0 -ErrorSummary $null -Artifacts @($artifacts)
    exit 0
}
catch [TimeoutException] {
    Write-StageState -Status "timed_out" -ExitCode 124 -ErrorSummary $_.Exception.Message -Artifacts @()
    exit 124
}
catch {
    Write-StageState -Status "failed" -ExitCode 1 -ErrorSummary ($_ | Out-String) -Artifacts @()
    exit 1
}
finally {
    if ($null -ne $lockStream) {
        $lockStream.Dispose()
    }
}
