#!/usr/bin/env python3
"""
EveryAction Daily Count Gap Checker

Diagnostic script for missing days in the three daily list-count tables
(daily_full_list, sah_donors, daily_sah_365). For each report it compares:
    - the days BigQuery has a usable summary row for (record_count > 1, the
      same filter the dbt daily_totals model applies)
    - every EveryAction email for that report in Gmail, processed or not
and, for the first missing day that does have an email, tries that email's
download link to see whether a backfill from Gmail is still possible
(EveryAction report links can expire).

It then does the same for the two places the counts are copied to: the
EveryAction sheet's `dailys` tab (with its extract row limit and refresh
time) and the Daily Totals Airtable table the daily sync fills from it.

Read-only: it never labels emails and never writes anywhere.

Usage:
    python check_everyaction_daily_gap.py

Reads the same env vars as sync_everyaction_gmail.py:
    GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN
    GOOGLE_SERVICE_ACCOUNT_JSON / GOOGLE_SERVICE_ACCOUNT_FILE, BQ_PROJECT, BQ_DATASET
  and, for the sheet / Airtable sections (skipped when unset):
    EVERYACTION_GOOGLE_SHEET_ID, DAILYS_TAB (default "dailys")
    AIRTABLE_PAT, AIRTABLE_BASE_ID, DAILY_TOTALS_AIRTABLE_TABLE_ID
"""

import json
import os
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

import gspread
import requests
from google.oauth2.service_account import Credentials as SACredentials
from pyairtable import Api

from sync_everyaction_gmail import (
    BQ_DATASET,
    BQ_PROJECT,
    PROCESSED_LABEL,
    REPORT_CONFIGS,
    SENDER_EMAIL,
    _extract_download_url,
    _get_email_body_html,
    get_bigquery_client,
    get_gmail_service,
)

# Summary rows (one per day) start on 2026-03-19; earlier rows are per-contact.
SINCE = date(2026, 3, 19)
DAILY_REPORTS = [k for k, cfg in REPORT_CONFIGS.items() if cfg["schedule"] == "daily"]


def bq_days(client, table: str) -> set[date]:
    sql = f"""
        SELECT DISTINCT DATE(TIMESTAMP(_email_date)) AS d
        FROM `{BQ_PROJECT}.{BQ_DATASET}.{table}`
        WHERE record_count > 1 AND DATE(TIMESTAMP(_email_date)) >= '{SINCE}'
    """
    return {row.d for row in client.query(sql).result()}


def gmail_emails(service, subject: str, label_id: str) -> dict[date, list[dict]]:
    """Every EveryAction email for this report since SINCE, keyed by UTC day."""
    query = f'from:{SENDER_EMAIL} subject:("{subject}") after:{SINCE:%Y/%m/%d}'
    ids, page_token = [], None
    while True:
        resp = service.users().messages().list(userId="me", q=query, pageToken=page_token).execute()
        ids.extend(m["id"] for m in resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    by_day: dict[date, list[dict]] = {}
    for msg_id in ids:
        msg = service.users().messages().get(userId="me", id=msg_id, format="minimal").execute()
        day = datetime.fromtimestamp(int(msg["internalDate"]) / 1000, tz=timezone.utc).date()
        by_day.setdefault(day, []).append(
            {"id": msg_id, "processed": label_id in msg.get("labelIds", [])}
        )
    return by_day


def ranges(days: list[date]) -> list[str]:
    """Collapse sorted dates into 'start..end (n)' runs."""
    out, start, prev = [], None, None
    for d in days:
        if start is None:
            start = prev = d
        elif d == prev + timedelta(days=1):
            prev = d
        else:
            out.append(f"{start}..{prev} ({(prev - start).days + 1})")
            start = prev = d
    if start is not None:
        out.append(f"{start}..{prev} ({(prev - start).days + 1})")
    return out


def count_runs(client, table: str) -> None:
    """Collapse consecutive days that share a record_count, with load metadata.

    A count that stays identical for weeks means the loaded file wasn't that
    day's real report (e.g. an expired link served the same stub file).
    """
    sql = f"""
        SELECT DATE(TIMESTAMP(_email_date)) AS d, record_count,
               DATE(_import_timestamp) AS loaded_on, _source_filename AS file
        FROM `{BQ_PROJECT}.{BQ_DATASET}.{table}`
        WHERE record_count > 1 AND DATE(TIMESTAMP(_email_date)) >= '{SINCE}'
        QUALIFY ROW_NUMBER() OVER (PARTITION BY DATE(TIMESTAMP(_email_date))
                                   ORDER BY _import_timestamp DESC) = 1
        ORDER BY d
    """
    runs: list[list] = []
    for row in client.query(sql).result():
        if runs and runs[-1][2] == row.record_count:
            runs[-1][1] = row.d
            runs[-1][3].add(row.loaded_on)
            runs[-1][4].add(row.file)
        else:
            runs.append([row.d, row.d, row.record_count, {row.loaded_on}, {row.file}])
    print(f"  record_count runs (days sharing one value; >2 days flagged):")
    for start, end, count, loaded, files in runs:
        days = (end - start).days + 1
        flag = "  <-- SAME VALUE" if days > 2 else ""
        print(f"    {start}..{end} ({days}d): {count}  loaded {sorted(loaded)[0]}..{sorted(loaded)[-1]}"
              f"  files={len(files)} e.g. {sorted(files)[0]}{flag}")


def try_link(service, msg_id: str) -> str:
    url = _extract_download_url(_get_email_body_html(service, msg_id))
    if not url:
        return "no download link in email"
    try:
        resp = requests.get(url, timeout=120, allow_redirects=True)
    except requests.RequestException as exc:
        return f"request failed: {exc}"
    head = resp.content[:200].decode("utf-8", errors="replace").replace("\n", " ")
    looks_csv = "," in head and "<html" not in head.lower()
    return (f"HTTP {resp.status_code}, {len(resp.content)} bytes, "
            f"{'looks like CSV' if looks_csv else 'NOT a CSV'}: {head[:120]!r}")


def parse_day(value) -> date | None:
    """Dates arrive as M/D/YYYY from Sheets and YYYY-MM-DD (maybe with time) from Airtable."""
    text = str(value or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:10] if fmt == "%Y-%m-%d" else text, fmt).date()
        except ValueError:
            continue
    return None


def report_copy(label: str, days_by_source: dict[str, set[date]], all_days: list[date]) -> None:
    print("=" * 70)
    print(label)
    print("=" * 70)
    if not days_by_source:
        print("  no rows with a recognisable _email_date / source_table")
    for source, days in sorted(days_by_source.items()):
        in_range = {d for d in days if d >= SINCE}
        missing = [d for d in all_days if d not in in_range]
        print(f"  {source}: {len(in_range)} days since {SINCE}, "
              f"range {min(days) if days else '-'}..{max(days) if days else '-'}")
        print(f"    by month: {dict(sorted(Counter(d.strftime('%Y-%m') for d in in_range).items()))}")
        print(f"    missing:  {ranges(missing) or 'none'}")
    print()


def check_dailys_tab(all_days: list[date]) -> None:
    sheet_id = os.getenv("EVERYACTION_GOOGLE_SHEET_ID")
    tab = os.getenv("DAILYS_TAB", "dailys")
    if not sheet_id:
        print("(EVERYACTION_GOOGLE_SHEET_ID not set; skipping sheet check)\n")
        return
    info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    creds = SACredentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly",
                      "https://www.googleapis.com/auth/drive.readonly"])
    sheet = gspread.authorize(creds).open_by_key(sheet_id)
    meta = sheet.fetch_sheet_metadata(params={
        "ranges": f"'{tab}'!A1", "includeGridData": "true",
        "fields": "sheets(properties(title,gridProperties),data(rowData(values(dataSourceTable))))",
    })
    for s_ in meta.get("sheets", []):
        print(f"SHEET TAB {tab!r}: grid {s_['properties'].get('gridProperties')}")
        for d in s_.get("data", []):
            for r in d.get("rowData", []):
                for v in r.get("values", []):
                    if v.get("dataSourceTable"):
                        dst = v["dataSourceTable"]
                        print(f"  extract: rowLimit={dst.get('rowLimit')} sortSpecs={dst.get('sortSpecs')} "
                              f"filterSpecs={dst.get('filterSpecs')} status={dst.get('dataExecutionStatus')}")
    values = sheet.worksheet(tab).get_all_values()
    header, rows = (values[0], values[1:]) if values else ([], [])
    print(f"  header: {header}")
    print(f"  data rows: {len([r for r in rows if any(r)])}")
    col = {h: i for i, h in enumerate(header)}
    by_source: dict[str, set[date]] = defaultdict(set)
    if "_email_date" in col and "source_table" in col:
        for r in rows:
            d = parse_day(r[col["_email_date"]]) if len(r) > col["_email_date"] else None
            if d:
                by_source[r[col["source_table"]]].add(d)
    report_copy(f"SHEET {tab!r}: days per source_table", by_source, all_days)


def check_daily_totals_airtable(all_days: list[date]) -> None:
    pat, base, table_id = (os.getenv("AIRTABLE_PAT"), os.getenv("AIRTABLE_BASE_ID"),
                           os.getenv("DAILY_TOTALS_AIRTABLE_TABLE_ID"))
    if not (pat and base and table_id):
        print("(Daily Totals Airtable env not set; skipping Airtable check)\n")
        return
    records = Api(pat).table(base, table_id).all()
    by_source: dict[str, set[date]] = defaultdict(set)
    keys = Counter()
    for r in records:
        f = r.get("fields", {})
        d = parse_day(f.get("_email_date"))
        if d:
            by_source[str(f.get("source_table"))].add(d)
            keys[(d, f.get("source_table"))] += 1
    print(f"AIRTABLE Daily Totals: {len(records)} records, "
          f"{sum(n - 1 for n in keys.values() if n > 1)} duplicate (date, source_table) copies")
    report_copy("AIRTABLE Daily Totals: days per source_table", by_source, all_days)


def main() -> None:
    bq = get_bigquery_client()
    gmail = get_gmail_service()
    labels = gmail.users().labels().list(userId="me").execute().get("labels", [])
    label_id = next((l["id"] for l in labels if l["name"] == PROCESSED_LABEL), None)
    today = datetime.now(timezone.utc).date()
    all_days = [SINCE + timedelta(days=i) for i in range((today - SINCE).days + 1)]

    for key in DAILY_REPORTS:
        cfg = REPORT_CONFIGS[key]
        have = bq_days(bq, cfg["table"])
        emails = gmail_emails(gmail, cfg["subject_pattern"], label_id)
        missing = [d for d in all_days if d not in have]
        missing_with_email = [d for d in missing if d in emails]
        missing_no_email = [d for d in missing if d not in emails]
        processed_but_missing = [d for d in missing_with_email
                                 if any(e["processed"] for e in emails[d])]

        print("=" * 70)
        print(f"{key}  (table {cfg['table']}, since {SINCE})")
        print("=" * 70)
        print(f"  days in range:                 {len(all_days)}")
        print(f"  days with a usable BQ row:     {len(have)}")
        print(f"  days missing in BQ:            {len(missing)}")
        print(f"    ...with an email in Gmail:   {len(missing_with_email)}"
              f"  (of which labeled {PROCESSED_LABEL}: {len(processed_but_missing)})")
        print(f"    ...with NO email in Gmail:   {len(missing_no_email)}")
        print(f"  Gmail emails by month: {dict(sorted(Counter(d.strftime('%Y-%m') for d in emails).items()))}")
        print(f"  BQ days by month:      {dict(sorted(Counter(d.strftime('%Y-%m') for d in have).items()))}")
        print(f"  missing, email exists: {ranges(missing_with_email) or 'none'}")
        print(f"  missing, no email:     {ranges(missing_no_email) or 'none'}")
        count_runs(bq, cfg["table"])
        if missing_with_email:
            first = missing_with_email[0]
            print(f"  download test, email from {first}: {try_link(gmail, emails[first][0]['id'])}")
        probe = date(2026, 5, 15)
        if probe in emails:
            print(f"  download test, email from {probe}: {try_link(gmail, emails[probe][0]['id'])}")
        print()

    check_dailys_tab(all_days)
    check_daily_totals_airtable(all_days)


if __name__ == "__main__":
    main()
