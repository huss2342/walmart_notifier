# Content-script invalidation after extension reload — October 23, 2026

Owner pasted `Uncaught Error: Extension context invalidated` from content.js on
a reviewer page. The attachment also contains earlier fetch/HTTP 503 intake
errors. Scope: fix stale content-script lifecycle without changing delivery,
pagination, verification input, budgets, or durable progress.

Root cause: `chrome.runtime.sendMessage(...).catch(...)` cannot catch Chrome
throwing before a Promise is returned. Old DOM listeners, mutation observers,
role polling and navigation timers can outlive an extension reload. Similar
unguarded storage calls and late acknowledgements can continue the old run.

Implemented:

- Guard runtime.id and route content messaging/storage through one boundary
  handling synchronous and asynchronous context invalidation. Other failures
  still propagate to existing retry/error handling.
- Deactivate the old role; disconnect both observers; remove input/message
  listeners; clear relay, live observation, verification, role retry/poll,
  native navigation and pager reveal timers. Cancelled pager waits resolve so
  callers can unwind without another scroll/navigation.
- Recheck the live context after awaits and immediately before navigation or
  clicking a native control after diagnostics. Late successful acknowledgements
  cannot advance saved sweep progress after invalidation. A page starting with
  no runtime context does not install observers or timers.
- The affected already-invalid document still needs one refresh; dead extension
  contexts cannot reconnect themselves. No automatic page reload was added.

Nine lifecycle regressions cover thrown/rejected invalidation, missing runtime
id at startup/during navigation, storage and in-flight acknowledgements, pager
wait cancellation, native/full-load diagnostic races, ordinary worker retries
and ordinary storage errors. Red failures were observed before the fixes.
The live-backoff regression now waits for its actual delivery-start boundary
instead of assuming five Promise microtasks; assertions and retry timing remain.

Verification: all 221 content/runtime, collection/Discord and timing tests pass.
Receipt: ignored `data/context-invalidation-content-tests-20261023.log`.
The subsequent full combined extension command also passes **299/299**, zero
failures/skips, including the concurrent verification-owner tests then present.
Receipt: `data/context-invalidation-tests-20261023.log`. The manifest was still
2.17.0 in that run; the other chat owns the final 2.17.1 version/assertion check.
JavaScript syntax and scoped tracked/untracked whitespace checks pass.
The foreground in-app browser fixture passes with no console warnings/errors,
including real DOM mutations after simulated invalidation and zero further
API calls/navigation. Screenshot: `data/context-invalidation-fixture-20261023.png`.
The first background Chrome run failed an unrelated visible-scroll precondition;
the visible in-app run met that precondition without changing production code or
weakening the scrolling test. Owned fixture tab/server are closed at handoff.

Read-only live health returned HTTP 200/status ok, with scanning waiting for
verification. This is not proof that a new ingest transaction would succeed.
No test items, backend changes, live verification input, extension reload,
commit or push were performed. The HTTP 503 path remains retryable and separate.

Self-review found and fixed pending pager wait and synchronous navigation-race
cleanup gaps. The other owner-authorized chat is separately repairing verifier
input diagnostics and owns combined version **2.17.1**, its manifest assertion
and final release receipt. It agreed these lifecycle edits are disjoint from
its verification modules and `verificationInputErrorFixture` Options block.
Preserve both sets of work. The lifecycle patch is implemented and locally
verified; combined activation follows that chat's stable release check.

## Follow-up error paste, 2026-10-23 12:37 Central

The new attachment `55de2f4c-f937-46ec-93b5-a246f3d5ea20` repeats the
`content.js:60` stack and page-7 context, while its displayed source already
contains the lifecycle guards. Current line 60 is a variable declaration,
not an extension API call. This supports a retained historical error entry;
the paste alone does not establish a fresh failure in the guarded code.

Read-only diagnostics now confirm active version 2.17.1, verification pause
cleared, a successful 43-item page delivery, and a page-2 `unusable` transition
to the character sweep. Health returned HTTP 200/status ok, browser reachable
and sweeping. Historical fetch/503 entries remain separate; the successful
delivery demonstrates a more recent working intake transaction.

No additional production edits or repeated tests were warranted by this
evidence. Clear the extension Errors list and refresh the affected Walmart
document once to distinguish a newly emitted error from the retained entry.
The browser policy prevents this chat from accessing Chrome extension pages;
no alternate browser transport was used.
