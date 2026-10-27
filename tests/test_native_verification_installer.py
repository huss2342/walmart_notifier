"""Installer safety is exercised without registering the real native host."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "install-native-verification.ps1"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")


def run_script(command):
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", command],
        text=True,
        capture_output=True,
        timeout=30,
    )


def quoted(path):
    return "'" + str(path).replace("'", "''") + "'"


@pytest.mark.skipif(
    sys.platform != "win32" or not POWERSHELL, reason="Windows PowerShell installer"
)
def test_installer_whatif_does_not_write_registration_or_files(tmp_path):
    # The copied project has no installed host and uses the real .NET compiler.
    (tmp_path / "native_verification").mkdir()
    (tmp_path / ".venv" / "Scripts").mkdir(parents=True)
    for relative in [
        "native_verification/launcher.cs",
        "native_verification/host.py",
        ".venv/Scripts/python.exe",
    ]:
        source = ROOT / relative
        (tmp_path / relative).write_bytes(source.read_bytes())
    copy = tmp_path / INSTALLER.name
    copy.write_bytes(INSTALLER.read_bytes())
    # Hide only the genuine registry read; the isolated copied project must be
    # testable even after the real project has installed its own registration.
    isolated_registry = (
        "function Test-Path {param([string]$LiteralPath,[string]$PathType); "
        "if ($LiteralPath -like 'HKCU:*') {return $false}; "
        "if ($PathType) {Microsoft.PowerShell.Management\\Test-Path "
        "-LiteralPath $LiteralPath -PathType $PathType} else {"
        "Microsoft.PowerShell.Management\\Test-Path -LiteralPath $LiteralPath}}; "
    )
    result = run_script(isolated_registry + f"& {quoted(copy)} -WhatIf")
    assert result.returncode == 0, result.stderr
    assert "What if" in result.stdout
    assert not (tmp_path / "data").exists()


@pytest.mark.skipif(
    sys.platform != "win32" or not POWERSHELL, reason="Windows PowerShell installer"
)
def test_owned_registration_rejects_foreign_manifest_and_accepts_exact_manifest(tmp_path):
    manifest = tmp_path / "host.json"
    launcher = tmp_path / "launcher.exe"
    document = {
        "name": "com.reviewer_item_relay.verification",
        "type": "stdio",
        "path": str(launcher),
        "allowed_origins": ["chrome-extension://oidgbnehgnikmniolojjkmhlpifdoiil/"],
    }
    manifest.write_text(json.dumps(document))
    prefix = f". {quoted(INSTALLER)}; "
    test_owned = (
        f"Test-OwnedNativeRegistration -RegisteredPath {quoted(manifest)} "
        f"-ExpectedManifest {quoted(manifest)} -ExpectedLauncher {quoted(launcher)}"
    )
    result = run_script(prefix + test_owned)
    assert result.returncode == 0 and result.stdout.strip() == "True", result.stderr
    document["allowed_origins"] = ["chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/"]
    manifest.write_text(json.dumps(document))
    result = run_script(prefix + test_owned)
    assert result.returncode == 0 and result.stdout.strip() == "False", result.stderr
    result = run_script(
        prefix
        + "Test-OwnedNativeRegistration -RegisteredPath 'C:\\foreign\\host.json' "
        + f"-ExpectedManifest {quoted(manifest)} -ExpectedLauncher {quoted(launcher)}"
    )
    assert result.returncode == 0 and result.stdout.strip() == "False"


def test_installer_has_no_service_elevation_or_download_and_current_user_only():
    script = INSTALLER.read_text(encoding="utf-8")
    assert "HKCU:\\Software\\Google\\Chrome\\NativeMessagingHosts" in script
    assert "SupportsShouldProcess = $true" in script
    assert "Test-OwnedNativeRegistration" in script
    assert "-LiteralPath" in script
    for forbidden in [
        "Invoke-WebRequest",
        "DownloadFile",
        "RunAs",
        "New-Service",
        "Register-ScheduledTask",
        "HKLM:",
    ]:
        assert forbidden not in script


@pytest.mark.skipif(sys.platform != "win32", reason="Windows .NET launcher")
def test_compiled_launcher_preserves_binary_stdio_and_quoted_arguments(tmp_path):
    root = tmp_path / "project with spaces"
    folder = root / "data" / "native-verification"
    folder.mkdir(parents=True)
    (root / ".venv" / "Scripts").mkdir(parents=True)
    (root / "native_verification").mkdir()
    shutil.copyfile(sys.executable, root / ".venv" / "Scripts" / "python.exe")
    shutil.copyfile(ROOT / ".venv" / "pyvenv.cfg", root / ".venv" / "pyvenv.cfg")
    dummy = root / "native_verification" / "host.py"
    dummy.write_text(
        "import sys,struct,json\n"
        "size=struct.unpack('<I',sys.stdin.buffer.read(4))[0]\n"
        "request=sys.stdin.buffer.read(size)\n"
        "payload=json.dumps({'args':sys.argv[1:],'request':request.decode()}).encode()\n"
        "sys.stdout.buffer.write(struct.pack('<I',len(payload))+payload)\n"
        "sys.stdout.buffer.flush()\n"
    )
    manifest = folder / "host.json"
    manifest.write_text("{}")
    compiler = Path("C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe")
    executable = folder / "launcher.exe"
    compiled = subprocess.run(
        [
            str(compiler),
            "/nologo",
            "/target:exe",
            f"/out:{executable}",
            str(ROOT / "native_verification" / "launcher.cs"),
        ],
        capture_output=True,
        timeout=30,
    )
    assert compiled.returncode == 0, compiled.stderr
    request = b'{"probe":"binary pipes"}'
    import struct

    wire = struct.pack("<I", len(request)) + request
    result = subprocess.run(
        [
            str(executable),
            "chrome-extension://fixture/",
            "--parent-window=123",
            'quoted"arg',
            "trailing\\",
        ],
        input=wire,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    size = struct.unpack("<I", result.stdout[:4])[0]
    response = json.loads(result.stdout[4:])
    assert size == len(result.stdout[4:])
    assert response["request"] == request.decode()
    assert response["args"] == [
        "chrome-extension://fixture/",
        "--parent-window=123",
        'quoted"arg',
        "trailing\\",
        "--host-manifest",
        str(manifest),
    ]
