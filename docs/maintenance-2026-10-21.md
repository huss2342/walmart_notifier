# Discovery diagnostics and recovery — October 21, 2026

## Authorized scope and current state

The owner authorized improving regular-mode discovery, verification recovery,
and diagnostics, and later authorized tuning scan settings. New items can first
appear on later pages, so page-1-only polling does not satisfy catalogue coverage.
No scheduled follow-up was created; the owner will report later issues.

Extension **2.9.3** is confirmed active in live telemetry with the latest tested
extraction optimization. The Windows
recovery helper and updated notifier are running. The notifier persists
browser snapshots and editable scan settings. Existing uncommitted changes and
separate research/Discord workstreams were preserved. No commit or push
was performed. Long-term prompt frequency and a sub-minute discovery guarantee
are not verified outcomes. Press & Hold remains a manual step.

## Findings from the live diagnostic history

- Saved settings were regular mode, a 10-minute refresh target, and a **60-second**
  page minimum. Recent challenge history raised effective page spacing to about
  two minutes and the refresh target to two hours without changing saved values.
- Three retained complete scans covered 17–18 pages and took **34.3–37.3 minutes**.
  These historical events predate the 2.7.1 patch.
- All 67 retained navigation events in the inspected snapshot were full-navigation
  fallbacks with `missing-control`; the site's pager was not being recognized.
- The first five timed local page deliveries took **16–29 ms** (median 18 ms).
  Those figures cover local processing, not Walmart loading or Telegram latency.
- A usable newly opened tab cleared an outstanding pause, then the original
  challenged tab immediately caused the same outage to be counted again (8 → 9).
  A regression reproduced that sequence before the fix. Existing historical
  counts were retained rather than guessing which past challenges were duplicates.
- A read-only merge of `seen.json` and `seen.log` found **490 newly observed items
  across 65 distinct five-minute windows in the preceding 24 hours**. This counts
  discovery timestamps, not Walmart publication times; it does not support
  assuming a fixed 30-minute drop cadence.
- The 2.8.0 report confirmed JSON settings **10/15/none** at 13:13 Central.
  Its first page-2 fallback then encountered no results and completed a scan
  after only page 1. Source/tests confirmed the unknown-boundary early-end gap;
  the same condition also affected the last unread page of a known range.
- Version 2.8.1 was confirmed active at 13:18 Central. Its pager compatibility
  additions did **not** resolve the actual site's fallback: reports still found
  one recognized pager, zero exact links, and zero matching native buttons.
  This remains a diagnosis in progress, not a demonstrated native-navigation fix.

These observations do not disclose Walmart's private detection criteria. No
browser fingerprint changes, simulated human input, CAPTCHA solver, proxy
rotation, or private endpoint probing was added.

## Changes

- `live-observer.js` relays previously unacknowledged cards during a page's
  existing navigation delay and after completion, without restarting a sweep
  or issuing a Walmart request. Failed late sends hold navigation and retry;
  role changes and verification cancel pending work. This cannot discover items
  on pages that the browser has not loaded.
- `diagnostics.js` mirrors the existing allowlisted report to loopback on the
  one-minute heartbeat. Unchanged reports are suppressed for five minutes, with
  suppression metadata surviving service-worker recreation in session storage.
  Mirror failure does not stop normal heartbeat or item delivery.
- `src/browser_diagnostics.py` validates and atomically persists a bounded snapshot
  at `data/browser-diagnostics.json`. The new GET/POST `/browser-diagnostics`
  routes retain existing token/origin boundaries and a 128 KiB limit. Unknown or
  private fields are rejected without echoing their contents. These routes do
  not reset stale-item warnings or send notifications.
- Timing events now include authoritative item counts, local delivery duration,
  and completed scan duration. Options shows the mirror and scan timing.
- Pagination accepts a visible, enabled link with the exact normalized target
  anywhere in the document, even if the pager wrapper changed. Empty `q` and
  `queryNups` fields match omitted fields; nonempty filters remain exact.
  Label-only buttons still require a recognized pager. Stale-card and single
  fallback protections remain in effect.
- Automatic verification recovery checks every still-open challenged tab before
  clearing the pause. `/blocked` keeps the pause even without a content-script
  response. Closing, solving, or navigating away from an old challenged tab allows
  the normal usable-page recovery path. This prevents false resume/escalation.
- Missing-control fallbacks now emit one numeric `pager-inspection` per document,
  capped at 1,000 candidates. The marked pager root accepts `ul`, `ol`, `nav`, or
  a navigation role. Actual 2.7.1 pagination still used fallback; broader matching
  was not reported as a demonstrated live-site fix. The new counts support the
  next diagnosis without exporting raw DOM, labels, URLs, or attributes.
  Version 2.8.2 additionally counts at most 200 pager descendants by tag shape,
  numeric text matches, hidden state, and marker presence. The server accepts
  the same numeric allowlist. No raw text, attribute values or markup is stored.
- An unknown-length scan requires repeated empty-end confirmation even after
  earlier pages were acknowledged. Empty pages within a known range, including
  its last unread page, follow bounded recovery rather than silently finishing.
  Regression cases demonstrated both early-end defects before the fix.
- `src/relay_settings.py` owns a strict schema-v1 `data/relay-settings.json`.
  GET/PUT `/relay-settings` preserve existing authorization and mutation-origin
  checks, require JSON for saves, limit bodies to 16 KiB, and use atomic writes.
  Manual edits are read fresh. Missing files preserve legacy Chrome preferences;
  invalid/unavailable settings retain the client's working cache and show a
  warning. Endpoint/token/path remain in Chrome. Options writes this same file
  before claiming save success. The heartbeat and installation event load it;
  active sweeps retain their stored search mode until the next sweep.

## Verification

- Node: `node --test --test-isolation=none tests/test_extension_runtime.mjs`
  → **92 passed** for 2.8.2. New behavior was demonstrated failing before implementation.
  Default process isolation cannot spawn in this Windows sandbox.
- Python: `.venv/Scripts/python.exe -m pytest --ignore=tests/test_discord_bridge_provision.py --ignore=tests/test_discord_bridge_runtime.py --basetemp=data/qa-final-20261021-v280-3 -p no:cacheprovider --tb=short --maxfail=3`
  → **549 passed**, outside the sandbox's restrictive temporary-directory ACLs.
  The unrestricted run encountered missing imports in two newly added Discord
  tests from another concurrent workstream; those files were excluded explicitly
  and were not edited. Earlier pre-Discord full runs passed 484 tests.
- After the 2.8.2 numeric schema additions,
  `.venv/Scripts/python.exe -m pytest tests/test_browser_diagnostics.py tests/test_server.py --basetemp=data/qa-pager-20261021-v282 -p no:cacheprovider --tb=short --maxfail=3`
  → **217 passed**. The remaining notifier code was unchanged by this patch.
- Ruff passed for `src` and `tests` with the concurrently added
  `test_discord_bridge*.py` files excluded (a third appeared after the full run).
  JavaScript syntax, manifest assets/version, and Git whitespace checks passed.
  The concurrent research and Discord files had unrelated Ruff findings and
  were preserved.
- Real in-app browser, loopback-only fixtures: native navigation with an
  unrecognized pager wrapper, stale-card protection, new live-card delivery
  without a sweep, verification cancellation, cached-card protection, hidden
  markers, JSON settings loading/saving, and credential-free diagnostic export passed.
  The 2.8.2 fixture also verified cards arriving during the page delay and a
  no-results response on the last unread page, with no console warnings/errors.
  Both fixture tabs had no console warnings or errors. The fixture intercepts
  the export instead of writing a real user download.
- Independent read-only review approved both the 2.7 implementation and the
  2.7.1 follow-up and 2.8.0–2.8.2 additions, with no remaining required findings.
- Container: `docker compose build notifier`, then
  `docker compose up -d --no-build --wait --wait-timeout 60 notifier` succeeded.
  `/health`, `/relay-settings` and `/browser-diagnostics` responded successfully.
  The second container activation at 12:54 Central retained 70,406 recorded items.
  Actual 2.7.1 telemetry included an acknowledged page-1 relay in 19 ms.
  The 2.8.2 numeric schema container was rebuilt and activated successfully;
  health retained 70,438 recorded items and the 10/15/none JSON settings.

## Operations and remaining evidence

The source-only backup of the previous running notifier is retained under
`data/notifier-source-before-2.7-20261021-01a15bc0/`; it contains no environment
or notifier data snapshot. The user's existing data mount remains in use.

The live Chrome session is not connected to the available browser controller;
only the in-app QA browser is accessible. The owner reloaded 2.7.1 and initially
saved 30/15 on request. After discussing discovery speed, the authorized editable
JSON was created with **regular mode / 10 minutes / 15 seconds**. Version 2.8 loads
that file automatically, so later edits do not require controlling Chrome.
The existing challenge count/backoff remains intact; the faster saved baseline
is not a one-minute discovery promise. Actual effective timings and current
values can be read from the local diagnostic snapshot and Options.

After any later prompt, inspect the bounded diagnostic snapshot promptly (or
use Options → Export diagnostics). Correlate `challenge`, `verification-resumed`,
`navigation-native`/`navigation-fallback`, `page-delivered`, and `sweep-complete`.
Use real scan durations and prompt history to assess future tuning. A short
successful interval cannot establish that verification prompts are eliminated.

All owned QA browser tabs and the port-8879 fixture server were closed, and
task-owned temporary test directories were removed. The notifier remains
running for the owner. Implementation workers and reviewers have completed.
The latest inspected real Chrome snapshot reports 2.8.2 and 10/15/none.
Its pager subtree has four visible anchors and numeric/ARIA target markers,
but native pagination still falls back with `missing-control`. The exact
anchor behavior remains unconfirmed; no native-navigation success is claimed.
No sub-minute full-catalogue or prompt-elimination claim has been verified.

## Missing-tab and Windows-login recovery (2.9.0)

The owner explicitly requested reopening an accidentally closed reviewer tab
and starting Chrome/Docker after heartbeat loss or a PC restart. No Codex
scheduled follow-up was created. The extension coalesces tab-open attempts,
checks for existing/loading/blocked tabs and persists a two-minute retry
cooldown. Verification pauses and refresh `0` suppress automatic reopening.

The host-only `src/browser_recovery.py` uses a real single-instance lock,
persisted launch/outage/pause limits, safe rotating logs and strict hot-read
`data/browser-recovery.json`. It targets the profile with this extension's
unpacked path, uses only the fixed reviewer URL, and restores the existing
local Docker service with bounded commands. A missing item relay alone never
launches Chrome. Default browser recovery waits for a three-minute stale
heartbeat, allows at most three launches/hour and keeps ten minutes between
attempts. Chrome already running receives one launch per outage until a fresh
reachable heartbeat rearms recovery. Known verification pauses survive helper
and notifier restart; restoring the backend remains allowed.

`install-startup.ps1` installs a current-user, limited, interactive-logon task
running the project `pythonw.exe`, with no execution-policy override or stored
password. `-Remove` stops and removes only this repository's named task.
`start-docker.ps1 -Once -DryRun` matched live **Profile 1** and reported
**healthy**, without launches. The Task Scheduler `-WhatIf` preview succeeded.
Focused validation passed **101 extension tests**, **47 host recovery tests**
and **218 notifier/diagnostic tests**; the host tests use fakes and never launch
Chrome or Docker. Independent review approved the final recovery logic after
fixing stale-disabled settings, failed-launch retries, ambiguous/disabled
profiles and JSON synchronization before extension startup recovery.

The login task **ReviewerItemNotifierRecovery-121c23d6dd01** was installed and
started at 13:44 Central. Read-back confirmed the project `pythonw.exe` action,
current-user interactive login, limited privileges and running state. Its real
file log immediately reported **healthy**. The notifier image was rebuilt and
activated successfully with the existing data mount. Actual forced browser
closure or a PC reboot was not performed against the owner's working session.
After the owner reloaded, live telemetry confirmed **2.9.0** at 13:45:52 Central
with one reachable sweeping reviewer tab and 10/15/none settings. Its page-1
delivery acknowledged 64 existing items in 30 ms without failures. The host
helper remained running and healthy; a second manual helper invocation
correctly reported `recovery-already-running`.

## Reliability assessment and pager placeholders (2.9.1)

The owner clarified that one installation will send alerts to many people.
The current Telegram destination is a single selected group, so group member
count does not multiply Walmart scans or the provider requests per item. The
collector remains a single point of failure. Verification still needs manual
completion; neither a prompt-free guarantee nor sub-minute catalogue discovery
has been demonstrated. Successful Telegram acceptance is not proof that every
member received a device notification.

At 13:50 Central the live 2.9.0 scan had acknowledged pages 1–4 with 17–30 ms
local processing and no failed/pending deliveries. Actual waits between pages
were 72–101 seconds under recent verification backoff. Native control lookup
still returned `missing-control` although the pager had numbered/ARIA targets
and 4–6 anchors. The retained completed-scan event was an older one-page early
completion and is not evidence of healthy complete catalogue coverage.

Source inspection found a compatibility gap: a scoped numbered anchor with an
inert placeholder href was excluded from label matching. Version 2.9.1 permits
only empty href, `#`, and strictly matched `javascript:void(0)`/`void 0` forms
inside a recognized pager. Active script URLs, named fragments and actual
destination/filter mismatches remain excluded. Four bounded numeric counters
classify pager href shapes; no href text, DOM labels or URLs are exported.
This is a supported explanation for the live mismatch, not a confirmed account
of the actual site's href values. The existing changed-card and single-fallback
guards remain active.

Validation: **105 extension tests**, **74 diagnostic-store tests**, and the
real in-app browser fixture passed with zero warning/error console entries.
The fixture used a placeholder anchor and verified native navigation, stale
cards, late cards, empty-page confirmation and verification cancellation.
Independent read-only review approved the compatibility and privacy changes.
After the owner reloaded, live telemetry confirmed **2.9.1** at 14:00:17
Central. Page 1 delivered 65 items (two new, 63 duplicates) in 22 ms without
failures. Its first native-control attempt at 14:00:36 selected a control but
did not complete the required URL/card transition. After about 60 seconds in
the background tab, `native-stalled` fallback loaded page 2 and delivered
71 items in 27 ms. This is not a successful native-navigation result.
The existing group delivery, notifier and Telegram command suites also passed
**82 tests** using mocked providers; no test messages were sent to the group.

The same review found a host-recovery edge case: a notifier restart clears
in-memory browser status, so a long-running helper could mistake that brief
gap for a lost browser. The helper now persists the last real heartbeat time
(accounting for its reported age) and waits its remaining stale window through
backend/helper restarts. Exact legacy state is migrated without erasing known
pauses, disabled refresh or launch limits. Backend recovery still proceeds
during the browser grace period. Four reproductions failed before the fix;
**51 host tests** and independent read-only review passed afterward.
The installed login helper was restarted with this patch at 13:58 Central;
live persisted state showed a 26-second-old heartbeat and zero launch attempts.
The notifier was then restarted successfully. Its fresh 2.9.1 heartbeat arrived
without any Chrome launch attempts, confirming the short-gap behavior in the
real running setup.

Version **2.9.2** adds a one-hour native-pagination cooldown after a stall or
click exception. It persists before URL fallback and is loaded before each
page advance, avoiding repeated timeout costs across page loads. After expiry
native controls may be tried again. Verification, role changes and new user
interaction while the state write is pending cancel the old navigation.
Validation: **107 extension tests**, **75 diagnostic-store tests** and lint
passed. The cooldown regression reproduced failure before implementation;
independent review also checked the pending-write cancellation boundary.
After the owner's reload, live snapshots confirmed **2.9.2**, with page 1
acknowledging 65 existing items in 18 ms and no failed/pending deliveries.
Verification count remained nine with no current pause. The cooldown's
cross-document and expiry behavior is regression-tested; a complete live
post-upgrade catalogue scan and a successful native transition remain unverified.

## Measured collection and notification efficiency (2.9.3)

The owner authorized further efficiency work while retaining all scan abilities
and asked again for detection resistance and bypass. No challenge solver,
identity/fingerprint modification or simulated-human input was added. There is
no prompt-free or sub-minute discovery guarantee. The live 2.9.2 history now
confirms that page-3/page-4 URL navigation used `native-backoff`, avoided another
roughly 55-second native timeout and acknowledged all visible items. At the
later inspection it had reached page 8 with no failed/pending delivery.

Two bounded improvements were implemented:

- `collect(excludedIds)` skips acknowledged live-card IDs before reading card
  text, titles and values. Claims are read lazily once if a fresh valid card
  exists. The ordinary scan/probe/navigation paths still collect every card.
  Failed sends are never added to the acknowledged set. A 72-card regression
  reduced card reads from 72 to 1 with one fresh item, and to zero when every
  card was acknowledged; default full collection retained all 72.
- Telegram HTTP/JSON 429 handling honors `parameters.retry_after` per chat.
  A monotonic in-memory cooldown suppresses further HTTP calls without sleeping
  or acknowledging unsent items. A mocked 100-item burst made only one failed
  Telegram request during the cooldown, kept every item retryable and delivered
  them after expiry. Private-owner delivery remains independent. These are
  mocked provider tests, not a real Telegram load test.

Performance ledger (same 100-card DOM, 99 acknowledged IDs, seven counterbalanced
samples of 25 collection calls per variant, one fresh item retained each call):

| Run | Full collection then filtering, median | Early ID exclusion, median | Result |
| --- | --- | --- | --- |
| 1 | 18.0 ms | 2.3 ms | 87.2% less collection time |
| 2 | 32.8 ms | 2.5 ms | 92.4% less collection time |

The timings cover each 25-call batch, not an individual call or whole scan.
Both runs exceeded within-run variation, and the existing browser fixture
passed without console warnings/errors. No equivalent improvement in scan
duration, total browser CPU or verification frequency is claimed.

Review also considered budgeting regular-page spacing against the saved
10-minute interval rather than the effective post-challenge interval. That
would reduce the present baseline from 90 to 60 seconds and increase active
browsing by 50%. Existing architecture explicitly applies the protective
effective interval, so this was not treated as a bug or silently weakened.
Page coverage, timing policy and verification pauses remain intact.

Verification: **109 extension tests**, **49 notifier tests**, and **249 combined
notifier/pipeline/Telegram-command/server tests** passed (the 49 are included
in the 249). Ruff passed. Independent read-only reviews approved both changes.
Source version is **2.9.3**; extension activation requires a manual reload.
The notifier image was rebuilt and activated with Telegram cooldowns; health
remained OK with 70,457 recorded items. The host helper logged heartbeat grace
during the restart, then returned to healthy without a Chrome launch. No live
test alerts were sent. The current 2.9.2 catalogue walk was left running.

The owner's bypass question was checked against HUMAN's current primary
documentation. HUMAN describes additional interaction/tampering checks and
test bypass tokens created through the site operator's challenge settings.
This extension has no access to Walmart's operator configuration; no supported
extension-side bypass was established. Sources:
<https://docs.humansecurity.com/applications/human-challenge> and
<https://docs.humansecurity.com/applications/create-a-challenge-bypass-token>.
No test tokens, header changes or solver interactions were attempted.

At 14:26 Central the current browser walk had delivered page 11 (591 items,
16 newly observed, no failed/pending deliveries; 47 ms aggregate local time).
The 2.9.3 browser reload was deferred until the walk completes so this change
does not reset current coverage again. The extraction improvement is tested
and ready, but is not yet active in Chrome. Owned QA tabs, fixture listener and
created test scratch directories were closed/cleaned; notifier and login helper
remain running for the owner.

Subsequent live telemetry at 14:30 Central confirmed **2.9.3**, one reachable
sweeping tab and no current verification pause. The owner had activated the
extension, and its scan restarted at page 1; uninterrupted full-catalogue
completion remains unverified.

Further research clarified that desktop libraries such as PyAutoGUI and the
Windows SendInput API can physically move, hold and release a mouse. The
operator-token discussion does not establish that desktop input is impossible.
No automated challenge attempt has been performed or proven successful here.
HUMAN documents interaction/device/environment signals and automated-solver
defenses beyond hold duration; a dependable unattended solve is not established
by the existence of a mouse library. Windows input-desktop isolation also
means a normal desktop helper cannot be assumed to control Chrome while the
secure desktop is active. Primary research:
<https://pyautogui.readthedocs.io/en/latest/mouse.html>,
<https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput>,
<https://learn.microsoft.com/en-us/windows/win32/winstation/desktops>, and
<https://docs.humansecurity.com/applications/top-ten-questions-during-onboarding>.
