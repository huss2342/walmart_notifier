# Evening Discord delivery recovery — October 25, 2026

The owner's earlier instruction to fix all delivery issues remains the accepted
scope. The evening status check found another block and carried the repair
through tests, independent review, deployment and actual Discord verification.

## Cause

Relay 2.19.1 continued scanning and normal native page transitions. The last Pro
send before the incident was 11:46:54 AM Central; the first privacy-audit block
was recorded at 12:47:20 PM. By the 7:13 PM check, 83 Pro and 14 Starter jobs
were due, and no messages had been sent in the preceding five minutes.

The live `welcome` channel matched its expected effective permissions, but had
one extra member overwrite with both `allow=0` and `deny=0`. The exact comparison
treated the inert entry as policy drift. Under Discord's
[permission calculation](https://docs.discord.com/developers/topics/permissions),
zero masks change no inherited bits. No channel or role permissions were changed
by this repair; the neutral entry remains present.

## Repair and verification

`discord_bridge/provision.py::_acl` now removes validated zero-mask entries from
the canonical comparison. It first validates actual integer target types,
explicit integer or ASCII-decimal-string bitfields, nonnegative/disjoint masks,
valid IDs and unique targets. Nonzero additions/removals still fail the audit.

Four live-shaped regression cases failed on the original source. Independent
review then caught permissive numeric coercion exposed by normalization; eight
malformed-input cases reproduced it, and validation was tightened before any
deployment. All 24 new cases pass. The complete Python suite passes **1,478
tests**, with one existing platform skip and no failures/errors. Lint, scoped
formatting and whitespace checks pass; independent review approved the final
delta. The candidate passes the read-only live audit and isolated packaged
comparison/startup checks.

Only `provision.py` was layered over the actual running image. Container env
values matched Compose before replacement. The service was recreated at
7:21:27 PM Central with its data mount, credentials, read-only filesystem,
loopback binding, privileges, request bot and source from other chats preserved.
No schema change, history replay, queue reset, extension reload or new privileges.

The replacement is healthy. Real Pro messages resumed, and a GET verified this
[bot-authored Pro message at 7:22:14 PM Central](https://discord.com/channels/1562530389866717295/1562537637787603055/1564071623131140147).
The request runtime is ready with a connected Gateway; retained alerts are
draining through existing provider limits. Live health and message receipts are
saved with the incident evidence.

At 7:23:12 PM, the follow-up confirms zero due Pro/Starter jobs and zero failed
jobs, with 159 item deliveries in 38 messages since recovery. Free history is
still catching up. Relay 2.19.1 remains active and sweeping without verification
pause; no extension reload was needed.

## Evidence and rollback

Evidence: `data/incident-20261025-evening/`, including original source/test and
channel snapshots, RED results, full test text/JUnit XML, candidate live audit,
post-deploy health and actual Pro-message verification. A consistent SQLite
backup is `collector-before.sqlite3` in that directory.

Baseline image: `reviewer-notifier:before-neutral-acl-20261025`, original image
`sha256:619ecd60b2f67ac9878303667103a186ae07168e2adea5a26571ed07988bab90`.
Candidate: `reviewer-notifier:neutral-acl-20261025`, now also tagged
`reviewer-notifier:local`. To roll back, retag the baseline as
`reviewer-notifier:local` and run `docker compose up -d --no-build notifier`.
Rollback restores the old false-positive behavior, so retain the evidence for
diagnosis. The database backup is precautionary; the live database was preserved.

Changed files: `discord_bridge/provision.py`,
`tests/test_discord_bridge_provision.py`, this receipt and the current handoff.
Other pending changes were preserved. No commit or push was performed.
