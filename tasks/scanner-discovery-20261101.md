# Scanner discovery and fourth tab — November 1, 2026

State: complete — reviewed implementation and backend live; owner reloaded the
extension and the fourth tab/settings are enabled and verified in production.

## Authorized outcome

The owner confirms the Gingerbread house, Harley mirrors and glow football are
shared-catalogue misses. Repair reproducible discovery defects, improve keyword
coverage, and implement a fourth high-value search tab using the existing shared
scanner. Preserve deep pagination, durable intake, existing progress, personal
watches, traffic waiting, verification pauses and fair foreground reading.
No social scraper or recurring social comparison feature is authorized.

## Accepted implementation contract

- New auxiliary `priority` lane follows departments and keywords in the shared
  coordinator and content pipeline. All four tabs share existing focus/reading
  leases and global target-start spacing. There is no preemption or forked scraper.
- Priority defaults: Furniture, Sofa, Couch, Sectional, Recliner, Mattress, Bed,
  Desk, Dining Table, Dresser; full pages for every target. Its completed round
  waits one minute by default (configurable 1–120). Global refresh zero suppresses
  automatic rounds. A busy four-tab workspace can still delay a particular turn.
- Optional schemaVersion 1 fields: auxiliaryPriority (legacy default false),
  auxiliaryPriorityQueries (null/default or at most 24 terms of 80 codepoints),
  auxiliaryPriorityRefreshMinutes (default 1). Strict controls and lowercase
  duplicate checks; absence of optional fields survives legacy round trips.
- General keyword capacity rises from 100 to 128; default 100 stays unchanged.
  Owner's saved list receives Alloy, Red Light, Gingerbread, Football, Mirror.
  Exact-prefix append migrates work in place, preserving existing target receipts,
  cursor and generation. Reordering/removing/editing the prefix still resets it.
- High value means focused searches in categories supported by retained price
  observations, not a verified price filter or a measurement of market demand.

## Evidence and practical limits

Evidence is in ignored `data/scanner-discovery-20261101/`: exact preimages,
worker patches/hashes, failing/passing regressions and sanitized investigations.
The three exact missed IDs have no positive retained rendering evidence. This
does not disprove their historical availability; sampled logs cannot establish
the exact historical cause. The current fix must not be presented as proof of it.

A captured card with a canonical heading and explicitly labelled same-product
variant links reproduced a real parser rejection. The repair selects only its
own primary item and availability/value, rejects ambiguous or unrelated cards,
and preserves the existing View-item authority. No stock/price is borrowed from
neighbours or variants. Current searches cannot recover expired historical offers.

## Rollout and compatibility

Deploy only reviewed backend modules over exact live image 5fd8a91dba3c, preserving
runtime and data. Keep new settings absent/off until the updated extension is
loaded and verified. Backend-first compatibility is one-way: new backend accepts
old reports; old backend rejects new fields/priority reports. Enable priority and
append keywords only after the new client is verified. Downgrade requires removing
new settings/reports and reloading the old client before the old backend.

## Ownership and verification

Root owns integration, Options, background, settings schema, version, docs and
release. Workers owned disjoint coordinator, backend/diagnostics and extraction
files. Independent reviews cross those ownership boundaries; implementation
success alone is not a review verdict.

## Verified release evidence

- Final whole-tree checks: **2,667 Python passed / one existing platform skip**;
  **1,014 JavaScript passed**. Ruff, extension syntax, native protocol generation
  and server startup pass. Earlier full Python runs each hit Windows 10053 in the
  unchanged server's auth test; the test sent a JSON body on GET. Normal bodyless
  GET now retains every 403 assertion and full POST/origin checks. A separate
  247-check server/priority run and the final complete suite pass. No retry or
  exception suppression was added to production.
- Independent reviews resolved target-start starvation (persisted fair rotation),
  stale priority cache under legacy authoritative settings, and FEFF trimming
  parity. Coordinator/navigation review: 100 checks; root/cache review: 66 checks;
  extraction and actual unavailable-card relay regressions pass.
- Real Chrome offline replay: captured heading card is extracted correctly as
  its unavailable primary, sibling variants are rejected, mismatched headings
  rejected; no console/runtime errors. Options rejects zero-minute priority
  intervals and saves the valid ten-query/one-minute configuration in the fake
  backend. Screenshot `data/scanner-discovery-20261101/options-browser-preview.png`
  is a local preview, not evidence of production enablement.
- Warmed offline DOM benchmark: ordinary marked card 0.258→0.252 ms median with
  zero additional heading queries; repaired card 0.083→0.223 ms with one heading
  query and zero→one correct primary. These are fixture timings, not site latency.
- Exact candidate image passes network-disabled settings persistence/restart,
  legacy shape, three-lane status and durable priority log ACK/restart smoke.
- Backend image **b5a19642db21** activated healthy **16:08:07 UTC / 10:08:07 CST**,
  over exact **5fd8a91dba3c**. Full HostConfig, environment hash, mounts, networks,
  launch config and settings are unchanged. All **12 watches** retain hash
  `7b1b18b1f169c7b2b412cc413edc18772b2abbdac4990880c1d32b85231d0618`.
  Live queue has zero due/failed items, no worker/log errors, and no log loss.
  406 Free-tier pending messages at the snapshot are all future scheduled work.

## Live browser activation — November 1

Owner confirmed the extension reload. A fresh backend report verified exact
**2.26.13** and departments/keywords/priority before any new settings were saved.
The reviewed enable helper ran at **20:05:56 UTC / 14:05:56 CST**, saving
priority=true/default ten terms/one-minute completed-round break. The general
keyword list is **105**, with the full original 100-term prefix and all other
settings preserved. Settings were atomically written and read back.

The activation helper used was:

```powershell
.venv\Scripts\python.exe data\scanner-discovery-20261101\enable_priority.py
```

The explicit guards remain enforced under Python -O; they refused the old client
without writing during preflight. The helper was independently reviewed. Three
existing owned scanner tabs were refreshed through supported Chrome UI/CUA while
preserving saved sweeps. Four distinct owned tabs are verified. Main retained its
generation and completed the remaining 11-page round before its normal next
round. Department scan retained round 124 and finished Beauty (24 further pages,
240→264), advancing to Gifts & Registry. Keywords retained round 40 and its
verified receipts (48→50 targets while the queue expanded to 105).

Priority completed **Furniture, Sofa, Couch and Sectional** and advanced to
Recliner, with **four completed targets and zero failed priority targets** at the
verification snapshot. First-page intake was acknowledged in **334 ms**; its
nine available candidates deduplicated correctly, while four unavailable cards
were excluded. Live extraction specifically retained HOMRELEXA 19698913226 as
out-of-stock with its own $29.89 value, proving the repaired layout on the site.
Priority start/read/intake/completion/focus events and durable ACK are archived.
The public screenshot `priority-live.png` shows its Couch search during the run.

Live health remains OK with zero due/failed Discord jobs, no worker/log errors,
zero browser sequence gaps and all 12 watches unchanged. Source hashes still
match the reviewed 27-file manifest; no further application code changed during
activation and there was no synthetic provider notification.

Live validation also observed stalled site navigations and Pet's native pager
adding `affinityOverride=store_led`. They remain honestly reported and undergo
bounded recovery; neither wrong-scope content nor incomplete targets are counted
as completed coverage. Pet retained its checkpoint and was deferred unresolved
after the existing retry budget; the keyword failure counter rose from 3 to 4.
Department's pre-existing seven failures were not erased. A fresh read-only review
found no reproduced source defect: successful passive role responses can wait
for the existing 30-second poll after a URL transition, and the longer commit
delays cannot be attributed from retained evidence. Sectional's later successful
rebind/intake/completion demonstrates recovery without weakening URL checks.
Activation verifies the new lane and shared behavior; it is not a guarantee that
every future Walmart response renders or that all prior unresolved coverage has
been repaired. Keep the downgrade constraints above.

All task-owned research and fixture tabs were closed, the loopback fixture server
was stopped, and workers are complete. Production scanner tabs stay open; CUA
claims release at turn end. Exact code hashes and task-only delta are
`final-source-hashes.json` and `task-only.patch` in the ignored evidence directory.
