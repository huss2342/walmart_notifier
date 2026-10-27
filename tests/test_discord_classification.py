"""Department routing uses product evidence, not a sweep's wish list."""

from dataclasses import FrozenInstanceError

import pytest
from discord_bridge.classification import Classification, classify_product
from discord_bridge.core import CATEGORIES


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Modern Upholstered Sofa, Three Seat Couch", "furniture"),
        ("Queen Metal Bed Frame with Storage Headboard", "furniture"),
        ("Ergonomic Mesh Office Chair", "furniture"),
        ("Vanity Desk with LED Mirror and Drawers", "furniture"),
        ("6 Shelf Bookcase in Oak Finish", "furniture"),
        (
            "Throw Pillow Covers 18×18 2 Pack Striped Corduroy Decorative for Couch",
            "home-storage-decor",
        ),
        ("Queen Bed Sheets and Pillowcases, 4 Piece Set", "home-storage-decor"),
        ("Waterproof Mattress Protector for Queen Bed", "home-storage-decor"),
        (
            "Dademeo Cute Cat Door Frame Corner Decoration, Wooden Cat Ornament",
            "home-storage-decor",
        ),
        (
            "Dademeo Christmas Bell Hanging Ornament, Festive Plastic Hanging Decor",
            "home-storage-decor",
        ),
        ("Cat Graphic Cotton T-Shirt for Women", "clothing-accessories"),
        ("Reppunk Simple Solid Loose Men's Jeans Straight Trousers", "clothing-accessories"),
        ("High Waisted Yoga Pants with Pockets", "clothing-accessories"),
        ("Sterling Silver Heart Necklace", "clothing-accessories"),
        ("Lightweight Running Shoes Size 9", "clothing-accessories"),
        ("Digital Air Fryer with Nonstick Basket", "kitchen-appliances"),
        ("Ninja Blender Replacement Gasket and Blade", "kitchen-appliances"),
        ("Stainless Steel Electric Kettle", "kitchen-appliances"),
        ("Dishwasher Safe Dinner Plates Set of 6", "kitchen-appliances"),
        ("Cordless Stick Vacuum Cleaner", "kitchen-appliances"),
        ("Bluetooth Wireless Headphones with Microphone", "electronics"),
        ("Clear Shockproof Phone Case for iPhone 17", "electronics"),
        ("Waterproof Shower Speaker with Bluetooth", "electronics"),
        ("Wireless Gaming Mouse and Keyboard", "electronics"),
        ("Portable Power Station for Camping", "electronics"),
        ("Cordless Drill Driver and Battery Kit", "tools-auto"),
        ("Universal Car Seat Covers, Front Pair", "tools-auto"),
        ("Garden Hose with Spray Nozzle", "tools-auto"),
        ("3 Piece Paint Brush Set", "tools-auto"),
        ("Hydrating Facial Serum with Hyaluronic Acid", "beauty-health"),
        ("Electric Toothbrush with Travel Case", "beauty-health"),
        ("Makeup Brush Set with Storage Pouch", "beauty-health"),
        ("Adult Incontinence Diapers, 20 Count", "beauty-health"),
        ("Five Minute Dinosaur Stories Audio Card for Use with Yoto Player", "toys-baby-books"),
        ("Miniature Dollhouse Furniture Sofa and Table", "toys-baby-books"),
        ("Baby High Chair with Adjustable Tray", "toys-baby-books"),
        ("Convertible Baby Car Seat", "toys-baby-books"),
        ("Disposable Baby Diapers, Size 3", "toys-baby-books"),
        ("Pet Dog Bed with Washable Cover", "pets"),
        ("Cat Toy Mouse with Feathers", "pets"),
        ("Automatic Pet Food Feeder for Cats", "pets"),
        ("Pet Grooming Brush for Dogs and Cats", "pets"),
        ("Yoga Mat with Carrying Strap", "sports-office-travel"),
        ("Camping Folding Chair with Cup Holder", "sports-office-travel"),
        ("Hard Shell Spinner Luggage Set", "sports-office-travel"),
        ("Retractable Ballpoint Pens, 12 Pack", "sports-office-travel"),
        ("Adjustable Dumbbell Weight Set", "sports-office-travel"),
        ("LED Floor Lamp with USB Charging Station", "home-storage-decor"),
        ("Toilet Brush and Holder Set", "home-storage-decor"),
        ("Vintage Jewelry Box with Mirror", "home-storage-decor"),
    ],
)
def test_representative_products_and_accessories_route_to_their_department(title, expected):
    result = classify_product(title)
    assert result.category == expected
    assert result.source == "title"
    assert result.matched_term


@pytest.mark.parametrize(
    "title",
    ["Category selection form", "Catering Accessory", "Unknown Item", "Chicken Soup 12 Pack", ""],
)
def test_unknown_products_and_substrings_do_not_invent_a_department(title):
    assert classify_product(title, search_query="cat electronics furniture") == Classification(
        category="other", source="fallback", matched_term=""
    )


@pytest.mark.parametrize("badge", ["New Arrival", "Best Seller", "Sampling", "Review Product"])
def test_page_badges_are_not_department_hints(badge):
    assert classify_product("Unknown Item", category_hint=badge).category == "other"


@pytest.mark.parametrize("category", CATEGORIES)
def test_explicit_known_category_hint_is_recognized(category):
    result = classify_product("Unidentified Model 100", category_hint=category)
    assert result.category == category
    assert result.source == ("fallback" if category == "other" else "category_hint")


def test_department_breadcrumbs_can_fill_in_an_unrecognized_title():
    result = classify_product(
        "Model X100, Blue", category_hint="Electronics > Computers > Accessories"
    )
    assert result.category == "electronics"
    assert result.source == "category_hint"
    assert result.matched_term == "computers"


def test_title_evidence_beats_a_stale_hint_and_an_unrelated_sweep_query():
    result = classify_product(
        "Modern Upholstered Sofa", category_hint="electronics", search_query="laptop"
    )
    assert result.category == "furniture"
    assert result.source == "title"


def test_query_only_breaks_a_tie_between_departments_supported_by_the_title():
    # Both products are genuinely present; the sweep may resolve this tie, but
    # may never turn an unrelated product into an electronic item.
    result = classify_product("Notebook and Storybook Bundle", search_query="office")
    assert result.category == "sports-office-travel"
    assert result.source == "search_query"
    assert result.matched_term == "notebook"
    assert (
        classify_product("Notebook and Storybook Bundle", search_query="laptop").category
        == "toys-baby-books"
    )
    assert classify_product("Chicken Soup", search_query="laptop").category == "other"


def test_normalization_handles_case_punctuation_and_unicode_without_mutable_results():
    result = classify_product("  STAINLESS-STEEL Electric KETTLE™  ")
    assert result.category == "kitchen-appliances"
    assert result.matched_term == "electric kettle"
    with pytest.raises(FrozenInstanceError):
        result.category = "other"


@pytest.mark.parametrize(
    "title",
    [
        "JC Press On Nails Kit, Durable, Easy Application, Short Length Nail Press Ons, "
        "Handmade, Stylish Impress, Red (S10 M)",
        "Nail Press Ons, Short Oval Shape, 24 Pack",
        "Reusable Press-On Nails in Red",
        "Nail Kit with Manicure Accessories",
        "Glossy Nail Polish Kit, Red",
    ],
)
def test_cosmetic_nail_phrases_are_recognized_without_using_a_raw_nail_keyword(title):
    assert classify_product(title).category == "beauty-health"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Steel Nails for Woodworking, 200 Piece", "tools-auto"),
        ("Brad Nail Kit for Nail Gun, Galvanized Steel", "tools-auto"),
        ("Framing Nails Hardware Kit", "tools-auto"),
        ("Nails 100 Count", "other"),
        ("Nailhead Trim Upholstered Sofa", "furniture"),
        ("Cat Category Index", "other"),
        ("Chicken Soup 12 Pack", "other"),
        ("GymGum Energy Caffeine Gum Sugar-Free Spearmint", "other"),
    ],
)
def test_construction_nails_and_legitimate_unknowns_do_not_become_cosmetics(title, expected):
    assert classify_product(title, search_query="beauty nails").category == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("TICONN Heat Shrink Butt Connectors Wire Connector 200 Piece", "tools-auto"),
        ("TICONN 60pc Heat Shrink Butt Wire Connectors, Waterproof Crimp Terminals", "tools-auto"),
        (
            "Trailmaker Ski Mask Balaclava Skull Cap Face Covering for Winter",
            "clothing-accessories",
        ),
        ("Silicone Gear Shift Knob Cover, for Accord 2018-2026", "tools-auto"),
        ("PUREFLOW HEPA Cabin Air Filter for Ford Fusion", "tools-auto"),
        ("Season Time Pedal Covers for Tesla Model 3/Y", "tools-auto"),
        ("Nichiyo Epoxy Floor Squeegee, Rubber Squeegee Trowel for Concrete", "tools-auto"),
        ("Women's Probiotic Dietary Supplement Vegetarian Capsules", "beauty-health"),
        ("925 Silver Hypoallergenic Earring Backs Replacements", "clothing-accessories"),
        ("Hanging Toiletry Bag for Women/Men, Travel Makeup Organizer", "beauty-health"),
        ("Keeygo Reusable Waterproof Shower Cap", "beauty-health"),
        ("Polyhedral DND Dice Set for Dungeons and Dragons Tabletop Games", "toys-baby-books"),
        ("17 oz Glass Cocktail Shaker Set with Built-in Strainer", "kitchen-appliances"),
    ],
)
def test_obvious_product_phrases_seen_in_the_first_thirty_live_unknowns(title, expected):
    result = classify_product(title)
    assert result.category == expected
    assert result.source == "title"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        (
            "Elegant Choise All-Weather Air Conditioner Cover - Durable & Easy To Install "
            "Outdoor Protector",
            "home-storage-decor",
        ),
        ("Window Air Conditioner, 6000 BTU", "kitchen-appliances"),
        ("Moisturizing Hair Conditioner for Dry Hair", "beauty-health"),
        (
            "Blush Pink, variant on KAMBERFT Men’s Black Pre-tied Bow Tie Set, 4PCS Gift Set "
            "with Pocket Square, Cufflinks & Lapel Pin",
            "clothing-accessories",
        ),
        ("Silk Bowtie for Men", "clothing-accessories"),
        ("Silver Cufflinks Gift Set", "clothing-accessories"),
        ("Blush Pink, variant on Unknown Model X100", "other"),
        ("Blush Pink, Large, variant on Solid Wood Dining Table", "furniture"),
        ("Cat, variant on Wireless Headphones", "electronics"),
        ("Blush Pink Makeup Blush Compact", "beauty-health"),
        ("LED Book Reading Light with Flexible Neck", "home-storage-decor"),
        ("Clip-On Booklight for Reading in Bed", "home-storage-decor"),
        ("Children's Dinosaur Story Book", "toys-baby-books"),
        ("Silicone Chair Leg Floor Protectors, 16 Pack", "home-storage-decor"),
        ("Upholstered Dining Chair with Wood Legs", "furniture"),
    ],
)
def test_variant_prefixes_and_accessory_phrases_do_not_override_the_main_product(title, expected):
    assert classify_product(title).category == expected
