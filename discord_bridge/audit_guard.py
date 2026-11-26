"""Advisory privacy audit: findings and audit errors warn, nothing is blocked.

On 2026-11-25 a server boost made Discord create its managed Server Booster
role with Add Reactions. The runtime audit flagged it and, because a failing
audit gated every send and was never cached, it re-ran every cycle, used up
the local Discord rate budget and stopped all channel alerts for ~80 minutes.

The audit is now a cached, rate-limited warning. Channel roles and overwrites
remain the access control; this only tells the owner (delivery.jsonl
``privacy_warning`` and ``/health`` ``discord.privacy``) that the server no
longer matches the reviewed policy. One shared guard serves every worker, so
the audit runs at most once per TTL however many callers ask, pass or fail.
"""

from __future__ import annotations

import threading
import time

from .monitoring import emit_event, error_fields

DEFAULT_TTL_SECONDS = 300.0
WARN_INTERVAL_SECONDS = 3600.0
MAX_REPORTED_FINDINGS = 20

_PENDING = {"ok": None, "findings": [], "limitations": [], "pending": True}


class AuditGuard:
    """Call like an auditor; returns the latest assessment and never raises."""

    def __init__(
        self,
        auditor=None,
        *,
        ttl=DEFAULT_TTL_SECONDS,
        warn_interval=WARN_INTERVAL_SECONDS,
        event_sink=None,
        clock=None,
        wall_clock=None,
        background=False,
    ):
        self.auditor = auditor
        # background=True: a stale check starts one audit thread and returns the
        # previous result at once, so no caller ever waits on audit reads.
        self.background = bool(background)
        self._thread = None
        self.ttl = max(0.0, float(ttl))
        self.warn_interval = max(0.0, float(warn_interval))
        self.event_sink = event_sink
        self.clock = clock or time.monotonic
        self.wall_clock = wall_clock or time.time
        self._lock = threading.Lock()
        self._running = threading.Lock()
        self._assessment = None  # last completed audit
        self._result = None  # what callers get until _until (may be an error)
        self._until = None
        self._checked_at = None
        self._audits = 0
        self._error = None
        self._error_at = None
        self._findings_key = None
        self._findings_warned_at = None
        self._error_key = None
        self._error_warned_at = None

    @classmethod
    def wrap(cls, auditor, **kwargs):
        """Reuse a shared guard; give a plain auditor (or None) its own guard."""
        return auditor if isinstance(auditor, cls) else cls(auditor, **kwargs)

    def __call__(
        self, client, guild_id, state, *, caller="other", stop_event=None, interrupts=()
    ):
        return self.check(
            client, guild_id, state, caller=caller, stop_event=stop_event, interrupts=interrupts
        )

    def _cached(self, now):
        if self._result is not None and self._until is not None and now < self._until:
            return self._result
        return None

    def check(
        self, client, guild_id, state, *, caller="other", stop_event=None, interrupts=()
    ):
        """The latest assessment. Callers must not gate any work on it.

        A cached result (pass, findings or error) is reused for ``ttl``. While
        another thread is auditing, the previous result returns at once. A
        set ``stop_event`` or an exception in ``interrupts`` (a caller's own
        "not now", such as yielding to paid delivery) is neither cached nor
        reported, so the next call simply audits again.
        """
        args = (client, guild_id, state, caller, stop_event, tuple(interrupts))
        with self._lock:
            cached = self._cached(self.clock())
            if cached is not None:
                return cached
        if not self._running.acquire(blocking=False):
            with self._lock:
                return self._assessment or dict(_PENDING)
        if self.background:
            try:
                thread = threading.Thread(
                    target=self._run_and_release, args=args,
                    name="discord-privacy-audit", daemon=True,
                )
                self._thread = thread
                thread.start()
            except Exception:
                self._running.release()
            with self._lock:
                return self._assessment or dict(_PENDING)
        try:
            with self._lock:
                cached = self._cached(self.clock())
                if cached is not None:
                    return cached
            return self._audit(*args)
        finally:
            self._running.release()

    def _run_and_release(self, *args):
        try:
            with self._lock:
                if self._cached(self.clock()) is not None:
                    return
            self._audit(*args)
        finally:
            self._running.release()

    def join(self, timeout=None):
        """Wait for a background audit (tests and orderly shutdown)."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _audit(self, client, guild_id, state, caller, stop_event, interrupts):
        try:
            auditor = self.auditor
            if auditor is None:
                from .provision import audit as auditor
            result = auditor(client, guild_id, state)
            if not isinstance(result, dict):
                raise TypeError("auditor returned no assessment")
            findings = result.get("findings")
            findings = [str(f) for f in findings] if isinstance(findings, list) else []
            ok = result.get("ok") is True and not findings
            if not ok and not findings:
                findings = ["Privacy audit did not pass."]
            limitations = result.get("limitations")
            assessment = {
                "ok": ok,
                "findings": findings,
                "limitations": list(limitations) if isinstance(limitations, list) else [],
            }
        except Exception as exc:
            if (stop_event is not None and stop_event.is_set()) or isinstance(exc, interrupts):
                # Shutdown or the caller interrupted the read; nothing was learned.
                with self._lock:
                    return self._assessment or dict(_PENDING)
            return self._failed(exc, caller)
        self._completed(assessment, caller)
        return assessment

    def _completed(self, assessment, caller):
        now = self.clock()
        warn = cleared = False
        with self._lock:
            self._assessment = self._result = assessment
            self._until = now + self.ttl
            self._checked_at = self.wall_clock()
            self._audits += 1
            self._error = self._error_at = None
            self._error_key = self._error_warned_at = None
            if assessment["ok"]:
                cleared = self._findings_key is not None
                self._findings_key = self._findings_warned_at = None
            else:
                key = tuple(sorted(assessment["findings"]))
                if (
                    key != self._findings_key
                    or self._findings_warned_at is None
                    or now - self._findings_warned_at >= self.warn_interval
                ):
                    warn = True
                    self._findings_key, self._findings_warned_at = key, now
        findings = assessment["findings"]
        if warn:
            emit_event(
                self.event_sink,
                "privacy_warning",
                reason="privacy_audit",
                phase="audit",
                caller=caller,
                blocked=False,
                findings=findings[:MAX_REPORTED_FINDINGS],
                findings_count=len(findings),
            )
        elif cleared:
            emit_event(
                self.event_sink, "privacy_cleared", reason="privacy_audit", phase="audit",
                caller=caller,
            )

    def _failed(self, exc, caller):
        now = self.clock()
        status = getattr(exc, "status", None)
        key = (type(exc).__name__, status if type(status) is int else None)
        with self._lock:
            # Errors are cached too, so a failing provider is read once per TTL.
            # Callers keep the last known findings meanwhile.
            self._result = self._assessment or {
                "ok": None,
                "findings": [],
                "limitations": [],
                "error": type(exc).__name__,
            }
            result = self._result
            self._until = now + self.ttl
            self._error, self._error_at = type(exc).__name__, self.wall_clock()
            warn = (
                key != self._error_key
                or self._error_warned_at is None
                or now - self._error_warned_at >= self.warn_interval
            )
            if warn:
                self._error_key, self._error_warned_at = key, now
        if warn:
            emit_event(
                self.event_sink,
                "privacy_warning",
                reason="audit_error",
                phase="audit",
                caller=caller,
                blocked=False,
                **error_fields(exc),
            )
        return result

    def status(self):
        """Owner-facing summary for /health; role/channel IDs only, no secrets."""
        with self._lock:
            assessment = self._assessment
            now = self.clock()
            findings = list(assessment["findings"]) if assessment else []
            return {
                "mode": "advisory",
                "blocks_delivery": False,
                "ok": assessment.get("ok") if assessment else None,
                "findings": findings[:MAX_REPORTED_FINDINGS],
                "findings_count": len(findings),
                "last_checked_at": self._checked_at,
                "last_error": self._error,
                "last_error_at": self._error_at,
                "audits": self._audits,
                "ttl_seconds": self.ttl,
                "next_check_in_seconds": round(max(0.0, self._until - now), 1)
                if self._until is not None
                else 0.0,
            }
