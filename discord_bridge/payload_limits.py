"""Discord message limits, checked before a POST instead of learned from a 413/400.

Text limits are counted in UTF-16 code units, which is never fewer than the
characters Discord counts, so a payload that passes here passes there.
"""

from __future__ import annotations

from .api import encode_payload

CONTENT = 2000
EMBEDS = 10
TITLE = 256
DESCRIPTION = 4096
FIELDS = 25
FIELD_NAME = 256
FIELD_VALUE = 1024
FOOTER = 2048
AUTHOR = 256
EMBED_TOTAL = 6000
ACTION_ROWS = 5
ROW_BUTTONS = 5
BUTTON_LABEL = 80
BUTTON_URL = 512
# Application ceiling, the same as the channel feed's core.MAX_MESSAGE_BYTES.
# Discord answered 413 (code 40005) for 10,888-12,803 byte DM bodies on
# 2026-11-18; no smaller body has been rejected.
BODY_BYTES = 10 * 1024


def text_length(value) -> int:
    if value is None:
        return 0
    return len(str(value).encode("utf-16-le", errors="surrogatepass")) // 2


def _mapping(value):
    return value if isinstance(value, dict) else {}


def payload_problems(payload, *, max_bytes=BODY_BYTES) -> list[str]:
    """Every Discord limit this payload breaks; an empty list means it can be sent."""
    if not isinstance(payload, dict):
        return ["payload"]
    problems = []
    if text_length(payload.get("content")) > CONTENT:
        problems.append("content")
    embeds = payload.get("embeds") or []
    if len(embeds) > EMBEDS:
        problems.append("embeds")
    total = 0
    for index, embed in enumerate(embeds):
        embed = _mapping(embed)
        checks = [
            ("title", embed.get("title"), TITLE),
            ("description", embed.get("description"), DESCRIPTION),
            ("footer", _mapping(embed.get("footer")).get("text"), FOOTER),
            ("author", _mapping(embed.get("author")).get("name"), AUTHOR),
        ]
        fields = embed.get("fields") or []
        if len(fields) > FIELDS:
            problems.append(f"embed {index} fields")
        for number, field in enumerate(fields):
            field = _mapping(field)
            checks.append((f"field {number} name", field.get("name"), FIELD_NAME))
            checks.append((f"field {number} value", field.get("value"), FIELD_VALUE))
        for name, value, limit in checks:
            size = text_length(value)
            total += size
            if size > limit:
                problems.append(f"embed {index} {name}")
    if total > EMBED_TOTAL:
        problems.append("embed total")
    rows = payload.get("components") or []
    if len(rows) > ACTION_ROWS:
        problems.append("action rows")
    for index, row in enumerate(rows):
        buttons = _mapping(row).get("components") or []
        if len(buttons) > ROW_BUTTONS:
            problems.append(f"row {index} buttons")
        for button in buttons:
            button = _mapping(button)
            if text_length(button.get("label")) > BUTTON_LABEL:
                problems.append(f"row {index} label")
            if text_length(button.get("url")) > BUTTON_URL:
                problems.append(f"row {index} url")
    try:
        size = len(encode_payload(payload))
    except (TypeError, ValueError):
        problems.append("encoding")
    else:
        if size > max_bytes:
            problems.append("body bytes")
    return problems
