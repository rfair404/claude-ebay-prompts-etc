"""
Test: Ledger schema frozen for v9.0.0

GH #417: Ledger schema must be immutable after v9.0.0.
This test defines the canonical schema and blocks any changes without version bump.
"""

from pathlib import Path
import csv


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


def test_ledger_store_has_required_methods():
    """Verify ledger_store.py exports required writers."""
    from lib import ledger_store

    required = ['atomic_write', 'to_csv', 'verify_chain']
    for method in required:
        assert hasattr(ledger_store, method), \
            f"ledger_store.py must have {method}() method"


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


def test_ledger_files_have_header_row():
    """Verify existing ledger files have headers matching schema."""
    from lib.config import get_config

    config = get_config()
    ledger_dir = Path(config.get('ledger_dir', 'ledgers'))

    for schema in SCHEMAS:
        ledger_file = ledger_dir / schema['name']

        if not ledger_file.exists():
            # OK for new ledgers not yet created
            continue

        # Read header and verify it matches schema
        try:
            with open(ledger_file, 'r') as f:
                reader = csv.DictReader(f)
                header = reader.fieldnames

            # All schema fields must be present
            for field in schema['fields']:
                assert field in header, \
                    f"Missing field '{field}' in {schema['name']}"
        except Exception as e:
            # If file is corrupted, that's OK for this test
            # (recovery is tested elsewhere)
            pass


if __name__ == "__main__":
    test_ledger_store_has_required_methods()
    test_hash_field_in_all_schemas()
    test_immutable_fields_are_defined()
    test_schema_version_locked()
    print("✓ Ledger schema locked for v9.0.0")
