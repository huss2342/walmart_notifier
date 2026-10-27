"""Scanner settings, commands and status stay strict, bounded and atomic."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

import scanner_control
from scanner_control import (
    ScannerCommandsStore,
    ScannerSettingsStore,
    ScannerStatusStore,
    parse_json,
    validate_commands,
    validate_settings,
    validate_status,
)


def settings():
    return {
        "schemaVersion": 1,
        "auxiliaryDepartments": True,
        "auxiliaryKeywords": True,
        "auxiliaryBrands": False,
        "auxiliaryKeywordQueries": ["Chair", "Pet"],
        "groupScannerTabs": True,
        "rotateScannerTabs": True,
        "verificationAssistEnabled": True,
        "verificationNativeEnabled": False,
        "pathPattern": "^/reviews/claim-product",
    }


def commands():
    return {"schemaVersion": 1, "restartHomeId": "home_20261025-1", "restartAdditionalId": None}


def report():
    return {
        "schemaVersion": 1,
        "extensionVersion": "2.22.0",
        "exportedAt": 100_000,
        "settings": settings(),
        "primary": {
            "tabId": 1,
            "generation": 5,
            "phase": "waiting-navigation",
            "reachable": True,
            "page": 2,
            "totalPages": 25,
            "visitedPages": 2,
            "lastActivityAt": 100_000,
            "nextActionAt": 115_000,
        },
        "lanes": [
            {
                "lane": "departments",
                "tabId": 2,
                "generation": 8,
                "status": "scanning",
                "targetLabel": "Home",
                "completedTargets": 1,
                "totalTargets": 35,
                "pages": 2,
                "failures": 0,
                "truncatedTargets": 0,
                "coverageLimitedTargets": 0,
                "nextAt": 0,
                "round": 1,
                "lastErrorCode": "none",
                "owned": True,
                "enabled": True,
            }
        ],
        "verification": {
            "pausedUntil": 0,
            "count": 1,
            "lastAt": 90_000,
            "assistEnabled": True,
            "nativeEnabled": False,
            "statusCode": "watching",
            "attempt": 0,
            "attemptLimit": 10,
            "lastOutcome": "",
            "errorStage": "",
            "errorReason": "",
            "pressSent": None,
            "cleanupConfirmed": None,
            "retryAt": 0,
        },
        "sync": {"settingsOk": True, "settingsSyncedAt": 100_000, "commandsOk": True},
        "commands": {
            "restartHome": {"id": "home_1", "outcome": "started", "at": 100_000},
            "restartAdditional": None,
        },
        "errors": [],
    }


@pytest.mark.parametrize(
    "validator,factory",
    [
        (validate_settings, settings),
        (validate_commands, commands),
        (validate_status, report),
    ],
)
def test_exact_schemas_return_independent_copies(validator, factory):
    incoming = factory()
    clean = validator(incoming)
    assert clean == incoming
    clean["schemaVersion"] = 2
    assert incoming["schemaVersion"] == 1
    for key in list(incoming):
        missing = factory()
        del missing[key]
        with pytest.raises(ValueError):
            validator(missing)
    incoming["secret"] = "payload-must-not-leak"
    with pytest.raises(ValueError) as error:
        validator(incoming)
    assert "payload" not in str(error.value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schemaVersion", True),
        ("schemaVersion", 1.0),
        ("schemaVersion", 2),
        ("auxiliaryDepartments", 1),
        ("auxiliaryKeywords", "true"),
        ("auxiliaryBrands", None),
        ("groupScannerTabs", []),
        ("rotateScannerTabs", {}),
        ("verificationAssistEnabled", 1),
        ("verificationNativeEnabled", "false"),
        ("auxiliaryKeywordQueries", "Chair"),
        ("auxiliaryKeywordQueries", [True]),
        ("auxiliaryKeywordQueries", [""]),
        ("auxiliaryKeywordQueries", ["   "]),
        ("auxiliaryKeywordQueries", ["Chair", "chair"]),
        ("auxiliaryKeywordQueries", ["STRASSE", "Straße"]),
        ("auxiliaryKeywordQueries", ["x" * 81]),
        ("auxiliaryKeywordQueries", ["chair\nsecret"]),
        ("auxiliaryKeywordQueries", [str(index) for index in range(101)]),
        ("pathPattern", ""),
        ("pathPattern", "x" * 201),
        ("pathPattern", "path\x7fcontrol"),
    ],
)
def test_settings_reject_weak_types_duplicates_controls_and_oversized_values(field, value):
    incoming = settings()
    incoming[field] = value
    with pytest.raises(ValueError, match="^Invalid scanner settings$"):
        validate_settings(incoming)


@pytest.mark.parametrize("queries", [None, [], ["x" * 80], [str(i) for i in range(100)]])
def test_optional_and_boundary_keyword_lists_are_valid(queries):
    incoming = {**settings(), "auxiliaryKeywordQueries": queries}
    assert validate_settings(incoming) == incoming


def test_keyword_trimming_is_canonical_and_does_not_modify_the_input():
    incoming = {**settings(), "auxiliaryKeywordQueries": ["  Chair  ", " Pet "]}
    assert validate_settings(incoming)["auxiliaryKeywordQueries"] == ["Chair", "Pet"]
    assert incoming["auxiliaryKeywordQueries"] == ["  Chair  ", " Pet "]


@pytest.mark.parametrize("value", ["", "a" * 65, "with space", "secret/url", 1, True, []])
def test_command_identifiers_are_bounded_safe_strings(value):
    with pytest.raises(ValueError, match="^Invalid scanner commands$"):
        validate_commands({**commands(), "restartHomeId": value})


@pytest.mark.parametrize(
    "section,field,value",
    [
        (None, "extensionVersion", "unknown-private"),
        (None, "extensionVersion", "2.0-private"),
        (None, "exportedAt", True),
        (None, "exportedAt", -1),
        ("primary", "generation", 1.0),
        ("primary", "tabId", -1),
        ("primary", "phase", "secret-error"),
        ("primary", "reachable", 1),
        ("primary", "page", float("nan")),
        ("primary", "nextActionAt", 10**15 + 1),
        ("verification", "statusCode", "private data"),
        ("verification", "attempt", 11),
        ("verification", "attemptLimit", 9),
        ("verification", "lastOutcome", "x" * 65),
        ("verification", "errorStage", "secret/path"),
        ("verification", "pressSent", 1),
        ("verification", "cleanupConfirmed", "true"),
        ("sync", "commandsOk", 0),
    ],
)
def test_status_nested_fields_reject_unbounded_values_and_free_text(section, field, value):
    incoming = report()
    (incoming if section is None else incoming[section])[field] = value
    with pytest.raises(ValueError, match="^Invalid scanner status$"):
        validate_status(incoming)


def test_status_rejects_duplicate_lanes_secret_keys_urls_and_free_error_messages():
    for change in [
        lambda data: data["lanes"].append(dict(data["lanes"][0])),
        lambda data: data["lanes"][0].update(targetLabel="https://private.example/secret"),
        lambda data: data["lanes"][0].update(lastErrorCode="private stack trace"),
        lambda data: data["lanes"][0].update(owned=1),
        lambda data: data["lanes"][0].update(enabled="true"),
        lambda data: data["primary"].update(url="https://private.example/secret"),
        lambda data: data.update(errors=[{"component": "token", "code": "private"}]),
        lambda data: data.update(errors=[{"component": "primary", "code": "not safe"}]),
        lambda data: data.update(errors=[{"component": "primary", "code": "error"}] * 9),
        lambda data: data["commands"].update(
            restartHome={"id": "a", "outcome": "unknown", "at": 0},
        ),
    ]:
        incoming = report()
        change(incoming)
        with pytest.raises(ValueError, match="^Invalid scanner status$"):
            validate_status(incoming)


def test_unknown_extension_versions_and_coarse_scanning_phase_are_safe_status_fallbacks():
    incoming = report()
    incoming["extensionVersion"] = "unknown"
    incoming["primary"]["phase"] = "scanning"
    assert validate_status(incoming) == incoming


@pytest.mark.parametrize("field", ["tabActive", "tabFrozen", "windowFocused"])
@pytest.mark.parametrize("value", [True, False, None])
def test_optional_verification_visibility_metadata_accepts_only_safe_nullable_booleans(
    field, value
):
    incoming = report()
    incoming["verification"][field] = value
    assert validate_status(incoming) == incoming


@pytest.mark.parametrize("field", ["tabActive", "tabFrozen", "windowFocused"])
@pytest.mark.parametrize("value", [1, 0, "false", [], {}, 0.0])
def test_optional_verification_visibility_metadata_rejects_weak_types(field, value):
    incoming = report()
    incoming["verification"][field] = value
    with pytest.raises(ValueError, match="^Invalid scanner status$"):
        validate_status(incoming)


@pytest.mark.parametrize(
    "code",
    [
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
    ],
)
def test_optional_lane_failure_code_preserves_only_the_agreed_safe_enum(code):
    incoming = report()
    incoming["lanes"][0]["lastFailureCode"] = code
    assert validate_status(incoming) == incoming


@pytest.mark.parametrize(
    "code",
    [
        None,
        True,
        0,
        [],
        {},
        "",
        "not-allowlisted",
        "missing-panel: private data",
        "https://private.example/",
        "x" * 65,
    ],
)
def test_optional_lane_failure_code_rejects_unknown_codes_and_raw_details(code):
    incoming = report()
    incoming["lanes"][0]["lastFailureCode"] = code
    with pytest.raises(ValueError, match="^Invalid scanner status$"):
        validate_status(incoming)


def test_optional_lane_runtime_retains_only_bounded_probe_metadata():
    incoming = report()
    incoming["lanes"][0]["runtime"] = {
        "phase": "waiting-selection",
        "reachable": True,
        "active": True,
        "busy": False,
        "visibility": "visible",
        "focusReason": "short-lease",
        "lastActivityAt": 100_000,
        "nextActionAt": 105_000,
    }
    assert validate_status(incoming) == incoming


@pytest.mark.parametrize(
    "field,value",
    [
        ("phase", "private error text"),
        ("focusReason", "https://private.example/"),
        ("visibility", "private"),
        ("active", 1),
        ("lastActivityAt", -1),
        ("nextActionAt", True),
        ("token", "private"),
    ],
)
def test_optional_lane_runtime_rejects_raw_details_and_weak_types(field, value):
    incoming = report()
    incoming["lanes"][0]["runtime"] = {
        "phase": "waiting-selection",
        "reachable": True,
        "active": True,
        "busy": False,
        "visibility": "visible",
        "focusReason": "busy",
        "lastActivityAt": 100_000,
        "nextActionAt": 105_000,
    }
    incoming["lanes"][0]["runtime"][field] = value
    with pytest.raises(ValueError, match="^Invalid scanner status$"):
        validate_status(incoming)


@pytest.mark.parametrize(
    "encoded",
    [
        b'{"schemaVersion":1,"schemaVersion":1}',
        b'{"nested":{"a":1,"a":2}}',
        b'{"private":NaN}',
        b'{"private":Infinity}',
        b"private invalid JSON",
        b"\xff",
    ],
)
def test_parser_rejects_duplicate_keys_and_malformed_json_without_payload_details(encoded):
    with pytest.raises(ValueError) as error:
        parse_json(encoded)
    assert str(error.value) == "Invalid scanner control payload"


@pytest.mark.parametrize(
    "store_type,factory",
    [
        (ScannerSettingsStore, settings),
        (ScannerCommandsStore, commands),
    ],
)
def test_missing_memory_atomic_disk_manual_edit_and_return_copy_semantics(
    tmp_path,
    store_type,
    factory,
):
    disk = store_type(tmp_path / "nested" / "control.json")
    memory = store_type(":memory:")
    assert disk.read() is None and memory.read() is None
    assert not disk.path.parent.exists()
    for store in (disk, memory):
        incoming = factory()
        saved = store.save(incoming)
        saved["schemaVersion"] = 7
        incoming["schemaVersion"] = 8
        assert store.read() == factory()
    changed = factory()
    if store_type is ScannerSettingsStore:
        changed["rotateScannerTabs"] = False
    else:
        changed["restartAdditionalId"] = "new_1"
    disk.path.write_text(json.dumps(changed), encoding="utf-8")
    assert disk.read() == changed
    assert store_type(disk.path).read() == changed
    disk.path.unlink()
    assert disk.read() is None


@pytest.mark.parametrize(
    "store_type,factory",
    [
        (ScannerSettingsStore, settings),
        (ScannerCommandsStore, commands),
    ],
)
@pytest.mark.parametrize("failure", ["fsync", "replace"])
def test_failed_atomic_save_preserves_previous_file_and_removes_temporary_files(
    tmp_path,
    monkeypatch,
    store_type,
    factory,
    failure,
):
    store = store_type(tmp_path / "control.json")
    store.save(factory())
    before = store.path.read_bytes()

    def fail(*args):
        raise OSError("private path must never enter an HTTP response")

    monkeypatch.setattr(scanner_control.os, failure, fail)
    with pytest.raises(OSError):
        store.save(factory())
    assert store.path.read_bytes() == before
    assert store.read() == factory()
    assert list(tmp_path.iterdir()) == [store.path]


def test_status_is_current_disk_backed_atomic_and_reports_staleness_without_affecting_report(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(scanner_control.time, "time", lambda: 100)
    store = ScannerStatusStore(tmp_path / "scanner-status.json")
    assert store.snapshot() == {"receivedAt": 0, "report": None, "stale": True}
    incoming = report()
    store.record_report(incoming)
    incoming["errors"].append({"component": "primary", "code": "later"})
    snapshot = store.snapshot()
    assert snapshot == {"receivedAt": 100_000, "report": report(), "stale": False}
    snapshot["report"]["primary"]["page"] = 99
    assert store.snapshot()["report"] == report()
    monkeypatch.setattr(scanner_control.time, "time", lambda: 221)
    assert store.snapshot()["stale"] is True
    assert ScannerStatusStore(store.path).snapshot()["report"] == report()
    changed = {"receivedAt": 220_000, "report": report()}
    changed["report"]["primary"]["page"] = 3
    store.path.write_text(json.dumps(changed), encoding="utf-8")
    assert store.snapshot() == {**changed, "stale": False}


def test_bad_files_are_bounded_and_never_replaced_with_a_cached_copy(tmp_path):
    store = ScannerSettingsStore(tmp_path / "scanner-settings.json")
    store.save(settings())
    duplicate = (
        json.dumps(settings())
        .replace(
            '"schemaVersion": 1',
            '"schemaVersion": 1,"schemaVersion": 1',
        )
        .encode()
    )
    for encoded in [b"private bad JSON", b" " * (16 * 1024 + 1), duplicate]:
        store.path.write_bytes(encoded)
        with pytest.raises(ValueError) as error:
            store.read()
        assert "private" not in str(error.value)


def test_concurrent_command_writes_remain_complete_and_restartable(tmp_path):
    store = ScannerCommandsStore(tmp_path / "scanner-commands.json")
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(
            executor.map(
                lambda index: store.save({**commands(), "restartHomeId": f"job_{index}"}), range(20)
            )
        )
    assert ScannerCommandsStore(store.path).read() == store.read()
    assert list(tmp_path.iterdir()) == [store.path]


def test_failed_status_replace_preserves_the_last_report_in_memory_and_on_disk(
    tmp_path,
    monkeypatch,
):
    store = ScannerStatusStore(tmp_path / "scanner-status.json")
    store.record_report(report())
    before = store.snapshot()
    encoded = store.path.read_bytes()
    updated = report()
    updated["primary"]["page"] = 8

    def fail_replace(*args):
        raise OSError("private replacement failure")

    monkeypatch.setattr(scanner_control.os, "replace", fail_replace)
    with pytest.raises(OSError):
        store.record_report(updated)
    assert store.path.read_bytes() == encoded
    assert store.snapshot() == before
    assert list(tmp_path.iterdir()) == [store.path]


@pytest.mark.parametrize(
    "store_type,factory,maximum",
    [
        (ScannerSettingsStore, settings, 16 * 1024),
        (ScannerCommandsStore, commands, 16 * 1024),
    ],
)
def test_file_boundary_accepts_exact_limit_and_rejects_one_extra_byte(
    tmp_path,
    store_type,
    factory,
    maximum,
):
    store = store_type(tmp_path / "control.json")
    encoded = json.dumps(factory()).encode()
    store.path.write_bytes(encoded + b" " * (maximum - len(encoded)))
    assert store.read() == factory()
    store.path.write_bytes(store.path.read_bytes() + b" ")
    with pytest.raises(ValueError):
        store.read()


def test_status_memory_mode_and_invalid_disk_snapshots_are_bounded(tmp_path):
    memory = ScannerStatusStore(":memory:")
    memory.record_report(report())
    assert memory.snapshot()["report"] == report()
    disk = ScannerStatusStore(tmp_path / "scanner-status.json")
    for encoded in [
        b" " * (64 * 1024 + 1),
        b'{"receivedAt":1,"receivedAt":2,"report":null}',
        b'{"receivedAt":true,"report":null}',
    ]:
        disk.path.write_bytes(encoded)
        with pytest.raises(ValueError, match="^Invalid scanner status$"):
            disk.snapshot()
