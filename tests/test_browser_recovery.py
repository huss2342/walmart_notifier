"""Host recovery responds to missing extension heartbeats without fighting pauses."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import browser_recovery as recovery

NOW = 1_792_555_200.0


def heartbeat(age=0, **changes):
    browser = {
        "age_seconds": age, "phase": "ready", "refresh_minutes": 10,
        "paused_until": 0, "reachable": True,
    }
    browser.update(changes)
    return {"runtime": {"browser": browser, "last_successful_relay": {"age_seconds": 90000}}}


class FakePlatform:
    def __init__(self, health=None, running=False):
        self.report = health
        self.running = running
        self.calls = []
        self.docker_is_ready = True
        self.launch_ok = True
        self.profile = "Default"

    def health(self, root):
        return self.report

    def chrome_running(self):
        return self.running

    def docker_ready(self, root):
        self.calls.append("docker-info")
        return self.docker_is_ready

    def start_docker(self):
        self.calls.append("docker-desktop")
        return True

    def compose_up(self, root):
        self.calls.append("compose-up")
        return True

    def find_chrome(self):
        return Path("chrome.exe")

    def find_profile(self, root, selected):
        return self.profile

    def launch_chrome(self, executable, profile):
        self.calls.append("chrome")
        return self.launch_ok


def supervisor(tmp_path, platform, clock, **options):
    return recovery.RecoverySupervisor(
        tmp_path, platform=platform, now=lambda: clock[0], **options,
    )


def write_config(tmp_path, value):
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "browser-recovery.json").write_text(json.dumps(value), encoding="utf-8")


def test_absent_config_uses_documented_defaults_without_creating_it(tmp_path):
    config = recovery.load_config(tmp_path)
    assert config == {
        "enabled": True, "chromeProfileDirectory": "auto", "staleHeartbeatSeconds": 180,
        "launchCooldownSeconds": 600, "pollSeconds": 30, "maxChromeLaunchesPerHour": 3,
        "startDockerDesktop": True,
    }
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("value", [
    [], {"enabled": "true"}, {"pollSeconds": True}, {"pollSeconds": 0},
    {"chromeProfileDirectory": "../private"}, {"chromeProfileDirectory": "Profile X"},
    {"executable": "private.exe"}, {"url": "https://private.example"},
    {"staleHeartbeatSeconds": float("nan")}, {"maxChromeLaunchesPerHour": 1000},
])
def test_bad_config_is_rejected_without_echoing_values(tmp_path, value):
    write_config(tmp_path, value)
    with pytest.raises(ValueError, match="^Invalid browser recovery configuration$"):
        recovery.load_config(tmp_path)


def test_fresh_quiet_heartbeat_never_launches_because_items_are_old(tmp_path):
    platform = FakePlatform(heartbeat(), running=True)
    runner = supervisor(tmp_path, platform, [NOW])
    assert runner.tick() == "healthy"
    assert platform.calls == []


def test_missing_chrome_launches_without_waiting_for_startup_grace(tmp_path):
    platform = FakePlatform(heartbeat(500), running=False)
    runner = supervisor(tmp_path, platform, [NOW])
    assert runner.tick() == "chrome-launched"
    assert platform.calls == ["chrome"]


def test_existing_chrome_missing_heartbeat_has_three_minute_startup_grace(tmp_path):
    platform = FakePlatform({"runtime": {"browser": None}}, running=True)
    clock = [NOW]
    runner = supervisor(tmp_path, platform, clock)
    assert runner.tick() == "startup-grace"
    clock[0] += 179
    assert runner.tick() == "startup-grace"
    clock[0] += 1
    assert runner.tick() == "chrome-launched"


def test_stale_heartbeat_existing_chrome_launches_once_then_rearms_after_fresh_report(tmp_path):
    platform = FakePlatform(heartbeat(500), running=True)
    clock = [NOW]
    runner = supervisor(tmp_path, platform, clock)
    assert runner.tick() == "chrome-launched"
    clock[0] += 3601
    assert runner.tick() == "outage-already-handled"
    restarted = supervisor(tmp_path, platform, clock)
    assert restarted.tick() == "outage-already-handled"
    platform.report = heartbeat()
    assert restarted.tick() == "healthy"
    platform.report = heartbeat(500)
    assert restarted.tick() == "chrome-launched"
    assert platform.calls.count("chrome") == 2


@pytest.mark.parametrize("gap", [None, {"runtime": {"browser": None}}],
                         ids=["health-unavailable", "browser-cleared"])
def test_recent_heartbeat_prevents_duplicate_chrome_after_backend_gap_and_old_startup(
    tmp_path, gap,
):
    platform = FakePlatform(heartbeat(), running=True)
    clock = [NOW]
    runner = supervisor(tmp_path, platform, clock)
    clock[0] += 1000
    assert runner.tick() == "healthy"
    platform.report = gap
    clock[0] += 30
    assert runner.tick() == "heartbeat-grace"
    assert "chrome" not in platform.calls
    if gap is None:
        assert platform.calls == ["docker-info", "compose-up"]
    clock[0] += 151
    assert runner.tick() == "chrome-launched"


def test_last_real_heartbeat_grace_survives_supervisor_restart_and_expires_on_time(tmp_path):
    platform = FakePlatform(heartbeat(20), running=True)
    clock = [NOW]
    runner = supervisor(tmp_path, platform, clock)
    assert runner.tick() == "healthy"
    platform.report = {"runtime": {"browser": None}}
    clock[0] += 30
    restarted = supervisor(tmp_path, platform, clock)
    assert restarted.tick() == "heartbeat-grace"
    clock[0] += 130
    assert restarted.tick() == "heartbeat-grace"
    clock[0] += 1
    assert restarted.tick() == "chrome-launched"


def test_old_exact_state_schema_migrates_without_losing_known_verification_pause(tmp_path):
    old_state = {
        "lastLaunch": 0, "launches": [], "outageLaunched": False,
        "pausedUntil": NOW + 600, "waitingVerification": True, "refreshDisabled": True,
        "lastBackendAttempt": 0, "lastDockerLaunch": 0,
    }
    (tmp_path / "data").mkdir()
    path = tmp_path / "data" / "browser-recovery-state.json"
    path.write_text(json.dumps(old_state), encoding="utf-8")
    platform = FakePlatform(None, running=True)
    runner = supervisor(tmp_path, platform, [NOW])
    assert runner.state["pausedUntil"] == NOW + 600
    assert runner.state["waitingVerification"] is True
    assert runner.state["refreshDisabled"] is True
    assert runner.state["lastHeartbeatAt"] == 0
    assert runner.tick() == "refresh-disabled"
    assert "chrome" not in platform.calls
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["waitingVerification"] is True
    assert persisted["lastHeartbeatAt"] == 0


def test_missing_chrome_can_restart_after_cooldown_but_obeys_hourly_cap(tmp_path):
    platform = FakePlatform(heartbeat(500), running=False)
    clock = [NOW]
    runner = supervisor(tmp_path, platform, clock)
    assert runner.tick() == "chrome-launched"
    clock[0] += 599
    assert runner.tick() == "launch-cooldown"
    clock[0] += 1
    assert runner.tick() == "chrome-launched"
    clock[0] += 600
    assert runner.tick() == "chrome-launched"
    clock[0] += 600
    assert runner.tick() == "launch-hourly-limit"
    clock[0] += 1801
    assert runner.tick() == "chrome-launched"


def test_refresh_disabled_suppresses_chrome_but_can_restore_the_local_notifier(tmp_path):
    write_config(tmp_path, {})
    (tmp_path / "data" / "relay-settings.json").write_text(json.dumps({
        "schemaVersion": 1, "refreshMinutes": 0, "pageDelaySeconds": 15, "sweepMode": "none",
    }), encoding="utf-8")
    platform = FakePlatform(None, running=False)
    runner = supervisor(tmp_path, platform, [NOW])
    assert runner.tick() == "refresh-disabled"
    assert platform.calls == ["docker-info", "compose-up"]


def test_fresh_refresh_disabled_heartbeat_remains_respected_across_restart(tmp_path):
    platform = FakePlatform(heartbeat(refresh_minutes=0), running=True)
    clock = [NOW]
    assert supervisor(tmp_path, platform, clock).tick() == "refresh-disabled"
    clock[0] += 1000
    platform.report = {"runtime": {"browser": None}}
    assert supervisor(tmp_path, platform, clock).tick() == "refresh-disabled"
    platform.report = heartbeat(refresh_minutes=10)
    runner = supervisor(tmp_path, platform, clock)
    assert runner.tick() == "healthy"


@pytest.mark.parametrize("running", [True, False])
def test_initial_stale_refresh_disabled_signal_blocks_launch_and_survives_restart(
    tmp_path, running,
):
    platform = FakePlatform(heartbeat(500, refresh_minutes=0), running=running)
    clock = [NOW]
    runner = supervisor(tmp_path, platform, clock)
    assert runner.tick() == "refresh-disabled"
    platform.report = {"runtime": {"browser": None}}
    clock[0] += 1000
    assert supervisor(tmp_path, platform, clock).tick() == "refresh-disabled"
    assert "chrome" not in platform.calls


def test_authoritative_positive_settings_override_stale_refresh_disabled_signal(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "relay-settings.json").write_text(json.dumps({
        "schemaVersion": 1, "refreshMinutes": 10, "pageDelaySeconds": 15, "sweepMode": "none",
    }), encoding="utf-8")
    platform = FakePlatform(heartbeat(500, refresh_minutes=0), running=True)
    assert supervisor(tmp_path, platform, [NOW]).tick() == "chrome-launched"


def test_verification_wait_survives_notifier_restart_without_opening_another_tab(tmp_path):
    platform = FakePlatform(heartbeat(phase="waiting-verification", paused_until=0), running=True)
    clock = [NOW]
    runner = supervisor(tmp_path, platform, clock)
    assert runner.tick() == "verification-paused"
    platform.report = {"runtime": {"browser": None}}
    clock[0] += 5000
    assert supervisor(tmp_path, platform, clock).tick() == "verification-paused"
    platform.report = heartbeat(phase="ready")
    assert runner.tick() == "healthy"


def test_known_future_pause_blocks_recovery_until_expiration(tmp_path):
    platform = FakePlatform(heartbeat(paused_until=(NOW + 600) * 1000), running=True)
    clock = [NOW]
    assert supervisor(tmp_path, platform, clock).tick() == "verification-paused"
    platform.report = heartbeat(500)
    clock[0] += 500
    assert supervisor(tmp_path, platform, clock).tick() == "verification-paused"
    clock[0] += 101
    assert supervisor(tmp_path, platform, clock).tick() == "chrome-launched"


def test_docker_recovery_has_bounded_backoff_and_does_not_run_every_tick(tmp_path):
    platform = FakePlatform(None, running=True)
    platform.docker_is_ready = False
    clock = [NOW]
    runner = supervisor(tmp_path, platform, clock)
    runner.tick()
    assert platform.calls == ["docker-info", "docker-desktop"]
    clock[0] += 30
    runner.tick()
    assert platform.calls == ["docker-info", "docker-desktop"]
    clock[0] += 90
    platform.docker_is_ready = True
    runner.tick()
    assert platform.calls == ["docker-info", "docker-desktop", "docker-info", "compose-up"]


def test_dry_run_does_not_launch_compose_or_persist_recovery_attempts(tmp_path):
    platform = FakePlatform(None, running=False)
    runner = supervisor(tmp_path, platform, [NOW], dry_run=True)
    assert runner.tick() == "would-launch-chrome"
    assert platform.calls == []
    assert not (tmp_path / "data" / "browser-recovery-state.json").exists()


def test_disabled_and_bad_config_fail_closed(tmp_path):
    platform = FakePlatform(None, running=False)
    write_config(tmp_path, {"enabled": False})
    runner = supervisor(tmp_path, platform, [NOW])
    assert runner.tick() == "disabled"
    write_config(tmp_path, {"url": "https://private.example"})
    assert runner.tick() == "invalid-config"
    assert platform.calls == []


def test_failed_browser_launch_is_rate_limited_and_can_retry_when_process_is_missing(tmp_path):
    platform = FakePlatform(heartbeat(500), running=False)
    platform.launch_ok = False
    clock = [NOW]
    runner = supervisor(tmp_path, platform, clock)
    assert runner.tick() == "chrome-launch-failed"
    clock[0] += 30
    assert runner.tick() == "launch-cooldown"
    clock[0] += 570
    assert runner.tick() == "chrome-launch-failed"
    assert platform.calls.count("chrome") == 2


def test_confirmed_failed_launch_retries_existing_chrome_after_cooldown_and_restart(tmp_path):
    platform = FakePlatform(heartbeat(500), running=True)
    platform.launch_ok = False
    clock = [NOW]
    runner = supervisor(tmp_path, platform, clock)
    assert runner.tick() == "chrome-launch-failed"
    clock[0] += 30
    restarted = supervisor(tmp_path, platform, clock)
    assert restarted.tick() == "launch-cooldown"
    clock[0] += 570
    assert restarted.tick() == "chrome-launch-failed"
    clock[0] += 600
    assert restarted.tick() == "chrome-launch-failed"
    clock[0] += 600
    assert supervisor(tmp_path, platform, clock).tick() == "launch-hourly-limit"
    assert platform.calls.count("chrome") == 3


def test_missing_profile_does_not_launch_wrong_chrome_profile(tmp_path):
    platform = FakePlatform(heartbeat(500), running=False)
    platform.profile = None
    assert supervisor(tmp_path, platform, [NOW]).tick() == "chrome-profile-unavailable"
    assert platform.calls == []


def test_chrome_launch_uses_fixed_url_profile_argv_without_shell_or_console(monkeypatch):
    calls = []

    def popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(pid=10)

    monkeypatch.setattr(recovery.subprocess, "Popen", popen)
    platform = recovery.HostPlatform()
    assert platform.launch_chrome(Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
                                  "Profile 2")
    argv, options = calls[0]
    assert argv[1:] == [
        "--profile-directory=Profile 2", "https://www.walmart.com/reviews/claim-product",
    ]
    assert options["shell"] is False
    assert options["stdout"] is subprocess.DEVNULL
    assert options["stderr"] is subprocess.DEVNULL
    assert options["creationflags"] == recovery.NO_WINDOW


def test_docker_commands_use_timeout_no_build_and_discard_output(tmp_path, monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(recovery.subprocess, "run", run)
    monkeypatch.setattr(recovery.shutil, "which", lambda command: "docker.exe")
    platform = recovery.HostPlatform()
    assert platform.docker_ready(tmp_path)
    assert platform.compose_up(tmp_path)
    assert calls[1][0] == ["docker.exe", "compose", "up", "-d", "--no-build"]
    for _, options in calls:
        assert options["timeout"] <= 60
        assert options["shell"] is False
        assert options["stdout"] is subprocess.DEVNULL
        assert options["stderr"] is subprocess.DEVNULL
        assert options["cwd"] == tmp_path


def test_auto_profile_discovery_matches_only_our_extension_path(tmp_path, monkeypatch):
    user_data = tmp_path / "chrome-data"
    profiles = (("Default", "somewhere-else"), ("Profile 2", str(tmp_path / "extension")))
    for name, extension in profiles:
        profile = user_data / name
        profile.mkdir(parents=True)
        (profile / "Secure Preferences").write_text(json.dumps({
            "extensions": {"settings": {"id": {"path": extension}}},
        }), encoding="utf-8")
    platform = recovery.HostPlatform()
    monkeypatch.setattr(platform, "chrome_user_data", lambda: user_data)
    assert platform.find_profile(tmp_path, "auto") == "Profile 2"


@pytest.mark.parametrize("disabled", [{"state": 0}, {"disable_reasons": [1]},
                                      {"disable_reasons": 1}])
def test_auto_profile_skips_disabled_default_and_selects_enabled_profile_one(
    tmp_path, monkeypatch, disabled,
):
    user_data = tmp_path / "chrome-data"
    for name, flags in (("Default", disabled), ("Profile 1", {"state": 1})):
        profile = user_data / name
        profile.mkdir(parents=True)
        (profile / "Secure Preferences").write_text(json.dumps({
            "extensions": {"settings": {"id": {"path": str(tmp_path / "extension"), **flags}}},
        }), encoding="utf-8")
    platform = recovery.HostPlatform()
    monkeypatch.setattr(platform, "chrome_user_data", lambda: user_data)
    assert platform.find_profile(tmp_path, "auto") == "Profile 1"


def test_auto_profile_accepts_absent_state_and_disable_reasons_and_deduplicates_files(
    tmp_path, monkeypatch,
):
    user_data = tmp_path / "chrome-data"
    profile = user_data / "Profile 1"
    profile.mkdir(parents=True)
    contents = json.dumps({"extensions": {"settings": {"id": {
        "path": str(tmp_path / "extension"),
    }}}})
    for name in ("Secure Preferences", "Preferences"):
        (profile / name).write_text(contents, encoding="utf-8")
    platform = recovery.HostPlatform()
    monkeypatch.setattr(platform, "chrome_user_data", lambda: user_data)
    assert platform.find_profile(tmp_path, "auto") == "Profile 1"


def test_multiple_enabled_profiles_are_ambiguous_until_explicitly_configured(
    tmp_path, monkeypatch,
):
    user_data = tmp_path / "chrome-data"
    for name in ("Default", "Profile 1"):
        profile = user_data / name
        profile.mkdir(parents=True)
        (profile / "Secure Preferences").write_text(json.dumps({
            "extensions": {"settings": {"id": {
                "path": str(tmp_path / "extension"), "state": 1, "disable_reasons": [],
            }}},
        }), encoding="utf-8")
    platform = recovery.HostPlatform()
    monkeypatch.setattr(platform, "chrome_user_data", lambda: user_data)
    assert platform.find_profile(tmp_path, "auto") is None
    assert platform.find_profile(tmp_path, "Profile 1") == "Profile 1"


def test_single_instance_lock_uses_os_lock_and_allows_existing_unlocked_file(tmp_path):
    path = tmp_path / "browser-recovery.lock"
    path.write_bytes(b"old lock file")
    first = recovery.InstanceLock(path)
    second = recovery.InstanceLock(path)
    assert first.acquire()
    try:
        assert not second.acquire()
    finally:
        first.release()
    assert second.acquire()
    second.release()


def test_paused_outage_can_restart_backend_without_opening_chrome(tmp_path):
    platform = FakePlatform(heartbeat(phase="waiting-verification"), running=True)
    clock = [NOW]
    runner = supervisor(tmp_path, platform, clock)
    assert runner.tick() == "verification-paused"
    platform.report = None
    clock[0] += 1000
    assert supervisor(tmp_path, platform, clock).tick() == "verification-paused"
    assert platform.calls == ["docker-info", "compose-up"]


def test_docker_desktop_start_can_be_disabled_without_repeating_checks_each_tick(tmp_path):
    write_config(tmp_path, {"startDockerDesktop": False})
    platform = FakePlatform(None, running=True)
    platform.docker_is_ready = False
    clock = [NOW]
    runner = supervisor(tmp_path, platform, clock)
    runner.tick()
    clock[0] += 30
    runner.tick()
    assert platform.calls == ["docker-info"]


def test_once_dry_run_reports_only_profile_and_fixed_action_without_launching(
    tmp_path, monkeypatch,
):
    platform = FakePlatform(heartbeat(), running=True)
    platform.profile = "Profile 4"
    monkeypatch.setattr(recovery, "HostPlatform", lambda: platform)
    assert recovery.main(["--root", str(tmp_path), "--once", "--dry-run"]) == 0
    output = (tmp_path / "data" / "browser-recovery.log").read_text(encoding="utf-8")
    assert "chrome-profile-selected profile=Profile 4" in output
    assert "healthy" in output
    assert "https://" not in output
    assert platform.calls == []


@pytest.mark.parametrize("contents", [b"secret invalid JSON", b"x" * 16385])
def test_corrupt_state_warns_with_no_payload_and_does_not_abort_startup(tmp_path, caplog, contents):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "browser-recovery-state.json").write_bytes(contents)
    platform = FakePlatform(heartbeat(), running=True)
    runner = supervisor(tmp_path, platform, [NOW])
    assert runner.tick() == "healthy"
    assert [record.getMessage() for record in caplog.records] == ["recovery-state-unavailable"]


def test_atomic_state_failure_keeps_prior_snapshot_and_removes_temporary_file(
    tmp_path, monkeypatch,
):
    state = recovery._default_state()
    recovery._save_state(tmp_path, state)
    path = tmp_path / "data" / "browser-recovery-state.json"
    before = path.read_bytes()

    def fail_replace(*args):
        raise OSError("write unavailable")

    monkeypatch.setattr(recovery.os, "replace", fail_replace)
    state["lastLaunch"] = NOW
    with pytest.raises(OSError):
        recovery._save_state(tmp_path, state)
    assert path.read_bytes() == before
    assert list(path.parent.iterdir()) == [path]


def test_health_uses_only_ingest_token_fixed_loopback_and_five_second_timeout(
    tmp_path, monkeypatch,
):
    (tmp_path / "notifier.env").write_text(
        "TELEGRAM_TOKEN=unrelated-private-token\nINGEST_TOKEN=private-ingest-token\n",
        encoding="utf-8",
    )
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, limit):
            assert limit == 128 * 1024 + 1
            return json.dumps(heartbeat()).encode()

    def urlopen(request, **kwargs):
        calls.append((request, kwargs))
        return Response()

    def build_opener(*handlers):
        assert any(isinstance(handler, recovery._NoRedirect) for handler in handlers)
        assert any(
            isinstance(handler, recovery.urllib.request.ProxyHandler) for handler in handlers
        )
        return SimpleNamespace(open=urlopen)

    monkeypatch.setattr(recovery.urllib.request, "build_opener", build_opener)
    assert recovery.HostPlatform().health(tmp_path) == heartbeat()
    request, options = calls[0]
    assert request.full_url == "http://127.0.0.1:8787/health"
    assert request.get_header("X-ingest-token") == "private-ingest-token"
    assert options["timeout"] == 5


def test_process_and_docker_failures_return_safe_results_without_logging_exceptions(
    tmp_path, monkeypatch, caplog,
):
    def timeout(argv, **kwargs):
        assert kwargs["timeout"] <= 60
        assert kwargs["shell"] is False
        raise subprocess.TimeoutExpired("private command", 1, output="private output")

    monkeypatch.setattr(recovery.subprocess, "run", timeout)
    monkeypatch.setattr(recovery.shutil, "which", lambda command: "docker.exe")
    platform = recovery.HostPlatform()
    assert not platform.chrome_running()
    assert not platform.docker_ready(tmp_path)
    assert not platform.compose_up(tmp_path)
    assert caplog.records == []


def test_pythonw_mode_runs_without_console_streams(tmp_path, monkeypatch):
    platform = FakePlatform(heartbeat(), running=True)
    monkeypatch.setattr(recovery, "HostPlatform", lambda: platform)
    monkeypatch.setattr(recovery.sys, "stdout", None)
    monkeypatch.setattr(recovery.sys, "stderr", None)
    assert recovery.main(["--root", str(tmp_path), "--once", "--dry-run"]) == 0
    output = (tmp_path / "data" / "browser-recovery.log").read_text(encoding="utf-8")
    assert "healthy" in output
