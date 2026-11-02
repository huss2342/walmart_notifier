"""Canonical product feeds, price boundaries and intentional release timing.

Discord provisioning and observation routing consume the same definitions. This
module has no storage, transport or guild-specific state.
"""

# Starter's historical price remains for compatibility with old queue records.
PLAN_PRICES_CENTS = {"starter": 500, "pro": 3000}
RETIRED_ROUTE_KEYS = ("starter-items",)
STARTER_MAX_PRICE_CENTS = 4000  # Historical ceiling; this route is retired.
STARTER_DELAY_SECONDS = 300
FREE_DELAY_SECONDS = 86400
FREE_ROUTE_KEY = "yesterday-recap"
JOB_LIFETIME_SECONDS = 1800
FREE_JOB_LIFETIME_SECONDS = 7 * 86400

PRODUCT_CATEGORY_TOPICS = (
    ("furniture", "Furniture alerts. Immediate after detection."),
    ("home-storage-decor", "Home, storage, and decor alerts. Immediate after detection."),
    ("kitchen-appliances", "Kitchen and appliance alerts. Immediate after detection."),
    ("electronics", "Electronics alerts. Immediate after detection."),
    ("tools-auto", "Tools and automotive alerts. Immediate after detection."),
    ("beauty-health", "Beauty and health alerts. Immediate after detection."),
    ("clothing-accessories", "Clothing and accessories alerts. Immediate after detection."),
    ("toys-baby-books", "Toys, baby products, and books. Immediate after detection."),
    ("pets", "Pet supplies. Immediate after detection."),
    ("sports-office-travel", "Sports, office, and travel products. Immediate after detection."),
    (
        "other",
        "Other or unclassified items. Immediate after detection; classifications may be corrected.",
    ),
)
PRODUCT_CATEGORIES = tuple(key for key, _ in PRODUCT_CATEGORY_TOPICS)
PRICE_BANDS = (
    (1000, "under-10"),
    (2000, "10-20"),
    (4000, "20-40"),
    (8000, "40-80"),
    (20000, "80-200"),
)
TOP_PRICE_BAND = "200-plus"


def price_feed_topics():
    """Yield existing feed keys/topics directly from exclusive cent ceilings."""
    lower = 0
    for upper, key in PRICE_BANDS:
        description = (
            f"Observed value from ${lower / 100:g} to below ${upper / 100:g}."
            if lower
            else f"Observed value below ${upper / 100:g}."
        )
        yield key, description + " Immediate after detection."
        lower = upper
    yield TOP_PRICE_BAND, f"Observed value ${lower / 100:g} and above. Immediate after detection."
