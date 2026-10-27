# Discord alert outage from category heading changes

October26,2026 America/Chicago. **Complete: delivery restored and verified.**

## Accepted scope and evidence

Owner reported pro-all stopped around08:05. During diagnosis they confirmed
changing the category names and supplied a screenshot. Keep those names, restore
normal delivery and preserve privacy, tier timing, expiry and duplicate rules.

The read-only queue ledger confirmed last pro-all send13:05:53.937976 UTC,
08:05:53 Central. Items continued arriving while delivery cycles repeatedly
reported privacy_blocked/privacy_audit with zero sends. Read-only Discord audit
returned exactly two findings: tracked starter and pro-price name/type mismatch.
Single channel inventory confirmed both IDs still existed as root categories,
type4, with these labels:

| Key | Old configured heading | Owner's heading |
| --- | --- | --- |
| starter | STARTER - $5 / MONTH - 5 MIN DELAY | STARTER - 5 MIN DELAY |
| pro-price | PRO - $20 / MONTH - BY VALUE | PRO - BY VALUE |

All actual role/ACL/privacy checks passed. The audit was too strict about
cosmetic category headings and stopped all feeds, including pro-all. Discord
also throttled repeated audit GETs; diagnosis honored returned retry waits.
Earlier scanner-only validation had observed privacy_blocked console events
without tracing delivery. The user was told this was missed; scan progress and
container health must not be reported as delivery health.

## Fix and boundaries

Source discord_bridge/layout.py defaults match the owner's names; prices and
plan rules remain unchanged. discord_bridge/provision.py resource findings have
an explicit allow_category_renames=False default. Runtime audit passes true so
only known category names may vary. Known IDs, channel/category types, parent
relationships, exact ACLs, unknown resources, audience roles and channel names
remain mandatory. Provisioning still uses strict names and refuses changed
resources rather than adopting them. No Discord role/channel/permission edit or
manual/test product post was sent. Existing scheduled delivery resumed normally.

Seven new tests in tests/test_discord_bridge_provision.py reproduce the name
failure and verify renamed headings still reject wrong type/parent/extra ACL/
missing category. Further tests prove provisioning and channel-name validation
stay strict and no remote/local state writes occur during audit/refusal.
Independent read-only reviewer approved;108 provisioning tests passed, with
additional replacement-ID and zero-write probes. Related provision/collector/
burst suite passed137 tests before the final two scope tests were added; final
seven scope/incident tests passed together.

## Deployment and live proof

Docker thin image reviewer-notifier:category-names-20261026 also tagged local,
based on the exact prior live image. Only layout.py and provision.py copied;
saved source comparisons confirm no unrelated changes. Immutable new image
sha256:4ecc83730b5db97c23c7792af180662155bbaccd220c5cf3a590f49a6a77d390.
Compose recreate preserved environment hash, mounts, protections, networks,
runtime user and command. Container healthy. Rollback tag:
reviewer-notifier:before-discord-category-names-20261026.

Fresh direct audit ok:true, findings:[]. Direct bounded Discord history read
confirms pro-all's latest bot-authored five-embed message at00:05:10.073 UTC
October27, **19:05:10 Central October26**. Queue ledger and dispatch_sent events
confirm first recovery19:04:35 and ten pro-all item destinations delivered in
two messages. Pending pro-all0 at the saved snapshot; other paid routes resumed.
6703 pro-all destinations expired during the gap under the existing30-minute
TTL. They were not requeued; expired/stale replay is outside this repair.

Receipts in data/discord_bridge:
- delivery-incident-audit-20261026.json: original two audit findings and queue.
- delivery-incident-recovered-20261026.json: passing audit and latest message
  metadata only, no message bodies or credentials.
- delivery-name-fix-20261026/queue-recovery.json: read-only ledger aggregate.
- delivery-name-fix-20261026/deployment: before/after config checks, two-file
  source snapshots/diffs, Dockerfile and candidate copies.
- delivery-name-fix-tests-20261026.txt: related checks.
- delivery-name-fix-python-verified-20261026.txt: final full run, **1746 passed,
  one platform skip, zero failures**; counted progress receipt in
  delivery-name-fix-20261026/test-summary.json.
- delivery-name-fix-20261026/health-recovered.json: authenticated health snapshot
  confirms live sending and Pro/Starter pending0; Free is draining its delayed
  backlog under learned Discord rate limits.

An earlier final full run hit WinError10053 in the existing oversized HTTP-body
test; isolated rerun passed, while the pre-tolerance full run had also passed.
Final full run without overlapping deployment passed. No test or
assertion was weakened. Ruff, byte compilation and server --help passed.
No extension change/reload, recurring automation, or manual alert replay required.
No owned test servers/processes remain; the production service is left running.
Unrelated repository edits were preserved. No commit/push requested or performed.
