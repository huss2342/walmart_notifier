"""Small shared contracts; no browser content or arbitrary desktop commands."""

import json
from dataclasses import dataclass
from pathlib import Path

PROTOCOL = json.loads(Path(__file__).with_name("protocol.json").read_text(encoding="utf-8"))
HOST_NAME = PROTOCOL["hostName"]
EXTENSION_ORIGIN = PROTOCOL["extensionOrigin"]
PROTOCOL_VERSION = PROTOCOL["version"]
MAX_MESSAGE_BYTES = PROTOCOL["limits"]["messageBytes"]
MAX_HOLD_MS = PROTOCOL["limits"]["holdMs"]
MIN_IDLE_MS = PROTOCOL["limits"]["idleMs"]
PREPARE_TTL_MS = PROTOCOL["limits"]["prepareTtlMs"]
MAX_VIEWPORT = PROTOCOL["limits"]["viewportMax"]
MIN_DPR = PROTOCOL["limits"]["dprMin"]
MAX_DPR = PROTOCOL["limits"]["dprMax"]
ERROR_CODES = frozenset(PROTOCOL["errorCodes"])
OPS = frozenset(PROTOCOL["operations"])
REQUEST_ID_PATTERN = PROTOCOL["requestIdPattern"]
PREPARE_TOKEN_PATTERN = PROTOCOL["prepareTokenPattern"]


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
