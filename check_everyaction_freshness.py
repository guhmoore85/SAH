#!/usr/bin/env python3
"""
EveryAction + Google Sheets Freshness Checker

Diagnostic script: checks how current the EveryAction data is at each layer
of the pipeline, and how current the Google Sheets fed from BigQuery are:
    Gmail CSV loads (everyaction_reports.*) -> dbt everyaction_combined view
        -> SAH_EveryAction_Live_2026 'everyaction_master' tab -> Airtable
    dbt social_media.cross_channel_all_items -> Social_Master_2026 tab

A green sync job only proves the job ran; a Connected Sheets tab that never
refreshes, or one capped at a fixed row count, still feeds the Airtable sync
the same rows every day. This prints the BigQuery side and the sheet side
next to each other so the gap is visible.

Usage:
    python check_everyaction_freshness.py

Reads:
    GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_FILE
    BQ_PROJECT (default: stopaapihate-472516)
    EVERYACTION_GOOGLE_SHEET_ID (default: SAH_EveryAction_Live_2026)
    SOCIAL_GOOGLE_SHEET_ID (default: Social_Master_2026)
"""

import json
import os
from collections import Counter
from datetime import date, datetime

import gspread
from google.cloud import bigquery
from google.oauth2 import service_account
from dotenv import load_dotenv

load_dotenv()

PROJECT = os.getenv("BQ_PROJECT", "stopaapihate-472516")
EA_SHEET_ID = os.getenv("EVERYACTION_GOOGLE_SHEET_ID") or "15WbyrlRvtLIeE8SMghSFHS-rhW7bSkDkssc1Q7cLy2I"
EA_TAB = os.getenv("EVERYACTION_GOOGLE_SHEET_TAB", "everyaction_master")
SOCIAL_SHEET_ID = os.getenv("SOCIAL_GOOGLE_SHEET_ID") or "1h2t_g0pUGsH-GhALjeXLSvzm_yu2Q9eAhpgSuReBu5w"

SCOPES = [
    "https://www.googleapis.com/auth/bigquery",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


def get_credentials() -> service_account.Credentials:
    json_str = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if json_str:
        return service_account.Credentials.from_service_account_info(json.loads(json_str), scopes=SCOPES)
    file_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    if file_path and os.path.isfile(file_path):
        return service_account.Credentials.from_service_account_file(file_path, scopes=SCOPES)
    raise RuntimeError("Set GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_FILE")


def _date_sql(col: str) -> str:
    """Parse an EveryAction CSV date column the same way the dbt staging models do."""
    return (f"COALESCE(SAFE.PARSE_DATE('%m/%d/%y', CAST({col} AS STRING)), "
            f"SAFE.PARSE_DATE('%m/%d/%Y', CAST({col} AS STRING)), SAFE_CAST({col} AS DATE))")


QUERIES = {
    "RAW: everyaction_reports.email_comparison (weekly Gmail CSV loads)": f"""
        SELECT MAX(_import_timestamp) AS last_import, COUNT(*) AS row_count,
               COUNT(DISTINCT email_name) AS distinct_emails
        FROM `{PROJECT}.everyaction_reports.email_comparison`
    """,
    "RAW: everyaction_reports.forms_report (weekly Gmail CSV loads)": f"""
        SELECT MAX(_import_timestamp) AS last_import, COUNT(*) AS row_count
        FROM `{PROJECT}.everyaction_reports.forms_report`
    """,
    "RAW: newest email in each Email Comparison Report load (is the lag in the report itself?)": f"""
        SELECT DATE(_import_timestamp) AS loaded_on, COUNT(*) AS emails,
               MAX({_date_sql('first_sent_date')}) AS newest_first_sent,
               MAX({_date_sql('last_sent_date')}) AS newest_last_sent
        FROM `{PROJECT}.everyaction_reports.email_comparison`
        GROUP BY loaded_on ORDER BY loaded_on DESC LIMIT 6
    """,
    "RAW: newest form activity in each Forms report load": f"""
        SELECT DATE(_import_timestamp) AS loaded_on, COUNT(*) AS forms,
               MAX({_date_sql('first_submission_date')}) AS newest_first_submission,
               MAX({_date_sql('last_submission_date')}) AS newest_last_submission
        FROM `{PROJECT}.everyaction_reports.forms_report`
        GROUP BY loaded_on ORDER BY loaded_on DESC LIMIT 6
    """,
    "RAW: daily list-count tables (max report_date)": f"""
        SELECT 'daily_full_list' AS tbl, MAX(report_date) AS max_report_date
        FROM `{PROJECT}.everyaction_reports.daily_full_list`
        UNION ALL
        SELECT 'sah_donors', MAX(report_date) FROM `{PROJECT}.everyaction_reports.sah_donors`
        UNION ALL
        SELECT 'daily_sah_365', MAX(report_date) FROM `{PROJECT}.everyaction_reports.daily_sah_365`
    """,
    "DBT: everyaction_reports.everyaction_combined, by type (what the EA sheet should show)": f"""
        SELECT type, COUNT(*) AS row_count, MAX(first_date) AS max_first_date,
               MAX(last_date) AS max_last_date
        FROM `{PROJECT}.everyaction_reports.everyaction_combined`
        GROUP BY type
        ORDER BY type
    """,
    "DBT: social_media.cross_channel_all_items, by channel_group (what the social sheet should show)": f"""
        SELECT channel_group, COUNT(*) AS row_count, MAX(date) AS max_date
        FROM `{PROJECT}.social_media.cross_channel_all_items`
        GROUP BY channel_group
        ORDER BY channel_group
    """,
}


def run_queries(client: bigquery.Client) -> None:
    for label, sql in QUERIES.items():
        print("=" * 70)
        print(label)
        print("=" * 70)
        try:
            rows = list(client.query(sql).result())
            if not rows:
                print("  (no rows returned)")
            for row in rows:
                print(f"  {dict(row)}")
        except Exception as e:
            print(f"  ERROR: {e}")
        print()


def describe_sheet(gc: gspread.Client, sheet_id: str) -> gspread.Spreadsheet | None:
    """Print every tab's type and size, plus Connected Sheets refresh state."""
    try:
        sheet = gc.open_by_key(sheet_id)
    except Exception as e:
        print(f"  ERROR opening sheet {sheet_id}: {e}")
        print()
        return None

    print("=" * 70)
    print(f"SHEET: {sheet.title} ({sheet_id})")
    print("=" * 70)
    meta = sheet.fetch_sheet_metadata(params={
        "fields": "dataSources,dataSourceSchedules,sheets(properties)",
    })
    for s in meta.get("sheets", []):
        props = s.get("properties", {})
        grid = props.get("gridProperties", {})
        line = (f"  tab {props.get('title')!r}: type={props.get('sheetType')} "
                f"{grid.get('rowCount')} rows x {grid.get('columnCount')} cols")
        ds_props = props.get("dataSourceSheetProperties")
        if ds_props:
            status = ds_props.get("dataExecutionStatus", {})
            line += (f" | connected sheet, dataSourceId={ds_props.get('dataSourceId')}, "
                     f"state={status.get('state')}, lastRefreshTime={status.get('lastRefreshTime')}")
            if status.get("errorMessage"):
                line += f", error={status.get('errorMessage')}"
        print(line)

    sources = meta.get("dataSources", [])
    for ds in sources:
        spec = ds.get("spec", {}).get("bigQuery", {})
        query = spec.get("querySpec", {}).get("rawQuery")
        table = spec.get("tableSpec")
        print(f"  dataSource {ds.get('dataSourceId')}: "
              f"{'query: ' + ' '.join(query.split())[:300] if query else 'table: ' + str(table)}")
    schedules = meta.get("dataSourceSchedules", [])
    if schedules:
        for sch in schedules:
            print(f"  refresh schedule: {sch}")
    else:
        print("  refresh schedule: NONE configured (only manual refreshes)")
    print()
    return sheet


def parse_sheet_date(value: str) -> date | None:
    """Sheets renders dates as M/D/YYYY; compare them as dates, not strings."""
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def describe_extract_tab(sheet: gspread.Spreadsheet, tab: str) -> None:
    """Show the extract's own refresh state and what rows it actually holds."""
    print("=" * 70)
    print(f"TAB CONTENTS: {sheet.title} / {tab}")
    print("=" * 70)
    try:
        meta = sheet.fetch_sheet_metadata(params={
            "ranges": f"'{tab}'!A1",
            "includeGridData": "true",
            "fields": "sheets(data(rowData(values(dataSourceTable))))",
        })
        for s in meta.get("sheets", []):
            for d in s.get("data", []):
                for r in d.get("rowData", []):
                    for v in r.get("values", []):
                        dst = v.get("dataSourceTable")
                        if dst:
                            print(f"  extract: rowLimit={dst.get('rowLimit')}, "
                                  f"dataSourceId={dst.get('dataSourceId')}, "
                                  f"status={dst.get('dataExecutionStatus')}")
    except Exception as e:
        print(f"  (could not read extract metadata: {e})")

    try:
        values = sheet.worksheet(tab).get_all_values()
    except Exception as e:
        print(f"  ERROR reading tab: {e}")
        print()
        return
    if not values:
        print("  (empty)")
        print()
        return
    header, rows = values[0], values[1:]
    print(f"  data rows: {len(rows)}")
    print(f"  header: {header}")
    col = {name: i for i, name in enumerate(header)}
    if "type" in col:
        print(f"  rows by type: {dict(Counter(r[col['type']] for r in rows if len(r) > col['type']))}")
    for date_col in ("first_date", "last_date"):
        if date_col in col:
            dates = sorted(filter(None, (parse_sheet_date(r[col[date_col]])
                                         for r in rows if len(r) > col[date_col])))
            if dates:
                print(f"  {date_col}: min={dates[0]} max={dates[-1]}")
    print()


def main() -> None:
    creds = get_credentials()
    run_queries(bigquery.Client(project=PROJECT, credentials=creds))

    gc = gspread.authorize(creds)
    ea_sheet = describe_sheet(gc, EA_SHEET_ID)
    if ea_sheet is not None:
        describe_extract_tab(ea_sheet, EA_TAB)
    describe_sheet(gc, SOCIAL_SHEET_ID)


if __name__ == "__main__":
    main()
