"""Host-only recovery for a missing browser heartbeat and local notifier.

This supervisor opens the installed reviewer profile, starts existing Docker
services, and respects intentional stops and verification pauses. It never
interacts with verification controls, changes browser security, or kills tabs.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from relay_settings import RelaySettingsStore

log = logging.getLogger("browser-recovery")
REVIEWER_URL = "https://www.walmart.com/reviews/claim-product"
HEALTH_URL = "http://127.0.0.1:8787/health"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DEFAULT_CONFIG = {
    "enabled": True,
    "chromeProfileDirectory": "auto",
    "staleHeartbeatSeconds": 180,
    "launchCooldownSeconds": 600,
    "pollSeconds": 30,
    "maxChromeLaunchesPerHour": 3,
    "startDockerDesktop": True,
}
_PROFILE = re.compile(r"Default|Profile [1-9][0-9]{0,3}")
_CONFIG_ERROR = "Invalid browser recovery configuration"
_STARTUP_GRACE = 180
_BACKEND_RETRY = 120
_DOCKER_RETRY = 600
_MAX_JSON = 16 * 1024


def _read_json(path: Path, limit: int = _MAX_JSON):
    with path.open("rb") as handle:
        encoded = handle.read(limit + 1)
    if len(encoded) > limit:
        raise ValueError("Invalid recovery JSON")
    try:
        return json.loads(encoded.decode("utf-8-sig"))
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("Invalid recovery JSON") from None


def load_config(root: Path) -> dict:
    try:
        value = _read_json(root / "data" / "browser-recovery.json")
    except FileNotFoundError:
        return dict(DEFAULT_CONFIG)
    except ValueError:
        raise ValueError(_CONFIG_ERROR) from None
    if type(value) is not dict or not value.keys() <= DEFAULT_CONFIG.keys():
        raise ValueError(_CONFIG_ERROR)
    config = {**DEFAULT_CONFIG, **value}
    for field in ("enabled", "startDockerDesktop"):
        if type(config[field]) is not bool:
            raise ValueError(_CONFIG_ERROR)
    profile = config["chromeProfileDirectory"]
    if type(profile) is not str or (profile != "auto" and _PROFILE.fullmatch(profile) is None):
        raise ValueError(_CONFIG_ERROR)
    ranges = {
        "staleHeartbeatSeconds": (30, 86400), "launchCooldownSeconds": (120, 86400),
        "pollSeconds": (5, 300), "maxChromeLaunchesPerHour": (1, 12),
    }
    for field, (minimum, maximum) in ranges.items():
        number = config[field]
        if type(number) is not int or not minimum <= number <= maximum:
            raise ValueError(_CONFIG_ERROR)
    return config


def _default_state() -> dict:
    return {
        "lastLaunch": 0, "launches": [], "outageLaunched": False,
        "pausedUntil": 0, "waitingVerification": False, "refreshDisabled": False,
        "lastBackendAttempt": 0, "lastDockerLaunch": 0, "lastHeartbeatAt": 0,
    }


def _finite_number(value: object) -> bool:
    return type(value) in (int, float) and 0 <= value <= 1e15 and math.isfinite(value)


def _load_state(root: Path) -> dict:
    try:
        state = _read_json(root / "data" / "browser-recovery-state.json")
        if type(state) is not dict:
            raise ValueError("Invalid state")
        expected = _default_state().keys()
        if state.keys() == expected - {"lastHeartbeatAt"}:
            # Preserve intentional stops and known verification across upgrades
            # from the original exact state schema.
            state["lastHeartbeatAt"] = 0
        if state.keys() != expected:
            raise ValueError("Invalid state")
        for field in ("outageLaunched", "waitingVerification", "refreshDisabled"):
            if type(state[field]) is not bool:
                raise ValueError("Invalid state")
        for field in (
            "lastLaunch", "pausedUntil", "lastBackendAttempt", "lastDockerLaunch",
            "lastHeartbeatAt",
        ):
            if not _finite_number(state[field]):
                raise ValueError("Invalid state")
        launches = state["launches"]
        if type(launches) is not list or len(launches) > 32:
            raise ValueError("Invalid state")
        if not all(_finite_number(stamp) for stamp in launches):
            raise ValueError("Invalid state")
        return state
    except FileNotFoundError:
        return _default_state()
    except (OSError, ValueError):
        log.warning("recovery-state-unavailable")
        return _default_state()


def _save_state(root: Path, state: dict) -> None:
    path = root / "data" / "browser-recovery-state.json"
    encoded = (json.dumps(state, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if len(encoded) > _MAX_JSON:
        raise ValueError("Invalid recovery state")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".browser-recovery-state.", suffix=".tmp",
            mode="wb", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                log.warning("recovery-state-cleanup-unavailable")


@dataclass(frozen=True)
class BrowserSignal:
    age: float
    phase: str
    refresh: int | float | None
    paused_until: float
    reachable: bool


def browser_signal(health: object, now: float) -> BrowserSignal | None:
    if not isinstance(health, dict) or not isinstance(health.get("runtime"), dict):
        return None
    value = health["runtime"].get("browser")
    if not isinstance(value, dict) or not _finite_number(value.get("age_seconds")):
        return None
    refresh = value.get("refresh_minutes")
    if not _finite_number(refresh) or refresh > 120:
        refresh = None
    paused = value.get("paused_until", 0)
    if not _finite_number(paused) or paused > (now + 7 * 86400) * 1000:
        paused = 0
    return BrowserSignal(
        age=value["age_seconds"], phase=value.get("phase", ""), refresh=refresh,
        paused_until=paused / 1000, reachable=value.get("reachable") is True,
    )


def recovery_decision(config, state, signal, chrome_running, now, started_at, refresh=None) -> str:
    """Pure launch decision, using the heartbeat rather than observed item age."""
    if not config["enabled"]:
        return "disabled"
    if refresh == 0 or (refresh is None and state["refreshDisabled"]):
        return "refresh-disabled"
    if state["waitingVerification"] or state["pausedUntil"] > now:
        return "verification-paused"
    if signal is not None and signal.age <= config["staleHeartbeatSeconds"]:
        return "healthy"
    if chrome_running and signal is None:
        last_heartbeat = state["lastHeartbeatAt"]
        if last_heartbeat and now - last_heartbeat <= config["staleHeartbeatSeconds"]:
            return "heartbeat-grace"
        if not last_heartbeat and now - started_at < _STARTUP_GRACE:
            return "startup-grace"
    if chrome_running and state["outageLaunched"]:
        return "outage-already-handled"
    if state["lastLaunch"] and now - state["lastLaunch"] < config["launchCooldownSeconds"]:
        return "launch-cooldown"
    recent = [stamp for stamp in state["launches"] if now - 3600 < stamp <= now]
    if len(recent) >= config["maxChromeLaunchesPerHour"]:
        return "launch-hourly-limit"
    return "launch-chrome"


class RecoverySupervisor:
    def __init__(self, root: Path, *, platform=None, now=None, dry_run=False):
        self.root = Path(root)
        self.platform = platform if platform is not None else HostPlatform()
        self.now = now if now is not None else time.time
        self.dry_run = dry_run
        self.started_at = self.now()
        self.state = _load_state(self.root)
        self._persisted = deepcopy(self.state)
        self.config = dict(DEFAULT_CONFIG)

    def _persist(self):
        if not self.dry_run and self.state != self._persisted:
            _save_state(self.root, self.state)
            self._persisted = deepcopy(self.state)

    def _observe(self, signal, now):
        self.state["launches"] = [
            stamp for stamp in self.state["launches"] if now - 3600 < stamp <= now
        ]
        if signal is None:
            return
        # Server browser state is deliberately cleared on restart. Preserve
        # the real heartbeat time, accounting for its reported age, so a short
        # backend gap cannot immediately produce another reviewer tab.
        self.state["lastHeartbeatAt"] = max(
            self.state["lastHeartbeatAt"], max(0, now - signal.age),
        )
        if signal.phase == "waiting-verification":
            self.state["waitingVerification"] = True
        # A stale stopped scanner remains intentionally stopped. A positive
        # JSON setting can override this, or a fresh enabled heartbeat clear it.
        if signal.refresh == 0:
            self.state["refreshDisabled"] = True
        self.state["pausedUntil"] = max(self.state["pausedUntil"], signal.paused_until)
        if signal.age <= self.config["staleHeartbeatSeconds"]:
            self.state["refreshDisabled"] = signal.refresh == 0
            if signal.phase != "waiting-verification":
                self.state["waitingVerification"] = False
                self.state["pausedUntil"] = signal.paused_until
            if signal.reachable:
                self.state["outageLaunched"] = False

    def _recover_backend(self, now):
        if self.dry_run:
            log.info("would-check-local-notifier")
            return
        previous = self.state["lastBackendAttempt"]
        if previous and now - previous < _BACKEND_RETRY:
            return
        self.state["lastBackendAttempt"] = now
        self._persist()
        if self.platform.docker_ready(self.root):
            result = self.platform.compose_up(self.root)
            log.info("notifier-start-requested" if result else "notifier-start-failed")
            return
        previous = self.state["lastDockerLaunch"]
        if self.config["startDockerDesktop"] and (not previous or now - previous >= _DOCKER_RETRY):
            self.state["lastDockerLaunch"] = now
            self._persist()
            result = self.platform.start_docker()
            log.info("docker-start-requested" if result else "docker-start-unavailable")

    def tick(self) -> str:
        try:
            self.config = load_config(self.root)
        except (OSError, ValueError):
            return "invalid-config"
        if not self.config["enabled"]:
            return "disabled"
        inspected_profile = None
        if self.dry_run:
            inspected_profile = self.platform.find_profile(
                self.root, self.config["chromeProfileDirectory"],
            )
            if inspected_profile is None:
                log.info("chrome-profile-unavailable")
            else:
                log.info("chrome-profile-selected profile=%s", inspected_profile)
        try:
            saved = RelaySettingsStore(self.root / "data" / "relay-settings.json").read()
        except (OSError, ValueError):
            return "invalid-relay-settings"
        refresh = saved["refreshMinutes"] if saved is not None else None
        now = self.now()
        health = self.platform.health(self.root)
        signal = browser_signal(health, now)
        self._observe(signal, now)
        decision = recovery_decision(
            self.config, self.state, signal, self.platform.chrome_running(), now,
            self.started_at, refresh,
        )
        self._persist()
        # A running notifier can receive the heartbeat that clears a saved
        # pause. Restoring that local backend never opens or reloads a tab.
        if health is None:
            self._recover_backend(now)
        if decision in ("disabled", "refresh-disabled", "verification-paused"):
            return decision
        if decision != "launch-chrome":
            return decision
        executable = self.platform.find_chrome()
        if executable is None:
            return "chrome-unavailable"
        profile = inspected_profile if self.dry_run else self.platform.find_profile(
            self.root, self.config["chromeProfileDirectory"],
        )
        if profile is None:
            return "chrome-profile-unavailable"
        if not self.dry_run:
            log.info("chrome-profile-selected profile=%s", profile)
        if self.dry_run:
            return "would-launch-chrome"
        # Commit the attempt first: a supervisor crash must not create an
        # unlimited stream of duplicate tabs after login.
        self.state["lastLaunch"] = now
        self.state["launches"].append(now)
        self.state["outageLaunched"] = True
        self._persist()
        if self.platform.launch_chrome(executable, profile):
            return "chrome-launched"
        # A confirmed failure did not create a tab. Preserve attempt budgets,
        # but allow a later retry after cooldown even if another Chrome runs.
        self.state["outageLaunched"] = False
        self._persist()
        return "chrome-launch-failed"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, url):
        # Never forward the local ingest token to a redirected destination.
        return None


class HostPlatform:
    """Only fixed local services and official installed browser paths are used."""

    def health(self, root: Path) -> dict | None:
        token = ""
        try:
            with (root / "notifier.env").open("r", encoding="utf-8-sig") as handle:
                for line in handle.read(65537).splitlines():
                    key, separator, value = line.partition("=")
                    if separator and key.strip() == "INGEST_TOKEN":
                        token = value.strip().strip("\"'")
                        break
            if len(token) > 4096:
                return None
        except FileNotFoundError:
            pass
        except (OSError, UnicodeError):
            return None
        try:
            request = urllib.request.Request(HEALTH_URL, headers={"X-Ingest-Token": token})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
            with opener.open(request, timeout=5) as response:
                encoded = response.read(128 * 1024 + 1)
            if len(encoded) > 128 * 1024:
                return None
            value = json.loads(encoded)
            return value if isinstance(value, dict) else None
        except (OSError, ValueError, RecursionError, urllib.error.URLError):
            return None

    def _process_running(self, name: str) -> bool:
        try:
            # Bounded read, even if a machine has an unusually large process
            # table. Command output is never retained in logs or state.
            with tempfile.TemporaryFile() as output_file:
                result = subprocess.run(
                    ["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
                    timeout=10, stdout=output_file, stderr=subprocess.DEVNULL,
                    shell=False, creationflags=NO_WINDOW,
                )
                output_file.seek(0)
                output = output_file.read(65536).lower()
            return result.returncode == 0 and (f'"{name.lower()}"'.encode() in output)
        except (OSError, subprocess.TimeoutExpired):
            return False

    def chrome_running(self) -> bool:
        return self._process_running("chrome.exe")

    def find_chrome(self) -> Path | None:
        candidates = [
            Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) /
            "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) /
            "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        ]
        return next((path for path in candidates if path.is_absolute() and path.is_file()), None)

    def chrome_user_data(self) -> Path:
        return Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/User Data"

    def find_profile(self, root: Path, selected: str) -> str | None:
        data = self.chrome_user_data()
        if selected != "auto":
            return selected if _PROFILE.fullmatch(selected) and (data / selected).is_dir() else None
        target = os.path.normcase(str((root / "extension").resolve()))
        try:
            profiles = [path for path in data.iterdir() if _PROFILE.fullmatch(path.name)][:50]
        except OSError:
            return None
        matches = set()
        for profile in sorted(profiles, key=lambda path: (path.name != "Default", path.name)):
            for name in ("Secure Preferences", "Preferences"):
                try:
                    prefs = _read_json(profile / name, limit=8 * 1024 * 1024)
                    extensions = prefs.get("extensions", {}).get("settings", {})
                    if not isinstance(extensions, dict):
                        continue
                    for details in extensions.values():
                        if not isinstance(details, dict):
                            continue
                        # Chrome now omits empty disable reasons and may omit
                        # legacy state entirely. Only explicit disablement
                        # excludes an otherwise matching installed extension.
                        if (type(details.get("state")) is int and details["state"] == 0) or (
                            details.get("disable_reasons")
                        ):
                            continue
                        path = details.get("path")
                        if not isinstance(path, str):
                            continue
                        if os.path.normcase(str(Path(path).resolve())) == target:
                            matches.add(profile.name)
                except (OSError, ValueError, AttributeError, RuntimeError):
                    continue
        # Multiple preference files may describe the same profile. Multiple
        # enabled profiles require an explicit choice in recovery settings.
        return next(iter(matches)) if len(matches) == 1 else None

    def _launch(self, argv: list[str]) -> bool:
        try:
            subprocess.Popen(
                argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, shell=False, creationflags=NO_WINDOW,
            )
            return True
        except OSError:
            return False

    def launch_chrome(self, executable: Path, profile: str) -> bool:
        if _PROFILE.fullmatch(profile) is None:
            return False
        return self._launch([str(executable), f"--profile-directory={profile}", REVIEWER_URL])

    def _docker_command(self, root: Path, args: list[str]) -> bool:
        executable = shutil.which("docker")
        if executable is None:
            return False
        try:
            result = subprocess.run(
                [executable, *args], cwd=root, timeout=60, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False,
                creationflags=NO_WINDOW,
            )
            return result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def docker_ready(self, root: Path) -> bool:
        return self._docker_command(root, ["info"])

    def compose_up(self, root: Path) -> bool:
        return self._docker_command(root, ["compose", "up", "-d", "--no-build"])

    def start_docker(self) -> bool:
        if self._process_running("Docker Desktop.exe"):
            return True
        candidates = [
            Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) /
            "Docker/Docker/Docker Desktop.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) /
            "Programs/DockerDesktop/Docker Desktop.exe",
        ]
        installed = next((path for path in candidates if path.is_file()), None)
        return installed is not None and installed.is_absolute() and self._launch([str(installed)])


class InstanceLock:
    """OS advisory lock; an old on-disk lock filename is harmless."""

    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            if self.path.stat().st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self.handle = handle
        return True

    def release(self):
        if self.handle is None:
            return
        handle, self.handle = self.handle, None
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def main(argv=None) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from discord_bridge.rolling_logs import RollingLogHandler

    parser = argparse.ArgumentParser(description="Recover the local reviewer browser heartbeat")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    (root / "data").mkdir(parents=True, exist_ok=True)
    log.setLevel(logging.INFO)
    file_handler = RollingLogHandler(
        root / "data" / "browser-recovery.log", max_bytes=256 * 1024,
        total_bytes=2 * 1024 * 1024,
    )
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(file_handler)
    # pythonw.exe intentionally has no stdout/stderr when launched at login.
    stream_handler = None
    if sys.stdout is not None:
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(stream_handler)
    lock = InstanceLock(root / "data" / "browser-recovery.lock")
    try:
        if not lock.acquire():
            log.info("recovery-already-running")
            return 0
        runner = RecoverySupervisor(root, dry_run=args.dry_run)
        previous = None
        while True:
            try:
                result = runner.tick()
                if result != previous or args.once:
                    log.info("%s", result)
                    previous = result
            except (OSError, ValueError, RuntimeError):
                log.warning("recovery-check-failed")
                if args.once:
                    return 1
            if args.once:
                return 0
            time.sleep(runner.config["pollSeconds"])
    except KeyboardInterrupt:
        return 0
    finally:
        lock.release()
        log.removeHandler(file_handler)
        file_handler.close()
        if stream_handler is not None:
            log.removeHandler(stream_handler)
            stream_handler.close()


if __name__ == "__main__":
    raise SystemExit(main())
