# Install a current-user, interactive-login recovery task. No password/elevation.
# .\install-startup.ps1 -WhatIf previews it. -Remove disables and uninstalls it.
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$Remove,
    [switch]$NoStart
)

$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$taskHasher = [Security.Cryptography.SHA256]::Create()
try {
    $taskDigest = $taskHasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($taskRoot.ToLowerInvariant()))
} finally { $taskHasher.Dispose() }
$taskSuffix = ([BitConverter]::ToString($taskDigest) -replace '-', '').Substring(0, 12).ToLowerInvariant()
$taskName = "ReviewerItemNotifierRecovery-$taskSuffix"
$taskExisting = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
$taskIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
if ($taskExisting) {
    $taskOwnerMatches = $taskExisting.Principal.UserId -in @($taskIdentity.Name, $taskIdentity.User.Value)
    if (-not $taskOwnerMatches) {
        try {
            # Task Scheduler may normalize MACHINE\User to the short User name.
            $taskOwnerAccount = [Security.Principal.NTAccount]::new([string]$taskExisting.Principal.UserId)
            $taskOwnerSid = $taskOwnerAccount.Translate([Security.Principal.SecurityIdentifier])
            $taskOwnerMatches = $taskOwnerSid.Value -eq $taskIdentity.User.Value
        } catch { $taskOwnerMatches = $false }
    }
    if (-not $taskOwnerMatches) {
        throw 'The existing task belongs to another Windows user; it was left unchanged.'
    }
}

if ($Remove) {
    if ($taskExisting -and $PSCmdlet.ShouldProcess($taskName, 'Stop and unregister current-user notifier recovery')) {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }
    return
}

$taskPython = Join-Path $taskRoot '.venv\Scripts\pythonw.exe'
$taskScript = Join-Path $taskRoot 'src\browser_recovery.py'
if (-not (Test-Path -LiteralPath $taskPython) -or -not (Test-Path -LiteralPath $taskScript)) {
    throw 'Create the project .venv and verify src/browser_recovery.py before installing startup recovery.'
}
$taskArguments = '"' + $taskScript + '" --root "' + $taskRoot + '"'
$taskAction = New-ScheduledTaskAction -Execute $taskPython -Argument $taskArguments -WorkingDirectory $taskRoot
$taskTrigger = New-ScheduledTaskTrigger -AtLogOn -User $taskIdentity.Name
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $taskIdentity.Name -LogonType Interactive -RunLevel Limited
$taskSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$taskDefinition = New-ScheduledTask -Action $taskAction -Trigger $taskTrigger `
    -Principal $taskPrincipal -Settings $taskSettings `
    -Description 'Keep the local reviewer notifier running and recover Chrome when its relay heartbeat is missing.'
if ($PSCmdlet.ShouldProcess($taskName, 'Register notifier recovery at current-user login')) {
    Register-ScheduledTask -TaskName $taskName -InputObject $taskDefinition -Force | Out-Null
    if (-not $NoStart) { Start-ScheduledTask -TaskName $taskName }
    Write-Output "Installed $taskName. Logs: data/browser-recovery.log. Remove with .\install-startup.ps1 -Remove."
}
