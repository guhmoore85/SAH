# EveryAction Airtable Field Mapping Reference

Field mapping for the `Campaigns` table in the `SAH EveryAction Dashboard`
Airtable base. These columns come from the BigQuery query in
`everyaction_combined` via the Google Sheets connected sheet.

## Field Definitions

| # | Column | Airtable Field Type | Notes |
|---|--------|-------------------|-------|
| 1 | `type` | Single select | Values: "Email", "Form" |
| 2 | `name` | Single line text | Campaign or form name |
| 3 | `first_date` | Date | First activity date (YYYY-MM-DD) |
| 4 | `last_date` | Date | Most recent activity date (YYYY-MM-DD) |
| 5 | `days_active` | Number (integer) | Days between first and last activity |
| 6 | `year` | Number (integer) | Calendar year |
| 7 | `month` | Number (integer) | Month number (1–12) |
| 8 | `month_name` | Single line text | E.g. "January", "February" |
| 9 | `conversion_rate` | Percent | Stored as decimal (0.452 = 45.2%) |
| 10 | `total_contributions` | Number (integer) | Count of contributions |
| 11 | `amount_raised` | Currency (USD) | Total dollar amount raised |
| 12 | `avg_contribution_amount` | Currency (USD) | Average donation amount |
| 13 | `recipients` | Number (integer) | Email recipients count |
| 14 | `unique_open_rate` | Percent | Stored as decimal (0.35 = 35%) |
| 15 | `unique_click_rate` | Percent | Stored as decimal (0.12 = 12%) |
| 16 | `bounce_rate` | Percent | Stored as decimal (0.05 = 5%) |
| 17 | `unsubscribe_rate` | Percent | Stored as decimal (0.01 = 1%) |
| 18 | `submissions` | Number (integer) | Form submissions count |
| 19 | `views` | Number (integer) | Form page views |
| 20 | `form_type` | Single line text | E.g. "Petition", "Survey", "Signup" |
| 21 | `form_status` | Single line text | E.g. "Active", "Inactive" |
| 22 | `recurring_commitments` | Number (integer) | Recurring donor commitments |
| 23 | `new_contacts` | Number (integer) | New contacts acquired |
| 24 | `recipient_list` | Long text | Names/IDs of recipient lists used |
| 25 | `excluded_list` | Long text | Names/IDs of excluded lists |

## Auto-Coercion Behavior

The `sync_sheet_to_airtable.py` script automatically coerces cell values:

| Google Sheets Value | Coerced To | Example |
|-------------------|-----------|---------|
| `45.2%` | `0.452` (float) | Percent fields |
| `$1,234.56` | `1234.56` (float) | Currency fields |
| `1234` | `1234` (int) | Number fields |
| `3.14` | `3.14` (float) | Decimal numbers |
| `3/15/2026` | `2026-03-15` (string) | Date fields (M/D/YYYY → ISO) |
| `2026-03-15` | `2026-03-15` (string) | Date fields (already ISO) |
| `Email` | `"Email"` (string) | Text/select fields |
| _(empty)_ | _(skipped)_ | Not sent to Airtable |

## Airtable Setup Tips

1. **Create fields manually first** with the correct types listed above.
   Airtable auto-creates fields as "Single line text" which won't sort or
   filter correctly for dates, numbers, and percentages.

2. **Single select fields** (`type`): Add the known options ("Email", "Form")
   in advance so Airtable can color-code them.

3. **Percent fields** (`conversion_rate`, `unique_open_rate`, `unique_click_rate`,
   `bounce_rate`, `unsubscribe_rate`): Set the Airtable field to "Percent" type.
   The script sends values as decimals (e.g. 0.452), which Airtable displays as 45.2%.

4. **Currency fields** (`amount_raised`, `avg_contribution_amount`):
   Set the Airtable field to "Currency" with USD. The script strips `$` and `,`
   from the source data and sends a plain number.

5. **Date fields** (`first_date`, `last_date`):
   Set the Airtable field to "Date". The script converts to ISO format
   (YYYY-MM-DD) which Airtable accepts natively.
