# Parallel scans and page reading — October 26, 2026

Owner confirmed the 2.22.2 reload; live safe status confirms that version.
Home and keyword lanes advance independently (keyword round 4, 176 pages,
35/65 completed at the initial read). Department discovery still reports
`missing-panel`, with zero pages in round 91. Live normal-page DOM has the
correct scoped Department chip expanded and its four initial inputs mounted.
No agent CAPTCHA input was sent.

## Source 2.23.2 (2.23.0 and 2.23.1 activated)

- Existing home catalogue, department and keyword lanes remain concurrent.
  Each lane visits its own pages sequentially. Keywords use all 65 saved terms;
  optional per-department brands stay off. Dedupe, checkpoints, per-target failure
  recovery and shared verification pause remain in place.
- The former scrolling code only animated an offscreen pager into view. New
  `page-scroll.js` reads each delivered results page with a bounded downward
  animation, including a search's last page. It varies viewport-sized steps,
  animation duration and pauses, honors reduced motion, and watches height growth.
- Full-page scrolling requests a bounded 10-second tab selection lease and uses
  up to 8 seconds for its pass. Default pager leases remain 3 seconds. No window
  activation or switching away from an unrelated manual tab. Busy scanner tabs
  retain FIFO priority despite short activation deadlines. Minimized/unfocused
  or denied optional focus falls back to background scanning.
- Real input, hidden visibility, scope/role/generation change and verification
  cancel the animation. All timers/frames/listeners are cleaned up. A short
  leftover lease cannot consume the fresh page's top reset before a pass starts.
- Cards rendered during scrolling are acknowledged before pagination/completion;
  an ingest failure retains the page for retry without repeating a completed pass.
- Department opening now waits up to 7.2 seconds for actual owned options. An
  already-expanded chip with no panel is waited on rather than toggled or
  immediately rejected. Scope/visibility and Show More growth guards are retained.

## Evidence and remaining activation

Production scrolling module in a real browser: complete, 7,506px downward,
12 steps, 476 recorded animation samples, monotonic, 5,180ms, exact bottom,
zero warning/error console entries. Receipt and screenshot:
`data/page-scroll-20261026/browser-green.json` and `browser-green.png`.

Captured Walmart markup delayed opening: old helper fails `missing-panel`;
new helper returns all 34 expected departments with zero missing labels,
without toggling an already-expanded chip. Simulated timing reproduces a possible
sequence; live timing is not inferred solely from this fixture. Receipts:
`data/facet-missing-panel-20261026/browser-red.json`, `browser-green.json`.

Independent review of content/focus integration found visibility cancellation,
unused short-lease reset, and expired FIFO ticket bugs; all fixed before release.
Independent module review found no required changes. Focused module tests cover
suspended frames, hung guards, cancellation, lazy growth and 12,000px pages.

Owner activated 2.23.0, confirmed through fresh direct status. Discovery now
enumerates 35 departments (36 targets including discovery), while home and
keywords advance independently. A remaining live race prevents department
selection: during the real 15-second selection delay, the generic
`waiting-navigation` phase permitted live observation of unbound parent cards.
The manager correctly rejected that post with an inactive role, canceling the
pending selection. The compressed browser fixture's 40ms pacing did not expose it.

Source 2.23.1 uses `waiting-selection` for this delay and explicitly forbids
live observation while a facet selection remains unbound. Two regression cases
fail the in-memory baseline and pass current source: real parent mutations retain
the 15-second action timer, no parent cards are posted, and the selected scope is
bound before its new cards are ingested. Independent narrow review approved.
Source 2.23.0 passed all 518 extension tests; 2.23.1 passed 520. Owner activated
2.23.1; discovery works but further selections still fail with `missing-panel`.
Safe pre-focus status retained in `data/page-scroll-20261026/live-2231-before-focus.json`.

### Foreground facet readiness and root replacement

Source 2.23.2 requests a dedicated 10-second focus slot **before** any department
discovery/selection begins. Hidden, denied or short remaining slots defer without
marking a selection attempted, posting parent cards or failing a target. A helper
lease checks captured URL, generation, user grace, verification, visibility and
expiry. Visibility/lease loss during mounting yields for retry. Disabled optional
rotation still allows manually visible controls. Chrome must be in front with a
registered scanner selected for automatic rotation; the extension never activates
an OS window or steals selection from an unrelated tab. Home/keyword ingestion
can continue while facet work waits. Chrome documents visibility-dependent frames;
Walmart's particular internal rendering dependency is not established.

Independent VM evidence also proves stale root references cause `missing-panel`
when a filter section is replaced. The helper now reacquires only a uniquely
visible exact Sort-and-Filter section and matching chip after stored nodes detach.
Replacement chips never inherit cleanup-click ownership. Opening and Show More
replacement, ambiguous sections and cancellation have RED/GREEN coverage.

All **536 extension tests** pass for source 2.23.2, with repository Ruff, extension
syntax and manifest parsing. Independent content/focus review approved; root
reviewed scoped helper reacquisition and cleanup. Receipt:
`data/page-scroll-extension-2232-20261026.txt`. No backend source/deployment or
verification configuration changed in this turn. Existing Python backend suite
receipt remains 1,641 passes/one platform skip from the prior backend change.

Owner was asked to reload 2.23.2, refresh Walmart and leave a scanner selected
with Chrome in front for one minute before replying. Live department pagination
and scrolling remain the final check. Do not ask for Options read-backs; inspect
`data/scanner-status.json` directly. All local fixture servers/tabs were stopped.
