"""Azure Functions entry points."""

from __future__ import annotations

import hmac
import json
import logging
import os

import azure.functions as func

from config import load_rules, seed_mode
from notifiers import build_notifier
from pipeline import process
from sources.imap_source import ImapSource
from sources.webhook_source import parse_ingest_payload
from state import SeenStore

log = logging.getLogger(__name__)
app = func.FunctionApp()

MAX_INGEST_BYTES = 1_000_000


def _poll_schedule() -> str:
    # Every two minutes by default: ~21,600 runs/month, comfortably inside the
    # Functions free grant of 1M executions.
    return os.environ.get("POLL_SCHEDULE", "0 */2 * * * *")


@app.function_name(name="poll")
@app.timer_trigger(schedule=_poll_schedule(), arg_name="timer", run_on_startup=False)
def poll(timer: func.TimerRequest) -> None:
    """Scheduled sweep of every configured source."""
    if timer.past_due:
        log.warning("Timer is past due; running immediately.")

    rules = load_rules()
    store = SeenStore()
    notifier = build_notifier()
    seed = seed_mode()

    source = None
    items = []
    if os.environ.get("IMAP_HOST"):
        try:
            source = ImapSource.from_env(store=store)
            items.extend(source.fetch())
        except Exception:
            log.exception("Email source failed this run.")
    else:
        log.info("IMAP_HOST unset; running in ingest-only mode.")

    summary = process(items, rules, store, notifier, seed_only=seed)

    # Only advance the IMAP high-water mark once everything that matched was
    # actually delivered. A failed push leaves the marker where it is so the
    # next run re-reads the same mail and tries again.
    if source is not None and summary.failed == 0:
        source.commit_marker()
    elif source is not None:
        log.warning("Holding IMAP marker: %s deliveries failed this run.", summary.failed)

    log.info("Poll complete: %s", json.dumps(summary.as_dict()))


@app.function_name(name="ingest")
@app.route(route="ingest", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def ingest(req: func.HttpRequest) -> func.HttpResponse:
    """Accept items pushed from the browser extension or an email webhook."""
    if not _authorized(req):
        return func.HttpResponse("forbidden", status_code=403)

    body = req.get_body()
    if len(body) > MAX_INGEST_BYTES:
        return func.HttpResponse("payload too large", status_code=413)

    try:
        items = parse_ingest_payload(body, source=req.params.get("source", "ingest"))
    except Exception:
        log.exception("Could not parse ingest payload.")
        return func.HttpResponse("bad payload", status_code=400)

    summary = process(
        items, load_rules(), SeenStore(), build_notifier(), seed_only=seed_mode()
    )
    return func.HttpResponse(
        json.dumps(summary.as_dict()),
        status_code=200,
        mimetype="application/json",
    )


@app.function_name(name="health")
@app.route(route="health", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def health(req: func.HttpRequest) -> func.HttpResponse:
    """Liveness probe.

    Anonymous, so it deliberately reports only whether each part is configured.
    Rule names and the poll cadence are the user's business, not the internet's.
    """
    notifier = build_notifier()
    return func.HttpResponse(
        json.dumps(
            {
                "status": "ok",
                "rules": len(load_rules()),
                "notifier_configured": type(notifier).__name__ != "NullNotifier",
                "email_source": bool(os.environ.get("IMAP_HOST")),
                "seed_mode": seed_mode(),
            }
        ),
        mimetype="application/json",
    )


def _authorized(req: func.HttpRequest) -> bool:
    """Optional shared secret on top of the Functions key.

    The extension ships its token in a header rather than the query string so it
    does not end up in Application Insights request logs.
    """
    expected = os.environ.get("INGEST_TOKEN", "")
    if not expected:
        return True
    return hmac.compare_digest(req.headers.get("X-Ingest-Token", ""), expected)
