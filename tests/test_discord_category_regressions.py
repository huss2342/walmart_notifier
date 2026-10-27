"""Observed catalogue misses and ambiguous products must reach the right feed."""

import pytest
from discord_bridge.classification import classify_product
from discord_bridge.core import JobStore, route_keys
from discord_bridge.layout import server_manifest

from models import Item
from notifiers.discord import project_item


@pytest.mark.parametrize(
    "title",
    [
        "Fnnxeal Mens Sweatpants, Drawstring Casual Slacks with Stretch Fall Winter Fleece",
        "WHOINSHOP Women Christmas Holiday Colorblock Sweatshirt Polka Dot Mock Neck",
        "Frnrwgo Boys Quilted Padded Vest Zip Up Stand Collar Lightweight Winter Gilet",
        "Akmoler Baby Boys and Girls Sleeveless Romper, Solid Color Tank One-Piece Outfit",
        "HAHASHOW Infant Toddler Animal Flannel Hooded Romper Jumpsuit One-Piece Onesie",
        "VNJQPG Women's Family Christmas V-Neck Tee Holiday Reunion Top",
        "Merokeety Womens Summer Lightweight Cardigan Button Beach Cover Up Tops",
        "KAMBERFT Men's Heavy Duty Hidden Suspenders for Under Clothes",
        "Oversized Aviator Sunglasses for Women Men Retro 70s Flat Top Shades",
        "FURTALK Baby Sun Hat UPF 50+ Cotton Toddler Hats for Boys Girls",
        "Cooraby 3 Pairs Kid's Winter Gloves Thick Warm Knitted Gloves",
        "Sentuca Girls Fleece Pullover with Pockets",
        "Women's Sleeveless Openwork Knit Top Lightweight Beach Cover-Up",
        "Grey's Anatomy Women's V-Neck Scrub Top with Short Sleeves and Multi-Pockets",
        "alpinario Men's Sleeveless Tank Top Lightweight Quick Dry Running Tee",
        "Amazhiyu Womens Linen Camisole V-Neck Sleeveless Casual Summer Cami",
        "RELLECIGA Womens Bathing suits V Neck One Piece Swimsuits",
        "Toddler Girls Rash Guard Sets with Bikini Bottoms Swimwear",
        "PIFLCAM Women's Lace Stockings Tights Nylon Pantyhose",
        "Fnnxeal Mens Casual Joggers",
        "Amazhiyu Women's High Waist Elastic Sweatshorts Comfy Lounge Bottom",
        "Girls Corduroy Overalls with Pockets",
        "Men's Cotton Polo, Navy",
        "Women's Leather Loafers",
        "Women's Cotton Sleepwear Nightgown",
        "Dqueduo Women's Casual Hooded Sweatshirts 2026, Solid Color Long Sleeve Loose "
        "Pullover Tops with Pet Carrier Pocket, Fashion Sweatshirts for Everyday Wear",
        "Women's Hoodie with Pet Carrier Pouch for Cats",
        "6 Tie Dye Elastic Headbands for Girls, No Slip Rainbow Hair Bands",
        'Womens Adult "Spooky Season" Ghost Cat T Shirt Black White Leopard Bow Pumpkin Graphic',
        'Women\'s "Catnip Dealer" Graphic Sweatshirt',
        "Black Cat T Shirt Halloween Graphic for Adults",
        "Funny Dog Shirt for Dog Mom",
    ],
)
def test_missing_clothing_vocabulary_and_pet_pockets(title):
    for observed in (title, f"Blush Pink, Large, variant on {title}"):
        result = classify_product(observed, category_hint="pets", search_query="dog")
        assert result.category == "clothing-accessories"
        assert result.source == "title"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Dog Sweater Warm Knitted Winter Clothes for Small Dogs", "pets"),
        ("Cat Hoodie with Warm Fleece Lining", "pets"),
        ("Pet Raincoat Waterproof Puppy Jacket", "pets"),
        ("Christmas Shirts for Dogs and Cats", "pets"),
        ("Warm Fleece Sweater for Small Dogs", "pets"),
        ("Dog Winter Hat with Ear Holes", "pets"),
        ("Dog Rear Leg Sleeve to Stop Licking", "pets"),
        ("Pet Hair Removal Gloves for Dogs and Cats", "pets"),
        ("Washable Puppy Pee Pads for Dogs and Cats", "pets"),
        ("Raised Inflatable Dog Donut Cone Collar to Stop Licking", "pets"),
        ("Pet Carrier with Extra Pet Carrier Pocket", "pets"),
        ("Cat Graphic Cotton Sweatshirt for Women", "clothing-accessories"),
        ("Women's Cat Ear Hooded Jumpsuit", "clothing-accessories"),
        ("Pet Carrier Backpack with Shoulder Straps", "pets"),
        ("Dog Bed with Washable Blanket", "pets"),
        ("Vagascape Art Cat House with Cat Scratcher, Cardboard Cat Bed, Home Decoration", "pets"),
        (
            "BSJJY Christmas Acrylic Ornament F, Cat Tree Hole Hanging Decoration",
            "home-storage-decor",
        ),
        (
            "Squirrel Metal Sign with Bird Feeder is Empty Message, Rustic Wall Decor",
            "home-storage-decor",
        ),
        (
            "Halloween Pumpkin Porch Goose Outfit with Matching Hat for Garden Statue",
            "home-storage-decor",
        ),
        ("Wall Mount Baseball Hat Rack for Closet, Cap Organizer Holder", "home-storage-decor"),
        ("3-Tier Foldable Clothes Drying Rack, Portable Clothing Rack", "home-storage-decor"),
        ("Silicone Jar Lids, Reusable Bottle Caps", "kitchen-appliances"),
        ("Food Grade Disposable Nitrile Gloves for Cooking", "kitchen-appliances"),
        ("Heat Resistant Oven Gloves for Baking", "kitchen-appliances"),
        ("Grill Gloves Powered by Scrub Daddy", "kitchen-appliances"),
        ("Nitrile Exam Gloves, Latex Free", "beauty-health"),
        ("Exfoliating Bath Gloves, Shower Scrub", "beauty-health"),
        ("Compression Arthritis Gloves for Men & Women", "beauty-health"),
        ("Pototlls Work Gloves for Men Women, Gardening Auto Repair DIY", "tools-auto"),
        ("Cestus Gardening Gloves for Women", "tools-auto"),
        ("Boxing Gloves for Kickboxing and Martial Arts", "sports-office-travel"),
        ("Paddle Pals Toddler Swim Vest for Swim Lessons", "sports-office-travel"),
        ("Adjustable Weighted Vest for Workout Training", "sports-office-travel"),
        ("Glossy Nail Polish Top Coat", "beauty-health"),
        ("Aircraft Engine Fuel Filter", "tools-auto"),
        ("HEPA Air Purifier Replacement Filters", "kitchen-appliances"),
        ("Center Console Cover for Honda Pilot Leather Armrest", "tools-auto"),
        ("Car Retractable USB-C Hub Fast Charger for Tesla", "tools-auto"),
        ("Parchment Paper Circles, 25 Pack", "kitchen-appliances"),
        ("Stainless Steel Cookie Press for Baking", "kitchen-appliances"),
        ("PC Power Supply Sleeved Cable Extension Kit for Gaming Desktop", "electronics"),
        ("Contact Paper for Kitchen Peel and Stick Wallpaper", "home-storage-decor"),
        ("60 Pack Baby Shower Thank You Cards, Pink Clothes Cards", "sports-office-travel"),
        ("Cat Category Index", "other"),
        ("Unknown Model X100", "other"),
        ("Chicken Soup 12 Pack", "other"),
        ("Tabletop Ornament Replacement Part", "home-storage-decor"),
        ("Dog Squeaky Chew Toy, Sunglasses Wearing Monster Shaped Puppy Play Toy", "pets"),
        ("Pet Hair Remover Glove, Dog Grooming Gloves for Cats Dogs", "pets"),
        ("Pet Fit For Life Reusable Pet Hair Removal Glove Dog & Cat Fur Remover Mitt", "pets"),
        ("Dog Sunglasses Windproof UV Protection Goggles", "pets"),
        ("WOBOX Hero-Style Glove Launcher Toy, Kids Party Play Set", "toys-baby-books"),
        ("Kung Fu Hand Puppets Boxing Puppets Toys Gloves", "toys-baby-books"),
        (
            "Christmas Stocking Holders for Mantle, Hooks for Garland & Stockings",
            "home-storage-decor",
        ),
        ("Rubber Broom & Squeegee with Gloves", "home-storage-decor"),
        ("Travel Pouches for Earphones, Makeup, Sunglasses and Socks", "home-storage-decor"),
        ("Catnip & Silvervine Fish Chew Toy for Indoor Cats", "pets"),
        ("Cat Recovery Suit, Post Surgery Bodysuit for Kittens & Adult Cats", "pets"),
        ("Dog Lace Princess Dress", "pets"),
        ("Dog Recovery Boot for Injured Paws", "pets"),
        ("Bronze Hydrant Tee Reducing Tee, Yard Hydrant Connector", "tools-auto"),
        ("Coaxial Cable for Direct Burial, RF Rubber Boot Waterproof", "electronics"),
        ("Bottle Boot Set for 40 oz Tumblers", "kitchen-appliances"),
        ("Canvas Golf Ball Pouch with 8 Tee Holders", "sports-office-travel"),
        ("SPST Toggle Switch with Boot Cover, Auto Electrical Accessories", "tools-auto"),
        ("Microfiber Cleaning Gloves and Mitts", "home-storage-decor"),
        ("Sangaloz Bath Glove for Kids, Cotton", "beauty-health"),
        ("Christmas Wrapping Paper, Santa Hat Print Holiday Gift Wrap", "home-storage-decor"),
        ("Tee Adapter X100", "other"),
        ("Boot Model X100", "other"),
        ("Car Sun Visor Organizer Magnetic Sunglasses Holder", "tools-auto"),
        ("Car Visor Organizer with Sunglasses Holder for Cars Trucks", "tools-auto"),
        ("Arrest My Vest Odor Eliminator Spray for Ballistic Vests", "home-storage-decor"),
        ("Dog Print T Shirt for Adults", "clothing-accessories"),
        ("Cotton Graphic Shirts for Dogs", "pets"),
        ("Dog Shirt for Small Dogs, Gift for Dog Mom", "pets"),
        (
            "Star Stripe Print Dog Camisole, Breathable Mesh Summer Puppy T Shirt, "
            "Sleeveless Pet Vest, Multiple Sizes Available",
            "pets",
        ),
        ("Graphic Pet Vest with Cotton Lining", "pets"),
    ],
)
def test_product_purpose_beats_animal_motifs_and_ambiguous_clothing_words(title, expected):
    assert classify_product(title).category == expected


@pytest.mark.parametrize(
    ("title", "category"),
    [
        ("Women's Sweatshirt with Pet Carrier Pocket", "clothing-accessories"),
        ("Kids Cotton Romper", "clothing-accessories"),
        ("Warm Sweater for Small Dogs", "pets"),
        ("Christmas Acrylic Ornament, Cat Tree Hole Decoration", "home-storage-decor"),
    ],
)
def test_corrected_classification_reaches_an_existing_discord_channel(tmp_path, title, category):
    projected = project_item(
        Item.from_dict(
            {
                "item_id": "ip-1234567",
                "title": title,
                "value_usd": 15,
                "category": "pets",
                "raw": {"query": "dog"},
            }
        )
    )
    assert projected["category"] == category
    routes = route_keys(projected)
    expected = f"type-{category}"
    assert expected in routes
    channels = {channel["key"]: channel for channel in server_manifest()["channels"]}
    assert channels[expected]["audience"] == "pro"
    assert channels[expected]["bot_only_posting"] is True
    store = JobStore(tmp_path / "queue.sqlite3")
    store.record([projected], now=1000)
    with store._connect() as db:
        recorded = {row[0] for row in db.execute("SELECT route FROM jobs")}
    assert recorded == set(routes)
    assert len([route for route in recorded if route.startswith("type-")]) == 1
