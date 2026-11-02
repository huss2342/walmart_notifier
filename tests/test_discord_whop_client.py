"""Offline Whop wire-contract tests; never reach the real account or charge."""

from __future__ import annotations

import io
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit

import pytest
from discord_bridge.whop_client import API_VERSION, WhopClient, WhopError

COMPANY = "biz_Example123456"
PRODUCT = "prod_Example123456"
VARIANT = "plan_Example123456"
MEMBERSHIP = "mem_Example123456"
USER = "user_Example123456"
PROMO = "promo_Example123456"


def money(amount="30.00"):
    return {"amount": amount, "currency": "usd", "decimals": 2, "display_decimals": 2}


def membership(**changes):
    return {
        "id": MEMBERSHIP,
        "account": {"id": COMPANY},
        "product": {"id": PRODUCT},
        "product_id": PRODUCT,
        "variant_id": VARIANT,
        "user_id": USER,
        "status": "active",
        "promo_code_id": None,
        "cancel_at_period_end": False,
        "billing_period_days": 30,
        "current_period_start": "2026-10-01T00:00:00Z",
        "current_period_end": "2026-11-01T00:00:00Z",
        "renewal_price": money(),
        **changes,
    }


def promo(**changes):
    return {
        "id": PROMO,
        "account": {"id": COMPANY},
        "product": {"id": PRODUCT},
        "code": "rrexample123",
        "amount_off": 0.30,
        "promo_type": "percentage",
        "duration": "once",
        "promo_duration_months": 1,
        "new_users_only": True,
        "existing_memberships_only": False,
        "one_per_customer": True,
        "unlimited_stock": True,
        "affiliate": None,
        "status": "active",
        **changes,
    }


def payment(**changes):
    return {
        "id": "pay_Example123456",
        "account_id": COMPANY,
        "product_id": PRODUCT,
        "variant_id": VARIANT,
        "membership_id": MEMBERSHIP,
        "user": {"id": USER},
        "status": "paid",
        "billing_reason": "subscription_create",
        "promo_code_id": PROMO,
        "paid_at": "2026-10-30T00:00:00Z",
        "total": money("21.00"),
        "subtotal": money("21.00"),
        "refunded_amount": money("0.00"),
        **changes,
    }


def page(*items, next_cursor=None, final_cursor=None):
    return {
        "data": list(items),
        "page_info": {
            "has_next_page": next_cursor is not None,
            "has_previous_page": False,
            "start_cursor": None,
            "end_cursor": next_cursor or final_cursor,
        },
    }


class Response:
    def __init__(self, data=None, *, status=200, raw=None, headers=None):
        self.status = status
        self.headers = headers or {}
        self.body = io.BytesIO(raw if raw is not None else json.dumps(data).encode())
        self.closed = False

    def read(self, size):
        return self.body.read(size)

    def close(self):
        self.closed = True


class Socket:
    def settimeout(self, _timeout):
        pass

    def shutdown(self, _how):
        pass

    def close(self):
        pass


class Factory:
    def __init__(self, *responses, connect_error=None, response_barrier=None):
        self.responses = list(responses)
        self.connect_error = connect_error
        self.response_barrier = response_barrier
        self.lock = threading.Lock()
        self.calls, self.hosts, self.connections = [], [], []
        self.timeouts = []

    def __call__(self, host, *, timeout):
        self.hosts.append(host)
        self.timeouts.append(timeout)
        outer = self

        class Connection:
            sock = None
            closed = False

            def connect(self):
                if outer.connect_error:
                    raise outer.connect_error
                self.sock = Socket()

            def request(self, method, path, *, body, headers):
                outer.calls.append((method, path, body, headers))

            def getresponse(self):
                with outer.lock:
                    response = outer.responses.pop(0)
                if outer.response_barrier is not None:
                    outer.response_barrier.wait(timeout=3)
                if isinstance(response, Exception):
                    raise response
                return response

            def close(self):
                self.closed = True

        connection = Connection()
        self.connections.append(connection)
        return connection


def client(*responses, sandbox=False, **factory_options):
    result = WhopClient(
        "offline-secret",
        company_id=COMPANY,
        product_ids=[PRODUCT],
        variant_ids=[VARIANT],
        sandbox=sandbox,
    )
    fake = Factory(*responses, **factory_options)
    result._connection_factory = fake
    return result, fake


def test_memberships_use_company_scope_discord_identity_and_complete_pagination():
    other = membership(id="mem_Second123456", status="canceled", promo_code_id=PROMO)
    c, fake = client(
        Response(page(membership(), next_cursor="cursor-one")),
        Response(page(other, final_cursor="last-record")),
    )
    assert c.list_memberships(discord_id="123456789012345678") == [membership(), other]
    assert len(fake.calls) == 2
    query = parse_qs(urlsplit(fake.calls[0][1]).query)
    assert query["account_id"] == [COMPANY]
    assert query["external_account"] == ["discord_123456789012345678"]
    assert query["product_ids"] == [PRODUCT] and query["variant_ids"] == [VARIANT]
    assert parse_qs(urlsplit(fake.calls[1][1]).query)["after"] == ["cursor-one"]
    assert fake.calls[0][3]["Api-Version-Date"] == API_VERSION == "2026-10-29-8"
    assert fake.calls[0][3]["Authorization"] == "Bearer offline-secret"
    assert all(connection.closed for connection in fake.connections)


def test_current_payment_fields_are_preserved_without_float_conversion():
    c, _ = client(Response(page(payment())))
    assert c.list_payments(created_after="2026-10-01T00:00:00Z") == [payment()]


def test_friend_promo_uses_whole_percent_once_and_a_stable_idempotency_key():
    c, fake = client(Response(promo()))
    assert c.create_promo("rrexample123", idempotency_key="referral-create-123") == promo()
    method, path, body, headers = fake.calls[0]
    assert method == "POST" and path == "/api/v1/promo_codes"
    assert headers["Idempotency-Key"] == "referral-create-123"
    assert json.loads(body) == {
        "account_id": COMPANY,
        "amount_off": 30,
        "base_currency": "usd",
        "code": "rrexample123",
        "new_users_only": True,
        "promo_duration_months": 1,
        "promo_type": "percentage",
        "one_per_customer": True,
        "existing_memberships_only": False,
        "unlimited_stock": True,
        "product_id": PRODUCT,
        "variant_ids": [VARIANT],
    }


def test_reward_code_is_usable_for_existing_members_only_and_has_one_redemption():
    expected = promo(
        new_users_only=False, existing_memberships_only=True, unlimited_stock=False, stock=1
    )
    c, fake = client(Response(expected))
    assert (
        c.create_promo(
            "rrexample123", existing_memberships_only=True, idempotency_key="reward-create-123"
        )
        == expected
    )
    body = json.loads(fake.calls[0][2])
    assert body["new_users_only"] is False and body["stock"] == 1


def test_apply_checks_membership_scope_then_uses_membership_promo_endpoint():
    updated = membership(promo_code_id=PROMO, renewal_price=money("21.00"))
    c, fake = client(Response(membership()), Response(updated))
    assert c.apply_promo(MEMBERSHIP, "rrexample123", idempotency_key="apply-reward-123") == updated
    method, path, body, headers = fake.calls[-1]
    assert (method, path) == ("POST", f"/api/v1/memberships/{MEMBERSHIP}/apply_promo_code")
    assert json.loads(body) == {"promo_code": "rrexample123"}
    assert headers["Idempotency-Key"] == "apply-reward-123"


@pytest.mark.parametrize(
    "changes",
    [
        {"account": {"id": "biz_Other123456"}},
        {"product_id": "prod_Other123456"},
        {"variant_id": "plan_Other123456"},
        {"id": None},
        {"user_id": "not-a-user"},
        {"status": None},
        {"renewal_price": money("nan")},
    ],
)
def test_wrong_or_malformed_membership_never_reaches_a_write(changes):
    c, fake = client(Response(membership(**changes)))
    with pytest.raises(WhopError):
        c.apply_promo(MEMBERSHIP, "rrexample123", idempotency_key="apply-reward-123")
    assert all(call[0] == "GET" for call in fake.calls)


@pytest.mark.parametrize("changes", [{"status": "canceled"}, {"promo_code_id": PROMO}])
def test_ineligible_membership_cannot_receive_another_promo(changes):
    c, fake = client(Response(membership(**changes)))
    with pytest.raises(WhopError) as raised:
        c.apply_promo(MEMBERSHIP, "rrexample123", idempotency_key="apply-reward-123")
    assert raised.value.ambiguous is False
    assert len(fake.calls) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"amount_off": 30},
        {"duration": "forever"},
        {"promo_duration_months": 0},
        {"account": {"id": "biz_Other123456"}},
        {"affiliate": {"user_id": USER}},
        {"code": "anothercode"},
        {"status": "inactive"},
        {"promo_duration_months": True},
        {"product": None},
    ],
)
def test_unexpected_created_promo_is_not_reported_as_a_valid_referral_code(changes):
    c, _ = client(Response(promo(**changes)))
    with pytest.raises(WhopError) as raised:
        c.create_promo("rrexample123", idempotency_key="referral-create-123")
    assert raised.value.ambiguous is True


def test_promo_list_allows_its_documented_accountless_item_shape():
    listed = promo()
    del listed["account"]
    c, _ = client(Response(page(listed)))
    assert c.list_promos() == [listed]


@pytest.mark.parametrize(
    "data",
    [
        {"data": [], "page_info": {}},
        {"data": {}, "page_info": {"has_next_page": False}},
        {"data": [], "page_info": {"has_next_page": True, "end_cursor": None}},
    ],
)
def test_bad_pagination_is_not_silently_treated_as_an_empty_account(data):
    c, _ = client(Response(data))
    with pytest.raises(WhopError):
        c.list_memberships()


def test_repeating_cursor_fails_instead_of_polling_forever():
    c, fake = client(
        Response(page(membership(), next_cursor="same")),
        Response(page(membership(), next_cursor="same")),
    )
    with pytest.raises(WhopError):
        c.list_memberships()
    assert len(fake.calls) == 2


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_redirects_never_forward_the_credential(status):
    c, fake = client(Response(status=status, headers={"Location": "https://attacker.test"}))
    with pytest.raises(WhopError) as raised:
        c.list_memberships()
    assert raised.value.status == status
    assert fake.hosts == ["api.whop.com"] and len(fake.calls) == 1


def test_sandbox_has_a_separate_fixed_host():
    c, fake = client(Response(page()), sandbox=True)
    assert c.list_memberships() == []
    assert fake.hosts == ["sandbox-api.whop.com"]


def test_credential_fingerprint_detects_rotation_and_environment_without_exposing_secret():
    production, _ = client()
    repeated, _ = client()
    sandbox, _ = client(sandbox=True)
    rotated = WhopClient(
        "different-offline-key", company_id=COMPANY, product_ids=[PRODUCT], variant_ids=[VARIANT]
    )
    assert len(production.credential_fingerprint) == 64
    assert production.credential_fingerprint == repeated.credential_fingerprint
    assert production.credential_fingerprint != sandbox.credential_fingerprint
    assert production.credential_fingerprint != rotated.credential_fingerprint
    assert "offline-secret" not in production.credential_fingerprint


def test_unknown_write_outcome_is_marked_ambiguous_and_never_retried():
    c, fake = client(TimeoutError("offline-secret private account details"))
    with pytest.raises(WhopError) as raised:
        c.create_promo("rrexample123", idempotency_key="referral-create-123")
    assert raised.value.ambiguous is True and len(fake.calls) == 1
    assert "offline-secret" not in str(raised.value) and raised.value.__cause__ is None


def test_connect_failure_before_any_bytes_is_not_an_ambiguous_write():
    c, fake = client(connect_error=OSError("private error"))
    with pytest.raises(WhopError) as raised:
        c.create_promo("rrexample123", idempotency_key="referral-create-123")
    assert raised.value.ambiguous is False and fake.calls == []


def test_rate_limit_is_returned_to_the_worker_without_inline_retries():
    c, fake = client(
        Response(
            {"error": {"message": "offline-secret"}}, status=429, headers={"Retry-After": "12.5"}
        )
    )
    with pytest.raises(WhopError) as raised:
        c.list_memberships()
    assert raised.value.status == 429 and raised.value.retry_after == 12.5
    assert "offline-secret" not in str(raised.value) and len(fake.calls) == 1


@pytest.mark.parametrize("status", [429, 503])
def test_retry_after_blocks_the_shared_client_without_network_until_its_deadline(
    monkeypatch, status
):
    clock = [1000.0]
    monkeypatch.setattr("discord_bridge.whop_client.time.monotonic", lambda: clock[0])
    c, fake = client(Response({}, status=status, headers={"Retry-After": "300"}), Response(page()))
    with pytest.raises(WhopError) as original:
        c.list_memberships()
    assert original.value.retry_after == 300
    clock[0] += 25
    with pytest.raises(WhopError) as limited:
        c.list_promos()
    assert limited.value.status == status
    assert limited.value.retry_after == 275
    assert limited.value.ambiguous is False
    assert len(fake.calls) == 1 and len(fake.hosts) == 1
    clock[0] = 1300.0
    assert c.list_memberships() == []
    assert len(fake.calls) == 2


def test_ui_and_worker_calls_share_the_same_cooldown_without_sleeping(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("discord_bridge.whop_client.time.monotonic", lambda: clock[0])
    c, fake = client(Response({}, status=429, headers={"Retry-After": "300"}))
    with pytest.raises(WhopError):
        c.list_memberships()

    def call(index):
        try:
            (c.list_promos if index % 2 else c.list_payments)()
        except WhopError as error:
            return error.status, error.retry_after, error.ambiguous
        raise AssertionError("Cooldown should have refused the call")

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(call, range(24))) == [(429, 300.0, False)] * 24
    assert len(fake.calls) == 1 and len(fake.hosts) == 1


def test_long_server_delay_is_not_shortened_to_an_arbitrary_one_day_limit(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("discord_bridge.whop_client.time.monotonic", lambda: clock[0])
    c, fake = client(Response({}, status=429, headers={"Retry-After": "90000"}))
    with pytest.raises(WhopError) as original:
        c.list_memberships()
    assert original.value.retry_after == 90000.0
    clock[0] += 86400
    with pytest.raises(WhopError) as limited:
        c.list_payments()
    assert limited.value.retry_after == 3600.0 and len(fake.calls) == 1


def test_concurrent_provider_responses_keep_the_longest_cooldown(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("discord_bridge.whop_client.time.monotonic", lambda: clock[0])
    c, fake = client(
        Response({}, status=429, headers={"Retry-After": "100"}),
        Response({}, status=503, headers={"Retry-After": "300"}),
        response_barrier=threading.Barrier(2),
    )

    def call():
        with pytest.raises(WhopError) as result:
            c.list_memberships()
        return result.value.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(call) for _ in range(2)]
        assert sorted(future.result() for future in futures) == [429, 503]
    clock[0] += 150
    with pytest.raises(WhopError) as result:
        c.list_payments()
    assert result.value.status == 503 and result.value.retry_after == 150.0
    assert len(fake.calls) == 2


@pytest.mark.parametrize("code", [None, "", "bad/path", "<@123456789>", {"code": "rrexample123"}])
def test_promo_retrieval_rejects_malformed_provider_code(code):
    c, _ = client(Response(promo(code=code)))
    with pytest.raises(WhopError) as result:
        c.retrieve_promo(PROMO)
    assert result.value.code == "invalid_response" and not result.value.ambiguous


def test_promo_list_rejects_malformed_provider_code():
    listed = promo(code="../../another")
    del listed["account"]
    c, _ = client(Response(page(listed)))
    with pytest.raises(WhopError):
        c.list_promos()


def test_idempotent_request_in_progress_is_an_ambiguous_existing_intent():
    c, fake = client(
        Response(
            {"error": {"code": "idempotency_request_in_progress", "message": "private"}},
            status=409,
        )
    )
    with pytest.raises(WhopError) as raised:
        c.create_promo("rrexample123", idempotency_key="referral-create-123")
    assert raised.value.ambiguous is True
    assert raised.value.provider_code == "idempotency_request_in_progress"
    assert "private" not in str(raised.value) and len(fake.calls) == 1


@pytest.mark.parametrize(
    "raw",
    [b"not json", b"{" + b"x" * 1_048_576, b"[]"],
    ids=["invalid-json", "over-response-limit", "non-object"],
)
def test_invalid_or_oversized_response_is_rejected(raw):
    c, _ = client(Response(raw=raw))
    with pytest.raises(WhopError):
        c.list_memberships()


@pytest.mark.parametrize(
    "invalid", ["../mem_Example123456", "https://attacker.test", "mem_bad/other"]
)
def test_untrusted_ids_never_become_paths(invalid):
    c, fake = client()
    with pytest.raises(ValueError):
        c.retrieve_membership(invalid)
    assert fake.calls == []


def test_wrong_business_payment_and_missing_id_are_rejected():
    for changed in (payment(account_id="biz_Other123456"), payment(id=None)):
        c, _ = client(Response(page(changed)))
        with pytest.raises(WhopError):
            c.list_payments()


def test_client_refuses_arbitrary_endpoints_even_for_internal_callers():
    c, fake = client()
    with pytest.raises(ValueError):
        c._request("GET", "https://attacker.test/steal")
    assert fake.calls == []


def test_configuration_rejects_tokens_headers_and_invalid_scope_ids():
    for token, company in [
        ("secret\r\nInjected: true", COMPANY),
        ("secret\x00", COMPANY),
        ("secret", "bad-company"),
    ]:
        with pytest.raises(ValueError):
            WhopClient(token, company_id=company, product_ids=[PRODUCT], variant_ids=[VARIANT])


def test_operation_budget_spans_distinct_api_fetches_and_refuses_late_requests(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("discord_bridge.whop_client.time.monotonic", lambda: clock[0])
    c, fake = client(Response(page()), Response(membership()), Response(page()))
    with c.operation_deadline(25):
        assert c.list_memberships() == []
        clock[0] += 9
        assert c.retrieve_membership(MEMBERSHIP) == membership()
        clock[0] += 9
        assert c.list_payments() == []
        clock[0] += 7
        with pytest.raises(WhopError) as expired:
            c.list_promos()
        assert expired.value.code == "operation_timeout"
        assert expired.value.ambiguous is False
    assert fake.timeouts == [10.0, 10.0, 7.0]
    assert len(fake.calls) == 3


def test_operation_budget_expiring_during_connection_construction_never_connects(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("discord_bridge.whop_client.time.monotonic", lambda: clock[0])
    c, fake = client(Response(page()))

    def slow_factory(host, *, timeout):
        connection = fake(host, timeout=timeout)
        clock[0] += 3
        return connection

    c._connection_factory = slow_factory
    with c.operation_deadline(2), pytest.raises(WhopError) as expired:
        c.list_memberships()
    assert expired.value.code == "operation_timeout" and expired.value.ambiguous is False
    assert fake.calls == [] and fake.connections[0].closed


def test_operation_budget_does_not_reset_for_each_page(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("discord_bridge.whop_client.time.monotonic", lambda: clock[0])

    class SlowResponse(Response):
        def read(self, size):
            result = super().read(size)
            if result:
                clock[0] += 9
            return result

    c, fake = client(
        SlowResponse(page(membership(), next_cursor="next-page")),
        SlowResponse(page(membership(id="mem_Second123456"))),
    )
    with c.operation_deadline(15), pytest.raises(WhopError) as expired:
        c.list_memberships()
    assert expired.value.ambiguous is False
    assert fake.timeouts == [10.0, 6.0]
    assert len(fake.calls) == 2


def test_operation_deadline_stops_paging_if_validation_consumes_remaining_budget(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("discord_bridge.whop_client.time.monotonic", lambda: clock[0])
    c, fake = client(Response(page(membership(), next_cursor="next-page")))
    original = c._membership

    def validate(row):
        result = original(row)
        clock[0] += 25
        return result

    monkeypatch.setattr(c, "_membership", validate)
    with c.operation_deadline(25), pytest.raises(WhopError) as expired:
        c.list_memberships()
    assert expired.value.code == "operation_timeout" and len(fake.calls) == 1


def test_operation_timeout_after_a_write_preserves_unknown_outcome(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("discord_bridge.whop_client.time.monotonic", lambda: clock[0])

    class SlowWriteResponse(Response):
        def read(self, size):
            result = super().read(size)
            if result:
                clock[0] += 3
            return result

    c, fake = client(SlowWriteResponse(promo()))
    with c.operation_deadline(2), pytest.raises(WhopError) as expired:
        c.create_promo("rrexample123", idempotency_key="referral-create-123")
    assert expired.value.ambiguous is True
    assert fake.timeouts == [2.0] and len(fake.calls) == 1


@pytest.mark.parametrize("seconds", [0, -1, float("inf"), True, "25"])
def test_operation_deadline_requires_a_finite_positive_duration(seconds):
    c, fake = client()
    with pytest.raises(ValueError), c.operation_deadline(seconds):
        raise AssertionError("Invalid operation budget was accepted")
    assert fake.calls == []


def test_nested_operation_deadlines_only_tighten_and_restore_on_exit(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("discord_bridge.whop_client.time.monotonic", lambda: clock[0])
    c, fake = client(*(Response(page()) for _ in range(4)))
    with c.operation_deadline(10):
        with c.operation_deadline(2):
            assert c.list_memberships() == []
        clock[0] += 1
        assert c.list_memberships() == []
        with c.operation_deadline(100):
            assert c.list_memberships() == []
    clock[0] += 100
    assert c.list_memberships() == []
    assert fake.timeouts == [2.0, 9.0, 9.0, 10.0]


def test_operation_context_restores_default_even_when_its_body_raises(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("discord_bridge.whop_client.time.monotonic", lambda: clock[0])
    c, fake = client(Response(page()))
    with pytest.raises(RuntimeError), c.operation_deadline(1):
        raise RuntimeError("body failed")
    clock[0] += 100
    assert c.list_memberships() == [] and fake.timeouts == [10.0]


def test_operation_deadlines_are_isolated_between_threads(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("discord_bridge.whop_client.time.monotonic", lambda: clock[0])
    c, fake = client(Response(page()))
    with c.operation_deadline(1):
        clock[0] += 2
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(c.list_memberships).result(timeout=3) == []
        with pytest.raises(WhopError) as expired:
            c.list_payments()
        assert expired.value.code == "operation_timeout"
    assert fake.timeouts == [10.0] and len(fake.calls) == 1


def test_closed_client_refuses_future_calls_without_starting_connections():
    c, fake = client()
    c.close()
    c.close()
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(c.list_memberships) for _ in range(8)]
        for future in futures:
            with pytest.raises(WhopError) as closed:
                future.result(timeout=3)
            assert closed.value.code == "closed" and closed.value.ambiguous is False
    assert fake.hosts == [] and fake.calls == []
