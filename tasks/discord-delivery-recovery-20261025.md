# Discord delivery and pager recovery — October 25, 2026

Evening follow-up: a separate neutral-overwrite audit false positive was found
and repaired; see the current
[`evening recovery receipt`](discord-neutral-permission-recovery-20261025.md).
The scrolling repair below remains active.

Owner authorized checking and fixing Discord delivery and all issues found in
the preceding status check, including scrolling/navigation.

## Current state

- Discord delivery is live and verified. The required Pro overwrite on
  `#item-requests` was restored by the concurrent Discord-request repair chat;
  the full privacy audit passes. Existing audit rules were retained.
- Pro and Starter backlogs cleared. The delayed Free recap backlog is draining
  under Discord's existing rate limits. No failed jobs or blind replay added.
- The concurrent request-bot repair was deployed; live health confirms connected
  Gateway, ready request runtime and a running DM sender.
- Relay **2.19.1** is implemented, independently reviewed, tested and live.
  The owner reloaded the extension and refreshed Walmart. A fresh 7:36:57 AM
  Central report confirms 2.19.1; pages 4–7 delivered, three native transitions
  completed in 1.9–2.4 seconds, and no fallback or false manual-input interruption
  occurred in the new session. Two newly found items were confirmed in the
  actual Pro message at 7:37:04 AM. No activation step remains for this repair.

## Findings and repair

At 7:06 AM Central, scanning was completing normally but the privacy audit was
rejecting `#item-requests` permission drift. The last Pro send was October 24 at
7:11:54 PM; roughly twelve hours of Free history had accumulated. Pro messages
resumed at 7:13 AM after the permission repair. A GET of the actual Discord
message confirmed bot-authored product embeds, independently of the local ledger:
[recovered Pro message](https://discord.com/channels/1562530389866717295/1562537637787603055/1563888178534813751).

The scrolling worker reproduced two fallback defects and one scroll interruption:

- All numbered controls shared one persisted cooldown. Keys now include the
  destination page; legacy broad keys no longer suppress unrelated pages.
- An expired failure shortened the next startup allowance to one second, below
  observed successful native transitions. Retries retain the normal five seconds.
- Site-driven scroll restoration during pending native navigation could look
  like manual input and release the stale-card guard. Scroll-only events during
  that transition retain the guard; direct trusted input still yields.

## Verification

- Four focused regressions failed against the prior scripts; five focused cases
  pass after repair, including real-input yielding and stale-card protection.
- All **348 extension tests** pass after the integrated 2.19.1 version bump.
- Relevant Discord provisioning, request setup, collector, burst delivery and
  priority suites pass. Repository lint, changed-script syntax, fixture script
  syntax, manifest parsing and scoped whitespace checks pass.
- The visible in-app Chromium fixture passes real eased scrolling, bounded
  upward correction, unchanged navigation deadlines, delayed card rendering with
  a site-generated scroll event, scope/verification guards and extension cleanup.
  Running that same fixture with the saved pre-fix scripts reproduces
  `waiting-user` immediately after the site's scroll restoration.
- Independent read-only review approved production changes and the browser
  regression fixture, with no required findings.
- Actual Discord health confirms zero due Pro/Starter jobs and no failed jobs;
  rate-limited Free backlog is retained and draining. Positive personal-request
  DM delivery is not claimed: there are no real watches in its outbox.
- User-confirmed reload and fresh runtime diagnostics establish live activation.
  `activation-verification.json` records the native transitions, discovered
  products and actual [post-reload Pro message](https://discord.com/channels/1562530389866717295/1562537637787603055/1563894164490428512).

Evidence and before snapshots: `data/incident-20261025/`. This includes
`scrolling-red.txt`, `integrated-extension-suite.txt`, `scrolling-browser-pass.txt`,
`scrolling-browser-pass.png`, `pro-message-verified.json` and
`discord-recovered-health.json`, plus the browser baseline failure receipt.
Earlier browser attempts exposed fixture-only
declaration/timer issues, which were corrected before the passing run; a hidden
Chrome tab correctly did not perform visibility-dependent animation.

No CAPTCHA input, privilege expansion, history replay, commit or push was
performed by this chat. Concurrent Discord-request edits and other dirty work
were preserved. Source changes belong to `extension/content.js`,
`extension/page-controls.js`, `extension/manifest.json`,
`tests/test_extension_runtime.mjs`, `tests/test_integrated_verification.mjs`,
`tests/extension_fixture.html`, and the scoped README/handoff text.

Owned local fixture tabs and loopback servers on ports 8879/8880 were closed
after validation. The owner's reviewer tab and production notifier remain up.
