# Opens the coverage-loop Claude chat (Windows Task "ReviewerCoverageLoop", every 2 days).
$repo = "C:\Users\Navi\Documents\repos\recognized_reviewer_notifier\walmart_notifier"
$prompt = "Scheduled coverage loop. Read tasks/coverage-loop/brief.md and follow it."
$log = Join-Path $PSScriptRoot "chat.log"
$hc = "C:\Users\Navi\AppData\Local\Programs\herdr-core\hc.exe"
$name = "coverage-loop-$(Get-Date -Format yyyyMMdd)"
try {
    & $hc run --bg --no-parent --outlive-parent --name $name --cwd $repo claude $prompt
    if ($LASTEXITCODE -ne 0) { throw "hc exit $LASTEXITCODE" }
    "$(Get-Date -Format s) started $name in herdr" | Add-Content $log
} catch {
    # herdr not running: open a plain terminal window instead.
    Start-Process powershell.exe -WorkingDirectory $repo -ArgumentList "-NoExit", "-Command", "claude '$prompt'"
    "$(Get-Date -Format s) herdr failed ($_); opened PowerShell window" | Add-Content $log
}
