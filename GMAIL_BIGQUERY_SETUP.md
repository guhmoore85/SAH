# Gmail → BigQuery Pipeline

Automatically downloads CSV report attachments from EveryAction emails in Gmail and loads them into BigQuery.

## Reports

| Report | Schedule | BigQuery Table | Filename Pattern |
|--------|----------|----------------|------------------|
| Daily Full List Count | Daily | `daily_full_list` | `Daily_Full_L_*.csv` |
| SAH Donors Data | Daily | `sah_donors` | `SAH_Donors_D_*.csv` |
| Daily SAH 365 | Daily | `daily_sah_365` | `Daily_SAH_36_*.csv` |
| Email Comparison | Weekly | `email_comparison` | `Email_Compar_*.csv` |
| Forms Report | Weekly | `forms_report` | `Forms_report_*.csv` |

All tables live in `stopaapihate-472516.everyaction_reports`.

---

## Prerequisites

### 1. Google Cloud Service Account

You need a service account with these roles:

- **BigQuery Data Editor** (`roles/bigquery.dataEditor`) on the project
- **BigQuery Job User** (`roles/bigquery.jobUser`) on the project

If you already have `GOOGLE_SERVICE_ACCOUNT_JSON` in GitHub Secrets from the Sheets→Airtable pipeline, you can reuse the same service account — just add the BigQuery roles.

### 2. Gmail API — Domain-Wide Delegation

A service account cannot access Gmail directly. It needs **domain-wide delegation** configured by a Google Workspace admin.

#### Steps:

1. **Enable the Gmail API** in the Google Cloud Console:
   - Go to [APIs & Services → Library](https://console.cloud.google.com/apis/library)
   - Search for "Gmail API" and click **Enable**

2. **Enable domain-wide delegation** on the service account:
   - Go to [IAM → Service Accounts](https://console.cloud.google.com/iam-admin/serviceaccounts)
   - Click on your service account
   - Under **Show domain-wide delegation**, check **Enable G Suite domain-wide delegation**
   - Note the **Client ID** (numeric)

3. **Authorize the service account in Google Workspace Admin**:
   - Go to [admin.google.com → Security → API Controls → Domain-wide Delegation](https://admin.google.com/ac/owl/domainwidedelegation)
   - Click **Add new**
   - **Client ID**: paste the numeric Client ID from step 2
   - **OAuth scopes**: `https://www.googleapis.com/auth/gmail.modify`
   - Click **Authorize**

4. **Verify**: The service account can now impersonate `hmoore@stopaapihate.org` to read/modify Gmail.

### 3. BigQuery Setup

The script auto-creates the dataset (`everyaction_reports`) and tables on first run. No manual BigQuery setup is needed.

---

## GitHub Secrets

Add these secrets to the repository (Settings → Secrets → Actions):

| Secret | Description |
|--------|-------------|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Full JSON content of the service account key file |

The BigQuery project, dataset, and Gmail user are set in the workflow files (not secrets, since they're not sensitive).

---

## How It Works

1. **Search Gmail** for unread emails from `noreply@everyaction.com` matching each report's subject line, excluding emails already labeled `BQ-Processed`.
2. **Download the CSV attachment** matching the expected filename pattern.
3. **Parse the CSV**, auto-detecting column names and types (INT64, FLOAT64, DATE, STRING).
4. **Create/update the BigQuery table** schema if needed (additive only — never drops columns).
5. **Append rows** to the BigQuery table with metadata columns:
   - `_import_timestamp` — when the data was loaded
   - `_source_filename` — original CSV filename
   - `_email_date` — when the email was received
   - `_gmail_message_id` — Gmail message ID (for dedup tracking)
6. **Label the email** as `BQ-Processed` so it's skipped on the next run.

### Duplicate Prevention

- Emails are labeled after successful processing; the Gmail search query excludes labeled emails.
- The `_gmail_message_id` column lets you identify and deduplicate rows if needed:
  ```sql
  SELECT * FROM `stopaapihate-472516.everyaction_reports.daily_full_list`
  WHERE _gmail_message_id = '18abc123def'
  ```

---

## GitHub Actions Workflows

| Workflow | File | Schedule |
|----------|------|----------|
| Daily reports | `.github/workflows/gmail-bq-daily.yml` | Every day at 9 AM UTC |
| Weekly reports | `.github/workflows/gmail-bq-weekly.yml` | Every Monday at 9 AM UTC |

Both workflows support manual triggers via the **Actions** tab (workflow_dispatch).

---

## Local Development

```bash
# 1. Clone and install
pip install -r requirements.txt

# 2. Set environment variables (copy and edit .env.example)
cp .env.example .env
# Edit .env — set GOOGLE_SERVICE_ACCOUNT_FILE to your local key path

# 3. Run
python gmail_to_bigquery.py --mode daily
python gmail_to_bigquery.py --mode weekly
python gmail_to_bigquery.py --mode all
```

---

## Troubleshooting

### "403 Forbidden" from Gmail API

- Domain-wide delegation is not configured, or the OAuth scope is wrong.
- Verify the service account Client ID and scope (`gmail.modify`) in Workspace Admin.

### "403 Access Denied" from BigQuery

- The service account lacks BigQuery roles. Add `BigQuery Data Editor` and `BigQuery Job User`.

### "No attachment matching ..." warnings

- The filename pattern in `REPORT_CONFIGS` doesn't match the actual filename. Check the email manually and update the regex in `gmail_to_bigquery.py`.

### Emails are re-processed / duplicates in BigQuery

- Check that the `BQ-Processed` label exists in Gmail and is being applied. You can query:
  ```sql
  SELECT _gmail_message_id, COUNT(*) as cnt
  FROM `stopaapihate-472516.everyaction_reports.daily_full_list`
  GROUP BY _gmail_message_id
  HAVING cnt > 1
  ```
- If duplicates exist, delete them:
  ```sql
  DELETE FROM `stopaapihate-472516.everyaction_reports.daily_full_list`
  WHERE _gmail_message_id = '<id>' AND _import_timestamp != (
    SELECT MIN(_import_timestamp)
    FROM `stopaapihate-472516.everyaction_reports.daily_full_list`
    WHERE _gmail_message_id = '<id>'
  )
  ```

### Schema mismatch / type errors

- The script infers types from CSV data. If a column that looks numeric has text in later emails, the load job may fail.
- Fix: manually alter the BigQuery column to STRING, or update the schema inference in `gmail_to_bigquery.py`.

### Workflow not running on schedule

- GitHub Actions cron can be delayed by up to 15 minutes. Check the Actions tab for run history.
- Cron schedules only run on the default branch. Make sure these workflow files are merged to `master`.

### Testing with a single email

Run manually and check logs:
```bash
python gmail_to_bigquery.py --mode daily 2>&1 | tee test_run.log
```
