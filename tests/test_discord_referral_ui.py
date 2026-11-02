"""Private referral-button entry, identity boundaries and honest billing status."""

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from discord_bridge.referral_ui import ReferralHandler, referral_button

GUILD = "1700000000000000001"
WELCOME = "1700000000000000002"
GUIDE = "1700000000000000003"
USER = "1700000000000000005"
OTHER = "1700000000000000006"
BOT = "1700000000000000008"
STATE = {
    "guild_id": GUILD,
    "bot_user_id": BOT,
    "channels": {"welcome": WELCOME, "how-it-works": GUIDE},
}
VIEW = {
    "ready": True,
    "url": "https://whop.com/checkout/plan_example?promoCode=RR12345",
    "code": "RR12345",
    "queued": 0,
    "applied": 0,
    "used": 0,
    "membership_state": "active",
    "next_payment_cents": None,
    "normal_price_cents": 3000,
    "reason": None,
}
_HANDLERS = []


@pytest.fixture(autouse=True)
def close_handlers():
    yield
    while _HANDLERS:
        close = getattr(_HANDLERS.pop(), "close", None)
        if close is not None:
            close()


class Interaction:
    def __init__(self):
        self.guild_id, self.channel_id = int(GUILD), int(WELCOME)
        self.user = SimpleNamespace(id=int(USER), bot=False)
        self.message = SimpleNamespace(author=SimpleNamespace(id=int(BOT)))
        self.type = SimpleNamespace(value=3)
        self.data = {"custom_id": "referral:link:v1", "component_type": 2}
        self.acknowledged = False
        self.events, self.messages = [], []
        self.defer_error = self.followup_error = None
        self.response = SimpleNamespace(
            defer=self.defer,
            is_done=lambda: self.acknowledged,
            send_message=self.send,
        )
        self.followup = SimpleNamespace(send=self.followup_send)

    async def defer(self, **kwargs):
        self.events.append(("defer", kwargs))
        if self.defer_error:
            raise self.defer_error
        self.acknowledged = True

    async def send(self, content, **kwargs):
        self.acknowledged = True
        self.messages.append((content, kwargs))

    async def followup_send(self, content, **kwargs):
        self.events.append(("followup", kwargs))
        if self.followup_error:
            raise self.followup_error
        await self.send(content, **kwargs)


class Service:
    def __init__(self, view=None, interaction=None):
        self.view = dict(VIEW if view is None else view)
        self.interaction = interaction
        self.calls = []
        self.error = None

    def member_view(self, user):
        if self.interaction:
            assert self.interaction.acknowledged, "Acknowledge before API or database work"
        self.calls.append((user, threading.get_ident()))
        if self.error:
            raise self.error
        return self.view


def make(view=None, **options):
    interaction = Interaction()
    service = Service(view, interaction)
    handler = ReferralHandler(STATE, service, **options)
    _HANDLERS.append(handler)
    return handler, interaction, service


def run(handler, interaction):
    asyncio.run(handler.handle(interaction))


def assert_private(interaction):
    for _, options in interaction.messages:
        assert options["ephemeral"] is True
        assert options["allowed_mentions"].to_dict() == {"parse": []}


def test_button_and_handler_survive_a_process_restart_without_registration():
    button = referral_button()
    assert button == {
        "type": 2,
        "style": 1,
        "label": "Get my referral link",
        "custom_id": "referral:link:v1",
    }
    first, interaction, _ = make()
    restarted = ReferralHandler(STATE, Service())
    assert first.accepts(interaction) and restarted.accepts(interaction)
    # Public button data contains no member identity, credential or member-specific URL.
    assert "url" not in button and USER not in str(button)


@pytest.mark.parametrize(
    "custom_id", ["referral:link", "referral:link:v2", "request-ui:x:delete", 12]
)
def test_other_component_namespaces_are_left_untouched(custom_id):
    handler, interaction, service = make()
    interaction.data["custom_id"] = custom_id
    assert not handler.accepts(interaction)
    run(handler, interaction)
    assert not service.calls and not interaction.messages and not interaction.events


@pytest.mark.parametrize("kind", [None, 2, 4, 5])
def test_only_message_components_are_accepted(kind):
    handler, interaction, service = make()
    interaction.type = SimpleNamespace(value=kind)
    assert not handler.accepts(interaction)
    run(handler, interaction)
    assert not service.calls and not interaction.messages


@pytest.mark.parametrize(
    "mutation",
    [
        lambda i: setattr(i, "guild_id", int(OTHER)),
        lambda i: setattr(i, "guild_id", None),
        lambda i: setattr(i, "channel_id", int(OTHER)),
        lambda i: setattr(i.user, "id", "not-an-id"),
        lambda i: setattr(i.user, "bot", True),
        lambda i: setattr(i.user, "bot", None),
        lambda i: setattr(i, "user", None),
        lambda i: setattr(i, "message", None),
        lambda i: setattr(i.message.author, "id", int(OTHER)),
    ],
)
def test_untrusted_context_never_reaches_billing_service(mutation):
    handler, interaction, service = make()
    mutation(interaction)
    run(handler, interaction)
    assert not service.calls
    assert "setup guide" in interaction.messages[-1][0]
    assert VIEW["url"] not in str(interaction.messages)
    assert_private(interaction)


def test_free_member_gets_a_private_link_after_deferring_and_off_event_loop():
    handler, interaction, service = make()
    main_thread = threading.get_ident()
    run(handler, interaction)
    assert interaction.events[0] == ("defer", {"ephemeral": True, "thinking": True})
    assert len(service.calls) == 1 and service.calls[0][0] == USER
    assert service.calls[0][1] != main_thread
    text = interaction.messages[-1][0]
    assert VIEW["url"] in text and "`RR12345`" in text
    assert "30% off their first month" in text and "30% off your next month" in text
    assert "normal price" in text and "$30.00/month" in text
    assert "No paid referrals yet" in text
    assert_private(interaction)


def test_blocked_service_does_not_block_discord_event_loop():
    entered, release = threading.Event(), threading.Event()
    handler, interaction, service = make()

    def blocking(user):
        assert interaction.acknowledged and user == USER
        entered.set()
        assert release.wait(5)
        return dict(VIEW)

    service.member_view = blocking

    async def scenario():
        task = asyncio.create_task(handler.handle(interaction))
        try:
            for _ in range(200):
                if entered.is_set():
                    break
                await asyncio.sleep(0.001)
            assert entered.is_set() and not task.done()
            # This coroutine must run while the synchronous billing operation is blocked.
            await asyncio.sleep(0)
        finally:
            release.set()
        await task

    asyncio.run(scenario())
    assert_private(interaction)


def test_queued_reward_is_never_advertised_as_applied():
    handler, interaction, _ = make({**VIEW, "queued": 2})
    run(handler, interaction)
    text = interaction.messages[-1][0]
    assert "2 discounted months queued" in text
    assert "Next payment:" not in text and "$21" not in text


def test_applied_reward_shows_actual_next_payment_and_normal_renewal():
    handler, interaction, _ = make(
        {**VIEW, "queued": 2, "applied": 1, "used": 3, "next_payment_cents": 2100}
    )
    run(handler, interaction)
    text = interaction.messages[-1][0]
    assert "Next payment: **$21.00**" in text
    assert "$30.00/month" in text and "2 more discounted months queued" in text
    assert "Discounted payments used: 3" in text
    assert_private(interaction)


def test_future_tier_uses_service_amounts_without_a_fixed_nine_dollar_reward():
    handler, interaction, _ = make(
        {**VIEW, "applied": 1, "next_payment_cents": 6930, "normal_price_cents": 9900}
    )
    run(handler, interaction)
    text = interaction.messages[-1][0]
    assert "$69.30" in text and "$99.00/month" in text
    assert "$21.00" not in text and "$9" not in text.replace("$99.00", "")


def test_manual_tester_keeps_rewards_until_a_paid_membership_is_linked():
    handler, interaction, _ = make({**VIEW, "queued": 2, "membership_state": "none"})
    run(handler, interaction)
    text = interaction.messages[-1][0]
    assert "2 discounted months saved" in text
    assert "link a paid membership through Whop" in text
    assert "Next payment:" not in text


def test_another_promotion_does_not_erase_or_claim_to_apply_queued_rewards():
    handler, interaction, _ = make({**VIEW, "queued": 1, "membership_state": "conflict"})
    run(handler, interaction)
    text = interaction.messages[-1][0]
    assert "Another promotion is active" in text and "remain queued" in text
    assert "Next payment:" not in text


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/checkout/plan_test",
        "https://whop.com.evil.example/checkout/plan_test",
        "http://whop.com/checkout/plan_test",
        "https://user:password@whop.com/checkout/plan_test",
        "https://whop.com:444/checkout/plan_test",
        "https://whop.com/dashboard/biz_private",
        "https://whop.com/checkout/plan_test> @everyone",
        "https://whop.com/checkout/plan_test#secret",
    ],
)
def test_unsafe_or_noncheckout_links_are_never_rendered(url):
    handler, interaction, _ = make({**VIEW, "url": url})
    run(handler, interaction)
    assert "temporarily unavailable" in interaction.messages[-1][0]
    assert url not in str(interaction.messages)
    assert_private(interaction)


@pytest.mark.parametrize(
    "bad",
    [
        {"ready": False},
        {**VIEW, "code": "@everyone"},
        {**VIEW, "queued": -1},
        {**VIEW, "normal_price_cents": True},
    ],
)
def test_unready_or_malformed_view_returns_only_safe_retry_guidance(bad):
    handler, interaction, _ = make(bad)
    run(handler, interaction)
    assert "temporarily unavailable" in interaction.messages[-1][0]
    assert VIEW["url"] not in str(interaction.messages)
    assert_private(interaction)


def test_service_failures_do_not_expose_exception_or_secret(caplog):
    handler, interaction, service = make()
    service.error = RuntimeError("PRIVATE_TOKEN payment customer details")
    run(handler, interaction)
    assert "temporarily unavailable" in interaction.messages[-1][0]
    assert "PRIVATE_TOKEN" not in str(interaction.messages) + caplog.text
    assert_private(interaction)


def test_failed_acknowledgement_never_starts_billing_work():
    handler, interaction, service = make()
    interaction.defer_error = RuntimeError("expired interaction PRIVATE_TOKEN")
    run(handler, interaction)
    assert service.calls == []
    assert "PRIVATE_TOKEN" not in str(interaction.messages)


def test_already_acknowledged_interaction_is_not_processed_twice():
    handler, interaction, service = make()
    interaction.acknowledged = True
    run(handler, interaction)
    assert service.calls == [] and interaction.messages == [] and interaction.events == []


def test_followup_failure_is_contained_and_does_not_repeat_service_work(caplog):
    handler, interaction, service = make()
    interaction.followup_error = RuntimeError("PRIVATE_TOKEN webhook URL")
    run(handler, interaction)
    assert len(service.calls) == 1
    assert "PRIVATE_TOKEN" not in str(interaction.messages) + caplog.text


@pytest.mark.parametrize(
    "state",
    [{}, {**STATE, "bot_user_id": "bad"}, {**STATE, "channels": {}}, {**STATE, "guild_id": None}],
)
def test_invalid_configuration_cannot_create_a_handler(state):
    with pytest.raises(ValueError):
        ReferralHandler(state, Service())


def interaction_for(user):
    interaction = Interaction()
    interaction.user.id = int(user)
    return interaction


async def wait_for_calls(calls, count):
    for _ in range(1000):
        if len(calls) >= count:
            return
        await asyncio.sleep(0.001)
    raise AssertionError("The admitted referral work did not start")


def blocking_service(service, release):
    def member_view(user):
        service.calls.append((user, threading.get_ident()))
        assert release.wait(5)
        return dict(VIEW)

    service.member_view = member_view


def test_referral_work_cannot_starve_the_shared_discord_executor():
    handler, first, service = make()
    release = threading.Event()
    blocking_service(service, release)

    async def scenario():
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        task = asyncio.create_task(handler.handle(first))
        try:
            await wait_for_calls(service.calls, 1)
            # Request commands still use the shared executor. Referral I/O must
            # not occupy its only worker while the provider is blocked.
            result = await asyncio.wait_for(asyncio.to_thread(lambda: "request-ready"), 0.3)
            assert result == "request-ready" and not task.done()
        finally:
            release.set()
            await task

    asyncio.run(scenario())


def test_one_member_has_only_one_inflight_service_call():
    handler, first, service = make()
    release = threading.Event()
    blocking_service(service, release)
    repeated = interaction_for(USER)

    async def scenario():
        task = asyncio.create_task(handler.handle(first))
        try:
            await wait_for_calls(service.calls, 1)
            await asyncio.wait_for(handler.handle(repeated), 0.3)
            assert len(service.calls) == 1
            assert "already being prepared" in repeated.messages[-1][0]
            assert_private(repeated)
        finally:
            release.set()
            await task

    asyncio.run(scenario())


def test_other_member_burst_is_bounded_without_queuing_hidden_provider_jobs():
    handler, first, service = make()
    release = threading.Event()
    blocking_service(service, release)
    second = interaction_for(OTHER)
    third = interaction_for("1700000000000000009")

    async def scenario():
        tasks = [asyncio.create_task(handler.handle(row)) for row in (first, second)]
        try:
            await wait_for_calls(service.calls, 2)
            await asyncio.wait_for(handler.handle(third), 0.3)
            assert len(service.calls) == 2
            assert "busy" in third.messages[-1][0]
            assert_private(third)
        finally:
            release.set()
            await asyncio.gather(*tasks)
        await asyncio.sleep(0)
        assert len(service.calls) == 2  # The rejected click was never queued.

    asyncio.run(scenario())


def test_timeout_does_not_readmit_a_member_while_the_underlying_thread_is_alive():
    handler, first, service = make(timeout=0.02)
    release = threading.Event()
    blocking_service(service, release)

    async def scenario():
        try:
            await handler.handle(first)
            assert "temporarily unavailable" in first.messages[-1][0]
            repeated = interaction_for(USER)
            await asyncio.wait_for(handler.handle(repeated), 0.3)
            assert "already being prepared" in repeated.messages[-1][0]
            assert len(service.calls) == 1
        finally:
            release.set()
        for _ in range(1000):
            retry = interaction_for(USER)
            await handler.handle(retry)
            if len(service.calls) == 2:
                break
            await asyncio.sleep(0.001)
        assert len(service.calls) == 2
        assert VIEW["url"] in retry.messages[-1][0]

    asyncio.run(scenario())


def test_timeouts_keep_total_concurrency_bounded_across_different_members():
    handler, first, service = make(timeout=0.02)
    release = threading.Event()
    blocking_service(service, release)

    async def scenario():
        try:
            await handler.handle(first)
            await handler.handle(interaction_for(OTHER))
            third = interaction_for("1700000000000000009")
            await asyncio.wait_for(handler.handle(third), 0.3)
            assert "busy" in third.messages[-1][0]
            assert len(service.calls) == 2
        finally:
            release.set()

    asyncio.run(scenario())


def test_close_is_nonblocking_and_stops_admitting_new_work():
    handler, first, service = make()
    release = threading.Event()
    blocking_service(service, release)

    async def scenario():
        task = asyncio.create_task(handler.handle(first))
        try:
            await wait_for_calls(service.calls, 1)
            started = time.monotonic()
            handler.close()
            assert time.monotonic() - started < 0.3
            after_close = interaction_for(OTHER)
            await handler.handle(after_close)
            assert "temporarily unavailable" in after_close.messages[-1][0]
            assert len(service.calls) == 1
            assert_private(after_close)
        finally:
            release.set()
            await task

    asyncio.run(scenario())


def test_close_between_admission_and_acknowledgement_prevents_submission():
    handler, interaction, service = make()

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def defer(**kwargs):
            entered.set()
            await release.wait()
            interaction.acknowledged = True

        interaction.response.defer = defer
        task = asyncio.create_task(handler.handle(interaction))
        try:
            await entered.wait()
            handler.close()
        finally:
            release.set()
            await task
        assert not service.calls
        assert "temporarily unavailable" in interaction.messages[-1][0]

    asyncio.run(scenario())


def test_public_catalog_price_does_not_claim_to_be_the_members_actual_normal_bill():
    handler, interaction, _ = make(
        {**VIEW, "friend_normal_price_cents": 9900, "applied": 1, "next_payment_cents": 1400}
    )
    run(handler, interaction)
    text = interaction.messages[-1][0]
    assert "Standard plan price: **$30.00/month**" in text
    assert "Your normal renewals" not in text
    assert "Next payment: **$14.00**" in text
    assert "Friends using this link renew at **$99.00/month**" in text


def test_cancelled_waiter_does_not_release_a_still_running_members_admission():
    handler, first, service = make()
    release = threading.Event()
    blocking_service(service, release)

    async def scenario():
        task = asyncio.create_task(handler.handle(first))
        try:
            await wait_for_calls(service.calls, 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            repeated = interaction_for(USER)
            await handler.handle(repeated)
            assert "already being prepared" in repeated.messages[-1][0]
            assert len(service.calls) == 1
        finally:
            release.set()

    asyncio.run(scenario())


def test_late_provider_exception_after_timeout_cannot_log_an_unobserved_secret(caplog):
    handler, first, service = make(timeout=0.02)
    release, completed = threading.Event(), threading.Event()

    def late_failure(user):
        assert user == USER
        assert release.wait(5)
        completed.set()
        raise RuntimeError("PRIVATE_TOKEN provider customer details")

    service.member_view = late_failure

    async def scenario():
        failures = []
        asyncio.get_running_loop().set_exception_handler(
            lambda _loop, context: failures.append(context)
        )
        try:
            await handler.handle(first)
            assert "temporarily unavailable" in first.messages[-1][0]
        finally:
            release.set()
        for _ in range(1000):
            if completed.is_set():
                break
            await asyncio.sleep(0.001)
        assert completed.is_set()
        # Drain concurrent-future and shield callbacks on this still-live loop.
        for _ in range(5):
            await asyncio.sleep(0)
        assert not failures

    asyncio.run(scenario())
    assert "PRIVATE_TOKEN" not in caplog.text + str(first.messages)
