# Navigation hardening and diagnostic attribution — November 4, 2026

State: implemented, reviewed, live and verified.

## Authorized outcome

Investigate and repair further navigation/general issues, and improve missing
logs for future diagnosis. Preserve the existing four scanners, full pages,
furniture-first kitchen preferences, progress, request pacing, input/traffic/
verification guards and normal notification policies. The previous 2.26.14
changes remain part of the same uncommitted canonical checkout.

## Evidence and corrections

The bounded post-activation audit found 70 navigation outcomes, all `ready`,
through approximately 13:10 CST. Bed still failed after three render attempts:
HTTP 200, reviewer shell, three links and one offer, but zero extracted cards.
Live signed-in DOM showed the primary Rollaway cover heading with `Best seller,`
while its two explicitly bound variant labels omitted that badge. A fixture
reproduced rejection; stripping only the comma-separated badge for identity
matching retains the primary's own $35/out-of-stock evidence and excludes variants.

An auxiliary wait still trusted cached `user-active`/`manual-tab-active` labels,
renewing its budget forever even with `busy:false`. Six regressions reproduced
the indefinite wait plus blocked rebind/boundary revalidation. Fresh input and
normal workspace authorization remain the action guards.

Primary recovery could race an ACK after the background's last progress check;
it also started a five-minute success cooldown when the receiver rejected it.
Four new regressions reproduce these paths. Numeric document/progress/start
guards are now checked at the content boundary, and rejected/unavailable attempts
have a 30-second floor; only accepted recovery earns the longer cooldown.

**Earlier audit attribution was too specific:** unresolved targets are carried
to the front of a new round, so an ordinal target index is not a stable query
identity. Historical names attached to slots and exact per-query revisit claims
were corrected in both audit receipts and the earlier task. Round/slot timing
estimates and exact Golf item/ledger proof remain valid. A new current keyword
failure is attributed to **Crafts** by contemporaneous status, and the current
priority failure to **Bed**; these names do not retroactively label earlier rounds.

Historical trial errors omitted status/throttle identity; local preflight 429s
and Discord HTTP 429s could not be distinguished. Recent SHM/ingest errors
clustered around Windows-host SQLite inspection in the earlier audit and have
recovered. All further live SQLite inspection uses Docker-side read-only access.
No current paid backlog was observed. No rate-policy changes are made from
historical error counts alone.

## Diagnostic questions and contract

1. Which scanner/target stopped useful progress, and was it loading, yielding,
   paused, unreachable or ingesting? Bounded historical `scanner-state` events
   preserve typed runtime state and coordinator progress independently of the
   latest overwritten status snapshot.
2. What recovery was requested, accepted or deferred, and why? `scanner-recovery`
   records fixed reasons, outcomes and decisions with tab/generation/document
   context. Empty interiors and unreadable cards receive distinct failure codes.
3. Which logical target failed across rounds? `targetId` is the first 128 bits
   of SHA-256 of the internal target key. It is correlation metadata only;
   raw keys, query strings, URLs and private bodies never enter these logs.
4. Did a provider request hit a local rate gate, Discord HTTP throttling or an
   uncertain transport result? Validated shared error fields include status,
   provider code, effective retry delay, global flag and local/server/unknown
   provenance while preserving ambiguity and byte-sent metadata.

State observations emit on meaningful changes, with a one-minute unchanged
heartbeat; changing activity/retry timestamps alone do not defeat suppression.
Queues, cache sizes, event history and log budgets remain bounded. Diagnostics
cannot change scanner authority, committed intake or delivery decisions.
Strict browser/Python schemas retain legacy report compatibility. The updated
audit tool recognizes and safely projects the new evidence.

## Verification and rollout

Focused regressions reproduced the stale-input, recovery-race, badge parser,
failure-classification and telemetry defects before the fixes. Independent
review approved the combined changes. Final checks passed: **2,833 Python tests
and one existing platform skip**, **1,086 JavaScript tests**, Ruff, protocol
generation, JavaScript syntax, manifest parsing, server startup and **20 real
browser fixture checks**. An initial full Python run exposed an expected-event
fixture needing the new safe metadata, which was corrected; it also hit a
Windows loopback socket abort in an authorization test. Five separate focused
runs and the final full suite passed without retries or weakened assertions.
That transient transport trigger remains unproven; the bounded investigation
and next evidence to collect are saved with the other receipts.

The exact eight-module backend overlay passed network-disabled packaged smoke:
both new browser events persist across reopen, and local throttle provenance is
projected without provider bodies. It activated at **13:35:11 CST** as image
`0739cd99b981`, preserving container security, environment, mounts, network,
command and all three settings hashes. Fresh checks confirm all eight live
module hashes, healthy notification/request workers, zero due paid backlog and
unchanged saved requests, watch definitions, trial grants and trial settings.
No synthetic provider notification was sent.

The owner reloaded **2.26.15** after the backend activation. Fresh status and
durable diagnostic reports confirm the version, all four scanners reachable,
settings/commands synchronized and no current status errors. The 118 general / 17
priority queries remain. General round 64 and department round 142 retained
their progress; priority round 374 completed its carried Bed target. Homepage
generation 45 naturally read through page 12 and completed. Existing unresolved
department work remains reported honestly; this bounded check is not proof of
complete-round coverage or future revisit speed.

The actual previously failing Bed listing **20321560444** was naturally extracted
at **13:44:17 CST**, retaining its own $35/out-of-stock evidence and excluding
both variants. Intake acknowledged the page and the target completed at
**13:44:22 CST**, instead of timing out. This is live parser-repair proof; it
did not generate an in-stock alert. No synthetic provider notification was sent.

The backend durably received state observations and hashed target metadata for
all four lanes with **zero sequence gaps, rejected events, storage/sync failures
or unacknowledged overwrites** in the fresh receipt. Natural backend error events
also demonstrate `status:429`, `rate_limit_source:local`, `outcome:not_submitted`
and the effective retry delay without provider bodies. Recovery guards and
failure paths use the regression/fixture evidence; none was forced live.

Evidence receipts are in ignored `data/navigation-audit-20261104/`, especially
`live-scanner-confirmation.json`, `after-reload.json` and `verification-gates.json`.
The browser audit command hit its configured byte limit while reading retained
history, so its aggregate counts are a partial bounded scan; fresh snapshot and
exact item receipts establish the activation claims above. Chrome's browser
security policy blocks its internal extension manager; the owner performed the
reload. No browser-policy workaround was used.

No social scraper, extra scanner lane, synthetic member notification or purchase
is authorized by this task. Existing scanner/settings/data must survive rollout.
