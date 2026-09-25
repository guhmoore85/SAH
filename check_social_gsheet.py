#!/usr/bin/env python3
"""
Diagnostic: inspect the Google Sheet someone believes feeds the
SAH_Social_2026 Airtable base's Combined_social table.

Not referenced anywhere else in this repo. Tries the same Google service
account already used for the GA4 sheet sync -- if it hasn't been shared
with that account, this will fail with a clear permission error rather
than silently guessing at contents.

Usage:
    python check_social_gsheet.py
"""

import json
import os

import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()

SHEET_ID = os.getenv("SOCIAL_GOOGLE_SHEET_ID", "1h2t_g0pUGsH-GhALjeXLSvzm_yu2Q9eAhpgSuReBu5w")
TARGET_GID = os.getenv("SOCIAL_GOOGLE_SHEET_GID", "301738696")


def get_client() -> tuple[gspread.Client, str]:
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ]
    json_creds = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if json_creds:
        creds_dict = json.loads(json_creds)
        credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        return gspread.authorize(credentials), creds_dict.get("client_email", "unknown")

    creds_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    if creds_file:
        credentials = Credentials.from_service_account_file(creds_file, scopes=scopes)
        with open(creds_file) as f:
            client_email = json.load(f).get("client_email", "unknown")
        return gspread.authorize(credentials), client_email

    raise RuntimeError("Set GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_FILE")


def main() -> None:
    client, client_email = get_client()
    print(f"Service account: {client_email}")

    print(f"Opening sheet: {SHEET_ID}")
    try:
        sheet = client.open_by_key(SHEET_ID)
    except PermissionError:
        print()
        print("PERMISSION DENIED. Share this sheet (Editor or Viewer) with:")
        print(f"  {client_email}")
        raise
    print(f"Title: {sheet.title}")
    print()

    print("=" * 70)
    print("All tabs")
    print("=" * 70)
    target_ws = None
    for ws in sheet.worksheets():
        marker = " <-- target gid" if str(ws.id) == str(TARGET_GID) else ""
        print(f"  gid={ws.id}  '{ws.title}'  ({ws.row_count} rows x {ws.col_count} cols){marker}")
        if str(ws.id) == str(TARGET_GID):
            target_ws = ws
    print()

    if target_ws is None:
        print(f"No tab found with gid={TARGET_GID}")
        return

    print("=" * 70)
    print(f"Header row + first 3 data rows of '{target_ws.title}'")
    print("=" * 70)
    values = target_ws.get_values("A1:Z4")
    for row in values:
        print(f"  {row}")


if __name__ == "__main__":
    main()
