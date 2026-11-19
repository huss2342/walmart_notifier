"""Read-only discovery-yield report: which scan lane/page first finds items, at what value.

Joins the browser's ingest requestId (X-Request-ID) to the Discord collector's
http_request_id. A first sighting is a pro-all job queued at intake: the extension
posts only in-stock items, so every first sighting is a new Pro alert. Prints
aggregates only (no item IDs, titles, queries or member data).

    .venv/Scripts/python.exe tools/lane_yield.py --hours 48
    .venv/Scripts/python.exe tools/lane_yield.py --hours 4 --coverage   # $80+ coverage only
    .venv/Scripts/python.exe tools/lane_yield.py --self-test

The $80+ coverage section asks, for every $80+ offer seen in stock: did a fast
$80+ lane (price_high pages or the min_price list) see it, and how long after
its first sighting by any lane. --details adds the item IDs of the misses.
"""

from __future__ import annotations

import argparse
import collections
import gzip
import json
import time
from pathlib import Path

BANDS = ("under-10", "10-20", "20-40", "40-80", "80-200", "200-plus")
# Before 2.30.0 direct reads posted with intakePhase "live"; tab live observers also use it.
LEGACY_FAST_LANES = {"newest", "value", "requests"}  # value80 arrived with phase "fast"
CAP_ITEMS = 25 * 40  # Walmart shows at most 25 pages of 40 per listing.
HIGH_BANDS = {"80-200", "200-plus"}
FAST_80 = {"fast:value", "fast:value80"}
# A fast $80+ lane seeing an offer a minute after another lane did counts as late.
LATE_SECONDS = 60


def records(paths, since):
    for path in paths:
        opener = gzip.open if path.suffix == ".gz" else open
        try:
            with opener(path, "rt", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    at = row.get("at")
                    stamp = at / 1000 if at else row.get("timestamp") or 0
                    if stamp >= since:
                        yield row
        except OSError:
            continue


def files(data, name):
    base = data / name
    return sorted(base.parent.glob(base.name + ".*.gz")) + [base]


def lane_key(row):
    # Fast reads post with intakePhase "fast" (legacy "live"); tab lanes use initial/late.
    phase, lane = row.get("intakePhase"), row.get("lane")
    fast = phase == "fast" or (phase == "live" and lane in LEGACY_FAST_LANES)
    return ("fast:" if fast else "tab:") + str(lane)


def bucket(page):
    page = page or 0
    return str(page) if page <= 4 else "5-10" if page <= 10 else "11+"


def pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))] if values else None


def price_bands(paths):
    """Item -> price band from every retained routing record (bands are logged only there)."""
    bands = {}
    for path in paths:
        opener = gzip.open if path.suffix == ".gz" else open
        try:
            with opener(path, "rt", encoding="utf-8") as handle:
                for line in handle:
                    if '"route":"price-' not in line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    for item in row.get("item_ids") or []:
                        bands[item] = str(row.get("route", ""))[6:]
        except OSError:
            continue
    return bands


def coverage80(requests, delivery_rows, bands):
    """For each $80+ offer seen in stock: did a fast $80+ lane see it, and how late."""
    first, fast, lanes = {}, {}, collections.defaultdict(set)
    queued, restocks = set(), 0
    for row in delivery_rows:
        event = row.get("event")
        if event == "intake":
            lane = (requests.get(row.get("http_request_id")) or ("unknown",))[0]
            stamp = row.get("timestamp") or 0
            for item in row.get("observation_item_ids") or []:
                if bands.get(item) not in HIGH_BANDS:
                    continue
                lanes[item].add(lane)
                first[item] = min(first.get(item, stamp), stamp)
                if lane in FAST_80:
                    fast[item] = min(fast.get(item, stamp), stamp)
        elif event == "observation_routed" and row.get("route") == "pro-all":
            if row.get("transition") == "queued":
                queued.update(row.get("item_ids") or [])
            elif row.get("transition") == "restocked":
                restocks += len(row.get("item_ids") or [])
    missed = sorted(item for item in lanes if item not in fast)
    late = sorted(item for item in fast if fast[item] - first[item] > LATE_SECONDS)
    new = sorted(item for item in queued if item in lanes)
    return {"seen": len(lanes), "missed": missed, "late": late, "restocks": restocks,
            "new": len(new), "new_missed": [i for i in new if i in missed],
            "new_late": [i for i in new if i in late],
            "slow_lanes": collections.Counter(lane for item in missed for lane in lanes[item])}


def analyze(browser_rows, delivery_rows, bands=None):
    requests, reads, seen = {}, collections.defaultdict(list), set()
    rounds = collections.defaultdict(lambda: [None, None])
    dept_pages, dept_attempts = collections.Counter(), collections.Counter()
    skips = collections.Counter()
    challenges = 0
    sizes = {}  # department index -> (items, pages) from its latest page-1 direct read
    for row in browser_rows:
        key = (row.get("streamId"), row.get("eventSeq"))
        if key[1] is not None:
            if key in seen:  # Rotated archives can repeat rows.
                continue
            seen.add(key)
        event = row.get("event")
        if event == "ingest-stage" and row.get("stage") == "queued" and row.get("requestId"):
            requests[row["requestId"]] = (lane_key(row), row.get("page"))
        elif event == "fast-read":
            reads[(row.get("lane"), row.get("page"))].append(row)
            if (row.get("lane") == "departments" and row.get("page") == 1
                    and row.get("count") is not None and row.get("targetIndex") is not None):
                sizes[row["targetIndex"]] = (row["count"], row.get("totalPages"))
        elif event == "challenge":
            challenges += 1
        if row.get("lane") == "departments" and row.get("round") is not None:
            span = rounds[row["round"]]
            span[0] = min(span[0] or row["at"], row["at"])
            span[1] = max(span[1] or row["at"], row["at"])
            target = row.get("targetIndex")
            if event == "auxiliary-page-delivered":
                dept_pages[target] += 1
            elif event == "auxiliary-target-start":
                dept_attempts[target] += 1
            elif event == "auxiliary-target-skipped":
                skips[(target, row.get("failureCode"))] += 1

    finds = collections.defaultdict(collections.Counter)
    band, dms = {}, collections.Counter()
    for row in delivery_rows:
        where = requests.get(row.get("http_request_id"))
        if row.get("event") == "observation_routed" and row.get("transition") == "queued":
            route = str(row.get("route", ""))
            for item in row.get("item_ids") or []:
                if route.startswith("price-"):
                    band[item] = route[6:]
                if route == "pro-all" and where:
                    finds[(where[0], bucket(where[1]))][item] += 1
        elif row.get("event") == "request_match" and row.get("queued") and where:
            dms[where[0]] += row["queued"]
    found = collections.defaultdict(collections.Counter)
    for key, items in finds.items():
        for item in items:
            found[key][band.get(item, "unknown")] += 1
    return {"reads": reads, "found": found, "dms": dms, "rounds": rounds, "challenges": challenges,
            "dept_pages": dept_pages, "dept_attempts": dept_attempts, "skips": skips,
            "sizes": sizes,
            "coverage": coverage80(requests, delivery_rows, bands) if bands is not None else None}


def gap_stats(reads, keys):
    gaps = []
    for key in keys:
        stamps = sorted(row["at"] for row in reads.get(key, []))
        gaps += [(b - a) / 1000 for a, b in zip(stamps, stamps[1:], strict=False)]
    return (round(pct(gaps, 0.5), 1), round(pct(gaps, 0.9), 1)) if gaps else None


def report_coverage(result, details=False):
    reads, cov = result["reads"], result["coverage"]
    rows = [row for rows in reads.values() for row in rows]
    span = (max(r["at"] for r in rows) - min(r["at"] for r in rows)) / 60_000 if rows else 0
    blocked = sum(r.get("outcome") == "blocked" for r in rows)
    print(f"== $80+ coverage ({len(rows) / span if span else 0:.1f} reads/min, blocked {blocked}, "
          f"challenges {result['challenges']})")
    pages80 = [key for key in reads if key[0] == "value80"]
    print(f"   cadence s (median, p90): value p1 {gap_stats(reads, [('value', 1)])}  "
          f"value p2-4 {gap_stats(reads, [('value', p) for p in (2, 3, 4)])}  "
          f"value80 pages {gap_stats(reads, pages80)}")
    seen = cov["seen"]
    caught = seen - len(cov["missed"])
    share = 100 * caught / seen if seen else 100
    print(f"   $80+ offers seen in stock: {seen}; by a fast $80+ lane: {caught} ({share:.0f}%);"
          f" only other lanes: {len(cov['missed'])} {dict(cov['slow_lanes'])}")
    # Unchanged pages are re-posted every 5 minutes, so lateness is judged on new alerts only.
    print(f"   new $80+ alerts: {cov['new']}; fast lanes missed {len(cov['new_missed'])}, "
          f"over {LATE_SECONDS} s late {len(cov['new_late'])}; restock alerts: {cov['restocks']}")
    if details and (cov["missed"] or cov["new_late"]):
        print(f"   missed: {cov['missed']}")
        print(f"   new and late: {cov['new_late']}")


def report(result):
    reads = result["reads"]
    total = sum(len(rows) for rows in reads.values())
    stamps = [row["at"] for rows in reads.values() for row in rows]
    minutes = (max(stamps) - min(stamps)) / 60000 if stamps else 0
    print(f"== fast reads: {total} over {minutes:.0f} min ({total / max(minutes, 1):.1f}/min)")
    print(f"{'lane':9} {'pg':>3} {'reads':>6} {'share':>6} {'new':>4} "
          f"{'revisit p50 s':>13} {'blocked':>7}")
    for (lane, page), rows in sorted(reads.items(), key=lambda kv: (str(kv[0][0]), kv[0][1] or 0)):
        stamps = sorted(r["at"] for r in rows)
        gaps = [(b - a) / 1000 for a, b in zip(stamps, stamps[1:], strict=False)]
        revisit = pct(gaps, 0.5)
        print(f"{lane!s:9} {page!s:>3} {len(rows):>6} {len(rows) / max(total, 1):>6.1%} "
              f"{sum(r.get('new', 0) for r in rows):>4} "
              f"{'-' if revisit is None else round(revisit, 1):>13} "
              f"{sum(r.get('outcome') == 'blocked' for r in rows):>7}")
    print("\n== first sightings (new in-stock items) by lane/page and value band")
    print(f"{'lane':17} {'pg':>4} {'total':>5} " + " ".join(f"{b:>8}" for b in BANDS) + "   >=$80")
    for key in sorted(result["found"]):
        counts = result["found"][key]
        high = counts["80-200"] + counts["200-plus"]
        print(f"{key[0]:17} {key[1]:>4} {sum(counts.values()):>5} "
              + " ".join(f"{counts[b]:>8}" for b in BANDS) + f"   {high:>5}")
    searches = collections.defaultdict(list)
    for (lane, page), rows in reads.items():
        if lane in ("requests", "priority") and page == 1:
            for row in rows:
                if row.get("targetIndex"):
                    searches[(lane, row["targetIndex"])].append(row["at"])
    for lane in ("requests", "priority"):
        per_query = []
        for (name, _), stamps in searches.items():
            stamps = sorted(stamps)
            if name == lane and len(stamps) > 1:
                gaps = [(b - a) / 1000 for a, b in zip(stamps, stamps[1:], strict=False)]
                per_query.append(pct(gaps, 0.5))
        if per_query:
            print(f"{lane} search page-1 revisit per query: p50 {pct(per_query, 0.5):.0f} s "
                  f"over {len(per_query)} searches")
    print("\n== personal-request DMs queued, by lane that first matched:", dict(result["dms"]))
    spans =[(end - start) / 60000 for start, end in result["rounds"].values() if start and end]
    if spans:
        print(f"\n== departments: {len(spans)} rounds, minutes p50 {pct(spans, 0.5):.0f}")
    stuck = sorted(t for t, n in result["dept_attempts"].items()
                   if t is not None and n >= 3 and not result["dept_pages"][t])
    print("departments targets attempted >=3x with zero delivered pages:", stuck)
    print("department skips (targetIndex, failureCode):", result["skips"].most_common(8))
    sizes = result["sizes"]
    if sizes:
        capped = [items for items, _ in sizes.values() if items > CAP_ITEMS]
        print(f"department sizes from direct reads: {len(sizes)} measured, {len(capped)} over the "
              f"25-page cap, ~{sum(capped) - CAP_ITEMS * len(capped)} items beyond it "
              f"(of {sum(items for items, _ in sizes.values())})")
    print("challenge events:", result["challenges"])


def self_test_coverage():
    requests = {"a": ("fast:value80", 2), "b": ("fast:requests", 1), "c": ("fast:value", 1)}
    def routed(route, transition, items):
        return {"event": "observation_routed", "route": route, "transition": transition,
                "item_ids": items}

    def intake(request, stamp, items):
        return {"event": "intake", "http_request_id": request, "timestamp": stamp,
                "observation_item_ids": items}

    delivery = [routed("price-80-200", "queued", ["x", "y"]),
                routed("pro-all", "queued", ["x", "y"]),
                routed("pro-all", "restocked", ["z"]), intake("a", 100, ["x"]),
                intake("b", 90, ["y", "x"]), intake("c", 500, ["cheap"])]
    cov = coverage80(requests, delivery, {"x": "80-200", "y": "200-plus", "cheap": "10-20"})
    assert cov["seen"] == 2 and cov["missed"] == ["y"] and cov["late"] == [], cov
    assert cov["new"] == 2 and cov["new_missed"] == ["y"] and cov["restocks"] == 1, cov
    assert cov["slow_lanes"] == {"fast:requests": 1}, cov


def self_test():
    self_test_coverage()
    browser = [
        {"event": "ingest-stage", "stage": "queued", "requestId": "a", "lane": "value",
         "intakePhase": "live", "page": 1, "at": 1, "eventSeq": 1},
        {"event": "ingest-stage", "stage": "queued", "requestId": "b", "lane": "departments",
         "intakePhase": "initial", "page": 25, "at": 2, "eventSeq": 2},
        {"event": "fast-read", "lane": "value", "page": 1, "new": 1, "outcome": "items",
         "at": 3, "eventSeq": 3},
        {"event": "fast-read", "lane": "value", "page": 1, "new": 1, "outcome": "items",
         "at": 3, "eventSeq": 3},  # duplicate archive row is ignored
    ]
    delivery = [
        {"event": "observation_routed", "transition": "queued", "route": "pro-all",
         "http_request_id": "a", "item_ids": ["ip-1"]},
        {"event": "observation_routed", "transition": "queued", "route": "price-200-plus",
         "http_request_id": "a", "item_ids": ["ip-1"]},
        {"event": "observation_routed", "transition": "queued", "route": "pro-all",
         "http_request_id": "b", "item_ids": ["ip-2"]},
        {"event": "request_match", "queued": 1, "http_request_id": "b"},
    ]
    browser.append({"event": "fast-read", "lane": "departments", "page": 1, "count": 1840,
                    "totalPages": 25, "targetIndex": 4, "outcome": "items", "at": 4, "eventSeq": 4})
    browser.append({"event": "ingest-stage", "stage": "queued", "requestId": "c",
                    "lane": "departments", "intakePhase": "fast", "page": 3, "at": 5,
                    "eventSeq": 5})
    delivery.append({"event": "observation_routed", "transition": "queued", "route": "pro-all",
                     "http_request_id": "c", "item_ids": ["ip-3"]})
    out = analyze(browser, delivery)
    assert out["sizes"] == {4: (1840, 25)}
    assert out["found"][("fast:departments", "3")]["unknown"] == 1
    assert out["found"][("fast:value", "1")]["200-plus"] == 1
    assert out["found"][("tab:departments", "11+")]["unknown"] == 1
    assert out["dms"]["tab:departments"] == 1
    assert len(out["reads"][("value", 1)]) == 1
    print("self-test ok")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, default=Path(__file__).resolve().parents[1] / "data")
    parser.add_argument("--hours", type=float, default=48.0, help="0 = all retained logs")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--coverage", action="store_true", help="only the $80+ coverage section")
    parser.add_argument("--details", action="store_true", help="item IDs of $80+ misses")
    args = parser.parse_args(argv)
    if args.self_test:
        self_test()
        return
    since = time.time() - args.hours * 3600 if args.hours > 0 else 0
    result = analyze(
        records(files(args.data, "browser-diagnostic-events.jsonl"), since),
        list(records(files(args.data, "discord_bridge/delivery.jsonl"), since - 120)),
        price_bands(files(args.data, "discord_bridge/delivery.jsonl")),
    )
    if not args.coverage:
        report(result)
    report_coverage(result, args.details)


if __name__ == "__main__":
    main()
