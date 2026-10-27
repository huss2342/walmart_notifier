"""Read-only history audit for the October 21 Discord plan; no project imports.

Run from the repository root with:
    .venv/Scripts/python.exe tasks/research/analyze_item_history.py
Only derived files in tasks/research are written. Runtime state is never opened
for writing, instantiated as a SeenStore, pruned, or compacted.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import math
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).resolve().parent
BANDS = [(0, 10), (10, 20), (20, 40), (40, 80), (80, 200), (200, None)]


def analyze(root: Path = ROOT) -> dict:
    snapshot_bytes = (root / "data/seen.json").read_bytes()
    log_bytes = (root / "data/seen.log").read_bytes()
    rows = dict(json.loads(snapshot_bytes)["seen"])
    snapshot_count = len(rows)
    event_counts: Counter = Counter()
    malformed = 0
    overlap = 0
    for line in log_bytes.decode("utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        event_counts[event.get("t")] += 1
        if event.get("t") == "item" and isinstance(event.get("v"), dict):
            overlap += event["k"] in rows
            rows[event["k"]] = event["v"]

    valid = {
        key: row
        for key, row in rows.items()
        if isinstance(row.get("value_usd"), (int, float))
        and not isinstance(row["value_usd"], bool)
        and math.isfinite(row["value_usd"])
        and row["value_usd"] >= 0
    }
    values = sorted(row["value_usd"] for row in valid.values())
    stamps = [datetime.fromisoformat(row["seen_at"]) for row in rows.values()]
    latest = max(stamps)
    histogram = []
    for low, high in BANDS:
        count = sum(low <= value and (high is None or value < high) for value in values)
        histogram.append(
            {
                "band": f"${low} to <${high}" if high else f"${low}+",
                "lower_inclusive": low,
                "upper_exclusive": high,
                "item_ids": count,
                "share_pct": round(100 * count / len(values), 4),
            }
        )
    recent = {}
    for days in (7, 14):
        subset = [
            row
            for row in valid.values()
            if datetime.fromisoformat(row["seen_at"]) >= latest - timedelta(days=days)
        ]
        recent[str(days)] = {
            "item_ids": len(subset),
            "counts_at_least_usd": {
                str(floor): sum(row["value_usd"] >= floor for row in subset)
                for floor in (40, 80, 100, 200)
            },
        }
    high = {key: row for key, row in valid.items() if row["value_usd"] >= 200}
    # Reconcile the disjoint histogram against independent threshold counts.
    assert sum(band["item_ids"] for band in histogram) == len(values)
    assert sum(band["item_ids"] for band in histogram[:3]) == sum(v < 40 for v in values)
    assert histogram[-1]["item_ids"] == len(high)
    assert snapshot_count + event_counts["item"] - overlap == len(rows)

    return {
        "captured_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "sources": {
            "data/seen.json": {
                "sha256": hashlib.sha256(snapshot_bytes).hexdigest(),
                "bytes": len(snapshot_bytes),
                "item_ids": snapshot_count,
            },
            "data/seen.log": {
                "sha256": hashlib.sha256(log_bytes).hexdigest(),
                "bytes": len(log_bytes),
                "event_counts": dict(event_counts),
            },
        },
        "malformed_log_lines": malformed,
        "overlapping_item_events": overlap,
        "item_ids": len(rows),
        "known_values": len(values),
        "unknown_or_invalid_values": len(rows) - len(values),
        "non_walmart_id_records": sum(not key.startswith("ip-") for key in rows),
        "recorded_from_utc": min(stamps).isoformat(),
        "recorded_through_utc": latest.isoformat(),
        "zero_value_ids": sum(v == 0 for v in values),
        "explicit_variant_ids": sum("variant on" in row["title"].lower() for row in rows.values()),
        "high_value_explicit_variant_ids": sum(
            "variant on" in row["title"].lower() for row in high.values()
        ),
        "min_value_usd": min(values),
        "max_value_usd": max(values),
        "percentiles_nearest_rank_index": {
            str(p): values[round((len(values) - 1) * p)]
            for p in (0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99)
        },
        "bands": histogram,
        "trailing_days_ending_at_latest_record": recent,
        "daily_recorded_ids_utc": dict(
            sorted(Counter(row["seen_at"][:10] for row in rows.values()).items())
        ),
        "top_value_sample": [
            dict(item_id=key, **row)
            for key, row in sorted(
                valid.items(), key=lambda pair: (-pair[1]["value_usd"], pair[0])
            )[:20]
        ],
        "record_fields": sorted({field for row in rows.values() for field in row}),
        "caveats": [
            "Handled item IDs, including filtered and seeded records, "
            "are not successful alerts or claims.",
            "Variants are separate Walmart item IDs; explicit variant text "
            "is only a lower bound on families.",
            "seen_at is a handling timestamp, not a Walmart listing timestamp "
            "or verified availability.",
            "No department, source query, last-seen, full URL, image, "
            "or stock history is retained here.",
            "Historical titles may be truncated to 120 characters; older rows can be longer.",
            "Zero retail values are recorded observations and need separate quality review.",
            "Coverage depends on the owner's account, query modes, retention, and scanner uptime.",
            "Trailing windows end at the latest recorded timestamp, not at execution time.",
        ],
    }


def write_companion_notebook() -> None:
    """Create and execute a dependency-free evidence notebook, in cell order."""
    markdown = (
        "# Walmart item history audit\n\n## tl;dr\n"
        "The October 21 capture contains 70,399 IDs; 95.7% are below $40 and only 18 are $200+. "
        "These are observed IDs including variants, not verified claim opportunities.\n\n"
        "## Context & Methods\n"
        "Replay item events over the snapshot, exclude marker events, "
        "and count one row per Walmart ID. "
        "Use half-open USD bands. The latest history record defines trailing windows.\n\n"
        "### Key Assumptions\n"
        "This local retained history is useful for a pilot's routing design, "
        "not a complete Walmart catalog. "
        "Re-execution uses current files and can change results. "
        "No Walmart, Discord, or Telegram calls occur.\n\n"
        "## Data\n"
        "Inputs: repository data/seen.json and data/seen.log. "
        "Exact hashes are in item-history-summary.json. "
        "Python standard library only; run from this repository or tasks/research.\n\n## Results"
    )
    code = (
        "import json\nimport runpy\nfrom pathlib import Path\n\n"
        "root = Path.cwd().resolve()\n"
        "while not (root / 'src/state.py').is_file():\n"
        "    if root == root.parent:\n"
        "        raise RuntimeError('Run inside the walmart_notifier repository')\n"
        "    root = root.parent\n"
        "audit = runpy.run_path(str(root / 'tasks/research/analyze_item_history.py'))\n"
        "result = audit['analyze'](root)\n"
        "keys = ['item_ids', 'known_values', 'zero_value_ids', 'explicit_variant_ids',\n"
        "        'bands', 'max_value_usd', 'trailing_days_ending_at_latest_record']\n"
        "print(json.dumps({k: result[k] for k in keys}, indent=2))\n"
    )
    namespace: dict = {}
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        exec(compile(code, "item-history-audit.ipynb cell 1", "exec"), namespace)
    notebook = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": "3.13"},
        },
        "cells": [
            {
                "id": "context",
                "cell_type": "markdown",
                "metadata": {},
                "source": markdown.splitlines(True),
            },
            {
                "id": "audit",
                "cell_type": "code",
                "metadata": {},
                "source": code.splitlines(True),
                "execution_count": 1,
                "outputs": [
                    {
                        "output_type": "stream",
                        "name": "stdout",
                        "text": stdout.getvalue().splitlines(True),
                    }
                ],
            },
            {
                "id": "takeaways",
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    "## Takeaways\nStart with low-value price bands and a small paid beta. "
                    "Do not promise $200+ supply, whole-catalog coverage, "
                    "or live stock based on this ledger.\n\n"
                    "Executed sequentially with a standard-library harness. "
                    "Jupyter/nbformat are not installed; "
                    "native Jupyter rendering was not checked. "
                    "Exact tables are also in the design brief."
                ],
            },
        ],
    }
    (OUTPUT / "item-history-audit.ipynb").write_text(
        json.dumps(notebook, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    report = analyze()
    (OUTPUT / "item-history-summary.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    write_companion_notebook()
    print(
        json.dumps(
            {k: report[k] for k in ("item_ids", "known_values", "bands", "max_value_usd")}, indent=2
        )
    )
