# MVP OSM import — 서대문구 sample area (default: data/osm/seoul-small.osm.pbf)
#
# Usage:
#   1. Copy .env.example to .env
#   2. docker compose up -d db
#   3. .\scripts\prepare_sample_osm.ps1   # first time only, if sample PBF missing
#   4. .\scripts\import_osm.ps1
#
# For full South Korea import, use .\scripts\import_osm_full.ps1 instead.

param(
    [string]$PbfPath = $env:OSM_PBF_PATH,
    [string]$DbHost = "localhost",
    [int]$CacheMb = 512
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "_load_env.ps1") | Out-Null
$projectRoot = Split-Path $PSScriptRoot -Parent

. (Join-Path $PSScriptRoot "_import_osm.ps1")

if (-not $PbfPath) { $PbfPath = $env:OSM_PBF_PATH }
if (-not $PbfPath) { $PbfPath = "data/osm/seoul-small.osm.pbf" }

$PbfPath = Resolve-ProjectPath -Path $PbfPath -ProjectRoot $projectRoot

if (-not (Test-Path $PbfPath)) {
    throw @"
Sample PBF not found: $PbfPath

Create it from a full/regional PBF:
  .\scripts\prepare_sample_osm.ps1

Or set OSM_PBF_PATH in .env to an existing small PBF file.
"@
}

Invoke-OsmImport `
    -PbfPath $PbfPath `
    -ProjectRoot $projectRoot `
    -DbHost $DbHost `
    -CacheMb $CacheMb `
    -DurationHint "a few minutes for the MVP sample extract"
