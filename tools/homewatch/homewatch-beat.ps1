# Heartbeat for the Hetzner alert server watch (reviewer_alerts_elite/deploy/hetzner/homewatch.py).
# Runs as SYSTEM from boot, signed in or not (install-homewatch-task.ps1 copies it to
# %ProgramData%\homewatch). Every minute it posts whether someone is signed in, whether the
# bot's /health answers, how long since the scanner's last page batch and whether channel
# sending has stalled. The server pings the owner when beats stop or a problem lasts.
$ErrorActionPreference = 'Stop'
$dir = Join-Path $env:ProgramData 'homewatch'
$key = (Get-Content -LiteralPath (Join-Path $dir 'key.txt') -Raw).Trim()
$url = 'https://178-105-83-9.sslip.io/homewatch/beat'
$headers = @{ Authorization = "Bearer $key" }
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

while ($true) {
    $started = Get-Date
    $now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
    $beat = [ordered]@{ logged_in = $false; bot_ok = $false }
    try {
        $beat.logged_in = [bool](Get-Process -Name explorer -IncludeUserName -ErrorAction SilentlyContinue |
            Where-Object { $_.UserName -and $_.UserName -notmatch '^(NT AUTHORITY|Window Manager|Font Driver Host)\\' })
    } catch {}
    try {
        $h = Invoke-RestMethod -Uri 'http://127.0.0.1:8787/health' -TimeoutSec 10
        $beat.bot_ok = $h.status -eq 'ok'
        $snap = $h.logging.page_snapshots.last_batch_at
        if ($snap) { $beat.scanner_age = [math]::Round($now - [double]$snap) }
        $wd = $h.discord.watchdog
        if ($wd) {
            $beat.stalled = [bool]$wd.stalled
            $beat.stalled_seconds = $wd.stalled_seconds
            $beat.due_items = $wd.due_items
        }
        if ($null -ne $h.discord.live_sending) { $beat.live_sending = [bool]$h.discord.live_sending }
        if (-not $beat.bot_ok) { $beat.bot_error = "health status $($h.status)" }
    } catch {
        $beat.bot_error = ($_.Exception.GetType().Name)
    }
    try {
        Invoke-RestMethod -Method Post -Uri $url -Headers $headers -ContentType 'application/json' `
            -Body ($beat | ConvertTo-Json -Compress) -TimeoutSec 15 | Out-Null
        $result = 'sent'
    } catch {
        $result = "post failed: $($_.Exception.Message)"
    }
    "$(Get-Date -Format s) $result $($beat | ConvertTo-Json -Compress)" |
        Set-Content -LiteralPath (Join-Path $dir 'last.txt') -Encoding utf8
    $wait = 60 - ((Get-Date) - $started).TotalSeconds
    if ($wait -gt 0) { Start-Sleep -Seconds $wait }
}
