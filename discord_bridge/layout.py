"""Private-server layout and least-privilege Discord permission policy.

Prices are monthly USD. The retired Starter binding is retained privately so
existing delivery records and the ownership audit remain valid.
"""

from __future__ import annotations

from dataclasses import dataclass

from .policy import FREE_DELAY_SECONDS as FREE_DELAY_SECONDS
from .policy import FREE_ROUTE_KEY as FREE_ROUTE_KEY
from .policy import PLAN_PRICES_CENTS as PLAN_PRICES_CENTS
from .policy import (
    PRODUCT_CATEGORY_TOPICS,
    price_feed_topics,
)
from .policy import STARTER_DELAY_SECONDS as STARTER_DELAY_SECONDS
from .policy import STARTER_MAX_PRICE_CENTS as STARTER_MAX_PRICE_CENTS

CREATE_INVITE = 1 << 0
ADMINISTRATOR = 1 << 3
MANAGE_CHANNELS = 1 << 4
MANAGE_GUILD = 1 << 5
ADD_REACTIONS = 1 << 6
VIEW_CHANNEL = 1 << 10
SEND_MESSAGES = 1 << 11
SEND_TTS_MESSAGES = 1 << 12
MANAGE_MESSAGES = 1 << 13
EMBED_LINKS = 1 << 14
ATTACH_FILES = 1 << 15
READ_MESSAGE_HISTORY = 1 << 16
MENTION_EVERYONE = 1 << 17
MANAGE_ROLES = 1 << 28
MANAGE_WEBHOOKS = 1 << 29
USE_APPLICATION_COMMANDS = 1 << 31
MANAGE_THREADS = 1 << 34
CREATE_PUBLIC_THREADS = 1 << 35
CREATE_PRIVATE_THREADS = 1 << 36
SEND_MESSAGES_IN_THREADS = 1 << 38
SEND_VOICE_MESSAGES = 1 << 46
SEND_POLLS = 1 << 49
USE_EXTERNAL_APPS = 1 << 50
PIN_MESSAGES = 1 << 51
BYPASS_SLOWMODE = 1 << 52

READER_PERMISSIONS = VIEW_CHANNEL | READ_MESSAGE_HISTORY
BOT_RUNTIME_PERMISSIONS = READER_PERMISSIONS | SEND_MESSAGES | EMBED_LINKS
SETUP_PERMISSIONS = MANAGE_CHANNELS | MANAGE_GUILD | MANAGE_ROLES
PROVISION_PERMISSIONS = SETUP_PERMISSIONS | BOT_RUNTIME_PERMISSIONS
# Discord only accepts overwrite bits the creating bot holds. Manage Roles in
# an overwrite additionally requires Administrator, which we never request.
# Other member actions stay unavailable through zero base permissions and the
# strict guild-role audit; channel rules only need these posting restrictions.
MEMBER_ACTION_DENY = SEND_MESSAGES | EMBED_LINKS

ROLE_NAMES = {"starter": "Starter", "pro": "Pro"}
OPTIONAL_ROLE_NAMES = {"trial": "Pro Trial"}
CHANNEL_DISPLAY_NAMES = {
    "welcome": "💳-welcome",
    "how-it-works": "how-to-use",
    "feed-status": "updates",
    "price-under-10": "💲0-10",
    "price-10-20": "💲10-20",
    "price-20-40": "💲20-40",
    "price-40-80": "💲40-80",
    "price-80-200": "💲80-200",
    "price-200-plus": "💲200-plus",
    FREE_ROUTE_KEY: "🆓24h-delayed-alerts",
    "starter-items": "retired-starter-feed",
}


@dataclass(frozen=True)
class Category:
    key: str
    name: str
    audience: str  # everyone, starter (also Pro), pro, or operations
    community: bool = False


@dataclass(frozen=True)
class Channel:
    key: str
    category: str
    topic: str

    @property
    def name(self) -> str:
        if self.category == "pro-type":
            return self.key.removeprefix("type-")
        return CHANNEL_DISPLAY_NAMES.get(self.key, self.key)


CATEGORIES = (
    Category("public-info", "START HERE", "everyone"),
    Category("community", "COMMUNITY", "everyone", community=True),
    Category("starter", "RETIRED FEED", "operations"),
    Category("pro-price", "PRO - BY VALUE", "pro"),
    Category("pro-type", "PRO - BY CATEGORY", "pro"),
    Category("pro-firehose", "PRO - ALL ITEMS", "pro"),
    Category("operations", "PRIVATE OPERATIONS", "operations"),
)

CHANNELS = (
    Channel(
        "welcome",
        "public-info",
        "Free: historical alerts after 24 hours. Pro: $30/month for all values, "
        "category feeds and personal item requests. Subscribe using the pinned Whop link.",
    ),
    Channel(
        "greeting",
        "public-info",
        "Personal greetings for new members. Only the joining member is mentioned. "
        "Choose your plan in #welcome; notification preferences remain under your control.",
    ),
    Channel(
        "how-it-works",
        "public-info",
        "Choose your feeds and notifications, open Walmart links, and get help. "
        "Use your own eligible account; availability and claims are never guaranteed.",
    ),
    Channel(
        "feed-status",
        "public-info",
        "Announcements, service issues and resolved updates from the team. Members can read; "
        "the server owner can post updates directly.",
    ),
    Channel(
        "plans-and-help",
        "operations",
        "Free: historical alerts after 24 hours. "
        f"Pro: ${PLAN_PRICES_CENTS['pro'] / 100:g}/month, "
        "all observed values, category feeds and personal item requests. "
        "Subscribe, manage or cancel through Whop. Starter is retired.",
    ),
    Channel(
        "yesterday-recap",
        "public-info",
        f"Free: every detected item, released at least {FREE_DELAY_SECONDS / 3600:g} hours "
        "after detection. "
        "Historical listings; current availability is not verified.",
    ),
    Channel(
        "general",
        "community",
        "Community chat with 10-second slowmode. No uploads, GIFs or links. "
        "Never share account credentials or personal information.",
    ),
    Channel(
        "feature-requests",
        "community",
        "Feature requests with 10-second slowmode. No uploads, GIFs or links. "
        "Describe your request without account credentials or personal information.",
    ),
    Channel(
        "starter-items",
        "starter",
        "Retired Starter feed, retained privately for existing delivery records. "
        "Current Free and Pro options are in #welcome.",
    ),
    *(Channel(f"price-{key}", "pro-price", topic) for key, topic in price_feed_topics()),
    *(Channel(f"type-{key}", "pro-type", topic) for key, topic in PRODUCT_CATEGORY_TOPICS),
    Channel(
        "pro-all",
        "pro-firehose",
        "All detected items. Also appears in a value/category channel; "
        "mute this feed if you prefer those views.",
    ),
    Channel(
        "collector-health",
        "operations",
        "Private collector health. Never post credentials, account-specific claim counts, "
        "or private browsing data.",
    ),
    Channel(
        "delivery-errors",
        "operations",
        "Private delivery failures. Sanitized diagnostic summaries only.",
    ),
    Channel(
        "classification-review", "operations", "Private review of uncertain item classifications."
    ),
    Channel(
        "audit",
        "operations",
        "Private access and configuration audit summaries. "
        "Never post secrets or payment personal data.",
    ),
)

# Optional features are bound explicitly so existing installations keep working
# while their owner prepares the new channel and runtime.
OPTIONAL_CHANNELS = (
    Channel(
        "item-requests",
        "pro-firehose",
        "Pro personal item requests. Use /request add, list, edit, delete or test-dm. "
        "Matches are sent only by DM. Enable server DMs; ten requests per member.",
    ),
    Channel(
        "trial-management", "operations",
        "Owner-only Pro trial controls. Use /trial to grant, extend, revoke or list access, "
        "and enable or disable the automatic welcome trial.",
    ),
)


def permission_overwrites(
    guild_id: str,
    bot_user_id: str,
    roles: dict[str, str],
    category: Category,
    *,
    application_commands: bool = False,
) -> list[dict]:
    """Explicit ACLs avoid role-combination leaks and public creation windows."""
    public = category.audience == "everyone"
    if application_commands and category.audience not in {"pro", "operations"}:
        raise ValueError("Commands require a Pro-only or owner-only channel")
    chat = public and category.community
    reader_allow = (READER_PERMISSIONS | (SEND_MESSAGES if chat else 0)) if public else 0
    member_deny = (EMBED_LINKS if chat else MEMBER_ACTION_DENY) | (0 if public else VIEW_CHANNEL)
    if application_commands:
        member_deny |= USE_APPLICATION_COMMANDS
    result = [{"id": guild_id, "type": 0, "allow": str(reader_allow), "deny": str(member_deny)}]
    audience_roles = ("starter", "pro") + (("trial",) if "trial" in roles else ())
    for role in audience_roles:
        allowed = category.audience == "starter" or (
            category.audience == "pro" and role in {"pro", "trial"}
        )
        if allowed:
            role_allow = READER_PERMISSIONS
            if application_commands and role in {"pro", "trial"}:
                # Discord requires Send Messages even for private slash replies.
                role_allow |= USE_APPLICATION_COMMANDS | SEND_MESSAGES
            result.append({"id": roles[role], "type": 0, "allow": str(role_allow), "deny": "0"})
    result.append(
        {"id": bot_user_id, "type": 1, "allow": str(BOT_RUNTIME_PERMISSIONS), "deny": "0"}
    )
    return result


def server_manifest() -> dict:
    """Return a credential-free, JSON-serializable setup preview."""
    categories = {category.key: category for category in CATEGORIES}
    return {
        "schema_version": 1,
        "plans": {
            "free": {
                "monthly_price_usd": 0,
                "delay_seconds": FREE_DELAY_SECONDS,
                "route": FREE_ROUTE_KEY,
                "historical": True,
            },
            "pro": {
                "role": ROLE_NAMES["pro"],
                "monthly_price_usd": PLAN_PRICES_CENTS["pro"] / 100,
                "monthly_price_cents": PLAN_PRICES_CENTS["pro"],
                "delay_seconds": 0,
                "max_price_cents_exclusive": None,
            },
        },
        "categories": [
            {
                "key": category.key,
                "name": category.name,
                "audience": category.audience,
                "bot_only_posting": not category.community,
            }
            for category in CATEGORIES
        ],
        "channels": [
            {
                "key": channel.key,
                "name": channel.name,
                "category": channel.category,
                "topic": channel.topic,
                "audience": categories[channel.category].audience,
                "bot_only_posting": not categories[channel.category].community,
            }
            for channel in CHANNELS
        ],
        "optional_channels": [
            {
                "key": channel.key,
                "name": channel.name,
                "category": channel.category,
                "topic": channel.topic,
                "audience": "pro",
                "bot_only_posting": True,
                "application_commands": True,
            }
            for channel in OPTIONAL_CHANNELS
        ],
        "security": {
            "everyone_base_permissions": "0",
            "paid_role_base_permissions": "0",
            "bot_runtime_permissions": str(BOT_RUNTIME_PERMISSIONS),
            "temporary_setup_permissions": str(PROVISION_PERMISSIONS),
            "temporary_management_permissions": str(SETUP_PERMISSIONS),
            "administrator_required": False,
            "invite_creation": "owner and explicitly bound membership bot only",
            "discovery": "disabled; owner must disable before setup",
            "widget": "disabled",
            "verification_level_minimum": 2,
            "explicit_content_filter_minimum": 2,
            "default_notifications": "mentions only",
            "untracked_resource_policy": "refuse automatic adoption; delete nothing",
            "free_live_delivery": False,
            "billing_enabled": False,
        },
    }
