# General alert forwarding — November 21, 2026

The owner requested member forwarding from Pro feeds into General while keeping
Pro feeds read-only, then confirmed uploads should remain blocked. General now
allows Embed Links so forwarded alert cards can retain their embeds. Discord
does not expose a separate permission for forwarding. Forwarded file attachments
still require Attach Files; that permission remains unavailable to members.

Only General's @everyone overwrite changed: allow `2147552256` became
`2147568640`, and deny `16384` became `0`. No category, Pro channel, role base,
slowmode, application command, or AutoMod rule changed. General's existing live
topic remains unchanged; the manifest topic now describes forwarded alerts.

The canonical layout and strict runtime audit now require this General-only
exception. Two regressions first failed under the previous policy. The full
3,106-test run found only three trial integration failures caused by a fixture
rebuilding General's previous ACL; that fixture was corrected and all 11 trial
integration tests passed. A final combined provisioning/trial integration run
passed all 125 tests. One existing platform test was skipped in the full run.
Changed-file lint and whitespace checks pass. Repo-wide lint still reports 13
existing findings in unrelated Elite integration and server test files.

The replacement image layers only `layout.py` and `provision.py` over the running
image. Notifier environment values matched Compose before replacement. Live
read-back verified only General's ACL changed, uploads remained unavailable, and
all 18 Pro feeds remained read-only for Pro, Pro Trial, and their combination.
The deployed source hashes match the repository. The deployed runtime audit
passes, Docker health is healthy, and the request Gateway is connected. No
member-account forwarding test or public test message was performed.

Evidence: `data/forwarding-20261121/`, including channel snapshots, preview and
runtime audits, test output, the application receipt and health snapshot.

Rollback requires restoring General's previous overwrite **and** the previous
audit policy together. Restore the old overwrite from `applied.json`, tag
`reviewer-notifier:before-forwarding-20261121` as `reviewer-notifier:local`, then
run `docker compose up -d --no-build notifier`. The prepared application script
automatically restores both on deployment failure. No database schema changed.
