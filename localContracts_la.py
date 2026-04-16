#!/usr/bin/env python3
"""
Louisiana LaPAC Solicitations Scraper
Fetches solicitations from Louisiana Procurement and Contract Network (LaPAC)
URL: https://wwwcfprd.doa.louisiana.gov/osp/lapac/pubMain.cfm

Features:
- Scrapes open solicitations from LaPAC filtered by issue date
- Downloads bid PDFs and all attachment files directly from search results
- Extracts text from downloaded files
- Processes with OpenAI for vendor lead generation
- Uploads files to Google Drive
- Adds results to Google Sheets 'localContracts' tab

Output Structure:
- la_downloads/[BID_NUMBER]/ - Directory for each solicitation
  ├── [main_bid.pdf]
  ├── [attachment_1.docx]
  └── [additional attachments...]

Usage:
    python3 localContracts_la.py
"""

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from bs4 import BeautifulSoup
import time
import re
import os
import requests
from datetime import datetime, timedelta
from urllib.parse import urljoin, quote

from localContracts_texas import add_row_to_local_contracts, add_row_to_skipped_contracts
from services.openai_service import generate_vendor_leads
from google_drive_utils import extract_text_from_file_content
from gemini import has_site_visit
from backfillfolderLinks import get_drive_access_token, create_drive_folder, upload_file_to_drive
from config import DRIVE_PARENT_FOLDER_ID
from get_empty_rows import GmailClient, NEXAN_ACCOUNT_CONFIG, create_email_draft
import json

LA_BASE_URL = "https://wwwcfprd.doa.louisiana.gov"
LA_SEARCH_URL = f"{LA_BASE_URL}/osp/lapac/srchopen.cfm"
LA_PDF_BASE = f"{LA_BASE_URL}/osp/lapac/agency/pdf"
LA_DOWNLOADS_DIR = "la_downloads"
LA_CACHE_DIR = "la_cache"


def _ensure_cache_dir():
    os.makedirs(LA_CACHE_DIR, exist_ok=True)


def _cache_path(name):
    return os.path.join(LA_CACHE_DIR, name)


def save_solicitations_cache(solicitations, date_key):
    _ensure_cache_dir()
    cache_data = {
        "date_key": date_key,
        "fetched_at": datetime.now().isoformat(),
        "solicitations": solicitations,
    }
    path = _cache_path("solicitations.json")
    with open(path, "w") as f:
        json.dump(cache_data, f, indent=2)
    print(f"  Cached {len(solicitations)} solicitations to {path}")


def load_solicitations_cache(date_key):
    path = _cache_path("solicitations.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            cache_data = json.load(f)
        if cache_data.get("date_key") == date_key:
            sols = cache_data["solicitations"]
            print(f"  Loaded {len(sols)} solicitations from cache (fetched {cache_data['fetched_at']})")
            return sols
        else:
            print(f"  Cache date mismatch ({cache_data.get('date_key')} != {date_key}), refetching")
            return None
    except Exception as e:
        print(f"  Cache load error: {e}")
        return None


def save_progress(sol_id, status, data=None):
    _ensure_cache_dir()
    progress = load_all_progress()
    progress[sol_id] = {
        "status": status,
        "timestamp": datetime.now().isoformat(),
        "data": data or {},
    }
    path = _cache_path("progress.json")
    with open(path, "w") as f:
        json.dump(progress, f, indent=2)


def load_all_progress():
    path = _cache_path("progress.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_solicitation_cache(sol_id, page_text, files_text, filter_result, lead_result=None):
    _ensure_cache_dir()
    sol_cache_dir = os.path.join(LA_CACHE_DIR, "solicitations")
    os.makedirs(sol_cache_dir, exist_ok=True)
    cache_data = {
        "sol_id": sol_id,
        "cached_at": datetime.now().isoformat(),
        "page_text_len": len(page_text),
        "files_text_len": len(files_text),
        "filter_result": filter_result,
        "lead_result": lead_result,
    }
    text_dir = os.path.join(sol_cache_dir, re.sub(r"[^\w\-]", "_", sol_id))
    os.makedirs(text_dir, exist_ok=True)
    with open(os.path.join(text_dir, "page_text.txt"), "w", encoding="utf-8") as f:
        f.write(page_text)
    with open(os.path.join(text_dir, "files_text.txt"), "w", encoding="utf-8") as f:
        f.write(files_text)
    with open(os.path.join(text_dir, "result.json"), "w") as f:
        json.dump(cache_data, f, indent=2)


def load_solicitation_cache(sol_id):
    safe_id = re.sub(r"[^\w\-]", "_", sol_id)
    text_dir = os.path.join(LA_CACHE_DIR, "solicitations", safe_id)
    result_path = os.path.join(text_dir, "result.json")
    if not os.path.exists(result_path):
        return None
    try:
        with open(result_path, "r") as f:
            cache_data = json.load(f)
        page_text_path = os.path.join(text_dir, "page_text.txt")
        files_text_path = os.path.join(text_dir, "files_text.txt")
        if os.path.exists(page_text_path):
            with open(page_text_path, "r", encoding="utf-8") as f:
                cache_data["page_text"] = f.read()
        if os.path.exists(files_text_path):
            with open(files_text_path, "r", encoding="utf-8") as f:
                cache_data["files_text"] = f.read()
        return cache_data
    except Exception:
        return None


def setup_chrome(download_dir=None):
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument(
        "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    if download_dir:
        download_dir = os.path.abspath(download_dir)
        prefs = {
            "download.default_directory": download_dir,
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
            "plugins.always_open_pdf_externally": True,
        }
        chrome_options.add_experimental_option("prefs", prefs)

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()), options=chrome_options
    )
    driver.set_page_load_timeout(60)
    return driver


def parse_solicitations_from_html(soup):
    """
    Parse the LaPAC search results HTML into structured solicitation data.

    LaPAC table structure (5 columns):
      Cell 0: Bid Number (plain text)
      Cell 1: Description (contains PDF link for "Original:" and attachment links)
      Cell 2: Date Issued
      Cell 3: Bid Open Date/Time
      Cell 4: Help code

    Addendum rows have only 2 cells and appear below their parent bid.
    """
    solicitations = []

    tables = soup.find_all("table")
    results_table = None
    for t in tables:
        text = t.get_text(strip=True)
        if "Bid Number" in text and "Date Issued" in text:
            results_table = t
            break

    if not results_table:
        print("Could not find the solicitations results table")
        return solicitations

    rows = results_table.find_all("tr")
    if not rows:
        return solicitations

    current_sol = None

    for row in rows:
        cells = row.find_all("td")

        if not cells:
            continue

        row_text = row.get_text(strip=True)
        if "Bid Number" in row_text and "Date Issued" in row_text and "Help" in row_text:
            continue

        if len(cells) >= 5:
            bid_number = cells[0].get_text(strip=True)
            if not bid_number:
                continue

            if current_sol and current_sol.get("bid_number"):
                solicitations.append(current_sol)

            current_sol = {
                "bid_number": bid_number,
                "description": "",
                "date_issued": "",
                "bid_open_date": "",
                "help_code": "",
                "pdf_url": "",
                "attachment_links": [],
            }

            desc_cell = cells[1]
            desc_links = desc_cell.find_all("a", href=True)

            desc_parts = []
            for child in desc_cell.children:
                if hasattr(child, 'name') and child.name == 'a':
                    break
                text_piece = child.string if hasattr(child, 'string') and child.string else ""
                if text_piece and text_piece.strip():
                    desc_parts.append(text_piece.strip())
            raw_desc = " ".join(desc_parts) if desc_parts else desc_cell.get_text(separator=" ", strip=True)

            for tag in ["Original:", "Attachments:", "Attachment"]:
                idx = raw_desc.find(tag)
                if idx > 0:
                    raw_desc = raw_desc[:idx].strip()
            current_sol["description"] = raw_desc

            for link in desc_links:
                href = link.get("href", "")
                link_text = link.get_text(strip=True)
                if "/agency/pdf/" not in href:
                    continue
                full_url = urljoin(LA_BASE_URL, href)

                if link_text == bid_number or not current_sol["pdf_url"]:
                    current_sol["pdf_url"] = full_url
                else:
                    current_sol["attachment_links"].append({
                        "url": full_url,
                        "filename": link_text,
                    })

            current_sol["date_issued"] = cells[2].get_text(strip=True)
            current_sol["bid_open_date"] = cells[3].get_text(strip=True)
            current_sol["help_code"] = cells[4].get_text(strip=True)

        elif current_sol and len(cells) < 5:
            all_links = row.find_all("a", href=True)
            for link in all_links:
                href = link.get("href", "")
                link_text = link.get_text(strip=True)
                if "/agency/pdf/" in href and link_text:
                    full_url = urljoin(LA_BASE_URL, href)
                    if full_url != current_sol.get("pdf_url"):
                        current_sol["attachment_links"].append({
                            "url": full_url,
                            "filename": link_text,
                        })

    if current_sol and current_sol.get("bid_number"):
        solicitations.append(current_sol)

    return solicitations


def fetch_la_solicitations(issue_date=None):
    """
    Fetch solicitations from LaPAC filtered by issue date.

    Args:
        issue_date (str): Date in MM/DD/YYYY format to filter by issue date.
                         Both dateStart and dateEnd will use this date.

    Returns:
        list: List of solicitation dictionaries
    """
    print(f"\n{'='*80}")
    print("FETCHING LOUISIANA LaPAC SOLICITATIONS")
    print(f"{'='*80}")

    driver = None
    try:
        driver = setup_chrome()

        if issue_date:
            encoded_date = quote(issue_date, safe="")
            search_url = (
                f"{LA_SEARCH_URL}?catno=all&compareDate=I"
                f"&dateEnd={encoded_date}&dateStart={encoded_date}"
                f"&deptno=all&keywords=&keywordsCheck=all"
            )
        else:
            search_url = (
                f"{LA_SEARCH_URL}?catno=all&compareDate=O"
                f"&dateEnd=&dateStart="
                f"&deptno=all&keywords=&keywordsCheck=all"
            )

        print(f"Navigating to: {search_url}")
        driver.get(search_url)
        time.sleep(5)

        page_source = driver.page_source

        soup = BeautifulSoup(page_source, "html.parser")

        no_results_markers = [
            "no bids were found",
            "no records found",
            "0 records found",
            "no solicitations found",
        ]
        page_text_lower = soup.get_text().lower()
        for marker in no_results_markers:
            if marker in page_text_lower:
                print(f"No solicitations found ('{marker}' detected)")
                return []

        solicitations = parse_solicitations_from_html(soup)

        print(f"\n{'='*80}")
        print(f"Total solicitations scraped: {len(solicitations)}")
        if issue_date:
            print(f"Filtered by issue date: {issue_date}")
        print(f"{'='*80}")

        for i, sol in enumerate(solicitations[:10], 1):
            print(f"  {i}. {sol['bid_number']} - {sol['description'][:60]}")
            print(f"     PDF: {sol['pdf_url']}")
            print(f"     Attachments: {len(sol['attachment_links'])}")

        return solicitations

    except Exception as e:
        print(f"Error fetching LA solicitations: {e}")
        import traceback
        traceback.print_exc()
        return []

    finally:
        if driver:
            driver.quit()
            print("Browser closed")


def download_la_files(solicitation):
    """
    Download the main bid PDF and all attachment files for a solicitation.

    Args:
        solicitation (dict): Solicitation dict with pdf_url and attachment_links

    Returns:
        dict: {
            'files': list of downloaded filenames,
            'download_dir': path to the download directory,
            'page_text': description text from the solicitation
        }
    """
    bid_number = solicitation["bid_number"]
    safe_id = re.sub(r"[^\w\-]", "_", bid_number)
    download_dir = os.path.abspath(os.path.join(LA_DOWNLOADS_DIR, safe_id))
    os.makedirs(download_dir, exist_ok=True)

    print(f"    Downloading files for: {bid_number}")

    downloaded_files = []
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    })

    if solicitation.get("pdf_url"):
        pdf_url = solicitation["pdf_url"]
        pdf_filename = os.path.basename(pdf_url)
        if not pdf_filename:
            pdf_filename = f"{safe_id}.pdf"
        filepath = os.path.join(download_dir, pdf_filename)

        try:
            print(f"      Downloading main PDF: {pdf_filename}")
            resp = session.get(pdf_url, timeout=60)
            resp.raise_for_status()
            with open(filepath, "wb") as f:
                f.write(resp.content)
            size_kb = len(resp.content) / 1024
            print(f"      Downloaded {pdf_filename} ({size_kb:.1f} KB)")
            downloaded_files.append(pdf_filename)
        except Exception as e:
            print(f"      Failed to download main PDF: {e}")

    for att in solicitation.get("attachment_links", []):
        att_url = att["url"]
        att_filename = att.get("filename", os.path.basename(att_url))
        safe_filename = re.sub(r'[^\w\-_\. ]', '_', att_filename)
        if not safe_filename:
            safe_filename = os.path.basename(att_url)

        if not os.path.splitext(safe_filename)[1]:
            url_ext = os.path.splitext(att_url)[1]
            if url_ext:
                safe_filename += url_ext

        filepath = os.path.join(download_dir, safe_filename)

        try:
            print(f"      Downloading attachment: {safe_filename}")
            resp = session.get(att_url, timeout=60)
            resp.raise_for_status()
            with open(filepath, "wb") as f:
                f.write(resp.content)
            size_kb = len(resp.content) / 1024
            print(f"      Downloaded {safe_filename} ({size_kb:.1f} KB)")
            downloaded_files.append(safe_filename)
        except Exception as e:
            print(f"      Failed to download {safe_filename}: {e}")

    page_text = (
        f"Louisiana LaPAC Solicitation\n"
        f"Bid Number: {bid_number}\n"
        f"Description: {solicitation.get('description', '')}\n"
        f"Date Issued: {solicitation.get('date_issued', '')}\n"
        f"Bid Open Date/Time: {solicitation.get('bid_open_date', '')}\n"
        f"Help Code: {solicitation.get('help_code', '')}\n"
    )

    return {
        "files": downloaded_files,
        "download_dir": download_dir,
        "page_text": page_text,
    }


def extract_text_from_la_files(download_dir):
    """Extract text from all downloaded LA files."""
    combined_text = ""

    if not os.path.exists(download_dir):
        return combined_text

    for filename in os.listdir(download_dir):
        filepath = os.path.join(download_dir, filename)

        if os.path.isdir(filepath):
            continue

        try:
            with open(filepath, "rb") as f:
                content = f.read()

            extracted = extract_text_from_file_content(filename, content)

            if extracted:
                combined_text += f"\n\n=== FILE: {filename} ===\n"
                combined_text += "-" * 50 + "\n"
                combined_text += extracted + "\n"
            else:
                combined_text += f"\n\n=== FILE: {filename} ===\n[No text extracted]\n"

        except Exception as e:
            combined_text += f"\n\n=== FILE: {filename} ===\n[ERROR: {e}]\n"

    return combined_text


def upload_la_files_to_drive(bid_number, download_dir):
    """Upload downloaded LA files to Google Drive and return the folder link."""
    try:
        print(f"    Uploading LA files to Google Drive for: {bid_number}")

        if not download_dir or not os.path.exists(download_dir):
            print(f"    No download directory found for {bid_number}")
            return None

        files_to_upload = []
        for filename in os.listdir(download_dir):
            file_path = os.path.join(download_dir, filename)
            if os.path.isfile(file_path):
                files_to_upload.append(file_path)

        if not files_to_upload:
            print(f"    No files found to upload for {bid_number}")
            return None

        print(f"    Found {len(files_to_upload)} files to upload")

        access_token = get_drive_access_token()
        if not access_token:
            print("    Failed to get Google Drive access token")
            return None

        folder_name = f"LA_{bid_number}"
        folder_id, folder_link = create_drive_folder(access_token, folder_name, DRIVE_PARENT_FOLDER_ID)

        if not folder_id:
            print("    Failed to create Google Drive folder")
            return None

        uploaded_count = 0
        for file_path in files_to_upload:
            if upload_file_to_drive(access_token, file_path, folder_id):
                uploaded_count += 1

        print(f"    Uploaded {uploaded_count}/{len(files_to_upload)} files to Google Drive")
        print(f"    Folder link: {folder_link}")

        return folder_link

    except Exception as e:
        print(f"    Error uploading LA files to Google Drive: {e}")
        return None


def process_la_with_openai(bid_number, page_text, files_text):
    """Process a LA solicitation with OpenAI for vendor lead generation."""
    complete_text = f"""
LOUISIANA LaPAC SOLICITATION:
Bid Number: {bid_number}

{page_text}

DOWNLOADED FILES CONTENT:
{files_text}
"""

    MAX_CHARS = 200000
    if len(complete_text) > MAX_CHARS:
        print(f"    Truncating text from {len(complete_text)} to {MAX_CHARS} characters")
        complete_text = complete_text[:MAX_CHARS]

    print(f"    Total text for OpenAI: {len(complete_text)} characters")

    result = generate_vendor_leads(
        solicitation_text=complete_text,
        source="Louisiana LaPAC",
        subject_suffix="",
    )
    return result


def process_la_solicitations(
    issue_date=None,
    max_to_process=None,
    add_to_sheets=True,
):
    """
    Main workflow: fetch LA solicitations, download files, process with OpenAI,
    and add results to Google Sheets.

    Args:
        issue_date (str): Filter by issue date (MM/DD/YYYY)
        max_to_process (int): Limit number of solicitations to process (None = all)
        add_to_sheets (bool): Whether to add results to Google Sheets

    Returns:
        list: Results from processing
    """
    print("\n" + "=" * 80)
    print("LOUISIANA LaPAC SOLICITATIONS PROCESSOR")
    print("=" * 80)
    if issue_date:
        print(f"Issue Date Filter: {issue_date}")
    print("=" * 80)

    date_key = issue_date or "all_open"

    print("\nStep 1: Fetching solicitations from LaPAC...")
    solicitations = load_solicitations_cache(date_key)
    if solicitations is None:
        solicitations = fetch_la_solicitations(issue_date=issue_date)
        if solicitations:
            save_solicitations_cache(solicitations, date_key)

    if not solicitations:
        print("No solicitations found. Exiting.")
        return []

    print(f"\nFound {len(solicitations)} solicitations")

    to_process = solicitations[:max_to_process] if max_to_process else solicitations
    print(f"Will process {len(to_process)} solicitation(s)")

    for i, sol in enumerate(to_process[:10], 1):
        print(f"  {i}. {sol['bid_number']} - {sol.get('description', 'N/A')[:60]}")
    if len(to_process) > 10:
        print(f"  ... and {len(to_process) - 10} more")

    local_contracts_wks = None
    skipped_wks = None
    existing_ids = set()

    if add_to_sheets:
        print("\nStep 2: Connecting to Google Sheets...")
        try:
            import pygsheets

            gc = pygsheets.authorize(service_file="key.json")
            spreadsheet = gc.open("Quote Request")
            local_contracts_wks = spreadsheet.worksheet_by_title("localContracts")
            print("  Connected to 'localContracts' worksheet")

            existing_records = local_contracts_wks.get_all_records()
            existing_ids = {
                str(r.get("Solicitation ID", "")).strip() for r in existing_records
            }
            print(f"  Found {len(existing_ids)} existing records (for duplicate check)")

            try:
                skipped_wks = spreadsheet.worksheet_by_title("localContracts_skipped")
                print("  Connected to 'localContracts_skipped' worksheet")
            except Exception:
                try:
                    skipped_wks = spreadsheet.add_worksheet("localContracts_skipped", rows=1000, cols=10)
                    skipped_wks.update_value("A1", "Name")
                    skipped_wks.update_value("B1", "Solicitation ID")
                    skipped_wks.update_value("C1", "Due Date")
                    skipped_wks.update_value("D1", "Status")
                    skipped_wks.update_value("E1", "Reasoning")
                    skipped_wks.update_value("F1", "Attachment URL")
                    print("  Created 'localContracts_skipped' worksheet with headers")
                except Exception as e2:
                    print(f"  Could not create 'localContracts_skipped' worksheet: {e2}")

        except Exception as e:
            print(f"  Error connecting to Google Sheets: {e}")
            print("  Will continue processing without adding to sheets")
            local_contracts_wks = None

    gmail_client = None
    if add_to_sheets:
        print("Authenticating Gmail client...")
        gmail_client = GmailClient(NEXAN_ACCOUNT_CONFIG)
        if not gmail_client.authenticate():
            print("Failed to authenticate Gmail. Continuing without draft creation.")
            gmail_client = None

    print(f"\nStep 3: Processing {len(to_process)} solicitation(s)...")
    progress = load_all_progress()
    results = []
    skipped_dup = 0
    skipped_filtered = 0
    skipped_no_files = 0
    skipped_cached = 0
    failed = 0

    for i, sol in enumerate(to_process, 1):
        bid_number = sol["bid_number"]
        description = sol.get("description", "N/A")
        bid_open_date = sol.get("bid_open_date", "")
        solicitation_url = sol.get("pdf_url", "")

        print(f"\n{'='*80}")
        print(f"[{i}/{len(to_process)}] {bid_number} - {description[:70]}")
        print(f"{'='*80}")

        if solicitation_url in existing_ids or bid_number in existing_ids:
            print("  SKIP: Already exists in Google Sheets")
            skipped_dup += 1
            continue

        cached_progress = progress.get(bid_number)
        if cached_progress:
            cached_status = cached_progress.get("status", "")
            if cached_status in ("added_to_sheets", "skipped_filtered", "skipped_duplicate"):
                print(f"  SKIP (cached): {cached_status} - {cached_progress.get('data', {}).get('reason', '')[:80]}")
                skipped_cached += 1
                continue

        try:
            sol_cache = load_solicitation_cache(bid_number)
            page_text = ""
            files_text = ""
            site_visit_result = None
            download_dir = ""

            if sol_cache and sol_cache.get("filter_result"):
                print("  Using cached download + filter results")
                page_text = sol_cache.get("page_text", "")
                files_text = sol_cache.get("files_text", "")
                site_visit_result = sol_cache["filter_result"]
                safe_id = re.sub(r"[^\w\-]", "_", bid_number)
                download_dir = os.path.abspath(os.path.join(LA_DOWNLOADS_DIR, safe_id))
            else:
                print("  Downloading files...")
                dl_result = download_la_files(sol)
                page_text = dl_result.get("page_text", "")
                downloaded_files = dl_result.get("files", [])
                download_dir = dl_result.get("download_dir", "")

                print(f"  Downloaded {len(downloaded_files)} file(s)")

                if downloaded_files:
                    print("  Extracting text from files...")
                    files_text = extract_text_from_la_files(download_dir)
                    print(f"  Extracted {len(files_text)} characters")
                else:
                    print("  No files downloaded, using page text only")

                print("  Checking filters: site visit, controlled attachments, in-person submission, missing bid docs...")
                check_text = f"{page_text}\n\n{files_text}"
                site_visit_result = has_site_visit(check_text, check_in_person_submission=True)

                save_solicitation_cache(bid_number, page_text, files_text, site_visit_result)

            print(f"    Site visit: {site_visit_result['has_site_visit']}")
            print(f"    Controlled attachments: {site_visit_result['has_controlled_attachments']}")
            print(f"    In-person submission: {site_visit_result.get('requires_in_person_submission', False)}")
            print(f"    Missing bid documents: {site_visit_result.get('missing_bid_documents', False)}")
            print(f"    Reasoning: {site_visit_result['reasoning']}")

            skip_status = None
            if site_visit_result["has_site_visit"]:
                skip_status = "Site Visit Required"
            elif site_visit_result["has_controlled_attachments"]:
                skip_status = "Controlled Attachments"
            elif site_visit_result.get("requires_in_person_submission", False):
                skip_status = "In-Person Submission Only"
            elif site_visit_result.get("missing_bid_documents", False):
                ext_url = site_visit_result.get("external_documents_url") or solicitation_url
                skip_status = f"Bid Docs External: {ext_url}"

            if skip_status:
                print(f"  SKIP: {skip_status} - {site_visit_result['reasoning'][:100]}")
                save_progress(bid_number, "skipped_filtered", {"reason": skip_status})
                if skipped_wks:
                    try:
                        add_row_to_skipped_contracts(
                            skipped_wks, description, solicitation_url or bid_number,
                            bid_open_date, skip_status,
                            site_visit_result["reasoning"],
                            solicitation_url,
                        )
                    except Exception as e:
                        print(f"  Failed to add to localContracts_skipped: {e}")
                skipped_filtered += 1
                continue

            if sol_cache and sol_cache.get("lead_result") and "ERROR" not in str(sol_cache["lead_result"].get("emails", "")):
                print("  Using cached OpenAI lead results")
                lead_result = sol_cache["lead_result"]
            else:
                print("  Processing with OpenAI...")
                lead_result = process_la_with_openai(bid_number, page_text, files_text)

                save_solicitation_cache(bid_number, page_text, files_text, site_visit_result, lead_result)

            if "ERROR" in str(lead_result.get("emails", "")):
                print(f"  FAILED: OpenAI error - {lead_result.get('emails', '')[:100]}")
                save_progress(bid_number, "failed_openai", {"error": str(lead_result.get("emails", ""))[:200]})
                if skipped_wks:
                    try:
                        add_row_to_skipped_contracts(
                            skipped_wks, description, solicitation_url or bid_number,
                            bid_open_date, "Lead Gen Failed",
                            f"OpenAI error: {str(lead_result.get('emails', ''))[:200]}",
                            solicitation_url,
                        )
                    except Exception as e:
                        print(f"  Failed to add to localContracts_skipped: {e}")
                failed += 1
                continue

            print("  OpenAI processing successful!")

            print("  Uploading files to Google Drive...")
            drive_folder_link = upload_la_files_to_drive(bid_number, download_dir)

            if local_contracts_wks:
                try:
                    subject = lead_result.get("subject", "Not found")
                    if subject and subject != "Not found":
                        subject = subject + " - laLocal"

                    reasoning = (
                        f"LA LaPAC | Bid#: {bid_number} | "
                        f"Issued: {sol.get('date_issued', 'N/A')} | "
                        f"Help: {sol.get('help_code', 'N/A')}"
                    )

                    target_row = add_row_to_local_contracts(
                        local_contracts_wks=local_contracts_wks,
                        name=description,
                        solicitation_id=solicitation_url,
                        due_date=bid_open_date,
                        status="LA Local - Leads Generated",
                        reasoning=reasoning,
                        subject=subject,
                        body=lead_result.get("body", "Not found"),
                        emails=lead_result.get("emails", "Not found"),
                        folder_link=drive_folder_link,
                    )

                    print(f"  Added to localContracts (row {target_row})")
                    existing_ids.add(bid_number)
                    existing_ids.add(solicitation_url)
                    save_progress(bid_number, "added_to_sheets", {"row": target_row})

                    create_email_draft(
                        lead_result.get('emails', ''),
                        subject,
                        lead_result.get('body', ''),
                        gmail_client,
                    )

                except Exception as e:
                    print(f"  Error adding to Google Sheets: {e}")
                    save_progress(bid_number, "failed_sheets", {"error": str(e)[:200]})

            results.append({
                "bid_number": bid_number,
                "solicitation_url": solicitation_url,
                "description": description,
                "files_downloaded": len(os.listdir(download_dir)) if download_dir and os.path.exists(download_dir) else 0,
                "emails": lead_result.get("emails", ""),
                "subject": lead_result.get("subject", ""),
            })

            print(f"  SUCCESS")

        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback
            traceback.print_exc()
            save_progress(bid_number, "failed_error", {"error": str(e)[:200]})
            failed += 1

    print(f"\n{'='*80}")
    print("LOUISIANA LaPAC PROCESSING COMPLETE")
    print(f"{'='*80}")
    print(f"Total solicitations found:    {len(solicitations)}")
    print(f"Processed:                    {len(to_process)}")
    print(f"Successfully added to sheets: {len(results)}")
    print(f"Skipped (duplicates):         {skipped_dup}")
    print(f"Skipped (cached/done):        {skipped_cached}")
    print(f"Skipped (filtered):           {skipped_filtered}")
    print(f"Skipped (no files):           {skipped_no_files}")
    print(f"Failed:                       {failed}")
    print(f"{'='*80}")

    return results


def auto_process_recent_la_solicitations():
    """
    Automatically fetch and process LA solicitations issued yesterday.
    Designed for daily cron job execution.
    """
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%m/%d/%Y")

    print(f"\nAuto-processing LA solicitations issued on: {yesterday}")

    return process_la_solicitations(
        issue_date=yesterday,
        max_to_process=None,
        add_to_sheets=True,
    )


def main():
    """Main entry point - processes recent LA solicitations."""
    print("\n" + "=" * 80)
    print("LOUISIANA LaPAC AUTO PROCESSOR")
    print("=" * 80)

    auto_process_recent_la_solicitations()


if __name__ == "__main__":
    main()
