import requests
import json
import os
from datetime import datetime, timedelta
import hashlib
from bs4 import BeautifulSoup
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
import re # Import the regular expression module
import csv # Import the csv module

# Import Google Drive utility functions
try:
    import google_drive_utils
    gdrive_available = True
except ImportError:
    print("Warning: google_drive_utils.py not found or missing dependencies.")
    gdrive_available = False

# Import OpenAI integration
try:
    import gemini as gemini_module  # Import with alias to avoid conflict
    openai_available = True
except ImportError:
    print("Warning: gemini.py not found or missing dependencies.")
    openai_available = False

# Import contract AI utils
try:
    import contract_ai_utils
    contract_ai_utils_available = False
except ImportError:
    print("Warning: contract_ai_utils.py not found or missing dependencies.")
    contract_ai_utils_available = False

# Replace 'YOUR_API_KEY' with your actual SAM.gov API key
# API_KEY="xNHPaUmU2qJN0a2JsZJZ38pJ9TkNKYozOVKxYuGH"
API_KEY = 'SAM-16d1e74e-3847-428c-b351-912e3fb1116c'
API_KEY = 'NG46DPgpTfSH7tXEgedX3t1LaYhVyVPnVMhY9seY'
BASE_URL = 'https://api.sam.gov/opportunities/v2/search'
CACHE_DIR = 'cache'  # Directory to store cached responses

# --- Constants ---
PROCESSED_IDS_FILE = 'processed_notice_ids.txt'
CONTRACTS_DIR = 'contracts'  # Directory to store output files
# Define output filename based on date range early
# Note: This means if you run the script multiple times for the *same* date range,
# it will append to the *same* file.
TODAY_STR = datetime.now().strftime('%Y%m%d') # Use a fixed date for the run if needed
# Or use the actual date range:
# START_DATE_STR = start_date.strftime('%m-%d-%Y') # Need start_date defined first
# END_DATE_STR = end_date.strftime('%m-%d-%Y') # Need end_date defined first
# OUTPUT_JSON_FILENAME = f"collected_contracts_{START_DATE_STR}_to_{END_DATE_STR}.json"
# Using a simpler name for now, define dates later if needed
OUTPUT_JSON_FILENAME_TEMPLATE = "collected_contracts_{start}_to_{end}.json"
OUTPUT_CSV_FILENAME_TEMPLATE = "collected_contracts_{start}_to_{end}.csv" # CSV Filename Template

def load_processed_ids():
    """Loads processed notice IDs from the tracking file."""
    processed_ids = set()
    if os.path.exists(PROCESSED_IDS_FILE):
        try:
            with open(PROCESSED_IDS_FILE, 'r') as f:
                for line in f:
                    processed_ids.add(line.strip())
        except IOError as e:
            print(f"Warning: Could not read processed IDs file '{PROCESSED_IDS_FILE}': {e}")
    return processed_ids
    # return set()

def save_processed_id(notice_id):
    """Appends a successfully processed notice ID to the tracking file."""
    try:
        with open(PROCESSED_IDS_FILE, 'a') as f:
            f.write(notice_id + '\n')
    except IOError as e:
        print(f"Warning: Could not write to processed IDs file '{PROCESSED_IDS_FILE}': {e}")

def load_existing_json_data(filename):
    """Loads data from the JSON output file if it exists."""
    if os.path.exists(filename):
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                # Handle empty file case
                content = f.read()
                if not content:
                    return []
                return json.loads(content)
        except json.JSONDecodeError:
            print(f"Warning: Could not decode JSON from '{filename}'. Starting with empty list.")
            # Optionally backup the corrupted file here
            return []
        except IOError as e:
            print(f"Warning: Could not read existing data file '{filename}': {e}")
            return []
    return [] # Return empty list if file doesn't exist

def save_data_to_json(data_list, filename):
    """Saves the entire list of contract data to the JSON file."""
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data_list, f, indent=2, ensure_ascii=False)
        # print(f"Data successfully saved to {filename}") # Make less verbose for loop saving
    except IOError as e:
        print(f"\nError saving data to file '{filename}': {e}")
    except TypeError as e:
        print(f"\nError: Data is not JSON serializable: {e}")

def save_data_to_csv(data_list, filename):
    """Saves the list of contract data dictionaries to a CSV file."""
    if not data_list: # Don't create an empty file with just headers
        print("No data to save to CSV.")
        return

    # Define the order of columns in the CSV
    # Use keys from the first dictionary, assuming all have the same structure
    fieldnames = list(data_list[0].keys())

    try:
        with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

            writer.writeheader() # Write the header row
            writer.writerows(data_list) # Write all data rows
        print(f"Data successfully saved to {filename}")
    except IOError as e:
        print(f"\nError saving data to CSV file '{filename}': {e}")
    except Exception as e:
        print(f"\nAn unexpected error occurred saving to CSV '{filename}': {e}")

def append_contract_to_csv(contract_data, filename):
    """Appends a single contract to the CSV file, creating the file with headers if needed."""
    # Only save feasible contracts to CSV
    if contract_data.get('isFeasible') is not True:
        # Skip non-feasible contracts for CSV
        return
        
    try:
        # Check if file exists to determine if we need to write headers
        file_exists = os.path.isfile(filename)
        
        # Define fieldnames from the contract_data dictionary
        fieldnames = list(contract_data.keys())
        
        with open(filename, 'a', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            
            # Write header only if file is being created
            if not file_exists:
                writer.writeheader()
            
            # Write the single contract row
            writer.writerow(contract_data)
        
        # print(f"Contract {contract_data.get('noticeId')} appended to CSV: {filename}")
    except IOError as e:
        print(f"\nError appending to CSV file '{filename}': {e}")
    except Exception as e:
        print(f"\nAn unexpected error occurred appending to CSV '{filename}': {e}")

def fetch_contracts(posted_from, posted_to, limit=1000, use_cache=True, cache_expiry_hours=24):
    """
    Fetch government contracts from SAM.gov within the specified date range.
    Uses human-readable filenames for caching based on the date range.

    Parameters:
    - posted_from (str): Start date in 'MM/dd/yyyy' format.
    - posted_to (str): End date in 'MM/dd/yyyy' format.
    - limit (int): Number of records to fetch per request (max 1000).
    - use_cache (bool): Whether to use cached responses if available.
    - cache_expiry_hours (int): Number of hours after which cache expires.

    Returns:
    - List of contract opportunities.
    """
    # Create cache directory if it doesn't exist
    os.makedirs(CACHE_DIR, exist_ok=True)

    # Create a human-readable cache filename based on the date range
    # Replace '/' with '-' for filename compatibility
    from_date_str = posted_from.replace('/', '-')
    to_date_str = posted_to.replace('/', '-')
    # Consider adding limit to filename if it varies significantly, otherwise omit for simplicity
    # cache_filename = f"contracts_{from_date_str}_to_{to_date_str}_limit{limit}.json"
    cache_filename = f"contracts_{from_date_str}_to_{to_date_str}.json"
    cache_file = os.path.join(CACHE_DIR, cache_filename)
    print(f"Using cache file: {cache_file}") # Added for clarity

    # Check if cache exists and is valid
    if use_cache and os.path.exists(cache_file):
        file_modified_time = datetime.fromtimestamp(os.path.getmtime(cache_file))
        if datetime.now() - file_modified_time < timedelta(hours=cache_expiry_hours):
            print(f"Using cached data from {file_modified_time}")
            with open(cache_file, 'r') as f:
                return json.load(f)
    
    # If no valid cache exists, make the API request
    offset = 0
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
        if response.status_code == 200:
            data = response.json()
            total_records = data.get('totalRecords', 0)
            print(f"Total records: {total_records}")
            opportunities = data.get('opportunitiesData', [])
            all_contracts.extend(opportunities)
            offset += len(opportunities)
            if offset >= total_records or len(opportunities) < limit:
                break
        else:
            print(f"Error: {response.status_code} - {response.text}")
            break

    # Save response to cache
    if use_cache:
        with open(cache_file, 'w') as f:
            json.dump(all_contracts, f)
        print(f"Saved data to cache: {cache_file}")

    return all_contracts

def fetch_ui_link_data(ui_link, use_cache=False, cache_expiry_hours=24):
    """
    Fetch text content from a contract's UI link using Selenium.
    Uses the opportunity ID from the link as the cache filename.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)

    # Extract Opportunity ID from the link for the cache filename
    opportunity_id = None
    match = re.search(r'/opp/([^/]+)/view', ui_link)
    if match:
        opportunity_id = match.group(1)
    else:
        print(f"Warning: Could not extract Opportunity ID from link: {ui_link}. Using hash for cache.")
        opportunity_id = hashlib.md5(ui_link.encode()).hexdigest()

    if not opportunity_id:
         print("Error: Could not generate a cache key.")
         return None

    cache_file = os.path.join(CACHE_DIR, f"{opportunity_id}.json")
    print(f"Using cache file: {cache_file}")

    # Check cache
    if use_cache and os.path.exists(cache_file):
        file_modified_time = datetime.fromtimestamp(os.path.getmtime(cache_file))
        if datetime.now() - file_modified_time < timedelta(hours=cache_expiry_hours):
            print(f"Using cached text content from {file_modified_time}")
            with open(cache_file, 'r', encoding='utf-8') as f:
                return json.load(f)

    print(f"Fetching text content from UI link with Selenium: {ui_link}")
    driver = None
    try:
        # Configure Chrome options for better compatibility
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("--disable-plugins")
        chrome_options.add_argument("--disable-images")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--user-agent=Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Mobile Safari/537.36")

        # Initialize driver
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
        driver.set_page_load_timeout(60)  # Increase page load timeout

        # Load page
        print("Loading page...")
        driver.get(ui_link)

        # Wait for Angular app to load - use multiple strategies
        try:
            print("Waiting for page content to load...")
            
            # Strategy 1: Wait for Angular app root
            try:
                WebDriverWait(driver, 20).until(
                    EC.presence_of_element_located((By.TAG_NAME, "app-root"))
                )
                print("Angular app root detected.")
            except TimeoutException:
                print("Angular app root not found, trying alternative selectors...")
            
            # Strategy 2: Wait for main content containers
            content_selectors = [
                "app-opps-display",
                ".sds-card",
                ".grid-container",
                ".contract-title",
                ".field-value-text",
                "[class*='contract']",
                "[class*='opportunity']"
            ]
            
            content_found = False
            for selector in content_selectors:
                try:
                    WebDriverWait(driver, 5).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                    )
                    print(f"Content detected with selector: {selector}")
                    content_found = True
                    break
                except TimeoutException:
                    continue
            
            if not content_found:
                print("No specific content selectors found, proceeding with basic page load...")
            
            # Strategy 3: Wait for page to be in ready state and give Angular time to render
            WebDriverWait(driver, 10).until(
                lambda driver: driver.execute_script("return document.readyState") == "complete"
            )
            print("Page ready state complete.")
            
            # Give Angular additional time to render dynamic content
            print("Waiting for Angular to render content...")
            time.sleep(8)  # Increased wait time for Angular to fully load
            
            # Additional check: Wait for text content to appear
            try:
                WebDriverWait(driver, 10).until(
                    lambda driver: len(driver.page_source) > 1000  # Ensure substantial content loaded
                )
                print("Substantial page content detected.")
            except TimeoutException:
                print("Warning: Page content seems minimal, proceeding anyway...")

            # Parse with BeautifulSoup
            soup = BeautifulSoup(driver.page_source, 'html.parser')

            # Extract all text content
            page_text = soup.get_text(separator='\n', strip=True)

            # Extract title
            page_title = soup.title.string if soup.title else "No Title Found"

            # If content seems too short, it might not have loaded properly
            if len(page_text) < 500:
                print(f"Warning: Extracted text is quite short ({len(page_text)} chars). Content may not have loaded fully.")
                # Save debug HTML
                html_path = f'response_short_content_{opportunity_id}.html'
                with open(html_path, 'w', encoding='utf-8') as f:
                    f.write(driver.page_source)
                print(f"Saved HTML for short content debugging to: {html_path}")

            data = {
                'title': page_title,
                'text_content': page_text
            }

            # Save to cache
            if use_cache:
                with open(cache_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                print(f"Saved text content to cache: {cache_file}")

            print(f"Successfully extracted {len(page_text)} characters of text content.")
            return data

        except TimeoutException:
            print(f"Error: Timed out waiting for page elements on {ui_link}")
            if driver:
                 html_path = f'response_timeout_{opportunity_id}.html'
                 with open(html_path, 'w', encoding='utf-8') as f:
                    f.write(driver.page_source)
                 print(f"Saved HTML on timeout for debugging to: {html_path}")
            return None
        except Exception as parse_err:
             print(f"Error during text extraction: {parse_err}")
             if driver:
                 html_path = f'response_parse_error_{opportunity_id}.html'
                 with open(html_path, 'w', encoding='utf-8') as f:
                    f.write(driver.page_source)
                 print(f"Saved HTML on parse error for debugging to: {html_path}")
             return None

    except Exception as e:
        print(f"Exception when fetching UI link with Selenium: {str(e)}")
        return None
    finally:
        if driver:
            driver.quit()
            print("Browser closed.")

# Example usage (modify main function or call directly):
# ui_link_to_test = "YOUR_SAM_GOV_URL_HERE"
# text_data = fetch_ui_link_data(ui_link_to_test, use_cache=False)
# if text_data:
#     print("\n--- Extracted Text Content ---")
#     print(f"Title: {text_data['title']}")
#     print("\nText:")
#     print(text_data['text_content'][:1000] + "...") # Print first 1000 chars
# else:
#     print("Failed to extract text content.")
# fetch_ui_link_data("https://sam.gov/opp/5f94b816e0ea4e16b26c836cb8b7b409/view")

def process_parts_procurement(contract_data, text_content, ui_link):
    """Process a contract to identify parts procurement needs and supplier information."""
    if not openai_available:
        print("  Gemini integration not available for parts procurement analysis.")
        return None
        
    notice_id = contract_data.get("noticeId", "unknown")
    print(f"  Analyzing parts procurement for contract {notice_id}...")
    
    procurement_analysis = gemini_module.analyze_parts_procurement(
        text_content,
        ui_link=ui_link,
        use_cache=True
    )
    
    if "error" in procurement_analysis:
        print(f"  Error in parts procurement analysis: {procurement_analysis['error']}")
        return None
        
    # Check if parts procurement is required
    if procurement_analysis.get("requires_parts_procurement", False):
        items_count = len(procurement_analysis.get("key_items_needed", []))
        print(f"  Contract requires parts procurement. Found {items_count} key items.")
        
        # Format a summary for display/logging
        summary = []
        for item in procurement_analysis.get("key_items_needed", []):
            item_name = item.get("item_name", "Unnamed item")
            price = item.get("estimated_price", "Price unknown")
            suppliers = len(item.get("suppliers", []))
            summary.append(f"    - {item_name}: {price} ({suppliers} supplier(s) found)")
        
        if summary:
            print("\n".join(summary))
    else:
        print("  No parts procurement needed for this contract.")
    
    return procurement_analysis

def main():
    end_date = datetime.now()
    start_date = end_date - timedelta(days=1)
    posted_from = start_date.strftime('%m/%d/%Y')
    posted_to = end_date.strftime('%m/%d/%Y')
    # posted_to = "05/09/2025"
    # posted_from = "05/08/2025"
    # Create contracts directory if it doesn't exist
    os.makedirs(CONTRACTS_DIR, exist_ok=True)

    # Define the specific output filenames for this run
    start_str_file = posted_from.replace('/', '-')
    end_str_file = posted_to.replace('/', '-')
    output_json_filename = os.path.join(CONTRACTS_DIR, OUTPUT_JSON_FILENAME_TEMPLATE.format(start=start_str_file, end=end_str_file))
    output_csv_filename = os.path.join(CONTRACTS_DIR, OUTPUT_CSV_FILENAME_TEMPLATE.format(start=start_str_file, end=end_str_file))
    print(f"--- Using output JSON file: {output_json_filename} ---")
    print(f"--- Using output CSV file: {output_csv_filename} ---")

    print("\n--- Loading Previously Processed Contract IDs ---")
    processed_ids = load_processed_ids()
    # processed_ids=[]
    print(f"Loaded {len(processed_ids)} previously processed IDs (from .txt).")

    print(f"\n--- Loading Existing Data from {output_json_filename} ---")
    # Load existing data to append to it (for JSON incremental save)
    processed_contracts_data = load_existing_json_data(output_json_filename)
    print(f"Loaded {len(processed_contracts_data)} existing contract records (from .json).")

    print("\n--- Fetching Contracts ---")
    contracts = fetch_contracts(posted_from, posted_to, use_cache=True)

    if not contracts:
        print("No new contracts found or error fetching contracts.")
        # If we loaded data, save it back to CSV in case it wasn't saved before
        if processed_contracts_data:
             save_data_to_csv(processed_contracts_data, output_csv_filename)
        return

    print(f"\n--- Found {len(contracts)} Contracts in API Response ---")

    # --- Google Drive Integration ---
    print("\n--- Initializing Google Drive Service ---")
    gdrive_service = google_drive_utils.authenticate_gdrive()

    if not gdrive_service:
        print("Failed to authenticate Google Drive. File upload will be skipped.")
        # Decide if you want to proceed without uploads or stop
        # return

    main_drive_folder_id = None
    date_range_folder_id = None
    date_range_folder_name = f"Contracts_{posted_from.replace('/', '-')}_to_{posted_to.replace('/', '-')}" # Define name here
    if gdrive_service:
        # Define the main folder name where all date-range folders will reside
        main_sam_folder_name = "SAM_Contract_Files"
        print(f"Checking/Creating main Google Drive folder: '{main_sam_folder_name}'")
        main_drive_folder_id = google_drive_utils.find_or_create_folder(
            gdrive_service, main_sam_folder_name, google_drive_utils.ROOT_FOLDER_ID
        )

        if main_drive_folder_id:
            # Create a folder for this specific date range inside the main folder
            print(f"Checking/Creating date-range Google Drive folder: '{date_range_folder_name}'")
            date_range_folder_id = google_drive_utils.find_or_create_folder(
                gdrive_service, date_range_folder_name, main_drive_folder_id
            )
        else:
            print("Failed to create or find the main SAM folder. Cannot create date-range folder.")
            # Decide if you want to stop if the date range folder fails
            # return

    # --- Process Contracts and Collect Data ---
    contracts_to_process = contracts # Process all
    # contracts_to_process = contracts[:1] if contracts else [] # Test only first

    # Make sure date_range_folder_id exists if gdrive_service is available
    if gdrive_service and not date_range_folder_id:
         print("Error: Google Drive service is available but date range folder could not be created/found. Skipping processing.")
         contracts_to_process = [] # Prevent processing loop

    print(f"\n--- Checking {len(contracts_to_process)} Contract(s) for Processing ---")

    newly_processed_count = 0
    skipped_count = 0
    skipped_award_count = 0  # New counter for skipped award contracts
    skipped_email_count = 0 # New counter for skipped email
    # Keep track of newly added data in this run for the final CSV save
    new_data_this_run = []

    for i, contract in enumerate(contracts_to_process):
        notice_id = contract.get('noticeId')
        if not notice_id:
             print(f"Skipping contract at index {i} due to missing 'noticeId'.")
             continue

        # Check if this is a Sources Sought notice type
        notice_type = contract.get('type', '')
        if notice_type == "Sources Sought" or notice_type == "Presolicitation":
            print(f"Skipping contract {notice_id} - Type is 'Sources Sought'")
            continue

        # Check if this is an award notice with awardee information
        if contract["award"]!=None:
            print(f"Skipping contract {notice_id} - Contains award information")
            skipped_award_count += 1
            continue

        if notice_id in processed_ids:
            skipped_count += 1
            continue

        # print(f"\n--- Processing Contract {i+1}/{len(contracts_to_process)} ({notice_id}) ---")

        # Initialize variables
        contract_folder_id = None
        contract_folder_link = None
        processing_successful = False
        has_resource_links = bool(contract.get('resourceLinks'))
        is_feasible = None
        reasoning = None
        
        # Extract basic contract information first
        ui_link = contract.get('uiLink')
        naics_code = contract.get('naicsCode')
        posted_date = contract.get('postedDate')
        response_deadline = contract.get('responseDeadLine')
        poc_list = contract.get('pointOfContact', [])
        typeOfSetAside=contract.get("typeOfSetAside")
        primary_email = None
        
        # Extract primary email if available
        if poc_list:
            # Try finding primary
            for contact_person in poc_list:
                if contact_person.get('type') == 'primary' and contact_person.get('email'):
                    primary_email = contact_person['email']
                    break
            # If no primary found, take the first email available
            if not primary_email:
                for contact_person in poc_list:
                    if contact_person.get('email'):
                        primary_email = contact_person['email']
                        break
        if primary_email:
            #  print(f"  Extracted Primary Email: {primary_email}")
            pass
        else:
             print("  No primary email found for Point of Contact.")

        # --- Skip if email matches DibbsBSM@dla.mil ---
        if primary_email == 'DibbsBSM@dla.mil':
            # print(f"Skipping contract {notice_id} - Point of contact email is DibbsBSM@dla.mil")
            skipped_email_count += 1
            continue
        # --- End of email skip check ---

        if typeOfSetAside!="SBA":
            skipped_email_count += 1
            continue
        # --- End of email skip check ---

        # 1. First, analyze with OpenAI to determine feasibility
        if ui_link and openai_available:
            # print(f"  Fetching and analyzing text from UI link: {ui_link}")
            text_details = fetch_ui_link_data(ui_link, use_cache=True)
            if text_details and 'text_content' in text_details:
                text_content = text_details['text_content']
                print(f"  Retrieved {len(text_content)} characters of text content from UI link.")
                
                # Call OpenAI for analysis
                print("  Sending to gemini for analysis...")
                ai_analysis = gemini_module.analyze_contract_text(
                    text_content, 
                    use_cache=True
                )
                
                # Extract the parts of the AI analysis
                if isinstance(ai_analysis, dict):
                    is_feasible = ai_analysis.get('is_feasible')
                    reasoning = ai_analysis.get('reasoning')
                    print(f"  AI analysis completed. Feasible: {is_feasible}")
                    processing_successful = True  # Mark as processed even if not feasible
                else:
                    print(f"  AI analysis returned unexpected format: {type(ai_analysis)}")
                    processing_successful = True  # Still mark as processed to avoid repeated attempts
            else:
                print("  Failed to extract text content from UI link.")
                processing_successful = True  # Mark as processed to avoid repeated attempts
        elif not openai_available:
            print("  OpenAI integration not available. Skipping text analysis.")
            processing_successful = True  # Mark as processed even without analysis
        elif not ui_link:
            print("  No UI link available for text extraction and analysis.")
            processing_successful = True  # Mark as processed to avoid repeated attempts

        # 2. Only download files if contract is feasible
        if processing_successful and is_feasible is True and gdrive_service and date_range_folder_id and has_resource_links:
            print(f"  Contract deemed feasible. Processing resource files...")
            temp_folder_id, temp_folder_link = google_drive_utils.process_contract_files(
                gdrive_service, contract, date_range_folder_id
            )
            if temp_folder_id:
                contract_folder_id = temp_folder_id
                contract_folder_link = temp_folder_link
                print(f"  Successfully created Drive folder and downloaded files.")
            else:
                print(f"  Failed to create Drive folder or download files.")
        elif processing_successful and is_feasible is False:
            print(f"  Contract not deemed feasible. Skipping file downloads.")
            # Still mark as successful processing since we made a decision
        elif not gdrive_service:
            print("  Google Drive service not available. Skipping file processing.")
        elif not date_range_folder_id:
            print("  Date range folder ID not available. Skipping file processing.")
        elif not has_resource_links:
            print(f"  No resource links found for {notice_id}. No files to download.")

        # 3. Prepare Data and Save Incrementally (JSON and CSV)
        if processing_successful:
            # Extract place of performance information
            # place_of_performance = contract.get('placeOfPerformance', {})
            if("placeOfPerformance" in contract and contract["placeOfPerformance"] is not None):
                place_of_performance = contract["placeOfPerformance"]
            else:
                place_of_performance = {}
                
            # Extract city information
            pop_city = {}
            if place_of_performance.get('city'):
                pop_city = place_of_performance['city']
            pop_city_name = pop_city.get('name', '')
            pop_city_code = pop_city.get('code', '')
            
            # Extract state information
            pop_state = {}
            if place_of_performance.get('state'):
                pop_state = place_of_performance['state']
            pop_state_name = pop_state.get('name', '')
            pop_state_code = pop_state.get('code', '')
            
            # Extract zip code
            pop_zip = place_of_performance.get('zip', '')
            
            # Extract country information
            pop_country = {}
            if place_of_performance.get('country'):
                pop_country = place_of_performance['country']
            pop_country_name = pop_country.get('name', '')
            pop_country_code = pop_country.get('code', '')
            
            contract_data = {
                "noticeId": notice_id,
                "title": contract.get('title', ''),
                "uiLink": ui_link,
                "naicsCode": naics_code,
                "postedDate": posted_date,
                "responseDeadLine": response_deadline,
                "pointOfContactEmail": primary_email,
                "googleDriveFolderId": contract_folder_id,
                "googleDriveFolderLink": contract_folder_link,
                "isFeasible": is_feasible,
                # Add place of performance information
                "popCityName": pop_city_name,
                "popStateName": pop_state_name
            }
            
            processed_contracts_data.append(contract_data)
            new_data_this_run.append(contract_data)

            # Save the entire updated list to the JSON file immediately
            save_data_to_json(processed_contracts_data, output_json_filename)
            
            # Append this contract to the CSV file immediately
            if contract_ai_utils_available:
                contract_ai_utils.append_contract_to_csv_dynamic(
                    contract_data, output_csv_filename
                )
            else:
                print("Warning: contract_ai_utils.py not found or missing dependencies.")
                append_contract_to_csv(contract_data, output_csv_filename)  # fallback

            # Mark as Processed in .txt file
            save_processed_id(notice_id)
            processed_ids.add(notice_id)
            newly_processed_count += 1
            print(f"Successfully processed and saved contract {notice_id} to {output_json_filename} and {output_csv_filename}.")

            # Process parts procurement analysis
            # parts_procurement_analysis = process_parts_procurement(contract_data, text_content, ui_link)
            # if parts_procurement_analysis:
            #     print("\n--- Parts Procurement Analysis ---")
            #     print(json.dumps(parts_procurement_analysis))

        else:
             print(f"Contract {notice_id} processing skipped or failed prerequisites. Will retry next time.")


    # --- Final Output & CSV Save ---
    print(f"\n--- Processing Summary ---")
    print(f"Total contracts checked in API response: {len(contracts_to_process)}")
    print(f"Skipped due to award information: {skipped_award_count}")
    print(f"Skipped due to specific email (DibbsBSM@dla.mil): {skipped_email_count}") # Added email skip count
    print(f"Newly processed and saved this run: {newly_processed_count}")
    print(f"Skipped (already processed according to .txt): {skipped_count}")
    print(f"Total records now in {output_json_filename}: {len(processed_contracts_data)}")

    # --- Get Shareable Link for the MAIN Date Range Folder (Optional) ---
    # You might still want the link to the parent folder containing all individual contract folders
    if gdrive_service and date_range_folder_id:
        print("\n--- Getting Google Drive Link for Date Range Folder ---")
        parent_shareable_link = google_drive_utils.get_shareable_link(gdrive_service, date_range_folder_id)
        if parent_shareable_link:
            print(f"\nParent folder containing all processed contracts for this date range:")
            print(f"Folder Name: '{date_range_folder_name}'")
            print(f"Link: {parent_shareable_link}")
        else:
            print("\nCould not retrieve the shareable link for the main date range Google Drive folder.")
            print(f"You can find the folder named '{date_range_folder_name}' inside '{main_sam_folder_name}' in your Google Drive.")
    # else:
    #     print("\nGoogle Drive operations were skipped or failed. No parent folder link available.")

    print("\n--- Script Finished ---")


if __name__ == "__main__":
    main()
    # ... rest of your example code if needed ...
