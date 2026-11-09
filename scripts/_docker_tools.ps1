# Docker helpers for osmium / osm2pgsql when local tools are not installed.

function Test-DockerAvailable {
    return [bool](Get-Command docker -ErrorAction SilentlyContinue)
}

function Test-DockerDaemon {
    if (-not (Test-DockerAvailable)) {
        return $false
    }

    docker info 2>$null | Out-Null
    return $LASTEXITCODE -eq 0
}

function Convert-DockerMountPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $resolved = (Resolve-Path -LiteralPath $Path).Path
    if ($IsWindows -or $env:OS -match "Windows") {
        # Docker Desktop on Windows accepts C:\path mounts.
        return $resolved
    }

    return $resolved
}

function Ensure-ComposeDatabase {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot
    )

    Push-Location $ProjectRoot
    try {
        $status = docker compose ps db --format json 2>$null | ConvertFrom-Json
        $running = @($status | Where-Object { $_.State -eq "running" }).Count -gt 0
        if (-not $running) {
            Write-Host "Starting PostGIS container (docker compose up -d db)..."
            docker compose up -d db | Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw "Failed to start PostGIS via docker compose."
            }
        }

        Write-Host "Waiting for PostGIS to become healthy..."
        $deadline = (Get-Date).AddMinutes(3)
        while ((Get-Date) -lt $deadline) {
            $health = docker compose ps db --format "{{.Health}}" 2>$null
            if ($health -match "healthy") {
                return
            }
            Start-Sleep -Seconds 2
        }

        throw "PostGIS container did not become healthy in time. Check: docker compose logs db"
    } finally {
        Pop-Location
    }
}

function Get-ComposeNetworkName {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot
    )

    Push-Location $ProjectRoot
    try {
        $containerId = docker compose ps -q db 2>$null
        if (-not $containerId) {
            throw "PostGIS container is not running. Run: docker compose up -d db"
        }

        return docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}' $containerId
    } finally {
        Pop-Location
    }
}

function Invoke-OsmiumExtractDocker {
    param(
        [Parameter(Mandatory = $true)][string]$SourcePath,
        [Parameter(Mandatory = $true)][string]$OutputPath,
        [Parameter(Mandatory = $true)][string]$Bbox
    )

    if (-not (Test-DockerAvailable)) {
        throw "Docker is not installed."
    }

    if (-not (Test-DockerDaemon)) {
        throw @"
Docker is installed but not running.

Start Docker Desktop, wait until it is ready, then re-run this script.
"@
    }

    $sourceDir = Convert-DockerMountPath -Path (Split-Path $SourcePath -Parent)
    $outputDir = Convert-DockerMountPath -Path (Split-Path $OutputPath -Parent)
    $sourceName = Split-Path $SourcePath -Leaf
    $outputName = Split-Path $OutputPath -Leaf

    $images = @(
        "mschilde/osmium-tool:latest",
        "stefda/osmium-tool:latest"
    )

    $lastError = $null
    foreach ($image in $images) {
        Write-Host "Using Docker ($image) to extract bbox..."

        docker run --rm `
            -v "${sourceDir}:/data/in:ro" `
            -v "${outputDir}:/data/out" `
            $image `
            osmium extract --bbox $Bbox -o "/data/out/$outputName" --overwrite "/data/in/$sourceName"

        if ($LASTEXITCODE -eq 0) {
            return
        }

        $lastError = $LASTEXITCODE
        Write-Warning "Docker extract failed with $image (exit code $LASTEXITCODE). Trying next image..."
    }

    throw "Docker osmium extract failed with exit code $lastError. Tried: $($images -join ', ')"
}

function Invoke-OsmImportDocker {
    param(
        [Parameter(Mandatory = $true)][string]$PbfPath,
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [string]$DbName = $env:POSTGRES_DB,
        [string]$DbUser = $env:POSTGRES_USER,
        [string]$DbPassword = $env:POSTGRES_PASSWORD,
        [int]$CacheMb = 512,
        [string]$DurationHint = "a few minutes"
    )

    if (-not (Test-DockerAvailable)) {
        throw "Docker is not installed."
    }

    if (-not (Test-DockerDaemon)) {
        throw @"
Docker is installed but not running.

Start Docker Desktop, wait until it is ready, then re-run this script.
"@
    }

    if (-not $DbName) { $DbName = "spacesyntax" }
    if (-not $DbUser) { $DbUser = "spacesyntax" }
    if (-not $DbPassword) { $DbPassword = "spacesyntax" }

    Ensure-ComposeDatabase -ProjectRoot $ProjectRoot
    $network = Get-ComposeNetworkName -ProjectRoot $ProjectRoot

    $pbfDir = Convert-DockerMountPath -Path (Split-Path $PbfPath -Parent)
    $pbfName = Split-Path $PbfPath -Leaf
    $initSql = Join-Path $ProjectRoot "scripts\init_db.sql"

    Write-Host "Importing OSM data from: $PbfPath"
    Write-Host "Target database: $DbName @ db (docker network: $network)"
    Write-Host "Estimated duration: $DurationHint"
    Write-Host "Using Docker (osm2pgsql)..."
    Write-Host ""

    docker run --rm `
        --network $network `
        -e PGPASSWORD=$DbPassword `
        -v "${pbfDir}:/data:ro" `
        iboates/osm2pgsql:latest `
        osm2pgsql `
            -H db `
            -U $DbUser `
            -d $DbName `
            --create `
            --slim `
            --hstore `
            --latlong `
            -C $CacheMb `
            "/data/$pbfName"

    if ($LASTEXITCODE -ne 0) {
        throw "Docker osm2pgsql import failed with exit code $LASTEXITCODE"
    }

    Write-Host ""
    Write-Host "Import complete. Applying indexes..."

    Get-Content -LiteralPath $initSql -Raw | docker compose -f (Join-Path $ProjectRoot "docker-compose.yml") exec -T db `
        psql -U $DbUser -d $DbName

    if ($LASTEXITCODE -ne 0) {
        throw "init_db.sql failed via docker compose exec."
    }

    Write-Host "Done. Database is ready for analysis."
}

function Get-LocalToolOrDockerHint {
    param(
        [Parameter(Mandatory = $true)][string]$ToolName,
        [Parameter(Mandatory = $true)][string]$DockerFallbackDescription
    )

    if (Test-DockerAvailable) {
        if (Test-DockerDaemon) {
            return "Docker is available. Re-run this script and $DockerFallbackDescription automatically."
        }
        return "Docker is installed but not running. Start Docker Desktop, then re-run this script."
    }

    return @"
Install options:
  - Docker Desktop (recommended): start Docker, then re-run this script
  - Chocolatey: choco install $ToolName
  - Manual download: see osmcode.org/osmium-tool or osm2pgsql.org/doc/install.html
"@
}
