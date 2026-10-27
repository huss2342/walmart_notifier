"""Lifecycle for the optional Gateway listener and private request sender."""

from __future__ import annotations

import threading

from .request_setup import request_audit
from .request_store import RequestStore


class RequestRuntime:
    def __init__(
        self,
        client,
        state,
        path,
        *,
        token,
        stop_event=None,
        auditor=None,
        event_sink=None,
        worker_factory=None,
        gateway_factory=None,
    ):
        self.client, self.state, self.path, self.token = client, state, path, token
        self.auditor, self.event_sink = auditor, event_sink
        self._worker_factory, self._gateway_factory = worker_factory, gateway_factory
        self._external_stop = stop_event
        self._stop = threading.Event()
        self._thread = None
        self.worker = self.gateway = None
        self._ready = False
        self._error = None

    def _stopped(self):
        return self._stop.is_set() or (
            self._external_stop is not None and self._external_stop.is_set()
        )

    def prepare(self):
        """Audit and start once; failures disable this feature, retaining feeds."""
        if self._stopped():
            return False
        if self._ready:
            return True
        try:
            if (
                not self.token
                or not self.state.get("channels", {}).get("item-requests")
                or not self.state.get("roles", {}).get("pro")
            ):
                self._error = "configuration"
                return False
            assessment = (
                self.auditor(self.client, self.state["guild_id"], self.state)
                if self.auditor is not None
                else request_audit(self.client, self.state, stop_event=self._stop)
            )
            if not isinstance(assessment, dict) or assessment.get("ok") is not True:
                self._error = "privacy_audit"
                return False
            if self._stopped():
                return False
            from .request_commands import RequestGateway
            from .request_delivery import RequestWorker

            store = RequestStore(self.path, guild_id=self.state["guild_id"])
            worker = (self._worker_factory or RequestWorker)(
                self.client,
                self.state,
                store,
                enabled=True,
                stop_event=self._stop,
                event_sink=self.event_sink,
            )
            gateway = (self._gateway_factory or RequestGateway)(
                self.client,
                self.state,
                store,
                token=self.token,
                enabled=True,
                stop_event=self._stop,
                event_sink=self.event_sink,
            )
            self.worker, self.gateway = worker, gateway
            gateway.start()
            worker.start()
            self._ready, self._error = True, None
            return True
        except Exception as exc:
            self._error = type(exc).__name__
            if self.worker is not None or self.gateway is not None:
                self.stop()
            return False

    def start(self):
        if self._thread is not None or self._stopped():
            return

        def supervise():
            while not self._stopped():
                if self.prepare():
                    return
                self._stop.wait(5)

        self._thread = threading.Thread(target=supervise, name="discord-requests", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        for child in (self.gateway, self.worker):
            if child is not None:
                child.stop()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)
        self._ready = False

    def is_alive(self):
        threads = [self._thread]
        threads.extend(getattr(child, "_thread", None) for child in (self.gateway, self.worker))
        return any(thread is not None and thread.is_alive() for thread in threads)

    def health(self):
        gateway = self.gateway.health() if self.gateway is not None else None
        delivery = self.worker.health() if self.worker is not None else None
        return {
            "enabled": True,
            "ready": bool(
                self._ready
                and not self._stopped()
                and gateway
                and gateway.get("connected") is True
                and delivery
                and delivery.get("running") is True
            ),
            "last_error": self._error,
            "gateway": gateway,
            "delivery": delivery,
        }
