"""Request identity shared by HTTP intake and local/standalone delivery code."""

from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar

_REQUEST_ID = re.compile(r"[a-f0-9]{32}\Z")
_current: ContextVar[str | None] = ContextVar("notifier_http_request_id", default=None)


def valid_request_id(value: object) -> bool:
    return isinstance(value, str) and _REQUEST_ID.fullmatch(value) is not None


def current_request_id() -> str | None:
    return _current.get()


@contextmanager
def bind_request(request_id: str):
    if not valid_request_id(request_id):
        raise ValueError("invalid telemetry request identity")
    token = _current.set(request_id)
    try:
        yield
    finally:
        _current.reset(token)
