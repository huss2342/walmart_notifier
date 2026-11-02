"""Durable referral identities and once-only discount reward intents, no provider IO."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from discord_bridge.referral_store import ReferralStore

GUILD = "1562530389866717295"
OTHER_GUILD = "1562530389866717296"
MEMBER = "1562530389866717300"
OTHER = "1562530389866717301"
THIRD = "1562530389866717302"
COMPANY = "biz_testcompany"


def make(tmp_path):
    return ReferralStore(tmp_path / "referrals.sqlite3", guild_id=GUILD, company_id=COMPANY)


def profile(store, discord_id=MEMBER, whop_user="user_referrer"):
    store.ensure_profile(discord_id, now=100)
    return store.bind_whop_user(discord_id, whop_user, now=101)


def credit(store, suffix="one", *, member=MEMBER, now=110):
    return store.credit_referral(
        referrer_discord_id=member,
        referee_whop_user_id="user_friend" + suffix,
        payment_id="pay_first" + suffix,
        membership_id="mem_friend" + suffix,
        now=now,
    )


def test_profiles_have_stable_private_codes_and_survive_restart(tmp_path):
    store = make(tmp_path)
    first = store.ensure_profile(MEMBER, now=100)
    again = store.ensure_profile(MEMBER, now=101)
    restarted = make(tmp_path)
    assert first["share_code"] == again["share_code"] == restarted.profile(MEMBER)["share_code"]
    assert MEMBER not in first["share_code"]
    assert first["whop_user_id"] is None
    assert len(store.profiles()) == 1
    assert store.profile(OTHER) is None
    assert store.ensure_profile(OTHER)["share_code"] != first["share_code"]


@pytest.mark.parametrize("guild,company", [(OTHER_GUILD, COMPANY), (GUILD, "biz_anothercompany")])
def test_database_binding_rejects_other_guild_or_company(tmp_path, guild, company):
    store = make(tmp_path)
    store.ensure_profile(MEMBER)
    with pytest.raises(ValueError, match="binding"):
        ReferralStore(store.path, guild_id=guild, company_id=company)
    assert make(tmp_path).profile(MEMBER)


def test_an_unrelated_existing_database_is_not_adopted(tmp_path):
    path = tmp_path / "referrals.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE unrelated (value TEXT)")
        db.execute("INSERT INTO unrelated VALUES ('preserve me')")
    with pytest.raises(ValueError, match="binding"):
        make(tmp_path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM unrelated").fetchone()[0] == "preserve me"
        assert (
            db.execute("SELECT name FROM sqlite_master WHERE name='referral_profiles'").fetchone()
            is None
        )


def test_verified_whop_binding_is_one_to_one_and_immutable(tmp_path):
    store = make(tmp_path)
    bound = profile(store)
    assert store.bind_whop_user(MEMBER, "user_referrer")["whop_user_id"] == bound["whop_user_id"]
    with pytest.raises(ValueError, match="bound"):
        store.bind_whop_user(MEMBER, "user_different")
    store.ensure_profile(OTHER)
    with pytest.raises(ValueError, match="bound"):
        store.bind_whop_user(OTHER, "user_referrer")
    assert store.profile(OTHER)["whop_user_id"] is None


def test_friend_promo_mapping_is_unique_and_preserves_earlier_codes(tmp_path):
    store = make(tmp_path)
    store.ensure_profile(MEMBER)
    store.set_friend_promo(MEMBER, promo_id="promo_friend", promo_code="FRIEND30")
    store.set_friend_promo(MEMBER, promo_id="promo_later", promo_code="FRIEND30NEW")
    assert store.profile_for_promo("promo_friend")["discord_id"] == MEMBER
    assert store.profile_for_promo("promo_later")["discord_id"] == MEMBER
    assert store.profile(MEMBER)["friend_promo_id"] == "promo_later"
    assert store.profile_for_promo("promo_unknown") is None
    store.ensure_profile(OTHER)
    with pytest.raises(ValueError, match="promo"):
        store.set_friend_promo(OTHER, promo_id="promo_friend", promo_code="FRIEND30")
    with pytest.raises(ValueError, match="promo"):
        store.set_friend_promo(OTHER, promo_id="promo_other", promo_code="FRIEND30")


def test_renewals_and_duplicate_events_do_not_earn_additional_rewards(tmp_path):
    store = make(tmp_path)
    profile(store)
    first = credit(store)
    assert credit(store)["reward_id"] == first["reward_id"]
    renewal = store.credit_referral(
        referrer_discord_id=MEMBER,
        referee_whop_user_id="user_friendone",
        payment_id="pay_renewal",
        membership_id="mem_upgraded",
    )
    assert renewal["reward_id"] == first["reward_id"]
    assert store.summary(MEMBER)["pending"] == 1
    assert make(tmp_path).rewards(MEMBER)[0]["reward_code"] == first["reward_code"]
    assert first["qualifying_payment_id"] == "pay_firstone"


def test_reward_codes_are_random_but_the_persisted_intent_is_stable(tmp_path):
    first_store, second_store = make(tmp_path), make(tmp_path / "independent")
    profile(first_store)
    profile(second_store)
    first, other_database = credit(first_store), credit(second_store)
    assert first["reward_id"] == other_database["reward_id"]
    assert first["reward_code"] != other_database["reward_code"]
    assert first["reward_code"] == credit(first_store)["reward_code"]
    assert first["reward_code"] == make(tmp_path).get_reward(first["reward_id"])["reward_code"]


def test_payment_or_referee_cannot_be_reassigned_to_another_referrer(tmp_path):
    store = make(tmp_path)
    profile(store)
    profile(store, OTHER, "user_otherreferrer")
    credit(store)
    with pytest.raises(ValueError, match="attribution"):
        credit(store, member=OTHER)
    with pytest.raises(ValueError, match="attribution"):
        store.credit_referral(
            referrer_discord_id=OTHER,
            referee_whop_user_id="user_differentfriend",
            payment_id="pay_firstone",
            membership_id="mem_differentfriend",
        )
    assert store.summary(OTHER)["total"] == 0


def test_verified_self_referrals_are_rejected_by_whop_or_discord_identity(tmp_path):
    store = make(tmp_path)
    profile(store)
    with pytest.raises(ValueError, match="self"):
        store.credit_referral(
            referrer_discord_id=MEMBER,
            referee_whop_user_id="user_referrer",
            payment_id="pay_self",
            membership_id="mem_self",
        )
    with pytest.raises(ValueError, match="self"):
        store.credit_referral(
            referrer_discord_id=MEMBER,
            referee_whop_user_id="user_unbound",
            referee_discord_id=MEMBER,
            payment_id="pay_selftwo",
            membership_id="mem_selftwo",
        )
    assert store.rewards(MEMBER) == []


def test_manual_tester_rewards_wait_for_binding_and_late_self_credit_is_held(tmp_path):
    store = make(tmp_path)
    store.ensure_profile(MEMBER)
    reward = credit(store)
    assert store.claim_reward(MEMBER, "mem_paidreferrer", now=120) is None
    store.bind_whop_user(MEMBER, "user_friendone", now=121)
    assert store.get_reward(reward["reward_id"])["state"] == "exception"
    assert store.get_reward(reward["reward_id"])["reason"] == "self_referral"
    assert store.claim_reward(MEMBER, "mem_paidreferrer", now=122) is None


def test_concurrent_identity_creation_and_credit_are_once_only(tmp_path):
    store = make(tmp_path)
    with ThreadPoolExecutor(max_workers=6) as pool:
        profiles = list(pool.map(lambda _: store.ensure_profile(MEMBER), range(12)))
    assert len({row["share_code"] for row in profiles}) == 1
    store.bind_whop_user(MEMBER, "user_referrer")
    with ThreadPoolExecutor(max_workers=6) as pool:
        rewards = list(pool.map(lambda _: credit(store), range(12)))
    assert len({row["reward_id"] for row in rewards}) == 1
    assert store.summary(MEMBER)["total"] == 1


def test_lease_claim_is_atomic_member_scoped_and_blocks_a_second_membership(tmp_path):
    store = make(tmp_path)
    profile(store)
    credit(store, "one", now=110)
    credit(store, "two", now=111)
    with ThreadPoolExecutor(max_workers=6) as pool:
        claims = list(
            pool.map(lambda _: store.claim_reward(MEMBER, "mem_referrer", now=120), range(8))
        )
    claimed = [row for row in claims if row]
    assert len(claimed) == 1
    claim = claimed[0]
    assert claim["membership_id"] == "mem_referrer"
    assert claim["referrer_discord_id"] == MEMBER
    assert claim["lease_token"]
    assert claim["intent_key"]
    assert store.claim_reward(MEMBER, "mem_othermembership", now=121) is None
    assert store.summary(MEMBER)["pending"] == 1


def test_one_membership_cannot_receive_rewards_for_two_members(tmp_path):
    store = make(tmp_path)
    profile(store)
    profile(store, OTHER, "user_otherreferrer")
    credit(store)
    credit(store, "two", member=OTHER)
    assert store.claim_reward(MEMBER, "mem_shared", now=120)
    assert store.claim_reward(OTHER, "mem_shared", now=121) is None
    assert store.summary(OTHER)["pending"] == 1


def test_expired_applying_intent_is_never_blindly_resubmitted_after_restart(tmp_path):
    store = make(tmp_path)
    profile(store)
    reward = credit(store)
    claim = store.claim_reward(
        MEMBER, "mem_referrer", now=120, lease_seconds=10, provider_fingerprint="a" * 64
    )
    restarted = make(tmp_path)
    assert (
        restarted.claim_reward(MEMBER, "mem_referrer", now=1000, provider_fingerprint="b" * 64)
        is None
    )
    rows = restarted.reconciliation_rewards(now=1000)
    assert [row["reward_id"] for row in rows] == [reward["reward_id"]]
    assert rows[0]["lease_token"] == claim["lease_token"]
    assert rows[0]["intent_key"] == claim["intent_key"]
    assert rows[0]["application_generation"] == 1
    assert rows[0]["provider_fingerprint"] == "a" * 64
    restarted.reconcile_application(reward["reward_id"], outcome="applied", promo_id="promo_reward")
    assert restarted.get_reward(reward["reward_id"])["state"] == "applied"
    assert restarted.reward_for_promo("promo_reward")["reward_id"] == reward["reward_id"]


def test_proven_non_submission_can_release_but_wrong_lease_cannot(tmp_path):
    store = make(tmp_path)
    profile(store)
    reward = credit(store)
    claim = store.claim_reward(MEMBER, "mem_referrer", now=120)
    with pytest.raises(ValueError, match="lease"):
        store.release_reward(reward["reward_id"], "wrong", reason="provider_rejected")
    store.release_reward(reward["reward_id"], claim["lease_token"], reason="provider_rejected")
    assert store.summary(MEMBER)["pending"] == 1
    retry = store.claim_reward(MEMBER, "mem_referrer", now=200)
    assert retry["reward_code"] == claim["reward_code"]
    assert retry["intent_key"] != claim["intent_key"]
    assert claim["application_generation"] == 1
    assert retry["application_generation"] == 2
    assert retry["provider_fingerprint"] is None
    assert retry["lease_token"] != claim["lease_token"]
    with pytest.raises(ValueError, match="lease"):
        store.mark_applied(reward["reward_id"], claim["lease_token"], promo_id="promo_oldattempt")


def test_next_reward_stays_queued_until_successful_invoice_confirmation(tmp_path):
    store = make(tmp_path)
    profile(store)
    first, second = credit(store, "one", now=110), credit(store, "two", now=111)
    claim = store.claim_reward(MEMBER, "mem_referrer", now=120)
    store.mark_applied(first["reward_id"], claim["lease_token"], promo_id="promo_rewardone")
    assert store.summary(MEMBER)["applied"] == 1
    assert store.claim_reward(MEMBER, "mem_referrer", now=300) is None
    store.mark_used(
        first["reward_id"], payment_id="pay_discountedone", membership_id="mem_referrer"
    )
    store.mark_used(
        first["reward_id"], payment_id="pay_discountedone", membership_id="mem_referrer"
    )
    assert store.summary(MEMBER)["used"] == 1
    assert store.claim_reward(MEMBER, "mem_referrer", now=301)["reward_id"] == second["reward_id"]


def test_used_invoice_requires_applied_reward_and_matching_membership_and_unique_payment(tmp_path):
    store = make(tmp_path)
    profile(store)
    reward = credit(store)
    with pytest.raises(ValueError, match="applied"):
        store.mark_used(reward["reward_id"], payment_id="pay_bill", membership_id="mem_referrer")
    claim = store.claim_reward(MEMBER, "mem_referrer", now=120)
    store.mark_applied(reward["reward_id"], claim["lease_token"], promo_id="promo_rewardone")
    with pytest.raises(ValueError, match="membership"):
        store.mark_used(reward["reward_id"], payment_id="pay_bill", membership_id="mem_wrong")
    store.mark_used(reward["reward_id"], payment_id="pay_bill", membership_id="mem_referrer")
    second = credit(store, "two")
    claim2 = store.claim_reward(MEMBER, "mem_referrer", now=200)
    store.mark_applied(second["reward_id"], claim2["lease_token"], promo_id="promo_rewardtwo")
    with pytest.raises(ValueError, match="payment"):
        store.mark_used(second["reward_id"], payment_id="pay_bill", membership_id="mem_referrer")
    assert store.get_reward(second["reward_id"])["state"] == "applied"


def test_a_new_definite_application_attempt_records_its_credential_fingerprint(tmp_path):
    store = make(tmp_path)
    profile(store)
    reward = credit(store)
    first = store.claim_reward(MEMBER, "mem_referrer", now=120, provider_fingerprint="a" * 64)
    store.release_reward(reward["reward_id"], first["lease_token"], reason="provider_rejected")
    second = store.claim_reward(MEMBER, "mem_referrer", now=121, provider_fingerprint="b" * 64)
    assert second["application_generation"] == 2
    assert second["provider_fingerprint"] == "b" * 64
    assert second["intent_key"] != first["intent_key"]
    assert make(tmp_path).get_reward(reward["reward_id"])["intent_key"] == second["intent_key"]


def test_existing_ledger_gains_application_columns_without_losing_queued_rewards(tmp_path):
    store = make(tmp_path)
    profile(store)
    reward = credit(store)
    with sqlite3.connect(store.path) as db:
        db.execute("ALTER TABLE referral_rewards DROP COLUMN application_generation")
        db.execute("ALTER TABLE referral_rewards DROP COLUMN provider_fingerprint")
        db.execute("ALTER TABLE referral_rewards DROP COLUMN apply_submitted_at")
    restarted = make(tmp_path)
    migrated = restarted.get_reward(reward["reward_id"])
    assert migrated["state"] == "pending"
    assert migrated["application_generation"] == 0
    assert migrated["provider_fingerprint"] is None
    assert migrated["apply_submitted_at"] is None
    claimed = restarted.claim_reward(MEMBER, "mem_referrer", now=120)
    assert claimed["reward_id"] == reward["reward_id"]
    assert claimed["application_generation"] == 1


def test_submission_boundary_persists_and_original_timestamp_is_not_extended(tmp_path):
    store = make(tmp_path)
    profile(store)
    reward = credit(store)
    claim = store.claim_reward(MEMBER, "mem_referrer", now=120)
    assert claim["apply_submitted_at"] is None
    first = store.mark_submission(reward["reward_id"], claim["lease_token"], now=121)
    assert first["apply_submitted_at"] == 121
    repeated = store.mark_submission(reward["reward_id"], claim["lease_token"], now=999)
    assert repeated["apply_submitted_at"] == 121
    restarted = make(tmp_path)
    assert restarted.get_reward(reward["reward_id"])["apply_submitted_at"] == 121
    assert restarted.claim_reward(MEMBER, "mem_referrer", now=1000) is None
    restarted.release_reward(
        reward["reward_id"], claim["lease_token"], reason="provider_rejected", now=1001
    )
    pending = restarted.get_reward(reward["reward_id"])
    assert pending["apply_submitted_at"] is None
    assert pending["state"] == "pending"


def test_prepare_crash_release_is_atomic_against_a_late_submission(tmp_path):
    store = make(tmp_path)
    profile(store)
    reward = credit(store)
    claim = store.claim_reward(MEMBER, "mem_referrer", now=120)
    # Recovery observed the unsubmitted phase before the other worker advanced.
    assert make(tmp_path).get_reward(reward["reward_id"])["apply_submitted_at"] is None
    store.mark_submission(reward["reward_id"], claim["lease_token"], now=121)
    with pytest.raises(ValueError, match="submitted"):
        store.release_reward(
            reward["reward_id"],
            claim["lease_token"],
            reason="prepare_interrupted",
            require_unsubmitted=True,
        )
    assert store.get_reward(reward["reward_id"])["state"] == "applying"


def test_unsubmitted_prepare_can_restart_but_the_old_worker_cannot_cross_boundary(tmp_path):
    store = make(tmp_path)
    profile(store)
    reward = credit(store)
    claim = store.claim_reward(MEMBER, "mem_referrer", now=120)
    restarted = make(tmp_path)
    restarted.release_reward(
        reward["reward_id"],
        claim["lease_token"],
        reason="prepare_interrupted",
        require_unsubmitted=True,
        now=500,
    )
    next_attempt = restarted.claim_reward(MEMBER, "mem_referrer", now=501)
    assert next_attempt["apply_submitted_at"] is None
    assert next_attempt["application_generation"] == 2
    with pytest.raises(ValueError, match="lease"):
        store.mark_submission(reward["reward_id"], claim["lease_token"], now=502)


def test_legacy_applying_migration_never_asserts_definite_non_submission(tmp_path):
    store = make(tmp_path)
    profile(store)
    reward = credit(store)
    claim = store.claim_reward(MEMBER, "mem_referrer", now=120)
    with sqlite3.connect(store.path) as db:
        db.execute("ALTER TABLE referral_rewards DROP COLUMN application_generation")
        db.execute("ALTER TABLE referral_rewards DROP COLUMN provider_fingerprint")
        db.execute("ALTER TABLE referral_rewards DROP COLUMN apply_submitted_at")
    migrated = make(tmp_path)
    row = migrated.get_reward(reward["reward_id"])
    assert row["apply_submitted_at"] is not None
    assert row["provider_fingerprint"] is None
    assert row["intent_key"] == claim["intent_key"]
    with pytest.raises(ValueError, match="submitted"):
        migrated.release_reward(
            reward["reward_id"],
            claim["lease_token"],
            reason="prepare_interrupted",
            require_unsubmitted=True,
        )


@pytest.mark.parametrize("fingerprint", ["raw-token", "f" * 63, "G" * 64, 12, True])
def test_credential_fingerprint_must_be_a_sha256_digest(tmp_path, fingerprint):
    store = make(tmp_path)
    profile(store)
    credit(store)
    with pytest.raises(ValueError, match="fingerprint"):
        store.claim_reward(MEMBER, "mem_referrer", provider_fingerprint=fingerprint)
    assert store.summary(MEMBER)["pending"] == 1


def test_unknown_application_retains_reward_and_blocks_other_rewards_until_reconciled(tmp_path):
    store = make(tmp_path)
    profile(store)
    reward = credit(store)
    credit(store, "two")
    claim = store.claim_reward(MEMBER, "mem_referrer", now=120)
    store.flag_exception(reward["reward_id"], "ambiguous_provider_response")
    assert store.claim_reward(MEMBER, "mem_referrer", now=1000) is None
    store.reconcile_application(reward["reward_id"], outcome="unknown")
    assert store.get_reward(reward["reward_id"])["state"] == "exception"
    store.reconcile_application(reward["reward_id"], outcome="not_applied")
    retried = store.claim_reward(MEMBER, "mem_referrer", now=1001)
    assert retried["reward_id"] == claim["reward_id"]
    assert store.summary(MEMBER)["pending"] == 1


def test_pending_void_and_exception_are_durable_without_creating_debt(tmp_path):
    store = make(tmp_path)
    profile(store)
    reward = credit(store)
    store.void_reward(reward["reward_id"], reason="refunded_purchase")
    assert store.summary(MEMBER)["void"] == 1
    assert store.claim_reward(MEMBER, "mem_referrer") is None
    assert credit(store)["state"] == "void"


def test_self_referral_exception_cannot_be_reconciled_back_to_payable(tmp_path):
    store = make(tmp_path)
    store.ensure_profile(MEMBER)
    reward = credit(store)
    store.bind_whop_user(MEMBER, "user_friendone")
    with pytest.raises(ValueError, match="self"):
        store.reconcile_application(reward["reward_id"], outcome="not_applied")
    assert store.get_reward(reward["reward_id"])["state"] == "exception"


@pytest.mark.parametrize(
    "user", [None, 12, True, "short", "123' OR 1=1 --", "1" * 1000, "0" * 17, "9" * 20]
)
def test_hostile_or_invalid_member_values_are_rejected(tmp_path, user):
    store = make(tmp_path)
    with pytest.raises(ValueError):
        store.ensure_profile(user)
    assert store.profiles() == []


@pytest.mark.parametrize("time", [-1, True, float("nan"), float("inf"), "100"])
def test_invalid_time_is_rejected_without_writes(tmp_path, time):
    store = make(tmp_path)
    with pytest.raises(ValueError):
        store.ensure_profile(MEMBER, now=time)
    assert store.profiles() == []


def test_hostile_provider_values_and_reasons_are_rejected_without_credits(tmp_path):
    store = make(tmp_path)
    profile(store)
    with pytest.raises(ValueError):
        store.credit_referral(
            referrer_discord_id=MEMBER,
            referee_whop_user_id="https://attacker.example/user_victim",
            payment_id="pay_injected",
            membership_id="mem_injected",
        )
    reward = credit(store)
    with pytest.raises(ValueError):
        store.flag_exception(reward["reward_id"], "https://attacker.example/private-token")
    assert store.summary(MEMBER)["pending"] == 1
