"""Read reviewer emails from the account holder's own mailbox over IMAP.

This is the primary source. It touches only the user's own mail provider, needs
no Walmart credentials, and works with two-factor authentication left switched
on (Gmail/Outlook app passwords are scoped, revocable, per-application secrets).
"""

from __future__ import annotations

import email
import imaplib
import logging
import os
from email.header import decode_header, make_header
from email.message import Message
from typing import Iterator

from models import Item
from .parsing import extract_items

log = logging.getLogger(__name__)

DEFAULT_SENDERS = ("walmart.com", "bazaarvoice.com")


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
    ):
        if not (host and user and password):
            raise ValueError("IMAP_HOST, IMAP_USER and IMAP_PASSWORD are required")
        self.host, self.port = host, port
        self.user, self.password = user, password
        self.mailbox = mailbox
        self.senders = senders
        self.lookback = lookback

    @classmethod
    def from_env(cls) -> "ImapSource":
        senders = os.environ.get("IMAP_SENDERS", "")
        return cls(
            host=os.environ.get("IMAP_HOST", ""),
            user=os.environ.get("IMAP_USER", ""),
            password=os.environ.get("IMAP_PASSWORD", ""),
            mailbox=os.environ.get("IMAP_MAILBOX", "INBOX"),
            senders=tuple(s.strip() for s in senders.split(",") if s.strip()) or DEFAULT_SENDERS,
            lookback=int(os.environ.get("IMAP_LOOKBACK", "25")),
            port=int(os.environ.get("IMAP_PORT", "993")),
        )

    def fetch(self) -> Iterator[Item]:
        try:
            conn = imaplib.IMAP4_SSL(self.host, self.port)
        except OSError:
            log.exception("Could not reach IMAP server %s", self.host)
            return
        try:
            conn.login(self.user, self.password)
            conn.select(self.mailbox, readonly=True)
            for msg in self._recent_messages(conn):
                yield from self._items_from_message(msg)
        except imaplib.IMAP4.error:
            log.exception("IMAP session failed for %s", self.user)
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    def _recent_messages(self, conn: imaplib.IMAP4_SSL) -> Iterator[Message]:
        ids: list[bytes] = []
        for sender in self.senders:
            status, data = conn.search(None, "FROM", f'"{sender}"')
            if status == "OK" and data and data[0]:
                ids.extend(data[0].split())
        # Newest last in IMAP sequence order; take the tail and de-duplicate.
        for msg_id in list(dict.fromkeys(ids))[-self.lookback :]:
            status, payload = conn.fetch(msg_id, "(RFC822)")
            if status != "OK" or not payload or not isinstance(payload[0], tuple):
                continue
            yield email.message_from_bytes(payload[0][1])

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
