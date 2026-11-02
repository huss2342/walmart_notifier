"""Canonical protocol fixtures use fake geometry and never perform native input."""

import json
from pathlib import Path

import pytest
from native_verification import contracts
from native_verification.contracts import DesktopError
from native_verification.host import validate_request

ROOT = Path(__file__).resolve().parent.parent
SPEC = json.loads((ROOT / "native_verification" / "protocol.json").read_text())
CASES = json.loads((ROOT / "tests" / "fixtures" / "native-protocol-cases.json").read_text())


@pytest.mark.parametrize("case", CASES["prepare"], ids=lambda case: case["name"])
def test_host_geometry_obeys_the_shared_conservative_protocol(case):
    request = {"v": 1, "id": "fixture", "op": "prepare", "payload": {
        "viewport": case["viewport"], "point": case["point"],
    }}
    if case["valid"]:
        assert validate_request(request) == request
    else:
        with pytest.raises(DesktopError, match="^invalid-request$"):
            validate_request(request)


def test_python_contract_is_loaded_from_the_canonical_protocol():
    assert SPEC["version"] == contracts.PROTOCOL_VERSION
    assert SPEC["hostName"] == contracts.HOST_NAME
    assert frozenset(SPEC["errorCodes"]) == contracts.ERROR_CODES
    assert SPEC["limits"]["messageBytes"] == contracts.MAX_MESSAGE_BYTES
    assert SPEC["limits"]["holdMs"] == contracts.MAX_HOLD_MS
    assert SPEC["limits"]["idleMs"] == contracts.MIN_IDLE_MS
    assert SPEC["limits"]["prepareTtlMs"] == contracts.PREPARE_TTL_MS
    assert SPEC["limits"]["viewportMax"] == contracts.MAX_VIEWPORT
    assert SPEC["limits"]["dprMin"] == contracts.MIN_DPR
    assert SPEC["limits"]["dprMax"] == contracts.MAX_DPR
    assert frozenset(SPEC["operations"]) == contracts.OPS
