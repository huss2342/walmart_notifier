""""Copy link" under item alerts.

Discord link buttons can't be copied on a phone, so members had to open an alert on a
computer to paste its claim link somewhere else. This button answers only the member
who tapped it, with the alert's Claim item links as plain links (long-press > Copy).
"""

from urllib.parse import urlsplit

CUSTOM_ID = "copy-link"
REPLY_LIMIT = 2000


def copy_button(count: int = 1) -> dict:
    return {"type": 2, "style": 2, "label": "Copy link" if count == 1 else "Copy links",
            "custom_id": CUSTOM_ID}


def _field(value, name):
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def claim_links(rows) -> list[str]:
    """Walmart link buttons in display order (dict payloads or discord.py components)."""
    links = []
    for row in rows or ():
        for child in _field(row, "components") or _field(row, "children") or ():
            url = _field(child, "url")
            if isinstance(url, str) and (urlsplit(url).hostname or "").endswith("walmart.com"):
                links.append(url)
    return links


def reply_text(links: list[str]) -> str:
    if not links:
        return "This alert has no claim link."
    if len(links) == 1:
        return links[0]
    text = ""
    for index, link in enumerate(links, 1):
        line = f"{index}. {link}\n"
        if len(text) + len(line) > REPLY_LIMIT:
            break
        text += line
    return text.rstrip()


class CopyLinkHandler:
    def accepts(self, interaction) -> bool:
        data = getattr(interaction, "data", None)
        kind = getattr(getattr(interaction, "type", None), "value", None)
        return kind == 3 and isinstance(data, dict) and data.get("custom_id") == CUSTOM_ID

    async def handle(self, interaction):
        message = getattr(interaction, "message", None)
        text = reply_text(claim_links(getattr(message, "components", None)))
        # Only the member who tapped sees it; no link preview, so the link is easy to long-press.
        await interaction.response.send_message(text, ephemeral=True, suppress_embeds=True)
