#!/usr/bin/env python3
"""
Google Sheets to Airtable Sync Script

Syncs GA4 analytics data from Google Sheets to Airtable.
Designed to run daily via GitHub Actions or cron.
"""

import os
import sys
import json
import logging
import time
from datetime import datetime
from typing import Any

import gspread
from google.oauth2.service_account import Credentials
from pyairtable import Api, Table
from pyairtable.formulas import match
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# Configuration
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "12teYXqd8kBIFRorrndQMmYIy0gp2D5FLhfeq-p8-l5I")
GOOGLE_SHEET_TAB = os.getenv("GOOGLE_SHEET_TAB", "Airtable_Active_365")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID", "appdZ68evU4qlG8mg")
AIRTABLE_TABLE_ID = os.getenv("AIRTABLE_TABLE_ID", "tbl15Hfjy2CZTqgV6")
AIRTABLE_PAT = os.getenv("AIRTABLE_PAT")

# For testing: limit rows (set to None for full sync)
ROW_LIMIT = int(os.getenv("ROW_LIMIT", 0)) or None

# Airtable batch size (API limit is 10)
BATCH_SIZE = 10

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds

# Field definitions with Airtable types
FIELD_DEFINITIONS = {
    "date": {"type": "date"},
    "dimension_type": {"type": "singleLineText"},
    "dimension_value": {"type": "singleLineText"},
    "users": {"type": "number", "options": {"precision": 0}},
    "new_users": {"type": "number", "options": {"precision": 0}},
    "sessions": {"type": "number", "options": {"precision": 0}},
    "engagement_rate": {"type": "number", "options": {"precision": 8}},
    "key_events": {"type": "number", "options": {"precision": 0}},
    "user_engagement_duration": {"type": "number", "options": {"precision": 0}},
    "year": {"type": "number", "options": {"precision": 0}},
    "month": {"type": "number", "options": {"precision": 0}},
    "week_of_year": {"type": "number", "options": {"precision": 0}},
    "month_name": {"type": "singleLineText"},
    "day_of_week": {"type": "singleLineText"},
    "days_ago": {"type": "number", "options": {"precision": 0}},
}


def get_google_sheets_client() -> gspread.Client:
    """
    Initialize Google Sheets client using service account credentials.

    Credentials can be provided via:
    1. GOOGLE_SERVICE_ACCOUNT_JSON env var (JSON string)
    2. GOOGLE_SERVICE_ACCOUNT_FILE env var (path to JSON file)
    """
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ]

    # Try JSON string first (for GitHub Actions secrets)
    json_creds = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if json_creds:
        logger.info("Using Google credentials from GOOGLE_SERVICE_ACCOUNT_JSON")
        creds_dict = json.loads(json_creds)
        credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        return gspread.authorize(credentials)

    # Try file path
    creds_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    if creds_file:
        logger.info(f"Using Google credentials from file: {creds_file}")
        credentials = Credentials.from_service_account_file(creds_file, scopes=scopes)
        return gspread.authorize(credentials)

    raise ValueError(
        "Google credentials not found. Set GOOGLE_SERVICE_ACCOUNT_JSON or "
        "GOOGLE_SERVICE_ACCOUNT_FILE environment variable."
    )


def get_airtable_table() -> Table:
    """Initialize Airtable table client."""
    if not AIRTABLE_PAT:
        raise ValueError("AIRTABLE_PAT environment variable is required")

    api = Api(AIRTABLE_PAT)
    return api.table(AIRTABLE_BASE_ID, AIRTABLE_TABLE_ID)


def read_google_sheet(client: gspread.Client) -> tuple[list[str], list[list[Any]]]:
    """
    Read all data from Google Sheet.

    Returns:
        Tuple of (headers, rows)
    """
    logger.info(f"Opening Google Sheet: {GOOGLE_SHEET_ID}")
    sheet = client.open_by_key(GOOGLE_SHEET_ID)

    logger.info(f"Reading tab: {GOOGLE_SHEET_TAB}")
    worksheet = sheet.worksheet(GOOGLE_SHEET_TAB)

    # Get all values
    all_values = worksheet.get_all_values()

    if not all_values:
        raise ValueError("Google Sheet is empty")

    headers = all_values[0]
    rows = all_values[1:]

    if ROW_LIMIT:
        rows = rows[:ROW_LIMIT]
        logger.info(f"Row limit applied: {ROW_LIMIT} rows")

    logger.info(f"Read {len(rows)} rows with {len(headers)} columns")
    return headers, rows


def transform_row(headers: list[str], row: list[Any]) -> dict[str, Any]:
    """
    Transform a row from Google Sheets to Airtable format.

    Handles:
    - Date formatting
    - Null/empty value handling
    - Numeric type conversion
    """
    record = {}

    for i, header in enumerate(headers):
        if i >= len(row):
            value = None
        else:
            value = row[i]

        # Skip empty values
        if value == "" or value is None:
            continue

        field_def = FIELD_DEFINITIONS.get(header)
        if not field_def:
            # Unknown field, skip it
            continue

        field_type = field_def["type"]

        try:
            if field_type == "date":
                # Airtable expects ISO format: YYYY-MM-DD
                # Handle common date formats
                if isinstance(value, str):
                    # Try parsing different formats
                    for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"]:
                        try:
                            dt = datetime.strptime(value, fmt)
                            value = dt.strftime("%Y-%m-%d")
                            break
                        except ValueError:
                            continue
                record[header] = value

            elif field_type == "number":
                # Convert to number
                if isinstance(value, str):
                    value = value.replace(",", "").strip()
                    if value == "":
                        continue
                    # Check if it's a decimal
                    if "." in value:
                        value = float(value)
                    else:
                        value = int(value)
                elif isinstance(value, (int, float)):
                    pass  # Already a number
                else:
                    continue
                record[header] = value

            elif field_type == "singleLineText":
                record[header] = str(value)

            else:
                record[header] = value

        except (ValueError, TypeError) as e:
            logger.warning(f"Could not convert {header}={value}: {e}")
            continue

    return record


def retry_operation(operation, *args, **kwargs):
    """Execute an operation with retry logic."""
    last_error = None

    for attempt in range(MAX_RETRIES):
        try:
            return operation(*args, **kwargs)
        except Exception as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                wait_time = RETRY_DELAY * (2 ** attempt)
                logger.warning(f"Attempt {attempt + 1} failed: {e}. Retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                logger.error(f"All {MAX_RETRIES} attempts failed")

    raise last_error


def delete_all_records(table: Table) -> int:
    """
    Delete all existing records from Airtable table.

    Returns:
        Number of records deleted
    """
    logger.info("Fetching existing records for deletion...")

    # Get all record IDs
    all_records = retry_operation(table.all)
    record_ids = [r["id"] for r in all_records]

    if not record_ids:
        logger.info("No existing records to delete")
        return 0

    logger.info(f"Deleting {len(record_ids)} existing records...")

    # Delete in batches
    deleted = 0
    for i in range(0, len(record_ids), BATCH_SIZE):
        batch = record_ids[i : i + BATCH_SIZE]
        retry_operation(table.batch_delete, batch)
        deleted += len(batch)
        logger.info(f"Deleted {deleted}/{len(record_ids)} records")

    return deleted


def create_records_batch(table: Table, records: list[dict]) -> int:
    """
    Create records in Airtable in batches.

    Returns:
        Number of records created
    """
    total = len(records)
    created = 0

    logger.info(f"Creating {total} records in batches of {BATCH_SIZE}...")

    for i in range(0, total, BATCH_SIZE):
        batch = records[i : i + BATCH_SIZE]
        retry_operation(table.batch_create, batch)
        created += len(batch)

        # Progress update every 100 records or at the end
        if created % 100 == 0 or created == total:
            progress = (created / total) * 100
            logger.info(f"Progress: {created}/{total} records ({progress:.1f}%)")

    return created


def check_and_create_fields(table: Table) -> None:
    """
    Check if Airtable table has the required fields.
    If table is empty/new, create the fields.

    Note: Airtable API automatically creates fields when you insert records
    with new field names, but they default to single line text. For proper
    field types, you may need to configure them in the Airtable UI first,
    or use the Airtable metadata API (requires different permissions).
    """
    logger.info("Checking Airtable table schema...")

    # Try to get table schema using metadata API
    try:
        api = Api(AIRTABLE_PAT)
        base = api.base(AIRTABLE_BASE_ID)
        schema = base.schema()

        # Find our table in the schema
        table_schema = None
        for t in schema.tables:
            if t.id == AIRTABLE_TABLE_ID or t.name == AIRTABLE_TABLE_ID:
                table_schema = t
                break

        if table_schema:
            existing_fields = {f.name for f in table_schema.fields}
            logger.info(f"Existing fields: {existing_fields}")

            # Check for missing fields
            required_fields = set(FIELD_DEFINITIONS.keys())
            missing_fields = required_fields - existing_fields

            if missing_fields:
                logger.warning(f"Missing fields (will be auto-created as text): {missing_fields}")
                logger.warning("For proper field types, configure them in Airtable UI")
        else:
            logger.warning("Could not find table in schema")

    except Exception as e:
        logger.warning(f"Could not check table schema: {e}")
        logger.info("Fields will be auto-created when records are inserted")


def sync() -> dict[str, Any]:
    """
    Main sync function.

    Returns:
        Dictionary with sync statistics
    """
    start_time = datetime.now()
    stats = {
        "start_time": start_time.isoformat(),
        "rows_read": 0,
        "records_deleted": 0,
        "records_created": 0,
        "errors": [],
        "success": False,
    }

    try:
        logger.info("=" * 60)
        logger.info("Starting Google Sheets to Airtable Sync")
        logger.info("=" * 60)

        # Initialize clients
        logger.info("Initializing Google Sheets client...")
        gs_client = get_google_sheets_client()

        logger.info("Initializing Airtable client...")
        airtable_table = get_airtable_table()

        # Check table schema
        check_and_create_fields(airtable_table)

        # Read from Google Sheets
        headers, rows = read_google_sheet(gs_client)
        stats["rows_read"] = len(rows)

        # Transform data
        logger.info("Transforming data...")
        records = []
        for i, row in enumerate(rows):
            try:
                record = transform_row(headers, row)
                if record:  # Only add non-empty records
                    records.append(record)
            except Exception as e:
                logger.warning(f"Error transforming row {i + 2}: {e}")
                stats["errors"].append(f"Row {i + 2}: {str(e)}")

        logger.info(f"Transformed {len(records)} valid records")

        if not records:
            raise ValueError("No valid records to sync")

        # Delete existing records
        stats["records_deleted"] = delete_all_records(airtable_table)

        # Create new records
        stats["records_created"] = create_records_batch(airtable_table, records)

        stats["success"] = True

    except Exception as e:
        logger.error(f"Sync failed: {e}")
        stats["errors"].append(str(e))
        raise

    finally:
        end_time = datetime.now()
        stats["end_time"] = end_time.isoformat()
        stats["duration_seconds"] = (end_time - start_time).total_seconds()

        logger.info("=" * 60)
        logger.info("Sync Summary")
        logger.info("=" * 60)
        logger.info(f"Start time: {stats['start_time']}")
        logger.info(f"End time: {stats['end_time']}")
        logger.info(f"Duration: {stats['duration_seconds']:.1f} seconds")
        logger.info(f"Rows read: {stats['rows_read']}")
        logger.info(f"Records deleted: {stats['records_deleted']}")
        logger.info(f"Records created: {stats['records_created']}")
        logger.info(f"Errors: {len(stats['errors'])}")
        logger.info(f"Success: {stats['success']}")
        logger.info("=" * 60)

    return stats


def main():
    """Entry point."""
    try:
        stats = sync()
        if not stats["success"]:
            sys.exit(1)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
