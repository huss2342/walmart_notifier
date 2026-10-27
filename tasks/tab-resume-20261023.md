# Last-page and scan recovery

Updated October 23, 2026 (America/Chicago). Source implemented and locally verified;
owner activation remains pending. Version: Reviewer Item Relay 2.18.0.

## Accepted outcome

The owner requested remembering the extension's last page so closing a tab can
reopen or refresh that page without restarting the walk. This slice preserves
the existing one-primary coordinator, delivery acknowledgement and pacing rules.

## Implementation

- `extension/background.js` owns a local `reviewerResume` checkpoint with the
  committed URL, configured mode/path and acknowledged sweep. Current-generation
  primary messages checkpoint before navigation; passive/stale writers cannot
  overwrite it. URL updates also save pages that have not delivered yet.
- Tab removal retains the checkpoint while retiring per-tab state. Replacements
  restore that URL and transfer progress under a fresh lease. Eligible passive
  tabs and a supervisor-opened homepage can be reused. An old document stays
  passive while the recovered URL is pending; ordinary primary navigation keeps
  its bounded loading lease.
- Chrome restart restores progress with empty session storage, including search
  position and temporary category fallback. Installation/reload refreshes the
  current URL and adopts legacy per-tab progress on the first upgrade.
- `extension/content.js` awaits checkpoint confirmation before navigation and
  retries an unconfirmed save. Already acknowledged pages remain recorded.
- Manual restart and verification pause clear saved progress as before. Mode/path
  mismatch invalidates incompatible checkpoints. Completion clears resumable
  sweep progress while retaining the final URL. Disabled refreshing, verification
  and duplicate-tab cooldowns remain authoritative.
- Manifest/integrated version assertion moved to 2.18.0; README and architecture
  document recovery. Existing unrelated dirty files and concurrent changes were
  preserved. No commit, push or backend deployment was made.

## Evidence and limits

Meaningful regressions demonstrated the original default-home reopen, lost
progress, missing checkpoint handler, page-one extension reload, stale mode
migration and recovery races before their fixes. Added 16 focused scenarios,
covering all scan modes, window close, stale writers, pending navigation,
supervisor/backup reuse, manual navigation, disabled refresh, invalid URLs,
completion, manual restart and checkpoint retry.

Final current-tree command:

```powershell
node --test --test-isolation=none tests/test_extension_runtime.mjs tests/test_extension_discord.mjs tests/test_scan_timing.mjs tests/test_challenge_evidence.mjs tests/test_verification_assist.mjs tests/test_verification_automation.mjs tests/test_integrated_verification.mjs
```

**331 passed, no failures or skips.** Receipt:
`data/qa-tab-resume-20261023/extension-tests-final.log`.
All extension/helper JavaScript syntax, manifest parsing and scoped tracked
whitespace checks pass. Python application behavior was not changed.

The bundled Playwright/Chromium runtime exercised actual extension storage,
content scripts, tab closure and full process restart in an isolated profile.
Both paths reopen page 7 and preserve pages 1–7. The staged extension changes
only the Walmart fixture hostname and backend port; no production profile,
Walmart session, notifier data or verification input was used. Playwright misses
extension-created initial request interception, so the harness verifies Chrome's
intended URL before loading that same URL through its fixture route. First
replacement resets the test-only automatic-open cooldown to avoid a two-minute
wait. Production cooldown behavior remains covered by runtime tests.

Browser receipt: `data/qa-tab-resume-20261023/browser-resume.log`;
harness: `data/qa-tab-resume-20261023/browser-resume.cjs`;
screenshot: `data/qa-tab-resume-20261023/reopened-page-seven.png`.
Extension reload/first-upgrade behavior is covered by runtime regressions;
the Playwright attempt to observe `chrome.runtime.reload()` could not obtain a
replacement worker event and is not claimed as real-browser reload evidence.
Final browser harness contains only the verified close/process-restart paths.
Owned browser contexts and the fixture server are closed. Automatic approval
review rejected scratch-profile deletion with "blocked by policy"; the seven
`chromium-profile-*` directories remain under `data/qa-tab-resume-20261023`.
Self-review covered lease/race authority, validation,
scope/settings compatibility, retry behavior and bounded storage work.

## Activate

Reload the existing unpacked **Reviewer Item Relay** in `chrome://extensions`
and confirm **2.18.0**. The install/reload handler repairs the current reviewer
page automatically, retaining compatible existing progress. No manual
**Restart sweep** is needed for recovery; that button intentionally starts over.
Normal recovery can re-read the last page; backend deduplication already handles
acknowledged duplicate items.

The storage and tab API contracts were checked against
[Chrome storage](https://developer.chrome.com/docs/extensions/reference/api/storage)
and [Chrome tabs](https://developer.chrome.com/docs/extensions/reference/api/tabs).
