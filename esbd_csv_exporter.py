#!/usr/bin/env python3
"""
ESBD Auto Processor
Automatically processes yesterday's ESBD solicitations daily.

USAGE:
------
Simply run the script - it automatically processes yesterday's solicitations:
   python3 esbd_csv_exporter.py

Or from localContracts.py:
   python3 localContracts.py

WHAT IT DOES:
-------------
1. Calculates yesterday's date automatically
2. Exports CSV from ESBD website for yesterday
3. Reads all solicitations from the CSV
4. Processes each solicitation:
   - Checks if registration is required
   - Downloads attachments
   - Generates vendor leads with AI
   - Adds successful results to Google Sheets 'localContracts' tab
5. Provides detailed summary of results

FEATURES:
---------
- Fully automated - no manual date entry needed
- Perfect for daily cron jobs
- Processes solicitations directly from CSV (no Google Sheets tracking needed)
- Visible browser mode to see automation in action
- Detailed logging and error handling

AUTOMATION SETUP:
-----------------
Add to crontab for daily execution at 8 AM:
   0 8 * * * cd /home/abhiram/govermentCOntracts && python3 esbd_csv_exporter.py >> logs/esbd_auto.log 2>&1

NOTES:
------
- Browser will be visible during automation (not headless)
- Downloaded CSV files saved to: esbd_downloads/
- Results written to Google Sheets 'Quote Request' -> 'localContracts' tab
- Processing typically takes 5-10 minutes depending on number of solicitations
"""

import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


def step1_inspect_esbd_website(output_dir="esbd_inspection"):
    """
    Step 1: Visit ESBD website, save page source, and identify form elements.
    
    This function will:
    1. Open the ESBD search/export page
    2. Save the HTML source to a file
    3. Print all input fields, buttons, and links
    4. Take a screenshot for visual reference
    
    Args:
        output_dir (str): Directory to save inspection files
    """
    print("="*80)
    print("STEP 1: INSPECTING ESBD WEBSITE")
    print("="*80)
    
    # Create output directory
    os.makedirs(output_dir, exist_ok=True)
    
    driver = None
    try:
        # Configure Chrome - NOT headless so we can see what's happening
        chrome_options = Options()
        # chrome_options.add_argument("--headless")  # Commented out to see browser
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        
        # Initialize driver
        print("Initializing Chrome driver...")
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.set_page_load_timeout(60)
        
        # Navigate to ESBD main page
        # This is a JavaScript-heavy single-page application that takes time to load
        esbd_url = "https://www.txsmartbuy.gov/esbd"
        print(f"\nNavigating to: {esbd_url}")
        driver.get(esbd_url)
        
        # Wait for JavaScript to render the page content
        # The initial HTML shows "Page not found" but the JS app will load the real content
        print("Waiting for JavaScript application to load (this may take 15-20 seconds)...")
        
        # Wait for the loading indicator to disappear or content to appear
        wait_time = 0
        max_wait = 30
        content_loaded = False
        
        while wait_time < max_wait:
            time.sleep(2)
            wait_time += 2
            
            # Check if content has loaded by looking for common ESBD elements
            page_source = driver.page_source
            
            # Check for signs that the app has loaded
            if any(keyword in page_source for keyword in ['solicitation', 'Solicitation', 'ESBD', 'Search', 'Export', 'Date']):
                print(f"✅ Content detected after {wait_time} seconds!")
                content_loaded = True
                break
            
            print(f"  Still waiting... ({wait_time}s / {max_wait}s)", end='\r')
        
        if not content_loaded:
            print(f"\n⚠️  Content may not have fully loaded after {max_wait} seconds")
            print("   Proceeding with inspection anyway...")
        
        # Give a bit more time for any animations to complete
        print("\nGiving extra time for page to stabilize...")
        time.sleep(5)
        
        # Save screenshot
        screenshot_path = os.path.join(output_dir, "esbd_page_screenshot.png")
        driver.save_screenshot(screenshot_path)
        print(f"✅ Screenshot saved: {screenshot_path}")
        
        # Save page source
        html_path = os.path.join(output_dir, "esbd_page_source.html")
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(driver.page_source)
        print(f"✅ Page source saved: {html_path}")
        
        # Get page title and URL
        print(f"\n📄 Page Title: {driver.title}")
        print(f"🔗 Current URL: {driver.current_url}")
        
        # Analyze the page structure
        print("\n" + "="*80)
        print("ANALYZING PAGE STRUCTURE")
        print("="*80)
        
        # Find all input fields
        print("\n🔍 INPUT FIELDS:")
        print("-"*80)
        inputs = driver.find_elements(By.TAG_NAME, 'input')
        input_info = []
        
        for i, inp in enumerate(inputs, 1):
            inp_type = inp.get_attribute('type') or 'text'
            inp_id = inp.get_attribute('id') or 'N/A'
            inp_name = inp.get_attribute('name') or 'N/A'
            inp_placeholder = inp.get_attribute('placeholder') or 'N/A'
            inp_class = inp.get_attribute('class') or 'N/A'
            inp_value = inp.get_attribute('value') or 'N/A'
            
            info = {
                'index': i,
                'type': inp_type,
                'id': inp_id,
                'name': inp_name,
                'placeholder': inp_placeholder,
                'class': inp_class,
                'value': inp_value
            }
            input_info.append(info)
            
            # Print relevant inputs (dates, search, etc.)
            if any(keyword in inp_placeholder.lower() or keyword in inp_id.lower() or keyword in inp_name.lower() 
                   for keyword in ['date', 'search', 'from', 'to', 'start', 'end']):
                print(f"\n  Input #{i} ⭐ RELEVANT:")
                print(f"    Type: {inp_type}")
                print(f"    ID: {inp_id}")
                print(f"    Name: {inp_name}")
                print(f"    Placeholder: {inp_placeholder}")
                print(f"    Class: {inp_class}")
        
        # Find all buttons
        print("\n\n🔍 BUTTONS:")
        print("-"*80)
        buttons = driver.find_elements(By.TAG_NAME, 'button')
        button_info = []
        
        for i, btn in enumerate(buttons, 1):
            btn_text = btn.text.strip() or 'N/A'
            btn_id = btn.get_attribute('id') or 'N/A'
            btn_class = btn.get_attribute('class') or 'N/A'
            btn_type = btn.get_attribute('type') or 'N/A'
            btn_onclick = btn.get_attribute('onclick') or 'N/A'
            
            info = {
                'index': i,
                'text': btn_text,
                'id': btn_id,
                'class': btn_class,
                'type': btn_type,
                'onclick': btn_onclick
            }
            button_info.append(info)
            
            # Print relevant buttons (export, search, CSV, etc.)
            if any(keyword in btn_text.lower() or keyword in btn_id.lower() or keyword in btn_class.lower()
                   for keyword in ['export', 'csv', 'download', 'search', 'submit']):
                print(f"\n  Button #{i} ⭐ RELEVANT:")
                print(f"    Text: {btn_text}")
                print(f"    ID: {btn_id}")
                print(f"    Class: {btn_class}")
                print(f"    Type: {btn_type}")
                if len(btn_onclick) < 100:
                    print(f"    OnClick: {btn_onclick}")
        
        # Find all links
        print("\n\n🔍 LINKS (with relevant keywords):")
        print("-"*80)
        links = driver.find_elements(By.TAG_NAME, 'a')
        link_info = []
        
        for i, link in enumerate(links, 1):
            link_text = link.text.strip() or 'N/A'
            link_href = link.get_attribute('href') or 'N/A'
            link_id = link.get_attribute('id') or 'N/A'
            link_class = link.get_attribute('class') or 'N/A'
            
            # Only show relevant links
            if any(keyword in link_text.lower() or keyword in link_href.lower()
                   for keyword in ['export', 'csv', 'download', 'search', 'solicitation']):
                info = {
                    'index': i,
                    'text': link_text,
                    'href': link_href,
                    'id': link_id,
                    'class': link_class
                }
                link_info.append(info)
                
                print(f"\n  Link #{i}:")
                print(f"    Text: {link_text}")
                print(f"    Href: {link_href[:100]}...")  # Truncate long URLs
                print(f"    ID: {link_id}")
        
        # Look for forms
        print("\n\n🔍 FORMS:")
        print("-"*80)
        forms = driver.find_elements(By.TAG_NAME, 'form')
        print(f"Found {len(forms)} form(s) on the page")
        
        for i, form in enumerate(forms, 1):
            form_id = form.get_attribute('id') or 'N/A'
            form_name = form.get_attribute('name') or 'N/A'
            form_action = form.get_attribute('action') or 'N/A'
            form_method = form.get_attribute('method') or 'N/A'
            
            print(f"\n  Form #{i}:")
            print(f"    ID: {form_id}")
            print(f"    Name: {form_name}")
            print(f"    Action: {form_action}")
            print(f"    Method: {form_method}")
        
        # Save detailed report
        report_path = os.path.join(output_dir, "inspection_report.txt")
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write("ESBD WEBSITE INSPECTION REPORT\n")
            f.write("="*80 + "\n\n")
            f.write(f"URL: {driver.current_url}\n")
            f.write(f"Title: {driver.title}\n\n")
            
            f.write("\nALL INPUT FIELDS:\n")
            f.write("-"*80 + "\n")
            for info in input_info:
                f.write(f"\nInput #{info['index']}:\n")
                for key, value in info.items():
                    if key != 'index':
                        f.write(f"  {key}: {value}\n")
            
            f.write("\n\nALL BUTTONS:\n")
            f.write("-"*80 + "\n")
            for info in button_info:
                f.write(f"\nButton #{info['index']}:\n")
                for key, value in info.items():
                    if key != 'index':
                        f.write(f"  {key}: {value}\n")
            
            f.write("\n\nRELEVANT LINKS:\n")
            f.write("-"*80 + "\n")
            for info in link_info:
                f.write(f"\nLink #{info['index']}:\n")
                for key, value in info.items():
                    if key != 'index':
                        f.write(f"  {key}: {value}\n")
        
        print(f"\n✅ Detailed report saved: {report_path}")
        
        # Keep browser open for manual inspection
        print("\n" + "="*80)
        print("INSPECTION COMPLETE")
        print("="*80)
        print(f"Files saved in: {os.path.abspath(output_dir)}/")
        print("  - esbd_page_source.html (full HTML)")
        print("  - esbd_page_screenshot.png (visual reference)")
        print("  - inspection_report.txt (detailed element list)")
        print("\n⏸️  Browser will stay open for 30 seconds for manual inspection...")
        print("    Look for date input fields and export/CSV buttons")
        
        time.sleep(30)
        
        return {
            'inputs': input_info,
            'buttons': button_info,
            'links': link_info,
            'html_path': html_path,
            'screenshot_path': screenshot_path,
            'report_path': report_path
        }
        
    except Exception as e:
        print(f"\n❌ ERROR during inspection: {e}")
        import traceback
        traceback.print_exc()
        return None
    
    finally:
        if driver:
            print("\nClosing browser...")
            driver.quit()


def step2_export_csv_with_date_range(start_date, end_date, download_dir="esbd_downloads"):
    """
    Step 2: Export CSV using discovered selectors.
    
    Uses the selectors found in Step 1 to automate CSV export.
    
    Args:
        start_date (str): Start date in format MM/DD/YYYY
        end_date (str): End date in format MM/DD/YYYY
        download_dir (str): Directory to save downloaded CSV
        
    Returns:
        str: Path to downloaded CSV file, or None if failed
    """
    print("="*80)
    print("STEP 2: EXPORTING CSV WITH DATE RANGE")
    print("="*80)
    print(f"Start Date: {start_date}")
    print(f"End Date: {end_date}")
    print("="*80)
    
    # Create download directory
    download_path = os.path.abspath(download_dir)
    os.makedirs(download_path, exist_ok=True)
    print(f"Download directory: {download_path}")
    
    # Track files before download
    initial_files = set(os.listdir(download_path)) if os.path.exists(download_path) else set()
    
    driver = None
    try:
        # Configure Chrome options for download
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        
        # Set download preferences
        prefs = {
            "download.default_directory": download_path,
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True
        }
        chrome_options.add_experimental_option("prefs", prefs)
        
        # Initialize driver
        print("\nInitializing Chrome driver...")
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.set_page_load_timeout(60)
        
        # Navigate to ESBD page
        esbd_url = "https://www.txsmartbuy.gov/esbd"
        print(f"\nNavigating to: {esbd_url}")
        driver.get(esbd_url)
        
        # Wait for page to load
        print("Waiting for page to load...")
        time.sleep(8)  # Longer wait to ensure full load
        
        # Additional wait to ensure JavaScript is fully loaded
        wait = WebDriverWait(driver, 20)
        
        # Step 0: Change status field to "Posted On"
        print(f"\n{'='*60}")
        print("Step 0: Setting status to 'Posted On'")
        print(f"{'='*60}")
        try:
            # Look for select/dropdown elements that might be the status field
            # Common names: "status", "dateType", "searchType", etc.
            selects = driver.find_elements(By.TAG_NAME, "select")
            print(f"  Found {len(selects)} select/dropdown elements")
            
            status_field = None
            for i, select in enumerate(selects):
                select_name = select.get_attribute('name') or 'N/A'
                select_id = select.get_attribute('id') or 'N/A'
                print(f"    Select {i+1}: name='{select_name}', id='{select_id}'")
                
                # Check if this might be the date type/status field
                if any(keyword in select_name.lower() or keyword in select_id.lower() 
                       for keyword in ['date', 'type', 'status', 'search']):
                    # Get all options in this select
                    from selenium.webdriver.support.ui import Select
                    select_obj = Select(select)
                    options = [opt.text for opt in select_obj.options]
                    print(f"      Options: {options}")
                    
                    # Check if "Posted On" or similar is an option
                    for option_text in options:
                        if 'posted' in option_text.lower():
                            status_field = select
                            print(f"      ✓ Found 'Posted On' option!")
                            break
                    
                    if status_field:
                        break
            
            if status_field:
                from selenium.webdriver.support.ui import Select
                select_obj = Select(status_field)
                
                # Try to select "Posted On"
                for option in select_obj.options:
                    if 'posted' in option.text.lower():
                        print(f"  Setting status to: '{option.text}'")
                        select_obj.select_by_visible_text(option.text)
                        time.sleep(1)
                        print("✅ Status set to 'Posted On' successfully")
                        break
            else:
                print("⚠️  Status dropdown not found, using default")
                
        except Exception as e:
            print(f"⚠️  Could not set status field: {e}")
            print("  Proceeding with default status...")
        
        time.sleep(2)
        
        # Step 1: Find and fill the start date field
        print(f"\n{'='*60}")
        print(f"Step 1: Filling start date field with: {start_date}")
        print(f"{'='*60}")
        try:
            start_date_field = wait.until(
                EC.presence_of_element_located((By.NAME, "startDate"))
            )
            print("  Found start date field, clearing existing value...")
            start_date_field.clear()
            time.sleep(1)
            print(f"  Typing: {start_date}")
            start_date_field.send_keys(start_date)
            time.sleep(1)
            print("✅ Start date entered successfully")
        except Exception as e:
            print(f"❌ ERROR: Could not find or fill start date field: {e}")
            driver.save_screenshot(os.path.join(download_path, "error_start_date.png"))
            return None
        
        # Step 2: Find and fill the end date field
        print(f"\n{'='*60}")
        print(f"Step 2: Filling end date field with: {end_date}")
        print(f"{'='*60}")
        try:
            end_date_field = driver.find_element(By.NAME, "endDate")
            print("  Found end date field, clearing existing value...")
            end_date_field.clear()
            time.sleep(1)
            print(f"  Typing: {end_date}")
            end_date_field.send_keys(end_date)
            time.sleep(1)
            print("✅ End date entered successfully")
        except Exception as e:
            print(f"❌ ERROR: Could not find or fill end date field: {e}")
            driver.save_screenshot(os.path.join(download_path, "error_end_date.png"))
            return None
        
        time.sleep(2)
        
        # Step 3: Click the Search button
        print(f"\n{'='*60}")
        print("Step 3: Clicking Search button...")
        print(f"{'='*60}")
        try:
            # Find the Search button (class="esbd-button" and text="Search")
            print("  Looking for Search button...")
            search_buttons = driver.find_elements(By.CSS_SELECTOR, "button.esbd-button")
            search_button = None
            
            print(f"  Found {len(search_buttons)} buttons with class 'esbd-button'")
            for i, btn in enumerate(search_buttons):
                btn_text = btn.text.strip()
                print(f"    Button {i+1}: '{btn_text}'")
                if btn_text == "Search":
                    search_button = btn
                    break
            
            if search_button:
                print("  Clicking Search button...")
                search_button.click()
                print("✅ Search button clicked successfully")
                
                # Wait for search results to load
                print("  Waiting for search results to load...")
                time.sleep(7)  # Longer wait to see results
                print("  Results should be loaded now")
            else:
                print("⚠️  Search button not found, proceeding to export anyway...")
        except Exception as e:
            print(f"⚠️  Could not click search button: {e}")
            print("Proceeding to export anyway...")
        
        # Step 4: Click the Export to CSV button
        print(f"\n{'='*60}")
        print("Step 4: Clicking 'Export to CSV' button...")
        print(f"{'='*60}")
        try:
            # Find the Export to CSV button
            print("  Looking for Export to CSV button...")
            export_buttons = driver.find_elements(By.CSS_SELECTOR, "button.esbd-button")
            export_button = None
            
            print(f"  Found {len(export_buttons)} buttons with class 'esbd-button'")
            for i, btn in enumerate(export_buttons):
                btn_text = btn.text.strip()
                print(f"    Button {i+1}: '{btn_text}'")
                if "Export to CSV" in btn_text or "Export" in btn_text:
                    export_button = btn
                    break
            
            if export_button:
                print(f"  Found Export button with text: '{export_button.text}'")
                time.sleep(2)
                print("  Clicking Export to CSV button...")
                export_button.click()
                print("✅ Export to CSV button clicked successfully")
                
                # Wait for download to start
                print("\n  Waiting for CSV download to complete...")
                print("  (You should see the download starting in the browser)")
                downloaded_file = wait_for_new_csv_file(download_path, initial_files, timeout=60)
                
                if downloaded_file:
                    print(f"\n{'='*60}")
                    print("✅ SUCCESS! CSV DOWNLOADED")
                    print(f"{'='*60}")
                    print(f"File: {downloaded_file}")
                    
                    # Get file size
                    file_size = os.path.getsize(downloaded_file)
                    print(f"Size: {file_size:,} bytes")
                    
                    return downloaded_file
                else:
                    print("\n❌ ERROR: CSV download failed or timed out")
                    driver.save_screenshot(os.path.join(download_path, "error_no_download.png"))
                    return None
            else:
                print("❌ ERROR: Export to CSV button not found")
                driver.save_screenshot(os.path.join(download_path, "error_no_export_button.png"))
                return None
                
        except Exception as e:
            print(f"❌ ERROR: Failed to click export button: {e}")
            driver.save_screenshot(os.path.join(download_path, "error_export_click.png"))
            return None
        
    except Exception as e:
        print(f"\n❌ ERROR: Unexpected error during CSV export: {e}")
        import traceback
        traceback.print_exc()
        if driver:
            driver.save_screenshot(os.path.join(download_path, "error_unexpected.png"))
        return None
    
    finally:
        if driver:
            print("\nClosing browser...")
            driver.quit()


def wait_for_new_csv_file(download_dir, initial_files, timeout=30):
    """
    Wait for a new CSV file to be downloaded.
    
    Args:
        download_dir (str): Directory where files are downloaded
        initial_files (set): Set of filenames that existed before download
        timeout (int): Maximum time to wait in seconds
        
    Returns:
        str: Full path to the downloaded CSV file, or None if timeout
    """
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        # Get current files (excluding temp files)
        current_files = set([f for f in os.listdir(download_dir) 
                            if not f.endswith('.crdownload') and not f.endswith('.tmp')])
        
        # Find new files
        new_files = current_files - initial_files
        
        # Look for CSV files in new files
        csv_files = [f for f in new_files if f.endswith('.csv')]
        
        if csv_files:
            # Return the first CSV file found
            csv_path = os.path.join(download_dir, csv_files[0])
            
            # Verify file is not being written to (size stable)
            time.sleep(2)
            size1 = os.path.getsize(csv_path)
            time.sleep(1)
            size2 = os.path.getsize(csv_path)
            
            if size1 == size2 and size1 > 0:
                return csv_path
        
        elapsed = int(time.time() - start_time)
        print(f"  Waiting for download... ({elapsed}s / {timeout}s)", end='\r')
        time.sleep(1)
    
    print(f"\n  Timeout after {timeout} seconds")
    return None


def process_csv_and_create_leads(csv_file_path):
    """
    Process the downloaded CSV and run through the existing localContracts workflow.
    
    Args:
        csv_file_path (str): Path to the downloaded CSV file
        
    Returns:
        list: Results from processing
    """
    import pandas as pd
    import pygsheets
    
    print("\n" + "="*80)
    print("PROCESSING CSV AND GENERATING LEADS")
    print("="*80)
    
    try:
        # Read CSV file
        print(f"\nReading CSV file: {csv_file_path}")
        df = pd.read_csv(csv_file_path)
        print(f"✅ Found {len(df)} solicitations in CSV")
        print(f"Columns: {list(df.columns)}")
        
        # Convert to list of dictionaries (same format as Google Sheets)
        records = df.to_dict('records')
        
        # Connect to Google Sheets for output
        print("\nConnecting to Google Sheets for output...")
        gc = pygsheets.authorize(service_file='key.json')
        sh = gc.open('Quote Request')
        
        # Get or create processing worksheet
        try:
            temp_wks = sh.worksheet_by_title('ESBD_CSV_Processing')
        except:
            print("Creating temporary processing worksheet...")
            temp_wks = sh.add_worksheet('ESBD_CSV_Processing', rows=1000, cols=15)
            # Add headers
            headers = ['Name', 'Solicitation ID', 'Due Date', 'Status', 'Reasoning']
            temp_wks.update_row(1, headers)
        
        print("✅ Google Sheets connected")
        
        # Import the processing function from localContracts
        print("\nImporting processing functions from localContracts...")
        from localContracts_texas import processEsbdSolicitations
        
        # Process the solicitations
        print("\n" + "="*80)
        print("PROCESSING SOLICITATIONS")
        print("="*80)
        results = processEsbdSolicitations(records, temp_wks, sh)
        
        print(f"\n✅ Processing complete! {len(results)} successful solicitations")
        return results
        
    except Exception as e:
        print(f"\n❌ ERROR processing CSV: {e}")
        import traceback
        traceback.print_exc()
        return []


def _get_or_create_skipped_wks(spreadsheet):
    """Get or create the localContracts_skipped worksheet."""
    try:
        wks = spreadsheet.worksheet_by_title('localContracts_skipped')
        print("✅ Found 'localContracts_skipped' worksheet")
        return wks
    except Exception:
        try:
            wks = spreadsheet.add_worksheet('localContracts_skipped', rows=1000, cols=10)
            wks.update_value('A1', 'Name')
            wks.update_value('B1', 'Solicitation ID')
            wks.update_value('C1', 'Due Date')
            wks.update_value('D1', 'Status')
            wks.update_value('E1', 'Reasoning')
            print("✅ Created 'localContracts_skipped' worksheet with headers")
            return wks
        except Exception as e2:
            print(f"❌ Could not create 'localContracts_skipped' worksheet: {e2}")
            return None


def _get_or_create_east_tx_wks(spreadsheet, sheet_title):
    """Get or create the East TX local contracts worksheet."""
    try:
        wks = spreadsheet.worksheet_by_title(sheet_title)
        print(f"✅ Found '{sheet_title}' worksheet")
        return wks
    except Exception:
        try:
            wks = spreadsheet.add_worksheet(sheet_title, rows=1000, cols=15)
            headers = ['Name', 'Solicitation ID', 'Due Date', 'Status', 'Reasoning',
                       'Subject', 'Email Body', 'Emails', '', 'Folder Link']
            wks.update_row(1, headers)
            print(f"✅ Created '{sheet_title}' worksheet with headers")
            return wks
        except Exception as e2:
            print(f"❌ Could not create '{sheet_title}' worksheet: {e2}")
            return None


def processEsbdSolicitationsFromCsv(records, local_contracts_wks, spreadsheet=None, east_tx_wks=None):
    """
    Process multiple ESBD solicitations from CSV file and add successful results to localContracts tab.
    Skipped contracts are logged to the localContracts_skipped sheet.
    Contracts in East TX target counties are also added to east_tx_wks.
    
    Args:
        records (list): List of dictionaries from CSV with solicitation data
        local_contracts_wks: The localContracts worksheet object to write results to
        spreadsheet: The pygsheets Spreadsheet object (needed for skipped sheet access)
        east_tx_wks: Optional worksheet for East TX county contracts
        
    Returns:
        list: Results from successful processing
    """
    from localContracts_texas import (
        can_apply_without_registration, add_row_to_local_contracts,
        find_next_available_row,
        solicitation_exists_in_local_contracts, upload_esbd_files_to_drive,
        add_row_to_skipped_contracts, solicitation_exists_in_skipped,
    )
    from config import EAST_TX_COUNTIES
    from get_empty_rows import (
        GmailClient, NEXAN_ACCOUNT_CONFIG,
        create_email_draft, generate_subject_body,
    )

    print(f"Processing {len(records)} ESBD solicitation(s) from CSV...")
    results = []

    print("Authenticating Gmail client for draft creation...")
    gmail_client = GmailClient(NEXAN_ACCOUNT_CONFIG)
    if not gmail_client.authenticate():
        print("⚠️ Failed to authenticate Gmail. Will continue without draft creation.")
        gmail_client = None
    gmail_lock = threading.Lock()
    
    # Get the localContracts_skipped worksheet
    skipped_wks = None
    if spreadsheet:
        skipped_wks = _get_or_create_skipped_wks(spreadsheet)
    else:
        print("⚠️ No spreadsheet object provided — skipped contracts will not be logged to sheet")
    
    # Pre-filter: collect eligible records before parallel processing
    eligible_records = []
    for i, record in enumerate(records, start=1):
        solicitation_id = str(record.get('Solicitation ID', '')).strip()
        if not solicitation_id:
            print(f"Record {i}: No Solicitation ID found, skipping...")
            continue
        
        name = str(record.get('Name', '')).strip()
        due_date = str(record.get('Due Date', '')).strip()
        
        exists, existing_row = solicitation_exists_in_local_contracts(local_contracts_wks, solicitation_id)
        if exists:
            print(f"Record {i}: Solicitation {solicitation_id} already exists in localContracts (row {existing_row}), skipping...")
            continue
        
        if skipped_wks:
            already_skipped, _ = solicitation_exists_in_skipped(skipped_wks, solicitation_id)
            if already_skipped:
                print(f"Record {i}: Solicitation {solicitation_id} already in localContracts_skipped, skipping...")
                continue
        
        eligible_records.append({
            'index': i,
            'solicitation_id': solicitation_id,
            'name': name,
            'due_date': due_date,
        })
    
    total_eligible = len(eligible_records)
    print(f"\n{total_eligible} eligible solicitations to process (2 in parallel)")
    
    sheet_lock = threading.Lock()

    # Pre-compute next available row for each sheet ONCE before threads start.
    # Each counter is a single-element list so threads can mutate it under the lock.
    next_row_local = [find_next_available_row(local_contracts_wks)]
    next_row_east_tx = [find_next_available_row(east_tx_wks)] if east_tx_wks else [1]
    next_row_skipped = [find_next_available_row(skipped_wks)] if skipped_wks else [1]
    print(f"Starting rows — localContracts: {next_row_local[0]}, eastTX: {next_row_east_tx[0]}, skipped: {next_row_skipped[0]}")

    def handle_csv_record(item):
        i = item['index']
        solicitation_id = item['solicitation_id']
        name = item['name']
        due_date = item['due_date']
        esbd_url = f"https://www.txsmartbuy.gov/esbd/{solicitation_id}"

        print(f"\n{'='*80}")
        print(f"Processing record {i}/{len(records)}: {solicitation_id}")
        print(f"{'='*80}")

        try:
            result = can_apply_without_registration(esbd_url, generate_leads=True)

            # Check if this contract is in an East TX target county
            county = result.get('county', 'Unknown')
            is_east_tx = east_tx_wks and any(
                county.lower() == c.lower() for c in EAST_TX_COUNTIES
            )
            if is_east_tx:
                already_in_east_tx, _ = solicitation_exists_in_local_contracts(east_tx_wks, solicitation_id)
                if already_in_east_tx:
                    print(f"📍 Record {i}: {county} County — already in eastTX_localContracts, skipping East TX add")
                    is_east_tx = False

            if result.get('skip_silently'):
                print(f"⏭️ Record {i}: Skipped silently — {result.get('reasoning', '')}")
                return

            if result['can_apply']:
                if 'lead_generation' in result and result['lead_generation'] and 'error' not in result['lead_generation']:
                    lead_gen = result['lead_generation']
                    reasoning = result.get('reasoning', 'Can apply without additional registration')

                    # Federal-style: OpenAI gives us emails; Gemini fills in subject/body.
                    complete_text = result.get('complete_text', '') or ''
                    gemini_subject, gemini_body = (None, None)
                    if complete_text.strip():
                        print(f"  Generating subject/body with Gemini (federal-style)...")
                        try:
                            gemini_subject, gemini_body = generate_subject_body(complete_text)
                        except Exception as e:
                            print(f"  ⚠️ Gemini subject/body generation errored: {e}")
                    if not gemini_subject or not gemini_body:
                        print(f"  ⚠️ Gemini subject/body unavailable — Gmail draft will be skipped.")

                    final_subject = (gemini_subject + " - texasLocal") if gemini_subject else "Not found"
                    final_body = gemini_body if gemini_body else "Not found"
                    emails_value = lead_gen.get('emails', 'Not found')

                    print(f"📤 Uploading files to Google Drive...")
                    if result.get("bonfire_files"):
                        from localContracts_texas import _upload_bonfire_files_to_drive
                        drive_folder_link = _upload_bonfire_files_to_drive(solicitation_id, result["bonfire_files"])
                    else:
                        drive_folder_link = upload_esbd_files_to_drive(solicitation_id)

                    with sheet_lock:
                        row = next_row_local[0]
                        next_row_local[0] += 1
                        target_row = add_row_to_local_contracts(
                            local_contracts_wks=local_contracts_wks,
                            name=name,
                            solicitation_id=solicitation_id,
                            due_date=due_date,
                            status="Can Apply - Leads Generated",
                            reasoning=reasoning,
                            subject=final_subject,
                            body=final_body,
                            emails=emails_value,
                            folder_link=drive_folder_link,
                            target_row=row
                        )
                        if is_east_tx:
                            etx_row = next_row_east_tx[0]
                            next_row_east_tx[0] += 1
                            add_row_to_local_contracts(
                                local_contracts_wks=east_tx_wks,
                                name=name,
                                solicitation_id=solicitation_id,
                                due_date=due_date,
                                status=f"Can Apply - Leads Generated ({county} Co.)",
                                reasoning=reasoning,
                                subject=final_subject,
                                body=final_body,
                                emails=emails_value,
                                folder_link=drive_folder_link,
                                target_row=etx_row
                            )
                            print(f"📍 Record {i}: Also added to eastTX_localContracts (row {etx_row}) — {county} County")
                    print(f"✅ Record {i}: Added to localContracts (row {target_row})")

                    if gmail_client and gemini_subject and gemini_body:
                        try:
                            with gmail_lock:
                                create_email_draft(
                                    emails_value,
                                    final_subject,
                                    final_body,
                                    gmail_client,
                                )
                        except Exception as e:
                            print(f"⚠️ Record {i}: Failed to create Gmail draft: {e}")
                    else:
                        print(f"  Skipping Gmail draft (gmail_client={'ok' if gmail_client else 'missing'}, "
                              f"subject={'ok' if gemini_subject else 'missing'}, body={'ok' if gemini_body else 'missing'}).")

                    results.append({
                        'esbd_url': esbd_url,
                        'solicitation_id': solicitation_id,
                        'name': name,
                        'can_apply': True,
                        'county': county,
                        'emails': emails_value,
                        'subject': final_subject,
                        'body': final_body
                    })

                    print(f"✅ Record {i}: Success - leads generated and added to localContracts")

                else:
                    error_msg = result.get('lead_generation', {}).get('error', 'Lead generation failed') if result.get('lead_generation') else 'Lead generation returned None'
                    reasoning = f"Can apply but lead generation failed: {error_msg}"
                    print(f"⚠️ Record {i}: {reasoning}")

                    with sheet_lock:
                        if skipped_wks:
                            try:
                                skip_row = next_row_skipped[0]
                                next_row_skipped[0] += 1
                                add_row_to_skipped_contracts(skipped_wks, name, solicitation_id, due_date, "Lead Gen Failed", reasoning, result.get('attachment_url'), target_row=skip_row)
                            except Exception as e:
                                print(f"⚠️ Failed to add to localContracts_skipped: {e}")

                        if is_east_tx:
                            try:
                                etx_row = next_row_east_tx[0]
                                next_row_east_tx[0] += 1
                                add_row_to_local_contracts(
                                    local_contracts_wks=east_tx_wks,
                                    name=name, solicitation_id=solicitation_id,
                                    due_date=due_date,
                                    status=f"Lead Gen Failed ({county} Co.)",
                                    reasoning=reasoning,
                                    subject="", body="", emails="",
                                    target_row=etx_row
                                )
                                print(f"📍 Record {i}: Added to eastTX_localContracts despite lead gen failure — {county} County")
                            except Exception as e:
                                print(f"⚠️ Failed to add to eastTX_localContracts: {e}")

            else:
                reasoning = result.get('reasoning', 'Registration required')
                status = result.get('status', 'Skipped')
                print(f"❌ Record {i}: {status} - {reasoning}")

                with sheet_lock:
                    if skipped_wks:
                        try:
                            skip_row = next_row_skipped[0]
                            next_row_skipped[0] += 1
                            add_row_to_skipped_contracts(skipped_wks, name, solicitation_id, due_date, status, reasoning, result.get('attachment_url'), target_row=skip_row)
                        except Exception as e:
                            print(f"⚠️ Failed to add to localContracts_skipped: {e}")

                    if is_east_tx:
                        try:
                            etx_row = next_row_east_tx[0]
                            next_row_east_tx[0] += 1
                            add_row_to_local_contracts(
                                local_contracts_wks=east_tx_wks,
                                name=name, solicitation_id=solicitation_id,
                                due_date=due_date,
                                status=f"{status} ({county} Co.)",
                                reasoning=reasoning,
                                subject="", body="", emails="",
                                target_row=etx_row
                            )
                            print(f"📍 Record {i}: Added to eastTX_localContracts despite skip — {county} County")
                        except Exception as e:
                            print(f"⚠️ Failed to add to eastTX_localContracts: {e}")

        except Exception as e:
            error_msg = str(e)[:100]
            print(f"❌ Record {i}: Error - {error_msg}")
            import traceback
            traceback.print_exc()

            if skipped_wks:
                try:
                    with sheet_lock:
                        skip_row = next_row_skipped[0]
                        next_row_skipped[0] += 1
                        add_row_to_skipped_contracts(skipped_wks, name, solicitation_id, due_date, "Processing Error", f"Error: {error_msg}", None, target_row=skip_row)
                except Exception as e2:
                    print(f"⚠️ Failed to add error to localContracts_skipped: {e2}")

    with ThreadPoolExecutor(max_workers=2) as executor:
        executor.map(handle_csv_record, eligible_records)
    
    # Clean up Bonfire session if one was started
    try:
        from bonfire_downloader import close_bonfire_session
        close_bonfire_session()
    except Exception:
        pass

    print(f"\n{'='*100}")
    print("ALL CSV ESBD SOLICITATIONS PROCESSED")
    print(f"{'='*100}")
    print(f"Total processed: {len(records)}")
    print(f"Eligible (not duplicates): {total_eligible}")
    print(f"Successful entries added to localContracts tab: {len(results)}")
    
    return results


def auto_process_yesterday_solicitations():
    """
    Automatically fetch and process all ESBD solicitations from yesterday.
    This function:
    1. Calculates yesterday's date range
    2. Exports CSV from ESBD website
    3. Processes all solicitations in the CSV
    4. Adds results to Google Sheets localContracts tab
    """
    from datetime import datetime, timedelta
    import pandas as pd
    import pygsheets
    
    print("\n" + "="*80)
    print("AUTO PROCESS YESTERDAY'S ESBD SOLICITATIONS")
    print("="*80)
    
    # Calculate yesterday's date
    yesterday = datetime.now() - timedelta(days=1)
    date_str = yesterday.strftime("%m/%d/%Y")
    # date_str="01/21/2026"
    
    print(f"Date range: {date_str} to {date_str}")
    print("="*80)
    
    # Step 1: Export CSV from ESBD website for yesterday
    print("\n📥 Step 1: Exporting CSV from ESBD website...")
    start_date = date_str
    end_date = date_str
    csv_file = step2_export_csv_with_date_range(start_date, end_date)
    
    if not csv_file:
        print("\n❌ FAILED: Could not export CSV from ESBD website")
        return None
    
    print(f"✅ CSV exported successfully: {csv_file}")
    
    # Step 2: Read CSV file
    print("\n📖 Step 2: Reading CSV file...")
    try:
        df = pd.read_csv(csv_file)
        print(f"✅ Found {len(df)} solicitations in CSV")
        print(f"Columns: {list(df.columns)}")
        
        # Convert to list of dictionaries
        records = df.to_dict('records')
        
    except Exception as e:
        print(f"❌ ERROR reading CSV file: {e}")
        return None
    
    # Step 3: Connect to Google Sheets
    print("\n📊 Step 3: Connecting to Google Sheets...")
    try:
        gc = pygsheets.authorize(service_file='key.json')
        sh = gc.open('Quote Request')
        
        # Get the localContracts worksheet
        try:
            local_contracts_wks = sh.worksheet_by_title('localContracts')
            print("✅ Found 'localContracts' worksheet")
        except Exception as e:
            print(f"❌ Error: Could not find 'localContracts' worksheet: {e}")
            return None

        # Get or create the East TX worksheet
        from config import EAST_TX_WORKSHEET
        east_tx_wks = _get_or_create_east_tx_wks(sh, EAST_TX_WORKSHEET)
            
    except Exception as e:
        print(f"❌ ERROR connecting to Google Sheets: {e}")
        return None
    
    # Step 4: Process solicitations from CSV
    print("\n🔄 Step 4: Processing solicitations...")
    results = processEsbdSolicitationsFromCsv(records, local_contracts_wks, spreadsheet=sh, east_tx_wks=east_tx_wks)
    
    # Summary
    print("\n" + "="*80)
    print("WORKFLOW COMPLETE")
    print("="*80)
    print(f"📅 Date processed: {date_str}")
    print(f"📄 CSV file: {csv_file}")
    print(f"📊 Total solicitations in CSV: {len(records)}")
    print(f"✅ Successfully processed: {len(results)}")
    print("="*80)
    
    return results


def auto_process_date_range_solicitations(start_date, end_date):
    """
    Automatically fetch and process all ESBD solicitations for a custom date range.
    
    Args:
        start_date (str): Start date in format MM/DD/YYYY
        end_date (str): End date in format MM/DD/YYYY
        
    Returns:
        list: Results from processing
    """
    import pandas as pd
    import pygsheets
    
    print("\n" + "="*80)
    print("AUTO PROCESS ESBD SOLICITATIONS - CUSTOM DATE RANGE")
    print("="*80)
    print(f"Date range: {start_date} to {end_date}")
    print("="*80)
    
    # Step 1: Export CSV from ESBD website
    print("\n📥 Step 1: Exporting CSV from ESBD website...")
    csv_file = step2_export_csv_with_date_range(start_date, end_date)
    
    if not csv_file:
        print("\n❌ FAILED: Could not export CSV from ESBD website")
        return None
    
    print(f"✅ CSV exported successfully: {csv_file}")
    
    # Step 2: Read CSV file
    print("\n📖 Step 2: Reading CSV file...")
    try:
        df = pd.read_csv(csv_file)
        print(f"✅ Found {len(df)} solicitations in CSV")
        print(f"Columns: {list(df.columns)}")
        
        # Convert to list of dictionaries
        records = df.to_dict('records')
        
    except Exception as e:
        print(f"❌ ERROR reading CSV file: {e}")
        return None
    
    # Step 3: Connect to Google Sheets
    print("\n📊 Step 3: Connecting to Google Sheets...")
    try:
        gc = pygsheets.authorize(service_file='key.json')
        sh = gc.open('Quote Request')
        
        # Get the localContracts worksheet
        try:
            local_contracts_wks = sh.worksheet_by_title('localContracts')
            print("✅ Found 'localContracts' worksheet")
        except Exception as e:
            print(f"❌ Error: Could not find 'localContracts' worksheet: {e}")
            return None

        # Get or create the East TX worksheet
        from config import EAST_TX_WORKSHEET
        east_tx_wks = _get_or_create_east_tx_wks(sh, EAST_TX_WORKSHEET)
            
    except Exception as e:
        print(f"❌ ERROR connecting to Google Sheets: {e}")
        return None
    
    # Step 4: Process solicitations from CSV
    print("\n🔄 Step 4: Processing solicitations...")
    results = processEsbdSolicitationsFromCsv(records, local_contracts_wks, spreadsheet=sh, east_tx_wks=east_tx_wks)
    
    # Summary
    print("\n" + "="*80)
    print("WORKFLOW COMPLETE")
    print("="*80)
    print(f"📅 Date range: {start_date} to {end_date}")
    print(f"📄 CSV file: {csv_file}")
    print(f"📊 Total solicitations in CSV: {len(records)}")
    print(f"✅ Successfully processed: {len(results)}")
    print("="*80)
    
    return results


def export_and_process(start_date, end_date):
    """
    Complete workflow: Export CSV from ESBD and process solicitations.
    
    This is the main function to replace the Google Sheets approach.
    
    Args:
        start_date (str): Start date in format MM/DD/YYYY
        end_date (str): End date in format MM/DD/YYYY
    """
    print("\n" + "="*80)
    print("ESBD AUTOMATED CSV EXPORT AND PROCESSING")
    print("="*80)
    print(f"Date Range: {start_date} to {end_date}")
    print("="*80)
    
    # Step 1: Export CSV from ESBD website
    csv_file = step2_export_csv_with_date_range(start_date, end_date)
    
    if not csv_file:
        print("\n❌ FAILED: Could not export CSV from ESBD website")
        return None
    
    # Step 2: Process the CSV and generate leads
    results = process_csv_and_create_leads(csv_file)
    
    print("\n" + "="*80)
    print("WORKFLOW COMPLETE")
    print("="*80)
    print(f"✅ Exported CSV: {csv_file}")
    print(f"✅ Processed {len(results)} solicitations successfully")
    print("="*80)
    
    return results


def main():
    """Main function - automatically processes yesterday's ESBD solicitations."""
    print("\n" + "="*80)
    print("ESBD AUTO PROCESSOR - YESTERDAY'S SOLICITATIONS")
    print("="*80)
    
    # Always auto-process yesterday's solicitations
    auto_process_yesterday_solicitations()


if __name__ == "__main__":
    main()

