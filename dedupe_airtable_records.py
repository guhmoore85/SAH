#!/usr/bin/env python3
"""
Airtable Duplicate Record Cleanup

Removes extra Airtable records that share a sync key with another record,
keeping exactly one per key: the same record the upsert sync matches and
updates (see split_duplicates in sync_sheet_to_airtable.py), so the copy
that stays is the one carrying current values.

Dry run by default -- prints what would be deleted and changes nothing.
Pass --apply to delete.

Before deleting, it reports any field whose value differs between the kept
record and its extra copies, beyond fields the sync itself writes. A field
like that may hold hand-entered data on a copy that would be lost.

Usage:
    python dedupe_airtable_records.py            # dry run
    python dedupe_airtable_records.py --apply    # delete the extra copies

Reads:
    AIRTABLE_PAT, AIRTABLE_BASE_ID, AIRTABLE_TABLE_ID
    SYNC_KEY_FIELDS - comma-separated key fields (e.g. "type,name")
"""

import argparse
import os
import sys
from collections import Counter

from pyairtable import Api
from dotenv import load_dotenv

from sync_sheet_to_airtable import _make_key, delete_records, fetch_existing_records, split_duplicates

load_dotenv()

AIRTABLE_PAT = os.getenv("AIRTABLE_PAT")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_TABLE_ID = os.getenv("AIRTABLE_TABLE_ID")
KEY_FIELDS = [f.strip() for f in os.getenv("SYNC_KEY_FIELDS", "").split(",") if f.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="delete the extra copies (default: dry run)")
    args = parser.parse_args()

    if not (AIRTABLE_PAT and AIRTABLE_BASE_ID and AIRTABLE_TABLE_ID and KEY_FIELDS):
        print("ERROR: AIRTABLE_PAT, AIRTABLE_BASE_ID, AIRTABLE_TABLE_ID and SYNC_KEY_FIELDS are required",
              file=sys.stderr)
        sys.exit(1)

    table = Api(AIRTABLE_PAT).table(AIRTABLE_BASE_ID, AIRTABLE_TABLE_ID)
    existing = fetch_existing_records(table)
    kept_by_key, duplicate_ids = split_duplicates(existing, KEY_FIELDS)

    # Compare each extra copy with the record kept for its key: a field that
    # differs on a copy is data that would be gone after the delete.
    duplicate_set = set(duplicate_ids)
    differing_fields: Counter = Counter()
    examples: dict[str, tuple] = {}
    for rec in existing:
        if rec["id"] not in duplicate_set:
            continue
        key = _make_key(rec["fields"], KEY_FIELDS)
        kept = kept_by_key[key]["fields"]
        for field in set(rec["fields"]) | set(kept):
            if rec["fields"].get(field) != kept.get(field):
                differing_fields[field] += 1
                examples.setdefault(field, (key, rec["fields"].get(field), kept.get(field)))

    print("=" * 70)
    print(f"{'APPLY' if args.apply else 'DRY RUN'}: dedupe on key ({', '.join(KEY_FIELDS)})")
    print("=" * 70)
    print(f"Records now:          {len(existing)}")
    print(f"Distinct keys:        {len(kept_by_key)}")
    print(f"Extra copies:         {len(duplicate_ids)}")
    print(f"Records after:        {len(existing) - len(duplicate_ids)}")
    print()
    print("Fields whose value on an extra copy differs from the kept record")
    print("(the sync's own fields differ because copies hold old snapshots;")
    print(" a field the sync never writes differing may mean hand-entered data):")
    if not differing_fields:
        print("  none -- every copy matches its kept record")
    for field, n in differing_fields.most_common():
        key, copy_val, kept_val = examples[field]
        print(f"  {field}: {n} copies differ  e.g. {key}: copy={copy_val!r} kept={kept_val!r}")
    print()

    if not duplicate_ids:
        print("Nothing to delete.")
        return
    if not args.apply:
        print("Dry run only -- nothing was deleted. Re-run with --apply to delete the extra copies.")
        return

    deleted = delete_records(table, duplicate_ids)
    print(f"Deleted {deleted} extra copies; {len(existing) - deleted} records remain.")


if __name__ == "__main__":
    main()
