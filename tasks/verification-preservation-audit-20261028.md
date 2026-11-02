# Verification preservation and scan load audit — October 28, 2026

Owner reported unchanged old scanner pages after reloading2.26.1, more robot/human
prompts, and concern that their existing verification code had been removed.
This audit made no scanner/verification source or saved-settings changes.

## Verified preservation

- `verification-engine.js`, `verification-automation.js`, and
  `verification-watch.js` exactly match HEADa146712. Native controller.py,
  watchdog.py, launcher.cs and __init__.py also match that baseline.
- Other dirty verification/native/tool files already appeared in this chat's
  first Git status, before the recent search/offer changes. Their modification
  times precede October28. Existing shared observation/protocol extraction keeps
  the driver/native/helper entry points; it was not introduced by this task.
- Background imports still load the verification modules and create/trigger the
  existing automation controller. Debugger permission, optional native messaging
  and passive challenge watcher remain in the manifest.
- Before-search saved settings versus current settings differ only in keywords:
  verificationAssistEnabled remains true and verificationNativeEnabled remains
  false. Relay pacing settings match the previous production baseline.
- Fresh scanner mirror confirms active2.26.1 and enabled verification assistance.
  Its last recorded result is unsupported, attempt1of10. Enabled code does not
  establish that this particular prompt was successfully handled.

There is no complete byte-hash snapshot from the beginning of today's task.
Initial Git evidence, historical hashes and file times support preservation;
the large existing Git diff must not be described as entirely this task's work.

## Actual change and limitation

The new product-page verifier does add substantial Walmart traffic. Previously
the extension read catalogue pages. The new admission path also loads each
available candidate's individual sampling page before backend rules and dedupe.
A page containing20 uncached candidates can add20 authenticated product HTML
requests, two concurrently and up to six per report. The existing page delay does
not pace those product requests. Caches reduce repeat requests but reset with a
service-worker recreation; the blocked-request circuit survives in session storage.
The automated tests did not measure sustained live challenge frequency.

The18 earlier challenge episodes include the repaired false-attribution loop.
They cannot measure the number of genuine new browser challenges. More traffic
is a plausible contributor, not a proven sole cause of the owner's current prompts.

## Current tabs and pause

- Signed-in Chrome homepage tab1530560734 is still on catalogue page11, with
  Recognized Reviewer heading,767 product links and no visible robot/human text
  or challenge frame at inspection.
- Department tab1530560752 is on Health and Medicine page14. Its accessibility
  tree contains a real visible dialog titled Robot or human?, with the instruction
  to activate and hold the button. No verification input was performed.
- Saved shared pause is the earlier count18 episode. Primary is unassigned and
  both auxiliary lanes are paused, with session ownership lost after reload.
  Tab creation/repair is intentionally blocked while verification is unresolved.
- Existing content scripts may be invalidated by an unpacked reload. Recovery
  needs fresh evidence from the affected documents. A read-only harness reproduces
  no recreation while paused/unreachable, then recovery on a fresh clean probe
  from the same tab. A real visible challenge must not be cleared artificially.
- Cleanup ownership is stored in Chrome session storage and disappears on
  extension reload. The new cleanup conservatively preserves tabs when it loses
  proof. Exact URL matching also preserves an old scanner if it paginated after
  its last ownership snapshot. These are limitations in the promised cleanup,
  separate from the current pause blocking new tabs.

## Accepted restoration

Recommended temporary restoration: remove only the extra individual-product
loads while preserving catalogue variant/OOS guards, keyword routing, tab safety,
and the existing verification helper. The owner asked what that proposal meant,
then explicitly replied 'yes restore' and specified that the card's own Free/OOS
labels suffice. Source2.26.2 now implements that instruction: the added loader,
caches/cooldown and partial product admission are removed. Original message
deadlines/retry timing are restored. New card-only regressions pass, with all767
remaining/current JS tests passing and an independent review approved. Tests for
the withdrawn loader were removed with it; no required checks were skipped.
All14 audited verification/native/tool hashes remain unchanged. Saved settings
and backend source remain intact. One owner reload/tab refresh remains to activate.

Read-only audits used three independent agents. Verification files were not
operated or modified, and no CAPTCHA interaction occurred. A local browser fixture
was run for restoration verification; its tab/server are closed during closeout.
Claimed user scanner tabs remain open.
