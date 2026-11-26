"""Catch-up DMs for older matches: compact, size-checked, split safely; /request listed."""

import re
import sqlite3
import time
from urllib.parse import urlencode

import pytest
from discord_bridge.api import DiscordError, encode_payload
from discord_bridge.core import JobStore, normalize_item
from discord_bridge.observations import SAMPLING_PARAMS
from discord_bridge.payload_limits import BODY_BYTES, payload_problems, text_length
from discord_bridge.request_delivery import RequestWorker, render_request_message
from discord_bridge.request_render import pack_catchup, render_bundle_message
from discord_bridge.request_store import BACKLOG_GATHER_SECONDS, BUNDLE_LIMIT, RequestStore

from test_discord_request_delivery import DM, STATE, Provider
from test_discord_requests import GUILD, USER

WORDS = ["recliner", *(f"w{index:03d}" for index in range(60))]
# Longest keyword requests (150 characters), every word present in each title.
QUERIES = [" ".join(WORDS[start:start + 30])[:150].rsplit(" ", 1)[0] for start in (0, 10, 20)]
# Emoji, markdown and mention characters first: they are what survives the
# per-item title cut, and they cost the most JSON bytes once escaped.
_NASTY = "😀*_[`|]<>@é "
WORST_TITLE = (_NASTY * 10 + " ".join(WORDS) + " " + _NASTY * 200)[:2000]
PARAMS = "?" + urlencode(SAMPLING_PARAMS)


def item(index, *, value=None, title=None, worst=False, available=True):
    number = str(10**19 + index) if worst else str(100000000 + index)
    raw = {"item_id": f"ip-{number}", "title": title or f"Recliner chair model {index}",
           "value_cents": value, "available": available}
    if worst:
        prefix, suffix = "https://www.walmart.com/ip/", f"/{number}"
        slug = "s" * (512 - len(prefix) - len(suffix) - len(PARAMS))
        raw.update(title=title or WORST_TITLE, product_url=prefix + slug + suffix,
                   value_cents=value if value is not None else 100_000_000 - index)
    return normalize_item(raw)


def fake_jobs(items, *, total=None):
    shown = min(len(items), BUNDLE_LIMIT)
    return [
        {"id": f"{index:032x}", "created": 1.0, "nonce": str(10**24 + index), "item": product,
         "queries": QUERIES, "observed": 1.0,
         "catchup": {"rank": index + 1, "shown": shown, "total": total or len(items)}}
        for index, product in enumerate(items)
    ]


def older_matches(tmp_path, products, queries=("recliner",), start=300):
    """Products listed before the member's requests, then observed again."""
    store = RequestStore(tmp_path / "requests.sqlite3", guild_id=GUILD)
    store.set_dm_status(USER, "ready")
    jobs = JobStore(store.path)
    jobs.record(products, now=start - 200)
    for query in queries:
        store.add(USER, query, now=start - 100)
    jobs.record(products, now=start)
    return store, jobs


def states(store):
    with sqlite3.connect(store.path) as db:
        return dict(db.execute(
            "SELECT state,COUNT(*) FROM personal_request_alerts GROUP BY state").fetchall())


def run(worker, start, ticks=40):
    for offset in range(ticks):
        worker.tick(now=start + offset)


class SizeProvider(Provider):
    """Rejects message bodies like Discord does when `reject(payload)` is true."""

    def __init__(self, reject=None, status=413, provider_code=40005):
        super().__init__()
        self.reject, self.status, self.code = reject, status, provider_code
        self.rejected = []

    def request(self, method, path, payload=None):
        if path == f"/channels/{DM}/messages" and self.reject and self.reject(payload):
            self.calls.append((method, path, payload))
            self.rejected.append(encode_payload(payload))
            raise DiscordError(status=self.status, provider_code=self.code)
        return super().request(method, path, payload)


_CLAIM = re.compile(r"^\*\*(\d+)\.\*\* \[.*\]\((https://[^)\s]+)\)", re.M)


def claims(payload):
    """(rank, link) per listed match: each numbered title links its claim page."""
    assert "components" not in payload, "catch-up DMs carry no buttons"
    return [(int(rank), url) for embed in payload.get("embeds", [])
            for rank, url in _CLAIM.findall(embed.get("description") or "")]


def test_worst_case_items_and_requests_fill_the_old_format_past_discords_limit():
    worst = item(0, worst=True)
    assert len(worst["review_url"]) == 512 and len(worst["title"]) == 2000
    assert all(len(query) <= 150 for query in QUERIES)


@pytest.mark.parametrize("count", [10, 11, 25])
def test_worst_case_catchup_parts_each_pass_every_discord_limit(count):
    products = [item(index, worst=True) for index in range(count)]
    jobs = fake_jobs(products)[:BUNDLE_LIMIT]
    packed = pack_catchup(jobs)
    assert [job["id"] for part in packed for job in part] == [job["id"] for job in jobs]
    for number, part in enumerate(packed, 1):
        payload = render_bundle_message(part, part=number, parts=len(packed))
        assert payload_problems(payload) == []
        assert len(encode_payload(payload)) <= BODY_BYTES
        assert len(payload["embeds"]) == 1 and len(claims(payload)) == len(part)
        assert (f"({number} of {len(packed)})" in payload["content"]) is (len(packed) > 1)
        if count > BUNDLE_LIMIT:
            assert f"Use `/request listed` to see all {count}." in payload["content"]
        else:
            assert "/request listed" not in payload["content"]


def test_typical_ten_older_matches_fit_one_compact_dm():
    products = [item(index, value=10000 + index) for index in range(10)]
    packed = pack_catchup(fake_jobs(products))
    assert len(packed) == 1
    payload = render_bundle_message(packed[0])
    assert payload_problems(payload) == [] and len(encode_payload(payload)) < 6000
    description = payload["embeds"][0]["description"]
    assert description.startswith("**1.** [Recliner chair model 0](https://www.walmart.com/ip/")
    assert [rank for rank, _ in claims(payload)] == list(range(1, 11))
    assert all("sampling" in url.lower() for _, url in claims(payload))


def test_catchup_text_is_inert_markdown_and_never_mentions():
    product = item(1, value=None, title="Chair [50% off](https://evil.example) @everyone **x**")
    jobs = fake_jobs([product, item(2, value=2500)])
    jobs[0]["queries"] = ["chair @here"]
    text = render_bundle_message(jobs)["embeds"][0]["description"]
    assert "\\[50% off\\]" in text and "@​everyone" in text and "@​here" in text
    assert "\\*\\*x\\*\\*" in text and "value not shown" in text and "$25.00" in text


@pytest.mark.parametrize(
    "payload,problem",
    [
        ({"content": "x" * 2001}, "content"),
        ({"embeds": [{}] * 11}, "embeds"),
        ({"embeds": [{"title": "x" * 257}]}, "embed 0 title"),
        ({"embeds": [{"description": "x" * 4097}]}, "embed 0 description"),
        ({"embeds": [{"fields": [{"name": "n", "value": "v"}] * 26}]}, "embed 0 fields"),
        ({"embeds": [{"fields": [{"name": "x" * 257, "value": "v"}]}]}, "embed 0 field 0 name"),
        ({"embeds": [{"fields": [{"name": "n", "value": "x" * 1025}]}]}, "embed 0 field 0 value"),
        ({"embeds": [{"footer": {"text": "x" * 2049}}]}, "embed 0 footer"),
        ({"embeds": [{"author": {"name": "x" * 257}}]}, "embed 0 author"),
        ({"embeds": [{"description": "x" * 4000}, {"description": "x" * 2001}]}, "embed total"),
        ({"components": [{"type": 1, "components": []}] * 6}, "action rows"),
        ({"components": [{"type": 1, "components": [{"label": "a"}] * 6}]}, "row 0 buttons"),
        ({"components": [{"type": 1, "components": [{"label": "x" * 81}]}]}, "row 0 label"),
        ({"components": [{"type": 1, "components": [{"url": "x" * 513}]}]}, "row 0 url"),
        ({"content": "x" * 1900, "embeds": [{"description": "é" * 1600}]}, "body bytes"),
        # Non-BMP characters count twice, as Discord's UTF-16 limits do.
        ({"embeds": [{"title": "😀" * 129}]}, "embed 0 title"),
    ],
)
def test_validator_reports_each_discord_limit(payload, problem):
    assert problem in payload_problems(payload)


def test_validator_accepts_payloads_exactly_at_the_limits():
    payload = {
        "content": "x" * 2000,
        "embeds": [{"title": "x" * 256, "description": "x" * 3000,
                    "footer": {"text": "x" * 2000}}],
        "components": [{"type": 1, "components": [{"label": "x" * 80, "url": "u" * 512}] * 5}],
    }
    assert text_length("😀") == 2
    assert payload_problems(payload, max_bytes=20_000) == []


def test_catchup_shows_ten_highest_values_and_skips_the_rest(tmp_path):
    values = [500 * (index + 1) for index in range(15)]
    store, jobs = older_matches(tmp_path, [item(index, value=value)
                                           for index, value in enumerate(values)])
    provider = Provider()
    worker = RequestWorker(provider, STATE, store, enabled=True)
    run(worker, 300 + BACKLOG_GATHER_SECONDS)
    assert len(provider.messages) == 1
    payload = provider.messages[0]
    assert payload_problems(payload) == []
    assert "15 items matching your requests were" in payload["content"]
    assert "Use `/request listed` to see all 15." in payload["content"]
    description = payload["embeds"][0]["description"]
    shown = sorted(values, reverse=True)[:10]
    assert [f"${value / 100:,.2f}" in description for value in shown] == [True] * 10
    assert description.index(f"${shown[0] / 100:,.2f}") < description.index(
        f"${shown[-1] / 100:,.2f}")
    assert "$5.00" not in description
    assert states(store) == {"sent": 10, "skipped": 5}
    assert store.stats()["reasons"]["skipped"] == {"catchup_overflow": 5}
    # Skipped older matches never re-arm into another catch-up DM.
    jobs.record([item(index, value=value) for index, value in enumerate(values)], now=500)
    run(worker, 500 + BACKLOG_GATHER_SECONDS, ticks=5)
    assert len(provider.messages) == 1


def test_oversized_catchup_is_split_at_claim_and_every_part_is_settled(tmp_path):
    products = [item(index, worst=True) for index in range(12)]
    store, _ = older_matches(tmp_path, products, queries=QUERIES)
    provider = Provider()
    worker = RequestWorker(provider, STATE, store, enabled=True)
    run(worker, 300 + BACKLOG_GATHER_SECONDS)
    messages = provider.messages
    assert len(messages) >= 2, "worst-case items need more than one DM"
    for number, payload in enumerate(messages, 1):
        assert payload_problems(payload) == []
        assert payload["content"].startswith(f"({number} of {len(messages)}) ")
        assert "Use `/request listed` to see all 12." in payload["content"]
    assert len({payload["nonce"] for payload in messages}) == len(messages)
    ranks = [rank for payload in messages for rank, _ in claims(payload)]
    assert ranks == list(range(1, 11))
    assert states(store) == {"sent": 10, "skipped": 2}


def test_413_splits_a_bundle_and_never_resends_the_rejected_body(tmp_path):
    products = [item(index, value=10000 + index) for index in range(6)]
    store, _ = older_matches(tmp_path, products)
    provider = SizeProvider(reject=lambda payload: len(claims(payload)) > 2)
    events = []
    worker = RequestWorker(provider, STATE, store, enabled=True,
                           event_sink=lambda event, **fields: events.append((event, fields)))
    run(worker, 300 + BACKLOG_GATHER_SECONDS)
    sent = [payload for payload in provider.messages
            if encode_payload(payload) not in provider.rejected]
    assert len(provider.rejected) == len(set(provider.rejected)), "a rejected body was resent"
    assert not set(provider.rejected) & {encode_payload(payload) for payload in sent}
    assert states(store) == {"sent": 6}
    assert sorted(rank for payload in sent for rank, _ in claims(payload)) == list(range(1, 7))
    assert all(len(claims(payload)) <= 2 for payload in sent)
    # Parts stay in order; a split renumbers only parts not yet sent, so the
    # last DM always reads "(n of n)".
    labels = [payload["content"][1:].split(")")[0].split(" of ") for payload in sent]
    assert [int(number) for number, _ in labels] == list(range(1, len(sent) + 1))
    assert labels[-1] == [str(len(sent))] * 2
    retries = [fields for event, fields in events if event == "dispatch_retry"]
    assert retries and all(fields["reason"] == "payload_too_large" for fields in retries)
    assert all(fields["status"] == 413 for fields in retries)


def test_embed_size_400_also_splits_a_bundle(tmp_path):
    store, _ = older_matches(tmp_path, [item(index, value=900 + index) for index in range(4)])
    provider = SizeProvider(reject=lambda payload: len(claims(payload)) > 1,
                            status=400, provider_code=50035)
    worker = RequestWorker(provider, STATE, store, enabled=True)
    run(worker, 300 + BACKLOG_GATHER_SECONDS)
    assert states(store) == {"sent": 4}
    assert len(provider.rejected) == len(set(provider.rejected))


def test_413_for_a_single_item_fails_with_a_clear_reason_and_is_not_retried(tmp_path):
    store, _ = older_matches(tmp_path, [item(1, value=5000)])
    provider = SizeProvider(reject=lambda payload: True)
    worker = RequestWorker(provider, STATE, store, enabled=True)
    result = worker.tick(now=300 + BACKLOG_GATHER_SECONDS)
    assert result["failed"] == 1 and result["retried"] == 0
    run(worker, 301 + BACKLOG_GATHER_SECONDS, ticks=60)
    assert len(provider.rejected) == 1
    assert states(store) == {"failed": 1}
    assert store.stats()["reasons"]["failed"] == {"payload_too_large": 1}


def test_fresh_single_alert_413_fails_without_retrying(tmp_path):
    store = RequestStore(tmp_path / "requests.sqlite3", guild_id=GUILD)
    store.set_dm_status(USER, "ready")
    store.add(USER, "recliner", now=10)
    JobStore(store.path).record([item(1, value=5000)], now=11)
    provider = SizeProvider(reject=lambda payload: True)
    worker = RequestWorker(provider, STATE, store, enabled=True)
    assert worker.tick(now=12)["failed"] == 1
    run(worker, 13, ticks=60)
    assert len(provider.rejected) == 1 and states(store) == {"failed": 1}


def test_local_validator_splits_before_any_oversized_post(tmp_path, monkeypatch):
    # Even if packing were skipped, no body breaking a limit is ever submitted.
    monkeypatch.setattr("discord_bridge.request_store.pack_catchup", lambda jobs: [jobs])
    products = [item(index, worst=True) for index in range(10)]
    store, _ = older_matches(tmp_path, products, queries=QUERIES)
    provider = Provider()
    worker = RequestWorker(provider, STATE, store, enabled=True)
    run(worker, 300 + BACKLOG_GATHER_SECONDS, ticks=60)
    assert provider.messages and all(payload_problems(payload) == []
                                     for payload in provider.messages)
    assert states(store) == {"sent": 10}


def test_rate_limited_part_resends_the_same_body_and_nonce(tmp_path):
    store, _ = older_matches(tmp_path, [item(index, value=700 + index) for index in range(3)])
    provider = Provider()
    provider.send_error = DiscordError(status=429, retry_after=2)
    worker = RequestWorker(provider, STATE, store, enabled=True)
    start = 300 + BACKLOG_GATHER_SECONDS
    assert worker.tick(now=start)["throttled"] == 1
    provider.send_error = None
    assert worker.tick(now=start + 3)["sent"] == 1
    first, second = provider.messages
    assert encode_payload(first) == encode_payload(second)
    assert states(store) == {"sent": 3}


def test_expired_unsent_catchup_rearms_as_a_new_catchup(tmp_path):
    products = [item(index, value=100 + index) for index in range(2)]
    store, jobs = older_matches(tmp_path, products)
    job = store.claim(now=360)
    assert job["catchup"]["parts"] == 1 and len(job["bundle"]) == 1
    group = job["catchup"]["group"]
    assert store.claim(now=360 + 25 * 60) is None  # Expired unsent.
    jobs.record(products, now=3000)
    again = store.claim(now=3000 + BACKLOG_GATHER_SECONDS)
    assert again["catchup"]["group"] != group and len(again["bundle"]) == 1


def test_single_older_match_keeps_the_full_card():
    product = item(1, value=12345)
    job = fake_jobs([product])[0]
    payload = render_request_message(job)
    assert payload["content"] == "Your requested item was found!"
    assert payload["embeds"][0]["title"].startswith("$123.45 | ")


def test_listed_returns_current_older_matches_by_value(tmp_path):
    now = time.time()
    products = [item(index, value=1000 * (index + 1)) for index in range(4)]
    store, jobs = older_matches(tmp_path, products, start=now - 600)
    store.claim(now=now)  # Older matches become one catch-up.
    fresh = item(50, value=99999)
    jobs.record([fresh], now=now - 500)  # A new listing is not an older match.
    jobs.record([item(1, value=2000, available=False)], now=now - 400)
    rows = store.listed(USER, now=now)
    assert [row["item"]["value_cents"] for row in rows] == [4000, 3000, 1000]
    assert rows[0]["queries"] == ["recliner"] and rows[0]["seen"] == pytest.approx(now - 600)
    # Stale observations drop out; another member sees nothing.
    assert store.listed(USER, now=now + 3 * 3600) == []
    assert store.listed("444444444444444444", now=now) == []


def test_listed_follows_request_edits(tmp_path):
    now = time.time()
    store, _ = older_matches(tmp_path, [item(1, value=1000)], start=now - 60)
    (request,) = store.list(USER)
    assert len(store.listed(USER, now=now)) == 1
    store.edit(USER, request["id"], "desk lamp", now=now)
    assert store.listed(USER, now=now) == []


def test_sent_and_skipped_older_matches_stay_listed_after_an_edit(tmp_path):
    now = time.time()
    products = [item(index, value=100 * (index + 1)) for index in range(12)]
    store, jobs = older_matches(tmp_path, products, start=now - 900)
    worker = RequestWorker(Provider(), STATE, store, enabled=True)
    run(worker, now - 900 + BACKLOG_GATHER_SECONDS, ticks=3)
    assert states(store) == {"sent": 10, "skipped": 2}
    assert len(store.listed(USER, now=now)) == 12
    (request,) = store.list(USER)
    store.edit(USER, request["id"], "recliner chair", now=now - 500)
    assert store.listed(USER, now=now) == []
    jobs.record(products, now=now - 400)  # Seen again: the edited request matches.
    rows = store.listed(USER, now=now)
    assert len(rows) == 12 and {row["state"] for row in rows} == {"sent", "skipped"}
    assert rows[0]["queries"] == ["recliner chair"]
    assert states(store) == {"sent": 10, "skipped": 2}, "nothing re-armed"


def _listed_commands(tmp_path, count):
    from test_discord_request_commands import USER as MEMBER
    from test_discord_request_commands import real_commands

    commands, interaction, client, store = real_commands(tmp_path)
    store.set_dm_status(MEMBER, "ready")
    jobs = JobStore(store.path)
    now = time.time()
    products = [item(index, worst=index % 2 == 0, value=100 * (index + 1))
                for index in range(count)]
    jobs.record(products, now=now - 900)
    store.add(MEMBER, "recliner", now=now - 800)
    jobs.record(products, now=now - 700)
    return commands, interaction, client


def test_request_listed_replies_privately_in_pages_within_limits(tmp_path):
    from test_discord_request_commands import Interaction, invoke

    commands, interaction, client = _listed_commands(tmp_path, 30)
    invoke(commands.listed, interaction)
    content, kwargs = interaction.messages[-1]
    assert kwargs["ephemeral"] is True and "30 items matching your requests were" in content
    assert "Page 1 of" in content and "/request listed page:2" in content
    pages = int(content.split("Page 1 of ")[1].split(".")[0])
    seen = []
    for number in range(1, pages + 1):
        reply = Interaction()
        client.interaction = reply
        invoke(commands.listed, reply, page=number)
        content, kwargs = reply.messages[-1]
        embed = kwargs["embed"]
        payload = {"content": content, "embeds": [embed.to_dict()]}
        assert payload_problems(payload) == []
        seen += [line.split("**")[1] for line in embed.description.split("\n\n")]
    assert seen == [f"{index}." for index in range(1, 31)]
    # A page past the end shows the last page instead of failing.
    reply = Interaction()
    client.interaction = reply
    invoke(commands.listed, reply, page=99)
    assert f"Page {pages} of {pages}." in reply.messages[-1][0]


def test_request_listed_with_no_matches_and_for_non_pro_members(tmp_path):
    from test_discord_request_commands import STARTER, make, real_commands
    from test_discord_request_commands import invoke as run_command

    commands, interaction, _, _ = real_commands(tmp_path)
    run_command(commands.listed, interaction)
    assert interaction.messages[-1][0].startswith("No already-listed matches right now.")
    commands, interaction, client, _ = make(roles=(STARTER,))
    run_command(commands.listed, interaction)
    assert "Pro members only" in interaction.messages[-1][0]
    assert not any("/messages" in path for _, path, _ in client.calls)


def test_request_listed_outside_the_requests_channel_only_points_there():
    from test_discord_request_commands import OTHER, invoke, make, pointed_only

    commands, interaction, client, _ = make(channel=OTHER)
    invoke(commands.listed, interaction)
    pointed_only(interaction)
    assert client.calls == []
