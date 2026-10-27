# Discord alerts execution checklist

Original integrated backlog. The later authorized standalone Discord preparation
is recorded in [ACTIVE.md](ACTIVE.md) and [the bridge guide](../discord_bridge/README.md).
Those isolated modules do not imply completion of the collector-integration,
source-permission, live account, billing or paid-pilot checks below. Design and
source references: [plan.md](plan.md). File lists are proposed future ownership,
not instructions to overwrite existing edits. All tasks are small/medium slices
of at most five files; expand narrowly if existing code requires a safer split.

## Task 0: Establish source and link feasibility

- [ ] Record the permission/permitted-source decision for a commercial feed and
      the monetization route's eligibility. Keep private beta separate from sales.
- [ ] Record manual desktop/mobile checks of observed review links on two invited
      accounts and negative cases, without submitting orders; choose review or
      ordinary-link fallback based on observed results.
- [ ] Agree pilot scope, prices, delays and draft capability map before writing
      module implementation specs.

Verify: a short evidence matrix distinguishes observed behavior, permission
obtained, unavailable evidence and product assumptions. Dependencies: none.
Files: `tasks/plan.md`, `tasks/link-test-results.md`. Scope: small.

## Task 1: Deliver a safe link metadata contract

- [ ] Keep product and review URL separately, preserving only the allowlisted
      sampling context, full title and visible image when present.
- [ ] Put promotional badge in its own field; leave department unclassified until
      classifier evidence exists. Exclude the collector's quota from public data.
- [ ] Preserve numeric item identity and existing ingest compatibility.

Verify: focused models/parser tests plus extension runtime cases cover the two
provided URLs, tracking removal, wrong host/scheme, variants and old payloads.
Dependencies: Task 0 decisions. Files: `extension/content.js`, `src/models.py`,
`src/sources/webhook_source.py`, `tests/test_models.py`,
`tests/test_extension_runtime.mjs`. Scope: medium.

## Task 2: Persist observations without replaying history

- [ ] Create versioned SQLite items/observations storage and explicit schema
      initialization in the mounted data directory.
- [ ] Upsert metadata and last-seen for existing IDs, retaining query evidence
      and first detection; isolate the current Telegram seen ledger.
- [ ] Default initial population to seed/shadow mode with no public sends.

Verify: temporary SQLite fixtures exercise rollback, repeated batches, restart,
initial seeding and 70k-row offline replay without contacting providers.
Dependencies: Task 1. Files: `src/catalog.py`, `src/server.py`,
`tests/test_catalog.py`, `tests/test_server.py`. Scope: medium.

## Task 3: Queue one durable private Discord delivery

- [ ] Transactionally create a single destination job per item/event.
- [ ] Lease pending jobs, store due/expiry/status/message ID, and resume after
      restart without relying on process sleep timers.
- [ ] Distinguish durable acceptance from remote success and reconcile failures.

Verify: fake time and mocked provider prove restart, timeout ambiguity, rollback,
duplicate ingest and expired-job behavior. Dependencies: Task 2.
Files: `src/outbox.py`, `src/catalog.py`, `src/delivery_worker.py`,
`tests/test_outbox.py`, `tests/test_delivery_worker.py`. Scope: medium.

## Checkpoint A: Durable intake

- [ ] Observation retries do not multiply jobs or reset timestamps.
- [ ] Restart/crash tests pass and no historical item was announced as new.
- [ ] Existing personal notification behavior remains verified.

## Task 4: Render and send one private Discord alert

- [ ] Add bot-authenticated REST delivery for one owner test channel, with an
      embed, safe URL buttons, observed value/category/time and no account quota.
- [ ] Honor rate-limit headers, bound retries, redact tokens, and suppress all
      unrequested mentions. Save remote message IDs and stable send nonces.
- [ ] Expose delivered, retrying, failed and expired states separately.

Verify: notifier/worker tests mock 200, 429, 403/404, timeout and secret-bearing
errors; send one explicitly labeled alert to the owner's private test channel
when execution is authorized. Dependencies: Tasks 1–3.
Files: `src/notifiers/discord.py`, `src/delivery_worker.py`,
`tests/test_discord.py`, `notifier.example.env`, `src/config.py`. Scope: medium.

## Task 5: Add category classification

- [ ] Add versioned rules, overrides, weighted phrases, accessory exclusions,
      evidence source and a visible unknown queue.
- [ ] Classify per item; query is a weak hint and a–z contributes no department.
- [ ] Enrich duplicate observations without sending another drop.

Verify: curated cases include sofa covers, desk organizers, earbuds cases,
pet toys, tools, trees versus ornaments, Yoto media and overlapping queries;
review a stratified 200-item sample and record mistakes and coverage.
Dependencies: Task 2. Files: `src/classification.py`, `src/taxonomy.json`,
`src/catalog.py`, `tests/test_classification.py`. Scope: medium.

## Task 6: Route price/category copies

- [ ] Add pure, versioned policy evaluating cents, primary category, cohort,
      destination and due time; unknown and zero values have explicit handling.
- [ ] Produce at most one copy per price/type destination and optional firehose.
- [ ] Maintain per-destination state; one success never suppresses another route.

Verify: table tests cover $9.99/$10, $19.99/$20, $39.99/$40, $79.99/$80,
$199.99/$200, unknown/zero, repeated queries, destination failure and policy edits.
Dependencies: Tasks 3–5. Files: `src/alert_policy.py`, `src/discord_routes.json`,
`src/outbox.py`, `tests/test_alert_policy.py`, `tests/test_outbox.py`. Scope: medium.

## Checkpoint B: Private feed

- [ ] One private end-to-end path works with ordinary-link fallback.
- [ ] Offline history replay creates expected routes with bounded queue growth.
- [ ] Category mistakes and send latency are measured; failures are visible.

## Task 7: Provision Discord roles and channels

- [ ] Generate an idempotent dry-run server manifest reflecting the proposed
      protected categories and one read-only Starter feed.
- [ ] Apply the reviewed manifest during authorized execution; store resource IDs.
- [ ] Test free/Starter/Pro/interest-role combinations and direct message links;
      no unprotected category channel may mirror protected content early.

Verify: permission-matrix report plus actual test accounts; second provisioning
run makes no duplicates. Do not send invites or turn on billing as a side effect.
Dependencies: Task 6. Files: `tools/provision_discord.py`,
`config/discord-server.json`, `tests/test_discord_provisioning.py`,
`tasks/discord-permission-results.md`. Scope: medium.

## Task 8: Prove delays and stale behavior

- [ ] Deliver Pro at detection, Starter after five minutes, and the free capped
      historical recap the next day, without shared-channel visibility leaks.
- [ ] Persist timing across restarts; repeated observations do not reset delays.
- [ ] Suppress positively unavailable/expired paid jobs, retain last-observed
      context, and label all historical recaps.

Verify: fake-clock integration at t=0, 4:59, 5:00 and next-day boundaries,
restart mid-delay, late retries, partial scans and unavailable observations.
Dependencies: Tasks 3, 6, 7. Files: `src/alert_policy.py`,
`src/delivery_worker.py`, `src/recap.py`, `tests/test_alert_policy.py`,
`tests/test_recap.py`. Scope: medium.

## Task 9: Decouple Telegram from discovery

- [ ] Queue personal matches durably with the existing saved rules and separate
      Telegram destination dedupe; retain useful owner diagnostics.
- [ ] A Telegram network failure cannot hold browser progression or duplicate
      already delivered Discord messages.
- [ ] Cut over with a recorded watermark/seed policy and rollback switch;
      disabling Discord keeps the personal notifier usable.

Verify: pipeline/server regressions plus fault injection where one provider
fails and the other succeeds; explicitly check existing tests/status semantics.
Dependencies: Tasks 3, 4, 8. Files: `src/pipeline.py`, `src/server.py`,
`src/delivery_worker.py`, `tests/test_pipeline.py`, `tests/test_server.py`.
Scope: medium; split status follow-up if needed.

## Task 10: Connect paid entitlements

- [ ] Use the selected eligible billing platform; map authoritative active
      subscription/role state to tier and paid-through expiry.
- [ ] Reconcile missed events, cancellations, refunds, upgrades and downgrades;
      pending billing never grants access. Verify paid-app obligations for DMs.
- [ ] Publish exact benefits, source limitations and cancellation/refund terms
      during separately authorized commercial setup.

Verify: provider test mode/fixtures for activation, duplicate event, expiry,
refund, renewal and role repair; actual member-role permission retest.
Dependencies: Tasks 0, 7–9 and billing selection. Files:
`src/entitlements.py`, `src/membership_sync.py`,
`tests/test_entitlements.py`, `tests/test_membership_sync.py`,
`docs/membership-operations.md`. Scope: medium.

## Checkpoint C: Paid-pilot readiness

- [ ] Source permissions and monetization eligibility are resolved and recorded.
- [ ] Free/paid isolation, delayed release, expiry and downgrade tests pass.
- [ ] Existing Python/Node checks pass; production observation confirms healthy
      collection/delivery and a usable fallback when the browser pauses.
- [ ] Begin a capped pilot; collect feedback and renewal evidence before scale.

## Task 11: Add opt-in personal DMs after pilot feedback

- [ ] Add saved category/keyword/value filters with per-user entitlement checks,
      quiet hours, timezone, pause/mute and explicit DM opt-in/unsubscribe.
- [ ] Enforce match caps and recipient dedupe; closed DMs do not block feeds.
- [ ] Obey tier value ceilings and original release times for every DM.

Verify: command/worker tests cover independent user settings, opt-out,
blocked DMs, quota caps, upgrades/downgrades and delayed eligibility.
Dependencies: Task 10 and demonstrated demand. Files:
`src/discord_preferences.py`, `src/personal_alerts.py`,
`tests/test_discord_preferences.py`, `tests/test_personal_alerts.py`,
`docs/membership-operations.md`. Scope: medium.

## Task 12: Reduce variant noise without hiding products

- [ ] Bundle only high-confidence families, retaining every observed variant ID,
      value and safe link; unresolved families stay separate.
- [ ] Apply per-variant entitlement before grouping and update existing messages
      for metadata changes without new-drop spam.
- [ ] Expose a filtered view or expansion for every eligible variant.

Verify: fixtures include same-title distinct products, truncation collisions,
mixed-price families, new variants, category revisions and family overrides.
Dependencies: Tasks 5, 6, 10. Files: `src/families.py`, `src/catalog.py`,
`src/notifiers/discord.py`, `tests/test_families.py`, `tests/test_discord.py`.
Scope: medium.

## Task 13: Add configurable keyword coverage

- [ ] Add saved custom queries and optional weak taxonomy hints while preserving
      regular, a–z and High Value mode compatibility.
- [ ] Show scan/query duration, coverage and overlap; do not silently speed up
      requests or bypass verification pauses.
- [ ] Collect category requests as coverage improvements for the service.

Verify: Node runtime tests cover list validation, empty searches, recovery,
duplicates, saved-setting upgrade and existing pacing behavior.
Dependencies: Task 5 and pilot coverage evidence. Files:
`extension/sweep-modes.js`, `extension/options.html`, `extension/options.js`,
`extension/content.js`, `tests/test_extension_runtime.mjs`. Scope: medium.
