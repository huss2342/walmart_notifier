# Scanner pagination and focus repair — October 28, 2026

## State

Owner authorized fixing the issues in the follow-up performance analysis.
Extension **2.26.5 is activated**. The owner confirmed reload and refresh;
saved live reports identify 2.26.5 and all three scanner documents are reachable.
New focus and page telemetry is arriving. The first post-reload snapshot still
had unreachable documents; refreshing the primary and the normal recovery tick
reconnected them. Normal user-activity grace remained in effect. Full-round
useful-throughput improvement has not yet been measured.

The one-file backend diagnostic update is live, healthy and compatible with
2.26.4. No pending/due/leased Pro jobs at post-activation health readback.

## Changes

- Recognize Walmart's current page as a visible plain numeric leaf when there
  is no aria-current marker. Conflicting or hidden current markers stay unknown.
- Recognize omitted Next only with a coherent rendered page, usable Previous,
  an adjacent numbered predecessor and no higher numeric controls. Hidden Next,
  ambiguous plain numbers and growing/windowed pagers cannot establish this end.
- Require the same card-bound terminal observation on separate reads at least
  one second apart. Late cards reset it; enabled Next or a higher page defeats
  it. No extra network request is needed for this confirmation.
- A URL requesting a different page from that stable terminal is corrected
  before intake/ready receipts. Remove out-of-range receipts, revisit the actual
  terminal URL and retain/fill interior gaps before any completion. Never count
  a clamped response as a new covered page.
- Identical consecutive unvisited-page signatures remain unresolved rather
  than adding another page receipt. The preceding fingerprint persists with
  the existing durable sweep checkpoint. Revisited gap/terminal pages remain
  revalidatable. Existing finite recovery budgets and unresolved-target carry
  remain in force.
- Native Next selection uses the same rendered-page recognition. A windowed
  plain-current pager can use its available Next rather than a full URL reload.
- Results scrolling returns its exact selected-tab lease in finally, including
  failed/cancelled work or a role change after the grant. Local lease identity is
  cleared before release, so the old revocation cannot cancel completed reading.
  Late-item intake and reading proof validation still occur afterward.
- Workspace release validates Chrome's top-level sender, tab, generation and
  exact current finite lease, with fresh authorization and async race rechecks.
  It does not change tab/window selection or clear the FIFO waiting queue.
- Bounded focus grant/denial/release timings, cumulative wait, remaining lease,
  fixed operation/reason labels, clamp and repeat events survive JS sanitization
  and backend archival. No URLs, raw DOM, lease identities or titles are exported.

There is no universal 25-page limit. Three scanners, 100 keywords, brands off,
existing navigation pacing, user-activity grace, card-only eligibility and the
verification helper are retained. Existing unrelated dirty checkout work was
preserved; no branch, commit or push was made.

## Verification and review

- New behavioral regressions first reproduced the pagination, repeated receipt,
  missing focus release and diagnostic sanitizer failures.
- Full extension suite: **835 passed**, no failures/cancellations/skips:
  `node --test --test-isolation=none tests/test_*.mjs`.
- Full Python suite: **1,984 passed, one existing Windows-platform skip**:
  `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp
  data/scanner-performance-fix-20261028/python-full`. Windows test temp directories
  required normal filesystem permissions. The skipped case is the explicitly
  off-Windows boundary in test_native_verification_win32.py.
- Real browser local replay: **18 checks passed**. The fixture uses the captured
  public Sofa cards plus the observed plain-current/omitted-Next pager layout.
  It checks hidden/growing controls, stable confirmation, variants, stock and
  long/windowed pagination. This is a replay, not a live speed measurement.
- Maintained VM replay still covers a 121-page department and the complete
  242-item initial/late union across fresh documents. Omitted terminal proof also
  succeeds at pages 35 and 121; clamped URL recovery cannot drop valid receipts
  in the presence of a plain numeric higher page control.
- Exact lease return, FIFO fairness, stale/expired/foreign identities,
  subframes, activity/pauses and replacement races have regression coverage.
- Ruff, all extension/helper JavaScript syntax checks, native protocol generation
  check, server --help and whitespace checks pass.
- Independent read-only review found two required issues: missing native Next
  reuse and unlabeled higher numeric controls. Both fixed and verified, including
  independent source probes. No remaining required P1/P2 findings. A misleading
  success diagnostic reason was also removed.

An initial browser fixture placed test Next/higher controls outside the pager's
list, so the scope-correct parser did not see them. Correcting that replay DOM
produced the final 18 passing checks; production code was not weakened.

## Backend activation and rollback

One module, src/browser_diagnostics.py, was overlaid on the exact running image
f34a0192132d. Its live SHA matched the pre-task local snapshot before building.
New image **60bd0ba9a16b**, healthy at **14:43:24 CDT**. Readback verifies unchanged
HostConfig, mounts, environment hash, saved scanner/relay settings, networks,
user/entrypoint/command/work directory and stop configuration. No live database
was opened from Windows during this implementation.

Rollback image: `reviewer-notifier:before-performance-20261028`. Only use the
existing configuration-preserving deployment process; do not rebuild the entire
unrelated dirty checkout. Exact pre-task source snapshots, seven reviewed diffs,
hashes, image baseline, activation receipt, health readback and test logs are
ignored under `data/scanner-performance-fix-20261028/`.

Browser-side rollback copies are in its before/ folder. Preserve saved scanner
checkpoints/settings if rolling back, and remember earlier code cannot use the
new omitted-next proof as verified completion.

## Remaining measurement

Activation is verified; no further extension reload is currently required.
At 14:51 CDT all three documents were reachable, but focus diagnostics reported
manual-tab-active: Chrome's selected non-scanner tab prevents automatic selection
while that window is foreground. This is the preserved manual-browsing guard.
Minimizing Chrome or leaving an idle scanner tab selected permits normal rotation.
No live early-release timing improvement has yet been established in that state.
Verify that the department lane moves past its current terminal rather than probing to 100.
Observe new focus-released events and compare useful page coverage on matched
work. A short sample alone cannot establish a verified full department round or
promise a percentage improvement.

The local replay tab and server have been closed. The implementation/reviewer
worker has finished; it owns no active resource. Activation observations and
sanitized events are saved in the same ignored evidence directory.
