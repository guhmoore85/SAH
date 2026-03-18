#!/usr/bin/env python3
"""
Google Sheets → Airtable Sync (Reusable)
==========================================
Reads data from any Google Sheet tab and syncs it to an Airtable table.
Designed to be reusable across multiple pipelines (GA4, EveryAction, Social
Media, etc.) — all configuration is driven by environment variables.

Strategy (configurable via SYNC_MODE):
  - "upsert" (default): match on key fields, update existing rows, insert new ones
  - "replace": delete all existing records then batch-insert (legacy mode)

Environment variables:
    GOOGLE_SERVICE_ACCOUNT_JSON  - Service account credentials (JSON string)
    GOOGLE_SERVICE_ACCOUNT_FILE  - Or path to JSON key file (local dev)
    AIRTABLE_PAT                 - Airtable Personal Access Token
    AIRTABLE_BASE_ID             - Destination Airtable base ID
    AIRTABLE_TABLE_ID            - Destination Airtable table ID
    GOOGLE_SHEET_ID              - Source Google Sheet ID
    GOOGLE_SHEET_TAB             - Source tab/worksheet name
    SYNC_MODE                    - "upsert" (default) or "replace"
    SYNC_KEY_FIELDS              - Comma-separated fields for upsert key (e.g. "type,name")
    ROW_LIMIT                    - (optional) Limit rows for testing

Usage:
    python sync_sheet_to_airtable.py
"""

import json
import logging
import os
import sys
import time
from datetime import datetime
from typing import Any

import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from pyairtable import Api

# ---------------------------------------------------------------------------
# Configuration (all from environment variables)
# ---------------------------------------------------------------------------

load_dotenv()

GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_SHEET_TAB = os.getenv("GOOGLE_SHEET_TAB")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_TABLE_ID = os.getenv("AIRTABLE_TABLE_ID")
AIRTABLE_PAT = os.getenv("AIRTABLE_PAT")

SYNC_MODE = os.getenv("SYNC_MODE", "upsert").lower()  # "upsert" or "replace"
SYNC_KEY_FIELDS = [f.strip() for f in os.getenv("SYNC_KEY_FIELDS", "").split(",") if f.strip()]

ROW_LIMIT = int(os.getenv("ROW_LIMIT", 0)) or None

# Airtable API limit is 10 records per batch request
BATCH_SIZE = 10
# Small sleep between batches to respect Airtable rate limits (5 req/s)
BATCH_SLEEP = 0.25

MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds (doubles each attempt)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def get_google_sheets_client() -> gspread.Client:
    """Authenticate with Google Sheets via service account."""
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ]

    json_creds = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if json_creds:
        logger.info("Using Google credentials from GOOGLE_SERVICE_ACCOUNT_JSON")
        creds_dict = json.loads(json_creds)
        credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        return gspread.authorize(credentials)

    creds_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    if creds_file:
        logger.info("Using Google credentials from file: %s", creds_file)
        credentials = Credentials.from_service_account_file(creds_file, scopes=scopes)
        return gspread.authorize(credentials)

    raise ValueError(
        "Google credentials not found. Set GOOGLE_SERVICE_ACCOUNT_JSON or "
        "GOOGLE_SERVICE_ACCOUNT_FILE environment variable."
    )


def get_airtable_table():
    """Return a pyairtable Table object."""
    if not AIRTABLE_PAT:
        raise ValueError("AIRTABLE_PAT environment variable is required")
    if not AIRTABLE_BASE_ID or not AIRTABLE_TABLE_ID:
        raise ValueError(
            "AIRTABLE_BASE_ID and AIRTABLE_TABLE_ID environment variables are required"
        )
    api = Api(AIRTABLE_PAT)
    return api.table(AIRTABLE_BASE_ID, AIRTABLE_TABLE_ID)


# ---------------------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------------------


def retry_operation(operation, *args, **kwargs):
    """Execute an operation with exponential-backoff retry."""
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            return operation(*args, **kwargs)
        except Exception as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                wait = RETRY_DELAY * (2 ** attempt)
                logger.warning("Attempt %d failed: %s. Retrying in %ds...", attempt + 1, e, wait)
                time.sleep(wait)
            else:
                logger.error("All %d attempts failed", MAX_RETRIES)
    raise last_error


# ---------------------------------------------------------------------------
# Google Sheets reader
# ---------------------------------------------------------------------------


def read_google_sheet(client: gspread.Client) -> tuple[list[str], list[list[Any]]]:
    """Read all data from the configured Google Sheet tab.

    Returns (headers, rows).
    """
    logger.info("Opening Google Sheet: %s", GOOGLE_SHEET_ID)
    sheet = client.open_by_key(GOOGLE_SHEET_ID)

    logger.info("Reading tab: %s", GOOGLE_SHEET_TAB)
    worksheet = sheet.worksheet(GOOGLE_SHEET_TAB)

    all_values = worksheet.get_all_values()
    if not all_values:
        raise ValueError("Google Sheet is empty")

    headers = all_values[0]
    rows = all_values[1:]

    if ROW_LIMIT:
        rows = rows[:ROW_LIMIT]
        logger.info("Row limit applied: %d rows", ROW_LIMIT)

    logger.info("Read %d rows with %d columns", len(rows), len(headers))
    return headers, rows


# ---------------------------------------------------------------------------
# Auto-coerce cell values
# ---------------------------------------------------------------------------


def _coerce_value(value: str) -> Any:
    """Auto-coerce a Google Sheets cell value.

    - Empty/whitespace → skip (returns None)
    - ISO dates (YYYY-MM-DD) → kept as string (Airtable date fields accept this)
    - M/D/YYYY dates → converted to YYYY-MM-DD
    - Numbers (int or float) → numeric
    - Percentages like "45.2%" → 0.452 (float)
    - Currency like "$1,234.56" → 1234.56 (float)
    - Everything else → string
    """
    if not isinstance(value, str):
        return value

    value = value.strip()
    if not value:
        return None

    # Percentage: "45.2%" → 0.452
    if value.endswith("%"):
        try:
            return float(value[:-1].replace(",", "")) / 100.0
        except ValueError:
            pass

    # Currency: "$1,234.56" → 1234.56
    if value.startswith("$"):
        try:
            return float(value[1:].replace(",", ""))
        except ValueError:
            pass

    # Integer
    cleaned = value.replace(",", "")
    try:
        return int(cleaned)
    except ValueError:
        pass

    # Float
    try:
        return float(cleaned)
    except ValueError:
        pass

    # Date: M/D/YYYY → YYYY-MM-DD
    for fmt in ("%m/%d/%Y", "%d/%m/%Y", "%m/%d/%y"):
        try:
            dt = datetime.strptime(value, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue

    # Already ISO date or plain text — return as-is
    return value


def transform_row(headers: list[str], row: list[Any]) -> dict[str, Any]:
    """Transform a Google Sheets row into an Airtable record dict.

    All columns from the sheet are included (no hardcoded field list).
    Values are auto-coerced to appropriate types.
    """
    record = {}
    for i, header in enumerate(headers):
        if i >= len(row):
            continue
        raw = row[i]
        coerced = _coerce_value(raw)
        if coerced is not None:
            record[header] = coerced
    return record


# ---------------------------------------------------------------------------
# Airtable operations
# ---------------------------------------------------------------------------


def _make_key(record_fields: dict, key_fields: list[str]) -> tuple:
    """Build a hashable composite key from a record's fields."""
    return tuple(str(record_fields.get(k, "")).strip().lower() for k in key_fields)


def fetch_existing_records(table) -> list[dict]:
    """Fetch all existing records from Airtable."""
    logger.info("Fetching existing Airtable records...")
    records = retry_operation(table.all)
    logger.info("Found %d existing records", len(records))
    return records


def delete_all_records(table) -> int:
    """Delete all existing records from the Airtable table."""
    all_records = fetch_existing_records(table)
    record_ids = [r["id"] for r in all_records]

    if not record_ids:
        logger.info("No existing records to delete")
        return 0

    logger.info("Deleting %d existing records...", len(record_ids))
    deleted = 0
    for i in range(0, len(record_ids), BATCH_SIZE):
        batch = record_ids[i : i + BATCH_SIZE]
        retry_operation(table.batch_delete, batch)
        deleted += len(batch)
        if deleted % 100 == 0 or deleted == len(record_ids):
            logger.info("Deleted %d/%d records", deleted, len(record_ids))
        time.sleep(BATCH_SLEEP)

    return deleted


def create_records_batch(table, records: list[dict]) -> int:
    """Create records in Airtable in batches with rate-limit sleeps."""
    total = len(records)
    created = 0

    logger.info("Creating %d records in batches of %d...", total, BATCH_SIZE)

    for i in range(0, total, BATCH_SIZE):
        batch = records[i : i + BATCH_SIZE]
        retry_operation(table.batch_create, batch)
        created += len(batch)

        if created % 100 == 0 or created == total:
            logger.info("Progress: %d/%d records (%.1f%%)", created, total, created / total * 100)

        time.sleep(BATCH_SLEEP)

    return created


def upsert_records(table, new_records: list[dict], key_fields: list[str]) -> dict:
    """Upsert: update existing records that match on key_fields, insert new ones.

    Returns dict with counts: created, updated, skipped (unchanged).
    """
    existing = fetch_existing_records(table)

    # Build lookup: composite key → {airtable_record_id, fields}
    existing_by_key = {}
    for rec in existing:
        key = _make_key(rec["fields"], key_fields)
        existing_by_key[key] = {"id": rec["id"], "fields": rec["fields"]}

    to_create = []
    to_update = []
    skipped = 0

    for record in new_records:
        key = _make_key(record, key_fields)
        match = existing_by_key.pop(key, None)

        if match is None:
            # New record — insert
            to_create.append(record)
        else:
            # Existing record — check if anything changed
            changed = False
            for field, value in record.items():
                existing_val = match["fields"].get(field)
                # Normalize for comparison (Airtable may return slightly different types)
                if str(value).strip() != str(existing_val).strip() if existing_val is not None else value is not None:
                    changed = True
                    break

            if changed:
                to_update.append({"id": match["id"], "fields": record})
            else:
                skipped += 1

    logger.info(
        "Upsert plan: %d to create, %d to update, %d unchanged (skipped)",
        len(to_create), len(to_update), skipped,
    )

    # Batch create new records
    created = 0
    for i in range(0, len(to_create), BATCH_SIZE):
        batch = to_create[i : i + BATCH_SIZE]
        retry_operation(table.batch_create, batch)
        created += len(batch)
        if created % 100 == 0 or created == len(to_create):
            logger.info("Created %d/%d new records", created, len(to_create))
        time.sleep(BATCH_SLEEP)

    # Batch update existing records
    updated = 0
    for i in range(0, len(to_update), BATCH_SIZE):
        batch = to_update[i : i + BATCH_SIZE]
        retry_operation(table.batch_update, batch)
        updated += len(batch)
        if updated % 100 == 0 or updated == len(to_update):
            logger.info("Updated %d/%d existing records", updated, len(to_update))
        time.sleep(BATCH_SLEEP)

    return {"created": created, "updated": updated, "skipped": skipped}


# ---------------------------------------------------------------------------
# Main sync
# ---------------------------------------------------------------------------


def sync() -> dict[str, Any]:
    """Run the sync: read Google Sheets → upsert or replace Airtable records.

    Returns a stats dict.
    """
    start_time = datetime.now()
    stats = {
        "start_time": start_time.isoformat(),
        "sheet_id": GOOGLE_SHEET_ID,
        "sheet_tab": GOOGLE_SHEET_TAB,
        "airtable_base": AIRTABLE_BASE_ID,
        "airtable_table": AIRTABLE_TABLE_ID,
        "sync_mode": SYNC_MODE,
        "rows_read": 0,
        "records_deleted": 0,
        "records_created": 0,
        "records_updated": 0,
        "records_skipped": 0,
        "errors": [],
        "success": False,
    }

    try:
        logger.info("=" * 60)
        logger.info("Google Sheets → Airtable Sync")
        logger.info("  Sheet: %s / %s", GOOGLE_SHEET_ID, GOOGLE_SHEET_TAB)
        logger.info("  Airtable: %s / %s", AIRTABLE_BASE_ID, AIRTABLE_TABLE_ID)
        logger.info("  Mode: %s", SYNC_MODE)
        if SYNC_KEY_FIELDS:
            logger.info("  Key fields: %s", ", ".join(SYNC_KEY_FIELDS))
        logger.info("=" * 60)

        # Validate required config
        missing = []
        for var in ("GOOGLE_SHEET_ID", "GOOGLE_SHEET_TAB", "AIRTABLE_BASE_ID", "AIRTABLE_TABLE_ID", "AIRTABLE_PAT"):
            if not os.getenv(var):
                missing.append(var)
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

        if SYNC_MODE == "upsert" and not SYNC_KEY_FIELDS:
            raise ValueError(
                "SYNC_KEY_FIELDS is required when SYNC_MODE=upsert. "
                "Set it to a comma-separated list of field names (e.g. 'type,name')."
            )

        # Initialize clients
        gs_client = get_google_sheets_client()
        airtable_table = get_airtable_table()

        # Read from Google Sheets
        headers, rows = read_google_sheet(gs_client)
        stats["rows_read"] = len(rows)

        # Transform data
        logger.info("Transforming data...")
        records = []
        for i, row in enumerate(rows):
            try:
                record = transform_row(headers, row)
                if record:
                    records.append(record)
            except Exception as e:
                logger.warning("Error transforming row %d: %s", i + 2, e)
                stats["errors"].append(f"Row {i + 2}: {e}")

        logger.info("Transformed %d valid records", len(records))

        if not records:
            raise ValueError("No valid records to sync")

        if SYNC_MODE == "upsert":
            result = upsert_records(airtable_table, records, SYNC_KEY_FIELDS)
            stats["records_created"] = result["created"]
            stats["records_updated"] = result["updated"]
            stats["records_skipped"] = result["skipped"]
        else:
            # Full replace mode
            stats["records_deleted"] = delete_all_records(airtable_table)
            stats["records_created"] = create_records_batch(airtable_table, records)

        stats["success"] = True

    except Exception as e:
        logger.error("Sync failed: %s", e)
        stats["errors"].append(str(e))
        raise

    finally:
        end_time = datetime.now()
        stats["end_time"] = end_time.isoformat()
        stats["duration_seconds"] = (end_time - start_time).total_seconds()

        logger.info("=" * 60)
        logger.info("Sync Summary")
        logger.info("=" * 60)
        logger.info("  Mode:            %s", SYNC_MODE)
        logger.info("  Duration:        %.1f seconds", stats["duration_seconds"])
        logger.info("  Rows read:       %d", stats["rows_read"])
        if SYNC_MODE == "upsert":
            logger.info("  Records created: %d", stats["records_created"])
            logger.info("  Records updated: %d", stats["records_updated"])
            logger.info("  Records skipped: %d (unchanged)", stats["records_skipped"])
        else:
            logger.info("  Records deleted: %d", stats["records_deleted"])
            logger.info("  Records created: %d", stats["records_created"])
        logger.info("  Errors:          %d", len(stats["errors"]))
        logger.info("  Success:         %s", stats["success"])
        logger.info("=" * 60)

    return stats


def main():
    try:
        stats = sync()
        if not stats["success"]:
            sys.exit(1)
    except Exception as e:
        logger.error("Fatal error: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
