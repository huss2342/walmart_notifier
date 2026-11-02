# Production collector recovery and consolidation

Authorized October 26, 2026 (America/Chicago): implement all findings from this
chat's code review, preserve existing functionality and code entry points, and
fix the reported relay silence, empty page-10 stall, staggered third-tab creation,
and missing pagination focus. Owner configures settings once; preserve the saved
production configuration. No reset, history replay, timing/ACL weakening, or
changes to verification input behavior are part of this work.

**Latest accepted correction:** ordinary collection continues in the background;
auto-select the scanner tab before clicking/scrolling without requiring Chrome's
OS window to be foreground. The earlier foreground prerequisite was an agent
assumption, not an owner requirement. Source2.25.1 corrects selection, reading,
pagination, pager preparation and ordinary department controls accordingly.
Window focus changes preserve established scanner leases and only invalidate
pending acquisitions; manual tabs remain protected while Chrome is foreground.
Revocation uses unique lease IDs to cancel old content safely. Timer fallbacks
handle suppressed animation frames; measured long scheduler delays do not consume
the target failure budget, while active unsettled reading retains its120s bound.
No settings, backend image or verification input behavior changed in this slice.

Background correction verification: **734 JS tests passed, zero failures/skips**
in17.12 seconds; `background-js-release-final.log` records the full current glob.
Ruff, changed JS syntax, generated protocol parity and whitespace checks pass.
Fresh independent review APPROVE, no remaining Critical/Required findings;
421 focused tests and eight final targeted checks also passed independently.
Real Chrome fixture passes with real DOM/scrolling, simulated tab grants and
document focus forced false, both with normal frames and with animation-frame
callbacks suppressed. Console warnings/errors0. This is controlled browser
evidence, not a claim that all live Walmart lanes have advanced on2.25.1.
The temporary fixture tab/server were closed/stopped after verification.

**Activation remaining:** live mirror still reports **2.25.0**. Owner must reload
Reviewer Item Relay once in Chrome Extensions to activate source **2.25.1**;
the browser tools cannot claim that internal page. Then verify background lane
advancement. Backend restart/settings changes are unnecessary. Scanner and relay
saved-file hashes still match the original production baseline.

Baseline: clean working tree at a146712; extension source/runtime 2.24.1.
Historical review checks: 655 JS passes; 380 focused Python passes with four
temporary-directory fixture errors. Fresh verification is required after edits.

## Outcomes and ownership

- [x] Root: live diagnosis, startup tab reservation and fair focus recovery;
  Options settings mutation safety/request deadlines/shared clients and schemas;
  screenshot capture race; manifest/integration/docs/release verification.
- [x] Content worker: bounded empty-result/message recovery, explicit document
  session state, lightweight item signatures; content/live-observer/page-scroll
  source and runtime/scroll tests only.
- [x] Backend worker: log-tail and failed-snapshot recovery, atomic JSON helper,
  complete filter context for historical rescans; Python collector source/tests.
- [x] Discord worker: canonical queue adapters, policy/observation/rendering
  boundaries, indexed watch candidates, explicit transport outcomes; bridge/tests.
- [x] Subsequent verification wave: passive observation contract, native protocol
  parity fixtures, separate canonical/frozen companion test bundles.
- [x] Independent review, full Python/JS/lint/startup checks and isolated Linux
  container smoke test. Real-browser focus fixture passes.
- [x] Activate the backend and confirm extension2.25.0 is live; compare production
  settings, environment, mounts, ports and security configuration to the baseline.
- [x] Verify ordinary background reading/navigation and suspended-frame scrolling
  in regression tests and the controlled real-browser fixture.
- [ ] Reload extension2.25.1 and observe all three live scanners advancing in the
  background. Previous2.25.0 foreground waits were the bug corrected above.

Workers have exclusive source/test ownership, preserve concurrent edits and do
not commit/deploy or interact with live external services. Root owns shared
contracts, manifest and handoff. Existing deprecated entry points are retained as
compatibility adapters. No old reviewer inventory is replayed into Discord.

## Initial evidence

Read-only live inspection: the reported primary URL currently renders 41 cards
and 17 pages. Its backend mirror reports page10/visited9 and an active scan.
Diagnostics show two empty retries around00:32 UTC and no subsequent home intake
until02:55 UTC. At02:59 UTC the backend has fresh browser heartbeats, a successful
02:55 relay, no verification pause and no due Pro backlog. Auxiliary lanes report
waiting-focus (busy/window-unfocused), which must be distinguished from delivery
failure. Docker reads initially require pipe access beyond the default sandbox.

## Verification and handoff

State: implementation, automated verification and production activation complete.
Owner explicitly approved the restart with "restart it ye". Only the notifier
service was recreated at **2026-10-27 04:03:22 UTC** (October26 23:03 Central).
Extension source and live status both report **2.25.0**, settings sync succeeds,
and the primary plus two owned auxiliary scanner slots are present. Foreground
browser regression remains pending: live scanners report waiting-user/focus
because the Chrome window is unfocused. All production settings hashes still
match the baseline.

Final full checks:

- Python: **1,805 passed, one expected platform skip**, 159.18 seconds. Command:
  `.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider
  --basetemp=data/production-consolidation-20261026/pytest-full -o addopts= -q
  --tb=short`. Approved elevation addresses the established Windows temp ACL
  issue; tests use isolated local state, never live providers.
- JavaScript: **707 passed, zero failures/skips**, 17.51 seconds. All
  `tests/test_*.mjs` ran using `node --test --test-isolation=none`; CI discovers
  the same test glob with ordinary process isolation.
- Ruff, all extension/companion JS syntax, generated native contract parity,
  server startup and scoped whitespace checks pass.
- Fresh independent review: APPROVE, no remaining Critical/Required findings;
  724 Python and 538 JS tests passed in the separate review lane. Review found
  and verified fixes for late issued tab mutations and completed/partial lane
  slot restoration after session loss.
- Browser focus fixture: PASS for denied/granted turns, settled downward reading,
  upward pager preparation, late-card invalidation and stale replies. Fixture
  geometry now scales to the viewport; async randomness remains deterministic
  through preparation. Broader animation fixture needs a foreground run; its
  assertions were retained rather than weakened for background throttling.
- Isolated new Linux image (network none, read-only filesystem, no host data):
  server imports, queue schema/triggers and atomic persistence pass.
- Production database backups (collector/welcome/membership) use SQLite backup
  and pass `PRAGMA quick_check`. No historical data was requeued.

Source-only image: `reviewer-notifier:consolidation-20261026`, built on the exact
prior production image with unchanged dependencies. Rollback:
`reviewer-notifier:before-consolidation-20261026`. Sanitized baseline, logs and
consistent backups are ignored under `data/production-consolidation-20261026/`.
The activated container is healthy on image
`sha256:7ae02ae5a3f3bf08e312a5222fe5394250ec52901803a859096b16b71602699b`.
All recorded configuration comparisons pass: environment hash, command/entrypoint,
nonroot user, working directory, restart policy, mounts/binds, loopback ports,
read-only root, dropped capabilities, security options, network, init and stop
signal. Scanner/relay settings file hashes are unchanged. Sanitized evidence is
saved as `activation-verified.json` in the evidence directory.

Independent postdeployment read-only verification also passes: all17 reviewed
backend files match the workspace byte-for-byte; the candidate table, three
indexes and insert/update/delete triggers exist with matching-index version1.
Missing, stale and orphaned candidate rows are all0. SQLite was opened read-only
with query-only mode. Final health at117 seconds uptime remains ok, confirms
another natural notification at04:04:39 UTC, and reports no extension errors.

Automatic approval review initially rejected the requested live container recreation,
stating the user authorized implementation/testing but did not explicitly
authorize this production restart. A separate review rejected bringing the
Chrome test window forward under the retained no-focus-steal rule. Neither
rejection was bypassed. Explicit restart approval was subsequently received and
the exact notifier restart completed. The extension has also been reloaded:
mirrored version2.25.0 is confirmed. Chrome foregrounding still requires an
owner-controlled action under the retained focus rule.

Completed activation and remaining browser check:

1. `docker image tag reviewer-notifier:consolidation-20261026 reviewer-notifier:local`
2. `docker compose up -d --no-build --force-recreate --no-deps notifier`
3. Configuration comparisons and health pass. Natural Discord notification at
   04:03:39 UTC confirms the delivery worker resumed; live sending is enabled,
   Pro due0 and Starter due0. Existing delayed Free work remains subject to the
   established provider rate limits. No synthetic/provider test messages were sent.
4. Owner selects an owned blue scanner and keeps Chrome in front. Mirrored
   version2.25.0/settingsOk and three owned slots are confirmed; scrolling,
   pagination and fresh intake across repeated foreground transitions remain to
   be observed. Browser connection cannot claim the internal Extensions page.

Preserve manual-tab/user-interference safeguards without a foreground prerequisite and exact
delayed-feed contracts. Existing truncated records cannot recover lost title or
category history; newly captured records preserve complete filtering context.
No native challenge input or automatic solving behavior was changed/tested live.
No commits, pushes, settings edits, synthetic provider messages or historical
replays occurred. Only the explicitly approved notifier deployment was activated.

Closeout resources: all four implementation/review agents have finished. The
owned loopback fixture server on8879 was stopped and the one agent-created
fixture tab was closed. Production scanner tabs and unrelated services/tabs were
preserved. No task-owned background test process is required for resumption.
