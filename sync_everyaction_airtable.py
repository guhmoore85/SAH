#!/usr/bin/env python3
"""
EveryAction → Airtable Sync
============================
Reads EveryAction report data from BigQuery and syncs it to Airtable.

This connects the existing EveryAction → BigQuery pipeline (via Gmail)
to Airtable, giving you a dashboard-friendly view of your EveryAction data.

Supports two sync modes:
    --mode summaries   Sync daily summary tables (daily_full_list, sah_donors,
                       daily_sah_365) into a single Airtable table.
    --mode details     Sync weekly detail tables (email_comparison, forms_report)
                       into separate Airtable tables.
    --mode all         Sync everything (default).

Usage:
    python sync_everyaction_airtable.py --mode all
    python sync_everyaction_airtable.py --mode summaries
    python sync_everyaction_airtable.py --mode details
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
from google.cloud import bigquery
from google.oauth2 import service_account
from pyairtable import Api

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

BQ_PROJECT = os.getenv("BQ_PROJECT", "stopaapihate-472516")
BQ_DATASET = os.getenv("BQ_DATASET", "everyaction_reports")

AIRTABLE_PAT = os.getenv("AIRTABLE_PAT")

# Airtable destination for daily summaries (single consolidated table)
EA_SUMMARIES_BASE_ID = os.getenv("EA_SUMMARIES_BASE_ID")
EA_SUMMARIES_TABLE_ID = os.getenv("EA_SUMMARIES_TABLE_ID")

# Airtable destinations for weekly detail reports (separate tables)
EA_EMAIL_COMPARISON_BASE_ID = os.getenv("EA_EMAIL_COMPARISON_BASE_ID")
EA_EMAIL_COMPARISON_TABLE_ID = os.getenv("EA_EMAIL_COMPARISON_TABLE_ID")

EA_FORMS_REPORT_BASE_ID = os.getenv("EA_FORMS_REPORT_BASE_ID")
EA_FORMS_REPORT_TABLE_ID = os.getenv("EA_FORMS_REPORT_TABLE_ID")

# Retry / batch configuration
BATCH_SIZE = 10  # Airtable API limit
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds

# BigQuery tables that store daily summaries
SUMMARY_TABLES = ["daily_full_list", "sah_donors", "daily_sah_365"]

# BigQuery tables that store full detail rows
DETAIL_TABLES = {
    "email_comparison": {
        "base_id_env": "EA_EMAIL_COMPARISON_BASE_ID",
        "table_id_env": "EA_EMAIL_COMPARISON_TABLE_ID",
    },
    "forms_report": {
        "base_id_env": "EA_FORMS_REPORT_BASE_ID",
        "table_id_env": "EA_FORMS_REPORT_TABLE_ID",
    },
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def _load_service_account_info() -> dict:
    """Return parsed service-account JSON from env var or file."""
    json_str = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if json_str:
        return json.loads(json_str)

    file_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    if file_path and os.path.isfile(file_path):
        with open(file_path) as f:
            return json.load(f)

    raise RuntimeError(
        "Set GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_FILE"
    )


def get_bigquery_client() -> bigquery.Client:
    """Return an authenticated BigQuery client."""
    info = _load_service_account_info()
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/bigquery"]
    )
    return bigquery.Client(project=BQ_PROJECT, credentials=creds)


def get_airtable_table(base_id: str, table_id: str):
    """Return a pyairtable Table object."""
    if not AIRTABLE_PAT:
        raise RuntimeError("AIRTABLE_PAT environment variable is required")
    api = Api(AIRTABLE_PAT)
    return api.table(base_id, table_id)


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
                log.warning(
                    "Attempt %d failed: %s. Retrying in %ds...",
                    attempt + 1, e, wait,
                )
                time.sleep(wait)
            else:
                log.error("All %d attempts failed", MAX_RETRIES)
    raise last_error


# ---------------------------------------------------------------------------
# BigQuery → rows
# ---------------------------------------------------------------------------


def query_summary_tables(bq_client: bigquery.Client) -> list[dict]:
    """Read all daily summary tables and return consolidated rows.

    Each row gets a ``report_name`` column so you can distinguish
    daily_full_list vs sah_donors vs daily_sah_365 in Airtable.
    """
    all_rows = []

    for table_name in SUMMARY_TABLES:
        full_table = f"{BQ_PROJECT}.{BQ_DATASET}.{table_name}"

        # Check if the table exists before querying
        try:
            bq_client.get_table(full_table)
        except Exception:
            log.warning("Table %s does not exist, skipping", full_table)
            continue

        query = f"""
            SELECT *
            FROM `{full_table}`
            ORDER BY report_date DESC
        """
        log.info("Querying %s ...", full_table)

        try:
            results = bq_client.query(query).result()
        except Exception as exc:
            log.error("Failed to query %s: %s", full_table, exc)
            continue

        count = 0
        for row in results:
            record = dict(row.items())
            record["report_name"] = table_name
            # Convert non-serialisable types
            for key, val in record.items():
                if isinstance(val, datetime):
                    record[key] = val.isoformat()
                elif hasattr(val, "isoformat"):
                    record[key] = val.isoformat()
            all_rows.append(record)
            count += 1

        log.info("  → %d rows from %s", count, table_name)

    return all_rows


def query_detail_table(bq_client: bigquery.Client, table_name: str) -> list[dict]:
    """Read a full-detail BigQuery table and return rows as dicts."""
    full_table = f"{BQ_PROJECT}.{BQ_DATASET}.{table_name}"

    try:
        bq_client.get_table(full_table)
    except Exception:
        log.warning("Table %s does not exist, skipping", full_table)
        return []

    query = f"SELECT * FROM `{full_table}` ORDER BY _import_timestamp DESC"
    log.info("Querying %s ...", full_table)

    rows = []
    for row in bq_client.query(query).result():
        record = dict(row.items())
        for key, val in record.items():
            if isinstance(val, datetime):
                record[key] = val.isoformat()
            elif hasattr(val, "isoformat"):
                record[key] = val.isoformat()
        rows.append(record)

    log.info("  → %d rows from %s", len(rows), table_name)
    return rows


# ---------------------------------------------------------------------------
# Airtable helpers
# ---------------------------------------------------------------------------


def _prepare_airtable_record(row: dict) -> dict:
    """Clean a BigQuery row dict for Airtable ingestion.

    - Removes internal metadata columns (prefixed with _)
    - Converts None to empty string (Airtable ignores None)
    - Truncates any string values to Airtable's 100k char limit
    """
    record = {}
    for key, val in row.items():
        if key.startswith("_"):
            continue
        if val is None:
            continue
        # Airtable field names can't exceed 255 chars
        field_name = key[:255]
        record[field_name] = val
    return record


def delete_all_records(table) -> int:
    """Delete all records from an Airtable table."""
    log.info("Fetching existing Airtable records for deletion...")
    all_records = retry_operation(table.all)
    record_ids = [r["id"] for r in all_records]

    if not record_ids:
        log.info("No existing records to delete")
        return 0

    log.info("Deleting %d existing records...", len(record_ids))
    deleted = 0
    for i in range(0, len(record_ids), BATCH_SIZE):
        batch = record_ids[i : i + BATCH_SIZE]
        retry_operation(table.batch_delete, batch)
        deleted += len(batch)
        if deleted % 100 == 0 or deleted == len(record_ids):
            log.info("  Deleted %d/%d", deleted, len(record_ids))

    return deleted


def create_records_batch(table, records: list[dict]) -> int:
    """Create records in Airtable in batches."""
    total = len(records)
    created = 0

    log.info("Creating %d records in batches of %d...", total, BATCH_SIZE)

    for i in range(0, total, BATCH_SIZE):
        batch = records[i : i + BATCH_SIZE]
        retry_operation(table.batch_create, batch)
        created += len(batch)

        if created % 100 == 0 or created == total:
            log.info("  Progress: %d/%d (%.1f%%)", created, total, created / total * 100)

    return created


def sync_rows_to_airtable(base_id: str, table_id: str, rows: list[dict], label: str) -> dict:
    """Full delete-and-replace sync of rows into an Airtable table.

    Returns a stats dict.
    """
    stats = {"label": label, "rows_from_bq": len(rows), "deleted": 0, "created": 0, "errors": []}

    if not base_id or not table_id:
        msg = f"Skipping {label}: Airtable base/table IDs not configured"
        log.warning(msg)
        stats["errors"].append(msg)
        return stats

    if not rows:
        log.info("No rows for %s — nothing to sync", label)
        return stats

    try:
        table = get_airtable_table(base_id, table_id)

        # Prepare records
        airtable_records = [_prepare_airtable_record(r) for r in rows]
        airtable_records = [r for r in airtable_records if r]  # drop empties

        # Delete existing, then create new
        stats["deleted"] = delete_all_records(table)
        stats["created"] = create_records_batch(table, airtable_records)

    except Exception as exc:
        log.error("Error syncing %s: %s", label, exc, exc_info=True)
        stats["errors"].append(str(exc))

    return stats


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def run(mode: str) -> bool:
    """Run the EveryAction → Airtable sync.

    Returns True on success.
    """
    log.info("=" * 60)
    log.info("EveryAction → Airtable Sync  |  mode=%s", mode)
    log.info("=" * 60)

    bq_client = get_bigquery_client()
    all_stats: list[dict] = []

    # --- Summaries ---
    if mode in ("all", "summaries"):
        log.info("--- Syncing daily summaries ---")
        summary_rows = query_summary_tables(bq_client)
        stats = sync_rows_to_airtable(
            EA_SUMMARIES_BASE_ID,
            EA_SUMMARIES_TABLE_ID,
            summary_rows,
            label="daily_summaries",
        )
        all_stats.append(stats)

    # --- Detail tables ---
    if mode in ("all", "details"):
        log.info("--- Syncing detail tables ---")
        for table_name, cfg in DETAIL_TABLES.items():
            base_id = os.getenv(cfg["base_id_env"])
            table_id = os.getenv(cfg["table_id_env"])
            rows = query_detail_table(bq_client, table_name)
            stats = sync_rows_to_airtable(base_id, table_id, rows, label=table_name)
            all_stats.append(stats)

    # --- Print summary ---
    log.info("=" * 60)
    log.info("SYNC SUMMARY")
    log.info("=" * 60)
    total_errors = 0
    for s in all_stats:
        status = "OK" if not s["errors"] else "ERRORS"
        log.info(
            "  %-25s  bq_rows=%-5d  deleted=%-5d  created=%-5d  %s",
            s["label"], s["rows_from_bq"], s["deleted"], s["created"], status,
        )
        for err in s["errors"]:
            log.error("    → %s", err)
        total_errors += len(s["errors"])

    log.info("=" * 60)
    return total_errors == 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Sync EveryAction data from BigQuery to Airtable"
    )
    parser.add_argument(
        "--mode",
        choices=["all", "summaries", "details"],
        default="all",
        help="Which tables to sync (default: all)",
    )
    args = parser.parse_args()

    success = run(args.mode)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
