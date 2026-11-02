"""Priority is an additive, bounded schema-1 lane with durable private telemetry."""

import json
from copy import deepcopy

import pytest

import browser_diagnostics
from browser_diagnostics import BrowserDiagnosticsStore
from scanner_control import ScannerSettingsStore, validate_settings, validate_status
from test_logging_browser import NOW, STREAM
from test_logging_browser import report as diagnostic_report
from test_scanner_control import report as status_report
from test_scanner_control import settings as legacy_settings


@pytest.mark.parametrize(
    "optional",
    [
        {},
        {"auxiliaryPriority": False},
        {"auxiliaryPriority": True},
        {"auxiliaryPriorityQueries": None},
        {"auxiliaryPriorityQueries": []},
        {"auxiliaryPriorityQueries": ["Straße", "STRASSE"]},
        {"auxiliaryPriorityQueries": [f"term-{i}" for i in range(24)]},
        {"auxiliaryPriorityRefreshMinutes": 1},
        {"auxiliaryPriorityRefreshMinutes": 120},
        {
            "auxiliaryPriority": True,
            "auxiliaryPriorityQueries": ["Luggage", "Kitchen"],
            "auxiliaryPriorityRefreshMinutes": 2,
        },
    ],
)
def test_optional_priority_settings_roundtrip_without_inserting_absent_keys(tmp_path, optional):
    incoming = {**legacy_settings(), **optional}
    assert validate_settings(incoming) == incoming
    store = ScannerSettingsStore(tmp_path / "scanner-settings.json")
    assert store.save(incoming) == incoming
    assert ScannerSettingsStore(store.path).read() == incoming
    assert json.loads(store.path.read_text(encoding="utf-8")) == incoming


def test_priority_query_normalization_is_independent_of_the_input():
    incoming = {**legacy_settings(), "auxiliaryPriorityQueries": ["  Chair  ", "Équipement"]}
    clean = validate_settings(incoming)
    assert clean["auxiliaryPriorityQueries"] == ["Chair", "Équipement"]
    assert incoming["auxiliaryPriorityQueries"] == ["  Chair  ", "Équipement"]


@pytest.mark.parametrize(
    "query,expected",
    [
        ("\ufeffSofa", "Sofa"),
        ("Sofa\ufeff", "Sofa"),
        (" \ufeff \u2000\ufeffSofa\ufeff \u3000\ufeff ", "Sofa"),
        ("Sofa\ufeffBed", "Sofa\ufeffBed"),
        ("\ufeff" + "a" * 80 + "\ufeff", "a" * 80),
    ],
)
def test_priority_trims_ecmascript_edge_whitespace_without_removing_internal_feff(query, expected):
    incoming = {**legacy_settings(), "auxiliaryPriorityQueries": [query]}
    assert validate_settings(incoming)["auxiliaryPriorityQueries"] == [expected]


@pytest.mark.parametrize(
    "queries",
    [["Sofa", "\ufeffSofa"], ["Sofa", "Sofa\ufeff"], ["Sofa", " \ufeff Sofa\ufeff "]],
)
def test_priority_rejects_duplicates_after_ecmascript_edge_trimming(queries):
    with pytest.raises(ValueError, match="^Invalid scanner settings$"):
        validate_settings({**legacy_settings(), "auxiliaryPriorityQueries": queries})


def test_new_priority_trimming_does_not_change_legacy_keyword_whitespace():
    incoming = {**legacy_settings(), "auxiliaryKeywordQueries": ["Sofa", "\ufeffSofa"]}
    assert validate_settings(incoming) == incoming


@pytest.mark.parametrize(
    "field,value",
    [
        ("auxiliaryPriority", value) for value in (None, 0, 1, "false", [], {})
    ]
    + [
        ("auxiliaryPriorityRefreshMinutes", value)
        for value in (None, False, True, 0, 121, -1, 1.0, "1")
    ]
    + [
        ("auxiliaryPriorityQueries", value)
        for value in (
            "Chair",
            {},
            [None],
            [1],
            [""],
            ["  "],
            [" \ufeff \u2000\ufeff "],
            ["a" * 81],
            [f"term-{i}" for i in range(25)],
            ["Chair", " chair "],
            ["Chair\x00"],
            ["Chair\x85"],
            ["\tChair\n"],
            ["Chair\ud800"],
        )
    ],
)
def test_invalid_priority_settings_are_bounded_and_errors_do_not_echo_input(field, value):
    with pytest.raises(ValueError, match="^Invalid scanner settings$"):
        validate_settings({**legacy_settings(), field: value})


def test_additive_settings_still_require_every_legacy_key_and_reject_unknown_keys():
    incoming = {**legacy_settings(), "auxiliaryPriority": True}
    for key in legacy_settings():
        missing = incoming.copy()
        del missing[key]
        with pytest.raises(ValueError):
            validate_settings(missing)
    with pytest.raises(ValueError, match="^Invalid scanner settings$"):
        validate_settings({**incoming, "private-token": "never-echo"})


def three_lane_report():
    incoming = status_report()
    lane = incoming["lanes"][0]
    incoming["lanes"] = [
        {**deepcopy(lane), "lane": name} for name in ("departments", "keywords", "priority")
    ]
    return incoming


def test_three_unique_auxiliary_lanes_and_priority_error_component_are_preserved():
    incoming = three_lane_report()
    incoming["settings"].update(auxiliaryPriority=True, auxiliaryPriorityRefreshMinutes=1)
    incoming["errors"] = [{"component": "priority", "code": "navigation-error"}]
    assert validate_status(incoming) == incoming


def test_legacy_one_and_two_lane_reports_remain_exact():
    for count in (1, 2):
        incoming = three_lane_report()
        incoming["lanes"] = incoming["lanes"][:count]
        assert validate_status(incoming) == incoming


@pytest.mark.parametrize(
    "names",
    [
        ("departments", "keywords", "priority", "priority"),
        ("departments", "priority", "priority"),
        ("departments", "keywords", "private-lane"),
        ("departments", "keywords", "brands"),
    ],
)
def test_status_rejects_excess_duplicate_and_unknown_lanes(names):
    incoming = three_lane_report()
    lane = incoming["lanes"][0]
    incoming["lanes"] = [{**deepcopy(lane), "lane": name} for name in names]
    with pytest.raises(ValueError, match="^Invalid scanner status$"):
        validate_status(incoming)


def test_priority_diagnostics_are_archived_and_durably_acknowledged_after_restart(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(browser_diagnostics.time, "time", lambda: NOW / 1000)
    incoming = diagnostic_report((1, 2))
    for event in incoming["events"]:
        event.update(event="auxiliary-target-start", lane="priority", round=1, targetIndex=0)
    path = tmp_path / "browser-diagnostics.json"
    receipt = {"streamId": STREAM, "lastSequence": 2, "gapEvents": 0}
    assert BrowserDiagnosticsStore(path).record_report(incoming) == receipt
    store = BrowserDiagnosticsStore(path)
    assert store.record_report(incoming) == receipt
    archive = path.with_name("browser-diagnostic-events.jsonl")
    rows = [json.loads(line) for line in archive.read_text().splitlines()]
    assert [row["eventSeq"] for row in rows] == [1, 2]
    assert all(row["lane"] == "priority" for row in rows)
    assert store.snapshot()["report"] == incoming


def test_legacy_diagnostics_still_omit_receipts_and_remain_exact(monkeypatch):
    monkeypatch.setattr(browser_diagnostics.time, "time", lambda: NOW / 1000)
    incoming = diagnostic_report()
    incoming.pop("logging")
    incoming["events"] = [{"at": NOW, "event": "page-loaded", "lane": "keywords"}]
    store = BrowserDiagnosticsStore(":memory:")
    assert store.record_report(incoming) is None
    assert store.snapshot() == {"receivedAt": NOW, "report": incoming}


@pytest.mark.parametrize("private", ["url", "query", "html", "token", "title"])
def test_priority_diagnostic_rejection_never_echoes_arbitrary_page_data(private):
    incoming = diagnostic_report()
    incoming["events"][0].update(lane="priority", **{private: "never-echo-private"})
    store = BrowserDiagnosticsStore(":memory:")
    with pytest.raises(ValueError) as error:
        store.record_report(incoming)
    assert "never-echo-private" not in str(error.value)
    assert store.snapshot()["report"] is None
