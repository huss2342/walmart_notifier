# Focus before navigation — October 26, 2026

User authorized selecting scanner tabs before changing pages or departments,
preserving smooth scrolling. No reduction in Walmart verification frequency is
claimed. Existing grouping, rotation preferences, input grace and shared
verification pause remain authoritative.

Source uses the combined current **2.23.3** release, preserving concurrent
verification-watch changes. No commit, push, backend deployment, credential or
verification-assistant configuration change was made for this slice.

## Behavior

- Department selection/discovery already requests a dedicated bounded focus
  slot before rendering controls. This remains intact.
- `navigateWithFocus` now requests a fresh `page-navigation` turn at the actual
  action boundary; earlier reading/pager preparation can no longer serve as an
  assumed current selection. The existing workspace gives this a short bounded
  slot, without activating an OS window.
- Main page navigation, URL fallbacks after stalled native controls, search
  transitions, scan restart, empty-page reload and watchdog recovery use this
  step. Native/fallback collection and scroll behavior are retained.
- Busy turns retain the queued action and retry; a grant waits for visible
  selection and cannot navigate after its expiry. Input, changed source URL,
  generation/role changes and verification invalidate pending acknowledgements.
- Optional focus failure or explicit rotation disable still permits existing
  background work. An unrelated selected tab or unfocused Chrome window is not
  disturbed. Chrome must be in front with a scanner selected for tab rotation.

## Verification

- Five new focus behaviors demonstrated RED before implementation. Additional
  tests cover both actual queued native/fallback paths and expired grants.
- The shared runtime harness now awaits navigation's real async completion;
  fallback-round worker/document clocks are aligned and fresh document boundaries
  clear their pending page work explicitly. Existing assertions remain.
- Declared 15-suite extension command: **552 passes, zero failures/skips**.
  Receipt: `data/navigation-focus-extension-20261026.txt`.
- Repository Ruff, extension syntax, manifest parsing and scoped whitespace pass.
- Real browser: production lifecycle and real timers pass focus-before-change,
  busy-turn resume and stale-role cancellation. Chrome API grants are simulated;
  this does not prove live Walmart focus or reduced challenge frequency.
  Browser artifacts are under `data/navigation-focus-20261026/`.
- Self-review checked scope/role/input/verification races, expired leases and
  continuity of native/fallback navigation. No subagents were dispatched.

## Activation

Owner reloaded/refreshed; fresh safe status confirms **2.23.3** and rotation
enabled. Activation receipt: `data/navigation-focus-20261026/activated-status.json`.
Live browser inspection shows Chrome in front with an unrelated tab selected;
automatic scanner activation correctly yields in this state. The new navigation
order is proved by the real-browser fixture with simulated grants, not by a live
Walmart transition in this manual-browsing state. Select a registered scanner in
the blue group to allow foreground rotation. Live department coverage remains
unverified separately. No Options read-back is needed. Fixture tab/server stopped.
