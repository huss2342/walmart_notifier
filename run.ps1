# Start the notifier. Reads settings from notifier.env if it exists.
#
#   .\run.ps1              normal operation
#   .\run.ps1 -Seed        record what is on the page without alerting
#   .\run.ps1 -Verbose     log every relay, not just the ones that alert
#
# Leave the window open. Ctrl-C to stop.

[CmdletBinding()]
param(
    [switch]$Seed,
    [int]$Port = 0
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

# --- settings ---------------------------------------------------------------

$envFile = Join-Path $root 'notifier.env'
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith('#')) {
            $name, $value = $line -split '=', 2
            if ($name -and $null -ne $value) {
                Set-Item -Path "env:$($name.Trim())" -Value $value.Trim().Trim('"')
            }
        }
    }
    Write-Host "Loaded settings from notifier.env"
} else {
    Write-Host "No notifier.env found. Copy notifier.example.env to notifier.env and set NTFY_TOPIC." -ForegroundColor Yellow
}

if ($Seed) { $env:SEED_MODE = 'true' }

if (-not $env:NTFY_TOPIC -and $env:NOTIFY_PROVIDER -ne 'pushover' -and $env:NOTIFY_PROVIDER -ne 'telegram') {
    Write-Host "NTFY_TOPIC is not set - nothing will reach your phone." -ForegroundColor Red
    Write-Host "Generate one with:  python -c `"import secrets; print(secrets.token_hex(16))`"" -ForegroundColor Red
}

# --- python -----------------------------------------------------------------

$venvPython = Join-Path $root '.venv\Scripts\python.exe'
if (Test-Path $venvPython) {
    $python = $venvPython
} else {
    $python = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $python) { throw "python not found on PATH" }
}

$serverArgs = @((Join-Path $root 'src\server.py'))
if ($Port -gt 0) { $serverArgs += @('--port', $Port) }
if ($VerbosePreference -ne 'SilentlyContinue') { $serverArgs += '--verbose' }

& $python $serverArgs
