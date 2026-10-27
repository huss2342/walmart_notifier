# Discord delivery operations

Updated October 23, 2026. The sender writes private operational logs;
this does not create a scheduled Codex chat or post technical logs to members.

## What takes priority

Priority slots choose Pro before Starter/free, then higher observed retail
values before lower values. Equal-value ties use freshness, Pro all-items and
age. Newly formed full Pro bundles keep up to four highest-value positions and
reserve the last card for older work in that channel. Separate fair-route slots
help quieter routes, overdue Starter and due free history keep moving.

The first ready item never waits for a batch to fill. In-flight sends and frozen
retry bundles are not interrupted/repacked. Known per-channel/global cooldowns
still apply. Catch-up cards share the leader's expiry group: items within 60s of
expiry stay separate from fresher work, so an old item can still expire under
overload. Priority is a scheduling policy, not a guarantee that every expensive
item arrives before every cheaper concurrent send or that no alert ever expires.

Starter remains positive retail values below $40, at least five minutes after
bridge intake. Every free snapshot waits at least 24 hours. Pro has no
intentional release delay. Paid work expires after 30 minutes; historical free
work has seven days to catch up after release. These clocks are unchanged.

## Watch the logs

Run from the repository root in PowerShell:

```powershell
Get-Content .\data\discord_bridge\delivery.jsonl -Tail 10 -Wait
```

The same structured events appear in the existing service's console:

```powershell
docker compose logs --tail 100 -f notifier
```

Stop either live view with Ctrl+C. This stops the viewer, not the sender.
The JSONL log survives container replacement in the existing data bind mount.
It rotates daily (UTC) or at 5 MiB, compressing completed segments as
`delivery.jsonl.<timestamp>-<id>.gz`. The owner selected a **60-day history target**
on October 23. Cleanup removes the oldest segments first, with a **32-MiB total
disk backstop** and at most 256 archives; heavy traffic can shorten that history.
Cleanup runs on writes, at rotation and at least hourly while logging. A segment
ages from its last write, so a few records can remain up to one segment longer.
Existing numbered backups remain subject to the same age and space limits.
SQLite remains the persistent outcome ledger; log cleanup never deletes queued
deliveries, dedupe records, membership state or database backups.

Backend Python logs also persist in `data/notifier.log` with a 60-day target,
2-MiB segments and a 16-MiB total cap. Delivery events are excluded from that
file because they already have the JSONL archive. Host recovery uses
`data/browser-recovery.log`, 256-KiB segments and a 2-MiB total cap, also targeting
60 days. Docker's separate console cache remains capped at three 10-MiB files;
use the persistent files for older history. These log families total about
80 MiB at their caps, plus a temporary segment during compression and tiny lock
files. The extension/backend browser diagnostic snapshot remains a short,
bounded troubleshooting cache (300 events, at most seven days), not an archive.

For example, inspect a compressed delivery segment without extracting it:

```powershell
python -c "import gzip, pathlib; p = sorted(pathlib.Path('data/discord_bridge').glob('delivery.jsonl.*.gz'))[-1]; print(gzip.decompress(p.read_bytes()).decode())"
```

Log files use private permissions on creation. Threads serialize writes and
processes use an OS file lock; a competing writer reports a dropped event
rather than blocking delivery or corrupting rotation. A failed compression
leaves the original log in place. The policy takes effect after rebuilding the
notifier and restarting the host recovery helper; no extension reload is needed.

## Read the signals

- `dispatch_sent`: confirmed Discord response and atomic local settlement.
  `dispatch_id` stays stable across retries; `run_id` identifies the process and
  `correlation_id` ties events to a delivery cycle. Job/item IDs, observed values,
  tier and route locate the affected work. Provider-response duration and delay
  since scheduled release are separate fields.
- `dispatch_throttled` / `audit_throttled`: route/global retry wait; not a
  permanent failure. `dispatch_retry` is a transient error or controlled stop.
  `dispatch_failed` needs investigation of the enumerated status/reason.
- `privacy_blocked`: fix the permission/audit problem before sending resumes.
  Do not disable the audit. `worker_error` reports a sanitized error class.
  Since October26, renaming a mapped category heading is cosmetic at runtime;
  IDs, types, parents, ACLs and channel names remain checked. Provisioning stays
  strict. Do not treat scraper progress or a healthy container as evidence that
  Discord delivery works: verify recent `dispatch_sent` or confirmed messages.
- `queue_summary`: checked every cycle, normally every 60 seconds including idle
  time; a long-running delivery cycle can postpone the next summary. Individual
  dispatch events still record each settled/retried batch. `by_tier.future` is
  scheduled tier waiting; `due` is released unsent work; `retry_wait`,
  `throttled` and `leased` explain outstanding work. These counters can overlap.
  `oldest_due_age_seconds` reveals backlog independently of future free history.
- Terminal totals/reasons retain failed, expired and skipped history. Compare
  successive summaries to find new failures/expirations; an unchanged historical
  total is not a current incident. Routing skips can be expected after an item
  observation changes. Never replay expired items just to clear a counter.
- Recent latency p50/p95/max covers confirmed destination deliveries in the last
  five minutes, measured after the tier's scheduled release. No samples yields
  null, not zero delay. Old sends without a stored `sent_at` are excluded. This
  measures backend-to-Discord delivery, not Walmart discovery or mobile push.
- Authenticated `/health` exposes the same queue metrics and `monitoring` log
  status. Non-null `last_error` or increasing `dropped_events` means log storage
  needs attention; log failure does not cause a second provider write.

Counts describe item destinations, not people or unique products. One product
can have all-items, category, price, Starter and free destinations. A partial
delivery can therefore appear in both sent and expired totals.

## When something needs attention

For increasing due age, first inspect tier counts and active cooldowns. If a
route is waiting, let its retry deadline pass and watch whether confirmed sends
resume. For repeated provider failures or a privacy block, inspect the stable
status/reason, fix connectivity/credentials/permissions and verify recovery with
the next queue summaries. Avoid manually duplicating pending jobs.

For new expirations, retain the evidence and inspect recent intake volume,
throttles and latency. Report delays through the owner's #updates channel if
members are affected; no member announcement is automatically published by this
instrumentation. No fresh intake by itself proves neither a broken collector
nor a healthy one; use the existing collector health/watchdog evidence too.

Logs deliberately omit tokens, raw page/HTTP payloads, provider error bodies,
product titles and customer account data. Error classification is allowlisted.
Timestamps in the JSONL are Unix seconds; user-facing reports use America/Chicago.
