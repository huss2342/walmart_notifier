# Notification and verification fixes — October 5, 2026

The user authorized investigation and implementation of Telegram command,
notification reliability, manual Walmart verification recovery, browsing cadence,
and unnecessary priority-control fixes.

## Findings

- Telegram registered only `/status`, and switching alerts to a group prevented
  owner commands in the original private chat. Unexpected update-handler errors
  could terminate polling permanently.
- Verification applied an escalating cooldown with no automatic recovery after
  the user solved the challenge or reopened the reviewer page. Events from one
  unresolved check could repeatedly extend the same cooldown.
- A failed silence-warning delivery armed its suppression flag before success,
  preventing retries throughout that outage.
- The live service was healthy and receiving pages. One observed batch had 97
  already-recorded items; another had 100 already-recorded items and one filtered
  new item. Neither called for an item alert. Current rules allow value $70+ OR
  the keywords `vanity` / `mirror`, excluding `covers`. Alerts go to a group.
  These observations explain the inspected batches; historic notification times
  were not recorded, so they do not establish the cause of every earlier silence.

## Implemented behavior

- `/help`, `/start`, `/status`, `/test`, `/filters`, and contextual destination
  commands are registered. Owner commands work privately after selecting a group.
  A failed update cannot stop the remaining polling batch.
- Usable markup in a challenged or newly opened tab clears a verification pause.
  Existing cached fallbacks cannot clear it, including legacy-pause upgrades.
  All tabs actually challenged during one outage remain eligible to recover it.
- Serialized coordinator changes and authorization checks cancel stale selection,
  queued deliveries, and later batches if verification begins. Already-issued
  requests may finish; their records still deduplicate subsequent retries.
- Default browsing uses a 30-minute refresh and 15-second page delay, with varied
  timing and slower pacing after repeated checks. Explicit zero refresh remains
  disabled and is diagnosed. Missing Chrome alarms are recreated on worker start.
- A one-minute browser heartbeat supplies phase and refresh diagnostics without
  counting as an item relay. Successful item-alert timestamps persist separately.
- Silence warnings default to two hours and retry until delivered. The Options
  priority selector is removed; new filter saves use normal priority. Existing
  JSON priority remains compatible. The user's filter thresholds were preserved.
- CI now executes the extension runtime regressions.

## Verification and runtime handoff

- Baseline: 326 Python tests and 27 extension runtime tests passed.
- Final: `.venv\Scripts\python -m pytest` — 406 passed; `node --test
  tests\test_extension_runtime.mjs` — 39 passed. Ruff, JavaScript syntax,
  manifest parsing, CLI startup, and diff whitespace checks passed.
- An isolated real Edge profile loaded the actual unpacked extension against
  mocked Walmart and notifier endpoints. Settings, filter saving, extraction,
  verification pause, and automatic resume passed with no page errors. No live
  test notification was sent. Evidence is in ignored `data/verification`.
- Independent review approved after deterministic regressions proved and fixed
  selection/verification and queued/multi-batch delivery races.
- Docker build passed. `docker compose up -d --no-build --wait --wait-timeout 45`
  recreated the local notifier and reported healthy. Live health confirms
  successful Telegram polling and no provider/menu diagnostic. Read-only Telegram
  API checks confirmed the expanded command menus in both private and group chats.
- The user's Chrome UI is unavailable to the browser-control tool. Reload
  **Reviewer Item Relay** at `chrome://extensions` and confirm version **2.4.0**.
  This activates the extension fixes and browser heartbeats. Then `/status`
  should show current browser state; `/test` explicitly checks the selected
  alert destination. Existing browser settings are retained.
- The isolated test browser and mock server are closed. Automatic approval review
  rejected removal of the temporary test profile as "blocked by policy"; profile
  files remain under `data/verification/edge-profile`.
- No commits, pushes, external user messages, or scheduled jobs were created.
