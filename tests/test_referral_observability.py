"""Referral transitions use private operational metadata, never billing bodies."""

import json

import pytest
from discord_bridge.monitoring import DeliveryMonitor
from discord_bridge.referral_store import ReferralStore
from discord_bridge.referral_worker import ReferralService
from discord_bridge.whop_client import WhopError

from test_discord_referral_worker import COMPANY, GUILD, PLAN, PRODUCT, REFERRER, Provider, payment


def service_fixture(tmp_path, sink):
    store = ReferralStore(tmp_path / "rewards.sqlite3", guild_id=GUILD, company_id=COMPANY)
    provider = Provider()
    service = ReferralService(provider, store, company_id=COMPANY, product_ids={PRODUCT},
                              plan_prices={PLAN: 3000}, checkout_plan_id=PLAN, event_sink=sink)
    return service, store, provider


def test_referral_provider_error_is_logged_without_credentials_or_payloads(tmp_path):
    monitor = DeliveryMonitor(tmp_path, entry_point="collector_worker", console=False)
    service, _, provider = service_fixture(tmp_path, monitor.emit)

    def failed_payments():
        raise WhopError("rate_limited", status=429, retry_after=30)

    provider.list_payments = failed_payments
    with pytest.raises(WhopError):
        service.poll()
    events = [json.loads(line) for line in monitor.path.read_text().splitlines()]
    error = next(event for event in events if event["event"] == "referral_error")
    assert error["status"] == 429 and error["provider_error"] == "rate_limited"
    assert error["component"] == "referrals"
    assert not {"token", "code", "payment_id", "membership_id"} & error.keys()


def test_unchanged_referral_polls_do_not_generate_idle_cycle_logs(tmp_path):
    events = []
    service, _, _ = service_fixture(
        tmp_path, lambda event, **fields: events.append((event, fields))
    )
    service.poll()
    assert any(event == "referral_cycle" for event, _ in events)
    events.clear()
    service.poll()
    assert not events


def test_reward_application_stages_are_correlated_by_safe_hashed_refs(tmp_path):
    monitor = DeliveryMonitor(tmp_path, entry_point="collector_worker", console=False)
    service, store, provider = service_fixture(tmp_path, monitor.emit)
    view = service.member_view(REFERRER)
    provider.payments = [payment(store.profile(REFERRER)["friend_promo_id"])]
    service.poll()
    events = [json.loads(line) for line in monitor.path.read_text().splitlines()]
    changes = [event for event in events if event["event"] == "referral_changed"]
    assert {event.get("reward_state") for event in changes} >= {"applying", "applied"}
    application = [event for event in changes if "reward_ref" in event]
    assert len({event["reward_ref"] for event in application}) == 1
    assert all(len(event["reward_ref"]) == 32 for event in application)
    encoded = json.dumps(events)
    assert REFERRER not in encoded and view["code"] not in encoded
    assert "promoCode=" not in encoded and "membership_id" not in encoded


def test_referral_logging_failure_cannot_change_reward_application(tmp_path):
    def unavailable_sink(*_args, **_kwargs):
        raise OSError("Private logging failure")

    service, store, provider = service_fixture(tmp_path, unavailable_sink)
    service.member_view(REFERRER)
    provider.payments = [payment(store.profile(REFERRER)["friend_promo_id"])]
    service.poll()
    assert store.summary(REFERRER)["applied"] == 1


def test_completed_referral_polls_do_not_relog_old_payments_or_unchanged_state(tmp_path):
    events = []
    service, store, provider = service_fixture(
        tmp_path, lambda event, **fields: events.append((event, fields))
    )
    service.member_view(REFERRER)
    provider.payments = [payment(store.profile(REFERRER)["friend_promo_id"])]
    service.poll()
    events.clear()
    service.poll()
    assert not events


def test_referral_worker_error_and_recovery_are_logged_once_per_transition(tmp_path):
    events = []
    service, _, provider = service_fixture(
        tmp_path, lambda event, **fields: events.append((event, fields))
    )
    attempts = 0

    def payments():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise WhopError("network_failure")
        return []

    class BoundedStop:
        waits = 0
        stopped = False

        def is_set(self):
            return self.stopped

        def set(self):
            self.stopped = True

        def wait(self, _seconds):
            self.waits += 1
            if self.waits == 2:
                self.stopped = True

    provider.list_payments = payments
    service._stop = BoundedStop()
    service.start()
    service._thread.join(timeout=2)
    assert not service.is_alive()
    assert attempts == 2
    assert sum(event == "referral_error" for event, _ in events) == 1
    assert sum(event == "worker_recovered" for event, _ in events) == 1
    assert [fields["transition"] for event, fields in events if event == "worker_lifecycle"] == [
        "started", "stopped"
    ]


def test_unchanged_exception_refresh_does_not_emit_another_reward_change(tmp_path):
    events = []
    service, store, provider = service_fixture(
        tmp_path, lambda event, **fields: events.append((event, fields))
    )
    service.member_view(REFERRER)
    provider.payments = [payment(store.profile(REFERRER)["friend_promo_id"])]
    service.poll()
    provider.payments[0]["refunded_at"] = "2026-10-31T00:00:00Z"
    service.poll()
    before = store.rewards(REFERRER)[0]
    assert before["state"] == "exception" and before["reason"] == "purchase_reversed"
    events.clear()
    service.poll()
    after = store.rewards(REFERRER)[0]
    assert (after["state"], after["reason"]) == (before["state"], before["reason"])
    assert not events


def test_reward_reason_changes_still_emit_a_state_receipt(tmp_path):
    events = []
    service, store, provider = service_fixture(
        tmp_path, lambda event, **fields: events.append((event, fields))
    )
    service.member_view(REFERRER)
    provider.payments = [payment(store.profile(REFERRER)["friend_promo_id"])]
    service.poll()
    reward = store.rewards(REFERRER)[0]
    service._record_reward(store.flag_exception, reward["reward_id"], "purchase_reversed")
    events.clear()
    service._record_reward(store.flag_exception, reward["reward_id"], "application_reconciliation")
    assert len(events) == 1 and events[0][0] == "referral_changed"
