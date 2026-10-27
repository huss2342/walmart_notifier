"""Strict Chrome Native Messaging endpoint; stdout is protocol frames only."""

import argparse
import json
import logging
import logging.handlers
import math
import re
import struct
import sys
import time
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from native_verification.contracts import (
    ERROR_CODES,
    HOST_NAME,
    MAX_MESSAGE_BYTES,
    PROTOCOL_VERSION,
    DesktopError,
)
from native_verification.controller import NativeController

EXTENSION_ORIGIN = "chrome-extension://oidgbnehgnikmniolojjkmhlpifdoiil/"
OPS = frozenset({"probe", "prepare", "press", "status", "release", "close"})
ID_PATTERN = re.compile(r"[A-Za-z0-9:_-]{1,80}\Z")


def _invalid():
    raise DesktopError("invalid-request")


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            _invalid()
        value[key] = item
    return value


def _read_exact(stream, count, *, clean_eof=False):
    data = bytearray()
    while len(data) < count:
        chunk = stream.read(count - len(data))
        if not chunk:
            if clean_eof and not data:
                return None
            _invalid()
        data.extend(chunk)
    return bytes(data)


def read_frame(stream):
    header = _read_exact(stream, 4, clean_eof=True)
    if header is None:
        return None
    size = struct.unpack("<I", header)[0]
    if not 0 < size <= MAX_MESSAGE_BYTES:
        _invalid()
    payload = _read_exact(stream, size)
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda _: _invalid(),
        )
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        return value
    except (ValueError, UnicodeError, RecursionError) as error:
        raise DesktopError("invalid-request") from error


def write_frame(stream, value):
    payload = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode(
        "utf-8"
    )
    if len(payload) > MAX_MESSAGE_BYTES:
        _invalid()
    stream.write(struct.pack("<I", len(payload)) + payload)
    stream.flush()


def _number(value):
    if type(value) not in {int, float}:
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def validate_request(value):
    if not isinstance(value, dict) or set(value) != {"v", "id", "op", "payload"}:
        _invalid()
    if type(value["v"]) is not int or value["v"] != PROTOCOL_VERSION:
        _invalid()
    if not isinstance(value["id"], str) or not ID_PATTERN.fullmatch(value["id"]):
        _invalid()
    op, payload = value["op"], value["payload"]
    if not isinstance(op, str) or op not in OPS or not isinstance(payload, dict):
        _invalid()
    if op in {"probe", "status", "release", "close"}:
        if payload:
            _invalid()
    elif op == "press":
        if (
            set(payload) != {"token"}
            or not isinstance(payload["token"], str)
            or not re.fullmatch(r"[a-f0-9]{48}", payload["token"])
        ):
            _invalid()
    elif op == "prepare":
        if set(payload) != {"viewport", "point"}:
            _invalid()
        viewport, point = payload["viewport"], payload["point"]
        if not isinstance(viewport, dict) or set(viewport) != {"width", "height", "dpr"}:
            _invalid()
        if not isinstance(point, dict) or set(point) != {"x", "y"}:
            _invalid()
        if not all(_number(item) for item in [*viewport.values(), *point.values()]):
            _invalid()
        if not (
            0 < viewport["width"] <= 32768
            and 0 < viewport["height"] <= 32768
            and 0.5 <= viewport["dpr"] <= 4
        ):
            _invalid()
        if not (0 <= point["x"] < viewport["width"] and 0 <= point["y"] < viewport["height"]):
            _invalid()
    return value


def validate_launch(argv):
    parser = argparse.ArgumentParser(add_help=False, exit_on_error=False)
    parser.add_argument("origin")
    parser.add_argument("--parent-window", required=True, type=int)
    parser.add_argument("--host-manifest", required=True)
    try:
        args, unknown = parser.parse_known_args(argv)
        if unknown or not 0 < args.parent_window <= (2**64 - 1):
            raise DesktopError("invalid-window")
        manifest = json.loads(Path(args.host_manifest).read_text(encoding="utf-8-sig"))
        if manifest.get("name") != HOST_NAME or manifest.get("type") != "stdio":
            raise DesktopError("invalid-origin")
        if manifest.get("allowed_origins") != [EXTENSION_ORIGIN] or args.origin != EXTENSION_ORIGIN:
            raise DesktopError("invalid-origin")
        return args.parent_window
    except DesktopError:
        raise
    except (
        OSError,
        ValueError,
        TypeError,
        AttributeError,
        argparse.ArgumentError,
        SystemExit,
    ) as error:
        raise DesktopError("invalid-origin") from error


def safe_logger():
    root = Path(__file__).resolve().parent.parent / "data" / "native-verification"
    root.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("native-verification")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.handlers.RotatingFileHandler(
            root / "host.log", maxBytes=65536, backupCount=2, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    return logger


def run_session(input_stream, output_stream, controller, logger=None):
    try:
        while True:
            request_id, op = "invalid", None
            try:
                value = read_frame(input_stream)
                if value is None:
                    break
                if (
                    isinstance(value, dict)
                    and isinstance(value.get("id"), str)
                    and ID_PATTERN.fullmatch(value["id"])
                ):
                    request_id = value["id"]
                request = validate_request(value)
                op = request["op"]
                state = controller.handle(op, request["payload"])
                response = {"v": PROTOCOL_VERSION, "id": request_id, "ok": True, "state": state}
            except DesktopError as error:
                code = error.code if error.code in ERROR_CODES else "host-unavailable"
                # A malformed frame terminates the entire lease, including owned input.
                if code == "invalid-request":
                    controller.close()
                response = {
                    "v": PROTOCOL_VERSION,
                    "id": request_id,
                    "ok": False,
                    "error": code,
                    "state": controller.state(),
                }
            write_frame(output_stream, response)
            if logger:
                state = response["state"]
                record = {
                    "at": round(time.time(), 3),
                    "op": op if op in OPS else "invalid",
                    "ok": response["ok"],
                }
                record.update(
                    {
                        key: state[key]
                        for key in (
                            "phase",
                            "pressSent",
                            "pressConfirmed",
                            "releaseConfirmed",
                            "holdMs",
                        )
                    }
                )
                if "error" in response:
                    record["error"] = response["error"]
                logger.info(json.dumps(record, separators=(",", ":")))
            if op == "close" or response.get("error") == "invalid-request":
                break
    finally:
        controller.close()


def main(argv=None):
    controller = None
    try:
        parent_window = validate_launch(sys.argv[1:] if argv is None else argv)
        from native_verification.win32 import WindowsDesktop

        controller = NativeController(WindowsDesktop(parent_window))
        run_session(sys.stdin.buffer, sys.stdout.buffer, controller, safe_logger())
        return 0
    except Exception:
        # Chrome stderr/diagnostics must not expose raw exception text or arguments.
        return 1
    finally:
        if controller is not None:
            controller.close()


if __name__ == "__main__":
    raise SystemExit(main())
