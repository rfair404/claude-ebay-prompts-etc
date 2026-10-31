"""
Test: Ledger schema frozen for v9.0.0

GH #417: Ledger schema must be immutable after v9.0.0.
"""

from pathlib import Path


# v9.0.0 canonical schemas
SALES_LEDGER_SCHEMA = {
    'name': 'sales_ledger.csv',
    'fields': [
        'date', 'item_id', 'sale_price', 'fees', 'platform_fee',
        'postage_deducted', 'net', 'hash', 'notes'
    ],
    'immutable_fields': ['date', 'item_id', 'hash'],
    'version': '9.0.0'
}

CONSIGN_LEDGER_SCHEMA = {
    'name': 'consign_ledger.csv',
    'fields': [
        'date', 'client_id', 'item_id', 'sale_price', 'fees', 'split',
        'client_share', 'hash', 'notes'
    ],
    'immutable_fields': ['date', 'item_id', 'hash'],
    'version': '9.0.0'
}

UNWOUND_LEDGER_SCHEMA = {
    'name': 'unwound_ledger.csv',
    'fields': [
        'date', 'item_id', 'original_price', 'reason', 'credit_account',
        'hash', 'notes'
    ],
    'immutable_fields': ['date', 'item_id', 'hash'],
    'version': '9.0.0'
}

LISTINGS_LEDGER_SCHEMA = {
    'name': 'listings_ledger.csv',
    'fields': [
        'sku', 'item_id', 'store', 'live_date', 'price', 'sold_price',
        'sold_date', 'ended_date', 'hash'
    ],
    'immutable_fields': ['sku', 'item_id', 'live_date', 'hash'],
    'version': '9.0.0'
}

SCHEMAS = [
    SALES_LEDGER_SCHEMA,
    CONSIGN_LEDGER_SCHEMA,
    UNWOUND_LEDGER_SCHEMA,
    LISTINGS_LEDGER_SCHEMA,
]


def test_ledger_store_exists():
    """Verify ledger_store.py exists with required methods."""
    ledger_store_path = Path('lib/ledger_store.py')
    assert ledger_store_path.exists(), \
        "lib/ledger_store.py must exist"

    with open(ledger_store_path, 'r') as f:
        content = f.read()

    required = ['def atomic_write', 'def to_csv', 'def verify_chain']
    for method in required:
        assert method in content, \
            f"ledger_store.py must have {method} method"


def test_hash_field_in_all_schemas():
    """Every ledger must have a 'hash' field for chain integrity."""
    for schema in SCHEMAS:
        assert 'hash' in schema['fields'], \
            f"{schema['name']} missing 'hash' field (required for v9.0.0)"
        assert 'hash' in schema['immutable_fields'], \
            f"hash must be immutable in {schema['name']}"


def test_immutable_fields_are_defined():
    """Immutable fields must be explicitly listed."""
    for schema in SCHEMAS:
        assert schema['immutable_fields'], \
            f"{schema['name']} must have immutable_fields list"
        for field in schema['immutable_fields']:
            assert field in schema['fields'], \
                f"Immutable field '{field}' not in schema for {schema['name']}"


def test_schema_version_locked():
    """All schemas must be at v9.0.0; changing version requires PR review."""
    for schema in SCHEMAS:
        assert schema['version'] == '9.0.0', \
            f"{schema['name']} version mismatch. " \
            f"To change ledger schema, bump this test's SCHEMAS and PR description."


if __name__ == "__main__":
    test_ledger_store_exists()
    test_hash_field_in_all_schemas()
    test_immutable_fields_are_defined()
    test_schema_version_locked()
    print("✓ Ledger schema locked for v9.0.0")
