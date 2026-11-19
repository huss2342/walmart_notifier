"""Minimum-value watches and atomic comma-separated request creation."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from discord_bridge.core import JobStore, normalize_item
from discord_bridge.request_store import RequestStore

USER = "111111111111111111"


def setup_store(tmp_path):
    store = RequestStore(tmp_path / "requests.sqlite3")
    store.set_dm_status(USER, "ready")
    return store, JobStore(store.path)


def product(value, number="123456789", title="Recliner chair"):
    return normalize_item({"item_id": f"ip-{number}", "title": title, "value_cents": value})


@pytest.mark.parametrize("query", ["recliner", "123456789"])
@pytest.mark.parametrize(
    # A listing that shows no value still alerts (the DM says so): about a
    # third of reviewer cards omit it, and skipping them silently hid matches.
    "value,eligible", [(None, True), (9999, False), (10000, True), (10001, True)]
)
def test_minimum_is_inclusive_for_keywords_and_exact_ids(tmp_path, query, value, eligible):
    store, jobs = setup_store(tmp_path)
    row = store.add(USER, query, min_value=100, now=10)
    assert row["min_value_cents"] == 10000
    jobs.record([product(value)], now=11)
    assert (store.claim(now=12) is not None) is eligible


def test_inline_common_minimum_and_bulk_normalization(tmp_path):
    store, _ = setup_store(tmp_path)
    rows = store.add_many(USER, " recliner, chair, RECLINER, reclining chairs $100.25+ ", now=10)
    assert [row["query"] for row in rows] == ["recliner", "chair", "reclining chairs"]
    assert [row["min_value_cents"] for row in rows] == [10025] * 3
    restarted = RequestStore(store.path)
    assert restarted.list(USER) == rows


def test_bulk_overlap_sends_once_and_requests_stay_active(tmp_path):
    store, jobs = setup_store(tmp_path)
    store.add_many(USER, "recliner, chair", min_value=100, now=10)
    jobs.record([product(10000)], now=11)
    job = store.claim(now=12)
    assert set(job["queries"]) == {"recliner", "chair"}
    assert store.begin_send(job, now=12)
    assert store.settle(job, "sent", now=12)
    jobs.record([product(10000)], now=13)
    assert store.claim(now=14) is None
    jobs.record([product(11000, "987654321")], now=15)
    assert store.claim(now=16)["item"]["item_id"] == "ip-987654321"
    assert len(store.list(USER)) == 2


@pytest.mark.parametrize(
    "query,min_value",
    [
        ("chair,", None),
        ("chair,,recliner", None),
        ("chair,https://example.com/ip/123456789", None),
        ("chair $-100+", None),
        ("chair $100.001+", None),
        ("chair $100", None),
        ("chair $100+, recliner", None),
        ("chair $100+", 200),
        ("chair", -1),
        ("chair", True),
        ("chair", float("nan")),
        ("chair", float("inf")),
        ("chair", 1.001),
        ("chair", "abc"),
        ("chair", 100_000_001),
    ],
)
def test_invalid_batch_or_minimum_writes_nothing(tmp_path, query, min_value):
    store, _ = setup_store(tmp_path)
    with pytest.raises(ValueError):
        store.add_many(USER, query, min_value=min_value, now=10)
    assert store.list(USER) == []


def test_bulk_capacity_duplicate_and_preflight_are_atomic(tmp_path):
    store, _ = setup_store(tmp_path)
    for number in range(8):
        store.add(USER, f"keyword {number}", now=10)
    store.validate_add(USER, "chair, recliner", min_value="100.25")
    assert len(store.list(USER)) == 8
    rows = store.add_many(USER, "chair, keyword 0", now=11)
    assert [row.get("already_saved", False) for row in rows] == [False, True]
    assert len(store.list(USER)) == 9
    rows = store.add_many(USER, "chair, recliner, CHAIR, sofa, desk", now=12)
    assert len(rows) == 4 and rows[0]["already_saved"] is True
    assert len(store.list(USER)) == 12, "there is no per-member request cap"


def test_overlapping_batches_reuse_existing_phrase_without_changing_it(tmp_path):
    store, _ = setup_store(tmp_path)
    original = store.add_many(USER, "ice, ice maker, ice machine", now=10)
    store.validate_add(USER, "ice maker, ice tray, ice cubes")
    assert store.list(USER) == original
    rows = store.add_many(USER, "ICE MAKER, ice tray, ice cubes", now=20)
    assert rows[0] == {**original[1], "already_saved": True}
    assert [row["query"] for row in rows] == ["ice maker", "ice tray", "ice cubes"]
    saved = store.list(USER)
    assert len(saved) == 5 and saved[:3] == original
    assert all("already_saved" not in row for row in saved)


def test_reused_minimum_is_retained_when_batch_minimum_is_unspecified(tmp_path):
    store, _ = setup_store(tmp_path)
    original = store.add(USER, "ice maker", min_value=150, now=10)
    rows = store.add_many(USER, "ice tray, ice maker", now=20)
    assert rows[0]["min_value_cents"] is None
    assert rows[1] == {**original, "already_saved": True}
    matching = store.add_many(USER, "ice maker $150+", now=30)
    assert matching == [{**original, "already_saved": True}]


@pytest.mark.parametrize("old,new", [(100, 200), (100, 0), (None, 100)])
def test_conflicting_reused_minimum_rejects_entire_batch_and_preflight(tmp_path, old, new):
    store, _ = setup_store(tmp_path)
    original = store.add(USER, "ice maker", min_value=old, now=10)
    for operation in (store.validate_add, store.add_many):
        with pytest.raises(ValueError, match="edit"):
            operation(USER, "ice tray, ice maker", min_value=new)
        assert store.list(USER) == [original]


def test_all_reused_batch_succeeds_at_capacity_but_single_add_stays_strict(tmp_path):
    store, _ = setup_store(tmp_path)
    for number in range(10):
        store.add(USER, f"keyword {number}", now=10)
    original = store.list(USER)
    store.validate_add(USER, "keyword 9, KEYWORD 0")
    rows = store.add_many(USER, "keyword 9, KEYWORD 0", now=20)
    assert rows == [{**original[index], "already_saved": True} for index in (9, 0)]
    assert store.list(USER) == original
    with pytest.raises(ValueError, match="already"):
        store.add(USER, "keyword 0", now=30)


def test_reused_product_link_and_reordered_phrase_aliases_keep_original_rows(tmp_path):
    store, _ = setup_store(tmp_path)
    original = store.add_many(USER, "air fryer, 123456789", now=10)
    rows = store.add_many(USER, "FRYER air, https://www.walmart.com/ip/123456789", now=20)
    assert rows == [{**row, "already_saved": True} for row in original]
    assert store.list(USER) == original


def test_reused_request_emits_no_add_log_or_rearmed_sent_alert(tmp_path):
    events = []
    store = RequestStore(
        tmp_path / "q", event_sink=lambda event, **fields: events.append((event, fields))
    )
    store.set_dm_status(USER, "ready")
    original = store.add(USER, "ice maker", min_value=100, now=10)
    jobs = JobStore(store.path)
    jobs.record([product(10000, title="ice maker")], now=11)
    alert = store.claim(now=12)
    assert store.begin_send(alert, now=12)
    assert store.settle(alert, "sent", now=12)
    events.clear()
    rows = store.add_many(USER, "ice maker, ice tray", now=20)
    changed = [fields for event, fields in events if event == "request_changed"]
    assert len(changed) == 1 and changed[0]["request_id"] == rows[1]["id"]
    assert rows[0] == {**original, "already_saved": True}
    jobs.record([product(10000, title="ice maker")], now=21)
    assert store.claim(now=22) is None
    assert store.stats()["sent"] == 1


def test_exact_link_aliases_and_reordered_words_deduplicate_in_batch(tmp_path):
    store, _ = setup_store(tmp_path)
    rows = store.add_many(
        USER,
        "air fryer, FRYER air, 123456789, https://www.walmart.com/ip/123456789 $100+",
        min_value="100.00",
        now=10,
    )
    assert [row["query"] for row in rows] == ["air fryer", "123456789"]
    assert [row["min_value_cents"] for row in rows] == [10000, 10000]


def test_unfiltered_watch_still_matches_when_overlapping_filtered_watch_does_not(tmp_path):
    store, jobs = setup_store(tmp_path)
    store.add(USER, "recliner", min_value=100, now=10)
    store.add(USER, "chair", now=10)
    jobs.record([product(9999)], now=11)
    assert store.claim(now=12)["queries"] == ["chair"]


def test_each_bulk_phrase_still_requires_all_its_words(tmp_path):
    store, jobs = setup_store(tmp_path)
    store.add_many(USER, "reclining chairs, air fryer", now=10)
    jobs.record([product(10000, title="reclining sofa")], now=11)
    assert store.claim(now=12) is None
    jobs.record([product(10000, title="comfortable reclining chairs")], now=13)
    assert store.claim(now=14)["queries"] == ["reclining chairs"]


def test_minimum_edit_preserves_changes_and_clears_and_invalidates_pending(tmp_path):
    store, jobs = setup_store(tmp_path)
    row = store.add(USER, "chair", min_value=100, now=10)
    row = store.edit(USER, row["id"], "recliner", now=11)
    assert row["min_value_cents"] == 10000
    jobs.record([product(11000)], now=12)
    leased = store.claim(now=13)
    row = store.edit(USER, row["id"], "recliner $120+", now=14)
    assert row["min_value_cents"] == 12000
    assert store.validate(leased, now=15) is None
    jobs.record([product(11000)], now=16)
    assert store.claim(now=17) is None
    row = store.edit(USER, row["id"], "recliner", min_value=0, now=18)
    assert row["min_value_cents"] is None
    jobs.record([product(None)], now=19)
    assert store.claim(now=20) is not None


def test_edit_rejects_bulk_without_changing_saved_request(tmp_path):
    store, _ = setup_store(tmp_path)
    row = store.add(USER, "chair", min_value=100, now=10)
    with pytest.raises(ValueError):
        store.edit(USER, row["id"], "recliner, sofa", now=11)
    assert store.list(USER) == [row]


@pytest.mark.parametrize("latest_value", [5000, 9999])
def test_send_revalidates_latest_value_and_fresh_eligible_observation_rearms(
    tmp_path, latest_value
):
    store, jobs = setup_store(tmp_path)
    store.add(USER, "recliner", min_value=100, now=10)
    jobs.record([product(11000)], now=11)
    leased = store.claim(now=12)
    jobs.record([product(latest_value)], now=13)
    assert store.validate(leased, now=14) is None
    assert store.begin_send(leased, now=14) is None
    # Let claim retire the now ineligible observation before observing it again.
    assert store.claim(now=75) is None
    jobs.record([product(12000)], now=76)
    assert store.claim(now=77)["item"]["value_cents"] == 12000


def test_schema_upgrade_keeps_old_watches_unfiltered_and_indexed(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE personal_requests (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "user_id TEXT NOT NULL,query TEXT NOT NULL,kind TEXT NOT NULL,matcher TEXT NOT NULL,"
            "revision INTEGER NOT NULL DEFAULT 1,created REAL NOT NULL,updated REAL NOT NULL)"
        )
        db.execute(
            "INSERT INTO personal_requests(user_id,query,kind,matcher,created,updated) "
            "VALUES (?,?,?, ?,10,10)",
            (USER, "chair", "keywords", '["chair"]'),
        )
    store = RequestStore(path)
    assert store.list(USER)[0]["min_value_cents"] is None
    store.set_dm_status(USER, "ready")
    jobs = JobStore(path)
    jobs.record([product(None)], now=11)
    assert store.claim(now=12)["queries"] == ["chair"]
    assert RequestStore(path).list(USER)[0]["min_value_cents"] is None


def test_concurrent_bulk_adds_cannot_overfill_member_capacity(tmp_path):
    store, _ = setup_store(tmp_path)
    for number in range(8):
        store.add(USER, f"keyword {number}", now=10)

    def add_batch(number):
        try:
            return len(
                RequestStore(store.path).add_many(USER, f"chair {number}, sofa {number}", now=11)
            )
        except ValueError:
            return 0

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(add_batch, range(2))) == 4
    assert len(store.list(USER)) == 12


@pytest.mark.parametrize(
    "query,title",
    [
        ("chairs", "Ergonomic Office Chair"),
        ("chair", "Set of 2 Dining Chairs"),
        ("e-bike", "Jasion EB5 eBike for Adults"),
        ("ebike", "Folding E-Bike 500W"),
        ("ride on", "Kids Ride-On Truck 12V"),
        ("air fryer", "Ninja Airfryer 4qt"),
        ("batteries", "AA Battery 24 Pack"),
    ],
)
def test_keyword_matching_folds_plurals_and_joined_words(tmp_path, query, title):
    store, jobs = setup_store(tmp_path)
    store.add(USER, query, now=10)
    jobs.record([product(5000, title=title)], now=11)
    assert store.claim(now=12)["queries"] == [query]


def test_walmart_search_results_match_the_requests_own_search(tmp_path):
    store, jobs = setup_store(tmp_path)
    store.add(USER, "electric bicycle", min_value=100, now=10)
    bike = product(64999, number="111111111", title="Soonaway Electric Bike for Adults")
    cheap = product(2999, number="222222222", title="Electric Bike Horn")
    unknown = product(None, number="333333333", title="Electric Bike Seat")
    other = product(64999, number="444444444", title="Soonaway Electric Bike for Adults 2")
    jobs.record([bike, cheap, unknown], now=11, search_queries={
        bike["item_id"]: "electric bicycle", cheap["item_id"]: "electric bicycle",
        unknown["item_id"]: "Electric Bicycle"})
    jobs.record([other], now=11, search_queries={other["item_id"]: "ebike"})
    job = store.claim(now=12)
    assert job["item_id"] == bike["item_id"] and job["search_queries"] == ["electric bicycle"]
    from discord_bridge.request_delivery import render_request_message

    text = render_request_message(job)["embeds"][0]["description"]
    assert "Walmart's search results for: electric bicycle" in text
    store.settle(job, "sent", now=13, message_id="1" * 18)
    # Below the minimum, value not shown, or a different search: no alert.
    assert store.claim(now=14) is None


def test_item_seen_by_several_lanes_alerts_a_member_once(tmp_path):
    store, jobs = setup_store(tmp_path)
    store.add_many(USER, "recliner, chair", now=10)
    item = product(15000)
    jobs.record([item], now=11)  # home/departments lane: keyword match
    jobs.record([item], now=12, search_queries={item["item_id"]: "recliner"})  # requests lane
    job = store.claim(now=13)
    assert job["queries"] == ["recliner", "chair"]
    store.settle(job, "sent", now=14, message_id="1" * 18)
    jobs.record([item], now=15, search_queries={item["item_id"]: "chair"})
    assert store.claim(now=16) is None


def test_unknown_value_is_flagged_in_the_dm(tmp_path):
    from discord_bridge.request_delivery import render_request_message

    store, jobs = setup_store(tmp_path)
    store.add(USER, "recliner", min_value=100, now=10)
    jobs.record([product(None)], now=11)
    text = render_request_message(store.claim(now=12))["embeds"][0]["description"]
    assert "Value: not shown" in text


def test_valueless_variant_rows_do_not_satisfy_a_minimum(tmp_path):
    store, jobs = setup_store(tmp_path)
    store.add(USER, "bike", min_value=100, now=10)
    jobs.record([product(None, title="Blue, variant on Mini Bike Horn")], now=11)
    assert store.claim(now=12) is None
    jobs.record([product(None, number="987654321", title="Electric Bike for Adults")], now=13)
    assert store.claim(now=14)["item"]["title"] == "Electric Bike for Adults"


def test_near_misses_are_logged_with_reasons_and_item_ids(tmp_path):
    store, jobs = setup_store(tmp_path)
    store.add(USER, "recliner", min_value=100, now=10)
    events = []
    sink = lambda name, **fields: events.append((name, fields))  # noqa: E731
    cheap = product(5000, number="111111111")
    gone = normalize_item({"item_id": "ip-222222222", "title": "Recliner chair",
                           "value_cents": 20000, "available": False})
    hit = product(20000, number="333333333")
    jobs.record([cheap, gone, hit], now=11, request_event_sink=sink)
    (name, row), = [event for event in events if event[0] == "request_match"]
    assert row["matched"] == 1 and row["item_ids"] == [hit["item_id"]]
    assert row["below_minimum"] == 1 and row["unavailable"] == 1
    assert row["near_item_ids"] == [cheap["item_id"], gone["item_id"]]


def test_search_results_without_any_shared_word_are_not_matches(tmp_path):
    store, jobs = setup_store(tmp_path)
    store.add(USER, "NAS", now=10)
    store.add(USER, "filing cabinet", now=10)
    cable = product(1399, number="111111111", title="Cat 6 Ethernet Cable 50 ft")
    nas = product(29999, number="222222222", title="Synology 2-Bay Diskstation NAS")
    cabinet = product(12999, number="333333333", title="Rolling File Cabinet with 3 Drawers")
    jobs.record([cable, nas, cabinet], now=11, search_queries={
        cable["item_id"]: "nas", nas["item_id"]: "nas", cabinet["item_id"]: "filing cabinet"})
    found = set()
    while (job := store.claim(now=12)) is not None:
        found.add(job["item_id"])
        store.settle(job, "sent", now=12, message_id="1" * 18)
    assert found == {nas["item_id"], cabinet["item_id"]}


def test_request_dm_goes_before_pro_post_and_releases_it(tmp_path):
    import time

    store, jobs = setup_store(tmp_path)
    woke = []
    store.on_release = lambda: woke.append(1)
    store.add(USER, "recliner", now=1)
    now = time.time()
    jobs.record([product(10000), product(10000, "987654321", "Desk lamp")], now=now)

    def holds():
        with sqlite3.connect(store.path) as db:
            return dict(db.execute(
                "SELECT item_id,MAX(next_attempt)>? FROM jobs WHERE route!='yesterday-recap' "
                "GROUP BY item_id", (now + 1,)))

    # Requested item's Pro posts wait for the DM; unrelated items do not.
    assert holds() == {"ip-123456789": 1, "ip-987654321": 0}
    job = store.claim(now=now)
    store.settle(job, "sent", message_id="444444444444444444")
    assert holds() == {"ip-123456789": 0, "ip-987654321": 0} and woke == [1]


def test_older_matches_wait_and_bundle_while_new_listings_alert_at_once(tmp_path):
    from discord_bridge.request_delivery import render_request_message
    from discord_bridge.request_store import BACKLOG_GATHER_SECONDS

    store, jobs = setup_store(tmp_path)
    old = [product(15000, str(100000000 + index)) for index in range(3)]
    jobs.record(old, now=100)  # Listed before the member asked.
    store.add(USER, "recliner", now=200)
    jobs.record([*old, product(15000, "987654321")], now=300)
    fresh = store.claim(now=301)
    assert fresh["item_id"] == "ip-987654321" and not fresh.get("bundle")
    assert store.settle(fresh, "sent", message_id="444444444444444444")
    assert store.claim(now=300 + BACKLOG_GATHER_SECONDS - 1) is None, "older matches gather first"
    job = store.claim(now=300 + BACKLOG_GATHER_SECONDS)
    bundle = [job, *job["bundle"]]
    assert sorted(row["item_id"] for row in bundle) == sorted(item["item_id"] for item in old)
    payload = render_request_message(store.begin_send(store.validate(job, now=361), now=361,
                                                      channel_id="555555555555555555"))
    # One compact list, not a card per item; numbered Claim buttons.
    assert len(payload["embeds"]) == 1 and "3 items" in payload["content"]
    assert payload["embeds"][0]["description"].count("Matched: recliner") == 3
    assert sum(len(row["components"]) for row in payload["components"]) == 3
    buttons = [button for row in payload["components"] for button in row["components"]]
    assert all("Claim item" in b["label"] and "sampling" in b["url"].lower() for b in buttons)
    assert store.settle(job, "sent", message_id="666666666666666666")
    with sqlite3.connect(store.path) as db:
        states = db.execute(
            "SELECT state,COUNT(*) FROM personal_request_alerts GROUP BY state").fetchall()
    assert states == [("sent", 4)]
    assert store.claim(now=400) is None


def test_bundle_retry_returns_every_member_to_the_queue(tmp_path):
    from discord_bridge.request_store import BACKLOG_GATHER_SECONDS

    store, jobs = setup_store(tmp_path)
    old = [product(15000, str(100000000 + index)) for index in range(2)]
    jobs.record(old, now=100)
    store.add(USER, "recliner", now=200)
    jobs.record(old, now=300)
    job = store.claim(now=300 + BACKLOG_GATHER_SECONDS)
    assert len(job["bundle"]) == 1
    assert store.retry(job, now=361, delay=5)
    again = store.claim(now=367)
    assert len([again, *again["bundle"]]) == 2
