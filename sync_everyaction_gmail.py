#!/usr/bin/env python3
"""
EveryAction Gmail → BigQuery Sync
===================================
Downloads CSV report attachments from Gmail (sent by EveryAction)
and loads them into BigQuery with append mode and duplicate tracking.

Authentication:
    - Gmail: OAuth 2.0 refresh token (user consent, no admin required)
    - BigQuery: Service account credentials

Usage:
    python sync_everyaction_gmail.py --mode daily
    python sync_everyaction_gmail.py --mode weekly
    python sync_everyaction_gmail.py --mode all
"""

import argparse
import base64
import csv
import io
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.cloud import bigquery
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

BQ_PROJECT = os.getenv("BQ_PROJECT", "stopaapihate-472516")
BQ_DATASET = os.getenv("BQ_DATASET", "everyaction_reports")
SENDER_EMAIL = "noreply@everyaction.com"

# Label applied to processed emails so they are not re-processed.
PROCESSED_LABEL = os.getenv("GMAIL_PROCESSED_LABEL", "BQ-Processed")

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
BQ_SCOPES = ["https://www.googleapis.com/auth/bigquery"]

# Google OAuth token endpoint
TOKEN_URI = "https://oauth2.googleapis.com/token"

# Report definitions ---------------------------------------------------------
REPORT_CONFIGS = {
    "daily_full_list": {
        "subject_pattern": "EveryAction Scheduled Report - Daily Full List Count",
        "filename_pattern": r"Daily_Full_L_.*\.csv",
        "table": "daily_full_list",
        "schedule": "daily",
    },
    "sah_donors": {
        "subject_pattern": "EveryAction Scheduled Report - SAH Donors Data",
        "filename_pattern": r"SAH_Donors_D_.*\.csv",
        "table": "sah_donors",
        "schedule": "daily",
    },
    "daily_sah_365": {
        "subject_pattern": "EveryAction Scheduled Report - Daily SAH 365",
        "filename_pattern": r"Daily_SAH_36_.*\.csv",
        "table": "daily_sah_365",
        "schedule": "daily",
    },
    "email_comparison": {
        "subject_pattern": "EveryAction Scheduled Report - Email Comparison",
        "filename_pattern": r"Email_Compar_.*\.csv",
        "table": "email_comparison",
        "schedule": "weekly",
    },
    "forms_report": {
        "subject_pattern": "EveryAction Scheduled Report - Forms Report",
        "filename_pattern": r"Forms_report_.*\.csv",
        "table": "forms_report",
        "schedule": "weekly",
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
# Authentication helpers
# ---------------------------------------------------------------------------


def get_gmail_service():
    """Build an authorized Gmail API service using OAuth 2.0 refresh token.

    Reads credentials from environment variables:
        GMAIL_CLIENT_ID      - OAuth client ID
        GMAIL_CLIENT_SECRET  - OAuth client secret
        GMAIL_REFRESH_TOKEN  - Refresh token from authenticate_gmail.py
    """
    client_id = os.getenv("GMAIL_CLIENT_ID")
    client_secret = os.getenv("GMAIL_CLIENT_SECRET")
    refresh_token = os.getenv("GMAIL_REFRESH_TOKEN")

    if not all([client_id, client_secret, refresh_token]):
        raise RuntimeError(
            "Set GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, and GMAIL_REFRESH_TOKEN "
            "environment variables. Run authenticate_gmail.py to generate them."
        )

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=GMAIL_SCOPES,
    )

    # Force a token refresh to verify credentials work
    creds.refresh(Request())
    log.info("Gmail OAuth authentication successful")

    return build("gmail", "v1", credentials=creds)


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
    """Return an authenticated BigQuery client (service account)."""
    info = _load_service_account_info()
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=BQ_SCOPES
    )
    return bigquery.Client(project=BQ_PROJECT, credentials=creds)


# ---------------------------------------------------------------------------
# Gmail helpers
# ---------------------------------------------------------------------------


def _get_or_create_label(service, label_name: str) -> str:
    """Return the Gmail label ID for *label_name*, creating it if needed."""
    results = service.users().labels().list(userId="me").execute()
    for label in results.get("labels", []):
        if label["name"] == label_name:
            return label["id"]

    body = {
        "name": label_name,
        "labelListVisibility": "labelShow",
        "messageListVisibility": "show",
    }
    created = service.users().labels().create(userId="me", body=body).execute()
    log.info("Created Gmail label: %s (id=%s)", label_name, created["id"])
    return created["id"]


def search_emails(service, subject_pattern: str) -> list[dict]:
    """Search Gmail for unread messages from the EveryAction sender matching
    the given subject. Returns a list of message metadata dicts."""
    query = (
        f"from:{SENDER_EMAIL} "
        f"subject:({subject_pattern}) "
        f"-label:{PROCESSED_LABEL} "
        "has:attachment"
    )
    log.info("Gmail query: %s", query)

    messages: list[dict] = []
    page_token = None
    while True:
        resp = (
            service.users()
            .messages()
            .list(userId="me", q=query, pageToken=page_token)
            .execute()
        )
        messages.extend(resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    log.info("Found %d email(s) matching '%s'", len(messages), subject_pattern)
    return messages


def get_csv_attachment(service, msg_id: str, filename_re: str) -> tuple[str, bytes] | None:
    """Download the first CSV attachment whose name matches *filename_re*.

    Returns (filename, raw_bytes) or None.
    """
    msg = (
        service.users()
        .messages()
        .get(userId="me", id=msg_id, format="full")
        .execute()
    )
    parts = msg.get("payload", {}).get("parts", [])
    pattern = re.compile(filename_re, re.IGNORECASE)

    for part in parts:
        fname = part.get("filename", "")
        if not fname or not pattern.match(fname):
            continue
        att_id = part["body"].get("attachmentId")
        if not att_id:
            continue
        att = (
            service.users()
            .messages()
            .attachments()
            .get(userId="me", messageId=msg_id, id=att_id)
            .execute()
        )
        data = base64.urlsafe_b64decode(att["data"])
        return fname, data

    log.warning("No attachment matching '%s' in message %s", filename_re, msg_id)
    return None


def mark_processed(service, msg_id: str, label_id: str) -> None:
    """Add the processed label to *msg_id* so it won't be picked up again."""
    service.users().messages().modify(
        userId="me",
        id=msg_id,
        body={"addLabelIds": [label_id]},
    ).execute()
    log.info("Labeled message %s as processed", msg_id)


def get_email_date(service, msg_id: str) -> str:
    """Return the email received date as an ISO-8601 string."""
    msg = (
        service.users()
        .messages()
        .get(userId="me", id=msg_id, format="metadata", metadataHeaders=["Date"])
        .execute()
    )
    internal_ts_ms = int(msg.get("internalDate", 0))
    if internal_ts_ms:
        dt = datetime.fromtimestamp(internal_ts_ms / 1000, tz=timezone.utc)
        return dt.isoformat()
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# CSV → BigQuery
# ---------------------------------------------------------------------------


def _clean_column_name(raw: str) -> str:
    """Normalise a CSV header into a valid BigQuery column name."""
    name = raw.strip().lower()
    name = re.sub(r"[^a-z0-9_]", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    if name and name[0].isdigit():
        name = "_" + name
    return name or "col"


def _infer_bq_type(values: list[str]) -> str:
    """Guess a BigQuery type from sample string values.

    All numeric columns are mapped to FLOAT64 for consistency
    (EveryAction mixes integers and decimals across report runs).
    """
    # Take up to 100 non-empty samples
    samples = [v.strip() for v in values if v.strip()][:100]
    if not samples:
        return "STRING"

    # Check for numeric values (integers or floats / currency) → always FLOAT64
    num_re = re.compile(r"^-?\$?[\d,]*\.?\d+%?$")
    if all(num_re.match(s.replace(",", "")) for s in samples):
        return "FLOAT64"

    # Check for datetime with time component: M/D/YYYY H:MM:SS AM/PM
    datetime_re = re.compile(
        r"^\d{1,2}/\d{1,2}/\d{2,4}\s+\d{1,2}:\d{2}:\d{2}\s*[AaPp][Mm]$"
    )
    if all(datetime_re.match(s) for s in samples):
        return "TIMESTAMP"

    # Check for dates (YYYY-MM-DD or M/D/YYYY)
    date_re = re.compile(
        r"^(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})$"
    )
    if all(date_re.match(s) for s in samples):
        return "DATE"

    return "STRING"


def csv_to_rows(raw: bytes) -> tuple[list[str], list[dict]]:
    """Parse raw CSV bytes into (column_names, list_of_row_dicts).

    Handles BOM, different line endings, and common CSV quirks.
    """
    text = raw.decode("utf-8-sig")  # handles BOM
    reader = csv.DictReader(io.StringIO(text))
    raw_headers = reader.fieldnames or []
    clean_headers = [_clean_column_name(h) for h in raw_headers]

    # Build a mapping from raw header → clean header
    header_map = dict(zip(raw_headers, clean_headers))

    rows: list[dict] = []
    for raw_row in reader:
        row = {}
        for raw_key, clean_key in header_map.items():
            row[clean_key] = (raw_row.get(raw_key) or "").strip()
        rows.append(row)

    return clean_headers, rows


def ensure_table(
    client: bigquery.Client,
    table_id: str,
    columns: list[str],
    sample_rows: list[dict],
) -> bigquery.Table:
    """Create (or patch) a BigQuery table to fit the given columns."""
    full_id = f"{BQ_PROJECT}.{BQ_DATASET}.{table_id}"

    # Infer schema from sample data
    col_samples = {c: [] for c in columns}
    for row in sample_rows:
        for c in columns:
            col_samples[c].append(row.get(c, ""))

    schema = []
    for col in columns:
        bq_type = _infer_bq_type(col_samples[col])
        schema.append(bigquery.SchemaField(col, bq_type))

    # Always include metadata columns
    meta_fields = [
        bigquery.SchemaField("_import_timestamp", "TIMESTAMP"),
        bigquery.SchemaField("_source_filename", "STRING"),
        bigquery.SchemaField("_email_date", "STRING"),
        bigquery.SchemaField("_gmail_message_id", "STRING"),
    ]
    schema.extend(meta_fields)

    table = bigquery.Table(full_id, schema=schema)

    try:
        existing = client.get_table(full_id)
        # Merge schemas (add new columns, keep existing ones)
        existing_names = {f.name for f in existing.schema}
        merged = list(existing.schema)
        for field in schema:
            if field.name not in existing_names:
                merged.append(field)
        if len(merged) > len(existing.schema):
            existing.schema = merged
            client.update_table(existing, ["schema"])
            log.info("Updated schema for %s (added new columns)", full_id)
        return existing
    except Exception:
        client.create_table(table)
        log.info("Created table %s", full_id)
        return client.get_table(full_id)


def _coerce_value(value: str, bq_type: str):
    """Convert a CSV string value to the appropriate Python type for BQ."""
    if not value:
        return None
    if bq_type == "FLOAT64":
        try:
            cleaned = value.replace(",", "").replace("$", "").rstrip("%")
            return float(cleaned)
        except ValueError:
            return None
    if bq_type == "TIMESTAMP":
        # Handles "M/D/YYYY H:MM:SS AM/PM" from EveryAction
        for fmt in ("%m/%d/%Y %I:%M:%S %p", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(value, fmt).isoformat()
            except ValueError:
                continue
        return value
    if bq_type == "DATE":
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
            try:
                return datetime.strptime(value, fmt).date().isoformat()
            except ValueError:
                continue
        return value
    return value


def load_rows_to_bigquery(
    client: bigquery.Client,
    table_id: str,
    columns: list[str],
    rows: list[dict],
    source_filename: str,
    email_date: str,
    gmail_msg_id: str,
) -> int:
    """Load rows into BigQuery using the streaming insert / load job API.

    Returns the number of rows loaded.
    """
    if not rows:
        log.warning("No rows to load for table %s", table_id)
        return 0

    table = ensure_table(client, table_id, columns, rows)
    schema_map = {f.name: f.field_type for f in table.schema}

    now = datetime.now(timezone.utc).isoformat()

    bq_rows = []
    for row in rows:
        bq_row = {}
        for col in columns:
            bq_type = schema_map.get(col, "STRING")
            bq_row[col] = _coerce_value(row.get(col, ""), bq_type)
        bq_row["_import_timestamp"] = now
        bq_row["_source_filename"] = source_filename
        bq_row["_email_date"] = email_date
        bq_row["_gmail_message_id"] = gmail_msg_id
        bq_rows.append(bq_row)

    full_id = f"{BQ_PROJECT}.{BQ_DATASET}.{table_id}"

    # Use a load job via newline-delimited JSON for reliability at scale.
    ndjson = "\n".join(json.dumps(r) for r in bq_rows)
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        schema=table.schema,
    )
    job = client.load_table_from_file(
        io.BytesIO(ndjson.encode("utf-8")),
        full_id,
        job_config=job_config,
    )
    job.result()  # block until complete

    log.info("Loaded %d rows into %s", len(bq_rows), full_id)
    return len(bq_rows)


# ---------------------------------------------------------------------------
# Ensure BQ dataset exists
# ---------------------------------------------------------------------------


def ensure_dataset(client: bigquery.Client) -> None:
    """Create the BigQuery dataset if it does not already exist."""
    dataset_ref = bigquery.DatasetReference(BQ_PROJECT, BQ_DATASET)
    try:
        client.get_dataset(dataset_ref)
    except Exception:
        dataset = bigquery.Dataset(dataset_ref)
        dataset.location = "US"
        client.create_dataset(dataset)
        log.info("Created dataset %s.%s", BQ_PROJECT, BQ_DATASET)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def process_report(
    gmail_service,
    bq_client: bigquery.Client,
    report_key: str,
    config: dict,
    label_id: str,
) -> dict:
    """Process all unread emails for a single report type.

    Returns a summary dict with counts.
    """
    summary = {"report": report_key, "emails_found": 0, "rows_loaded": 0, "errors": []}

    messages = search_emails(gmail_service, config["subject_pattern"])
    summary["emails_found"] = len(messages)

    for msg_meta in messages:
        msg_id = msg_meta["id"]
        try:
            result = get_csv_attachment(
                gmail_service, msg_id, config["filename_pattern"]
            )
            if result is None:
                summary["errors"].append(f"No matching attachment in {msg_id}")
                continue

            filename, raw_csv = result
            email_date = get_email_date(gmail_service, msg_id)
            columns, rows = csv_to_rows(raw_csv)

            if not rows:
                log.warning("CSV %s from message %s has 0 data rows", filename, msg_id)
                summary["errors"].append(f"Empty CSV {filename}")
                continue

            log.info(
                "Processing %s: %d rows, %d columns", filename, len(rows), len(columns)
            )

            loaded = load_rows_to_bigquery(
                bq_client,
                config["table"],
                columns,
                rows,
                source_filename=filename,
                email_date=email_date,
                gmail_msg_id=msg_id,
            )
            summary["rows_loaded"] += loaded

            mark_processed(gmail_service, msg_id, label_id)

        except Exception as exc:
            log.error("Error processing message %s: %s", msg_id, exc, exc_info=True)
            summary["errors"].append(f"{msg_id}: {exc}")

    return summary


def run(mode: str) -> bool:
    """Run the pipeline.  *mode* is 'daily', 'weekly', or 'all'.

    Returns True if all reports succeeded with no errors.
    """
    log.info("=== EveryAction Gmail → BigQuery pipeline  |  mode=%s ===", mode)

    gmail_service = get_gmail_service()
    bq_client = get_bigquery_client()

    ensure_dataset(bq_client)
    label_id = _get_or_create_label(gmail_service, PROCESSED_LABEL)

    configs = {
        k: v
        for k, v in REPORT_CONFIGS.items()
        if mode == "all" or v["schedule"] == mode
    }

    if not configs:
        log.warning("No reports configured for mode '%s'", mode)
        return True

    log.info("Reports to process: %s", ", ".join(configs.keys()))

    summaries: list[dict] = []
    for key, cfg in configs.items():
        summary = process_report(gmail_service, bq_client, key, cfg, label_id)
        summaries.append(summary)

    # Print summary table
    log.info("=" * 60)
    log.info("PIPELINE SUMMARY")
    log.info("=" * 60)
    total_rows = 0
    total_errors = 0
    for s in summaries:
        status = "OK" if not s["errors"] else "ERRORS"
        log.info(
            "  %-25s  emails=%-3d  rows=%-6d  %s",
            s["report"],
            s["emails_found"],
            s["rows_loaded"],
            status,
        )
        if s["errors"]:
            for err in s["errors"]:
                log.error("    -> %s", err)
        total_rows += s["rows_loaded"]
        total_errors += len(s["errors"])

    log.info("-" * 60)
    log.info("Total rows loaded: %d  |  Total errors: %d", total_rows, total_errors)
    log.info("=" * 60)

    return total_errors == 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Sync EveryAction CSV reports from Gmail to BigQuery"
    )
    parser.add_argument(
        "--mode",
        choices=["daily", "weekly", "all"],
        default="all",
        help="Which reports to process (default: all)",
    )
    args = parser.parse_args()

    success = run(args.mode)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
