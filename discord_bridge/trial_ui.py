"""Private, expiring multi-member trial review and confirmation controls."""

import asyncio
import math
import re
import uuid

import discord


def _id(value):
    return (
        isinstance(value, str) and re.fullmatch(r"[0-9]{17,20}", value) and 0 < int(value) < 1 << 64
    )


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


class TrialBatchView(discord.ui.View):
    """One owner reviews up to 25 members before any access changes are made."""

    def __init__(self, handler, *, actor, intent, operation, seconds=None, until=None):
        super().__init__(timeout=180)
        if (
            not _id(actor)
            or not isinstance(intent, str)
            or not re.fullmatch(r"[A-Za-z0-9:_-]{1,100}", intent)
            or operation not in {"grant", "extend", "revoke"}
        ):
            raise ValueError("Invalid trial batch identity or operation.")
        if operation == "grant":
            valid = (seconds is None) != (until is None)
        elif operation == "extend":
            valid = seconds is not None and until is None
        else:
            valid = seconds is None and until is None
        if (
            not valid
            or (seconds is not None and (not _number(seconds) or seconds > 366 * 86400))
            or (until is not None and not _number(until))
        ):
            raise ValueError("Invalid trial batch duration or expiry.")
        self.handler, self.actor, self.intent = handler, actor, intent
        self.operation, self.seconds, self.until = operation, seconds, until
        self.nonce = uuid.uuid4().hex
        self.message = None
        self.selected = ()
        self.state = "review"
        self._result_content = None
        self._grant_expiry = None
        self._lock = asyncio.Lock()
        self.select = discord.ui.UserSelect(
            placeholder="Choose members (up to 25)",
            min_values=1,
            max_values=25,
            custom_id=f"trial-ui:{self.nonce}:select",
            row=0,
        )
        self.confirm_button = discord.ui.Button(
            label="Confirm",
            style=discord.ButtonStyle.primary,
            disabled=True,
            custom_id=f"trial-ui:{self.nonce}:confirm",
            row=1,
        )
        self.cancel_button = discord.ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.secondary,
            custom_id=f"trial-ui:{self.nonce}:cancel",
            row=1,
        )
        self.select.callback = self.choose
        self.confirm_button.callback = self.confirm
        self.cancel_button.callback = self.cancel
        for control in (self.select, self.confirm_button, self.cancel_button):
            self.add_item(control)

    def content(self):
        if self._result_content is not None:
            return self._result_content
        label = {"grant": "Grant", "extend": "Extend", "revoke": "Revoke"}[self.operation]
        lines = [f"**{label} Pro trials**", "Choose up to 25 members, then review and Confirm."]
        if self.until is not None:
            lines.append(f"Expiry: <t:{int(self.until)}:F>.")
        elif self.seconds is not None:
            hours = self.seconds / 3600
            duration = f"{hours / 24:g} days" if hours % 24 == 0 else f"{hours:g} hours"
            suffix = " from confirmation" if self.operation == "grant" else " added to each trial"
            lines.append(f"Duration: {duration}{suffix}.")
        if self.selected:
            lines.append(
                f"Selected ({len(self.selected)}): "
                + ", ".join(f"<@{user}>" for user in self.selected)
            )
        else:
            lines.append("No members selected yet.")
        lines.append("Access changes only after Confirm. Paid Pro access is preserved.")
        return "\n".join(lines)

    async def _authorize(self, interaction):
        actor = await self.handler.authorize(interaction, expected_actor=self.actor, thinking=False)
        return actor == self.actor

    async def _edit(self, interaction, content):
        try:
            await interaction.edit_original_response(
                content=content, view=self, allowed_mentions=discord.AllowedMentions.none()
            )
        except Exception as exc:
            self.handler._failure(exc)
            return False
        return True

    def _finish(self, state, content=None):
        self.state = state
        self._result_content = content
        for child in self.children:
            child.disabled = True
        self.stop()

    async def _closed(self, interaction):
        await self.handler._reply(interaction, "These controls have finished. Run /trial again.")

    def _selection(self, interaction, values):
        raw = (interaction.data or {}).get("values")
        if (
            (interaction.data or {}).get("custom_id") != self.select.custom_id
            or not isinstance(raw, list)
            or not 1 <= len(raw) <= 25
            or not 1 <= len(values) <= 25
            or not all(_id(user) for user in raw)
        ):
            raise ValueError("Select between 1 and 25 server members.")
        ids = []
        for value in values:
            if (
                not isinstance(value, (discord.Member, discord.User))
                or not _id(str(value.id))
                or value.bot
                or (isinstance(value, discord.Member) and value.guild.id != interaction.guild_id)
            ):
                raise ValueError("Select human members from this server.")
            ids.append(str(value.id))
        if set(ids) != set(raw):
            raise ValueError("The member selection could not be verified. Select members again.")
        return tuple(dict.fromkeys(ids))

    async def choose(self, interaction):
        # The SDK's task-local resolved values can change while awaiting ownership.
        values = tuple(self.select.values)
        if not await self._authorize(interaction):
            return
        async with self._lock:
            if self.state != "review" or self.is_finished():
                await self._closed(interaction)
                return
            try:
                selected = self._selection(interaction, values)
            except ValueError as exc:
                await self.handler._reply(interaction, str(exc))
                return
            self.selected = selected
            self.confirm_button.disabled = False
            if not await self._edit(interaction, self.content()):
                self._finish(
                    "complete", "The member review could not be updated. Run /trial again."
                )
                await self.handler._reply(interaction, self.content())

    async def confirm(self, interaction):
        selected = self.selected
        if not await self._authorize(interaction):
            return
        async with self._lock:
            if self.state != "review" or self.is_finished():
                await self._closed(interaction)
                return
            if selected != self.selected:
                await self.handler._reply(
                    interaction, "The selection changed. Review the members, then Confirm again."
                )
                return
            if not self.selected:
                await self.handler._reply(interaction, "Choose at least one member first.")
                return
            self._finish("submitting")
            if not await self._edit(interaction, "Applying the selected trial changes…"):
                self.state = "complete"
                await self.handler._reply(
                    interaction,
                    "The trial review could not be updated. No access changes were made. "
                    "Run /trial again.",
                )
                return
            try:
                if self.operation == "grant":
                    self._grant_expiry = self.until
                    if self._grant_expiry is None:
                        self._grant_expiry = float(self.handler.service.clock()) + self.seconds
                    if not _number(self._grant_expiry):
                        raise ValueError("Invalid trial expiry.")
                results = []
                for user in self.selected:
                    result = await asyncio.to_thread(self._apply, user)
                    results.append((user, *result))
                self._result_content = self._results(results)
            except Exception as exc:
                self.handler._failure(exc)
                self._result_content = "Trial results could not be confirmed. Check /trial list."
            self.state = "complete"
            if not await self._edit(interaction, self._result_content):
                await self.handler._reply(interaction, self._result_content)

    def _apply(self, user):
        kwargs = {
            "actor_id": self.actor,
            "interaction_id": f"{self.intent}:{self.operation}:{user}",
        }
        try:
            if self.operation == "grant":
                row = self.handler.service.grant(user, until=self._grant_expiry, **kwargs)
            elif self.operation == "extend":
                row = self.handler.service.extend(user, seconds=self.seconds, **kwargs)
            else:
                row = self.handler.service.revoke(user, **kwargs)
        except ValueError:
            return "not applied", None
        except Exception as exc:
            self.handler._failure(exc)
            return "check /trial list", None
        try:
            if not isinstance(row, dict) or row.get("user_id") != user:
                raise ValueError("Invalid trial result identity.")
            expiry = row.get("expires_at")
            if self.operation != "revoke" and (not _number(expiry) or expiry > 253402300799):
                raise ValueError("Invalid trial result expiry.")
            return ("confirmed" if row.get("synced") is True else "pending", expiry)
        except Exception as exc:
            self.handler._failure(exc)
            return "check /trial list", None

    def _results(self, results):
        counts = {
            status: sum(row[1] == status for row in results)
            for status in ("confirmed", "pending", "not applied", "check /trial list")
        }
        lines = [
            f"**Trial {self.operation} results**",
            f"{counts['confirmed']} confirmed · {counts['pending']} sync pending · "
            f"{counts['not applied']} not applied · "
            f"{counts['check /trial list']} need a status check",
        ]
        if self.operation == "grant":
            lines.append(f"Shared expiry: <t:{int(self._grant_expiry)}:F>.")
        for user, status, expiry in results:
            timestamp = (
                f" · <t:{int(expiry)}:f>"
                if self.operation == "extend" and expiry is not None
                else ""
            )
            label = "sync pending" if status == "pending" else status
            lines.append(f"<@{user}>{timestamp} · {label}")
        lines.append("Paid Pro access is preserved.")
        content = "\n".join(lines)
        if self.operation == "grant" and counts["confirmed"]:
            copy = (
                "\n\n**Optional copy for confirmed members:**\n"
                f"Your Pro trial is active until <t:{int(self._grant_expiry)}:F>. "
                "Enjoy the Pro feeds and /request. No signup or charge."
            )
            if len(content) + len(copy) <= 2000:
                content += copy
        return content

    async def cancel(self, interaction):
        if not await self._authorize(interaction):
            return
        async with self._lock:
            if self.state != "review" or self.is_finished():
                await self._closed(interaction)
                return
            self._finish("cancelled", "Trial batch cancelled. No access changes were made.")
            if not await self._edit(interaction, self.content()):
                await self.handler._reply(interaction, self.content())

    async def on_timeout(self):
        async with self._lock:
            if self.state != "review":
                return
            self._finish(
                "expired", "These trial controls expired. Run /trial for a fresh selection."
            )
            if self.message is not None:
                try:
                    await self.message.edit(
                        content=self.content(),
                        view=self,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except Exception as exc:
                    self.handler._failure(exc)

    async def on_error(self, interaction, error, _item):
        self.handler._failure(error)
        if await self._authorize(interaction):
            await self.handler._reply(
                interaction, "Trial controls are temporarily unavailable. Run /trial again."
            )
