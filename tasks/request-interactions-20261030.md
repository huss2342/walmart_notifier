# Overlapping requests and interaction timeouts

October 30, 2026, America/Chicago. Owner supplied member questions about retaining
requests, comma-separated limits, repeated phrases across additions, DM testing,
and “Reviewer Alerts Bridge didn't respond in time.” Authorized scope: fix
overlapping additions and investigate/repair interaction failures, preserving
existing watches, filters, deduplication and prior releases.

## Member-facing behavior

- Existing requests remain saved across updates. Edit them; a fresh list/reset is
  unnecessary. Successfully delivered products still alert only once per member.
- Each comma-separated phrase is a separate watch. `ice maker, ice machine` uses
  two of ten active watches. `recliner, couch, chair, couch, sofa` uses four unique
  watches. Shared words are allowed; all words within one phrase must match.
  Each phrase allows 150 characters, and the entire add input allows 512.
- Bulk additions now reuse existing normalized phrases and add only new ones.
  `ice, ice maker, ice machine` followed by `ice maker, ice tray, ice cubes` leaves
  five unique watches. Reuse does not change IDs, filters, timestamps, revisions,
  matching clocks or notification history. Replies show new vs already-saved
  counts. All-existing batches succeed; only new phrases consume capacity.
- An explicitly different minimum on a reused phrase fails atomically with
  `/request edit` guidance. An omitted minimum preserves an existing filter.
  This prevents a later bulk addition silently widening a prior watch.
- `/request test-dm` sends the existing private transport test and restores DM
  readiness when it succeeds. It does not manufacture a matching product, consume
  a watch, reset deduplication, or replay historical alerts. Matching product DMs
  require an eligible fresh collector observation.

## Timeout evidence and repair

Live checks found the expected application identity, no HTTP interaction endpoint,
no duplicate global command, correct guild request subcommands, connected Gateway,
and healthy personal sender. Existing logs show request changes at 19:57 and
20:00–20:15 CDT, plus one generic command-provider error at 20:14; they do not prove
which interaction caused the member's 19:55 warning. No continuous outage was
established.

The installed Discord.py 2.7.1 SDK reproducibly removes request-view handlers after
300 seconds of inactivity while leaving their buttons visibly enabled. Clicking
an expired Delete through the actual SDK `ViewStore` silently produced neither
acknowledgement nor reply. Edit modals likewise dropped late submissions. Earlier
callback-only tests bypassed this boundary. Valid slash commands passed through
the actual command tree and namespace handling locally.

The fix binds the returned private followup message to its view, disables controls
on timeout and edits that exact message with fresh `/request list` guidance.
Initial list copy also explains inactivity expiry. Bounded-lifetime SDK ownership
and weak registries distinguish active views/modals from expired or pre-restart
controls. The Gateway observation callback privately acknowledges stale controls
with guidance, including old random IDs left over from before this release. It
never replays Delete, Edit or request creation. Active SDK callbacks retain their
normal authorization; already-acknowledged interactions receive no extra fallback.

`request_interaction` events add safe received/acknowledged/replied/failed/expired
stages, fixed command names, hashed interaction/member correlation, initial-ack
duration, interaction age and numeric provider errors. They exclude input text,
raw custom IDs, tokens, payloads and exception messages. Gateway health exposes
the last received interaction time. An acknowledgement failure returns before
any watch mutation. Logging remains best effort and cannot fail a provider write.

Discord requires an initial response within three seconds; acknowledgement stays
ahead of membership, SQLite and DM work. See the official
[interaction response documentation](https://github.com/discord/discord-api-docs/blob/main/developers/interactions/receiving-and-responding.mdx)
and [SDK interaction API](https://discordpy.readthedocs.io/en/stable/interactions/api.html).
The repair closes a reproduced timeout path; it does not retroactively prove that
the member's earlier warning used stale controls. The new logs support the next
precise diagnosis if fresh slash commands still fail.

## Verification and release

Store regressions reproduced nine initial failures, then 82 focused checks passed.
New interaction tests cover actual expired view/modal dispatch, full command-tree
dispatch, active Delete exactly once, private message binding, failed-ack safety
and overlap feedback. Repository Ruff, startup/import and whitespace checks pass.
Independent read-only review approved all three implementation modules.

The exact Linux candidate passed a network-disabled smoke using fake transport
and temporary storage: five unique overlapping watches, unchanged repeated batch,
bound expired-control update, numeric error diagnostics and no mutation after
failed acknowledgement. No member requests, synthetic live DMs, command
registrations or permission changes were performed during verification.

**Activated at 22:28:30 CDT on October 30**, image `2f76ba56305b` over exact
`ccf65816deaf`. Full Python regression: **2,255 passed, one existing platform skip**
in 201.728 seconds (JUnit records 2,256 total, zero failures/errors). Repository
Ruff, server startup and whitespace checks pass. No JavaScript changed.

Actual post-deployment health confirms requests ready, Gateway connected, personal
sender running, poll `ok`, queue metrics available and zero dropped log events.
The saved-watch table hash is identical before and after activation: all 12
server-wide watches remain intact. Environment, full host configuration, mounts,
networks, launch configuration and scanner/relay settings all compare unchanged.
The new last-interaction health field is present; a real member retry is still
needed to establish whether their original timeout involved stale controls or a
different provider/client failure. No customer interaction was impersonated.

Evidence and rollback preparation are under `data/request-interactions-20261030/`:
exact live/baseline sources,
`release/candidate.json`, `release/interactions.patch`, `smoke-result.json`,
`activated.json`, `health-before.json`, `health-after.json`, full-suite logs/XML
and drift-checked build/activation helper. The image overlays
only request_commands.py, request_store.py and monitoring.py over exact live
`ccf65816deaf`; all scanner, pricing/bulk, sender recovery and existing diagnostics
remain part of the base. Rollback tag:
`reviewer-notifier:before-request-interactions-20261030`; rolling back removes this
overlap/control fix while retaining the preceding scanner, filter and sender
releases. All helper agents and test processes are finished; no temporary test
services remain running.
