"""Keep a "Free" role on every member without Pro so the owner can @ them.

A reconciler rather than event hooks: Pro arrives through Whop's own bot, the
Get Pro worker and trials, so diffing member roles covers every path at once.
The role has zero permissions and stays non-mentionable; the server owner can
still mention it.
"""

from __future__ import annotations

import threading

from .api import DiscordError, retry_local_contention
from .monitoring import emit_event, error_fields

SCAN_SECONDS = 60
MAX_CHANGES_PER_SCAN = 50  # backfill spreads over scans; raise if member count explodes


def plan_changes(members, *, free_role, paid_roles):
    """Return (add, remove) member IDs for one full member listing."""
    add, remove = [], []
    for row in members:
        if row.get("user", {}).get("bot"):
            continue
        roles = set(row.get("roles", ()))
        paid = bool(roles & paid_roles)
        if not paid and free_role not in roles:
            add.append(row["user"]["id"])
        elif paid and free_role in roles:
            remove.append(row["user"]["id"])
    return add, remove


class FreeRoleSync:
    def __init__(self, client, *, guild_id, free_role, paid_roles, event_sink=None):
        self.client, self.guild, self.role = client, guild_id, free_role
        self.paid = {r for r in paid_roles if r}
        self.event_sink = event_sink
        self._stop = threading.Event()
        self._thread = None
        self.last_error = None

    def _members(self):
        rows, after = [], None
        while True:
            page = retry_local_contention(
                self.client.guild_members, self.guild, limit=1000, after=after,
                stop_event=self._stop,
            )
            rows.extend(page)
            if len(page) < 1000:
                return rows
            after = page[-1]["user"]["id"]

    def scan(self):
        add, remove = plan_changes(self._members(), free_role=self.role, paid_roles=self.paid)
        changes = [("DELETE", m) for m in remove] + [("PUT", m) for m in add]
        for method, member in changes[:MAX_CHANGES_PER_SCAN]:
            if self._stop.is_set():
                break
            path = f"/guilds/{self.guild}/members/{member}/roles/{self.role}"
            while not self._stop.is_set():
                try:
                    self.client.request(method, path)
                    break
                except DiscordError as exc:
                    status, wait = getattr(exc, "status", None), getattr(exc, "retry_after", None)
                    if status == 404:  # member left mid-scan
                        break
                    # Role writes share a small per-route bucket; wait out short cooldowns.
                    if status == 429 and type(wait) in (int, float) and 0 <= wait <= 30:
                        self._stop.wait(wait + 0.1)
                        continue
                    raise
        return len(add), len(remove)

    def start(self):
        if self._thread and self._thread.is_alive():
            return

        def run():
            while not self._stop.is_set():
                try:
                    self.scan()
                    self.last_error = None
                except Exception as exc:  # never crash the bot
                    self.last_error = type(exc).__name__
                    emit_event(self.event_sink, "free_role_error", component="free-role",
                               **error_fields(exc))
                self._stop.wait(SCAN_SECONDS)

        self._thread = threading.Thread(target=run, name="free-role", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def is_alive(self):
        return bool(self._thread and self._thread.is_alive())
