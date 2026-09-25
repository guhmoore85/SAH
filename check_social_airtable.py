#!/usr/bin/env python3
"""
Diagnostic: inspect the SAH_Social_2026 Airtable base/table someone found
manually (base appEYxbed1rP9wyUq, table tbl5WCOhWD0c8tum5, "Combined_social"-
shaped data). Not referenced anywhere else in this repo -- this script only
exists to figure out what's actually in there and how fresh it is, before
deciding whether/how to wire an automated sync into it.

Usage:
    python check_social_airtable.py

Reads:
    SOCIAL_AIRTABLE_PAT (or AIRTABLE_PAT) - needs access granted to this base
    SOCIAL_AIRTABLE_BASE_ID (default: appEYxbed1rP9wyUq)
    SOCIAL_AIRTABLE_TABLE_ID (default: tbl5WCOhWD0c8tum5)
"""

import os
from collections import Counter

from pyairtable import Api
from dotenv import load_dotenv

load_dotenv()

BASE_ID = os.getenv("SOCIAL_AIRTABLE_BASE_ID", "appEYxbed1rP9wyUq")
TABLE_ID = os.getenv("SOCIAL_AIRTABLE_TABLE_ID", "tbl5WCOhWD0c8tum5")
PAT = os.getenv("SOCIAL_AIRTABLE_PAT") or os.getenv("AIRTABLE_PAT")


def main() -> None:
    if not PAT:
        raise RuntimeError("Set SOCIAL_AIRTABLE_PAT or AIRTABLE_PAT")

    api = Api(PAT)

    print("=" * 70)
    print(f"Base schema: {BASE_ID}")
    print("=" * 70)
    base = api.base(BASE_ID)
    schema = base.schema()
    table_schema = None
    for t in schema.tables:
        marker = " <-- target" if t.id == TABLE_ID else ""
        print(f"  {t.id}  {t.name}  ({len(t.fields)} fields){marker}")
        if t.id == TABLE_ID:
            table_schema = t
    print()

    if table_schema is None:
        print(f"Table {TABLE_ID} not found in this base's schema (PAT may lack access).")
        return

    print("=" * 70)
    print(f"Fields in {table_schema.name} ({TABLE_ID})")
    print("=" * 70)
    for f in table_schema.fields:
        print(f"  {f.name}: {f.type}")
    print()

    table = api.table(BASE_ID, TABLE_ID)
    print("Fetching all records (this may take a moment for ~30k rows)...")
    records = table.all()
    print(f"Total records: {len(records)}")
    print()

    dates = [r["fields"].get("date") for r in records if r["fields"].get("date")]
    channels = Counter(r["fields"].get("channel") for r in records)

    print("=" * 70)
    print("Date range")
    print("=" * 70)
    if dates:
        print(f"  min date: {min(dates)}")
        print(f"  max date: {max(dates)}")
        print(f"  rows with no date: {len(records) - len(dates)}")
    else:
        print("  No date values found")
    print()

    print("=" * 70)
    print("Row counts by channel")
    print("=" * 70)
    for channel, count in channels.most_common():
        print(f"  {channel}: {count}")
    print()

    print("=" * 70)
    print("Sample record (first)")
    print("=" * 70)
    if records:
        print(f"  {records[0]['fields']}")


if __name__ == "__main__":
    main()
