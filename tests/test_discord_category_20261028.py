"""Observed product nouns beat incidental features without inventing taxonomy."""

import pytest
from discord_bridge.classification import classify_product
from discord_bridge.core import route_keys

from models import Item
from notifiers.discord import project_item


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        (
            "iGPSPORT VeRun GPS Sports Watch, 1.32'' AMOLED Display Running Training "
            "Metrics Plan Route Navigation ANT+ Bluetooth Gray",
            "electronics",
        ),
        ("GPS Watch with Route Navigation", "electronics"),
        (
            "Derrick Rose Graphic T Shirt Men Vintage Basketball Star Portrait Print "
            "Oversized Streetwear Hip Hop Casual Cotton Short Sleeve Tees Tops",
            "clothing-accessories",
        ),
        (
            'Soonaway S9Pro Electric Bike for Adults, 20"4" Fat Tire Off-Road Ebike, '
            "750W Rated, 25 mph, 48V20Ah, 65 mile, UL2849",
            "sports-office-travel",
        ),
        ("Electric Bicycle with 26 Inch Tires and Headlight", "sports-office-travel"),
        (
            "MATSKY 20x4.0 Fat Bike Tire, Folding All-Terrain Tire for E-Bikes "
            "and Mountain Bikes, 1 Pack",
            "sports-office-travel",
        ),
        (
            "Cruiser Bike Tire 2-Pack, 26 Inch Includes Inner Tubes and Tire Levers",
            "sports-office-travel",
        ),
        ("Bicycle Inner Tube for 700C Road Bike Tire", "sports-office-travel"),
        (
            "Treetopia Kennedy Fir Narrow 6.5 ft ColorFlex Artificial Christmas Tree",
            "home-storage-decor",
        ),
        (
            "Treetopia Kennedy Fir - Artificial Indoor Christmas Tree, Hinged Branches, "
            "Narrow Profile, Holiday Décor, Unlit, 6.5ft",
            "home-storage-decor",
        ),
        (
            "Treetopia Empire Pencil 9 ft ColorFlex Artificial Christmas Tree",
            "home-storage-decor",
        ),
        (
            "Little Grape Land Muslin Crib Sheets 2 Pack, 100% Cotton Fitted Baby "
            'Mattress Sheets for Boys & Girls, Standard Crib Size 52"x28", Flower',
            "home-storage-decor",
        ),
        (
            "Top Cotton Organic Cotton Crib Sheets Set of 2, Fitted Elastic Baby "
            'Mattress Sheet for Boys & Girls, 52" x 28"',
            "home-storage-decor",
        ),
        (
            "Minorsun Beach Towel Clips for Beach Chairs 16 Pack, Large Heavy-Duty "
            "Towel Holder Clips for Pool, Cruise, Laundry & Hanging",
            "home-storage-decor",
        ),
        (
            "Licensed Chuggington Kids Electric Train, 24V Ride On Train with Trailer, 3 Seats",
            "toys-baby-books",
        ),
        (
            "BEEPRINCESS Electric Christmas Tree Train Set, Elevated Around-the-Tree "
            "Train with Steam, Lights & Sounds, Ages 3+",
            "toys-baby-books",
        ),
    ],
)
def test_observed_product_purpose_wins_over_features_and_stale_hints(title, expected):
    for observed in (title, f"Blush Pink, Large, variant on {title}"):
        result = classify_product(observed, category_hint="pets", search_query="laptop")
        assert result.category == expected
        assert result.source == "title"
        assert result.matched_term


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Replacement Silicone Straps for GPS Sports Watch", "electronics"),
        ("Tire Inflator for Electric Bike and Car Tires", "tools-auto"),
        ("Replacement Car Tire, All-Season 18 Inch", "tools-auto"),
        ("Women's Christmas Tree Graphic T-Shirt", "clothing-accessories"),
        ("Women's Artificial Christmas Tree Graphic T-Shirt", "clothing-accessories"),
        ("Artificial Christmas Tree Graphic T-Shirt", "clothing-accessories"),
        ("Sports Watch Graphic T-Shirt for Men", "clothing-accessories"),
        ("Unisex Electric Bike Graphic Tee", "clothing-accessories"),
        ("Graphic T-Shirt with Artificial Christmas Tree Print", "clothing-accessories"),
        ("Women's Artificial Christmas Tree Graphic Hoodie", "clothing-accessories"),
        ("Sports Watch Graphic Sweatshirt for Men", "clothing-accessories"),
        ("Unisex Electric Bike Graphic Hoodie", "clothing-accessories"),
        ("Clear Clothes Storage Bags for Graphic T-Shirts", "home-storage-decor"),
        (
            "Shadow Box Picture Frame for Graphic T-Shirts and Jerseys",
            "home-storage-decor",
        ),
        (
            "Gift for Men: Clear Clothes Storage Bags for Graphic T-Shirts",
            "home-storage-decor",
        ),
        (
            "Articulated Action Figure Toy with Decorative Statue Display Base",
            "toys-baby-books",
        ),
        ("Office Chair with Decorative Figurine Armrests", "furniture"),
        ("Office Chair with Figurine Sculpture Armrests", "furniture"),
        ("Action Figure with Graphic T-Shirt for Men", "toys-baby-books"),
        ("Action Figure Wearing Graphic T-Shirt", "toys-baby-books"),
        ("Action Figure Featuring Graphic T-Shirt", "toys-baby-books"),
        ("Graphic Cotton T-Shirt for Dogs", "pets"),
        ("Graphic Sweatshirt for Dogs", "pets"),
        ("Christmas Tree Cat Scratching Tower with Fleece Base", "pets"),
        ("Dollhouse Artificial Christmas Tree Toy", "toys-baby-books"),
        ("Animal Figurines Farm Animals Toys Educational Playset", "toys-baby-books"),
        ("Toy Figurine Sculpture Set for Kids", "toys-baby-books"),
        ("Retractable Kitchen Towel Clip Holder", "kitchen-appliances"),
        ("Solid Wood Nightstand with Shelf", "furniture"),
        ("Upholstered Dining Chairs", "furniture"),
        ("Ride On Lawn Mower", "tools-auto"),
        ("Wooden Toy Train", "toys-baby-books"),
        ("Train Model X100", "other"),
        ("Unknown Model X100", "other"),
    ],
)
def test_accessories_and_other_product_purposes_keep_their_supported_routes(title, expected):
    assert classify_product(title, search_query="laptop").category == expected


def test_new_query_vocabulary_cannot_classify_an_unrecognized_product():
    assert (
        classify_product(
            "Unknown Model X100", search_query="sports watch artificial christmas tree"
        ).category
        == "other"
    )


@pytest.mark.parametrize(
    ("title", "category"),
    [
        ("GPS Sports Watch with AMOLED Display", "electronics"),
        ("Electric Bike with Fat Tire", "sports-office-travel"),
        ("Pencil Artificial Christmas Tree", "home-storage-decor"),
        ("Kids Electric Train, Ride On Train with Trailer", "toys-baby-books"),
        ("Sports Watch Graphic Sweatshirt, Gift for Men", "clothing-accessories"),
        ("Unisex Electric Bike Graphic Hoodie, Birthday Gift for Dad", "clothing-accessories"),
        ("Gift for Men: Sports Watch Graphic Sweatshirt", "clothing-accessories"),
    ],
)
def test_product_projection_selects_one_existing_category_route(title, category):
    item = project_item(
        Item.from_dict(
            {
                "item_id": "ip-1234567",
                "title": title,
                "value_usd": 25,
                "category": "pets",
                "raw": {"query": "laptop"},
            }
        )
    )
    assert item["category"] == category
    routes = route_keys(item)
    assert [route for route in routes if route.startswith("type-")] == [f"type-{category}"]
