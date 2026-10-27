"""Small shared contracts; no browser content or arbitrary desktop commands."""

from dataclasses import dataclass

HOST_NAME = "com.reviewer_item_relay.verification"
PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 16_384
MAX_HOLD_MS = 30_000
MIN_IDLE_MS = 5_000
PREPARE_TTL_MS = 2_000

ERROR_CODES = frozenset({
    "unsupported-platform", "invalid-request", "invalid-origin", "invalid-window",
    "helper-busy", "desktop-locked", "user-busy", "button-held", "focus-changed",
    "window-changed", "viewport-mismatch", "target-obscured", "target-expired",
    "user-input", "input-failed", "release-failed", "watchdog-unavailable",
    "already-used", "not-prepared", "host-unavailable", "max-duration", "closed",
})


class DesktopError(Exception):
    def __init__(self, code: str):
        self.code = code if code in ERROR_CODES else "host-unavailable"
        super().__init__(self.code)


@dataclass(frozen=True)
class Target:
    window: int
    widget: int
    x: int
    y: int
    bounds: tuple[int, int, int, int]
    input_stamp: int
