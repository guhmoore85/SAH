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

Read-only: it never labels emails and never writes to BigQuery.

Usage:
    python check_everyaction_daily_gap.py

Reads the same env vars as sync_everyaction_gmail.py:
    GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN
    GOOGLE_SERVICE_ACCOUNT_JSON / GOOGLE_SERVICE_ACCOUNT_FILE, BQ_PROJECT, BQ_DATASET
"""

from collections import Counter
from datetime import date, datetime, timedelta, timezone

import requests

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
        if missing_with_email:
            first = missing_with_email[0]
            print(f"  download test, email from {first}: {try_link(gmail, emails[first][0]['id'])}")
        print()


if __name__ == "__main__":
    main()
