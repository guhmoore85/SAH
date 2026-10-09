#!/usr/bin/env python3
"""
BigQuery cross_channel_all_items -> Airtable "Digital_master" sync
====================================================================
Syncs the cross-channel executive dashboard data (Website/GA4, Social,
Email, Forms -- all of EveryAction + social + GA4 blended into one
table) directly from BigQuery into the Digital_master Airtable table.

Digital_master already existed with the right shape (date, channel,
subchannel, reach, contributions, amount_raised, etc.) but nothing in
this repo synced it -- it was a one-time manual export frozen at
2026-03-10. This replaces that with a live weekly pipeline, following
the same pattern as sync_combined_social_to_airtable.py: a rolling
DAYS_BACK window plus self-pruning upserts, since cross_channel_all_items
unions GA4 + social + email + forms and is far larger than a dashboard
window needs.

Digital_master's own field names don't match cross_channel_all_items's
column names 1:1 -- see map_row() below for the exact mapping and the
judgment calls made where there's no clean 1:1 source column.

Environment variables:
    GOOGLE_SERVICE_ACCOUNT_JSON / GOOGLE_SERVICE_ACCOUNT_FILE - BigQuery auth
    AIRTABLE_PAT                 - needs access granted to the target base
    AIRTABLE_BASE_ID             - default: appEYxbed1rP9wyUq (SAH_Social_2026)
    AIRTABLE_TABLE_ID            - default: tblvC0c2uFOgX5w54 (Digital_master)
    BQ_PROJECT                   - default: stopaapihate-472516
    SYNC_MODE                    - "upsert" (default, self-pruning) or "replace"
    DAYS_BACK                    - default: 180; rolling window on `date`
    ROW_LIMIT                    - (optional) limit rows for testing
    CREATE_MISSING_FIELDS        - "true" to create the Number fields in
                                   NEW_NUMBER_FIELDS that the table lacks

Usage:
    python sync_digital_master_to_airtable.py
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
# script's defaults (SAH_Social_2026 / Digital_master) before importing it.
os.environ.setdefault("AIRTABLE_BASE_ID", "appEYxbed1rP9wyUq")
os.environ.setdefault("AIRTABLE_TABLE_ID", "tblvC0c2uFOgX5w54")

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
    f.strip()
    for f in os.getenv("SYNC_KEY_FIELDS", "channel,subchannel,date,content_name").split(",")
    if f.strip()
]
ROW_LIMIT = int(os.getenv("ROW_LIMIT") or 0) or None
DAYS_BACK = int(os.getenv("DAYS_BACK") or 180)
CREATE_MISSING_FIELDS = os.getenv("CREATE_MISSING_FIELDS", "").lower() == "true"

# Fields map_row() writes that Digital_master didn't originally have, with
# their Number precision. Created only when CREATE_MISSING_FIELDS=true.
NEW_NUMBER_FIELDS = {
    "engagements": 0,
    "website_users": 0,
    "website_new_users": 0,
    "key_events": 0,
    "avg_engagement_seconds": 1,
    "impressions": 0,
    "likes": 0,
    "comments": 0,
    "shares": 0,
    "saves": 0,
    "clicks": 0,
    "video_views": 0,
    "avg_watch_time_seconds": 1,
}

# cross_channel_all_items's channel_group values (Social, Web, Email, Forms)
# don't match Digital_master's own channel values -- map them here rather
# than touching the BigQuery model, which other consumers may also read.
CHANNEL_GROUP_DISPLAY = {
    "Social": "Social Media",
    "Web": "Website",
    "Email": "Email Marketing",
    "Forms": "Forms & Actions",
}

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


def map_row(row: dict[str, Any]) -> dict[str, Any]:
    """Map one cross_channel_all_items row onto Digital_master's field names.

    Mappings that are a direct 1:1 rename: date, year, month, month_name,
    contributions.

    Judgment calls (no clean 1:1 source column in cross_channel_all_items):
      - channel: channel_group, translated via CHANNEL_GROUP_DISPLAY
        (Digital_master's own values are "Social Media"/"Website"/
        "Email Marketing"/"Forms & Actions", not the Social/Web/Email/Forms
        used elsewhere)
      - subchannel: ga4_dimension_type if set, else channel -- gives
        Page/Location/Campaign/Device for GA4 (matches the one sample
        row we inspected: channel=Website, subchannel=Page), but
        Instagram/Facebook/TikTok for social, Email for email, Forms
        for forms. item_type alone would collapse all social rows to
        the single value "Post", losing per-platform detail the old
        dashboard's channel filter likely needs
      - content_name: item_name
      - reach: reach_or_impressions
      - sessions: website_sessions (GA4-only; null elsewhere, matching
        the field's literal name)
      - website_users: website_users, passed through as-is (GA4-only).
        Not one of Digital_master's original 16 fields -- add a
        "website_users" (Number) field in Airtable for this to actually
        populate, same as engagements
      - new_followers_contacts: new_followers (social) + new_contacts
        (forms) added together -- the field name implies a single
        cross-channel "new audience" figure
      - engagement_rate_pct: engagement_rate, passed through as-is. Source
        note (marts.yml): GA4's version is already *100, social/email/
        form versions are raw 0-1 decimals -- that inconsistency is
        upstream in cross_channel_all_items itself, not introduced here
      - action_takers: clicks (closest existing cross-channel "took
        action" proxy: link clicks for social, key events for GA4,
        clicks for email; null for forms)
      - engagements: engagement, passed through as-is. Not one of
        Digital_master's original 16 fields -- add an "engagements"
        (Number) field in Airtable for this to actually populate;
        until then it's silently dropped like any other unknown field
      - impressions, likes, comments, shares, saves, clicks, video_views,
        avg_watch_time_seconds:
        the social columns, passed through as-is (social_impressions for
        impressions). clicks is also in action_takers for back-compat
      - website_new_users, key_events: GA4's new users and key events
      - avg_engagement_seconds: GA4 engagement time / users, computed
        here. Null on Device and Location rows, where GA4's reports
        don't include engagement time
      - Every field above that isn't one of Digital_master's original 16
        is dropped until the field exists in Airtable; CREATE_MISSING_FIELDS
        =true creates them as Number fields (see NEW_NUMBER_FIELDS)
      - amount_raised: revenue
      - avg_contribution: computed here (revenue / contributions) since
        cross_channel_all_items has no equivalent column
      - days_ago: computed here, not read from BigQuery
    """
    channel_group = row.get("channel_group")
    new_followers = row.get("new_followers") or 0
    new_contacts = row.get("new_contacts") or 0
    revenue = row.get("revenue")
    contributions = row.get("contributions")
    avg_contribution = None
    if revenue is not None and contributions:
        avg_contribution = revenue / contributions

    avg_engagement_seconds = None
    engagement_seconds = row.get("website_engagement_seconds")
    users = row.get("website_users")
    if engagement_seconds is not None and users:
        avg_engagement_seconds = round(engagement_seconds / users, 1)

    row_date = row.get("date")
    days_ago = None
    if isinstance(row_date, date):
        days_ago = (date.today() - row_date).days

    mapped = {
        "date": row_date,
        "channel": CHANNEL_GROUP_DISPLAY.get(channel_group, channel_group),
        "subchannel": row.get("ga4_dimension_type") or row.get("channel"),
        "content_name": row.get("item_name"),
        "reach": row.get("reach_or_impressions"),
        "new_followers_contacts": (new_followers + new_contacts) or None,
        "sessions": row.get("website_sessions"),
        "website_users": row.get("website_users"),
        "engagement_rate_pct": row.get("engagement_rate"),
        "action_takers": row.get("clicks"),
        "engagements": row.get("engagement"),
        "video_views": row.get("video_views"),
        "avg_watch_time_seconds": row.get("avg_watch_time_seconds"),
        "impressions": row.get("social_impressions"),
        "likes": row.get("likes"),
        "comments": row.get("comments"),
        "shares": row.get("shares"),
        "saves": row.get("saves"),
        "clicks": row.get("clicks") if channel_group == "Social" else None,
        "website_new_users": row.get("website_new_users"),
        "key_events": row.get("website_key_events"),
        "avg_engagement_seconds": avg_engagement_seconds,
        "contributions": contributions,
        "amount_raised": revenue,
        "year": row.get("year"),
        "month": row.get("month"),
        "month_name": row.get("month_name"),
        "days_ago": days_ago,
        "avg_contribution": avg_contribution,
    }
    return {k: _coerce_bq_value(v) for k, v in mapped.items() if v is not None}


def create_missing_fields(table) -> None:
    """Create any NEW_NUMBER_FIELDS missing from the table as Number fields."""
    existing = {f.name for f in table.schema(force=True).fields}
    for name, precision in NEW_NUMBER_FIELDS.items():
        if name in existing:
            continue
        logger.info("Creating Number field %r in Airtable", name)
        table.create_field(name, "number", options={"precision": precision})
    # Refresh the cached schema so the field filter in sync() sees the new fields
    table.schema(force=True)


def read_cross_channel_items(client: bigquery.Client) -> list[dict[str, Any]]:
    """Read rows from social_media.cross_channel_all_items within the last DAYS_BACK days."""
    limit_clause = f"LIMIT {ROW_LIMIT}" if ROW_LIMIT else ""
    query = f"""
        SELECT *
        FROM `{BQ_PROJECT}.social_media.cross_channel_all_items`
        WHERE date >= DATE_SUB(CURRENT_DATE(), INTERVAL {DAYS_BACK} DAY)
        {limit_clause}
    """
    logger.info(
        "Querying %s.social_media.cross_channel_all_items (last %d days)",
        BQ_PROJECT, DAYS_BACK,
    )
    rows = list(client.query(query).result())
    logger.info("Read %d rows", len(rows))

    records = [map_row(dict(row)) for row in rows]
    records = [r for r in records if r]
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
        "records_pruned": 0,
        "errors": [],
        "success": False,
    }

    try:
        logger.info("=" * 60)
        logger.info("BigQuery cross_channel_all_items -> Airtable Digital_master Sync")
        logger.info("  Mode: %s", SYNC_MODE)
        if SYNC_KEY_FIELDS:
            logger.info("  Key fields: %s", ", ".join(SYNC_KEY_FIELDS))
        logger.info("=" * 60)

        bq_client = get_bigquery_client()
        airtable_table = get_airtable_table()

        records = read_cross_channel_items(bq_client)
        stats["rows_read"] = len(records)

        if not records:
            raise ValueError("No records read from BigQuery")

        if CREATE_MISSING_FIELDS:
            create_missing_fields(airtable_table)

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

        # typecast=True lets Airtable auto-add missing single-select options
        # (e.g. a new subchannel value). prune_stale=True deletes records
        # that have aged out of the DAYS_BACK window, keeping the table
        # bounded the same way the combined social sync does.
        if SYNC_MODE == "upsert":
            result = upsert_records(
                airtable_table, records, SYNC_KEY_FIELDS, typecast=True, prune_stale=True
            )
            stats["records_created"] = result["created"]
            stats["records_updated"] = result["updated"]
            stats["records_skipped"] = result["skipped"]
            stats["records_pruned"] = result["pruned"]
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
            logger.info("  Records pruned:  %d (aged out of window)", stats["records_pruned"])
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
