"""Fixed-origin Whop billing adapter for the 2026-10-29-8 native contract.

Response dictionaries keep provider field names and exact Money strings. This
adapter never charges, refunds, cancels, creates memberships, manages Discord
roles, follows redirects, or retries writes. The caller owns durable intent and
reconciliation. Public source: whopio/whopsdk-python, revision
564bda1e655630e14ed7e0094c91b20947f4d8b7; see memberships, payments and promo_codes.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import math
import re
import threading
import time
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode

from .api import _RequestDeadline

API_VERSION = "2026-10-29-8"
_MAX_RESPONSE = 1_048_576
_MAX_PAGES = 100
_PAGE_SIZE = 100
_ID = re.compile(r"[A-Za-z0-9]{6,80}\Z")
_PATH = re.compile(
    r"/(?:memberships|payments|promo_codes)(?:/[A-Za-z0-9_]+)?(?:/apply_promo_code)?\Z"
)
_PROVIDER_CODES = frozenset(
    {
        "idempotency_request_in_progress",
        "idempotency_key_mismatch",
        "membership_promo_code_conflict",
        "promo_code_ineligible",
    }
)


class WhopError(Exception):
    """Sanitized failure; ambiguous=True means a write may have been accepted."""

    def __init__(self, code, *, status=None, retry_after=None, ambiguous=False, provider_code=None):
        super().__init__(f"Whop request failed ({code})")
        self.code, self.status = code, status
        self.retry_after, self.ambiguous = retry_after, bool(ambiguous)
        self.provider_code = (
            provider_code
            if isinstance(provider_code, str) and provider_code in _PROVIDER_CODES
            else None
        )


def _id(value, prefix):
    if (
        not isinstance(value, str)
        or not value.startswith(prefix)
        or not _ID.fullmatch(value[len(prefix) :])
    ):
        raise ValueError("Invalid Whop identifier")
    return value


def _promo_code(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9]{6,64}", value):
        raise ValueError("Invalid referral promo code")
    return value.lower()


def _intent(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{8,200}", value):
        raise ValueError("A stable idempotency key is required")
    return value


def _invalid(ambiguous=False):
    raise WhopError("invalid_response", ambiguous=ambiguous)


def _optional_id(value, prefix, *, ambiguous=False):
    if value is not None:
        try:
            _id(value, prefix)
        except ValueError:
            _invalid(ambiguous)


def _money(value, *, ambiguous=False):
    if value is None:
        return
    if not isinstance(value, dict) or not isinstance(value.get("amount"), str):
        _invalid(ambiguous)
    try:
        amount = Decimal(value["amount"])
    except InvalidOperation:
        _invalid(ambiguous)
    if not amount.is_finite() or amount < 0:
        _invalid(ambiguous)
    if not re.fullmatch(r"[a-z]{3}", str(value.get("currency", ""))):
        _invalid(ambiguous)
    for field in ("decimals", "display_decimals"):
        if type(value.get(field)) is not int or not 0 <= value[field] <= 18:
            _invalid(ambiguous)


def _date(value):
    if value is not None:
        if not isinstance(value, str) or len(value) > 64:
            raise ValueError("Invalid timestamp")
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("Invalid timestamp") from None
        if result.tzinfo is None:
            raise ValueError("Timestamp must include timezone")
    return value


def _retry_after(headers):
    value = headers.get("Retry-After")
    if value is None:
        return None
    try:
        delay = float(value)
    except (ValueError, TypeError):
        try:
            delay = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0.0, delay) if math.isfinite(delay) else None


class WhopClient:
    def __init__(self, token, *, company_id, product_ids, variant_ids, sandbox=False, timeout=10.0):
        if not isinstance(token, str) or not re.fullmatch(r"[\x21-\x7e]{1,8192}", token):
            raise ValueError("Whop API key is missing or invalid")
        if (
            type(sandbox) is not bool
            or isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
        ):
            raise ValueError("Invalid Whop transport configuration")
        if not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise ValueError("Invalid Whop request timeout")
        self.company_id = _id(company_id, "biz_")
        self.product_ids = self._scope_ids(product_ids, "prod_")
        self.variant_ids = self._scope_ids(variant_ids, "plan_")
        self._token = token
        self._host = "sandbox-api.whop.com" if sandbox else "api.whop.com"
        self._timeout = float(timeout)
        self._connection_factory = http.client.HTTPSConnection
        self._cooldown_lock = threading.Lock()
        self._cooldown_until = 0.0
        self._cooldown_status = None
        self._closed = False
        self._operation = threading.local()

    @property
    def credential_fingerprint(self):
        """Non-secret replay boundary: Whop keys scope its idempotency cache."""
        identity = "\0".join((self._token, "https://" + self._host, API_VERSION))
        return hashlib.sha256(identity.encode()).hexdigest()

    @staticmethod
    def _scope_ids(values, prefix):
        if not isinstance(values, (list, tuple, set, frozenset)) or not 1 <= len(values) <= 100:
            raise ValueError("Explicit Whop product and variant scope is required")
        return tuple(sorted({_id(value, prefix) for value in values}))

    def _check_cooldown(self):
        """Refuse immediately; UI callbacks must never sleep on provider limits."""
        with self._cooldown_lock:
            if self._closed:
                raise WhopError("closed")
            remaining = self._cooldown_until - time.monotonic()
            if remaining > 0:
                status = self._cooldown_status
                raise WhopError(
                    "rate_limited" if status == 429 else "provider_unavailable",
                    status=status,
                    retry_after=remaining,
                )

    def _remember_cooldown(self, status, delay):
        if status not in (429, 503) or delay is None:
            return
        with self._cooldown_lock:
            until = time.monotonic() + delay
            if until > self._cooldown_until:
                self._cooldown_until, self._cooldown_status = until, status

    @contextmanager
    def operation_deadline(self, seconds):
        """Bound all requests in this thread's operation, including pagination."""
        if (
            isinstance(seconds, bool)
            or not isinstance(seconds, (int, float))
            or not math.isfinite(seconds)
            or seconds <= 0
        ):
            raise ValueError("Invalid Whop operation deadline")
        previous = getattr(self._operation, "until", None)
        until = time.monotonic() + seconds
        self._operation.until = until if previous is None else min(previous, until)
        try:
            yield self
        finally:
            if previous is None:
                del self._operation.until
            else:
                self._operation.until = previous

    def _operation_remaining(self):
        until = getattr(self._operation, "until", None)
        if until is None:
            return None
        remaining = until - time.monotonic()
        if remaining <= 0:
            raise WhopError("operation_timeout")
        return remaining

    def close(self):
        """Stop admitting work; already-admitted HTTP retains its hard deadline."""
        with self._cooldown_lock:
            self._closed = True

    def _request(self, method, path, payload=None, *, query=None, idempotency_key=None):
        if method not in {"GET", "POST"} or not isinstance(path, str) or not _PATH.fullmatch(path):
            raise ValueError("Invalid Whop API route")
        if method == "POST" or idempotency_key is not None:
            _intent(idempotency_key)
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "Api-Version-Date": API_VERSION,
        }
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        target = "/api/v1" + path
        if query:
            target += "?" + urlencode({k: v for k, v in query.items() if v is not None}, doseq=True)
        self._check_cooldown()
        remaining = self._operation_remaining()
        timeout = self._timeout if remaining is None else min(self._timeout, remaining)
        connection = self._connection_factory(self._host, timeout=timeout)
        response = None
        submitted = False
        deadline = None
        try:
            # Connection construction can itself take time. Recheck the absolute
            # operation deadline and stopped flag immediately before network I/O.
            self._check_cooldown()
            remaining = self._operation_remaining()
            if remaining is not None:
                timeout = min(timeout, remaining)
            connection.timeout = timeout
            deadline = _RequestDeadline(connection, timeout, threading.Timer)
            connection.connect()
            deadline.check()
            submitted = method == "POST"
            connection.request(method, target, body=body, headers=headers)
            deadline.check()
            response = connection.getresponse()
            if not 200 <= response.status < 300:
                status = response.status
                retry_after = _retry_after(response.headers)
                self._remember_cooldown(status, retry_after)
                provider_code = None
                # Provider messages may contain private data. Only these fixed
                # machine codes cross the error boundary, never message/body.
                deadline.check()
                error_body = response.read(8193)
                if len(error_body) <= 8192:
                    try:
                        parsed = json.loads(error_body)
                    except (ValueError, UnicodeError):
                        parsed = None
                    error = parsed.get("error") if isinstance(parsed, dict) else None
                    if (
                        isinstance(error, dict)
                        and isinstance(error.get("code"), str)
                        and error["code"] in _PROVIDER_CODES
                    ):
                        provider_code = error["code"]
                if status == 429:
                    code = "rate_limited"
                elif status in {401, 403}:
                    code = "unauthorized" if status == 401 else "forbidden"
                elif status == 409:
                    code = "conflict"
                elif 300 <= status < 400:
                    code = "redirect_rejected"
                elif status >= 500:
                    code = "provider_unavailable"
                else:
                    code = "provider_rejected"
                raise WhopError(
                    code,
                    status=status,
                    retry_after=retry_after,
                    ambiguous=submitted
                    and (
                        status >= 500
                        or 300 <= status < 400
                        or provider_code == "idempotency_request_in_progress"
                    ),
                    provider_code=provider_code,
                )
            data = bytearray()
            while True:
                deadline.check()
                chunk = response.read(min(8192, _MAX_RESPONSE + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > _MAX_RESPONSE:
                    _invalid(submitted)
            try:
                result = json.loads(data, parse_constant=lambda _value: _invalid(submitted))
            except (ValueError, UnicodeError):
                _invalid(submitted)
            if not isinstance(result, dict):
                _invalid(submitted)
            deadline.check()
            return result
        except WhopError:
            raise
        except (OSError, TimeoutError, http.client.HTTPException):
            raise WhopError("network_failure", ambiguous=submitted) from None
        finally:
            if deadline is not None:
                deadline.finish()
            if response is not None:
                with suppress(Exception):
                    response.close()
            with suppress(Exception):
                connection.close()

    def _account(self, value, *, required=True, ambiguous=False):
        account = value.get("account")
        if account is None and not required:
            return
        if not isinstance(account, dict) or account.get("id") != self.company_id:
            _invalid(ambiguous)

    def _membership(self, row, *, ambiguous=False):
        if not isinstance(row, dict):
            _invalid(ambiguous)
        self._account(row, ambiguous=ambiguous)
        try:
            _id(row.get("id"), "mem_")
        except ValueError:
            _invalid(ambiguous)
        if (
            row.get("product_id") not in self.product_ids
            or row.get("variant_id") not in self.variant_ids
        ):
            _invalid(ambiguous)
        product = row.get("product")
        if not isinstance(product, dict) or product.get("id") != row["product_id"]:
            _invalid(ambiguous)
        for key, prefix in (
            ("user_id", "user_"),
            ("member_id", "mber_"),
            ("promo_code_id", "promo_"),
        ):
            _optional_id(row.get(key), prefix, ambiguous=ambiguous)
        if not isinstance(row.get("status"), str) or not re.fullmatch(
            r"[a-z_]{1,40}", row["status"]
        ):
            _invalid(ambiguous)
        if type(row.get("cancel_at_period_end")) is not bool:
            _invalid(ambiguous)
        for field in ("initial_price", "renewal_price"):
            _money(row.get(field), ambiguous=ambiguous)
        for field in ("current_period_start", "current_period_end", "created_at", "updated_at"):
            try:
                _date(row.get(field))
            except ValueError:
                _invalid(ambiguous)
        return row

    def _payment(self, row):
        if not isinstance(row, dict) or row.get("account_id") != self.company_id:
            _invalid()
        try:
            _id(row.get("id"), "pay_")
        except ValueError:
            _invalid()
        if (
            row.get("product_id") not in self.product_ids
            or row.get("variant_id") not in self.variant_ids
        ):
            _invalid()
        for key, prefix in (
            ("membership_id", "mem_"),
            ("member_id", "mber_"),
            ("promo_code_id", "promo_"),
        ):
            _optional_id(row.get(key), prefix)
        user = row.get("user")
        if user is not None:
            if not isinstance(user, dict) or user.get("id") is None:
                _invalid()
            _optional_id(user.get("id"), "user_")
        if not isinstance(row.get("status"), str) or not re.fullmatch(
            r"[a-z_]{1,40}", row["status"]
        ):
            _invalid()
        reason = row.get("billing_reason")
        if reason is not None and (
            not isinstance(reason, str) or not re.fullmatch(r"[a-z_]{1,40}", reason)
        ):
            _invalid()
        for field in (
            "total",
            "subtotal",
            "refunded_amount",
            "presentment_total",
            "presentment_refunded_amount",
        ):
            _money(row.get(field))
        for field in ("paid_at", "created_at", "updated_at", "refunded_at", "dispute_created_at"):
            try:
                _date(row.get(field))
            except ValueError:
                _invalid()
        return row

    def _promo(self, row, *, account_required=True, ambiguous=False):
        if not isinstance(row, dict):
            _invalid(ambiguous)
        self._account(row, required=account_required, ambiguous=ambiguous)
        try:
            _id(row.get("id"), "promo_")
            _promo_code(row.get("code"))
        except ValueError:
            _invalid(ambiguous)
        product = row.get("product")
        if product is not None and (
            not isinstance(product, dict) or product.get("id") not in self.product_ids
        ):
            _invalid(ambiguous)
        amount = row.get("amount_off")
        if (
            isinstance(amount, bool)
            or not isinstance(amount, (int, float))
            or not math.isfinite(amount)
            or amount < 0
        ):
            _invalid(ambiguous)
        if row.get("promo_type") not in ("percentage", "flat_amount"):
            _invalid(ambiguous)
        if row["promo_type"] == "percentage" and amount > 1:
            _invalid(ambiguous)
        if row.get("duration") not in ("once", "repeating", "forever"):
            _invalid(ambiguous)
        if row.get("status") not in ("active", "inactive", "archived"):
            _invalid(ambiguous)
        duration = row.get("promo_duration_months")
        if duration is not None and (type(duration) is not int or not 0 <= duration <= 120):
            _invalid(ambiguous)
        for field in (
            "new_users_only",
            "existing_memberships_only",
            "one_per_customer",
            "unlimited_stock",
        ):
            if type(row.get(field)) is not bool:
                _invalid(ambiguous)
        return row

    def _pages(self, path, query, validator):
        seen, rows = set(), []
        for _ in range(_MAX_PAGES):
            self._operation_remaining()
            result = self._request("GET", path, query={**query, "first": _PAGE_SIZE})
            data, info = result.get("data"), result.get("page_info")
            if not isinstance(data, list) or len(data) > _PAGE_SIZE or not isinstance(info, dict):
                _invalid()
            if type(info.get("has_next_page")) is not bool:
                _invalid()
            rows.extend(validator(row) for row in data)
            self._operation_remaining()
            if not info["has_next_page"]:
                return rows
            cursor = info.get("end_cursor")
            if not isinstance(cursor, str) or not cursor or len(cursor) > 2048 or cursor in seen:
                _invalid()
            seen.add(cursor)
            query = {**query, "after": cursor}
        raise WhopError("pagination_limit")

    def _query(self):
        return {
            "account_id": self.company_id,
            "product_ids": self.product_ids,
            "variant_ids": self.variant_ids,
        }

    def list_memberships(self, discord_id=None):
        query = self._query()
        if discord_id is not None:
            if not isinstance(discord_id, str) or not re.fullmatch(r"[0-9]{17,20}", discord_id):
                raise ValueError("Invalid Discord identifier")
            query["external_account"] = "discord_" + discord_id
        return self._pages("/memberships", query, self._membership)

    def retrieve_membership(self, membership_id):
        _id(membership_id, "mem_")
        row = self._membership(self._request("GET", "/memberships/" + membership_id))
        if row["id"] != membership_id:
            _invalid()
        return row

    def list_payments(self, created_after=None):
        return self._pages(
            "/payments", {**self._query(), "created_after": _date(created_after)}, self._payment
        )

    def list_promos(self):
        return self._pages(
            "/promo_codes", self._query(), lambda row: self._promo(row, account_required=False)
        )

    def retrieve_promo(self, promo_id):
        _id(promo_id, "promo_")
        row = self._promo(self._request("GET", "/promo_codes/" + promo_id))
        if row["id"] != promo_id:
            _invalid()
        return row

    def create_promo(self, code, *, existing_memberships_only=False, idempotency_key):
        code = _promo_code(code)
        _intent(idempotency_key)
        if type(existing_memberships_only) is not bool:
            raise ValueError("Invalid referral reward type")
        payload = {
            "account_id": self.company_id,
            "amount_off": 30,
            "base_currency": "usd",
            "code": code,
            "new_users_only": not existing_memberships_only,
            "promo_duration_months": 1,
            "promo_type": "percentage",
            "one_per_customer": True,
            "existing_memberships_only": existing_memberships_only,
            "unlimited_stock": not existing_memberships_only,
            "variant_ids": list(self.variant_ids),
        }
        if len(self.product_ids) == 1:
            payload["product_id"] = self.product_ids[0]
        if existing_memberships_only:
            payload["stock"] = 1
        row = self._promo(
            self._request("POST", "/promo_codes", payload, idempotency_key=idempotency_key),
            ambiguous=True,
        )
        if (
            row.get("code") != code
            or row["amount_off"] != 0.30
            or row["duration"] != "once"
            or row.get("promo_duration_months") != 1
            or row.get("affiliate") is not None
            or row["new_users_only"] != payload["new_users_only"]
            or row["existing_memberships_only"] != existing_memberships_only
            or row["one_per_customer"] is not True
            or row["unlimited_stock"] != payload["unlimited_stock"]
            or row["status"] != "active"
            or (
                len(self.product_ids) == 1
                and (row.get("product") or {}).get("id") != self.product_ids[0]
            )
            or (
                existing_memberships_only
                and (type(row.get("stock")) is not int or row["stock"] != 1)
            )
        ):
            _invalid(True)
        return row

    def apply_promo(self, membership_id, code, *, idempotency_key):
        code = _promo_code(code)
        _intent(idempotency_key)
        existing = self.retrieve_membership(membership_id)
        if existing.get("promo_code_id") is not None:
            raise WhopError("promo_conflict", status=409)
        if existing["status"] not in {"active", "trialing"}:
            raise WhopError("membership_ineligible", status=409)
        row = self._membership(
            self._request(
                "POST",
                f"/memberships/{membership_id}/apply_promo_code",
                {"promo_code": code},
                idempotency_key=idempotency_key,
            ),
            ambiguous=True,
        )
        if row["id"] != membership_id or row.get("promo_code_id") is None:
            _invalid(True)
        return row
