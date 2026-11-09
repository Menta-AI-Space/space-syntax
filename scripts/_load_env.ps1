# Load project .env into process environment variables.
param(
    [string]$ProjectRoot = (Split-Path $PSScriptRoot -Parent)
)

$envFile = Join-Path $ProjectRoot ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*([^#][^=]+)=(.*)$') {
            $name = $matches[1].Trim()
            $value = $matches[2].Trim()
            Set-Item -Path "env:$name" -Value $value
        }
    }
}

return @{
    ProjectRoot = $ProjectRoot
    EnvFile     = $envFile
}
