"""Two-sided invoice discounts; Whop remains the sole paid-access authority."""

from __future__ import annotations

import hashlib
import threading
import time
import uuid
from contextlib import nullcontext
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from .monitoring import emit_event, error_fields
from .request_store import member_ref
from .whop_client import WhopError


def money_cents(value):
    """Only explicit USD provider Money values are eligible financial evidence."""
    if not isinstance(value, dict) or str(value.get("currency", "")).lower() != "usd":
        return None
    try:
        amount = Decimal(str(value["amount"]))
        if not amount.is_finite() or amount < 0:
            return None
        cents = amount * 100
        if cents != cents.to_integral_value():
            return None
        return int(cents)
    except (KeyError, ValueError, InvalidOperation):
        return None


def _user(row):
    return row.get("user_id") or (row.get("user") or {}).get("id")


def _account(row):
    return row.get("account_id") or (row.get("account") or {}).get("id")


class ReferralService:
    def __init__(self, provider, store, *, company_id, product_ids, plan_prices, checkout_plan_id,
                 event_sink=None):
        self.provider, self.store = provider, store
        self.company_id, self.product_ids = company_id, frozenset(product_ids)
        self.plan_prices = dict(plan_prices)
        self.checkout_plan_id = checkout_plan_id
        if checkout_plan_id not in self.plan_prices:
            raise ValueError("Referral checkout must use a configured monthly plan")
        if not self.product_ids or any(
            type(v) is not int or v <= 0 for v in self.plan_prices.values()
        ):
            raise ValueError("Referral monthly prices must be positive USD cents")
        self._thread = None
        self._stop = threading.Event()
        self._last_poll = None
        self._last_error = None
        self._poll_lock = threading.Lock()
        self._exceptions = 0
        self.event_sink = event_sink
        self._correlation_id = None
        self._summary_signature = None
        self._member_summaries = {}
        self._reward_log_state = {}

    def _event(self, event, **fields):
        emit_event(self.event_sink, event, component="referrals",
                   correlation_id=self._correlation_id, **fields)

    def _error(self, phase, exc, *, discord_id=None, reward=None):
        fields = error_fields(exc)
        if isinstance(exc, WhopError):
            fields.update(provider_error=exc.code, status=exc.status,
                          retry_after_seconds=exc.retry_after, ambiguous=exc.ambiguous)
        if discord_id is not None:
            fields["member_ref"] = member_ref(discord_id)
        if reward is not None:
            fields["reward_ref"] = hashlib.sha256(reward["reward_id"].encode()).hexdigest()[:32]
        self._event("referral_error", phase=phase, **fields)

    def _record_reward(self, operation, *args, **kwargs):
        # Each store method commits before returning. Logging observes the
        # returned state and cannot alter the operation or its retry policy.
        row = operation(*args, **kwargs)
        if self.event_sink is not None and isinstance(row, dict) and "state" in row:
            signature = tuple(row.get(key) for key in (
                "state", "reason", "application_generation", "membership_id", "promo_id",
                "used_payment_id",
            )) + (row.get("apply_submitted_at") is not None,)
            if self._reward_log_state.get(row["reward_id"]) == signature:
                return row
            self._reward_log_state[row["reward_id"]] = signature
            while len(self._reward_log_state) > 4096:
                self._reward_log_state.pop(next(iter(self._reward_log_state)))
            operation_name = getattr(operation, "__name__", None)
            phase = {"claim_reward": "claim", "mark_submission": "begin_send",
                     "mark_applied": "settle"}.get(operation_name, "reconcile")
            self._event("referral_changed", reward_state=row["state"],
                        phase=phase,
                        reward_ref=hashlib.sha256(row["reward_id"].encode()).hexdigest()[:32],
                        member_ref=member_ref(row["referrer_discord_id"]))
        return row

    def _scoped(self, row):
        return (
            isinstance(row, dict)
            and _account(row) == self.company_id
            and row.get("product_id") in self.product_ids
            and row.get("variant_id") in self.plan_prices
        )

    def _promo(self, row, *, reward, require_active=True):
        if not isinstance(row, dict) or _account(row) != self.company_id:
            raise ValueError("Invalid referral promo account")
        try:
            amount = Decimal(str(row.get("amount_off")))
        except InvalidOperation:
            raise ValueError("Invalid referral promo percentage") from None
        if (
            amount != Decimal("0.30")
            or row.get("promo_type") != "percentage"
            or row.get("duration") != "once"
            or row.get("promo_duration_months") != 1
            or row.get("affiliate") is not None
            or not isinstance(row.get("id"), str)
            or not isinstance(row.get("code"), str)
            or row.get("new_users_only") is not (not reward)
            or row.get("existing_memberships_only") is not reward
            or (require_active and row.get("status") != "active")
        ):
            raise ValueError("Referral promo must be a commission-free one-payment 30% discount")
        return row

    def _find_promo(self, code, rows=None):
        rows = self.provider.list_promos() if rows is None else rows
        found = [row for row in rows if str(row.get("code", "")).lower() == code.lower()]
        if len(found) > 1:
            raise ValueError("Ambiguous referral promo")
        if not found:
            return None
        row = found[0]
        # Account-scoped promo list items omit full account/eligibility data.
        return self.provider.retrieve_promo(row["id"]) if _account(row) is None else row

    def _ensure_promo(self, code, *, reward, intent_key, promos=None):
        existing = self._find_promo(code, promos)
        if existing is not None:
            return self._promo(existing, reward=reward)
        try:
            row = self.provider.create_promo(
                code, existing_memberships_only=reward, idempotency_key=intent_key
            )
        except WhopError as exc:
            if not exc.ambiguous and exc.status not in {409, 422}:
                raise
            # A create response can be lost. Recover the exact persisted intent,
            # never create another random code or assume the write did not land.
            row = self._find_promo(code)
            if row is None:
                raise
        row = self._promo(row, reward=reward)
        if promos is not None:
            promos.append(row)
        return row

    def _profile(self, discord_id):
        profile = self.store.ensure_profile(discord_id)
        scope = hashlib.sha256("|".join(sorted(self.plan_prices)).encode()).hexdigest()[:8]
        code = profile["share_code"].lower() + scope
        if (profile["friend_promo_code"] or "").lower() != code:
            promo = self._ensure_promo(
                code,
                reward=False,
                intent_key=f"referral-friend:{self.company_id}:{discord_id}:{scope}",
            )
            self.store.set_friend_promo(discord_id, promo_id=promo["id"], promo_code=promo["code"])
            profile = self.store.profile(discord_id)
        else:
            promo = self._find_promo(profile["friend_promo_code"])
            if promo is None or promo["id"] != profile["friend_promo_id"]:
                raise ValueError("Referral checkout promotion is unavailable")
            self._promo(promo, reward=False)
        return profile

    def _membership(self, discord_id):
        # This provider filter is the identity proof. No email, display name,
        # manually supplied membership ID, browser parameter or role is trusted.
        rows = [
            row
            for row in self.provider.list_memberships(discord_id=discord_id)
            if self._scoped(row) and _user(row)
        ]
        users = {_user(row) for row in rows}
        if len(users) > 1:
            raise ValueError("Discord identity has ambiguous billing accounts")
        if users:
            self.store.bind_whop_user(discord_id, next(iter(users)))
        eligible = [
            row
            for row in rows
            if row.get("status") in {"active", "trialing"}
            and row.get("cancel_at_period_end") is False
            and money_cents(row.get("renewal_price")) not in {None, 0}
        ]
        # Select one next eligible monthly bill, never discount both memberships.
        return (
            min(eligible, key=lambda row: (row.get("current_period_end") or "9999", row["id"]))
            if eligible
            else None
        )

    def _paid(self, payment):
        return (
            self._scoped(payment)
            and payment.get("status") == "paid"
            and bool(payment.get("paid_at"))
            and bool(payment.get("membership_id"))
            and bool(_user(payment))
            and money_cents(payment.get("total")) not in {None, 0}
            and (
                payment.get("refunded_amount") is None
                or money_cents(payment.get("refunded_amount")) == 0
            )
            and not payment.get("refunded_at")
            and not payment.get("dispute_created_at")
        )

    def _credit(self, payments):
        for payment in payments:
            if (
                not self._paid(payment)
                or payment.get("billing_reason") != "subscription_create"
                or not payment.get("promo_code_id")
            ):
                continue
            try:
                profile = self.store.profile_for_promo(payment["promo_code_id"])
            except ValueError:
                continue
            if not profile or profile["whop_user_id"] == _user(payment):
                continue
            try:
                self.store.credit_referral(
                    referrer_discord_id=profile["discord_id"],
                    referee_whop_user_id=_user(payment),
                    payment_id=payment["id"],
                    membership_id=payment["membership_id"],
                )
            except ValueError as exc:
                self._last_error = "attribution_conflict"
                self._error("reconcile", exc, discord_id=profile["discord_id"])

    def _reversals(self, profile, payments):
        by_id = {row["id"]: row for row in payments}
        for reward in self.store.rewards(profile["discord_id"]):
            payment = by_id.get(reward["qualifying_payment_id"])
            if payment is None or not self._scoped(payment):
                continue
            reversed_sale = (
                bool(payment.get("refunded_at"))
                or bool(payment.get("dispute_created_at"))
                or money_cents(payment.get("refunded_amount")) not in {None, 0}
            )
            if not reversed_sale or reward["state"] == "void":
                continue
            if reward["state"] == "pending":
                self._record_reward(self.store.void_reward, reward["reward_id"],
                                    reason="purchase_reversed")
            else:
                # Never issue a retroactive charge or erase a consumed invoice.
                self._record_reward(self.store.flag_exception, reward["reward_id"],
                                    "purchase_reversed")

    def _reconcile(self, profile, memberships, payments, promos):
        discord_id = profile["discord_id"]
        for reward in self.store.rewards(discord_id):
            if (
                reward["state"] not in {"applying", "applied", "exception"}
                or reward["reason"] == "self_referral"
            ):
                continue
            targets = list(memberships)
            if reward["membership_id"] and not any(
                row["id"] == reward["membership_id"] for row in targets
            ):
                row = self.provider.retrieve_membership(reward["membership_id"])
                if not self._scoped(row) or _user(row) != profile["whop_user_id"]:
                    raise ValueError("Reward belongs to another membership identity")
                targets.append(row)
            if (
                reward["state"] == "applying"
                and reward["apply_submitted_at"] is None
                and reward["lease_until"] <= time.time()
            ):
                self._record_reward(self.store.release_reward,
                    reward["reward_id"],
                    reward["lease_token"],
                    reason="preparation_recovered",
                    require_unsubmitted=True,
                )
                continue
            promo = self._find_promo(reward["reward_code"], promos)
            if promo is None:
                continue
            self._promo(promo, reward=True, require_active=False)
            matching = [
                payment
                for payment in payments
                if payment.get("promo_code_id") == promo["id"]
                and payment.get("membership_id") == reward["membership_id"]
                and _user(payment) == profile["whop_user_id"]
                and self._paid(payment)
                and payment.get("billing_reason")
                in {"subscription_create", "subscription_cycle", "subscription"}
            ]
            attached = any(
                row["id"] == reward["membership_id"]
                and row.get("promo_code_id") == promo["id"]
                and _user(row) == profile["whop_user_id"]
                for row in targets
            )
            if reward["state"] in {"applying", "exception"} and (attached or matching):
                if reward["reason"] == "purchase_reversed" and not matching:
                    continue
                self._record_reward(self.store.reconcile_application,
                    reward["reward_id"], outcome="applied", promo_id=promo["id"]
                )
            if matching:
                first = min(matching, key=lambda row: (row["paid_at"], row["id"]))
                self._record_reward(self.store.mark_used,
                    reward["reward_id"],
                    payment_id=first["id"],
                    membership_id=first["membership_id"],
                )
                if reward["reason"] == "purchase_reversed":
                    self._record_reward(self.store.flag_exception, reward["reward_id"],
                                        "purchase_reversed")
            elif (
                not attached
                and reward["state"] == "applying"
                and reward["apply_submitted_at"] is not None
            ):
                age = time.time() - reward["apply_submitted_at"]
                same_key = reward["provider_fingerprint"] is not None and reward[
                    "provider_fingerprint"
                ] == getattr(self.provider, "credential_fingerprint", None)
                if not same_key or not 0 <= age < 86340:
                    self._record_reward(self.store.flag_exception, reward["reward_id"],
                                        "application_reconciliation")
                    continue
                target = next(
                    (row for row in targets if row["id"] == reward["membership_id"]), None
                )
                if (
                    target
                    and not target.get("promo_code_id")
                    and target["status"] in {"active", "trialing"}
                ):
                    # Whop caches the identical authenticated POST for 24h.
                    # Keep the same credential, version, body and intent key.
                    result = self.provider.apply_promo(
                        target["id"], promo["code"], idempotency_key=reward["intent_key"] + ":apply"
                    )
                    if (
                        self._scoped(result)
                        and _user(result) == profile["whop_user_id"]
                        and result.get("promo_code_id") == promo["id"]
                    ):
                        self._record_reward(self.store.reconcile_application,
                            reward["reward_id"], outcome="applied", promo_id=promo["id"]
                        )

    def _apply(self, profile, membership, promos):
        if membership is None or membership.get("promo_code_id"):
            return
        reward = self._record_reward(self.store.claim_reward,
            profile["discord_id"],
            membership["id"],
            provider_fingerprint=getattr(self.provider, "credential_fingerprint", None),
        )
        if reward is None:
            return
        if reward["referee_whop_user_id"] == _user(membership):
            self._record_reward(self.store.flag_exception, reward["reward_id"], "self_referral")
            return
        submitted = False
        try:
            promo = self._ensure_promo(
                reward["reward_code"],
                reward=True,
                intent_key=reward["intent_key"] + ":promo",
                promos=promos,
            )
            self._record_reward(self.store.mark_submission, reward["reward_id"],
                                reward["lease_token"])
            submitted = True
            result = self.provider.apply_promo(
                membership["id"], promo["code"], idempotency_key=reward["intent_key"] + ":apply"
            )
            if not self._scoped(result) or _user(result) != profile["whop_user_id"]:
                raise ValueError("Discount response did not match the intended member")
            if result.get("promo_code_id") != promo["id"]:
                raise ValueError("Discount application is not confirmed")
            self._record_reward(self.store.mark_applied,
                reward["reward_id"], reward["lease_token"], promo_id=promo["id"]
            )
        except WhopError as exc:
            self._error("apply", exc, discord_id=profile["discord_id"], reward=reward)
            if not submitted or not exc.ambiguous:
                # A definite provider rejection has no application to reconcile.
                self._record_reward(self.store.release_reward,
                    reward["reward_id"], reward["lease_token"], reason=exc.code
                )
            self._last_error = "provider_retry"
        except Exception as exc:
            self._error("apply", exc, discord_id=profile["discord_id"], reward=reward)
            # Unknown write outcomes stay applying until provider evidence agrees.
            if not submitted:
                self._record_reward(self.store.release_reward,
                    reward["reward_id"],
                    reward["lease_token"],
                    reason="preparation_retry",
                    require_unsubmitted=True,
                )
            self._last_error = "discount_reconciliation"

    def poll(self):
        if not self._poll_lock.acquire(blocking=False):
            return
        previous_error = self._last_error
        self._correlation_id = uuid.uuid4().hex
        started = time.monotonic()
        try:
            self._last_error = None
            profiles = self.store.profiles()
            memberships = {}
            for profile in profiles:
                try:
                    memberships[profile["discord_id"]] = self._membership(profile["discord_id"])
                except (WhopError, ValueError) as exc:
                    self._last_error = "member_lookup"
                    self._error("lookup", exc, discord_id=profile["discord_id"])
            payments = self.provider.list_payments()
            promos = self.provider.list_promos()
            self._credit(payments)
            for original in profiles:
                discord_id = original["discord_id"]
                if discord_id not in memberships:
                    continue
                profile = self.store.profile(discord_id)
                member = memberships[discord_id]
                try:
                    self._reversals(profile, payments)
                    self._reconcile(profile, [member] if member else [], payments, promos)
                    self._apply(profile, member, promos)
                except (WhopError, ValueError) as exc:
                    self._last_error = "application_reconciliation"
                    self._error("reconcile", exc, discord_id=discord_id)
            summaries = {row["discord_id"]: self.store.summary(row["discord_id"])
                         for row in profiles}
            self._exceptions = sum(summary["exception"] for summary in summaries.values())
            for discord_id, summary in summaries.items():
                if self._member_summaries.get(discord_id) != summary:
                    self._event("referral_changed", member_ref=member_ref(discord_id),
                                queue=summary,
                                baseline=discord_id not in self._member_summaries)
            self._member_summaries = summaries
            signature = (len(profiles), self._exceptions, self._last_error,
                         sum(summary["total"] for summary in summaries.values()))
            if signature != self._summary_signature:
                self._event("referral_cycle", profiles=len(profiles), exceptions=self._exceptions,
                            elapsed_seconds=time.monotonic() - started,
                            baseline=self._summary_signature is None)
                self._summary_signature = signature
            if previous_error is not None and self._last_error is None:
                self._event("worker_recovered", transition="recovered", phase="poll")
            self._last_poll = time.time()
        except Exception as exc:
            self._error("poll", exc)
            raise
        finally:
            self._poll_lock.release()

    def member_view(self, discord_id):
        operation = getattr(self.provider, "operation_deadline", None)
        with operation(25) if operation is not None else nullcontext():
            return self._member_view(discord_id)

    def _member_view(self, discord_id):
        profile = self._profile(discord_id)
        member = self._membership(discord_id)
        counts = self.store.summary(discord_id)
        normal = self.plan_prices[(member or {}).get("variant_id", self.checkout_plan_id)]
        own_applied = [
            row
            for row in self.store.rewards(discord_id)
            if row["state"] == "applied"
            and member
            and row["membership_id"] == member["id"]
            and row["promo_id"] == member.get("promo_code_id")
        ]
        own_ids = {row["reward_id"] for row in own_applied}
        unresolved = sum(
            1
            for row in self.store.rewards(discord_id)
            if row["state"] in {"applying", "applied", "exception"}
            and row["reward_id"] not in own_ids
            and row["reason"] not in {"self_referral", "purchase_reversed"}
        )
        conflict = bool(member and member.get("promo_code_id") and not own_applied)
        # Whop's renewal_price already includes the still-active discount.
        # The catalog price cannot stand in for a member's actual invoice.
        discounted = money_cents(member.get("renewal_price")) if own_applied else None
        return {
            "ready": True,
            "url": f"https://whop.com/checkout/{self.checkout_plan_id}?"
            + urlencode({"promoCode": profile["friend_promo_code"]}),
            "code": profile["friend_promo_code"],
            "queued": counts["pending"] + unresolved,
            "applied": len(own_applied),
            "used": counts["used"],
            "membership_state": "none" if member is None else "conflict" if conflict else "active",
            "next_payment_cents": discounted if own_applied else None,
            "normal_price_cents": normal,
            "friend_normal_price_cents": self.plan_prices[self.checkout_plan_id],
            "reason": "existing_discount" if conflict else None,
        }

    def start(self):
        if self._thread and self._thread.is_alive():
            return

        def run():
            self._event("worker_lifecycle", transition="started")
            while not self._stop.is_set():
                try:
                    self.poll()
                except Exception:
                    self._last_error = "provider_unavailable"
                self._stop.wait(60)
            self._event("worker_lifecycle", transition="stopped")

        self._thread = threading.Thread(target=run, name="membership-referrals", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=12)

    def is_alive(self):
        return bool(self._thread and self._thread.is_alive())

    def health(self):
        return {
            "enabled": True,
            "running": self.is_alive(),
            "last_poll_at": self._last_poll,
            "last_error": self._last_error,
            "exceptions": self._exceptions,
        }
