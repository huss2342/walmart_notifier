# Pagination behavior and verification research — October 23, 2026

Latest read-only investigation: live data contains a 40-slot initial batch,
701 advertised items and 18 pages. Larger batch support and the CSR request
contract remain unverified. See
[`catalogue-pagination-research-20261023.md`](catalogue-pagination-research-20261023.md).

## Current follow-up: variable pager motion

Owner explicitly requested slightly slower scrolling, variable speed/amount and
some upward motion. Implemented a short eased animation when a real next-page
control is offscreen: duration **600–900 ms**, landing at **45–60%** of viewport
height where document bounds permit, and a **30%** chance of a **6–18 px** settling
correction (smaller for short moves/edges). No per-frame random shaking, pointer
movement, tab switching, added requests or changed page deadlines. The motion
fits the existing wait only when at least one second is available.

Animation cancels on trusted wheel/touch/pointer/keyboard input, page-work
cancellation, role/context loss, challenge detection or queued navigation. Each
frame checks visibility, detached controls, reduced motion, current monitoring
scope and the one-second budget. Reduced motion uses an immediate scroll.
Horizontal targets and unavailable animation APIs retain browser scrolling. Frame errors cannot
cancel delivery or pagination. Existing settings and notifier behavior remain.

Nine focused scrolling tests pass, including five new animation regressions.
The combined extension suite passes **311/311** at the concurrently updated
2.17.2 source; syntax/manifest and scoped whitespace checks pass. Independent
read-only review approved, also running all nine focused tests. Real local
browser validation passed the actual eased movement and small upward correction
with an unchanged navigation deadline, alongside the concurrent category-fallback
and existing regression scenarios; zero console warnings/errors. Receipts:
`data/scroll-motion-red-20261023.txt`, `data/scroll-motion-focused-20261023.txt`,
`data/scroll-motion-suite-20261023.txt` and
`data/scroll-motion-fixture-20261023.png`. Snapshot before this slice:
`data/scroll-motion-before-20261023/`.

No claim is made that variation reduces HUMAN challenges; that needs live rate
measurement. This slice changes only the page-control animation, its scoped
content hooks and tests/fixture. Concurrent category-fallback edits are preserved.
The category-fallback writer has now finished its checks with this slice
preserved. Combined source is **2.17.2**; reload the existing Relay and refresh
the reviewer tab once to activate. No live input, extension reload or backend
deployment was performed by this slice. The owned local fixture tab/server are
closed after validation.

## Authorized outcome and current state

The owner authorized research and implementation of more natural pagination and
notification behavior that could reduce verification interruptions, preserving
new-item discovery speed and catalogue coverage. The owner clarified that the
unfiltered catalogue is currently buggy and letter searches normally expose the
pager. Missing pagination is not itself evidence of a bot block.

**Implemented and locally verified:** the scoped navigation changes below,
initially prepared as 2.16.2 and included in the other chat's combined
2.17.0 source. That chat owns the final manifest/version assertion.
Independent read-only review approved this slice with no required findings.
**Ready for owner activation:** the owner explicitly selected letting the other
chat finish first. It has now delivered its final 2.17.0 source and verification
receipt, with our pagination edits preserved. No live input or extension reload
was made by either chat. Its retry implementation is documented separately in
`verification-automation.md`. No claim is made that these changes eliminate
challenges.

## Findings and confidence

| Candidate | Evidence | Assessment |
| --- | --- | --- |
| Repeated full-load pagination, failed transitions and request volume | HUMAN lists volume anomalies and unusual navigation flow as indicators. The retained baseline had 43 URL fallbacks, including 36 on native-control cooldown, versus 17 site-control attempts. | Strongest actionable hypothesis, not an identified Walmart rule or proven cause. Counts are events in a bounded window, not a success-rate denominator. |
| Incorrect destination comparison | The code accepted `affinityOverride=default` as catalogue scope but did not normalize it for destination comparisons. A regression reproduced changed cards remaining locked after a successful site-control transition that added this parameter. | Confirmed implementation defect. Fix prevents this particular false stall and unnecessary reload; actual challenge benefit unmeasured. |
| Offscreen or lazily rendered controls | Existing code clicks a discovered pager without bringing it into view. Scrolling is useful for real control readiness, and can reveal lazy content. | Reasonable low-cost improvement. No evidence that decorative scrolling itself lowers challenge rates; it can load more images/ads. |
| Missing or inconsistent session state | HUMAN uses cookies and browser/network indicators. Its Precheck FAQ describes repeat interstitials when cookies are lost. | Preserve the existing signed-in profile. No evidence of cookie loss here, and Precheck guidance is not a diagnosis of Walmart's Press & Hold. |
| Tab switching or fake mouse movement | Visibility changes are observable; background timers may be throttled. HUMAN describes simulated mouse interactions and environment spoofing as detectable capabilities/indicators. | No demonstrated benefit; no tab cycling, fake pointer paths, identity changes or proxy service added. |

The source `.click()` is a programmatic DOM click (`isTrusted=false` by the DOM
standard). “Native” in existing diagnostic labels means using the site's control,
not physical human input. Scrolling does not change that distinction. We have no
evidence that Walmart used `isTrusted` as the trigger in this session.

The baseline recorded two full 19-page scans of 307.631 and 307.818 seconds. The
latest short one-page runs do not establish full catalogue coverage. The eighth
recorded verification episode at about **11:52:41 Central** followed a collector
URL fallback to page 2 of the pagerless catalogue by less than a second. A dialog
also appeared in the manual search tab. This shared-session observation does not
prove which action caused the score. Earlier retained events also include a
challenge during a scheduled page wait, so URL fallback is not established as the
only trigger. The live challenge was left untouched.

## Implemented behavior

- Normalize `affinityOverride` only when every supplied value is exactly
  `default`; preserve nondefault/mixed values and nonempty search restrictions.
  Existing changed-card and query guards still control acknowledgement.
- Bring a real offscreen next-page control into view with one smooth scroll
  during the existing scheduled wait. Respect reduced motion. Do this only on
  the visible, current, idle, unchallenged monitoring tab, with at least one
  second left and no native-control cooldown. Do not activate tabs or add waits.
- Treat real wheel/touch input as user activity even while ignoring the
  extension's own scroll event. User activity postpones navigation normally.
  A detached/replaced control or failed scroll cannot cancel the queued scan.
- Preserve the saved five-minute/15-second timing, all page visits, passive
  late-item discovery, notifier delivery, verification pauses and the concurrent
  one-round alphabet fallback. No backend or provider service was redeployed.

Files owned by this slice: `extension/page-controls.js`; the input listener,
`preparePageNavigation`, and one call in `continueSweep` in `extension/content.js`;
the version in `extension/manifest.json`; six appended runtime regressions and
minimal harness support; scroll/affinity cases in `tests/extension_fixture.html`;
the expected manifest version in `tests/test_integrated_verification.mjs`; this
record and the short README paragraph. Other modifications remain preserved.

## Verification and remaining measurement

- Before changes: 191 runtime/timing tests passed.
- Red evidence: five of the six new regressions failed against the old code.
  Green: all six pass; the independent reviewer also ran them successfully.
- Real in-app browser: the actual extension scripts passed purposeful scrolling
  without moving the navigation deadline, changed-card guards, neutral affinity,
  pagination/restarts, live discovery, scope guards, verification cancellation
  and empty-tail/temporary alphabet fallback. Zero console warnings/errors.
  Screenshot: `data/browsing-behavior-fixture-20261023.png`.
- All extension JavaScript syntax and manifest checks passed. Tracked changed
  files passed whitespace checking.
- During integration, the first combined suite's old manifest assertion and
  subsequently four newly added retry cases failed while the other implementation
  was in progress. After that implementation landed, the complete combined suite
  passed **282/282**, zero failures/skips, in 12.27 seconds. Receipt:
  `data/browsing-behavior-combined-20261023.txt`. This run preceded the final
  2.17.0 manifest bump. The other chat's subsequent stable-version receipt,
  `data/verification-retry-tests-20261023.log`, records **283/283 passing**, zero
  failures/skips, in 12.54 seconds. This is local verification, not activation.

The other chat has finished its release handoff as the owner requested. The
declared combined extension suite in `.github/workflows/ci.yml` has passed against
2.17.0. Reload Reviewer Item Relay and refresh the reviewer tab
to load the combined version. Browser tools cannot operate Chrome's internal
extension management page; no profile-file or debugger workaround was used.
Compare completed-page coverage, scan duration, challenge episodes per successful
page transitions and total verification downtime after activation. A short clear
run will not prove lasting prevention. The seven-day/300-event diagnostic mirror
limits retrospective rate estimates.

## Primary sources read

- [HUMAN bot characteristics](https://docs.humansecurity.com/applications/bot-characteristics)
- [HUMAN detection overview](https://docs.humansecurity.com/applications/bd-detection-overview)
- [HUMAN Precheck FAQ](https://docs.humansecurity.com/applications/precheck-faq)
- [WHATWG DOM: isTrusted](https://dom.spec.whatwg.org/#dom-event-istrusted)
- [MDN: isTrusted](https://developer.mozilla.org/en-US/docs/Web/API/Event/isTrusted)
- [MDN: scrollIntoView](https://developer.mozilla.org/en-US/docs/Web/API/Element/scrollIntoView)
- [MDN: lazy loading](https://developer.mozilla.org/en-US/docs/Web/Performance/Guides/Lazy_loading)
- [MDN: Page Visibility API](https://developer.mozilla.org/en-US/docs/Web/API/Page_Visibility_API)
- [Chrome background timer throttling](https://developer.chrome.com/blog/timer-throttling-in-chrome-88/)
