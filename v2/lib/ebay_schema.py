"""
eBay Sell Inventory API — field reference (static schema).

Static representation of the InventoryItem and Offer request bodies used
when creating a listing via eBay's Sell Inventory API. Sourced from
eBay's official REST API documentation (2026-05 snapshot). Use this
reference to design DRAFT prompt output — DRAFT renders into these
fields directly.

This is intentionally a STATIC reference (not a live OpenAPI fetch).
The InventoryItem and Offer schemas change very infrequently (years).
Per-category aspects (which item-specific fields appear for a given
eBay category) DO change and are fetched live via
`ebay_client.get_category_aspects()`.

Schema format:
    Each leaf field is a dict with:
        type:       "string" / "integer" / "number" / "boolean" / "enum" / "object" / "array"
        required:   True / False / "conditional" (with notes explaining when)
        max_length: int (for strings, where eBay imposes a limit)
        values:     list (for enums; allowed values)
        item_type:  str (for arrays; element type)
        notes:      one-line description of the field's purpose
        example:    a representative value (for documentation only)
    Object fields nest under `fields`.

CLI:
    python ebay_client.py --schema inventory_item
    python ebay_client.py --schema offer
    python ebay_client.py --schema all
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Shared enums (referenced by both InventoryItem and Offer)
# ---------------------------------------------------------------------------

CONDITION_ENUM = [
    "NEW",
    "LIKE_NEW",
    "NEW_OTHER",
    "NEW_WITH_DEFECTS",
    "MANUFACTURER_REFURBISHED",
    "CERTIFIED_REFURBISHED",
    "EXCELLENT_REFURBISHED",
    "VERY_GOOD_REFURBISHED",
    "GOOD_REFURBISHED",
    "SELLER_REFURBISHED",
    "USED_EXCELLENT",
    "USED_VERY_GOOD",
    "USED_GOOD",
    "USED_ACCEPTABLE",
    "FOR_PARTS_OR_NOT_WORKING",
]

LENGTH_UNIT_ENUM = ["INCH", "FEET", "CENTIMETER", "METER"]
WEIGHT_UNIT_ENUM = ["POUND", "KILOGRAM", "OUNCE", "GRAM"]
CURRENCY_ENUM_COMMON = ["USD", "GBP", "EUR", "CAD", "AUD"]
MARKETPLACE_ENUM_COMMON = [
    "EBAY_US", "EBAY_GB", "EBAY_DE", "EBAY_AU", "EBAY_CA",
    "EBAY_FR", "EBAY_IT", "EBAY_ES",
]
LISTING_FORMAT_ENUM = ["FIXED_PRICE", "AUCTION"]
LISTING_DURATION_ENUM = [
    "DAYS_1", "DAYS_3", "DAYS_5", "DAYS_7", "DAYS_10", "DAYS_21", "DAYS_30",
    "GTC",  # Good 'Til Cancelled — the default for fixed-price
]
PACKAGE_TYPE_ENUM = [
    "LETTER", "BULKY_GOODS", "CARAVAN", "CARS", "EUROPALLET",
    "EXPANDABLE_TOUGH_BAGS", "EXTRA_LARGE_PACK", "FURNITURE",
    "INDUSTRY_VEHICLES", "LARGE_CANADA_POSTBOX",
    "LARGE_CANADA_POST_BUBBLE_MAILER", "LARGE_ENVELOPE", "MAILING_BOX",
    "MEDIUM_CANADA_POST_BOX", "MEDIUM_CANADA_POST_BUBBLE_MAILER",
    "MOTORBIKES", "ONE_WAY_PALLET", "PACKAGE_THICK_ENVELOPE",
    "PADDED_BAGS", "PARCEL_OR_PADDED_ENVELOPE", "ROLL",
    "SMALL_CANADA_POST_BOX", "SMALL_CANADA_POST_BUBBLE_MAILER",
    "TOUGH_BAGS", "UPS_LETTER", "USPS_FLAT_RATE_ENVELOPE",
    "USPS_LARGE_PACK", "VERY_LARGE_PACK", "WINE_PAK",
]

# ---------------------------------------------------------------------------
# InventoryItem schema (POST /sell/inventory/v1/inventory_item/{sku})
# ---------------------------------------------------------------------------

INVENTORY_ITEM_SCHEMA = {
    "sku": {
        "type": "string",
        "required": True,
        "max_length": 50,
        "notes": "Seller-defined unique inventory ID. Set as URL path param. "
                 "Treat as the canonical handle for the item — DRAFT generates it.",
        "example": "polo-1977-vol2-muses",
    },
    "locale": {
        "type": "enum",
        "required": False,
        "values": ["en_US", "en_GB", "en_AU", "en_CA", "de_DE", "fr_FR", "it_IT", "es_ES"],
        "notes": "Locale for the listing. Defaults to en_US for EBAY_US marketplace.",
        "example": "en_US",
    },
    "condition": {
        "type": "enum",
        "required": True,
        "values": CONDITION_ENUM,
        "notes": "eBay condition code. USED_GOOD / USED_VERY_GOOD / "
                 "USED_EXCELLENT cover most vintage resale. FOR_PARTS_OR_NOT_WORKING "
                 "for untested electronics or known-broken items.",
        "example": "USED_GOOD",
    },
    "conditionDescription": {
        "type": "string",
        "required": False,
        "max_length": 1000,
        "notes": "Free-text elaboration on condition. Used / refurbished items only. "
                 "Reuse INVESTIGATE's listing-safe condition sentence here.",
        "example": "Back cover shows foxing and age spotting; front cover intact.",
    },
    "conditionDescriptors": {
        "type": "array",
        "required": False,
        "item_type": "object",
        "notes": "Category-specific structured condition descriptors "
                 "(e.g., 'Damage Type: Scratches'). Required for some "
                 "categories (jewelry, watches). Discover applicable descriptors "
                 "via the Metadata API per category.",
    },
    "availability": {
        "type": "object",
        "required": True,
        "notes": "Where and how many units are available to ship.",
        "fields": {
            "shipToLocationAvailability": {
                "type": "object",
                "required": True,
                "fields": {
                    "quantity": {
                        "type": "integer",
                        "required": True,
                        "notes": "Total units available across all merchant locations. "
                                 "For single/pair/set/lot unit_types: always 1. "
                                 "For duplicate unit_type: the count.",
                        "example": 1,
                    },
                    "availabilityDistributions": {
                        "type": "array",
                        "required": False,
                        "item_type": "object",
                        "notes": "Per-merchant-location quantity breakdown (multi-warehouse).",
                    },
                },
            },
            "pickupAtLocationAvailability": {
                "type": "array",
                "required": False,
                "item_type": "object",
                "notes": "In-store-pickup availability per location. Rare for resellers.",
            },
        },
    },
    "packageWeightAndSize": {
        "type": "object",
        "required": False,
        "notes": "Required if you want eBay to calculate shipping. Always include "
                 "for resale items to avoid manual shipping config per listing.",
        "fields": {
            "dimensions": {
                "type": "object",
                "required": False,
                "fields": {
                    "length": {"type": "number", "required": True, "notes": "Longest side."},
                    "width":  {"type": "number", "required": True, "notes": "Middle side."},
                    "height": {"type": "number", "required": True, "notes": "Shortest side."},
                    "unit":   {"type": "enum", "values": LENGTH_UNIT_ENUM, "required": True},
                },
            },
            "weight": {
                "type": "object",
                "required": False,
                "fields": {
                    "value": {"type": "number", "required": True, "notes": "Numeric weight."},
                    "unit":  {"type": "enum", "values": WEIGHT_UNIT_ENUM, "required": True},
                },
            },
            "packageType": {
                "type": "enum",
                "required": False,
                "values": PACKAGE_TYPE_ENUM,
                "notes": "eBay package type. Affects shipping-cost calculation. "
                         "Most resale items: PACKAGE_THICK_ENVELOPE, MAILING_BOX, "
                         "PADDED_BAGS, LARGE_ENVELOPE, or USPS_LARGE_PACK.",
                "example": "MAILING_BOX",
            },
            "shippingIrregular": {
                "type": "boolean",
                "required": False,
                "notes": "True if item ships irregular (long, awkward, etc.). "
                         "Triggers dimensional-weight pricing.",
                "example": False,
            },
        },
    },
    "product": {
        "type": "object",
        "required": True,
        "notes": "Product details — title, description, aspects, images. The "
                 "core content DRAFT fills. Required for InventoryItem.",
        "fields": {
            "title": {
                "type": "string",
                "required": True,
                "max_length": 80,
                "notes": "Listing title. The single most important search field on eBay. "
                         "Hard 80-char limit. Lead with the most distinctive defensible claim "
                         "(brand + year + type + key descriptor).",
                "example": "Polo by Ralph Lauren Volume II Fall 1977 Catalog Vintage Atlanta Muse's",
            },
            "subtitle": {
                "type": "string",
                "required": False,
                "max_length": 55,
                "notes": "Optional paid subtitle ($1.50 fee). Rarely justifies cost for "
                         "vintage resale; skip unless category has heavy competition.",
            },
            "description": {
                "type": "string",
                "required": True,
                "max_length": 500000,  # eBay technical max; practical limit ~4000 chars
                "notes": "HTML-allowed listing description body. Practical sweet spot is "
                         "300-1500 chars of clean prose + structured detail block. "
                         "Mobile-first: 80% of buyers read on phones. Avoid heavy HTML / images.",
            },
            "aspects": {
                "type": "object",
                "required": "conditional",
                "notes": "Category-specific item specifics (Brand, Type, Material, Era, etc.). "
                         "Required for most categories. KEY is the aspect name "
                         "(category-specific); VALUE is an array of one-or-more string values. "
                         "FETCH the per-category aspect set via "
                         "ebay_client.get_category_aspects(category_id) BEFORE rendering.",
                "example": {
                    "Brand": ["Polo Ralph Lauren"],
                    "Year": ["1977"],
                    "Type": ["Catalog"],
                    "Original/Reproduction": ["Original"],
                },
            },
            "brand": {
                "type": "string",
                "required": False,
                "max_length": 65,
                "notes": "Brand name. Also surfaceable via aspects.Brand. Set both for "
                         "max search coverage. Use 'Unbranded' when no maker is "
                         "defensibly identified.",
                "example": "Polo Ralph Lauren",
            },
            "mpn": {
                "type": "string",
                "required": False,
                "max_length": 65,
                "notes": "Manufacturer part number. Rare for vintage / collectibles.",
            },
            "isbn": {
                "type": "array",
                "required": False,
                "item_type": "string",
                "notes": "ISBNs (books). Up to 5 entries. Drives eBay catalog match.",
            },
            "upc": {
                "type": "array",
                "required": False,
                "item_type": "string",
                "notes": "UPC codes. Up to 5. For modern barcoded items.",
            },
            "ean": {
                "type": "array",
                "required": False,
                "item_type": "string",
                "notes": "EAN codes (European barcodes). Up to 5.",
            },
            "epid": {
                "type": "string",
                "required": False,
                "notes": "eBay catalog Product ID. If set, eBay attaches the listing to "
                         "the catalog product page. Discover via Catalog API or search-match.",
            },
            "imageUrls": {
                "type": "array",
                "required": True,
                "item_type": "string",
                "max_items": 24,
                "notes": "Listing image URLs (HTTPS only). MIN 1, MAX 24. First image is "
                         "the gallery thumbnail. Hosted on eBay Picture Service (EPS) or "
                         "external HTTPS-accessible URLs. eBay re-hosts external URLs on "
                         "first publish. Typical resale flow: upload to EPS via Sell API.",
                "example": ["https://i.ebayimg.com/example.jpg"],
            },
            "videoIds": {
                "type": "array",
                "required": False,
                "item_type": "string",
                "max_items": 1,
                "notes": "eBay-hosted video IDs (upload via Sell Account API). One per listing.",
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Offer schema (POST /sell/inventory/v1/offer)
# ---------------------------------------------------------------------------

OFFER_SCHEMA = {
    "sku": {
        "type": "string",
        "required": True,
        "max_length": 50,
        "notes": "Inventory SKU this offer belongs to. Must reference an existing "
                 "InventoryItem.",
        "example": "polo-1977-vol2-muses",
    },
    "marketplaceId": {
        "type": "enum",
        "required": True,
        "values": MARKETPLACE_ENUM_COMMON,
        "notes": "Which eBay marketplace to list on. EBAY_US for US listings.",
        "example": "EBAY_US",
    },
    "format": {
        "type": "enum",
        "required": True,
        "values": LISTING_FORMAT_ENUM,
        "notes": "Listing format. FIXED_PRICE for Buy It Now / BIN + Best Offer "
                 "(typical resale). AUCTION is rare for the user's flow.",
        "example": "FIXED_PRICE",
    },
    "availableQuantity": {
        "type": "integer",
        "required": "conditional",
        "notes": "Quantity available to sell under this offer. Required for "
                 "FIXED_PRICE offers; for AUCTION always 1. For duplicate unit_type, "
                 "this is the multi-unit count.",
        "example": 1,
    },
    "categoryId": {
        "type": "string",
        "required": True,
        "notes": "Leaf eBay category ID (numeric string). Discover via Taxonomy API "
                 "(getCategorySuggestions or browse the category tree). Determines "
                 "which aspects apply to product.aspects above.",
        "example": "1184",  # eBay > Collectibles > Paper > Catalogs
    },
    "secondaryCategoryId": {
        "type": "string",
        "required": False,
        "notes": "Optional second category for cross-listing (fee applies). Boosts "
                 "search coverage at modest cost ($0.20-$0.40). Rarely worth it for "
                 "vintage paper; can be worth it for fashion items.",
    },
    "storeCategoryNames": {
        "type": "array",
        "required": False,
        "item_type": "string",
        "max_items": 2,
        "notes": "Up to two eBay Store category names (if seller has an eBay Store). "
                 "Organizational only — does NOT replace categoryId.",
    },
    "listingDescription": {
        "type": "string",
        "required": False,
        "max_length": 500000,
        "notes": "Optional override of product.description for THIS specific offer. "
                 "Useful when same inventory item is listed on multiple marketplaces with "
                 "marketplace-specific copy. Otherwise leave null and the offer inherits "
                 "from product.description.",
    },
    "listingDuration": {
        "type": "enum",
        "required": True,
        "values": LISTING_DURATION_ENUM,
        "notes": "GTC (Good 'Til Cancelled) is the standard for FIXED_PRICE — renews "
                 "every 30 days automatically. AUCTION offers use DAYS_1-DAYS_10.",
        "example": "GTC",
    },
    "listingPolicies": {
        "type": "object",
        "required": True,
        "notes": "References to seller's pre-configured business policies. Create the "
                 "underlying policies once via the Account API; reference by ID per offer.",
        "fields": {
            "paymentPolicyId": {
                "type": "string",
                "required": True,
                "notes": "ID of payment policy (eBay Managed Payments — no PayPal "
                         "configuration needed). Set once per account.",
            },
            "fulfillmentPolicyId": {
                "type": "string",
                "required": True,
                "notes": "ID of shipping policy. Carrier/service/cost/handling-time. "
                         "Typical: one Media Mail policy + one USPS Priority policy + one "
                         "Free-Shipping-Calculated policy.",
            },
            "returnPolicyId": {
                "type": "string",
                "required": True,
                "notes": "ID of return policy (30-day or 60-day; seller-pays or "
                         "buyer-pays return shipping). Vintage items: 30-day buyer-pays "
                         "is typical, or 'No Returns' for certain low-value lots.",
            },
            "bestOfferTerms": {
                "type": "object",
                "required": False,
                "fields": {
                    "bestOfferEnabled": {
                        "type": "boolean",
                        "required": False,
                        "notes": "Enable Best Offer on this listing. Default false; "
                                 "explicit true for negotiable vintage items.",
                    },
                    "autoAcceptPrice": {
                        "type": "object",
                        "required": False,
                        "fields": {
                            "value": {"type": "string", "required": True, "notes": "Numeric string, e.g. '199.00'"},
                            "currency": {"type": "enum", "values": CURRENCY_ENUM_COMMON, "required": True},
                        },
                        "notes": "Auto-accept any Best Offer at or above this price.",
                    },
                    "autoDeclinePrice": {
                        "type": "object",
                        "required": False,
                        "fields": {
                            "value": {"type": "string", "required": True},
                            "currency": {"type": "enum", "values": CURRENCY_ENUM_COMMON, "required": True},
                        },
                        "notes": "Auto-decline any Best Offer at or below this price.",
                    },
                },
            },
        },
    },
    "merchantLocationKey": {
        "type": "string",
        "required": True,
        "max_length": 36,
        "notes": "ID of merchant location (warehouse / home address) where item ships "
                 "FROM. Create once via Inventory Location API. Used by eBay for "
                 "shipping-cost calculation.",
        "example": "home-warehouse",
    },
    "pricingSummary": {
        "type": "object",
        "required": True,
        "notes": "Price and any promotional pricing.",
        "fields": {
            "price": {
                "type": "object",
                "required": True,
                "fields": {
                    "value": {"type": "string", "required": True, "notes": "Numeric string with up to 2 decimals."},
                    "currency": {"type": "enum", "values": CURRENCY_ENUM_COMMON, "required": True},
                },
                "notes": "The asking price (or Buy It Now price for FIXED_PRICE format).",
                "example": {"value": "225.00", "currency": "USD"},
            },
            "originallySoldForRetailPriceOn": {
                "type": "enum",
                "required": False,
                "values": ["ON_EBAY", "OFF_EBAY"],
                "notes": "For Strike-Through pricing: where was the original retail "
                         "price set. Rarely used in resale.",
            },
            "originalRetailPrice": {
                "type": "object",
                "required": False,
                "fields": {
                    "value": {"type": "string", "required": True},
                    "currency": {"type": "enum", "values": CURRENCY_ENUM_COMMON, "required": True},
                },
                "notes": "Used with Strike-Through Pricing to show MSRP vs. current price.",
            },
            "minimumAdvertisedPrice": {
                "type": "object",
                "required": False,
                "fields": {
                    "value": {"type": "string", "required": True},
                    "currency": {"type": "enum", "values": CURRENCY_ENUM_COMMON, "required": True},
                },
                "notes": "MAP enforcement (manufacturer-restricted minimums). Rare in resale.",
            },
            "pricingVisibility": {
                "type": "enum",
                "required": False,
                "values": ["NONE", "PRE_CHECKOUT", "DURING_CHECKOUT"],
                "notes": "When to show MAP-suppressed price to the buyer. With MAP only.",
            },
        },
    },
    "quantityLimitPerBuyer": {
        "type": "integer",
        "required": False,
        "notes": "Max units one buyer can purchase across this offer. Useful for "
                 "duplicate unit_type listings to prevent one buyer wiping inventory.",
    },
    "tax": {
        "type": "object",
        "required": False,
        "fields": {
            "applyTax": {
                "type": "boolean",
                "required": False,
                "notes": "True to apply tax tables. eBay handles US sales-tax collection "
                         "automatically in Managed Payments — typically leave null.",
            },
            "vatPercentage": {
                "type": "number",
                "required": False,
                "notes": "VAT percentage for EU/UK marketplaces.",
            },
            "thirdPartyTaxCategory": {
                "type": "string",
                "required": False,
                "notes": "Tax category ID (third-party tax service integrations).",
            },
        },
    },
    "charity": {
        "type": "object",
        "required": False,
        "fields": {
            "charityId": {
                "type": "string",
                "required": True,
                "notes": "eBay Charity registered charity ID.",
            },
            "donationPercentage": {
                "type": "string",
                "required": True,
                "notes": "Numeric string 10-100. Percentage of sale donated to charity.",
            },
        },
        "notes": "Optional eBay for Charity setup per listing.",
    },
    "includeCatalogProductDetails": {
        "type": "boolean",
        "required": False,
        "notes": "If true and the inventory item has an EPID, eBay pulls catalog "
                 "details (stock photos, description text) and overlays them. Default "
                 "true. Set false to use ONLY seller-supplied content (recommended for "
                 "vintage / collectibles where catalog data is generic).",
        "example": False,
    },
    "regulatory": {
        "type": "object",
        "required": False,
        "notes": "Category-specific regulatory metadata (hazmat, energy efficiency, "
                 "etc.). Most resale categories don't need this.",
    },
}


# ---------------------------------------------------------------------------
# Helper: print a schema tree in human-readable form
# ---------------------------------------------------------------------------

def print_schema(schema: dict, name: str, indent: int = 0) -> None:
    """Pretty-print a schema dict to stdout as an indented tree."""
    bar = "  " * indent
    print(f"{bar}=== {name} ===")
    for field_name, spec in schema.items():
        _print_field(field_name, spec, indent + 1)


def _print_field(field_name: str, spec: dict, indent: int) -> None:
    bar = "  " * indent
    ftype = spec.get("type", "?")
    req_raw = spec.get("required", False)
    req = "REQUIRED" if req_raw is True else ("CONDITIONAL" if req_raw == "conditional" else "optional")

    header = f"{bar}{field_name}: {ftype} [{req}]"
    extras = []
    if "max_length" in spec:
        extras.append(f"max_length={spec['max_length']}")
    if "max_items" in spec:
        extras.append(f"max_items={spec['max_items']}")
    if "item_type" in spec:
        extras.append(f"item_type={spec['item_type']}")
    if extras:
        header += "  (" + ", ".join(extras) + ")"
    print(header)

    if "values" in spec:
        vals = spec["values"]
        if len(vals) <= 4:
            print(f"{bar}  values: {vals}")
        else:
            print(f"{bar}  values: {vals[:4]} ... +{len(vals) - 4} more")

    if "notes" in spec:
        notes = spec["notes"].replace("\n", " ")
        # wrap at 78 chars for readability
        wrapped = _wrap(notes, width=78 - len(bar) - 9)
        for line in wrapped:
            print(f"{bar}  notes: {line}" if line is wrapped[0] else f"{bar}         {line}")

    if "example" in spec:
        print(f"{bar}  example: {spec['example']!r}")

    if "fields" in spec:
        for sub_name, sub_spec in spec["fields"].items():
            _print_field(sub_name, sub_spec, indent + 1)


def _wrap(text: str, width: int) -> list[str]:
    """Simple word-wrap for note text."""
    out: list[str] = []
    cur = ""
    for word in text.split():
        if not cur:
            cur = word
        elif len(cur) + 1 + len(word) <= width:
            cur += " " + word
        else:
            out.append(cur)
            cur = word
    if cur:
        out.append(cur)
    return out or [""]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

SCHEMAS = {
    "inventory_item": (INVENTORY_ITEM_SCHEMA, "InventoryItem"),
    "offer":          (OFFER_SCHEMA,          "Offer"),
}


def get_schema(name: str) -> tuple[dict, str]:
    """Return (schema_dict, display_name) for one of: inventory_item, offer."""
    if name not in SCHEMAS:
        raise ValueError(
            f"Unknown schema '{name}'. Options: {sorted(SCHEMAS)}"
        )
    return SCHEMAS[name]
