#!/usr/bin/env python3
"""
Fetch rows from the SAM.GOV sheet where Email Subject, Email Body, and
Email Drafted are all empty, download the Drive files, generate a subject
and body via Gemini 3 Flash, and write them back to the sheet.
"""

import io
import os
import sys
import re
import json
import threading
import time
import requests
import pygsheets
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import SPREADSHEET_NAME, SAM_GOV_WORKSHEET
from gemini import call_llm, has_site_visit as check_site_visit
from google_drive_utils import extract_text_from_file_content
from main import fetch_ui_link_data
from bouncer import verify_emails_batch

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "newEmailDashboard", "backend")

import importlib

def _load_gmail_client():
    """Import GmailClient from the backend dir without polluting sys.path."""
    original_path = sys.path.copy()
    sys.path.insert(0, BACKEND_DIR)
    # Temporarily swap out our config so the backend's config loads instead
    our_config = sys.modules.pop("config")
    try:
        mod = importlib.import_module("gmail_client")
    finally:
        sys.modules["config"] = our_config
        sys.path = original_path
    return mod.GmailClient

GmailClient = _load_gmail_client()

NEXAN_EMAIL = "info@thenexan.com"
NEXAN_ACCOUNT_CONFIG = {
    "name": NEXAN_EMAIL,
    "token_file": os.path.join(os.path.dirname(__file__), "newEmailDashboard", "token3.pickle"),
    "credentials_file": os.path.join(os.path.dirname(__file__), "newEmailDashboard", "credentials_nexan.json"),
}

with open("prompt_subject_body.txt", "r") as _f:
    PROMPT_TEMPLATE = _f.read()


def setup_sheet():
    gc = pygsheets.authorize(service_file='key.json')
    sh = gc.open(SPREADSHEET_NAME)
    wks = sh.worksheet_by_title(SAM_GOV_WORKSHEET)
    return wks


_cached_drive_token = None
_cached_drive_token_expiry = 0


def get_drive_access_token():
    """Get an OAuth2 access token using the service-account key.
    Caches the token and auto-refreshes 5 minutes before expiry."""
    global _cached_drive_token, _cached_drive_token_expiry
    import jwt, time as _time

    now = int(_time.time())
    if _cached_drive_token and now < _cached_drive_token_expiry - 300:
        return _cached_drive_token

    with open("key.json", "r") as f:
        creds = json.load(f)
    payload = {
        "iss": creds["client_email"],
        "scope": "https://www.googleapis.com/auth/drive.readonly",
        "aud": "https://oauth2.googleapis.com/token",
        "iat": now,
        "exp": now + 3600,
    }
    token = jwt.encode(payload, creds["private_key"], algorithm="RS256")
    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": token,
        },
    )
    data = resp.json()
    _cached_drive_token = data["access_token"]
    _cached_drive_token_expiry = now + data.get("expires_in", 3600)
    print(f"  [Drive token] Refreshed (expires in {data.get('expires_in', 3600)}s)")
    return _cached_drive_token


def download_drive_files(folder_id, access_token):
    """Download all files from a Google Drive folder and return {filename: bytes}."""
    drive_api = "https://www.googleapis.com/drive/v3"
    headers = {"Authorization": f"Bearer {access_token}"}

    list_url = f"{drive_api}/files"
    params = {
        "q": f"'{folder_id}' in parents and trashed=false",
        "fields": "files(id,name,mimeType)",
    }
    files_resp = requests.get(list_url, headers=headers, params=params).json()
    files = files_resp.get("files", [])

    if not files:
        print("  No files found in Drive folder.")
        return {}

    print(f"  Found {len(files)} file(s) in Drive folder.")
    if len(files) > 10:
        print(f"  ⏭️ Skipping download: {len(files)} files exceeds 10-file limit")
        return None

    downloaded = {}
    for f in files:
        print(f"    Downloading {f['name']}...")
        dl_url = f"{drive_api}/files/{f['id']}?alt=media"
        dl_resp = requests.get(dl_url, headers=headers)
        if dl_resp.status_code == 200:
            downloaded[f["name"]] = dl_resp.content
        else:
            print(f"    Failed to download {f['name']} (status {dl_resp.status_code})")
    return downloaded


def extract_text_from_downloaded(downloaded_files):
    """Extract text from all downloaded files and concatenate."""
    all_text = ""
    for filename, file_bytes in downloaded_files.items():
        text = extract_text_from_file_content(filename, file_bytes)
        if text:
            all_text += f"\n\n--- Content from file: {filename} ---\n{text}"
    return all_text


SUBJECT_BODY_PROMPT = """{prompt}

SOLICITATION TEXT:
{solicitation_text}
"""


def generate_subject_body(solicitation_text):
    """Call Gemini 3 Flash to generate a subject and body from solicitation text."""
    full_prompt = SUBJECT_BODY_PROMPT.format(
        prompt=PROMPT_TEMPLATE,
        solicitation_text=solicitation_text[:50000],
    )
    response = call_llm(full_prompt, temperature=0.3, timeout=120, max_retries=5)
    if not response:
        return None, None

    subject = None
    body = None

    subject_match = re.search(r"Subject:\s*(.+)", response)
    if subject_match:
        subject = subject_match.group(1).strip()

    body_match = re.search(r"Body:\s*\n(.*)", response, re.DOTALL)
    if body_match:
        body = body_match.group(1).strip()

    return subject, body


def create_email_draft(emails_raw, subject, html_body, gmail_client):
    """
    Verify emails via Bouncer and create a Gmail draft.
    Shared by federal_contracts_main, localContracts_texas, and localContracts_la.

    Returns the draft dict on success, None on failure.
    """
    if not emails_raw or not subject or not html_body or not gmail_client:
        return None

    bcc_list = [e.strip() for e in re.split(r"[;,]", emails_raw) if e.strip()]
    if not bcc_list:
        return None

    print(f"  Verifying {len(bcc_list)} emails via Bouncer...")
    verified = verify_emails_batch(bcc_list)
    if not verified:
        print("  No deliverable emails — skipping draft.")
        return None

    print(f"  {len(verified)} deliverable emails. Creating draft...")
    draft = gmail_client.create_draft(
        to=NEXAN_EMAIL,
        subject=subject,
        html_body=html_body,
        bcc=", ".join(verified),
    )
    if draft:
        print(f"  Draft created (ID: {draft.get('id', 'N/A')})")
    else:
        print("  Failed to create Gmail draft.")
    return draft


SKIP_STATUSES = {"controlled attachments", "skipped", "no files", "site visit"}

def get_empty_rows(records):
    """Filter for rows after 1500 with empty subject/body/drafted and a valid Drive link."""
    empty_rows = []
    for i, record in enumerate(records, start=2):
        subject = str(record.get("Email Subject", "")).strip()
        body = str(record.get("Email Body", "")).strip()
        drafted = str(record.get("Email Drafted", "")).strip()
        sam_link = str(record.get("Sam Link", "")).strip()
        drive_link = str(record.get("Google Drive Folder Link", "")).strip()
        status = str(record.get("getEmails", "")).strip().lower()

        if (
            not subject
            and not body
            and not drafted
            and sam_link
            and i > 1500
            and drive_link
            and drive_link != "No files uploaded"
            and status not in SKIP_STATUSES
        ):
            empty_rows.append((i, record))
    return empty_rows


def has_controlled_attachments(sam_link):
    """Fetch the SAM.gov UI text and check the Access column for controlled files."""
    print("  Checking SAM.gov page for controlled attachments...")
    ui_data = fetch_ui_link_data(sam_link, use_cache=True)
    if not ui_data:
        print("  Could not fetch UI text — assuming no controlled attachments.")
        return False, None

    text = ui_data.get("text_content", "")

    header_marker = "Document\nFile Size\nAccess\nUpdated Date"
    header_idx = text.find(header_marker)
    if header_idx == -1:
        print("  Could not find attachments table — assuming no controlled attachments.")
        return False, text

    table_text = text[header_idx + len(header_marker):]
    end_idx = table_text.find("Feedback")
    if end_idx != -1:
        table_text = table_text[:end_idx]

    lines = [l.strip() for l in table_text.strip().split("\n") if l.strip()]
    # Each attachment is a group of 4 lines: filename, size, access, date
    for i in range(2, len(lines), 4):  # access is the 3rd value in each group
        access_value = lines[i]
        if access_value == "Controlled":
            filename = lines[i - 2] if i >= 2 else "unknown"
            print(f"  ⚠️  Controlled attachment found: {filename}")
            return True, text

    print("  No controlled attachments found.")
    return False, text


def process_row(row_num, sam_link, drive_link, emails_raw, wks, access_token, gmail_client, sheet_lock):
    """
    Download Drive files, generate subject/body, verify emails via Bouncer,
    create draft, and update the sheet.

    Accepts sam_link, drive_link, and emails_raw directly so callers don't
    need to pass a full sheet record.
    """
    folder_match = re.search(r"/folders/([a-zA-Z0-9_-]+)", drive_link)
    if not folder_match:
        print(f"  [Row {row_num}] Could not extract folder ID from: {drive_link}")
        return False
    folder_id = folder_match.group(1)

    print(f"\n{'='*80}")
    print(f"Processing Row {row_num}")
    print(f"  Sam Link  : {sam_link}")
    print(f"  Drive Link: {drive_link}")
    print(f"{'='*80}")

    controlled, ui_text = has_controlled_attachments(sam_link)
    if controlled:
        print(f"  [Row {row_num}] Skipping — controlled attachments.")
        with sheet_lock:
            wks.update_value(f"K{row_num}", "controlled attachments")
        return False

    downloaded = download_drive_files(folder_id, access_token)
    if downloaded is None:
        print(f"  [Row {row_num}] Too many files — skipping.")
        with sheet_lock:
            wks.update_value(f"K{row_num}", "skipped: too many files")
        return False
    if not downloaded:
        print(f"  [Row {row_num}] No files downloaded — skipping.")
        with sheet_lock:
            wks.update_value(f"K{row_num}", "no files")
        return False

    solicitation_text = extract_text_from_downloaded(downloaded)
    if ui_text:
        solicitation_text = f"--- SAM.gov Page Text ---\n{ui_text}\n\n{solicitation_text}"
    if not solicitation_text or len(solicitation_text.strip()) < 50:
        print(f"  [Row {row_num}] Extracted text too short — skipping.")
        return False
    print(f"  [Row {row_num}] Extracted {len(solicitation_text)} chars of text.")

    print(f"  [Row {row_num}] Generating subject and body with Gemini 3 Flash...")
    subject, body = generate_subject_body(solicitation_text)

    if not subject or not body:
        print(f"  [Row {row_num}] Failed to generate subject/body.")
        return False

    print(f"  [Row {row_num}] Subject: {subject}")

    if not emails_raw:
        print(f"  [Row {row_num}] No emails provided — writing subject/body only.")
        with sheet_lock:
            wks.update_value(f"G{row_num}", subject)
            wks.update_value(f"H{row_num}", body)
        return True

    bcc_list = [e.strip() for e in re.split(r"[;,]", emails_raw) if e.strip()]
    print(f"  [Row {row_num}] Verifying {len(bcc_list)} emails via Bouncer batch...")

    verified = verify_emails_batch(bcc_list)
    if verified is None:
        print(f"  [Row {row_num}] Bouncer verification failed — skipping draft.")
        with sheet_lock:
            wks.update_value(f"G{row_num}", subject)
            wks.update_value(f"H{row_num}", body)
            wks.update_value(f"K{row_num}", "bouncer error")
        return True

    if not verified:
        print(f"  [Row {row_num}] 0 deliverable emails — skipping draft.")
        with sheet_lock:
            wks.update_value(f"G{row_num}", subject)
            wks.update_value(f"H{row_num}", body)
            wks.update_value(f"K{row_num}", "no deliverable emails")
        return True

    bcc_list = verified
    print(f"  [Row {row_num}] {len(bcc_list)} deliverable emails. Creating draft...")

    draft = gmail_client.create_draft(
        to=NEXAN_EMAIL,
        subject=subject,
        html_body=body,
        bcc=", ".join(bcc_list),
    )

    with sheet_lock:
        wks.update_value(f"G{row_num}", subject)
        wks.update_value(f"H{row_num}", body)
        wks.update_value(f"I{row_num}", "; ".join(bcc_list))
        if draft:
            print(f"  [Row {row_num}] Draft created (ID: {draft.get('id', 'N/A')})")
            wks.update_value(f"J{row_num}", "yes")
        else:
            print(f"  [Row {row_num}] Failed to create Gmail draft.")

    return True


def main():
    print("Connecting to Google Sheets...")
    wks = setup_sheet()

    records = wks.get_all_records()
    print(f"Total records: {len(records)}")

    empty_rows = get_empty_rows(records)
    print(f"Found {len(empty_rows)} rows with empty Subject, Body, and Email Drafted.\n")

    if not empty_rows:
        print("Nothing to process.")
        return

    print("Getting Google Drive access token...")
    access_token = get_drive_access_token()

    print("Authenticating Gmail client...")
    gmail_client = GmailClient(NEXAN_ACCOUNT_CONFIG)
    if not gmail_client.authenticate():
        print("Failed to authenticate Gmail. Exiting.")
        return
    print(f"Authenticated as: {gmail_client.user_email}")

    # limit = 30
    rows_to_process = empty_rows
    sheet_lock = threading.Lock()
    success_count = 0
    total = len(rows_to_process)

    print(f"\nProcessing {total} rows with 3 parallel workers...\n")

    def handle_row(item):
        idx, (row_num, record) = item
        sam_link = str(record.get("Sam Link", "")).strip()
        drive_link = str(record.get("Google Drive Folder Link", "")).strip()
        emails_raw = str(record.get("Email Address'", "")).strip()
        try:
            success = process_row(
                row_num, sam_link, drive_link, emails_raw,
                wks, access_token, gmail_client, sheet_lock,
            )
            status = "Success" if success else "Failed"
            print(f"\n[{idx + 1}/{total}] Row {row_num}: {status}")
            return success
        except Exception as e:
            print(f"\n[{idx + 1}/{total}] Row {row_num}: Error — {e}")
            return False

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(handle_row, (idx, row)): idx
            for idx, row in enumerate(rows_to_process)
        }
        for future in as_completed(futures):
            if future.result():
                success_count += 1

    print(f"\nDone! Successfully processed {success_count}/{total} rows.")


if __name__ == "__main__":
    main()
    # outpt=has_controlled_attachments("https://sam.gov/workspace/contract/opp/96cf2e1b01654fdeb47b8eec6b08f69e/view")
