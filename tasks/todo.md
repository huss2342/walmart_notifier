# Discord alerts execution checklist

## Referral discounts revision: October 30, 2026

Plan: [plan.md](plan.md). This replaces the prior cash-affiliate RA checklist.
Accepted terms: **30% off the friend's first monthly payment and 30% off the
referrer's next monthly payment. No cash commissions.** Core implementation is
now deployed, with native coupons/checkout and real private-button verification.
The [rollout record](referral-automation-20261030.md) distinguishes the completed
implementation from actual invoice/lifecycle events still awaiting paid members.
The acceptance checklist and existing history remain below the separator.

### RA1: Prove the provider's required billing and access behavior

**Description:** Resolve the billing integration's highest-risk assumptions
before building or advertising automated referral discounts.

**Acceptance criteria:**
- [ ] Confirm ordinary buyer Discord mapping/lifecycle in the selected app;
      preserve tester grants, current Pro pricing and zero cash-affiliate rates.
- [ ] Verify friend first-payment promo, existing-member next-invoice promo,
      eligible account/API access and authoritative invoice/promo identity data.
- [ ] Demonstrate the supported way to apply a later reward after a one-payment
      promo is consumed; document existing-promo conflicts and test facilities.

**Verification:** Official versioned SDK/API review, read-back of saved settings
and supported test invoices. Distinguish configuration proof from charged-invoice
proof; do not submit unapproved payments or refunds.
**Dependencies:** None. Core amount and one-payment duration already selected.
**Surfaces/files:** Whop promo and Discord app settings; private evidence and
`discord_bridge/whop-products.json` if recording verified metadata.
**Scope:** Small, provider feasibility and existing configuration.

### RA2: Deliver personal links and the friend's discounted checkout

**Description:** Members obtain their own referral link/code without asking
for a manual coupon or Discord promotion.

**Acceptance criteria:**
- [ ] Bind a referrer to verified provider identity and persist a unique code;
      do not trust a display name, arbitrary account ID or unsigned URL claim.
- [ ] The link reaches the active Pro checkout with a visible $21 first payment
      and $30 renewal; verify provider preapplication or give a clear code step.
- [ ] Codes have one-payment duration and no cash affiliate. Keep API secrets
      server-side; provide a private, authenticated self-service entry.

**Verification:** Test a non-owner flow, identity isolation and code attribution;
read checkout amounts without charging. Focused contract tests use sanitized
provider fixtures. Confirm $99 arithmetic with a disabled fixture only.
**Dependencies:** RA1.
**Files/surfaces:** Proposed `discord_bridge/referrals.py`,
`discord_bridge/whop_client.py`, referral storage and focused tests; approved
self-service surface selected from the verified identity path.
**Scope:** Medium, one complete link-to-checkout path.

### Checkpoint RA-A: Safe customer entry

- [ ] One-payment provider semantics and repeat application path are evidenced.
- [ ] Non-owner can obtain a personal link and see the correct checkout amounts.
- [ ] Cash rates stay zero; no current role, price or retired offer was changed.

### RA3: Record qualifying referrals exactly once

**Description:** Detect a referred customer's successful first paid subscription
using authenticated provider data on the existing backend.

**Acceptance criteria:**
- [ ] Poll with pagination, restart-safe cursors/overlap and reconciliation, or
      use verified signed webhooks if secured ingress is actually available.
- [ ] Persist referral/customer/payment ownership and one pending reward;
      duplicate events, renewal, upgrade and retried payment create no extras.
- [ ] A Discord join, unpaid checkout or manual trial is not mistaken for a
      successful purchase. Isolate another business's events and identities.

**Verification:** Focused fixture-based tests for duplicates, out-of-order data,
crash/restart, missing pages and provider timeouts; run a sanitized integration
read. Do not fabricate live conversions or trust query parameters as payment.
**Dependencies:** RA2.
**Files:** Proposed `referral_worker.py`, `referral_store.py`, Whop adapter and
focused tests under `discord_bridge/`. Use actual local conventions at build time.
**Scope:** Medium, durable attribution slice.

### RA4: Apply and reconcile the referrer's reward

**Description:** A qualifying purchase produces a one-payment 30% discount on
the referrer's next eligible bill without owner bookkeeping.

**Acceptance criteria:**
- [ ] Apply via the supported membership promo API and persist intended reward,
      membership/invoice and provider result before claiming it was granted.
- [ ] Reconcile ambiguous timeouts and concurrent workers without duplicated
      promos; record used only after the correct discounted invoice succeeds.
- [ ] Pro pays $21 then returns to $30. Percentage calculation also gives $69.30
      for a disabled $99 test tier, never a fixed $9 or cash balance.

**Verification:** Focused state-transition and retry tests plus supported test
invoice evidence. Read back Whop's membership/invoice result; a sent API call or
zero-dollar test does not verify the billed discount.
**Dependencies:** RA3.
**Files:** Referral worker/store, Whop adapter and focused tests, 3-5 files.
**Scope:** Medium, reward-to-invoice slice.

### Checkpoint RA-B: Two-sided discounts work

- [ ] Friend's first payment and referrer's next payment are each discounted
      once; following monthly payments return to normal.
- [ ] Paid buyer receives Discord role through Whop; referral code never grants
      paid access or creates an owner-managed Discord affiliate role.
- [ ] Duplicate delivery and service restart do not duplicate a reward.

### RA5: Recover exceptions and show private reward status

**Description:** Explain what happened to a referral and retain valid pending
rewards through billing conflicts instead of requiring routine manual fixes.

**Acceptance criteria:**
- [ ] Show referral link, pending/applied/used counts and actual next-bill state;
      explain unlinked/no-paid-membership/existing-promo cases plainly.
- [ ] Implement accepted multi-referral policy and invoice-failure recovery;
      preserve pending rewards without overwriting unrelated promotions.
- [ ] Reconcile refund/dispute events with an agreed policy; no automatic
      retroactive charge, new subscription, cash debt or tester role revocation.

**Verification:** Tests for another active promo, no subscription, unpaid invoice,
cancellation, multiple referrals and refund before/after use. Member can inspect
only their own status. Confirm terms before exposing enrollment publicly.
**Dependencies:** RA4; agree successive-month queue and refund interaction.
**Files:** Referral worker/store, authenticated status surface and focused tests.
**Scope:** Medium, billing exception/status slice. No unsolicited Discord DMs.

### RA6: Prepare shared feature access for a future tier

**Description:** Shared Pro capabilities can accept a separately configured
higher-tier role, independent of its eventual public name or $99 price.

**Acceptance criteria:**
- [ ] Add stable tier/capability configuration with future entry disabled until
      real product/plan/role IDs and benefits exist; preserve existing ledgers.
- [ ] Request commands and request-DM delivery accept Pro OR an enabled higher
      role. Permission generation gives that role only its configured channels.
- [ ] Current Pro retains its speed/features. Disabled/missing future IDs do not
      break startup, widen Free access or trigger welcome-message replay.

**Verification:** Focused access tests for Free, Pro, future-only and both-role
members; failed member lookup, disabled tier, restart and ledger compatibility.
Run existing bridge permission and request suites appropriate to these changes.
**Dependencies:** RA1; independent of future public tier name.
**Files:** Split into two small changes: capability helper plus
`request_commands.py`/`request_delivery.py`, then `layout.py`/`provision.py`/
`membership.py` with their focused tests. Keep each change reviewable.
**Scope:** Two medium slices; no live future product or role created here.

### Checkpoint RA-C: Recovery and tier access remain correct

- [ ] Earned rewards survive supported failures and users can understand status.
- [ ] Pro and future-only test identities get shared access; Free remains gated.
- [ ] Existing tester promises and membership ledger state remain intact.

### RA7: Verify lifecycle and future-tier transitions

**Description:** Prove routine Discord billing access and document the exact
provider transition needed before the eventual $99 launch.

**Acceptance criteria:**
- [ ] Renewal, cancel-at-period-end, actual expiry and failed-payment recovery
      retain/remove only the access supported by valid memberships.
- [ ] Supported upgrade/downgrade tests avoid unintended dual subscriptions;
      shared access survives expiry of one tier while another remains valid.
- [ ] Pending/used referral rewards survive the supported tier transition;
      prorated adjustments do not consume a full-month reward accidentally.

**Verification:** Use supported provider test facilities for invoices/transitions
and a permission matrix for both roles. Record unprovable live events as open,
not passed. Do not charge, refund or remove real memberships for test coverage.
**Dependencies:** RA5, RA6. Live $99 launch waits for actual product definition.
**Files/surfaces:** Whop integration fixtures/tests, entitlement tests, private
provider evidence and `discord_bridge/whop-products.json` verified metadata.
**Scope:** Medium; transition verification rather than new premium features.

### RA8: Publish clear instructions and operating handoff

Copy-only progress on October 30: the owner authorized multiple placements.
Coming-soon copy is verified in existing Discord cards/channel descriptions and
Whop. See [placement evidence](referral-offer-placements-20261030.md). This does
not complete RA8's live links, billing evidence or operating automation criteria.

**Description:** Expose the verified program through existing approved surfaces
with ordinary routine administration handled by the worker and Whop.

**Acceptance criteria:**
- [ ] Explain friend first-month and referrer next-month 30% discounts, normal
      renewals and accepted repeat-referral policy. No cash/earnings wording.
- [ ] Verified links/status are self-service; if editing a Discord guide is
      authorized, edit existing text only. No new announcements or outreach.
- [ ] Document status/retry inspection, reconciliation, backup/restore and safe
      disable behavior. Disabling enrollment must retain already-earned rewards.

**Verification:** Follow instructions as a non-owner, compare saved coupons,
checkout and next-invoice evidence, and review the final plan/checklist. Run
focused tests once for final changes; preserve private billing evidence locally.
**Dependencies:** RA7; implementation authorization before live configuration.
**Files/surfaces:** Whop instructions, existing guide if authorized,
`discord_bridge/README.md`, plan/todo and worker operating notes.
**Scope:** Small. No paid hosting or third-party affiliate subscription planned.

### Checkpoint RA-D: Automation accepted

- [ ] Two-sided one-payment discounts and routine paid access are demonstrated.
- [ ] Repeat referrals, conflicts and upgrade boundaries have evidence and clear
      terms; remaining provider exceptions are honestly recorded.
- [ ] Owner has reviewed the concrete implementation before activation. Current
      task approved planning only; no cash commission or $99 launch is inferred.

---

October 28 implementation: Free and $30/month Pro with unlimited signups are
configured. Starter and old $20 checkout options are hidden and out of stock.
The owner rejected member caps, waitlists and added restrictions. Earlier FP
capacity, complimentary setup and admission gates below are superseded, not an
authorized backlog. Current scope, verification and limitations:
[free-pro-launch-20261028.md](free-pro-launch-20261028.md).

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

---

# Free plus $30 Pro launch revision (October 28, planning only)

## Task FP1: Snapshot account state and promised access

Description: establish the actual products, purchase state, Discord bindings and
outstanding free-month promises before retiring any option. Owner reports no
members; do not treat that as a provider read-back.

Acceptance criteria:
- [ ] Existing products/pricing/checkout links and Discord bindings are recorded
      privately; paid, trial and pending members are checked.
- [ ] Existing free-month offers are listed, and capacity is reserved for them.
- [ ] Proposed ten-seat initial cohort and no-auto-charge complimentary terms
      are recorded, with any previously quoted price commitments identified.

Verification: compare provider pages and a current Discord access read; no
mutation or broad personal-data export. Dependencies: none.
Files likely touched: `tasks/free-pro-launch-20261028.md`, `tasks/todo.md`;
private operational records stay outside tracked source. Scope: small.

## Task FP2: Prepare the Whop offer and complimentary access

Description: prepare one $30 Pro offer plus a private free 30-day pricing option
linked to the existing Pro experience, and close legacy Starter/$20 sales.

Acceptance criteria:
- [ ] Pro is $30 USD/month with zero initial fee; retired direct checkout URLs
      cannot sell Starter or an unintended legacy $20 offer.
- [ ] Complimentary Pro is free, lasts 30 days, has no paid renewal/card
      requirement, and has controlled issuance rather than a public free link.
- [ ] Paid activation, free expiry, cancellation and overlapping paid/free
      entitlement have a successful provider-level test before admissions open.

Verification: preview as a non-owner; use dedicated short-lived test access;
record real Discord role behavior. Verify old URLs without purchasing. Do not
perform a real charge without authorization. If native overlap fails, document
the manual complimentary fallback and check paid entitlement before revocation.
Dependencies: FP1. External surface: Whop only.
Files likely touched: `discord_bridge/whop-products.json`,
`tasks/free-pro-launch-20261028.md`, `tasks/todo.md`. Scope: medium.

## Checkpoint FP-A: Offer and lifecycle supported

- [ ] Native free access and paid/free overlap behavior are proven, or the
      bounded manual fallback is chosen and documented.
- [ ] Checkout settings and seat reservations match the proposed offer.
- [ ] No new member is promised automatic billing or unverified functionality.

## Task FP3: Retire Starter routing and layout

Description: add a supported inactive Starter configuration and canonical $30
Pro price without disturbing durable Free/Pro jobs or Pro's low-value feeds.

Acceptance criteria:
- [ ] Active offer definitions show Free and $30 Pro; no new Starter jobs are
      created and existing Starter destinations are safely retired.
- [ ] Free remains delayed 86400 seconds; Pro has no added delay and retains
      category/value/personal-request benefits.
- [ ] Existing observations, dedupe and Free/Pro outbox work survive migration
      and restart without replay or queue reset.

Verification: focused route/migration/restart tests using temporary SQLite state,
including low/unknown/high values and pending Starter/Free/Pro work.
Dependencies: FP1; FP2's exact role/experience IDs inform the migration.
Files likely touched: `discord_bridge/policy.py`, `discord_bridge/layout.py`,
`discord_bridge/core.py`, `tests/test_discord_bridge_runtime.py`,
`tests/test_discord_bridge_free.py`. Scope: medium.

## Task FP4: Retire Starter permissions and membership handling

Description: reconcile the managed server layout and membership audit with a
retired Starter tier before hiding its resources from members.

Acceptance criteria:
- [ ] Free and Pro cannot browse retired Starter channels; Free cannot browse
      Pro or private operations. Existing retired resources are preserved
      privately where needed rather than deleted blindly.
- [ ] Provisioning/auditing accepts the supported retired state, membership
      events handle Pro and compliments correctly, and role counts do not
      claim that complimentary users are paying subscribers.
- [ ] Pro role loss stops personal requests; a valid paid entitlement preserves
      Pro after complimentary expiry. No new bot permissions are introduced.

Verification: focused provisioning/membership tests, free and Pro permission
views, personal-request access regression and the live guild audit.
Dependencies: FP2, FP3.
Files likely touched: `discord_bridge/provision.py`,
`discord_bridge/membership.py`, `tests/test_discord_bridge_provision.py`,
`tests/test_discord_membership.py`, `tests/test_discord_request_runtime.py`.
Scope: medium. Split a further code slice if inspection shows more than five
files are required; do not omit necessary checks to meet a file-count target.

## Checkpoint FP-B: Access and delivery remain correct

- [ ] Relevant routing, membership, permission and request checks pass.
- [ ] Deployment health and live audit pass; natural alerts continue without
      resetting collection/dedupe or changing scanner behavior.
- [ ] Paid admissions remain closed until published content is consistent.

## Task FP5: Publish consistent pricing and member instructions

Description: align all actual member-facing pricing, benefits and checkout
buttons with the verified two-tier offer.

Acceptance criteria:
- [ ] Welcome/payment card, channel topics, how-to-use text and checkout buttons
      show Free versus $30 Pro, with no customer-visible Starter/$5/$20 offer.
- [ ] Free is described as historical; Pro promises alerts after detection,
      not complete inventory, guaranteed claims or publication-time delivery.
- [ ] Complimentary access clearly explains expiry, optional $30 continuation,
      no automatic charge and how to get help. Support/cancellation/refund text
      matches the actual Whop settings and terms.

Verification: inspect the actual storefront, checkout and Discord messages from
an ordinary mobile/member view; verify buttons and old links. Templates alone
do not prove the live copy changed.
Dependencies: FP2, FP3, FP4.
Files likely touched: `tasks/discord-onboarding-draft.md`,
`discord_bridge/README.md`, `README.md`, `tasks/discord-updates-templates.md`,
`tasks/discord-capacity-plan.md`. External surfaces: existing Whop/Discord copy.
Scope: medium. Rendered live cards must use the same canonical price as FP3.

## Task FP6: Verify the integrated launch and open the first cohort

Description: complete the new offer's access lifecycle checks, then activate
existing promised testers within reserved capacity.

Acceptance criteria:
- [ ] Non-owner Free, complimentary Pro, paid Pro, expiry/cancellation and
      complimentary-to-paid overlap pass; legacy URLs cannot bypass the offer.
- [ ] The active/complimentary/reserved ledger prevents overselling, and ordinary
      members have a simple mobile setup path with voluntary notifications.
- [ ] Each tester's real start/expiry is recorded; no automatic charge or
      scheduled outreach is created without the corresponding authorization.

Verification: run the full relevant bridge regression once after integration;
record provider lifecycle and live guild audit/health evidence, distinguishing
sandbox checks from actual Discord behavior. Dependencies: FP5 and FP-B.
Files likely touched: `tasks/free-pro-launch-20261028.md`, `tasks/ACTIVE.md`,
`tasks/todo.md`; private ledger/evidence are not committed. Scope: medium.

## Checkpoint FP-C: Ready for controlled admissions

- [ ] Only Free and $30 Pro are publicly offered.
- [ ] Previously promised complimentary months are honored and expiry is known.
- [ ] No broad ads, new referral rewards or expanded cohort until capacity is
      available and the owner decides the claim/revenue tradeoff is acceptable.

## Task FP7: Review cohort results before expansion

Description: evaluate usefulness, owner competition and actual voluntary paid
continuation rather than assuming a good screenshot establishes demand.

Acceptance criteria:
- [ ] Around two weeks, review voluntary useful-alert/claim reports, notification
      noise, service reliability, support effort and the owner's own experience.
- [ ] After free months end, record optional $30 conversions separately from
      complimentary access and later paid renewals.
- [ ] Document whether to keep, reduce or cautiously expand the initial cohort;
      referrals and ad spending require available seats and an explicit next step.

Verification: compare private access/payment records and volunteered feedback;
do not treat listed retail value as savings or role counts as revenue.
Dependencies: FP6 and actual elapsed cohort time.
Files likely touched: `tasks/free-pro-launch-20261028.md`,
`tasks/discord-capacity-plan.md`, `tasks/todo.md`. Scope: medium.
