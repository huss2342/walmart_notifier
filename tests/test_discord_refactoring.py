"""Compatibility, policy and scale checks for the canonical bridge paths."""

import json
import sqlite3
import time

import pytest
from discord_bridge.core import JobStore, normalize_item
from discord_bridge.request_store import RequestStore, initialize_requests, match_requests


def product(number=123456789, title="Wanted fryer"):
    return normalize_item({"item_id": f"ip-{number}", "title": title, "value_usd": 19.99})


def only_pro(store):
    with store._connect() as db:
        db.execute("UPDATE jobs SET state='skipped' WHERE route!='pro-all'")


def test_legacy_single_claim_obeys_canonical_route_and_global_cooldowns(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product()], now=1000)
    only_pro(store)
    store.defer_route("pro-all", until=1030)
    assert store.claim(now=1000) is None
    store.defer_global(until=1060)
    assert store.claim(now=1030) is None
    assert store.claim(now=1060)["route"] == "pro-all"


def test_legacy_single_api_cannot_split_or_settle_a_frozen_multi_item_dispatch(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(123456780 + index) for index in range(3)], now=1000)
    only_pro(store)
    batch = store.claim_batch(now=1000, size=3)
    store.retry_batch(batch, now=1000, delay=1, reason="remote_retry")
    assert store.claim(now=1001) is None
    recovered = store.claim_batch(now=1001)
    assert recovered["payload"] == batch["payload"]
    assert len(recovered["jobs"]) == 3


def test_legacy_settlement_records_canonical_delivery_metrics(tmp_path, monkeypatch):
    store = JobStore(tmp_path / "q")
    store.record([product()], now=1000)
    only_pro(store)
    job = store.claim(now=1000)
    monkeypatch.setattr("discord_bridge.core.time.time", lambda: 1001)
    assert store.finish(job, "sent", message_id="1800000000000000001")
    metrics = store.delivery_metrics(now=1002)
    assert metrics["sent_items_last_5m"] == 1
    assert metrics["sent_messages_last_5m"] == 1
    with store._connect() as db:
        assert db.execute("SELECT state FROM dispatches").fetchone()[0] == "sent"


def test_product_policy_and_managed_feeds_share_exact_categories_and_price_bands():
    from discord_bridge import core, layout, policy

    assert core.CATEGORIES is policy.PRODUCT_CATEGORIES
    assert core.PRICE_BANDS is policy.PRICE_BANDS
    assert layout.STARTER_MAX_PRICE_CENTS == policy.STARTER_MAX_PRICE_CENTS
    assert {c.key for c in layout.CHANNELS if c.category == "pro-type"} == {
        f"type-{key}" for key in policy.PRODUCT_CATEGORIES
    }
    assert {c.key for c in layout.CHANNELS if c.category == "pro-price"} == {
        f"price-{band}" for _, band in policy.PRICE_BANDS
    } | {f"price-{policy.TOP_PRICE_BAND}"}


def test_core_observation_and_message_exports_keep_the_existing_public_contract():
    from discord_bridge import core, messages, observations

    assert core.normalize_item is observations.normalize_item
    assert core.validate_batch is observations.validate_batch
    assert core.route_keys is observations.route_keys
    assert core.render_message is messages.render_message
    assert core.render_batch is messages.render_batch
    assert observations.route_keys(product()) == core.route_keys(product())


def test_request_candidates_do_not_compare_every_watch_to_every_observation(tmp_path, monkeypatch):
    import discord_bridge.request_store as requests

    store = RequestStore(tmp_path / "requests")
    user = "111111111111111111"
    # Model an existing installation before the candidate-index migration. This
    # avoids thousands of per-command transactions in the benchmark setup.
    with store._db() as db:
        db.executemany(
            "INSERT INTO personal_requests(user_id,query,kind,matcher,created,updated) "
            "VALUES (?,?,?,?,?,?)",
            [
                (
                    str(200000000000000000 + i),
                    f"other {i}",
                    "keywords",
                    json.dumps(["other", str(i)]),
                    1,
                    1,
                )
                for i in range(3000)
            ]
            + [
                (
                    str(300000000000000000 + i),
                    str(900000000 + i),
                    "exact",
                    f"ip-{900000000 + i}",
                    1,
                    1,
                )
                for i in range(3000)
            ],
        )
        db.execute("DELETE FROM personal_request_settings WHERE key='matching_index_version'")
        initialize_requests(db)
    store.add(user, "wanted fryer", now=2)
    store.set_dm_status(user, "ready")
    comparisons = []
    original = requests._matches

    def counted(*args, **kwargs):
        comparisons.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(requests, "_matches", counted)
    items = [product(123456780 + index) for index in range(20)]
    started = time.perf_counter()
    sqlite_steps = []
    with store._db() as db:
        db.execute("BEGIN IMMEDIATE")
        db.set_progress_handler(lambda: sqlite_steps.append(100) or 0, 100)
        assert match_requests(db, items, now=3) == len(items)
        db.set_progress_handler(None, 0)
    elapsed = time.perf_counter() - started
    assert len(comparisons) <= 2 * len(items)
    assert sum(sqlite_steps) < 100_000, "candidate selection must not scan every keyword watch"
    assert store.stats()["pending"] == len(items)
    # Time is recorded for diagnostics; the stable regression gate is candidate
    # work, so slower Windows/CI machines do not fail a wall-clock threshold.
    print(
        f"6001 watches / 20 observations: {len(comparisons)} comparisons, "
        f"about {sum(sqlite_steps)} SQLite VM steps, {elapsed:.4f}s"
    )


def test_candidate_migration_is_atomic_and_survives_reopening_old_database(tmp_path):
    path = tmp_path / "requests"
    store = RequestStore(path)
    user = "111111111111111111"
    request = store.add(user, "CAFÉ air-fryer", now=1)
    store.set_dm_status(user, "ready")
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE IF EXISTS personal_request_keywords")
        db.execute("DELETE FROM personal_request_settings WHERE key='matching_index_version'")
    restarted = RequestStore(path)
    with restarted._db() as db:
        db.execute("BEGIN IMMEDIATE")
        assert match_requests(db, [product(title="Café air fryer")], now=2) == 1
    job = restarted.claim(now=3)
    assert job["queries"] == ["CAFÉ air-fryer"]
    restarted.edit(user, request["id"], "coffee maker", now=4)
    assert restarted.validate(job, now=5) is None
    with restarted._db() as db:
        assert match_requests(db, [product(title="Café air fryer")], now=6) == 0
        assert match_requests(db, [product(title="Coffee maker")], now=7) == 1
    assert restarted.claim(now=8)["queries"] == ["coffee maker"]


def test_candidate_index_rolls_back_with_a_failed_edit(tmp_path):
    store = RequestStore(tmp_path / "requests")
    user = "111111111111111111"
    request = store.add(user, "wanted fryer", now=1)
    store.set_dm_status(user, "ready")
    with store._db() as db:
        db.execute(
            "CREATE TRIGGER reject_keyword_change BEFORE INSERT ON personal_request_keywords "
            "WHEN NEW.token='coffee' BEGIN SELECT RAISE(ABORT,'test edit failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        store.edit(user, request["id"], "coffee maker", now=2)
    assert store.list(user)[0]["query"] == "wanted fryer"
    with store._db() as db:
        assert match_requests(db, [product()], now=3) == 1


def test_keyword_candidates_stay_current_with_legacy_database_writers(tmp_path):
    store = RequestStore(tmp_path / "requests")
    user = "111111111111111111"
    request = store.add(user, "wanted fryer", now=1)
    # A rollback image knows only the original watch table and cannot call new
    # Python index helpers. The database must preserve its write contract too.
    with store._db() as db:
        db.execute(
            "UPDATE personal_requests SET query=?,matcher=?,revision=revision+1,updated=2 "
            "WHERE id=?",
            ("coffee maker", json.dumps(["coffee", "maker"]), request["id"]),
        )
    restarted = RequestStore(store.path)
    with restarted._db() as db:
        assert match_requests(db, [product(title="Coffee maker")], now=3) == 1
        assert match_requests(db, [product(123456790)], now=3) == 0
        db.execute("DELETE FROM personal_requests WHERE id=?", (request["id"],))
        assert db.execute("SELECT COUNT(*) FROM personal_request_keywords").fetchone()[0] == 0
