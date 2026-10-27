"""Real SQLite request lifecycle and transactional observation matching."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from discord_bridge.core import JobStore, normalize_item
from discord_bridge.request_store import RequestStore, initialize_requests, match_requests

USER = "111111111111111111"
OTHER = "222222222222222222"
GUILD = "333333333333333333"


def product(title="Ninja air fryer", number="123456789", **fields):
    return normalize_item({"item_id": f"ip-{number}", "title": title, **fields})


def observe(store, items, now=20, seed_only=False):
    # This is the exact transaction hook used by JobStore, independently of
    # other workers' integration edits to core.py.
    with sqlite3.connect(store.path) as db:
        db.execute("BEGIN IMMEDIATE")
        initialize_requests(db)
        return match_requests(db, items, now=now, seed_only=seed_only)


def ready(tmp_path):
    store = RequestStore(tmp_path / "queue.sqlite3", guild_id=GUILD)
    store.set_dm_status(USER, "ready")
    return store


def test_requests_survive_restart_and_enforce_ownership_and_limit(tmp_path):
    store = ready(tmp_path)
    request = store.add(USER, "  air fryer  ", now=10)
    restarted = RequestStore(store.path, guild_id=GUILD)
    assert restarted.list(USER)[0]["query"] == "air fryer"
    assert restarted.list(OTHER) == []
    with pytest.raises(ValueError, match="not found"):
        restarted.edit(OTHER, request["id"], "coffee", now=11)
    assert restarted.delete(OTHER, request["id"]) is False
    for index in range(9):
        restarted.add(USER, f"keyword {index}", now=12)
    with pytest.raises(ValueError, match="10"):
        restarted.add(USER, "one too many", now=13)


@pytest.mark.parametrize(
    "query",
    [
        "",
        "*",
        "a" * 151,
        "hello\nworld",
        "https://example.com/ip/123456789",
        "https://www.walmart.com.evil/ip/123456789",
        "https://www.walmart.com:443/ip/123456789",
        "https://www.walmart.com/search?q=fryer",
        "ip-123",
        "123",
    ],
)
def test_invalid_queries_are_rejected(tmp_path, query):
    store = ready(tmp_path)
    with pytest.raises(ValueError):
        store.add(USER, query)


@pytest.mark.parametrize(
    "query",
    [
        "123456789",
        "ip-123456789",
        "https://www.walmart.com/ip/Ninja-Fryer/123456789?selected=true",
        "https://walmart.com/ip/123456789",
    ],
)
def test_exact_product_queries_match_only_the_id(tmp_path, query):
    store = ready(tmp_path)
    store.add(USER, query, now=10)
    observe(store, [product("unrelated", "987654321"), product("unrelated")])
    job = store.claim(now=21)
    assert job["item"]["item_id"] == "ip-123456789"
    assert store.claim(now=21) is None


def test_keywords_use_unicode_and_word_boundaries_and_all_words(tmp_path):
    store = ready(tmp_path)
    store.add(USER, "CAFÉ air-fryer", now=10)
    observe(store, [product("Café airplane fryer"), product("air fryer café", "987654321")])
    job = store.claim(now=21)
    assert job["item"]["item_id"] == "ip-987654321"


def test_no_historical_seeded_or_unavailable_alerts(tmp_path):
    store = ready(tmp_path)
    store.add(USER, "air fryer", now=10)
    observe(store, [product()], now=9)
    observe(store, [product()], now=11, seed_only=True)
    observe(store, [product(available=False)], now=12)
    assert store.claim(now=13) is None
    observe(store, [product()], now=14)
    assert store.claim(now=15)["item"]["available"] is True


def test_multiple_matches_one_dm_deduplicates_across_restarts(tmp_path):
    store = ready(tmp_path)
    store.add(USER, "air fryer", now=10)
    store.add(USER, "Ninja", now=10)
    observe(store, [product()], now=11)
    job = store.claim(now=12)
    assert set(job["queries"]) == {"air fryer", "Ninja"}
    assert store.begin_send(job, now=12)
    assert store.settle(job, "sent", now=12, message_id="444444444444444444")
    restarted = RequestStore(store.path, guild_id=GUILD)
    observe(restarted, [product()], now=13)
    assert restarted.claim(now=14) is None


def test_edit_and_delete_cancel_queued_associations(tmp_path):
    store = ready(tmp_path)
    first = store.add(USER, "air fryer", now=10)
    second = store.add(USER, "Ninja", now=10)
    observe(store, [product()], now=11)
    leased = store.claim(now=12)
    store.edit(USER, first["id"], "coffee", now=13)
    assert store.validate(leased, now=14)["queries"] == ["Ninja"]
    assert store.delete(USER, second["id"])
    assert store.validate(leased, now=14) is None
    assert not store.begin_send(leased, now=14)


def test_unavailable_reobservation_cancels_even_a_leased_alert(tmp_path):
    store = ready(tmp_path)
    store.add(USER, "air fryer", now=10)
    observe(store, [product()], now=11)
    leased = store.claim(now=12)
    observe(store, [product(available=False)], now=13)
    assert store.validate(leased, now=14) is None


def test_lease_recovery_expiry_and_ambiguous_sending_restart(tmp_path):
    store = ready(tmp_path)
    store.add(USER, "air fryer", now=10)
    observe(store, [product()], now=11)
    old = store.claim(now=12)
    assert RequestStore(store.path).claim(now=13) is None
    recovered = RequestStore(store.path).claim(now=80)
    assert recovered and recovered["lease_token"] != old["lease_token"]
    assert store.begin_send(recovered, now=80)
    assert RequestStore(store.path).claim(now=150) is None
    assert store.stats()["review"] == 1
    observe(store, [product("air fryer", "987654321")], now=20)
    assert store.claim(now=1221) is None
    assert store.stats()["expired"] == 1


def test_concurrent_claims_deliver_only_one_lease(tmp_path):
    store = ready(tmp_path)
    store.add(USER, "air fryer", now=10)
    observe(store, [product()], now=11)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: RequestStore(store.path).claim(now=12), range(2)))
    assert sum(result is not None for result in results) == 1


def test_shared_database_hook_rolls_back_with_intake(tmp_path):
    store = ready(tmp_path)
    store.add(USER, "air fryer", now=10)
    with pytest.raises(RuntimeError), sqlite3.connect(store.path) as db:
        db.execute("BEGIN IMMEDIATE")
        match_requests(db, [product()], now=11)
        raise RuntimeError("transaction rejected")
    assert store.claim(now=12) is None


def test_store_binding_rejects_other_server(tmp_path):
    store = ready(tmp_path)
    with pytest.raises(ValueError, match="another"):
        RequestStore(store.path, guild_id=OTHER)


def test_dm_status_defaults_private_and_blocks_queued_delivery(tmp_path):
    store = RequestStore(tmp_path / "queue.sqlite3")
    assert store.dm_status(USER) == "needs_test"
    store.add(USER, "air fryer", now=10)
    observe(store, [product()], now=11)
    assert store.claim(now=12) is None
    store.set_dm_status(USER, "ready", channel_id="444444444444444444")
    assert store.claim(now=12) is not None


def test_real_jobstore_intake_atomically_matches_fresh_reobservations(tmp_path):
    path = tmp_path / "shared.sqlite3"
    jobs = JobStore(path)
    jobs.record([product()], now=5)
    store = RequestStore(path, guild_id=GUILD)
    store.set_dm_status(USER, "ready")
    store.add(USER, "air fryer", now=10)
    assert store.claim(now=11) is None
    accepted = jobs.record([product()], now=12)
    assert accepted["duplicates"] == 1
    assert store.claim(now=13)["queries"] == ["air fryer"]


def test_jobstore_seed_is_quiet_but_later_fresh_observation_can_match(tmp_path):
    path = tmp_path / "shared.sqlite3"
    jobs = JobStore(path)
    store = RequestStore(path, guild_id=GUILD)
    store.set_dm_status(USER, "ready")
    store.add(USER, "air fryer", now=10)
    jobs.record([product()], now=11, seed_only=True)
    assert store.claim(now=12) is None
    jobs.record([product()], now=12)
    assert store.claim(now=13)["observed"] == 12


def test_fresh_available_reobservation_rearms_unsent_cancelled_match(tmp_path):
    store = ready(tmp_path)
    store.add(USER, "air fryer", now=10)
    observe(store, [product()], now=11)
    observe(store, [product(available=False)], now=12)
    assert store.claim(now=13) is None
    observe(store, [product()], now=14)
    assert store.claim(now=15)["observed"] == 14


def test_new_request_after_edit_and_readd_can_match_same_unsent_product(tmp_path):
    store = ready(tmp_path)
    request = store.add(USER, "air fryer", now=10)
    observe(store, [product()], now=11)
    assert store.delete(USER, request["id"])
    store.add(USER, "Ninja", now=13)
    observe(store, [product()], now=14)
    assert store.claim(now=15)["queries"] == ["Ninja"]


def test_fresh_observation_rearms_expired_unsent_job_with_new_lifetime(tmp_path):
    store = ready(tmp_path)
    store.add(USER, "air fryer", now=10)
    observe(store, [product()], now=11)
    # Matching must retire stale pending rows even if delivery was stopped.
    observe(store, [product()], now=1300)
    job = store.claim(now=1301)
    assert job and job["expires"] == 2500


def test_definite_rejection_can_rearm_but_unknown_send_stays_protected(tmp_path):
    store = ready(tmp_path)
    store.add(USER, "air fryer", now=10)
    observe(store, [product()], now=11)
    first = store.claim(now=12)
    assert store.begin_send(first, now=12)
    assert store.settle(first, "failed", now=12, reason="dm_blocked")
    observe(store, [product()], now=20)
    second = store.claim(now=21)
    assert second and second["nonce"] != first["nonce"]
    assert store.begin_send(second, now=21)
    assert store.settle(second, "review", now=21, reason="ambiguous_send")
    observe(store, [product()], now=22)
    assert store.claim(now=23) is None
