# Full/regional OSM import — South Korea or other large PBF (30–90+ minutes)
#
# Usage:
#   1. Set OSM_PBF_FULL_PATH in .env (or pass -PbfPath)
#   2. docker compose up -d db
#   3. .\scripts\import_osm_full.ps1
#
# For MVP testing with a small extract, use .\scripts\import_osm.ps1 instead.

param(
    [string]$PbfPath = $env:OSM_PBF_FULL_PATH,
    [string]$DbHost = "localhost",
    [int]$CacheMb = 4096
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "_load_env.ps1") | Out-Null
$projectRoot = Split-Path $PSScriptRoot -Parent

. (Join-Path $PSScriptRoot "_import_osm.ps1")

if (-not $PbfPath) { $PbfPath = $env:OSM_PBF_FULL_PATH }
if (-not $PbfPath) {
    $PbfPath = "C:\Users\cmdys\Downloads\south-korea-latest.osm.pbf"
}

$PbfPath = Resolve-ProjectPath -Path $PbfPath -ProjectRoot $projectRoot

if (-not (Test-Path $PbfPath)) {
    throw @"
Full PBF not found: $PbfPath

Download South Korea extract from Geofabrik:
  https://download.geofabrik.de/asia/south-korea-latest.osm.pbf

Then set OSM_PBF_FULL_PATH in .env or pass -PbfPath.
"@
}

Write-Host "WARNING: This imports the full/regional dataset and may take 30–90+ minutes."
Write-Host ""

Invoke-OsmImport `
    -PbfPath $PbfPath `
    -ProjectRoot $projectRoot `
    -DbHost $DbHost `
    -CacheMb $CacheMb `
    -DurationHint "30–90+ minutes for full South Korea data"
