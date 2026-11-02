"""Supervisor observability is redacted and cannot change native input cleanup."""

import io
import json
import logging

import pytest
from native_verification import host

import browser_recovery


class Controller:
    def __init__(self):
        self.closed = 0

    def state(self):
        return {
            "phase": "idle",
            "pressSent": False,
            "pressConfirmed": False,
            "releaseConfirmed": False,
            "holdMs": 0,
        }

    def handle(self, op, _payload):
        assert op == "probe"
        return self.state()

    def close(self):
        self.closed += 1


def request():
    source = io.BytesIO()
    host.write_frame(
        source, {"v": host.PROTOCOL_VERSION, "id": "probe-1", "op": "probe", "payload": {}}
    )
    source.seek(0)
    return source


def test_native_session_lifecycle_and_operations_are_correlated_without_protocol_payloads():
    records = []

    class Logger:
        def info(self, message):
            records.append(json.loads(message))

    controller, output = Controller(), io.BytesIO()
    host.run_session(request(), output, controller, Logger())
    assert [row["event"] for row in records] == [
        "native-session-started",
        "native-operation",
        "native-session-closed",
    ]
    assert len({row["run_id"] for row in records}) == 1
    assert records[1]["duration_ms"] >= 0
    assert "probe-1" not in json.dumps(records)
    assert controller.closed == 1
    output.seek(0)
    assert host.read_frame(output)["id"] == "probe-1"


def test_native_logging_failure_does_not_break_protocol_or_cleanup():
    class Logger:
        def info(self, _message):
            raise OSError("private-path-or-token")

    controller, output = Controller(), io.BytesIO()
    before = host.log_status()["dropped_events"]
    host.run_session(request(), output, controller, Logger())
    assert controller.closed == 1
    assert host.log_status()["dropped_events"] >= before + 1
    assert "private-path" not in json.dumps(host.log_status())
    output.seek(0)
    assert host.read_frame(output)["ok"] is True


def test_recovery_json_formatter_preserves_decision_duration_but_drops_profile_and_error_text():
    record = logging.LogRecord(
        "browser-recovery",
        logging.INFO,
        "private-path",
        1,
        "chrome-profile-selected profile=%s",
        ("private-profile",),
        None,
    )
    record.duration_ms = 20.5
    value = json.loads(browser_recovery.RecoveryJSONFormatter().format(record))
    assert value["event"] == "chrome-profile-selected"
    assert value["duration_ms"] == 20.5
    assert value["timestamp"] == record.created
    assert "private" not in json.dumps(value)
    record.msg, record.args = "unknown-secret", ()
    assert json.loads(browser_recovery.RecoveryJSONFormatter().format(record))["event"] == "other"


def test_native_operation_latency_excludes_idle_frame_wait(monkeypatch):
    clock = [0]
    records = []
    original_read = host.read_frame

    def read(stream):
        clock[0] += 10
        return original_read(stream)

    class TimedController(Controller):
        def handle(self, op, payload):
            clock[0] += 0.02
            return super().handle(op, payload)

    class Logger:
        def info(self, value):
            records.append(json.loads(value))

    monkeypatch.setattr(host.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(host, "read_frame", read)
    host.run_session(request(), io.BytesIO(), TimedController(), Logger())
    operation = next(row for row in records if row["event"] == "native-operation")
    assert operation["duration_ms"] == 20


def test_native_file_handler_failure_is_counted_without_stderr_or_payload(tmp_path, monkeypatch):
    handler = host._NativeLogHandler(tmp_path / "native.log", delay=True)

    def unavailable():
        raise OSError("private-file-path")

    monkeypatch.setattr(handler, "_open", unavailable)
    before = host.log_status()["dropped_events"]
    handler.emit(logging.LogRecord("native", logging.INFO, "", 1, "safe-record", (), None))
    assert host.log_status()["dropped_events"] == before + 1
    handler.close()


@pytest.mark.parametrize("stage", ["mkdir", "handler"])
def test_native_logger_initialization_failure_still_services_protocol(
    tmp_path, monkeypatch, capsys, stage
):
    logger = logging.getLogger("native-verification")
    monkeypatch.setattr(logger, "handlers", [])

    def unavailable(*_args, **_kwargs):
        raise OSError("private-log-path-or-account")

    if stage == "mkdir":
        class UnavailablePath:
            parent = property(lambda self: self)

            def resolve(self):
                return self

            def __truediv__(self, _part):
                return self

            mkdir = unavailable

        monkeypatch.setattr(host, "Path", lambda _value: UnavailablePath())
    else:
        monkeypatch.setattr(host, "__file__", str(tmp_path / "native_verification" / "host.py"))
        monkeypatch.setattr(host, "_NativeLogHandler", unavailable)
    before = host.log_status()["dropped_events"]
    controller, output = Controller(), io.BytesIO()
    host.run_session(request(), output, controller, host.safe_logger())
    assert controller.closed == 1
    assert host.log_status()["dropped_events"] > before
    output.seek(0)
    assert host.read_frame(output)["ok"] is True
    assert "private" not in capsys.readouterr().err


def test_native_delayed_log_open_failure_cannot_break_a_session(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(host, "__file__", str(tmp_path / "native_verification" / "host.py"))
    logger = logging.getLogger("native-verification")
    monkeypatch.setattr(logger, "handlers", [])

    def unavailable(_self):
        raise OSError("private-open-path-or-account")

    monkeypatch.setattr(host._NativeLogHandler, "_open", unavailable)
    before = host.log_status()["dropped_events"]
    controller, output = Controller(), io.BytesIO()
    host.run_session(request(), output, controller, host.safe_logger())
    assert controller.closed == 1
    assert host.log_status()["dropped_events"] > before
    output.seek(0)
    assert host.read_frame(output)["ok"] is True
    assert "private" not in capsys.readouterr().err
    for handler in logger.handlers:
        handler.close()
