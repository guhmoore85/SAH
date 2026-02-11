#!/usr/bin/env python3
"""
One-time Gmail OAuth Authentication
=====================================
Run this script ONCE on a local machine with a browser to generate
a refresh token for the Gmail API.

Prerequisites:
    1. Create an OAuth 2.0 "Desktop app" credential in Google Cloud Console
    2. Download the JSON file and save it as oauth_credentials.json
       in the same directory as this script

Usage:
    pip install google-auth-oauthlib
    python authenticate_gmail.py

After running:
    - Copy the printed GMAIL_REFRESH_TOKEN value
    - Add it (along with GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET)
      as GitHub repository secrets
"""

import json
import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), "oauth_credentials.json")
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "gmail_token.json")


def main():
    if not os.path.isfile(CREDENTIALS_FILE):
        print(f"ERROR: {CREDENTIALS_FILE} not found.")
        print()
        print("Download your OAuth client credentials from:")
        print("  https://console.cloud.google.com/apis/credentials")
        print(f'and save the JSON file as "{CREDENTIALS_FILE}"')
        sys.exit(1)

    print("Starting OAuth flow — a browser window will open.")
    print("Log in as: hmoore@stopaapihate.org")
    print()

    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
    creds = flow.run_local_server(port=8080, prompt="consent", access_type="offline")

    # Save the full token for local testing
    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or SCOPES),
    }
    with open(TOKEN_FILE, "w") as f:
        json.dump(token_data, f, indent=2)
    print(f"Token saved to {TOKEN_FILE}")

    # Also read client_id/secret from the credentials file
    with open(CREDENTIALS_FILE) as f:
        oauth_info = json.load(f)
    # Handle both "installed" and "web" application types
    client_info = oauth_info.get("installed") or oauth_info.get("web", {})

    print()
    print("=" * 60)
    print("ADD THESE AS GITHUB REPOSITORY SECRETS:")
    print("=" * 60)
    print(f"  GMAIL_CLIENT_ID     = {client_info.get('client_id', creds.client_id)}")
    print(f"  GMAIL_CLIENT_SECRET = {client_info.get('client_secret', creds.client_secret)}")
    print(f"  GMAIL_REFRESH_TOKEN = {creds.refresh_token}")
    print("=" * 60)
    print()
    print("Go to: https://github.com/<org>/SAH/settings/secrets/actions")
    print("and add each of the three values above as a repository secret.")


if __name__ == "__main__":
    main()
