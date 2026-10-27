"""Explainable, local department classification for public product observations.

Product nouns and specific accessory phrases win over department hints. Search
queries may resolve an equal-strength title match; they cannot supply missing
product evidence. The source and matched title term can be logged for review.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Classification:
    category: str
    source: str
    matched_term: str


# Higher scores describe the actual product more specifically. In particular,
# "dog bed", "dollhouse sofa" and "sofa cover" must beat the word "sofa"/"bed".
# Keep terms as normalized whole words/phrases, never substring fragments.
def _terms(values: str) -> tuple[str, ...]:
    """Keep the local vocabulary readable without one row per word."""
    return tuple(values.split("|"))


_RULES = (
    (
        160,
        "home-storage-decor",
        _terms("acrylic ornament|metal sign|tin sign|garden statue|goose outfit"),
    ),
    (
        150,
        "pets",
        _terms(
            "dog bed|cat bed|pet bed|dog toy|cat toy|pet toy|dog toys|cat toys|pet toys|"
            "dog squeaky chew toy|dog food|cat food|pet food|"
            "dog treats|cat treats|cat tree|cat litter|litter box|dog collar|cat collar|"
            "pet collar|dog leash|pet leash|dog harness|cat harness|pet carrier|dog crate|"
            "dog kennel|pet grooming|grooming for dogs|grooming for cats|aquarium filter|"
            "fish tank|bird feeder|bird cage|pet fountain|pet feeder|food feeder for cats|"
            "food feeder for dogs|pet bowl|dog bowl|cat bowl|dog shampoo|dog cone|"
            "dog donut cone|pet recovery collar|cat recovery suit|dog recovery suit|"
            "pet recovery suit|dog rear leg sleeve|pet hair removal gloves|"
            "pet hair removal glove|pet hair remover glove|grooming gloves|"
            "puppy pads|puppy pee pads|dog pee pads|cat scratcher|cat scratching|"
            "catnip toy|catnip toys|catnip pouches|catnip fish|catnip silvervine|"
            "with catnip|organic catnip|dog whistle|dog supplement|cat supplement"
        ),
    ),
    (
        150,
        "toys-baby-books",
        _terms(
            "dollhouse|doll house|toy car|toy truck|toy sofa|toy kitchen|play kitchen|"
            "pretend play|baby car seat|infant car seat|baby high chair|baby monitor|"
            "baby carrier|baby bottle|baby stroller|baby blanket|baby swing|baby bouncer|"
            "diaper bag|audio card|yoto card|tonies audio|board book|picture book|"
            "children s book|children s books|kids book|glove launcher toy|"
            "launcher toy|hand puppets|boxing puppets|costume glove"
        ),
    ),
    (
        150,
        "tools-auto",
        _terms(
            "car seat cover|car seat covers|car vacuum|car charger|car phone mount|"
            "car phone holder|car floor mats|car cover|truck cover|windshield wiper|"
            "windshield wipers|car battery|battery jump starter|tire inflator|oil filter|"
            "garden bed|cabin air filter|gear shift knob|pedal covers|steel nails|"
            "brad nail|brad nails|framing nails|nail gun"
        ),
    ),
    (
        150,
        "beauty-health",
        _terms("adult incontinence|incontinence diapers"),
    ),
    (
        130,
        "home-storage-decor",
        _terms(
            "pillow cover|pillow covers|pillowcase|pillowcases|sofa cover|sofa covers|"
            "couch cover|couch covers|chair cover|chair covers|mattress protector|"
            "mattress topper|bed sheet|bed sheets|sheet set|duvet cover|jewelry box|"
            "jewellery box|floor lamp|table lamp|desk lamp|wall art|wall decor|door decoration|"
            "corner decoration|hanging decor|toilet brush|shower curtain|air conditioner cover|"
            "air conditioner covers|book reading light|clip on booklight|clip on book light|"
            "chair leg floor protector|chair leg floor protectors|hat rack|cap organizer|"
            "clothes drying rack|clothing rack|clothes storage bags|clothesline|"
            "contact paper|wallpaper|stocking holder|stocking holders|rubber broom|travel pouches|"
            "cleaning gloves|wrapping paper|gift wrap|odor eliminator spray"
        ),
    ),
    (
        130,
        "clothing-accessories",
        _terms(
            "yoga pants|running shoes|walking shoes|tennis shoes|golf shirt|swim trunks|"
            "swimming trunks|swim suit|swimsuit|baby clothes|baby clothing|baby bodysuit|"
            "baby onesie|baby shoes|bow tie|bowtie|cufflinks|tank top|tank tops|"
            "knit top|knit tops|scrub top|scrub tops|rash guard|rash guards|bathing suit|"
            "bathing suits|hair accessories|hair band|hair bands"
        ),
    ),
    (
        130,
        "sports-office-travel",
        _terms(
            "camping chair|camping folding chair|camp chair|yoga mat|exercise mat|sleeping bag|"
            "camping tent|golf clubs|golf balls|tennis racket|pickleball paddle|luggage tag|"
            "luggage tags|boxing gloves|baseball gloves|batting gloves|goalkeeper gloves|"
            "swim vest|float vest|life vest|life jacket|weighted vest|swim cap|swim caps|"
            "swimming cap|swimming caps|swim goggles|swimming goggles|thank you cards|"
            "golf ball pouch|golf gloves|fishing gloves|gymnastics hand grips"
        ),
    ),
    (
        130,
        "tools-auto",
        _terms(
            "work gloves|gardening gloves|safety gloves|mechanic gloves|mechanics gloves|"
            "fuel filter|engine air filter|engine filter|center console cover|"
            "console lid|car fast charger|car usb hub|hub for tesla|charger for tesla|"
            "floor mats for tesla|hydrant tee|reducing tee|toggle switch|"
            "sun visor organizer|car visor organizer"
        ),
    ),
    (
        130,
        "kitchen-appliances",
        _terms("oven gloves|grill gloves|gloves for cooking|jar lids|bottle caps|bottle boot"),
    ),
    (
        120,
        "furniture",
        _terms(
            "vanity desk|vanity table|dining table|dining chair|dining chairs|coffee table|"
            "side table|end table|tv stand|bed frame|bedframe|office chair|gaming chair|"
            "patio chair|accent chair|bar stool|bar stools|kitchen island|bathroom vanity"
        ),
    ),
    (
        120,
        "beauty-health",
        _terms(
            "makeup brush|makeup brushes|electric toothbrush|hair dryer|hair straightener|"
            "curling iron|hair clipper|hair clippers|electric shaver|electric razor|"
            "facial serum|skin care|skincare|hair conditioner|press on nails|nail press ons|"
            "nail kit|manicure kit|pedicure kit|false nails|fake nails|toiletry bag|cosmetic bag|"
            "exam gloves|examination gloves|bath gloves|bath glove|arthritis gloves|nail top coat"
        ),
    ),
    (
        110,
        "kitchen-appliances",
        _terms(
            "air fryer|electric kettle|coffee maker|coffee machine|food processor|stand mixer|"
            "hand mixer|vacuum cleaner|stick vacuum|robot vacuum|dinner plates|dinner plate|"
            "drinking glass|drinking glasses|wine glass|wine glasses|air conditioner"
        ),
    ),
    (
        100,
        "electronics",
        _terms(
            "phone case|phone cases|phone cover|phone charm|screen protector|tablet case|"
            "laptop sleeve|charging cable|usb cable|hdmi cable|camera lens|power bank|"
            "power station|memory card|smart watch|wireless earbuds|gaming mouse|"
            "computer mouse|graphics card|pc power supply|"
            "nfc stickers|av cable|coaxial cable"
        ),
    ),
    (
        80,
        "clothing-accessories",
        _terms(
            "shirt|shirts|t shirt|t shirts|tshirt|blouse|blouses|pants|trousers|jeans|denim|"
            "leggings|shorts|dress|dresses|skirt|skirts|sweater|sweaters|hoodie|hoodies|jacket|"
            "jackets|coat|coats|underwear|bra|bras|socks|shoe|shoes|sneakers|boots|sandals|"
            "slippers|pajamas|pyjamas|robe|robes|beanie|scarf|scarves|belt|belts|wallet|"
            "wallets|handbag|handbags|purse|necklace|necklaces|earrings|bracelet|bracelets|"
            "jewelry|earring backs|balaclava|ski mask|skull cap|sweatshirt|sweatshirts|"
            "sweatpants|sweatshorts|joggers|slacks|pullover|pullovers|cardigan|cardigans|"
            "romper|rompers|jumpsuit|jumpsuits|bodysuit|bodysuits|onesie|onesies|overalls|"
            "vest|vests|gilet|gilets|waistcoat|waistcoats|tunic|tunics|tee shirt|tee shirts|"
            "graphic tee|graphic tees|v neck tee|cotton tee|running tee|polo|polos|"
            "swimsuits|swimwear|bikini|bikinis|camisole|camisoles|cami|suspenders|"
            "sunglasses|hat|hats|baseball cap|baseball caps|flat cap|flat caps|trucker cap|"
            "glove|gloves|mittens|headband|headbands|stockings|tights|pantyhose|"
            "loafers|oxfords|moccasins|sneaker|ankle boot|chelsea boot|sandal|sleepwear|"
            "nightgown|nightgowns|"
            "lingerie|bralette|bralettes|raincoat|raincoats|poncho|ponchos"
        ),
    ),
    (
        80,
        "electronics",
        _terms(
            "laptop|laptops|computer|computers|smartphone|smartphones|iphone|ipad|tablet|"
            "tablets|television|televisions|hdtv|headphones|headset|headsets|earbuds|earphones|"
            "speaker|speakers|microphone|microphones|webcam|webcams|camera|cameras|keyboard|"
            "keyboards|printer|printers|projector|projectors|router|routers|modem|smartwatch|"
            "smartwatches|monitor|monitors|ssd|hard drive|flash drive|game controller|"
            "charging station|usb hub|docking station|bluetooth adapter"
        ),
    ),
    (
        80,
        "tools-auto",
        _terms(
            "drill|drills|screwdriver|screwdrivers|wrench|wrenches|socket set|pliers|sander|"
            "grinder|circular saw|chain saw|chainsaw|jigsaw|tool kit|tool box|toolbox|"
            "workbench|ladder|ladders|extension cord|multimeter|soldering iron|paint brush|"
            "paint roller|garden hose|spray nozzle|lawn mower|leaf blower|hedge trimmer|"
            "pressure washer|snow shovel|automotive|motor oil|tire|tires|tyre|tyres|spark plug|"
            "floor jack|trailer hitch|obd2|brake pads|car polish|wire connector|"
            "wire connectors|butt connectors|floor squeegee|trowel"
        ),
    ),
    (
        80,
        "beauty-health",
        _terms(
            "serum|moisturizer|moisturiser|sunscreen|lotion|body wash|shampoo|conditioner|"
            "deodorant|toothbrush|toothpaste|mouthwash|floss|lipstick|mascara|eyeliner|"
            "eyeshadow|concealer|blush|nail polish|nail clippers|perfume|cologne|hair brush|"
            "hairbrush|razor|shaver|vitamins|supplements|thermometer|blood pressure|"
            "heating pad|first aid|bandages|reading glasses|hearing aid|massage gun|massager|"
            "dietary supplement|shower cap"
        ),
    ),
    (
        80,
        "kitchen-appliances",
        _terms(
            "blender|blenders|toaster|toasters|microwave|refrigerator|dishwasher|rice cooker|"
            "slow cooker|pressure cooker|waffle maker|ice maker|juicer|griddle|cookware|"
            "frying pan|saucepan|skillet|stockpot|bakeware|baking sheet|baking pan|"
            "cutting board|chopping board|kitchen knife|kitchen knives|spatula|colander|"
            "measuring cups|mixing bowl|mixing bowls|dinnerware|silverware|flatware|cutlery|"
            "coffee mug|mugs|dish rack|food container|food containers|kitchen towel|apron|"
            "oven mitt|oven mitts|washing machine|clothes dryer|air purifier|humidifier|"
            "dehumidifier|space heater|ceiling fan|cocktail shaker|parchment paper|"
            "cookie press|mason jar|mason jars|champagne flutes|champagne glasses|"
            "beverage dispenser|napkin holder|oven liner|oven liners"
        ),
    ),
    (
        80,
        "toys-baby-books",
        _terms(
            "toy|toys|doll|dolls|lego|play doh|playdough|action figure|action figures|"
            "stuffed animal|plush toy|puzzle|puzzles|board game|card game|building blocks|"
            "stroller|strollers|crib|cribs|bassinet|pacifier|pacifiers|teether|teethers|"
            "diapers|diaper|baby wipes|baby bath|playpen|play mat|baby food|book|books|"
            "storybook|coloring book|colouring book|activity book|yoto|tonies|dnd dice|"
            "polyhedral dice|rpg dice"
        ),
    ),
    (
        80,
        "sports-office-travel",
        _terms(
            "dumbbell|dumbbells|kettlebell|barbell|treadmill|elliptical|exercise bike|"
            "resistance band|resistance bands|jump rope|basketball|football|soccer ball|"
            "baseball glove|helmet|bicycle|bike|bikes|scooter|skateboard|fishing rod|"
            "fishing reel|camping|hiking|luggage|suitcase|suitcases|backpack|backpacks|"
            "travel pillow|travel adapter|ballpoint pens|pen|pens|pencil|pencils|notebook|"
            "notebooks|planner|planners|binder|binders|stapler|scissors|stationery|copy paper|"
            "desk organizer|file organizer|water bottle|tumbler"
        ),
    ),
    (
        80,
        "furniture",
        _terms(
            "sofa|sofas|couch|couches|loveseat|recliner|futon|chair|chairs|ottoman|ottomans|"
            "dresser|dressers|nightstand|nightstands|headboard|footboard|mattress|mattresses|"
            "bed|beds|bunk bed|desk|desks|bookcase|bookcases|bookshelf|bookshelves|cabinet|"
            "cabinets|dining set|patio set|sectional|bench|benches|wardrobe"
        ),
    ),
    (
        80,
        "home-storage-decor",
        _terms(
            "pillow|pillows|bedding|comforter|comforters|quilt|quilts|blanket|blankets|towel|"
            "towels|curtain|curtains|rug|rugs|bath mat|bath mats|mirror|mirrors|vase|vases|"
            "candle|candles|ornament|ornaments|wreath|wreaths|garland|garlands|decor|"
            "decoration|decorations|lamp|lamps|light fixture|string lights|wall clock|"
            "picture frame|storage bin|storage bins|storage basket|storage baskets|"
            "laundry basket|laundry hamper|closet organizer|shelf liner|trash can|trash bags|"
            "laundry detergent|dish soap|paper towels|toilet paper|mop|broom|dustpan|"
            "cleaning cloth|sponge"
        ),
    ),
)

_DEPARTMENT_ALIASES = {
    "furniture": "furniture",
    "home": "home-storage-decor",
    "home storage decor": "home-storage-decor",
    "home decor": "home-storage-decor",
    "home and garden": "home-storage-decor",
    "home garden": "home-storage-decor",
    "household essentials": "home-storage-decor",
    "bedding": "home-storage-decor",
    "kitchen": "kitchen-appliances",
    "appliances": "kitchen-appliances",
    "kitchen appliances": "kitchen-appliances",
    "electronics": "electronics",
    "computers": "electronics",
    "tools auto": "tools-auto",
    "tools": "tools-auto",
    "hardware": "tools-auto",
    "automotive": "tools-auto",
    "patio and garden": "tools-auto",
    "beauty health": "beauty-health",
    "beauty": "beauty-health",
    "health": "beauty-health",
    "personal care": "beauty-health",
    "clothing accessories": "clothing-accessories",
    "clothing": "clothing-accessories",
    "shoes": "clothing-accessories",
    "jewelry": "clothing-accessories",
    "toys baby books": "toys-baby-books",
    "toys": "toys-baby-books",
    "baby": "toys-baby-books",
    "books": "toys-baby-books",
    "pets": "pets",
    "pet supplies": "pets",
    "sports office travel": "sports-office-travel",
    "sports": "sports-office-travel",
    "sports and outdoors": "sports-office-travel",
    "office": "sports-office-travel",
    "office supplies": "sports-office-travel",
    "travel": "sports-office-travel",
    "luggage": "sports-office-travel",
}


# Pet garments need an actual wearer relationship. Animal prints and "cat ear"
# human outfits are not pet supplies. Keep adjectives bounded and explicit.
_PET_GARMENT = (
    r"(?:sweaters?|sweatshirts?|shirts?|t shirts?|hoodies?|jackets?|raincoats?|"
    r"vests?|hats?|boots?|socks?|dresses|dress|pajamas|clothes|clothing|sunglasses)"
)
_PET_WEARER = r"(?:dogs?|cats?|pets?|puppies|puppy|kittens?)"
_PET_SIZE = r"(?:(?:small|medium|large|extra|tiny|sized|and) ){0,5}"
_PET_APPAREL = re.compile(
    rf"\b{_PET_WEARER} (?:(?:warm|winter|knitted|fleece|waterproof|hooded|reflective|"
    rf"soft|cotton|recovery|lace|princess) ){{0,3}}{_PET_GARMENT}\b"
)
_PET_APPAREL_RECIPIENT = re.compile(
    rf"\b{_PET_GARMENT} for {_PET_SIZE}{_PET_WEARER}\b"
    r"(?! (?:mom|dad|lover|owner)s?\b)"
)
_HUMAN_APPAREL = re.compile(
    rf"\b(?:women|womens|men|mens|girls|boys|"
    rf"(?:dog|cat) (?:mom|dad)|adults?\b(?! {_PET_WEARER}\b))\b"
)


def _normalize(value: str, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    # NFKC expands the trademark symbol into letters ("Kettle™" -> "KettleTM"),
    # so remove non-title marks before normalization to preserve word boundaries.
    value = value[:limit].replace("™", "").replace("®", "").replace("©", "")
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.sub(r"[^\w]+|_", " ", value).split())


def _title_evidence(title: str) -> list[tuple[int, str, str]]:
    normalized = _normalize(title, 4096)
    padded = f" {normalized} "
    # A carrier pocket is a feature of a human sweatshirt, not a pet carrier.
    # Remove only that occurrence so a real carrier elsewhere still matches.
    pet_padded = " " + re.sub(r"\bpet carrier (?:pockets?|pouch(?:es)?)\b", "", normalized) + " "
    evidence = []
    # "Women's Ghost Cat T Shirt" depicts a cat; "shirt for dogs" still
    # states a pet wearer even if the listing also mentions a human owner.
    if not _HUMAN_APPAREL.search(normalized) and (pet_apparel := _PET_APPAREL.search(normalized)):
        evidence.append((150, "pets", pet_apparel.group()))
    if pet_recipient := _PET_APPAREL_RECIPIENT.search(normalized):
        evidence.append((150, "pets", pet_recipient.group()))
    for score, category, terms in _RULES:
        text = pet_padded if category == "pets" else padded
        matches = [term for term in terms if f" {term} " in text]
        if matches:
            evidence.append((score, category, max(matches, key=len)))
    return evidence


def _hint_category(hint: str) -> tuple[str, str] | None:
    if not isinstance(hint, str):
        return None
    # Prefer the most specific recognized breadcrumb, but never infer from an
    # arbitrary occurrence such as "category" or a promotional badge.
    parts = re.split(r">|/|→|\||::", hint[:1024])
    for part in reversed(parts):
        term = _normalize(part, 1024)
        if term in _DEPARTMENT_ALIASES:
            return _DEPARTMENT_ALIASES[term], term
    return None


def classify_product(
    title: str, *, category_hint: str = "", search_query: str = ""
) -> Classification:
    """Return a supported department plus an explanation for diagnostic logs.

    Color/style prefixes recorded as ``..., variant on ...`` are ignored. A
    title rule wins over both fallback hints and the sweep query. A query can
    only choose between equal-strength categories independently present in the
    title; its matched_term still names the supporting title evidence. Hints
    must be known departments/breadcrumbs, not the collector's listing badges.
    Unknowns route to ``other`` without making up a department.
    """
    if isinstance(title, str):
        variant = re.match(
            r"\A[^\r\n]{1,256}?,\s*variant\s+on\s+(.+)\Z", title[:4096], re.IGNORECASE
        )
        if variant:
            title = variant.group(1)
    evidence = _title_evidence(title)
    if evidence:
        highest = max(score for score, _, _ in evidence)
        candidates = [entry for entry in evidence if entry[0] == highest]
        candidate_categories = {category for _, category, _ in candidates}
        if len(candidate_categories) > 1:
            query_hint = _hint_category(search_query)
            query_evidence = _title_evidence(search_query)
            query_categories = (
                {query_hint[0]} if query_hint else {category for _, category, _ in query_evidence}
            )
            supported = candidate_categories & query_categories
            if len(supported) == 1:
                chosen = next(iter(supported))
                term = max(
                    (term for _, category, term in candidates if category == chosen), key=len
                )
                return Classification(chosen, "search_query", term)
        # Phrase length resolves equal-strength evidence before stable rule order.
        _, category, term = max(candidates, key=lambda entry: len(entry[2]))
        return Classification(category, "title", term)
    if hint := _hint_category(category_hint):
        return Classification(hint[0], "category_hint", hint[1])
    return Classification("other", "fallback", "")
