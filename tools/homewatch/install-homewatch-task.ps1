# Installs the alert server watch heartbeat as a SYSTEM scheduled task that starts at boot, before sign-in.
# Run once as administrator, after deploy-homewatch.sh saved data\homewatch.key:
#   powershell -ExecutionPolicy Bypass -File tools\homewatch\install-homewatch-task.ps1
# The script and key are copied to %ProgramData%\homewatch, writable only by SYSTEM and
# Administrators, so the SYSTEM task never runs a file a normal user can edit.
$ErrorActionPreference = 'Stop'
$repo = Resolve-Path (Join-Path $PSScriptRoot '..\..')
$keyFile = Join-Path $repo 'data\homewatch.key'
$key = (Get-Content -LiteralPath $keyFile -Raw).Trim()
if ($key -notmatch '^[0-9a-f]{64}$') { throw "bad key in $keyFile" }

$dir = Join-Path $env:ProgramData 'homewatch'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
icacls $dir /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'homewatch-beat.ps1') -Destination $dir -Force
Set-Content -LiteralPath (Join-Path $dir 'key.txt') -Value $key -NoNewline -Encoding ascii

$name = 'Reviewer homewatch heartbeat'
# A running loop keeps the old script in memory: stop it so the new copy starts below.
if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) { Stop-ScheduledTask -TaskName $name }
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$dir\homewatch-beat.ps1`""
# At boot, plus every 5 minutes so a crashed loop comes back (IgnoreNew skips it while running).
$triggers = @(
    (New-ScheduledTaskTrigger -AtStartup),
    (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5))
)
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $name -Action $action -Trigger $triggers -Principal $principal `
    -Settings $settings -Description 'Posts a heartbeat to the Hetzner alert server watch (reviewer alerts).' -Force | Out-Null
Start-ScheduledTask -TaskName $name
Start-Sleep -Seconds 20
Get-Content -LiteralPath (Join-Path $dir 'last.txt') -ErrorAction SilentlyContinue
(Get-ScheduledTask -TaskName $name).State
