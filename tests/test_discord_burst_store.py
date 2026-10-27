"""Durable burst envelopes, freshness and tier boundaries."""

import json
import sqlite3

import discord_bridge.core as core
import pytest


def product(number, **extra):
    return core.normalize_item(
        {
            "item_id": f"ip-{10000000 + number}",
            "title": f"Desk lamp {number}",
            "value_usd": 19.99,
            "category": "electronics",
            **extra,
        }
    )


def only_route(store, route):
    with store._connect() as db:
        db.execute("UPDATE jobs SET state='skipped' WHERE route!=?", (route,))


def test_new_envelopes_respect_conservative_total_wire_byte_ceiling(tmp_path):
    store = core.JobStore(tmp_path / "q")
    store.record(
        [
            product(
                i,
                title="🛒" * 1000,
                image_url="https://i5.walmartimages.com/asr/" + "x" * 1800 + ".jpg",
            )
            for i in range(10)
        ],
        now=1000,
    )
    only_route(store, "pro-all")
    batch = store.claim_batch(now=1000, size=10)
    assert len(json.dumps(batch["payload"], separators=(",", ":")).encode()) <= 10 * 1024
    assert 1 <= len(batch["jobs"]) < 10


def test_fresh_definitive_413_repackages_only_safe_owned_jobs_and_preserves_parent(tmp_path):
    store = core.JobStore(tmp_path / "q")
    store.record([product(i) for i in range(10)], now=1000)
    only_route(store, "pro-all")
    batch = store.claim_batch(now=1000, size=10)
    assert store.split_rejected_batch(batch, now=1001) == "split"
    with store._connect() as db:
        archived = db.execute("SELECT * FROM dispatches WHERE id=?", (batch["id"],)).fetchone()
        assert archived["state"] == "rejected_413"
        assert json.loads(archived["payload"]) == batch["payload"]
    recovered = core.JobStore(store.path).claim_batch(now=1001, size=10)
    assert len(recovered["jobs"]) <= 5
    assert recovered["id"] != batch["id"]


@pytest.mark.parametrize("uncertain_path", ["ambiguous_retry", "expired_lease", "legacy_upgrade"])
def test_413_never_repackages_prior_ambiguous_or_unknown_attempts(tmp_path, uncertain_path):
    store = core.JobStore(tmp_path / "q")
    store.record([product(i) for i in range(10)], now=1000)
    only_route(store, "pro-all")
    batch = store.claim_batch(now=1000, size=10)
    if uncertain_path == "ambiguous_retry":
        store.retry_batch(batch, now=1000, delay=1, reason="remote_retry", ambiguous=True)
        batch = store.claim_batch(now=1001)
    elif uncertain_path == "expired_lease":
        batch = store.claim_batch(now=1060)
    else:
        with store._connect() as db:
            db.execute("ALTER TABLE dispatches DROP COLUMN uncertain")
        store = core.JobStore(store.path)
    assert store.split_rejected_batch(batch, now=1061) == "held"
    with store._connect() as db:
        assert (
            json.loads(
                db.execute("SELECT payload FROM dispatches WHERE id=?", (batch["id"],)).fetchone()[
                    0
                ]
            )
            == batch["payload"]
        )
        assert (
            db.execute("SELECT COUNT(*) FROM jobs WHERE batch_id=?", (batch["id"],)).fetchone()[0]
            == 10
        )
    assert store.claim_batch(now=1062) is None


def test_rejected_413_does_not_revive_expired_or_seeded_jobs(tmp_path):
    store = core.JobStore(tmp_path / "q")
    store.record([product(i) for i in range(3)], now=1000)
    only_route(store, "pro-all")
    batch = store.claim_batch(now=1000)
    store.record([product(1)], now=1001, seed_only=True)
    assert store.split_rejected_batch(batch, now=2800) == "stale"
    assert store.claim_batch(now=2801) is None


def test_rejected_split_is_atomic_if_sqlite_fails_mid_detachment(tmp_path):
    store = core.JobStore(tmp_path / "q")
    store.record([product(i) for i in range(3)], now=1000)
    only_route(store, "pro-all")
    batch = store.claim_batch(now=1000)
    with store._connect() as db:
        target = batch["jobs"][1]["id"]
        db.execute(
            "CREATE TRIGGER reject_split BEFORE UPDATE OF batch_id ON jobs "
            "WHEN NEW.batch_id IS NULL AND OLD.id='" + target + "' "
            "BEGIN SELECT RAISE(ABORT,'offline split failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        store.split_rejected_batch(batch, now=1001)
    with store._connect() as db:
        assert {
            row[0] for row in db.execute("SELECT state FROM jobs WHERE batch_id=?", (batch["id"],))
        } == {"leased"}
        assert (
            db.execute("SELECT state FROM dispatches WHERE id=?", (batch["id"],)).fetchone()[0]
            == "leased"
        )
        assert db.execute("SELECT COUNT(*) FROM route_payload_limits").fetchone()[0] == 0


def test_new_byte_cap_never_rewrites_a_previously_attempted_oversized_body(tmp_path, monkeypatch):
    store = core.JobStore(tmp_path / "q")
    store.record(
        [
            product(i, image_url="https://i5.walmartimages.com/asr/" + "x" * 1800 + ".jpg")
            for i in range(10)
        ],
        now=1000,
    )
    only_route(store, "pro-all")
    monkeypatch.setattr(core, "MAX_MESSAGE_BYTES", 100_000)
    batch = store.claim_batch(now=1000, size=10)
    store.retry_batch(batch, now=1000, delay=1, reason="remote_retry", ambiguous=True)
    monkeypatch.setattr(core, "MAX_MESSAGE_BYTES", 10 * 1024)
    recovered = core.JobStore(store.path).claim_batch(now=1001, size=10)
    assert recovered["payload"] == batch["payload"]
    assert len(core.encode_payload(recovered["payload"])) > core.MAX_MESSAGE_BYTES
    assert store.split_rejected_batch(recovered, now=1001) == "held"


def test_definitively_rejected_single_is_terminal_without_nonce_repack(tmp_path):
    store = core.JobStore(tmp_path / "q")
    store.record([product(1)], now=1000)
    only_route(store, "pro-all")
    batch = store.claim_batch(now=1000)
    assert store.split_rejected_batch(batch, now=1001) == "failed"
    assert store.claim_batch(now=1002) is None
    with store._connect() as db:
        assert db.execute(
            "SELECT batch_id,reason FROM jobs WHERE id=?", (batch["jobs"][0]["id"],)
        ).fetchone()[:] == (batch["id"], "http_413")


def test_ready_burst_has_five_items_one_frozen_message_and_two_buttons_each(tmp_path):
    store = core.JobStore(tmp_path / "q")
    store.record([product(i) for i in range(8)], now=1000)
    only_route(store, "pro-all")
    batch = store.claim_batch(now=1000)
    assert len(batch["jobs"]) == 5
    payload = batch["payload"]
    assert len(payload["embeds"]) == len(payload["components"]) == 5
    assert all(len(row["components"]) == 2 for row in payload["components"])
    assert payload["nonce"] == batch["id"] and payload["enforce_nonce"] is True
    assert payload["allowed_mentions"] == {"parse": []}
    assert all("sampling" in embed["url"].lower() for embed in payload["embeds"])
    assert store.claim_batch(now=1001) is None  # Same channel stays serialized.
    assert store.finish_batch(batch, "sent", now=1001, message_id="1800000000000000001")
    assert len(store.claim_batch(now=1001)["jobs"]) == 3


def test_paid_ten_item_dispatch_keeps_all_cards_and_buttons_within_provider_limits(tmp_path):
    # The renderer's official embed/component bounds are independent of the
    # stricter application wire-byte guard tested above at reservation time.
    jobs = [
        {
            "item": product(i, title="@everyone " + "x" * 1990),
            "route": "pro-all",
            "first_seen": 1000,
            "last_seen": 1000,
        }
        for i in range(10)
    ]
    payload = core.render_batch(jobs)
    assert len(payload["embeds"]) == 10
    assert len(payload["components"]) == 5
    buttons = [button for row in payload["components"] for button in row["components"]]
    assert len(buttons) == 20
    assert all(len(row["components"]) == 4 for row in payload["components"])
    assert all(len(button["label"]) <= 80 and len(button["url"]) <= 512 for button in buttons)
    assert payload["allowed_mentions"] == {"parse": []}
    assert (
        sum(
            len(embed["title"]) + len(embed["description"]) + len(embed["footer"]["text"])
            for embed in payload["embeds"]
        )
        <= 6000
    )
    for index, job in enumerate(jobs, 1):
        assert buttons[(index - 1) * 2]["url"] == job["item"]["review_url"]
        assert buttons[(index - 1) * 2 + 1]["url"] == job["item"]["product_url"]
        assert payload["embeds"][index - 1]["title"].startswith(f"[{index}]")


def test_ten_item_retry_remains_frozen_when_reopened_with_legacy_batch_size(tmp_path):
    store = core.JobStore(tmp_path / "q")
    store.record([product(i) for i in range(10)], now=1000)
    only_route(store, "pro-all")
    batch = store.claim_batch(now=1000, size=10)
    store.retry_batch(batch, now=1000, delay=1, reason="remote_retry")
    store.record([product(99)], now=1001)
    only_route(store, "pro-all")
    recovered = core.JobStore(store.path).claim_batch(now=1001)
    assert recovered["payload"] == batch["payload"]
    assert recovered["id"] == batch["id"]
    assert len(recovered["jobs"]) == 10


def test_free_history_keeps_five_item_batches_even_when_ten_requested(tmp_path):
    store = core.JobStore(tmp_path / "q")
    store.record([product(i, available=False) for i in range(10)], now=1000)
    assert store.claim_batch(now=87399, size=10) is None
    batch = store.claim_batch(now=87400, size=10)
    assert len(batch["jobs"]) == 5
    assert all("Historical" in embed["description"] for embed in batch["payload"]["embeds"])


@pytest.mark.parametrize(
    "title", ["🛒" * 1000, "Broken \ud800 Unicode title"], ids=["emoji", "unpaired-surrogate"]
)
def test_ten_unicode_titles_remain_within_conservative_embed_text_limits(tmp_path, title):
    store = core.JobStore(tmp_path / "q")
    store.record([product(i, title=title) for i in range(10)], now=1000)
    only_route(store, "pro-all")
    payload = store.claim_batch(now=1000, size=10)["payload"]
    assert 1 <= len(payload["embeds"]) <= 10
    assert len(core.encode_payload(payload)) <= core.MAX_MESSAGE_BYTES
    assert all(len(embed["title"].encode("utf-16-le")) // 2 <= 256 for embed in payload["embeds"])
    assert (
        sum(
            len(
                (embed["title"] + embed["description"] + embed["footer"]["text"]).encode(
                    "utf-16-le"
                )
            )
            // 2
            for embed in payload["embeds"]
        )
        <= 6000
    )


def test_retry_keeps_membership_body_and_nonce_across_restart_and_new_intake(tmp_path):
    path = tmp_path / "q"
    store = core.JobStore(path)
    store.record([product(i) for i in range(3)], now=1000)
    only_route(store, "pro-all")
    batch = store.claim_batch(now=1000)
    store.retry_batch(batch, now=1000, delay=20, reason="rate_limited", count_attempt=False)
    store.record([product(99), product(1, title="Updated title")], now=1001)
    only_route(store, "pro-all")
    store = core.JobStore(path)
    recovered = store.claim_batch(now=1020, exclude_routes=())
    assert recovered["id"] == batch["id"]
    assert recovered["payload"] == batch["payload"]
    assert [job["id"] for job in recovered["jobs"]] == [job["id"] for job in batch["jobs"]]
    assert all(job["attempts"] == 1 for job in recovered["jobs"])


def test_fresh_first_batch_fills_old_backlog_without_crossing_channels(tmp_path):
    store = core.JobStore(tmp_path / "q")
    store.record([product(i) for i in range(12)], now=1000)
    store.record([product(99)], now=1200)
    only_route(store, "pro-all")
    batch = store.claim_batch(now=1200)
    assert batch["jobs"][0]["item"]["item_id"] == product(99)["item_id"]
    assert len(batch["jobs"]) == 5
    assert all(job["first_seen"] == 1000 for job in batch["jobs"][1:])


def test_seed_or_ineligible_member_cancels_frozen_batch_without_repacking(tmp_path):
    store = core.JobStore(tmp_path / "q")
    store.record([product(i) for i in range(3)], now=1000)
    only_route(store, "starter-items")
    batch = store.claim_batch(now=1300)
    store.retry_batch(batch, now=1300, delay=20, reason="remote_retry")
    store.record([product(1, value_usd=99.99)], now=1301)
    only_route(store, "starter-items")
    assert store.claim_batch(now=1320) is None
    with store._connect() as db:
        assert db.execute("SELECT count(*) FROM jobs WHERE state='leased'").fetchone()[0] == 0
        assert (
            db.execute("SELECT state FROM dispatches WHERE id=?", (batch["id"],)).fetchone()[0]
            == "skipped"
        )
    store.record([product(i) for i in range(3)], now=1321)
    only_route(store, "starter-items")
    assert store.claim_batch(now=1321) is None


def test_restarted_active_channel_lease_suppresses_idle_audit_spins(tmp_path):
    path = tmp_path / "q"
    store = core.JobStore(path)
    store.record([product(i) for i in range(8)], now=1000)
    only_route(store, "pro-all")
    batch = store.claim_batch(now=1000)
    restarted = core.JobStore(path)
    assert restarted.next_due_in(now=1001) == 59
    assert restarted.claim_batch(now=1001) is None
    assert restarted.claim_batch(now=1060)["payload"] == batch["payload"]


def test_fresh_item_does_not_share_envelope_with_almost_expired_backlog(tmp_path):
    store = core.JobStore(tmp_path / "q")
    store.record([product(i) for i in range(4)], now=1000)
    store.record([product(99)], now=2799)
    only_route(store, "pro-all")
    batch = store.claim_batch(now=2799)
    assert [job["item_id"] for job in batch["jobs"]] == [product(99)["item_id"]]
    assert store.validate_dispatch(batch, now=2801)


def test_cooldowns_survive_restart_and_do_not_spend_attempts(tmp_path):
    path = tmp_path / "q"
    store = core.JobStore(path)
    store.record([product(1)], now=1000)
    only_route(store, "pro-all")
    store.defer_route("pro-all", until=1030)
    store = core.JobStore(path)
    assert store.claim_batch(now=1029) is None
    assert store.next_due_in(now=1029) == 1
    store.defer_global(until=1060)
    assert store.claim_batch(now=1030) is None
    assert store.next_due_in(now=1030) == 30
    assert store.claim_batch(now=1060)["jobs"][0]["attempts"] == 1


def test_paid_due_wait_ignores_due_free_and_preserves_learned_route_cooldowns(tmp_path):
    store = core.JobStore(tmp_path / "q")
    store.record([product(999, available=False)], now=1000)
    store.record([product(1)], now=87400)
    for route in ("pro-all", "type-electronics", "price-10-20"):
        store.defer_route(route, until=87417)
    assert store.next_due_in(now=87400) == 0
    assert store.next_due_in(now=87400, audience="free") == 0
    assert store.next_due_in(now=87400, audience="paid") == 17
    assert core.JobStore(store.path).next_due_in(now=87400, audience="paid") == 17
    with pytest.raises(ValueError):
        store.next_due_in(now=87400, audience="private-url")


def test_partial_seed_during_send_releases_siblings_without_duplicate_retry(tmp_path):
    store = core.JobStore(tmp_path / "q")
    store.record([product(i) for i in range(3)], now=1000)
    only_route(store, "pro-all")
    batch = store.claim_batch(now=1000)
    store.record([product(1)], now=1001, seed_only=True)
    assert not store.finish_batch(batch, "sent", now=1001, message_id="1800000000000000001")
    assert store.claim_batch(now=1100) is None
    assert store.counts().get("leased", 0) == 0


def test_schema_upgrade_pins_attempted_jobs_to_legacy_single_nonce(tmp_path):
    path = tmp_path / "q"
    store = core.JobStore(path)
    store.record([product(i) for i in range(3)], now=1000)
    only_route(store, "pro-all")
    legacy = store.claim(now=1000)
    store.retry(legacy, now=1000, delay=5, reason="remote_retry")
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=3")
    store = core.JobStore(path)
    batch = store.claim_batch(now=1005)
    assert batch["id"] == legacy["id"] and len(batch["jobs"]) == 1
    assert json.loads(json.dumps(batch["payload"]))["nonce"] == legacy["id"]


def test_dispatch_settlement_is_atomic_even_if_sqlite_fails_mid_update(tmp_path):
    store = core.JobStore(tmp_path / "q")
    store.record([product(i) for i in range(3)], now=1000)
    only_route(store, "pro-all")
    batch = store.claim_batch(now=1000)
    with store._connect() as db:
        target = batch["jobs"][1]["id"]
        db.execute(
            "CREATE TRIGGER reject_partial BEFORE UPDATE OF state ON jobs "
            "WHEN NEW.state='sent' AND NEW.id='" + target + "' "
            "BEGIN SELECT RAISE(ABORT,'test disk error'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        store.finish_batch(batch, "sent", now=1001, message_id="1800000000000000001")
    with store._connect() as db:
        assert {
            row[0] for row in db.execute("SELECT state FROM jobs WHERE batch_id=?", (batch["id"],))
        } == {"leased"}
        db.execute("DROP TRIGGER reject_partial")
    restarted = core.JobStore(store.path)
    recovered = restarted.claim_batch(now=1060)
    assert recovered["payload"] == batch["payload"]
    assert restarted.finish_batch(recovered, "sent", now=1061, message_id="1800000000000000001")


def test_maximum_title_batch_stays_inside_discord_embed_and_button_limits(tmp_path):
    store = core.JobStore(tmp_path / "q")
    store.record([product(i, title="@everyone " + "x" * 1990) for i in range(5)], now=1000)
    only_route(store, "pro-all")
    payload = store.claim_batch(now=1000)["payload"]
    characters = sum(
        len(embed["title"]) + len(embed["description"]) + len(embed["footer"]["text"])
        for embed in payload["embeds"]
    )
    assert characters <= 6000
    assert len(payload["embeds"]) <= 10 and len(payload["components"]) <= 5
    assert all(
        len(button["label"]) <= 80 for row in payload["components"] for button in row["components"]
    )
