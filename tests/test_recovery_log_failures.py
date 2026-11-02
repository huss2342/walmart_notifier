"""A telemetry outage cannot stop recovery or print private filesystem details."""

import json
import logging

import browser_recovery


def test_recovery_append_failure_is_counted_without_stderr(tmp_path, monkeypatch, capsys):
    handler = browser_recovery._recovery_file_handler(tmp_path / "recovery.log")

    def fail(_data):
        raise OSError("private-file-path")

    monkeypatch.setattr(handler.writer, "append", fail)
    before = browser_recovery._RECOVERY_LOG_FAILURES
    record = logging.LogRecord(
        "browser-recovery", logging.INFO, "private-file", 1, "healthy", (), None
    )
    handler.handle(record)
    assert before + 1 == browser_recovery._RECOVERY_LOG_FAILURES
    assert capsys.readouterr().err == ""
    status = json.loads(browser_recovery.RecoveryJSONFormatter().format(record))
    assert status["dropped_events"] == before + 1
    assert "private" not in json.dumps(status)
    handler.close()


def test_recovery_log_handler_initialization_failure_is_nonfatal(tmp_path, monkeypatch, capsys):
    from discord_bridge import rolling_logs

    def fail(*args, **kwargs):
        raise OSError("private-init-path")

    monkeypatch.setattr(rolling_logs.RollingLogHandler, "__init__", fail)
    before = browser_recovery._RECOVERY_LOG_FAILURES
    handler = browser_recovery._recovery_file_handler(tmp_path / "recovery.log")
    handler.handle(
        logging.LogRecord("browser-recovery", logging.INFO, "private", 1, "healthy", (), None)
    )
    assert before + 2 == browser_recovery._RECOVERY_LOG_FAILURES
    assert capsys.readouterr().err == ""
    handler.close()
