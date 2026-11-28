# Coverage loop (every 2 days until 98%+)

Started by the Windows task "ReviewerCoverageLoop" (start-chat.ps1). Goal set by the
owner on 2026-11-28: **98%+ of new $80+ items found within 60 s**, held for
**3 runs in a row**. Then delete the task (`schtasks /delete /tn ReviewerCoverageLoop /f`)
and say so.

## Each run

1. Measure. Run `python data/opt-20261126/remeasure.py`, or delay_bounds.py
   directly with `--hours` set to the time since the last extension reload. Read
   tasks/ACTIVE.md "Search optimisation" first for how the tools work.
   - Leave out items whose bound spans an alert-server outage: no page reads at all
     for 5+ minutes, e.g. 2026-11-26 01:37-07:15, where bounds were about 20380 s.
     Report them on their own line. They are not scanner misses.
   - Report on-time % with and without the outage items, and the item count.
2. Explain the late items: which lane first saw each one, what its bound was, and
   why the faster lanes missed it.
3. Optimize within the load budget: about 48-50 Walmart reads/min. Walmart answers
   in about 1 s and allows one request in flight, and more risks bot checks.
   - Use the existing study scripts: items.py, marginal.py, searches.py,
     replay_hot.py, cadence_sim.mjs.
   - Prefer changes the simulation shows help at equal load.
   - Raising the load or adding a second scanner browser/profile is the owner's
     call: propose it with numbers, don't do it.
4. Ship. Run tests, bump the extension version, update tasks/ACTIVE.md (a new
   section with the result and rollback), commit and push. Standing OK: always
   commit and push. The repo is public, so scan the diff for secrets.
5. Tell the owner, in a few lines:
   - the measured %,
   - what changed,
   - **"reload the extension"** (it does not reload itself),
   - the run streak at ≥98%.
   Append one line to tasks/coverage-loop/history.md: date, version, window, %
   (without outages), items, change.

## Rules

- Call the machine "the alert server", never "home" or "PC".
- Other agents may share the working tree: stage only your own files, and check
  `hc ls` before rebuilding Docker.
- No Walmart probing beyond what the scanner already does. Analysis is read-only
  on the logs.
