# Google Sheets to Airtable Sync

Syncs GA4 analytics data from Google Sheets to Airtable on a daily schedule.

## Overview

- **Source**: Google Sheets (`Airtable_Active_365` tab)
- **Destination**: Airtable table
- **Schedule**: Daily at 6 AM UTC (via GitHub Actions)
- **Records**: ~40,000 rows

## Setup Instructions

### Step 1: Create Google Cloud Service Account

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select existing one
3. Enable the **Google Sheets API**:
   - Go to "APIs & Services" > "Library"
   - Search for "Google Sheets API"
   - Click "Enable"
4. Create a Service Account:
   - Go to "APIs & Services" > "Credentials"
   - Click "Create Credentials" > "Service Account"
   - Name it (e.g., `sheets-reader`)
   - Click "Create and Continue"
   - Skip the optional steps, click "Done"
5. Create a JSON key:
   - Click on the service account you just created
   - Go to "Keys" tab
   - Click "Add Key" > "Create new key"
   - Select "JSON" and click "Create"
   - Save the downloaded file securely
6. Share the Google Sheet:
   - Copy the service account email (looks like `name@project.iam.gserviceaccount.com`)
   - Open your Google Sheet
   - Click "Share" and add the service account email as a Viewer

### Step 2: Get Airtable Personal Access Token

1. Go to [Airtable Developer Hub](https://airtable.com/create/tokens)
2. Click "Create new token"
3. Name it (e.g., `sheets-sync`)
4. Add scopes:
   - `data.records:read`
   - `data.records:write`
   - `schema.bases:read`
5. Add access to your base
6. Click "Create token"
7. Copy and save the token securely

### Step 3: Local Testing

1. Clone this repository:
   ```bash
   git clone <repo-url>
   cd <repo-name>
   ```

2. Create virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Create `.env` file from example:
   ```bash
   cp .env.example .env
   ```

5. Edit `.env` with your credentials:
   ```bash
   # Set path to your service account JSON file
   GOOGLE_SERVICE_ACCOUNT_FILE=/path/to/your/service-account.json

   # Set your Airtable PAT
   AIRTABLE_PAT=pat.xxxxxxxxxxxxx
   ```

6. Test with limited rows first:
   ```bash
   ROW_LIMIT=100 python sync_ga4_airtable.py
   ```

7. Run full sync:
   ```bash
   python sync_ga4_airtable.py
   ```

### Step 4: GitHub Actions Setup

1. Go to your GitHub repository Settings > Secrets and variables > Actions

2. Add these **Secrets**:
   - `GOOGLE_SERVICE_ACCOUNT_JSON`: Paste the entire contents of your service account JSON file
   - `AIRTABLE_PAT`: Your Airtable Personal Access Token
   - `AIRTABLE_BASE_ID`: `appdZ68evU4qlG8mg` (or use Variables if not sensitive)
   - `AIRTABLE_TABLE_ID`: `tbl15Hfjy2CZTqgV6` (or use Variables if not sensitive)

3. Add these **Variables** (optional, have defaults):
   - `GOOGLE_SHEET_ID`: `12teYXqd8kBIFRorrndQMmYIy0gp2D5FLhfeq-p8-l5I`
   - `GOOGLE_SHEET_TAB`: `Airtable_Active_365`

4. Test manually:
   - Go to Actions tab
   - Select "Sync Google Sheets to Airtable" workflow
   - Click "Run workflow"
   - Optionally set `row_limit` to test with fewer rows

5. The workflow runs automatically daily at 6 AM UTC

## Data Schema

| Field | Type | Notes |
|-------|------|-------|
| date | Date | YYYY-MM-DD format |
| dimension_type | Text | Location, Campaign, Page, Device |
| dimension_value | Text | |
| users | Integer | |
| new_users | Integer | Can be null |
| sessions | Integer | Can be null |
| engagement_rate | Decimal | Can be null |
| key_events | Integer | |
| user_engagement_duration | Integer | Can be null, Pages only |
| year | Integer | |
| month | Integer | |
| week_of_year | Integer | |
| month_name | Text | |
| day_of_week | Text | |
| days_ago | Integer | |

## How It Works

1. **Delete**: Removes all existing records from Airtable
2. **Read**: Fetches all rows from Google Sheets
3. **Transform**: Converts data types (dates, numbers)
4. **Insert**: Batch creates records in Airtable (10 at a time)

## Troubleshooting

### "Google credentials not found"
- Ensure `GOOGLE_SERVICE_ACCOUNT_FILE` points to valid JSON file, OR
- Ensure `GOOGLE_SERVICE_ACCOUNT_JSON` contains the full JSON content

### "403 Forbidden" from Google Sheets
- Verify the Google Sheet is shared with your service account email
- Check that Google Sheets API is enabled in your project

### "INVALID_PERMISSIONS" from Airtable
- Verify your PAT has the required scopes
- Ensure the PAT has access to the specific base

### Rate limiting
- The script includes retry logic with exponential backoff
- For very large datasets, consider adding delays between batches

## Manual Sync

To run a manual sync:

```bash
# Full sync
python sync_ga4_airtable.py

# Test with 100 rows
ROW_LIMIT=100 python sync_ga4_airtable.py
```

## Modifying the Schedule

Edit `.github/workflows/sync.yml` and change the cron expression:

```yaml
schedule:
  - cron: '0 6 * * *'  # 6 AM UTC daily
```

Common cron patterns:
- `0 6 * * *` - Daily at 6 AM UTC
- `0 */6 * * *` - Every 6 hours
- `0 6 * * 1` - Weekly on Monday at 6 AM UTC
