# Coverage loop history

On-time = new $80+ items whose worst-case discovery delay is 60 s or less (delay_bounds.py).

| date | extension | window | on time (no outage) | items | change |
|---|---|---|---|---|---|
| 2026-11-25 | 2.32.8 | 95 h | 88.5% | 174 | baseline before 2.32.9 |
| 2026-11-27 | 2.32.9 | 47.9 h | 86.4% (80.5% incl. 11-26 outage) | 81 (+6 outage) | search reallocation at equal load: no gain |
| 2026-11-28 | 2.32.9 (2.32.10 shipped) | 58.9 h | 85.7% (80.4% incl. 11-26 outage) | 91 (+6 outage) | min79 pages 5-6 after the $80+ block at 40 s, not 3 min (equal load) |
