"""Read reviewer emails from the account holder's own mailbox over IMAP.

This is the primary source. It touches only the user's own mail provider, needs
no Walmart credentials, and works with two-factor authentication left switched
on (Gmail/Outlook app passwords are scoped, revocable, per-application secrets).

Polling is incremental. The poller runs every two minutes, and re-downloading
the same messages on every run would be roughly a gigabyte a day against
Gmail's IMAP bandwidth cap -- enough to get the mailbox throttled within days.
Instead the highest UID already read is stored as a marker and only newer
messages are fetched.
"""

from __future__ import annotations

import contextlib
import email
import imaplib
import logging
import os
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from email.header import decode_header, make_header
from email.message import Message

from models import Item
from state import SeenStore

from .parsing import extract_items

log = logging.getLogger(__name__)

DEFAULT_SENDERS = ("walmart.com", "bazaarvoice.com")

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_UIDVALIDITY_RE = re.compile(rb"UIDVALIDITY\s+(\d+)", re.I)


def _imap_date(when: datetime) -> str:
    """IMAP wants DD-Mon-YYYY with an English month, whatever the locale is."""
    return f"{when.day:02d}-{_MONTHS[when.month - 1]}-{when.year}"


class ImapSource:
    name = "email"

    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        mailbox: str = "INBOX",
        senders: tuple[str, ...] = DEFAULT_SENDERS,
        lookback: int = 25,
        port: int = 993,
        backfill_days: int = 1,
        store: SeenStore | None = None,
    ):
        if not (host and user and password):
            raise ValueError("IMAP_HOST, IMAP_USER and IMAP_PASSWORD are required")
        self.host, self.port = host, port
        self.user, self.password = user, password
        self.mailbox = mailbox
        self.senders = senders
        self.lookback = lookback
        self.backfill_days = backfill_days
        self.store = store
        # Highest UID read this run. Committed by the caller only once the items
        # have actually been delivered -- see commit_marker().
        self.pending_marker: str | None = None

    @classmethod
    def from_env(cls, store: SeenStore | None = None) -> ImapSource:
        senders = os.environ.get("IMAP_SENDERS", "")
        return cls(
            host=os.environ.get("IMAP_HOST", ""),
            user=os.environ.get("IMAP_USER", ""),
            password=os.environ.get("IMAP_PASSWORD", ""),
            mailbox=os.environ.get("IMAP_MAILBOX", "INBOX"),
            senders=tuple(s.strip() for s in senders.split(",") if s.strip()) or DEFAULT_SENDERS,
            lookback=int(os.environ.get("IMAP_LOOKBACK", "25")),
            port=int(os.environ.get("IMAP_PORT", "993")),
            backfill_days=int(os.environ.get("IMAP_BACKFILL_DAYS", "1")),
            store=store,
        )

    # --- marker -------------------------------------------------------------

    @property
    def marker_key(self) -> str:
        key = f"imap-{self.user}-{self.mailbox}"
        return re.sub(r"[/\\#?]", "-", key)[:512]

    def _read_marker(self) -> tuple[int, int]:
        """(uidvalidity, last_uid) from the store, or (0, 0) if there is none."""
        if self.store is None:
            return (0, 0)
        raw = self.store.get_marker(self.marker_key)
        if not raw or ":" not in raw:
            return (0, 0)
        validity, _, last = raw.partition(":")
        try:
            return (int(validity), int(last))
        except ValueError:
            return (0, 0)

    def commit_marker(self) -> None:
        """Advance the high-water mark. Call only after items were delivered.

        Committing before delivery would mean a transient ntfy outage silently
        swallows the mail that mentioned the item: the message is never read
        again, so the alert is simply lost.
        """
        if self.store is None or not self.pending_marker:
            return
        self.store.set_marker(self.marker_key, self.pending_marker)
        log.info("IMAP marker for %s advanced to %s", self.mailbox, self.pending_marker)

    # --- fetch --------------------------------------------------------------

    def fetch(self) -> Iterator[Item]:
        try:
            conn = imaplib.IMAP4_SSL(self.host, self.port)
        except OSError:
            log.exception("Could not reach IMAP server %s", self.host)
            return
        try:
            conn.login(self.user, self.password)
            status, _ = conn.select(self.mailbox, readonly=True)
            if status != "OK":
                log.error("Could not select mailbox %s", self.mailbox)
                return
            yield from self._messages_to_items(conn, self._uidvalidity(conn))
        except imaplib.IMAP4.error:
            log.exception("IMAP session failed for %s", self.user)
        finally:
            with contextlib.suppress(Exception):
                conn.logout()

    def _uidvalidity(self, conn: imaplib.IMAP4_SSL) -> int:
        """UIDs are only comparable within one UIDVALIDITY generation.

        A mailbox rebuild resets them, so the generation is stored alongside the
        marker and a change forces a fresh backfill window instead of trusting
        a UID that now means something else.
        """
        try:
            _, data = conn.response("UIDVALIDITY")
            for chunk in data or []:
                if not chunk:
                    continue
                raw = chunk if isinstance(chunk, bytes) else str(chunk).encode()
                match = _UIDVALIDITY_RE.search(raw)
                if match:
                    return int(match.group(1))
                if raw.isdigit():
                    return int(raw)
        except Exception:
            log.warning("Could not read UIDVALIDITY; treating mailbox history as reset.")
        return 0

    def _messages_to_items(self, conn: imaplib.IMAP4_SSL, uidvalidity: int) -> Iterator[Item]:
        stored_validity, last_uid = self._read_marker()
        if uidvalidity and stored_validity and uidvalidity != stored_validity:
            log.warning(
                "Mailbox UIDVALIDITY changed (%s -> %s); restarting from the backfill window.",
                stored_validity, uidvalidity,
            )
            last_uid = 0

        uids = self._search(conn, last_uid)
        if not uids:
            return

        highest = last_uid
        for uid in uids:
            msg = self._fetch_message(conn, uid)
            highest = max(highest, uid)
            if msg is None:
                continue
            yield from self._items_from_message(msg)

        if highest > last_uid:
            self.pending_marker = f"{uidvalidity}:{highest}"

    def _search(self, conn: imaplib.IMAP4_SSL, last_uid: int) -> list[int]:
        """UIDs newer than the marker across every configured sender, oldest first."""
        if last_uid:
            # `UID n:*` still returns the newest message when n is past the end
            # of the mailbox, so the result always needs filtering against
            # last_uid rather than being trusted as-is.
            window = ("UID", f"{last_uid + 1}:*")
        else:
            since = datetime.now(UTC) - timedelta(days=max(self.backfill_days, 0))
            window = ("SINCE", _imap_date(since))

        found: set[int] = set()
        for sender in self.senders:
            try:
                status, data = conn.uid("SEARCH", None, "FROM", f'"{sender}"', *window)
            except imaplib.IMAP4.error:
                log.exception("IMAP search failed for sender %s", sender)
                continue
            if status != "OK" or not data or not data[0]:
                continue
            for raw in data[0].split():
                try:
                    uid = int(raw)
                except ValueError:
                    continue
                if uid > last_uid:
                    found.add(uid)

        # Sort numerically. Concatenating per-sender results and slicing the
        # tail does not give you the newest messages, it gives you whichever
        # sender happened to be searched last.
        return sorted(found)[-self.lookback:]

    def _fetch_message(self, conn: imaplib.IMAP4_SSL, uid: int) -> Message | None:
        try:
            # PEEK so polling never marks the user's own mail as read.
            status, payload = conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
        except imaplib.IMAP4.error:
            log.exception("Could not fetch UID %s", uid)
            return None
        if status != "OK" or not payload or not isinstance(payload[0], tuple):
            return None
        try:
            return email.message_from_bytes(payload[0][1])
        except Exception:
            log.exception("Could not parse message UID %s", uid)
            return None

    def _items_from_message(self, msg: Message) -> Iterator[Item]:
        subject = _decode(msg.get("Subject", ""))
        body, is_html = _best_body(msg)
        if not body:
            return
        for item in extract_items(body, source=self.name, is_html=is_html):
            item.raw["subject"] = subject
            yield item


def _decode(raw: str) -> str:
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return raw


def _best_body(msg: Message) -> tuple[str, bool]:
    """Prefer the HTML part; fall back to plain text."""
    plain = ""
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_maintype() == "multipart":
            continue
        if part.get_filename():
            continue
        ctype = part.get_content_type()
        try:
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            continue
        if ctype == "text/html":
            return text, True
        if ctype == "text/plain" and not plain:
            plain = text
    return plain, False
