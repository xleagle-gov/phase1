#!/usr/bin/env python3
"""
Local Contracts Analysis
Functions for analyzing local contract opportunities (ESBD, etc.)
"""

import os
from main import fetch_ui_link_data
from download_esbd_files import download_esbd_files, DOWNLOADS_DIR as ESBD_DOWNLOADS_DIR
from gemini import analyze_contract_text, has_site_visit
from generateLeads import process_single_solicitation
from backfillfolderLinks import get_drive_access_token, create_drive_folder, upload_file_to_drive
import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException
from runN8nFlows import call_LocalContractFlow, call_samGovFlow
import re
from list_rfq_drafts import rename_rfq_drafts
from get_empty_rows import GmailClient, NEXAN_ACCOUNT_CONFIG, create_email_draft

# Import from centralized config and services
from config import DRIVE_PARENT_FOLDER_ID, EAST_TX_COUNTIES, OPENAI_API_KEY
from services.openai_service import generate_vendor_leads

# Google Drive folder for ESBD/local contract files (use centralized config)
ESBD_PARENT_FOLDER_ID = DRIVE_PARENT_FOLDER_ID


def classify_county(solicitation_text):
    """
    Use AI to determine which Texas county a solicitation's work is performed in.
    Returns the county name (e.g. "Jefferson") or "Unknown".
    """
    import openai

    prompt = (
        f"You are given a Texas government solicitation. Determine which Texas county "
        f"the work or service will be performed in.\n\n"
        f"Return ONLY the county name — a single name like \"Jefferson\" or \"Angelina\".\n"
        f"If the solicitation explicitly mentions a county, use that.\n"
        f"If it mentions a city or location, infer the county from that.\n"
        f"If the county truly cannot be determined, return \"Unknown\".\n\n"
        f"Solicitation text:\n{solicitation_text}"
    )

    try:
        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        response = client.responses.create(
            model="gpt-5-mini",
            input=[{"role": "user", "content": prompt}],
            stream=False,
        )
        county = (response.output_text or "Unknown").strip().strip('"').strip("'")
        # Normalise: strip trailing " County" if the model included it
        if county.lower().endswith(" county"):
            county = county[: -len(" county")].strip()
        print(f"🗺️  AI county classification: {county}")
        return county
    except Exception as e:
        print(f"⚠️ County classification failed: {e}")
        return "Unknown"



def fetch_esbd_ui_data(esbd_url):
    """
    Fetch ESBD page content using Selenium to handle JavaScript-rendered content.
    Designed specifically for Texas SmartBuy ESBD pages.
    
    Args:
        esbd_url (str): The ESBD URL to fetch
        
    Returns:
        dict: Contains 'title' and 'text_content' keys, or None if failed
    """
    print(f"Fetching ESBD page content from: {esbd_url}")
    
    driver = None
    try:
        # Configure Chrome options for ESBD pages
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--user-agent=Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Mobile Safari/537.36")
        
        # Initialize driver
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
        driver.set_page_load_timeout(30)
        
        # Load the page
        print("Loading ESBD page...")
        driver.get(esbd_url)
        
        # Wait for the page to load - look for ESBD-specific content
        print("Waiting for ESBD content to load...")
        try:
            # Wait for solicitation details to appear
            WebDriverWait(driver, 15).until(
                lambda d: "Solicitation ID:" in d.page_source or 
                         "Contact Name:" in d.page_source or
                         len(d.page_source) > 5000
            )
            print("ESBD content detected")
        except TimeoutException:
            print("Timeout waiting for ESBD content, proceeding anyway...")
        
        # Give extra time for dynamic content to load
        time.sleep(3)
        
        # Parse with BeautifulSoup
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        # Extract title
        title = "ESBD Solicitation"
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        
        # Try to find the main ESBD content container first
        esbd_container = soup.find('div', class_='esbd-container')
        if esbd_container:
            print("Found ESBD container, extracting content from it...")
            # Remove script and style elements from the container
            for script in esbd_container(["script", "style"]):
                script.decompose()
            text_content = esbd_container.get_text(separator='\n', strip=True)
        else:
            print("ESBD container not found, extracting from full page...")
            # Remove script and style elements
            for script in soup(["script", "style"]):
                script.decompose()
            text_content = soup.get_text(separator='\n', strip=True)
        
        # Clean up excessive whitespace
        lines = [line.strip() for line in text_content.split('\n') if line.strip()]
        clean_text = '\n'.join(lines)
        
        print(f"Successfully extracted {len(clean_text)} characters of content")
        
        # Check if we got meaningful content
        if len(clean_text) < 500 or "Javascript is disabled" in clean_text:
            print("Warning: Content seems minimal or JavaScript-disabled message detected")
            # Save debug HTML
            with open('debug_esbd_content.html', 'w', encoding='utf-8') as f:
                f.write(driver.page_source)
            print("Saved debug HTML to debug_esbd_content.html")
        
        return {
            'title': title,
            'text_content': clean_text
        }
        
    except Exception as e:
        print(f"Error fetching ESBD page: {e}")
        return None
    finally:
        if driver:
            driver.quit()
            print("Browser closed")

def extract_attachment_url(ui_data):
    """
    Extract the attachment URL from ESBD UI data.
    
    Args:
        ui_data (dict): UI data containing 'text_content'
        
    Returns:
        str: The attachment URL or None if not found
    """
    if not ui_data or 'text_content' not in ui_data:
        return None
    
    text_content = ui_data['text_content']
    
    # Look for "Attachment URL:" followed by the URL
    attachment_pattern = r'Attachment URL:\s*(https?://[^\s\n]+)'
    match = re.search(attachment_pattern, text_content)
    
    if match:
        attachment_url = match.group(1)
        print(f"Found attachment URL: {attachment_url}")
        return attachment_url
    else:
        print("No attachment URL found in the content")
        return None

def upload_esbd_files_to_drive(solicitation_id):
    """
    Upload downloaded ESBD files to Google Drive and return the folder link.
    
    Parameters:
    - solicitation_id: The ESBD solicitation ID
    
    Returns:
    - folder_link: Google Drive folder link, or None if failed
    """
    try:
        print(f"\n📁 Uploading ESBD files to Google Drive for solicitation: {solicitation_id}")
        
        # Check if files exist in the download directory
        download_dir = os.path.join(ESBD_DOWNLOADS_DIR, solicitation_id)
        extract_dir = os.path.join(ESBD_DOWNLOADS_DIR, solicitation_id, "extracted")
        
        # Collect all files to upload
        files_to_upload = []
        
        # Check extracted directory first
        if os.path.exists(extract_dir):
            for filename in os.listdir(extract_dir):
                file_path = os.path.join(extract_dir, filename)
                if os.path.isfile(file_path):
                    files_to_upload.append(file_path)
        
        # Also check the main download directory for any files
        if os.path.exists(download_dir):
            for filename in os.listdir(download_dir):
                file_path = os.path.join(download_dir, filename)
                # Skip the extracted folder and the consolidated extracted text file
                if os.path.isfile(file_path) and not filename.endswith('_extracted_text.txt'):
                    files_to_upload.append(file_path)
        
        if not files_to_upload:
            print(f"⚠️ No files found to upload for solicitation {solicitation_id}")
            return None
        
        print(f"Found {len(files_to_upload)} files to upload")
        
        # Get Google Drive access token
        access_token = get_drive_access_token()
        if not access_token:
            print("❌ Failed to get Google Drive access token")
            return None
        
        # Create folder in Google Drive
        folder_name = f"ESBD_{solicitation_id}"
        folder_id, folder_link = create_drive_folder(access_token, folder_name, ESBD_PARENT_FOLDER_ID)
        
        if not folder_id:
            print("❌ Failed to create Google Drive folder")
            return None
        
        # Upload all files
        uploaded_count = 0
        for file_path in files_to_upload:
            if upload_file_to_drive(access_token, file_path, folder_id):
                uploaded_count += 1
        
        print(f"✅ Uploaded {uploaded_count}/{len(files_to_upload)} files to Google Drive")
        print(f"📎 Folder link: {folder_link}")
        
        return folder_link
        
    except Exception as e:
        print(f"❌ Error uploading ESBD files to Google Drive: {e}")
        return None

def process_esbd_text_with_openai(esbd_url, complete_text):
    """
    Process ESBD text using OpenAI to generate vendor leads.
    Uses the centralized OpenAI service.
    
    Args:
        esbd_url (str): The ESBD URL
        complete_text (str): Already extracted text from ESBD page and files
        
    Returns:
        dict: Result with emails, subject, and body
    """
    print("Processing ESBD text with OpenAI for lead generation...")
    
    # Use the centralized OpenAI service
    result = generate_vendor_leads(
        solicitation_text=complete_text,
        source="ESBD",
        subject_suffix=""  # No suffix for ESBD, suffix added later in processEsbdSolicitations
    )
    
    print(f"✅ Successfully processed ESBD text for lead generation")
    return result

def count_attached_files(text_content):
    """Count the number of attached files mentioned in the ESBD page text."""
    file_pattern = r'\b[\w\-\.]+\.(?:pdf|doc|docx|xls|xlsx|zip|txt|csv|ppt|pptx)\b'
    matches = re.findall(file_pattern, text_content, re.IGNORECASE)
    unique_files = set(matches)
    return len(unique_files)

def can_apply_without_registration(esbd_url, generate_leads=True):
    """
    Determines if we can apply to an ESBD solicitation without registering in a different portal.
    If can apply, optionally generates vendor leads using OpenAI.
    
    Args:
        esbd_url (str): The ESBD URL to analyze
        generate_leads (bool): If True and can_apply is True, generate vendor leads
        
    Returns:
        dict: Analysis result with 'can_apply', 'reasoning', and optionally lead generation results
    """
    print(f"Analyzing ESBD opportunity: {esbd_url}")
    
    try:
        # Step 1: Get UI data from the ESBD page using the new simple function
        print("Step 1: Fetching UI data...")
        ui_data = fetch_esbd_ui_data(esbd_url)
        
        if ui_data is None:
            return {
                "can_apply": False,
                "reasoning": "Failed to fetch ESBD page content",
                "lead_generation": None,
                "county": "Unknown",
            }
        
        # Save for debugging
        with open("ui_data.txt", "w", encoding="utf-8") as f:
            f.write(json.dumps(ui_data, indent=2))
        
        print(f"Successfully fetched {len(ui_data.get('text_content', ''))} characters of content")
        
        # Extract attachment URL
        attachment_url = extract_attachment_url(ui_data)
        
        # Skip URLs that are known to be irrelevant (no AI, no skipped sheet)
        SKIP_URLS = [
            "https://www.txdot.gov/business/letting-bids/letting.html",
        ]
        if attachment_url and any(skip_url in attachment_url for skip_url in SKIP_URLS):
            print(f"Skipping silently — blocked attachment URL: {attachment_url}")
            return {
                "can_apply": False,
                "skip_silently": True,
                "reasoning": f"Blocked attachment URL: {attachment_url}",
                "attachment_url": attachment_url,
                "lead_generation": None,
                "county": "Unknown",
            }
        
        # Step 2: Analyze the content to determine if we can apply
        text_content = ui_data.get('text_content', '')
        
        can_apply = True
        reasoning = "Can apply directly through ESBD"
        if attachment_url:
            print(f"Attachment URL found: {attachment_url} — will verify via LLM if bid docs are missing")
        
        # Step 3: If can apply and leads requested, download files and generate leads
        lead_generation = None
        if can_apply and generate_leads:
            print("Step 3: Can apply! Downloading files and generating vendor leads...")
            
            # Download ESBD files first
            try:
                print("Downloading ESBD files...")
                download_result = download_esbd_files(esbd_url)
                if download_result and download_result.get('text'):
                    print(f"Successfully downloaded {len(download_result.get('files', []))} files")
                    complete_text = text_content + "\n\n" + download_result['text']
                else:
                    print("No file text extracted, using page content only")
                    complete_text = text_content
            except Exception as e:
                print(f"Error downloading files: {e}. Using page content only.")
                complete_text = text_content
            
            # Classify which county this contract is in (before filters so
            # East TX contracts are captured even when skipped)
            county = classify_county(complete_text)

            # Check for mandatory site visit, controlled attachments, in-person submission, and missing bid docs
            print("Checking filters: site visit, controlled attachments, in-person submission, missing bid docs...")
            site_visit_result = has_site_visit(complete_text, check_in_person_submission=True)
            print(f"  Site visit detected: {site_visit_result['has_site_visit']}")
            print(f"  Controlled attachments detected: {site_visit_result['has_controlled_attachments']}")
            print(f"  In-person submission only: {site_visit_result.get('requires_in_person_submission', False)}")
            print(f"  Missing bid documents: {site_visit_result.get('missing_bid_documents', False)}")
            print(f"  External documents URL: {site_visit_result.get('external_documents_url', None)}")
            print(f"  Reasoning: {site_visit_result['reasoning']}")
            if site_visit_result["has_site_visit"]:
                print("Skipping: Contract has a site visit/pre-proposal conference.")
                return {
                    "can_apply": False,
                    "status": "Site Visit Required",
                    "reasoning": site_visit_result['reasoning'],
                    "attachment_url": attachment_url,
                    "lead_generation": None,
                    "county": county,
                }
            if site_visit_result["has_controlled_attachments"]:
                print("Skipping: Contract has controlled (non-public) attachments.")
                return {
                    "can_apply": False,
                    "status": "Controlled Attachments",
                    "reasoning": site_visit_result['reasoning'],
                    "attachment_url": attachment_url,
                    "lead_generation": None,
                    "county": county,
                }
            if site_visit_result.get("requires_in_person_submission", False):
                print("Skipping: Contract only accepts in-person submission.")
                return {
                    "can_apply": False,
                    "status": "In-Person Submission Only",
                    "reasoning": site_visit_result['reasoning'],
                    "attachment_url": attachment_url,
                    "lead_generation": None,
                    "county": county,
                }
            if site_visit_result.get("missing_bid_documents", False):
                ext_url = site_visit_result.get("external_documents_url") or attachment_url or "unknown"

                # If the external URL is a Bonfire portal, download files from there
                from bonfire_downloader import is_bonfire_url
                if is_bonfire_url(ext_url):
                    print(f"Bonfire URL detected: {ext_url} — downloading bid docs from Bonfire...")
                    sol_id = esbd_url.rstrip("/").split("/")[-1]
                    try:
                        from bonfire_downloader import get_bonfire_session
                        bf = get_bonfire_session()
                        bf_result = bf.download_solicitation_files(ext_url, sol_id)
                        if bf_result and bf_result.get("text"):
                            print(f"Bonfire download success: {len(bf_result['files'])} files")
                            complete_text = complete_text + "\n\n" + bf_result["text"]
                            # Fall through to lead generation below with enriched text
                        elif bf_result and bf_result.get("files"):
                            print(f"Bonfire files downloaded but no text extracted, proceeding anyway")
                        else:
                            print(f"Bonfire download failed — skipping as Bid Docs External")
                            return {
                                "can_apply": False,
                                "status": "Bid Docs External",
                                "reasoning": site_visit_result['reasoning'],
                                "attachment_url": ext_url,
                                "lead_generation": None,
                                "county": county,
                            }
                    except Exception as e:
                        print(f"Bonfire download error: {e} — skipping as Bid Docs External")
                        return {
                            "can_apply": False,
                            "status": "Bid Docs External",
                            "reasoning": site_visit_result['reasoning'],
                            "attachment_url": ext_url,
                            "lead_generation": None,
                            "county": county,
                        }
                    # Store bonfire file info for Drive upload later
                    bonfire_files = bf_result.get("files", []) if bf_result else []
                else:
                    print(f"Skipping: Bid documents hosted elsewhere: {ext_url}")
                    return {
                        "can_apply": False,
                        "status": "Bid Docs External",
                        "reasoning": site_visit_result['reasoning'],
                        "attachment_url": ext_url,
                        "lead_generation": None,
                        "county": county,
                    }

            # Generate leads using OpenAI
            lead_generation = process_esbd_text_with_openai(esbd_url, complete_text)
        
        result = {
            "can_apply": can_apply,
            "reasoning": reasoning,
            "attachment_url": attachment_url,
            "lead_generation": lead_generation,
            "county": county if 'county' in dir() else "Unknown",
        }
        # If we downloaded files from Bonfire, pass them for Drive upload
        if 'bonfire_files' in dir():
            result["bonfire_files"] = bonfire_files
        return result
          
    except Exception as e:
        print(f"Error: {e}")
        return {
            "can_apply": False,
            "reasoning": f"Error: {e}",
            "lead_generation": None,
            "county": "Unknown",
        }

def main():
    """Test function"""
    # Test with the ESBD URL
    test_url = "https://www.txsmartbuy.gov/esbd/754-TXST-2026-RFP-434-UMKT"
    
    result = can_apply_without_registration(test_url, generate_leads=True)
    
    print(f"\n{'='*60}")
    print("ESBD ANALYSIS RESULT")
    print(f"{'='*60}")
    print(f"Can apply without additional registration: {result['can_apply']}")
    print(f"Reasoning: {result['reasoning']}")
    
    if result['can_apply'] and 'lead_generation' in result:
        print(f"\n{'='*60}")
        print("LEAD GENERATION RESULTS")
        print(f"{'='*60}")
        lead_gen = result['lead_generation']
        
        if 'error' in lead_gen:
            print(f"Error: {lead_gen['error']}")
        else:
            print(f"Emails: {lead_gen['emails']}")
            print(f"Subject: {lead_gen['subject']}")
            print(f"Body: {lead_gen['body'][:200]}..." if len(str(lead_gen['body'])) > 200 else f"Body: {lead_gen['body']}")
    
    print(f"{'='*60}")

def find_next_available_row(wks):
    """Scan a worksheet once and return the row number after the last non-empty Solicitation ID."""
    records = wks.get_all_records()
    last_non_empty = 1
    for i, record in enumerate(records, start=2):
        if str(record.get('Solicitation ID', '')).strip():
            last_non_empty = i
    return last_non_empty + 1


def add_row_to_local_contracts(local_contracts_wks, name, solicitation_id, due_date, status, reasoning, subject, body, emails, folder_link=None, target_row=None):
    """
    Add a row to the localContracts worksheet.

    If target_row is provided the write goes directly there (used by the
    parallel CSV processor which tracks rows via a shared counter).
    Otherwise falls back to scanning the sheet for the first empty row.
    """
    if target_row is None:
        print(f"Finding empty row in localContracts for solicitation {solicitation_id}...")
        records = local_contracts_wks.get_all_records()
        empty_row = None
        last_non_empty_row = 1
        for i, record in enumerate(records, start=2):
            existing_id = str(record.get('Solicitation ID', '')).strip()
            if existing_id:
                last_non_empty_row = i
                if existing_id == solicitation_id:
                    print(f"⚠️ Solicitation {solicitation_id} already in this sheet (row {i}), skipping write")
                    return i
            elif not empty_row:
                empty_row = i
        target_row = empty_row if empty_row else last_non_empty_row + 1

    print(f"Writing solicitation {solicitation_id} to row {target_row}")

    local_contracts_wks.update_value(f'A{target_row}', name)
    local_contracts_wks.update_value(f'B{target_row}', solicitation_id)
    local_contracts_wks.update_value(f'C{target_row}', due_date)
    local_contracts_wks.update_value(f'D{target_row}', status)
    local_contracts_wks.update_value(f'E{target_row}', reasoning)
    local_contracts_wks.update_value(f'F{target_row}', subject)
    local_contracts_wks.update_value(f'G{target_row}', body)
    local_contracts_wks.update_value(f'H{target_row}', emails)

    if folder_link:
        local_contracts_wks.update_value(f'J{target_row}', folder_link)
    else:
        local_contracts_wks.update_value(f'J{target_row}', "No files uploaded")

    print(f"✅ Added to localContracts (row {target_row})")
    return target_row

def _upload_bonfire_files_to_drive(solicitation_id, file_paths):
    """Upload Bonfire-downloaded files to Google Drive. Returns folder link or None."""
    try:
        # Filter out zip files, upload only extracted content
        files_to_upload = [f for f in file_paths if not f.lower().endswith(".zip")]
        if not files_to_upload:
            files_to_upload = file_paths

        access_token = get_drive_access_token()
        if not access_token:
            return None
        folder_name = f"BONFIRE_{solicitation_id}"
        folder_id, folder_link = create_drive_folder(access_token, folder_name, ESBD_PARENT_FOLDER_ID)
        if not folder_id:
            return None
        uploaded = 0
        for fp in files_to_upload:
            if upload_file_to_drive(access_token, fp, folder_id):
                uploaded += 1
        print(f"Uploaded {uploaded}/{len(files_to_upload)} Bonfire files to Drive")
        return folder_link
    except Exception as e:
        print(f"Bonfire Drive upload error: {e}")
        return None


def solicitation_exists_in_skipped(skipped_wks, solicitation_id):
    """
    Check if a solicitation ID already exists in the localContracts_skipped worksheet.
    
    Returns:
        tuple: (exists: bool, row_number: int or None)
    """
    try:
        records = skipped_wks.get_all_records()
        for i, record in enumerate(records, start=2):
            existing_id = str(record.get('Solicitation ID', '')).strip()
            if existing_id == solicitation_id:
                return True, i
        return False, None
    except Exception as e:
        print(f"Error checking skipped sheet: {e}")
        return False, None

def add_row_to_skipped_contracts(skipped_wks, name, solicitation_id, due_date, status, reasoning, attachment_url=None, target_row=None):
    """
    Add a row to the localContracts_skipped worksheet.
    If target_row is provided, write directly there (parallel-safe).
    Otherwise scans the sheet for the next empty row.
    """
    if target_row is None:
        already_exists, _ = solicitation_exists_in_skipped(skipped_wks, solicitation_id)
        if already_exists:
            print(f"Solicitation {solicitation_id} already in localContracts_skipped, skipping duplicate.")
            return None

        print(f"Adding skipped solicitation {solicitation_id} to localContracts_skipped...")
        records = skipped_wks.get_all_records()
        empty_row = None
        last_non_empty_row = 1

        for i, record in enumerate(records, start=2):
            existing_id = str(record.get('Solicitation ID', '')).strip()
            if existing_id:
                last_non_empty_row = i
            elif not empty_row:
                empty_row = i

        target_row = empty_row if empty_row else last_non_empty_row + 1

    print(f"Writing skipped solicitation {solicitation_id} to row {target_row}")
    skipped_wks.update_value(f'A{target_row}', name)
    skipped_wks.update_value(f'B{target_row}', solicitation_id)
    skipped_wks.update_value(f'C{target_row}', due_date)
    skipped_wks.update_value(f'D{target_row}', status)
    skipped_wks.update_value(f'E{target_row}', reasoning)
    skipped_wks.update_value(f'F{target_row}', attachment_url or "")

    print(f"✅ Added to localContracts_skipped (row {target_row})")
    return target_row



def solicitation_exists_in_local_contracts(local_contracts_wks, solicitation_id):
    """
    Check if a solicitation ID already exists in the localContracts worksheet.
    
    Args:
        local_contracts_wks: The localContracts worksheet object
        solicitation_id (str): The solicitation ID to check
        
    Returns:
        tuple: (exists: bool, row_number: int or None)
    """
    try:
        records = local_contracts_wks.get_all_records()
        for i, record in enumerate(records, start=2):  # Start at 2 because row 1 is headers
            existing_id = str(record.get('Solicitation ID', '')).strip()
            if existing_id == solicitation_id:
                return True, i
        return False, None
    except Exception as e:
        print(f"Error checking for existing solicitation: {e}")
        return False, None

def processEsbdSolicitations(records, sh, spreadsheet):
    """
    Process multiple ESBD URLs from spreadsheet and add successful results to localContracts tab.
    Columns in localContracts: Name, Solicitation ID, Due Date, status, reasoning, Subject, Email Body, emails
    """
    
    print(f"Processing {len(records)} ESBD solicitation(s)...")
    results = []
    rowNumber = 1
    
    # Get the localContracts worksheet
    try:
        local_contracts_wks = spreadsheet.worksheet_by_title('localContracts')
        print("✅ Found 'localContracts' worksheet")
    except Exception as e:
        print(f"❌ Error: Could not find 'localContracts' worksheet: {e}")
        return results
    
    # Get or create the localContracts_skipped worksheet
    skipped_wks = None
    try:
        skipped_wks = spreadsheet.worksheet_by_title('localContracts_skipped')
        print("✅ Found 'localContracts_skipped' worksheet")
    except Exception as e:
        print(f"⚠️ 'localContracts_skipped' worksheet not found: {e}")
        try:
            skipped_wks = spreadsheet.add_worksheet('localContracts_skipped', rows=1000, cols=10)
            skipped_wks.update_value('A1', 'Name')
            skipped_wks.update_value('B1', 'Solicitation ID')
            skipped_wks.update_value('C1', 'Due Date')
            skipped_wks.update_value('D1', 'Status')
            skipped_wks.update_value('E1', 'Reasoning')
            skipped_wks.update_value('F1', 'Attachment URL')
            print("✅ Created 'localContracts_skipped' worksheet with headers")
        except Exception as e2:
            print(f"❌ Could not create 'localContracts_skipped' worksheet: {e2}")
    
    # Pre-filter: collect eligible records sequentially (sheet reads are fast)
    eligible_items = []
    for record in records:
        rowNumber += 1
        
        solicitation_id = str(record.get('Solicitation ID', '')).strip()
        if not solicitation_id:
            print(f"Row {rowNumber}: No Solicitation ID found, skipping...")
            sh.update_value('K'+str(rowNumber), "No Solicitation ID")
            continue
        
        try:
            current_status = sh.cell(f'K{rowNumber}').value
            if current_status and str(current_status).strip() not in ['', 'Processing...']:
                print(f"Row {rowNumber}: Already processed ({current_status}), skipping...")
                continue
        except:
            pass
        
        exists, existing_row = solicitation_exists_in_local_contracts(local_contracts_wks, solicitation_id)
        if exists:
            print(f"Row {rowNumber}: Solicitation {solicitation_id} already exists in localContracts (row {existing_row}), skipping...")
            sh.update_value('K'+str(rowNumber), f"Already in localContracts (row {existing_row})")
            continue
        
        name = str(record.get('Name', '')).strip()
        due_date = str(record.get('Due Date', '')).strip()
        
        eligible_items.append({
            'rowNumber': rowNumber,
            'solicitation_id': solicitation_id,
            'name': name,
            'due_date': due_date,
        })
    
    total_eligible = len(eligible_items)
    print(f"\n{total_eligible} eligible solicitations to process (2 in parallel)")

    print("Authenticating Gmail client...")
    gmail_client = GmailClient(NEXAN_ACCOUNT_CONFIG)
    if not gmail_client.authenticate():
        print("Failed to authenticate Gmail. Continuing without draft creation.")
        gmail_client = None

    sheet_lock = threading.Lock()
    next_row_local = [find_next_available_row(local_contracts_wks)]
    next_row_skipped = [find_next_available_row(skipped_wks)] if skipped_wks else [1]

    def handle_solicitation(item):
        row = item['rowNumber']
        solicitation_id = item['solicitation_id']
        name = item['name']
        due_date = item['due_date']
        esbd_url = f"https://www.txsmartbuy.gov/esbd/{solicitation_id}"

        print(f"Processing row {row}: {solicitation_id}")

        with sheet_lock:
            sh.update_value('K'+str(row), "Processing...")

        try:
            result = can_apply_without_registration(esbd_url, generate_leads=True)

            if result.get('skip_silently'):
                with sheet_lock:
                    sh.update_value('K'+str(row), "Skipped - Irrelevant")
                print(f"⏭️ Row {row}: Skipped silently — {result.get('reasoning', '')}")
                return

            if result['can_apply']:
                if 'lead_generation' in result and 'error' not in result['lead_generation']:
                    lead_gen = result['lead_generation']
                    if "subject" in lead_gen:
                        lead_gen['subject'] = lead_gen['subject'] + "- texasLocal"
                    reasoning = result.get('reasoning', 'Can apply without additional registration')

                    print(f"📤 Uploading files to Google Drive...")
                    if result.get("bonfire_files"):
                        drive_folder_link = _upload_bonfire_files_to_drive(solicitation_id, result["bonfire_files"])
                    else:
                        drive_folder_link = upload_esbd_files_to_drive(solicitation_id)

                    with sheet_lock:
                        sh.update_value('K'+str(row), "Can Apply - Leads Generated - Added to localContracts")
                        lc_row = next_row_local[0]
                        next_row_local[0] += 1
                        target_row = add_row_to_local_contracts(
                            local_contracts_wks=local_contracts_wks,
                            name=name,
                            solicitation_id=solicitation_id,
                            due_date=due_date,
                            status="Can Apply - Leads Generated",
                            reasoning=reasoning,
                            subject=lead_gen.get('subject', 'Not found'),
                            body=lead_gen.get('body', 'Not found'),
                            emails=lead_gen.get('emails', 'Not found'),
                            folder_link=drive_folder_link,
                            target_row=lc_row
                        )
                    print(f"✅ Row {row}: Added to localContracts (row {target_row})")

                    create_email_draft(
                        lead_gen.get('emails', ''),
                        lead_gen.get('subject', ''),
                        lead_gen.get('body', ''),
                        gmail_client,
                    )

                    results.append({
                        'esbd_url': esbd_url,
                        'can_apply': True,
                        'emails': lead_gen.get('emails', 'Not found'),
                        'subject': lead_gen.get('subject', 'Not found'),
                        'body': lead_gen.get('body', 'Not found')
                    })

                    print(f"✅ Row {row}: Success - leads generated and added to localContracts")

                else:
                    error_msg = result.get('lead_generation', {}).get('error', 'Lead generation failed')
                    reasoning = f"Can apply but lead generation failed: {error_msg}"

                    with sheet_lock:
                        sh.update_value('K'+str(row), "Can Apply - Lead Gen Failed")
                        sh.update_value('L'+str(row), reasoning)
                        if skipped_wks:
                            try:
                                skip_row = next_row_skipped[0]
                                next_row_skipped[0] += 1
                                add_row_to_skipped_contracts(skipped_wks, name, solicitation_id, due_date, "Lead Gen Failed", reasoning, result.get('attachment_url'), target_row=skip_row)
                            except Exception as e:
                                print(f"⚠️ Failed to add to localContracts_skipped: {e}")

                    print(f"⚠️ Row {row}: Can apply but lead generation failed: {error_msg}")

            else:
                reasoning = result.get('reasoning', 'Registration required')
                status = result.get('status', 'Skipped')

                with sheet_lock:
                    sh.update_value('K'+str(row), status)
                    sh.update_value('L'+str(row), reasoning)
                    if skipped_wks:
                        try:
                            skip_row = next_row_skipped[0]
                            next_row_skipped[0] += 1
                            add_row_to_skipped_contracts(skipped_wks, name, solicitation_id, due_date, status, reasoning, result.get('attachment_url'), target_row=skip_row)
                        except Exception as e:
                            print(f"⚠️ Failed to add to localContracts_skipped: {e}")

                print(f"❌ Row {row}: {status} - {reasoning}")

        except Exception as e:
            error_msg = str(e)[:100]

            with sheet_lock:
                sh.update_value('K'+str(row), f"Error: {error_msg}")
                sh.update_value('L'+str(row), "Error during processing")
                if skipped_wks:
                    try:
                        skip_row = next_row_skipped[0]
                        next_row_skipped[0] += 1
                        add_row_to_skipped_contracts(skipped_wks, name, solicitation_id, due_date, "Processing Error", f"Error: {error_msg}", None, target_row=skip_row)
                    except Exception as e2:
                        print(f"⚠️ Failed to add error to localContracts_skipped: {e2}")

            print(f"❌ Row {row}: Error - {error_msg}")

    with ThreadPoolExecutor(max_workers=2) as executor:
        executor.map(handle_solicitation, eligible_items)
    
    # Clean up Bonfire session if one was started
    try:
        from bonfire_downloader import close_bonfire_session
        close_bonfire_session()
    except Exception:
        pass

    print(f"\n{'='*100}")
    print("ALL ESBD SOLICITATIONS PROCESSED")
    print(f"{'='*100}")
    print(f"Total records: {len(records)}")
    print(f"Eligible (not duplicates): {total_eligible}")
    print(f"Successful entries added to localContracts tab: {len(results)}")
    
    return results


# CSV processing functions are now in esbd_csv_exporter.py
# Import them when needed


if __name__ == "__main__":
    from esbd_csv_exporter import auto_process_yesterday_solicitations
    
    print("\n" + "="*80)
    print("ESBD AUTO PROCESSOR - YESTERDAY'S SOLICITATIONS")
    print("="*80)
    
    # Always auto-process yesterday's solicitations
    auto_process_yesterday_solicitations()
    # call_LocalContractFlow()
    # rename_rfq_drafts()
    
