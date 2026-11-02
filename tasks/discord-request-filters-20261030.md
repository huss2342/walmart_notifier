# Personal requests: minimum value and bulk additions

October 30, 2026 (America/Chicago).

Owner authorized these two features from member feedback and subsequently
authorized deployment. Implementation is deployed and verified. No synthetic
member watch, test DM, guide publication, role change, commit or push was performed.

## Accepted behavior

- `/request add item:recliner min_value:100` watches values of $100 or more.
- `/request add item:recliner, chair, reclining chairs min_value:100` adds three
  independent persistent watches sharing one minimum. The equivalent item text
  `recliner, chair, reclining chairs $100+` is accepted.
- Minimum is optional, inclusive, expressed in USD and stored as integer cents.
  Unknown values do not meet a minimum. Zero removes the filter; omitted edit
  input preserves it. Existing watches migrate with no minimum.
- Commas separate alternative phrases; words in a phrase retain AND matching.
  A batch is atomic and keeps the existing ten-watch member limit. Equivalent
  phrases within a batch count once; already-saved watches must be edited.
- Watches stay active until deleted; sent products remain deduplicated per
  member even when several watches match. Latest observed values are checked
  again at submission. Existing Pro, channel and DM privacy guards remain.

## Implementation and verification

Store/matcher: `discord_bridge/request_store.py`; handlers, modal, labels and
SDK/registration contract: `discord_bridge/request_commands.py`. Documentation:
`discord_bridge/README.md` and `tasks/discord-onboarding-draft.md`.

Focused command regressions first demonstrated eight missing behaviors. Final
integrated request suite: **183 passed**, including real SQLite migration,
restart, concurrency, bulk atomicity, fresh observation/revalidation and fake
Discord interactions. Provider calls are mocked; no live user DM was sent.

Independent review: **APPROVE**, after fixing a reproduced request-list length
overflow. A ten-watch list with long queries, maximum minima and delivery errors
now stays under Discord's 2,000-character content limit, including paused DMs.
Both ready/paused regressions failed before the display cap and pass after it.

Repository Ruff, scoped format checks, whitespace checks and server `--help`
pass. Full Python regression suite: **2,211 passed, 1 intentional platform skip**
in 203.49 seconds. The subsequent 183-test request run covers the final list cap.
Windows tests used a fresh `data/qa-request-*` basetemp and the established
elevated test execution workaround for sandbox-created directory ACLs.

## Deployment receipt and hosting

Activated October 30 at 5:34 PM CDT. Hosting is local Docker Desktop on this
Windows PC, container `reviewer-item-notifier`, Compose project `reviewer-notifier`,
loopback `127.0.0.1:8787`; persistent data is the repository's `data` bind mount.
Image: `sha256:6802e6561e23689f61616706d86fee77218b3e596534b6d5c578c0596891c57d`.

Only two request modules were overlaid on the exact prior live image. The
canonical store had separately pending monitoring changes; those were excluded
from this release while preserving them in source. Independent narrowed-overlay
review approved. All 183 request tests pass against the exact staged modules and
copied live dependencies. The actual candidate image passed network-disabled
Linux bulk/minimum/latest-value/restart/deduplication/SDK checks.

Consistent SQLite backup and sanitized receipts are under
`data/request-minimum-bulk-deploy-20261030/`. Environment, settings, mounts,
security/host configuration, networks, user and launch command all match the
prior container. Rollback image: `reviewer-notifier:before-request-filters-20261030`.
The additive migration and integrity check pass; all 12 existing watches remain.

`request_setup register --apply` passed its privacy audit and updated the existing
command `1563633366450970685`. Live API readback verifies the expected add/edit
options and all four unchanged permission overrides. Discord omits
`required:false` in readback; verification normalizes that observed API default.
Health confirms request readiness, connected Gateway, running DM worker and
enabled live sending, without new request errors. No test user DMs were sent.

Any separately prepared monitoring image based on the old live modules must
rebase on this release to preserve these features. The updated onboarding draft
remains unpublished. Workers and temporary tests finished; no preview/server
resources remain. The existing production container stays running.
