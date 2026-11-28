"""Discord-first Pro checkout: personal links and Whop-driven Pro role sync."""

import asyncio
from types import SimpleNamespace

import pytest
from discord_bridge.api import DiscordError
from discord_bridge.pro_access import (
    ACCOUNT_ID,
    CUSTOM_ID,
    REDEEM_ID,
    REDEEM_MODAL_ID,
    AccountHandler,
    CompositeHandler,
    ProAccessService,
    ProAccessStore,
    ProCheckoutHandler,
    RedeemHandler,
    _text,
    _typed_code,
)

GUILD = "1700000000000000001"
WELCOME = "1700000000000000002"
ROLE = "1700000000000000004"
USER = "1700000000000000005"
OTHER = "1700000000000000006"
BOT = "1700000000000000008"
FOUND, STD = "plan_found15", "plan_std30"
STATE = {"guild_id": GUILD, "bot_user_id": BOT, "channels": {"welcome": WELCOME}}


def member(mid, status="active", discord=USER, plan=FOUND):
    return {"id": mid, "status": status, "variant_id": plan,
            "metadata": {"discord_user_id": discord} if discord else {}}


def paid(mid, amount="15.00", plan=FOUND, **changes):
    return {"id": f"pay_{mid}", "membership_id": mid, "variant_id": plan, "status": "paid",
            "total": {"currency": "usd", "amount": amount}, **changes}


class Whop:
    def __init__(self, rows=(), linked=None, payments=(), retired=()):
        self.rows, self.linked = list(rows), linked or {}
        self.payments = list(payments)
        self.retired = list(retired)  # memberships on plans outside the scope
        self.created = []

    def list_payments(self):
        return list(self.payments)

    def list_memberships(self, discord_id=None, *, any_plan=False):
        rows = self.linked.get(discord_id, []) if discord_id else list(self.rows)
        if any_plan and not discord_id:
            rows = rows + self.retired
        return rows

    def create_checkout(self, plan, *, metadata, redirect_url, idempotency_key):
        self.created.append((plan, metadata, redirect_url, idempotency_key))
        return {"id": "ch_1", "purchase_url": f"https://whop.com/checkout/{plan}/?session=x{len(self.created)}"}


class Discord:
    def __init__(self, missing=()):
        self.calls, self.missing = [], set(missing)
        self.dms, self.dm_texts = [], []

    def request(self, method, path, payload=None):
        if path == "/users/@me/channels":
            self.dms.append(payload["recipient_id"])
            return {"id": "1700000000000000099"}
        if path.startswith("/channels/"):
            self.dm_texts.append(payload["content"])
            return {}
        user = path.split("/members/")[1].split("/")[0]
        if user in self.missing:
            raise DiscordError("Unknown Member", status=404)
        self.calls.append((method, user))


CODES = {"rrfriendcode1": OTHER, "rrmycode0001": USER}


def service(tmp_path, whop, discord=None, total=25, now=1000.0, dm=None, clock=None,
            no_trial=None):
    return ProAccessService(
        whop, discord or Discord(), ProAccessStore(tmp_path / "pro.sqlite3"),
        guild_id=GUILD, pro_role=ROLE, founding_plan=FOUND, standard_plan=STD,
        founding_total=total, redirect_url="https://discord.com/channels/x/y", welcome_dm=dm,
        code_owner=lambda code: CODES.get(code.lower()),
        clock=(lambda: clock[0]) if clock else (lambda: now), no_trial_plans=no_trial,
    )


def test_poll_grants_once_and_removes_after_cancel(tmp_path):
    whop, discord = Whop([member("mem_a")]), Discord()
    svc = service(tmp_path, whop, discord)
    svc.poll()
    svc.poll()
    assert discord.calls == [("PUT", USER)]
    whop.rows = [member("mem_a", status="canceled")]
    svc.poll()
    assert discord.calls[-1] == ("DELETE", USER)
    svc.poll()
    assert len(discord.calls) == 2


def test_poll_ignores_memberships_without_discord_metadata(tmp_path):
    discord = Discord()
    svc = service(tmp_path, Whop([member("mem_a", discord=None), member("mem_b", discord="bad")]), discord)
    svc.poll()
    assert discord.calls == []


def test_member_not_in_server_is_retried_later(tmp_path):
    discord = Discord(missing={USER})
    svc = service(tmp_path, Whop([member("mem_a")]), discord)
    svc.poll()
    assert svc.store.granted() == {}
    discord.missing.clear()
    svc.poll()
    assert discord.calls == [("PUT", USER)]


def test_removal_keeps_role_when_whop_links_another_active_membership(tmp_path):
    whop, discord = Whop([member("mem_a")]), Discord()
    svc = service(tmp_path, whop, discord)
    svc.poll()
    whop.rows = [member("mem_a", status="expired")]
    whop.linked = {USER: [member("mem_z", discord=None)]}
    svc.poll()
    assert discord.calls == [("PUT", USER)]
    assert svc.store.granted() == {}


def test_past_due_keeps_access_only_for_members_who_paid_before(tmp_path):
    whop, discord = Whop([member("mem_a")], payments=[paid("mem_a")]), Discord()
    svc = service(tmp_path, whop, discord)
    svc.poll()
    whop.rows = [member("mem_a", status="past_due")]
    svc.poll()
    assert discord.calls == [("PUT", USER)]


def test_trial_whose_first_charge_fails_loses_pro(tmp_path):
    zero = paid("mem_a", amount="0.00")  # the $0 trial start is not a payment
    whop, discord = Whop([member("mem_a", status="trialing")], payments=[zero]), Discord()
    svc = service(tmp_path, whop, discord)
    svc.poll()
    whop.rows = [member("mem_a", status="past_due")]
    svc.poll()
    assert discord.calls == [("PUT", USER), ("DELETE", USER)]
    view = svc.member_view(USER)
    assert view == {"ready": True, "payment_failed": True} and not whop.created
    assert "Payment methods" in _text(view)


def test_role_is_reasserted_so_rejoining_members_get_pro_back(tmp_path):
    clock = [1000.0]
    whop, discord = Whop([member("mem_a")]), Discord()
    svc = service(tmp_path, whop, discord, clock=clock)
    svc.poll()  # grant
    svc.poll()  # within the reassert window: no extra call
    assert discord.calls == [("PUT", USER)]
    clock[0] += 121  # member left (Discord dropped the role) and rejoined
    svc.poll()
    assert discord.calls == [("PUT", USER)] * 2
    assert svc.store.granted() == {USER: "mem_a"}


def test_reassert_runs_in_small_batches_and_pauses_on_rate_limit(tmp_path):
    ids = [f"17000000000000001{i:02d}" for i in range(12)]
    whop = Whop([member(f"mem_{i}", discord=d) for i, d in enumerate(ids)])

    class Limited(Discord):
        limit = False

        def request(self, method, path, payload=None):
            if self.limit:
                raise DiscordError("rate limited", status=429)
            return super().request(method, path, payload)

    clock, discord = [1000.0], Limited()
    svc = service(tmp_path, whop, discord, clock=clock)
    svc.poll()  # 12 grants
    discord.calls.clear()
    clock[0] += 121
    svc.poll()
    assert len(discord.calls) == 5  # one batch per poll
    discord.limit = True
    svc.poll()  # rate limited: no error, resumes later
    discord.limit = False
    svc.poll()
    svc.poll()
    assert sorted(user for _, user in discord.calls) == sorted(ids)


def test_checkout_uses_founding_plan_until_sold_out_and_reuses_link(tmp_path):
    clock = [1000.0]
    whop = Whop(payments=[paid(f"mem_{i}") for i in range(2)])
    svc = service(tmp_path, whop, total=3, clock=clock)
    first = svc.member_view(USER)
    again = svc.member_view(USER)
    assert first["founding"] and first["left"] == 1 and first["url"] == again["url"]
    assert len(whop.created) == 1
    plan, metadata, redirect, key = whop.created[0]
    assert plan == FOUND and metadata["discord_user_id"] == USER and redirect.startswith("https://discord.com/")
    whop.payments.append(paid("mem_x"))  # 3/3 founding used
    clock[0] += 61  # founding count is cached for a minute
    sold = svc.member_view(OTHER)
    assert not sold["founding"] and whop.created[-1][0] == STD
    whop.payments[-1]["refunded_at"] = "2026-11-20T00:00:00Z"  # refund after sellout
    clock[0] += 61
    third = "1700000000000000007"
    assert not svc.member_view(third)["founding"]  # sellout is sticky


def test_returning_member_gets_no_trial_checkout(tmp_path):
    whop = Whop([member("mem_old", status="canceled")])
    svc = service(tmp_path, whop, no_trial={FOUND: "plan_found15nt", STD: "plan_std30nt"})
    view = svc.member_view(USER)
    assert view["trial"] is False and whop.created[-1][0] == "plan_found15nt"
    assert view["founding"]  # the no-trial copy still counts as founding
    text = _text(view)
    assert "free trial before" in text and "$15/month" in text and "3 days" not in text
    assert svc.member_view(USER, promo_code="rrfriendcode1") == {
        "ready": True, "code_error": "returning"}
    assert "new members only" in _text({"ready": True, "code_error": "returning"})


def test_returning_member_without_no_trial_plans_gets_no_second_trial(tmp_path):
    whop = Whop([member("mem_old", status="expired")])
    svc = service(tmp_path, whop)
    assert svc.member_view(USER) == {"ready": True, "trial_used": True}
    assert not whop.created
    assert "already used your free trial" in _text({"ready": True, "trial_used": True})


def test_no_trial_founding_payments_count_toward_founding_spots(tmp_path):
    whop = Whop(payments=[paid("mem_1"), paid("mem_2", plan="plan_found15nt")])
    svc = service(tmp_path, whop, total=2, no_trial={FOUND: "plan_found15nt"})
    assert svc.member_view(USER)["founding"] is False


def test_mistyped_code_costs_no_whop_calls(tmp_path):
    class Counting(Whop):
        calls = 0

        def list_memberships(self, discord_id=None, *, any_plan=False):
            Counting.calls += 1
            return super().list_memberships(discord_id, any_plan=any_plan)

    whop = Counting()
    svc = service(tmp_path, whop)
    assert svc.member_view(USER, promo_code="nosuchcode")["code_error"] == "unknown"
    assert Counting.calls == 0


def test_checkout_reports_existing_pro(tmp_path):
    svc = service(tmp_path, Whop([member("mem_a")]))
    assert svc.member_view(USER) == {"ready": True, "active": True}
    linked = service(tmp_path, Whop([], linked={OTHER: [member("mem_b", discord=None)]}))
    assert linked.member_view(OTHER)["active"] is True


def test_reply_text():
    text = _text({"ready": True, "active": False, "url": "https://whop.com/checkout/p/?s=1",
                  "founding": True, "left": 24})
    assert "24/25" in text and "<https://whop.com/checkout/p/?s=1>" in text
    assert "$30/month" in _text({"ready": True, "url": "https://whop.com/c", "founding": False})
    assert "already have Pro" in _text({"ready": True, "active": True})
    with pytest.raises(ValueError):
        _text({"ready": True, "url": "https://evil.example/"})


class Interaction:
    def __init__(self, custom_id, kind=3, components=None):
        self.guild_id, self.channel_id = int(GUILD), int(WELCOME)
        self.user = SimpleNamespace(id=int(USER), bot=False)
        self.message = SimpleNamespace(author=SimpleNamespace(id=int(BOT)))
        self.type = SimpleNamespace(value=kind)
        self.data = {"custom_id": custom_id}
        if components is not None:
            self.data["components"] = components
        self.done, self.sent, self.modals, self.options = False, [], [], []

        async def defer(**_):
            self.done = True

        async def send(content, **options):
            self.done = True
            self.sent.append(content)
            self.options.append(options)

        async def send_modal(modal):
            self.done = True
            self.modals.append(modal)

        self.response = SimpleNamespace(defer=defer, is_done=lambda: self.done, send_message=send,
                                        send_modal=send_modal)
        self.followup = SimpleNamespace(send=send)


def test_button_routes_to_pro_handler_privately(tmp_path, monkeypatch):
    from discord_bridge import referral_ui

    monkeypatch.setattr(referral_ui, "_sdk", lambda: SimpleNamespace(
        AllowedMentions=SimpleNamespace(none=lambda: None)))
    svc = service(tmp_path, Whop())
    pro = ProCheckoutHandler(STATE, svc)
    other = SimpleNamespace(accepts=lambda i: i.data["custom_id"] == "referral:link:v1")
    composite = CompositeHandler(other, pro)
    try:
        click = Interaction(CUSTOM_ID)
        assert composite.accepts(click)
        asyncio.run(composite.handle(click))
        assert "Try Pro free for 3 days" in click.sent[-1]
        assert not composite.accepts(Interaction("something:else"))
    finally:
        composite.close()


def test_grant_dms_once_and_survives_closed_dms(tmp_path):
    whop, discord = Whop([member("mem_a")]), Discord()
    svc = service(tmp_path, whop, discord, dm="Pro is unlocked")
    svc.poll()
    svc.poll()
    assert discord.dms == [USER] and discord.dm_texts == ["Pro is unlocked"]

    class Closed(Discord):
        def request(self, method, path, payload=None):
            if "@me" in path:
                raise DiscordError("Cannot send messages to this user", status=403)
            return super().request(method, path, payload)

    closed = Closed()
    (tmp_path / "x").mkdir()
    svc2 = service(tmp_path / "x", Whop([member("mem_b", discord=OTHER)]), closed, dm="hi")
    svc2.poll()
    assert closed.calls == [("PUT", OTHER)]


def test_polls_fast_only_while_someone_has_an_open_checkout(tmp_path):
    whop = Whop()
    svc = service(tmp_path, whop)
    assert svc.poll_interval() == 20
    svc.member_view(USER)
    assert svc.poll_interval() == 3
    whop.rows = [member("mem_a")]
    svc.poll()
    assert svc.poll_interval() == 20


def test_only_paid_first_charges_take_founding_spots(tmp_path):
    whop = Whop(payments=[
        paid("mem_trial", amount="0.00"),                           # $0 trial start
        paid("mem_open", status="open"),                            # failed post-trial charge
        paid("mem_refund", refunded_at="2026-11-20T00:00:00Z"),     # refunded
        paid("mem_std", plan=STD),                                  # $30 plan
        paid("mem_ok", amount="10.50"),                             # referral-discounted founder
        paid("mem_ok", amount="15.00", id="pay_renewal"),           # same founder renews
    ])
    view = service(tmp_path, whop, total=3).member_view(USER)
    assert view["founding"] and view["left"] == 2


def _fake_sdk(monkeypatch):
    from discord_bridge import pro_access, referral_ui

    class Widget:
        def __init__(self, **kwargs):
            self.kwargs, self.items, self.stopped = kwargs, [], False

        def add_item(self, item):
            self.items.append(item)

        def stop(self):
            self.stopped = True

    ui = SimpleNamespace(Modal=Widget, TextInput=Widget, View=Widget, Button=Widget)
    sdk = SimpleNamespace(ui=ui, ButtonStyle=SimpleNamespace(primary=1),
                          AllowedMentions=SimpleNamespace(none=lambda: None))
    monkeypatch.setattr(referral_ui, "_sdk", lambda: sdk)
    monkeypatch.setattr(pro_access, "_sdk", lambda: sdk)


def submitted(code, layout="row"):
    field = {"type": 4, "custom_id": "code", "value": code}
    if layout == "label":  # newer Discord modal layout
        return [{"type": 18, "component": field}]
    return [{"type": 1, "components": [field]}]


def test_redeem_link_prefills_the_friend_code_on_the_personal_checkout(tmp_path):
    whop = Whop()
    svc = service(tmp_path, whop)
    view = svc.member_view(USER, promo_code="RRFriendCode1")
    assert view["promo"] and view["url"].endswith("&promoCode=rrfriendcode1")
    assert whop.created[0][1]["discord_user_id"] == USER  # Pro links without Whop Discord connect
    plain = svc.member_view(USER)
    assert not plain["promo"] and "promoCode" not in plain["url"]  # cached link stays clean
    assert len(whop.created) == 1


def test_redeem_rejects_unknown_and_own_codes_without_creating_checkouts(tmp_path):
    whop = Whop()
    svc = service(tmp_path, whop)
    assert svc.member_view(USER, promo_code="nopenope99")["code_error"] == "unknown"
    assert svc.member_view(USER, promo_code="rrmycode0001")["code_error"] == "own"
    assert whop.created == []
    assert "own code" in _text({"ready": True, "code_error": "own"})
    assert "doesn't match" in _text({"ready": True, "code_error": "unknown"})
    active = service(tmp_path, Whop([member("mem_a")]))
    assert active.member_view(USER, promo_code="rrfriendcode1")["active"] is True


def test_redeem_reply_text_shows_discounted_first_month():
    url = "https://whop.com/checkout/ch_1/?session=x&promoCode=abc"
    found = _text({"ready": True, "url": url, "founding": True, "promo": True, "left": 20})
    assert "$10.50" in found and "$15/month" in found and f"<{url}>" in found
    assert "$21" in _text({"ready": True, "url": url, "founding": False, "promo": True})


def test_typed_code_reads_both_modal_layouts():
    assert _typed_code(submitted("abc")) == "abc"
    assert _typed_code(submitted("xyz", layout="label")) == "xyz"
    assert _typed_code([]) is None


def test_redeem_button_opens_form_then_submit_replies_privately(tmp_path, monkeypatch):
    _fake_sdk(monkeypatch)
    svc = service(tmp_path, Whop())
    redeem = RedeemHandler(STATE, svc)
    composite = CompositeHandler(ProCheckoutHandler(STATE, svc), redeem)
    try:
        click = Interaction(REDEEM_ID)
        assert composite.accepts(click)
        asyncio.run(composite.handle(click))
        assert click.modals and click.modals[0].kwargs["custom_id"] == REDEEM_MODAL_ID
        assert click.sent == []

        form = Interaction(REDEEM_MODAL_ID, kind=5, components=submitted(" rrfriend code1 "))
        assert composite.accepts(form)
        asyncio.run(composite.handle(form))
        assert "Code added" in form.sent[-1] and "promoCode=rrfriendcode1" in form.sent[-1]
        assert form.options[-1]["ephemeral"] is True

        bad = Interaction(REDEEM_MODAL_ID, kind=5, components=submitted("no!"))
        asyncio.run(composite.handle(bad))
        assert "doesn't look right" in bad.sent[-1]
        assert not composite.accepts(Interaction(REDEEM_MODAL_ID))  # a button with the form id
    finally:
        composite.close()


def test_account_button_sends_private_menu_without_provider_calls(tmp_path, monkeypatch):
    _fake_sdk(monkeypatch)

    class NoCalls(Whop):
        def list_memberships(self, discord_id=None):
            raise AssertionError("menu must not call Whop")

    handler = AccountHandler(STATE, service(tmp_path, NoCalls()))
    try:
        click = Interaction(ACCOUNT_ID)
        assert handler.accepts(click) and not handler.accepts(Interaction(CUSTOM_ID))
        asyncio.run(handler.handle(click))
        assert "Your account" in click.sent[-1] and click.options[-1]["ephemeral"] is True
        menu = click.options[-1]["view"]
        labels = [item.kwargs["label"] for item in menu.items]
        assert labels == ["Manage subscription", "Payment methods",
                          "Bought on Whop? Link Discord", "Get my referral link"]
        assert menu.items[-1].kwargs["custom_id"] == "referral:link:v1"
        assert menu.stopped  # rendered, never stored in discord.py's view store
    finally:
        handler.close()


def test_checkout_clicks_have_a_per_member_cooldown_and_own_texts(tmp_path, monkeypatch):
    from discord_bridge import referral_ui

    monkeypatch.setattr(referral_ui, "_sdk", lambda: SimpleNamespace(
        AllowedMentions=SimpleNamespace(none=lambda: None)))
    tick = [100.0]
    whop = Whop()
    pro = ProCheckoutHandler(STATE, service(tmp_path, whop), clock=lambda: tick[0])
    try:
        first, second = Interaction(CUSTOM_ID), Interaction(CUSTOM_ID)
        asyncio.run(pro.handle(first))
        asyncio.run(pro.handle(second))
        assert "Try Pro free" in first.sent[-1]
        assert second.sent[-1] == pro.cooldown_text and len(whop.created) == 1
        tick[0] += 11
        third = Interaction(CUSTOM_ID)
        asyncio.run(pro.handle(third))
        assert "Try Pro free" in third.sent[-1]
        assert "Referral" not in pro.unavailable_text and "Referral" not in pro.busy_text
        wrong = Interaction(CUSTOM_ID)
        wrong.channel_id = 1
        asyncio.run(pro.handle(wrong))
        assert wrong.sent[-1] == pro.scope_text
    finally:
        pro.close()


def test_member_of_a_retired_plan_gets_no_second_trial(tmp_path):
    old = member("mem_old20", status="canceled", plan="plan_old20")
    whop = Whop(retired=[old])
    svc = service(tmp_path, whop, no_trial={FOUND: "plan_found15nt", STD: "plan_std30nt"})
    view = svc.member_view(USER)
    assert view["trial"] is False and whop.created[-1][0] == "plan_found15nt"


def test_founding_counts_people_and_ignores_partial_refunds(tmp_path):
    person = {"id": "user_a"}
    payments = [
        paid("mem_1", user=person), paid("mem_2", user=person),  # same founder returned
        paid("mem_3", refunded_amount={"currency": "usd", "amount": "5.00"}),
        paid("mem_4"),
    ]
    from discord_bridge.pro_access import paid_founders
    assert paid_founders(payments, FOUND) == 2


def test_sellout_seen_by_the_counter_closes_founding_for_the_bot(tmp_path):
    flag = tmp_path / "founding-sold-out"
    whop = Whop(payments=[paid("mem_1")])
    svc = ProAccessService(
        whop, Discord(), ProAccessStore(tmp_path / "pro.sqlite3"),
        guild_id=GUILD, pro_role=ROLE, founding_plan=FOUND, standard_plan=STD,
        founding_total=25, redirect_url="https://discord.com/channels/x/y",
        sold_out_file=flag, clock=lambda: 1000.0,
    )
    assert svc.member_view(USER)["founding"] is True
    flag.write_text("1", encoding="utf-8")
    assert svc.member_view(OTHER)["founding"] is False


def test_one_members_role_error_does_not_block_other_grants(tmp_path):
    other = "1700000000000000007"

    class Flaky(Discord):
        def request(self, method, path, payload=None):
            if USER in path and method == "PUT":
                raise DiscordError("Missing Access", status=403)
            return super().request(method, path, payload)

    discord = Flaky()
    svc = service(tmp_path, Whop([member("mem_a"), member("mem_b", discord=other)]), discord)
    svc.poll()
    assert ("PUT", other) in discord.calls and svc.store.granted() == {other: "mem_b"}


def test_cooldown_is_shared_across_buttons(tmp_path, monkeypatch):
    from discord_bridge import referral_ui

    monkeypatch.setattr(referral_ui, "_sdk", lambda: SimpleNamespace(
        AllowedMentions=SimpleNamespace(none=lambda: None)))
    clicks, tick = {}, [100.0]
    svc = service(tmp_path, Whop())
    pro = ProCheckoutHandler(STATE, svc, clicks=clicks, clock=lambda: tick[0])
    redeem = RedeemHandler(STATE, svc, clicks=clicks, clock=lambda: tick[0])
    try:
        asyncio.run(pro.handle(Interaction(CUSTOM_ID)))
        submit = Interaction(REDEEM_MODAL_ID, kind=5, components=[
            {"type": 1, "components": [{"custom_id": "code", "value": "rrfriendcode1"}]}])
        asyncio.run(redeem.handle(submit))
        assert submit.sent[-1] == redeem.cooldown_text
    finally:
        pro.close()
        redeem.close()


def test_poll_reports_who_is_only_on_a_free_trial(tmp_path):
    whop = Whop([member("mem_a", status="trialing"), member("mem_b", discord=OTHER),
                 member("mem_c", status="trialing", discord=OTHER)])
    svc = service(tmp_path, whop)
    assert svc.trial_members is None  # unknown until the first poll
    svc.poll()
    assert svc.trial_members == {USER}  # OTHER also pays on mem_b
    whop.rows = [member("mem_a"), member("mem_b", discord=OTHER)]  # trial converted
    svc.poll()
    assert svc.trial_members == frozenset()
