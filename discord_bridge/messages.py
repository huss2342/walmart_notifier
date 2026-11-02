"""Discord message rendering, independent of queue persistence and transport."""

from __future__ import annotations

from .observations import _product_image
from .policy import FREE_DELAY_SECONDS, FREE_ROUTE_KEY, STARTER_DELAY_SECONDS


def _embed_title(text: str) -> str:
    # A conservative UTF-16 bound also covers non-BMP titles; never split a
    # surrogate pair at Discord's per-title limit.
    return text.encode("utf-16-le", errors="replace")[:512].decode("utf-16-le", errors="ignore")


def render_message(item: dict, *, first_seen: float, last_seen: float, route: str) -> dict:
    value = item["value_cents"]
    money = f"${value / 100:,.2f}" if value else "Retail value unverified"
    title = item["title"].replace("@", "@\u200b")
    historical = route == FREE_ROUTE_KEY
    lines = [f"Category: {item['category']}"]
    if historical:
        lines.append(f"Historical alert — {FREE_DELAY_SECONDS / 3600:g}-hour delay")
    elif route == "starter-items":
        lines.append(f"{STARTER_DELAY_SECONDS / 60:g}-minute delay")
    lines.extend(
        [f"First detected: <t:{int(first_seen)}:f>", f"Last observed: <t:{int(last_seen)}:R>"]
    )
    description = "\n".join(lines)
    if historical:
        description += (
            "\nTitle and retail value are from the original captured observation. "
            "This is a historical listing, not a current availability alert."
        )
    embed = {
        "title": _embed_title(f"{money} | {title}"),
        "url": item["product_url"],
        "description": description,
        "footer": {"text": item["item_id"]},
    }
    if image := _product_image(item.get("image_url")):
        embed["thumbnail"] = {"url": image}
    return {
        "allowed_mentions": {"parse": []},
        "embeds": [embed],
        "components": [
            {
                "type": 1,
                "components": [
                    {"type": 2, "style": 5, "label": label, "url": url}
                    for label, url in (
                        ("Open review item", item["review_url"]),
                        ("View product", item["product_url"]),
                    )
                ],
            }
        ],
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
        )
        for job in jobs
    ]
    if len(messages) == 1:
        return messages[0]
    result = {
        "content": f"**{len(jobs)} item alerts**",
        "allowed_mentions": {"parse": []},
        "embeds": [],
        "components": [],
    }
    for index, message in enumerate(messages, 1):
        embed = message["embeds"][0]
        embed["title"] = _embed_title(f"[{index}] {embed['title']}")
        # Each card itself opens review context; members needn't scroll through
        # the other cards to reach the numbered button rows below the bundle.
        embed["url"] = jobs[index - 1]["item"]["review_url"]
        result["embeds"].append(embed)
        row = message["components"][0]
        for button in row["components"]:
            button["label"] = f"{index} · {button['label']}"
        # Two item pairs share each row only for larger paid bursts. All
        # review/product links survive within five rows and four buttons/row.
        if len(jobs) > 5 and index % 2 == 0:
            result["components"][-1]["components"].extend(row["components"])
        else:
            result["components"].append(row)
    characters = sum(
        len((embed["title"] + embed["description"] + embed["footer"]["text"]).encode("utf-16-le"))
        // 2
        for embed in result["embeds"]
    )
    if characters > 6000:
        raise ValueError("dispatch exceeds Discord embed text budget")
    return result
