# Solar personal-alert investigation

October 30, 2026, America/Chicago. Owner authorized investigating a member report
that a generic `solar` request stopped notifying after approximately twenty
minutes, and adding enough logs to diagnose the next occurrence. Owner also
authorized coordination with the active coverage/performance and request-feature
chats to combine overlapping sender changes and preserve their releases.

## Findings

- There is no maximum total notification count or twenty-minute watch lifetime.
  `MAX_REQUESTS=10` limits saved watches; `ALERT_LIFETIME_SECONDS=1200` limits
  individual unsent outbox entries. Watches persist until deleted. A product
  already successfully sent to a member stays deduplicated across requests.
- The live SQLite ledger contains **37 solar-titled products**, all `sent`, for
  one member. Read-only GETs of the first and last existing Discord messages
  confirm both explicitly matched the `solar` request. Discord timestamps are
  **3:45:07 p.m. through 4:14:24 p.m. CDT**, approximately 29 minutes. Backend
  queue/log clocks place these roughly thirteen seconds later; use Discord
  message timestamps for actual delivery receipt timing.
- The same member's ledger had **61 confirmed product DMs** at the 5:29 p.m.
  snapshot, including other requests after the solar interval. Their DM status
  is `ready`; the personal sender and Gateway are running and ready. No pending,
  failed, expired or review-held alerts belong to this member at that snapshot.
- Six personal-route 429 events occurred during the solar interval, with
  subsequent successful sends. They did not impose a permanent cap. The one
  historical ambiguous-send review and the blocked-DM member in aggregate health
  belong to other members; they must not be attributed to this report.
- No `solar` watch is currently saved. Existing command code deletes a watch
  only through an owner-authorized delete; it has no automatic watch expiration.
  Old lifecycle changes and matching suppression were not logged. Therefore
  **the exact time/reason for the initial quiet period cannot be proved**.
  Repeat-product suppression and later deletion are consistent with the surviving
  evidence, but neither proves why notifications first stopped. Five currently
  available solar-titled products seen after the last solar alert have no sent
  member entry; without historical watch state they are not proof of a miss.

Evidence: `data/request-notifications-20261030/incident-snapshot.json` is a
redacted snapshot. `inspect_solar.py` performs only SQLite/health/provider reads;
it neither writes messages nor replays alerts. Operational files under `data/`
remain private and ignored by Git.

## Implemented diagnostics

- `request_changed`: add/edit/delete after transaction commit, request ID,
  revision and pseudonymous member/query references. Deletion no longer erases
  all historical evidence that a watch existed. Request text and Discord user
  IDs are excluded.
- `request_dm_status`: DM readiness changes.
- `request_match`: fresh-observation matches by watch, including new/rearmed
  queued member/product pairs, already-sent suppression and uncertain-send holds.
  Counts are watch/product matches; overlapping watches can count the same pair.
  Events emit after the intake transaction commits.
- Personal `dispatch_*` and `request_provider_error` events carry
  `audience=personal`, the stable outbox ID, pseudonymous member reference,
  product ID, attempt, phase and safe numeric provider status/code.
- `request_queue_summary` at most once per minute even when idle: due backlog,
  retry waits, blocked/untested DMs and terminal reasons, including expiry/review.
  These distinguish an idle healthy sender from blocked or stalled work.
- A second failure while retrying/settling an error no longer escapes the
  sender's tick. The durable lease/sending state remains authoritative and an
  uncertain submission is never blindly replayed. SQLite numeric classification
  and further poll/recovery telemetry were integrated with the coordinated
  coverage/performance chat.

Use `audience=personal` to separate personal activity from public Pro feeds.
Find the reporter with `request_store.member_ref(discord_user_id)`, then follow
their `request_id` / `dispatch_id`. For a known deleted phrase, its `query_ref`
is the first 32 hex characters of SHA256(`kind + ':' + matcher`) from
`parse_query(phrase)`. Compare lifecycle records, match/dedup counts, provider
outcomes, summaries and collector `intake` events. No match events with continuing
intake indicate no eligible matches for that saved watch; stopped intake requires
scanner investigation. A deleted watch has an explicit lifecycle event.

Logs use the existing 60-day target / 32-MiB delivery-log storage backstop.
Observability failures cannot roll back committed watches or queue intake,
trigger additional provider writes, or expose provider bodies or exception text.

## Verification and activation

**Live at 5:38:54 p.m. CDT.** Seven-module image `5418be51ee3b` overlays exact
healthy request-feature image `6802e6561e23`. The first candidate deliberately
excluded then-pending bulk/minimum features; after that chat deployed them, this
release was rebased onto its exact deployed source and preserves those features,
schema, commands and permission overrides. The coordinated recovery improvements
are included. Environment, full host configuration, mounts, networks, launch
configuration and scanner/relay settings all compare unchanged.

**290 tests passed** against the final isolated candidate, with no deselections.
This includes all request/filter/command/store/setup/runtime checks, eight new
diagnostic cases, seven new recovery cases, monitoring, collector, burst,
transport and priority tests. Imports were explicitly pinned to the release
candidate. Candidate/source hashes match; compile, repository Ruff, startup and
whitespace checks pass. An independent read-only review approved the combined
source and final rebased candidate. The actual built Linux image also passed a
network-disabled integration smoke with fake Discord transport, covering
bulk/minimum preservation, persistent logging, deduplication, and sending a new
product after twenty minutes.

Live health confirms the Gateway connected, requests ready, personal sender
running, last poll `ok`, queue metrics available, zero personal due/failed/expired
jobs, and zero dropped monitoring events. A real `request_queue_summary` with
`audience=personal` is persisted in `delivery.jsonl`; historical review/blocked
counts are preserved. No synthetic member messages, watch edits, or historical
alert replays were performed. Both other chats received the exact final image
and preservation receipt before their next overlay.

Evidence under `data/request-notifications-20261030/`: `activated.json`,
`docker-baseline.json`, `smoke-result.json`, `release/candidate.json`,
`release/diagnostics.patch`, and `release/candidate-tests.xml`. Rollback image:
`reviewer-notifier:before-request-diagnostics-20261030` (preserves minimum/bulk
features but removes these diagnostics and recovery improvements). Build/activate
helpers reject source, image or runtime drift and automatically restore the base
image if activation checks fail.

The first full Python run sampled the other chat's five RED tests during its
implementation; these now pass. Its subsequent combined full Python suite
completed with **2,236 passed and one existing platform skip in 196.17 seconds**;
the result was verified in
`data/coverage-performance-fix-20261030/python-full.txt`. Repository Ruff and server
startup checks also pass. The broader scanner rollout remains owned by that chat;
this investigation has no further deployments planned during its activation.
All helper agents have finished; no task-owned test services remain running.
