# EveryAction → Airtable Sync Setup Guide

## Pipeline Overview

```
EveryAction → BigQuery → Google Sheets (connector) → Airtable
                         (refreshes 6:00 AM UTC)      (syncs 6:30 AM UTC)
```

The GitHub Actions workflow `sync-airtable-everyaction.yml` runs the reusable
`sync_sheet_to_airtable.py` script daily, reading from a Google Sheet that is
populated by a BigQuery connected sheet, and writing to an Airtable table.

---

## Step 1: Google Sheets Setup

1. **Create a new Google Sheet** named `SAH_EveryAction_Live_2026`
2. **Share the sheet** with the service account:
   `sheets-reader@stopaapihate-472516.iam.gserviceaccount.com` (Viewer access)
3. **Add a BigQuery connected sheet** (Data → Data connectors → BigQuery):
   - Project: `stopaapihate-472516`
   - Use the following custom query:

```sql
SELECT
  type,
  name,
  first_date,
  last_date,
  days_active,
  year,
  month,
  month_name,
  conversion_rate,
  total_contributions,
  amount_raised,
  avg_contribution_amount,
  recipients,
  unique_open_rate,
  unique_click_rate,
  bounce_rate,
  unsubscribe_rate,
  submissions,
  views,
  form_type,
  form_status,
  recurring_commitments,
  new_contacts,
  recipient_list,
  excluded_list
FROM stopaapihate-472516.everyaction_reports.everyaction_combined
ORDER BY last_date DESC
```

4. **Schedule the query to refresh daily at 6:00 AM UTC**:
   - In the connected sheet, click the refresh icon → Scheduled refresh
   - Set to "Daily" at 6:00 AM UTC
5. **Rename the tab** to `everyaction_master`
6. **Copy the Sheet ID** from the URL:
   `https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit`
   - Sheet ID: `15WbyrlRvtLIeE8SMghSFHS-rhW7bSkDkssc1Q7cLy2I`

---

## Step 2: Airtable Setup

1. **Create a new Airtable base**: `SAH EveryAction Dashboard`
2. **Create a table**: `Campaigns`
3. **Configure field types** (see `docs/EVERYACTION_AIRTABLE_SCHEMA.md` for the
   full reference):

| Field | Airtable Type |
|-------|--------------|
| type | Single select |
| name | Single line text |
| first_date | Date |
| last_date | Date |
| days_active | Number (integer) |
| year | Number (integer) |
| month | Number (integer) |
| month_name | Single line text |
| conversion_rate | Percent |
| total_contributions | Number (integer) |
| amount_raised | Currency |
| avg_contribution_amount | Currency |
| recipients | Number (integer) |
| unique_open_rate | Percent |
| unique_click_rate | Percent |
| bounce_rate | Percent |
| unsubscribe_rate | Percent |
| submissions | Number (integer) |
| views | Number (integer) |
| form_type | Single line text |
| form_status | Single line text |
| recurring_commitments | Number (integer) |
| new_contacts | Number (integer) |
| recipient_list | Long text |
| excluded_list | Long text |

4. **Copy the Base ID and Table ID** from the Airtable API docs:
   - Base ID: `appA34PWBceEaSXdv`
   - Table ID: `tblof83wNUbXdUw9n`

---

## Step 3: GitHub Secrets

Go to **Settings → Secrets and variables → Actions** in the GitHub repo and add
these secrets (some already exist from the GA4 sync):

| Secret | Value | Notes |
|--------|-------|-------|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | `{...}` | Already exists |
| `AIRTABLE_PAT` | `pat...` | Already exists |
| `EVERYACTION_AIRTABLE_BASE_ID` | `appA34PWBceEaSXdv` | New |
| `EVERYACTION_AIRTABLE_TABLE_ID` | `tblof83wNUbXdUw9n` | New |
| `EVERYACTION_GOOGLE_SHEET_ID` | `15WbyrlRvtLIeE8SMghSFHS-rhW7bSkDkssc1Q7cLy2I` | New |

---

## Step 4: Test the Workflow

### Manual trigger (recommended first test)

1. Go to **Actions** → **Sync EveryAction to Airtable**
2. Click **Run workflow**
3. Optionally set `row_limit` to `10` for a quick test
4. Click **Run workflow** and watch the logs

### Local testing

```bash
# Set environment variables
export GOOGLE_SERVICE_ACCOUNT_FILE=path/to/service-account.json
export AIRTABLE_PAT=pat...
export AIRTABLE_BASE_ID=appA34PWBceEaSXdv
export AIRTABLE_TABLE_ID=tblof83wNUbXdUw9n
export GOOGLE_SHEET_ID=15WbyrlRvtLIeE8SMghSFHS-rhW7bSkDkssc1Q7cLy2I
export GOOGLE_SHEET_TAB=everyaction_master
export ROW_LIMIT=10  # optional, for testing

# Install dependencies
pip install -r requirements.txt

# Run
python sync_sheet_to_airtable.py
```

---

## Troubleshooting

### "Google Sheet is empty"
- Verify the BigQuery connector has run at least once
- Check that the tab is named exactly `everyaction_master`
- Ensure the service account has Viewer access to the sheet

### "AIRTABLE_PAT environment variable is required"
- Check that the `AIRTABLE_PAT` secret is set in GitHub repo settings

### Rate limit errors (429)
- The script includes 0.25s sleeps between Airtable batches
- Retry logic with exponential backoff handles transient failures
- If persistent, increase `BATCH_SLEEP` in `sync_sheet_to_airtable.py`

### Schema mismatch
- The script auto-coerces values (dates, numbers, percentages, currency)
- Airtable will auto-create new fields as "Single line text" if they don't exist
- For proper field types, configure them in the Airtable UI first (see schema doc)

### BigQuery table `everyaction_combined` doesn't exist
- This table needs to be created in BigQuery as a view or table combining
  the EveryAction report data. Check with your data team.
