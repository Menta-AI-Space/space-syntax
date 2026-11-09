# Shared osm2pgsql import logic. Dot-source from import_osm.ps1 / import_osm_full.ps1.

function Resolve-ProjectPath {
    param(
        [string]$Path,
        [string]$ProjectRoot
    )

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return $null
    }

    if ([System.IO.Path]::IsPathRooted($Path)) {
        return $Path
    }

    return (Join-Path $ProjectRoot $Path)
}

function Get-DatabaseDefaults {
    param(
        [string]$DbHost = "localhost",
        [string]$DbName = $env:POSTGRES_DB,
        [string]$DbUser = $env:POSTGRES_USER,
        [string]$DbPassword = $env:POSTGRES_PASSWORD
    )

    if (-not $DbName) { $DbName = "spacesyntax" }
    if (-not $DbUser) { $DbUser = "spacesyntax" }
    if (-not $DbPassword) { $DbPassword = "spacesyntax" }

    return @{
        DbHost     = $DbHost
        DbName     = $DbName
        DbUser     = $DbUser
        DbPassword = $DbPassword
    }
}

function Invoke-OsmImport {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PbfPath,

        [string]$ProjectRoot = (Split-Path $PSScriptRoot -Parent),
        [string]$DbHost = "localhost",
        [string]$DbName = $env:POSTGRES_DB,
        [string]$DbUser = $env:POSTGRES_USER,
        [string]$DbPassword = $env:POSTGRES_PASSWORD,

        [int]$CacheMb = 512,
        [string]$DurationHint = "a few minutes",
        [switch]$ForceDocker
    )

    $ErrorActionPreference = "Stop"

    if (-not (Test-Path $PbfPath)) {
        throw "PBF file not found: $PbfPath"
    }

    . (Join-Path $PSScriptRoot "_docker_tools.ps1")

    $osm2pgsql = Get-Command osm2pgsql -ErrorAction SilentlyContinue
    if ($ForceDocker -or (-not $osm2pgsql -and (Test-DockerDaemon))) {
        Invoke-OsmImportDocker `
            -PbfPath $PbfPath `
            -ProjectRoot $ProjectRoot `
            -DbName $DbName `
            -DbUser $DbUser `
            -DbPassword $DbPassword `
            -CacheMb $CacheMb `
            -DurationHint $DurationHint
        return
    }

    if (-not $osm2pgsql) {
        $hint = Get-LocalToolOrDockerHint -ToolName "osm2pgsql" -DockerFallbackDescription "Docker will be used"
        if ((Test-DockerAvailable) -and -not (Test-DockerDaemon)) {
            $hint = @"
Docker is installed but not running.

Start Docker Desktop, wait until it is ready, then re-run:
  .\scripts\import_osm.ps1
"@
        }
        throw "osm2pgsql not found in PATH.`n`n$hint"
    }

    $db = Get-DatabaseDefaults -DbHost $DbHost -DbName $DbName -DbUser $DbUser -DbPassword $DbPassword

    Write-Host "Importing OSM data from: $PbfPath"
    Write-Host "Target database: $($db.DbName) @ $($db.DbHost) (user: $($db.DbUser))"
    Write-Host "Estimated duration: $DurationHint"
    Write-Host ""

    $env:PGPASSWORD = $db.DbPassword

    & osm2pgsql `
        -d $db.DbName `
        -H $db.DbHost `
        -U $db.DbUser `
        --create `
        --slim `
        --hstore `
        --latlong `
        -C $CacheMb `
        $PbfPath

    if ($LASTEXITCODE -ne 0) {
        throw "osm2pgsql import failed with exit code $LASTEXITCODE"
    }

    Write-Host ""
    Write-Host "Import complete. Applying indexes..."
    $initSql = Join-Path $PSScriptRoot "init_db.sql"
    & psql -h $db.DbHost -U $db.DbUser -d $db.DbName -f $initSql

    if ($LASTEXITCODE -ne 0) {
        throw "init_db.sql failed. Run manually: psql -h $($db.DbHost) -U $($db.DbUser) -d $($db.DbName) -f $initSql"
    }

    Write-Host "Done. Database is ready for analysis."
}
