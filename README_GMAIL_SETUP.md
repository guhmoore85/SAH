# Gmail → BigQuery Pipeline Setup

This guide walks through setting up OAuth 2.0 authentication for the EveryAction Gmail → BigQuery sync pipeline.

## Overview

The pipeline uses two authentication methods:
- **Gmail**: OAuth 2.0 user consent (authenticates as hmoore@stopaapihate.org)
- **BigQuery**: Service account credentials (unchanged)

## Step 1: Create OAuth 2.0 Credentials

1. Go to [Google Cloud Console → Credentials](https://console.cloud.google.com/apis/credentials?project=stopaapihate-472516)
2. Click **Create Credentials** → **OAuth client ID**
3. Application type: **Desktop app**
4. Name: `EveryAction Gmail Sync`
5. Click **Create**
6. Click **Download JSON** and save the file as `oauth_credentials.json` in the project root

## Step 2: Enable the Gmail API

1. Go to [Gmail API Library](https://console.cloud.google.com/apis/library/gmail.googleapis.com?project=stopaapihate-472516)
2. Click **Enable** (if not already enabled)

## Step 3: Run the Authentication Script (One Time)

On your local machine with a browser:

```bash
# Install the dependency
pip install google-auth-oauthlib

# Run the authentication script
python authenticate_gmail.py
```

This will:
1. Open a browser window
2. Log in as **hmoore@stopaapihate.org**
3. Grant Gmail permissions (read/modify access)
4. Print three values: `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`

## Step 4: Add GitHub Repository Secrets

Go to your repository's **Settings → Secrets and variables → Actions** and add:

| Secret Name | Value | Source |
|---|---|---|
| `GMAIL_CLIENT_ID` | OAuth client ID | From authenticate_gmail.py output |
| `GMAIL_CLIENT_SECRET` | OAuth client secret | From authenticate_gmail.py output |
| `GMAIL_REFRESH_TOKEN` | OAuth refresh token | From authenticate_gmail.py output |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Service account JSON | (already configured for BigQuery) |

## Step 5: Verify

Trigger the workflow manually from **Actions → Gmail → BigQuery (Daily Reports) → Run workflow**.

Check the logs to confirm:
- `Gmail OAuth authentication successful` appears
- Emails are found and processed
- Rows are loaded into BigQuery

## How It Works in GitHub Actions

1. GitHub Actions reads `GMAIL_REFRESH_TOKEN` from secrets
2. The script exchanges the refresh token for an access token (no browser needed)
3. Connects to Gmail API as hmoore@stopaapihate.org
4. Processes emails and loads data to BigQuery

## Token Lifespan

- The refresh token **does not expire** as long as it is used regularly
- Daily/weekly pipeline runs keep the token fresh
- The token only expires if:
  - The user revokes access in [Google Account Settings](https://myaccount.google.com/permissions)
  - The user changes their Google password
  - 6+ months pass with no usage
  - The OAuth app is deleted from Google Cloud Console

For this use case (daily runs), authenticate once and it runs indefinitely.

## Files

| File | Purpose |
|---|---|
| `authenticate_gmail.py` | One-time local script to generate refresh token |
| `sync_everyaction_gmail.py` | Main pipeline script (runs in GitHub Actions) |
| `gmail_to_bigquery.py` | Previous version (service account auth, kept for reference) |

## Troubleshooting

**"Token has been expired or revoked"**
Re-run `authenticate_gmail.py` locally and update the `GMAIL_REFRESH_TOKEN` secret.

**"Access blocked: app has not been verified"**
During OAuth consent, click **Advanced** → **Go to EveryAction Gmail Sync (unsafe)**. This is expected for internal apps.

**"GMAIL_CLIENT_ID not set"**
Ensure all three Gmail secrets are set in GitHub repository settings.
