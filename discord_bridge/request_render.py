"""Personal request DMs: the single new-listing card and the compact catch-up list.

Rendering only; no storage or transport. The store packs catch-up DMs with the
same renderer and payload_limits, so every part it leases fits one message.
"""

from __future__ import annotations

import re

from .messages import render_message
from .payload_limits import payload_problems

# Bounded per-item text keeps ten compact items inside one DM in practice.
TITLE_CHARS = 80
MATCHED_CHARS = 60
SINGLE_MATCHED_CHARS = 1000
_MARKDOWN = re.compile(r"([\\*_~`|\[\]<>])")


def plain(text, limit):
    """One line of member/product text with Discord markdown and mentions inert."""
    text = " ".join(str(text).split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return _MARKDOWN.sub(r"\\\1", text).replace("@", "@​")


def money(value):
    return f"${value / 100:,.2f}" if value else "value not shown"


def product_link(item):
    """Short canonical product page; the Claim button carries the review link."""
    return "https://www.walmart.com/ip/" + item["item_id"].removeprefix("ip-")


def _meta(job):
    meta = job.get("catchup")
    return meta if isinstance(meta, dict) else {}


def render_bundle_message(jobs, *, part=None, parts=None, shown=None, total=None):
    """Older matches in one DM: one numbered list, links on numbered buttons."""
    meta = _meta(jobs[0])
    part = part or meta.get("part") or 1
    parts = parts or meta.get("parts") or 1
    shown = shown or meta.get("shown") or len(jobs)
    total = max(total or meta.get("total") or 0, shown)
    renumber = parts == 1 and len(jobs) < shown
    if renumber:
        # Matches that sold out (or stopped matching) since the catch-up was
        # ranked are not sent: count and number only what this DM carries.
        total, shown = max(total - (shown - len(jobs)), len(jobs)), len(jobs)
    count = "1 item" if total == 1 else f"{total} items"
    verb = "was" if total == 1 else "were"
    content = f"{count} matching your requests {verb} already listed when you added them."
    if total > shown:
        content += (
            f" These are the {shown} with the highest value."
            f" Use `/request listed` to see all {total}."
        )
    else:
        content += " New listings still alert you right away."
    if parts > 1:
        content = f"({part} of {parts}) {content}"
    lines, buttons = [], []
    for position, job in enumerate(jobs, 1):
        item = job["item"]
        rank = position if renumber else _meta(job).get("rank") or position
        lines.append(
            f"**{rank}.** [{plain(item['title'], TITLE_CHARS)}]({product_link(item)})"
            f" · {money(item.get('value_cents'))}\n"
            f"Matched: {plain(', '.join(job['queries']), MATCHED_CHARS)}"
        )
        buttons.append(
            {"type": 2, "style": 5, "label": f"{rank} · Claim item", "url": item["review_url"]}
        )
    return {
        "content": content,
        "allowed_mentions": {"parse": []},
        "embeds": [{"title": "Already-listed matches", "description": "\n\n".join(lines)}],
        "components": [
            {"type": 1, "components": buttons[start:start + 5]}
            for start in range(0, len(buttons), 5)
        ],
        "nonce": jobs[0]["nonce"],
        "enforce_nonce": True,
    }


def pack_catchup(jobs):
    """Split ranked catch-up jobs, in order, into DMs that each pass every limit.

    Sizes use the widest part label, so numbering the parts afterwards cannot
    push a packed DM over a limit.
    """
    packed, current = [], []
    for job in jobs:
        trial = [*current, job]
        if current and payload_problems(render_bundle_message(trial, part=10, parts=10)):
            packed.append(current)
            trial = [job]
        current = trial
    if current:
        packed.append(current)
    return packed


def is_catchup_list(job):
    meta = _meta(job)
    return bool(job.get("bundle")) or meta.get("parts", 1) > 1 or meta.get("total", 1) > 1


def render_request_message(job):
    if is_catchup_list(job):
        return render_bundle_message([job, *(job.get("bundle") or ())])
    item = job["item"]
    payload = render_message(
        item, first_seen=job["observed"], last_seen=job["observed"], route="pro-all"
    )
    queries = ", ".join(query.replace("@", "@​") for query in job["queries"])
    if len(queries) > SINGLE_MATCHED_CHARS:
        queries = queries[: SINGLE_MATCHED_CHARS - 1] + "…"
    payload["content"] = "Your requested item was found!"
    lines = [f"Matched requests: {queries}"]
    searched = job.get("search_queries") or []
    if searched:
        found = ", ".join(query.replace("@", "@​") for query in searched)
        if len(found) > SINGLE_MATCHED_CHARS:
            found = found[: SINGLE_MATCHED_CHARS - 1] + "…"
        lines.append("Found in Walmart's search results for: " + found)
    if item.get("value_cents") is None:
        lines.append("Value: not shown on the listing, so check it before claiming.")
    lines.append(f"Observed: <t:{int(job['observed'])}:R>")
    payload["embeds"][0]["description"] = "\n".join(lines)
    payload.update(nonce=job["nonce"], enforce_nonce=True)
    return payload
