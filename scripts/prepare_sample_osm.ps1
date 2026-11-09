# Extract 서대문구 (Seodaemun-gu) bbox from a larger OSM PBF for MVP testing.
#
# Usage:
#   1. Set OSM_PBF_FULL_PATH in .env to your full/regional PBF
#   2. .\scripts\prepare_sample_osm.ps1
#   3. .\scripts\import_osm.ps1
#
# Uses local osmium if installed; otherwise Docker (no Chocolatey required).

param(
    [string]$SourcePath = $env:OSM_PBF_FULL_PATH,
    [string]$OutputPath = $env:OSM_PBF_PATH,
    [string]$Bbox = "126.900,37.553,126.972,37.613",  # OSM relation 2419951 (서대문구) + ~200m pad
    [switch]$ForceDocker
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "_load_env.ps1") | Out-Null
$projectRoot = Split-Path $PSScriptRoot -Parent

. (Join-Path $PSScriptRoot "_import_osm.ps1")
. (Join-Path $PSScriptRoot "_docker_tools.ps1")

if (-not $SourcePath) { $SourcePath = $env:OSM_PBF_FULL_PATH }
if (-not $OutputPath) { $OutputPath = "data/osm/seoul-small.osm.pbf" }

$SourcePath = Resolve-ProjectPath -Path $SourcePath -ProjectRoot $projectRoot
$OutputPath = Resolve-ProjectPath -Path $OutputPath -ProjectRoot $projectRoot

$outputDir = Split-Path $OutputPath -Parent
if (-not (Test-Path $outputDir)) {
    New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
}

if (-not $SourcePath -or -not (Test-Path $SourcePath)) {
    throw @"
Source PBF not found.

Set OSM_PBF_FULL_PATH in .env to a full or regional PBF, then re-run:
  .\scripts\prepare_sample_osm.ps1

Example:
  OSM_PBF_FULL_PATH=C:\Users\you\Downloads\south-korea-latest.osm.pbf

Or pass -SourcePath explicitly:
  .\scripts\prepare_sample_osm.ps1 -SourcePath C:\path\to\south-korea-latest.osm.pbf
"@
}

Write-Host "Extracting Seodaemun-gu sample bbox: $Bbox"
Write-Host "Source: $SourcePath"
Write-Host "Output: $OutputPath"
Write-Host ""

$osmium = Get-Command osmium -ErrorAction SilentlyContinue
if ($ForceDocker -or (-not $osmium -and (Test-DockerDaemon))) {
    Invoke-OsmiumExtractDocker -SourcePath $SourcePath -OutputPath $OutputPath -Bbox $Bbox
} elseif ($osmium) {
    & osmium extract --bbox $Bbox -o $OutputPath --overwrite $SourcePath
    if ($LASTEXITCODE -ne 0) {
        throw "osmium extract failed with exit code $LASTEXITCODE"
    }
} else {
    $hint = Get-LocalToolOrDockerHint -ToolName "osmium-tool" -DockerFallbackDescription "Docker will be used"
    if ((Test-DockerAvailable) -and -not (Test-DockerDaemon)) {
        $hint = @"
Docker is installed but not running.

Start Docker Desktop, wait until it is ready, then re-run:
  .\scripts\prepare_sample_osm.ps1
"@
    }
    throw "osmium not found in PATH.`n`n$hint"
}

$sizeMb = [math]::Round((Get-Item $OutputPath).Length / 1MB, 2)
Write-Host "Done. Created $OutputPath ($sizeMb MB)"
Write-Host "Next: .\scripts\import_osm.ps1"