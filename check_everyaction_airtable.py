#!/usr/bin/env python3
"""
EveryAction Airtable Table Inspector

Diagnostic script: the EveryAction Airtable table should hold roughly one
record per email and form (a few hundred), matching everyaction_combined,
but it has held ~55k. This fetches every record and breaks the table down so
the extra records can be identified before anything is deleted:
    - records per (type, name) key -- the key the daily sync upserts on
    - records with an empty type or name (never matched by the sync)
    - records by type, and by the month Airtable created them
    - example records from the largest duplicate groups

Read-only: it never writes to Airtable.

Usage:
    python check_everyaction_airtable.py

Reads:
    AIRTABLE_PAT
    AIRTABLE_BASE_ID  - the EveryAction base (EVERYACTION_AIRTABLE_BASE_ID secret)
    AIRTABLE_TABLE_ID - the EveryAction table (EVERYACTION_AIRTABLE_TABLE_ID secret)
"""

import os
import sys
from collections import Counter, defaultdict

from pyairtable import Api
from dotenv import load_dotenv

load_dotenv()

AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_TABLE_ID = os.getenv("AIRTABLE_TABLE_ID")
AIRTABLE_PAT = os.getenv("AIRTABLE_PAT")

KEY_FIELDS = ["type", "name"]


def main() -> None:
    if not (AIRTABLE_PAT and AIRTABLE_BASE_ID and AIRTABLE_TABLE_ID):
        print("ERROR: AIRTABLE_PAT, AIRTABLE_BASE_ID and AIRTABLE_TABLE_ID are required", file=sys.stderr)
        sys.exit(1)

    table = Api(AIRTABLE_PAT).table(AIRTABLE_BASE_ID, AIRTABLE_TABLE_ID)
    print("Fetching all records ...")
    records = table.all()
    print(f"Fetched {len(records)} records\n")

    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in records:
        fields = r.get("fields", {})
        groups[tuple(fields.get(f) for f in KEY_FIELDS)].append(r)

    empty_key = [k for k in groups if any(v in (None, "") for v in k)]
    empty_key_rows = sum(len(groups[k]) for k in empty_key)
    dupes = {k: v for k, v in groups.items() if len(v) > 1 and k not in empty_key}
    extra = sum(len(v) - 1 for v in dupes.values())

    print("=" * 70)
    print("Summary")
    print("=" * 70)
    print(f"Total records:                 {len(records)}")
    print(f"Distinct (type, name) keys:    {len(groups)}")
    print(f"Records with empty type/name:  {empty_key_rows}")
    print(f"Duplicate keys (non-empty):    {len(dupes)}")
    print(f"Extra records in those keys:   {extra}")
    print()

    print("=" * 70)
    print("Records by type")
    print("=" * 70)
    for t, n in Counter(r.get("fields", {}).get("type") for r in records).most_common():
        print(f"  {t!r}: {n}")
    print()

    print("=" * 70)
    print("Records by month created in Airtable (createdTime)")
    print("=" * 70)
    for month, n in sorted(Counter(r.get("createdTime", "")[:7] for r in records).items()):
        print(f"  {month}: {n}")
    print()

    print("=" * 70)
    print("Fields populated (how many records have a value)")
    print("=" * 70)
    filled = Counter(f for r in records for f, v in r.get("fields", {}).items() if v not in (None, ""))
    for f, n in filled.most_common():
        print(f"  {f}: {n}")
    print()

    print("=" * 70)
    print("Largest duplicate groups: key -> count, created range, sample fields")
    print("=" * 70)
    for key, recs in sorted(dupes.items(), key=lambda kv: -len(kv[1]))[:15]:
        created = sorted(r.get("createdTime", "") for r in recs)
        sample = {k: v for k, v in recs[0].get("fields", {}).items()
                  if k in ("first_date", "last_date", "recipients", "submissions", "views")}
        print(f"  {key}  x{len(recs)}  created {created[0][:10]}..{created[-1][:10]}  {sample}")
    if empty_key:
        print()
        print("Sample records with an empty type or name:")
        shown = 0
        for k in empty_key:
            for r in groups[k][:2]:
                print(f"  created {r.get('createdTime', '')[:10]}  {dict(list(r.get('fields', {}).items())[:8])}")
                shown += 1
            if shown >= 6:
                break


if __name__ == "__main__":
    main()
