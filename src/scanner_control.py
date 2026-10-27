"""Strict local scanner control and safe health snapshots, separate from items.

Every read consults the current file. Saves validate before an atomic sibling
replacement; a failed write never acknowledges or caches an unpersisted value.
No defaults are seeded here, so an existing browser remains authoritative until
the owner explicitly saves its scanner preferences.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
import unicodedata
from copy import deepcopy
from pathlib import Path

log = logging.getLogger(__name__)

MAX_SETTINGS_BYTES = 16 * 1024
MAX_COMMANDS_BYTES = 16 * 1024
MAX_STATUS_BYTES = 64 * 1024
MAX_NUMBER = 10**15
STALE_MS = 120_000
DEFAULT_DIRECTORY = Path(__file__).resolve().parent.parent / "data"
INVALID_SETTINGS = "Invalid scanner settings"
INVALID_COMMANDS = "Invalid scanner commands"
INVALID_STATUS = "Invalid scanner status"
INVALID_JSON = "Invalid scanner control payload"

_SETTINGS_KEYS = frozenset(
    {
        "schemaVersion",
        "auxiliaryDepartments",
        "auxiliaryKeywords",
        "auxiliaryBrands",
        "auxiliaryKeywordQueries",
        "groupScannerTabs",
        "rotateScannerTabs",
        "verificationAssistEnabled",
        "verificationNativeEnabled",
        "pathPattern",
    }
)
_COMMAND_KEYS = frozenset({"schemaVersion", "restartHomeId", "restartAdditionalId"})
_STATUS_KEYS = frozenset(
    {
        "schemaVersion",
        "extensionVersion",
        "exportedAt",
        "settings",
        "primary",
        "lanes",
        "verification",
        "sync",
        "commands",
        "errors",
    }
)
_PRIMARY_KEYS = frozenset(
    {
        "tabId",
        "generation",
        "phase",
        "reachable",
        "page",
        "totalPages",
        "visitedPages",
        "lastActivityAt",
        "nextActionAt",
    }
)
_LANE_KEYS = frozenset(
    {
        "lane",
        "tabId",
        "generation",
        "status",
        "targetLabel",
        "completedTargets",
        "totalTargets",
        "pages",
        "failures",
        "truncatedTargets",
        "coverageLimitedTargets",
        "nextAt",
        "round",
        "lastErrorCode",
        "owned",
        "enabled",
    }
)
_RUNTIME_KEYS = frozenset(
    {
        "phase",
        "reachable",
        "active",
        "busy",
        "visibility",
        "focusReason",
        "lastActivityAt",
        "nextActionAt",
    }
)
_FOCUS_REASONS = frozenset(
    {
        "unknown",
        "none",
        "busy",
        "waiting-turn",
        "manual-tab-active",
        "window-unfocused",
        "user-active",
        "not-authorized",
        "expired",
        "paused",
        "disabled",
        "unavailable",
        "short-lease",
        "hidden",
        "cancelled",
        "revoked",
        "selection-changed",
        "not-scanner",
        "activation-unavailable",
    }
)
_VERIFICATION_KEYS = frozenset(
    {
        "pausedUntil",
        "count",
        "lastAt",
        "assistEnabled",
        "nativeEnabled",
        "statusCode",
        "attempt",
        "attemptLimit",
        "lastOutcome",
        "errorStage",
        "errorReason",
        "pressSent",
        "cleanupConfirmed",
        "retryAt",
    }
)
_VERIFICATION_OPTIONAL_KEYS = frozenset({"tabActive", "tabFrozen", "windowFocused"})
PRIMARY_PHASES = frozenset(
    {
        "unknown",
        "idle",
        "ready",
        "unreachable",
        "sweeping",
        "scanning",
        "passive",
        "starting",
        "collecting",
        "ingesting",
        "ingest-retry",
        "relay-retry",
        "complete",
        "empty-timeout",
        "extension-reloaded",
        "manual-browsing",
        "navigating",
        "recovering",
        "transient-end-retry",
        "waiting-content",
        "waiting-navigation",
        "waiting-page-change",
        "waiting-target-change",
        "waiting-user",
        "waiting-verification",
        "waiting-foreground",
        "waiting-selection",
        "waiting-focus",
        "reading-page",
    }
)
LANE_STATUSES = frozenset(
    {
        "idle",
        "ready",
        "starting",
        "waiting",
        "scanning",
        "paused",
        "complete",
        "partial",
        "disabled",
        "unreachable",
        "error",
    }
)
_COMPONENTS = frozenset(
    {
        "settings",
        "primary",
        "departments",
        "keywords",
        "verification",
        "commands",
    }
)
_OUTCOMES = frozenset({"started", "complete", "failed", "interrupted", "paused"})
_LAST_ERRORS = frozenset({"none", "target-error", "status-unavailable"})
_FAILURE_CODES = frozenset(
    {
        "none",
        "missing-document",
        "unsupported-kind",
        "ambiguous-filter-section",
        "missing-filter-section",
        "ambiguous-control",
        "missing-control",
        "missing-panel",
        "ambiguous-expansion",
        "missing-expansion",
        "expansion-stalled",
        "expansion-overflow",
        "option-overflow",
        "missing-selection",
        "ambiguous-selection",
        "already-selected",
        "cancelled",
        "facet-error",
        "controls-unavailable",
        "discovery-save-failed",
        "content-unresponsive",
        "progress-timeout",
        "unexpected-error",
        "selection-render-timeout",
        "selection-document-timeout",
        "selection-bind-failed",
        "selection-unconfirmed",
    }
)
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,64}")
_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+)*")
_CODE = re.compile(r"[a-z-]+")
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")


def _object(
    value: object,
    keys: frozenset[str],
    error: str,
    *,
    optional: frozenset[str] = frozenset(),
) -> dict:
    if type(value) is not dict or not keys <= value.keys() or not value.keys() <= keys | optional:
        raise ValueError(error)
    return value


def _integer(value: object, error: str, maximum: int = MAX_NUMBER) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(error)
    return value


def _boolean(value: object, error: str) -> bool:
    if type(value) is not bool:
        raise ValueError(error)
    return value


def _text(value: object, maximum: int, error: str, *, empty: bool = False) -> str:
    if (
        type(value) is not str
        or len(value) > maximum
        or (not empty and not value.strip())
        or any(unicodedata.category(char) in {"Cc", "Cs"} for char in value)
    ):
        raise ValueError(error)
    return value


def _choice(value: object, choices: frozenset[str], error: str) -> str:
    if type(value) is not str or value not in choices:
        raise ValueError(error)
    return value


def _schema(value: dict, error: str) -> None:
    if type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1:
        raise ValueError(error)


def _encode(value: dict, maximum: int, error: str) -> bytes:
    encoded = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode(
        "utf-8",
    )
    if len(encoded) > maximum:
        raise ValueError(error)
    return encoded


def _unique_object(pairs: list[tuple]) -> dict:
    value = {}
    for key, entry in pairs:
        if key in value:
            raise ValueError(INVALID_JSON)
        value[key] = entry
    return value


def _invalid_constant(value: str) -> None:
    raise ValueError(INVALID_JSON)


def parse_json(encoded: bytes | str) -> object:
    """Reject duplicate keys, non-JSON numbers and decoder payload disclosure."""
    try:
        text = encoded.decode("utf-8-sig") if isinstance(encoded, bytes) else encoded
        return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise ValueError(INVALID_JSON) from None


def validate_settings(value: object) -> dict:
    data = _object(value, _SETTINGS_KEYS, INVALID_SETTINGS)
    _schema(data, INVALID_SETTINGS)
    clean = {"schemaVersion": 1}
    for key in _SETTINGS_KEYS - {"schemaVersion", "auxiliaryKeywordQueries", "pathPattern"}:
        clean[key] = _boolean(data[key], INVALID_SETTINGS)
    incoming = data["auxiliaryKeywordQueries"]
    clean["auxiliaryKeywordQueries"] = None
    if incoming is not None:
        if type(incoming) is not list or len(incoming) > 100:
            raise ValueError(INVALID_SETTINGS)
        queries = []
        seen = set()
        for value in incoming:
            if type(value) is not str:
                raise ValueError(INVALID_SETTINGS)
            query = _text(value.strip(), 80, INVALID_SETTINGS)
            folded = query.casefold()
            if folded in seen:
                raise ValueError(INVALID_SETTINGS)
            seen.add(folded)
            queries.append(query)
        clean["auxiliaryKeywordQueries"] = queries
    clean["pathPattern"] = _text(data["pathPattern"], 200, INVALID_SETTINGS)
    _encode(clean, MAX_SETTINGS_BYTES, INVALID_SETTINGS)
    return clean


def _identifier(value: object, error: str) -> str:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(error)
    return value


def validate_commands(value: object) -> dict:
    data = _object(value, _COMMAND_KEYS, INVALID_COMMANDS)
    _schema(data, INVALID_COMMANDS)
    clean = {"schemaVersion": 1}
    for key in ("restartHomeId", "restartAdditionalId"):
        clean[key] = None if data[key] is None else _identifier(data[key], INVALID_COMMANDS)
    _encode(clean, MAX_COMMANDS_BYTES, INVALID_COMMANDS)
    return clean


def _code(value: object, maximum: int = 64, *, empty: bool = False) -> str:
    text = _text(value, maximum, INVALID_STATUS, empty=empty)
    if text and _CODE.fullmatch(text) is None:
        raise ValueError(INVALID_STATUS)
    return text


def _nullable_integer(value: object) -> int | None:
    return None if value is None else _integer(value, INVALID_STATUS)


def validate_status(value: object) -> dict:
    data = _object(value, _STATUS_KEYS, INVALID_STATUS)
    _schema(data, INVALID_STATUS)
    version = _text(data["extensionVersion"], 64, INVALID_STATUS)
    if version != "unknown" and _VERSION.fullmatch(version) is None:
        raise ValueError(INVALID_STATUS)
    try:
        settings = validate_settings(data["settings"])
    except ValueError:
        raise ValueError(INVALID_STATUS) from None
    primary = _object(data["primary"], _PRIMARY_KEYS, INVALID_STATUS)
    clean_primary = {
        key: _integer(primary[key], INVALID_STATUS)
        for key in _PRIMARY_KEYS - {"tabId", "totalPages", "phase", "reachable"}
    }
    clean_primary.update(
        {
            "tabId": _nullable_integer(primary["tabId"]),
            "totalPages": _nullable_integer(primary["totalPages"]),
            "phase": _choice(primary["phase"], PRIMARY_PHASES, INVALID_STATUS),
            "reachable": _boolean(primary["reachable"], INVALID_STATUS),
        }
    )
    lanes = data["lanes"]
    if type(lanes) is not list or len(lanes) > 2:
        raise ValueError(INVALID_STATUS)
    clean_lanes = []
    seen = set()
    for value in lanes:
        lane = _object(
            value, _LANE_KEYS, INVALID_STATUS, optional=frozenset({"lastFailureCode", "runtime"})
        )
        name = _choice(lane["lane"], frozenset({"departments", "keywords"}), INVALID_STATUS)
        if name in seen:
            raise ValueError(INVALID_STATUS)
        seen.add(name)
        label = _text(lane["targetLabel"], 150, INVALID_STATUS, empty=True)
        if _URL.search(label):
            raise ValueError(INVALID_STATUS)
        clean = {
            key: _integer(lane[key], INVALID_STATUS)
            for key in _LANE_KEYS
            - {"lane", "tabId", "status", "targetLabel", "lastErrorCode", "owned", "enabled"}
        }
        clean.update(
            {
                "lane": name,
                "tabId": _nullable_integer(lane["tabId"]),
                "status": _choice(lane["status"], LANE_STATUSES, INVALID_STATUS),
                "targetLabel": label,
                "owned": _boolean(lane["owned"], INVALID_STATUS),
                "enabled": _boolean(lane["enabled"], INVALID_STATUS),
                "lastErrorCode": _choice(lane["lastErrorCode"], _LAST_ERRORS, INVALID_STATUS),
            }
        )
        if "lastFailureCode" in lane:
            clean["lastFailureCode"] = _choice(
                lane["lastFailureCode"], _FAILURE_CODES, INVALID_STATUS
            )
        if "runtime" in lane:
            runtime = _object(lane["runtime"], _RUNTIME_KEYS, INVALID_STATUS)
            clean["runtime"] = {
                "phase": _choice(runtime["phase"], PRIMARY_PHASES, INVALID_STATUS),
                "visibility": _choice(
                    runtime["visibility"],
                    frozenset({"visible", "hidden", "unknown"}),
                    INVALID_STATUS,
                ),
                "focusReason": _choice(runtime["focusReason"], _FOCUS_REASONS, INVALID_STATUS),
                **{
                    key: _boolean(runtime[key], INVALID_STATUS)
                    for key in ("reachable", "active", "busy")
                },
                **{
                    key: _integer(runtime[key], INVALID_STATUS)
                    for key in ("lastActivityAt", "nextActionAt")
                },
            }
        clean_lanes.append(clean)
    verification = _object(
        data["verification"],
        _VERIFICATION_KEYS,
        INVALID_STATUS,
        optional=_VERIFICATION_OPTIONAL_KEYS,
    )
    clean_verification = {
        key: _integer(verification[key], INVALID_STATUS)
        for key in ("pausedUntil", "count", "lastAt", "retryAt")
    }
    clean_verification.update(
        {
            "assistEnabled": _boolean(verification["assistEnabled"], INVALID_STATUS),
            "nativeEnabled": _boolean(verification["nativeEnabled"], INVALID_STATUS),
            "statusCode": _code(verification["statusCode"]),
            "attempt": _integer(verification["attempt"], INVALID_STATUS, 10),
            "attemptLimit": _integer(verification["attemptLimit"], INVALID_STATUS, 10),
            "lastOutcome": _code(verification["lastOutcome"], empty=True),
            "errorStage": _code(verification["errorStage"], 32, empty=True),
            "errorReason": _code(verification["errorReason"], empty=True),
            **{
                key: None
                if verification[key] is None
                else _boolean(verification[key], INVALID_STATUS)
                for key in ("pressSent", "cleanupConfirmed")
            },
        }
    )
    for key in _VERIFICATION_OPTIONAL_KEYS & verification.keys():
        clean_verification[key] = (
            None if verification[key] is None else _boolean(verification[key], INVALID_STATUS)
        )
    if clean_verification["attemptLimit"] != 10:
        raise ValueError(INVALID_STATUS)
    sync = _object(
        data["sync"], frozenset({"settingsOk", "settingsSyncedAt", "commandsOk"}), INVALID_STATUS
    )
    command_status = _object(
        data["commands"], frozenset({"restartHome", "restartAdditional"}), INVALID_STATUS
    )
    clean_commands = {}
    for key, value in command_status.items():
        clean_commands[key] = None
        if value is not None:
            command = _object(value, frozenset({"id", "outcome", "at"}), INVALID_STATUS)
            clean_commands[key] = {
                "id": _identifier(command["id"], INVALID_STATUS),
                "outcome": _choice(command["outcome"], _OUTCOMES, INVALID_STATUS),
                "at": _integer(command["at"], INVALID_STATUS),
            }
    errors = data["errors"]
    if type(errors) is not list or len(errors) > 8:
        raise ValueError(INVALID_STATUS)
    clean_errors = []
    for value in errors:
        error = _object(value, frozenset({"component", "code"}), INVALID_STATUS)
        clean_errors.append(
            {
                "component": _choice(error["component"], _COMPONENTS, INVALID_STATUS),
                "code": _code(error["code"]),
            }
        )
    report = {
        "schemaVersion": 1,
        "extensionVersion": version,
        "exportedAt": _integer(data["exportedAt"], INVALID_STATUS),
        "settings": settings,
        "primary": clean_primary,
        "lanes": clean_lanes,
        "verification": clean_verification,
        "sync": {
            "settingsOk": _boolean(sync["settingsOk"], INVALID_STATUS),
            "settingsSyncedAt": _integer(sync["settingsSyncedAt"], INVALID_STATUS),
            "commandsOk": _boolean(sync["commandsOk"], INVALID_STATUS),
        },
        "commands": clean_commands,
        "errors": clean_errors,
    }
    _encode(report, MAX_STATUS_BYTES, INVALID_STATUS)
    return report


def _status_snapshot(value: object) -> dict:
    data = _object(value, frozenset({"receivedAt", "report"}), INVALID_STATUS)
    return {
        "receivedAt": _integer(data["receivedAt"], INVALID_STATUS),
        "report": validate_status(data["report"]),
    }


class _AtomicJsonStore:
    """Small current-file store shared by scanner settings and health models."""

    filename: str
    maximum: int
    error: str

    def __init__(self, path: Path | str | None = None):
        self.path = (
            None
            if path in ("", ":memory:")
            else Path(path)
            if path is not None
            else DEFAULT_DIRECTORY / self.filename
        )
        self._lock = threading.RLock()
        self._value: dict | None = None

    def validate(self, value: object) -> dict:
        raise NotImplementedError

    def read(self) -> dict | None:
        with self._lock:
            if self.path is None:
                return deepcopy(self._value)
            try:
                with self.path.open("rb") as handle:
                    encoded = handle.read(self.maximum + 1)
            except FileNotFoundError:
                return None
            if len(encoded) > self.maximum:
                raise ValueError(self.error)
            try:
                clean = self.validate(parse_json(encoded))
            except (TypeError, ValueError, UnicodeError, RecursionError):
                raise ValueError(self.error) from None
            self._value = clean
            return deepcopy(clean)

    def save(self, value: object) -> dict:
        clean = self.validate(value)
        encoded = _encode(clean, self.maximum, self.error)
        with self._lock:
            if self.path is not None:
                self._write(encoded)
            self._value = clean
            return deepcopy(clean)

    def _write(self, encoded: bytes) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                mode="wb",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    log.warning("Scanner control temporary file cleanup unavailable")


class ScannerSettingsStore(_AtomicJsonStore):
    filename = "scanner-settings.json"
    maximum = MAX_SETTINGS_BYTES
    error = INVALID_SETTINGS
    validate = staticmethod(validate_settings)


class ScannerCommandsStore(_AtomicJsonStore):
    filename = "scanner-commands.json"
    maximum = MAX_COMMANDS_BYTES
    error = INVALID_COMMANDS
    validate = staticmethod(validate_commands)


class ScannerStatusStore(_AtomicJsonStore):
    filename = "scanner-status.json"
    maximum = MAX_STATUS_BYTES
    error = INVALID_STATUS
    validate = staticmethod(_status_snapshot)

    def record_report(self, value: object) -> None:
        report = validate_status(value)
        self.save({"receivedAt": int(time.time() * 1000), "report": report})

    def snapshot(self) -> dict:
        snapshot = self.read() or {"receivedAt": 0, "report": None}
        age = int(time.time() * 1000) - snapshot["receivedAt"]
        return {**snapshot, "stale": snapshot["report"] is None or not 0 <= age <= STALE_MS}
