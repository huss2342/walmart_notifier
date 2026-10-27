# Windows native verification fallback

This optional, current-user Chrome Native Messaging host delivers one bounded
mouse press for a prepared point in Chrome's own parent window. The extension
keeps its existing attempt limits and confirms recovery from the reviewer page.
Successful input delivery cannot guarantee the site's acceptance.

Run `./install-native-verification.ps1 -WhatIf` to preview, then run the installer
without `-WhatIf`. It uses the project `.venv` and the Windows .NET C# compiler;
it downloads nothing and needs no elevation. Chrome's registry entry lives under
the current user's `Software\Google\Chrome\NativeMessagingHosts`. Its manifest
allows only extension `oidgbnehgnikmniolojjkmhlpifdoiil`. Reload the extension and
enable Windows fallback in Options. A different unpacked extension ID is refused.

`./install-native-verification.ps1 -Remove` removes only this project's owned
registration, generated executable and manifest. Source and diagnostic logs stay.
An existing foreign registration is never replaced or removed.

Chrome launches `data/native-verification/launcher.exe` on demand. The launcher
copies Chrome's raw binary pipes and invokes project Python with the launch origin,
nonzero `--parent-window` and installed manifest. The host rejects other origins,
zero parent windows, extra fields, non-finite geometry and frames over 16 KiB.
Only `probe`, `prepare`, `press`, `status`, `release` and `close` are supported.
There is no shell, keyboard, arbitrary window or network command.

Preparation expires after two seconds. One connection can press once. A 40 ms
poller checks input/focus/window/desktop changes and the 30 second maximum. A
separate release-only watchdog is armed before the press, receives heartbeats
every 200 ms during a hold and stays armed until release or no-press cancellation is confirmed. EOF,
malformed input, crashes, missed heartbeats and user activity trigger release.
Native fallback requires an unlocked interactive desktop and five seconds of
system-wide user idle; it yields when the user interacts.

Logs in `data/native-verification/host.log` rotate at 64 KiB with two backups and
contain only operation/error labels, timestamps, input confirmations and durations.
They contain no page content, coordinates, URLs, product/account data or raw errors.

Tests use fake input backends and never touch the mouse. In PowerShell run:
```powershell
$nativeTests = @(Get-ChildItem -LiteralPath tests -Filter 'test_native_verification*.py' | ForEach-Object { $_.FullName })
& .venv/Scripts/python.exe -m pytest -q @nativeTests
```
Live verification input requires separate action-time authorization; local dummy
button QA can exercise delivery without contacting a protected site.
