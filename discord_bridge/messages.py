"""Discord message rendering, independent of queue persistence and transport."""

from __future__ import annotations

from .observations import _product_image
from .policy import (
    FREE_DELAY_SECONDS,
    FREE_ROUTE_KEY,
    RESTOCK_AWAY_SECONDS,
    STARTER_DELAY_SECONDS,
)


def _embed_title(text: str) -> str:
    # A conservative UTF-16 bound also covers non-BMP titles; never split a
    # surrogate pair at Discord's per-title limit.
    return text.encode("utf-16-le", errors="replace")[:512].decode("utf-16-le", errors="ignore")


def render_message(
    item: dict, *, first_seen: float, last_seen: float, route: str, restocked: bool = False
) -> dict:
    value = item["value_cents"]
    money = f"${value / 100:,.2f}" if value else "Retail value unverified"
    title = item["title"].replace("@", "@\u200b")
    historical = route == FREE_ROUTE_KEY
    lines = [f"Category: {item['category']}"]
    if restocked and not historical:
        lines.insert(0, "Back in stock")
    if historical:
        lines.append(f"Historical alert — {FREE_DELAY_SECONDS / 3600:g}-hour delay")
    elif route == "starter-items":
        lines.append(f"{STARTER_DELAY_SECONDS / 60:g}-minute delay")
    # No "Last observed": it was frozen at send time and only repeated the
    # detection time. Back-in-stock alerts say when the item was first listed.
    label = "First listed" if restocked and not historical else "First detected"
    lines.append(f"{label}: <t:{int(first_seen)}:f>")
    description = "\n".join(lines)
    if historical:
        description += (
            "\nTitle and retail value are from the original captured observation. "
            "This is a historical listing, not a current availability alert."
        )
    embed = {
        "title": _embed_title(f"{money} | {title}"),
        # The title is the claim link: members tap or long-press it (copyable on
        # phones, kept when an alert is forwarded). No buttons since 2026-11-26.
        "url": item["review_url"],
        "description": description,
        "footer": {"text": item["item_id"]},
    }
    if image := _product_image(item.get("image_url")):
        embed["thumbnail"] = {"url": image}
    return {
        "allowed_mentions": {"parse": []},
        "embeds": [embed],
    }


def render_batch(jobs: list[dict]) -> dict:
    """Bundle only already-ready items from one destination, without a fill timer."""
    maximum = 5 if jobs and jobs[0]["route"] == FREE_ROUTE_KEY else 10
    if not 1 <= len(jobs) <= maximum or len({job["route"] for job in jobs}) != 1:
        raise ValueError("invalid dispatch item count or destination")
    messages = [
        render_message(
            job["item"],
            first_seen=job["first_seen"],
            last_seen=job["last_seen"],
            route=job["route"],
            # A re-armed restock is the only job due well after first detection.
            restocked=job.get("due", job["first_seen"]) - job["first_seen"] >= RESTOCK_AWAY_SECONDS,
        )
        for job in jobs
    ]
    if len(messages) == 1:
        return messages[0]
    result = {
        "content": f"**{len(jobs)} item alerts**",
        "allowed_mentions": {"parse": []},
        "embeds": [],
    }
    for index, message in enumerate(messages, 1):
        embed = message["embeds"][0]
        embed["title"] = _embed_title(f"[{index}] {embed['title']}")
        result["embeds"].append(embed)
    characters = sum(
        len((embed["title"] + embed["description"] + embed["footer"]["text"]).encode("utf-16-le"))
        // 2
        for embed in result["embeds"]
    )
    if characters > 6000:
        raise ValueError("dispatch exceeds Discord embed text budget")
    return result
