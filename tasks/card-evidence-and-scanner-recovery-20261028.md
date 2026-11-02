# Card evidence and scanner recovery — October 28, 2026

The owner authorized stronger logging and investigation of a missing keyword
scanner after activating2.26.2. Preserve the accepted card-only checks, pacing,
search settings and existing verification helper. Do not add product-page loads.

## Live diagnosis and repair

Initial fresh status showed2.26.2, count18 saved verification pause, no primary
lease and both auxiliary lanes paused/unowned. Chrome had the main page11 and
Health and Medicine page14; the saved keyword tab no longer existed. Both pages
had no challenge dialog, marker or challenge text. A pause prevents auxiliary
reservation, so the missing keyword tab was a consequence of that pause.

Refreshed both existing reviewer documents once. The existing coordinator then
cleared the pause, reclaimed main0734 and reserved department0764/keyword0765.
Confirmed progression in the browser and mirrored runtime: main passed page11
and completed/restarted its catalogue sweep; departments advanced Health and
Medicine to Office Supplies; keywords advanced Kitchen Appliance, Ride On,
Smart Ring and Tablet. Closed old department0752 only after its replacement was
owned and active. The three live scanner tabs remain open.

The main tab is the broad catalogue. Reload recovery intentionally preserves its
saved page. Walmart's current document initially held230 valuation lines and767
product links; many links are duplicates or variant swatches. These counts are
page contents, not a count of independent eligible offers. No product-page
requests, verification input, settings mutation or scanner policy changes were
used for recovery.

## Logging questions and implementation

- What evidence admitted/rejected a card? Actual relay/live extraction emits
  document/page/generation/lane context, accepted/rejected/variant/OOS/missing-value
  counts and at most12 prioritized samples. Each sample has a public item ID,
  fixed decision, card-found flag, nullable Free/OOS facts and nullable retail
  cents. Missing-card evidence stays unknown. Exact repeated payloads are silent.
- Which auxiliary lane failed? Existing auxiliary start/delivery/skip/error events
  now pass the diagnostic allowlist. Errors become fixed classifier codes.
- Will evidence survive the next status update? The backend appends only fresh
  validated events to a dedicated rotating JSONL archive before replacing the
  latest snapshot. The archive targets60days within a16MiB space cap, rotating
  at2MiB or a day boundary. Restarts and unchanged heartbeats avoid duplicates.

The seven-day/300-event extension ring remains bounded. Snapshot request/storage
bound is1MiB to fit card samples. There is no raw page text, title, query, URL,
HTML, quota, account data or credential logging. Samples are diagnostic examples;
this is not a complete card DOM history. No external diagnostic service is used.

Logging failures cannot change eligibility or stop the scan. Failed archive
appends prevent mirror acknowledgement and retry later. Short/zero-progress writes
restore the original log length under the existing locks; a failed truncation
blocks subsequent process-local writers until repaired. Crash/power-loss exactly
once guarantees are not claimed.

## Verification and activation

- 776 JS tests pass; final output data/qa-card-evidence-all-js-verified-20261028.txt.
- 571 server/diagnostic/storage/control tests pass. After archive review repair,
  250 rolling-log/diagnostic tests pass, including partial writes, failed rollback,
  retry framing, empty migration, restart dedupe, privacy and schema bounds.
- Real local browser regression fixture passes with no console warnings/errors;
  its owned tab/server are closed. Actual JS sanitizer output passes the Python
  validator without data loss. Ruff, protocol and scoped whitespace checks pass.
- Independent review approved the final implementation. Verification engine,
  automation and watch hashes match their pre-task baselines.

Backend activated10:42CDT on imagecbba8caf18a8, overlaying only server.py,
browser_diagnostics.py and rolling_logs.py on prior running image398418cabf1a.
Source hashes match deployed hashes, health isok, and naturally mirrored events
are parseable in data/browser-diagnostic-events.jsonl. Runtime configuration and
saved scanner/relay settings match the fresh baseline. A first activation check
compared environment list order too strictly; the healthy rollback was retained,
its temporary Compose name repaired, and the final activation compared values.
No unrelated dirty source was shipped. Rollback tag:
reviewer-notifier:before-card-evidence-20261028.

Chrome still runs2.26.2 with all three scans working. Source2.26.3 requires one
extension reload and refresh of reviewer tabs to enable the new card/auxiliary
logging. The backend archive is already active. Activation receipts and baseline
comparisons are ignored under data/card-evidence-20261028.
