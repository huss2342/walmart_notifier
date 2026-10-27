# Scanner workspace and interruption recovery — October 25, 2026

Historical 2.21 receipt. Current live validation and backend status migration are
tracked in [scanner-control-20261025.md](scanner-control-20261025.md) for 2.22.
The owner supplied Options text; do not request further Options read-backs.

## Authorized outcome and current state

Owner requested grouped relevant tabs, tab selection that preserves scrolling,
resume after interruptions with a reset fallback, and verification compatibility.
Implemented Relay **2.21.0**. Grouping and rotation default on; existing explicit
settings remain respected. No commit, push or backend deployment. Other pending
workspace changes are preserved.

Local verification and independent review pass. Owner reloaded 2.21.0 and
refreshed Walmart. The safe live diagnostic confirms **2.21.0**, verification
pause **0**, and the home scan advancing to page eight. Automatic **Reviewer
scans** grouping is present, currently with home and keyword tabs. Department
live status is still being investigated: it is absent from that group. Owner
was asked for its Options status and checked state because browser security
policy rejects extension-page navigation; do not work around that restriction.

## Implemented contracts

- `tab-workspace.js` groups registered coordinator/auxiliary-owned IDs per
  existing window. Session group identity requires live matching members;
  shared groups are left alone. Grouping can run while verification is paused.
- Before pager preparation and lazy-pager discovery, content requests a bounded
  focus opportunity. Only a current active scanner lease may select its tab.
  Selection stays within a focused, non-minimized Chrome window; other selected
  tabs, verification and real input prevent rotation. No OS window is focused.
- Optional three-second slots, fair contention and absolute request expiry
  prevent stale queued activation. Expected activation events are consumed once.
  Focus failures retain normal background scanning and existing navigation waits.
- Real input revokes rotation before any storage/group RPC, with a persisted
  thirty-second shared grace. Completed owned tabs retain this protection.
  Animation, lazy return scroll and pending focus requests cancel for input,
  context invalidation or verification.
- Primary verification retains its last validated durable checkpoint separately
  from revoked leases. Recovery reuses compatible acknowledged progress with a
  new generation; unchanged readiness guards require all challenged open tabs
  to be usable. Known progress older than twenty-four hours starts fresh.
- Auxiliary failures resume the saved page first, reset only that target on the
  second failure, then skip on the third. Pending facet selections retry their
  parent. Verification spends no target retries. Recovery stage and checkpoints
  survive worker/browser recreation; stale known checkpoints start fresh.
- Existing automatic verifier enable settings, single controller, durable
  attempt limits, cleanup and recovery checks remain intact. No new CAPTCHA
  input or automatic verification enable action was performed in this work.

## Verification and review

- Declared extension command including `tests/test_tab_workspace.mjs`:
  **451/451 pass**, no failures/skips. Receipt:
  `data/scanner-workspace-extension-20261025.txt`.
- Meaningful RED cases covered lost primary progress, inactive-tab activity,
  lazy return scrolling, request expiry, repeated activation tokens, paused
  grouping, trailing-slash scope and shared groups. Follow-up regressions also
  cover the trusted-input/slow-group race and actual background+auxiliary+
  verification-controller recovery with a stub input engine.
- Actual production manager/content/facet/pager/observer scripts in the isolated
  browser fixture pass the **31-page** expected round, isolated failure,
  checkpoint recreation and shared pause. Chrome APIs/ingestion and pacing are
  simulated. Receipt: `data/scanner-workspace-browser-20261025.json` and `.jpg`.
- Visible browser scrolling fixture passes eased movement/upward settling,
  unchanged deadlines, fresh-card guards, recovery, live observation and context
  cleanup. Receipt: `data/scanner-workspace-scroll-20261025.json`.
- Real Options preview saves group/rotate/departments/keywords on and brands off;
  console warnings/errors are empty. All browser fixtures have clean consoles.
  Receipt: `data/scanner-workspace-options-20261025.json`.
- All JavaScript syntax, manifest JSON, repository Ruff and scoped whitespace
  checks pass. Backend implementation unchanged; its earlier full-suite result
  remains documented in `parallel-scans-20261025.md`, not a new backend run.
- Fresh independent reviewer approved after three concrete input conflicts were
  repaired and reproduced as closed. Final review reran 21 workspace tests and
  four critical adapter regressions, with no remaining required findings.

## Live evidence and limits

One ordinary reload of the supplied blocked tab left the challenge visible.
Later that same tab returned to Pet results with 129 product links and no visible
challenge marker. This does not attribute clearance to automatic or manual
input. The live diagnostic recorded resumption and then another challenge in a
different tab; following the owner's reload/refresh, the shared pause is clear.
No acceptance guarantee or lower challenge-rate claim follows from these checks.

The six then-open reviewer tabs were explicitly grouped via Chrome's supported
management API in the blue **Reviewer tabs** group. Read-back confirmed the
membership; Chrome's normal ungroup action reverses it. Automatic grouping uses
the separate **Reviewer scans** group for extension-owned tabs. No user tab was
closed. Live rotation still needs an eligible scanner tab selected in foreground
Chrome; selecting an unrelated/manual tab correctly yields.

## Ownership and resources

Root owns background/content integration, runtime tests, manifest/CI and records.
Workers owned the separate workspace module/tests, Options/tests and auxiliary
recovery module/tests. Reviewers were read-only. Workers have finished.
Temporary loopback browser fixtures and their servers are task-owned and must be
closed after QA; the user Walmart tabs and backend remain running.

Primary API references:
- https://developer.chrome.com/docs/extensions/reference/api/tabs
- https://developer.chrome.com/docs/extensions/reference/api/tabGroups
- https://developer.chrome.com/docs/extensions/reference/api/windows
- https://developer.mozilla.org/en-US/docs/Web/API/Page_Visibility_API
