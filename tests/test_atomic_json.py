"""Shared replacement mechanics preserve the prior file on any failed write."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import atomic_json
from atomic_json import write_atomic_json


@pytest.mark.parametrize("encoded", [b'{"value":"bytes"}\n', '{"value":"text"}\n'])
def test_atomic_write_creates_parents_and_leaves_only_the_destination(tmp_path, encoded):
    path = tmp_path / "nested" / "state.json"
    write_atomic_json(path, encoded)
    assert json.loads(path.read_bytes())["value"] in {"bytes", "text"}
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("operation", ["fsync", "replace"])
def test_failed_atomic_write_preserves_prior_file_and_cleans_up(tmp_path, monkeypatch, operation):
    path = tmp_path / "state.json"
    path.write_bytes(b'{"value":"original"}')

    def unavailable(*args):
        raise OSError("storage unavailable")

    monkeypatch.setattr(atomic_json.os, operation, unavailable)
    with pytest.raises(OSError, match="storage unavailable"):
        write_atomic_json(path, b'{"value":"replacement"}')
    assert path.read_bytes() == b'{"value":"original"}'
    assert list(tmp_path.iterdir()) == [path]


def test_replacement_uses_a_complete_unique_sibling_closed_before_replace(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    real_replace = atomic_json.os.replace
    sources = []

    def verify_replace(source, destination):
        source = Path(source)
        assert source.parent == path.parent
        assert destination == path
        assert json.loads(source.read_bytes()) == {"value": len(sources)}
        sources.append(source)
        real_replace(source, destination)  # Windows refuses open temporary-file handles.

    monkeypatch.setattr(atomic_json.os, "replace", verify_replace)
    for number in range(2):
        write_atomic_json(path, json.dumps({"value": number}))
    assert len(set(sources)) == 2
    assert json.loads(path.read_bytes()) == {"value": 1}
    assert list(tmp_path.iterdir()) == [path]


def test_separate_callers_cannot_collide_on_their_temporary_files(tmp_path):
    path = tmp_path / "state.json"

    def write(number):
        write_atomic_json(path, json.dumps({"value": number, "padding": "x" * 4096}))

    with ThreadPoolExecutor(max_workers=6) as executor:
        list(executor.map(write, range(30)))
    saved = json.loads(path.read_bytes())
    assert saved["value"] in range(30)
    assert saved["padding"] == "x" * 4096
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("windows_error", [5, 32, 33])
def test_transient_windows_replacement_contention_is_retried_boundedly(
    tmp_path, monkeypatch, windows_error,
):
    path = tmp_path / "state.json"
    real_replace = atomic_json.os.replace
    calls = []

    def briefly_busy(source, destination):
        calls.append(source)
        if len(calls) < 3:
            error = PermissionError("busy destination")
            error.winerror = windows_error
            raise error
        real_replace(source, destination)

    monkeypatch.setattr(atomic_json.os, "replace", briefly_busy)
    write_atomic_json(path, b'{"value":"saved"}')
    assert len(calls) == 3
    assert json.loads(path.read_bytes()) == {"value": "saved"}
    assert list(tmp_path.iterdir()) == [path]


def test_persistent_windows_denial_has_a_finite_retry_budget(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_bytes(b'{"value":"original"}')
    attempts = []

    def always_busy(*args):
        attempts.append(1)
        error = PermissionError("access denied")
        error.winerror = 5
        raise error

    monkeypatch.setattr(atomic_json.os, "replace", always_busy)
    with pytest.raises(PermissionError, match="access denied"):
        write_atomic_json(path, b'{"value":"replacement"}')
    assert 1 < len(attempts) <= 5
    assert json.loads(path.read_bytes()) == {"value": "original"}
    assert list(tmp_path.iterdir()) == [path]
