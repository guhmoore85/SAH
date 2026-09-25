#!/usr/bin/env python3
"""
BigQuery combined_metrics_full -> Airtable "Combined_social" sync
====================================================================
Syncs Facebook/Instagram/TikTok post data directly from BigQuery's
social_media.combined_metrics_full table into Airtable, bypassing the
Google Sheet entirely.

That sheet's "cross_channel_all_items"/"combined_metrics" tabs turned out
to be live BigQuery Connected Sheets, not static exports -- Connected
Sheets aren't readable via the classic Sheets Values API (every read
attempt fails with "Unable to parse range" regardless of syntax), so
there's no reliable way to read them programmatically. combined_metrics_full
is the same underlying data, already fixed and current (GA4/Instagram/
TikTok as of today), and going BigQuery -> Airtable directly is one hop
instead of two.

Reuses the upsert/retry/field-filtering logic from sync_sheet_to_airtable.py
rather than duplicating it -- only the data source (BigQuery query instead
of a Google Sheet read) differs.

Environment variables:
    GOOGLE_SERVICE_ACCOUNT_JSON / GOOGLE_SERVICE_ACCOUNT_FILE - BigQuery auth
    AIRTABLE_PAT                 - needs access granted to the target base
    AIRTABLE_BASE_ID             - default: appEYxbed1rP9wyUq (SAH_Social_2026)
    AIRTABLE_TABLE_ID            - default: tbl5WCOhWD0c8tum5 (Combined_social)
    BQ_PROJECT                   - default: stopaapihate-472516
    SYNC_MODE                    - "upsert" (default) or "replace"
    SYNC_KEY_FIELDS               - default: platform,post_id
    ROW_LIMIT                    - (optional) limit rows for testing

Usage:
    python sync_combined_social_to_airtable.py
"""

import json
import logging
import os
import sys
from datetime import date, datetime
from typing import Any

from dotenv import load_dotenv
from google.cloud import bigquery
from google.oauth2 import service_account

load_dotenv()

# sync_sheet_to_airtable reads AIRTABLE_BASE_ID/AIRTABLE_TABLE_ID from the
# environment at import time with no defaults of its own -- set this
# script's defaults (SAH_Social_2026 / Combined_social) before importing it,
# so get_airtable_table() below points at the right base without requiring
# the caller to set them explicitly.
os.environ.setdefault("AIRTABLE_BASE_ID", "appEYxbed1rP9wyUq")
os.environ.setdefault("AIRTABLE_TABLE_ID", "tbl5WCOhWD0c8tum5")

from sync_sheet_to_airtable import (  # noqa: E402
    get_airtable_field_names,
    get_airtable_table,
    upsert_records,
    create_records_batch,
    delete_all_records,
)

BQ_PROJECT = os.getenv("BQ_PROJECT", "stopaapihate-472516")
SYNC_MODE = os.getenv("SYNC_MODE", "upsert").lower()
SYNC_KEY_FIELDS = [
    f.strip() for f in os.getenv("SYNC_KEY_FIELDS", "platform,post_id").split(",") if f.strip()
]
ROW_LIMIT = int(os.getenv("ROW_LIMIT") or 0) or None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def get_bigquery_client() -> bigquery.Client:
    json_creds = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if json_creds:
        info = json.loads(json_creds)
        creds = service_account.Credentials.from_service_account_info(info)
        return bigquery.Client(project=BQ_PROJECT, credentials=creds)

    creds_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    if creds_file:
        creds = service_account.Credentials.from_service_account_file(creds_file)
        return bigquery.Client(project=BQ_PROJECT, credentials=creds)

    raise ValueError("Set GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_FILE")


def _coerce_bq_value(value: Any) -> Any:
    """Make a BigQuery cell value JSON/Airtable safe."""
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def read_combined_metrics(client: bigquery.Client) -> list[dict[str, Any]]:
    """Read all rows from social_media.combined_metrics_full."""
    limit_clause = f"LIMIT {ROW_LIMIT}" if ROW_LIMIT else ""
    query = f"""
        SELECT *
        FROM `{BQ_PROJECT}.social_media.combined_metrics_full`
        {limit_clause}
    """
    logger.info("Querying %s.social_media.combined_metrics_full", BQ_PROJECT)
    rows = list(client.query(query).result())
    logger.info("Read %d rows", len(rows))

    records = []
    for row in rows:
        record = {k: _coerce_bq_value(v) for k, v in dict(row).items() if v is not None}
        if record:
            records.append(record)
    return records


def sync() -> dict[str, Any]:
    start_time = datetime.now()
    stats = {
        "start_time": start_time.isoformat(),
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
        logger.info("BigQuery combined_metrics_full -> Airtable Sync")
        logger.info("  Mode: %s", SYNC_MODE)
        if SYNC_KEY_FIELDS:
            logger.info("  Key fields: %s", ", ".join(SYNC_KEY_FIELDS))
        logger.info("=" * 60)

        bq_client = get_bigquery_client()
        airtable_table = get_airtable_table()

        records = read_combined_metrics(bq_client)
        stats["rows_read"] = len(records)

        if not records:
            raise ValueError("No records read from BigQuery")

        # Drop any fields that don't exist as columns in the Airtable table
        valid_fields = get_airtable_field_names(airtable_table)
        if valid_fields:
            all_fields = set()
            for r in records:
                all_fields.update(r.keys())
            drop_fields = all_fields - valid_fields
            if drop_fields:
                logger.warning("Dropping fields not present in Airtable: %s", ", ".join(sorted(drop_fields)))
                records = [{k: v for k, v in r.items() if k not in drop_fields} for r in records]

        # BigQuery source data can carry values (e.g. content_type values
        # like "Image") that don't yet exist as options on Airtable's
        # single/multi-select fields. typecast=True lets Airtable add them
        # automatically instead of rejecting the write.
        if SYNC_MODE == "upsert":
            result = upsert_records(airtable_table, records, SYNC_KEY_FIELDS, typecast=True)
            stats["records_created"] = result["created"]
            stats["records_updated"] = result["updated"]
            stats["records_skipped"] = result["skipped"]
        else:
            stats["records_deleted"] = delete_all_records(airtable_table)
            stats["records_created"] = create_records_batch(airtable_table, records, typecast=True)

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
