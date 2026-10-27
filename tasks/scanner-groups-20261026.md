# Identify current scanners and retire duplicate groups — October 26, 2026

User reported multiple indistinguishable Reviewer scans groups and could not
tell which to keep. Immediate organization and a reload regression repair are
authorized by that request. No tabs, data or item records were deleted.

## Immediate live cleanup

Fresh local status (age20.8s, 2.23.3) identifies primary1530560551,
department1530560564 and keyword1530560565. Chrome independently confirms
these three in blue group739906817.

Old blue groups824976180,886884864,276508245 contained only reviewer tabs
1530560556–1530560561, none current or selected. After rechecking each group's
title/color/window/private status and each member's reviewer route, the browser
management API consolidated them into824976180, renamed **Old reviewer tabs —
safe to close**, grey/collapsed. Read-back confirms exactly one blue current
group with three tabs and one grey older group with six tabs. All tabs remain
available; the change is reversible through ordinary grouping controls.

## Root cause and source 2.23.4

Chrome clears extension storage.session on reload. The workspace's only cached
group identity and auxiliary owned-tab mapping lived there; a reload can create
replacement workers, then a new group despite a surviving primary already being
grouped. Saving only current member IDs also made the next cached group check
reject its own retired members and create another group.
[Chrome storage lifecycle documentation](https://developer.chrome.com/docs/extensions/reference/api/storage).

The workspace now recovers only through a **currently registered live tab's**
existing group, requiring the exact private title/color/window and exclusively
safe reviewer/blocked members. Title matching alone never grants ownership or
rotation authority. Retired members move into a separately labelled grey group,
which is reused only when private, reviewer-only and free of current scanners.
Older selected tabs remain selected and their archive stays expanded. Shared
groups, unrelated pages/windows and disabled grouping stay protected. Group/API
failure remains optional and cannot stop ingestion. No window is focused and no
tab is closed by this code.

## Evidence

- Three meaningful cases fail prior source: extension reload, replacement worker
  accumulation, and older selected tab separation. Safety case already passed.
- Thirty workspace tests pass, including group reuse across lost session state,
  stable current/archive groups, retired-tab authorization refusal, no selection
  loss, and shared/unrelated archive rejection.
- Declared 15-suite extension command: **557 passes, zero failures/skips**.
  `data/scanner-groups-extension-20261026.txt`.
- Ruff, workspace JS syntax, manifest JSON and scoped whitespace pass.
- Self-review checked private-group identity, live-anchor recovery, registration
  authority, shared-group rejection, cross-window behavior and option changes.
- No backend/deployment or verification behavior changed.

## Live reload result and 2.23.5 follow-up

Owner reloaded/refreshed 2.23.4. Fresh safe status confirms that version, primary
1530560551 and new workers1530560569/1530560570. Chrome shows those three in
new blue group2083254464, but previous blue739906817 still held retired workers
1530560564/1530560565. Thus 2.23.4 did not yet satisfy real reload stability.
Preserved read-back: `data/scanner-groups-20261026/reload-2234-before-cleanup.json`.

After confirming the two retired tabs were unselected, private and reviewer-only,
the browser management API moved them into existing grey824976180. Read-back
confirms one blue group with the current three and one collapsed grey group with
eight older tabs; no tabs closed and unrelated groups preserved.
`data/scanner-groups-20261026/reloaded-2234-cleaned.json` and `status-2234.json`.

The missing regression was workers registering before primary election. A
provisional worker group became cached; the later full sync preferred it to the
surviving primary's group, leaving retired workers behind. The new reproduction
fails on 2.23.4 (group11 chosen instead of original10). 2.23.5 prefers the first
live scanner's existing validated group (the caller puts primary/interrupted
first), then falls back to the cached group. The empty provisional group dissolves
when workers move. The test fake now mirrors that Chrome behavior.

All 31 workspace tests and the declared 15-suite extension command pass:
**558 passes, zero failures/skips**. Receipt:
`data/scanner-groups-2235-extension-20261026.txt`. Ruff, JS syntax, manifest JSON
and scoped whitespace pass. Focused self-review checked candidate order at the
background caller, private/shared/unrelated protections, retired authorization,
empty group behavior and failure containment. No backend or verifier changes.

## Verified 2.23.5 activation

Owner reloaded/refreshed. Fresh safe status (10.2s old) reports **2.23.5**,
primary1530560551 and owned department1530560571. Chrome management read-back
confirms exactly one blue Reviewer scans group, **same2083254464**, containing
those current tabs. Retired0569/0570 automatically moved into existing collapsed
grey824976180, now ten older tabs. No manual grouping actions were needed after
this reload. The other unrelated group remains intact. No tabs closed.

Keyword is enabled but between rounds (partial, owned=false, future nextAt), so
its previous0570 is correctly archived rather than a currently registered tab.
Status also retains department missing-panel failures; this task verifies only
grouping and reload behavior and does not certify broader live scan coverage.

Receipts: `data/scanner-groups-20261026/reloaded-2235.json` and
`data/scanner-groups-20261026/status-2235.json`. Grouping task complete.
