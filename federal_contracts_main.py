#!/usr/bin/env python3
"""
Simple Government Contracts Fetcher
A minimalistic version that fetches contracts from SAM.gov, caches them, and prints contract information.
"""

import requests
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import pygsheets
from generateLeads import process_single_solicitation, upload_sam_files_to_drive
import time
import csv
from runN8nFlows import call_samGovFlow
from get_empty_rows import (
    process_row, get_drive_access_token, GmailClient, NEXAN_ACCOUNT_CONFIG,
)

# Import from centralized config
from config import (
    SAM_GOV_API_KEY,
    SAM_GOV_API_URL,
    CONTRACT_CACHE_DIR,
    ALLOWED_CONTRACT_TYPES,
    ALLOWED_SETASIDE_TYPES,
    BLOCKED_EMAIL_DOMAINS,
    MIN_DAYS_UNTIL_DEADLINE,
    ENABLE_DRIVE_UPLOAD
)

# Set to True to test one contract end-to-end; False for normal full processing
TEST_SINGLE_CONTRACT = False

# SAM.gov API Configuration (use centralized config)
API_KEY = SAM_GOV_API_KEY
BASE_URL = SAM_GOV_API_URL
CACHE_DIR = CONTRACT_CACHE_DIR

# URLs to explicitly skip during processing
BLOCKED_URLS = {
    "https://sam.gov/workspace/contract/opp/7ea247ac9ba2400696adf783eccd0853/view",
}

def fetch_contracts(posted_from, posted_to, use_cache=True):
    """
    Fetch government contracts from SAM.gov within the specified date range.
    
    Parameters:
    - posted_from (str): Start date in 'MM/dd/yyyy' format
    - posted_to (str): End date in 'MM/dd/yyyy' format
    - use_cache (bool): Whether to use cached responses if available
    
    Returns:
    - List of contract opportunities
    """
    # Create cache directory if it doesn't exist
    os.makedirs(CACHE_DIR, exist_ok=True)
    
    # Create cache filename based on date range
    from_date_str = posted_from.replace('/', '-')
    to_date_str = posted_to.replace('/', '-')
    cache_filename = f"contracts_{from_date_str}_to_{to_date_str}.json"
    cache_file = os.path.join(CACHE_DIR, cache_filename)
    
    print(f"Using cache file: {cache_file}")
    
    # Check if cache exists and use it
    if use_cache and os.path.exists(cache_file):
        print("Using cached data...")
        with open(cache_file, 'r') as f:
            return json.load(f)
    
    # If no cache, make API request
    print("Fetching fresh data from SAM.gov API...")
    offset = 0
    limit = 1000
    all_contracts = []
    
    while True:
        params = {
            'api_key': API_KEY,
            'postedFrom': posted_from,
            'postedTo': posted_to,
            'limit': limit,
            'offset': offset
        }
        
        response = requests.get(BASE_URL, params=params)
        print(response.text)
        
        if response.status_code == 200:
            data = response.json()
            total_records = data.get('totalRecords', 0)
            print(f"Total records available: {total_records}")
            
            opportunities = data.get('opportunitiesData', [])
            all_contracts.extend(opportunities)
            offset += len(opportunities)
            
            print(f"Fetched {len(all_contracts)} contracts so far...")
            
            # Break if we've fetched all records or no more data
            if offset >= total_records or len(opportunities) < limit:
                break
        else:
            print(f"API Error: {response.status_code} - {response.text}")
            break
    
    # Save to cache
    if use_cache and all_contracts:
        with open(cache_file, 'w') as f:
            json.dump(all_contracts, f, indent=2)
        print(f"Saved {len(all_contracts)} contracts to cache: {cache_file}")
    
    return all_contracts

def setup_google_sheet():
    """Setup Google Sheets connection."""
    try:
        gc = pygsheets.authorize(service_file='key.json')
        sh = gc.open('Quote Request')
        wks = sh.worksheet_by_title('SAM.GOV')
        return wks
    except Exception as e:
        print(f"Error setting up Google Sheets: {e}")
        return None

def load_sheet_state(wks):
    """
    Read the entire sheet once and return a lookup of existing URLs
    plus the next available row number.

    Returns (url_to_row, next_empty_row) where:
      url_to_row  – dict mapping Sam Link → (row_num, record_dict)
      next_empty_row – first empty row (or row after the last non-empty one)
    """
    try:
        records = wks.get_all_records()
    except Exception as e:
        print(f"Error reading sheet state: {e}")
        return {}, 2

    url_to_row = {}
    first_empty = None
    last_non_empty = 1

    for i, record in enumerate(records, start=2):
        link = (record.get('Sam Link') or '').strip()
        if link:
            url_to_row[link] = (i, record)
            last_non_empty = i
        elif first_empty is None:
            first_empty = i

    next_empty = first_empty if first_empty else last_non_empty + 1
    return url_to_row, next_empty


def find_link_in_sheet(wks, url):
    """Find if a URL already exists in the sheet and return row number."""
    try:
        records = wks.get_all_records()
        for i, record in enumerate(records, start=2):
            if record.get('Sam Link') == url:
                return i, record
        return None, None
    except Exception as e:
        print(f"Error searching sheet: {e}")
        return None, None

def add_or_update_sheet(wks, url, result_data, contract_data=None, row_num=None):
    """
    Write the lead-discovery columns to the sheet: B (Sam Link), C (Drive link),
    I (emails), K (status).  Subject/body generation and drafting are handled
    later by get_empty_rows.process_row().

    Returns (success_bool, target_row_number).
    """
    try:
        drive_folder_link = result_data.get("folder_link")
        if ENABLE_DRIVE_UPLOAD and not drive_folder_link:
            print(f"Uploading SAM.gov files to Google Drive...")
            drive_folder_link = upload_sam_files_to_drive(url)

        if row_num:
            target_row = row_num
            print(f"Updating existing row {row_num} for URL: {url}")
        else:
            print(f"Finding empty row for URL: {url}")
            records = wks.get_all_records()
            empty_row = None
            last_non_empty_row = 1
            for i, record in enumerate(records, start=2):
                if record.get('Sam Link') and record.get('Sam Link').strip() != '':
                    last_non_empty_row = i
                elif not empty_row and (not record.get('Sam Link') or record.get('Sam Link').strip() == ''):
                    empty_row = i
            target_row = empty_row if empty_row else last_non_empty_row + 1
            print(f"Writing to row {target_row} for URL: {url}")
            wks.update_value(f'B{target_row}', url)

        wks.update_value(f'I{target_row}', result_data['emails'])
        wks.update_value(f'K{target_row}', 'generated')

        if ENABLE_DRIVE_UPLOAD:
            if drive_folder_link:
                wks.update_value(f'C{target_row}', drive_folder_link)
            else:
                wks.update_value(f'C{target_row}', "No files uploaded")

        time.sleep(1)
        return True, target_row
    except Exception as e:
        print(f"Error updating sheet: {e}")
        return False, None

def _find_next_empty_row(wks):
    """Scan the sheet and return the first empty row (by Sam Link column).
    Must be called under sheet_lock so two workers never get the same row."""
    records = wks.get_all_records()
    first_empty = None
    last_non_empty = 1
    for i, record in enumerate(records, start=2):
        link = (record.get('Sam Link') or '').strip()
        if link:
            last_non_empty = i
        elif first_empty is None:
            first_empty = i
    return first_empty if first_empty else last_non_empty + 1


def process_contracts_to_sheet(contracts):
    """
    Process filtered contracts:
    1. Extract emails via OpenAI and write B, C, I, K to the sheet.
    2. For each row written, call get_empty_rows.process_row() to generate
       subject/body (Gemini), verify emails (Bouncer), and create a Gmail draft.
    """
    print("\n=== Setting up Google Sheets connection ===")
    wks = setup_google_sheet()
    if not wks:
        print("Failed to setup Google Sheets. Skipping sheet operations.")
        return

    # ------------------------------------------------------------------
    # Bulk-check: read the sheet ONCE to filter out already-processed URLs
    # ------------------------------------------------------------------
    print("Loading existing sheet data for duplicate check...")
    url_to_row, _ = load_sheet_state(wks)

    already_skipped = 0
    new_contracts = []
    preloaded_rows = {}

    for contract in contracts:
        url = (contract.get('uiLink') or '').strip()
        if not url:
            continue
        if url in url_to_row:
            row_num, record = url_to_row[url]
            status = str(record.get('getEmails', '')).strip().lower()
            if status and status != 'processing...':
                already_skipped += 1
                continue
            preloaded_rows[url] = row_num
        new_contracts.append(contract)

    print(f"Already processed in sheet: {already_skipped} (skipped)")
    print(f"Remaining to process: {len(new_contracts)}")

    if not new_contracts:
        print("All contracts already processed. Nothing to do.")
        return

    print("Authenticating Gmail client...")
    gmail_client = GmailClient(NEXAN_ACCOUNT_CONFIG)
    if not gmail_client.authenticate():
        print("Failed to authenticate Gmail. Continuing without draft creation.")
        gmail_client = None

    workers = 1 if TEST_SINGLE_CONTRACT else 3
    if TEST_SINGLE_CONTRACT:
        print(f"\n=== TEST MODE: Processing {len(new_contracts)} contracts sequentially until first full success ===")
    else:
        print(f"\n=== Processing {len(new_contracts)} contracts ({workers} in parallel) ===")

    sheet_lock = threading.Lock()
    test_done = threading.Event() if TEST_SINGLE_CONTRACT else None

    def _write_to_row(url, row_num=None, status_msg=None, result_data=None, contract_data=None):
        """
        Write to the sheet under sheet_lock.  If row_num is None, scans
        for the next empty row fresh each time so we never collide with
        rows added by other scripts or parallel workers.
        """
        if row_num is None:
            row_num = _find_next_empty_row(wks)
            wks.update_value(f'B{row_num}', url)

        if status_msg:
            wks.update_value(f'K{row_num}', status_msg)
        elif result_data:
            drive_folder_link = result_data.get("folder_link")
            if ENABLE_DRIVE_UPLOAD and not drive_folder_link:
                print(f"Uploading SAM.gov files to Google Drive...")
                drive_folder_link = upload_sam_files_to_drive(url)

            wks.update_value(f'I{row_num}', result_data['emails'])
            wks.update_value(f'K{row_num}', 'generated')
            if ENABLE_DRIVE_UPLOAD:
                wks.update_value(f'C{row_num}', drive_folder_link or "No files uploaded")

        time.sleep(1)
        return row_num

    def handle_contract(contract):
        if test_done and test_done.is_set():
            return

        url = contract.get('uiLink')
        if not url:
            return

        print(f"\n--- Processing Contract: {url} ---")

        row_num = preloaded_rows.get(url)

        resource_links = contract.get('resourceLinks')
        notice_id = contract.get('noticeId')

        if resource_links:
            print(f"📥 {len(resource_links)} resource links available for API download")
        else:
            print("⚠️ No resource links - will use Selenium fallback if needed")

        result_data = process_single_solicitation(url, resource_links=resource_links, notice_id=notice_id)

        emails = result_data.get('emails', '') if result_data else ''
        if not result_data or emails == 'skipped':
            print(f"Processing failed or was skipped for {url}")
            with sheet_lock:
                _write_to_row(url, row_num, status_msg="skipped")
            return
        if emails == 'Not found':
            print(f"Skipping sheet: no vendor/emails found for {url}")
            with sheet_lock:
                _write_to_row(url, row_num, status_msg="no emails found")
            return
        if isinstance(emails, str) and emails.strip().startswith('ERROR:'):
            err_preview = emails.strip()[:80] + ('...' if len(emails) > 80 else '')
            print(f"Skipping sheet: error for {url} - {err_preview}")
            with sheet_lock:
                _write_to_row(url, row_num, status_msg=f"error: {err_preview}")
            return

        with sheet_lock:
            try:
                target_row = _write_to_row(url, row_num, result_data=result_data, contract_data=contract)
                print(f"Wrote lead data to row {target_row} for {url}")
            except Exception as e:
                print(f"Failed to write lead data for {url}: {e}")
                return

        with sheet_lock:
            drive_link = wks.cell(f'C{target_row}').value or ""

        if gmail_client:
            fresh_token = get_drive_access_token()
            row_success = process_row(
                target_row, url, drive_link, emails,
                wks, fresh_token, gmail_client, sheet_lock,
            )
            if row_success and test_done:
                print(f"\n=== TEST MODE: First fully successful contract done. Stopping. ===")
                test_done.set()

    with ThreadPoolExecutor(max_workers=workers) as executor:
        executor.map(handle_contract, new_contracts)

    print("\n=== Finished processing contracts to Google Sheets ===")

def should_skip_contract(contract):
    """Check if a contract should be skipped based on filtering criteria."""
    # Check against explicitly blocked URLs
    ui_link = contract.get('uiLink', '')
    if ui_link in BLOCKED_URLS:
        return True, f"URL is in blocked list: {ui_link}"

    # Check contract type - use centralized config
    contract_type = contract.get('type', '')
    if contract_type not in ALLOWED_CONTRACT_TYPES:
        return True, f"Contract type '{contract_type}' not in allowed types"
    
    # Check for blocked email domains in primary contact
    poc_list = contract.get('pointOfContact', [])
    primary_email = None
    
    if poc_list:
        # Try finding primary contact email
        for contact in poc_list:
            if contact.get('type') == 'primary' and contact.get('email'):
                primary_email = contact['email']
                break
        # If no primary found, take the first email available
        if not primary_email:
            for contact in poc_list:
                if contact.get('email'):
                    primary_email = contact['email']
                    break
    
    # Skip if primary contact contains any blocked domain - use centralized config
    if primary_email:
        for blocked_domain in BLOCKED_EMAIL_DOMAINS:
            if blocked_domain in primary_email:
                return True, f"Primary contact contains blocked domain: {blocked_domain}"
    
    # Check set aside type - use centralized config
    setaside_type = contract.get('typeOfSetAsideDescription')
    
    if setaside_type is None:
        return True, f"Set aside type is None"
    
    if setaside_type not in ALLOWED_SETASIDE_TYPES:
        return True, f"Set aside type '{setaside_type}' not in allowed types"
    
    # Check response deadline - use centralized config for MIN_DAYS_UNTIL_DEADLINE
    response_deadline = contract.get('responseDeadLine')
    if response_deadline:
        try:
            # Handle ISO format with timezone
            if 'T' in response_deadline:
                # Remove timezone info for parsing (keep just the date and time part)
                date_part = response_deadline.split('T')[0]  # Get "2025-09-22"
                deadline_date = datetime.strptime(date_part, "%Y-%m-%d")
                
                # Calculate minimum days from now (compare just dates, not times)
                min_deadline_date = datetime.now() + timedelta(days=MIN_DAYS_UNTIL_DEADLINE - 1)
                
                # Skip if deadline is less than minimum days away
                if deadline_date < min_deadline_date:
                    days_until_deadline = (deadline_date - datetime.now()).days
                    return True, f"Response deadline is in {days_until_deadline} days (less than {MIN_DAYS_UNTIL_DEADLINE})"
            else:
                # If it's not ISO format, skip it to be safe
                return True, f"Could not parse response deadline format: {response_deadline}"
        except Exception as e:
            # If there's any error parsing the date, skip it
            return True, f"Error parsing deadline date: {str(e)}"
    else:
        # If no deadline is provided, skip it
        return True, f"No response deadline provided"
    
    return False, None

def print_contract_info(contract, index):
    """Print basic information about a contract."""
    # print(contract)
    notice_id = contract.get('noticeId', 'N/A')
    title = contract.get('title', 'No Title')
    posted_date = contract.get('postedDate', 'N/A')
    response_deadline = contract.get('responseDeadLine', 'N/A')
    naics_code = contract.get('naicsCode', 'N/A')
    contract_type = contract.get('type', 'N/A')
    ui_link = contract.get('uiLink', 'N/A')
    setaside_type = contract.get('typeOfSetAsideDescription', 'N/A')
    # if(ui_link=="https://sam.gov/workspace/contract/opp/e696837489ba41b5a246bb132494f077/view"):
    #     print(contract)
    print(ui_link,response_deadline)
    # print(f"\n--- Contract #{index + 1} ---")
    # print(f"Notice ID: {notice_id}")
    # print(f"Title: {title}")
    # print(f"Type: {contract_type}")
    # print(f"NAICS Code: {naics_code}")
    # print(f"Posted Date: {posted_date}")
    # print(f"Response Deadline: {response_deadline}")
    # print(f"Link: {ui_link}")
    # print(f"Set Aside Type: {setaside_type}")
    # Print point of contact if available
    poc_list = contract.get('pointOfContact', [])
    if poc_list:
        for contact in poc_list:
            if contact.get('type') == 'primary' and contact.get('email'):
                # print(f"Primary Contact: {contact.get('email')}")
                break

def write_to_csv(filtered_contracts, filename=None):
    """
    Write filtered contracts to a CSV file.
    
    Parameters:
    - filtered_contracts (list): List of contract dictionaries to write to CSV
    - filename (str, optional): Name of the CSV file. If not provided, uses timestamp-based name
    
    Returns:
    - str: Path to the created CSV file
    """
    if not filtered_contracts:
        print("No contracts to write to CSV.")
        return None
    
    # Generate default filename if not provided
    if filename is None:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"contracts_{timestamp}.csv"
    
    # Ensure the filename ends with .csv
    if not filename.endswith('.csv'):
        filename = f"{filename}.csv"
    
    # Define CSV headers
    headers = [
        'Notice ID',
        'Title',
        'Type',
        'NAICS Code',
        'Posted Date',
        'Response Deadline',
        'Set Aside Type',
        'Primary Contact Email',
        'Primary Contact Name',
        'Primary Contact Phone',
        'UI Link'
    ]
    
    try:
        with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=headers)
            writer.writeheader()
            
            for contract in filtered_contracts:
                # Extract primary contact information
                poc_list = contract.get('pointOfContact', [])
                primary_email = ''
                primary_name = ''
                primary_phone = ''
                
                if poc_list:
                    # Try finding primary contact
                    primary_contact = None
                    for contact in poc_list:
                        if contact.get('type') == 'primary':
                            primary_contact = contact
                            break
                    
                    # If no primary found, take the first contact
                    if not primary_contact and poc_list:
                        primary_contact = poc_list[0]
                    
                    if primary_contact:
                        primary_email = primary_contact.get('email', '')
                        primary_name = f"{primary_contact.get('firstName', '')} {primary_contact.get('lastName', '')}".strip()
                        primary_phone = primary_contact.get('phone', '')
                
                # Write row to CSV
                writer.writerow({
                    'Notice ID': contract.get('noticeId', ''),
                    'Title': contract.get('title', ''),
                    'Type': contract.get('type', ''),
                    'NAICS Code': contract.get('naicsCode', ''),
                    'Posted Date': contract.get('postedDate', ''),
                    'Response Deadline': contract.get('responseDeadLine', ''),
                    'Set Aside Type': contract.get('typeOfSetAsideDescription', ''),
                    'Primary Contact Email': primary_email,
                    'Primary Contact Name': primary_name,
                    'Primary Contact Phone': primary_phone,
                    'UI Link': contract.get('uiLink', '')
                })
        
        print(f"\nSuccessfully wrote {len(filtered_contracts)} contracts to {filename}")
        return filename
        
    except Exception as e:
        print(f"Error writing to CSV: {e}")
        return None

def main():
    """Main function to fetch and display contracts."""
    print("=== Simple Government Contracts Fetcher ===\n")
    
    # Set date range (last 7 days)
    end_date = datetime.now()
    start_date = end_date - timedelta(days=1)
    posted_from = start_date.strftime('%m/%d/%Y')
    posted_to = end_date.strftime('%m/%d/%Y')
    print(posted_from, posted_to)
    # posted_to = "01/23/2026"
    # posted_from = "01/22/2026"
    print(f"Fetching contracts from {posted_from} to {posted_to}")
    
    # Fetch contracts
    contracts = fetch_contracts(posted_from, posted_to, use_cache=True)
    
    if not contracts:
        print("No contracts found or error fetching contracts.")
        return
    
    print(f"\n=== Found {len(contracts)} Contracts ===")
    
    # Filter contracts and keep track of skipped ones
    filtered_contracts = []
    skipped_counts = {
        'wrong_type': 0,
        'DibbsBSM@dla.mil': 0,
        'None_setaside': 0,
        'wrong_setaside': 0,
        'deadline_too_soon': 0
    }
    
    # Collect all unique Set Aside Types and Contract Types
    setaside_types = set()
    contract_types = set()
    
    for contract in contracts:
        # Collect Set Aside Type and Contract Type for analysis
        setaside_type = contract.get('typeOfSetAsideDescription')
        contract_type = contract.get('type')
        setaside_types.add(setaside_type)
        contract_types.add(contract_type)
        
        should_skip, reason = should_skip_contract(contract)
        if should_skip:
            if 'Contract type' in reason and 'not in allowed types' in reason:
                skipped_counts['wrong_type'] += 1
            elif 'DibbsBSM@dla.mil' in reason:
                skipped_counts['DibbsBSM@dla.mil'] += 1
            elif 'Set aside type is None' in reason:
                skipped_counts['None_setaside'] += 1
            elif 'Set aside type' in reason and 'not in allowed types' in reason:
                skipped_counts['wrong_setaside'] += 1
            elif 'deadline' in reason.lower() or 'response' in reason.lower():
                skipped_counts['deadline_too_soon'] += 1
        else:
            filtered_contracts.append(contract)
    
    print(f"Skipped {skipped_counts['wrong_type']} contracts with wrong type (not Combined Synopsis/Solicitation or Solicitation)")
    print(f"Skipped {skipped_counts['DibbsBSM@dla.mil']} contracts with DibbsBSM@dla.mil contact")
    print(f"Skipped {skipped_counts['None_setaside']} contracts with None set aside type")
    print(f"Skipped {skipped_counts['wrong_setaside']} contracts with wrong set aside type")
    print(f"Skipped {skipped_counts['deadline_too_soon']} contracts with deadline less than 5 days away")
    print(f"Displaying {len(filtered_contracts)} filtered contracts")
    
    # Loop through and print filtered contracts
    for i, contract in enumerate(filtered_contracts):
        print_contract_info(contract, i)
        
        # Add a separator between contracts (except for the last one)
        if i < len(filtered_contracts) - 1:
            print("-" * 50)
    
    print(f"\n=== Processing Complete ===")
    print(f"Total contracts found: {len(contracts)}")
    print(f"Total contracts displayed: {len(filtered_contracts)}")
    print(f"Total contracts skipped: {len(contracts) - len(filtered_contracts)}")
    
    # Write filtered contracts to CSV
    if filtered_contracts:
        write_to_csv(filtered_contracts)
    
    # Process filtered contracts to Google Sheets
    if filtered_contracts:
        print(f"\n=== Starting Google Sheets Processing ===")
        process_contracts_to_sheet(filtered_contracts)
        if not TEST_SINGLE_CONTRACT:
            call_samGovFlow()
        else:
            print("TEST MODE: Skipping n8n flow trigger.")
    else:
        print("\nNo filtered contracts to process for Google Sheets.")
    
    # Print all unique Set Aside Types found
    print(f"\n=== All Set Aside Types Found ===")
    sorted_setaside_types = sorted([str(t) for t in setaside_types])
    for i, setaside_type in enumerate(sorted_setaside_types, 1):
        print(f"{i}. {setaside_type}")
    print(f"\nTotal unique Set Aside Types: {len(setaside_types)}")
    
    # Print all unique Contract Types found
    print(f"\n=== All Contract Types Found ===")
    sorted_contract_types = sorted([str(t) for t in contract_types])
    for i, contract_type in enumerate(sorted_contract_types, 1):
        print(f"{i}. {contract_type}")
    print(f"\nTotal unique Contract Types: {len(contract_types)}")


def testFetchContracts():
    posted_from = "01/15/2026"
    posted_to = "01/16/2026"
    contracts = fetch_contracts(posted_from, posted_to, use_cache=True)
    print(f"Found {len(contracts)} contracts")
    for contract in contracts:
        print_contract_info(contract, 0)
        print("-" * 50)

if __name__ == "__main__":
    main()
    # testFetchContracts()
