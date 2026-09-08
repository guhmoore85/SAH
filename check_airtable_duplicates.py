#!/usr/bin/env python3
"""
Airtable Duplicate Checker (GA4 sync)

Diagnostic script: fetches every record from the GA4 Airtable table and
checks for duplicate (date, dimension_type, dimension_value) combinations —
the same key the sync script treats as unique. If BigQuery's source table
is clean (no duplicates there) but Airtable has dupes, they were introduced
during the Sheets -> Airtable sync (e.g. a retried batch_create after a
timeout, or a partial/failed run that left stale rows behind).

Usage:
    python check_airtable_duplicates.py

Reads the same env vars as sync_ga4_airtable.py:
    AIRTABLE_PAT, AIRTABLE_BASE_ID, AIRTABLE_TABLE_ID
"""

import os
import sys
from collections import defaultdict

from pyairtable import Api
from dotenv import load_dotenv

load_dotenv()

AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID", "appdZ68evU4qlG8mg")
AIRTABLE_TABLE_ID = os.getenv("AIRTABLE_TABLE_ID", "tbl15Hfjy2CZTqgV6")
AIRTABLE_PAT = os.getenv("AIRTABLE_PAT")

KEY_FIELDS = ["date", "dimension_type", "dimension_value"]


def main() -> None:
    if not AIRTABLE_PAT:
        print("ERROR: AIRTABLE_PAT environment variable is required", file=sys.stderr)
        sys.exit(1)

    api = Api(AIRTABLE_PAT)
    table = api.table(AIRTABLE_BASE_ID, AIRTABLE_TABLE_ID)

    print(f"Fetching all records from base={AIRTABLE_BASE_ID} table={AIRTABLE_TABLE_ID} ...")
    records = table.all(fields=KEY_FIELDS)
    print(f"Fetched {len(records)} records\n")

    groups: dict[tuple, list[str]] = defaultdict(list)
    for r in records:
        fields = r.get("fields", {})
        key = tuple(fields.get(f) for f in KEY_FIELDS)
        groups[key].append(r["id"])

    duplicate_groups = {k: v for k, v in groups.items() if len(v) > 1}
    extra_rows = sum(len(v) - 1 for v in duplicate_groups.values())
    missing_key_rows = [k for k in groups if any(v is None for v in k)]

    print("=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"Total records:        {len(records)}")
    print(f"Distinct keys:        {len(groups)}")
    print(f"Duplicate groups:     {len(duplicate_groups)}")
    print(f"Extra (dupe) rows:    {extra_rows}")
    print(f"Rows w/ missing key:  {len(missing_key_rows)}  (null date/type/value)")
    print("=" * 60)

    if duplicate_groups:
        print("\nTop duplicate groups (date, dimension_type, dimension_value) -> record IDs:\n")
        for key, ids in sorted(duplicate_groups.items(), key=lambda kv: -len(kv[1]))[:50]:
            print(f"  {key}  x{len(ids)}  {ids}")
        if len(duplicate_groups) > 50:
            print(f"  ... and {len(duplicate_groups) - 50} more groups")
    else:
        print("\nNo duplicates found.")


if __name__ == "__main__":
    main()
