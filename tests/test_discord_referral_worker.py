"""Referral billing uses provider evidence and a real durable reward ledger."""

from copy import deepcopy
from decimal import Decimal

import pytest
from discord_bridge.referral_store import ReferralStore
from discord_bridge.referral_worker import ReferralService
from discord_bridge.whop_client import WhopError

GUILD = "1700000000000000001"
REFERRER = "1700000000000010001"
FRIEND = "1700000000000010002"
COMPANY = "biz_referraltests"
PRODUCT = "prod_referraltests"
PLAN = "plan_referraltests"
OWNER_USER = "user_referrertests"
FRIEND_USER = "user_friendtests"
MEMBERSHIP = "mem_referrertests"
FRIEND_MEMBERSHIP = "mem_friendtests"


def money(amount):
    return {"amount": str(amount), "currency": "usd", "decimals": 2, "display_decimals": 2}


def membership(**changes):
    return {
        "id": MEMBERSHIP,
        "account": {"id": COMPANY},
        "product_id": PRODUCT,
        "variant_id": PLAN,
        "user_id": OWNER_USER,
        "status": "active",
        "cancel_at_period_end": False,
        "promo_code_id": None,
        "current_period_end": "2026-12-01T00:00:00Z",
        "renewal_price": money("30.00"),
        **changes,
    }


def payment(promo_id, **changes):
    return {
        "id": "pay_friendtests",
        "account_id": COMPANY,
        "product_id": PRODUCT,
        "variant_id": PLAN,
        "membership_id": FRIEND_MEMBERSHIP,
        "user": {"id": FRIEND_USER},
        "promo_code_id": promo_id,
        "status": "paid",
        "billing_reason": "subscription_create",
        "paid_at": "2026-10-30T12:00:00Z",
        "total": money("21.00"),
        "refunded_amount": money("0.00"),
        "refunded_at": None,
        "dispute_created_at": None,
        **changes,
    }


class Provider:
    credential_fingerprint = "a" * 64

    def __init__(self):
        self.promos = []
        self.members = {REFERRER: [membership()]}
        self.payments = []
        self.applied = []
        self.create_calls = []
        self.apply_error = None
        self.accept_before_error = False

    def list_promos(self):
        return deepcopy(self.promos)

    def create_promo(self, code, *, existing_memberships_only=False, idempotency_key):
        self.create_calls.append((code, existing_memberships_only, idempotency_key))
        row = {
            "id": f"promo_test{len(self.promos) + 1}",
            "account": {"id": COMPANY},
            "code": code.lower(),
            "amount_off": 0.30,
            "promo_type": "percentage",
            "duration": "once",
            "promo_duration_months": 1,
            "new_users_only": not existing_memberships_only,
            "existing_memberships_only": existing_memberships_only,
            "affiliate": None,
            "status": "active",
        }
        self.promos.append(row)
        return deepcopy(row)

    def list_memberships(self, discord_id=None):
        if discord_id is not None:
            return deepcopy(self.members.get(discord_id, []))
        return deepcopy([row for values in self.members.values() for row in values])

    def retrieve_membership(self, membership_id):
        return next(
            row for rows in self.members.values() for row in rows if row["id"] == membership_id
        )

    def retrieve_promo(self, promo_id):
        return deepcopy(next(row for row in self.promos if row["id"] == promo_id))

    def list_payments(self, created_after=None):
        return deepcopy(self.payments)

    def apply_promo(self, membership_id, code, *, idempotency_key):
        self.applied.append((membership_id, code, idempotency_key))
        row = self.retrieve_membership(membership_id)
        promo = next(item for item in self.promos if item["code"].lower() == code.lower())
        if self.apply_error is None or self.accept_before_error:
            row["promo_code_id"] = promo["id"]
            row["renewal_price"] = money(
                f"{Decimal(row['renewal_price']['amount']) * Decimal('0.70'):.2f}"
            )
        if self.apply_error:
            raise self.apply_error
        return deepcopy(row)


@pytest.fixture
def setup(tmp_path):
    store = ReferralStore(tmp_path / "referrals.sqlite3", guild_id=GUILD, company_id=COMPANY)
    provider = Provider()
    service = ReferralService(
        provider,
        store,
        company_id=COMPANY,
        product_ids={PRODUCT},
        plan_prices={PLAN: 3000},
        checkout_plan_id=PLAN,
    )
    return service, store, provider


def referred_purchase(setup):
    service, store, provider = setup
    view = service.member_view(REFERRER)
    promo = store.profile(REFERRER)["friend_promo_id"]
    provider.payments = [payment(promo)]
    return view


def test_member_gets_personal_preapplied_checkout_and_provider_linked_identity(setup):
    service, store, provider = setup
    view = service.member_view(REFERRER)
    assert view["ready"] is True
    assert "promoCode=" in view["url"] and f"/checkout/{PLAN}" in view["url"]
    assert view["code"] in view["url"]
    assert view["normal_price_cents"] == 3000
    assert store.profile(REFERRER)["whop_user_id"] == OWNER_USER
    assert service.member_view(REFERRER)["code"] == view["code"]
    assert len(provider.create_calls) == 1


def test_paid_referral_applies_one_reward_then_returns_to_normal_after_use(setup):
    service, store, provider = setup
    referred_purchase(setup)
    service.poll()
    reward = store.rewards(REFERRER)[0]
    assert reward["state"] == "applied"
    assert len(provider.applied) == 1
    service.poll()
    assert len(provider.applied) == 1
    view = service.member_view(REFERRER)
    assert view["applied"] == 1 and view["next_payment_cents"] == 2100
    provider.payments.append(
        payment(
            reward["promo_id"],
            id="pay_rewardused",
            membership_id=MEMBERSHIP,
            user={"id": OWNER_USER},
            billing_reason="subscription_cycle",
        )
    )
    provider.members[REFERRER][0]["promo_code_id"] = None
    service.poll()
    assert store.rewards(REFERRER)[0]["state"] == "used"
    assert service.member_view(REFERRER)["next_payment_cents"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "open"},
        {"billing_reason": "subscription_cycle"},
        {"billing_reason": "subscription_update"},
        {"paid_at": None},
        {"account_id": "biz_someoneelse"},
        {"product_id": "prod_someoneelse"},
        {"variant_id": "plan_retired"},
        {"total": money("0.00")},
        {"refunded_amount": money("21.00")},
        {"dispute_created_at": "2026-10-30T13:00:00Z"},
    ],
)
def test_unqualified_events_do_not_manufacture_rewards(setup, changes):
    service, store, provider = setup
    referred_purchase(setup)
    provider.payments[0].update(changes)
    service.poll()
    assert store.rewards(REFERRER) == []
    assert provider.applied == []


def test_manual_tester_can_share_and_keep_reward_until_a_paid_membership_is_linked(setup):
    service, store, provider = setup
    provider.members = {}
    referred_purchase(setup)
    service.poll()
    assert store.rewards(REFERRER)[0]["state"] == "pending"
    assert service.member_view(REFERRER)["membership_state"] == "none"
    provider.members[REFERRER] = [membership()]
    service.poll()
    assert store.rewards(REFERRER)[0]["state"] == "applied"


def test_existing_promo_is_preserved_and_later_rewards_queue(setup):
    service, store, provider = setup
    referred_purchase(setup)
    provider.members[REFERRER][0]["promo_code_id"] = "promo_existingoffer"
    provider.payments.append(
        payment(
            store.profile(REFERRER)["friend_promo_id"],
            id="pay_secondfriend",
            user={"id": "user_anotherfriend"},
            membership_id="mem_anotherfriend",
        )
    )
    service.poll()
    assert len(store.rewards(REFERRER)) == 2
    assert provider.applied == []
    assert service.member_view(REFERRER)["membership_state"] == "conflict"
    provider.members[REFERRER][0]["promo_code_id"] = None
    service.poll()
    assert len(provider.applied) == 1
    assert sorted(row["state"] for row in store.rewards(REFERRER)) == ["applied", "pending"]


def test_unknown_write_reconciles_accepted_promo_without_reapplying(setup):
    service, store, provider = setup
    referred_purchase(setup)
    provider.apply_error = WhopError("network", ambiguous=True)
    provider.accept_before_error = True
    service.poll()
    assert store.rewards(REFERRER)[0]["state"] in {"applying", "applied"}
    provider.apply_error = None
    service.poll()
    assert store.rewards(REFERRER)[0]["state"] == "applied"
    assert len(provider.applied) == 1


def test_future_tier_uses_percentage_of_its_own_price(setup):
    service, store, provider = setup
    service.plan_prices[PLAN] = 9900
    provider.members[REFERRER][0]["renewal_price"] = money("99.00")
    referred_purchase(setup)
    service.poll()
    assert service.member_view(REFERRER)["next_payment_cents"] == 6930


def test_canceled_or_unverified_membership_does_not_get_a_discount(setup):
    service, store, provider = setup
    referred_purchase(setup)
    provider.members[REFERRER][0]["cancel_at_period_end"] = True
    service.poll()
    assert provider.applied == []
    assert store.rewards(REFERRER)[0]["state"] == "pending"


def test_self_referral_is_rejected_after_verified_binding(setup):
    service, store, provider = setup
    referred_purchase(setup)
    provider.payments[0]["user"] = {"id": OWNER_USER}
    service.poll()
    assert provider.applied == []
    assert all(row["state"] not in {"applied", "used"} for row in store.rewards(REFERRER))


def test_direct_purchase_without_a_promo_does_not_block_a_later_referral(setup):
    service, store, provider = setup
    referred_purchase(setup)
    provider.payments.insert(0, payment(None, id="pay_directpurchase"))
    service.poll()
    assert store.summary(REFERRER)["applied"] == 1


def test_no_refund_amount_is_legitimate_paid_purchase_evidence(setup):
    service, store, provider = setup
    referred_purchase(setup)
    provider.payments[0]["refunded_amount"] = None
    service.poll()
    assert store.summary(REFERRER)["applied"] == 1


def test_restart_before_apply_submission_reprepares_instead_of_stranding_reward(setup):
    service, store, provider = setup
    referred_purchase(setup)
    store.bind_whop_user(REFERRER, OWNER_USER)
    reward = store.credit_referral(
        referrer_discord_id=REFERRER,
        referee_whop_user_id=FRIEND_USER,
        payment_id="pay_friendtests",
        membership_id=FRIEND_MEMBERSHIP,
    )
    store.claim_reward(
        REFERRER, MEMBERSHIP, now=1, provider_fingerprint=provider.credential_fingerprint
    )
    service.poll()
    assert store.get_reward(reward["reward_id"])["state"] == "applied"


def test_unknown_unaccepted_apply_can_retry_same_intent_inside_documented_window(setup):
    service, store, provider = setup
    referred_purchase(setup)
    provider.apply_error = WhopError("network", ambiguous=True)
    service.poll()
    first = provider.applied[0]
    provider.apply_error = None
    service.poll()
    assert provider.applied == [first, first]
    assert store.summary(REFERRER)["applied"] == 1


def test_changed_api_credential_never_replays_an_ambiguous_application(setup):
    service, store, provider = setup
    referred_purchase(setup)
    provider.apply_error = WhopError("network", ambiguous=True)
    service.poll()
    provider.credential_fingerprint = "b" * 64
    provider.apply_error = None
    service.poll()
    assert len(provider.applied) == 1
    assert store.summary(REFERRER)["used"] == 0


def test_refunded_pending_purchase_does_not_later_award_a_discount(setup):
    service, store, provider = setup
    provider.members = {}
    referred_purchase(setup)
    service.poll()
    provider.payments[0]["refunded_amount"] = money("21.00")
    provider.payments[0]["refunded_at"] = "2026-10-31T00:00:00Z"
    provider.members[REFERRER] = [membership()]
    service.poll()
    assert provider.applied == []
    assert store.summary(REFERRER)["void"] == 1


def test_applied_old_membership_is_not_claimed_as_discount_on_a_new_membership(setup):
    service, store, provider = setup
    referred_purchase(setup)
    service.poll()
    old = provider.members[REFERRER][0]
    old["status"] = "canceled"
    provider.members[REFERRER].append(membership(id="mem_newsubscription"))
    view = service.member_view(REFERRER)
    assert view["applied"] == 0 and view["next_payment_cents"] is None
    assert view["queued"] == 1
    service.poll()
    assert len(provider.applied) == 1


def test_future_plan_change_rotates_code_but_preserves_old_referral_attribution(setup):
    service, store, provider = setup
    first = service.member_view(REFERRER)
    old_promo = store.profile(REFERRER)["friend_promo_id"]
    service.plan_prices["plan_futuretier"] = 9900
    second = service.member_view(REFERRER)
    assert second["code"] != first["code"]
    assert store.profile_for_promo(old_promo)["discord_id"] == REFERRER


def test_next_payment_quote_uses_provider_invoice_price_not_catalog_projection(setup):
    service, store, provider = setup
    service.plan_prices[PLAN] = 9900
    referred_purchase(setup)
    service.poll()
    provider.members[REFERRER][0]["renewal_price"] = money("30.00")
    assert service.member_view(REFERRER)["next_payment_cents"] == 3000


def test_consumed_reward_promo_can_be_inactive_without_losing_invoice_confirmation(setup):
    service, store, provider = setup
    referred_purchase(setup)
    service.poll()
    reward = store.rewards(REFERRER)[0]
    next(row for row in provider.promos if row["id"] == reward["promo_id"])["status"] = "inactive"
    provider.payments.append(
        payment(
            reward["promo_id"],
            id="pay_usedinactive",
            membership_id=MEMBERSHIP,
            user={"id": OWNER_USER},
            billing_reason="subscription_cycle",
        )
    )
    service.poll()
    assert store.summary(REFERRER)["used"] == 1
