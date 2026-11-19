# Four scanner tabs: November 5, 2026

Owner reports three grouped scanners, a missing fourth member, and extra Walmart
tabs. Authorized scope is four scanners: home, departments, keywords, priority.

## Cause and fix

Live 2.26.15 status and Chrome inventory showed three auxiliary tabs in window
1530561601, with the home scanner and abandoned home tabs in window 1530561988.
Diagnostic event 678675 records creation of home tab 1530562219; another home tab
1530562220 subsequently became primary while both older tabs remained open.

A reproducible coordinator failure revokes a temporarily unresponsive home
reader, deletes its per-tab checkpoint and forgets its workspace identity. During
temporary category fallback, subsequent pagination no longer matches the exact
resume URL, so auto-open treats the existing home reader as a manual search and
creates another tab. Losing the identity also excludes it from grouping.

2.26.16 retains the existing interrupted-tab identity and checkpoint while
revoking scan authority. Scoped fallback pagination remains recognized only for
that registered tab; reconnecting restores progress under a fresh generation.
Manual filters remain unauthorized. Resume navigation cannot commandeer the
reserved reader or an auxiliary tab. New tabs explicitly use the existing scanner
window instead of the current browsing window; existing tabs are not moved.

## Verification and activation

Four regression cases cover temporary reader loss across fallback pagination,
all four workspace registrations, scanner-window creation/replacement, and
manual-search/closed-tab behavior. The primary reproduction and window-selection
test failed before their fixes. Run `node --test tests/test_*.mjs`; local captured
output is `data/tab-fix-js-tests.txt`: **1,090 passed, zero failures/skips**.
JavaScript syntax and diff whitespace checks
pass. Self-review covers ownership, unchanged pause and scope guards, fresh
generation authority, checkpoint retention, and bounded window selection.

Activation confirmed from fresh local scanner status: **2.26.16**, primary
1530562220, departments 1530562225, keywords 1530562226, priority 1530562224.
The primary is reachable; the first auxiliary status still reflects startup.
Browser control became unavailable during verification: the selected Chrome
entry point reports "Local browser use is disabled in Work settings". No policy
workaround was used. Exact open-tab count, grouping and cleanup remain unverified.
When browser control is enabled, reconcile these four active tab IDs against a
fresh scanner status and Chrome inventory, close confirmed abandoned scanner
tabs and consolidate the requested four into one group/window if needed. Do not
close an active scanner or unrelated browsing tabs. Existing unrelated
working-tree changes were preserved; no
backend change, restart, commit, push or synthetic notification was performed.
