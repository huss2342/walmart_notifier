# Start Docker/notifier and keep the signed-in Chrome relay recoverable.
# .\start-docker.ps1 -Once -DryRun previews the current recovery decision.
[CmdletBinding()]
param(
    [switch]$Once,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    $taskPythonCommand = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue
    if (-not $taskPythonCommand) { throw 'Python is required for host recovery. Create the project .venv first.' }
    $taskPython = $taskPythonCommand.Source
}
$taskArguments = @((Join-Path $PSScriptRoot 'src\browser_recovery.py'), '--root', $PSScriptRoot)
if ($Once) { $taskArguments += '--once' }
if ($DryRun) { $taskArguments += '--dry-run' }
& $taskPython @taskArguments
exit $LASTEXITCODE
