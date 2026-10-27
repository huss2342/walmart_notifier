"""Scan settings remain strict, editable JSON without changing a running server."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import relay_settings
from relay_settings import RelaySettingsStore, validate_settings


def settings():
    return {
        "schemaVersion": 1,
        "refreshMinutes": 10,
        "pageDelaySeconds": 15,
        "sweepMode": "none",
    }


def test_validation_returns_an_independent_dictionary():
    incoming = settings()
    clean = validate_settings(incoming)
    assert clean == incoming
    clean["refreshMinutes"] = 30
    assert incoming["refreshMinutes"] == 10


@pytest.mark.parametrize("value", [None, True, 1, [], "private token"])
def test_settings_must_be_an_object(value):
    with pytest.raises(ValueError, match="^Invalid relay settings$"):
        validate_settings(value)


@pytest.mark.parametrize("field,value", [
    ("schemaVersion", True), ("schemaVersion", 1.0), ("schemaVersion", 2),
    ("refreshMinutes", True), ("refreshMinutes", -1), ("refreshMinutes", 121),
    ("refreshMinutes", 1.5), ("refreshMinutes", float("nan")),
    ("refreshMinutes", float("inf")), ("refreshMinutes", float("-inf")),
    ("refreshMinutes", "10"), ("refreshMinutes", []),
    ("pageDelaySeconds", False), ("pageDelaySeconds", 4), ("pageDelaySeconds", 61),
    ("pageDelaySeconds", 5.0), ("pageDelaySeconds", float("nan")),
    ("sweepMode", None), ("sweepMode", "unknown"), ("sweepMode", []),
    ("sweepMode", "secret" * 5000), ("token", "private token"),
], ids=lambda value: "oversized-label" if isinstance(value, str) and len(value) > 100 else None)
def test_invalid_fields_raise_only_a_fixed_generic_error(field, value):
    incoming = settings()
    incoming[field] = value
    with pytest.raises(ValueError) as error:
        validate_settings(incoming)
    assert str(error.value) == "Invalid relay settings"


@pytest.mark.parametrize("field", list(settings()))
def test_every_schema_field_is_required(field):
    incoming = settings()
    del incoming[field]
    with pytest.raises(ValueError, match="^Invalid relay settings$"):
        validate_settings(incoming)


@pytest.mark.parametrize("refresh,delay,mode", [
    (0, 5, "none"), (120, 60, "sweep"), (1, 15, "high-value"),
])
def test_documented_boundaries_and_modes_are_valid(refresh, delay, mode):
    incoming = settings()
    incoming.update(refreshMinutes=refresh, pageDelaySeconds=delay, sweepMode=mode)
    assert validate_settings(incoming) == incoming


def test_missing_file_returns_none_without_creating_or_migrating_it(tmp_path, monkeypatch):
    path = tmp_path / "nested" / "relay-settings.json"
    monkeypatch.setattr(relay_settings, "DEFAULT_PATH", path)
    store = RelaySettingsStore()
    assert store.read() is None
    assert not path.parent.exists()


@pytest.mark.parametrize("path", ["", ":memory:"])
def test_memory_store_starts_empty_and_protects_input_and_return_values(path):
    store = RelaySettingsStore(path)
    assert store.read() is None
    incoming = settings()
    saved = store.save(incoming)
    incoming["refreshMinutes"] = 20
    saved["pageDelaySeconds"] = 60
    outgoing = store.read()
    outgoing["sweepMode"] = "sweep"
    assert store.read() == settings()


def test_saved_file_is_readable_json_and_survives_restart(tmp_path):
    path = tmp_path / "nested" / "relay-settings.json"
    store = RelaySettingsStore(path)
    assert store.save(settings()) == settings()
    encoded = path.read_bytes()
    assert encoded.endswith(b"\n")
    assert b'\n  "refreshMinutes": 10' in encoded
    assert json.loads(encoded) == settings()
    assert RelaySettingsStore(str(path)).read() == settings()
    assert list(path.parent.iterdir()) == [path]


def test_each_read_observes_manual_edits_and_deletion_without_restart(tmp_path):
    path = tmp_path / "relay-settings.json"
    store = RelaySettingsStore(path)
    store.save(settings())
    edited = settings()
    edited.update(refreshMinutes=2, pageDelaySeconds=30, sweepMode="high-value")
    path.write_text(json.dumps(edited), encoding="utf-8")
    assert store.read() == edited
    assert RelaySettingsStore(path).read() == edited
    path.unlink()
    assert store.read() is None


def test_invalid_manual_edit_is_rejected_then_corrected_without_restart(tmp_path):
    path = tmp_path / "relay-settings.json"
    store = RelaySettingsStore(path)
    store.save(settings())
    path.write_text("private invalid JSON", encoding="utf-8")
    with pytest.raises(ValueError, match="^Invalid relay settings$"):
        store.read()
    path.write_text(json.dumps(settings()), encoding="utf-8")
    assert store.read() == settings()


@pytest.mark.parametrize("encoded", [
    b"", b"[]", b"null", b'{"schemaVersion":1}', b"private invalid JSON",
    b'{"schemaVersion":NaN}', b'{"schemaVersion":Infinity}', b"\xff\xfeinvalid",
    b"[" * 2000 + b"0" + b"]" * 2000,
    b'{"schemaVersion":1,"refreshMinutes":10,"pageDelaySeconds":15,'
    b'"sweepMode":"none","refreshMinutes":30}',
], ids=[
    "empty", "array", "null", "missing-fields", "invalid-json", "nan", "infinity",
    "invalid-encoding", "deep-json", "duplicate-key",
])
def test_malformed_file_errors_do_not_expose_payload_or_decoder_details(tmp_path, encoded):
    path = tmp_path / "relay-settings.json"
    path.write_bytes(encoded)
    with pytest.raises(ValueError) as error:
        RelaySettingsStore(path).read()
    assert str(error.value) == "Invalid relay settings"


def test_read_accepts_exactly_sixteen_kib_but_rejects_one_byte_more(tmp_path):
    path = tmp_path / "relay-settings.json"
    encoded = json.dumps(settings()).encode("utf-8")
    bounded = encoded + b" " * (16 * 1024 - len(encoded))
    path.write_bytes(bounded)
    store = RelaySettingsStore(path)
    assert store.read() == settings()
    path.write_bytes(bounded + b" ")
    with pytest.raises(ValueError, match="^Invalid relay settings$"):
        store.read()


def test_file_read_io_errors_propagate(tmp_path, monkeypatch):
    path = tmp_path / "relay-settings.json"

    def denied_open(self, *args, **kwargs):
        raise PermissionError("read unavailable")

    monkeypatch.setattr(Path, "open", denied_open)
    with pytest.raises(PermissionError, match="read unavailable"):
        RelaySettingsStore(path).read()


@pytest.mark.parametrize("failure", ["fsync", "replace"])
def test_failed_save_preserves_prior_file_and_cleans_temporary_sibling(
    tmp_path, monkeypatch, failure,
):
    path = tmp_path / "relay-settings.json"
    store = RelaySettingsStore(path)
    store.save(settings())
    before = path.read_bytes()
    incoming = settings()
    incoming["refreshMinutes"] = 30

    def fail_write(*args):
        raise OSError("write unavailable")

    monkeypatch.setattr(relay_settings.os, failure, fail_write)
    with pytest.raises(OSError, match="write unavailable"):
        store.save(incoming)
    assert path.read_bytes() == before
    assert store.read() == settings()
    assert list(tmp_path.iterdir()) == [path]


def test_invalid_save_preserves_prior_memory_and_disk(tmp_path):
    path = tmp_path / "relay-settings.json"
    disk = RelaySettingsStore(path)
    memory = RelaySettingsStore(":memory:")
    disk.save(settings())
    memory.save(settings())
    before = path.read_bytes()
    incoming = settings()
    incoming["refreshMinutes"] = True
    for store in (disk, memory):
        with pytest.raises(ValueError, match="^Invalid relay settings$"):
            store.save(incoming)
        assert store.read() == settings()
    assert path.read_bytes() == before


def test_concurrent_saves_leave_a_complete_restartable_file(tmp_path):
    path = tmp_path / "relay-settings.json"
    store = RelaySettingsStore(path)

    def save_refresh(refresh):
        incoming = settings()
        incoming["refreshMinutes"] = refresh
        assert store.save(incoming) == incoming
        assert store.read()["refreshMinutes"] in range(30)

    with ThreadPoolExecutor(max_workers=6) as executor:
        list(executor.map(save_refresh, range(30)))

    assert RelaySettingsStore(path).read() == store.read()
    assert list(tmp_path.iterdir()) == [path]
