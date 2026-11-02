# Elite mobile product specification v1

Updated November 1, 2026 (America/Chicago).

Development baseline accompanying the [implementation plan](elite-mobile-development-20261101.md).
The owner has finalized the product direction and requested development readiness.
The software is not implemented by this document. Engineering defaults below
make v1 actionable; record substantive product changes as a revision.

## Outcome and delivery rules

Provide a $99/month Elite app for invited Walmart reviewers on Android and iOS.
The backend discovers matching items and selects customers; the customer's
foreground app uses a locally retained Walmart session and preferred address
to perform a zero-dollar claim and recognize the order confirmation.

- Write Android and iOS source concurrently against one versioned contract.
- Run Android/backend builds and tests throughout development.
- Defer **all iOS build/test executions until explicit owner approval of the
  demonstrated Android experience**. Write and review iOS source/test scenarios
  alongside Android; label it uncompiled and runtime-unverified until built.
- After approval, batch iOS builds, tests and necessary fixes, then signed-device
  and release validation. Android acceptance never substitutes for iOS testing.
- Launch Elite publicly only after both store downloads have passed install smoke.
- Use Codemagic Personal M2 allowance, manual-only triggers and paid billing off.
  Do not rent a dedicated Mac or start a device-cloud subscription.

## V1 user experience

### Account and setup

Sign in through verified Whop identity; bind Discord through provider-backed
identity, not a display name or a user-entered member ID. The server checks
Elite access and paid-through dates. A canceled-at-period-end member retains
remaining paid access. Subscription expiry/revocation prevents new assignments.

Connect Walmart in the app-owned WebView/WKWebView, complete customer verification
there, and select an existing Walmart shipping address. Retain its local stable
selection if available; the backend receives neither Walmart credentials nor
raw address details. If the selected address becomes unavailable or ambiguous,
pause and ask the customer to select it again. Do not substitute another address.

Settings include reconnect/disconnect Walmart, preferred address, notifications,
pause all claiming, device selection, spending limits, account deletion and sign-out.
One selected device per member is eligible to execute jobs at a time.

### Requests and competition

Pick an owner-approved product type: initial examples are Couch, Recliner,
Dishware, Pots and Pans, and Ice Cream. The owner controls the actual enabled
catalog; disabled or ambiguous types cannot trigger jobs. No custom keywords,
color, brand, shape or dimension filters in v1. Provide Suggest category.

Each request contains:

| Field | V1 rule |
| --- | --- |
| Product type | Stable enabled catalog ID |
| Minimum listed retail value | Integer USD cents; UI says "at least"; match value >= minimum |
| Successful quantity | One; stop the request after confirmed success |
| Offered success fee | Integer USD cents; zero is allowed; customer confirms any nonzero offer |
| Monthly success-fee limit | Required for paid offers; paid fees + confirmed unpaid fees + open reservations consume it, counting each job once |
| State | Active, paused, fulfilled or canceled |

One active request per member/product type; edit that request instead of making
duplicates. Unknown retail value does not satisfy a minimum. Every product must
match a specific enabled type; uncertain classification produces no claim.

Show distinct active-member request counts for a clearly named product type,
not account identities or other members' fees. Label this as category demand;
actual item-specific eligible competition can be smaller. No public leaderboard.

### Claiming Mode

Start Claiming keeps the foreground screen awake, offers a dim display, maintains
a live connection and shows session/device readiness. Stop Claiming restores
ordinary sleep behavior and makes the device unavailable for new assignments.
Notify/sound when a login, challenge or other customer action is necessary.

Screen states: Ready, Claiming, Reconnect Walmart, Connection lost, Paused,
Action needed and Confirmation pending. Push is for notification/reconnection;
the foreground connection delivers time-sensitive jobs.

### Claims and receipts

Claims history separates: confirmed claim, failed attempt, uncertain outcome,
fee pending, fee paid and payment action needed. Show the service fee separately
from Walmart's zero checkout total. Supply an order reference when available;
do not imply delivery or fulfillment has been completed.

## Allocation, submission and success contract

Filter candidates by matching request, active Elite access, ready device, permitted
budget and absence of an unresolved in-flight submission for that member.
Rank eligible offers descending; equal fees use oldest qualifying offer timestamp
then a stable request ID. Changing the offer or matching criteria resets its
qualifying timestamp; reconnecting a device does not.

Reserve monthly budget atomically before assignment. Snapshot request revision,
offered fee, item/variant, device and expiry into the job. Serialize submissions
per member across devices. Enforce lease expiry and replay protection server-side.

Use UTC calendar months for v1 and display the reset date/time to the customer.
Snapshot the budget-period ID at allocation; late confirmations stay associated
with that period. Transition reservation -> confirmed unpaid -> paid without
freeing the consumed amount or counting a job twice. Release a reservation only
after definite non-submission/failure. Include period rollover, successful payment,
failed payment and concurrent requests in cap tests.

The executing app checks the assigned item, preferred address, current Walmart
checkout total of exactly $0.00, job expiry and local execution state before
submitting. A wrong item/address, unexpected charge or required verification
stops automatic submission. Never proceed through an identity/consent gate by
pretending a human action occurred.

Success requires a definitive confirmation page/state associated with this
submission and its exact item/variant. Capture the order reference when present.
A click, cart, loading page, generic success toast or previously cached confirmation
does not establish success. Use structured page state first; redacted screenshot
evidence can supplement it. No persistent whole-screen recording is required.

Offline before dispatch: skip without a fee. A definitely unsubmitted failure:
release its reservation and consider another eligible member. A lost connection
after possible submission: hold as unknown; reconcile before any repeat for that
request. The service cannot make an external Walmart submission exactly-once by
retrying blindly. Confirmed reporting and settlement must be idempotent.

After confirmation, retire the member's one-item request and consider the next
eligible member while claiming remains available. Do not infer unit counts from
one success. Each successful recipient pays their own offered fee; losers owe
no success fee and retain their ordinary monthly subscription obligation.

## Billing rules

Store provider payment-method references only. Obtain explicit consent to the
offered fee and monthly limit. Charge the snapshotted fee after confirmed claim
acceptance; never charge for notification delivery or dispatch alone.

Use a separate payment ledger with unique confirmation/job identity. Deduplicate
webhooks and reconcile timeout/unknown provider responses before retrying.
Payment failure leaves the claim confirmed and the fee unpaid; pause further
paid assignments until resolved. Never cancel the Walmart order to collect a fee.
Do not create wallets, deposits, entry charges or pooled prize funds.

Record the commercial success event as order acceptance. Shipping performance,
arrival and later retailer cancellation are outside that event; publish clear
service terms before charging. Statutory/provider refund obligations still apply.
Claim screenshots are client-originated evidence; integrity checks mitigate
tampering but do not make them independent Walmart attestations.

## Architecture and implementation boundaries

Native Kotlin/Compose + Android WebView; native Swift/SwiftUI + WKWebView.
The private mobile project owns both clients, a Python FastAPI mobile service,
versioned JSON contracts, test fixtures and tooling. Pin actual dependency/tool
versions when scaffolding. Start with one service instance and durable SQLite
transactions/outboxes; do not introduce distributed allocation before needed.

The existing notifier continues collecting. A reviewed outbound durable adapter
exports fresh normalized observations to the new service; no history/seed replay,
no synchronous mobile-provider work inside live notifier intake, and no public
exposure of the existing loopback HTTP server. Production mobile API uses HTTPS;
local fixture endpoints are debug-only and cannot be enabled in release builds.

API boundary v1, to be frozen into machine-readable fixtures at E02:

| Boundary | Required behavior |
| --- | --- |
| Current member | Verified identity, effective Elite access and expiry; no client-selected member identity |
| Catalog/suggestions | Enabled stable types; suggestions do not become live filters |
| Requests/counts | Owner-scoped CRUD, minimum value, offer, cap, distinct-member demand count |
| Devices/readiness | Register/revoke, selected device, foreground readiness lease and reconnect |
| Job channel | Member/device/item-bound offer, acknowledgement, progress, expiry and outcome |
| Confirmations | Durable evidence association, duplicate detection and unknown reconciliation |
| Claims/payments | Separate claim and fee statuses; member-scoped receipts |
| Collector export | Authenticated service-to-service observations, deduplication and bounded retries |

All writes are authenticated and scoped; use server time for ordering/expiry.
Never accept an arbitrary remote URL as a claim instruction. Store bounded,
redacted correlation logs; omit raw addresses, credentials, cookies and payment
details. App sign-out or device revocation invalidates future assignments.

## Build/test policy and acceptance

Android/backend changes run focused local tests and relevant regression checks.
Both teams use shared happy/failure fixtures before any iOS build. iOS source
review is a development check, with runtime status explicitly pending.

G1 owner Android approval can use the running emulator, fixture-backed flows,
screenshots and a walkthrough. It approves the product experience; it does not
assert a live Walmart claim or physical-device success. Those remain required
signed-device release checks at E12.

After G1 owner approval, run combined iOS build/unit/UI validation, batch fixes,
and then signed TestFlight/release validation. Use the actual app target for the
first CI execution. No push/PR/scheduled iOS triggers, and no alternate automatic
GitHub macOS job. Every iOS run has a revision, purpose, duration and artifact
record. Default timeout: 20 minutes; adjust deliberately for a justified test.

Required scenarios: exact match, unknown value/type, wrong variant/address,
nonzero checkout, expired session, verification pause, sold out, multiple bidders,
equal offers, zero offer, offline device, duplicate job/report, process restart,
submission timeout, duplicate order reference, concurrent devices, capped budget,
membership expiry, paid-through cancellation, fee failure and account deletion.

Before release: real signed builds on both platforms prove the Walmart flow,
address/$0 guard, confirmation association, foreground keep-awake and reconnect;
actual APNs/FCM configuration is tested. Verify provider billing behavior with
supported test facilities and distinguish simulated from actual payment evidence.
Store accounts, policies, billing route, privacy/deletion and installation checks
are release work. Existing Pro/referral/scanner behavior must not regress.

## First development assignment

Prepare the new private project, freeze schemas/fixtures and create the Android
testable shell plus matching iOS source. Build Home/Requests/Competition/Claims/
Account with fixture data, then connect the shared backend incrementally.
Codemagic stays idle until Android acceptance. No live customer charges, claims,
production deployment or store publication are part of the planning turn.
